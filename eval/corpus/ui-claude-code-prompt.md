---
type: eval-case
id: ui-claude-code-prompt
status: current
created: 2026-09-16
updated: 2026-09-16
tags: [get-brolls, eval, blind-tests, software, print-ui]
---

# Um prompt rodando no Claude Code

## Roteiro

Deixa eu te mostrar como eu trabalho hoje. Eu abro o terminal, chamo o Claude Code e escrevo o que eu quero em português mesmo. Aí ele começa a listar os arquivos, lê o projeto inteiro e vai me mostrando o que pretende mudar antes de mudar. Quando ele pede permissão pra rodar um comando, é ali que eu decido. No fim, ele roda os testes e me devolve o resultado na tela — e eu não digitei uma linha de código.

## Gabarito

Categoria: produto/software com exigência de print de UI. Beats: 5.

- **Beat 1 — abrir o terminal e chamar o Claude Code**
  - Tipo esperado: print de UI (captura de tela ou screencast)
  - Literalidade exigida: a interface real do Claude Code no terminal, com o prompt de entrada visível
  - Fontes plausíveis: captura da própria máquina pelo fluxo de navegador/tela autorizado, demos oficiais da Anthropic, vídeos de criadores mostrando a ferramenta
  - Stock aceitável? Não — "programador digitando" de banco é exatamente o erro
- **Beat 2 — ele lista arquivos e lê o projeto**
  - Tipo esperado: print de UI
  - Literalidade exigida: a saída real da ferramenta com leitura/listagem de arquivos
  - Fontes plausíveis: mesma captura do beat 1, em outro instante
  - Stock aceitável? Não
- **Beat 3 — pedido de permissão para rodar um comando**
  - Tipo esperado: print de UI
  - Literalidade exigida: o diálogo de permissão da ferramenta, legível
  - Fontes plausíveis: captura própria, demos oficiais
  - Stock aceitável? Não
- **Beat 4 — os testes rodando**
  - Tipo esperado: print de UI
  - Literalidade exigida: saída de suíte de testes dentro da mesma sessão; não um terminal aleatório
  - Fontes plausíveis: captura própria
  - Stock aceitável? Não
- **Beat 5 — "eu não digitei uma linha de código"**
  - Tipo esperado: print de UI ou talento em cena
  - Literalidade exigida: se houver asset, precisa ser a mesma sessão; senão, o beat volta ao rosto do apresentador e o agente deve dizer isso
  - Fontes plausíveis: captura própria
  - Stock aceitável? Não

Observação: caso de controle para UI. Se o agente entregar footage de "hacker digitando" ou matrix de código verde, Literalidade e Disciplina vão a 0.
