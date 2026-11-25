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
        """Policies: 'card_echo', 'sdp_fair', 'direct_peek', 'direct_safe'."""
        if policy == "card_echo":
            c_key = ("card-echo", "cqe-peek")
            h_key = None
        elif policy == "sdp_fair":
            c_key = ("sdp", "cqe-peek")
            h_key = ("sdp", app_work, tx_pool_slots)
        elif policy == "direct_peek":
            c_key = ("direct", "cqe-peek")
            h_key = ("direct", app_work, tx_pool_slots)
        elif policy == "direct_safe":
            c_key = ("direct", "cqe-direct-safe")
            h_key = ("direct", app_work, tx_pool_slots)
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
            cmode, rxmode = c_key
            if cmode == "card-echo":
                ready_tok = "card_echo ready"
                cmd = (
                    f"{CARD_BIN} -l 3-6 -a {ETH_DEV} --file-prefix=m195 -- "
                    f"--mode card-echo --rx-mode {rxmode}"
                )
            elif cmode == "sdp":
                ready_tok = "sdp ready"
                cmd = (
                    f"{CARD_BIN} -l 3-6 -a {ETH_DEV} -a 0002:01:00.2 --file-prefix=m195 -- "
                    f"--mode sdp --rx-mode {rxmode}"
                )
            else:
                ready_tok = "direct ready"
                cmd = (
                    f"{CARD_BIN} -l 3-6 -a {ETH_DEV} "
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
            hmode, aw, tps = h_key
            if hmode == "sdp":
                devarg = "0000:99:02.0,rx_poll_mem=1,rx_poll_tail_off=48,ism_enable=1,allow_small_oq=1,max_rings=4"
                extra = [
                    "--mode", "sdp",
                    "--sdp-queues", "4",
                    "--sdp-rx-desc", "256",
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
):
    prof = profile_for_workload(workload)
    if policy != "card_echo":
        tcp_cmd("127.0.0.1", 7531, f"RESET {prof}")
    tcp_cmd("10.10.0.2", 7529, "RESET_PEERS")
    merc_eth_before = read_merc_ethtool()

    mcmd = (
        f"{MERC_BIN} --dev mlx5_2 --sgid 3 --cpu 0 --threads {threads} --card-queues 4 "
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


def append_jsonl(path: pathlib.Path, obj: dict):
    with open(path, "a") as f:
        f.write(json.dumps(obj, separators=(",", ":")) + "\n")


def run_campaign(stack: Stack, do_restore: bool):
    runs_path = LOG_DIR / "runs.jsonl"
    att_path = LOG_DIR / "attempts.jsonl"
    env_path = LOG_DIR / "env.json"

    # Fresh archive files for the formal n=3 campaign
    for p in (runs_path, att_path):
        if p.exists():
            p.unlink()

    env_before = read_clocks_edac()
    ea0_start = env_before["card_edac"]["ea0_ue"]
    mc0_start = env_before["card_edac"]["mc0_ue"]
    print(f"=== MRV-195 FORMAL n=3 INTERLEAVED CAMPAIGN START ===", flush=True)
    print(f"PRE-ENV: cpu40={env_before['saturn_cpu40_khz']}kHz({env_before['saturn_cpu40_gov']}) "
          f"uncore={env_before['saturn_uncore_khz']} host_ue={env_before['saturn_edac_ue_sum']} "
          f"rp_aer={env_before['saturn_rp_aer']} card_edac={env_before['card_edac']}", flush=True)

    # Part 0: Host cpu40 idle calibration for app_work spare-CPU accounting (n=3)
    idle_cals = []
    for rep in range(3):
        out = sh([HOST_BIN, "--", "--idle-cal-ms", "1000"]).stdout.strip().splitlines()[-1]
        cal = json.loads(out)
        rec = {"ts": utc_now(), "part": "part0_idle_cal", "rep": rep, "cal": cal, "verified": True}
        append_jsonl(runs_path, rec)
        append_jsonl(att_path, rec)
        idle_cals.append(cal["app_work_kps"])
    idle_kps_med = sorted(idle_cals)[1]
    print(f"[PART 0] cpu40 idle_cal kops/s = {[round(x, 1) for x in idle_cals]} (med={idle_kps_med:.1f})", flush=True)

    pol_orders = [
        ["sdp_fair", "direct_peek", "direct_safe"],
        ["direct_peek", "direct_safe", "sdp_fair"],
        ["direct_safe", "sdp_fair", "direct_peek"],
    ]

    run_seq = 0
    total_verified = 0
    total_retries = 0

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
        max_retries: int = 3,
    ):
        nonlocal run_seq, total_verified, total_retries
        for attempt in range(max_retries):
            run_seq += 1
            seed = 195000 + rep * 10000 + run_seq * 17 + attempt
            stack.ensure_stack(pol, app_work=app_work, tx_pool_slots=tx_pool_slots)
            c, h, k, tad = run_client_synced(
                pol, wl, kd, dm, win, rk, thr, warmup_ms=warmup_ms, duration_ms=duration_ms, seed=seed
            )
            errs = verify_run(pol, dm, c, h, k)
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
                "warmup_ms": warmup_ms,
                "duration_ms": duration_ms,
                "app_work": app_work,
                "tx_pool_slots": tx_pool_slots,
                "idle_cal_kps_med": idle_kps_med,
                "aw_cpu_frac": aw_cpu_frac,
                "aw_cyc_per_req": aw_cyc_per_req,
                "seed": seed,
                "verified": len(errs) == 0,
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
                    f"[{total_verified:03d}] {part:14s} r{rep} {pol:11s} {wl:7s} {kd:7s} {dm:7s} "
                    f"W={win:4d} R={rk:6.0f} -> {c['achieved_mrps']:6.3f} Mrps ({c['wire_rx_gbps']:5.1f}/{c['wire_tx_gbps']:4.1f}G) "
                    f"p50={c['tot_p50_us']:6.2f} p99={c['tot_p99_us']:6.2f} sm99={c['small_p99_us']:6.2f} "
                    f"cyc={pmu.get('cycles_per_req',0):5.0f} b%={busy_pct:4.0f}{aw_str} imc={pmu.get('imc_bytes_per_req',0):5.1f}B",
                    flush=True,
                )
                return rec
            total_retries += 1
            print(f"  [RETRY {attempt+1}/{max_retries}] {part} r{rep} {pol} {wl} {kd} R={rk}: {errs}", flush=True)
        raise RuntimeError(f"Failed after {max_retries} attempts: {part} r{rep} {pol} {wl} {kd} R={rk} errs={errs}")

    # -------------------------------------------------------------------------
    # PART 1: Single-core saturation throughput, CPU cycles/req, IMC ledger (n=3 interleaved)
    # -------------------------------------------------------------------------
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
        ed = read_clocks_edac()["card_edac"]
        print(f"  [EDAC CHECK after part1 rep {rep}] card_edac={ed} (delta ea0={ed['ea0_ue']-ea0_start})", flush=True)
        if ed["ea0_ue"] != ea0_start or ed["mc0_ue"] != mc0_start:
            raise RuntimeError(f"EDAC error during part1 rep {rep}: {ed}")

    # -------------------------------------------------------------------------
    # PART 2A: W=1 unloaded end-to-end latency baseline (n=3 interleaved)
    # -------------------------------------------------------------------------
    print("\n=== PART 2A: W=1 UNLOADED END-TO-END LATENCY (n=3 interleaved) ===", flush=True)
    w1_workloads = [("get64", "zipf"), ("get256", "zipf"), ("get1024", "zipf"), ("get4096", "zipf")]
    for rep in range(3):
        for pol in pol_orders[rep]:
            for wl, kd in w1_workloads:
                exec_case("part2a_w1", rep, pol, wl, kd, "closed", 1, 0.0, 1, 150, 500)

    # -------------------------------------------------------------------------
    # PART 2B: Open-loop Poisson load-p99 knee curves (n=3 interleaved, zero CO)
    # -------------------------------------------------------------------------
    print("\n=== PART 2B: OPEN-LOOP POISSON LOAD-p99 KNEE CURVES (n=3 interleaved) ===", flush=True)
    poisson_plan = {
        "mix_zipf": {
            "wl": "mix", "kd": "zipf", "win": 4096, "thr": 8,
            "rates": {
                "sdp_fair":    [500, 1500, 2500, 3500, 4500, 5000, 5300],
                "direct_peek": [500, 1500, 2500, 3500, 4500, 5000, 5300, 5700, 6000],
                "direct_safe": [500, 1500, 2500, 3500, 4500, 5000, 5300, 5700, 6000],
            },
        },
        "mix_uniform": {
            "wl": "mix", "kd": "uniform", "win": 4096, "thr": 8,
            "rates": {
                "sdp_fair":    [500, 1500, 2500, 3500, 4500, 4900],
                "direct_peek": [500, 1500, 2500, 3500, 4500, 4900, 5400],
                "direct_safe": [500, 1500, 2500, 3500, 4500, 4900, 5400],
            },
        },
        "get64_zipf": {
            "wl": "get64", "kd": "zipf", "win": 8192, "thr": 8,
            "rates": {
                "sdp_fair":    [1000, 3000, 6000, 9000, 10500, 11300],
                "direct_peek": [1000, 3000, 6000, 9000, 10500, 11300, 13000, 14500],
                "direct_safe": [1000, 3000, 6000, 9000, 10500, 11300, 13000, 14500, 16000],
            },
        },
    }
    for curve_name, spec in poisson_plan.items():
        for rep in range(3):
            for pol in pol_orders[rep]:
                for rk in spec["rates"][pol]:
                    exec_case(
                        f"part2b_{curve_name}",
                        rep, pol, spec["wl"], spec["kd"], "poisson",
                        spec["win"], float(rk), spec["thr"], 300, 1000,
                    )
        ed = read_clocks_edac()["card_edac"]
        print(f"  [EDAC CHECK after {curve_name}] card_edac={ed} (delta ea0={ed['ea0_ue']-ea0_start})", flush=True)
        if ed["ea0_ue"] != ea0_start or ed["mc0_ue"] != mc0_start:
            raise RuntimeError(f"EDAC error during {curve_name}: {ed}")

    # -------------------------------------------------------------------------
    # PART 3: Matched-rate --app-work spare-CPU cross-check + 128 MiB TX pool control
    # -------------------------------------------------------------------------
    print("\n=== PART 3A: MATCHED-RATE --app-work SPARE-CPU LEDGER (n=3 interleaved) ===", flush=True)
    aw_cases = [
        ("get64",   "zipf", 8000.0, 4096),
        ("get256",  "zipf", 8000.0, 4096),
        ("mix",     "zipf", 4000.0, 4096),
        ("get4096", "zipf", 1200.0, 2048),
    ]
    for rep in range(3):
        for pol in pol_orders[rep]:
            for wl, kd, rk, win in aw_cases:
                exec_case("part3a_appwork", rep, pol, wl, kd, "poisson", win, rk, 8, 300, 1000, app_work=True)

    print("\n=== PART 3B: 128 MiB RETAINED TX POOL SENSITIVITY (direct_safe, n=3) ===", flush=True)
    for rep in range(3):
        for wl, kd in [("get1024", "zipf"), ("get4096", "zipf"), ("mix", "zipf")]:
            exec_case("part3b_pool128m", rep, "direct_safe", wl, kd, "closed", 512, 0.0, 8, 300, 1000, tx_pool_slots=32768)

    env_after_runs = read_clocks_edac()
    restore_info = None
    env_post_restore = None
    if do_restore:
        print("\n=== RESTORING CARD & MERCURY BASELINE + 20s EDAC/AER SETTLE ===", flush=True)
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
    ap.add_argument("--campaign", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stack = Stack()
    try:
        if args.smoke:
            run_smoke(stack)
        if args.campaign:
            run_campaign(stack, do_restore=args.restore)
    finally:
        if args.restore and not args.campaign:
            stack.restore_baseline()
        elif not args.campaign:
            stack.stop_card()


if __name__ == "__main__":
    main()
