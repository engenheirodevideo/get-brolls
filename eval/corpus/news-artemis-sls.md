---
type: eval-case
id: news-artemis-sls
status: current
created: 2026-09-16
updated: 2026-09-16
tags: [get-brolls, eval, blind-tests, noticia]
---

# Programa Artemis e o foguete SLS

## Roteiro

A NASA voltou a mirar a Lua com o programa Artemis. O foguete é o SLS, montado no Kennedy Space Center e levado até a plataforma 39B naquele transportador gigante que anda mais devagar que gente caminhando. A cápsula Orion foi a primeira a fazer o caminho todo sem tripulação, deu a volta na Lua e voltou. E o que ficou daquela missão não foi o lançamento: foi a foto da Terra pequenininha vista de trás da Orion.

## Gabarito

Categoria: notícia factual. Beats: 4.

- **Beat 1 — o foguete SLS**
  - Tipo esperado: vídeo
  - Literalidade exigida: o SLS/Artemis identificável, não Falcon 9, Starship ou Saturno V
  - Fontes plausíveis: NASA (YouTube e acervo público), provedor `nasa` da própria CLI
  - Stock aceitável? Não
- **Beat 2 — rollout até a plataforma 39B**
  - Tipo esperado: vídeo
  - Literalidade exigida: o crawler-transporter movendo o SLS no Kennedy Space Center
  - Fontes plausíveis: NASA, cobertura de agências, canais de space flight
  - Stock aceitável? Não
- **Beat 3 — Orion contorna a Lua sem tripulação**
  - Tipo esperado: vídeo (imagem da própria missão) ou animação oficial claramente identificada
  - Literalidade exigida: material da Artemis I; animação serve apenas se a fonte for oficial e o relatório disser que é animação
  - Fontes plausíveis: NASA
  - Stock aceitável? Não
- **Beat 4 — a Terra vista de trás da Orion**
  - Tipo esperado: imagem (still de alta resolução) ou vídeo curto
  - Literalidade exigida: o enquadramento real da missão, com a Terra pequena e a Orion em primeiro plano
  - Fontes plausíveis: acervo NASA, Wikimedia Commons
  - Stock aceitável? Não

Observação: este caso também testa se o agente lembra que existe provedor `nasa` na CLI em vez de tratar tudo como busca no YouTube.
