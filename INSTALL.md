---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, recovery]
---

# Instalação — Get B-rolls 2.3.4

## Dependências por capacidade

| Componente | Necessário para |
|---|---|
| Python 3.11+ | CLI e coletor de pares Instagram |
| FFmpeg e ffprobe, executáveis | Prévia, cortes, vídeo+áudio e validação |
| yt-dlp com extras `default` (inclui EJS) | Busca YouTube e aquisição de URLs sociais |
| Node 22+ | Playwright CLI e EJS do yt-dlp; Deno 2.3+ é alternativa apenas ao runtime EJS |
| Node/npm/npx + Playwright CLI + navegador | Captura de streams Instagram e inspeção pelo navegador |
| curl | Baixar os dois streams Instagram capturados |
| Bash e awk | Helpers originais `scripts/broll/gb_*.sh` |

Git é opcional. API key YouTube não é necessária. Pexels/Pixabay usam apenas suas próprias chaves opcionais. `curl-cffi` é extra opcional do yt-dlp, não requisito universal.

## macOS / Linux

Instale Python, FFmpeg, curl e Node pelo gerenciador de pacotes do sistema. Em macOS com Homebrew:

```sh
brew install python ffmpeg node
```

Em Ubuntu/Debian, instale a base com `sudo apt-get install python3 python3-venv ffmpeg curl`; instale também Node 22+ pela distribuição oficial. Confirme versões; não presuma que o Node do repositório do sistema é recente.

Dentro da pasta da skill `get-brolls/`:

```sh
bash scripts/install.sh --check
bash scripts/install.sh
```

O instalador cria `.venv` e instala `yt-dlp[default]`/EJS do PyPI via `requirements.txt`; instala também `@playwright/cli@0.1.20` do npm em `.tools`. Valida Python 3.11+, Node 22+, npm/npx e os executáveis base. Essas pastas de dependências ficam somente na máquina de quem instala e não fazem parte da distribuição da skill. Não instala executáveis do sistema nem altera a instalação do agente. A CLI procura primeiro `.venv/bin/yt-dlp`, depois o PATH. Os helpers também localizam a venv; ativá-la é opcional:

```sh
source .venv/bin/activate
python scripts/gb.py doctor
```

`doctor` lista executáveis e transporte; não certifica login, acesso a cada site ou extração ao vivo. `doctor --live` faz buscas remotas explicitamente e pode consumir quota dos bancos configurados.

## Navegador / Instagram

Use primeiro o navegador autorizado já conectado ao agente. Se o usuário indicou Chrome logado, selecione esse Chrome no plugin e abra o Reel ali; não troque silenciosamente para navegador integrado sem login.

Alternativa com a extensão oficial Playwright já instalada no Chrome:

```sh
bash scripts/playwright.sh -s=getbrolls-instagram attach --extension=chrome
bash scripts/playwright.sh -s=getbrolls-instagram tab-list
bash scripts/playwright.sh -s=getbrolls-instagram tab-select INDICE_OBSERVADO
```

A extensão Playwright e o plugin de navegador do agente são integrações diferentes. Use a que estiver disponível; não tente conectar a extensão de uma ferramenta com a CLI da outra. A instalação da CLI não instala extensões nem importa cookies. Consulte a [documentação oficial da extensão](https://github.com/microsoft/playwright/blob/main/packages/extension/README.md) quando precisar configurar essa alternativa.

Sem navegador existente, crie sessão própria:

```sh
bash scripts/playwright.sh -s=getbrolls-instagram open https://www.instagram.com/ --headed
```

Se faltar o navegador, execute `bash scripts/playwright.sh install-browser chrome`, conforme `--help` da CLI. Login ocorre nessa sessão e depende da conta do destinatário. Nunca distribua perfil/sessão de Bruno.

O método completo está em [INSTAGRAM-BROWSER](references/INSTAGRAM-BROWSER.md): navegador → streams vídeo/áudio → configs privados → curl → FFmpeg → ffprobe. O script de pares não substitui a etapa de captura operada pelo agente.

Referências de instalação: [yt-dlp/EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS), [Playwright CLI](https://github.com/microsoft/playwright-cli). Não há bibliotecas dessas ferramentas distribuídas junto da skill; o instalador obtém as distribuições oficiais.

## Configuração

`cp .env.example .env` é opcional. O `.env` pertence à raiz da skill, independentemente da pasta atual. Ambiente do processo prevalece. `--env-file /caminho/.env` vem antes do subcomando. Nunca distribua `.env`, cookies, configs CDN ou perfis do navegador.

## Codex e Claude Code

Copie os arquivos da skill, incluindo `AGENTS.md`, `scripts/broll/` e `scripts/instagram/`, para **um** dos destinos abaixo. Escolha instalação pessoal ou por projeto para evitar duplicatas com o mesmo nome. Ao partir de uma pasta de desenvolvimento, exclua `dist/`, `.venv/`, `.tools/`, `__pycache__/`, projetos e arquivos privados. Execute o instalador no destino final; não mova uma venv entre pastas:

| Agente | Pessoal | Projeto | Invocação |
|---|---|---|---|
| Codex | `~/.agents/skills/get-brolls/` | `.agents/skills/get-brolls/` | `$get-brolls` |
| Claude Code | `~/.claude/skills/get-brolls/` | `.claude/skills/get-brolls/` | `/get-brolls` |

Preserve cópias anteriores antes de substituir. Abra nova sessão para verificar descoberta. Use caminhos absolutos quando executar de outra pasta:

```sh
python3 "$GB_SKILL_DIR/scripts/gb.py" doctor
python3 "$GB_SKILL_DIR/scripts/gb.py" search --provider youtube --query "NASA Artemis" --limit 3 --project "$GB_PROJECT"
```

Defina GB_SKILL_DIR e GB_PROJECT com os caminhos reais. O pacote completo usa `fcntl` para trava e Bash nos helpers: macOS/Linux. Windows nativo não foi validado; WSL precisa dos executáveis instalados dentro do Linux e deve ser testado nesse ambiente.

## Verificação e atualização

Após instalar, confirme `yt-dlp` e `playwright-cli` em `doctor`. Para a CLI Playwright local, execute `bash scripts/playwright.sh --version`. Um status positivo indica disponibilidade, não que todas as URLs serão acessíveis.

O conjunto ensaiado nesta versão foi yt-dlp 2026.08.19, EJS 0.8.0 e Playwright CLI 0.1.20. `requirements.txt` aceita yt-dlp a partir de 2026.8.19 e pode resolver uma versão mais nova; o npm instala a versão de Playwright indicada no instalador. Preserve configurações privadas antes de atualizar a skill e repita um ensaio da rota utilizada se as dependências mudarem.

Se `--check` falhar, instale o executável/versão apontado. Se a extração falhar, confirme primeiro que a URL abre no navegador autorizado; confira instalação, sessão e disponibilidade do post. Não peça chave YouTube. Para URL CDN Instagram expirada, recapture os dois canais e siga o guia de recuperação. Os testes reais documentados estão em [QA](QA.md).
