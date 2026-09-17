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

Wikimedia Commons e NASA não pedem chave e costumam ser a rota literal mais rápida para fato histórico, espaço e ciência: `--provider commons` ou `--provider nasa`. Arquivo local entra com `resolve --file --source-url --creator --shot`.

Panorama completo em [Fontes e transportes](../docs/GUIDE.md#fontes-e-transportes).

## Quantidade

Insert isolado não exige roteiro completo. Para roteiro completo, o padrão editorial é buscar 8+ clipes literais quando o conteúdo comportar; prefira pessoas, produtos e fatos nomeados, e 1080p quando disponível. Não preencha com stock genérico para atingir uma contagem.

## Sem pista para escolher o intervalo

Quando `inspect` não devolve janela nenhuma — vídeo sem legenda, sem capítulo e sem tempo escrito na descrição —, varra o vídeo inteiro num contact sheet de baixa resolução:

```sh
python3 scripts/gb.py preview --scan --candidate <ID> --project <projeto>
```

O `--scan` não define intervalo: ele só mostra o vídeo todo, um quadro a cada N segundos (N = duração/12), com teto de `GB_SCAN_MAX_SECONDS` (padrão 900 s). Escolha o `--start/--end` olhando o resultado.

Quando o pedido é uma referência estática — um print, uma capa, um quadro só —, `preview --reference-only` gera apenas essa referência, sem GIF.

## Quando não há fonte

Reporte o que você tentou, com a fonte real do erro, e pergunte ao usuário se ele tem material próprio ou um link. Falha de extração é reportada com o motivo real: não invente indisponibilidade permanente nem troque de arquitetura por conta própria.
