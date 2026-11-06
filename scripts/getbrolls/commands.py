"""Existing workflow command handlers; CLI parsing and reporting live separately."""

# pylint: disable=cyclic-import
# `cyclic-import`: `_execute_toolchain` importa `cli_parser.build_parser` e `capabilities` de forma tardia (só roda
# com tudo carregado); o pylint só permite suprimir R0401 no módulo.

import contextlib
import copy
import json
import logging
import os
import re
from pathlib import Path

from . import _paths, effects, labels, logs
from .brief_report import brief_report
from .candidate_commands import (  # noqa: F401 -- reexported: importers and tests keep reading these here; pylint: disable=unused-import
    _execute_candidate_command,
    mark_rejected,
    reject_all,
)
from .doctor import (  # pylint: disable=unused-import
    _doctor_plugin_inventory,  # noqa: F401 -- reexported: importers and tests keep reading this here
    _execute_providers_or_doctor,
)
from .fetch_commands import (  # pylint: disable=unused-import
    _verify_one_clip,
    preview_files,  # noqa: F401 -- reexported: importers and tests keep reading this here
)
from .inspect_commands import (  # noqa: F401 -- reexported: importers and tests keep reading these here; pylint: disable=unused-import
    clamp_windows,
    inspect_source,
    inspect_summary,
    inspect_warnings,
    scan_candidate,
)
from .ledger import Ledger, atomic_write
from .presets import PERMIT_PRESETS  # noqa: F401 -- tests importam daqui; pylint: disable=unused-import
from .queue import execute as queue_execute
from .queue import hint as queue_hint
from .readiness import (  # noqa: F401 -- reexported: importers and tests keep reading these here; pylint: disable=unused-import
    PLUGIN_PROBLEM_STATUSES,
    REQUIRED_EXECUTABLES,
    SYSTEM_TOOLS,
    _local_playwright,
    doctor_plugin_problems,
    doctor_summary,
)
from .rendering import render
from .runtime import record_warning
from .search_commands import (  # noqa: F401 -- reexported: importers and tests keep reading these here; pylint: disable=unused-import
    _beat_search_names,
    _execute_resolve,
    _execute_search,
    _resolve_build_candidate,
)
from .status_report import (  # noqa: F401 -- reexported: importers and tests keep reading these here; pylint: disable=unused-import
    _flow_next,
    _flow_state,
    _preview_is_image,
    _reference_only,
    _step_candidates,
    _uninspected,
    brief_state,
    deliver_report,
    record_empty_searches,
    status_journal,
    status_references,
    status_report,
)
from .summaries import (  # noqa: F401 -- reexported: importers and tests keep reading these here; pylint: disable=unused-import
    EMPTY_STORYBOARD,
    FLOW_SUMMARIES,
    STATUS_LADDER,
    STATUS_STAGES,
    _has_preview,
    _plugin_reference_note,
    provider_error_text,
    status_next,
    summary_next,
    with_summary,
)

_log = logs.get("commands")

# Comandos que só consultam o projeto: `inspect` grava no máximo `media.duration_s`
# e `references` não grava nada — nenhum dos dois muda formato nem aprovação.
READ_ONLY_CONSULTS = ("references", "inspect")
# Comandos com `--dry-run`: o ensaio não grava nada, nem pelo portão de formato
# (`sync_formats`) nem pela recuperação de uma gravação interrompida.
DRY_RUN_COMMANDS = ("deliver", "search")


def _rules_block(text):
    """O único bloco ```json de um RULES.md: (texto cru, dados)."""
    blocks = re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if len(blocks) != 1:
        raise ValueError("Modelo de RULES.md precisa de exatamente um bloco JSON.")
    return blocks[0], json.loads(blocks[0])


