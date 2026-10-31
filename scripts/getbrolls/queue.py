"""Paced batch queue for social sources. Sleep-free: the CLI reports how long to wait.

State lives in `<project>/work/queue.json` (private, atomic writes). Pacing is a
random interval between items plus hourly/daily caps per provider. A failure reason
is one of three kinds: a rate limit (429, rate limit, too many requests) opens a
doubling cooldown, which is pacing and nothing else; a login wall, a challenge, a
401/403 or an explicit refusal stops the provider (`stopped`) with no resume time,
because waiting does not authorize anything (only `resume`, run by a person, clears it);
anything else is a plain failure.
"""

# pylint: disable=missing-function-docstring,too-many-locals,cyclic-import
# Legado: ocorrências pré-existentes (corpo idêntico ao código anterior à 2.6.0). O ciclo
# (getbrolls.queue <-> getbrolls.rules) já existia antes da 2.6.0: `report()`
# importa `rules.load_rules` tardiamente (dentro de função) exatamente para
# quebrar esse ciclo em tempo de execução.

import json
import logging
import os
import random
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import effects, json_read, labels, logs, versioning
from ._paths import cli_prefix_text, strict_is_file
from .sdk import jsonschema, schemas

log = logs.get("queue")

QUEUE_FILE = Path("work") / "queue.json"
STATUSES = ("pending", "active", "done", "failed", "skipped")
TERMINAL_STATUSES = ("done", "failed", "skipped")

# Defaults per provider: (min_s, max_s) between items.
DEFAULT_PACE = {"instagram": (45, 120), "tiktok": (15, 40), "youtube": (15, 40)}
# Providers the queue accepts: exactly those with a default pace above.
PROVIDERS = tuple(DEFAULT_PACE)
DEFAULT_MAX_PER_HOUR = 20
DEFAULT_MAX_PER_DAY = 60
COOLDOWN_BASE_S = 30 * 60
COOLDOWN_CAP_S = 4 * 60 * 60
# Anchored on word boundaries and full phrases: a bare "rate" false-positives on
# "bitrate"/"inaccurate"; require "rate limit" or "too many requests" instead.
RATE_LIMIT_RE = re.compile(
    r"\b(?:429|rate[\s-]?limit|too\s+many\s+requests|limite\s+de\s+requisi[çc][õo]es)\b",
    re.IGNORECASE,
)
# A login wall, a challenge, a 401/403 (cause unknown until someone looks) or an explicit refusal.
# No "recusado" or "bloqueado" alone: our own local refusals use those words and are not an access wall.
ACCESS_REFUSED_RE = re.compile(
    r"\b(?:401|403|challenge|checkpoint|captcha|login|unauthori[sz]ed|forbidden|access\s+denied|acesso\s+negado"
    r"|sess[ãa]o\s+de\s+acesso|bloqueou\s+o\s+ip|ip\s+bloqueado)\b",
    re.IGNORECASE,
)
# Category for the `reason_class` log field and the `stopped.class` field: a coarse bucket, never
# the raw free-text reason (which may carry a title, a URL, or other free text passed in by a caller).
_REFUSAL_CATEGORIES = (
    (re.compile(r"\b403\b"), "http_403"),
    (re.compile(r"\b401\b"), "http_401"),
    (re.compile(r"\bchallenge\b|\bcheckpoint\b|\bcaptcha\b", re.IGNORECASE), "challenge"),
    (re.compile(r"\blogin\b|sess[ãa]o\s+de\s+acesso", re.IGNORECASE), "login"),
    (re.compile(r"bloqueou\s+o\s+ip|ip\s+bloqueado", re.IGNORECASE), "ip_block"),
)
_RATE_CATEGORIES = (
    (re.compile(r"\b429\b"), "http_429"),
    (
        re.compile(r"rate[\s-]?limit|too\s+many\s+requests|limite\s+de\s+requisi[çc][õo]es", re.IGNORECASE),
        "rate_limit",
    ),
)


def _reason_class(reason):
    """The coarse bucket of a reason, searched in the table of its `failure_kind`."""
    text = str(reason or "")
    refused = failure_kind(text) == "access_refused"
    default = "refused" if refused else "other"
    for pattern, category in _REFUSAL_CATEGORIES if refused else _RATE_CATEGORIES:
        if pattern.search(text):
            return category
    return default


def failure_kind(reason):
    """`access_refused`, `rate_limit` or None for the free text a caller gives `mark`.

    A refusal wins over a rate-limit word in the same text: a 403 whose cause nobody has seen
    must not become "wait and come back" because the text also says "rate limit".
    """
    text = str(reason or "")
    if ACCESS_REFUSED_RE.search(text):
        return "access_refused"
    if RATE_LIMIT_RE.search(text):
        return "rate_limit"
    return None


