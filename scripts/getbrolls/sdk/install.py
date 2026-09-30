"""`plugins --action install|update`: trazer um plugin de uma pasta ou de um repositório git.

Dois passos, como o `enable`: sem `--yes` só mostra o que chegaria (id, versão,
permissões, contribuições, origem, commit e o sha256 do conteúdo já materializado)
e nada fica em `plugins/`; com `--yes` **e** `--expect <sha256>` batendo com essa
prévia, move para `plugins/<id>`, habilita com pin de hash e grava origem/commit
em `plugins.json`. Nenhum código do plugin roda aqui — só o manifesto é lido.

Um repositório git nunca é `checkout`ado: cloná-lo popularia a árvore de trabalho
passando por filtros de conteúdo (`clean`/`smudge`, ex.: um `filter.lfs.smudge`
ou um filtro arbitrário citado em `.gitattributes`) — código de terceiro rodando
antes de qualquer `--yes`. Em vez disso, clonamos com `--no-checkout` e
materializamos nós mesmos, um blob por vez, direto de `git ls-tree`/`git
cat-file blob` (que nunca aplicam filtro), recusando qualquer entrada que não
seja arquivo regular (link simbólico, submódulo).
"""

import json
import logging
import os
import re
import shutil
import tempfile
import time
import unicodedata
import uuid
from pathlib import Path
from typing import NamedTuple

from .. import logs
from ..runtime import force_rmtree
from . import git_source, loader
from .contracts import NAME_RE
from .files import JUNK_FILENAMES, LFS_POINTER_MAX_BYTES, counted_files, is_lfs_pointer
from .manifest import MANIFEST_MAX_BYTES, MANIFEST_NAME, compatibility_problem, read_manifest

_log = logs.get("sdk")

# Cópia de pasta local: sem metadado de VCS de topo (VCS aninhado é recusado antes,
# em `_refuse_nested_vcs`) e sem lixo de SO (`files.JUNK_FILENAMES`), que fica fora
# do hash. Bytecode e link simbólico não são ignorados: são recusados.
_COPY_IGNORED = (".git", ".hg", ".svn", *sorted(JUNK_FILENAMES))


def _copy_ignore(directory, names):
    """`ignore=` do `shutil.copytree`: os nomes de `_COPY_IGNORED` (VCS de topo e lixo de SO)."""
    return shutil.ignore_patterns(*_COPY_IGNORED)(directory, names)


# Quantos nomes de arquivo a prévia do install/update lista (o total vem sempre).
PREVIEW_FILES_MAX = 50

# Tamanho materializado que aceitamos sem confirmação extra: além de link
# simbólico/submódulo recusado à parte, um plugin gigantesco (histórico git
# inflado, pasta local errada) fica caro demais para copiar/ler às cegas.
# Módulo-nível (não default de parâmetro) de propósito: o teste troca o valor
# via `patch.object(install, "MAX_FILES", N)` e a função tem que enxergar isso
# na hora, não um default já resolvido na definição.
MAX_FILES = 2000
MAX_BYTES = 200 * 1024 * 1024

# Entrada de `git ls-tree` que não é arquivo regular: 120000 é link simbólico
# (o alvo pode apontar pra fora da pasta do plugin), 160000 é gitlink/submódulo
# (aponta pra outro repositório, não é conteúdo que materializamos aqui).
_REFUSED_TREE_MODES = {"120000": "link simbólico", "160000": "submódulo (gitlink)"}
_ALLOWED_TREE_MODES = {"100644", "100755"}

# `.old-*`/`.install-*` mais nova que isso pode ser um `install`/`update` concorrente
# ainda em andamento (não terminou de rodar o próprio `finally`); só mexemos em
# quem já passou desse prazo.
STALE_STAGING_MAX_AGE_S = 3600


def _run_git(args, cwd=None, ssh=False, binary=False):
    return git_source.run_git(args, cwd=cwd, ssh=ssh, binary=binary)


def _git(args, cwd=None, ssh=False):
    return git_source.git_text(args, cwd=cwd, ssh=ssh)


def _git_blob(dest, sha):
    """Conteúdo bruto do blob `sha`, direto do banco de objetos — `cat-file` nunca
    aplica filtro `clean`/`smudge` (ao contrário de um `checkout`)."""
    return _run_git(["cat-file", "blob", sha], cwd=dest, binary=True)


