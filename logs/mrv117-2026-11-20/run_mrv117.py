#!/usr/bin/env python3
"""MRV-117 full interleaved experiment harness (pure commercial PCIe NIC, zero Intel DSA).

3-tier Occam's Razor ladder (MRV-82) on identical hardware (cpu40 3.90 GHz, Socket 1
uncore 2.20 GHz, CN106 2.00 GHz, intel_iommu=on,sm_on), identical 128 MiB Socket-1
2 MiB-hugepage retained TX payload pool (page_faults=0), MAX_DRAIN=32, and strictly
symmetric 1 Host core (cpu40) + 1 Card core (core 3) accounting:
  (a) sdp        : Traditional SDP descriptor ring (MTU 9000 single-frame) + controls
                   (sdp_zero_copy, sdp_rx_poll_mem)
  (b) push       : Pure MOVDIR64B -> BAR4 + Card PEM0 WC -> Host DDIO (all sizes)
  (c) directpush : Unified DirectPush (<=256 B Push + >=4 KiB CN106 Native DPI DMA)

Parts (3 fully interleaved replicates, alternating SDP <-> Direct order per rep):
  - part0_cal    : Pure idle app_work_step() baseline calibration on cpu40 (3 runs)
  - part1_size   : Pure-size single-variable crossover sweep (64/256/1024/4096/8192 B,
                   W=1 unloaded RTT & W=16/32 saturation + SDP controls)
  - part2_mixed  : Mixed RPC/KV workloads (mix_ads 90/10, mix_geo 50/50, mix_4size 60/20/15/5)
                   across W in {1, 4, 8, 16, 32}
  - part3_cpu    : Matched-throughput host CPU utilization & compute headroom (--app-work)
                   at fixed offered rates R where all tiers keep up cleanly
"""

import json
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv117/logs/mrv117-2026-11-20")
RUNS_PATH = OUT_DIR / "runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"
HOST_BIN = "/home/hongtao/dev/marvell-mrv117/testbed/target/release/mrv117_host"
CARD_BIN = "/root/bench/mrv117_card"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

REPS = 3


def sh(cmd, check=True, timeout=180):
    r = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        print(f"CMD FAILED ({r.returncode}): {cmd}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}", file=sys.stderr, flush=True)
        raise subprocess.CalledProcessError(r.returncode, cmd, r.stdout, r.stderr)
    return r

def ssh(script, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def check_host_env():
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
    edac = [
        int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count"))
    ]
    cmdline = pathlib.Path("/proc/cmdline").read_text().strip()
    assert gov == "performance", f"cpu40 governor={gov}"
    assert uncore.get("package_01_die_01", 0) >= 2_190_000, f"uncore={uncore}"
    assert paranoid <= 0, f"perf_event_paranoid={paranoid}"
    assert hp_free >= 256, f"node1 2M hugepages free too low: {hp_free}"
    return {
        "cpu40_governor": gov,
        "cpu40_khz": cur,
        "uncore_khz": uncore,
        "perf_event_paranoid": paranoid,
        "node1_hugepages_2m_free": hp_free,
        "node1_hugepages_2m_total": hp_total,
        "edac_ue_counts": edac,
        "edac_ue_sum": sum(edac),
        "cmdline": cmdline,
    }


def check_card_env():
    out = ssh(
        "echo freq=$(cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 0);"
        "echo pem_wc=$(lsmod | grep -c '^pem_wc ' || true);"
        "echo hp512_free=$(cat /sys/kernel/mm/hugepages/hugepages-524288kB/free_hugepages 2>/dev/null || echo 0);"
        "echo dpi_vfs=$(dpdk-devbind.py -s | grep -c '0000:06:00\\.' || true)"
    ).stdout
    info = {}
    for line in out.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k] = int(v) if v.isdigit() else v
    return info


def stop_card_server():
    ssh(
        "tmux send-keys -t m117 C-c 2>/dev/null || true; "
        "tmux send-keys -t refl C-c 2>/dev/null || true; "
        "sleep 0.8; "
        "tmux kill-session -t m117 2>/dev/null || true; "
        "tmux kill-session -t refl 2>/dev/null || true; "
        "sleep 0.3"
    )


