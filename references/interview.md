---
type: reference
status: current
created: 2026-09-17
updated: 2026-09-17
tags: [get-brolls, brief, entrevista, onboarding]
---

# Entrevista de brief — 7 perguntas, uma de cada vez

Para quando o projeto ainda não tem `BRIEF.md` e alguém pediu b-roll. O objetivo é sair com um brief válido, não com um formulário preenchido. **Uma pergunta por mensagem**, teto de sete no total. Ninguém é obrigado a responder tudo: com as perguntas 1, 3 e 7 respondidas já dá para escrever o arquivo.

## As perguntas, na ordem

1. **Qual é o vídeo e o que ele precisa provar?** → `video.title`, `video.objective`. Obrigatória.
2. **Para quem é, e onde vai ser publicado?** → `video.audience`, `video.delivery.platform`, `video.delivery.format`.
3. **Cola a narração, ou me diz os momentos em ordem.** → `beats[].narration`. Obrigatória. Segmente você mesmo e confirme a divisão **uma vez** ("ficaram 6 trechos, começando em X e terminando em Y — fecha?"), sem pedir aprovação beat a beat.
4. **Neste trecho aqui, o que precisa aparecer?** → `beats[].target`. Só para os beats em que o alvo não sai sozinho da fala. Se a narração diz "o foguete da NASA subindo", o alvo é óbvio: não pergunte.
5. **Você tem material próprio ou links que já quer usar?** → acrescenta `local` a `allowed_sources` do beat e vira `resolve --file` ou `resolve --url` depois.
6. **Quando não existir registro real do que a fala cita, pode entrar material de banco (Pexels/Pixabay)?** → `rights.stock_allowed`, e `stock` nos beats. Pergunta **global**, uma vez só para o vídeo inteiro.
7. **Quem assina a responsabilidade pelo uso desse material?** → `rights.posture`. Obrigatória. `per_item_evidence` = a pessoa confere fonte por fonte; `user_declaration` = alguém declara e assume, e aí o RULES.md precisa ter nome e frase (`init-rules --mode user_declaration --responsible "NOME" --declaration "frase" --force`).

## Quando parar

- Pare assim que **1, 3 e 7** estiverem respondidas, mesmo que falte pergunta na lista.
- Pare no teto de **sete perguntas**, sempre.
- **Dois "tanto faz" / "faz aí" / "confia" seguidos**: pare de perguntar, aplique os defaults, escreva o arquivo e **mostre o que você assumiu** em três a cinco linhas, pedindo só correção. Não repita a pergunta com outras palavras.

## Defaults quando a pessoa não decide

| Campo | Default |
| --- | --- |
| `video.delivery.format` | o `video_format` que já está no RULES.md |
| `beats[].intent` | `literal` |
| `allowed_sources` | `["youtube", "commons", "nasa"]` |
| `stock` / `rights.stock_allowed` | `false` — material literal primeiro |
| `duration_hint_s` | `4` |
| `rights.posture` | `per_item_evidence` |

Default não é invenção: ele fica visível no arquivo e na mensagem de devolução, e a pessoa pode mudar qualquer um. O que **nunca** tem default é declaração de responsabilidade preenchida em nome de alguém.

## Depois da entrevista

1. Escreva o `BRIEF.md` na pasta do projeto (use `init-brief --project ...` para partir do modelo, ou escreva o arquivo direto — um único bloco ```json).
2. Rode `brief --validate --project ...` e conserte o que ele apontar. **Só busque depois que isso passar.**
3. Devolva o brief em poucas linhas usando o formato de `templates-de-resposta.md` (a mesma voz dos outros retornos): o que você entendeu, quantos beats, o que assumiu por default e o que falta.
4. Colete beat a beat com `brief --beat ID --project ...`: ele entrega `search`, `resolve --shot <id>` e `preview --narration` prontos. Todo material de um beat entra com `--shot <beat.id>` — é esse campo que liga o beat ao candidato.
5. Feche pela decisão humana de sempre: Storyboard (`review` + `import-review --by NOME`) ou fala explícita no chat (`approve --candidate ID --by NOME --channel chat --statement "frase exata"`). Nunca deduza aprovação de silêncio.

## Checkpoints — as três paradas com a pessoa

Cada checkpoint cabe em poucas linhas e termina numa pergunta fechada. Não repita o checkpoint se já teve resposta.

### C1 — depois do brief, antes de buscar

- O que o vídeo precisa provar, em uma linha.
- Quantos beats ficaram e quais fontes você vai tentar, na ordem.
- O que entrou por default e quem assina a responsabilidade.
- "Fecho assim?" — e espere. Correção aqui é barata; depois de baixar, não.

### C2 — depois da shortlist, antes de baixar

- De 5 a 8 candidatos, um por linha: título, canal/autor, duração.
- A janela que o `inspect` apontou e a legenda ou capítulo que a justifica.
- Diga o que ainda não conferiu; não descreva quadro que você não viu.
- "Sigo com estes?" — quem responde escolhe, tira ou pede outra fonte.

### C3 — depois das prévias, no lugar do board

- Um resumo por contact sheet, citando as células e os tempos que você olhou.
- "Aprova todos, ou quais?"
- Aprove exatamente os IDs que você mostrou: `approve --candidate ID1 --candidate ID2 … --by NOME --channel chat --statement "frase exata"`.
- `--all` só quando todos os candidatos com prévia foram mostrados: ele pega o que ficou em disco, inclusive o que você descartou sem rejeitar.
- Silêncio nunca é aprovação. Com revisor terceiro, prefira o Storyboard (`review` + `import-review --by NOME`).

## Biblioteca entre projetos

Antes de sair buscando, rode `library --search TERMO --project ...`: ela lembra buscas que renderam, fontes que falharam e trechos já usados em outros vídeos. É memória editorial, não licença — toda resposta traz `rights_not_transferable: true`, e aprovação e `permit` continuam por projeto. Depois de uma decisão útil, guarde com `learn --from-candidate ID` (exige `remember` antes), `learn --query "..." --provider FONTE --outcome hit|miss` ou `learn --preference "frase da pessoa"`. `GB_LIBRARY=off` desliga leitura e escrita.
