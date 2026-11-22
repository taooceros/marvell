#!/usr/bin/env python3
"""MRV-112 experiment harness: single-variable BAR4 request-ring cell stride comparison
(64 B / 128 B / 4096 B, plus 4224 B anti-aliasing & matched-capacity / WC4 / W=128 / pad_next controls).

Fixed baseline (MRV-82): MOVDIR64B -> BAR4 slot 15 + card 4x16 B write-combining (store_wc),
64 B effective payload, host batch = 1, card busy-poll batch cap = 32, T_pause = 0,
host cpu40 (performance 3.90 GHz), Socket-1 uncore 2.20 GHz, card 2.00 GHz.
"""
import csv
import json
import pathlib
import re
import socket
import statistics
import subprocess
import time

LOG_DIR = pathlib.Path(__file__).resolve().parent
ROOT = LOG_DIR.parents[1]
STAMP_DIR = LOG_DIR / "stamps"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
HOST_BIN = ROOT / "testbed/target/release/inline_pingpong"
CARD_BIN = "/root/bench/inline_reflector.mrv112"

MIN_CPU_GHZ = 3.80


def wait_for_turbo(min_khz: int = 3_850_000, max_wait_s: float = 120.0):
    t0 = time.monotonic()
    while True:
        khz = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
        if khz >= min_khz or time.monotonic() - t0 > max_wait_s:
            return khz
        time.sleep(0.5)


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
    card_gov_freq = subprocess.check_output(
        [
            "ssh",
            "root@10.10.0.2",
            "cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_governor /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq",
        ],
        text=True,
    ).strip().splitlines()
    return {
        "cpu40_governor": gov,
        "uncore_khz": uncore,
        "perf_event_paranoid": paranoid,
        "card_cpu3_governor": card_gov_freq[0],
        "card_cpu3_khz": int(card_gov_freq[1]),
    }


def read_card_edac():
    cmd = (
        "cat /sys/devices/system/edac/mc/mc0/ce_count "
        "/sys/devices/system/edac/mc/mc0/ue_count "
        "/sys/devices/system/edac/ea/ea0/ce_count "
        "/sys/devices/system/edac/ea/ea0/ue_count; "
        "dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || true"
    )
    out = subprocess.check_output(["ssh", "root@10.10.0.2", cmd], text=True).strip().splitlines()
    vals = [int(x) for x in out]
    return {
        "mc0_ce": vals[0],
        "mc0_ue": vals[1],
        "ea0_ce": vals[2],
        "ea0_ue": vals[3],
        "sei_uer_dmesg": vals[4],
    }


def start_card(
    lcores: str,
    req_stride: int,
    req_ring_cells: int = 0,
    pad_next_128: bool = False,
    stamp: bool = False,
    sparse: bool = False,
    settle_s: float = 0.4,
) -> dict:
    extra = [f"--req-stride {req_stride}"]
    if req_ring_cells > 0:
        extra.append(f"--req-ring-cells {req_ring_cells}")
    if pad_next_128:
        extra.append("--pad-next-128")
    if stamp:
        extra.append("--exp-stamp")
    if sparse:
        extra.append("--exp-stamp-sparse")
    extra_str = " ".join(extra)
    script = f"""
pkill -INT -f inline_reflector 2>/dev/null || true
for _ in $(seq 1 40); do pgrep -f inline_reflector >/dev/null || break; sleep 0.05; done
lsmod | grep -q pem_wc || insmod /root/bench/pem_wc.ko
rm -f /tmp/mrv112_card.log
nohup {CARD_BIN} -l {lcores} -a 0002:01:00.2 --file-prefix=inl -- --store --store-wc --sync 10.10.0.2:7527 {extra_str} > /tmp/mrv112_card.log 2>&1 &
for _ in $(seq 1 60); do
  grep -q "hosts lease the slot at" /tmp/mrv112_card.log 2>/dev/null && break
  sleep 0.1
done
grep -q "hosts lease the slot at" /tmp/mrv112_card.log 2>/dev/null || {{ cat /tmp/mrv112_card.log >&2; exit 1; }}
grep "BAR4 slot" /tmp/mrv112_card.log | head -n 1
"""
    banner = subprocess.check_output(["ssh", "root@10.10.0.2", script], text=True).strip()
    m_pa = re.search(r"-> pa (0x[0-9a-fA-F]+)", banner)
    if settle_s > 0:
        time.sleep(settle_s)
    return {"banner": banner, "bar4_pa": m_pa.group(1) if m_pa else "unknown"}


