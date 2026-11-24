#!/usr/bin/env python3
"""MRV-170 dual-machine 100GbE DirectPush performance verification harness.

Single-variable workload-outer 4-arm Latin-square interleaved n=3 comparison of:
  1. default           (stock DPDK scalar_enable=1 LDADDA)
  2. cqe_peek          (MRV-161 coherent ldar peek + on-demand LDADDA + 128 B clear)
  3. cqe_direct_wd0    (MRV-170 hardened TRUE zero-LDADDA: multi-seg + err/0-seg fallback
                        + full 128 B clear, watchdog_interval=0 -> 0 LDADDA on healthy pkts)
  4. cqe_direct_wd4096 (MRV-170 hardened 1/4096 periodic LDADDA watchdog hybrid:
                        adds sw_head==hw_head drift check once per CQ wrap)

Workloads: 64 B (OP_PUSH), 256 B (OP_PUSH), 1536 B (1.5 KiB, 2x768 B OP_DMA)
Windows:   W in {1, 4, 8, 32}
MRV-171 Gate 3 fixes applied:
  - primary matrix runs mercury client on clean NUMA 0 cpu 4 (off housekeeping cpu 0 / falcon-sensor)
  - includes a dedicated 64 B W=1 mercury cpu 0 bridge control (n=3) for direct MRV-161 baseline check
  - client --sync-reset pauses after warmup drains so card & host RESET happens cleanly
    after warmup and before measurement (zero warmup contamination in card.host_rtt).
"""

import json
import pathlib
import socket
import subprocess
import time
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv170/logs/mrv170-2026-11-24")
RAW_DIR = OUT_DIR / "raw_perf"
ATTEMPTS_PATH = OUT_DIR / "attempts.jsonl"
PERF_RUNS_PATH = OUT_DIR / "perf_runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"

HOST_BIN = "/tmp/mrv170-target/release/mrv170_host"
CARD_BIN = "/root/bench/mrv170_card"
MERC_BIN = "/tmp/mrv170/mrv170_client"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]

ETH_DEV = "0002:02:00.0,scalar_enable=1"
MERC_CPU_PRIMARY = 0
REPS = 3
WARMUP = 5_000
ROUNDS_LOW = 30_000
ROUNDS_W32 = 80_000

POLICY_ARGS = {
    "default": (),
    "cqe_peek": ("--cqe-peek",),
    "cqe_direct_wd0": ("--cqe-direct-safe", "--watchdog-interval", "0", "--full-clear", "true", "--drop-err", "true"),
    "cqe_direct_wd4096": ("--cqe-direct-safe", "--watchdog-interval", "4096", "--full-clear", "true", "--drop-err", "true"),
}

