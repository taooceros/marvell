#!/usr/bin/env python3
"""MRV-123 experiment harness:
After restoring RingReplies::poll MAX_DRAIN=32 (single RDTSCP after drain) and fixing
open-loop steady-state achieved_mps, test whether pure-host credit hysteresis batch release
B in {1, 8, 16} (fixed W_credit=32, card T_pause=0) preserves ~0.9 us light-load RTT and
brings 20 M/s end-to-end tot p99 back to ~2 us.

Fixed fastest direct path baseline (MRV-82, MRV-49):
  - Host cpu40 (performance, 3.90 GHz), Socket 1 uncore 2.20 GHz
  - MOVDIR64B -> BAR4 slot 15 (batch=1, no fence), 64 B cell (64 B stride)
  - Card CN106 (userspace 2.00 GHz), pem_wc write-combining core stores -> Host DDIO
  - Card T_pause = 0 (zero card-side modification)
  - Host RingReplies MAX_DRAIN = 32 (single RDTSCP after batch drain)
  - Direct Host Core+Uncore PMU & Card TAD+ARMv8 PMU (100.0% enabled, zero multiplexing)
"""
import json
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv123")
LOG_DIR = ROOT / "logs/mrv123-2026-11-22"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
HOST_BIN = ROOT / "testbed/target/release/inline_pingpong"
CARD_BIN = "/root/bench/inline_reflector.mrv123"

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
        "/sys/devices/system/edac/ea/ea0/ue_count; "
        "dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || true"
    )
    out = subprocess.check_output(["ssh", "root@10.10.0.2", cmd], text=True)
    vals = [int(x) for x in out.split()]
    return {
        "mc0_ce": vals[0],
        "mc0_ue": vals[1],
        "ea0_ce": vals[2],
        "ea0_ue": vals[3],
        "sei_uer": vals[4],
    }


def stop_card():
    subprocess.run(
        ["ssh", "root@10.10.0.2", "pkill -INT -f '[/]root/bench/inline_reflector.mrv123' || true"],
        check=False,
    )
    for _ in range(40):
        time.sleep(0.2)
        p = subprocess.run(
            ["ssh", "root@10.10.0.2", "pgrep -f '[/]root/bench/inline_reflector.mrv123' || true"],
            capture_output=True,
            text=True,
        )
        if not p.stdout.strip():
            return
    raise RuntimeError("card inline_reflector.mrv123 did not exit cleanly after SIGINT")


def start_card(lcores: str, expected_cores: int, settle_s: float = 1.5):
    stop_card()
    cmd = (
        "lsmod | grep -q pem_wc || insmod /root/bench/pem_wc.ko; "
        f"nohup {CARD_BIN} -l {lcores} -a 0002:01:00.2 --file-prefix=mrv123 "
        "-- --store --store-wc --sync 10.10.0.2:7527 > /tmp/mrv123_card.log 2>&1 < /dev/null &"
    )
    subprocess.check_call(["ssh", "root@10.10.0.2", cmd])
    for _ in range(40):
        time.sleep(0.25)
        try:
            with socket.create_connection(("10.10.0.2", 7527), timeout=1.0) as s:
                s.sendall(b"SET_PAUSE 0\n")
                resp = s.recv(128).decode().strip()
                if resp.startswith("OK"):
                    time.sleep(settle_s)
                    # Verify process line matches expected lcores
                    ps = subprocess.check_output(
                        ["ssh", "root@10.10.0.2", "pgrep -af '[/]root/bench/inline_reflector.mrv123'"],
                        text=True,
                    )
                    assert f"-l {lcores} " in ps, f"unexpected card lcores: {ps.strip()}"
                    return
        except OSError:
            pass
    raise RuntimeError(f"card reflector ({lcores}, expected {expected_cores} cores) failed to start")


