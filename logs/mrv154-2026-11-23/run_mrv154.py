#!/usr/bin/env python3
"""MRV-154 full n=3 interleaved verified experiment harness.

Question: Why is dual-machine 100GbE DirectPush card-internal CNTVCT RTT
1.30 us vs 0.89 us in single-host bare DirectPush (+0.41 us)?

Fixed baseline:
  - mercury (Xeon 6438M, NUMA 0 cpu0, ConnectX-5 mlx5_2 / ens6f1np1, non-root IBV_QPT_UD RoCEv2)
  - 3m 100GbE passive DAC (100000Mb/s Full, No FEC)
  - CN106 eth0 (0002:02:00.0, RPM0+NIX0, 1 card core -l 3 @ 2.00 GHz, pem_wc loaded)
  - saturn (Xeon 6438M, NUMA 1 cpu40 @ 3.90 GHz performance, Socket-1 uncore 2.20 GHz,
            IOMMU on, mrv154_host --fix-stack-zero --bypass-tx-pool, batch = 1)
"""

import json
import pathlib
import socket
import subprocess
import time
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv154/logs/mrv154-2026-11-23")
RUNS_PATH = OUT_DIR / "runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"

HOST_BIN = "/tmp/mrv154-target/release/mrv154_host"
CARD_BIN = "/root/bench/mrv154_card"
MERC_BIN = "/tmp/mrv154/mrv133_client"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]

REPS = 3
WARMUP = 5_000
ROUNDS_W1 = 30_000
ROUNDS_W32 = 100_000
ROUNDS_SYNTH = 30_000


def sh(cmd, check=True, timeout=120):
    r = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed ({r.returncode}): {cmd}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    return r


def ssh_card(script, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def ssh_merc(script, check=True, timeout=60):
    return sh(MERC_SSH + [script], check=check, timeout=timeout)


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
    card_info = ssh_card(
        "echo freq=$(cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq); "
        "echo ue=$(grep . /sys/devices/system/edac/*/*/ue_count | awk -F: '{s+=$2} END{print s+0}'); "
        "echo sei=$(dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || true)"
    ).stdout.strip().splitlines()
    kv = dict(line.split("=", 1) for line in card_info if "=" in line)
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "saturn_cpu40_gov": gov,
        "saturn_cpu40_khz": cur,
        "saturn_uncore_khz": uncore,
        "saturn_edac_ue": edac_host,
        "card_cpu3_khz": int(kv["freq"]),
        "card_edac_ue": int(kv["ue"]),
        "card_sei_uer": int(kv["sei"]),
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
            sh(["rm", "-f", "/dev/hugepages/m154h*"], check=False)

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m154 C-c 2>/dev/null || true; sleep 0.6; "
            "tmux kill-session -t m154 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/m154*"
        )
        self.card_key = None

    def ensure_card(self, card_mode: str, extra_args: tuple = ()):
        key = (card_mode, extra_args)
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
        extra_str = " ".join(extra_args)
        if card_mode == "card_echo":
            ready_tok = "card_echo ready"
            cmd = f"{CARD_BIN} -l 3 -a 0002:02:00.0 --file-prefix=m154 -- --mode card-echo {extra_str}"
            prep = ""
        elif card_mode == "direct":
            ready_tok = "direct ready"
            cmd = (
                f"{CARD_BIN} -l 3 -a 0002:02:00.0 -a 0000:06:00.1 -a 0000:06:00.2 "
                f"--file-prefix=m154 -- --mode direct --dma-threshold 1024 {extra_str}"
            )
            prep = "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "
        else:
            raise ValueError(card_mode)

        ssh_card(
            f"{prep}"
            f'tmux new-session -d -s m154 "{cmd} > /tmp/m154_card.log 2>&1"; '
            f'for i in $(seq 1 50); do grep -q "MRV133_CARD {ready_tok}" /tmp/m154_card.log && break; sleep 0.2; done; '
            f'grep "MRV133_CARD {ready_tok}" /tmp/m154_card.log'
        )
        self.card_key = key

    def ensure_host(self, extra_args: tuple = ()):
        if self.host_key == extra_args and self.host_proc is not None and self.host_proc.poll() is None:
            return
        self.stop_host()
        cmd = [
            HOST_BIN, "-l", "40", "-a", "0000:99:02.0",
            "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m154h", "--",
            "--mode", "direct",
            "--dma-threshold", "1024",
            "--fix-stack-zero",
            "--bypass-tx-pool",
            *extra_args,
        ]
        logf = open("/tmp/m154_host_run.log", "w")
        self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        logf.close()
        for _ in range(60):
            if self.host_proc.poll() is not None:
                raise RuntimeError(f"mrv154_host exited early:\n{pathlib.Path('/tmp/m154_host_run.log').read_text()}")
            txt = pathlib.Path("/tmp/m154_host_run.log").read_text()
            if "MRV133_HOST READY" in txt:
                time.sleep(0.15)
                self.host_key = extra_args
                return
            time.sleep(0.1)
        raise RuntimeError(f"mrv154_host timeout:\n{pathlib.Path('/tmp/m154_host_run.log').read_text()}")

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


