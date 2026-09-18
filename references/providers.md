---
type: reference
status: current
created: 2026-09-17
updated: 2026-09-17
tags: [get-brolls, fontes, provedores]
---

# Fontes — qual tentar, nessa ordem

**Literal primeiro.** Procure footage, print ou imagem real do fato, da pessoa, do produto, da notícia ou da tela que a narração cita. Banco genérico não substitui fonte literal: Pexels e Pixabay entram **somente quando o usuário pedir stock explicitamente**, nunca para fechar a conta de um beat que ficou sem fonte.

> **Caminhos.** Os exemplos escrevem `scripts/gb.py` por brevidade. Rode sempre pelo **caminho absoluto da instalação da skill** (no plugin, `${CLAUDE_PLUGIN_ROOT}/scripts/gb.py`) e passe `--project` com a pasta absoluta do usuário em todo comando. No Windows, use `python` no lugar de `python3`.

Antes de sair buscando, consulte o que já se sabe:

```sh
python3 scripts/gb.py rules --project <projeto>
python3 scripts/gb.py references --project <projeto>
python3 scripts/gb.py library --search "termo" --project <projeto>
```

A biblioteca pessoal (`~/.getbrolls/library`, desligável com `GB_LIBRARY=off`) lembra que busca rendeu, que fonte falhou e que trecho já serviu. Ela é ponteiro editorial e nada além disso: `rights_not_transferable` vem em toda resposta e nenhuma pista dela aprova nem permite nada. A biblioteca mistura projetos — não cole o texto dela na conversa com um cliente. As pistas anexadas ao `search` (`library_hints[]`) já vêm sem o motivo escrito noutro projeto; o texto completo só aparece quando você pede `library --search` de propósito.

As regras valem em camadas: `~/.getbrolls/RULES.md` → `GB_RULES_FILE` → `RULES.md` do projeto. `rules` mostra em `sources` de onde veio cada campo. Responsabilidade e declaração **nunca** vêm da camada global.

Depois de uma decisão útil, guarde-a:

```sh
python3 scripts/gb.py learn --from-candidate <ID> --project <projeto>
python3 scripts/gb.py learn --query "..." --provider <fonte> --outcome hit --project <projeto>
python3 scripts/gb.py learn --preference "frase da pessoa" --project <projeto>
```

## YouTube

Sem API key — usa yt-dlp.

```sh
python3 scripts/gb.py search --provider youtube --query "entidade ação" --intent literal --project <projeto>
python3 scripts/gb.py resolve --url <URL> --shot <beat.id> --project <projeto>
```

