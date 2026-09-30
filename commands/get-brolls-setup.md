---
name: get-brolls-setup
description: Instala o runtime do getbrolls em $GB_HOME (compartilhado entre versões do plugin) e reporta o veredito do doctor.
---

# Configurar o Get B-rolls

Prepare a instalação do plugin nesta máquina e devolva um veredito curto ao usuário. Execute os comandos na ordem abaixo, um de cada vez, mostrando a saída real. No Windows, use `python` no lugar de `python3` em todos eles. Código de saída 4 não é falha: é o getbrolls dizendo que faltam itens, com o JSON completo em stdout; leia o `summary` e siga, não pare ali. Pare só com outro código diferente de 0.

1. Confira o que já existe, sem instalar nada:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" setup --check
```

2. Instale o runtime (a venv do yt-dlp e o Playwright CLI):

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" setup
```

O runtime vai para `$GB_HOME/runtime` (padrão `~/.getbrolls/runtime`, ou `GB_RUNTIME_DIR`), compartilhado entre versões do plugin, sem alterar instalações globais; rodar de novo não refaz o que já está pronto. Com código 4, faltam itens do sistema (FFmpeg, ffprobe, curl, Node 22+, npx); leia o `summary` e o `hint` de cada passo em `steps`, que traz o comando do sistema operacional, peça ao usuário que instale o que falta conforme `${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md` e não pare: siga para a etapa 3.

3. Diagnostique o resultado:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" doctor
```

Código 0: pronto (`ready: true`). Código 4: faltam itens (`ready: false`, lista em `summary.missing`); o JSON sai em stdout do mesmo jeito, então não pare: vá para a etapa 4.

4. Leia o objeto `summary` do JSON e responda ao usuário em **uma linha**: quantas capacidades estão em `ok`, o que aparece em `missing` com o comando que resolve cada item e o que é apenas `optional` (por exemplo `PEXELS_API_KEY` ausente). Não invente resultados: cite apenas o que o `doctor` devolveu.

O runtime fica em `$GB_HOME/runtime`, fora da pasta versionada do plugin, e sobrevive ao `/plugin update`. Depois de um `/plugin update`, rode de novo só se o `doctor` apontar yt-dlp ou Playwright ausentes (a versão das dependências mudou). Chaves opcionais de Pexels/Pixabay ficam no ambiente ou em `$GB_HOME/.env` (padrão `~/.getbrolls/.env`), também fora da pasta do plugin. Outro arquivo só com `--env-file` na raiz do parser: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" --env-file CAMINHO <subcomando> …`; o `doctor` mostra qual `.env` valeu em `install.env_file`.
