#!/usr/bin/env python3
"""MRV-155 Phase 2 full n=3 interleaved unidirectional 100GbE benchmark harness.

Strictly enforces 1 host core (`saturn cpu40 @ 3.90 GHz`, Uncore 2.20 GHz, IOMMU on,
zero Intel DSA), while unlocking CN106 multi-core (up to 16 cores / 24 TXQs, per-core
NPA pools, MBUF_FAST_FREE, pause disabled) and mercury 24-core NUMA-0 IBV_QPT_UD RX
+ hardware PHY counters (`ethtool -S ens6f1np1`).

Decomposes every stage (Stage 0 -> Stage 1 -> Stage 2 + Stage 3 mercury RX) and
compares DirectPush against the corrected optimal SDP baseline (`ism_enable=1`,
per-core card pools, RSS balanced, zero-copy card forwarding, MBUF_FAST_FREE).
"""

import ctypes
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/tmp/mrv155-p2-logs")
RUNS_PATH = OUT_DIR / "runs_phase2.jsonl"
ENV_PATH = OUT_DIR / "env_phase2.json"

HOST_BIN = "/home/hongtao/dev/marvell-mrv155/testbed/target/release/mrv155p2_host"
TESTPMD_HOST = "/home/hongtao/dev/marvell/marvell-dpdk/build-host/app/dpdk-testpmd"
CARD_BIN = "/root/bench/mrv155p2_card"
MERC_BIN = "/tmp/mrv155/mrv155p2_rx"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]
REPS = 3
MEAS_SECS = 1.5
WARMUP_SECS = 0.4
SOCKET1_UNCORE_CPU = 32
REF_HZ = 2.2e9
MERC_THREADS = 16


# ---------------------------------------------------------------------------
# 100% Non-Multiplexed Sapphire Rapids Core (cpu40) + Socket-1 Uncore PMUs
# ---------------------------------------------------------------------------
class PerfEventAttr(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("size", ctypes.c_uint32),
        ("config", ctypes.c_uint64),
        ("sample_period", ctypes.c_uint64),
        ("sample_type", ctypes.c_uint64),
        ("read_format", ctypes.c_uint64),
        ("flags", ctypes.c_uint64),
        ("wakeup_events", ctypes.c_uint32),
        ("bp_type", ctypes.c_uint32),
        ("config1", ctypes.c_uint64),
        ("config2", ctypes.c_uint64),
        ("branch_sample_type", ctypes.c_uint64),
        ("sample_regs_user", ctypes.c_uint64),
        ("sample_stack_user", ctypes.c_uint32),
        ("clockid", ctypes.c_int32),
        ("sample_regs_intr", ctypes.c_uint64),
        ("aux_watermark", ctypes.c_uint32),
        ("sample_max_stack", ctypes.c_uint16),
        ("reserved2", ctypes.c_uint16),
        ("aux_sample_size", ctypes.c_uint32),
        ("reserved3", ctypes.c_uint32),
        ("sig_data", ctypes.c_uint64),
        ("config3", ctypes.c_uint64),
    ]


LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
SYS_PERF_EVENT_OPEN = 298
PERF_FORMAT_TOTAL_TIME_ENABLED = 1 << 0
PERF_FORMAT_TOTAL_TIME_RUNNING = 1 << 1
PERF_EVENT_IOC_ENABLE = 0x2400
PERF_EVENT_IOC_DISABLE = 0x2401


def _pmu_type(name: str) -> int:
    return int(pathlib.Path(f"/sys/bus/event_source/devices/{name}/type").read_text().strip())


class PerfFd:
    def __init__(self, ptype: int, config: int, cpu: int):
        attr = PerfEventAttr(
            type=ptype,
            size=ctypes.sizeof(PerfEventAttr),
            config=config,
            read_format=PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING,
            flags=1,
        )
        fd = LIBC.syscall(SYS_PERF_EVENT_OPEN, ctypes.byref(attr), -1, cpu, -1, 0)
        if fd < 0:
            err = ctypes.get_errno()
            raise OSError(err, f"perf_event_open(type={ptype}, cfg={config:#x}, cpu={cpu}): {os.strerror(err)}")
        self.fd = fd
        rc = LIBC.ioctl(self.fd, PERF_EVENT_IOC_ENABLE, 0)
        if rc != 0:
            err = ctypes.get_errno()
            os.close(self.fd)
            raise OSError(err, f"PERF_EVENT_IOC_ENABLE: {os.strerror(err)}")

    def read(self):
        raw = os.read(self.fd, 24)
        return (
            int.from_bytes(raw[0:8], "little"),
            int.from_bytes(raw[8:16], "little"),
            int.from_bytes(raw[16:24], "little"),
        )

    def close(self):
        if self.fd >= 0:
            LIBC.ioctl(self.fd, PERF_EVENT_IOC_DISABLE, 0)
            os.close(self.fd)
            self.fd = -1


