"""Social acquisition using the existing yt-dlp/FFmpeg engine, without API keys."""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import executable_override, venv_override
from .http import ProviderError
from .runtime import record_warning, redact, stderr_tail

LAYOUTS = ("Scripts/yt-dlp.exe", "Scripts/yt-dlp", "bin/yt-dlp")
# Pauses between yt-dlp requests: (--sleep-requests, --sleep-interval, --max-sleep-interval).
DEFAULT_SLEEP = (1, 3, 8)


def sleep_settings():
    """GB_YTDLP_SLEEP as "requests,min,max" seconds; defaults keep the source unhurried."""
    raw = (os.environ.get("GB_YTDLP_SLEEP") or "").strip()
    if not raw:
        return DEFAULT_SLEEP
    parts = raw.split(",")
    try:
        values = tuple(int(part.strip()) for part in parts)
    except ValueError:
        values = ()
    if len(values) != 3 or any(v < 0 for v in values) or values[1] > values[2]:
        raise ValueError('GB_YTDLP_SLEEP: use "requests,min,max" em segundos inteiros, com min <= max.')
    return values


def local_ytdlp(root=None):
    pinned = executable_override("GB_YTDLP_PATH")
    if pinned:
        return Path(pinned)
    base = venv_override()
    explicit = base is not None
    if not explicit:
        root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
        base = root / ".venv"
    for relative in LAYOUTS:
        candidate = base / relative
        if candidate.is_file():
            return candidate
    if explicit:
        # Pin explícito é promessa: sem yt-dlp dentro, nada de voltar ao PATH.
        raise ValueError(
            f"GB_VENV_PATH: {base} não contém yt-dlp (procurado em "
            + ", ".join(LAYOUTS)
            + "). Instale o yt-dlp nessa venv ou remova a variável."
        )
    return None


def command():
    local = local_ytdlp()
    exe = str(local) if local else shutil.which("yt-dlp")
    if not exe:
        raise ProviderError(
            "yt-dlp ausente: execute bash scripts/install.sh (ou install.ps1) na raiz da skill/plugin; "
            "após /plugin update é preciso reinstalar. Confira com python3 scripts/gb.py doctor."
        )
    requests, low, high = sleep_settings()
    # --no-warnings would hide exactly the rate-limit/PO-token/fallback warnings we want to surface.
    args = [
        exe,
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--socket-timeout",
        "20",
        "--retries",
        "1",
        "--fragment-retries",
        "1",
        "--sleep-requests",
        str(requests),
        "--sleep-interval",
        str(low),
        "--max-sleep-interval",
        str(high),
    ]
    if shutil.which("deno"):
        args += ["--js-runtimes", "deno"]
    elif shutil.which("node"):
        args += ["--js-runtimes", "node"]
    return args


def _with_tail(message, stderr):
    tail = stderr_tail(stderr)
    return f"{message}; stderr: {tail}" if tail else message


# "login"/"sign in" alone is too broad (matches unrelated text); only these phrases mean auth is required.
_LOGIN_RE = re.compile(r"sign in to confirm|login required", re.IGNORECASE)
# Anchored to real HTTP 429 context, not any standalone "429" (e.g. an ffmpeg "fps= 429" counter).
_RATE_RE = re.compile(r"http error 429|429[:\s]+too many requests|rate.?limit", re.IGNORECASE)
# "\bremoved\b" alone also matched yt-dlp's own "removed temporary file" cleanup message;
# require it to describe the video itself, not an unrelated file operation.
_UNAVAILABLE_RE = re.compile(r"\bprivate\b|\bunavailable\b|\bvideo (?:has been |was )?removed\b", re.IGNORECASE)


def _classify_ytdlp_error(exc):
    stderr = exc.stderr or ""
    detail = stderr.lower()
    if _RATE_RE.search(detail):
        message = "limite de requisições da fonte (429); aguarde e tente de novo"
    elif "ip address is blocked" in detail:
        message = "A fonte bloqueou o IP desta rede para esse post; download não concluído."
    elif "not available in your country" in detail:
        message = "Vídeo bloqueado geograficamente (geo-block) para esta região."
    elif "requested format is not available" in detail:
        message = "Formato solicitado não está disponível para esta fonte."
    elif "unsupported url" in detail:
        message = "URL não suportada por yt-dlp."
    elif _LOGIN_RE.search(detail):
        message = "A fonte exige uma sessão de acesso. Use o navegador autorizado conforme o guia da plataforma."
    elif _UNAVAILABLE_RE.search(detail):
        message = "Vídeo indisponível, privado ou removido."
    else:
        message = "yt-dlp não concluiu a extração; confira disponibilidade do post e siga o guia da plataforma."
    return ProviderError(_with_tail(message, stderr))


