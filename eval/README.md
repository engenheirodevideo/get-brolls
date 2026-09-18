---
type: documentation
status: current
created: 2026-09-16
updated: 2026-09-17
tags: [get-brolls, eval, blind-tests, quality]
---

# Testes cegos — medição editorial do Get B-rolls

Esta pasta guarda o processo de **teste cego**: o jeito de medir se a skill entrega o que promete a um criador de conteúdo real. É medição **editorial**, não de código. A suíte `python3 -m unittest discover -s tests` responde se o programa funciona; o teste cego responde outra pergunta: *dado um roteiro que o agente nunca viu, ele acha a fonte literal certa, mostra a prévia certa e para na hora certa?*

Nada aqui substitui [QUALITY.md](../docs/QUALITY.md). Lá ficam as evidências de código e os ensaios por provedor; aqui fica o comportamento do conjunto skill + CLI + agente diante de um roteiro.

## Estado do processo — 2.4.0

A rodada da 2.4.0 foi executada **por inteiro**: os **16 casos** do corpus, distribuídos entre seis executores independentes, cada um cego ao gabarito do seu caso. É a primeira rodada em que nenhum caso ficou de fora — as anteriores cobriam uma amostra. As fricções relatadas pelos executores foram tratadas numa onda de correção dentro da própria 2.4.0, e cada uma virou teste na suíte (a lista está no CHANGELOG e em [QUALITY.md](../docs/QUALITY.md)).

