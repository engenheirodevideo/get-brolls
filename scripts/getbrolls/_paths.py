"""Onde esta instalação mora: dados, CLI, runtime e `.env`.

Escopo: só instalação. O layout de projeto (0 = `brolls/clips`, 1 = `project.json`/
`analysis/`/`broll/`) mora em módulo próprio, nunca aqui.

A origem sai só de sentinelas: `_data/MANIFEST` ao lado do pacote (wheel) ou os
arquivos do repositório acima de `scripts/getbrolls` (checkout). Sem nenhuma das duas,
a origem é `unknown` e nada é adivinhado por `exists()` em pasta vizinha.
"""

import contextlib
import errno
import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import sys
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import json_read, versioning
from .errors import DataRootError, UsageError

# O que `stat` responde quando o caminho comprovadamente não está lá: não existe, um trecho
# não é pasta, descritor inválido ou laço de links. E, no Windows, unidade não pronta, nome
# inválido e nome que não resolve.
_ABSENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP})
# `ERROR_CANT_RESOLVE_FILENAME`: what Windows answers to a link loop (it has no `ELOOP`), also used by `cli`.
WINERROR_CANT_RESOLVE_FILENAME = 1921
_ABSENT_WINERRORS = frozenset({21, 123, WINERROR_CANT_RESOLVE_FILENAME})


def _stat_or_none(path, *, follow_symlinks=True):
    try:
        return Path(path).stat(follow_symlinks=follow_symlinks)
    except ValueError:
        return None
    except OSError as exc:
        if exc.errno in _ABSENT_ERRNOS or getattr(exc, "winerror", None) in _ABSENT_WINERRORS:
            return None
        raise


def strict_exists(path):
    """`path` existe? Uma pasta que não dá para ver (EACCES) levanta o `OSError`.

    Até o Python 3.13, `Path.exists()`/`is_dir()`/`is_file()` relançam o EACCES; a partir do
    3.14 eles respondem `False` a qualquer `OSError`. Onde uma pasta sem permissão tem de ser
    erro de E/S da pessoa (`IO_ERROR`), e não ausência, use as `strict_*`: elas respondem igual
    em todas as versões."""
    return _stat_or_none(path) is not None


def strict_is_dir(path):
    """`path` é uma pasta? Como `strict_exists`, levanta o EACCES em vez de dizer `False`."""
    info = _stat_or_none(path)
    return info is not None and stat.S_ISDIR(info.st_mode)


def strict_is_file(path, *, follow_symlinks=True):
    """`path` é um arquivo? Como `strict_exists`, levanta o EACCES em vez de dizer `False`.

    Com `follow_symlinks=False` olha o próprio item (`lstat`): um link, vivo ou quebrado, não é arquivo."""
    info = _stat_or_none(path, follow_symlinks=follow_symlinks)
    return info is not None and stat.S_ISREG(info.st_mode)


def strict_lexists(path):
    """Há algo em `path`, mesmo um link quebrado? É o `exists() or is_symlink()` (e o
    `os.path.lexists`, que diz `False` a qualquer erro em todo Python), mas o EACCES levanta."""
    return _stat_or_none(path, follow_symlinks=False) is not None


def _lexical(path) -> Path:
    """`path` absolute, with `.` and `..` collapsed by text (never by following links)."""
    return Path(os.path.normpath(Path(path).absolute()))


def _identity(path):
    """`(st_dev, st_ino)` of the folder `path` names (links followed), or `None` when it does not stat."""
    try:
        info = Path(path).stat()
    # lenient: a folder that does not stat is compared by its resolved text instead (`_real_text`)
    except (OSError, ValueError):
        return None
    return (info.st_dev, info.st_ino)


def _real_text(path) -> str:
    """Resolved spelling for a folder that does not exist (yet): what `identity` cannot compare."""
    try:
        text = str(Path(path).resolve())
    except (OSError, RuntimeError, ValueError):
        text = str(path)
    return os.path.normcase(unicodedata.normalize("NFC", text))  # NFD and NFC name the same folder


def _anchor_folder(chain, root):
    """The folder of `chain` (the path and its parents, as written) that IS `root`, or `None`.

    Same folder means same `st_dev`/`st_ino`, so a link spelling (macOS `/var` → `/private/var`)
    and a case-only difference on a case-insensitive disk both match, while a link from
    outside that points INTO `root` never does: it is not `root` itself.
    """
    wanted = _identity(root)
    real = None if wanted is not None else _real_text(_lexical(root))
    for folder, seen in chain:
        if wanted is not None and seen == wanted:
            return folder
        if wanted is None and _real_text(folder) == real:
            return folder
    return None


