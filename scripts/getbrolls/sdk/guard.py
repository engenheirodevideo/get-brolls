"""Guarda-corpos do core sobre tudo que um plugin devolve.

Plugin sugere candidatos; quem aprova, assina condições de uso e publica é gente.
Por isso aprovação, direitos, saída e estado são sempre reescritos aqui, campos
de topo fora do schema caem, e URLs passam pelo mesmo `public_url` do core.
"""

import copy
import logging

from .. import logs
from ..http import ProviderError, public_url
from ..models import empty_output
from .jsonschema import errors
from .schemas import load

_log = logs.get("sdk")


def call(owner, provider, fn, *args):
    try:
        return fn(*args)
    except ProviderError as exc:
        logs.event(_log, logging.WARNING, "plugin_call_failed", plugin=owner, provider=provider, error="ProviderError")
        raise ProviderError(f"Plugin {owner}: {exc}") from exc
    except Exception as exc:  # código de plugin é de terceiro: a falha vira erro de fonte, não queda da CLI
        logs.event(
            _log, logging.WARNING, "plugin_call_failed", plugin=owner, provider=provider, error=type(exc).__name__
        )
        raise ProviderError(f"Plugin {owner}: falha em {provider} ({type(exc).__name__}).") from exc


def _tampered(item, allowed):
    """Nomes dos campos que o core vai descartar ou reescrever neste candidato."""
    dropped = sorted(k for k in item if k not in allowed)
    pending = {"status": "pending", "by": None, "at": None, "revision": None}
    rewritten = []
    if item.get("approval") not in (None, pending):
        rewritten.append("approval")
    if isinstance(item.get("rights"), dict) and item["rights"].get("status", "unknown") != "unknown":
        rewritten.append("rights.status")
    if item.get("output") not in (None, empty_output()):
        rewritten.append("output")
    if item.get("state", "candidate") != "candidate":
        rewritten.append("state")
    return dropped + rewritten


def plugin_candidate(item, provider, owner):
    if not isinstance(item, dict):
        raise ProviderError(f"Plugin {owner}: {provider} devolveu um candidato que não é objeto.")
    allowed = set(load("candidate")["properties"]) - {"ext"}
    tampered = _tampered(item, allowed)
    if tampered:
        logs.event(
            _log,
            logging.WARNING,
            "plugin_candidate_sanitized",
            plugin=owner,
            provider=provider,
            fields=",".join(tampered),
        )
    clean = {k: copy.deepcopy(v) for k, v in item.items() if k in allowed}
    if clean.get("provider") != provider or clean.get("id") != f"{provider}:{clean.get('source_id')}":
        raise ProviderError(f"Plugin {owner}: candidato sem id/provider coerentes; use api.candidate().")
    clean["source_url"] = public_url(clean.get("source_url"))
    clean["media_url"] = public_url(clean.get("media_url"))
    clean["approval"] = {"status": "pending", "by": None, "at": None, "revision": None}
    rights = clean.get("rights")
    if not isinstance(rights, dict):
        rights = {}
    clean["rights"] = {**rights, "status": "unknown"}
    clean["output"] = empty_output()
    clean["state"] = "candidate"
    problems = errors(clean, load("candidate"))
    if problems:
        raise ProviderError(f"Plugin {owner}: candidato fora do schema ({'; '.join(problems[:3])}).")
    return clean


def refreshed(current, fresh, owner):
    if not isinstance(fresh, dict):
        raise ProviderError(f"Plugin {owner}: refresh tem que devolver o candidato.")
    media_url = public_url(fresh.get("media_url"))
    if not media_url:
        raise ProviderError("Arquivo do provedor não está mais disponível")
    return {**current, "media_url": media_url}