def measure_card_idle(cond: str, card_cores: int, rep: int, secs: float = 1.0):
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
        "cond": cond,
        "card_cores_label": card_cores,
        "rep": rep,
        "target_secs": secs,
        "card_pmu": cp,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def summarize_stamps(csv_path: pathlib.Path, card_hz: float):
    with csv_path.open() as f:
        hdr = f.readline().strip()
        parts = dict(tok.split("=") for tok in hdr.lstrip("# ").split())
        tsc_hz = float(parts["tsc_hz"])
        sb_t, sb_c, sb_rtt = [float(x) for x in parts["sb"].split(",")]
        sa_t, sa_c, sa_rtt = [float(x) for x in parts["sa"].split(",")]
        rows = list(csv.DictReader(f))
    if not rows:
        return {}
    slope = (sa_t - sb_t) / (sa_c - sb_c)
    nom_slope = tsc_hz / card_hz
    ppm = (slope / nom_slope - 1.0) * 1e6
    ns_h = 1e9 / tsc_hz
    ns_c = 1e9 / card_hz

    def to_host(c_ts: float) -> float:
        return sb_t + slope * (c_ts - sb_c)

    def pct(vals, q):
        s = sorted(vals)
        return s[int((len(s) - 1) * q)]

    h2c, c2h, card_t, rtts, cw, hw = [], [], [], [], [], []
    by_blk = {}
    for r in rows:
        seq = int(r["seq"])
        st, sn = float(r["send_tsc"]), float(r["seen_tsc"])
        cin, cout = float(r["card_t_in"]), float(r["card_t_out"])
        h2c.append((to_host(cin) - st) * ns_h)
        c2h.append((sn - to_host(cout)) * ns_h)
        card_t.append((cout - cin) * ns_c)
        rtts.append((sn - st) * ns_h)
        cw.append(int(r["card_waited_polls"]))
        hw.append(int(r["host_waited_polls"]))
        by_blk.setdefault(seq >> 6, []).append((cin, seq))

    dcin_sorted = []
    for blk in by_blk.values():
        blk.sort()
        for i in range(1, len(blk)):
            dcin_sorted.append((blk[i][0] - blk[i - 1][0]) * ns_c)

    n = len(rows)
    return {
        "n": n,
        "ppm": round(ppm, 3),
        "sync_rtt_before_ns": round(sb_rtt * ns_h, 1),
        "sync_rtt_after_ns": round(sa_rtt * ns_h, 1),
        "h2c_p50_ns": round(pct(h2c, 0.5), 1),
        "h2c_mean_ns": round(statistics.mean(h2c), 1),
        "h2c_p90_ns": round(pct(h2c, 0.9), 1),
        "c2h_p50_ns": round(pct(c2h, 0.5), 1),
        "c2h_mean_ns": round(statistics.mean(c2h), 1),
        "card_p50_ns": round(pct(card_t, 0.5), 1),
        "rtt_p10_ns": round(pct(rtts, 0.1), 1),
        "rtt_p50_ns": round(pct(rtts, 0.5), 1),
        "rtt_p90_ns": round(pct(rtts, 0.9), 1),
        "rtt_lt1_5us_pct": round(100.0 * sum(1 for x in rtts if x < 1500.0) / n, 2),
        "rtt_1_5_to_3us_pct": round(100.0 * sum(1 for x in rtts if 1500.0 <= x < 3000.0) / n, 2),
        "rtt_ge3us_pct": round(100.0 * sum(1 for x in rtts if x >= 3000.0) / n, 2),
        "cw_gt0_pct": round(100.0 * sum(1 for x in cw if x > 0) / n, 2),
        "hw_gt0_pct": round(100.0 * sum(1 for x in hw if x > 0) / n, 2),
        "dcin_sorted_n": len(dcin_sorted),
        "dcin_sorted_p05_ns": round(pct(dcin_sorted, 0.05), 1) if dcin_sorted else 0.0,
        "dcin_sorted_p50_ns": round(pct(dcin_sorted, 0.5), 1) if dcin_sorted else 0.0,
        "dcin_sorted_p90_ns": round(pct(dcin_sorted, 0.9), 1) if dcin_sorted else 0.0,
        "dcin_sorted_lt50_pct": round(100.0 * sum(1 for x in dcin_sorted if x < 50.0) / max(1, len(dcin_sorted)), 2),
    }


