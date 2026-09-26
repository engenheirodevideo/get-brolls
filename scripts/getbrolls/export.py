"""`gb export --to <exporter>`: um roteiro revisado vira um projeto de edição numa pasta numerada.

Fluxo: portões (`export_gates`) → exporter do registro (só plugin habilitado) →
plano de export (`export_plan.build`, com os resolvedores de plugin injetados) →
`run_exporter` do SDK (valida o que o plugin devolveu) → pedidos de mídia conferidos
contra o plano → `--dry-run` para aqui; senão a pasta numerada nova é gravada
(`export_folder.write_export`, mídia por `export_place.place`). Nada passa por
`sync_formats`, `recover` ou `entrega/`.
"""

import errno
import functools
import os
from pathlib import Path

from . import __version__, export_folder, export_gates, export_place, export_plan
from .delivery import copies_forced
from .sdk import guard, loader, safe_copy
from .sdk.contracts import CORE, NAME_RE
from .sdk.exporters import find_local_paths, note_line, run_exporter
from .sdk.registry import get_registry
from .sdk.resolvers import resolve_with_plugins

OPTIONS = {"args": {}}
# Método que o dry-run mostra para cada fonte (o real pode cair para cópia).
_PREDICTED = {"hardlink": "hardlink", "clone": "clone", "plugin": "copy"}
# Clone pelo `cp`: num disco que não clona ele copia tudo, então o resumo diz "até N MB".
_CLONE_METHODS = ("clone", "reflink-auto")
_OS_REASONS = {
    errno.EACCES: "sem permissão",
    errno.EPERM: "sem permissão",
    errno.EROFS: "o disco está somente leitura",
    errno.ENOSPC: "o disco está cheio",
}
_CHANGED = "{name} mudou durante o export: repita."
_UNSAFE = {
    safe_copy.CHANGED: _CHANGED,
    safe_copy.TOO_BIG: _CHANGED,
    safe_copy.NOT_REGULAR: "{name} deixou de ser um arquivo durante o export: repita.",
    safe_copy.LINKED: "{name} tem mais de um nome no disco (hardlink): o export não copia esse arquivo do plugin {store}.",
    safe_copy.OUTSIDE: "{name} está fora de permissions.paths do plugin {store}: o export não copia esse arquivo.",
    safe_copy.OPEN_FAILED: "{name} não pôde ser aberto para a cópia ({type_name}): repita.",
    safe_copy.TARGET_EXISTS: "{name} já existe no export em montagem: repita o export.",
    safe_copy.TARGET_FAILED: "Não consegui criar {name} no export em montagem ({type_name}): confira o disco e repita.",
}


def _unavailable(registry, name):
    """Por que `--to <name>` não tem exporter agora, com a saída certa para cada caso."""
    row = loader.declared_by(name, "exporters")
    if row is None:
        known = ", ".join(registry.exporter_names()) or "nenhum"
        return (
            f"Não há exporter {name} instalado. Exporters habilitados: {known}. "
            "Veja plugins --action list e docs/SDK.md."
        )
    if row.get("status") != "enabled":
        reason = guard.without_prefix(row["id"], (row.get("reason") or "").strip()).rstrip(" .")
        detail = f": {reason}" if reason else ""
        hint = loader.status_hint(row, f"Habilite com plugins --action enable --id {row['id']}.")
        return f"Plugin {row['id']} está {row['status']}{detail}. {hint}"
    return f"Plugin {row['id']} não registrou o exporter {name}: rode plugins --action check --id {row['id']}."


def _unsafe(exc, name, store):
    template = _UNSAFE.get(exc.reason, "{name} não pôde ser copiado ({type_name}): repita.")
    return ValueError(template.format(name=name, store=store, type_name=exc.type_name or "OSError"))


def copy_plugin(registry, source, dest):
    """Mídia de resolvedor: reabre o acerto dentro de `permissions.paths` do plugin dono,
    confere `st_dev`/`st_ino`/tamanho no descritor e copia dele (nunca hardlink nem chmod)."""
    store = source["store"]
    name = guard.plain_line(Path(source["path"]).name or "arquivo", limit=120)
    roots = [
        Path(root) for resolver in registry.owned_by(store)["resolver"] for root in registry.resolver_roots(resolver)
    ]
    size = source["st_size"]
    try:
        fd, _ = safe_copy.recheck(source["path"], source["st_dev"], source["st_ino"], size, roots=roots)
    except safe_copy.UnsafeFileError as exc:
        raise _unsafe(exc, name, store) from None
    try:
        copied = safe_copy.copy_from_fd(fd, dest, cap=size)
    except safe_copy.UnsafeFileError as exc:
        raise _unsafe(exc, name, store) from None
    finally:
        os.close(fd)
    if copied != size:
        Path(dest).unlink(missing_ok=True)
        raise ValueError(_CHANGED.format(name=name))


