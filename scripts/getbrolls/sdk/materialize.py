"""Traz um plugin para uma pasta de staging: copia a pasta local ou materializa a árvore de um commit git.

Um repositório git nunca é `checkout`ado: cloná-lo popularia a árvore de trabalho passando por filtros de
conteúdo (`clean`/`smudge`, ex.: um `filter.lfs.smudge` ou um filtro arbitrário citado em `.gitattributes`) —
código de terceiro rodando antes de qualquer `--yes`. Em vez disso, clonamos com `--no-checkout` e
materializamos nós mesmos, um blob por vez, direto de `git ls-tree`/`git cat-file blob` (que nunca aplicam
filtro), recusando qualquer entrada que não seja arquivo regular (link simbólico, submódulo), nome fora de NFC,
ponteiro do Git LFS, bytecode, pasta de VCS aninhada, nome que colide ou que o Windows trocaria, e o que passa
dos tetos de arquivos e de bytes. Nenhum código do plugin roda aqui: só o manifesto é lido.
"""

import json
import shutil
import unicodedata
from pathlib import Path

from .. import effects, logs, runtime
from . import git_source, loader
from .files import JUNK_FILENAMES, LFS_POINTER_MAX_BYTES, RESERVED_STEMS, counted_files, is_lfs_pointer
from .manifest import MANIFEST_MAX_BYTES, MANIFEST_NAME, compatibility_problem, read_manifest
from .staging import _new_install_staging_name

_log = logs.get("sdk")


def _refused(reason, exc):
    """Registra a recusa do conteúdo (`plugin_content_refused`, com `reason` e o `error_code` do erro) e devolve `exc`.

    Só o motivo: o caminho, o nome do arquivo e o texto da mensagem nunca vão para o log.
    """
    return logs.refusal(_log, "plugin_content_refused", exc, reason=reason)


# Cópia de pasta local: sem metadado de VCS de topo (VCS aninhado é recusado antes,
# em `_refuse_nested_vcs`) e sem lixo de SO (`files.JUNK_FILENAMES`), que fica fora
# do hash. Bytecode e link simbólico não são ignorados: são recusados.
_COPY_IGNORED = (".git", ".hg", ".svn", *sorted(JUNK_FILENAMES))


def _copy_ignore(directory, names):
    """`ignore=` do `shutil.copytree`: os nomes de `_COPY_IGNORED` (VCS de topo e lixo de SO)."""
    return shutil.ignore_patterns(*_COPY_IGNORED)(directory, names)


# Tamanho materializado que aceitamos sem confirmação extra: além de link
# simbólico/submódulo recusado à parte, um plugin gigantesco (histórico git
# inflado, pasta local errada) fica caro demais para copiar/ler às cegas.
# Módulo-nível (não default de parâmetro) de propósito: o teste troca o valor
# via `patch.object(materialize, "MAX_FILES", N)` e a função tem que enxergar isso
# na hora, não um default já resolvido na definição.
MAX_FILES = 2000
MAX_BYTES = 200 * 1024 * 1024

# Entrada de `git ls-tree` que não é arquivo regular: 120000 é link simbólico
# (o alvo pode apontar pra fora da pasta do plugin), 160000 é gitlink/submódulo
# (aponta pra outro repositório, não é conteúdo que materializamos aqui).
_REFUSED_TREE_MODES = {"120000": "link simbólico", "160000": "submódulo (gitlink)"}
_ALLOWED_TREE_MODES = {"100644", "100755"}


def _run_git(args, cwd=None, ssh=False, binary=False):
    return git_source.run_git(args, cwd=cwd, ssh=ssh, binary=binary)


def _git(args, cwd=None, ssh=False):
    return git_source.git_text(args, cwd=cwd, ssh=ssh)


def _git_blob(dest, sha):
    """Conteúdo bruto do blob `sha`, direto do banco de objetos — `cat-file` nunca
    aplica filtro `clean`/`smudge` (ao contrário de um `checkout`)."""
    return _run_git(["cat-file", "blob", sha], cwd=dest, binary=True)


def _refuse_links_and_bytecode(folder, tolerated=frozenset()):
    """Link simbólico ou junction do NTFS (conteúdo fora do hash) e
    bytecode (roda no lugar da fonte revisada) nunca entram em `plugins/` — o loader
    marcaria a pasta `invalid`. `tolerated`: o bytecode do instalador num exemplo do pacote."""
    problem = loader.content_problem(folder, tolerated)
    if problem is not None:
        raise _refused(problem[0], ValueError(loader.content_reason(problem, "rode a prévia de novo")))


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
        raise _refused(
            "non_nfc",
            ValueError(
                f"Nome de arquivo fora da forma Unicode NFC (ou que não é UTF-8) {where}: {path!r}; renomeie o arquivo."
            ),
        )


