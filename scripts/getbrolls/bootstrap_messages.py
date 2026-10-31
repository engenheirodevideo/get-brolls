"""Textos, tetos e tabelas constantes do `setup` (sem lógica): mensagens ao usuário, marcadores de falha do pip
e dicas de instalação das ferramentas do sistema. `bootstrap` reexporta tudo daqui."""

import re

from .config import TOOL_PATH_KEYS

# Executável do `doctor` → id do passo; os que não estão aqui usam o próprio nome.
STEP_IDS = {"yt-dlp": "ytdlp", "playwright-cli": "playwright"}
# Passos que o runtime da instalação resolve (vêm primeiro); os outros são do sistema.
RUNTIME_STEPS = ("ytdlp", "playwright")
# Pin → executável que ele fixa: um pin quebrado reprova o passo, como no `doctor`.
_PIN_TOOLS = {**{key: name for name, key in TOOL_PATH_KEYS.items()}, "GB_VENV_PATH": "yt-dlp"}
# Pins que tiram a `.venv` das mãos do `setup`.
_VENV_PINS = ("GB_YTDLP_PATH", "GB_VENV_PATH")

PART_STEP = {"venv": "ytdlp", "tools": "playwright"}
# Teto de cada passo, em segundos; estourou → a árvore do processo é encerrada (código 124).
STEP_TIMEOUT_S = {"venv": 300, "pip": 1200, "npm": 1200, "probe": 60, "health": 20}
# Sonda barata da venv em uso: o Python dela importa o yt_dlp? (`venv_health`)
HEALTH_LABEL = "health"
MIN_NODE_MAJOR = 22
# argv do npm: constante (sem caminho), então passa pela regra do `.cmd` no Windows; a
# pasta vai no `cwd` (o npm usa o `package.json` dali) e o cache, pelo ambiente.
_NPM_ARGS = ("ci", "--ignore-scripts", "--no-audit", "--no-fund")
_TOOLS_FILES = ("package.json", "package-lock.json")
NPM_CACHE = ".npm-cache"
TAIL_LINES = 40
# Linhas `ERROR:` do passo (e a linha seguinte, que nomeia o pacote) que sobrevivem ao corte da cauda: o pip lista um
# `Expected sha256` por hash do pacote (até 170), e sem isso a cauda perderia o diagnóstico de um arquivo adulterado.
KEPT_ERROR_LINES = 8
TIMED_OUT = 124
NOT_FOUND = 127
LOCK_NAME = ".getbrolls-setup.lock"
LOW_DISK_BYTES = 1 << 30
_PIP_FLAGS = ("--disable-pip-version-check", "--no-input", "--progress-bar", "off")
_HASHED_FLAGS = ("--require-hashes", "--only-binary", ":all:")
_UPGRADE_SPECS = ("yt-dlp[default]", "yt-dlp-ejs")
_BATCH_SAFE = re.compile(r"[A-Za-z0-9_\-=.]+")
# `PIP_NO_BINARY` traria o sdist de volta (e as ferramentas de construção sem hash) ao passo do pip.
_ENV_DROPPED = ("PYTHONPATH", "PYTHONHOME", "PIP_NO_BINARY")
# Segredos que o passo do npm precisa: o `.npmrc` de um registro privado costuma ler
# `${NPM_TOKEN}`. Os outros `*_TOKEN`/`*_KEY` continuam de fora de todo passo.
_NPM_SECRETS = ("NPM_TOKEN",)

