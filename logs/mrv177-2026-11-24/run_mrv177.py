#!/usr/bin/env python3
"""MRV-177 full n=3 interleaved single-variable experiment harness.

Directly compares three write-frontier collision-avoidance families on fixed CN106 BAR4:
  - Baseline: direct polling of `cell[next]` (unshaped + fixed host credit C_line=32)
  - Arm A (B-Queue adaptive lookahead `next + D - 1`):
      * bq_probe: textbook probe-first + T_bt spin + binary-halving D/2 -> 1
      * bq_wait:  wait-first T_bt (zero BAR4 loads) + probe + exact-D bounded drain
  - Arm B (Decoupled Doorbell / dedicated 128 B-aligned `prod_idx` line at 0x3ff800):
      * db_pkt_f / db_pkt_uf: per-packet doorbell (ordered SFENCE vs unfenced control)
      * db_adp_f / db_adp_uf: light-load immediate (inflight <= 4) + busy batch-coalesced
        (B_db = 32, T_flush = 250 ns on 1C / 500 ns on 4C; ordered SFENCE vs unfenced)
      * db_adp_f_c64 (4C): ordered adaptive doorbell + generous safety credit C_line = 64

Workloads (both 1C `-l 3` and 4C `-l 3-6`, n=3 rotated-arm interleaving):
  1. WL1 Isolated small packets: Closed W = 1, 2
  2. WL2 Burst-stop tail packets:
       - burst32_resid2:    burst=32 + immediate residual 2 tail packets (gap=0, no drain)
       - burst32_pause_iso2: burst=32 -> drain -> 5 us pause -> 2 isolated tail packets
  3. WL3 Closed-loop high concurrency: W = 32, 64, 128
  4. WL4 Open-loop Poisson: 10, 13.5, 16, 20, 25 M/s

Strict gates on every run (zero silent retries; ALL attempts logged to attempts.jsonl):
  lost = bad = torn = corrupt = 0, host & card PMU min_pct >= 99.99%,
  host page_faults = 0, cpu40 >= 3.85 GHz, mesh >= 2.18 GHz.
"""

import hashlib
import json
import pathlib
import socket
import subprocess
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv177")
LOG_DIR = ROOT / "logs/mrv177-2026-11-24"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = "/tmp/mrv177-target/release/mrv177_host"
CARD_BIN = "/root/bench/mrv177_card"
EAL_ARGS = ["-l", "40", "-a", "0000:99:02.0", "--file-prefix=mrv177h", "--log-level=lib.eal:warning"]
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

REPS = 3
MIN_CPU_GHZ = 3.85
MIN_MESH_GHZ = 2.18
MAX_ATTEMPTS = 5


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def wait_for_turbo(min_khz: int = 3_850_000, max_wait_s: float = 20.0) -> int:
    p = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq")
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_wait_s:
        khz = int(p.read_text().strip())
        if khz >= min_khz:
            return khz
        time.sleep(0.05)
    return int(p.read_text().strip())


def read_host_env() -> dict:
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    cur = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz")):
        uncore[p.parent.name] = int(p.read_text().strip())
    paranoid = int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    hp_free = int(
        pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/free_hugepages").read_text().strip()
    )
    hp_total = int(
        pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/nr_hugepages").read_text().strip()
    )
    edac_ue = {}
    for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count")):
        edac_ue[p.parent.name] = int(p.read_text().strip())
    assert gov == "performance", f"cpu40 governor={gov}"
    assert uncore.get("package_01_die_01", 0) >= 2_190_000, f"uncore={uncore}"
    assert paranoid <= 0, f"perf_event_paranoid={paranoid}"
    return {
        "cpu40_governor": gov,
        "cpu40_khz": cur,
        "uncore_khz": uncore,
        "perf_event_paranoid": paranoid,
        "node1_hugepages_2m_free": hp_free,
        "node1_hugepages_2m_total": hp_total,
        "host_edac_ue": edac_ue,
    }


