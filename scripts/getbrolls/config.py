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
    "GB_YTDLP_PATH",
    "GB_VENV_PATH",
    "GB_FFMPEG_PATH",
    "GB_FFPROBE_PATH",
}

# Optional pins: an explicit path always wins over the usual discovery.
TOOL_PATH_KEYS = {
    "ffmpeg": "GB_FFMPEG_PATH",
    "ffprobe": "GB_FFPROBE_PATH",
    "yt-dlp": "GB_YTDLP_PATH",
}
PATH_KEYS = ("GB_YTDLP_PATH", "GB_VENV_PATH", "GB_FFMPEG_PATH", "GB_FFPROBE_PATH")


def load_env(path):
    path = Path(path)
    if not path.is_file():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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


def executable_override(key):
    """Executable pinned by key, or None when the variable is unset or empty."""
    value = (os.environ.get(key) or "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_file():
        raise ValueError(
            f"{key}: {value} não existe. Aponte para o executável correto ou remova a variável."
        )
    if not os.access(path, os.X_OK):
        raise ValueError(
            f"{key}: {value} não é executável. Ajuste as permissões ou remova a variável."
        )
    return str(path)


def venv_override():
    """Directory pinned by GB_VENV_PATH, or None when unset or empty."""
    value = (os.environ.get("GB_VENV_PATH") or "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_dir():
        raise ValueError(
            f"GB_VENV_PATH: {value} não é um diretório existente. Aponte para a pasta .venv ou remova a variável."
        )
    return path


def tool_path(name):
    """Executable name honouring its GB_*_PATH pin; unchanged when unset."""
    key = TOOL_PATH_KEYS.get(name)
    if not key:
        return name
    return executable_override(key) or name


def active_overrides():
    """Validated GB_*_PATH pins currently in effect, for doctor reporting."""
    result = {}
    for key in PATH_KEYS:
        value = venv_override() if key == "GB_VENV_PATH" else executable_override(key)
        if value:
            result[key] = str(value)
    return result


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
