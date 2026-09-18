"""Paced batch queue for social sources. Sleep-free: the CLI reports how long to wait.

State lives in `<project>/work/queue.json` (private, atomic writes). Pacing is a
random interval between items plus hourly/daily caps per provider; a failure that
looks like a block (403/429/challenge/login/rate limit) opens a doubling cooldown.
"""

import json
import os
import random
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROVIDERS = ("instagram", "tiktok", "youtube")
QUEUE_FILE = Path("work") / "queue.json"
SCHEMA_VERSION = 1
STATUSES = ("pending", "active", "done", "failed", "skipped")
TERMINAL_STATUSES = ("done", "failed", "skipped")

# Defaults per provider: (min_s, max_s) between items.
DEFAULT_PACE = {"instagram": (45, 120), "tiktok": (15, 40), "youtube": (15, 40)}
DEFAULT_MAX_PER_HOUR = 20
DEFAULT_MAX_PER_DAY = 60
COOLDOWN_BASE_S = 30 * 60
COOLDOWN_CAP_S = 4 * 60 * 60
# Anchored on word boundaries and full phrases: a bare "rate" false-positives on
# "bitrate"/"inaccurate"; require "rate limit" or "too many requests" instead.
COOLDOWN_RE = re.compile(
    r"\b(?:403|429|challenge|login|rate[\s-]?limit|too\s+many\s+requests"
    r"|sess[ãa]o\s+de\s+acesso|bloqueou\s+o\s+ip|limite\s+de\s+requisi[çc][õo]es)\b",
    re.IGNORECASE,
)
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
        return datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        raise ValueError(f"queue.json inválido: timestamp corrompido ({stamp!r}).") from None


def _rng():
    return random.Random(os.urandom(16))


def queue_path(project):
    return Path(project).expanduser().resolve() / QUEUE_FILE


def empty_state():
    return {"schema_version": SCHEMA_VERSION, "items": [], "providers": {}}


def _check(data, path):
    """Integrity of a loaded queue: duplicate ids, active without started_at, more than one
    active item per provider, and done/failed items missing finished_at (which would let
    them fall out of the hourly/daily rate-limit windows for free)."""
    seen_ids = set()
    active_by_provider = {}
    for item in data["items"]:
        item_id = item.get("id")
        if item_id in seen_ids:
            raise ValueError(f"queue.json inválido em {path}: id duplicado {item_id!r}.")
        seen_ids.add(item_id)
        status = item.get("status")
        provider = item.get("provider")
        if status == "active":
            if not item.get("started_at"):
                raise ValueError(f"queue.json inválido em {path}: item ativo sem started_at ({item_id!r}).")
            if provider in active_by_provider:
                raise ValueError(
                    f"queue.json inválido em {path}: mais de um item ativo para {provider!r} "
                    f"({active_by_provider[provider]!r} e {item_id!r})."
                )
            active_by_provider[provider] = item_id
        if status in ("done", "failed") and not item.get("finished_at"):
            raise ValueError(
                f"queue.json inválido em {path}: item {item_id!r} em {status!r} sem finished_at "
                "(quebraria as contagens de hora/dia)."
            )


def load(path):
    path = Path(path)
    if not path.is_file():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ValueError(f"queue.json inválido em {path}; preserve o arquivo e restaure uma cópia válida.") from None
    if not isinstance(data, dict):
        raise ValueError(f"queue.json incompatível em {path}.")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        if isinstance(version, int) and version > SCHEMA_VERSION:
            raise ValueError(
                f"queue.json em {path} foi gravado por uma versão mais nova do get-brolls "
                f"(schema {version}, esperado {SCHEMA_VERSION}); atualize antes de continuar."
            )
        raise ValueError(f"queue.json incompatível em {path} (schema_version esperado {SCHEMA_VERSION}).")
    if not isinstance(data.get("items"), list) or not isinstance(data.get("providers"), dict):
        raise ValueError(f"queue.json incompatível em {path}.")
    _check(data, path)
    return data


