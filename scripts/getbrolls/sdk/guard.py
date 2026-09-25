"""Guarda-corpos do core sobre tudo que um plugin devolve.

Plugin sugere candidatos; quem aprova, assina condições de uso e publica é gente.
Por isso aprovação, direitos, saída e estado são sempre reescritos aqui, campos
de topo (e de dentro de `preview`/`rights`/`acquisition`) fora do allowlist
caem, e URLs passam pelo mesmo `public_url` do core. Todo valor que sai de um
plugin passa primeiro por um round-trip de JSON: um objeto de terceiro com
`__eq__`/`__deepcopy__`/`.get` hostil não chega a rodar dentro da sanitização.
"""

import builtins
import copy
import itertools
import json
import logging
import os
import re
import types
import unicodedata
from typing import Any, NamedTuple

from .. import logs
from ..http import ProviderError, public_url
from ..models import empty_output
from ..runtime import redact
from .contracts import PluginError, RouteResult
from .jsonschema import errors
from .schemas import load

_log = logs.get("sdk")

# Identificador Python simples (usado só para validar o que `safe_type_name`
# devolve — nunca para nomear nada em si).
_TYPE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def safe_type_name(exc):
    """Nome da classe de `exc`, sem rodar código do plugin para obtê-lo.

    `type(exc).__name__` parece inofensivo, mas uma metaclasse de terceiro pode
    declarar `__name__` como property — que roda na hora do acesso e pode
    levantar (inclusive `SystemExit`) ou devolver qualquer texto (inclusive um
    segredo). Por isso lê o descritor cru guardado em `type.__dict__`, que
    aponta direto para o slot interno do tipo (`tp_name`) e nunca passa pela
    metaclasse de quem chamou. Qualquer falha nessa leitura, ou um resultado
    que não pareça um identificador Python simples, cai em `"Exception"` — o
    chamador sempre recebe um texto seguro de repetir em mensagem/log.

    `isinstance(name, str)` não bastaria: CPython aceita uma SUBCLASSE de str
    como nome de classe e guarda essa instância como veio (sem normalizar para
    `str` puro) — `type.__dict__["__name__"].__get__(...)` pode devolver
    exatamente essa subclasse. Uma subclasse hostil pode sobrescrever
    `__format__`/`__str__`/`__eq__` para levantar (inclusive `SystemExit`) ou
    devolver texto diferente na hora de formatar/logar. `type(name) is not str`
    (nunca `isinstance`) barra qualquer subclasse ANTES de fazer qualquer outra
    coisa com ela — inclusive antes do regex abaixo, que só roda depois desse
    curto-circuito (`or`), então nunca toca num objeto de tipo não confiável.
    """
    try:
        name = type.__dict__["__name__"].__get__(type(exc))
    except BaseException:  # noqa: BLE001 - a própria leitura do nome não pode falhar por conta do plugin; isolamento deliberado, sem propósito de continuar processando nada além do fallback abaixo
        return "Exception"
    if type(name) is not str or not _TYPE_NAME_RE.fullmatch(name):
        return "Exception"
    return str(name)


# --- Isolamento uniforme -------------------------------------------------------
#
# Toda porta de entrada de código de plugin (search/resolve/refresh, Route.prepare,
# handler de comando, register() no loader e no `plugins check`, a checagem de
# contrato e a normalização do que o plugin devolveu) passa por `attempt`/`isolated`:
#
# - pega `BaseException` (menos `KeyboardInterrupt`): `SystemExit`, `GeneratorExit`,
#   `asyncio.CancelledError` e uma classe que herde `BaseException` direto não
#   derrubam a CLI nem saem como traceback cru;
# - o tipo vem de `safe_type_name` (nunca `type(exc).__name__`, que roda metaclasse);
# - o texto da exceção só é lido quando o TIPO EXATO é um dos confiáveis
#   (`PluginError` público ou recusa escrita pelo core) e o único argumento é um
#   `str` puro — nunca `str(exc)`/`repr(exc)`, que rodariam `__str__` do plugin;
#   esse texto ainda passa por `sanitize_text` (uma linha, sem controle, `redact`,
#   valores de `permissions.env` trocados por [REDACTED], teto de 300);
# - o erro novo é levantado FORA do `except`, então nem `__cause__` nem `__context__`
#   carregam a exceção do plugin até `traceback.format_exc()` em `runtime.audited`.

MESSAGE_MAX_CHARS = 300

# id do plugin → nomes de `permissions.env`; preenchido pelo `PluginApi` na carga.
_ENV_KEYS: dict[str, tuple[str, ...]] = {}

# Tipos embutidos de exceção: só o `plugins check` (ferramenta de quem escreve o
# plugin, rodando a pasta que ele mesmo apontou) mostra o texto deles — e só quando
# o tipo é EXATAMENTE um desses (sem `__str__` sobrescrito por ninguém).
_BUILTIN_EXCEPTIONS = tuple(
    value for value in vars(builtins).values() if isinstance(value, type) and issubclass(value, BaseException)
)


