#!/usr/bin/env python3
"""Baixa e mescla pares de configs curl de vídeo/áudio de Reels do Instagram.

Um navegador autorizado captura URLs diretas da CDN como arquivos de config curl, um
config somente vídeo e um config somente áudio. Este script baixa ou reaproveita as
partes, mescla com ffmpeg e verifica os streams/hash de áudio da saída.

Nunca imprime URLs assinadas da CDN. Lotes têm ritmo (pausa aleatória entre stems, teto
por execução) e param no primeiro HTTP 401/403/429 para nunca martelar uma conta enquanto a
CDN já está recusando. Três casos, porque esperar só serve a um deles: um 429 é limite de
ritmo (cooldown, espere e volte ao mesmo escopo); um 403 cuja URL assinada tem `oe` no
passado é URL expirada (falha do item, recapture pela mesma rota autorizada); qualquer outro
401/403 é recusa (o lote termina, sem cooldown e sem horário para voltar: diagnostique).
"""

# pylint: disable=missing-function-docstring,too-many-arguments,too-many-locals
# pylint: disable=raise-missing-from,too-many-statements,broad-exception-caught,unnecessary-lambda-assignment

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import random
import sys
import time
from pathlib import Path

if __package__:
    from . import effects, logs
    from ._paths import strict_exists
    from .instagram_common import (
        PACE_RE,
        STEM_RE,
        CollectError,
        _safe_item_id,
        die,
        parse_pace,
        run,
        sh_quote,
    )
    from .instagram_media import (
        BLOCKED_RE,
        OE_SKEW_S,
        _log_pair_stage,
        audio_hash,
        download_or_reuse,
        ensure_tool,
        ffprobe_json,
        merge_parts,
        publish_exclusive,
        signed_url_expired,
        verify_output,
    )
    from .instagram_queue import (
        RECORD_LOCK_WAIT_S,
        QueueNotRecordedError,
        QueueRecordBusyError,
        _queue_hold,
        record_queue_cooldown,
        record_queue_refusal,
    )
    from .instagram_urls import (
        CURL_ADDRESS_LIST,
        HOST_RE,
        OUTPUT_RE,
        URL_RE,
        curl_config_input,
        infer_output_for_stem,
        pair_configs,
        parse_curl_config,
        resolve_config_output,
        validate_media_url,
    )
    from .runtime import redact
    from .sdk import schemas
else:  # Executado diretamente como `python3 scripts/getbrolls/instagram_pairs.py`, per docs/GUIDE.md.
    # Também roda como `python -m getbrolls.instagram_pairs`; este ramo é só o modo script.
    # Rodado como arquivo, a pasta deste módulo entra em `sys.path`, e o `queue.py`/`http.py`
    # do pacote encobririam os da stdlib (`logging.handlers` importa `queue`).
    # Só este módulo, o ponto de entrada, faz esse ajuste: os irmãos `instagram_*` são sempre carregados
    # como `getbrolls.instagram_*` e usam importações relativas simples.
    _package_dir = Path(__file__).resolve().parent
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != _package_dir]
    sys.path.insert(0, str(_package_dir.parent))
    from getbrolls import effects, logs
    from getbrolls._paths import strict_exists
    from getbrolls.instagram_common import (
        PACE_RE,
        STEM_RE,
        CollectError,
        _safe_item_id,
        die,
        parse_pace,
        run,
        sh_quote,
    )
    from getbrolls.instagram_media import (
        BLOCKED_RE,
        OE_SKEW_S,
        _log_pair_stage,
        audio_hash,
        download_or_reuse,
        ensure_tool,
        ffprobe_json,
        merge_parts,
        publish_exclusive,
        signed_url_expired,
        verify_output,
    )
    from getbrolls.instagram_queue import (
        RECORD_LOCK_WAIT_S,
        QueueNotRecordedError,
        QueueRecordBusyError,
        _queue_hold,
        record_queue_cooldown,
        record_queue_refusal,
    )
    from getbrolls.instagram_urls import (
        CURL_ADDRESS_LIST,
        HOST_RE,
        OUTPUT_RE,
        URL_RE,
        curl_config_input,
        infer_output_for_stem,
        pair_configs,
        parse_curl_config,
        resolve_config_output,
        validate_media_url,
    )
    from getbrolls.runtime import redact
    from getbrolls.sdk import schemas