def start_card_sdp():
    stop_card_server()
    ssh(
        f'tmux new-session -d -s m117 "{CARD_BIN} -l 3 -a 0002:01:00.2 --file-prefix=m117 -- --mode sdp"; '
        'for i in $(seq 1 40); do '
        '  if tmux capture-pane -pt m117 | grep -q "sdp ready"; then break; fi; '
        '  sleep 0.2; '
        'done; '
        'tmux capture-pane -pt m117 | grep "sdp ready"'
    )
    pane = ssh("tmux capture-pane -pt m117").stdout
    m = re.search(r"mac=([0-9a-f:]{17})", pane)
    if not m:
        raise RuntimeError(f"Failed to detect card SDP MAC:\n{pane}")
    return m.group(1)


def start_card_direct():
    stop_card_server()
    ssh(
        "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "
        f'tmux new-session -d -s m117 "{CARD_BIN} -l 3 -a 0002:01:00.2 -a 0000:06:00.1 -a 0000:06:00.2 --file-prefix=m117 -- --mode direct"; '
        'for i in $(seq 1 40); do '
        '  if tmux capture-pane -pt m117 | grep -q "direct ready"; then break; fi; '
        '  sleep 0.2; '
        'done; '
        'tmux capture-pane -pt m117 | grep "direct ready"'
    )


def run_calibration(rep):
    cmd = [
        HOST_BIN, "-l", "40", "--file-prefix=mrv117h",
        "-a", "0000:99:02.0", "--socket-mem=0,512", "--iova-mode=va", "--",
        "--calibrate-app-work-ms", "500", "--pmu",
    ]
    res = sh(cmd, timeout=60)
    for line in res.stdout.splitlines():
        if line.startswith("MRV117_CAL "):
            rec = json.loads(line[len("MRV117_CAL "):])
            rec["part"] = "part0_cal"
            rec["cfg"] = "app_work_idle_cal"
            rec["rep"] = rep
            rec["ts_utc"] = datetime.now(timezone.utc).isoformat()
            hp = rec["host_pmu"]
            ok = (hp["page_faults"] == 0 and hp["min_running_pct"] >= 99.9 and hp["ghz"] >= 3.85)
            rec["verified"] = ok
            rec["verify_errors"] = [] if ok else [f"pmu={hp}"]
            return rec
    raise RuntimeError(f"No MRV117_CAL line:\n{res.stdout}\n{res.stderr}")


