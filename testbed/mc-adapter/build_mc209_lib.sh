#!/usr/bin/env bash
# Build static library `/tmp/mrv209-upstream/libmrv209_mc.a` from 100% unmodified
# upstream Memcached (`github.com/memcached/memcached`) plus the minimal MRV-209
# transport adapter (`mrv209_mc_adapter.c`).
#
# Pins compilation jobs to Socket 0 (cpus 0-31) with max 4 concurrent jobs so
# Socket 1 (cpus 32-63) stays completely undisturbed.
set -euo pipefail

MC_SRC="${MC_SRC:-/tmp/mrv206-upstream/memcached}"
LIBEVENT_PREFIX="${LIBEVENT_PREFIX:-/tmp/mrv206-upstream/libevent-prefix}"
ADAPTER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-/tmp/mrv209-upstream/mc-objs}"
OUT_LIB="${OUT_LIB:-/tmp/mrv209-upstream/libmrv209_mc.a}"
MAX_JOBS="${CARGO_BUILD_JOBS:-4}"

if [ ! -f "$MC_SRC/config.h" ]; then
    echo "ERROR: $MC_SRC/config.h missing; configure upstream memcached first." >&2
    exit 1
fi

mkdir -p "$(dirname "$OUT_LIB")"
rm -rf "$OUT_DIR" && mkdir -p "$OUT_DIR"

# Verify upstream repo has zero modifications to tracked source files.
UPSTREAM_DIFF="$(git -C "$MC_SRC" diff --stat)"
if [ -n "$UPSTREAM_DIFF" ]; then
    echo "ERROR: Upstream Memcached repo $MC_SRC has local modifications:" >&2
    echo "$UPSTREAM_DIFF" >&2
    exit 1
fi

CFLAGS="-DHAVE_CONFIG_H -DNDEBUG -O3 -march=native -mno-rtm -pthread -I$MC_SRC -I$LIBEVENT_PREFIX/include -I$ADAPTER_DIR"

UPSTREAM_SRCS=(
    hash.c
    jenkins_hash.c
    murmur3_hash.c
    slabs.c
    items.c
    assoc.c
    thread.c
    daemon.c
    stats_prefix.c
    util.c
    cache.c
    bipbuffer.c
    base64.c
    logger.c
    crawler.c
    itoa_ljust.c
    slab_automove.c
    slabs_mover.c
    authfile.c
    restart.c
    proto_text.c
    proto_bin.c
    proto_parser.c
    vendor/mcmc/mcmc.c
    extstore.c
    crc32c.c
    storage.c
    slab_automove_extstore.c
)

echo "Building unmodified upstream Memcached ($(git -C "$MC_SRC" rev-parse --short=12 HEAD)) objects on Socket 0 (jobs=$MAX_JOBS)..."
PIDS=()
RUNNING=0
for src in "${UPSTREAM_SRCS[@]}"; do
    obj="$OUT_DIR/$(basename "$src" .c).o"
    extra=""
    if [ "$src" = "vendor/mcmc/mcmc.c" ]; then
        extra="-Wno-discarded-qualifiers"
    fi
    taskset -c 0-31 gcc $CFLAGS $extra -c "$MC_SRC/$src" -o "$obj" &
    PIDS+=($!)
    RUNNING=$((RUNNING + 1))
    if [ "$RUNNING" -ge "$MAX_JOBS" ]; then
        wait -n
        RUNNING=$((RUNNING - 1))
    fi
done
taskset -c 0-31 gcc $CFLAGS -c "$ADAPTER_DIR/mrv209_mc_adapter.c" -o "$OUT_DIR/mrv209_mc_adapter.o" &
PIDS+=($!)
for pid in "${PIDS[@]}"; do
    wait "$pid"
done

rm -f "$OUT_LIB"
taskset -c 0-31 ar rcs "$OUT_LIB" "$OUT_DIR"/*.o
echo "Built $OUT_LIB ($(stat -c%s "$OUT_LIB") bytes, upstream git diff: 0 lines)"