def label(path, anchors) -> str | None:
    """`path` for a message, as `<tag>/<relative>` under the deepest anchor that holds it.

    `anchors` is a sequence of `(root, tag)` — `(project, "<projeto>")`, `(client_root,
    "<cliente>")`… —; a `None` root is skipped. `.` and `..` are collapsed first, so a path
    that leaves every anchor gets no label. A folder of the path (as written) holds the path
    when it is the same folder as the root (`_anchor_folder`). With nested anchors (a project
    inside a client folder inside `$GB_HOME`), the deepest real folder wins, whatever the order
    and the spelling; a tie keeps the first. `None` outside every anchor: the caller decides
    the fallback. Never raises.
    """
    try:
        target = _lexical(path)
    except (TypeError, ValueError, OSError):
        return None  # not a path (`None`, an embedded NUL): nothing to label
    chain = [(folder, _identity(folder)) for folder in (target, *target.parents)]
    best_depth, best = -1, None
    for root, tag in anchors:
        if root is None:
            continue
        folder = _anchor_folder(chain, root)
        if folder is None:
            continue
        depth = len(Path(_real_text(folder)).parts)
        if depth > best_depth:
            rest = target.relative_to(folder).as_posix()
            best_depth, best = depth, tag if rest == "." else f"{tag}/{rest}"
    return best


# Ordenada; precisa ser igual ao manifesto (`packaging/data_manifest.txt`).
REQUIRED_DATA: tuple[str, ...] = (
    "assets/brand-logo.png",
    "assets/review.css",
    "assets/review.js",
    "assets/storyboard.css",
    "assets/storyboard.js",
    "docs/BRIEF.md",
    "docs/RULES.md",
    "examples/plans/reels.plan.json",
    "package-lock.json",
    "package.json",
    "plugins_official/hyperframes/README.md",
    "plugins_official/hyperframes/getbrolls-plugin.json",
    "plugins_official/hyperframes/plugin.py",
    "requirements-bootstrap.txt",
    "requirements.txt",
    "schemas/analysis_index.schema.json",
    "schemas/brief.schema.json",
    "schemas/candidate.schema.json",
    "schemas/capabilities.schema.json",
    "schemas/client.schema.json",
    "schemas/clients.schema.json",
    "schemas/components.schema.json",
    "schemas/draft.schema.json",
    "schemas/export_marker.schema.json",
    "schemas/export_plan.schema.json",
    "schemas/handoff.schema.json",
    "schemas/handoff_return.schema.json",
    "schemas/instagram_summary.schema.json",
    "schemas/library_index.schema.json",
    "schemas/manifest.schema.json",
    "schemas/markers.schema.json",
    "schemas/marketplace_index.schema.json",
    "schemas/marketplaces.schema.json",
    "schemas/media.schema.json",
    "schemas/pending_transaction.schema.json",
    "schemas/plugin_manifest.schema.json",
    "schemas/plugins_state.schema.json",
    "schemas/profile_toml.schema.json",
    "schemas/project.schema.json",
    "schemas/queue.schema.json",
    "schemas/recipe.schema.json",
    "schemas/recipe_lock.schema.json",
    "schemas/references.schema.json",
    "schemas/review_decisions.schema.json",
    "schemas/roteiro_state.schema.json",
    "schemas/rules_block.schema.json",
    "schemas/runtime_marker.schema.json",
    "schemas/scenes.schema.json",
    "schemas/silence.schema.json",
    "schemas/source_index.schema.json",
    "schemas/speakers.schema.json",
    "schemas/transcript.schema.json",
    "schemas/trusted_profiles.schema.json",
    "schemas/visual.schema.json",
)
WHEEL_MANIFEST = "_data/MANIFEST"  # relativo à pasta do pacote
CHECKOUT_MANIFEST = "packaging/data_manifest.txt"  # relativo à raiz do repositório
_REPO_SENTINELS = ("scripts/gb.py", "docs/RULES.md", "assets/brand-logo.png")

# Reinstalar é o único jeito de repor os arquivos de dados de um pacote instalado.
REINSTALL_COMMAND = "uv tool install --reinstall getbrolls"
DATA_REINSTALL = f"Reinstale (`{REINSTALL_COMMAND}`) ou rode pelo repositório (`python3 scripts/gb.py`)."
_DATA_ROOT_MISSING = f"Os arquivos de dados do getbrolls não estão junto do pacote. {DATA_REINSTALL}"
_ENV_FLAG_MISSING = "--env-file não existe. Confira o caminho."
_ENV_VAR_MISSING = "GB_ENV_FILE aponta para um arquivo que não existe. Confira o caminho ou remova a variável."
# Sem aspas no Windows só o que nenhum shell (Git Bash, PowerShell, cmd) reinterpreta.
_NT_SAFE = frozenset("_-.,:/=@+")


@dataclass(frozen=True)
class Install:
    """Origem da instalação e as pastas que ela determina."""

    origin: str  # "wheel", "checkout" ou "unknown"
    package_dir: Path
    data_root: Path | None
    checkout_root: Path | None


@dataclass(frozen=True)
class RuntimePart:
    """Pasta do runtime (`.venv` ou `.tools`) e de onde ela veio."""

    path: Path
    source: str  # "GB_RUNTIME_DIR", "profile", "gb_home" ou "checkout"


@dataclass(frozen=True)
class EnvChoice:
    """`.env` escolhido, os que ficaram de fora e tudo o que foi procurado."""

    path: Path | None
    source: str | None
    ignored: tuple[Path, ...]
    searched: tuple[Path, ...]


