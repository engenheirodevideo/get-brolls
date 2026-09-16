"""Existing workflow command handlers; CLI parsing and reporting live separately."""

import json, sys, hashlib, shutil, os, re
from pathlib import Path
from . import __version__
from .models import candidate, set_segment, approve, require_fetch, signature
from .ledger import Ledger, digest
from .media import probe, cut, run
from .rendering import render

# Raiz real da skill/plugin: o comando sugerido não pode depender da pasta atual.
SKILL_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = (
    f'bash "{SKILL_ROOT / "scripts" / "install.sh"}" '
    f'(ou "{SKILL_ROOT / "scripts" / "install.ps1"}" no Windows)'
)
SYSTEM_TOOLS = "instale pelo gerenciador do sistema; veja GUIDE.md#instalação"

# Executável obrigatório → (comando que resolve, impacto real da ausência).
REQUIRED_EXECUTABLES = {
    "ffmpeg": (SYSTEM_TOOLS, "Prévia, corte e verificação ficam indisponíveis"),
    "ffprobe": (SYSTEM_TOOLS, "Prévia, corte e verificação ficam indisponíveis"),
    "curl": (SYSTEM_TOOLS, "Download dos pares CDN do Instagram fica indisponível"),
    "node": (SYSTEM_TOOLS, "Playwright CLI e runtime EJS do yt-dlp ficam indisponíveis"),
    "npx": (SYSTEM_TOOLS, "Instalação e execução do Playwright CLI ficam indisponíveis"),
    "yt-dlp": (INSTALLER, "YouTube e TikTok ficam indisponíveis sem ele"),
    "playwright-cli": (INSTALLER, "Instagram indisponível sem ele"),
}

# Ausência esperada em parte dos ambientes: não bloqueia o fluxo principal.
OPTIONAL_EXECUTABLES = {
    "deno": "Alternativa ao Node apenas para o runtime EJS",
    "bash": "Somente os helpers opcionais de YouTube; a CLI não depende dele",
}

OPTIONAL_KEYS = {
    "PEXELS_API_KEY": "Busca no Pexels desativada; defina a chave no ambiente ou no .env",
    "PIXABAY_API_KEY": "Busca no Pixabay desativada; defina a chave no ambiente ou no .env",
}


# Todo executável que o doctor sonda: obrigatórios mais opcionais, sem duplicar.
PROBED_EXECUTABLES = tuple(
    sorted(set(REQUIRED_EXECUTABLES) | set(OPTIONAL_EXECUTABLES))
)


def doctor_overrides():
    """Pins válidos e pins inválidos: um pin quebrado não derruba o diagnóstico."""
    from getbrolls.config import PATH_KEYS, pin_override

    active = {}
    problems = []
    for key in PATH_KEYS:
        try:
            value = pin_override(key)
        except ValueError as exc:
            problems.append({"item": key, "fix": INSTALLER, "note": str(exc)})
            continue
        if value:
            active[key] = str(value)
    return active, problems


def doctor_resolved(overrides):
    """Executável absoluto realmente usado por ferramenta, ou None quando ausente."""
    from getbrolls.config import TOOL_PATH_KEYS

    resolved = {}
    for name in PROBED_EXECUTABLES:
        found = overrides.get(TOOL_PATH_KEYS.get(name) or "") or shutil.which(name)
        if not found and name == "yt-dlp":
            from .social import local_ytdlp

            try:
                found = local_ytdlp()
            except ValueError:
                found = None
        if not found and name == "playwright-cli":
            found = _local_playwright()
        resolved[name] = str(Path(found).resolve()) if found else None
    return resolved


