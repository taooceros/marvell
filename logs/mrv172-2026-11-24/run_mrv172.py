#!/usr/bin/python3
"""MRV-172 full n=3 interleaved unidirectional 100GbE RX benchmark harness.

Mirror of MRV-155 Phase 2 (TX) in the reverse RX direction:
  mercury multi-core TX -> 3m 100GbE DAC -> CN106 multi-core RX -> saturn STRICTLY 1 core (cpu40 @ 3.90 GHz)

Strict audit & accounting guarantees (addressing every MRV-155 audit finding):
  1. Zero silent retries: EVERY hardware run is permanently appended to `attempts.jsonl` (never overwritten).
     Integrity/PMU errors are NEVER retried (0 retries); ONLY shared-host Socket-1 clock/mesh contention
     (`cpu40_ghz < 3.85` or `mesh_ghz < 2.18`) may wait for host quiescence and re-measure transparently,
     with both rejected and accepted attempts archived in `attempts.jsonl` and `clock_retries_before_accept` recorded.
  2. Synchronized timing windows: `mercury` reads `ens6f1np1` PHY counters via in-process ETHTOOL_GSTATS
     ioctl with its own exact `dt_phy_s` separated from `dt_app_s` (zero +10 ms subprocess bias).
  3. Untouched end-to-end payload verification: `mercury` writes `[seq, cookie, meta, ..., tail]` into every
     UD frame (and HW 24-bit BTH.PSN + ICRC + FCS for exact 64 B L2 UC frames); CN106 forwards untouched
     and `saturn` verifies `mercury`'s actual bytes with genuine incremented `bad`, `torn`, `late`, and
     per-segment loss (`eth0 imissed/xstats`, `wire_lost`, `reasm_drops`, `bp_drops`, `c2h_lost`, `merc_stream_lost`).
  4. 100.000% non-multiplexed Host (92 counters: cpu40 core + IIO1 inb_wr/dw/rd/cpu_wr + 32x CHA DDIO
     io_hit/miss_itom + 8x IMC DRAM rd/wr + mesh_clk) and Card (TAD LLC + 6x mrvl_ddr_pmu) PMUs.
"""

import argparse
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

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv172")
LOG_DIR = ROOT / "logs/mrv172-2026-11-24"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = "/tmp/mrv172-target/release/mrv172_host"
TESTPMD_HOST = "/home/hongtao/dev/marvell/marvell-dpdk/build-host/app/dpdk-testpmd"
CARD_BIN = "/root/bench/mrv172_card"
MERC_BIN = "/tmp/mrv172/mrv172_tx"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]

REPS = 3
WARMUP_SECS = 0.4
MEAS_SECS = 1.5
SOCKET1_UNCORE_CPU = 32
REF_HZ = 2.2e9


