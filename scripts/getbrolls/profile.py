"""`getbrolls.toml`: perfil de workspace que fixa pastas, executáveis e plugins.

O perfil é um arquivo TOML pequeno (stdlib `tomllib`) que a CLI descobre a partir do
`--project` e depois da pasta atual. Ele pode apontar executáveis e pastas, então só
vale quando é confiável:

- `env`: veio de `GB_PROFILE` no ambiente do processo (a mesma fronteira de confiança
  de `GB_VENV_PATH`); com `GB_PROFILE_SHA256` junto, o sha tem que bater;
- `inside_home`: é exatamente `<GB_HOME>/getbrolls.toml`, com o `GB_HOME` de antes do
  perfil;
- `trusted`: `profile trust` gravou o sha destes bytes em `trusted-profiles.json`, que
  mora no `GB_HOME` de antes do perfil — nunca no `home` que o próprio perfil escolhe,
  senão ele se autoconfiaria.

O arquivo em si nunca é um link simbólico e, no POSIX, é do usuário atual e não é
gravável por grupo nem por outros.

Precedência: ambiente (inclusive `.env`) vence o perfil, que vence o padrão. `home` é
aplicado antes da escolha do `.env` (`apply_home`), o resto depois (`apply_rest`).
`plugins` é a exceção: é um teto — o perfil só estreita o `GB_PLUGINS` do ambiente.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import stat
import tempfile
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__, _paths
from .errors import PrerequisiteError, UsageError

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

PROFILE_NAME = "getbrolls.toml"
TRUST_FILE = "trusted-profiles.json"
MAX_BYTES = 64 * 1024
MAX_DEPTH = 64
SCHEMA_VERSION = 1
LOCK_TIMEOUT_S = 10.0
FIELDS: dict[str, str] = {
    "home": "GB_HOME",
    "cache_dir": "GB_CACHE_DIR",
    "runtime_dir": "GB_RUNTIME_DIR",
    "plugins": "GB_PLUGINS",
    "tools.ffmpeg": "GB_FFMPEG_PATH",
    "tools.ffprobe": "GB_FFPROBE_PATH",
    "tools.ytdlp": "GB_YTDLP_PATH",
    "tools.venv": "GB_VENV_PATH",
}
TOP_LEVEL = ("schema_version", "requires", "home", "cache_dir", "runtime_dir", "plugins", "marketplaces", "tools")
TOOLS = ("ffmpeg", "ffprobe", "ytdlp", "venv")
DIR_FIELDS = ("home", "cache_dir", "runtime_dir")
# Comandos que relatam o problema do perfil em vez de falhar.
TOLERANT_COMMANDS = ("doctor", "profile")
# Mesmo formato dos nomes de marketplace do core (`sdk/marketplace`).
MARKETPLACE_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_LEADING_VERSION_RE = re.compile(r"\d+(?:\.\d+){0,2}")

_UNTRUSTED = (
    "O getbrolls.toml deste workspace ainda não é confiável: ele pode fixar executáveis e pastas. "
    "Revise com `getbrolls profile show` e confie com `getbrolls profile trust` (ou ignore com `--profile off`)."
)
_CHANGED = "O getbrolls.toml mudou desde o `profile trust`; revise (`profile show`) e confie de novo."
_REQUIRES = (
    "Este workspace pede getbrolls {spec} (getbrolls.toml); esta instalação é {version}. "
    "Use uma instalação compatível ou ajuste `requires`."
)
MESSAGES = {"untrusted": _UNTRUSTED, "changed": _CHANGED}


@dataclass(frozen=True)
class Profile:
    """Um getbrolls.toml válido, já traduzido para chaves `GB_*`."""

    path: Path  # caminho absoluto resolvido do arquivo
    sha256: str  # dos bytes exatos que foram lidos
    values: dict[str, str]  # chave GB → valor final (pastas absolutas; plugins "a,b" ou "off")
    requires: str | None
    plugins: tuple[str, ...] | None = None  # None: o perfil não fala de plugins
    marketplaces: tuple[str, ...] | None = None  # None: o perfil não restringe marketplaces


@dataclass(frozen=True)
class Located:
    """Onde o perfil foi (ou não foi) encontrado."""

    path: Path | None
    source: str | None  # "flag" | "GB_PROFILE" | "project" | "cwd" | None
    disabled: bool  # --profile off / GB_PROFILE=off
    searched: tuple[Path, ...]


@dataclass
class Active:  # pylint: disable=too-many-instance-attributes
    """Estado do perfil desta execução, para `doctor` e `profile show`."""

    located: Located
    profile: Profile | None
    trust: str  # "env" | "inside_home" | "trusted" | "untrusted" | "changed" | "disabled" | "none" | "invalid"
    error: str | None  # mensagem do perfil inválido
    requires_problem: str | None
    pre_home: Path  # GB_HOME de antes do perfil (onde mora o trusted-profiles.json)
    applied: dict[str, str]  # chave GB → valor que este perfil pôs no ambiente


# O que os `apply_*` puseram no ambiente, e o GB_HOME de antes de o perfil trocar o dele.
_FROM_PROFILE: dict[str, str] = {}
_PRE_HOME: list[Path] = []


def reset_state() -> None:
    """Esquece o que um perfil anterior aplicou (nova ativação, ou testes)."""
    _FROM_PROFILE.clear()
    _PRE_HOME.clear()


def from_profile() -> dict[str, str]:
    """Chaves `GB_*` que o perfil pôs no ambiente, com o valor que pôs."""
    return dict(_FROM_PROFILE)


def _leading_version(text: str) -> str:
    """Prefixo numérico da versão: `"2.6.0rc1"` → `"2.6.0"`, antes de `satisfies`.

    Mesma semântica do helper de versões do contrato de plugins; quando ele existir em
    `sdk/manifest`, este aqui passa a usá-lo.
    """
    match = _LEADING_VERSION_RE.match(text.strip())
    return match.group(0) if match else "0"


# --- leitura -------------------------------------------------------------------------


def _absolute(value: str | Path) -> Path:
    """Caminho absoluto e normalizado, sem seguir links (o `abspath` do pathlib)."""
    return Path(os.path.normpath(Path(value).absolute()))


def _check_owner(opened: os.stat_result, label: str) -> None:
    """No POSIX: o arquivo é do usuário atual e ninguém mais escreve nele."""
    if not stat.S_ISREG(opened.st_mode):
        raise UsageError(f"O {label} não é um arquivo comum.")
    if os.name == "nt":
        return
    if opened.st_uid != os.getuid():
        raise UsageError(f"O {label} tem outro dono; só vale um arquivo do seu usuário.")
    if opened.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise UsageError(f"O {label} é gravável por outros usuários; rode `chmod go-w` nele.")


def _read_guarded(path: Path, label: str, *, missing_ok: bool = False) -> bytes | None:
    """Bytes de um arquivo de configuração sensível, com as recusas de segurança.

    Recusa link simbólico (lstat + `O_NOFOLLOW` onde existe), o que não for arquivo
    regular e, no POSIX, arquivo de outro dono ou gravável por grupo/outros.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise UsageError(f"{label} não existe. Confira o caminho.") from None
    except OSError as exc:
        raise UsageError(f"Não consegui ler o {label}: {exc.strerror}.") from None
    if stat.S_ISLNK(info.st_mode):
        raise UsageError(f"O {label} é um link simbólico; use o arquivo de verdade no lugar do link.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise UsageError(f"Não consegui ler o {label}: {exc.strerror}.") from None
    with os.fdopen(fd, "rb") as stream:
        _check_owner(os.fstat(stream.fileno()), label)
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise UsageError(f"O {label} passa de {MAX_BYTES // 1024} KiB; um perfil é um arquivo pequeno.")
    return data


def _suggest(key: str, options: tuple[str, ...]) -> str:
    close = difflib.get_close_matches(key, options, n=1)
    return f" Você quis dizer `{close[0]}`?" if close else " Aceitos: " + ", ".join(options) + "."


def _dir_value(field: str, value: object, folder: Path) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise UsageError(f"{field} precisa ser um caminho (texto não vazio) no getbrolls.toml.")
    if value.startswith("~"):
        raise UsageError(f"{field} não expande `~`; use um caminho absoluto ou relativo ao getbrolls.toml.")
    return os.path.normpath(folder / value)


def _tool_value(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or "\0" in value or not Path(value).is_absolute():
        raise UsageError(f"tools.{name} precisa ser um caminho absoluto.")
    return value


def _id_list(field: str, value: object, pattern: re.Pattern[str], hint: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and pattern.fullmatch(item) for item in value):
        raise UsageError(hint)
    if len(set(value)) != len(value):
        raise UsageError(f"{field} tem ids repetidos no getbrolls.toml.")
    return tuple(value)


def _requires_value(value: object) -> str:
    from .sdk.manifest import ManifestError, satisfies  # tardio: o sdk carrega o contrato inteiro

    message = (
        f"requires inválido no getbrolls.toml: {value!r}; use cláusulas como "
        '">=2.6,<3" ou "==2.6.0" (`==2.6` só casa com 2.6.0).'
    )
    if not isinstance(value, str):
        raise UsageError(message)
    try:
        satisfies("0", value)
    except ManifestError:
        raise UsageError(message) from None
    return value


def _check_keys(data: dict, tools: object) -> None:
    for key in data:
        if key not in TOP_LEVEL:
            raise UsageError(f"getbrolls.toml: campo desconhecido `{key}`." + _suggest(key, TOP_LEVEL))
    if not isinstance(tools, dict):
        raise UsageError("tools no getbrolls.toml é uma tabela (`[tools]`).")
    for key in tools:
        if key not in TOOLS:
            raise UsageError(f"getbrolls.toml: ferramenta desconhecida `tools.{key}`." + _suggest(key, TOOLS))
    version = data.get("schema_version", SCHEMA_VERSION)
    if type(version) is not int or version != SCHEMA_VERSION:
        raise UsageError(f"schema_version do getbrolls.toml tem que ser {SCHEMA_VERSION}.")


def load(path: Path) -> Profile:
    """Lê e valida o getbrolls.toml; erro de conteúdo é `UsageError`, sem caminho absoluto.

    Pastas relativas resolvem contra a pasta do caminho localizado (não a do alvo de um
    link de diretório), com `normpath`, sem `expanduser` e sem `resolve`.
    """
    located = _absolute(path)
    raw = _read_guarded(located, PROFILE_NAME) or b""  # sem missing_ok, ausente já levantou
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise UsageError("O getbrolls.toml não está em UTF-8.") from None
    except tomllib.TOMLDecodeError as exc:
        raise UsageError(f"getbrolls.toml inválido: {exc}.") from None
    tools = data.get("tools", {})
    _check_keys(data, tools)
    folder = located.parent
    values: dict[str, str] = {}
    for field in DIR_FIELDS:
        if field in data:
            values[FIELDS[field]] = _dir_value(field, data[field], folder)
    for name in TOOLS:
        if name in tools:
            values[FIELDS[f"tools.{name}"]] = _tool_value(name, tools[name])
    plugins = None
    if "plugins" in data:
        from .sdk.contracts import NAME_RE  # tardio: o sdk carrega o contrato inteiro

        plugins = _id_list("plugins", data["plugins"], NAME_RE, "plugins é uma lista de ids (use [] para nenhum).")
        values["GB_PLUGINS"] = ",".join(plugins) or "off"
    marketplaces = None
    if "marketplaces" in data:
        hint = "marketplaces é uma lista de nomes de marketplace (use [] para nenhum)."
        marketplaces = _id_list("marketplaces", data["marketplaces"], MARKETPLACE_RE, hint)
    requires = _requires_value(data["requires"]) if "requires" in data else None
    return Profile(
        path=located.resolve(),
        sha256=hashlib.sha256(raw).hexdigest(),
        values=values,
        requires=requires,
        plugins=plugins,
        marketplaces=marketplaces,
    )


# --- descoberta ----------------------------------------------------------------------


def _is_candidate(path: Path) -> bool:
    """Arquivo comum ou link com o nome do perfil; pasta com esse nome não conta.

    O link entra para ser recusado por `load` com a mensagem certa, em vez de a busca
    seguir adiante e achar outro perfil em silêncio.
    """
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) or stat.S_ISLNK(mode)


def _explicit(value: str, source: str) -> Located:
    if value.strip().lower() == "off":
        return Located(None, source, True, ())
    path = _absolute(value)
    if not _is_candidate(path):
        if source == "flag":
            raise UsageError("--profile não existe. Confira o caminho.")
        raise UsageError("GB_PROFILE aponta para um arquivo que não existe. Confira o caminho ou remova a variável.")
    return Located(path, source, False, (path,))


def _start(folder: Path) -> Path:
    folder = _absolute(folder)
    for candidate in (folder, *folder.parents):
        if candidate.is_dir():
            return candidate.resolve()
    return folder


def locate(
    project: str | None,
    flag: str | None,
    *,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Located:
    """Flag, depois `GB_PROFILE`, depois subindo do projeto e, por fim, da pasta atual.

    `off` (na flag ou na variável) desliga o perfil. Caminho explícito que não existe é
    `UsageError`. A busca parte da pasta existente mais próxima do projeto, resolvida.
    """
    environ = os.environ if environ is None else environ
    if flag:
        return _explicit(flag, "flag")
    variable = environ.get("GB_PROFILE")
    if variable:
        return _explicit(variable, "GB_PROFILE")
    searched: list[Path] = []
    seen: set[Path] = set()
    starts = [(Path(project), "project")] if project else []
    starts.append((cwd if cwd is not None else Path.cwd(), "cwd"))
    for start, source in starts:
        folder = _start(start)
        for depth, current in enumerate((folder, *folder.parents)):
            if depth >= MAX_DEPTH or current in seen:
                break
            seen.add(current)
            candidate = current / PROFILE_NAME
            searched.append(candidate)
            if _is_candidate(candidate):
                return Located(candidate, source, False, tuple(searched))
    return Located(None, None, False, tuple(searched))


# --- confiança -----------------------------------------------------------------------


def _key(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _home_from(environ: Mapping[str, str]) -> Path:
    if environ is os.environ:
        return _paths.gb_home()
    return Path(environ.get("GB_HOME") or Path.home() / ".getbrolls")


def trust_home(environ: Mapping[str, str] | None = None) -> Path:
    """`GB_HOME` de antes do perfil: onde mora o `trusted-profiles.json`.

    Se o perfil ativo trocou o `GB_HOME`, vale o de antes dele — o `home` escolhido pelo
    próprio perfil nunca decide se ele é confiável.
    """
    environ = os.environ if environ is None else environ
    if _PRE_HOME and "GB_HOME" in _FROM_PROFILE and environ.get("GB_HOME") == _FROM_PROFILE["GB_HOME"]:
        return _PRE_HOME[0]
    return _home_from(environ)


def read_store(home: Path) -> dict:
    """`{"schema_version": 1, "profiles": {chave: {"sha256", "trusted_at"}}}`; ausente = vazio."""
    raw = _read_guarded(Path(home) / TRUST_FILE, TRUST_FILE, missing_ok=True)
    if raw is None:
        return {"schema_version": SCHEMA_VERSION, "profiles": {}}
    invalid = UsageError(f"{TRUST_FILE} está corrompido; apague o arquivo e confie de novo nos perfis.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise invalid from None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise invalid
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise invalid
    for entry in profiles.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise invalid
    return {"schema_version": SCHEMA_VERSION, "profiles": profiles}


def trust_state(
    profile: Profile,
    located: Located,
    home: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """`env`, `inside_home`, `trusted`, `changed` ou `untrusted` (ver o topo do módulo)."""
    environ = os.environ if environ is None else environ
    if located.source == "GB_PROFILE":
        expected = environ.get("GB_PROFILE_SHA256")
        return "changed" if expected and expected != profile.sha256 else "env"
    if _key(profile.path) == _key(Path(home) / PROFILE_NAME):
        return "inside_home"
    entry = read_store(home)["profiles"].get(_key(profile.path))
    if entry is None:
        return "untrusted"
    return "trusted" if entry["sha256"] == profile.sha256 else "changed"


def _target(path: Path | None, located: Located) -> Path:
    target = path if path is not None else located.path
    if target is None:
        raise UsageError("Nenhum getbrolls.toml encontrado; indique o arquivo com `--profile`.")
    return Path(target)


def _write_store(home: Path, data: dict) -> None:
    """Grava atômico (temporário + `os.replace`), 0600; as pastas criadas ficam 0700."""
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{TRUST_FILE}.", dir=home)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        Path(tmp).chmod(0o600)
        Path(tmp).replace(home / TRUST_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _update_store(home: Path, change) -> bool:
    """Lê, muda e grava o store sob a trava do SO (flock/msvcrt); devolve o que `change` diz.

    A trava é liberada pelo kernel se o processo morrer. Quem espera desiste depois de
    `LOCK_TIMEOUT_S`, com um erro que pede para repetir.
    """
    from . import runtime  # tardio: o runtime traz logs/http, que o perfil não precisa

    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = home / f"{TRUST_FILE}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                runtime._acquire_lock(lock)  # pylint: disable=protected-access
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise UsageError(f"Outro processo está gravando {TRUST_FILE}; tente de novo.") from None
                time.sleep(0.05)
        try:
            data = read_store(home)
            changed = change(data["profiles"])
            if changed:
                _write_store(home, data)
            return changed
        finally:
            runtime._release_lock(lock)  # pylint: disable=protected-access


def trust(path: Path | None, *, located: Located, yes: bool, expect: str | None) -> dict:
    """Confia no perfil em dois passos: prévia com o sha, depois `--yes --expect <sha>`."""
    loaded = load(_target(path, located))
    result = {
        "path": str(loaded.path),
        "sha256": loaded.sha256,
        "requires": loaded.requires,
        "would_set": dict(loaded.values),
    }
    if not yes:
        command = " ".join(
            (
                _paths.cli_prefix_text(),
                "profile",
                "trust",
                _paths.quote_arg(str(loaded.path)),
                "--yes",
                "--expect",
                loaded.sha256,
            )
        )
        return {**result, "trusted": False, "confirm": {"command": command}}
    if not expect:
        raise UsageError("`profile trust --yes` precisa de `--expect <sha256>` da prévia.")
    if expect != loaded.sha256:
        raise UsageError("O getbrolls.toml mudou desde a prévia; rode `profile trust` de novo e confira.")
    home = trust_home()
    entry = {"sha256": loaded.sha256, "trusted_at": datetime.now(UTC).isoformat()}

    def add(profiles: dict) -> bool:
        profiles[_key(loaded.path)] = entry
        return True

    _update_store(home, add)
    return {**result, "trusted": True}


def untrust(path: Path | None, *, located: Located) -> dict:
    """Tira o perfil do store; não precisa que o arquivo ainda seja válido."""
    target = _target(path, located)
    key = _key(target)
    removed = _update_store(trust_home(), lambda profiles: profiles.pop(key, None) is not None)
    return {"path": str(Path(target).resolve()), "trusted": False, "removed": removed}


# --- requires ------------------------------------------------------------------------


def requires_problem(profile: Profile, version: str = __version__) -> str | None:
    """Por que esta instalação não atende o `requires` do perfil, ou `None`.

    Usa a gramática de `requires_getbrolls` dos plugins; a versão instalada entra só com o
    prefixo numérico (`2.6.0rc1` conta como 2.6.0). Versão curta completa com zeros:
    `==2.6` só casa com 2.6.0.
    """
    if profile.requires is None:
        return None
    from .sdk.manifest import satisfies  # tardio: o sdk carrega o contrato inteiro

    if satisfies(_leading_version(version), profile.requires):
        return None
    return _REQUIRES.format(spec=profile.requires, version=version)


def check_requires(profile: Profile, version: str = __version__) -> None:
    """`PrerequisiteError` (exit 4) quando o `requires` do perfil não é atendido."""
    problem = requires_problem(profile, version)
    if problem:
        raise PrerequisiteError(problem)


# --- aplicação -----------------------------------------------------------------------


def _set(environ: MutableMapping[str, str], key: str, value: str, applied: dict[str, str]) -> None:
    if environ.get(key) != value:
        environ[key] = value
    applied[key] = value
    _FROM_PROFILE[key] = value


def apply_home(profile: Profile, environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Só `GB_HOME`, e só quando o ambiente não tem um (vazio conta como não definido).

    Guarda o `GB_HOME` de antes, que `trust_home` usa para achar o store.
    """
    environ = os.environ if environ is None else environ
    applied: dict[str, str] = {}
    value = profile.values.get("GB_HOME")
    if value is None or (environ.get("GB_HOME") and environ.get("GB_HOME") != _FROM_PROFILE.get("GB_HOME")):
        return applied
    if not _PRE_HOME:
        _PRE_HOME.append(_home_from(environ))
    if environ.get("GB_HOME") != value:
        _set(environ, "GB_HOME", value, applied)
    return applied


def _plugins_ceiling(profile: Profile, current: str | None) -> str | None:
    """Novo `GB_PLUGINS` com o teto do perfil, ou `None` se nada muda."""
    ceiling = profile.values["GB_PLUGINS"]
    if not current or not current.strip():
        return ceiling
    if current.strip().lower() == "off":
        return None
    allowed = set(profile.plugins or ())
    chosen = [part.strip() for part in current.split(",") if part.strip()]
    narrowed = ",".join(item for item in dict.fromkeys(chosen) if item in allowed) or "off"
    return None if narrowed == current else narrowed


def apply_rest(profile: Profile, environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Todo campo menos `home`, quando o ambiente não o define; `GB_PLUGINS` vira teto.

    Com `GB_PLUGINS` no ambiente, fica a interseção com a lista do perfil (na ordem do
    ambiente); interseção vazia vira `off`, e `off` continua `off`.
    """
    environ = os.environ if environ is None else environ
    applied: dict[str, str] = {}
    for key, value in profile.values.items():
        if key == "GB_HOME":
            continue
        if key == "GB_PLUGINS":
            narrowed = _plugins_ceiling(profile, environ.get(key))
            if narrowed is not None:
                _set(environ, key, narrowed, applied)
            continue
        current = environ.get(key)
        if not current:
            _set(environ, key, value, applied)
    return applied


def plugin_ceiling(profile: Profile | None) -> frozenset[str] | None:
    """Ids de plugin que o perfil permite; `None` quando ele não restringe plugins."""
    if profile is None or profile.plugins is None:
        return None
    return frozenset(profile.plugins)


def marketplace_ceiling(profile: Profile | None) -> frozenset[str] | None:
    """Marketplaces que o perfil permite; `None` quando ele não restringe marketplaces."""
    if profile is None or profile.marketplaces is None:
        return None
    return frozenset(profile.marketplaces)


def value_sources(active: Active | None, environ: Mapping[str, str] | None = None) -> dict[str, dict]:
    """De onde vem cada campo do perfil: `profile`, `env_file`, `env` ou `default`."""
    from . import config  # tardio: o config importa o runtime

    environ = os.environ if environ is None else environ
    applied = active.applied if active is not None else {}
    env_applied = config._ENV_APPLIED  # pylint: disable=protected-access
    sources: dict[str, dict] = {}
    for field, key in FIELDS.items():
        value = environ.get(key) or None
        if value is None:
            source = "default"
        elif applied.get(key) == value:
            source = "profile"
        elif env_applied.get(key) == value:
            source = "env_file"
        else:
            source = "env"
        sources[field] = {"env": key, "value": value, "source": source}
    return sources
