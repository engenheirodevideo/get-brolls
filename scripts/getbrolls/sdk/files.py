"""Checagens de arquivo e de caminho que várias partes do SDK dividem.

Módulo folha: só usa a biblioteca padrão. `loader`, `api`, `safe_copy`,
`resolvers`, `install` e `exporters` importam daqui sem criar ciclo entre eles.
Nada aqui é contrato público: `getbrolls.sdk` não exporta estes nomes.
"""

import os
import re
import stat
from pathlib import Path

# Arquivo de lixo de SO que aparece sozinho (Finder/Explorer abriram a pasta): contá-lo
# no hash suspenderia o plugin por um arquivo que ninguém escreveu de propósito. Fica
# fora do hash — e por isso o `install` nunca o materializa: código do plugin
# não pode ler nem executar esses nomes, nem o `.git` de topo (docs/SDK.md).
JUNK_FILENAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
# Só o `.git` DE TOPO (o de um `git pull`/clone da própria pasta do plugin) fica fora
# do hash: é metadado do controle de versão, não conteúdo que o loader executa.
TOP_LEVEL_VCS = ".git"


def counted_files(folder):
    """(caminho relativo, caminho) de cada arquivo que entra no hash, em ordem estável.

    Ordena por `rel.parts` (tupla de `str`, componente por componente), não pelo
    `Path` em si: no `WindowsPath` real, `Path.__lt__` compara sem diferenciar
    maiúsculas de minúsculas, o que mudaria a ordem (e portanto o hash) entre
    Windows e POSIX para o mesmo conteúdo. `.parts` é texto puro em
    qualquer SO, então a comparação de tupla já é por ponto de código — e dá a
    MESMA ordem que o `sorted(Path...)` antigo já dava no POSIX (`sub/x.py`
    antes de `sub.py`, porque a tupla compara `"sub"` com `"sub.py"` antes de
    olhar o resto do caminho): nenhum pin de plugin no POSIX muda com este
    fix. Ordenar pela string inteira (`rel.as_posix()`) foi tentado antes e
    descartado — inverte esse par (`.` fica antes de `/` na comparação de string
    inteira), o que mudaria pin no POSIX também, não só no Windows."""
    candidates = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(folder)
        if rel.name in JUNK_FILENAMES or rel.parts[0] == TOP_LEVEL_VCS:
            continue
        candidates.append((rel, path))
    candidates.sort(key=lambda item: item[0].parts)
    yield from candidates


# Reparse points que são "name surrogate" — apontam para outro caminho, como um
# link: a junction (MOUNT_POINT) e o link simbólico do NTFS. Os outros reparse
# points (arquivo sob demanda do OneDrive, deduplicação) guardam o próprio
# conteúdo e não tiram nada do hash; tratá-los como link deixaria `invalid` todo
# plugin numa pasta sincronizada. As constantes só existem no `stat` do Windows.
_NAME_SURROGATE_TAGS = frozenset(
    {
        getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
        getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
    }
)


def is_link(path, windows=None):
    """Link simbólico, ou junction do NTFS (que não é `is_symlink()`).

    No Windows, além de `is_symlink()` e de `Path.is_junction()` (3.12+), confere o
    `st_reparse_tag` do `lstat` — o que acha a junction também no 3.11. Só as tags
    de "name surrogate" contam; o bit `FILE_ATTRIBUTE_REPARSE_POINT` sozinho não.
    No POSIX, `is_symlink()` basta."""
    if path.is_symlink():
        return True
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return False
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None and is_junction():
        return True
    try:
        tag = getattr(os.lstat(path), "st_reparse_tag", 0)
    except OSError:
        return False
    return tag in _NAME_SURROGATE_TAGS


# Nome de arquivo que a rota pode pedir dentro da pasta de trabalho: sem barra,
# sem `..`, sem começar por ponto (nada de arquivo escondido nem caminho).
FILE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")

# Nomes reservados do Windows (case-insensitive): mesmo num projeto rodando em
# Linux/macOS, o arquivo pode acabar sincronizado ou aberto numa máquina Windows,
# onde "CON.mp4"/"con"/"LPT1.txt" não são arquivos normais. `stem` = parte antes
# do primeiro ponto.
RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{n}" for n in range(10)} | {f"LPT{n}" for n in range(10)}
)


def bad_file_name(name):
    """`True` quando `name` não serve como nome de arquivo dentro do workdir da rota."""
    if not isinstance(name, str) or not FILE_NAME_RE.fullmatch(name) or ".." in name or name.endswith("."):
        return True
    stem = name.split(".", 1)[0]
    return stem.upper() in RESERVED_STEMS


def _same(a, b):
    try:
        return Path(a).samefile(b)
    except OSError:
        return False


def _too_broad(root):
    """A raiz resolvida é a raiz de um disco (inclusive um ponto de montagem, como
    `/Volumes/Backup`), a pasta pessoal ou uma pasta que a contém? Comparação pelo
    arquivo de verdade (`samefile`), não pelo texto: vale para link, firmlink e disco
    que não diferencia maiúsculas."""
    if root == Path(root.anchor) or os.path.ismount(root):
        return True
    try:
        home = Path.home().resolve()
    except (RuntimeError, OSError):
        return False
    parts = home.parts
    return any(_same(root.joinpath(*parts[i:]), home) for i in range(1, len(parts) + 1))


def checked_roots(paths):
    """`(raízes resolvidas que valem, entradas ignoradas por serem amplas demais)`."""
    roots, ignored = [], []
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            continue
        resolved = path.resolve()
        if _too_broad(resolved):
            ignored.append(raw)
        else:
            roots.append(resolved)
    return roots, ignored