def detect(package_dir: Path | None = None) -> Install:
    """Classifica a instalação pelas sentinelas; nunca chuta."""
    pkg = package_dir if package_dir is not None else Path(__file__).resolve().parent
    # lenient: installation sniffing: an install it cannot read is reported as unknown or broken (exit 4)
    if (pkg / WHEEL_MANIFEST).is_file():
        return Install("wheel", pkg, pkg / "_data", None)
    root = pkg.parent.parent
    # lenient: installation sniffing: an install it cannot read is reported as unknown or broken (exit 4)
    if pkg.parent.name == "scripts" and all((root / sentinel).is_file() for sentinel in _REPO_SENTINELS):
        return Install("checkout", pkg, root, root)
    return Install("unknown", pkg, None, None)


@functools.cache
def install() -> Install:
    """Instalação deste processo, detectada uma vez."""
    return detect()


def origin() -> str:
    """`wheel`, `checkout` ou `unknown`; nunca levanta."""
    return install().origin


def data_root() -> Path:
    """Pasta dos dados do pacote; `DataRootError` quando ela não existe."""
    root = install().data_root
    if root is None:
        raise DataRootError(_DATA_ROOT_MISSING)
    return root


def data_path(*parts: str) -> Path:
    """Caminho de um arquivo de dados do pacote; `DataRootError` quando ele falta.

    Sem o arquivo, o erro nomeia só a entrada relativa (nunca o caminho desta máquina):
    é pré-requisito da instalação (exit 4), não `FileNotFoundError` de operação.
    """
    path = data_root().joinpath(*parts)
    # lenient: package data file: unreadable is a broken install, the hint is to reinstall (exit 4)
    if not path.is_file():
        raise DataRootError(f"Falta o arquivo de dados {'/'.join(parts)} do getbrolls. {DATA_REINSTALL}")
    return path


def manifest_entries(inst: Install | None = None) -> tuple[str, ...]:
    """Entradas do manifesto de dados (wheel ou checkout), sem comentários."""
    inst = inst if inst is not None else install()
    if inst.origin == "wheel":
        manifest = inst.package_dir / WHEEL_MANIFEST
    elif inst.checkout_root is not None:
        manifest = inst.checkout_root / CHECKOUT_MANIFEST
    else:
        raise DataRootError(_DATA_ROOT_MISSING)
    lines = (line.strip() for line in manifest.read_text(encoding="utf-8").splitlines())
    return tuple(line for line in lines if line and not line.startswith("#"))


def data_inventory() -> tuple[list[str], list[str]]:
    """(faltam, sem permissão de leitura) entre as entradas de `REQUIRED_DATA`, na ordem dela.

    Uma entrada que não dá para conferir (pasta sem permissão no caminho, ou outro erro de E/S)
    nunca conta como faltando: o `doctor` diz o motivo certo, e não "reinstale"."""
    root = data_root()
    missing, unreadable = [], []
    for entry in REQUIRED_DATA:
        try:
            if not strict_is_file(root / entry):
                missing.append(entry)
        except OSError:
            unreadable.append(entry)
    return missing, unreadable


def verify_data() -> list[str]:
    """Entradas de `REQUIRED_DATA` que não dá para usar (faltam ou não abrem); `[]` quando está tudo lá."""
    missing, unreadable = data_inventory()
    return [entry for entry in REQUIRED_DATA if entry in missing or entry in unreadable]


def gb_home() -> Path:
    """Pasta pessoal: `GB_HOME`, depois `~/.getbrolls` (sem resolve).

    Um `~` cru no começo (entre aspas num `.env`, ou vindo do PowerShell) é expandido: sem isso a
    pasta pessoal virava `./~/…`, relativa à pasta atual.
    """
    return Path(os.environ.get("GB_HOME") or Path.home() / ".getbrolls").expanduser()


# Partes do runtime e a pasta de cada uma dentro da raiz.
RUNTIME_PARTS: dict[str, str] = {"venv": ".venv", "tools": ".tools"}
# Arquivos que definem a versão de cada parte: o `npm ci` exige os dois do npm em acordo, e a venv
# é o pip de `requirements-bootstrap.txt` mais o conjunto de `requirements.txt`.
PART_FILES: dict[str, tuple[str, ...]] = {
    "venv": ("requirements-bootstrap.txt", "requirements.txt"),
    "tools": ("package.json", "package-lock.json"),
}
# Marcador de cada parte, na raiz dela (ao lado de `.venv`/`.tools`). Quem instala grava
# `building` antes e `ready` por último: quem lê só aceita uma parte compartilhada `ready`.
RUNTIME_MARKER = ".getbrolls-runtime-{part}.json"
# Versão do marcador: constante de inicialização (o runtime é resolvido antes de a pasta de dados existir),
# igual à de `schemas/runtime_marker.schema.json` (o teste do registro confere as duas).
MARKER_SCHEMA_VERSION = 1
MARKER_STATUSES = ("building", "upgrading", "ready")
_RUNTIME_CONFLICT = (
    "{source} aponta para uma pasta de runtime montada por outra versão do getbrolls ({part}). "
    "Pastas explícitas não são trocadas sozinhas: use uma pasta por versão ou apague essa à mão."
)
# A pasta compartilhada (`$GB_HOME/runtime/<sha>`) com o marcador de uma versão mais nova: nunca montada por cima.
_RUNTIME_NEWER = (
    "A pasta de runtime compartilhada ({part}) foi montada por uma versão mais nova do getbrolls; "
    "atualize o getbrolls ou aponte GB_RUNTIME_DIR para outra pasta."
)

