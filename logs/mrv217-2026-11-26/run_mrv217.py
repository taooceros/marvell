#!/usr/bin/env python3
"""MRV-217 full n=3 interleaved hardware backpressure (SDPX_OUT_BP_ENX_W1S=1) vs
explicit BAR4 CreditRing experiment runner on saturn + CN106 + mercury.

Covers 3 single-variable verifications (n=3 interleaved, zero silent retries):
  - Part V1: Live hardware backpressure activation & granularity:
      * V1A: DPDK 24.11 nb_txq=1 (ROC_NIX_TM_DEFAULT TL4=0x3007 bug) with BP=1
      * V1B: 64 B high-rate WMARK=64 skid boundary without FIFO overflow:
             64B@14Mpps (2ms/10ms pause, use_fifo=1, ffull=0, af=0) and
             64B@25Mpps (1ms/10ms pause, use_fifo=0, ffull=0, af=0) comparing
             BP=0 D=256, BP=1 D=256 (WMARK=64), BP=1 D=512 (WMARK=128), CreditRing D=256 R=128
  - Part V2: E1 single-queue D1 & dual-queue D2 (dedicated 2 ARM card cores,
             nb_txq=2 -> ROC_NIX_TM_SDP TL4=0x3008/0x3009):
      * V2_D1: 1500B@5Mpps 1ms/10ms pause: BP=0 (D=256/512), BP=1 (D=256/512),
               CreditRing (R=256/128/64 and byte-granular 96 KiB)
      * V2_D2: Q0=3.0Mpps healthy + Q1=1.5Mpps stalled (8ms/10ms pause AND
               60ms/200ms zero-overflow sustained freeze, ffull=0, af=0):
               BP=0 D=512, BP=1 D=256/512 (mask 0xFF00), BP=1 Q1-only (mask 0x0200),
               CreditRing (R=128/256)
  - Part V3: E2 real 100GbE 1500B bursts (4/16/64/128 MiB from mercury -> CN106
             4-core -> saturn) + concurrent Socket-1 32 MiB KV on cpu41:
             kv_solo, large_mono (D=8192), bp0_d256, bp1_d256, bp1_d512,
             bp1_d2048 (unpatched stock octeon_ep min OQ), credit_ring (D=512, R=256).

Per-run audit gates:
  - Verifies pcifunc 0x2 NIX_AF_TL4_SDP_LINK_CFG matches expected channels.
  - Reads SDPX_GBL_CONTROL and SDPX_OUT_BP_ENX_W1S(0) BOTH before and after EVERY run.
  - Enforces full integer loss accounting (loss_ledger) and never silently drops
    or hides real packet losses; retries ONLY on transient clock dips or minor pf.
  - Computes host IMC GB/s from imc_all_{rd,wr}_mib and distinguishes gross apparent
    drain from net PCIe SDP drain Gbps (subtracting pre-staged host-ring packets).
  - Guaranteed `finally` restores all CSRs (verified == original), BAR4 slot 15,
    eth0 100G UP, refl tmux, cleans hugepages & mercury /tmp/mrv217*, and performs
    a 21 s post-restore EDAC/AER recheck saved in env.json.
"""

import hashlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv217")
LOG_DIR = ROOT / "logs/mrv217-2026-11-26"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ENV_PATH = LOG_DIR / "env.json"
RUN_LOG_PATH = LOG_DIR / "run.log"

BIN_DIR = ROOT / "testbed/target/release"
CARD_BIN_DIR = ROOT / "testbed/target/aarch64-unknown-linux-gnu/release"
E1_HOST = BIN_DIR / "mrv217_e1_host"
E2_HOST = BIN_DIR / "mrv217_e2_host"
VFIO_FLR = BIN_DIR / "mrv217_vfio_flr"
E1_CARD_LOCAL = CARD_BIN_DIR / "mrv217_e1_card"
E2_CARD_LOCAL = CARD_BIN_DIR / "mrv217_e2_card"
E1_CARD_REMOTE = "/root/bench/mrv217_e1_card"
E2_CARD_REMOTE = "/root/bench/mrv217_e2_card"
MERC_SRC_DIR = ROOT / "testbed/mercury-client"
MERC_REMOTE_DIR = "/tmp/mrv217"
MERC_REMOTE_BIN = "/tmp/mrv217/mrv217_client"

CARD_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "mercury"]

CARD_SYNC_HOST = "10.10.0.2"
CARD_SYNC_PORT = 7527
MERC_CTRL_HOST = "mercury"
MERC_CTRL_PORT = 7547

REPS = 3
MIN_CORE_GHZ = 3.85
MIN_MESH_GHZ = 2.18
MAX_RETRIES = 5

MB = 1024 * 1024
RUN_GEN = 217000


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    line = f"[{utc_now()}] {msg}"
    print(line, flush=True)
    with open(RUN_LOG_PATH, "a") as f:
        f.write(line + "\n")


