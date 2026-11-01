"""`plugins --action install|update`: trazer um plugin de uma pasta ou de um repositório git.

Dois passos, como o `enable`: sem `--yes` só mostra o que chegaria (id, versão,
permissões, contribuições, origem, commit e o sha256 do conteúdo já materializado)
e nada fica em `plugins/`; com `--yes` **e** `--expect <sha256>` batendo com essa
prévia, move para `plugins/<id>`, habilita com pin de hash e grava origem/commit
em `plugins.json`. Nenhum código do plugin roda aqui — só o manifesto é lido.

A materialização (a cópia da pasta e a árvore git, que nunca é `checkout`ada) vive em `materialize`.
"""

import logging
import os
from pathlib import Path
from typing import NamedTuple

from .. import effects, labels, logs, runtime
from .._paths import strict_exists, strict_is_dir
from . import git_source, loader, materialize, registry_state
from .files import counted_files
from .manifest import read_manifest
from .materialize import _checked_manifest, _materialize
from .staging import (
    _new_old_staging_name,
    _staging,
    _staging_names_hidden,
    _sweep_stale_staging,
)

_log = logs.get("sdk")

fetch_file = materialize.fetch_file  # o marketplace e os testes leem `install.fetch_file`


# Quantos nomes de arquivo a prévia do install/update lista (o total vem sempre).
PREVIEW_FILES_MAX = 50


def _file_list(folder):
    """Arquivos que o pin vai cobrir, para a prévia: total e nomes (no máximo `PREVIEW_FILES_MAX`)."""
    names = [rel.as_posix() for rel, _path in counted_files(folder)]
    return {"count": len(names), "names": names[:PREVIEW_FILES_MAX], "truncated": len(names) > PREVIEW_FILES_MAX}


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
    tmp = Path(effects.mkdtemp(prefix="gb-digest-"))
    try:
        dest = tmp / "plugin"
        _materialize(spec, dest, compat=False)
        return loader.folder_digest(dest), read_manifest(dest, require_folder_match=False)
    finally:
        # by attribute (as in staging/materialize) so the Windows retry test can patch it;
        # remove/marketplace/git_source bind the name
        runtime.force_rmtree(tmp)


def check_expect(expect, sha, *, action, plugin_id, announce=True):
    """`loader.check_expect` (compartilhado com o `enable` de plugin suspenso), com o log da confirmação.

    A recusa vira `plugin_confirm_refused` (`expect_missing` ou `expect_mismatch`, com o `error_code`);
    passar vira `plugin_confirmed`, a menos que `announce=False` (o marketplace confere um valor
    derivado antes e o sha256 do conteúdo depois: a confirmação é uma só).
    """
    try:
        loader.check_expect(expect, sha)
    except ValueError as exc:
        reason = "expect_mismatch" if expect else "expect_missing"
        logs.refusal(_log, "plugin_confirm_refused", exc, action=action, plugin=plugin_id, reason=reason)
        raise
    if announce:
        logs.event(_log, logging.INFO, "plugin_confirmed", action=action, plugin=plugin_id, sha256=sha[:12])


# Chaves que um chamador (o marketplace) pode acrescentar à origem gravada.
ORIGIN_EXTRA_KEYS = ("marketplace", "tier", "index_commit")


def _with_extra(origin, origin_extra):
    extra = dict(origin_extra or {})
    unknown = set(extra) - set(ORIGIN_EXTRA_KEYS)
    if unknown:
        raise ValueError(f"origin_extra só aceita {', '.join(ORIGIN_EXTRA_KEYS)}: {', '.join(sorted(unknown))}.")
    if any(value is not None and not isinstance(value, str) for value in extra.values()):
        raise ValueError("origin_extra só aceita texto ou None.")
    return {**origin, **extra}


def _origin_kind(origin):
    """De onde veio o plugin, para o log: `marketplace`, `git` (repositório) ou `folder` (pasta comum)."""
    if origin.get("marketplace"):
        return "marketplace"
    return "git" if origin.get("commit") else "folder"


def _log_preview(action, manifest, origin, preview, current=None):
    """`plugin_preview` (INFO): o que a prévia mostrou, em ids e contagens (nunca caminho, URL nem nome de arquivo)."""
    added = loader.permissions_added(current["permissions"] if current else None, manifest["permissions"])
    logs.event(
        _log,
        logging.INFO,
        "plugin_preview",
        action=action,
        plugin=manifest["id"],
        version=manifest["version"],
        source=_origin_kind(origin),
        marketplace=origin.get("marketplace"),
        commit=(origin.get("commit") or "-")[:12],
        files=preview["files"]["count"],
        permissions_added=sum(len(items) for items in added.values()),
        warnings=len(preview.get("warnings", ())),
    )