LIMITS = (
    ("min_s", "GB_PACE_MIN_S", 0, 24 * 3600),
    ("max_s", "GB_PACE_MAX_S", 0, 24 * 3600),
    ("max_per_hour", "GB_MAX_PER_HOUR", 1, 10000),
    ("max_per_day", "GB_MAX_PER_DAY", 1, 100000),
)


def _now():
    return datetime.now(UTC)


def _at(value):
    return value if isinstance(value, datetime) else _now()


def _stamp(moment):
    return moment.isoformat()


def _parse(stamp):
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        raise ValueError(f"queue.json inválido: timestamp corrompido ({stamp!r}).") from None
    if moment.tzinfo is None:
        # Comparing it with an aware instant would raise TypeError (an internal error): refuse it as data.
        raise ValueError(f"queue.json inválido: timestamp sem fuso horário ({stamp!r}).")
    return moment


def _rng():
    return random.Random(os.urandom(16))  # noqa: S311 - pacing jitter, not security


def queue_path(project):
    return Path(project).expanduser().resolve() / QUEUE_FILE


def empty_state():
    return {"schema_version": schemas.version("queue"), "items": [], "providers": {}}


def _check(data, path):
    """Integrity of a loaded queue: duplicate ids, active without started_at, more than one
    active item per provider, and done/failed items missing finished_at (which would let
    them fall out of the hourly/daily rate-limit windows for free)."""
    seen_ids = set()
    active_by_provider = {}
    for item in data["items"]:
        if not isinstance(item, dict):
            continue  # the schema check at the end of `load` refuses it
        item_id = item.get("id")
        if item_id in seen_ids:
            raise ValueError(f"queue.json inválido em {labels.shown(path)}: id duplicado {item_id!r}.")
        seen_ids.add(item_id)
        status = item.get("status")
        provider = item.get("provider")
        if status == "active":
            if not item.get("started_at"):
                raise ValueError(
                    f"queue.json inválido em {labels.shown(path)}: item ativo sem started_at ({item_id!r})."
                )
            if provider in active_by_provider:
                raise ValueError(
                    f"queue.json inválido em {labels.shown(path)}: mais de um item ativo para {provider!r} "
                    f"({active_by_provider[provider]!r} e {item_id!r})."
                )
            active_by_provider[provider] = item_id
        if status in ("done", "failed") and not item.get("finished_at"):
            raise ValueError(
                f"queue.json inválido em {labels.shown(path)}: item {item_id!r} em {status!r} sem finished_at "
                "(quebraria as contagens de hora/dia)."
            )


def _check_shapes(data, path):
    """Shape of every provider state and every timestamp, so no command meets a TypeError later.

    A null provider state reads as the default state; anything else must be an object whose
    timestamps are timezone-aware ISO strings and whose strike counter is an integer.
    """
    where = labels.shown(path)
    for name, state in data["providers"].items():
        if state is None:
            continue
        if not isinstance(state, dict):
            raise ValueError(  # noqa: TRY004 - contrato da CLI: dado inválido é ValueError (INVALID_DATA)
                f"queue.json inválido em {where}: estado de {name!r} corrompido; "
                "preserve o arquivo e restaure uma cópia válida."
            )
        for key in ("last_action_at", "next_allowed_at", "cooldown_until"):
            _parse(state.get(key))
        strikes = state.get("cooldown_strikes", 0)
        if strikes is not None and (isinstance(strikes, bool) or not isinstance(strikes, int)):
            raise ValueError(f"queue.json inválido em {where}: cooldown_strikes de {name!r} não é inteiro.")
        _stopped(state, name)
    for item in data["items"]:
        if not isinstance(item, dict):
            continue  # the schema check at the end of `load` refuses it
        for key in ("added_at", "started_at", "finished_at"):
            _parse(item.get(key))


