#!/usr/bin/env python3
"""MRV-231 full n=3 interleaved experiment harness.

Proves via three single-variable controls (MRV-82):
  1. Control 1 (pure on-chip ARM <-> ARM, zero PCIe): CN106 TAD total snoop
     bandwidth ceiling across K = 1 / 2 / 4 / 8 core pairs (128 B strided
     L1D-resident verified ring + single-line hot-thrash verification).
  2. Control 2 (BAR4 slot 15, MRV-26 leased, closed-loop W=64 WC replies,
     zero BAR4 credit reads): K = 1 / 2 / 4 independent host cores (Socket 1
     cpu40..43) x K independent 960 KiB-spaced sub-rings x independent card
     cores + single host core (cpu40) striping across K sub-rings + card-silent
     no-poll MOVDIR64B direct-push baselines (K=1, K=4).
  3. Control 3 (card-side read ordering relaxation): same-slot single-variable
     comparison of ldar vs weak ldr vs ldr + dmb ishld vs ldnp on the exact
     ~13.4 M/s serialized anchor (K=1, 2c/lane, W=64).

Strictly pins all host threads to Socket 1 (cpu32..63), zero interference with
Socket 0 (MRV-230). Restores full card/host baseline and runs 21 s EDAC/AER
zero-delta audit into env.json on exit.
"""
import json
import pathlib
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv231")
LOG_DIR = ROOT / "logs/mrv231-2026-11-27"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
ENV_JSON = LOG_DIR / "env.json"
HOST_BIN = ROOT / "testbed/target/release/mrv231_host"
CARD_BIN = "/root/bench/mrv231_card"

REPS = 3
WARMUP_MS = 500
DURATION_MS = 1000


def ssh_card(cmd: str) -> str:
    return subprocess.check_output(["ssh", "-o", "BatchMode=yes", "root@10.10.0.2", cmd], text=True)


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
# Read BAR4_INDEX[0..15] and PEM0_REG_NORM_ACC[248..255] via single 64-bit loads (never slice!)
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x8e0000000000)
q = (ctypes.c_uint64 * 8192).from_buffer(m)
bar4 = {i: f"0x{q[(0x700 + i*8)>>3]:x}" for i in range(16)}
outb = {i: f"0x{q[(0x4000 + i*16)>>3]:x}" for i in range(248, 256)}
del q; m.close(); os.close(fd)

