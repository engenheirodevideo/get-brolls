"""`plugins --action install|update`: trazer um plugin de uma pasta ou de um repositório git.

Dois passos, como o `enable`: sem `--yes` só mostra o que chegaria (id, versão,
permissões, contribuições, origem e commit) e nada fica em `plugins/`; com `--yes`
move para `plugins/<id>`, habilita com pin de hash e grava origem/commit em
`plugins.json`. Nenhum código do plugin roda aqui — só o manifesto é lido.
"""

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
from .manifest import compatibility_problem, read_manifest

_log = logs.get("sdk")

GIT_URL_RE = re.compile(r"(https://\S+|git@[A-Za-z0-9.-]+:\S+)")
GIT_TIMEOUT_S = 120
COPY_IGNORE = shutil.ignore_patterns(".git", ".hg", ".svn", "__pycache__", "*.pyc", ".DS_Store", "Thumbs.db")
# `os.devnull` desliga hooks do clone (`core.hooksPath`) sem depender de "/dev/null"
# existir (Windows usa "nul"). `protocol.ext.allow=never` recusa o transporte
# `ext::` (que executaria um comando arbitrário como "URL"); `protocol.file.allow`
# fica em "always" só para o clone que nós mesmos montamos com `folder.as_uri()" —
# nunca para uma URL que a pessoa digitou, que só passa pelo `GIT_URL_RE`
# (https://… ou git@host:caminho), então "file::"/"ext::" digitado nunca casa e
# já cai no ValueError antes de chegar ao git.
_GIT_HARDENING = ["-c", "protocol.ext.allow=never", "-c", f"core.hooksPath={os.devnull}"]


def _git(args, cwd=None):
    git = shutil.which("git")
    if not git:
        raise ValueError("git não encontrado no PATH; instale o git ou use --source com uma pasta local.")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
    try:
        done = subprocess.run(
            [git, *_GIT_HARDENING, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"git excedeu {GIT_TIMEOUT_S} s; confira a rede e a URL do repositório.") from exc
    if done.returncode != 0:
        detail = stderr_tail(done.stderr)
        raise ValueError(f"git falhou (exit {done.returncode})" + (f": {detail}" if detail else "."))
    return done.stdout.strip()


def _refuse_links(folder):
    if any(path.is_symlink() for path in folder.rglob("*")):
        raise ValueError("O plugin tem link simbólico; copie os arquivos reais para a pasta antes de instalar.")


def _materialize(source, dest):
    """Traz `source` para `dest` (que não existe) e devolve `(origem, commit)`.

    Pasta com `.git` e URL git passam por `git clone --depth 1` (commit gravado);
    pasta comum é copiada sem `.git`/`__pycache__` (commit `None`)."""
    raw = str(source).strip()
    folder = Path(raw).expanduser()
    if raw and not raw.startswith("-") and folder.is_dir():
        folder = folder.resolve()
        _refuse_links(folder)
        if (folder / ".git").exists():
            _git(
                [
                    "-c",
                    "protocol.file.allow=always",
                    "clone",
                    "--depth",
                    "1",
                    "--quiet",
                    "--",
                    folder.as_uri(),
                    str(dest),
                ]
            )
            commit = _git(["rev-parse", "HEAD"], cwd=dest)
            shutil.rmtree(dest / ".git", ignore_errors=True)
            return str(folder), commit
        shutil.copytree(folder, dest, ignore=COPY_IGNORE)
        return str(folder), None
    if not GIT_URL_RE.fullmatch(raw):
        raise ValueError("--source tem que ser uma pasta local ou uma URL git (https://… ou git@host:caminho).")
    if raw.startswith("https://"):
        parts = urlsplit(raw)
        if parts.username or parts.password:
            raise ValueError("URL git com usuário/senha não é aceita; use uma URL sem credencial.")
    _git(["clone", "--depth", "1", "--quiet", "--", raw, str(dest)])
    commit = _git(["rev-parse", "HEAD"], cwd=dest)
    _refuse_links(dest)
    shutil.rmtree(dest / ".git", ignore_errors=True)
    return raw, commit


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


def _summary(manifest, origin, commit):
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "version": manifest["version"],
        "contributes": {k: v for k, v in manifest["contributes"].items() if v},
        "permissions": manifest["permissions"],
        "source": origin,
        "commit": commit,
    }


def install(source, confirm):
    staging = _staging()
    try:
        origin, commit = _materialize(source, staging)
        manifest = _checked_manifest(staging)
        target = loader.plugins_root() / manifest["id"]
        if target.exists():
            raise ValueError(
                f"Plugin {manifest['id']} já está instalado; use plugins --action update --id {manifest['id']}."
            )
        preview = _summary(manifest, origin, commit)
        if not confirm:
            return {"installed": False, "plugin": preview, "note": loader.SANDBOX_NOTE}
        staging.replace(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    sha = loader.pin(manifest, target, {"source": origin, "commit": commit})
    logs.event(_log, logging.INFO, "plugin_installed", plugin=manifest["id"], version=manifest["version"])
    return {"installed": True, "plugin": {**preview, "sha256": sha}, "note": loader.SANDBOX_NOTE}


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


def update(plugin_id, confirm):
    origin = (loader.read_state().get("sources") or {}).get(plugin_id)
    if origin is None:
        raise ValueError(
            f"Plugin {plugin_id} não foi instalado por plugins --action install; atualize a pasta à mão e rode enable."
        )
    _, folder, current = loader.find(plugin_id)
    staging = _staging()
    try:
        source, commit = _materialize(origin["source"], staging)
        manifest = _checked_manifest(staging)
        if manifest["id"] != plugin_id:
            raise ValueError(f"A origem agora traz o plugin {manifest['id']}, não {plugin_id}; nada foi trocado.")
        preview = _summary(manifest, source, commit)
        diff = _diff(folder, current, staging, manifest)
        if not confirm:
            return {"updated": False, "plugin": preview, "diff": diff, "note": loader.SANDBOX_NOTE}
        retired = folder.with_name(f".old-{uuid.uuid4().hex}")
        folder.replace(retired)
        staging.replace(folder)
        shutil.rmtree(retired, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    sha = loader.pin(manifest, folder, {"source": source, "commit": commit})
    logs.event(
        _log, logging.INFO, "plugin_updated", plugin=plugin_id, version=manifest["version"], commit=(commit or "-")[:12]
    )
    return {"updated": True, "plugin": {**preview, "sha256": sha}, "diff": diff, "note": loader.SANDBOX_NOTE}
