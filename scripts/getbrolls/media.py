import json, subprocess, os
from pathlib import Path


def run(args):
    try:
        return subprocess.run(
            args, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180
        ).stdout
    except (subprocess.SubprocessError, OSError) as e:
        raise ValueError(
            "Falha de mídia: confirme arquivo, intervalo e FFmpeg/ffprobe instalados."
        ) from e


def probe(path):
    d = json.loads(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise ValueError("Arquivo sem vídeo.")
    a, b = v.get("avg_frame_rate", "0/1").split("/")
    fps = float(a) / float(b) if float(b) else None
    return {
        "duration_s": float(d["format"].get("duration", v.get("duration", 0))),
        "width": v["width"],
        "height": v["height"],
        "fps": fps,
    }


def cut(src, dst, start, end):
    dst = Path(dst)
    if dst.exists():
        raise ValueError("Arquivo final já existe; nenhum arquivo foi sobrescrito.")
    tmp = dst.with_suffix(".part.mp4")
    try:
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(src),
                "-ss",
                str(start),
                "-t",
                str(end - start),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(tmp),
            ]
        )
        info = probe(tmp)
        if abs(info["duration_s"] - (end - start)) > max(0.25, 2 / (info["fps"] or 10)):
            raise ValueError("Duração do corte não corresponde ao intervalo aprovado.")
        run(["ffmpeg", "-v", "error", "-i", str(tmp), "-f", "null", "-"])
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def preview(src, dst, start, end):
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            str(start),
            "-t",
            str(end - start),
            "-i",
            str(src),
            "-vf",
            f"fps=4/{end - start},scale=480:-2,tile=2x2",
            "-frames:v",
            "1",
            str(dst),
        ]
    )
    if not Path(dst).exists():
        raise ValueError("Não foi possível criar a prévia.")


def review_preview(src, directory, stem, start, end, config):
    """Full selected interval, native aspect, static gallery and bounded GIF."""
    import math, tempfile

    directory = Path(directory)
    if end - start > config["max_seconds"]:
        raise ValueError(
            "Trecho excede GB_PREVIEW_MAX_SECONDS; selecione um insert menor ou ajuste a configuração."
        )
    # Stage every output before replacing any prior preview.
    with tempfile.TemporaryDirectory(dir=directory) as stage:
        stage = Path(stage)
        poster = stage / "poster.jpg"
        sheet = stage / "sheet.jpg"
        gif = stage / "preview.gif"
        base = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            str(start),
            "-t",
            str(end - start),
            "-i",
            str(src),
        ]
        scale = "scale='min(360,iw)':-2:flags=lanczos"
        run(base + ["-vf", scale, "-frames:v", "1", str(poster)])
        n = config["frames"]
        cols = min(4, n)
        rows = math.ceil(n / cols)
        # Sample at each bin midpoint; include the entire interval rather than its first frames.
        run(
            base
            + [
                "-vf",
                f"fps={n / (end - start)}:start_time=0,{scale},tile={cols}x{rows}:nb_frames={n}",
                "-frames:v",
                "1",
                str(sheet),
            ]
        )
        result = {
            "poster_path": "previews/" + stem + "-poster.jpg",
            "contact_sheet_path": "previews/" + stem + "-sheet.jpg",
            "gif_path": None,
            "config": dict(config),
            "warning": None,
        }
        files = [(poster, result["poster_path"]), (sheet, result["contact_sheet_path"])]
        if config["mode"] == "gif":
            w = config["width"]
            fps = config["fps"]
            colors = config["colors"]
            filt = f"fps={fps},scale='min({w},iw)':-2:flags=lanczos,split[a][b];[a]palettegen=max_colors={colors}:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle"
            run(base + ["-filter_complex", filt, "-loop", "0", str(gif)])
            size = gif.stat().st_size
            result["gif_bytes"] = size
            if size <= config["max_mb"] * 1000000:
                result["gif_path"] = "previews/" + stem + ".gif"
                files.append((gif, result["gif_path"]))
            else:
                result["warning"] = (
                    "GIF excedeu o limite de tamanho; entregue estático. Reduza largura/FPS ou aumente GB_GIF_MAX_MB e gere novamente."
                )
        for source, relative in files:
            os.replace(source, directory.parent / relative)
    return result


def image_preview(src, directory, stem):
    import tempfile

    directory = Path(directory)
    rel = "previews/" + stem + "-poster.jpg"
    with tempfile.TemporaryDirectory(dir=directory) as stage:
        dest = Path(stage) / "poster.jpg"
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(src),
                "-vf",
                "scale='min(720,iw)':-2",
                "-frames:v",
                "1",
                str(dest),
            ]
        )
        os.replace(dest, directory.parent / rel)
    return {
        "poster_path": rel,
        "contact_sheet_path": None,
        "gif_path": None,
        "warning": None,
    }


def copy_image(src, dst):
    import tempfile, shutil

    dst = Path(dst)
    if dst.exists():
        raise ValueError("Arquivo final já existe; não foi sobrescrito.")
    with tempfile.TemporaryDirectory(dir=dst.parent) as stage:
        tmp = Path(stage) / dst.name
        shutil.copyfile(src, tmp)
        probe(tmp)
        run(["ffmpeg", "-v", "error", "-i", str(tmp), "-f", "null", "-"])
        from .ledger import digest

        if digest(src) != digest(tmp):
            raise ValueError("Cópia da imagem não confere com o original.")
        os.replace(tmp, dst)