# Importing `logs` (above) registers a NullHandler on the shared `getbrolls` logger at
# module import time (see logs.py), which is what keeps Python's own "handler of last
# resort" from ever printing a bare record to stderr before `logs.configure()` runs (or
# when it never runs, e.g. this script invoked without --project). Verified in
# tests/test_logging_social.py: nothing reaches stderr from logging alone when
# unconfigured, at any level.
_log = logs.get("instagram_pairs")

# Public surface of this module: names that moved to the `instagram_*` siblings (re-exported here, still importable)
# plus the few defined here (process_one, build_parser, write_summary, run_batch, refuse_when_stopped, main, DEFAULT_*).
# Patch a moved function in the module that defines it: this module's copy of the name is only the one its own
# callers read.
__all__ = [
    "BLOCKED_RE",
    "CURL_ADDRESS_LIST",
    "DEFAULT_MAX_PER_RUN",
    "DEFAULT_PACE",
    "HOST_RE",
    "OE_SKEW_S",
    "OUTPUT_RE",
    "PACE_RE",
    "RECORD_LOCK_WAIT_S",
    "STEM_RE",
    "URL_RE",
    "CollectError",
    "QueueNotRecordedError",
    "QueueRecordBusyError",
    "audio_hash",
    "build_parser",
    "curl_config_input",
    "die",
    "download_or_reuse",
    "ensure_tool",
    "ffprobe_json",
    "infer_output_for_stem",
    "main",
    "merge_parts",
    "pair_configs",
    "parse_curl_config",
    "parse_pace",
    "process_one",
    "publish_exclusive",
    "record_queue_cooldown",
    "record_queue_refusal",
    "refuse_when_stopped",
    "resolve_config_output",
    "run",
    "run_batch",
    "sh_quote",
    "signed_url_expired",
    "validate_media_url",
    "verify_output",
    "write_summary",
]

DEFAULT_PACE = "20-60"
DEFAULT_MAX_PER_RUN = 25


def process_one(  # noqa: PLR0913 - existing size; one field per stem/config/output path the pair processor needs
    *,
    stem: str,
    video_config: Path,
    audio_config: Path,
    output: Path,
    parts_dir: Path,
    config_output_root: Path,
    force_download: bool,
    prefer_config_output: bool,
    copy_streams: bool,
    compute_audio_hash: bool = True,
) -> dict:
    if not STEM_RE.fullmatch(stem) or ".." in stem:
        die(f"stem de config inseguro: {stem}")
    if strict_exists(output):
        die(f"a saída já existe; nada foi sobrescrito: {output}")
    safe_stem = stem
    video_part = parts_dir / f"{safe_stem}_video.mp4"
    audio_part = parts_dir / f"{safe_stem}_audio.mp4"
    video_action = download_or_reuse(
        cfg_path=video_config,
        part_path=video_part,
        config_output_root=config_output_root,
        force_download=force_download,
        prefer_config_output=prefer_config_output,
        stage="video",
    )
    audio_action = download_or_reuse(
        cfg_path=audio_config,
        part_path=audio_part,
        config_output_root=config_output_root,
        force_download=force_download,
        prefer_config_output=prefer_config_output,
        stage="audio",
    )
    effects.mkdir(output.parent, parents=True, exist_ok=True)
    with effects.temp_dir(folder=output.parent) as stage:
        pending = Path(stage) / "merged.mp4"
        merge_started = time.monotonic()
        try:
            merge_parts(video_part, audio_part, pending, copy_streams=copy_streams)
        except BaseException:
            _log_pair_stage("merge", merge_started, status="error")
            raise
        # lenient: log field only: merged size, or None when the file is absent
        _log_pair_stage("merge", merge_started, status="ok", size=pending.stat().st_size if pending.exists() else None)
        validate_started = time.monotonic()
        try:
            verification = verify_output(pending, compute_audio_hash=compute_audio_hash)
        except BaseException:
            _log_pair_stage("validation", validate_started, status="error")
            raise
        _log_pair_stage("validation", validate_started, status="ok", size=verification.get("size"))
        publish_exclusive(pending, output)
    verification["output"] = str(output)
    verification.update(
        {
            "stem": stem,
            "video_config": str(video_config),
            "audio_config": str(audio_config),
            "video_part_action": video_action,
            "audio_part_action": audio_action,
        }
    )
    return verification


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    """The config inputs and the single-pair output paths."""
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config-dir", type=Path, help="Diretório contendo pares *_video.conf e *_audio.conf.")
    group.add_argument("--video-config", type=Path, help="Config curl de vídeo único.")
    parser.add_argument("--audio-config", type=Path, help="Config curl de áudio único; obrigatório com --video-config.")
    parser.add_argument("--output", type=Path, help="Único mp4 de saída; obrigatório com --video-config.")
    parser.add_argument("--output-dir", type=Path, help="Diretório de saída do lote; obrigatório com --config-dir.")


