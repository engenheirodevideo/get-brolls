---
type: documentation
status: current
created: 2026-09-23
updated: 2026-09-30
tags: [get-brolls, manual, tutorial, commands]
---

# 🎬 getbrolls: manual + tutorial

Versão 2.6.0. Feito pra **videomaker que está começando a mexer com código**.

Você pede o B-roll, vê a prévia, aprova, e **só o trecho aprovado** é baixado, com a fonte anotada.

## ⚡ Se ler só isso, já dá pra usar

- 💬 **No Claude Code, é só pedir:** `/get-brolls` + o que você precisa + a pasta do vídeo. O agente roda os comandos por você.
- 🔒 **Nada é baixado sem duas coisas:** você **aprovar** o trecho e **registrar os direitos** de uso.
- 🧭 **Se perdeu?** Peça `/get-brolls-status` (ou rode o `status`). Ele diz o próximo passo.

## 📖 Dicionário rápido

**Do vídeo:**

| Palavra | O que é | Pensa assim |
|---|---|---|
| **Projeto** | A pasta do seu vídeo (`--project`) | A pasta do projeto no Premiere/DaVinci |
| **Beat** | Um momento do vídeo que precisa de B-roll | Um marcador na timeline |
| **Candidato** | Um vídeo encontrado que *pode* servir | Um clipe no bin, ainda não usado |
| **ID** | O código do candidato (ex.: `youtube:abc123`) | O nome do clipe |
| **Trecho** | O pedaço que você quer, em segundos | O in e o out |

**Do código:**

| Palavra | O que é |
|---|---|
| **Terminal** | A janela onde você digita comandos. No Mac: app *Terminal*. No Windows: *PowerShell* |
| **Comando** | Uma linha que você cola no terminal e aperta Enter |
| **Flag** | As opções que começam com `--`. Ex.: `--project` diz *qual* projeto |
| **JSON** | O formato da resposta. Um texto organizado em `"chave": valor`, que tanto você quanto um script conseguem ler |
| **Script** | Um arquivo com vários comandos em sequência, que roda sozinho |

## 📦 Instalar como comando

Além do plugin do Claude Code e do checkout, o getbrolls instala como pacote e ganha o comando `getbrolls`:

```bash
uv tool install git+https://github.com/engenheirodevideo/get-brolls@v2.6.0
# ou
pipx install git+https://github.com/engenheirodevideo/get-brolls@v2.6.0

getbrolls setup
getbrolls doctor
```

- Em todo este manual, `getbrolls …` e `python3 scripts/gb.py …` são o mesmo comando: use o primeiro no pacote instalado e o segundo dentro de um checkout.
- **Usa o plugin do Claude Code? Nada muda.** No checkout e no plugin, `python3 scripts/gb.py` continua igual.
- **`getbrolls setup` instala o runtime** e nunca pergunta nada:
  - o yt-dlp numa venv criada com o Python que roda o getbrolls, em `$GB_HOME/runtime/<versão>/.venv`;
  - o Playwright CLI (Instagram) com `npm ci --ignore-scripts`, em `$GB_HOME/runtime/<versão>/.tools` (precisa de Node 22+ e npm no `PATH`; sem eles, essa parte fica de fora com a dica de instalação);
  - `<versão>` é a das dependências de cada parte; com `GB_RUNTIME_DIR`, as duas partes vão para essa pasta;
  - FFmpeg, ffprobe, curl e Node são do sistema: o `setup` só confere e mostra o comando do seu sistema (`brew`, `apt` ou `winget install Gyan.FFmpeg`);
  - nunca mexe no projeto nem na pasta da instalação; progresso no stderr, resultado em JSON no stdout; sai `0` pronto e `4` se ainda falta algo;
  - o ambiente dos passos não leva segredos (`*_API_KEY`, `*_TOKEN`, `*_SECRET`), com uma exceção: o `npm ci` recebe o `NPM_TOKEN`, que o `.npmrc` de um registro privado costuma ler.
- **Rodar de novo é seguro:** o que já está pronto não é refeito, e uma instalação interrompida é refeita. **Depois de atualizar o getbrolls (ou o plugin), rode `getbrolls setup` de novo:** só a parte cuja versão das dependências mudou é instalada.
- **Venv quebrada se refaz sozinha.** A venv só conta como pronta se o Python dela ainda existe e importa o yt-dlp (`python -I -c "import yt_dlp"`, com tempo curto). Python do sistema removido ou atualizado (link `bin/python` quebrado, "bad interpreter"): o `setup` refaz a venv em vez de responder `already`, e o `setup --check`/`doctor` dão o yt-dlp como ausente, mesmo com outro yt-dlp no `PATH`.
- **Qual yt-dlp vale:** o do pin `GB_YTDLP_PATH`, depois o da venv (a do runtime ou `GB_VENV_PATH`), depois o do `PATH`. O `setup --check` julga esse mesmo executável: `steps[ytdlp].found` é o que está em uso e `source` diz de onde veio (`env`, `venv` ou `path`). Sem venv gerenciada, um yt-dlp do `PATH` vale, com `source: "path"` e uma nota; se o `setup` tentou montar a venv e falhou (ou ela ficou pela metade), o yt-dlp do `PATH` não esconde a falha e o `summary.line` nunca diz "Runtime pronto".
- **`getbrolls setup --upgrade ytdlp`** atualiza o yt-dlp da venv gerenciada além da versão fixada. `upgrade.status` diz o que houve:
  - `upgraded`: a versão mudou (`previous` → `version`);
  - `unchanged`: o pip terminou, mas a versão é a mesma (já era a mais nova, ou o índice de pacotes não respondeu), com o motivo em `upgrade.warnings`; sai `0`;
  - `failed`: a atualização falhou. Se a venv foi refeita na versão fixada, `ready` continua `true` e o `setup` sai com `1`, para o script perceber; se nem isso deu, a venv é removida, o `error` diz isso (rode `getbrolls setup` quando a causa for resolvida) e o `setup` sai com `4`.
- `getbrolls setup --where` mostra, sem instalar nada, onde cada parte fica e qual está em uso (`--where venv` ou `--where tools` para uma só). Sai `0`, salvo erro de uso do `.env` (`--env-file`/`GB_ENV_FILE` que não existe, linha recusada), que vale para qualquer comando e sai `2`.
- `getbrolls setup --check` mostra o que falta (yt-dlp, Playwright CLI, FFmpeg, ffprobe, curl, Node, npx e os arquivos de dados, os mesmos itens obrigatórios do `doctor`) e como resolver (`getbrolls setup` para yt-dlp e Playwright), sem instalar nada. `getbrolls doctor` diz se está pronto (`ready`) e sai com código `4` quando falta algo; o `ready` dos dois bate.
- **Python antigo:** abaixo do 3.11, `python3 scripts/gb.py` e os instaladores param antes de qualquer outra coisa com "getbrolls precisa de Python 3.11 ou mais novo; você tem X.Y" e código `4`.
- **Dois `setup` ao mesmo tempo:** o segundo para com `error_code: "LOCKED"` e código `1` (o mesmo de dois comandos no mesmo projeto); espere o primeiro terminar e rode de novo. A pasta `runtime/<versão>` nunca pode ser um link: o `setup` recusa em vez de instalar no destino dele.
- **Windows:** os comandos sugeridos pela ferramenta (`summary.do.command`) usam `python` e aspas duplas e rodam colados no PowerShell, no cmd e no Git Bash quando nenhum caminho tem `$`, `%` ou `"`; com esses caracteres, ajuste as aspas à mão.