def doctor_summary(executables, pins=()):
    """Veredito humano do doctor: o que funciona, o que falta e o que é opcional."""
    ok = sorted(name for name, present in executables.items() if present)
    missing = [
        {"item": name, "fix": REQUIRED_EXECUTABLES[name][0], "note": REQUIRED_EXECUTABLES[name][1]}
        for name in sorted(REQUIRED_EXECUTABLES)
        if not executables.get(name)
    ]
    missing += list(pins)
    optional = [
        {"item": name, "note": note}
        for name, note in sorted(OPTIONAL_EXECUTABLES.items())
        if not executables.get(name)
    ]
    optional += [
        {"item": key, "note": note}
        for key, note in sorted(OPTIONAL_KEYS.items())
        if not os.environ.get(key)
    ]
    return {"ok": ok, "missing": missing, "optional": optional}


# Etapas do fluxo, na ordem coleta → revisão → entrega, com singular e plural.
STATUS_STAGES = (
    ("candidates", "candidato encontrado", "candidatos encontrados"),
    ("previews", "prévia gerada", "prévias geradas"),
    ("pending", "decisão pendente", "decisões pendentes"),
    ("approved", "decisão aprovada", "decisões aprovadas"),
    ("rejected", "decisão rejeitada", "decisões rejeitadas"),
    ("permitted", "item com permit registrado", "itens com permit registrado"),
    ("delivered", "item entregue", "itens entregues"),
    ("verified", "item verificado", "itens verificados"),
)

PREVIEW_ARTIFACTS = ("gif_path", "contact_sheet_path", "poster_path")


def _count(value, singular, plural):
    return f"{value} {singular if value == 1 else plural}"


def _identifier(result):
    return result.get("id") or "candidato"


def _note(result):
    """Observação do provedor, quando houver, colada ao fim da linha humana."""
    note = result.get("note")
    return f" {note}" if note else ""


def _status_line(result):
    counts = result.get("counts") or {}
    stages = ", ".join(
        _count(counts.get(key, 0), singular, plural)
        for key, singular, plural in STATUS_STAGES
    )
    return f"Resumi o projeto: {stages}."


# Uma linha por comando do fluxo: verbo + objeto + resultado, sempre em PT-BR.
FLOW_SUMMARIES = {
    "search": lambda r: f"Pesquisei candidatos: "
    f"{_count(len(r.get('items') or []), 'registrado', 'registrados')}, "
    f"{_count(r.get('excluded_by_rules') or 0, 'excluído pelas regras', 'excluídos pelas regras')}, "
    f"{_count(len(r.get('errors') or []), 'fonte com erro', 'fontes com erro')}."
    f"{_note(r)}",
    "resolve": lambda r: f"Registrei o candidato {_identifier(r)}: estado {r.get('state')}.",
    "preview": lambda r: (
        f"Gerei somente a referência estática de {_identifier(r)}: "
        f"estado {r.get('state')}, aprovação {(r.get('approval') or {}).get('status')}."
        if r.get("state") == "reference_only"
        else f"Gerei a prévia de {_identifier(r)}: "
        f"estado {r.get('state')}, aprovação {(r.get('approval') or {}).get('status')}."
    ),
    "approve": lambda r: f"Registrei a aprovação humana de {_identifier(r)}: "
    f"estado {r.get('state')}, por {(r.get('approval') or {}).get('by')}.",
    "reject": lambda r: f"Rejeitei {_identifier(r)}: estado {r.get('state')}, revisão invalidada.",
    "review": lambda r: f"Gerei o Storyboard em {r.get('review')}.",
    "import-review": lambda r: f"Importei "
    f"{_count(r.get('imported') or 0, 'decisão', 'decisões')} assinada(s) por {r.get('by')}.",
    "permit": lambda r: f"Registrei as condições de uso de {_identifier(r)}: "
    f"direitos {(r.get('rights') or {}).get('status')}.",
    "fetch": lambda r: f"Coletei o corte final de {_identifier(r)} em "
    f"{(r.get('output') or {}).get('path')}.",
    "verify": lambda r: f"Verifiquei "
    f"{_count(r.get('count') or 0, 'arquivo coletado', 'arquivos coletados')}: "
    f"{'íntegro e decodificável' if (r.get('count') or 0) == 1 else 'íntegros e decodificáveis'}.",
    "status": _status_line,
}


