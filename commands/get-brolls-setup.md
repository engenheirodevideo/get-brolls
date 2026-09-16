---
name: get-brolls-setup
description: Instala as dependências do Get B-rolls na pasta do plugin e reporta o veredito do doctor.
---

# Configurar o Get B-rolls

Prepare a instalação do plugin nesta máquina e devolva um veredito curto ao usuário. Execute os comandos na ordem abaixo, um de cada vez, e pare no primeiro que falhar, mostrando a saída real.

1. Confira os pré-requisitos sem instalar nada:

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/install.sh" --check
```

No Windows, use `powershell -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/scripts/install.ps1" -Check`. Se algum executável estiver ausente (`MISSING:`), peça ao usuário que o instale pelo gerenciador oficial do sistema conforme `${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md` e repita esta etapa.

2. Rode o instalador completo do sistema operacional:

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/install.sh"
```

No Windows, use `powershell -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/scripts/install.ps1"`. Ele cria `.venv/` e `.tools/` dentro da pasta do plugin, obtém yt-dlp/EJS e o Playwright CLI e não altera instalações globais.

3. Diagnostique o resultado:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" doctor
```

No Windows, use `python` no lugar de `python3`.

4. Leia o objeto `summary` do JSON e responda ao usuário em **uma linha**: quantas capacidades estão em `ok`, o que aparece em `missing` com o comando que resolve cada item e o que é apenas `optional` (por exemplo `PEXELS_API_KEY` ausente). Não invente resultados: cite apenas o que o `doctor` devolveu.

Lembre o usuário de repetir `/get-brolls-setup` após cada `/plugin update`, porque as dependências vivem na pasta versionada do plugin. Chaves opcionais de Pexels/Pixabay ficam no ambiente ou em um `.env` fora dessa pasta, apontado na raiz do parser: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" --env-file CAMINHO <subcomando> …`.