def install(  # noqa: PLR0913 - keyword-only pin options, one per CLI flag
    source, confirm, expect=None, *, commit=None, ref=None, subdir=None
):  # pylint: disable=too-many-arguments  # keyword-only pin options, one per CLI flag
    """Prévia (sem `confirm`) ou instalação de um plugin a partir de pasta local ou URL git.

    `commit` fixa o commit (senão, `ref` — ausente vale `HEAD` — é resolvida na
    origem); `subdir` é a pasta do plugin dentro do repositório. Confirmar exige o
    sha256 da prévia em `expect`; o plugin sai instalado e habilitado."""
    spec = git_source.parse_source(source, commit=commit, ref=ref, subdir=subdir)
    return install_from(spec, confirm, expect)


def install_from(spec, confirm, expect=None, *, verify=None, origin_extra=None):
    """`install` a partir de um `git_source.Source` já montado.

    `verify(manifest, sha256)` roda depois de materializar e antes da prévia; um
    `ValueError` dele recusa o install e o staging é apagado. `origin_extra`
    acrescenta `marketplace`/`tier`/`index_commit` à origem gravada."""
    with _staging_names_hidden():
        return _install_from(spec, confirm, expect, (verify, origin_extra))


def _install_from(spec, confirm, expect, hooks):
    verify, origin_extra = hooks
    loader.read_state()  # plugins.json corrompido recusa antes de qualquer mutação.
    _sweep_stale_staging()
    staging = _staging()
    try:
        origin, warnings = _materialize(spec, staging)
        origin = _with_extra(origin, origin_extra)
        manifest = _checked_manifest(staging)
        target = loader.plugins_root() / manifest["id"]
        if strict_exists(target):
            raise logs.refusal(
                _log,
                "plugin_refused",
                ValueError(
                    f"Plugin {manifest['id']} já está instalado; use plugins --action update --id {manifest['id']}."
                ),
                action="install",
                plugin=manifest["id"],
                reason="already_installed",
            )
        content = loader.PinContent(*loader.pin_digests(staging), loader.pinned_roots(manifest))
        if verify is not None:
            verify(manifest, content.sha)
        preview = _summary(manifest, origin, content, _file_list(staging), warnings)
        if not confirm:
            _log_preview("install", manifest, origin, preview)
            return {"installed": False, "plugin": preview, "note": loader.EXPECT_NOTE}
        check_expect(expect, content.sha, action="install", plugin_id=manifest["id"])
        with effects.op("plugin.install", manifest["id"]):
            pinned_sha, _ = _pin_then_move(manifest, origin, content, lambda: effects.replace(staging, target))
    finally:
        runtime.force_rmtree(staging)
    logs.event(
        _log,
        logging.INFO,
        "plugin_installed",
        plugin=manifest["id"],
        version=manifest["version"],
        source=_origin_kind(origin),
        marketplace=origin.get("marketplace"),
    )
    return {"installed": True, "plugin": {**preview, "sha256": pinned_sha}, "note": loader.DONE_NOTE}


def _pin_then_move(manifest, origin, content, move, *, enabled=True):
    """Grava o pin (do conteúdo conferido no staging) e só depois põe a pasta em `plugins/<id>` (`move()`).

    Vale para o `install` e para o `update`. Nessa ordem, um `plugins.json` que não grava (disco cheio)
    não deixa pasta sem pin, que o `install` chamaria de "já instalado" e o `update` de "não foi instalado
    por install", nem código novo com o pin antigo (suspenso, sem o conteúdo de antes). Se a troca de
    pasta falhar, o `plugins.json` volta ao que era. `enabled` como em `loader.pin`. Devolve
    `(sha256 gravado, o que move() devolveu)`."""
    previous = loader.read_state()
    pinned_sha = loader.pin(manifest, loader.plugins_root() / manifest["id"], origin, enabled=enabled, content=content)
    try:
        moved = move()
    except BaseException:
        try:
            loader.write_state(previous)
        except OSError:
            logs.event(_log, logging.ERROR, "plugin_swap_failed", plugin=manifest["id"], rolled_back=False)
        else:
            logs.event(_log, logging.WARNING, "plugin_swap_failed", plugin=manifest["id"], rolled_back=True)
        registry_state.forget()
        raise
    return pinned_sha, moved


def _diff(old_folder, old_manifest, new_folder, new_manifest):
    before = loader.file_digests(old_folder)
    after = loader.file_digests(new_folder)
    old_permissions = old_manifest["permissions"] if old_manifest else None
    added = loader.permissions_added(old_permissions, new_manifest["permissions"])
    return {
        "version": {"from": old_manifest["version"] if old_manifest else None, "to": new_manifest["version"]},
        "permissions": loader.permissions_diff(old_permissions, new_manifest["permissions"]),
        "permissions_added": added,
        "permissions_increased": any(added.values()),
        "files": loader.files_diff(before, after),
    }