# ---------------------------------------------------------------------------
# 100% Non-Multiplexed Sapphire Rapids Host PMU (92 counters)
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
        if LIBC.ioctl(self.fd, PERF_EVENT_IOC_ENABLE, 0) != 0:
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
            "page_faults": PerfFd(1, 2, cpu),
        }
        iio1_t = _pmu_type("uncore_iio_1")
        self.uncore_fds = {
            "mesh_clk": PerfFd(_pmu_type("uncore_m2pcie_1"), 0x01, SOCKET1_UNCORE_CPU),
            "iio1_inb_wr": PerfFd(iio1_t, 0x0007_0FF0_0000_0184, SOCKET1_UNCORE_CPU),
            "iio1_inb_wr_dw": PerfFd(iio1_t, 0x0007_0FF0_0000_0183, SOCKET1_UNCORE_CPU),
            "iio1_inb_rd": PerfFd(iio1_t, 0x0007_0FF0_0000_0484, SOCKET1_UNCORE_CPU),
            "iio1_cpu_wr": PerfFd(iio1_t, 0x0007_0FF0_0000_01C1, SOCKET1_UNCORE_CPU),
        }
        self.cha_hit = []
        self.cha_miss = []
        for i in range(32):
            t = _pmu_type(f"uncore_cha_{i}")
            self.cha_hit.append(PerfFd(t, 0x00CC_43FD_0000_0435, SOCKET1_UNCORE_CPU))
            self.cha_miss.append(PerfFd(t, 0x00CC_43FE_0000_0435, SOCKET1_UNCORE_CPU))
        self.imc_rd = []
        self.imc_wr = []
        for i in range(8):
            t = _pmu_type(f"uncore_imc_{i}")
            self.imc_rd.append(PerfFd(t, 0xCF05, SOCKET1_UNCORE_CPU))
            self.imc_wr.append(PerfFd(t, 0xF005, SOCKET1_UNCORE_CPU))

    def snap(self):
        return {
            "t": time.monotonic(),
            "c": {k: fd.read() for k, fd in self.core_fds.items()},
            "u": {k: fd.read() for k, fd in self.uncore_fds.items()},
            "cha_hit": [fd.read() for fd in self.cha_hit],
            "cha_miss": [fd.read() for fd in self.cha_miss],
            "imc_rd": [fd.read() for fd in self.imc_rd],
            "imc_wr": [fd.read() for fd in self.imc_wr],
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
        cha_hit = sum(d1(x0, x1) for x0, x1 in zip(a["cha_hit"], b["cha_hit"]))
        cha_miss = sum(d1(x0, x1) for x0, x1 in zip(a["cha_miss"], b["cha_miss"]))
        imc_rd = sum(d1(x0, x1) for x0, x1 in zip(a["imc_rd"], b["imc_rd"]))
        imc_wr = sum(d1(x0, x1) for x0, x1 in zip(a["imc_wr"], b["imc_wr"]))

        ref_c = max(c["ref_cycles"], 1)
        ghz = (c["cycles"] / ref_c) * (REF_HZ / 1e9)
        mesh_ghz = (u["mesh_clk"] / dt) / 1e9
        inb_wr = u["iio1_inb_wr"]
        avg_wr_bytes = (4.0 * u["iio1_inb_wr_dw"] / inb_wr) if inb_wr > 0 else 0.0
        ddio_tot = cha_hit + cha_miss
        ddio_hit_pct = (100.0 * cha_hit / ddio_tot) if ddio_tot > 0 else 100.0
        return {
            "dt_s": dt,
            "min_running_pct": min_pct,
            "cpu40_ghz": ghz,
            "mesh_ghz": mesh_ghz,
            "cycles": c["cycles"],
            "instr": c["instr"],
            "ipc": c["instr"] / max(c["cycles"], 1),
            "sb_stall_pct": 100.0 * c["sb_stall"] / max(c["cycles"], 1),
            "llc_loads": c["llc_loads"],
            "llc_load_misses": c["llc_load_misses"],
            "page_faults": c["page_faults"],
            "iio1_inb_wr_mops": inb_wr / dt / 1e6,
            "iio1_inb_wr_gbps": (u["iio1_inb_wr_dw"] * 4.0 * 8.0) / dt / 1e9,
            "iio1_inb_wr_avg_bytes": avg_wr_bytes,
            "iio1_inb_rd_mops": u["iio1_inb_rd"] / dt / 1e6,
            "iio1_cpu_wr_mops": u["iio1_cpu_wr"] / dt / 1e6,
            "cha_io_hit_itom": cha_hit,
            "cha_io_miss_itom": cha_miss,
            "ddio_hit_pct": ddio_hit_pct,
            "imc_rd_mops": imc_rd / dt / 1e6,
            "imc_wr_mops": imc_wr / dt / 1e6,
            "imc_rd_gbps": (imc_rd * 64.0 * 8.0) / dt / 1e9,
            "imc_wr_gbps": (imc_wr * 64.0 * 8.0) / dt / 1e9,
        }

    def close(self):
        for fd in (
            list(self.core_fds.values())
            + list(self.uncore_fds.values())
            + self.cha_hit
            + self.cha_miss
            + self.imc_rd
            + self.imc_wr
        ):
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
# Subprocess, SSH, and TCP helpers
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


def tcp_cmd(host, port, cmd, timeout=6.0):
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


def append_jsonl(path: pathlib.Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")
        f.flush()


def check_env_all():
    gov40 = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    cur40 = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
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
        "ethtool -S ens6f1np1 | grep -E 'tx_packets_phy:|tx_bytes_phy:|rx_crc_errors_phy:|tx_global_pause:|rx_global_pause:'"
    ).stdout
    md = {}
    for l in merc_info.strip().splitlines():
        if ":" in l:
            k, v = l.split(":", 1)
            md[k.strip()] = int(v.strip())
    return {
        "saturn": {"cpu40_gov": gov40, "cpu40_khz": cur40, "uncore_khz": uncore, "edac_ue_sum": edac_host},
        "cn106": cd,
        "mercury": md,
    }


def prepare_card_vfio():
    ssh_card(
        "tmux send-keys -t refl C-c 2>/dev/null || true; tmux kill-session -t refl 2>/dev/null || true; "
        "tmux kill-session -t m172c 2>/dev/null || true; pkill -9 mrv172_card 2>/dev/null || true; "
        "rm -f /dev/hugepages/refl* /dev/hugepages/m172c*; "
        "ip link set eth0 down 2>/dev/null || true; "
        "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
        'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
        "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko"
    )


def start_card(eal_str, app_str):
    ssh_card("tmux kill-session -t m172c 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/m172c* /tmp/m172c.log")
    cmd = f"{CARD_BIN} {eal_str} --file-prefix=m172c -- {app_str}"
    r = ssh_card(
        f'tmux new-session -d -s m172c "{cmd} > /tmp/m172c.log 2>&1"; '
        f'for i in $(seq 1 60); do grep -q "MRV172_CARD READY" /tmp/m172c.log && break; sleep 0.15; done; '
        f'grep "MRV172_CARD READY" /tmp/m172c.log',
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError("card start failed:\n" + ssh_card("cat /tmp/m172c.log", check=False).stdout)


def stop_card():
    try:
        tcp_cmd("10.10.0.2", 7545, "QUIT", timeout=2.0)
    except Exception:
        pass
    ssh_card(
        "sleep 0.2; tmux kill-session -t m172c 2>/dev/null || true; rm -f /dev/hugepages/m172c*",
        check=False,
    )


def start_host(dev, app_args):
    sh(["bash", "-c", "pkill -9 mrv172_host 2>/dev/null || true; rm -f /dev/hugepages/m172h* /tmp/m172h.log"], check=False)
    cmd = [HOST_BIN, "-l", "40", "-a", dev, "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m172h", "--"] + app_args
    lf = open("/tmp/m172h.log", "w")
    hp = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
    lf.close()
    for _ in range(60):
        if hp.poll() is not None:
            raise RuntimeError("host exited:\n" + pathlib.Path("/tmp/m172h.log").read_text())
        if "MRV172_HOST READY" in pathlib.Path("/tmp/m172h.log").read_text():
            return hp
        time.sleep(0.1)
    raise RuntimeError("host timeout:\n" + pathlib.Path("/tmp/m172h.log").read_text())


def stop_host(hp):
    if hp is None:
        return
    try:
        tcp_cmd("127.0.0.1", 7546, "QUIT", timeout=2.0)
    except Exception:
        pass
    try:
        hp.wait(timeout=4)
    except Exception:
        hp.kill()
    sh(["bash", "-c", "rm -f /dev/hugepages/m172h*"], check=False)


def wait_for_quiet_host(pmu: HostPmu = None, min_khz: int = 3_850_000, max_imc_gbps: float = 8.0, max_wait_s: float = 180.0):
    """Wait until cpu40 holds >= min_khz, Socket-1 mesh >= 2.185 GHz, and neighbor IMC traffic is quiet."""
    p = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq")
    t0 = time.monotonic()
    streak = 0
    last_khz = 0
    last_imc = 0.0
    last_mesh = 0.0
    while time.monotonic() - t0 < max_wait_s:
        last_khz = int(p.read_text().strip())
        ok = last_khz >= min_khz
        if ok and pmu is not None:
            s0 = pmu.snap()
            time.sleep(0.25)
            s1 = pmu.snap()
            d = HostPmu.delta(s0, s1)
            last_imc = d["imc_rd_gbps"] + d["imc_wr_gbps"]
            last_mesh = d["mesh_ghz"]
            ok = (last_mesh >= 2.185) and (last_imc <= max_imc_gbps)
        else:
            time.sleep(0.15)
        if ok:
            streak += 1
            if streak >= 2:
                return
        else:
            if streak == 0 and (time.monotonic() - t0) > 5.0:
                print(
                    f"  [WAIT_QUIET] cpu40={last_khz/1e6:.2f}GHz mesh={last_mesh:.3f}GHz imc={last_imc:.1f}Gbps",
                    flush=True,
                )
            streak = 0
            if pmu is not None:
                time.sleep(1.0)
    raise RuntimeError(
        f"wait_for_quiet_host timed out after {max_wait_s:.0f}s "
        f"(cpu40={last_khz} kHz, mesh={last_mesh:.3f} GHz, imc={last_imc:.1f} Gbps)"
    )
def probe_balanced_flows(nc: int) -> list:
    """Probe flow_labels 1..128 against CN106 eth0 RSS with `nc` queues and return 1:1 balanced list."""
    if nc <= 1:
        return [1]
    lcores = f"2,4-{3+nc}"
    start_card(f"-l {lcores} --main-lcore 2 -a 0002:02:00.0", "--mode stage0-rx --frame-kind ud --payload-bytes 40 --frags 1")
    try:
        tcp_cmd("10.10.0.2", 7545, "RESET")
        tcp_cmd("mercury", 7547, "PROBE_RSS count=128", timeout=15.0)
        time.sleep(0.05)
        cs = tcp_cmd("10.10.0.2", 7545, "STATS")
        fq = cs.get("flow_q", [])
    finally:
        stop_card()
    by_q = {q: [] for q in range(nc)}
    for idx, q1 in enumerate(fq):
        if 1 <= q1 <= nc:
            by_q[q1 - 1].append(idx + 1)
    balanced = []
    max_depth = max((len(v) for v in by_q.values()), default=0)
    for d in range(max_depth):
        for q in range(nc):
            if d < len(by_q[q]):
                balanced.append(by_q[q][d])
    missing = [q for q in range(nc) if not by_q[q]]
    if missing:
        print(f"  WARN probe_balanced_flows(nc={nc}): missing queues {missing}", flush=True)
    else:
        print(f"  RSS probe nc={nc}: all {nc} queues covered (min {min(len(v) for v in by_q.values())} flows/q)", flush=True)
    return balanced

def run_one(pmu: HostPmu, sp: dict, rep: int, phase: str, flow_map: dict, meas_s: float = MEAS_SECS):
    stage = sp["stage"]
    kind = sp["kind"]
    nc = sp["nc"]
    fk = sp["fk"]
    plen = sp["plen"]
    frags = sp.get("frags", 1)
    use_dma = sp.get("use_dma", False)
    cpp = sp.get("cpp", 1)
    hp = None
    ms = None
    cs = None
    hs = None
    p0 = p1 = t0 = t1 = None
    t_start = t_end = 0.0

    wait_for_quiet_host(pmu)

    def build_merc_cfg():
        mmode = "uc" if fk == "uc64" else "ud"
        mthreads = sp.get("mthreads", 16)
        mbatch = sp.get("mbatch", 64)
        mrate = sp.get("rate_mpps", 0.0)
        fls = flow_map.get(nc, [])
        farg = (" flows=" + ",".join(str(x) for x in fls[:mthreads])) if fls else ""
        return f"CONFIG mode={mmode} threads={mthreads} payload={plen} frags={frags} batch={mbatch} rate_mpps={mrate}{farg}"

    try:
        # Pin CN106 data-plane workers to cpus 4..(3+nc), keeping cpus 0..3 for Linux + main-lcore 2!
        card_lcores = sp.get("card_lcores", f"2,4-{3+nc}")
        if stage == 0:
            extra = sp.get("card_extra", "")
            start_card(
                f"-l {card_lcores} --main-lcore 2 -a 0002:02:00.0",
                f"--mode stage0-rx --frame-kind {fk} --payload-bytes {plen} --frags {frags} {extra}",
            )
            cfg_res = tcp_cmd("mercury", 7547, build_merc_cfg())
            if not cfg_res.get("ok"):
                raise RuntimeError(f"mercury CONFIG failed: {cfg_res}")
            tcp_cmd("mercury", 7547, "START")
        elif stage == 1 and kind == "direct":
            ddevs = ",".join(f"0000:06:00.{i+1}" for i in range(nc)) if use_dma else ""
            eal_d = ("-a 0002:02:00.0 " + " ".join(f"-a 0000:06:00.{i+1}" for i in range(nc))) if use_dma else ""
            extra = sp.get("card_extra", "")
            start_card(
                f"-l {card_lcores} --main-lcore 2 {eal_d}",
                f"--mode stage1-direct --frame-kind {fk} --payload-bytes {plen} --frags {frags}"
                + (f" --dma-devs {ddevs}" if ddevs else "")
                + f" {extra}",
            )
            hargs = [
                "--mode", "direct", "--frame-kind", fk, "--payload-bytes", str(plen),
                "--frags", str(frags), "--cells-per-pkt", str(cpp),
                "--ring-slots", str(sp.get("ring_slots", 1024)),
                "--credit-batch", str(sp.get("credit_batch", 256)),
            ] + (["--use-dma"] if use_dma else [])
            hp = start_host("0000:99:02.0", hargs)
            tcp_cmd("127.0.0.1", 7546, "START")
        elif stage == 1 and kind == "sdp":
            start_card(
                f"-l {card_lcores} --main-lcore 2 -a 0002:01:00.2",
                f"--mode stage1-sdp --frame-kind {fk} --payload-bytes {plen} --frags {frags}",
            )
            dev = sp["sdp_dev"]
            hargs = [
                "--mode", "sdp", "--frame-kind", fk, "--payload-bytes", str(plen), "--frags", str(frags),
                "--sdp-queues", str(nc), "--rxd", str(sp.get("rxd", 2048)),
                "--sdp-pool-size", str(sp.get("sdp_pool_size", 16384)),
            ] + (["--rx-poll-mem"] if sp.get("rpm", False) else [])
            hp = start_host(dev, hargs)
            tcp_cmd("127.0.0.1", 7546, "START")
            tcp_cmd("10.10.0.2", 7545, "START")
        elif stage == 2 and kind == "direct":
            ddevs = ",".join(f"0000:06:00.{i+1}" for i in range(nc)) if use_dma else ""
            eal_d = " ".join(f"-a 0000:06:00.{i+1}" for i in range(nc)) if use_dma else ""
            start_card(
                f"-l {card_lcores} --main-lcore 2 -a 0002:02:00.0 {eal_d}",
                f"--mode stage2-direct --frame-kind {fk} --payload-bytes {plen} --frags {frags}"
                + (f" --dma-devs {ddevs}" if ddevs else ""),
            )
            hargs = [
                "--mode", "direct", "--frame-kind", fk, "--payload-bytes", str(plen),
                "--frags", str(frags), "--cells-per-pkt", str(cpp),
                "--ring-slots", str(sp.get("ring_slots", 1024)),
                "--credit-batch", str(sp.get("credit_batch", 256)),
            ] + (["--use-dma"] if use_dma else [])
            hp = start_host("0000:99:02.0", hargs)
            tcp_cmd("127.0.0.1", 7546, "START")
            cfg_res = tcp_cmd("mercury", 7547, build_merc_cfg())
            if not cfg_res.get("ok"):
                raise RuntimeError(f"mercury CONFIG failed: {cfg_res}")
            tcp_cmd("mercury", 7547, "START")
        elif stage == 2 and kind == "sdp":
            start_card(
                f"-l {card_lcores} --main-lcore 2 -a 0002:02:00.0 -a 0002:01:00.2",
                f"--mode stage2-sdp --frame-kind {fk} --payload-bytes {plen} --frags {frags}",
            )
            dev = sp["sdp_dev"]
            hargs = [
                "--mode", "sdp", "--frame-kind", fk, "--payload-bytes", str(plen), "--frags", str(frags),
                "--sdp-queues", str(nc), "--rxd", str(sp.get("rxd", 2048)),
                "--sdp-pool-size", str(sp.get("sdp_pool_size", 16384)),
            ] + (["--rx-poll-mem"] if sp.get("rpm", False) else [])
            hp = start_host(dev, hargs)
            tcp_cmd("127.0.0.1", 7546, "START")
            tcp_cmd("10.10.0.2", 7545, "START")
            cfg_res = tcp_cmd("mercury", 7547, build_merc_cfg())
            if not cfg_res.get("ok"):
                raise RuntimeError(f"mercury CONFIG failed: {cfg_res}")
            tcp_cmd("mercury", 7547, "START")

        time.sleep(WARMUP_SECS)
        if stage in (0, 2):
            # Clean drain boundary between warmup and measurement window (ZERO in-flight skew!)
            tcp_cmd("mercury", 7547, "STOP")
            time.sleep(0.015)
            tcp_cmd("mercury", 7547, "RESET")
            tcp_cmd("10.10.0.2", 7545, "RESET")
            if hp is not None:
                tcp_cmd("127.0.0.1", 7546, "RESET")
            t_start = time.monotonic()
            tcp_cmd("mercury", 7547, "START")
            # Snap PMUs strictly inside active steady-state window [50 ms .. meas_s - 20 ms]
            time.sleep(0.05)
            p0 = pmu.snap()
            t0 = tcp_cmd("10.10.0.2", 7545, "PMU_SNAP")
            time.sleep(max(meas_s - 0.07, 0.2))
            p1 = pmu.snap()
            t1 = tcp_cmd("10.10.0.2", 7545, "PMU_SNAP")
            time.sleep(0.02)
            tcp_cmd("mercury", 7547, "STOP")
            t_end = time.monotonic()
            # Wait 15 ms so all packets sent during [t_start, t_end] drain cleanly through wire + CN106 + saturn
            time.sleep(0.015)
            ms = tcp_cmd("mercury", 7547, "STATS")
            cs = tcp_cmd("10.10.0.2", 7545, "STATS")
            hs = tcp_cmd("127.0.0.1", 7546, "STATS") if hp is not None else None
        else:
            tcp_cmd("10.10.0.2", 7545, "RESET")
            if hp is not None:
                tcp_cmd("127.0.0.1", 7546, "RESET")
            p0 = pmu.snap()
            t0 = tcp_cmd("10.10.0.2", 7545, "PMU_SNAP")
            t_start = time.monotonic()
            time.sleep(meas_s)
            t_end = time.monotonic()
            p1 = pmu.snap()
            t1 = tcp_cmd("10.10.0.2", 7545, "PMU_SNAP")
            cs = tcp_cmd("10.10.0.2", 7545, "STATS")
            hs = tcp_cmd("127.0.0.1", 7546, "STATS") if hp is not None else None
    except Exception as exc:
        err_rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "part": sp["part"],
            "cfg": sp["cfg"],
            "rep": rep,
            "spec": sp,
            "verified": False,
            "verify_errors": [f"exception: {type(exc).__name__}: {exc}"],
        }
        append_jsonl(ATTEMPTS_PATH, err_rec)
        raise
    finally:
        if stage in (0, 2):
            try:
                tcp_cmd("mercury", 7547, "STOP", timeout=2.0)
            except Exception:
                pass
        stop_host(hp)
        stop_card()

    hp_d = HostPmu.delta(p0, p1)
    tad_d = tad_delta(t0, t1)
    dt_wall = t_end - t_start

    errs = []
    if hp_d["min_running_pct"] < 99.95:
        errs.append(f"host_pmu_pct={hp_d['min_running_pct']:.3f}")
    if tad_d["min_running_pct"] < 99.95:
        errs.append(f"card_pmu_pct={tad_d['min_running_pct']:.3f}")
    if hp is not None and hp_d["cpu40_ghz"] < 3.85:
        errs.append(f"cpu40_ghz={hp_d['cpu40_ghz']:.3f}")
    if hp_d["mesh_ghz"] < 2.18:
        errs.append(f"mesh_ghz={hp_d['mesh_ghz']:.3f}")
    if cs["wire_bad"] != 0 or cs["wire_torn"] != 0 or cs["dma_failed"] != 0 or cs["alloc_fails"] != 0:
        errs.append(f"card_err wb={cs['wire_bad']} wt={cs['wire_torn']} df={cs['dma_failed']} af={cs['alloc_fails']}")
    if hs is not None:
        if hs["bad"] != 0 or hs["torn"] != 0 or hs["c2h_lost"] != 0:
            errs.append(f"host_err bad={hs['bad']} torn={hs['torn']} c2h_lost={hs['c2h_lost']}")
        if stage == 1 and kind == "direct" and hs["merc_stream_lost"] != 0:
            errs.append(f"s1_slost={hs['merc_stream_lost']}")
    if ms is not None:
        if ms["cqe_err"] != 0 or ms["post_err"] != 0:
            errs.append(f"merc_err cqe={ms['cqe_err']} post={ms['post_err']}")
        if ms["phy"]["tx_pause"] != 0 or ms["phy"]["rx_pause"] != 0:
            errs.append(f"pause tx={ms['phy']['tx_pause']} rx={ms['phy']['rx_pause']}")
        if abs(ms["dt_phy_s"] - ms["dt_app_s"]) > 0.003:
            errs.append(f"dt_skew={(ms['dt_phy_s']-ms['dt_app_s'])*1000:.2f}ms")
    # Compute exact unbiased active-window rates using mercury's local START->STOP dt_active_s
    # (zero TCP RTT or drain-tail bias) for Stage 0/2, and host elapsed_s for steady-state Stage 1.
    dt_rate = ms["dt_active_s"] if (stage in (0, 2) and ms is not None) else (hs["elapsed_s"] if hs is not None else cs["elapsed_s"])
    dt_rate = max(dt_rate, 1e-9)
    rates = {
        "dt_rate_s": dt_rate,
        "card_eth_rx_mpps": cs["eth_rx_pkts"] / dt_rate / 1e6,
        "card_nix_good_mpps": cs["eth"]["ipackets"] / dt_rate / 1e6,
        "card_rpm_ok_mpps": cs.get("eth_xstats", {}).get("rpm_rx_aFramesReceivedOK", 0) / dt_rate / 1e6,
        "card_c2h_mpps": cs["c2h_pkts"] / dt_rate / 1e6,
        "card_c2h_mmsg": cs["c2h_msgs"] / dt_rate / 1e6,
    }
    if ms is not None:
        phy = ms["phy"]
        rates["merc_phy_mpps"] = phy["tx_pkts"] / dt_rate / 1e6
        rates["merc_phy_l2_gbps"] = (phy["tx_bytes"] * 8.0) / dt_rate / 1e9
        rates["merc_phy_wire_gbps"] = ((phy["tx_bytes"] + phy["tx_pkts"] * 20) * 8.0) / dt_rate / 1e9
        rates["merc_app_mpps"] = ms["app_tx_pkts"] / dt_rate / 1e6
    if hs is not None:
        l2_bytes = 64 if (fk == "uc64" and plen == 0) else ((62 + plen) if fk == "uc64" else (70 + plen))
        rates["host_rx_mpps"] = hs["rx_pkts"] / dt_rate / 1e6
        rates["host_rx_mmsg"] = hs["rx_msgs"] / dt_rate / 1e6
        rates["host_pay_gbps"] = (hs["rx_bytes"] * 8.0) / dt_rate / 1e9
        rates["host_wire_gbps"] = (hs["rx_pkts"] * (l2_bytes + 20) * 8.0) / dt_rate / 1e9

    # Explicit per-segment drop accounting (strictly separated from measurement/integrity `verified`)
    rpm_ok = cs.get("eth_xstats", {}).get("rpm_rx_aFramesReceivedOK", 0)
    nix_good = cs["eth"]["ipackets"]
    seg_drops = {
        "wire_mac_drop": (ms["phy"]["tx_pkts"] - rpm_ok) if ms is not None else 0,
        "rpm_to_nix_drop": (rpm_ok - nix_good) if stage in (0, 2) else 0,
        "nix_to_sw_drop": (nix_good - cs["eth_rx_pkts"]) if stage in (0, 2) else 0,
        "card_wire_lost": cs["wire_lost"],
        "card_bp_drop": cs["bp_drops"] + cs["reasm_drops"],
        "c2h_to_host_drop": (cs["c2h_pkts"] - hs["rx_pkts"]) if hs is not None else 0,
        "host_merc_stream_lost": hs["merc_stream_lost"] if hs is not None else 0,
    }
    loss_free = all(v == 0 for v in seg_drops.values())

    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "part": sp["part"],
        "cfg": sp["cfg"],
        "rep": rep,
        "spec": sp,
        "dt_wall_s": dt_wall,
        "rates": rates,
        "segment_drops": seg_drops,
        "loss_free": loss_free,
        "mercury": ms,
        "card": cs,
        "host": hs,
        "host_pmu": hp_d,
        "card_pmu": tad_d,
        "verified": len(errs) == 0,
        "verify_errors": errs,
    }
    append_jsonl(ATTEMPTS_PATH, rec)
    return rec

