---
name: get-brolls
description: Use this skill when the user asks to "pegar b-rolls", collect B-roll / stock footage for an edit, or fill the brolls/ folder of a project. Plans beats from the edit contract, searches YouTube, PREVIEWS each candidate in the browser (screenshot) for confirmation BEFORE downloading, then downloads trimmed 1080p clips named NN_entity_context into the project's brolls/ folder. Ports the get-broll workflow into the autoedit pipeline.
version: 0.1.0
type: reference
status: reference
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, autoedit, original]
---

> Referência de origem do processo autoedit. Para execução nesta versão, siga SKILL.md e os guias de rotas atuais; nomes específicos de ferramentas e limitações históricas abaixo não substituem a capacidade da sessão atual.

# get-brolls (autoedit)

Coleta B-roll dirigida pelo contrato de edição. Confirma no browser antes de baixar; nunca autora vídeo.

## Fluxo
> **Meta: 8+ clipes literais por roteiro** (padrão do Bruno). Se os beats óbvios não fecham 8, amplie: mais empresas/pessoas citadas, cobertura de telejornal do mesmo fato, produto nomeado (ex.: Agentforce), pregão/mercado real, segmentos extras da mesma fonte forte. Todos 1080p (cheque com ffprobe; troque fontes 360/720 quando houver 1080).
1. **plan** — dos blocos do contrato (`clips[].bloco_roteiro` / `fala`), derive N beats visuais (query em inglês). Prefira **entidade literal nomeada** (pessoa/produto/logo do que a fala cita: Sam Altman, OpenAI, SoftBank, Codex…) — é onde o YouTube dá material real e limpo. Beats abstratos (back-office, "dev", "automação") caem em tutorial/vlog/stock/desenho; use no máximo um demo de produto real (ex.: dashboard ERP) ou deixe no rosto do talento.
2. **search** — YouTube sem baixar: `gb_search.sh "<query>" [n]` → `ID | DURATION | TITLE`.
3. **confirm** — preview por **contact sheet** (NÃO 1 frame solto): `gb_contact.sh <id> <START-END> <out.jpg> [interval_seg=1.5] [cols=4]` amostra N frames igualmente espaçados no segmento e tila num grid numerado → dá pra ler o MOVIMENTO/sequência e pegar legenda queimada/overlay antes de baixar. Default `1.5s` (visão ampla); `0.5s` quando precisar densidade. `gb_frame.sh` (1 still) vira fallback. **Gate: Bruno aprova antes de baixar.**
4. **download** — segmento trimado 1080p: `gb_fetch.sh <id> <START-END> <NN_entity_context> <PROJETO>/brolls`.
5. **verify** — `gb_verify.sh <PROJETO>/brolls` (tabela ffprobe + tamanho).
6. **(opcional) vertical** — `gb_vertical.sh <in>` pra 9:16 com fundo borrado.

## Imagens de notícia (manchete/dado)
- **Print de site não vira arquivo utilizável.** O `save_to_disk` do `claude-in-chrome` guarda no sandbox da extensão (só devolve `ss_<id>`, sem path no filesystem); `screencapture -R` da janela falhou ("could not create image from rect" — falta permissão de Screen Recording pro processo). Não prometa baixar manchete estática.
- **Prefira notícia em VÍDEO**: cobertura real (Reuters/CNBC/Bloomberg) no YouTube pelo mesmo fluxo (vira mp4 no projeto).
- **Estático** = entregar **lista de links + o que grifar** num `BROLL-MAP.md`, pro humano printar. Alguns sites (PYMNTS) caem em Cloudflare; Benzinga abre normal.

## Engine / scripts
Os helpers `gb_*.sh` agora são vendorizados no próprio plugin para não depender da skill externa `get-broll`:
`${GB_SKILL_DIR}/scripts/broll/gb_search.sh`, `gb_contact.sh` (contact sheet — preview padrão),
`gb_frame.sh` (still fallback), `gb_fetch.sh`, `gb_verify.sh`, `gb_vertical.sh`.
Deps: `yt-dlp` + `ffmpeg` + `screencapture`(macOS quando houver preview/browser).

## Saída
`<PROJETO>/brolls/NN_entity_context.mp4`. Registrar no ledger (`step: get-brolls`, outputs = arquivos baixados).