def _refuse_links_and_bytecode(folder):
    """Link simbólico ou junction do NTFS (conteúdo fora do hash) e
    bytecode (roda no lugar da fonte revisada) nunca entram em `plugins/` — o loader
    marcaria a pasta `invalid`."""
    problem = loader.content_problem(folder)
    if problem is not None:
        raise ValueError(loader.content_reason(problem, "rode a prévia de novo"))


def _tree_entries(dest, treeish):
    """(modo, sha, caminho, tamanho) de cada entrada de `git ls-tree -r -l <treeish>` —
    inclui todo blob recursivamente e as entradas de submódulo (que `-r` não
    expande). `-l` traz o tamanho declarado do objeto (`-` para submódulo), o que
    deixa checar o tamanho ANTES de pedir o conteúdo (`cat-file blob`) — um blob
    gigante nunca chega a ser lido só para descobrirmos que ele estoura o teto."""
    raw = _git(["ls-tree", "-r", "-l", "-z", treeish], cwd=dest)
    for record in raw.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        mode, _kind, sha, size_text = meta.split()
        try:
            size = int(size_text)
        except ValueError:
            size = None
        yield mode, sha, path, size


# Nomes curtos 8.3 que o NTFS pode resolver como alias de uma pasta de VCS.
_VCS_SHORT_ALIASES = frozenset({"git~1", "hg~1", "svn~1"})


def _refuse_non_nfc(path, where):
    """Nome de arquivo fora da forma NFC (ex.: acento decomposto, comum em pasta
    criada no HFS+) ou que não era UTF-8 (vira U+FFFD na leitura) é recusado: o
    mesmo nome viraria bytes diferentes em outro sistema, e o sha256 do pin
    deixaria de ser o mesmo entre macOS, Linux e Windows."""
    if "\ufffd" in path or not unicodedata.is_normalized("NFC", path):
        raise ValueError(
            f"Nome de arquivo fora da forma Unicode NFC (ou que não é UTF-8) {where}: {path!r}; renomeie o arquivo."
        )


def _refuse_lfs_pointer(path, content):
    if is_lfs_pointer(content, len(content)):
        raise ValueError(
            f"O plugin tem um ponteiro do Git LFS em {path!r}, não o arquivo de verdade; o install não roda o "
            "Git LFS. Commite o arquivo sem LFS (ou tire-o do plugin)."
        )


def _refuse_git_path_component(path):
    """Recusa qualquer entrada cujo caminho tenha um componente de VCS — `.git`,
    `.hg` ou `.svn` — em qualquer maiúsc./minúsc.; com ponto(s)/espaço(s) sobrando
    à direita (`.git.`, `.hg `, `.SVN. `), porque o Windows apaga ponto e espaço à
    direita do nome ao gravar em disco, então essas variantes viram a pasta de
    verdade sem bater numa comparação exata; ou o nome curto 8.3 (`GIT~1`, `HG~1`,
    `SVN~1`), que o NTFS pode resolver como alias. Também recusa `:` e `\\` em
    qualquer componente: `:` abre fluxo alternativo NTFS (`.git::$INDEX_ALLOCATION`)
    e `\\` vira separador de pasta no Windows. `.hg`/`.svn` materializados de um
    histórico git ficariam fora do hash do pin; nenhum deles é conteúdo de
    plugin — na melhor das hipóteses é lixo, na pior é metadado plantado que outra
    ferramenta trataria como especial mais adiante."""
    _refuse_non_nfc(path, "no histórico git")
    for part in path.split("/"):
        if ":" in part or "\\" in part:
            raise ValueError(f"Caminho com ':' ou '\\' no histórico git não é aceito: {path!r}.")
        folded = part.casefold()
        if folded.rstrip(". ") in loader.VCS_DIRNAMES or folded in _VCS_SHORT_ALIASES:
            raise ValueError(
                "Caminho não pode ter um componente de controle de versão (.git, .hg, .svn) no histórico git: "
                f"{path!r}."
            )


def _refuse_bytecode_path(path):
    parts = path.split("/")
    if any(loader.is_bytecode_name(part, True) for part in parts[:-1]) or loader.is_bytecode_name(parts[-1], False):
        raise ValueError(
            f"O plugin tem bytecode Python ({path!r}) no histórico git; o Python pode rodá-lo no lugar da "
            "fonte revisada. Tire __pycache__/.pyc/.pyo do repositório."
        )


def _is_junk(path):
    return path.rsplit("/", 1)[-1] in JUNK_FILENAMES


