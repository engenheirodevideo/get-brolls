#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/.tools/node_modules/.bin/playwright-cli"
if [ ! -x "$CLI" ]; then
  printf 'Playwright CLI ausente. Execute bash "%s/scripts/install.sh"\n' "$ROOT" >&2
  exit 1
fi
exec "$CLI" "$@"
