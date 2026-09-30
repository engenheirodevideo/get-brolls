"""`gb export --to <exporter>`: um roteiro revisado vira um projeto de edição numa pasta numerada.

Fluxo: portões (`export_gates`) → exporter do registro (só plugin habilitado) →
plano de export (`export_plan.build`, com os resolvedores de plugin injetados) →
`run_exporter` do SDK (valida o que o plugin devolveu) → pedidos de mídia conferidos
contra o plano → `--dry-run` para aqui; senão a pasta numerada nova é gravada
(`export_folder.write_export`, mídia por `export_place.place`), com o plano entregue
ao exporter em `getbrolls-plan.json` e o id do projeto (só lido, nunca criado) no marcador. Nada passa por
`sync_formats`, `recover` ou `entrega/`.
"""

import contextlib
import errno
import functools
import json
import logging
import os
import re
import unicodedata
import urllib.parse
from pathlib import Path, PureWindowsPath

from . import __version__, assets, export_folder, export_gates, export_place, export_plan, logs
from .delivery import copies_forced
from .ledger import existing_project_id
from .rules import home_dir
from .sdk import guard, loader, safe_copy
from .sdk.contracts import CORE, NAME_RE, RESOLVER_KINDS
from .sdk.exporters import find_local_paths, note_line, run_exporter
from .sdk.files import walk_leaves
from .sdk.registry import get_registry
from .sdk.resolvers import resolve_with_plugins

log = logs.get("export")

OPTIONS = {"args": {}}
# Método que o dry-run mostra para cada fonte (o real pode cair para cópia).
_PREDICTED = {"hardlink": "hardlink", "clone": "clone", "plugin": "copy"}
# Clone pelo `cp`: num disco que não clona ele copia tudo, então o resumo diz "até N MB".
_CLONE_METHODS = ("clone", "reflink-auto")
# Compartilhado com `roteiro_commands` (mesmos códigos, mesmo texto): ver `_OS_REASONS_COMMON`.
_OS_REASONS_COMMON = {
    errno.EACCES: "sem permissão",
    errno.EPERM: "sem permissão",
    errno.EROFS: "o disco está somente leitura",
    errno.ENOSPC: "o disco está cheio",
}
_OS_REASONS = {
    **_OS_REASONS_COMMON,
    errno.EEXIST: "o arquivo já existe",
    errno.ENOENT: "o arquivo ou a pasta não existe",
    errno.ENOTDIR: "um trecho do caminho não é uma pasta",
}
_CHANGED = "{name} mudou durante o export: repita."
_UNSAFE = {
    safe_copy.CHANGED: _CHANGED,
    safe_copy.TOO_BIG: _CHANGED,
    # Aberto sem seguir link: o acerto trocado por um link (ou apagado) não abre.
    safe_copy.OPEN_FAILED: _CHANGED,
    safe_copy.NOT_REGULAR: "{name} deixou de ser um arquivo durante o export: repita.",
    safe_copy.LINKED: (
        "{name} tem mais de um nome no disco (hardlink): o export não copia esse arquivo do plugin {store}."
    ),
    safe_copy.OUTSIDE: "{name} está fora de permissions.paths do plugin {store}: o export não copia esse arquivo.",
    safe_copy.TARGET_EXISTS: "{name} já existe no export em montagem: repita o export.",
    safe_copy.TARGET_FAILED: (
        "Não consegui criar {name} no export em montagem: confira as permissões e o espaço em disco e repita."
    ),
}
# Mídia de resolvedor fica legível como a de clone e cópia (o `copy_from_fd` cria 0o600 por padrão).
_PLUGIN_MEDIA_MODE = 0o644


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
    template = _UNSAFE.get(exc.reason, "{name} não pôde ser copiado: repita.")
    return ValueError(template.format(name=name, store=store))


def copy_plugin(registry, source, dest):
    """Mídia de resolvedor: reabre o acerto dentro das raízes do resolvedor que o achou,
    confere `st_dev`/`st_ino`/tamanho no descritor e copia dele (nunca hardlink nem chmod)."""
    store = source["store"]
    name = guard.plain_line(Path(source["path"]).name or "arquivo", limit=120)
    roots = [Path(root) for root in registry.resolver_roots(source["resolver"])]
    size = source["st_size"]
    try:
        fd, _ = safe_copy.recheck(source["path"], source["st_dev"], source["st_ino"], size, roots=roots)
    except safe_copy.UnsafeFileError as exc:
        raise _unsafe(exc, name, store) from None
    try:
        copied = safe_copy.copy_from_fd(fd, dest, cap=size, mode=_PLUGIN_MEDIA_MODE)
    except safe_copy.UnsafeFileError as exc:
        raise _unsafe(exc, name, store) from None
    finally:
        os.close(fd)
    if copied != size:
        Path(dest).unlink(missing_ok=True)
        raise ValueError(_CHANGED.format(name=name))