def load(path):
    path = Path(path)
    if not strict_is_file(path):
        return empty_state()
    try:
        data = json_read.read(path)
    except json_read.JsonReadError:
        raise ValueError(
            f"queue.json inválido em {labels.shown(path)}; preserve o arquivo e restaure uma cópia válida."
        ) from None
    if not isinstance(data, dict):
        raise ValueError(  # noqa: TRY004 - contrato da CLI: dado inválido é ValueError (INVALID_DATA)
            f"queue.json incompatível em {labels.shown(path)}."
        )
    current = schemas.version("queue")
    invalid = f"queue.json incompatível em {labels.shown(path)} (schema_version esperado {current})."
    versioning.read_version(data, f"queue.json em {labels.shown(path)}", current, invalid=invalid)
    if not isinstance(data.get("items"), list) or not isinstance(data.get("providers"), dict):
        raise ValueError(  # noqa: TRY004 - contrato da CLI: dado inválido é ValueError (INVALID_DATA)
            f"queue.json incompatível em {labels.shown(path)}."
        )
    _check_shapes(data, path)
    _check(data, path)
    # Last, so the specific messages above keep naming the provider or the item; it also catches what they skip.
    if jsonschema.errors(data, schemas.load("queue")):
        raise ValueError(f"queue.json incompatível em {labels.shown(path)}.")
    return data


