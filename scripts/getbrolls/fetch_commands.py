"""Coleta e conferência dos cortes (`fetch`, `verify`), o cartaz das referências e os arquivos de prévia."""

import contextlib
import logging
import time
from pathlib import Path

from . import _paths, effects, labels, layout, logs
from .acquisition import candidate_arg
from .errors import PrerequisiteError
from .ledger import digest
from .media import cut, probe, run
from .models import id_stem, now, require_fetch
from .rendering import render
from .runtime import record_warning

_log = logs.get("commands")


# --- Audit-trail logging helpers -------------------------------------------
# Pure, defensive readers used only to build fields for `logs.event()` calls.
# Each one swallows its own failure and returns a safe default instead of
# raising, so a logging call site can never change command behavior.


def _safe_size(path):
    """File size in bytes, or None when the file is missing/unreadable."""
    try:
        return Path(path).stat().st_size
    # lenient: log field only: file size or None, so a logging call never raises
    except OSError:
        return None


def _sha256_prefix(value):
    """First 12 chars of a sha256 hex digest, or None."""
    return value[:12] if isinstance(value, str) and value else None


def _fetch_local_source(c):
    """Confirma que o original local importado ainda bate com o hash registrado."""
    src = c["local_path"]
    if digest(src) != c["local_sha256"]:
        raise ValueError("Original local mudou: importe novamente e aprove a nova versão.")
    return src


def _fetch_routed_check_existing(ledger, c):
    """Recusa a rota de plugin se a revisão atual já foi coletada (sem gastar licença/cota)."""
    stem = id_stem(c["id"]) + f"-r{c['segment']['revision']}"
    project = ledger.root.parent
    if c.get("media", {}).get("kind") == "image":
        # Todas as pastas de clipes do layout: no 1, a antiga `brolls/clips/` também.
        existing = layout.existing_clips(project, stem + ".*")
        if existing:
            # Recusa antes de gastar licença/cota numa revisão já coletada, seja
            # qual for a extensão que a imagem coletada usou.
            raise ValueError(_already_collected(existing[0]))
        return
    planned = "clips/" + stem + ".mp4"
    if layout.clip_taken(project, planned):
        # Recusa antes de gastar licença/cota numa revisão já coletada.
        raise ValueError(_already_collected(planned))


def _fetch_routed_record_license(c, routed, reacquire):
    """Grava consumo/reaquisição de licença da rota de plugin; devolve se algo mudou."""
    from .acquisition import license_evidence

    changed = False
    if reacquire and not routed.reused and c["acquisition"].get("route_consumed_at"):
        # Nova aquisição confirmada pela pessoa: a primeira data fica, e esta
        # entra na lista — o ledger mostra que a licença foi consumida de novo.
        c["acquisition"].setdefault("route_reacquired_at", []).append(now())
        logs.event(_log, logging.INFO, "route_reacquired", candidate=c["id"], plugin=routed.plugin)
        changed = True
    if routed.license:
        # Evidência a mais, gravada depois do permit humano — nunca no lugar dele.
        evidence = license_evidence(routed.plugin, routed.license)
        if evidence not in c["rights"]["evidence"]:
            c["rights"]["evidence"].append(evidence)
            changed = True
    if not c["acquisition"].get("route_consumed_at"):
        c["acquisition"]["route_consumed_at"] = now()
        changed = True
    return changed


def _fetch_routed_validate_format(c, routed):
    """Confere formato de imagem, ou duração mínima do vídeo, entregues pela rota de plugin."""
    if c.get("media", {}).get("kind") == "image":
        # O formato só é conferido DEPOIS que cache e licença já estão
        # gravados (acima) — uma imagem de formato não reconhecido é recusada sem
        # custar a rota (e a licença) de novo a cada retry; o cache já existe.
        # A extensão vem do conteúdo, como na foto das fontes embutidas
        # (`media.image_suffix`), nunca do nome que o plugin deu ao arquivo.
        from .media import SNIFFED_IMAGE_SUFFIXES, image_suffix

        try:
            image_suffix(routed.path)
        except ValueError:
            from .http import ProviderError

            raise ProviderError(
                f"Plugin {routed.plugin}: a rota devolveu uma imagem de formato não "
                f"reconhecido; entregue {', '.join(SNIFFED_IMAGE_SUFFIXES)}."
            ) from None
        return
    if routed.duration_s is not None and c["segment"]["end_s"] > routed.duration_s + 0.1:
        raise ValueError(
            f"A fonte entregou {routed.duration_s:g} s, mas o trecho aprovado vai até "
            f"{c['segment']['end_s']:g} s: a duração real é menor. Gere a prévia com um "
            "intervalo dentro dela (preview --reference-only), aprove e rode fetch de novo; "
            "o arquivo já baixado é reaproveitado."
        )


