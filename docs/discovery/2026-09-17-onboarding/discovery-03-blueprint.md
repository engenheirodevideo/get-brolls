# Blueprint — camada de brief/intake do Get B-rolls (v2.4.0)

## 0. Padrões existentes respeitados
- `cli.py:12` `SUMMARIES` (uma linha PT-BR por subcomando; `tests/test_cli_help.py:27` exige help = SUMMARIES).
- `rules.py:12` `load_rules` — Markdown com exatamente um bloco ```json, validação manual, erros PT-BR; `GB_RULES_FILE`.
- `commands.py:494` `init-rules` (recusa sobrescrever); `commands.py:502` `rules`.
- `commands.py:471` `status` somente leitura; `summary` primeira chave com `line`/`stages`/`next` (`tests/test_status.py:120`); `STATUS_LADDER` (`commands.py:229`).
- `ledger.py:105` `Ledger` — `brolls/manifest.json` (`schema_version: 1`), `candidates/<sha16>.json`, `events.jsonl`, `validate_manifest` restringe a `previews/`/`clips/`.
- `models.py:9` `candidate()` tolera campos extras; `resolve --shot` grava `c["shot"]` e sufixa id `:shot:<nome>` (`commands.py:655`).
- `memory.py` `references.json` lateral. `schemas/*.json` documentais.

Decisão: **brief = Markdown com um único bloco JSON** (igual RULES.md). Sem YAML.

## A. Conceito `brief`
- Template `docs/BRIEF.md`; cópia do usuário `<projeto>/BRIEF.md` (fora de `brolls/`); override `GB_BRIEF_FILE`.
- Schema v1: `video {title, objective, audience, delivery{format, duration_s, platform}}`, `rights {posture per_item_evidence|user_declaration, stock_allowed, notes}`, `defaults {allowed_sources, intent, duration_hint_s, stock}`, `beats[] {id [a-z0-9-]{1,40} único e compatível com --shot, narration|null, target obrigatório, intent, allowed_sources ⊆ {youtube,instagram,tiktok,pexels,pixabay,commons,nasa,local}, stock, duration_hint_s, queries[], notes}`.
- Validação: `delivery.format` divergente de `rules.video_format` → `brief_conflict` (não fatal); `user_declaration` exige `rules.copyright` preenchido; `stock:true` sem banco permitido = erro; `stock:false` + pexels/pixabay = erro; campos desconhecidos ignorados.
- CLI: `init-brief`, `brief` (`--validate`, `--beat ID`, `--json`). **Não fundir em `rules`.** Saída com `summary {line, problems, next}`, `beats[].resolved`, `beats[].commands` (search/resolve prontos), `candidates`, `state`, `conflicts`.
- Arquivos: criar `scripts/getbrolls/brief.py` (~180 linhas: `load_brief`, `validate_brief`, `resolve_beat`, `beat_commands`, `beat_progress`), `docs/BRIEF.md`, `schemas/brief.schema.json`, `tests/test_brief.py`; modificar `cli.py`, `commands.py`, SKILL.md + espelho, READMEs, GUIDE, CHANGELOG.
- Compat: nenhum comando passa a exigir brief; manifest e `signature()` (`models.py:44`) intocados; vínculo beat↔asset via `shot`.
- **Esforço M.**

## B. Protocolo de entrevista
- Mora em: `commands/get-brolls-brief.md` (slash command operacional), `references/interview.md` (perguntas, defaults, heurísticas), 3 linhas no `SKILL.md` + espelho.
- Perguntas (ordem fixa, uma por vez, teto 7):
  1. Qual é o vídeo e o que ele precisa provar? → `video.title/objective`
  2. Para quem, e onde vai ser publicado? → `audience`, `delivery`
  3. Cole a narração ou diga os momentos em ordem → `beats[].narration` (agente segmenta e confirma uma vez)
  4. Por beat, só se não óbvio: o que precisa aparecer? → `beats[].target`
  5. Tem material próprio ou links? → `allowed_sources: ["local"]` + `resolve --file/--url`
  6. Pode usar banco genérico quando não existir registro real? (global) → `rights.stock_allowed`
  7. Quem assina a responsabilidade pelo uso? → `rights.posture`
- Parar quando 1, 3 e 7 respondidas. Dois "tanto faz" → aplica defaults, grava, mostra.
- Defaults: format = `rules.video_format`; intent `literal`; sources `["youtube","commons","nasa"]`; stock `false`; duration_hint 4s; posture `per_item_evidence`.
- Agente escreve BRIEF.md, roda `brief --validate`, depois coleta por `brief --beat` (`--shot <beat.id>` em todo resolve; `--narration` literal; query usada gravada de volta em `beat.queries`).
- Testes: packaging do command e `references/`; espelhamento SKILL; eval `brief-entrevista-preguicosa.md` (≤7 perguntas, `stock:false`).
- **Esforço S.**

## C. Organização dos assets
- Hoje: nomes por hash (`brolls/clips/<sha16>-r<N>.mp4`, `commands.py:833`); só o Storyboard traduz.
- Proposta: camada derivada `entrega/` (regenerável), `brolls/` continua canônico:
```
<projeto>/
  BRIEF.md
  RULES.md
  entrega/
    README.md                         # índice PT-BR gerado
    01-beat-01-jensen-huang-gtc/
      01-beat-01-jensen-huang-gtc.mp4 # hardlink→symlink→cópia
      contact-sheet.jpg
      ORIGEM.md                       # fonte, autor, intervalo, direitos, sha256
    00-sem-beat/
  brolls/                             # cache interno inalterado
```
- Numeração pela ordem dos beats; slug `<NN>-<beat.id>-<slug(target)>` ASCII ≤60.
- Comando `deliver --project` (`--dry-run`), idempotente, roda ao fim de `verify` mas falha vira `record_warning("DELIVERY_LINK_FAILED")` sem reprovar verify.
- Opcional `c["delivery"] = {path, method}` no manifesto (retrocompatível).
- Arquivos: `scripts/getbrolls/delivery.py`, `tests/test_delivery.py`; `cli.py`, `commands.py`, GUIDE.
- Testes: slug estável, idempotência, órfãos, fallback de link, dry-run, path safety (`BEAT_ID_RE`), warning em verify.
- **Esforço M/L.** Se apertar, entregar A+B+D primeiro.

## D. "Próximo passo" no `status`
- Problema: `STATUS_LADDER` nomeia verbo, nunca o comando completo nem URL.
- Aditivo em `summary`: `do {why, command (absoluto, executável), url (só na revisão: http://127.0.0.1:8767/review.html), for_human (única frase a repassar), blocking_human}` e `brief {beats, covered, missing}`.
- Degrau novo no topo: sem BRIEF.md → "Rode /get-brolls-brief"; beats sem candidato → search pronto do primeiro. Conflito brief×rules junto de `format_pending`.
- Módulo `scripts/getbrolls/guidance.py` (`next_action`, `human_line`, `command_for`) reusado por `status`, `brief`, `deliver`.
- Teste-chave: todo `do.command` parseia com `build_parser().parse_args(shlex.split(...))`. Atualizar `tests/test_status.py:126` (única quebra interna). `status` segue somente leitura.
- SKILL.md:37 → agente repassa `summary.do.for_human` sem parafrasear.
- **Esforço S/M.**

## E. Sequência
1. `brief.py` + `docs/BRIEF.md` + schema + `tests/test_brief.py`
2. `init-brief`/`brief` no CLI
3. `guidance.py` + `status.summary.do/brief`
4. `delivery.py` + `deliver` + hook pós-verify
5. `commands/get-brolls-brief.md` + `references/interview.md` + SKILL/espelho
6. Eval preguiçoso + run em `eval/runs/`
7. Docs + bump 2.4.0 (`__init__.py`, SKILL.md, plugin.json, package.json)

## F. Riscos transversais
- Duas fontes editoriais: RULES vence em formato/direitos/domínios; brief só declara intenção.
- `signature()` intocada → nenhuma aprovação invalidada.
- `deliver` é a peça com mais risco de FS (cross-fs, Windows symlink).
- Nada relaxa gate humano, `permit` ou proveniência.
