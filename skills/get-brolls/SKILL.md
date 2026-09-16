---
name: get-brolls
description: Use when the user asks to collect B-roll, download supporting footage from YouTube, Instagram or TikTok, search stock videos, or review inserts for an edit.
license: MIT
metadata:
  version: "2.3.6"
  type: "skill"
  status: "current"
  created: "2026-09-15"
  updated: "2026-09-16"
  tags: "b-roll, youtube, instagram, tiktok, storyboard"
---

<!-- Gerado a partir do SKILL.md da raiz (fonte canônica do fluxo clone-como-skill). Ao editar um, sincronize o outro. -->

# GET B-ROLLS — ENGENHEIRO DE VÍDEO

Fluxo editorial completo para planejar fontes literais, mostrar a sequência do trecho, receber decisão humana, obter o corte final e verificar. YouTube usa yt-dlp **sem API key**. Instagram usa navegador/Playwright com streams separados de vídeo e áudio.

## Instalação e contexto

A raiz do plugin instalado é `${CLAUDE_PLUGIN_ROOT}`. Leia a seção de [instalação do guia](${CLAUDE_PLUGIN_ROOT}/GUIDE.md#instalação), instale os pré-requisitos faltantes pelos gerenciadores oficiais indicados e execute `${CLAUDE_PLUGIN_ROOT}/scripts/install.sh` no macOS ou `${CLAUDE_PLUGIN_ROOT}/scripts/install.ps1` no Windows para obter yt-dlp/EJS e Playwright na máquina do destinatário; nunca copie `.venv`, `.tools` ou bibliotecas de outra instalação. Execute `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" doctor` no macOS ou `python "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" doctor` no Windows. Use caminho absoluto do CLI e `--project` ao trabalhar de outra pasta. O `.env` é da raiz da skill (`${CLAUDE_PLUGIN_ROOT}`). Pexels/Pixabay têm chaves opcionais próprias.

## Fontes e preparação

1. Consulte `rules --project` e `references --project`. Use fala/objetivo fornecidos, sem inventar citação. Insert isolado não exige roteiro completo. Para roteiro completo, o padrão editorial é buscar 8+ clipes literais quando o conteúdo comportar; prefira pessoas/produtos/fatos nomeados e 1080p quando disponível. Não preencha com stock genérico para atingir uma contagem.
2. **YouTube:** `search --provider youtube --query "entidade ação"`; para URL use `resolve --url`. Leia a seção [YouTube](${CLAUDE_PLUGIN_ROOT}/GUIDE.md#provedor--youtube).
3. **Instagram:** leia a seção [Instagram](${CLAUDE_PLUGIN_ROOT}/GUIDE.md#instagram--navegadorplaywright-dois-streams-e-mp4). Reutilize o Chrome logado indicado pelo usuário; abra o Reel nesse navegador/Playwright, identifique os streams do mesmo post, salve pares privados `_video.conf`/`_audio.conf` e execute `${CLAUDE_PLUGIN_ROOT}/scripts/getbrolls/instagram_pairs.py`. Ele baixa/junta/verifica os dois canais. Importe o MP4 com `resolve --file --source-url --creator --shot`. Em batch, use `--fail-on-duplicate-audio`. Preserve a sessão e nunca publique URLs assinadas/configs. yt-dlp é outra rota possível, não substitui o processo do navegador.
4. **TikTok:** descubra URL completa pelo navegador e use `resolve --url`; leia a seção [TikTok](${CLAUDE_PLUGIN_ROOT}/GUIDE.md#provedor--tiktok). **Bancos:** `search --provider pexels|pixabay`; leia [Bancos](${CLAUDE_PLUGIN_ROOT}/GUIDE.md#bancos--busca-prévia-e-coleta). Veja também [Fontes e transportes](${CLAUDE_PLUGIN_ROOT}/GUIDE.md#fontes-e-transportes).

## Revisão e entrega

5. `preview --candidate ID --start INICIO --end FIM --project ...` obtém mídia de trabalho remota quando necessário e gera GIF/contact sheet. Não use um poster isolado como prova do movimento. Sem fala, omita `--narration`. `--reference-only` gera somente referência estática quando esse for o pedido.
6. `review --project ...`: entregue `brolls/` completo. Usuário aprova/ajusta e exporta JSON. Importe com `import-review --by`; `approve` apenas registra decisão humana já recebida. Não se autoaprove. Alterações de contexto/intervalo invalidam aprovação.
7. Registre condições reais com `permit --evidence` ou declaração do usuário configurada; depois `fetch` e `verify`. Não invente licença. O corte usa os bytes revisados e mantém origem/autor.

Utilitários YouTube estão em `${CLAUDE_PLUGIN_ROOT}/scripts/getbrolls/tools/youtube/`; o [README](${CLAUDE_PLUGIN_ROOT}/README.md) mostra os comandos e explica sua relação com o ledger. Contexto da pessoa permanece estático; GIF padrão anima só B-roll. Full exige composição pronta do insert. Preserve originais, cache, eventos e journal. Página falhou após salvar: regenere `review`.

Para manutenção do código/documentação, siga [AGENTS](${CLAUDE_PLUGIN_ROOT}/AGENTS.md). Leia [Qualidade e evidências](${CLAUDE_PLUGIN_ROOT}/QUALITY.md) antes de declarar rotas testadas. Falha de extração é reportada com a fonte real; não invente indisponibilidade permanente nem mude para outra arquitetura.