def read_card_env() -> dict:
    cmd = (
        "cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 2000000; "
        "lsmod | grep -c '^pem_wc ' || true; "
        "cat /sys/devices/system/edac/mc/mc0/ce_count /sys/devices/system/edac/mc/mc0/ue_count "
        "/sys/devices/system/edac/ea/ea0/ce_count /sys/devices/system/edac/ea/ea0/ue_count; "
        "dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || true; "
        "cat /sys/devices/system/cpu/cpu3/cache/index3/size /sys/devices/system/cpu/cpu3/cache/index3/coherency_line_size; "
        "sha256sum /root/bench/mrv177_card | awk '{print $1}'"
    )
    out = subprocess.check_output(CARD_SSH + [cmd], text=True).strip().splitlines()
    return {
        "card_cpu3_khz": int(out[0]),
        "pem_wc_loaded": int(out[1]),
        "mc0_ce": int(out[2]),
        "mc0_ue": int(out[3]),
        "ea0_ce": int(out[4]),
        "ea0_ue": int(out[5]),
        "sei_uer": int(out[6]),
        "card_l3_size": out[7],
        "card_l3_line_bytes": int(out[8]),
        "card_bin_sha256": out[9],
    }


def stop_card():
    subprocess.run(CARD_SSH + ["pkill -INT -f '[/]root/bench/mrv177_card' || true"], check=False)
    for _ in range(40):
        time.sleep(0.15)
        p = subprocess.run(
            CARD_SSH + ["pgrep -f '[/]root/bench/mrv177_card' || true"],
            capture_output=True,
            text=True,
        )
        if not p.stdout.strip():
            return
    raise RuntimeError("mrv177_card did not exit cleanly after SIGINT")


def start_card(lcores: str, expected_cores: int):
    stop_card()
    cmd = (
        "tmux kill-session -t refl 2>/dev/null || true; "
        "rm -f /dev/hugepages/mrv177*; "
        "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "
        f"nohup {CARD_BIN} -l {lcores} --no-pci --file-prefix=mrv177 "
        "-- --control 10.10.0.2:7526 --sync 10.10.0.2:7527 > /tmp/mrv177_card.log 2>&1 < /dev/null &"
    )
    subprocess.check_call(CARD_SSH + [cmd])
    for _ in range(40):
        time.sleep(0.2)
        try:
            with socket.create_connection(("10.10.0.2", 7527), timeout=1.0) as s:
                s.sendall(b"PING\n")
                if s.recv(64).decode().strip() == "OK":
                    ps = subprocess.check_output(
                        CARD_SSH + ["pgrep -af '[/]root/bench/mrv177_card'"], text=True
                    )
                    assert f"-l {lcores} " in ps, f"unexpected card lcores: {ps.strip()}"
                    time.sleep(0.5)
                    return
        except OSError:
            pass
    raise RuntimeError(f"mrv177_card ({lcores}, expected {expected_cores}) failed to start")


def set_card_mode(card_cmd: str):
    with socket.create_connection(("10.10.0.2", 7527), timeout=3.0) as s:
        s.sendall((card_cmd + "\n").encode())
        resp = s.recv(256).decode().strip()
        assert resp.startswith("OK"), f"SET_MODE failed for {card_cmd!r}: {resp!r}"


