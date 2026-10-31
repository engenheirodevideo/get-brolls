"""Perfil `getbrolls.toml`, a base: tipos, leitura com as recusas de segurança, `load`, descoberta e `requires`.

A confiança (`profile_trust`) e a aplicação (`profile_apply`) ficam acima; o módulo `profile` reúne os três e
tem a documentação do perfil.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__, _paths, logs
from .errors import DataRootError, PrerequisiteError, UsageError

if TYPE_CHECKING:
    from collections.abc import Mapping

_log = logs.get("profile")

PROFILE_NAME = "getbrolls.toml"
TRUST_FILE = "trusted-profiles.json"
MAX_BYTES = 64 * 1024
MAX_DEPTH = 64
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
# Comandos que relatam o problema do perfil em vez de falhar (o perfil confiável vale
# neles do mesmo jeito). `setup` só é tolerante quando só confere (`--check`) ou só
# responde onde fica o runtime (`--where`): instalar exige o perfil em ordem.
TOLERANT_COMMANDS = ("doctor", "profile", "capabilities")
TOLERANT_SETUP_FLAGS = ("check", "where")
# Confianças com que o perfil vale (é aplicado e passado aos filhos).
APPLIED_TRUST = ("env", "inside_home", "trusted")
# Mesmo formato dos nomes de marketplace do core (`sdk/marketplace`).
MARKETPLACE_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")

_UNTRUSTED = (
    "O getbrolls.toml deste workspace ainda não é confiável: ele pode fixar executáveis e pastas. "
    "Revise com `getbrolls profile --action show` e confie com `getbrolls profile --action trust` "
    "(ou ignore com `--profile off`)."
)
_CHANGED = "O getbrolls.toml mudou desde o `profile --action trust`; revise (`profile --action show`) e confie de novo."
_REQUIRES = (
    "Este workspace pede getbrolls {spec} (getbrolls.toml); esta instalação é {version}. "
    "Use uma instalação compatível ou ajuste `requires`."
)
MESSAGES = {"untrusted": _UNTRUSTED, "changed": _CHANGED}
# Arquivos do `GB_HOME` que, com o `home` do perfil, passam pelas recusas do próprio toml.
# `marketplaces/` (cache dos índices) passa pelas mesmas recusas: um link ali levaria a leitura e o `remove` para fora.
HOME_FILES = (".env", "plugins.json", "marketplaces")


class ProfileRefusedError(UsageError):
    """O perfil não vale para este comando; `reason` diz por quê (`untrusted`, `changed` ou `invalid`)."""

    def __init__(self, message: str, reason: str):
        super().__init__(message, reason=reason)


def _refused(action: str, reason: str, exc: Exception) -> Exception:
    """Registra a recusa (`profile_refused`: ação, motivo e `error_code`) e devolve `exc` para levantar.

    Nunca o caminho do perfil nem o texto dele: só o motivo e o código. O `UsageError` leva o
    motivo junto: recusado antes do log (arquivo do `GB_HOME` do perfil), ele é o `reason` do
    `command_refused`.
    """
    if isinstance(exc, UsageError) and exc.reason is None:
        exc.reason = reason
    return logs.refusal(_log, "profile_refused", exc, action=action, reason=reason)


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
    """Estado do perfil desta execução, para `doctor` e `profile --action show`."""

    located: Located
    profile: Profile | None
    trust: str  # "env" | "inside_home" | "trusted" | "untrusted" | "changed" | "disabled" | "none" | "invalid"
    error: str | None  # mensagem do perfil inválido
    requires_problem: str | None
    pre_home: Path  # GB_HOME de antes do perfil (onde mora o trusted-profiles.json)
    applied: dict[str, str]  # chave GB → valor que este perfil pôs no ambiente


# GB_HOME de antes de o perfil trocar o dele (o `profile_trust` acha o store por ele).
_PRE_HOME: list[Path] = []


# --- leitura -------------------------------------------------------------------------


def _absolute(value: str | Path) -> Path:
    """Caminho absoluto e normalizado, sem seguir links (o `abspath` do pathlib)."""
    return Path(os.path.normpath(Path(value).absolute()))


def _check_owner(opened: os.stat_result, label: str) -> None:
    """No POSIX: o arquivo é do usuário atual e ninguém mais escreve nele."""
    if not stat.S_ISREG(opened.st_mode):
        raise _refused("read", "not_regular", UsageError(f"O {label} não é um arquivo comum."))
    if os.name == "nt":
        return
    if opened.st_uid != os.getuid():
        raise _refused(
            "read", "other_owner", UsageError(f"O {label} tem outro dono; só vale um arquivo do seu usuário.")
        )
    if opened.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise _refused(
            "read", "group_writable", UsageError(f"O {label} é gravável por outros usuários; rode `chmod go-w` nele.")
        )


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
        raise _refused("read", "missing", UsageError(f"{label} não existe. Confira o caminho.")) from None
    except OSError as exc:
        raise _refused("read", "unreadable", UsageError(f"Não consegui ler o {label}: {exc.strerror}.")) from None
    if stat.S_ISLNK(info.st_mode):
        raise _refused(
            "read",
            "link",
            UsageError(f"O {label} é um link simbólico; use o arquivo de verdade no lugar do link."),
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise _refused("read", "unreadable", UsageError(f"Não consegui ler o {label}: {exc.strerror}.")) from None
    with os.fdopen(fd, "rb") as stream:
        _check_owner(os.fstat(stream.fileno()), label)
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise _refused(
            "read",
            "too_big",
            UsageError(f"O {label} passa de {MAX_BYTES // 1024} KiB; um perfil é um arquivo pequeno."),
        )
    return data


def _guard_file(path: Path, label: str) -> None:
    """As recusas de `_read_guarded` (link, não regular, dono, gravável) sem ler o arquivo."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _refused("read", "unreadable", UsageError(f"Não consegui ler o {label}: {exc.strerror}.")) from None
    if stat.S_ISLNK(info.st_mode):
        raise _refused(
            "read",
            "link",
            UsageError(f"O {label} é um link simbólico; use o arquivo de verdade no lugar do link."),
        )
    _check_owner(info, label)


