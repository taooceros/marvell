#!/usr/bin/env bash
# Layout check: render one scene at half size, then tile frames at the given (real) seconds.
#   ./stills.sh S02 3 10 16 22 30 36 41   ->  out/S02-sheet.png
set -euo pipefail
cd "$(dirname "$0")"
scene=$1
shift
mkdir -p out
npx remotion render src/index.ts "$scene" "out/$scene-preview.mp4" --scale=0.5 --concurrency=8 --log=error
inputs=()
for s in "$@"; do
  ffmpeg -v error -y -ss "$s" -i "out/$scene-preview.mp4" -frames:v 1 "out/$scene-$s.png"
  inputs+=("out/$scene-$s.png")
done
n=${#inputs[@]}
cols=$(( n < 3 ? n : 3 ))
rows=$(( (n + cols - 1) / cols ))
ffmpeg -v error -y $(printf -- '-i %s ' "${inputs[@]}") \
  -filter_complex "$(for i in $(seq 0 $((n - 1))); do printf '[%d:v]drawtext=text=%s:x=10:y=10:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.6[f%d];' "$i" "${@:$((i + 1)):1}s" "$i"; done)$(for i in $(seq 0 $((n - 1))); do printf '[f%d]' "$i"; done)xstack=inputs=$n:layout=$(for i in $(seq 0 $((n - 1))); do printf '%s%d_%d' "$([ "$i" -gt 0 ] && echo '|')" "$(( (i % cols) * 960 ))" "$(( (i / cols) * 540 ))"; done):fill=black" \
  "out/$scene-sheet.png"
rm -f "${inputs[@]}"
echo "out/$scene-sheet.png"
