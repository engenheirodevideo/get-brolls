# Discovery 05 — Gates, coleta de trechos e aprendizado (Get B-rolls 2.3.8)

## 1. Gates: código vs prosa

### Gates de código
| # | Gate | Evidência | Efeito |
|---|---|---|---|
| G1 | `load_rules` exige um bloco JSON válido | `rules.py:19-25` | Edição manual quebrada derruba todos os comandos (`commands.py:501`) |
| G2 | `copyright.mode == "user_declaration"` exige `responsible_person` e `declaration` | `rules.py:73-76` | **O gate que trava tudo**: falha em `load_rules` → search/resolve/preview/review/status morrem |
| G3 | `permit --declaration` só lê `rules["copyright"]` | `commands.py:715-729` | Sem caminho para declaração dada no chat |
| G4 | `require_fetch` exige aprovação com assinatura + `rights.status == "permitted"` | `models.py:111-125` | Correto, manter |
| G5 | `approve` exige segmento + `--by` | `models.py:95-108`, `cli.py:140-145` | Nada impede `approve --by "Fulano"` a partir do chat |
| G6 | `--candidate` obrigatório, um por vez | `cli.py:116-121` | Sem `--all`; "APROVEI TODOS" vira N chamadas |
| G7 | `import-review` valida `signature`/`reviewEpoch` | `review.py:57-90` | Integridade do board; manter |
| G8 | `allowed()` por `asset_types`/`blocked_domains` | `rules.py:97-100` | Manter |
| G9 | Mudança de contexto/intervalo invalida aprovação | `models.py:71-92`, `rules.py:125-146` | Núcleo da proveniência; manter |

**Descoberta:** `init-rules` **já funciona sem edição manual** no modo padrão (`per_item_evidence`, nulos — `docs/RULES.md:13-39`, `rules.py:70-76`; fallback para `docs/RULES.md` em `rules.py:13-17`). A trava percebida é a prosa mandando editar o arquivo + G2/G3 quando o usuário escolhe declaração.

### Gates de prosa (nenhum código aplica)
- `docs/RULES.md:11` "A skill nunca preenche uma declaração de responsabilidade em seu nome." — fonte direta da fricção.
- `docs/RULES.md:48` reforça "edite o arquivo".
- `SKILL.md:34` "`approve` apenas registra decisão humana já recebida. Não se autoaprove." — já autoriza `approve` para decisão recebida no chat, mas o agente lê como "só pelo board".
- `docs/GUIDE.md:618`, `:235`, `:764-766` — rota do board descrita como única.
- `STATUS_LADDER` (`commands.py:238-241`) só sugere `review` + `import-review`, nunca `approve` pelo chat.

**Conclusão:** nenhum gate de código impede "APROVEI TODOS" no chat. Recusa é 100% prosa + empurrão do `status`. Gate travante real: G2/G3.

### Mudança mínima
- **P1** `approve --all --channel chat|storyboard --statement "frase"` (`cli.py`, `commands.py:699-714`, `models.py:95-108`); grava `approval.channel/statement`; evento `approve-chat` no journal. Só para itens com prévia. Esforço S. Risco baixo (`signature()` não inclui `approval`). **Cuidado:** `review_epoch` (`review.py:10-14`) serializa `approval` inteiro — calcular sobre subconjunto estável (`status, by, at, revision`) para não invalidar boards já exportados.
- **P2** `permit --declared-by NOME --declaration-text "..."` (`commands.py:715-729`, `cli.py:146-153`); grava `rights.basis = user_declaration`, `responsible_person`, `declaration_channel = chat`, texto literal em `rights.evidence`. Esforço S. Risco médio → exigir nome explícito e texto mínimo.
- **P3** `init-rules --responsible --declaration --mode --force` gerando o bloco JSON preenchido (`commands.py:494-500`). Esforço S/M.
- **P4** Prosa: `SKILL.md:34` + espelho, `GUIDE.md:618`, `RULES.md:11,48` → "Aprovação vem sempre de uma pessoa: pelo Storyboard ou por fala explícita no chat; registre com `approve --by --channel chat --statement`. Nunca inferir de silêncio." Esforço S.
- **P5** `STATUS_LADDER` oferecer as duas rotas. Esforço S.

## 2. Coleta de trechos — "analisar antes de coletar"

### Hoje: chute puro
- YouTube `search` (`providers.py:207-221`, `social.py:150-158`, `--flat-playlist`) só traz `id, title, channel, duration, thumbnails`. Sem capítulos, legendas, descrição.
- Commons/NASA sem duração (`providers.py:254,308`). `resolve --url` (`providers.py:312-367`) não busca metadado; candidato nasce com `media.duration_s = None` (`models.py:21`).
- `set_segment` (`models.py:74-76`) só valida intervalo se houver duração; erro aparece depois do download (`acquisition.py:129`, `social.py:181`).
- Primeiro sinal visual é `preview` (`commands.py:741-768`): cada tentativa = um download.