def arm_spec(card_cores: int, arm: str) -> tuple[str, list[str], str]:
    """Return (card_mode_cmd, host_extra_args, credit_group)."""
    base_arm = arm
    extra_credit: list[str] = []
    group = "unshaped"
    if arm.endswith("_c64"):
        base_arm = arm[:-4]
        extra_credit = ["--credit-cap", "64"]
        group = "common_c64"
    elif arm.endswith("_c32"):
        base_arm = arm[:-4]
        extra_credit = ["--credit-cap", "32"]
        group = "ref_c32"
    elif arm.endswith("_uf"):
        group = "unfenced_ctrl"

    if base_arm == "base":
        return "SET_MODE baseline", extra_credit, group
    if base_arm == "bq_probe":
        if card_cores == 1:
            return "SET_MODE bqueue dmax=16 dmin=4 tbt_ns=200 ramp_k=4 fast_dt_ns=0 wait_first=0", extra_credit, group
        return "SET_MODE bqueue dmax=8 dmin=2 tbt_ns=200 ramp_k=4 fast_dt_ns=480 wait_first=0", extra_credit, group
    if base_arm == "bq_wait":
        if card_cores == 1:
            return "SET_MODE bqueue dmax=16 dmin=4 tbt_ns=200 ramp_k=4 fast_dt_ns=120 wait_first=1", extra_credit, group
        return "SET_MODE bqueue dmax=8 dmin=2 tbt_ns=1000 ramp_k=4 fast_dt_ns=480 wait_first=1", extra_credit, group
    if base_arm == "db_pkt_f":
        return "SET_MODE doorbell", ["--doorbell", "per-pkt", "--db-sfence", "true"] + extra_credit, group
    if base_arm == "db_pkt_uf":
        return "SET_MODE doorbell", ["--doorbell", "per-pkt", "--db-sfence", "false"] + extra_credit, group
    if base_arm == "db_adp_f":
        flush = "250" if card_cores == 1 else "500"
        return (
            "SET_MODE doorbell",
            [
                "--doorbell", "adaptive",
                "--db-sfence", "true",
                "--db-batch", "32",
                "--db-light", "4",
                "--db-flush-ns", flush,
            ]
            + extra_credit,
            group,
        )
    if base_arm == "db_adp_uf":
        flush = "250" if card_cores == 1 else "500"
        return (
            "SET_MODE doorbell",
            [
                "--doorbell", "adaptive",
                "--db-sfence", "false",
                "--db-batch", "32",
                "--db-light", "4",
                "--db-flush-ns", flush,
            ]
            + extra_credit,
            group,
        )
    raise ValueError(f"unknown arm: {arm}")

def rotate(items: list[str], rep: int) -> list[str]:
    if not items:
        return items
    k = rep % len(items)
    return items[k:] + items[:k]


