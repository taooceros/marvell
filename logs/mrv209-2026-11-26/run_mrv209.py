#!/usr/bin/env python3
"""MRV-209 (MRV-203 E5) Automated Experiment Harness.

End-to-end real application benchmark over 100% unified standard DPDK SDP data plane:
  - App A: 100% unmodified upstream Memcached 1.6.45 (Binary UDP, mlocked NUMA-1 slabs)
  - App B: Lightweight stateful 5-tuple NFV (NAT / Maglev LB, ShRing/rxBisect comparable)
  - Co-located Tenant: 32 MiB pointer-chasing + self-verifying KV index on independent
    Socket-1 physical core (cpu41, same 60 MiB L3 / 2-way DDIO / 2-ch DDR5 as cpu40)
  - Traffic: mercury 100GbE RoCEv2 open-loop Poisson, Poisson + periodic microbursts
    (WebSearch / Facebook ETC), and synchronized many-to-one Incast
  - Single-variable 4-policy comparison (fixed standard SDP data plane):
      1. large_sdp      : Large host SDP ring + large non-compact mbuf pool (default privRing)
      2. small_sdp_drop : Small host SDP ring (allow_small_oq=1, compact pool), no card FIFO
      3. ceio_style     : Reactive per-flow dual-path (switch lag leak + slow queue + phase
                          exclusivity pause + host 2P1C SW merge ring)
      4. credit_sdp     : On-Path SmartNIC head-resetting L3-local ElasticFifo + compact
                          DDIO-hot SDP ring (nb_desc=512, WMARK=128, R_eff=128) + no-SFENCE
                          BAR4 MOVDIR64B per-queue credit ring (B=32)

Offline validation (ZERO hardware access):
  python3 logs/mrv209-2026-11-26/run_mrv209.py --dry-run
"""

import argparse
import json
import pathlib
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

WORKSPACE = pathlib.Path("/home/hongtao/dev/marvell-mrv209")
LOG_DIR = WORKSPACE / "logs/mrv209-2026-11-26"
HOST_BIN = str(WORKSPACE / "testbed/target/release/mrv209_host")
CARD_BIN_LOCAL = str(WORKSPACE / "testbed/target/aarch64-unknown-linux-gnu/release/mrv209_card")
CARD_BIN = "/root/bench/mrv209_card"
MERC_SRC_DIR = WORKSPACE / "testbed/mercury-client"
MERC_BIN = "/tmp/mrv209/mrv209_client"

CARD_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mercury"]

ETH_DEV = "0002:02:00.0"
SDP_DEV_CARD = "0002:01:00.2"
SDP_DEV_HOST = "0000:99:02.0"

CARD_SYNC_PORT = 7549
HOST_CTRL_PORT = 7551

APP_CPU = 40
TENANT_CPU = 41
TENANT_WS_MB = 32

POLICIES = ("large_sdp", "small_sdp_drop", "ceio_style", "credit_sdp")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sh(cmd, check=True, timeout=120):
    return subprocess.run(cmd, text=True, capture_output=True, check=check, timeout=timeout)


