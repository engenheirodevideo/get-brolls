# Plano — Get B-rolls 2.4: onboarding, brief, aprovação sem fricção

Spec: PRDs aprovados por Bruno em 2026-09-17 (issues #36–#45), resumidos abaixo por tarefa. Discovery de apoio em `docs/discovery/2026-09-17-onboarding/` (5 relatórios com file:line). Branch: `feat/onboarding-brief-2.4` (base `origin/main` = v2.3.8, 301 testes verdes com `python3 -m unittest discover -s tests`).

## Global Constraints

- Nunca relaxar: `require_fetch` (`models.py:111-125`), `signature()` (`models.py:44-68`), invalidação por mudança de intervalo/contexto, `validate_manifest` restrito a `previews/`/`clips/`, `allowed()` por domínio. Nenhuma feature preenche `rights.evidence` ou `approval` sem ação humana explícita.
- Nada de dependência nova (sem YAML, sem jsonschema). Arquivos editáveis pelo humano = Markdown com um único bloco ```json, validação manual em PT-BR (padrão `rules.py`).
- Todo subcomando novo entra em `SUMMARIES` (`cli.py:12`) com help = summary (`tests/test_cli_help.py` exige). `--project` no laço de `cli.py:59`.
- `status` continua somente leitura (`test_status_never_writes_to_the_project`).
- SKILL.md raiz e `skills/get-brolls/SKILL.md` são espelhos: toda edição em um replica no outro.
- Texto ao humano em PT-BR coloquial; identificadores em inglês. Mensagens de erro dizem o que fazer.
- Suíte verde antes de cada commit: `python3 -m unittest discover -s tests`. Testes novos por tarefa. Use `Edit` em hunks pequenos; nunca reescreva arquivos inteiros existentes.
- Commits pequenos, mensagem em inglês no padrão do repo (`feat:`, `fix:`, `docs:`, `test:`), terminando com `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Nunca push.
- CHANGELOG: cada tarefa adiciona bullet sob `## [2.4.0] — Unreleased` (criar na Task 1).

## Task 1 — #39 Aprovação pelo chat e RULES sem edição manual

- P1 `approve`: `--all` (aplica a todos os candidatos com prévia gerada e sem aprovação válida; lista os aprovados e os pulados), `--channel chat|storyboard` (default `chat`), `--statement "frase exata"`. `models.approve(c, by, channel, statement)` grava `approval.channel` e `approval.statement`; `ledger.save_many("approve-chat", ...)` quando channel=chat. `--candidate` continua aceito.
- `review.review_epoch` (`review.py:10-14`) passa a calcular sobre `{status, by, at, revision}` de `approval`, não o dict inteiro. Teste: JSON de board exportado com approval antigo continua importável.
- P2 `permit --declared-by NOME --declaration-text "..."`: grava `rights.status=permitted`, `rights.basis="user_declaration"`, `rights.responsible_person`, `rights.declaration_channel="chat"`, `rights.evidence` = nome + texto literal. Exige nome ≠ vazio/"usuário" e texto ≥ 20 chars. Sem as flags, comportamento atual.
- P3 `init-rules --responsible NOME --declaration TEXTO --mode per_item_evidence|user_declaration --force`: gera RULES.md com o bloco JSON preenchido (reescreve só o bloco ```json, preserva a prosa). Sem flags = comportamento atual.
- P4 prosa: `SKILL.md:34` + espelho, `docs/GUIDE.md:618`, `docs/RULES.md:11,48` → "Aprovação vem sempre de uma pessoa: pelo Storyboard (`import-review`) ou por fala explícita no chat (`approve --by NOME --channel chat --statement "frase"`). Nunca inferir de silêncio."
- P5 `STATUS_LADDER` (`commands.py:238-241`): degrau de decisão humana oferece as duas rotas.
- Testes: `tests/test_approve_chat.py`, `tests/test_permit_declaration.py`, `tests/test_init_rules_flags.py`; regressão de import-review.
- Critérios: projeto novo com `init-rules` sem flags roda `search → fetch` sem tocar no arquivo; item sem prévia não é aprovado por `--all`; `events.jsonl` distingue chat de board.

## Task 2 — #45 Docs legado no GUIDE

- `docs/GUIDE.md:220-260`: reescrever "Fluxo editorial" para `gb.py` (sem `.sh`); mover helpers `.sh` para "Apêndice — utilitários legados"; remover `clips[].bloco_roteiro` (`GUIDE.md:227-233`) com nota "substituído por BRIEF.md na 2.4".
- `commands/get-brolls-eval.md:39`: usar `gb.py serve` em vez de `http.server`.
- Script `scripts/check_anchors.py` (stdlib) que valida que toda âncora `GUIDE.md#...` citada em SKILL.md, README.md, README.en.md resolve para um heading; teste `tests/test_doc_anchors.py`.
- Sincronizar README.en.md onde tocar.

## Task 3 — #43 UX copy do agente e do Storyboard

- Storyboard (`storyboard.py`, `rendering.py:67-70`, `assets/review.js`, `assets/*.css` se preciso): rótulos PT-BR coloquial; botões de decisão reduzidos a Aprovar / Pedir ajuste / Reprovar (manter compatibilidade de valores exportados: `approved|adjust|rejected`; "Outra fonte" vira opção dentro de Pedir ajuste); consequência de cada botão em `title`; validação do comentário na hora (não só no export); faixa pós-export "Decisões salvas em <arquivo>. Agora volte à conversa e diga onde salvou."; botão "Exportar revisão" → "Salvar decisões"; rodapé de direitos (`storyboard.py:23`) e "Uso: a confirmar" explicados em uma frase.
- `references/templates-de-resposta.md` (novo, criar pasta `references/`): entrega do Storyboard, explicação de direitos/permit, "onde estão meus arquivos" (promove `credits.md`), fonte indisponível, status em 5 linhas. `references/glossario.md`.
- Mensagens de erro de `rules.py` (load_rules) e `review.py:86-89` com o que fazer em uma frase.
- Não mudar `templateVersion` nem o schema do export. 375 px sem overflow.
- Testes: `test_storyboard` snapshots atualizados; `test_plugin_packaging` inclui `references/`.

## Task 4 — #36 Brief/intake e entrevista

- `docs/BRIEF.md` template (frontmatter `type: brief`), `schemas/brief.schema.json` (documental), `scripts/getbrolls/brief.py`: `load_brief(project)`, `validate_brief`, `resolve_beat`, `beat_commands`, `beat_progress`, `BEAT_ID_RE = [a-z0-9-]{1,40}`; `GB_BRIEF_FILE` override.
- Schema v1: `video {title, objective, audience|null, delivery {format native|reels|horizontal, duration_s|null, platform|null}}`, `rights {posture per_item_evidence|user_declaration, stock_allowed bool, notes|null}`, `defaults {allowed_sources[], intent, duration_hint_s, stock}`, `beats[] {id, narration|null, target, intent literal|illustrative, allowed_sources[] ⊆ {youtube,instagram,tiktok,pexels,pixabay,commons,nasa,local}, stock, duration_hint_s 0.5–120|null, queries[], notes|null}`. Regras: `delivery.format` ≠ `rules.video_format` → `conflicts` (não fatal); `user_declaration` exige `rules.copyright` preenchido; `stock:true` sem banco permitido = erro; `stock:false` + pexels/pixabay = erro; chaves desconhecidas ignoradas.
- CLI: `init-brief` (espelha `init-rules`), `brief` (saída com `summary {line, problems, next}` primeiro, `beats[].resolved`, `beats[].commands` = `search/resolve/preview` prontos com `--shot <beat.id>`, `--intent`, `--narration`; `candidates` por `shot`; `conflicts`), flags `--validate`, `--beat ID`.
- `commands/get-brolls-brief.md` + `references/interview.md`: 7 perguntas em ordem fixa (vídeo/objetivo; público/plataforma; narração ou momentos; alvo por trecho só se não óbvio; material próprio/links; banco genérico sim/não — global; quem assina responsabilidade), uma por vez, teto 7, para com 1, 3 e 7; dois "tanto faz" → defaults (format = rules, intent literal, sources youtube/commons/nasa, stock false, 4 s, per_item_evidence); agente escreve BRIEF.md, roda `brief --validate`, só então busca.
- SKILL.md + espelho: 3 linhas em "Fontes e preparação": sem BRIEF.md → entrevista antes de buscar.
- Eval: `eval/corpus/brief-entrevista-preguicosa.md` (usuário responde "faz aí"; gabarito: brief válido, stock=false, ≤ 7 perguntas).
- Testes: `tests/test_brief.py`; packaging.

## Task 5 — #37 status.summary.do

- `scripts/getbrolls/guidance.py`: `next_action(state) -> {why, command, url, for_human, blocking_human}`, `command_for(step, project, candidate)` com CLI absoluto (`SKILL_ROOT`) e `--project` absoluto; placeholders em MAIÚSCULAS quando só o humano tem o valor.
- `status.summary` ganha `do` e `brief {beats, covered, missing}` (aditivo; `line/stages/next` inalterados). Degraus novos no topo: sem BRIEF.md → "Rode /get-brolls-brief ou `init-brief`"; beats sem candidato → `search` pronto do primeiro. Decisão humana: `do` oferece board (url `http://127.0.0.1:8767/review.html` só se `review.html` existe) e chat (`approve --all --by NOME --channel chat`). Conflito brief×rules junto de `format_pending`. `blocking_human: true` só em revisão, `permit` per_item, conflito de formato.
- `brief` e (futuro) `deliver` reusam `next_action`.
- SKILL.md + espelho: "repasse `summary.do.for_human` sem parafrasear".
- Testes: `tests/test_guidance.py` — todo `do.command` de todo degrau parseia com `build_parser().parse_args(shlex.split(...))`; `tests/test_status.py` atualizado (chaves de summary), `status` continua somente leitura.

## Task 6 — #40 Analisar antes de coletar

- `social.probe_remote(url, langs=("pt","en"))`: `yt-dlp --dump-single-json --skip-download --write-auto-subs --sub-langs pt,en` (com pacing `GB_YTDLP_SLEEP`); parse de `duration`, `chapters[]`, `automatic_captions`/`subtitles`, `description` (timestamps `mm:ss`), VTT salvo em `.getbrolls-sources/` (0600).
- Comando `inspect --candidate ID | --url URL [--query "frase"] [--max-windows 3]` read-only sobre approval/segment: devolve `{duration_s, chapters[], subtitle_langs[], candidate_windows[{start_s, end_s, text, source subtitle|chapter|description_timestamp, score}]}`; com `--candidate`, grava `media.duration_s` no candidato (único efeito). Score = sobreposição de tokens normalizados entre `--query` e o texto da janela (stdlib).
- `preview --scan`: contact sheet de baixa resolução do vídeo inteiro, 1 quadro a cada N s (N = duração/12), teto `GB_SCAN_MAX_SECONDS` (default 900); não define segmento.
- `brief --beat` sugere `inspect --query <narration>` antes de `preview`. SKILL.md + espelho: "analise antes de pré-visualizar".
- Testes: `tests/test_inspect.py` com fixtures JSON do yt-dlp (com/sem capítulos/legendas; VTT mínimo), `set_segment` rejeita intervalo > duração após inspect, nada gravado em `brolls/` fora de `media.duration_s`.

## Task 7 — #44 Loop de revisão sem fricção

- `serve`: endpoint `POST /__save` (127.0.0.1, token por sessão injetado no HTML) grava `brolls/reviews/<timestamp>.json`; `assets/review.js` usa o endpoint quando servido, senão download + botão "copiar caminho". `serve --background` (PID em `brolls/.serve.pid`), `serve --stop`; `status` reporta `serve.running`.
- `import-review` sem `--file`: usa o mais recente de `brolls/reviews/`; importação parcial: aplica válidos, reporta `skipped[]` com motivo (`stale_epoch`, `signature_mismatch`).
- `sync_formats` (`rules.py:125-146`): quando invalidaria aprovações, aborta com lista a menos que `--confirm-format-change`; `status` mostra `format_pending` como hoje.
- `permit --preset youtube|nasa|commons|pexels|pixabay`: preenche `rights.evidence` com texto de condições genérico da fonte + "verifique a página da fonte: <url>"; `--evidence` adicional concatena.
- Testes: `test_serve` (save endpoint, token, background/stop, Windows-safe), `test_review_import` (auto-descoberta, parcial), `test_rules_assets` (gate), `test_permit_presets`.

## Task 8 — #38 Pasta entrega/ por beat

- `scripts/getbrolls/delivery.py`: `build_delivery(project, dry_run)`, `beat_dir_name(nn, beat_id, target)` (slug ASCII ≤ 60), `link_or_copy` (hardlink → symlink → cópia, método retornado), `render_index`.
- Comando `deliver --project [--dry-run]`: `entrega/NN-<beat.id>-<slug>/{<mesmo nome>.mp4, contact-sheet.jpg, ORIGEM.md}`, `entrega/00-sem-beat/`, `entrega/README.md` (tabela beat × narração × alvo × arquivo × estado × direitos + linha `do.for_human`). Idempotente; remove links órfãos; nunca sobrescreve arquivo regular editado (erro nomeando). Grava `c["delivery"] = {path, method}`.
- Hook: ao fim de `verify`, chama `build_delivery`; falha → `record_warning("DELIVERY_LINK_FAILED")`, verify continua exit 0.
- Sem brief: usa `shot` quando houver, senão ordem do manifesto.
- Testes: `tests/test_delivery.py` (slug, idempotência, órfãos, fallback forçado por OSError, dry-run, `../` rejeitado, warning em verify).

## Task 9 — #41 Biblioteca global e checkpoints

- P9 `load_rules` em camadas: `~/.getbrolls/RULES.md` (`GB_HOME` override para testes) → `GB_RULES_FILE` → projeto. Listas unem (`blocked_domains` só acumula); escalares sobrescritos pela mais específica; `copyright`/`responsible_person` nunca herdam (aviso em `rules_warnings`). `rules` devolve `sources {campo: caminho}`.
- P10 `scripts/getbrolls/library.py`: `~/.getbrolls/library/index.json` (`schema_version:1`, `assets[]`, `queries[]`, `providers{}`), `notes/<sha>.md`; escrita atômica, `chmod 0600`; `GB_LIBRARY=off` desliga. Entrada de asset: `asset_id, source_url, provider, title, creator, license_name, license_url, rights_basis, clip{start_s,end_s,signature}, tags[], decision, reason, by, at, used_by[{project_id, shot, at}]`. Toda resposta inclui `rights_not_transferable: true`.
- P11 comandos: `learn --query Q --provider P --outcome hit|miss [--note]`, `learn --preference "..."`, `learn --from-candidate ID` (copia de `references.json` + ledger), `library --search TERMO`. `search` anexa `library_hints[]` (até 5) quando houver.
- Checkpoints em SKILL.md + espelho + `references/interview.md`: C1 pós-brief ("fecho assim?"), C2 pós-shortlist (5–8 candidatos com janela/legenda), C3 pós-prévias ("aprova todos, ou quais?" → `approve --all --channel chat`). Cada um ≤ 8 linhas.
- Testes: `tests/test_library.py`, `tests/test_rules_layers.py` (declaração global ignorada com aviso; `GB_LIBRARY=off`; nenhum `project_path` no ledger de outro projeto).

## Task 10 — #42 Reescrita do SKILL.md, references/ e commands

- SKILL.md (+ espelho idêntico) ~800 palavras, ≤ 900, parágrafos ≤ 80 palavras, zero comandos de instalação: frontmatter com description ampliada (gatilhos: "vídeos de apoio", "corte do X falando Y", "print da tela", "imagens de apoio", "b-roll", "footage"; "Not for editing or rendering"); três guardas; Passo 1 entreviste; Passo 2 confirme o brief; Passo 3 busque literal (`references/providers.md`); Passo 4 analise e pré-visualize; Passo 5 revisão humana (chat ou board, texto pronto); Passo 6 direitos, corte, verify, entrega; relate status (`summary.do.for_human`); quando não há fonte; ambiente (`doctor` → `/get-brolls-setup`); índice de references.
- `references/providers.md`, `references/instagram.md` (extrai `SKILL.md:27-29`), `references/rights.md`; `commands/get-brolls-status.md`, `commands/get-brolls-review.md`; `agents/openai.yaml` `default_prompt` com entrevista; README/README.en: checklist de onboarding sem CLI visível; bump 2.4.0 em `__init__.py`, SKILL.md, `.claude-plugin/plugin.json`, `package.json`; CHANGELOG fechado.
- Testes: `tests/test_skill_mirror.py` (raiz == espelho), `test_plugin_packaging` (references + commands), smoke de trigger: 10 frases em `tests/test_skill_description.py` (só checa que as palavras-gatilho estão na description).

## Task 11 — Smoke + blind eval contra o baseline

- `bash scripts/install.sh` no worktree; `python3 scripts/gb.py doctor` verde.
- Rodada smoke (3 casos: `news-artemis-sls`, `ui-github-actions-pipeline`, `trap-reuniao-fechada`) + `brief-entrevista-preguicosa` + caso "aprovei todos pelo chat" (executor ad hoc), seguindo `eval/README.md` e `commands/get-brolls-eval.md`: executor só vê `## Roteiro`; juiz aplica `eval/rubric.md`.
- Relatório `eval/runs/2026-09-17-2.4.0-rc-claude-opus.md` a partir de `eval/runs/TEMPLATE.md` com comparação métrica a métrica ao baseline `2026-09-16-2.3.7-claude-opus.md` (reach literal, stock não pedido, parada na revisão, tempo até Storyboard, perguntas ao usuário, acerto de janela na primeira prévia). Qualquer queda = listar como regressão bloqueante.
- Separar ambiente de comportamento conforme a rubrica.