def build_specs():
    """Return (sdp_specs, direct_specs) for one replicate."""
    sdp_specs = []
    direct_specs = []

    def add(part, cfg, tier, wl, w, rounds, warmup, **extra):
        s = {
            "part": part,
            "cfg": cfg,
            "tier": tier,
            "workload": wl,
            "window": w,
            "rounds": rounds,
            "warmup": warmup,
            "dma_threshold": extra.get("dma_threshold", 1024),
            "sdp_zero_copy": extra.get("sdp_zero_copy", False),
            "rx_poll_mem": extra.get("rx_poll_mem", False),
            "pace_rate_krps": extra.get("pace_rate_krps", 0.0),
            "app_work": extra.get("app_work", False),
        }
        if tier == "sdp":
            sdp_specs.append(s)
        else:
            direct_specs.append(s)

    # ---- Part 1A: Pure-size W=1 unloaded latency (64/256/1024/4096/8192 B) ----
    w1_rounds = {"64": (60000, 10000), "256": (60000, 10000), "1024": (40000, 8000), "4096": (30000, 5000), "8192": (25000, 5000)}
    for wl in ("64", "256", "1024", "4096", "8192"):
        r, wu = w1_rounds[wl]
        add("part1_size_w1", f"sdp_{wl}_w1", "sdp", wl, 1, r, wu)
        add("part1_size_w1", f"push_{wl}_w1", "push", wl, 1, r, wu)
        add("part1_size_w1", f"directpush_{wl}_w1", "directpush", wl, 1, r, wu)
    # SDP rx_poll_mem=1 control at W=1 (64 B, 4096 B)
    add("part1_size_w1_ctrl", "sdp_rpm_64_w1", "sdp", "64", 1, 60000, 10000, rx_poll_mem=True)
    add("part1_size_w1_ctrl", "sdp_rpm_4096_w1", "sdp", "4096", 1, 30000, 5000, rx_poll_mem=True)

    # ---- Part 1B: Pure-size W=16 saturation throughput & PMU + W=32 small peak + SDP controls ----
    w16_rounds = {
        "64": (400000, 80000, 200000, 40000),
        "256": (300000, 60000, 200000, 40000),
        "1024": (200000, 40000, 180000, 30000),
        "4096": (150000, 30000, 120000, 20000),
        "8192": (100000, 20000, 90000, 20000),
    }
    for wl in ("64", "256", "1024", "4096", "8192"):
        rd, wud, rs, wus = w16_rounds[wl]
        add("part1_size_w16", f"sdp_{wl}_w16", "sdp", wl, 16, rs, wus)
        add("part1_size_w16", f"push_{wl}_w16", "push", wl, 16, rd, wud)
        add("part1_size_w16", f"directpush_{wl}_w16", "directpush", wl, 16, rd, wud)
    # Small peak at W=32
    for wl in ("64", "256"):
        add("part1_size_w32", f"sdp_{wl}_w32", "sdp", wl, 32, 200000, 40000)
        add("part1_size_w32", f"push_{wl}_w32", "push", wl, 32, 400000, 80000)
        add("part1_size_w32", f"directpush_{wl}_w32", "directpush", wl, 32, 400000, 80000)
    # SDP zero-copy & rx_poll_mem controls at W=16
    add("part1_size_ctrl", "sdp_zc_4096_w16", "sdp", "4096", 16, 120000, 20000, sdp_zero_copy=True)
    add("part1_size_ctrl", "sdp_zc_8192_w16", "sdp", "8192", 16, 90000, 20000, sdp_zero_copy=True)
    add("part1_size_ctrl", "sdp_rpm_64_w16", "sdp", "64", 16, 200000, 40000, rx_poll_mem=True)
    add("part1_size_ctrl", "sdp_rpm_4096_w16", "sdp", "4096", 16, 120000, 20000, rx_poll_mem=True)

    # ---- Part 2: Mixed RPC/KV workloads (mix_ads, mix_geo, mix_4size) across W in {1, 4, 8, 16, 32} ----
    for wl in ("mix_ads", "mix_geo", "mix_4size"):
        for w in (1, 4, 8, 16, 32):
            if w == 1:
                rs, wus, rd, wud = 40000, 8000, 50000, 10000
            elif wl == "mix_ads":
                rs, wus, rd, wud = 200000, 40000, 350000, 70000
            elif wl == "mix_geo":
                rs, wus, rd, wud = 150000, 30000, 200000, 40000
            else:
                rs, wus, rd, wud = 180000, 35000, 250000, 50000
            add("part2_mixed", f"sdp_{wl}_w{w}", "sdp", wl, w, rs, wus)
            add("part2_mixed", f"push_{wl}_w{w}", "push", wl, w, rd, wud)
            add("part2_mixed", f"directpush_{wl}_w{w}", "directpush", wl, w, rd, wud)

    # ---- Part 3: Matched-throughput host CPU utilization & compute headroom (--app-work) ----
    # Each run lasts exactly 200 ms at the offered rate R.
    cpu_cases = [
        ("mix_ads", 1000.0, 200000, 40000),
        ("mix_ads", 2000.0, 400000, 80000),
        ("mix_geo", 500.0, 100000, 20000),
        ("mix_geo", 1000.0, 200000, 40000),
        ("mix_4size", 1000.0, 200000, 40000),
        ("4096", 1000.0, 200000, 40000),
    ]
    for wl, rate, r, wu in cpu_cases:
        tag = f"{wl}_r{int(rate)}"
        add("part3_cpu", f"sdp_{tag}", "sdp", wl, 16, r, wu, pace_rate_krps=rate, app_work=True)
        add("part3_cpu", f"push_{tag}", "push", wl, 16, r, wu, pace_rate_krps=rate, app_work=True)
        add("part3_cpu", f"directpush_{tag}", "directpush", wl, 16, r, wu, pace_rate_krps=rate, app_work=True)
    # Controls for Part 3: SDP rx_poll_mem=1 & SDP zero-copy
    add("part3_cpu_ctrl", "sdp_rpm_mix_ads_r2000", "sdp", "mix_ads", 16, 400000, 80000, pace_rate_krps=2000.0, app_work=True, rx_poll_mem=True)
    add("part3_cpu_ctrl", "sdp_rpm_mix_geo_r1000", "sdp", "mix_geo", 16, 200000, 40000, pace_rate_krps=1000.0, app_work=True, rx_poll_mem=True)
    add("part3_cpu_ctrl", "sdp_rpm_4096_r1000", "sdp", "4096", 16, 200000, 40000, pace_rate_krps=1000.0, app_work=True, rx_poll_mem=True)
    add("part3_cpu_ctrl", "sdp_zc_4096_r1000", "sdp", "4096", 16, 200000, 40000, pace_rate_krps=1000.0, app_work=True, sdp_zero_copy=True)
    add("part3_cpu_ctrl", "sdp_rpm_zc_4096_r1000", "sdp", "4096", 16, 200000, 40000, pace_rate_krps=1000.0, app_work=True, rx_poll_mem=True, sdp_zero_copy=True)

    return sdp_specs, direct_specs