# Chaves que o perfil aplicou ao ambiente, com o valor aplicado (ver `note_profile_env`).
_PROFILE_ENV: dict[str, str] = {}
# Por parte: (assinatura dos arquivos por mtime/tamanho, sha calculado).
_SHA_CACHE: dict[str, tuple[tuple, str]] = {}


# A folder spelling counts only as a whole path. Before it, no letter or digit: `/a/x/gb` is not
# `/x/gb` (nor `/private/var/…` the `/var/…` inside it), but `file:///x/gb`, `-/x/gb` and `~/x/gb` are.
# After it, the end, a separator or punctuation (`%` too: `%2F` is an encoded separator): `/x/gb2`
# and `/x/gb-copia` are other folders. A final `.` ends a sentence, not a name.
_BEFORE = r"(?<!\w)"
_AFTER = r"(?=$|[/\\\s\"'`),;:\]}%]|\.(?![\w\-]))"
# A relative spelling only starts a path: never after a name, a `/` or a `\`, and always before a separator.
_BEFORE_RELATIVE = r"(?<![\w.~\-/\\])"
_AFTER_RELATIVE = r"(?=[/\\])"
_PRIVATE = ("/private/var/", "/private/tmp/")
# Where the disk ignores case (macOS, Windows), so does the match: `/Users/Ana` is `/users/ana`.
_CASELESS = sys.platform in ("darwin", "win32")


def _encoded(spelling):
    """`spelling` in each form a text carries it (`_forms`), for each of its variants: NFC and NFD (a
    name typed on one system, read on another), and a Windows `\\` spelling also with `/` (`C:/Users/…`,
    the way `file:///C:/…` writes it)."""
    variants = {unicodedata.normalize("NFC", spelling), unicodedata.normalize("NFD", spelling)}
    variants |= {variant.replace("\\", "/") for variant in variants if "\\" in variant}
    return {form for variant in variants for form in _forms(variant)}


def _forms(spelling):
    """Raw; JSON (`\\/`, `\\\\/`, `\\u002f`, `\\u005c`, doubled `\\`, `\\uXXXX`); URL (`/` kept, `/` encoded, the
    hex in lowercase, encoded twice) and `file://` (`Path.as_uri`, drive letter kept)."""
    import urllib.parse  # local: only messages and log lines need it

    quoted = urllib.parse.quote(spelling, safe="")
    forms = {
        spelling,
        spelling.replace("/", "\\/"),
        spelling.replace("/", "\\\\/"),
        spelling.replace("/", "\\u002f"),
        spelling.replace("\\", "\\u005c"),
        json.dumps(spelling)[1:-1],
        json.dumps(spelling, ensure_ascii=False)[1:-1],
        urllib.parse.quote(spelling),
        urllib.parse.quote(spelling, safe=""),
        _lower_hex(quoted),
        urllib.parse.quote(quoted, safe=""),
    }
    # The `file://` URI as `Path.as_uri` writes it (`PurePath.as_uri` is deprecated from 3.14): the drive
    # letter and the separators kept, the rest percent-encoded.
    if _WINDOWS_DRIVE.match(spelling):
        forms.add("file:///" + urllib.parse.quote(spelling.replace("\\", "/"), safe="/:"))
    else:
        forms.add("file://" + urllib.parse.quote(spelling, safe="/"))
    return forms


_WINDOWS_DRIVE = re.compile(r"[A-Za-z]:[\\/]")


def _lower_hex(text):
    return re.sub(r"%[0-9A-F]{2}", lambda found: found.group(0).lower(), text)


def replace_spellings(text, spellings, tag, *, ignore_case=False, slashes=False) -> str:
    """`text` with each of `spellings`, in every form of `_encoded`, replaced by `tag`, only as a whole
    path (`_BEFORE`/`_AFTER`): `/Users/ana` never inside `/Users/anax`. Without case where the
    disk ignores it, or with `ignore_case` (a Windows folder seen from another system). With `slashes`, the
    names that follow a Windows folder (`\\brolls\\a`) are written with `/` like a label (`<projeto>/brolls/a`)."""
    value = str(text)
    flags = re.IGNORECASE if ignore_case or _CASELESS else 0
    probed: dict[str, bool] = {}  # the sibling checks of this call (`_ends_here`): each name asked once
    # No order to keep: one form never matches inside another (the boundaries refuse it).
    for spelling in spellings:
        for form in _encoded(spelling):
            raw = form == spelling
            pattern = _compiled(form, flags, slashes)
            value = pattern.sub(lambda found, raw=raw: _swapped(found, raw, probed, tag), value)
    return value


def _swapped(found, raw, probed, tag):
    """What replaces one match of `_compiled`: the tag, and the rest of a Windows path (`\\brolls\\a`) written
    with `/` as the label always does; the match itself when the folder goes on into a longer name."""
    if not _ends_here(found, raw, probed):
        return found.group(0)
    return tag + _BACKSLASHES.sub("/", found.group("tail") or "")


