#!/usr/bin/env python3
"""MRV-210 (MRV-203 E4) full n=3 interleaved multi-core (1-8 host cores) benchmark harness.

Compares four receive-ring architectures over the identical standard DPDK `octeon_ep`
VF0 (`0000:99:02.0`, up to 8 queues) data plane under real 100GbE traffic
(`mercury -> CN106 eth0 -> CN106 sdp1-1 -> saturn VF0`, 256 B & 1500 B frames)
with a concurrent Socket-1 32 MiB cache-sensitive KV neighbor (`cpu48`):

  1. `priv-ring`  : Per-core large private SDP ring (`rx_desc=2048`, 8c ~ 32 MiB >> 8 MiB DDIO).
  2. `sh-ring`    : ShRing-style (OSDI'23) compact shared host `RxArr` (`R_sh=1024`) + return spinlock.
  3. `rx-bisect`  : rxBisect-style (OSDI'25) lock-free small hot `Ax` LIFO pool + large per-core
                    host delivery ring `Bx` (`bx_cap=8192`), accumulating stalled backlog in host RAM.
  4. `credit-sdp` : Our design — per-core compact SDP hot ring (`rx_desc=512`, `R_eff=192`,
                    dedicated compact hot pool `640` mbufs/q) + exclusive 64 B NO-SFENCE `MOVDIR64B`
                    `BAR4` credit lines gating per-queue CN106 on-path elastic FIFOs (L3 + DDR5).

Three E4 multi-core workloads (strictly single-variable, n=3 interleaved with arm rotation):
  - Part 0 (kv_solo)        : Unloaded 32 MiB KV neighbor baseline on Socket 1 `cpu48`.
  - Part 1 (uniform_scale)  : Uniform 100GbE scaling across `1 -> 2 -> 4 -> 8` host cores (256 B & 1500 B).
  - Part 2 (rss_skew)       : RSS skew (`ratio4to1` & `zipf` alpha=1.0) on `4` & `8` cores (256 B & 1500 B).
  - Part 3 (slow_core_stall): Single slow-core `1 / 5 / 10 ms` stall injection on `8` cores (+ 4c 5 ms check).

Modes:
  --mode self-test : Strictly offline verification of schedule, WMARK invariants, and PMU math (zero HW).
  --mode dry-run   : Strictly offline printout of full campaign schedule and exact commands (zero HW).
  --mode deploy    : Copy `mrv210_card` to CN106 and compile `mrv210_client` on mercury (requires nic-lock).
  --mode smoke     : Short end-to-end verification of all 4 arms + 3 workloads (requires nic-lock).
  --mode main      : Full n=3 interleaved campaign + 21 s post-restore EDAC/AER audit (requires nic-lock).
"""

import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv210")
LOG_DIR = ROOT / "logs/mrv210-2026-11-26"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = str(ROOT / "testbed/target/release/mrv210_host")
CARD_BIN_LOCAL = str(ROOT / "testbed/target/aarch64-unknown-linux-gnu/release/mrv210_card")
CARD_BIN_REMOTE = "/root/bench/mrv210_card"
MERC_SRC_DIR = ROOT / "testbed/mercury-client"
MERC_DIR_REMOTE = "/tmp/mrv210"
MERC_BIN_REMOTE = "/tmp/mrv210/mrv210_client"

CARD_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mercury"]

CARD_CTRL_PORT = 7550
HOST_CTRL_PORT = 7551
MERC_CTRL_PORT = 7552

# Dedicated Socket-1 physical cores (zero SMT sharing: physical cores 32..63, SMT 96..127)
HOST_MAIN_CPU = 39
HOST_RX_CPUS = [40, 41, 42, 43, 44, 45, 46, 47]
HOST_KV_CPU = 48
KV_BYTES = 32 * 1024 * 1024

REPS = 3
WARMUP_SECS = 0.4
MEAS_SECS = 1.5
STALL_MEAS_SECS = 2.0
MIN_CORE_GHZ = 3.85
MIN_MESH_GHZ = 2.18

ARMS = ["priv-ring", "sh-ring", "rx-bisect", "credit-sdp"]

