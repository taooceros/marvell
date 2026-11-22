#!/usr/bin/env python3
"""MRV-137 full interleaved 3-machine 100GbE multi-host-core scaling harness.

Zero sudo, zero SDP, zero Intel DSA (MRV-49 & MRV-82):
  - Part 0: Multi-core host idle calibration (`H ∈ {1, 2, 4}`, `64`-step amortized
            rdtsc after 4.5 s busy ramp for H>1, verifying 3.85-3.90 GHz per core, n=3)
  - Part 1: 4C `card_echo` physical ceiling (`W=64` & `W=256`), `1H` baseline upgrade
            single-variable ladder (`64B+tx_pool` -> `128B+tx_pool` -> `128B+bypass` ->
            `128B+bypass+per-card 16`), and `128 B` exclusive sub-ring `C_line`
            boundary verification (`C_line ∈ {0, 48, 64, 80, 96, 128}` on H=1 & H=4,
            plus unshaped `W = 64/128` slow-cliff check, n=3)
  - Part 2: Core single-variable host-core scaling `H ∈ {1, 2, 4}` (fixed `128 B`
            exclusive BAR4 sub-ring, immediate-after-BAR4-read 8-line hysteresis
            cursor, stack-bypass MOVDIR64B, fixed `C_line = 64` = `16`/card, fixed
            `W = 256` = `64`/QP, 1 s FastHist):
            closed-loop `W ∈ {32, 64, 128, 256}` + open-loop `B = 8` micro-bursts
            `4.0..9.0 Mreq/s` (`16..36 M lines/s`), plus `H=1` shared-64 reference (n=3)
  - Part 3: `4C + C_line = 64` host CPU headroom (`--app-work`, zero-traffic + `CBR`
            at `16 / 24 / 30 M lines/s` across `H ∈ {1, 2, 4}`), with ZERO mid-stream
            card control TCP traffic (eliminating MRV-135 CBR tail artifact, n=3)

All PMUs (all 4 Socket-1 physical cores `cpus 40-43` + 32x CHA + IRP1 + IIO1 + Mesh;
Card TAD) are 100% non-multiplexed and captured strictly inside the steady-state
`[+150ms, +1150ms]` window (`1.000 s`) aligned with lock-free `cpu 44` `SNAP`.
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

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv137/logs/mrv137-2026-11-22")
RUNS_PATH = OUT_DIR / "runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"

HOST_BIN = "/home/hongtao/dev/marvell-mrv137/testbed/target/release/mrv137_host"
CARD_BIN = "/root/bench/mrv137_card"
MERC_BIN = "/tmp/mrv137/mrv137_client"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]

WORKER_CPUS = (40, 41, 42, 43)
REPS = 3


# ---------------------------------------------------------------------------
# 100% Non-Multiplexed Sapphire Rapids 4-Core + Socket-1 Uncore PMUs
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

SOCKET1_UNCORE_CPU = 32
REF_HZ = 2.2e9


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
            raise OSError(err, f"perf_event_open(type={ptype}, cfg={config:#x}, cpu={cpu}) failed: {os.strerror(err)}")
        self.fd = fd
        rc = LIBC.ioctl(self.fd, PERF_EVENT_IOC_ENABLE, 0)
        if rc != 0:
            err = ctypes.get_errno()
            os.close(self.fd)
            raise OSError(err, f"PERF_EVENT_IOC_ENABLE failed: {os.strerror(err)}")

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
    def __init__(self, cpus=WORKER_CPUS):
        self.cpus = tuple(cpus)
        self.core_fds = {}
        for c in self.cpus:
            self.core_fds[c] = {
                "cycles": PerfFd(0, 0, c),
                "instr": PerfFd(0, 1, c),
                "ref_cycles": PerfFd(0, 9, c),
                "llc_loads": PerfFd(3, 0x00002, c),
                "llc_load_misses": PerfFd(3, 0x10002, c),
                "sb_stall": PerfFd(4, 0x08A2, c),
            }
        self.uncore_fds = {
            "mesh_clk": PerfFd(_pmu_type("uncore_m2pcie_1"), 0x01, SOCKET1_UNCORE_CPU),
            "irp1_occ": PerfFd(_pmu_type("uncore_irp_1"), 0x0C, SOCKET1_UNCORE_CPU),
            "irp1_ins": PerfFd(_pmu_type("uncore_irp_1"), 0x0E, SOCKET1_UNCORE_CPU),
        }
        iio1_t = _pmu_type("uncore_iio_1")
        self.uncore_fds["iio1_cpu_wr"] = PerfFd(iio1_t, 0x0007_0FF0_0000_01C1, SOCKET1_UNCORE_CPU)
        self.uncore_fds["iio1_inb_wr"] = PerfFd(iio1_t, 0x0007_0FF0_0000_0184, SOCKET1_UNCORE_CPU)
        self.uncore_fds["iio1_inb_rd"] = PerfFd(iio1_t, 0x0007_0FF0_0000_0484, SOCKET1_UNCORE_CPU)
        self.uncore_fds["iio1_clk"] = PerfFd(iio1_t, 0x01, SOCKET1_UNCORE_CPU)

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
            "cores": {c: {k: fd.read() for k, fd in d.items()} for c, d in self.core_fds.items()},
            "u": {k: fd.read() for k, fd in self.uncore_fds.items()},
            "wcilf_ins": [fd.read() for fd in self.cha_wcilf_ins],
            "wcilf_occ": [fd.read() for fd in self.cha_wcilf_occ],
            "io_hit": [fd.read() for fd in self.cha_io_hit],
            "io_miss": [fd.read() for fd in self.cha_io_miss],
        }

    def measure_ghz(self, active_cpus, wait_s=0.20):
        s0 = {c: (self.core_fds[c]["cycles"].read()[0], self.core_fds[c]["ref_cycles"].read()[0]) for c in active_cpus}
        time.sleep(wait_s)
        s1 = {c: (self.core_fds[c]["cycles"].read()[0], self.core_fds[c]["ref_cycles"].read()[0]) for c in active_cpus}
        return {
            c: ((s1[c][0] - s0[c][0]) / max(s1[c][1] - s0[c][1], 1)) * (REF_HZ / 1e9)
            for c in active_cpus
        }

    @staticmethod
    def delta(a, b, active_h=1):
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

        per_core = {}
        active_cpus = WORKER_CPUS[:active_h]
        for c in WORKER_CPUS:
            cs = {k: d1(a["cores"][c][k], b["cores"][c][k]) for k in a["cores"][c]}
            ghz = (cs["cycles"] / cs["ref_cycles"]) * (REF_HZ / 1e9) if cs["ref_cycles"] > 0 else 0.0
            sb_pct = cs["sb_stall"] * 100.0 / max(cs["cycles"], 1)
            per_core[str(c)] = {
                "ghz": round(ghz, 4),
                "cycles": cs["cycles"],
                "instr": cs["instr"],
                "llc_loads": cs["llc_loads"],
                "llc_load_misses": cs["llc_load_misses"],
                "sb_stall_pct": round(sb_pct, 4),
            }

        u = {k: d1(a["u"][k], b["u"][k]) for k in a["u"]}
        wcilf_ins = sum(d1(x, y) for x, y in zip(a["wcilf_ins"], b["wcilf_ins"]))
        wcilf_occ = sum(d1(x, y) for x, y in zip(a["wcilf_occ"], b["wcilf_occ"]))
        io_hit = sum(d1(x, y) for x, y in zip(a["io_hit"], b["io_hit"]))
        io_miss = sum(d1(x, y) for x, y in zip(a["io_miss"], b["io_miss"]))
        itom = io_hit + io_miss
        ddio_pct = (io_hit * 100.0 / itom) if itom > 0 else 100.0
        mesh_ghz = (u["mesh_clk"] / dt) / 1e9
        cha_inflight = wcilf_occ / max(u["mesh_clk"], 1)
        irp1_inflight = u["irp1_occ"] / max(u["iio1_clk"], 1)

        act_ghzs = [per_core[str(c)]["ghz"] for c in active_cpus]
        act_sbs = [per_core[str(c)]["sb_stall_pct"] for c in active_cpus]
        act_llc_m = sum(per_core[str(c)]["llc_load_misses"] for c in active_cpus)
        act_cyc = sum(per_core[str(c)]["cycles"] for c in active_cpus)
        act_ins = sum(per_core[str(c)]["instr"] for c in active_cpus)

        return {
            "dt_s": round(dt, 6),
            "min_running_pct": round(min_pct, 3),
            "active_cpus": list(active_cpus),
            "active_ghz_min": round(min(act_ghzs), 4),
            "active_ghz_mean": round(sum(act_ghzs) / len(act_ghzs), 4),
            "active_sb_stall_mean_pct": round(sum(act_sbs) / len(act_sbs), 4),
            "active_llc_load_misses": act_llc_m,
            "active_cycles": act_cyc,
            "active_instr": act_ins,
            "per_core": per_core,
            "mesh_ghz": round(mesh_ghz, 4),
            "iio1_cpu_wr": u["iio1_cpu_wr"],
            "iio1_inb_wr": u["iio1_inb_wr"],
            "iio1_inb_rd": u["iio1_inb_rd"],
            "iio1_clk": u["iio1_clk"],
            "irp1_occ": u["irp1_occ"],
            "irp1_ins": u["irp1_ins"],
            "irp1_inflight": round(irp1_inflight, 4),
            "cha_ia_wcilf": wcilf_ins,
            "cha_wcilf_occ": wcilf_occ,
            "cha_inflight": round(cha_inflight, 4),
            "cha_io_hit_itom": io_hit,
            "cha_io_miss_itom": io_miss,
            "ddio_hit_pct": round(ddio_pct, 4),
        }

    def close(self):
        for d in self.core_fds.values():
            for fd in d.values():
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
    govs = {
        f"cpu{c}": pathlib.Path(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_governor").read_text().strip()
        for c in WORKER_CPUS
    }
    maxs = {
        f"cpu{c}": int(pathlib.Path(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_max_freq").read_text().strip())
        for c in WORKER_CPUS
    }
    uncore = {
        p.parent.name: int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz"))
    }
    edac_host = [
        int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count"))
    ]
    assert govs["cpu40"] == "performance", f"govs={govs}"
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
        "saturn": {"govs": govs, "max_khz": maxs, "uncore_khz": uncore, "edac_ue_sum": sum(edac_host)},
        "cn106": card_d,
        "mercury": merc_d,
    }


# ---------------------------------------------------------------------------
# Card & Host lifecycle managers
# ---------------------------------------------------------------------------
class StackManager:
    def __init__(self, pmu: HostPmu):
        self.pmu = pmu
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
            sh(["rm", "-f", "/dev/hugepages/m137hmap_*"], check=False)

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m137 C-c 2>/dev/null || true; sleep 0.6; "
            "tmux kill-session -t m137 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/m137*"
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
            cmd = f"{CARD_BIN} -l 3-6 -a 0002:02:00.0 --file-prefix=m137 -- --mode card-echo"
        elif card_mode == "direct":
            ready_tok = "direct ready"
            cmd = f"{CARD_BIN} -l 3-6 -a 0002:02:00.0 --file-prefix=m137 -- --mode direct"
        else:
            raise ValueError(card_mode)

        ssh_card(
            f'tmux new-session -d -s m137 "{cmd} > /tmp/m137_card.log 2>&1"; '
            f'for i in $(seq 1 50); do grep -q "MRV137_CARD {ready_tok}" /tmp/m137_card.log && break; sleep 0.2; done; '
            f'grep "MRV137_CARD {ready_tok}" /tmp/m137_card.log'
        )
        self.card_key = card_mode

    def ensure_host(
        self,
        host_mode: str,
        host_cores: int,
        credit_lines: int,
        credit_hyst: int,
        bar4_stride: int,
        per_card_credit: bool,
        use_tx_pool: bool,
        app_work: bool,
    ):
        if host_mode == "none":
            self.stop_host()
            return
        key = (host_mode, host_cores, credit_lines, credit_hyst, bar4_stride, per_card_credit, use_tx_pool, app_work)
        if self.host_key == key and self.host_proc is not None and self.host_proc.poll() is None:
            return
        self.stop_host()
        cmd = [
            HOST_BIN, "-l", "40", "-a", "0000:99:02.0",
            "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m137h", "--",
            "--host-cores", str(host_cores),
            "--credit-lines", str(credit_lines),
            "--credit-hyst", str(credit_hyst),
            "--bar4-stride", str(bar4_stride),
        ]
        if per_card_credit:
            cmd.append("--per-card-credit")
        if use_tx_pool:
            cmd.append("--use-tx-pool")
        if app_work:
            cmd.append("--app-work")
        logf = open("/tmp/m137_host_run.log", "w")
        self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        logf.close()
        for _ in range(60):
            if self.host_proc.poll() is not None:
                err = pathlib.Path("/tmp/m137_host_run.log").read_text()
                raise RuntimeError(f"mrv137_host exited early:\n{err}")
            txt = pathlib.Path("/tmp/m137_host_run.log").read_text()
            if "MRV137_HOST READY" in txt:
                # Zero-sudo frequency ramp for schedutil cores cpu41..43 (MRV-89 verified method)
                if host_cores > 1:
                    time.sleep(4.2)
                    active_cpus = WORKER_CPUS[:host_cores]
                    for _w in range(15):
                        ghzs = self.pmu.measure_ghz(active_cpus, wait_s=0.20)
                        if min(ghzs.values()) >= 3.83:
                            break
                self.host_key = key
                return
            time.sleep(0.1)
        raise RuntimeError(f"mrv137_host timeout:\n{pathlib.Path('/tmp/m137_host_run.log').read_text()}")


def run_idle_cal(pmu: HostPmu, host_cores: int, rep: int):
    ramp_ms = 4500 if host_cores > 1 else 300
    # Snapshot PMU strictly during the 1.0 s calibrated window? Better: start process, wait ramp_ms + 100ms,
    # measure 0.8 s inside the 1.0 s window so GHz is pure steady-state at 3.90 GHz!
    proc = subprocess.Popen(
        [HOST_BIN, "--idle-cal-ms", "1000", "--host-cores", str(host_cores), "--ramp-ms", str(ramp_ms)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(ramp_ms / 1000.0 + 0.18)
    s0 = pmu.snap()
    time.sleep(0.75)
    s1 = pmu.snap()
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    pm = pmu.delta(s0, s1, active_h=host_cores)
    cal = None
    for line in out.splitlines():
        if line.strip().startswith("{"):
            cal = json.loads(line.strip())
            break
    assert cal is not None, out
    errs = []
    if pm["min_running_pct"] < 99.9:
        errs.append(f"pmu_pct={pm['min_running_pct']}")
    if pm["active_ghz_min"] < 3.80:
        errs.append(f"active_ghz_min={pm['active_ghz_min']}")
    for entry in cal["per_cpu"]:
        if entry["app_work_kps"] < 3900.0:
            errs.append(f"low_kps_cpu{entry['cpu']}={entry['app_work_kps']}")
    return {
        "part": "part0_cal",
        "cfg": f"idle_cal_h{host_cores}",
        "rep": rep,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "spec": {"part": "part0_cal", "cfg": f"idle_cal_h{host_cores}", "host_cores": host_cores, "ramp_ms": ramp_ms},
        "cal": cal,
        "pmu": pm,
        "verified": len(errs) == 0,
        "verify_errors": errs,
    }


def run_zero_traffic_cpu(stack: StackManager, pmu: HostPmu, host_cores: int, rep: int):
    stack.ensure_card("direct")
    stack.ensure_host("direct", host_cores, 64, 8, 128, True, False, True)
    tcp_cmd("127.0.0.1", 7531, "RESET")
    time.sleep(0.10)
    h0 = tcp_cmd("127.0.0.1", 7531, "SNAP")
    p0 = pmu.snap()
    time.sleep(1.00)
    p1 = pmu.snap()
    h1 = tcp_cmd("127.0.0.1", 7531, "SNAP")
    host_st = tcp_cmd("127.0.0.1", 7531, "STATS")
    pm = pmu.delta(p0, p1, active_h=host_cores)
    h_dt = max(h1["dt_s"] - h0["dt_s"], 1e-9)
    work_kps = (h1["app_work_units"] - h0["app_work_units"]) / h_dt / 1e3
    pw_work_kps = []
    for w0, w1 in zip(h0["per_worker"], h1["per_worker"]):
        wdt = max(w1["dt_s"] - w0["dt_s"], 1e-9)
        pw_work_kps.append(round((w1["work"] - w0["work"]) / wdt / 1e3, 3))
    errs = []
    if pm["min_running_pct"] < 99.9:
        errs.append(f"pmu_pct={pm['min_running_pct']}")
    if pm["active_ghz_min"] < 3.80:
        errs.append(f"active_ghz_min={pm['active_ghz_min']}")
    if host_st["rx_rpcs"] != 0 or host_st["bad"] != 0 or host_st["torn"] != 0:
        errs.append(f"host_err={host_st}")
    return {
        "part": "part3_cpu_4c",
        "cfg": f"h{host_cores}_zero_traffic_poll",
        "rep": rep,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "spec": {
            "part": "part3_cpu_4c",
            "cfg": f"h{host_cores}_zero_traffic_poll",
            "host_cores": host_cores,
            "card_cores": 4,
            "credit_lines": 64,
            "per_card_credit": True,
            "app_work": True,
        },
        "client": None,
        "card": None,
        "host": host_st,
        "pmu": pm,
        "card_pmu": None,
        "steady_window": {
            "host_dt_s": round(h_dt, 6),
            "app_work_kps": round(work_kps, 3),
            "per_worker_work_kps": pw_work_kps,
        },
        "verified": len(errs) == 0,
        "verify_errors": errs,
    }


def run_rpc_spec(stack: StackManager, pmu: HostPmu, spec: dict, rep: int):
    card_mode = spec["card_mode"]
    host_mode = spec["host_mode"]
    host_cores = spec["host_cores"]
    credit_lines = spec.get("credit_lines", 64)
    credit_hyst = spec.get("credit_hyst", 8 if credit_lines > 0 else 0)
    bar4_stride = spec.get("bar4_stride", 128)
    per_card_credit = spec.get("per_card_credit", True)
    use_tx_pool = spec.get("use_tx_pool", False)
    app_work = spec.get("app_work", False)

    stack.ensure_card(card_mode)
    stack.ensure_host(
        host_mode,
        host_cores,
        credit_lines,
        credit_hyst,
        bar4_stride,
        per_card_credit,
        use_tx_pool,
        app_work,
    )

    seed = 0x1370_2026_1122 + rep * 100003 + (abs(hash(spec["cfg"])) & 0xFFFF)
    warmup_ms = spec.get("warmup_ms", 400)
    duration_ms = spec.get("duration_ms", 1500)

    mcmd = (
        f"{MERC_BIN} --threads 4 --card-queues 4 "
        f"--mode {spec['drive']} --workload 256 --window {spec['window']} "
        f"--warmup-ms {warmup_ms} --duration-ms {duration_ms} --seed {seed}"
    )
    if spec["drive"] in ("poisson", "burst", "cbr"):
        mcmd += f" --rate-krps {spec['rate_krps']:.3f}"
    if spec["drive"] == "burst":
        mcmd += f" --burst {spec.get('burst', 8)}"

    proc = subprocess.Popen(
        MERC_SSH + [mcmd],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    t0_tad = None
    got_warmup = False
    t_wait = time.monotonic()
    while time.monotonic() - t_wait < 20.0:
        line = proc.stdout.readline()
        if not line:
            break
        if "PROBE_DONE" in line:
            # Uniform across ALL modes including CBR: RESET & TAD snap BEFORE traffic starts!
            tcp_cmd("10.10.0.2", 7529, "RESET")
            if host_mode != "none":
                tcp_cmd("127.0.0.1", 7531, "RESET")
            t0_tad = tcp_cmd("10.10.0.2", 7529, "PMU_SNAP")
            proc.stdin.write("GO\n")
            proc.stdin.flush()
        elif "WARMUP_DONE" in line:
            got_warmup = True
            break

    if not got_warmup:
        proc.kill()
        out_r, err_r = proc.communicate(timeout=5)
        raise RuntimeError(f"missed WARMUP_DONE for {spec['cfg']}:\n{out_r}\n{err_r}")

    # Pure steady-state 1.000 s Host PMU + lock-free cpu 44 SNAP strictly inside [+150ms, +1150ms].
    # ZERO mid-stream TCP traffic to 10.10.0.2 for ALL modes (including CBR)!
    time.sleep(0.15)
    h0 = tcp_cmd("127.0.0.1", 7531, "SNAP") if host_mode != "none" else None
    p0 = pmu.snap()
    time.sleep(1.00)
    p1 = pmu.snap()
    h1 = tcp_cmd("127.0.0.1", 7531, "SNAP") if host_mode != "none" else None
    pm = pmu.delta(p0, p1, active_h=max(host_cores, 1))
    out_rest, err_rest = proc.communicate(timeout=25)
    t1_tad = tcp_cmd("10.10.0.2", 7529, "PMU_SNAP")
    card_st = tcp_cmd("10.10.0.2", 7529, "STATS")
    host_st = tcp_cmd("127.0.0.1", 7531, "STATS") if host_mode != "none" else None
    cpm = delta_tad(t0_tad, t1_tad)

    steady_win = None
    if h0 is not None and h1 is not None and host_st is not None:
        h_dt = max(h1["dt_s"] - h0["dt_s"], 1e-9)
        h_lines = h1["bar4_lines_written"] - h0["bar4_lines_written"]
        h_rpcs = h1["rx_rpcs"] - h0["rx_rpcs"]
        h_cwaits = h1["credit_waits"] - h0["credit_waits"]
        h_work = h1["app_work_units"] - h0["app_work_units"]
        line_rate = h_lines / h_dt
        iio_ratio = (pm["iio1_cpu_wr"] / pm["dt_s"]) / line_rate if line_rate > 0 else 0.0
        cha_ratio = (pm["cha_ia_wcilf"] / pm["dt_s"]) / line_rate if line_rate > 0 else 0.0
        tot_lines = max(host_st["bar4_lines_written"], 1)
        tad_snp_per_line = cpm["tad_snp_msh_out_any"] / tot_lines
        pw_work_kps = []
        for w0, w1 in zip(h0["per_worker"], h1["per_worker"]):
            wdt = max(w1["dt_s"] - w0["dt_s"], 1e-9)
            pw_work_kps.append(round((w1["work"] - w0["work"]) / wdt / 1e3, 3))
        steady_win = {
            "host_dt_s": round(h_dt, 6),
            "bar4_lines": h_lines,
            "rx_rpcs": h_rpcs,
            "credit_waits": h_cwaits,
            "app_work_units": h_work,
            "app_work_kps": round(h_work / h_dt / 1e3, 3),
            "per_worker_work_kps": pw_work_kps,
            "bar4_mlines_per_s": round(line_rate / 1e6, 4),
            "iio_cpu_wr_ratio": round(iio_ratio, 4),
            "cha_wcilf_ratio": round(cha_ratio, 4),
            "tad_snp_per_line": round(tad_snp_per_line, 4),
        }

    client_st = None
    for line in out_rest.splitlines():
        if line.startswith("MRV137_CLIENT_JSON "):
            client_st = json.loads(line[len("MRV137_CLIENT_JSON "):])
            break
    if client_st is None:
        raise RuntimeError(f"client crashed ({proc.returncode}) {spec['cfg']}:\n{out_rest}\n{err_rest}")

    # Strict verification gate (uniform across ALL modes including CBR!)
    errs = []
    if not client_st.get("verified", False):
        errs.append(f"client_unverified lost={client_st['lost']} bad={client_st['bad']} torn={client_st['torn']}")
    for k in ("bad_wire", "bad_host", "torn_bar4", "dma_failed", "eth_tx_drops",
              "eth_imissed", "eth_ierrors", "eth_oerrors", "eth_rx_nombuf"):
        if card_st.get(k, 0) != 0:
            errs.append(f"card_{k}={card_st.get(k)}")
    xs = card_st.get("eth_xstats", {})
    if xs.get("rx_err", 0) != 0 or xs.get("rx_fcs", 0) != 0 or xs.get("rx_drop", 0) != 0 or xs.get("tx_drop", 0) != 0:
        errs.append(f"card_xstats_err={xs}")
    exp_pkts = client_st["tx_pkts_total"]
    if card_st["eth_ipackets"] != exp_pkts or card_st["eth_opackets"] != exp_pkts:
        errs.append(f"eth_pkts={card_st['eth_ipackets']}/{card_st['eth_opackets']} != {exp_pkts}")
    if host_st is not None:
        exp_rpcs = client_st["tx_rpcs_total"]
        if host_st["rx_rpcs"] != exp_rpcs or host_st["tx_rpcs"] != exp_rpcs:
            errs.append(f"host_rpcs={host_st['rx_rpcs']}/{host_st['tx_rpcs']} != {exp_rpcs}")
        if host_st["bad"] != 0 or host_st["torn"] != 0:
            errs.append(f"host_err bad={host_st['bad']} torn={host_st['torn']}")
        if "N0=0," not in host_st.get("tx_pool_numa", ""):
            errs.append(f"tx_pool_numa={host_st.get('tx_pool_numa')}")
    pc = card_st.get("per_core_rpcs_in", [])
    if len(pc) != 4 or min(pc) == 0:
        errs.append(f"rss_unbalanced={pc}")
    if pm["min_running_pct"] < 99.9:
        errs.append(f"host_pmu_pct={pm['min_running_pct']}")
    if cpm["min_running_pct"] < 99.9:
        errs.append(f"card_pmu_pct={cpm['min_running_pct']}")
    if pm["mesh_ghz"] < 2.15:
        errs.append(f"mesh_ghz={pm['mesh_ghz']}")
    if host_mode != "none" and pm["active_ghz_min"] < 3.80:
        errs.append(f"active_ghz_min={pm['active_ghz_min']}")

    return {
        "part": spec["part"],
        "cfg": spec["cfg"],
        "rep": rep,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "spec": spec,
        "client": client_st,
        "card": card_st,
        "host": host_st,
        "pmu": pm,
        "card_pmu": cpm,
        "steady_window": steady_win,
        "verified": len(errs) == 0,
        "verify_errors": errs,
    }


def build_specs():
    specs = []

    def add(part, cfg, card_mode, host_mode, host_cores, drive, window,
            rate_krps=0.0, burst=8, credit_lines=64, credit_hyst=8, bar4_stride=128,
            per_card_credit=True, use_tx_pool=False, app_work=False, warmup_ms=400, duration_ms=1500):
        specs.append({
            "part": part,
            "cfg": cfg,
            "card_mode": card_mode,
            "host_mode": host_mode,
            "host_cores": host_cores,
            "card_cores": 4,
            "threads": 4,
            "workload": "256",
            "drive": drive,
            "window": window,
            "rate_krps": rate_krps,
            "burst": burst,
            "credit_lines": credit_lines,
            "credit_hyst": credit_hyst if credit_lines > 0 else 0,
            "bar4_stride": bar4_stride,
            "per_card_credit": per_card_credit,
            "use_tx_pool": use_tx_pool,
            "app_work": app_work,
            "warmup_ms": warmup_ms,
            "duration_ms": duration_ms,
        })

    # -----------------------------------------------------------------------
    # Part 1: Physical 4C card_echo ceiling, 1H baseline ablation ladder, & C_line boundaries
    # -----------------------------------------------------------------------
    add("part1_baseline", "card_echo_4c_w64", "card_echo", "none", 0, "closed", 64, credit_lines=0)
    add("part1_baseline", "card_echo_4c_w256", "card_echo", "none", 0, "closed", 256, credit_lines=0)

    # 1H single-variable upgrade ladder at W=256, C_line=64, hyst=8
    add("part1_baseline", "h1_s64_cl64_txp_shr_w256", "direct", "direct", 1, "closed", 256,
        credit_lines=64, bar4_stride=64, per_card_credit=False, use_tx_pool=True)
    add("part1_baseline", "h1_s128_cl64_txp_shr_w256", "direct", "direct", 1, "closed", 256,
        credit_lines=64, bar4_stride=128, per_card_credit=False, use_tx_pool=True)
    add("part1_baseline", "h1_s128_cl64_shr_w256", "direct", "direct", 1, "closed", 256,
        credit_lines=64, bar4_stride=128, per_card_credit=False, use_tx_pool=False)

    # 128 B exclusive sub-ring C_line boundary verification on H=1 and H=4 (W=256 + unshaped W=64/128)
    for w in (64, 128, 256):
        add("part1_baseline", f"h1_s128_cl0_w{w}", "direct", "direct", 1, "closed", w,
            credit_lines=0, bar4_stride=128, per_card_credit=False)
    for r in (4000, 6000, 8000, 9000):
        add("part1_baseline", f"h1_s128_cl0_b8_r{r}", "direct", "direct", 1, "burst", 256,
            rate_krps=float(r), burst=8, credit_lines=0, bar4_stride=128, per_card_credit=False)
    for cl in (48, 80):
        add("part1_baseline", f"h1_s128_cl{cl}_pcc_w256", "direct", "direct", 1, "closed", 256,
            credit_lines=cl, bar4_stride=128, per_card_credit=True)
    add("part1_baseline", "h4_s128_cl0_w256", "direct", "direct", 4, "closed", 256,
        credit_lines=0, bar4_stride=128, per_card_credit=False)
    for cl in (48, 80, 96, 128):
        add("part1_baseline", f"h4_s128_cl{cl}_pcc_w256", "direct", "direct", 4, "closed", 256,
            credit_lines=cl, bar4_stride=128, per_card_credit=True)

    # -----------------------------------------------------------------------
    # Part 2: Core single-variable H ∈ {1, 2, 4} at fixed 128 B stride + C_line=64 (16/card) + W=256
    # -----------------------------------------------------------------------
    for h in (1, 2, 4):
        for w in (32, 64, 128, 256):
            add("part2_scale_h", f"h{h}_cl64_w{w}", "direct", "direct", h, "closed", w,
                credit_lines=64, credit_hyst=8, bar4_stride=128, per_card_credit=True)
        for r in (4000, 5000, 6000, 7000, 8000, 8500, 9000):
            add("part2_scale_h", f"h{h}_cl64_b8_r{r}", "direct", "direct", h, "burst", 256,
                rate_krps=float(r), burst=8, credit_lines=64, credit_hyst=8, bar4_stride=128, per_card_credit=True)

    # Diagnostic reference: H=1 shared 64-line pool under B=8 (isolates credit-pooling vs core-count effect)
    for r in (4000, 6000, 7000, 8000):
        add("part2_scale_h", f"h1_cl64_shr_b8_r{r}", "direct", "direct", 1, "burst", 256,
            rate_krps=float(r), burst=8, credit_lines=64, credit_hyst=8, bar4_stride=128, per_card_credit=False)

    # -----------------------------------------------------------------------
    # Part 3: 4C + C_line = 64 host CPU cost (--app-work, CBR, zero mid-stream card TCP)
    # -----------------------------------------------------------------------
    for h in (1, 2, 4):
        for r in (4000, 6000, 7500):
            add("part3_cpu_4c", f"h{h}_cl64_cbr_work_r{r}", "direct", "direct", h, "cbr", 256,
                rate_krps=float(r), credit_lines=64, credit_hyst=8, bar4_stride=128,
                per_card_credit=True, app_work=True)

    return specs


def group_key(spec):
    return (
        spec["card_mode"],
        spec["host_mode"],
        spec["host_cores"],
        spec["bar4_stride"],
        spec["credit_lines"],
        spec["credit_hyst"],
        spec["per_card_credit"],
        spec["use_tx_pool"],
        spec["app_work"],
    )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    env_before = check_env_all()
    print(f"ENV BEFORE: {json.dumps(env_before)}", flush=True)

    specs = build_specs()
    groups = {}
    for s in specs:
        groups.setdefault(group_key(s), []).append(s)
    group_keys = list(groups.keys())

    pmu = HostPmu(cpus=WORKER_CPUS)
    stack = StackManager(pmu)
    total_runs = REPS * (3 + 3 + len(specs))
    done = 0
    failed = 0
    retries_total = 0

    h_orders = {
        0: {1: 0, 2: 1, 4: 2, 0: -1},
        1: {4: 0, 2: 1, 1: 2, 0: -1},
        2: {2: 0, 4: 1, 1: 2, 0: -1},
    }

    try:
        with open(RUNS_PATH, "w") as out:
            for rep in range(REPS):
                # 1. Idle calibration for H=1,2,4 (stop host so CPUs are idle before ramp)
                stack.stop_host()
                for hc in (1, 2, 4):
                    cal_rec = run_idle_cal(pmu, hc, rep)
                    out.write(json.dumps(cal_rec) + "\n")
                    out.flush()
                    done += 1
                    print(
                        f"[{done}/{total_runs}] rep={rep} {cal_rec['cfg']:26s} "
                        f"tot={cal_rec['cal']['total_app_work_kps']:.1f}k/s "
                        f"ghz_min={cal_rec['pmu']['active_ghz_min']:.3f} ok={cal_rec['verified']}",
                        flush=True,
                    )

                # 2. Zero-traffic poll overhead for H=1,2,4
                for hc in (1, 2, 4):
                    zt_rec = run_zero_traffic_cpu(stack, pmu, hc, rep)
                    out.write(json.dumps(zt_rec) + "\n")
                    out.flush()
                    done += 1
                    print(
                        f"[{done}/{total_runs}] rep={rep} {zt_rec['cfg']:26s} "
                        f"work={zt_rec['steady_window']['app_work_kps']:.1f}k/s "
                        f"ghz_min={zt_rec['pmu']['active_ghz_min']:.3f} ok={zt_rec['verified']}",
                        flush=True,
                    )

                # 3. Interleaved H-order groups across reps
                order_map = h_orders[rep % 3]

                def sort_rank(gk):
                    cm, hm, hc, st, cl, hy, pcc, txp, aw = gk
                    if cm == "card_echo":
                        return (0, 0, gk)
                    return (1 + (1 if aw else 0), order_map[hc], st, cl, pcc, txp, gk)

                for gk in sorted(group_keys, key=sort_rank):
                    for spec in groups[gk]:
                        rec = run_rpc_spec(stack, pmu, spec, rep)
                        for attempt in range(2):
                            if rec["verified"]:
                                break
                            retries_total += 1
                            print(f"  RETRY({attempt+1}) rep={rep} {spec['cfg']} errs={rec['verify_errors']}", flush=True)
                            rec = run_rpc_spec(stack, pmu, spec, rep)
                        out.write(json.dumps(rec) + "\n")
                        out.flush()
                        done += 1
                        if not rec["verified"]:
                            failed += 1
                        c = rec["client"]
                        p = rec["pmu"]
                        sw = rec["steady_window"]
                        extra = ""
                        if sw is not None:
                            if spec.get("app_work"):
                                extra = f" work={sw['app_work_kps']:.1f}k/s"
                            else:
                                extra = f" iio={sw['iio_cpu_wr_ratio']:.3f}x cha_q={p['cha_inflight']:.2f}"
                        print(
                            f"[{done}/{total_runs}] rep={rep} {spec['cfg']:26s} "
                            f"{c['achieved_mrps']*4:5.2f}Ml/s ({c['achieved_mrps']:.3f}M) "
                            f"tot50/99={c['tot_p50_us']:6.1f}/{c['tot_p99_us']:7.1f} w99={c['wire_p99_us']:5.1f} "
                            f"hrtt={rec['card']['host_rtt_p50_us']:4.1f}/{rec['card']['host_rtt_p99_us']:5.1f} "
                            f"ghz={p['active_ghz_min']:.2f}{extra} ok={rec['verified']}",
                            flush=True,
                        )
    finally:
        stack.stop_card()
        pmu.close()

    env_after = check_env_all()
    ENV_PATH.write_text(
        json.dumps(
            {
                "before": env_before,
                "after": env_after,
                "total_runs": done,
                "failed": failed,
                "retries_total": retries_total,
            },
            indent=2,
        )
    )
    print(f"DONE total={done} failed={failed} retries={retries_total}", flush=True)
    print(f"ENV AFTER: {json.dumps(env_after)}", flush=True)
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
