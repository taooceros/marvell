#!/usr/bin/env python3
"""MRV-166 full interleaved experiment harness.

Compares SDP (default -> sync-tail -> async-tail -> +ism -> +rx512 DDIO -> fair-full)
against clean DirectPush across 64 B, 1 KiB, and 4 KiB on the exact same 1H+1C setup
(cpu40 @ 3.90 GHz, Socket 1 uncore 2.20 GHz, CN106 core 3 @ 2.00 GHz, zero DSA).

Enforces strictly:
- n = 3 interleaved replicates (alternating SDP / DirectPush order)
- lost = bad = torn = 0, card corrupt = sdp_drops = dma_failed = 0
- Host PMU min_running_pct >= 99.9% (100% non-multiplexed)
- page_faults = 0, tx_pool on Socket 1 2 MiB hugepages (N0=0, N1=64)
- EDAC ue_count delta = 0
"""

import json
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv166/logs/mrv166-2026-11-23")
RUNS_PATH = OUT_DIR / "runs.jsonl"
ATTEMPTS_PATH = OUT_DIR / "attempts.jsonl"
ENV_PATH = OUT_DIR / "env.json"
HOST_BIN = "/home/hongtao/dev/marvell-mrv166/testbed/target/release/mrv166_host"
CARD_BIN = "/root/bench/mrv166_card"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

REPS = 3


def sh(cmd, check=True, timeout=180):
    r = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed ({r.returncode}): {cmd}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
    return r


