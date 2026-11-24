#!/usr/bin/env python3
"""MRV-161 full n=3 workload-outer interleaved & counterbalanced experiment harness.

Question: Can coherent CQE peek under CN106 NIX0 `cq_ctx->caching = 1` replace
unconditional 582 ns `LDADDA NIX_LF_CQ_OP_STATUS` empty polling to achieve BOTH
`< 0.95 us` W=1 card-internal RTT AND zero blind-window head-of-line blocking across
W in {1, 2, 4, 8, 32} and open-loop Poisson lambda in {0.5, 1.0, 1.5} Mreq/s?

Fixed baseline (MRV-82 single-variable discipline):
  - mercury (Xeon 6438M, NUMA 0 cpu0, ConnectX-5 mlx5_2 IBV_QPT_UD RoCEv2,
    fixed mrv161_client with post-sleep 64 MiB DRAM/LLC ramp keeping core ~3.90 GHz
    and Sapphire Rapids Uncore hot; card_echo ~4.27 us)
  - 3 m 100GbE passive DAC (100000Mb/s Full, No FEC)
  - CN106 eth0 (`0002:02:00.0,scalar_enable=1` across ALL arms so Vector-Neon vs Scalar
    is held strictly constant; RPM0+NIX0, 1C -l 3 @ 2.00 GHz, pem_wc loaded)
  - saturn (Xeon 6438M, NUMA 1 cpu40 @ 3.90 GHz performance, Socket-0/1 uncore 2.20 GHz,
    IOMMU on, mrv161_host --fix-stack-zero --bypass-tx-pool)

Single variable changed (card-side eth0 RX polling policy), run adjacently per
workload in rotated counterbalanced order across reps:
  Required tuple:
    1. default    : unmodified rte_eth_rx_burst() (Scalar No Offload, unconditional LDADDA)
    2. bar4_first : --bar4-first --bar4-first-budget-ns 2500 (with OP_DMA reset fix)
    3. cqe_peek   : --cqe-peek (ldar cqe_type != 0 -> return 0 if empty; on hit LDADDA + clear w0/w9 before cq_door)
  Exploratory bonus arm (adjacent, clearly tagged exploratory=True):
    4. cqe_direct : --cqe-direct (ldar w0 & w9, zero LDADDA even on hit)
"""

import json
import pathlib
import socket
import subprocess
import time
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv161/logs/mrv161-2026-11-24")
RUNS_PATH = OUT_DIR / "runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"

HOST_BIN = "/tmp/mrv161-target/release/mrv161_host"
CARD_BIN = "/root/bench/mrv161_card"
MERC_BIN = "/tmp/mrv161/mrv161_client"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]

ETH_DEV_SCALAR = "0002:02:00.0,scalar_enable=1"
ETH_DEV_VEC = "0002:02:00.0"

REPS = 3
WARMUP = 5_000
ROUNDS_LOW = 30_000
ROUNDS_W32 = 100_000
ROUNDS_POISSON = 40_000

POLICY_ARGS = {
    "default": (),
    "bar4_first": ("--bar4-first", "--bar4-first-budget-ns", "2500"),
    "cqe_peek": ("--cqe-peek",),
    "cqe_direct": ("--cqe-direct",),
}

# Latin-square rotations of the 3 required arms; exploratory cqe_direct runs adjacent after them.
REQUIRED_ROTATIONS = [
    ["default", "bar4_first", "cqe_peek"],
    ["bar4_first", "cqe_peek", "default"],
    ["cqe_peek", "default", "bar4_first"],
]


