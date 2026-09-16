---
name: get-brolls
description: Collects, previews and delivers B-roll inserts with human review and source provenance. Use when the user asks to collect B-roll, cutaways, inserts or supporting footage for a video or Reel — searching YouTube, Instagram, TikTok or stock providers (Pexels, Pixabay, Wikimedia Commons, NASA), generating Storyboard previews for human review, and delivering licensed clips with provenance. Também: coletar b-roll, imagens de apoio, baixar cortes ou footage para ilustrar um vídeo. Not for editing or rendering the finished video.
license: MIT
metadata:
  version: "2.3.7"
  type: "skill"
  status: "current"
  created: "2026-09-15"
  updated: "2026-09-16"
  tags: "b-roll, youtube, instagram, tiktok, storyboard"
---

<!-- Fonte canônica do fluxo clone-como-skill. Espelhado em skills/get-brolls/SKILL.md (plugin do Claude Code). Ao editar um, sincronize o outro. -->

# GET B-ROLLS — ENGENHEIRO DE VÍDEO

Fluxo editorial completo para planejar fontes literais, mostrar a sequência do trecho, receber decisão humana, obter o corte final e verificar. YouTube usa yt-dlp **sem API key**. Instagram usa navegador/Playwright com streams separados de vídeo e áudio.

## Instalação e contexto

Localize esta pasta completa. Leia a seção de [instalação do guia](GUIDE.md#instalação), instale os pré-requisitos faltantes pelos gerenciadores oficiais indicados e execute `scripts/install.sh` no macOS ou `scripts/install.ps1` no Windows para obter yt-dlp/EJS e Playwright na máquina do destinatário; nunca copie `.venv`, `.tools` ou bibliotecas de outra instalação. Execute `python3 scripts/gb.py doctor` no macOS ou `python scripts/gb.py doctor` no Windows. Use caminho absoluto do CLI e `--project` ao trabalhar de outra pasta. O `.env` é da raiz da skill. Pexels/Pixabay têm chaves opcionais próprias.

## Fontes e preparação

1. Consulte `rules --project` e `references --project`. Use fala/objetivo fornecidos, sem inventar citação. Insert isolado não exige roteiro completo. Para roteiro completo, o padrão editorial é buscar 8+ clipes literais quando o conteúdo comportar; prefira pessoas/produtos/fatos nomeados e 1080p quando disponível. Não preencha com stock genérico para atingir uma contagem.
2. **YouTube:** `search --provider youtube --query "entidade ação"`; para URL use `resolve --url`. Leia a seção [YouTube](GUIDE.md#provedor--youtube).
3. **Instagram:** leia a seção [Instagram](GUIDE.md#instagram--navegadorplaywright-dois-streams-e-mp4). Reutilize o Chrome logado indicado pelo usuário; abra o Reel nesse navegador/Playwright, identifique os streams do mesmo post, salve pares privados `_video.conf`/`_audio.conf` e execute `scripts/getbrolls/instagram_pairs.py`. Ele baixa/junta/verifica os dois canais. Importe o MP4 com `resolve --file --source-url --creator --shot`. Em batch, use `--fail-on-duplicate-audio`. Preserve a sessão e nunca publique URLs assinadas/configs. yt-dlp é outra rota possível, não substitui o processo do navegador.
4. **TikTok:** descubra URL completa pelo navegador e use `resolve --url`; leia a seção [TikTok](GUIDE.md#provedor--tiktok). **Bancos:** `search --provider pexels|pixabay`; leia [Bancos](GUIDE.md#bancos--busca-prévia-e-coleta). Veja também [Fontes e transportes](GUIDE.md#fontes-e-transportes).

## Revisão e entrega

5. `preview --candidate ID --start INICIO --end FIM --project ...` obtém mídia de trabalho remota quando necessário e gera GIF/contact sheet. Não use um poster isolado como prova do movimento. Sem fala, omita `--narration`. `--reference-only` gera somente referência estática quando esse for o pedido.
6. `review --project ...`: entregue `brolls/` completo. Sirva a pasta localmente com `python3 -m http.server 8767 --bind 127.0.0.1 --directory <projeto>/brolls`, entregue ao usuário a URL `http://127.0.0.1:8767/review.html` e avise que abrir por `file://` pode desativar o salvamento local — exporte o JSON antes de fechar a página. Usuário aprova/ajusta e exporta JSON. Importe com `import-review --by`; `approve` apenas registra decisão humana já recebida. Não se autoaprove. Alterações de contexto/intervalo invalidam aprovação.
7. Registre condições reais com `permit --evidence` ou declaração do usuário configurada; depois `fetch` e `verify`. Não invente licença. O corte usa os bytes revisados e mantém origem/autor.

Utilitários YouTube estão em `scripts/getbrolls/tools/youtube/`; o [README](README.md) mostra os comandos e explica sua relação com o ledger. Contexto da pessoa permanece estático; GIF padrão anima só B-roll. Full exige composição pronta do insert. Preserve originais, cache, eventos e journal. Página falhou após salvar: regenere `review`.

Para manutenção do código/documentação, siga [AGENTS](AGENTS.md). Leia [Qualidade e evidências](QUALITY.md) antes de declarar rotas testadas. Se `doctor` informar uma versão diferente da documentada aqui, leia [CHANGELOG](CHANGELOG.md) antes de seguir. Falha de extração é reportada com a fonte real; não invente indisponibilidade permanente nem mude para outra arquitetura.
