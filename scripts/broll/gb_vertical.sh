#!/usr/bin/env bash
# gb_vertical.sh — convert a landscape clip to 9:16 (1080x1920) with a blurred
# fill background, ready for Reels/Shorts.
# Usage: gb_vertical.sh <in.mp4> [out.mp4]
set -euo pipefail
in="${1:?usage: gb_vertical.sh <in.mp4> [out.mp4]}"
out="${2:-${in%.*}_vertical.mp4}"
ffmpeg -y -i "$in" -filter_complex \
"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];\
[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];\
[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1" \
-c:a copy "$out"
echo "saved: $out"