def sh(cmd, check=True, timeout=120):
    r = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(
            f"cmd failed ({r.returncode}): {cmd}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
    return r


def ssh_card(script, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def ssh_merc(script, check=True, timeout=90):
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
        for p in sorted(
            pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz")
        )
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
            sh(["rm", "-f", "/dev/hugepages/m161h*"], check=False)

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m161 C-c 2>/dev/null || true; sleep 0.4; "
            "tmux kill-session -t m161 2>/dev/null || true; sleep 0.15; rm -f /dev/hugepages/m161*"
        )
        self.card_key = None

    def ensure_card(self, card_mode: str, extra_args: tuple = (), eth_dev: str = ETH_DEV_SCALAR):
        key = (card_mode, extra_args, eth_dev)
        if self.card_key == key:
            return
        self.stop_card()
        ssh_card(
            "tmux kill-session -t refl 2>/dev/null || true; "
            "ip link set eth0 down 2>/dev/null || true; "
            "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
            'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
            "drv2=$(basename $(readlink -f /sys/bus/pci/devices/0002:01:00.2/driver) 2>/dev/null || echo none); "
            'if [ "$drv2" != "vfio-pci" ]; then sh /root/bench/dpdk_up.sh 0002:01:00.2; fi'
        )
        extra_str = " ".join(extra_args)
        if card_mode == "card_echo":
            ready_tok = "card_echo ready"
            cmd = f"{CARD_BIN} -l 3 -a {eth_dev} --file-prefix=m161 -- --mode card-echo {extra_str}"
            prep = ""
        elif card_mode == "direct":
            ready_tok = "direct ready"
            cmd = (
                f"{CARD_BIN} -l 3 -a {eth_dev} -a 0000:06:00.1 -a 0000:06:00.2 "
                f"--file-prefix=m161 -- --mode direct --dma-threshold 1024 {extra_str}"
            )
            prep = "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "
        else:
            raise ValueError(card_mode)

        ssh_card(
            f"{prep}"
            f'tmux new-session -d -s m161 "{cmd} > /tmp/m161_card.log 2>&1"; '
            f'for i in $(seq 1 50); do grep -q "MRV133_CARD {ready_tok}" /tmp/m161_card.log && break; sleep 0.15; done; '
            f'grep "MRV133_CARD {ready_tok}" /tmp/m161_card.log'
        )
        self.card_key = key

    def ensure_host(self):
        if self.host_proc is not None and self.host_proc.poll() is None:
            return
        self.stop_host()
        cmd = [
            HOST_BIN, "-l", "40", "-a", "0000:99:02.0",
            "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m161h", "--",
            "--mode", "direct",
            "--dma-threshold", "1024",
            "--fix-stack-zero",
            "--bypass-tx-pool",
        ]
        with open("/tmp/m161_host_run.log", "w") as logf:
            self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        for _ in range(60):
            if self.host_proc.poll() is not None:
                raise RuntimeError(
                    f"mrv161_host exited early:\n{pathlib.Path('/tmp/m161_host_run.log').read_text()}"
                )
            txt = pathlib.Path("/tmp/m161_host_run.log").read_text()
            if "MRV133_HOST READY" in txt:
                time.sleep(0.1)
                return
            time.sleep(0.1)
        raise RuntimeError(f"mrv161_host timeout:\n{pathlib.Path('/tmp/m161_host_run.log').read_text()}")

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


def run_merc(mode: str, workload: str, window: int, warmup: int, rounds: int, seed: int, rate_krps=None):
    mcmd = (
        f"{MERC_BIN} --mode {mode} --workload {workload} --window {window} "
        f"--warmup {warmup} --rounds {rounds} --seed {seed}"
    )
    if rate_krps is not None:
        mcmd += f" --rate-krps {rate_krps:.1f}"
    res = ssh_merc(mcmd, timeout=90)
    for line in res.stdout.splitlines():
        if line.startswith("MRV133_CLIENT_JSON "):
            return json.loads(line[len("MRV133_CLIENT_JSON "):])
    raise RuntimeError(f"Missing MRV133_CLIENT_JSON:\n{res.stdout}\n{res.stderr}")


def verify_run(c, k, h, warmup, rounds):
    xs = k.get("eth_xstats", {})
    exp_pkts = c["tx_pkts_total"]
    checks = {
        "c_verified": c["verified"] is True,
        "c_bad": c["bad"] == 0,
        "c_torn": c["torn"] == 0,
        "c_lost": c["lost"] == 0,
        "c_send_err": c["send_err"] == 0,
        "c_done": c["done_measured"] == rounds,
        "c_hot_start": c["cpu_khz_start"] >= 3_400_000,
        "k_bad_wire": k["bad_wire"] == 0,
        "k_bad_host": k["bad_host"] == 0,
        "k_torn_bar4": k["torn_bar4"] == 0,
        "k_dma_failed": k["dma_failed"] == 0,
        "k_eth_tx_drops": k["eth_tx_drops"] == 0,
        "k_imissed": k["eth_imissed"] == 0,
        "k_ierrors": k["eth_ierrors"] == 0,
        "k_oerrors": k["eth_oerrors"] == 0,
        "k_rx_nombuf": k["eth_rx_nombuf"] == 0,
        "k_rx_eq_tx": k["eth_ipackets"] == k["eth_opackets"] == exp_pkts and c["rx_pkts_total"] == exp_pkts,
        "k_xs_clean": (
            xs.get("rx_err", 0) == 0
            and xs.get("rx_fcs", 0) == 0
            and xs.get("rx_drop", 0) == 0
            and xs.get("tx_drop", 0) == 0
        ),
        "h_ok": h is None or (
            h["bad"] == 0
            and h["torn"] == 0
            and h["page_faults"] == 0
            and h["rx_rpcs"] == (warmup + rounds)
            and h["tx_rpcs"] == (warmup + rounds)
        ),
    }
    return all(checks.values()), checks


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if RUNS_PATH.exists():
        RUNS_PATH.unlink()
    env = {
        "eth_dev_main": ETH_DEV_SCALAR,
        "eth_dev_vec_control": ETH_DEV_VEC,
        "wc_flush_mode": "none",
        "before": read_clocks_edac(),
    }

    stack = Stack()
    records = []
    active_cpu40_khz = []

    def append_rec(rec):
        records.append(rec)
        with open(RUNS_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")

    workloads = [
        # Group 3: 64 B closed-loop W in {1, 2, 4, 8, 32}
        ("dual_closed_64", "closed", 64, 1, None, WARMUP, ROUNDS_LOW),
        ("dual_closed_64", "closed", 64, 2, None, WARMUP, ROUNDS_LOW),
        ("dual_closed_64", "closed", 64, 4, None, WARMUP, ROUNDS_LOW),
        ("dual_closed_64", "closed", 64, 8, None, WARMUP, ROUNDS_LOW),
        ("dual_closed_64", "closed", 64, 32, None, WARMUP, ROUNDS_W32),
        # Group 4: 256 B closed-loop control W in {1, 4, 32}
        ("dual_closed_256", "closed", 256, 1, None, WARMUP, ROUNDS_LOW),
        ("dual_closed_256", "closed", 256, 4, None, WARMUP, ROUNDS_LOW),
        ("dual_closed_256", "closed", 256, 32, None, WARMUP, ROUNDS_W32),
        # Group 5: 64 B open-loop Poisson lambda in {0.5, 1.0, 1.5} Mreq/s
        ("dual_poisson_64", "poisson", 64, 256, 500.0, WARMUP, ROUNDS_POISSON),
        ("dual_poisson_64", "poisson", 64, 256, 1000.0, WARMUP, ROUNDS_POISSON),
        ("dual_poisson_64", "poisson", 64, 256, 1500.0, WARMUP, ROUNDS_POISSON),
    ]

    try:
        for rep in range(1, REPS + 1):
            print(f"\n==================== REP {rep}/{REPS} ====================", flush=True)

            # -----------------------------------------------------------------
            # Group 1: Hardware mechanism probe (empty-queue inspect + bare
            # empty-poll microbench + live 15k-pkt CQE coherency gate)
            # -----------------------------------------------------------------
            stack.ensure_card("direct", (), ETH_DEV_SCALAR)
            stack.ensure_host()
            active_cpu40_khz.append(
                int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
            )
            probe = tcp_cmd("10.10.0.2", 7529, "CQE_PROBE")
            assert probe["rx_burst_mode"].startswith("Scalar"), probe
            assert probe["rxq"]["init_nonzero_w0"] == 0 and probe["rxq"]["init_nonzero_w9"] == 0, probe
            assert probe["eth_got"] == 0 and probe["peek_got"] == 0 and probe["direct_got"] == 0 and probe["w0w9_nz"] == 0, probe
            assert probe["mbuf_alloc_ok"] == probe["iters"], probe
            append_rec({
                "rep": rep,
                "group": "hw_probe",
                "cfg": "cqe_probe_bare",
                "eth_dev": ETH_DEV_SCALAR,
                "probe": probe,
                "verified": True,
            })
            print(
                f"rep {rep} | cqe_probe_bare | mode='{probe['rx_burst_mode']}' qmask={probe['rxq']['qmask']} "
                f"| ldadda={probe['eth_rx_empty_bare_ns']:.2f} ns | cqe_peek={probe['cqe_peek_empty_bare_ns']:.2f} ns "
                f"| cqe_direct={probe['cqe_direct_empty_bare_ns']:.2f} ns | w0w9={probe['cqe_peek_w0w9_forced_ns']:.2f} ns "
                f"| bar4={probe['bar4_poll_empty_bare_ns']:.2f} ns",
                flush=True,
            )

            # Live hardware gate: --cqe-peek --cqe-peek-diag on 15,000 packets (3.66x 4096-entry CQ wrap)
            stack.ensure_card("card_echo", ("--cqe-peek", "--cqe-peek-diag"), ETH_DEV_SCALAR)
            stack.stop_host()
            tcp_cmd("10.10.0.2", 7529, "RESET")
            c = run_merc("closed", "64", 1, 5_000, 10_000, 0x1610_0000 + rep * 1009 + 1)
            k = tcp_cmd("10.10.0.2", 7529, "STATS")
            ok, checks = verify_run(c, k, None, 5_000, 10_000)
            cp = k["cqe_peek"]
            gate_ok = (
                ok
                and cp["peek_empty"] > 0
                and cp["peek_hit"] == 15_000
                and cp["ldadda_calls"] == 15_000
                and cp["peek_hit_ldadda_zero"] == 0
                and cp["w9_zero_on_w0_hit"] == 0
                and cp["w2_zero_on_w0_hit"] == 0
                and cp["cqe_type_rx"] == 15_000
                and cp["cqe_type_other"] == 0
            )
            assert gate_ok, f"FAILED hw_gate rep {rep}: checks={checks} cqe_peek={cp}"
            append_rec({
                "rep": rep,
                "group": "hw_probe",
                "cfg": "hw_gate_cqe_peek_diag_64_w1",
                "policy": "cqe_peek_diag",
                "eth_dev": ETH_DEV_SCALAR,
                "size": 64,
                "window": 1,
                "warmup": 5_000,
                "rounds": 10_000,
                "client": c,
                "card": k,
                "host": None,
                "verified": True,
            })
            print(
                f"rep {rep} | hw_gate_cqe_peek_diag | empty_skips={cp['peek_empty']} hits={cp['peek_hit']} "
                f"ldadda={cp['ldadda_calls']} ({cp['ldadda_mean_ns']:.1f} ns) ld0={cp['peek_hit_ldadda_zero']} "
                f"w9_0={cp['w9_zero_on_w0_hit']} ctype_rx={cp['cqe_type_rx']} | merc_p50={c['tot_p50_us']:.3f} | ok",
                flush=True,
            )

            # -----------------------------------------------------------------
            # Group 2: Hot-state card_echo baseline (default 64B/256B, cqe_peek 64B, cqe_direct 64B)
            # -----------------------------------------------------------------
            echo_specs = [
                ("card_echo_default_64_w1", "default", (), 64),
                ("card_echo_default_256_w1", "default", (), 256),
                ("card_echo_cqe_peek_64_w1", "cqe_peek", ("--cqe-peek",), 64),
                ("card_echo_cqe_direct_64_w1", "cqe_direct", ("--cqe-direct",), 64),
            ]
            for eidx, (cfg_name, pol, cargs, sz) in enumerate(echo_specs):
                stack.ensure_card("card_echo", cargs, ETH_DEV_SCALAR)
                stack.stop_host()
                tcp_cmd("10.10.0.2", 7529, "RESET")
                seed = 0x1611_0000 + rep * 1009 + sz
                c = run_merc("closed", str(sz), 1, WARMUP, ROUNDS_LOW, seed)
                k = tcp_cmd("10.10.0.2", 7529, "STATS")
                ok, checks = verify_run(c, k, None, WARMUP, ROUNDS_LOW)
                assert ok, f"FAILED {cfg_name} rep {rep}: checks={checks} c={c} k={k}"
                append_rec({
                    "rep": rep,
                    "group": "card_echo",
                    "cfg": cfg_name,
                    "policy": pol,
                    "exploratory": pol == "cqe_direct",
                    "seed": seed,
                    "eth_dev": ETH_DEV_SCALAR,
                    "drive_mode": "closed",
                    "size": sz,
                    "window": 1,
                    "warmup": WARMUP,
                    "rounds": ROUNDS_LOW,
                    "client": c,
                    "card": k,
                    "host": None,
                    "verified": True,
                })
                print(
                    f"rep {rep} | {cfg_name:<28} | cpu={c['cpu_khz_start']//1000}->{c['cpu_khz_end']//1000}MHz "
                    f"| merc_p50={c['tot_p50_us']:.3f} p99={c['tot_p99_us']:.3f} mean={c['tot_mean_us']:.3f} | ok",
                    flush=True,
                )

            # -----------------------------------------------------------------
            # Groups 3, 4, 5: Workload-outer, policy-inner interleaved sweep.
            # Each workload runs the required (default / bar4_first / cqe_peek)
            # tuple adjacently in rotated counterbalanced order, followed
            # immediately by the clearly labeled exploratory `cqe_direct` arm.
            # -----------------------------------------------------------------
            for widx, (grp, dmode, sz, win, rate_krps, wu, rds) in enumerate(workloads):
                wtag = f"w{win}" if dmode == "closed" else f"p{int(rate_krps)}k"
                req_order = REQUIRED_ROTATIONS[(rep - 1 + widx) % len(REQUIRED_ROTATIONS)]
                pol_order = req_order + ["cqe_direct"]

                for pidx, pol in enumerate(pol_order):
                    cargs = POLICY_ARGS[pol]
                    cfg_name = f"direct_{pol}_{sz}_{wtag}"
                    stack.ensure_card("direct", cargs, ETH_DEV_SCALAR)
                    stack.ensure_host()

                    tcp_cmd("10.10.0.2", 7529, "RESET")
                    tcp_cmd("127.0.0.1", 7531, "RESET")
                    seed = 0x1612_0000 + rep * 100003 + widx * 1009
                    c = run_merc(dmode, str(sz), win, wu, rds, seed, rate_krps=rate_krps)
                    k = tcp_cmd("10.10.0.2", 7529, "STATS")
                    h = tcp_cmd("127.0.0.1", 7531, "STATS")
                    cur_cpu40 = int(
                        pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip()
                    )
                    ok, checks = verify_run(c, k, h, wu, rds)
                    assert ok, f"FAILED {cfg_name} rep {rep}: checks={checks}\nc={c}\nk={k}\nh={h}"

                    append_rec({
                        "rep": rep,
                        "group": grp,
                        "cfg": cfg_name,
                        "policy": pol,
                        "exploratory": pol == "cqe_direct",
                        "policy_order_in_workload": pol_order,
                        "seed": seed,
                        "eth_dev": ETH_DEV_SCALAR,
                        "drive_mode": dmode,
                        "size": sz,
                        "window": win,
                        "rate_krps": rate_krps,
                        "warmup": wu,
                        "rounds": rds,
                        "card_args": list(cargs),
                        "saturn_cpu40_khz": cur_cpu40,
                        "client": c,
                        "card": k,
                        "host": h,
                        "verified": True,
                    })
                    print(
                        f"rep {rep} | {cfg_name:<30} | rate={c['achieved_mrps']:.3f}M/s "
                        f"| merc={c['tot_p50_us']:.3f}/{c['tot_p99_us']:.3f} "
                        f"| card={k['host_rtt_p50_us']:.3f}/{k['host_rtt_p99_us']:.3f} "
                        f"| avgB={c['avg_tx_batch']:.2f} neth={k['avg_n_eth']:.2f} "
                        f"| b4act={k['bar4_first_activations']} ld={k['cqe_peek']['ldadda_calls']} | ok",
                        flush=True,
                    )

            # -----------------------------------------------------------------
            # Group 6a: OP_DMA + OP_PUSH mixed bar4_wait_active reset verification
            # -----------------------------------------------------------------
            stack.ensure_card("direct", POLICY_ARGS["bar4_first"], ETH_DEV_SCALAR)
            stack.ensure_host()
            cfg_name = "op_dma_bar4_first_mix_ads_w4"
            tcp_cmd("10.10.0.2", 7529, "RESET")
            tcp_cmd("127.0.0.1", 7531, "RESET")
            c = run_merc("closed", "mix_ads", 4, 2_000, 10_000, 0x1615_0000 + rep * 1009)
            k = tcp_cmd("10.10.0.2", 7529, "STATS")
            h = tcp_cmd("127.0.0.1", 7531, "STATS")
            ok, checks = verify_run(c, k, h, 2_000, 10_000)
            assert ok and k["small_rpcs"] > 0 and k["large_rpcs"] > 0, (
                f"FAILED {cfg_name} rep {rep}: {checks} k={k}"
            )
            append_rec({
                "rep": rep,
                "group": "op_dma_check",
                "cfg": cfg_name,
                "policy": "bar4_first",
                "exploratory": False,
                "eth_dev": ETH_DEV_SCALAR,
                "drive_mode": "closed",
                "size": "mix_ads",
                "window": 4,
                "warmup": 2_000,
                "rounds": 10_000,
                "card_args": list(POLICY_ARGS["bar4_first"]),
                "client": c,
                "card": k,
                "host": h,
                "verified": True,
            })
            print(
                f"rep {rep} | {cfg_name:<30} | merc_p50={c['tot_p50_us']:.3f} card_p50={k['host_rtt_p50_us']:.3f} "
                f"small={k['small_rpcs']} large={k['large_rpcs']} b4act={k['bar4_first_activations']} to={k['stages']['tight_timeouts']} | ok",
                flush=True,
            )

            # -----------------------------------------------------------------
            # Group 6b: Vector Neon control for default W=32
            # -----------------------------------------------------------------
            stack.ensure_card("direct", (), ETH_DEV_VEC)
            stack.ensure_host()
            tcp_cmd("10.10.0.2", 7529, "RESET")
            tcp_cmd("127.0.0.1", 7531, "RESET")
            c = run_merc("closed", "64", 32, WARMUP, ROUNDS_W32, 0x1619_0000 + rep * 1009)
            k = tcp_cmd("10.10.0.2", 7529, "STATS")
            h = tcp_cmd("127.0.0.1", 7531, "STATS")
            ok, checks = verify_run(c, k, h, WARMUP, ROUNDS_W32)
            assert ok, f"FAILED default_vec_64_w32 rep {rep}: {checks}"
            append_rec({
                "rep": rep,
                "group": "vector_control",
                "cfg": "direct_default_vec_64_w32",
                "policy": "default_vec",
                "exploratory": False,
                "eth_dev": ETH_DEV_VEC,
                "drive_mode": "closed",
                "size": 64,
                "window": 32,
                "warmup": WARMUP,
                "rounds": ROUNDS_W32,
                "card_args": [],
                "client": c,
                "card": k,
                "host": h,
                "verified": True,
            })
            print(
                f"rep {rep} | {'direct_default_vec_64_w32':<30} | rate={c['achieved_mrps']:.3f}M/s "
                f"| merc={c['tot_p50_us']:.3f}/{c['tot_p99_us']:.3f} | card={k['host_rtt_p50_us']:.3f} | ok",
                flush=True,
            )

    finally:
        print("\nRestoring CN106 card to default state...", flush=True)
        stack.restore_card()

    print(f"\nWrote {len(records)} verified runs to {RUNS_PATH}", flush=True)

    eth0_link = ssh_card("ip -br link show eth0; ethtool eth0 | grep -E 'Speed|Link detected'").stdout.strip()
    ping_out = ssh_card("ping -c 3 -W 1 10.10.100.1 | tail -n 2").stdout.strip()
    print(f"eth0 link:\n{eth0_link}\neth0 ping:\n{ping_out}", flush=True)
    print("Waiting 20 s for post-restore EDAC / SEI-UER recheck...", flush=True)
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
    print(f"Post-restore 20s card check:\n{post_card}", flush=True)
    print(f"Post-restore host EDAC ue_count={after_env['saturn_edac_ue']}", flush=True)


if __name__ == "__main__":
    main()
