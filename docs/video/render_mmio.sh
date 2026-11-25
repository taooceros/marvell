#!/usr/bin/env bash
# Full 1080p render -> out/mmio-latency-breakdown.mp4. Narration must exist first (see SCRIPT_MMIO.md "Build").
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p out
npx tsc -p .
npx remotion render src/index.ts MmioLatencyBreakdown out/mmio-latency-breakdown.mp4 --concurrency=16 --codec=h264 --audio-codec=aac "$@"
