#!/usr/bin/env python3
"""MRV-118 full interleaved experiment harness (Real CN106 NIX0+LBK0 Hardware Loopback).

Pure commercial PCIe NIC only (ZERO Intel DSA). Single-variable comparison (MRV-82)
on identical hardware (cpu40 3.90 GHz, Socket 1 uncore 2.20 GHz, CN106 2.00 GHz,
intel_iommu=on,sm_on), identical 128 MiB Socket-1 2 MiB-hugepage retained TX pool
(page_faults=0), MAX_DRAIN=32, and strictly symmetric 1 Host core (cpu40) +
1 Card core (core 3) accounting:
  - Card NIX hardware loopback device: 0002:03:00.0 (RVU NIC PF2 / eth1) via
    devlink eswitch switchdev on pci/0002:01:00.0 -> NIX0 TX -> NPC TX MCAM ->
    Hardware LBK0 (ch 63) -> NPC RX MCAM -> NPA -> NIX0 RX (100% isolated from
    control path sdp1-0 / 0002:01:00.1).
  - Interfaces compared (ONLY variable changed):
      (a) sdp        : Traditional SDP descriptor ring (MTU 9000) + controls
                       (rx_poll_mem=1, sdp_zero_copy)
      (b) directpush : DirectPush (<=256 B MOVDIR64B+WC + >=1 KiB native CN106 DPI DMA)
      (c) No-NIX ref : Identical mrv118_card binary without --nix-dev for clean
                       latency stage decomposition & CPU-savings invariance check.

Parts (3 fully interleaved replicates alternating SDP <-> DirectPush & NIX <-> No-NIX):
  - part0_cal      : Pure idle app_work_step() calibration on cpu40 (3 runs)
  - part1_decomp   : Unloaded W=1 stage breakdown (64 / 256 / 4096 B, No-NIX vs NIX0+LBK0)
  - part2_closed   : Closed-loop loaded curves through NIX0+LBK0 (64, 4096, mix_ads, mix_geo)
  - part3_openloop : Realistic Poisson & microburst (B=8) open-loop arrival through NIX0+LBK0
                     (true tot = t_recv - t_sched, verifying zero 13 M/s cliff)
  - part4_cpu      : Matched-throughput host CPU headroom (--app-work + 100% PMU)
                     through NIX0+LBK0 (+ No-NIX invariance control)
"""

import json
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv118/logs/mrv118-2026-11-20")
RUNS_PATH = OUT_DIR / "runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"
HOST_BIN = "/home/hongtao/dev/marvell-mrv118/testbed/target/release/mrv118_host"
CARD_BIN = "/root/bench/mrv118_card"
NIX_DEV = "0002:03:00.0"
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
        "echo dpi_vfs=$(dpdk-devbind.py -s | grep -c '0000:06:00\\.' || true);"
        f"echo nix_drv=$(basename $(readlink -f /sys/bus/pci/devices/{NIX_DEV}/driver) 2>/dev/null || echo none);"
        "echo eswitch=$(devlink dev eswitch show pci/0002:01:00.0 | awk '{print $NF}');"
        "echo edac_ue=$(cat /sys/devices/system/edac/mc/mc0/ue_count 2>/dev/null || echo 0)"
    ).stdout
    info = {}
    for line in out.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k] = int(v) if v.isdigit() else v
    return info


def stop_card_server():
    ssh(
        "tmux send-keys -t m118 C-c 2>/dev/null || true; "
        "tmux send-keys -t refl C-c 2>/dev/null || true; "
        "sleep 0.8; "
        "tmux kill-session -t m118 2>/dev/null || true; "
        "tmux kill-session -t refl 2>/dev/null || true; "
        "sleep 0.3"
    )


def ensure_nix_vfio():
    ssh(
        f'drv=$(basename $(readlink -f /sys/bus/pci/devices/{NIX_DEV}/driver) 2>/dev/null || echo none); '
        f'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci {NIX_DEV}; fi'
    )


def enable_nix_switchdev():
    out = ssh(
        "devlink dev eswitch set pci/0002:01:00.0 mode legacy; "
        "devlink dev eswitch set pci/0002:01:00.0 mode switchdev; "
        "cat /sys/kernel/debug/cn10k/npc/mcam_rules | grep -A6 '40:60:5a:02:ed:69'"
    ).stdout
    if "Unicast to channel 63" not in out or "channel_mask: 0x0" not in out:
        raise RuntimeError(f"NIX0 switchdev MCAM rules missing for PF2:\n{out}")


