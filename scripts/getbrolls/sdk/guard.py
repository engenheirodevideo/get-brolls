"""Guarda-corpos do core sobre tudo que um plugin devolve.

Plugin sugere candidatos; quem aprova, assina condições de uso e publica é gente.
Por isso aprovação, direitos, saída e estado são sempre reescritos aqui, campos
de topo (e de dentro de `preview`/`rights`/`acquisition`) fora do allowlist
caem, e URLs passam pelo mesmo `public_url` do core. Todo valor que sai de um
plugin passa primeiro por um round-trip de JSON: um objeto de terceiro com
`__eq__`/`__deepcopy__`/`.get` hostil não chega a rodar dentro da sanitização.
"""

import copy
import itertools
import json
import logging
import types

from .. import logs
from ..http import ProviderError, public_url
from ..models import empty_output
from .jsonschema import errors
from .schemas import load

_log = logs.get("sdk")

# Campos de topo que um plugin pode preencher; o resto cai em silêncio (mas
# aparece no log `plugin_candidate_sanitized`). approval/output/state/segment/
# errors estão aqui porque são *aceitos como chave* — o valor é sempre
# reescrito por esta função, nunca lido do plugin.
ALLOWED_TOP = (
    "schema_version",
    "id",
    "provider",
    "source_id",
    "source_url",
    "title",
    "creator",
    "query",
    "collected_at",
    "match",
    "media",
    "media_url",
    "preview",
    "rights",
    "acquisition",
    "segment",
    "approval",
    "output",
    "state",
    "errors",
)
CREATOR_KEYS = ("name", "url", "handle")
MATCH_KEYS = ("kind", "reason")
MEDIA_KEYS = ("duration_s", "width", "height", "fps", "kind")
# poster_path/contact_sheet_path ficam na allowlist (são campos legítimos do
# candidato) mas o valor é sempre forçado a None: só o core grava caminho local.
PREVIEW_KEYS = ("poster_path", "contact_sheet_path", "poster_url", "embed_url", "seek_mode")
RIGHTS_KEYS = ("status", "license_name", "license_url", "evidence", "attribution")
ACQUISITION_KEYS = ("status", "method", "evidence")
ACQUISITION_STATUSES = ("available", "unavailable")
ACQUISITION_METHODS = (None, "https", "yt-dlp")
PENDING_SEGMENT = {"start_s": None, "end_s": None, "revision": 0}
PENDING_APPROVAL = {"status": "pending", "by": None, "at": None, "revision": None}


def call(owner, provider, fn, *args):
    try:
        return fn(*args)
    except ProviderError as exc:
        logs.event(_log, logging.WARNING, "plugin_call_failed", plugin=owner, provider=provider, error="ProviderError")
        raise ProviderError(f"Plugin {owner}: {exc}") from exc
    except (
        Exception,
        SystemExit,
    ) as exc:  # código de plugin é de terceiro: a falha (incl. SystemExit) vira erro de fonte, não queda da CLI; KeyboardInterrupt continua propagando
        logs.event(
            _log, logging.WARNING, "plugin_call_failed", plugin=owner, provider=provider, error=type(exc).__name__
        )
        raise ProviderError(f"Plugin {owner}: falha em {provider} ({type(exc).__name__}).") from exc


