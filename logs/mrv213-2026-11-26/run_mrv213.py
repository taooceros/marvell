#!/usr/bin/env python3
"""MRV-213 (MRV-203 E2) full n=3 interleaved 100GbE + standard-SDP burst-protection harness.

Single-variable comparison of 5 standard-SDP receive architectures across
real `mercury -> CN106 -> saturn` 100GbE bursts (`64 B / 256 B / 1500 B` x
`64 KiB -> 128 MiB`) with concurrent Socket-1 independent-core (`cpu41`)
32 MiB cache-sensitive KV random lookup:
  0) `kv_solo`     : unloaded 32 MiB KV baseline on cpu41
  1) `large_mono`  : Arm 1 - Large SDP OQ (8192 desc) + >=128 MiB strict FIFO (`ring_sp_sc`) pool
  2) `small_drop`  : Arm 2 - Small passive SDP OQ (256 desc, `allow_small_oq=1`); card drops on full
  3) `large_lifo`  : Arm 3 - Large SDP OQ (8192 desc) + >=128 MiB ideal LIFO (`stack`) pool
  4) `native_bp`   : Arm 4 - Native SDP `SLIST_DBELL/WMARK` backpressure + card elastic FIFO (zero BAR4 credit)
  5) `credit_ring` : Arm 5 - Full design: On-Path SmartNIC elastic FIFO + compact hot SDP ring/pool
                     (`rx_desc=512, WMARK=128, R_eff=256`) + NO-SFENCE `BAR4` `MOVDIR64B` credit

Audit & safety guarantees:
  - Zero silent retries: EVERY attempt is appended to `attempts.jsonl`; only verified
    runs are appended to `runs.jsonl`; clock/EDAC/AER before/after/post-restore-21s in `env.json`.
  - Integrity/PMU/drop-accounting errors are never silently masked; only shared-host
    Socket-1 clock/mesh contention (`cpu40/cpu41 < 3.85 GHz` or `mesh < 2.18 GHz`) retries.
  - Guaranteed `finally` teardown restores CN106 (`BAR4` slot 15 default, `eth0` back to
    `rvu_nicpf` at `10.10.100.2/24`, `reflector-rs` in tmux `refl`, EDAC/AER 21 s recheck).
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

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv213")
LOG_DIR = ROOT / "logs/mrv213-2026-11-26"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = ROOT / "testbed/target/release/mrv213_host"
CARD_LOCAL_BIN = ROOT / "testbed/target/aarch64-unknown-linux-gnu/release/mrv213_card"
CARD_REMOTE_BIN = "/root/bench/mrv213_card"
MERC_SRC_DIR = ROOT / "testbed/mercury-client"
MERC_REMOTE_DIR = "/tmp/mrv213"
MERC_REMOTE_BIN = "/tmp/mrv213/mrv213_client"

CARD_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mercury"]

CARD_SYNC_HOST = "10.10.0.2"
CARD_SYNC_PORT = 7527
MERC_CTRL_HOST = "mercury"
MERC_CTRL_PORT = 7547

SDP_QUEUES = 4
CARD_LCORES = "3-6"
MIN_CORE_GHZ = 3.85
MIN_MESH_GHZ = 2.18
MAX_CLOCK_RETRIES = 5

KB = 1024
MB = 1024 * 1024

# Burst schedule: (label, burst_bytes, measured_bursts, warmup_bursts)
BURST_MATRIX = [
    ("64KiB",  64 * KB,   256, 8),
    ("256KiB", 256 * KB,  128, 4),
    ("1MiB",   1 * MB,    64,  2),
    ("4MiB",   4 * MB,    32,  2),
    ("16MiB",  16 * MB,   16,  2),
    ("32MiB",  32 * MB,   8,   2),
    ("64MiB",  64 * MB,   6,   1),
    ("128MiB", 128 * MB,  4,   1),
]

SMOKE_BURSTS = [
    ("256KiB", 256 * KB, 16, 2),
    ("32MiB",  32 * MB,  4,  1),
    ("128MiB", 128 * MB, 2,  1),
]

MBUF_ABLATION_BURSTS = [
    ("16MiB",  16 * MB,  16, 2),
    ("32MiB",  32 * MB,  8,  2),
    ("64MiB",  64 * MB,  6,  1),
    ("128MiB", 128 * MB, 4,  1),
]

# 5 single-variable standard-SDP arms:
#   (arm_tag, host_arm_flag, rx_desc, allow_small_oq, r_eff, card_sdp_tx_desc)
ARMS = [
    ("large_mono",  "large-mono",  8192, False, 256, 2048),
    ("small_drop",  "small-drop",  256,  True,  256, 256),
    ("large_lifo",  "large-lifo",  8192, False, 256, 2048),
    ("native_bp",   "native-bp",   512,  True,  256, 2048),
    ("credit_ring", "credit-ring", 512,  True,  256, 2048),
]

PROFILE_CFG = {
    "1500b": {"l2_bytes": 1500, "merc_threads": 8,  "merc_batch": 32, "probe_mode": "udp1500"},
    "256b":  {"l2_bytes": 256,  "merc_threads": 16, "merc_batch": 32, "probe_mode": "ud256"},
    "64b":   {"l2_bytes": 64,   "merc_threads": 16, "merc_batch": 64, "probe_mode": "uc64"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(cmd: list[str], check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"CMD FAILED ({r.returncode}): {' '.join(cmd)}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
    return r


def ssh_card(script: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def ssh_merc(script: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    return sh(MERC_SSH + [script], check=check, timeout=timeout)


def tcp_cmd(host: str, port: int, cmd: str, timeout: float = 15.0) -> dict:
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


def append_jsonl(path: pathlib.Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")
        f.flush()


def load_completed_keys(path: pathlib.Path) -> set[tuple[str, str, int]]:
    done = set()
    if not path.exists():
        return done
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            done.add((rec["part"], rec["tag"], int(rec["rep"])))
        except Exception:
            pass
    return done


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
    edac_ue = {}
    edac_ce = {}
    for mc in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*")):
        edac_ue[mc.name] = int((mc / "ue_count").read_text().strip())
        edac_ce[mc.name] = int((mc / "ce_count").read_text().strip())
    rp = pathlib.Path("/sys/bus/pci/devices/0000:98:01.0")
    saturn_env = {
        "cpu40_gov": pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip(),
        "cpu40_khz": int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip()),
        "cpu41_gov": pathlib.Path("/sys/devices/system/cpu/cpu41/cpufreq/scaling_governor").read_text().strip(),
        "cpu41_khz": int(pathlib.Path("/sys/devices/system/cpu/cpu41/cpufreq/scaling_cur_freq").read_text().strip()),
        "uncore_khz": uncore,
        "perf_event_paranoid": int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip()),
        "node1_hugepages_2m_total": int(
            pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/nr_hugepages").read_text().strip()
        ),
        "node1_hugepages_2m_free": int(
            pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/free_hugepages").read_text().strip()
        ),
        "host_edac_ue": edac_ue,
        "host_edac_ue_sum": sum(edac_ue.values()),
        "host_edac_ce": edac_ce,
        "rp_98_01_aer": {
            "fatal": parse_aer_total(rp / "aer_rootport_total_err_fatal"),
            "nonfatal": parse_aer_total(rp / "aer_rootport_total_err_nonfatal"),
            "correctable": parse_aer_total(rp / "aer_rootport_total_err_cor"),
        },
    }

    card_raw = ssh_card(
        "echo cpu3_khz=$(cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 2000000); "
        "echo mc0_ue=$(cat /sys/devices/system/edac/mc/mc0/ue_count 2>/dev/null || echo 0); "
        "echo mc0_ce=$(cat /sys/devices/system/edac/mc/mc0/ce_count 2>/dev/null || echo 0); "
        "echo ea0_ue=$(cat /sys/devices/system/edac/ea/ea0/ue_count 2>/dev/null || echo 0); "
        "echo ea0_ce=$(cat /sys/devices/system/edac/ea/ea0/ce_count 2>/dev/null || echo 0); "
        "echo sei_total=$(dmesg | grep -c 'EDAC DEVICE4' || echo 0); "
        "echo hp_total=$(cat /sys/kernel/mm/hugepages/hugepages-524288kB/nr_hugepages); "
        "echo hp_free=$(cat /sys/kernel/mm/hugepages/hugepages-524288kB/free_hugepages)"
    ).stdout.strip()
    card_env = {}
    for line in card_raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            card_env[k.strip()] = int(v.strip()) if v.strip().isdigit() else v.strip()

    merc_raw = ssh_merc(
        "echo mtu=$(cat /sys/class/net/ens6f1np1/mtu); "
        "echo oper=$(cat /sys/class/net/ens6f1np1/operstate); "
        "ethtool -S ens6f1np1 | grep -E 'tx_packets_phy:|tx_bytes_phy:|rx_crc_errors_phy:|tx_global_pause:|rx_global_pause:' || true"
    ).stdout.strip()
    merc_env = {}
    for line in merc_raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            merc_env[k.strip()] = int(v.strip()) if v.strip().isdigit() else v.strip()
        elif ":" in line:
            k, v = line.split(":", 1)
            merc_env[k.strip()] = int(v.strip())

    return {"ts_utc": utc_now(), "saturn": saturn_env, "cn106": card_env, "mercury": merc_env}


def deploy_binaries() -> None:
    if not HOST_BIN.exists():
        raise FileNotFoundError(f"Missing host binary: {HOST_BIN}")
    if not CARD_LOCAL_BIN.exists():
        raise FileNotFoundError(f"Missing card binary: {CARD_LOCAL_BIN}")
    print("[deploy] syncing mrv213_card to CN106 ...", flush=True)
    sh(["scp", "-q", str(CARD_LOCAL_BIN), f"root@10.10.0.2:{CARD_REMOTE_BIN}"])
    ssh_card(f"chmod +x {CARD_REMOTE_BIN}")

    print("[deploy] syncing & compiling mrv213_client on mercury ...", flush=True)
    ssh_merc(f"mkdir -p {MERC_REMOTE_DIR}")
    sh([
        "scp", "-q",
        str(MERC_SRC_DIR / "mrv213_verbs_shim.c"),
        str(MERC_SRC_DIR / "mrv213_client.rs"),
        f"mercury:{MERC_REMOTE_DIR}/",
    ])
    ssh_merc(
        f"cd {MERC_REMOTE_DIR} && "
        f"gcc -O3 -Wall -Wextra -Werror -c mrv213_verbs_shim.c -o mrv213_verbs_shim.o && "
        f"rustc -O -D warnings mrv213_client.rs -C link-arg=mrv213_verbs_shim.o -C link-arg=-libverbs -o mrv213_client && "
        f"./mrv213_client --self-test"
    )
    print("[deploy] all binaries verified ready", flush=True)


class TestbedStack:
    def __init__(self):
        self.card_key = None
        self.merc_started = False

    def prepare_card_vfio(self) -> None:
        ssh_card(
            "tmux send-keys -t refl C-c 2>/dev/null || true; sleep 0.3; "
            "tmux kill-session -t refl 2>/dev/null || true; "
            "tmux kill-session -t m213c 2>/dev/null || true; "
            "rm -f /dev/hugepages/refl* /dev/hugepages/m213c*; "
            "ip link set eth0 down 2>/dev/null || true; "
            "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
            'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
            "drv2=$(basename $(readlink -f /sys/bus/pci/devices/0002:01:00.2/driver) 2>/dev/null || echo none); "
            'if [ "$drv2" != "vfio-pci" ]; then sh /root/bench/dpdk_up.sh 0002:01:00.2; fi'
        )

    def stop_card(self) -> None:
        try:
            tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "QUIT", timeout=2.0)
        except Exception:
            pass
        ssh_card(
            "sleep 0.3; tmux send-keys -t m213c C-c 2>/dev/null || true; sleep 0.3; "
            "tmux kill-session -t m213c 2>/dev/null || true; rm -f /dev/hugepages/m213c*",
            check=False,
        )
        self.card_key = None

    def ensure_card(self, sdp_tx_desc: int, eth_pool_size: int, fifo_mode: str) -> None:
        want = (sdp_tx_desc, eth_pool_size, fifo_mode)
        if self.card_key == want:
            try:
                if tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "PING", timeout=2.0).get("ok"):
                    return
            except Exception:
                pass
        self.stop_card()
        self.prepare_card_vfio()
        cmd = (
            f"{CARD_REMOTE_BIN} -l {CARD_LCORES} -a 0002:02:00.0 -a 0002:01:00.2 --file-prefix=m213c -- "
            f"--sdp-tx-desc {sdp_tx_desc} --eth-pool-size {eth_pool_size} --fifo-mode {fifo_mode} "
            f"--fifo-cap-bytes {192 * MB}"
        )
        r = ssh_card(
            f'rm -f /tmp/m213_card.log; '
            f'tmux new-session -d -s m213c "{cmd} > /tmp/m213_card.log 2>&1"; '
            f'for i in $(seq 1 80); do grep -q "MRV213_CARD READY" /tmp/m213_card.log 2>/dev/null && break; sleep 0.15; done; '
            f'grep "MRV213_CARD READY" /tmp/m213_card.log',
            check=False,
        )
        if r.returncode != 0:
            log = ssh_card("cat /tmp/m213_card.log", check=False).stdout
            raise RuntimeError(f"mrv213_card failed to start:\n{log}")
        self.card_key = want

    def ensure_mercury(self) -> None:
        if self.merc_started:
            try:
                if tcp_cmd(MERC_CTRL_HOST, MERC_CTRL_PORT, "PING", timeout=2.0).get("ok"):
                    return
            except Exception:
                pass
        self.stop_mercury()
        cmd = (
            f"{MERC_REMOTE_BIN} --port {MERC_CTRL_PORT} --dev mlx5_2 --ifname ens6f1np1 "
            f"--src-ip 10.10.100.1 --dst-ip 10.10.100.2 --sgid 3"
        )
        r = ssh_merc(
            f'rm -f /tmp/m213_merc.log; '
            f'nohup {cmd} > /tmp/m213_merc.log 2>&1 < /dev/null & '
            f'for i in $(seq 1 60); do grep -q "MRV213_CLIENT READY" /tmp/m213_merc.log 2>/dev/null && break; sleep 0.15; done; '
            f'grep "MRV213_CLIENT READY" /tmp/m213_merc.log',
            check=False,
        )
        if r.returncode != 0:
            log = ssh_merc("cat /tmp/m213_merc.log", check=False).stdout
            raise RuntimeError(f"mrv213_client failed to start on mercury:\n{log}")
        self.merc_started = True

    def stop_mercury(self) -> None:
        try:
            tcp_cmd(MERC_CTRL_HOST, MERC_CTRL_PORT, "QUIT", timeout=2.0)
        except Exception:
            pass
        ssh_merc("pkill -f mrv213_client 2>/dev/null || true", check=False)
        self.merc_started = False

    def probe_balanced_flows(self, profile: str) -> str:
        """Probe CN106 eth0 RSS queues and return a 1:1 balanced comma-separated flow list."""
        pcfg = PROFILE_CFG[profile]
        self.ensure_card(2048, 16384, "compact")
        self.ensure_mercury()
        tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, f"CONFIG policy=idle profile={profile} run_gen=1")
        tcp_cmd(MERC_CTRL_HOST, MERC_CTRL_PORT, f"PROBE_RSS mode={pcfg['probe_mode']} count=64 run_gen=1")
        time.sleep(0.05)
        st = tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "STATS")
        fq = st.get("flow_q", [])
        by_q = {q: [] for q in range(SDP_QUEUES)}
        for idx, q1 in enumerate(fq):
            if 1 <= q1 <= SDP_QUEUES:
                by_q[q1 - 1].append(idx + 1)
        balanced = []
        max_d = max((len(v) for v in by_q.values()), default=0)
        for d in range(max_d):
            for q in range(SDP_QUEUES):
                if d < len(by_q[q]):
                    balanced.append(by_q[q][d])
        want_n = pcfg["merc_threads"]
        if len(balanced) < want_n:
            print(f"  [WARN] RSS probe({profile}) balanced={len(balanced)} < {want_n}, per_q={[len(v) for v in by_q.values()]}", flush=True)
        else:
            print(f"  [RSS] profile={profile}: balanced {want_n} flows across {SDP_QUEUES} queues ({balanced[:want_n]})", flush=True)
        return ",".join(str(x) for x in balanced[:want_n])

    def restore_baseline(self) -> dict:
        self.stop_mercury()
        self.stop_card()
        sh(["rm", "-f", "/dev/hugepages/m213h*"], check=False)
        out = ssh_card(
            f"{CARD_REMOTE_BIN} --restore 2>&1 || true; "
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


def check_quality_gates(rec: dict) -> tuple[list[str], list[str]]:
    """Return `(hard_errors, clock_errors)` separating fatal integrity errors from clock retries."""
    hard = []
    clock = []
    arm = rec["arm"]
    hp = rec["host_pmu"]
    cp = rec.get("card_pmu")
    cs = rec.get("card_stats")
    ms = rec.get("mercury")
    kv = rec.get("kv")

    if hp["min_pct"] < 99.99:
        hard.append(f"host_pmu min_pct={hp['min_pct']:.2f}")
    if hp["mesh_ghz"] < MIN_MESH_GHZ:
        clock.append(f"mesh_ghz={hp['mesh_ghz']:.3f}")

    if arm != "kv-solo":
        if hp["rx"]["ghz"] < MIN_CORE_GHZ:
            clock.append(f"rx_ghz={hp['rx']['ghz']:.3f}")
        if hp["rx"]["page_faults"] != 0:
            hard.append(f"rx_pf={hp['rx']['page_faults']}")
        if rec["bad"] != 0 or rec["torn"] != 0:
            hard.append(f"host_integrity bad={rec['bad']} torn={rec['torn']}")
        if arm == "credit-ring" and not rec.get("bar4_lease_ok", False):
            hard.append("bar4_lease_lost")
        if cp is None or cs is None or ms is None:
            hard.append("missing card/mercury telemetry")
        else:
            if cp["min_pct"] < 99.99:
                hard.append(f"card_pmu min_pct={cp['min_pct']:.2f}")
            if cs["wire_bad"] != 0 or cs["wire_torn"] != 0 or cs["credit_torn"] != 0:
                hard.append(f"card_integrity wb={cs['wire_bad']} wt={cs['wire_torn']} ct={cs['credit_torn']}")
            if ms["cqe_err"] != 0 or ms["post_err"] != 0:
                hard.append(f"merc_err cqe={ms['cqe_err']} post={ms['post_err']}")
            if ms["phy"]["tx_pause"] != 0 or ms["phy"]["rx_pause"] != 0:
                hard.append(f"phy_pause tx={ms['phy']['tx_pause']} rx={ms['phy']['rx_pause']}")
            if arm != "small-drop":
                if rec["lost_pkts"] != 0:
                    hard.append(f"unexpected_lost={rec['lost_pkts']}")
                if cs["sdp_tx_drops"] != 0 or cs["fifo_drops"] != 0:
                    hard.append(f"card_drops sdp={cs['sdp_tx_drops']} fifo={cs['fifo_drops']}")
                if cs["eth"]["imissed"] != 0:
                    hard.append(f"eth_imissed={cs['eth']['imissed']}")
            else:
                # Arm 2: every lost packet must be accounted for by card sdp_tx_drops (allow 0 unexplained wire loss)
                unexplained = abs(rec["lost_pkts"] - cs["sdp_tx_drops"] - cs["eth"]["imissed"])
                if unexplained > 0:
                    hard.append(
                        f"drop_accounting_mismatch lost={rec['lost_pkts']} sdp_drops={cs['sdp_tx_drops']} imissed={cs['eth']['imissed']}"
                    )

    if kv is not None:
        kp = hp["kv"]
        if kv["bad"] != 0:
            hard.append(f"kv_bad={kv['bad']}")
        if kv["page_faults"] != 0 or kp["page_faults"] != 0:
            hard.append(f"kv_pf={kv['page_faults']}/{kp['page_faults']}")
        if kp["ghz"] < MIN_CORE_GHZ:
            clock.append(f"kv_ghz={kp['ghz']:.3f}")
        if rec["kv_page_kb"] != 2048 or rec["kv_n0"] != 0 or rec["kv_n1"] <= 0:
            hard.append(f"kv_numa={rec['kv_page_kb']}k/N0={rec['kv_n0']}/N1={rec['kv_n1']}")

    return hard, clock


def build_schedule(part: str, reps: int, smoke: bool) -> list[dict]:
    sched = []
    bursts = SMOKE_BURSTS if smoke else BURST_MATRIX

    def add_profile_sweep(part_name: str, profile: str, fifo_mode: str = "compact"):
        pcfg = PROFILE_CFG[profile]
        for rep in range(reps):
            sched.append({
                "part": part_name,
                "tag": f"kv_solo_32m_{profile}",
                "rep": rep,
                "arm_tag": "kv_solo",
                "arm": "kv-solo",
                "profile": profile,
                "burst_label": "0",
                "burst_bytes": 0,
                "bursts": 0,
                "warmup_bursts": 0,
                "rx_desc": 512,
                "allow_small_oq": False,
                "r_eff": 256,
                "card_sdp_tx_desc": 2048,
                "card_eth_pool": 16384,
                "card_fifo_mode": fifo_mode,
                "merc_threads": pcfg["merc_threads"],
                "merc_batch": pcfg["merc_batch"],
            })
            rot_arms = ARMS[rep % len(ARMS):] + ARMS[:rep % len(ARMS)]
            for b_lbl, b_bytes, n_bursts, wu_bursts in bursts:
                for arm_tag, arm_flag, rx_desc, small_oq, r_eff, sdp_txd in rot_arms:
                    sched.append({
                        "part": part_name,
                        "tag": f"{arm_tag}_{profile}_{b_lbl}",
                        "rep": rep,
                        "arm_tag": arm_tag,
                        "arm": arm_flag,
                        "profile": profile,
                        "burst_label": b_lbl,
                        "burst_bytes": b_bytes,
                        "bursts": n_bursts,
                        "warmup_bursts": wu_bursts,
                        "rx_desc": rx_desc,
                        "allow_small_oq": small_oq,
                        "r_eff": r_eff,
                        "card_sdp_tx_desc": sdp_txd,
                        "card_eth_pool": 16384,
                        "card_fifo_mode": fifo_mode,
                        "merc_threads": pcfg["merc_threads"],
                        "merc_batch": pcfg["merc_batch"],
                    })

    if part in ("all", "main_1500b"):
        add_profile_sweep("e2_1500b_kv32m", "1500b", "compact")
    if part in ("all", "size_sweep_256b"):
        add_profile_sweep("e2_256b_kv32m", "256b", "compact")
    if part in ("all", "size_sweep_64b"):
        add_profile_sweep("e2_64b_kv32m", "64b", "compact")
    if part in ("all", "mbuf_fifo_check"):
        pcfg = PROFILE_CFG["1500b"]
        ab_bursts = SMOKE_BURSTS if smoke else MBUF_ABLATION_BURSTS
        for rep in range(reps):
            for b_lbl, b_bytes, n_bursts, wu_bursts in ab_bursts:
                for fm, pool_sz in [("compact", 16384), ("mbuf", 131072)]:
                    sched.append({
                        "part": "e2_1500b_mbuf_ablation",
                        "tag": f"credit_ring_{fm}_1500b_{b_lbl}",
                        "rep": rep,
                        "arm_tag": f"credit_ring_{fm}",
                        "arm": "credit-ring",
                        "profile": "1500b",
                        "burst_label": b_lbl,
                        "burst_bytes": b_bytes,
                        "bursts": n_bursts,
                        "warmup_bursts": wu_bursts,
                        "rx_desc": 512,
                        "allow_small_oq": True,
                        "r_eff": 256,
                        "card_sdp_tx_desc": 2048,
                        "card_eth_pool": pool_sz,
                        "card_fifo_mode": fm,
                        "merc_threads": pcfg["merc_threads"],
                        "merc_batch": pcfg["merc_batch"],
                    })

    return sched


def build_host_cmd(spec: dict, run_gen: int, flows: str) -> list[str]:
    if spec["arm"] == "kv-solo":
        devarg = "0000:99:02.0"
        app = [
            "--arm", "kv-solo",
            "--profile", spec["profile"],
            "--kv-bytes", str(32 * MB),
            "--kv-cpu", "41",
            "--solo-ms", "500",
            "--warmup-ms", "1100",
        ]
    else:
        dev_parts = ["0000:99:02.0", "ism_enable=1", f"max_rings={SDP_QUEUES}"]
        if spec["allow_small_oq"]:
            dev_parts.append("allow_small_oq=1")
        devarg = ",".join(dev_parts)
        app = [
            "--arm", spec["arm"],
            "--profile", spec["profile"],
            "--burst-bytes", str(spec["burst_bytes"]),
            "--bursts", str(spec["bursts"]),
            "--warmup-bursts", str(spec["warmup_bursts"]),
            "--sdp-queues", str(SDP_QUEUES),
            "--rx-desc", str(spec["rx_desc"]),
            "--tx-desc", "2048",
            "--r-eff", str(spec["r_eff"]),
            "--credit-batch", "64",
            "--card-fifo-mode", spec["card_fifo_mode"],
            "--merc-threads", str(spec["merc_threads"]),
            "--merc-batch", str(spec["merc_batch"]),
            "--kv-bytes", str(32 * MB),
            "--kv-cpu", "41",
            "--warmup-ms", "1100",
            "--run-gen", str(run_gen),
        ]
        if flows:
            app += ["--merc-flows", flows]

    return [
        str(HOST_BIN),
        "-l", "40",
        "-a", devarg,
        "--socket-mem=0,2048",
        "--iova-mode=va",
        "--file-prefix=m213h",
        "--log-level=lib.eal:warning",
        "--",
    ] + app


def run_one_point(
    stack: TestbedStack,
    run_id: int,
    spec: dict,
    flows: str,
    run_gen_counter: list[int],
) -> dict:
    if spec["arm"] != "kv-solo":
        stack.ensure_card(spec["card_sdp_tx_desc"], spec["card_eth_pool"], spec["card_fifo_mode"])
        stack.ensure_mercury()

    for attempt in range(1, MAX_CLOCK_RETRIES + 1):
        rg = run_gen_counter[0]
        run_gen_counter[0] += 4
        cmd = build_host_cmd(spec, rg, flows)
        sh(["rm", "-f", "/dev/hugepages/m213h*"], check=False)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        lines = [l for l in p.stdout.splitlines() if l.startswith("MRV213 ")]
        if p.returncode != 0 or not lines:
            err_rec = {
                "run_id": run_id,
                "ts_utc": utc_now(),
                "part": spec["part"],
                "tag": spec["tag"],
                "rep": spec["rep"],
                "attempt": attempt,
                "verified": False,
                "error": f"rc={p.returncode} stderr={p.stderr.strip()[:300]}",
                "cmd": " ".join(cmd),
            }
            append_jsonl(ATTEMPTS_PATH, err_rec)
            raise RuntimeError(f"run_id={run_id} {spec['tag']} failed: {err_rec['error']}")

        rec = json.loads(lines[-1].split("MRV213 ", 1)[1])
        hard_errs, clock_errs = check_quality_gates(rec)
        full = {
            "run_id": run_id,
            "ts_utc": utc_now(),
            "part": spec["part"],
            "tag": spec["tag"],
            "rep": spec["rep"],
            "arm_tag": spec["arm_tag"],
            "burst_label": spec["burst_label"],
            "attempt": attempt,
            "verified": (not hard_errs and not clock_errs),
            "hard_errors": hard_errs,
            "clock_errors": clock_errs,
            "cmd": " ".join(cmd),
        }
        full.update(rec)
        append_jsonl(ATTEMPTS_PATH, full)

        if hard_errs:
            raise RuntimeError(
                f"run_id={run_id} {spec['part']}/{spec['tag']} rep={spec['rep']} hard gate fail: {', '.join(hard_errs)}"
            )
        if clock_errs:
            print(
                f"  [clock retry {attempt}/{MAX_CLOCK_RETRIES}] {spec['tag']} rep={spec['rep']}: {', '.join(clock_errs)}",
                flush=True,
            )
            time.sleep(2.0)
            continue

        append_jsonl(RUNS_PATH, full)
        return full

    raise RuntimeError(f"run_id={run_id} {spec['tag']} exceeded clock retries")


def main() -> int:
    ap = argparse.ArgumentParser(description="MRV-213 (MRV-203 E2) 100GbE standard-SDP burst harness")
    ap.add_argument(
        "--part",
        choices=["all", "main_1500b", "size_sweep_256b", "size_sweep_64b", "mbuf_fifo_check"],
        default="all",
    )
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--smoke", action="store_true", help="Run compact 3-burst smoke subset")
    ap.add_argument("--resume", action="store_true", help="Skip (part, tag, rep) already in runs.jsonl")
    ap.add_argument("--skip-deploy", action="store_true", help="Do not scp/build binaries on CN106/mercury")
    ap.add_argument("--dry-run", action="store_true", help="Print schedule & sample commands offline (ZERO hardware access)")
    args = ap.parse_args()

    sched = build_schedule(args.part, args.reps, args.smoke)
    if args.dry_run:
        print(f"[DRY-RUN] MRV-213 schedule: part={args.part} reps={args.reps} smoke={args.smoke} total_runs={len(sched)}")
        by_part: dict[str, int] = {}
        for sp in sched:
            by_part[sp["part"]] = by_part.get(sp["part"], 0) + 1
        for k, v in by_part.items():
            print(f"  {k}: {v} runs")
        print(f"  host_bin_exists={HOST_BIN.exists()} ({HOST_BIN})")
        print(f"  card_bin_exists={CARD_LOCAL_BIN.exists()} ({CARD_LOCAL_BIN})")
        print(f"  merc_srcs_exist={(MERC_SRC_DIR / 'mrv213_client.rs').exists()} / {(MERC_SRC_DIR / 'mrv213_verbs_shim.c').exists()}")
        # Print sample host commands for each distinct arm
        seen = set()
        for sp in sched:
            if sp["arm_tag"] not in seen:
                seen.add(sp["arm_tag"])
                cmd = build_host_cmd(sp, 100, "1,2,3,4,5,6,7,8")
                print(f"  sample[{sp['arm_tag']}]: {' '.join(cmd)}")
        return 0

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not args.skip_deploy:
        deploy_binaries()

    done_keys = load_completed_keys(RUNS_PATH) if args.resume else set()
    env_doc = {"issue": "MRV-213", "part": args.part, "reps": args.reps, "smoke": args.smoke}
    env_doc["env_before"] = read_env_snapshot()

    stack = TestbedStack()
    flow_cache: dict[str, str] = {}
    run_gen_counter = [100]
    run_id = len(done_keys)
    t_campaign0 = time.monotonic()

    try:
        profiles_needed = sorted({sp["profile"] for sp in sched if sp["arm"] != "kv-solo"})
        for prof in profiles_needed:
            flow_cache[prof] = stack.probe_balanced_flows(prof)

        for sp in sched:
            key = (sp["part"], sp["tag"], sp["rep"])
            if key in done_keys:
                continue
            run_id += 1
            flows = flow_cache.get(sp["profile"], "")
            rec = run_one_point(stack, run_id, sp, flows, run_gen_counter)
            kv_p99 = rec["kv"]["p99_ns"] if rec.get("kv") else 0.0
            kv_p999 = rec["kv"]["p999_ns"] if rec.get("kv") else 0.0
            ddio = rec["host_pmu"]["ddio_hit_pct"]
            imc_mib = rec["host_pmu"]["imc_all_rd_mib"] + rec["host_pmu"]["imc_all_wr_mib"]
            drain_g = rec.get("drain_wire_gbps", 0.0)
            drop_p = rec.get("drop_pct", 0.0)
            print(
                f"[{run_id:3d}/{len(sched)}] {sp['part']}/{sp['tag']} rep={sp['rep']} "
                f"drop={drop_p:5.2f}% drain={drain_g:6.2f}Gbps kv_p99/p999={kv_p99:5.1f}/{kv_p999:5.1f}ns "
                f"ddio={ddio:5.1f}% host_imc={imc_mib:6.1f}MiB",
                flush=True,
            )

        env_doc["env_after_runs"] = read_env_snapshot()
    finally:
        env_doc["restore_info"] = stack.restore_baseline()
        print("[restore] waiting 21s for EDAC/AER delayed counters ...", flush=True)
        time.sleep(21.0)
        env_doc["env_post_restore_21s"] = read_env_snapshot()
        env_doc["elapsed_s"] = round(time.monotonic() - t_campaign0, 2)
        ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n")
        print(f"[done] saved env snapshot to {ENV_PATH}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