# Frame profiles:
#   - 256B : RoCEv2 IBV_QPT_UD payload=184 B -> 254 B L2 wire frame (hardware-bypass 100GbE @ 45+ Mpps)
#   - 1500B: Linux UDP_SEGMENT GSO payload=1454 B -> exact 1500 B L2 wire frame (100GbE @ 8.22 Mpps, MTU 1500)
FRAME_PROFILES = {
    "256B": {"tx_mode": "ud", "payload": 184, "wire_l2_bytes": 254, "mthreads": 16, "mbatch": 32, "gso": 0},
    "1500B": {"tx_mode": "udp", "payload": 1454, "wire_l2_bytes": 1500, "mthreads": 16, "mbatch": 44, "gso": 1},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def arm_config(arm: str, nc: int) -> dict:
    """Return exact hardware/software parameters for each E4 arm at `nc` host cores (`1..=8`)."""
    assert 1 <= nc <= 8, f"VF0 supports 1..=8 queues, got {nc}"
    if arm == "priv-ring":
        return {
            "arm": "priv-ring",
            "card_mode": "passive",
            "devarg": f"0000:99:02.0,ism_enable=1,max_rings={nc}",
            "rx_desc": 2048,
            "pool_size": 4096,
            "pool_cache": 256,
            "r_eff": 0,
            "credit_batch": 64,
            "align_refill": True,
            "shring_cap": 1024,
            "bx_cap": 8192,
            "fifo_cap": 65536,
        }
    if arm == "sh-ring":
        shring_cap = 1024
        return {
            "arm": "sh-ring",
            "card_mode": "passive",
            "devarg": f"0000:99:02.0,ism_enable=1,allow_small_oq=1,max_rings={nc}",
            "rx_desc": 256,
            "pool_size": nc * 256 + shring_cap + 256,
            "pool_cache": 32,
            "r_eff": 0,
            "credit_batch": 64,
            "align_refill": True,
            "shring_cap": shring_cap,
            "bx_cap": 8192,
            "fifo_cap": 65536,
        }
    if arm == "rx-bisect":
        return {
            "arm": "rx-bisect",
            "card_mode": "passive",
            "devarg": f"0000:99:02.0,ism_enable=1,allow_small_oq=1,max_rings={nc}",
            "rx_desc": 256,
            "pool_size": 32768,
            "pool_cache": 256,
            "r_eff": 0,
            "credit_batch": 64,
            "align_refill": True,
            "shring_cap": 1024,
            "bx_cap": 8192,
            "fifo_cap": 65536,
        }
    if arm == "credit-sdp":
        # Pitfall 1 & 2 safety invariant:
        #   rx_desc = 512 -> hardware WMARK = min(256, 512/4) = 128.
        #   R_eff = 192, DROQ_REFILL_THRESHOLD = 64 -> free_hw_desc >= 512 - 192 - 64 = 256 > WMARK (128).
        #   Per-queue hot pool = 640 mbufs (512 in DROQ + 128 LIFO cache) -> 8c x 192 x 1.5 KiB = 2.25 MiB active
        #   and 8c x 512 x 1.5 KiB = 6.0 MiB max span, strictly inside 8 MiB 2-way DDIO!
        return {
            "arm": "credit-sdp",
            "card_mode": "credit",
            "devarg": f"0000:99:02.0,ism_enable=1,allow_small_oq=1,max_rings={nc}",
            "rx_desc": 512,
            "pool_size": 640,
            "pool_cache": 128,
            "r_eff": 192,
            "credit_batch": 64,
            "align_refill": True,
            "shring_cap": 1024,
            "bx_cap": 8192,
            "fifo_cap": 65536,
        }
    raise ValueError(f"Unknown arm: {arm}")


def validate_arm_invariants():
    """Offline check of CN106 octeon_ep WMARK, DROQ_REFILL_THRESHOLD, and DDIO working-set invariants."""
    for nc in (1, 2, 4, 8):
        for arm in ARMS:
            c = arm_config(arm, nc)
            rxd = c["rx_desc"]
            wmark = min(256, rxd // 4) if rxd < 2048 else 256
            if arm == "credit-sdp":
                min_free_desc = rxd - c["r_eff"] - 64
                assert min_free_desc > wmark, (
                    f"Pitfall 1 violation: nc={nc} rxd={rxd} r_eff={c['r_eff']} min_free={min_free_desc} <= wmark={wmark}"
                )
                assert c["pool_size"] >= rxd + c["pool_cache"], "pool_size too small for DROQ + LIFO cache"
                max_ddio_mib = (nc * rxd * 1536) / (1024 * 1024)
                assert max_ddio_mib < 8.0, f"CreditSDP 8c span {max_ddio_mib:.2f} MiB exceeds 8 MiB 2-way DDIO"
            if arm == "priv-ring" and nc == 8:
                priv_mib = (nc * rxd * 2048) / (1024 * 1024)
                assert priv_mib >= 32.0, f"privRing 8c should be >= 32 MiB, got {priv_mib:.1f}"


def build_smoke_specs() -> list[dict]:
    specs = []
    # Quick smoke across all 4 arms: 4c uniform 1500B, 8c uniform 256B, 8c 4:1 skew 1500B, 8c 5ms stall 1500B
    for arm in ARMS:
        specs.append({
            "part": "smoke_uniform_4c",
            "cfg": f"smoke_{arm}_4c_1500B_unif",
            "arm": arm, "nc": 4, "frame": "1500B", "dist": "uniform",
            "zipf_alpha": 1.0, "app_work_ns": 0, "stall_core": -1, "stall_us": 0,
            "stall_period_ms": 100, "kv_bytes": KV_BYTES, "meas_s": 0.8,
        })
    for arm in ARMS:
        specs.append({
            "part": "smoke_stall_8c",
            "cfg": f"smoke_{arm}_8c_1500B_stall5ms",
            "arm": arm, "nc": 8, "frame": "1500B", "dist": "uniform",
            "zipf_alpha": 1.0, "app_work_ns": 0, "stall_core": 0, "stall_us": 5000,
            "stall_period_ms": 50, "kv_bytes": KV_BYTES, "meas_s": 1.0,
        })
    for arm in ("priv-ring", "credit-sdp"):
        specs.append({
            "part": "smoke_skew_8c_256B",
            "cfg": f"smoke_{arm}_8c_256B_4to1",
            "arm": arm, "nc": 8, "frame": "256B", "dist": "ratio4to1",
            "zipf_alpha": 1.0, "app_work_ns": 0, "stall_core": -1, "stall_us": 0,
            "stall_period_ms": 100, "kv_bytes": KV_BYTES, "meas_s": 0.8,
        })
    return specs


def build_main_specs() -> list[dict]:
    """Complete E4 campaign spec list (per repetition; caller interleaves arm order across n=3 reps)."""
    specs = []

    # Part 1: Uniform 100GbE scaling (1 -> 2 -> 4 -> 8 host cores) x 2 frame sizes (256B, 1500B) x 4 arms
    for frame in ("1500B", "256B"):
        for nc in (1, 2, 4, 8):
            for arm in ARMS:
                specs.append({
                    "part": "part1_uniform_scale",
                    "cfg": f"p1_{arm}_c{nc}_{frame}_unif",
                    "arm": arm, "nc": nc, "frame": frame, "dist": "uniform",
                    "zipf_alpha": 1.0, "app_work_ns": 0, "stall_core": -1, "stall_us": 0,
                    "stall_period_ms": 100, "kv_bytes": KV_BYTES, "meas_s": MEAS_SECS,
                })

    # Part 2: RSS Skew (ratio4to1 & zipf alpha=1.0) on 4 & 8 cores x (256B, 1500B) x 4 arms
    for frame in ("1500B", "256B"):
        for nc in (4, 8):
            for dist in ("ratio4to1", "zipf"):
                for arm in ARMS:
                    specs.append({
                        "part": "part2_rss_skew",
                        "cfg": f"p2_{arm}_c{nc}_{frame}_{dist}",
                        "arm": arm, "nc": nc, "frame": frame, "dist": dist,
                        "zipf_alpha": 1.0, "app_work_ns": 0, "stall_core": -1, "stall_us": 0,
                        "stall_period_ms": 100, "kv_bytes": KV_BYTES, "meas_s": MEAS_SECS,
                    })

    # Part 3: Single Slow-Core Stall Isolation (1, 5, 10 ms on 8 cores + 5 ms on 4 cores) x (1500B, 256B) x 4 arms
    for frame in ("1500B", "256B"):
        for nc, stall_ms_list in ((8, (1, 5, 10)), (4, (5,))):
            for stall_ms in stall_ms_list:
                for arm in ARMS:
                    specs.append({
                        "part": "part3_slow_core_stall",
                        "cfg": f"p3_{arm}_c{nc}_{frame}_stall{stall_ms}ms",
                        "arm": arm, "nc": nc, "frame": frame, "dist": "uniform",
                        "zipf_alpha": 1.0, "app_work_ns": 0, "stall_core": 0,
                        "stall_us": stall_ms * 1000, "stall_period_ms": 50,
                        "kv_bytes": KV_BYTES, "meas_s": STALL_MEAS_SECS,
                    })

    return specs


def rotate_specs_for_rep(specs: list[dict], rep: int) -> list[dict]:
    """Interleave the 4 arms in cyclic order per rep to cancel thermal/order drift."""
    out = []
    i = 0
    while i < len(specs):
        # Group consecutive 4-arm blocks sharing identical (part, nc, frame, dist, stall_us)
        if i + 4 <= len(specs) and [s["arm"] for s in specs[i:i + 4]] == ARMS:
            blk = specs[i:i + 4]
            shift = rep % 4
            out.extend(blk[shift:] + blk[:shift])
            i += 4
        else:
            out.append(specs[i])
            i += 1
    return out


def tad_delta(a: dict, b: dict) -> dict:
    dt = max(b["t_s"] - a["t_s"], 1e-9)
    min_pct = min(
        100.0,
        a.get("ddr_rd_min_pct", 100.0),
        b.get("ddr_rd_min_pct", 100.0),
        a.get("ddr_wr_min_pct", 100.0),
        b.get("ddr_wr_min_pct", 100.0),
    )
    out = {"dt_s": dt, "ddr_channels": b.get("ddr_channels", 0)}
    for k in a["tad"]:
        v0, e0, r0 = a["tad"][k]
        v1, e1, r1 = b["tad"][k]
        de = e1 - e0
        dr = r1 - r0
        if de > 0:
            min_pct = min(min_pct, 100.0 * dr / de)
        dv = v1 - v0
        out[k] = dv
        out[k + "_mops"] = dv / dt / 1e6
    out["ddr_rd_gbps"] = (out.get("ddr_reads", 0) * 64.0 * 8.0) / dt / 1e9
    out["ddr_wr_gbps"] = (out.get("ddr_writes", 0) * 64.0 * 8.0) / dt / 1e9
    out["min_running_pct"] = min_pct
    return out


def evaluate_gates(spec: dict, hs: dict, cs: dict, ms: dict, tad_d: dict) -> list[str]:
    """Strict verification gates (integrity errors are NEVER retried; clock errors may back off)."""
    errs = []
    arm = spec["arm"]
    hp = hs.get("host_pmu") or {}
    kv = hs.get("kv")

    if hp.get("min_running_pct", 0.0) < 99.95:
        errs.append(f"host_pmu_pct={hp.get('min_running_pct', 0.0):.3f}")
    if tad_d.get("min_running_pct", 0.0) < 99.95:
        errs.append(f"card_pmu_pct={tad_d.get('min_running_pct', 0.0):.3f}")
    if hp.get("min_rx_ghz", 0.0) < MIN_CORE_GHZ:
        errs.append(f"min_rx_ghz={hp.get('min_rx_ghz', 0.0):.3f}")
    if hp.get("mesh_ghz", 0.0) < MIN_MESH_GHZ:
        errs.append(f"mesh_ghz={hp.get('mesh_ghz', 0.0):.3f}")

    if hs.get("bad", 0) != 0 or hs.get("torn", 0) != 0:
        errs.append(f"host_bad={hs.get('bad')} host_torn={hs.get('torn')}")
    if hs.get("page_faults", 0) != 0:
        errs.append(f"host_pf={hs.get('page_faults')}")
    if cs.get("wire_bad", 0) != 0 or cs.get("wire_torn", 0) != 0:
        errs.append(f"card_wire_bad={cs.get('wire_bad')} card_wire_torn={cs.get('wire_torn')}")
    if cs.get("credit_torn", 0) != 0:
        errs.append(f"card_credit_torn={cs.get('credit_torn')}")
    if ms.get("cqe_err", 0) != 0 or ms.get("post_err", 0) != 0:
        errs.append(f"merc_cqe_err={ms.get('cqe_err')} post_err={ms.get('post_err')}")
    if ms.get("phy", {}).get("rx_crc_errors", 0) != 0:
        errs.append(f"merc_crc={ms['phy']['rx_crc_errors']}")

    if kv is not None:
        if kv.get("bad", 0) != 0:
            errs.append(f"kv_bad={kv.get('bad')}")
        if kv.get("page_faults", 0) != 0:
            errs.append(f"kv_pf={kv.get('page_faults')}")
        kv_pmu = hp.get("kv_core") or {}
        if kv_pmu.get("ghz", 0.0) < MIN_CORE_GHZ:
            errs.append(f"kv_ghz={kv_pmu.get('ghz', 0.0):.3f}")

    # CreditSDP zero-loss & zero-backpressure invariant
    if arm == "credit-sdp":
        if cs.get("bp_drops", 0) != 0 or cs.get("fifo_full_drops", 0) != 0:
            errs.append(f"credit_sdp_drops bp={cs.get('bp_drops')} fifo_full={cs.get('fifo_full_drops')}")
        if hs.get("rx_pkts", 0) == 0:
            errs.append("credit_sdp_zero_rx")

    return errs


# ---------------------------------------------------------------------------
# Subprocess, SSH, and TCP helpers (ONLY invoked in deploy / smoke / main)
# ---------------------------------------------------------------------------
def sh(cmd, check=True, timeout=180):
    r = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"CMD FAILED ({r.returncode}): {cmd}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
    return r


def ssh_card(script: str, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def ssh_merc(script: str, check=True, timeout=60):
    return sh(MERC_SSH + [script], check=check, timeout=timeout)


def tcp_cmd(host: str, port: int, cmd: str, timeout: float = 10.0) -> dict:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((cmd + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode().strip())


def append_jsonl(path: pathlib.Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj, separators=(",", ":")) + "\n")
        f.flush()


def read_env_all() -> dict:
    cpus = [HOST_MAIN_CPU] + HOST_RX_CPUS + [HOST_KV_CPU]
    cpu_govs = {}
    cpu_khz = {}
    for c in cpus:
        cpu_govs[f"cpu{c}"] = pathlib.Path(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_governor").read_text().strip()
        cpu_khz[f"cpu{c}"] = int(pathlib.Path(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = {
        p.parent.name: int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz"))
    }
    host_ue = [
        int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count"))
    ]
    rp = pathlib.Path("/sys/bus/pci/devices/0000:98:01.0")
    rp_aer = {}
    for name in ("aer_rootport_total_err_cor", "aer_rootport_total_err_nonfatal", "aer_rootport_total_err_fatal"):
        f = rp / name
        rp_aer[name] = int(f.read_text().strip()) if f.exists() else -1

    card_raw = ssh_card(
        "echo cpu3_khz=$(cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 2000000) "
        "mc0_ue=$(cat /sys/devices/system/edac/mc/mc0/ue_count 2>/dev/null || echo 0) "
        "ea0_ue=$(cat /sys/devices/system/edac/ea/ea0/ue_count 2>/dev/null || echo 0) "
        "sei_uer=$(dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || echo 0) "
        "bar4_idx7=$(python3 /root/bar4idx7.py 2>/dev/null | awk '{print $NF}' || echo n/a)"
    ).stdout.strip()
    card_env = {}
    for tok in card_raw.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            card_env[k] = int(v) if v.isdigit() else v

    merc_raw = ssh_merc(
        "ethtool -S ens6f1np1 | grep -E 'tx_packets_phy:|tx_bytes_phy:|rx_crc_errors_phy:|tx_global_pause:|rx_global_pause:'"
    ).stdout
    merc_env = {}
    for line in merc_raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            try:
                merc_env[k.strip()] = int(v.strip())
            except ValueError:
                pass

    return {
        "ts": utc_now(),
        "saturn": {
            "cpu_govs": cpu_govs,
            "cpu_khz": cpu_khz,
            "uncore_khz": uncore,
            "edac_ue_sum": sum(host_ue),
            "rp_aer": rp_aer,
        },
        "cn106": card_env,
        "mercury": merc_env,
    }


def deploy_binaries():
    """Deploy compiled aarch64 mrv210_card to CN106 and compile mrv210_client on mercury."""
    assert pathlib.Path(HOST_BIN).exists(), f"Missing host binary: {HOST_BIN}"
    assert pathlib.Path(CARD_BIN_LOCAL).exists(), f"Missing card binary: {CARD_BIN_LOCAL}"
    print(f"[DEPLOY] Copying {CARD_BIN_LOCAL} -> root@10.10.0.2:{CARD_BIN_REMOTE}", flush=True)
    sh(["scp", "-o", "BatchMode=yes", CARD_BIN_LOCAL, f"root@10.10.0.2:{CARD_BIN_REMOTE}"])
    ssh_card(f"chmod +x {CARD_BIN_REMOTE}")

    print(f"[DEPLOY] Copying & compiling mercury client in mercury:{MERC_DIR_REMOTE}", flush=True)
    ssh_merc(f"mkdir -p {MERC_DIR_REMOTE}")
    sh([
        "scp", "-o", "BatchMode=yes",
        str(MERC_SRC_DIR / "mrv210_verbs_shim.c"),
        str(MERC_SRC_DIR / "mrv210_client.rs"),
        f"mercury:{MERC_DIR_REMOTE}/",
    ])
    ssh_merc(
        f"cd {MERC_DIR_REMOTE} && "
        "gcc -O3 -Wall -Wextra -Werror -c mrv210_verbs_shim.c -o mrv210_verbs_shim.o && "
        "rustc --edition 2024 -O -D warnings mrv210_client.rs "
        "-C link-arg=mrv210_verbs_shim.o -C link-arg=-libverbs -o mrv210_client && "
        "ls -lh mrv210_client"
    )
    print("[DEPLOY] Done.", flush=True)


def prepare_card_vfio():
    ssh_card(
        "tmux send-keys -t refl C-c 2>/dev/null || true; sleep 0.2; "
        "tmux kill-session -t refl 2>/dev/null || true; "
        "tmux kill-session -t m210c 2>/dev/null || true; "
        "pkill -9 mrv210_card 2>/dev/null || true; "
        "rm -f /dev/hugepages/refl* /dev/hugepages/m210c*; "
        "ip link set eth0 down 2>/dev/null || true; "
        "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
        'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
        "drv2=$(basename $(readlink -f /sys/bus/pci/devices/0002:01:00.2/driver) 2>/dev/null || echo none); "
        'if [ "$drv2" != "vfio-pci" ]; then sh /root/bench/dpdk_up.sh 0002:01:00.2; fi'
    )


def restore_baseline() -> dict:
    try:
        tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "QUIT", timeout=2.0)
    except Exception:
        pass
    ssh_card(
        "sleep 0.3; tmux kill-session -t m210c 2>/dev/null || true; rm -f /dev/hugepages/m210c*; "
        f"{CARD_BIN_REMOTE} --restore || true; "
        "dpdk-devbind.py -b rvu_nicpf 0002:02:00.0; sleep 1.5; "
        "ip addr flush dev eth0 2>/dev/null || true; "
        "ip addr add 10.10.100.2/24 dev eth0; "
        "ip link set eth0 up; sleep 2.0; "
        "tmux kill-session -t refl 2>/dev/null || true; rm -f /dev/hugepages/refl*; "
        "tmux new-session -d -s refl /root/bench/reflector-rs -l 3 -a 0002:01:00.2 --file-prefix=refl; "
        "sleep 1.0; ethtool eth0 | grep -E 'Speed|Link detected' || true; "
        "ping -c 2 -W 1 10.10.100.1 | tail -n 2 || true",
        check=False,
    )
    sh(MERC_SSH + ["pkill -f mrv210_client 2>/dev/null || true"], check=False)
    print("[RESTORE] Waiting 21 s for delayed EDAC/AER audit (AGENTS.md)...", flush=True)
    time.sleep(21.0)
    return read_env_all()


def start_card(nc: int, mode: str = "credit", r_eff: int = 192, fifo_cap: int = 65536):
    ssh_card("tmux kill-session -t m210c 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/m210c* /tmp/m210c.log")
    # Pin card workers to CN106 CPUs 4..(3+nc), main lcore on CPU 2 (leaving CPUs 0..1 for Linux)
    lcores = f"2,4-{3 + nc}" if nc > 1 else "2,4"
    cmd = (
        f"{CARD_BIN_REMOTE} -l {lcores} --main-lcore 2 "
        f"-a 0002:02:00.0 -a 0002:01:00.2 --file-prefix=m210c -- "
        f"--mode {mode} --r-eff {r_eff} --fifo-cap {fifo_cap} --ctrl-port {CARD_CTRL_PORT}"
    )
    r = ssh_card(
        f'tmux new-session -d -s m210c "{cmd} > /tmp/m210c.log 2>&1"; '
        f'for i in $(seq 1 80); do grep -q "MRV210_CARD READY" /tmp/m210c.log 2>/dev/null && break; sleep 0.15; done; '
        f'grep "MRV210_CARD READY" /tmp/m210c.log',
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError("card start failed:\n" + ssh_card("cat /tmp/m210c.log", check=False).stdout)


def stop_card():
    try:
        tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "QUIT", timeout=2.0)
    except Exception:
        pass
    ssh_card("sleep 0.25; tmux kill-session -t m210c 2>/dev/null || true; rm -f /dev/hugepages/m210c*", check=False)


def start_host(spec: dict, cfg: dict, run_gen: int, expected_payload: int):
    sh(["bash", "-c", "pkill -9 mrv210_host 2>/dev/null || true; rm -f /dev/hugepages/m210h* /tmp/m210h.log"], check=False)
    nc = spec["nc"]
    rx_cpus = HOST_RX_CPUS[:nc]
    lcore_list = ",".join(str(c) for c in ([HOST_MAIN_CPU] + rx_cpus))
    rx_cpus_str = ",".join(str(c) for c in rx_cpus)
    cmd = [
        HOST_BIN,
        "-l", lcore_list,
        "--main-lcore", str(HOST_MAIN_CPU),
        "-a", cfg["devarg"],
        "--socket-mem=0,2048",
        "--iova-mode=va",
        "--file-prefix=m210h",
        "--",
        "--arm", cfg["arm"],
        "--queues", str(nc),
        "--rx-cpus", rx_cpus_str,
        "--rx-desc", str(cfg["rx_desc"]),
        "--pool-size", str(cfg["pool_size"]),
        "--pool-cache", str(cfg["pool_cache"]),
        "--credit-batch", str(cfg["credit_batch"]),
        "--align-refill", "true" if cfg["align_refill"] else "false",
        "--shring-cap", str(cfg["shring_cap"]),
        "--bx-cap", str(cfg["bx_cap"]),
        "--app-work-ns", str(spec["app_work_ns"]),
        "--stall-core", str(spec["stall_core"]),
        "--stall-us", str(spec["stall_us"]),
        "--stall-period-ms", str(spec["stall_period_ms"]),
        "--expected-payload", str(expected_payload),
        "--run-gen", str(run_gen),
        "--kv-bytes", str(spec["kv_bytes"]),
        "--kv-cpu", str(HOST_KV_CPU),
        "--ctrl-port", str(HOST_CTRL_PORT),
    ]
    lf = open("/tmp/m210h.log", "w")
    hp = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
    lf.close()
    for _ in range(80):
        if hp.poll() is not None:
            raise RuntimeError("mrv210_host exited early:\n" + pathlib.Path("/tmp/m210h.log").read_text())
        txt = pathlib.Path("/tmp/m210h.log").read_text()
        if "MRV210_HOST READY" in txt:
            return hp
        time.sleep(0.1)
    raise RuntimeError("mrv210_host ready timeout:\n" + pathlib.Path("/tmp/m210h.log").read_text())


def stop_host(hp):
    if hp is None:
        return
    try:
        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "QUIT", timeout=2.0)
    except Exception:
        pass
    try:
        hp.wait(timeout=5)
    except Exception:
        hp.kill()
    sh(["bash", "-c", "rm -f /dev/hugepages/m210h*"], check=False)


def probe_rss_flows(nc: int, tx_mode: str) -> str:
    """Discover per-queue RSS flow_labels (UD) or UDP src_ports (UDP) for `nc` CN106 eth0 queues."""
    if nc == 1:
        return "30001" if tx_mode == "udp" else "1"
    start_card(nc, mode="probe-rss")
    try:
        tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "CONFIG mode=probe-rss run_gen=1")
        pr = tcp_cmd("mercury", MERC_CTRL_PORT, f"PROBE_RSS mode={tx_mode} count=128 run_gen=1", timeout=20.0)
        base_sport = int(pr.get("base_sport", 30000))
        time.sleep(0.08)
        cs = tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "STATS")
        fq = cs.get("flow_q", [])
    finally:
        stop_card()
    by_q = {q: [] for q in range(nc)}
    for idx, q1 in enumerate(fq[:128]):
        if 1 <= q1 <= nc:
            val = (base_sport + idx + 1) if tx_mode == "udp" else (idx + 1)
            by_q[q1 - 1].append(val)
    missing = [q for q in range(nc) if not by_q[q]]
    if missing:
        raise RuntimeError(f"RSS probe failed for nc={nc} mode={tx_mode}: missing queues {missing}")
    print(
        f"  [RSS PROBE] nc={nc} mode={tx_mode}: min={min(len(v) for v in by_q.values())} flows/q",
        flush=True,
    )
    return ";".join(",".join(str(x) for x in by_q[q][:16]) for q in range(nc))


def run_kv_solo_baseline(rep: int) -> dict:
    cmd = [
        HOST_BIN,
        "-l", str(HOST_MAIN_CPU),
        "--no-pci",
        "--file-prefix=m210kv",
        "--",
        "--arm", "kv-solo",
        "--kv-bytes", str(KV_BYTES),
        "--kv-cpu", str(HOST_KV_CPU),
        "--solo-warmup-ms", "400",
        "--solo-ms", "1200",
    ]
    out = sh(cmd, timeout=30).stdout
    sh(["bash", "-c", "rm -f /dev/hugepages/m210kv*"], check=False)
    for line in out.splitlines():
        if line.startswith("MRV210_KV_SOLO "):
            obj = json.loads(line[len("MRV210_KV_SOLO "):])
            return {"ts": utc_now(), "part": "part0_kv_solo", "cfg": "kv_solo_32m", "rep": rep, "verified": True, **obj}
    raise RuntimeError(f"Missing MRV210_KV_SOLO output:\n{out}")


def run_one(spec: dict, rep: int, phase: str, run_gen: int, flow_map: dict, active_card_nc: list):
    nc = spec["nc"]
    arm = spec["arm"]
    cfg = arm_config(arm, nc)
    fprof = FRAME_PROFILES[spec["frame"]]
    tx_mode = fprof["tx_mode"]
    payload = fprof["payload"]
    q_flows = flow_map[(nc, tx_mode)]

    # Keep card running across same `nc` runs; only restart card when `nc` changes
    if active_card_nc[0] != nc:
        stop_card()
        start_card(nc, mode=cfg["card_mode"], r_eff=max(cfg["r_eff"], 1), fifo_cap=cfg["fifo_cap"])
        active_card_nc[0] = nc

    tcp_cmd(
        "10.10.0.2",
        CARD_CTRL_PORT,
        f"CONFIG mode={cfg['card_mode']} r_eff={max(cfg['r_eff'], 1)} fifo_cap={cfg['fifo_cap']} "
        f"run_gen={run_gen} expected_payload={payload}",
    )

    hp = None
    try:
        hp = start_host(spec, cfg, run_gen, payload)
        mcfg = (
            f"CONFIG mode={tx_mode} threads={fprof['mthreads']} queues={nc} payload={payload} "
            f"batch={fprof['mbatch']} rate_mpps=0.0 run_gen={run_gen} dist={spec['dist']} "
            f"zipf_alpha={spec['zipf_alpha']:.3f} gso={fprof['gso']} q_flows={q_flows}"
        )
        mr = tcp_cmd("mercury", MERC_CTRL_PORT, mcfg, timeout=15.0)
        if not mr.get("ok"):
            raise RuntimeError(f"mercury CONFIG failed: {mr}")

        # Warmup phase
        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "START")
        tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "START")
        tcp_cmd("mercury", MERC_CTRL_PORT, "START")
        time.sleep(WARMUP_SECS)

        # Clean drain boundary between warmup and measurement window
        tcp_cmd("mercury", MERC_CTRL_PORT, "STOP")
        time.sleep(0.025)
        tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "STOP")
        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "STOP")
        time.sleep(0.01)

        # Synchronized reset + PMU snap right before measurement
        tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "CLEAR_BAR4")
        tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "RESET")
        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, f"RESET run_gen={run_gen}")
        tcp_cmd("mercury", MERC_CTRL_PORT, "RESET")

        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "START")
        tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "START")
        t0_card_pmu = tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "PMU_SNAP")
        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "PMU_START")
        t_start = time.monotonic()
        tcp_cmd("mercury", MERC_CTRL_PORT, "START")

        time.sleep(spec["meas_s"])

        tcp_cmd("mercury", MERC_CTRL_PORT, "STOP")
        t_end = time.monotonic()
        # Drain in-flight packets cleanly (especially after slow-core stall wakeup)
        time.sleep(0.025)
        t1_card_pmu = tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "PMU_SNAP")
        hs = tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "STATS")
        cs = tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "STATS")
        ms = tcp_cmd("mercury", MERC_CTRL_PORT, "STATS")
    finally:
        try:
            tcp_cmd("mercury", MERC_CTRL_PORT, "STOP", timeout=2.0)
        except Exception:
            pass
        try:
            tcp_cmd("10.10.0.2", CARD_CTRL_PORT, "STOP", timeout=2.0)
        except Exception:
            pass
        stop_host(hp)

    tad_d = tad_delta(t0_card_pmu, t1_card_pmu)
    errs = evaluate_gates(spec, hs, cs, ms, tad_d)
    dt_wall = max(t_end - t_start, 1e-9)
    wire_l2 = fprof["wire_l2_bytes"]
    host_rx_pkts = hs["rx_pkts"]
    host_rx_mpps = host_rx_pkts / dt_wall / 1e6
    host_wire_gbps = (host_rx_pkts * (wire_l2 + 20) * 8.0) / dt_wall / 1e9

    rec = {
        "ts": utc_now(),
        "phase": phase,
        "part": spec["part"],
        "cfg": spec["cfg"],
        "rep": rep,
        "run_gen": run_gen,
        "spec": spec,
        "arm_cfg": cfg,
        "dt_wall_s": dt_wall,
        "summary": {
            "host_rx_mpps": host_rx_mpps,
            "host_wire_gbps": host_wire_gbps,
            "merc_phy_gbps": ms["phy"]["wire_gbps"],
            "ddio_hit_pct": hs["host_pmu"]["ddio_hit_pct"],
            "imc_rd_gbps": hs["host_pmu"]["imc_rd_gbps"],
            "imc_wr_gbps": hs["host_pmu"]["imc_wr_gbps"],
            "card_ddr_rd_gbps": tad_d["ddr_rd_gbps"],
            "card_ddr_wr_gbps": tad_d["ddr_wr_gbps"],
            "kv_p99_ns": hs["kv"]["p99_ns"] if hs.get("kv") else None,
            "kv_p999_ns": hs["kv"]["p999_ns"] if hs.get("kv") else None,
            "card_bp_drops": cs["bp_drops"],
            "card_fifo_peak": cs["fifo_peak_max"],
            "host_bx_peak": hs["bx_peak_max"],
            "host_bx_drops": hs["bx_overflow_drops"],
            "host_hol_stalls": hs["hol_stalls"],
        },
        "host": hs,
        "card": cs,
        "mercury": ms,
        "card_pmu": tad_d,
        "verified": len(errs) == 0,
        "verify_errors": errs,
    }
    append_jsonl(ATTEMPTS_PATH, rec)
    return rec


