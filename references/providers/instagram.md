---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, recovery]
---

# Instagram

Rota principal: **navegador/Playwright → URL CDN de vídeo + URL CDN de áudio → curl → FFmpeg → ffprobe**. Leia [INSTAGRAM-BROWSER](../INSTAGRAM-BROWSER.md) e use o coletor incluído em `scripts/instagram/ig_curl_pair_downloader.py`.

O MP4 unido entra com `resolve --file --source-url --creator --shot`; depois preview/review/fetch. O browser captura os streams; o script baixa/junta. Não exige chave da API oficial Instagram. A página pode exigir sessão. yt-dlp também está disponível via URL completa, se funcionar para aquele post; falha dessa rota não remove o fluxo de navegador.
