#!/usr/bin/env python3
"""MRV-163: Single-variable comparison of CN106 NIX0 TX TM tree
`ROC_NIX_TM_DEFAULT` (single shared SMQ[0]) vs `ROC_NIX_TM_RLIMIT` (per-SQ SMQ)
at 64 B L2 frames (Stage 0 card-only TX -> 3m DAC -> mercury PHY), plus
hardware `roc_clk_freq_get(&rclk, &sclk)` readout, `TadPmu`, per-SQ `tx_retries`,
and `rte_eth_dev_priv_dump` hardware register evidence.
"""

import json
import pathlib
import re
import socket
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/tmp/mrv163-logs")
TM_DUMP_DIR = OUT_DIR / "tm_dumps"
RUNS_PATH = OUT_DIR / "runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"

CARD_BIN = "/root/bench/mrv163_card"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]

REPS = 3
WARMUP_SECS = 0.4
MEAS_SECS = 1.5

MERC_AGENT_PY = r"""
import json, subprocess, sys, time

KEYS = (
    "rx_packets_phy",
    "rx_bytes_phy",
    "rx_crc_errors_phy",
    "rx_discards_phy",
    "tx_global_pause",
    "rx_global_pause",
)

def snap():
    out = subprocess.check_output(["ethtool", "-S", "ens6f1np1"], text=True)
    t = time.monotonic()
    d = {"t_mono": t}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            ks = k.strip()
            if ks in KEYS:
                d[ks] = int(v.strip())
    return d

sys.stdout.write(json.dumps({"ready": True}) + "\n")
sys.stdout.flush()
for line in sys.stdin:
    cmd = line.strip()
    if cmd == "SNAP":
        sys.stdout.write(json.dumps(snap()) + "\n")
        sys.stdout.flush()
    elif cmd == "QUIT":
        break
"""


def sh(cmd, check=True, timeout=60):
    r = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"CMD FAILED ({r.returncode}): {cmd}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
    return r


