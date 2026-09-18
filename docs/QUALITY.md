---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-17
tags: [get-brolls, quality, qa, evidence]
---

# Qualidade e evidências — GET B-ROLLS 2.4.0

Este documento reúne o estado de qualidade, as regressões cobertas, os limites conhecidos e as evidências reais por provedor. Resultados ao vivo são registros datados, não promessa de disponibilidade futura nem aprovação editorial.

## Blind tests

A suíte automatizada responde se o programa funciona; o **teste cego** responde se a skill entrega o que promete a um criador de conteúdo. O processo, os papéis (executor cego, juiz com gabarito, amostragem humana), a cadência e o corpus de 14 casos estão em [eval/README.md](../eval/README.md); a pontuação por beat e as metas, em [eval/rubric.md](../eval/rubric.md). É medição editorial, fora do CI de propósito: precisa de rede, sessão e tempo de agente, e todo relatório separa **ambiente** (URL fora do ar, sessão, quota) de **comportamento** (stock sem pedido, licença inventada, aprovação pelo próprio agente).

Rodada mais recente: [2026-09-16 — 2.3.7 — Claude Opus](../eval/runs/2026-09-16-2.3.7-claude-opus.md), a linha de base. Instalação limpa a partir do clone até um Storyboard revisável em **≈ 4 minutos** (instalador em 32s, primeira prévia em T+2min16s), com 4 beats, 12 candidatos e 6 prévias, sem nenhuma chave de API. Métricas: **reach literal 100%**, **stock sem pedido 0**, **origem registrada 100%**, **previews corretos 3/4 na primeira tentativa** (meta de 90% não atingida). Causa nomeada da única meta perdida: escolher `--start/--end` às cegas, porque `search` devolve `duration_s: null` — mesma raiz do recorte parcial do beat de keynote. Nada foi aprovado ou coletado pelo agente: a rodada para na revisão humana, por definição.

## QA da versão 2.4.0 — 17/09/2026

A 2.4.0 adiciona a entrevista de intake e o `BRIEF.md` por projeto (`init-brief`/`brief`), a aprovação pelo chat (`approve --all --channel chat --statement`), a declaração de responsabilidade sem editar arquivo (`permit --declared-by/--declaration-text`), a biblioteca de aprendizados entre projetos (`learn`/`library`), a análise da fonte antes de coletar (`inspect`, `preview --scan`), a pasta `entrega/` por beat (`deliver`), o Storyboard que salva as decisões dentro do projeto (`serve --background` + `POST /__save`, `import-review` sem `--file`), o próximo passo pronto em `status.summary.do`, e as flags `search --shot/--dry-run` e `init-rules --format`.

**O que foi testado.** A suíte unitária local passou inteira (505 testes no fecho da implementação, ampliada na onda final de correções); `ruff check`, `ruff format --check` e `pyright` saem em zero, agora também no job `quality` do CI; `scripts/check_anchors.py` confirma que toda âncora de `GUIDE.md` citada em SKILL.md, READMEs e `commands/*.md` resolve. As garantias de proveniência foram verificadas sem relaxamento: `require_fetch` continua exigindo aprovação humana, assinatura conferida e direitos permitidos com evidência; `signature()` mantém a invalidação por mudança de intervalo ou contexto; `validate_manifest` segue restrito a `previews/` e `clips/`; nenhuma feature nova preenche `rights.evidence` ou `approval` sem ação explícita de uma pessoa. A biblioteca entre projetos carrega `rights_not_transferable: true` em toda resposta e não faz `require_fetch` passar em projeto nenhum.

**Rodada cega.** Cinco executores independentes rodaram a 2.4.0-rc em 17/09/2026 contra YouTube e GitHub reais, cada um num caso diferente do corpus (notícia espacial, print de UI, aprovação pelo chat, entrevista preguiçosa e a armadilha do roteiro sem entidade nomeada). Registro consolidado em [eval/runs/2026-09-17-2.4.0-rc-claude-opus.md](../eval/runs/2026-09-17-2.4.0-rc-claude-opus.md). Nenhum executor aprovou nada por conta própria e nenhum inventou licença, entidade ou disponibilidade: todos pararam na revisão humana, como o contrato manda. As fricções que a rodada expôs viraram correções nesta mesma versão — `search --shot` e `--dry-run`, `inspect` com resumo e janelas de fallback em vez de lista vazia, `preview --scan` medido pela mídia que existe de fato, `preview --reference-only` sem intervalo, contagem de "decisões pendentes" só para quem tem prévia, Storyboard vazio reportado, `init-rules --format` e a página de item do `images.nasa.gov` aceita por `resolve --url`.

