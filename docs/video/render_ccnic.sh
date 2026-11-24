#!/usr/bin/env bash
# Full 1080p render -> out/ccnic-paper-story.mp4. Narration must exist first (see SCRIPT_CCNIC.md "Build").
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p out
npx tsc -p .
npx remotion render src/index.ts CcnicPaperStory out/ccnic-paper-story.mp4 --concurrency=16 --codec=h264 --audio-codec=aac "$@"