class Failure(NamedTuple):
    """Falha de código de plugin, já reduzida ao que é seguro repetir."""

    type_name: str
    text: str | None


def remember_env(owner, keys):
    """Guarda os nomes de `permissions.env` do plugin para `sanitize_text`."""
    _ENV_KEYS[owner] = tuple(key for key in keys if type(key) is str)


def _trusted_types():
    from .api import ApiError
    from .manifest import ManifestError
    from .registry import RegistryError

    return (PluginError, ProviderError, ApiError, ManifestError, RegistryError)


def sanitize_text(owner, text):
    """Uma linha, sem caractere de controle/formatação, sem segredo, até 300 caracteres."""
    for key in _ENV_KEYS.get(owner, ()):
        value = os.environ.get(key)
        if value:
            text = text.replace(value, "[REDACTED]")
    text = "".join(
        " " if unicodedata.category(char)[0] == "C" or unicodedata.category(char) in ("Zl", "Zp") else char
        for char in text
    )
    text = redact(" ".join(text.split()))
    if len(text) > MESSAGE_MAX_CHARS:
        text = text[: MESSAGE_MAX_CHARS - 1].rstrip() + "…"
    return text


def plugin_text(owner, exc, *, builtin=False):
    """Texto seguro de `exc`, ou `None` quando ele não pode chegar à pessoa.

    Só tipos exatos (`type(exc) is ...`, comparado por identidade — nem `in` numa
    tupla, que chamaria `__eq__`/`__hash__` de metaclasse): uma subclasse do plugin
    pode ter `__str__`/`args` hostis. `builtin=True` (só no `plugins check`) aceita
    também os tipos embutidos exatos."""
    cls = type(exc)
    allowed = _trusted_types() + (_BUILTIN_EXCEPTIONS if builtin else ())
    if not any(cls is candidate for candidate in allowed):
        return None
    args = BaseException.__dict__["args"].__get__(exc)
    if type(args) is not tuple or len(args) != 1 or type(args[0]) is not str:
        return None
    return sanitize_text(owner, args[0]) or None


class Outcome(NamedTuple):
    """Resultado de `attempt`: `failure is None` quando `fn` voltou normalmente."""

    value: Any
    failure: Failure | None


def attempt(owner, fn, *args, builtin_text=False) -> Outcome:
    """`Outcome(resultado, None)` ou `Outcome(None, Failure)`; só `KeyboardInterrupt` atravessa."""
    try:
        return Outcome(fn(*args), None)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001 - isolamento deliberado de código de plugin de terceiro; ver o bloco acima
        failure = Failure(safe_type_name(exc), plugin_text(owner, exc, builtin=builtin_text))
    return Outcome(None, failure)


def isolated(owner, fn, *args, on_failure, log_fields=None) -> Any:
    """Roda `fn(*args)`; na falha, registra `plugin_call_failed` (se `log_fields`) e
    levanta `on_failure(Failure)` — fora do `except`, sem cadeia até o plugin."""
    outcome = attempt(owner, fn, *args)
    if outcome.failure is None:
        return outcome.value
    if log_fields is not None:
        logs.event(
            _log, logging.WARNING, "plugin_call_failed", plugin=owner, **log_fields, error=outcome.failure.type_name
        )
    raise on_failure(outcome.failure) from None


def prefixed(owner, text):
    """`Plugin <id>: <texto>`, sem repetir o prefixo quando o texto já o traz."""
    prefix = f"Plugin {owner}:"
    return text if text.startswith(prefix) else f"{prefix} {text}"


def without_prefix(owner, text):
    prefix = f"Plugin {owner}:"
    return text[len(prefix) :].strip() if text.startswith(prefix) else text


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
UNAVAILABLE_ACQUISITION = {"status": "unavailable", "method": None, "evidence": []}
LICENSE_MAX_CHARS = 500
PENDING_SEGMENT = {"start_s": None, "end_s": None, "revision": 0}
PENDING_APPROVAL = {"status": "pending", "by": None, "at": None, "revision": None}


def call(owner, provider, fn, *args):
    return isolated(
        owner,
        fn,
        *args,
        log_fields={"provider": provider},
        on_failure=lambda failure: ProviderError(
            prefixed(owner, failure.text)
            if failure.text
            else f"Plugin {owner}: falha em {provider} ({failure.type_name})."
        ),
    )


