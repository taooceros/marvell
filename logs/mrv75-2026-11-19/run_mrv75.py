#!/usr/bin/env python3
"""MRV-75 experiment harness: hardware root cause of the 13 M/s closed-loop slow state
(WC4 W=64, MOVDIR64B + WC) and single-variable interventions (MRV-82).

Fixed baseline: MOVDIR64B -> BAR4 slot 15 + card 4x16 B write-combining (store_wc),
64 B cells, host cpu40 (performance 3.90 GHz), Socket-1 uncore 2.20 GHz, card 2.00 GHz.
"""
import csv
import json
import math
import pathlib
import socket
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv75")
LOG_DIR = ROOT / "logs/mrv75-2026-11-19"
STAMP_DIR = LOG_DIR / "stamps"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
HOST_BIN = ROOT / "testbed/target/release/inline_pingpong"
CARD_BIN = "/root/bench/inline_reflector.mrv75"
def wait_for_turbo(min_khz: int = 3_850_000, max_wait_s: float = 120.0):
    t0 = time.monotonic()
    while True:
        khz = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
        if khz >= min_khz:
            return khz
        if time.monotonic() - t0 > max_wait_s:
            return khz
        time.sleep(1.0)

REPS = 3
MIN_CPU_GHZ = 3.80


def check_host_env():
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    if gov != "performance":
        raise RuntimeError(f"cpu40 governor is {gov!r}, expected 'performance'")
    uncore = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz")):
        khz = int(p.read_text().strip())
        uncore[p.parent.name] = khz
        if khz != 2_200_000:
            raise RuntimeError(f"uncore {p.parent.name} = {khz} kHz, expected 2200000")
    paranoid = int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    if paranoid > 0:
        raise RuntimeError(f"perf_event_paranoid = {paranoid}, expected <= 0")
    return {"cpu40_governor": gov, "uncore_khz": uncore, "perf_event_paranoid": paranoid}


def read_card_edac():
    cmd = (
        "cat /sys/devices/system/edac/mc/mc0/ce_count "
        "/sys/devices/system/edac/mc/mc0/ue_count "
        "/sys/devices/system/edac/ea/ea0/ce_count "
        "/sys/devices/system/edac/ea/ea0/ue_count"
    )
    out = subprocess.check_output(["ssh", "root@10.10.0.2", cmd], text=True).strip().splitlines()
    vals = [int(x) for x in out]
    return {"mc0_ce": vals[0], "mc0_ue": vals[1], "ea0_ce": vals[2], "ea0_ue": vals[3]}


def start_card(lcores: str, stamp: bool = False, sparse: bool = False, settle_s: float = 1.0):
    extra = []
    if stamp:
        extra.append("--exp-stamp")
    if sparse:
        extra.append("--exp-stamp-sparse")
    extra_str = " ".join(extra)
    script = f"""
pkill -INT -f inline_reflector 2>/dev/null || true
for _ in $(seq 1 40); do pgrep -f inline_reflector >/dev/null || break; sleep 0.05; done
lsmod | grep -q pem_wc || insmod /root/bench/pem_wc.ko
rm -f /tmp/mrv75_card.log
nohup {CARD_BIN} -l {lcores} -a 0002:01:00.2 --file-prefix=inl -- --store --store-wc --sync 10.10.0.2:7527 {extra_str} > /tmp/mrv75_card.log 2>&1 &
for _ in $(seq 1 60); do
  grep -q "hosts lease the slot at" /tmp/mrv75_card.log 2>/dev/null && exit 0
  sleep 0.1
done
cat /tmp/mrv75_card.log >&2; exit 1
"""
    subprocess.run(["ssh", "root@10.10.0.2", script], check=True)
    if settle_s > 0:
        time.sleep(settle_s)


def set_card_pause(ns: int):
    with socket.create_connection(("10.10.0.2", 7527), timeout=5.0) as s:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.sendall(f"SET_PAUSE {ns}\n".encode())
        resp = s.recv(128).decode().strip()
    if not resp.startswith("OK"):
        raise RuntimeError(f"SET_PAUSE {ns} failed: {resp}")
    return resp