PIP_VERSION_FAILED = (
    "Falha ao instalar as dependências Python: seu Python é {version}; o conjunto fixado foi validado "
    "em Python 3.11–3.13. Rode o getbrolls com um Python dessa faixa ou atualize requirements.txt "
    "como um conjunto revisado."
)
PIP_NETWORK_FAILED = (
    "Falha ao baixar as dependências Python: sem acesso à rede ou ao índice de pacotes (PyPI, proxy, "
    "certificado). Confira a conexão e rode o `setup` de novo."
)
PIP_CONFLICT_FAILED = (
    "Falha ao instalar as dependências Python: o pip não achou um conjunto compatível (conflito entre "
    "versões ou com o arquivo de restrições requirements.txt). Isso não é sobre a versão do seu Python; "
    "veja `output_tail` e revise o conjunto fixado."
)
PIP_HASH_FAILED = (
    "Falha ao instalar as dependências Python: um arquivo baixado não confere com o sha256 fixado em {file}. "
    "Não instale por fora: atualize o getbrolls e rode o `setup` de novo; se repetir, algo na rede pode estar "
    "alterando os downloads."
)
PIP_FAILED = "Falha ao instalar as dependências Python (pip saiu com {code}); veja `output_tail`."
ENSUREPIP_MISSING = (
    "O Python que roda o getbrolls não consegue criar venv (falta o ensurepip). No Debian/Ubuntu, "
    "instale o pacote `python3-venv` (ou `python3.X-venv` da sua versão) e rode o `setup` de novo."
)
VENV_FAILED = "Falha ao criar a venv (python -m venv saiu com {code}); veja `output_tail`."
PROBE_FAILED = "A venv foi criada, mas o yt-dlp não respondeu; veja `output_tail`."
STEP_TIMED_OUT = "O passo `{label}` passou do tempo limite ({seconds} s) e foi encerrado."
LOCKED = "Outro `setup` está instalando nesta pasta agora; espere ele terminar e rode de novo."
ROOT_IS_LINK = (
    "A pasta do runtime ({folder}) é um link (ou junção); o `setup` não instala através de link. "
    "Remova o link à mão ou aponte GB_RUNTIME_DIR para uma pasta de verdade."
)
FOREIGN_BROKEN = (
    "A pasta do runtime ({folder}) já existe, não foi criada pelo getbrolls e está incompleta; "
    "apague essa pasta ou aponte GB_RUNTIME_DIR para outra."
)
NOT_A_FOLDER = (
    "A pasta do runtime ({folder}) é um link ou não é uma pasta comum; o `setup` não apaga isso. "
    "Remova o link à mão ou aponte GB_RUNTIME_DIR para outra pasta."
)
PINNED = "yt-dlp fixado por {key}: o `setup` não mexe na venv."
UPGRADE_PINNED = (
    "yt-dlp está fixado por {key} (ambiente, .env ou perfil): `setup --upgrade ytdlp` só atualiza a venv "
    "gerenciada. Atualize o yt-dlp desse caminho à mão ou remova a variável."
)
UPGRADE_FOREIGN = "A venv em uso não foi criada pelo getbrolls: `setup --upgrade ytdlp` só atualiza a venv gerenciada."
UPGRADE_FAILED = "A atualização do yt-dlp falhou; a venv foi refeita na versão fixada. {reason}"
UPGRADE_REBUILD_FAILED = (
    "A atualização do yt-dlp falhou e a venv não pôde ser refeita na versão fixada: ela foi removida e o "
    "yt-dlp do getbrolls não está pronto. Rode `{fix}` quando a causa for resolvida. Atualização: {reason} "
    "Refazer: {rebuild}"
)
UPGRADE_UNCHANGED = (
    "O yt-dlp continua na versão {version}: ou ela já é a mais nova, ou o índice de pacotes não respondeu "
    "(rede, proxy). Nada mudou na venv."
)
VENV_BROKEN = (
    "A venv do yt-dlp em uso não funciona (o Python dela não importa o yt_dlp; ele pode ter sido removido "
    "ou atualizado). Rode `{fix}` para refazê-la."
)
VENV_PIN_BROKEN = (
    "A venv de GB_VENV_PATH não funciona (o Python dela não importa o yt_dlp). Conserte essa venv ou remova a variável."
)
VENV_EXPECTED = (
    "A venv gerenciada do yt-dlp está pela metade (marcador `{status}`): o yt-dlp do PATH não a substitui. "
    "Rode `{fix}`."
)
YTDLP_FROM_PATH = (
    "yt-dlp do PATH, fora do runtime do getbrolls: vale, mas a versão é a desse executável; `setup` instala a "
    "versão fixada na venv gerenciada."
)
NPM_MISSING = (
    "npm não encontrado no PATH: o Playwright CLI (Instagram) ficou de fora. Instale o Node {major}+ "
    "(ele traz o npm) e rode o `setup` de novo."
)
NODE_MISSING = (
    "Node não encontrado no PATH: o Playwright CLI (Instagram) precisa de Node {major}+. Instale e rode o "
    "`setup` de novo."
)
NODE_TOO_OLD = (
    "O Playwright CLI (Instagram) precisa de Node {major}+; o Node do PATH é {version}. Atualize e rode o "
    "`setup` de novo."
)
NPM_FAILED = (
    "Falha ao instalar o Playwright CLI (npm ci saiu com {code}); veja `output_tail`. Sem acesso ao "
    "registro do npm (rede, proxy, certificado), confira a conexão e rode o `setup` de novo."
)
TOOLS_PROBE_FAILED = "O npm terminou, mas o Playwright CLI não respondeu; veja `output_tail`."
LOW_DISK = "Pouco espaço livre na pasta do runtime ({free} MiB; recomendado 1 GiB ou mais)."

_NETWORK_MARKERS = (
    "newconnectionerror",
    "max retries exceeded",
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname",
    "could not fetch url",
    "connecttimeout",
    "readtimeouterror",
    "proxyerror",
    "sslerror",
    "certificate_verify_failed",
    "network is unreachable",
    "connection refused",
)
# O pip com `--require-hashes`: arquivo cujo sha256 não confere, ou requisito sem hash nenhum.
_HASH_MARKERS = ("do not match the hashes", "hashes are required")
_VERSION_MARKERS = ("requires-python", "requires a different python")
# Conflito de resolução/restrições: não é a versão do Python nem a rede.
_CONFLICT_MARKERS = ("resolutionimpossible", "conflicting dependencies", "constraint file", "constraints.txt")
# "No matching distribution" só aponta o Python quando o pip cita a etiqueta/versão dele.
_PYTHON_TAG_HINTS = ("python_tag", "no wheel for tag", "different python", "python version", " cp3")

# Ferramenta do sistema → sistema operacional → como instalar. Só dica: o `setup` não instala.
_NODE_HINTS = {
    "darwin": "brew install node",
    "linux": "instale o Node 22+ (nodejs.org ou o gerenciador da distribuição)",
    "nt": "winget install --id OpenJS.NodeJS.LTS -e",
}
_FFMPEG_HINTS = {
    "darwin": "brew install ffmpeg",
    "linux": "sudo apt install ffmpeg",
    "nt": "winget install Gyan.FFmpeg",
}
SYSTEM_HINTS: dict[str, dict[str, str]] = {
    "ffmpeg": _FFMPEG_HINTS,
    "ffprobe": _FFMPEG_HINTS,
    "curl": {"darwin": "brew install curl", "linux": "sudo apt install curl", "nt": "winget install --id cURL.cURL -e"},
    "node": _NODE_HINTS,
    "npx": _NODE_HINTS,
    "npm": _NODE_HINTS,
}
