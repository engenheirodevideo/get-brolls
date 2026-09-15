#!/usr/bin/env bash
# gb_verify.sh — list duration + resolution of every mp4 in a folder.
# Usage: gb_verify.sh [dir]
set -euo pipefail
dir="${1:-.}"
for f in "$dir"/*.mp4; do
  [ -e "$f" ] || continue
  d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" | cut -d. -f1)
  r=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "$f")
  printf "%s | %ss | %s\n" "$(basename "$f")" "$d" "$r"
done
du -sh "$dir"
