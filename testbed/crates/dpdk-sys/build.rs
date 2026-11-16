//! Bind and statically link the DPDK that pkg-config resolves for `$TARGET`.
//!
//! pkg-config settings are read per target (`PKG_CONFIG_PATH_<target>`,
//! `PKG_CONFIG_LIBDIR_<target>`, `PKG_CONFIG_SYSROOT_DIR_<target>`, set by
//! devenv.nix); the generic variables apply only to native builds, so a cross
//! build never picks up the host's DPDK. The sysroot also feeds clang (bindgen)
//! and the C compiler.

use std::collections::HashSet;
use std::env;
use std::path::{Path, PathBuf};
use std::process::Command;

/// DPDK functions the testbed calls. Static inline ones get C wrappers.
const FUNCTIONS: &[&str] = &[
    "rte_eal_init",
    "rte_eal_cleanup",
    "rte_eal_remote_launch",
    "rte_eal_wait_lcore",
    "rte_get_next_lcore",
    "rte_lcore_count",
    "rte_strerror",
    "rte_get_tsc_hz",
    "rte_rdtsc",
    "rte_eth_dev_configure",
    "rte_eth_dev_socket_id",
    "rte_eth_dev_start",
    "rte_eth_dev_stop",
    "rte_eth_macaddr_get",
    "rte_eth_promiscuous_enable",
    "rte_eth_rx_queue_setup",
    "rte_eth_tx_queue_setup",
    "rte_eth_rx_burst",
    "rte_eth_tx_burst",
    "rte_pktmbuf_pool_create",
    "rte_mempool_free",
    "rte_pktmbuf_alloc",
    "rte_pktmbuf_append",
    "rte_pktmbuf_free",
    "rte_pktmbuf_free_bulk",
    "rte_memzone_free",
    "rte_mem_virt2phy",
    "rte_dma_get_dev_id_by_name",
    "rte_dma_start",
    "rte_dma_stop",
    "rte_dma_close",
    "rte_dma_copy",
    "rte_dma_submit",
    "rte_dma_completed",
    "dpdk_sys_.*",
];

fn main() {
    let target = env::var("TARGET").unwrap();
    let cross = target != env::var("HOST").unwrap();
    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    let sysroot = target_var(&target, cross, "PKG_CONFIG_SYSROOT_DIR");
    println!("cargo:rerun-if-changed=wrapper.h");

    let mut cflags = pkg_config(&target, cross, &["--cflags"]);
    if let Some(s) = &sysroot {
        cflags.push(format!("--sysroot={s}"));
    }

    bindgen::Builder::default()
        .header("wrapper.h")
        .clang_args(&cflags)
        .allowlist_function(FUNCTIONS.join("|"))
        .wrap_static_fns(true)
        .wrap_static_fns_path(out.join("static_fns"))
        .derive_default(true)
        .layout_tests(false)
        .generate_comments(false)
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .generate()
        .expect("bindgen failed on wrapper.h")
        .write_to_file(out.join("bindings.rs"))
        .unwrap();

    // The generated wrappers `#include "wrapper.h"`; build them exactly like DPDK
    // expects its headers to be compiled (-include rte_config.h, -march/-mcpu).
    let mut cc = cc::Build::new();
    cc.file(out.join("static_fns.c"))
        .include(env::var("CARGO_MANIFEST_DIR").unwrap())
        .warnings(false);
    for flag in &cflags {
        cc.flag(flag);
    }
    cc.compile("dpdk_static_fns");

    link(&pkg_config(&target, cross, &["--static", "--libs"]));
}

/// `NAME_<target>` (either spelling), falling back to `NAME` for native builds.
fn target_var(target: &str, cross: bool, name: &str) -> Option<String> {
    let keys = [format!("{name}_{target}"), format!("{name}_{}", target.replace('-', "_"))];
    for key in &keys {
        println!("cargo:rerun-if-env-changed={key}");
    }
    println!("cargo:rerun-if-env-changed={name}");
    keys.iter()
        .find_map(|k| env::var(k).ok())
        .or_else(|| if cross { None } else { env::var(name).ok() })
}

fn pkg_config(target: &str, cross: bool, args: &[&str]) -> Vec<String> {
    let mut cmd = Command::new("pkg-config");
    for name in ["PKG_CONFIG_PATH", "PKG_CONFIG_LIBDIR", "PKG_CONFIG_SYSROOT_DIR"] {
        match target_var(target, cross, name) {
            Some(v) => cmd.env(name, v),
            None => cmd.env_remove(name),
        };
    }
    let out = cmd.args(args).arg("libdpdk").output().expect("running pkg-config");
    assert!(
        out.status.success(),
        "pkg-config {args:?} libdpdk failed for {target}: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8(out.stdout).unwrap().split_whitespace().map(str::to_owned).collect()
}

/// Translate `pkg-config --static --libs` into Cargo link directives, keeping
/// order. DPDK libraries are static (drivers inside `--whole-archive`, since
/// they register through constructors); everything else is a system dylib.
/// The archives are not bundled into the rlib, so each one is tracked: a
/// rebuilt DPDK reruns this script, which relinks the binaries.
fn link(libs: &[String]) {
    let dirs: Vec<&str> = libs.iter().filter_map(|f| f.strip_prefix("-L")).collect();
    let mut whole = false;
    let mut seen = HashSet::new();
    let mut link_static = |name: &str, whole: bool| {
        if seen.insert(name.to_owned()) {
            let modifiers = if whole { "+whole-archive,-bundle" } else { "-bundle" };
            println!("cargo:rustc-link-lib=static:{modifiers}={name}");
            let archive = format!("lib{name}.a");
            if let Some(path) = dirs.iter().map(|d| Path::new(d).join(&archive)).find(|p| p.exists()) {
                println!("cargo:rerun-if-changed={}", path.display());
            }
        }
    };
    for flag in libs {
        match flag.as_str() {
            "-Wl,--whole-archive" => whole = true,
            "-Wl,--no-whole-archive" => whole = false,
            "-pthread" => println!("cargo:rustc-link-lib=dylib=pthread"),
            f if f.starts_with("-L") => println!("cargo:rustc-link-search=native={}", &f[2..]),
            f if f.starts_with("-l:") => {
                let archive = &f[3..];
                let name = archive.strip_prefix("lib").and_then(|n| n.strip_suffix(".a"));
                link_static(name.unwrap_or_else(|| panic!("unexpected {f}")), whole);
            }
            f if f.starts_with("-lrte_") => link_static(&f[2..], whole),
            f if f.starts_with("-l") => println!("cargo:rustc-link-lib=dylib={}", &f[2..]),
            // --as-needed / --export-dynamic only matter for shared PMD plugins.
            _ => {}
        }
    }
}
