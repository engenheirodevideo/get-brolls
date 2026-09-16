"""Social acquisition using the existing yt-dlp/FFmpeg engine, without API keys."""
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
from .http import ProviderError
from .config import executable_override, venv_override


def local_ytdlp(root=None):
    pinned = executable_override('GB_YTDLP_PATH')
    if pinned:
        return Path(pinned)
    base = venv_override()
    if base is None:
        root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
        base = root / '.venv'
    for relative in (
        'Scripts/yt-dlp.exe',
        'Scripts/yt-dlp',
        'bin/yt-dlp',
    ):
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return None


def command():
    local = local_ytdlp()
    exe = str(local) if local else shutil.which('yt-dlp')
    if not exe:
        raise ProviderError('yt-dlp ausente: siga GUIDE.md e instale requirements.txt.')
    args = [exe, '--ignore-config', '--no-playlist', '--no-progress', '--no-warnings',
            '--socket-timeout', '20', '--retries', '1', '--fragment-retries', '1']
    if shutil.which('deno'):
        args += ['--js-runtimes', 'deno']
    elif shutil.which('node'):
        args += ['--js-runtimes', 'node']
    return args


def run(arguments, timeout=180):
    try:
        return subprocess.run(command() + arguments, check=True, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout).stdout
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or '').lower()
        if 'ip address is blocked' in detail:
            raise ProviderError('A fonte bloqueou o IP desta rede para esse post; download não concluído.') from None
        if 'login' in detail or 'sign in' in detail:
            raise ProviderError('A fonte exige uma sessão de acesso. Use o navegador autorizado conforme o guia da plataforma.') from None
        raise ProviderError('yt-dlp não concluiu a extração; confira disponibilidade do post e siga o guia da plataforma.') from None
    except (subprocess.SubprocessError, OSError):
        raise ProviderError('yt-dlp não concluiu: confira dependências, disponibilidade do vídeo e sessão exigida pela fonte. Para Instagram, use o fluxo navegador → pares CDN descrito em GUIDE.md.') from None


def search(query, limit):
    raw = run(['--flat-playlist', '--dump-single-json', f'ytsearch{limit}:{query}'], timeout=60)
    try:
        data = json.loads(raw)
        return [r for r in data.get('entries', []) if isinstance(r, dict)]
    except (ValueError, AttributeError):
        raise ProviderError('yt-dlp retornou metadados inválidos.') from None


def download_segment(url, target, start, end):
    from .providers import resolve
    from .media import probe, run as media_run
    # Only recognized social pages, never a user-provided command or arbitrary URL.
    resolve(url)
    if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
        raise ProviderError('Intervalo inválido para download social.')
    target = Path(target)
    if target.exists():
        raise ProviderError('Destino existente; não foi sobrescrito.')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
        output = Path(folder) / 'source.mp4'
        run(['-f', 'bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b',
             '--download-sections', f'*{start}-{end}', '--force-keyframes-at-cuts',
             '--merge-output-format', 'mp4', '--remux-video', 'mp4',
             '-o', str(output), '--', url])
        info = probe(output)
        if abs(info['duration_s'] - (end-start)) > max(.25, 2/(info['fps'] or 10)):
            raise ProviderError('O trecho social não corresponde ao intervalo solicitado.')
        media_run(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 'null', '-'])
        # Exclusive publication also protects a target created while downloading.
        with output.open('rb') as source, target.open('xb') as dest:
            try:
                shutil.copyfileobj(source, dest)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
    return target


def doctor():
    return {'engine': 'yt-dlp', 'installed': bool(local_ytdlp() or shutil.which('yt-dlp')),
            'javascript_runtime': 'deno' if shutil.which('deno') else 'node' if shutil.which('node') else None,
            'youtube_api_key_required': False,
            'instagram': 'Navegador/Playwright → configs vídeo+áudio → scripts/getbrolls/instagram_pairs.py',
            'scope': 'Disponibilidade de executáveis; não comprova extração ao vivo nem versão/runtime EJS.'}
