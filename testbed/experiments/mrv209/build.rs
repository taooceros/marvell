use std::path::Path;

fn main() {
    println!("cargo:rerun-if-changed=../../mc-adapter/mrv209_mc_adapter.c");
    println!("cargo:rerun-if-changed=../../mc-adapter/mrv209_mc_adapter.h");
    println!("cargo:rerun-if-changed=/tmp/mrv209-upstream/libmrv209_mc.a");

    let target_arch = std::env::var("CARGO_CFG_TARGET_ARCH").unwrap_or_default();
    if target_arch == "x86_64" && Path::new("/tmp/mrv209-upstream/libmrv209_mc.a").exists() {
        println!("cargo:rustc-link-search=native=/tmp/mrv209-upstream");
        println!("cargo:rustc-link-search=native=/tmp/mrv206-upstream/libevent-prefix/lib");
        println!("cargo:rustc-link-lib=static=mrv209_mc");
        println!("cargo:rustc-link-lib=dylib=event");
        println!("cargo:rustc-link-arg=-Wl,-rpath,/tmp/mrv206-upstream/libevent-prefix/lib");
    }
}
