#!/usr/bin/env bash
# Thin wrapper: confere o sistema e chama `gb.py setup`, que instala em $GB_HOME/runtime/<versão>/ (ou GB_RUNTIME_DIR).
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
  printf 'Instale os executáveis conforme docs/GUIDE.md e repita.\n'; exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ obrigatório"'
if ! ffmpeg -hide_banner -filters 2>/dev/null | grep -q ' drawtext '; then
  printf 'AVISO: FFmpeg sem o filtro drawtext (libfreetype): o contact sheet sai sem número e timecode nas células. Reinstale o FFmpeg com freetype (Homebrew: brew reinstall ffmpeg; Ubuntu: apt install ffmpeg).\n'
fi
if [ "${1:-}" = "--check" ]; then
  printf 'Pré-requisitos do instalador encontrados; check não instala nem testa rede/login.\n'
  exit 0
fi
# setup sai 4 quando falta item do sistema (FFmpeg, Node…): o runtime saiu do mesmo jeito.
setup_status=0
python3 "$ROOT/scripts/gb.py" setup || setup_status=$?
if [ "$setup_status" -ne 0 ] && [ "$setup_status" -ne 4 ]; then
  printf 'setup falhou (código %s); veja a mensagem acima.\n' "$setup_status" >&2
  exit "$setup_status"
fi
if [ "$setup_status" -eq 0 ]; then bash "$ROOT/scripts/playwright.sh" --version; fi
# doctor sai 4 quando summary.missing não está vazio (faltam itens), com o JSON em stdout.
status=0
doctor_json="$(python3 "$ROOT/scripts/gb.py" doctor)" || status=$?
printf '%s\n' "$doctor_json"
if [ "$status" -eq 4 ]; then
  printf '\ndoctor encontrou pendências (código 4: faltam itens). O que falta e como resolver, de summary.missing:\n' >&2
  printf '%s' "$doctor_json" \
    | python3 -c 'import json, sys; [print("- %s: %s" % (m["item"], m["fix"])) for m in json.load(sys.stdin)["summary"]["missing"]]' >&2 || true
fi
if [ "$status" -ne 0 ]; then
  exit "$status"
fi
printf '\nDependências em $GB_HOME/runtime/ (ou GB_RUNTIME_DIR), compartilhadas entre instalações; fora do repositório.\n'
printf 'Navegador existente: siga docs/GUIDE.md para reutilizar a sessão autorizada.\n'