**Limites conhecidos desta rodada.** O ritmo e o cooldown do lote do Instagram continuam cobertos só por mocks: não houve nova sessão de captura real. Windows é exercitado apenas pela matriz do CI; a proteção de escrita no Windows (`deliver` congela o arquivo, `_thaw_unlink` devolve a escrita antes de apagar) tem regressão offline, não ensaio em máquina real. A varredura remota de `preview --scan` contra uma fonte real não entra na suíte: ela baixa mídia e leva minutos; o que a suíte prova é a grade, os rótulos e a tolerância a mídia mais curta, com fixture local.

## QA da versão 2.3.8 — 17/09/2026

A 2.3.8 adiciona a fila com ritmo (`queue`), o comando `serve`, pausas no `instagram_pairs.py`/yt-dlp, respeito a `Retry-After`, erros legíveis com stderr redigido e caches de intervalo/NASA/drawtext. A suíte unitária local passou (packaging, mirror e `--help` inclusos); consulte a linha de verificação no final desta seção para a contagem exata desta rodada. O ensaio cego de instalação (macOS, Python 3.14) repetiu busca e prévia reais no YouTube com sucesso, confirmando que o quickstart README continua funcional sem chave. O ritmo de lote do Instagram (`--pace`/`--max-per-run`/`--continue-on-error`, cooldown por `429`/`403`) foi validado somente com mocks — não houve nova sessão de captura real do Instagram nesta rodada, então o comportamento de cooldown/ritmo contra a CDN real permanece um limite conhecido, coberto por regressão offline. A instalação/testes em Windows correm apenas via CI (matriz do repositório); nenhuma máquina Windows local foi usada para esta QA.

## QA da versão 2.3.7 — 16/09/2026

A 2.3.7 adiciona os pins opcionais `GB_YTDLP_PATH`, `GB_VENV_PATH`, `GB_FFMPEG_PATH` e `GB_FFPROBE_PATH`, resolvidos num helper único em `scripts/getbrolls/config.py` e aplicados na chamada de mídia (`media.run`) e na descoberta do yt-dlp (`social.local_ytdlp`). 132 testes passaram localmente (86 da 2.3.6 mais 16 em `tests/test_env_paths.py`, 10 do contrato de autoexplicação da CLI em `tests/test_cli_help.py`, 2 de empacotamento do comando de setup, 4 de higiene do repositório, 2 do vocabulário do `.env`, 9 de observabilidade em `tests/test_status.py` e 3 do hub `AGENTS.md`). As regressões offline novas cobrem: comando de mídia idêntico ao anterior com as variáveis ausentes ou vazias; pin de ffmpeg e ffprobe aplicado ao primeiro argumento; `GB_YTDLP_PATH` vencendo a `.venv`; `GB_VENV_PATH` nos layouts `bin/` e `Scripts/`, sem cair na `.venv` padrão quando a pasta fixada não tem o executável; erro nomeando variável e caminho para destino inexistente, sem permissão de execução ou não-diretório; carga pelo `.env`/`--env-file` com o ambiente do processo prevalecendo; e `doctor` publicando `tool_paths` vazio sem pins e com uma linha por pin ativo. Conferência manual: `doctor` sem variáveis, `doctor` com `GB_FFMPEG_PATH`/`GB_FFPROBE_PATH` reais e corte/probe de um mp4 sintético pelos executáveis fixados. O coletor autônomo `scripts/getbrolls/instagram_pairs.py` não foi alterado: continua usando `ffmpeg`/`ffprobe` do `PATH`, preservando o comportamento de transporte.

