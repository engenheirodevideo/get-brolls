"""Explicit .env loading: no interpolation, evaluation or secret output."""

import os
from pathlib import Path

KEYS = {
    "GB_GIF_SCOPE",
    "GB_RULES_FILE",
    "PEXELS_API_KEY",
    "PIXABAY_API_KEY",
    "YOUTUBE_API_KEY",
    "GB_PREVIEW_MODE",
    "GB_GIF_WIDTH",
    "GB_GIF_FPS",
    "GB_GIF_COLORS",
    "GB_GIF_MAX_MB",
    "GB_PREVIEW_MAX_SECONDS",
    "GB_STATIC_FRAMES",
}


def load_env(path):
    path = Path(path)
    if not path.is_file():
        return
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f".env: linha {number} inválida.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key not in KEYS:
            raise ValueError(f".env: variável desconhecida na linha {number}.")
        if value[:1] in ('"', "'"):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f".env: aspas inválidas na linha {number}.")
            value = value[1:-1]
        os.environ.setdefault(key, value)


def settings():
    def integer(key, default, lo, hi):
        try:
            value = int(os.getenv(key, str(default)))
        except ValueError:
            raise ValueError(key + ": use um inteiro.") from None
        if not lo <= value <= hi:
            raise ValueError(f"{key}: intervalo permitido {lo}–{hi}.")
        return value

    mode = os.getenv("GB_PREVIEW_MODE", "gif")
    if mode not in ("gif", "static"):
        raise ValueError("GB_PREVIEW_MODE: use gif ou static.")
    scope = os.getenv("GB_GIF_SCOPE", "broll")
    if scope not in ("broll", "full"):
        raise ValueError("GB_GIF_SCOPE: use broll ou full.")
    return dict(
        scope=scope,
        mode=mode,
        width=integer("GB_GIF_WIDTH", 360, 160, 720),
        fps=integer("GB_GIF_FPS", 8, 2, 18),
        colors=integer("GB_GIF_COLORS", 128, 32, 256),
        max_mb=integer("GB_GIF_MAX_MB", 5, 1, 30),
        max_seconds=integer("GB_PREVIEW_MAX_SECONDS", 10, 1, 30),
        frames=integer("GB_STATIC_FRAMES", 12, 1, 30),
    )
