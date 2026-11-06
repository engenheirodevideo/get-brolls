# Plugins do getbrolls

Esta pasta guarda os plugins oficiais feitos para o próprio getbrolls (fontes, análise e afins), cada um na
própria pasta (`plugins/getbrolls/<id>/`). Hoje ela não traz nenhum.

Um plugin daqui tem a mesma origem oficial das pontes de `plugins/bridges/`: vem no pacote, no mesmo caminho
relativo (`getbrolls/_data/plugins/getbrolls/<id>/`), e só roda se o `folder_digest` da pasta for o que o core
fixou em `scripts/getbrolls/sdk/official_pins.py` (gerado por `scripts/gen_official_pins.py`). Ele não passa
por `enable` nem pelo `plugins.json`.