# What follows a Windows folder in a text: `\name` after `\name`, up to a blank, a `/`, a quote or any
# character a name cannot hold. A name with a blank ends the run (the rest of the text is left as it came).
# The separator depends on how the text writes a backslash. Plain text (`C:\\x\\a`, `\\\\host\\share`): any run
# of them, so a folder called `new` or `uni` is a folder. JSON text (`C:\\\\x\\\\a`, the backslash doubled): exactly
# two, because a single one there is an escape (`\n`, `\t`, `\"`, `\u00e9`) and ends the path.
_PLAIN_TAIL = r"(?P<tail>(?:\\+[^\s\\/'\"`<>|:]+)*)"
_JSON_TAIL = r"(?P<tail>(?:\\\\[^\s\\/'\"`<>|:]+)*)"
_NO_TAIL = "(?P<tail>)"
_BACKSLASHES = re.compile(r"\\+")
# Starts like a Windows path in any of its spellings: a drive (`C:\`, `C:/`, `C:\\`) or a UNC share.
_WINDOWS_START = re.compile(r"(?:[A-Za-z]:[\\/]|(?:\\\\){1,2}[^\\/])")


# A `\uXXXX` escape: `\u` and exactly four hex digits. A folder called `usuarios` is no escape.
_UNICODE_ESCAPE = re.compile(r"\\u[0-9A-Fa-f]{4}")


def _tail_of(form):
    """The pattern of the names that may follow `form` (see above); none unless `form` is a Windows
    spelling. The `\\u005c` form and the escaped forms of a POSIX folder (`\\u002f`, `\\/`) take none."""
    if _WINDOWS_START.match(form) is None or _UNICODE_ESCAPE.search(form):
        return _NO_TAIL
    return _JSON_TAIL if "\\\\" in form.lstrip("\\") else _PLAIN_TAIL


@functools.lru_cache(maxsize=4096)
def _compiled(form, flags, slashes=False):
    """The pattern of one form, compiled once per process: a literal between two fixed-width
    assertions, so a search is linear in the text (no nested quantifier, nothing to backtrack). With
    `slashes`, a Windows spelling also takes the names that follow it (`_tail_of`): they alternate with
    the `\\` they start with, so there is still nothing to backtrack."""
    return re.compile(_BEFORE + re.escape(form) + _AFTER + (_tail_of(form) if slashes else _NO_TAIL), flags)


# After a space, `,`, `:`, `;`, `)` or `'` a folder name can still go on (`Projeto 2/x`, `gb,v2/x`): it does
# when a separator comes before the next blank. Otherwise the text goes on, and the folder ends here.
# The look-ahead stops after one folder name (255 characters, the longest a disk allows): checking each
# match costs at most that, never the rest of a long line.
_CONTINUES = re.compile(r"[ ,:;)\]}'][^\s/\\\"`]{0,255}[/\\]")
_NAME_DELIMITERS = " ,:;)]}'"


# How many sibling names one text may ask the disk about (`_ends_here`): a slow disk (a network `$GB_HOME`)
# costs at most this per log line. Past it, the match is the folder (the label side).
_SIBLING_PROBES = 16


def _ends_here(found, raw, probed):
    """The match is the whole folder: the text does not go on into a longer name (`_CONTINUES`) and, for a
    raw spelling, no existing sibling starts with it (`Projeto 2` next to `Projeto`). `probed` holds the
    answers of this text: each candidate name is asked once, at most `_SIBLING_PROBES` in all."""
    string, end = found.string, found.start("tail")
    if end >= len(string) or string[end] not in _NAME_DELIMITERS:
        return True
    if _CONTINUES.match(string, end):
        return False
    if raw:
        cut = string[end : end + 200]
        for index in range(1, len(cut) + 1):
            if not (index == len(cut) or cut[index] in _NAME_DELIMITERS):
                continue
            name = cut[:index]
            if any(char in name for char in "/\\\n\r\t\0"):
                break  # past the end of a name: nothing longer can be a sibling either
            candidate = string[found.start() : end] + name
            if candidate not in probed:
                if len(probed) >= _SIBLING_PROBES:
                    break
                # lenient: a sibling it cannot see is not there for a message; the label is the safe side
                probed[candidate] = os.path.lexists(candidate)
            if probed[candidate]:
                return False
    return True


def with_linked(spellings) -> set[str]:
    """`spellings` and, for each one resolved under `/private/var` or `/private/tmp`, the same folder written
    without `/private` (macOS: `/var` and `/tmp` are links to `/private/…`, so one folder has two spellings)."""
    found = set(spellings)
    return found | {spelling[len("/private") :] for spelling in found if spelling.startswith(_PRIVATE)}


def replace_folder(text, folder, tag) -> str:
    """`text` with each spelling of `folder` (absolute as written, resolved, and without the macOS
    `/private`) replaced by `tag` (`replace_spellings`). A relative `folder` also matches its own
    spelling, but only as the start of a path (followed by a separator): `gbh/plugins.json`, never
    the word `gbh`."""
    spellings = {str(_lexical(folder))}
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        spellings.add(str(Path(folder).resolve()))
    value = replace_spellings(text, with_linked(spellings), tag, slashes=True)
    if not Path(folder).is_absolute() and str(folder) not in ("", "."):
        pattern = _BEFORE_RELATIVE + re.escape(os.path.normpath(folder)) + _AFTER_RELATIVE
        value = re.sub(pattern, lambda _found: tag, value)
    return value


