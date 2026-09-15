#!/usr/bin/env bash
# Shared yt-dlp runtime preserving the original helper arguments.
GB_RUNTIME_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
gb_ytdlp() {
  local exe="yt-dlp"
  if [ -x "$GB_RUNTIME_ROOT/.venv/bin/yt-dlp" ]; then exe="$GB_RUNTIME_ROOT/.venv/bin/yt-dlp"; fi
  if command -v deno >/dev/null 2>&1; then
    "$exe" --js-runtimes deno "$@"
  elif command -v node >/dev/null 2>&1; then
    "$exe" --js-runtimes node "$@"
  else
    "$exe" "$@"
  fi
}