def rounds_for_rate(rate_mps: float) -> tuple[int, int]:
    if rate_mps <= 1.5:
        return 300_000, 20_000
    if rate_mps <= 6.0:
        return 1_000_000, 100_000
    if rate_mps <= 14.0:
        return 2_000_000, 200_000
    return 2_500_000, 200_000


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
            "--file-prefix=mrv123",
            "--",
            "--reply",
            "ring",
            "--pmu",
        ] + extra_args
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(
                f"run_id={run_id} {cfg} failed rc={proc.returncode}:\n{proc.stderr}\n{proc.stdout}"
            )
        payload = None
        for line in proc.stdout.splitlines():
            if line.startswith("MRV123 "):
                payload = json.loads(line[7:])
                break
        if payload is None:
            raise RuntimeError(f"run_id={run_id} {cfg} missing MRV123 JSON")
        hp = payload["host_pmu"]
        cp = payload["card_pmu"]
        ghz = hp["ghz"]
        corrupt = sum(c["corrupt"] for c in cp["cores"])
        assert payload["cores"] == card_cores, (
            f"core mismatch: expected {card_cores}, got {payload['cores']}"
        )
        assert cp["empty_pause_ns"] == 0, f"expected T_pause=0, got {cp['empty_pause_ns']}"
        if (
            payload["lost"] == 0
            and payload["bad"] == 0
            and payload["torn"] == 0
            and corrupt == 0
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
                "card_pause_ns": 0,
                "rep": rep,
                "attempt": attempt,
                **meta,
                **payload,
            }
            return rec
        print(
            f"  [retry {attempt+1}] {cfg} rep={rep}: ghz={ghz:.3f} "
            f"h_pct={hp['min_pct']:.1f} c_pct={cp['min_pct']:.1f} "
            f"lost/bad/torn/corrupt={payload['lost']}/{payload['bad']}/{payload['torn']}/{corrupt}",
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
        f"[{rec['run_id']:3d}] {rec['part']:18s} {rec['cfg']:28s} r{rec['rep']} | "
        f"ach={ach:5.2f} M/s | tot p50={t['p50']:7.2f} p99={t['p99']:8.2f} | "
        f"wire p50={w['p50']:4.2f} p99={w['p99']:5.2f} | "
        f"trips={rec['credit_trips']:6d} pause={rec['mean_pause_us']:4.2f}us | "
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
        # PHASE A: WC4 (4 card cores: -l 3-6, T_pause = 0)
        # =====================================================================
        print("\n=== Starting WC4 (-l 3-6, T_pause=0) ===", flush=True)
        start_card("3-6", expected_cores=4)

        # Part 1A: WC4 closed-loop de-confounding (MAX_DRAIN=32 vs MRV-115 MAX_DRAIN=8)
        wc4_cl_windows = [1, 24, 32, 40, 48, 64]
        for rep in range(REPS):
            print(f"\n--- Part 1A WC4 Closed-Loop Rep {rep} ---", flush=True)
            for w in wc4_cl_windows:
                rounds, warmup = rounds_for_window(w)
                for label, extra in [
                    ("unshaped", []),
                    ("burst_8", ["--max-send-burst", "8"]),
                ]:
                    run_id += 1
                    rec = run_one(
                        run_id=run_id,
                        part="p1_cl_wc4",
                        cfg=f"wc4_cl_w{w}_{label}",
                        card_cores=4,
                        rep=rep,
                        extra_args=["-w", str(w), "-n", str(rounds), "--warmup", str(warmup)] + extra,
                        meta={"shaper": label},
                    )
                    append_rec(f, rec)
            # Also closed-loop W=64 with W_credit=32, B in {1, 8, 16}
            rounds, warmup = rounds_for_window(64)
            for b in [1, 8, 16]:
                run_id += 1
                rec = run_one(
                    run_id=run_id,
                    part="p1_cl_wc4",
                    cfg=f"wc4_cl_w64_c32_b{b}",
                    card_cores=4,
                    rep=rep,
                    extra_args=[
                        "-w", "64",
                        "-n", str(rounds),
                        "--warmup", str(warmup),
                        "--credit-cap", "32",
                        "--release-batch", str(b),
                    ],
                    meta={"shaper": f"c32_b{b}"},
                )
                append_rec(f, rec)

        # Part 2A: WC4 Open-Loop Poisson 1 -> 20 M/s (unshaped vs W_credit=32, B in {1, 8, 16})
        poisson_rates = [1.0, 5.0, 10.0, 13.5, 16.0, 20.0]
        p2_cfgs = [
            ("unshaped", 4032, 1),
            ("c32_b1", 32, 1),
            ("c32_b8", 32, 8),
            ("c32_b16", 32, 16),
        ]
        for rep in range(REPS):
            seed = 123 + rep * 100
            print(f"\n--- Part 2A WC4 Poisson Rep {rep} (seed={seed}) ---", flush=True)
            for rate in poisson_rates:
                rounds, warmup = rounds_for_rate(rate)
                rtag = f"{rate:g}m"
                for label, cap, b in p2_cfgs:
                    run_id += 1
                    rec = run_one(
                        run_id=run_id,
                        part="p2_wc4_poisson",
                        cfg=f"wc4_p_{rtag}_{label}",
                        card_cores=4,
                        rep=rep,
                        extra_args=[
                            "--open-mps", f"{rate}",
                            "--arrival", "poisson",
                            "--seed", str(seed),
                            "--credit-cap", str(cap),
                            "--release-batch", str(b),
                            "-n", str(rounds),
                            "--warmup", str(warmup),
                        ],
                        meta={"shaper": label},
                    )
                    append_rec(f, rec)

        # Part 3A: WC4 20 M/s Poisson controls (C=32 B=32 extreme drain; C=40/48 B in {1, 8, 16})
        wc4_p3_cfgs = [
            ("c32_b32", 32, 32),
            ("c40_b1", 40, 1),
            ("c40_b8", 40, 8),
            ("c40_b16", 40, 16),
            ("c48_b1", 48, 1),
            ("c48_b8", 48, 8),
            ("c48_b16", 48, 16),
        ]
        for rep in range(REPS):
            seed = 123 + rep * 100
            print(f"\n--- Part 3A WC4 20 M/s Control Rep {rep} (seed={seed}) ---", flush=True)
            rounds, warmup = rounds_for_rate(20.0)
            for label, cap, b in wc4_p3_cfgs:
                run_id += 1
                rec = run_one(
                    run_id=run_id,
                    part="p3_wc4_ctrl20",
                    cfg=f"wc4_p_20m_{label}",
                    card_cores=4,
                    rep=rep,
                    extra_args=[
                        "--open-mps", "20.0",
                        "--arrival", "poisson",
                        "--seed", str(seed),
                        "--credit-cap", str(cap),
                        "--release-batch", str(b),
                        "-n", str(rounds),
                        "--warmup", str(warmup),
                    ],
                    meta={"shaper": label},
                )
                append_rec(f, rec)

        # =====================================================================
        # PHASE B: WC1 (1 card core: -l 3, T_pause = 0)
        # =====================================================================
        print("\n=== Starting WC1 (-l 3, T_pause=0) ===", flush=True)
        start_card("3", expected_cores=1)

        # Part 1B: WC1 closed-loop baseline (MAX_DRAIN=32 stop-and-wait diagnostic at W=32)
        wc1_cl_windows = [1, 16, 24, 32, 48, 64]
        for rep in range(REPS):
            print(f"\n--- Part 1B WC1 Closed-Loop Rep {rep} ---", flush=True)
            for w in wc1_cl_windows:
                rounds, warmup = rounds_for_window(w)
                run_id += 1
                rec = run_one(
                    run_id=run_id,
                    part="p1_cl_wc1",
                    cfg=f"wc1_cl_w{w}_unshaped",
                    card_cores=1,
                    rep=rep,
                    extra_args=["-w", str(w), "-n", str(rounds), "--warmup", str(warmup)],
                    meta={"shaper": "unshaped"},
                )
                append_rec(f, rec)

        # Part 2B: WC1 Open-Loop Poisson 1 -> 20 M/s (unshaped vs W_credit=32, B in {1, 8, 16})
        for rep in range(REPS):
            seed = 123 + rep * 100
            print(f"\n--- Part 2B WC1 Poisson Rep {rep} (seed={seed}) ---", flush=True)
            for rate in poisson_rates:
                rounds, warmup = rounds_for_rate(rate)
                rtag = f"{rate:g}m"
                for label, cap, b in p2_cfgs:
                    run_id += 1
                    rec = run_one(
                        run_id=run_id,
                        part="p2_wc1_poisson",
                        cfg=f"wc1_p_{rtag}_{label}",
                        card_cores=1,
                        rep=rep,
                        extra_args=[
                            "--open-mps", f"{rate}",
                            "--arrival", "poisson",
                            "--seed", str(seed),
                            "--credit-cap", str(cap),
                            "--release-batch", str(b),
                            "-n", str(rounds),
                            "--warmup", str(warmup),
                        ],
                        meta={"shaper": label},
                    )
                    append_rec(f, rec)

        # Part 3B: WC1 20 M/s Poisson controls (C=32 B=32; C=48/64 > MAX_DRAIN=32 with B in {1, 8, 16, 32})
        wc1_p3_cfgs = [
            ("c32_b32", 32, 32),
            ("c48_b1", 48, 1),
            ("c48_b8", 48, 8),
            ("c48_b16", 48, 16),
            ("c48_b32", 48, 32),
            ("c64_b1", 64, 1),
            ("c64_b8", 64, 8),
            ("c64_b16", 64, 16),
            ("c64_b32", 64, 32),
        ]
        for rep in range(REPS):
            seed = 123 + rep * 100
            print(f"\n--- Part 3B WC1 20 M/s Control Rep {rep} (seed={seed}) ---", flush=True)
            rounds, warmup = rounds_for_rate(20.0)
            for label, cap, b in wc1_p3_cfgs:
                run_id += 1
                rec = run_one(
                    run_id=run_id,
                    part="p3_wc1_ctrl20",
                    cfg=f"wc1_p_20m_{label}",
                    card_cores=1,
                    rep=rep,
                    extra_args=[
                        "--open-mps", "20.0",
                        "--arrival", "poisson",
                        "--seed", str(seed),
                        "--credit-cap", str(cap),
                        "--release-batch", str(b),
                        "-n", str(rounds),
                        "--warmup", str(warmup),
                    ],
                    meta={"shaper": label},
                )
                append_rec(f, rec)

    stop_card()
    edac_after = read_card_edac()
    print(f"\nALL {run_id} RUNS COMPLETED CLEANLY -> {RUNS_JSONL}", flush=True)
    print(f"EDAC BEFORE: {edac_before}", flush=True)
    print(f"EDAC AFTER:  {edac_after}", flush=True)


if __name__ == "__main__":
    main()
