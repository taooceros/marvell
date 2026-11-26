#!/usr/bin/env python3
"""MRV-206 Dual-Host 100GbE Real Upstream Memcached 1.6.45 Experiment Harness.

Runs 100% unmodified upstream Memcached (`github.com/memcached/memcached`,
commit `2d51e364799bc9698bd4b11728ea978cea12da6e`, 0 lines modified in upstream core)
on 1 host core (`saturn cpu40 @ 3.90 GHz`, NUMA-1 membind, 256 MiB preallocated slabs,
Murmur3 hash, segmented LRU, full binary UDP protocol parser) across:
  - `sdp_fair`    : Fair async optimal `SDP fair-full` (`rx_poll_mem=1,rx_poll_tail_off=48,
                    ism_enable=1,allow_small_oq=1`, 4Q x 256 DDIO-fit RX desc, compact RX
                    pool, in-place zero-copy resize, batched TX) + CN106 `--rx-mode cqe-peek`
  - `sdp_ism512`  : MRV-205 control (`rx_poll_mem=0,ism_enable=1,allow_small_oq=1`, 4Q x 512)
  - `sdp_1q`      : 1-queue SDP control (`rx_poll_mem=1,rx_poll_tail_off=48`, 1Q x 1024)
  - `direct_peek` : DirectPush (`<= 192 B` Memcached UDP via 1..4 x 64 B PEM0 WC / BAR4
                    MOVDIR64B + `> 192 B` native DPI DMA) + CN106 `--rx-mode cqe-peek`
  - `direct_safe` : DirectPush + CN106 zero-LDADDA `--rx-mode cqe-direct-safe`
                    (`watchdog_interval=4096`, full 128 B clear, err-drop hardened)
"""

import argparse
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

LOG_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv206/logs/mrv206-2026-11-26")
HOST_BIN = "/home/hongtao/dev/marvell-mrv206/testbed/target/release/mrv206_host"
CARD_BIN = "/root/bench/mrv206_card"
MERC_BIN = "/tmp/mrv206/mrv206_client"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mercury"]
ETH_DEV = "0002:02:00.0,scalar_enable=1"
CARD_SYNC_PORT = 7539
HOST_CTRL_PORT = 7541


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sh(cmd, check=True, timeout=120):
    return subprocess.run(cmd, text=True, capture_output=True, check=check, timeout=timeout)


def ssh_card(script, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def tcp_cmd(host: str, port: int, cmd: str, timeout: float = 15.0):
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


def read_merc_ethtool() -> dict:
    out = sh(
        MERC_SSH
        + [
            "ethtool -S ens6f1np1 | grep -E 'rx_out_of_buffer|rx_discards_phy|rx_crc_errors_phy|rx_err_lane' || true"
        ],
        check=False,
    ).stdout
    res = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            try:
                res[k.strip()] = int(v.strip())
            except ValueError:
                pass
    return res


def read_clocks_edac():
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    khz = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("package_*")):
        uncore[p.name] = int((p / "current_freq_khz").read_text().strip())
    host_ue = []
    for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count")):
        host_ue.append(int(p.read_text().strip()))
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
        "sei_total=$(dmesg | grep -c 'EDAC DEVICE4' || echo 0)"
    ).stdout.strip()
    card_edac = {}
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


def profile_for_workload(wl: str) -> str:
    if wl in ("get8", "set8", "rw8", "8"):
        return "8"
    if wl in ("get128", "set128", "rw128", "128"):
        return "128"
    if wl in ("get1024", "set1024", "1024"):
        return "1024"
    if wl in ("get3840", "set3840", "3840"):
        return "3840"
    if wl in ("small_mix", "etc_small"):
        return "small_mix"
    return "etc_mix"


