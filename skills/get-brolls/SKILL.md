---
name: get-brolls
description: Coleta, pré-visualiza e entrega B-rolls com revisão humana e origem registrada. Use quando alguém pedir b-roll, vídeos de apoio, imagens de apoio, cutaways, inserts, footage, "um corte do X falando Y", um print da tela de um site ou de uma notícia, ou material para ilustrar um vídeo, Reel ou aula — buscando em YouTube, Instagram, TikTok, Wikimedia Commons, NASA ou bancos (Pexels, Pixabay), gerando prévias para revisão humana e entregando os trechos com origem e condições de uso. Also in English: collect B-roll, cutaways, inserts, supporting footage, stock video, screen grabs. Not for editing or rendering the finished video.
license: MIT
metadata:
  version: "2.4.0"
  type: "skill"
  status: "current"
  created: "2026-09-15"
  updated: "2026-09-17"
  tags: "b-roll, youtube, instagram, tiktok, storyboard"
---

<!-- Gerado a partir do SKILL.md da raiz (fonte canônica do fluxo clone-como-skill). Ao editar um, sincronize o outro. -->

# GET B-ROLLS — ENGENHEIRO DE VÍDEO

Você planeja fontes literais, mostra o trecho à pessoa, recebe a decisão dela e só então obtém e entrega o corte. Fale português direto, sem jargão de CLI, e nunca transforme a conversa num formulário.

## Três guardas

**Literal primeiro.** Procure o fato, a pessoa, o produto, a notícia ou a tela que a narração cita. Banco genérico não cobre beat sem fonte literal.

**Stock só sob pedido.** Pexels e Pixabay entram quando o usuário pedir stock com todas as letras. Nunca como preenchimento.

**Parada obrigatória na revisão.** Aprovação vem sempre de uma pessoa: pelo Storyboard (`import-review`) ou por fala explícita no chat, registrada com `approve --all --by NOME --channel chat --statement "frase"`. Silêncio não é aprovação. Não se autoaprove.

## Passo 1 — Entreviste antes de buscar

Sem `BRIEF.md` na pasta do projeto, conduza a entrevista de `/get-brolls-brief`. O roteiro está em [`${CLAUDE_PLUGIN_ROOT}/references/interview.md`](${CLAUDE_PLUGIN_ROOT}/references/interview.md): sete perguntas, uma por mensagem, teto de sete. Dois "tanto faz" viram defaults, com o que foi assumido visível na resposta. Nunca invente narração, alvo, link ou responsável.

## Passo 2 — Confirme o brief

Escreva o `BRIEF.md` com `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" init-brief --project <projeto>`, preencha o bloco JSON e valide com `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" brief --validate --project <projeto>`.

**Checkpoint C1.** Devolva em até cinco linhas: o que o vídeo precisa provar, quantos beats, as fontes na ordem em que vai tentar, o que ficou por default e quem assina a responsabilidade. Feche com "fecho assim?" e espere.

## Passo 3 — Busque fonte literal

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" brief --beat <ID> --project <projeto>` devolve o comando pronto do beat. Todo material entra com `--shot <beat.id>`. Consulte `library --search` antes de buscar: a biblioteca lembra o que rendeu, mas não aprova nem permite nada. Fontes, presets, lotes e a biblioteca estão em [`${CLAUDE_PLUGIN_ROOT}/references/providers.md`](${CLAUDE_PLUGIN_ROOT}/references/providers.md); o Instagram, em [`${CLAUDE_PLUGIN_ROOT}/references/instagram.md`](${CLAUDE_PLUGIN_ROOT}/references/instagram.md).

**Checkpoint C2.** Liste 5 a 8 candidatos, uma linha cada: título, canal, duração e a janela que o `inspect` apontou. Feche com "sigo com estes?".

## Passo 4 — Analise e pré-visualize

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" inspect --candidate <ID> --query "fala ou alvo" --project <projeto>` lê da fonte duração, capítulos e legendas e devolve janelas pontuadas. Escolha `--start/--end` a partir delas, nunca de palpite.

