"""Explicit .env loading: no interpolation, evaluation or secret output."""

import os
from pathlib import Path

# Folga de ponto flutuante ao comparar um intervalo com o teto de prévia. `16.1 - 6.1`
# dá 10.000000000000002 em binário: sem a folga, o `--end` que o próprio `inspect`
# sugere seria recusado pelo `preview` logo depois. Um intervalo igual ao teto vale.
CAP_EPSILON = 1e-6

KEYS = {
    "GB_GIF_SCOPE",
    "GB_RULES_FILE",
    # Caminho alternativo do BRIEF.md do projeto; padrão `<projeto>/BRIEF.md`.
    "GB_BRIEF_FILE",
    # Pasta pessoal da skill (RULES.md global e biblioteca); padrão ~/.getbrolls.
    "GB_HOME",
    # `off` desliga leitura e escrita da biblioteca global.
    "GB_LIBRARY",
    # Lista de ids de plugin separados por vírgula: só filtra os habilitados com
    # pin válido em plugins.json (nunca carrega sem pin). `off` desliga todos.
    "GB_PLUGINS",
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
    # Pasta do cache local (drawtext, respostas HTTP); padrão ~/.cache/getbrolls.
    # GETBROLLS_CACHE_DIR (nome antigo) continua funcionando via ambiente real, mas
    # só o nome novo é aceito em `.env`.
    "GB_CACHE_DIR",
    # Nível do getbrolls.log: DEBUG, INFO, WARNING, ERROR ou off. Padrão INFO.
    "GB_LOG_LEVEL",
    # `1` espelha as linhas do getbrolls.log em stderr, antes do envelope JSON.
    "GB_LOG_STDERR",
}

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

# Optional pins: an explicit path always wins over the usual discovery.
TOOL_PATH_KEYS = {
    "ffmpeg": "GB_FFMPEG_PATH",
    "ffprobe": "GB_FFPROBE_PATH",
    "yt-dlp": "GB_YTDLP_PATH",
}
PATH_KEYS = (*TOOL_PATH_KEYS.values(), "GB_VENV_PATH")


# Nomes que um plugin pode declarar em `permissions.env` mas que o `.env` nunca
# aceita em nome dele: mexeriam no core, no git ou no carregamento de processos.
_PLUGIN_ENV_REFUSED_PREFIXES = ("GB_", "GETBROLLS_", "GIT_", "PYTHON", "LD_", "DYLD_")
_PLUGIN_ENV_REFUSED = frozenset({"PATH", "HOME", "SHELL", "TMPDIR", "TEMP", "TMP", "USER", "LANG"})


def plugin_env_keys():
    """Nomes de `permissions.env` dos plugins INSTALADOS em `plugins/` — lidos só do
    manifesto, sem rodar código de plugin —, que o `.env` passa a aceitar (C H-1)."""
    from .sdk import loader
    from .sdk.manifest import read_manifest

    root = loader.plugins_root()
    try:
        folders = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))]
    except OSError:
        return frozenset()
    keys = set()
    for folder in folders:
        try:
            keys.update(read_manifest(folder)["permissions"]["env"])
        except (ValueError, OSError):
            continue
    return frozenset(
        key for key in keys if key not in _PLUGIN_ENV_REFUSED and not key.startswith(_PLUGIN_ENV_REFUSED_PREFIXES)
    )


def _parse_env(path):
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f".env: linha {number} inválida.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] in ('"', "'"):
            if len(value) < 2 or value[-1] != value[0]:  # noqa: PLR2004 - a pair of quotes: opening + closing
                raise ValueError(f".env: aspas inválidas na linha {number}.")
            value = value[1:-1]
        yield number, key, value


def load_env(path):
    """Lê o `.env`: chaves do core (`KEYS`) e as de `permissions.env` de plugins
    instalados. As do core entram primeiro — `GB_HOME` no próprio `.env` decide em
    qual `plugins/` procurar os manifestos. Chave que ninguém declara é erro."""
    path = Path(path)
    if not path.is_file():
        return
    entries = list(_parse_env(path))
    for _number, key, value in entries:
        if key in KEYS:
            os.environ.setdefault(key, value)
    unknown = [entry for entry in entries if entry[1] not in KEYS]
    if not unknown:
        return
    declared = plugin_env_keys()
    for number, key, value in unknown:
        if key not in declared:
            raise ValueError(
                f".env: variável desconhecida na linha {number}: {key}. Aceitas: " + ", ".join(sorted(KEYS)) + "."
            )
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


def cache_root():
    """Pasta do cache local: GB_CACHE_DIR vence; GETBROLLS_CACHE_DIR é o fallback antigo.

    Uma string vazia conta como "não definida" (o `or` cai para o nome antigo,
    e depois para o padrão) — isso é proposital: uma variável exportada vazia
    não deve forçar o cache para a raiz.
    """
    value = os.environ.get("GB_CACHE_DIR") or os.environ.get("GETBROLLS_CACHE_DIR")
    return Path(value) if value else Path.home() / ".cache" / "getbrolls"


def log_level():
    """Validated GB_LOG_LEVEL: DEBUG/INFO/WARNING/ERROR, or `OFF` to disable getbrolls.log.

    Same validation style as the rest of this module: an unrecognised value is a
    clear ValueError, not a silent fallback.
    """
    value = (os.getenv("GB_LOG_LEVEL") or "INFO").strip().upper()
    if value == "OFF":
        return "OFF"
    if value not in LOG_LEVELS:
        raise ValueError("GB_LOG_LEVEL: use DEBUG, INFO, WARNING, ERROR ou off.")
    return value


def log_stderr():
    """Whether GB_LOG_STDERR is set truthy (`1`, `true`, `yes`, `on`); default off."""
    return (os.getenv("GB_LOG_STDERR") or "").strip().lower() in ("1", "true", "yes", "on")


def settings():
    def integer(key, default, lo, hi):
        try:
            value = int(os.getenv(key, str(default)))
        except ValueError:
            raise ValueError(key + ": use um inteiro.") from None
        if not lo <= value <= hi:
            raise ValueError(f"{key}: intervalo permitido {lo}–{hi}.")
        return value

    # Validated here too, like every other setting, so a bad GB_LOG_LEVEL fails
    # the command fast with the same shape as any other invalid .env value. The
    # logging setup itself (logs.configure()) re-reads it independently and
    # never raises — see logs.py for why.
    log_level()
    mode = os.getenv("GB_PREVIEW_MODE", "gif")
    if mode not in ("gif", "static"):
        raise ValueError("GB_PREVIEW_MODE: use gif ou static.")
    scope = os.getenv("GB_GIF_SCOPE", "broll")
    if scope not in ("broll", "full"):
        raise ValueError("GB_GIF_SCOPE: use broll ou full.")
    return {
        "scope": scope,
        "mode": mode,
        "width": integer("GB_GIF_WIDTH", 360, 160, 720),
        "fps": integer("GB_GIF_FPS", 8, 2, 18),
        "colors": integer("GB_GIF_COLORS", 128, 32, 256),
        "max_mb": integer("GB_GIF_MAX_MB", 5, 1, 30),
        "max_seconds": integer("GB_PREVIEW_MAX_SECONDS", 10, 1, 30),
        "frames": integer("GB_STATIC_FRAMES", 12, 1, 30),
        "scan_max_seconds": integer("GB_SCAN_MAX_SECONDS", 900, 30, 7200),
    }
