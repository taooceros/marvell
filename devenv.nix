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

  # Scaffold a minimal experiment crate under testbed/experiments/mrv<N>/.
  # Usage: mrv-new <issue-num|mrvN|MRV-N> [short description]
  scripts.mrv-new.exec = ''
    set -euo pipefail
    if [ "$#" -lt 1 ]; then
      echo "usage: mrv-new <issue-num|mrvN|MRV-N> [short description]" >&2
      exit 1
    fi
    raw="$1"
    shift
    num="$(printf '%s' "$raw" | sed -E 's/^[Mm][Rr][Vv]-?//')"
    if ! printf '%s' "$num" | grep -Eq '^[0-9]+$'; then
      echo "error: expected issue number (e.g. 265, mrv265, MRV-265), got '$raw'" >&2
      exit 1
    fi
    crate="mrv$num"
    issue="MRV-$num"
    desc="''${*:-$issue experiment}"
    dir="$DEVENV_ROOT/testbed/experiments/$crate"
    if [ -e "$dir" ]; then
      echo "error: $dir already exists" >&2
      exit 1
    fi
    mkdir -p "$dir/src/bin"
    cat > "$dir/Cargo.toml" <<EOF
[package]
name = "$crate"
description = "$issue: $desc"
version.workspace = true
edition.workspace = true
publish.workspace = true

[dependencies]
dpdk.workspace = true
testbed.workspace = true
clap.workspace = true
ctrlc.workspace = true
libc.workspace = true
EOF
    cat > "$dir/src/bin/''${crate}_host.rs" <<EOF
//! $issue host binary.
//! Put reusable helpers/workarounds in \`testbed/crates/*\` instead of duplicating them here.

use clap::Parser;

#[derive(Parser, Debug)]
#[command(about = "$issue host driver")]
struct Args {}

fn main() {
    let _args = Args::parse();
}
EOF
    cat > "$dir/src/bin/''${crate}_card.rs" <<EOF
//! $issue card binary.
//! Put reusable helpers/workarounds in \`testbed/crates/*\` instead of duplicating them here.

use clap::Parser;

#[derive(Parser, Debug)]
#[command(about = "$issue card driver")]
struct Args {}

fn main() {
    let _args = Args::parse();
}
EOF
    echo "Created $dir (auto-discovered by testbed/Cargo.toml)."
    echo "Next:"
    echo "  devenv shell -- mrv-build $crate"
    echo "  devenv shell -- mrv-check"
  '';

  # Build release binaries for host (x86_64) and card (aarch64) in one step.
  # Usage: mrv-build [--host|--card] [crate]
  scripts.mrv-build.exec = ''
    set -euo pipefail
    build_host=1
    build_card=1
    pkg=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --host) build_card=0 ;;
        --card) build_host=0 ;;
        -h|--help)
          echo "usage: mrv-build [--host|--card] [crate]"
          exit 0
          ;;
        -*)
          echo "error: unknown flag '$1'" >&2
          exit 1
          ;;
        *)
          pkg="$1"
          ;;
      esac
      shift
    done
    cd "$DEVENV_ROOT/testbed"
    if [ -n "$pkg" ]; then
      [ "$build_host" -eq 1 ] && cargo build --release -p "$pkg"
      [ "$build_card" -eq 1 ] && cargo build --release --target aarch64-unknown-linux-gnu -p "$pkg"
    else
      [ "$build_host" -eq 1 ] && cargo build --release
      [ "$build_card" -eq 1 ] && cargo build --release --target aarch64-unknown-linux-gnu
    fi
  '';

  # Pre-land verification gate for main: check all workspace targets + run lib tests.
  scripts.mrv-check.exec = ''
    set -euo pipefail
    cd "$DEVENV_ROOT/testbed"
    cargo check --workspace --all-targets
    cargo test --workspace --lib
  '';
}
