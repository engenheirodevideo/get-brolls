"""The `diagnostics.jsonl` line and the text that goes next to it: tracebacks, stderr tails, the log path.

Everything that reaches the audit trail is redacted whole (`redaction.redact_diagnostic`), labelled by known
folder (`labels`) and only then cut, never the other way around. Nothing here writes the file: `runtime`
appends the line `diagnostic_line` builds."""

import json
import traceback
from pathlib import Path

from . import _paths, labels, redaction

# Quanto do fim de um traceback fica no diagnostics.jsonl: o frame que levantou e a linha
# do erro estão no fim, então o corte guarda a cauda, nunca o começo.
TRACEBACK_TAIL_CHARS = 8000


def redact_diagnostic(text, limit: int | None = redaction.REDACT_LIMIT):
    """`redact` mais um passe para `repr` e `traceback` do diagnostics.jsonl.

    Um segredo dentro de um dict (`{'access_token': '…'}`, ou o mesmo em JSON) não tem `=`
    nem nome de cabeçalho na frente: aqui, o valor de toda chave entre aspas com cara de
    segredo (`secret_name`) vira `[REDACTED]`. Só para texto que ninguém lê como mensagem:
    a `message` da pessoa continua passando só por `redact`. Idempotente e nunca levanta."""
    # pylint: disable-next=protected-access
    value = redaction._QUOTED_PAIR_PATTERN.sub(redaction._hide_quoted_secret, redaction.redact(text, limit=None))
    return value if limit is None else value[:limit]


def _redacted_event(value):
    """`value` (the JSON of an event: dict, list, text) with every text through `redact_diagnostic`, whole and
    only then labelled: a `password=` in a warning or a message the person reads is as secret in the journal as
    one in a `repr`. The text is bounded first (`labels.bounded`): nothing hostile is searched at full size."""
    if isinstance(value, dict):
        return {key: _redacted_event(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted_event(item) for item in value]
    if isinstance(value, str):
        bounded = labels.bounded(value)
        return bounded if bounded != value else redact_diagnostic(value, limit=None)
    return value


def diagnostic_line(event):
    """The `diagnostics.jsonl` line of `event`: every text redacted, known folders by label, then home as `~`
    (escaped forms too)."""
    return redaction.scrub_home(json.dumps(labels.labelled(_redacted_event(event)), ensure_ascii=False))


def redacted_traceback():
    """Traceback da exceção em tratamento, redigido e sem a pasta pessoal, para o diagnostics.jsonl.

    Longo demais, fica a cauda (`…` + os últimos `TRACEBACK_TAIL_CHARS` caracteres): é lá que
    estão o frame que levantou e a linha do erro."""
    # Redacted whole, then cut.
    text = labels.for_log(redaction.scrub_home(redact_diagnostic(traceback.format_exc(), limit=None)))
    return text if len(text) <= TRACEBACK_TAIL_CHARS else "…" + text[-TRACEBACK_TAIL_CHARS:]


STDERR_TAIL_MAX_CHARS = 600


def stderr_tail(stderr, limit=6):
    """Last `limit` non-empty stderr/detail lines, redacted, with folders by label, joined and capped.

    Shared helper for CLI stderr/detail text (subprocess stderr, HTTP error bodies), so callers cannot drift
    apart on secrets, URLs, machine paths or how much is surfaced; every cut comes after the redaction.
    """
    # Redacted whole, before the split: a private key spans lines, and only its first line says what it is.
    lines = [line.strip() for line in redaction.redact(stderr or "", limit=None).splitlines() if line.strip()]
    joined = " | ".join(labels.for_log(line, redaction.REDACT_LIMIT) for line in lines[-limit:])
    return joined[:STDERR_TAIL_MAX_CHARS]


def diagnostics_path(project):
    """`<projeto>/brolls/diagnostics.jsonl`; sem projeto, `$GB_HOME/diagnostics.jsonl`.

    `None` só quando nem a pasta pessoal se resolve (sem HOME no ambiente)."""
    if project:
        return Path(project).resolve() / "brolls/diagnostics.jsonl"
    try:
        return _paths.gb_home() / "diagnostics.jsonl"
    except RuntimeError:
        return None
