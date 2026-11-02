"""Hash da pasta de um plugin, o pin e o que o `enable`/`install` mostram dele.

O `folder_digest` amarra o conteúdo que roda ao conteúdo que a pessoa revisou: o bytecode e o
link simbólico (conteúdo fora do hash) tornam a pasta inválida, os tetos de tamanho param o hash antes
de ele custar memória, e o pin guarda o sha256, o mapa por arquivo e as raízes de `permissions.paths`
resolvidas. Nada aqui lê ou grava o `plugins.json` (isso é de `plugin_state`) nem carrega plugin.
`loader` reexporta os nomes públicos, então `loader.folder_digest` e companhia seguem valendo.
"""

import hashlib
import os
import re
from pathlib import Path
from typing import NamedTuple

from .. import _paths
from ..errors import UsageError
from .files import TOP_LEVEL_VCS, counted_files, resolved_roots
from .files import is_link as _is_link  # `export_folder`/`export_plan` leem `loader._is_link`
from .manifest import PERMISSION_KEYS

# Bytecode ao lado da fonte: o `import` de um módulo irmão lê um `.pyc` de
# `__pycache__` (um `.pyc` com hash não conferido nem olha a fonte), então o código
# que roda deixaria de ser o que a pessoa revisou. Pasta com bytecode, ou com
# link simbólico (conteúdo fora do hash), fica `invalid` e nunca carrega.
BYTECODE_DIRNAME = "__pycache__"
BYTECODE_SUFFIXES = (".pyc", ".pyo")
# Pastas de VCS. Só o `.git` DE TOPO (`files.TOP_LEVEL_VCS`) fica fora do hash.
# Qualquer pasta de VCS em outro lugar (`vendor/.hg`, `.svn` aninhado, `.git` dentro
# de subpasta) torna o plugin inválido (`nested_vcs`), e `.hg`/`.svn` de topo contam
# no hash como qualquer arquivo.
VCS_DIRNAMES = frozenset({".git", ".hg", ".svn"})


def nested_vcs(folder):
    """Caminho relativo (texto) da primeira pasta de VCS fora do `.git` de topo, ou `None`."""
    for current, dirs, _files in os.walk(folder):
        rel = Path(current).relative_to(folder)
        for name in sorted(dirs):
            if name.casefold() in VCS_DIRNAMES and (rel.parts or name != TOP_LEVEL_VCS):
                return (rel / name).as_posix()
        if not rel.parts and TOP_LEVEL_VCS in dirs:
            dirs.remove(TOP_LEVEL_VCS)
    return None


def is_bytecode_name(name, is_dir):
    """`__pycache__` (pasta) ou `.pyc`/`.pyo` (arquivo), sem diferenciar maiúsculas."""
    folded = name.casefold()
    return folded == BYTECODE_DIRNAME if is_dir else folded.endswith(BYTECODE_SUFFIXES)


# O bytecode que o instalador compila (`pip install`, `uv pip install --compile-bytecode`): um `__pycache__/`
# de topo só com `<nome>.<tag>[.opt-N].pyc` de um `<nome>.py` da própria pasta. Só ele é tolerado, e só nos
# plugins que vêm no pacote (os oficiais e os exemplos): fica fora do hash e nada o lê, porque `_import`
# compila a fonte e roda com `exec` (nunca `importlib`) e um plugin oficial não importa módulo irmão.
INSTALLER_PYC_RE = re.compile(r"(?P<stem>[A-Za-z0-9_]+)\.[a-z]+-?[0-9]+(?:\.opt-[12])?\.pyc")


def installer_bytecode(folder):
    """Caminhos relativos (POSIX) do `__pycache__` de topo que o instalador deixou: `frozenset()` sem
    ele; `None` quando ele não segue o padrão (link, subpasta, outro nome, `.pyc` sem a fonte) — aí
    `content_problem` o acusa como sempre."""
    cache = Path(folder) / BYTECODE_DIRNAME
    if not _paths.strict_lexists(cache):
        return frozenset()
    if _is_link(cache) or not _paths.strict_is_dir(cache):
        return None
    tolerated = {BYTECODE_DIRNAME}
    for item in sorted(cache.iterdir()):
        match = INSTALLER_PYC_RE.fullmatch(item.name)
        if (
            match is None
            or not _paths.strict_is_file(item, follow_symlinks=False)
            or not _paths.strict_is_file(Path(folder) / f"{match['stem']}.py", follow_symlinks=False)
        ):
            return None
        tolerated.add(f"{BYTECODE_DIRNAME}/{item.name}")
    return frozenset(tolerated)


