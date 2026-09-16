---
type: rules
status: template
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, user-preferences]
---

# Regras do usuário

Copie para o projeto com `gb.py init-rules --project ./video-01`. Edite o bloco JSON abaixo: ele é lido pelo CLI, sem executar código. O `.env` guarda parâmetros técnicos; este arquivo guarda suas escolhas editoriais. A skill nunca preenche uma declaração de responsabilidade em seu nome.

```json
{
  "version": 1,
  "asset_types": ["video", "image", "news_screenshot", "web_screenshot"],
  "video_format": "native",
  "preferred_providers": {
    "literal": ["youtube", "commons", "nasa"],
    "illustrative": ["pexels", "pixabay"]
  },
  "preferred_domains": [],
  "blocked_domains": [],
  "editorial_rules": [],
  "copyright": {
    "mode": "per_item_evidence",
    "responsible_person": null,
    "declaration": null
  },
  "browser": {
    "viewport": "mobile",
    "mobile_width": 390,
    "mobile_height": 844,
    "desktop_width": 1440,
    "desktop_height": 900,
    "full_page": false
  }
}
```

## O que você decide

- `asset_types`: vídeos, imagens, prints de notícia ou página web. GIF é formato de prévia de vídeo, não nova categoria editorial. Áudio e vetores não são suportados nesta versão.
- `video_format`: `native`, `reels` (9:16) ou `horizontal` (16:9). O relatório indica adequação; a skill preserva o enquadramento, sem cortar pessoas/textos automaticamente.
- `preferred_providers`: ordem real da busca automática para cada intenção. Somente provedores suportados; indisponíveis por falta de chave são informados.
- `preferred_domains`: sites a priorizar na pesquisa pelo navegador e na ordenação de resultados; `blocked_domains` exclui o domínio e subdomínios.
- `editorial_rules`: instruções para o agente, por exemplo “preservar data/manchete”, “não usar stock para representar a pessoa citada”. Texto editorial requer interpretação do agente; não é classificador visual automático.
- `copyright.mode`: `per_item_evidence` ou `user_declaration`. O segundo exige nome e declaração preenchidos pelo usuário e `permit --declaration` em cada asset. O registro identifica uma declaração do usuário, não uma licença verificada automaticamente. Nenhuma modalidade altera as condições da fonte nem comprova direitos por si só.
- `browser`: viewport móvel/desktop e tamanho de captura. Viewport móvel muda o layout; não simula sozinho hardware, touch e user-agent de um celular.

## Memória de referências

Use `remember --candidate ID --decision approved|rejected --reason "motivo" --by "Pessoa" --project ...`. Aprovação positiva precisa existir no ledger. `references --project ...` devolve exemplos e motivos; o agente deve consultar antes de novas buscas. Não copie automaticamente uma decisão de um asset para outro. Cada insert continua exigindo revisão própria.

Regras nunca autorizam o agente a ignorar autenticação, burlar paywall ou remover evidências da origem. O usuário escolhe materiais e assume as declarações que fornece; a ferramenta registra fonte, decisão e evidência sem incentivar uso indevido.