def start_card_server(mode, nix_enabled):
    stop_card_server()
    if nix_enabled:
        ensure_nix_vfio()
        nix_eal = f"-a {NIX_DEV}"
        nix_arg = f"--nix-dev {NIX_DEV}"
    else:
        nix_eal = ""
        nix_arg = ""

    if mode == "sdp":
        ready_tok = "sdp ready"
        cmd = f"{CARD_BIN} -l 3 -a 0002:01:00.2 {nix_eal} --file-prefix=m118 -- --mode sdp {nix_arg}"
        prep = ""
    else:
        ready_tok = "direct ready"
        cmd = (
            f"{CARD_BIN} -l 3 -a 0002:01:00.2 {nix_eal} -a 0000:06:00.1 -a 0000:06:00.2 "
            f"--file-prefix=m118 -- --mode direct {nix_arg}"
        )
        prep = "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "

    ssh(
        f'{prep}'
        f'tmux new-session -d -s m118 "{cmd}"; '
        f'for i in $(seq 1 40); do '
        f'  if tmux capture-pane -pt m118 | grep -q "{ready_tok}"; then break; fi; '
        f'  sleep 0.2; '
        f'done; '
        f'tmux capture-pane -pt m118 | grep "{ready_tok}"'
    )
    if nix_enabled:
        enable_nix_switchdev()

    if mode == "sdp":
        pane = ssh("tmux capture-pane -pt m118").stdout
        m = re.search(r"mac=([0-9a-f:]{17})", pane)
        if not m:
            raise RuntimeError(f"Failed to detect card SDP MAC:\n{pane}")
        return m.group(1)
    return None


def run_calibration(rep):
    cmd = [
        HOST_BIN, "-l", "40", "--file-prefix=mrv118h",
        "-a", "0000:99:02.0", "--socket-mem=0,512", "--iova-mode=va", "--",
        "--calibrate-app-work-ms", "500", "--pmu",
    ]
    res = sh(cmd, timeout=60)
    for line in res.stdout.splitlines():
        if line.startswith("MRV118_CAL "):
            rec = json.loads(line[len("MRV118_CAL "):])
            rec["part"] = "part0_cal"
            rec["cfg"] = "app_work_idle_cal"
            rec["rep"] = rep
            rec["nix_enabled"] = False
            rec["ts_utc"] = datetime.now(timezone.utc).isoformat()
            hp = rec["host_pmu"]
            ok = (hp["page_faults"] == 0 and hp["min_running_pct"] >= 99.9 and hp["ghz"] >= 3.85)
            rec["verified"] = ok
            rec["verify_errors"] = [] if ok else [f"pmu={hp}"]
            return rec
    raise RuntimeError(f"No MRV118_CAL line:\n{res.stdout}\n{res.stderr}")


