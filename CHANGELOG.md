---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-16
tags: [get-brolls]
---

# Changelog

## Não lançado

- **Correção:** `scripts/install.ps1` falhava no Windows PowerShell 5.1 (padrão do Windows 11) porque as aspas duplas internas dos snippets `python -c` eram removidas ao chamar o executável nativo, gerando `SyntaxError`. Os snippets agora usam aspas simples escapadas. A CI passa a executar o instalador Windows também em PowerShell 5.1 e um teste impede a regressão. ([#23](https://github.com/engenheirodevideo/get-brolls/issues/23))

## 2.3.7 — 2026-09-16

- Adiciona variáveis opcionais que fixam caminhos de ferramentas: `GB_YTDLP_PATH`, `GB_VENV_PATH`, `GB_FFMPEG_PATH` e `GB_FFPROBE_PATH`. Valem tanto pelo ambiente do processo quanto pelo `.env` da skill ou por `python3 scripts/gb.py --env-file CAMINHO <subcomando> …`, com `--env-file` na raiz do parser, antes do subcomando.
- Define a precedência: variável explícita vence a descoberta atual. Com a variável ausente ou vazia, o comportamento é idêntico ao anterior — `.venv/Scripts` e `.venv/bin` para yt-dlp, `PATH` para ffmpeg/ffprobe.
- Recusa caminho inválido em vez de voltar em silêncio à descoberta: um destino inexistente, sem permissão de execução ou, no caso de `GB_VENV_PATH`, que não seja diretório, encerra o comando com erro nomeando a variável e o caminho.
- `doctor` passa a listar os pins ativos em `tool_paths`, uma linha por variável, e reflete o pin em `executables`. Sem variáveis definidas, `tool_paths` sai vazio.
- Não há `GB_PYTHON_PATH`: o código não reinvoca o interpretador Python em nenhum ponto.
- Adiciona `tests/test_env_paths.py` com 16 regressões offline de precedência, padrão inalterado, erro de caminho inválido e layouts `.venv` POSIX e Windows.
- Torna a CLI autoexplicativa: `help` em todos os subcomandos e argumentos, `--version` na raiz e `doctor` com veredito `summary` (ok, missing com o comando que resolve, optional) além de `get_brolls` e `python` — a chave `runtime` passou a se chamar `python`.
- Nomeia o instalador no erro de yt-dlp ausente e separa "FFmpeg/ffprobe não encontrado" de "arquivo/intervalo inválido", apontando `doctor` nos dois casos.
- Adiciona o comando de plugin `/get-brolls-setup` (`commands/get-brolls-setup.md`), que executa `--check`, o instalador do sistema e o `doctor` pela raiz do plugin e reporta o veredito em uma linha.
- Documenta o acionamento do plugin (contexto, `/get-brolls:get-brolls`, `/get-brolls-setup`) e acrescenta o bloco "Primeiro B-roll em 5 minutos" nos dois READMEs, com fonte sem chave, servidor local do Storyboard e saída esperada.
- Os dois SKILL.md passam a mandar servir `brolls/` em `127.0.0.1:8767` e avisar que `file://` pode desativar o salvamento local antes da exportação do JSON.
- Substitui a `description` dos dois SKILL.md por uma descrição bilíngue com o que a skill faz, quando acioná-la e o que está fora de escopo; os READMEs distinguem a skill de coletores genéricos de download.
- Troca os cinco marcadores do teste do espelho por um contrato estrutural: corpo da raiz e do espelho comparados linha a linha, com os caminhos `${CLAUDE_PLUGIN_ROOT}` normalizados e apenas a seção "Instalação e contexto" autorizada a divergir.
- Reescreve `GEMINI.md` como configuração real: o Gemini CLI carrega `GEMINI.md` a partir do diretório de trabalho, então o roteador precisa ser importado por `@/caminho/absoluto/.../SKILL.md` no `GEMINI.md` do usuário.
- Os dois SKILL.md mandam ler o CHANGELOG quando o `doctor` reportar uma versão diferente da documentada.
- SECURITY ganha a tabela de egress (instalação, execução e o que nunca sai da máquina) e a declaração de ausência de telemetria.
- Os instaladores explicam a falha do `pip`: informam o Python em uso e a faixa validada do conjunto fixado (3.11–3.13), continuando fail-fast. A matriz de CI segue em 3.11/3.13 porque `pycryptodomex==3.23.0` ainda não publica wheel cp314.
- Adiciona `.github/ISSUE_TEMPLATE/bug_report.md` (SO, versão do Python e saída completa do `doctor`), `config.yml` com issues em branco habilitadas e `PULL_REQUEST_TEMPLATE.md` com o bloco de verificação do AGENTS.
- Adiciona `.github/workflows/release.yml`: em tags `v*`, extrai a seção da versão no CHANGELOG e publica a release pelo `gh`, sem sobrescrever uma release existente e sem novas actions de terceiros.
- Documenta que `skills/` e `.claude-plugin/` são artefatos do plugin e podem ser omitidos ao copiar para o Codex, e registra no GUIDE a permissão opcional `python3 */gb.py *`.
- Aceita `GB_FONT_FILE` no `.env` (fonte TrueType do contact sheet dos helpers Bash opcionais) e faz o erro de variável desconhecida nomear a variável recusada e o conjunto aceito.
- Torna `AGENTS.md` o hub principal do repositório: uma tabela "Mapa do repositório" roteia cada público — operar a skill (SKILL.md e o espelho do plugin), instalação por agente (Codex, skill e plugin do Claude Code, Gemini CLI), GUIDE, QUALITY, CONTRIBUTING, SECURITY e CHANGELOG — sem remover nenhuma regra de manutenção existente. `CLAUDE.md` e `GEMINI.md` viram roteadores finos que apontam primeiro para o hub, e os dois READMEs nomeiam `AGENTS.md` como índice de agentes e mantenedores.
- Adiciona o subcomando `status --project`, que responde "onde estamos?" lendo o estado já gravado (manifesto, candidatos, decisões e journal) e devolve contagem e lista de IDs por etapa: candidatos encontrados, prévias geradas, decisões pendentes/aprovadas/rejeitadas, itens com `permit` registrado, itens entregues e verificados, além do próximo passo do fluxo. É somente leitura: não grava manifesto, candidatos, prévias, clipes nem eventos.
- Padroniza o relato de progresso: `search`, `resolve`, `preview`, `review`, `import-review`, `permit`, `fetch` e `verify` passam a devolver um campo `summary` com uma linha em português no formato verbo + objeto + resultado. O campo é aditivo — nenhuma chave existente muda de nome, tipo ou posição.
- Documenta o `status` e a convenção do campo `summary` no GUIDE, acrescenta uma frase sobre `status --project` na seção "Revisão e entrega" dos dois SKILL.md e cita o comando na seção de terminal dos dois READMEs.
- Fixa o padrão editorial **literal primeiro** nos dois SKILL.md, no GUIDE e nos READMEs: o material padrão é footage, print ou imagem real do fato, pessoa, produto, notícia ou tela que a narração cita; bancos de stock (Pexels/Pixabay) entram somente quando o usuário pedir stock explicitamente.
- Registra a divisão de responsabilidade editorial: as condições de uso do material são de quem produz o vídeo; a skill responde pela fidelidade/literalidade e pelo registro de origem de cada asset. O fluxo de `permit` e proveniência continua sendo o mecanismo desse registro, sem alteração de lógica.
- Adiciona `tests/test_status.py` (9 regressões offline do `status`, do campo `summary` e da garantia de somente leitura) e três regressões do hub em `tests/test_repository.py`.

### Correções da revisão tripla

- `status` passa a ser somente leitura de verdade: abre o ledger sem criar `brolls/candidates`, `previews` e `clips`, relata uma gravação interrompida como `journal.recovered_write: "pending"` sem concluí-la e recusa um projeto inexistente com "Projeto não encontrado em …; nenhum arquivo foi criado.", sem escrever nada — nem diagnóstico.
- `status` deixa de pedir a trava exclusiva do projeto: agora responde mesmo durante um `fetch` em andamento, em vez de falhar com "Outro comando está usando este projeto".
- `status` deixa de morrer com o projeto: `RULES.md` inválido vira `rules_error` no relatório, `events.jsonl` e `references.json` corrompidos degradam para contagem com `error`, e uma mudança de formato-alvo nas regras vira `format_pending` mais um aviso de que o próximo comando invalidará as aprovações.
- `release.yml` ganha portões antes de publicar: confere `__version__` contra a tag, roda `python3 -m unittest discover -s tests`, extrai a seção do CHANGELOG por comparação literal (a versão não é mais tratada como expressão regular) e marca `--prerelease` quando a tag tem hífen.
- Restaura as GitHub Actions fixadas nas v7 (`actions/checkout` 7.0.1 e `actions/setup-node` 7.0.0) que uma alteração anterior havia rebaixado para v4, e passa a usar o mesmo checkout no `release.yml`. A regressão compara apenas a referência fixada por SHA, sem exigir o comentário da versão.
- Os pins `GB_*_PATH` resolvem para caminho absoluto antes de validar, então um valor relativo não muda de significado conforme a pasta atual; um diretório fixado informa "não é um arquivo executável"; e `GB_VENV_PATH` sem yt-dlp dentro falha nomeando a variável, a pasta e os layouts procurados, em vez de cair em silêncio no `PATH`.
- `doctor` fica resiliente a pin inválido: em vez de encerrar com erro, publica o pin quebrado em `summary.missing` com a mensagem do erro, sugere o instalador pelo caminho absoluto da skill e acrescenta o bloco `resolved` com o executável absoluto realmente usado por ferramenta. Os demais comandos continuam falhando de imediato.
- O teste do espelho passa a cobrir também a seção "Instalação e contexto": só linhas de mecânica de instalação podem divergir da raiz, a indentação conta e uma linha que normaliza para vazio falha em vez de sumir.
- Ajustes de relato: o `summary` de `search` mantém a observação do provedor, `approve` e `reject` ganham a própria linha, singular e plural concordam com as contagens, `preview` diz quando gerou somente referência estática e `resolve` recusa `--file`/`--url` vazios nomeando a opção.

### Revisão documental — testes cegos (sem alteração de versão)

- Institucionaliza o teste cego como medição **editorial** do produto, em `eval/`: `eval/README.md` descreve o processo (executor recebe só o roteiro, juiz e amostragem humana aplicam a rubrica), a cadência (rodada completa por release candidate, smoke de 3 casos após mudança de SKILL/prompt) e por que ele fica fora do CI — precisa de rede, sessão e tempo de agente.
- Adiciona `eval/rubric.md`: pontuação 0/0.5/1 por beat em quatro eixos (Reach, Literalidade, Preview, Disciplina), métricas da rodada e a separação obrigatória entre **ambiente** (URL/rede/sessão/quota) e **comportamento** (stock sem pedido, licença inventada, aprovação pelo próprio agente). Metas atuais: ≥80% de reach literal, 0 stock sem pedido, 100% de origem registrada e ≥90% de previews corretos.
- Adiciona `eval/corpus/` com 14 casos, cada um com `## Roteiro` (única parte mostrada ao executor) e `## Gabarito` (oculto): 4 de notícia factual, 3 exigindo print de UI, 2 de pessoa pública em evento, 2 de acontecimento local/nicho, 2 que pedem stock explicitamente e 1 armadilha com beat impossível/ambíguo.
- Registra a rodada inaugural em `eval/runs/2026-09-16-2.3.7-claude-opus.md` (clone → Storyboard em ≈4 min, 4 beats, 12 candidatos, 6 prévias, zero chave de API, zero stock, nada aprovado) e padroniza o relatório em `eval/runs/TEMPLATE.md`.
- Adiciona o comando de plugin `/get-brolls-eval` (`commands/get-brolls-eval.md`), que executa um caso às cegas até o Storyboard, para na revisão humana e preenche o relatório — com a regra explícita de que o executor nunca abre `## Gabarito`.
- Roteia a medição editorial: nova linha no hub do `AGENTS.md`, seção "Blind tests" no `QUALITY.md` com as métricas da baseline e um parágrafo no `GUIDE.md` ligando `eval/`. Acrescenta uma regressão em `tests/test_repository.py` que exige `eval/README.md`, `eval/rubric.md` e `eval/runs/TEMPLATE.md` publicados e o hub linkando o processo. Nenhuma mudança de código do produto: `scripts/` e os dois `SKILL.md` não foram tocados.

## 2.3.6 — 2026-09-16

- Empacota a skill como plugin do Claude Code: `.claude-plugin/plugin.json` descreve o plugin e `.claude-plugin/marketplace.json` transforma o próprio repositório em marketplace.
- Espelha a skill no layout `skills/get-brolls/SKILL.md`, com os caminhos internos resolvidos via `${CLAUDE_PLUGIN_ROOT}` a partir da raiz do plugin instalado.
- Documenta a instalação via `/plugin marketplace add engenheirodevideo/get-brolls` e `/plugin install get-brolls@engenheirodevideo` nos READMEs e no GUIDE.
- Preserva o fluxo clone-como-skill (Codex e instalações manuais) sem mudanças; o SKILL.md da raiz continua sendo a fonte canônica desse fluxo.
- Orienta o contexto de plugin instalado: resolução de `${CLAUDE_PLUGIN_ROOT}`, `--project` obrigatório, chaves via `python3 scripts/gb.py --env-file CAMINHO <subcomando> …` fora da pasta gerenciada e reinstalação das dependências após `/plugin update`.
- Adiciona testes offline que validam os manifestos do plugin, a igualdade da `description` entre os dois SKILL.md, a existência dos alvos `${CLAUDE_PLUGIN_ROOT}` e as regras operacionais do espelho.
- Atualiza SECURITY: o relatório privado de vulnerabilidades do GitHub está habilitado no repositório.
- Adiciona roteadores `CLAUDE.md` e `GEMINI.md` apontando para SKILL.md (operação) e AGENTS.md (manutenção), para descoberta de contexto no Claude Code e no Gemini CLI sem duplicar instruções.
- Nenhuma mudança de lógica do produto ou dos scripts.

## 2.3.5 — 2026-09-16

- Recusa JSONs de revisão baseados em decisões antigas, inclusive depois de rejeição ou de outra importação. Valida `reviewEpoch` além da assinatura do conteúdo, sem gravar parcialmente os itens de um lote inválido.
- Fixa conexões HTTP de APIs e downloads nos endereços públicos validados, preservando SNI, hostname e certificados HTTPS. Revalida DNS a cada nova tentativa; mantém redirects bloqueados e conecta diretamente, sem proxies automáticos do ambiente.
- Fixa as dependências Python nas versões instaladas pela CI anterior, registra o conjunto npm em `package-lock.json` e instala com `npm ci`. Os instaladores continuam usando `.venv/` e `.tools/` locais.
- Fixa as Actions pelos commits já utilizados na CI e prepara atualizações de dependências por PR com Dependabot.
- Acrescenta regressões offline para decisões obsoletas, DNS rebinding IPv4/IPv6, proxy, retries, redirects e validação TLS. Atualiza a orientação de migração.

## 2.3.4 — 2026-09-15

- Consolida todo o produto sob `scripts/getbrolls/`: CLI, provedores, Storyboard, coletor de pares Instagram e utilitários YouTube.
- Padroniza a marca pública como **GET B-ROLLS — ENGENHEIRO DE VÍDEO**.
- Faz o Storyboard gerado pela CLI usar a logo transparente oficial e remove o snapshot HTML preenchido que duplicava a interface real.
- Corrige o contact sheet para usar arquivo temporário portátil em macOS e Linux e cobre o comportamento com regressão automatizada.
- Adiciona descoberta de fontes Linux para manter título e numeração do contact sheet; `GB_FONT_FILE` permite definir uma fonte válida explicitamente.
- Torna a CLI nativa em Windows com trava `msvcrt`, descoberta da `.venv\\Scripts`, instalador e launcher Playwright em PowerShell.
- Promove macOS e Windows à matriz principal de CI; Linux permanece como plataforma secundária.
- Endurece o coletor Instagram com pinagem de DNS público, redirects desativados, nomes batch validados, saída confinada e publicação sem sobrescrever arquivos existentes.
- Torna o instalador PowerShell fail-fast e exercita a instalação completa em macOS e Windows na CI.
- Define UTF-8 explicitamente na CLI, nos arquivos e nos subprocessos para funcionar de forma consistente no Windows.
- Restringe configs Instagram a HTTPS público e impede que `output=` escape de `--config-output-root`.
- Mantém YouTube via yt-dlp sem API key e Instagram via navegador autorizado, dois streams, curl, FFmpeg e ffprobe.
- Instala dependências oficiais via PyPI e npm na máquina do usuário; nenhuma biblioteca de runtime ou sessão de navegador integra o repositório.
- Preview remoto preserva origem, autoria e intervalo absoluto. Fetch usa os mesmos bytes revisados depois da decisão humana e do registro das condições de uso.
- Corrige reuso de download parcial, descoberta da venv, runtime JavaScript e URLs Instagram com perfil antes de `/reel/`.
- Consolida README, GUIDE, QUALITY, AGENTS, segurança, contribuição e avisos de terceiros como documentação autônoma do produto.
- Define o repositório GitHub como fonte oficial da entrega; não há fluxo paralelo de pacote ZIP.
- Publica um README completo em inglês e adiciona navegação de idioma entre as duas versões.
- Credita **Bruno Moreira — Engenheiro de Vídeo** como autor e mantenedor, com o Instagram [@zbrunomoreira](https://www.instagram.com/zbrunomoreira/) nas duas versões do README.

## Histórico anterior

- **2.3.3:** estabilização de downloads, bancos Pexels/Pixabay e prévias; não foi considerada a entrega consolidada.
- **2.3.2:** organização inicial da skill para Codex e Claude Code.
- **2.3.1:** recuperação do ledger, regras, logs, GIFs e separação interna da CLI.
- **2.3.0:** RULES por projeto, memória de referências, imagens locais e captura pelo navegador.
- **2.2.0:** Storyboard com revisão exportável/importável e prévias estáticas ou animadas.
- **2.1.0:** roteamento multi-fonte, aprovação, evidência de uso, corte e verificação.
