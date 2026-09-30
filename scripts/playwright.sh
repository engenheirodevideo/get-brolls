#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# `setup --where tools` says which .tools is in use ($GB_HOME/runtime or GB_RUNTIME_DIR).
source "$ROOT/scripts/getbrolls/tools/youtube/_runtime.sh"
CLI="$(gb_where_executable tools)" || CLI=""
if [ -z "$CLI" ] || [ ! -x "$CLI" ]; then
  printf 'Playwright CLI ausente. Rode python3 "%s/scripts/gb.py" setup\n' "$ROOT" >&2
  exit 1
fi
exec "$CLI" "$@"
