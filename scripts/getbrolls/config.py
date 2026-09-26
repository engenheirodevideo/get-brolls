"""Explicit .env loading: no interpolation, evaluation or secret output."""

# pylint: disable=missing-function-docstring,cyclic-import
# Legado: ocorrência pré-existente em `settings` (corpo idêntico à origin/main).
# `cyclic-import` é novo nesta release (getbrolls.config -> getbrolls.runtime ->
# getbrolls.http): o import de `runtime.record_warning` é tardio (dentro de
# função) de propósito, seguindo a mesma convenção já usada em runtime.py para
# quebrar ciclos em tempo de execução (ver "avoids a runtime<->http/logs import
# cycle" nesse arquivo); o pylint só permite suprimir R0401 no módulo (a
# mensagem é reportada uma vez por todo o projeto, não por linha).

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


# Um plugin só pode receber do `.env` variáveis do próprio espaço de nomes,
# `<ID_EM_MAIÚSCULAS>_...` (ex.: `BANCO_HTTP_TOKEN` do plugin `banco_http`), nunca uma do
# core (`KEYS`, `GB_*`). E essas variáveis NUNCA vão para `os.environ`: ficam num mapa do
# core (`_PLUGIN_ENV`) que só `api.env` do plugin dono lê, por `plugin_env_value`. Subprocessos (ffmpeg, yt-dlp,
# git, playwright), OpenSSL e o `ssl` do Python não enxergam valor de `.env` de plugin —
# o que tira do caminho toda a classe `GIT_SSH_COMMAND`/`XDG_CONFIG_HOME`/`OPENSSL_CONF`,
# não só uma lista de nomes.
_PLUGIN_ENV: dict[str, tuple[str, str]] = {}

# Variáveis que ferramentas do sistema leem. Não são mais recusadas (o valor nunca chega
# ao ambiente); só geram aviso na prévia de enable/install/check e ao ler o `.env`.
TOOLCHAIN_ENV_PREFIXES = (
    "LD_",
    "DYLD_",
    "GIT_",
    "PYTHON",
    "NODE_",
    "NPM_",
    "PIP_",
    "HTTP_",
    "HTTPS_",
    "SSL_",
    "SSH_",
    "GPG_",
    "JAVA_",
    "CURL_",
    "REQUESTS_",
    "PERL",
    "RUBY",
    "BASH_",
    "ZSH",
    "LUA_",
    "GEM_",
    "BUNDLE_",
    "CARGO_",
    "RUSTUP_",
    "DOCKER_",
    "FFMPEG_",
    "YTDLP_",
    "YT_DLP_",
    "OPENSSL_",
    "XDG_",
    "PLAYWRIGHT_",
    "DENO_",
    "FONTCONFIG_",
)
TOOLCHAIN_ENV_KEYS = frozenset(
    {
        "ENV",
        "BASH_ENV",
        "SSLKEYLOGFILE",
        "ALL_PROXY",
        "NO_PROXY",
        "FTP_PROXY",
        "FFREPORT",
        "PATH",
        "HOME",
        "SHELL",
        "TMPDIR",
        "TEMP",
        "TMP",
        "IFS",
        "PS4",
        "EDITOR",
        "VISUAL",
        "PAGER",
        "BROWSER",
    }
)


def core_env_key(key):
    """Variável reconhecida pelo core (`GB_*` ou listada em `KEYS`)."""
    return key in KEYS or key.startswith("GB_")


def toolchain_env_key(key):
    """Variável que uma ferramenta do sistema lê (`GIT_SSH_COMMAND`, `XDG_CONFIG_HOME`,
    `OPENSSL_CONF`, `HTTPS_PROXY`...). Só gera aviso: vinda do `.env` de um plugin ela
    nunca chega ao ambiente, então não muda o comportamento de ferramenta nenhuma."""
    return key in TOOLCHAIN_ENV_KEYS or key.startswith(TOOLCHAIN_ENV_PREFIXES)


def plugin_env_value(plugin_id, key):
    """Valor que o `.env` guardou para `key`, só se `plugin_id` for o dono; senão `None`."""
    owner, value = _PLUGIN_ENV.get(key, (None, None))
    return value if owner == plugin_id else None