As regressões documentais e de interface da mesma versão cobrem: `help` presente em todos os subcomandos e argumentos do parser; `--version` imprimindo `get-brolls 2.3.7`; `doctor` publicando `get_brolls`, `python` (no lugar de `runtime`) e o veredito `summary` com as três listas, o comando que resolve cada item ausente e as chaves opcionais não definidas; erro de yt-dlp ausente nomeando o instalador; distinção entre FFmpeg ausente e falha de arquivo/intervalo; presença e conteúdo de `commands/get-brolls-setup.md`; contrato estrutural do espelho `skills/get-brolls/SKILL.md` comparado linha a linha com a raiz (verificado falhando ao mutar uma regra e voltando a passar após restaurar); tabela de egress e ausência de telemetria no SECURITY; faixa de Python validada nas mensagens dos dois instaladores; templates de issue/PR; `release.yml` usando apenas `gh` e o checkout já fixado por SHA; e `GB_FONT_FILE` aceito no `.env` com erro de variável desconhecida nomeando a variável recusada. Nenhuma lógica editorial, de revisão ou de transporte foi alterada.

As regressões de observabilidade da mesma versão cobrem o novo `status` e a convenção do campo `summary`: contagem e lista de IDs por etapa num projeto sintético com um item em cada estágio (pendente, entregue e rejeitado); `summary` como primeira chave do JSON, com `line`, `stages` e `next`; a escada do próximo passo (`search` → `preview` → `review` → `permit` → `fetch` → `verify` → fluxo completo); o campo `summary` aditivo nos caminhos de `search`, `preview`, `fetch`, `verify`, `permit` e `import-review`, sem mutar o resultado original; e o ciclo real com FFmpeg de `resolve` a `status`. A garantia de somente leitura é verificada comparando conteúdo e mtime de todos os arquivos do projeto antes e depois de duas execuções de `status`; os arquivos de auditoria que a CLI grava em qualquer comando (`diagnostics.jsonl` e a trava `.command.lock`) ficam fora da comparação por serem infraestrutura de diagnóstico, não estado do projeto. O hub `AGENTS.md` tem três regressões: links para todos os destinos roteados, presença de cada acionamento por agente e os roteadores (`CLAUDE.md`, `GEMINI.md`, READMEs) apontando para o hub. As mudanças editoriais de literal primeiro são de documentação e prompt; o fluxo de `permit` e proveniência não foi alterado. As evidências das versões anteriores permanecem válidas.

A 2.3.7 passou por uma rodada adversarial de três revisões independentes sobre o mesmo código. Os achados foram corrigidos e cobertos por regressão: `status` era somente leitura no papel mas criava a árvore `brolls/`, concluía uma gravação pendente e pedia a trava exclusiva do projeto (agora abre o ledger sem recuperar, responde durante um `fetch` em andamento e recusa projeto inexistente sem escrever nada); `release.yml` publicava sem conferir versão nem rodar testes e tratava a versão como expressão regular na extração do CHANGELOG; as Actions fixadas haviam sido rebaixadas de v7 para v4 para satisfazer o próprio teste, que exigia o comentário `# v4`; os pins `GB_*_PATH` dependiam da pasta atual e `GB_VENV_PATH` sem yt-dlp caía em silêncio no `PATH`; `doctor` encerrava com erro diante de um pin inválido e sugeria o instalador por caminho relativo; e o teste do espelho deixava a seção de instalação inteiramente livre, o que permitiria injetar uma regra editorial só no plugin. **148 testes passam localmente** (132 da rodada anterior mais 16 novos: somente leitura real do `status`, projeto inexistente, pendência preservada, resposta sob trava, degradação de `events.jsonl`/`references.json`, aviso de formato pendente, `rules_error`, pin relativo resolvido, pin de diretório, venv fixada sem yt-dlp, três do `doctor` resiliente, divergência do espelho, `resolve` com opção vazia e as concordâncias de singular/plural). O ciclo real com FFmpeg deixou de estar duplicado: vive apenas em `tests/test_cli.py`, que agora também confere as oito linhas de `summary`. O quickstart dos READMEs foi exercitado ao vivo contra a NASA em 16/09/2026, num projeto temporário: `search --provider nasa` devolveu candidatos reais, `preview` baixou a mídia e gerou GIF, poster e contact sheet, `review` publicou o Storyboard, a decisão foi registrada por `approve` (a página do Storyboard não é clicável num ensaio automatizado), `permit` gravou uma evidência sintética identificada como tal, `fetch` produziu o corte e `verify` respondeu "1 arquivo coletado: íntegro e decodificável". Esse ensaio é um registro datado de disponibilidade, não promessa futura nem aprovação editorial, e sua evidência de direitos não vale como licença. Nenhuma lógica editorial, de revisão ou de transporte foi alterada pelas correções.

## QA da versão 2.3.6 — 16/09/2026