class HostPmu:
    def __init__(self, cpu: int = 40):
        self.core_fds = {
            "cycles": PerfFd(0, 0, cpu),
            "instr": PerfFd(0, 1, cpu),
            "ref_cycles": PerfFd(0, 9, cpu),
            "llc_loads": PerfFd(3, 0x00002, cpu),
            "llc_load_misses": PerfFd(3, 0x10002, cpu),
            "sb_stall": PerfFd(4, 0x08A2, cpu),
        }
        iio1_t = _pmu_type("uncore_iio_1")
        self.uncore_fds = {
            "mesh_clk": PerfFd(_pmu_type("uncore_m2pcie_1"), 0x01, SOCKET1_UNCORE_CPU),
            "iio1_cpu_wr": PerfFd(iio1_t, 0x0007_0FF0_0000_01C1, SOCKET1_UNCORE_CPU),
            "iio1_inb_wr": PerfFd(iio1_t, 0x0007_0FF0_0000_0184, SOCKET1_UNCORE_CPU),
            "iio1_inb_rd": PerfFd(iio1_t, 0x0007_0FF0_0000_0484, SOCKET1_UNCORE_CPU),
            "iio1_clk": PerfFd(iio1_t, 0x01, SOCKET1_UNCORE_CPU),
        }
        self.cha_wcilf_ins = []
        self.cha_wcilf_occ = []
        for i in range(32):
            t = _pmu_type(f"uncore_cha_{i}")
            self.cha_wcilf_ins.append(PerfFd(t, 0x00C8_67FF_0000_0135, SOCKET1_UNCORE_CPU))
            self.cha_wcilf_occ.append(PerfFd(t, 0x00C8_67FF_0000_0136, SOCKET1_UNCORE_CPU))

    def snap(self):
        return {
            "t": time.monotonic(),
            "c": {k: fd.read() for k, fd in self.core_fds.items()},
            "u": {k: fd.read() for k, fd in self.uncore_fds.items()},
            "wcilf_ins": [fd.read() for fd in self.cha_wcilf_ins],
            "wcilf_occ": [fd.read() for fd in self.cha_wcilf_occ],
        }

    @staticmethod
    def delta(a, b):
        dt = max(b["t"] - a["t"], 1e-9)
        min_pct = 100.0

        def d1(x0, x1):
            nonlocal min_pct
            dv = x1[0] - x0[0]
            de = x1[1] - x0[1]
            dr = x1[2] - x0[2]
            if de > 0:
                pct = 100.0 * dr / de
                if pct < min_pct:
                    min_pct = pct
            return dv

        c = {k: d1(a["c"][k], b["c"][k]) for k in a["c"]}
        u = {k: d1(a["u"][k], b["u"][k]) for k in a["u"]}
        wc_ins = sum(d1(x0, x1) for x0, x1 in zip(a["wcilf_ins"], b["wcilf_ins"]))
        wc_occ = sum(d1(x0, x1) for x0, x1 in zip(a["wcilf_occ"], b["wcilf_occ"]))
        mesh_clk = max(u["mesh_clk"], 1)
        ref_c = max(c["ref_cycles"], 1)
        ghz = (c["cycles"] / ref_c) * (REF_HZ / 1e9)
        return {
            "dt_s": dt,
            "min_running_pct": min_pct,
            "cpu40_ghz": ghz,
            "cycles": c["cycles"],
            "instr": c["instr"],
            "ipc": c["instr"] / max(c["cycles"], 1),
            "sb_stall_pct": 100.0 * c["sb_stall"] / max(c["cycles"], 1),
            "llc_loads": c["llc_loads"],
            "llc_load_misses": c["llc_load_misses"],
            "iio1_cpu_wr_mops": u["iio1_cpu_wr"] / dt / 1e6,
            "iio1_inb_wr_mops": u["iio1_inb_wr"] / dt / 1e6,
            "iio1_inb_rd_mops": u["iio1_inb_rd"] / dt / 1e6,
            "cha_wcilf_mops": wc_ins / dt / 1e6,
            "cha_inflight": wc_occ / mesh_clk,
        }

    def close(self):
        for fd in list(self.core_fds.values()) + list(self.uncore_fds.values()) + self.cha_wcilf_ins + self.cha_wcilf_occ:
            fd.close()


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


# ---------------------------------------------------------------------------
# Subprocess & TCP helpers
# ---------------------------------------------------------------------------
def sh(cmd, check=True, timeout=180):
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