def run_testpmd_rx_ref(label, nq, ism, rpm, rxd, pkt_len, rep):
    """Official Marvell dpdk-testpmd 1H bare SDP RX reference (card txonly -> host rxonly cpu40)."""
    ssh_card("tmux kill-session -t tp_rx 2>/dev/null || true; rm -f /dev/hugepages/tp_rx* /tmp/tp_rx.log")
    sh(["bash", "-c", "rm -f /dev/hugepages/htp_rx* /tmp/htp_rx.log"], check=False)
    dev = f"0000:99:02.0,ism_enable={ism}" + (",rx_poll_mem=1" if rpm else "") + (",allow_small_oq=1" if rxd < 1024 else "")
    host_cmd = [
        TESTPMD_HOST, "-l", "44,40", "--main-lcore", "44", "-a", dev,
        "--socket-mem=0,2048", "--iova-mode=va", "--file-prefix=htp_rx", "--",
        "--no-mlockall", "--forward-mode=rxonly",
        f"--rxq={nq}", f"--txq={nq}", "--nb-cores=1",
        f"--rxd={rxd}", "--txd=2048", "--burst=64",
        "--auto-start", "--stats-period=2",
    ]
    hp = subprocess.Popen(host_cmd, stdin=subprocess.PIPE, stdout=open("/tmp/htp_rx.log", "w"), stderr=subprocess.STDOUT)
    time.sleep(2.2)
    card_cmd = (
        f"dpdk-testpmd -l 2-{2+nq} --main-lcore 2 -a 0002:01:00.2 --file-prefix=tp_rx -- "
        f"--forward-mode=txonly --rxq={nq} --txq={nq} --nb-cores={nq} --rxd=512 --txd=2048 "
        f"--burst=64 --txpkts={pkt_len} --auto-start --stats-period=2"
    )
    ssh_card(f'tmux new-session -d -s tp_rx "{card_cmd} > /tmp/tp_rx.log 2>&1"')
    time.sleep(5.5)
    ssh_card("tmux send-keys -t tp_rx C-c 2>/dev/null || true; sleep 0.3; tmux kill-session -t tp_rx 2>/dev/null || true; rm -f /dev/hugepages/tp_rx*", check=False)
    hp.terminate()
    try:
        hp.wait(timeout=3)
    except subprocess.TimeoutExpired:
        hp.kill()
    hlog = pathlib.Path("/tmp/htp_rx.log").read_text()
    clog = ssh_card("cat /tmp/tp_rx.log", check=False).stdout
    sh(["bash", "-c", "rm -f /dev/hugepages/htp_rx*"], check=False)
    hrx = [int(x) for x in re.findall(r"Rx-pps:\s+(\d+)", hlog)]
    ctx = [int(x) for x in re.findall(r"Tx-pps:\s+(\d+)", clog)]
    hmiss = [int(x) for x in re.findall(r"RX-missed:\s+(\d+)", hlog)]
    rx_mpps = hrx[-1] / 1e6 if hrx else 0.0
    tx_mpps = ctx[-1] / 1e6 if ctx else 0.0
    miss = hmiss[-1] if hmiss else -1
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": "main",
        "part": "part1_stage1_testpmd_rx_ref",
        "cfg": label,
        "rep": rep,
        "spec": {"nq": nq, "ism": ism, "rpm": rpm, "rxd": rxd, "pkt_len": pkt_len},
        "host_rx_mpps": rx_mpps,
        "card_tx_mpps": tx_mpps,
        "host_rx_missed": miss,
        "verified": (rx_mpps > 1.0 and tx_mpps > 1.0),
    }
    append_jsonl(ATTEMPTS_PATH, rec)
    return rec