def with_summary(command, result):
    """Acrescenta a linha humana ao JSON do comando sem tocar nas chaves existentes."""
    formatter = FLOW_SUMMARIES.get(command)
    if formatter is None or not isinstance(result, dict) or "summary" in result:
        return result
    return {**result, "summary": formatter(result)}


# Escada do fluxo: a primeira condição verdadeira nomeia o próximo passo real.
STATUS_LADDER = (
    (
        lambda c: not c["candidates"],
        "Nenhum candidato ainda: registre fontes com search ou resolve.",
    ),
    (
        lambda c: c["previews"] < c["candidates"],
        "Gere prévias com preview para os candidatos ainda sem quadro.",
    ),
    (
        lambda c: not c["approved"],
        "Gere o Storyboard com review e importe a decisão humana com import-review.",
    ),
    (
        lambda c: c["permitted"] < c["approved"],
        "Registre as condições reais de uso com permit nos itens aprovados.",
    ),
    (
        lambda c: c["delivered"] < c["permitted"],
        "Colete os cortes aprovados e permitidos com fetch.",
    ),
    (
        lambda c: c["verified"] < c["delivered"],
        "Confira os arquivos coletados com verify.",
    ),
)


def status_next(counts, format_pending=0):
    """Próximo passo real do fluxo, derivado das contagens por etapa."""
    if format_pending:
        return (
            "As regras editoriais mudaram: o próximo comando invalidará "
            + _count(format_pending, "aprovação", "aprovações")
            + "; gere prévia e revisão novamente antes de coletar."
        )
    for matches, step in STATUS_LADDER:
        if matches(counts):
            return step
    return "Fluxo completo: os itens aprovados estão coletados e verificados."


def _has_preview(c):
    return any((c.get("preview") or {}).get(key) for key in PREVIEW_ARTIFACTS)


def _stage_status(c, field):
    return (c.get(field) or {}).get("status")


# Um predicado por etapa: a mesma leitura serve para contagem, lista e item.
STAGE_TESTS = {
    "candidates": lambda c: True,
    "previews": _has_preview,
    "pending": lambda c: _stage_status(c, "approval") not in ("approved", "rejected"),
    "approved": lambda c: _stage_status(c, "approval") == "approved",
    "rejected": lambda c: _stage_status(c, "approval") == "rejected",
    "permitted": lambda c: _stage_status(c, "rights") == "permitted",
    "delivered": lambda c: bool((c.get("output") or {}).get("path")),
    "verified": lambda c: bool((c.get("output") or {}).get("verified")),
}


def status_journal(root):
    """Leitura do journal append-only: quantos eventos e qual foi o último."""
    path = root / "events.jsonl"
    if not path.is_file():
        return {"events": 0, "last": None}
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        last = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        # Relatar o estado não pode falhar por causa de um log corrompido.
        return {
            "events": len(lines),
            "last": None,
            "error": "events.jsonl contém JSON inválido no último registro. Preserve o histórico e restaure o log.",
        }
    return {"events": len(lines), "last": last}


def status_references(root):
    """Quantas referências memorizadas o projeto tem, sem derrubar o relatório."""
    path = root / "references.json"
    if not path.is_file():
        return 0, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return 0, (
            "references.json inválido ou incompatível. Preserve o arquivo e restaure uma cópia válida."
        )
    return len(items), None


def _format_pending(c, rules):
    """True quando as regras atuais mudariam o formato-alvo já gravado no item."""
    if rules is None:
        return None
    from getbrolls.rules import format_report

    return c.get("format", {}).get("target", "native") != format_report(c, rules)["target"]


