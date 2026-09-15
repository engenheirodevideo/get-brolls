#!/usr/bin/env bash
# gb_search.sh — search YouTube for B-roll candidates (no download).
# Usage: gb_search.sh "<query>" [count]
# Output: one line per candidate -> "ID | DURATION | TITLE"
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_runtime.sh"
q="${1:?usage: gb_search.sh \"<query>\" [count]}"
n="${2:-6}"
gb_ytdlp --no-warnings --flat-playlist \
  --print "%(id)s | %(duration_string)s | %(title)s" \
  "ytsearch${n}:${q}"
