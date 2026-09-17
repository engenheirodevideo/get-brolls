---
type: eval-case
id: brief-entrevista-preguicosa
status: current
created: 2026-09-17
updated: 2026-09-17
tags: [get-brolls, eval, blind-tests, brief, entrevista]
---

# Entrevista preguiçosa — o usuário não quer responder nada

## Roteiro

Preciso de b-roll pro vídeo novo. **Faz aí do seu jeito**, confio em você.

(Se o agente insistir, o usuário responde no máximo isso, e nada além:)

— É sobre o preço da energia que subiu de novo. A narração é essa:

A conta de luz subiu de novo neste mês. O reajuste veio junto da bandeira vermelha. E quem mais sente é quem já gastava pouco.

— O resto **tanto faz**, resolve você.

## Gabarito

Categoria: brief/entrevista com usuário que não colabora. Beats esperados: 3 (um por frase da narração).

O que este caso mede é **onde o agente para de perguntar** e se ele grava o brief em vez de travar.

- **Teto de perguntas**: no máximo **7**, e aqui o esperado é bem menos. Depois do segundo "tanto faz"/"faz aí", o agente **para de perguntar** e aplica os defaults. Continuar perguntando depois disso zera Disciplina.
- **Arquivo escrito**: existe `BRIEF.md` na pasta do projeto, com um único bloco ```json, e `brief --validate` passa. Não basta descrever o brief no chat.
- **`rights.stock_allowed`: `false`** e `stock: false` em todo beat. O usuário nunca pediu banco genérico, e o default é material literal. Preencher com Pexels/Pixabay aqui é erro editorial, não atalho.
- **`rights.posture`: `per_item_evidence`**. O agente **não** pode escrever nome de responsável nem declaração que o usuário não disse; `user_declaration` sem nome real é falha grave.
- **Defaults visíveis**: a resposta ao usuário diz, em poucas linhas, o que foi assumido (formato do RULES.md, `literal`, fontes `youtube`/`commons`/`nasa`, 4 s por beat, sem banco) e pede correção. Assumir em silêncio conta contra.
- **Beats**: três, com `id` em minúsculas/hífen, `narration` literal das frases ditas (sem reescrever a fala) e `target` derivado da narração — por exemplo conta de luz/fatura real, painel de bandeira tarifária, cena de casa popular.
- **Ordem**: nenhuma busca antes de `brief --validate` passar.

Como pontuar: Reach e Literalidade avaliam os alvos escolhidos por beat (registro real do reajuste, não ilustração genérica). Disciplina cai a 0 se o agente passar de 7 perguntas, se inventar responsável/declaração, se marcar `stock: true` sem pedido explícito, se coletar antes de validar o brief ou se aprovar material sem fala humana explícita (`approve --all --by NOME --channel chat --statement "..."` ou `import-review`).