def _fetch_routed_source(args, ledger, c):
    """Fonte de plugin: cache privado reaproveitável, licença registrada e formato conferido."""
    from .acquisition import fetch_routed_source

    # Rota de plugin: roda só aqui, depois de aprovação + permit (require_fetch antes
    # de chamar). O plugin traz o arquivo; corte, hash e ledger seguem com o core.
    _fetch_routed_check_existing(ledger, c)
    # O arquivo da rota vai para o cache privado (fora de `brolls/`) e é
    # reaproveitado se este `fetch` falhar adiante: a licença é consumida uma vez.
    reacquire = bool(getattr(args, "reacquire", False))
    routed = fetch_routed_source(ledger, c, reacquire=reacquire)
    if _fetch_routed_record_license(c, routed, reacquire):
        # Grava já, antes do corte: se o corte falhar, a licença consumida e o
        # marcador ficam no ledger (a rota não roda de novo no próximo fetch).
        ledger.save("fetch-route", c)
    _fetch_routed_validate_format(c, routed)
    return routed.path


def _fetch_downloaded_source(ledger, c):
    """Sem original local nem rota de plugin: baixa direto do provedor para um arquivo temporário."""
    from getbrolls import providers
    from getbrolls.http import download_rendition

    # Re-resolve from the provider to refresh temporary variant URLs.
    fresh = providers.refresh(c)
    url = fresh.get("media_url")
    if not url:
        raise ValueError(
            "Esta fonte não disponibilizou arquivo por transporte permitido; "
            f"execute antes: preview --candidate {candidate_arg(c)} --start INICIO --end FIM"
        )
    temp = ledger.root / "previews" / ("download-" + id_stem(c["id"]) + ".part")
    # Sobra de um `fetch` morto no meio (SIGTERM): o download abre com "xb" e
    # recusaria o nome já existente. É o mesmo cuidado do `reference_poster`.
    effects.unlink(temp, missing_ok=True)
    download_rendition(fresh, temp)
    return temp


def _fetch_source(args, ledger, c):
    """Resolve de onde vem o arquivo do `fetch`: local, rota de plugin, ou download direto.

    Devolve {src, temp, routed_remote}. `temp` é o arquivo temporário a apagar depois
    (só no caso de download); `routed_remote` marca a rota de plugin para o log final.
    """
    from .acquisition import route_name

    if c.get("local_path"):
        return {"src": _fetch_local_source(c), "temp": None, "routed_remote": False}
    if route_name(c) is not None:
        return {"src": _fetch_routed_source(args, ledger, c), "temp": None, "routed_remote": True}
    temp = _fetch_downloaded_source(ledger, c)
    return {"src": temp, "temp": temp, "routed_remote": False}


def _fetch_image_output(ledger, c, source):
    """Copia a imagem baixada/roteada/local para clips/, com extensão pelo conteúdo real."""
    from .media import IMAGE_SUFFIXES, copy_image, image_suffix

    src, temp = source["src"], source["temp"]
    # O download chega como `.part` (e cópia antiga do cache como `.mp4`), e a
    # rota de plugin nomeia o arquivo como quiser: a extensão de `clips/` — e,
    # dela, a de `entrega/` — sai do conteúdo real, só de formato conhecido; o
    # resto é recusado, nunca herda a da URL nem a do nome. Só o original
    # local importado pela pessoa, já com extensão de imagem, fica como está.
    suffix = Path(src).suffix.lower()
    try:
        if c["provider"] != "local" or suffix not in IMAGE_SUFFIXES:
            suffix = image_suffix(src)
        rel = "clips/" + id_stem(c["id"]) + f"-r{c['segment']['revision']}" + suffix
        dest = layout.clip_file(ledger.root.parent, rel, for_write=True)
        if layout.clip_taken(ledger.root.parent, rel):
            raise ValueError(_already_collected(rel))
        staged = _staged_clip(dest)
        copy_image(src, staged)
    finally:
        if temp:
            effects.unlink(temp, missing_ok=True)
    c["output"] = {"path": rel, "sha256": digest(staged), "verified": True}
    c["state"] = "verified"
    return staged, dest