def run_one(spec, rep, sdp_mac):
    dev_arg = "0000:99:02.0,rx_poll_mem=1" if spec["rx_poll_mem"] else "0000:99:02.0"
    seed = 0x1180_2026_1120 + rep * 100003 + (hash(spec["cfg"]) & 0xFFFF)
    cmd = [
        HOST_BIN, "-l", "40", "--file-prefix=mrv118h",
        "-a", dev_arg, "--socket-mem=0,512", "--iova-mode=va", "--",
        "--tier", spec["tier"],
        "--workload", spec["workload"],
        "--arrival", spec["arrival"],
        "-w", str(spec["window"]),
        "-n", str(spec["rounds"]),
        "--warmup", str(spec["warmup"]),
        "--dma-threshold", str(spec["dma_threshold"]),
        "--burst-size", str(spec["burst_size"]),
        "--seed", str(seed),
        "--pmu",
    ]
    if spec["tier"] == "sdp":
        cmd += ["--sdp-peer", sdp_mac]
        if spec["sdp_zero_copy"]:
            cmd += ["--sdp-zero-copy"]
    if spec["pace_rate_krps"] > 0.0:
        cmd += ["--pace-rate-krps", f"{spec['pace_rate_krps']:.3f}"]
    if spec["app_work"]:
        cmd += ["--app-work"]

    res = sh(cmd, timeout=120)
    rec = None
    for line in res.stdout.splitlines():
        if line.startswith("MRV118 "):
            rec = json.loads(line[len("MRV118 "):])
            break
    if rec is None:
        raise RuntimeError(f"No MRV118 JSON line for {spec['cfg']}:\n{res.stdout}\n{res.stderr}")

    rec["part"] = spec["part"]
    rec["cfg"] = spec["cfg"]
    rec["rep"] = rep
    rec["nix_enabled"] = spec["nix_enabled"]
    rec["rx_poll_mem"] = spec["rx_poll_mem"]
    rec["ts_utc"] = datetime.now(timezone.utc).isoformat()

    # Strict hardware + integrity verification gate
    errs = []
    if rec["lost"] != 0 or rec["bad"] != 0 or rec["torn"] != 0:
        errs.append(f"integrity lost={rec['lost']} bad={rec['bad']} torn={rec['torn']}")
    if rec["measured"] != spec["rounds"]:
        errs.append(f"measured={rec['measured']} != rounds={spec['rounds']}")
    if rec["pool_page_kb"] != 2048 or rec["pool_numa_n0"] != 0 or rec["pool_numa_n1"] != 64:
        errs.append(f"pool numa mismatch: {rec['pool_page_kb']}/{rec['pool_numa_n0']}/{rec['pool_numa_n1']}")
    hp = rec["host_pmu"]
    if hp["page_faults"] != 0:
        errs.append(f"page_faults={hp['page_faults']}")
    if hp["min_running_pct"] < 99.9:
        errs.append(f"pmu min_running_pct={hp['min_running_pct']}")
    if hp["mesh_ghz"] < 2.15:
        errs.append(f"mesh_ghz={hp['mesh_ghz']}")
    cs = rec["card_stats"]
    if cs["corrupt"] != 0 or cs["dma_failed"] != 0 or cs["nix_drops"] != 0:
        errs.append(f"card errs corrupt={cs['corrupt']} dma_failed={cs['dma_failed']} nix_drops={cs['nix_drops']}")
    if spec["nix_enabled"]:
        expected_pkts = spec["warmup"] + spec["rounds"]
        expected_bytes = rec["bytes_total_all"]
        ns = cs["nix_stats"]
        xs = cs["nix_xstats"]
        if not cs["nix_enabled"] or ns is None or xs is None:
            errs.append("nix_stats missing while nix_enabled=True")
        else:
            if ns["opackets"] != expected_pkts or ns["ipackets"] != expected_pkts:
                errs.append(f"nix_stats pkts={ns['opackets']}/{ns['ipackets']} != {expected_pkts}")
            if ns["obytes"] != expected_bytes or ns["ibytes"] != expected_bytes:
                errs.append(f"nix_stats bytes={ns['obytes']}/{ns['ibytes']} != {expected_bytes}")
            if ns["ierrors"] != 0 or ns["oerrors"] != 0 or ns["imissed"] != 0 or ns["rx_nombuf"] != 0:
                errs.append(f"nix_stats errors={ns}")
            if xs["tx_ucast"] != expected_pkts or xs["rx_ucast"] != expected_pkts:
                errs.append(f"nix_xstats ucast={xs['tx_ucast']}/{xs['rx_ucast']} != {expected_pkts}")
            if xs["tx_drop"] != 0 or xs["rx_drop"] != 0 or xs["rx_err"] != 0:
                errs.append(f"nix_xstats drops/err={xs}")
            if cs["nix_hw_all_us"] is None or cs["nix_hw_all_us"]["count"] != expected_pkts:
                errs.append(f"nix_hw_all_us count mismatch")

    rec["verified"] = len(errs) == 0
    rec["verify_errors"] = errs
    if errs:
        raise RuntimeError(f"VERIFICATION FAILED for {spec['cfg']} rep={rep}: {errs}")
    return rec


