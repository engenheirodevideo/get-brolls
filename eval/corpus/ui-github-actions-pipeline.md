---
type: eval-case
id: ui-github-actions-pipeline
status: current
created: 2026-09-16
updated: 2026-09-16
tags: [get-brolls, eval, blind-tests, software, print-ui]
---

# O pipeline verde no GitHub Actions

## Roteiro

Antigamente a gente descobria que quebrou o projeto quando o cliente ligava. Hoje é diferente: você sobe o código, abre o pull request e o GitHub Actions já começa a rodar sozinho. Aparece aquela bolinha amarela girando enquanto a matriz de testes roda em Mac, Linux e Windows ao mesmo tempo. Se um deles falhar, o log te mostra exatamente a linha que quebrou. E quando fica tudo verde, o botão de merge libera.

## Gabarito

Categoria: produto/software com exigência de print de UI. Beats: 5.

- **Beat 1 — abrir o pull request**
  - Tipo esperado: print de UI
  - Literalidade exigida: a tela real de um pull request no GitHub
  - Fontes plausíveis: captura do próprio repositório pelo navegador autorizado, documentação oficial do GitHub, screencasts
  - Stock aceitável? Não
- **Beat 2 — a bolinha amarela rodando**
  - Tipo esperado: print de UI
  - Literalidade exigida: check em estado "in progress" na interface do GitHub, legível
  - Fontes plausíveis: captura própria, docs do GitHub
  - Stock aceitável? Não
- **Beat 3 — matriz rodando em três sistemas**
  - Tipo esperado: print de UI
  - Literalidade exigida: a lista de jobs da matriz com os sistemas nomeados
  - Fontes plausíveis: captura própria de uma execução real, docs oficiais
  - Stock aceitável? Não
- **Beat 4 — o log mostrando a linha que quebrou**
  - Tipo esperado: print de UI
  - Literalidade exigida: log de job com falha, com a linha do erro legível
  - Fontes plausíveis: captura própria
  - Stock aceitável? Não
- **Beat 5 — tudo verde e merge liberado**
  - Tipo esperado: print de UI
  - Literalidade exigida: checks verdes e o botão de merge habilitado na mesma tela
  - Fontes plausíveis: captura própria, docs oficiais
  - Stock aceitável? Não

Observação: todos os beats pedem captura de página. O caso mede se o agente vai pelo fluxo de navegador (`browser-plan` + importação do arquivo real) em vez de buscar vídeo genérico de "DevOps".