Detalhe operacional na seção [YouTube](../docs/GUIDE.md#provedor--youtube) do guia.

`--shot <beat.id>` também funciona no `search`: ele liga cada candidato ao beat do
BRIEF.md na hora, sem precisar re-registrar por URL depois. E `--dry-run` lista o que
a fonte devolveu **sem gravar nada** no projeto — use-o para sondar uma query antes de
sujar as contagens do `status` com material que você não vai usar.

## Instagram

Procedimento próprio, com navegador e dois streams. Está inteiro em [`references/instagram.md`](instagram.md) — leia antes de tocar em qualquer Reel.

## TikTok

Descubra a URL completa pelo navegador e registre com `resolve --url`. Veja [TikTok](../docs/GUIDE.md#provedor--tiktok).

## Bancos genéricos (só sob pedido)

```sh
python3 scripts/gb.py search --provider pexels --query "..." --project <projeto>
python3 scripts/gb.py search --provider pixabay --query "..." --project <projeto>
```

Chaves de Pexels/Pixabay são opcionais e ficam no ambiente ou num `.env` apontado com `--env-file`. Veja [Bancos](../docs/GUIDE.md#bancos--busca-prévia-e-coleta).

## Domínio público e arquivo

Wikimedia Commons e NASA não pedem chave e costumam ser a rota literal mais rápida para fato histórico, espaço e ciência: `--provider commons` ou `--provider nasa`. Quando você já tem o link do item no acervo da NASA, `resolve --url https://images.nasa.gov/details/<id>` registra o item direto — vídeo ou imagem estática. Arquivo local entra com `resolve --file --source-url --creator --shot`.

Panorama completo em [Fontes e transportes](../docs/GUIDE.md#fontes-e-transportes).

## Quantidade

Insert isolado não exige roteiro completo. Para roteiro completo, o padrão editorial é buscar 8+ clipes literais quando o conteúdo comportar; prefira pessoas, produtos e fatos nomeados, e 1080p quando disponível. Não preencha com stock genérico para atingir uma contagem.

## Sem pista para escolher o intervalo

Quando `inspect` não devolve janela nenhuma — vídeo sem legenda, sem capítulo e sem tempo escrito na descrição —, varra o vídeo inteiro num contact sheet de baixa resolução:

```sh
python3 scripts/gb.py preview --scan --candidate <ID> --project <projeto>
```

O `--scan` não define intervalo: ele só mostra o vídeo todo, um quadro a cada N segundos (N = duração/12), com teto de `GB_SCAN_MAX_SECONDS` (padrão 900 s). **Ele baixa mídia de trabalho** — até esse teto — e por isso pode levar minutos num vídeo longo; rode em segundo plano se o seu shell tiver limite de tempo. A resposta traz `scan.downloaded_seconds`, `scan.start_s`/`scan.end_s` e um `note` dizendo que trecho da fonte entrou na grade — os rótulos de `frame_times_s` são tempo da fonte, mesmo quando a mídia de trabalho começa depois do zero. **Prefira `inspect --query` primeiro:** ele responde de graça, sem baixar nada, e só quando não sobrar pista é que a varredura (que baixa o vídeo inteiro até o teto) compensa. A varredura ignora o intervalo já escolhido no candidato: ela é exploratória e não define nem invalida segmento. Escolha o `--start/--end` olhando o resultado.

Quando `inspect` não encontra nada que case com a frase, ele ainda devolve janelas com
`score: 0` e a `source` que as gerou (capítulo, legenda ou espaçamento pelo relógio):
são ponto de partida para confirmar no contact sheet, nunca resposta pronta.

Quando o pedido é uma referência estática — um print, uma capa, um quadro só —, `preview --reference-only` gera apenas essa referência, sem GIF.

## Onde ficam os arquivos da prévia

Toda resposta de `preview` — corte normal, `--reference-only`, `--scan` e imagem estática — traz `files`, com o **caminho absoluto** de `contact_sheet`, `poster`, `gif`, `review` e, na varredura, `scan`. É esse caminho que você abre para olhar.

No `status` e dentro do `manifest.json` o mesmo arquivo aparece como `preview.contact_sheet_path`, e ali ele é **relativo a `brolls/`** (`previews/<id>.jpg`). Os dois falam do mesmo arquivo; o que muda é a forma. Para montar o caminho absoluto a partir do manifesto, junte `<projeto>/brolls/` na frente.

## Um `preview` por chamada

Não encadeie várias prévias numa chamada só de shell. Cada `preview` baixa mídia de trabalho e chama o FFmpeg; três ou quatro em sequência passam do teto de tempo da ferramenta, e a chamada morre no meio — com arquivos pela metade e nenhum resumo. Rode um `preview` por chamada, leia o `files.contact_sheet` daquele item, e só então peça o próximo. Vale o mesmo para `preview --scan`, que sozinho já pode levar minutos.

## Print de tela (UI)

Quando o beat pede a tela real de um produto, de um painel ou de um site — não um
vídeo dele —, a rota é capturar a página e registrar o arquivo como candidato:

```sh
python3 scripts/gb.py browser-plan --url <URL_PUBLICA> --project <projeto>
# execute os passos devolvidos (open / resize / snapshot / screenshot) e então:
python3 scripts/gb.py resolve --file <CAMINHO_DO_PNG> --source-url <URL_PUBLICA> \
  --asset-type web_screenshot --shot <beat.id> --project <projeto>
python3 scripts/gb.py preview --reference-only --candidate <ID> --project <projeto>
```

`--source-url` é obrigatório para print: sem ele a origem se perde e `resolve` recusa.
Capture a página de verdade, com o navegador: não descreva de memória nem reconstrua a
interface. O tamanho de viewport que o `browser-plan` sugere é **dica**, não regra —
se a tela que interessa só aparece em outra largura, use a que mostra o que a narração
cita e diga isso na revisão.

`--reference-only` vale também para vídeo cuja fonte não libera o trecho: rode-o
**sozinho**, sem `--start/--end`, e a skill gera só o cartaz estático (miniatura da
fonte ou primeiro quadro), o bastante para a pessoa decidir.

**Aviso de cookies antes de capturar.** Muita página abre com uma faixa de consentimento
por cima justamente do que a narração cita, e o print sai com o banner tapando a tela. Feche
a faixa com os helpers do navegador antes do `screenshot` — clique na opção **mais
preservadora de privacidade** que a página oferecer ("Rejeitar tudo", "Somente essenciais",
"Continuar sem aceitar"), nunca em "Aceitar tudo", e não aceite termos em nome do usuário.
Nada disso é automático: não existe código na skill que dispense banner sozinho, é uma
interação do navegador, feita à vista, antes da captura. Se a faixa não fechar, diga isso na
revisão em vez de entregar um print tapado.

**Tela de um aplicativo de desktop.** Quando o beat pede a interface de um programa que roda
na máquina — um editor, uma IDE, um terminal, um painel instalado —, não existe URL para o
`browser-plan` abrir. Duas rotas, nessa ordem: (1) a tela do próprio usuário — peça a ele o
screenshot do seu computador e registre o arquivo com `resolve --file --source-url` (a
`--source-url` aqui é a página oficial do produto) e `--asset-type web_screenshot`; ou (2) uma
captura pública da interface — um tutorial ou demonstração no YouTube que mostre a mesma tela,
achado com `search --provider youtube`, localizado com `inspect --query` e confirmado com
`preview`. A rota (1) mostra a tela real dele e é sempre a melhor quando ele pode mandar; a
rota (2) é material de terceiro e passa pelo `permit` como qualquer outro. O que não vale é
recriar a interface de memória.

## Quando não há fonte

Reporte o que você tentou, com a fonte real do erro, e pergunte ao usuário se ele tem material próprio ou um link. Falha de extração é reportada com o motivo real: não invente indisponibilidade permanente nem troque de arquitetura por conta própria.
