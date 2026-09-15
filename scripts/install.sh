#!/usr/bin/env bash
# Dependencies are installed from PyPI/npm on the recipient's computer, never vendored.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-}" in ''|--check) ;; *) printf 'Uso: bash scripts/install.sh [--check]\n'; exit 2;; esac
missing=0
for tool in python3 ffmpeg ffprobe curl bash awk node npm npx; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf 'MISSING: %s\n' "$tool"; missing=1
  fi
done
if command -v node >/dev/null 2>&1; then
  node_version="$(node --version)"
  node_major="${node_version#v}"; node_major="${node_major%%.*}"
  if ! [[ "$node_major" =~ ^[0-9]+$ ]] || [ "$node_major" -lt 22 ]; then
    printf 'MISSING: Node 22+ (encontrado %s)\n' "$node_version"; missing=1
  fi
fi
if [ "$missing" -ne 0 ]; then
  printf 'Instale os executáveis conforme INSTALL.md e repita.\n'; exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ obrigatório"'
if [ "${1:-}" = "--check" ]; then
  printf 'Pré-requisitos do instalador encontrados; check não instala nem testa rede/login.\n'
  exit 0
fi
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
"$ROOT/.venv/bin/python" -c 'import yt_dlp, yt_dlp_ejs; print("yt-dlp e EJS importados")'
# Local npm cache avoids changing permissions or global npm configuration.
npm --cache "$ROOT/.tools/npm-cache" install --prefix "$ROOT/.tools" --no-audit --no-fund --save-exact @playwright/cli@0.1.20
bash "$ROOT/scripts/playwright.sh" --version
PATH="$ROOT/.venv/bin:$PATH" "$ROOT/.venv/bin/python" "$ROOT/scripts/gb.py" doctor
printf '\nDependências instaladas em .venv/ e .tools/, não fazem parte dos arquivos de distribuição.\n'
printf 'Navegador existente: siga INSTALL.md para reutilizar a sessão autorizada.\n'