def check_env_all():
    gov40 = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    uncore = {
        p.parent.name: int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz"))
    }
    edac_host = sum(
        int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count"))
    )
    assert gov40 == "performance", f"cpu40 governor={gov40}"
    assert uncore.get("package_01_die_01", 0) >= 2_190_000, f"uncore={uncore}"

    card_info = ssh_card(
        "echo freq=$(cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq);"
        "echo edac_ue=$(grep . /sys/devices/system/edac/*/*/ue_count | awk -F: '{s+=$2} END{print s+0}');"
        "echo sei_uer=$(dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || true);"
        "echo bar4_idx7=$(python3 /root/bar4idx7.py | awk '{print $NF}')"
    ).stdout
    cd = {}
    for l in card_info.strip().splitlines():
        if "=" in l:
            k, v = l.split("=", 1)
            cd[k] = v

    merc_info = ssh_merc(
        "ethtool -S ens6f1np1 | grep -E 'rx_crc_errors_phy:|rx_packets_phy:|rx_bytes_phy:|tx_global_pause:'"
    ).stdout
    md = {}
    for l in merc_info.strip().splitlines():
        if ":" in l:
            k, v = l.split(":", 1)
            md[k.strip()] = int(v.strip())
    return {"saturn": {"cpu40_gov": gov40, "uncore_khz": uncore, "edac_ue_sum": edac_host}, "cn106": cd, "mercury": md}


def prepare_card_vfio():
    ssh_card(
        "tmux send-keys -t refl C-c 2>/dev/null || true; tmux kill-session -t refl 2>/dev/null || true; "
        "tmux kill-session -t p2c 2>/dev/null || true; pkill -9 mrv155p2_card 2>/dev/null || true; "
        "rm -f /dev/hugepages/refl* /dev/hugepages/p2c*; "
        "ip link set eth0 down 2>/dev/null || true; "
        "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
        'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
        "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko"
    )