def _windows_name(function, text):
    """O que `GetShortPathNameW`/`GetLongPathNameW` devolve para `text`; None quando o sistema não responde."""
    import ctypes  # só chega aqui no Windows ou num teste com a API de mentira

    size = function(text, None, 0)
    if not size:
        return None
    buffer = ctypes.create_unicode_buffer(size)
    written = function(text, buffer, size)
    return buffer.value if 0 < written < size else None


def _windows_spellings(path, platform=None, kernel32=None):
    """No Windows, as outras grafias de `path` com nome curto (8.3, como `RUNNER~1`); fora dele, nenhuma.

    O `realpath` devolve o nome longo, mas o TEMP e o que a pessoa digita podem vir com o nome curto
    numa pasta de cima e o longo no resto (`C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\gb-x`): vale o
    nome longo inteiro e, para cada pasta de cima, ela no nome curto seguida do resto como está."""
    if (platform or os.name) != "nt":
        return []
    if kernel32 is None:
        import ctypes  # só no Windows

        kernel32 = ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]  # só existe no Windows
    long_name = _windows_name(kernel32.GetLongPathNameW, str(path)) or str(path)
    pure = PureWindowsPath(long_name)
    found = [long_name]
    for folder in (pure, *pure.parents):
        short = _windows_name(kernel32.GetShortPathNameW, str(folder))
        if short and short != str(folder):
            found.append(str(PureWindowsPath(short) / pure.relative_to(folder)))
    return found


def _machine_paths(project, registry, sources):
    """`{caminho: (rótulo, marca)}` dos caminhos concretos desta máquina que o core conhece:
    projeto, fontes de mídia do plano, raízes de `permissions.paths`, GB_HOME e pasta pessoal."""
    found = {}

    def add(path, label, tag):
        real = os.path.realpath(path)
        # No macOS, /var e /tmp são links para /private/...: as duas grafias valem.
        alias = real[len("/private") :] if real.startswith(("/private/var/", "/private/tmp/")) else ""
        texts = [str(path), real, alias]
        # No Windows, o nome curto (8.3) de qualquer pasta do caminho também vale.
        for name in dict.fromkeys((str(path), real)):
            texts.extend(_windows_spellings(name))
        for text in texts:
            trimmed = text.rstrip("/\\")
            if len(trimmed) > 1:
                found.setdefault(trimmed, (label, tag))

    add(project, "a pasta do projeto", "<projeto>")
    for source in sources.values():
        add(source["path"], "o caminho de uma mídia", "<mídia>")
    for kind in RESOLVER_KINDS:
        for _, spec in registry.resolvers_for(kind):
            for root in registry.resolver_roots(spec.name):
                add(root, "uma pasta de permissions.paths", "<permissions.paths>")
    _, client_root = assets.project_client(project)
    if client_root is not None:
        add(client_root, "a pasta do cliente", "<cliente>")
    add(home_dir(), "a pasta do get-brolls (GB_HOME)", "<GB_HOME>")
    with contextlib.suppress(RuntimeError):
        add(Path.home(), "a pasta pessoal", "<pasta pessoal>")
    # O mais longo primeiro: o projeto dentro da pasta pessoal é "a pasta do projeto".
    return dict(sorted(found.items(), key=lambda pair: len(pair[0]), reverse=True))


def _path_re(path):
    """O caminho inteiro, sem caixa: não casa dentro de um nome maior (`/root` em `github.com/rootless`,
    `/srv/bo` em `/srv/bob`); o ponto final de uma frase logo depois ainda casa, e um "." logo antes
    (caminho relativo como `../../<pasta>/x`) não blinda mais o caminho."""
    return re.compile(r"(?<![\w~-])" + re.escape(path) + r"(?![\w-]|\.\w)", re.IGNORECASE)


# Percent-encoding decodificado até estabilizar, no máximo esta quantidade de vezes (`%252F` → `%2F` → `/`).
_UNQUOTE_ROUNDS = 3
_REPEATED_SLASHES = re.compile(r"/{2,}")


