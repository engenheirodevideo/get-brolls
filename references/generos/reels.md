---
type: reference
status: current
created: 2026-09-25
updated: 2026-09-25
tags: [get-brolls, roteiro, reels, copy]
---

# Reels (9:16, 15–60 s)

O esqueleto do `roteiro --action new --genero reels` traz quatro cenas: Gancho, Problema, Prova e CTA. Formato das cenas e diretivas: [`../roteiro.md`](../roteiro.md).

- **Gancho (até 3 s):** uma frase que promete ou contradiz. Sem "oi, pessoal".
- **Uma ideia por cena.** Cena com mais de duas frases vira duas.
- **O b-roll mostra o que a fala diz**, literal primeiro (a mesma regra da coleta).
- **Prova antes do CTA:** tela, número verificável ou demonstração — nunca inventado.
- **CTA explícito:** o que a pessoa faz agora, em uma frase.
- Legenda ligada por padrão (`legenda: true`): escreva frases curtas.
- Duração: acrescente `duracao_alvo_s` ao frontmatter (ex.: `duracao_alvo_s: 45`); o `check` avisa quando a soma das cenas passa.
