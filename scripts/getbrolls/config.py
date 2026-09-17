"""Explicit .env loading: no interpolation, evaluation or secret output."""

import os
from pathlib import Path

KEYS = {
    "GB_GIF_SCOPE",
    "GB_RULES_FILE",
    # Pasta pessoal da skill (RULES.md global e biblioteca); padrão ~/.getbrolls.
    "GB_HOME",
    # `off` desliga leitura e escrita da biblioteca global.
    "GB_LIBRARY",
    "PEXELS_API_KEY",
    "PIXABAY_API_KEY",
    "YOUTUBE_API_KEY",
    "GB_PREVIEW_MODE",
    "GB_GIF_WIDTH",
    "GB_GIF_FPS",
    "GB_GIF_COLORS",
    "GB_GIF_MAX_MB",
    "GB_PREVIEW_MAX_SECONDS",
    # Teto da varredura do vídeo inteiro em `preview --scan`, em segundos.
    "GB_SCAN_MAX_SECONDS",
    "GB_STATIC_FRAMES",
    "GB_YTDLP_PATH",
    "GB_VENV_PATH",
    "GB_FFMPEG_PATH",
    "GB_FFPROBE_PATH",
    # Fonte TrueType para rotular o contact sheet (CLI e helpers Bash de YouTube).
    "GB_FONT_FILE",
    # Ritmo da fila social (`queue`): intervalo e tetos por provedor.
    "GB_PACE_MIN_S",
    "GB_PACE_MAX_S",
    "GB_MAX_PER_HOUR",
    "GB_MAX_PER_DAY",
    # Pausas do yt-dlp entre pedidos: "requests,min,max" em segundos.
    "GB_YTDLP_SLEEP",
    # `1` faz `deliver` copiar em vez de hardlinkar: cópias independentes, editáveis.
    "GB_DELIVERY_COPY",
}

# Optional pins: an explicit path always wins over the usual discovery.
TOOL_PATH_KEYS = {
    "ffmpeg": "GB_FFMPEG_PATH",
    "ffprobe": "GB_FFPROBE_PATH",
    "yt-dlp": "GB_YTDLP_PATH",
}
PATH_KEYS = (*TOOL_PATH_KEYS.values(), "GB_VENV_PATH")


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
            raise ValueError(
                f".env: variável desconhecida na linha {number}: {key}. Aceitas: " + ", ".join(sorted(KEYS)) + "."
            )
        if value[:1] in ('"', "'"):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f".env: aspas inválidas na linha {number}.")
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _pinned(key):
    """Trimmed value of the pin, or None when the variable is unset or empty."""
    return (os.environ.get(key) or "").strip() or None


def executable_override(key):
    """Executable pinned by key, resolved to an absolute path; None when unset."""
    value = _pinned(key)
    if value is None:
        return None
    # Absoluto antes de validar: o pin não pode depender da pasta atual.
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        detail = "não é um arquivo executável" if path.exists() else "não existe"
        raise ValueError(f"{key}: {value} {detail}. Aponte para o executável correto ou remova a variável.")
    # No Windows a executabilidade vem da extensão; os.access(X_OK) aceita qualquer legível.
    if os.name != "nt" and not os.access(path, os.X_OK):
        raise ValueError(f"{key}: {value} não é executável. Ajuste as permissões ou remova a variável.")
    return str(path)


def venv_override():
    """Directory pinned by GB_VENV_PATH, resolved to an absolute path; None when unset."""
    value = _pinned("GB_VENV_PATH")
    if value is None:
        return None
    path = Path(value).expanduser().resolve()
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


def pin_override(key):
    """Resolved value of one GB_*_PATH pin, whatever kind of path it holds."""
    return venv_override() if key == "GB_VENV_PATH" else executable_override(key)


def active_overrides():
    """Validated GB_*_PATH pins currently in effect, for doctor reporting."""
    resolved = {key: pin_override(key) for key in PATH_KEYS}
    return {key: str(value) for key, value in resolved.items() if value}


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
        scan_max_seconds=integer("GB_SCAN_MAX_SECONDS", 900, 30, 7200),
    )
