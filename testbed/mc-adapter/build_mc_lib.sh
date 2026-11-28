#!/usr/bin/env bash
# Build static library `/tmp/mrv206-upstream/libmrv206_mc.a` from 100% unmodified
# upstream Memcached (`github.com/memcached/memcached`) plus the minimal MRV-206
# transport adapter (`mrv206_mc_adapter.c`).
#
# Run inside `devenv shell` from `/home/hongtao/dev/marvell`.
set -euo pipefail

MC_SRC="${MC_SRC:-/tmp/mrv206-upstream/memcached}"
LIBEVENT_PREFIX="${LIBEVENT_PREFIX:-/tmp/mrv206-upstream/libevent-prefix}"
ADAPTER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-/tmp/mrv206-upstream/mc-objs}"
OUT_LIB="${OUT_LIB:-/tmp/mrv206-upstream/libmrv206_mc.a}"

if [ ! -f "$MC_SRC/config.h" ]; then
    echo "ERROR: $MC_SRC/config.h missing; configure upstream memcached first." >&2
    exit 1
fi

rm -rf "$OUT_DIR" && mkdir -p "$OUT_DIR"

# Verify upstream repo has zero modifications to tracked source files.
UPSTREAM_DIFF="$(git -C "$MC_SRC" diff --stat)"
if [ -n "$UPSTREAM_DIFF" ]; then
    echo "ERROR: Upstream Memcached repo $MC_SRC has local modifications:" >&2
    echo "$UPSTREAM_DIFF" >&2
    exit 1
fi

CFLAGS="-DHAVE_CONFIG_H -DNDEBUG -O3 -march=native -mno-rtm -pthread -I$MC_SRC -I$LIBEVENT_PREFIX/include -I$ADAPTER_DIR"

# All unmodified upstream Memcached core compilation units (except memcached.c,
# which is included verbatim by mrv206_mc_adapter.c with main renamed).
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

echo "Building unmodified upstream Memcached ($(git -C "$MC_SRC" rev-parse --short=12 HEAD)) objects..."
PIDS=()
for src in "${UPSTREAM_SRCS[@]}"; do
    obj="$OUT_DIR/$(basename "$src" .c).o"
    gcc $CFLAGS -c "$MC_SRC/$src" -o "$obj" &
    PIDS+=($!)
done
gcc $CFLAGS -c "$ADAPTER_DIR/mrv206_mc_adapter.c" -o "$OUT_DIR/mrv206_mc_adapter.o" &
PIDS+=($!)
for pid in "${PIDS[@]}"; do
    wait "$pid"
done

rm -f "$OUT_LIB"
ar rcs "$OUT_LIB" "$OUT_DIR"/*.o
echo "Built $OUT_LIB ($(stat -c%s "$OUT_LIB") bytes, upstream git diff: 0 lines)"