A 2.3.6 é uma release de empacotamento: adiciona o plugin do Claude Code (`.claude-plugin/` e `skills/get-brolls/`) e documentação, sem nenhuma mudança de código do produto. 86 testes passaram localmente (80 da 2.3.5 mais 6 novos de empacotamento em `tests/test_plugin_packaging.py`, que validam manifestos, igualdade da `description` root↔espelho, alvos `${CLAUDE_PLUGIN_ROOT}` e regras operacionais do espelho). A estrutura foi validada por revisão dupla independente (estrutura do plugin e qualidade da skill). As evidências de código da 2.3.5 abaixo permanecem válidas para esta versão.

## QA da versão 2.3.5 — 16/09/2026

80 testes passaram localmente em macOS/Python 3.14.6, incluindo 10 regressões adicionais. As seis regressões iniciais dos defeitos falharam na 2.3.4 e passaram após a correção. DNS, sockets e TLS dos casos adversariais são simulados na fronteira de rede; urllib, parsing HTTP, geração do Storyboard, importação e persistência são reais. Nenhuma conexão adversarial foi feita contra redes públicas ou internas.

Cobertura nova: export anterior à rejeição; reimportação após decisão já aplicada; lote com `reviewEpoch` ausente sem gravação parcial; IPv4/IPv6 público fixado; proxies automáticos ignorados; revalidação DNS nas tentativas; destino misto público/privado recusado; HTTPS/porta 443 obrigatórios; redirects recusados; falha de certificado sem arquivo publicado. Novas exportações válidas continuam sendo aceitas.