def _add_location_arguments(parser: argparse.ArgumentParser) -> None:
    """Where the parts, the config outputs and the queue live, and the output layout."""
    parser.add_argument(
        "--parts-dir",
        type=Path,
        default=Path("work/instagram_parts"),
        help="Diretório temporário/reaproveitado das partes de vídeo/áudio.",
    )
    parser.add_argument(
        "--config-output-root",
        type=Path,
        default=Path.cwd(),
        help="Raiz usada para resolver as linhas output= (relativas) dos configs curl.",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help=(
            "Raiz do projeto que guarda work/queue.json; registra o cooldown (429) ou a parada (401/403) e, "
            "com a fila parada, impede o lote de sair."
        ),
    )
    parser.add_argument(
        "--layout",
        choices=["auto", "flat", "student"],
        default="auto",
        help="Layout da saída do lote. auto preserva pastas por usuário para stems '<username>_<rank>_<code>'.",
    )


def _add_processing_arguments(parser: argparse.ArgumentParser) -> None:
    """Reuse, stream copy, the duplicate-audio check and the summary file."""
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Ignora arquivos de config output e partes existentes; baixa das URLs assinadas.",
    )
    parser.add_argument(
        "--no-prefer-config-output",
        action="store_true",
        help="Não reaproveita arquivos já apontados por output= nos configs curl.",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copia os streams de vídeo/áudio em vez de normalizar para h264 yuv420p + aac.",
    )
    parser.add_argument(
        "--fail-on-duplicate-audio",
        action="store_true",
        help="Falha o lote se duas saídas tiverem o mesmo hash SHA-256 do AAC extraído.",
    )
    parser.add_argument("--summary-json", type=Path, help="Grava o resumo em JSON; reescrito a cada stem.")