def _staged_clip(dest):
    """Nome provisório do clipe, na mesma pasta: o nome final só vem depois do journal.

    Uma sobra de `fetch` interrompido com o mesmo nome é apagada antes.
    """
    staged = layout.staged_clip(dest)
    effects.unlink(staged, missing_ok=True)
    return staged


def _drop_staged_leftovers(ledger, c):
    """Apaga o provisório que um `fetch` morto (SIGKILL) deixou desta revisão.

    Roda antes de tudo: a recusa "já coletada" da rota de plugin olha `<nome>.*` e
    tomaria a sobra por um clipe. Pasta de clipes inutilizável fica para quem grava recusar.
    """
    stem = id_stem(c["id"]) + f"-r{c['segment']['revision']}"
    try:
        folder = layout.clips_dir(ledger.root.parent)
    except ValueError:
        return
    # lenient: cleanup of staged leftovers from an interrupted fetch
    if folder.is_dir():
        for leftover in folder.glob(stem + layout.STAGED_CLIP_MARK + ".*"):
            effects.unlink(leftover, missing_ok=True)


def _publish_clip(ledger, c, staged):
    """Grava o journal do `fetch` com o nome final do clipe; o ledger troca o nome do provisório.

    A troca roda dentro da transação, depois do journal e antes do manifesto. Um SIGKILL em qualquer
    ponto deixa o journal para o próximo comando concluir: o manifesto nunca nomeia um clipe que não
    existe. Interrompido antes do journal, o manifesto não conhece o corte e o provisório some.
    """
    try:
        ledger.save("fetch", c, publish=[c["output"]["path"]])
    except BaseException:
        # lenient: inside except BaseException of a failed save; must not mask that error
        if not ledger.pending.exists():
            effects.unlink(staged, missing_ok=True)
        raise


def _fetch_finish_image(ledger, c, source, fetch_started_at):
    """Grava e loga o `fetch` de imagem; devolve o candidato (retorno antecipado do pipeline)."""
    staged, dest = _fetch_image_output(ledger, c, source)
    _publish_clip(ledger, c, staged)
    render(ledger)
    logs.event(
        _log,
        logging.INFO,
        "fetch",
        candidate=c["id"],
        kind="remote" if c["provider"] != "local" else "local",
        bytes=_safe_size(dest),
        sha256_prefix=_sha256_prefix(c["output"]["sha256"]),
        ms=round((time.monotonic() - fetch_started_at) * 1000),
    )
    return c


def _fetch_video_output(ledger, c, source):
    """Corta o vídeo para clips/, recusando sobrescrever uma revisão já coletada."""
    src, temp = source["src"], source["temp"]
    rel = "clips/" + id_stem(c["id"]) + f"-r{c['segment']['revision']}.mp4"
    try:
        dest = layout.clip_file(ledger.root.parent, rel, for_write=True)
    except ValueError:
        if temp:
            effects.unlink(temp, missing_ok=True)
        raise
    # O arquivo entregue nasce somente-leitura (delivery_files._freeze congela o inode
    # compartilhado): sem esta checagem o ffmpeg falharia por permissão, sem dizer
    # o motivo. Recusar aqui, antes de gastar a fonte, explica o que fazer.
    if layout.clip_taken(ledger.root.parent, rel):
        if temp:
            effects.unlink(temp, missing_ok=True)
        raise ValueError(_already_collected(rel))
    staged = _staged_clip(dest)
    try:
        offset = c.get("local_start_s", 0)
        start = c["segment"]["start_s"] - offset
        end = c["segment"]["end_s"] - offset
        if start < 0 or (c.get("local_duration_s") is not None and end > c["local_duration_s"] + 0.1):
            raise ValueError("Gere uma nova prévia para este intervalo antes da coleta.")
        cut(src, staged, start, end)
    finally:
        if temp:
            effects.unlink(temp, missing_ok=True)
    c["output"] = {
        "path": rel,
        "sha256": digest(staged),
        "verified": True,
    }
    c["state"] = "verified"
    c["output_media"] = probe(staged)
    return staged, dest


