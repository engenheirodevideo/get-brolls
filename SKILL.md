---
name: get-brolls
description: Use when the user asks to collect B-roll, download supporting footage from YouTube, Instagram or TikTok, search stock videos, or review inserts for an edit.
license: MIT
metadata:
  version: "2.3.4"
  type: "skill"
  status: "current"
  created: "2026-09-15"
  updated: "2026-09-15"
  tags: "b-roll, autoedit, youtube, instagram, tiktok"
---

# Get B-rolls

Preserva o processo autoedit: planejar fontes literais, mostrar sequência do trecho, receber decisão humana, obter corte final e verificar. YouTube usa yt-dlp **sem API key**. Instagram usa navegador/Playwright com streams separados de vídeo e áudio.

## Instalação e contexto

Localize esta pasta completa. Leia [INSTALL](INSTALL.md), instale os pré-requisitos faltantes pelos gerenciadores oficiais indicados e execute `bash scripts/install.sh` para obter yt-dlp/EJS e Playwright na máquina do destinatário; nunca copie `.venv`, `.tools` ou bibliotecas de outra instalação. Execute `python3 scripts/gb.py doctor`. Use caminho absoluto do CLI e `--project` ao trabalhar de outra pasta. O `.env` é da raiz da skill. Pexels/Pixabay têm chaves opcionais próprias.

## Fontes e preparação

1. Consulte `rules --project` e `references --project`. Use fala/objetivo fornecidos, sem inventar citação. Insert isolado não exige roteiro completo. Para roteiro completo, padrão autoedit: buscar 8+ clipes literais quando o conteúdo comportar; prefira pessoas/produtos/fatos nomeados e 1080p quando disponível. Não preencha com stock genérico para atingir uma contagem.
2. **YouTube:** `search --provider youtube --query "entidade ação"`; para URL use `resolve --url`. Leia [YouTube](references/providers/youtube.md).
3. **Instagram:** leia [INSTAGRAM-BROWSER](references/INSTAGRAM-BROWSER.md). Reutilize o Chrome logado indicado pelo usuário; abra o Reel nesse navegador/Playwright, identifique os streams do mesmo post, salve pares privados `_video.conf`/`_audio.conf` e execute `scripts/instagram/ig_curl_pair_downloader.py`. Ele baixa/junta/verifica os dois canais. Importe o MP4 com `resolve --file --source-url --creator --shot`. Em batch, use `--fail-on-duplicate-audio`. Preserve a sessão e nunca publique URLs assinadas/configs. yt-dlp é outra rota possível, não substitui o processo do navegador.
4. **TikTok:** descubra URL completa pelo navegador e use `resolve --url`; leia [TikTok](references/providers/tiktok.md). **Bancos:** `search --provider pexels|pixabay`; leia [STOCK](references/STOCK.md). Outras fontes: [ROUTER](references/ROUTER.md).

## Revisão e entrega

5. `preview --candidate ID --start INICIO --end FIM --project ...` obtém mídia de trabalho remota quando necessário e gera GIF/contact sheet. Não use um poster isolado como prova do movimento. Sem fala, omita `--narration`. `--reference-only` gera somente referência estática quando esse for o pedido.
6. `review --project ...`: entregue `brolls/` completo. Usuário aprova/ajusta e exporta JSON. Importe com `import-review --by`; `approve` apenas registra decisão humana já recebida. Não se autoaprove. Alterações de contexto/intervalo invalidam aprovação.
7. Registre condições reais com `permit --evidence` ou declaração do usuário configurada; depois `fetch` e `verify`. Não invente licença. O corte usa os bytes revisados e mantém origem/autor.

Helpers originais estão em `scripts/broll/`; o [README](README.md) mostra os comandos e explica sua relação com o ledger. Contexto da pessoa permanece estático; GIF padrão anima só B-roll. Full exige composição pronta do insert. Preserve originais, cache, eventos e journal. Página falhou após salvar: regenere `review`.

Para manutenção do código/documentação, siga [AGENTS](AGENTS.md). Leia [QA](QA.md) antes de declarar rotas testadas. Falha de extração é reportada com a fonte real; não invente indisponibilidade permanente nem mude para outra arquitetura.
