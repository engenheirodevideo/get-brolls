---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, recovery, qa]
---

# Rotas e evidências — 15/09/2026

YouTube usa yt-dlp, sem YouTube Data API e sem API key. Pexels/Pixabay usam suas próprias APIs; Instagram usa o navegador e o coletor de dois canais; TikTok usa URL completa e yt-dlp.

| Fonte | Ensaio real | Resultado |
|---|---|---|
| YouTube | Busca NASA Artemis sem variável de chave; preview 0–3 s e repetição 3–5 s com yt-dlp instalado em pasta limpa | Busca/download/decodificação/GIF passaram, 1920×1080, 25fps; pendente de revisão |
| Instagram | Reel DcMXl1IPNtB; Chrome → dois streams → curl → coletor/FFmpeg | Download e merge completos: 50,226009 s, 1076×1912, H.264/AAC, 16.528.141 bytes; decodificação integral e GIF 5 s passaram |
| TikTok | @nasa/7665358680627399966 descoberto na busca Chrome, via yt-dlp instalado pelo INSTALL | Trecho 0–3 s: 576×1024/30fps, H.264/AAC, 269.139 bytes; decodificação e GIF passaram |
| Pexels | coffee, ID 31264406; busca/refresh/download | 1080×1920, 12 s, 1.682.522 bytes |
| Pixabay | coffee, ID 46989; busca/refresh/download | 1920×1080, 35 s, 12.258.063 bytes |
| Commons | earth, 1 resultado; refresh e HEAD | 200 video/webm; corpo não baixado nesse ensaio |
| NASA API | busca, refresh e HEAD | 200 video/mp4; corpo não baixado nesse ensaio |

## Procedência

- [YouTube — lançamento Artemis](https://www.youtube.com/watch?v=B_7EUmCxcvE): metadados públicos e trecho técnico; não houve aprovação editorial do agente.
- [Instagram — NASA Johnson](https://www.instagram.com/nasajohnson/reel/DcMXl1IPNtB/): Captura e download dos dois canais concluídos no Chrome logado, com o coletor desta pasta.
- [TikTok — NASA, Earthset](https://www.tiktok.com/@nasa/video/7665358680627399966): busca no navegador e aquisição do trecho passaram. A URL anterior @nasa_space9/7512513421288492334 aparece indisponível no próprio navegador; seu erro não certificava o estado de toda a plataforma.
- [Pexels — espresso, İsa Kılavuzoğlu](https://www.pexels.com/video/close-up-of-espresso-brewing-into-elegant-cup-31264406/).
- [Pixabay — 46989, NickyPe](https://pixabay.com/videos/id-46989/).

Pexels/Pixabay geraram GIFs de 5 s (1.030.433 / 720.171 bytes) e cortes técnicos de 2 s verificados com ffprobe e FFmpeg. As chaves fornecidas foram usadas só no processo; não integram código, documentação, configs distribuídos ou exemplos. Esses ensaios validam os adapters; não representam aprovação/licença inventada nem garantia de disponibilidade futura.

O coletor Instagram passou também ensaio de captura/CDN real nesta nova tentativa. Fixtures continuam cobrindo detecção de áudio duplicado e falha de transferência. Na captura real, o asset_id e a duração relacionaram os dois canais; seletores de faixa bytestart/byteend foram removidos sem alterar a assinatura. Configs privados não integram a skill.

## Dependências oficiais

[yt-dlp/EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS) exige runtime suportado; o setup completo usa Node 22+ e instala yt-dlp com extras `default`. [Playwright CLI](https://github.com/microsoft/playwright-cli) é instalada via npm, separadamente do código da skill. [Pexels](https://www.pexels.com/api/documentation/) e [Pixabay](https://pixabay.com/api/docs/) documentam suas APIs; documentação não substitui teste autenticado.

A correção NASA promove HTTP somente no host exato images-assets.nasa.gov antes da validação. `doctor --live` busca nas fontes disponíveis e faz refresh de bancos; sem --live, inspeciona o ambiente local.
