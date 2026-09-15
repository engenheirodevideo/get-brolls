"""Opt-in real provider smoke checks; never prints credentials or downloads video."""

import time, os
from . import providers


def live_checks():
    results = []
    for name in ("commons", "nasa", "pexels", "pixabay", "youtube"):
        key = providers.KEYS.get(name)
        if key and not os.getenv(key):
            results.append(
                {"provider": name, "status": "not_tested_missing_key", "env_key": key}
            )
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
                result["refresh"] = (
                    "media_url_available" if fresh.get("media_url") else "no_media_url"
                )
            result["seconds"] = round(time.monotonic() - start, 3)
            results.append(result)
        except (ValueError, OSError, KeyError, TypeError):
            # Provider errors can originate from arbitrary upstream response text.
            results.append(
                {
                    "provider": name,
                    "status": "failed",
                    "seconds": round(time.monotonic() - start, 3),
                    "detail": "Consulte configuração, conectividade e disponibilidade do provedor; nenhuma chave é exibida.",
                }
            )
    return {
        "checks": results,
        "scope": "Busca real limite 1 e refresh. Não verifica download integral, existência/reprodução de links sociais ou direitos. Pode consumir quota de API.",
    }
