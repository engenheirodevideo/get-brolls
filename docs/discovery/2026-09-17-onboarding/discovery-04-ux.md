---
type: research
status: current
created: 2026-09-17
updated: 2026-09-17
tags: [get-brolls, ux, pesquisa, persona, jornada, copy]
---

# Discovery UX — Get B-rolls 2.3.8 para quem nunca abriu um terminal

Pesquisa de síntese (sem entrevistas novas): cruza o PRD de testes cegos, os 71 achados da review 2.3.8, a nota de release 2.3, a rodada cega baseline e a leitura direta do código/copy da 2.3.8. Tudo que está citado tem arquivo e linha. Nenhum arquivo foi alterado.

**Caminhos das fontes**
- Repo: `/private/tmp/claude-501/-Users-brunomoreira-orca-projects-zbrunomoreira/877a5885-b55d-4115-93c5-dcdce18c038a/scratchpad/get-brolls-main`
- PRD: `/Users/brunomoreira/orca/projects/zbrunomoreira/docs/superpowers/specs/2026-09-16-get-brolls-blind-tests-prd.md`
- Achados: `/Users/brunomoreira/orca/projects/zbrunomoreira/docs/superpowers/specs/2026-09-17-get-brolls-2-3-8-review-findings.md`
- Release: `/Users/brunomoreira/orca/projects/zbrunomoreira/wiki/personal_brand/engenheirodevideo-getbrolls-release-2-3.md`

---

## 0. A tese em uma frase

O produto **já resolve o problema editorial** — a rodada cega fechou 100% de reach literal, 0 stock não pedido e Storyboard revisável em ~4 minutos (`eval/runs/2026-09-16-2.3.7-claude-opus.md:36,79-87`). O que ele **ainda não resolve é o problema de linguagem**: toda a superfície visível para o usuário final fala em candidato, provedor, permit, evidência, ledger, projeto, JSON e localhost. A persona-alvo não tem essas palavras. A distância entre "funciona" e "dá pra usar" hoje é quase inteiramente de copy e de sequência de perguntas, não de engenharia.

---

## 1. Persona, jobs-to-be-done e ansiedades

### 1.1 Persona primária — "Dani, a editora que só quer os inserts"

| Dimensão | Realidade |
|---|---|
| Quem | Criadora/editora de conteúdo brasileira, 24–38 anos, edita no Premiere, DaVinci ou CapCut. Entrega Reels, YouTube e cortes para clientes pequenos |
| Repertório técnico | Sabe pastas, sabe arrastar arquivo pra timeline, sabe baixar do Drive. **Nunca** abriu Terminal por vontade própria. Não distingue "instalar Python" de "instalar um app" |
| Vocabulário que ela tem | vídeo, corte, insert, imagem de apoio, trecho, "de onde saiu isso", pasta, link |
| Vocabulário que ela **não** tem | candidato, ID do candidato, provedor, ledger, manifest, projeto (no sentido de pasta-raiz do CLI), JSON, export, permit, evidência, contact sheet, drawtext, cooldown, fila, `--project`, `localhost:8767` |
| Como ela chega | Post do Engenheiro de Vídeo → GitHub. O tráfego real registrado foi 18 clones / 80 views / 1 visitante único (`...release-2-3.md:17`) — ou seja, **a descoberta hoje é toda pelo README**, e o README abre com uma tabela de seis dependências |
| Como ela pede | Em português corrido: "preciso de umas imagens do lançamento do foguete da SpaceX e do Vision Pro pro vídeo de amanhã" |
| Como ela mede sucesso | Arquivos .mp4 numa pasta que ela consegue arrastar pro timeline, hoje, sem medo de tomar strike |

Persona secundária relevante (não a do brief, mas presente nos dados): **o agente executor**. A rodada cega mostra que o agente também sofre com falta de sinal — escolheu `--start/--end` às cegas porque `search` devolve `duration_s: null` (`eval/runs/2026-09-16-2.3.7-claude-opus.md:95`). Copy que ajuda a pessoa e sinal que ajuda o agente são o mesmo investimento.

### 1.2 Jobs-to-be-done

Formulados como a pessoa diria, não como o produto nomeia.

| # | Job (voz da pessoa) | Job canônico | Onde o produto atende hoje |
|---|---|---|---|
| J1 | "Quero mostrar a coisa de que eu tô falando, não uma imagem bonitinha qualquer" | Obter material **literal** do fato/pessoa/produto citado | É o contrato central do produto (`SKILL.md:26`; `eval/README.md:30-32`). **Atendido, e é o diferencial** |
| J2 | "Não quero ver 40 vídeos, quero ver o trecho e dizer sim ou não" | Decidir rápido por prévia visual | Contact sheet + GIF + Storyboard (`SKILL.md:33`; `scripts/getbrolls/rendering.py:141-157`). **Atendido** |
| J3 | "Quero os arquivos numa pasta pra arrastar no Premiere" | Receber entregável utilizável | `brolls/clips/` (`README.md` quickstart, seção "Primeiro B-roll em 5 minutos"). Atendido, mas **mal comunicado** (ver §4.4) |
| J4 | "Não quero tomar strike nem processo" | Entender o risco de direitos | Registro de origem existe; a **explicação** do que isso significa não (ver §4.3) |
| J5 | "Quero voltar semana que vem e pedir mais do mesmo vídeo" | Reutilizar o projeto | Existe (ledger, memória de referências) mas **invisível**: nada na copy ensina a voltar |
| J6 | "Quero mandar as escolhas pro cliente/editor aprovar" | Compartilhar a revisão | Parcial: Storyboard é HTML local em `localhost:8767`, não compartilhável (`SKILL.md:34`) |

### 1.3 Top ansiedades (ordenadas por força de evidência)

