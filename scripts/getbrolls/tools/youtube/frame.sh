#!/usr/bin/env bash
# frame.sh — grab a single still frame at a timestamp WITHOUT downloading the
# whole video. Fast fallback preview when the browser is not the confirm surface.
# Usage: frame.sh <video_id> <HH:MM:SS> [out.jpg]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_runtime.sh"
id="${1:?usage: frame.sh <video_id> <HH:MM:SS> [out.jpg]}"
ts="${2:?timestamp HH:MM:SS}"
out="${3:-preview_${id}_${ts//:/-}.jpg}"
url=$(gb_ytdlp --no-warnings -f "b[height<=720][ext=mp4]/b[ext=mp4]/b" -g \
  "https://www.youtube.com/watch?v=${id}" | head -1)
ffmpeg -y -ss "$ts" -i "$url" -frames:v 1 -q:v 3 "$out" 2>/dev/null
echo "$out"
