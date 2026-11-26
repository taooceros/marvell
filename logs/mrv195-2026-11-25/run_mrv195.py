#!/usr/bin/env python3
"""MRV-195 Dual-Host 100GbE Real Lightweight RPC/KV Experiment Harness.

Compares:
  - `sdp_fair`    : Fair async optimal SDP (`rx_poll_mem=1,rx_poll_tail_off=48,
                    ism_enable=1,allow_small_oq=1`, 4 queues x 256 DDIO-fit RX desc,
                    compact RX pool, zero-copy in-place resize, batched TX; 48 B
                    aligned Eth+IP+UDP header enables 4-core CN106 sdp1-1 RSS)
                    + CN106 `--rx-mode cqe-peek` (identical eth0 RX to DirectPush!)
  - `direct_peek` : DirectPush (`<= 256 B` L1-stack MOVDIR64B to 128 B-stride BAR4
                    sub-rings + `>= 1024 B` native DPI DMA) + CN106 `--rx-mode cqe-peek`
  - `direct_safe` : DirectPush + CN106 zero-LDADDA `--rx-mode cqe-direct-safe`
                    (watchdog_interval=4096, full 128 B clear, err-drop hardened)

Identical 1-host-core (`saturn cpu40 @ 3.90 GHz`, Socket 1 uncore `2.20 GHz`) real
open-addressing 32.5 MiB KV engine across all interfaces, driven by 4-core CN106
(`-l 3-6`, `eth0 4RxQ/4TxQ` RSS) and multi-thread `mercury` (`mlx5_2`, NUMA 0).
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

LOG_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv195/logs/mrv195-2026-11-25")
HOST_BIN = "/home/hongtao/dev/marvell-mrv195/testbed/target/release/mrv195_host"
CARD_BIN = "/root/bench/mrv195_card"
MERC_BIN = "/tmp/mrv195/mrv195_client"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mercury"]
ETH_DEV = "0002:02:00.0,scalar_enable=1"


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
        MERC_SSH + ["ethtool -S ens6f1np1 | grep -E 'rx_out_of_buffer|rx_discards_phy|rx_crc_errors_phy|rx_err_lane' || true"],
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
    if wl in ("get64", "put64", "rw64", "64"):
        return "64"
    if wl in ("get256", "put256", "rw256", "256"):
        return "256"
    if wl in ("get1024", "put1024", "1024"):
        return "1024"
    if wl in ("get4096", "put4096", "4096"):
        return "4096"
    return "mix"


class Stack:
    def __init__(self):
        self.card_key = None
        self.host_key = None
        self.host_proc = None

    def stop_host(self):
        if self.host_proc is not None:
            # Detach card pem_wc mappings BEFORE host exits and tears down VF0 IOMMU!
            if self.card_key is not None and self.card_key[0] == "direct":
                try:
                    tcp_cmd("10.10.0.2", 7529, "DETACH", timeout=3.0)
                except Exception:
                    pass
            try:
                tcp_cmd("127.0.0.1", 7531, "QUIT", timeout=3.0)
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
        sh(["rm", "-f", "/dev/hugepages/m195h*"], check=False)
        self.host_key = None

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m195 C-c 2>/dev/null || true; sleep 0.6; "
            "tmux kill-session -t m195 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/m195*"
        )
        self.card_key = None

    def ensure_stack(self, policy: str, app_work: bool = False, tx_pool_slots: int = 1024):
        """Policies: 'card_echo', 'sdp_fair', 'sdp_notail', 'sdp_1q', 'direct_peek', 'direct_safe'."""
        if policy == "card_echo":
            c_key = ("card-echo", "cqe-peek", 4)
            h_key = None
        elif policy == "sdp_fair":
            c_key = ("sdp", "cqe-peek", 4)
            h_key = ("sdp", 4, 48, app_work, tx_pool_slots)
        elif policy == "sdp_notail":
            c_key = ("sdp", "cqe-peek", 4)
            h_key = ("sdp", 4, 0, app_work, tx_pool_slots)
        elif policy == "sdp_1q":
            c_key = ("sdp", "cqe-peek", 1)
            h_key = ("sdp", 1, 48, app_work, tx_pool_slots)
        elif policy == "direct_peek":
            c_key = ("direct", "cqe-peek", 4)
            h_key = ("direct", 4, 0, app_work, tx_pool_slots)
        elif policy == "direct_safe":
            c_key = ("direct", "cqe-direct-safe", 4)
            h_key = ("direct", 4, 0, app_work, tx_pool_slots)
        else:
            raise ValueError(f"Unknown policy: {policy}")

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
                    f"{CARD_BIN} -l {lcores} -a {ETH_DEV} --file-prefix=m195 -- "
                    f"--mode card-echo --rx-mode {rxmode}"
                )
            elif cmode == "sdp":
                ready_tok = "sdp ready"
                rss_flag = " --no-sdp-rss" if ncores == 1 else ""
                cmd = (
                    f"{CARD_BIN} -l {lcores} -a {ETH_DEV} -a 0002:01:00.2 --file-prefix=m195 -- "
                    f"--mode sdp --rx-mode {rxmode}{rss_flag}"
                )
            else:
                ready_tok = "direct ready"
                cmd = (
                    f"{CARD_BIN} -l {lcores} -a {ETH_DEV} "
                    f"-a 0000:06:00.1 -a 0000:06:00.2 -a 0000:06:00.3 -a 0000:06:00.4 "
                    f"--file-prefix=m195 -- --mode direct --rx-mode {rxmode} --dma-threshold 1024"
                )
            ssh_card(
                f'rm -f /tmp/m195_card.log; '
                f'tmux new-session -d -s m195 "{cmd} > /tmp/m195_card.log 2>&1"; '
                f'for i in $(seq 1 80); do grep -q "{ready_tok}" /tmp/m195_card.log 2>/dev/null && break; sleep 0.15; done; '
                f'grep "{ready_tok}" /tmp/m195_card.log'
            )
            self.card_key = c_key

        if h_key is None:
            self.stop_host()
            return

        if self.host_key != h_key or self.host_proc is None or self.host_proc.poll() is not None:
            self.stop_host()
            hmode, nq, tail_off, aw, tps = h_key
            if hmode == "sdp":
                rx_desc = 256 if nq == 4 else 1024
                devarg = (
                    f"0000:99:02.0,rx_poll_mem=1,rx_poll_tail_off={tail_off},"
                    f"ism_enable=1,allow_small_oq=1,max_rings={nq}"
                )
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
                    "--dma-threshold", "1024",
                    "--tx-pool-slots", str(tps),
                ]
            if aw:
                extra.append("--app-work")
            cmd = [
                HOST_BIN, "-l", "40", "-a", devarg,
                "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m195h", "--",
                "--kv-profile", "mix",
                "--pmu", "true",
            ] + extra
            log_path = pathlib.Path("/tmp/m195_host_run.log")
            with open(log_path, "w") as logf:
                self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
            for _ in range(80):
                if self.host_proc.poll() is not None:
                    raise RuntimeError(f"mrv195_host exited early:\n{log_path.read_text()}")
                txt = log_path.read_text()
                if "MRV195_HOST READY" in txt:
                    time.sleep(0.1)
                    self.host_key = h_key
                    return
                time.sleep(0.1)
            raise RuntimeError(f"mrv195_host ready timeout:\n{log_path.read_text()}")

    def restore_baseline(self) -> dict:
        self.stop_card()
        sh(MERC_SSH + ["pkill -f mrv195_client 2>/dev/null || true"], check=False)
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
        return {"restore_stdout": out}


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
    timeout: int = 60,
    card_queues: int = 4,
):
    prof = profile_for_workload(workload)
    if policy != "card_echo":
        tcp_cmd("127.0.0.1", 7531, f"RESET {prof}")
    tcp_cmd("10.10.0.2", 7529, "RESET_PEERS")
    merc_eth_before = read_merc_ethtool()

    mcmd = (
        f"{MERC_BIN} --dev mlx5_2 --sgid 3 --cpu 4 --threads {threads} --card-queues {card_queues} "
        f"--mode {drive_mode} --workload {workload} --key-dist {key_dist} "
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

        # Exact synchronized PMU/stats reset right before measurement starts!
        tcp_cmd("10.10.0.2", 7529, "RESET")
        if policy != "card_echo":
            tcp_cmd("127.0.0.1", 7531, "RESET")
        tad_before = tcp_cmd("10.10.0.2", 7529, "PMU_SNAP")

        p.stdin.write("GO\n")
        p.stdin.flush()
        p.stdin.close()

        # Wait for MEAS_DONE so host/card PMU snapshot matches measurement window to <1 ms!
        t1 = time.time()
        while time.time() - t1 < timeout:
            line = p.stdout.readline()
            if not line:
                break
            lines.append(line)
            if "MEAS_DONE" in line:
                break

        card_stats = tcp_cmd("10.10.0.2", 7529, "STATS")
        tad_after = tcp_cmd("10.10.0.2", 7529, "PMU_SNAP")
        host_stats = tcp_cmd("127.0.0.1", 7531, "STATS") if policy != "card_echo" else None

        rest_out, err = p.communicate(timeout=timeout)
        lines.append(rest_out or "")
        full_out = "".join(lines)

        client_json = None
        for l in full_out.splitlines():
            if l.startswith("MRV195_CLIENT_JSON "):
                client_json = json.loads(l[len("MRV195_CLIENT_JSON "):])
                break
        merc_eth_after = read_merc_ethtool()
        merc_eth_delta = {
            k: merc_eth_after.get(k, 0) - merc_eth_before.get(k, 0)
            for k in set(merc_eth_before) | set(merc_eth_after)
        }
        if client_json is None:
            raise RuntimeError(f"Missing MRV195_CLIENT_JSON (rc={p.returncode}):\n{full_out}\n{err}")
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
    if not c.get("verified") or c["lost"] != 0 or c["bad"] != 0 or c["torn"] != 0 or c["send_err"] != 0:
        errs.append(f"client lost={c['lost']} bad={c['bad']} torn={c['torn']} send_err={c['send_err']}")
    if drive_mode == "poisson" and c.get("window_cap_hits", 0) != 0:
        errs.append(f"poisson window_cap_hits={c.get('window_cap_hits')}")
    eth_d = c.get("merc_ethtool_delta") or {}
    for ek in ("rx_out_of_buffer", "rx_discards_phy", "rx_crc_errors_phy"):
        if eth_d.get(ek, 0) != 0:
            errs.append(f"merc_{ek}={eth_d.get(ek)}")
    if k["bad_wire"] != 0 or k["bad_host"] != 0 or k["torn_bar4"] != 0 or k["dma_failed"] != 0:
        errs.append(f"card bad_wire={k['bad_wire']} bad_host={k['bad_host']} torn_bar4={k['torn_bar4']} dma_failed={k['dma_failed']}")
    if k["eth_imissed"] != 0 or k["eth_ierrors"] != 0 or k["eth_oerrors"] != 0 or k["eth_tx_drops"] != 0:
        errs.append(f"eth imissed={k['eth_imissed']} ierr={k['eth_ierrors']} oerr={k['eth_oerrors']} tx_drop={k['eth_tx_drops']}")
    if h is not None:
        if h["bad"] != 0 or h["torn"] != 0 or h["kv_misses"] != 0 or h["tx_drops"] != 0:
            errs.append(f"host bad={h['bad']} torn={h['torn']} miss={h['kv_misses']} tx_drop={h['tx_drops']}")
        if h["page_faults"] != 0:
            errs.append(f"host page_faults={h['page_faults']}")
        pmu = h.get("pmu") or {}
        if pmu and pmu.get("min_running_pct", 100.0) < 99.5:
            errs.append(f"host pmu mux min_running_pct={pmu.get('min_running_pct')}")
    return errs


def run_smoke(stack: Stack):
    print("=== MRV-195 END-TO-END SMOKE & SATURATION SCOUT ===")
    cases = [
        ("direct_peek", "get64", "zipf", "closed", 256, 0.0, 4),
        ("direct_peek", "get256", "zipf", "closed", 256, 0.0, 4),
        ("direct_peek", "get1024", "zipf", "closed", 256, 0.0, 4),
        ("direct_peek", "get4096", "zipf", "closed", 256, 0.0, 4),
        ("direct_peek", "put64", "zipf", "closed", 256, 0.0, 4),
        ("direct_peek", "put4096", "zipf", "closed", 256, 0.0, 4),
        ("direct_peek", "mix", "zipf", "closed", 256, 0.0, 4),
        ("direct_peek", "mix", "uniform", "closed", 256, 0.0, 4),
        ("direct_peek", "mix", "zipf", "poisson", 256, 1500.0, 4),
        ("direct_safe", "get64", "zipf", "closed", 256, 0.0, 4),
        ("direct_safe", "mix", "zipf", "closed", 256, 0.0, 4),
        ("direct_safe", "mix", "zipf", "poisson", 256, 1500.0, 4),
        ("sdp_fair", "get64", "zipf", "closed", 256, 0.0, 4),
        ("sdp_fair", "get256", "zipf", "closed", 256, 0.0, 4),
        ("sdp_fair", "get1024", "zipf", "closed", 256, 0.0, 4),
        ("sdp_fair", "get4096", "zipf", "closed", 256, 0.0, 4),
        ("sdp_fair", "put64", "zipf", "closed", 256, 0.0, 4),
        ("sdp_fair", "put4096", "zipf", "closed", 256, 0.0, 4),
        ("sdp_fair", "mix", "zipf", "closed", 256, 0.0, 4),
        ("sdp_fair", "mix", "uniform", "closed", 256, 0.0, 4),
        ("sdp_fair", "mix", "zipf", "poisson", 256, 1500.0, 4),
    ]
    all_ok = True
    for idx, (pol, wl, kd, dm, win, rk, thr) in enumerate(cases, 1):
        stack.ensure_stack(pol, app_work=False)
        c, h, k, _tad = run_client_synced(
            pol, wl, kd, dm, win, rk, thr, warmup_ms=300, duration_ms=1000, seed=19500 + idx
        )
        errs = verify_run(pol, dm, c, h, k)
        pmu = (h or {}).get("pmu") or {}
        cyc = pmu.get("cycles_per_req", 0.0)
        imc = pmu.get("imc_bytes_per_req", 0.0)
        ddio = pmu.get("ddio_hit_pct", 0.0)
        status = "OK" if not errs else f"FAIL({'; '.join(errs)})"
        if errs:
            all_ok = False
        print(
            f"[{idx:02d}/{len(cases)}] {pol:11s} {wl:7s} {kd:7s} {dm:7s} W={win:3d} R={rk:6.0f} -> "
            f"{c['achieved_mrps']:6.3f} Mrps | wire={c['wire_rx_gbps']:5.1f}/{c['wire_tx_gbps']:5.1f}G | "
            f"p50={c['tot_p50_us']:6.2f}us p99={c['tot_p99_us']:6.2f}us (sm99={c['small_p99_us']:6.2f}) | "
            f"cyc/req={cyc:6.0f} imcB/req={imc:6.1f} ddio={ddio:5.1f}% | {status}",
            flush=True,
        )
    if not all_ok:
        sys.exit(1)


def run_smoke_controls(stack: Stack):
    print("=== MRV-195 SDP CONTROLS SMOKE (sdp_notail + sdp_1q) ===", flush=True)
    cases = [
        ("sdp_notail", "get64", "zipf", "closed", 512, 0.0, 8, 4, False),
        ("sdp_notail", "get64", "zipf", "poisson", 8192, 8000.0, 8, 4, True),
        ("sdp_1q", "get64", "zipf", "closed", 512, 0.0, 8, 1, False),
        ("sdp_1q", "get64", "zipf", "closed", 1, 0.0, 1, 1, False),
        ("sdp_1q", "get4096", "zipf", "closed", 512, 0.0, 8, 1, False),
        ("sdp_1q", "mix", "zipf", "closed", 512, 0.0, 8, 1, False),
    ]
    all_ok = True
    for idx, (pol, wl, kd, dm, win, rk, thr, cq, aw) in enumerate(cases, 1):
        stack.ensure_stack(pol, app_work=aw)
        c, h, k, _tad = run_client_synced(
            pol, wl, kd, dm, win, rk, thr, warmup_ms=300, duration_ms=1000,
            seed=19550 + idx, card_queues=cq
        )
        errs = verify_run(pol, dm, c, h, k)
        pmu = (h or {}).get("pmu") or {}
        status = "OK" if not errs else f"FAIL({'; '.join(errs)})"
        if errs:
            all_ok = False
        print(
            f"[{idx:02d}/{len(cases)}] {pol:11s} {wl:7s} {dm:7s} W={win:4d} R={rk:5.0f} aw={int(aw)} -> "
            f"{c['achieved_mrps']:6.3f} Mrps ({c['wire_rx_gbps']:5.1f}G) "
            f"p50={c['tot_p50_us']:6.2f} p99={c['tot_p99_us']:6.2f} "
            f"cyc={pmu.get('cycles_per_req',0):5.0f} imc={pmu.get('imc_bytes_per_req',0):5.1f}B | {status}",
            flush=True,
        )
    if not all_ok:
        sys.exit(1)


def append_jsonl(path: pathlib.Path, obj: dict):
    with open(path, "a") as f:
        f.write(json.dumps(obj, separators=(",", ":")) + "\n")


def run_campaign(stack: Stack, do_restore: bool, resume_from_2b: bool = False, resume_from_3a: bool = False):
    runs_path = LOG_DIR / "runs.jsonl"
    att_path = LOG_DIR / "attempts.jsonl"
    env_path = LOG_DIR / "env.json"

    keep_parts = {"part0_idle_cal", "part1_sat", "part2a_w1"}
    if resume_from_3a:
        keep_parts |= {"part2b_mix_zipf", "part2b_mix_uniform", "part2b_get64_zipf"}
    env_before = read_clocks_edac()
    ea0_start = env_before["card_edac"]["ea0_ue"]
    mc0_start = env_before["card_edac"]["mc0_ue"]
    host_ue_start = env_before["saturn_edac_ue_sum"]
    print(f"=== MRV-195 FORMAL n=3 INTERLEAVED CAMPAIGN START (resume_2b={resume_from_2b}, resume_3a={resume_from_3a}) ===", flush=True)
    print(f"PRE-ENV: cpu40={env_before['saturn_cpu40_khz']}kHz({env_before['saturn_cpu40_gov']}) "
          f"uncore={env_before['saturn_uncore_khz']} host_ue={host_ue_start} "
          f"rp_aer={env_before['saturn_rp_aer']} card_edac={env_before['card_edac']}", flush=True)

    def check_edac_gate(tag: str):
        snap = read_clocks_edac()
        ed = snap["card_edac"]
        aer = snap["saturn_rp_aer"]
        aer_sum = sum(v for v in aer.values() if v > 0)
        print(
            f"  [EDAC/AER GATE {tag}] card_edac={ed} (delta ea0={ed['ea0_ue']-ea0_start}, mc0={ed['mc0_ue']-mc0_start}) "
            f"host_ue={snap['saturn_edac_ue_sum']} rp_aer={aer}",
            flush=True,
        )
        if (
            ed["ea0_ue"] != ea0_start
            or ed["mc0_ue"] != mc0_start
            or snap["saturn_edac_ue_sum"] != host_ue_start
            or aer_sum != 0
        ):
            raise RuntimeError(f"EDAC/AER error at {tag}: {snap}")

    pol_orders = [
        ["sdp_fair", "direct_peek", "direct_safe"],
        ["direct_peek", "direct_safe", "sdp_fair"],
        ["direct_safe", "sdp_fair", "direct_peek"],
    ]

    run_seq = 0
    total_verified = 0
    total_retries = 0
    idle_cals = []

    if resume_from_2b or resume_from_3a:
        kept_runs = []
        for ln in runs_path.read_text().splitlines():
            if not ln.strip():
                continue
            obj = json.loads(ln)
            if obj.get("part") in keep_parts:
                kept_runs.append(obj)
                if obj["part"] == "part0_idle_cal":
                    idle_cals.append(obj["cal"]["app_work_kps"])
                else:
                    total_verified += 1
        kept_atts = []
        for ln in att_path.read_text().splitlines():
            if not ln.strip():
                continue
            obj = json.loads(ln)
            if obj.get("part") in keep_parts:
                kept_atts.append(obj)
                if not obj.get("verified"):
                    total_retries += 1
        assert len(idle_cals) == 3, f"expected 3 idle_cal rows, got {len(idle_cals)}"
        assert total_verified >= 108, f"expected >=108 verified rows, got {total_verified}"
        runs_path.write_text("".join(json.dumps(x, separators=(",", ":")) + "\n" for x in kept_runs))
        att_path.write_text("".join(json.dumps(x, separators=(",", ":")) + "\n" for x in kept_atts))
        run_seq = len(kept_atts)
        idle_kps_med = sorted(idle_cals)[1]
        print(
            f"[RESUMED] kept {len(kept_runs)} verified rows (runs={total_verified}, idle_cal=3), "
            f"idle_kps_med={idle_kps_med:.1f}",
            flush=True,
        )
    else:
        for p in (runs_path, att_path):
            if p.exists():
                p.unlink()
        for rep in range(3):
            out = sh([HOST_BIN, "--", "--idle-cal-ms", "1000"]).stdout.strip().splitlines()[-1]
            cal = json.loads(out)
            rec = {"ts": utc_now(), "part": "part0_idle_cal", "rep": rep, "cal": cal, "verified": True}
            append_jsonl(runs_path, rec)
            append_jsonl(att_path, rec)
            idle_cals.append(cal["app_work_kps"])
        idle_kps_med = sorted(idle_cals)[1]
        print(f"[PART 0] cpu40 idle_cal kops/s = {[round(x, 1) for x in idle_cals]} (med={idle_kps_med:.1f})", flush=True)

    def exec_case(
        part: str,
        rep: int,
        pol: str,
        wl: str,
        kd: str,
        dm: str,
        win: int,
        rk: float,
        thr: int,
        warmup_ms: int,
        duration_ms: int,
        app_work: bool = False,
        tx_pool_slots: int = 1024,
        max_retries: int = 5,
        card_queues: int = 4,
        stop_on_overload: bool = False,
    ):
        nonlocal run_seq, total_verified, total_retries
        last_errs = []
        ov_count = 0
        phy_count = 0
        ov_limit = 2 if stop_on_overload else max_retries
        attempt = 0
        while True:
            run_seq += 1
            seed = 195000 + rep * 10000 + run_seq * 17 + attempt
            stack.ensure_stack(pol, app_work=app_work, tx_pool_slots=tx_pool_slots)
            c, h, k, tad = run_client_synced(
                pol, wl, kd, dm, win, rk, thr,
                warmup_ms=warmup_ms, duration_ms=duration_ms, seed=seed,
                card_queues=card_queues,
            )
            errs = verify_run(pol, dm, c, h, k)
            last_errs = errs
            is_phy_crc = any("merc_rx_crc_errors_phy" in e for e in errs) and c["lost"] <= 3
            is_overload = (
                stop_on_overload
                and not is_phy_crc
                and len(errs) > 0
                and all(("window_cap_hits" in e or "client lost=" in e or "merc_rx_crc_errors_phy" in e) for e in errs)
                and c["bad"] == 0 and c["torn"] == 0 and c["send_err"] == 0
            )
            pmu = (h or {}).get("pmu") or {}
            aw_cpu_frac = (1.0 - h["app_work_kps"] / idle_kps_med) if (app_work and h) else None
            aw_cyc_per_req = (
                (aw_cpu_frac * pmu["ghz"] * 1e9 / (c["achieved_mrps"] * 1e6))
                if (aw_cpu_frac is not None and pmu.get("ghz", 0) > 0 and c["achieved_mrps"] > 0)
                else None
            )
            rec = {
                "ts": utc_now(),
                "part": part,
                "rep": rep,
                "attempt": attempt,
                "policy": pol,
                "workload": wl,
                "key_dist": kd,
                "drive_mode": dm,
                "window": win,
                "rate_krps": rk,
                "threads": thr,
                "card_queues": card_queues,
                "warmup_ms": warmup_ms,
                "duration_ms": duration_ms,
                "app_work": app_work,
                "tx_pool_slots": tx_pool_slots,
                "idle_cal_kps_med": idle_kps_med,
                "aw_cpu_frac": aw_cpu_frac,
                "aw_cyc_per_req": aw_cyc_per_req,
                "seed": seed,
                "verified": len(errs) == 0,
                "overload": is_overload,
                "verify_errors": errs,
                "client": c,
                "host": h,
                "card": k,
                "tad_delta": tad,
            }
            append_jsonl(att_path, rec)
            if not errs:
                append_jsonl(runs_path, rec)
                total_verified += 1
                busy_pct = (h["busy_ns_per_req"] * c["achieved_mrps"] * 0.1) if h else 0.0
                aw_str = f" aw_cyc={aw_cyc_per_req:5.0f}" if aw_cyc_per_req is not None else ""
                print(
                    f"[{total_verified:03d}] {part:18s} r{rep} {pol:11s} {wl:7s} {kd:7s} {dm:7s} "
                    f"W={win:5d} R={rk:6.0f} -> {c['achieved_mrps']:6.3f} Mrps ({c['wire_rx_gbps']:5.1f}/{c['wire_tx_gbps']:4.1f}G) "
                    f"p50={c['tot_p50_us']:6.2f} p99={c['tot_p99_us']:6.2f} sm99={c['small_p99_us']:6.2f} "
                    f"cyc={pmu.get('cycles_per_req',0):5.0f} b%={busy_pct:4.0f}{aw_str} imc={pmu.get('imc_bytes_per_req',0):5.1f}B",
                    flush=True,
                )
                return rec
            total_retries += 1
            attempt += 1
            if is_phy_crc:
                phy_count += 1
                print(f"  [PHY-CRC RETRY {phy_count}/5] {part} r{rep} {pol} {wl} {kd} R={rk}: {errs}", flush=True)
                if phy_count >= 5:
                    raise RuntimeError(f"Too many physical CRC errors: {part} r{rep} {pol} {wl} {kd} R={rk} errs={last_errs}")
                continue
            ov_count += 1
            print(f"  [RETRY {ov_count}/{ov_limit}] {part} r{rep} {pol} {wl} {kd} R={rk}: {errs}", flush=True)
            if ov_count >= ov_limit:
                if is_overload:
                    print(f"  [OVERLOAD KNEE REACHED] {part} r{rep} {pol} {wl} {kd} R={rk}: {last_errs}", flush=True)
                    return None
                raise RuntimeError(f"Failed after {ov_limit} attempts: {part} r{rep} {pol} {wl} {kd} R={rk} errs={last_errs}")
    if not resume_from_2b and not resume_from_3a:
        print("\n=== PART 1: SINGLE-CORE SATURATION MATRIX (W=512, T=8, n=3 interleaved) ===", flush=True)
        sat_workloads = [
            ("get64", "zipf"),
            ("put64", "zipf"),
            ("get256", "zipf"),
            ("get1024", "zipf"),
            ("get4096", "zipf"),
            ("put4096", "zipf"),
            ("mix", "zipf"),
            ("mix", "uniform"),
        ]
        for rep in range(3):
            for pol in pol_orders[rep]:
                for wl, kd in sat_workloads:
                    exec_case("part1_sat", rep, pol, wl, kd, "closed", 512, 0.0, 8, 300, 1200)
            check_edac_gate(f"part1 rep {rep}")

        print("\n=== PART 2A: W=1 UNLOADED END-TO-END LATENCY (n=3 interleaved) ===", flush=True)
        w1_workloads = [("get64", "zipf"), ("get256", "zipf"), ("get1024", "zipf"), ("get4096", "zipf")]
        for rep in range(3):
            for pol in pol_orders[rep]:
                for wl, kd in w1_workloads:
                    exec_case("part2a_w1", rep, pol, wl, kd, "closed", 1, 0.0, 1, 150, 500)

    # -------------------------------------------------------------------------
    # PART 2B: Open-loop Poisson load-p99 knee curves (T=8, W=4096, n=3 interleaved)
    #   W=4096 (512/thread -> 1024/card_core) matches DirectPush RX_SUBRING_SLOTS.
    #   Rates above an arm's lossless knee are logged with overload=True and stop climbing.
    # -------------------------------------------------------------------------
    if not resume_from_3a:
        print("\n=== PART 2B: OPEN-LOOP POISSON LOAD-p99 KNEE CURVES (T=8, W=4096, n=3 interleaved) ===", flush=True)
        poisson_plan = {
            "mix_zipf": {
                "wl": "mix", "kd": "zipf", "win": 4096, "thr": 8,
                "rates": {
                    "sdp_fair":    [(500, False), (1500, False), (2500, False), (3000, False), (3500, True), (4000, True), (4500, True)],
                    "direct_peek": [(500, False), (1500, False), (2500, False), (3000, False), (3500, False), (4000, False), (4500, False), (5000, False), (5300, True), (5700, True), (6000, True)],
                    "direct_safe": [(500, False), (1500, False), (2500, False), (3000, False), (3500, False), (4000, False), (4500, False), (5000, False), (5300, True), (5700, True), (6000, True)],
                },
            },
            "mix_uniform": {
                "wl": "mix", "kd": "uniform", "win": 4096, "thr": 8,
                "rates": {
                    "sdp_fair":    [(500, False), (1500, False), (2500, False), (3000, False), (3500, True), (4000, True)],
                    "direct_peek": [(500, False), (1500, False), (2500, False), (3000, False), (3500, False), (4000, False), (4500, False), (5000, True), (5400, True)],
                    "direct_safe": [(500, False), (1500, False), (2500, False), (3000, False), (3500, False), (4000, False), (4500, False), (5000, True), (5400, True)],
                },
            },
            "get64_zipf": {
                "wl": "get64", "kd": "zipf", "win": 4096, "thr": 8,
                "rates": {
                    "sdp_fair":    [(1000, False), (3000, False), (6000, False), (7500, False), (9000, True), (10000, True), (10800, True)],
                    "direct_peek": [(1000, False), (3000, False), (6000, False), (7500, False), (9000, False), (10500, False), (12000, True), (13500, True), (15000, True)],
                    "direct_safe": [(1000, False), (3000, False), (6000, False), (7500, False), (9000, False), (10500, False), (12000, True), (13500, True), (15000, True), (16000, True)],
                },
            },
        }
        for curve_name, spec in poisson_plan.items():
            for rep in range(3):
                for pol in pol_orders[rep]:
                    for rk, stop_ov in spec["rates"][pol]:
                        res = exec_case(
                            f"part2b_{curve_name}",
                            rep, pol, spec["wl"], spec["kd"], "poisson",
                            spec["win"], float(rk), spec["thr"], 500, 1000,
                            stop_on_overload=stop_ov,
                        )
                        if res is None:
                            break
            check_edac_gate(curve_name)
    # -------------------------------------------------------------------------
    # PART 3A: Matched-rate --app-work spare-CPU + paired aw=0 IMC/latency (n=3 interleaved)
    # -------------------------------------------------------------------------
    print("\n=== PART 3A: MATCHED-RATE --app-work SPARE-CPU & PAIRED aw=0 IMC LEDGER (n=3 interleaved) ===", flush=True)
    aw_cases = [
        ("get64",   "zipf",    4500.0, 4096),
        ("get256",  "zipf",    4500.0, 4096),
        ("get1024", "zipf",    2500.0, 4096),
        ("get4096", "zipf",    1000.0, 4096),
        ("mix",     "zipf",    2500.0, 4096),
        ("mix",     "uniform", 2500.0, 4096),
    ]
    for rep in range(3):
        for pol in pol_orders[rep]:
            for wl, kd, rk, win in aw_cases:
                exec_case("part3a_appwork", rep, pol, wl, kd, "poisson", win, rk, 8, 500, 1000, app_work=True)
    check_edac_gate("part3a_appwork")

    for rep in range(3):
        for pol in pol_orders[rep]:
            for wl, kd, rk, win in aw_cases:
                exec_case("part3a_matched_aw0", rep, pol, wl, kd, "poisson", win, rk, 8, 500, 1000, app_work=False)
    check_edac_gate("part3a_matched_aw0")

    # -------------------------------------------------------------------------
    # PART 3B: 128 MiB retained TX pool sensitivity (direct_safe, n=3)
    # -------------------------------------------------------------------------
    print("\n=== PART 3B: 128 MiB RETAINED TX POOL SENSITIVITY (direct_safe, n=3) ===", flush=True)
    for rep in range(3):
        for wl, kd in [("get1024", "zipf"), ("get4096", "zipf"), ("mix", "zipf")]:
            exec_case("part3b_pool128m", rep, "direct_safe", wl, kd, "closed", 512, 0.0, 8, 300, 1000, tx_pool_slots=32768)
    check_edac_gate("part3b_pool128m")

    # -------------------------------------------------------------------------
    # PART 3C: Deployed-binary SDP fair-full controls (n=3 interleaved)
    #   (1) rx_poll_tail_off=48 (`sdp_fair`) vs 0 (`sdp_notail`) on 64 B
    #   (2) 1-queue SDP (`sdp_1q`, 1Q x 1024 desc, 1 card core) vs 4Q (`sdp_fair`, 4Q x 256)
    # -------------------------------------------------------------------------
    print("\n=== PART 3C: SDP CONTROLS (rx_poll_tail_off=48 vs 0 on 64B + 1Q vs 4Q, n=3) ===", flush=True)
    notail_orders = [
        ["sdp_fair", "sdp_notail"],
        ["sdp_notail", "sdp_fair"],
        ["sdp_fair", "sdp_notail"],
    ]
    for rep in range(3):
        for pol in notail_orders[rep]:
            exec_case("part3c_sdp_tail", rep, pol, "get64", "zipf", "closed", 512, 0.0, 8, 300, 1000, app_work=False)
            exec_case("part3c_sdp_tail", rep, pol, "get64", "zipf", "closed", 1, 0.0, 1, 150, 500, app_work=False)
            exec_case("part3c_sdp_tail", rep, pol, "get64", "zipf", "poisson", 4096, 4500.0, 8, 500, 1000, app_work=False)
            exec_case("part3c_sdp_tail_aw", rep, pol, "get64", "zipf", "poisson", 4096, 4500.0, 8, 500, 1000, app_work=True)

    sdp1q_wls = [
        ("get64", "zipf", "closed", 512, 0.0, 8),
        ("get256", "zipf", "closed", 512, 0.0, 8),
        ("get1024", "zipf", "closed", 512, 0.0, 8),
        ("get4096", "zipf", "closed", 512, 0.0, 8),
        ("mix", "zipf", "closed", 512, 0.0, 8),
        ("get64", "zipf", "closed", 1, 0.0, 1),
    ]
    for rep in range(3):
        for wl, kd, dm, win, rk, thr in sdp1q_wls:
            exec_case("part3c_sdp_1q", rep, "sdp_1q", wl, kd, dm, win, rk, thr, 300, 1000, card_queues=1)
    check_edac_gate("part3c_sdp_controls")

    env_after_runs = read_clocks_edac()
    restore_info = None
    env_post_restore = None
    if do_restore:
        print("\n=== RESTORING CARD & MERCURY BASELINE + 21s EDAC/AER SETTLE ===", flush=True)
        restore_info = stack.restore_baseline()
        print(restore_info["restore_stdout"], flush=True)
        time.sleep(21)
        env_post_restore = read_clocks_edac()
        print(f"POST-RESTORE (21s): cpu40={env_post_restore['saturn_cpu40_khz']}kHz "
              f"uncore={env_post_restore['saturn_uncore_khz']} host_ue={env_post_restore['saturn_edac_ue_sum']} "
              f"rp_aer={env_post_restore['saturn_rp_aer']} card_edac={env_post_restore['card_edac']}", flush=True)
    else:
        stack.stop_card()

    env_doc = {
        "issue": "MRV-195",
        "ts_completed": utc_now(),
        "total_verified_runs": total_verified,
        "total_retries": total_retries,
        "idle_cal_kps": idle_cals,
        "idle_cal_kps_med": idle_kps_med,
        "ea0_ue_before_campaign": ea0_start,
        "ea0_ue_after_campaign": env_after_runs["card_edac"]["ea0_ue"],
        "ea0_ue_campaign_delta": env_after_runs["card_edac"]["ea0_ue"] - ea0_start,
        "ea0_ue_post_restore_delta": (
            env_post_restore["card_edac"]["ea0_ue"] - ea0_start if env_post_restore else None
        ),
        "env_before": env_before,
        "env_after_runs": env_after_runs,
        "restore_info": restore_info,
        "env_post_restore_21s": env_post_restore,
    }
    env_path.write_text(json.dumps(env_doc, indent=2) + "\n")
    print(f"\n=== CAMPAIGN COMPLETE: {total_verified} verified runs, {total_retries} retries, "
          f"ea0_delta={env_doc['ea0_ue_campaign_delta']} ===", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--smoke-controls", action="store_true")
    ap.add_argument("--campaign", action="store_true")
    ap.add_argument("--resume-from-2b", action="store_true")
    ap.add_argument("--resume-from-3a", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stack = Stack()
    restored = False
    try:
        if args.smoke:
            run_smoke(stack)
        if args.smoke_controls:
            run_smoke_controls(stack)
        if args.campaign:
            run_campaign(stack, do_restore=args.restore, resume_from_2b=args.resume_from_2b, resume_from_3a=args.resume_from_3a)
            restored = args.restore
    finally:
        if args.restore and not restored:
            print("\n=== FINALLY: RESTORING BASELINE AFTER INTERRUPT/ERROR ===", flush=True)
            stack.restore_baseline()
        elif not restored:
            stack.stop_card()


if __name__ == "__main__":
    main()