1. **"Vou quebrar meu computador / não vou conseguir instalar."** O passo 0 do README exige Python, FFmpeg **com libfreetype**, Node 22+, npm/npx, curl, Git, yt-dlp e Playwright, com tabela de "sem ela → nada roda" (`README.md:53-62`). Para quem nunca instalou nada por terminal, isso é uma parede antes do primeiro benefício.
2. **"Vou usar e tomar strike."** O rodapé do Storyboard diz literalmente `A seleção visual não concede permissão de uso. Confira as condições de cada fonte.` (`scripts/getbrolls/storyboard.py:23`). É juridicamente correto e **operacionalmente inútil**: não diz o que conferir, onde, nem o que fazer.
3. **"Não sei se ele tá fazendo alguma coisa."** A fila social pede espera explícita (`wait_seconds`) e o próprio achado #71 registra que a CLI não dorme — quem espera é o agente/usuário (`...review-findings.md`, item 71). Sem copy de espera, isso lê como travamento.
4. **"Cadê meus arquivos?"** Não há, em nenhuma superfície, uma resposta em linguagem de pessoa para onde o material foi parar. O `verify` responde `"count": 1` (README, quickstart) — um JSON.
5. **"Fiz tudo e perdi."** O Storyboard salva em `localStorage` e a própria SKILL avisa que abrir por `file://` pode desativar o salvamento (`SKILL.md:34`); a página mostra `Salvamento local indisponível. Exporte antes de fechar.` (`assets/review.js:132`). A pessoa não sabe o que é "exportar" nem por que fechar a aba seria perigoso.
6. **"Tô fazendo certo?"** Nenhuma etapa confirma acerto em voz humana. O feedback de sucesso mais próximo é `${c.approved} aprovados · ${c.other} com pedidos · ${c.pending} pendentes` (`assets/review.js:117`).

---

## 2. Mapa da jornada atual

Sete estágios. Para cada um: o que o produto faz, o que a pessoa entende, a dor e a evidência.

### 2.1 Descobrir