def build_calibrate_specs():
    return [
        {"part": "cal2_s0", "cfg": "cal2_s0_uc64_c16",   "stage": 0, "kind": "rx", "nc": 16, "fk": "uc64", "plen": 0,    "mthreads": 16, "mbatch": 64},
        {"part": "cal2_s0", "cfg": "cal2_s0_ud110_c12",  "stage": 0, "kind": "rx", "nc": 12, "fk": "ud",   "plen": 40,   "mthreads": 24, "mbatch": 64},
        {"part": "cal2_s0", "cfg": "cal2_s0_uc134_c12",  "stage": 0, "kind": "rx", "nc": 12, "fk": "uc64", "plen": 72,   "mthreads": 24, "mbatch": 64},
        {"part": "cal2_s0", "cfg": "cal2_s0_ud326_c8",   "stage": 0, "kind": "rx", "nc": 8,  "fk": "ud",   "plen": 256,  "mthreads": 24, "mbatch": 64},
        {"part": "cal2_s1", "cfg": "cal2_s1_dp_wc256_c4","stage": 1, "kind": "direct", "nc": 4, "fk": "ud", "plen": 256,  "use_dma": False, "cpp": 5, "ring_slots": 512, "credit_batch": 128},
        {"part": "cal2_s1", "cfg": "cal2_s1_sdp_64_ism1_512", "stage": 1, "kind": "sdp", "nc": 4, "fk": "uc64", "plen": 0, "sdp_dev": "0000:99:02.0,ism_enable=1,allow_small_oq=1", "rxd": 512, "sdp_pool_size": 4096, "rpm": False},
        {"part": "cal2_s1", "cfg": "cal2_s1_sdp_64_ism1_q8",  "stage": 1, "kind": "sdp", "nc": 8, "fk": "uc64", "plen": 0, "sdp_dev": "0000:99:02.0,ism_enable=1", "rxd": 2048, "sdp_pool_size": 32768, "rpm": False},
        {"part": "cal2_s2", "cfg": "cal2_s2_dp_uc64_c12", "stage": 2, "kind": "direct", "nc": 12, "fk": "uc64", "plen": 0,    "use_dma": False, "cpp": 1, "ring_slots": 1024, "credit_batch": 256, "mthreads": 24, "mbatch": 64},
        {"part": "cal2_s2", "cfg": "cal2_s2_dp_ud110_c12","stage": 2, "kind": "direct", "nc": 12, "fk": "ud",   "plen": 40,   "use_dma": False, "cpp": 1, "ring_slots": 1024, "credit_batch": 256, "mthreads": 24, "mbatch": 64},
        {"part": "cal2_s2", "cfg": "cal2_s2_dp_uc134_c12","stage": 2, "kind": "direct", "nc": 12, "fk": "uc64", "plen": 72,   "use_dma": False, "cpp": 2, "ring_slots": 1024, "credit_batch": 256, "mthreads": 24, "mbatch": 64},
        {"part": "cal2_s2", "cfg": "cal2_s2_dp_ud326_c4", "stage": 2, "kind": "direct", "nc": 4,  "fk": "ud",   "plen": 256,  "use_dma": True,  "cpp": 1, "ring_slots": 1024, "credit_batch": 128, "mthreads": 24, "mbatch": 64},
        {"part": "cal2_s2", "cfg": "cal2_s2_sdp_uc64_ism1_c8", "stage": 2, "kind": "sdp", "nc": 8, "fk": "uc64", "plen": 0,   "sdp_dev": "0000:99:02.0,ism_enable=1", "rxd": 2048, "sdp_pool_size": 32768, "rpm": False, "mthreads": 24, "mbatch": 64},
        {"part": "cal2_s2", "cfg": "cal2_s2_sdp_ud1k_rpm1_c4", "stage": 2, "kind": "sdp", "nc": 4, "fk": "ud",   "plen": 1024,"sdp_dev": "0000:99:02.0,ism_enable=1,rx_poll_mem=1,allow_small_oq=1", "rxd": 512, "sdp_pool_size": 4096, "rpm": True, "mthreads": 24, "mbatch": 64},
        {"part": "cal2_s2", "cfg": "cal2_s2_sdp_ud4k_rpm1_c4", "stage": 2, "kind": "sdp", "nc": 4, "fk": "ud",   "plen": 1024,"frags": 4, "sdp_dev": "0000:99:02.0,ism_enable=1,rx_poll_mem=1,allow_small_oq=1", "rxd": 512, "sdp_pool_size": 4096, "rpm": True, "mthreads": 24, "mbatch": 64},
    ]