Esta nota registra só o **processo**: que a rodada aconteceu e com que cobertura. As notas por beat, as métricas e o veredito de cada caso são do juiz e ficam em [runs/2026-09-17-2.4.0-rc-claude-opus.md](runs/2026-09-17-2.4.0-rc-claude-opus.md) — o mesmo arquivo reúne a baseline, as rodadas 1 a 3 e, na seção [Rodada final (16 casos)](runs/2026-09-17-2.4.0-rc-claude-opus.md#rodada-final-16-casos), os 16 casos completos com a confirmação pós-onda (c2); nada aqui os antecipa nem os substitui.

## O que estamos medindo — o propósito do produto

O produto tem uma cadeia de seis passos. O teste cego mede a cadeia inteira, não um comando isolado:

**pede → categoriza → busca → separa → apresenta → entrega.**

1. **pede** — o usuário chega com um roteiro, não com uma query.
2. **categoriza** — o agente transforma cada fala em um beat e decide o tipo de asset (vídeo do fato, print de UI, imagem de arquivo).
3. **busca** — procura a fonte **literal** primeiro: o fato, a pessoa, o produto, a notícia ou a tela que a narração cita.
4. **separa** — escolhe candidatos e intervalos defensáveis, descartando o que não mostra o que a fala diz.
5. **apresenta** — gera prévia legível (GIF/contact sheet) com fala, motivo e origem, e publica o Storyboard.
6. **entrega** — só depois da decisão humana, com condições de uso registradas.

Três guardas são inegociáveis e qualquer violação derruba a nota do beat, mesmo que o material seja bonito:

- **Literal primeiro.** O padrão é footage/print/imagem real do que a narração cita.
- **Stock só sob pedido.** Pexels/Pixabay entram **somente** quando o roteiro ou o usuário pedir stock; nunca como preenchimento de um beat sem fonte literal.
- **Parada obrigatória na revisão humana.** O executor vai até o Storyboard e para. Não aprova, não coleta corte final, não inventa licença.

## Papéis — por que "cego"

| Papel | Enxerga | Faz |
|---|---|---|
| **Executor** | Somente a seção `## Roteiro` do caso | Monta o projeto, roda o fluxo até o Storyboard, para na revisão humana e preenche o relatório |
| **Juiz** | Roteiro, `## Gabarito` e o relatório do executor | Aplica [rubric.md](rubric.md) beat a beat e fecha as métricas da rodada |
| **Amostragem humana** | Tudo, mais as prévias reais | Confere uma fatia dos beats (mínimo 3 por rodada) e arbitra desempate entre executor e juiz |

O executor **nunca** abre `## Gabarito`. É isso que torna o teste cego: se ele soubesse a resposta esperada, mediríamos leitura de gabarito, não capacidade de achar fonte literal. O julgamento é um passo separado, feito depois do relatório fechado.

## Fluxo de uma rodada

1. Escolha os casos (rodada completa = os 16 de [corpus/](corpus/); smoke = 3).
2. Para cada caso, dispare o executor com `/get-brolls-eval` ([commands/get-brolls-eval.md](../commands/get-brolls-eval.md)) passando só o id ou o texto do roteiro.
3. O executor cria um projeto isolado, executa `search` → `preview` → `review`, para no Storyboard e coleta `python3 scripts/gb.py status --project <projeto>`.
4. O executor preenche um relatório a partir de [runs/TEMPLATE.md](runs/TEMPLATE.md).
5. O juiz abre o gabarito, pontua cada beat pela rubrica e escreve as métricas da rodada no mesmo relatório.
6. A amostragem humana confere ao menos 3 beats, incluindo todo beat marcado como indisponível.
7. O relatório final entra em [runs/](runs/) com o nome `AAAA-MM-DD-<versão>-<agente>.md`.

## Cadência

- **Rodada completa (16 casos)** — a cada release candidate, antes de publicar a tag.
- **Smoke de 3 casos** — depois de qualquer mudança em `SKILL.md`, no espelho `skills/get-brolls/SKILL.md` ou nos prompts/comandos que dirigem o agente. Escolha um caso de notícia, um de print de UI e a armadilha.
- **Fora de ciclo** — quando um provedor mudar de comportamento ou quando um usuário relatar que o agente "encheu com stock".

## Fora do CI — de propósito

O teste cego **não roda no CI** e não deve rodar. Ele precisa de rede, de sessão de navegador, de yt-dlp e de tempo de agente; o resultado varia com disponibilidade de fonte, região e horário. Colocá-lo no CI transformaria indisponibilidade de terceiro em build vermelho e ensinaria a equipe a ignorar o vermelho.

Consequência prática: todo relatório separa **ambiente** (URL fora do ar, bloqueio de região, sessão expirada, quota) de **comportamento** (o agente escolheu stock sem pedido, inventou licença, aprovou sozinho). Só a coluna de comportamento é regressão do produto. A regra está detalhada em [rubric.md](rubric.md).

## Estrutura

```
eval/
├── README.md              # este processo
├── rubric.md              # como pontuar beat a beat e fechar a rodada
├── corpus/                # 16 casos: ## Roteiro (visível) + ## Gabarito (oculto)
└── runs/
    ├── TEMPLATE.md        # modelo do relatório
    └── 2026-09-16-2.3.7-claude-opus.md   # baseline inaugural
```

## Corpus — os 16 casos

Cada caso tem um `## Roteiro` escrito como um criador de conteúdo escreveria e um `## Gabarito` com o tipo de asset esperado, a literalidade exigida, fontes plausíveis e se stock é aceitável (padrão: não).

| Categoria | id | O que o caso mede |
|---|---|---|
| Notícia factual | [news-cop30-belem](corpus/news-cop30-belem.md) | Fato brasileiro; resistir a aéreas genéricas de floresta |
| Notícia factual | [news-eclipse-solar-2024](corpus/news-eclipse-solar-2024.md) | Evento datado; não aceitar "um eclipse qualquer" |
| Notícia factual | [news-artemis-sls](corpus/news-artemis-sls.md) | Entidade técnica certa (SLS, não outro foguete) e uso do provedor `nasa` |
| Notícia factual | [news-ai-act-europeu](corpus/news-ai-act-europeu.md) | Escolher captura de página onde vídeo não resolve |
| Print de UI | [ui-claude-code-prompt](corpus/ui-claude-code-prompt.md) | UI real do produto, não "hacker digitando" |
| Print de UI | [ui-davinci-resolve-timeline](corpus/ui-davinci-resolve-timeline.md) | Intervalo em que a UI está legível dentro de um tutorial |
| Print de UI | [ui-github-actions-pipeline](corpus/ui-github-actions-pipeline.md) | Fluxo de navegador em vez de vídeo genérico de DevOps |
| Pessoa em evento | [person-jensen-huang-gtc](corpus/person-jensen-huang-gtc.md) | Palco citado, não trailer/aftermovie |
| Pessoa em evento | [person-satya-nadella-build](corpus/person-satya-nadella-build.md) | Recorte curto dentro de um keynote longo |
| Local/nicho | [local-galo-da-madrugada](corpus/local-galo-da-madrugada.md) | Cauda longa em português; carnaval específico, não genérico |
| Local/nicho | [local-festival-gramado](corpus/local-festival-gramado.md) | Aceitar still literal quando não há movimento |
| Stock sob pedido | [stock-abertura-meditacao](corpus/stock-abertura-meditacao.md) | Stock **é** a resposta certa quando pedido — e a direção de arte é o critério |
| Stock sob pedido | [stock-fundo-abstrato-tech](corpus/stock-fundo-abstrato-tech.md) | Permissão parcial: não generalizar stock para o beat proibido |
| Armadilha | [trap-reuniao-fechada](corpus/trap-reuniao-fechada.md) | Reportar indisponibilidade e perguntar, em vez de inventar |
| Brief/intake | [brief-entrevista-preguicosa](corpus/brief-entrevista-preguicosa.md) | Parar de perguntar, gravar o `BRIEF.md` e aplicar defaults sem inventar direitos |
| Rede social | [social-tiktok-publico](corpus/social-tiktok-publico.md) | Descobrir a URL do TikTok público no navegador e não declarar "exige sessão" |
