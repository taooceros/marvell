#!/usr/bin/env python3
"""MRV-211 (MRV-203 E3) Dual-Host 100GbE Experiment Harness.

Same-silicon CN106 + standard SDP showdown between:
  - `unified`        : On-Path SmartNIC unified single-path O(1) credit-gated SDP push
                       (nb_desc=512, WMARK=128, R_eff=256/lane = 1024 total, compact DDIO-hot
                       host mempool, fence-free MOVDIR64B -> BAR4 credit return, optional
                       lightweight in-NIC RPC priority queue; zero RMT rewrites, zero phase
                       pauses, zero 2P1C SW ring, zero async_recv()).
  - `unified_noprio` : Single-variable ablation of `unified` with `--prio-reserve 0` (Suite W3).
  - `ceio`           : Faithful CEIO (SIGCOMM '25 §4.1-4.2) reactive dual-path baseline on the
                       exact same CN106 + standard SDP base (Fast Path SDP Q0..3, on-NIC DRAM
                       Slow Path ring + SDP Q4..7 async pull, dedicated 5th CN106 ARM core
                       running CEIO Algorithm 1 + RMT rule updates + phase exclusivity, and
                       host-side 2P1C SW merge ring + async_recv() + lazy credit release;
                       matched LLC budget C_total=1024).
  - `ceio_zero_rmt`  : CEIO sensitivity control with `--ceio-rmt-rewrite-us 0` (isolates pure
                       reactive poll + phase-exclusivity + 2P1C overhead from HW RMT rewrite lag).
  - `large_sdp`      : Uncontrolled passive large SDP baseline (nb_desc=4096, 65535-mbuf pool).
  - `card_echo`      : Pure CN106 eth0 echo wire-ceiling sanity control.

Covers all three MRV-211 (E3) stress benchmarks:
  - W1 (`burst-onset`) : Microsecond-scale sudden burst onset (transient LLC leakage, phase-pause
                         throughput dips & tail spikes, co-located 32 MiB KV tenant p99/p999).
  - W2 (`flow-churn`)  : 64 -> 16,384 transient flows across 100 us / 500 us / 1 ms active time
                         slots (reproducing CEIO Fig. 12: 16 active flows, 512 B echo).
  - W3 (`mixed-rpc`)   : Concurrent latency-sensitive RPC (256 B open-loop Poisson + 32 MiB KV
                         lookup) + large bulk burst flows (1400 B lazy release), sweeping CEIO
                         Table 4 ratios (3:1, 1:1, 1:3) with co-located 32 MiB KV tenant.

Strict audit guarantees (MRV-165/172/195/206):
  - Every run appended to `attempts.jsonl`; verified runs appended to `runs.jsonl`.
  - Synchronized measurement window: client WARMUP_DONE -> host RESET -> card RESET -> client GO.
  - Hard zero-tolerance integrity gates (lost/bad/torn/ooo/drops/page_faults == 0, PMU >= 99.95%).
  - Baseline restore + 21 s post-restore host/card EDAC & PCIe AER audit archived in `env.json`.
"""

import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv211")
LOG_DIR = ROOT / "logs/mrv211-2026-11-26"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
RUNS_PATH = LOG_DIR / "runs.jsonl"
SMOKE_PATH = LOG_DIR / "smoke.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = str(ROOT / "testbed/target/release/mrv211_host")
CARD_BIN_LOCAL = str(ROOT / "testbed/target/aarch64-unknown-linux-gnu/release/mrv211_card")
CARD_BIN_REMOTE = "/root/bench/mrv211_card"
MERC_SRC_DIR = ROOT / "testbed/mercury-client"
MERC_REMOTE_DIR = "/tmp/mrv211"
MERC_BIN_REMOTE = f"{MERC_REMOTE_DIR}/mrv211_client"

CARD_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mercury"]

ETH_DEV = "0002:02:00.0"
SDP_CARD_DEV = "0002:01:00.2"
SDP_HOST_BDF = "0000:99:02.0"

CARD_SYNC_HOST = "10.10.0.2"
CARD_SYNC_PORT = 7551
HOST_CTRL_HOST = "127.0.0.1"
HOST_CTRL_PORT = 7553

DATA_LANES = 4
UNIFIED_RX_DESC = 512
UNIFIED_R_EFF_PER_Q = 256
UNIFIED_CREDIT_BATCH = 32
CEIO_FAST_RX_DESC = 2048
CEIO_SLOW_RX_DESC = 512
CEIO_C_TOTAL = DATA_LANES * UNIFIED_R_EFF_PER_Q  # Exact matched LLC budget = 1024 pkts
CEIO_LAZY_BATCH = 64
CEIO_PULL_WINDOW = 128
DROQ_REFILL_THRESHOLD = 64

