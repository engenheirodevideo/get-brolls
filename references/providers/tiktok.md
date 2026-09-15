---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, recovery]
---

# TikTok

Recebe URL completa `https://www.tiktok.com/@usuario/video/ID` e usa o extrator TikTok do yt-dlp para obter o intervalo. `resolve --url`, `preview`, revisão e `fetch` seguem o mesmo fluxo. Sem API key da plataforma.

Descubra a URL pelo navegador; não há busca global TikTok por palavra-chave implementada. Links encurtados precisam ser abertos no navegador para obter URL canônica. A existência do extrator não garante acesso a todo vídeo; teste a URL real e registre eventual exigência de sessão/indisponibilidade. Consulte QA para evidência desta versão.