def _check_output(owner, name, validated, marker_base):
    """O que vai para o disco: nenhum nome do core entre os arquivos do plugin e nenhum caminho local."""
    reserved = sorted(path for path in validated.files if path.casefold() in export_folder.RESERVED_NAMES)
    if reserved:
        raise ValueError(
            f"Plugin {owner}: o exportador {name} devolveu {', '.join(reserved)}, nome reservado do get-brolls "
            "na pasta do export. Nada foi gravado."
        )
    leaks = find_local_paths(validated.files, "files") + find_local_paths(marker_base, "marcador")
    if leaks:
        raise ValueError(
            f"Plugin {owner}: o exportador {name} escreveu um caminho local em {', '.join(leaks[:5])}. "
            "Um export é para compartilhar e não leva caminho do disco. Nada foi gravado."
        )


def _os_error(exc, project):
    """Erro do sistema de arquivos em pt-BR, sem `[Errno N]` nem caminho absoluto."""
    reason = _OS_REASONS.get(exc.errno) or exc.strerror or type(exc).__name__
    where = "um arquivo do export"
    if isinstance(exc.filename, (str, bytes)):
        path = Path(os.path.realpath(os.fsdecode(exc.filename)))
        where = path.relative_to(project).as_posix() if path.is_relative_to(project) else path.name
    return OSError(
        f"Não consegui ler ou gravar {where} ({reason}). Confira as permissões e o espaço em disco e repita o export."
    )


def _predicted(source):
    if source["method"] == "hardlink" and copies_forced():
        return "copy"
    return _PREDICTED[source["method"]]


def _megabytes(size):
    return f"{size / 1_000_000:.1f}".replace(".", ",")


def _summary(out, files, written):
    cloned = any(m["method"] in _CLONE_METHODS for m in written["media"])
    amount = _megabytes(written["copied_bytes"])
    copied = f"até {amount} MB copiados" if cloned else f"{amount} MB copiados de fato"
    line = f"Export {written['number']} em {out}: {len(files)} arquivo(s), {len(written['media'])} mídia(s) ({copied})."
    return line + (f" Abra {out}/EXPORT.md." if "EXPORT.md" in files else "")


def run(args):
    name = args.to
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("--to espera o nome de um exporter (minúsculas, números e _), ex.: --to hyperframes.")
    project = Path(args.project).expanduser().resolve()
    try:
        return _run(args, name, project)
    except OSError as exc:
        raise _os_error(exc, project) from exc


def _run(args, name, project):
    ready = export_gates.check(project)
    registry = get_registry()
    if registry.exporter(name) is None:
        raise ValueError(_unavailable(registry, name))
    owner = registry.owner("exporter", name) or CORE
    root = export_folder.exporter_root(project, name)
    number = export_folder.next_number(root)
    out_dir = f"{export_folder.EXPORTS_DIR}/{name}/{export_folder.folder_name(number)}"
    resolve_media = functools.partial(resolve_with_plugins, registry)
    plan, sources = export_plan.build(project, ready["plan"], ready["items"], out_dir, resolve_media=resolve_media)
    warnings = list(plan["warnings"])
    warnings += export_folder.changed_sources(export_folder.latest_marker(root), sources, project, number=number)
    validated = run_exporter(registry, name, plan, OPTIONS)
    checked = export_place.check_requests(validated.media, plan["media"], sources)
    row = registry.plugins.get(owner) or {}
    marker_base = {
        "export_version": plan["export_version"], "exporter": name, "plugin": owner,
        "plugin_version": row.get("version"), "getbrolls_version": __version__, "created": plan["generated_at"],
    }  # fmt: skip
    _check_output(owner, name, validated, marker_base)
    envelope = {
        "exporter": name, "plugin": owner, "out": out_dir, "number": export_folder.folder_name(number),
        "latest": False, "files": sorted(validated.files), "media": [],
        "notes": [note_line(owner, note) for note in validated.notes], "warnings": warnings,
        "dry_run": bool(args.dry_run),
    }  # fmt: skip
    if args.dry_run:
        media = [{"media_id": m, "dest": d, "method": _predicted(sources[m])} for m, d in checked]
        line = (
            f"Ensaio: {len(validated.files)} arquivo(s) e {len(media)} mídia(s) iriam para {out_dir}; nada foi gravado."
        )
        return {**envelope, "media": media, "summary": {"line": line}}
    # O código do plugin rodou desde a primeira conferência: `exports/` pode ter virado link.
    root = export_folder.exporter_root(project, name)
    content = {"files": validated.files, "placements": [(m, d, sources[m]) for m, d in checked]}
    place = functools.partial(export_place.place, copy_plugin=functools.partial(copy_plugin, registry))
    written = export_folder.write_export(root, number, content, marker_base, place)
    out = f"{export_folder.EXPORTS_DIR}/{name}/{written['number']}"
    latest_ok = not any(w.endswith(": não mexi nele") for w in written["warnings"])
    return {
        **envelope, "out": out, "number": written["number"], "latest": latest_ok, "media": written["media"],
        "warnings": warnings + written["warnings"], "summary": {"line": _summary(out, validated.files, written)},
    }  # fmt: skip
