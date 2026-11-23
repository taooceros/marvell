#!/usr/bin/python3
"""MRV-155 full interleaved 3-machine 100GbE 1-host-core E2E bandwidth harness.

Fixed hardware topology (zero sudo, zero Intel DSA):
  - Client  : mercury (4x IBV_QPT_UD RoCEv2 threads on NUMA 0 cpus 0..3, mlx5_2,
              MTU 1500 / active_mtu 1024, RSS-probed 1:1 onto CN106 eth0 queues 0..3)
  - Wire    : 3m 100GbE passive DAC (No FEC)
  - SmartNIC: CN106 (4 cores 3-6 @ 2.00 GHz, physical eth0 4RxQ/4TxQ RSS;
              4x exclusive bidirectional DPI VFs 06:00.1..4 for DirectPush >=1 KiB;
              4x 4-queue IPv4+UDP RSS sdp1-1 for balanced 4C SDP)
  - Server  : saturn (ONLY 1 host core cpu40 @ 3.90 GHz performance, Socket-1
              uncore 2.20 GHz, intel_iommu=on,sm_on, 128 MiB 2 MiB-hugepage TX pool;
              control TCP thread isolated on cpu 44)

Single-variable comparison across packet sizes {64, 256, 1024, 4096, 8192} B:
  - card_echo  : 4C CN106 eth0 pure hardware ceiling (no host traversal)
  - direct     : 1H <-> 4C DirectPush (<=256 B WC + 128 B exclusive BAR4 sub-ring +
                 bypass tx_pool) + Native 4-VF bidir DPI DMA (>=1 KiB)
  - sdp_def    : 1H <-> 4C traditional SDP (4 queues, rx_poll_mem=0, 128 MiB tx_pool copy)
  - sdp_rpm_zc : 1H <-> 4C strong-control SDP (4 queues, rx_poll_mem=1 + zero-copy)

Controls:
  - direct_cl64 @ 64 B / 256 B (C_line = 64 sliding-window credit vs C_line = 0 CHA check)
  - direct_noaw @ 4096 B (--app-work OFF verification of zero throughput perturbation)
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

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv155/logs/mrv155-2026-11-23")
RUNS_PATH = OUT_DIR / "runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"

HOST_BIN = "/home/hongtao/dev/marvell-mrv155/testbed/target/release/mrv155_host"
CARD_BIN = "/root/bench/mrv155_card"
MERC_BIN = "/tmp/mrv155/mrv155_client"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]

REPS = 3
SOCKET1_UNCORE_CPU = 32
REF_HZ = 2.2e9


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
        self.cpu = cpu
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
            "irp1_occ": PerfFd(_pmu_type("uncore_irp_1"), 0x0C, SOCKET1_UNCORE_CPU),
            "irp1_ins": PerfFd(_pmu_type("uncore_irp_1"), 0x0E, SOCKET1_UNCORE_CPU),
            "iio1_cpu_wr": PerfFd(iio1_t, 0x0007_0FF0_0000_01C1, SOCKET1_UNCORE_CPU),
            "iio1_inb_wr": PerfFd(iio1_t, 0x0007_0FF0_0000_0184, SOCKET1_UNCORE_CPU),
            "iio1_inb_rd": PerfFd(iio1_t, 0x0007_0FF0_0000_0484, SOCKET1_UNCORE_CPU),
            "iio1_clk": PerfFd(iio1_t, 0x01, SOCKET1_UNCORE_CPU),
        }
        self.cha_wcilf_ins = []
        self.cha_wcilf_occ = []
        self.cha_io_hit = []
        self.cha_io_miss = []
        for i in range(32):
            t = _pmu_type(f"uncore_cha_{i}")
            self.cha_wcilf_ins.append(PerfFd(t, 0x00C8_67FF_0000_0135, SOCKET1_UNCORE_CPU))
            self.cha_wcilf_occ.append(PerfFd(t, 0x00C8_67FF_0000_0136, SOCKET1_UNCORE_CPU))
            self.cha_io_hit.append(PerfFd(t, 0x00CC_43FD_0000_0435, SOCKET1_UNCORE_CPU))
            self.cha_io_miss.append(PerfFd(t, 0x00CC_43FE_0000_0435, SOCKET1_UNCORE_CPU))

    def snap(self):
        return {
            "t": time.monotonic(),
            "c": {k: fd.read() for k, fd in self.core_fds.items()},
            "u": {k: fd.read() for k, fd in self.uncore_fds.items()},
            "wcilf_ins": [fd.read() for fd in self.cha_wcilf_ins],
            "wcilf_occ": [fd.read() for fd in self.cha_wcilf_occ],
            "io_hit": [fd.read() for fd in self.cha_io_hit],
            "io_miss": [fd.read() for fd in self.cha_io_miss],
        }

    @staticmethod
    def delta(a, b):
        dt = max(b["t"] - a["t"], 1e-9)
        min_pct = 100.0

        def d1(x0, x1):
            nonlocal min_pct
            dv = max(0, x1[0] - x0[0])
            de = max(0, x1[1] - x0[1])
            dr = max(0, x1[2] - x0[2])
            if de > 0:
                min_pct = min(min_pct, (dr / de) * 100.0)
            return dv

        c = {k: d1(a["c"][k], b["c"][k]) for k in a["c"]}
        u = {k: d1(a["u"][k], b["u"][k]) for k in a["u"]}
        wcilf_ins = sum(d1(x, y) for x, y in zip(a["wcilf_ins"], b["wcilf_ins"]))
        wcilf_occ = sum(d1(x, y) for x, y in zip(a["wcilf_occ"], b["wcilf_occ"]))
        io_hit = sum(d1(x, y) for x, y in zip(a["io_hit"], b["io_hit"]))
        io_miss = sum(d1(x, y) for x, y in zip(a["io_miss"], b["io_miss"]))
        itom = io_hit + io_miss
        ddio_pct = (io_hit * 100.0 / itom) if itom > 0 else 100.0
        cpu_ghz = (c["cycles"] / c["ref_cycles"]) * (REF_HZ / 1e9) if c["ref_cycles"] > 0 else 0.0
        mesh_ghz = (u["mesh_clk"] / dt) / 1e9
        cha_inflight = wcilf_occ / max(u["mesh_clk"], 1)
        irp1_inflight = u["irp1_occ"] / max(u["iio1_clk"], 1)
        return {
            "dt_s": round(dt, 6),
            "min_running_pct": round(min_pct, 3),
            "cpu40_ghz": round(cpu_ghz, 4),
            "mesh_ghz": round(mesh_ghz, 4),
            "cycles": c["cycles"],
            "instr": c["instr"],
            "ipc": round(c["instr"] / max(c["cycles"], 1), 4),
            "llc_loads": c["llc_loads"],
            "llc_load_misses": c["llc_load_misses"],
            "llc_miss_mrps": round(c["llc_load_misses"] / dt / 1e6, 4),
            "sb_stall_pct": round(c["sb_stall"] * 100.0 / max(c["cycles"], 1), 4),
            "iio1_cpu_wr": u["iio1_cpu_wr"],
            "iio1_inb_wr": u["iio1_inb_wr"],
            "iio1_inb_rd": u["iio1_inb_rd"],
            "iio1_inb_wr_gbps": round(u["iio1_inb_wr"] * 64 * 8 / dt / 1e9, 4),
            "iio1_inb_rd_gbps": round(u["iio1_inb_rd"] * 64 * 8 / dt / 1e9, 4),
            "iio1_cpu_wr_gbps": round(u["iio1_cpu_wr"] * 64 * 8 / dt / 1e9, 4),
            "irp1_inflight": round(irp1_inflight, 4),
            "cha_ia_wcilf": wcilf_ins,
            "cha_inflight": round(cha_inflight, 4),
            "cha_io_hit_itom": io_hit,
            "cha_io_miss_itom": io_miss,
            "ddio_hit_pct": round(ddio_pct, 4),
        }

    def close(self):
        for fd in self.core_fds.values():
            fd.close()
        for fd in self.uncore_fds.values():
            fd.close()
        for lst in (self.cha_wcilf_ins, self.cha_wcilf_occ, self.cha_io_hit, self.cha_io_miss):
            for fd in lst:
                fd.close()


def delta_tad(a, b):
    dt = max(b["t_s"] - a["t_s"], 1e-9)
    min_pct = 100.0
    out = {"dt_s": round(dt, 6)}
    for k in a["tad"]:
        v0, e0, r0 = a["tad"][k]
        v1, e1, r1 = b["tad"][k]
        dv = max(0, v1 - v0)
        de = max(0, e1 - e0)
        dr = max(0, r1 - r0)
        if de > 0:
            min_pct = min(min_pct, (dr / de) * 100.0)
        out[k] = dv
    out["min_running_pct"] = round(min_pct, 3)
    return out


# ---------------------------------------------------------------------------
# Subprocess, TCP & environment helpers
# ---------------------------------------------------------------------------
def sh(cmd, check=True, timeout=180):
    r = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        print(f"CMD FAILED ({r.returncode}): {cmd}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}", file=sys.stderr, flush=True)
        raise subprocess.CalledProcessError(r.returncode, cmd, r.stdout, r.stderr)
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


def check_env_all():
    gov40 = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    max40 = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_max_freq").read_text().strip())
    uncore = {
        p.parent.name: int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz"))
    }
    edac_host = [
        int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count"))
    ]
    assert gov40 == "performance", f"cpu40 governor={gov40}"
    assert uncore.get("package_01_die_01", 0) >= 2_190_000, f"uncore={uncore}"

    card_info = ssh_card(
        "echo freq=$(cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq);"
        "echo edac_ue=$(grep . /sys/devices/system/edac/*/*/ue_count | awk -F: '{s+=$2} END{print s+0}');"
        "echo sei_uer=$(dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || true);"
        "echo hp512_free=$(cat /sys/kernel/mm/hugepages/hugepages-524288kB/free_hugepages);"
        "echo bar4_idx7=$(python3 /root/bar4idx7.py | awk '{print $NF}')"
    ).stdout
    card_d = {}
    for line in card_info.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            card_d[k] = v

    merc_info = ssh_merc(
        "ethtool -S ens6f1np1 | grep -E 'rx_crc_errors_phy|tx_errors_phy|rx_pcs_symbol_err_phy|rx_packets_phy|tx_packets_phy';"
        "ip link show ens6f1np1 | head -n 1"
    ).stdout
    merc_d = {}
    for line in merc_info.strip().splitlines():
        if ":" in line and not line[0].isdigit():
            k, v = line.split(":", 1)
            merc_d[k.strip()] = int(v.strip())
        elif "mtu" in line:
            m = re.search(r"mtu (\d+)", line)
            if m:
                merc_d["mtu"] = int(m.group(1))
    return {
        "saturn": {"cpu40_gov": gov40, "cpu40_max_khz": max40, "uncore_khz": uncore, "edac_ue_sum": sum(edac_host)},
        "cn106": card_d,
        "mercury": merc_d,
    }


# ---------------------------------------------------------------------------
# Card & Host lifecycle managers
# ---------------------------------------------------------------------------
class StackManager:
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
            sh(["rm", "-f", "/dev/hugepages/m155hmap_*"], check=False)

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m155 C-c 2>/dev/null || true; sleep 0.6; "
            "tmux kill-session -t m155 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/m155*"
        )
        self.card_key = None

    def ensure_card(self, card_mode: str):
        if self.card_key == card_mode:
            return
        self.stop_card()
        ssh_card(
            "tmux send-keys -t refl C-c 2>/dev/null || true; tmux kill-session -t refl 2>/dev/null || true; "
            "rm -f /dev/hugepages/refl*; "
            "ip link set eth0 down 2>/dev/null || true; "
            "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
            'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
            "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko"
        )
        if card_mode == "card_echo":
            ready_tok = "card_echo ready"
            cmd = f"{CARD_BIN} -l 3-6 -a 0002:02:00.0 --file-prefix=m155 -- --mode card-echo"
        elif card_mode == "direct":
            ready_tok = "direct ready"
            cmd = (
                f"{CARD_BIN} -l 3-6 -a 0002:02:00.0 "
                f"-a 0000:06:00.1 -a 0000:06:00.2 -a 0000:06:00.3 -a 0000:06:00.4 "
                f"--file-prefix=m155 -- --mode direct"
            )
        elif card_mode == "sdp":
            ready_tok = "sdp ready"
            cmd = f"{CARD_BIN} -l 3-6 -a 0002:02:00.0 -a 0002:01:00.2 --file-prefix=m155 -- --mode sdp"
        else:
            raise ValueError(card_mode)

        ssh_card(
            f'tmux new-session -d -s m155 "{cmd} > /tmp/m155_card.log 2>&1"; '
            f'for i in $(seq 1 50); do grep -q "MRV155_CARD {ready_tok}" /tmp/m155_card.log && break; sleep 0.2; done; '
            f'grep "MRV155_CARD {ready_tok}" /tmp/m155_card.log'
        )
        self.card_key = card_mode

    def ensure_host(self, host_mode: str, rpm: bool, zc: bool, credit_lines: int, app_work: bool):
        if host_mode == "none":
            self.stop_host()
            return
        key = (host_mode, rpm, zc, credit_lines, app_work)
        if self.host_key == key and self.host_proc is not None and self.host_proc.poll() is None:
            return
        self.stop_host()
        dev = "0000:99:02.0,rx_poll_mem=1" if (host_mode == "sdp" and rpm) else "0000:99:02.0"
        cmd = [
            HOST_BIN, "-l", "40", "-a", dev,
            "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m155h", "--",
            "--mode", host_mode,
            "--sdp-queues", "4",
            "--credit-lines", str(credit_lines),
            "--credit-hyst", "8",
            "--bar4-stride", "128",
        ]
        if rpm:
            cmd.append("--rx-poll-mem")
        if zc:
            cmd.append("--zero-copy")
        if app_work:
            cmd.append("--app-work")
        logf = open("/tmp/m155_host_run.log", "w")
        self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        logf.close()
        for _ in range(60):
            if self.host_proc.poll() is not None:
                err = pathlib.Path("/tmp/m155_host_run.log").read_text()
                raise RuntimeError(f"mrv155_host exited early:\n{err}")
            txt = pathlib.Path("/tmp/m155_host_run.log").read_text()
            if "MRV155_HOST READY" in txt:
                self.host_key = key
                return
            time.sleep(0.1)
        raise RuntimeError(f"mrv155_host timeout:\n{pathlib.Path('/tmp/m155_host_run.log').read_text()}")


def run_idle_cal(pmu: HostPmu, rep: int):
    p0 = pmu.snap()
    r = sh([HOST_BIN, "--", "--idle-cal-ms", "1000"])
    p1 = pmu.snap()
    cal = json.loads(r.stdout.strip().splitlines()[-1])
    dp = HostPmu.delta(p0, p1)
    ok = (dp["min_running_pct"] >= 99.9) and (dp["cpu40_ghz"] >= 3.80) and (cal["app_work_kps"] > 3000.0)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "part": "part0_cal",
        "cfg": "idle_cal_1h",
        "rep": rep,
        "cal": cal,
        "pmu": dp,
        "verified": ok,
        "verify_errors": [] if ok else [f"cal_check_failed ghz={dp['cpu40_ghz']} pct={dp['min_running_pct']}"],
    }


def run_spec(stack: StackManager, pmu: HostPmu, spec: dict, rep: int, idle_kps: float):
    stack.ensure_card(spec["card_mode"])
    stack.ensure_host(
        spec["host_mode"],
        spec.get("rpm", False),
        spec.get("zc", False),
        spec.get("credit_lines", 0),
        spec.get("app_work", False),
    )

    tcp_cmd("10.10.0.2", 7529, "RESET")
    if spec["host_mode"] != "none":
        tcp_cmd("127.0.0.1", 7531, "RESET")

    wl = spec["rpc_bytes"]
    w = spec["window"]
    threads = spec.get("threads", 4)
    warmup_ms = spec.get("warmup_ms", 300)
    dur_ms = spec.get("duration_ms", 1200)
    seed = 0x1550_0000 + rep * 1009 + wl

    merc_cmd = (
        f"{MERC_BIN} --threads {threads} --card-queues 4 --mode closed "
        f"--workload {wl} --window {w} --warmup-ms {warmup_ms} --duration-ms {dur_ms} --seed {seed}"
    )
    proc = subprocess.Popen(
        MERC_SSH + [merc_cmd],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    p0 = p1 = None
    t0 = t1 = None
    h0 = h1 = None
    out_lines = []
    for line in proc.stdout:
        s = line.strip()
        if s == "PROBE_DONE":
            tcp_cmd("10.10.0.2", 7529, "RESET")
            if spec["host_mode"] != "none":
                tcp_cmd("127.0.0.1", 7531, "RESET")
            proc.stdin.write("GO\n")
            proc.stdin.flush()
        elif s == "WARMUP_DONE":
            time.sleep(0.15)
            p0 = pmu.snap()
            t0 = tcp_cmd("10.10.0.2", 7529, "PMU_SNAP")
            if spec["host_mode"] != "none":
                h0 = tcp_cmd("127.0.0.1", 7531, "SNAP")
            time.sleep(0.90)
            p1 = pmu.snap()
            t1 = tcp_cmd("10.10.0.2", 7529, "PMU_SNAP")
            if spec["host_mode"] != "none":
                h1 = tcp_cmd("127.0.0.1", 7531, "SNAP")
        elif s.startswith("{"):
            out_lines.append(s)

    stderr = proc.stderr.read()
    proc.wait(timeout=30)
    if proc.returncode != 0 or not out_lines:
        raise RuntimeError(f"mercury rc={proc.returncode} cfg={spec['cfg']} stderr={stderr}")

    client = json.loads(out_lines[-1])
    card = tcp_cmd("10.10.0.2", 7529, "STATS")
    host = tcp_cmd("127.0.0.1", 7531, "STATS") if spec["host_mode"] != "none" else None

    dp = HostPmu.delta(p0, p1)
    dtad = delta_tad(t0, t1)

    steady_host = None
    host_cpu_pct = None
    if h0 is not None and h1 is not None:
        dh = max(h1["dt_s"] - h0["dt_s"], 1e-9)
        dw = h1["app_work_units"] - h0["app_work_units"]
        wkps = (dw / dh) / 1e3
        steady_host = {
            "dt_s": round(dh, 6),
            "rx_rpcs": h1["rx_rpcs"] - h0["rx_rpcs"],
            "tx_rpcs": h1["tx_rpcs"] - h0["tx_rpcs"],
            "bar4_lines_written": h1["bar4_lines_written"] - h0["bar4_lines_written"],
            "app_work_units": dw,
            "app_work_kps": round(wkps, 3),
            "empty_polls": h1["empty_polls"] - h0["empty_polls"],
            "busy_polls": h1["busy_polls"] - h0["busy_polls"],
            "credit_waits": h1["credit_waits"] - h0["credit_waits"],
            "late_tails": h1["late_tails"] - h0["late_tails"],
        }
        if spec.get("app_work", False) and idle_kps > 0:
            host_cpu_pct = round(max(0.0, min(100.0, (1.0 - wkps / idle_kps) * 100.0)), 3)

    # Strict zero-error verification
    errs = []
    for k in ("lost", "bad", "torn", "send_err"):
        if client.get(k, 0) != 0:
            errs.append(f"client.{k}={client.get(k)}")
    for k in ("bad_wire", "bad_host", "torn_bar4", "dma_failed", "eth_tx_drops", "sdp_tx_drops"):
        if card.get(k, 0) != 0:
            errs.append(f"card.{k}={card.get(k)}")
    for k in ("imissed", "ierrors", "oerrors", "rx_nombuf"):
        if card["eth"].get(k, 0) != 0:
            errs.append(f"card.eth.{k}={card['eth'].get(k)}")
    if card.get("sdp") is not None:
        for k in ("imissed", "ierrors", "oerrors", "rx_nombuf"):
            if card["sdp"].get(k, 0) != 0:
                errs.append(f"card.sdp.{k}={card['sdp'].get(k)}")
    if host is not None:
        for k in ("bad", "torn", "tx_drops"):
            if host.get(k, 0) != 0:
                errs.append(f"host.{k}={host.get(k)}")
        if dp["cpu40_ghz"] < 3.80:
            errs.append(f"cpu40_ghz={dp['cpu40_ghz']}")
    if dp["min_running_pct"] < 99.9:
        errs.append(f"host_pmu_pct={dp['min_running_pct']}")
    if dtad["min_running_pct"] < 99.9:
        errs.append(f"tad_pmu_pct={dtad['min_running_pct']}")

    # Verify all 4 mercury threads matched distinct RSS queues 0..3
    rss_qs = sorted(t["rss_q"] for t in client["per_thread"])
    if rss_qs != [0, 1, 2, 3]:
        errs.append(f"rss_qs={rss_qs}")

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "part": spec["part"],
        "cfg": spec["cfg"],
        "rep": rep,
        "spec": spec,
        "client": client,
        "card": card,
        "host": host,
        "steady_host": steady_host,
        "host_cpu_pct": host_cpu_pct,
        "idle_cal_kps": round(idle_kps, 3),
        "pmu": dp,
        "tad": dtad,
        "verified": len(errs) == 0,
        "verify_errors": errs,
    }


def build_specs():
    specs = []
    # Main single-variable size sweep: 64 B, 256 B, 1 KiB, 4 KiB, plus optional 8 KiB
    sizes = [(64, 256), (256, 256), (1024, 256), (4096, 256), (8192, 128)]
    modes = [
        ("card_echo", "card_echo", "none", False, False, 0, False),
        ("direct", "direct", "direct", False, False, 0, True),
        ("sdp_def", "sdp", "sdp", False, False, 0, True),
        ("sdp_rpm_zc", "sdp", "sdp", True, True, 0, True),
    ]
    for mname, cmode, hmode, rpm, zc, cl, aw in modes:
        for sz, w in sizes:
            specs.append({
                "part": "part1_sweep",
                "mode_group": mname,
                "cfg": f"{mname}_{sz}B_w{w}",
                "card_mode": cmode,
                "host_mode": hmode,
                "rpm": rpm,
                "zc": zc,
                "credit_lines": cl,
                "app_work": aw,
                "rpc_bytes": sz,
                "window": w,
                "threads": 4,
            })

    # Controls: C_line=64 sliding-window credit check (64 B / 256 B) + app-work=OFF check (4 KiB)
    for sz in (64, 256):
        specs.append({
            "part": "part2_controls",
            "mode_group": "direct_cl64",
            "cfg": f"direct_cl64_{sz}B_w256",
            "card_mode": "direct",
            "host_mode": "direct",
            "rpm": False,
            "zc": False,
            "credit_lines": 64,
            "app_work": True,
            "rpc_bytes": sz,
            "window": 256,
            "threads": 4,
        })
    specs.append({
        "part": "part2_controls",
        "mode_group": "direct_noaw",
        "cfg": "direct_noaw_4096B_w256",
        "card_mode": "direct",
        "host_mode": "direct",
        "rpm": False,
        "zc": False,
        "credit_lines": 0,
        "app_work": False,
        "rpc_bytes": 4096,
        "window": 256,
        "threads": 4,
    })
    return specs


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    env_before = check_env_all()
    print("ENV BEFORE:", json.dumps(env_before), flush=True)

    specs = build_specs()
    # Group by (card_mode, host_mode, rpm, zc, credit_lines, app_work) to avoid unnecessary restarts within a rep
    groups = {}
    for sp in specs:
        gk = (sp["mode_group"], sp["card_mode"], sp["host_mode"], sp["rpm"], sp["zc"], sp["credit_lines"], sp["app_work"])
        groups.setdefault(gk, []).append(sp)

    stack = StackManager()
    pmu = HostPmu(cpu=40)
    total_runs = REPS * (1 + len(specs))
    done = 0
    failed = 0

    order_table = [
        ["card_echo", "direct", "direct_cl64", "direct_noaw", "sdp_def", "sdp_rpm_zc"],
        ["sdp_def", "sdp_rpm_zc", "direct", "direct_noaw", "direct_cl64", "card_echo"],
        ["direct", "sdp_rpm_zc", "card_echo", "sdp_def", "direct_cl64", "direct_noaw"],
    ]

    try:
        with open(RUNS_PATH, "w") as out:
            for rep in range(REPS):
                # 1. Idle calibration each rep (must run with host stopped)
                stack.stop_host()
                cal_rec = run_idle_cal(pmu, rep)
                out.write(json.dumps(cal_rec) + "\n")
                out.flush()
                done += 1
                idle_kps = cal_rec["cal"]["app_work_kps"]
                if not cal_rec["verified"]:
                    failed += 1
                print(
                    f"[{done}/{total_runs}] rep={rep} idle_cal: {idle_kps:.2f} k/s "
                    f"({cal_rec['cal']['ns_per_unit']:.1f} ns) cpu={cal_rec['pmu']['cpu40_ghz']:.3f}GHz ok={cal_rec['verified']}",
                    flush=True,
                )

                rank_map = {name: i for i, name in enumerate(order_table[rep % len(order_table)])}
                for gk in sorted(groups.keys(), key=lambda x: rank_map[x[0]]):
                    for sp in groups[gk]:
                        rec = run_spec(stack, pmu, sp, rep, idle_kps)
                        retries = 0
                        while not rec["verified"] and retries < 2:
                            retries += 1
                            print(f"  RETRY#{retries} rep={rep} {sp['cfg']} errs={rec['verify_errors']}", flush=True)
                            rec = run_spec(stack, pmu, sp, rep, idle_kps)
                        out.write(json.dumps(rec) + "\n")
                        out.flush()
                        done += 1
                        if not rec["verified"]:
                            failed += 1
                        c = rec["client"]
                        p = rec["pmu"]
                        cpu_s = f"{rec['host_cpu_pct']:5.1f}%" if rec["host_cpu_pct"] is not None else "  n/a "
                        print(
                            f"[{done}/{total_runs}] rep={rep} {sp['cfg']:24s} "
                            f"{c['achieved_mrps']:6.3f}M/s pay={c['payload_gbps_unidir']:5.2f}G "
                            f"({c['payload_gbps_bidir']:6.2f}bi) wire={c['wire_gbps_unidir']:5.2f}G "
                            f"cpu={cpu_s} p50={c['tot_p50_us']:5.1f}us cha={p['cha_inflight']:4.1f} ok={rec['verified']}",
                            flush=True,
                        )
    finally:
        stack.stop_card()
        pmu.close()

    env_after = check_env_all()
    ENV_PATH.write_text(json.dumps({"before": env_before, "after": env_after, "total_runs": done, "failed": failed}, indent=2))
    print(f"DONE total={done} failed={failed}", flush=True)
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
