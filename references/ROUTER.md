---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, recovery]
---

# Fontes e transportes

| Fonte | Descoberta | Aquisição |
|---|---|---|
| YouTube | ytsearch sem chave | yt-dlp, intervalo via FFmpeg |
| Instagram | navegador/URL | navegador captura vídeo+áudio; coletor de pares incluído; yt-dlp como outra rota |
| TikTok | navegador/URL completa | yt-dlp |
| Pexels | API, PEXELS_API_KEY | HTTPS e cache de original para prévia |
| Pixabay | API, PIXABAY_API_KEY; cache 24 h | HTTPS e cache de original para prévia |
| Commons / NASA | APIs sem chave | HTTPS |
| Local | resolve --file | arquivo local |

Fluxo único: descobrir → obter mídia de trabalho/mostrar sequência → revisão humana → corte final → verify. Prévia não equivale a aprovação. `--reference-only` é opção explícita para não adquirir mídia. Consulte o guia da fonte; Instagram começa em [INSTAGRAM-BROWSER](INSTAGRAM-BROWSER.md).

`providers` declara transporte/capacidades implementadas e configuração, não garantia de acesso universal. `auto` segue a ordem de fontes das regras e o intent. Prefira entidades literais quando o roteiro citar pessoa/produto/fato.