def content_problem(folder, tolerated=frozenset()):
    """`("link" | "bytecode", caminho relativo)` do primeiro item que torna a pasta
    inválida, ou `None`. O `.git` de topo não é percorrido (é metadado de VCS, fora
    do hash); um link com esse nome ainda conta como link. `tolerated` (de
    `installer_bytecode`) são os caminhos de bytecode do instalador que não contam."""
    for current, dirs, files in os.walk(folder):
        here = Path(current)
        rel = here.relative_to(folder)
        for name in sorted([*dirs, *files]):
            path = here / name
            if _is_link(path):
                return "link", (rel / name).as_posix()
            if is_bytecode_name(name, name in dirs) and (rel / name).as_posix() not in tolerated:
                return "bytecode", (rel / name).as_posix()
        if not rel.parts and TOP_LEVEL_VCS in dirs:
            dirs.remove(TOP_LEVEL_VCS)
    return None


def content_reason(problem, fix):
    """Motivo legível de `content_problem`; `fix` diz o que fazer depois de limpar a pasta."""
    kind, rel = problem
    if kind == "link":
        return (
            f"O plugin tem link simbólico ({rel}); o conteúdo apontado fica fora do hash do pin. "
            f"Copie os arquivos reais para a pasta e {fix}."
        )
    return (
        f"O plugin tem bytecode Python ({rel}); o Python pode rodá-lo no lugar da fonte revisada. "
        f"Apague a pasta __pycache__ (e qualquer .pyc/.pyo solto) da pasta do plugin — isso basta — e {fix}."
    )


# Tetos do hash da pasta: o `folder_digest` roda a cada comando, para cada
# plugin habilitado. Um arquivo enorme largado na pasta (um plugin que faz cache ao
# lado do `__file__`) não pode custar a memória/tempo dele a cada comando: passou do
# teto, o plugin fica suspenso com o motivo — nunca um MemoryError derrubando a CLI.
# O `install` já recusa mais de 200 MB; o total aqui dá folga para o que cresce depois.
DIGEST_MAX_FILE_BYTES = 200 * 1024 * 1024
DIGEST_MAX_TOTAL_BYTES = 400 * 1024 * 1024
_DIGEST_CHUNK = 1024 * 1024


class DigestLimitError(ValueError):
    """A pasta do plugin passou de um teto do hash; a mensagem diz qual."""


def _size_label(limit):
    return f"{limit // (1024 * 1024)} MB" if limit >= 1024 * 1024 else f"{limit} bytes"


def _hash_file(path, budget, *into):
    """Soma `path` em cada hash de `into` (e devolve os bytes lidos), em pedaços de
    1 MB, sem passar de `DIGEST_MAX_FILE_BYTES` nem do `budget` que resta do total."""
    limit = DIGEST_MAX_FILE_BYTES
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_DIGEST_CHUNK):
            total += len(chunk)
            if total > limit:
                raise DigestLimitError(
                    f"O plugin tem um arquivo acima do teto de {_size_label(limit)} para conferir o conteúdo "
                    f"({path.name}); tire-o da pasta do plugin (use api.data_dir) e habilite de novo."
                )
            if total > budget:
                raise DigestLimitError(
                    f"A pasta do plugin passa do teto de {_size_label(DIGEST_MAX_TOTAL_BYTES)} para conferir o "
                    "conteúdo; tire os arquivos grandes dela (use api.data_dir) e habilite de novo."
                )
            for hasher in into:
                hasher.update(chunk)
    return total


# Formato do hash da pasta, gravado ao lado do sha256 em cada pin (`digest`). O 2 é o mapa por arquivo
# enquadrado (`_tree_digest`); um pin sem a chave é do formato antigo (`caminho\0conteúdo\0` por arquivo,
# sem tamanho, em que duas árvores diferentes davam o mesmo hash) e deixa o plugin `suspended`.
DIGEST_FORMAT = 2
DIGEST_FIELD = "digest"
_DIGEST_DOMAIN = b"getbrolls.plugin_folder/2\0"
_NAME_LENGTH_BYTES = 8