def ssh_card(script, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def tcp_cmd(host: str, port: int, cmd: str, timeout: float = 15.0):
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((cmd.strip() + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode().strip())


def workload_app(workload: str) -> str:
    if workload.startswith("nfv_lb"):
        return "nfv-lb"
    if workload.startswith("nfv_"):
        return "nfv-nat"
    return "memcached"


def workload_profile(workload: str) -> str:
    if workload in ("get8", "set8", "rw8", "8"):
        return "8"
    if workload in ("get128", "set128", "rw128", "128"):
        return "128"
    if workload in ("get1024", "1024"):
        return "1024"
    if workload in ("get3840", "3840"):
        return "3840"
    if workload in ("small_mix", "etc_small"):
        return "small_mix"
    if workload in ("websearch_mc", "websearch"):
        return "websearch_mc"
    return "etc_mix"


def sdp_buf_size_for_workload(workload: str) -> int:
    # NFV & <= 1024 B workloads fit cleanly in 2176 B mbufs (half the DDIO footprint);
    # Memcached etc_mix / websearch_mc / 3840 B require 4608 B jumbo mbufs.
    if workload.startswith("nfv_") or workload in ("get8", "set8", "rw8", "get128", "set128", "rw128", "small_mix", "get1024"):
        return 2176
    return 4608


def build_policy_spec(policy: str, app: str, buf_size: int, n_queues: int = 4) -> dict:
    """Single-variable policy configuration over 100% unified standard SDP data plane."""
    if policy == "large_sdp":
        return {
            "policy_cli": "large-sdp",
            "card_sdp_tx_desc": 2048,
            "card_sdp_rx_desc": 2048,
            "card_sdp_pool": 65535,
            "card_r_eff": 0,
            "card_elastic_cap": 0,
            "card_drop_inflight_cap": 0,
            "host_sdp_rx_desc": 2048,
            "host_sdp_pool": 32768,
            "host_allow_small_oq": 0,
            "host_r_eff": 0,
            "host_credit_batch": 32,
            "allow_drops": False,
            "app": app,
            "buf_size": buf_size,
            "n_queues": n_queues,
        }
    if policy == "small_sdp_drop":
        return {
            "policy_cli": "small-sdp-drop",
            "card_sdp_tx_desc": 256,
            "card_sdp_rx_desc": 2048,
            "card_sdp_pool": 16384,
            "card_r_eff": 0,
            "card_elastic_cap": 0,
            "card_drop_inflight_cap": 128,
            "host_sdp_rx_desc": 256,
            "host_sdp_pool": 0,  # compact auto pool
            "host_allow_small_oq": 1,
            "host_r_eff": 0,
            "host_credit_batch": 32,
            "allow_drops": True,
            "app": app,
            "buf_size": buf_size,
            "n_queues": n_queues,
        }
    if policy == "ceio_style":
        return {
            "policy_cli": "ceio-style",
            "card_sdp_tx_desc": 2048,
            "card_sdp_rx_desc": 2048,
            "card_sdp_pool": 65535,
            "card_r_eff": 128,
            "card_elastic_cap": 16384,
            "card_drop_inflight_cap": 0,
            "ceio_flow_credit": 32,
            "ceio_switch_lag_us": 25,
            "ceio_phase_pause_us": 15,
            "ceio_window_us": 250,
            "ceio_fast_cap": 128,
            "ceio_slow_credit": 64,
            "host_sdp_rx_desc": 512,
            "host_sdp_pool": 0,  # compact auto pool
            "host_allow_small_oq": 1,
            "host_r_eff": 128,
            "host_credit_batch": 32,
            "allow_drops": True,
            "app": app,
            "buf_size": buf_size,
            "n_queues": n_queues,
        }
    if policy == "credit_sdp":
        return {
            "policy_cli": "credit-sdp",
            "card_sdp_tx_desc": 2048,
            "card_sdp_rx_desc": 2048,
            "card_sdp_pool": 65535,
            "card_r_eff": 128,
            "card_elastic_cap": 16384,
            "card_drop_inflight_cap": 0,
            "host_sdp_rx_desc": 512,
            "host_sdp_pool": 0,  # compact auto pool (Pitfall 2)
            "host_allow_small_oq": 1,
            "host_r_eff": 128,  # Pitfall 1: 128 <= 512 - WMARK(128) - 64 = 320
            "host_credit_batch": 32,
            "allow_drops": False,
            "app": app,
            "buf_size": buf_size,
            "n_queues": n_queues,
        }
    raise ValueError(f"Unknown policy: {policy}")


def build_card_cmd(spec: dict) -> str:
    nq = spec["n_queues"]
    lcores = "3" if nq == 1 else f"3-{3 + nq - 1}"
    rss_flag = " --no-sdp-rss" if nq == 1 else ""
    extra = ""
    if spec["policy_cli"] == "ceio-style":
        extra = (
            f" --ceio-flow-credit {spec['ceio_flow_credit']}"
            f" --ceio-switch-lag-us {spec['ceio_switch_lag_us']}"
            f" --ceio-phase-pause-us {spec['ceio_phase_pause_us']}"
            f" --ceio-window-us {spec['ceio_window_us']}"
            f" --ceio-fast-cap {spec['ceio_fast_cap']}"
            f" --ceio-slow-credit {spec['ceio_slow_credit']}"
        )
    return (
        f"{CARD_BIN} -l {lcores} -a {ETH_DEV} -a {SDP_DEV_CARD} --file-prefix=m209 -- "
        f"--policy {spec['policy_cli']} --sdp-rx-desc {spec['card_sdp_rx_desc']} "
        f"--sdp-tx-desc {spec['card_sdp_tx_desc']} --sdp-buf-size {spec['buf_size']} "
        f"--sdp-pool-size {spec['card_sdp_pool']} --r-eff {spec['card_r_eff']} "
        f"--elastic-cap {spec['card_elastic_cap']} --drop-inflight-cap {spec['card_drop_inflight_cap']}"
        f"{rss_flag}{extra}"
    )


def build_host_cmd(spec: dict, tenant: bool = True) -> list:
    nq = spec["n_queues"]
    devarg = (
        f"{SDP_DEV_HOST},rx_poll_mem=1,rx_poll_tail_off=48,"
        f"ism_enable=1,allow_small_oq={spec['host_allow_small_oq']},max_rings={nq}"
    )
    numactl_bin = shutil.which("numactl")
    if not numactl_bin:
        raise RuntimeError("numactl not found in PATH; run inside `devenv shell` to enforce --membind=1")
    prefix = [numactl_bin, "--membind=1"]
    return prefix + [
        HOST_BIN,
        "-l", str(APP_CPU),
        "-a", devarg,
        "--socket-mem=0,1024",
        "--iova-mode=va",
        "--file-prefix=m209h",
        "--",
        "--policy", spec["policy_cli"],
        "--app", spec["app"],
        "--mc-profile", "etc_mix",
        "--sdp-queues", str(nq),
        "--sdp-rx-desc", str(spec["host_sdp_rx_desc"]),
        "--sdp-tx-desc", "2048",
        "--sdp-buf-size", str(spec["buf_size"]),
        "--sdp-pool-size", str(spec["host_sdp_pool"]),
        "--sdp-zero-copy", "true",
        "--r-eff", str(spec["host_r_eff"]),
        "--credit-batch", str(spec["host_credit_batch"]),
        "--tenant", "true" if tenant else "false",
        "--tenant-cpu", str(TENANT_CPU),
        "--tenant-ws-mb", str(TENANT_WS_MB),
        "--pmu", "true",
        "--ctrl", f"127.0.0.1:{HOST_CTRL_PORT}",
    ]


def build_client_cmd(
    workload: str,
    drive_mode: str,
    rate_krps: float,
    window: int,
    threads: int,
    card_queues: int,
    num_flows: int,
    warmup_ms: int,
    duration_ms: int,
    seed: int,
    allow_drops: bool,
    burst_period_ms: int = 25,
    burst_dur_us: int = 1500,
    burst_rate_krps: float = 4500.0,
    incast_pkts: int = 512,
    key_dist: str = "zipf",
    set_pct: int = 5,
) -> str:
    drop_flag = " --allow-drops --drop-timeout-ms 100" if allow_drops else ""
    return (
        f"{MERC_BIN} --dev mlx5_2 --sgid 3 --cpu 4 --threads {threads} --card-queues {card_queues} "
        f"--mode {drive_mode} --workload {workload} --key-dist {key_dist} --set-pct {set_pct} "
        f"--num-flows {num_flows} --window {window} --rate-krps {rate_krps:.3f} "
        f"--burst-period-ms {burst_period_ms} --burst-dur-us {burst_dur_us} "
        f"--burst-rate-krps {burst_rate_krps:.3f} --incast-pkts {incast_pkts} "
        f"--warmup-ms {warmup_ms} --duration-ms {duration_ms} --seed {seed} --sync-reset{drop_flag}"
    )


def read_merc_ethtool() -> dict:
    out = sh(
        MERC_SSH + ["ethtool -S ens6f1np1 | grep -E 'rx_out_of_buffer|rx_discards_phy|rx_crc_errors_phy|rx_pause|tx_pause'"],
        check=False,
    ).stdout
    res = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            try:
                res[k] = int(v.strip())
            except ValueError:
                pass
    return res


def read_clocks_edac() -> dict:
    def read_cpu(c: int):
        gov = pathlib.Path(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_governor").read_text().strip()
        khz = int(pathlib.Path(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq").read_text().strip())
        return gov, khz

    g40, f40 = read_cpu(APP_CPU)
    g41, f41 = read_cpu(TENANT_CPU)
    unc = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("package_*")):
        cf = p / "current_freq_khz"
        if cf.exists():
            unc[p.name] = int(cf.read_text().strip())
    host_ue = []
    for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count")):
        try:
            host_ue.append(int(p.read_text().strip()))
        except Exception:
            pass
    rp_aer = {}
    rp = pathlib.Path("/sys/bus/pci/devices/0000:98:01.0")
    for name in ("aer_rootport_total_err_cor", "aer_rootport_total_err_nonfatal", "aer_rootport_total_err_fatal"):
        f = rp / name
        rp_aer[name] = int(f.read_text().strip()) if f.exists() else -1
    card_raw = ssh_card(
        "echo mc0_ue=$(cat /sys/devices/system/edac/mc/mc0/ue_count 2>/dev/null || echo 0) "
        "mc0_ce=$(cat /sys/devices/system/edac/mc/mc0/ce_count 2>/dev/null || echo 0) "
        "ea0_ue=$(cat /sys/devices/system/edac/ea/ea0/ue_count 2>/dev/null || echo 0) "
        "ea0_ce=$(cat /sys/devices/system/edac/ea/ea0/ce_count 2>/dev/null || echo 0) "
        "sei_total=$(dmesg | grep -c 'EDAC DEVICE4' || echo 0)",
        check=False,
    ).stdout.strip()
    card_edac = {}
    for tok in card_raw.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            try:
                card_edac[k] = int(v.strip())
            except ValueError:
                pass
    return {
        "ts": utc_now(),
        "saturn_cpu40_gov": g40,
        "saturn_cpu40_khz": f40,
        "saturn_cpu41_gov": g41,
        "saturn_cpu41_khz": f41,
        "saturn_uncore_khz": unc,
        "saturn_edac_ue": host_ue,
        "saturn_edac_ue_sum": sum(host_ue),
        "saturn_rp_aer": rp_aer,
        "card_edac": card_edac,
    }


def deploy_binaries():
    """Copy pre-built aarch64 mrv209_card to CN106 and build mrv209_client on mercury."""
    if not pathlib.Path(HOST_BIN).exists():
        raise FileNotFoundError(f"Missing host binary: {HOST_BIN}")
    if not pathlib.Path(CARD_BIN_LOCAL).exists():
        raise FileNotFoundError(f"Missing card binary: {CARD_BIN_LOCAL}")
    sh(["scp", "-o", "BatchMode=yes", CARD_BIN_LOCAL, f"root@10.10.0.2:{CARD_BIN}"])
    sh(MERC_SSH + ["mkdir -p /tmp/mrv209"])
    sh([
        "scp", "-o", "BatchMode=yes",
        str(MERC_SRC_DIR / "mrv209_verbs_shim.c"),
        str(MERC_SRC_DIR / "mrv209_client.rs"),
        "mercury:/tmp/mrv209/",
    ])
    sh(
        MERC_SSH + [
            'export PATH="$HOME/.cargo/bin:$PATH"; '
            "cd /tmp/mrv209 && "
            "gcc -O3 -c mrv209_verbs_shim.c -o mrv209_verbs_shim.o && "
            "rustc --edition 2021 -O mrv209_client.rs -C link-arg=mrv209_verbs_shim.o "
            "-C link-arg=-libverbs -o mrv209_client && "
            "./mrv209_client --self-test"
        ]
    )
    print("Deployed mrv209_card to CN106 and built/self-tested mrv209_client on mercury.", flush=True)


class Stack:
    def __init__(self):
        self.card_key = None
        self.host_key = None
        self.host_proc = None

    def stop_host(self):
        if self.host_proc is not None:
            if self.card_key is not None:
                try:
                    tcp_cmd("10.10.0.2", CARD_SYNC_PORT, "DETACH", timeout=3.0)
                except Exception:
                    pass
            try:
                tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "QUIT", timeout=3.0)
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
        for p in pathlib.Path("/dev/hugepages").glob("m209*"):
            try:
                p.unlink()
            except OSError:
                pass
        self.host_key = None

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m209 C-c 2>/dev/null || true; sleep 0.6; "
            "tmux kill-session -t m209 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/m209*"
        )
        self.card_key = None

    def stop_all_procs(self):
        self.stop_card()
        sh(MERC_SSH + ["pkill -f mrv209_client 2>/dev/null || true"], check=False)

    def ensure_stack(self, policy: str, workload: str, tenant: bool = True, n_queues: int = 4):
        app = workload_app(workload)
        buf_size = sdp_buf_size_for_workload(workload)
        spec = build_policy_spec(policy, app, buf_size, n_queues=n_queues)
        c_key = (policy, buf_size, n_queues)
        h_key = (policy, app, buf_size, n_queues, tenant)

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
            cmd = build_card_cmd(spec)
            ssh_card(
                f'rm -f /tmp/m209_card.log; '
                f'tmux new-session -d -s m209 "{cmd} > /tmp/m209_card.log 2>&1"; '
                f'for i in $(seq 1 80); do grep -q "MRV209_CARD READY" /tmp/m209_card.log 2>/dev/null && break; sleep 0.15; done; '
                f'grep "MRV209_CARD READY" /tmp/m209_card.log'
            )
            self.card_key = c_key

        if self.host_key != h_key or self.host_proc is None or self.host_proc.poll() is not None:
            self.stop_host()
            cmd = build_host_cmd(spec, tenant=tenant)
            log_path = pathlib.Path("/tmp/m209_host_run.log")
            with open(log_path, "w") as logf:
                self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
            for _ in range(120):
                if self.host_proc.poll() is not None:
                    raise RuntimeError(f"mrv209_host exited early:\n{log_path.read_text()}")
                txt = log_path.read_text()
                if "MRV209_HOST READY" in txt:
                    time.sleep(0.1)
                    self.host_key = h_key
                    return spec
                time.sleep(0.1)
            raise RuntimeError(f"mrv209_host ready timeout:\n{log_path.read_text()}")
        return spec

    def restore_baseline(self, clean_mercury_tmp: bool = True) -> dict:
        self.stop_all_procs()
        out = ssh_card(
            f"{CARD_BIN} --restore 2>/dev/null || true; "
            "rm -f /dev/hugepages/m209*; "
            "lsmod | grep -q '^pem_wc ' && rmmod pem_wc || true; "
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
            "tmux ls; "
            "lsmod | grep pem_wc || echo 'pem_wc unloaded'"
        ).stdout.strip()
        if clean_mercury_tmp:
            sh(MERC_SSH + ["rm -rf /tmp/mrv209*"], check=False)
        merc_chk = sh(
            MERC_SSH + ["pgrep -af mrv209_client || echo 'mercury proc clean'; ls -d /tmp/mrv209* 2>/dev/null || echo 'mercury tmp clean'"],
            check=False,
        ).stdout.strip()
        saturn_hp = sorted(str(p) for p in pathlib.Path("/dev/hugepages").glob("m209*"))
        return {"restore_stdout": out, "mercury_check": merc_chk, "saturn_m209_hugepages": saturn_hp}

def run_client_synced(
    policy: str,
    workload: str,
    drive_mode: str,
    rate_krps: float,
    window: int,
    threads: int,
    card_queues: int,
    num_flows: int,
    warmup_ms: int,
    duration_ms: int,
    seed: int,
    burst_period_ms: int = 25,
    burst_dur_us: int = 1500,
    burst_rate_krps: float = 4500.0,
    incast_pkts: int = 512,
    timeout: int = 90,
):
    allow_drops = policy in ("small_sdp_drop", "ceio_style")
    if workload_app(workload) == "memcached":
        prof = workload_profile(workload)
        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, f"RESET {prof}")
    else:
        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "RESET")
    tcp_cmd("10.10.0.2", CARD_SYNC_PORT, "RESET_PEERS")
    merc_eth_before = read_merc_ethtool()

    mcmd = build_client_cmd(
        workload=workload,
        drive_mode=drive_mode,
        rate_krps=rate_krps,
        window=window,
        threads=threads,
        card_queues=card_queues,
        num_flows=num_flows,
        warmup_ms=warmup_ms,
        duration_ms=duration_ms,
        seed=seed,
        allow_drops=allow_drops,
        burst_period_ms=burst_period_ms,
        burst_dur_us=burst_dur_us,
        burst_rate_krps=burst_rate_krps,
        incast_pkts=incast_pkts,
    )
    p = subprocess.Popen(
        MERC_SSH + [mcmd],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    lines = []
    warmup_seen = False
    t0 = time.time()
    try:
        while time.time() - t0 < 35.0:
            line = p.stdout.readline()
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
            raise RuntimeError(f"mercury client did not emit WARMUP_DONE:\n{''.join(lines)}{out}\n{err}")

        tcp_cmd("10.10.0.2", CARD_SYNC_PORT, "RESET")
        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "RESET")
        tad_before = tcp_cmd("10.10.0.2", CARD_SYNC_PORT, "PMU_SNAP")

        p.stdin.write("GO\n")
        p.stdin.flush()
        p.stdin.close()

        t1 = time.time()
        while time.time() - t1 < timeout:
            line = p.stdout.readline()
            if not line:
                break
            lines.append(line)
            if "MEAS_DONE" in line:
                break

        card_stats = tcp_cmd("10.10.0.2", CARD_SYNC_PORT, "STATS")
        tad_after = tcp_cmd("10.10.0.2", CARD_SYNC_PORT, "PMU_SNAP")
        host_stats = tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "STATS")

        rest_out, err = p.communicate(timeout=timeout)
        lines.append(rest_out or "")
        full_out = "".join(lines)

        client_json = None
        for l in full_out.splitlines():
            if l.startswith("MRV209_CLIENT_JSON "):
                client_json = json.loads(l[len("MRV209_CLIENT_JSON "):])
                break
        merc_eth_after = read_merc_ethtool()
        merc_eth_delta = {
            k: merc_eth_after.get(k, 0) - merc_eth_before.get(k, 0)
            for k in set(merc_eth_before) | set(merc_eth_after)
        }
        if client_json is None:
            raise RuntimeError(f"Missing MRV209_CLIENT_JSON (rc={p.returncode}):\n{full_out}\n{err}")
        client_json["merc_ethtool_delta"] = merc_eth_delta
        client_json["client_rc"] = p.returncode

        tad_delta = {}
        for k in tad_after:
            if k in tad_before:
                tad_delta[k] = {
                    "val": tad_after[k]["val"] - tad_before[k]["val"],
                    "pct": min(tad_before[k]["pct"], tad_after[k]["pct"]),
                }
        return client_json, host_stats, card_stats, tad_delta
    except Exception:
        if p.poll() is None:
            p.kill()
            try:
                p.communicate(timeout=3)
            except Exception:
                pass
        raise