def build_main_specs():
    specs = []
    # ---- Part 0: Stage 0 (mercury multi-core TX -> 100GbE -> CN106 eth0 multi-core RX, balanced RSS) ----
    for nc in [1, 2, 4, 6, 8, 12, 16]:
        mt = 16 if nc in (1, 2, 4, 8, 16) else (12 if nc == 6 else 24)
        specs.append({
            "part": "part0_stage0_cores",
            "cfg": f"s0_uc64_c{nc}",
            "stage": 0, "kind": "rx", "nc": nc, "fk": "uc64", "plen": 0, "mthreads": mt, "mbatch": 64,
        })
    specs.append({
        "part": "part0_stage0_cores",
        "cfg": "s0_uc64_c16_notouch",
        "stage": 0, "kind": "rx", "nc": 16, "fk": "uc64", "plen": 0, "mthreads": 16, "mbatch": 64,
        "card_extra": "--no-touch",
    })
    for cfg, fk, plen, frags, nc, mt in [
        ("s0_uc94_c12",   "uc64", 32,   1, 12, 24),
        ("s0_ud102_c12",  "ud",   32,   1, 12, 24),
        ("s0_ud110_c12",  "ud",   40,   1, 12, 24),
        ("s0_uc134_c12",  "uc64", 72,   1, 12, 24),
        ("s0_ud134_c12",  "ud",   64,   1, 12, 24),
        ("s0_ud254_c8",   "ud",   184,  1, 8,  24),
        ("s0_ud326_c8",   "ud",   256,  1, 8,  24),
        ("s0_ud1094_c4",  "ud",   1024, 1, 4,  24),
        ("s0_ud4096_c4",  "ud",   1024, 4, 4,  24),
    ]:
        specs.append({
            "part": "part0_stage0_knee",
            "cfg": cfg, "stage": 0, "kind": "rx", "nc": nc, "fk": fk, "plen": plen, "frags": frags,
            "mthreads": mt, "mbatch": 64,
        })

    # ---- Part 1: Stage 1 (CN106 -> PCIe -> saturn 1 core cpu40, no eth0) ----
    for nc in [1, 2, 4, 8]:
        specs.append({
            "part": "part1_stage1_dp",
            "cfg": f"s1_dp_wc64_c{nc}",
            "stage": 1, "kind": "direct", "nc": nc, "fk": "uc64", "plen": 0,
            "use_dma": False, "cpp": 1, "ring_slots": 1024, "credit_batch": 256,
        })
    specs.append({
        "part": "part1_stage1_dp", "cfg": "s1_dp_wc256_c4",
        "stage": 1, "kind": "direct", "nc": 4, "fk": "ud", "plen": 256,
        "use_dma": False, "cpp": 5, "ring_slots": 512, "credit_batch": 128,
    })
    for cfg, plen, frags, rs, cb, extra in [
        ("s1_dp_dma256_c4",  256,  1, 512, 128, ""),
        ("s1_dp_dma1024_c4", 1024, 1, 256, 64,  ""),
        ("s1_dp_dma4096_c4", 1024, 4, 128, 32,  ""),
        ("s1_dp_dma4ks_c4",  1024, 4, 128, 32,  "--dma-split-frags"),
    ]:
        specs.append({
            "part": "part1_stage1_dp", "cfg": cfg,
            "stage": 1, "kind": "direct", "nc": 4, "fk": "ud", "plen": plen, "frags": frags,
            "use_dma": True, "cpp": 1, "ring_slots": rs, "credit_batch": cb, "card_extra": extra,
        })

    sdp_rpm_dev = "0000:99:02.0,ism_enable=1,rx_poll_mem=1,allow_small_oq=1"
    sdp_ism_512 = "0000:99:02.0,ism_enable=1,allow_small_oq=1"
    sdp_ism_2048 = "0000:99:02.0,ism_enable=1"
    for cfg, nc, fk, plen, frags, dev, rxd, psz, rpm in [
        ("s1_sdp_64_rpm1_512_c4",   4, "uc64", 0,    1, sdp_rpm_dev,  512,  4096,  True),
        ("s1_sdp_64_ism1_512_c4",   4, "uc64", 0,    1, sdp_ism_512,  512,  4096,  False),
        ("s1_sdp_64_ism1_2048_c4",  4, "uc64", 0,    1, sdp_ism_2048, 2048, 16384, False),
        ("s1_sdp_64_ism1_2048_c8",  8, "uc64", 0,    1, sdp_ism_2048, 2048, 32768, False),
        ("s1_sdp_256_rpm1_512_c4",  4, "ud",   256,  1, sdp_rpm_dev,  512,  4096,  True),
        ("s1_sdp_256_ism1_2048_c4", 4, "ud",   256,  1, sdp_ism_2048, 2048, 16384, False),
        ("s1_sdp_1024_rpm1_512_c4", 4, "ud",   1024, 1, sdp_rpm_dev,  512,  4096,  True),
        ("s1_sdp_1024_ism1_2048_c4",4, "ud",   1024, 1, sdp_ism_2048, 2048, 16384, False),
    ]:
        specs.append({
            "part": "part1_stage1_sdp", "cfg": cfg,
            "stage": 1, "kind": "sdp", "nc": nc, "fk": fk, "plen": plen, "frags": frags,
            "sdp_dev": dev, "rxd": rxd, "sdp_pool_size": psz, "rpm": rpm,
        })

    # ---- Part 2: Stage 2 Full E2E (mercury -> 100GbE -> CN106 -> saturn 1 core cpu40) ----
    e2e_sizes = [
        ("64B",   "uc64", 0,    1, 16, False, 1, 1024, 256, 16, 8),
        ("110B",  "ud",   40,   1, 16, False, 1, 1024, 256, 16, 8),
        ("134B",  "uc64", 72,   1, 16, False, 2, 1024, 256, 16, 8),
        ("326B",  "ud",   256,  1, 4,  True,  1, 1024, 128, 28, 4),
        ("1094B", "ud",   1024, 1, 4,  True,  1, 512,  64,  24, 4),
        ("4096B", "ud",   1024, 4, 4,  True,  1, 128,  32,  24, 4),
    ]
    for sz_lbl, fk, plen, frags, dp_nc, use_dma, cpp, rs, cb, mt, sdp_nc in e2e_sizes:
        specs.append({
            "part": "part2_stage2_e2e",
            "cfg": f"s2_dp_{sz_lbl}",
            "stage": 2, "kind": "direct", "nc": dp_nc, "fk": fk, "plen": plen, "frags": frags,
            "use_dma": use_dma, "cpp": cpp, "ring_slots": rs, "credit_batch": cb,
            "mthreads": mt, "mbatch": 64,
        })
        specs.append({
            "part": "part2_stage2_e2e",
            "cfg": f"s2_sdp_ism1_{sz_lbl}",
            "stage": 2, "kind": "sdp", "nc": sdp_nc, "fk": fk, "plen": plen, "frags": frags,
            "sdp_dev": sdp_ism_2048, "rxd": 2048, "sdp_pool_size": 8192 * sdp_nc // 2, "rpm": False,
            "mthreads": mt, "mbatch": 64,
        })
        specs.append({
            "part": "part2_stage2_e2e",
            "cfg": f"s2_sdp_rpm1_{sz_lbl}",
            "stage": 2, "kind": "sdp", "nc": sdp_nc, "fk": fk, "plen": plen, "frags": frags,
            "sdp_dev": sdp_rpm_dev, "rxd": 512, "sdp_pool_size": 1024 * sdp_nc, "rpm": True,
            "mthreads": mt, "mbatch": 64,
        })

    # ---- Part 3: Controls ----
    specs.append({
        "part": "part3_controls",
        "cfg": "s2_dp_64B_paced95M_zl",
        "stage": 2, "kind": "direct", "nc": 16, "fk": "uc64", "plen": 0, "frags": 1,
        "use_dma": False, "cpp": 1, "ring_slots": 2048, "credit_batch": 256,
        "mthreads": 16, "mbatch": 32, "rate_mpps": 95.0,
    })
    specs.append({
        "part": "part3_controls",
        "cfg": "s2_dp_ud134_ref",
        "stage": 2, "kind": "direct", "nc": 16, "fk": "ud", "plen": 64, "frags": 1,
        "use_dma": False, "cpp": 2, "ring_slots": 1024, "credit_batch": 256,
        "mthreads": 16, "mbatch": 64,
    })
    specs.append({
        "part": "part3_controls",
        "cfg": "s2_sdp_ism1_512_64B",
        "stage": 2, "kind": "sdp", "nc": 8, "fk": "uc64", "plen": 0, "frags": 1,
        "sdp_dev": sdp_ism_512, "rxd": 512, "sdp_pool_size": 8192, "rpm": False,
        "mthreads": 16, "mbatch": 64,
    })
    return specs

