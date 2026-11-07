---
type: documentation
status: current
created: 2026-09-23
updated: 2026-11-06
tags: [get-brolls, manual, tutorial, commands]
---

# getbrolls: manual e tutorial

Versão 2.6.1. Feito para **videomaker que está começando a mexer com código**.

Você pede o B-roll, vê a prévia, aprova, e **só o trecho aprovado** vira corte final, com a fonte anotada. O getbrolls não edita nem renderiza o vídeo: ele coleta, mostra e entrega os clipes com origem e condições de uso. Quem monta é o seu editor (ou o HyperFrames).

## Se ler só isso, já dá para usar

- **No Claude Code, é só pedir:** `/get-brolls` + o que você precisa + a pasta do vídeo. O agente roda os comandos por você e mostra a saída real.
- **O corte final não sai sem duas coisas:** você **aprovar** o trecho e **registrar os direitos** de uso. A prévia pode baixar uma mídia de trabalho antes disso (`--reference-only` evita).
- **Se perdeu?** Peça `/get-brolls:get-brolls-status` ou rode o `status`. Ele diz onde você parou e já traz o próximo comando pronto.

## Dicionário rápido

**Do vídeo:**

| Palavra | O que é | Pensa assim |
|---|---|---|
| **Projeto** | A pasta do seu vídeo (`--project`) | A pasta do projeto no Premiere ou no DaVinci |
| **Beat** | Um momento do vídeo que precisa de B-roll | Um marcador na timeline |
| **Candidato** | Um vídeo encontrado que *pode* servir | Um clipe no bin, ainda não usado |
| **ID** | O código do candidato (ex.: `youtube:abc123`) | O nome do clipe |
| **Trecho** | O pedaço que você quer, em segundos | O in e o out |
| **A-roll** | A sua gravação: a pessoa falando, a narração, o UGC. Fica em `aroll/` | A faixa V1 |
| **Roteiro** | O `ROTEIRO.md`: o vídeo escrito cena por cena. Os beats nascem dele | As faixas da timeline em texto |
| **Componente** | Marca, lettering, SFX, música, imagem ou composição, em `assets/` | Os bins do projeto |
| **Receita** | O formato selado de um vídeo que deu certo, numa versão que não muda | O template do cliente |
| **Exportador** | Um plugin que transforma o roteiro revisado num projeto de edição | O preset de export |

**Do código:**

| Palavra | O que é |
|---|---|
| **Terminal** | A janela onde você digita comandos. No Mac: app *Terminal*. No Windows: *PowerShell* |
| **Comando** | Uma linha que você cola no terminal e aperta Enter |
| **Flag** | As opções que começam com `--`. Ex.: `--project` diz *qual* projeto |
| **JSON** | O formato da resposta: um texto organizado em `"chave": valor` |
| **Hash (sha256)** | A impressão digital de um arquivo: mudou um byte, muda o hash |
| **Plugin** | Uma extensão do próprio getbrolls (fonte, rota, comando ou exportador). Não é o plugin do Claude Code |
| **Perfil** | Um `getbrolls.toml` que fixa pastas e executáveis para um workspace |

## Instalar

