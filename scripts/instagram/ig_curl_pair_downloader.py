#!/usr/bin/env python3
"""Download/merge Instagram Reel video+audio curl config pairs.

This tool preserves the validated 2026-07-08 Codex workflow:
Instagram browser/devtools captures direct CDN URLs as curl config files, one
video-only config and one audio-only config. This script downloads or reuses the
parts, merges them with ffmpeg, and verifies output streams/audio hashes.

It intentionally never prints signed CDN URLs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

URL_RE = re.compile(r'^\s*url\s*=\s*"(.*)"\s*$')
OUTPUT_RE = re.compile(r'^\s*output\s*=\s*"(.*)"\s*$')


def die(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def run(cmd: list[str], *, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    if not quiet:
        print("+ " + " ".join(sh_quote(x) for x in cmd), file=sys.stderr)
    return subprocess.run(cmd, text=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def sh_quote(value: str) -> str:
    if not value:
        return "''"
    if re.match(r"^[A-Za-z0-9_./:=@%+-]+$", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def parse_curl_config(path: Path) -> dict[str, str | None]:
    text = path.read_text(errors="replace")
    url: str | None = None
    output: str | None = None
    for line in text.splitlines():
        url_match = URL_RE.match(line)
        if url_match:
            url = url_match.group(1)
            continue
        output_match = OUTPUT_RE.match(line)
        if output_match:
            output = output_match.group(1)
    if not url:
        die(f"missing url line in curl config: {path}")
    return {"url": url, "output": output}


def resolve_config_output(config_output: str | None, root: Path) -> Path | None:
    if not config_output:
        return None
    output = Path(config_output)
    if output.is_absolute():
        return output
    return root / output


def infer_output_for_stem(stem: str, output_dir: Path, layout: str) -> Path:
    if layout == "flat":
        return output_dir / f"{stem}.mp4"
    student_match = re.match(r"^(.+)_([0-9]{2})_(.+)$", stem)
    is_student_stem = student_match is not None and not re.match(r"^[0-9]{2}$", student_match.group(1))
    if layout == "student" or (layout == "auto" and is_student_stem):
        if not student_match:
            die(f"layout=student requires stem '<username>_<rank>_<code>', got: {stem}")
        username, rank, code = student_match.groups()
        return output_dir / username / f"{rank}_{code}.mp4"
    return output_dir / f"{stem}.mp4"


def pair_configs(config_dir: Path) -> list[tuple[str, Path, Path]]:
    video_configs = sorted(config_dir.glob("*_video.conf"))
    pairs: list[tuple[str, Path, Path]] = []
    for video_config in video_configs:
        stem = video_config.name[: -len("_video.conf")]
        audio_config = config_dir / f"{stem}_audio.conf"
        if not audio_config.exists():
            die(f"missing audio config for {stem}: {audio_config}")
        pairs.append((stem, video_config, audio_config))
    if not pairs:
        die(f"no *_video.conf files found in {config_dir}")
    return pairs


def ensure_tool(name: str) -> None:
    if shutil.which(name) is None:
        die(f"required command not found: {name}")


def download_or_reuse(
    *,
    cfg_path: Path,
    part_path: Path,
    config_output_root: Path,
    force_download: bool,
    prefer_config_output: bool,
) -> str:
    parsed = parse_curl_config(cfg_path)
    config_output = resolve_config_output(parsed["output"], config_output_root)

    if prefer_config_output and not force_download and config_output and config_output.exists() and config_output.stat().st_size > 0:
        part_path.parent.mkdir(parents=True, exist_ok=True)
        if config_output.resolve() != part_path.resolve():
            shutil.copy2(config_output, part_path)
        return "reused-config-output"

    if part_path.exists() and part_path.stat().st_size > 0 and not force_download:
        return "reused-part"

    part_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep valid existing parts until the replacement transfer succeeds.
    with tempfile.TemporaryDirectory(dir=part_path.parent) as stage:
        pending = Path(stage) / "download.part"
        cmd = ["curl", "--fail", "--location", "--retry", "3",
               "--retry-all-errors", "--connect-timeout", "20", "--max-time", "180",
               "--output", str(pending), str(parsed["url"])]
        print(f"+ curl <url-from {cfg_path}> --output {part_path}", file=sys.stderr)
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        except (subprocess.SubprocessError, OSError):
            die(f"curl failed for config {cfg_path}; recapture expired/forbidden URLs and retry")
        if not pending.is_file() or pending.stat().st_size == 0:
            die(f"empty media for config {cfg_path}")
        os.replace(pending, part_path)
    return "downloaded"


def merge_parts(video_part: Path, audio_part: Path, output: Path, *, copy_streams: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if copy_streams:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video_part), "-i", str(audio_part), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(output)]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(video_part),
            "-i",
            str(audio_part),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output),
        ]
    run(cmd, quiet=True)


def ffprobe_json(path: Path) -> dict:
    result = run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ], quiet=True)
    return json.loads(result.stdout)


def audio_hash(path: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="getbrolls-ig-audiohash-") as tmp:
        audio = Path(tmp) / "audio.aac"
        run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c", "copy", str(audio)], quiet=True)
        h = hashlib.sha256()
        with audio.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()


def verify_output(path: Path) -> dict:
    data = ffprobe_json(path)
    streams = data.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not video_streams:
        die(f"output has no video stream: {path}")
    if not audio_streams:
        die(f"output has no audio stream: {path}")
    v0 = video_streams[0]
    a0 = audio_streams[0]
    return {
        "output": str(path),
        "size": int(data.get("format", {}).get("size", 0)),
        "duration": float(data.get("format", {}).get("duration", 0.0)),
        "video_codec": v0.get("codec_name"),
        "pix_fmt": v0.get("pix_fmt"),
        "width": v0.get("width"),
        "height": v0.get("height"),
        "audio_codec": a0.get("codec_name"),
        "audio_hash_sha256": audio_hash(path),
    }


def process_one(
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
) -> dict:
    safe_stem = stem.replace("/", "_")
    video_part = parts_dir / f"{safe_stem}_video.mp4"
    audio_part = parts_dir / f"{safe_stem}_audio.mp4"
    video_action = download_or_reuse(
        cfg_path=video_config,
        part_path=video_part,
        config_output_root=config_output_root,
        force_download=force_download,
        prefer_config_output=prefer_config_output,
    )
    audio_action = download_or_reuse(
        cfg_path=audio_config,
        part_path=audio_part,
        config_output_root=config_output_root,
        force_download=force_download,
        prefer_config_output=prefer_config_output,
    )
    merge_parts(video_part, audio_part, output, copy_streams=copy_streams)
    verification = verify_output(output)
    verification.update({
        "stem": stem,
        "video_config": str(video_config),
        "audio_config": str(audio_config),
        "video_part_action": video_action,
        "audio_part_action": audio_action,
    })
    return verification


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download/merge Instagram Reel video+audio curl config pairs without printing signed URLs.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config-dir", type=Path, help="Directory containing *_video.conf and *_audio.conf pairs.")
    group.add_argument("--video-config", type=Path, help="Single video curl config.")
    parser.add_argument("--audio-config", type=Path, help="Single audio curl config; required with --video-config.")
    parser.add_argument("--output", type=Path, help="Single output mp4; required with --video-config.")
    parser.add_argument("--output-dir", type=Path, help="Batch output directory; required with --config-dir.")
    parser.add_argument("--parts-dir", type=Path, default=Path("work/instagram_parts"), help="Temporary/reused video/audio part directory.")
    parser.add_argument("--config-output-root", type=Path, default=Path.cwd(), help="Root used to resolve relative output= lines from curl configs.")
    parser.add_argument("--layout", choices=["auto", "flat", "student"], default="auto", help="Batch output layout. auto preserves username folders for '<username>_<rank>_<code>' stems.")
    parser.add_argument("--force-download", action="store_true", help="Ignore existing config output files and existing parts; download from signed URLs.")
    parser.add_argument("--no-prefer-config-output", action="store_true", help="Do not reuse existing files pointed to by output= in curl configs.")
    parser.add_argument("--copy", action="store_true", help="Stream-copy video/audio instead of normalizing to h264 yuv420p + aac.")
    parser.add_argument("--fail-on-duplicate-audio", action="store_true", help="Fail batch if two outputs have the same extracted AAC SHA-256 hash.")
    parser.add_argument("--summary-json", type=Path, help="Write summary JSON file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_tool("curl")
    ensure_tool("ffmpeg")
    ensure_tool("ffprobe")

    prefer_config_output = not args.no_prefer_config_output
    results: list[dict] = []

    if args.video_config:
        if not args.audio_config or not args.output:
            die("--audio-config and --output are required with --video-config")
        stem = args.video_config.name.removesuffix("_video.conf")
        results.append(process_one(
            stem=stem,
            video_config=args.video_config,
            audio_config=args.audio_config,
            output=args.output,
            parts_dir=args.parts_dir,
            config_output_root=args.config_output_root,
            force_download=args.force_download,
            prefer_config_output=prefer_config_output,
            copy_streams=args.copy,
        ))
    else:
        if not args.output_dir:
            die("--output-dir is required with --config-dir")
        for stem, video_config, audio_config in pair_configs(args.config_dir):
            output = infer_output_for_stem(stem, args.output_dir, args.layout)
            print(f"== {stem} -> {output} ==", file=sys.stderr)
            results.append(process_one(
                stem=stem,
                video_config=video_config,
                audio_config=audio_config,
                output=output,
                parts_dir=args.parts_dir,
                config_output_root=args.config_output_root,
                force_download=args.force_download,
                prefer_config_output=prefer_config_output,
                copy_streams=args.copy,
            ))

    if args.fail_on_duplicate_audio and len(results) > 1:
        seen: dict[str, str] = {}
        for item in results:
            h = item["audio_hash_sha256"]
            if h in seen:
                die(f"duplicate audio hash detected: {item['output']} and {seen[h]} share {h}")
            seen[h] = item["output"]

    summary = {"count": len(results), "results": results}
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
