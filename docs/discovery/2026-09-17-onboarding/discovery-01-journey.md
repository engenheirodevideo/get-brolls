# Discovery 01 — Jornada real do criador não técnico no Get B-rolls 2.3.8

## (a) Jornada passo a passo

| # | Etapa | Quem age | O que o usuário precisa saber (e nunca foi ensinado) | Fricção |
|---|---|---|---|---|
| 0 | Instalar stack (Python 3.11+, FFmpeg com libfreetype, Node 22+, curl, Git) | Usuário | Homebrew/PowerShell, `PATH`, `libfreetype`/`drawtext` (`docs/GUIDE.md:27-78`) | 5 |
| 1 | Instalar skill/plugin e rodar `/get-brolls-setup` | Usuário | clone-como-skill vs plugin, `~/.claude/skills` vs `.claude/skills`, `${CLAUDE_PLUGIN_ROOT}` (`docs/GUIDE.md:139-197`) | 4 |
| 2 | `doctor` | CLI | Ler JSON, `summary.missing`, `contact_sheet.labels` (`commands.py:421-461`) | 3 |
| 3 | **Pedido** "preciso de b-roll pro meu vídeo" | Usuário → Agente | **Não existe intake.** Nenhum comando de brief em `SUMMARIES` (`cli.py:12-33`) | 5 |
| 4 | Fornecer roteiro/narração/objetivo | Usuário | Formato livre. `clips[].bloco_roteiro`/`fala` (`GUIDE.md:227-233`) **não tem schema, parser nem validador** — referência órfã | 5 |
| 5 | Escolher pasta `--project` | Usuário | `--project` obrigatório em tudo (`cli.py:80-84`); `status` recusa projeto inexistente (`commands.py:471-477`) | 4 |
| 6 | `init-rules` + editar RULES.md | Usuário | **JSON dentro de Markdown** com validação rígida: um bloco ```json (`rules.py:19-21`), `version:1`, domínios minúsculos sem protocolo (`rules.py:49-59`), modos de copyright (`rules.py:64-76`) | 5 |
| 7 | Derivar beats/queries em inglês | Agente | Só em prosa (`SKILL.md:26`, `GUIDE.md:230-233`); nada no CLI registra beats | 3 |
| 8 | `search --provider --query --intent --limit` | Agente/CLI | `--intent literal|illustrative` decide ordem de provedores (`cli.py:169-184`, `commands.py:516-529`) | 3 |
| 9 | Escolher `--start/--end` | Agente | **Às cegas**: `duration_s: null` — fricção nº1 da rodada cega (`eval/runs/2026-09-16-2.3.7-claude-opus.md:70-71`) | 5 |
| 10 | `preview --candidate --start --end --narration --reason` | Agente/CLI | Abrir contact sheet e citar células (`SKILL.md:33`); ID com espaços exige aspas (`README.md:129`) | 3 |
| 11 | `review` gera `brolls/review.html` | CLI | — | 1 |
| 12 | `serve --project` | CLI (bloqueante) | `serve_forever()` trava o terminal (`serve.py:59-79`); comandos seguintes exigem outro terminal (`README.md:138-141`); porta pode cair para efêmera (`serve.py:51-55`) | 4 |
| 13 | Revisar no Storyboard | Usuário | Decisões em `localStorage`; só aviso "Exporte antes de fechar" (`assets/review.js:122-133`) | 3 |
| 14 | "Exportar revisão" → `getbrolls-review.json` | Usuário | Arquivo foi para Downloads; precisa dizer o caminho ao agente (`assets/review.js:351`) | 4 |
| 15 | `import-review --file --by` | Agente/CLI | `reviewEpoch` validado; JSON antigo recusa o lote inteiro (`review.py:85-89`, `GUIDE.md:213`) | 4 |
| 16 | `permit --evidence` ou `--declaration` | Usuário/Agente | Redigir condições reais; `--declaration` exige `responsible_person` + `declaration` em RULES (`cli.py:146-153`, `rules.py:73-76`) | 5 |
| 17 | `fetch --candidate` (um por vez) | Agente/CLI | N clipes = N comandos (`cli.py:116-121`) | 3 |
| 18 | `verify` | CLI | — | 1 |
| 19 | Pegar os arquivos | Usuário | Entregável é `brolls/clips/` + `brolls/credits.md`; `candidates/`, `previews/`, `events.jsonl`, `manifest.json`, `.getbrolls-sources/`, `work/` são de máquina (`GUIDE.md:369-386`) | 3 |
| 20 | Mudar `video_format` em RULES | Usuário | **Invalida silenciosamente todas as aprovações** (`rules.py:125-146`); `status` só avisa via `format_pending` (`commands.py:257-264`) | 5 |
| I1 | Instagram: Reel no Chrome logado, capturar rede | Agente + Usuário | Extensão Playwright, `tab-list`/`tab-select`, `requests`/`response-body`, manifesto DASH (`GUIDE.md:461-521`) | 5 |
| I2 | Escrever `_video.conf`/`_audio.conf` à mão | Agente | Remover só `bytestart`/`byteend` preservando `oh`/`oe` (`GUIDE.md:491-531`) | 5 |
| I3 | `instagram_pairs.py` com 6 flags | Agente | `--config-output-root`, `--parts-dir`, `--layout`, `--fail-on-duplicate-audio` (`GUIDE.md:536-571`, `697-713`) | 5 |
| I4 | Lote: `queue add/next/mark` + `wait_seconds` | Agente | Cooldown 30min→4h por 403/429; `--project` obrigatório (`GUIDE.md:575-604`, `cli.py:92-115`) | 5 |

## (b) Top 10 fricções

1. **Não existe intake.** `SUMMARIES` (`cli.py:12-33`) vai de `doctor` para `search`. `clips[].bloco_roteiro` (`GUIDE.md:227-233`) é doc órfã de fluxo shell antigo.
2. **GUIDE.md descreve dois produtos.** `GUIDE.md:233-252` ensina `search.sh`/`contact.sh`/`fetch.sh`/`verify.sh`; o produto real é `gb.py`.
3. **Intervalo às cegas.** `duration_s: null` no `search`; única meta perdida do baseline (`eval/runs/2026-09-16-2.3.7-claude-opus.md:60,66,70`).
4. **RULES.md = JSON-em-Markdown intolerante** (`rules.py:19-88`). Sem erro guiado, sem `init-rules --interactive`.
5. **Mudar `video_format` apaga aprovações.** `sync_formats` (`rules.py:125-146`) roda em quase todo comando (`commands.py:507`), sem confirmação.
6. **Loop de revisão com 3 trocas de contexto manual** + `serve` bloqueando terminal (`serve.py:74`).
7. **`reviewEpoch` recusa o lote inteiro** (`review.py:85-89`).
8. **`permit` exige redação jurídica** (`cli.py:146-153`); sem templates por provedor.
9. **Instagram é engenharia reversa** de DASH (`GUIDE.md:487-531`).
10. **Pasta do projeto sem mapa humano** (`GUIDE.md:369-386`); nomes de clipes vêm de `--shot`, que o usuário nunca define.

## (c) Melhorias

| # | Proposta | Esforço |
|---|---|---|
| 1 | `gb.py brief --project` (ou seção obrigatória no SKILL.md) gravando `brolls/brief.json` com `beats[] {fala, objetivo, tipo, query, provider}` | M |
| 1b | `search`/`preview` aceitando `--beat ID` que puxa `narration`/`reason` do brief | L |
| 2 | Reescrever `GUIDE.md:233-252` para `gb.py`; helpers `.sh` em apêndice "legado" | S |
| 3 | `duration_s` + chapters no `search` (yt-dlp já retorna); sugerir 2–3 janelas por resultado | M |
| 4 | `init-rules --interactive` em PT; erros de `rules.py` com exemplo corrigido | M |
| 5 | `sync_formats` exigir `--confirm-format-change` quando invalidar aprovações | S |
| 6 | "Copiar caminho do JSON" + salvar em `brolls/reviews/`; `import-review` sem `--file` pega o mais recente | M |
| 7 | `import-review` parcial: importa válidas, lista obsoletas | M |
| 8 | `permit --preset youtube|nasa|commons|pexels` | S |
| 9 | `gb.py instagram capture --url` orquestrando extensão + DASH + `.conf` | L |
| 10 | `review`/`fetch` gerarem `brolls/LEIA-ME.md`; clipes `NN_entidade_contexto.mp4` do beat | S |