ROTATIONS_4 = [
    ["default", "cqe_peek", "cqe_direct_wd0", "cqe_direct_wd4096"],
    ["cqe_peek", "cqe_direct_wd0", "cqe_direct_wd4096", "default"],
    ["cqe_direct_wd0", "cqe_direct_wd4096", "default", "cqe_peek"],
    ["cqe_direct_wd4096", "default", "cqe_peek", "cqe_direct_wd0"],
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_attempt_id() -> int:
    if not ATTEMPTS_PATH.exists():
        return 1
    with ATTEMPTS_PATH.open() as f:
        return sum(1 for _ in f) + 1


def log_attempt(rec: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with ATTEMPTS_PATH.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()


def sh(cmd, check=True, timeout=120):
    r = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed ({r.returncode}): {cmd}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
    return r


def ssh_card(script, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def tcp_cmd(host, port, cmd, timeout=30.0):
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


def read_clocks_edac():
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    khz = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = None
    for cand in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*01*/current_freq_khz")):
        uncore = int(cand.read_text().strip())
        break
    host_ue = []
    for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count")):
        host_ue.append(int(p.read_text().strip()))
    card_info = ssh_card(
        "echo UE=$(cat /sys/devices/system/edac/mc/mc0/ue_count 2>/dev/null || echo NA) "
        "SEI=$(dmesg | grep -icE 'SEI|UER|SError' || echo 0)"
    ).stdout.strip()
    return {
        "saturn_cpu40_gov": gov,
        "saturn_cpu40_khz": khz,
        "saturn_uncore_pkg1_khz": uncore,
        "saturn_edac_ue": host_ue,
        "card_edac_sei": card_info,
    }


class Stack:
    def __init__(self):
        self.card_key = None
        self.host_proc = None

    def stop_host(self):
        if self.host_proc is not None:
            try:
                tcp_cmd("127.0.0.1", 7531, "QUIT", timeout=2.0)
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
            sh(["rm", "-f", "/dev/hugepages/m170h*"], check=False)

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m170 C-c 2>/dev/null || true; sleep 0.4; "
            "tmux kill-session -t m170 2>/dev/null || true; sleep 0.15; rm -f /dev/hugepages/m170*"
        )
        self.card_key = None

    def ensure_card(self, extra_args: tuple = ()):
        key = extra_args
        if self.card_key == key:
            return
        self.stop_card()
        ssh_card(
            "tmux kill-session -t refl 2>/dev/null || true; "
            "ip link set eth0 down 2>/dev/null || true; "
            "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
            'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
            "drv2=$(basename $(readlink -f /sys/bus/pci/devices/0002:01:00.2/driver) 2>/dev/null || echo none); "
            'if [ "$drv2" != "vfio-pci" ]; then sh /root/bench/dpdk_up.sh 0002:01:00.2; fi; '
            "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko"
        )
        extra_str = " ".join(extra_args)
        cmd = (
            f"{CARD_BIN} -l 3 -a {ETH_DEV} -a 0000:06:00.1 -a 0000:06:00.2 "
            f"--file-prefix=m170 -- --mode direct --dma-threshold 1024 {extra_str}"
        )
        ssh_card(
            f'tmux new-session -d -s m170 "{cmd} > /tmp/m170_card.log 2>&1"; '
            f'for i in $(seq 1 50); do grep -q "MRV133_CARD direct ready" /tmp/m170_card.log && break; sleep 0.15; done; '
            f'grep "MRV133_CARD direct ready" /tmp/m170_card.log'
        )
        self.card_key = key

    def ensure_host(self):
        if self.host_proc is not None and self.host_proc.poll() is None:
            return
        self.stop_host()
        cmd = [
            HOST_BIN, "-l", "40", "-a", "0000:99:02.0",
            "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m170h", "--",
            "--mode", "direct",
            "--dma-threshold", "1024",
            "--fix-stack-zero",
            "--bypass-tx-pool",
        ]
        with open("/tmp/m170_host_run.log", "w") as logf:
            self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        for _ in range(60):
            if self.host_proc.poll() is not None:
                raise RuntimeError(f"mrv170_host exited early:\n{pathlib.Path('/tmp/m170_host_run.log').read_text()}")
            txt = pathlib.Path("/tmp/m170_host_run.log").read_text()
            if "MRV133_HOST READY" in txt:
                time.sleep(0.1)
                return
            time.sleep(0.1)
        raise RuntimeError(f"mrv170_host timeout:\n{pathlib.Path('/tmp/m170_host_run.log').read_text()}")


def run_merc_synced(merc_cpu: int, workload: str, window: int, warmup: int, rounds: int, seed: int, timeout: int = 90):
    mcmd = (
        f"{MERC_BIN} --dev mlx5_2 --sgid 3 --cpu {merc_cpu} --mode closed --workload {workload} "
        f"--window {window} --warmup {warmup} --rounds {rounds} --seed {seed} --sync-reset"
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

        tcp_cmd("10.10.0.2", 7529, "RESET")
        tcp_cmd("127.0.0.1", 7531, "RESET")

        p.stdin.write("GO\n")
        p.stdin.flush()
        p.stdin.close()
        rest_out, err = p.communicate(timeout=timeout)
        lines.append(rest_out or "")
        full_out = "".join(lines)
        if p.returncode != 0:
            raise RuntimeError(f"mercury client rc={p.returncode}:\n{full_out}\n{err}")
        for l in full_out.splitlines():
            if l.startswith("MRV133_CLIENT_JSON "):
                return json.loads(l[len("MRV133_CLIENT_JSON "):]), mcmd, full_out
        raise RuntimeError(f"Missing MRV133_CLIENT_JSON:\n{full_out}\n{err}")
    except Exception:
        if p.poll() is None:
            p.kill()
            try:
                p.communicate(timeout=3)
            except Exception:
                pass
        raise


def verify_perf_run(pol: str, c, k, h, rounds):
    xs = k.get("eth_xstats", {})
    cp = k.get("cqe_peek", {})
    checks = {
        "client_verified": c.get("verified") is True,
        "client_lost_0": c.get("lost", -1) == 0,
        "client_bad_0": c.get("bad", -1) == 0,
        "client_torn_0": c.get("torn", -1) == 0,
        "client_send_err_0": c.get("send_err", -1) == 0,
        "client_done_exact": c.get("done_measured", -1) == rounds,
        "card_rpcs_exact": k.get("rpcs_in", -1) == rounds and k.get("rpcs_out", -1) == rounds,
        "card_bad_wire_0": k.get("bad_wire", -1) == 0,
        "card_bad_host_0": k.get("bad_host", -1) == 0,
        "card_torn_bar4_0": k.get("torn_bar4", -1) == 0,
        "card_dma_failed_0": k.get("dma_failed", -1) == 0,
        "card_eth_imissed_0": k.get("eth_imissed", -1) == 0,
        "card_eth_ierrors_0": k.get("eth_ierrors", -1) == 0,
        "card_eth_oerrors_0": k.get("eth_oerrors", -1) == 0,
        "card_eth_rx_nombuf_0": k.get("eth_rx_nombuf", -1) == 0,
        "card_rx_drop_0": xs.get("rx_drop", -1) == 0,
        "card_fatal_cq_err_0": cp.get("fatal_cq_err", 0) == 0,
        "card_cq_err_seen_0": cp.get("cq_err_seen", 0) == 0,
        "card_wd_head_mismatch_0": cp.get("watchdog_head_mismatch", 0) == 0,
        "host_bad_0": h.get("bad", -1) == 0,
        "host_torn_0": h.get("torn", -1) == 0,
        "host_page_faults_0": h.get("page_faults", -1) == 0,
        "host_rpcs_exact": h.get("rx_rpcs", -1) == rounds,
    }
    if pol == "cqe_direct_wd0":
        checks["wd0_zero_ldadda"] = cp.get("ldadda_calls", 0) == 0 and cp.get("watchdog_ldadda", 0) == 0 and cp.get("fallback_ldadda", 0) == 0
    return all(checks.values()), checks


def run_one(stack, idx, total, merc_cpu, tag_prefix, pol, wl, win, rounds, rep, seed):
    case_id = f"{tag_prefix}_{pol}_{wl}_w{win}_rep{rep}"
    aid = next_attempt_id()
    t0 = utc_now()
    err_str = None
    c = k = h = None
    ok = False
    checks = {}
    mcmd = ""
    try:
        stack.ensure_card(POLICY_ARGS[pol])
        stack.ensure_host()
        c, mcmd, raw_out = run_merc_synced(merc_cpu, wl, win, WARMUP, rounds, seed)
        k = tcp_cmd("10.10.0.2", 7529, "STATS")
        h = tcp_cmd("127.0.0.1", 7531, "STATS")
        ok, checks = verify_perf_run(pol, c, k, h, rounds)
        (RAW_DIR / f"attempt_{aid:04d}_{case_id}.log").write_text(raw_out)
    except Exception as e:
        err_str = repr(e)
    finally:
        t1 = utc_now()
        clks = read_clocks_edac()
        log_attempt({
            "attempt_id": aid,
            "case_id": case_id,
            "kind": "perf_direct",
            "is_retry": False,
            "ts_start": t0,
            "ts_end": t1,
            "merc_cpu": merc_cpu,
            "policy": pol,
            "workload": wl,
            "window": win,
            "rep": rep,
            "merc_cmd": mcmd,
            "verified": ok,
            "error": err_str,
            "failed_checks": [ck for ck, cv in checks.items() if not cv],
        })
    if err_str is not None or not ok:
        raise RuntimeError(f"[{idx}/{total}] {case_id} FAILED err={err_str} checks={checks}")
    rec = {
        "idx": idx,
        "attempt_id": aid,
        "name": f"{tag_prefix}_{pol}_{wl}_w{win}",
        "merc_cpu": merc_cpu,
        "policy": pol,
        "workload": wl,
        "window": win,
        "rep": rep,
        "seed": seed,
        "warmup": WARMUP,
        "rounds": rounds,
        "ts": t1,
        "verified": ok,
        "clocks": clks,
        "client": c,
        "card": k,
        "host": h,
    }
    with PERF_RUNS_PATH.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
    cp = k.get("cqe_peek", {})
    print(
        f"[{idx:3d}/{total}] {case_id:38s} "
        f"rate={c['achieved_mrps']:6.3f}M/s "
        f"merc={c['tot_p50_us']:6.3f}/{c['tot_p99_us']:6.3f}us "
        f"card={k['host_rtt_p50_us']:5.3f}/{k['host_rtt_p99_us']:5.3f}us "
        f"ld={cp.get('ldadda_calls',0)} wd={cp.get('watchdog_ldadda',0)} fb={cp.get('fallback_ldadda',0)}",
        flush=True,
    )


def load_done_cases() -> set[str]:
    done = set()
    if PERF_RUNS_PATH.exists():
        for line in PERF_RUNS_PATH.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("verified") is True:
                    done.add(f"{r['name']}_rep{r['rep']}")
    return done


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    done_cases = load_done_cases() if args.resume else set()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stack = Stack()
    env = {"ts_start": utc_now(), "merc_cpu_primary": MERC_CPU_PRIMARY, "eth_dev": ETH_DEV, "before": read_clocks_edac()}

    workloads = [
        ("64", 1, ROUNDS_LOW),
        ("64", 4, ROUNDS_LOW),
        ("64", 8, ROUNDS_LOW),
        ("64", 32, ROUNDS_W32),
        ("256", 1, ROUNDS_LOW),
        ("256", 4, ROUNDS_LOW),
        ("256", 8, ROUNDS_LOW),
        ("256", 32, ROUNDS_W32),
        ("1536", 1, ROUNDS_LOW),
        ("1536", 4, ROUNDS_LOW),
        ("1536", 8, ROUNDS_LOW),
        ("1536", 32, ROUNDS_W32),
    ]

    total = len(workloads) * 4 * REPS + 4 * REPS
    idx = 0
    try:
        # 1. Dedicated mercury cpu 4 clean-core control (MRV-171 Gate 3 off-cpu0 check, 64 B W=1)
        for rep in range(1, REPS + 1):
            rot = ROTATIONS_4[(rep - 1) % 4]
            seed = 0x1610_0000 + rep * 100
            for pol in rot:
                idx += 1
                if f"cpu4_clean_{pol}_64_w1_rep{rep}" in done_cases:
                    continue
                run_one(stack, idx, total, 4, "cpu4_clean", pol, "64", 1, ROUNDS_LOW, rep, seed)
        # 2. Primary 4-arm Latin-square interleaved matrix on exact MRV-161 baseline (mercury cpu 0)
        for rep in range(1, REPS + 1):
            for w_idx, (wl, win, rounds) in enumerate(workloads):
                rot = ROTATIONS_4[(rep - 1 + w_idx) % 4]
                seed = 0x1700_0000 + rep * 1000 + w_idx
                for pol in rot:
                    idx += 1
                    if f"direct_{pol}_{wl}_w{win}_rep{rep}" in done_cases:
                        continue
                    run_one(stack, idx, total, MERC_CPU_PRIMARY, "direct", pol, wl, win, rounds, rep, seed)
    finally:
        stack.stop_card()
        env["ts_end"] = utc_now()
        env["after_perf"] = read_clocks_edac()
        ENV_PATH.write_text(json.dumps(env, indent=2) + "\n")


if __name__ == "__main__":
    main()
