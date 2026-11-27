#!/usr/bin/env python3
"""MRV-228 Dual-Host 100GbE Experiment Harness:
Multi-Flow Multiplexing (F = 16 / 64 / 256 / 1,024 flows sharing 4 standard SDP HW queues).

5-Arm Single-Variable Showdown on the exact same CN106 + standard SDP + saturn + mercury link:
  1. `bp1_stop`    : BP=1 (`0xFF00`) + allow_small_oq=1 (D=256, compact 3.7 MiB stack LIFO pool)
                     + nb_txq=4 (`ROC_NIX_TM_SDP` 0x3008..0x300b) + fair small sdp_tx_desc=256;
                     host stops draining HW ring when a stalled/slow flow sits at head -> triggers
                     real HW backpressure (preserves DDIO/KV, causes severe in-queue HoL on RPCs).
  2. `bp1_drain`   : Identical HW (`BP=1`, D=256, nb_txq=4, sdp_tx_desc=256) + large LIFO stack
                     pool; host continuously drains HW ring into per-flow host software queues to
                     avoid HoL -> BP=1 never fires, bursts flood host memory (DDIO/KV collapse).
  3. `large_sdp`   : Default passive Large SDP (BP=0, D=2048, ring_sp_sc pool) + per-flow staging.
  4. `ceio`        : Fortified CEIO §4.1-4.2 dual-path (4 Fast D=2048 + 4 Slow D=512, 5th CN106
                     ARM control core, per-flow BAR4 visibility, 2P1C SW merge, async_recv pull).
  5. `per_flow_cr` : OUR DESIGN — identical D=256 compact 3.7 MiB stack LIFO pool + nb_txq=4 +
                     sdp_tx_desc=256 + 64 B packed BAR4 per-flow fence-free MOVDIR64B credit ring
                     (16 flows/line -> 1,024 flows in 4 KiB BAR4), gating slow/elephant flows on
                     CN106 BEFORE the shared HW SDP queue -> zero HoL, 0.00% loss, ~99-100% DDIO,
                     ~41 ns co-located 32 MiB KV p99, zero CEIO dual-path tax!

Scenarios:
  - `kv_solo` : Unloaded Socket-1 cpu41 32 MiB 2 MiB-hugepage KV baseline (~41 ns p99).
  - Scenario A (`stall`): Minority slow/stalled flow (1/HW-q, 48 Gbps) + (F-4) healthy 256 B
    open-loop Poisson RPCs (200 krps):
      * A1: F = 16 / 64 / 256 / 1,024 @ stall = 4 ms / 40 ms (all 5 arms)
      * A2: stall = 1 ms / 20 ms & 10 ms / 60 ms @ F = 256 (all 5 arms)
  - Scenario B (`burst`): Wire-speed 16 / 64 MiB elephant bursts (host consumer 16 Gbps) +
    (F-4) healthy 256 B open-loop Poisson RPCs (200 krps):
      * B1: 64 MiB burst / 60 ms across F = 16 / 64 / 256 / 1,024 (all 5 arms)
      * B2: 16 MiB burst / 25 ms @ F = 256 (all 5 arms)

Safety & Audit Guarantees (AGENTS.md + MRV-26/132/217):
  - Single 64-bit aligned loads/stores ONLY on the 6 kernel-source-verified CN106 SDP CSRs.
  - Pre-run 6-CSR snapshot recorded; BP=1 cleared strictly via BPFLR_D=0 + VF0 VFIO FLR + BPFLR_D=1.
  - Pre/post CSR equality + NIX tm_topo (0x3008..0x300b) verified on every run.
  - Zero silent retries: every attempt logged to `attempts.jsonl`, verified runs to `runs.jsonl`.
  - Guaranteed `finally` restores all 6 CSRs, BAR4 slot 15, eth0 100G UP, refl tmux, cleans
    hugepages & mercury /tmp/mrv228*, and runs 21 s post-restore EDAC/AER audit into `env.json`.
"""

import argparse
import gzip
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv228")
LOG_DIR = ROOT / "logs/mrv228-2026-11-27"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
RUNS_PATH = LOG_DIR / "runs.jsonl"
SMOKE_PATH = LOG_DIR / "smoke.jsonl"
ENV_PATH = LOG_DIR / "env.json"
RUN_LOG_PATH = LOG_DIR / "run.log"

BIN_DIR = ROOT / "testbed/target/release"
CARD_BIN_DIR = ROOT / "testbed/target/aarch64-unknown-linux-gnu/release"
HOST_BIN = BIN_DIR / "mrv228_host"
VFIO_FLR_BIN = BIN_DIR / "mrv228_vfio_flr"
CARD_BIN_LOCAL = CARD_BIN_DIR / "mrv228_card"
CARD_BIN_REMOTE = "/root/bench/mrv228_card"
MERC_SRC_DIR = ROOT / "testbed/mercury-client"
MERC_REMOTE_DIR = "/tmp/mrv228"
MERC_BIN_REMOTE = f"{MERC_REMOTE_DIR}/mrv228_client"

CARD_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mercury"]

CARD_SYNC_HOST = "10.10.0.2"
CARD_SYNC_PORT = 7561
HOST_CTRL_HOST = "127.0.0.1"
HOST_CTRL_PORT = 7563

ETH_DEV = "0002:02:00.0"
SDP_CARD_DEV = "0002:01:00.2"
SDP_HOST_BDF = "0000:99:02.0"

NUMACTL_FALLBACK = "/nix/store/00p2pzg3i0bdlg9iab09jyr46lnvpi6n-numactl-2.0.18/bin/numactl"

MIN_CORE_GHZ = 3.85
MIN_MESH_GHZ = 2.18
MAX_RETRIES = 4
EPOCH_GEN = 228000


def resolve_numactl() -> str:
    found = shutil.which("numactl")
    if found:
        return found
    if pathlib.Path(NUMACTL_FALLBACK).is_file():
        return NUMACTL_FALLBACK
    raise FileNotFoundError(f"numactl not found in PATH or {NUMACTL_FALLBACK}")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    line = f"[{utc_now()}] {msg}"
    print(line, flush=True)
    with open(RUN_LOG_PATH, "a") as f:
        f.write(line + "\n")


