"""Origem git de um plugin: `GitSource`, validação e o git endurecido que o `install` usa.

Uma origem é uma pasta comum (`FolderSource`) ou um repositório git (`GitSource`):
URL `https://…` sem credencial, `git@host:caminho`, ou uma pasta local que é
repositório (`.git` de verdade, ou um repositório *bare*). `commit`, `ref` e
`subdir` são validados aqui, antes de qualquer processo git rodar: um valor que
começa com `-` viraria opção do git, e `..`/`//` não são nomes de ref válidos.

Todo processo git sai de `run_git`, com o ambiente limpo (`git_env`) e a
configuração de `GIT_HARDENING` na linha de comando.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

from ..runtime import stderr_tail
from .files import is_link

GIT_URL_RE = re.compile(r"(https://\S+|git@[A-Za-z0-9.-]+:\S+)")
GIT_TIMEOUT_S = 120
# Só o sha completo (SHA-1, 40 hex minúsculos): abreviado é ambíguo, maiúsculo é
# outra grafia do mesmo objeto, e repositório SHA-256 fica para uma versão futura.
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
REF_MAX_CHARS = 200
# Nome de ref aceito: começa por letra ou dígito (nunca `-`, que viraria opção do
# git, nem `/` ou `.`) e só usa ASCII seguro; `_ref_problem` recusa o resto do que o
# `git check-ref-format` recusa (`..`, `//`, `@{`, `.lock`, componente com ponto).
REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+-]*")


class GitSource(NamedTuple):
    """Repositório git fixado por commit.

    `repo` é a URL (`https://…`, `git@host:caminho`) ou o caminho absoluto de uma
    pasta local que é repositório. `commit` ausente é resolvido a partir de `ref`
    (ausente vale `HEAD`); `subdir` é a pasta do plugin dentro do repositório."""

    repo: str
    commit: str | None = None
    ref: str | None = None
    subdir: str | None = None


class FolderSource(NamedTuple):
    """Pasta local comum (sem repositório git), copiada como está."""

    path: str


Source = GitSource | FolderSource


def is_remote(repo):
    """`repo` é uma URL (`https://`, `git@`)? Senão é uma pasta local."""
    return repo.startswith(("https://", "git@"))


def is_ssh(repo):
    """`repo` usa SSH (`git@host:caminho`)?"""
    return repo.startswith("git@")


def validate_commit(commit):
    """`commit` como veio, se for o sha completo de 40 hex minúsculos."""
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise ValueError(
            "--commit tem que ser o sha completo do commit: 40 caracteres hexadecimais minúsculos "
            "(git rev-parse <ref>)."
        )
    return commit


def _ref_problem(ref):
    if len(ref) > REF_MAX_CHARS:
        return f"mais de {REF_MAX_CHARS} caracteres"
    if not REF_RE.fullmatch(ref):
        return "só letras, dígitos e . _ / + -, começando por letra ou dígito"
    if ".." in ref or "//" in ref or "@{" in ref:
        return "sem '..', '//' nem '@{'"
    if ref.endswith(("/", ".")):
        return "sem '/' ou '.' no fim"
    for part in ref.split("/"):
        if part.startswith(".") or part.endswith(".lock"):
            return "nenhuma parte começando por '.' ou terminando em '.lock'"
    return None


def validate_ref(ref):
    """`ref` como veio (branch, tag, `HEAD` ou `refs/…`), se for um nome de ref seguro."""
    problem = "vazia" if not isinstance(ref, str) or not ref else _ref_problem(ref)
    if problem:
        raise ValueError(f"--ref inválida ({problem}): {str(ref)[:80]!r}.")
    return ref


def _refuse_query_or_fragment(raw):
    # Verificação por substring, não `urlsplit`: cobre tanto `https://…` quanto a
    # sintaxe `git@host:caminho` (que não é uma URL de verdade e não tem "query"
    # nem "fragment" pro `urlsplit` reconhecer).
    if "?" in raw or "#" in raw:
        raise ValueError("URL git não pode ter query (?) nem fragmento (#); use uma URL sem esses caracteres.")


def validate_url(raw):
    """`raw` como veio, se for uma URL git aceita: `https://` sem credencial ou `git@host:caminho`."""
    if not GIT_URL_RE.fullmatch(raw):
        raise ValueError("--source tem que ser uma pasta local ou uma URL git (https://… ou git@host:caminho).")
    _refuse_query_or_fragment(raw)
    if raw.startswith("https://"):
        parts = urlsplit(raw)
        if parts.username or parts.password:
            raise ValueError("URL git com usuário/senha não é aceita; use uma URL sem credencial.")
    return raw


def is_repository(folder):
    """A pasta é um repositório git? Uma PASTA `.git` de verdade — um arquivo `.git`
    (gitfile de worktree/submódulo) ou um link apontariam para outro repositório,
    não para a pasta que a pessoa está vendo — ou um repositório *bare* (`HEAD`,
    `objects/` e `refs/` no topo)."""
    git_dir = folder / ".git"
    if git_dir.is_dir() and not is_link(git_dir):
        return True
    return (
        (folder / "HEAD").is_file()
        and (folder / "objects").is_dir()
        and (folder / "refs").is_dir()
        and not any(is_link(folder / name) for name in ("HEAD", "objects", "refs"))
    )


def parse_source(raw, *, commit=None, ref=None, subdir=None):
    """`--source` (e `--commit`/`--ref`/`--subdir`) como `GitSource` ou `FolderSource`.

    Nada roda aqui: só confere a forma. Pasta comum não aceita `--commit`/`--ref`/
    `--subdir` — eles só fazem sentido num repositório."""
    raw = str(raw).strip()
    if commit is not None:
        validate_commit(commit)
    if ref is not None:
        validate_ref(ref)
    folder = Path(raw).expanduser()
    if raw and not raw.startswith("-") and folder.is_dir():
        folder = folder.resolve()
        if is_repository(folder):
            return GitSource(str(folder), commit, ref, subdir)
        if commit is not None or ref is not None or subdir is not None:
            raise ValueError(
                "--commit, --ref e --subdir só valem para repositório git; para uma pasta comum, aponte "
                "--source direto para a pasta do plugin."
            )
        return FolderSource(str(folder))
    return GitSource(validate_url(raw), commit, ref, subdir)


# Configuração que vai em todo processo git, na linha de comando (vence qualquer
# config de repositório ou global):
# - `protocol.allow=never` com exceção só para `https` e `ssh`: nenhum outro
#   transporte (`ext::` rodaria um comando arbitrário como "URL", `http://` sem TLS,
#   `git://` sem autenticação, `file://` fora de uma pasta que a própria pessoa
#   indicou) é aceito, nem via redirecionamento ou `url.<x>.insteadOf`.
#   `protocol.file.allow=always` só entra para uma origem que é pasta local
#   (`LOCAL_PROTOCOL`); `protocol.ext.allow=never` fica explícito.
# - `transfer.fsckObjects=true`: objeto malformado vindo da origem é recusado no fetch.
# - `init.templateDir=` vazio: o `git init` do clone não copia hook nem config de template.
# - `core.hooksPath` para um diretório sem hooks desliga qualquer hook do clone;
#   os três `filter.lfs.*` desarmam o smudge do Git LFS especificamente. Nenhum
#   deles é o que realmente nos protege de um filtro malicioso arbitrário citado
#   em `.gitattributes` — isso é a materialização manual do `install`, que nunca
#   aciona filtro nenhum; estes são defesa em profundidade.
GIT_HARDENING = [
    "-c",
    "protocol.allow=never",
    "-c",
    "protocol.https.allow=always",
    "-c",
    "protocol.ssh.allow=always",
    "-c",
    "protocol.ext.allow=never",
    "-c",
    "transfer.fsckObjects=true",
    "-c",
    "init.templateDir=",
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "filter.lfs.process=",
    "-c",
    "filter.lfs.smudge=",
    "-c",
    "filter.lfs.required=false",
]
LOCAL_PROTOCOL = ["-c", "protocol.file.allow=always"]


def has_core_ssh_command(env):
    """`git config --global --includes --get core.sshCommand` já configurado
    pela pessoa — nesse caso o próprio git já sabe como conectar (e não
    precisamos, nem devemos, empurrar um `GIT_SSH_COMMAND` nosso por cima).
    `--global` de propósito: isto roda antes de existir repositório, então sem
    `--global` a busca cairia no cwd deste processo, que pode por acaso estar
    dentro de outro repositório qualquer. `--includes` resolve
    `[include]`/`[includeIf]` do config global, senão um `core.sshCommand`
    guardado num arquivo incluído passaria despercebido."""
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
SAFE_GIT_ENV = ("GIT_SSL_CAINFO", "GIT_SSL_CAPATH")


def git_env(ssh=False):
    """Ambiente do subprocesso git: toda variável `GIT_*` herdada é removida
    primeiro — nenhuma delas (`GIT_DIR`, `GIT_CONFIG_GLOBAL`, `GIT_COMMON_DIR`,
    `GIT_CONFIG_COUNT`, etc.) tem uso legítimo aqui, e preservar qualquer uma
    por engano reabriria a porta que estamos fechando (redirecionar o git pra
    um `.git`/índice/config que não é o do clone que acabamos de criar). Só
    depois disso o código volta a acrescentar, de propósito, as poucas que
    ele mesmo decide usar — as fixas, as de `SAFE_GIT_ENV` quando a pessoa já
    as tinha, e (só pra fonte SSH) a de conexão."""
    original = os.environ
    env = {k: v for k, v in original.items() if not k.startswith("GIT_")}
    for key in SAFE_GIT_ENV:
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
    elif not has_core_ssh_command(env):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return env


def run_git(args, cwd=None, ssh=False, binary=False, local=False):
    """Roda `git <GIT_HARDENING> <args>` com `git_env`; saída não zero vira `ValueError`.

    `local=True` (origem que é pasta local) libera o transporte `file://`."""
    git = shutil.which("git")
    if not git:
        raise ValueError("git não encontrado no PATH; instale o git ou use --source com uma pasta local.")
    env = git_env(ssh=ssh)
    text_kwargs = {} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}
    try:
        done = subprocess.run(
            [git, *GIT_HARDENING, *(LOCAL_PROTOCOL if local else []), *args],
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


def git_text(args, cwd=None, ssh=False, local=False):
    """`run_git` em modo texto, sem espaço nas pontas."""
    return run_git(args, cwd=cwd, ssh=ssh, local=local).strip()


def _transport(spec):
    """`(uri, ssh, local)` para falar com a origem de `spec`."""
    if is_remote(spec.repo):
        return spec.repo, is_ssh(spec.repo), False
    return Path(spec.repo).as_uri(), False, True


def _ref_names(ref):
    """Nomes completos que `ref` pode ser na origem: `HEAD`, `refs/…` como veio, ou branch e tag."""
    if ref in (None, "HEAD"):
        return ["HEAD"]
    if ref.startswith("refs/"):
        return [ref]
    return [f"refs/heads/{ref}", f"refs/tags/{ref}"]


def resolve_ref(spec):
    """`(nome completo, commit)` de `spec.ref` (ausente vale `HEAD`) na origem, via `git ls-remote`.

    Tag anotada vale pelo commit a que aponta (`^{}`). Um nome que é branch e tag ao
    mesmo tempo é ambíguo e recusado: escreva `refs/heads/<nome>` ou `refs/tags/<nome>`."""
    ref = spec.ref or "HEAD"
    names = _ref_names(ref)
    uri, ssh, local = _transport(spec)
    patterns = [pattern for name in names for pattern in (name, f"{name}^{{}}")]
    listed = {}
    for line in git_text(["ls-remote", "--", uri, *patterns], ssh=ssh, local=local).splitlines():
        sha, _, name = line.partition("\t")
        listed[name.strip()] = sha.strip()
    found = {name: listed.get(f"{name}^{{}}") or listed[name] for name in names if name in listed}
    if not found:
        raise ValueError(f"A ref {ref!r} não existe na origem; confira o nome da branch ou da tag.")
    if len(found) > 1:
        raise ValueError(
            f"A ref {ref!r} é ambígua na origem (branch e tag com o mesmo nome); use --ref refs/heads/{ref} "
            f"ou --ref refs/tags/{ref}."
        )
    ((name, sha),) = found.items()
    if not COMMIT_RE.fullmatch(sha):
        raise ValueError(f"A ref {ref!r} não aponta para um commit SHA-1 de 40 caracteres; recusado.")
    return name, sha


def _peeled_commit(clone, rev):
    """O commit de `rev` no clone (`rev-parse --verify <rev>^{commit}`), ou `None`."""
    try:
        return git_text(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], cwd=clone)
    except ValueError:
        return None


def _fetch_one(spec, clone, rev):
    """`git fetch --depth 1 <origem> <rev>` no clone: só esse commit, sem tags."""
    uri, ssh, local = _transport(spec)
    git_text(["fetch", "--quiet", "--no-tags", "--depth", "1", "--", uri, rev], cwd=clone, ssh=ssh, local=local)


def _fetch_ref_tip(spec, clone, sha, ref_name):
    """Reserva do fetch por sha: `fetch --depth 1 <ref>`, e a ponta tem que ser `sha`."""
    try:
        _fetch_one(spec, clone, ref_name)
    except ValueError as exc:
        raise ValueError(f"Não consegui buscar o commit {sha[:12]} na origem: {exc}") from exc
    if _peeled_commit(clone, "FETCH_HEAD") != sha:
        raise ValueError(
            f"Commit {sha[:12]} não encontrado na origem: o servidor não entrega commit por sha e ele não é "
            f"a ponta de {ref_name}. Confira o --commit (ou use --ref com a branch/tag que aponta para ele)."
        )


def fetch(spec, clone):
    """Traz para `clone` (criado aqui, vazio) só o commit de `spec` e devolve o sha dele.

    Sem `spec.commit`, resolve `spec.ref` antes (`resolve_ref`). Busca o commit pelo
    sha (`fetch --depth 1 <sha>`); servidor que não entrega commit por sha recebe
    `fetch --depth 1 <ref>`, e a ponta dessa ref tem que ser o próprio sha — senão,
    recusa. Depois, `<sha>^{commit}` no clone tem que ser o sha pedido."""
    if spec.commit is not None:
        sha, ref_name = validate_commit(spec.commit), validate_ref(spec.ref or "HEAD")
    else:
        ref_name, sha = resolve_ref(spec)
    git_text(["init", "--quiet", str(clone)])
    try:
        _fetch_one(spec, clone, sha)
    except ValueError:
        _fetch_ref_tip(spec, clone, sha, ref_name)
    if _peeled_commit(clone, sha) != sha:
        raise ValueError(f"Commit {sha[:12]} não encontrado na origem; confira o --commit.")
    return sha