def run_merc(workload: str, window: int, warmup: int, rounds: int, seed: int):
    mcmd = (
        f"{MERC_BIN} --mode closed --workload {workload} --window {window} "
        f"--warmup {warmup} --rounds {rounds} --seed {seed}"
    )
    res = ssh_merc(mcmd, timeout=90)
    for line in res.stdout.splitlines():
        if line.startswith("MRV133_CLIENT_JSON "):
            return json.loads(line[len("MRV133_CLIENT_JSON "):])
    raise RuntimeError(f"Missing MRV133_CLIENT_JSON:\n{res.stdout}\n{res.stderr}")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    env = {"before": read_clocks_edac()}

    # Primary dual-machine W=1 single-variable ablation specs (interleaved across 3 reps)
    w1_specs = [
        {"cfg": "card_echo_64_w1", "group": "dual_w1", "size": 64, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "card_echo", "card_args": (), "host_args": None},
        {"cfg": "direct_baseline_64_w1", "group": "dual_w1", "size": 64, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "direct", "card_args": (), "host_args": ()},
        {"cfg": "direct_defer_free_64_w1", "group": "dual_w1", "size": 64, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "direct", "card_args": ("--defer-mbuf-free",), "host_args": ()},
        {"cfg": "direct_bar4_first_64_w1", "group": "dual_w1", "size": 64, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "direct", "card_args": ("--bar4-first", "--bar4-first-budget-ns", "2500"), "host_args": ()},
        {"cfg": "direct_bar4_first_defer_64_w1", "group": "dual_w1", "size": 64, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "direct", "card_args": ("--bar4-first", "--bar4-first-budget-ns", "2500", "--defer-mbuf-free"), "host_args": ()},
        {"cfg": "direct_skip_eth_64_w1", "group": "dual_w1", "size": 64, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "direct", "card_args": ("--skip-eth-while-inflight",), "host_args": ()},
        {"cfg": "direct_stage_profile_64_w1", "group": "stage_profile", "size": 64, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "direct", "card_args": ("--stage-timing",), "host_args": ("--host-timing",)},
        {"cfg": "card_echo_256_w1", "group": "dual_w1", "size": 256, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "card_echo", "card_args": (), "host_args": None},
        {"cfg": "direct_baseline_256_w1", "group": "dual_w1", "size": 256, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "direct", "card_args": (), "host_args": ()},
        {"cfg": "direct_defer_free_256_w1", "group": "dual_w1", "size": 256, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "direct", "card_args": ("--defer-mbuf-free",), "host_args": ()},
        {"cfg": "direct_bar4_first_256_w1", "group": "dual_w1", "size": 256, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "direct", "card_args": ("--bar4-first", "--bar4-first-budget-ns", "2500"), "host_args": ()},
        {"cfg": "direct_bar4_first_defer_256_w1", "group": "dual_w1", "size": 256, "window": 1, "rounds": ROUNDS_W1,
         "card_mode": "direct", "card_args": ("--bar4-first", "--bar4-first-budget-ns", "2500", "--defer-mbuf-free"), "host_args": ()},
    ]

    # W=32 high-concurrency non-regression specs
    w32_specs = [
        {"cfg": "direct_baseline_64_w32", "group": "dual_w32", "size": 64, "window": 32, "rounds": ROUNDS_W32,
         "card_mode": "direct", "card_args": (), "host_args": ()},
        {"cfg": "direct_bar4_first_64_w32", "group": "dual_w32", "size": 64, "window": 32, "rounds": ROUNDS_W32,
         "card_mode": "direct", "card_args": ("--bar4-first", "--bar4-first-budget-ns", "2500"), "host_args": ()},
        {"cfg": "direct_baseline_256_w32", "group": "dual_w32", "size": 256, "window": 32, "rounds": ROUNDS_W32,
         "card_mode": "direct", "card_args": (), "host_args": ()},
        {"cfg": "direct_bar4_first_256_w32", "group": "dual_w32", "size": 256, "window": 32, "rounds": ROUNDS_W32,
         "card_mode": "direct", "card_args": ("--bar4-first", "--bar4-first-budget-ns", "2500"), "host_args": ()},
    ]

    # Synthetic card-initiated gap + eth_rx injection sweep
    synth_specs = [
        {"cfg": "synth_64_gap0", "size": 64, "gap_ns": 0, "eth_poll": 0, "mbuf_churn": 0},
        {"cfg": "synth_64_gap1000", "size": 64, "gap_ns": 1000, "eth_poll": 0, "mbuf_churn": 0},
        {"cfg": "synth_64_gap3000", "size": 64, "gap_ns": 3000, "eth_poll": 0, "mbuf_churn": 0},
        {"cfg": "synth_64_gap6000", "size": 64, "gap_ns": 6000, "eth_poll": 0, "mbuf_churn": 0},
        {"cfg": "synth_64_gap6000_mbufchurn", "size": 64, "gap_ns": 6000, "eth_poll": 0, "mbuf_churn": 1},
        {"cfg": "synth_64_gap0_ethpoll", "size": 64, "gap_ns": 0, "eth_poll": 1, "mbuf_churn": 0},
        {"cfg": "synth_64_gap6000_ethpoll", "size": 64, "gap_ns": 6000, "eth_poll": 1, "mbuf_churn": 0},
        {"cfg": "synth_256_gap0", "size": 256, "gap_ns": 0, "eth_poll": 0, "mbuf_churn": 0},
        {"cfg": "synth_256_gap6000", "size": 256, "gap_ns": 6000, "eth_poll": 0, "mbuf_churn": 0},
    ]

    stack = Stack()
    records = []
    active_cpu40_khz = []
    try:
        for rep in range(1, REPS + 1):
            print(f"\n================ REP {rep}/{REPS} ================")

            # Group 1: Bare CN106 hardware poll microbenchmark
            stack.ensure_card("direct", ())
            stack.ensure_host(())
            active_cpu40_khz.append(
                int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
            )
            mb = tcp_cmd("10.10.0.2", 7529, "ETH_RX_BENCH")
            assert mb["eth_got"] == 0 and mb["mbuf_alloc_ok"] == mb["iters"], mb
            records.append({
                "rep": rep,
                "group": "bare_microbench",
                "cfg": "cn106_bare_poll_bench",
                "bench": mb,
                "verified": True,
            })
            print(
                f"rep {rep} | bare_bench | eth_empty={mb['eth_rx_empty_bare_ns']:.2f} ns "
                f"| bar4_empty={mb['bar4_poll_empty_bare_ns']:.2f} ns "
                f"| mbuf={mb['mbuf_alloc_free_bare_ns']:.2f} ns | rdtsc_al={mb['rdtsc_after_loads_bare_ns']:.2f} ns"
            )

            # Group 4 (interleaved each rep): Synthetic card-initiated gap + eth_rx injection
            for s in synth_specs:
                tcp_cmd("127.0.0.1", 7531, "RESET")
                r = tcp_cmd(
                    "10.10.0.2",
                    7529,
                    f"SYNTH {s['size']} {WARMUP} {ROUNDS_SYNTH} {s['gap_ns']} {s['eth_poll']} {s['mbuf_churn']}",
                    timeout=45.0,
                )
                h = tcp_cmd("127.0.0.1", 7531, "STATS")
                verified = (
                    r.get("bad") == 0
                    and r.get("torn") == 0
                    and r.get("lost") == 0
                    and r.get("cnt") == ROUNDS_SYNTH
                    and h.get("bad") == 0
                    and h.get("rx_rpcs") == (WARMUP + ROUNDS_SYNTH)
                    and h.get("tx_rpcs") == (WARMUP + ROUNDS_SYNTH)
                )
                assert verified, f"FAILED synth {s['cfg']} rep {rep}: r={r} h={h}"
                records.append({
                    "rep": rep,
                    "group": "synth_gap",
                    "cfg": s["cfg"],
                    "size": s["size"],
                    "gap_ns": s["gap_ns"],
                    "eth_poll": bool(s["eth_poll"]),
                    "mbuf_churn": bool(s["mbuf_churn"]),
                    "synth": r,
                    "host": h,
                    "verified": verified,
                })
                print(
                    f"rep {rep} | {s['cfg']:<28} | p50={r['p50_us']:.3f} p10={r['p10_us']:.3f} p99={r['p99_us']:.3f} "
                    f"| eth_polls={r['eth_polls_avg']:.2f} | ok"
                )

            # Group 2 & 3: Dual-machine 100GbE W=1 and W=32 ablations
            for idx, spec in enumerate(w1_specs + w32_specs):
                stack.ensure_card(spec["card_mode"], spec["card_args"])
                if spec["host_args"] is None:
                    stack.stop_host()
                else:
                    stack.ensure_host(spec["host_args"])

                tcp_cmd("10.10.0.2", 7529, "RESET")
                if spec["host_args"] is not None:
                    tcp_cmd("127.0.0.1", 7531, "RESET")

                seed = 0x1540_2026_1123 + rep * 100003 + idx * 97
                c = run_merc(str(spec["size"]), spec["window"], WARMUP, spec["rounds"], seed)
                k = tcp_cmd("10.10.0.2", 7529, "STATS")
                h = tcp_cmd("127.0.0.1", 7531, "STATS") if spec["host_args"] is not None else None
                cur_cpu40 = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())

                checks = {
                    "c_bad": c["bad"] == 0,
                    "c_torn": c["torn"] == 0,
                    "c_lost": c["lost"] == 0,
                    "c_done": c["done_measured"] == spec["rounds"],
                    "k_bad_wire": k["bad_wire"] == 0,
                    "k_bad_host": k["bad_host"] == 0,
                    "k_torn_bar4": k["torn_bar4"] == 0,
                    "k_imissed": k["eth_imissed"] == 0,
                    "k_ierrors": k["eth_ierrors"] == 0,
                    "k_oerrors": k["eth_oerrors"] == 0,
                    "k_rx_eq_tx": k["eth_ipackets"] == k["eth_opackets"] == (WARMUP + spec["rounds"]),
                    "h_ok": h is None or (h["bad"] == 0 and h["torn"] == 0 and h["page_faults"] == 0),
                }
                verified = all(checks.values())
                assert verified, f"FAILED verify {spec['cfg']} rep {rep}: checks={checks} card={k} host={h}"

                records.append({
                    "rep": rep,
                    "group": spec["group"],
                    "cfg": spec["cfg"],
                    "size": spec["size"],
                    "window": spec["window"],
                    "batch": 1,
                    "warmup": WARMUP,
                    "rounds": spec["rounds"],
                    "card_mode": spec["card_mode"],
                    "card_args": list(spec["card_args"]),
                    "host_args": list(spec["host_args"]) if spec["host_args"] is not None else None,
                    "saturn_cpu40_khz": cur_cpu40,
                    "client": c,
                    "card": k,
                    "host": h,
                    "verified": verified,
                })
                hrtt = k["host_rtt_p50_us"] if spec["host_args"] is not None else 0.0
                ep = k["stages"]["lite_inflight_eth_empty"] / max(k["rpcs_in"], 1)
                print(
                    f"rep {rep} | {spec['cfg']:<30} | rate={c['achieved_mrps']:.3f} M/s | merc_p50={c['tot_p50_us']:.3f} "
                    f"p99={c['tot_p99_us']:.3f} | card_p50={hrtt:.3f} p99={k['host_rtt_p99_us']:.3f} | eth_empty/rpc={ep:.3f} | ok"
                )
    finally:
        print("\nRestoring CN106 card to default state...")
        stack.restore_card()

    RUNS_PATH.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    print(f"Wrote {len(records)} verified runs to {RUNS_PATH}")

    eth0_link = ssh_card("ip -br link show eth0; ethtool eth0 | grep -E 'Speed|Link detected'").stdout.strip()
    ping_out = ssh_card("ping -c 3 -W 1 10.10.100.1 | tail -n 2").stdout.strip()
    print(f"eth0 link:\n{eth0_link}\neth0 ping:\n{ping_out}")
    print("Waiting 20 s for post-restore EDAC / SEI-UER recheck...")
    time.sleep(20)
    post_card = ssh_card(
        "echo ue=$(grep . /sys/devices/system/edac/*/*/ue_count | awk -F: '{s+=$2} END{print s+0}'); "
        "echo sei=$(dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || true); "
        "echo pem_wc=$(lsmod | grep -c '^pem_wc ' || true); "
        "echo refl=$(pgrep -c reflector-rs || true)"
    ).stdout.strip()
    after_env = read_clocks_edac()
    env["active_cpu40_khz"] = active_cpu40_khz
    env["after"] = after_env
    env["restore"] = {
        "eth0_link": eth0_link,
        "ping_mercury": ping_out,
        "post_20s_card": post_card,
    }
    ENV_PATH.write_text(json.dumps(env, indent=2) + "\n")
    print(f"Post-restore 20s card check:\n{post_card}")
    print(f"Post-restore host EDAC ue_count={after_env['saturn_edac_ue']}")


if __name__ == "__main__":
    main()
