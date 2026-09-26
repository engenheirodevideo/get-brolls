"""Perguntar aos resolvedores de plugin por um arquivo de som ou música (experimental).

Só o comando que exporta chama isto, só para os tipos de `RESOLVER_KINDS` e só
depois de as pastas do projeto e da pessoa não acharem nada. O resolvedor diz
onde está o arquivo; o core confere tudo pelo disco e pelo descritor aberto. A
cópia acontece depois, com `safe_copy.recheck(..., roots=...)` + `safe_copy.copy_from_fd`: sempre
cópia, nunca hardlink nem mudança de permissão no original da pessoa.
"""

import logging
import os
from pathlib import Path

from .. import http as core_http
from .. import logs
from ..http import ProviderError
from . import guard, safe_copy
from .contracts import RESOLVER_KINDS, ResolverHit
from .files import is_link

_log = logs.get("sdk")

LICENSE_LABEL = "Licença informada pelo plugin"


def _plain_hit(result):
    """`(caminho, licença)` como `str` puros, ou `None` quando o resolvedor não achou.

    Roda dentro do isolamento: `os.fspath` de um objeto do plugin pode rodar código
    dele. Nada que não seja `str` puro sai daqui."""
    if result is None:
        return None
    if type(result) is not ResolverHit:
        raise ProviderError("o resolvedor tem que devolver ResolverHit(path, license) ou None.")
    raw_path, license_text = result.path, result.license
    raw = os.fspath(raw_path)
    if type(raw) is not str or not raw or "\x00" in raw or not Path(raw).is_absolute():
        raise ProviderError("ResolverHit.path tem que ser um caminho absoluto de arquivo.")
    if license_text is not None:
        if type(license_text) is not str or not license_text.strip() or len(license_text) > guard.LICENSE_MAX_CHARS:
            raise ProviderError(f"ResolverHit.license tem que ser texto de até {guard.LICENSE_MAX_CHARS} caracteres.")
        license_text = license_text.strip()
    return str(raw), license_text


class _RefusedError(Exception):
    pass


def _checked_file(owner, raw, roots, extensions):
    """Confere o arquivo no disco (nada de código do plugin roda aqui) e devolve
    `(caminho resolvido, stat)`; o descritor aberto para conferir já sai fechado.
    `roots` são as raízes guardadas com o resolvedor, como texto."""
    roots = [Path(root) for root in roots]
    shown = guard.plain_line(Path(raw).name or "arquivo", limit=120)
    if not roots:
        raise _RefusedError("nenhuma pasta de permissions.paths vale neste sistema.")
    if is_link(Path(raw)):
        raise _RefusedError(f"{shown} é um link; aponte para o arquivo de verdade.")
    try:
        resolved = Path(raw).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _RefusedError(f"{shown} não foi encontrado ({type(exc).__name__}).") from None
    if not safe_copy.within_roots(resolved, roots):
        logs.event(_log, logging.WARNING, "plugin_path_refused", plugin=owner, reason="resolver_outside_roots")
        raise _RefusedError(f"{shown} está fora de permissions.paths.")
    if resolved.suffix.lower() not in extensions:
        raise _RefusedError(f"{shown} não tem uma extensão aceita ({', '.join(extensions)}).")
    try:
        # `open_under`: com o arquivo aberto, o caminho ainda fica dentro da raiz — uma
        # pasta do meio trocada por link depois da conferência acima é recusada.
        fd, info = safe_copy.open_under(resolved, roots)
    except safe_copy.UnsafeFileError as exc:
        if exc.reason == safe_copy.OUTSIDE:
            logs.event(_log, logging.WARNING, "plugin_path_refused", plugin=owner, reason="resolver_outside_roots")
        reasons = {
            safe_copy.NOT_REGULAR: f"{shown} não é um arquivo.",
            safe_copy.LINKED: f"{shown} tem mais de um nome no disco (hardlink) e foi recusado.",
            safe_copy.OUTSIDE: f"{shown} está fora de permissions.paths.",
            safe_copy.CHANGED: f"{shown} mudou enquanto era aberto.",
        }
        raise _RefusedError(reasons.get(exc.reason) or f"{shown} não pôde ser aberto ({exc.type_name}).") from None
    os.close(fd)
    cap = core_http.DOWNLOAD_MAX_BYTES
    if info.st_size > cap:
        raise _RefusedError(f"{shown} passa do teto de {cap // (1024 * 1024)} MB.")
    return resolved, info


def _failure_warning(owner, spec, failure):
    """Registra `plugin_call_failed` e devolve o aviso de um resolvedor que falhou."""
    logs.event(_log, logging.WARNING, "plugin_call_failed", plugin=owner, resolver=spec.name, error=failure.type_name)
    if failure.text:
        return guard.prefixed(owner, failure.text)
    return f"Plugin {owner}: o resolvedor {spec.name} falhou ({failure.type_name})."


def resolve_with_plugins(registry, kind, name, extensions):
    """`(acerto, avisos)`: o primeiro arquivo válido que um resolvedor achou para
    `name`, ou `None`; cada resolvedor que falhou ou devolveu algo inválido vira um
    aviso `Plugin <id>: …` e o próximo é consultado. Nunca levanta por culpa de plugin.

    O acerto é `{"path", "store", "license", "resolver", "st_dev", "st_ino",
    "st_size"}`: `store` é sempre o id do plugin dono; `license` é só informativa
    (use `license_line` para mostrá-la); `st_*` são conferidos de novo na cópia."""
    if kind not in RESOLVER_KINDS:
        raise ValueError(f"Resolvedor de plugin só vale para {', '.join(RESOLVER_KINDS)}; veio {kind!r}.")
    if type(name) is not str or not name:
        raise ValueError("O nome procurado tem que ser texto não vazio.")
    wanted = tuple(sorted({(ext if ext.startswith(".") else "." + ext).lower() for ext in map(str, extensions)}))
    warnings = []
    for owner, spec in registry.resolvers_for(kind):
        outcome = guard.attempt(owner, lambda spec=spec: _plain_hit(spec.resolve(kind, name)))
        if outcome.failure is not None:
            warnings.append(_failure_warning(owner, spec, outcome.failure))
            continue
        if outcome.value is None:
            continue
        raw, license_text = outcome.value
        try:
            resolved, info = _checked_file(owner, raw, registry.resolver_roots(spec.name), wanted)
        except _RefusedError as exc:
            warnings.append(f"Plugin {owner}: o resolvedor {spec.name} foi ignorado: {exc}")
            continue
        hit = {
            "path": str(resolved),
            "store": owner,
            "license": guard.sanitize_text(owner, license_text) if license_text else None,
            "resolver": spec.name,
            "st_dev": info.st_dev,
            "st_ino": info.st_ino,
            "st_size": info.st_size,
        }
        logs.event(_log, logging.INFO, "plugin_resolved", plugin=owner, resolver=spec.name, kind=kind)
        return hit, warnings
    return None, warnings


def license_line(owner, text):
    """A licença que o plugin informou, numa linha e sem marcação ativa (link, imagem,
    ênfase saem escapados), sempre com o prefixo que diz quem a informou. Nunca vale
    como `permit` nem entra em `rights.evidence`."""
    from ..delivery import inert

    clean = guard.sanitize_text(owner, str(text))
    return f"{LICENSE_LABEL} {owner}: {inert(clean, plugin=owner)}"