class _Staged(NamedTuple):
    """O que o `update` materializou no staging e conferiu, antes de trocar a pasta."""

    origin: dict
    manifest: dict
    content: "loader.PinContent"
    preview: dict
    diff: dict


def _stage_update(plugin_id, spec, staging, installed, hooks):
    """Materializa `spec` em `staging`, confere o manifesto e monta a prévia com o diff.

    `installed` é `(pasta, manifesto)` do plugin instalado; `hooks`, `(verify, origin_extra)`."""
    folder, current = installed
    verify, origin_extra = hooks
    origin, warnings = _materialize(spec, staging)
    origin = _with_extra(origin, origin_extra)
    manifest = _checked_manifest(staging)
    if manifest["id"] != plugin_id:
        raise logs.refusal(
            _log,
            "plugin_refused",
            ValueError(f"A origem agora traz o plugin {manifest['id']}, não {plugin_id}; nada foi trocado."),
            action="update",
            plugin=plugin_id,
            reason="id_mismatch",
        )
    content = loader.PinContent(*loader.pin_digests(staging), loader.pinned_roots(manifest))
    if verify is not None:
        verify(manifest, content.sha)
    preview = _summary(manifest, origin, content, _file_list(staging), warnings)
    diff = _diff(folder, current, staging, manifest)
    return _Staged(origin, manifest, content, preview, diff)


def _swap_in(plugin_id, folder, staging):
    """Troca a pasta instalada `folder` pelo conteúdo de `staging` e devolve onde ficou a antiga
    (`.old-*`), que quem chamou apaga depois de o pin estar gravado."""
    with effects.op("plugin.install", plugin_id):
        retired = folder.with_name(_new_old_staging_name(plugin_id))
        effects.replace(folder, retired)
        try:
            effects.replace(staging, folder)
        except OSError as exc:
            # A troca de verdade falhou no meio — devolve o conteúdo antigo
            # ao lugar em vez de deixar o plugin sem pasta nenhuma.
            try:
                effects.replace(retired, folder)
            except OSError as rollback_exc:
                logs.event(_log, logging.ERROR, "plugin_swap_failed", plugin=plugin_id, rolled_back=False)
                raise ValueError(
                    f"A troca de {plugin_id} falhou ({type(exc).__name__}) e desfazê-la também falhou "
                    f"({type(rollback_exc).__name__}); o conteúdo anterior pode estar em {labels.shown(retired)}, "
                    f"não em {labels.shown(folder)}. Confira as duas pastas manualmente antes de tentar de novo."
                ) from rollback_exc
            logs.event(_log, logging.WARNING, "plugin_swap_failed", plugin=plugin_id, rolled_back=True)
            raise
        return retired


def update(plugin_id, confirm, expect=None, *, commit=None):
    """Prévia (com o diff) ou troca de um plugin instalado pela versão da origem gravada.

    Sem `commit`, a ref gravada (ausente vale `HEAD`) é resolvida de novo na origem;
    com `commit`, fixa aquele commit (inclusive um anterior, para voltar atrás).
    Confirmar exige o sha256 da prévia em `expect`; um plugin desabilitado continua
    desabilitado."""
    origin = _recorded_origin(plugin_id, loader.read_state())
    return update_from(plugin_id, recorded_spec(plugin_id, origin, commit), confirm, expect)


def _origin_refused(plugin_id, reason, message):
    return logs.refusal(_log, "plugin_refused", ValueError(message), action="update", plugin=plugin_id, reason=reason)


def _origin_gone(plugin_id, source, problem):
    """A origem gravada não serve mais: o plugin só volta de outra origem."""
    return _origin_refused(
        plugin_id,
        "origin_unavailable",
        f"A origem gravada do plugin {plugin_id} ({source}) não serve mais para o update: {problem}. Para trazer o "
        f"plugin de outra origem, rode plugins --action remove --id {plugin_id} --yes e depois "
        "plugins --action install --source <pasta ou URL git>.",
    )


