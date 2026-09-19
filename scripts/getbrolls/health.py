"""Opt-in real provider smoke checks; never prints credentials or downloads video."""

import os
import time

from . import providers


def live_checks():
    results = []
    for name in ("commons", "nasa", "pexels", "pixabay", "youtube"):
        key = providers.KEYS.get(name)
        if key and not os.getenv(key):
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
                fresh = providers.refresh(rows[0])
                result["refresh"] = "media_url_available" if fresh.get("media_url") else "no_media_url"
            result["seconds"] = round(time.monotonic() - start, 3)
            results.append(result)
        except (ValueError, OSError) as error:
            # Provider/network failures: arbitrary upstream response text, timeouts,
            # DNS, etc. Never a key, only the exception class name.
            results.append(
                {
                    "provider": name,
                    "status": "failed",
                    "seconds": round(time.monotonic() - start, 3),
                    "detail": f"[{type(error).__name__}] Consulte configuração, conectividade e "
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