def verify_run(policy: str, drive_mode: str, c: dict, h: dict, k: dict) -> list:
    errs = []
    allow_drops = policy in ("small_sdp_drop", "ceio_style")
    if (
        not c.get("integrity_ok", False)
        or c.get("bad", 0) != 0
        or c.get("proto_err", 0) != 0
        or c.get("val_err", 0) != 0
        or c.get("torn", 0) != 0
        or c.get("send_err", 0) != 0
    ):
        errs.append(
            f"client integrity bad={c.get('bad')} proto={c.get('proto_err')} val={c.get('val_err')} "
            f"torn={c.get('torn')} serr={c.get('send_err')}"
        )
    if not allow_drops and (not c.get("verified") or c.get("lost", 0) != 0):
        errs.append(f"client lost={c.get('lost')} verified={c.get('verified')}")
    if not allow_drops and drive_mode == "poisson" and c.get("window_cap_hits", 0) != 0:
        errs.append(f"poisson window_cap_hits={c.get('window_cap_hits')}")

    eth_d = c.get("merc_ethtool_delta") or {}
    for ek in ("rx_out_of_buffer", "rx_discards_phy", "rx_crc_errors_phy", "rx_pause_ctrl_phy", "tx_pause_ctrl_phy"):
        if eth_d.get(ek, 0) != 0:
            errs.append(f"merc_{ek}={eth_d.get(ek)}")
    if k.get("bad_wire", 0) != 0 or k.get("bad_host", 0) != 0 or k.get("torn_bar4", 0) != 0:
        errs.append(f"card bad_wire={k.get('bad_wire')} bad_host={k.get('bad_host')} torn_bar4={k.get('torn_bar4')}")
    if k.get("eth_ierrors", 0) != 0 or k.get("eth_oerrors", 0) != 0 or k.get("eth_tx_drops", 0) != 0:
        errs.append(f"card eth ierr={k.get('eth_ierrors')} oerr={k.get('eth_oerrors')} tx_drop={k.get('eth_tx_drops')}")
    if k.get("eth_imissed", 0) != 0 or k.get("elastic_drops", 0) != 0:
        errs.append(f"card drops eth_imissed={k.get('eth_imissed')} elastic_drops={k.get('elastic_drops')}")
    if policy != "small_sdp_drop" and k.get("sdp_tx_drops", 0) != 0:
        errs.append(f"card sdp_tx_drops={k.get('sdp_tx_drops')}")

    if h is not None:
        if (
            h.get("bad", 0) != 0
            or h.get("torn", 0) != 0
            or h.get("mc_misses", 0) != 0
            or h.get("mc_errors", 0) != 0
            or h.get("nfv_errors", 0) != 0
            or h.get("tx_drops", 0) != 0
            or h.get("sdp_rx_nombuf", 0) != 0
            or h.get("sdp_imissed", 0) != 0
            or h.get("sdp_ierrors", 0) != 0
            or h.get("sdp_oerrors", 0) != 0
        ):
            errs.append(
                f"host bad={h.get('bad')} torn={h.get('torn')} mc_err={h.get('mc_errors')} "
                f"nfv_err={h.get('nfv_errors')} tx_drop={h.get('tx_drops')} sdp_nombuf={h.get('sdp_rx_nombuf')}"
            )
        pmu = h.get("pmu") or {}
        tpmu = pmu.get("tenant_pmu") or {}
        if h.get("page_faults", 0) != 0 or pmu.get("page_faults", 0) != 0 or tpmu.get("page_faults", 0) != 0:
            errs.append(
                f"host page_faults={h.get('page_faults')} pmu_pf={pmu.get('page_faults')} tpmu_pf={tpmu.get('page_faults')}"
            )
        ms = h.get("mc_stats") or {}
        if ms.get("evictions", 0) != 0 or ms.get("get_misses", 0) != 0:
            errs.append(f"mc evictions={ms.get('evictions')} get_misses={ms.get('get_misses')}")
        ten = h.get("tenant")
        if ten is not None:
            if ten.get("bad", 0) != 0:
                errs.append(f"tenant bad={ten.get('bad')}")
            if tpmu and tpmu.get("ghz", 0.0) < 3.5:
                errs.append(f"tenant_ghz_low={tpmu.get('ghz')}")
        if pmu and pmu.get("ghz", 0.0) < 3.5:
            errs.append(f"app_ghz_low={pmu.get('ghz')}")
        if pmu and pmu.get("min_running_pct", 100.0) < 99.5:
            errs.append(f"host pmu mux min_running_pct={pmu.get('min_running_pct')}")
    return errs


