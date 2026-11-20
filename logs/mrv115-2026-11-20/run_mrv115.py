#!/usr/bin/env python3
"""MRV-115 experiment harness: Host in-flight burst/credit shaping for open-loop
(open_echo) tail-latency control and closed-loop monotonicity (WC1 & WC4).

Fixed fastest direct path baseline (MRV-82):
  - Host cpu40 (performance, 3.90 GHz), Socket 1 uncore 2.20 GHz
  - MOVDIR64B -> BAR4 slot 15 (batch = 1, no fence), 64 B cell
  - Card CN106 (2.00 GHz), pem_wc write-combining core stores -> Host DDIO
  - Real 4 KiB non-zero pre-touch on all latency arrays
  - Direct Host Core+Uncore PMU & Card TAD+ARMv8 PMU (100.0% enabled)
"""
import json
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv115")
LOG_DIR = ROOT / "logs/mrv115-2026-11-20"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
HOST_BIN = ROOT / "testbed/target/release/inline_pingpong"
CARD_BIN = "/root/bench/inline_reflector.mrv115"

REPS = 3
MIN_CPU_GHZ = 3.85


def wait_for_turbo(min_khz: int = 3_850_000, max_wait_s: float = 60.0):
    t0 = time.monotonic()
    p = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq")
    while time.monotonic() - t0 < max_wait_s:
        khz = int(p.read_text().strip())
        if khz >= min_khz:
            return khz
        time.sleep(0.5)
    return int(p.read_text().strip())


def check_host_env():
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    cur = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = int(
        pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency/package_01_die_01/current_freq_khz")
        .read_text()
        .strip()
    )
    paranoid = int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    assert gov == "performance", f"cpu40 governor is {gov}"
    assert uncore >= 2_190_000, f"uncore is {uncore} kHz"
    assert paranoid <= 0, f"perf_event_paranoid is {paranoid}"
    return {
        "cpu40_governor": gov,
        "cpu40_khz": cur,
        "uncore_khz": uncore,
        "perf_event_paranoid": paranoid,
    }


def read_card_edac():
    cmd = (
        "cat /sys/devices/system/edac/mc/mc0/ce_count "
        "/sys/devices/system/edac/mc/mc0/ue_count "
        "/sys/devices/system/edac/ea/ea0/ce_count "
        "/sys/devices/system/edac/ea/ea0/ue_count"
    )
    out = subprocess.check_output(["ssh", "root@10.10.0.2", cmd], text=True)
    vals = [int(x) for x in out.split()]
    return {"mc0_ce": vals[0], "mc0_ue": vals[1], "ea0_ce": vals[2], "ea0_ue": vals[3]}


def start_card(lcores: str, settle_s: float = 1.5):
    subprocess.run(
        ["ssh", "root@10.10.0.2", "pkill -f '[/]root/bench/inline_reflector.mrv115' 2>/dev/null || true"],
        check=False,
    )
    time.sleep(0.8)
    cmd = (
        "lsmod | grep -q pem_wc || insmod /root/bench/pem_wc.ko; "
        f"nohup {CARD_BIN} -l {lcores} -a 0002:01:00.2 --file-prefix=mrv115 "
        "-- --store --store-wc --sync 10.10.0.2:7527 > /tmp/mrv115_card.log 2>&1 < /dev/null &"
    )
    subprocess.check_call(["ssh", "root@10.10.0.2", cmd])
    for _ in range(30):
        time.sleep(0.25)
        try:
            with socket.create_connection(("10.10.0.2", 7527), timeout=1.0) as s:
                s.sendall(b"SET_PAUSE 0\n")
                resp = s.recv(128).decode().strip()
                if resp.startswith("OK"):
                    time.sleep(settle_s)
                    return
        except OSError:
            pass
    raise RuntimeError(f"card reflector ({lcores}) failed to come up on port 7527")


def set_card_pause(ns: int):
    with socket.create_connection(("10.10.0.2", 7527), timeout=5.0) as s:
        s.sendall(f"SET_PAUSE {ns}\n".encode())
        resp = s.recv(128).decode().strip()
        assert resp.startswith("OK"), f"SET_PAUSE {ns} failed: {resp}"
        return resp