def run_pingpong(
    exp: str,
    cond: str,
    rep: int,
    window: int,
    req_stride: int,
    rounds: int,
    warmup: int = 20_000,
    card_cores_label: int = 2,
    pad_next_128: bool = False,
    card_info: dict | None = None,
    stamp: bool = False,
    stamp_sparse: bool = False,
    record_cap: int = 0,
    record_skip: int = 0,
    stamp_csv: pathlib.Path | None = None,
    env_info: dict | None = None,
):
    cmd = [
        str(HOST_BIN),
        "-l", "40",
        "-a", "0000:99:02.0",
        "--file-prefix=mrv112",
        "--",
        "--reply", "ring",
        "--window", str(window),
        "--rounds", str(rounds),
        "--warmup", str(warmup),
        "--pmu",
        "--expect-stride", str(req_stride),
    ]
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
        lines = [l for l in p.stdout.splitlines() if l.startswith("MRV112 ")]
        if not lines:
            raise RuntimeError(f"No MRV112 line for {exp}/{cond}: {p.stdout[-400:]}")
        rec = json.loads(lines[-1][7:])
        hp, cp = rec["host_pmu"], rec["card_pmu"]
        if rec["lost"] != 0 or rec["bad"] != 0 or rec["torn"] != 0:
            raise RuntimeError(f"Integrity error {exp}/{cond}: lost={rec['lost']} bad={rec['bad']} torn={rec['torn']}")
        if any(c["corrupt"] != 0 for c in cp["cores"]):
            raise RuntimeError(f"Card corrupt payload in {exp}/{cond}: {cp['cores']}")
        if hp["min_pct"] < 99.9 or cp["min_pct"] < 99.9:
            raise RuntimeError(f"PMU mux in {exp}/{cond}: host={hp['min_pct']} card={cp['min_pct']}")
        if hp["ghz"] < MIN_CPU_GHZ:
            print(f"  [retry {attempt}] {exp}/{cond} rep={rep}: cpu40={hp['ghz']:.2f} GHz < {MIN_CPU_GHZ}, waiting...", flush=True)
            time.sleep(3.0)
            continue
        break
    else:
        raise RuntimeError(f"{exp}/{cond} rep={rep}: cpu40 stayed below {MIN_CPU_GHZ} GHz")

    rec["exp"] = exp
    rec["cond"] = cond
    rec["rep"] = rep
    rec["card_cores_label"] = card_cores_label
    rec["pad_next_128"] = pad_next_128
    rec["bar4_pa"] = card_info["bar4_pa"] if card_info else "unknown"
    rec["env"] = env_info
    rec["timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if stamp_csv is not None and stamp_csv.exists():
        rec["stamp_csv"] = str(stamp_csv.relative_to(ROOT))
        rec["stamp_summary"] = summarize_stamps(stamp_csv, float(cp["tsc_hz"]))

    tot = rec["rounds"] + rec["warmup"]
    tad_inf = cp["tad"]["tad_req_occ"]["val"] / (cp["wall_s"] * 2.0e9)
    snp = cp["tad"]["tad_snp_msh_out_any"]["val"] / tot
    l2 = sum(c["l2d_cache_refill"] for c in cp["cores"]) / tot
    llrd = sum(c["ll_cache_rd"] for c in cp["cores"]) / tot
    bat = sum(c["taken"] for c in cp["cores"]) / max(1, sum(c["busy_polls"] for c in cp["cores"]))
    st_str = ""
    if "stamp_summary" in rec:
        ss = rec["stamp_summary"]
        st_str = f" | h2c={ss['h2c_p50_ns']/1000:.2f}us dcin={ss['dcin_sorted_p50_ns']:.0f}ns(<50:{ss['dcin_sorted_lt50_pct']:.0f}%)"
    print(
        f"[{exp:14s}] {cond:16s} r{rep} (s={rec['req_stride']:4d},c={rec['ring_cells']:5d}) | "
        f"{rec['mops']:5.2f} M/s ({rec['ns_per_msg']:5.1f}ns) p50={rec['rtt_p50_us']:5.2f} p99={rec['rtt_p99_us']:5.2f} | "
        f"ghz={hp['ghz']:.2f} CHA={hp['cha_inflight']:4.2f} TAD={tad_inf:4.2f} snp={snp:4.2f} L2={l2:4.2f} LLrd={llrd:4.2f} bat={bat:4.1f}{st_str}",
        flush=True,
    )
    return rec


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STAMP_DIR.mkdir(parents=True, exist_ok=True)
    env_info = check_host_env()
    edac_before = read_card_edac()
    print(f"Host/Card env OK: {env_info}", flush=True)
    print(f"Card EDAC before: {edac_before}", flush=True)

    run_id = 0
    with RUNS_JSONL.open("w") as out_f:
        def save(rec):
            nonlocal run_id
            run_id += 1
            rec["id"] = run_id
            out_f.write(json.dumps(rec) + "\n")
            out_f.flush()

        # ------------------------------------------------------------------
        # 0. Idle card PMU baseline (WC2 and WC4, 3 reps x 1.0s each)
        # ------------------------------------------------------------------
        print("\n=== Phase 0: Idle card PMU baseline (WC2 & WC4) ===", flush=True)
        start_card("3-4", req_stride=64)
        for rep in range(3):
            save(measure_card_idle("idle_1s_wc2_s64", card_cores=2, rep=rep, secs=1.0))
        start_card("3-6", req_stride=64)
        for rep in range(3):
            save(measure_card_idle("idle_1s_wc4_s64", card_cores=4, rep=rep, secs=1.0))

        # ------------------------------------------------------------------
        # 1. Core single-variable sweep: WC2 W=64, T_pause=0, full ~4 MiB BAR4 slot
        #    stride in {64, 128, 4096} + 4224 (anti-aliasing >4 KiB control), 5 interleaved reps
        #    Plus W=1 baseline wire-RTT sanity check on rep 0.
        # ------------------------------------------------------------------
        print("\n=== Phase 1: Core single-variable WC2 W=64 stride sweep (5 interleaved reps) ===", flush=True)
        core_orders = [
            [64, 128, 4096, 4224],
            [4224, 4096, 128, 64],
            [128, 4096, 64, 4224],
            [4096, 64, 4224, 128],
            [64, 4224, 128, 4096],
        ]
        for rep, order in enumerate(core_orders):
            print(f"\n--- Core WC2 W=64 Rep {rep} order={order} ---", flush=True)
            for s in order:
                ci = start_card("3-4", req_stride=s)
                if rep == 0:
                    save(run_pingpong(
                        "ctrl_w1", f"wc2_w1_s{s}", 0,
                        window=1, req_stride=s, rounds=1_000_000, warmup=10_000,
                        card_cores_label=2, card_info=ci, env_info=env_info,
                    ))
                save(run_pingpong(
                    "core_wc2_w64", f"wc2_w64_s{s}", rep,
                    window=64, req_stride=s, rounds=12_000_000, warmup=20_000,
                    card_cores_label=2, card_info=ci, env_info=env_info,
                ))

        # ------------------------------------------------------------------
        # 2. Matched-capacity control: WC2 W=64 with ring_cells=1020 fixed
        #    across 64 / 128 / 4096 B (and 992 for 4224 B), 3 interleaved reps.
        # ------------------------------------------------------------------
        print("\n=== Phase 2: Matched ring_cells control (WC2 W=64, c=1020/992, 3 reps) ===", flush=True)
        matched_specs = [(64, 1020), (128, 1020), (4096, 1020), (4224, 992)]
        for rep in range(3):
            order = matched_specs if rep % 2 == 0 else list(reversed(matched_specs))
            for s, c in order:
                ci = start_card("3-4", req_stride=s, req_ring_cells=c)
                save(run_pingpong(
                    "ctrl_matched_c", f"wc2_w64_s{s}_c{c}", rep,
                    window=64, req_stride=s, rounds=10_000_000, warmup=20_000,
                    card_cores_label=2, card_info=ci, env_info=env_info,
                ))

        # ------------------------------------------------------------------
        # 3. Deeper-window & 4-core locking controls + pad_next_128 control (3 interleaved reps each)
        # ------------------------------------------------------------------
        print("\n=== Phase 3A: WC2 W=128 control (full ring, 3 reps) ===", flush=True)
        for rep in range(3):
            order = [64, 128, 4096, 4224] if rep % 2 == 0 else [4224, 4096, 128, 64]
            for s in order:
                ci = start_card("3-4", req_stride=s)
                save(run_pingpong(
                    "ctrl_wc2_w128", f"wc2_w128_s{s}", rep,
                    window=128, req_stride=s, rounds=10_000_000, warmup=20_000,
                    card_cores_label=2, card_info=ci, env_info=env_info,
                ))

        print("\n=== Phase 3B: WC2 W=64 --pad-next-128 control (full ring, 3 reps) ===", flush=True)
        for rep in range(3):
            order = [64, 128, 4096, 4224] if rep % 2 == 0 else [4224, 4096, 128, 64]
            for s in order:
                ci = start_card("3-4", req_stride=s, pad_next_128=True)
                save(run_pingpong(
                    "ctrl_padnext", f"wc2_w64_pn_s{s}", rep,
                    window=64, req_stride=s, rounds=10_000_000, warmup=20_000,
                    card_cores_label=2, pad_next_128=True, card_info=ci, env_info=env_info,
                ))

        print("\n=== Phase 3C: WC4 W=64 control (4 card cores, full ring, 3 reps) ===", flush=True)
        for rep in range(3):
            order = [64, 128, 4096, 4224] if rep % 2 == 0 else [4224, 4096, 128, 64]
            for s in order:
                ci = start_card("3-6", req_stride=s)
                save(run_pingpong(
                    "ctrl_wc4_w64", f"wc4_w64_s{s}", rep,
                    window=64, req_stride=s, rounds=12_000_000, warmup=20_000,
                    card_cores_label=4, card_info=ci, env_info=env_info,
                ))

        # ------------------------------------------------------------------
        # 4. Sparse clock-synced stamps (h2c / c2h / cross-core d_card_t_in)
        #    on WC2 W=64 and WC4 W=64 across all 4 strides
        # ------------------------------------------------------------------
        print("\n=== Phase 4: Sparse clock-synced timestamps (WC2 W=64 & WC4 W=64) ===", flush=True)
        for label, lcores, ncores in [("wc2", "3-4", 2), ("wc4", "3-6", 4)]:
            for s in [64, 128, 4096, 4224]:
                ci = start_card(lcores, req_stride=s, stamp=True, sparse=True)
                csv_p = STAMP_DIR / f"sparse_{label}_w64_s{s}.csv"
                save(run_pingpong(
                    f"stamp_{label}", f"{label}_w64_s{s}_sp", 0,
                    window=64, req_stride=s, rounds=6_000_000, warmup=20_000,
                    card_cores_label=ncores, card_info=ci,
                    stamp=True, stamp_sparse=True, record_cap=4000, record_skip=1_000_000,
                    stamp_csv=csv_p, env_info=env_info,
                ))

    edac_after = read_card_edac()
    print(f"\nCard EDAC before: {edac_before}", flush=True)
    print(f"Card EDAC after:  {edac_after}", flush=True)
    if edac_after != edac_before:
        raise RuntimeError(f"EDAC delta non-zero! before={edac_before} after={edac_after}")
    print(f"ALL {run_id} RUNS COMPLETED CLEANLY -> {RUNS_JSONL}", flush=True)


if __name__ == "__main__":
    main()