def save(path, data):
    """Private atomic write: temp file in the same folder, 0o600, then replace."""
    path = Path(path)
    effects.mkdir(path.parent, parents=True, exist_ok=True)
    with effects.op("fs.write", path):
        handle, temp = tempfile.mkstemp(prefix=".queue-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                effects.make_private(temp)  # inside: Ctrl-C during the wait closes the file and removes it
                stream.write(
                    json.dumps(versioning.stamp(data, schemas.version("queue")), ensure_ascii=False, indent=2) + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
            Path(temp).chmod(0o600)
            Path(temp).replace(path)
        finally:
            Path(temp).unlink(missing_ok=True)


def _integer(key, value, lo, hi):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: use um inteiro.") from None
    if not lo <= number <= hi:
        raise ValueError(f"{key}: intervalo permitido {lo}–{hi}.")
    return number


def pacing(provider, rules=None, use_env=True):
    """Pacing limits for one provider: defaults < RULES.md `pacing` block < environment.

    `use_env=False` skips the environment override entirely; callers that only want to
    validate the RULES.md block itself (see `validate_pacing_block`) must use it so a
    stray GB_PACE_*/GB_MAX_* in the environment cannot mask an invalid block or fail a
    valid one that the environment happens to disagree with.
    """
    if provider not in PROVIDERS:
        raise ValueError("Provedor da fila: use instagram, tiktok ou youtube.")
    low, high = DEFAULT_PACE[provider]
    limits = {"min_s": low, "max_s": high, "max_per_hour": DEFAULT_MAX_PER_HOUR, "max_per_day": DEFAULT_MAX_PER_DAY}
    block = ((rules or {}).get("pacing") or {}).get(provider) or {}
    overrides = {}
    for key, env_key, lo, hi in LIMITS:
        if key in block:
            limits[key] = _integer(f"RULES.md pacing.{provider}.{key}", block[key], lo, hi)
            overrides[key] = "rules"
        if use_env:
            env_value = (os.environ.get(env_key) or "").strip()
            if env_value:
                limits[key] = _integer(env_key, env_value, lo, hi)
                overrides[key] = "env"
    if limits["min_s"] > limits["max_s"]:
        raise ValueError(
            f"Ritmo de {provider}: min_s não pode ser maior que max_s "
            "(GB_PACE_MIN_S/GB_PACE_MAX_S ou o bloco pacing em RULES.md)."
        )
    if overrides:
        logs.event(log, logging.DEBUG, "pacing_override", provider=provider, **overrides)
    return limits


def validate_pacing_block(block):
    """RULES.md `pacing` block: {provider: {min_s, max_s, max_per_hour, max_per_day}}."""
    if block is None:
        return
    if not isinstance(block, dict) or any(
        provider not in PROVIDERS or not isinstance(values, dict) for provider, values in block.items()
    ):
        raise ValueError('pacing: use {"instagram"|"tiktok"|"youtube": {...}}.')
    known = {key for key, _, _, _ in LIMITS}
    for provider, values in block.items():
        if set(values) - known:
            raise ValueError(f"pacing.{provider}: chaves aceitas {', '.join(sorted(known))}.")
        # use_env=False: the block's own validity must not depend on what happens to be
        # set in the environment at validation time.
        pacing(provider, {"pacing": {provider: values}}, use_env=False)


def _default_provider_state():
    return {"last_action_at": None, "next_allowed_at": None, "cooldown_until": None, "cooldown_strikes": 0}


def _provider_state(data, provider):
    """Mutating accessor: only for write paths (claim_next/mark) that intend to persist."""
    return data["providers"].setdefault(provider, _default_provider_state())


def _provider_view(data, provider):
    """Read-only accessor: never mutates `data` (used by report/hint)."""
    return data["providers"].get(provider) or _default_provider_state()


def _stopped(state, provider):
    """The provider's recorded stop, or None. A corrupted one is an invalid queue, never a free pass.

    A null provider state reads as the default state (no stop), as `_provider_view` reads it; any other
    non-object state is corrupted like a bad stop record.
    """
    if state is None:
        return None
    if not isinstance(state, dict):
        raise ValueError(  # noqa: TRY004 - contrato da CLI: dado inválido é ValueError (INVALID_DATA)
            f"queue.json inválido: estado de {provider!r} corrompido; preserve o arquivo e restaure uma cópia válida."
        )
    stopped = state.get("stopped")
    if stopped is None:
        return None
    if not isinstance(stopped, dict) or not isinstance(stopped.get("at"), str) or not stopped["at"]:
        raise ValueError(
            f"queue.json inválido: `stopped` de {provider!r} corrompido; "
            "preserve o arquivo e restaure uma cópia válida."
        )
    _parse(stopped["at"])
    return stopped


def _stop_message(provider, stopped):
    return (
        f"Fila de {provider} parada em {stopped['at']}: acesso recusado ({stopped.get('class') or 'other'}). "
        "Parou por recusa, não por ritmo: esperar não autoriza a retomada. Diagnostique a causa com a pessoa e peça "
        "a ela, no chat, a confirmação de que o acesso voltou; só com a frase dela existe um modelo de retomada: "
        f'queue --action resume --provider {provider} --by NOME --phrase "FRASE EXATA" --expect AT_DA_PARADA '
        "(o AT_DA_PARADA é o campo stopped.at do provedor em queue --action status)."
    )


def stopped_providers(data):
    """The providers with a recorded stop, as `{provider: stopped}`; a corrupted record raises."""
    found = {}
    for name in sorted(data["providers"]):
        recorded = _stopped(data["providers"][name], name)
        if recorded:
            found[name] = recorded
    return found


def stop_of(project, provider):
    """The recorded stop of `provider` in `<project>/work/queue.json`, read-only; None when there is none.

    A queue.json that cannot be read raises, so a caller that guards network traffic fails closed.
    """
    path = queue_path(project)
    if not strict_is_file(path):
        return None
    data = load(path)
    return stopped_providers(data).get(provider)


def hold_of(project, provider, at=None):
    """What holds `provider` in `<project>/work/queue.json`, read-only: `(stopped, cooldown_until)`.

    `stopped` is the recorded stop (or None); `cooldown_until` is the stamp of a cooldown that is still
    running at `at` (or None once it passed). A queue.json that cannot be read raises, like `stop_of`.
    """
    path = queue_path(project)
    if not strict_is_file(path):
        return None, None
    data = load(path)
    stopped = stopped_providers(data).get(provider)
    state = _provider_view(data, provider)
    until = _parse(state.get("cooldown_until"))
    running = until is not None and until > _at(at)
    return stopped, (state["cooldown_until"] if running else None)


def add(data, provider, urls, at=None):
    """Enqueue public URLs, normalized through providers.resolve; duplicates are reported."""
    from .providers import resolve

    if provider not in PROVIDERS:
        raise ValueError("Provedor da fila: use instagram, tiktok ou youtube.")
    moment = _stamp(_at(at))
    known = {item["id"] for item in data["items"]}
    added, duplicates = [], []
    for url in urls:
        item = resolve(url)
        if item["provider"] != provider:
            raise ValueError(f"URL pertence a {item['provider']}, não a {provider}: {item['source_url']}")
        if item["id"] in known:
            duplicates.append(item["id"])
            continue
        known.add(item["id"])
        added.append(item["id"])
        data["items"].append(
            {
                "id": item["id"],
                "provider": provider,
                "url": item["source_url"],
                "status": "pending",
                "added_at": moment,
                "started_at": None,
                "finished_at": None,
                "reason": None,
            }
        )
    return {
        "added": added,
        "duplicates": duplicates,
        "pending": sum(1 for i in data["items"] if i["status"] == "pending"),
    }


def _finished_since(data, provider, since, now=None):
    """Parsed timestamps only (never None) so the caller can sort them directly.

    A done/failed item without finished_at is counted as finished "now" (the safest
    assumption for a rate limiter) instead of silently falling out of the window; in
    practice `load()`/`_check()` already reject such items, this is defense in depth
    for data built in-memory without going through `mark()`.
    """
    now = now if now is not None else _now()
    parsed = []
    for item in data["items"]:
        if item["provider"] != provider or item["status"] not in ("done", "failed"):
            continue
        moment = _parse(item["finished_at"]) if item["finished_at"] else now
        if moment is not None and moment > since:
            parsed.append(moment)
    return parsed


def _gate(data, provider, moment, limits, state=None):
    """Earliest allowed moment for the provider and the rule that holds it, or (None, None)."""
    state = state if state is not None else _provider_view(data, provider)
    holds = []
    cooldown = _parse(state.get("cooldown_until"))
    if cooldown and cooldown > moment:
        holds.append((cooldown, "cooldown"))
    allowed = _parse(state.get("next_allowed_at"))
    if allowed and allowed > moment:
        holds.append((allowed, "pace"))
    for window, cap, reason in (
        (timedelta(hours=1), limits["max_per_hour"], "max_per_hour"),
        (timedelta(days=1), limits["max_per_day"], "max_per_day"),
    ):
        finished = sorted(_finished_since(data, provider, moment - window, now=moment))
        if len(finished) >= cap:
            holds.append((finished[len(finished) - cap] + window, reason))
    if not holds:
        return None, None
    return max(holds)


def _waiting(moment, resume_at, reason, stopped=()):
    wait = max(0, int((resume_at - moment).total_seconds() + 0.999))
    result = {"item": None, "wait_seconds": wait, "resume_at": _stamp(resume_at), "reason": reason}
    if stopped:
        # Another provider is stopped, not waiting: say so, so the wait is not read as covering it too.
        result["stopped"] = sorted(stopped)
    return result


def _activate(data, item, moment, limits, rng):
    """Hand the item out and re-arm the provider's random interval before the next one."""
    state = _provider_state(data, item["provider"])
    interval = (rng or _rng()).uniform(limits["min_s"], limits["max_s"])
    state["last_action_at"] = _stamp(moment)
    state["next_allowed_at"] = _stamp(moment + timedelta(seconds=interval))
    item["status"] = "active"
    item["started_at"] = _stamp(moment)
    return {"item": item, "wait_seconds": 0, "resume_at": None, "reason": None}


def claim_next(data, provider=None, at=None, rng=None, rules=None):
    """Next pending item when pacing allows; otherwise how long to wait. Never sleeps."""
    moment = _at(at)
    candidates = [i for i in data["items"] if provider in (None, i["provider"])]
    held = {name: recorded for name, recorded in stopped_providers(data).items() if provider in (None, name)}
    # An item still `active` under a stopped provider is not handed out again: the stop wins.
    active = next((i for i in candidates if i["status"] == "active" and i["provider"] not in held), None)
    if active:
        return {"item": active, "wait_seconds": 0, "resume_at": None, "reason": "active"}
    pending = [i for i in candidates if i["status"] == "pending"]
    if not pending:
        return {"item": None, "wait_seconds": 0, "resume_at": None, "reason": "empty"}
    blocked = []
    for item in pending:
        if item["provider"] in held:
            continue
        limits = pacing(item["provider"], rules)
        state = _provider_state(data, item["provider"])
        resume_at, reason = _gate(data, item["provider"], moment, limits, state)
        if resume_at is None:
            result = _activate(data, item, moment, limits, rng)
            logs.event(log, logging.INFO, "queue_claim", provider=item["provider"], item=item["id"])
            return result
        blocked.append((resume_at, reason, item["provider"]))
    if not blocked:
        # Every pending item belongs to a stopped provider: say so as a refusal, not as a wait.
        name, recorded = next(iter(held.items()))
        raise ValueError(_stop_message(name, recorded))
    resume_at, reason, provider_blocked = min(blocked, key=lambda b: (b[0], b[1]))
    result = _waiting(moment, resume_at, reason, stopped=held)
    logs.event(
        log,
        logging.INFO,
        "queue_wait",
        provider=provider_blocked,
        hold=reason,
        next_allowed_in_s=result["wait_seconds"],
    )
    return result


# Backward-compatible alias: `claim_next` is the current name, `next_item` is kept for
# any caller (internal or third-party) that still imports the old symbol.
next_item = claim_next


def is_cooldown_reason(reason):
    """True only for a rate limit: the one failure a cooldown (pacing) is the answer to."""
    return failure_kind(reason) == "rate_limit"


def _open_cooldown(provider, state, moment, reason=None):
    previous_strikes = int(state.get("cooldown_strikes") or 0)
    state["cooldown_strikes"] = previous_strikes + 1
    seconds = min(COOLDOWN_BASE_S * 2 ** (state["cooldown_strikes"] - 1), COOLDOWN_CAP_S)
    until = moment + timedelta(seconds=seconds)
    state["cooldown_until"] = _stamp(until)
    logs.event(
        log,
        logging.INFO,
        "cooldown",
        provider=provider,
        action="open" if previous_strikes == 0 else "extend",
        until_s=seconds,
        reason_class=_reason_class(reason),
    )
    return {"seconds": seconds, "until": _stamp(until), "strikes": state["cooldown_strikes"]}


def _open_stop(provider, state, moment, reason_class, item_id=None):
    """Stop the provider: no cooldown and no resume time, because waiting authorizes nothing here."""
    recorded = _stopped(state, provider)
    if recorded is None:
        recorded = {"at": _stamp(moment), "class": reason_class, "item": item_id}
        state["stopped"] = recorded
        logs.event(log, logging.WARNING, "stop", provider=provider, reason_class=recorded["class"])
    return {"kind": "access_refused", "class": recorded["class"], "at": recorded["at"]}


def mark(data, item_id, status, reason=None, at=None):
    if status not in ("done", "failed", "skipped"):
        raise ValueError("mark: use --done, --failed ou --skipped.")
    item = next((i for i in data["items"] if i["id"] == item_id), None)
    if item is None:
        raise ValueError(f"Item não está na fila: {item_id}")
    if item["status"] in TERMINAL_STATUSES:
        raise ValueError(f"Item {item_id} já está em estado final ({item['status']}); não é possível marcar novamente.")
    old_status = item["status"]
    moment = _at(at)
    item["status"] = status
    item["finished_at"] = _stamp(moment)
    item["reason"] = reason
    state = _provider_state(data, item["provider"])
    cooldown = None
    stop = None
    kind = None if status == "done" else failure_kind(reason)
    if status == "done":
        had_cooldown = state.get("cooldown_until") is not None or int(state.get("cooldown_strikes") or 0) > 0
        state["cooldown_strikes"] = 0
        state["cooldown_until"] = None
        if had_cooldown:
            logs.event(
                log,
                logging.INFO,
                "cooldown",
                provider=item["provider"],
                action="close",
                until_s=None,
                reason_class=None,
            )
    elif kind == "rate_limit":
        cooldown = _open_cooldown(item["provider"], state, moment, reason)
    elif kind == "access_refused":
        stop = _open_stop(item["provider"], state, moment, _reason_class(reason), item_id=item_id)
    logs.event(log, logging.INFO, "queue_mark", item=item_id, **{"from": old_status, "to": status})
    result = {"item": item, "cooldown": cooldown}
    if stop:
        result["stop"] = stop
    return result


def record_cooldown(project, provider, reason, at=None):
    """Open a cooldown in an existing queue.json (used by instagram_pairs); None when absent."""
    path = queue_path(project)
    if not strict_is_file(path):
        return None
    data = load(path)
    moment = _at(at)
    cooldown = _open_cooldown(provider, _provider_state(data, provider), moment, reason)
    items_failed = []
    for item in data["items"]:
        if item["provider"] == provider and item["status"] == "active":
            item["status"] = "failed"
            # The item finished now, not at the (future) cooldown deadline: stamping it
            # with `cooldown["until"]` corrupted the hourly/daily window counts.
            item["finished_at"] = _stamp(moment)
            item["reason"] = reason
            items_failed.append(item["id"])
    save(path, data)
    return {"until": cooldown["until"], "items_failed": items_failed}


def record_refusal(project, provider, reason, at=None, reason_class=None):
    """Stop the provider in an existing queue.json (used by instagram_pairs); None when absent.

    No cooldown is opened: a refusal is not a wait. The active item of the provider fails now.
    """
    path = queue_path(project)
    if not strict_is_file(path):
        return None
    data = load(path)
    moment = _at(at)
    items_failed = []
    for item in data["items"]:
        if item["provider"] == provider and item["status"] == "active":
            item["status"] = "failed"
            item["finished_at"] = _stamp(moment)
            item["reason"] = reason
            items_failed.append(item["id"])
    stop = _open_stop(
        provider,
        _provider_state(data, provider),
        moment,
        reason_class or _reason_class(reason),
        item_id=items_failed[0] if items_failed else None,
    )
    save(path, data)
    return {"at": stop["at"], "items_failed": items_failed}


def resume(data, provider, *, by=None, statement=None, expect=None, at=None):  # noqa: PLR0913  # pylint: disable=too-many-arguments
    """Clear the provider's stop after a person confirmed that access is back; cooldowns and counters stay.

    The act is attested (`--by`, `--phrase`, `--expect`) and recorded in `last_resume` so an auditor can
    see who cleared it and with which words; `expect` ties it to the stop the person saw, so an old or replayed
    command cannot clear a newer one.
    """
    if provider not in PROVIDERS:
        raise ValueError("queue resume: informe --provider instagram, tiktok ou youtube.")
    if not isinstance(by, str) or not by.strip():
        raise ValueError("queue resume: informe em --by quem confirmou que o acesso voltou.")
    if not isinstance(statement, str) or not statement.strip():
        raise ValueError("queue resume: informe em --phrase a frase exata dita pela pessoa.")
    state = data["providers"].get(provider) or {}
    stopped = _stopped(state, provider)
    if stopped is None:
        raise ValueError(f"queue resume: a fila de {provider} não está parada; não há o que retomar.")
    if expect != stopped["at"]:
        raise ValueError(
            f"queue resume: --expect não confere com a parada atual de {provider} ({stopped['at']}); "
            "mostre a parada à pessoa de novo (queue --action status) e peça a confirmação outra vez."
        )
    del state["stopped"]
    state["last_resume"] = {
        "at": _stamp(_at(at)),
        "by": by.strip(),
        "statement": statement,
        "stopped_at": stopped["at"],
        "class": stopped.get("class"),
    }
    logs.event(
        log,
        logging.INFO,
        "queue_resume",
        provider=provider,
        reason_class=stopped.get("class") or "other",
        statement_present=True,
    )
    return {
        "provider": provider,
        "by": by.strip(),
        "resumed": {key: stopped.get(key) for key in ("at", "class", "item")},
    }


def report(data, at=None, rules=None):
    moment = _at(at)
    counts = {status: sum(1 for i in data["items"] if i["status"] == status) for status in STATUSES}
    providers = {}
    for provider in sorted({i["provider"] for i in data["items"]} | set(data["providers"])):
        state = _provider_view(data, provider)
        try:
            limits = pacing(provider, rules)
        except ValueError as exc:
            resume_at, reason = None, str(exc)
        else:
            # A corrupted timestamp raises ValueError from `_gate`/`_parse` and must
            # propagate as an invalid-queue error, not be swallowed into a silent hold.
            resume_at, reason = _gate(data, provider, moment, limits, state)
        cooldown_until = _parse(state.get("cooldown_until"))
        stopped = _stopped(state, provider)
        providers[provider] = {
            "pending": sum(1 for i in data["items"] if i["provider"] == provider and i["status"] == "pending"),
            "cooldown_until": state.get("cooldown_until")
            if cooldown_until is not None and cooldown_until > moment
            else None,
            "cooldown_strikes": state.get("cooldown_strikes", 0),
            "next_allowed_at": _stamp(resume_at) if resume_at else None,
            "wait_seconds": max(0, int((resume_at - moment).total_seconds() + 0.999)) if resume_at else 0,
            "hold": reason,
        }
        if stopped:
            providers[provider]["stopped"] = {key: stopped.get(key) for key in ("at", "class", "item")}
    return {"counts": counts, "providers": providers, "checked_at": _stamp(moment)}


def hint(project, at=None):
    """Read-only one-line view for `status`; None when the project has no queue."""
    path = queue_path(project)
    if not strict_is_file(path):
        return None
    try:
        data = load(path)
    except (ValueError, OSError) as exc:
        return {"error": str(exc)}
    rules = None
    try:
        from .rules import load_rules

        rules = load_rules(project)
    except (ValueError, OSError):
        # RULES.md is optional/best-effort here: fall back to pacing defaults/env.
        rules = None
    try:
        summary = report(data, at=at, rules=rules)
    except ValueError as exc:
        return {"error": str(exc)}
    counts = summary["counts"]
    stopped = sorted(name for name, p in summary["providers"].items() if p.get("stopped"))
    pending_providers = [p for p in summary["providers"].values() if p["pending"] > 0 and not p.get("stopped")]
    stopped_text = f"parada em {', '.join(stopped)} por recusa de acesso (esperar não autoriza a retomada)"
    idle = bool(stopped and not pending_providers and counts["pending"] > 0)
    if idle:
        when = f"nada a rodar: {stopped_text}"
    elif not pending_providers or any(p["next_allowed_at"] is None for p in pending_providers):
        # A pending provider with no hold is free right now: don't make the caller wait
        # for the slowest blocked provider (that used to take the `max`).
        when = "próximo permitido agora"
    else:
        soonest = min(p["next_allowed_at"] for p in pending_providers)
        when = f"próximo permitido em {soonest}"
    line = (
        f"Fila: {counts['pending']} pendente(s), {counts['active']} ativo(s), "
        f"{counts['done']} concluído(s), {counts['failed']} com falha, {counts['skipped']} pulado(s); {when}."
    )
    if stopped and not idle:
        line += f" Atenção: fila {stopped_text}."
    return {"counts": counts, "providers": summary["providers"], "line": line}


def _next_line(result):
    if result.get("item"):
        return f"Próximo item: {result['item']['id']} ({result['item']['url']})."
    if result.get("reason") == "empty":
        return "Fila vazia: nada pendente."
    wait = f"Aguarde {result.get('wait_seconds')} s (motivo: {result.get('reason')})"
    held = result.get("stopped")
    note = (
        f" A espera não vale para {', '.join(held)}: parado por recusa de acesso, sem horário para voltar."
        if held
        else ""
    )
    if result.get("reason") == "cooldown":
        return (
            f"{wait}. É ritmo de um limite temporário e não libera nada: chame next de novo a partir de "
            f"{result.get('resume_at')}, no mesmo escopo, e pare se o bloqueio voltar.{note}"
        )
    return f"{wait}; chame next de novo a partir de {result.get('resume_at')}.{note}"


def _mark_line(result):
    item = result.get("item") or {}
    cooldown = result.get("cooldown")
    stop = result.get("stop")
    extra = f" Cooldown de {cooldown['seconds']} s até {cooldown['until']}." if cooldown else ""
    if stop:
        extra = (
            f" Pare o lote: acesso recusado ({stop.get('class')}); esperar não autoriza a retomada, "
            "então não há cooldown nem horário para voltar. Diagnostique a causa com a pessoa."
        )
    return f"Marquei {item.get('id')} como {item.get('status')}.{extra}"


def _resume_summary(result):
    provider = result.get("provider")
    command = f"{cli_prefix_text()} queue --action next --provider {provider} --project PROJETO"
    return {
        "line": (
            f"Retomei a fila de {provider}: só a parada saiu; cooldown, contagens e ritmo ficam como estavam. "
            "Chame next para pegar o próximo item."
        ),
        "do": {
            "step": "queue-next",
            "why": "A parada foi limpa por quem confirmou o acesso; o próximo item sai pelo ritmo de sempre.",
            "command": command,
            "url": None,
            "for_human": "Se o bloqueio voltar, avise: a fila para de novo e não tente contornar.",
            "blocking_human": False,
        },
    }


def summary_line(result):
    """Human line for the CLI, chosen by the action that produced the result."""
    action = result.get("action")
    if action == "add":
        return (
            f"Enfileirei {len(result.get('added') or [])} URL(s); "
            f"{len(result.get('duplicates') or [])} repetida(s); {result.get('pending')} pendente(s)."
        )
    if action == "next":
        return _next_line(result)
    if action == "mark":
        return _mark_line(result)
    if action == "resume":
        return _resume_summary(result)
    counts = result.get("counts") or {}
    return (
        f"Fila: {counts.get('pending', 0)} pendente(s), {counts.get('done', 0)} concluído(s), "
        f"{counts.get('failed', 0)} com falha."
    )


def _mark_status(args):
    """The one --done/--failed/--skipped flag the CLI accepts for `mark`."""
    for flag in ("done", "failed", "skipped"):
        # lenient: dynamic getattr over argparse flag names, not an existence check on a path
        if getattr(args, flag, False):
            return flag
    raise ValueError("queue mark: escolha --done, --failed ou --skipped.")


def execute(args, rules=None):
    path = queue_path(args.project)
    data = load(path)
    action = args.action
    if action == "add":
        urls = [*(args.urls or []), *(args.url or [])]
        if not urls:
            raise ValueError("queue add: informe ao menos uma URL.")
        if not args.provider:
            raise ValueError("queue add: informe --provider instagram, tiktok ou youtube.")
        result = add(data, args.provider, urls)
        save(path, data)
    elif action == "next":
        result = claim_next(data, provider=args.provider, rules=rules)
        # reason "active" is a pure re-read of an already-claimed item: nothing changed,
        # so don't churn queue.json on every repeated `next`.
        if result["item"] and result["reason"] != "active":
            save(path, data)
    elif action == "mark":
        if not args.id:
            raise ValueError("queue mark: informe --id.")
        status = _mark_status(args)
        result = mark(data, args.id, status, reason=args.reason)
        save(path, data)
    elif action == "resume":
        if not args.provider:
            raise ValueError("queue resume: informe --provider instagram, tiktok ou youtube.")
        result = resume(data, args.provider, by=args.by, statement=args.statement, expect=args.expect)
        save(path, data)
    else:
        result = report(data, rules=rules)
    return {"action": action, "queue": str(path), **result}
