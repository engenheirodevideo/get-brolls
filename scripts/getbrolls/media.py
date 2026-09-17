import hashlib, json, subprocess, os, time
from pathlib import Path
from .config import tool_path
from .runtime import record_warning, stderr_tail


def run(args):
    name = args[0]
    args = [tool_path(name), *args[1:]]
    try:
        return subprocess.run(
            args, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180
        ).stdout
    except FileNotFoundError as e:
        # Executável ausente é outro problema que arquivo ou intervalo inválido.
        raise ValueError(
            f"{name} não encontrado: instale FFmpeg/ffprobe ou aponte GB_FFMPEG_PATH/GB_FFPROBE_PATH; "
            "verifique python3 scripts/gb.py doctor."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ValueError(
            f"{name} excedeu 180s; confirme arquivo e intervalo ou tente novamente."
        ) from e
    except subprocess.CalledProcessError as e:
        tail = stderr_tail(e.stderr)
        raise ValueError(
            f"{name} falhou (exit {e.returncode}); verifique python3 scripts/gb.py doctor."
            + (f" stderr: {tail}" if tail else "")
        ) from e
    except (subprocess.SubprocessError, OSError) as e:
        raise ValueError(
            "Falha de mídia: confirme arquivo e intervalo; verifique python3 scripts/gb.py doctor."
        ) from e


_DRAWTEXT = {}


def _cache_dir():
    return Path(os.environ.get("GETBROLLS_CACHE_DIR", str(Path.home() / ".cache" / "getbrolls")))


def _drawtext_cache_path(ffmpeg_path):
    """Keyed by ffmpeg path + mtime so a replaced binary invalidates the cache."""
    try:
        mtime = Path(ffmpeg_path).stat().st_mtime
    except OSError:
        mtime = 0
    key = hashlib.sha256(f"{ffmpeg_path}:{mtime}".encode()).hexdigest()
    return _cache_dir() / f"drawtext-{key}.json"

# Fontes TrueType habituais por sistema; GB_FONT_FILE sempre vence.
DEFAULT_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
)


def drawtext_available():
    """True when the resolved ffmpeg was built with the drawtext filter (libfreetype).

    Persisted per-process (`_DRAWTEXT`) and on disk under `GETBROLLS_CACHE_DIR`, keyed by the
    resolved ffmpeg path + mtime, so every CLI process doesn't rerun `ffmpeg -filters`.
    """
    try:
        name = tool_path("ffmpeg")
    except ValueError:
        # Pin inválido é reportado pelo doctor como item faltante, não aqui.
        return False
    if name in _DRAWTEXT:
        return _DRAWTEXT[name]
    cache_path = _drawtext_cache_path(name)
    try:
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            _DRAWTEXT[name] = bool(cached.get("available"))
            return _DRAWTEXT[name]
    except (OSError, ValueError):
        pass
    try:
        listing = run(["ffmpeg", "-hide_banner", "-filters"])
    except ValueError:
        # The probe itself failed (missing/broken ffmpeg) — different from a working ffmpeg
        # that simply lacks the filter; the caller should know sondagem failed. This
        # process-level False is NOT persisted to disk: a real determination (probe ran and
        # found no filter) must not be confused with "we couldn't even ask".
        record_warning(
            "FFMPEG_PROBE_FAILED",
            "Não foi possível sondar os filtros do ffmpeg; drawtext tratado como indisponível.",
        )
        return False
    available = " drawtext " in listing
    _DRAWTEXT[name] = available
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = cache_path.with_suffix(".tmp")
        temp.write_text(json.dumps({"available": available, "checked_at": time.time()}), encoding="utf-8")
        temp.replace(cache_path)
    except OSError:
        pass
    return available


def find_font():
    """Absolute TrueType font for sheet labels, or None; GB_FONT_FILE must exist when set."""
    pinned = os.environ.get("GB_FONT_FILE", "").strip()
    if pinned:
        path = Path(pinned).expanduser()
        if not path.is_file():
            raise ValueError(
                f"GB_FONT_FILE não aponta para uma fonte existente: {pinned}"
            )
        return str(path.resolve())
    for candidate in DEFAULT_FONTS:
        if Path(candidate).is_file():
            return str(Path(candidate).resolve())
    return None


def _filter_path(path):
    """Escape a path for use inside an ffmpeg filter option (colons, backslashes)."""
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def clock(seconds):
    """Seconds → M:SS.s for banners and captions (59 → 0:59.0, 122.4 → 2:02.4)."""
    minutes, rest = divmod(float(seconds), 60)
    return f"{int(minutes)}:{rest:04.1f}"


def frame_times(start, end, n):
    """Source timestamps sampled by fps=n/(end-start) with start_time=0: bin starts."""
    step = (end - start) / n
    return [round(start + i * step, 3) for i in range(n)]


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


def review_preview(src, directory, stem, start, end, config, label=None):
    """Full selected interval, native aspect, static gallery and bounded GIF.

    The contact sheet follows the original gb_contact.sh: evenly sampled frames tiled
    with padding, and, when ffmpeg has drawtext plus a font, a 1-based index per cell and
    a banner with title, id and window. Without drawtext the sheet is plain and the
    Storyboard prints the per-cell legend from ``frame_times_s`` instead.
    """
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
        label = label or {}
        # Source-time window for labels: the working file may start mid-source.
        offset = float(label.get("offset") or 0)
        src_start, src_end = start + offset, end + offset
        font = find_font() if drawtext_available() else None
        cell = ""
        banner = ""
        if font:
            font_opt = _filter_path(font)
            cell = (
                f"drawtext=fontfile='{font_opt}':text='%{{eif\\:n+1\\:d}}':x=8:y=8:fontsize=28:"
                "fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=6,"
            )
            title_file = stage / "title.txt"
            title = str(label.get("title") or "").replace("\n", " ").strip()
            ident = str(label.get("id") or "")
            title_file.write_text(
                f"{title}\n[{ident}]  corte {clock(src_start)}–{clock(src_end)}"
                + (f" de {clock(label['duration'])}" if label.get("duration") else "")
                + f"  ·  {n} quadros",
                encoding="utf-8",
            )
            banner = (
                f",pad=iw:ih+72:0:72:color=0x0b0b0b,drawtext=fontfile='{font_opt}':"
                f"textfile='{_filter_path(title_file)}':x=16:y=12:fontsize=26:"
                "fontcolor=white:line_spacing=8"
            )
        # Sample one frame per bin start across the whole interval, never only its head.
        run(
            base
            + [
                "-vf",
                f"fps={n / (end - start)}:start_time=0,scale=480:-2:flags=lanczos,{cell}"
                f"tile={cols}x{rows}:nb_frames={n}:padding=10:margin=10:color=0x111111{banner}",
                "-frames:v",
                "1",
                str(sheet),
            ]
        )
        result = {
            "poster_path": "previews/" + stem + "-poster.jpg",
            "contact_sheet_path": "previews/" + stem + "-sheet.jpg",
            "gif_path": None,
            "frame_times_s": frame_times(src_start, src_end, n),
            "sheet_grid": [cols, rows],
            "sheet_labels": bool(font),
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