def sh(
    cmd: list[str],
    check: bool = True,
    timeout: int = 120,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, text=True, input=input_text, capture_output=True, check=False, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(
            f"CMD FAILED ({r.returncode}): {' '.join(cmd)}\nSTDOUT:\n{r.stdout[-800:]}\nSTDERR:\n{r.stderr[-800:]}"
        )
    return r


def ssh_card(script: str, check: bool = True, timeout: int = 60, input_text: str | None = None) -> subprocess.CompletedProcess:
    return sh(CARD_SSH + [script], check=check, timeout=timeout, input_text=input_text)


def ssh_merc(script: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    return sh(MERC_SSH + [script], check=check, timeout=timeout)


def tcp_cmd(host: str, port: int, cmd: str, timeout: float = 15.0) -> dict:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((cmd + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            c = s.recv(65536)
            if not c:
                break
            buf += c
        return json.loads(buf.decode().strip())


def append_jsonl(path: pathlib.Path, obj: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")
        f.flush()


# -----------------------------------------------------------------------------
# Kernel-Source-Verified CN106 SDP CSR Helpers (identical to MRV-217!)
# -----------------------------------------------------------------------------

def read_all_safe_csrs() -> dict[str, str]:
    py = """
import ctypes, json, mmap, os
ps = os.sysconf("SC_PAGE_SIZE")
REGS = [
    ("SDPX_EPFX_RINFO(0)",       0x86E0800209F0),
    ("SDPX_MACX_PF_RING_CTL(0)", 0x86E08002C000),
    ("SDPX_LINK_CFG",            0x86E0C0080180),
    ("SDPX_GBL_CONTROL",         0x86E0C0080200),
    ("SDPX_OUT_BP_ENX_W1S(0)",   0x86E0C0080280),
    ("SDPX_OUT_BP_ENX_W1S(1)",   0x86E0C0080290),
]
out = {}
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
try:
    for name, phys in REGS:
        page = phys & ~(ps - 1)
        poff = phys & (ps - 1)
        m = mmap.mmap(fd, ps, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=page)
        try:
            q = (ctypes.c_uint64 * (ps // 8)).from_buffer(m)
            val = q[poff >> 3]
            del q
            out[name] = f"{val:#018x}"
        finally:
            m.close()
finally:
    os.close(fd)
print(json.dumps(out))
"""
    return json.loads(ssh_card("python3 -", input_text=py).stdout.strip())


def csr_read_pair() -> tuple[int, int]:
    py = """
import ctypes, mmap, os
ps = os.sysconf("SC_PAGE_SIZE")
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, ps, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x86E0C0080000)
q = (ctypes.c_uint64 * (ps // 8)).from_buffer(m)
print(f"{q[0x200>>3]:#x} {q[0x280>>3]:#x}")
del q; m.close(); os.close(fd)
"""
    a, b = ssh_card("python3 -", input_text=py).stdout.strip().split()
    return int(a, 16), int(b, 16)


def csr_clear_bp() -> tuple[int, int]:
    """Clear VF0 SDPX_OUT_BP_EN bits cleanly via BPFLR_D=0 + VFIO FLR + BPFLR_D=1 (host MUST be stopped!)."""
    py0 = """
import ctypes, mmap, os
ps = os.sysconf("SC_PAGE_SIZE")
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, ps, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x86E0C0080000)
q = (ctypes.c_uint64 * (ps // 8)).from_buffer(m)
q[0x200>>3] = 0
del q; m.close(); os.close(fd)
"""
    py4 = py0.replace("= 0", "= 4")
    ssh_card("python3 -", input_text=py0)
    try:
        sh([str(VFIO_FLR_BIN)])
        time.sleep(0.1)
    finally:
        ssh_card("python3 -", input_text=py4)
    gbl, bp0 = csr_read_pair()
    if gbl != 0x4 or bp0 != 0x0:
        raise RuntimeError(f"CSR clear failed: GBL={gbl:#018x} BP0={bp0:#018x}")
    return gbl, bp0


def csr_set_bp(mask: int) -> tuple[int, int]:
    """Set VF0-only SDPX_OUT_BP_ENX_W1S(0) bits (ONLY bits [15:8]; NEVER touch ens1 [7:0]!)."""
    if (mask & ~0xFF00) != 0:
        raise ValueError(f"Refusing to touch non-VF0 ring bits in mask={mask:#x}")
    gbl, bp0 = csr_read_pair()
    if gbl == 0x4 and bp0 == mask:
        return gbl, bp0
    csr_clear_bp()
    if mask != 0:
        py = f"""
import ctypes, mmap, os
ps = os.sysconf("SC_PAGE_SIZE")
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, ps, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x86E0C0080000)
q = (ctypes.c_uint64 * (ps // 8)).from_buffer(m)
q[0x280>>3] = {mask}
del q; m.close(); os.close(fd)
"""
        ssh_card("python3 -", input_text=py)
    gbl, bp0 = csr_read_pair()
    if gbl != 0x4 or bp0 != mask:
        raise RuntimeError(f"CSR verify failed: GBL={gbl:#018x} BP0={bp0:#018x} want={mask:#x}")
    return gbl, bp0


def verify_sdp1_1_tm_topo(expected_tl4: list[str]) -> list[str]:
    out = ssh_card(
        "lf=$(awk '/^PF0:VF1/{print $3}' /sys/kernel/debug/cn10k/rsrc_alloc); "
        "echo $lf > /sys/kernel/debug/cn10k/nix/tm_topo && "
        "cat /sys/kernel/debug/cn10k/nix/tm_topo | grep -E 'SDP_LINK_CFG'"
    ).stdout.strip()
    tl4_vals = [l.split("=")[-1].strip() for l in out.splitlines() if "SDP_LINK_CFG" in l]
    if tl4_vals != expected_tl4:
        raise RuntimeError(f"NIX TM topo mismatch: got={tl4_vals} expected={expected_tl4}")
    return tl4_vals


def parse_aer_total(path: pathlib.Path) -> int:
    if not path.exists():
        return -1
    txt = path.read_text().strip()
    if txt.isdigit():
        return int(txt)
    for line in txt.splitlines():
        if line.startswith("TOTAL_ERR_"):
            return int(line.split()[-1])
    return 0


def read_env_snapshot() -> dict:
    uncore = {}
    for d in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("package_*")):
        uncore[d.name] = {
            "min": int((d / "min_freq_khz").read_text().strip()),
            "max": int((d / "max_freq_khz").read_text().strip()),
            "cur": int((d / "current_freq_khz").read_text().strip()),
        }
    host_ue = {}
    for mc in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*")):
        p = mc / "ue_count"
        if p.exists():
            host_ue[mc.name] = int(p.read_text().strip())
    rp = pathlib.Path("/sys/bus/pci/devices/0000:98:01.0")
    saturn = {
        "cpu40_gov": pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip(),
        "cpu40_khz": int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip()),
        "uncore_khz": uncore,
        "host_edac_ue": host_ue,
        "host_edac_ue_sum": sum(host_ue.values()),
        "rp_98_01_aer": {
            "cor": parse_aer_total(rp / "aer_rootport_total_err_cor"),
            "nonfatal": parse_aer_total(rp / "aer_rootport_total_err_nonfatal"),
            "fatal": parse_aer_total(rp / "aer_rootport_total_err_fatal"),
        },
        "hugepages_m228_leftover": len(list(pathlib.Path("/dev/hugepages").glob("m228*"))),
    }
    card_raw = ssh_card(
        'echo "ETH0_OPER=$(cat /sys/class/net/eth0/operstate 2>/dev/null || echo unbound)"; '
        'echo "ETH0_SPEED=$(ethtool eth0 2>/dev/null | awk \'/Speed:/{print $2}\' || echo n/a)"; '
        'echo "PEM_WC=$(lsmod | grep -c \'^pem_wc \' || true)"; '
        'echo "REFL=$(tmux has-session -t refl 2>/dev/null && echo running || echo stopped)"; '
        'echo "EDAC_mc0=$(cat /sys/devices/system/edac/mc/mc0/ue_count 2>/dev/null || echo 0)"; '
        'echo "EDAC_llc0=$(cat /sys/devices/system/edac/llc/llc0/ue_count 2>/dev/null || echo 0)"; '
        'echo "EDAC_mdc0=$(cat /sys/devices/system/edac/mdc/mdc0/ue_count 2>/dev/null || echo 0)"; '
        'echo "EDAC_gic0=$(cat /sys/devices/system/edac/gic/gic0/ue_count 2>/dev/null || echo 0)"; '
        'echo "EDAC_ea0=$(cat /sys/devices/system/edac/ea/ea0/ue_count 2>/dev/null || echo 0)"; '
        'echo "EDAC_CPU_SUM=$(awk \'{s+=$1} END{print s+0}\' /sys/devices/system/edac/cpu/cpu*/ue_count 2>/dev/null || echo 0)"; '
        'echo "HP_M228=$(ls /dev/hugepages/m228* 2>/dev/null | wc -l)"'
    ).stdout.strip()
    card: dict[str, Any] = {}
    for line in card_raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            card[k] = int(v) if v.isdigit() else v
    card["csrs"] = read_all_safe_csrs()

    merc_raw = ssh_merc(
        'echo "oper=$(cat /sys/class/net/ens6f1np1/operstate 2>/dev/null || echo unknown)"; '
        'echo "m228_procs=$(pgrep -xc mrv228_client 2>/dev/null || true)"; '
        'echo "tmp_mrv228=$(ls -d /tmp/mrv228* 2>/dev/null | wc -l)"; '
        'ethtool -S ens6f1np1 2>/dev/null | grep -E "rx_global_pause|tx_global_pause|rx_crc_errors_phy" || true'
    ).stdout.strip()
    merc: dict[str, Any] = {}
    for line in merc_raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            merc[k] = int(v) if v.isdigit() else v
        elif ":" in line:
            k, v = line.split(":", 1)
            merc[k.strip()] = int(v.strip())
    return {"ts_utc": utc_now(), "saturn": saturn, "cn106": card, "mercury": merc}


def read_merc_phy() -> dict[str, int]:
    out = ssh_merc(
        'ethtool -S ens6f1np1 2>/dev/null | grep -E "rx_crc_errors_phy|rx_global_pause|tx_global_pause|tx_packets_phy|rx_packets_phy" || true'
    ).stdout
    d: dict[str, int] = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = int(v.strip())
    return d


# -----------------------------------------------------------------------------
# Spec Definitions & Campaign Matrix
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class RunSpec:
    part: str
    arm: str             # kv_solo | bp1_stop | bp1_drain | large_sdp | ceio | per_flow_cr
    scenario: str        # solo | stall | burst
    total_flows: int
    stall_ms: int = 0
    stall_period_ms: int = 40
    bulk_send_gbps: float = 48.0
    bulk_rate_gbps: float = 0.0
    burst_mib: int = 0
    burst_period_ms: int = 60
    rpc_rate_krps: float = 200.0
    warmup_ms: int = 250
    duration_ms: int = 1200
    tag: str = ""

    def bp_mask(self) -> int:
        return 0xFF00 if self.arm in ("bp1_stop", "bp1_drain") else 0x0000

    def card_mode(self) -> str:
        if self.arm in ("bp1_stop", "bp1_drain"):
            return "native-bp"
        if self.arm == "large_sdp":
            return "large-sdp"
        if self.arm == "ceio":
            return "ceio"
        if self.arm == "per_flow_cr":
            return "per-flow-cr"
        return "none"

    def host_mode(self) -> str:
        return {
            "kv_solo": "kv-solo",
            "bp1_stop": "bp1-stop",
            "bp1_drain": "bp1-drain",
            "large_sdp": "large-sdp",
            "ceio": "ceio",
            "per_flow_cr": "per-flow-cr",
        }[self.arm]

    def ceio_inactive_us(self) -> int:
        if self.arm != "ceio":
            return 0
        healthy = max(1, self.total_flows - 4)
        mean_gap_us = (healthy / max(1.0, self.rpc_rate_krps)) * 1000.0
        return max(10_000, int(round(8.0 * mean_gap_us)))

    def card_key(self) -> tuple[Any, ...]:
        if self.arm == "kv_solo":
            return ("none",)
        sdp_tx_desc = 2048 if self.arm in ("large_sdp", "ceio") else 256
        return (self.card_mode(), sdp_tx_desc, self.ceio_inactive_us())

    def host_key(self) -> tuple[Any, ...]:
        if self.arm == "kv_solo":
            return ("kv-solo",)
        return (
            self.host_mode(),
            self.bp_mask(),
            self.stall_ms,
            self.stall_period_ms,
            round(self.bulk_rate_gbps, 3),
        )


ALL_ARMS = ("bp1_stop", "bp1_drain", "large_sdp", "ceio", "per_flow_cr")


def build_specs(smoke: bool = False) -> list[RunSpec]:
    specs: list[RunSpec] = []
    if smoke:
        specs.append(RunSpec(part="S0", arm="kv_solo", scenario="solo", total_flows=0, tag="smoke_kv_solo"))
        for arm in ALL_ARMS:
            specs.append(
                RunSpec(
                    part="SA",
                    arm=arm,
                    scenario="stall",
                    total_flows=256,
                    stall_ms=4,
                    stall_period_ms=40,
                    bulk_send_gbps=48.0,
                    warmup_ms=200,
                    duration_ms=800,
                    tag="smoke_A_F256_s4ms",
                )
            )
            specs.append(
                RunSpec(
                    part="SB",
                    arm=arm,
                    scenario="burst",
                    total_flows=256,
                    bulk_rate_gbps=16.0,
                    burst_mib=64,
                    burst_period_ms=60,
                    warmup_ms=200,
                    duration_ms=800,
                    tag="smoke_B_F256_b64m",
                )
            )
        return specs

    # Unloaded 32 MiB KV solo baseline
    specs.append(RunSpec(part="S0", arm="kv_solo", scenario="solo", total_flows=0, tag="kv_solo"))

    # Scenario A1: Flow count sweep F = 16 / 64 / 256 / 1,024 @ 4 ms / 40 ms stall (all 5 arms)
    for f in (16, 64, 256, 1024):
        for arm in ALL_ARMS:
            specs.append(
                RunSpec(
                    part="A1_FLOWS",
                    arm=arm,
                    scenario="stall",
                    total_flows=f,
                    stall_ms=4,
                    stall_period_ms=40,
                    bulk_send_gbps=48.0,
                    tag=f"A1_F{f}_s4ms",
                )
            )

    # Scenario A2: Stall duration sweep 1 ms / 20 ms and 10 ms / 60 ms @ F = 256 (all 5 arms)
    for s_ms, p_ms in ((1, 20), (10, 60)):
        for arm in ALL_ARMS:
            specs.append(
                RunSpec(
                    part="A2_STALL",
                    arm=arm,
                    scenario="stall",
                    total_flows=256,
                    stall_ms=s_ms,
                    stall_period_ms=p_ms,
                    bulk_send_gbps=48.0,
                    tag=f"A2_F256_s{s_ms}ms",
                )
            )

    # Scenario B1: 64 MiB elephant burst + short RPC across F = 16 / 64 / 256 / 1,024 (all 5 arms)
    for f in (16, 64, 256, 1024):
        for arm in ALL_ARMS:
            specs.append(
                RunSpec(
                    part="B1_FLOWS",
                    arm=arm,
                    scenario="burst",
                    total_flows=f,
                    bulk_rate_gbps=16.0,
                    burst_mib=64,
                    burst_period_ms=60,
                    tag=f"B1_F{f}_b64m",
                )
            )

    # Scenario B2: 16 MiB elephant burst @ F = 256 (all 5 arms)
    for arm in ALL_ARMS:
        specs.append(
            RunSpec(
                part="B2_BURST16",
                arm=arm,
                scenario="burst",
                total_flows=256,
                bulk_rate_gbps=16.0,
                burst_mib=16,
                burst_period_ms=25,
                tag="B2_F256_b16m",
            )
        )
    return specs


# -----------------------------------------------------------------------------
# Process Lifecycle Management (Card, Host, Mercury)
# -----------------------------------------------------------------------------

class Harness:
    def __init__(self, numactl: str):
        self.numactl = numactl
        self.cur_card_key: tuple[Any, ...] | None = None
        self.cur_host_key: tuple[Any, ...] | None = None
        self.host_proc: subprocess.Popen | None = None
        self.host_log_f: Any = None

    def deploy(self) -> None:
        log("Deploying mrv228_card to CN106 and building mrv228_client on mercury...")
        sh(["scp", "-q", str(CARD_BIN_LOCAL), f"root@10.10.0.2:{CARD_BIN_REMOTE}"])
        ssh_card(f"chmod +x {CARD_BIN_REMOTE}")
        ssh_merc(f"pkill -x mrv228_client 2>/dev/null || true; mkdir -p {MERC_REMOTE_DIR}")
        sh([
            "scp", "-q",
            str(MERC_SRC_DIR / "mrv228_verbs_shim.c"),
            str(MERC_SRC_DIR / "mrv228_client.rs"),
            f"mercury:{MERC_REMOTE_DIR}/",
        ])
        ssh_merc(
            f'export PATH="$HOME/.cargo/bin:$PATH" && cd {MERC_REMOTE_DIR} && '
            'gcc -O3 -Wall -Wextra -Werror -c mrv228_verbs_shim.c -o mrv228_verbs_shim.o && '
            'rustc --edition=2024 -O -D warnings mrv228_client.rs -C link-arg=mrv228_verbs_shim.o -C link-arg=-libverbs -o mrv228_client'
        )
        log("Deploy complete.")

    def stop_host(self) -> None:
        if self.host_proc is not None:
            try:
                tcp_cmd(HOST_CTRL_HOST, HOST_CTRL_PORT, "QUIT", timeout=2.0)
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
        if self.host_log_f is not None:
            try:
                self.host_log_f.close()
            except Exception:
                pass
            self.host_log_f = None
        for p in pathlib.Path("/dev/hugepages").glob("m228h*"):
            try:
                p.unlink()
            except OSError:
                pass
        self.cur_host_key = None

    def stop_card(self) -> None:
        self.stop_host()
        try:
            tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "QUIT", timeout=2.0)
        except Exception:
            pass
        ssh_card(
            "sleep 0.25; tmux send-keys -t m228c C-c 2>/dev/null || true; sleep 0.25; "
            "tmux kill-session -t m228c 2>/dev/null || true; rm -f /dev/hugepages/m228c*",
            check=False,
        )
        self.cur_card_key = None

    def ensure_card(self, spec: RunSpec) -> list[str]:
        ck = spec.card_key()
        if ck == self.cur_card_key:
            expected_tl4 = (
                ["0x3008", "0x3009", "0x300a", "0x300b", "0x300c", "0x300d", "0x300e", "0x300f"]
                if spec.card_mode() == "ceio"
                else ["0x3008", "0x3009", "0x300a", "0x300b"]
            )
            return verify_sdp1_1_tm_topo(expected_tl4)

        self.stop_card()
        mode, sdp_tx_desc, ceio_inact_us = ck
        lcores = "3-7" if mode == "ceio" else "3-6"
        extra_card = f" --ceio-inactive-us {ceio_inact_us}" if mode == "ceio" else ""
        log(f"Starting mrv228_card mode={mode} lcores={lcores} sdp_tx_desc={sdp_tx_desc}{extra_card}...")
        ssh_card(
            "tmux send-keys -t refl C-c 2>/dev/null || true; sleep 0.25; "
            "tmux kill-session -t refl 2>/dev/null || true; rm -f /dev/hugepages/refl* /tmp/m228_card.log; "
            "ip link set eth0 down 2>/dev/null || true; "
            "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
            'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
            f"tmux new-session -d -s m228c '{CARD_BIN_REMOTE} -l {lcores} -a {ETH_DEV} -a {SDP_CARD_DEV} --file-prefix=m228c -- "
            f"--mode {mode} --data-cores 4 --sdp-tx-desc {sdp_tx_desc}{extra_card} > /tmp/m228_card.log 2>&1'; "
            "for i in $(seq 1 90); do grep -q 'MRV228_CARD READY' /tmp/m228_card.log 2>/dev/null && break; sleep 0.15; done; "
            "grep 'MRV228_CARD READY' /tmp/m228_card.log"
        )
        expected_tl4 = (
            ["0x3008", "0x3009", "0x300a", "0x300b", "0x300c", "0x300d", "0x300e", "0x300f"]
            if mode == "ceio"
            else ["0x3008", "0x3009", "0x300a", "0x300b"]
        )
        tl4 = verify_sdp1_1_tm_topo(expected_tl4)
        self.cur_card_key = ck
        return tl4

    def ensure_host(self, spec: RunSpec) -> tuple[int, int]:
        hk = spec.host_key()
        if hk == self.cur_host_key and self.host_proc is not None and self.host_proc.poll() is None:
            gbl, bp0 = csr_read_pair()
            if gbl != 0x4 or bp0 != spec.bp_mask():
                raise RuntimeError(f"CSR mismatch while host live: gbl={gbl:#x} bp0={bp0:#x} want={spec.bp_mask():#x}")
            return gbl, bp0

        self.stop_host()
        # Host is stopped -> safe to configure BP CSR (with VFIO FLR if clearing!)
        gbl, bp0 = csr_set_bp(spec.bp_mask())

        small_oq = spec.arm in ("bp1_stop", "bp1_drain", "per_flow_cr", "ceio")
        dev_arg = f"{SDP_HOST_BDF},rx_poll_mem=1,rx_poll_tail_off=48,ism_enable=1"
        if small_oq:
            dev_arg += ",allow_small_oq=1"

        cmd = [
            self.numactl, "--membind=1",
            str(HOST_BIN),
            "-l", "40", "-a", dev_arg, "--file-prefix=m228h", "--",
            "--mode", spec.host_mode(),
            "--lanes", "4",
            "--stall-ms", str(spec.stall_ms),
            "--stall-period-ms", str(spec.stall_period_ms),
            "--bulk-rate-gbps", f"{spec.bulk_rate_gbps:.3f}",
            "--coloc-kv-cpu", "41",
        ]
        log(f"Starting mrv228_host mode={spec.host_mode()} bp={bp0:#06x} stall={spec.stall_ms}/{spec.stall_period_ms} bulk_rate={spec.bulk_rate_gbps}")
        host_log = LOG_DIR / "host_latest.log"
        self.host_log_f = open(host_log, "w")
        self.host_proc = subprocess.Popen(cmd, stdout=self.host_log_f, stderr=subprocess.STDOUT, text=True)

        deadline = time.time() + 18.0
        ready = False
        while time.time() < deadline:
            if self.host_proc.poll() is not None:
                self.host_log_f.flush()
                txt = host_log.read_text() if host_log.exists() else ""
                raise RuntimeError(f"mrv228_host exited early ({self.host_proc.returncode}):\n{txt[-1200:]}")
            try:
                resp = tcp_cmd(HOST_CTRL_HOST, HOST_CTRL_PORT, "PING", timeout=0.5)
                if resp.get("ok"):
                    ready = True
                    break
            except Exception:
                time.sleep(0.15)
        if not ready:
            self.stop_host()
            txt = host_log.read_text() if host_log.exists() else ""
            raise RuntimeError(f"mrv228_host timed out waiting for PING:\n{txt[-1200:]}")
        self.cur_host_key = hk
        return gbl, bp0

    def run_kv_solo(self, spec: RunSpec, rep: int, out_path: pathlib.Path) -> dict:
        self.stop_host()
        pre_gbl, pre_bp0 = csr_set_bp(0x0)
        for attempt in range(1, MAX_RETRIES + 1):
            cmd = [
                self.numactl, "--membind=1",
                str(HOST_BIN), "-l", "40", "--no-pci", "--file-prefix=m228h", "--",
                "--mode", "kv-solo", "--coloc-kv-cpu", "41", "--solo-ms", "1200",
            ]
            r = sh(cmd, check=False, timeout=45)
            post_gbl, post_bp0 = csr_read_pair()
            for p in pathlib.Path("/dev/hugepages").glob("m228h*"):
                try:
                    p.unlink()
                except OSError:
                    pass
            jlines = [l for l in reversed(r.stdout.splitlines()) if l.startswith("MRV228_KV_SOLO ")]
            if r.returncode != 0 or not jlines:
                append_jsonl(ATTEMPTS_PATH, {"ts_utc": utc_now(), "part": spec.part, "arm": spec.arm, "tag": spec.tag, "rep": rep, "attempt": attempt, "accepted": False, "reject_reasons": [f"rc={r.returncode}"]})
                continue
            obj = json.loads(jlines[0][len("MRV228_KV_SOLO "):])
            pmu = obj["pmu"]
            kv = obj["coloc_kv"]
            rec = {
                "ts_utc": utc_now(),
                "part": spec.part,
                "arm": spec.arm,
                "scenario": spec.scenario,
                "total_flows": 0,
                "tag": spec.tag,
                "rep": rep,
                "attempt": attempt,
                "bp_mask": "0x0000",
                "csr_pre": {"gbl": f"{pre_gbl:#018x}", "bp0": f"{pre_bp0:#018x}"},
                "csr_post": {"gbl": f"{post_gbl:#018x}", "bp0": f"{post_bp0:#018x}"},
                "host": obj,
            }
            retryable = []
            hard = []
            if post_gbl != pre_gbl or post_bp0 != pre_bp0:
                hard.append("csr_drift")
            if kv["bad"] != 0 or kv["page_faults"] != 0:
                hard.append(f"kv_err bad={kv['bad']} pf={kv['page_faults']}")
            if pmu["min_running_pct"] < 99.95:
                hard.append(f"host_pmu_pct={pmu['min_running_pct']:.2f}")
            if pmu["mesh_ghz"] < MIN_MESH_GHZ:
                retryable.append(f"mesh_ghz={pmu['mesh_ghz']:.3f}")
            if hard:
                rec["accepted"] = False
                rec["reject_reasons"] = hard
                append_jsonl(ATTEMPTS_PATH, rec)
                raise RuntimeError(f"HARD FAIL kv_solo: {hard}")
            if retryable:
                rec["accepted"] = False
                rec["reject_reasons"] = retryable
                append_jsonl(ATTEMPTS_PATH, rec)
                continue
            rec["accepted"] = True
            rec["reject_reasons"] = []
            append_jsonl(ATTEMPTS_PATH, rec)
            append_jsonl(out_path, rec)
            log(f"  [PASS] {spec.tag} rep={rep}: KV p50={kv['p50_ns']:.1f} p99={kv['p99_ns']:.1f} p999={kv['p999_ns']:.1f} ns IMC={pmu['imc_total_gbps']:.2f} GB/s")
            return rec
        raise RuntimeError("kv_solo exhausted retries")

    def run_one(self, spec: RunSpec, rep: int, out_path: pathlib.Path, keep_ts: bool = False) -> dict:
        global EPOCH_GEN
        if spec.arm == "kv_solo":
            return self.run_kv_solo(spec, rep, out_path)

        tl4 = self.ensure_card(spec)
        pre_gbl, pre_bp0 = self.ensure_host(spec)

        for attempt in range(1, MAX_RETRIES + 1):
            # Warmup epoch reset (Host FIRST, then Card!)
            EPOCH_GEN += 1
            wu_ep = EPOCH_GEN
            tcp_cmd(HOST_CTRL_HOST, HOST_CTRL_PORT, f"RESET {wu_ep}")
            tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, f"RESET {wu_ep}")
            tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "RESET_PEERS")

            merc_phy0 = read_merc_phy()
            client_cmd = [
                "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mercury",
                f"{MERC_BIN_REMOTE} --dev mlx5_2 --sgid 3 --cpu 4 --card-queues 4 "
                f"--scenario {spec.scenario} --total-flows {spec.total_flows} "
                f"--rpc-rate-krps {spec.rpc_rate_krps:.1f} "
                f"--bulk-send-gbps {spec.bulk_send_gbps:.1f} "
                f"--burst-mib {spec.burst_mib} --burst-period-ms {spec.burst_period_ms} "
                f"--warmup-ms {spec.warmup_ms} --duration-ms {spec.duration_ms} "
                f"--seed {22800 + rep * 97 + attempt} --sync-reset",
            ]
            proc = subprocess.Popen(
                client_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            # Wait for WARMUP_DONE
            saw_warmup = False
            out_lines: list[str] = []
            t_wu_dl = time.time() + 25.0
            while time.time() < t_wu_dl:
                line = proc.stdout.readline()
                if not line:
                    break
                s = line.strip()
                out_lines.append(s)
                if s == "WARMUP_DONE":
                    saw_warmup = True
                    break

            if not saw_warmup:
                proc.kill()
                err_txt = proc.stderr.read()
                append_jsonl(ATTEMPTS_PATH, {"ts_utc": utc_now(), "part": spec.part, "arm": spec.arm, "tag": spec.tag, "rep": rep, "attempt": attempt, "accepted": False, "reject_reasons": [f"no_warmup_done: {err_txt[-200:]}"]})
                time.sleep(0.3)
                continue

            # Synchronized measurement-window RESET (Host FIRST -> zeroes all 64 BAR4 FlowCreditLines + posts new epoch -> Card!)
            EPOCH_GEN += 1
            meas_ep = EPOCH_GEN
            tcp_cmd(HOST_CTRL_HOST, HOST_CTRL_PORT, f"RESET {meas_ep}")
            card_rst = tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, f"RESET {meas_ep}")
            card_pmu0 = card_rst.get("pmu", {})

            assert proc.stdin is not None
            proc.stdin.write("GO\n")
            proc.stdin.flush()
            proc.stdin.close()

            rem_out, err_txt = proc.communicate(timeout=45)
            for l in rem_out.splitlines():
                out_lines.append(l.strip())

            # Snapshot host, card, CSR, mercury PHY immediately after client finishes
            host_st = tcp_cmd(HOST_CTRL_HOST, HOST_CTRL_PORT, "STATS")
            card_st = tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "STATS")
            merc_phy1 = read_merc_phy()
            post_gbl, post_bp0 = csr_read_pair()

            cl_lines = [l for l in reversed(out_lines) if l.startswith("MRV228_CLIENT ")]
            if proc.returncode != 0 or not cl_lines:
                append_jsonl(ATTEMPTS_PATH, {"ts_utc": utc_now(), "part": spec.part, "arm": spec.arm, "tag": spec.tag, "rep": rep, "attempt": attempt, "accepted": False, "reject_reasons": [f"client_rc={proc.returncode} {err_txt[-200:]}"]})
                continue

            cl = json.loads(cl_lines[0][len("MRV228_CLIENT "):])

            # Compute card PMU delta (100% non-multiplexed TAD L3 + 6-ch DDR5)
            card_pmu1 = card_st.get("pmu", {})
            card_pmu_delta: dict[str, int] = {}
            card_pmu_min_pct = 100.0
            for k, v1 in card_pmu1.items():
                v0 = card_pmu0.get(k, {"val": 0, "ena": 0, "run": 0})
                dval = max(0, v1["val"] - v0["val"])
                dena = max(0, v1["ena"] - v0["ena"])
                drun = max(0, v1["run"] - v0["run"])
                pct = 100.0 if dena == 0 else (drun / dena) * 100.0
                card_pmu_min_pct = min(card_pmu_min_pct, pct)
                card_pmu_delta[k] = dval
            card_ddr_rd = sum(card_pmu_delta.get(f"ddr_ch{c}_rd", 0) for c in range(6))
            card_ddr_wr = sum(card_pmu_delta.get(f"ddr_ch{c}_wr", 0) for c in range(6))
            card_pmu_summary = {
                "min_pct": round(card_pmu_min_pct, 3),
                "tad_hit": card_pmu_delta.get("tad_req_ltg_hit", 0),
                "tad_miss": card_pmu_delta.get("tad_req_ltg_miss", 0),
                "ddr_rd_lines": card_ddr_rd,
                "ddr_wr_lines": card_ddr_wr,
                "ddr_rd_mib": round(card_ddr_rd * 64.0 / (1024.0 * 1024.0), 3),
                "ddr_wr_mib": round(card_ddr_wr * 64.0 / (1024.0 * 1024.0), 3),
            }

            # Compute synchronized during-stall vs non-stall healthy RPC completion rate from host 500us buckets
            h_ts = host_st.get("ts_buckets", [])
            stall_rpc_sum = sum(b[0] for b in h_ts if b[2] == 1)
            stall_b_cnt = sum(1 for b in h_ts if b[2] == 1)
            nostall_rpc_sum = sum(b[0] for b in h_ts if b[2] == 0)
            nostall_b_cnt = sum(1 for b in h_ts if b[2] == 0)
            b_s = host_st.get("ts_bucket_us", 500) * 1e-6
            rpc_during_stall_krps = (stall_rpc_sum / (stall_b_cnt * b_s) / 1e3) if stall_b_cnt > 0 else 0.0
            rpc_outside_stall_krps = (nostall_rpc_sum / (nostall_b_cnt * b_s) / 1e3) if nostall_b_cnt > 0 else 0.0
            host_st["rpc_during_stall_krps"] = round(rpc_during_stall_krps, 3)
            host_st["rpc_outside_stall_krps"] = round(rpc_outside_stall_krps, 3)
            host_st["stall_bucket_cnt"] = stall_b_cnt

            # Keep full timeseries ONLY when explicitly requested (to keep jsonl << 4 MiB!)
            if not keep_ts:
                host_st.pop("ts_buckets", None)
                cl.pop("ts_rpc_acks", None)
            card_st.pop("pmu", None)

            merc_crc_delta = max(0, merc_phy1.get("rx_crc_errors_phy", 0) - merc_phy0.get("rx_crc_errors_phy", 0))
            merc_tx_pause_delta = max(0, merc_phy1.get("tx_global_pause", 0) - merc_phy0.get("tx_global_pause", 0))
            merc_rx_pause_delta = max(0, merc_phy1.get("rx_global_pause", 0) - merc_phy0.get("rx_global_pause", 0))

            rec = {
                "ts_utc": utc_now(),
                "part": spec.part,
                "arm": spec.arm,
                "scenario": spec.scenario,
                "total_flows": spec.total_flows,
                "stall_ms": spec.stall_ms,
                "stall_period_ms": spec.stall_period_ms,
                "bulk_send_gbps": spec.bulk_send_gbps,
                "bulk_rate_gbps": spec.bulk_rate_gbps,
                "burst_mib": spec.burst_mib,
                "burst_period_ms": spec.burst_period_ms,
                "tag": spec.tag,
                "rep": rep,
                "attempt": attempt,
                "bp_mask": f"{spec.bp_mask():#06x}",
                "ceio_inactive_us": spec.ceio_inactive_us(),
                "csr_pre": {"gbl": f"{pre_gbl:#018x}", "bp0": f"{pre_bp0:#018x}"},
                "csr_post": {"gbl": f"{post_gbl:#018x}", "bp0": f"{post_bp0:#018x}"},
                "tm_tl4_vals": tl4,
                "merc_phy_delta": {
                    "rx_crc_errors_phy": merc_crc_delta,
                    "tx_global_pause": merc_tx_pause_delta,
                    "rx_global_pause": merc_rx_pause_delta,
                },
                "client": cl,
                "host": host_st,
                "card": card_st,
                "card_pmu": card_pmu_summary,
            }

            # Strict Audit Gates
            hard = []
            retryable = []
            hpmu = host_st["pmu"]
            hkv = host_st["coloc_kv"]
            hsdp = host_st["sdp_port_stats"]
            ceth = card_st["eth_port_stats"]

            if post_gbl != pre_gbl or post_bp0 != pre_bp0:
                hard.append(f"csr_drift pre={pre_bp0:#x} post={post_bp0:#x}")
            if merc_tx_pause_delta != 0 or merc_rx_pause_delta != 0:
                hard.append(f"eth_pause tx={merc_tx_pause_delta} rx={merc_rx_pause_delta}")
            if cl["bad"] != 0 or cl["torn"] != 0 or cl["ooo"] != 0 or cl["send_err"] != 0:
                hard.append(f"client_err bad={cl['bad']} torn={cl['torn']} ooo={cl['ooo']} serr={cl['send_err']}")
            if host_st["bad"] != 0 or host_st["torn"] != 0 or host_st["ooo"] != 0 or host_st["tx_drops"] != 0 or host_st["ack_alloc_fail"] != 0:
                hard.append(f"host_err bad={host_st['bad']} torn={host_st['torn']} ooo={host_st['ooo']} txd={host_st['tx_drops']} af={host_st['ack_alloc_fail']}")
            card_sdp_tx_tot = card_st["sdp_fast_tx_pkts"] + card_st["sdp_slow_tx_pkts"]
            host_sdp_drop = max(0, card_sdp_tx_tot - host_st["rx_pkts"])
            host_sdp_drop_pct = (100.0 * host_sdp_drop / card_sdp_tx_tot) if card_sdp_tx_tot > 0 else 0.0
            client_loss_pct = (100.0 * cl["lost"] / cl["sent"]) if cl["sent"] > 0 else 0.0
            rec["sdp_drop_pkts"] = host_sdp_drop
            rec["sdp_drop_pct"] = round(host_sdp_drop_pct, 4)
            rec["client_loss_pct"] = round(client_loss_pct, 4)
            is_bp0_arm = (spec.arm == "large_sdp")

            if hsdp["ierrors"] != 0 or hsdp["oerrors"] != 0:
                hard.append(f"host_sdp_err ierr={hsdp['ierrors']} oerr={hsdp['oerrors']}")
            if not is_bp0_arm and host_st["rx_pkts"] != card_sdp_tx_tot:
                hard.append(f"host_sdp_drop hrx={host_st['rx_pkts']} csdp_tx={card_sdp_tx_tot} nombuf={hsdp['rx_nombuf']}")
            if card_st["bad_wire"] != 0 or card_st["bad_sdp"] != 0 or card_st["eth_tx_drops"] != 0 or card_st["sdp_tx_drops"] != 0 or card_st["card_buf_drops"] != 0 or card_st["pf_sched"]["fifo_drops"] != 0:
                hard.append(f"card_err bw={card_st['bad_wire']} bs={card_st['bad_sdp']} ed={card_st['eth_tx_drops']} sd={card_st['sdp_tx_drops']} bd={card_st['card_buf_drops']} fd={card_st['pf_sched']['fifo_drops']}")
            if hkv["bad"] != 0:
                hard.append(f"kv_bad={hkv['bad']}")
            if hpmu["min_running_pct"] < 99.95:
                hard.append(f"host_pmu_pct={hpmu['min_running_pct']:.2f}")
            if card_pmu_min_pct < 99.95:
                hard.append(f"card_pmu_pct={card_pmu_min_pct:.2f}")

            if cl["lost"] != 0:
                eth_wire_gap = max(0, cl["sent"] - card_st["eth_rx_pkts"])
                if is_bp0_arm and cl["lost"] <= host_sdp_drop + eth_wire_gap + merc_crc_delta + ceth["ierrors"] + ceth["imissed"]:
                    pass  # Genuine BP=0 passive SDP ring overflow drop, recorded in sdp_drop_pkts/client_loss_pct!
                elif (merc_crc_delta + ceth["ierrors"] > 0 and cl["lost"] <= 8 and host_sdp_drop == 0) or (
                    cl["lost"] <= 2 and host_sdp_drop == 0 and eth_wire_gap == cl["lost"]
                ):
                    retryable.append(f"phy_crc_loss lost={cl['lost']} wire_gap={eth_wire_gap} crc={merc_crc_delta} ierr={ceth['ierrors']}")
                else:
                    hard.append(f"unexplained_loss lost={cl['lost']} sdp_drop={host_sdp_drop} wire_gap={eth_wire_gap} crc={merc_crc_delta} ceth_ierr={ceth['ierrors']} ceth_imiss={ceth['imissed']} hsdp_nombuf={hsdp['rx_nombuf']}")
            if hpmu["page_faults"] != 0 or hkv["page_faults"] != 0:
                retryable.append(f"pf cpu40={hpmu['page_faults']} kv={hkv['page_faults']}")
            if hpmu["cpu_ghz"] < MIN_CORE_GHZ:
                retryable.append(f"cpu_ghz={hpmu['cpu_ghz']:.3f}")
            if hpmu["mesh_ghz"] < MIN_MESH_GHZ:
                retryable.append(f"mesh_ghz={hpmu['mesh_ghz']:.3f}")

            if hard:
                rec["accepted"] = False
                rec["reject_reasons"] = hard + retryable
                append_jsonl(ATTEMPTS_PATH, rec)
                raise RuntimeError(f"HARD GATE FAIL {spec.tag} {spec.arm} rep={rep}: {hard}")

            if retryable:
                rec["accepted"] = False
                rec["reject_reasons"] = retryable
                append_jsonl(ATTEMPTS_PATH, rec)
                log(f"  [RETRY] {spec.tag} {spec.arm} rep={rep} attempt={attempt}: {retryable}")
                time.sleep(0.3)
                continue

            rec["accepted"] = True
            rec["reject_reasons"] = []
            append_jsonl(ATTEMPTS_PATH, rec)
            append_jsonl(out_path, rec)
            log(
                f"  [PASS] {spec.tag:18s} {spec.arm:11s} rep={rep} | "
                f"RPC p50/p99={cl['rpc_p50_us']:6.1f}/{cl['rpc_p99_us']:7.1f}us (SLO100={cl['rpc_slo100_pct']:5.1f}%, gap={cl['rpc_max_ack_gap_us']:6.0f}us) | "
                f"DDIO={hpmu['ddio_hit_pct']:5.1f}% IMC={hpmu['imc_total_gbps']:4.2f}GB/s KV_p99={hkv['p99_ns']:5.1f}ns | "
                f"bp_stalls={card_st['sdp_tx_stalls']} h_stage_pk={host_st['host_stage_max_depth']} pf_q_pk={card_st['pf_sched']['fifo_max_depth']} slow={cl['slow_frac']*100:.1f}%"
            )
            return rec

        raise RuntimeError(f"Exhausted {MAX_RETRIES} retries for {spec.tag} {spec.arm} rep={rep}")

    def restore_baseline(self, orig_csrs: dict[str, str]) -> dict:
        log("Restoring all 3 nodes to exact clean baseline...")
        self.stop_card()
        # 1. Clear BP CSRs & verify exact 64-bit equality with pre-run values
        csr_clear_bp()
        post_csrs = read_all_safe_csrs()
        exact_match = (post_csrs == orig_csrs)
        if not exact_match:
            log(f"ERROR: CSR mismatch! orig={orig_csrs} post={post_csrs}")

        # 2. Restore BAR4 slot 15 defaults, ensure pem_wc unloaded, rebind eth0 -> rvu_nicpf 100G UP, restart refl tmux
        ssh_card(
            f"{CARD_BIN_REMOTE} --restore || true; "
            "rmmod pem_wc 2>/dev/null || true; "
            "rm -f /dev/hugepages/m228*; "
            "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
            'if [ "$drv" != "rvu_nicpf" ]; then dpdk-devbind.py -b rvu_nicpf 0002:02:00.0; fi; '
            "sleep 0.5; ip link set eth0 up; ip addr replace 10.10.100.2/24 dev eth0; "
            "for i in $(seq 1 30); do [ \"$(cat /sys/class/net/eth0/operstate 2>/dev/null)\" = 'up' ] && break; sleep 0.2; done; "
            "tmux has-session -t refl 2>/dev/null || tmux new-session -d -s refl '/root/bench/inline_reflector -l 3 -a 0002:01:00.2 --file-prefix=refl'; "
            "sleep 0.5; ping -c 2 -W 1 10.10.100.1 >/dev/null && echo PING_OK || echo PING_FAIL",
            check=False,
        )
        # 3. Clean saturn hugepages & mercury /tmp/mrv228*
        for p in pathlib.Path("/dev/hugepages").glob("m228*"):
            try:
                p.unlink()
            except OSError:
                pass
        ssh_merc("pkill -x mrv228_client 2>/dev/null || true; rm -rf /tmp/mrv228 /tmp/mrv228*", check=False)

        log("Waiting 21 s for post-restore EDAC / PCIe AER audit...")
        time.sleep(21)
        snap = read_env_snapshot()
        return {"csrs_restored_exact": exact_match, "post_restore_21s": snap}