def save(path, data):
    """Private atomic write: temp file in the same folder, 0o600, then replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".queue-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
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
    for key, env_key, lo, hi in LIMITS:
        if key in block:
            limits[key] = _integer(f"RULES.md pacing.{provider}.{key}", block[key], lo, hi)
        if use_env:
            env_value = (os.environ.get(env_key) or "").strip()
            if env_value:
                limits[key] = _integer(env_key, env_value, lo, hi)
    if limits["min_s"] > limits["max_s"]:
        raise ValueError(
            f"Ritmo de {provider}: min_s não pode ser maior que max_s (GB_PACE_MIN_S/GB_PACE_MAX_S ou o bloco pacing em RULES.md)."
        )
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


def _waiting(moment, resume_at, reason):
    wait = max(0, int((resume_at - moment).total_seconds() + 0.999))
    return {"item": None, "wait_seconds": wait, "resume_at": _stamp(resume_at), "reason": reason}


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
    active = next((i for i in candidates if i["status"] == "active"), None)
    if active:
        return {"item": active, "wait_seconds": 0, "resume_at": None, "reason": "active"}
    pending = [i for i in candidates if i["status"] == "pending"]
    if not pending:
        return {"item": None, "wait_seconds": 0, "resume_at": None, "reason": "empty"}
    blocked = []
    for item in pending:
        limits = pacing(item["provider"], rules)
        state = _provider_state(data, item["provider"])
        resume_at, reason = _gate(data, item["provider"], moment, limits, state)
        if resume_at is None:
            return _activate(data, item, moment, limits, rng)
        blocked.append((resume_at, reason))
    return _waiting(moment, *min(blocked))


# Backward-compatible alias: `claim_next` is the current name, `next_item` is kept for
# any caller (internal or third-party) that still imports the old symbol.
next_item = claim_next


def is_cooldown_reason(reason):
    return bool(reason and COOLDOWN_RE.search(str(reason)))


def _open_cooldown(state, moment):
    state["cooldown_strikes"] = int(state.get("cooldown_strikes") or 0) + 1
    seconds = min(COOLDOWN_BASE_S * 2 ** (state["cooldown_strikes"] - 1), COOLDOWN_CAP_S)
    until = moment + timedelta(seconds=seconds)
    state["cooldown_until"] = _stamp(until)
    return {"seconds": seconds, "until": _stamp(until), "strikes": state["cooldown_strikes"]}


def mark(data, item_id, status, reason=None, at=None):
    if status not in ("done", "failed", "skipped"):
        raise ValueError("mark: use --done, --failed ou --skipped.")
    item = next((i for i in data["items"] if i["id"] == item_id), None)
    if item is None:
        raise ValueError(f"Item não está na fila: {item_id}")
    if item["status"] in TERMINAL_STATUSES:
        raise ValueError(f"Item {item_id} já está em estado final ({item['status']}); não é possível marcar novamente.")
    moment = _at(at)
    item["status"] = status
    item["finished_at"] = _stamp(moment)
    item["reason"] = reason
    state = _provider_state(data, item["provider"])
    cooldown = None
    if status == "done":
        state["cooldown_strikes"] = 0
        state["cooldown_until"] = None
    elif is_cooldown_reason(reason):
        cooldown = _open_cooldown(state, moment)
    return {"item": item, "cooldown": cooldown}


def record_cooldown(project, provider, reason, at=None):
    """Open a cooldown in an existing queue.json (used by instagram_pairs); None when absent."""
    path = queue_path(project)
    if not path.is_file():
        return None
    data = load(path)
    moment = _at(at)
    cooldown = _open_cooldown(_provider_state(data, provider), moment)
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
    return {"counts": counts, "providers": providers, "checked_at": _stamp(moment)}


def hint(project):
    """Read-only one-line view for `status`; None when the project has no queue."""
    path = queue_path(project)
    if not path.is_file():
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
        summary = report(data, rules=rules)
    except ValueError as exc:
        return {"error": str(exc)}
    counts = summary["counts"]
    pending_providers = [p for p in summary["providers"].values() if p["pending"] > 0]
    if not pending_providers or any(p["next_allowed_at"] is None for p in pending_providers):
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
    return {"counts": counts, "providers": summary["providers"], "line": line}


def summary_line(result):
    """Human line for the CLI, chosen by the action that produced the result."""
    action = result.get("action")
    if action == "add":
        return f"Enfileirei {len(result.get('added') or [])} URL(s); {len(result.get('duplicates') or [])} repetida(s); {result.get('pending')} pendente(s)."
    if action == "next":
        if result.get("item"):
            return f"Próximo item: {result['item']['id']} ({result['item']['url']})."
        if result.get("reason") == "empty":
            return "Fila vazia: nada pendente."
        return f"Aguarde {result.get('wait_seconds')} s (motivo: {result.get('reason')}); retome em {result.get('resume_at')}."
    if action == "mark":
        item = result.get("item") or {}
        cooldown = result.get("cooldown")
        extra = f" Cooldown de {cooldown['seconds']} s até {cooldown['until']}." if cooldown else ""
        return f"Marquei {item.get('id')} como {item.get('status')}.{extra}"
    counts = result.get("counts") or {}
    return f"Fila: {counts.get('pending', 0)} pendente(s), {counts.get('done', 0)} concluído(s), {counts.get('failed', 0)} com falha."


def _mark_status(args):
    """The one --done/--failed/--skipped flag the CLI accepts for `mark`."""
    for flag in ("done", "failed", "skipped"):
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
    else:
        result = report(data, rules=rules)
    return {"action": action, "queue": str(path), **result}