def build_groups():
    """Return dict of 4 card-server groups: (mode, nix_enabled) -> list[spec]."""
    groups = {
        ("sdp", False): [],
        ("direct", False): [],
        ("sdp", True): [],
        ("direct", True): [],
    }

    def add(part, cfg, tier, nix, wl, arrival, w, rounds, warmup, **extra):
        mode = "sdp" if tier == "sdp" else "direct"
        groups[(mode, nix)].append({
            "part": part,
            "cfg": cfg,
            "tier": tier,
            "nix_enabled": nix,
            "workload": wl,
            "arrival": arrival,
            "window": w,
            "rounds": rounds,
            "warmup": warmup,
            "dma_threshold": extra.get("dma_threshold", 1024),
            "sdp_zero_copy": extra.get("sdp_zero_copy", False),
            "rx_poll_mem": extra.get("rx_poll_mem", False),
            "pace_rate_krps": extra.get("pace_rate_krps", 0.0),
            "burst_size": extra.get("burst_size", 8),
            "app_work": extra.get("app_work", False),
        })

    # ---- Part 1: Unloaded W=1 Stage Decomposition (64, 256, 4096 B; No-NIX vs Real NIX0+LBK0) ----
    w1_r_nonix = {"64": (120000, 40000), "256": (100000, 35000), "4096": (40000, 15000)}
    w1_r_nix = {"64": (30000, 10000), "256": (30000, 10000), "4096": (20000, 6000)}
    for wl in ("64", "256", "4096"):
        r0, wu0 = w1_r_nonix[wl]
        r1, wu1 = w1_r_nix[wl]
        add("part1_decomp", f"sdp_nonix_{wl}_w1", "sdp", False, wl, "closed", 1, min(r0, 40000), min(wu0, 12000))
        add("part1_decomp", f"directpush_nonix_{wl}_w1", "directpush", False, wl, "closed", 1, r0, wu0)
        add("part1_decomp", f"sdp_nix_{wl}_w1", "sdp", True, wl, "closed", 1, r1, wu1)
        add("part1_decomp", f"directpush_nix_{wl}_w1", "directpush", True, wl, "closed", 1, r1, wu1)
    # SDP rx_poll_mem=1 control on NIX0+LBK0
    add("part1_decomp_ctrl", "sdp_rpm_nix_64_w1", "sdp", True, "64", "closed", 1, 30000, 10000, rx_poll_mem=True)
    add("part1_decomp_ctrl", "sdp_rpm_nix_4096_w1", "sdp", True, "4096", "closed", 1, 20000, 6000, rx_poll_mem=True)

    # ---- Part 2: Closed-Loop Loaded Latency-Throughput Curves through NIX0+LBK0 ----
    # 64 B: W=8, 16, 32 (+ W=64 for DirectPush peak / slow-state check)
    for w, rs, rd in [(8, (180000, 40000), (250000, 50000)), (16, (250000, 50000), (400000, 60000)), (32, (350000, 60000), (500000, 80000))]:
        add("part2_closed", f"sdp_nix_64_w{w}", "sdp", True, "64", "closed", w, rs[0], rs[1])
        add("part2_closed", f"directpush_nix_64_w{w}", "directpush", True, "64", "closed", w, rd[0], rd[1])
    add("part2_closed", "sdp_nix_64_w64", "sdp", True, "64", "closed", 64, 450000, 80000)
    add("part2_closed", "directpush_nix_64_w64", "directpush", True, "64", "closed", 64, 600000, 100000)

    # 4096 B: W=8, 16, 32 (+ SDP zero-copy control at W=16)
    for w, r, wu in [(8, 100000, 20000), (16, 150000, 30000), (32, 180000, 30000)]:
        add("part2_closed", f"sdp_nix_4096_w{w}", "sdp", True, "4096", "closed", w, r, wu)
        add("part2_closed", f"directpush_nix_4096_w{w}", "directpush", True, "4096", "closed", w, r, wu)
    add("part2_closed_ctrl", "sdp_zc_nix_4096_w16", "sdp", True, "4096", "closed", 16, 150000, 30000, sdp_zero_copy=True)

    # Mix-Ads (90/10) & Mix-Geo (50/50): W=1, 8, 16, 32
    for wl, w_cfg in [
        ("mix_ads", [(1, 25000, 5000), (8, 180000, 35000), (16, 280000, 50000), (32, 400000, 60000)]),
        ("mix_geo", [(1, 20000, 4000), (8, 120000, 25000), (16, 180000, 35000), (32, 220000, 40000)]),
    ]:
        for w, r, wu in w_cfg:
            add("part2_closed", f"sdp_nix_{wl}_w{w}", "sdp", True, wl, "closed", w, r, wu)
            add("part2_closed", f"directpush_nix_{wl}_w{w}", "directpush", True, wl, "closed", w, r, wu)

    # ---- Part 3: Realistic Open-Loop Poisson & Microburst (B=8) through NIX0+LBK0 ----
    # 64 B Poisson
    for rate in (500, 1000, 1500, 2000, 2500, 3000):
        r = max(100000, int(rate * 150))
        wu = max(25000, int(rate * 35))
        add("part3_openloop", f"sdp_nix_64_poisson_r{rate}", "sdp", True, "64", "poisson", 64, r, wu, pace_rate_krps=rate)
        add("part3_openloop", f"directpush_nix_64_poisson_r{rate}", "directpush", True, "64", "poisson", 64, r, wu, pace_rate_krps=rate)
    for rate in (3500, 4000, 4500, 5000):
        r = int(rate * 120)
        wu = int(rate * 30)
        add("part3_openloop", f"sdp_nix_64_poisson_r{rate}", "sdp", True, "64", "poisson", 64, r, wu, pace_rate_krps=rate)
    for rate in (4000, 5000, 6000):
        r = int(rate * 120)
        wu = int(rate * 30)
        add("part3_openloop", f"directpush_nix_64_poisson_r{rate}", "directpush", True, "64", "poisson", 64, r, wu, pace_rate_krps=rate)

    # 64 B Bursty (B=8)
    for rate in (1000, 2000):
        r = int(rate * 150)
        wu = int(rate * 35)
        add("part3_openloop", f"sdp_nix_64_burst8_r{rate}", "sdp", True, "64", "bursty", 64, r, wu, pace_rate_krps=rate, burst_size=8)
        add("part3_openloop", f"directpush_nix_64_burst8_r{rate}", "directpush", True, "64", "bursty", 64, r, wu, pace_rate_krps=rate, burst_size=8)
    for rate in (3000, 4000):
        r = int(rate * 120)
        wu = int(rate * 30)
        add("part3_openloop", f"directpush_nix_64_burst8_r{rate}", "directpush", True, "64", "bursty", 64, r, wu, pace_rate_krps=rate, burst_size=8)

    # Mix-Ads Poisson
    for rate in (500, 1000, 1500, 2000, 2500):
        r = max(100000, int(rate * 150))
        wu = max(25000, int(rate * 35))
        add("part3_openloop", f"sdp_nix_mix_ads_poisson_r{rate}", "sdp", True, "mix_ads", "poisson", 64, r, wu, pace_rate_krps=rate)
        add("part3_openloop", f"directpush_nix_mix_ads_poisson_r{rate}", "directpush", True, "mix_ads", "poisson", 64, r, wu, pace_rate_krps=rate)
    add("part3_openloop", "directpush_nix_mix_ads_poisson_r3500", "directpush", True, "mix_ads", "poisson", 64, 420000, 80000, pace_rate_krps=3500)

    # Mix-Ads Bursty (B=8)
    for rate in (1000, 1500):
        r = int(rate * 150)
        wu = int(rate * 35)
        add("part3_openloop", f"sdp_nix_mix_ads_burst8_r{rate}", "sdp", True, "mix_ads", "bursty", 64, r, wu, pace_rate_krps=rate, burst_size=8)
        add("part3_openloop", f"directpush_nix_mix_ads_burst8_r{rate}", "directpush", True, "mix_ads", "bursty", 64, r, wu, pace_rate_krps=rate, burst_size=8)
    add("part3_openloop", "directpush_nix_mix_ads_burst8_r2500", "directpush", True, "mix_ads", "bursty", 64, 350000, 70000, pace_rate_krps=2500, burst_size=8)

    # ---- Part 4: Matched-Throughput Host CPU Core Savings (--app-work + 100% PMU) ----
    # Real NIX0+LBK0 matched rates
    cpu_cases = [
        ("64", 1500, 300000, 50000, True, False),
        ("4096", 800, 160000, 30000, False, True),
        ("mix_ads", 1500, 300000, 50000, True, False),
        ("mix_geo", 800, 160000, 30000, False, True),
    ]
    for wl, rate, r, wu, add_rpm, add_zc in cpu_cases:
        add("part4_cpu", f"sdp_nix_{wl}_r{rate}", "sdp", True, wl, "cbr", 64, r, wu, pace_rate_krps=rate, app_work=True)
        add("part4_cpu", f"directpush_nix_{wl}_r{rate}", "directpush", True, wl, "cbr", 64, r, wu, pace_rate_krps=rate, app_work=True)
        if add_rpm:
            add("part4_cpu_ctrl", f"sdp_rpm_nix_{wl}_r{rate}", "sdp", True, wl, "cbr", 64, r, wu, pace_rate_krps=rate, app_work=True, rx_poll_mem=True)
        if add_zc:
            add("part4_cpu_ctrl", f"sdp_zc_nix_{wl}_r{rate}", "sdp", True, wl, "cbr", 64, r, wu, pace_rate_krps=rate, app_work=True, sdp_zero_copy=True)

    # No-NIX invariance control for Host CPU core savings
    add("part4_cpu_nonix", "sdp_nonix_mix_ads_r1500", "sdp", False, "mix_ads", "cbr", 64, 300000, 50000, pace_rate_krps=1500, app_work=True)
    add("part4_cpu_nonix", "directpush_nonix_mix_ads_r1500", "directpush", False, "mix_ads", "cbr", 64, 300000, 50000, pace_rate_krps=1500, app_work=True)

    return groups


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    host_before = check_host_env()
    card_before = check_card_env()
    groups = build_groups()
    per_rep = sum(len(v) for v in groups.values()) + 1
    total_planned = per_rep * REPS
    print(f"MRV-118 starting: {REPS} interleaved reps x {per_rep} runs = {total_planned} total runs", flush=True)

    # Interleaved group orders per replicate to cancel temporal/thermal drift
    orders = {
        1: [("sdp", False), ("direct", False), ("sdp", True), ("direct", True)],
        2: [("direct", True), ("sdp", True), ("direct", False), ("sdp", False)],
        3: [("sdp", True), ("direct", True), ("sdp", False), ("direct", False)],
    }

    resume = "--resume" in sys.argv
    done_keys = set()
    if resume and RUNS_PATH.exists():
        for line in RUNS_PATH.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("verified"):
                done_keys.add((r["rep"], r["cfg"]))
        print(f"Resuming: {len(done_keys)} verified runs already in {RUNS_PATH}", flush=True)
    elif RUNS_PATH.exists():
        RUNS_PATH.unlink()

    t_start = time.time()
    completed = len(done_keys)
    open_mode = "a" if resume else "w"

    with RUNS_PATH.open(open_mode) as out:
        for rep in range(1, REPS + 1):
            if (rep, "app_work_idle_cal") not in done_keys:
                rec = run_calibration(rep)
                out.write(json.dumps(rec) + "\n")
                out.flush()
                completed += 1
                print(
                    f"[{completed:3d}/{total_planned}] rep={rep} CAL idle={rec['units_per_s']/1e3:.1f}k/s "
                    f"({rec['ns_per_unit']:.1f}ns/u) ghz={rec['host_pmu']['ghz']:.3f}",
                    flush=True,
                )

            for mode, nix_en in orders[rep]:
                specs = [s for s in groups[(mode, nix_en)] if (rep, s["cfg"]) not in done_keys]
                if not specs:
                    continue
                tag = f"{mode}_{'nix' if nix_en else 'nonix'}"
                print(f"--- rep={rep} switching card -> {tag} ({len(specs)} runs) ---", flush=True)
                sdp_mac = start_card_server(mode, nix_en)
                for spec in specs:
                    for attempt in (1, 2):
                        try:
                            rec = run_one(spec, rep, sdp_mac)
                            break
                        except RuntimeError as e:
                            if attempt == 1 and "page_faults=" in str(e):
                                print(f"  [retry {spec['cfg']} rep={rep} after transient {e}]", flush=True)
                                continue
                            raise
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    completed += 1
                    nix_str = ""
                    if nix_en:
                        nh = rec["card_stats"]["nix_hw_all_us"]["p50"]
                        npkts = rec["card_stats"]["nix_stats"]["ipackets"]
                        nix_str = f" nix_hw={nh:.2f}us pkts={npkts}"
                    aw_str = f" app={rec['app_work_per_s']/1e3:.0f}k/s" if spec["app_work"] else ""
                    print(
                        f"[{completed:3d}/{total_planned}] rep={rep} {spec['cfg']:36s} "
                        f"mreq={rec['mreq_s']:5.3f} p50={rec['lat_all_us']['p50']:6.3f} "
                        f"p99={rec['lat_all_us']['p99']:6.3f}{nix_str}{aw_str} ghz={rec['host_pmu']['ghz']:.3f}",
                        flush=True,
                    )
    elapsed = time.time() - t_start
    host_after = check_host_env()
    card_after = check_card_env()
    env = {
        "issue": "MRV-118",
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(elapsed, 2),
        "total_runs": completed,
        "reps": REPS,
        "nix_dev": NIX_DEV,
        "nix_path": "NIX0 SQ -> TM -> NPC TX MCAM -> HW LBK0 (ch63) -> NPC RX MCAM -> NPA -> NIX0 RQ/CQ",
        "host_bin": HOST_BIN,
        "card_bin": CARD_BIN,
        "host_before": host_before,
        "host_after": host_after,
        "card_before": card_before,
        "card_after": card_after,
    }
    ENV_PATH.write_text(json.dumps(env, indent=2) + "\n")
    print(f"ALL DONE: {completed}/{total_planned} verified runs in {elapsed:.1f}s -> {RUNS_PATH}", flush=True)


if __name__ == "__main__":
    main()