def env_is_set(key, owner=None):
    """A variável da fonte tem valor? Para `doctor`/`providers`/BRIEF dizerem se uma
    fonte está configurada. Vale o ambiente do processo; o `.env` de plugin só conta
    quando `owner` (o dono da fonte que declara `key`) é o mesmo plugin dono do valor
    — um plugin que declara como `env_key` uma variável do espaço de nomes de outro
    não aparece "configurado" com o segredo alheio."""
    return bool(os.environ.get(key)) or (owner is not None and bool(plugin_env_value(owner, key)))


def env_namespace_owner(key, plugin_ids):
    """Id do plugin cujo prefixo `<ID>_` contém `key` (o mais longo vence), ou `None`."""
    owner = None
    for plugin_id in plugin_ids:
        if key.startswith(plugin_id.upper() + "_") and (owner is None or len(plugin_id) > len(owner)):
            owner = plugin_id
    return owner


def installed_env():
    """`{id: [nomes de permissions.env]}` dos plugins em `plugins/`, só pelo manifesto."""
    from .sdk import loader
    from .sdk.manifest import read_manifest

    try:
        folders = [p for p in loader.plugins_root().iterdir() if p.is_dir() and not p.name.startswith((".", "_"))]
    except OSError:
        return {}
    installed = {}
    for folder in folders:
        try:
            manifest = read_manifest(folder)
        except (ValueError, OSError):
            continue
        installed[manifest["id"]] = list(manifest["permissions"]["env"])
    return installed


def plugin_env_owners():
    """`{variável: id do plugin}` que o `.env` aceita para plugins instalados: só as do
    espaço de nomes do próprio plugin, nunca uma variável do core."""
    installed = installed_env()
    return {
        key: plugin_id
        for plugin_id, keys in installed.items()
        for key in keys
        if not core_env_key(key) and env_namespace_owner(key, installed) == plugin_id
    }


def plugin_env_keys():
    """Nomes de `permissions.env` dos plugins instalados que o `.env` aceita."""
    return frozenset(plugin_env_owners())


def _refused_plugin_key(number, key, installed):
    """Mensagem para uma chave do `.env` que nenhum plugin pode receber, ou `None`."""
    declared_by = sorted(plugin_id for plugin_id, keys in installed.items() if key in keys)
    if declared_by:
        prefix = declared_by[0].upper() + "_"
        return (
            f".env: a variável {key} (linha {number}) é pedida pelo plugin {declared_by[0]}, mas o .env só "
            f"entrega a um plugin variáveis do espaço de nomes dele ({prefix}...), nunca uma do core ou de "
            "outro plugin. Tire a linha do .env; se o plugin precisa dela, defina-a no ambiente do processo."
        )
    from .sdk import loader

    try:
        state = loader.read_state()
    except (ValueError, OSError):
        return None
    known = set(state.get("enabled", {})) | set(state.get("last_pins", {})) | set(state.get("sources", {}))
    owner = env_namespace_owner(key, known - set(installed))
    if owner is None:
        return None
    return (
        f".env: a variável {key} (linha {number}) é do plugin {owner}, que não está mais em plugins/. "
        f"Tire a linha do .env, ou reinstale o plugin {owner}."
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
    """Lê o `.env`: chaves do core (`KEYS`) vão para o ambiente do processo, como
    sempre; as de `permissions.env` de plugins instalados ficam só em `_PLUGIN_ENV`,
    lidas por `api.env` do plugin dono, nunca exportadas. As do core entram primeiro —
    `GB_HOME` no próprio `.env` decide em qual `plugins/` procurar os manifestos.
    Chave que ninguém declara é erro."""
    _PLUGIN_ENV.clear()
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
    owners = plugin_env_owners()
    for number, key, value in unknown:
        if key not in owners:
            raise ValueError(
                _refused_plugin_key(number, key, installed_env())
                or f".env: variável desconhecida na linha {number}: {key}. Aceitas: " + ", ".join(sorted(KEYS)) + "."
            )
        _PLUGIN_ENV.setdefault(key, (owners[key], value))
        if toolchain_env_key(key):
            from .runtime import record_warning

            record_warning(
                "PLUGIN_ENV_TOOLCHAIN",
                f".env: {key} (linha {number}) é do plugin {owners[key]} e tem nome de variável que ferramentas "
                "do sistema leem; ela chega só ao plugin, por api.env, nunca ao ambiente dos subprocessos.",
            )


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
