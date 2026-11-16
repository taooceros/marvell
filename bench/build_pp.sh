#!/bin/sh
# Build dpdk_pingpong against the static libs of ../marvell-dpdk/build-host
# (libdpdk.pc --static drags in libpcap's libnl dependency, so link by hand).
# usage: devenv shell -- bench/build_pp.sh
set -eu
B=$DEVENV_ROOT/marvell-dpdk/build-host
cd "$DEVENV_ROOT/bench"
DRIVERS=$(ls $B/drivers/librte_*.a | grep -v '_lib\.a$')
gcc -O2 -Wall -march=native -include rte_config.h -o dpdk_pingpong dpdk_pingpong.c \
	$(PKG_CONFIG_PATH=$B/meson-uninstalled pkg-config --cflags libdpdk) \
	-Wl,--whole-archive $DRIVERS -Wl,--no-whole-archive \
	-Wl,--start-group $B/lib/librte_*.a -Wl,--end-group \
	-lnuma -lpcap -lm -ldl -pthread
ls -la dpdk_pingpong