def gb_home_label(path) -> str | None:
    """`path` como `$GB_HOME/…` quando mora no `$GB_HOME`; `None` fora dele (ou sem `$GB_HOME` legível)."""
    try:
        home = gb_home()
    except (OSError, RuntimeError, ValueError):
        return None
    return label(path, [(home, "$GB_HOME")])


def note_profile_env(applied: Mapping[str, str]) -> None:
    """Registra as chaves que o perfil pôs no ambiente, para citar `profile` como origem.

    Único registro do processo: `profile` grava aqui e lê daqui (`profile_env`).
    """
    _PROFILE_ENV.update(applied)


def profile_env() -> dict[str, str]:
    """Cópia do registro: chave `GB_*` → valor que o perfil pôs no ambiente."""
    return dict(_PROFILE_ENV)


def forget_profile_env() -> None:
    """Esvazia o registro (nova ativação do perfil, ou testes)."""
    _PROFILE_ENV.clear()


def from_profile(key: str) -> bool:
    """A chave veio do perfil e ninguém a trocou depois?"""
    return key in _PROFILE_ENV and os.environ.get(key) == _PROFILE_ENV[key]


def part_sha(part: str) -> str | None:
    """Versão de uma parte do runtime pelos arquivos que a definem; `None` sem eles.

    Guardada por mtime e tamanho dos arquivos: só relê quando um deles muda. O fim de
    linha não conta: o checkout do Windows (`text=auto`) traz esses arquivos com CRLF, e
    a mesma versão do runtime precisa da mesma chave em qualquer sistema.
    """
    try:
        files = [data_root() / name for name in PART_FILES[part]]
        signature = tuple((str(path), info.st_mtime_ns, info.st_size) for path, info in ((p, p.stat()) for p in files))
        cached = _SHA_CACHE.get(part)
        if cached is not None and cached[0] == signature:
            return cached[1]
        blob = b"\0".join(path.read_bytes().replace(b"\r\n", b"\n") for path in files)
    # lenient: runtime version probe: unreadable dependency files mean no sha, setup reports it
    except (DataRootError, OSError):
        return None
    sha = hashlib.sha256(blob).hexdigest()[:16]
    _SHA_CACHE[part] = (signature, sha)
    return sha


def _explicit_root() -> tuple[Path, str] | None:
    explicit = os.environ.get("GB_RUNTIME_DIR")
    if not explicit:
        return None
    return Path(explicit), "profile" if from_profile("GB_RUNTIME_DIR") else "GB_RUNTIME_DIR"


def runtime_root(part: str) -> tuple[Path, str]:
    """Raiz de uma parte e sua origem: a pasta explícita ou `$GB_HOME/runtime/<sha da parte>`.

    Numa pasta explícita (`GB_RUNTIME_DIR` ou o `runtime_dir` do perfil) as duas partes
    dividem a raiz e não há garantia de troca atômica: quem lê usa o que estiver lá.
    """
    explicit = _explicit_root()
    if explicit is not None:
        return explicit
    # Sem os dados não há versão da parte; a pasta compartilhada ganha um nome fixo.
    return gb_home() / "runtime" / (part_sha(part) or "unknown"), "gb_home"


def runtime_target(part: str) -> RuntimePart:
    """Onde a parte seria instalada; nunca a pasta do checkout."""
    root, source = runtime_root(part)
    return RuntimePart(root / RUNTIME_PARTS[part], source)


def marker_path(part: str, root: Path) -> Path:
    """Arquivo do marcador de uma parte, na raiz dela."""
    return root / RUNTIME_MARKER.format(part=part)


def read_marker(part: str, root: Path) -> dict | None:
    """Conteúdo do marcador, ou `None` (ausente, ilegível, não é objeto); nunca levanta."""
    try:
        # Um marcador tem poucos campos: 64 KiB já é folga; a recusa do `json_read` é um `ValueError`.
        # `write_marker` grava o `python` como escape ASCII, inclusive um surrogate de caminho não-UTF-8.
        marker = json_read.read(marker_path(part, root), max_bytes=64 * 1024, allow_lone_surrogates=True)
    except (OSError, ValueError):
        return None
    return marker if isinstance(marker, dict) else None


def marker_supported(marker: Mapping) -> bool:
    """O marcador é de uma versão que esta lê: `schema` ausente (vale 1) ou inteiro até `MARKER_SCHEMA_VERSION`.

    Um marcador mais novo (ou com `schema` ilegível) é de outra versão do getbrolls: a parte não está pronta
    e não é desta — quem instala recusa (`runtime_conflict`) em vez de montar por cima."""
    version = marker.get("schema", versioning.FIRST_VERSION)
    return type(version) is int and 1 <= version <= MARKER_SCHEMA_VERSION


def venv_python(folder: Path) -> Path:
    """O Python da própria `.venv` (`bin/python`, ou `Scripts/python.exe` no Windows)."""
    return folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_base_exists(venv: Path) -> bool:
    """O Python base da `.venv` (`home` do `pyvenv.cfg`) ainda existe?"""
    try:
        lines = (venv / "pyvenv.cfg").read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return False
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip().lower() == "home":
            # lenient: runtime readiness probe: not ready sends the user to setup, which raises on a locked folder
            return bool(value.strip()) and Path(value.strip()).is_dir()
    return False