def check_home_files(home: Path | None = None) -> None:
    """Com o `GB_HOME` vindo do perfil: `.env`, `plugins.json` e `marketplaces/` passam pelas recusas do toml.

    Sem o `home` do perfil não faz nada: o `GB_HOME` do ambiente ou o padrão é da pessoa.
    """
    if not _paths.from_profile("GB_HOME"):
        return
    home = _paths.gb_home() if home is None else home
    for name in HOME_FILES:
        _guard_file(home / name, f"{name} do GB_HOME do perfil")


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
    if "schema_version" not in data:
        return  # ausente vale 1: o getbrolls.toml de antes do campo
    supported = _toml_version()
    version = data["schema_version"]
    if type(version) is not int or version != supported:
        raise UsageError(f"schema_version do getbrolls.toml tem que ser {supported}.")


def _schema_version(name: str) -> int:
    from .sdk import schemas  # tardio: o sdk carrega o contrato inteiro

    return schemas.version(name)


def _toml_version() -> int:
    """A versão do getbrolls.toml, lida do schema; sem a pasta de dados, a conferência não dá para fazer.

    Um perfil que declara `schema_version` sem a pasta de dados vira perfil inválido (recusado no comando
    estrito, relatado no tolerante): o `doctor` ainda chega ao relatório e aponta a reinstalação.
    """
    try:
        return _schema_version("profile_toml")
    except DataRootError as exc:
        raise UsageError(f"Não deu para conferir o schema_version do getbrolls.toml: {exc}") from None


def _check_schema(data: dict) -> None:
    """Confere o getbrolls.toml contra o schema publicado, depois das conferências do código."""
    from .sdk import jsonschema, schemas  # tardio: o sdk carrega o contrato inteiro

    try:
        schema = schemas.load("profile_toml")
    except DataRootError:
        return  # sem a pasta de dados, as conferências do código acima são a autoridade
    problems = jsonschema.errors(data, schema)
    if problems:
        raise UsageError("getbrolls.toml fora do formato: " + "; ".join(problems[:3]) + ".")


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
    _check_schema(data)
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
    # lenient: every folder above a reachable start is searchable, so lstat cannot hit EACCES
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
        # lenient: search start: walks up to the nearest folder it can see
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
        for depth, here in enumerate((folder, *folder.parents)):
            if depth >= MAX_DEPTH or here in seen:
                break
            seen.add(here)
            candidate = here / PROFILE_NAME
            searched.append(candidate)
            if _is_candidate(candidate):
                return Located(candidate, source, False, tuple(searched))
    return Located(None, None, False, tuple(searched))


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
    from .sdk.requirements import leading_version

    if satisfies(leading_version(version.strip()), profile.requires):
        return None
    return _REQUIRES.format(spec=profile.requires, version=version)


def check_requires(profile: Profile, version: str = __version__) -> None:
    """`PrerequisiteError` (exit 4) quando o `requires` do perfil não é atendido."""
    problem = requires_problem(profile, version)
    if problem:
        raise PrerequisiteError(problem)