def _fetch_finish_video(ledger, c, source, fetch_started_at):
    """Corta, grava e loga o `fetch` de vídeo; devolve o candidato."""
    staged, _ = _fetch_video_output(ledger, c, source)
    _publish_clip(ledger, c, staged)
    render(ledger)
    with contextlib.suppress(Exception):  # logging must never break a command
        logs.event(
            _log,
            logging.INFO,
            "fetch",
            candidate=c["id"],
            kind="remote" if source["temp"] or source["routed_remote"] else "local",
            bytes=_safe_size(layout.clip_file(ledger.root.parent, c["output"]["path"], for_write=True))
            if c["output"].get("path")
            else None,
            sha256_prefix=_sha256_prefix(c["output"].get("sha256")),
            ms=round((time.monotonic() - fetch_started_at) * 1000),
        )
    return c


def _apply_fetch(args, ledger, c):
    """Aplica `fetch` por completo (rota local/plugin/download, corte, hash) e devolve o candidato salvo."""
    fetch_started_at = time.monotonic()
    require_fetch(c)
    _drop_staged_leftovers(ledger, c)
    source = _fetch_source(args, ledger, c)
    if c.get("media", {}).get("kind") == "image":
        return _fetch_finish_image(ledger, c, source, fetch_started_at)
    return _fetch_finish_video(ledger, c, source, fetch_started_at)


def _verify_failure_result(exc, existed):
    """Classifica a falha de verificação (missing/mismatch/undecodable) para o log."""
    if not existed:
        return "missing"
    if "Arquivo alterado após coleta" in str(exc):
        return "mismatch"
    return "undecodable"


def _verify_clip_failure(ledger, c, exc, existed):
    """Reage a uma falha de verificação: derruba `verified` (se estava true) e loga."""
    # Um clipe que não bate mais com o registrado, ou que não decodifica,
    # não pode continuar marcado como verificado: quem entrega depois
    # confiaria num hash velho. `sha256` fica como está — é a prova do
    # que foi coletado — só `verified` cai (e o estado volta a
    # `approved`), e uma nova `verify` bem-sucedida volta a marcar.
    if c["output"]["verified"]:
        c["output"]["verified"] = False
        if c["state"] == "verified":
            c["state"] = "approved"
        ledger.save("verify", c)
    with contextlib.suppress(Exception):  # logging must never break a command
        logs.event(_log, logging.WARNING, "verify", candidate=c["id"], result=_verify_failure_result(exc, existed))


def _verify_clip_success(ledger, c, info):
    """Registra a verificação bem-sucedida e devolve a entrada de `checked`."""
    # Probe, hash e decodificação bateram: se uma verificação anterior tinha
    # derrubado a flag (arquivo trocado e depois restaurado), volta a True.
    if not c["output"]["verified"]:
        c["output"]["verified"] = True
        if c["state"] == "approved":
            c["state"] = "verified"
        ledger.save("verify", c)
    logs.event(_log, logging.INFO, "verify", candidate=c["id"], result="ok")
    is_hd = min(info["width"], info["height"]) >= 1080  # noqa: PLR2004 - short side of 1080p, the usual floor for "HD"
    return {"id": c["id"], "media": info, "hd": is_hd}


def _verify_one_clip(ledger, c):
    """Reconfere um clipe coletado: devolve (entrada p/ `checked`, exceção de falha) — um dos dois é None."""
    if not c["output"]["path"]:
        return None, None
    try:
        # Layout 1 lê `broll/` e a antiga `brolls/clips/`; o sha256 registrado
        # desempata o mesmo nome nas duas, e sem desempate a falha diz as duas.
        path = layout.output_file(ledger.root.parent, c["output"]["path"], sha256=c["output"].get("sha256"))
    except ValueError as exc:
        _verify_clip_failure(ledger, c, exc, True)
        return None, exc
    existed = _paths.strict_exists(path)
    try:
        info = probe(path)
        if digest(path) != c["output"]["sha256"]:
            raise ValueError("Arquivo alterado após coleta: " + c["id"])
        run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])
    except PrerequisiteError:
        # Faltar ffprobe/ffmpeg é defeito da instalação, não do clipe: nada de
        # derrubar `verified` de um clipe que ninguém conseguiu conferir.
        raise
    except ValueError as exc:
        _verify_clip_failure(ledger, c, exc, existed)
        return None, exc
    return _verify_clip_success(ledger, c, info), None