def _tree_digest(files):
    """sha256 do mapa `{caminho relativo POSIX: sha256 do arquivo}`, na ordem dada (a de `counted_files`).

    Uma etiqueta de domínio e, por arquivo, o tamanho do caminho em 8 bytes, o caminho em UTF-8 e os 32
    bytes do sha256 dele: cada campo tem fronteira fixa, então duas árvores diferentes nunca dão a mesma
    sequência."""
    whole = hashlib.sha256(_DIGEST_DOMAIN)
    for rel, sha in files.items():
        name = rel.encode()
        whole.update(len(name).to_bytes(_NAME_LENGTH_BYTES, "big"))
        whole.update(name)
        whole.update(bytes.fromhex(sha))
    return whole.hexdigest()


def _digests(folder, skip=frozenset()):
    """`(hash da pasta, {caminho: sha256})` numa leitura só: a única conta de `folder_digest`,
    `file_digests` e `pin_digests`, que assim nunca divergem."""
    files = {}
    budget = DIGEST_MAX_TOTAL_BYTES
    for rel, path in counted_files(folder):
        name = rel.as_posix()
        if name in skip:
            continue
        one = hashlib.sha256()
        budget -= _hash_file(path, budget, one)
        files[name] = one.hexdigest()
    return _tree_digest(files), files


def folder_digest(folder, skip=frozenset()):
    """Hash de todo arquivo da pasta, exceto lixo de SO (`files.JUNK_FILENAMES`), o
    `.git` de topo e os caminhos de `skip` — o resto conta sem exceção. Link simbólico e
    bytecode nem chegam aqui: tornam o plugin `invalid` antes (`content_problem`).

    É o sha256 do mapa por arquivo enquadrado (`_tree_digest`, formato `DIGEST_FORMAT`).
    Lê em pedaços, com teto por arquivo e total (`DigestLimitError`, um
    `ValueError`, quando passa): nunca carrega um arquivo inteiro na memória."""
    return _digests(folder, skip)[0]


# Plugins oficiais: no checkout em `plugins_official/<id>/`, no wheel em `getbrolls/_data/plugins_official/<id>/`
# (o mesmo caminho relativo à raiz de dados). A confiança é o `folder_digest` em `official_pins.PINS`.
OFFICIAL_DIR = "plugins_official"


def official_digest(folder):
    """O número que `official_pins.PINS` guarda: `folder_digest` sem o bytecode do instalador."""
    return folder_digest(folder, skip=installer_bytecode(folder) or frozenset())


def shipped_bytecode(folder):
    """O bytecode do instalador tolerado em `folder` quando ela é dado do próprio pacote (um exemplo em
    `examples/plugins/`); em qualquer outra pasta, nada é tolerado."""
    try:
        inside = Path(folder).resolve().is_relative_to(_paths.data_root().resolve())
    except (OSError, ValueError):
        return frozenset()
    return (installer_bytecode(folder) or frozenset()) if inside else frozenset()


def file_digests(folder):
    """sha256 por arquivo, com o mesmo recorte e os mesmos tetos de `folder_digest` — base do diff do `update`."""
    return _digests(folder)[1]


def pin_digests(folder):
    """`(folder_digest, file_digests)` numa leitura só — o que o pin grava.

    O mapa por arquivo (caminho relativo POSIX → sha256) fica ao lado do pin em
    plugins.json: é o que deixa o `enable` de um plugin suspenso mostrar o que
    mudou. Mesma conta (`_digests`) das duas funções acima."""
    return _digests(folder)


# Teto do mapa por arquivo do pin: o mesmo do `install` (`materialize.MAX_FILES`). Uma
# pasta copiada à mão não passa por esse teto; acima dele o pin guarda só o sha256
# total (`files_omitted`), senão o plugins.json — relido a cada comando — incharia.
PIN_MAP_MAX_FILES = 2000
# Quantos nomes de arquivo a prévia do `enable` lista (o total vem sempre), como no install.
PREVIEW_FILES_MAX = 50
MAP_OMITTED_NOTE = (
    "Plugin com muitos arquivos, diff omitido (mais de {limit}); confira a pasta do plugin antes de confirmar."
)