def _spellings(text):
    """O texto cru e as grafias normalizadas dele, cada passo sobre o anterior: `\\/` → `/`; `\\\\` e `\\` → `/`;
    percent-decode repetido; NFC; barras repetidas colapsadas. Só para comparar: nada disso vai para o disco."""
    forms = [text]
    current = text.replace("\\/", "/")
    forms.append(current)
    current = current.replace("\\\\", "/").replace("\\", "/")
    forms.append(current)
    for _ in range(_UNQUOTE_ROUNDS):
        decoded = urllib.parse.unquote(current)
        if decoded == current:
            break
        current = decoded.replace("\\", "/")  # `%5C` decodificado também vira barra
    forms.append(current)
    current = unicodedata.normalize("NFC", current)
    forms.append(current)
    forms.append(_REPEATED_SLASHES.sub("/", current))
    return list(dict.fromkeys(forms))


# `re.IGNORECASE` iguala İ e ı ao i; o filtro rápido de `_first_label` precisa igualar também.
_DOTTED_I = str.maketrans({"\u0130": "i", "\u0131": "i"})


def _fold(text):
    """Chave do filtro rápido: sem caixa. Mais frouxa que o `re.IGNORECASE`, nunca mais estrita."""
    return text.translate(_DOTTED_I).casefold()


def _patterns(machine):
    """`[(regex, rótulo, marca, chave)]` de cada grafia de cada caminho conhecido, na ordem de `machine`
    (o mais longo primeiro)."""
    patterns = []
    for path, (label, tag) in machine.items():
        patterns.extend((_path_re(form), label, tag, _fold(form)) for form in _spellings(path) if len(form) > 1)
    return patterns


def _first_label(text, patterns):
    """Rótulo do primeiro caminho conhecido que aparece em qualquer grafia de `text`, ou None.

    A regex (com a fronteira) só roda onde a busca de substring, bem mais barata, já achou o caminho."""
    forms = [(form, _fold(form)) for form in _spellings(text)]
    for pattern, label, _, key in patterns:
        if any(key in folded and pattern.search(form) for form, folded in forms):
            return label
    return None


def _machine_hits(value, machine, where):
    """`[(posição, rótulo)]` de cada texto de `value` que contém um caminho de `machine` (inteiro, sem caixa),
    na grafia crua ou em qualquer grafia normalizada (`_spellings`)."""
    patterns = _patterns(machine)
    hits = []
    for position, item in walk_leaves(value, where, keys_of_any_type=True):
        if type(item) is str:
            label = _first_label(item, patterns)
            if label is not None:
                hits.append((position, label))
    return sorted(hits)


def _scrub(text, machine):
    """Troca cada caminho concreto desta máquina pela marca dele (`<projeto>`, `<pasta pessoal>`…).

    Sobrou caminho numa grafia alternativa (URI, percent-encoded, NFD, `\\`): o texto sai na grafia
    normalizada, com a marca no lugar do caminho."""
    for path, (_, tag) in machine.items():
        text = _path_re(path).sub(tag, text)
    patterns = _patterns(machine)
    if _first_label(text, patterns) is None:
        return text
    normalized = _spellings(text)[-1]
    for pattern, _, tag, _ in patterns:
        normalized = pattern.sub(tag, normalized)
    return normalized


def _check_output(owner, name, validated, core_files, machine):
    """O que vai para o disco: nenhum nome do core entre os arquivos do plugin e nenhum caminho
    desta máquina (recusa); texto só com cara de caminho vira um aviso (costuma vir do roteiro).

    `core_files` = `(marcador, plano)`: o que o core grava ao lado passa pela mesma conferência."""
    marker_base, plan = core_files
    reserved = sorted(path for path in validated.files if path.casefold() in export_folder.RESERVED_NAMES)
    if reserved:
        raise ValueError(
            f"Plugin {owner}: o exportador {name} devolveu {', '.join(reserved)}, nome reservado do get-brolls "
            "na pasta do export. Nada foi gravado."
        )
    plan_label = export_folder.PLAN_FILE
    hits = (
        _machine_hits(validated.files, machine, "files")
        + _machine_hits(marker_base, machine, "marcador")
        + _machine_hits(plan, machine, plan_label)
    )
    if hits:
        labels = ", ".join(dict.fromkeys(label for _, label in hits))
        positions = ", ".join(dict.fromkeys(position for position, _ in hits[:5]))
        raise ValueError(
            f"O export ia gravar um caminho desta máquina ({labels}) em {positions}. Um export é para "
            "compartilhar e não leva caminho do disco: se o caminho está no roteiro, tire-o do texto; senão, "
            f"quem o escreveu foi o exportador {name} (plugin {owner}). Nada foi gravado."
        )
    looks = (
        find_local_paths(validated.files, "files")
        + find_local_paths(marker_base, "marcador")
        + find_local_paths(plan, plan_label)
    )
    if not looks:
        return []
    warning = (
        f"{', '.join(looks[:5])}: texto com cara de caminho de disco (como ~/…, C:\\… ou /tmp/…); "
        "ficou como está no export: confira antes de compartilhar"
    )
    return [warning]


