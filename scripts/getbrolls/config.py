"""Explicit .env loading: no interpolation, evaluation or secret output."""

# pylint: disable=missing-function-docstring,cyclic-import
# Legado: ocorrência pré-existente em `settings` (corpo idêntico ao código anterior à 2.6.0).
# `cyclic-import` vem do ciclo getbrolls.config <-> getbrolls.env_loader: o `env_loader` importa este módulo, e
# uma função-ponte (`installed_env`) o procura de volta com import tardio, dentro de função, de propósito; o
# pylint só permite suprimir R0401 no
# módulo (a mensagem é reportada uma vez por todo o projeto, não por linha).

import os
from pathlib import Path

from . import labels
from ._paths import strict_exists, strict_is_dir, strict_is_file
from .errors import EnvReadError, UsageError

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
    # Pasta com `.venv/` (yt-dlp) e `.tools/` (Playwright); padrão
    # `$GB_HOME/runtime/<versão das dependências>/`, e o checkout usa os da própria pasta.
    "GB_RUNTIME_DIR",
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
    # Segundos que um comando espera o projeto ocupado por outro antes de `LOCKED`; padrão 0 (recusa na hora).
    "GB_LOCK_WAIT_S",
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
    # `json` força a saída de máquina; hoje toda saída já é JSON, e outro valor não muda nada.
    "GB_OUTPUT",
}

# Só do ambiente do processo; nunca num .env. `GB_PROFILE` (caminho do getbrolls.toml ou
# `off`) e `GB_PROFILE_SHA256` (o sha que o processo pai conferiu) passam o perfil já
# ativado aos filhos: vindos de um .env, forjariam a confiança no perfil.
PROCESS_ONLY_KEYS = frozenset({"GB_ENV_FILE", "GB_PROFILE", "GB_PROFILE_SHA256"})

# Sufixos que marcam uma variável como segredo: nenhuma delas tem por que alcançar um
# processo filho que nunca fala com um provedor.
SECRET_ENV_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET")

# Quem sobe o servidor de fundo (`serve.start_background`) põe isto no ambiente do filho, já sem os segredos
# (`SECRET_ENV_SUFFIXES`). O filho também lê o `.env` ao subir; com o marcador, o comando `serve` não traz de volta as
# chaves de segredo que o pai tirou (`load_environment`). As demais configurações do `.env` valem como em qualquer
# processo. O valor é uma senha de uso interno, conferida por igualdade: a variável exportada à mão (`=0`, `=1`) não
# faz nenhum outro comando perder as chaves.
SERVE_CHILD_ENV = "GETBROLLS_SERVE_LOG_FILTER"
SERVE_CHILD_TOKEN = "serve-child"  # noqa: S105 - marcador interno, não é senha

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


def _typed_env(source, path):
    """O `.env` como a pessoa o escreveu (`--env-file`, `GB_ENV_FILE`), ou `None` quando foi o getbrolls que o achou."""
    if source == "GB_ENV_FILE":
        return os.environ.get("GB_ENV_FILE") or str(path)
    return str(path) if source == "flag" else None


def _env_shown(path, typed):
    return typed if typed is not None else labels.shown(path)


