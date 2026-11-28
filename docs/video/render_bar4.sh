#!/usr/bin/env bash
# Full 1080p30 render -> out/mrv258-bar4-polling-walls.mp4. Narration must exist first (see SCRIPT_BAR4.md "Build").
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p out
npx tsc -p .
npx remotion render src/index.ts Bar4PollingWalls out/mrv258-bar4-polling-walls.mp4 --concurrency=16 --codec=h264 --audio-codec=aac "$@"
