#!/usr/bin/env python3
"""MRV-245 full n=3 interleaved experiment harness.

Strictly enforces:
  - enable_custom_mair=0 (ZERO MAIR_EL1 modification; standard Linux MT_NORMAL_NC = 0x44)
  - MRV-26 BAR4 slot 15 lease only, PEM0 outbound regions 248..255 only, ZERO SDP
  - lost = bad = torn = corrupt = partial = db_ahead_empty = db_ahead_partial = 0
  - 100.0% non-multiplexed Host Socket 1 + Card TAD/PMUv3/DDR5 PMUs
  - Full post-run restore (`rmmod bar4_mair`, `rmmod pem_wc`, `inline_reflector --restore`,
    default `refl` tmux, `eth0 100G UP`, `2200000 / 3900000`, 21 s EDAC/AER zero-delta audit)

Groups:
  1. One-way TX full-64 B verified (no per-pkt reply, zero decoupled doorbell, zero SFENCE):
     card-silent ceilings (H=1/2), Normal-WB (K=1/2/4/8), MT_NORMAL_NC u1 (K=1/2/4/8/12)
     vs u4 (K=1/2/3/4/8) on H=1, and H=2 (K=4/8/12) joint PCIe Gen5x4 ceiling.
  2. High-concurrency closed-loop ping-pong (W=64/256/512, H=1/2, K=2/4/8) + W=1 unloaded RTT.
  3. Head-to-head comparison vs Software Pacing (MRV-115/123 hyst credit & burst/gap) and
     Decoupled Coalesced Doorbell (MRV-177 Arm B2 SFENCE + prod_idx) on W=1, W=64/256/512,
     and one-way TX.
"""
import argparse
import json
import pathlib
import socket
import subprocess
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv245")
LOG_DIR = ROOT / "logs/mrv245-2026-11-27"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
ENV_JSON = LOG_DIR / "env.json"
HOST_BIN = ROOT / "testbed/target/release/mrv245_host"
CARD_BIN_LOCAL = ROOT / "testbed/target/aarch64-unknown-linux-gnu/release/mrv245_card"
CARD_BIN = "/root/bench/mrv245_card"
KMOD_LOCAL = pathlib.Path("/tmp/bar4_mair_mrv245.ko")
KMOD_CARD = "/tmp/bar4_mair.ko"

REPS = 3
WARMUP_MS_H1 = 500
WARMUP_MS_H2 = 600
DURATION_MS = 1000


def ssh_card(cmd: str) -> str:
    return subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2", cmd], text=True
    )


