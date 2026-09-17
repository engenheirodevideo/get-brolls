# Discovery 02 — Skill review (SKILL.md/GUIDE/README) — get-brolls v2.3.8

Veredito: **Needs Major Revision** para persona não técnica. Eval/rubrica excelentes; SKILL.md é manual de CLI, não contrato de comportamento com humano.

## Achados (ranqueados)
- **Crítico** — sem entrevista/briefing: `SKILL.md:24-29` salta de `rules --project` para `search --query`. Nunca pergunta vídeo, beats, tom, duração, formato, fontes permitidas, stock sim/não, pasta.
- **Crítico** — rubrica cobra o que o SKILL não ensina: `eval/rubric.md:49-57` zera por "stock sem pedido"; `eval/corpus/trap-reuniao-fechada.md:36-39` exige que o agente pergunte; única instrução "pergunta, não chute" está em `commands/get-brolls-eval.md:43` (comando de QA).
- **Crítico** — `SKILL.md:20-22` gasta o bloco mais lido com instalação, já duplicada em `commands/get-brolls-setup.md`.
- **Crítico** — instruções ao humano em jargão: `SKILL.md:34` (`file://`, "salvamento local", "exporte o JSON"); `SKILL.md:35` (`permit --evidence`). Usuário fecha a aba e perde decisões.
- **Crítico** — sem comando de entrada; `README.md:159-164` manda `/get-brolls <pedido>`, inexistente em `commands/`.
- **Maior** — triggering `SKILL.md:3` não cobre "vídeos de apoio", "corte do Elon falando X", "print da tela" (3 casos de UI no corpus).
- **Maior** — sem `references/`; `SKILL.md:27,28,29,33` dependem de âncoras num GUIDE de 731 linhas; `SKILL.md:28` tem ~230 palavras de Instagram (~20% do arquivo).
- **Maior** — contradição `SKILL.md:34` (`gb.py serve`) vs `commands/get-brolls-eval.md:39` (`http.server`); `agents/openai.yaml:4` `default_prompt` pula entrevista, contra `eval/README.md:32`.

## Outline proposto do SKILL.md (~700-900 palavras)
1. Frontmatter com description ampliada (gatilhos PT-BR naturais).
2. Três guardas: literal primeiro; stock só sob pedido; parada obrigatória na revisão.
3. Passo 1 — Entreviste antes de buscar.
4. Passo 2 — Devolva o brief e confirme.
5. Passo 3 — Buscar fonte literal (→ `references/providers.md`).
6. Passo 4 — Prévia e checagem do contact sheet.
7. Passo 5 — Storyboard e revisão humana (texto pronto).
8. Passo 6 — Condições de uso, corte final, verificação.
9. Relatar status (template 5 linhas).
10. Quando não há fonte — reportar e perguntar.
11. Ambiente — `doctor`; falhou → `/get-brolls-setup`.
12. Índice de `references/`.

## Novos arquivos/comandos
| Item | Esforço |
|---|---|
| `references/glossario.md` | S |
| `references/templates-de-resposta.md` | S |
| Description ampliada (raiz + espelho) | S |
| Corrigir `commands/get-brolls-eval.md:39` → `serve` | S |
| Reescrever `default_prompt` em `agents/openai.yaml` | S |
| `references/providers.md` | M |
| `references/instagram.md` | M |
| `references/rights.md` | M |
| `commands/get-brolls-brief.md` | M |
| `commands/get-brolls-status.md` | M |
| `commands/get-brolls-review.md` | M |
| Reescrita SKILL.md + espelho | L |
| Checklist onboarding no README sem CLI | L |
| Smoke 3 casos pós-reescrita (`eval/README.md:57`) | L |

Preservar: `eval/rubric.md:82-91`, consistência "literal primeiro" (`SKILL.md:26`/`docs/GUIDE.md:230`/`eval/README.md:30`), contratos de devolutiva de `commands/get-brolls-setup.md:34` e `get-brolls-eval.md:55`.