def ssh_card(script, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def ssh_merc(script, check=True, timeout=60):
    return sh(MERC_SSH + [script], check=check, timeout=timeout)


def tcp_cmd(host, port, cmd, timeout=5.0):
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((cmd + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            c = s.recv(4096)
            if not c:
                break
            buf += c
        return json.loads(buf.decode().strip())


class MercPhyAgent:
    """Persistent local ethtool -S sampler on mercury with unbiased local monotonic timestamps."""

    def __init__(self):
        self.p = subprocess.Popen(
            MERC_SSH + ["python3 -u -c " + shlex.quote(MERC_AGENT_PY)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        line = self.p.stdout.readline().strip()
        if not line:
            raise RuntimeError("mercury agent failed: " + self.p.stderr.read())
        info = json.loads(line)
        assert info.get("ready"), f"bad mercury ready: {info}"

    def snap(self):
        self.p.stdin.write("SNAP\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline().strip()
        return json.loads(line)

    def close(self):
        try:
            self.p.stdin.write("QUIT\n")
            self.p.stdin.flush()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def phy_delta(a, b):
    dt = max(b["t_mono"] - a["t_mono"], 1e-9)
    rx_pkts = b["rx_packets_phy"] - a["rx_packets_phy"]
    rx_bytes = b["rx_bytes_phy"] - a["rx_bytes_phy"]
    crc_err = b.get("rx_crc_errors_phy", 0) - a.get("rx_crc_errors_phy", 0)
    discards = b.get("rx_discards_phy", 0) - a.get("rx_discards_phy", 0)
    tx_pause = b.get("tx_global_pause", 0) - a.get("tx_global_pause", 0)
    rx_pause = b.get("rx_global_pause", 0) - a.get("rx_global_pause", 0)
    rx_mpps = rx_pkts / dt / 1e6
    l2_gbps = (rx_bytes * 8.0) / dt / 1e9
    wire_gbps = ((rx_bytes + rx_pkts * 20) * 8.0) / dt / 1e9
    avg_l2_bytes = (rx_bytes / rx_pkts) if rx_pkts > 0 else 0.0
    return {
        "dt_s": dt,
        "rx_pkts": rx_pkts,
        "rx_bytes": rx_bytes,
        "avg_l2_bytes": avg_l2_bytes,
        "rx_mpps": rx_mpps,
        "l2_gbps": l2_gbps,
        "wire_gbps": wire_gbps,
        "crc_err": crc_err,
        "discards": discards,
        "tx_global_pause": tx_pause,
        "rx_global_pause": rx_pause,
    }


def tad_delta(a, b):
    dt = max(b["t_s"] - a["t_s"], 1e-9)
    min_pct = 100.0
    out = {"dt_s": dt}
    for k in a["tad"]:
        v0, e0, r0 = a["tad"][k]
        v1, e1, r1 = b["tad"][k]
        de = e1 - e0
        dr = r1 - r0
        if de > 0:
            min_pct = min(min_pct, 100.0 * dr / de)
        out[k + "_mops"] = (v1 - v0) / dt / 1e6
    out["min_running_pct"] = min_pct
    return out


def check_env():
    edac_host = sum(
        int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count"))
    )
    gov40 = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    uncore = {
        p.parent.name: int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz"))
    }
    card_out = ssh_card(
        "echo freq=$(cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq);"
        "echo edac_ue=$(grep . /sys/devices/system/edac/*/*/ue_count | awk -F: '{s+=$2} END{print s+0}');"
        "echo sei_uer=$(dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || true);"
        "echo bar4_idx7=$(python3 /root/bar4idx7.py | awk '{print $NF}');"
        "echo dpdk_pkg=$(dpkg-query -W -f='${Version}' dpdk-24.11-cn10k 2>/dev/null || echo unknown)"
    ).stdout
    cd = {}
    for l in card_out.strip().splitlines():
        if "=" in l:
            k, v = l.split("=", 1)
            cd[k] = v
    merc_out = ssh_merc(
        "ethtool -S ens6f1np1 | grep -E 'rx_packets_phy:|rx_bytes_phy:|rx_crc_errors_phy:|tx_global_pause:'"
    ).stdout
    md = {}
    for l in merc_out.strip().splitlines():
        if ":" in l:
            k, v = l.split(":", 1)
            md[k.strip()] = int(v.strip())
    return {
        "saturn": {"cpu40_gov": gov40, "uncore_khz": uncore, "edac_ue_sum": edac_host},
        "cn106": cd,
        "mercury": md,
    }


def prepare_card_vfio():
    ssh_card(
        "tmux send-keys -t refl C-c 2>/dev/null || true; sleep 0.3; "
        "tmux kill-session -t refl 2>/dev/null || true; "
        "tmux kill-session -t m163 2>/dev/null || true; "
        "pkill -9 mrv163_card 2>/dev/null || true; "
        "rm -f /dev/hugepages/refl* /dev/hugepages/m163*; "
        "ip link set eth0 down 2>/dev/null || true; "
        "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
        'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi'
    )


def start_card(nc, pool_mode, tm_mode, sz=64):
    ssh_card("tmux kill-session -t m163 2>/dev/null || true; sleep 0.15; rm -f /dev/hugepages/m163* /tmp/m163.log /tmp/m163_priv.txt")
    cmd = (
        f"{CARD_BIN} -l 2-{1+nc} -a 0002:02:00.0 --file-prefix=m163 -- "
        f"--mode stage0-gen --pool-mode {pool_mode} --tm-mode {tm_mode} "
        f"--frame-kind raw --pkt-bytes {sz} --dst-mac 02:15:50:00:00:01 --dst-ip 10.10.100.1 "
        f"--priv-dump-path /tmp/m163_priv.txt"
    )
    r = ssh_card(
        f'tmux new-session -d -s m163 "{cmd} > /tmp/m163.log 2>&1"; '
        f'for i in $(seq 1 60); do grep -q "MRV163_CARD READY" /tmp/m163.log && break; sleep 0.2; done; '
        f'grep "MRV163_CARD READY" /tmp/m163.log',
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError("card start failed:\n" + ssh_card("cat /tmp/m163.log", check=False).stdout)
    return r.stdout.strip()


def stop_card():
    ssh_card(
        "tmux send-keys -t m163 C-c 2>/dev/null || true; sleep 0.35; "
        "tmux kill-session -t m163 2>/dev/null || true; rm -f /dev/hugepages/m163*",
        check=False,
    )


def capture_tm_dump(cfg):
    out = ssh_card(
        "echo '=== LOG TM LINES ==='; "
        "grep -E 'MRV163_CARD READY|TM |->|NIX_AF_(TL|MDQ)|txschq' /tmp/m163.log || true; "
        "echo '=== PRIV DUMP FILE ==='; "
        "grep -E '===TM|TM |txschq|pf =|vf =|nb_tx_queues' /tmp/m163_priv.txt || true",
        check=False,
    ).stdout
    (TM_DUMP_DIR / f"{cfg}.txt").write_text(out)
    # Extract concise SQ -> SMQ mapping summary for JSON
    sq_map = re.findall(r"(SQ_\d+->SMQ/MDQ_-?\d+)", out)
    # Keep only active tree mappings (non -1)
    active_sq_map = [m for m in sq_map if not m.endswith("-1")]
    pir_regs = re.findall(r"(NIX_AF_MDQ\[\d+\]_PIR=0x[0-9a-f]+)", out)
    return {"active_sq_map": active_sq_map, "mdq_pir": pir_regs}


def build_specs():
    specs = []
    # Part A: Primary single-variable core scaling (ROC_NIX_TM_DEFAULT vs ROC_NIX_TM_RLIMIT)
    for nc in [1, 2, 4, 6, 8]:
        for tm in ["default", "rlimit"]:
            specs.append({
                "part": "partA_core_scaling",
                "cfg": f"s0_64B_percore_{tm}_c{nc}",
                "nc": nc,
                "pool_mode": "per-core",
                "tm_mode": tm,
                "sz": 64,
            })
    # Part B: Zero-shaper control (rlimit-q0: only SMQ[0] PIR=100G, SMQ[1..C-1] PIR=0)
    for nc in [4, 8]:
        specs.append({
            "part": "partB_zero_shaper_ctrl",
            "cfg": f"s0_64B_percore_rlimitq0_c{nc}",
            "nc": nc,
            "pool_mode": "per-core",
            "tm_mode": "rlimit-q0",
            "sz": 64,
        })
    # Part C: Zero-NPA-free refcnt reuse control (default vs rlimit)
    for nc in [4, 8]:
        for tm in ["default", "rlimit"]:
            specs.append({
                "part": "partC_reuse_ctrl",
                "cfg": f"s0_64B_reuse_{tm}_c{nc}",
                "nc": nc,
                "pool_mode": "reuse",
                "tm_mode": tm,
                "sz": 64,
            })
    return specs


def run_one(merc: MercPhyAgent, sp: dict, rep: int):
    nc = sp["nc"]
    pm = sp["pool_mode"]
    tm = sp["tm_mode"]
    sz = sp["sz"]
    tm_meta = None
    try:
        ready = start_card(nc, pm, tm, sz)
        if rep == 0 and (nc in (1, 4, 6, 8) or pm == "reuse"):
            tm_meta = capture_tm_dump(sp["cfg"])
        tcp_cmd("10.10.0.2", 7535, "START")
        time.sleep(WARMUP_SECS)
        tcp_cmd("10.10.0.2", 7535, "RESET")
        t0 = tcp_cmd("10.10.0.2", 7535, "PMU_SNAP")
        m0 = merc.snap()
        t_wall0 = time.monotonic()
        time.sleep(MEAS_SECS)
        m1 = merc.snap()
        t_wall1 = time.monotonic()
        t1 = tcp_cmd("10.10.0.2", 7535, "PMU_SNAP")
        cs = tcp_cmd("10.10.0.2", 7535, "STATS")
        tcp_cmd("10.10.0.2", 7535, "STOP")
    finally:
        stop_card()

    phy = phy_delta(m0, m1)
    tad = tad_delta(t0, t1)
    dt_wall = t_wall1 - t_wall0
    card_dt = max(cs.get("elapsed_s", dt_wall), 1e-9)
    eth_opkts = cs["eth"]["opackets"] if cs.get("eth") else 0
    card_eth_mpps = eth_opkts / card_dt / 1e6
    card_sw_mpps = cs.get("pkts_out", 0) / card_dt / 1e6
    rel_diff = abs(card_eth_mpps - phy["rx_mpps"]) / max(phy["rx_mpps"], 1e-6)

    errs = []
    if tad["min_running_pct"] < 99.9:
        errs.append(f"tad_pmu_pct={tad['min_running_pct']:.2f}")
    if cs.get("tx_drops", -1) != 0 or cs.get("alloc_fails", -1) != 0:
        errs.append(f"card_drops={cs.get('tx_drops')} alloc_fails={cs.get('alloc_fails')}")
    if cs.get("eth", {}).get("oerrors", -1) != 0:
        errs.append(f"eth_oerrors={cs.get('eth', {}).get('oerrors')}")
    if phy["rx_pkts"] == 0:
        errs.append("phy_rx_zero")
    if abs(phy["avg_l2_bytes"] - sz) > 0.01:
        errs.append(f"avg_l2_bytes={phy['avg_l2_bytes']:.3f}")
    if rel_diff > 0.015:
        errs.append(f"card_vs_phy_diff={rel_diff*100:.2f}% (card={card_eth_mpps:.2f}M phy={phy['rx_mpps']:.2f}M)")
    if cs.get("rclk_mhz", 0) == 0 or cs.get("sclk_mhz", 0) == 0:
        errs.append(f"clk_zero rclk={cs.get('rclk_mhz')} sclk={cs.get('sclk_mhz')}")

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "part": sp["part"],
        "cfg": sp["cfg"],
        "rep": rep,
        "spec": sp,
        "ready_line": ready,
        "tm_dump_meta": tm_meta,
        "dt_wall_s": dt_wall,
        "card_eth_mpps": card_eth_mpps,
        "card_sw_mpps": card_sw_mpps,
        "card_vs_phy_rel_diff": rel_diff,
        "card": cs,
        "mercury_phy": phy,
        "tad_pmu": tad,
        "verified": len(errs) == 0,
        "verify_errors": errs,
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TM_DUMP_DIR.mkdir(parents=True, exist_ok=True)
    env_before = check_env()
    print("ENV BEFORE:", json.dumps(env_before), flush=True)
    prepare_card_vfio()

    merc = MercPhyAgent()
    specs = build_specs()
    done = 0
    failed = 0

    try:
        with open(RUNS_PATH, "w") as out:
            for rep in range(REPS):
                order = list(specs) if rep % 2 == 0 else list(reversed(specs))
                for idx, sp in enumerate(order):
                    rec = run_one(merc, sp, rep)
                    retries = 0
                    while not rec["verified"] and retries < 2:
                        retries += 1
                        print(f"  RETRY#{retries} rep={rep} {sp['cfg']} errs={rec['verify_errors']}", flush=True)
                        rec = run_one(merc, sp, rep)
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    done += 1
                    if not rec["verified"]:
                        failed += 1
                    phy = rec["mercury_phy"]
                    c = rec["card"]
                    tad = rec["tad_pmu"]
                    print(
                        f"[{rep+1}/{REPS} {idx+1:2d}/{len(specs)}] {sp['cfg']:28s} | "
                        f"phy={phy['rx_mpps']:6.2f}M (card={rec['card_eth_mpps']:6.2f}M) "
                        f"wire={phy['wire_gbps']:5.2f}G crc={phy['crc_err']} "
                        f"ret={c['tx_retries']/c['elapsed_s']/1e6:6.1f}M/s "
                        f"tad={tad['tad_req_msh_in_any_mops']:5.1f}M/s "
                        f"sclk={c['sclk_mhz']} rclk={c['rclk_mhz']} ok={rec['verified']}",
                        flush=True,
                    )
    finally:
        stop_card()
        merc.close()

    env_after = check_env()
    ENV_PATH.write_text(json.dumps({"before": env_before, "after": env_after, "done": done, "failed": failed}, indent=2))
    print(f"ALL DONE total={done} failed={failed}", flush=True)
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