def _read_env_text(path, typed=None):
    """Texto do `.env` em UTF-8, com ou sem BOM (o "UTF-8 com BOM" do Bloco de Notas).

    Outra codificação (UTF-16, cp1252 com acento) é erro de uso em português, com o motivo
    `env_encoding`, em vez do erro de codec do Python. O arquivo que existe mas não abre é
    `EnvReadError`, com o caminho dele."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        error = EnvReadError(exc.errno, exc.strerror, str(path))
        error.shown = _env_shown(path, typed)  # a mensagem do IO_ERROR nomeia o arquivo assim
        raise error from None
    except UnicodeDecodeError:
        raise UsageError(
            f".env: {_env_shown(path, typed)} não está em UTF-8. Salve o arquivo como UTF-8 e repita.",
            reason="env_encoding",
        ) from None


def _parse_env(path, typed=None):
    for number, raw_line in enumerate(_read_env_text(path, typed).splitlines(), 1):
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


def cache_dir_alias_in_use():
    """`GETBROLLS_CACHE_DIR` é quem decide a pasta do cache (`GB_CACHE_DIR` ausente ou vazia)."""
    return not os.environ.get("GB_CACHE_DIR") and bool(os.environ.get("GETBROLLS_CACHE_DIR"))


# Chave do core → valor que um `.env` pôs no ambiente deste processo (nunca o que já estava lá).
_ENV_APPLIED: dict[str, str] = {}


def _is_home_env(path, gb_home_value):
    """O arquivo é o `.env` da pasta pessoal: a atual ou a que a própria linha `GB_HOME` apontaria.

    Compara caminhos resolvidos, não a rota que trouxe o arquivo: `$GB_HOME/.env` lido por
    `GB_ENV_FILE` ou `--env-file` continua sendo o `.env` de `$GB_HOME`.
    """
    from . import _paths  # tardio: o `_paths` importa este módulo dentro de funções

    target = Path(path).resolve()
    homes = (_paths.gb_home(), Path(gb_home_value).expanduser()) if gb_home_value else (_paths.gb_home(),)
    return any(target == (home / ".env").resolve() for home in homes)


def _refuse_env_entries(entries, source, path=None):
    """Recusa, antes de exportar qualquer coisa, chaves que um `.env` não pode definir."""
    for number, key, value in entries:
        if key in PROCESS_ONLY_KEYS:
            raise UsageError(
                f".env: {key} (linha {number}) só vale no ambiente do processo; tire a linha do .env.",
                reason="env_process_only_key",
            )
        if key == "GB_HOME" and (source == "gb_home" or (path is not None and _is_home_env(path, value))):
            raise UsageError(
                f".env de $GB_HOME: GB_HOME (linha {number}) não pode ser definido aqui; a pasta pessoal vem "
                "do ambiente do processo ou do padrão ~/.getbrolls. Tire a linha.",
                reason="env_gb_home",
            )


def is_serve_child(args):
    """Este processo é o servidor de fundo: o comando é `serve` e o pai pôs a senha do marcador no ambiente."""
    return getattr(args, "command", None) == "serve" and os.environ.get(SERVE_CHILD_ENV) == SERVE_CHILD_TOKEN


# O que o carregamento do `.env` precisa do perfil, do carregador de plugins e do runtime mora em `env_loader`
# (camada acima). `installed_env` segue respondendo aqui para o carregador de plugins (`sdk.loader`, abaixo do
# `env_loader`), que a lê em `config`; só repassa a chamada: o `env_loader` importa este módulo, então o caminho de
# volta é uma busca tardia, dentro de função.
def installed_env():
    """Repassa a chamada para `env_loader.installed_env`."""
    from . import env_loader  # tardio: o `env_loader` importa este módulo

    return env_loader.installed_env()


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
    if not strict_is_file(path):
        detail = "não é um arquivo executável" if strict_exists(path) else "não existe"
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
    if not strict_is_dir(path):
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


def _setting(key, default):
    """Valor de uma configuração de prévia; vazia (ou só espaços) vale o padrão.

    `GB_GIF_WIDTH=` num `.env` é "não definida", como já era para `GB_LOG_LEVEL` e
    `GB_CACHE_DIR`: nunca um erro que derruba todo comando."""
    return (os.getenv(key) or "").strip() or default


LOCK_WAIT_MAX_S = 3600.0


def lock_wait_s():
    """Validated GB_LOCK_WAIT_S: seconds to wait for a project another command holds; empty or unset is 0.

    Same style as the other numeric settings: a clear ValueError naming the key, never a silent fallback."""
    text = _setting("GB_LOCK_WAIT_S", "0")
    try:
        value = float(text)
    except ValueError:
        raise ValueError("GB_LOCK_WAIT_S: use um número de segundos.") from None
    if not 0 <= value <= LOCK_WAIT_MAX_S:
        raise ValueError(f"GB_LOCK_WAIT_S: intervalo permitido 0–{int(LOCK_WAIT_MAX_S)} segundos.")
    return value


def settings():
    def integer(key, default, lo, hi):
        try:
            value = int(_setting(key, str(default)))
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
    mode = _setting("GB_PREVIEW_MODE", "gif")
    if mode not in ("gif", "static"):
        raise ValueError("GB_PREVIEW_MODE: use gif ou static.")
    scope = _setting("GB_GIF_SCOPE", "broll")
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