class Stack:
    def __init__(self):
        self.card_key = None
        self.host_key = None
        self.host_proc = None

    def stop_host(self):
        if self.host_proc is not None:
            if self.card_key is not None and self.card_key[0] == "direct":
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
        sh(["rm", "-f", "/dev/hugepages/m206h*"], check=False)
        self.host_key = None

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m206 C-c 2>/dev/null || true; sleep 0.6; "
            "tmux kill-session -t m206 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/m206*"
        )
        self.card_key = None

    def stop_all_procs(self):
        self.stop_card()
        sh(MERC_SSH + ["pkill -f mrv206_client 2>/dev/null || true"], check=False)

    def ensure_stack(
        self,
        policy: str,
        app_work: bool = False,
        app_timing: bool = False,
        tx_pool_slots: int = 1024,
    ):
        no_batch = False
        if policy == "card_echo":
            c_key = ("card-echo", "cqe-peek", 4)
            h_key = None
        elif policy == "sdp_fair":
            c_key = ("sdp", "cqe-peek", 4)
            h_key = ("sdp", 4, 1, 48, 512, app_work, app_timing, tx_pool_slots, False)
        elif policy == "sdp_fair256":
            c_key = ("sdp", "cqe-peek", 4)
            h_key = ("sdp", 4, 1, 48, 256, app_work, app_timing, tx_pool_slots, False)
        elif policy == "sdp_ism512":
            c_key = ("sdp", "cqe-peek", 4)
            h_key = ("sdp", 4, 0, 0, 512, app_work, app_timing, tx_pool_slots, False)
        elif policy == "sdp_ism256":
            c_key = ("sdp", "cqe-peek", 4)
            h_key = ("sdp", 4, 0, 0, 256, app_work, app_timing, tx_pool_slots, False)
        elif policy == "sdp_1q":
            c_key = ("sdp", "cqe-peek", 1)
            h_key = ("sdp", 1, 1, 48, 1024, app_work, app_timing, tx_pool_slots, False)
        elif policy == "direct_peek":
            c_key = ("direct", "cqe-peek", 4)
            h_key = ("direct", 4, 0, 0, 0, app_work, app_timing, tx_pool_slots, False)
        elif policy == "direct_peek_nobatch":
            c_key = ("direct", "cqe-peek", 4)
            no_batch = True
            h_key = ("direct", 4, 0, 0, 0, app_work, app_timing, tx_pool_slots, True)
        elif policy == "direct_safe":
            c_key = ("direct", "cqe-direct-safe", 4)
            h_key = ("direct", 4, 0, 0, 0, app_work, app_timing, tx_pool_slots, False)
        else:
            raise ValueError(f"Unknown policy: {policy}")
        _ = no_batch

        if self.card_key != c_key:
            self.stop_card()
            ssh_card(
                "tmux send-keys -t refl C-c 2>/dev/null || true; sleep 0.3; "
                "tmux kill-session -t refl 2>/dev/null || true; rm -f /dev/hugepages/refl*; "
                "ip link set eth0 down 2>/dev/null || true; "
                "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
                'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
                "drv2=$(basename $(readlink -f /sys/bus/pci/devices/0002:01:00.2/driver) 2>/dev/null || echo none); "
                'if [ "$drv2" != "vfio-pci" ]; then sh /root/bench/dpdk_up.sh 0002:01:00.2; fi; '
                "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko"
            )
            cmode, rxmode, ncores = c_key
            lcores = "3-6" if ncores == 4 else "3"
            if cmode == "card-echo":
                ready_tok = "card_echo ready"
                cmd = (
                    f"{CARD_BIN} -l {lcores} -a {ETH_DEV} --file-prefix=m206 -- "
                    f"--mode card-echo --rx-mode {rxmode}"
                )
            elif cmode == "sdp":
                ready_tok = "sdp ready"
                rss_flag = " --no-sdp-rss" if ncores == 1 else ""
                cmd = (
                    f"{CARD_BIN} -l {lcores} -a {ETH_DEV} -a 0002:01:00.2 --file-prefix=m206 -- "
                    f"--mode sdp --rx-mode {rxmode}{rss_flag}"
                )
            else:
                ready_tok = "direct ready"
                cmd = (
                    f"{CARD_BIN} -l {lcores} -a {ETH_DEV} "
                    f"-a 0000:06:00.1 -a 0000:06:00.2 -a 0000:06:00.3 -a 0000:06:00.4 "
                    f"--file-prefix=m206 -- --mode direct --rx-mode {rxmode}"
                )
            ssh_card(
                f'rm -f /tmp/m206_card.log; '
                f'tmux new-session -d -s m206 "{cmd} > /tmp/m206_card.log 2>&1"; '
                f'for i in $(seq 1 80); do grep -q "{ready_tok}" /tmp/m206_card.log 2>/dev/null && break; sleep 0.15; done; '
                f'grep "{ready_tok}" /tmp/m206_card.log'
            )
            self.card_key = c_key

        if h_key is None:
            self.stop_host()
            return

        if self.host_key != h_key or self.host_proc is None or self.host_proc.poll() is not None:
            self.stop_host()
            hmode, nq, rpm, tail_off, rx_desc, aw, atim, tps, nbm = h_key
            if hmode == "sdp":
                if rpm == 1:
                    devarg = (
                        f"0000:99:02.0,rx_poll_mem=1,rx_poll_tail_off={tail_off},"
                        f"ism_enable=1,allow_small_oq=1,max_rings={nq}"
                    )
                else:
                    devarg = f"0000:99:02.0,rx_poll_mem=0,ism_enable=1,allow_small_oq=1,max_rings={nq}"
                extra = [
                    "--mode", "sdp",
                    "--sdp-queues", str(nq),
                    "--sdp-rx-desc", str(rx_desc),
                    "--sdp-tx-desc", "2048",
                    "--sdp-compact-pool", "true",
                    "--sdp-zero-copy", "true",
                ]
            else:
                devarg = "0000:99:02.0"
                extra = [
                    "--mode", "direct",
                    "--tx-pool-slots", str(tps),
                ]
            if aw:
                extra.append("--app-work")
            if atim:
                extra.append("--app-timing")
            if nbm:
                extra.append("--no-batch-mmio")
            prefix = ["numactl", "--membind=1"] if shutil.which("numactl") else []
            cmd = prefix + [
                HOST_BIN, "-l", "40", "-a", devarg,
                "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m206h", "--",
                "--mc-profile", "etc_mix",
                "--pmu", "true",
            ] + extra
            log_path = pathlib.Path("/tmp/m206_host_run.log")
            with open(log_path, "w") as logf:
                self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
            for _ in range(100):
                if self.host_proc.poll() is not None:
                    raise RuntimeError(f"mrv206_host exited early:\n{log_path.read_text()}")
                txt = log_path.read_text()
                if "MRV206_HOST READY" in txt:
                    time.sleep(0.1)
                    self.host_key = h_key
                    return
                time.sleep(0.1)
            raise RuntimeError(f"mrv206_host ready timeout:\n{log_path.read_text()}")

    def restore_baseline(self) -> dict:
        self.stop_all_procs()
        out = ssh_card(
            "dpdk-devbind.py -b rvu_nicpf 0002:02:00.0; "
            "sleep 1.5; "
            "ip addr flush dev eth0 2>/dev/null || true; "
            "ip addr add 10.10.100.2/24 dev eth0; "
            "ip link set eth0 up; "
            "sleep 2.0; "
            "lsmod | grep -q '^pem_wc ' && rmmod pem_wc || true; "
            "tmux kill-session -t refl 2>/dev/null || true; "
            "rm -f /dev/hugepages/refl*; "
            "tmux new-session -d -s refl /root/bench/reflector-rs -l 3 -a 0002:01:00.2 --file-prefix=refl; "
            "sleep 1.0; "
            "ethtool eth0 | grep -E 'Speed|Link detected'; "
            "ping -c 2 -W 1 10.10.100.1 | tail -n 2; "
            "tmux ls; "
            "lsmod | grep pem_wc || echo 'pem_wc unloaded'"
        ).stdout.strip()
        merc_chk = sh(MERC_SSH + ["pgrep -af mrv206_client || echo 'mercury clean'"], check=False).stdout.strip()
        return {"restore_stdout": out, "mercury_check": merc_chk}