def status_report(ledger, rules=None, rules_error=None):
    """Onde o projeto está, por etapa. Somente leitura: não grava nada."""
    items = ledger.data["items"]
    listing = {
        key: [c["id"] for c in items if STAGE_TESTS[key](c)]
        for key, _, _ in STATUS_STAGES
    }
    counts = {key: len(listing[key]) for key, _, _ in STATUS_STAGES}
    pending_format = {c["id"]: _format_pending(c, rules) for c in items}
    format_pending = sum(1 for value in pending_format.values() if value)
    remembered, references_error = status_references(ledger.root)
    review_page = ledger.root / "review.html"
    line = _status_line({"counts": counts})
    if ledger.recovered:
        line += (
            " Há uma gravação interrompida pendente; o próximo comando de escrita a concluirá."
        )
    # Veredito primeiro, como no doctor: o JSON completo continua logo abaixo.
    summary = {
        "line": line,
        "stages": [
            {"stage": plural, "count": counts[key], "items": listing[key]}
            for key, _, plural in STATUS_STAGES
        ],
        "next": status_next(counts, format_pending),
    }
    return {
        "summary": summary,
        "project": str(ledger.root),
        "counts": counts,
        "stages": listing,
        "items": [
            {
                "id": c["id"],
                "title": c.get("title"),
                "provider": c.get("provider"),
                "source_url": c.get("source_url"),
                "state": c.get("state"),
                "segment": c.get("segment"),
                "approval": (c.get("approval") or {}).get("status"),
                "rights": (c.get("rights") or {}).get("status"),
                "preview": _has_preview(c),
                "format_pending": pending_format[c["id"]],
                "output": (c.get("output") or {}).get("path"),
            }
            for c in items
        ],
        "format_pending": format_pending,
        "rules_error": rules_error,
        "references": remembered,
        "references_error": references_error,
        "review_page": str(review_page) if review_page.is_file() else None,
        "journal": {
            **status_journal(ledger.root),
            "recovered_write": "pending" if ledger.recovered else False,
        },
    }


def _local_playwright(root=None):
    """Playwright CLI instalado em `.tools`, ou None quando não há um."""
    root = Path(root) if root is not None else SKILL_ROOT
    root = root / ".tools/node_modules/.bin"
    for name in ("playwright-cli.cmd", "playwright-cli"):
        if (root / name).is_file():
            return root / name
    return None