def _venv_intact(folder: Path, marker: Mapping) -> bool:
    """A `.venv` ainda tem Python: o base que a criou (marcador e `pyvenv.cfg`) e o dela.

    `Path.exists` segue links: `bin/python` → `python3.X` → um Python removido conta
    como ausente, mesmo com a pasta `home` do `pyvenv.cfg` ainda lá (o `/usr/bin` do Debian).
    """
    base = marker.get("python")
    # lenient: runtime readiness probe: not ready sends the user to setup, which raises on a locked folder
    if not isinstance(base, str) or not base or not Path(base).exists():
        return False
    # lenient: runtime readiness probe: not ready sends the user to setup, which raises on a locked folder
    return venv_python(folder).exists() and _venv_base_exists(folder)


def part_ready(part: str, root: Path) -> bool:
    """A parte em `root` está pronta: marcador `ready` da versão atual e pasta íntegra.

    Só olha arquivos (barato, chamado a cada resolução); a sonda que roda o Python da
    venv (`bootstrap.venv_health`) fica com `setup`, `setup --check` e `doctor`.
    """
    marker = read_marker(part, root)
    sha = part_sha(part)
    if marker is None or not marker_supported(marker) or sha is None:
        return False
    if marker.get("status") != "ready" or marker.get("sha") != sha:
        return False
    folder = root / RUNTIME_PARTS[part]
    if part == "venv":
        return _venv_intact(folder, marker)
    # lenient: runtime readiness probe: not ready sends the user to setup, which raises on a locked folder
    return folder.is_dir()


def runtime_conflict(part: str) -> str | None:
    """Mensagem quando a pasta da parte é de outra versão do getbrolls; `None` quando é desta.

    Duas versões dividindo uma pasta explícita se reinstalariam sem fim; quem instala
    recusa em vez de apagar. Um marcador que não chegou a `ready` é build interrompido.
    Um marcador de versão mais nova (`marker_supported` falso) é sempre de outra versão,
    pronto ou não, e também na pasta compartilhada: nunca "ausente, monte por cima".
    """
    explicit = _explicit_root()
    if explicit is None:
        root = runtime_root(part)[0]
        marker = read_marker(part, root)
        return _RUNTIME_NEWER.format(part=part) if marker is not None and not marker_supported(marker) else None
    root, source = explicit
    marker = read_marker(part, root)
    if marker is None:
        return None
    if marker_supported(marker) and (marker.get("status") != "ready" or marker.get("sha") == part_sha(part)):
        return None
    return _RUNTIME_CONFLICT.format(
        source=source if source == "GB_RUNTIME_DIR" else "O runtime_dir do perfil", part=part
    )


def newer_marker_message(part: str, root: Path) -> str:
    """A recusa para a parte em `root` com o marcador de uma versão mais nova: o conflito de runtime."""
    conflict = runtime_conflict(part) if runtime_root(part)[0] == root else None
    return conflict or _RUNTIME_NEWER.format(part=part)


def _runtime_part(part: str) -> RuntimePart:
    root, source = runtime_root(part)
    shared = RuntimePart(root / RUNTIME_PARTS[part], source)
    if source != "gb_home" or part_ready(part, root):
        return shared
    inst = install()
    local = inst.checkout_root / RUNTIME_PARTS[part] if inst.origin == "checkout" and inst.checkout_root else None
    # lenient: checkout runtime fallback: one it cannot see falls back to the shared runtime
    if local is not None and local.is_dir():
        return RuntimePart(local, "checkout")
    return shared


def venv_dir() -> RuntimePart:
    """`.venv` do runtime (yt-dlp)."""
    return _runtime_part("venv")


def tools_dir() -> RuntimePart:
    """`.tools` do runtime (Playwright)."""
    return _runtime_part("tools")


def _part_info(part: str, found: RuntimePart) -> dict:
    marker = read_marker(part, found.path.parent)
    return {
        "path": str(found.path),
        "source": found.source,
        "managed": found.source != "checkout" and part_ready(part, found.path.parent),
        "marker": marker.get("status") if marker is not None else None,
        "conflict": runtime_conflict(part),
    }


def runtime_info() -> dict:
    """Resumo do runtime para diagnóstico."""
    return {
        "shas": {part: part_sha(part) for part in RUNTIME_PARTS},
        "venv": _part_info("venv", venv_dir()),
        "tools": _part_info("tools", tools_dir()),
        "explicit": bool(os.environ.get("GB_RUNTIME_DIR")),
    }


def env_file(flag: str | None = None) -> EnvChoice:
    """`.env` que vale: flag, `GB_ENV_FILE`, o do checkout e, por fim, o de `gb_home()`."""
    if flag:
        path = Path(flag)
        if not strict_is_file(path):
            raise UsageError(_ENV_FLAG_MISSING, reason="env_missing")
        return EnvChoice(path, "flag", (), (path,))
    variable = os.environ.get("GB_ENV_FILE")
    if variable:
        path = Path(variable)
        if not strict_is_file(path):
            raise UsageError(_ENV_VAR_MISSING, reason="env_missing")
        return EnvChoice(path, "GB_ENV_FILE", (), (path,))
    home_env = gb_home() / ".env"
    inst = install()
    searched: list[Path] = []
    if inst.origin == "checkout" and inst.checkout_root is not None:
        checkout_env = inst.checkout_root / ".env"
        searched.append(checkout_env)
        if strict_is_file(checkout_env):
            ignored = (home_env,) if strict_is_file(home_env) else ()
            return EnvChoice(checkout_env, "checkout", ignored, (checkout_env, home_env))
    searched.append(home_env)
    if strict_is_file(home_env):
        return EnvChoice(home_env, "gb_home", (), tuple(searched))
    return EnvChoice(None, None, (), tuple(searched))