def rpc_call(cmd: str) -> str:
    with socket.create_connection(("10.10.0.2", 7527), timeout=15) as s:
        s.sendall((cmd + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf.decode().strip()


def check_host_env() -> dict:
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    freq = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz")):
        uncore[p.parent.name] = int(p.read_text().strip())
    aer = {}
    for name in ("aer_dev_correctable", "aer_dev_fatal", "aer_dev_nonfatal"):
        p = pathlib.Path(f"/sys/bus/pci/devices/0000:99:00.0/{name}")
        tot = 0
        for line in p.read_text().splitlines():
            if line.startswith("TOTAL_ERR_"):
                tot = int(line.split()[-1])
        aer[name] = tot
    paranoid = int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    assert gov == "performance", f"cpu40 governor={gov!r}"
    for k, v in uncore.items():
        assert v == 2_200_000, f"uncore {k}={v}"
    assert paranoid <= 0, f"perf_event_paranoid={paranoid}"
    return {
        "cpu40_governor": gov,
        "cpu40_cur_khz": freq,
        "uncore_khz": uncore,
        "aer": aer,
        "perf_event_paranoid": paranoid,
    }


def read_card_state() -> dict:
    script = r"""python3 - <<'PY'
import glob, json, os, mmap, ctypes, subprocess
edac = {}
for p in sorted(glob.glob("/sys/devices/system/edac/*/*/ce_count")):
    d = os.path.dirname(p)
    key = os.path.relpath(d, "/sys/devices/system/edac")
    edac[key] = {
        "ce": int(open(os.path.join(d, "ce_count")).read().strip()),
        "ue": int(open(os.path.join(d, "ue_count")).read().strip()),
    }
# Single 64-bit loads only (never slice /dev/mem!)
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x8e0000000000)
q = (ctypes.c_uint64 * 8192).from_buffer(m)
bar4 = {str(i): f"0x{q[(0x700 + i*8)>>3]:x}" for i in range(16)}
outb = {str(i): f"0x{q[(0x4000 + i*16)>>3]:x}" for i in range(248, 256)}
del q; m.close(); os.close(fd)

mods = open("/proc/modules").read()
pem_wc_loaded = "pem_wc " in mods
bar4_mair_loaded = "bar4_mair " in mods
eth0_out = subprocess.check_output(["ethtool", "eth0"], text=True)
eth0_speed = next((l.split(":", 1)[1].strip() for l in eth0_out.splitlines() if "Speed:" in l), "unknown")
eth0_link = next((l.split(":", 1)[1].strip() for l in eth0_out.splitlines() if "Link detected:" in l), "unknown")
tmux_ls = subprocess.run(["tmux", "ls"], capture_output=True, text=True).stdout.strip()
print(json.dumps({
    "edac": edac,
    "bar4_index": bar4,
    "outbound_248_255": outb,
    "pem_wc_loaded": pem_wc_loaded,
    "bar4_mair_loaded": bar4_mair_loaded,
    "eth0_speed": eth0_speed,
    "eth0_link": eth0_link,
    "tmux_ls": tmux_ls,
}))
PY"""
    return json.loads(ssh_card(script).strip())


def stop_server():
    ssh_card(
        "pkill -INT -f mrv245_card 2>/dev/null || true; "
        "for _ in $(seq 1 50); do pgrep -f mrv245_card >/dev/null || break; sleep 0.1; done"
    )


def deploy_and_start_server() -> dict:
    stop_server()
    if KMOD_LOCAL.exists():
        subprocess.check_call(["scp", "-q", "-o", "BatchMode=yes", str(KMOD_LOCAL), f"root@10.10.0.2:{KMOD_CARD}"])
    if CARD_BIN_LOCAL.exists():
        subprocess.check_call(["scp", "-q", "-o", "BatchMode=yes", str(CARD_BIN_LOCAL), f"root@10.10.0.2:{CARD_BIN}"])
    ssh_card(
        "tmux kill-session -t refl 2>/dev/null || true; "
        "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "
        "lsmod | grep -q '^bar4_mair ' && rmmod bar4_mair || true; "
        f"insmod {KMOD_CARD} enable_custom_mair=0; "
        "rm -f /tmp/mrv245_card.log; "
        f"nohup {CARD_BIN} --wc > /tmp/mrv245_card.log 2>&1 & "
        "for _ in $(seq 1 50); do grep -q 'hosts lease the slot at' /tmp/mrv245_card.log 2>/dev/null && exit 0; sleep 0.1; done; "
        "cat /tmp/mrv245_card.log >&2; exit 1"
    )
    info = json.loads(rpc_call("INFO"))
    assert info["custom_active"] == 0, f"custom_active != 0: {info}"
    assert info["mair_orig"] == info["mair_curr"] == "0x000000040044ffff", f"MAIR altered: {info}"
    assert info["all_cpus_equal"] == 1 and info["online_cpus"] == 24, f"CPU MAIR mismatch: {info}"
    assert info["translations"]["normal_nc"]["attr"] == "0x44", f"normal_nc attr != 0x44: {info}"
    return info


def build_cases():
    """Returns list of dicts describing every single-variable case across Groups 1, 2, 3."""
    cases = []

    def add(exp, cond, h, k, wm, w, rc, mm, pm, cstep=128, pace="none", pc=32, pb=16, pg=0, db="none", dbb=32, dbl=4, dbf=500):
        cases.append({
            "exp": exp, "cond": cond, "h": h, "k": k, "wm": wm, "w": w, "rc": rc,
            "mm": mm, "pm": pm, "cstep": cstep,
            "pace": pace, "pc": pc, "pb": pb, "pg": pg,
            "db": db, "dbb": dbb, "dbl": dbl, "dbf": dbf,
        })

    # ------------------------------------------------------------------
    # Group 1: One-way TX full-64 B verified (zero per-pkt reply, zero SFENCE, zero doorbell)
    # ------------------------------------------------------------------
    add("g1_tx_ceil",   "tx_none_h1",        1,  1, "tx_oneway", 6144, 8192, "normal_nc", "none")
    add("g1_tx_ceil",   "tx_none_h2",        2,  2, "tx_oneway", 6144, 8192, "normal_nc", "none")

    for k in (1, 2, 4, 8):
        add("g1_tx_wb", f"tx_wb_u1_h1_k{k}", 1,  k, "tx_oneway", 6144, 8192, "wb_64k",    "u1_ldp")

    for k in (1, 2, 4, 8, 12):
        add("g1_tx_nc_u1", f"tx_nc_u1_h1_k{k}", 1, k, "tx_oneway", 6144, 8192, "normal_nc", "u1_ldp")

    for k in (1, 2, 3, 4, 8):
        add("g1_tx_nc_u4", f"tx_nc_u4_h1_k{k}", 1, k, "tx_oneway", 6144, 8192, "normal_nc", "u4_ldpq")

    for k in (8, 12):
        add("g1_tx_nc_h2", f"tx_nc_u1_h2_k{k}", 2, k, "tx_oneway", 6144, 8192, "normal_nc", "u1_ldp")
    for k in (4, 8, 12):
        add("g1_tx_nc_h2", f"tx_nc_u4_h2_k{k}", 2, k, "tx_oneway", 6144, 8192, "normal_nc", "u4_ldpq")

    # ------------------------------------------------------------------
    # Group 2: High-concurrency closed-loop + W=1 unloaded RTT (same zero-SFENCE NC path)
    # ------------------------------------------------------------------
    add("g2_cl_w1", "cl_wb_u1_k1_w1",    1, 1, "closed", 1, 4096, "wb_64k",    "u1_ldp")
    add("g2_cl_w1", "cl_wb_u1_k4_w1",    1, 4, "closed", 1, 4096, "wb_64k",    "u1_ldp")
    add("g2_cl_w1", "cl_nc_u1_k1_w1",    1, 1, "closed", 1, 4096, "normal_nc", "u1_ldp")
    add("g2_cl_w1", "cl_nc_u1_k4_w1",    1, 4, "closed", 1, 4096, "normal_nc", "u1_ldp")
    add("g2_cl_w1", "cl_nc_u1_k8_w1",    1, 8, "closed", 1, 4096, "normal_nc", "u1_ldp")
    add("g2_cl_w1", "cl_nc_u4_k1_w1",    1, 1, "closed", 1, 4096, "normal_nc", "u4_ldpq")
    add("g2_cl_w1", "cl_nc_u4_k4_w1",    1, 4, "closed", 1, 4096, "normal_nc", "u4_ldpq")

    for w in (64, 256, 512):
        add("g2_cl_h1", f"cl_nc_u1_h1_k2_w{w}",  1, 2, "closed", w, 4096, "normal_nc", "u1_ldp")
    add("g2_cl_h1",     "cl_nc_u1q_h1_k2_w256",  1, 2, "closed", 256, 4096, "normal_nc", "u1_ldpq")
    for w in (64, 256, 512):
        add("g2_cl_h1", f"cl_nc_u1_h1_k4_w{w}",  1, 4, "closed", w, 4096, "normal_nc", "u1_ldp")
    add("g2_cl_h1",     "cl_nc_u1_h1_k8_w256",   1, 8, "closed", 256, 4096, "normal_nc", "u1_ldp")
    add("g2_cl_h1",     "cl_nc_u4_h1_k2_w256",   1, 2, "closed", 256, 4096, "normal_nc", "u4_ldpq")

    for w in (64, 256, 512):
        add("g2_cl_h2", f"cl_nc_u1_h2_k4_w{w}",  2, 4, "closed", w, 4096, "normal_nc", "u1_ldp")
    add("g2_cl_h2",     "cl_nc_u1_h2_k8_w512",   2, 8, "closed", 512, 4096, "normal_nc", "u1_ldp")
    add("g2_cl_h2",     "cl_nc_u4_h2_k2_w512",   2, 2, "closed", 512, 4096, "normal_nc", "u4_ldpq")

    # ------------------------------------------------------------------
    # Group 3: Head-to-head vs Software Pacing (MRV-115/123) & Decoupled Coalesced Doorbell (MRV-177)
    # ------------------------------------------------------------------
    # Unpaced Normal-WB closed-loop reference (K=4)
    for w in (64, 256, 512):
        add("g3_cmp_wb",   f"cl_wb_raw_k4_w{w}",   1, 4, "closed", w, 4096, "wb_64k", "u1_ldp")

    # Software Pacing (Normal-WB + MRV-115/123 hysteresis credit C=32, B=16 in closed-loop; burst+gap in TX)
    for w in (1, 64, 256, 512):
        add("g3_cmp_pace", f"cl_wb_hyst32b16_k4_w{w}", 1, 4, "closed", w, 4096, "wb_64k", "u1_ldp",
            pace="hyst", pc=32, pb=16)
    add("g3_cmp_pace",     "tx_wb_pace32g200_k4",      1, 4, "tx_oneway", 6144, 8192, "wb_64k", "u1_ldp",
        pace="burst", pb=32, pg=200)

    # Decoupled Coalesced Doorbell (Normal-WB + MRV-177 Arm B2 SFENCE + prod_idx)
    for w in (1, 64, 256, 512):
        add("g3_cmp_db",   f"cl_wb_db32_k4_w{w}",  1, 4, "closed", w, 4096, "wb_64k", "doorbell",
            db="adaptive", dbb=32, dbl=4, dbf=500)
    add("g3_cmp_db",       "cl_wb_db32_k1_w256",   1, 1, "closed", 256, 4096, "wb_64k", "doorbell",
        db="adaptive", dbb=32, dbl=4, dbf=500)
    add("g3_cmp_db",       "tx_wb_db32_k2",        1, 2, "tx_oneway", 6144, 8192, "wb_64k", "doorbell",
        db="batch", dbb=32)
    add("g3_cmp_db",       "tx_wb_db32_k4",        1, 4, "tx_oneway", 6144, 8192, "wb_64k", "doorbell",
        db="batch", dbb=32)
    add("g3_cmp_db",       "tx_wb_db64_k2",        1, 2, "tx_oneway", 6144, 8192, "wb_64k", "doorbell",
        db="batch", dbb=64)
    add("g3_cmp_db",       "tx_wb_db64_k4",        1, 4, "tx_oneway", 6144, 8192, "wb_64k", "doorbell",
        db="batch", dbb=64)
    add("g3_cmp_db",       "tx_wb_db128_k2",       1, 2, "tx_oneway", 6144, 8192, "wb_64k", "doorbell",
        db="batch", dbb=128)
    add("g3_cmp_db",       "tx_wb_db32_k8",        1, 8, "tx_oneway", 6144, 8192, "wb_64k", "doorbell",
        db="batch", dbb=32)

    return cases


def run_case(c: dict, rep: int, duration_ms: int) -> dict:
    warmup = WARMUP_MS_H2 if c["h"] == 2 else WARMUP_MS_H1
    cmd = [
        str(HOST_BIN),
        "--host-cores", str(c["h"]),
        "--card-cores", str(c["k"]),
        "--work-mode", c["wm"],
        "--window", str(c["w"]),
        "--ring-cells", str(c["rc"]),
        "--map-mode", c["mm"],
        "--poll-mode", c["pm"],
        "--credit-step", str(c["cstep"]),
        "--pace-mode", c["pace"],
        "--pace-credit", str(c["pc"]),
        "--pace-batch", str(c["pb"]),
        "--pace-gap-ns", str(c["pg"]),
        "--doorbell", c["db"],
        "--db-sfence", "true",
        "--db-batch", str(c["dbb"]),
        "--db-light", str(c["dbl"]),
        "--db-flush-ns", str(c["dbf"]),
        "--warmup-ms", str(warmup),
        "--duration-ms", str(duration_ms),
        "--exp", c["exp"],
        "--cond", c["cond"],
        "--rep", str(rep),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(
            f"FAIL {c['exp']}/{c['cond']} r{rep} rc={p.returncode}\nSTDERR:\n{p.stderr[-500:]}\nSTDOUT:\n{p.stdout[-500:]}"
        )
    lines = [l for l in p.stdout.splitlines() if l.startswith("MRV245_RUN ")]
    assert lines, f"No MRV245_RUN output: {p.stdout[-400:]}"
    rec = json.loads(lines[-1][11:])
    cp = rec["card_pmu"]
    hp = rec["host_pmu"]
    k = rec["card_cores"]
    corrupt = sum(x["corrupt"] for x in cp["workers"])
    partial = sum(x["partial"] for x in cp["workers"])
    db_ae = sum(x["db_ahead_empty"] for x in cp["workers"])
    db_ap = sum(x["db_ahead_partial"] for x in cp["workers"])
    tak_tot = sum(x["taken"] for x in rec["card_stats"]["workers"])

    assert rec["lost"] == 0, f"lost={rec['lost']} in {c['cond']} r{rep}"
    assert rec["bad_or_torn"] == 0, f"bad_or_torn={rec['bad_or_torn']} in {c['cond']} r{rep}"
    assert corrupt == 0, f"corrupt={corrupt} in {c['cond']} r{rep}"
    assert partial == 0, f"partial={partial} in {c['cond']} r{rep}"
    assert db_ae == 0 and db_ap == 0, f"db_ahead={db_ae}/{db_ap} in {c['cond']} r{rep}"
    if c["pm"] != "none":
        assert tak_tot == rec["sent_tot"], f"card taken_tot ({tak_tot}) != host sent_tot ({rec['sent_tot']}) in {c['cond']} r{rep}"
    assert rec["host_min_pct"] >= 99.9 and cp["min_pct"] >= 99.9, (
        f"PMU mux in {c['cond']} r{rep}: host={rec['host_min_pct']} card={cp['min_pct']}"
    )

    rec["timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dt = cp["elapsed_ns"] / 1e9
    tak_win = sum(x["taken_win"] for x in cp["workers"])
    l2i = sum(cp["core_pmu"][i]["l2d_cache_inval"] for i in range(k)) / max(1, tak_win)
    snp = cp["tad"]["tad_snp_msh_out_any"]["val"] / max(1, tak_win)
    ddr_r = cp["ddr"]["reads_total"] / max(1, tak_win)
    ghz_s = ",".join(f"{x['ghz']:.2f}" for x in hp["cpus"])
    print(
        f"[{c['exp']:12s}] {c['cond']:24s} r{rep} | {rec['mops']:6.2f} M/s ({rec['gb_s']:5.2f} GB/s) "
        f"rtt50={rec['rtt_p50_ns']:5.0f} p99={rec['rtt_p99_ns']:5.0f} | "
        f"cha={hp['cha_inflight']:5.1f} l2i={l2i:4.2f} snp={snp:4.2f} ddrR={ddr_r:4.2f} | "
        f"ghz={ghz_s} pct={rec['host_min_pct']:.0f}/{cp['min_pct']:.0f}",
        flush=True,
    )
    return rec


def restore_baseline() -> dict:
    print("\n=== Restoring card & host baseline (enable_custom_mair=0, zero MAIR edit) ===", flush=True)
    stop_server()
    verify_out = ssh_card(
        "lsmod | grep -q '^bar4_mair ' && rmmod bar4_mair || true; "
        "DMESG_UNLOAD=$(dmesg | grep 'bar4_mair: unloaded' | tail -n 1); "
        f"rm -f {KMOD_CARD} {CARD_BIN} /tmp/mrv245_card.log; "
        "/root/bench/inline_reflector --restore; "
        "lsmod | grep -q '^pem_wc ' && rmmod pem_wc || true; "
        "BAR4_RESTORED=$(python3 -c 'import os,mmap,ctypes; fd=os.open(\"/dev/mem\",os.O_RDWR|os.O_SYNC); m=mmap.mmap(fd,0x10000,mmap.MAP_SHARED,mmap.PROT_READ|mmap.PROT_WRITE,offset=0x8e0000000000); q=(ctypes.c_uint64*8192).from_buffer(m); print(hex(q[(0x700+15*8)>>3])); del q; m.close(); os.close(fd)'); "
        "tmux has-session -t refl 2>/dev/null || "
        "tmux new-session -d -s refl '/root/bench/inline_reflector -l 3 -a 0002:01:00.2 --file-prefix=refl'; "
        "sleep 1.5; "
        "echo \"DMESG=$DMESG_UNLOAD\"; "
        "echo \"BAR4_RESTORED=$BAR4_RESTORED\""
    )
    dmesg_line = ""
    bar4_restored = ""
    for l in verify_out.splitlines():
        if l.startswith("DMESG="):
            dmesg_line = l[6:]
        elif l.startswith("BAR4_RESTORED="):
            bar4_restored = l[14:]
    print(f"bar4_mair unload dmesg : {dmesg_line}", flush=True)
    print(f"BAR4 slot 15 restored  : {bar4_restored}", flush=True)
    assert "MAIR_EL1 untouched: 0x000000040044ffff" in dmesg_line, f"Unexpected bar4_mair unload dmesg: {dmesg_line}"
    assert bar4_restored == "0x2ff71", f"BAR4_INDEX[15] before refl start not 0x2ff71: {bar4_restored}"
    return {
        "dmesg_unload": dmesg_line,
        "bar4_slot15_before_refl": bar4_restored,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=REPS)
    ap.add_argument("--duration-ms", type=int, default=DURATION_MS)
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    host_before = check_host_env()
    card_before = read_card_state()
    print(f"Host before: {host_before}", flush=True)
    print(
        f"Card before: eth0={card_before['eth0_speed']}/{card_before['eth0_link']} "
        f"pem_wc={card_before['pem_wc_loaded']} bar4_mair={card_before['bar4_mair_loaded']} "
        f"mc0={card_before['edac']['mc/mc0']}",
        flush=True,
    )

    cases = build_cases()
    print(f"Planned {len(cases)} conditions x {args.reps} reps = {len(cases) * args.reps} runs", flush=True)

    records = []
    mair_info = None
    try:
        mair_info = deploy_and_start_server()
        print(f"Card INFO verified (enable_custom_mair=0): {mair_info['mair_curr']} translations={list(mair_info['translations'].keys())}", flush=True)

        with RUNS_JSONL.open("w") as f:
            for rep in range(args.reps):
                seq = list(reversed(cases)) if (rep % 2 == 1) else cases
                for c in seq:
                    rec = run_case(c, rep, args.duration_ms)
                    records.append(rec)
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
        print(f"\nSaved {len(records)} verified runs to {RUNS_JSONL}", flush=True)
    finally:
        restore_info = restore_baseline()

    print("Waiting 21.0 s for delayed EDAC/SEI-UER & PCIe AER audit...", flush=True)
    time.sleep(21.0)
    host_after = check_host_env()
    card_after = read_card_state()

    aer_delta = {k: host_after["aer"][k] - host_before["aer"][k] for k in host_before["aer"]}
    edac_delta = {}
    for k in card_before["edac"]:
        edac_delta[k] = {
            "ce": card_after["edac"][k]["ce"] - card_before["edac"][k]["ce"],
            "ue": card_after["edac"][k]["ue"] - card_before["edac"][k]["ue"],
        }
    all_zero = (
        all(v == 0 for v in aer_delta.values())
        and all(v["ce"] == 0 and v["ue"] == 0 for v in edac_delta.values())
    )

    assert not card_after["bar4_mair_loaded"], "bar4_mair still loaded!"
    assert not card_after["pem_wc_loaded"], "pem_wc still loaded!"
    assert card_after["eth0_speed"] == "100000Mb/s" and card_after["eth0_link"] == "yes", (
        f"eth0 not 100G UP: {card_after['eth0_speed']}/{card_after['eth0_link']}"
    )
    assert (int(card_after["bar4_index"]["15"], 16) & 1) == 1, (
        f"BAR4_INDEX[15] invalid after refl restart: {card_after['bar4_index']['15']}"
    )
    assert all(v == "0x0" for v in card_after["outbound_248_255"].values()), (
        f"PEM outbound 248..255 not zeroed: {card_after['outbound_248_255']}"
    )
    assert "refl:" in card_after["tmux_ls"], f"refl tmux not restored: {card_after['tmux_ls']}"
    assert all_zero, f"Non-zero EDAC/AER delta: aer={aer_delta} edac={edac_delta}"

    env = {
        "issue": "MRV-245",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_runs": len(records),
        "conditions": len(cases),
        "reps": args.reps,
        "audit_wait_s": 21.0,
        "mair_info": mair_info,
        "restore_verify": restore_info,
        "host_before": host_before,
        "host_after": host_after,
        "aer_delta": aer_delta,
        "card_before": card_before,
        "card_after": card_after,
        "edac_delta": edac_delta,
        "all_zero_edac_aer_delta": all_zero,
    }
    ENV_JSON.write_text(json.dumps(env, indent=2) + "\n")
    print(f"Wrote {ENV_JSON}", flush=True)
    print(f"ALL {len(records)} RUNS + ZERO-MAIR-EDIT RESTORE + 21 s EDAC/AER ZERO-DELTA VERIFIED 100%!", flush=True)


if __name__ == "__main__":
    main()