def _os_error(exc, project):
    """Erro do sistema de arquivos em pt-BR, sem `[Errno N]` nem caminho absoluto."""
    reason = _OS_REASONS.get(exc.errno) or (
        f"erro do sistema: {exc.strerror}" if exc.strerror else "erro do sistema sem descrição"
    )
    where = "um arquivo do export"
    if isinstance(exc.filename, (str, bytes)):
        path = Path(os.path.realpath(os.fsdecode(exc.filename)))
        where = path.relative_to(project).as_posix() if path.is_relative_to(project) else path.name
    return OSError(  # pylint: disable=line-too-long  # mensagem em pt-BR; ruff format mantém numa linha só
        f"Não consegui ler ou gravar {where} ({reason}). Confira as permissões e o espaço em disco e repita o export."
    )


def _predicted(source):
    if source["method"] == "hardlink" and copies_forced():
        return "copy"
    if source["method"] == "clone" and not export_place.can_clone():
        return "copy"  # o mesmo que o `place` fará (Windows, Linux sem `cp`)
    return _PREDICTED[source["method"]]


def _megabytes(size):
    return f"{size / 1_000_000:.1f}".replace(".", ",")


def _summary(out, files, written):
    cloned = any(m["method"] in _CLONE_METHODS for m in written["media"])
    amount = _megabytes(written["copied_bytes"])
    copied = f"até {amount} MB copiados" if cloned else f"{amount} MB copiados de fato"
    # pylint: disable-next=line-too-long  # mensagem em pt-BR; ruff format mantém numa linha só
    line = f"Export {written['number']} em {out}: {len(files)} arquivo(s), {len(written['media'])} mídia(s) ({copied})."
    return line + (f" Abra {out}/EXPORT.md." if "EXPORT.md" in files else "")


def run(args):
    """Ponto de entrada de `gb export --to <exporter>`: valida o nome e delega para `_run`."""
    name = args.to
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("--to espera o nome de um exporter (minúsculas, números e _), ex.: --to hyperframes.")
    project = Path(args.project).expanduser().resolve()
    try:
        return _run(args, name, project)
    except OSError as exc:
        raise _os_error(exc, project) from exc


def _plan_text(plan):
    """O plano em JSON pronto para gravar em `getbrolls-plan.json` (com quebra de linha no fim)."""
    return json.dumps(plan, ensure_ascii=False, indent=2) + "\n"


def _phase_target(ctx):
    """1ª fase de `_run`: portões, exporter e pasta/número do próximo export, gravados no `ctx`."""
    project, name = ctx["project"], ctx["name"]
    ready = export_gates.check(project)
    registry = get_registry()
    if registry.exporter(name) is None:
        raise ValueError(_unavailable(registry, name))
    root = export_folder.exporter_root(project, name)
    number = export_folder.next_number(root)
    ctx.update(
        ready=ready,
        registry=registry,
        owner=registry.owner("exporter", name) or CORE,
        root=root,
        number=number,
        out_dir=f"{export_folder.EXPORTS_DIR}/{name}/{export_folder.folder_name(number)}",
    )


def _phase_plan(ctx):
    """2ª fase de `_run`: plano de export com os resolvedores injetados, avisos já com a marca da máquina."""
    project, registry, ready = ctx["project"], ctx["registry"], ctx["ready"]
    resolve_media = functools.partial(resolve_with_plugins, registry)
    plan, sources = export_plan.build(
        project,
        ready["plan"],
        ready["items"],
        ctx["out_dir"],
        resolve_media=resolve_media,
        project_id=ready["project_id"],
    )
    machine = _machine_paths(project, registry, sources)
    # Aviso escrito por resolvedor de plugin pode trazer caminho desta máquina: sai com a marca, não recusa.
    plan["warnings"] = list(dict.fromkeys(_scrub(warning, machine) for warning in plan["warnings"]))
    ctx.update(plan=plan, sources=sources, machine=machine)


