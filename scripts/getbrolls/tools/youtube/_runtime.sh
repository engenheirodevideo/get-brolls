#!/usr/bin/env bash
# Shared yt-dlp runtime for the Get B-rolls YouTube helpers.
# The venv lives in $GB_HOME/runtime (or GB_RUNTIME_DIR); `setup --where venv` says
# which one is in use. A GB_PROFILE inherited from getbrolls is kept; run by hand, no
# workspace profile applies (`--profile off`).
GB_RUNTIME_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"

# Prints `in_use.executable` of `setup --where <part>` (empty when there is none).
gb_where_executable() (
  set -o pipefail
  local part="$1" json
  local args=(setup --where "$part")
  if [ -z "${GB_PROFILE:-}" ]; then args=(--profile off "${args[@]}"); fi
  if [ -f "$GB_RUNTIME_ROOT/scripts/gb.py" ]; then
    json="$(python3 "$GB_RUNTIME_ROOT/scripts/gb.py" "${args[@]}")" || json=""
  elif command -v getbrolls >/dev/null 2>&1; then
    json="$(getbrolls "${args[@]}")" || json=""
  else
    return 1
  fi
  if [ -z "$json" ]; then
    printf 'getbrolls setup --where %s não devolveu JSON; confira a instalação com o doctor.\n' "$part" >&2
    return 1
  fi
  printf '%s' "$json" | python3 -c '
import json, sys
try:
    where = json.load(sys.stdin)
except ValueError:
    sys.exit("getbrolls setup --where devolveu algo que não é JSON; confira a instalação com o doctor.")
print((where.get("in_use") or {}).get("executable") or "")
'
)

gb_ytdlp() {
  local exe="yt-dlp" found
  if found="$(gb_where_executable venv)" && [ -n "$found" ] && [ -x "$found" ]; then exe="$found"; fi
  if command -v deno >/dev/null 2>&1; then
    "$exe" --js-runtimes deno "$@"
  elif command -v node >/dev/null 2>&1; then
    "$exe" --js-runtimes node "$@"
  else
    "$exe" "$@"
  fi
}