def ssh(script, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def check_host_env():
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    khz = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("package_*")):
        uncore[p.name] = int((p / "current_freq_khz").read_text().strip())
    paranoid = int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    hp_free = int(pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/free_hugepages").read_text().strip())
    hp_tot = int(pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/nr_hugepages").read_text().strip())
    edac_ue = []
    for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count")):
        edac_ue.append(int(p.read_text().strip()))
    cmdline = pathlib.Path("/proc/cmdline").read_text().strip()
    return {
        "cpu40_governor": gov,
        "cpu40_khz": khz,
        "uncore_khz": uncore,
        "perf_event_paranoid": paranoid,
        "node1_hugepages_2m_free": hp_free,
        "node1_hugepages_2m_total": hp_tot,
        "edac_ue_counts": edac_ue,
        "edac_ue_sum": sum(edac_ue),
        "cmdline": cmdline,
    }


def check_card_env():
    out = ssh(
        "echo freq=$(cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 2000000); "
        "echo pem_wc=$(lsmod | grep -c '^pem_wc ' || true); "
        "echo hp512_free=$(cat /sys/kernel/mm/hugepages/hugepages-524288kB/free_hugepages 2>/dev/null || echo -1); "
        "echo dpi_vfs=$(ls -d /sys/bus/pci/devices/0000:06:00.[1-4] 2>/dev/null | wc -l); "
        "echo card_edac_ue=$(cat /sys/devices/system/edac/mc/mc*/ue_count 2>/dev/null | awk '{s+=$1} END{print s+0}')"
    ).stdout
    info = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = int(v.strip())
    return info


def stop_card_server():
    ssh(
        "tmux send-keys -t m166 C-c 2>/dev/null || true; "
        "tmux send-keys -t refl C-c 2>/dev/null || true; "
        "pkill -INT mrv166_card 2>/dev/null || true; "
        "for i in $(seq 1 30); do pgrep -f mrv166_card >/dev/null || break; sleep 0.1; done; "
        "tmux kill-session -t m166 2>/dev/null || true; "
        "tmux kill-session -t refl 2>/dev/null || true; "
        "sleep 0.3"
    )


def start_card_sdp():
    stop_card_server()
    ssh(
        f'tmux new-session -d -s m166 "exec {CARD_BIN} -l 3 -a 0002:01:00.2 --file-prefix=m166 -- --mode sdp"; '
        'for i in $(seq 1 50); do '
        '  if tmux capture-pane -pt m166 2>/dev/null | grep -q "sdp ready"; then break; fi; '
        '  sleep 0.2; '
        'done; '
        'tmux capture-pane -pt m166 | grep "sdp ready"'
    )
    pane = ssh("tmux capture-pane -pt m166").stdout
    m = re.search(r"mac=([0-9a-f:]{17})", pane)
    if not m:
        raise RuntimeError(f"Failed to detect card SDP MAC:\n{pane}")
    return m.group(1)


def start_card_direct():
    stop_card_server()
    ssh(
        "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "
        f'tmux new-session -d -s m166 "exec {CARD_BIN} -l 3 -a 0002:01:00.2 -a 0000:06:00.1 -a 0000:06:00.2 --file-prefix=m166 -- --mode direct"; '
        'for i in $(seq 1 50); do '
        '  if tmux capture-pane -pt m166 2>/dev/null | grep -q "direct ready"; then break; fi; '
        '  sleep 0.2; '
        'done; '
        'tmux capture-pane -pt m166 | grep "direct ready"'
    )


def build_devarg(spec):
    opts = []
    if spec.get("rx_poll_mem"):
        opts.append("rx_poll_mem=1")
    if spec.get("rx_poll_tail_off", 0):
        opts.append(f"rx_poll_tail_off={spec['rx_poll_tail_off']}")
    if spec.get("ism_enable"):
        opts.append("ism_enable=1")
    if spec.get("allow_small_oq"):
        opts.append("allow_small_oq=1")
    return "0000:99:02.0" + ("," + ",".join(opts) if opts else "")


def run_calibration(rep):
    cmd = [
        HOST_BIN, "-l", "40", "--file-prefix=mrv166h",
        "-a", "0000:99:02.0", "--socket-mem=0,512", "--iova-mode=va", "--",
        "--calibrate-app-work-ms", "500", "--pmu",
    ]
    res = sh(cmd, timeout=60)
    for line in res.stdout.splitlines():
        if line.startswith("MRV166_CAL "):
            rec = json.loads(line[len("MRV166_CAL "):])
            rec["part"] = "part0_cal"
            rec["cfg"] = "app_work_idle_cal"
            rec["rep"] = rep
            rec["ts_utc"] = datetime.now(timezone.utc).isoformat()
            hp = rec["host_pmu"]
            ok = (hp["page_faults"] == 0 and hp["min_running_pct"] >= 99.9 and hp["ghz"] >= 3.88)
            rec["verified"] = ok
            rec["verify_errors"] = [] if ok else [f"cpu40 ghz={hp['ghz']} pf={hp['page_faults']} min_pct={hp['min_running_pct']}"]
            return rec
    raise RuntimeError(f"No MRV166_CAL line:\n{res.stdout}\n{res.stderr}")


def run_zero_load(spec, rep):
    devarg = build_devarg(spec)
    cmd = [
        HOST_BIN, "-l", "40", "--file-prefix=mrv166h",
        "-a", devarg, "--socket-mem=0,512", "--iova-mode=va", "--",
        "--tier", spec["tier"],
        "--sdp-rx-desc", str(spec.get("sdp_rx_desc", 2048)),
        "--zero-load-ms", "300",
        "--pmu",
    ]
    if spec.get("sdp_separate_pools"):
        cmd.append("--sdp-separate-pools")
    res = sh(cmd, timeout=60)
    for line in res.stdout.splitlines():
        if line.startswith("MRV166_ZERO "):
            rec = json.loads(line[len("MRV166_ZERO "):])
            rec["part"] = "part0_zero"
            rec["cfg"] = spec["cfg"]
            rec["rep"] = rep
            rec["devarg"] = devarg
            rec["ts_utc"] = datetime.now(timezone.utc).isoformat()
            hp = rec["host_pmu"]
            ok = (hp["page_faults"] == 0 and hp["min_running_pct"] >= 99.9 and hp["ghz"] >= 3.88)
            rec["verified"] = ok
            rec["verify_errors"] = [] if ok else [f"cpu40 ghz={hp['ghz']} pf={hp['page_faults']} min_pct={hp['min_running_pct']}"]
            return rec
    raise RuntimeError(f"No MRV166_ZERO line for {spec['cfg']}:\n{res.stdout}\n{res.stderr}")


def build_specs():
    """Return (sdp_zero, direct_zero, sdp_specs, direct_specs) for one replicate."""
    sdp_zero = [
        {"cfg": "zero_sdp_default", "tier": "sdp"},
        {"cfg": "zero_sdp_sync", "tier": "sdp", "rx_poll_mem": True},
        {"cfg": "zero_sdp_async", "tier": "sdp", "rx_poll_mem": True, "rx_poll_tail_off": 16},
        {"cfg": "zero_sdp_fair", "tier": "sdp", "rx_poll_mem": True, "rx_poll_tail_off": 16,
         "ism_enable": True, "allow_small_oq": True, "sdp_rx_desc": 512, "sdp_separate_pools": True},
    ]
    direct_zero = [
        {"cfg": "zero_directpush", "tier": "directpush"},
    ]

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
            "rx_poll_tail_off": extra.get("rx_poll_tail_off", 0),
            "ism_enable": extra.get("ism_enable", False),
            "allow_small_oq": extra.get("allow_small_oq", False),
            "sdp_rx_desc": extra.get("sdp_rx_desc", 2048),
            "sdp_separate_pools": extra.get("sdp_separate_pools", False),
            "sdp_tx_batch": extra.get("sdp_tx_batch", False),
            "pace_rate_krps": extra.get("pace_rate_krps", 0.0),
            "pace_batch": extra.get("pace_batch", 1),
            "app_work": extra.get("app_work", False),
        }
        if tier == "sdp":
            sdp_specs.append(s)
        else:
            direct_specs.append(s)

    fair_kw = dict(
        sdp_zero_copy=True,
        rx_poll_mem=True,
        rx_poll_tail_off=16,
        ism_enable=True,
        allow_small_oq=True,
        sdp_rx_desc=512,
        sdp_separate_pools=True,
        sdp_tx_batch=True,
    )

    # ---- Part 1: W=1 unloaded latency across 64 B / 1 KiB / 4 KiB ----
    w1_counts = {"64": (40000, 8000), "1024": (30000, 6000), "4096": (25000, 5000)}
    for wl in ("64", "1024", "4096"):
        r, wu = w1_counts[wl]
        add("part1_w1", f"sdp_def_{wl}_w1", "sdp", wl, 1, r, wu)
        add("part1_w1", f"sdp_sync_{wl}_w1", "sdp", wl, 1, r, wu, sdp_zero_copy=True, rx_poll_mem=True)
        add("part1_w1", f"sdp_fair_{wl}_w1", "sdp", wl, 1, r, wu, **fair_kw)
        add("part1_w1", f"directpush_{wl}_w1", "directpush", wl, 1, r, wu)

    # ---- Part 2: Strict single-variable ablation ladder at 4 KiB @ 1.0 M/s (200 ms) ----
    r4, wu4 = 200000, 40000
    add("part2_4k_ablation", "sdp_def_4096_r1000", "sdp", "4096", 16, r4, wu4,
        pace_rate_krps=1000.0, app_work=True)
    add("part2_4k_ablation", "sdp_rpm_copy_4096_r1000", "sdp", "4096", 16, r4, wu4,
        rx_poll_mem=True, pace_rate_krps=1000.0, app_work=True)
    add("part2_4k_ablation", "sdp_sync_4096_r1000", "sdp", "4096", 16, r4, wu4,
        sdp_zero_copy=True, rx_poll_mem=True, pace_rate_krps=1000.0, app_work=True)
    add("part2_4k_ablation", "sdp_async_4096_r1000", "sdp", "4096", 16, r4, wu4,
        sdp_zero_copy=True, rx_poll_mem=True, rx_poll_tail_off=16, pace_rate_krps=1000.0, app_work=True)
    add("part2_4k_ablation", "sdp_async_ism_4096_r1000", "sdp", "4096", 16, r4, wu4,
        sdp_zero_copy=True, rx_poll_mem=True, rx_poll_tail_off=16, ism_enable=True,
        pace_rate_krps=1000.0, app_work=True)
    add("part2_4k_ablation", "sdp_async_ism_rx512_4096_r1000", "sdp", "4096", 16, r4, wu4,
        sdp_zero_copy=True, rx_poll_mem=True, rx_poll_tail_off=16, ism_enable=True,
        allow_small_oq=True, sdp_rx_desc=512, sdp_separate_pools=True,
        pace_rate_krps=1000.0, app_work=True)
    add("part2_4k_ablation", "sdp_fair_4096_r1000", "sdp", "4096", 16, r4, wu4,
        **fair_kw, pace_rate_krps=1000.0, app_work=True)
    add("part2_4k_ablation", "sdp_fair_b8_4096_r1000", "sdp", "4096", 16, r4, wu4,
        **fair_kw, pace_rate_krps=1000.0, pace_batch=8, app_work=True)
    add("part2_4k_ablation", "directpush_4096_r1000", "directpush", "4096", 16, r4, wu4,
        pace_rate_krps=1000.0, app_work=True)
    add("part2_4k_ablation", "directpush_b8_4096_r1000", "directpush", "4096", 16, r4, wu4,
        pace_rate_krps=1000.0, pace_batch=8, app_work=True)

    # ---- Part 3: Size matrix for 64 B @ 1.5 M/s and 1 KiB @ 1.0 M/s (200 ms each) ----
    for wl, rate, r, wu in (("64", 1500.0, 300000, 60000), ("1024", 1000.0, 200000, 40000)):
        tag = f"{wl}_r{int(rate)}"
        add("part3_size_cpu", f"sdp_def_{tag}", "sdp", wl, 16, r, wu,
            pace_rate_krps=rate, app_work=True)
        add("part3_size_cpu", f"sdp_sync_{tag}", "sdp", wl, 16, r, wu,
            sdp_zero_copy=True, rx_poll_mem=True, pace_rate_krps=rate, app_work=True)
        add("part3_size_cpu", f"sdp_async_{tag}", "sdp", wl, 16, r, wu,
            sdp_zero_copy=True, rx_poll_mem=True, rx_poll_tail_off=16, pace_rate_krps=rate, app_work=True)
        add("part3_size_cpu", f"sdp_fair_{tag}", "sdp", wl, 16, r, wu,
            **fair_kw, pace_rate_krps=rate, app_work=True)
        add("part3_size_cpu", f"sdp_fair_b8_{tag}", "sdp", wl, 16, r, wu,
            **fair_kw, pace_rate_krps=rate, pace_batch=8, app_work=True)
        add("part3_size_cpu", f"directpush_{tag}", "directpush", wl, 16, r, wu,
            pace_rate_krps=rate, app_work=True)
        add("part3_size_cpu", f"directpush_b8_{tag}", "directpush", wl, 16, r, wu,
            pace_rate_krps=rate, pace_batch=8, app_work=True)

    return sdp_zero, direct_zero, sdp_specs, direct_specs


def run_one(spec, rep, sdp_mac=None):
    devarg = build_devarg(spec)
    cmd = [
        HOST_BIN, "-l", "40", "--file-prefix=mrv166h",
        "-a", devarg, "--socket-mem=0,512", "--iova-mode=va", "--",
        "--tier", spec["tier"],
        "--workload", spec["workload"],
        "-w", str(spec["window"]),
        "-n", str(spec["rounds"]),
        "--warmup", str(spec["warmup"]),
        "--dma-threshold", str(spec["dma_threshold"]),
        "--sdp-rx-desc", str(spec["sdp_rx_desc"]),
        "--pace-batch", str(spec["pace_batch"]),
        "--pmu",
    ]
    if spec["tier"] == "sdp":
        cmd += ["--sdp-peer", sdp_mac]
    if spec["sdp_zero_copy"]:
        cmd.append("--sdp-zero-copy")
    if spec["sdp_separate_pools"]:
        cmd.append("--sdp-separate-pools")
    if spec["sdp_tx_batch"]:
        cmd.append("--sdp-tx-batch")
    if spec["pace_rate_krps"] > 0.0:
        cmd += ["--pace-rate-krps", f"{spec['pace_rate_krps']:.3f}"]
    if spec["app_work"]:
        cmd.append("--app-work")

    res = sh(cmd, timeout=120)
    payload = None
    for line in res.stdout.splitlines():
        if line.startswith("MRV166 "):
            payload = json.loads(line[len("MRV166 "):])
            break
    if payload is None:
        raise RuntimeError(f"No MRV166 output for {spec['cfg']} rep={rep}:\n{res.stdout}\n{res.stderr}")

    payload["part"] = spec["part"]
    payload["cfg"] = spec["cfg"]
    payload["rep"] = rep
    payload["devarg"] = devarg
    payload["rx_poll_mem"] = spec["rx_poll_mem"]
    payload["rx_poll_tail_off"] = spec["rx_poll_tail_off"]
    payload["ism_enable"] = spec["ism_enable"]
    payload["allow_small_oq"] = spec["allow_small_oq"]
    payload["ts_utc"] = datetime.now(timezone.utc).isoformat()

    errs = []
    if payload["lost"] != 0 or payload["bad"] != 0 or payload["torn"] != 0:
        errs.append(f"integrity lost={payload['lost']} bad={payload['bad']} torn={payload['torn']}")
    if payload["measured"] != spec["rounds"]:
        errs.append(f"measured={payload['measured']} != rounds={spec['rounds']}")
    if payload["pool_page_kb"] != 2048 or payload["pool_numa_n0"] != 0 or payload["pool_numa_n1"] != 64:
        errs.append(f"pool page_kb={payload['pool_page_kb']} n0={payload['pool_numa_n0']} n1={payload['pool_numa_n1']}")
    hp = payload["host_pmu"]
    if hp["page_faults"] != 0:
        errs.append(f"page_faults={hp['page_faults']}")
    if hp["min_running_pct"] < 99.9:
        errs.append(f"pmu mux min_running_pct={hp['min_running_pct']}")
    if hp["ghz"] < 3.88:
        errs.append(f"cpu40 ghz={hp['ghz']} < 3.88")
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
    if spec["rx_poll_tail_off"] > 0 and payload["late"] != 0:
        errs.append(f"async-tail had non-zero late={payload['late']}")
    payload["verified"] = len(errs) == 0
    payload["verify_errors"] = errs
    return payload


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if RUNS_PATH.exists():
        RUNS_PATH.unlink()
    if ATTEMPTS_PATH.exists():
        ATTEMPTS_PATH.unlink()

    host_before = check_host_env()
    card_before = check_card_env()
    sdp_zero, direct_zero, sdp_specs, direct_specs = build_specs()
    per_rep = 1 + len(sdp_zero) + len(direct_zero) + len(sdp_specs) + len(direct_specs)
    total_runs = REPS * per_rep
    print(
        f"MRV-166 starting: {REPS} interleaved reps x {per_rep} runs = {total_runs} runs",
        flush=True,
    )

    run_id = 0
    t_start = time.time()
    cal_idle_rates = []

    try:
        with RUNS_PATH.open("w") as f:
            for rep in range(REPS):
                run_id += 1
                cal = None
                for attempt in range(4):
                    cand = run_calibration(rep)
                    if cand["verified"]:
                        cal = cand
                        break
                    cand["attempt"] = attempt + 1
                    with ATTEMPTS_PATH.open("a") as af:
                        af.write(json.dumps(cand) + "\n")
                    if "ghz=" in " ".join(cand["verify_errors"]) and attempt < 3:
                        print(f"  [retry {attempt+1}] CAL: {cand['verify_errors']}", flush=True)
                        time.sleep(0.5)
                    else:
                        raise RuntimeError(f"Calibration failed: {cand['verify_errors']}")
                cal["run_id"] = run_id
                cal_idle_rates.append(cal["units_per_s"])
                f.write(json.dumps(cal) + "\n")
                f.flush()
                idle_r = cal["units_per_s"]
                print(
                    f"[{run_id:3d}/{total_runs}] rep={rep} CAL idle={idle_r/1e6:.3f} M/s "
                    f"({cal['ns_per_unit']:.1f} ns/u) ghz={cal['host_pmu']['ghz']:.3f}",
                    flush=True,
                )

                order = ("sdp", "direct") if (rep % 2 == 0) else ("direct", "sdp")
                for mode in order:
                    if mode == "sdp":
                        sdp_mac = start_card_sdp()
                        for zs in sdp_zero:
                            run_id += 1
                            zrec = None
                            for attempt in range(3):
                                cand = run_zero_load(zs, rep)
                                if cand["verified"]:
                                    zrec = cand
                                    break
                                cand["attempt"] = attempt + 1
                                with ATTEMPTS_PATH.open("a") as af:
                                    af.write(json.dumps(cand) + "\n")
                                if "ghz=" in " ".join(cand["verify_errors"]) and attempt < 2:
                                    print(f"  [retry {attempt+1}] {zs['cfg']}: {cand['verify_errors']}", flush=True)
                                    time.sleep(0.5)
                                else:
                                    raise RuntimeError(f"Zero-load failed {zs['cfg']}: {cand['verify_errors']}")
                            zrec["run_id"] = run_id
                            f.write(json.dumps(zrec) + "\n")
                            f.flush()
                            zpct = (1.0 - zrec["app_work_per_s"] / idle_r) * 100.0
                            print(
                                f"[{run_id:3d}/{total_runs}] rep={rep} {zs['cfg']:30s} | zero_net_cpu={zpct:5.2f}% "
                                f"ghz={zrec['host_pmu']['ghz']:.3f}",
                                flush=True,
                            )
                        specs = sdp_specs
                    else:
                        sdp_mac = None
                        start_card_direct()
                        for zs in direct_zero:
                            run_id += 1
                            zrec = None
                            for attempt in range(3):
                                cand = run_zero_load(zs, rep)
                                if cand["verified"]:
                                    zrec = cand
                                    break
                                cand["attempt"] = attempt + 1
                                with ATTEMPTS_PATH.open("a") as af:
                                    af.write(json.dumps(cand) + "\n")
                                if "ghz=" in " ".join(cand["verify_errors"]) and attempt < 2:
                                    print(f"  [retry {attempt+1}] {zs['cfg']}: {cand['verify_errors']}", flush=True)
                                    time.sleep(0.5)
                                else:
                                    raise RuntimeError(f"Zero-load failed {zs['cfg']}: {cand['verify_errors']}")
                            zrec["run_id"] = run_id
                            f.write(json.dumps(zrec) + "\n")
                            f.flush()
                            zpct = (1.0 - zrec["app_work_per_s"] / idle_r) * 100.0
                            print(
                                f"[{run_id:3d}/{total_runs}] rep={rep} {zs['cfg']:30s} | zero_net_cpu={zpct:5.2f}% "
                                f"ghz={zrec['host_pmu']['ghz']:.3f}",
                                flush=True,
                            )
                        specs = direct_specs

                    for spec in specs:
                        run_id += 1
                        rec = None
                        for attempt in range(3):
                            cand = run_one(spec, rep, sdp_mac=sdp_mac)
                            if cand["verified"]:
                                rec = cand
                                break
                            cand["attempt"] = attempt + 1
                            with ATTEMPTS_PATH.open("a") as af:
                                af.write(json.dumps(cand) + "\n")
                            if all("cpu40 ghz=" in e for e in cand["verify_errors"]) and attempt < 2:
                                print(f"  [retry {attempt+1}] {spec['cfg']}: {cand['verify_errors']}", flush=True)
                                time.sleep(0.5)
                            else:
                                raise RuntimeError(f"Verification failed on {spec['cfg']} rep={rep}: {cand['verify_errors']}")
                        rec["run_id"] = run_id
                        f.write(json.dumps(rec) + "\n")
                        f.flush()
                        hp = rec["host_pmu"]
                        lat = rec["lat_all_us"]
                        extra = ""
                        if spec["app_work"]:
                            net_pct = (1.0 - rec["app_work_per_s"] / idle_r) * 100.0
                            cyc_r = (net_pct / 100.0) * hp["cycles"] / rec["measured"]
                            extra = (
                                f" | net={net_pct:5.1f}% ({cyc_r:6.0f} cyc/r) "
                                f"late={rec['late_pct']:4.1f}% ddio={hp['ddio_hit_pct']:5.1f}%"
                            )
                        print(
                            f"[{run_id:3d}/{total_runs}] rep={rep} {spec['cfg']:30s} | "
                            f"{rec['mreq_s']:5.3f} M/s | p50={lat['p50']:5.2f} p99={lat['p99']:5.2f}us{extra}",
                            flush=True,
                        )
    finally:
        stop_card_server()

    host_after = check_host_env()
    card_after = check_card_env()
    elapsed = time.time() - t_start
    env_doc = {
        "issue": "MRV-166",
        "date": "2026-11-23",
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
        f"MRV-166 COMPLETE: {run_id} verified runs in {elapsed:.1f}s | EDAC ue delta = {env_doc['edac_ue_delta']}",
        flush=True,
    )
    assert env_doc["edac_ue_delta"] == 0, f"EDAC ue_count increased: {env_doc['edac_ue_delta']}"


if __name__ == "__main__":
    main()