def run_self_test():
    """Strictly offline validation of arm invariants, schedules, interleaving, and PMU math."""
    validate_arm_invariants()
    smoke = build_smoke_specs()
    main_s = build_main_specs()
    assert len(smoke) == 10, f"expected 10 smoke specs, got {len(smoke)}"
    assert len(main_s) == (2 * 4 * 4) + (2 * 2 * 2 * 4) + (2 * 4 * 4), f"unexpected main len {len(main_s)}"
    # Verify cyclic arm rotation across reps 0, 1, 2
    for rep in range(REPS):
        rot = rotate_specs_for_rep(main_s, rep)
        assert len(rot) == len(main_s)
        assert rot[0]["arm"] == ARMS[rep % 4]
    # Verify tad_delta calculation
    s0 = {"t_s": 1.0, "ddr_channels": 6, "ddr_rd_min_pct": 100.0, "ddr_wr_min_pct": 100.0,
          "tad": {"tad_hit_any": [100, 1000, 1000], "ddr_reads": [0, 1000, 1000], "ddr_writes": [0, 1000, 1000]}}
    s1 = {"t_s": 2.0, "ddr_channels": 6, "ddr_rd_min_pct": 100.0, "ddr_wr_min_pct": 100.0,
          "tad": {"tad_hit_any": [1_000_100, 2000, 2000], "ddr_reads": [1_000_000, 2000, 2000], "ddr_writes": [2_000_000, 2000, 2000]}}
    td = tad_delta(s0, s1)
    assert td["min_running_pct"] == 100.0
    assert abs(td["tad_hit_any_mops"] - 1.0) < 1e-6
    assert abs(td["ddr_rd_gbps"] - 0.512) < 1e-6
    assert abs(td["ddr_wr_gbps"] - 1.024) < 1e-6
    # Verify gate rejection on bad/torn/clock/drops
    fake_spec = main_s[0]
    ok_hs = {"bad": 0, "torn": 0, "page_faults": 0, "rx_pkts": 1000, "kv": {"bad": 0, "page_faults": 0},
             "host_pmu": {"min_running_pct": 100.0, "min_rx_ghz": 3.90, "mesh_ghz": 2.20, "kv_core": {"ghz": 3.90}}}
    ok_cs = {"wire_bad": 0, "wire_torn": 0, "credit_torn": 0, "bp_drops": 0, "fifo_full_drops": 0}
    ok_ms = {"cqe_err": 0, "post_err": 0, "phy": {"rx_crc_errors": 0}}
    assert evaluate_gates(fake_spec, ok_hs, ok_cs, ok_ms, td) == []
    bad_hs = dict(ok_hs, torn=1)
    assert any("host_torn=1" in e for e in evaluate_gates(fake_spec, bad_hs, ok_cs, ok_ms, td))
    print(
        f"SELF-TEST OK: invariants verified, smoke={len(smoke)} specs, main={len(main_s)} specs/rep "
        f"({len(main_s) * REPS + REPS} total runs incl. {REPS} kv-solo).",
        flush=True,
    )