def maybe_gzip_large(path: pathlib.Path) -> None:
    if path.exists() and path.stat().st_size > 3_500_000:
        gz = pathlib.Path(str(path) + ".gz")
        with open(path, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        path.unlink()
        log(f"Compressed {path.name} -> {gz.name} ({gz.stat().st_size / (1024*1024):.2f} MiB)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--no-restore", action="store_true", help="Skip 21s restore (only when chaining immediately)")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    numactl = resolve_numactl()

    pre_env = read_env_snapshot()
    orig_csrs = pre_env["cn106"]["csrs"]
    log(f"Pre-run 6 safe SDP CSRs: {json.dumps(orig_csrs)}")
    if orig_csrs.get("SDPX_OUT_BP_ENX_W1S(0)") != "0x0000000000000000" or orig_csrs.get("SDPX_GBL_CONTROL") != "0x0000000000000004":
        raise RuntimeError(f"Unexpected pre-run CSR state: {orig_csrs}")

    h = Harness(numactl)
    out_path = SMOKE_PATH if args.smoke else RUNS_PATH
    specs = build_specs(smoke=args.smoke)
    reps = 1 if args.smoke else args.reps

    done_keys: set[tuple[int, str, str]] = set()
    if args.resume and out_path.exists():
        for line in out_path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            done_keys.add((r["rep"], r["tag"], r["arm"]))
        log(f"Resume mode: {len(done_keys)} completed runs found in {out_path.name}")

    try:
        if args.deploy:
            h.deploy()

        # Interleaved by rep (rep 0 -> rep 1 -> rep 2), rotating arm order per rep to eliminate order bias
        arm_orders = [
            ("kv_solo", "bp1_stop", "bp1_drain", "large_sdp", "ceio", "per_flow_cr"),
            ("kv_solo", "per_flow_cr", "ceio", "large_sdp", "bp1_drain", "bp1_stop"),
            ("kv_solo", "ceio", "per_flow_cr", "bp1_stop", "bp1_drain", "large_sdp"),
        ]

        total_planned = len(specs) * reps
        log(f"Starting {'SMOKE' if args.smoke else 'CAMPAIGN'}: {len(specs)} specs x {reps} reps = {total_planned} runs")
        for rep in range(reps):
            order = arm_orders[rep % len(arm_orders)]
            order_idx = {a: i for i, a in enumerate(order)}
            # Group by arm within each rep so card/host only restart once per arm per rep!
            ordered = sorted(specs, key=lambda s: (order_idx[s.arm], s.card_key(), s.host_key(), s.total_flows, s.tag))
            for sp in ordered:
                if (rep, sp.tag, sp.arm) in done_keys:
                    continue
                keep_ts = (rep == 0 and sp.tag in ("A1_F256_s4ms", "B1_F256_b64m"))
                h.run_one(sp, rep, out_path, keep_ts=keep_ts)
    finally:
        if not args.no_restore:
            rest = h.restore_baseline(orig_csrs)
            post_env = rest["post_restore_21s"]
            edac_ok = (
                post_env["saturn"]["host_edac_ue_sum"] == pre_env["saturn"]["host_edac_ue_sum"]
                and post_env["saturn"]["rp_98_01_aer"] == pre_env["saturn"]["rp_98_01_aer"]
                and post_env["cn106"]["EDAC_mc0"] == pre_env["cn106"]["EDAC_mc0"]
                and post_env["cn106"]["EDAC_llc0"] == pre_env["cn106"]["EDAC_llc0"]
                and post_env["cn106"]["EDAC_mdc0"] == pre_env["cn106"]["EDAC_mdc0"]
                and post_env["cn106"]["EDAC_gic0"] == pre_env["cn106"]["EDAC_gic0"]
                and post_env["cn106"]["EDAC_ea0"] == pre_env["cn106"]["EDAC_ea0"]
                and post_env["cn106"]["EDAC_CPU_SUM"] == pre_env["cn106"]["EDAC_CPU_SUM"]
            )
            uncore_pkgs = post_env["saturn"]["uncore_khz"]
            uncore_2200_ok = bool(uncore_pkgs) and all(
                v["min"] == 2200000 and v["max"] == 2200000 and v["cur"] == 2200000
                for v in uncore_pkgs.values()
            )
            cpu40_3900_ok = (
                post_env["saturn"]["cpu40_gov"] == "performance"
                and post_env["saturn"]["cpu40_khz"] >= 3_880_000
            )
            mercury_clean_ok = (
                post_env["mercury"]["oper"] == "up"
                and post_env["mercury"]["m228_procs"] == 0
                and post_env["mercury"]["tmp_mrv228"] == 0
            )
            cn106_clean_ok = (
                post_env["cn106"]["ETH0_OPER"] == "up"
                and post_env["cn106"]["ETH0_SPEED"] == "100000Mb/s"
                and post_env["cn106"]["PEM_WC"] == 0
                and post_env["cn106"]["REFL"] == "running"
                and post_env["cn106"]["HP_M228"] == 0
            )
            saturn_hp_clean_ok = post_env["saturn"]["hugepages_m228_leftover"] == 0
            env_doc = {
                "issue": "MRV-228",
                "mode": "smoke" if args.smoke else "full",
                "baseline_restored_ok": bool(
                    rest["csrs_restored_exact"]
                    and edac_ok
                    and uncore_2200_ok
                    and cpu40_3900_ok
                    and mercury_clean_ok
                    and cn106_clean_ok
                    and saturn_hp_clean_ok
                ),
                "csrs_restored_exact": rest["csrs_restored_exact"],
                "edac_aer_zero_delta": edac_ok,
                "uncore_2200_ok": uncore_2200_ok,
                "cpu40_3900_ok": cpu40_3900_ok,
                "mercury_clean_ok": mercury_clean_ok,
                "cn106_clean_ok": cn106_clean_ok,
                "saturn_hp_clean_ok": saturn_hp_clean_ok,
                "pre_run": pre_env,
                "post_restore_21s": post_env,
            }
            ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n")
            log(
                f"Restore audit written to {ENV_PATH}: baseline_restored_ok={env_doc['baseline_restored_ok']} "
                f"csrs_exact={rest['csrs_restored_exact']} edac_zero_delta={edac_ok} "
                f"uncore_2200={uncore_2200_ok} cpu40_3900={cpu40_3900_ok} merc_clean={mercury_clean_ok}"
            )
            if not args.smoke:
                maybe_gzip_large(RUNS_PATH)
                maybe_gzip_large(ATTEMPTS_PATH)


if __name__ == "__main__":
    main()
