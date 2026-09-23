---
type: documentation
status: current
created: 2026-09-23
updated: 2026-09-23
tags: [get-brolls, manual, tutorial, commands]
---

# 🎬 Get B-rolls: manual + tutorial

Versão 2.5.0. Feito pra **videomaker que está começando a mexer com código**.

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

> Pensa como a **pré-produção**: RULES é o padrão do canal, BRIEF é o roteiro deste vídeo.
> 💬 **No chat:** `/get-brolls-brief` faz uma entrevista de até 7 perguntas e escreve o BRIEF por você.

```bash
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
# VAI PARA: brolls/clips/
# SE RECUSAR: falta aprovar (passo 5) ou registrar os direitos (passo 6).
python3 scripts/gb.py fetch --candidate "<ID>" --project /caminho/meu-video

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
    ├── clips/          # cortes finais
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

## ❓ Ajuda

```bash
# Lista todos os comandos
python3 scripts/gb.py --help

# Mostra a versão instalada
python3 scripts/gb.py --version

# Mostra tudo o que um comando aceita (troque "search" por qualquer um)
python3 scripts/gb.py search --help
```

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
- 🔴 **Deu erro?** A mensagem sai na última linha, também em JSON, com `error` (o que houve) e `error_code` (o tipo do erro).

**Código de saída** (o número que o terminal guarda depois de cada comando, útil em scripts):

| Código | Significa |
|---|---|
| `0` | Deu certo |
| `2` | Erro de uso ou de dados: um ID errado, falta aprovação, link fora do ar |
| `3` | Erro interno (bug). Abra uma issue com o `brolls/diagnostics.jsonl` |

---

# 📐 Os arquivos que você edita

Dois arquivos na pasta do projeto guardam as suas escolhas. Os dois são Markdown com **um bloco JSON dentro**. **Edite só o bloco JSON.**

## BRIEF.md: o roteiro de B-rolls deste vídeo

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

Arquivo opcional na pasta da skill. Copie o [`.env.example.pt-BR`](../.env.example.pt-BR) pra `.env` e mude só o que quiser. A lista completa, com faixas e padrões, está nesse arquivo.

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
# de brolls/clips/ (não ocupa disco a mais) e vem como somente leitura
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

Referência completa: [GUIDE.md](GUIDE.md) · Formato do brief: [BRIEF.md](BRIEF.md) · Regras: [RULES.md](RULES.md) · Código da CLI: [`scripts/getbrolls/cli.py`](../scripts/getbrolls/cli.py)