def _phase_validate(ctx):
    """3ª fase de `_run`: roda o exporter, confere as mídias contra o plano e monta o marcador base."""
    registry, name, plan, sources = ctx["registry"], ctx["name"], ctx["plan"], ctx["sources"]
    warnings = list(plan["warnings"])
    warnings += export_folder.changed_sources(
        export_folder.latest_marker(ctx["root"]), sources, ctx["project"], number=ctx["number"]
    )
    validated = run_exporter(registry, name, plan, OPTIONS)
    checked = export_place.check_requests(validated.media, plan["media"], sources)
    owner = ctx["owner"]
    row = registry.plugins.get(owner) or {}
    marker_base = {
        "export_version": plan["export_version"], "exporter": name, "plugin": owner,
        "plugin_version": row.get("version"), "getbrolls_version": __version__, "created": plan["generated_at"],
        "projeto_id": plan["meta"]["projeto_id"], "cliente": plan["meta"]["cliente"],
        "direcao": plan["meta"]["direcao"],
    }  # fmt: skip
    warnings += _check_output(owner, name, validated, (marker_base, plan), ctx["machine"])
    ctx.update(warnings=warnings, validated=validated, checked=checked, marker_base=marker_base)


def _phase_envelope(ctx):
    """4ª fase de `_run`: resposta base do export, antes de saber se é ensaio (`dry-run`) ou gravação de verdade."""
    validated = ctx["validated"]
    ctx["envelope"] = {
        "exporter": ctx["name"], "plugin": ctx["owner"], "out": ctx["out_dir"],
        "number": export_folder.folder_name(ctx["number"]), "latest": False,
        "files": sorted(validated.files), "media": [],
        "notes": [note_line(ctx["owner"], _scrub(note, ctx["machine"])) for note in validated.notes],
        "warnings": ctx["warnings"], "dry_run": bool(ctx["args"].dry_run),
    }  # fmt: skip


def _dry_run_result(ctx):
    """Resposta do `--dry-run`: só o que iria acontecer, nada gravado."""
    sources, checked, validated = ctx["sources"], ctx["checked"], ctx["validated"]
    media = [{"media_id": m, "dest": d, "method": _predicted(sources[m])} for m, d in checked]
    line = (
        f"Ensaio: {len(validated.files)} arquivo(s) e {len(media)} mídia(s) "
        f"iriam para {ctx['out_dir']}; nada foi gravado."
    )
    return {**ctx["envelope"], "media": media, "summary": {"line": line}}


def _write_result(ctx):
    """Grava a pasta numerada do export e devolve a resposta com o resultado de verdade."""
    project, name = ctx["project"], ctx["name"]
    validated = ctx["validated"]
    # O código do plugin rodou desde a primeira conferência: `exports/` pode ter virado link.
    root = export_folder.exporter_root(project, name)
    content = {
        "files": validated.files,
        "plan": _plan_text(ctx["plan"]),
        "placements": [(m, d, ctx["sources"][m]) for m, d in ctx["checked"]],
    }
    place = functools.partial(export_place.place, copy_plugin=functools.partial(copy_plugin, ctx["registry"]))
    written = export_folder.write_export(root, ctx["number"], content, ctx["marker_base"], place)
    out = f"{export_folder.EXPORTS_DIR}/{name}/{written['number']}"
    logs.event(
        log,
        logging.INFO,
        "export_done",
        # Relê e valida o manifesto (não confia no campo do plano): mesma regra de
        # `roteiro_review`/`roteiro_sync`, nunca loga um id que não é uuid.
        projeto_id=existing_project_id(project),
        exporter=name,
        plugin=ctx["owner"],
        number=written["number"],
        files=len(validated.files),
        media=len(written["media"]),
        bytes=written["copied_bytes"],
    )
    return {
        **ctx["envelope"], "out": out, "number": written["number"], "latest": written["latest"],
        "media": written["media"], "warnings": ctx["warnings"] + written["warnings"],
        "summary": {"line": _summary(out, validated.files, written)},
    }  # fmt: skip


def _run(args, name, project):
    ctx = {"args": args, "name": name, "project": project}
    _phase_target(ctx)
    _phase_plan(ctx)
    _phase_validate(ctx)
    _phase_envelope(ctx)
    if args.dry_run:
        return _dry_run_result(ctx)
    return _write_result(ctx)