MAX_CLOCK_RETRIES = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_pitfall1_invariants() -> None:
    """Offline invariant guard matching `testbed::mrv211::verify_sdp_credit_safety`."""
    assert UNIFIED_RX_DESC >= 256 and (UNIFIED_RX_DESC & (UNIFIED_RX_DESC - 1)) == 0
    wmark = min(256, UNIFIED_RX_DESC // 4) if UNIFIED_RX_DESC < 2048 else 256
    usable_hw = UNIFIED_RX_DESC - wmark
    worst_occ = UNIFIED_R_EFF_PER_Q + (DROQ_REFILL_THRESHOLD - 1)
    assert worst_occ < usable_hw, f"Pitfall 1 violation: {worst_occ} >= {usable_hw}"
    assert 0 < UNIFIED_CREDIT_BATCH <= UNIFIED_R_EFF_PER_Q // 2
    assert CEIO_C_TOTAL == DATA_LANES * UNIFIED_R_EFF_PER_Q, "Fairness invariant: matched LLC budget"


@dataclass(frozen=True)
class RunSpec:
    suite: str
    policy: str
    workload: str
    pkt_bytes: int
    total_flows: int
    active_flows: int
    slot_us: int
    window: int
    burst_ms: int = 5
    idle_ms: int = 20
    idle_rate_kpps: float = 50.0
    rpc_bytes: int = 256
    bulk_bytes: int = 1400
    rpc_threads: int = 2
    rpc_rate_krps: float = 400.0
    host_work_ns: int = 0
    host_pause_every_ms: int = 0
    host_pause_us: int = 0
    coloc_kv_cpu: int = 0
    warmup_ms: int = 300
    duration_ms: int = 1500
    tag: str = ""

    def card_key(self) -> Tuple[Any, ...]:
        if self.policy == "card_echo":
            return ("card-echo", DATA_LANES, 0, 0)
        if self.policy == "large_sdp":
            return ("large-sdp", DATA_LANES, 0, 0)
        if self.policy == "unified":
            prio = 32 if self.workload == "mixed-rpc" else 0
            return ("unified", DATA_LANES, UNIFIED_R_EFF_PER_Q, prio)
        if self.policy == "unified_noprio":
            return ("unified", DATA_LANES, UNIFIED_R_EFF_PER_Q, 0)
        if self.policy == "ceio":
            return ("ceio", DATA_LANES, CEIO_C_TOTAL, 15)
        if self.policy == "ceio_zero_rmt":
            return ("ceio", DATA_LANES, CEIO_C_TOTAL, 0)
        raise ValueError(f"Unknown policy: {self.policy}")

    def host_key(self) -> Optional[Tuple[Any, ...]]:
        if self.policy == "card_echo":
            return None
        hmode = "unified" if self.policy.startswith("unified") else ("ceio" if self.policy.startswith("ceio") else "large-sdp")
        return (
            hmode,
            DATA_LANES,
            self.host_work_ns,
            self.host_pause_every_ms,
            self.host_pause_us,
            self.coloc_kv_cpu,
        )


def build_campaign_specs(suite: str, smoke: bool = False) -> List[RunSpec]:
    specs: List[RunSpec] = []

    if smoke:
        # 6-run quick verification matrix (unified vs ceio across W1, W2, W3)
        for pol in ("unified", "ceio"):
            specs.append(
                RunSpec(
                    suite="w1",
                    policy=pol,
                    workload="burst-onset",
                    pkt_bytes=1024,
                    total_flows=8,
                    active_flows=8,
                    slot_us=1000,
                    window=1024,
                    burst_ms=5,
                    idle_ms=20,
                    idle_rate_kpps=50.0,
                    host_pause_every_ms=25,
                    host_pause_us=400,
                    coloc_kv_cpu=41,
                    warmup_ms=200,
                    duration_ms=800,
                    tag="smoke_w1_1024B",
                )
            )
            specs.append(
                RunSpec(
                    suite="w2",
                    policy=pol,
                    workload="flow-churn",
                    pkt_bytes=512,
                    total_flows=4096,
                    active_flows=16,
                    slot_us=100,
                    window=1024,
                    warmup_ms=200,
                    duration_ms=800,
                    tag="smoke_w2_4096f_100us",
                )
            )
            specs.append(
                RunSpec(
                    suite="w3",
                    policy=pol,
                    workload="mixed-rpc",
                    pkt_bytes=512,
                    total_flows=8,
                    active_flows=8,
                    slot_us=1000,
                    window=1024,
                    rpc_bytes=256,
                    bulk_bytes=1400,
                    rpc_threads=4,
                    rpc_rate_krps=400.0,
                    coloc_kv_cpu=41,
                    warmup_ms=200,
                    duration_ms=800,
                    tag="smoke_w3_1to1",
                )
            )
        return specs

    # -------------------------------------------------------------------------
    # Suite W1: Microsecond-scale sudden burst onset (LLC leakage + phase pause)
    # -------------------------------------------------------------------------
    if suite in ("all", "w1"):
        for pkt_bytes in (512, 1024, 1400):
            for pol in ("unified", "ceio", "ceio_zero_rmt", "large_sdp"):
                specs.append(
                    RunSpec(
                        suite="w1",
                        policy=pol,
                        workload="burst-onset",
                        pkt_bytes=pkt_bytes,
                        total_flows=8,
                        active_flows=8,
                        slot_us=1000,
                        window=1024,
                        burst_ms=5,
                        idle_ms=20,
                        idle_rate_kpps=50.0,
                        host_pause_every_ms=25,
                        host_pause_us=400,
                        coloc_kv_cpu=41,
                        tag=f"w1_burst_{pkt_bytes}B",
                    )
                )

    # -------------------------------------------------------------------------
    # Suite W2: 64 -> 16,384 transient flows @ 100 us / 500 us / 1 ms (CEIO Fig. 12)
    # -------------------------------------------------------------------------
    if suite in ("all", "w2"):
        flow_counts = (64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384)
        for slot_us in (1000, 500, 100):
            for n_flows in flow_counts:
                for pol in ("unified", "ceio"):
                    specs.append(
                        RunSpec(
                            suite="w2",
                            policy=pol,
                            workload="flow-churn",
                            pkt_bytes=512,
                            total_flows=n_flows,
                            active_flows=16,
                            slot_us=slot_us,
                            window=1024,
                            tag=f"w2_churn_{n_flows}f_{slot_us}us",
                        )
                    )
        # Wire-ceiling card-echo reference at extremes (64 and 16,384 flows)
        for n_flows in (64, 16384):
            specs.append(
                RunSpec(
                    suite="w2",
                    policy="card_echo",
                    workload="flow-churn",
                    pkt_bytes=512,
                    total_flows=n_flows,
                    active_flows=16,
                    slot_us=100,
                    window=1024,
                    tag=f"w2_card_echo_{n_flows}f",
                )
            )

    # -------------------------------------------------------------------------
    # Suite W3: Latency-sensitive RPC + Large bulk burst mixed (CEIO Fig. 10a / Table 4)
    # -------------------------------------------------------------------------
    if suite in ("all", "w3"):
        # Exact CEIO Table 4 ratios over 8 client threads: 3:1 (6 RPC/2 Bulk), 1:1 (4/4), 1:3 (2/6)
        for ratio_tag, rpc_thr, rpc_krps in (
            ("3to1", 6, 600.0),
            ("1to1", 4, 400.0),
            ("1to3", 2, 200.0),
        ):
            for pol in ("unified", "unified_noprio", "ceio", "large_sdp"):
                specs.append(
                    RunSpec(
                        suite="w3",
                        policy=pol,
                        workload="mixed-rpc",
                        pkt_bytes=512,
                        total_flows=8,
                        active_flows=8,
                        slot_us=1000,
                        window=1024,
                        rpc_bytes=256,
                        bulk_bytes=1400,
                        rpc_threads=rpc_thr,
                        rpc_rate_krps=rpc_krps,
                        coloc_kv_cpu=41,
                        tag=f"w3_mixed_{ratio_tag}",
                    )
                )

    return specs


def build_card_cmd(c_key: Tuple[Any, ...]) -> Tuple[str, str]:
    cmode, n_data, param_a, param_b = c_key
    if cmode == "card-echo":
        lcores = f"3-{3 + n_data - 1}"
        cmd = (
            f"{CARD_BIN_REMOTE} -l {lcores} -a {ETH_DEV} --file-prefix=m211 -- "
            f"--mode card-echo --data-cores {n_data}"
        )
        return cmd, "MRV211_CARD READY mode=card-echo"
    if cmode == "large-sdp":
        lcores = f"3-{3 + n_data - 1}"
        cmd = (
            f"{CARD_BIN_REMOTE} -l {lcores} -a {ETH_DEV} -a {SDP_CARD_DEV} --file-prefix=m211 -- "
            f"--mode large-sdp --data-cores {n_data}"
        )
        return cmd, "MRV211_CARD READY mode=large-sdp"
    if cmode == "unified":
        lcores = f"3-{3 + n_data - 1}"
        r_eff, prio = param_a, param_b
        cmd = (
            f"{CARD_BIN_REMOTE} -l {lcores} -a {ETH_DEV} -a {SDP_CARD_DEV} --file-prefix=m211 -- "
            f"--mode unified --data-cores {n_data} --r-eff-per-q {r_eff} --prio-reserve {prio}"
        )
        return cmd, "MRV211_CARD READY mode=unified"
    if cmode == "ceio":
        # Dedicated 5th ARM core (lcore 7) for CEIO Flow Controller!
        lcores = f"3-{3 + n_data}"
        c_total, rmt_us = param_a, param_b
        cmd = (
            f"{CARD_BIN_REMOTE} -l {lcores} -a {ETH_DEV} -a {SDP_CARD_DEV} --file-prefix=m211 -- "
            f"--mode ceio --data-cores {n_data} --ceio-c-total {c_total} "
            f"--ceio-max-fast-flows 256 --ceio-ctrl-us 10 --ceio-rmt-rewrite-us {rmt_us} "
            f"--ceio-inactive-us 1000 --ceio-rr-period-us 250 --ceio-rr-batch 8 "
            f"--ceio-phase-pause-us 20 --ceio-lazy-batch {CEIO_LAZY_BATCH} "
            f"--ceio-pull-window {CEIO_PULL_WINDOW}"
        )
        return cmd, "MRV211_CARD READY mode=ceio"
    raise ValueError(f"Unknown card key: {c_key}")


def build_host_cmd(h_key: Tuple[Any, ...]) -> List[str]:
    hmode, lanes, work_ns, pause_every_ms, pause_us, coloc_cpu = h_key
    max_rings = lanes * 2 if hmode == "ceio" else lanes
    devarg = (
        f"{SDP_HOST_BDF},rx_poll_mem=1,rx_poll_tail_off=48,"
        f"ism_enable=1,allow_small_oq=1,max_rings={max_rings}"
    )
    cmd = [
        HOST_BIN,
        "-l",
        "40",
        "-a",
        devarg,
        "--socket-mem=0,2048",
        "--iova-mode=va",
        "--file-prefix=m211h",
        "--",
        "--mode",
        hmode,
        "--lanes",
        str(lanes),
        "--unified-rx-desc",
        str(UNIFIED_RX_DESC),
        "--r-eff-per-q",
        str(UNIFIED_R_EFF_PER_Q),
        "--credit-batch",
        str(UNIFIED_CREDIT_BATCH),
        "--ceio-fast-rx-desc",
        str(CEIO_FAST_RX_DESC),
        "--ceio-slow-rx-desc",
        str(CEIO_SLOW_RX_DESC),
        "--ceio-lazy-batch",
        str(CEIO_LAZY_BATCH),
        "--ceio-pull-window",
        str(CEIO_PULL_WINDOW),
        "--large-rx-desc",
        "4096",
        "--touch-payload",
        "true",
        "--work-ns",
        str(work_ns),
        "--pause-every-ms",
        str(pause_every_ms),
        "--pause-us",
        str(pause_us),
        "--coloc-kv-cpu",
        str(coloc_cpu),
        "--ts-bucket-us",
        "500",
        "--pmu",
        "true",
    ]
    return cmd


def build_client_cmd(spec: RunSpec, seed: int) -> str:
    return (
        f"{MERC_BIN_REMOTE} --dev mlx5_2 --sgid 3 --cpu 4 --threads 8 --card-queues {DATA_LANES} "
        f"--workload {spec.workload} --pkt-bytes {spec.pkt_bytes} "
        f"--rpc-bytes {spec.rpc_bytes} --bulk-bytes {spec.bulk_bytes} "
        f"--window {spec.window} --total-flows {spec.total_flows} "
        f"--active-flows {spec.active_flows} --slot-us {spec.slot_us} "
        f"--burst-ms {spec.burst_ms} --idle-ms {spec.idle_ms} --idle-rate-kpps {spec.idle_rate_kpps:.3f} "
        f"--rpc-threads {spec.rpc_threads} --rpc-rate-krps {spec.rpc_rate_krps:.3f} "
        f"--warmup-ms {spec.warmup_ms} --duration-ms {spec.duration_ms} "
        f"--ts-bucket-us 500 --seed {seed} --sync-reset"
    )


def sh(cmd: List[str], check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check, timeout=timeout)


def ssh_card(script: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def tcp_cmd(host: str, port: int, cmd: str, timeout: float = 15.0) -> Dict[str, Any]:
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


def deploy_binaries() -> None:
    if not pathlib.Path(HOST_BIN).exists():
        raise FileNotFoundError(f"Missing host binary: {HOST_BIN}")
    if not pathlib.Path(CARD_BIN_LOCAL).exists():
        raise FileNotFoundError(f"Missing card binary: {CARD_BIN_LOCAL}")
    sh(["scp", "-o", "BatchMode=yes", CARD_BIN_LOCAL, f"root@10.10.0.2:{CARD_BIN_REMOTE}"])
    ssh_card(f"chmod +x {CARD_BIN_REMOTE}")
    sh(MERC_SSH + [f"mkdir -p {MERC_REMOTE_DIR}"])
    sh(
        [
            "scp",
            "-o",
            "BatchMode=yes",
            str(MERC_SRC_DIR / "mrv211_client.rs"),
            str(MERC_SRC_DIR / "mrv211_verbs_shim.c"),
            f"mercury:{MERC_REMOTE_DIR}/",
        ]
    )
    sh(
        MERC_SSH
        + [
            f"cd {MERC_REMOTE_DIR} && "
            f"gcc -O3 -c mrv211_verbs_shim.c -o mrv211_verbs_shim.o && "
            f"rustc --edition=2024 -O mrv211_client.rs "
            f"-C link-arg=mrv211_verbs_shim.o -C link-arg=-libverbs -o mrv211_client"
        ]
    )


def read_merc_ethtool() -> Dict[str, int]:
    out = sh(
        MERC_SSH
        + [
            "ethtool -S ens6f1np1 | grep -E 'rx_out_of_buffer|rx_discards_phy|rx_crc_errors_phy|rx_err_lane' || true"
        ],
        check=False,
    ).stdout
    res: Dict[str, int] = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            try:
                res[k.strip()] = int(v.strip())
            except ValueError:
                pass
    return res


def read_clocks_edac() -> Dict[str, Any]:
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    khz = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore: Dict[str, int] = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("package_*")):
        uncore[p.name] = int((p / "current_freq_khz").read_text().strip())
    host_ue = [int(p.read_text().strip()) for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count"))]
    rp_aer: Dict[str, int] = {}
    rp = pathlib.Path("/sys/bus/pci/devices/0000:98:01.0")
    for name in ("aer_rootport_total_err_cor", "aer_rootport_total_err_nonfatal", "aer_rootport_total_err_fatal"):
        f = rp / name
        rp_aer[name] = int(f.read_text().strip()) if f.exists() else -1
    card_raw = ssh_card(
        "echo mc0_ue=$(cat /sys/devices/system/edac/mc/mc0/ue_count 2>/dev/null || echo 0) "
        "mc0_ce=$(cat /sys/devices/system/edac/mc/mc0/ce_count 2>/dev/null || echo 0) "
        "ea0_ue=$(cat /sys/devices/system/edac/ea/ea0/ue_count 2>/dev/null || echo 0) "
        "ea0_ce=$(cat /sys/devices/system/edac/ea/ea0/ce_count 2>/dev/null || echo 0) "
        "sei_total=$(dmesg | grep -c 'EDAC DEVICE4' || echo 0)"
    ).stdout.strip()
    card_edac: Dict[str, int] = {}
    for tok in card_raw.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            card_edac[k] = int(v)
    return {
        "ts": utc_now(),
        "saturn_cpu40_gov": gov,
        "saturn_cpu40_khz": khz,
        "saturn_uncore_khz": uncore,
        "saturn_edac_ue": host_ue,
        "saturn_edac_ue_sum": sum(host_ue),
        "saturn_rp_aer": rp_aer,
        "card_edac": card_edac,
    }


def card_pmu_delta(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    min_pct = 100.0
    for k, vb in b.items():
        va = a.get(k, {"val": 0, "ena": 0, "run": 0})
        dv = max(0, int(vb.get("val", 0)) - int(va.get("val", 0)))
        de = max(0, int(vb.get("ena", 0)) - int(va.get("ena", 0)))
        dr = max(0, int(vb.get("run", 0)) - int(va.get("run", 0)))
        pct = 100.0 if de == 0 else (100.0 * dr / de)
        min_pct = min(min_pct, pct)
        out[k] = {"val": dv, "pct": round(pct, 3)}
    out["min_running_pct"] = round(min_pct, 3)
    return out


class Stack:
    def __init__(self) -> None:
        self.card_key: Optional[Tuple[Any, ...]] = None
        self.host_key: Optional[Tuple[Any, ...]] = None
        self.host_proc: Optional[subprocess.Popen] = None

    def stop_host(self) -> None:
        if self.host_proc is not None:
            try:
                tcp_cmd(HOST_CTRL_HOST, HOST_CTRL_PORT, "QUIT", timeout=3.0)
            except Exception:
                pass
            try:
                self.host_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.host_proc.terminate()
                try:
                    self.host_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.host_proc.kill()
            self.host_proc = None
        sh(["rm", "-f", "/dev/hugepages/m211h*"], check=False)
        self.host_key = None

    def stop_card(self) -> None:
        self.stop_host()
        try:
            tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "QUIT", timeout=3.0)
            time.sleep(0.4)
        except Exception:
            pass
        ssh_card(
            "tmux send-keys -t m211 C-c 2>/dev/null || true; sleep 0.6; "
            "tmux kill-session -t m211 2>/dev/null || true; sleep 0.2; "
            "rm -f /dev/hugepages/m211*",
            check=False,
        )
        self.card_key = None

    def ensure_stack(self, spec: RunSpec) -> None:
        c_key = spec.card_key()
        h_key = spec.host_key()

        if self.card_key != c_key:
            self.stop_card()
            ssh_card(
                "tmux send-keys -t refl C-c 2>/dev/null || true; sleep 0.3; "
                "tmux kill-session -t refl 2>/dev/null || true; rm -f /dev/hugepages/refl*; "
                "ip link set eth0 down 2>/dev/null || true; "
                "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
                'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
                "drv2=$(basename $(readlink -f /sys/bus/pci/devices/0002:01:00.2/driver) 2>/dev/null || echo none); "
                'if [ "$drv2" != "vfio-pci" ]; then sh /root/bench/dpdk_up.sh 0002:01:00.2; fi'
            )
            cmd, ready_tok = build_card_cmd(c_key)
            ssh_card(
                f'rm -f /tmp/m211_card.log; '
                f'tmux new-session -d -s m211 "{cmd} > /tmp/m211_card.log 2>&1"; '
                f'for i in $(seq 1 100); do grep -q "{ready_tok}" /tmp/m211_card.log 2>/dev/null && break; sleep 0.15; done; '
                f'grep "{ready_tok}" /tmp/m211_card.log'
            )
            self.card_key = c_key

        if h_key is None:
            self.stop_host()
            return

        if self.host_key != h_key or self.host_proc is None or self.host_proc.poll() is not None:
            self.stop_host()
            cmd = build_host_cmd(h_key)
            log_path = pathlib.Path("/tmp/m211_host_run.log")
            with open(log_path, "w") as logf:
                self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
            for _ in range(100):
                if self.host_proc.poll() is not None:
                    raise RuntimeError(f"mrv211_host exited early:\n{log_path.read_text()}")
                txt = log_path.read_text()
                if "MRV211_HOST READY" in txt:
                    time.sleep(0.1)
                    self.host_key = h_key
                    return
                time.sleep(0.1)
            raise RuntimeError(f"mrv211_host ready timeout:\n{log_path.read_text()}")

    def restore_baseline(self) -> Dict[str, Any]:
        self.stop_card()
        sh(MERC_SSH + ["pkill -f mrv211_client 2>/dev/null || true"], check=False)
        out = ssh_card(
            f"{CARD_BIN_REMOTE} --restore 2>/dev/null || true; "
            "dpdk-devbind.py -b rvu_nicpf 0002:02:00.0; "
            "sleep 1.5; "
            "ip addr flush dev eth0 2>/dev/null || true; "
            "ip addr add 10.10.100.2/24 dev eth0; "
            "ip link set eth0 up; "
            "sleep 2.0; "
            "tmux kill-session -t refl 2>/dev/null || true; "
            "rm -f /dev/hugepages/refl*; "
            "tmux new-session -d -s refl /root/bench/reflector-rs -l 3 -a 0002:01:00.2 --file-prefix=refl; "
            "sleep 1.0; "
            "ethtool eth0 | grep -E 'Speed|Link detected'; "
            "ping -c 2 -W 1 10.10.100.1 | tail -n 2; "
            "tmux ls"
        ).stdout.strip()
        return {"restore_stdout": out}


def validate_record(spec: RunSpec, client: Dict[str, Any], host: Optional[Dict[str, Any]], card: Dict[str, Any], card_pmu: Dict[str, Any], merc_err_delta: Dict[str, int]) -> Tuple[List[str], List[str]]:
    """Returns `(hard_errors, clock_errors)`."""
    hard_errs: List[str] = []
    clock_errs: List[str] = []

    for fld in ("lost", "bad", "torn", "ooo", "send_err"):
        if int(client.get(fld, 0)) != 0:
            hard_errs.append(f"client.{fld}={client.get(fld)}")
    if int(client.get("done", 0)) == 0:
        hard_errs.append("client.done=0")

    for k, dv in merc_err_delta.items():
        if dv > 0:
            hard_errs.append(f"merc.{k}_delta={dv}")

    for fld in ("bad_wire", "bad_sdp", "eth_tx_drops", "sdp_tx_drops", "card_buf_drops"):
        if int(card.get(fld, 0)) != 0:
            hard_errs.append(f"card.{fld}={card.get(fld)}")
    eth_st = card.get("eth_stats") or {}
    for fld in ("imissed", "ierrors", "oerrors", "rx_nombuf"):
        if int(eth_st.get(fld, 0)) != 0:
            hard_errs.append(f"card.eth.{fld}={eth_st.get(fld)}")
    if float(card_pmu.get("min_running_pct", 100.0)) < 99.95:
        hard_errs.append(f"card_pmu_pct={card_pmu.get('min_running_pct')}")

    if host is not None:
        for fld in ("bad", "torn", "ooo", "tx_drops"):
            if int(host.get(fld, 0)) != 0:
                hard_errs.append(f"host.{fld}={host.get(fld)}")
        pmu = host.get("pmu") or {}
        if int(pmu.get("page_faults", 0)) != 0:
            hard_errs.append(f"host.page_faults={pmu.get('page_faults')}")
        if float(pmu.get("min_running_pct", 100.0)) < 99.95:
            hard_errs.append(f"host_pmu_pct={pmu.get('min_running_pct')}")
        cpu_ghz = float(pmu.get("cpu_ghz", 0.0))
        mesh_ghz = float(pmu.get("mesh_ghz", 0.0))
        if cpu_ghz < 3.85:
            clock_errs.append(f"cpu40_ghz={cpu_ghz:.3f}")
        if mesh_ghz < 2.18:
            clock_errs.append(f"mesh_ghz={mesh_ghz:.3f}")

    return hard_errs, clock_errs


def execute_single_run(stack: Stack, spec: RunSpec, rep: int, seed: int, epoch: int) -> Dict[str, Any]:
    stack.ensure_stack(spec)
    tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "RESET_PEERS")
    merc_eth_before = read_merc_ethtool()

    mcmd = build_client_cmd(spec, seed)
    p = subprocess.Popen(
        MERC_SSH + [mcmd],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    lines: List[str] = []
    warmup_seen = False
    t0 = time.time()
    try:
        while time.time() - t0 < 30.0:
            line = p.stdout.readline() if p.stdout else ""
            if not line:
                if p.poll() is not None:
                    break
                time.sleep(0.01)
                continue
            lines.append(line)
            if "WARMUP_DONE" in line:
                warmup_seen = True
                break
        if not warmup_seen:
            p.kill()
            out, err = p.communicate(timeout=5)
            raise RuntimeError(f"mrv211_client missed WARMUP_DONE:\n{''.join(lines)}{out}\n{err}")

        # Exact epoch-synchronized reset order: HOST FIRST, then CARD SECOND with matching epoch!
        if spec.policy != "card_echo":
            tcp_cmd(HOST_CTRL_HOST, HOST_CTRL_PORT, f"RESET {epoch}")
        card_reset_resp = tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, f"RESET {epoch}")
        card_pmu_start = card_reset_resp.get("pmu") or {}

        assert p.stdin is not None
        p.stdin.write("GO\n")
        p.stdin.flush()

        out, err = p.communicate(timeout=60)
        if p.returncode != 0:
            raise RuntimeError(f"mrv211_client rc={p.returncode}:\n{out}\n{err}")
        json_line = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("{")]
        if not json_line:
            raise RuntimeError(f"mrv211_client emitted no JSON:\n{out}\n{err}")
        client_json = json.loads(json_line[-1])
        host_stats = tcp_cmd(HOST_CTRL_HOST, HOST_CTRL_PORT, "STATS") if spec.policy != "card_echo" else None
        card_stats = tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "STATS")
        card_pmu = card_pmu_delta(card_pmu_start, card_stats.get("pmu") or {})
        merc_eth_after = read_merc_ethtool()
        merc_delta = {k: merc_eth_after.get(k, 0) - merc_eth_before.get(k, 0) for k in merc_eth_after}

        hard_errs, clock_errs = validate_record(spec, client_json, host_stats, card_stats, card_pmu, merc_delta)
        return {
            "ts": utc_now(),
            "rep": rep,
            "seed": seed,
            "spec": asdict(spec),
            "client": client_json,
            "host": host_stats,
            "card": card_stats,
            "card_pmu_delta": card_pmu,
            "merc_ethtool_delta": merc_delta,
            "hard_errors": hard_errs,
            "clock_errors": clock_errs,
            "accepted": len(hard_errs) == 0 and len(clock_errs) == 0,
        }
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
        raise