def reference_poster(ledger, c):
    """Materializa o cartaz estático da referência: miniatura da fonte, ou 1º quadro local.

    Não baixa o vídeo e não escolhe intervalo: só garante que exista um arquivo de
    imagem para a pessoa olhar (e para `status` contar como prévia). Falhar aqui é
    aceitável — vira aviso, não erro, porque a referência continua válida sem imagem.
    """
    from .media import image_preview

    if c["preview"].get("poster_path"):
        return c["preview"]["poster_path"]
    stem = id_stem(c["id"]) + "-ref"
    previews = ledger.root / "previews"
    effects.mkdir(previews, parents=True, exist_ok=True)
    source = c.get("local_path")
    temp = None
    try:
        if not source:
            url = (c.get("preview") or {}).get("poster_url")
            if not url:
                record_warning(
                    "REFERENCE_POSTER_MISSING",
                    "A fonte não ofereceu miniatura pública; a referência fica sem imagem.",
                )
                return None
            from .http import download

            temp = previews / (stem + ".part")
            effects.unlink(temp, missing_ok=True)
            download(url, temp, max_bytes=32 * 1024 * 1024)
            source = temp
        result = image_preview(source, previews, stem)
    except (OSError, ValueError, RuntimeError) as error:
        record_warning(
            "REFERENCE_POSTER_MISSING",
            f"Não consegui montar o cartaz estático desta referência ({labels.reason(error)}).",
        )
        return None
    finally:
        if temp is not None:
            effects.unlink(temp, missing_ok=True)
    c["preview"]["poster_path"] = result["poster_path"]
    return result["poster_path"]


def _already_collected(rel):
    """Mesma recusa para imagem e vídeo: já existe corte desta revisão, e eu não sobrescrevo."""
    return (
        f"Arquivo final já existe e não foi sobrescrito: `{rel}`. Esta revisão do "
        "trecho já está coletada — rode `verify` para conferir, ou gere uma prévia "
        "nova (com outro `preview --start` e `--end`) se quiser outro corte."
    )


# Páginas em que o yt-dlp lê título, canal e duração sem baixar mídia. Instagram fica
# de fora: a rota dele é o navegador, e um pedido solto ali só gasta bloqueio.
METADATA_PROVIDERS = ("youtube", "tiktok")


def fill_remote_metadata(c):
    """Preenche título, autoria e duração na hora do `resolve`, com um pedido só.

    Sem isto o candidato entrava com `title: "TikTok · 7312…"`, `creator: null` e
    `duration_s: null`, e o C2 — "título, canal, duração" — não tinha o que listar.
    Nada aqui é obrigatório: a página pode recusar, e a URL continua registrada.
    """
    if c.get("provider") not in METADATA_PROVIDERS or not c.get("source_url"):
        return c
    from .social import metadata

    try:
        found = metadata(c["source_url"])
    except (ValueError, OSError):
        return c
    if found.get("title"):
        c["title"] = found["title"]
    # O handle público é o que a pessoa reconhece; o nome de exibição vem junto.
    name = found.get("creator") or found.get("handle")
    if name:
        c["creator"]["name"] = name
    if found.get("handle"):
        c["creator"]["handle"] = found["handle"]
    if found.get("creator_url"):
        c["creator"]["url"] = found["creator_url"]
    if found.get("duration_s"):
        c["media"]["duration_s"] = found["duration_s"]
    return c


def preview_files(ledger, c):
    """Absolute paths of the preview artifacts that the Storyboard embeds for this item."""
    files = {}
    for key, name in (
        ("contact_sheet_path", "contact_sheet"),
        ("poster_path", "poster"),
        ("gif_path", "gif"),
    ):
        rel = c["preview"].get(key)
        files[name] = str((ledger.root / rel).resolve()) if rel else None
    files["review"] = str((ledger.root / "review.html").resolve())
    return files