def execute(args):
    from getbrolls.config import load_env, settings

    if args.env_file and not Path(args.env_file).is_file():
        raise ValueError("--env-file não existe. Confira o caminho.")
    load_env(args.env_file or Path(__file__).resolve().parents[2] / ".env")
    config = settings()
    from getbrolls import providers

    if args.command in ("providers", "doctor"):
        result = providers.capabilities()
        if args.command == "doctor":
            from getbrolls.config import TOOL_PATH_KEYS
            from .social import doctor as social_doctor

            # Pin inválido vira item de `missing`, não morte do diagnóstico.
            overrides, pin_problems = doctor_overrides()
            resolved = doctor_resolved(overrides)
            try:
                social = social_doctor()
            except ValueError as exc:
                social = {"engine": "yt-dlp", "installed": False, "error": str(exc)}
            executables = {
                name: bool(overrides.get(TOOL_PATH_KEYS.get(name) or "") or shutil.which(name))
                for name in PROBED_EXECUTABLES
            }
            executables["yt-dlp"] = social["installed"]
            executables["playwright-cli"] = bool(
                _local_playwright() or shutil.which("playwright-cli")
            )
            result = {
                # Veredito primeiro: o JSON continua completo logo abaixo dele.
                "summary": doctor_summary(executables, pin_problems),
                "get_brolls": __version__,
                "preview": config,
                "python": sys.version.split()[0],
                "tool_paths": overrides,
                "executables": executables,
                "resolved": resolved,
                "providers": result,
                "social": social,
            }
            if args.live:
                from getbrolls.health import live_checks

                result["live"] = live_checks()
        return result
    cmd = args.command
    from getbrolls.rules import load_rules, allowed, domain_matches, format_report

    if cmd == "status":
        # Somente leitura: nada é criado, nem a árvore do projeto, nem pendências.
        project = Path(args.project).expanduser().resolve()
        if not (project / "brolls").is_dir():
            raise ValueError(
                f"Projeto não encontrado em {project}; nenhum arquivo foi criado."
            )
        rules = None
        rules_error = None
        try:
            rules = load_rules(args.project)
        except (ValueError, OSError) as exc:
            rules_error = str(exc)
        return status_report(Ledger(project, recover=False), rules, rules_error)
    if cmd == "init-rules":
        dest = Path(args.project) / "RULES.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            raise ValueError("RULES.md já existe; edite sem sobrescrever suas regras.")
        shutil.copyfile(SKILL_ROOT / "RULES.md", dest)
        return {"rules": str(dest)}
    rules = load_rules(args.project)
    if cmd == "rules":
        return rules
    ledger = Ledger(args.project)
    from getbrolls.rules import sync_formats

    sync_formats(ledger, rules)
    if cmd == "browser-plan":
        from getbrolls.browser import plan

        return plan(ledger, args.url, rules)
    if cmd == "references":
        path = ledger.root / "references.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}

    if cmd == "search":
        if "video" not in rules["asset_types"]:
            return {
                "items": [],
                "errors": [],
                "note": "APIs atuais pesquisam vídeos. Para imagem/notícia use importação local ou browser-plan.",
            }
        args.provider = {"pixel": "pexels", "getbrolls": "auto"}.get(
            args.provider, args.provider
        )
        if not 1 <= args.limit <= 50:
            raise ValueError("Use --limit entre 1 e 50.")
        names = (
            rules["preferred_providers"][args.intent]
            if args.provider == "auto"
            else [args.provider]
        )
        if not names:
            raise ValueError(
                "Nenhuma fonte configurada: use resolve --file, Commons/NASA ou configure a chave de um banco."
            )
        items = []
        errors = []
        excluded = 0
        for name in names:
            if len(items) >= args.limit:
                break
            try:
                for c in providers.search(name, args.query, args.limit - len(items)):
                    if not allowed(c, rules):
                        excluded += 1
                        continue
                    c["format"] = format_report(c, rules)
                    c["match"] = {
                        "kind": args.intent,
                        "reason": "Candidato de busca: correspondência visual deve ser revisada.",
                    }
                    c = ledger.add(c)
                    ledger.save("search", c)
                    items.append(c)
            except ValueError as e:
                errors.append({"provider": name, "error": str(e)})
        if not items and errors:
            raise ValueError(json.dumps(errors, ensure_ascii=False))
        items.sort(
            key=lambda c: (
                not domain_matches(c.get("source_url"), rules["preferred_domains"])
            )
        )
        return {
            "items": items,
            "errors": errors,
            "excluded_by_rules": excluded,
            "editorial_rules": rules["editorial_rules"],
        }
    if cmd == "resolve":
        for flag, value in (("--file", args.file), ("--url", args.url)):
            if value is not None and not value.strip():
                raise ValueError(
                    f"{flag} não pode ser vazio: informe o caminho ou a URL real."
                )
        if args.file:
            path = Path(args.file).expanduser().resolve()
            if not path.is_file():
                raise ValueError("Arquivo local inexistente.")
            sha = digest(path)
            c = candidate("local", sha[:16], path.name)
            c["local_path"] = str(path)
            c["local_sha256"] = sha
            c["media"] = probe(path)
            c["acquisition"] = {
                "status": "available",
                "method": "local",
                "evidence": [],
            }
            c["preview"]["seek_mode"] = "local"
        else:
            c = providers.resolve(args.url)
        for argument, field in (
            (args.context_image, "context_image"),
            (args.full_preview_file, "full_preview"),
        ):
            if argument:
                if not args.file:
                    raise ValueError(
                        "Contexto/composição exigem um B-roll local em --file."
                    )
                auxiliary = Path(argument).expanduser().resolve()
                if not auxiliary.is_file():
                    raise ValueError("Arquivo de contexto/composição não encontrado.")
                if field == "context_image" and auxiliary.suffix.lower() not in (
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                ):
                    raise ValueError("--context-image deve ser um print PNG/JPG/WebP.")
                c[field + "_path"] = str(auxiliary)
                c[field + "_sha256"] = digest(auxiliary)
                c[field + "_media"] = probe(auxiliary)
        if args.file:
            inferred = (
                "image"
                if path.suffix.lower()
                in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")
                else "video"
            )
            c["asset_type"] = args.asset_type or inferred
            if (c["asset_type"] == "video") != (inferred == "video"):
                raise ValueError("asset-type não corresponde ao formato do arquivo.")
            c["media"]["kind"] = "video" if c["asset_type"] == "video" else "image"
            if args.title:
                c["title"] = args.title
            if args.captured_at:
                from datetime import datetime

                datetime.fromisoformat(args.captured_at)
                c["captured_at"] = args.captured_at
        elif args.asset_type or args.title or args.captured_at:
            raise ValueError("Metadados locais exigem --file.")
        if args.source_url or args.creator:
            if not args.file:
                raise ValueError("--source-url/--creator são exclusivos de --file.")
            if args.source_url:
                from getbrolls.http import public_url

                url = public_url(args.source_url)
                if not url:
                    raise ValueError(
                        "Fonte deve ser URL HTTPS pública sem credenciais."
                    )
                c["source_url"] = url
            if args.creator:
                c["creator"]["name"] = args.creator
        if args.shot:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.shot):
                raise ValueError(
                    "--shot: use 1–80 letras, números, hífen ou underscore."
                )
            c["id"] += ":shot:" + args.shot
            c["shot"] = args.shot
        if c.get("asset_type") in ("news_screenshot", "web_screenshot") and not c.get(
            "source_url"
        ):
            raise ValueError("Screenshot exige --source-url para manter a origem.")
        if not allowed(c, rules):
            raise ValueError(
                "Fonte ou tipo de asset bloqueado pelas regras do usuário."
            )
        c["format"] = format_report(c, rules)
        c = ledger.add(c)
        ledger.save(cmd, c)
        return c
    if cmd == "import-review":
        from getbrolls.review import import_review

        result = import_review(ledger, args.file, args.by, rules)
        render(ledger)
        return result
    if cmd == "review":
        return {"review": render(ledger)}
    if cmd == "verify":
        checked = []
        for c in ledger.data["items"]:
            if c["output"]["path"]:
                path = ledger.root / c["output"]["path"]
                info = probe(path)
                if digest(path) != c["output"]["sha256"]:
                    raise ValueError("Arquivo alterado após coleta: " + c["id"])
                run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])
                checked.append(
                    {
                        "id": c["id"],
                        "media": info,
                        "hd": min(info["width"], info["height"]) >= 1080,
                    }
                )
        return {"verified": checked, "count": len(checked)}
    c = ledger.get(args.candidate)
    if not allowed(c, rules) and cmd in ("preview", "approve", "permit", "fetch"):
        raise ValueError("Asset bloqueado pelas regras atuais do usuário.")
    if cmd == "remember":
        from getbrolls.memory import remember

        return remember(ledger, c, args.decision, args.reason, args.by)
    if cmd in ("preview", "approve"):
        if c.get("media", {}).get("kind") != "image":
            if args.start is None or args.end is None:
                raise ValueError("Vídeo exige --start e --end.")
            set_segment(c, args.start, args.end)
        elif args.start is not None or args.end is not None:
            raise ValueError("Imagem estática não precisa de intervalo de origem.")
    if cmd == "approve":
        approve(c, args.by)
    elif cmd == "permit":
        if args.declaration:
            rights = rules["copyright"]
            if rights["mode"] != "user_declaration":
                raise ValueError(
                    "Usuário deve configurar sua declaração em RULES.md primeiro."
                )
            evidence = (
                "Declaração do usuário "
                + rights["responsible_person"]
                + ": "
                + rights["declaration"]
            )
            c["rights"]["basis"] = "user_declaration"
            c["rights"]["responsible_person"] = rights["responsible_person"]
        else:
            if not args.evidence.strip():
                raise ValueError("Evidência não pode ser vazia.")
            evidence = args.evidence
            c["rights"]["basis"] = "per_item_evidence"
        c["rights"]["status"] = "permitted"
        c["rights"]["evidence"].append(evidence)
    elif cmd == "reject":
        c["approval"]["status"] = "rejected"
        c["state"] = "rejected"
        c.pop("review", None)
    elif cmd == "preview":
        context_before = signature(c)
        if not args.reference_only and c["provider"] != "local":
            if args.end - args.start > config["max_seconds"]:
                raise ValueError("Trecho excede GB_PREVIEW_MAX_SECONDS; ajuste o intervalo antes de obter mídia.")
            from .acquisition import prepare_source
            prepare_source(ledger, c, args.start, args.end)
        if c.get("local_path") and not args.reference_only:
            from .previewing import prepare_preview

            prepare_preview(ledger, c, args.start, args.end, config)
            if (
                c["approval"]["status"] == "approved"
                and c["approval"].get("signature") == signature(c)
            ):
                c["state"] = "verified" if c["output"].get("verified") else "approved"
            else:
                c["state"] = (
                    "rejected" if c["approval"]["status"] == "rejected"
                    else "awaiting_approval"
                )
        else:
            c["preview"]["warning"] = (
                "Somente referência estática; o trecho animado requer original local autorizado."
            )
            c["state"] = "reference_only"
        if c["preview"].get("warning"):
            from .runtime import record_warning

            record_warning("PREVIEW_LIMITATION", c["preview"]["warning"])
        if args.narration is not None:
            c["narration"] = args.narration
        if args.reason is not None:
            c["match"]["reason"] = args.reason
        if context_before != signature(c):
            c["approval"] = {
                "status": "pending",
                "by": None,
                "at": None,
                "revision": None,
            }
            c.pop("review", None)
            c["state"] = (
                "awaiting_approval" if c.get("local_path") else "reference_only"
            )
    elif cmd == "fetch":
        require_fetch(c)
        src = c.get("local_path")
        temp = None
        if src:
            if digest(src) != c["local_sha256"]:
                raise ValueError(
                    "Original local mudou: importe novamente e aprove a nova versão."
                )
        else:
            # Re-resolve from the provider to refresh temporary variant URLs.
            fresh = providers.refresh(c)
            url = fresh.get("media_url")
            if not url:
                raise ValueError(
                    "Esta fonte não disponibilizou arquivo por transporte permitido."
                )
            from getbrolls.http import download

            temp = (
                ledger.root
                / "previews"
                / (
                    "download-"
                    + hashlib.sha256(c["id"].encode()).hexdigest()[:16]
                    + ".part"
                )
            )
            download(url, temp)
            src = temp
        if c.get("media", {}).get("kind") == "image":
            rel = (
                "clips/"
                + hashlib.sha256(c["id"].encode()).hexdigest()[:16]
                + f"-r{c['segment']['revision']}"
                + Path(src).suffix.lower()
            )
            dest = ledger.root / rel
            if dest.exists():
                raise ValueError("Arquivo final já existe; não foi sobrescrito.")
            from .media import copy_image

            copy_image(src, dest)
            c["output"] = {"path": rel, "sha256": digest(dest), "verified": True}
            c["state"] = "verified"
            ledger.save(cmd, c)
            render(ledger)
            return c
        rel = (
            "clips/"
            + hashlib.sha256(c["id"].encode()).hexdigest()[:16]
            + f"-r{c['segment']['revision']}.mp4"
        )
        try:
            offset = c.get("local_start_s", 0)
            start = c["segment"]["start_s"] - offset
            end = c["segment"]["end_s"] - offset
            if start < 0 or (c.get("local_duration_s") is not None and end > c["local_duration_s"] + .1):
                raise ValueError("Gere uma nova prévia para este intervalo antes da coleta.")
            cut(src, ledger.root / rel, start, end)
        finally:
            if temp:
                temp.unlink(missing_ok=True)
        c["output"] = {
            "path": rel,
            "sha256": digest(ledger.root / rel),
            "verified": True,
        }
        c["state"] = "verified"
        c["output_media"] = probe(ledger.root / rel)
    ledger.save(cmd, c)
    render(ledger)
    return c