def _refuse_oversized_blob(path, size):
    if size is not None and size > MAX_BYTES:
        raise ValueError(f"O plugin tem um arquivo de mais de {MAX_BYTES // (1024 * 1024)} MB ({path!r}); recusado.")


def _casefold_path(path):
    return "/".join(part.casefold() for part in path.split("/"))


def _refuse_tree_collisions(paths):
    """Detecta, só a partir da lista de caminhos do `git ls-tree` — nunca do
    disco de destino —, duas entradas que colidiriam num sistema de arquivos
    que não distingue maiúsc./minúsc.: arquivo × arquivo (`plugin.py` e
    `Plugin.py`) ou arquivo × pasta (`Config` e `config/extra.py`). Roda ANTES
    de qualquer escrita e não depende do disco real distinguir caixa ou não —
    um disco case-sensitive (comum em CI Linux) materializaria as duas
    entradas sem erro nenhum, escondendo o problema até outra ferramenta
    (`git checkout` de verdade, Windows, um zip/tar que alguém abra depois)
    tratar os dois caminhos como o mesmo."""
    seen_files = {}
    seen_dir_prefixes = {}
    for path in paths:
        folded = _casefold_path(path)
        if folded in seen_files:
            raise ValueError(f"Caminhos colidem no histórico git (maiúsc./minúsc.): {seen_files[folded]!r} e {path!r}.")
        if folded in seen_dir_prefixes:
            raise ValueError(
                f"Caminhos colidem no histórico git (arquivo x pasta): {seen_dir_prefixes[folded]!r} e {path!r}."
            )
        parts = folded.split("/")
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            if prefix in seen_files:
                raise ValueError(
                    f"Caminhos colidem no histórico git (pasta x arquivo): {seen_files[prefix]!r} e {path!r}."
                )
            seen_dir_prefixes.setdefault(prefix, path)
        seen_files[folded] = path


