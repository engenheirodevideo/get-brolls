---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls]
---

# Storyboard V2

Entregável de revisão independente da landing page. `gb.py review` gera `brolls/review.html` com CSS e JavaScript próprios incorporados; a tipografia usa fontes do sistema; imagens/GIF ficam em `previews/`.

1. Resolva o original autorizado e use um `--shot` distinto por insert.
2. Execute `preview` com intervalo, `--narration` (fala exata, quando fornecida; omita se ausente) e `--reason` (motivo da fonte).
3. O topo mostra o insert em sua proporção; à direita, fonte e ações de revisão. Galeria sempre estática. O GIF anima só no quadro selecionado; clique para alternar estático/animação. A preferência de movimento reduzido é respeitada.
4. Revisor aprova, pede ajuste ou sugere fonte; ajustes exigem comentário. Exporte JSON para devolver decisões. O botão PDF gera versão estática dos quadros com fontes/comentários.
5. Importe com `import-review --by`. Projeto, IDs e assinatura do intervalo/fonte são validados. Mudança de intervalo invalida decisão anterior.
6. Só colete o corte final depois de aprovação humana e registro da permissão. Clips MP4 ficam separados do storyboard.

Configurações, presets e limitações estão no [README](../README.md). `preview` obtém mídia de trabalho remota nas rotas de aquisição implementadas; no Instagram por navegador, importe primeiro o MP4 unido. Um poster isolado, inclusive com `--reference-only`, não comprova movimento.

Configuração padrão: `GB_GIF_SCOPE=broll`. O print opcional da pessoa permanece estático. Para revisar composição pronta do mesmo insert, escolha `full` e forneça `--full-preview-file`. O objetivo continua ser decidir a direção da coleta; nenhuma montagem adicional é exigida.
