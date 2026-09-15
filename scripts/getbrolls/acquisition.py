"""Private working sources for review; final clips remain approval-gated."""
import hashlib
import os
from pathlib import Path
import tempfile
from .ledger import digest
from .media import probe


def prepare_source(ledger, candidate, start, end):
    c = candidate
    remote = c['provider'] != 'local'
    if not remote:
        return
    path = c.get('local_path')
    if path and Path(path).is_file():
        if digest(path) != c['local_sha256']:
            raise ValueError('Fonte de trabalho alterada; importe novamente antes de revisar.')
        offset = c.get('local_start_s', 0)
        duration = c.get('local_duration_s')
        if duration is not None and start >= offset and end <= offset + duration + .05:
            return
    cache = ledger.root.parent / '.getbrolls-sources'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as work:
        target = Path(work) / 'source.mp4'
        if c['acquisition'].get('method') == 'yt-dlp':
            from .social import download_segment
            download_segment(c['source_url'], target, start, end)
            offset = start
        elif c['acquisition'].get('method') == 'https':
            from .providers import refresh
            from .http import download
            fresh = refresh(c)
            download(fresh.get('media_url'), target)
            offset = 0
        else:
            raise ValueError('Esta fonte requer importação do original local.')
        info = probe(target)
        if end - offset > info['duration_s'] + .1:
            raise ValueError('Original não contém o intervalo solicitado.')
        sha = digest(target)
        final = cache / (hashlib.sha256(c['id'].encode()).hexdigest()[:16] + '-' + sha + '.mp4')
        if not final.exists():
            os.replace(target, final)
        elif digest(final) != sha:
            raise ValueError('Cache de mídia inconsistente; não foi sobrescrito.')
    c.update(local_path=str(final.resolve()), local_sha256=sha,
             local_start_s=offset, local_duration_s=info['duration_s'])
    c['media'].update(width=info['width'], height=info['height'], fps=info['fps'])
