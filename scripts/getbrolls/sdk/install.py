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
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .. import logs
from ..runtime import stderr_tail
from . import loader
from .contracts import NAME_RE
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

# `.old-*`/`.install-*` mais nova que isso pode ser um `install`/`update` concorrente
# ainda em andamento (não terminou de rodar o próprio `finally`); só mexemos em
# quem já passou desse prazo.
STALE_STAGING_MAX_AGE_S = 3600

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


def _has_core_ssh_command(env):
    """`git config --global --includes --get core.sshCommand` já configurado
    pela pessoa — nesse caso o próprio git já sabe como conectar (e não
    precisamos, nem devemos, empurrar um `GIT_SSH_COMMAND` nosso por cima).
    `--global` de propósito: ainda não existe repositório clonado (isto roda
    antes do `clone`), então sem `--global` a busca cairia no cwd deste
    processo, que pode por acaso estar dentro de outro repositório qualquer.
    `--includes` resolve `[include]`/`[includeIf]` do config global, senão um
    `core.sshCommand` guardado num arquivo incluído passaria despercebido."""
    git = shutil.which("git")
    if not git:
        return False
    done = subprocess.run(
        [git, "config", "--global", "--includes", "--get", "core.sshCommand"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    return bool(done.stdout.strip())


# GIT_* que não redirecionam o git pra um repositório/config diferente do
# clone que acabamos de criar, e que a pessoa pode legitimamente precisar:
# CA customizada (proxy corporativo, registro interno) sem elas quebraria todo
# clone HTTPS que dependesse dela. Só voltam se já estivessem no ambiente de
# quem chamou; nunca inventamos valor pra elas.
_SAFE_GIT_ENV = ("GIT_SSL_CAINFO", "GIT_SSL_CAPATH")


def _git_env(ssh=False):
    """Ambiente do subprocesso git: toda variável `GIT_*` herdada é removida
    primeiro — nenhuma delas (`GIT_DIR`, `GIT_CONFIG_GLOBAL`, `GIT_COMMON_DIR`,
    `GIT_CONFIG_COUNT`, etc.) tem uso legítimo aqui, e preservar qualquer uma
    por engano reabriria a porta que estamos fechando (redirecionar o git pra
    um `.git`/índice/config que não é o do clone que acabamos de criar). Só
    depois disso o código volta a acrescentar, de propósito, as poucas que
    ele mesmo decide usar — as fixas, as de `_SAFE_GIT_ENV` quando a pessoa já
    as tinha, e (só pra fonte SSH) a de conexão."""
    original = os.environ
    env = {k: v for k, v in original.items() if not k.startswith("GIT_")}
    for key in _SAFE_GIT_ENV:
        if key in original:
            env[key] = original[key]
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    if not ssh:
        return env
    # SSH sem terminal para responder a um prompt de host novo/senha trava o
    # processo; só usamos o `BatchMode=yes` de reserva quando a pessoa não já
    # tem a própria forma de conectar (variável de ambiente ou `core.sshCommand`).
    user_ssh_command = original.get("GIT_SSH_COMMAND")
    user_ssh = original.get("GIT_SSH")
    if user_ssh_command:
        env["GIT_SSH_COMMAND"] = user_ssh_command
    elif user_ssh:
        env["GIT_SSH"] = user_ssh
        # GIT_SSH_VARIANT (ex.: "ssh" x "putty"/"plink") só faz sentido junto
        # com GIT_SSH — diz ao git como montar os argumentos pro cliente
        # legado que essa variável aponta; sem GIT_SSH não há o que descrever.
        if "GIT_SSH_VARIANT" in original:
            env["GIT_SSH_VARIANT"] = original["GIT_SSH_VARIANT"]
    elif not _has_core_ssh_command(env):
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
    """(modo, sha, caminho, tamanho) de cada entrada de `git ls-tree -r -l HEAD` —
    inclui todo blob recursivamente e as entradas de submódulo (que `-r` não
    expande). `-l` traz o tamanho declarado do objeto (`-` para submódulo), o que
    deixa checar o tamanho ANTES de pedir o conteúdo (`cat-file blob`) — um blob
    gigante nunca chega a ser lido só para descobrirmos que ele estoura o teto."""
    raw = _git(["ls-tree", "-r", "-l", "-z", "HEAD"], cwd=dest)
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


def _refuse_git_path_component(path):
    """Recusa qualquer entrada cujo caminho tenha um componente `.git` — em
    qualquer maiúsc./minúsc.; com ponto(s)/espaço(s) sobrando à direita
    (`.git.`, `.git `, `.GIT. `), porque o Windows apaga ponto e espaço à
    direita do nome ao gravar em disco, então essas variantes viram `.git` de
    verdade sem bater numa comparação exata; ou o nome curto 8.3 `GIT~1`
    (também em qualquer maiúsc./minúsc.), que o NTFS pode resolver como alias
    de `.git`. Mesmo já nunca fazendo `checkout`, escrever um `.git`/arquivo
    dentro da própria pasta materializada não é conteúdo de plugin: na melhor
    das hipóteses é lixo, na pior é uma tentativa de plantar metadado git que
    outra ferramenta (fora deste código) trataria como especial mais adiante."""
    for part in path.split("/"):
        folded = part.casefold()
        if folded.rstrip(". ") == ".git" or folded == "git~1":
            raise ValueError(f'Caminho não pode ter um componente equivalente a ".git" no histórico git: {path!r}.')


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


def _materialize_tree(dest, paths_only=None):
    """Escreve o conteúdo de HEAD em `dest` (que já é a pasta clonada com
    `--no-checkout`, ainda sem nenhum arquivo de trabalho), um blob por vez, sem
    jamais passar por um `checkout` — por isso sem filtro/smudge/hook. Recusa
    qualquer modo que não seja arquivo regular, qualquer caminho com componente
    `.git`, e qualquer blob (mesmo na passada cedo) maior que `MAX_BYTES` — tudo
    isso ANTES de pedir o conteúdo do blob. Na passada completa (`paths_only=None`)
    também limita o total de arquivos/bytes a `MAX_FILES`/`MAX_BYTES`.

    `paths_only`, quando dado, materializa só esses caminhos exatos — usado pela
    validação cedo do manifesto (I4), que não passa pelo teto de total (são no
    máximo dois arquivos pequenos: o manifesto e o `entry`), mas passa pelo teto
    por-blob do jeito que qualquer outra entrada passa."""
    root = dest.resolve()
    entries = [entry for entry in _tree_entries(dest) if paths_only is None or entry[2] in paths_only]
    _refuse_tree_collisions(path for _mode, _sha, path, _size in entries)
    total_files = 0
    total_bytes = 0
    for mode, sha, path, size in entries:
        if mode not in _ALLOWED_TREE_MODES:
            reason = _REFUSED_TREE_MODES.get(mode, f"modo {mode}")
            raise ValueError(f"O plugin tem {reason} em {path!r} no histórico git; isso não é aceito.")
        _refuse_git_path_component(path)
        _refuse_oversized_blob(path, size)
        if paths_only is None:
            total_files += 1
            if total_files > MAX_FILES:
                raise ValueError(f"O plugin tem mais de {MAX_FILES} arquivos; recusado.")
            total_bytes += size or 0
            if total_bytes > MAX_BYTES:
                raise ValueError(f"O plugin passa de {MAX_BYTES // (1024 * 1024)} MB; recusado.")
        content = _git_blob(dest, sha)
        _write_tree_entry(dest, root, path, mode, content)


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
        _refuse_tree_collisions(rel.as_posix() for rel, _path in loader._counted_files(folder))
        try:
            shutil.copytree(folder, dest, symlinks=True, ignore=COPY_IGNORE)
        except OSError as exc:
            raise ValueError(f"Não consegui copiar a pasta do plugin ({type(exc).__name__}).") from exc
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
    shutil.rmtree(entry, ignore_errors=True)


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
        if not target.exists():
            entry.replace(target)
        else:
            shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        # Uma corrida com outro processo (a pasta já não existe mais, ou o
        # destino apareceu entre o `exists()` e o `replace()`) nunca pode
        # abortar quem chamou `install`/`update`: na pior hipótese essa pasta
        # de resto fica pra próxima varredura.
        pass


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
        retired = folder.with_name(_new_old_staging_name(plugin_id))
        os.replace(folder, retired)
        try:
            os.replace(staging, folder)
        except OSError as exc:
            # M2: a troca de verdade falhou no meio — devolve o conteúdo antigo
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
