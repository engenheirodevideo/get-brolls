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
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .. import logs
from ..runtime import stderr_tail
from . import loader
from .manifest import MANIFEST_NAME, compatibility_problem, read_manifest

_log = logs.get("sdk")

GIT_URL_RE = re.compile(r"(https://\S+|git@[A-Za-z0-9.-]+:\S+)")
GIT_TIMEOUT_S = 120
COPY_IGNORE = shutil.ignore_patterns(".git", ".hg", ".svn", "__pycache__", "*.pyc", ".DS_Store", "Thumbs.db")

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

# Variáveis que, vindas do ambiente de quem chama, redirecionariam o git para
# um `.git`/índice/objetos que não são os do clone que acabamos de criar
# (ex.: um `GIT_DIR` apontando pro repositório de outro projeto). Nenhuma tem
# uso legítimo aqui: cada `_git`/`_git_blob` já recebe `cwd` explícito.
_STRIP_GIT_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG_PARAMETERS",
)

# `core.hooksPath` para um diretório sem hooks desliga qualquer hook do clone;
# `protocol.ext.allow=never` recusa o transporte `ext::` (rodaria um comando
# arbitrário como "URL"); os três `filter.lfs.*` desarmam o smudge do Git LFS
# especificamente. Nenhum deles é o que realmente nos protege de um filtro
# malicioso arbitrário citado em `.gitattributes` — isso é o `--no-checkout` +
# materialização manual abaixo, que nunca aciona filtro nenhum; estes são
# defesa em profundidade.
_GIT_HARDENING = [
    "-c",
    "protocol.ext.allow=never",
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "filter.lfs.process=",
    "-c",
    "filter.lfs.smudge=",
    "-c",
    "filter.lfs.required=false",
]


def _git_env(ssh=False):
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_GIT_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    if ssh and "GIT_SSH_COMMAND" not in env:
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return env


