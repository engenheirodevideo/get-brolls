---
name: get-brolls
description: Collects, previews and delivers B-roll inserts with human review and source provenance. Use when the user asks to collect B-roll, cutaways, inserts or supporting footage for a video or Reel — searching YouTube, Instagram, TikTok or stock providers (Pexels, Pixabay, Wikimedia Commons, NASA), generating Storyboard previews for human review, and delivering licensed clips with provenance. Também: coletar b-roll, imagens de apoio, baixar cortes ou footage para ilustrar um vídeo. Not for editing or rendering the finished video.
license: MIT
metadata:
  version: "2.3.8"
  type: "skill"
  status: "current"
  created: "2026-09-15"
  updated: "2026-09-17"
  tags: "b-roll, youtube, instagram, tiktok, storyboard"
---

<!-- Gerado a partir do SKILL.md da raiz (fonte canônica do fluxo clone-como-skill). Ao editar um, sincronize o outro. -->

# GET B-ROLLS — ENGENHEIRO DE VÍDEO

Fluxo editorial completo para planejar fontes literais, mostrar a sequência do trecho, receber decisão humana, obter o corte final e verificar. YouTube usa yt-dlp **sem API key**. Instagram usa navegador/Playwright com streams separados de vídeo e áudio.

## Instalação e contexto

