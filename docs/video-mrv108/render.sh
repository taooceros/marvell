#!/usr/bin/env bash
# Full 1080p render -> out/mrv108-card-dsa.mp4. Narration must exist first (see SCRIPT.md "Build").
set -euo pipefail
cd "$(dirname "$0")"
npx tsc -p .
npx remotion render src/index.ts Mrv108CardDsa out/mrv108-card-dsa.mp4 --concurrency=16 --codec=h264 --audio-codec=aac "$@"