### Propostas
- **P6** `inspect --candidate ID|--url [--query "frase"]` read-only: `yt-dlp --dump-single-json --skip-download --write-auto-subs --sub-langs pt,en` → `{duration_s, chapters[], subtitle_langs[], candidate_windows[{start_s,end_s,text,source,score}]}`; casa legenda com a narração do beat. Grava `media.duration_s` (ativa o gate de `set_segment` antes do download). VTT em `.getbrolls-sources/` (privado). Respeitar `GB_YTDLP_SLEEP`. Arquivos: `social.py` (`probe_remote`), `providers.py`, `cli.py`, `commands.py`. Esforço M. Risco baixo.
- **P7** `preview --scan`: contact sheet de baixa resolução do vídeo inteiro antes de escolher intervalo (`previewing.py`, `media.py:294`; base em `tools/youtube/contact.sh:51`). Teto próprio de segundos. Esforço M.
- **P8** Não enriquecer `search` (encarece); análise fica no `inspect`.

## 3. Diretrizes — onde vivem
- Por projeto: `editorial_rules`, `preferred_providers/domains`, `blocked_domains`, `video_format`, `pacing` em RULES.md (`rules.py:60-63`), devolvidas em cada `search` (`commands.py:574`).
- Global só técnico: `.env` (`config.py:6-32`, rejeita variável desconhecida `config.py:56-60`).
- Gancho pronto: `GB_RULES_FILE` (`rules.py:13`) aponta para um RULES.md fora do projeto — não documentado como preferências do usuário.
- **Não existe lugar global para preferências editoriais.**
- **P9** `load_rules` em camadas: `~/.getbrolls/RULES.md` → `GB_RULES_FILE` → `<projeto>/RULES.md`. Listas fazem união (`blocked_domains` só acumula); escalares sobrescritos pela mais específica; **`copyright`/`responsible_person` nunca herdam do global**. `rules` devolve `sources` por valor. Esforço M. Risco médio.

## 4. Aprendizados e assets reutilizáveis
- Hoje: `remember` (`memory.py:7-37`) grava `brolls/references.json` **por projeto**; cross-project só manual via `references --project /outro` (`GUIDE.md:386`). Não existe registro de queries que acertaram, provedores que falharam (`record_warning("PROVIDER_FAILED")`, `commands.py:547`, se perde) ou preferências.
- **P10** `~/.getbrolls/library/` com `index.json {schema_version, assets[], queries[], providers{}}` + `notes/<sha>.md`. Entrada de asset: `asset_id, source_url, provider, title, creator, license_name/url, rights_basis, clip{start,end,signature}, tags, decision, reason, by, at, used_by[{project_path, project_id, shot, at}]` (`project_id` estável de `review.py:17-24`).
- **P11** `learn --query ... --provider ... --outcome hit|miss --note`, `learn --preference "..."`, `learn --from-candidate ID`; `library --search`. `SKILL.md:26` passa a consultar `library --search` + `references --project`. Novo `scripts/getbrolls/library.py`.
- **Limites obrigatórios:** biblioteca é ponteiro editorial, **nunca transfere licença nem aprovação** (`permit` e `approval` continuam por projeto/revisão); resposta traz `rights_not_transferable: true`; `chmod 0600`; `GB_LIBRARY=off`. Esforço L. Risco médio-alto se mal delimitado.

## 5. Checkpoints leves (rota chat)
- **C1** pós-brief, antes de buscar: ecoar intenção, regras, provedores em ordem, quantos inserts; "fecho assim?". Prosa em `SKILL.md:26`. S.
- **C2** pós-shortlist, antes de baixar: 5–8 candidatos com título, canal, duração e janela + legenda (depende de P6); "sigo com estes?". M.
- **C3** pós-prévias, no lugar do board: descrever contact sheets e perguntar "aprova todos, ou quais?" → `approve --all --by --channel chat --statement` (P1) ou parcial. Board segue recomendado com revisor terceiro (garantia de `signature`+`reviewEpoch`). S.
- `status` (`commands.py:471-486`) já gera a linha-resumo para os três momentos.

## Arquivos essenciais
`rules.py`, `models.py`, `commands.py` (:494, :715, :228, :516), `cli.py` (:64-208), `review.py`, `memory.py`, `providers.py`, `social.py`, `acquisition.py`, `ledger.py`, `SKILL.md` + espelho, `docs/RULES.md`, `docs/GUIDE.md`.
