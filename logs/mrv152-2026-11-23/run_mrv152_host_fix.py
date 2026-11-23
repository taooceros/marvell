#!/usr/bin/env python3
"""MRV-152 real 2-machine 100GbE W=1 ablation for DirectPush sub-microsecond host-NIC path.

Tests the exact MRV-133 100GbE path (mercury mlx5_2 <-> 3m DAC <-> CN106 eth0 1C <-> saturn cpu40)
with warm-calibrated mrv133_client, mrv152_card, and mrv152_host toggling ONLY:
  1. direct_baseline       (exact MRV-133: no card dsb sy WC flush, host stack-zero + cold 128 MiB tx_pool)
  2. direct_host_clean     (no card dsb sy WC flush, host --fix-stack-zero --bypass-tx-pool)
  3. direct_wc_flush_clean (card --wc-flush dsb sy + host --fix-stack-zero --bypass-tx-pool)
plus card_echo reference on the exact same warm mrv133_client.

Interleaved n=3 replicates across {64 B, 256 B}, zero-error verification on every run,
and full card restoration + 20 s EDAC check in finally.
"""

import json
import pathlib
import socket
import subprocess
import time
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv152/logs/mrv152-2026-11-23")
RUNS_PATH = OUT_DIR / "runs_host_fix.jsonl"
ENV_PATH = OUT_DIR / "env_host_fix.json"

HOST_BIN = "/tmp/mrv152-target/release/mrv152_host"
CARD_BIN = "/root/bench/mrv152_card"
MERC_BIN = "/tmp/mrv152/mrv133_client"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]

REPS = 3
WARMUP = 5_000
ROUNDS = 30_000


def sh(cmd, check=True, timeout=120):
    r = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed ({r.returncode}): {cmd}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}")
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
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode().strip())


def check_env():
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    cur = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = {
        p.parent.name: int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz"))
    }
    edac_host = [
        int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count"))
    ]
    assert gov == "performance", f"cpu40 gov={gov}"
    assert uncore.get("package_01_die_01", 0) >= 2_190_000, f"uncore={uncore}"
    card_freq = int(ssh_card("cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq").stdout.strip())
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "saturn_cpu40_gov": gov,
        "saturn_cpu40_khz": cur,
        "saturn_uncore_khz": uncore,
        "saturn_edac_ue": edac_host,
        "card_cpu3_khz": card_freq,
    }


