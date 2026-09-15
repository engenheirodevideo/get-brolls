---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, recovery]
---

# YouTube

Motor original: yt-dlp + FFmpeg, **sem API key**. `search --provider youtube` usa ytsearch. `resolve --url` aceita URL de vídeo/shorts; `preview` obtém o intervalo e gera GIF/contact sheet, mantendo aprovação pendente. `fetch` publica os bytes revisados após decisão humana e registro de condições do projeto.

Helpers originais em `scripts/broll/`: gb_search.sh, gb_contact.sh, gb_frame.sh, gb_fetch.sh, gb_verify.sh e gb_vertical.sh. Interface antiga usa VIDEO_ID; o CLI novo aceita URL. Configure EJS/runtime conforme INSTALL. Se o site exigir sessão ou negar mídia, reporte o erro real; não troque silenciosamente para API com chave.
