---
name: get-brolls-setup
description: Instala as dependências do Get B-rolls na pasta do plugin e reporta o veredito do doctor.
---

# Configurar o Get B-rolls

Prepare a instalação do plugin nesta máquina e devolva um veredito curto ao usuário. Execute os comandos na ordem abaixo, um de cada vez, mostrando a saída real. Código de saída 4 não é falha: é o `doctor` dizendo que faltam itens, com o JSON completo em stdout; leia o `summary` e siga, não pare ali. Pare só com outro código diferente de 0.

1. Confira os pré-requisitos sem instalar nada:

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/install.sh" --check
```

No Windows, use `powershell -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/scripts/install.ps1" -Check`. Se algum executável estiver ausente (`MISSING:`), peça ao usuário que o instale pelo gerenciador oficial do sistema conforme `${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md` e repita esta etapa.

2. Rode o instalador completo do sistema operacional:

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/install.sh"
```

No Windows, use `powershell -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/scripts/install.ps1"`. Ele cria `.venv/` e `.tools/` dentro da pasta do plugin, obtém yt-dlp/EJS e o Playwright CLI e não altera instalações globais. No fim ele roda o `doctor`: se sair com código 4 ("doctor encontrou pendências"), a instalação terminou e faltam itens em `summary.missing`; siga para a etapa 3.

3. Diagnostique o resultado:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" doctor
```

No Windows, use `python` no lugar de `python3`. Código 0: pronto (`ready: true`). Código 4: faltam itens (`ready: false`, lista em `summary.missing`); o JSON sai em stdout do mesmo jeito, então não pare: vá para a etapa 4.

4. Leia o objeto `summary` do JSON e responda ao usuário em **uma linha**: quantas capacidades estão em `ok`, o que aparece em `missing` com o comando que resolve cada item e o que é apenas `optional` (por exemplo `PEXELS_API_KEY` ausente). Não invente resultados: cite apenas o que o `doctor` devolveu.

Lembre o usuário de repetir `/get-brolls-setup` após cada `/plugin update`, porque as dependências vivem na pasta versionada do plugin. Chaves opcionais de Pexels/Pixabay ficam no ambiente ou em `$GB_HOME/.env` (padrão `~/.getbrolls/.env`), fora da pasta do plugin: vale para qualquer instalação e sobrevive ao `/plugin update`. Outro arquivo só com `--env-file` na raiz do parser: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" --env-file CAMINHO <subcomando> …`; o `doctor` mostra qual `.env` valeu em `install.env_file`.
