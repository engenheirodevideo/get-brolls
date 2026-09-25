"""Opt-in real provider smoke checks; never prints credentials or downloads video."""

import time

from . import providers
from .config import env_is_set
from .http import ProviderError
from .runtime import redact
from .sdk.contracts import CORE

METADATA_ONLY = "no_media_url (fonte só-metadados)"


def _plugin_detail(owner, error):
    """Texto do erro de uma fonte de plugin — "Plugin <id>: …", já saneado pelo
    guarda (sem segredo, uma linha) — em vez da frase genérica; `None` nos outros."""
    text = str(error) if isinstance(error, ProviderError) else ""
    if owner in (None, CORE) or not text.startswith(f"Plugin {owner}:"):
        return None
    return f"[{type(error).__name__}] {redact(text)}"


def _plugin_refresh(source, owner, row):
    """Refresh de uma fonte de plugin sem transformar desenho em falha: a
    rota traz o arquivo (nem chama refresh), e fonte sem `media_url` na busca é
    só-metadados. Refresh que falha de verdade vira campo, não derruba a busca ok."""
    if source.capabilities.route:
        return {"refresh": "route"}
    if not row.get("media_url"):
        return {"refresh": METADATA_ONLY}
    try:
        fresh = providers.refresh(row)
    except ProviderError as error:
        detail = _plugin_detail(owner, error) or f"[{type(error).__name__}] refresh sem arquivo."
        return {"refresh": "failed", "refresh_detail": detail}
    return {"refresh": "media_url_available" if fresh.get("media_url") else METADATA_ONLY}


def live_checks():
    results = []
    from .sdk.registry import get_registry

    reg = get_registry()
    builtin = ("commons", "nasa", "pexels", "pixabay", "youtube")
    extra = tuple(
        n
        for n in reg.provider_names()
        if n not in builtin and reg.provider(n).capabilities.search  # type: ignore[union-attr] - name veio de provider_names()
    )
    for name in builtin + extra:
        key = reg.provider(name).capabilities.env_key  # type: ignore[union-attr] - name veio de builtin/provider_names()
        if key and not env_is_set(key):
            results.append({"provider": name, "status": "not_tested_missing_key", "env_key": key})
            continue
        start = time.monotonic()
        try:
            rows = providers.search(name, "earth", 1)
            result = {
                "provider": name,
                "status": "search_ok" if rows else "search_ok_empty",
                "count": len(rows),
            }
            if rows and name != "youtube":
                owner = reg.owner("provider", name)
                if owner != CORE:
                    result.update(_plugin_refresh(reg.provider(name), owner, rows[0]))
                else:
                    fresh = providers.refresh(rows[0])
                    result["refresh"] = "media_url_available" if fresh.get("media_url") else "no_media_url"
            result["seconds"] = round(time.monotonic() - start, 3)
            results.append(result)
        except (ValueError, OSError) as error:
            # Provider/network failures: arbitrary upstream response text, timeouts,
            # DNS, etc. Never a key, only the exception class name — except the text of
            # a plugin's own error, which the guard already sanitized for the person.
            results.append(
                {
                    "provider": name,
                    "status": "failed",
                    "seconds": round(time.monotonic() - start, 3),
                    "detail": _plugin_detail(reg.owner("provider", name), error)
                    or f"[{type(error).__name__}] Consulte configuração, conectividade e "
                    "disponibilidade do provedor; nenhuma chave é exibida.",
                }
            )
        except (KeyError, TypeError, AttributeError) as error:
            # Bug signatures, not provider/network trouble: keep them visibly distinct
            # so a maintainer doesn't go looking for a connectivity problem instead.
            results.append(
                {
                    "provider": name,
                    "status": "failed",
                    "seconds": round(time.monotonic() - start, 3),
                    "detail": f"[{type(error).__name__}] Erro interno inesperado (bug), não é problema de "
                    "configuração/conectividade do provedor.",
                }
            )
    return {
        "checks": results,
        "scope": "Busca real limite 1 e refresh. Não verifica download integral, existência/reprodução de links sociais ou direitos. Pode consumir quota de API.",
    }