def _missing_copyright(video_format):
    """Recusa de `init-rules --force` num RULES.md sem `copyright`, com o comando pronto."""
    shown = f" --format {video_format}" if video_format else ""
    return (
        'Este RULES.md não tem o bloco "copyright", e todo RULES.md de projeto precisa dele: '
        "o do RULES.md global não vale aqui. Nada foi gravado. Escolha o modo junto: "
        f"`init-rules --force --mode per_item_evidence{shown} --project PROJETO` (você confere "
        'fonte por fonte) ou `--mode user_declaration --responsible "SEU NOME" --declaration "..."` '
        "(você assume a responsabilidade)."
    )


def rules_from_flags(  # noqa: PLR0913, PLR0917 - one parameter per init-rules flag, plus the template
    template, mode, responsible, declaration, video_format=None, model=None
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Reescreve só o bloco ```json do modelo, preservando toda a prosa do arquivo.

    `--format` mexe só em `video_format`; `copyright` só muda com `--mode`,
    `--responsible` ou `--declaration`. Um RULES.md de camada pode não ter
    `copyright`: com uma dessas flags, ele nasce do bloco de `model` (o modelo da
    instalação); sem nenhuma, a regravação é recusada, porque `copyright` nunca vem
    do global e o arquivo continuaria sem carregar.
    """
    raw, data = _rules_block(template)
    if video_format is not None:
        # Único campo que `--format` toca: o resto das regras continua do jeito que a
        # pessoa deixou. Era esta a lacuna que fazia o conflito brief×rules travar.
        data["video_format"] = video_format
    if any(value is not None for value in (mode, responsible, declaration)):
        if not isinstance(data.get("copyright"), dict):
            data["copyright"] = copy.deepcopy(_rules_block(model if model is not None else template)[1]["copyright"])
        rights = data["copyright"]
        rights["mode"] = mode or ("user_declaration" if (responsible or declaration) else rights.get("mode"))
        if responsible is not None:
            rights["responsible_person"] = responsible.strip() or None
        if declaration is not None:
            rights["declaration"] = declaration.strip() or None
        if rights["mode"] == "user_declaration" and not (
            (rights.get("responsible_person") or "").strip() and (rights.get("declaration") or "").strip()
        ):
            raise ValueError("Modo user_declaration exige --responsible NOME e --declaration TEXTO.")
    if not isinstance(data.get("copyright"), dict):
        raise ValueError(  # noqa: TRY004 - contrato da CLI: dado inválido é ValueError (INVALID_DATA)
            _missing_copyright(video_format)
        )
    return (
        template.replace(raw, json.dumps(data, ensure_ascii=False, indent=2), 1),
        data.get("copyright"),
    )


def library_command(args):
    """`learn` e `library`: memória editorial entre projetos, sem direito junto."""
    from getbrolls import library

    if args.command == "library":
        if not 1 <= args.limit <= 20:  # noqa: PLR2004 - matches the "--limit entre 1 e 20" message below
            raise ValueError("Use --limit entre 1 e 20.")
        return library.search(args.search, limit=args.limit, project=args.project)
    given = [
        flag
        for flag, value in (
            ("--query", args.query),
            ("--preference", args.preference),
            ("--from-candidate", args.from_candidate),
        )
        if value
    ]
    if len(given) != 1:
        raise ValueError(
            "Diga o que aprender, uma coisa por vez: --query (com --provider e "
            '--outcome), --preference "frase" ou --from-candidate ID.'
        )
    if args.query:
        return library.learn_query(args.query, args.provider, args.outcome, note=args.note, project=args.project)
    if args.preference:
        return library.learn_preference(args.preference, by=args.by, project=args.project)
    return library.learn_from_candidate(args.project, args.from_candidate, shot=args.shot)


# --- Audit-trail logging helpers -------------------------------------------
# Pure, defensive readers used only to build fields for `logs.event()` calls.
# Each one swallows its own failure and returns a safe default instead of
# raising, so a logging call site can never change command behavior.


def _rules_project_present(args):
    """Whether the target project already has a RULES.md, for the `config` event."""
    project = getattr(args, "project", None)
    if not project:
        return False
    try:
        # lenient: telemetry field, inside try/except OSError
        return (Path(project).expanduser() / "RULES.md").is_file()
    except OSError:
        return False


def _brief_present(args):
    """Whether the target project already has a BRIEF.md, for the `config` event."""
    project = getattr(args, "project", None)
    if not project:
        return False
    try:
        from getbrolls.brief import brief_path

        # lenient: telemetry field, inside try/except (ValueError, OSError)
        return brief_path(project).is_file()
    except (ValueError, OSError):
        return False


def _provider_keys_set():
    """Comma list of which provider API keys are SET in the environment, never their values."""
    names = (("pexels", "PEXELS_API_KEY"), ("pixabay", "PIXABAY_API_KEY"), ("youtube", "YOUTUBE_API_KEY"))
    return ",".join(name for name, key in names if os.environ.get(key))


_TOOLCHAIN_RUNNERS = {
    "profile": "getbrolls.profile",
    "client": "getbrolls.clients",
    "template": "getbrolls.templates",
    "recipe": "getbrolls.recipes",
    "setup": "getbrolls.bootstrap",
    "plugins": "getbrolls.sdk.cli",
    "x": "getbrolls.sdk.plugin_commands",
    "environment": "getbrolls.sdk.environments",
}


def _execute_toolchain(args):
    """`plugins`/`x` vão ao SDK, `setup` ao `bootstrap`, `capabilities` ao manifesto, `profile` ao perfil,
    `client` ao registro de clientes e `template` e `recipe` às receitas; nenhum toca projeto."""
    if args.command == "capabilities":
        from getbrolls import capabilities
        from getbrolls.cli_parser import build_parser

        return capabilities.describe(build_parser())
    # lenient: dynamic import of a toolchain runner module, never os or os.path
    return effects.import_module(_TOOLCHAIN_RUNNERS[args.command], operation="code.import").run(args)


def _execute_serve(args):
    """`serve`: sobe/derruba o servidor local do Storyboard em segundo plano, ou roda em primeiro plano."""
    from getbrolls import serve as serve_module

    port = getattr(args, "port", None) or serve_module.DEFAULT_PORT
    if getattr(args, "stop", False):
        return serve_module.stop(args.project)
    if getattr(args, "background", False):
        return serve_module.start_background(args.project, port)
    return serve_module.run(args.project, port)


def _execute_init(args):
    """`init`: cria um projeto novo de layout 1 (`project.json` e as pastas de trabalho)."""
    from getbrolls import project_init

    return project_init.run(args)


def _execute_migrate(args):
    """`migrate`: adota o layout 1 sem mover arquivos (`plan` só mostra, `apply` grava `project.json`)."""
    from getbrolls import migrate

    return migrate.run(args)


def _execute_init_rules(args):
    """`init-rules`: cria RULES.md a partir do template, ou regrava só o bloco JSON com --force."""
    dest = Path(args.project) / "RULES.md"
    effects.mkdir(dest.parent, parents=True, exist_ok=True)
    video_format = getattr(args, "video_format", None)
    has_flags = bool(args.mode or args.responsible or args.declaration or video_format)
    if _paths.strict_exists(dest) and not args.force:
        raise ValueError(
            "RULES.md já existe; edite sem sobrescrever suas regras. "
            "Use --force com --mode/--responsible/--declaration/--format para regravar só o bloco JSON."
        )
    if args.force and not has_flags:
        raise ValueError("--force só regrava o bloco JSON: informe --mode, --responsible, --declaration ou --format.")
    template = _paths.data_path("docs", "RULES.md")
    if has_flags:
        from getbrolls.brief import read_utf8

        # Regravar preserva o que o usuário já escolheu: a base é o arquivo dele.
        source = dest if _paths.strict_exists(dest) else template
        text, rights = rules_from_flags(
            read_utf8(source),
            args.mode,
            args.responsible,
            args.declaration,
            video_format=video_format,
            model=template.read_text(encoding="utf-8"),
        )
        # Temporário + troca: um disco cheio no meio nunca deixa o RULES.md truncado. RULES.md
        # pode ser link (nota do Obsidian): grava no arquivo de verdade e o link continua link.
        atomic_write(dest.resolve(), text)
        result = {"rules": str(dest), "copyright": rights}
        if video_format:
            result["video_format"] = video_format
        return result
    effects.copy_file(template, dest)
    return {"rules": str(dest)}


def _execute_status_or_queue(cmd, args):
    """`status`/`queue`: leem RULES.md; só `queue next` recusa um RULES.md inválido (ritmo é o que ele fixa)."""
    from getbrolls.rules import load_rules

    if cmd == "status":
        # Somente leitura: nada é criado, nem a árvore do projeto, nem pendências.
        project = Path(args.project).expanduser().resolve()
        if not _paths.strict_is_dir(project / "brolls"):
            # A pasta como a pessoa a digitou: resolvida, um link no caminho mudaria a grafia.
            typed = getattr(args, "project_typed", None) or args.project
            raise ValueError(f"Projeto não encontrado em {typed}; nenhum arquivo foi criado.")
        rules = None
        rules_error = None
        try:
            rules = load_rules(args.project)
        except (ValueError, OSError) as exc:
            rules_error = labels.reason(exc)
        from getbrolls import recipe_init

        recipe_init.record_lock_warnings(project)
        return status_report(Ledger(project, recover=False), rules, rules_error, queue_hint(project))
    rules = None
    try:
        rules = load_rules(args.project)
    except (ValueError, OSError) as exc:
        if getattr(args, "action", None) == "next":
            # Handing out an item paces the next request: with the rules unreadable, the faster default
            # pacing would be a silent loosening. An absent RULES.md is not an error (load_rules reads defaults).
            raise ValueError(
                f"RULES.md inválido; `queue next` não entrega item sem o ritmo dele: {labels.reason(exc)}"
            ) from exc
        record_warning("RULES_UNAVAILABLE", f"RULES.md ignorado para o ritmo: {labels.reason(exc)}")
    return queue_execute(args, rules)


def _execute_init_brief(args):
    """`init-brief`: cria BRIEF.md a partir do template, recusando sobrescrever um existente."""
    from getbrolls.brief import brief_path

    # O arquivo que conta é o mesmo que `brief` vai ler, GB_BRIEF_FILE incluído:
    # criar um BRIEF.md que ninguém lê seria pior que recusar.
    dest = brief_path(args.project)
    effects.mkdir(dest.parent, parents=True, exist_ok=True)
    if _paths.strict_exists(dest):
        raise ValueError(
            f"{labels.shown(dest)} já existe; edite o plano deste vídeo sem sobrescrever o que "
            "você já respondeu. Rode `brief --validate --project PROJETO` para conferi-lo."
        )
    template = _paths.data_path("docs", "BRIEF.md")
    from getbrolls.roteiro_frontmatter import is_roteiro, roteiro_format

    if not is_roteiro(args.project):
        effects.copy_file(template, dest)
        return {"brief": str(dest), "summary": {"line": "BRIEF.md criado do modelo: preencha o plano e os beats."}}
    # Com ROTEIRO.md do getbrolls, os beats nascem do sync e o formato é o do roteiro:
    # nada de beat de exemplo, nem "native" contra um roteiro em 9:16.
    text = template.read_text(encoding="utf-8")
    block = re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)[0]
    data = json.loads(block)
    video_format = roteiro_format(args.project)
    if video_format:
        data["video"]["delivery"]["format"] = video_format
    data["beats"] = []
    effects.write_text(dest, text.replace(block, json.dumps(data, ensure_ascii=False, indent=2), 1), encoding="utf-8")
    shown = f'"{video_format}"' if video_format else "o do modelo"
    return {
        "brief": str(dest),
        "summary": {
            "line": (
                f"BRIEF.md criado com o formato {shown} do ROTEIRO.md e sem beats: eles nascem do "
                "roteiro --action sync, depois da revisão."
            )
        },
    }


def _execute_roteiro_or_assets(args):
    """`roteiro`/`assets`: organização de conteúdo, sem portão de formato — o roteiro confere o
    aspecto contra RULES.md/BRIEF.md por conta própria."""
    from getbrolls import roteiro_commands

    return roteiro_commands.run(args)


def _execute_analysis(args):
    """`analysis`: índice de mídias por conteúdo e arquivos de `analysis/`, com trava própria."""
    from getbrolls import analysis

    return analysis.run(args)


def _execute_export(args):
    """`export`: delega ao exporter escolhido; sem portão de formato, sem `sync_formats`, sem
    recuperar o manifesto (os portões recusam gravação pendente antes disso)."""
    from getbrolls import export

    return export.run(args)


# Comandos administrativos que não tocam em `rules`/`ledger` do vídeo. `learn`/`library` são a
# biblioteca pessoal, que vive fora do projeto e não depende das regras dele.
_ADMIN_COMMAND_HANDLERS = {
    "init": _execute_init,
    "migrate": _execute_migrate,
    "init-rules": _execute_init_rules,
    "init-brief": _execute_init_brief,
    # Somente leitura, como status: orienta a coleta sem criar nada no projeto.
    "brief": brief_report,
    "learn": library_command,
    "library": library_command,
    "roteiro": _execute_roteiro_or_assets,
    "assets": _execute_roteiro_or_assets,
    "export": _execute_export,
    "analysis": _execute_analysis,
}


def _execute_config_free_command(cmd, args):
    """Comandos que não dependem de `rules`/`ledger`: status, queue e os administrativos."""
    if cmd in ("status", "queue"):
        return _execute_status_or_queue(cmd, args)
    handler = _ADMIN_COMMAND_HANDLERS.get(cmd)
    return handler(args) if handler else None


def _execute_read_only_project_command(cmd, args, config, rules, ledger):
    """Consultas e `deliver`: já com `rules`/`ledger` prontos, mas sem tocar num candidato só."""
    if cmd == "deliver":
        return deliver_report(ledger, rules, getattr(args, "dry_run", False))
    if cmd == "browser-plan":
        from getbrolls.browser import plan

        return plan(ledger, args.url, rules)
    if cmd == "references":
        from getbrolls.memory import load_references

        return load_references(ledger.root)
    if cmd == "inspect":
        return inspect_source(ledger, args, config)
    return None


def _execute_verify(args, rules, ledger):
    """`verify`: reconfere hash/decodificação de cada clipe coletado e tenta refazer entrega/."""
    checked = []
    # Every collected clip is checked even after one fails: stopping at the first
    # would leave a second altered clip marked as verified, and `deliver` would
    # ship it. The first failure is raised once the whole list has been flagged.
    first_failure = None
    for c in ledger.data["items"]:
        entry, failure = _verify_one_clip(ledger, c)
        if entry is not None:
            checked.append(entry)
        if failure is not None and first_failure is None:
            first_failure = failure
    if first_failure is not None:
        raise first_failure
    # `entrega/` é camada derivada: refazê-la nunca pode reprovar a conferência dos
    # arquivos canônicos. Se o sistema não deixar ligar/copiar, isso vira aviso.
    from getbrolls import delivery as delivery_module

    try:
        delivery_module.build_delivery(
            args.project, ledger=ledger, for_human=lambda: _flow_next(ledger, rules), rules=rules
        )
    except (ValueError, OSError) as exc:
        record_warning(
            "DELIVERY_LINK_FAILED",
            f"Os arquivos estão íntegros, mas não consegui refazer entrega/: {labels.reason(exc)}",
        )
    return {"verified": checked, "count": len(checked)}


def _execute_review_command(cmd, args, rules, ledger):
    """`import-review`/`review`/`verify`: publica ou confere a página, sem tocar num candidato só."""
    if cmd == "import-review":
        from getbrolls.review import import_review

        result = import_review(ledger, args.file, args.by, rules)
        render(ledger)
        return result
    if cmd == "review":
        page = render(ledger)
        if not any(_has_preview(c) for c in ledger.data["items"]):
            # Publicar uma página sem nada para decidir manda a pessoa abrir uma URL
            # à toa. O aviso aparece aqui e em `status`, no mesmo código.
            record_warning("EMPTY_STORYBOARD", EMPTY_STORYBOARD)
        return {"review": page}
    if cmd == "verify":
        return _execute_verify(args, rules, ledger)
    return None


def _execute_project_command(cmd, args, config, rules, ledger):
    """Comandos que já têm `rules`/`ledger` prontos e passaram pelo portão de formato."""
    read_only = _execute_read_only_project_command(cmd, args, config, rules, ledger)
    if read_only is not None:
        return read_only
    if cmd == "search":
        return _execute_search(args, rules, ledger)
    if cmd == "resolve":
        return _execute_resolve(args, rules, ledger)
    review_result = _execute_review_command(cmd, args, rules, ledger)
    if review_result is not None:
        return review_result
    return _execute_candidate_command(cmd, args, config, rules, ledger)


def execute(args):
    """Prepara ambiente/config e despacha o comando para o handler certo, em ordem de dependência crescente."""
    from getbrolls.config import settings
    from getbrolls.env_loader import load_environment

    # `UsageError` quando `--env-file` ou `GB_ENV_FILE` apontam para um arquivo que não existe,
    # ou quando o getbrolls.toml não vale para este comando (ver `profile.activate`).
    choice = load_environment(args, warn=True)
    config = settings()
    with contextlib.suppress(Exception):
        logs.event(
            _log,
            logging.DEBUG,
            "config",
            env_file_present=bool(getattr(args, "env_file", None)),
            env_file_source=choice.source,
            rules_project=_rules_project_present(args),
            brief_present=_brief_present(args),
            provider_keys=_provider_keys_set(),
        )
    if args.command == "capabilities" or args.command in _TOOLCHAIN_RUNNERS:
        return _execute_toolchain(args)
    if args.command in ("providers", "doctor"):
        return _execute_providers_or_doctor(args, config)
    cmd = args.command
    if cmd == "serve":
        return _execute_serve(args)
    config_free = _execute_config_free_command(cmd, args)
    if config_free is not None:
        return config_free
    from getbrolls.rules import load_rules, sync_formats

    rules = load_rules(args.project)
    if cmd == "rules":
        return rules
    dry_run = cmd in DRY_RUN_COMMANDS and bool(getattr(args, "dry_run", False))
    # `references` roda sem a trava do projeto (READ_ONLY_COMMANDS) e só lê
    # `references.json`, e o ensaio não grava: os dois abrem o manifesto sem criar
    # pastas nem concluir a gravação pendente de quem segura a trava.
    ledger = Ledger(args.project, recover=not (dry_run or cmd == "references"))
    # Consultas (`references`, `inspect`) não decidem nada sobre formato: como o
    # `status`, elas nunca podem ser barradas pelo portão de `--confirm-format-change`.
    # `deliver --dry-run` e `search --dry-run` são ensaio: não podem reescrever o
    # manifesto nem por tabela.
    if cmd not in READ_ONLY_CONSULTS and not dry_run:
        sync_formats(ledger, rules, confirm=getattr(args, "confirm_format_change", False))
    return _execute_project_command(cmd, args, config, rules, ledger)