## 🗂️ Perfil do workspace (`getbrolls.toml`)

Um `getbrolls.toml` na pasta do workspace fixa pastas, executáveis e plugins para quem trabalha ali, sem script de variáveis de ambiente:

```toml
requires = ">=2.6,<3"       # versão do getbrolls que este workspace aceita
home = ".getbrolls"         # GB_HOME (relativo ao próprio getbrolls.toml)
cache_dir = ".cache/getbrolls"
runtime_dir = "tools/runtime"
plugins = []                # teto: só estes plugins podem valer ([] = nenhum)

[tools]                     # só caminhos absolutos
ffmpeg = "/usr/local/bin/ffmpeg"
ffprobe = "/usr/local/bin/ffprobe"
```

- **Onde é achado:** `--profile <arquivo>`, depois `GB_PROFILE`, depois subindo a partir do `--project` e, por fim, da pasta atual. `--profile off` (ou `GB_PROFILE=off`) desliga.
- **Precedência:** flag > ambiente (incluindo o `.env`) > perfil > padrão. O `home` do perfil é aplicado antes, então decide qual `$GB_HOME/.env` é lido. `plugins` só estreita o `GB_PLUGINS`, nunca acrescenta.
- **Confiança em dois passos:** o perfil pode apontar executáveis, então só vale depois de `getbrolls profile trust` (mostra o que ele fixaria e o sha256) e `getbrolls profile trust --yes --expect <sha256>`. Editou o arquivo, precisa confiar de novo; `getbrolls profile untrust` desfaz. O único dispensado é `$GB_HOME/getbrolls.toml`.
- **Sem confiança:** só `doctor`, `profile`, `capabilities` e `setup --where`/`setup --check` rodam, relatando o problema (o `doctor` em `summary.missing`). Todo o resto para com código `2` e a dica do `profile trust` antes de tocar em nada, inclusive comandos que não usam projeto, como `providers` e `plugins --action list`, e o `setup` que instala. `requires` que não bate sai com `4`.
- **A prévia avisa:** `profile trust` lista em `warnings` o `home`, `runtime_dir` ou `cache_dir` que caem dentro da pasta do `getbrolls.toml` (num repositório compartilhado, quem grava ali poderia deixar um `.env` ou um `plugins.json` que o sha do perfil não cobre) e ids de `plugins` que não estão instalados (sem efeito até serem instalados).
- **`home` do perfil:** com o `GB_HOME` vindo do perfil, o `.env` e o `plugins.json` dessa pasta só valem se forem arquivos comuns, seus e não graváveis por grupo ou outros (as mesmas recusas do próprio `getbrolls.toml`); senão, qualquer comando para com código `2`.
- `getbrolls profile show` lista cada campo com o valor e a origem (`env`, `env_file`, `profile` ou `default`).
- O perfil não escolhe qual instalação roda: use `requires` para exigir a versão certa.

**Do script de variáveis para o perfil.** Um workspace que hoje usa um script assim:

```bash
export GB_HOME="$WS/.getbrolls"
export GB_CACHE_DIR="$WS/.cache/getbrolls"
export GB_VENV_PATH="$WS/tools/beta/.venv"
export GB_YTDLP_PATH="$WS/tools/beta/.venv/bin/yt-dlp"
export GB_FFMPEG_PATH="/usr/local/bin/ffmpeg"
export GB_FFPROBE_PATH="/usr/local/bin/ffprobe"
export GB_PLUGINS="off"
mkdir -p "$GB_HOME" "$GB_CACHE_DIR"
```

vira este `getbrolls.toml` na raiz do workspace (troque `/caminho/do/workspace` pelo caminho real):

```toml
requires = "==2.6.0"        # o clone fixado; outra versão sai com código 4
home = ".getbrolls"
cache_dir = ".cache/getbrolls"
plugins = []                # equivale a GB_PLUGINS=off

[tools]
venv = "/caminho/do/workspace/tools/beta/.venv"
ytdlp = "/caminho/do/workspace/tools/beta/.venv/bin/yt-dlp"
ffmpeg = "/usr/local/bin/ffmpeg"
ffprobe = "/usr/local/bin/ffprobe"
```

Depois de `getbrolls profile trust` e `getbrolls profile trust --yes --expect <sha256>`, `getbrolls doctor` dentro do workspace relata as mesmas pastas e os mesmos executáveis que o script. Não precisa mais do `mkdir`: as pastas são criadas quando o primeiro comando grava nelas.

**O que o perfil não faz:**

- Não habilita plugin: `plugins` só estreita o que o ambiente já permite (`[]` ou `"off"` desliga; uma lista limita). Quem quer habilitar um plugin continua usando `GB_PLUGINS` ou `plugins --action enable`.
- Não aceita caminho relativo em `[tools]`: ferramentas são caminhos absolutos. Só `home`, `cache_dir` e `runtime_dir` são relativos, ao lado do próprio `getbrolls.toml`.
- Não vale sem a confiança de dois passos, nem depois de o arquivo mudar; um `getbrolls.toml` que seja link simbólico é recusado.

## 🧭 O caminho inteiro em 8 passos

```text
1 🩺 conferir → 2 📝 planejar → 3 🔎 buscar → 4 👀 prévia
                                                   ↓
8 📦 entregar ← 7 ⬇️ baixar ← 6 📄 direitos ← 5 ✅ aprovar
```

É igual a um fluxo de edição: **ingest → seleção → aprovação do cliente → export**.

## 🚀 Tutorial: seu primeiro B-roll em 5 minutos

Usa a NASA, que não pede chave. **Rode um por vez, na ordem.**