class Stack:
    def __init__(self):
        self.card_key = None
        self.host_key = None
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
            self.host_key = None
            sh(["rm", "-f", "/dev/hugepages/m152h*"], check=False)

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m152 C-c 2>/dev/null || true; sleep 0.6; "
            "tmux kill-session -t m152 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/m152*"
        )
        self.card_key = None

    def ensure_card(self, card_mode: str, wc_flush: bool = False):
        key = (card_mode, wc_flush)
        if self.card_key == key:
            return
        self.stop_card()
        ssh_card(
            "tmux kill-session -t refl 2>/dev/null || true; sleep 0.2; "
            "ip link set eth0 down 2>/dev/null || true; "
            "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
            'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
            "drv2=$(basename $(readlink -f /sys/bus/pci/devices/0002:01:00.2/driver) 2>/dev/null || echo none); "
            'if [ "$drv2" != "vfio-pci" ]; then sh /root/bench/dpdk_up.sh 0002:01:00.2; fi'
        )
        if card_mode == "card_echo":
            ready_tok = "card_echo ready"
            cmd = f"{CARD_BIN} -l 3 -a 0002:02:00.0 --file-prefix=m152 -- --mode card-echo"
            prep = ""
        elif card_mode == "direct":
            ready_tok = "direct ready"
            wf = " --wc-flush" if wc_flush else ""
            cmd = (
                f"{CARD_BIN} -l 3 -a 0002:02:00.0 -a 0000:06:00.1 -a 0000:06:00.2 "
                f"--file-prefix=m152 -- --mode direct --dma-threshold 1024{wf}"
            )
            prep = "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "
        else:
            raise ValueError(card_mode)

        ssh_card(
            f"{prep}"
            f'tmux new-session -d -s m152 "{cmd} > /tmp/m152_card.log 2>&1"; '
            f'for i in $(seq 1 50); do grep -q "MRV133_CARD {ready_tok}" /tmp/m152_card.log && break; sleep 0.2; done; '
            f'grep "MRV133_CARD {ready_tok}" /tmp/m152_card.log'
        )
        self.card_key = key

    def ensure_host(self, fix_stack_zero: bool, bypass_tx_pool: bool):
        key = (fix_stack_zero, bypass_tx_pool)
        if self.host_key == key and self.host_proc is not None and self.host_proc.poll() is None:
            return
        self.stop_host()
        cmd = [
            HOST_BIN, "-l", "40", "-a", "0000:99:02.0",
            "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m152h", "--",
            "--mode", "direct",
            "--dma-threshold", "1024",
        ]
        if fix_stack_zero:
            cmd.append("--fix-stack-zero")
        if bypass_tx_pool:
            cmd.append("--bypass-tx-pool")
        logf = open("/tmp/m152_host_run.log", "w")
        self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        logf.close()
        for _ in range(60):
            if self.host_proc.poll() is not None:
                raise RuntimeError(f"mrv152_host exited early:\n{pathlib.Path('/tmp/m152_host_run.log').read_text()}")
            txt = pathlib.Path("/tmp/m152_host_run.log").read_text()
            if "MRV133_HOST READY" in txt:
                time.sleep(0.15)
                self.host_key = key
                return
            time.sleep(0.1)
        raise RuntimeError(f"mrv152_host timeout:\n{pathlib.Path('/tmp/m152_host_run.log').read_text()}")

    def restore_card(self):
        self.stop_card()
        ssh_card(
            "/root/bench/inline_reflector --restore 2>&1 || true; "
            "rmmod pem_wc 2>/dev/null || true; "
            "dpdk-devbind.py -b rvu_nicpf 0002:02:00.0; "
            "sleep 0.5; ip link set eth0 up; "
            "ip addr replace 10.10.100.2/24 dev eth0; "
            "tmux new-session -d -s refl /root/bench/reflector-rs -l 3 -a 0002:01:00.2 --file-prefix=refl; "
            "sleep 1.0"
        )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    env = check_env()
    ENV_PATH.write_text(json.dumps(env, indent=2) + "\n")

    specs = [
        {"cfg": "card_echo_64_w1", "card_mode": "card_echo", "wc_flush": False, "host": None, "workload": "64", "size": 64},
        {"cfg": "direct_baseline_64_w1", "card_mode": "direct", "wc_flush": False, "host": (False, False), "workload": "64", "size": 64},
        {"cfg": "direct_host_clean_64_w1", "card_mode": "direct", "wc_flush": False, "host": (True, True), "workload": "64", "size": 64},
        {"cfg": "direct_wc_flush_clean_64_w1", "card_mode": "direct", "wc_flush": True, "host": (True, True), "workload": "64", "size": 64},
        {"cfg": "card_echo_256_w1", "card_mode": "card_echo", "wc_flush": False, "host": None, "workload": "256", "size": 256},
        {"cfg": "direct_baseline_256_w1", "card_mode": "direct", "wc_flush": False, "host": (False, False), "workload": "256", "size": 256},
        {"cfg": "direct_host_clean_256_w1", "card_mode": "direct", "wc_flush": False, "host": (True, True), "workload": "256", "size": 256},
        {"cfg": "direct_wc_flush_clean_256_w1", "card_mode": "direct", "wc_flush": True, "host": (True, True), "workload": "256", "size": 256},
    ]

    stack = Stack()
    records = []
    try:
        for rep in range(1, REPS + 1):
            for spec in specs:
                stack.ensure_card(spec["card_mode"], wc_flush=spec["wc_flush"])
                if spec["host"] is None:
                    stack.stop_host()
                else:
                    stack.ensure_host(spec["host"][0], spec["host"][1])

                tcp_cmd("10.10.0.2", 7529, "RESET")
                if spec["host"] is not None:
                    tcp_cmd("127.0.0.1", 7531, "RESET")

                seed = 0x1520_2026_1123 + rep * 100003 + (abs(hash(spec["cfg"])) & 0xFFFF)
                mcmd = (
                    f"{MERC_BIN} --mode closed --workload {spec['workload']} --window 1 "
                    f"--warmup {WARMUP} --rounds {ROUNDS} --seed {seed}"
                )
                res = ssh_merc(mcmd, timeout=90)
                card_st = tcp_cmd("10.10.0.2", 7529, "STATS")
                host_st = tcp_cmd("127.0.0.1", 7531, "STATS") if spec["host"] is not None else None

                client_st = None
                for line in res.stdout.splitlines():
                    if line.startswith("MRV133_CLIENT_JSON "):
                        client_st = json.loads(line[len("MRV133_CLIENT_JSON "):])
                        break
                assert client_st is not None, res.stdout

                checks = {
                    "c_bad": client_st["bad"] == 0,
                    "c_torn": client_st["torn"] == 0,
                    "c_lost": client_st["lost"] == 0,
                    "c_done": client_st["done_measured"] == ROUNDS,
                    "k_bad_wire": card_st["bad_wire"] == 0,
                    "k_bad_host": card_st["bad_host"] == 0,
                    "k_torn_bar4": card_st["torn_bar4"] == 0,
                    "k_imissed": card_st["eth_imissed"] == 0,
                    "k_ierrors": card_st["eth_ierrors"] == 0,
                    "h_ok": host_st is None or (host_st["bad"] == 0 and host_st["torn"] == 0),
                }
                verified = all(checks.values())
                assert verified, f"FAILED verify on {spec['cfg']} rep {rep}: checks={checks} card={card_st}"

                rec = {
                    "rep": rep,
                    "cfg": spec["cfg"],
                    "size": spec["size"],
                    "card_mode": spec["card_mode"],
                    "wc_flush": spec["wc_flush"],
                    "fix_stack_zero": spec["host"][0] if spec["host"] else None,
                    "bypass_tx_pool": spec["host"][1] if spec["host"] else None,
                    "client": client_st,
                    "card": card_st,
                    "host": host_st,
                    "verified": verified,
                }
                records.append(rec)
                hrtt = card_st["host_rtt_p50_us"] if spec["host"] else 0.0
                print(
                    f"rep {rep} | {spec['cfg']:<28} | tot_p50={client_st['tot_p50_us']:.3f} us "
                    f"| card_cntvct={hrtt:.3f} us | p99={client_st['tot_p99_us']:.3f} us | ok"
                )
    finally:
        print("Restoring card state...")
        stack.restore_card()

    RUNS_PATH.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    print(f"Wrote {len(records)} verified runs to {RUNS_PATH}")

    ping_out = ssh_card("ping -c 3 -W 1 10.10.100.1 | tail -n 2").stdout.strip()
    print(f"eth0 ping check:\n{ping_out}")
    print("Waiting 20 s for EDAC / SEI-UER recheck...")
    time.sleep(20)
    card_edac = ssh_card(
        "echo ue=$(grep . /sys/devices/system/edac/*/*/ue_count | awk -F: '{s+=$2} END{print s+0}'); "
        "echo sei=$(dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || true); "
        "echo pem_wc=$(lsmod | grep -c '^pem_wc ' || true); "
        "echo refl=$(pgrep -c reflector-rs || true)"
    ).stdout.strip()
    host_edac = [
        int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count"))
    ]
    print(f"Card post-restore:\n{card_edac}\nHost EDAC ue_count={host_edac}")


if __name__ == "__main__":
    main()