def _refuse_lfs_pointer(path, content):
    if is_lfs_pointer(content, len(content)):
        raise _refused(
            "lfs_pointer",
            ValueError(
                f"O plugin tem um ponteiro do Git LFS em {path!r}, não o arquivo de verdade; o install não roda o "
                "Git LFS. Commite o arquivo sem LFS (ou tire-o do plugin)."
            ),
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
            raise _refused(
                "path_chars", ValueError(f"Caminho com ':' ou '\\' no histórico git não é aceito: {path!r}.")
            )
        folded = part.casefold()
        if folded.rstrip(". ") in loader.VCS_DIRNAMES or folded in _VCS_SHORT_ALIASES:
            raise _refused(
                "vcs_path",
                ValueError(
                    "Caminho não pode ter um componente de controle de versão (.git, .hg, .svn) no histórico git: "
                    f"{path!r}."
                ),
            )


def _refuse_bytecode_path(path):
    parts = path.split("/")
    if any(loader.is_bytecode_name(part, True) for part in parts[:-1]) or loader.is_bytecode_name(parts[-1], False):
        raise _refused(
            "bytecode",
            ValueError(
                f"O plugin tem bytecode Python ({path!r}) no histórico git; o Python pode rodá-lo no lugar da "
                "fonte revisada. Tire __pycache__/.pyc/.pyo do repositório."
            ),
        )


def _is_junk(path):
    return path.rsplit("/", 1)[-1] in JUNK_FILENAMES


def _refuse_oversized_blob(path, size):
    if size is not None and size > MAX_BYTES:
        raise _refused(
            "oversized_blob",
            ValueError(f"O plugin tem um arquivo de mais de {MAX_BYTES // (1024 * 1024)} MB ({path!r}); recusado."),
        )


def _casefold_path(path):
    """A chave de colisão de `path`: cada componente sem diferença de caixa e sem o ponto
    ou espaço do fim, que o Windows apaga ao gravar (`plugin.py.` vira `plugin.py`)."""
    return "/".join(part.casefold().rstrip(". ") for part in path.split("/"))


def _refuse_windows_name(path):
    """Componente que o Windows não grava com o mesmo nome é recusado, em arquivo ou pasta:
    nome reservado (`CON`, `aux.txt`, `Nul.json`, `CONIN$`, `COM¹`: o radical antes do
    primeiro ponto, sem diferença de caixa) não vira arquivo lá, e ponto ou espaço no fim
    (`LEIAME.md.`) some ao gravar. O plugin chegaria diferente do que a prévia mostrou, e o
    sha256 do conteúdo mudaria só no Windows."""
    for part in path.split("/"):
        if part.split(".", 1)[0].rstrip(" ").upper() in RESERVED_STEMS:
            raise _refused(
                "reserved_name",
                ValueError(
                    f"O plugin tem um nome reservado do Windows ({path!r}: CON, PRN, AUX, NUL, CONIN$, CONOUT$, "
                    "COM0-9 ou LPT0-9, também com ¹ ² ³, com ou sem extensão); renomeie o arquivo ou a pasta."
                ),
            )
        if part.endswith((".", " ")):
            raise _refused(
                "trailing_dot",
                ValueError(
                    f"O plugin tem um nome com ponto ou espaço no fim ({path!r}), que o Windows apaga ao gravar: "
                    "lá o plugin chegaria com outro nome; renomeie o arquivo ou a pasta."
                ),
            )


def _refuse_tree_collisions(paths):
    """Detecta, só a partir da lista de caminhos (do `git ls-tree` ou da pasta comum) — nunca do
    disco de destino —, duas entradas que colidiriam num sistema de arquivos
    que não distingue maiúsc./minúsc. ou que apaga ponto/espaço do fim do nome
    (o Windows): arquivo × arquivo (`plugin.py` e `Plugin.py`, `plugin.py` e
    `plugin.py.`) ou arquivo × pasta (`Config` e `config/extra.py`). Depois da colisão
    (que nomeia os dois caminhos), recusa o nome que o Windows não grava igual
    (`_refuse_windows_name`). Roda ANTES
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
            raise _refused(
                "path_collision",
                ValueError(
                    f"Caminhos do plugin colidem (maiúsc./minúsc. ou ponto/espaço no fim): "
                    f"{seen_files[folded]!r} e {path!r}."
                ),
            )
        if folded in seen_dir_prefixes:
            raise _refused(
                "path_collision",
                ValueError(f"Caminhos do plugin colidem (arquivo x pasta): {seen_dir_prefixes[folded]!r} e {path!r}."),
            )
        parts = folded.split("/")
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            if prefix in seen_files:
                raise _refused(
                    "path_collision",
                    ValueError(f"Caminhos do plugin colidem (pasta x arquivo): {seen_files[prefix]!r} e {path!r}."),
                )
            seen_dir_prefixes.setdefault(prefix, path)
        seen_files[folded] = path
        _refuse_windows_name(path)


def _write_tree_entry(dest, root, path, mode, content):
    """Escreve `content` em `dest/path`. `_refuse_tree_collisions` já recusou
    qualquer colisão de maiúsc./minúsc. antes de chegar aqui; este
    `try/except` é só defesa em profundidade pra qualquer outro `OSError` de
    sistema de arquivos (disco cheio, permissão), convertido num `ValueError`
    com o caminho e o motivo em vez de deixar o traceback cru vazar."""
    target = (dest / path).resolve()
    if root not in target.parents:
        raise _refused("outside_folder", ValueError(f"Caminho fora da pasta do plugin no histórico git: {path!r}."))
    try:
        effects.mkdir(target.parent, parents=True, exist_ok=True)
        effects.write_bytes(target, content)
        if mode == "100755":
            target.chmod(target.stat().st_mode | 0o111)
    except OSError as exc:
        raise _refused(
            "write_failed", ValueError(f"Não consegui gravar {path!r} do histórico git ({type(exc).__name__}).")
        ) from exc


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
    effects.mkdir(dest, exist_ok=True)
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
            raise _refused(
                "submodule" if mode == "160000" else "link" if mode == "120000" else "tree_mode",
                ValueError(f"O plugin tem {reason} em {path!r} no histórico git; isso não é aceito."),
            )
        _refuse_git_path_component(path)
        _refuse_bytecode_path(path)
        _refuse_oversized_blob(path, size)
        if paths_only is None:
            total_files += 1
            if total_files > MAX_FILES:
                raise _refused("too_many_files", ValueError(f"O plugin tem mais de {MAX_FILES} arquivos; recusado."))
            total_bytes += size or 0
            if total_bytes > MAX_BYTES:
                raise _refused("too_large", ValueError(f"O plugin passa de {MAX_BYTES // (1024 * 1024)} MB; recusado."))
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
    # lenient: optional entry preview: the real manifest check right after raises
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
        runtime.force_rmtree(clone)
    _refuse_links_and_bytecode(dest)
    return commit


def _refuse_nested_vcs(folder):
    """Pasta de VCS (`.git`, `.hg`, `.svn`) fora do topo da pasta do plugin é
    recusada: o hash do pin só deixa de fora o `.git` de topo, e uma pasta
    aninhada dessas seria conteúdo que o plugin lê/executa sem ser metadado de nada."""
    nested = loader.nested_vcs(folder)
    if nested is not None:
        raise _refused(
            "nested_vcs",
            ValueError(f"O plugin tem uma pasta de controle de versão aninhada ({nested}); tire-a antes de instalar."),
        )


def _guard_folder_cap(folder):
    """Teto de árvore antes de copiar, no caminho de pasta comum: mesmo recorte de `folder_digest`
    (`files.counted_files`), então o que conta aqui é exatamente o que seria
    materializado por `shutil.copytree` com `_copy_ignore`. Recusa também, como no
    caminho git, nome fora de NFC e ponteiro do Git LFS."""
    total_bytes = 0
    for total_files, (rel, path) in enumerate(counted_files(folder), start=1):
        if total_files > MAX_FILES:
            raise _refused("too_many_files", ValueError(f"O plugin tem mais de {MAX_FILES} arquivos; recusado."))
        _refuse_non_nfc(rel.as_posix(), "na pasta")
        size = path.stat().st_size
        total_bytes += size
        if total_bytes > MAX_BYTES:
            raise _refused("too_large", ValueError(f"O plugin passa de {MAX_BYTES // (1024 * 1024)} MB; recusado."))
        if size <= LFS_POINTER_MAX_BYTES:
            _refuse_lfs_pointer(rel.as_posix(), path.read_bytes())


def _subdir_tree(clone, commit, subdir):
    """`<commit>:<subdir>` conferido como pasta (`tree`) no commit; sem `subdir`, o próprio commit."""
    if not subdir:
        return commit
    entry = git_source.tree_entry(clone, commit, subdir)
    if entry is None:
        raise _refused("subdir_missing", ValueError(f"--subdir {subdir!r} não existe no commit {commit[:12]}."))
    if entry[1] != "tree" or entry[0] != "040000":
        raise _refused(
            "subdir_not_folder",
            ValueError(f"--subdir {subdir!r} não é uma pasta no commit {commit[:12]}; aponte para a pasta do plugin."),
        )
    return f"{commit}:{subdir}"


def local_repository_warning(commit, ref):
    """Aviso da prévia quando a origem é uma pasta local que é repositório git."""
    return (
        f"A pasta é um repositório git: entra o commit {commit[:12]} ({ref or 'HEAD'}), não a árvore de "
        "trabalho; o que não foi commitado fica de fora."
    )


def _copy_folder(folder, dest, compat=True):
    """Copia a pasta comum `folder` para `dest`, com as mesmas recusas do caminho git. O bytecode que o
    instalador compilou num exemplo do pacote (`loader.shipped_bytecode`) não é copiado."""
    _checked_manifest(folder, compat)
    _refuse_nested_vcs(folder)
    shipped = loader.shipped_bytecode(folder)
    _refuse_links_and_bytecode(folder, shipped)
    _guard_folder_cap(folder)
    _refuse_tree_collisions(rel.as_posix() for rel, _path in counted_files(folder) if rel.as_posix() not in shipped)
    ignored = _copy_ignore if not shipped else shutil.ignore_patterns(*_COPY_IGNORED, loader.BYTECODE_DIRNAME)
    try:
        effects.copytree(folder, dest, symlinks=True, ignore=ignored)
    except OSError as exc:
        raise _refused(
            "copy_failed", ValueError(f"Não consegui copiar a pasta do plugin ({type(exc).__name__}).")
        ) from exc
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
    # Só para a pasta local sem --commit: quem fixou o commit (ou o índice) já sabe o que entra.
    plain_local = not git_source.is_remote(spec.repo) and spec.commit is None
    warnings = [local_repository_warning(commit, spec.ref)] if plain_local else []
    # Commit fixado E ref dada: o commit tem que ser a ponta dela, senão pode vir de um fork.
    tip = git_source.tip_warning(spec, commit) if spec.commit is not None else None
    if tip:
        warnings.append(tip)
    return {"source": spec.repo, "commit": commit, "ref": spec.ref, "subdir": spec.subdir}, warnings


def _checked_manifest(folder, compat=True):
    """Manifesto de `folder`; com `compat`, também compatível com esta instalação."""
    manifest = read_manifest(folder, require_folder_match=False)
    problem = compatibility_problem(manifest) if compat else None
    if problem:
        raise ValueError(f"Plugin {manifest['id']}: {problem}")
    return manifest


def fetch_file(source, path, max_bytes):
    """`(commit, bytes)` do arquivo `path` (relativo a `source.subdir`, quando há) no
    commit de `source`, sem checkout: recusa o que não é arquivo regular e o que
    passa de `max_bytes` antes de ler o conteúdo."""
    if not isinstance(source, git_source.GitSource):
        raise _refused("not_git", ValueError("fetch_file só lê de um repositório git."))
    git_source.validate_repo_path(path, "caminho")
    full = f"{source.subdir}/{path}" if source.subdir else path
    tmp = Path(effects.mkdtemp(prefix="gb-fetch-"))
    try:
        clone = tmp / "clone"
        commit = git_source.fetch(source, clone)
        entry = git_source.tree_entry(clone, commit, full)
        if entry is None or entry[0] not in _ALLOWED_TREE_MODES:
            raise _refused(
                "not_regular_file", ValueError(f"{full!r} não é um arquivo regular no commit {commit[:12]}.")
            )
        if entry[3] is None or entry[3] > max_bytes:
            raise _refused(
                "file_too_large",
                ValueError(f"{full!r} passa de {max_bytes} bytes no commit {commit[:12]}; recusado."),
            )
        return commit, _git_blob(clone, entry[2])
    finally:
        runtime.force_rmtree(tmp)
