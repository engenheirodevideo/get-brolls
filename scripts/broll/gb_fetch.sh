#!/usr/bin/env bash
# gb_fetch.sh — download a trimmed 1080p mp4 segment from YouTube.
# Usage: gb_fetch.sh <video_id> <START-END> <outname> [outdir]
#   START-END example: 00:15-00:35  (HH:MM:SS-HH:MM:SS or MM:SS-MM:SS)
# Result: <outdir>/<outname>.mp4
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_runtime.sh"
id="${1:?usage: gb_fetch.sh <video_id> <START-END> <outname> [outdir]}"
span="${2:?span START-END e.g. 00:15-00:35}"
name="${3:?output basename}"
dir="${4:-.}"
mkdir -p "$dir"
FMT="bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b"
gb_ytdlp --no-warnings -f "$FMT" \
  --download-sections "*${span}" --force-keyframes-at-cuts \
  -o "${dir}/${name}.%(ext)s" \
  "https://www.youtube.com/watch?v=${id}"
echo "saved: ${dir}/${name}.mp4"