pem_wc_loaded = "pem_wc" in open("/proc/modules").read()
eth0_out = subprocess.check_output(["ethtool", "eth0"], text=True)
eth0_speed = next((l.split(":",1)[1].strip() for l in eth0_out.splitlines() if "Speed:" in l), "unknown")
eth0_link = next((l.split(":",1)[1].strip() for l in eth0_out.splitlines() if "Link detected:" in l), "unknown")
tmux_ls = subprocess.run(["tmux", "ls"], capture_output=True, text=True).stdout.strip()
print(json.dumps({
    "edac": edac,
    "bar4_index": bar4,
    "outbound_248_255": outb,
    "pem_wc_loaded": pem_wc_loaded,
    "eth0_speed": eth0_speed,
    "eth0_link": eth0_link,
    "tmux_ls": tmux_ls,
}))
PY"""
    return json.loads(ssh_card(script).strip())


def run_arm_snoop(pattern: str, pairs: int, rep: int) -> dict:
    if pairs == 0:
        cmd = f"{CARD_BIN} --mode arm-snoop --pairs 0 --warmup-ms 0 --duration-ms {DURATION_MS} --rep {rep}"
    else:
        cmd = (
            f"{CARD_BIN} --mode arm-snoop --pairs {pairs} --arm-pattern {pattern} "
            f"--ring-cells 64 --warmup-ms {WARMUP_MS} --duration-ms {DURATION_MS} --rep {rep}"
        )
    out = ssh_card(cmd)
    lines = [l for l in out.splitlines() if l.startswith("MRV231_ARM ")]
    assert lines, f"No MRV231_ARM output: {out}"
    rec = json.loads(lines[-1][11:])
    assert rec["corrupt"] == 0 and rec["lost"] == 0, f"Integrity error in arm_snoop: {rec}"
    assert rec["min_pct"] >= 99.9, f"PMU mux in arm_snoop: {rec['min_pct']}"
    rec["exp"] = "ctrl1_arm_snoop"
    rec["cond"] = "idle" if pairs == 0 else f"arm_{pattern}_K{pairs}"
    rec["timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dt = rec["elapsed_ns"] / 1e9
    snp_s = rec["tad"]["tad_snp_msh_out_any"]["val"] / 1e6 / dt
    req_s = rec["tad"]["tad_req_msh_in_any"]["val"] / 1e6 / dt
    tad_inf = rec["tad"]["tad_req_occ"]["val"] / (dt * 2.0e9)
    print(
        f"[ctrl1] {rec['cond']:14s} r{rep} | msgs={rec['mops']:7.2f} M/s | "
        f"TAD_snp={snp_s:6.1f} M/s TAD_req={req_s:6.1f} M/s TAD_inf={tad_inf:5.2f} | "
        f"pct={rec['min_pct']:.1f}% lost={rec['lost']} cor={rec['corrupt']}",
        flush=True,
    )
    return rec


def start_bar4_server():
    ssh_card(
        f"pkill -INT -f mrv231_card 2>/dev/null || true; sleep 0.3; "
        f"lsmod | grep -q pem_wc || insmod /root/bench/pem_wc.ko; "
        f"rm -f /tmp/mrv231_card.log; "
        f"nohup {CARD_BIN} --mode bar4-server --wc > /tmp/mrv231_card.log 2>&1 & "
        f"for _ in $(seq 1 50); do grep -q 'hosts lease the slot at' /tmp/mrv231_card.log 2>/dev/null && exit 0; sleep 0.1; done; "
        f"cat /tmp/mrv231_card.log >&2; exit 1"
    )


def stop_bar4_server():
    ssh_card(
        "pkill -INT -f mrv231_card 2>/dev/null || true; "
        "for _ in $(seq 1 50); do pgrep -f mrv231_card >/dev/null || break; sleep 0.1; done"
    )


def run_bar4_case(exp: str, cond: str, extra_args: list[str], rep: int) -> dict:
    cmd = [
        str(HOST_BIN),
        *extra_args,
        "--warmup-ms", str(WARMUP_MS),
        "--duration-ms", str(DURATION_MS),
        "--exp", exp,
        "--cond", cond,
        "--rep", str(rep),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"FAIL {exp}/{cond} r{rep} rc={p.returncode}\nSTDERR:\n{p.stderr[-500:]}\nSTDOUT:\n{p.stdout[-500:]}")
    lines = [l for l in p.stdout.splitlines() if l.startswith("MRV231_BAR4 ")]
    assert lines, f"No MRV231_BAR4 line: {p.stdout[-400:]}"
    rec = json.loads(lines[-1][12:])
    hp, cp = rec["host_pmu"], rec["card_pmu"]
    corrupt = sum(w["corrupt"] for w in cp["workers"])
    partial = sum(w["partial"] for w in cp["workers"])
    assert rec["lost"] == 0, f"lost={rec['lost']} in {cond} r{rep}"
    assert corrupt == 0, f"corrupt={corrupt} in {cond} r{rep}"
    assert rec["host_min_pct"] >= 99.9 and cp["min_pct"] >= 99.9, (
        f"PMU mux in {cond} r{rep}: host={rec['host_min_pct']} card={cp['min_pct']}"
    )
    rec["timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dt = cp["elapsed_ns"] / 1e9
    snp_s = cp["tad"]["tad_snp_msh_out_any"]["val"] / 1e6 / dt
    tad_inf = cp["tad"]["tad_req_occ"]["val"] / (dt * 2.0e9)
    tak_s = sum(w["taken_win"] for w in cp["workers"]) / 1e6 / dt
    emp_s = sum(w["empty_win"] for w in cp["workers"]) / 1e6 / dt
    per_l = ",".join(f"{l['mops']:.2f}" for l in rec["per_lane"])
    ghzs = ",".join(f"{c['ghz']:.2f}" for c in hp["cores"])
    print(
        f"[{exp:14s}] {cond:16s} r{rep} | mops={rec['mops']:6.2f} [{per_l:23s}] | "
        f"snp={snp_s:5.1f}M/s ({snp_s/max(tak_s,0.01):4.2f}/m) TAD_inf={tad_inf:4.2f} emp={emp_s:6.1f}M/s | "
        f"CHA={hp['cha_inflight']:5.2f}({hp['cha_res_ns']:5.0f}ns) | par={partial} pct={rec['host_min_pct']:.0f}/{cp['min_pct']:.0f} ghz=[{ghzs}]",
        flush=True,
    )
    return rec


def restore_baseline():
    print("\n=== Restoring card & host baseline ===", flush=True)
    ssh_card(
        "pkill -INT -f mrv231_card 2>/dev/null || true; "
        "for _ in $(seq 1 50); do pgrep -f mrv231_card >/dev/null || break; sleep 0.1; done; "
        "/root/bench/inline_reflector --restore; "
        "lsmod | grep -q pem_wc && rmmod pem_wc || true; "
        "rm -f /root/bench/mrv231_card /tmp/mrv231_card.log; "
        "tmux has-session -t refl 2>/dev/null || "
        "tmux new-session -d -s refl '/root/bench/inline_reflector -l 3 -a 0002:01:00.2 --file-prefix=refl'; "
        "sleep 1.5; tmux ls"
    )


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    host_before = check_host_env()
    card_before = read_card_state()
    print(f"Host before: {host_before}", flush=True)
    print(f"Card before: eth0={card_before['eth0_speed']}/{card_before['eth0_link']} pem_wc={card_before['pem_wc_loaded']} mc0={card_before['edac']['mc/mc0']} ea0={card_before['edac']['ea/ea0']}", flush=True)

    run_id = 0
    with RUNS_JSONL.open("w") as out_f:
        def save(rec):
            nonlocal run_id
            run_id += 1
            rec["id"] = run_id
            out_f.write(json.dumps(rec) + "\n")
            out_f.flush()

        # =====================================================================
        # CONTROL 1: Pure on-chip ARM <-> ARM TAD snoop bandwidth ceiling (zero PCIe)
        # =====================================================================
        print("\n=== CONTROL 1: Pure on-chip ARM <-> ARM TAD snoop ceiling (n=3 interleaved) ===", flush=True)
        ctrl1_conds = [
            ("idle", 0),
            ("ring", 1),
            ("ring", 2),
            ("ring", 4),
            ("ring", 8),
            ("hotline", 1),
            ("hotline", 2),
            ("hotline", 4),
            ("hotline", 8),
        ]
        for rep in range(REPS):
            order = ctrl1_conds if rep % 2 == 0 else list(reversed(ctrl1_conds))
            for pat, pairs in order:
                save(run_arm_snoop(pat, pairs, rep))

        # =====================================================================
        # CONTROL 2 & 3: BAR4 slot 15 (MRV-26 leased) closed-loop WC + no_poll
        # =====================================================================
        print("\n=== Starting mrv231_card bar4-server (--wc, slot 15 only) ===", flush=True)
        start_bar4_server()
        try:
            ctrl2_conds = [
                ("nopoll_K1_1h",    ["--lanes","1","--host-cpus","40","--poll-mode","none"]),
                ("nopoll_K4_4h",    ["--lanes","4","--host-cpus","40,41,42,43","--poll-mode","none"]),
                ("cl_K1_2c",        ["--closed-loop","--lanes","1","--cards-per-lane","2","--host-cpus","40","--window","64","--poll-mode","ldar"]),
                ("cl_K2_2c",        ["--closed-loop","--lanes","2","--cards-per-lane","2","--host-cpus","40,41","--window","64","--poll-mode","ldar"]),
                ("cl_K4_2c",        ["--closed-loop","--lanes","4","--cards-per-lane","2","--host-cpus","40,41,42,43","--window","64","--poll-mode","ldar"]),
                ("cl_K1_1c",        ["--closed-loop","--lanes","1","--cards-per-lane","1","--host-cpus","40","--window","64","--poll-mode","ldar"]),
                ("cl_K2_1c",        ["--closed-loop","--lanes","2","--cards-per-lane","1","--host-cpus","40,41","--window","64","--poll-mode","ldar"]),
                ("cl_K4_1c",        ["--closed-loop","--lanes","4","--cards-per-lane","1","--host-cpus","40,41,42,43","--window","64","--poll-mode","ldar"]),
                ("stripe_K2_1c",    ["--closed-loop","--lanes","2","--cards-per-lane","1","--host-cpus","40","--stripe","--window","64","--poll-mode","ldar"]),
                ("stripe_K4_1c_W16",["--closed-loop","--lanes","4","--cards-per-lane","1","--host-cpus","40","--stripe","--window","16","--poll-mode","ldar"]),
                ("stripe_K4_1c_W64",["--closed-loop","--lanes","4","--cards-per-lane","1","--host-cpus","40","--stripe","--window","64","--poll-mode","ldar"]),
            ]
            print("\n=== CONTROL 2: BAR4 slot 15 K=1/2/4 scaling + stripe + no_poll (n=3 interleaved) ===", flush=True)
            for rep in range(REPS):
                order = ctrl2_conds if rep % 2 == 0 else list(reversed(ctrl2_conds))
                for cond, extra in order:
                    save(run_bar4_case("ctrl2_bar4_scale", cond, extra, rep))

            ctrl3_conds = [
                ("order_ldar",    ["--closed-loop","--lanes","1","--cards-per-lane","2","--host-cpus","40","--window","64","--poll-mode","ldar"]),
                ("order_ldr",     ["--closed-loop","--lanes","1","--cards-per-lane","2","--host-cpus","40","--window","64","--poll-mode","ldr"]),
                ("order_ldr_dmb", ["--closed-loop","--lanes","1","--cards-per-lane","2","--host-cpus","40","--window","64","--poll-mode","ldr_dmb"]),
                ("order_ldnp",    ["--closed-loop","--lanes","1","--cards-per-lane","2","--host-cpus","40","--window","64","--poll-mode","ldnp"]),
            ]
            print("\n=== CONTROL 3: Card read-ordering relaxation ldar vs ldr vs ldr+dmb vs ldnp (n=3 interleaved) ===", flush=True)
            for rep in range(REPS):
                order = ctrl3_conds if rep % 2 == 0 else list(reversed(ctrl3_conds))
                for cond, extra in order:
                    save(run_bar4_case("ctrl3_read_order", cond, extra, rep))
        finally:
            stop_bar4_server()

    restore_baseline()
    print("Waiting 21 s for delayed EDAC/AER audit...", flush=True)
    time.sleep(21.0)
    host_after = check_host_env()
    card_after = read_card_state()

    # Compute exact EDAC and AER deltas
    edac_delta = {}
    for k, v in card_after["edac"].items():
        b = card_before["edac"].get(k, {"ce": 0, "ue": 0})
        edac_delta[k] = {"ce_delta": v["ce"] - b["ce"], "ue_delta": v["ue"] - b["ue"]}
    aer_delta = {k: host_after["aer"][k] - host_before["aer"][k] for k in host_after["aer"]}

    env_record = {
        "issue": "MRV-231",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_runs": run_id,
        "audit_wait_s": 21.0,
        "host_before": host_before,
        "host_after": host_after,
        "aer_delta": aer_delta,
        "card_before": card_before,
        "card_after": card_after,
        "edac_delta": edac_delta,
        "all_zero_edac_aer_delta": (
            all(v["ce_delta"] == 0 and v["ue_delta"] == 0 for v in edac_delta.values())
            and all(v == 0 for v in aer_delta.values())
        ),
    }
    ENV_JSON.write_text(json.dumps(env_record, indent=2) + "\n")
    print(f"\nALL {run_id} RUNS DONE! EDAC/AER zero-delta={env_record['all_zero_edac_aer_delta']} -> {ENV_JSON}", flush=True)
    assert env_record["all_zero_edac_aer_delta"], f"Non-zero EDAC/AER delta: {edac_delta} {aer_delta}"
    assert not card_after["pem_wc_loaded"], "pem_wc still loaded!"
    assert card_after["eth0_speed"] == "100000Mb/s" and card_after["eth0_link"] == "yes", f"eth0 bad: {card_after}"
    assert "refl:" in card_after["tmux_ls"], f"refl tmux missing: {card_after['tmux_ls']}"
    for i in range(248, 256):
        assert card_after["outbound_248_255"][str(i)] == "0x0", f"outbound {i} not 0"
    print("BASELINE RESTORE 100% VERIFIED!", flush=True)


if __name__ == "__main__":
    main()