def _add_pace_arguments(parser: argparse.ArgumentParser) -> None:
    """Pacing, the per-run cap and what a failed stem does to the batch."""
    parser.add_argument(
        "--pace",
        default=DEFAULT_PACE,
        help=(
            "Pausa aleatória em segundos entre stems, como MIN-MAX (padrão 20-60); "
            "0 desabilita. Nunca antes do primeiro stem."
        ),
    )
    parser.add_argument(
        "--max-per-run",
        type=int,
        default=DEFAULT_MAX_PER_RUN,
        help="Processa no máximo N stems por execução (padrão 25); o resto é registrado como pulado.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help=(
            "Registra um stem com falha e continua em vez de interromper o lote. "
            "HTTP 401/403/429 sempre interrompe o lote."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    """Monta o parser de argumentos do comando de pares."""
    parser = argparse.ArgumentParser(
        description=(
            "Baixa/mescla pares de configs curl de vídeo+áudio de Reels do Instagram sem imprimir URLs assinadas."
        ),
        epilog=(
            "Lotes grandes: use --pace MIN-MAX (padrão 20-60s) e --max-per-run (padrão 25) para dividir "
            "o lote em execuções menores e manter o ritmo. O resumo em --summary-json é sempre gravado de forma "
            "incremental, com ou sem --continue-on-error; a flag só muda o que acontece após uma falha comum: "
            "sem ela o lote é interrompido (401/403/429 sempre interrompe, com ou sem a flag); com ela, o lote segue "
            "para o próximo stem. "
            "Veja a seção Instagram (navegador/Playwright, dois streams e pares CDN) em docs/GUIDE.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_source_arguments(parser)
    _add_location_arguments(parser)
    _add_processing_arguments(parser)
    _add_pace_arguments(parser)
    return parser


def write_summary(path: Path | None, summary: dict) -> None:
    """Grava o resumo incremental; um OSError vira WARNING e não interrompe o lote."""
    if path is None:
        return
    try:
        effects.mkdir(path.parent, parents=True, exist_ok=True)
        effects.write_text(path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"WARNING: não foi possível gravar --summary-json ({redact(exc)})", file=sys.stderr)


def _build_summary(
    total: int, results: list[dict], stopped_by: str | None, *, refusal_not_recorded: bool = False
) -> dict:
    counts = {"done": 0, "failed": 0, "skipped": 0}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "schema_version": schemas.version("instagram_summary"),
        "count": total,
        **counts,
        "results": list(results),
        "stopped_by": stopped_by,
        # True when a 401/403/429 could not be written to work/queue.json (the project was busy, or the
        # write failed: disk full, read-only folder): the next run would not know about the refusal.
        "refusal_not_recorded": refusal_not_recorded,
    }


def _skip(results: list[dict], stem: str, reason: str) -> None:
    results.append({"stem": stem, "status": "skipped", "reason": reason})


def _pause_before(stem: str, low: int, high: int) -> None:
    """Pausa aleatória para o lote nunca bater na CDN no ritmo de uma máquina."""
    pause = random.uniform(low, high)  # noqa: S311 - pacing jitter, not security
    print(f"-- pacing: waiting {pause:.1f}s before {stem}", file=sys.stderr)
    logs.event(_log, logging.DEBUG, "pace", wait_s=round(pause, 1))
    time.sleep(pause)


def _collect_one(stem: str, video_config: Path, audio_config: Path, output: Path, args) -> dict:
    return process_one(
        stem=stem,
        video_config=video_config,
        audio_config=audio_config,
        output=output,
        parts_dir=args.parts_dir,
        config_output_root=args.config_output_root,
        force_download=args.force_download,
        prefer_config_output=args.prefer_config_output,
        copy_streams=args.copy,
        compute_audio_hash=args.fail_on_duplicate_audio,
    )


def _recorded(args, record, *what):
    """Run a `record_queue_*` call; a refusal left out of the queue (busy lock, failed write) marks the batch
    (`args.queue_unrecorded`) and returns None."""
    try:
        return record(getattr(args, "project", None), *what)
    except QueueNotRecordedError as exc:
        args.queue_unrecorded = True
        print(
            f"ERROR: a recusa NÃO foi registrada em work/queue.json ({exc.cause}); "
            "registre-a à mão (queue --action mark --failed) antes de repetir o lote",
            file=sys.stderr,
        )
        return None


def _record_failure(
    results: list[dict], stem: str, exc: BaseException, args
) -> tuple[str | None, BaseException | None]:
    """Registra o stem com falha e diz se (e por que) o resto do lote para."""
    if isinstance(exc, CollectError):
        reason = exc.message
        cooldown = exc.cooldown
    else:
        # Uma exceção inesperada (não CollectError) não pode derrubar o resumo: registra
        # com o nome do tipo e trata como qualquer outra falha.
        reason = redact(f"{type(exc).__name__}: {exc}")
        cooldown = False
    results.append({"stem": stem, "status": "failed", "reason": reason})
    if getattr(exc, "refused", False):
        # Recusa de acesso: o lote termina pelo mesmo caminho de qualquer erro fatal, com ou sem
        # --continue-on-error. A fila ganha uma parada, nunca um cooldown nem um horário.
        status = getattr(exc, "http_status", None)
        stopped_at = _recorded(args, record_queue_refusal, reason, f"http_{status}")
        logs.event(_log, logging.WARNING, "refused", http_status=status, stop_recorded=bool(stopped_at))
        if stopped_at:
            print("-- parada registrada em queue.json (sem cooldown: esperar não libera o acesso)", file=sys.stderr)
        return "aborted", exc
    if cooldown:
        until = _recorded(args, record_queue_cooldown, reason)
        logs.event(
            _log,
            logging.WARNING,
            "blocked",
            http_status=getattr(exc, "http_status", None),
            cooldown_recorded=bool(until),
        )
        if until:
            print(f"-- cooldown recorded in queue.json until {until}", file=sys.stderr)
        return "cooldown", None
    if not args.continue_on_error:
        return "aborted", exc
    if getattr(exc, "expired", False):
        # URLs capturadas juntas vencem juntas: o resto do lote espera a recaptura, sem um pedido por item.
        return "recapture", None
    return None, None


def run_batch(pairs, *, output_for, args) -> dict:  # noqa: C901, PLR0912, PLR0915 - pace/cap/hold/summary loop
    """Processa stems em ordem com ritmo, teto por execução, resumo incremental e detecção de bloqueio."""
    # pylint: disable=too-many-branches  # the same loop the noqa above names
    low, high = parse_pace(args.pace)
    if args.max_per_run < 1:
        die("--max-per-run deve ser pelo menos 1")
    results: list[dict] = []
    stop_reason: str | None = None
    stopped_by: str | None = None
    fatal: BaseException | None = None
    processed = 0

    def snapshot() -> dict:
        return _build_summary(
            len(pairs), results, stopped_by, refusal_not_recorded=bool(getattr(args, "queue_unrecorded", False))
        )

    try:
        # Valida que o caminho do resumo é gravável antes do primeiro stem.
        write_summary(args.summary_json, snapshot())
        for stem, video_config, audio_config in pairs:
            if stop_reason:
                _skip(results, stem, stop_reason)
                continue
            if processed >= args.max_per_run:
                stopped_by = stopped_by or "max-per-run"
                _skip(results, stem, "max-per-run")
                write_summary(args.summary_json, snapshot())
                continue
            item_id = _safe_item_id(stem)
            logs.event(_log, logging.INFO, "pair_item_start", shortcode=item_id)
            result: dict = {}
            try:
                # Before the pause and the per-run slot: an unsafe stem (`die`) fails this item
                # at once, like any other per-item error, instead of aborting the whole batch.
                output = output_for(stem)
                if processed and high > 0:
                    _pause_before(stem, low, high)
                # Right before the request, after the pause: another process may have written a stop or a
                # cooldown to queue.json since the last item.
                held = _queue_hold(args) if processed else None
                if held is None:
                    processed += 1
                    print(f"== {stem} -> {output} ==", file=sys.stderr)
                    result = _collect_one(stem, video_config, audio_config, output, args)
            except Exception as exc:  # noqa: BLE001 - one item's failure must not kill the batch
                stop_reason, fatal = _record_failure(results, stem, exc, args)
                if stop_reason in ("cooldown", "recapture"):
                    stopped_by = stop_reason
                logs.event(_log, logging.INFO, "pair_item_end", shortcode=item_id, status="failed")
            else:
                if held is not None:
                    stop_reason, fatal = held[0], CollectError(held[1])
                    stopped_by = "cooldown" if stop_reason == "cooldown" else stopped_by
                    _skip(results, stem, stop_reason)
                    logs.event(_log, logging.INFO, "pair_item_end", shortcode=item_id, status="skipped")
                else:
                    results.append({**result, "status": "done"})
                    logs.event(_log, logging.INFO, "pair_item_end", shortcode=item_id, status="done")
            write_summary(args.summary_json, snapshot())
    finally:
        write_summary(args.summary_json, snapshot())
    summary = snapshot()
    if fatal is None and summary["refusal_not_recorded"]:
        fatal = CollectError("a recusa não foi registrada em work/queue.json; o lote termina com falha")
    logs.event(
        _log,
        logging.INFO,
        "pair_batch_summary",
        count=summary["count"],
        done=summary["done"],
        failed=summary["failed"],
        skipped=summary["skipped"],
        stopped_by=stopped_by,
    )
    if fatal is not None:
        raise fatal
    return summary


def refuse_when_stopped(pairs, args) -> None:
    """Lê `work/queue.json` (só leitura) antes de qualquer pedido: parada ou cooldown em vigor, o lote não sai.

    Uma fila ilegível, ou o módulo da fila indisponível, também recusa: sem saber, não se pede.
    """
    held = _queue_hold(args)
    if held is None:
        return
    kind, message = held
    results = [{"stem": stem, "status": "skipped", "reason": kind} for stem, _video, _audio in pairs]
    write_summary(args.summary_json, _build_summary(len(pairs), results, "cooldown" if kind == "cooldown" else None))
    die(message)


def _plan(args):
    """The tools the batch needs, then its `(stem, video, audio)` pairs and where each output goes."""
    ensure_tool("curl")
    ensure_tool("ffmpeg")
    ensure_tool("ffprobe")
    if args.video_config:
        if not args.audio_config or not args.output:
            die("--audio-config e --output são obrigatórios com --video-config")
        stem = args.video_config.name.removesuffix("_video.conf")
        return [(stem, args.video_config, args.audio_config)], lambda _stem: args.output
    if not args.output_dir:
        die("--output-dir é obrigatório com --config-dir")
    return pair_configs(args.config_dir), lambda stem: infer_output_for_stem(stem, args.output_dir, args.layout)


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.project is not None:
        # Standalone entry point (also the shared entry when driven programmatically as
        # `getbrolls.instagram_pairs.main()`): only configure logging when the project
        # root is known and this run is never read-only (it always writes media). With
        # no --project, the logger stays unconfigured — safe per logs.py's own
        # NullHandler guard (see the module docstring above), no file/stderr side effects.
        logs.configure(str(args.project), read_only=False)
    args.prefer_config_output = not args.no_prefer_config_output

    try:
        # Inside the `try`: a missing tool or argument ends like any batch error (exit 1 and
        # the message), never as a traceback.
        pairs, output_for = _plan(args)
        refuse_when_stopped(pairs, args)
        summary = run_batch(pairs, output_for=output_for, args=args)
        results = [item for item in summary["results"] if item["status"] == "done"]

        if args.fail_on_duplicate_audio and len(results) > 1:
            seen: dict[str, str] = {}
            for item in results:
                h = item["audio_hash_sha256"]
                if h in seen:
                    die(f"hash de áudio duplicado detectado: {item['output']} e {seen[h]} compartilham {h}")
                seen[h] = item["output"]
    except CollectError as exc:
        print(f"-- lote interrompido: {exc.message}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # noqa: BLE001 - batch must end with a clean message, not a raw traceback
        print(f"-- lote interrompido: {type(exc).__name__}: {redact(str(exc))}", file=sys.stderr)
        return 1

    stopped_by = summary.get("stopped_by")
    print(
        f"-- lote concluído: {summary['done']} feito(s), {summary['failed']} falha(s), "
        f"{summary['skipped']} pulado(s); parado por: {stopped_by or 'nada (lote completo)'}",
        file=sys.stderr,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    """Run the batch and release the log file when it ends, however it ends."""
    for stream in (sys.stdout, sys.stderr):
        # UTF-8 like `getbrolls` itself: a Windows pipe defaults to cp1252, and the
        # help and the errors are in Portuguese. TextIO does not declare `reconfigure`.
        with contextlib.suppress(AttributeError, OSError):
            stream.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]
    try:
        return _main(argv)
    finally:
        logs.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