def append_jsonl(path: pathlib.Path, rec: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def run_offline_self_test() -> None:
    """Pure offline validation: checks Pitfall 1 invariants, full campaign matrix, and command syntax."""
    verify_pitfall1_invariants()
    for suite in ("w1", "w2", "w3", "all"):
        specs = build_campaign_specs(suite, smoke=False)
        assert len(specs) > 0
        for s in specs:
            c_cmd, c_tok = build_card_cmd(s.card_key())
            assert c_tok.startswith("MRV211_CARD READY")
            assert "--mode" in c_cmd
            hk = s.host_key()
            if hk is not None:
                h_cmd = build_host_cmd(hk)
                assert h_cmd[0] == HOST_BIN
                assert "--mode" in h_cmd
            cl_cmd = build_client_cmd(s, seed=211)
            assert "--sync-reset" in cl_cmd
    smoke = build_campaign_specs("all", smoke=True)
    assert len(smoke) == 6

    # Verify validation logic catches integrity/clock errors accurately
    sample_spec = smoke[0]
    ok_h, ok_c = validate_record(
        sample_spec,
        {"done": 1000, "lost": 0, "bad": 0, "torn": 0, "ooo": 0, "send_err": 0},
        {"bad": 0, "torn": 0, "ooo": 0, "tx_drops": 0, "pmu": {"page_faults": 0, "min_running_pct": 100.0, "cpu_ghz": 3.90, "mesh_ghz": 2.20}},
        {"bad_wire": 0, "bad_sdp": 0, "eth_tx_drops": 0, "sdp_tx_drops": 0, "card_buf_drops": 0, "eth_stats": {"imissed": 0, "ierrors": 0, "oerrors": 0, "rx_nombuf": 0}},
        {"min_running_pct": 100.0},
        {"rx_discards_phy": 0},
    )
    assert ok_h == [] and ok_c == []

    bad_h, bad_c = validate_record(
        sample_spec,
        {"done": 1000, "lost": 0, "bad": 0, "torn": 0, "ooo": 1, "send_err": 0},
        {"bad": 0, "torn": 0, "ooo": 0, "tx_drops": 0, "pmu": {"page_faults": 0, "min_running_pct": 100.0, "cpu_ghz": 3.50, "mesh_ghz": 2.20}},
        {"bad_wire": 0, "bad_sdp": 0, "eth_tx_drops": 0, "sdp_tx_drops": 0, "card_buf_drops": 0, "eth_stats": {"imissed": 0, "ierrors": 0, "oerrors": 0, "rx_nombuf": 0}},
        {"min_running_pct": 100.0},
        {"rx_discards_phy": 0},
    )
    assert "client.ooo=1" in bad_h and any("cpu40_ghz" in e for e in bad_c)
    print(f"SELF-TEST OK: all invariants verified; full campaign = {len(build_campaign_specs('all'))} specs/rep ({3 * len(build_campaign_specs('all'))} runs @ n=3), smoke = {len(smoke)} runs")


def main() -> int:
    ap = argparse.ArgumentParser(description="MRV-211 (E3) CN106 + SDP vs CEIO Experiment Harness")
    ap.add_argument("--suite", choices=("all", "w1", "w2", "w3"), default="all")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--smoke", action="store_true", help="Run 6-point smoke matrix to smoke.jsonl")
    ap.add_argument("--dry-run", action="store_true", help="Print planned matrix & commands offline (zero HW access)")
    ap.add_argument("--self-test", action="store_true", help="Run offline harness self-test (zero HW access)")
    ap.add_argument("--deploy", action="store_true", help="Deploy card binary and compile mercury client before running")
    ap.add_argument("--deploy-only", action="store_true", help="Only deploy binaries to card and mercury")
    ap.add_argument("--restore-only", action="store_true", help="Only restore card baseline & run 21 s EDAC audit")
    args = ap.parse_args()

    verify_pitfall1_invariants()

    if args.self_test:
        run_offline_self_test()
        return 0

    specs = build_campaign_specs(args.suite, smoke=args.smoke)
    reps = 1 if args.smoke else args.reps

    if args.dry_run:
        print(f"MRV-211 DRY-RUN: suite={args.suite} smoke={args.smoke} reps={reps} specs_per_rep={len(specs)} total_runs={reps * len(specs)}")
        print(f"  LLC budget parity: Unified {DATA_LANES}x{UNIFIED_R_EFF_PER_Q} = {DATA_LANES * UNIFIED_R_EFF_PER_Q} pkts vs CEIO C_total = {CEIO_C_TOTAL} pkts")
        seen_card = set()
        seen_host = set()
        for idx, s in enumerate(specs):
            ck = s.card_key()
            hk = s.host_key()
            if ck not in seen_card:
                seen_card.add(ck)
                ccmd, _ = build_card_cmd(ck)
                print(f"  [card cfg] {ck}: {ccmd}")
            if hk is not None and hk not in seen_host:
                seen_host.add(hk)
                print(f"  [host cfg] {hk}: {' '.join(build_host_cmd(hk))}")
            if idx < 6 or idx == len(specs) - 1:
                print(f"  [spec #{idx:02d}] {s.tag} policy={s.policy} -> {build_client_cmd(s, 211 + idx)}")
        return 0

    if args.deploy or args.deploy_only:
        deploy_binaries()
        if args.deploy_only:
            print("Deploy complete.")
            return 0

    stack = Stack()
    if args.restore_only:
        res = stack.restore_baseline()
        time.sleep(21.0)
        audit = read_clocks_edac()
        print(json.dumps({"restore": res, "post_21s_audit": audit}, indent=2))
        return 0

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SMOKE_PATH if args.smoke else RUNS_PATH
    env_doc: Dict[str, Any] = {
        "issue": "MRV-211",
        "started_utc": utc_now(),
        "suite": args.suite,
        "smoke": args.smoke,
        "reps": reps,
        "specs_per_rep": len(specs),
        "pre_audit": read_clocks_edac(),
    }

    try:
        # Interleaved repetitions: run rep 0..reps-1 across all specs in order
        run_epoch = 1
        for rep in range(reps):
            for idx, spec in enumerate(specs):
                clock_retries = 0
                while True:
                    seed = 211_000 + rep * 1000 + idx * 10 + clock_retries
                    rec = execute_single_run(stack, spec, rep, seed, run_epoch)
                    rec["reset_epoch"] = run_epoch
                    run_epoch += 1
                    rec["clock_retries_before_accept"] = clock_retries
                    append_jsonl(ATTEMPTS_PATH, rec)
                    if rec["accepted"]:
                        append_jsonl(out_path, rec)
                        cl = rec["client"]
                        print(
                            f"[rep={rep} {idx + 1}/{len(specs)}] OK {spec.tag} {spec.policy}: "
                            f"{cl['tx_gbps_wire']:.2f} Gbps ({cl['mpps']:.2f} Mpps) "
                            f"p50={cl['p50_us']:.1f} p99={cl['p99_us']:.1f} p999={cl['p999_us']:.1f} us "
                            f"slow={cl['slow_frac_pct']:.1f}% leak={cl['leak_flagged_acks']}",
                            flush=True,
                        )
                        break
                    if rec["hard_errors"]:
                        raise RuntimeError(
                            f"HARD INTEGRITY FAILURE on {spec.tag} {spec.policy}: {rec['hard_errors']}"
                        )
                    clock_retries += 1
                    if clock_retries > MAX_CLOCK_RETRIES:
                        raise RuntimeError(
                            f"Clock/mesh instability persisted after {MAX_CLOCK_RETRIES} retries on {spec.tag}: {rec['clock_errors']}"
                        )
                    print(
                        f"[rep={rep} {idx + 1}/{len(specs)}] CLOCK RETRY {clock_retries} ({rec['clock_errors']}), sleeping 3s...",
                        flush=True,
                    )
                    time.sleep(3.0)
    finally:
        restore_info = stack.restore_baseline()
        time.sleep(21.0)
        env_doc["finished_utc"] = utc_now()
        env_doc["restore"] = restore_info
        env_doc["post_21s_audit"] = read_clocks_edac()
        ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