Uma cópia limpa concluiu o instalador macOS com as dependências fixadas; `doctor` reconheceu yt-dlp e Playwright, e `pip check` não encontrou incompatibilidades. A [CI no main](https://github.com/engenheirodevideo/get-brolls/actions/runs/35053424856) executou instalação completa e testes em todos os cinco ambientes da matriz, incluindo Python 3.11, e passou em 16/09/2026.

As dependências Python fixadas vieram dos logs da [CI da 2.3.4](https://github.com/engenheirodevideo/get-brolls/actions/runs/35045895066), idênticas nas instalações macOS/Windows. Os ensaios sociais abaixo são históricos da 2.3.4 e não foram repetidos com sessões/chaves nesta correção. A matriz de instalação/testes passou no PR e novamente no merge para o main antes da publicação; os testes antigos não certificam atualizações futuras.

## Histórico — QA da versão 2.3.4

### Estado atual

**Validação da árvore consolidada: 70 testes passaram localmente em 15/09/2026.** O repositório é a fonte oficial da entrega; não existe uma seleção paralela de arquivos ou pacote ZIP para manter sincronizado.

Uma revisão adversarial executou a suíte em fonte e cópia limpa, instalação completa, `doctor`, `--help`, sintaxe Bash e JavaScript, busca de caminhos locais/segredos e inspeção do branding. A instalação limpa reconheceu yt-dlp 2026.08.19, EJS 0.8.0, Playwright CLI 0.1.20, FFmpeg e ffprobe. Nenhuma biblioteca externa, credencial ou sessão de navegador faz parte do repositório.

### Evidência por capacidade

| Capacidade | Evidência | Limite |
|---|---|---|
| YouTube sem API key | Busca real, aquisição 0–3 s e GIF; nova instalação limpa repetiu trecho 3–5 s em 1920×1080/25fps | Um vídeo público; aprovação permaneceu pendente |
| Instagram pares | Captura no Chrome, curl dos dois canais, merge e decodificação integral reais; MP4 50,226 s, 1076×1912, H.264/AAC | Um Reel público; identidade dos canais conferida por asset e prévia |
| Instagram Chrome logado | Nova tentativa concluiu captura/CDN/merge e GIF de 5 s | Sessão do próprio usuário; sem copiar cookies para a skill |
| TikTok | Vídeo @nasa/7665358680627399966 baixado via instalação limpa; trecho 3 s, 576×1024/30fps, H.264/AAC; decodificação e GIF passaram | Link anterior indisponível no navegador; houve bloqueios/erros em tentativas anteriores |
| Pexels/Pixabay | Busca, refresh, download integral, GIF e corte técnico reais com chaves temporárias | Ensaio dos adapters na rodada anterior; sem aprovação editorial fictícia |
| Commons/NASA | Busca, refresh e HEAD de mídia reais na rodada anterior | Não equivalem a download integral de todos os itens |
| Prévia → coleta final | Fixture real FFmpeg, cache/hash/offset, aprovação sintética e corte verificado | Simulação explícita, não aprovação atribuída ao usuário |
| Instalação | venv + import yt-dlp/EJS; npm + Playwright --version; --check valida Node 22+ | Executáveis do sistema conforme GUIDE; não instala extensões/login |

### Regressões cobertas

- YouTube e fontes sociais têm transporte, sem exigir chave YouTube.
- URL Instagram pode conter perfil antes de `/reel/`.
- Preview remoto mantém origem, autor, aprovação pendente e intervalo original.
- Cache é reutilizado por hash e o fetch desconta `local_start_s`, preservando o trecho visto.
- A CLI encontra yt-dlp nos layouts `.venv/bin` e `.venv/Scripts`; a trava serial usa backend nativo de macOS/Linux e Windows.
- Utilitários opcionais YouTube encontram yt-dlp local, habilitam Node e usam arquivo temporário portátil em macOS/Linux; o coletor não reutiliza parcial de download fracassado.
- O coletor Instagram rejeita DNS privado/misto e IPv6 local, fixa o curl em IP público validado, recusa redirects, confina saídas batch e nunca sobrescreve um MP4 existente.
- Node antigo é rejeitado antes de instalar dependências.
- O instalador PowerShell valida o código de saída de venv, pip, imports, npm, Playwright e `doctor`; a CI executa instalação real em macOS e Windows/Python 3.13.
- Leitura, escrita, subprocessos e saída da CLI declaram UTF-8 explicitamente, sem depender da página de código padrão do Windows.
- CLI, coletor Instagram e utilitários YouTube vivem sob a única raiz `scripts/getbrolls/`.
- O Storyboard produzido por `review` incorpora a logo oficial e usa a mesma implementação coberta pela suíte; não há snapshot HTML paralelo preenchido com caso real.
- Arquivos de bibliotecas, fontes externas, mídia e credenciais não integram o repositório.
- Permanecem testes de recuperação do ledger, revisão obsoleta, rejeição, interrupção, proporção, GIF, regras e logs sem segredos.

### Verificação documental

Revisor independente conferiu transportes, runtime, download parcial, instalação, reuso de sessão, estrutura pública, Storyboard e documentação. O guia Instagram diferencia plugin do agente e extensão Playwright, mostra attach/tab-list/tab-select e captura por requests/response-body ou CDP. Não existe promessa de parser automático de captura: o agente opera o navegador autorizado e entrega os pares ao coletor.

### Limites preservados

YouTube, Instagram e TikTok têm agora pelo menos um ensaio real concluído nesta rodada. Isso não garante qualquer URL/sessão: o primeiro link TikTok estava indisponível no site e o extrator retornou bloqueio em tentativas anteriores. Reteste com URL disponível pelo navegador e com as dependências instaladas pelo [guia](GUIDE.md#instalação). Não confundir esses erros com ausência de implementação.

macOS e Windows são as plataformas principais. O Windows tem instalador e launcher Playwright em PowerShell, layout de venv próprio e trava nativa; os helpers Bash de YouTube são opcionais e ficam no caminho macOS/Linux. A [matriz remota da entrega](https://github.com/engenheirodevideo/get-brolls/actions/runs/35040887806) passou em macOS 3.11/3.13, Windows 3.11/3.13 e Ubuntu 3.13; nas combinações principais 3.13, executou também a instalação completa. A descoberta automática em novas sessões Codex/Claude continua dependente da instalação/configuração do agente destinatário. O Storyboard é gerado pelo próprio CLI, incorpora apenas a logo oficial e usa fontes do sistema. Aprovação editorial continua humana.

Comandos para repetir na pasta da skill:

```sh
bash scripts/install.sh --check
bash scripts/install.sh
python3 -m unittest discover -s tests -v
python3 scripts/gb.py doctor
```

No Windows PowerShell, use `scripts/install.ps1 -Check`, `scripts/install.ps1` e `python` nos comandos da suíte/CLI.

`doctor --live` executa buscas limitadas reais e pode consumir quota dos bancos configurados; não testa toda a captura Instagram/TikTok. Os detalhes de origem aparecem na seção [Rotas e evidências](#rotas-e-evidências--15092026).

### Revisão documental

README, GUIDE, SKILL, AGENTS, CHANGELOG, CONTRIBUTING, SECURITY e avisos de terceiros foram conferidos contra a árvore atual. `agents/openai.yaml` mantém a invocação `$get-brolls`; caminhos documentados apontam para o núcleo `scripts/getbrolls/`. A validação documental não substitui os ensaios ao vivo registrados abaixo.

## Rotas e evidências — 15/09/2026

YouTube usa yt-dlp, sem YouTube Data API e sem API key. Pexels/Pixabay usam suas próprias APIs; Instagram usa o navegador e o coletor de dois canais; TikTok usa URL completa e yt-dlp.

| Fonte | Ensaio real | Resultado |
|---|---|---|
| YouTube | Busca NASA Artemis sem variável de chave; preview 0–3 s e repetição 3–5 s com yt-dlp instalado em pasta limpa | Busca/download/decodificação/GIF passaram, 1920×1080, 25fps; pendente de revisão |
| Instagram | Reel DcMXl1IPNtB; Chrome → dois streams → curl → coletor/FFmpeg | Download e merge completos: 50,226009 s, 1076×1912, H.264/AAC, 16.528.141 bytes; decodificação integral e GIF 5 s passaram |
| TikTok | @nasa/7665358680627399966 descoberto no navegador, via yt-dlp instalado pelo GUIDE | Trecho 0–3 s: 576×1024/30fps, H.264/AAC, 269.139 bytes; decodificação e GIF passaram |
| Pexels | coffee, ID 31264406; busca/refresh/download | 1080×1920, 12 s, 1.682.522 bytes |
| Pixabay | coffee, ID 46989; busca/refresh/download | 1920×1080, 35 s, 12.258.063 bytes |
| Commons | earth, 1 resultado; refresh e HEAD | 200 video/webm; corpo não baixado nesse ensaio |
| NASA API | busca, refresh e HEAD | 200 video/mp4; corpo não baixado nesse ensaio |

### Procedência

- [YouTube — lançamento Artemis](https://www.youtube.com/watch?v=B_7EUmCxcvE): metadados públicos e trecho técnico; não houve aprovação editorial do agente.
- [Instagram — NASA Johnson](https://www.instagram.com/nasajohnson/reel/DcMXl1IPNtB/): Captura e download dos dois canais concluídos no Chrome logado, com o coletor desta pasta.
- [TikTok — NASA, Earthset](https://www.tiktok.com/@nasa/video/7665358680627399966): busca no navegador e aquisição do trecho passaram. A URL anterior @nasa_space9/7512513421288492334 aparece indisponível no próprio navegador; seu erro não certificava o estado de toda a plataforma.
- [Pexels — espresso, İsa Kılavuzoğlu](https://www.pexels.com/video/close-up-of-espresso-brewing-into-elegant-cup-31264406/).
- [Pixabay — 46989, NickyPe](https://pixabay.com/videos/id-46989/).

Pexels/Pixabay geraram GIFs de 5 s (1.030.433 / 720.171 bytes) e cortes técnicos de 2 s verificados com ffprobe e FFmpeg. As chaves fornecidas foram usadas só no processo; não integram código, documentação, configs distribuídos ou exemplos. Esses ensaios validam os adapters; não representam aprovação/licença inventada nem garantia de disponibilidade futura.

O coletor Instagram passou também ensaio de captura/CDN real nesta nova tentativa. Fixtures continuam cobrindo detecção de áudio duplicado e falha de transferência. Na captura real, o asset_id e a duração relacionaram os dois canais; seletores de faixa bytestart/byteend foram removidos sem alterar a assinatura. Configs privados não integram a skill.

### Dependências oficiais

[yt-dlp/EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS) exige runtime suportado; o setup completo usa Node 22+ e instala yt-dlp com extras `default`. [Playwright CLI](https://github.com/microsoft/playwright-cli) é instalada via npm, separadamente do código da skill. [Pexels](https://www.pexels.com/api/documentation/) e [Pixabay](https://pixabay.com/api/docs/) documentam suas APIs; documentação não substitui teste autenticado.

A correção NASA promove HTTP somente no host exato images-assets.nasa.gov antes da validação. `doctor --live` busca nas fontes disponíveis e faz refresh de bancos; sem --live, inspeciona o ambiente local.