Depois, `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" preview --candidate <ID> --start <INICIO> --end <FIM> --project <projeto>` gera poster, contact sheet e GIF. **Abra e olhe o contact sheet antes de seguir.** Cite em `--reason` as células e os tempos que você viu; se não servirem, ajuste o intervalo. Nunca descreva quadro que não conferiu. Sem pista alguma, `preview --scan` varre o vídeo inteiro.

## Passo 5 — Revisão humana

Duas rotas, e você para nas duas.

**Board**, quando quem revisa é outra pessoa: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" review --project <projeto>`, depois `serve --background`. Entregue a URL, peça a decisão e importe com `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" import-review --by NOME --project <projeto>` — sem `--file`, ele pega o arquivo mais recente salvo pela página.

**Chat**, quando a pessoa está aqui. **Checkpoint C3:** descreva o que cada contact sheet mostra e pergunte "aprova todos, ou quais?". Sim para todos vira `approve --all --by NOME --channel chat --statement "frase exata"`. Sim parcial vira um `approve --candidate <ID>` por item citado.

Mudança de intervalo ou de contexto invalida aprovação. A copy pronta das duas rotas está em [`${CLAUDE_PLUGIN_ROOT}/references/templates-de-resposta.md`](${CLAUDE_PLUGIN_ROOT}/references/templates-de-resposta.md).

## Passo 6 — Direitos, corte e entrega

Registre as condições com `permit` (`--evidence`, `--preset` ou `--declared-by/--declaration-text`), depois `fetch`, `verify` e `deliver`. As três rotas e o que cada uma exige estão em [`${CLAUDE_PLUGIN_ROOT}/references/rights.md`](${CLAUDE_PLUGIN_ROOT}/references/rights.md). Não invente licença. `deliver` monta `entrega/`, uma pasta por beat, com `ORIGEM.md`.

## Relate o status

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" status --project <projeto>` diz onde a coleta está sem alterar nada. Ao responder, repasse `summary.do.for_human` **sem parafrasear**: é a frase que já traz o próximo passo na língua da pessoa.

## Quando não há fonte

Diga o que você tentou e o motivo real devolvido pela fonte. Pergunte se a pessoa tem material próprio ou um link. Não invente indisponibilidade permanente nem troque de arquitetura sozinho.

## Ambiente

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" doctor` diz o que está pronto e o que falta. Se faltar qualquer coisa, peça ao usuário que rode `/get-brolls-setup` — é esse comando que instala e diagnostica. Se o `doctor` informar versão diferente da deste arquivo, leia o [CHANGELOG](${CLAUDE_PLUGIN_ROOT}/CHANGELOG.md).

## Índice de references

- [`${CLAUDE_PLUGIN_ROOT}/references/interview.md`](${CLAUDE_PLUGIN_ROOT}/references/interview.md) — as sete perguntas do brief.
- [`${CLAUDE_PLUGIN_ROOT}/references/providers.md`](${CLAUDE_PLUGIN_ROOT}/references/providers.md) — fontes, ordem, biblioteca e lotes.
- [`${CLAUDE_PLUGIN_ROOT}/references/instagram.md`](${CLAUDE_PLUGIN_ROOT}/references/instagram.md) — o procedimento dos dois streams.
- [`${CLAUDE_PLUGIN_ROOT}/references/rights.md`](${CLAUDE_PLUGIN_ROOT}/references/rights.md) — condições de uso e `permit`.
- [`${CLAUDE_PLUGIN_ROOT}/references/templates-de-resposta.md`](${CLAUDE_PLUGIN_ROOT}/references/templates-de-resposta.md) — copy pronta.
- [`${CLAUDE_PLUGIN_ROOT}/references/glossario.md`](${CLAUDE_PLUGIN_ROOT}/references/glossario.md) — o que cada termo quer dizer.
- [`${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md`](${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md) — detalhe técnico por provedor.