def rounds_for_rate(rate_mps: float) -> tuple[int, int]:
    if rate_mps <= 1.5:
        return 300_000, 20_000
    if rate_mps <= 6.0:
        return 1_000_000, 50_000
    if rate_mps <= 14.0:
        return 2_000_000, 50_000
    return 2_500_000, 100_000


def rounds_for_window(w: int) -> tuple[int, int]:
    if w == 1:
        return 300_000, 20_000
    if w <= 16:
        return 1_500_000, 50_000
    return 2_000_000, 50_000


def run_one(
    run_id: int,
    part: str,
    cfg: str,
    card_cores: int,
    card_pause_ns: int,
    rep: int,
    extra_args: list[str],
    meta: dict,
):
    for attempt in range(4):
        wait_for_turbo()
        cmd = [
            str(HOST_BIN),
            "-l",
            "40",
            "-a",
            "0000:99:02.0",
            "--file-prefix=mrv115",
            "--",
            "--reply",
            "ring",
            "--pmu",
        ] + extra_args
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(f"run_id={run_id} {cfg} failed rc={proc.returncode}:\n{proc.stderr}\n{proc.stdout}")
        payload = None
        for line in proc.stdout.splitlines():
            if line.startswith("MRV115 "):
                payload = json.loads(line[7:])
                break
        if payload is None:
            raise RuntimeError(f"run_id={run_id} {cfg} missing MRV115 JSON")
        hp = payload["host_pmu"]
        cp = payload["card_pmu"]
        ghz = hp["ghz"]
        if (
            payload["lost"] == 0
            and payload["bad"] == 0
            and payload["torn"] == 0
            and hp["min_pct"] >= 99.99
            and cp["min_pct"] >= 99.99
            and ghz >= MIN_CPU_GHZ
        ):
            rec = {
                "run_id": run_id,
                "ts": round(t0, 3),
                "part": part,
                "cfg": cfg,
                "card_cores": card_cores,
                "card_pause_ns": card_pause_ns,
                "rep": rep,
                "attempt": attempt,
                **meta,
                **payload,
            }
            return rec
        print(
            f"  [retry {attempt+1}] {cfg} rep={rep}: ghz={ghz:.3f} "
            f"h_pct={hp['min_pct']:.1f} c_pct={cp['min_pct']:.1f} "
            f"lost/bad/torn={payload['lost']}/{payload['bad']}/{payload['torn']}",
            flush=True,
        )
        time.sleep(1.5)
    raise RuntimeError(f"run_id={run_id} {cfg} failed quality checks after retries")