def _write_tree_entry(dest, root, path, mode, content):
    """Escreve `content` em `dest/path`. `_refuse_tree_collisions` já recusou
    qualquer colisão de maiúsc./minúsc. antes de chegar aqui; este
    `try/except` é só defesa em profundidade pra qualquer outro `OSError` de
    sistema de arquivos (disco cheio, permissão), convertido num `ValueError`
    com o caminho e o motivo em vez de deixar o traceback cru vazar."""
    target = (dest / path).resolve()
    if root not in target.parents:
        raise ValueError(f"Caminho fora da pasta do plugin no histórico git: {path!r}.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if mode == "100755":
            target.chmod(target.stat().st_mode | 0o111)
    except OSError as exc:
        raise ValueError(f"Não consegui gravar {path!r} do histórico git ({type(exc).__name__}).") from exc


def _materialize_tree(clone, dest, treeish, paths_only=None):
    """Escreve o conteúdo de `treeish` de `clone` (só o banco de objetos, sem checkout) em `dest`
    — uma pasta de staging SEPARADA do clone, então nenhuma entrada da árvore
    consegue alcançar o `.git` do clone —, um blob por vez, sem jamais
    passar por um `checkout` — por isso sem filtro/smudge/hook. Recusa qualquer
    modo que não seja arquivo regular, qualquer caminho com componente de VCS ou com
    `:`/`\\`, e qualquer blob (mesmo na passada cedo) maior que `MAX_BYTES` — tudo
    isso ANTES de pedir o conteúdo do blob. Na passada completa (`paths_only=None`)
    também limita o total de arquivos/bytes a `MAX_FILES`/`MAX_BYTES`.

    `paths_only`, quando dado, materializa só esses caminhos exatos — usado pela
    validação cedo do manifesto, que não passa pelo teto de total (são no
    máximo dois arquivos pequenos: o manifesto e o `entry`), mas passa pelo teto
    por-blob do jeito que qualquer outra entrada passa."""
    dest.mkdir(exist_ok=True)
    root = dest.resolve()
    # Lixo de SO (`.DS_Store`...) fica fora do hash e da lista da prévia: nunca é
    # gravado, senão o arquivo que roda poderia mudar sem mudar o sha256.
    entries = [
        entry
        for entry in _tree_entries(clone, treeish)
        if (paths_only is None or entry[2] in paths_only) and not _is_junk(entry[2])
    ]
    _refuse_tree_collisions(path for _mode, _sha, path, _size in entries)
    total_files = 0
    total_bytes = 0
    for mode, sha, path, size in entries:
        if mode not in _ALLOWED_TREE_MODES:
            reason = _REFUSED_TREE_MODES.get(mode, f"modo {mode}")
            raise ValueError(f"O plugin tem {reason} em {path!r} no histórico git; isso não é aceito.")
        _refuse_git_path_component(path)
        _refuse_bytecode_path(path)
        _refuse_oversized_blob(path, size)
        if paths_only is None:
            total_files += 1
            if total_files > MAX_FILES:
                raise ValueError(f"O plugin tem mais de {MAX_FILES} arquivos; recusado.")
            total_bytes += size or 0
            if total_bytes > MAX_BYTES:
                raise ValueError(f"O plugin passa de {MAX_BYTES // (1024 * 1024)} MB; recusado.")
        content = _git_blob(clone, sha)
        _refuse_lfs_pointer(path, content)
        _write_tree_entry(dest, root, path, mode, content)


def _peek_entry_name(dest):
    """Nome do arquivo `entry` do manifesto já materializado em `dest`, só pra
    saber qual outro arquivo trazer cedo junto — `read_manifest` exige o
    `entry` presente em disco, então validar o manifesto sozinho sempre falharia.
    Não é validação de verdade (isso é `_checked_manifest`/`read_manifest`
    logo a seguir): um manifesto ilegível aqui só significa que não dá pra
    adiantar o `entry`, e a validação de verdade falha do jeito certo depois."""
    try:
        path = dest / MANIFEST_NAME
        if path.stat().st_size > MANIFEST_MAX_BYTES:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        return None
    entry = raw.get("entry") if isinstance(raw, dict) else None
    return entry if isinstance(entry, str) else None


def _validate_manifest_early(clone, dest, treeish, compat=True):
    """Materializa e valida o manifesto (e o `entry` que ele declara) antes
    de trazer o resto — potencialmente grande — da árvore; falha cedo, sem gastar
    tempo/disco com um plugin incompatível ou inválido."""
    _materialize_tree(clone, dest, treeish, paths_only={MANIFEST_NAME})
    entry = _peek_entry_name(dest)
    if entry:
        _materialize_tree(clone, dest, treeish, paths_only={MANIFEST_NAME, entry})
    _checked_manifest(dest, compat)


def _from_git(spec, dest, compat=True):
    """Busca o commit de `spec` numa pasta de staging própria (sem checkout),
    materializa a árvore (de `spec.subdir`, quando há) em `dest` (outra pasta) e
    apaga o clone; devolve o commit."""
    clone = dest.with_name(_new_install_staging_name())
    try:
        commit = git_source.fetch(spec, clone)
        treeish = _subdir_tree(clone, commit, spec.subdir)
        _validate_manifest_early(clone, dest, treeish, compat)
        _materialize_tree(clone, dest, treeish)
    finally:
        force_rmtree(clone)
    _refuse_links_and_bytecode(dest)
    return commit


def _refuse_nested_vcs(folder):
    """Pasta de VCS (`.git`, `.hg`, `.svn`) fora do topo da pasta do plugin é
    recusada: o hash do pin só deixa de fora o `.git` de topo, e uma pasta
    aninhada dessas seria conteúdo que o plugin lê/executa sem ser metadado de nada."""
    nested = loader.nested_vcs(folder)
    if nested is not None:
        raise ValueError(f"O plugin tem uma pasta de controle de versão aninhada ({nested}); tire-a antes de instalar.")


def _file_list(folder):
    """Arquivos que o pin vai cobrir, para a prévia: total e nomes (no máximo `PREVIEW_FILES_MAX`)."""
    names = [rel.as_posix() for rel, _path in counted_files(folder)]
    return {"count": len(names), "names": names[:PREVIEW_FILES_MAX], "truncated": len(names) > PREVIEW_FILES_MAX}


def _guard_folder_cap(folder):
    """Teto de árvore antes de copiar, no caminho de pasta comum: mesmo recorte de `folder_digest`
    (`files.counted_files`), então o que conta aqui é exatamente o que seria
    materializado por `shutil.copytree` com `_copy_ignore`. Recusa também, como no
    caminho git, nome fora de NFC e ponteiro do Git LFS."""
    total_bytes = 0
    for total_files, (rel, path) in enumerate(counted_files(folder), start=1):
        if total_files > MAX_FILES:
            raise ValueError(f"O plugin tem mais de {MAX_FILES} arquivos; recusado.")
        _refuse_non_nfc(rel.as_posix(), "na pasta")
        size = path.stat().st_size
        total_bytes += size
        if total_bytes > MAX_BYTES:
            raise ValueError(f"O plugin passa de {MAX_BYTES // (1024 * 1024)} MB; recusado.")
        if size <= LFS_POINTER_MAX_BYTES:
            _refuse_lfs_pointer(rel.as_posix(), path.read_bytes())


def _subdir_tree(clone, commit, subdir):
    """`<commit>:<subdir>` conferido como pasta (`tree`) no commit; sem `subdir`, o próprio commit."""
    if not subdir:
        return commit
    entry = git_source.tree_entry(clone, commit, subdir)
    if entry is None:
        raise ValueError(f"--subdir {subdir!r} não existe no commit {commit[:12]}.")
    if entry[1] != "tree" or entry[0] != "040000":
        raise ValueError(f"--subdir {subdir!r} não é uma pasta no commit {commit[:12]}; aponte para a pasta do plugin.")
    return f"{commit}:{subdir}"


def local_repository_warning(commit, ref):
    """Aviso da prévia quando a origem é uma pasta local que é repositório git."""
    return (
        f"A pasta é um repositório git: entra o commit {commit[:12]} ({ref or 'HEAD'}), não a árvore de "
        "trabalho; o que não foi commitado fica de fora."
    )


def _copy_folder(folder, dest, compat=True):
    """Copia a pasta comum `folder` para `dest`, com as mesmas recusas do caminho git."""
    _checked_manifest(folder, compat)
    _refuse_nested_vcs(folder)
    _refuse_links_and_bytecode(folder)
    _guard_folder_cap(folder)
    _refuse_tree_collisions(rel.as_posix() for rel, _path in counted_files(folder))
    try:
        shutil.copytree(folder, dest, symlinks=True, ignore=_copy_ignore)
    except OSError as exc:
        raise ValueError(f"Não consegui copiar a pasta do plugin ({type(exc).__name__}).") from exc
    _refuse_links_and_bytecode(dest)


def _materialize(spec, dest, compat=True):
    """Traz `spec` (`git_source.Source`) para `dest` (que não existe) e devolve
    `(origem, avisos)`; a origem é o que o `plugins.json` grava em `sources`.

    Repositório (pasta com `.git` ou URL) nunca é `checkout`ado (ver o docstring do
    módulo); pasta comum com link ou bytecode é recusada, e a cópia (links
    copiados como links, `symlinks=True`, sem `.git` nem lixo de SO) é
    conferida de novo — checar o resultado materializado, não só a origem,
    fecha a corrida entre "olhar" e "copiar"."""
    if isinstance(spec, git_source.FolderSource):
        _copy_folder(Path(spec.path), dest, compat)
        return {"source": spec.path, "commit": None}, []
    commit = _from_git(spec, dest, compat)
    warnings = [] if git_source.is_remote(spec.repo) else [local_repository_warning(commit, spec.ref)]
    return {"source": spec.repo, "commit": commit, "ref": spec.ref, "subdir": spec.subdir}, warnings


# O epoch de criação vai no NOME da pasta de staging, não é lido do `st_mtime`
# dela: `os.replace`/`shutil.copytree` preservam timestamp do conteúdo de
# origem (um plugin instalado há muito tempo, movido ou copiado agora,
# continua com o `st_mtime` antigo do próprio conteúdo) — uma pasta de staging
# recém-criada podia "parecer" velha o bastante pra a varredura mexer nela no
# meio de um `install`/`update` concorrente, apagando o staging de outro
# processo ainda rodando (`FileNotFoundError` cru quando esse processo, mais
# tarde, tentasse a própria troca). O nome é a única fonte de verdade da idade.
_INSTALL_STAGING_RE = re.compile(r"^\.install-(\d+)-[0-9a-f]{32}$")
_OLD_STAGING_RE = re.compile(r"^\.old-(\d+)-(.+)-[0-9a-f]{32}$")


def _new_install_staging_name():
    return f".install-{int(time.time())}-{uuid.uuid4().hex}"


def _new_old_staging_name(plugin_id):
    return f".old-{int(time.time())}-{plugin_id}-{uuid.uuid4().hex}"


def _sweep_stale_install(entry, name, now):
    match = _INSTALL_STAGING_RE.match(name)
    if not match or now - int(match.group(1)) < STALE_STAGING_MAX_AGE_S:
        return  # nome não reconhecido (versão anterior/lixo) ou jovem demais: não mexe
    force_rmtree(entry)


def _sweep_stale_old(root, entry, name, now):
    match = _OLD_STAGING_RE.match(name)
    if not match:
        return  # nome não reconhecido: sem como saber a idade com confiança, não mexe
    epoch, plugin_id = int(match.group(1)), match.group(2)
    if now - epoch < STALE_STAGING_MAX_AGE_S:
        return
    if not NAME_RE.fullmatch(plugin_id):
        return  # nome corrompido/inesperado: nunca usa como caminho sem validar antes
    target = root / plugin_id
    try:
        if not target.exists() and _restorable(entry, plugin_id):
            entry.replace(target)
        else:
            force_rmtree(entry)
    except OSError:
        # Uma corrida com outro processo (a pasta já não existe mais, ou o
        # destino apareceu entre o `exists()` e o `replace()`) nunca pode
        # abortar quem chamou `install`/`update`: na pior hipótese essa pasta
        # de resto fica pra próxima varredura.
        pass


def _restorable(entry, plugin_id):
    """Um `.old-*` só volta ao lugar se for mesmo aquele plugin: manifesto legível
    e com o mesmo id do nome. Pasta vazia, parcial ou de outro plugin é
    resto — restaurá-la travaria um `install` futuro com um plugin quebrado."""
    try:
        return read_manifest(entry, require_folder_match=False)["id"] == plugin_id
    except (ValueError, OSError):
        return False


def _sweep_one_stale_entry(root, entry, now):
    if not entry.is_dir():
        return
    name = entry.name
    if name.startswith(".install-"):
        _sweep_stale_install(entry, name, now)
    elif name.startswith(".old-"):
        _sweep_stale_old(root, entry, name, now)


def _sweep_stale_staging():
    """Uma pasta `.install-*`/`.old-*` que sobrou de um processo anterior morto
    no meio (sem chance de rodar o próprio `finally`) não deve ficar acumulando
    disco nem confundir uma leitura futura de `plugins/`. Duas ressalvas, e uma
    regra de nunca-quebrar (ver `_sweep_one_stale_entry`):

    - Uma pasta mais nova que `STALE_STAGING_MAX_AGE_S` (pelo epoch no NOME,
      não pelo `st_mtime`) pode ser de um `install`/`update` concorrente ainda
      em andamento — mexer nela agora corromperia esse processo em vez de
      limpar um resto de verdade.
    - Se `plugins/<id>` já não existe e sobrou um `.old-<epoch>-<id>-<uuid>`
      antigo, é porque o processo morreu bem entre as duas trocas do `update`
      (depois de retirar a pasta original, antes de pôr a nova no lugar):
      esse `.old-*` é a ÚNICA cópia que resta do plugin, e apagá-lo destruiria
      o plugin inteiro. Devolvemos ele ao lugar em vez de apagar.

    Nenhum erro de sistema de arquivos numa entrada individual pode abortar
    quem chamou `install`/`update`: o pior caso aceitável é deixar aquela
    pasta de resto pra próxima varredura."""
    root = loader.plugins_root()
    if not root.is_dir():
        return
    now = time.time()
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            _sweep_one_stale_entry(root, entry, now)
        except OSError:
            continue


def _staging():
    root = loader.plugins_root()
    root.mkdir(parents=True, exist_ok=True)
    # Começa com ponto: `loader.entries()` ignora a pasta enquanto ela existe.
    return root / _new_install_staging_name()


def _checked_manifest(folder, compat=True):
    """Manifesto de `folder`; com `compat`, também compatível com esta instalação."""
    manifest = read_manifest(folder, require_folder_match=False)
    problem = compatibility_problem(manifest) if compat else None
    if problem:
        raise ValueError(f"Plugin {manifest['id']}: {problem}")
    return manifest


def _summary(manifest, origin, content, files, warnings=()):
    """Prévia do install/update: manifesto, origem (com commit e ref), sha256, arquivos
    e, quando o plugin declara `permissions.paths`, as raízes resolvidas que o pin vai
    gravar. `warnings` junta os avisos da materialização aos das permissões."""
    warnings = [*warnings, *loader.permission_warnings(manifest)]
    summary = {
        **loader.manifest_summary(manifest),
        "source": origin["source"],
        "commit": origin["commit"],
        "ref": origin.get("ref"),
        "subdir": origin.get("subdir"),
        "sha256": content.sha,
        "files": files,
    }
    if manifest["permissions"]["paths"]:
        summary[loader.ROOTS_FIELD] = content.roots
    if warnings:
        summary["warnings"] = warnings
    return summary


def _as_spec(source):
    if isinstance(source, git_source.GitSource | git_source.FolderSource):
        return source
    return git_source.parse_source(source)


def content_digest(source):
    """`(sha256 do conteúdo, manifesto)` de `source` (`GitSource`, `FolderSource` ou
    `--source`), sem instalar nada: o mesmo sha256 que a prévia do `install` mostraria.

    Materializa numa pasta temporária do sistema (nunca em `plugins/`), sempre
    apagada no fim, e só lê o manifesto — nenhum código do plugin roda. Não confere
    compatibilidade com esta instalação (`platforms`, `requires_getbrolls`): quem
    monta um índice pode estar em outro sistema."""
    spec = _as_spec(source)
    tmp = Path(tempfile.mkdtemp(prefix="gb-digest-"))
    try:
        dest = tmp / "plugin"
        _materialize(spec, dest, compat=False)
        return loader.folder_digest(dest), read_manifest(dest, require_folder_match=False)
    finally:
        force_rmtree(tmp)


def fetch_file(source, path, max_bytes):
    """`(commit, bytes)` do arquivo `path` (relativo a `source.subdir`, quando há) no
    commit de `source`, sem checkout: recusa o que não é arquivo regular e o que
    passa de `max_bytes` antes de ler o conteúdo."""
    if not isinstance(source, git_source.GitSource):
        raise ValueError("fetch_file só lê de um repositório git.")
    git_source.validate_repo_path(path, "caminho")
    full = f"{source.subdir}/{path}" if source.subdir else path
    tmp = Path(tempfile.mkdtemp(prefix="gb-fetch-"))
    try:
        clone = tmp / "clone"
        commit = git_source.fetch(source, clone)
        entry = git_source.tree_entry(clone, commit, full)
        if entry is None or entry[0] not in _ALLOWED_TREE_MODES:
            raise ValueError(f"{full!r} não é um arquivo regular no commit {commit[:12]}.")
        if entry[3] is None or entry[3] > max_bytes:
            raise ValueError(f"{full!r} passa de {max_bytes} bytes no commit {commit[:12]}; recusado.")
        return commit, _git_blob(clone, entry[2])
    finally:
        force_rmtree(tmp)


def _check_expect(expect, sha):
    """Ver `loader.check_expect` (compartilhado com o `enable` de plugin suspenso)."""
    loader.check_expect(expect, sha)


def install(  # noqa: PLR0913 - keyword-only pin options, one per CLI flag
    source, confirm, expect=None, *, commit=None, ref=None, subdir=None
):  # pylint: disable=too-many-arguments  # keyword-only pin options, one per CLI flag
    """Prévia (sem `confirm`) ou instalação de um plugin a partir de pasta local ou URL git.

    `commit` fixa o commit (senão, `ref` — ausente vale `HEAD` — é resolvida na
    origem); `subdir` é a pasta do plugin dentro do repositório. Confirmar exige o
    sha256 da prévia em `expect`; o plugin sai instalado e habilitado."""
    spec = git_source.parse_source(source, commit=commit, ref=ref, subdir=subdir)
    loader.read_state()  # plugins.json corrompido recusa antes de qualquer mutação.
    _sweep_stale_staging()
    staging = _staging()
    try:
        origin, warnings = _materialize(spec, staging)
        manifest = _checked_manifest(staging)
        target = loader.plugins_root() / manifest["id"]
        if target.exists():
            raise ValueError(
                f"Plugin {manifest['id']} já está instalado; use plugins --action update --id {manifest['id']}."
            )
        content = loader.PinContent(*loader.pin_digests(staging), loader.pinned_roots(manifest))
        preview = _summary(manifest, origin, content, _file_list(staging), warnings)
        if not confirm:
            return {"installed": False, "plugin": preview, "note": loader.EXPECT_NOTE}
        _check_expect(expect, content.sha)
        staging.replace(target)
    finally:
        force_rmtree(staging)
    pinned_sha = loader.pin(manifest, target, origin, content=content)
    logs.event(_log, logging.INFO, "plugin_installed", plugin=manifest["id"], version=manifest["version"])
    return {"installed": True, "plugin": {**preview, "sha256": pinned_sha}, "note": loader.DONE_NOTE}


def _diff(old_folder, old_manifest, new_folder, new_manifest):
    before = loader.file_digests(old_folder)
    after = loader.file_digests(new_folder)
    return {
        "version": {"from": old_manifest["version"] if old_manifest else None, "to": new_manifest["version"]},
        "permissions": loader.permissions_diff(
            old_manifest["permissions"] if old_manifest else None, new_manifest["permissions"]
        ),
        "files": loader.files_diff(before, after),
    }


class _Staged(NamedTuple):
    """O que o `update` materializou no staging e conferiu, antes de trocar a pasta."""

    origin: dict
    manifest: dict
    content: "loader.PinContent"
    preview: dict
    diff: dict


def _stage_update(plugin_id, spec, staging, folder, current):
    """Materializa `spec` em `staging`, confere o manifesto e monta a prévia com o diff."""
    origin, warnings = _materialize(spec, staging)
    manifest = _checked_manifest(staging)
    if manifest["id"] != plugin_id:
        raise ValueError(f"A origem agora traz o plugin {manifest['id']}, não {plugin_id}; nada foi trocado.")
    content = loader.PinContent(*loader.pin_digests(staging), loader.pinned_roots(manifest))
    preview = _summary(manifest, origin, content, _file_list(staging), warnings)
    diff = _diff(folder, current, staging, manifest)
    return _Staged(origin, manifest, content, preview, diff)


def _swap_in(plugin_id, folder, staging):
    """Troca a pasta instalada `folder` pelo conteúdo de `staging`."""
    retired = folder.with_name(_new_old_staging_name(plugin_id))
    os.replace(folder, retired)
    try:
        os.replace(staging, folder)
    except OSError as exc:
        # A troca de verdade falhou no meio — devolve o conteúdo antigo
        # ao lugar em vez de deixar o plugin sem pasta nenhuma.
        try:
            os.replace(retired, folder)
        except OSError as rollback_exc:
            raise ValueError(
                f"A troca de {plugin_id} falhou ({type(exc).__name__}) e desfazê-la também falhou "
                f"({type(rollback_exc).__name__}); o conteúdo anterior pode estar em {retired}, não em "
                f"{folder}. Confira as duas pastas manualmente antes de tentar de novo."
            ) from rollback_exc
        raise
    force_rmtree(retired)


def update(plugin_id, confirm, expect=None):
    """Prévia (com o diff) ou troca de um plugin instalado pela versão atual da origem.

    Confirmar exige o sha256 da prévia em `expect`; um plugin desabilitado continua desabilitado."""
    state = loader.read_state()  # plugins.json corrompido recusa antes de qualquer mutação.
    origin = (state.get("sources") or {}).get(plugin_id)
    if origin is None:
        raise ValueError(
            f"Plugin {plugin_id} não foi instalado por plugins --action install; atualize a pasta à mão e rode enable."
        )
    # A origem gravada passa pela mesma validação de um `--source` novo; sem commit
    # fixado, a ref gravada (ausente vale `HEAD`) é resolvida de novo na origem.
    spec = git_source.parse_source(origin["source"], ref=origin.get("ref"), subdir=origin.get("subdir"))
    was_enabled = plugin_id in state.get("enabled", {})
    _sweep_stale_staging()
    _, folder, current = loader.find(plugin_id)
    staging = _staging()
    try:
        staged = _stage_update(plugin_id, spec, staging, folder, current)
        if not confirm:
            return {"updated": False, "plugin": staged.preview, "diff": staged.diff, "note": loader.EXPECT_NOTE}
        _check_expect(expect, staged.content.sha)
        _swap_in(plugin_id, folder, staging)
    finally:
        force_rmtree(staging)
    manifest, commit = staged.manifest, staged.origin["commit"]
    # Atualizar o conteúdo não liga de volta um plugin que estava desabilitado —
    # só quem já estava habilitado sai daqui com pin novo (senão o pin some).
    sha_after = loader.pin(manifest, folder, staged.origin, enabled=was_enabled, content=staged.content)
    logs.event(
        _log,
        logging.INFO,
        "plugin_updated",
        plugin=plugin_id,
        version=manifest["version"],
        commit=(commit or "-")[:12],
    )
    result = {
        "updated": True,
        "plugin": {**staged.preview, "sha256": sha_after},
        "diff": staged.diff,
        "enabled": was_enabled,
        "note": loader.DONE_NOTE,
    }
    if not was_enabled:
        result["note"] = (
            "Plugin atualizado, mas continua desabilitado (já estava antes do update); "
            f"rode `plugins --action enable --id {plugin_id} --yes` para habilitar."
        )
    return result