def _run_git(args, cwd=None, ssh=False, binary=False):
    git = shutil.which("git")
    if not git:
        raise ValueError("git não encontrado no PATH; instale o git ou use --source com uma pasta local.")
    env = _git_env(ssh=ssh)
    text_kwargs = {} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}
    try:
        done = subprocess.run(
            [git, *_GIT_HARDENING, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
            **text_kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"git excedeu {GIT_TIMEOUT_S} s; confira a rede e a URL do repositório.") from exc
    if done.returncode != 0:
        stderr = done.stderr if binary is False else (done.stderr or b"").decode("utf-8", errors="replace")
        detail = stderr_tail(stderr)
        raise ValueError(f"git falhou (exit {done.returncode})" + (f": {detail}" if detail else "."))
    return done.stdout


def _git(args, cwd=None, ssh=False):
    return _run_git(args, cwd=cwd, ssh=ssh).strip()


def _git_blob(dest, sha):
    """Conteúdo bruto do blob `sha`, direto do banco de objetos — `cat-file` nunca
    aplica filtro `clean`/`smudge` (ao contrário de um `checkout`)."""
    return _run_git(["cat-file", "blob", sha], cwd=dest, binary=True)


def _refuse_links(folder):
    if any(path.is_symlink() for path in folder.rglob("*")):
        raise ValueError("O plugin tem link simbólico; copie os arquivos reais para a pasta antes de instalar.")


def _refuse_query_or_fragment(raw):
    # Verificação por substring, não `urlsplit`: cobre tanto `https://…` quanto a
    # sintaxe `git@host:caminho` (que não é uma URL de verdade e não tem "query"
    # nem "fragment" pro `urlsplit` reconhecer).
    if "?" in raw or "#" in raw:
        raise ValueError("URL git não pode ter query (?) nem fragmento (#); use uma URL sem esses caracteres.")


def _clone_no_checkout(source_uri, dest, ssh=False):
    """Clona sem popular a árvore de trabalho: sem `checkout`, nenhum filtro de
    conteúdo, hook ou smudge roda — `dest` fica só com `.git` até
    `_materialize_tree` escrever os blobs um a um."""
    _git(["clone", "--no-checkout", "--depth", "1", "--quiet", "--", source_uri, str(dest)], ssh=ssh)
    return _git(["rev-parse", "HEAD"], cwd=dest)


def _tree_entries(dest):
    """(modo, sha, caminho) de cada entrada de `git ls-tree -r HEAD` — inclui todo
    blob recursivamente e as entradas de submódulo (que `-r` não expande)."""
    raw = _git(["ls-tree", "-r", "-z", "HEAD"], cwd=dest)
    for record in raw.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        mode, _kind, sha = meta.split(" ")
        yield mode, sha, path


def _materialize_tree(dest, paths_only=None):
    """Escreve o conteúdo de HEAD em `dest` (que já é a pasta clonada com
    `--no-checkout`, ainda sem nenhum arquivo de trabalho), um blob por vez, sem
    jamais passar por um `checkout` — por isso sem filtro/smudge/hook. Recusa
    qualquer modo que não seja arquivo regular e, na passada completa
    (`paths_only=None`), limita o total de arquivos/bytes a `MAX_FILES`/`MAX_BYTES`.

    `paths_only`, quando dado, materializa só esses caminhos exatos — usado pela
    validação cedo do manifesto (I4), que não passa pelo teto (são no máximo dois
    arquivos pequenos: o manifesto e o `entry`)."""
    root = dest.resolve()
    total_files = 0
    total_bytes = 0
    for mode, sha, path in _tree_entries(dest):
        if paths_only is not None and path not in paths_only:
            continue
        if mode not in _ALLOWED_TREE_MODES:
            reason = _REFUSED_TREE_MODES.get(mode, f"modo {mode}")
            raise ValueError(f"O plugin tem {reason} em {path!r} no histórico git; isso não é aceito.")
        target = (dest / path).resolve()
        if root not in target.parents:
            raise ValueError(f"Caminho fora da pasta do plugin no histórico git: {path!r}.")
        content = _git_blob(dest, sha)
        if paths_only is None:
            total_files += 1
            if total_files > MAX_FILES:
                raise ValueError(f"O plugin tem mais de {MAX_FILES} arquivos; recusado.")
            total_bytes += len(content)
            if total_bytes > MAX_BYTES:
                raise ValueError(f"O plugin passa de {MAX_BYTES // (1024 * 1024)} MB; recusado.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if mode == "100755":
            target.chmod(target.stat().st_mode | 0o111)


def _peek_entry_name(dest):
    """Nome do arquivo `entry` do manifesto já materializado em `dest`, só pra
    saber qual outro arquivo trazer cedo junto (I4) — `read_manifest` exige o
    `entry` presente em disco, então validar o manifesto sozinho sempre falharia.
    Não é validação de verdade (isso é `_checked_manifest`/`read_manifest`
    logo a seguir): um manifesto ilegível aqui só significa que não dá pra
    adiantar o `entry`, e a validação de verdade falha do jeito certo depois."""
    try:
        raw = json.loads((dest / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    entry = raw.get("entry") if isinstance(raw, dict) else None
    return entry if isinstance(entry, str) else None


def _validate_manifest_early(dest):
    """I4: materializa e valida o manifesto (e o `entry` que ele declara) antes
    de trazer o resto — potencialmente grande — da árvore; falha cedo, sem gastar
    tempo/disco com um plugin incompatível ou inválido."""
    _materialize_tree(dest, paths_only={MANIFEST_NAME})
    entry = _peek_entry_name(dest)
    if entry:
        _materialize_tree(dest, paths_only={MANIFEST_NAME, entry})
    _checked_manifest(dest)


def _guard_folder_cap(folder):
    """I4 para o caminho de pasta comum: mesmo recorte de `folder_digest`
    (`loader._counted_files`), então o que conta aqui é exatamente o que seria
    materializado por `shutil.copytree` com `COPY_IGNORE`."""
    total_bytes = 0
    for total_files, (_rel, path) in enumerate(loader._counted_files(folder), start=1):
        if total_files > MAX_FILES:
            raise ValueError(f"O plugin tem mais de {MAX_FILES} arquivos; recusado.")
        total_bytes += path.stat().st_size
        if total_bytes > MAX_BYTES:
            raise ValueError(f"O plugin passa de {MAX_BYTES // (1024 * 1024)} MB; recusado.")


def _materialize(source, dest):
    """Traz `source` para `dest` (que não existe) e devolve `(origem, commit)`.

    Pasta com `.git` e URL git nunca são `checkout`adas (ver o docstring do
    módulo); pasta comum é copiada com os links copiados como links
    (`symlinks=True`), sem `.git`/`__pycache__`, e então recusada se sobrar
    algum link — checar o resultado materializado, não a origem, fecha a
    corrida entre "olhar" e "copiar" (I2)."""
    raw = str(source).strip()
    folder = Path(raw).expanduser()
    if raw and not raw.startswith("-") and folder.is_dir():
        folder = folder.resolve()
        if (folder / ".git").exists():
            commit = _clone_no_checkout(folder.as_uri(), dest)
            _validate_manifest_early(dest)
            _materialize_tree(dest)
            shutil.rmtree(dest / ".git", ignore_errors=True)
            _refuse_links(dest)
            return str(folder), commit
        _checked_manifest(folder)
        _guard_folder_cap(folder)
        shutil.copytree(folder, dest, symlinks=True, ignore=COPY_IGNORE)
        _refuse_links(dest)
        return str(folder), None
    if not GIT_URL_RE.fullmatch(raw):
        raise ValueError("--source tem que ser uma pasta local ou uma URL git (https://… ou git@host:caminho).")
    _refuse_query_or_fragment(raw)
    if raw.startswith("https://"):
        parts = urlsplit(raw)
        if parts.username or parts.password:
            raise ValueError("URL git com usuário/senha não é aceita; use uma URL sem credencial.")
    commit = _clone_no_checkout(raw, dest, ssh=raw.startswith("git@"))
    _validate_manifest_early(dest)
    _materialize_tree(dest)
    shutil.rmtree(dest / ".git", ignore_errors=True)
    _refuse_links(dest)
    return raw, commit


def _sweep_stale_staging():
    """M2: uma pasta `.install-*`/`.old-*` que sobrou de um processo anterior
    morto no meio (sem chance de rodar o `finally`) não deve ficar acumulando
    disco nem confundir uma leitura futura de `plugins/`."""
    root = loader.plugins_root()
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.is_dir() and entry.name.startswith((".install-", ".old-")):
            shutil.rmtree(entry, ignore_errors=True)


def _staging():
    root = loader.plugins_root()
    root.mkdir(parents=True, exist_ok=True)
    # Começa com ponto: `loader.entries()` ignora a pasta enquanto ela existe.
    return root / f".install-{uuid.uuid4().hex}"


def _checked_manifest(folder):
    manifest = read_manifest(folder, require_folder_match=False)
    problem = compatibility_problem(manifest)
    if problem:
        raise ValueError(f"Plugin {manifest['id']}: {problem}")
    return manifest


def _summary(manifest, origin, commit, sha256):
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "version": manifest["version"],
        "contributes": {k: v for k, v in manifest["contributes"].items() if v},
        "permissions": manifest["permissions"],
        "source": origin,
        "commit": commit,
        "sha256": sha256,
    }


def _check_expect(expect, sha):
    """I3: `--yes` sozinho não basta — o sha256 mostrado na prévia (do conteúdo
    já materializado, não de um manifesto solto) tem que ser reapresentado, ou
    a pessoa pode estar confirmando um `install`/`update` diferente do que viu."""
    if not expect:
        raise ValueError("--yes precisa de --expect <sha256>; rode a prévia (sem --yes) de novo e confira o valor.")
    if expect != sha:
        raise ValueError(
            "O sha256 não bate com o conteúdo agora (a origem mudou desde a prévia); "
            "rode a prévia de novo (sem --yes) e confirme com o --expect atualizado."
        )


def install(source, confirm, expect=None):
    loader.read_state()  # M1: plugins.json corrompido recusa antes de qualquer mutação.
    _sweep_stale_staging()
    staging = _staging()
    try:
        origin, commit = _materialize(source, staging)
        manifest = _checked_manifest(staging)
        target = loader.plugins_root() / manifest["id"]
        if target.exists():
            raise ValueError(
                f"Plugin {manifest['id']} já está instalado; use plugins --action update --id {manifest['id']}."
            )
        sha = loader.folder_digest(staging)
        preview = _summary(manifest, origin, commit, sha)
        if not confirm:
            return {"installed": False, "plugin": preview, "note": loader.SANDBOX_NOTE}
        _check_expect(expect, sha)
        staging.replace(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    pinned_sha = loader.pin(manifest, target, {"source": origin, "commit": commit})
    logs.event(_log, logging.INFO, "plugin_installed", plugin=manifest["id"], version=manifest["version"])
    return {"installed": True, "plugin": {**preview, "sha256": pinned_sha}, "note": loader.SANDBOX_NOTE}


def _diff(old_folder, old_manifest, new_folder, new_manifest):
    before = loader.file_digests(old_folder)
    after = loader.file_digests(new_folder)
    return {
        "version": {"from": old_manifest["version"] if old_manifest else None, "to": new_manifest["version"]},
        "permissions": {
            "from": old_manifest["permissions"] if old_manifest else None,
            "to": new_manifest["permissions"],
        },
        "files": {
            "added": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after)),
            "changed": sorted(name for name in set(before) & set(after) if before[name] != after[name]),
        },
    }


def update(plugin_id, confirm, expect=None):
    state = loader.read_state()  # M1: plugins.json corrompido recusa antes de qualquer mutação.
    origin = (state.get("sources") or {}).get(plugin_id)
    if origin is None:
        raise ValueError(
            f"Plugin {plugin_id} não foi instalado por plugins --action install; atualize a pasta à mão e rode enable."
        )
    was_enabled = plugin_id in state.get("enabled", {})
    _sweep_stale_staging()
    _, folder, current = loader.find(plugin_id)
    staging = _staging()
    try:
        source, commit = _materialize(origin["source"], staging)
        manifest = _checked_manifest(staging)
        if manifest["id"] != plugin_id:
            raise ValueError(f"A origem agora traz o plugin {manifest['id']}, não {plugin_id}; nada foi trocado.")
        sha = loader.folder_digest(staging)
        preview = _summary(manifest, source, commit, sha)
        diff = _diff(folder, current, staging, manifest)
        if not confirm:
            return {"updated": False, "plugin": preview, "diff": diff, "note": loader.SANDBOX_NOTE}
        _check_expect(expect, sha)
        retired = folder.with_name(f".old-{uuid.uuid4().hex}")
        os.replace(folder, retired)
        try:
            os.replace(staging, folder)
        except OSError:
            # M2: a troca de verdade falhou no meio — devolve o conteúdo antigo
            # ao lugar em vez de deixar o plugin sem pasta nenhuma.
            os.replace(retired, folder)
            raise
        shutil.rmtree(retired, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    # M3: atualizar o conteúdo não liga de volta um plugin que estava desabilitado —
    # só quem já estava habilitado sai daqui com pin novo (senão o pin some).
    sha_after = loader.pin(manifest, folder, {"source": source, "commit": commit}, enable=was_enabled)
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
        "plugin": {**preview, "sha256": sha_after},
        "diff": diff,
        "enabled": was_enabled,
        "note": loader.SANDBOX_NOTE,
    }
    if not was_enabled:
        result["note"] = (
            "Plugin atualizado, mas continua desabilitado (já estava antes do update); "
            f"rode `plugins --action enable --id {plugin_id} --yes` para habilitar."
        )
    return result
