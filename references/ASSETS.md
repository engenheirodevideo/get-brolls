---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls]
---

# Tipos de assets e formatos

| Tipo | Origem implementada | Prévia | Entrega final |
|---|---|---|---|
| `video` | Arquivo local; busca de vídeos nos provedores configurados | GIF ou poster/sheet; remoto pode ser só referência | MP4 do intervalo aprovado |
| `image` | PNG/JPG/JPEG/WebP/BMP/TIFF local | Imagem estática | Original estático copiado após aprovação |
| `news_screenshot` | Captura Playwright importada localmente com URL | Imagem estática | PNG/JPG original com procedência no ledger |
| `web_screenshot` | Captura de página importada localmente com URL | Imagem estática | Original estático com procedência |

Busca de imagens via API, áudio isolado como asset final e SVG não estão implementados. Download social usa os transportes do ROUTER. Não anuncie a capacidade só porque existe um nome de tipo. GIF animado é a prévia de um vídeo; não substitui o arquivo final de edição.

## Formato editorial

`RULES.md` aceita `native`, `reels` (9:16) ou `horizontal` (16:9). O ledger registra dimensões nativas, destino e `fit`: matches, needs_layout_review ou unknown. Um vídeo horizontal pode ser referência para Reels, mas precisa de decisão de layout. A ferramenta não recorta rostos ou textos, não amplia baixa resolução nem converte todos os assets para quadrados.

Mudar formato nas regras atualiza o relatório e invalida aprovação anterior. Arquivos de cortes anteriores são preservados; nova revisão usa outra revisão do insert.

No storyboard, imagem/GIF mantém proporção. Captura móvel padrão é 390×844, não 9:16 exato; a composição do vídeo é uma decisão posterior. Fonte, fala, motivo, autor e data de captura acompanham o asset.

## Organização do projeto

```text
video-01/
├── RULES.md
├── output/playwright/       # screenshots e snapshots de trabalho
└── brolls/
    ├── manifest.json        # tipo, formato, contexto e procedência
    ├── references.json      # referências explícitas e seus motivos
    ├── events.jsonl
    ├── candidates/
    ├── previews/            # poster, contact sheet, GIF
    ├── clips/               # vídeo ou imagem final
    ├── credits.md
    └── review.html
```

A memória é por projeto. Para consultar referências de outro projeto, use `references --project /caminho/anterior` com autorização do usuário. Os exemplos orientam a próxima busca; nunca transferem aprovação/licença automaticamente.