def _extract_warnings(stderr):
    """WARNING lines, with indented continuation lines folded into the warning they wrap."""
    warnings = []
    for raw_line in (stderr or "").splitlines():
        if raw_line.strip().upper().startswith("WARNING"):
            warnings.append(raw_line.strip())
        elif raw_line[:1].isspace() and raw_line.strip() and warnings:
            # An indented line with no "WARNING" prefix of its own is a continuation of
            # the previous warning (yt-dlp wraps long warnings this way), not a new one.
            warnings[-1] = f"{warnings[-1]} {raw_line.strip()}"
    return [redact(w) for w in warnings]


def run(arguments, timeout=180):
    cmd = command() + arguments
    try:
        proc = subprocess.run(
            cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(f"yt-dlp excedeu {timeout}s; a fonte pode estar lenta ou bloqueando.") from exc
    except subprocess.CalledProcessError as exc:
        raise _classify_ytdlp_error(exc) from exc
    except FileNotFoundError as exc:
        name = exc.filename or (cmd[0] if cmd else "yt-dlp")
        raise ProviderError(
            f"{name} não encontrado: execute bash scripts/install.sh (ou install.ps1) na raiz da skill/plugin."
        ) from exc
    except PermissionError as exc:
        name = exc.filename or (cmd[0] if cmd else "yt-dlp")
        raise ProviderError(f"Permissão negada ao executar {name}.") from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise ProviderError(
            "yt-dlp não concluiu: confira dependências, disponibilidade do vídeo e sessão exigida pela fonte. Para Instagram, use o fluxo navegador → pares CDN descrito em docs/GUIDE.md."
        ) from exc
    return proc.stdout, _extract_warnings(proc.stderr)


def search(query, limit):
    raw, warnings = run(["--flat-playlist", "--dump-single-json", f"ytsearch{limit}:{query}"], timeout=60)
    for w in warnings:
        record_warning("YTDLP_WARNING", w)
    try:
        data = json.loads(raw)
        return [r for r in data.get("entries", []) if isinstance(r, dict)]
    except (ValueError, AttributeError):
        raise ProviderError("yt-dlp retornou metadados inválidos.") from None


SUBTITLE_LANGS = ("pt", "en")


def _language_from(name):
    """`probe.pt.vtt` → `pt`; `probe.pt-BR.vtt` → `pt-BR`."""
    parts = Path(name).name.split(".")
    return parts[-2] if len(parts) >= 3 else "und"


# Teto da lista de idiomas na resposta: o YouTube anuncia centenas de traduções
# automáticas, e despejar tudo isso afoga a informação que interessa.
MAX_SUBTITLE_LANGS = 10


def _write_private(path, text):
    """Cria o VTT já com 0600 e sem sobrescrever nada: `O_CREAT|O_EXCL`, como na biblioteca.

    Escrever e só depois chamar `chmod` deixaria uma janela de leitura pública, e
    escreveria através de um arquivo (ou link) plantado com esse nome.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with open(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
        encoding="utf-8",
    ) as stream:
        stream.write(text)


def _original_language(data):
    """Idioma falado de fato: o que o yt-dlp marca como "(Original)" ou `<code>-orig`."""
    automatic = data.get("automatic_captions") or {}
    for code in automatic:
        if str(code).endswith("-orig"):
            return code
    for code, tracks in automatic.items():
        for track in tracks or []:
            if "original" in str((track or {}).get("name") or "").lower():
                return code
    return None


def relevant_langs(data, langs):
    """Idiomas que valem listar, e quantos a fonte anuncia ao todo.

    Os pedidos em `langs` mais o original; nunca as centenas de traduções automáticas
    que o YouTube gera sob demanda. Sem nenhum desses, os primeiros da lista servem
    de amostra, para ninguém achar que a fonte não tem legenda.
    """
    manual = set(data.get("subtitles") or {})
    automatic = set(data.get("automatic_captions") or {})
    every = sorted(manual | automatic)
    listed = [code for code in langs if code in manual or code in automatic]
    original = _original_language(data)
    if original and original not in listed:
        listed.append(original)
    if not listed:
        listed = every[:MAX_SUBTITLE_LANGS]
    return listed[:MAX_SUBTITLE_LANGS], len(every)


def metadata(url):
    """Título, autoria e duração da página, num pedido só e sem baixar mídia.

    É o mínimo que o C2 precisa para listar canal e duração, e `probe_remote` seria
    caro demais aqui: ele ainda escreve as legendas em disco. Falhar não é erro — a
    URL continua registrável —, então o chamador recebe `{}` e segue.
    """
    from .providers import resolve

    # Só páginas reconhecidas, nunca uma URL qualquer vinda do chat.
    resolve(url)
    try:
        raw, warnings = run(["--dump-single-json", "--skip-download", "--", url], timeout=60)
    except (ProviderError, OSError) as exc:
        record_warning("YTDLP_WARNING", f"metadados não vieram desta página: {exc}")
        return {}
    for w in warnings:
        record_warning("YTDLP_WARNING", w)
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    duration = data.get("duration")
    # `uploader_id` do TikTok já vem como `@handle`; o do YouTube é o id do canal.
    handle = data.get("uploader_id") or data.get("channel_id")
    return {
        "title": data.get("title") or data.get("fulltitle") or None,
        "creator": data.get("uploader") or data.get("channel") or None,
        "handle": str(handle) if handle else None,
        "creator_url": data.get("uploader_url") or data.get("channel_url") or None,
        "duration_s": float(duration) if isinstance(duration, (int, float)) else None,
    }


def probe_remote(url, langs=SUBTITLE_LANGS, cache=None):
    """O que a fonte conta sobre si: duração, capítulos, legendas e descrição.

    Um único pedido ao yt-dlp, sem baixar vídeo, com as mesmas pausas de
    `GB_YTDLP_SLEEP` do resto da skill. O VTT das legendas fica na pasta privada
    `.getbrolls-sources/` com 0600, nunca dentro de `brolls/`.
    """
    from .providers import resolve

    # Só páginas reconhecidas, nunca uma URL qualquer vinda do chat.
    resolve(url)
    cache = Path(cache) if cache is not None else None
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        cache.chmod(0o700)
    subtitles = {}
    with tempfile.TemporaryDirectory(dir=str(cache) if cache else None) as work:
        info = Path(work) / "probe.info.json"
        # `--dump-single-json` implicaria `--simulate`, e em modo simulado o yt-dlp não
        # escreve arquivo nenhum: os `.vtt` nunca chegavam ao disco e o `inspect` voltava
        # sem uma única fala, por mais legendas que a fonte anunciasse. Com `--no-simulate`
        # os arquivos aparecem e o stdout deixa de trazer JSON, então os metadados vêm do
        # `.info.json` escrito ao lado das legendas.
        _, warnings = run(
            [
                # Uma faixa que falha (429 num idioma só, tradução que sumiu) não pode
                # derrubar a análise inteira: sem isto, o `pt` já baixado ia para o lixo
                # junto com o erro do `en`. O que falta é tratado logo abaixo.
                "--ignore-errors",
                "--no-simulate",
                "--skip-download",
                "--write-info-json",
                "--write-auto-subs",
                "--sub-langs",
                ",".join(langs),
                "--sub-format",
                "vtt",
                "-o",
                str(Path(work) / "probe.%(ext)s"),
                "--quiet",
                "--no-warnings",
                "--",
                url,
            ],
            timeout=60,
        )
        for w in warnings:
            record_warning("YTDLP_WARNING", w)
        if info.is_file():
            raw = info.read_text(encoding="utf-8", errors="replace")
        else:
            # `--ignore-errors` engole o motivo junto com o erro. Um segundo pedido, só
            # de metadados (simulado, sem escrever arquivo nenhum), devolve a
            # classificação de sempre — vídeo privado, 429, sessão exigida — em vez de um
            # "metadados inválidos" genérico, e ainda salva título, duração e capítulos.
            raw, more = run(["--dump-single-json", "--skip-download", "--", url], timeout=60)
            for w in more:
                record_warning("YTDLP_WARNING", w)
        try:
            data = json.loads(raw)
        except ValueError:
            raise ProviderError("yt-dlp retornou metadados inválidos.") from None
        if not isinstance(data, dict):
            raise ProviderError("yt-dlp retornou metadados inválidos.")
        found = {_language_from(v.name): v for v in sorted(Path(work).glob("*.vtt"))}
        # Na ordem pedida em `langs`: o primeiro idioma é o preferido quando dois
        # trazem os mesmos tempos, e é essa ordem que `candidate_windows` respeita.
        ordered = [found.pop(code) for code in langs if code in found]
        ordered += [found[code] for code in sorted(found)]
        for vtt in ordered:
            from .inspecting import parse_vtt, parse_vtt_language

            text = vtt.read_text(encoding="utf-8", errors="replace")
            # O cabeçalho `Language:` do próprio arquivo vale mais que o sufixo do
            # nome quando o sufixo não diz nada.
            language = _language_from(vtt.name)
            if language == "und":
                language = parse_vtt_language(text) or language
            destination = None
            if cache is not None:
                stem = hashlib.sha256(url.encode()).hexdigest()[:16]
                destination = cache / f"{stem}-{language}.vtt"
                _write_private(destination, text)
            subtitles[language] = {
                "path": str(destination) if destination else None,
                "cues": parse_vtt(text),
            }
    duration = data.get("duration")
    chapters = []
    for chapter in data.get("chapters") or []:
        if not isinstance(chapter, dict) or chapter.get("start_time") is None:
            continue
        chapters.append(
            {
                "start_s": float(chapter["start_time"]),
                "end_s": float(chapter["end_time"]) if chapter.get("end_time") is not None else None,
                "title": chapter.get("title") or "",
            }
        )
    listed, total = relevant_langs(data, langs)
    return {
        "url": url,
        "title": data.get("title"),
        "duration_s": float(duration) if isinstance(duration, (int, float)) else None,
        "chapters": chapters,
        "subtitle_langs": listed,
        # Quantas faixas a fonte anuncia ao todo; `subtitle_langs` mostra só as úteis.
        "subtitle_langs_total": total,
        "description": data.get("description") or "",
        # Só o que a fonte declara: serve para avisar sobre 360°/VR antes da prévia.
        "tags": [str(tag) for tag in (data.get("tags") or []) if tag],
        "subtitles": subtitles,
    }


def download_segment(url, target, start, end):
    from .media import probe
    from .media import run as media_run
    from .providers import resolve

    # Only recognized social pages, never a user-provided command or arbitrary URL.
    resolve(url)
    if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
        raise ProviderError("Intervalo inválido para download social.")
    target = Path(target)
    if target.exists():
        raise ProviderError("Destino existente; não foi sobrescrito.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
        output = Path(folder) / "source.mp4"
        _, warnings = run(
            [
                "-f",
                "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b",
                "--download-sections",
                f"*{start}-{end}",
                "--force-keyframes-at-cuts",
                "--merge-output-format",
                "mp4",
                "--remux-video",
                "mp4",
                "-o",
                str(output),
                "--",
                url,
            ]
        )
        for w in warnings:
            record_warning("YTDLP_WARNING", w)
        info = probe(output)
        if abs(info["duration_s"] - (end - start)) > max(0.25, 2 / (info["fps"] or 10)):
            raise ProviderError("O trecho social não corresponde ao intervalo solicitado.")
        media_run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"])
        # Exclusive publication also protects a target created while downloading.
        with output.open("rb") as source, target.open("xb") as dest:
            try:
                shutil.copyfileobj(source, dest)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
    return target


def doctor():
    return {
        "engine": "yt-dlp",
        "installed": bool(local_ytdlp() or shutil.which("yt-dlp")),
        "javascript_runtime": "deno" if shutil.which("deno") else "node" if shutil.which("node") else None,
        "youtube_api_key_required": False,
        "instagram": "Navegador/Playwright → configs vídeo+áudio → scripts/getbrolls/instagram_pairs.py",
        "scope": "Disponibilidade de executáveis; não comprova extração ao vivo nem versão/runtime EJS.",
    }