def run_client_synced(
    policy: str,
    workload: str,
    key_dist: str,
    drive_mode: str,
    window: int,
    rate_krps: float,
    threads: int,
    warmup_ms: int,
    duration_ms: int,
    seed: int,
    set_pct: int = 5,
    timeout: int = 60,
    card_queues: int = 4,
):
    prof = profile_for_workload(workload)
    if policy != "card_echo":
        tcp_cmd("127.0.0.1", HOST_CTRL_PORT, f"RESET {prof}")
    tcp_cmd("10.10.0.2", CARD_SYNC_PORT, "RESET_PEERS")
    merc_eth_before = read_merc_ethtool()

    mcmd = (
        f"{MERC_BIN} --dev mlx5_2 --sgid 3 --cpu 4 --threads {threads} --card-queues {card_queues} "
        f"--mode {drive_mode} --workload {workload} --key-dist {key_dist} --set-pct {set_pct} "
        f"--window {window} --rate-krps {rate_krps:.3f} "
        f"--warmup-ms {warmup_ms} --duration-ms {duration_ms} --seed {seed} --sync-reset"
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
        while time.time() - t0 < 30.0:
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
        if policy != "card_echo":
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
        host_stats = tcp_cmd("127.0.0.1", HOST_CTRL_PORT, "STATS") if policy != "card_echo" else None

        rest_out, err = p.communicate(timeout=timeout)
        lines.append(rest_out or "")
        full_out = "".join(lines)

        client_json = None
        for l in full_out.splitlines():
            if l.startswith("MRV206_CLIENT_JSON "):
                client_json = json.loads(l[len("MRV206_CLIENT_JSON "):])
                break
        merc_eth_after = read_merc_ethtool()
        merc_eth_delta = {
            k: merc_eth_after.get(k, 0) - merc_eth_before.get(k, 0)
            for k in set(merc_eth_before) | set(merc_eth_after)
        }
        if client_json is None:
            raise RuntimeError(f"Missing MRV206_CLIENT_JSON (rc={p.returncode}):\n{full_out}\n{err}")
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
    if (
        not c.get("verified")
        or c["lost"] != 0
        or c["bad"] != 0
        or c.get("proto_err", 0) != 0
        or c.get("val_err", 0) != 0
        or c["torn"] != 0
        or c["send_err"] != 0
    ):
        errs.append(
            f"client lost={c['lost']} bad={c['bad']} proto={c.get('proto_err')} val={c.get('val_err')} torn={c['torn']} serr={c['send_err']}"
        )
    if drive_mode == "poisson" and c.get("window_cap_hits", 0) != 0:
        errs.append(f"poisson window_cap_hits={c.get('window_cap_hits')}")
    eth_d = c.get("merc_ethtool_delta") or {}
    for ek in ("rx_out_of_buffer", "rx_discards_phy", "rx_crc_errors_phy"):
        if eth_d.get(ek, 0) != 0:
            errs.append(f"merc_{ek}={eth_d.get(ek)}")
    if k["bad_wire"] != 0 or k["bad_host"] != 0 or k["torn_bar4"] != 0 or k["dma_failed"] != 0:
        errs.append(
            f"card bad_wire={k['bad_wire']} bad_host={k['bad_host']} torn_bar4={k['torn_bar4']} dma_failed={k['dma_failed']}"
        )
    if k["eth_imissed"] != 0 or k["eth_ierrors"] != 0 or k["eth_oerrors"] != 0 or k["eth_tx_drops"] != 0:
        errs.append(
            f"eth imissed={k['eth_imissed']} ierr={k['eth_ierrors']} oerr={k['eth_oerrors']} tx_drop={k['eth_tx_drops']}"
        )
    if h is not None:
        if (
            h["bad"] != 0
            or h["torn"] != 0
            or h["mc_misses"] != 0
            or h["mc_errors"] != 0
            or h["tx_drops"] != 0
            or h.get("sdp_rx_nombuf", 0) != 0
            or h.get("sdp_imissed", 0) != 0
            or h.get("sdp_ierrors", 0) != 0
            or h.get("sdp_oerrors", 0) != 0
        ):
            errs.append(
                f"host bad={h['bad']} torn={h['torn']} mc_miss={h['mc_misses']} mc_err={h['mc_errors']} "
                f"tx_drop={h['tx_drops']} sdp_nombuf={h.get('sdp_rx_nombuf')} sdp_imiss={h.get('sdp_imissed')}"
            )
        pmu = h.get("pmu") or {}
        if h["page_faults"] != 0 or pmu.get("page_faults", 0) != 0:
            errs.append(f"host page_faults={h['page_faults']} pmu_pf={pmu.get('page_faults')}")
        ms = h.get("mc_stats") or {}
        if ms.get("evictions", 0) != 0 or ms.get("get_misses", 0) != 0:
            errs.append(f"mc evictions={ms.get('evictions')} get_misses={ms.get('get_misses')}")
        if pmu and pmu.get("min_running_pct", 100.0) < 99.5:
            errs.append(f"host pmu mux min_running_pct={pmu.get('min_running_pct')}")
    return errs

def run_smoke(stack: Stack):
    print("=== MRV-206 REAL UPSTREAM MEMCACHED 1.6.45 END-TO-END SMOKE ===")
    edac0 = read_clocks_edac()
    ea0_start = edac0["card_edac"].get("ea0_ue", -1)
    print(f"Pre-smoke clocks/EDAC: cpu40={edac0['saturn_cpu40_khz']} kHz ({edac0['saturn_cpu40_gov']}) card_ea0_ue={ea0_start}")

    cases = [
        # 1. DirectPush cqe-peek across all value classes + mixed Zipf + Poisson + W=4
        ("direct_peek",         "get8",      "zipf",    "closed",   64,    0.0, 4, False),
        ("direct_peek",         "get8",      "zipf",    "closed",    4,    0.0, 4, False),
        ("direct_peek",         "set8",      "zipf",    "closed",   64,    0.0, 4, False),
        ("direct_peek",         "get128",    "zipf",    "closed",   64,    0.0, 4, False),
        ("direct_peek",         "set128",    "zipf",    "closed",   64,    0.0, 4, False),
        ("direct_peek",         "get1024",   "zipf",    "closed",   64,    0.0, 4, False),
        ("direct_peek",         "get3840",   "zipf",    "closed",   64,    0.0, 4, False),
        ("direct_peek",         "set3840",   "zipf",    "closed",   64,    0.0, 4, False),
        ("direct_peek",         "etc_mix",   "zipf",    "closed",   64,    0.0, 4, False),
        ("direct_peek",         "etc_mix",   "uniform", "closed",   64,    0.0, 4, False),
        ("direct_peek",         "etc_mix",   "zipf",    "poisson", 256, 1500.0, 4, False),
        ("direct_peek",         "get8",      "zipf",    "closed",   64,    0.0, 4, True),
        ("direct_peek_nobatch", "get8",      "zipf",    "closed",   64,    0.0, 4, True),
        # 2. DirectPush cqe-direct-safe (zero-LDADDA)
        ("direct_safe",         "get8",      "zipf",    "closed",   64,    0.0, 4, False),
        ("direct_safe",         "etc_mix",   "zipf",    "closed",   64,    0.0, 4, False),
        ("direct_safe",         "etc_mix",   "zipf",    "poisson", 256, 1500.0, 4, False),
        # 3. SDP fair-full (rx_poll_mem=1, tail=48, rxd=512, pool=5119)
        ("sdp_fair",            "get8",      "zipf",    "closed",   64,    0.0, 4, False),
        ("sdp_fair",            "get8",      "zipf",    "closed",    4,    0.0, 4, False),
        ("sdp_fair",            "set8",      "zipf",    "closed",   64,    0.0, 4, False),
        ("sdp_fair",            "get128",    "zipf",    "closed",   64,    0.0, 4, False),
        ("sdp_fair",            "set128",    "zipf",    "closed",   64,    0.0, 4, False),
        ("sdp_fair",            "get1024",   "zipf",    "closed",   64,    0.0, 4, False),
        ("sdp_fair",            "get3840",   "zipf",    "closed",   64,    0.0, 4, False),
        ("sdp_fair",            "set3840",   "zipf",    "closed",   64,    0.0, 4, False),
        ("sdp_fair",            "etc_mix",   "zipf",    "closed",   64,    0.0, 4, False),
        ("sdp_fair",            "etc_mix",   "uniform", "closed",   64,    0.0, 4, False),
        ("sdp_fair",            "etc_mix",   "zipf",    "poisson", 256, 1500.0, 4, False),
        ("sdp_fair",            "etc_mix",   "zipf",    "poisson", 256, 1800.0, 4, False),
        ("sdp_fair",            "get8",      "zipf",    "closed",   64,    0.0, 4, True),
        # 4. MRV-205 / OQ ring controls
        ("sdp_fair256",         "get8",      "zipf",    "closed",   64,    0.0, 4, False),
        ("sdp_ism512",          "get8",      "zipf",    "closed",   64,    0.0, 4, False),
        ("sdp_ism512",          "etc_mix",   "zipf",    "closed",   64,    0.0, 4, False),
    ]

    smoke_out = LOG_DIR / "smoke.jsonl"
    with open(smoke_out, "w") as sf:
        all_ok = True
        for idx, (pol, wl, kd, dm, win, rk, thr, atim) in enumerate(cases, 1):
            stack.ensure_stack(pol, app_work=False, app_timing=atim)
            c, h, k, tad = run_client_synced(
                pol, wl, kd, dm, win, rk, thr, warmup_ms=300, duration_ms=1000, seed=20600 + idx
            )
            errs = verify_run(pol, dm, c, h, k)
            pmu = (h or {}).get("pmu") or {}
            ms = (h or {}).get("mc_stats") or {}
            cyc = pmu.get("cycles_per_req", 0.0)
            imc = pmu.get("imc_bytes_per_req", 0.0)
            ddio = pmu.get("ddio_hit_pct", 0.0)
            mc_ns = (h or {}).get("mc_exec_ns_per_req", 0.0)
            busy_ns = (h or {}).get("busy_ns_per_req", 0.0)
            status = "OK" if not errs else f"FAIL({'; '.join(errs)})"
            if errs:
                all_ok = False
            tag = "+atim" if atim else ""
            print(
                f"[{idx:02d}/{len(cases)}] {pol+tag:17s} {wl:8s} {kd:7s} {dm:7s} W={win:3d} r={rk:6.0f} | "
                f"{c['achieved_mrps']:5.2f} Mrps p50={c['tot_p50_us']:6.2f}us p99={c['tot_p99_us']:6.2f}us "
                f"sm99={c['small_p99_us']:6.2f}us | cyc={cyc:6.0f} busy={busy_ns:5.0f}ns mc={mc_ns:5.0f}ns "
                f"imc={imc:5.1f}B ddio={ddio:5.1f}% hits={ms.get('get_hits',0)} sets={ms.get('set_cmds',0)} | {status}",
                flush=True,
            )
            sf.write(json.dumps({
                "ts": utc_now(), "policy": pol, "workload": wl, "key_dist": kd, "drive_mode": dm,
                "window": win, "rate_krps": rk, "threads": thr, "app_timing": atim,
                "errors": errs, "client": c, "host": h, "card": k, "tad_delta": tad,
            }) + "\n")
            sf.flush()

    edac1 = read_clocks_edac()
    ea0_end = edac1["card_edac"].get("ea0_ue", -1)
    print(f"Post-smoke EDAC: card_ea0_ue {ea0_start} -> {ea0_end} (delta={ea0_end - ea0_start}), saturn_ue_sum={edac1['saturn_edac_ue_sum']}")
    if not all_ok or (ea0_end != ea0_start):
        sys.exit(1)


def append_jsonl(path: pathlib.Path, rec: dict):
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def run_verified_repeat(
    stack: Stack,
    runs_path: pathlib.Path,
    attempts_path: pathlib.Path,
    section: str,
    run_id: str,
    rep: int,
    policy: str,
    workload: str,
    key_dist: str,
    drive_mode: str,
    window: int,
    rate_krps: float,
    threads: int = 4,
    warmup_ms: int = 400,
    duration_ms: int = 3000,
    set_pct: int = 5,
    app_work: bool = False,
    app_timing: bool = False,
    tx_pool_slots: int = 1024,
    max_attempts: int = 4,
    seed_base: int = 206000,
):
    cq = 1 if policy == "sdp_1q" else 4
    for att in range(1, max_attempts + 1):
        stack.ensure_stack(policy, app_work=app_work, app_timing=app_timing, tx_pool_slots=tx_pool_slots)
        seed = seed_base + rep * 997 + att * 31
        c, h, k, tad = run_client_synced(
            policy, workload, key_dist, drive_mode, window, rate_krps, threads,
            warmup_ms=warmup_ms, duration_ms=duration_ms, seed=seed,
            set_pct=set_pct, card_queues=cq,
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
            "key_dist": key_dist,
            "drive_mode": drive_mode,
            "window": window,
            "rate_krps": rate_krps,
            "threads": threads,
            "set_pct": set_pct,
            "app_work": app_work,
            "app_timing": app_timing,
            "tx_pool_slots": tx_pool_slots,
            "warmup_ms": warmup_ms,
            "duration_ms": duration_ms,
            "client": c,
            "host": h,
            "card": k,
            "tad_delta": tad,
        }
        append_jsonl(attempts_path, rec)
        pmu = (h or {}).get("pmu") or {}
        print(
            f"  [{section}] {run_id} r={rep} a={att} | {c['achieved_mrps']:5.2f} Mrps "
            f"p50={c['tot_p50_us']:6.2f}us p99={c['tot_p99_us']:6.2f}us sm99={c['small_p99_us']:6.2f}us "
            f"cyc={pmu.get('cycles_per_req',0):6.0f} imc={pmu.get('imc_bytes_per_req',0):5.1f}B "
            f"| {'OK' if not errs else 'RETRY:' + ';'.join(errs)}",
            flush=True,
        )
        if not errs:
            append_jsonl(runs_path, rec)
            return rec
        eth_d = c.get("merc_ethtool_delta") or {}
        if drive_mode == "poisson" and eth_d.get("rx_crc_errors_phy", 0) == 0 and (c.get("window_cap_hits", 0) > 1000 or c.get("lost", 0) > 50):
            raise RuntimeError(f"Open-loop knee saturated for {run_id} rep={rep}: {'; '.join(errs)}")
        time.sleep(0.3)
    raise RuntimeError(f"Exhausted {max_attempts} attempts for {run_id} rep={rep}")


def run_full_campaign(stack: Stack, reps: int = 3):
    """Full interleaved n=3 campaign comparing SDP fair-full vs DirectPush (peek & safe)
    on 100% unmodified upstream Memcached 1.6.45."""
    runs_path = LOG_DIR / "runs.jsonl"
    attempts_path = LOG_DIR / "attempts.jsonl"
    env_path = LOG_DIR / "env.json"
    for p in (runs_path, attempts_path):
        if p.exists():
            p.unlink()

    env_before = read_clocks_edac()
    ea0_start = env_before["card_edac"].get("ea0_ue", -1)
    print(
        f"=== MRV-206 FULL CAMPAIGN (reps={reps}) pre-EDAC: cpu40={env_before['saturn_cpu40_khz']} kHz "
        f"uncore={env_before['saturn_uncore_khz']} card_ea0_ue={ea0_start} ===",
        flush=True,
    )

    # Idle calibration of background app_work unit on cpu40
    cal_out = sh([HOST_BIN, "--idle-cal-ms", "1500"]).stdout.strip()
    idle_cal = json.loads(cal_out.splitlines()[-1])
    print(f"Idle app_work calibration: {idle_cal}", flush=True)

    policies_3 = ["sdp_fair", "direct_peek", "direct_safe"]

    # Section 1: Closed-loop Saturation Matrix (W=64) + Low-Concurrency RTT (W=4)
    sat_workloads = [
        ("get8",      "zipf",    64, 3000),
        ("get8",      "zipf",     4, 2000),
        ("set8",      "zipf",    64, 3000),
        ("get128",    "zipf",    64, 3000),
        ("set128",    "zipf",    64, 3000),
        ("get1024",   "zipf",    64, 3000),
        ("get3840",   "zipf",    64, 3000),
        ("set3840",   "zipf",    64, 3000),
        ("small_mix", "zipf",    64, 3000),
        ("etc_mix",   "zipf",    64, 3000),
        ("etc_mix",   "zipf",     4, 2000),
        ("etc_mix",   "uniform", 64, 3000),
    ]
    for rep in range(1, reps + 1):
        print(f"=== SECTION 1: SATURATION & W=4 RTT (rep {rep}/{reps}) ===", flush=True)
        for pol in policies_3:
            for wl, kd, win, dur in sat_workloads:
                sec = "loww" if win == 4 else "sat"
                rid = f"{sec}_{pol}_{wl}_{kd}_w{win}"
                run_verified_repeat(
                    stack, runs_path, attempts_path, sec, rid, rep,
                    pol, wl, kd, "closed", window=win, rate_krps=0.0, threads=4,
                    warmup_ms=400, duration_ms=dur,
                )

    edac_s1 = read_clocks_edac()
    print(f"[Post-Section-1 EDAC] card_ea0_ue={edac_s1['card_edac'].get('ea0_ue')} (delta={edac_s1['card_edac'].get('ea0_ue', -1) - ea0_start})", flush=True)

    # Section 2: Open-loop Poisson Latency-Throughput Curves (p50/p99/small_p99 knees)
    poisson_curves = [
        ("get8",    "zipf", [200.0, 500.0, 1000.0, 1500.0, 2000.0, 2500.0, 2900.0, 3300.0, 3600.0]),
        ("etc_mix", "zipf", [200.0, 500.0, 1000.0, 1400.0, 1800.0, 2100.0, 2400.0, 2700.0, 3000.0]),
        ("get128",  "zipf", [200.0, 500.0, 1000.0, 1500.0, 2000.0, 2500.0, 3000.0]),
    ]
    for rep in range(1, reps + 1):
        print(f"=== SECTION 2: OPEN-LOOP POISSON (rep {rep}/{reps}) ===", flush=True)
        for pol in policies_3:
            for wl, kd, rates in poisson_curves:
                for rk in rates:
                    rid = f"pois_{pol}_{wl}_{kd}_{int(rk)}k"
                    try:
                        run_verified_repeat(
                            stack, runs_path, attempts_path, "poisson", rid, rep,
                            pol, wl, kd, "poisson", window=256, rate_krps=rk, threads=4,
                            warmup_ms=400, duration_ms=2500, max_attempts=4,
                        )
                    except RuntimeError as e:
                        print(f"  [knee-reached] {rid}: {e} -> stopping higher rates for {pol}/{wl}", flush=True)
                        break

    edac_s2 = read_clocks_edac()
    print(f"[Post-Section-2 EDAC] card_ea0_ue={edac_s2['card_edac'].get('ea0_ue')} (delta={edac_s2['card_edac'].get('ea0_ue', -1) - ea0_start})", flush=True)

    # Section 3: Fixed-Rate Host Spare-CPU (`--app-work`) & Memcached vs Transport Breakdown (`--app-timing`)
    spare_cases = [
        ("get8",    "zipf", 1500.0),
        ("etc_mix", "zipf", 1200.0),
        ("get3840", "zipf",  600.0),
    ]
    for rep in range(1, reps + 1):
        print(f"=== SECTION 3: SPARE-CPU & APP BREAKDOWN (rep {rep}/{reps}) ===", flush=True)
        for pol in policies_3:
            for wl, kd, rk in spare_cases:
                rid = f"spare_{pol}_{wl}_{int(rk)}k"
                run_verified_repeat(
                    stack, runs_path, attempts_path, "spare_cpu", rid, rep,
                    pol, wl, kd, "poisson", window=256, rate_krps=rk, threads=4,
                    warmup_ms=400, duration_ms=2500, app_work=True,
                )
            for wl in ("get8", "get128", "etc_mix", "get3840"):
                rid = f"breakdown_{pol}_{wl}"
                run_verified_repeat(
                    stack, runs_path, attempts_path, "breakdown", rid, rep,
                    pol, wl, "zipf", "closed", window=64, rate_krps=0.0, threads=4,
                    warmup_ms=400, duration_ms=2000, app_timing=True,
                )

    # Section 4: Mechanism Ablations (MMIO-LOCK `--no-batch-mmio`) & MRV-205 SDP Controls (`sdp_ism512`, `sdp_fair256`)
    for rep in range(1, reps + 1):
        print(f"=== SECTION 4: ABLATIONS & CONTROLS (rep {rep}/{reps}) ===", flush=True)
        for wl, atim in [("get8", False), ("etc_mix", False), ("get8", True)]:
            tag = "_atim" if atim else ""
            rid = f"ablation_direct_peek_nobatch_{wl}{tag}"
            run_verified_repeat(
                stack, runs_path, attempts_path, "ablation", rid, rep,
                "direct_peek_nobatch", wl, "zipf", "closed", window=64, rate_krps=0.0, threads=4,
                warmup_ms=400, duration_ms=2500, app_timing=atim,
            )
        for pol in ("sdp_ism512", "sdp_fair256"):
            for wl, dm, win, rk in [
                ("get8",    "closed",   64,    0.0),
                ("etc_mix", "closed",   64,    0.0),
                ("etc_mix", "poisson", 256, 1500.0),
            ]:
                rid = f"ctrl_{pol}_{wl}_{dm}_{int(rk)}k"
                run_verified_repeat(
                    stack, runs_path, attempts_path, "sdp_ctrl", rid, rep,
                    pol, wl, "zipf", dm, window=win, rate_krps=rk, threads=4,
                    warmup_ms=400, duration_ms=2500,
                )
    upstream_head = sh(["git", "-C", "/tmp/mrv206-upstream/memcached", "rev-parse", "HEAD"]).stdout.strip()
    upstream_diff = sh(["git", "-C", "/tmp/mrv206-upstream/memcached", "diff", "--stat"]).stdout.strip()
    restore_res = stack.restore_baseline()
    time.sleep(21.0)  # EDAC delayed logging audit (AGENTS.md rule)
    env_after = read_clocks_edac()
    env_path.write_text(json.dumps({
        "upstream_memcached_repo": "https://github.com/memcached/memcached.git",
        "upstream_memcached_head": upstream_head,
        "upstream_memcached_diff": upstream_diff,
        "idle_cal": idle_cal,
        "before": env_before,
        "after": env_after,
        "restore": restore_res,
    }, indent=2))
    print(
        f"Campaign complete! env={env_path} runs={runs_path} "
        f"card_ea0_ue {ea0_start} -> {env_after['card_edac'].get('ea0_ue')} "
        f"saturn_ue_sum={env_after['saturn_edac_ue_sum']} aer={env_after['saturn_rp_aer']}",
        flush=True,
    )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--stop-all", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stack = Stack()
    try:
        if args.restore:
            print(json.dumps(stack.restore_baseline(), indent=2))
            return
        if args.stop_all:
            stack.stop_all_procs()
            print("Stopped all mrv206 host/card/mercury processes cleanly.")
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