def rows(owner, provider, fn, *args, limit=None):
    """Como `call`, mas também materializa a lista dentro do mesmo `try`: um
    gerador que levanta no meio da iteração, ou um retorno que não é lista,
    tupla nem gerador, vira `ProviderError` aqui — nunca uma exceção crua
    (ou um `TypeError` de `list(int)`) até quem chamou `search`.

    Com `limit`, materializa no máximo essa quantidade via `itertools.islice`:
    um gerador infinito de um plugin mal-comportado não trava `search` a
    consumir o resto que ninguém vai ler — e só as linhas de fato materializadas
    passam por `plugin_candidate` depois, então o corte também evita o custo de
    sanitizar candidato que seria descartado pelo `[:limit]` no final de `search`."""
    try:
        result = fn(*args)
        if not isinstance(result, (list, tuple)) and not isinstance(result, types.GeneratorType):
            raise ProviderError(f"{provider} tem que devolver uma lista de candidatos.")
        materialized = list(result) if limit is None else list(itertools.islice(result, limit))
    except ProviderError as exc:
        logs.event(_log, logging.WARNING, "plugin_call_failed", plugin=owner, provider=provider, error="ProviderError")
        raise ProviderError(f"Plugin {owner}: {exc}") from exc
    except (
        Exception,
        SystemExit,
    ) as exc:  # código de plugin é de terceiro: a falha (incl. SystemExit) vira erro de fonte, não queda da CLI; KeyboardInterrupt continua propagando
        logs.event(
            _log, logging.WARNING, "plugin_call_failed", plugin=owner, provider=provider, error=type(exc).__name__
        )
        raise ProviderError(f"Plugin {owner}: falha em {provider} ({type(exc).__name__}).") from exc
    return materialized