- **Produto:** README abre com headline forte — "Da ideia ao trecho certo para a sua edição." (`README.md:5`) — e três benefícios claros (`README.md:22-24`).
- **Pessoa:** entende a promessa. Rola a página e a segunda coisa que vê é uma tabela de dependências.
- **Dor D1 — a promessa e a parede na mesma tela.** O primeiro heading acionável é `### 0. Instale a stack inteira` (`README.md:51`). Nenhum caminho "só quero ver funcionando".
- **Dor D2 — não existe o "para quem".** O README não diz em nenhum lugar que isso roda dentro do Claude Code/Codex e que a pessoa vai *conversar*, não digitar comando. Os comandos `python3 scripts/gb.py ...` aparecem com muito mais destaque que `/get-brolls <seu pedido>` (`README.md`, seção Comandos, item 2).
- Evidência de que o problema é real: 18 clones para 1 visitante único (`...release-2-3.md:17`) e o bug de instalação em PowerShell 5.1 reportado por usuário real (issue #23, `...release-2-3.md:17`).

### 2.2 Instalar

- **Produto:** `install.sh`/`install.ps1`, depois `doctor` como gate (`docs/GUIDE.md`, seção Instalação; `README.md:64`).
- **Pessoa:** copia e cola, torce.
- **Dor D3 — o gate é um JSON.** O critério de sucesso publicado é "`summary.missing` vazio e `contact_sheet.labels: true`" (`README.md:64`). Isso é uma condição de teste, não uma mensagem para pessoa.
- **Dor D4 — sucesso falso.** Achado #68: `doctor`/`--check` saem com **exit 0 mesmo com `contact_sheet.labels=false`** (`...review-findings.md`, item 68). Ou seja, a pessoa recebe "deu certo" e depois recebe um contact sheet sem números, sem entender por quê.
- **Dor D5 — libfreetype.** O caso real do próprio Bruno: ffmpeg do Homebrew sem libfreetype, precisou de `ffmpeg-full` e `GB_FFMPEG_PATH` (`...release-2-3.md:26`). Se o dono do produto tropeçou nisso, a persona não passa.
- **Dor D6 — `--help` em inglês.** Achado #70: `instagram_pairs --help` está em inglês enquanto o resto é PT-BR (`...review-findings.md`, item 70).
- **Contraevidência a favor do produto:** instalação limpa levou **32s** e a blind install "funcionou de primeira" (`eval/runs/2026-09-16-2.3.7-claude-opus.md:31`; `...review-findings.md`, cabeçalho "blind install (concluída)"). O problema não é a instalação executar — é a pessoa **acreditar** que vai executar.

### 2.3 Pedir

- **Produto:** `/get-brolls <seu pedido>`; o agente lê `rules` e `references`, monta beats (`SKILL.md:26`).
- **Pessoa:** escreve uma frase e espera.
- **Dor D7 — não existe entrevista inicial.** A SKILL manda o agente consultar regras e partir para a busca; **nada** obriga o agente a perguntar formato (Reels/horizontal), quantidade, prazo, se stock é aceitável, onde salvar. O resultado é que decisões editoriais viram adivinhação.
- **Dor D8 — "projeto" é pedido sem ser explicado.** `--project` aparece em praticamente todo comando (`SKILL.md:22,28,33,34`) e o README manda "troque `/caminho/meu-video` pelo seu projeto". A persona não sabe que "projeto" é só uma pasta que ela escolhe.
- **Dor D9 — a regra do stock é invisível para quem pede.** "Bancos de stock entram somente quando o usuário pedir stock explicitamente" (`SKILL.md:26`) é uma regra forte e ótima — mas ela nunca é **apresentada como escolha** para a pessoa. Ela só descobre quando o resultado vem sem stock, ou quando reclama.

### 2.4 Esperar

- **Produto:** busca, download de mídia de trabalho, geração de poster/contact sheet/GIF; em lote, fila com `wait_seconds` e cooldown por 403/429 (`SKILL.md:28`).
- **Pessoa:** olha o cursor.
- **Dor D10 — silêncio estruturado.** Não há copy de progresso definida em lugar nenhum da SKILL. O único artefato de "onde estamos" é `status --project`, descrito como comando para o agente reportar (`SKILL.md:37`).
- **Dor D11 — a espera parece erro.** Cooldown de rate limit é hoje comunicado em vocabulário de infraestrutura. Pior: dois bugs de regex faziam **erro local de ffmpeg virar cooldown de 4 horas** e o contador de frames `frame= 429 fps` virar "429" (`...review-findings.md`, itens 12 e 13) — corrigidos, mas mostram que a superfície de mensagem de espera é frágil por design.
- **Dor D12 — hint pessimista.** Achado #57: o `hint` da fila calculava o "próximo permitido" pelo **máximo** dos bloqueados, ignorando provedores livres — "o agente espera 4 h à toa" (`...review-findings.md`, item 57). Corrigido; é o retrato de como uma mensagem errada custa horas.

### 2.5 Revisar no Storyboard

- **Produto:** `review --project` gera `brolls/review.html`; `serve` sobe em `http://localhost:8767/review.html` (`SKILL.md:34`).
- **Pessoa:** recebe um endereço estranho e uma página cheia de termos novos.
- **Dor D13 — a URL é assustadora.** `localhost:8767` não parece "meu site"; parece erro. E vem acompanhada do aviso de que `file://` pode desativar o salvamento (`SKILL.md:34`) — duas ameaças na mesma frase de entrega.
- **Dor D14 — a página assume vocabulário de catálogo.** `<code>{id}</code>`, `Decisão de coleta:`, `Uso: a confirmar` (`scripts/getbrolls/rendering.py:74-92`). Ver crítica completa em §5.
- **Dor D15 — "Exportar revisão" não tem consequência explicada.** Botão em `assets/review.js:47`. O que acontece depois do clique, a pessoa precisa adivinhar.
- **Ponto forte a preservar:** o Storyboard V2 foi validado em navegador e em 375 px sem overflow, com balão de fala, badge de sem-prévia e filtro de pendentes (`...release-2-3.md:22`). A estrutura é boa; o problema é nomenclatura.

### 2.6 Receber

- **Produto:** `import-review --by` → `permit --evidence` → `fetch` → `verify` (`SKILL.md:35-36`).
- **Pessoa:** quatro passos que ela não pediu, um deles jurídico.
- **Dor D16 — `permit --evidence` é o maior obstáculo conceitual do produto.** O comando exige que a pessoa escreva "Condições de uso reais dessa fonte" (README, quickstart). A persona não sabe o que é uma condição de uso, onde encontrar, nem que está assumindo responsabilidade. E o desenho é deliberado: "a responsabilidade pelas condições de uso é de quem produz o vídeo" (`SKILL.md:26`). O desenho está certo; **a copy não faz a transferência de responsabilidade acontecer de forma compreendida.**
- **Dor D17 — a entrega termina em JSON.** `verify` responde `"count": 1` (README). Nenhuma frase diz "pronto, seus 4 arquivos estão em tal pasta".
- **Dor D18 — `credits.md` não é anunciado como benefício.** Ele existe e é exatamente o que tira a ansiedade J4/A2, mas só aparece como detalhe de implementação.

### 2.7 Reutilizar

- **Produto:** ledger, memória de referências por projeto, invalidação de aprovação ao mudar intervalo/contexto (`SKILL.md:36`; `...release-2-3.md:167`).
- **Pessoa:** não sabe que existe.
- **Dor D19 — zero affordance de retorno.** Nenhuma copy diz "da próxima vez, abra a mesma pasta e peça mais".
- **Dor D20 — invalidação silenciosa parece bug.** "Alterações de contexto/intervalo invalidam aprovação" (`SKILL.md:34`) e a mensagem real é `Revisão desatualizada para <id>: a decisão mudou ou o arquivo não contém sua versão.` (`scripts/getbrolls/review.py:86-89`). Para a persona, isso lê como "perdi meu trabalho".

### 2.8 Resumo visual da jornada

| Estágio | Emoção | Dores | Gravidade |
|---|---|---|---|
| Descobrir | curiosa → intimidada | D1, D2 | Alta |
| Instalar | ansiosa | D3, D4, D5, D6 | **Crítica** |
| Pedir | aliviada | D7, D8, D9 | Alta |
| Esperar | insegura | D10, D11, D12 | Média |
| Revisar | engajada → confusa | D13, D14, D15 | Alta |
| Receber | travada | **D16**, D17, D18 | **Crítica** |
| Reutilizar | ausente | D19, D20 | Média |

Os dois vales são **instalar** e **receber** — exatamente os dois pontos que o teste cego não mede, porque o executor já tem a máquina pronta e para antes da entrega (`eval/README.md:32`; `eval/runs/2026-09-16-2.3.7-claude-opus.md`, seção final). **O produto é medido justamente nos trechos onde a persona não sofre.**

---

## 3. Entrevista inicial do agente (máx. 7 perguntas) e template de pedido

### 3.1 Princípios

- Toda pergunta tem **default explícito** e aceita "tanto faz", "sei lá", silêncio.
- Uma pergunta por vez, linguagem de conversa de WhatsApp.
- Nenhuma pergunta usa termo interno. Não existe "provedor", "candidato", "projeto", "intent".
- Perguntas 1 e 2 são obrigatórias; da 3 em diante, se a pessoa responder "tanto faz" duas vezes seguidas, o agente **para de perguntar** e assume todos os defaults restantes.

### 3.2 O roteiro

> **Abertura (não é pergunta):**
> "Beleza. Vou te fazer até 6 perguntas rápidas pra não errar a mão — pode responder 'tanto faz' em qualquer uma que eu escolho por você."

| # | Pergunta | Default se "tanto faz" | Por que existe |
|---|---|---|---|
| 1 | "Me conta com suas palavras o que aparece no vídeo. Pode ser o roteiro inteiro ou só a parte que precisa de imagem." | — (obrigatória; se vier vazio, o agente pede um exemplo: *"tipo: 'falo do lançamento do Starship e depois do Vision Pro'"*) | É a única entrada que o produto realmente precisa (J1) |
| 2 | "Tem alguma coisa que **precisa** aparecer na tela? Uma pessoa, um produto, uma tela de programa, uma notícia específica?" | Extrai as entidades da resposta 1 e confirma: *"então eu busco Starship e Vision Pro, certo?"* | É o eixo de literalidade da rubrica (`eval/rubric.md:29-37`) transformado em pergunta |
| 3 | "O vídeo é em pé (Reels/TikTok) ou deitado (YouTube)?" | **Deitado / 16:9** | Evita entregar material inutilizável; hoje o formato vem de `RULES.md` que a persona nunca abre (`...release-2-3.md:165`) |
| 4 | "Mais ou menos quantos trechos você precisa?" | **Um por ideia do roteiro, até 8** (espelha o padrão editorial de `SKILL.md:26`) | Evita tanto o subdimensionamento quanto o enchimento |
| 5 | "Se eu não achar a imagem real do que você citou, eu posso usar uma imagem genérica de banco no lugar, ou prefere que eu avise que não achei?" | **Avisar que não achei** (nunca stock) | Converte a regra mais importante do produto (`SKILL.md:26`) em consentimento informado, em vez de surpresa |
| 6 | "Onde você quer que eu salve? Pode ser qualquer pasta — se não tiver preferência eu crio uma na sua Área de Trabalho." | **`~/Desktop/brolls-<assunto>-<data>`** | Mata a dor D8 sem nunca dizer "projeto" |
| 7 | "É pra hoje ou dá pra caprichar?" | **Caprichar** (busca mais ampla, mais prévias) | Dá ao agente licença para escolher entre velocidade e reach sem perguntar de novo depois |

**Perguntas que NÃO devem existir na entrevista** (e por quê): qual provedor usar (o agente decide pela literalidade); intervalo `--start/--end` (é trabalho do agente, e é exatamente a fricção nº 1 da rodada cega — `eval/runs/...:95`); se quer contact sheet ou GIF; formato de saída; se quer `permit`. Tudo isso é decisão de implementação disfarçada de pergunta.

### 3.3 Template de pedido que a entrevista produz

O agente devolve isto **em voz alta, para confirmação**, e usa como contrato interno:

```
PEDIDO
Vídeo sobre: <assunto em uma linha>
Formato: <em pé 9:16 | deitado 16:9>          (padrão: deitado)
Trechos: <n>                                   (padrão: um por ideia, até 8)
Imagem genérica de banco: <não | sim, só em X> (padrão: não)
Salvar em: <pasta>                             (padrão: Desktop/brolls-<assunto>-<data>)
Ritmo: <hoje | caprichar>                      (padrão: caprichar)

O QUE PRECISA APARECER
1. <fala ou ideia> → precisa mostrar: <entidade nomeada>
2. <fala ou ideia> → precisa mostrar: <entidade nomeada>
...

COMBINADO
- Se eu não achar o material real de um item, eu te aviso — não substituo por imagem genérica.
- Eu não aprovo nada sozinho: você decide item por item numa página que eu te mando.
- Eu registro de onde veio cada arquivo. Conferir se você pode usar é sua chamada, e eu te mostro onde olhar.
```

Confirmação em uma linha: **"Tá certo assim? Se estiver, eu começo — leva uns minutos."**

Esse bloco também é o que fecha o gap entre a SKILL e a rubrica: cada linha de "O QUE PRECISA APARECER" é um beat com literalidade explícita, exatamente o que o juiz pontua (`eval/rubric.md:17-37`).

---

## 4. Propostas de copy

Formato: **Antes** (string real, com arquivo:linha) → **Depois** → porquê. Princípios aplicados: clareza, concisão, consistência de termo, utilidade, voz humana.

### 4.0 Glossário de tradução (aplicar em toda superfície)

Consistência é o princípio mais violado hoje: o mesmo objeto tem três nomes conforme o arquivo.

| Termo interno | Termo para a pessoa | Nunca dizer |
|---|---|---|
| candidate / candidato / ID | **trecho** (e "o nº 3 da lista") | candidato, ID |
| provider | **de onde vem** (YouTube, Instagram, banco de vídeo) | provedor |
| project / `--project` | **pasta do trabalho** | projeto, root |
| ledger / manifest | **registro da coleta** | ledger, manifest |
| export JSON | **salvar suas decisões num arquivo** | export, JSON, payload |
| permit / evidence | **anotar de onde veio e o que dá pra usar** | permit, evidência |
| contact sheet | **tirinha de quadros** (ou "os quadros do trecho") | contact sheet, sheet |
| fetch / verify | **baixar os trechos aprovados** / **conferir os arquivos** | fetch, verify |
| cooldown / rate limit | **pausa obrigatória do site** | cooldown, 429, rate limit |
| reference_only | **só pra ver, não dá pra baixar** | reference_only |

### 4.1 Como o agente narra cada etapa

**Descobrir / abrir**

- **Antes** (`README.md:51`): `### 0. Instale a stack inteira` + tabela de 6 ferramentas com coluna "Sem ela".
- **Depois:** manter a tabela, mas **abaixo** de um bloco novo:
  > **Nunca usou terminal? Comece por aqui.**
  > Cole isto no Claude Code ou no Codex e deixe ele instalar tudo pra você:
  > `/get-brolls-setup`
  > Se der algum erro, cole o erro na conversa. Ele sabe resolver.
  > *(A lista completa do que é instalado está logo abaixo, se você quiser ver.)*
- **Por quê:** inverte a ordem "parede → promessa" para "caminho → detalhe". O comando já existe (`README.md`, Comandos item 1); só não está no lugar onde a pessoa que tem medo chega primeiro. Ataca D1/D2.

**Instalar — resultado do doctor**

- **Antes** (`README.md:64`): "`doctor` é o gate: `summary.missing` vazio e `contact_sheet.labels: true`".
- **Depois** (o agente traduz o JSON, sempre):
  > ✅ **Tudo pronto.** Achei as 6 ferramentas que preciso.
  >
  > ⚠️ **Quase.** Falta uma coisa: seu FFmpeg veio sem o pedaço que escreve os números nos quadros. Funciona sem isso, mas a tirinha de quadros sai sem o tempo de cada um. Pra resolver: `brew reinstall ffmpeg`. Quer que eu siga assim mesmo?
- **Por quê:** estrutura de erro *o que aconteceu + por que + como resolver*, e transforma o achado #68 (exit 0 com labels=false) de bug silencioso em escolha consciente.

**Pedir — confirmação**

- **Antes:** não existe copy. A SKILL vai direto para `rules`/`references` (`SKILL.md:26`).
- **Depois:** o bloco PEDIDO de §3.3, seguido de "Tá certo assim? Se estiver, eu começo — leva uns minutos."
- **Por quê:** ataca D7/D9 e cria o contrato que o próprio PRD chama de cadeia "pede → categoriza" (`eval/README.md:19-22`).

**Esperar — progresso**

- **Antes:** silêncio; no máximo `status --project` como comando técnico (`SKILL.md:37`).
- **Depois** (uma linha por marco, nunca mais que isso):
  > 🔎 Procurando o material do item 1 de 4 — *lançamento do Starship*.
  > 👀 Achei 7 possibilidades. Vendo cada uma pra descartar o que não mostra o que você falou.
  > 🎞️ Montando a prévia de 3 trechos. É a parte mais lenta, ~1 min.
  > ✅ Pronto pra você olhar.
- **Por quê:** ataca D10. Cada linha responde "onde estamos" na cadeia do PRD sem expor comando.

**Esperar — pausa de rate limit**

- **Antes** (comportamento descrito em `SKILL.md:28`): "um HTTP 403/429 abre cooldown na fila: pare, não insista."
- **Depois:**
  > ⏸️ O Instagram pediu uma pausa (isso é normal quando se baixa vários seguidos). Volto a buscar daqui **40 minutos** e te aviso. Os 3 trechos que já peguei continuam aqui — pode ir revisando.
- **Por quê:** ataca D11/D12. Nomeia a causa (não é culpa dela, não é bug), dá prazo concreto e oferece trabalho útil no intervalo.

### 4.2 Como entregar a URL do Storyboard

- **Antes** (`SKILL.md:34`): "entregue ao usuário a URL `http://localhost:8767/review.html` e avise que abrir por `file://` pode desativar o salvamento local — exporte o JSON antes de fechar a página."
- **Depois:**
  > **Sua página de escolhas está pronta:** http://localhost:8767/review.html
  > Clique no link (ou cole no navegador). Você vai ver 6 trechos; em cada um, diz **Aprovar**, **Pedir ajuste**, **Reprovar** ou **Outra fonte**.
  >
  > Duas coisas só:
  > • **Não feche essa aba antes de clicar em "Salvar minhas escolhas"** no fim da página — é o que me manda de volta o que você decidiu.
  > • Enquanto essa página estiver aberta, deixe esta conversa parada. Eu espero.
  >
  > Terminou? Me avisa aqui que eu continuo.
- **Por quê:** ataca D13/D15. Remove `file://` (a pessoa nunca vai abrir por `file://` se o agente mandou o link do `serve`; avisar cria medo sem ação). Renomeia "Exportar revisão" para a consequência real. Diz explicitamente que o agente está esperando — resolve a ambiguidade de "acabou ou travou?".

### 4.3 Como explicar direitos de uso (o `permit`)

Este é o texto mais importante do documento, porque é onde o produto transfere responsabilidade legal.

- **Antes 1** (`scripts/getbrolls/storyboard.py:23`, rodapé de toda página): `A seleção visual não concede permissão de uso. Confira as condições de cada fonte.`
- **Antes 2** (`scripts/getbrolls/rendering.py:67-70`): `Uso: a confirmar` / `registrado pelo usuário` / `restrito`.
- **Antes 3** (README, quickstart): `permit --candidate "<ID>" --evidence "Condições de uso reais dessa fonte"`.

**Depois — rodapé do Storyboard:**
> Aprovar aqui significa "quero esse trecho", não "posso publicar esse trecho". Antes de subir seu vídeo, confira a licença de cada fonte — o link para o original está em cada card.

**Depois — campo do card** (substitui `Uso: a confirmar`):
> **Pode usar?** Ainda não conferido · Você anotou que pode · Uso restrito

**Depois — o momento do `permit`, dito pelo agente:**
> Antes de baixar, preciso de uma coisa sua. Eu sei **de onde** cada trecho veio, mas eu **não sei se você pode publicar** — isso muda por canal, por país e por tipo de vídeo, e quem responde por isso é quem publica.
>
> Abra o link do original (tá no card) e olhe a licença ou a descrição. Depois me diga, com suas palavras, o que você encontrou. Exemplos do que serve:
> • "Canal oficial da NASA, domínio público"
> • "Licença Creative Commons BY no próprio vídeo"
> • "Falei com o autor no direct e ele autorizou"
> • "É citação jornalística curta e eu vou creditar"
>
> Eu guardo isso junto do arquivo, num arquivo de créditos. Se você não tiver certeza de algum, me diga "esse eu pulo" — eu não baixo.

- **Por quê:** o desenho do produto já decidiu que a responsabilidade é de quem produz (`SKILL.md:26`). A copy anterior **afirmava** isso sem **operacionalizar**. A nova versão: (a) explica por que o software não pode responder, (b) dá exemplos concretos do formato de resposta, (c) oferece a saída "pulo esse" — sem a qual a pessoa inventa uma justificativa só para destravar o fluxo, que é exatamente o risco que `--evidence` tenta evitar.

### 4.4 A resposta "onde estão meus arquivos"

- **Antes** (README, quickstart): "`verify` responde `"count": 1` e o clipe aprovado está em `/caminho/meu-video/brolls/clips/`, com origem, autor e decisão registrados em `brolls/credits.md`."
- **Depois** (o agente diz isso ao terminar, sem ser perguntado):
  > 🎬 **Prontos: 4 arquivos.**
  >
  > `~/Desktop/brolls-spacex-17set/brolls/clips/`
  >
  > | Arquivo | O que é | Duração |
  > |---|---|---|
  > | `01-starship-decolagem.mp4` | Decolagem do Starship, Boca Chica | 6s |
  > | `02-webb-telescopio.mp4` | Telescópio James Webb, acervo NASA | 5s |
  > | … | | |
  >
  > É só arrastar pro seu editor. Os arquivos são 1080p e já vêm cortados no trecho que você aprovou.
  >
  > Na mesma pasta tem um **`credits.md`**: de onde saiu cada um, quem é o autor e o que você me disse sobre poder usar. Guarde junto do projeto — é sua prova se alguém perguntar.
  >
  > Quer mais trechos desse mesmo vídeo? Me chama de novo e diz "abre a pasta spacex-17set" que eu continuo de onde paramos.
- **Por quê:** ataca D17, D18 e D19 numa tacada. Transforma `credits.md` de detalhe de implementação em resposta direta à ansiedade nº 2. E o último parágrafo é a **única** affordance de reuso que a persona vai encontrar em toda a experiência.

### 4.5 Erro de revisão desatualizada

- **Antes** (`scripts/getbrolls/review.py:86-89`): `Revisão desatualizada para <id>: a decisão mudou ou o arquivo não contém sua versão. Execute review, confira as decisões e exporte um novo JSON.`
- **Depois:**
  > Esse arquivo de escolhas é de uma versão anterior da coleta — alguma coisa mudou depois que você decidiu (o trecho foi reajustado, por exemplo). Pra não usar o corte errado, vou abrir a página de novo com o estado atual. Suas decisões anteriores continuam marcadas; é só reconfirmar e salvar.
- **Por quê:** ataca D20. A mensagem atual descreve o mecanismo; a nova descreve a consequência e promete que nada se perdeu — que é a pergunta real da pessoa.

### 4.6 Item sem prévia

- **Antes** (`scripts/getbrolls/rendering.py:238` e `storyboard.py:34,62`): `Miniatura da fonte · sem prévia` / `Sem prévia`.
- **Depois:** **Não consegui gerar o movimento — veja o original no link**
- **Por quê:** "sem prévia" descreve uma ausência do sistema; a nova versão descreve a ação disponível. O badge da galeria pode ficar curto: **"só imagem"**.

---

## 5. Crítica da página de revisão para quem é novato

Leitura de `scripts/getbrolls/storyboard.py`, `scripts/getbrolls/rendering.py` e `assets/review.js`/`review.css`.

### 5.1 O que já está certo — preservar

- **Hierarquia visual funciona.** Player grande + galeria + filtro "Só pendentes" (`storyboard.py:76`) é o layout certo para decisão em série.
- **O balão de fala é a melhor ideia da página.** `Fala do roteiro` + a citação (`rendering.py:115-117`) coloca lado a lado "o que eu falo" e "o que vai aparecer" — é literalmente o critério de decisão.
- **A barra de posição do corte** (`rendering.py:81-83`) responde visualmente "esse trecho é de onde do vídeo", sem número.
- **Modos de GIF** (estático / ao passar o mouse / ligado — `storyboard.py:73-75`) é generosidade real com máquina fraca.
- **Contadores** `X aprovados · Y com pedidos · Z pendentes` (`review.js:117`) — linguagem já humana. Manter.
- Validado em 375 px sem overflow (`...release-2-3.md:22`).

### 5.2 Rótulos — problemas concretos

| Onde | String atual | Problema para a persona | Proposta |
|---|---|---|---|
| `rendering.py:22` | `<h2>Decisão</h2>` | Frio e ambíguo — decisão sobre o quê? | **"Esse trecho serve?"** |
| `rendering.py:56` | `<code>{c['id']}</code>` em destaque no card | Um hash. Ela não vai usar isso nunca; ocupa lugar nobre | Mover para um `title`/tooltip. No corpo, usar **"trecho 3 de 6"** |
| `rendering.py:88` | `Decisão de coleta: <motivo>` | "Decisão de coleta" colide com o botão "Decisão" ao lado — dois "decisão" com sentidos opostos (a do agente e a dela) | **"Por que eu escolhi este:"** |
| `rendering.py:67-70,92` | `Uso: a confirmar` | "a confirmar" por quem? Parece que o sistema vai confirmar | **"Pode usar? Ainda não conferido"** (§4.3) |
| `rendering.py:145,157` | `Contact sheet · N quadros` / `alt="Contact sheet do trecho"` | Termo de fotografia analógica. Zero chance de reconhecimento | **"Os quadros do trecho (N)"** |
| `rendering.py:47-48` | `Imagem estática` / `não definido` como intervalo | "não definido" parece defeito | **"Imagem parada"** / **"vídeo inteiro"** |
| `storyboard.py:22` | subtítulo `Prévias, intervalos e fontes para revisar.` | Três substantivos técnicos, nenhuma instrução | **"Veja cada trecho e diga se serve. Leva uns 2 minutos."** |
| `storyboard.py:45` | `Gravação não fornecida para este quadro.` | "Gravação" aqui significa o vídeo dela, mas ela lê como "gravação da fonte" | **"Sem imagem do seu vídeo aqui"** |
| `storyboard.py:81` | `Nenhum quadro nesta coleta. Importe uma fonte e prepare a prévia para começar.` | Empty state instruindo comandos de CLI que ela não roda | **"Nada aqui ainda. Volte pra conversa e peça os trechos — eu preencho essa página."** |

### 5.3 Botões de decisão

Atual (`rendering.py:23-26`): **Aprovar · Pedir ajuste · Reprovar · Outra fonte**.

Problemas:
1. **Quatro opções, duas redundantes na cabeça dela.** "Pedir ajuste" e "Outra fonte" são a mesma emoção ("não é bem isso") com destinos diferentes. A diferença — ajustar o intervalo vs. buscar outro vídeo — é invisível no rótulo.
2. **"Reprovar" é agressivo e ambíguo.** Reprovar o vídeo? O trabalho do agente?
3. **Nenhum rótulo diz o que acontece depois.** O princípio de CTA ("faça o rótulo corresponder ao resultado") está violado nos quatro.
4. **O comentário é obrigatório em duas delas** (`review.js:316`, `review.py:103-104`) mas isso só aparece **no momento da exportação**, como bloqueio. Ela decide 6 itens, clica em salvar, e leva um "não".

Proposta:

| Atual | Proposto | Micro-ajuda sob o botão |
|---|---|---|
| Aprovar | **Serve** | — |
| Pedir ajuste | **Quase — mude o pedaço** | "diz qual parte você queria" |
| Outra fonte | **Procura outro vídeo** | "diz o que faltou" |
| Reprovar | **Não serve** | — |

E: mostrar o campo de comentário **já aberto** ao clicar em "Quase" ou "Procura outro", com o botão de salvar desabilitado e a dica *"me conta em uma linha"* — em vez de validar no fim (`review.js:316`). Erro no ponto de entrada, não no ponto de saída.

### 5.4 O passo de exportar — o ponto mais frágil da página

Atual (`review.js:47`): `Próximo pendente →` · `Exportar revisão` · `Imprimir / PDF`, mais um status `Salvo neste navegador. Exporte para enviar.` / `Salvamento local indisponível. Exporte antes de fechar.` (`review.js:130-132`).

Problemas:
1. **"Exportar revisão" não diz para onde nem para quem.** A pessoa acabou de "aprovar" seis coisas; por que precisa exportar? Parece redundante.
2. **Contradição percebida:** "Salvo neste navegador" + "Exporte para enviar" = "tá salvo mas não tá salvo". Para quem não entende `localStorage`, isso é ruído puro.
3. **O caminho de volta é manual e não documentado na página.** O JSON cai em Downloads e alguém tem que rodar `import-review --file ... --by "..."` (`SKILL.md:34`). A página **não diz isso em lugar nenhum**. É o maior buraco da jornada: a página termina, e a pessoa não sabe que precisa voltar para a conversa.
4. **`--by` exige um nome** (`review.py:59`) que ninguém pediu à pessoa.
5. **"Imprimir / PDF" compete visualmente com a ação principal** e serve a um job raro.

Proposta:
- Renomear para **"Salvar minhas escolhas"**, botão primário, sozinho e grande no fim da lista.
- Depois do clique, **substituir a barra por uma instrução de volta**:
  > ✅ Salvei o arquivo `minhas-escolhas.json` na sua pasta de Downloads.
  > **Volte para a conversa e diga "pronto"** — eu leio o arquivo e continuo.
- Trocar o status de armazenamento por: **"Suas escolhas ficam guardadas nesta aba enquanto ela estiver aberta."** e, no caso de falha, **"Esta aba não consegue guardar suas escolhas. Salve assim que terminar cada um."**
- Mover "Imprimir / PDF" para um menu secundário.
- Pedir o nome do revisor (`--by`) **na página**, campo único opcional com default "você", em vez de exigir na CLI.

### 5.5 Outros pontos de confusão

- **A galeria não mostra progresso de tarefa.** Tem contadores, mas não tem "3 de 6". Sugestão: barra fina no topo — *"Faltam 3"*.
- **Nenhum atalho de teclado.** Para 8+ itens, `A`/`R`/`→` economizaria muito. Custo baixo, o JS já centraliza o estado (`review.js:22-35`).
- **Nada explica o que é "pendente".** Badge `Pendente` (`review.js:8`) sem tooltip. Sugestão: **"você ainda não disse"**.
- **O card é um `<a>` inteiro que abre a fonte** (`rendering.py:102-105`). Clique exploratório leva a pessoa para fora da página no meio da tarefa. Sugestão: restringir o link à linha "Abrir fonte original ↗", que já existe (`rendering.py:96`).

---

## 6. Recomendações priorizadas (impacto × esforço)

Esforço em dias-pessoa aproximados. Impacto = quantas dores da §2 são removidas, com peso para as fases de vale (instalar/receber).

### 6.1 Matriz

```
  ALTO  │  R1 Entrevista inicial        │  R6 Instalador pra leigo
 IMPACTO│  R2 Copy da entrega           │  R7 Storyboard compartilhável
        │  R3 Texto do permit           │
        │  R4 Rótulos do Storyboard     │
        │  R5 "Salvar minhas escolhas"  │
        ├───────────────────────────────┼───────────────────────────────
  MÉDIO │  R8 Copy de espera            │  R11 Duração/estrutura no search
 IMPACTO│  R9 Glossário único           │  R12 Atalhos de teclado
        │  R10 doctor em português      │
        └───────────────────────────────┴───────────────────────────────
           BAIXO ESFORÇO (≤2d)            ALTO ESFORÇO (>2d)
```

### 6.2 Detalhamento

| # | Recomendação | Dores | Impacto | Esforço | Onde mexer |
|---|---|---|---|---|---|
| **R1** | **Entrevista de 7 perguntas + template de pedido** (§3) | D7, D8, D9 | **Alto** | ~1d | `SKILL.md` (nova seção antes de "Fontes e preparação"), espelho `skills/get-brolls/SKILL.md` |
| **R2** | **Copy de entrega "onde estão meus arquivos"** (§4.4), incluindo tabela de arquivos e convite de reuso | D17, D18, D19 | **Alto** | ~0,5d | `SKILL.md:36`; opcionalmente resumo humano em `commands.py` |
| **R3** | **Reescrever a explicação de direitos/permit** (§4.3) nos 3 pontos | D16, ansiedade nº2 | **Alto** | ~1d | `storyboard.py:23`, `rendering.py:67-70`, `SKILL.md:35`, README quickstart |
| **R4** | **Renomear rótulos do Storyboard** pelo glossário (§4.0, §5.2) | D14 | **Alto** | ~1d | `rendering.py:22,47-48,56,88,145,157`, `storyboard.py:22,34,45,62,81` |
| **R5** | **"Exportar revisão" → "Salvar minhas escolhas" + instrução de volta** (§5.4) | D15, buraco do retorno | **Alto** | ~1d | `assets/review.js:47,130-132`; `--by` opcional em `review.py:59` |
| **R6** | **Caminho de instalação para leigo:** `/get-brolls-setup` no topo do README + doctor que traduz e propõe o conserto | D1, D2, D3, D4, D5 | **Alto** | ~3d (inclui gate do doctor, issue #34) | `README.md:51-64`, `README.en.md`, comando de setup, `doctor` |
| **R7** | **Storyboard compartilhável** (arquivo único auto-contido ou link) para aprovação de cliente | D13, J6 | Alto | ~5d | `storyboard.py`/`serve.py` |
| **R8** | **Copy de progresso e de pausa** (§4.1) | D10, D11, D12 | Médio | ~0,5d | `SKILL.md` (nova subseção "Como narrar"), `queue --help` (achado #71) |
| **R9** | **Glossário único aplicado a SKILL/GUIDE/README/UI** (§4.0) | consistência geral | Médio | ~2d | transversal |
| **R10** | **`doctor`/`--help` 100% PT-BR e legível** | D6 | Médio | ~0,5d | `instagram_pairs --help` (achado #70), saída do `doctor` |
| **R11** | **Devolver duração e estrutura no `search`** — mata a fricção nº 1 da rodada cega | fricção nº1, previews de 1ª | Médio (alto para o agente) | ~3d | `providers.py`, `commands.py` |
| **R12** | **Atalhos de teclado + "Faltam N" no Storyboard** | §5.5 | Médio | ~1d | `assets/review.js` |

### 6.3 Sequência sugerida

- **Sprint 1 (fazer agora, ~4d, tudo copy):** R1, R2, R3, R4, R5. Zero risco de regressão funcional, cobre os dois vales da jornada. Rodar smoke de 3 casos depois, como manda `eval/README.md:57`.
- **Sprint 2 (~4d):** R6, R8, R10 — a fase de instalação, que é onde a persona desiste antes de ver o valor.
- **Sprint 3 (~9d):** R7, R9, R11, R12.

### 6.4 Como medir se funcionou

A rubrica atual mede o agente, não a pessoa (`eval/rubric.md:59-78`). Proposta de 4 métricas complementares, para uma rodada moderada com 5 criadores reais sem experiência de CLI:

| Métrica | Como medir | Meta inicial |
|---|---|---|
| **Chegou ao Storyboard sozinha** | % que instala e vê a página sem ajuda humana | ≥ 60% |
| **Entendeu o permit** | % que escreve uma evidência real (não inventada, não vazia) sem o agente sugerir texto | ≥ 70% |
| **Achou os arquivos** | % que aponta a pasta correta quando perguntada, sem reabrir a conversa | ≥ 90% |
| **Voltou** | % que inicia uma segunda coleta em 7 dias | ≥ 40% |

Nenhuma delas substitui os testes cegos — elas medem a outra ponta, exatamente a que a suíte atual declara fora do escopo.

---

## 7. Limites desta pesquisa

- **Não houve entrevista com usuário real.** Tudo aqui é síntese de artefatos e leitura de código. As dores estão ancoradas em evidência documental, mas a **priorização** entre elas é julgamento, não dado.
- **Não executei o produto.** Não instalei, não rodei `doctor`, não abri o Storyboard no navegador. A crítica da página vem da leitura do gerador (`storyboard.py`, `rendering.py`, `review.js`/`review.css`), não de uso.
- **A persona "gente burra" é um constructo do dono do produto**, não um segmento validado. A validação de R1–R6 pede 5 sessões de usabilidade moderada com criadores reais — ~1 semana, conforme a tabela de métodos do protocolo de pesquisa.
- Os `eval/runs/` têm **uma única rodada** (baseline 2.3.7). Sem série temporal, "melhorou" ainda não é afirmável.