def _os_name(os_name: str | None = None) -> str:
    return os_name if os_name is not None else os.name


def _interpreter(os_name: str | None = None) -> str:
    """Nome do Python para exibir num comando do checkout."""
    return "python" if _os_name(os_name) == "nt" else "python3"


def _gb_script(inst: Install) -> Path | None:
    return inst.checkout_root / "scripts" / "gb.py" if inst.origin == "checkout" and inst.checkout_root else None


def cli_argv() -> list[str]:
    """argv novo para rodar a CLI como subprocesso."""
    script = _gb_script(install())
    if script is not None:
        return [sys.executable, str(script)]
    return [sys.executable, "-P", "-m", "getbrolls"]


def _started_from(found: Path) -> bool:
    """No Windows: o `getbrolls(.exe)` achado é o arquivo que iniciou este processo?"""
    if not sys.argv or not sys.argv[0]:
        return False
    started = Path(sys.argv[0]).resolve()
    names = {started, started.with_suffix("") if started.suffix.lower() == ".exe" else started.with_suffix(".exe")}
    return str(found).lower() in {str(name).lower() for name in names}


def _console_script_is_ours(os_name: str | None = None) -> bool:
    """`getbrolls` do PATH só vale se for o script deste mesmo interpretador.

    No Windows vale também o `getbrolls.exe` que iniciou este processo: o `uv tool` põe o
    executável numa pasta própria do PATH, longe do python do ambiente da ferramenta.
    """
    found = shutil.which("getbrolls")
    if not found:
        return False
    found_path = Path(found).resolve()
    if found_path.parent == Path(sys.executable).parent.resolve():
        return True
    return _os_name(os_name) == "nt" and _started_from(found_path)


def cli_command(os_name: str | None = None) -> list[str]:
    """argv para exibir à pessoa (o prefixo dos comandos sugeridos)."""
    script = _gb_script(install())
    if script is not None:
        return [_interpreter(os_name), str(script)]
    if _console_script_is_ours(os_name):
        return ["getbrolls"]
    return [sys.executable, "-P", "-m", "getbrolls"]


def module_invocation(os_name: str | None = None) -> list[str]:
    """`python -P -m getbrolls` sem caminho da máquina (o `-P` isola o pacote da pasta atual)."""
    return [_interpreter(os_name), "-P", "-m", "getbrolls"]


def portable_cli_argv(os_name: str | None = None) -> list[str]:
    """argv da CLI para um manifesto ou documento: roda, mas não cita caminho desta máquina.

    No checkout, relativo à raiz do repositório; no pacote, o `getbrolls` do PATH só quando
    ele é o deste interpretador, senão o módulo isolado.
    """
    if origin() == "checkout":
        return [_interpreter(os_name), "scripts/gb.py"]
    if _console_script_is_ours(os_name):
        return ["getbrolls"]
    return module_invocation(os_name)


def cli_prefix_text(os_name: str | None = None) -> str:
    """Prefixo dos comandos sugeridos; no checkout POSIX, idêntico ao da 2.5."""
    script = _gb_script(install())
    if script is not None:
        return f'{_interpreter(os_name)} "{script}"'
    return " ".join(quote_arg(arg, os_name) for arg in cli_command(os_name))


def _quote_nt(value: str) -> str:
    if value and all(char.isalnum() or char in _NT_SAFE for char in value):
        return value
    out = ['"']
    backslashes = 0
    for char in value:
        if char == "\\":
            backslashes += 1
            continue
        if char == '"':
            out.append("\\" * (2 * backslashes + 1) + '"')
        else:
            out.append("\\" * backslashes + char)
        backslashes = 0
    out.append("\\" * (2 * backslashes) + '"')
    return "".join(out)


def quote_arg(value: str, os_name: str | None = None) -> str:
    """Um argumento pronto para colar no terminal do sistema."""
    return _quote_nt(value) if _os_name(os_name) == "nt" else shlex.quote(value)


def cli_hint(*args: str) -> str:
    """Comando para citar em prosa, sem caminho da máquina."""
    base = "python3 scripts/gb.py" if origin() == "checkout" else "getbrolls"
    return " ".join((base, *args))


def installer_hint() -> str:
    """Como instalar as dependências do runtime nesta instalação."""
    inst = install()
    if inst.origin == "checkout" and inst.checkout_root is not None:
        scripts = inst.checkout_root / "scripts"
        return f'bash "{scripts / "install.sh"}" (ou "{scripts / "install.ps1"}" no Windows)'
    return "rode `getbrolls setup` (instala yt-dlp e Playwright em $GB_HOME/runtime)"