def _normalize(value, owner):
    """Só dict/list/str/number/bool/None sobrevivem a isto; qualquer outra
    coisa (objeto de terceiro, dict subclass hostil, referência circular,
    NaN/Infinity/-Infinity — que um `json.dumps` padrão deixaria passar como
    token não-JSON) vira `ProviderError` aqui, antes de qualquer
    `.get`/comparação abaixo."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProviderError(
            f"Plugin {owner}: devolveu algo que não é serializável em JSON ({type(exc).__name__})."
        ) from exc
    except Exception as exc:  # objeto de terceiro pode quebrar de um jeito que json.dumps não prevê
        raise ProviderError(f"Plugin {owner}: falha ao normalizar o retorno ({type(exc).__name__}).") from exc


def _kept(raw, allowed):
    """Só as chaves de `allowed` presentes em `raw`, mais a lista (ordenada) do
    que foi descartado — usado tanto para montar o candidato limpo quanto para
    o log de `plugin_candidate_sanitized`."""
    raw = raw if isinstance(raw, dict) else {}
    kept = {k: raw[k] for k in allowed if k in raw}
    dropped = sorted(k for k in raw if k not in allowed)
    return kept, dropped


def plugin_candidate(item, provider, owner, download=True):  # noqa: C901, PLR0912, PLR0915 - um campo guardado por seção do candidato (Finding 2 do fix round 1)
    item = _normalize(item, owner)
    if not isinstance(item, dict):
        raise ProviderError(f"Plugin {owner}: {provider} devolveu um candidato que não é objeto.")

    tampered = sorted(k for k in item if k not in ALLOWED_TOP)

    creator, dropped = _kept(item.get("creator"), CREATOR_KEYS)
    tampered += [f"creator.{k}" for k in dropped]
    match, dropped = _kept(item.get("match"), MATCH_KEYS)
    tampered += [f"match.{k}" for k in dropped]
    media, dropped = _kept(item.get("media"), MEDIA_KEYS)
    tampered += [f"media.{k}" for k in dropped]

    raw_preview, dropped = _kept(item.get("preview"), PREVIEW_KEYS)
    tampered += [f"preview.{k}" for k in dropped]
    if raw_preview.get("poster_path") is not None:
        tampered.append("preview.poster_path")
    if raw_preview.get("contact_sheet_path") is not None:
        tampered.append("preview.contact_sheet_path")
    preview = {
        "poster_path": None,
        "contact_sheet_path": None,
        "poster_url": public_url(raw_preview.get("poster_url")),
        "embed_url": public_url(raw_preview.get("embed_url")),
        "seek_mode": raw_preview.get("seek_mode", "unknown"),
    }

    raw_rights, dropped = _kept(item.get("rights"), RIGHTS_KEYS)
    tampered += [f"rights.{k}" for k in dropped]
    if raw_rights.get("status", "unknown") != "unknown":
        tampered.append("rights.status")
    raw_evidence = raw_rights.get("evidence")
    rights = {
        "status": "unknown",
        "license_name": raw_rights.get("license_name"),
        "license_url": raw_rights.get("license_url"),
        "evidence": [v for v in raw_evidence if isinstance(v, str)] if isinstance(raw_evidence, list) else [],
        "attribution": raw_rights.get("attribution"),
    }

    raw_acq, dropped = _kept(item.get("acquisition"), ACQUISITION_KEYS)
    tampered += [f"acquisition.{k}" for k in dropped]
    raw_status = raw_acq.get("status")
    raw_method = raw_acq.get("method")
    acq_status = raw_status if raw_status in ACQUISITION_STATUSES else "unavailable"
    acq_method = raw_method if raw_method in ACQUISITION_METHODS else None
    if acq_method != raw_method:
        tampered.append("acquisition.method")
        acq_status = "unavailable"
    if acq_status != raw_status:
        tampered.append("acquisition.status")
    raw_acq_evidence = raw_acq.get("evidence")
    acquisition = {
        "status": acq_status,
        "method": acq_method,
        "evidence": [v for v in raw_acq_evidence if isinstance(v, str)] if isinstance(raw_acq_evidence, list) else [],
    }
    if not download and acquisition != {"status": "unavailable", "method": None, "evidence": []}:
        # Fonte só-metadados (capabilities.download=False, ex.: o exemplo pasta_local):
        # o core nunca vai baixar por ela, então "available" aqui seria promessa que
        # ninguém cumpre. Vence a capability, não o que o plugin tentou escrever.
        tampered.append("acquisition.download")
        acquisition = {"status": "unavailable", "method": None, "evidence": []}

    if item.get("segment") not in (None, PENDING_SEGMENT):
        tampered.append("segment")
    if item.get("approval") not in (None, PENDING_APPROVAL):
        tampered.append("approval")
    if item.get("output") not in (None, empty_output()):
        tampered.append("output")
    if item.get("state", "candidate") != "candidate":
        tampered.append("state")
    if item.get("errors") not in (None, []):
        tampered.append("errors")

    if tampered:
        logs.event(
            _log,
            logging.WARNING,
            "plugin_candidate_sanitized",
            plugin=owner,
            provider=provider,
            fields=",".join(sorted(set(tampered))),
        )

    clean = {
        "schema_version": item.get("schema_version"),
        "id": item.get("id"),
        "provider": item.get("provider"),
        "source_id": item.get("source_id"),
        "source_url": public_url(item.get("source_url")),
        "title": item.get("title"),
        "creator": creator,
        "query": item.get("query"),
        "collected_at": item.get("collected_at"),
        "match": match,
        "media": media,
        "media_url": public_url(item.get("media_url")),
        "preview": preview,
        "rights": rights,
        "acquisition": acquisition,
        "segment": copy.deepcopy(PENDING_SEGMENT),
        "approval": copy.deepcopy(PENDING_APPROVAL),
        "output": empty_output(),
        "state": "candidate",
        "errors": [],
    }
    if clean.get("provider") != provider or clean.get("id") != f"{provider}:{clean.get('source_id')}":
        raise ProviderError(f"Plugin {owner}: candidato sem id/provider coerentes; use api.candidate().")
    problems = errors(clean, load("candidate"))
    if problems:
        raise ProviderError(f"Plugin {owner}: candidato fora do schema ({'; '.join(problems[:3])}).")
    return clean


def refreshed(current, fresh, owner):
    fresh = _normalize(fresh, owner)
    if not isinstance(fresh, dict):
        raise ProviderError(f"Plugin {owner}: refresh tem que devolver o candidato.")
    media_url = public_url(fresh.get("media_url"))
    if not media_url:
        raise ProviderError("Arquivo do provedor não está mais disponível")
    return {**current, "media_url": media_url}
