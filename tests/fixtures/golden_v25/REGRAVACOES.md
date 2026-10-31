---
type: documentation
status: current
created: 2026-10-08
updated: 2026-10-08
tags: [getbrolls, testes, ouro]
---

# Regravações do ouro da 2.5

Uma linha por regravação (`GB_UPDATE_GOLDEN=1` com `GB_GOLDEN_REASON`), a mais antiga primeiro. Nunca reescreva as anteriores.

Uma regravação que muda o que o `accepted.json` leva entra com o item sem data (`"ratificado": null`) e o motivo em `pendente`, até o mantenedor ratificar e pôr a data. A suíte comum pula `test_no_item_waits_for_ratification` dizendo quais itens aguardam; com `GB_GOLDEN_REQUIRE_RATIFIED=1` (antes de publicar) ele falha.

- 2026-10-08: fluxo: gravação inicial na árvore de e5d65f2
- 2026-10-08: layout0: gravação inicial na árvore de e5d65f2
- 2026-10-08: fluxo: a data do dia no frontmatter vira <TODAY> (o ouro valia só no dia da gravação)
- 2026-10-08: layout0: a data do dia no frontmatter vira <TODAY> (o ouro valia só no dia da gravação)
- 2026-10-08: layout0: o projeto de layout 0 congelado fica com a data fixa 2000-01-01, de um dia anterior à rodada, como um projeto real da 2.5
- 2026-10-09: fluxo: o doctor ganha install.data_unreadable (arquivos de dados sem permissão de leitura, à parte de data_missing)
- 2026-10-09: fluxo: effects log: linhas event=op por efeito colateral, duration_ms ao lado de ms e outcome no command_end
- 2026-10-09: layout0: effects log: linhas event=op por efeito colateral, duration_ms ao lado de ms e outcome no command_end
- 2026-10-09: fluxo: effects log: nomes com HMAC de sal fixo da rodada e extensão só no último nome
- 2026-10-09: layout0: effects log: nomes com HMAC de sal fixo da rodada e extensão só no último nome
- 2026-10-09: fluxo: effects log: falha de ferramenta externa sai como INVALID_DATA e o exit do check=True vai para a linha
- 2026-10-09: layout0: effects log: falha de ferramenta externa sai como INVALID_DATA e o exit do check=True vai para a linha
- 2026-10-09: fluxo: effects log: pastas temporárias do ffmpeg e do yt-dlp passam pelo effects (fs.mkdir e fs.rmtree)
- 2026-10-09: layout0: effects log: pastas temporárias do ffmpeg e do yt-dlp passam pelo effects (fs.mkdir e fs.rmtree)
- 2026-10-09: fluxo: effects log: a espera por trava (waited_ms) também sai como duration_ms
- 2026-10-09: layout0: effects log: a espera por trava (waited_ms) também sai como duration_ms
- 2026-10-09: fluxo: effects log: o doctor ganha install.log_salt (estado do sal dos hashes do event=op)
- 2026-10-09: layout0: effects log: o doctor ganha install.log_salt (estado do sal dos hashes do event=op)
- 2026-10-10: fluxo: review e deliver nomeiam o Storyboard e entrega/ por <projeto>/…, sem o caminho da máquina (regra de 2026-10-10)
- 2026-10-10: layout0: review e deliver nomeiam o Storyboard e entrega/ por <projeto>/…, sem o caminho da máquina (regra de 2026-10-10)
- 2026-10-10: fluxo: a trava ocupada da biblioteca nomeia o arquivo como $GB_HOME/library/index.lock (regra de 2026-10-10)
- 2026-10-10: layout0: a trava ocupada da biblioteca nomeia o arquivo como $GB_HOME/library/index.lock (regra de 2026-10-10)
- 2026-10-10: fluxo: o diagnostics.jsonl nomeia o projeto do comando como <projeto> também na grafia sem /private do macOS (regra de 2026-10-10)
- 2026-10-10: layout0: o diagnostics.jsonl nomeia o projeto do comando como <projeto> também na grafia sem /private do macOS (regra de 2026-10-10)
- 2026-10-10: fluxo: browser-plan: o screenshot grava em output/playwright/… relativo ao projeto, com o cwd no JSON (regra sem caminho da máquina)
- 2026-10-10: layout0: browser-plan: o screenshot grava em output/playwright/… relativo ao projeto, com o cwd no JSON (regra sem caminho da máquina)
- 2026-10-10: ratificados pelo mantenedor os itens caminho-da-maquina, trava-biblioteca e diagnostico-fora-do-projeto (regravações de 2026-10-10)
- 2026-10-12: fluxo: o doctor perde install.env_aliases: os nomes GETBROLLS_X deixam de valer como GB_X
- 2026-10-12: layout0: o doctor perde install.env_aliases: os nomes GETBROLLS_X deixam de valer como GB_X
- 2026-10-12: fluxo: o fetch dá o nome final ao clipe antes do ledger.commit do manifesto
- 2026-10-12: layout0: o fetch dá o nome final ao clipe antes do ledger.commit do manifesto
- 2026-10-23: fluxo: a biblioteca e a fila gravam schema_version 2 (o escopo de cliente e a parada de provedor); a 2.5 recusa o arquivo em vez de ignorar o campo (ratificado pelo mantenedor em 2026-10-23)
