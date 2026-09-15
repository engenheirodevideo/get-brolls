---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, recovery]
---

# Bancos — busca, prévia e coleta

Pexels/Pixabay usam suas próprias chaves no ambiente ou `.env` privado da skill. YouTube não depende delas. Execute `search --provider pexels|pixabay --query coffee --limit 2 --intent illustrative --project /projeto`.

`preview --candidate ID --start 0 --end 5 --project /projeto` atualiza a URL de mídia, obtém o original em `.getbrolls-sources/` e gera GIF/contact sheet. Preserva o ID remoto, fonte e autoria; não precisa aprovar um poster antes de ver o movimento. Aprovação fica pendente. Depois de review/decisão/condições, fetch usa a fonte revisada.

Também pode obter o original pela página oficial e usar resolve --file --source-url --creator. Nesse caso o ID local é novo. API indisponível não autoriza inventar candidato ou afirmar teste bem-sucedido.
