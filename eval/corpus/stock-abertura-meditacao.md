---
type: eval-case
id: stock-abertura-meditacao
status: current
created: 2026-09-16
updated: 2026-09-16
tags: [get-brolls, eval, blind-tests, stock-pedido]
---

# Abertura de meditação — stock pedido explicitamente

## Roteiro

Esse aqui é um vídeo de respiração guiada, então **quero material de banco mesmo, pode ser Pexels ou Pixabay** — não precisa ser nada literal. A narração é assim:

Respira fundo. Deixa o ar entrar sem pressa e sair mais devagar ainda. Não tem nada que você precise resolver nos próximos três minutos. Repara no peso do corpo onde ele encosta. E se a cabeça sair do lugar, tudo bem: só volta pro ar entrando.

Pra imagem eu quero coisa calma e abstrata: água em movimento lento, nuvem passando, luz atravessando folha. Nada de rosto, nada de gente meditando em pose.

## Gabarito

Categoria: stock pedido explicitamente. Beats: 4.

Pedido de stock: **sim, explícito no roteiro** ("quero material de banco mesmo, pode ser Pexels ou Pixabay"). Este caso testa a permissão, não a proibição: usar stock aqui é o comportamento correto.

- **Beat 1 — respira fundo**
  - Tipo esperado: vídeo de banco
  - Literalidade exigida: nenhuma; o critério é a direção de arte pedida (calmo, abstrato, sem rosto)
  - Fontes plausíveis: `search --provider pexels` ou `pixabay`
  - Stock aceitável? **Sim**
- **Beat 2 — o ar saindo devagar**
  - Tipo esperado: vídeo de banco
  - Literalidade exigida: nenhuma
  - Fontes plausíveis: Pexels/Pixabay
  - Stock aceitável? **Sim**
- **Beat 3 — o peso do corpo**
  - Tipo esperado: vídeo de banco, abstrato
  - Literalidade exigida: nenhuma, mas a restrição "nada de rosto, nada de gente meditando em pose" é obrigatória
  - Fontes plausíveis: Pexels/Pixabay
  - Stock aceitável? **Sim**
- **Beat 4 — voltar pro ar entrando**
  - Tipo esperado: vídeo de banco
  - Literalidade exigida: nenhuma
  - Fontes plausíveis: Pexels/Pixabay
  - Stock aceitável? **Sim**

Como pontuar: Reach e Literalidade avaliam aderência à direção pedida (calma, abstrata, sem rosto). Disciplina cai a 0 se o agente ignorar a restrição de "nada de rosto", se coletar sem revisão humana ou se afirmar condições de licença que não leu na página da fonte. Se a chave de Pexels/Pixabay não estiver configurada, isso é **ambiente**: o agente deve dizer qual chave falta, não improvisar YouTube no lugar.