def append_jsonl(path: pathlib.Path, rec: dict):
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def get_campaign_plan():
    """Return structured E5 single-variable experiment matrix (all unified standard SDP)."""
    return {
        "sec1_mc_microburst": [
            # (workload, drive_mode, rate_krps, burst_period_ms, burst_dur_us, burst_rate_krps, incast_pkts, num_flows)
            ("etc_mix",      "poisson_burst", 600.0, 25, 1500, 3500.0, 0, 256),
            ("websearch_mc", "poisson_burst", 600.0, 25, 1500, 3500.0, 0, 256),
        ],
        "sec2_mc_incast": [
            ("etc_mix",      "incast", 400.0, 25, 0, 0.0, 384, 256),
            ("etc_mix",      "incast", 400.0, 25, 0, 0.0, 512, 1024),
            ("websearch_mc", "incast", 400.0, 25, 0, 0.0, 384, 256),
        ],
        "sec3_nfv_burst_incast": [
            ("nfv_nat_256",   "poisson_burst", 1200.0, 20, 1500, 6000.0,   0, 256),
            ("nfv_nat_1024",  "poisson_burst",  800.0, 20, 1500, 4000.0,   0, 256),
            ("nfv_lb_256",    "incast",         800.0, 25,    0,    0.0, 512, 512),
            ("nfv_websearch", "incast",         800.0, 25,    0,    0.0, 512, 512),
        ],
        "sec4_poisson_and_flows": [
            # Idle/open-loop Poisson latency knee + high-flow-count churn (64 vs 4096 flows)
            ("etc_mix",     "poisson",  200.0, 25, 0, 0.0, 0,   64),
            ("etc_mix",     "poisson",  800.0, 25, 0, 0.0, 0,  256),
            ("etc_mix",     "poisson", 1400.0, 25, 0, 0.0, 0,  256),
            ("etc_mix",     "poisson_burst", 600.0, 25, 1500, 3500.0, 0, 4096),
            ("nfv_nat_256", "poisson",  500.0, 25, 0, 0.0, 0,   64),
            ("nfv_nat_256", "poisson", 1500.0, 25, 0, 0.0, 0, 4096),
        ],
    }