def build_phase_schedule(card_cores: int) -> list[dict]:
    sched = []
    # WL1: Isolated small packets (W = 1, 2)
    wl1_w1_arms = ["base", "bq_probe", "bq_wait", "db_pkt_f", "db_pkt_uf", "db_adp_f", "db_adp_uf"]
    wl1_w2_arms = ["base", "bq_probe", "bq_wait", "db_pkt_f", "db_adp_f"]
    for rep in range(REPS):
        for arm in rotate(wl1_w1_arms, rep):
            sched.append({
                "part": "wl1_isolated",
                "workload": "w1",
                "arm": arm,
                "rep": rep,
                "wl_args": ["--mode", "closed", "-w", "1", "-n", "200000", "--warmup", "10000"],
            })
        for arm in rotate(wl1_w2_arms, rep):
            sched.append({
                "part": "wl1_isolated",
                "workload": "w2",
                "arm": arm,
                "rep": rep,
                "wl_args": ["--mode", "closed", "-w", "2", "-n", "200000", "--warmup", "10000"],
            })

    # WL2: Burst-stop tail packets (immediate residual 2 pkts AND post-5us-pause isolated 2 pkts)
    wl2_arms = ["base", "bq_probe", "bq_wait", "db_pkt_f", "db_adp_f"]
    for rep in range(REPS):
        for arm in rotate(wl2_arms, rep):
            sched.append({
                "part": "wl2_burst_tail",
                "workload": "burst32_resid2",
                "arm": arm,
                "rep": rep,
                "wl_args": [
                    "--mode", "burst-tail",
                    "--burst-len", "32",
                    "--tail-len", "2",
                    "--tail-gap-ns", "0",
                    "--tail-wait-drain", "false",
                    "--cycle-idle-ns", "10000",
                    "--cycles", "15000",
                    "--warmup-cycles", "1500",
                ],
            })
        for arm in rotate(wl2_arms, rep):
            sched.append({
                "part": "wl2_burst_tail",
                "workload": "burst32_pause_iso2",
                "arm": arm,
                "rep": rep,
                "wl_args": [
                    "--mode", "burst-tail",
                    "--burst-len", "32",
                    "--tail-len", "2",
                    "--tail-gap-ns", "5000",
                    "--tail-wait-drain", "true",
                    "--cycle-idle-ns", "10000",
                    "--cycles", "15000",
                    "--warmup-cycles", "1500",
                ],
            })

    # WL3: Closed-loop high concurrency (W = 32, 64, 128)
    # Note: for W <= 64, inflight <= 64 naturally (unshaped == common_c64).
    # For W = 128, include BOTH unshaped AND full symmetric common_c64 arms + ref base_c32.
    wl3_specs = [
        ("w32", 32, ["base", "bq_probe", "bq_wait", "db_pkt_f", "db_adp_f"]),
        (
            "w64",
            64,
            ["base", "base_c32", "bq_probe", "bq_wait", "db_pkt_f", "db_pkt_uf", "db_adp_f", "db_adp_uf"],
        ),
        (
            "w128",
            128,
            [
                "base", "bq_probe", "bq_wait", "db_pkt_f", "db_adp_f",
                "base_c64", "bq_probe_c64", "bq_wait_c64", "db_pkt_f_c64", "db_adp_f_c64",
                "base_c32", "db_pkt_uf", "db_adp_uf",
            ],
        ),
    ]
    for rep in range(REPS):
        for wname, wval, arms in wl3_specs:
            for arm in rotate(arms, rep):
                sched.append({
                    "part": "wl3_closed",
                    "workload": wname,
                    "arm": arm,
                    "rep": rep,
                    "wl_args": ["--mode", "closed", "-w", str(wval), "-n", "1500000", "--warmup", "50000"],
                })

    # WL4: Open-loop Poisson (10, 13.5, 16, 20, 25 M/s)
    # Principal single-variable matrix:
    #   1) Full symmetric common_c64 arm set: base_c64, bq_probe_c64, bq_wait_c64, db_pkt_f_c64, db_adp_f_c64
    #   2) Full symmetric unshaped arm set:   base, bq_probe, bq_wait, db_pkt_f, db_adp_f
    # Supplemental controls:
    #   3) ref_c32: base_c32 (MRV-123 [18,25] reference)
    #   4) unfenced_ctrl: db_pkt_uf, db_adp_uf at 20 M/s (and db_adp_uf at 25 M/s on 1C)
    rates = [
        ("p10", 10.0, 1_000_000, 100_000),
        ("p13.5", 13.5, 2_000_000, 200_000),
        ("p16", 16.0, 2_000_000, 200_000),
        ("p20", 20.0, 2_000_000, 200_000),
        ("p25", 25.0, 2_000_000, 200_000),
    ]
    for rep in range(REPS):
        seed = 177 + rep * 100
        for rname, rate, rounds, warmup in rates:
            arms = [
                # Principal symmetric C_line = 64 set (zero credit confound across all 5 arms)
                "base_c64", "bq_probe_c64", "bq_wait_c64", "db_pkt_f_c64", "db_adp_f_c64",
                # Principal symmetric unshaped set (C_line = 4032)
                "base", "bq_probe", "bq_wait", "db_pkt_f", "db_adp_f",
                # Supplemental MRV-123 C_line = 32 reference
                "base_c32",
            ]
            if rate == 20.0:
                arms.extend(["db_pkt_uf", "db_adp_uf"])
            elif card_cores == 1 and rate == 25.0:
                arms.append("db_adp_uf")
            for arm in rotate(arms, rep):
                sched.append({
                    "part": "wl4_poisson",
                    "workload": rname,
                    "arm": arm,
                    "rep": rep,
                    "wl_args": [
                        "--mode", "open",
                        "--open-mps", f"{rate:g}",
                        "--arrival", "poisson",
                        "--seed", str(seed),
                        "-n", str(rounds),
                        "--warmup", str(warmup),
                    ],
                })
    return sched


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    host_before = read_host_env()
    card_before = read_card_env()
    jj_rev = subprocess.check_output(
        ["jj", "log", "-r", "@-", "--no-graph", "-T", 'change_id.short() ++ " " ++ commit_id.short() ++ " " ++ description.first_line()'],
        cwd=str(ROOT),
        text=True,
    ).strip()

    print(f"ENV host_before: {host_before}", flush=True)
    print(f"ENV card_before: {card_before}", flush=True)
    print(f"CODE commit: {jj_rev}", flush=True)

    campaign = utc_now()
    run_id = 0
    attempt_id = sum(1 for _ in ATTEMPTS_PATH.open("r", encoding="utf-8")) if ATTEMPTS_PATH.exists() else 0
    total_passed = 0
    total_failed_attempts = 0
    t_all = time.monotonic()

    with RUNS_PATH.open("w", encoding="utf-8") as frun, ATTEMPTS_PATH.open("a", encoding="utf-8") as fatt:
        for phase_name, lcores, card_cores in [("1C", "3", 1), ("4C", "3-6", 4)]:
            print(f"\n================ PHASE {phase_name} (-l {lcores}) ================", flush=True)
            start_card(lcores, expected_cores=card_cores)
            sched = build_phase_schedule(card_cores)
            print(f"Scheduled {len(sched)} verified runs for {phase_name}", flush=True)

            for idx, item in enumerate(sched, 1):
                run_id += 1
                part = item["part"]
                wl = item["workload"]
                arm = item["arm"]
                rep = item["rep"]
                card_cmd, arm_args, credit_group = arm_spec(card_cores, arm)
                cfg = f"{phase_name.lower()}_{wl}_{arm}"

                passed = False
                for attempt in range(MAX_ATTEMPTS):
                    attempt_id += 1
                    wait_for_turbo()
                    set_card_mode(card_cmd)
                    cmd = [HOST_BIN] + EAL_ARGS + ["--", "--pmu"] + item["wl_args"] + arm_args
                    ts_utc = utc_now()
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

                    payload = None
                    reasons = []
                    if proc.returncode != 0:
                        reasons.append(f"rc={proc.returncode}: {proc.stderr.strip()[:200]}")
                    else:
                        for line in proc.stdout.splitlines():
                            if line.startswith("MRV177 "):
                                payload = json.loads(line[7:])
                                break
                        if payload is None:
                            reasons.append("missing MRV177 JSON")

                    if payload is not None:
                        hp = payload["host_pmu"]
                        cp = payload["card_pmu"]
                        corrupt = sum(c["corrupt"] for c in cp["cores"])
                        if payload["cores"] != card_cores:
                            reasons.append(f"cores={payload['cores']}!=expected {card_cores}")
                        if payload["lost"] != 0 or payload["bad"] != 0 or payload["torn"] != 0 or corrupt != 0:
                            reasons.append(
                                f"integrity l/b/t/c={payload['lost']}/{payload['bad']}/{payload['torn']}/{corrupt}"
                            )
                        if hp["min_pct"] < 99.99 or cp["min_pct"] < 99.99:
                            reasons.append(f"pmu mux host={hp['min_pct']} card={cp['min_pct']}")
                        if hp["page_faults"] != 0:
                            reasons.append(f"page_faults={hp['page_faults']}")
                        if hp["ghz"] < MIN_CPU_GHZ:
                            reasons.append(f"cpu40_ghz={hp['ghz']:.4f}<{MIN_CPU_GHZ}")
                        if hp["mesh_ghz"] < MIN_MESH_GHZ:
                            reasons.append(f"mesh_ghz={hp['mesh_ghz']:.4f}<{MIN_MESH_GHZ}")

                    att_rec = {
                        "campaign": campaign,
                        "attempt_id": attempt_id,
                        "run_id": run_id,
                        "ts_utc": ts_utc,
                        "phase": phase_name,
                        "card_cores": card_cores,
                        "part": part,
                        "workload": wl,
                        "arm": arm,
                        "credit_group": credit_group,
                        "cfg": cfg,
                        "rep": rep,
                        "attempt": attempt,
                        "status": "pass" if not reasons else "fail",
                        "fail_reasons": reasons,
                        "card_mode_cmd": card_cmd,
                        "cmd": " ".join(cmd),
                    }
                    if payload is not None:
                        att_rec.update(payload)
                    fatt.write(json.dumps(att_rec, separators=(",", ":")) + "\n")
                    fatt.flush()

                    if not reasons:
                        frun.write(json.dumps(att_rec, separators=(",", ":")) + "\n")
                        frun.flush()
                        total_passed += 1
                        passed = True
                        hp = payload["host_pmu"]
                        cp = payload["card_pmu"]
                        tt = sum(c["taken"] for c in cp["cores"])
                        snp = cp["tad"]["tad_snp_msh_out_any"]["val"] / max(1, tt)
                        ret = cp["tad"]["tad_rsp_msh_out_retry_exlmn"]["val"] / max(1, tt)
                        ae = sum(c["db_ahead_empty"] for c in cp["cores"])
                        t = payload["tot_us"]
                        ach = payload.get("achieved_mps", 0.0)
                        if idx % 10 == 1 or idx == len(sched) or wl in ("w1", "w64", "w128", "p20", "p25", "burst32_resid2", "burst32_pause_iso2"):
                            print(
                                f"[{run_id:3d} {phase_name} {idx:3d}/{len(sched)}] {cfg:30s} r{rep} | "
                                f"ach={ach:5.2f} p50={t['p50']:6.2f} p99={t['p99']:7.2f} | "
                                f"cha={hp['cha_inflight']:5.2f} snp={snp:4.2f} ret={ret:5.3f} dbR={payload['db_rate_mps']:4.2f} ae={ae:4d} ghz={hp['ghz']:.2f}",
                                flush=True,
                            )
                        break
                    else:
                        total_failed_attempts += 1
                        print(
                            f"  [FAIL attempt {attempt+1}/{MAX_ATTEMPTS}] run_id={run_id} {cfg} r{rep}: {'; '.join(reasons)}",
                            flush=True,
                        )
                        time.sleep(0.5)

                if not passed:
                    raise RuntimeError(f"run_id={run_id} {cfg} r{rep} failed after {MAX_ATTEMPTS} attempts")

    stop_card()
    time.sleep(1.0)
    host_after = read_host_env()
    card_after = read_card_env()
    elapsed_s = time.monotonic() - t_all

    env_doc = {
        "issue": "MRV-177",
        "ts_utc": utc_now(),
        "elapsed_s": round(elapsed_s, 2),
        "code_commit": jj_rev,
        "host_bin_sha256": sha256_file(HOST_BIN),
        "total_verified_runs": total_passed,
        "total_attempts": attempt_id,
        "total_failed_attempts": total_failed_attempts,
        "host_before": host_before,
        "host_after": host_after,
        "card_before": card_before,
        "card_after": card_after,
    }
    ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n", encoding="utf-8")
    print(
        f"\nDONE: {total_passed} verified runs ({attempt_id} total attempts, {total_failed_attempts} failed) "
        f"in {elapsed_s:.1f}s. EDAC host_after={host_after['host_edac_ue']} card_after_ue={card_after['mc0_ue']}/{card_after['ea0_ue']}/{card_after['sei_uer']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
