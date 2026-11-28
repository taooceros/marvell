{ pkgs, config, ... }:

let
  root = config.devenv.root;
  # Card rootfs headers/libs copied by `card-sysroot-sync`; the single source of
  # truth for cross builds (pkg-config, bindgen, cc and the linker all use it).
  sysroot = "${root}/sysroot-card";
  # Ubuntu's gcc-aarch64-linux-gnu: with --sysroot it links against the card's
  # own glibc. A Nix cross gcc would bake a /nix/store loader into the binary.
  crossGcc = "/usr/bin/aarch64-linux-gnu-gcc";
in
{
  # Host-side build environment for the Marvell workspace. Root-only steps
  # (hugepages, vfio-pci, SR-IOV) stay outside the shell.
  packages = with pkgs; [
    gcc
    meson
    ninja
    pkg-config
    numactl
    (python3.withPackages (ps: [ ps.pyelftools ]))
  ];

  # DPDK builds with -march=native; the Nix cc wrapper strips it unless told not to.
  env.NIX_ENFORCE_NO_NATIVE = "0";
  # bindgen (testbed/crates/dpdk-sys)
  env.LIBCLANG_PATH = "${pkgs.llvmPackages.libclang.lib}/lib";

  # Rust testbed, card target: Cargo/pkg-config/cc read these per-target variables.
  env.PKG_CONFIG_SYSROOT_DIR_aarch64_unknown_linux_gnu = sysroot;
  env.PKG_CONFIG_LIBDIR_aarch64_unknown_linux_gnu =
    "${sysroot}/usr/lib/aarch64-linux-gnu/pkgconfig:${sysroot}/usr/share/pkgconfig";
  env.CC_aarch64_unknown_linux_gnu = crossGcc;
  env.CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER = crossGcc;
  env.CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUSTFLAGS = "-C link-arg=--sysroot=${sysroot}";

  # Rust testbed, host target. Cross-language LTO: the C wrappers around DPDK's
  # static inline fast path (dpdk-sys static_fns.c) are compiled to LLVM bitcode
  # by the clang matching rustc's LLVM, and lld inlines them into the Rust code.
  # Two conditions, both needed:
  # - Same LTO kind as the Rust side (`lto = "fat"` in testbed/Cargo.toml): a
  #   regular-LTO module cannot import from a ThinLTO one, so the wrappers use
  #   -flto, not -flto=thin.
  # - Callee target features a subset of the caller's. libdpdk.pc adds -mrtm on
  #   x86, but saturn's CPU has TSX disabled (no rtm in /proc/cpuinfo), so
  #   rustc's target-cpu=native leaves rtm off and forcing it needs the unstable
  #   `-C target-feature=+rtm`. -mno-rtm on the wrappers is the clean side to
  #   fix; none of them use RTM.
  # Result (2026-11-10): rdtsc and tx_burst inline; rx_burst, pktmbuf_alloc/
  # append/free stay calls (not investigated). Rate is within noise of the C
  # client either way, so this is tidiness, not speed.
  # The linker driver stays the Nix gcc wrapper (and its wrapped ld.lld) so the
  # binaries keep a RUNPATH to /nix/store libs such as libnuma; rustc's own
  # rust-lld would bypass that wrapper.
  env.CC_x86_64_unknown_linux_gnu = "${pkgs.llvmPackages.clang}/bin/clang";
  env.AR_x86_64_unknown_linux_gnu = "${pkgs.llvmPackages.bintools-unwrapped}/bin/llvm-ar";
  env.CFLAGS_x86_64_unknown_linux_gnu = "-flto -mno-rtm";
  env.CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS = builtins.concatStringsSep " " [
    "-C target-cpu=native" # same ISA as DPDK's -march=native
    "-C linker-features=-lld"
    "-C linker-plugin-lto"
    "-C link-arg=-fuse-ld=lld"
    "-C link-arg=-B${pkgs.llvmPackages.bintools}/bin" # wrapped ld.lld, not on PATH
  ];

  # Host target: the in-tree (uninstalled) DPDK build. In secondary jj
  # workspaces (`../marvell-<issue>`), fall back to the sibling main checkout
  # (`../marvell`) for the gitignored `marvell-dpdk/` and `sysroot-card/` trees.
  enterShell = ''
    _mrv_dpdk_root="${root}"
    if [ ! -d "$_mrv_dpdk_root/marvell-dpdk/build-host/meson-uninstalled" ] && [ -d "${root}/../marvell/marvell-dpdk/build-host/meson-uninstalled" ]; then
      _mrv_dpdk_root="$(cd "${root}/../marvell" && pwd)"
    fi
    export PKG_CONFIG_PATH="$_mrv_dpdk_root/marvell-dpdk/build-host/meson-uninstalled''${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"

    _mrv_sysroot="${sysroot}"
    if [ ! -d "$_mrv_sysroot/usr/lib/aarch64-linux-gnu/pkgconfig" ] && [ -d "${root}/../marvell/sysroot-card/usr/lib/aarch64-linux-gnu/pkgconfig" ]; then
      _mrv_sysroot="$(cd "${root}/../marvell/sysroot-card" && pwd)"
      export PKG_CONFIG_SYSROOT_DIR_aarch64_unknown_linux_gnu="$_mrv_sysroot"
      export PKG_CONFIG_LIBDIR_aarch64_unknown_linux_gnu="$_mrv_sysroot/usr/lib/aarch64-linux-gnu/pkgconfig:$_mrv_sysroot/usr/share/pkgconfig"
      export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUSTFLAGS="-C link-arg=--sysroot=$_mrv_sysroot"
    fi
  '';

  scripts.dpdk-host-build.exec = ''
    set -euo pipefail
    cd "$DEVENV_ROOT/marvell-dpdk"
    [ -d build-host ] || meson setup build-host \
      -Dplatform=native \
      -Denable_drivers=bus/pci,bus/vdev,mempool/ring,mempool/stack,net/octeon_ep,net/null,net/ring \
      -Denable_apps=test-pmd,proc-info \
      -Dtests=false -Dexamples=
    ninja -C build-host
  '';

  scripts.card-sysroot-sync.exec = ''
    set -euo pipefail
    # Card DPDK installs its headers in /include (prefix=/ in libdpdk.pc); libc.so
    # names the loader as /lib/ld-linux-aarch64.so.1 (lib -> usr/lib).
    rm -rf "${sysroot}" && mkdir -p "${sysroot}"
    ssh -o BatchMode=yes root@10.10.0.2 \
      'tar -C / -cf - include usr/include usr/lib/aarch64-linux-gnu usr/lib/gcc/aarch64-linux-gnu usr/lib/ld-linux-aarch64.so.1' \
      | tar -C "${sysroot}" -xf -
    ln -s usr/lib "${sysroot}/lib"
    du -sh "${sysroot}"
  '';
}