def permissions_diff(before, after):
    """Bloco `{from, to}` das permissões (todas as chaves de `PERMISSION_KEYS`) — o mesmo do diff do
    `update` e do `enable` de conteúdo mudado. `before` é `None` quando não se sabe."""
    return {"from": before, "to": after}


def permissions_added(before, after):
    """`{chave de permissions: itens de `after` que não estavam em `before`}`, para
    TODAS as chaves de `PERMISSION_KEYS`. Uma pasta nova em
    `paths` conta mesmo quando só alarga uma que já existia (`~/Midia` no lugar de
    `~/Midia/sfx`): o que o plugin passa a alcançar mudou. `before` `None`
    (desconhecido) conta tudo de `after` como novo."""
    before = before or {}
    return {key: [item for item in after.get(key, []) if item not in before.get(key, [])] for key in PERMISSION_KEYS}


ROOTS_FIELD = "roots_resolved"


class PinContent(NamedTuple):
    """O que o pin grava do conteúdo confirmado: sha256 da pasta, mapa por arquivo e
    as raízes de `permissions.paths` resolvidas (`pinned_roots`)."""

    sha: str
    files: dict
    roots: dict


def pinned_roots(manifest):
    """`{entrada de permissions.paths como escrita: caminho resolvido}` das raízes que valem agora.

    Vai para o pin (`roots_resolved`), fora do sha256 do conteúdo: na carga, uma raiz
    que passou a resolver para outro lugar fica ignorada. Uma entrada que não resolve,
    ou cujo caminho não cabe em UTF-8, fica de fora (e fica ignorada na carga). Quem
    instala ou atualiza calcula isto antes de mexer em qualquer pasta."""
    try:
        pairs, _ignored = resolved_roots(manifest["permissions"].get("paths", []))
    except (OSError, RuntimeError, ValueError):
        return {}
    return {raw: str(resolved) for raw, resolved in pairs if _utf8_text(raw) and _utf8_text(str(resolved))}


def _utf8_text(text):
    """`text` cabe em UTF-8? Um caminho com bytes fora de UTF-8 (Linux) vira `str` com
    substitutos que o `plugins.json` não grava: fica fora do pin (e ignorado na carga)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def pin_entry(manifest, sha, files, roots=None):
    """Entrada de `enabled`/`last_pins`: versão, sha256 (e o formato dele, `digest`), permissões aprovadas, as raízes
    resolvidas de `permissions.paths` (`roots`, ou calculadas agora) e o mapa por
    arquivo (se cabe no teto). As permissões guardadas deixam o `enable` de um conteúdo
    mudado mostrar o que elas eram antes."""
    entry = {
        "version": manifest["version"],
        "sha256": sha,
        DIGEST_FIELD: DIGEST_FORMAT,
        "permissions": manifest["permissions"],
        ROOTS_FIELD: pinned_roots(manifest) if roots is None else roots,
    }
    if len(files) > PIN_MAP_MAX_FILES:
        entry["files_omitted"] = True
    else:
        entry["files"] = files
    return entry


def current_format(entry):
    """O pin foi gravado com o hash de hoje (`digest` igual a `DIGEST_FORMAT`)? Sem a chave, é o formato antigo."""
    return entry.get(DIGEST_FIELD) == DIGEST_FORMAT


def _pinned_files(entry):
    """Mapa por arquivo guardado no pin, ou `None` num pin antigo (ou malformado)."""
    files = entry.get("files")
    if isinstance(files, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        return files
    return None


def files_diff(before, after):
    """`{added, removed, changed}` entre dois mapas caminho → sha256."""
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(name for name in set(before) & set(after) if before[name] != after[name]),
    }


def check_expect(expect, sha):
    """`--yes` sozinho não basta — o sha256 mostrado na prévia (do conteúdo
    já materializado, não de um manifesto solto) tem que ser reapresentado, ou
    a pessoa pode estar confirmando um conteúdo diferente do que viu."""
    if not expect:
        raise UsageError("--yes precisa de --expect SHA256; rode a prévia (sem --yes) de novo e confira o valor.")
    if expect != sha:
        raise ValueError(
            "O sha256 de --expect não bate com o conteúdo agora: o valor foi copiado errado, ou a origem "
            "mudou desde a prévia. Rode a prévia de novo (sem --yes) e confirme com o sha256 dela."
        )