def run_dry_run():
    """Strictly offline self-check: verifies compiled binaries, Pitfall 1/2 bounds,
    policy command generation, and verify_run() logic with ZERO hardware access."""
    print("=== MRV-209 OFFLINE DRY-RUN SELF-CHECK (ZERO HARDWARE ACCESS) ===")
    for label, p in [
        ("host_bin", pathlib.Path(HOST_BIN)),
        ("card_bin", pathlib.Path(CARD_BIN_LOCAL)),
        ("client_rs", MERC_SRC_DIR / "mrv209_client.rs"),
        ("verbs_c", MERC_SRC_DIR / "mrv209_verbs_shim.c"),
    ]:
        exists = p.exists()
        sz = p.stat().st_size if exists else 0
        print(f"  artifact {label:10s}: exists={exists} size={sz} path={p}")
        assert exists and sz > 0, f"Missing artifact: {p}"

    # Verify Pitfall-1 safety invariant for credit_sdp
    cspec = build_policy_spec("credit_sdp", "memcached", 4608, 4)
    wmark = min(256, cspec["host_sdp_rx_desc"] // 4)
    safe_cap = cspec["host_sdp_rx_desc"] - wmark - 64
    assert cspec["host_r_eff"] <= safe_cap, f"Pitfall 1 violation: {cspec['host_r_eff']} > {safe_cap}"
    print(f"  Pitfall-1 check OK: nb_desc={cspec['host_sdp_rx_desc']} WMARK={wmark} safe_cap={safe_cap} R_eff={cspec['host_r_eff']}")

    # Verify verify_run catches faults and distinguishes small_sdp_drop drops vs credit_sdp drops
    ok_c = {"verified": True, "integrity_ok": True, "lost": 0, "bad": 0, "proto_err": 0, "val_err": 0, "torn": 0, "send_err": 0, "window_cap_hits": 0, "merc_ethtool_delta": {}}
    ok_h = {"bad": 0, "torn": 0, "mc_misses": 0, "mc_errors": 0, "nfv_errors": 0, "tx_drops": 0, "sdp_rx_nombuf": 0, "sdp_imissed": 0, "sdp_ierrors": 0, "sdp_oerrors": 0, "page_faults": 0, "mc_stats": {"evictions": 0, "get_misses": 0}, "tenant": {"bad": 0}, "pmu": {"ghz": 3.89, "page_faults": 0, "min_running_pct": 100.0, "tenant_pmu": {"ghz": 3.89, "page_faults": 0}}}
    ok_k = {"bad_wire": 0, "bad_host": 0, "torn_bar4": 0, "eth_ierrors": 0, "eth_oerrors": 0, "eth_tx_drops": 0, "eth_imissed": 0, "sdp_tx_drops": 0, "elastic_drops": 0}
    assert verify_run("credit_sdp", "poisson_burst", ok_c, ok_h, ok_k) == []

    drop_c = dict(ok_c, lost=120, verified=True)
    drop_k = dict(ok_k, sdp_tx_drops=120)
    assert verify_run("small_sdp_drop", "incast", drop_c, ok_h, drop_k) == []
    assert len(verify_run("credit_sdp", "incast", drop_c, ok_h, drop_k)) >= 2

    plan = get_campaign_plan()
    total_cases = sum(len(v) for v in plan.values()) * len(POLICIES)
    print(f"  Campaign matrix: {len(plan)} sections, {total_cases} policy-workload cells ({total_cases * 3} runs @ n=3)")
    for pol in POLICIES:
        spec = build_policy_spec(pol, "memcached", 4608, 4)
        print(f"  [{pol:14s}] card: {build_card_cmd(spec)}")
        print(f"  [{pol:14s}] host: {' '.join(build_host_cmd(spec, tenant=True))}")
    print("MRV209_DRY_RUN OK")


def run_verified_repeat(
    stack: Stack,
    runs_path: pathlib.Path,
    attempts_path: pathlib.Path,
    section: str,
    run_id: str,
    rep: int,
    policy: str,
    workload: str,
    drive_mode: str,
    rate_krps: float,
    burst_period_ms: int,
    burst_dur_us: int,
    burst_rate_krps: float,
    incast_pkts: int,
    num_flows: int,
    threads: int = 4,
    window: int = 2048,
    warmup_ms: int = 400,
    duration_ms: int = 3000,
    tenant: bool = True,
    max_attempts: int = 4,
    seed_base: int = 209000,
):
    for att in range(1, max_attempts + 1):
        stack.ensure_stack(policy, workload, tenant=tenant, n_queues=4)
        seed = seed_base + rep * 997 + att * 31
        c, h, k, tad = run_client_synced(
            policy=policy,
            workload=workload,
            drive_mode=drive_mode,
            rate_krps=rate_krps,
            window=window,
            threads=threads,
            card_queues=4,
            num_flows=num_flows,
            warmup_ms=warmup_ms,
            duration_ms=duration_ms,
            seed=seed,
            burst_period_ms=burst_period_ms,
            burst_dur_us=burst_dur_us,
            burst_rate_krps=burst_rate_krps,
            incast_pkts=incast_pkts,
        )
        errs = verify_run(policy, drive_mode, c, h, k)
        rec = {
            "ts": utc_now(),
            "section": section,
            "run_id": run_id,
            "rep": rep,
            "attempt": att,
            "verified": len(errs) == 0,
            "errors": errs,
            "policy": policy,
            "workload": workload,
            "app": workload_app(workload),
            "drive_mode": drive_mode,
            "rate_krps": rate_krps,
            "burst_period_ms": burst_period_ms,
            "burst_dur_us": burst_dur_us,
            "burst_rate_krps": burst_rate_krps,
            "incast_pkts": incast_pkts,
            "num_flows": num_flows,
            "threads": threads,
            "window": window,
            "tenant": tenant,
            "warmup_ms": warmup_ms,
            "duration_ms": duration_ms,
            "client": c,
            "host": h,
            "card": k,
            "tad_delta": tad,
        }
        append_jsonl(attempts_path, rec)
        pmu = (h or {}).get("pmu") or {}
        ten = (h or {}).get("tenant") or {}
        print(
            f"  [{section}] {run_id} r={rep} a={att} | "
            f"goodput={c['achieved_mrps']:5.2f}Mrps drop={c['drop_pct']:5.2f}% "
            f"p99={c['tot_p99_us']:6.1f}us p999={c['tot_p999_us']:7.1f}us | "
            f"ten_p99={ten.get('p99_ns', 0):5.1f}ns ten_p999={ten.get('p999_ns', 0):5.1f}ns | "
            f"ddio={pmu.get('ddio_hit_pct', 0):5.1f}% imc={pmu.get('imc_gbps', 0):5.2f}GB/s "
            f"| {'OK' if not errs else 'RETRY:' + ';'.join(errs)}",
            flush=True,
        )
        if not errs:
            append_jsonl(runs_path, rec)
            return rec
        time.sleep(0.3)
    raise RuntimeError(f"Exhausted {max_attempts} attempts for {run_id} rep={rep}")


def run_smoke(stack: Stack):
    print("=== MRV-209 END-TO-END SMOKE (ALL 4 POLICIES, MEMCACHED 1.6.45 + NFV + 32 MiB TENANT) ===", flush=True)
    edac0 = read_clocks_edac()
    ea0_start = edac0["card_edac"].get("ea0_ue", -1)
    mc0_start = edac0["card_edac"].get("mc0_ue", -1)
    print(
        f"Pre-smoke clocks/EDAC: cpu40={edac0['saturn_cpu40_khz']}kHz({edac0['saturn_cpu40_gov']}) "
        f"uncore={edac0['saturn_uncore_khz']} card_ea0_ue={ea0_start} card_mc0_ue={mc0_start} "
        f"saturn_ue={edac0['saturn_edac_ue_sum']} aer={edac0['saturn_rp_aer']}",
        flush=True,
    )
    # Warm up cpu41 schedutil clock to steady ~3.9 GHz before first run
    numactl_bin = shutil.which("numactl")
    if not numactl_bin:
        raise RuntimeError("numactl not found in PATH; run inside `devenv shell`")
    solo_out = sh([
        numactl_bin, "--membind=1",
        HOST_BIN, "--tenant-solo-ms", "1000",
        "--tenant-cpu", str(TENANT_CPU),
        "--tenant-ws-mb", str(TENANT_WS_MB),
        "--pmu", "true",
    ]).stdout.strip()
    solo_j = json.loads(solo_out.splitlines()[-1])
    print(f"Pre-smoke solo tenant warmup: ten={solo_j['tenant']} ghz={solo_j['pmu']['tenant_pmu']['ghz']}", flush=True)

    smoke_cases = [
        ("etc_mix",     "poisson_burst", 500.0, 25, 1500, 3000.0,   0, 256),
        ("etc_mix",     "incast",        400.0, 25,    0,    0.0, 384, 256),
        ("nfv_nat_256", "poisson_burst", 900.0, 25, 1500, 4500.0,   0, 256),
        ("nfv_lb_256",  "incast",        700.0, 25,    0,    0.0, 384, 256),
    ]
    smoke_path = LOG_DIR / "smoke.jsonl"
    attempts_path = LOG_DIR / "smoke_attempts.jsonl"
    for p in (smoke_path, attempts_path):
        if p.exists():
            p.unlink()

    for pol in POLICIES:
        for idx, (wl, dm, rk, bp, bd, br, ipkts, nf) in enumerate(smoke_cases, 1):
            rid = f"smoke_{pol}_{wl}_{dm}"
            run_verified_repeat(
                stack, smoke_path, attempts_path, "smoke", rid, 1,
                pol, wl, dm, rk, bp, bd, br, ipkts, nf,
                warmup_ms=300, duration_ms=1000, seed_base=20900 + idx * 10,
            )
    edac1 = read_clocks_edac()
    ea0_end = edac1["card_edac"].get("ea0_ue", -1)
    mc0_end = edac1["card_edac"].get("mc0_ue", -1)
    print(
        f"Post-smoke EDAC: card_ea0_ue {ea0_start}->{ea0_end} (delta={ea0_end - ea0_start}), "
        f"card_mc0_ue {mc0_start}->{mc0_end}, saturn_ue={edac1['saturn_edac_ue_sum']}, aer={edac1['saturn_rp_aer']}",
        flush=True,
    )
    if ea0_start != ea0_end or mc0_start != mc0_end or edac1["saturn_edac_ue_sum"] != edac0["saturn_edac_ue_sum"]:
        sys.exit(1)


def run_full_campaign(stack: Stack, reps: int = 3):
    runs_path = LOG_DIR / "runs.jsonl"
    attempts_path = LOG_DIR / "attempts.jsonl"
    env_path = LOG_DIR / "env.json"
    for p in (runs_path, attempts_path):
        if p.exists():
            p.unlink()

    env_before = read_clocks_edac()
    ea0_start = env_before["card_edac"].get("ea0_ue", -1)
    print(
        f"=== MRV-209 FULL E5 CAMPAIGN (reps={reps}) cpu40={env_before['saturn_cpu40_khz']} kHz "
        f"cpu41={env_before['saturn_cpu41_khz']} kHz uncore={env_before['saturn_uncore_khz']} "
        f"card_ea0_ue={ea0_start} ===",
        flush=True,
    )

    # Solo 32 MiB co-located tenant calibration (zero network traffic baseline; includes 3.5 s clock ramp)
    numactl_bin = shutil.which("numactl")
    if not numactl_bin:
        raise RuntimeError("numactl not found in PATH; run inside `devenv shell` to enforce --membind=1")
    solo_out = sh([
        numactl_bin, "--membind=1",
        HOST_BIN, "--tenant-solo-ms", "2000",
        "--tenant-cpu", str(TENANT_CPU),
        "--tenant-ws-mb", str(TENANT_WS_MB),
        "--pmu", "true",
    ]).stdout.strip()
    solo_tenant = json.loads(solo_out.splitlines()[-1])
    print(
        f"Solo 32 MiB tenant baseline: {solo_tenant['tenant']} "
        f"(ghz={solo_tenant['pmu']['tenant_pmu']['ghz']:.3f}, pf={solo_tenant['pmu']['tenant_pmu']['page_faults']})",
        flush=True,
    )

    plan = get_campaign_plan()
    for rep in range(1, reps + 1):
        print(f"=== REP {rep}/{reps} (interleaved across all 4 policies) ===", flush=True)
        for sec, cases in plan.items():
            for pol in POLICIES:
                for idx, (wl, dm, rk, bp, bd, br, ipkts, nf) in enumerate(cases, 1):
                    rid = f"{sec}_{pol}_{wl}_{dm}_r{int(rk)}k_f{nf}_i{ipkts}"
                    run_verified_repeat(
                        stack, runs_path, attempts_path, sec, rid, rep,
                        pol, wl, dm, rk, bp, bd, br, ipkts, nf,
                        warmup_ms=400, duration_ms=3000,
                        seed_base=209000 + idx * 100,
                    )
        edac_mid = read_clocks_edac()
        print(
            f"[Post-Rep-{rep} EDAC] card_ea0_ue={edac_mid['card_edac'].get('ea0_ue')} "
            f"(delta={edac_mid['card_edac'].get('ea0_ue', -1) - ea0_start})",
            flush=True,
        )

    upstream_head = sh(["git", "-C", "/tmp/mrv206-upstream/memcached", "rev-parse", "HEAD"]).stdout.strip()
    upstream_diff = sh(["git", "-C", "/tmp/mrv206-upstream/memcached", "diff", "--stat"]).stdout.strip()
    restore_res = stack.restore_baseline(clean_mercury_tmp=True)
    time.sleep(21.0)  # Delayed EDAC logging audit (AGENTS.md)
    env_after = read_clocks_edac()
    deltas = {
        "card_ea0_ue_delta": env_after["card_edac"].get("ea0_ue", -1) - env_before["card_edac"].get("ea0_ue", -1),
        "card_mc0_ue_delta": env_after["card_edac"].get("mc0_ue", -1) - env_before["card_edac"].get("mc0_ue", -1),
        "saturn_edac_ue_delta": env_after["saturn_edac_ue_sum"] - env_before["saturn_edac_ue_sum"],
        "saturn_rp_aer_delta": {
            k: env_after["saturn_rp_aer"].get(k, -1) - env_before["saturn_rp_aer"].get(k, -1)
            for k in env_after["saturn_rp_aer"]
        },
    }
    env_path.write_text(json.dumps({
        "upstream_memcached_repo": "https://github.com/memcached/memcached.git",
        "upstream_memcached_head": upstream_head,
        "upstream_memcached_diff": upstream_diff,
        "solo_tenant": solo_tenant,
        "before": env_before,
        "after": env_after,
        "deltas": deltas,
        "restore": restore_res,
    }, indent=2))
    print(
        f"MRV-209 campaign complete! env={env_path} runs={runs_path} deltas={deltas}",
        flush=True,
    )


def main():
    ap = argparse.ArgumentParser(description="MRV-209 (MRV-203 E5) Experiment Harness")
    ap.add_argument("--dry-run", action="store_true", help="Offline self-check (zero hardware access)")
    ap.add_argument("--deploy", action="store_true", help="Deploy card binary and compile mercury client")
    ap.add_argument("--smoke", action="store_true", help="Run end-to-end smoke across all 4 policies")
    ap.add_argument("--full", action="store_true", help="Run full interleaved n=3 E5 campaign")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--stop-all", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        run_dry_run()
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stack = Stack()
    try:
        if args.deploy:
            deploy_binaries()
            return
        if args.restore:
            print(json.dumps(stack.restore_baseline(), indent=2))
            return
        if args.stop_all:
            stack.stop_all_procs()
            print("Stopped all mrv209 host/card/mercury processes cleanly.")
            return
        if args.smoke:
            run_smoke(stack)
            stack.stop_all_procs()
            return
        if args.full:
            run_full_campaign(stack, reps=args.reps)
            return
        ap.print_help()
    except BaseException:
        stack.stop_all_procs()
        raise


if __name__ == "__main__":
    main()