def rows(owner, provider, fn, *args, limit=None):
    """Como `call`, mas também materializa a lista dentro do mesmo isolamento: um
    gerador que levanta no meio da iteração, ou um retorno que não é lista,
    tupla nem gerador, vira `ProviderError` aqui — nunca uma exceção crua
    (ou um `TypeError` de `list(int)`) até quem chamou `search`.

    Com `limit`, materializa no máximo essa quantidade via `itertools.islice`:
    um gerador infinito de um plugin mal-comportado não trava `search` a
    consumir o resto que ninguém vai ler — e só as linhas de fato materializadas
    passam por `plugin_candidate` depois, então o corte também evita o custo de
    sanitizar candidato que seria descartado pelo `[:limit]` no final de `search`."""

    def materialize():
        result = fn(*args)
        if not isinstance(result, (list, tuple)) and not isinstance(result, types.GeneratorType):
            raise ProviderError(f"{provider} tem que devolver uma lista de candidatos.")
        return list(result) if limit is None else list(itertools.islice(result, limit))

    return call(owner, provider, materialize)


def _normalize(value, owner):
    """Só dict/list/str/number/bool/None sobrevivem a isto; qualquer outra
    coisa (objeto de terceiro, dict subclass hostil, referência circular,
    NaN/Infinity/-Infinity — que um `json.dumps` padrão deixaria passar como
    token não-JSON) vira `ProviderError` aqui, antes de qualquer
    `.get`/comparação abaixo. O `json.dumps` roda isolado: um `items()`/`__iter__`
    do plugin pode levantar qualquer coisa (inclusive `SystemExit`)."""

    def failed(failure):
        if failure.type_name in ("TypeError", "ValueError", "RecursionError"):
            return ProviderError(f"Plugin {owner}: devolveu algo que não é serializável em JSON ({failure.type_name}).")
        return ProviderError(f"Plugin {owner}: falha ao normalizar o retorno ({failure.type_name}).")

    return isolated(owner, lambda: json.loads(json.dumps(value, allow_nan=False)), on_failure=failed)


def _kept(raw, allowed):
    """Só as chaves de `allowed` presentes em `raw`, mais a lista (ordenada) do
    que foi descartado — usado tanto para montar o candidato limpo quanto para
    o log de `plugin_candidate_sanitized`."""
    raw = raw if isinstance(raw, dict) else {}
    kept = {k: raw[k] for k in allowed if k in raw}
    dropped = sorted(k for k in raw if k not in allowed)
    return kept, dropped


def plugin_candidate(item, provider, owner, download=True, route=None):  # noqa: C901, PLR0912, PLR0915 - um campo guardado por seção do candidato (Finding 2 do fix round 1)
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
    if route is not None:
        # capabilities.route: quem escreve a rota é o core, a partir da capability
        # (conferida no `api.finish()` como rota do mesmo plugin) — nunca o candidato.
        acquisition = {"status": "available", "method": f"plugin:{route}", "evidence": []}
        if raw_acq not in ({}, UNAVAILABLE_ACQUISITION, acquisition):
            tampered.append("acquisition.route")
    else:
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
            "evidence": [v for v in raw_acq_evidence if isinstance(v, str)]
            if isinstance(raw_acq_evidence, list)
            else [],
        }
        if not download and acquisition != UNAVAILABLE_ACQUISITION:
            # Fonte só-metadados (capabilities.download=False, sem rota): o core nunca
            # vai baixar por ela, então "available" aqui seria promessa que ninguém
            # cumpre. Vence a capability, não o que o plugin tentou escrever.
            tampered.append("acquisition.download")
            acquisition = copy.deepcopy(UNAVAILABLE_ACQUISITION)

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


def _route_result(result):
    """Extrai (caminho, licença) como `str` puros de dentro do `try` do chamador:
    nada de objeto do plugin (subclasse, `__fspath__` hostil) sai daqui."""
    if not isinstance(result, RouteResult):
        raise ProviderError("a rota tem que devolver RouteResult(path, license).")
    raw_path = os.fspath(result.path)
    if type(raw_path) is not str or not raw_path:
        raise ProviderError("RouteResult.path tem que ser um caminho de arquivo.")
    license_text = result.license
    if license_text is not None:
        if type(license_text) is not str or not license_text.strip() or len(license_text) > LICENSE_MAX_CHARS:
            raise ProviderError(f"RouteResult.license tem que ser texto de até {LICENSE_MAX_CHARS} caracteres.")
        license_text = license_text.strip()
    return str(raw_path), license_text


def route_call(owner, name, route, item, workdir):
    """Roda `route.prepare(item, workdir)` com `api.download`/`api.local_file` presos
    ao `workdir`; qualquer falha (incl. SystemExit) vira `ProviderError` com o id do plugin."""
    from .api import route_scope

    def run():
        with route_scope(owner, workdir):
            return _route_result(route.prepare(item, workdir))

    return isolated(
        owner,
        run,
        log_fields={"route": name},
        on_failure=lambda failure: ProviderError(
            prefixed(owner, failure.text)
            if failure.text
            else f"Plugin {owner}: falha na rota {name} ({failure.type_name})."
        ),
    )