def append_rec(f, rec: dict):
    f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    f.flush()
    t = rec["tot_us"]
    w = rec["wire_us"]
    hp = rec["host_pmu"]
    cp = rec["card_pmu"]
    snp = cp["tad"]["tad_snp_msh_out_any"]["val"] / max(1, rec["measured"])
    occ = cp["tad"]["tad_req_occ"]["val"] / (cp["wall_s"] * 2e9)
    ach = rec.get("achieved_mps", rec.get("mops", 0.0))
    print(
        f"[{rec['run_id']:3d}] {rec['part']:16s} {rec['cfg']:28s} r{rec['rep']} | "
        f"ach={ach:5.2f} M/s | tot p50={t['p50']:6.2f} p95={t['p95']:6.2f} p99={t['p99']:7.2f} | "
        f"wire p50={w['p50']:5.2f} p99={w['p99']:6.2f} | "
        f"ghz={hp['ghz']:.2f} cha={hp['cha_inflight']:5.2f} tad={occ:4.2f} snp={snp:4.2f}",
        flush=True,
    )


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = check_host_env()
    edac_before = read_card_edac()
    print(f"ENV: {env}", flush=True)
    print(f"EDAC BEFORE: {edac_before}", flush=True)

    run_id = 0
    with RUNS_JSONL.open("w", encoding="utf-8") as f:
        # =====================================================================
        # PHASE A: WC1 (1 card core: -l 3)
        # =====================================================================
        print("\n=== Starting WC1 (-l 3) ===", flush=True)
        start_card("3")
        set_card_pause(0)

        wc1_paced_rates = [1.0, 5.0, 10.0, 13.5, 16.0, 20.0, 25.0, 30.0]
        wc1_paced_cfgs = [
            ("unshaped", 4032, 0),
            ("credit_24", 24, 0),
            ("credit_32", 32, 0),
            ("credit_48", 48, 0),
            ("burst8_c32", 32, 8),
        ]
        wc1_pois_rates = [10.0, 15.0, 20.0]
        wc1_pois_cfgs = [
            ("unshaped", 4032, 0),
            ("credit_32", 32, 0),
            ("credit_48", 48, 0),
            ("burst8_c32", 32, 8),
        ]
        wc1_windows = [1, 8, 16, 24, 32, 40, 48, 64, 128, 256]

        for rep in range(REPS):
            print(f"\n--- WC1 Interleaved Rep {rep} ---", flush=True)
            set_card_pause(0)
            # Part 1A: WC1 Open-Loop Paced
            for rate in wc1_paced_rates:
                rounds, warmup = rounds_for_rate(rate)
                for cname, ccap, burst in wc1_paced_cfgs:
                    run_id += 1
                    rec = run_one(
                        run_id,
                        "p1_wc1_paced",
                        f"wc1_p_{rate:g}m_{cname}",
                        1,
                        0,
                        rep,
                        [
                            "--open-mps",
                            str(rate),
                            "--arrival",
                            "paced",
                            "--credit-cap",
                            str(ccap),
                            "--max-send-burst",
                            str(burst),
                            "--rounds",
                            str(rounds),
                            "--warmup",
                            str(warmup),
                        ],
                        {"shaping": cname, "target_mps": rate},
                    )
                    append_rec(f, rec)

            # Part 1A: WC1 Open-Loop Poisson (distinct deterministic seed per rep)
            seed = 115 + rep * 100
            for rate in wc1_pois_rates:
                rounds, warmup = rounds_for_rate(rate)
                for cname, ccap, burst in wc1_pois_cfgs:
                    run_id += 1
                    rec = run_one(
                        run_id,
                        "p1_wc1_poisson",
                        f"wc1_e_{rate:g}m_{cname}",
                        1,
                        0,
                        rep,
                        [
                            "--open-mps",
                            str(rate),
                            "--arrival",
                            "poisson",
                            "--seed",
                            str(seed),
                            "--credit-cap",
                            str(ccap),
                            "--max-send-burst",
                            str(burst),
                            "--rounds",
                            str(rounds),
                            "--warmup",
                            str(warmup),
                        ],
                        {"shaping": cname, "target_mps": rate},
                    )
                    append_rec(f, rec)

            # Part 2 reference: WC1 Closed-Loop monotonicity
            for w in wc1_windows:
                rounds, warmup = rounds_for_window(w)
                run_id += 1
                rec = run_one(
                    run_id,
                    "p2_cl_wc1",
                    f"cl_wc1_w{w}_unshaped",
                    1,
                    0,
                    rep,
                    ["--window", str(w), "--rounds", str(rounds), "--warmup", str(warmup)],
                    {"shaping": "unshaped"},
                )
                append_rec(f, rec)

            # Part 1A mechanism control: WC1 with card T_pause = 1000 ns at 20 M/s (paced & poisson)
            set_card_pause(1000)
            rounds, warmup = rounds_for_rate(20.0)
            for arr in ["paced", "poisson"]:
                for cname, ccap in [("unshaped", 4032), ("credit_48", 48)]:
                    run_id += 1
                    extra = [
                        "--open-mps",
                        "20",
                        "--arrival",
                        arr,
                        "--credit-cap",
                        str(ccap),
                        "--rounds",
                        str(rounds),
                        "--warmup",
                        str(warmup),
                    ]
                    if arr == "poisson":
                        extra += ["--seed", str(seed)]
                    rec = run_one(
                        run_id,
                        "p1_wc1_pause1000",
                        f"wc1_{arr[0]}_20m_{cname}_p1000",
                        1,
                        1000,
                        rep,
                        extra,
                        {"shaping": f"{cname}_p1000", "target_mps": 20.0},
                    )
                    append_rec(f, rec)
            set_card_pause(0)

        # =====================================================================
        # PHASE B: WC4 (4 card cores: -l 3-6)
        # =====================================================================
        print("\n=== Starting WC4 (-l 3-6) ===", flush=True)
        start_card("3-6")
        set_card_pause(0)

        wc4_paced_rates = [1.0, 5.0, 10.0, 13.5, 16.0, 20.0, 25.0]
        wc4_paced_cfgs = [
            ("unshaped", 4032, 0),
            ("burst8_c24", 24, 8),
            ("burst8_c32", 32, 8),
        ]
        wc4_pois_rates = [10.0, 13.5, 16.0, 20.0]
        wc4_pois_cfgs = [
            ("unshaped", 4032, 0),
            ("burst8_c32", 32, 8),
        ]
        wc4_p1800_rates = [10.0, 13.5, 16.0, 20.0, 25.0]
        wc4_windows = [1, 8, 16, 24, 32, 40, 48, 64, 128, 256]
        wc4_cl_cfgs = [
            ("unshaped", 0, 0),
            ("burst_8", 0, 8),
            ("burst8_c32", 32, 8),
        ]

        for rep in range(REPS):
            print(f"\n--- WC4 Interleaved Rep {rep} ---", flush=True)
            set_card_pause(0)
            # Part 1B: WC4 Open-Loop Paced
            for rate in wc4_paced_rates:
                rounds, warmup = rounds_for_rate(rate)
                for cname, ccap, burst in wc4_paced_cfgs:
                    run_id += 1
                    rec = run_one(
                        run_id,
                        "p1_wc4_paced",
                        f"wc4_p_{rate:g}m_{cname}",
                        4,
                        0,
                        rep,
                        [
                            "--open-mps",
                            str(rate),
                            "--arrival",
                            "paced",
                            "--credit-cap",
                            str(ccap),
                            "--max-send-burst",
                            str(burst),
                            "--rounds",
                            str(rounds),
                            "--warmup",
                            str(warmup),
                        ],
                        {"shaping": cname, "target_mps": rate},
                    )
                    append_rec(f, rec)

            # Part 1B: WC4 Open-Loop Poisson
            seed = 115 + rep * 100
            for rate in wc4_pois_rates:
                rounds, warmup = rounds_for_rate(rate)
                for cname, ccap, burst in wc4_pois_cfgs:
                    run_id += 1
                    rec = run_one(
                        run_id,
                        "p1_wc4_poisson",
                        f"wc4_e_{rate:g}m_{cname}",
                        4,
                        0,
                        rep,
                        [
                            "--open-mps",
                            str(rate),
                            "--arrival",
                            "poisson",
                            "--seed",
                            str(seed),
                            "--credit-cap",
                            str(ccap),
                            "--max-send-burst",
                            str(burst),
                            "--rounds",
                            str(rounds),
                            "--warmup",
                            str(warmup),
                        ],
                        {"shaping": cname, "target_mps": rate},
                    )
                    append_rec(f, rec)

            # Part 2: WC4 Closed-Loop Monotonicity
            for w in wc4_windows:
                rounds, warmup = rounds_for_window(w)
                for cname, ccap, burst in wc4_cl_cfgs:
                    run_id += 1
                    rec = run_one(
                        run_id,
                        "p2_cl_wc4",
                        f"cl_wc4_w{w}_{cname}",
                        4,
                        0,
                        rep,
                        [
                            "--window",
                            str(w),
                            "--credit-cap",
                            str(ccap),
                            "--max-send-burst",
                            str(burst),
                            "--rounds",
                            str(rounds),
                            "--warmup",
                            str(warmup),
                        ],
                        {"shaping": cname},
                    )
                    append_rec(f, rec)

            # Part 1B card-backoff reference: WC4 T_pause = 1800 ns
            set_card_pause(1800)
            for rate in wc4_p1800_rates:
                rounds, warmup = rounds_for_rate(rate)
                run_id += 1
                rec = run_one(
                    run_id,
                    "p1_wc4_pause1800",
                    f"wc4_p_{rate:g}m_p1800",
                    4,
                    1800,
                    rep,
                    [
                        "--open-mps",
                        str(rate),
                        "--arrival",
                        "paced",
                        "--credit-cap",
                        "4032",
                        "--rounds",
                        str(rounds),
                        "--warmup",
                        str(warmup),
                    ],
                    {"shaping": "p1800_unshaped", "target_mps": rate},
                )
                append_rec(f, rec)
            set_card_pause(0)

    edac_after = read_card_edac()
    print(f"\nEDAC AFTER: {edac_after}", flush=True)
    print(f"ALL {run_id} RUNS COMPLETED CLEANLY -> {RUNS_JSONL}", flush=True)


if __name__ == "__main__":
    main()