**Como ler os blocos de código:**
- Linha que começa com `#` é **explicação**: não precisa copiar.
- Troque `/caminho/meu-video` pela pasta do seu vídeo e `<ID>` pelo código que a busca devolver. **Mantenha as aspas.**
- Rode **dentro da pasta da skill** (onde está o `scripts/gb.py`). Instalou como plugin do Claude Code? A pasta é a do plugin: veja [instalação](GUIDE.md#instalação).
- No Windows, escreva `python` no lugar de `python3`.

```bash
# ── PASSO 1: BUSCAR ─────────────────────────────────────────
# O QUE FAZ: procura 3 vídeos do lançamento do Artemis no acervo da NASA.
# VOCÊ RECEBE: uma lista. Cada item tem um "id". Copie o id do que quiser.
python3 scripts/gb.py search --provider nasa --query "Artemis launch" --limit 3 --project /caminho/meu-video

# ── PASSO 2: PRÉVIA ─────────────────────────────────────────
# O QUE FAZ: gera um GIF do segundo 0 ao 4 desse vídeo.
# VOCÊ RECEBE: o GIF e uma folha de quadros em brolls/previews/.
python3 scripts/gb.py preview --candidate "<ID>" --start 0 --end 4 --project /caminho/meu-video

# ── PASSO 3: STORYBOARD ─────────────────────────────────────
# O QUE FAZ: monta a página de aprovação e abre no navegador.
# VOCÊ RECEBE: um link. Abra, clique em Aprovar e depois em "Salvar decisões".
python3 scripts/gb.py review --project /caminho/meu-video
python3 scripts/gb.py serve --background --project /caminho/meu-video

# ── PASSO 4: TRAZER A APROVAÇÃO ─────────────────────────────
# O QUE FAZ: lê o que você salvou no navegador e fecha a página.
python3 scripts/gb.py import-review --by "Seu nome" --project /caminho/meu-video
python3 scripts/gb.py serve --stop --project /caminho/meu-video

# ── PASSO 5: DIREITOS ───────────────────────────────────────
# O QUE FAZ: registra os termos de uso padrão da NASA para esse vídeo.
python3 scripts/gb.py permit --candidate "<ID>" --preset nasa --project /caminho/meu-video

# ── PASSO 6: BAIXAR E ENTREGAR ──────────────────────────────
# O QUE FAZ: corta só o trecho aprovado, confere o arquivo e organiza.
# VOCÊ RECEBE: a pasta entrega/, pronta pra arrastar pro editor.
python3 scripts/gb.py fetch --candidate "<ID>" --project /caminho/meu-video
python3 scripts/gb.py verify --project /caminho/meu-video
python3 scripts/gb.py deliver --project /caminho/meu-video
```

✅ **Pronto:** o corte está em `entrega/`, com um `ORIGEM.md` dizendo de onde veio. Como este teste não usou BRIEF, ele cai na pasta `00-sem-beat/`.

---

# 📚 Todos os comandos, passo a passo

Consulte quando precisar. Cada bloco é um passo do caminho.

## 1 🩺 Conferir

> 💬 **No chat:** `/get-brolls-setup` instala e confere · `/get-brolls-status` diz onde você parou.

```bash
# ── providers ───────────────────────────────────────────────
# O QUE FAZ: lista de onde dá pra puxar vídeo (YouTube, NASA, Pexels…).
# VOCÊ RECEBE: cada fonte, se está ligada e se precisa de chave de API.
# QUANDO USAR: pra saber quais fontes estão disponíveis na sua máquina.
python3 scripts/gb.py providers

# ── doctor ──────────────────────────────────────────────────
# O QUE FAZ: confere se FFmpeg, Node, yt-dlp e o resto estão instalados.
# VOCÊ RECEBE: o que está ok e o que falta, com o nome do que instalar.
# QUANDO USAR: na primeira vez e sempre que algo der erro.
# DEU CERTO QUANDO: "summary.missing" vem vazio: [].
python3 scripts/gb.py doctor

# ── doctor: pronto ou não ───────────────────────────────────
# O doctor traz "ready" logo depois de "summary" e sai com código 4 quando
# "summary.missing" não está vazio (o JSON sai igual). No bloco "install" ele
# mostra a origem (pacote ou checkout), a pasta de dados, o runtime e qual .env valeu.

# ── setup --check ───────────────────────────────────────────
# O QUE FAZ: mostra o que falta no runtime e os comandos para resolver. Não instala nada.
# Sai com código 4 quando falta algo. Sem --check, "setup" instala o que falta
# (código 0 pronto, 4 se ainda falta item do sistema); --where só mostra as pastas.
python3 scripts/gb.py setup --check

# ── doctor --live ───────────────────────────────────────────
# O QUE FAZ: igual ao doctor, mas faz buscas de teste de verdade nas fontes.
# ATENÇÃO: gasta cota das APIs pagas por uso (Pexels, Pixabay).
python3 scripts/gb.py doctor --live

# ── status ──────────────────────────────────────────────────
# O QUE FAZ: mostra em que etapa o projeto está.
# VOCÊ RECEBE: quantos candidatos, prévias, aprovados, baixados…
#              e o PRÓXIMO COMANDO já pronto pra copiar (em "summary.do").
# QUANDO USAR: sempre que se perder. Só lê, não altera nada.
python3 scripts/gb.py status --project /caminho/meu-video
```

## 2 📝 Planejar (opcional, mas recomendado)

> Pensa como a **pré-produção**: RULES é o padrão do canal, BRIEF é o plano do vídeo (o que cada trecho precisa mostrar).
> 💬 **No chat:** `/get-brolls-brief` faz uma entrevista de até 7 perguntas e escreve o BRIEF por você.

```bash
# ── init [--client … --canvas … --fps …] ────────────────────
# O QUE FAZ: cria um projeto novo de layout 1: project.json (id, cliente,
#            quadro e fps) e as pastas aroll/, assets/<sete pastas>, broll/ e analysis/.
# --client: cliente já registrado (client --action add). Com template: veja "Clientes e templates".
# RECUSA: pasta que já tem project.json ou brolls/manifest.json (esse usa migrate).
# OPCIONAL: projetos sem init continuam funcionando como sempre.
python3 scripts/gb.py init --client acme --canvas 1080x1920 --fps 30 --project /caminho/meu-video

# ── migrate --action plan|apply [--client …] ────────────────
# O QUE FAZ: adota o layout 1 num projeto antigo (com brolls/manifest.json) só
#            acrescentando o project.json. Não move, renomeia nem apaga nada.
#            O id é o project_id do manifesto (uuid em minúsculas; outro texto vira
#            um uuid derivado dele); o cliente vem do ROTEIRO.md (ou de --client).
# plan:  só lê; mostra o project.json com o MESMO id que o apply grava (sem project_id,
#        "será gerado") e avisa de cliente não registrado e de broll/ sua com arquivos.
# apply: grava. Já migrado: sai 0 com changed false ("nada a migrar"). Recusa pasta que
#        não existe, project.json quebrado ou gravação interrompida.
# status mostra o resultado em layout {version, source, problem}.
python3 scripts/gb.py migrate --action plan --project /caminho/meu-video
python3 scripts/gb.py migrate --action apply --project /caminho/meu-video

# ── init-rules ──────────────────────────────────────────────
# O QUE FAZ: cria o arquivo RULES.md na pasta do projeto.
# DENTRO DELE: formato do vídeo, fontes preferidas e sites bloqueados.
# --format: reels (vertical 9:16) · horizontal (16:9) · native (formato original)
# QUANDO USAR: uma vez, no começo do projeto.
python3 scripts/gb.py init-rules --format reels --project /caminho/meu-video

# ── rules ───────────────────────────────────────────────────
# O QUE FAZ: mostra as regras que estão valendo agora neste projeto.
python3 scripts/gb.py rules --project /caminho/meu-video

# ── init-brief ──────────────────────────────────────────────
# O QUE FAZ: cria o BRIEF.md, o plano do vídeo dividido em beats.
# DEPOIS: abra e preencha (veja "📐 Os arquivos que você edita" lá embaixo).
python3 scripts/gb.py init-brief --project /caminho/meu-video

# ── brief ───────────────────────────────────────────────────
# O QUE FAZ: lê o BRIEF e, pra cada beat, monta o comando de busca pronto.
# VOCÊ RECEBE: os beats, o que falta cobrir e os comandos pra copiar.
python3 scripts/gb.py brief --project /caminho/meu-video

# ── brief --validate ────────────────────────────────────────
# O QUE FAZ: só confere se o BRIEF está preenchido certo.
# VOCÊ RECEBE: a lista de erros, ou nada se estiver tudo certo.
# QUANDO USAR: toda vez que editar o BRIEF na mão.
python3 scripts/gb.py brief --validate --project /caminho/meu-video

# ── brief --beat ────────────────────────────────────────────
# O QUE FAZ: mostra um beat só, com o comando pronto dele.
# O ID DO BEAT é o "id" que está no BRIEF (ex.: "abertura").
python3 scripts/gb.py brief --beat "<ID_DO_BEAT>" --project /caminho/meu-video
```

## 3 🔎 Buscar

> Aqui é o **ingest**: cada resultado entra no projeto como candidato. **Nada é baixado ainda.**
> 💡 Busque com **poucas palavras** e **no idioma do vídeo** (vídeo gringo, busca em inglês).

```bash
# ── search ──────────────────────────────────────────────────
# O QUE FAZ: pesquisa na fonte e salva os resultados como candidatos.
# --provider: youtube · nasa · commons · pexels · pixabay
# --limit: quantos resultados (padrão 8).
# VOCÊ RECEBE: uma lista ("items"). Cada item tem "id", título, canal e duração.
python3 scripts/gb.py search --provider youtube --query "Sua busca" --project /caminho/meu-video

# ── search --shot ───────────────────────────────────────────
# O QUE FAZ: igual, mas já liga os resultados a um beat do BRIEF.
# POR QUE: assim a entrega sai organizada por beat no final.
# ATENÇÃO: a fonte tem que estar em allowed_sources do beat no BRIEF;
#          fora da lista, o comando recusa e diz quais fontes valem.
python3 scripts/gb.py search --provider youtube --query "Sua busca" --shot "<ID_DO_BEAT>" --project /caminho/meu-video

# ── search --dry-run ────────────────────────────────────────
# O QUE FAZ: só espia o resultado, sem salvar nada no projeto.
# QUANDO USAR: pra testar se a busca está boa antes de valer.
python3 scripts/gb.py search --provider youtube --query "Sua busca" --dry-run --project /caminho/meu-video

# ── resolve --url ───────────────────────────────────────────
# O QUE FAZ: registra um vídeo pelo link, sem precisar buscar.
# VALE PARA: YouTube · Instagram · TikTok · Wikimedia Commons · NASA
# QUANDO USAR: quando você já sabe exatamente qual vídeo quer.
python3 scripts/gb.py resolve --url "URL_PUBLICA" --project /caminho/meu-video

# ── resolve --file ──────────────────────────────────────────
# O QUE FAZ: registra um vídeo ou imagem que já está no seu computador.
# DICA: use --creator "Autor" e --source-url "link" pra anotar a origem.
python3 scripts/gb.py resolve --file "/caminho/arquivo.mp4" --project /caminho/meu-video

# ── inspect --candidate ─────────────────────────────────────
# O QUE FAZ: raio-x do vídeo: duração, capítulos e legendas.
# QUANDO USAR: antes de escolher o trecho, pra não assistir tudo.
python3 scripts/gb.py inspect --candidate "<ID>" --project /caminho/meu-video

# ── inspect --url --query ───────────────────────────────────
# O QUE FAZ: procura pela legenda em que minuto falam do assunto.
# VOCÊ RECEBE: os trechos mais prováveis, com início e fim em segundos.
# QUANDO USAR: vídeo de 1 hora e você só precisa de 5 segundos dele.
python3 scripts/gb.py inspect --url "URL_PUBLICA" --query "O que encontrar no video" --project /caminho/meu-video

# ── browser-plan ────────────────────────────────────────────
# O QUE FAZ: planeja o print de uma página (notícia, site) pelo navegador.
# QUANDO USAR: quando o B-roll é uma manchete ou uma tela de site.
python3 scripts/gb.py browser-plan --url "URL_DA_PAGINA" --project /caminho/meu-video
```

## 4 👀 Prévia e Storyboard

> É o **offline**: você vê o trecho em movimento, leve, antes de baixar em alta.
> 💬 **No chat:** `/get-brolls-review` monta, manda o link e importa suas decisões.

```bash
# ── preview ─────────────────────────────────────────────────
# O QUE FAZ: gera o GIF do trecho escolhido (aqui, do segundo 10 ao 15).
# LIMITE: no máximo 10 segundos por prévia.
# OPCIONAIS: --narration (a fala do roteiro) e --reason (por que escolheu).
#            Os dois aparecem no Storyboard pra quem for aprovar.
# VOCÊ RECEBE: GIF, pôster e folha de quadros em brolls/previews/.
python3 scripts/gb.py preview --candidate "<ID>" --start 10 --end 15 --narration "Fala exata do roteiro" --reason "Motivo da escolha" --project /caminho/meu-video

# ── preview (foto) ──────────────────────────────────────────
# O QUE FAZ: foto da NASA, do Commons ou do seu computador não tem trecho.
#            Rode sem --start/--end: a prévia é a própria imagem, parada.
python3 scripts/gb.py preview --candidate "<ID>" --project /caminho/meu-video

# ── preview --scan ──────────────────────────────────────────
# O QUE FAZ: gera uma folha com quadros do vídeo inteiro, cada um com o tempo.
# QUANDO USAR: pra achar o in/out certo e depois gerar o GIF só dele.
python3 scripts/gb.py preview --candidate "<ID>" --scan --project /caminho/meu-video

# ── preview --reference-only ────────────────────────────────
# O QUE FAZ: gera só uma imagem parada de referência, sem baixar o vídeo.
python3 scripts/gb.py preview --candidate "<ID>" --reference-only --project /caminho/meu-video

# ── review ──────────────────────────────────────────────────
# O QUE FAZ: monta o Storyboard (brolls/review.html) com todas as prévias.
# QUANDO USAR: depois de gerar as prévias.
python3 scripts/gb.py review --project /caminho/meu-video

# ── serve --background ──────────────────────────────────────
# O QUE FAZ: abre o Storyboard no navegador.
# VOCÊ RECEBE: um link, em "urls". Normalmente http://localhost:8767/review.html;
#              se a porta estiver ocupada, vem outra. Use o link que aparecer.
# NA PÁGINA: em cada trecho → Aprovar · Pedir ajuste · Reprovar.
#            No fim → "Salvar decisões".
# ATENÇÃO: abra PELO LINK e salve ANTES de fechar a aba.
python3 scripts/gb.py serve --background --project /caminho/meu-video

# ── serve --stop ────────────────────────────────────────────
# O QUE FAZ: fecha o Storyboard.
python3 scripts/gb.py serve --stop --project /caminho/meu-video
```

## 5 ✅ Aprovar

> É a **aprovação do cliente**. Primeira trava: sem ela, nada baixa.

```bash
# ── import-review ───────────────────────────────────────────
# O QUE FAZ: traz pro projeto o que você decidiu no Storyboard.
# --by: seu nome, que fica registrado como quem aprovou.
# QUANDO USAR: logo depois de clicar em "Salvar decisões".
python3 scripts/gb.py import-review --by "Seu nome" --project /caminho/meu-video

# ── import-review --file ────────────────────────────────────
# O QUE FAZ: igual, escolhendo um arquivo de decisões específico.
# QUANDO USAR: se salvou mais de uma vez e quer uma versão anterior.
python3 scripts/gb.py import-review --file "/caminho/decisoes.json" --by "Seu nome" --project /caminho/meu-video

# ── approve ─────────────────────────────────────────────────
# O QUE FAZ: aprova um trecho sem abrir o Storyboard.
# REGISTRA: quem aprovou (--by) e a frase exata da aprovação (--statement).
# APROVAR TUDO: troque --candidate "<ID>" por --all
#               (só se você viu todas as prévias).
python3 scripts/gb.py approve --candidate "<ID>" --by "Seu nome" --channel chat --statement "Frase exata da aprovacao recebida" --project /caminho/meu-video

# ── reject ──────────────────────────────────────────────────
# O QUE FAZ: descarta o candidato e guarda o motivo.
# POR QUE O MOTIVO: daqui a meses você sabe por que aquele trecho saiu.
python3 scripts/gb.py reject --candidate "<ID>" --reason "Motivo do descarte" --project /caminho/meu-video
```

## 6 📄 Direitos de uso

> Segunda trava. Registra **de quem é o material**. **Escolha UMA das três opções.**
> ⚠️ A ferramenta só **registra**. Ela não confere licença. Quem responde pelo uso é você.

```bash
# ── Opção A: permit --evidence ──────────────────────────────
# O QUE FAZ: registra as condições que você leu na página da fonte.
# QUANDO USAR: você foi na página do vídeo e viu a licença.
python3 scripts/gb.py permit --candidate "<ID>" --evidence "Condições de uso reais dessa fonte" --project /caminho/meu-video

# ── Opção B: permit --preset ────────────────────────────────
# O QUE FAZ: usa o texto de termos padrão da fonte.
# VALORES: youtube · nasa · commons · pexels · pixabay
# ATENÇÃO: mesmo assim, confira a página do vídeo.
python3 scripts/gb.py permit --candidate "<ID>" --preset youtube --project /caminho/meu-video

# ── Opção C: permit --declared-by ───────────────────────────
# O QUE FAZ: registra que você assume a responsabilidade pelo uso.
# EXIGE: nome e sobrenome + uma frase com pelo menos 20 caracteres.
python3 scripts/gb.py permit --candidate "<ID>" --declared-by "Seu Nome" --declaration-text "Frase dizendo que você assume o uso" --project /caminho/meu-video
```

## 7 ⬇️ Baixar

```bash
# ── fetch ───────────────────────────────────────────────────
# O QUE FAZ: baixa e corta só o trecho aprovado (1080p quando a fonte tem).
# VAI PARA: broll/ (projeto criado com init, layout 1) ou brolls/clips/ (os demais)
# SE RECUSAR: falta aprovar (passo 5) ou registrar os direitos (passo 6).
python3 scripts/gb.py fetch --candidate "<ID>" --project /caminho/meu-video

# FONTE DE PLUGIN JÁ CONSUMIDA (compra ou cota de uso único): rode de novo com --reacquire.
python3 scripts/gb.py fetch --candidate "<ID>" --reacquire --project /caminho/meu-video

# ── verify ──────────────────────────────────────────────────
# O QUE FAZ: confere se cada arquivo baixado está inteiro e abre.
# COMO: compara uma "impressão digital" do arquivo (hash) e tenta decodificar.
# DEU CERTO QUANDO: o "count" bate com o número de trechos baixados.
python3 scripts/gb.py verify --project /caminho/meu-video
```

## 8 📦 Entregar

```bash
# ── deliver --dry-run ───────────────────────────────────────
# O QUE FAZ: mostra como a pasta de entrega vai ficar, sem criar nada.
python3 scripts/gb.py deliver --dry-run --project /caminho/meu-video

# ── deliver ─────────────────────────────────────────────────
# O QUE FAZ: organiza tudo em entrega/, uma pasta por beat.
# CADA PASTA TEM: o corte, a folha de quadros e o ORIGEM.md (fonte, autor, trecho e hash).
# SEM BRIEF: tudo vai para a pasta 00-sem-beat/.
# É DAQUI que você arrasta pro editor.
python3 scripts/gb.py deliver --project /caminho/meu-video
```

**Como a pasta do projeto fica no final:**

```text
meu-video/
├── RULES.md            # regras do projeto (passo 2)
├── BRIEF.md            # plano do vídeo (passo 2)
├── entrega/            # ⭐ SEUS ARQUIVOS FINAIS, uma pasta por beat
│   ├── README.md       # explica a pasta
│   └── 01-abertura/    # número do beat + id (sem BRIEF: 00-sem-beat/)
│       ├── 01-abertura.mp4
│       ├── contact-sheet.jpg
│       └── ORIGEM.md
└── brolls/             # bastidores: não precisa mexer
    ├── manifest.json   # a "ficha" de todos os candidatos
    ├── review.html     # o Storyboard
    ├── previews/       # GIFs e folhas de quadros
    ├── clips/          # cortes finais (no projeto do init, ficam em broll/)
    ├── credits.md      # créditos de tudo que foi usado
    └── getbrolls.log   # registro de cada passo (pra achar erro)
```

---

# ➕ Extras

## 🧠 Memória: a ferramenta aprende com você

```bash
# ── remember ────────────────────────────────────────────────
# O QUE FAZ: guarda um trecho que funcionou (approved) ou não (rejected).
# VALE PARA: este projeto.
python3 scripts/gb.py remember --candidate "<ID>" --decision approved --reason "Motivo real da aprovacao" --by "Seu nome" --project /caminho/meu-video
python3 scripts/gb.py remember --candidate "<ID>" --decision rejected --reason "Motivo real do descarte" --by "Seu nome" --project /caminho/meu-video

# ── references ──────────────────────────────────────────────
# O QUE FAZ: mostra tudo que foi guardado com remember neste projeto.
python3 scripts/gb.py references --project /caminho/meu-video

# ── learn ───────────────────────────────────────────────────
# O QUE FAZ: guarda aprendizados que valem pra TODOS os seus projetos.
# --outcome hit (a busca rendeu) ou miss (não rendeu)
python3 scripts/gb.py learn --query "Busca realizada" --provider youtube --outcome hit --project /caminho/meu-video
# Guarda um gosto seu (ex.: "prefiro imagens sem texto na tela")
python3 scripts/gb.py learn --preference "Preferencia editorial informada" --by "Seu nome" --project /caminho/meu-video
# Guarda uma anotação livre
python3 scripts/gb.py learn --note "Aprendizado desta coleta" --project /caminho/meu-video

# ── library ─────────────────────────────────────────────────
# O QUE FAZ: procura no que você já guardou (buscas, gostos, trechos).
# QUANDO USAR: antes de começar a buscar num projeto novo.
python3 scripts/gb.py library --search "Termo para consultar" --project /caminho/meu-video
```

## 📥 Fila: vários Reels/TikToks sem tomar bloqueio

> Baixar 50 Reels de uma vez é o jeito mais rápido de tomar bloqueio. A fila espaça os downloads.
> Padrão: Instagram a cada 45–120 s, TikTok/YouTube a cada 15–40 s, no máximo 20 por hora e 60 por dia.

```bash
# ── queue add ───────────────────────────────────────────────
# O QUE FAZ: coloca um ou mais links na fila. Link repetido é ignorado.
# --provider: instagram · tiktok · youtube
python3 scripts/gb.py queue --action add --provider instagram --url "URL_DO_REEL" --project /caminho/meu-video

# ── queue next ──────────────────────────────────────────────
# O QUE FAZ: entrega o próximo link, se já puder.
# VOCÊ RECEBE: {"item": {...}} → pode baixar esse agora
#          ou: {"item": null, "wait_seconds": 83} → espere 83 s e peça de novo
python3 scripts/gb.py queue --action next --provider instagram --project /caminho/meu-video

# ── queue mark ──────────────────────────────────────────────
# O QUE FAZ: diz à fila como foi o item que você pegou no next.
# ✅ Baixou:
python3 scripts/gb.py queue --action mark --id "<ID_DA_FILA>" --done --project /caminho/meu-video
# ❌ Falhou (se o motivo tiver 403, 429 ou "login", a fila pausa de 30 min até 4 h):
python3 scripts/gb.py queue --action mark --id "<ID_DA_FILA>" --failed --reason "Motivo da falha" --project /caminho/meu-video
# ⏭️ Pulou:
python3 scripts/gb.py queue --action mark --id "<ID_DA_FILA>" --skipped --reason "Motivo para pular" --project /caminho/meu-video

# ── queue status ────────────────────────────────────────────
# O QUE FAZ: mostra quantos faltam, quantos foram e se está em pausa.
python3 scripts/gb.py queue --action status --project /caminho/meu-video
```

## 🎬 Roteiro e componentes (opcional)

> Para quem quer o conteúdo pronto (reels 9:16): o `ROTEIRO.md` descreve o vídeo por cenas, e os beats do BRIEF nascem dele. Só vale o `ROTEIRO.md` criado pelo `roteiro --action new` (frontmatter `type: roteiro`); um roteiro seu, escrito à parte, não muda nada. Detalhes em [GUIDE.md](GUIDE.md#roteiro-e-componentes).

```bash
# ── roteiro --action new ────────────────────────────────────
# O QUE FAZ: cria o ROTEIRO.md com o esqueleto do gênero e as pastas aroll/ e assets/.
# --force recomeça do esqueleto e guarda o anterior em ROTEIRO.md.bak.
python3 scripts/gb.py roteiro --action new --genero reels --tema "Seu tema" --project /caminho/meu-video

# ── roteiro --action check ──────────────────────────────────
# O QUE FAZ: valida o roteiro e mostra o plano de cena (componentes, duração). Só lê.
python3 scripts/gb.py roteiro --action check --project /caminho/meu-video

# ── roteiro --action review ─────────────────────────────────
# O QUE FAZ: registra que VOCÊ revisou o texto atual. Mudou o conteúdo? Revise de novo.
# --expect é o review.sha256 que o check mostrou: se o arquivo mudou desde então, recusa.
python3 scripts/gb.py roteiro --action review --by "Seu Nome" --channel chat --statement "pode seguir" --expect <sha256> --project /caminho/meu-video

# ── roteiro --action plan / sync ────────────────────────────
# O QUE FAZ: plan mostra o que o sync mudaria no BRIEF.md, sem gravar;
#            sync grava os ids de cena e os beats cNN (só com revisão válida).
# ATENÇÃO: alvo novo num beat já aprovado pede --confirm-target-change.
python3 scripts/gb.py roteiro --action plan --project /caminho/meu-video
python3 scripts/gb.py roteiro --action sync --project /caminho/meu-video

# ── assets --action list / where ────────────────────────────
# O QUE FAZ: mostra os componentes (marca, lettering, sfx, musica, imagem, composicoes)
#            e onde cada nome resolve: projeto, pasta do cliente ou biblioteca pessoal. Só lê.
python3 scripts/gb.py assets --action list --project /caminho/meu-video
python3 scripts/gb.py assets --action where --kind sfx --name whoosh --project /caminho/meu-video

# ── analysis --action register / list / check ───────────────
# O QUE FAZ: register dá a uma mídia do projeto um id por conteúdo e grava
#            analysis/media/<id>/media.json (duração, quadro, codecs pelo ffprobe);
#            o hash só roda de novo quando tamanho ou mtime mudam. list e check só leem;
#            check sai com código 1 quando acha problema (pasta órfã, JSON inválido, link).
# --role só quando a pasta não diz o papel (aroll/, broll/, assets/musica/, assets/sfx/).
python3 scripts/gb.py analysis --action register --path aroll/c01.mp4 --project /caminho/meu-video
python3 scripts/gb.py analysis --action check --project /caminho/meu-video

# ── export --to … [--dry-run] (experimental) ────────────────
# O QUE FAZ: com o roteiro revisado e sincronizado, monta um projeto de edição numa
#            pasta nova exports/<exporter>/001/, 002/… (LATEST guarda a mais nova).
#            O exporter vem de um plugin habilitado; nenhuma pasta de export é apagada.
# --dry-run mostra a pasta, os arquivos e a mídia, sem gravar nada.
# Passo a passo: references/roteiro.md#export-do-roteiro-ao-projeto-de-edição
python3 scripts/gb.py export --to hyperframes --dry-run --project /caminho/meu-video
python3 scripts/gb.py export --to hyperframes --project /caminho/meu-video
```

## 🏢 Clientes e templates (opcional)

> Para quem produz para clientes: a marca e os sons do cliente ficam numa pasta dele, e um projeto bom vira template para os próximos. Licenças e aprovações nunca vão junto. Detalhes em [GUIDE.md](GUIDE.md#clientes-e-templates).

```bash
# ── client --action add ─────────────────────────────────────
# O QUE FAZ: cria <pasta>/acme/ com client.json, components/ e templates/ e registra o cliente.
python3 scripts/gb.py client --action add --slug acme --root /caminho/Clientes

# ── template --action freeze ────────────────────────────────
# O QUE FAZ: congela o ROTEIRO.md (sem a fala) e os componentes citados na próxima
#            versão imutável: cat:getbrolls/template/reels-acme@1, @2…
python3 scripts/gb.py template --action freeze --from /caminho/meu-video --slug reels-acme

# ── template --action list / show ───────────────────────────
# O QUE FAZ: list mostra as versões; show confere o sha256 de cada arquivo (intact). Só leem.
python3 scripts/gb.py template --action list
python3 scripts/gb.py template --action show --ref cat:getbrolls/template/reels-acme@1 --client acme

# ── init --template ─────────────────────────────────────────
# O QUE FAZ: cria o projeto novo do template íntegro: project.json, ROTEIRO.md em rascunho,
#            componentes e template.lock.json. O que o cliente já tem fica na pasta dele;
#            o resto é copiado sem licença (aviso LICENCE_NOT_TRANSFERRED).
# DEPOIS: status e export avisam (TEMPLATE_LOCK_DRIFT) se um componente mudar.
python3 scripts/gb.py init --template cat:getbrolls/template/reels-acme@1 --client acme --tema "Seu tema" --project /caminho/video-novo
```

## 🧩 Plugins do SDK (experimental)

> Plugins acrescentam fontes, rotas de download e comandos próprios, instalados em `~/.getbrolls/plugins`. Não é o plugin do Claude Code. Experimental: o contrato (`sdk_api` 1) pode mudar em versão minor. Detalhes em [SDK.md](SDK.md).

```bash
# ── plugins --action list ───────────────────────────────────
# O QUE FAZ: lista os plugins instalados, com status e o que cada um traz.
# STATUS: disabled · enabled · suspended · invalid · incompatible
# ATENÇÃO: não roda código do plugin; o resultado real do carregamento sai no doctor.
python3 scripts/gb.py plugins --action list

# ── plugins --action install ────────────────────────────────
# O QUE FAZ: traz um plugin de uma pasta ou de um repositório git.
# EM DOIS PASSOS: sem --yes só mostra manifesto, permissões e o sha256;
#                 depois de revisar, repita com --yes --expect <sha256 da prévia>.
python3 scripts/gb.py plugins --action install --source "<pasta-ou-url-git>"
python3 scripts/gb.py plugins --action install --source "<pasta-ou-url-git>" --yes --expect "<sha256>"

# ── plugins --action update ─────────────────────────────────
# O QUE FAZ: compara com a origem gravada no install e traz a versão nova.
# CONFIRMA IGUAL AO INSTALL: --yes --expect <sha256 da prévia>.
python3 scripts/gb.py plugins --action update --id "<id>"

# ── plugins --action enable / disable ───────────────────────
# O QUE FAZ: liga ou desliga um plugin que já está na pasta.
# ENABLE EM DOIS PASSOS: sem --yes só mostra o manifesto; com --yes liga.
# PLUGIN SUSPENSO (a pasta mudou): a prévia mostra o que mudou e pede --yes --expect <sha256>.
python3 scripts/gb.py plugins --action enable --id "<id>"
python3 scripts/gb.py plugins --action disable --id "<id>"

# ── plugins --action new / check ────────────────────────────
# O QUE FAZ: new cria um plugin mínimo (provider, route, command ou exporter) que já passa no teste;
#            check valida uma pasta de plugin.
# ATENÇÃO: check EXECUTA o código da pasta. Use só em plugin que você escreveu ou revisou.
python3 scripts/gb.py plugins --action new --id meu_banco --kind route --path "<pasta>"
python3 scripts/gb.py plugins --action check --path "<pasta>/meu_banco"

# ── x ───────────────────────────────────────────────────────
# O QUE FAZ: roda um comando próprio de um plugin habilitado. Só lê o projeto.
# x --list mostra quais comandos existem.
python3 scripts/gb.py x --list
python3 scripts/gb.py x pasta_local recentes --arg limite=5 --project /caminho/meu-video
```

## ❓ Ajuda

```bash
# Lista todos os comandos (no pacote instalado: getbrolls --help, getbrolls --version)
python3 scripts/gb.py --help

# Mostra a versão, o Python e de onde vêm os dados: getbrolls 2.6.0 (Python 3.12.4; dados: checkout)
python3 scripts/gb.py --version

# Mostra o getbrolls.toml em vigor: cada campo com o valor e a origem
python3 scripts/gb.py profile show

# Mostra tudo o que um comando aceita (troque "search" por qualquer um)
python3 scripts/gb.py search --help

# Descreve em JSON todos os comandos, flags, o que só lê, códigos de saída e comandos de plugin
# (gerado do próprio parser; não precisa de projeto e não grava nada). --json é aceito.
python3 scripts/gb.py capabilities --json
```

O `capabilities` serve a agentes e scripts que precisam descobrir o que esta instalação sabe fazer sem ler o `--help` de cada comando: cada comando traz `summary`, `options`, `requires_project` e `read_only` (`true`, `false` ou `"by_action"`); a resposta também traz `invocation` (como chamar esta instalação, sem caminho da máquina), `exit_codes`, `error_codes`, `plugin_commands` (só plugins habilitados, lidos do manifesto) `plugins_problems` (plugins inválidos, com falha ou suspensos, com o motivo) e `marketplaces` (marketplaces de plugins fixados: nome, commit do índice, quantos plugins, `allowed`, `official` — se é o índice oficial — e `problem`, sem caminho da máquina).

---

# 🧩 Como ler a resposta dos comandos

Todo comando responde em **JSON**. Pensa como o **relatório de render**: um texto organizado que diz o que aconteceu.

```json
{
  "summary": {
    "line": "Coletei o corte final de nasa:abc em clips/abc.mp4."
  }
}
```

**Três regras que valem pra todos os comandos:**

- 🟢 **`summary.line`** é sempre uma frase em português dizendo o que acabou de acontecer. **Se for ler uma coisa só, leia essa.**
- 🧭 No **`status`**, o **`summary.do`** traz o próximo passo:
  - `command`: o comando pronto pra copiar.
  - `why`: por que é esse o próximo passo.
  - `blocking_human`: `true` quando o próximo passo depende de **você** (aprovar, dar uma informação).
- 🔴 **Deu erro?** A mensagem de erro sai em JSON em stderr, numa linha só, com `error` (o que houve) e `error_code` (o tipo do erro). Nunca sai traceback: os detalhes técnicos ficam em `brolls/diagnostics.jsonl` (ou em `~/.getbrolls/diagnostics.jsonl`, quando o comando não tem projeto).
- 💡 **`hint`** só aparece no erro quando há o que fazer além de corrigir e repetir: gravação pendente (`recovery_pending: true`, o próximo comando a retoma) ou estado já gravado (`state_committed: true`, rode `review` para regenerar a página).
- ⌨️ **Comando ou flag digitado errado** (erro de uso, código `2`): o erro vai para stderr; com stderr fora do terminal (agente, script, `2>` para arquivo) sai um JSON com `error`, `error_code: "USAGE_ERROR"`, `usage`, `prog` e `suggestion` (o nome parecido, ou `null`); com stderr no terminal, sai o texto do `usage` e, quando há nome parecido, "Você quis dizer: search?". Vale também para opção obrigatória digitada errado (`--projct` → `--project`).

**Código de saída** (o número que o terminal guarda depois de cada comando, útil em scripts):

| Código | Significa |
|---|---|
| `0` | Deu certo |
| `1` | Erro de operação ou de dados: um ID errado, falta aprovação, link fora do ar. Outro comando usando o mesmo projeto (ou outro `setup` na mesma pasta do runtime) também sai `1`, com `error_code: "LOCKED"`: é só esperar e repetir. `setup --upgrade ytdlp` cuja atualização falhou, com a venv refeita na versão fixada, sai `1` |
| `2` | Erro de uso: comando, flag ou configuração (`--env-file` que não existe, por exemplo) |
| `3` | Erro interno (bug). Abra uma issue com o `brolls/diagnostics.jsonl` |
| `4` | Falta um pré-requisito da instalação: arquivos de dados, ffmpeg, yt-dlp |
| `130` | Interrompido (Ctrl+C) |

> `serve` em primeiro plano é a exceção do Ctrl+C: é assim que se para o servidor, e ele sai com `0`.

> Até a 2.5, erro de operação ou de dados também saía com `2`. Script que testava `$? -eq 2` para esses erros agora testa `1`.

O contrato completo — o que vai para stdout e stderr, todos os `error_code`, os avisos (`warnings`), o que só lê, `--yes`/`--expect` e a política de deprecação — está em [CLI_CONTRACT.md](CLI_CONTRACT.md). Os formatos dos arquivos que a ferramenta grava (manifesto, `project.json`, `analysis/`, templates) estão em [SCHEMAS.md](SCHEMAS.md).

---

# 📐 Os arquivos que você edita

Dois arquivos na pasta do projeto guardam as suas escolhas. Os dois são Markdown com **um bloco JSON dentro**. **Edite só o bloco JSON.**

## BRIEF.md: o plano de B-rolls deste vídeo

Criado pelo `init-brief` ou pela entrevista do `/get-brolls-brief`.

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
| `stock_allowed` | `true` libera Pexels/Pixabay. Padrão: `false` (fonte real primeiro) |
| `defaults` | O que vale pra todo beat: fontes, `intent`, duração sugerida |
| `intent` | `literal` (a coisa exata citada) ou `illustrative` (ideia genérica) |
| `beats[].id` | Nome curto do beat: letras minúsculas, números e hífen. Ex.: `abertura` |
| `beats[].narration` | A fala do trecho, ou `null` |
| `beats[].target` | **Obrigatório.** O que precisa aparecer na tela |
| `beats[].queries` | Buscas que já funcionaram. A primeira vira a busca sugerida |
| `beats[].blocked_reason` | Preencha se o beat depende de algo que só você tem (um arquivo, um link). A ferramenta não busca esse beat até você apagar |

Depois de editar, rode `brief --validate`.

## RULES.md: o padrão do seu canal

Criado pelo `init-rules`. Vale pra todo o projeto.

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
| `preferred_providers` | A ordem das fontes pra busca literal e pra busca ilustrativa |
| `blocked_domains` | Sites que nunca podem entrar. Ex.: `["exemplo.com"]` |
| `copyright.mode` | `per_item_evidence` ou `user_declaration` |
| `responsible_person` / `declaration` | Seu nome e a frase, se usar `user_declaration` |

> 💡 **Quer uma regra pra todos os projetos?** Crie o arquivo `~/.getbrolls/RULES.md` (`~` é a sua pasta de usuário). A ordem é: global → arquivo em `GB_RULES_FILE` → RULES do projeto. O último vence.

## .env: configurações da ferramenta

Arquivo opcional. Copie o [`.env.example.pt-BR`](../.env.example.pt-BR) pra `.env` e mude só o que quiser. A lista completa, com faixas e padrões, está nesse arquivo.

**Qual `.env` vale** (só um é lido, nunca se misturam), do mais forte ao mais fraco:

1. `--env-file CAMINHO` (vem antes do subcomando);
2. a variável `GB_ENV_FILE`, só no ambiente do processo;
3. o `.env` da pasta do checkout ou do plugin;
4. `$GB_HOME/.env` (por padrão, `~/.getbrolls/.env`): é o lugar certo no pacote instalado.

- `--env-file` ou `GB_ENV_FILE` apontando para arquivo que não existe é erro de uso (código `2`).
- Com dois `.env` possíveis (o do checkout e o de `$GB_HOME`), vale o de cima e todo comando avisa `ENV_FILE_SHADOWED`; o `doctor` mostra qual valeu em `install.env_file`.
- `GB_HOME` dentro de `$GB_HOME/.env` é recusado, também quando esse arquivo chega por `--env-file` ou `GB_ENV_FILE`. Em qualquer outro `.env` ainda funciona, com o aviso `DEPRECATED` quando a linha vale (a leitura sai na 2.7): defina `GB_HOME` no ambiente.
- Qualquer variável `GB_*` do core também vale com o prefixo `GETBROLLS_*` no ambiente (`GETBROLLS_LOG_LEVEL` = `GB_LOG_LEVEL`). O nome `GB_*` é o canônico e vence quando os dois existem.

```bash
# Chaves dos bancos de imagem (grátis nos sites deles)
PEXELS_API_KEY=sua_chave
PIXABAY_API_KEY=sua_chave

# Prévia: gif (padrão) ou static (só imagens paradas)
GB_PREVIEW_MODE=gif

# Largura do GIF em pixels (160 a 720; padrão 360). Aumente pra ver texto pequeno
GB_GIF_WIDTH=480

# Duração máxima de uma prévia em segundos (1 a 30; padrão 10)
GB_PREVIEW_MAX_SECONDS=10

# Entrega com cópias independentes. Sem isso, o arquivo em entrega/ é o MESMO
# da pasta de clipes finais, broll/ ou brolls/clips/ (não ocupa disco a mais), e vem como somente leitura
GB_DELIVERY_COPY=1

# Quanto detalhe vai pro brolls/getbrolls.log: DEBUG, INFO (padrão), WARNING, ERROR, off
GB_LOG_LEVEL=INFO
```

---

# 🤖 Automação: fazer a ferramenta trabalhar sozinha

Como toda resposta é JSON, dá pra **encadear comandos num script**. As receitas abaixo são para o terminal do Mac ou do Linux (bash). Pensa como uma **action do Photoshop** ou um **preset de export em lote**: você monta uma vez e roda quando quiser.

**Você vai precisar do `jq`**, um programinha que lê JSON no terminal:

```bash
# Mac
brew install jq
```

## Receita 1: pegar o ID sem copiar na mão

```bash
# O QUE FAZ: busca e guarda o ID do primeiro resultado numa variável.
# "$( ... )" roda o comando e guarda a resposta.
# jq -r '.items[0].id' pega o "id" do primeiro item da lista.
ID=$(python3 scripts/gb.py search --provider nasa --query "Artemis launch" --limit 1 --project /caminho/meu-video | jq -r '.items[0].id')

# Confere o que veio
echo "$ID"

# Agora usa a variável no lugar de <ID>
python3 scripts/gb.py preview --candidate "$ID" --start 0 --end 4 --project /caminho/meu-video
```

## Receita 2: perguntar "qual o próximo passo?"

```bash
# O QUE FAZ: mostra a frase do status e o comando do próximo passo.
python3 scripts/gb.py status --project /caminho/meu-video | jq -r '.summary.line, .summary.do.command'
```

## Receita 3: um script que busca todos os beats de uma vez

Crie um arquivo `buscar_beats.sh` com isto:

```bash
#!/bin/bash
# O QUE FAZ: pra cada beat da lista, busca 5 candidatos e liga ao beat.
# COMO USAR: troque a pasta do projeto e a lista de beats/buscas.
PROJETO="/caminho/meu-video"

python3 scripts/gb.py search --provider youtube --query "rocket launch" --limit 5 --shot "abertura" --project "$PROJETO"
python3 scripts/gb.py search --provider nasa --query "Artemis crew" --limit 5 --shot "tripulacao" --project "$PROJETO"
python3 scripts/gb.py search --provider commons --query "Moon surface" --limit 5 --shot "lua" --project "$PROJETO"

# No fim, mostra onde o projeto parou
python3 scripts/gb.py status --project "$PROJETO" | jq -r '.summary.line'
```

E rode:

```bash
bash buscar_beats.sh
```

## Receita 4: parar o script se algo der errado

```bash
# O QUE FAZ: "set -e" faz o script parar no primeiro comando que falhar
#            (código de saída diferente de 0), em vez de seguir no escuro.
set -e
python3 scripts/gb.py fetch --candidate "$ID" --project /caminho/meu-video
python3 scripts/gb.py verify --project /caminho/meu-video
python3 scripts/gb.py deliver --project /caminho/meu-video
echo "Entrega pronta"
```

> ⚠️ **O que NUNCA automatizar:** a aprovação e os direitos. `approve` e `permit` registram uma **decisão sua**. Um script que aprova tudo sozinho derruba justamente a trava que protege o seu vídeo.

---

# 🆘 Deu erro?

| Aconteceu | Faça |
|---|---|
| `python3` não existe | No Windows, use `python` |
| `jq` não existe | `brew install jq` (Mac) |
| Falta alguma ferramenta | Rode `doctor` |
| `fetch` recusou | Faltou aprovar (passo 5) ou os direitos (passo 6) |
| Busca vazia | Menos palavras, no idioma do vídeo |
| "Trecho grande demais" | Prévia aceita no máximo 10 s |
| Salvei no Storyboard e nada | Rode `import-review` |
| Não sei onde parei | Rode `status` |
| Erro estranho | Olhe as últimas linhas do `brolls/getbrolls.log` |

---

Referência completa: [GUIDE.md](GUIDE.md) · Formato do brief: [BRIEF.md](BRIEF.md) · Regras: [RULES.md](RULES.md) · Contrato da CLI: [CLI_CONTRACT.md](CLI_CONTRACT.md) · Formatos de arquivo: [SCHEMAS.md](SCHEMAS.md) · Código da CLI: [`scripts/getbrolls/cli.py`](../scripts/getbrolls/cli.py)