def print_summary(rec: dict):
    sp = rec["spec"]
    r = rec["rates"]
    c = rec["card"]
    h = rec["host"]
    m = rec["mercury"]
    p = rec["host_pmu"]
    if sp["stage"] == 0:
        phy = m["phy"]
        qs = c.get("per_q_rx", [])
        qmin, qmax = (min(qs), max(qs)) if qs else (0, 0)
        qwl = c.get("per_q_wl", [])
        rpm = c.get("eth_xstats", {}).get("rpm_rx_aFramesReceivedOK", 0)
        print(
            f"[S0] {sp['cfg']:20s} | merc={r['merc_phy_mpps']:6.2f}M({r['merc_phy_wire_gbps']:5.1f}G L2={phy['avg_l2_bytes']:5.1f}B) "
            f"| rpm={r['card_rpm_ok_mpps']:6.2f}M nix={r['card_nix_good_mpps']:6.2f}M sw={r['card_eth_rx_mpps']:6.2f}M "
            f"| q=[{qmin/1e6:.1f}..{qmax/1e6:.1f}M] wl={c['wire_lost']} qwl={qwl} Δ(m-rpm)={phy['tx_pkts']-rpm} Δ(nix-sw)={c['eth']['ipackets']-c['eth_rx_pkts']} "
            f"| ok={rec['verified']} {rec['verify_errors']}",
            flush=True,
        )
    elif sp["stage"] == 1:
        print(
            f"[S1] {sp['cfg']:26s} | host={r['host_rx_mpps']:6.2f}M ({r['host_rx_mmsg']:5.2f}Mmsg {r['host_pay_gbps']:5.1f}G pay) card={r['card_c2h_mpps']:6.2f}M "
            f"| iioWr={p['iio1_inb_wr_mops']:5.1f}M({p['iio1_inb_wr_avg_bytes']:4.1f}B) ddio={p['ddio_hit_pct']:5.1f}% imc={p['imc_rd_gbps']:.1f}/{p['imc_wr_gbps']:.1f}G "
            f"| b/t/l={h['bad']}/{h['torn']}/{h['late']} sl={h['merc_stream_lost']} ok={rec['verified']} {rec['verify_errors']}",
            flush=True,
        )
    else:
        print(
            f"[S2] {sp['cfg']:20s} | merc={r['merc_phy_mpps']:6.2f}M({r['merc_phy_wire_gbps']:5.1f}G) card={r['card_eth_rx_mpps']:6.2f}M(bp={c['bp_drops']}) "
            f"| host={r['host_rx_mpps']:6.2f}M({r['host_rx_mmsg']:5.2f}Mmsg {r['host_pay_gbps']:5.1f}G pay / {r['host_wire_gbps']:5.1f}G wire) ddio={p['ddio_hit_pct']:5.1f}% "
            f"| Δ(c2h-h)={c['c2h_pkts']-h['rx_pkts']} b/t/l={h['bad']}/{h['torn']}/{h['late']} ok={rec['verified']} {rec['verify_errors']}",
            flush=True,
        )
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["calibrate", "probe_fix", "main"], default="calibrate")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env_before = check_env_all()
    print("ENV BEFORE:", json.dumps(env_before), flush=True)
    prepare_card_vfio()

    sh(MERC_SSH + ["pkill -9 mrv172_tx 2>/dev/null || true"], check=False)
    mp = subprocess.Popen(
        MERC_SSH + [f"{MERC_BIN} --port 7547"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    print("MERC READY:", mp.stdout.readline().strip(), flush=True)
    pmu = HostPmu(cpu=40)
    done = 0
    failed = 0
    clock_retries = 0
    flow_map = {}

    try:
        probe_ncs = [4, 16] if args.mode == "probe_fix" else ([4, 8, 12, 16] if args.mode == "calibrate" else [2, 4, 6, 8, 12, 16])
        for nc in probe_ncs:
            flow_map[nc] = probe_balanced_flows(nc)

        if args.mode == "calibrate":
            for sp in build_calibrate_specs():
                rec = run_one(pmu, sp, 0, "calibrate", flow_map, meas_s=1.0)
                print_summary(rec)
        elif args.mode == "probe_fix":
            targets = {
                "s1_dp_wc256_c4", "s2_dp_110B", "s2_dp_134B",
                "s2_dp_ud134_ref", "s2_dp_326B", "s2_dp_1094B", "s2_dp_64B_paced100M_zl",
            }
            for sp in build_main_specs():
                if sp["cfg"] in targets:
                    rec = run_one(pmu, sp, 0, "probe_fix", flow_map, meas_s=1.0)
                    print_summary(rec)
        else:
            specs = build_main_specs()
            with open(RUNS_PATH, "w") as out:
                for rep in range(REPS):
                    for label, nq, ism, rpm, rxd, plen in [
                        ("tp_rx_64B_ism0_2048", 4, 0, 0, 2048, 64),
                        ("tp_rx_64B_ism1_2048", 4, 1, 0, 2048, 64),
                        ("tp_rx_64B_rpm1_512",  4, 1, 1, 512,  64),
                    ]:
                        wait_for_quiet_host(pmu)
                        rec = run_testpmd_rx_ref(label, nq, ism, rpm, rxd, plen, rep)
                        out.write(json.dumps(rec) + "\n")
                        out.flush()
                        done += 1
                        if not rec["verified"]:
                            failed += 1
                        print(
                            f"[TP rep={rep}] {label:22s} host_rx={rec['host_rx_mpps']:6.2f}M card_tx={rec['card_tx_mpps']:6.2f}M "
                            f"miss={rec['host_rx_missed']} ok={rec['verified']}",
                            flush=True,
                        )

                for rep in range(REPS):
                    order = list(specs) if rep % 2 == 0 else list(reversed(specs))
                    for idx, sp in enumerate(order):
                        rec = run_one(pmu, sp, rep, "main", flow_map, meas_s=MEAS_SECS)
                        # Transparent shared-host clock-contention re-measure ONLY (never retry integrity errors!)
                        # Every attempt (rejected + accepted) is permanently archived in attempts.jsonl.
                        retries = 0
                        while (
                            not rec["verified"]
                            and retries < 6
                            and all(e.startswith(("cpu40_ghz=", "mesh_ghz=")) for e in rec["verify_errors"])
                        ):
                            retries += 1
                            clock_retries += 1
                            backoff = min(5.0 * retries, 20.0)
                            print(
                                f"  [CLOCK_RETRY #{retries} logged in attempts.jsonl, sleep {backoff:.0f}s] {sp['cfg']} rep={rep} errs={rec['verify_errors']}",
                                flush=True,
                            )
                            time.sleep(backoff)
                            rec = run_one(pmu, sp, rep, "main", flow_map, meas_s=MEAS_SECS)
                        rec["clock_retries_before_accept"] = retries
                        out.write(json.dumps(rec) + "\n")
                        out.flush()
                        done += 1
                        if not rec["verified"]:
                            failed += 1
                        print(f"[rep={rep+1}/{REPS} {idx+1:2d}/{len(specs)}] ", end="", flush=True)
                        print_summary(rec)
    finally:
        stop_card()
        try:
            tcp_cmd("mercury", 7547, "QUIT", timeout=2.0)
        except Exception:
            pass
        try:
            mp.wait(timeout=4)
        except Exception:
            mp.kill()
        pmu.close()

    env_after = check_env_all()
    if args.mode == "main":
        ENV_PATH.write_text(json.dumps({
            "before": env_before,
            "after": env_after,
            "flow_map": flow_map,
            "done": done,
            "failed": failed,
            "clock_retries": clock_retries,
        }, indent=2))
        print(f"MAIN DONE total={done} failed={failed} clock_retries={clock_retries}", flush=True)
    else:
        print("ENV AFTER:", json.dumps(env_after), flush=True)


if __name__ == "__main__":
    main()