def run_one(spec, rep, sdp_mac=None):
    devarg = "0000:99:02.0,rx_poll_mem=1" if spec["rx_poll_mem"] else "0000:99:02.0"
    cmd = [
        HOST_BIN, "-l", "40", "--file-prefix=mrv117h",
        "-a", devarg, "--socket-mem=0,512", "--iova-mode=va", "--",
        "--tier", spec["tier"],
        "--workload", spec["workload"],
        "-w", str(spec["window"]),
        "-n", str(spec["rounds"]),
        "--warmup", str(spec["warmup"]),
        "--dma-threshold", str(spec["dma_threshold"]),
        "--pmu",
    ]
    if spec["tier"] == "sdp":
        cmd += ["--sdp-peer", sdp_mac]
    if spec["sdp_zero_copy"]:
        cmd.append("--sdp-zero-copy")
    if spec["pace_rate_krps"] > 0.0:
        cmd += ["--pace-rate-krps", f"{spec['pace_rate_krps']:.3f}"]
    if spec["app_work"]:
        cmd.append("--app-work")

    res = sh(cmd, timeout=120)
    payload = None
    for line in res.stdout.splitlines():
        if line.startswith("MRV117 "):
            payload = json.loads(line[len("MRV117 "):])
            break
    if payload is None:
        raise RuntimeError(f"No MRV117 output for {spec['cfg']} rep={rep}:\n{res.stdout}\n{res.stderr}")

    payload["part"] = spec["part"]
    payload["cfg"] = spec["cfg"]
    payload["rep"] = rep
    payload["rx_poll_mem"] = spec["rx_poll_mem"]
    payload["ts_utc"] = datetime.now(timezone.utc).isoformat()

    # Strict programmatic verification
    errs = []
    if payload["lost"] != 0 or payload["bad"] != 0 or payload["torn"] != 0:
        errs.append(f"integrity lost={payload['lost']} bad={payload['bad']} torn={payload['torn']}")
    if payload["measured"] != spec["rounds"]:
        errs.append(f"measured={payload['measured']} != rounds={spec['rounds']}")
    if payload["pool_page_kb"] != 2048 or payload["pool_numa_n0"] != 0 or payload["pool_numa_n1"] != 64:
        errs.append(
            f"pool page_kb={payload['pool_page_kb']} n0={payload['pool_numa_n0']} n1={payload['pool_numa_n1']}"
        )
    hp = payload["host_pmu"]
    if hp["page_faults"] != 0:
        errs.append(f"page_faults={hp['page_faults']}")
    if hp["min_running_pct"] < 99.9:
        errs.append(f"pmu mux min_running_pct={hp['min_running_pct']}")
    cs = payload["card_stats"]
    if cs["corrupt"] != 0 or cs["sdp_drops"] != 0 or cs["dma_failed"] != 0:
        errs.append(f"card_stats={cs}")
    expected_total = spec["rounds"] + spec["warmup"]
    if spec["tier"] == "sdp":
        if cs["sdp_tx"] != expected_total:
            errs.append(f"card sdp_tx={cs['sdp_tx']} != {expected_total}")
    else:
        served = cs["push_small"] + cs["push_large"] + cs["dma_large"]
        if served != expected_total:
            errs.append(f"card served={served} != {expected_total}")
    if spec["window"] == 1 and spec["pace_rate_krps"] == 0.0:
        mean_us = payload["lat_all_us"]["mean"]
        ratio = mean_us * payload["req_per_s"] / 1e6
        if not (0.90 <= ratio <= 1.03):
            errs.append(f"W=1 RTT*rate={ratio:.4f}")
    payload["verified"] = len(errs) == 0
    payload["verify_errors"] = errs
    if errs:
        raise RuntimeError(f"Verification failed on {spec['cfg']} rep={rep}: {errs}")
    return payload


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if RUNS_PATH.exists():
        RUNS_PATH.unlink()

    host_before = check_host_env()
    card_before = check_card_env()
    sdp_specs, direct_specs = build_specs()
    total_runs = REPS * (1 + len(sdp_specs) + len(direct_specs))
    print(
        f"MRV-117 starting: {REPS} interleaved reps x (1 cal + {len(sdp_specs)} SDP + {len(direct_specs)} Direct) "
        f"= {total_runs} runs",
        flush=True,
    )

    run_id = 0
    t_start = time.time()
    cal_idle_rates = []

    with RUNS_PATH.open("w") as f:
        for rep in range(REPS):
            # 0. Calibration
            run_id += 1
            cal = run_calibration(rep)
            cal["run_id"] = run_id
            cal_idle_rates.append(cal["units_per_s"])
            f.write(json.dumps(cal) + "\n")
            f.flush()
            print(
                f"[{run_id:3d}/{total_runs}] rep={rep} CAL app_work_idle={cal['units_per_s']/1e6:.3f} M/s "
                f"({cal['ns_per_unit']:.1f} ns/u) ghz={cal['host_pmu']['ghz']:.3f}",
                flush=True,
            )

            # Alternate SDP vs Direct order across reps to cancel drift
            order = ("sdp", "direct") if (rep % 2 == 0) else ("direct", "sdp")
            for mode in order:
                if mode == "sdp":
                    sdp_mac = start_card_sdp()
                    specs = sdp_specs
                else:
                    sdp_mac = None
                    start_card_direct()
                    specs = direct_specs

                for spec in specs:
                    run_id += 1
                    rec = run_one(spec, rep, sdp_mac=sdp_mac)
                    rec["run_id"] = run_id
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                    hp = rec["host_pmu"]
                    lat = rec["lat_all_us"]
                    extra = ""
                    if spec["app_work"] and cal_idle_rates:
                        idle_r = cal_idle_rates[-1]
                        net_pct = (1.0 - rec["app_work_per_s"] / idle_r) * 100.0
                        extra = f" | app={rec['app_work_per_s']/1e6:5.2f}M net_cpu={net_pct:5.1f}%"
                    print(
                        f"[{run_id:3d}/{total_runs}] rep={rep} {spec['cfg']:26s} | "
                        f"{rec['mreq_s']:6.3f} Mreq/s ({rec['bidir_gbps']:5.2f} GB/s bi) | "
                        f"p50={lat['p50']:5.2f} p99={lat['p99']:6.2f}us | "
                        f"ghz={hp['ghz']:.2f} llcM/r={hp['llc_load_misses']/rec['measured']:5.2f}{extra}",
                        flush=True,
                    )

    stop_card_server()
    host_after = check_host_env()
    card_after = check_card_env()
    elapsed = time.time() - t_start
    env_doc = {
        "issue": "MRV-117",
        "date": "2026-11-20",
        "elapsed_s": round(elapsed, 2),
        "total_runs": run_id,
        "reps": REPS,
        "cal_idle_units_per_s": cal_idle_rates,
        "host_before": host_before,
        "host_after": host_after,
        "edac_ue_delta": host_after["edac_ue_sum"] - host_before["edac_ue_sum"],
        "card_before": card_before,
        "card_after": card_after,
    }
    ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n")
    print(
        f"MRV-117 COMPLETE: {run_id} verified runs in {elapsed:.1f}s | EDAC ue delta = {env_doc['edac_ue_delta']}",
        flush=True,
    )
    assert env_doc["edac_ue_delta"] == 0, f"EDAC ue_count increased: {env_doc['edac_ue_delta']}"


if __name__ == "__main__":
    main()