São três jeitos. O passo a passo completo, com a lista de ferramentas do sistema (Python 3.11+, FFmpeg, Node 22+, curl, Git), está no [README](../README.md#comece-aqui).

**No Claude Code:** `/plugin marketplace add engenheirodevideo/get-brolls`, depois `/plugin install get-brolls@engenheirodevideo` e `/get-brolls:get-brolls-setup`. Nada mais: o agente roda os comandos por você.

**Como comando no terminal:**

```bash
uv tool install git+https://github.com/engenheirodevideo/get-brolls@v2.6.1
getbrolls setup
getbrolls doctor
```

Com o pipx, a primeira linha é `pipx install git+https://github.com/engenheirodevideo/get-brolls@v2.6.1`.

**Como skill de outro agente (Codex e outros):** clone o repositório e rode o instalador da pasta, como no [README](../README.md#como-skill-codex-claude-code-e-outros-agentes).

- Em todo este manual, o comando é `getbrolls …`. Num clone, sem instalar o pacote, `python3 scripts/gb.py …` roda os mesmos comandos (no Windows, `python scripts/gb.py …`; de outra pasta, use o caminho absoluto de `scripts/gb.py`).
- **`getbrolls: command not found` depois de instalar?** Rode `uv tool update-shell` (ou `pipx ensurepath`), abra um terminal novo e confira com `getbrolls --version`.
- **O `setup` instala o runtime:** o yt-dlp numa venv e o Playwright CLI, em `$GB_HOME/runtime` (padrão `~/.getbrolls/runtime`). FFmpeg, ffprobe, curl e Node são do sistema: ele só confere e mostra o comando de instalar.
- **Rodar de novo é seguro:** o que já está pronto não é refeito. Depois de atualizar o getbrolls, rode `getbrolls setup` de novo.
- **Está pronto quando** o `doctor` mostra `"ready": true`. Com código `4`, leia `summary.missing`: cada item traz o comando que resolve.

Os detalhes do runtime (`setup --where`, `setup --upgrade ytdlp`, venv quebrada, Windows) estão no [GUIDE](GUIDE.md#instalar-como-pacote).

## Tutorial: seu primeiro B-roll em 5 minutos

Usa a NASA, que não pede chave. **Rode um por vez, na ordem.**

- Linha que começa com `#` é explicação: não precisa copiar.
- Troque `/caminho/meu-video` pela pasta do seu vídeo e `<ID>` pelo código que a busca devolver. **Mantenha as aspas.**

```bash
# PASSO 0 · CRIAR O PROJETO: project.json e as pastas aroll/, assets/, broll/ e analysis/.
getbrolls init --project /caminho/meu-video

# PASSO 1 · BUSCAR: 3 vídeos do lançamento do Artemis. Cada item tem um "id": copie o que quiser.
getbrolls search --provider nasa --query "Artemis launch" --limit 3 --project /caminho/meu-video

# PASSO 2 · PRÉVIA: um GIF do segundo 0 ao 4 e uma folha de quadros em brolls/previews/.
getbrolls preview --candidate "<ID>" --start 0 --end 4 --project /caminho/meu-video

# PASSO 3 · STORYBOARD: monta a página e devolve um link. Abra, clique em Aprovar e em "Salvar decisões".
getbrolls review --project /caminho/meu-video
getbrolls serve --background --project /caminho/meu-video

# PASSO 4 · TRAZER A APROVAÇÃO: lê o que você salvou e fecha a página.
getbrolls import-review --by "Seu nome" --project /caminho/meu-video
getbrolls serve --stop --project /caminho/meu-video

# PASSO 5 · DIREITOS: registra os termos de uso padrão da NASA para esse vídeo.
getbrolls permit --candidate "<ID>" --preset nasa --project /caminho/meu-video

# PASSO 6 · BAIXAR E ENTREGAR: corta só o trecho aprovado, confere e organiza.
getbrolls fetch --candidate "<ID>" --project /caminho/meu-video
getbrolls verify --project /caminho/meu-video
getbrolls deliver --project /caminho/meu-video
```

**Pronto:** o corte está em `broll/`, e a `entrega/` traz uma pasta por beat com um `ORIGEM.md` dizendo de onde ele veio. Como este teste não usou BRIEF, ele cai em `entrega/00-sem-beat/`.

## O caminho inteiro em 8 passos

```text
1 conferir → 2 planejar → 3 buscar → 4 prévia
                                        ↓
8 entregar ← 7 baixar ← 6 direitos ← 5 aprovar
```

É igual a um fluxo de edição: **ingest → seleção → aprovação do cliente → entrega**. Depois da entrega, três caminhos opcionais: a **memória** (o que funcionou fica guardado), o **roteiro** (o vídeo nasce do texto) e os **plugins** (fontes, comandos e exportadores novos).

## Os comandos, passo a passo

Consulte quando precisar. Cada bloco é um passo do caminho. Todo comando explica o que aceita com `getbrolls <comando> --help`.

### 1 Conferir

> **No chat:** `/get-brolls:get-brolls-setup` instala e confere · `/get-brolls:get-brolls-status` diz onde você parou.

```bash
# providers · de onde dá para puxar vídeo, se cada fonte está ligada e se pede chave de API.
getbrolls providers

# doctor · confere FFmpeg, Node, yt-dlp e o resto. Pronto quando "ready" é true e
#   "summary.missing" vem vazio; faltando algo, sai com código 4.
getbrolls doctor

# setup --check · mostra o que falta no runtime, sem instalar nada.
getbrolls setup --check

# doctor --live · igual, mas faz buscas de teste de verdade. Gasta cota de Pexels e Pixabay.
getbrolls doctor --live

# status · em que etapa o projeto está, com o PRÓXIMO COMANDO pronto em "summary.do". Só lê.
getbrolls status --project /caminho/meu-video
```

### 2 Planejar (opcional, mas recomendado)

> Pensa como a **pré-produção**: o RULES é o padrão do canal, o BRIEF é o plano do vídeo (o que cada beat precisa mostrar).
> **No chat:** `/get-brolls:get-brolls-brief` faz uma entrevista de até 7 perguntas e escreve o BRIEF por você.

```bash
# init · cria um projeto novo: project.json e as pastas aroll/, assets/ (sete pastas),
#   broll/ e analysis/. Recusa pasta que já é projeto (essa usa migrate).
getbrolls init --canvas 1080x1920 --fps 30 --project /caminho/meu-video

# migrate · adota o layout novo num projeto antigo. plan só mostra; apply só acrescenta o
#   project.json, sem mover, renomear nem apagar nada.
getbrolls migrate --action plan --project /caminho/meu-video
getbrolls migrate --action apply --project /caminho/meu-video

# init-rules · cria o RULES.md: formato, fontes preferidas e sites bloqueados.
#   --format: reels (9:16) · horizontal (16:9) · native (formato original)
getbrolls init-rules --format reels --project /caminho/meu-video

# init-rules com declaração · você assume o uso do material, em vez de juntar prova por item.
#   Escreva a sua frase: a ferramenta nunca preenche uma declaração por você.
getbrolls init-rules --mode user_declaration --responsible "Seu nome" --declaration "Frase da sua declaração" --project /caminho/meu-video

# rules · mostra as regras valendo agora neste projeto.
getbrolls rules --project /caminho/meu-video

# init-brief · cria o BRIEF.md, o plano do vídeo dividido em beats. Depois, preencha.
getbrolls init-brief --project /caminho/meu-video

# brief · para cada beat, o comando de busca pronto. --validate só confere o BRIEF.
getbrolls brief --project /caminho/meu-video
getbrolls brief --validate --project /caminho/meu-video
getbrolls brief --beat "<ID_DO_BEAT>" --project /caminho/meu-video
```

Sem roteiro, o caminho é `init` → `init-rules` → `init-brief` → `brief`. Com roteiro, veja [Roteiro](#roteiro-o-vídeo-nasce-do-texto).

### 3 Buscar

> Aqui é o **ingest**: cada resultado entra no projeto como candidato. **Nada é baixado ainda.**
> Busque com **poucas palavras** e **no idioma do vídeo** (vídeo gringo, busca em inglês).

```bash
# search · pesquisa e salva os resultados como candidatos.
#   --provider: youtube · nasa · commons · pexels · pixabay   --limit: quantos (padrão 8)
getbrolls search --provider youtube --query "Sua busca" --project /caminho/meu-video

# search --shot · liga os resultados a um beat do BRIEF; a entrega sai organizada por beat.
getbrolls search --provider youtube --query "Sua busca" --shot "<ID_DO_BEAT>" --project /caminho/meu-video

# search --dry-run · só espia o resultado, sem salvar nada no projeto.
getbrolls search --provider youtube --query "Sua busca" --dry-run --project /caminho/meu-video

# search --intent --media · literal (a coisa exata) ou illustrative (uma ideia);
#   image · video · any. Só NASA e Commons têm os dois tipos.
getbrolls search --provider nasa --query "Sua busca" --intent illustrative --media image --project /caminho/meu-video

# resolve --url · registra um vídeo pelo link: YouTube, Instagram, TikTok, Commons, NASA,
#   link direto .m3u8/.mp4 (fonte direct_stream) ou página com player embutido (embedded_player).
getbrolls resolve --url "URL_PUBLICA" --project /caminho/meu-video

# resolve --referer · no link direto, manda junto a página de origem do vídeo.
getbrolls resolve --url "https://cdn.videos.example/aula/master.m3u8" --referer "https://pagina.example/aula" --project /caminho/meu-video

# resolve --file · registra um arquivo seu. --creator e --source-url anotam a origem.
getbrolls resolve --file "/caminho/arquivo.mp4" --creator "Autor" --source-url "https://link.da.fonte" --project /caminho/meu-video

# resolve (print de notícia ou de site) · com título e data do print.
getbrolls resolve --file "/caminho/print.png" --asset-type news_screenshot --title "Título da notícia" --captured-at "2026-10-30T10:00:00Z" --source-url "https://link.da.noticia" --project /caminho/meu-video

# inspect · raio-x do vídeo: duração, capítulos e legendas. Com --query, em que minuto
#   falam do assunto (--max-windows de 1 a 20; padrão 3).
getbrolls inspect --candidate "<ID>" --project /caminho/meu-video
getbrolls inspect --url "URL_PUBLICA" --query "O que encontrar no vídeo" --max-windows 5 --project /caminho/meu-video

# browser-plan · planeja o print de uma página (notícia, site) pelo navegador. Não captura.
getbrolls browser-plan --url "URL_DA_PAGINA" --project /caminho/meu-video
```

O link direto só aceita HTTPS público, sem usuário e senha, fora da rede local. Quando o getbrolls não acha o vídeo numa página, a recusa traz `suggested_route` com o próximo comando ou o caminho manual. Os detalhes estão no [GUIDE](GUIDE.md#provedor--link-direto-e-player-embutido).

### 4 Prévia e Storyboard

> É o **offline**: você vê o trecho em movimento, leve, antes de baixar em alta.
> **No chat:** `/get-brolls:get-brolls-review` monta, manda o link e importa suas decisões.

```bash
# preview · GIF, pôster e folha de quadros do trecho, até 10 s por padrão (GB_PREVIEW_MAX_SECONDS, até 30).
#   --narration (a fala) e --reason (por que escolheu) aparecem no Storyboard.
getbrolls preview --candidate "<ID>" --start 10 --end 15 --narration "Fala exata do roteiro" --reason "Motivo da escolha" --project /caminho/meu-video

# preview de foto · sem --start/--end: a prévia é a própria imagem.
getbrolls preview --candidate "<ID>" --project /caminho/meu-video

# preview --scan · uma folha com quadros do vídeo inteiro, cada um com o tempo.
getbrolls preview --candidate "<ID>" --scan --project /caminho/meu-video

# preview --reference-only · só uma imagem parada de referência, sem baixar o vídeo.
getbrolls preview --candidate "<ID>" --reference-only --project /caminho/meu-video

# review · monta o Storyboard (brolls/review.html) com todas as prévias.
getbrolls review --project /caminho/meu-video

# serve · abre o Storyboard. Use o link que aparecer em "urls" (normalmente a porta 8767).
#   Em cada trecho: Aprovar · Pedir ajuste · Reprovar. No fim: "Salvar decisões".
getbrolls serve --background --project /caminho/meu-video
getbrolls serve --background --port 8893 --project /caminho/meu-video
getbrolls serve --stop --project /caminho/meu-video
```

### 5 Aprovar

> É a **aprovação do cliente**. Primeira trava: sem ela, o corte final não sai.

```bash
# import-review · traz o que você decidiu no Storyboard. --by fica registrado como quem aprovou.
getbrolls import-review --by "Seu nome" --project /caminho/meu-video
getbrolls import-review --file "/caminho/decisoes.json" --by "Seu nome" --project /caminho/meu-video

# approve · aprova pelo chat, sem abrir o Storyboard, com a frase exata recebida.
#   Repita --candidate para cada trecho mostrado; --all só se a pessoa viu todas as prévias.
getbrolls approve --candidate "<ID>" --by "Seu nome" --channel chat --statement "Frase exata da aprovação recebida" --project /caminho/meu-video

# reject · descarta o candidato e guarda o motivo. Daqui a meses você sabe por que saiu.
getbrolls reject --candidate "<ID>" --reason "Motivo do descarte" --project /caminho/meu-video
```

Mudou o `video_format` no RULES.md depois de aprovar? O comando para antes de mexer em nada e diz quais aprovações cairiam. Se é isso mesmo, repita com `--confirm-format-change`.

### 6 Direitos de uso

> Segunda trava. Registra **de quem é o material**. **Escolha UMA das três opções.**
> A ferramenta só **registra**: ela não confere licença. Quem responde pelo uso é você.

```bash
# Opção A · as condições que você leu na página da fonte.
getbrolls permit --candidate "<ID>" --evidence "Condições de uso reais dessa fonte" --project /caminho/meu-video

# Opção B · o texto de termos padrão da fonte: youtube · nasa · commons · pexels · pixabay.
#   Mesmo assim, confira a página do vídeo.
getbrolls permit --candidate "<ID>" --preset youtube --project /caminho/meu-video

# Opção C · você assume a responsabilidade: nome completo e uma frase de pelo menos 20 caracteres.
getbrolls permit --candidate "<ID>" --declared-by "Seu nome completo" --declaration-text "Frase dizendo que você assume o uso" --project /caminho/meu-video
```

### 7 Baixar

```bash
# fetch · baixa e corta só o trecho aprovado (1080p quando a fonte tem).
#   Vai para broll/ (projeto do init) ou brolls/clips/ (projeto da 2.5).
#   Recusou? Falta aprovar (passo 5) ou registrar os direitos (passo 6).
getbrolls fetch --candidate "<ID>" --project /caminho/meu-video

# fetch --reacquire · fonte de plugin já consumida (compra ou cota de uso único).
getbrolls fetch --candidate "<ID>" --reacquire --project /caminho/meu-video

# verify · confere se cada arquivo está inteiro e abre (hash e decodificação).
getbrolls verify --project /caminho/meu-video
```

### 8 Entregar

```bash
# deliver --dry-run · mostra como a entrega vai ficar, sem criar nada.
getbrolls deliver --dry-run --project /caminho/meu-video

# deliver · organiza tudo em entrega/, uma pasta por beat, com o corte, a folha de quadros
#   e o ORIGEM.md (fonte, autor, trecho e hash). Sem BRIEF, tudo vai para 00-sem-beat/.
getbrolls deliver --project /caminho/meu-video
```

Os arquivos de `entrega/` são os mesmos de `broll/` (hardlink, sem ocupar disco a mais) e nascem somente leitura. Quer cópias que você possa editar ali? Use `GB_DELIVERY_COPY=1` no `.env`.

## O que vai onde, na pasta do vídeo

```text
meu-video/
├── project.json          # a identidade do projeto (só no projeto criado com init)
├── ROTEIRO.md            # o roteiro, se você usar
├── BRIEF.md              # o plano do vídeo
├── RULES.md              # o padrão do canal
├── aroll/                # suas gravações, uma por cena: c01.mp4, c03.mp4…
├── assets/               # componentes: marca, lettering, sfx, musica, imagem, composicao, outros
├── broll/                # ⭐ OS CLIPES FINAIS: aponte o editor para cá
├── entrega/              # uma pasta por beat, para conferir e mandar para alguém
│   └── 01-abertura/      # número do beat + id (sem BRIEF: 00-sem-beat/)
│       ├── 01-abertura.mp4
│       ├── contact-sheet.jpg
│       └── ORIGEM.md
├── analysis/             # o que se sabe de cada mídia (veja Análise de mídia)
├── exports/              # projetos de edição gerados por um exportador
└── brolls/               # bastidor: não precisa mexer
    ├── manifest.json     # a ficha de todos os candidatos
    ├── review.html       # o Storyboard
    ├── previews/         # GIFs e folhas de quadros
    ├── credits.md        # créditos de tudo que foi usado
    └── getbrolls.log     # registro de cada passo, para achar erro
```

- **`broll/` não é `brolls/`.** `broll/` (sem s) guarda os clipes finais, conferidos por sha256: é o que você usa. `brolls/` (com s) é o bastidor: manifesto, prévias, logs. Não mexa nele.
- **Projeto da 2.5** (sem `project.json`): os clipes ficam em `brolls/clips/` e tudo continua igual.
- **Quando cada pasta nasce:** `aroll/` e `assets/` com `init` ou `roteiro --action new`; `exports/` no primeiro `export`; `brolls/` existe sempre.
- **Fora do projeto,** em `~/.getbrolls` (`$GB_HOME`): runtime, plugins, clientes, biblioteca e o `RULES.md` que vale para todos os vídeos.

## Roteiro: o vídeo nasce do texto

> **Experimental.** Só o gênero `reels` (9:16) nesta versão. O esqueleto vem com Gancho, Problema, Prova e CTA.

Você escreve o vídeo cena por cena no `ROTEIRO.md`: a fala, o que aparece na tela e as camadas (título, efeito, música). Depois que você revisa o texto, o getbrolls transforma as cenas em beats do `BRIEF.md` e a coleta segue como sempre. É escrever as faixas da timeline em texto: V1 é o A-roll, V2 o b-roll, e as camadas são lettering, SFX e música.

```markdown
---
type: roteiro
genero: reels
tema: "Manhã na praia"
duracao_alvo_s: 30
---

## Gancho
[A-ROLL]
Acordei e fui direto pro mar.

## Problema
[BROLL: praia vista de cima]
[SFX: whoosh]
Todo dia eu perdia a melhor luz.

## Prova
[SPLIT: A-ROLL | ondas quebrando]
[LETTERING: "6h da manhã" | destaque]
Agora eu chego antes do sol.

## CTA
[FULL: logo]
[MUSICA: trilha-suave]
Salva esse vídeo e testa amanhã.
```

- **Layout, um por cena:** `[A-ROLL]`, `[BROLL: alvo]`, `[SPLIT: esquerda | direita]`, `[FULL: alvo]` ou `[UGC: descrição]`. `[BROLL]` e o lado b-roll de um `[SPLIT]` viram beats de busca; `[FULL: logo]` usa um componente e não vira busca.
- **Camadas, quantas quiser:** `[LETTERING: "texto" | estilo]`, `[SFX: nome]`, `[MUSICA: nome]`, `[COMP: nome]`.
- **Tempo:** cerca de 2,5 palavras por segundo; cena sem fala vale 2 s. Com `duracao_alvo_s`, o `check` avisa quando passa.
- **O id de cada cena** (`<!-- c01 -->` no fim do título) é o `sync` que escreve. Você não escreve, e ao duplicar uma cena não copie esse comentário.
- **O roteiro é seu:** o agente pode redigir, mas você revisa e aprova o texto exato. Mudou uma palavra da fala ou um alvo? A revisão cai e você aprova de novo.

```bash
# new · cria o ROTEIRO.md com o esqueleto e as pastas aroll/ e assets/.
#   Sem RULES.md no projeto, cria um no formato reels. --force recomeça (o atual vira ROTEIRO.md.bak).
getbrolls roteiro --action new --genero reels --tema "Manhã na praia" --project /caminho/meu-video

# check · valida e mostra o plano de cenas, os avisos e o review.sha256. Só lê.
#   Texto de esqueleto entre chaves ({a dor, em uma frase}) vira o aviso ROTEIRO_ESQUELETO.
getbrolls roteiro --action check --project /caminho/meu-video

# init-brief · com o roteiro no projeto, o BRIEF nasce no formato dele e com "beats": [].
getbrolls init-brief --project /caminho/meu-video

# review · registra que VOCÊ revisou esse texto exato. --expect é o review.sha256 do check.
getbrolls roteiro --action review --by "Seu nome" --channel chat --statement "pode seguir" --expect <sha256> --project /caminho/meu-video

# plan · mostra o que o sync mudaria no BRIEF.md, sem gravar.
getbrolls roteiro --action plan --project /caminho/meu-video

# sync · grava os ids das cenas e os beats cNN no BRIEF.md (antes, guarda cópias .sync.bak).
#   Alvo novo num beat já aprovado pede --confirm-target-change.
getbrolls roteiro --action sync --project /caminho/meu-video

# brief --beat · o beat c02 com o comando de busca pronto. Daqui, a coleta de sempre.
getbrolls brief --beat c02 --project /caminho/meu-video

# assets · lista os componentes e diz onde um nome resolve. Só lê.
getbrolls assets --action list --project /caminho/meu-video
getbrolls assets --action where --kind sfx --name whoosh --project /caminho/meu-video
```

Cada componente resolve na ordem: projeto (`assets/<tipo>/`) → pasta do cliente → sua biblioteca pessoal (`~/.getbrolls/assets/<tipo>/`). Música, SFX e marca registram a licença num `<nome>.licenca.json` ao lado do arquivo (`origem`, `licenca`, `credito`). Ids de cena nunca voltam: cena removida deixa o beat aposentado, com candidatos e clipes intactos. O formato completo está no [GUIDE](GUIDE.md#roteiro-e-componentes).

### Do roteiro ao projeto de edição

Com o roteiro revisado e sincronizado, o `export` gera um projeto de edição por um **exportador de plugin** habilitado (o exemplo do SDK é o `exporter_example`, que gera uma lista de cortes em CSV).

```bash
# export --dry-run · mostra a pasta, os arquivos e a mídia que seriam gravados, sem gravar.
getbrolls export --to exporter_example --dry-run --project /caminho/meu-video

# export · cria uma pasta nova a cada vez: exports/exporter_example/001/, depois 002/…
getbrolls export --to exporter_example --project /caminho/meu-video
```

- Cada export é uma pasta nova, e `LATEST` guarda o número da mais nova. O getbrolls nunca apaga nem sobrescreve uma pasta de export: o que você edita lá dentro é seu.
- Dentro dela fica o `getbrolls-plan.json`, o plano exato que o exportador recebeu.
- **A voz de cada cena:** o A-roll gravado (`aroll/c01.mp4`) manda na duração da cena. A legenda palavra a palavra vem do `aroll/c01.transcript.json` ao lado do vídeo, se existir; senão, da transcrição em `analysis/`; senão, de uma estimativa.
- Sem exportador habilitado, ou com o `BRIEF.md` fora de dia com o roteiro, o `export` recusa e diz o que rodar.
- `export --to hyperframes` é recusado (saída `2`): o export é só para exportadores de plugin. Veja [Levar para o editor](#levar-para-o-editor).

## Clientes e receitas

Para quem produz para clientes. Pensa numa cozinha: a **prateleira** tem os ingredientes, a **receita** diz como montar, e o **roteiro** é o pedido.

- **A prateleira:** a pasta do cliente guarda marca, lettering, SFX, trilhas, imagens e composições, cada arquivo com a licença ao lado. Todo vídeo do cliente enxerga essa prateleira.
- **A receita:** o formato selado de um vídeo que deu certo (cenas, disposição na tela, estilo e componentes), numa versão que ninguém altera sem querer: `@1`, `@2`… Ela leva o formato, não o conteúdo: licenças, aprovações, A-roll e clipes nunca entram.
- **O que o getbrolls faz com as receitas:** encontra, lista, confere a integridade de cada arquivo e cria um projeto novo a partir de uma. Ele não sela receitas: nesta versão não há comando para criar uma versão nova.

```bash
# client add · cria <pasta>/cliente-exemplo/ com client.json, assets/, recipes/ e projetos/
#   e registra o cliente em ~/.getbrolls/clients.json. --root é uma pasta que já existe.
getbrolls client --action add --slug cliente-exemplo --name "Cliente Exemplo" --root /caminho/Clientes

# client list / show / remove · remove só desregistra; a pasta fica.
getbrolls client --action list
getbrolls client --action show --slug cliente-exemplo

# recipe list · as versões das receitas dos clientes registrados. Só lê.
getbrolls recipe --action list

# recipe show · uma versão, com o sha256 de cada arquivo e se ela está íntegra. Só lê.
getbrolls recipe --action show --ref cliente-exemplo.reels-split@1

# init --recipe · projeto novo da receita: project.json, ROTEIRO.md esboçado, componentes
#   e recipe.lock.json. Só roda com a receita íntegra.
getbrolls init --recipe reels-split@1 --client cliente-exemplo --tema "Lançamento" --project /caminho/Clientes/cliente-exemplo/projetos/v02

# init --client --slug · projeto novo, sem receita, dentro da pasta do cliente.
getbrolls init --client cliente-exemplo --slug video-01
```

- A receita mora na pasta do cliente, em `recipes/<slug>/<N>/`. Editou um arquivo dela à mão? A versão deixa de ser íntegra e o `recipe --action show` avisa.
- A licença dos componentes não vai junto para o projeto novo: o aviso `LICENCE_NOT_TRANSFERRED` lembra você de registrar.
- Se um componente da receita mudar depois, `status` e `export` avisam com `RECIPE_LOCK_DRIFT`.

## Memória: ele aprende com você

São duas memórias. Uma fica dentro do projeto e lembra as decisões deste vídeo. A outra fica na sua máquina e leva o que funcionou para os próximos. Consultar a memória nunca aprova nem libera direitos.

```bash
# remember · guarda um trecho aprovado ou reprovado neste vídeo, com o motivo.
getbrolls remember --candidate "<ID>" --decision approved --reason "Mostra o produto em uso" --by "Seu nome" --project /caminho/meu-video
getbrolls remember --candidate "<ID>" --decision rejected --reason "Texto na tela" --by "Seu nome" --project /caminho/meu-video

# references · tudo o que foi guardado neste vídeo.
getbrolls references --project /caminho/meu-video

# learn · guarda o que vale para todos os vídeos: uma busca que rendeu (hit) ou não (miss),
#   um gosto seu, ou um trecho já guardado com remember (--from-candidate, com --shot).
getbrolls learn --query "praia drone" --provider youtube --outcome hit --project /caminho/meu-video
getbrolls learn --query "praia drone" --provider youtube --outcome hit --note "Drone de manhã rende mais" --project /caminho/meu-video
getbrolls learn --preference "Prefiro imagem sem texto na tela" --by "Seu nome" --project /caminho/meu-video
getbrolls learn --from-candidate "<ID>" --shot "<ID_DO_BEAT>" --project /caminho/meu-video

# library · consulta a biblioteca antes de começar a buscar num vídeo novo.
getbrolls library --search "praia" --project /caminho/video-novo
```

A memória do projeto fica em `brolls/references.json`; a biblioteca, em `~/.getbrolls/library/`. O que você guarda a partir de um projeto de cliente só aparece em projetos do mesmo cliente. `GB_LIBRARY=off` desliga a biblioteca.

## Análise de mídia: seu material vira dado

Cada mídia do projeto ganha um id calculado pelos bytes do arquivo (`media_id`). Renomeou ou mudou de pasta, o id continua o mesmo; mudou um byte, o id muda. Assim a análise nunca aponta para o arquivo errado. É o metadado do clipe no bin, só que preso ao conteúdo.

```bash
# register · lê a mídia com o ffprobe e grava analysis/media/<media_id>/media.json
#   (duração, quadro, fps, codecs). O papel vem da pasta: aroll/, broll/, assets/musica/,
#   assets/sfx/. Use --role só quando a pasta não disser.
getbrolls analysis --action register --path aroll/c01.mp4 --project /caminho/meu-video

# list · as mídias registradas e se cada uma ainda bate com o arquivo. Só lê.
getbrolls analysis --action list --project /caminho/meu-video

# check · confere os arquivos de analysis/. Sai com código 1 quando acha problema. Só lê.
getbrolls analysis --action check --project /caminho/meu-video
```

Nesta versão, o getbrolls grava o `index.json` e o `media.json`. Transcrição, cenas, silêncio, falantes, visual e marcadores têm formato publicado e são gravados por plugins com permissão de escrita no projeto. O `export` usa a transcrição de `analysis/` para a legenda. Os formatos estão em [SCHEMAS.md](SCHEMAS.md#análise-de-mídia-analysis).

## Fila: vários Reels e TikToks sem tomar bloqueio

> Baixar 50 Reels de uma vez é o jeito mais rápido de tomar bloqueio. A fila espaça os downloads.
> Padrão: Instagram a cada 45–120 s, TikTok e YouTube a cada 15–40 s, no máximo 20 por hora e 60 por dia.

```bash
# add · coloca links na fila. Link repetido é ignorado.
getbrolls queue --action add --provider instagram --url "URL_DO_REEL" --project /caminho/meu-video

# next · o próximo link, se já puder. Senão, "wait_seconds" diz quanto esperar.
getbrolls queue --action next --provider instagram --project /caminho/meu-video

# mark · como foi o item: --done, --failed ou --skipped (com --reason).
getbrolls queue --action mark --id "<ID_DA_FILA>" --done --project /caminho/meu-video
getbrolls queue --action mark --id "<ID_DA_FILA>" --failed --reason "Motivo da falha" --project /caminho/meu-video

# status · quantos faltam, quantos foram e se está em pausa ou parada.
getbrolls queue --action status --project /caminho/meu-video

# resume · só VOCÊ roda, depois de confirmar que o acesso voltou. AT é o "stopped.at" do status.
getbrolls queue --action resume --provider instagram --by "Seu nome" --phrase "Sua frase exata" --expect AT --project /caminho/meu-video
```

Um limite de ritmo (429) pausa a fila por um tempo. Login, desafio, CAPTCHA ou recusa de acesso **param** a fila daquela fonte: esperar não libera nada, e só o `resume` tira a parada.

## Plugins

> **Experimental:** o contrato do SDK (`sdk_api` 1) pode mudar em versão minor.

Um plugin acrescenta ao getbrolls uma fonte de busca, uma rota de download, um comando próprio ou um exportador. Não é o plugin do Claude Code: é uma extensão do próprio getbrolls. Nenhum liga sozinho, e se o código mudar ele para até você revisar.

**De onde vêm.** No repositório, `plugins/bridges/` guarda as pontes oficiais (hoje, a do HyperFrames), `plugins/getbrolls/` os plugins oficiais feitos para o getbrolls (hoje nenhum), `plugins/community/` o lugar dos plugins da comunidade, que chegam por um marketplace, e `plugins/examples/` os modelos para copiar. Na sua máquina, todo plugin instalado mora numa pasta só: `$GB_HOME/plugins/` (padrão `~/.getbrolls/plugins/`).

**Instalar é sempre em dois passos:**

```bash
getbrolls plugins --action install --source <pasta ou URL git do plugin>
getbrolls plugins --action install --source <pasta ou URL git do plugin> --yes --expect <sha256 da prévia>
```

O primeiro só mostra o manifesto, as permissões, a origem e o `sha256` do conteúdo. Depois de ler o código e as permissões, o segundo instala e habilita.

```bash
# list · os plugins instalados, com estado e origem. As pontes oficiais aparecem à parte.
#   Estados: disabled · enabled · suspended · invalid · incompatible
getbrolls plugins --action list

# install de um repositório git · --commit fixa um sha; --subdir aponta a pasta dentro do repositório.
getbrolls plugins --action install --source "https://example.org/meus-plugins.git" --subdir "plugins/demo"

# enable / disable · liga ou desliga um plugin que já está na pasta. O enable mostra o manifesto
#   primeiro; com --yes, liga. Plugin suspenso (a pasta mudou) pede --yes --expect <sha256>.
getbrolls plugins --action enable --id "<id>"
getbrolls plugins --action enable --id "<id>" --yes
getbrolls plugins --action disable --id "<id>"

# update · compara com a origem gravada no install; confirma com --yes --expect <sha256>.
getbrolls plugins --action update --id "<id>"

# remove · em dois passos; os dados em plugin-data/ ficam.
getbrolls plugins --action remove --id "<id>"
getbrolls plugins --action remove --id "<id>" --yes

# x · roda um comando próprio de um plugin habilitado. Só lê o projeto.
getbrolls x --list
getbrolls x pasta_local recentes --arg limite=5 --project /caminho/meu-video
```

**Marketplaces.** Um marketplace é um repositório git com um índice (`getbrolls-marketplace.json`), fixado num commit. Qualquer pessoa pode publicar um. O selo (`tier`) de cada plugin é o que o dono do índice declara, não uma verificação.

```bash
getbrolls plugins --action marketplace-add --source "<url-git-do-marketplace>"
getbrolls plugins --action marketplace-list
getbrolls plugins --action search --query "banco"
getbrolls plugins --action install --id "<id>@<marketplace>"
getbrolls plugins --action install --id "<id>@<marketplace>" --yes --expect "<sha256 da prévia>"
getbrolls plugins --action marketplace-update --marketplace "<nome>"
getbrolls plugins --action marketplace-remove --marketplace "<nome>"
```

O `search` lê só os índices em cache, sem rede. O `marketplace-update` não atualiza plugin nenhum: use `update`.

- Um plugin local não tem a verificação de ninguém: o getbrolls só confere que o conteúdo é o que você aprovou. Uma pasta copiada à mão para `$GB_HOME/plugins/` fica `disabled` até o `enable`.
- Um plugin local com o id de uma ponte ou de um plugin oficial é recusado.
- **Plugin não é caixa de areia:** roda com as suas permissões. Instale só o que você revisou.

Quer criar um? `getbrolls plugins --action new --id meu_exportador --kind exporter --path <pasta>` gera um plugin mínimo que já passa no teste (`--kind`: `provider`, `route`, `command` ou `exporter`), e `plugins --action check --path <pasta>/meu_exportador` o valida, **executando** o código dele. O caminho curto está no [guia rápido de plugins](PLUGIN_DEV_QUICKSTART.md); a referência, no [SDK](SDK.md).

## Levar para o editor

O getbrolls coleta, mostra a prévia e entrega os clipes com origem e condições de uso. A montagem e o render acontecem no seu editor.

**Qualquer editor (Premiere, DaVinci, Final Cut, CapCut…).** Você não precisa de ponte nenhuma. Rode `fetch` e `verify`: os clipes aprovados caem em `broll/`. No editor, importe essa pasta. Para mandar para alguém ou conferir por beat, use a `entrega/`, onde cada clipe vem com o `ORIGEM.md` (fonte, autor, trecho e uso).

**HyperFrames.** Quem monta e renderiza é o próprio HyperFrames. A ponte HyperFrames (`plugins/bridges/hyperframes`) só faz a passagem entre os dois: o getbrolls não exporta uma timeline do HyperFrames, e isso é de propósito. Ela vem no pacote, conferida por sha256, e você vê a versão e o estado dela com:

```bash
getbrolls environment --action list
```

Para desligá-la, use `GB_PLUGINS=off` ou `plugins = []` no perfil. O que ela é, em detalhe: [README da ponte](../plugins/bridges/hyperframes/README.md).

**Um exportador de plugin.** Com o roteiro revisado, `export --to <exportador>` gera o projeto do editor que o plugin sabe montar, numa pasta nova. Veja [Do roteiro ao projeto de edição](#do-roteiro-ao-projeto-de-edição).

## Perfil do workspace

Um `getbrolls.toml` na pasta do workspace fixa pastas, executáveis e plugins para quem trabalha ali, sem script de variáveis de ambiente:

```toml
requires = ">=2.6,<3"                           # versão do getbrolls que este workspace aceita
home = "/caminho/fora-do-repositorio/.getbrolls" # o GB_HOME deste workspace
cache_dir = ".cache/getbrolls"
plugins = []                                     # teto: só estes plugins podem valer ([] = nenhum)

[tools]                                          # só caminhos absolutos
ffmpeg = "/usr/local/bin/ffmpeg"
ffprobe = "/usr/local/bin/ffprobe"
```

O perfil pode apontar executáveis, então só vale depois que você confia nele, em dois passos:

```bash
getbrolls profile --action trust
getbrolls profile --action trust --yes --expect <sha256>
getbrolls profile --action show
getbrolls profile --action untrust
```

O primeiro mostra o que ele fixaria e o `sha256`; o segundo confirma. Editou o arquivo? Precisa confiar de novo. O `show` lista cada campo com o valor e a origem. A precedência é flag > ambiente (e `.env`) > perfil > padrão. Num repositório compartilhado, ponha o `home` fora dele. As regras completas: [GUIDE](GUIDE.md#perfil-do-workspace-getbrollstoml) e [SECURITY](SECURITY.md#perfil-getbrollstoml).

## Ajuda

```bash
# Lista todos os comandos
getbrolls --help

# Mostra a versão, o Python e de onde vêm os dados: getbrolls 2.6.1 (Python 3.12.4; dados: wheel)
# (num clone, o script dele diz dados: checkout)
getbrolls --version

# Mostra tudo o que um comando aceita (troque "search" por qualquer um)
getbrolls search --help

# Descreve em JSON todos os comandos, flags, o que só lê, códigos de saída e comandos de plugin.
# Não precisa de projeto e não grava nada.
getbrolls capabilities --json
```

## Como ler a resposta

Todo comando responde em **JSON**. Pensa como o **relatório de render**: um texto organizado que diz o que aconteceu. Três campos resolvem quase tudo.

```json
{
  "summary": {
    "line": "Coletei o corte final de nasa:abc em broll/abc.mp4."
  }
}
```

- **`summary.line`** é sempre uma frase em português dizendo o que acabou de acontecer. **Se for ler uma coisa só, leia essa.**
- No **`status`**, o **`summary.do`** traz o próximo passo: `command` (o comando pronto), `why` (o motivo) e `blocking_human` (`true` quando o próximo passo depende de **você**).
- **Deu erro?** A mensagem sai em stderr, numa linha JSON, com `error` (o que houve) e `error_code` (o tipo). Nunca sai traceback: os detalhes ficam em `brolls/diagnostics.jsonl`.

**Código de saída** (o número que o terminal guarda depois de cada comando):

| Código | Significa |
|---|---|
| `0` | Deu certo |
| `1` | Erro de operação ou de dados: ID errado, falta aprovação, link fora do ar. Com `LOCKED`, outro comando está usando o projeto: espere e repita |
| `2` | Erro de uso: comando ou flag digitado errado. Vem com "Você quis dizer…?" |
| `3` | Erro interno (bug). Abra uma issue com o `brolls/diagnostics.jsonl` |
| `4` | Falta um pré-requisito da instalação (ffmpeg, yt-dlp, arquivos de dados). Rode `getbrolls setup` |
| `130` | Interrompido com Ctrl+C |

> Até a 2.5, erro de operação ou de dados também saía com `2`. Script que testava `$? -eq 2` para esses erros agora testa `1`.

O contrato completo (stdout e stderr, todos os `error_code`, os avisos, `--yes`/`--expect`) está em [CLI_CONTRACT.md](CLI_CONTRACT.md). Os formatos dos arquivos gravados estão em [SCHEMAS.md](SCHEMAS.md).

## Os arquivos que você edita

Dois arquivos na pasta do projeto guardam as suas escolhas. Os dois são Markdown com **um bloco JSON dentro**. **Edite só o bloco JSON.**

### BRIEF.md: o plano de B-rolls deste vídeo

Criado pelo `init-brief` ou pela entrevista do `/get-brolls:get-brolls-brief`.

```json
{
  "version": 1,
  "video": {
    "title": "Nome do vídeo",
    "objective": "O que o vídeo precisa provar pra quem assiste",
    "delivery": { "format": "reels", "duration_s": null, "platform": null }
  },
  "rights": { "posture": "per_item_evidence", "stock_allowed": false },
  "defaults": {
    "allowed_sources": ["youtube", "commons", "nasa"],
    "intent": "literal",
    "duration_hint_s": 4,
    "stock": false
  },
  "beats": [
    {
      "id": "abertura",
      "narration": "A fala exata deste trecho",
      "target": "O que precisa aparecer na tela",
      "queries": [],
      "blocked_reason": null
    }
  ]
}
```

| Campo | O que você coloca |
|---|---|
| `video.title` / `video.objective` | **Obrigatórios.** Nome do vídeo e o que ele precisa mostrar |
| `delivery.format` | `reels` (9:16), `horizontal` (16:9) ou `native` |
| `rights.posture` | `per_item_evidence` (direitos um por um) ou `user_declaration` (você assume tudo) |
| `stock_allowed` | `true` libera Pexels e Pixabay. Padrão: `false` (fonte real primeiro) |
| `defaults` | O que vale para todo beat: fontes, `intent`, duração sugerida |
| `intent` | `literal` (a coisa exata citada) ou `illustrative` (ideia genérica) |
| `beats[].id` | Nome curto do beat: letras minúsculas, números e hífen. Ex.: `abertura` |
| `beats[].target` | **Obrigatório.** O que precisa aparecer na tela |
| `beats[].queries` | Buscas que já funcionaram. A primeira vira a busca sugerida |
| `beats[].blocked_reason` | O beat depende de algo que só você tem. A ferramenta não o busca até você apagar |

Com roteiro, os beats `cNN` nascem do `roteiro --action sync`: não escreva esses à mão. Depois de editar, rode `brief --validate`. O formato completo: [BRIEF.md](BRIEF.md).

### RULES.md: o padrão do seu canal

Criado pelo `init-rules`. Vale para todo o projeto.

```json
{
  "version": 1,
  "video_format": "native",
  "preferred_providers": {
    "literal": ["youtube", "commons", "nasa"],
    "illustrative": ["pexels", "pixabay"]
  },
  "blocked_domains": [],
  "copyright": {
    "mode": "per_item_evidence",
    "responsible_person": null,
    "declaration": null
  }
}
```

| Campo | O que você coloca |
|---|---|
| `video_format` | `native`, `reels` ou `horizontal` |
| `preferred_providers` | A ordem das fontes para busca literal e para busca ilustrativa |
| `blocked_domains` | Sites que nunca podem entrar. Ex.: `["exemplo.com"]` |
| `copyright.mode` | `per_item_evidence` ou `user_declaration` |
| `responsible_person` / `declaration` | Seu nome e a frase, se usar `user_declaration` |

> **Quer uma regra para todos os projetos?** Crie `~/.getbrolls/RULES.md`. A ordem é: global → arquivo em `GB_RULES_FILE` → RULES do projeto. Num valor único, o último vence; numa lista, as camadas se somam. O `copyright` vale só no RULES do projeto. Detalhes: [RULES.md](RULES.md).

### .env: configurações da ferramenta

Opcional. Fica em `~/.getbrolls/.env`. Copie o [`.env.example.pt-BR`](../.env.example.pt-BR) e tire o `#` só da linha que quiser mudar. A lista completa das variáveis `GB_*` está em [CLI_CONTRACT.md](CLI_CONTRACT.md#variáveis-de-ambiente).

**Qual `.env` vale** (só um é lido, nunca se misturam): `--env-file`, depois `GB_ENV_FILE`, depois o `.env` da pasta da instalação (clone ou plugin) e por fim `$GB_HOME/.env`. Com dois, o primeiro vale e todo comando avisa `ENV_FILE_SHADOWED`. Defina `GB_HOME` no ambiente do terminal; `GB_ENV_FILE` e `GB_PROFILE` dentro de um `.env` são recusados.

```bash
# Chaves dos bancos de imagem (grátis nos sites deles)
PEXELS_API_KEY=sua_chave
PIXABAY_API_KEY=sua_chave

# Prévia: gif (padrão) ou static (só imagens paradas)
GB_PREVIEW_MODE=gif

# Largura do GIF em pixels (160 a 720; padrão 360). Aumente para ver texto pequeno
GB_GIF_WIDTH=480

# Entrega com cópias independentes, que você pode editar em entrega/
GB_DELIVERY_COPY=1

# Quanto detalhe vai para o brolls/getbrolls.log: DEBUG, INFO (padrão), WARNING, ERROR, off
GB_LOG_LEVEL=INFO
```

## Automação

Como toda resposta é JSON, dá para **encadear comandos num script**. Pensa como uma **action do Photoshop**: você monta uma vez e roda quando quiser. Você vai precisar do `jq`, que lê JSON no terminal (no Mac, `brew install jq`).

**Pegar o ID sem copiar na mão:**

```bash
ID=$(getbrolls search --provider nasa --query "Artemis launch" --limit 1 --project /caminho/meu-video | jq -r '.items[0].id')
getbrolls preview --candidate "$ID" --start 0 --end 4 --project /caminho/meu-video
```

**Perguntar qual é o próximo passo:**

```bash
getbrolls status --project /caminho/meu-video | jq -r '.summary.line, .summary.do.command'
```

**Buscar todos os beats de uma vez** (num arquivo `buscar_beats.sh`, rodado com `bash buscar_beats.sh`):

```bash
#!/bin/bash
set -e   # para no primeiro comando que falhar
PROJETO="/caminho/meu-video"
getbrolls search --provider youtube --query "rocket launch" --limit 5 --shot "abertura" --project "$PROJETO"
getbrolls search --provider nasa --query "Artemis crew" --limit 5 --shot "tripulacao" --project "$PROJETO"
getbrolls status --project "$PROJETO" | jq -r '.summary.line'
```

> **O que NUNCA automatizar:** a aprovação e os direitos. `approve` e `permit` registram uma **decisão sua**. Um script que aprova tudo sozinho derruba justamente a trava que protege o seu vídeo.

## Deu erro?

| Aconteceu | Faça |
|---|---|
| `getbrolls: command not found` | Com o pacote instalado, rode `uv tool update-shell` (ou `pipx ensurepath`) e abra um terminal novo. Num clone ou no plugin não existe `getbrolls`: o comando é o script da instalação |
| Falta alguma ferramenta | Rode `getbrolls doctor`, depois `getbrolls setup` |
| `fetch` recusou | Faltou aprovar (passo 5) ou registrar os direitos (passo 6) |
| Busca vazia | Menos palavras, no idioma do vídeo |
| "excede o teto de prévia" | A prévia aceita até 10 s por padrão (GB_PREVIEW_MAX_SECONDS, até 30) |
| Salvei no Storyboard e nada | Rode `import-review` |
| Não sei onde parei | Rode `status` |
| `LOCKED` | Outro comando está usando o projeto. Espere e repita |
| `jq` não existe | `brew install jq` (Mac) |
| Erro estranho | Olhe as últimas linhas de `brolls/getbrolls.log`. Comandos sem projeto (`plugins`, `client`, `recipe`, `profile`, `setup`) gravam em `~/.getbrolls/logs/getbrolls.log` |

## Perguntas frequentes

**O getbrolls edita ou renderiza o meu vídeo?** Não. Ele acha a fonte, mostra a prévia, espera a sua aprovação e entrega os trechos com a origem anotada. A montagem e o render são do seu editor (ou do HyperFrames).

**Preciso de chave de API ou de login?** Para começar, não. YouTube, TikTok, Commons e NASA funcionam sem chave. Pexels e Pixabay usam a chave de cada banco e só entram quando você pede stock. O Instagram passa pelo navegador autorizado, com a sua sessão.

**O getbrolls confere a licença por mim?** Não. Ele registra a decisão e a origem; quem responde pelo uso é você. Mesmo com o `--preset`, confira a página do vídeo.

**Dá para buscar por palavra no Instagram e no TikTok?** Não. Nos dois você precisa da URL do post, achada no navegador. Se o site pedir login ou desafio, o getbrolls relata o erro real em vez de contornar.

**Qual pasta levo para o editor: `broll/` ou `entrega/`?** `broll/`. A `entrega/` é renumerada quando a ordem dos beats muda: use para conferir ou mandar para alguém.

**Meu projeto é da 2.5. Preciso migrar?** Não. Sem `project.json`, ele segue igual. Se quiser o layout novo, `migrate --action plan` mostra o que mudaria. Projeto no layout novo exige getbrolls 2.6 ou mais novo.

**O plugin do getbrolls é o plugin do Claude Code?** Não. O do Claude Code traz os atalhos `/get-brolls:…` para o chat. O do getbrolls é uma extensão do próprio programa e fica em `~/.getbrolls/plugins/`.

## Atualizar

Atualizar não mexe nos seus projetos. Atualize o getbrolls pelo mesmo caminho em que instalou (o comando de instalação do README da versão nova, `/plugin update` no Claude Code ou `git pull` no clone) e rode o `setup` de novo: ele instala só a parte cuja versão das dependências mudou. Vindo da 2.5, veja [Atualizar da 2.5 para a 2.6](GUIDE.md#atualizar-da-25-para-a-26).

---

Referência completa: [GUIDE.md](GUIDE.md) · Formato do brief: [BRIEF.md](BRIEF.md) · Regras: [RULES.md](RULES.md) · Contrato da CLI: [CLI_CONTRACT.md](CLI_CONTRACT.md) · Formatos de arquivo: [SCHEMAS.md](SCHEMAS.md) · Plugins: [SDK.md](SDK.md)
