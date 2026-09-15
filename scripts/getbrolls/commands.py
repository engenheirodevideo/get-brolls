"""Existing workflow command handlers; CLI parsing and reporting live separately."""

import json, sys, hashlib, shutil, os, re
from pathlib import Path
from .models import candidate, set_segment, approve, require_fetch, signature
from .ledger import Ledger, digest
from .media import probe, cut, run
from .rendering import render


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
            result = {
                "preview": config,
                "runtime": sys.version.split()[0],
                "executables": {
                    x: bool(shutil.which(x))
                    for x in ("ffmpeg", "ffprobe", "yt-dlp", "curl", "bash", "node", "deno", "npx")
                },
                "providers": result,
            }
        if args.command == "doctor":
            from .social import doctor as social_doctor
            result["social"] = social_doctor()
            result["executables"]["yt-dlp"] = result["social"]["installed"]
            result["executables"]["playwright-cli"] = (Path(__file__).resolve().parents[2] / ".tools/node_modules/.bin/playwright-cli").is_file() or bool(shutil.which("playwright-cli"))
        if args.command == "doctor" and args.live:
            from getbrolls.health import live_checks

            result["live"] = live_checks()
        return result
    cmd = args.command
    from getbrolls.rules import (
        load_rules,
        allowed,
        domain_matches,
        format_report,
        ROOT as SKILL_ROOT,
    )

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
        return json.loads(path.read_text()) if path.exists() else {"items": []}

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
