---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, guide, installation, providers, storyboard]
---

# Guia completo — Get B-rolls

Este é o manual operacional único da entrega: instalação, compatibilidade, fluxo editorial, provedores, navegador, Instagram, tipos de asset e Storyboard. O conteúdo foi consolidado sem remover os procedimentos do produto de origem; nomes internos foram neutralizados para que a entrega seja autônoma.

## Navegação rápida

- [Instalação](#instalação)
- [Compatibilidade](#compatibilidade)
- [Fluxo editorial](#fluxo-editorial-de-origem)
- [Fontes e transportes](#fontes-e-transportes)
- [Tipos de assets](#tipos-de-assets-e-formatos)
- [Captura pelo navegador](#captura-de-notícias-e-páginas-pelo-navegador)
- [Instagram](#instagram--navegadorplaywright-dois-streams-e-mp4)
- [Storyboard](#storyboard)

## Instalação

### Dependências por capacidade

| Componente | Necessário para |
|---|---|
| Python 3.11+ | CLI e coletor de pares Instagram |
| FFmpeg e ffprobe, executáveis | Prévia, cortes, vídeo+áudio e validação |
| yt-dlp com extras `default` (inclui EJS) | Busca YouTube e aquisição de URLs sociais |
| Node 22+ | Playwright CLI e EJS do yt-dlp; Deno 2.3+ é alternativa apenas ao runtime EJS |
| Node/npm/npx + Playwright CLI + navegador | Captura de streams Instagram e inspeção pelo navegador |
| curl | Baixar os dois streams Instagram capturados |
| Bash e awk | Helpers originais `scripts/broll/gb_*.sh` |

Git é opcional. API key YouTube não é necessária. Pexels/Pixabay usam apenas suas próprias chaves opcionais. `curl-cffi` é extra opcional do yt-dlp, não requisito universal.

### macOS / Linux

Instale Python, FFmpeg, curl e Node pelo gerenciador de pacotes do sistema. Em macOS com Homebrew:

```sh
brew install python ffmpeg node
```

Em Ubuntu/Debian, instale a base com `sudo apt-get install python3 python3-venv ffmpeg curl`; instale também Node 22+ pela distribuição oficial. Confirme versões; não presuma que o Node do repositório do sistema é recente.

Dentro da pasta da skill `get-brolls/`:

```sh
bash scripts/install.sh --check
bash scripts/install.sh
```

O instalador cria `.venv` e instala `yt-dlp[default]`/EJS do PyPI via `requirements.txt`; instala também `@playwright/cli@0.1.20` do npm em `.tools`. Valida Python 3.11+, Node 22+, npm/npx e os executáveis base. Essas pastas de dependências ficam somente na máquina de quem instala e não fazem parte da distribuição da skill. Não instala executáveis do sistema nem altera a instalação do agente. A CLI procura primeiro `.venv/bin/yt-dlp`, depois o PATH. Os helpers também localizam a venv; ativá-la é opcional:

```sh
source .venv/bin/activate
python scripts/gb.py doctor
```

`doctor` lista executáveis e transporte; não certifica login, acesso a cada site ou extração ao vivo. `doctor --live` faz buscas remotas explicitamente e pode consumir quota dos bancos configurados.

### Navegador / Instagram

Use primeiro o navegador autorizado já conectado ao agente. Se o usuário indicou Chrome logado, selecione esse Chrome no plugin e abra o Reel ali; não troque silenciosamente para navegador integrado sem login.

Alternativa com a extensão oficial Playwright já instalada no Chrome:

```sh
bash scripts/playwright.sh -s=getbrolls-instagram attach --extension=chrome
bash scripts/playwright.sh -s=getbrolls-instagram tab-list
bash scripts/playwright.sh -s=getbrolls-instagram tab-select INDICE_OBSERVADO
```

A extensão Playwright e o plugin de navegador do agente são integrações diferentes. Use a que estiver disponível; não tente conectar a extensão de uma ferramenta com a CLI da outra. A instalação da CLI não instala extensões nem importa cookies. Consulte a [documentação oficial da extensão](https://github.com/microsoft/playwright/blob/main/packages/extension/README.md) quando precisar configurar essa alternativa.

Sem navegador existente, crie sessão própria:

```sh
bash scripts/playwright.sh -s=getbrolls-instagram open https://www.instagram.com/ --headed
```

Se faltar o navegador, execute `bash scripts/playwright.sh install-browser chrome`, conforme `--help` da CLI. Login ocorre nessa sessão e depende da conta do destinatário. Nunca distribua perfil/sessão de Bruno.

O método completo está na seção [Instagram pelo navegador](#instagram--navegadorplaywright-dois-streams-e-mp4): navegador → streams vídeo/áudio → configs privados → curl → FFmpeg → ffprobe. O script de pares não substitui a etapa de captura operada pelo agente.

Referências de instalação: [yt-dlp/EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS), [Playwright CLI](https://github.com/microsoft/playwright-cli). Não há bibliotecas dessas ferramentas distribuídas junto da skill; o instalador obtém as distribuições oficiais.

### Configuração

`cp .env.example .env` é opcional. O `.env` pertence à raiz da skill, independentemente da pasta atual. Ambiente do processo prevalece. `--env-file /caminho/.env` vem antes do subcomando. Nunca distribua `.env`, cookies, configs CDN ou perfis do navegador.

### Codex e Claude Code

Copie os arquivos da skill, incluindo `AGENTS.md`, `scripts/broll/` e `scripts/instagram/`, para **um** dos destinos abaixo. Escolha instalação pessoal ou por projeto para evitar duplicatas com o mesmo nome. Ao partir de uma pasta de desenvolvimento, exclua `dist/`, `.venv/`, `.tools/`, `__pycache__/`, projetos e arquivos privados. Execute o instalador no destino final; não mova uma venv entre pastas:

| Agente | Pessoal | Projeto | Invocação |
|---|---|---|---|
| Codex | `~/.agents/skills/get-brolls/` | `.agents/skills/get-brolls/` | `$get-brolls` |
| Claude Code | `~/.claude/skills/get-brolls/` | `.claude/skills/get-brolls/` | `/get-brolls` |

Preserve cópias anteriores antes de substituir. Abra nova sessão para verificar descoberta. Use caminhos absolutos quando executar de outra pasta:

```sh
python3 "$GB_SKILL_DIR/scripts/gb.py" doctor
python3 "$GB_SKILL_DIR/scripts/gb.py" search --provider youtube --query "NASA Artemis" --limit 3 --project "$GB_PROJECT"
```

Defina GB_SKILL_DIR e GB_PROJECT com os caminhos reais. O pacote completo usa `fcntl` para trava e Bash nos helpers: macOS/Linux. Windows nativo não foi validado; WSL precisa dos executáveis instalados dentro do Linux e deve ser testado nesse ambiente.

### Verificação e atualização

Após instalar, confirme `yt-dlp` e `playwright-cli` em `doctor`. Para a CLI Playwright local, execute `bash scripts/playwright.sh --version`. Um status positivo indica disponibilidade, não que todas as URLs serão acessíveis.

O conjunto ensaiado nesta versão foi yt-dlp 2026.08.19, EJS 0.8.0 e Playwright CLI 0.1.20. `requirements.txt` aceita yt-dlp a partir de 2026.8.19 e pode resolver uma versão mais nova; o npm instala a versão de Playwright indicada no instalador. Preserve configurações privadas antes de atualizar a skill e repita um ensaio da rota utilizada se as dependências mudarem.

Se `--check` falhar, instale o executável/versão apontado. Se a extração falhar, confirme primeiro que a URL abre no navegador autorizado; confira instalação, sessão e disponibilidade do post. Não peça chave YouTube. Para URL CDN Instagram expirada, recapture os dois canais e siga o guia de recuperação. Os testes reais documentados estão em [Qualidade e evidências](QUALITY.md).

## Compatibilidade

- Python 3.11+, FFmpeg/ffprobe; yt-dlp[default]/EJS e runtime JS para fontes sociais. Navegador/Playwright e curl no processo Instagram.
- O Storyboard usa navegador moderno com JavaScript, Blob e localStorage. Se armazenamento local falhar, exporte o JSON antes de fechar.
- Caminhos de prévias são relativos: compartilhe `brolls/` completo.
- CLI preserva schema do manifest v1 e adiciona `project_id`, metadados de prévia e revisão. Exportação de revisão usa `templateVersion: 2`.
- Artefatos do protótipo anterior não têm assinatura de fonte/intervalo; não podem ser importados. Regenere o storyboard com `review`.
- `--shot` permite múltiplos inserts da mesma fonte; sem ele, a resolução deduplica por fonte.
- Helpers originais e coletor Instagram incluídos na 2.3.4. Review usa GIF/imagens, sem player remoto incorporado.
- Projeto local confiável, uso serial. Comandos simultâneos no mesmo projeto são recusados. Não há colaboração multiusuário; evidências remotas por fonte estão em QUALITY.

Versão 2.3: asset_type/image, RULES e biblioteca de referências por projeto. APIs pesquisam vídeos; imagens e screenshots entram por arquivo local. Regras usam JSON embutido em Markdown; nenhum parser YAML externo é necessário.

2.3.1 centraliza a assinatura incluindo o escopo da prévia e arquivos de contexto. Regenere o storyboard de versões anteriores e solicite nova revisão; JSON antigo pode ser recusado como desatualizado. Suporte da trava local: macOS/Linux (`fcntl`).

### Agentes — revisão 2.3.2

Entrada no padrão Agent Skills (`name`, `description`, `license`, `metadata`). Metadados operacionais do vault ficam em `metadata`, sem campos próprios no nível superior do SKILL.md. Os demais documentos mantêm seu frontmatter operacional.

Codex e Claude Code usam a mesma pasta, com destinos e invocações descritos em GUIDE.md. `agents/openai.yaml` é opcional e específico do Codex. Não há dependência de hooks, MCP, permissões preaprovadas ou sintaxe de interpolação exclusiva do Claude. Validação estrutural não equivale a teste de descoberta em uma sessão nativa de cada produto.

### Migração para 2.3.4

A prévia remota agora pode adquirir um trecho e guardar `local_start_s` junto ao hash da fonte. A assinatura inclui esse offset. Preserve o projeto e as decisões antigas como histórico, gere nova prévia/review e solicite nova decisão quando a revisão anterior for recusada por assinatura desatualizada. Não edite hashes/assinaturas para forçar uma aprovação antiga.

Projetos que já possuíam arquivo local continuam usando esse arquivo. Preserve os caminhos dos originais e `.getbrolls-sources/` para regenerar prévias; compartilhar só `brolls/` permite visualizar o storyboard, não continuar toda a edição em outro computador.

A suíte atual passou em macOS/Python 3.14.6. A matriz CI de Python 3.11/3.12/3.13 no Ubuntu está configurada, mas sua execução remota não foi verificada nesta rodada. Windows nativo não é suportado pelo uso de `fcntl`; WSL é um ambiente Linux separado e ainda precisa de validação própria. As versões de dependências ensaiadas estão na seção [Instalação](#instalação).

## Fluxo editorial de origem

> Referência de origem do processo original. Para execução nesta versão, siga SKILL.md e os guias de rotas atuais; nomes específicos de ferramentas e limitações históricas abaixo não substituem a capacidade da sessão atual.


Coleta B-roll dirigida pelo contrato de edição. Confirma no navegador antes de baixar; nunca autora vídeo.

### Fluxo
> **Meta: 8+ clipes literais por roteiro** (padrão do Bruno). Se os beats óbvios não fecham 8, amplie: mais empresas/pessoas citadas, cobertura de telejornal do mesmo fato, produto nomeado (ex.: Agentforce), pregão/mercado real, segmentos extras da mesma fonte forte. Todos 1080p (cheque com ffprobe; troque fontes 360/720 quando houver 1080).
1. **plan** — dos blocos do contrato (`clips[].bloco_roteiro` / `fala`), derive N beats visuais (query em inglês). Prefira **entidade literal nomeada** (pessoa/produto/logo do que a fala cita: Sam Altman, OpenAI, SoftBank, Codex…) — é onde o YouTube dá material real e limpo. Beats abstratos (back-office, "dev", "automação") caem em tutorial/vlog/stock/desenho; use no máximo um demo de produto real (ex.: dashboard ERP) ou deixe no rosto do talento.
2. **search** — YouTube sem baixar: `gb_search.sh "<query>" [n]` → `ID | DURATION | TITLE`.
3. **confirm** — preview por **contact sheet** (NÃO 1 frame solto): `gb_contact.sh <id> <START-END> <out.jpg> [interval_seg=1.5] [cols=4]` amostra N frames igualmente espaçados no segmento e tila num grid numerado → dá pra ler o MOVIMENTO/sequência e pegar legenda queimada/overlay antes de baixar. Default `1.5s` (visão ampla); `0.5s` quando precisar densidade. `gb_frame.sh` (1 still) vira fallback. **Gate: Bruno aprova antes de baixar.**
4. **download** — segmento trimado 1080p: `gb_fetch.sh <id> <START-END> <NN_entity_context> <PROJETO>/brolls`.
5. **verify** — `gb_verify.sh <PROJETO>/brolls` (tabela ffprobe + tamanho).
6. **(opcional) vertical** — `gb_vertical.sh <in>` pra 9:16 com fundo borrado.

### Imagens de notícia (manchete/dado)
- **Print de site não vira arquivo utilizável.** O `save_to_disk` do `claude-in-chrome` guarda no sandbox da extensão (só devolve `ss_<id>`, sem path no filesystem); `screencapture -R` da janela falhou ("could not create image from rect" — falta permissão de Screen Recording pro processo). Não prometa baixar manchete estática.
- **Prefira notícia em VÍDEO**: cobertura real (Reuters/CNBC/Bloomberg) no YouTube pelo mesmo fluxo (vira mp4 no projeto).
- **Estático** = entregar **lista de links + o que grifar** num `BROLL-MAP.md`, pro humano printar. Alguns sites (PYMNTS) caem em Cloudflare; Benzinga abre normal.

### Engine / scripts
Os helpers `gb_*.sh` agora acompanham a própria skill, sem depender do componente externo `get-broll`:
`${GB_SKILL_DIR}/scripts/broll/gb_search.sh`, `gb_contact.sh` (contact sheet — preview padrão),
`gb_frame.sh` (still fallback), `gb_fetch.sh`, `gb_verify.sh`, `gb_vertical.sh`.
Deps: `yt-dlp` + `ffmpeg` + `screencapture`(macOS quando houver preview/browser).

### Saída
`<PROJETO>/brolls/NN_entity_context.mp4`. Registrar no ledger (`step: get-brolls`, outputs = arquivos baixados).

## Fontes e transportes

| Fonte | Descoberta | Aquisição |
|---|---|---|
| YouTube | ytsearch sem chave | yt-dlp, intervalo via FFmpeg |
| Instagram | navegador/URL | navegador captura vídeo+áudio; coletor de pares incluído; yt-dlp como outra rota |
| TikTok | navegador/URL completa | yt-dlp |
| Pexels | API, PEXELS_API_KEY | HTTPS e cache de original para prévia |
| Pixabay | API, PIXABAY_API_KEY; cache 24 h | HTTPS e cache de original para prévia |
| Commons / NASA | APIs sem chave | HTTPS |
| Local | resolve --file | arquivo local |

Fluxo único: descobrir → obter mídia de trabalho/mostrar sequência → revisão humana → corte final → verify. Prévia não equivale a aprovação. `--reference-only` é opção explícita para não adquirir mídia. Consulte o guia da fonte; Instagram começa na seção [Instagram pelo navegador](#instagram--navegadorplaywright-dois-streams-e-mp4).

`providers` declara transporte/capacidades implementadas e configuração, não garantia de acesso universal. `auto` segue a ordem de fontes das regras e o intent. Prefira entidades literais quando o roteiro citar pessoa/produto/fato.

## Provedor — YouTube

Motor original: yt-dlp + FFmpeg, **sem API key**. `search --provider youtube` usa ytsearch. `resolve --url` aceita URL de vídeo/shorts; `preview` obtém o intervalo e gera GIF/contact sheet, mantendo aprovação pendente. `fetch` publica os bytes revisados após decisão humana e registro de condições do projeto.

Helpers originais em `scripts/broll/`: gb_search.sh, gb_contact.sh, gb_frame.sh, gb_fetch.sh, gb_verify.sh e gb_vertical.sh. Interface antiga usa VIDEO_ID; o CLI novo aceita URL. Configure EJS/runtime conforme GUIDE. Se o site exigir sessão ou negar mídia, reporte o erro real; não troque silenciosamente para API com chave.

## Provedor — Instagram

Rota principal: **navegador/Playwright → URL CDN de vídeo + URL CDN de áudio → curl → FFmpeg → ffprobe**. Leia a seção [Instagram pelo navegador](#instagram--navegadorplaywright-dois-streams-e-mp4) e use o coletor incluído em `scripts/instagram/ig_curl_pair_downloader.py`.

O MP4 unido entra com `resolve --file --source-url --creator --shot`; depois preview/review/fetch. O browser captura os streams; o script baixa/junta. Não exige chave da API oficial Instagram. A página pode exigir sessão. yt-dlp também está disponível via URL completa, se funcionar para aquele post; falha dessa rota não remove o fluxo de navegador.

## Provedor — TikTok

Recebe URL completa `https://www.tiktok.com/@usuario/video/ID` e usa o extrator TikTok do yt-dlp para obter o intervalo. `resolve --url`, `preview`, revisão e `fetch` seguem o mesmo fluxo. Sem API key da plataforma.

Descubra a URL pelo navegador; não há busca global TikTok por palavra-chave implementada. Links encurtados precisam ser abertos no navegador para obter URL canônica. A existência do extrator não garante acesso a todo vídeo; teste a URL real e registre eventual exigência de sessão/indisponibilidade. Consulte [Qualidade e evidências](QUALITY.md) para a evidência desta versão.

## Provedor — Pexels

PEXELS_API_KEY no ambiente. API de vídeos, poster e variante MP4. Reconsulta ID no fetch. Verifique licença e requisitos da API. https://www.pexels.com/api/documentation/

Diagnóstico: `python3 scripts/gb.py providers`. Falha de credencial não ativa scraping ou outra conta.

## Provedor — Pixabay

PIXABAY_API_KEY no ambiente. Busca de vídeos com cache 24 horas. Reconsulta ID no fetch. Verifique licença e autoria. https://pixabay.com/api/docs/

Diagnóstico: `python3 scripts/gb.py providers`. Falha de credencial não ativa scraping ou outra conta.

## Provedor — Wikimedia Commons

Action API pública filtra vídeos, preserva autor e licença por arquivo. Licença desconhecida nunca vira domínio público. https://commons.wikimedia.org/wiki/Commons:API/MediaWiki

Diagnóstico: `python3 scripts/gb.py providers`. Falha de credencial não ativa scraping ou outra conta.

## Provedor — NASA

Images API pública consulta vídeos e assets MP4. Autoria de terceiros e condições precisam de verificação antes de permit. https://images.nasa.gov/docs/images.nasa.gov_api_docs.pdf

Diagnóstico: `python3 scripts/gb.py providers`. Falha de credencial não ativa scraping ou outra conta.

## Provedor — arquivo local

Importe com resolve --file. Sem upload. Verifique direitos e aprove intervalo antes do corte. Hash detecta alteração da fonte.

Diagnóstico: `python3 scripts/gb.py providers`. Falha de credencial não ativa scraping ou outra conta.

## Bancos — busca, prévia e coleta

Pexels/Pixabay usam suas próprias chaves no ambiente ou `.env` privado da skill. YouTube não depende delas. Execute `search --provider pexels|pixabay --query coffee --limit 2 --intent illustrative --project /projeto`.

`preview --candidate ID --start 0 --end 5 --project /projeto` atualiza a URL de mídia, obtém o original em `.getbrolls-sources/` e gera GIF/contact sheet. Preserva o ID remoto, fonte e autoria; não precisa aprovar um poster antes de ver o movimento. Aprovação fica pendente. Depois de review/decisão/condições, fetch usa a fonte revisada.

Também pode obter o original pela página oficial e usar resolve --file --source-url --creator. Nesse caso o ID local é novo. API indisponível não autoriza inventar candidato ou afirmar teste bem-sucedido.

## Tipos de assets e formatos

| Tipo | Origem implementada | Prévia | Entrega final |
|---|---|---|---|
| `video` | Arquivo local; busca de vídeos nos provedores configurados | GIF ou poster/sheet; remoto pode ser só referência | MP4 do intervalo aprovado |
| `image` | PNG/JPG/JPEG/WebP/BMP/TIFF local | Imagem estática | Original estático copiado após aprovação |
| `news_screenshot` | Captura Playwright importada localmente com URL | Imagem estática | PNG/JPG original com procedência no ledger |
| `web_screenshot` | Captura de página importada localmente com URL | Imagem estática | Original estático com procedência |

Busca de imagens via API, áudio isolado como asset final e SVG não estão implementados. Download social usa os transportes do ROUTER. Não anuncie a capacidade só porque existe um nome de tipo. GIF animado é a prévia de um vídeo; não substitui o arquivo final de edição.

### Formato editorial

`RULES.md` aceita `native`, `reels` (9:16) ou `horizontal` (16:9). O ledger registra dimensões nativas, destino e `fit`: matches, needs_layout_review ou unknown. Um vídeo horizontal pode ser referência para Reels, mas precisa de decisão de layout. A ferramenta não recorta rostos ou textos, não amplia baixa resolução nem converte todos os assets para quadrados.

Mudar formato nas regras atualiza o relatório e invalida aprovação anterior. Arquivos de cortes anteriores são preservados; nova revisão usa outra revisão do insert.

No storyboard, imagem/GIF mantém proporção. Captura móvel padrão é 390×844, não 9:16 exato; a composição do vídeo é uma decisão posterior. Fonte, fala, motivo, autor e data de captura acompanham o asset.

### Organização do projeto

```text
video-01/
├── RULES.md
├── output/playwright/       # screenshots e snapshots de trabalho
└── brolls/
    ├── manifest.json        # tipo, formato, contexto e procedência
    ├── references.json      # referências explícitas e seus motivos
    ├── events.jsonl
    ├── candidates/
    ├── previews/            # poster, contact sheet, GIF
    ├── clips/               # vídeo ou imagem final
    ├── credits.md
    └── review.html
```

A memória é por projeto. Para consultar referências de outro projeto, use `references --project /caminho/anterior` com autorização do usuário. Os exemplos orientam a próxima busca; nunca transferem aprovação/licença automaticamente.

## Captura de notícias e páginas pelo navegador

Workflow opcional do agente com [Playwright CLI oficial](https://github.com/microsoft/playwright-cli). Requer Node.js/npm/npx e navegador disponível. A skill não inclui navegador nem cookies. O plano usa listas de argumentos, não eval de conteúdo do usuário.

### Preparar

```sh
python3 scripts/gb.py rules --project ./video-01
python3 scripts/gb.py references --project ./video-01
python3 scripts/gb.py browser-plan --url https://www.nasa.gov/news/recently-published/ --project ./video-01
```

Leia primeiro as regras editoriais e referências aprovadas/rejeitadas. Priorize `preferred_domains` nas pesquisas do navegador e não use `blocked_domains`. Exemplos de queries: assunto + entidade + site preferido. Confira a URL real encontrada; não invente resultados.

Defina `GB_SKILL_DIR` com a pasta instalada. Os exemplos abaixo usam a CLI local preparada conforme a seção [Instalação](#instalação). `browser-plan` também pode emitir a forma equivalente via `npx`, que obtém a CLI do npm quando necessário.

O plano devolve comandos `open`, `resize`, `snapshot` e `screenshot`, caminho único e tipo habilitado para a captura. Execute em ordem e inspecione o snapshot antes de interações. Exemplo operacional:

```sh
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls open https://www.nasa.gov/news/recently-published/ --headed
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls resize 390 844
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls snapshot
# Depois de selecionar a notícia por uma referência do snapshot atual:
# ... click REF_REAL
# ... snapshot
# Use o caminho de captura fornecido por browser-plan:
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls screenshot --filename=./video-01/output/playwright/news.png
```

`resize` muda a área visível para layout móvel. Não é emulação completa de dispositivo/touch/user-agent. Para horizontal, configure viewport desktop no RULES. Full-page é configurável, mas prints longos não cabem automaticamente num insert 9:16: selecione trecho legível, mantendo a origem. Não distorça a página para preencher o frame.

### Inspeção e importação

- Confira manchete, autor, data da notícia, URL final e carregamento de imagens. Registre data de captura separada da publicação.
- Se conteúdo estiver atrás de login/paywall, registre indisponibilidade; não tente contornar. O agente só usa acessos autorizados pelo usuário.
- Tire novo snapshot após navegar ou alterar significativamente a página. Nunca reutilize referências obsoletas.
- Confira o screenshot visualmente antes de importar. Não transforme banner de erro/cookies em asset aprovado.

```sh
python3 scripts/gb.py resolve --file ./video-01/output/playwright/news.png --asset-type news_screenshot --source-url URL_REAL --title "Manchete real" --creator "Autor informado" --captured-at "2026-09-15T12:00:00-03:00" --shot news-01 --project ./video-01
python3 scripts/gb.py preview --candidate ID --narration "Fala do roteiro" --reason "Notícia comprova o evento citado" --project ./video-01
python3 scripts/gb.py review --project ./video-01
```

Substitua os metadados de exemplo pelos dados reais. Imagem estática não exige `--start/--end`. Se apenas `web_screenshot` estiver habilitado, use esse tipo. Revisão, decisão de direitos, fetch e referência seguem o fluxo normal.

Não execute `close-all` nem feche abas de outros projetos. Encerre somente a sessão criada para a captura quando terminar. Se o ambiente do agente exigir outro transporte de navegador, mantenha a mesma sequência e metadados usando suas ferramentas autorizadas.

## Instagram — navegador/Playwright, dois streams e MP4

Processo portado de fluxo Instagram de origem. O motor de download/merge é uma cópia do script existente, em `scripts/instagram/ig_curl_pair_downloader.py`. A captura acontece na sessão do navegador operada pelo agente; o script Python consome os pares capturados. [Instruções originais portadas](#instagram--recuperacao-e-auditoria).

### 1. Abrir o Reel e capturar as fontes

Use a URL real do Reel na sessão autorizada. **Se há Chrome logado indicado pelo usuário, reutilize esse Chrome pelo plugin do agente.** Não abra outra sessão sem necessidade. Com a extensão oficial Playwright disponível, a alternativa executável é:

```sh
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram attach --extension=chrome
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram tab-list
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram tab-select INDICE_OBSERVADO
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram goto "$REEL_URL"
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram snapshot
```

`INDICE_OBSERVADO` vem de `tab-list`. A extensão Playwright não é a extensão do plugin Codex; quando só esta estiver conectada, controle o Chrome por ela. Sem sessão existente, `open "$REEL_URL" --headed` cria uma sessão própria. Login necessário é realizado pelo humano nessa sessão.

#### Captura pela rede da página

1. Confira no snapshot a URL/código do Reel, perfil e legenda. Inspecione apenas esse post.
2. Registre as respostas antes de reproduzir/recarregar o Reel. No plugin com CDP: obtenha a capacidade `cdp`, leia sua documentação, envie `Network.enable`, guarde o cursor de `readEvents` e observe `Network.responseReceived` após a reprodução. No Playwright CLI instalado, os comandos são `requests` e `response-body` (não `network`).
3. Examine as respostas da página e do manifesto DASH que contêm o **mesmo código/ID do Reel**. Use a resposta/documento observado; não invente endpoints. Se o manifesto estiver em JSON, decodifique o campo de manifesto e depois seu XML. Identifique `AdaptationSet` de vídeo/áudio por `mimeType`/`contentType`, selecione suas `Representation` e `BaseURL`, preservando os parâmetros assinados. Prefira vídeo até 1080p quando disponível.
4. Se só houver requests de segmentos, relacione as representações ao mesmo Reel. No ensaio real, o parâmetro `efg` em base64 JSON identificou o mesmo `xpv_asset_id`, duração e `vencode_tag` de vídeo/áudio; isso distinguiu o Reel ativo de recomendações pré-carregadas. Compare também duração/dimensões do player e inspecione o conteúdo baixado.
5. Os URLs observados nesse formato tinham `bytestart`/`byteend`, seletores explícitos de faixa. Para obter o arquivo completo, remova **somente esses dois parâmetros de faixa**, preservando todos os demais parâmetros e assinatura exatamente como capturados. Não remova `oh`, `oe` ou parâmetros desconhecidos. Valide duração e decodificação completa; um fragmento/HTTP 206 não comprova download integral. Se a CDN rejeitar a URL completa, recapture a representação pelo manifesto; não altere assinatura nem credenciais.
6. Grave somente as duas URLs selecionadas em configs privados. Nunca exporte cookies, headers de autenticação ou todo o perfil para o pacote.

Exemplo de inspeção CLI, com saída sensível retida no projeto:

```sh
umask 077
mkdir -p "$GB_PROJECT/work/instagram-configs"
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram requests > "$GB_PROJECT/work/instagram-configs/requests.private.txt"
# O agente inspeciona o arquivo local e escolhe o índice da resposta do Reel.
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram --raw response-body INDICE_OBSERVADO > "$GB_PROJECT/work/instagram-configs/reel-response.private.txt"
```

`response-body` salva corpos binários em arquivo e informa o caminho. Em resposta textual, o agente analisa JSON/XML observado e escreve os configs a seguir; não há parser de captura automática embutido. Se a ferramenta não oferece respostas/manifesto, informe essa limitação e use a integração autorizada que ofereça, sem substituir a origem por stock.

Escolha as duas representações **do mesmo Reel** pelo manifesto/identificador e conteúdo, não simplesmente os dois primeiros MP4 da página. Recomendações e pré-carregamento podem pertencer a outros vídeos. URL `blob:` é referência interna do player e não serve ao curl; use a URL HTTPS real de CDN que a página requisitou. Preserve query assinada necessária à requisição.

Salve os configs em `<projeto>/work/instagram-configs`, com permissão privada; capture URL de vídeo e URL de áudio separadamente. Este exemplo descreve o formato, não contém URLs utilizáveis:

```text
# 01_REEL_video.conf
url = "URL_HTTPS_REAL_DO_STREAM_DE_VIDEO"
# 01_REEL_audio.conf
url = "URL_HTTPS_REAL_DO_STREAM_DE_AUDIO"
```

São **dois arquivos**, com o mesmo prefixo e sufixos `_video.conf` / `_audio.conf`. O coletor lê `url` e opcionalmente `output`; não repassa headers arbitrários do config ao curl. Se a fonte exigir headers/cookies além da URL, não invente suporte: registre a necessidade e use a ferramenta de navegador autorizada para obter as partes, registrando `output` no config para reaproveitá-las. Não exponha cookies/URLs assinadas nos relatórios, HTML ou ZIP.

### 2. Baixar os dois canais, juntar e verificar

```sh
python3 "$GB_SKILL_DIR/scripts/instagram/ig_curl_pair_downloader.py"   --video-config "$GB_PROJECT/work/instagram-configs/01_REEL_video.conf"   --audio-config "$GB_PROJECT/work/instagram-configs/01_REEL_audio.conf"   --output "$GB_PROJECT/sources/instagram/01_REEL.mp4"   --parts-dir "$GB_PROJECT/work/instagram-parts"   --config-output-root "$GB_PROJECT"   --summary-json "$GB_PROJECT/work/instagram-summary.json"
```

O script baixa com curl, mapeia vídeo do primeiro input e áudio do segundo, normaliza H.264/yuv420p + AAC e verifica streams via ffprobe. Se a URL expirou, recapture no navegador. Um erro de acesso não significa que a plataforma é somente referência.

### 3. Batch e áudio duplicado

```sh
python3 "$GB_SKILL_DIR/scripts/instagram/ig_curl_pair_downloader.py"   --config-dir "$GB_PROJECT/work/instagram-configs"   --output-dir "$GB_PROJECT/sources/instagram"   --parts-dir "$GB_PROJECT/work/instagram-parts"   --config-output-root "$GB_PROJECT"   --layout flat --fail-on-duplicate-audio   --summary-json "$GB_PROJECT/work/instagram-summary.json"
```

Em batch, mantenha o gate de hash de áudio. Dois Reels podem ter áudio igual legitimamente; uma colisão exige conferir o par correto, não ignorar a detecção automaticamente. `--force-download` obtém novamente; `--no-prefer-config-output` evita somente o arquivo apontado no config; ainda pode reutilizar `parts-dir`. Para descartar uma parte suspeita, use `--force-download` após recapturar os URLs ou um novo diretório de partes. Preserve arquivos anteriores antes de substituir.

### 4. Entrar no fluxo comum de B-roll

```sh
python3 "$GB_SKILL_DIR/scripts/gb.py" resolve --file "$GB_PROJECT/sources/instagram/01_REEL.mp4" --source-url "$REEL_URL" --creator "$CREATOR" --shot instagram-01 --project "$GB_PROJECT"
python3 "$GB_SKILL_DIR/scripts/gb.py" preview --candidate "$LOCAL_ID" --start 0 --end 5 --reason "Trecho do Reel selecionado para revisão" --project "$GB_PROJECT"
python3 "$GB_SKILL_DIR/scripts/gb.py" review --project "$GB_PROJECT"
```

Use o ID local retornado e um intervalo que caiba no vídeo. Confira visualmente sincronização, identidade e conteúdo; áudio presente não comprova que é o áudio correto. O candidato fica pendente; não se autoaprove. O download das partes para inspecionar a mídia é preparação, distinta do corte final aprovado.

### Teste de instalação

`python3 scripts/instagram/ig_curl_pair_downloader.py --help` precisa funcionar a partir da pasta da skill, sem qualquer instalação do produto de origem. A suíte testa pares locais distintos, junção real e detecção de áudio duplicado. Teste local não prova captura/login/CDN ao vivo; [Qualidade e evidências](QUALITY.md) identifica separadamente essa evidência.

### Evidência ao vivo desta versão

Em 15/09/2026, a sessão Chrome indicada pelo usuário abriu o Reel `DcMXl1IPNtB`. Vídeo e áudio compartilhavam o asset `1474399414721222`; o coletor desta pasta baixou ambos por curl, mesclou e verificou MP4 de 50,226009 s, 1076×1912, H.264/yuv420p e AAC. Decodificação integral FFmpeg passou, quadro visual foi inspecionado e a CLI gerou prévia de 5 s com decisão pendente. URLs assinadas/configs ficaram somente em temporário privado, fora da skill. Essa evidência substitui a pendência anterior causada pela UI de extensão.

## Instagram — recuperação e auditoria

> Referência de origem do processo original. Para execução nesta versão, siga SKILL.md e os guias de rotas atuais; nomes específicos de ferramentas e limitações históricas abaixo não substituem a capacidade da sessão atual.


Baixar e organizar Reels do Instagram para o fluxo Get B-rolls usando o método validado na campanha Codex de 2026-07-08: capturar URLs diretas de CDN como pares `*_video.conf` + `*_audio.conf`, baixar as partes separadas, mesclar com `ffmpeg`, e validar que o MP4 final tem vídeo + áudio corretos.

### Quando usar

Usar quando:

- `yt-dlp` falhar no Instagram com `empty media response` mesmo com cookies.
- Existirem curl configs gerados por navegador/devtools para Reels.
- Houver suspeita de áudio duplicado/trocado em MP4 local.
- For necessário redownload organizado de Reels antes de transcrever, fazer curated/tagged ou aprender formato/motion.

### Fonte preservada

A fonte que originou este método está documentada em:

[Processo de captura e download](#instagram--navegadorplaywright-dois-streams-e-mp4)

Consultar a referência antes de mudar o script. Ela preserva os comandos originais da pasta:

`<projeto>/work`

Não copiar signed CDN URLs para a skill. Elas expiram, podem carregar sessão/assinatura e pertencem ao material de trabalho, não ao runtime da skill.

### Script principal

Usar:

`$GB_SKILL_DIR/scripts/instagram/ig_curl_pair_downloader.py`

Dependências:

- `python3`
- `curl`
- `ffmpeg`
- `ffprobe`

O script nunca imprime a URL assinada; ele só mostra o arquivo `.conf` de origem e o destino.

### Fluxo single reel

Para um par de configs:

```bash
python3 "$GB_SKILL_DIR/scripts/instagram/ig_curl_pair_downloader.py" \
  --video-config /path/to/01_CODE_video.conf \
  --audio-config /path/to/01_CODE_audio.conf \
  --output /path/to/output/01_CODE.mp4 \
  --parts-dir /path/to/work/instagram_parts \
  --config-output-root /path/to/root_that_resolves_conf_output_lines \
  --summary-json /path/to/output/01_CODE.summary.json
```

### Fluxo batch

Para uma pasta de configs:

```bash
python3 "$GB_SKILL_DIR/scripts/instagram/ig_curl_pair_downloader.py" \
  --config-dir /path/to/curl_configs \
  --output-dir /path/to/outputs \
  --parts-dir /path/to/work/instagram_parts \
  --config-output-root /path/to/root_that_resolves_conf_output_lines \
  --layout auto \
  --fail-on-duplicate-audio \
  --summary-json /path/to/outputs/instagram-download-summary.json
```

Layouts:

- `auto`: mantém subpasta de username para stems no formato `<username>_<rank>_<code>` e usa output plano para stems `<rank>_<code>`.
- `student`: força output `<output-dir>/<username>/<rank>_<code>.mp4`.
- `flat`: escreve sempre `<output-dir>/<stem>.mp4`.

### Reuso dos outputs dos configs

O método original gravava `output = "work/..."` dentro de cada `.conf`. Por padrão, o script tenta reaproveitar esse arquivo se ele já existir, resolvendo o caminho via `--config-output-root`. Se não existir, baixa pela URL assinada do `.conf`.

Usar `--force-download` quando for obrigatório redownloadar a partir da URL assinada.

Usar `--no-prefer-config-output` (para invalidar também partes já salvas, use `--force-download` ou nova parts-dir) quando o arquivo apontado por `output =` for suspeito e não deve ser reaproveitado.

### Gate anti-áudio-duplicado

Sempre usar `--fail-on-duplicate-audio` em batch. Esse gate extrai o AAC dos outputs e falha se dois MP4 finais tiverem o mesmo SHA-256 de áudio. Foi exatamente esse o bug encontrado na track `07-15-2026/03-metodo-audience2-review`: quatro MP4s tinham vídeo diferente e o mesmo stream de áudio.

Para auditoria manual:

```bash
for f in /path/to/videos/*.mp4; do
  tmp="$(mktemp -d /tmp/getbrolls-audiohash-XXXXXX)"
  ffmpeg -nostdin -v error -i "$f" -map 0:a:0 -c copy "$tmp/audio.aac"
  shasum -a 256 "$tmp/audio.aac"
  rm -rf "$tmp"
done
```

### Organização recomendada no projeto

Guardar a mídia final em uma pasta de fonte, nunca sobrescrever sem backup:

```text
<projeto>/sources/instagram/<perfil>/<rank>_<code>.mp4
<projeto>/sources/instagram/<perfil>/<rank>_<code>.summary.json
<projeto>/work/instagram_parts/<stem>_video.mp4
<projeto>/work/instagram_parts/<stem>_audio.mp4
```

Quando a mídia substituir uma versão bugada, primeiro criar backup com timestamp/slug dentro da track ou ao lado do arquivo:

```text
<arquivo>.backup-audio-bug-YYYYMMDD-HHMMSS.mp4
```

### Depois do download

Executar nesta ordem:

1. `ffprobe`/summary JSON: confirmar stream de vídeo e áudio.
2. Gate de hash: confirmar que áudios que deveriam ser distintos não duplicaram.
3. Transcrição: `etapa de transcrição configurada no projeto` ou fluxo específico do corpus.
4. Curadoria/tagging: recriar derivados a partir do transcript correto.
5. Registro: salvar summary, comandos e evidências no journal da track.

### Segurança

Não ler nem imprimir cookies, `.env`, browser credential stores ou tokens. Usar signed CDN URLs já capturadas em `.conf` como fonte operacional temporária. A solicitação de coleta autoriza a captura das URLs do post na sessão indicada. Reutilize essa autorização; peça acesso somente se faltar sessão/autorização necessária. A captura é temporária e operada pelo agente, conforme a seção [Instagram pelo navegador](#instagram--navegadorplaywright-dois-streams-e-mp4).

## Storyboard

Entregável de revisão independente da landing page. `gb.py review` gera `brolls/review.html` com CSS e JavaScript próprios incorporados; a tipografia usa fontes do sistema; imagens/GIF ficam em `previews/`.

1. Resolva o original autorizado e use um `--shot` distinto por insert.
2. Execute `preview` com intervalo, `--narration` (fala exata, quando fornecida; omita se ausente) e `--reason` (motivo da fonte).
3. O topo mostra o insert em sua proporção; à direita, fonte e ações de revisão. Galeria sempre estática. O GIF anima só no quadro selecionado; clique para alternar estático/animação. A preferência de movimento reduzido é respeitada.
4. Revisor aprova, pede ajuste ou sugere fonte; ajustes exigem comentário. Exporte JSON para devolver decisões. O botão PDF gera versão estática dos quadros com fontes/comentários.
5. Importe com `import-review --by`. Projeto, IDs e assinatura do intervalo/fonte são validados. Mudança de intervalo invalida decisão anterior.
6. Só colete o corte final depois de aprovação humana e registro da permissão. Clips MP4 ficam separados do storyboard.

Configurações, presets e limitações estão no [README](README.md). `preview` obtém mídia de trabalho remota nas rotas de aquisição implementadas; no Instagram por navegador, importe primeiro o MP4 unido. Um poster isolado, inclusive com `--reference-only`, não comprova movimento.

Configuração padrão: `GB_GIF_SCOPE=broll`. O print opcional da pessoa permanece estático. Para revisar composição pronta do mesmo insert, escolha `full` e forneça `--full-preview-file`. O objetivo continua ser decidir a direção da coleta; nenhuma montagem adicional é exigida.