def measure_card_idle(rep: int, secs: float = 1.0):
    with socket.create_connection(("10.10.0.2", 7527), timeout=5.0) as s:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        f = s.makefile("rwb", buffering=0)
        f.write(b"PMU_START\n")
        resp = f.readline().decode().strip()
        assert resp == "OK", f"idle PMU_START failed: {resp}"
        time.sleep(secs)
        f.write(b"PMU_STOP\n")
        line = f.readline().decode().strip()
    cp = json.loads(line)
    assert cp["min_pct"] >= 99.9, f"idle card PMU mux: {cp}"
    return {
        "exp": "idle_card",
        "cond": "idle_1s_wc4",
        "rep": rep,
        "target_secs": secs,
        "card_pmu": cp,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def summarize_stamps(csv_path: pathlib.Path, card_hz: float):
    with csv_path.open() as f:
        hdr = f.readline().strip()
        # "# tsc_hz=... sb=t,c,rtt sa=t,c,rtt"
        parts = dict(tok.split("=") for tok in hdr.lstrip("# ").split())
        tsc_hz = float(parts["tsc_hz"])
        sb_t, sb_c, sb_rtt = [float(x) for x in parts["sb"].split(",")]
        sa_t, sa_c, sa_rtt = [float(x) for x in parts["sa"].split(",")]
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return {}

    # Linear clock map: host_ticks = sb_t + slope * (card_ticks - sb_c)
    # where slope = (sa_t - sb_t) / (sa_c - sb_c)
    dt_host = sa_t - sb_t
    dt_card = sa_c - sb_c
    slope = dt_host / dt_card
    nom_slope = tsc_hz / card_hz
    ppm = (slope / nom_slope - 1.0) * 1e6
    ns_per_host = 1e9 / tsc_hz
    ns_per_card = 1e9 / card_hz

    def to_host(c_ts: float) -> float:
        return sb_t + slope * (c_ts - sb_c)

    def pct(vals, q):
        if not vals:
            return 0.0
        s = sorted(vals)
        return s[int((len(s) - 1) * q)]

    def calc_stats(sub):
        h2c, c2h, card_t, rtt_s, cw, hw = [], [], [], [], [], []
        d_cin = []
        prev_cin = {}
        for r in sub:
            seq = int(r["seq"])
            st = float(r["send_tsc"])
            sn = float(r["seen_tsc"])
            cin = float(r["card_t_in"])
            cout = float(r["card_t_out"])
            cwp = int(r["card_waited_polls"])
            hwp = int(r["host_waited_polls"])
            cin_h = to_host(cin)
            cout_h = to_host(cout)
            h2c.append((cin_h - st) * ns_per_host)
            c2h.append((sn - cout_h) * ns_per_host)
            card_t.append((cout - cin) * ns_per_card)
            rtt_s.append((sn - st) * ns_per_host)
            cw.append(cwp)
            hw.append(hwp)
            if (seq - 1) in prev_cin:
                d_cin.append((cin - prev_cin[seq - 1]) * ns_per_card)
            prev_cin[seq] = cin
        return {
            "n": len(sub),
            "h2c_p50_ns": round(pct(h2c, 0.5), 1),
            "h2c_mean_ns": round(statistics.mean(h2c), 1),
            "h2c_p90_ns": round(pct(h2c, 0.9), 1),
            "c2h_p50_ns": round(pct(c2h, 0.5), 1),
            "c2h_mean_ns": round(statistics.mean(c2h), 1),
            "c2h_p90_ns": round(pct(c2h, 0.9), 1),
            "card_p50_ns": round(pct(card_t, 0.5), 1),
            "rtt_p50_ns": round(pct(rtt_s, 0.5), 1),
            "rtt_mean_ns": round(statistics.mean(rtt_s), 1),
            "cw_gt0_pct": round(100.0 * sum(1 for x in cw if x > 0) / len(cw), 2),
            "hw_gt0_pct": round(100.0 * sum(1 for x in hw if x > 0) / len(hw), 2),
            "cw_mean": round(statistics.mean(cw), 2),
            "d_cin_neg_pct": round(100.0 * sum(1 for x in d_cin if x < 0) / max(1, len(d_cin)), 2),
            "d_cin_p50_ns": round(pct(d_cin, 0.5), 1) if d_cin else 0.0,
        }

    out = {
        "ppm": round(ppm, 3),
        "sync_rtt_before_ns": round(sb_rtt * ns_per_host, 1),
        "sync_rtt_after_ns": round(sa_rtt * ns_per_host, 1),
        "all": calc_stats(rows),
    }
    # For MRV-50 pre-touch check: split before and after seq=20512
    pre = [r for r in rows if int(r["seq"]) < 20512]
    post = [r for r in rows if int(r["seq"]) >= 20512]
    if pre and post:
        out["pre_20512"] = calc_stats(pre)
        out["post_20512"] = calc_stats(post)
        # Also check max gap around multiples of 512 (seq = 20512, 21024, 21536, 22048...)
        seen_gaps_512 = []
        by_seq = {int(r["seq"]): float(r["seen_tsc"]) for r in rows}
        for s in range(20512, 24001, 512):
            if s in by_seq and (s - 1) in by_seq:
                seen_gaps_512.append(round((by_seq[s] - by_seq[s - 1]) * ns_per_host, 1))
        out["seen_gaps_at_512_ns"] = seen_gaps_512
    return out


def run_pingpong(
    exp: str,
    cond: str,
    rep: int,
    window: int,
    rounds: int,
    warmup: int = 20_000,
    max_send_burst: int = 0,
    send_gap_ns: int = 0,
    stamp: bool = False,
    stamp_sparse: bool = False,
    record_cap: int = 0,
    record_skip: int = 0,
    stamp_csv: pathlib.Path | None = None,
    pause_ns: int = 0,
    card_cores_label: int = 4,
    env_info: dict | None = None,
):
    cmd = [
        str(HOST_BIN),
        "-l", "40",
        "-a", "0000:99:02.0",
        "--file-prefix=mrv75",
        "--",
        "--reply", "ring",
        "--window", str(window),
        "--rounds", str(rounds),
        "--warmup", str(warmup),
        "--pmu",
    ]
    if max_send_burst > 0:
        cmd += ["--max-send-burst", str(max_send_burst)]
    if send_gap_ns > 0:
        cmd += ["--send-gap-ns", str(send_gap_ns)]
    if stamp:
        cmd.append("--stamp")
    if stamp_sparse:
        cmd.append("--stamp-sparse")
    if record_cap > 0:
        cmd += ["--record-cap", str(record_cap), "--record-skip", str(record_skip)]
    if stamp_csv is not None:
        cmd += ["--stamp-csv", str(stamp_csv)]
    for attempt in range(1, 25):
        wait_for_turbo(3_850_000, 120.0)
        p = subprocess.run(cmd, text=True, capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(f"{exp}/{cond} rep={rep} failed (rc={p.returncode}):\n{p.stderr[-500:]}\n{p.stdout[-500:]}")
        mrv_lines = [l for l in p.stdout.splitlines() if l.startswith("MRV75 ")]
        if not mrv_lines:
            raise RuntimeError(f"No MRV75 line for {exp}/{cond}: {p.stdout[-400:]}")
        rec = json.loads(mrv_lines[-1][6:])
        hp, cp = rec["host_pmu"], rec["card_pmu"]
        if rec["lost"] != 0 or rec["bad"] != 0 or rec["torn"] != 0:
            raise RuntimeError(f"Integrity error {exp}/{cond}: lost={rec['lost']} bad={rec['bad']} torn={rec['torn']}")
        if any(c["corrupt"] != 0 for c in cp["cores"]):
            raise RuntimeError(f"Card corrupt payload in {exp}/{cond}: {cp['cores']}")
        if hp["min_pct"] < 99.9 or cp["min_pct"] < 99.9:
            raise RuntimeError(f"PMU mux in {exp}/{cond}: host={hp['min_pct']} card={cp['min_pct']}")
        if hp["ghz"] < MIN_CPU_GHZ:
            print(f"  [retry {attempt}] {exp}/{cond} rep={rep}: cpu40 throttled to {hp['ghz']:.2f} GHz (< {MIN_CPU_GHZ}), waiting for turbo...", flush=True)
            time.sleep(3.0)
            continue
        break
    else:
        raise RuntimeError(f"{exp}/{cond} rep={rep}: cpu40 stayed below {MIN_CPU_GHZ} GHz after retries (last={hp['ghz']:.2f})")
    rec["exp"] = exp
    rec["cond"] = cond
    rec["rep"] = rep
    rec["pause_ns"] = pause_ns
    rec["card_cores_label"] = card_cores_label
    rec["env"] = env_info
    rec["timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if stamp_csv is not None and stamp_csv.exists():
        rec["stamp_csv"] = str(stamp_csv.relative_to(ROOT))
        rec["stamp_summary"] = summarize_stamps(stamp_csv, float(cp["tsc_hz"]))

    tot = rec["rounds"] + rec["warmup"]
    snp = cp["tad"]["tad_snp_msh_out_any"]["val"] / tot
    req = cp["tad"]["tad_req_msh_in_any"]["val"] / tot
    l2 = sum(c["l2d_cache_refill"] for c in cp["cores"]) / tot
    bb = sum(c["taken"] for c in cp["cores"]) / max(1, sum(c["busy_polls"] for c in cp["cores"]))
    h2c_str = ""
    if "stamp_summary" in rec and "all" in rec["stamp_summary"]:
        a = rec["stamp_summary"]["all"]
        h2c_str = f" | h2c={a['h2c_p50_ns']/1000:.2f}us c2h={a['c2h_p50_ns']/1000:.2f}us cw>0={a['cw_gt0_pct']:.0f}%"
    print(
        f"[{exp:14s}] {cond:14s} r{rep} | {rec['mops']:6.2f} M/s RTT={rec['rtt_p50_us']:6.2f}us | "
        f"ghz={hp['ghz']:.2f} SB={hp['sb_pct']:4.1f}% CHA={hp['cha_inflight']:6.2f}({hp['cha_res_ns']:6.1f}ns) IRP={hp['irp1_inflight_nom']:4.2f} | "
        f"snp/m={snp:4.2f} req/m={req:4.2f} L2/m={l2:4.2f} bat={bb:4.1f}{h2c_str}"
    )
    return rec


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STAMP_DIR.mkdir(parents=True, exist_ok=True)
    env_info = check_host_env()
    edac_before = read_card_edac()
    print(f"Host env OK: {env_info}")
    print(f"Card EDAC before: {edac_before}")

    run_id = 0
    with RUNS_JSONL.open("w") as out_f:
        def save(rec):
            nonlocal run_id
            run_id += 1
            rec["id"] = run_id
            out_f.write(json.dumps(rec) + "\n")
            out_f.flush()

        # =====================================================================
        # PHASE A: WC4 (4 card cores, unstamped pure PMU)
        #   - Idle baseline (3 reps)
        #   - Part 1A/1B: WC4 X-Ray across W in {1, 16, 32, 40, 48, 64, 128, 1024}
        #   - Part 2A: WC4 W=64 T_pause sweep {0, 200, 500, 1000, 1500, 1800, 2000, 3000}
        #   - Part 3A: WC4 W=64 max_send_burst sweep {1, 2, 4, 8, 12, 16, 32, 64}
        #   - Part 3B: WC4 W=64 host_gap_ns sweep {0, 10, 20, 40, 60}
        # =====================================================================
        print("\n=== Starting WC4 (4 card cores, pure PMU, no stamp) ===")
        start_card("3-6", stamp=False, sparse=False, settle_s=1.0)
        for rep in range(REPS):
            set_card_pause(0)
            save(measure_card_idle(rep, 1.0))

        # Build interleaved schedule for WC4 pure PMU runs
        wc4_windows = [
            ("part1_xray", "wc4_w1",    1,    1_000_000, 10_000),
            ("part1_wscan", "wc4_w16",  16,   8_000_000, 20_000),
            ("part1_wscan", "wc4_w32",  32,  10_000_000, 20_000),
            ("part1_wscan", "wc4_w40",  40,  10_000_000, 20_000),
            ("part1_wscan", "wc4_w48",  48,  10_000_000, 20_000),
            ("part1_xray", "wc4_w64",   64,  12_000_000, 20_000),
            ("part1_wscan", "wc4_w128", 128, 10_000_000, 20_000),
            ("part1_wscan", "wc4_w1024",1024,20_000_000, 20_000),
        ]
        pauses = [0, 200, 500, 1000, 1500, 1800, 2000, 3000]
        bursts = [1, 2, 4, 8, 12, 16, 32, 64]
        gaps = [0, 10, 20, 40, 60]

        for rep in range(REPS):
            print(f"\n--- WC4 Pure PMU Rep {rep} ---")
            # 1. Window scan (interleaved direction)
            set_card_pause(0)
            w_order = wc4_windows if rep % 2 == 0 else list(reversed(wc4_windows))
            for exp, cond, w, rnds, wm in w_order:
                save(run_pingpong(exp, cond, rep, window=w, rounds=rnds, warmup=wm, pause_ns=0, card_cores_label=4, env_info=env_info))

            # 2. Part 2A: T_pause sweep on WC4 W=64
            p_order = pauses if rep % 2 == 0 else list(reversed(pauses))
            for p_ns in p_order:
                set_card_pause(p_ns)
                save(run_pingpong("part2_pause", f"pause_{p_ns}ns", rep, window=64, rounds=12_000_000, pause_ns=p_ns, card_cores_label=4, env_info=env_info))

            # 3. Part 3A: max_send_burst sweep on WC4 W=64 (T_pause=0)
            set_card_pause(0)
            b_order = bursts if rep % 2 == 0 else list(reversed(bursts))
            for b in b_order:
                save(run_pingpong("part3_burst", f"burst_{b}", rep, window=64, rounds=10_000_000, max_send_burst=b, pause_ns=0, card_cores_label=4, env_info=env_info))

            # 4. Part 3B: host_gap_ns sweep on WC4 W=64 (T_pause=0)
            g_order = gaps if rep % 2 == 0 else list(reversed(gaps))
            for g in g_order:
                save(run_pingpong("part3_gap", f"gap_{g}ns", rep, window=64, rounds=10_000_000, send_gap_ns=g, pause_ns=0, card_cores_label=4, env_info=env_info))

        # =====================================================================
        # PHASE B: WC1 (1 card core, wait >=12s for steady fast state) + WC2 (2 card cores)
        # =====================================================================
        print("\n=== Starting WC1 (1 card core, waiting 12s for steady state) ===")
        start_card("3", stamp=False, sparse=False, settle_s=12.0)
        # Short warmup run to ensure WC1 is firmly in steady fast state
        _ = run_pingpong("warmup_check", "wc1_w64_warm", 0, window=64, rounds=5_000_000, card_cores_label=1, env_info=env_info)
        wc1_specs = [
            ("part1_xray", "wc1_w1",    1,    1_000_000, 10_000),
            ("part1_xray", "wc1_w64",   64,  25_000_000, 20_000),
            ("part1_xray", "wc1_w1024", 1024,40_000_000, 20_000),
        ]
        for rep in range(REPS):
            order = wc1_specs if rep % 2 == 0 else list(reversed(wc1_specs))
            for exp, cond, w, rnds, wm in order:
                save(run_pingpong(exp, cond, rep, window=w, rounds=rnds, warmup=wm, card_cores_label=1, env_info=env_info))

        print("\n=== Starting WC2 (2 card cores, W=64 & W=1024) ===")
        start_card("3-4", stamp=False, sparse=False, settle_s=3.0)
        wc2_specs = [
            ("part2_cores", "wc2_w64",   64,  12_000_000, 20_000),
            ("part2_cores", "wc2_w1024", 1024,25_000_000, 20_000),
        ]
        for rep in range(REPS):
            order = wc2_specs if rep % 2 == 0 else list(reversed(wc2_specs))
            for exp, cond, w, rnds, wm in order:
                save(run_pingpong(exp, cond, rep, window=w, rounds=rnds, warmup=wm, card_cores_label=2, env_info=env_info))

        # =====================================================================
        # PHASE C: MRV-50 Real 4 KiB Non-Zero Pre-Touch Check (4 reps, dense stamp on WC4 W=64)
        # =====================================================================
        print("\n=== Starting WC4 Dense Stamp (MRV-50 real non-zero pre-touch check, 4 reps) ===")
        start_card("3-6", stamp=True, sparse=False, settle_s=1.0)
        for rep in range(4):
            csv_p = STAMP_DIR / f"prefault_wc4_w64_rep{rep}.csv"
            save(run_pingpong(
                "part1_prefault", "wc4_w64_dense", rep,
                window=64, rounds=2_000_000, warmup=20_000,
                stamp=True, stamp_sparse=False, record_cap=4000, record_skip=0,
                stamp_csv=csv_p, pause_ns=0, card_cores_label=4, env_info=env_info,
            ))

        # =====================================================================
        # PHASE D: Paired Sparse Stamp (h2c / c2h decomposition across T_pause, burst, gap, W)
        # =====================================================================
        print("\n=== Starting WC4 Sparse Stamp (h2c / c2h for Part 1, 2, 3) ===")
        start_card("3-6", stamp=True, sparse=True, settle_s=1.0)
        # Key W points
        for w in [1, 40, 48, 64, 128]:
            set_card_pause(0)
            rnds = 500_000 if w == 1 else 4_000_000
            skip = 100_000 if w == 1 else 1_000_000
            csv_p = STAMP_DIR / f"sparse_wc4_w{w}.csv"
            save(run_pingpong(
                "part1_stamp", f"wc4_w{w}_sp", 0,
                window=w, rounds=rnds, warmup=10_000 if w == 1 else 20_000,
                stamp=True, stamp_sparse=True, record_cap=4000, record_skip=skip,
                stamp_csv=csv_p, pause_ns=0, card_cores_label=4, env_info=env_info,
            ))
        # Key T_pause points
        for p_ns in [0, 500, 1000, 1500, 1800, 2000, 3000]:
            set_card_pause(p_ns)
            csv_p = STAMP_DIR / f"sparse_pause_{p_ns}ns.csv"
            save(run_pingpong(
                "part2_stamp", f"pause_{p_ns}ns_sp", 0,
                window=64, rounds=4_000_000, warmup=20_000,
                stamp=True, stamp_sparse=True, record_cap=4000, record_skip=1_000_000,
                stamp_csv=csv_p, pause_ns=p_ns, card_cores_label=4, env_info=env_info,
            ))
        # Key burst & gap points
        set_card_pause(0)
        for b in [1, 4, 8, 12, 16, 64]:
            csv_p = STAMP_DIR / f"sparse_burst_{b}.csv"
            save(run_pingpong(
                "part3_stamp", f"burst_{b}_sp", 0,
                window=64, rounds=4_000_000, warmup=20_000,
                max_send_burst=b, stamp=True, stamp_sparse=True, record_cap=4000, record_skip=1_000_000,
                stamp_csv=csv_p, pause_ns=0, card_cores_label=4, env_info=env_info,
            ))
        for g in [10, 20, 40, 60]:
            csv_p = STAMP_DIR / f"sparse_gap_{g}ns.csv"
            save(run_pingpong(
                "part3_stamp", f"gap_{g}ns_sp", 0,
                window=64, rounds=4_000_000, warmup=20_000,
                send_gap_ns=g, stamp=True, stamp_sparse=True, record_cap=4000, record_skip=1_000_000,
                stamp_csv=csv_p, pause_ns=0, card_cores_label=4, env_info=env_info,
            ))

    edac_after = read_card_edac()
    print(f"\nCard EDAC before: {edac_before}")
    print(f"Card EDAC after:  {edac_after}")
    if edac_after != edac_before:
        raise RuntimeError(f"EDAC delta non-zero! before={edac_before} after={edac_after}")
    print(f"ALL {run_id} RUNS COMPLETED CLEANLY -> {RUNS_JSONL}")


if __name__ == "__main__":
    main()