def _check_recorded_folder(plugin_id, origin, commit):
    """A pasta gravada em `origin` tem que servir do jeito que a instalação a gravou.

    Cada recusa diz o conserto certo: só a pasta que sumiu (ou deixou de ser o repositório
    da instalação) pede outra origem. Um `.git` quebrado se conserta no lugar, e um
    `--commit` numa pasta comum é o erro da flag, não da origem."""
    source = origin["source"]
    folder = Path(source).expanduser()
    if not strict_is_dir(folder):
        raise _origin_gone(plugin_id, source, "a pasta não existe mais")
    folder = folder.resolve()
    if git_source.is_repository(folder):
        return
    # lenient: repository sniffing after is_repository; the copy raises on a locked folder
    if os.path.lexists(folder / ".git"):
        raise _origin_refused(
            plugin_id,
            "origin_invalid_worktree",
            f"A origem gravada do plugin {plugin_id} ({source}) tem um .git que não é a pasta de um repositório "
            "nem o gitfile de uma worktree ou submódulo desta pasta (link, gitfile copiado ou quebrado). Conserte "
            "o .git (numa worktree, git worktree repair) ou tire o .git para a pasta ser copiada como pasta comum, "
            "e rode o update de novo.",
        )
    if origin.get("ref") is not None or origin.get("subdir") is not None:
        raise _origin_gone(plugin_id, source, "a pasta não é mais o repositório git que era na instalação")
    if commit is not None:
        raise _origin_refused(
            plugin_id,
            "pin_flags_on_folder",
            f"--commit só vale para plugin instalado de um repositório git, e a origem gravada do plugin "
            f"{plugin_id} ({source}) é uma pasta comum: rode plugins --action update --id {plugin_id} sem --commit.",
        )


def recorded_spec(plugin_id, origin, commit=None):
    """A origem gravada de `plugin_id` (`sources.<id>` do plugins.json) como `git_source.Source`.

    Passa pela mesma validação de um `--source` novo, mas a recusa fala da origem gravada e do
    conserto (`_check_recorded_folder`): a pessoa não digitou `--source` nenhum no `update`. Só
    o `commit` (o `--commit` do update) é dela, e é conferido antes, com a própria mensagem.
    Uma URL (ou ref/subpasta) gravada que a validação de hoje não aceita mais deixa também a
    linha `git_source_refused` com a regra que falhou."""
    if commit is not None:
        git_source.validate_commit(commit)
    source = origin["source"]
    if not git_source.is_remote(source):
        _check_recorded_folder(plugin_id, origin, commit)
    try:
        return git_source.parse_source(source, commit=commit, ref=origin.get("ref"), subdir=origin.get("subdir"))
    except ValueError as exc:
        problem = (
            "a URL não é mais aceita"
            if git_source.is_remote(source)
            else "a ref ou a subpasta gravada não é mais aceita"
        )
        raise _origin_gone(plugin_id, source, problem) from exc


def _recorded_origin(plugin_id, state):
    origin = (state.get("sources") or {}).get(plugin_id)
    if origin is None:
        raise logs.refusal(
            _log,
            "plugin_refused",
            ValueError(
                f"Plugin {plugin_id} não foi instalado por plugins --action install; "
                "atualize a pasta à mão e rode enable."
            ),
            action="update",
            plugin=plugin_id,
            reason="no_recorded_origin",
        )
    return origin


def update_from(  # noqa: PLR0913 - keyword-only hooks, as in install_from
    plugin_id, spec, confirm, expect=None, *, verify=None, origin_extra=None
):  # pylint: disable=too-many-arguments  # keyword-only hooks, as in install_from
    """`update` de `plugin_id` a partir de um `git_source.Source` já montado.

    `verify` e `origin_extra` como em `install_from`. O plugin tem que ter sido
    instalado por `install` (origem gravada)."""
    with _staging_names_hidden():
        return _update_from(plugin_id, spec, confirm, expect, (verify, origin_extra))


def _update_from(plugin_id, spec, confirm, expect, hooks):
    state = loader.read_state()  # plugins.json corrompido recusa antes de qualquer mutação.
    _recorded_origin(plugin_id, state)
    was_enabled = plugin_id in state.get("enabled", {})
    _sweep_stale_staging()
    _, folder, current = loader.find(plugin_id)
    staging = _staging()
    try:
        staged = _stage_update(plugin_id, spec, staging, (folder, current), hooks)
        if not confirm:
            _log_preview("update", staged.manifest, staged.origin, staged.preview, current)
            return {"updated": False, "plugin": staged.preview, "diff": staged.diff, "note": loader.EXPECT_NOTE}
        check_expect(expect, staged.content.sha, action="update", plugin_id=plugin_id)
        # Pin antes da troca, como no `install`. Atualizar o conteúdo não liga de volta um plugin que
        # estava desabilitado — só quem já estava habilitado sai daqui com pin novo (senão o pin some).
        sha_after, retired = _pin_then_move(
            staged.manifest,
            staged.origin,
            staged.content,
            lambda: _swap_in(plugin_id, folder, staging),
            enabled=was_enabled,
        )
        runtime.force_rmtree(retired)
    finally:
        runtime.force_rmtree(staging)
    logs.event(
        _log,
        logging.INFO,
        "plugin_updated",
        plugin=plugin_id,
        version=staged.manifest["version"],
        commit=(staged.origin["commit"] or "-")[:12],
        source=_origin_kind(staged.origin),
        marketplace=staged.origin.get("marketplace"),
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
