#!/usr/bin/env bash
# contact.sh — contact sheet of evenly-sampled frames across a segment (no full download).
# Streams a 720p source URL and tiles N frames into ONE image, with a TITLE banner (video
# title/id/window) and per-cell index+timecode, so you can read the motion AND confirm the
# subject/identity before committing to fetch.sh.
#
# Usage: contact.sh <video_id> <START-END> <out.jpg> [interval_sec] [cols]
#   START-END : MM:SS-MM:SS or HH:MM:SS-HH:MM:SS   e.g. 00:57-01:09
#   interval  : seconds per sampled frame (default 1.5). Drop to 0.5 for dense.
#   cols      : grid columns (default 3 → bigger cells, easier identity). 4 for wide.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_runtime.sh"

id="${1:?usage: contact.sh <video_id> <START-END> <out.jpg> [interval_sec] [cols]}"
span="${2:?span START-END e.g. 00:57-01:09}"
out="${3:?output path .jpg}"
interval="${4:-1.5}"
cols="${5:-3}"

start="${span%-*}"; end="${span##*-}"
tosec(){ awk -F: '{ s=0; for(i=1;i<=NF;i++) s=s*60+$i; printf "%.3f", s }' <<<"$1"; }
ss=$(tosec "$start"); es=$(tosec "$end")
dur=$(awk "BEGIN{printf \"%.3f\", $es-$ss}")
nframes=$(awk "BEGIN{n=int($dur/$interval + 0.9999); if(n<1)n=1; print n}")
fps=$(awk "BEGIN{printf \"%.6f\", 1/$interval}")
rows=$(awk "BEGIN{print int(($nframes + $cols - 1)/$cols)}")

FONT="${GB_FONT_FILE:-}"
if [ -n "$FONT" ] && [ ! -f "$FONT" ]; then
  echo "ERROR: GB_FONT_FILE não aponta para uma fonte existente: $FONT" >&2
  exit 1
fi
for f in /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
         /usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf \
         /usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf \
         /System/Library/Fonts/Supplemental/Arial.ttf \
         /System/Library/Fonts/Supplemental/Courier\ New.ttf \
         /Library/Fonts/Arial.ttf ; do
  [ -n "$FONT" ] && break
  [ -f "$f" ] && { FONT="$f"; break; }
done
if [ -z "$FONT" ] && command -v fc-match >/dev/null 2>&1; then
  FONT="$(fc-match -f '%{file}' sans | head -1)"
fi
if [ -z "$FONT" ] || [ ! -f "$FONT" ]; then
  echo "ERROR: nenhuma fonte encontrada para numerar o contact sheet; instale DejaVu/Liberation ou defina GB_FONT_FILE" >&2
  exit 1
fi

# fetch title + stream url
title=$(gb_ytdlp --no-warnings --print "%(title)s" "https://www.youtube.com/watch?v=${id}" 2>/dev/null | head -1)
url=$(gb_ytdlp --no-warnings -f "b[height<=720][ext=mp4]/b[ext=mp4]/b" -g \
  "https://www.youtube.com/watch?v=${id}" | head -1)

# banner text file (avoids drawtext escaping of the title)
tf="$(mktemp "${TMPDIR:-/tmp}/getbrolls-title.XXXXXX")"
trap 'rm -f "$tf"' EXIT
printf '%s\n[%s]  %s  window %s-%s' "$title" "$id" "" "$start" "$end" > "$tf"

cell="drawtext=fontfile=${FONT}:text='%{n}':x=8:y=8:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=6,"
banner=",pad=iw:ih+72:0:72:color=0x0b0b0b,drawtext=fontfile=${FONT}:textfile=${tf}:x=16:y=12:fontsize=26:fontcolor=white:line_spacing=8"

ffmpeg -y -hide_banner -loglevel error -ss "$ss" -i "$url" -t "$dur" \
  -vf "fps=${fps},scale=480:-2,${cell}tile=${cols}x${rows}:padding=10:margin=10:color=0x111111${banner}" \
  -frames:v 1 "$out"
rm -f "$tf"
trap - EXIT

echo "$out"
echo "TITLE: ${title}"
echo "${nframes} frames @ ${interval}s, ${cols}x${rows}  (window ${start}-${end}, ${dur}s)"
awk "BEGIN{ for(i=0;i<$nframes;i++){ t=$ss + i*$interval; m=int(t/60); s=t-m*60; printf \"  cell %2d = %d:%05.2f\n\", i, m, s } }"