A raiz do plugin instalado é `${CLAUDE_PLUGIN_ROOT}` — a pasta dois níveis acima deste `skills/get-brolls/SKILL.md`; resolva o caminho absoluto real antes de ler arquivos ou executar comandos, porque a variável não é expandida pelas ferramentas. Leia a seção de [instalação do guia](${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md#instalação), instale os pré-requisitos faltantes pelos gerenciadores oficiais indicados e execute `bash "${CLAUDE_PLUGIN_ROOT}/scripts/install.sh"` no macOS ou `& "${CLAUDE_PLUGIN_ROOT}/scripts/install.ps1"` no Windows para obter yt-dlp/EJS e Playwright na máquina do destinatário; nunca copie `.venv`, `.tools` ou bibliotecas de outra instalação. Execute `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" doctor` no macOS ou `python "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" doctor` no Windows. No plugin o cwd é sempre o projeto do usuário: rode todos os subcomandos abaixo como `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" <subcomando> --project <projeto>`. O `.env` padrão fica na raiz do plugin, que é gerenciada por `/plugin update`: prefira variáveis de ambiente ou um `.env` fora dela, apontado na raiz do parser: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" --env-file CAMINHO <subcomando> …`, e repita o instalador após cada atualização do plugin. Pexels/Pixabay têm chaves opcionais próprias.

## Fontes e preparação

**Antes de buscar, tenha um brief.** Sem `BRIEF.md` na pasta do projeto, rode a entrevista de `/get-brolls-brief` (roteiro em `${CLAUDE_PLUGIN_ROOT}/references/interview.md`): até 7 perguntas, uma por vez, e dois "tanto faz" viram defaults com o que foi assumido visível na resposta. Depois use `init-brief --project ...`, preencha o bloco JSON e valide com `brief --validate --project ...` antes da primeira busca.
`brief --project ...` devolve cada beat com `search`, `resolve` e `preview` prontos (`brief --beat ID` mostra um só). Todo material de um beat entra com `--shot <beat.id>`: é esse campo que liga o beat ao candidato.
Brief orienta, não bloqueia — nenhum comando passa a exigir `BRIEF.md`. Mas nunca invente narração, alvo ou responsável para preencher um.

1. Consulte `rules --project` e `references --project`. Use fala/objetivo fornecidos, sem inventar citação. **Literal primeiro:** procure footage, prints ou imagens reais do fato, da pessoa, do produto, da notícia ou da tela que a narração cita; bancos de stock (Pexels/Pixabay) entram **somente quando o usuário pedir stock explicitamente**, nunca como preenchimento de um beat sem fonte literal. Insert isolado não exige roteiro completo. Para roteiro completo, o padrão editorial é buscar 8+ clipes literais quando o conteúdo comportar; prefira pessoas/produtos/fatos nomeados e 1080p quando disponível. Não preencha com stock genérico para atingir uma contagem. A responsabilidade pelas condições de uso do material é de quem produz o vídeo; a skill responde pela fidelidade/literalidade e pelo registro de origem de cada asset — `permit` e a proveniência gravada continuam sendo o mecanismo desse registro.
2. **YouTube:** `search --provider youtube --query "entidade ação"`; para URL use `resolve --url`. Leia a seção [YouTube](${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md#provedor--youtube).
3. **Instagram:** leia a seção [Instagram](${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md#instagram--navegadorplaywright-dois-streams-e-mp4). Reutilize o Chrome logado indicado pelo usuário; abra o Reel nesse navegador/Playwright, identifique os streams do mesmo post, salve pares privados `_video.conf`/`_audio.conf` e execute `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/getbrolls/instagram_pairs.py"`. Ele baixa/junta/verifica os dois canais. Importe o MP4 com `resolve --file --source-url --creator --shot`. Em batch, use `--fail-on-duplicate-audio`. Preserve a sessão e nunca publique URLs assinadas/configs. yt-dlp é outra rota possível, não substitui o processo do navegador. Para **lotes** (vários Reels), enfileire com `queue --action add --provider instagram --project ... URLs`, repita `queue --action next --project ...` — quando a resposta trouxer `wait_seconds` sem `item`, aguarde esse tempo antes de chamar de novo — capture os pares do item retornado e feche com `queue --action mark --id ID --done|--failed --reason "..." --project ...`. Rode o coletor com `--pace 20-60 --max-per-run 25 --continue-on-error --project ...` (use sempre `--project`, não `--config-output-root`, para o cooldown ir pra fila certa); um HTTP 403/429 abre cooldown na fila: pare, não insista.
4. **TikTok:** descubra URL completa pelo navegador e use `resolve --url`; leia a seção [TikTok](${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md#provedor--tiktok). **Bancos:** `search --provider pexels|pixabay`; leia [Bancos](${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md#bancos--busca-prévia-e-coleta). Veja também [Fontes e transportes](${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md#fontes-e-transportes).

## Revisão e entrega

5. `preview --candidate ID --start INICIO --end FIM --project ...` obtém mídia de trabalho remota quando necessário e gera poster, **contact sheet** e GIF do intervalo. A resposta traz `files.contact_sheet` (caminho absoluto) e `preview.frame_times_s` (tempo de cada célula). **Abra e olhe o contact sheet antes de seguir**: confirme que as células mostram o que a fala pede, cite as células/tempos vistos em `--reason` e, se não servirem, ajuste `--start/--end` e gere outra prévia. O Storyboard embute exatamente esse arquivo, então quem revisa vê os mesmos quadros que você viu; nunca descreva quadros que não conferiu. Não use um poster isolado como prova do movimento. Sem fala, omita `--narration`. `--reference-only` gera somente referência estática quando esse for o pedido.
6. `review --project ...`: entregue `brolls/` completo. Sirva a pasta localmente com `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" serve --project <projeto>`, entregue ao usuário a URL `http://localhost:8767/review.html` e avise que abrir por `file://` pode desativar o salvamento local — exporte o JSON antes de fechar a página. Usuário aprova/ajusta e exporta JSON. Importe com `import-review --by`. Aprovação vem sempre de uma pessoa: pelo Storyboard (`import-review`) ou por fala explícita no chat (`approve --by NOME --channel chat --statement "frase"`, com `--statement` obrigatório no canal chat; `--all` cobre todos os itens com prévia). Nunca inferir de silêncio. Não se autoaprove. Alterações de contexto/intervalo invalidam aprovação.
7. Registre condições reais com `permit --evidence`, ou a declaração dita no chat com `permit --declared-by NOME --declaration-text "frase"`; depois `fetch` e `verify`. Não invente licença. O corte usa os bytes revisados e mantém origem/autor.

Use `status --project ...` para reportar ao usuário onde a coleta está — candidatos, prévias, decisões, permissões e entregas — sem alterar o projeto.

Utilitários YouTube estão em `${CLAUDE_PLUGIN_ROOT}/scripts/getbrolls/tools/youtube/`; o [README](${CLAUDE_PLUGIN_ROOT}/README.md) mostra os comandos e explica sua relação com o ledger. Contexto da pessoa permanece estático; GIF padrão anima só B-roll. Full exige composição pronta do insert. Preserve originais, cache, eventos e journal. Página falhou após salvar: regenere `review`.

Para manutenção do código/documentação, siga [AGENTS](${CLAUDE_PLUGIN_ROOT}/AGENTS.md). Leia [Qualidade e evidências](${CLAUDE_PLUGIN_ROOT}/docs/QUALITY.md) antes de declarar rotas testadas. Se `doctor` informar uma versão diferente da documentada aqui, leia [CHANGELOG](${CLAUDE_PLUGIN_ROOT}/CHANGELOG.md) antes de seguir. Falha de extração é reportada com a fonte real; não invente indisponibilidade permanente nem mude para outra arquitetura.
