#!/usr/bin/env python3
"""MRV-235 full n=3 interleaved experiment harness.

Strictly follows MRV-26 BAR4 slot 15 lease, MRV-82 single-variable Occam's Razor,
100% non-multiplexed host + card (TAD + PMUv3 incl. 0x58 + 6-ch DDR5) PMUs,
Stage 1 zero-MAIR-edit check + Stage 2 custom MAIR slot 5 (0xF4), full
restoration (rmmod bar4_mair + 24-core MAIR_EL1 verify + rmmod pem_wc + refl
tmux + eth0 100G + 21 s EDAC/AER zero-delta audit into env.json).
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv235")
LOG_DIR = ROOT / "logs/mrv235-2026-11-27"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
ENV_JSON = LOG_DIR / "env.json"
HOST_BIN = ROOT / "testbed/target/release/mrv235_host"
CARD_BIN = "/root/bench/mrv235_card"
KMOD_PATH = "/tmp/bar4_mair.ko"

REPS = 3
WARMUP_MS = 500
DURATION_MS = 1000


def ssh_card(cmd: str) -> str:
    return subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2", cmd], text=True
    )


def rpc_call(cmd: str) -> str:
    with socket.create_connection(("10.10.0.2", 7527), timeout=10) as s:
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
        "pkill -INT -f mrv235_card 2>/dev/null || true; "
        "for _ in $(seq 1 50); do pgrep -f mrv235_card >/dev/null || break; sleep 0.1; done"
    )


def start_server(enable_custom_mair: bool) -> dict:
    stop_server()
    param = (
        "enable_custom_mair=1 custom_mair_slot=5 custom_mair_attr=0xf4"
        if enable_custom_mair
        else "enable_custom_mair=0"
    )
    ssh_card(
        "tmux kill-session -t refl 2>/dev/null || true; "
        "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "
        "lsmod | grep -q '^bar4_mair ' && rmmod bar4_mair || true; "
        f"insmod {KMOD_PATH} {param}; "
        "rm -f /tmp/mrv235_card.log; "
        f"nohup {CARD_BIN} --wc > /tmp/mrv235_card.log 2>&1 & "
        "for _ in $(seq 1 50); do grep -q 'hosts lease the slot at' /tmp/mrv235_card.log 2>/dev/null && exit 0; sleep 0.1; done; "
        "cat /tmp/mrv235_card.log >&2; exit 1"
    )
    info = json.loads(rpc_call("INFO"))
    return info


def run_case(exp: str, cond: str, k: int, w: int, ring_cells: int, map_mode: str, access_mode: str, rep: int, stage: str) -> dict:
    cmd = [
        str(HOST_BIN),
        "--card-cores", str(k),
        "--window", str(w),
        "--ring-cells", str(ring_cells),
        "--map-mode", map_mode,
        "--access-mode", access_mode,
        "--warmup-ms", str(WARMUP_MS),
        "--duration-ms", str(DURATION_MS),
        "--exp", exp,
        "--cond", cond,
        "--rep", str(rep),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(
            f"FAIL {exp}/{cond} r{rep} rc={p.returncode}\nSTDERR:\n{p.stderr[-500:]}\nSTDOUT:\n{p.stdout[-500:]}"
        )
    lines = [l for l in p.stdout.splitlines() if l.startswith("MRV235_RUN ")]
    assert lines, f"No MRV235_RUN output: {p.stdout[-400:]}"
    rec = json.loads(lines[-1][11:])
    cp = rec["card_pmu"]
    hp = rec["host_pmu"]
    corrupt = sum(x["corrupt"] for x in cp["workers"])
    partial = sum(x["partial"] for x in cp["workers"])
    assert rec["lost"] == 0, f"lost={rec['lost']} in {cond} r{rep}"
    assert rec["bad_or_torn"] == 0, f"bad_or_torn={rec['bad_or_torn']} in {cond} r{rep}"
    assert corrupt == 0, f"corrupt={corrupt} in {cond} r{rep}"
    assert rec["host_min_pct"] >= 99.9 and cp["min_pct"] >= 99.9, (
        f"PMU mux in {cond} r{rep}: host={rec['host_min_pct']} card={cp['min_pct']}"
    )
    rec["stage"] = stage
    rec["timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    dt = cp["elapsed_ns"] / 1e9
    tak = sum(x["taken_win"] for x in cp["workers"])
    l1r = sum(cp["core_pmu"][i]["l1d_cache_refill"] for i in range(k)) / max(1, tak)
    l2i = sum(cp["core_pmu"][i]["l2d_cache_inval"] for i in range(k)) / max(1, tak)
    snp = cp["tad"]["tad_snp_msh_out_any"]["val"] / max(1, tak)
    dss_rd = cp["tad"]["tad_req_msh_out_dss_rd"]["val"] / max(1, tak)
    ddr_rd = cp["ddr"]["reads_total"] / max(1, tak)
    tad_occ = cp["tad"]["tad_req_occ"]["val"] / (dt * 2.0e9)
    print(
        f"[{exp:19s}] {cond:24s} r{rep} | {rec['mops']:6.2f} M/s ({rec['gb_s']:4.2f} GB/s) "
        f"rtt50={rec['rtt_p50_ns']:5.0f}ns (1/r={rec['inv_rate_ns']:4.0f}) | "
        f"l1r={l1r:4.2f} l2i={l2i:4.2f} snp={snp:4.2f} occ={tad_occ:4.2f} dssR={dss_rd:4.2f} ddrR={ddr_rd:4.2f} | "
        f"par={partial} ghz={hp['ghz']:.2f} pct={rec['host_min_pct']:.0f}/{cp['min_pct']:.0f}",
        flush=True,
    )
    return rec


def restore_baseline() -> dict:
    print("\n=== Restoring card & host baseline & verifying 24-core MAIR_EL1 ===", flush=True)
    stop_server()
    # Unload bar4_mair (triggers 24-core MAIR_EL1 restore), capture dmesg, then re-read MAIR_EL1 with enable_custom_mair=0
    verify_out = ssh_card(
        "lsmod | grep -q '^bar4_mair ' && rmmod bar4_mair || true; "
        "DMESG_UNLOAD=$(dmesg | grep 'bar4_mair: unloaded' | tail -n 1); "
        f"insmod {KMOD_PATH} enable_custom_mair=0; "
        "POST_READ=$(cat /dev/bar4_mair); "
        "rmmod bar4_mair; "
        f"rm -f {KMOD_PATH} {CARD_BIN} /tmp/mrv235_card.log; "
        "/root/bench/inline_reflector --restore; "
        "lsmod | grep -q '^pem_wc ' && rmmod pem_wc || true; "
        "BAR4_RESTORED=$(python3 -c 'import os,mmap,ctypes; fd=os.open(\"/dev/mem\",os.O_RDWR|os.O_SYNC); m=mmap.mmap(fd,0x10000,mmap.MAP_SHARED,mmap.PROT_READ|mmap.PROT_WRITE,offset=0x8e0000000000); q=(ctypes.c_uint64*8192).from_buffer(m); print(hex(q[(0x700+15*8)>>3])); del q; m.close(); os.close(fd)'); "
        "tmux has-session -t refl 2>/dev/null || "
        "tmux new-session -d -s refl '/root/bench/inline_reflector -l 3 -a 0002:01:00.2 --file-prefix=refl'; "
        "sleep 1.5; "
        "echo \"DMESG=$DMESG_UNLOAD\"; "
        "echo \"POST_READ=$POST_READ\"; "
        "echo \"BAR4_RESTORED=$BAR4_RESTORED\""
    )
    dmesg_line = ""
    post_read = ""
    bar4_restored = ""
    for l in verify_out.splitlines():
        if l.startswith("DMESG="):
            dmesg_line = l[6:]
        elif l.startswith("POST_READ="):
            post_read = l[10:]
        elif l.startswith("BAR4_RESTORED="):
            bar4_restored = l[14:]
    print(f"MAIR restore dmesg : {dmesg_line}", flush=True)
    print(f"MAIR post-readback : {post_read}", flush=True)
    print(f"BAR4 slot 15 reset : {bar4_restored}", flush=True)
    assert "exact_match=1" in dmesg_line, f"MAIR restore dmesg did not report exact_match=1: {dmesg_line}"
    assert "online_cpus=24 all_equal=1 mair_orig=0x000000040044ffff mair_curr=0x000000040044ffff custom_active=0" in post_read, (
        f"Post-unload 24-core MAIR_EL1 verification FAILED: {post_read}"
    )
    assert bar4_restored == "0x2ff71", f"BAR4_INDEX[15] before refl start not 0x2ff71: {bar4_restored}"
    return {
        "dmesg_unload": dmesg_line,
        "post_unload_readback": post_read,
        "bar4_slot15_before_refl": bar4_restored,
    }


def main():
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

    records = []
    stage1_info = None
    stage2_info = None

    try:
        # ------------------------------------------------------------------
        # Stage 1: Zero-MAIR-edit confirmation (enable_custom_mair=0)
        # ------------------------------------------------------------------
        print("\n=== STAGE 1: Zero MAIR_EL1 edit (enable_custom_mair=0) ===", flush=True)
        stage1_info = start_server(enable_custom_mair=False)
        assert stage1_info["custom_active"] == 0
        assert stage1_info["mair_orig"] == stage1_info["mair_curr"] == "0x000000040044ffff"
        print(f"Stage 1 INFO verified: {stage1_info['mair_curr']} translations={list(stage1_info['translations'].keys())}", flush=True)

        stage1_cases = [
            ("stage1_zero_mair", "s1_wb64k_ldar_k2_w64", 2, 64, 4096, "wb_64k",    "full64_ldar"),
            ("stage1_zero_mair", "s1_nc44_ldar_k2_w64",  2, 64, 4096, "normal_nc", "full64_ldar"),
            ("stage1_zero_mair", "s1_nc44_1p_k2_w64",    2, 64, 4096, "normal_nc", "full64_1pass_ldp"),
            ("stage1_zero_mair", "s1_nc44_1p_k4_w64",    4, 64, 4096, "normal_nc", "full64_1pass_ldp"),
            ("stage1_zero_mair", "s1_nc44_1p_k1_w1",     1,  1, 4096, "normal_nc", "full64_1pass_ldp"),
        ]
        for rep in range(REPS):
            seq = list(reversed(stage1_cases)) if (rep % 2 == 1) else stage1_cases
            for exp, cond, k, w, rc, mm, am in seq:
                rec = run_case(exp, cond, k, w, rc, mm, am, rep, "stage1_zero_mair")
                records.append(rec)

        # ------------------------------------------------------------------
        # Stage 2: Custom MAIR slot 5 = 0xF4 (Inner-NC, Outer-WB) full suite
        # ------------------------------------------------------------------
        print("\n=== STAGE 2: Custom MAIR slot 5 = 0xF4 (Inner-NC, Outer-WB) ===", flush=True)
        stage2_info = start_server(enable_custom_mair=True)
        assert stage2_info["custom_active"] == 1
        assert stage2_info["mair_orig"] == "0x000000040044ffff"
        assert stage2_info["mair_curr"] == "0x0000f4040044ffff"
        assert stage2_info["translations"]["inner_nc_ish"]["attr"] == "0xf4"
        print(f"Stage 2 INFO verified: {stage2_info['mair_curr']} translations={list(stage2_info['translations'].keys())}", flush=True)

        stage2_cases = [
            # Exp 1: MRV-231 exact apples-to-apples `full64_ldar` at W=64, K=1/2/4
            ("exp1_mair_ldar", "wb_huge_k2_w64",       2, 64, 4096, "wb_huge",      "full64_ldar"),
            ("exp1_mair_ldar", "wb64k_k1_w64",         1, 64, 4096, "wb_64k",       "full64_ldar"),
            ("exp1_mair_ldar", "wb64k_k2_w64",         2, 64, 4096, "wb_64k",       "full64_ldar"),
            ("exp1_mair_ldar", "wb64k_k4_w64",         4, 64, 4096, "wb_64k",       "full64_ldar"),
            ("exp1_mair_ldar", "nc44_k1_w64",          1, 64, 4096, "normal_nc",    "full64_ldar"),
            ("exp1_mair_ldar", "nc44_k2_w64",          2, 64, 4096, "normal_nc",    "full64_ldar"),
            ("exp1_mair_ldar", "nc44_k4_w64",          4, 64, 4096, "normal_nc",    "full64_ldar"),
            ("exp1_mair_ldar", "f4_ish_k1_w64",        1, 64, 4096, "inner_nc_ish", "full64_ldar"),
            ("exp1_mair_ldar", "f4_ish_k2_w64",        2, 64, 4096, "inner_nc_ish", "full64_ldar"),
            ("exp1_mair_ldar", "f4_ish_k4_w64",        4, 64, 4096, "inner_nc_ish", "full64_ldar"),
            ("exp1_mair_ldar", "f4_osh_k2_w64",        2, 64, 4096, "inner_nc_osh", "full64_ldar"),

            # Exp 2: Single-pass 64B `full64_1pass_ldp` (eliminating 2-step L3 RTT serialization)
            ("exp2_1pass_scale", "wb64k_1p_k1_w64",    1, 64, 4096, "wb_64k",       "full64_1pass_ldp"),
            ("exp2_1pass_scale", "wb64k_1p_k2_w64",    2, 64, 4096, "wb_64k",       "full64_1pass_ldp"),
            ("exp2_1pass_scale", "wb64k_1p_k4_w64",    4, 64, 4096, "wb_64k",       "full64_1pass_ldp"),
            ("exp2_1pass_scale", "nc44_1p_k1_w64",     1, 64, 4096, "normal_nc",    "full64_1pass_ldp"),
            ("exp2_1pass_scale", "nc44_1p_k2_w64",     2, 64, 4096, "normal_nc",    "full64_1pass_ldp"),
            ("exp2_1pass_scale", "nc44_1p_k4_w64",     4, 64, 4096, "normal_nc",    "full64_1pass_ldp"),
            ("exp2_1pass_scale", "f4_1p_k1_w64",       1, 64, 4096, "inner_nc_ish", "full64_1pass_ldp"),
            ("exp2_1pass_scale", "f4_1p_k2_w64",       2, 64, 4096, "inner_nc_ish", "full64_1pass_ldp"),
            ("exp2_1pass_scale", "f4_1p_k4_w64",       4, 64, 4096, "inner_nc_ish", "full64_1pass_ldp"),

            # Exp 3: Single-message W=1 RTT cost & 1/rate cross-check
            ("exp3_w1_rtt", "wb64k_ldar_k1_w1",        1,  1, 4096, "wb_64k",       "full64_ldar"),
            ("exp3_w1_rtt", "wb64k_ldar_k2_w1",        2,  1, 4096, "wb_64k",       "full64_ldar"),
            ("exp3_w1_rtt", "wb64k_1p_k1_w1",          1,  1, 4096, "wb_64k",       "full64_1pass_ldp"),
            ("exp3_w1_rtt", "wb64k_1p_k2_w1",          2,  1, 4096, "wb_64k",       "full64_1pass_ldp"),
            ("exp3_w1_rtt", "nc44_ldar_k1_w1",         1,  1, 4096, "normal_nc",    "full64_ldar"),
            ("exp3_w1_rtt", "f4_ldar_k1_w1",           1,  1, 4096, "inner_nc_ish", "full64_ldar"),
            ("exp3_w1_rtt", "nc44_1p_k1_w1",           1,  1, 4096, "normal_nc",    "full64_1pass_ldp"),
            ("exp3_w1_rtt", "f4_1p_k1_w1",             1,  1, 4096, "inner_nc_ish", "full64_1pass_ldp"),
            ("exp3_w1_rtt", "f4_1p_k2_w1",             2,  1, 4096, "inner_nc_ish", "full64_1pass_ldp"),
            ("exp3_w1_rtt", "f4_hdr16_k1_w1",          1,  1, 4096, "inner_nc_ish", "hdr16_ldp"),
            ("exp3_w1_rtt", "f4_dualwb_3875k_k1_w1",   1,  1, 31744, "inner_nc_ish", "dual_va_wb"),

            # Exp 4: Empirical answer to Hongtao's prefetch & Dual-VA question
            ("exp4_prefetch_dualva", "f4_hdr16_k1_w64",       1, 64,  4096, "inner_nc_ish", "hdr16_ldp"),
            ("exp4_prefetch_dualva", "f4_hdr16_k2_w64",       2, 64,  4096, "inner_nc_ish", "hdr16_ldp"),
            ("exp4_prefetch_dualva", "f4_hdr16_k4_w64",       4, 64,  4096, "inner_nc_ish", "hdr16_ldp"),
            ("exp4_prefetch_dualva", "f4_ldrdmb_k2_w64",      2, 64,  4096, "inner_nc_ish", "full64_ldr_dmb"),
            ("exp4_prefetch_dualva", "f4_nodmb_k2_w64",       2, 64,  4096, "inner_nc_ish", "full64_nodmb_ldp"),
            ("exp4_prefetch_dualva", "f4_post_prfm_l3_k2",    2, 64,  4096, "inner_nc_ish", "post_prfm_l3"),
            ("exp4_prefetch_dualva", "f4_post_prfm_l1nc_k2",  2, 64,  4096, "inner_nc_ish", "post_prfm_l1_nc"),
            ("exp4_prefetch_dualva", "f4_front_prfm_l1nc_k2", 2, 64,  4096, "inner_nc_ish", "frontier_prfm_l1_nc"),
            ("exp4_prefetch_dualva", "f4_front_prfm_l1wb_k2", 2, 64,  4096, "inner_nc_ish", "frontier_prfm_l1_wb"),
            ("exp4_prefetch_dualva", "f4_dualwb_512k_k2",     2, 64,  4096, "inner_nc_ish", "dual_va_wb"),
            ("exp4_prefetch_dualva", "f4_dualwb_3875k_k2",    2, 64, 31744, "inner_nc_ish", "dual_va_wb"),
            ("exp4_prefetch_dualva", "f4_dualwb_civac_k2",    2, 64,  4096, "inner_nc_ish", "dual_va_wb_civac"),
            ("exp4_prefetch_dualva", "f4_post_prfm_l1wb_k2",  2, 64,  4096, "inner_nc_ish", "post_prfm_l1_wb"),
        ]

        for rep in range(REPS):
            seq = list(reversed(stage2_cases)) if (rep % 2 == 1) else stage2_cases
            for exp, cond, k, w, rc, mm, am in seq:
                rec = run_case(exp, cond, k, w, rc, mm, am, rep, "stage2_custom_mair_0xf4")
                records.append(rec)

        with RUNS_JSONL.open("w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        print(f"\nSaved {len(records)} verified runs to {RUNS_JSONL}", flush=True)

    finally:
        mair_restore = restore_baseline()

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
    # After refl starts, BAR4_INDEX[15] is owned by default inline_reflector (ADDR_V=1)
    assert (int(card_after["bar4_index"]["15"], 16) & 1) == 1, (
        f"BAR4_INDEX[15] invalid after refl restart: {card_after['bar4_index']['15']}"
    )
    assert all(v == "0x0" for v in card_after["outbound_248_255"].values()), (
        f"PEM outbound 248..255 not zeroed: {card_after['outbound_248_255']}"
    )
    assert "refl:" in card_after["tmux_ls"], f"refl tmux not restored: {card_after['tmux_ls']}"
    assert all_zero, f"Non-zero EDAC/AER delta: aer={aer_delta} edac={edac_delta}"

    env = {
        "issue": "MRV-235",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_runs": len(records),
        "audit_wait_s": 21.0,
        "stage1_mair_info": stage1_info,
        "stage2_mair_info": stage2_info,
        "mair_restore_verify": mair_restore,
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
    print("ALL 132 RUNS + 24-CORE MAIR RESTORE + 21 s EDAC/AER ZERO-DELTA VERIFIED 100%!", flush=True)


if __name__ == "__main__":
    main()