def sh(cmd: list[str], check: bool = True, timeout: int = 120, input_text: str | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, text=True, input=input_text, capture_output=True, check=False, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"CMD FAILED ({r.returncode}): {' '.join(cmd)}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
    return r


def ssh_card(script: str, check: bool = True, timeout: int = 60, input_text: str | None = None) -> subprocess.CompletedProcess:
    return sh(CARD_SSH + [script], check=check, timeout=timeout, input_text=input_text)


def ssh_merc(script: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    return sh(MERC_SSH + [script], check=check, timeout=timeout)


def tcp_cmd(host: str, port: int, cmd: str, timeout: float = 15.0) -> dict:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((cmd + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            c = s.recv(65536)
            if not c:
                break
            buf += c
        return json.loads(buf.decode().strip())


def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def append_jsonl(path: pathlib.Path, obj: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")
        f.flush()


def read_all_safe_csrs() -> dict[str, str]:
    py = """
import ctypes, json, mmap, os
ps = os.sysconf("SC_PAGE_SIZE")
REGS = [
    ("SDPX_EPFX_RINFO(0)",       0x86E0800209F0),
    ("SDPX_MACX_PF_RING_CTL(0)", 0x86E08002C000),
    ("SDPX_LINK_CFG",            0x86E0C0080180),
    ("SDPX_GBL_CONTROL",         0x86E0C0080200),
    ("SDPX_OUT_BP_ENX_W1S(0)",   0x86E0C0080280),
    ("SDPX_OUT_BP_ENX_W1S(1)",   0x86E0C0080290),
]
out = {}
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
try:
    for name, phys in REGS:
        page = phys & ~(ps - 1)
        poff = phys & (ps - 1)
        m = mmap.mmap(fd, ps, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=page)
        try:
            q = (ctypes.c_uint64 * (ps // 8)).from_buffer(m)
            val = q[poff >> 3]
            del q
            out[name] = f"{val:#018x}"
        finally:
            m.close()
finally:
    os.close(fd)
print(json.dumps(out))
"""
    return json.loads(ssh_card("python3 -", input_text=py).stdout.strip())


def csr_read_pair() -> tuple[int, int]:
    py = """
import ctypes, mmap, os
ps = os.sysconf("SC_PAGE_SIZE")
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, ps, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x86E0C0080000)
q = (ctypes.c_uint64 * (ps // 8)).from_buffer(m)
print(f"{q[0x200>>3]:#x} {q[0x280>>3]:#x}")
del q; m.close(); os.close(fd)
"""
    a, b = ssh_card("python3 -", input_text=py).stdout.strip().split()
    return int(a, 16), int(b, 16)


def csr_clear_bp() -> tuple[int, int]:
    py0 = """
import ctypes, mmap, os
ps = os.sysconf("SC_PAGE_SIZE")
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, ps, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x86E0C0080000)
q = (ctypes.c_uint64 * (ps // 8)).from_buffer(m)
q[0x200>>3] = 0
del q; m.close(); os.close(fd)
"""
    py4 = py0.replace("= 0", "= 4")
    ssh_card("python3 -", input_text=py0)
    try:
        sh([str(VFIO_FLR)])
        time.sleep(0.1)
    finally:
        ssh_card("python3 -", input_text=py4)
    gbl, bp0 = csr_read_pair()
    if gbl != 0x4 or bp0 != 0x0:
        raise RuntimeError(f"CSR clear failed: GBL={gbl:#018x} BP0={bp0:#018x}")
    return gbl, bp0


def csr_set_bp(mask: int) -> tuple[int, int]:
    if (mask & ~0xFF00) != 0:
        raise ValueError(f"Refusing to touch non-VF0 ring bits in mask={mask:#x}")
    csr_clear_bp()
    if mask != 0:
        py = f"""
import ctypes, mmap, os
ps = os.sysconf("SC_PAGE_SIZE")
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, ps, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x86E0C0080000)
q = (ctypes.c_uint64 * (ps // 8)).from_buffer(m)
q[0x280>>3] = {mask}
del q; m.close(); os.close(fd)
"""
        ssh_card("python3 -", input_text=py)
    gbl, bp0 = csr_read_pair()
    if gbl != 0x4 or bp0 != mask:
        raise RuntimeError(f"CSR verify failed: GBL={gbl:#018x} BP0={bp0:#018x} want={mask:#x}")
    return gbl, bp0


def capture_and_verify_sdp1_1_tm_topo(expected_tl4_cfgs: list[str]) -> dict:
    out = ssh_card(
        "lf=$(awk '/^PF0:VF1/{print $3}' /sys/kernel/debug/cn10k/rsrc_alloc); "
        "echo LF=$lf; "
        "echo $lf > /sys/kernel/debug/cn10k/nix/tm_tree && cat /sys/kernel/debug/cn10k/nix/tm_tree; "
        "echo '---'; "
        "echo $lf > /sys/kernel/debug/cn10k/nix/tm_topo && cat /sys/kernel/debug/cn10k/nix/tm_topo | grep -E 'pcifunc|SDP_LINK_CFG|HW_XOFF'"
    ).stdout.strip()
    parts = out.split("---")
    tree_lines = [l.strip() for l in parts[0].splitlines() if l.strip()]
    topo_lines = [l.strip() for l in parts[1].splitlines() if l.strip()] if len(parts) > 1 else []
    pcifunc_ok = any("pcifunc is 0x2" in l for l in tree_lines)
    tl4_vals = [l.split("=")[-1].strip() for l in topo_lines if "SDP_LINK_CFG" in l]
    if not pcifunc_ok or tl4_vals != expected_tl4_cfgs:
        raise RuntimeError(
            f"TM topo verification failed! pcifunc_ok={pcifunc_ok} got={tl4_vals} expected={expected_tl4_cfgs}\n"
            f"tree={tree_lines}\ntopo={topo_lines}"
        )
    return {"pcifunc": "0x2", "tm_tree": tree_lines, "sdp_link_cfg": topo_lines, "tl4_vals": tl4_vals}


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
    host_ue = {}
    for mc in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*")):
        p = mc / "ue_count"
        if p.exists():
            host_ue[mc.name] = int(p.read_text().strip())
    rp = pathlib.Path("/sys/bus/pci/devices/0000:98:01.0")
    saturn = {
        "cpu40_gov": pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip(),
        "cpu40_khz": int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip()),
        "uncore_khz": uncore,
        "host_edac_ue": host_ue,
        "host_edac_ue_sum": sum(host_ue.values()),
        "rp_98_01_aer": {
            "cor": parse_aer_total(rp / "aer_rootport_total_err_cor"),
            "nonfatal": parse_aer_total(rp / "aer_rootport_total_err_nonfatal"),
            "fatal": parse_aer_total(rp / "aer_rootport_total_err_fatal"),
        },
        "hugepages_m217_leftover": len(list(pathlib.Path("/dev/hugepages").glob("m217*"))),
    }
    card_raw = ssh_card(
        'echo "ETH0_OPER=$(cat /sys/class/net/eth0/operstate 2>/dev/null || echo unbound)"; '
        'echo "ETH0_SPEED=$(ethtool eth0 2>/dev/null | awk \'/Speed:/{print $2}\' || echo n/a)"; '
        'echo "PEM_WC=$(lsmod | grep -c \'^pem_wc \' || true)"; '
        'echo "REFL=$(tmux has-session -t refl 2>/dev/null && echo running || echo stopped)"; '
        'echo "EDAC_mc0=$(cat /sys/devices/system/edac/mc/mc0/ue_count 2>/dev/null || echo 0)"; '
        'echo "EDAC_llc0=$(cat /sys/devices/system/edac/llc/llc0/ue_count 2>/dev/null || echo 0)"; '
        'echo "EDAC_mdc0=$(cat /sys/devices/system/edac/mdc/mdc0/ue_count 2>/dev/null || echo 0)"; '
        'echo "EDAC_gic0=$(cat /sys/devices/system/edac/gic/gic0/ue_count 2>/dev/null || echo 0)"; '
        'echo "EDAC_ea0=$(cat /sys/devices/system/edac/ea/ea0/ue_count 2>/dev/null || echo 0)"; '
        'echo "EDAC_CPU_SUM=$(awk \'{s+=$1} END{print s+0}\' /sys/devices/system/edac/cpu/cpu*/ue_count 2>/dev/null || echo 0)"; '
        'echo "HP_M217=$(ls /dev/hugepages/m217* 2>/dev/null | wc -l)"'
    ).stdout.strip()
    card = {}
    for line in card_raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            card[k] = int(v) if v.isdigit() else v
    card["csrs"] = read_all_safe_csrs()

    merc_raw = ssh_merc(
        'echo "oper=$(cat /sys/class/net/ens6f1np1/operstate 2>/dev/null || echo unknown)"; '
        'echo "m217_procs=$(pgrep -xc mrv217_client 2>/dev/null || true)"; '
        'echo "tmp_mrv217=$(ls -d /tmp/mrv217* 2>/dev/null | wc -l)"; '
        'ethtool -S ens6f1np1 2>/dev/null | grep -E "rx_global_pause|tx_global_pause|rx_crc_errors_phy" || true'
    ).stdout.strip()
    merc = {}
    for line in merc_raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            merc[k] = int(v) if v.isdigit() else v
        elif ":" in line:
            k, v = line.split(":", 1)
            merc[k.strip()] = int(v.strip())
    return {"ts_utc": utc_now(), "saturn": saturn, "cn106": card, "mercury": merc}


def stop_e1_e2_card() -> None:
    try:
        tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "QUIT", timeout=1.5)
    except Exception:
        pass
    ssh_card(
        "sleep 0.25; tmux send-keys -t m217c C-c 2>/dev/null || true; sleep 0.25; "
        "tmux kill-session -t m217c 2>/dev/null || true; rm -f /dev/hugepages/m217c*",
        check=False,
    )


def start_e1_card(lcores: str, queues: int, expected_tl4: list[str], tx_desc: int = 512) -> dict:
    stop_e1_e2_card()
    ssh_card(
        "tmux send-keys -t refl C-c 2>/dev/null || true; sleep 0.25; "
        "tmux kill-session -t refl 2>/dev/null || true; rm -f /dev/hugepages/refl* /tmp/m217_card.log; "
        f"tmux new-session -d -s m217c '{E1_CARD_REMOTE} -l {lcores} -a 0002:01:00.2 --file-prefix=m217c -- --queues {queues} --tx-desc {tx_desc} --pool-size 131071 > /tmp/m217_card.log 2>&1'; "
        "for i in $(seq 1 80); do grep -q 'BAR4 slot 15 ready' /tmp/m217_card.log 2>/dev/null && break; sleep 0.15; done; "
        "grep 'BAR4 slot 15 ready' /tmp/m217_card.log"
    )
    return capture_and_verify_sdp1_1_tm_topo(expected_tl4)


def start_e2_card_and_mercury() -> tuple[dict, str]:
    stop_e1_e2_card()
    ssh_merc(f"pkill -x mrv217_client 2>/dev/null || true; mkdir -p {MERC_REMOTE_DIR}")
    sh(["scp", "-q", str(MERC_SRC_DIR / "mrv213_verbs_shim.c"), str(MERC_SRC_DIR / "mrv213_client.rs"), f"mercury:{MERC_REMOTE_DIR}/"])
    ssh_merc(
        f'export PATH="$HOME/.cargo/bin:$PATH" && cd {MERC_REMOTE_DIR} && '
        'gcc -O3 -Wall -Wextra -Werror -c mrv213_verbs_shim.c -o mrv213_verbs_shim.o && '
        'rustc -O -D warnings mrv213_client.rs -C link-arg=mrv213_verbs_shim.o -C link-arg=-libverbs -o mrv217_client && '
        './mrv217_client --self-test'
    )
    ssh_merc(
        'rm -f /tmp/mrv217_merc.log; '
        f'nohup {MERC_REMOTE_BIN} --port {MERC_CTRL_PORT} --dev mlx5_2 --ifname ens6f1np1 '
        '--src-ip 10.10.100.1 --dst-ip 10.10.100.2 --sgid 3 > /tmp/mrv217_merc.log 2>&1 < /dev/null & '
        'for i in $(seq 1 60); do grep -q "MRV213_CLIENT READY" /tmp/mrv217_merc.log 2>/dev/null && break; sleep 0.15; done; '
        'grep "MRV213_CLIENT READY" /tmp/mrv217_merc.log'
    )
    ssh_card(
        "tmux send-keys -t refl C-c 2>/dev/null || true; sleep 0.25; "
        "tmux kill-session -t refl 2>/dev/null || true; rm -f /dev/hugepages/refl* /tmp/m217_card.log; "
        "ip link set eth0 down 2>/dev/null || true; "
        "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
        'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
        f"tmux new-session -d -s m217c '{E2_CARD_REMOTE} -l 3-6 -a 0002:02:00.0 -a 0002:01:00.2 --file-prefix=m217c -- --sdp-tx-desc 2048 --eth-pool-size 16384 --fifo-mode compact --fifo-cap-bytes {192 * MB} > /tmp/m217_card.log 2>&1'; "
        "for i in $(seq 1 80); do grep -q 'MRV213_CARD READY' /tmp/m217_card.log 2>/dev/null && break; sleep 0.15; done; "
        "grep 'MRV213_CARD READY' /tmp/m217_card.log"
    )
    topo = capture_and_verify_sdp1_1_tm_topo(["0x3008", "0x3009", "0x300a", "0x300b"])
    tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "CONFIG policy=idle profile=1500b run_gen=1")
    tcp_cmd(MERC_CTRL_HOST, MERC_CTRL_PORT, "PROBE_RSS mode=udp1500 count=64 run_gen=1")
    time.sleep(0.05)
    st = tcp_cmd(CARD_SYNC_HOST, CARD_SYNC_PORT, "STATS")
    by_q = {q: [] for q in range(4)}
    for idx, q1 in enumerate(st.get("flow_q", [])):
        if 1 <= q1 <= 4:
            by_q[q1 - 1].append(idx + 1)
    balanced = []
    for d in range(max((len(v) for v in by_q.values()), default=0)):
        for q in range(4):
            if d < len(by_q[q]):
                balanced.append(by_q[q][d])
    flows = ",".join(str(x) for x in balanced[:8])
    return topo, flows


def exec_e1_run(part: str, tag: str, rep: int, bp_mask: int, expected_tl4: list[str],
                host_args: list[str], is_dual: bool) -> dict:
    global RUN_GEN
    for attempt in range(1, MAX_RETRIES + 1):
        RUN_GEN += 1
        pre_gbl, pre_bp0 = csr_set_bp(bp_mask)
        tm_check = capture_and_verify_sdp1_1_tm_topo(expected_tl4)
        sh(["sh", "-c", "rm -f /dev/hugepages/m217h*"], check=False)
        args = [str(E1_HOST)] + [str(RUN_GEN) if a == "__GEN__" else a for a in host_args]
        r = sh(args, check=False, timeout=90)
        post_gbl, post_bp0 = csr_read_pair()
        ts = utc_now()
        if r.returncode != 0:
            append_jsonl(ATTEMPTS_PATH, {
                "ts_utc": ts, "part": part, "tag": tag, "rep": rep, "attempt": attempt,
                "bp_mask": f"{bp_mask:#06x}", "accepted": False,
                "reject_reasons": [f"rc={r.returncode}: {r.stderr.strip()[-200:]}"],
            })
            time.sleep(0.3)
            continue
        jlines = [l for l in reversed(r.stdout.splitlines()) if l.startswith("{")]
        if not jlines:
            append_jsonl(ATTEMPTS_PATH, {
                "ts_utc": ts, "part": part, "tag": tag, "rep": rep, "attempt": attempt,
                "bp_mask": f"{bp_mask:#06x}", "accepted": False, "reject_reasons": ["no_json"],
            })
            continue
        rec = json.loads(jlines[0])
        rec["ts_utc"] = ts
        rec["part"] = part
        rec["tag"] = tag
        rec["rep"] = rep
        rec["attempt"] = attempt
        rec["bp_mask"] = f"{bp_mask:#06x}"
        rec["csr_pre"] = {"gbl": f"{pre_gbl:#018x}", "bp0": f"{pre_bp0:#018x}"}
        rec["csr_post"] = {"gbl": f"{post_gbl:#018x}", "bp0": f"{post_bp0:#018x}"}
        rec["tm_tl4_vals"] = tm_check["tl4_vals"]

        hp = rec["host_pmu"]
        cp = rec["card_pmu"]
        cs = rec["card_stats"]

        # Full end-to-end loss ledger
        if is_dual:
            rec["loss_ledger"] = {
                "q0_card_sent": cs["q0_sent"],
                "q0_sdp_oq_lost": rec["q0_lost"],
                "q0_card_fifo_full": cs["q0_fifo_full"],
                "q0_total_e2e_lost": rec["q0_lost"] + cs["q0_fifo_full"],
                "q1_card_sent": cs["q1_sent"],
                "q1_sdp_oq_lost": rec["q1_lost"],
                "q1_card_fifo_full": cs["q1_fifo_full"],
                "q1_total_e2e_lost": rec["q1_lost"] + cs["q1_fifo_full"],
                "card_alloc_fail": cs["alloc_fail"],
                "card_eth_oerrors": cs["eth_oerrors"],
            }
        else:
            rec["loss_ledger"] = {
                "q0_card_sent": cs["q0_sent"],
                "q0_host_rx": rec["total_rx"],
                "q0_sdp_oq_lost": rec["lost"],
                "q0_card_fifo_full": cs["q0_fifo_full"],
                "card_alloc_fail": cs["alloc_fail"],
                "card_eth_oerrors": cs["eth_oerrors"],
                "q0_total_e2e_lost": rec["lost"] + cs["q0_fifo_full"] + cs["alloc_fail"],
            }

        hard = []
        retryable = []
        if post_gbl != pre_gbl or post_bp0 != pre_bp0:
            hard.append(f"csr_drift pre=({pre_gbl:#x},{pre_bp0:#x}) post=({post_gbl:#x},{post_bp0:#x})")
        if hp["min_pct"] < 99.99:
            hard.append(f"host_pmu_pct={hp['min_pct']:.2f}")
        if cp.get("min_pct", 100.0) < 99.99:
            hard.append(f"card_pmu_pct={cp.get('min_pct', 0):.2f}")
        if cs.get("credit_torn", 0) != 0 or cs.get("alloc_fail", 0) != 0 or cs.get("eth_oerrors", 0) != 0:
            hard.append(f"card_err ct={cs.get('credit_torn')} af={cs.get('alloc_fail')} oe={cs.get('eth_oerrors')}")
        if cs.get("q0_fifo_full", 0) != 0 or cs.get("q1_fifo_full", 0) != 0:
            hard.append(f"card_fifo_full q0={cs.get('q0_fifo_full')} q1={cs.get('q1_fifo_full')}")
        if is_dual:
            if rec["q0_bad"] != 0 or rec["q0_torn"] != 0 or rec["q1_bad"] != 0 or rec["q1_torn"] != 0:
                hard.append(f"integrity q0={rec['q0_bad']}/{rec['q0_torn']} q1={rec['q1_bad']}/{rec['q1_torn']}")
            if rec["q0_page_faults"] != 0 or rec["q1_page_faults"] != 0:
                retryable.append(f"pf q0={rec['q0_page_faults']} q1={rec['q1_page_faults']}")
        else:
            if rec["bad"] != 0 or rec["torn"] != 0:
                hard.append(f"integrity bad={rec['bad']} torn={rec['torn']}")
            if rec["page_faults"] != 0:
                retryable.append(f"pf={rec['page_faults']}")
        if hp.get("ghz", 3.9) < MIN_CORE_GHZ:
            retryable.append(f"ghz={hp.get('ghz'):.3f}")

        if hard:
            rec["accepted"] = False
            rec["reject_reasons"] = hard + retryable
            append_jsonl(ATTEMPTS_PATH, rec)
            raise RuntimeError(f"HARD GATE FAIL {part}/{tag} rep={rep}: {hard}")
        if retryable:
            rec["accepted"] = False
            rec["reject_reasons"] = retryable
            append_jsonl(ATTEMPTS_PATH, rec)
            log(f"  [RETRY] {part}/{tag} rep={rep} attempt={attempt}: {retryable}")
            time.sleep(0.3)
            continue

        rec["accepted"] = True
        rec["reject_reasons"] = []
        append_jsonl(ATTEMPTS_PATH, rec)
        append_jsonl(RUNS_PATH, rec)
        return rec
    raise RuntimeError(f"Exhausted {MAX_RETRIES} retries for {part}/{tag} rep={rep}")


def exec_e2_run(part: str, tag: str, rep: int, bp_mask: int, arm: str, rxd: int, small_oq: bool,
                b_bytes: int, n_bursts: int, wu_bursts: int, flows: str) -> dict:
    global RUN_GEN
    for attempt in range(1, MAX_RETRIES + 1):
        RUN_GEN += 2
        pre_gbl, pre_bp0 = csr_set_bp(bp_mask)
        tm_check = capture_and_verify_sdp1_1_tm_topo(["0x3008", "0x3009", "0x300a", "0x300b"]) if arm != "kv-solo" else {"tl4_vals": []}
        sh(["sh", "-c", "rm -f /dev/hugepages/m217h*"], check=False)
        if arm == "kv-solo":
            cmd = [
                str(E2_HOST), "-l", "40", "--no-pci", "--file-prefix=m217h", "--",
                "--arm", "kv-solo", "--solo-ms", "600", "--warmup-ms", "1100",
                "--kv-bytes", str(32 * MB), "--kv-cpu", "41",
            ]
        else:
            dev = "0000:99:02.0,allow_small_oq=1" if small_oq else "0000:99:02.0"
            cmd = [
                str(E2_HOST), "-l", "40", "-a", dev, "--file-prefix=m217h", "--",
                "--arm", arm, "--profile", "1500b",
                "--burst-bytes", str(b_bytes), "--bursts", str(n_bursts), "--warmup-bursts", str(wu_bursts),
                "--sdp-queues", "4", "--rx-desc", str(rxd), "--r-eff", "256",
                "--merc-threads", "8", "--merc-batch", "32", "--merc-rate-mpps", "0.0",
                "--merc-flows", flows, "--run-gen", str(RUN_GEN),
            ]
        r = sh(cmd, check=False, timeout=120)
        post_gbl, post_bp0 = csr_read_pair()
        ts = utc_now()
        if r.returncode != 0:
            append_jsonl(ATTEMPTS_PATH, {
                "ts_utc": ts, "part": part, "tag": tag, "rep": rep, "attempt": attempt,
                "bp_mask": f"{bp_mask:#06x}", "accepted": False,
                "reject_reasons": [f"rc={r.returncode}: {r.stderr.strip()[-200:]}"],
            })
            time.sleep(0.5)
            continue
        jlines = [l for l in reversed(r.stdout.splitlines()) if l.startswith("MRV213 ")]
        if not jlines:
            append_jsonl(ATTEMPTS_PATH, {
                "ts_utc": ts, "part": part, "tag": tag, "rep": rep, "attempt": attempt,
                "bp_mask": f"{bp_mask:#06x}", "accepted": False, "reject_reasons": ["no_json"],
            })
            continue
        rec = json.loads(jlines[0][len("MRV213 "):])
        rec["ts_utc"] = ts
        rec["part"] = part
        rec["tag"] = tag
        rec["rep"] = rep
        rec["attempt"] = attempt
        rec["bp_mask"] = f"{bp_mask:#06x}"
        rec["csr_pre"] = {"gbl": f"{pre_gbl:#018x}", "bp0": f"{pre_bp0:#018x}"}
        rec["csr_post"] = {"gbl": f"{post_gbl:#018x}", "bp0": f"{post_bp0:#018x}"}
        rec["tm_tl4_vals"] = tm_check["tl4_vals"]

        hp = rec["host_pmu"]
        kv = rec.get("kv")
        imc_mib = hp["imc_all_rd_mib"] + hp["imc_all_wr_mib"]
        imc_gbps = (imc_mib * 1.048576 / 1000.0) / max(hp["wall_s"], 1e-9)
        rec["derived"] = {"host_imc_tot_mib": round(imc_mib, 3), "host_imc_tot_gbps": round(imc_gbps, 3)}

        hard = []
        retryable = []
        if post_gbl != pre_gbl or post_bp0 != pre_bp0:
            hard.append(f"csr_drift pre=({pre_gbl:#x},{pre_bp0:#x}) post=({post_gbl:#x},{post_bp0:#x})")
        if hp["min_pct"] < 99.99:
            hard.append(f"host_pmu_pct={hp['min_pct']:.2f}")
        if hp["mesh_ghz"] < MIN_MESH_GHZ:
            retryable.append(f"mesh_ghz={hp['mesh_ghz']:.3f}")

        if arm != "kv-solo":
            cp = rec["card_pmu"]
            cs = rec["card_stats"]
            ms = rec["mercury"]
            offered = rec["offered_pkts"]
            delivered = rec["rx_pkts"]
            sdp_oq_lost = cs["sdp_tx_pkts"] - delivered
            residual = offered - (delivered + max(sdp_oq_lost, 0) + cs["eth"]["imissed"] + cs["sdp_tx_drops"] + cs["fifo_drops"] + cs["fifo_cur_depth"])
            rec["loss_ledger"] = {
                "offered_pkts": offered,
                "eth_rx_pkts": cs["eth_rx_pkts"],
                "eth_imissed": cs["eth"]["imissed"],
                "sdp_tx_pkts": cs["sdp_tx_pkts"],
                "sdp_tx_drops": cs["sdp_tx_drops"],
                "fifo_drops": cs["fifo_drops"],
                "fifo_cur_depth": cs["fifo_cur_depth"],
                "host_rx_pkts": delivered,
                "sdp_oq_lost": sdp_oq_lost,
                "residual_unaccounted": residual,
            }
            # Net PCIe SDP drain Gbps: only defined for lossless card-buffered arms
            # (subtracting pre-staged host-ring packets already in host OQ before t_d0)
            if arm == "credit-ring" and rec["lost_pkts"] == 0 and rec["drain_s"] > 0:
                net_pkts = cs["fifo_deq"]
                net_wire_gbps = net_pkts * rec["wire_bytes"] * 8.0 / rec["drain_s"] / 1e9
                net_l2_gbps = net_pkts * rec["l2_bytes"] * 8.0 / rec["drain_s"] / 1e9
                rec["derived"]["net_pcie_drain_wire_gbps"] = round(net_wire_gbps, 3)
                rec["derived"]["net_pcie_drain_l2_gbps"] = round(net_l2_gbps, 3)
                rec["derived"]["net_pcie_pkts"] = net_pkts
            elif arm == "native-bp" and bp_mask != 0 and rec["lost_pkts"] == 0 and rec["drain_s"] > 0:
                # For BP=1, up to sdp_queues * rx_desc packets per burst are pre-staged in host OQ before t_d0;
                # remaining packets sit on CN106 (in NIX SQ + software FIFO) and cross PCIe during drain_s.
                prestaged = min(offered, rec["bursts"] * rec["sdp_queues"] * rec["rx_desc"])
                net_pkts = max(0, offered - prestaged)
                net_wire_gbps = net_pkts * rec["wire_bytes"] * 8.0 / rec["drain_s"] / 1e9
                net_l2_gbps = net_pkts * rec["l2_bytes"] * 8.0 / rec["drain_s"] / 1e9
                rec["derived"]["net_pcie_drain_wire_gbps"] = round(net_wire_gbps, 3)
                rec["derived"]["net_pcie_drain_l2_gbps"] = round(net_l2_gbps, 3)
                rec["derived"]["net_pcie_pkts"] = net_pkts
            else:
                rec["derived"]["net_pcie_drain_wire_gbps"] = None
                rec["derived"]["net_pcie_drain_l2_gbps"] = None
                rec["derived"]["net_pcie_pkts"] = None

            if hp["rx"]["ghz"] < MIN_CORE_GHZ:
                retryable.append(f"rx_ghz={hp['rx']['ghz']:.3f}")
            if hp["rx"]["page_faults"] != 0:
                retryable.append(f"rx_pf={hp['rx']['page_faults']}")
            if rec["bad"] != 0 or rec["torn"] != 0:
                hard.append(f"host_integrity bad={rec['bad']} torn={rec['torn']}")
            if arm == "credit-ring" and not rec.get("bar4_lease_ok", False):
                hard.append("bar4_lease_lost")
            if cp["min_pct"] < 99.99:
                hard.append(f"card_pmu_pct={cp['min_pct']:.2f}")
            if cs["wire_bad"] != 0 or cs["wire_torn"] != 0 or cs["credit_torn"] != 0:
                hard.append(f"card_integrity wb={cs['wire_bad']} wt={cs['wire_torn']} ct={cs['credit_torn']}")
            if cs["eth"]["imissed"] != 0 or cs["fifo_drops"] != 0:
                hard.append(f"card_drop eth_imiss={cs['eth']['imissed']} fifo_drops={cs['fifo_drops']}")
            if cs["eth_rx_pkts"] != offered:
                hard.append(f"wire_rx_mismatch {cs['eth_rx_pkts']}!={offered}")
            if residual != 0:
                hard.append(f"loss_ledger_residual={residual}")
            if ms["cqe_err"] != 0 or ms["post_err"] != 0 or ms["phy"]["tx_pause"] != 0 or ms["phy"]["rx_pause"] != 0:
                hard.append(f"merc_err cqe={ms['cqe_err']} post={ms['post_err']} pause={ms['phy']['tx_pause']}/{ms['phy']['rx_pause']}")
        if kv is not None:
            if kv["bad"] != 0:
                hard.append(f"kv_bad={kv['bad']}")
            if kv["page_faults"] != 0 or hp["kv"]["page_faults"] != 0:
                retryable.append(f"kv_pf={kv['page_faults']}/{hp['kv']['page_faults']}")
            if hp["kv"]["ghz"] < MIN_CORE_GHZ:
                retryable.append(f"kv_ghz={hp['kv']['ghz']:.3f}")
            if rec["kv_page_kb"] != 2048 or rec["kv_n0"] != 0 or rec["kv_n1"] <= 0:
                hard.append(f"kv_numa={rec['kv_page_kb']}k/N0={rec['kv_n0']}/N1={rec['kv_n1']}")

        if hard:
            rec["accepted"] = False
            rec["reject_reasons"] = hard + retryable
            append_jsonl(ATTEMPTS_PATH, rec)
            raise RuntimeError(f"HARD GATE FAIL {part}/{tag} rep={rep}: {hard}")
        if retryable:
            rec["accepted"] = False
            rec["reject_reasons"] = retryable
            append_jsonl(ATTEMPTS_PATH, rec)
            log(f"  [RETRY] {part}/{tag} rep={rep} attempt={attempt}: {retryable}")
            time.sleep(0.5)
            continue

        rec["accepted"] = True
        rec["reject_reasons"] = []
        append_jsonl(ATTEMPTS_PATH, rec)
        append_jsonl(RUNS_PATH, rec)
        return rec
    raise RuntimeError(f"Exhausted {MAX_RETRIES} retries for {part}/{tag} rep={rep}")


def restore_full_baseline() -> dict:
    log("=== Restoring full hardware & CSR baseline ===")
    try:
        tcp_cmd(MERC_CTRL_HOST, MERC_CTRL_PORT, "QUIT", timeout=2.0)
    except Exception:
        pass
    ssh_merc(f"pkill -x mrv217_client 2>/dev/null || true; rm -rf {MERC_REMOTE_DIR} /tmp/mrv217_merc.log", check=False)
    stop_e1_e2_card()
    sh(["sh", "-c", "rm -f /dev/hugepages/m217*"], check=False)
    gbl, bp0 = csr_clear_bp()
    out = ssh_card(
        "if lsmod | grep -q '^pem_wc '; then rmmod pem_wc; fi; "
        f"{E2_CARD_REMOTE} --restore 2>&1 || true; "
        "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
        'if [ "$drv" != "rvu_nicpf" ]; then dpdk-devbind.py -b rvu_nicpf 0002:02:00.0; sleep 1.5; fi; '
        "ip addr flush dev eth0 2>/dev/null || true; "
        "ip addr add 10.10.100.2/24 dev eth0; "
        "ip link set eth0 up; "
        "sleep 2.0; "
        "tmux kill-session -t refl 2>/dev/null || true; rm -f /dev/hugepages/refl*; "
        "tmux new-session -d -s refl /root/bench/reflector-rs -l 3 -a 0002:01:00.2 --file-prefix=refl; "
        "sleep 1.0; "
        "ethtool eth0 | grep -E 'Speed|Link detected'; "
        "ping -c 2 -W 1 10.10.100.1 | tail -n 2; "
        "tmux ls"
    ).stdout.strip()
    log("Waiting 21s for post-restore EDAC/AER recheck ...")
    time.sleep(21)
    return {"csr_gbl": f"{gbl:#018x}", "csr_bp0": f"{bp0:#018x}", "restore_stdout": out}


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for p in (ATTEMPTS_PATH, RUNS_PATH, RUN_LOG_PATH):
        if p.exists():
            p.unlink()

    t0 = time.monotonic()
    jj_parent = sh(["jj", "-R", str(ROOT), "log", "-r", "@-", "--no-graph", "-T", 'change_id.shortest(8) ++ " " ++ commit_id.shortest(12) ++ " " ++ description.first_line()']).stdout.strip()
    pre_env = read_env_snapshot()
    orig_csrs = dict(pre_env["cn106"]["csrs"])
    assert orig_csrs["SDPX_GBL_CONTROL"] == "0x0000000000000004", f"Unexpected pre GBL: {orig_csrs}"
    assert orig_csrs["SDPX_OUT_BP_ENX_W1S(0)"] == "0x0000000000000000", f"Unexpected pre BP0: {orig_csrs}"

    env_doc = {
        "issue": "MRV-217",
        "workspace": str(ROOT),
        "jj_code_parent": jj_parent,
        "binaries_sha256": {
            "mrv217_e1_host": sha256_file(E1_HOST),
            "mrv217_e2_host": sha256_file(E2_HOST),
            "mrv217_vfio_flr": sha256_file(VFIO_FLR),
            "mrv217_e1_card": sha256_file(E1_CARD_LOCAL),
            "mrv217_e2_card": sha256_file(E2_CARD_LOCAL),
        },
        "pre_session_csr_probe_note": "During initial read-only CSR audit prior to this campaign, probing legacy CN9K global SDPX_OUT_WMARK (0x86e0c0060000, unimplemented on CN106) raised 1 recoverable EA: SEI-UER (ea0/ue_count 61 -> 62, dmesg [501498.132783]). That legacy offset is never accessed in this campaign.",
        "pre_audit": pre_env,
        "tm_topo_snapshots": {},
    }

    try:
        # =====================================================================
        # VERIFICATION 1A: DPDK 24.11 nb_txq=1 (TL4=0x3007) bug (n=3)
        # =====================================================================
        log("=== V1A: DPDK 24.11 nb_txq=1 (ROC_NIX_TM_DEFAULT TL4=0x3007 bug) with BP=1 ===")
        env_doc["tm_topo_snapshots"]["e1_nb_txq_1"] = start_e1_card("3,4", queues=1, expected_tl4=["0x3007"], tx_desc=512)
        log(f"  TM topo (q=1): {env_doc['tm_topo_snapshots']['e1_nb_txq_1']['tl4_vals']}")
        for rep in range(REPS):
            rec = exec_e1_run("V1A", "q1_bp1_d256_bug", rep, 0xFF00, ["0x3007"], [
                "-l", "40", "-a", "0000:99:02.0,allow_small_oq=1", "--file-prefix=m217h", "--",
                "--mode", "rx-stream", "--credit", "native-bp", "--use-fifo", "--fifo-cap", "131072",
                "--frame-size", "1500", "--rx-desc", "256", "--pool-size", "1023",
                "--target-mpps", "5.0", "--pause-every-ms", "10", "--pause-dur-us", "1000",
                "--duration-ms", "1000", "--warmup-ms", "300", "--run-gen", "__GEN__",
            ], is_dual=False)
            cs = rec["card_stats"]
            log(f"  V1A rep={rep} q1_bp1_d256_bug: rx={rec['rx_mpps']:.3f}M({rec['rx_gbps']:.1f}G) lost={rec['lost']} ret={cs['q0_tx_retries']} fp={cs['q0_fifo_peak']}")

        # =====================================================================
        # SWITCH TO DEDICATED 2-CORE PER-QUEUE CARD SERVER (-l 3,4,5 --queues 2)
        # =====================================================================
        env_doc["tm_topo_snapshots"]["e1_nb_txq_2"] = start_e1_card("3,4,5", queues=2, expected_tl4=["0x3008", "0x3009"], tx_desc=512)
        log(f"  TM topo (q=2): {env_doc['tm_topo_snapshots']['e1_nb_txq_2']['tl4_vals']}")

        # =====================================================================
        # VERIFICATION 1B: 64B high-rate WMARK=64 skid test (zero FIFO overflow, n=3)
        # =====================================================================
        log("=== V1B: 64B high-rate WMARK=64 skid boundary (zero card FIFO overflow, n=3 interleaved) ===")
        v1b_specs = [
            # (tag, rate, use_fifo, pause_us, mask, cm, rxd, reff)
            ("64B_14M_fifo_bp0_d256", "14.0", True,  2000, 0x0000, "native-bp",    256, 128),
            ("64B_14M_fifo_bp1_d256", "14.0", True,  2000, 0xFF00, "native-bp",    256, 128),
            ("64B_14M_fifo_bp1_d512", "14.0", True,  2000, 0xFF00, "native-bp",    512, 256),
            ("64B_14M_fifo_cr_d256",  "14.0", True,  2000, 0x0000, "bar4-nofence", 256, 128),
            ("64B_25M_nofifo_bp1_d256", "25.0", False, 1000, 0xFF00, "native-bp",    256, 128),
            ("64B_25M_nofifo_bp1_d512", "25.0", False, 1000, 0xFF00, "native-bp",    512, 256),
            ("64B_25M_nofifo_cr_d256",  "25.0", False, 1000, 0x0000, "bar4-nofence", 256, 128),
        ]
        for rep in range(REPS):
            specs = v1b_specs if rep % 2 == 0 else list(reversed(v1b_specs))
            for tag, rate, use_fifo, pause_us, mask, cm, rxd, reff in specs:
                hargs = [
                    "-l", "40", "-a", "0000:99:02.0,allow_small_oq=1", "--file-prefix=m217h", "--",
                    "--mode", "rx-stream", "--credit", cm,
                    "--frame-size", "64", "--rx-desc", str(rxd), "--pool-size", "1023",
                    "--r-eff", str(reff), "--credit-batch", "32",
                    "--target-mpps", rate, "--pause-every-ms", "10", "--pause-dur-us", str(pause_us),
                    "--duration-ms", "1000", "--warmup-ms", "300", "--run-gen", "__GEN__",
                ]
                if use_fifo:
                    hargs += ["--use-fifo", "--fifo-cap", "131072"]
                rec = exec_e1_run("V1B", tag, rep, mask, ["0x3008", "0x3009"], hargs, is_dual=False)
                cs = rec["card_stats"]
                log(f"  V1B rep={rep} {tag:24s}: rx={rec['rx_mpps']:6.3f}M lost={rec['lost']:7d} ooo={rec['ooo']:3d} ret={cs['q0_tx_retries']:7d} fp={cs['q0_fifo_peak']:5d} dr={rec['pause_drained_max']:3d}")

        # =====================================================================
        # VERIFICATION 2A (E1 D1): 1500B@5Mpps single-queue 1ms/10ms pause (n=3)
        # =====================================================================
        log("=== V2_D1: E1 single-queue 1500B@5Mpps 1ms/10ms pause (8 configs x n=3 interleaved) ===")
        d1_specs = [
            ("d1_bp0_d256",       0x0000, "native-bp",    256, 128, 0),
            ("d1_bp0_d512",       0x0000, "native-bp",    512, 256, 0),
            ("d1_bp1_d256",       0xFF00, "native-bp",    256, 128, 0),
            ("d1_bp1_d512",       0xFF00, "native-bp",    512, 256, 0),
            ("d1_cr_d256_r128",   0x0000, "bar4-nofence", 256, 128, 0),
            ("d1_cr_d512_r256",   0x0000, "bar4-nofence", 512, 256, 0),
            ("d1_cr_d256_r64",    0x0000, "bar4-nofence", 256,  64, 0),
            ("d1_cr_d256_96kib",  0x0000, "bar4-nofence", 256, 256, 98304),
        ]
        for rep in range(REPS):
            specs = d1_specs if rep % 2 == 0 else list(reversed(d1_specs))
            for tag, mask, cm, rxd, reff, rbytes in specs:
                rec = exec_e1_run("V2_D1", tag, rep, mask, ["0x3008", "0x3009"], [
                    "-l", "40", "-a", "0000:99:02.0,allow_small_oq=1", "--file-prefix=m217h", "--",
                    "--mode", "rx-stream", "--credit", cm, "--use-fifo", "--fifo-cap", "131072",
                    "--frame-size", "1500", "--rx-desc", str(rxd), "--pool-size", "1023",
                    "--r-eff", str(reff), "--r-eff-bytes", str(rbytes), "--credit-batch", "32",
                    "--target-mpps", "5.0", "--pause-every-ms", "10", "--pause-dur-us", "1000",
                    "--duration-ms", "1000", "--warmup-ms", "300", "--run-gen", "__GEN__",
                ], is_dual=False)
                cs = rec["card_stats"]
                log(f"  D1  rep={rep} {tag:17s}: rx={rec['rx_mpps']:.3f}M({rec['rx_gbps']:.1f}G) lost={rec['lost']:6d} ret={cs['q0_tx_retries']:7d} fp={cs['q0_fifo_peak']:5d} dr={rec['pause_drained_max']:3d} rf_p50={rec['resume_first_p50_us']:.1f}us rf_max={rec['resume_first_max_us']:.1f}us")

        # =====================================================================
        # VERIFICATION 2B (E1 D2): Dual-queue Q0 healthy + Q1 stalled HoL (n=3)
        # =====================================================================
        log("=== V2_D2: E1 dual-queue Q0 healthy (3.0M) + Q1 8ms/10ms pause & 60ms zero-overflow freeze (n=3) ===")
        d2_specs = [
            ("d2_bp0_d512",         0x0000, "native-bp",    512, 256, 10,   8000, 1000),
            ("d2_bp1_d256",         0xFF00, "native-bp",    256, 128, 10,   8000, 1000),
            ("d2_bp1_d512",         0xFF00, "native-bp",    512, 256, 10,   8000, 1000),
            ("d2_bp1_q1only_d256",  0x0200, "native-bp",    256, 128, 10,   8000, 1000),
            ("d2_cr_d256_r128",     0x0000, "bar4-nofence", 256, 128, 10,   8000, 1000),
            ("d2_cr_d512_r256",     0x0000, "bar4-nofence", 512, 256, 10,   8000, 1000),
            ("d2_freeze60_bp1_d256", 0xFF00, "native-bp",   256, 128, 200, 60000, 1000),
            ("d2_freeze60_cr_r128",  0x0000, "bar4-nofence", 256, 128, 200, 60000, 1000),
        ]
        for rep in range(REPS):
            specs = d2_specs if rep % 2 == 0 else list(reversed(d2_specs))
            for tag, mask, cm, rxd, reff, per_ms, pause_us, dur_ms in specs:
                rec = exec_e1_run("V2_D2", tag, rep, mask, ["0x3008", "0x3009"], [
                    "-l", "40,41", "-a", "0000:99:02.0,allow_small_oq=1", "--file-prefix=m217h", "--",
                    "--mode", "dual-queue", "--credit", cm, "--use-fifo", "--fifo-cap", "131072",
                    "--frame-size", "1500", "--rx-desc", str(rxd),
                    "--r-eff", str(reff), "--credit-batch", "32",
                    "--target-mpps", "4.5", "--q1-rate-frac", "0.5",
                    "--q1-period-ms", str(per_ms), "--q1-pause-us", str(pause_us),
                    "--duration-ms", str(dur_ms), "--warmup-ms", "300", "--run-gen", "__GEN__",
                ], is_dual=True)
                cs = rec["card_stats"]
                log(f"  D2  rep={rep} {tag:21s}: Q0={rec['q0_mpps']:.3f}M(lost={rec['q0_lost']},p99={rec['q0_gap_p99_us']:.2f}us,max={rec['q0_gap_max_us']:.1f}us,ret={cs['q0_tx_retries']}) "
                    f"Q1={rec['q1_mpps']:.3f}M(lost={rec['q1_lost']},dr={rec['q1_drained_max']},ret={cs['q1_tx_retries']},fp={cs['q1_fifo_peak']},rf_max={rec['q1_resume_first_max_us']:.1f}us)")

        # =====================================================================
        # VERIFICATION 3 (E2): Real 100GbE 1500B bursts (4/16/64/128 MiB) + 32 MiB KV (n=3)
        # =====================================================================
        log("=== V3_E2: Real 100GbE 1500B burst (4/16/64/128 MiB) + concurrent 32 MiB KV (n=3 interleaved) ===")
        e2_topo, flows = start_e2_card_and_mercury()
        env_doc["tm_topo_snapshots"]["e2_nb_txq_4"] = e2_topo
        env_doc["e2_balanced_flows"] = flows
        log(f"  E2 TM topo (q=4): {e2_topo['tl4_vals']}  flows={flows}")

        e2_bursts = [
            ("4MiB",   4 * MB,   16, 2),
            ("16MiB",  16 * MB,  8,  2),
            ("64MiB",  64 * MB,  4,  1),
            ("128MiB", 128 * MB, 4,  1),
        ]
        e2_arms = [
            ("large_mono",  0x0000, "large-mono",  8192, False),
            ("bp0_d256",    0x0000, "native-bp",    256, True),
            ("bp1_d256",    0xFF00, "native-bp",    256, True),
            ("bp1_d512",    0xFF00, "native-bp",    512, True),
            ("bp1_d2048",   0xFF00, "native-bp",   2048, False),
            ("credit_ring", 0x0000, "credit-ring",  512, True),
        ]

        for rep in range(REPS):
            rec_solo = exec_e2_run("V3_E2", "kv_solo", rep, 0x0000, "kv-solo", 512, True, 0, 0, 0, flows)
            kv = rec_solo["kv"]
            log(f"  E2  rep={rep} {'kv_solo':22s}: kv_p50={kv['p50_ns']:.1f}ns p99={kv['p99_ns']:.1f}ns p999={kv['p999_ns']:.1f}ns imc={rec_solo['derived']['host_imc_tot_gbps']:.2f}GB/s")
            bursts_ord = e2_bursts if rep % 2 == 0 else list(reversed(e2_bursts))
            for blabel, bbytes, nb, wub in bursts_ord:
                arms_ord = e2_arms if rep % 2 == 0 else list(reversed(e2_arms))
                for alabel, mask, arm_flag, rxd, small_oq in arms_ord:
                    tag = f"{blabel}_{alabel}"
                    rec = exec_e2_run("V3_E2", tag, rep, mask, arm_flag, rxd, small_oq, bbytes, nb, wub, flows)
                    cs = rec["card_stats"]
                    hp = rec["host_pmu"]
                    kv = rec["kv"]
                    net_g = rec["derived"]["net_pcie_drain_wire_gbps"]
                    net_s = f"{net_g:5.1f}G" if net_g is not None else "  n/a "
                    log(f"  E2  rep={rep} {tag:22s}: drop={rec['drop_pct']:5.2f}% ddio={hp['ddio_hit_pct']:5.2f}% "
                        f"kv_p99={kv['p99_ns']:5.1f}ns p999={kv['p999_ns']:5.1f}ns imc={rec['derived']['host_imc_tot_gbps']:5.2f}GB/s "
                        f"net_pcie={net_s} stalls={cs['sdp_tx_stalls']:6d} fp={cs['fifo_max_depth']:6d}")

    finally:
        restore_info = restore_full_baseline()
        post_env = read_env_snapshot()
        env_doc["restore_info"] = restore_info
        env_doc["post_audit"] = post_env
        env_doc["campaign_wall_s"] = round(time.monotonic() - t0, 2)

        pre_s, post_s = pre_env["saturn"], post_env["saturn"]
        pre_c, post_c = pre_env["cn106"], post_env["cn106"]
        deltas = {
            "host_edac_ue_delta": {k: post_s["host_edac_ue"][k] - pre_s["host_edac_ue"][k] for k in pre_s["host_edac_ue"]},
            "host_rp_aer_delta": {k: post_s["rp_98_01_aer"][k] - pre_s["rp_98_01_aer"][k] for k in pre_s["rp_98_01_aer"]},
            "card_edac_delta": {
                k: post_c[k] - pre_c[k]
                for k in pre_c if k.startswith("EDAC_") and isinstance(pre_c[k], int)
            },
        }
        env_doc["edac_aer_deltas"] = deltas
        all_zero_edac = (
            all(v == 0 for v in deltas["host_edac_ue_delta"].values())
            and all(v == 0 for v in deltas["host_rp_aer_delta"].values())
            and all(v == 0 for v in deltas["card_edac_delta"].values())
        )
        csrs_match = (post_c["csrs"] == orig_csrs)
        env_doc["csrs_restored_exact"] = csrs_match
        baseline_ok = (
            all_zero_edac
            and csrs_match
            and post_c.get("ETH0_OPER") == "up"
            and post_c.get("ETH0_SPEED") == "100000Mb/s"
            and post_c.get("PEM_WC") == 0
            and post_c.get("REFL") == "running"
            and post_c.get("HP_M217") == 0
            and post_s.get("hugepages_m217_leftover") == 0
            and post_s.get("cpu40_gov") == "performance"
            and post_s.get("cpu40_khz", 0) >= 3850000
            and all(v["cur"] == 2200000 for v in post_s["uncore_khz"].values())
            and post_env["mercury"].get("m217_procs") == 0
            and post_env["mercury"].get("tmp_mrv217") == 0
        )
        env_doc["baseline_restored_ok"] = baseline_ok
        ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n")
        log(f"DONE! wall={env_doc['campaign_wall_s']}s baseline_restored_ok={baseline_ok} csrs_restored_exact={csrs_match} deltas={deltas}")


if __name__ == "__main__":
    main()