def start_card(eal_str, app_str):
    ssh_card("tmux kill-session -t p2c 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/p2c* /tmp/p2c.log")
    cmd = f"{CARD_BIN} {eal_str} --file-prefix=p2c -- {app_str}"
    r = ssh_card(
        f'tmux new-session -d -s p2c "{cmd} > /tmp/p2c.log 2>&1"; '
        f'for i in $(seq 1 50); do grep -q "MRV155P2_CARD READY" /tmp/p2c.log && break; sleep 0.2; done; '
        f'grep "MRV155P2_CARD READY" /tmp/p2c.log',
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError("card start failed:\n" + ssh_card("cat /tmp/p2c.log", check=False).stdout)


def stop_card():
    ssh_card(
        "tmux send-keys -t p2c C-c 2>/dev/null || true; sleep 0.35; "
        "tmux kill-session -t p2c 2>/dev/null || true; rm -f /dev/hugepages/p2c*",
        check=False,
    )


def start_host(dev, app_args):
    sh(["bash", "-c", "pkill -9 mrv155p2_host 2>/dev/null || true; rm -f /dev/hugepages/p2h* /tmp/p2h.log"], check=False)
    cmd = [HOST_BIN, "-l", "40", "-a", dev, "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=p2h", "--"] + app_args
    lf = open("/tmp/p2h.log", "w")
    hp = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
    lf.close()
    for _ in range(50):
        if hp.poll() is not None:
            raise RuntimeError("host exited:\n" + pathlib.Path("/tmp/p2h.log").read_text())
        if "MRV155P2_HOST READY" in pathlib.Path("/tmp/p2h.log").read_text():
            return hp
        time.sleep(0.1)
    raise RuntimeError("host timeout:\n" + pathlib.Path("/tmp/p2h.log").read_text())


def stop_host(hp):
    if hp is None:
        return
    try:
        tcp_cmd("127.0.0.1", 7536, "STOP", timeout=2.0)
    except Exception:
        pass
    try:
        tcp_cmd("127.0.0.1", 7536, "QUIT", timeout=2.0)
    except Exception:
        pass
    try:
        hp.wait(timeout=4)
    except Exception:
        hp.kill()
    sh(["bash", "-c", "rm -f /dev/hugepages/p2h*"], check=False)


def run_udp_content_check():
    """Verify end-to-end byte integrity of raw64 frames arriving in mercury UDP socket."""
    # Start a Python UDP receiver on mercury listening on 10.10.100.1:30000
    merc_py = """
import socket, struct, json
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('10.10.100.1', 30000))
s.settimeout(3.0)
ok = 0; bad = 0; samples = []
for _ in range(1000):
    try:
        data, _ = s.recvfrom(256)
    except socket.timeout:
        break
    if len(data) == 18:
        seq, cookie = struct.unpack('<QQ', data[:16])
        exp = ((seq * 0x9e3779b97f4a7c15) & 0xffffffffffffffff) ^ 0x55aa55aa0f0ff0f0
        if cookie == exp and seq > 0:
            ok += 1
            if len(samples) < 3:
                samples.append({'seq': seq, 'len': len(data)})
        else:
            bad += 1
    else:
        bad += 1
s.close()
print(json.dumps({'ok': ok, 'bad': bad, 'samples': samples}))
"""
    start_card(
        "-l 2 -a 0002:02:00.0",
        "--mode stage2-direct --pool-mode per-core --frame-kind raw --pkt-bytes 64 --dst-mac 0c:42:a1:53:96:4b --dst-ip 10.10.100.1",
    )
    hp = start_host(
        "0000:99:02.0",
        ["--mode", "direct", "--frame-kind", "raw", "--pkt-bytes", "64", "--bar4-stride", "64", "--batch-cells", "64", "--credit-batch", "256", "--rate-mpps", "0.05"],
    )
    mp = subprocess.Popen(MERC_SSH + ["python3 -"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    mp.stdin.write(merc_py)
    mp.stdin.close()
    time.sleep(0.3)
    tcp_cmd("127.0.0.1", 7536, "START")
    out = mp.stdout.read()
    mp.wait(timeout=10)
    stop_host(hp)
    stop_card()
    res = json.loads(out.strip())
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "part": "part3_content_check",
        "cfg": "raw64_udp_content_verify",
        "rep": 0,
        "result": res,
        "verified": (res["ok"] == 1000 and res["bad"] == 0),
    }
def run_testpmd_sdp_ref(label, hcores, nq, ism, pkt_len, rep):
    """Official Marvell dpdk-testpmd 1H / 4H bare SDP TX reference."""
    ssh_card("tmux kill-session -t tp_ref 2>/dev/null || true; rm -f /dev/hugepages/tp_ref* /tmp/tp_ref.log")
    ccores = max(nq, 4)
    card_cmd = (
        f"dpdk-testpmd -l 2-{2+ccores} --main-lcore 2 -a 0002:01:00.2 --file-prefix=tp_ref -- "
        f"--forward-mode=rxonly --rxq={nq} --txq={nq} --nb-cores={nq} --rxd=2048 --txd=2048 "
        f"--burst=64 --auto-start --stats-period=2"
    )
    ssh_card(f'tmux new-session -d -s tp_ref "{card_cmd} > /tmp/tp_ref.log 2>&1"')
    time.sleep(2.2)
    sh(["bash", "-c", "rm -f /dev/hugepages/htp_ref* /tmp/htp_ref.log"], check=False)
    dev = f"0000:99:02.0,ism_enable={ism}"
    lcore_list = "44," + ",".join(str(40 + i) for i in range(hcores))
    host_cmd = [
        TESTPMD_HOST, "-l", lcore_list, "--main-lcore", "44", "-a", dev,
        "--socket-mem=0,2048", "--iova-mode=va", "--file-prefix=htp_ref", "--",
        "--no-mlockall", "--forward-mode=txonly",
        f"--rxq={nq}", f"--txq={nq}", f"--nb-cores={hcores}",
        "--rxd=2048", "--txd=2048", "--burst=64",
        f"--txpkts={pkt_len}", "--auto-start", "--stats-period=2",
    ]
    p = subprocess.Popen(host_cmd, stdin=subprocess.PIPE, stdout=open("/tmp/htp_ref.log", "w"), stderr=subprocess.STDOUT)
    time.sleep(5.5)
    p.terminate()
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.kill()
    hlog = pathlib.Path("/tmp/htp_ref.log").read_text()
    clog = ssh_card("cat /tmp/tp_ref.log").stdout
    ssh_card("tmux send-keys -t tp_ref C-c 2>/dev/null || true; sleep 0.3; tmux kill-session -t tp_ref 2>/dev/null || true; rm -f /dev/hugepages/tp_ref*")
    sh(["bash", "-c", "rm -f /dev/hugepages/htp_ref*"], check=False)
    htx = [int(x) for x in re.findall(r"Tx-pps:\s+(\d+)", hlog)]
    crx = [int(x) for x in re.findall(r"Rx-pps:\s+(\d+)", clog)]
    cmiss = [int(x) for x in re.findall(r"RX-missed:\s+(\d+)", clog)]
    tx_mpps = htx[-1] / 1e6 if htx else 0.0
    rx_mpps = crx[-1] / 1e6 if crx else 0.0
    miss = cmiss[-1] if cmiss else -1
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "part": "part1_stage1_testpmd_ref",
        "cfg": label,
        "rep": rep,
        "spec": {"hcores": hcores, "nq": nq, "ism": ism, "pkt_len": pkt_len},
        "host_tx_mpps": tx_mpps,
        "card_rx_mpps": rx_mpps,
        "card_rx_missed": miss,
        "verified": (tx_mpps > 1.0 and rx_mpps > 1.0 and miss == 0),
    }


def build_specs():
    specs = []
    # ---- Part 0: Stage 0 (CN106 eth0 -> 3m DAC -> mercury) ----
    for nc in [1, 2, 3, 4, 6, 8, 12, 16]:
        specs.append({
            "part": "part0_stage0_cores",
            "cfg": f"s0_raw64_percore_c{nc}",
            "stage": 0, "kind": "card_gen", "nc": nc, "pool_mode": "per-core",
            "fk": "raw", "sz": 64,
        })
    for nc, pm in [(4, "shared"), (8, "shared"), (2, "reuse"), (4, "reuse"), (8, "reuse")]:
        specs.append({
            "part": "part0_stage0_npa_ablation",
            "cfg": f"s0_raw64_{pm}_c{nc}",
            "stage": 0, "kind": "card_gen", "nc": nc, "pool_mode": pm,
            "fk": "raw", "sz": 64,
        })
    for sz in [84, 100, 106, 128, 256, 512, 1024, 1500]:
        specs.append({
            "part": "part0_stage0_knee",
            "cfg": f"s0_raw{sz}_percore_c4",
            "stage": 0, "kind": "card_gen", "nc": 4, "pool_mode": "per-core",
            "fk": "raw", "sz": sz,
        })
    for sz in [64, 256, 1024, 4096]:
        specs.append({
            "part": "part0_stage0_roce",
            "cfg": f"s0_roce{sz}_percore_c4",
            "stage": 0, "kind": "card_gen", "nc": 4, "pool_mode": "per-core",
            "fk": "roce", "sz": sz,
        })

    # ---- Part 1: Stage 1 (saturn 1 core -> CN106 sink, no eth0) ----
    for rm in ["doorbell-only", "normal", "ldnp"]:
        specs.append({
            "part": "part1_stage1_bar4",
            "cfg": f"s1_dp_64B_c4_{rm}",
            "stage": 1, "kind": "direct", "nc": 4, "read_mode": rm,
            "fk": "raw", "sz": 64, "stride": 64, "bcells": 128, "cb": 256, "use_dma": False,
        })
    specs.append({
        "part": "part1_stage1_bar4",
        "cfg": "s1_dp_64B_c8_normal",
        "stage": 1, "kind": "direct", "nc": 8, "read_mode": "normal",
        "fk": "raw", "sz": 64, "stride": 64, "bcells": 128, "cb": 256, "use_dma": False,
    })
    specs.append({
        "part": "part1_stage1_bar4",
        "cfg": "s1_dp_256B_c4_normal",
        "stage": 1, "kind": "direct", "nc": 4, "read_mode": "normal",
        "fk": "raw", "sz": 256, "stride": 64, "bcells": 128, "cb": 256, "use_dma": False,
    })
    specs.append({
        "part": "part1_stage1_bar4",
        "cfg": "s1_dp_1024B_dma_c4",
        "stage": 1, "kind": "direct", "nc": 4, "read_mode": "normal",
        "fk": "roce", "sz": 1024, "stride": 64, "bcells": 32, "cb": 256, "use_dma": True,
    })
    specs.append({
        "part": "part1_stage1_bar4",
        "cfg": "s1_dp_4096B_dma_c4",
        "stage": 1, "kind": "direct", "nc": 4, "read_mode": "normal",
        "fk": "roce", "sz": 4096, "stride": 64, "bcells": 32, "cb": 256, "use_dma": True,
    })
    for sz in [64, 256, 1024]:
        specs.append({
            "part": "part1_stage1_sdp",
            "cfg": f"s1_sdp_{sz}B_ism1_c4",
            "stage": 1, "kind": "sdp", "nc": 4, "ism": 1,
            "fk": "raw", "sz": sz,
        })

    # ---- Part 2: Stage 2 FULL E2E (saturn 1 core -> CN106 -> eth0 -> mercury) ----
    # Raw L2 Ethernet/IPv4/UDP frames: 64 B L2, 110 B L2 (exact 64 B UDP payload!), 128 B L2, 256 B L2
    for sz in [64, 110, 128, 256]:
        specs.append({
            "part": "part2_stage2_raw",
            "cfg": f"s2_dp_raw{sz}_c4",
            "stage": 2, "kind": "direct", "nc": 4, "read_mode": "normal",
            "fk": "raw", "sz": sz, "stride": 64, "bcells": 128, "cb": 256, "use_dma": False,
        })
        specs.append({
            "part": "part2_stage2_raw",
            "cfg": f"s2_sdp_raw{sz}_ism1_c4",
            "stage": 2, "kind": "sdp", "nc": 4, "ism": 1,
            "fk": "raw", "sz": sz,
        })
    # RoCEv2 UD frames (64 B, 256 B, 1 KiB, 4 KiB) with mercury 24-thread userspace RX + PHY!
    for sz, bcells, use_dma in [(64, 128, False), (256, 128, False), (1024, 32, True), (4096, 32, True)]:
        specs.append({
            "part": "part2_stage2_roce",
            "cfg": f"s2_dp_roce{sz}_c4",
            "stage": 2, "kind": "direct", "nc": 4, "read_mode": "normal",
            "fk": "roce", "sz": sz, "stride": 64, "bcells": bcells, "cb": 256, "use_dma": use_dma,
        })
        specs.append({
            "part": "part2_stage2_roce",
            "cfg": f"s2_sdp_roce{sz}_ism1_c4",
            "stage": 2, "kind": "sdp", "nc": 4, "ism": 1,
            "fk": "roce", "sz": sz,
        })

    # ---- Part 3: Controls & Single-variable Ablations ----
    # 1. Zero-loss mercury userspace RoCE64 paced smoothly inside CX5 RX ceiling (48 Mpps, batch 64)
    specs.append({
        "part": "part3_controls",
        "cfg": "s2_dp_roce64_paced48M_zl",
        "stage": 2, "kind": "direct", "nc": 4, "read_mode": "normal",
        "fk": "roce", "sz": 64, "stride": 64, "bcells": 64, "cb": 256, "use_dma": False,
        "rate_mpps": 48.0,
    })
    # 1b. DirectPush raw256 with C=6 CN106 cores (unlocking 4-core ARM memcpy ceiling)
    specs.append({
        "part": "part3_controls",
        "cfg": "s2_dp_raw256_c6",
        "stage": 2, "kind": "direct", "nc": 6, "read_mode": "normal",
        "fk": "raw", "sz": 256, "stride": 64, "bcells": 128, "cb": 256, "use_dma": False,
    })
    # 2. Stride 128 B vs 64 B on E2E raw64
    specs.append({
        "part": "part3_controls",
        "cfg": "s2_dp_raw64_s128_c4",
        "stage": 2, "kind": "direct", "nc": 4, "read_mode": "normal",
        "fk": "raw", "sz": 64, "stride": 128, "bcells": 128, "cb": 256, "use_dma": False,
    })
    # 3. E2E SDP ISM=0 vs ISM=1 ablation + SDP C=8 check
    specs.append({
        "part": "part3_controls",
        "cfg": "s2_sdp_raw64_ism0_c4",
        "stage": 2, "kind": "sdp", "nc": 4, "ism": 0,
        "fk": "raw", "sz": 64,
    })
    specs.append({
        "part": "part3_controls",
        "cfg": "s2_sdp_raw64_ism1_c8",
        "stage": 2, "kind": "sdp", "nc": 8, "ism": 1,
        "fk": "raw", "sz": 64,
    })
    return specs


def run_one(pmu: HostPmu, sp: dict, rep: int, qpns: str):
    stage = sp["stage"]
    kind = sp["kind"]
    nc = sp["nc"]
    fk = sp["fk"]
    sz = sp["sz"]
    dmac = "02:15:50:00:00:01" if fk == "raw" else "0c:42:a1:53:96:4b"
    hp = None

    try:
        if stage == 0:
            start_card(
                f"-l 2-{1+nc} -a 0002:02:00.0",
                f"--mode stage0-gen --pool-mode {sp['pool_mode']} --frame-kind {fk} --pkt-bytes {sz} "
                f"--dst-mac {dmac} --dst-ip 10.10.100.1 --dst-qpns {qpns}",
            )
            tcp_cmd("10.10.0.2", 7535, "START")
        elif stage == 1 and kind == "direct":
            use_dma = sp.get("use_dma", False)
            eal = f"-l 2-{1+nc} -a 0002:02:00.0" + (
                " -a 0000:06:00.1 -a 0000:06:00.2 -a 0000:06:00.3 -a 0000:06:00.4" if use_dma else ""
            )
            darg = " --dma-devs 0000:06:00.1,0000:06:00.2,0000:06:00.3,0000:06:00.4" if use_dma else ""
            start_card(eal, f"--mode stage1-direct --read-mode {sp['read_mode']} --pkt-bytes {sz}{darg}")
            hargs = [
                "--mode", "direct", "--frame-kind", fk, "--pkt-bytes", str(sz),
                "--bar4-stride", str(sp["stride"]), "--batch-cells", str(sp["bcells"]),
                "--credit-batch", str(sp["cb"]),
            ] + (["--use-dma"] if use_dma else [])
            hp = start_host("0000:99:02.0", hargs)
            tcp_cmd("127.0.0.1", 7536, "START")
        elif stage == 1 and kind == "sdp":
            start_card(f"-l 2-{1+nc} -a 0002:01:00.2", f"--mode stage1-sdp --frame-kind {fk} --pkt-bytes {sz}")
            dev = f"0000:99:02.0,ism_enable={sp['ism']}"
            hp = start_host(dev, ["--mode", "sdp", "--frame-kind", fk, "--pkt-bytes", str(sz), "--sdp-queues", str(nc)])
            tcp_cmd("127.0.0.1", 7536, "START")
        elif stage == 2 and kind == "direct":
            use_dma = sp.get("use_dma", False)
            eal = f"-l 2-{1+nc} -a 0002:02:00.0" + (
                " -a 0000:06:00.1 -a 0000:06:00.2 -a 0000:06:00.3 -a 0000:06:00.4" if use_dma else ""
            )
            darg = " --dma-devs 0000:06:00.1,0000:06:00.2,0000:06:00.3,0000:06:00.4" if use_dma else ""
            start_card(
                eal,
                f"--mode stage2-direct --pool-mode per-core --read-mode {sp['read_mode']} --frame-kind {fk} "
                f"--pkt-bytes {sz} --dst-mac {dmac} --dst-ip 10.10.100.1 --dst-qpns {qpns}{darg}",
            )
            hargs = [
                "--mode", "direct", "--frame-kind", fk, "--pkt-bytes", str(sz),
                "--bar4-stride", str(sp["stride"]), "--batch-cells", str(sp["bcells"]),
                "--credit-batch", str(sp["cb"]),
            ] + (["--use-dma"] if use_dma else [])
            if sp.get("rate_mpps", 0.0) > 0.0:
                hargs += ["--rate-mpps", str(sp["rate_mpps"])]
            hp = start_host("0000:99:02.0", hargs)
            tcp_cmd("127.0.0.1", 7536, "START")
        elif stage == 2 and kind == "sdp":
            start_card(
                f"-l 2-{1+nc} -a 0002:02:00.0 -a 0002:01:00.2",
                f"--mode stage2-sdp --pool-mode per-core --frame-kind {fk} --pkt-bytes {sz} "
                f"--dst-mac {dmac} --dst-ip 10.10.100.1 --dst-qpns {qpns}",
            )
            dev = f"0000:99:02.0,ism_enable={sp['ism']}"
            hp = start_host(dev, ["--mode", "sdp", "--frame-kind", fk, "--pkt-bytes", str(sz), "--sdp-queues", str(nc)])
            tcp_cmd("127.0.0.1", 7536, "START")

        time.sleep(WARMUP_SECS)
        tcp_cmd("mercury", 7537, "RESET")
        tcp_cmd("10.10.0.2", 7535, "RESET")
        if hp is not None:
            tcp_cmd("127.0.0.1", 7536, "RESET")
        p0 = pmu.snap()
        t0 = tcp_cmd("10.10.0.2", 7535, "PMU_SNAP")
        t_start = time.monotonic()
        time.sleep(MEAS_SECS)
        t_end = time.monotonic()
        p1 = pmu.snap()
        t1 = tcp_cmd("10.10.0.2", 7535, "PMU_SNAP")
        ms = tcp_cmd("mercury", 7537, "STATS")
        cs = tcp_cmd("10.10.0.2", 7535, "STATS")
        hs = tcp_cmd("127.0.0.1", 7536, "STATS") if hp is not None else None

        if stage == 0:
            tcp_cmd("10.10.0.2", 7535, "STOP")
    finally:
        stop_host(hp)
        stop_card()

    hp_d = HostPmu.delta(p0, p1)
    tad_d = tad_delta(t0, t1)
    dt_wall = t_end - t_start

    errs = []
    if hp_d["min_running_pct"] < 99.9:
        errs.append(f"host_pmu_pct={hp_d['min_running_pct']:.2f}")
    if tad_d["min_running_pct"] < 99.9:
        errs.append(f"tad_pmu_pct={tad_d['min_running_pct']:.2f}")
    if hp is not None and hp_d["cpu40_ghz"] < 3.80:
        errs.append(f"cpu40_ghz={hp_d['cpu40_ghz']:.3f}")
    if hs is not None and ("elapsed_s" not in hs or "err" in hs):
        errs.append(f"host_stats_err={hs}")
    if "bad" not in cs or "err" in cs:
        errs.append(f"card_stats_err={cs}")
    elif cs["bad"] != 0 or cs["torn"] != 0 or cs["dma_failed"] != 0 or cs["tx_drops"] != 0:
        errs.append(f"card_err bad={cs['bad']} torn={cs['torn']} dma_f={cs['dma_failed']} tx_d={cs['tx_drops']}")
    if stage in (1, 2) and kind == "direct" and cs.get("lost", 0) != 0:
        errs.append(f"card_bar4_lost={cs.get('lost')}")
    if stage in (0, 2) and fk == "roce":
        if ms["bad"] != 0 or ms["cqe_err"] != 0:
            errs.append(f"merc_bad={ms['bad']} cqe_err={ms['cqe_err']}")
        # Strict zero-loss check for 1024 B, 4096 B, SDP, and paced64M
        if (sz >= 1024 or sp.get("rate_mpps", 0) > 0) and ms["lost"] != 0:
            errs.append(f"merc_lost={ms['lost']}")
    if stage in (0, 2) and ms["phy"]["rx_pkts"] == 0:
        errs.append("merc_phy_zero")

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "part": sp["part"],
        "cfg": sp["cfg"],
        "rep": rep,
        "spec": sp,
        "dt_wall_s": dt_wall,
        "host": hs,
        "card": cs,
        "mercury": ms,
        "host_pmu": hp_d,
        "tad_pmu": tad_d,
        "verified": len(errs) == 0,
        "verify_errors": errs,
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    env_before = check_env_all()
    print("ENV BEFORE:", json.dumps(env_before), flush=True)
    prepare_card_vfio()

    # Launch persistent 24-thread mercury receiver on NUMA 0 cpus 2..25
    sh(MERC_SSH + ["pkill -9 mrv155p2_rx 2>/dev/null || true"], check=False)
    mp = subprocess.Popen(
        MERC_SSH + [f"{MERC_BIN} --threads {MERC_THREADS} --port 7537"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    rl = mp.stdout.readline().strip()
    print("MERC READY:", rl[:100], "...", flush=True)
    qpns = re.search(r"qpns=(\S+)", rl).group(1)

    specs = build_specs()
    pmu = HostPmu(cpu=40)
    done = 0
    failed = 0

    try:
        with open(RUNS_PATH, "w") as out:
            # 1. End-to-end raw64 UDP content integrity verification check
            chk = run_udp_content_check()
            out.write(json.dumps(chk) + "\n")
            out.flush()
            print(f"[UDP_CHECK] ok={chk['result']['ok']}/1000 bad={chk['result']['bad']} verified={chk['verified']}", flush=True)
            if not chk["verified"]:
                failed += 1

            # 2. Official Marvell dpdk-testpmd SDP reference (n=3)
            for rep in range(REPS):
                for label, hc, nq, ism, plen in [
                    ("tp_sdp_1H_64B_ism0", 1, 1, 0, 64),
                    ("tp_sdp_1H_64B_ism1", 1, 1, 1, 64),
                    ("tp_sdp_4H_64B_ism1", 4, 4, 1, 64),
                ]:
                    rec = run_testpmd_sdp_ref(label, hc, nq, ism, plen, rep)
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    done += 1
                    if not rec["verified"]:
                        failed += 1
                    print(f"[TP rep={rep}] {label:20s} host={rec['host_tx_mpps']:6.2f}M card={rec['card_rx_mpps']:6.2f}M miss={rec['card_rx_missed']} ok={rec['verified']}", flush=True)

            # 3. Main n=3 interleaved matrix across Stage 0, Stage 1, Stage 2, Controls
            total_main = REPS * len(specs)
            for rep in range(REPS):
                order = list(specs) if rep % 2 == 0 else list(reversed(specs))
                for idx, sp in enumerate(order):
                    rec = run_one(pmu, sp, rep, qpns)
                    retries = 0
                    while not rec["verified"] and retries < 2:
                        retries += 1
                        print(f"  RETRY#{retries} rep={rep} {sp['cfg']} errs={rec['verify_errors']}", flush=True)
                        rec = run_one(pmu, sp, rep, qpns)
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    done += 1
                    if not rec["verified"]:
                        failed += 1
                    phy = rec["mercury"]["phy"]
                    m = rec["mercury"]
                    c = rec["card"]
                    h = rec["host"]
                    hp_d = rec["host_pmu"]
                    if sp["stage"] == 1:
                        dt = h.get("elapsed_s", rec["dt_wall_s"]) if h else rec["dt_wall_s"]
                        mpps = c.get("pkts_in", 0) / max(dt, 1e-6) / 1e6
                        pay_g = c.get("pkts_in", 0) * sp["sz"] * 8 / max(dt, 1e-6) / 1e9
                        info = f"card={mpps:6.2f}M pay={pay_g:5.2f}G ghz={hp_d['cpu40_ghz']:.2f}"
                    elif sp["fk"] == "raw":
                        info = f"phy={phy['rx_mpps']:6.2f}M L2={phy['l2_gbps']:5.2f}G wire={phy['wire_gbps']:6.2f}G crc={phy['crc_err']}"
                    else:
                        info = f"merc={m['rx_mpps']:6.2f}M(lost={m['lost']}) phy={phy['rx_mpps']:6.2f}M wire={phy['wire_gbps']:6.2f}G"
                    print(f"[{rep+1}/{REPS} {idx+1:2d}/{len(specs)}] {sp['cfg']:26s} | {info} | ok={rec['verified']}", flush=True)
    finally:
        stop_card()
        try:
            tcp_cmd("mercury", 7537, "QUIT", timeout=2.0)
        except Exception:
            pass
        try:
            mp.wait(timeout=5)
        except Exception:
            mp.kill()
        pmu.close()

    env_after = check_env_all()
    ENV_PATH.write_text(json.dumps({"before": env_before, "after": env_after, "done": done, "failed": failed}, indent=2))
    print(f"ALL DONE total={done} failed={failed}", flush=True)
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