def run_dry_run():
    validate_arm_invariants()
    specs = build_main_specs()
    print(f"=== MRV-210 E4 DRY-RUN (STRICTLY OFFLINE) ===", flush=True)
    print(f"Main specs per rep: {len(specs)} | Total n={REPS} runs: {len(specs) * REPS} + {REPS} kv-solo", flush=True)
    for part in ("part1_uniform_scale", "part2_rss_skew", "part3_slow_core_stall"):
        ps = [s for s in specs if s["part"] == part]
        print(f"  {part:24s}: {len(ps):3d} specs/rep ({len(ps) * REPS:3d} total)", flush=True)
    print("\nArm configurations at nc=8:", flush=True)
    for arm in ARMS:
        print(f"  {arm:12s}: {json.dumps(arm_config(arm, 8))}", flush=True)
    print("\nFirst 8 specs (rep 0 vs rep 1 rotation):", flush=True)
    r0 = rotate_specs_for_rep(specs, 0)[:8]
    r1 = rotate_specs_for_rep(specs, 1)[:8]
    for a, b in zip(r0, r1):
        print(f"  rep0={a['cfg']:34s} | rep1={b['cfg']}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="MRV-210 (MRV-203 E4) multi-core isolation harness")
    ap.add_argument("--mode", choices=["self-test", "dry-run", "deploy", "smoke", "main"], default="self-test")
    ap.add_argument("--no-restore", action="store_true", help="Skip baseline card/eth0 restore on exit")
    args = ap.parse_args()

    if args.mode == "self-test":
        run_self_test()
        return
    if args.mode == "dry-run":
        run_dry_run()
        return
    if args.mode == "deploy":
        deploy_binaries()
        return

    validate_arm_invariants()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env_before = read_env_all()
    print("ENV BEFORE:", json.dumps(env_before), flush=True)
    prepare_card_vfio()

    # Launch mercury client daemon
    sh(MERC_SSH + ["pkill -9 mrv210_client 2>/dev/null || true"], check=False)
    mp = subprocess.Popen(
        MERC_SSH + [f"{MERC_BIN_REMOTE} --port {MERC_CTRL_PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    ready_line = mp.stdout.readline().strip()
    print("MERC READY:", ready_line, flush=True)

    active_card_nc = [None]
    flow_map = {}
    run_gen = 100
    done = 0
    failed = 0
    clock_retries = 0

    try:
        specs = build_smoke_specs() if args.mode == "smoke" else build_main_specs()
        needed_probes = sorted({(s["nc"], FRAME_PROFILES[s["frame"]]["tx_mode"]) for s in specs})
        for nc, tx_mode in needed_probes:
            flow_map[(nc, tx_mode)] = probe_rss_flows(nc, tx_mode)

        if args.mode == "smoke":
            kv_rec = run_kv_solo_baseline(0)
            print(
                f"[KV-SOLO] p50={kv_rec['kv']['p50_ns']:.1f}ns p99={kv_rec['kv']['p99_ns']:.1f}ns "
                f"p999={kv_rec['kv']['p999_ns']:.1f}ns",
                flush=True,
            )
            for idx, sp in enumerate(specs, 1):
                run_gen += 1
                rec = run_one(sp, 0, "smoke", run_gen, flow_map, active_card_nc)
                sm = rec["summary"]
                print(
                    f"[SMOKE {idx:02d}/{len(specs)}] {sp['cfg']:30s} | rx={sm['host_wire_gbps']:5.1f}G "
                    f"(merc={sm['merc_phy_gbps']:5.1f}G) ddio={sm['ddio_hit_pct']:5.1f}% "
                    f"imc={sm['imc_rd_gbps']:.1f}/{sm['imc_wr_gbps']:.1f}G kv99={sm['kv_p99_ns']}ns "
                    f"bp={sm['card_bp_drops']} bx={sm['host_bx_peak']}/{sm['host_bx_drops']} "
                    f"hol={sm['host_hol_stalls']} fifo={sm['card_fifo_peak']} ok={rec['verified']} {rec['verify_errors']}",
                    flush=True,
                )
        else:
            with open(RUNS_PATH, "w") as out:
                for rep in range(REPS):
                    kv_rec = run_kv_solo_baseline(rep)
                    out.write(json.dumps(kv_rec, separators=(",", ":")) + "\n")
                    out.flush()
                    append_jsonl(ATTEMPTS_PATH, kv_rec)
                    print(
                        f"[PART 0 rep={rep+1}/{REPS}] kv-solo p50={kv_rec['kv']['p50_ns']:.1f}ns "
                        f"p99={kv_rec['kv']['p99_ns']:.1f}ns p999={kv_rec['kv']['p999_ns']:.1f}ns",
                        flush=True,
                    )

                for rep in range(REPS):
                    order = rotate_specs_for_rep(specs, rep)
                    for idx, sp in enumerate(order, 1):
                        run_gen += 1
                        rec = run_one(sp, rep, "main", run_gen, flow_map, active_card_nc)
                        retries = 0
                        while (
                            not rec["verified"]
                            and retries < 5
                            and all(e.startswith(("min_rx_ghz=", "mesh_ghz=", "kv_ghz=")) for e in rec["verify_errors"])
                        ):
                            retries += 1
                            clock_retries += 1
                            backoff = min(5.0 * retries, 20.0)
                            print(
                                f"  [CLOCK_RETRY #{retries} sleep {backoff:.0f}s] {sp['cfg']} rep={rep} errs={rec['verify_errors']}",
                                flush=True,
                            )
                            time.sleep(backoff)
                            run_gen += 1
                            rec = run_one(sp, rep, "main", run_gen, flow_map, active_card_nc)
                        rec["clock_retries_before_accept"] = retries
                        out.write(json.dumps(rec, separators=(",", ":")) + "\n")
                        out.flush()
                        done += 1
                        if not rec["verified"]:
                            failed += 1
                        sm = rec["summary"]
                        print(
                            f"[rep={rep+1}/{REPS} {idx:03d}/{len(order)}] {sp['cfg']:30s} | "
                            f"rx={sm['host_wire_gbps']:5.1f}G ddio={sm['ddio_hit_pct']:5.1f}% "
                            f"imc={sm['imc_rd_gbps']:.1f}/{sm['imc_wr_gbps']:.1f}G kv99={sm['kv_p99_ns']}ns "
                            f"bp={sm['card_bp_drops']} bx={sm['host_bx_peak']} hol={sm['host_hol_stalls']} "
                            f"fifo={sm['card_fifo_peak']} ok={rec['verified']}",
                            flush=True,
                        )
    finally:
        stop_card()
        try:
            tcp_cmd("mercury", MERC_CTRL_PORT, "QUIT", timeout=2.0)
        except Exception:
            pass
        try:
            mp.wait(timeout=4)
        except Exception:
            mp.kill()

    env_after = read_env_all() if args.no_restore else restore_baseline()
    if args.mode == "main":
        ENV_PATH.write_text(json.dumps({
            "before": env_before,
            "after": env_after,
            "flow_map": {f"{nc}_{m}": v for (nc, m), v in flow_map.items()},
            "done": done,
            "failed": failed,
            "clock_retries": clock_retries,
        }, indent=2))
        print(f"MRV-210 MAIN DONE total={done} failed={failed} clock_retries={clock_retries}", flush=True)


if __name__ == "__main__":
    main()
