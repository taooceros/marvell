#!/usr/bin/env python3
"""MRV-135 full interleaved 3-machine 100GbE multi-core slow-cliff & credit-shaping harness.

Zero SDP, zero Intel DSA (MRV-49 & MRV-82):
  - Part 0: Fix 2 idle calibration (`64`-step amortized rdtsc, `n=3`)
  - Part 1: Fix 1+2 pure `1H+1C` DirectPush CPU re-verification (`zero_traffic`, `64 B @ 1.50M`,
            `Mix-Ads @ 1.20M`, `4 KiB @ 0.50M`, `n=3`)
  - Part 2: `4C` `eth0 4RxQ/4TxQ` RSS scaling & closed-loop `256 B` slow-cliff + `C_line`
            inverted-U sweep (`W = 4..128`, `C_line = 0, 16, 24, 32, 40, 48, 64`, `n=3`)
  - Part 3: Core single-variable experiment (`256 B`, `B = 8` micro-bursts, `W = 64`, `2.0..6.0 Mreq/s`
            = `8..24 M lines/s`): unshaped (`C_line = 0`) vs card-cursor `C_line = 40 / 48 / 64` (`n=3`)
  - Part 4: Diagnostic single-variable intra-RPC `256 B` cell-spacing check (`bar4_shared`,
            `C_line = 0` vs `48` across `4.0..6.0 Mreq/s` and closed `W = 64/128`, `n=3`)

All PMUs (Host Core + 32x CHA + IRP1 + IIO1 + Mesh; Card TAD) are 100% non-multiplexed and
captured strictly inside the steady-state `[+150ms, +1150ms]` window (`1.000 s`) aligned with
exact `host.bar4_lines_written` and `host.dt_s` snapshots.
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

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv135/logs/mrv135-2026-11-21")
RUNS_PATH = OUT_DIR / "runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"

HOST_BIN = "/home/hongtao/dev/marvell-mrv135/testbed/target/release/mrv135_host"
CARD_BIN = "/root/bench/mrv135_card"
MERC_BIN = "/tmp/mrv135/mrv135_client"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]

REPS = 3


# ---------------------------------------------------------------------------
# 100% Non-Multiplexed Sapphire Rapids Core + Socket-1 Uncore PMUs
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
    def __init__(self, cpu: int = 40):
        self.cpu = cpu
        self.fds = {
            "cycles": PerfFd(0, 0, cpu),
            "instr": PerfFd(0, 1, cpu),
            "ref_cycles": PerfFd(0, 9, cpu),
            "llc_loads": PerfFd(3, 0x00002, cpu),
            "llc_load_misses": PerfFd(3, 0x10002, cpu),
            "sb_stall": PerfFd(4, 0x08A2, cpu),
            "mesh_clk": PerfFd(_pmu_type("uncore_m2pcie_1"), 0x01, SOCKET1_UNCORE_CPU),
            "irp1_occ": PerfFd(_pmu_type("uncore_irp_1"), 0x0C, SOCKET1_UNCORE_CPU),
            "irp1_ins": PerfFd(_pmu_type("uncore_irp_1"), 0x0E, SOCKET1_UNCORE_CPU),
        }
        iio1_t = _pmu_type("uncore_iio_1")
        self.fds["iio1_cpu_wr"] = PerfFd(iio1_t, 0x0007_0FF0_0000_01C1, SOCKET1_UNCORE_CPU)
        self.fds["iio1_inb_wr"] = PerfFd(iio1_t, 0x0007_0FF0_0000_0184, SOCKET1_UNCORE_CPU)
        self.fds["iio1_inb_rd"] = PerfFd(iio1_t, 0x0007_0FF0_0000_0484, SOCKET1_UNCORE_CPU)
        self.fds["iio1_clk"] = PerfFd(iio1_t, 0x01, SOCKET1_UNCORE_CPU)

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
            "s": {k: fd.read() for k, fd in self.fds.items()},
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

        s = {k: d1(a["s"][k], b["s"][k]) for k in a["s"]}
        wcilf_ins = sum(d1(x, y) for x, y in zip(a["wcilf_ins"], b["wcilf_ins"]))
        wcilf_occ = sum(d1(x, y) for x, y in zip(a["wcilf_occ"], b["wcilf_occ"]))
        io_hit = sum(d1(x, y) for x, y in zip(a["io_hit"], b["io_hit"]))
        io_miss = sum(d1(x, y) for x, y in zip(a["io_miss"], b["io_miss"]))
        itom = io_hit + io_miss
        ddio_pct = (io_hit * 100.0 / itom) if itom > 0 else 100.0
        cpu_ghz = (s["cycles"] / s["ref_cycles"]) * (REF_HZ / 1e9) if s["ref_cycles"] > 0 else 0.0
        mesh_ghz = (s["mesh_clk"] / dt) / 1e9
        cha_inflight = wcilf_occ / max(s["mesh_clk"], 1)
        irp1_inflight = s["irp1_occ"] / max(s["iio1_clk"], 1)
        sb_stall_pct = (s["sb_stall"] * 100.0 / max(s["cycles"], 1))
        return {
            "dt_s": round(dt, 6),
            "min_running_pct": round(min_pct, 3),
            "cpu40_ghz": round(cpu_ghz, 4),
            "mesh_ghz": round(mesh_ghz, 4),
            "cycles": s["cycles"],
            "instr": s["instr"],
            "llc_loads": s["llc_loads"],
            "llc_load_misses": s["llc_load_misses"],
            "sb_stall_pct": round(sb_stall_pct, 4),
            "iio1_cpu_wr": s["iio1_cpu_wr"],
            "iio1_inb_wr": s["iio1_inb_wr"],
            "iio1_inb_rd": s["iio1_inb_rd"],
            "iio1_clk": s["iio1_clk"],
            "irp1_occ": s["irp1_occ"],
            "irp1_ins": s["irp1_ins"],
            "irp1_inflight": round(irp1_inflight, 4),
            "cha_ia_wcilf": wcilf_ins,
            "cha_wcilf_occ": wcilf_occ,
            "cha_inflight": round(cha_inflight, 4),
            "cha_io_hit_itom": io_hit,
            "cha_io_miss_itom": io_miss,
            "ddio_hit_pct": round(ddio_pct, 4),
        }

    def close(self):
        for fd in self.fds.values():
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
        "saturn": {"cpu40_gov": gov, "cpu40_khz": cur, "uncore_khz": uncore, "edac_ue_sum": sum(edac_host)},
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
            sh(["rm", "-f", "/dev/hugepages/m135hmap_*"], check=False)

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m135 C-c 2>/dev/null || true; sleep 0.6; "
            "tmux kill-session -t m135 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/m135*"
        )
        self.card_key = None

    def ensure_card(self, card_mode: str, card_cores: int, enable_dma: bool):
        key = (card_mode, card_cores, enable_dma)
        if self.card_key == key:
            return
        self.stop_card()
        ssh_card(
            "ip link set eth0 down 2>/dev/null || true; "
            "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
            'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
            "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko"
        )
        lcores = "3" if card_cores == 1 else f"3-{3 + card_cores - 1}"
        eal_devs = "-a 0002:02:00.0"
        app_extra = ""
        if enable_dma:
            eal_devs += " -a 0000:06:00.1 -a 0000:06:00.2"
            app_extra += " --enable-dma"
        if card_mode == "card_echo":
            ready_tok = "card_echo ready"
            cmd = f"{CARD_BIN} -l {lcores} {eal_devs} --file-prefix=m135 -- --mode card-echo"
        elif card_mode == "direct":
            ready_tok = "direct ready"
            cmd = f"{CARD_BIN} -l {lcores} {eal_devs} --file-prefix=m135 -- --mode direct{app_extra}"
        else:
            raise ValueError(card_mode)

        ssh_card(
            f'tmux new-session -d -s m135 "{cmd} > /tmp/m135_card.log 2>&1"; '
            f'for i in $(seq 1 50); do grep -q "MRV135_CARD {ready_tok}" /tmp/m135_card.log && break; sleep 0.2; done; '
            f'grep "MRV135_CARD {ready_tok}" /tmp/m135_card.log'
        )
        self.card_key = key

    def ensure_host(self, host_mode: str, credit_lines: int, bar4_shared: bool, app_work: bool):
        if host_mode == "none":
            self.stop_host()
            return
        key = (host_mode, credit_lines, bar4_shared, app_work)
        if self.host_key == key and self.host_proc is not None and self.host_proc.poll() is None:
            return
        self.stop_host()
        cmd = [
            HOST_BIN, "-l", "40", "-a", "0000:99:02.0",
            "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m135h", "--",
            "--credit-lines", str(credit_lines),
        ]
        if bar4_shared:
            cmd.append("--bar4-shared")
        if app_work:
            cmd.append("--app-work")
        logf = open("/tmp/m135_host_run.log", "w")
        self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        logf.close()
        for _ in range(60):
            if self.host_proc.poll() is not None:
                err = pathlib.Path("/tmp/m135_host_run.log").read_text()
                raise RuntimeError(f"mrv135_host exited early:\n{err}")
            txt = pathlib.Path("/tmp/m135_host_run.log").read_text()
            if "MRV135_HOST READY" in txt:
                self.host_key = key
                return
            time.sleep(0.1)
        raise RuntimeError(f"mrv135_host timeout:\n{pathlib.Path('/tmp/m135_host_run.log').read_text()}")


def run_idle_cal(pmu: HostPmu, rep: int):
    s0 = pmu.snap()
    res = sh([HOST_BIN, "--idle-cal-ms", "1000"], timeout=30)
    s1 = pmu.snap()
    pm = pmu.delta(s0, s1)
    cal = None
    for line in res.stdout.splitlines():
        if line.strip().startswith("{"):
            cal = json.loads(line.strip())
            break
    assert cal is not None, res.stdout
    ok = pm["min_running_pct"] >= 99.9 and cal["app_work_kps"] >= 3950.0
    return {
        "part": "part0_cal",
        "cfg": "app_work_idle_cal",
        "rep": rep,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "cal": cal,
        "pmu": pm,
        "verified": ok,
        "verify_errors": [] if ok else [f"cal={cal}, pmu={pm}"],
    }


def run_zero_traffic_cpu(stack: StackManager, pmu: HostPmu, rep: int):
    stack.ensure_card("direct", 1, True)
    stack.ensure_host("direct", 0, False, True)
    tcp_cmd("127.0.0.1", 7531, "RESET")
    p0 = pmu.snap()
    time.sleep(1.5)
    p1 = pmu.snap()
    host_st = tcp_cmd("127.0.0.1", 7531, "STATS")
    pm = pmu.delta(p0, p1)
    errs = []
    if pm["min_running_pct"] < 99.9:
        errs.append(f"pmu_pct={pm['min_running_pct']}")
    if pm["cpu40_ghz"] < 3.85:
        errs.append(f"cpu40_ghz={pm['cpu40_ghz']}")
    if host_st["rx_rpcs"] != 0 or host_st["bad"] != 0 or host_st["torn"] != 0:
        errs.append(f"host_err={host_st}")
    return {
        "part": "part1_fix_cpu",
        "cfg": "direct_zero_traffic_poll",
        "rep": rep,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "spec": {"part": "part1_fix_cpu", "cfg": "direct_zero_traffic_poll", "card_cores": 1, "app_work": True},
        "client": None,
        "card": None,
        "host": host_st,
        "pmu": pm,
        "card_pmu": None,
        "steady_window": None,
        "verified": len(errs) == 0,
        "verify_errors": errs,
    }


def run_rpc_spec(stack: StackManager, pmu: HostPmu, spec: dict, rep: int):
    card_mode = spec["card_mode"]
    host_mode = spec["host_mode"]
    card_cores = spec["card_cores"]
    enable_dma = spec.get("enable_dma", False)
    credit_lines = spec.get("credit_lines", 0)
    bar4_shared = spec.get("bar4_shared", False)
    app_work = spec.get("app_work", False)
    threads = spec["threads"]

    stack.ensure_card(card_mode, card_cores, enable_dma)
    stack.ensure_host(host_mode, credit_lines, bar4_shared, app_work)

    seed = 0x1350_2026_1121 + rep * 100003 + (abs(hash(spec["cfg"])) & 0xFFFF)
    warmup_ms = spec.get("warmup_ms", 400)
    duration_ms = spec.get("duration_ms", 1500)

    mcmd = (
        f"{MERC_BIN} --threads {threads} --card-queues {card_cores} "
        f"--mode {spec['drive']} --workload {spec['workload']} --window {spec['window']} "
        f"--warmup-ms {warmup_ms} --duration-ms {duration_ms} --seed {seed}"
    )
    if spec["drive"] in ("poisson", "burst", "cbr"):
        mcmd += f" --rate-krps {spec['rate_krps']:.3f}"
    if spec["drive"] == "burst":
        mcmd += f" --burst {spec.get('burst', 8)}"

    t0_tad = None
    if card_cores == 1 and spec["drive"] != "cbr":
        tcp_cmd("10.10.0.2", 7529, "RESET")
        if host_mode != "none":
            tcp_cmd("127.0.0.1", 7531, "RESET")
        t0_tad = tcp_cmd("10.10.0.2", 7529, "PMU_SNAP")

    proc = subprocess.Popen(
        MERC_SSH + [mcmd],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    got_warmup = False
    t_wait = time.monotonic()
    while time.monotonic() - t_wait < 20.0:
        line = proc.stdout.readline()
        if not line:
            break
        if "PROBE_DONE" in line and card_cores > 1:
            if spec["drive"] != "cbr":
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

    steady_win = None
    if spec["drive"] == "cbr":
        # CBR steady-state CPU measurement strictly inside [0.5s, 2.0s] of 2.1s CBR
        tcp_cmd("10.10.0.2", 7529, "RESET")
        tcp_cmd("127.0.0.1", 7531, "RESET")
        t0_tad = tcp_cmd("10.10.0.2", 7529, "PMU_SNAP")
        p0 = pmu.snap()
        time.sleep(1.5)
        p1 = pmu.snap()
        t1_tad = tcp_cmd("10.10.0.2", 7529, "PMU_SNAP")
        host_st = tcp_cmd("127.0.0.1", 7531, "STATS")
        card_st = tcp_cmd("10.10.0.2", 7529, "STATS")
        pm = pmu.delta(p0, p1)
        cpm = delta_tad(t0_tad, t1_tad)
        out_rest, err_rest = proc.communicate(timeout=20)
    else:
        # Pure steady-state 1.000 s Host PMU + lock-free cpu41 SNAP strictly inside [150ms, 1150ms]
        # ZERO mid-stream TCP traffic to 10.10.0.2 (eliminates sdp1-0 IRQ preemption on CN106!).
        time.sleep(0.15)
        h0 = tcp_cmd("127.0.0.1", 7531, "SNAP") if host_mode != "none" else None
        p0 = pmu.snap()
        time.sleep(1.00)
        p1 = pmu.snap()
        h1 = tcp_cmd("127.0.0.1", 7531, "SNAP") if host_mode != "none" else None
        pm = pmu.delta(p0, p1)
        out_rest, err_rest = proc.communicate(timeout=25)
        t1_tad = tcp_cmd("10.10.0.2", 7529, "PMU_SNAP")
        card_st = tcp_cmd("10.10.0.2", 7529, "STATS")
        host_st = tcp_cmd("127.0.0.1", 7531, "STATS") if host_mode != "none" else None
        cpm = delta_tad(t0_tad, t1_tad)
        if h0 is not None and h1 is not None and host_st is not None:
            h_dt = max(h1["dt_s"] - h0["dt_s"], 1e-9)
            h_lines = h1["bar4_lines_written"] - h0["bar4_lines_written"]
            h_rpcs = h1["rx_rpcs"] - h0["rx_rpcs"]
            h_cwaits = h1["credit_waits"] - h0["credit_waits"]
            line_rate = h_lines / h_dt
            iio_ratio = (pm["iio1_cpu_wr"] / pm["dt_s"]) / line_rate if line_rate > 0 else 0.0
            cha_ratio = (pm["cha_ia_wcilf"] / pm["dt_s"]) / line_rate if line_rate > 0 else 0.0
            tot_lines = max(host_st["bar4_lines_written"], 1)
            tad_snp_per_line = cpm["tad_snp_msh_out_any"] / tot_lines
            steady_win = {
                "host_dt_s": round(h_dt, 6),
                "bar4_lines": h_lines,
                "rx_rpcs": h_rpcs,
                "credit_waits": h_cwaits,
                "bar4_mlines_per_s": round(line_rate / 1e6, 4),
                "iio_cpu_wr_ratio": round(iio_ratio, 4),
                "cha_wcilf_ratio": round(cha_ratio, 4),
                "tad_snp_per_line": round(tad_snp_per_line, 4),
            }

    client_st = None
    for line in out_rest.splitlines():
        if line.startswith("MRV135_CLIENT_JSON "):
            client_st = json.loads(line[len("MRV135_CLIENT_JSON "):])
            break
    if client_st is None:
        raise RuntimeError(f"client crashed ({proc.returncode}) {spec['cfg']}:\n{out_rest}\n{err_rest}")

    # Strict verification gate
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
    if spec["drive"] != "cbr":
        exp_pkts = client_st["tx_pkts_total"]
        if card_st["eth_ipackets"] != exp_pkts or card_st["eth_opackets"] != exp_pkts:
            errs.append(f"eth_pkts={card_st['eth_ipackets']}/{card_st['eth_opackets']} != {exp_pkts}")
        if host_st is not None:
            exp_rpcs = client_st["tx_rpcs_total"]
            if host_st["rx_rpcs"] != exp_rpcs or host_st["tx_rpcs"] != exp_rpcs:
                errs.append(f"host_rpcs={host_st['rx_rpcs']}/{host_st['tx_rpcs']} != {exp_rpcs}")
    if card_cores > 1:
        pc = card_st.get("per_core_rpcs_in", [])
        if len(pc) != card_cores or min(pc) == 0:
            errs.append(f"rss_unbalanced={pc}")
    if host_st is not None:
        if host_st["bad"] != 0 or host_st["torn"] != 0:
            errs.append(f"host_err bad={host_st['bad']} torn={host_st['torn']}")
        if "N0=0," not in host_st.get("tx_pool_numa", ""):
            errs.append(f"tx_pool_numa={host_st.get('tx_pool_numa')}")
    if pm["min_running_pct"] < 99.9:
        errs.append(f"host_pmu_pct={pm['min_running_pct']}")
    if cpm["min_running_pct"] < 99.9:
        errs.append(f"card_pmu_pct={cpm['min_running_pct']}")
    if pm["mesh_ghz"] < 2.15:
        errs.append(f"mesh_ghz={pm['mesh_ghz']}")
    if host_mode != "none" and pm["cpu40_ghz"] < 3.80:
        errs.append(f"cpu40_ghz={pm['cpu40_ghz']}")

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

    def add(part, cfg, card_mode, host_mode, card_cores, threads, workload, drive, window,
            rate_krps=0.0, burst=8, credit_lines=0, bar4_shared=False, app_work=False,
            enable_dma=False, warmup_ms=400, duration_ms=1500):
        specs.append({
            "part": part,
            "cfg": cfg,
            "card_mode": card_mode,
            "host_mode": host_mode,
            "card_cores": card_cores,
            "threads": threads,
            "workload": workload,
            "drive": drive,
            "window": window,
            "rate_krps": rate_krps,
            "burst": burst,
            "credit_lines": credit_lines,
            "bar4_shared": bar4_shared,
            "app_work": app_work,
            "enable_dma": enable_dma,
            "warmup_ms": warmup_ms,
            "duration_ms": duration_ms,
        })

    # Part 1: Post-fix purified 1H+1C DirectPush CPU re-verification (zero SDP!)
    for wl, rate in (("64", 1500.0), ("mix_ads", 1200.0), ("4096", 500.0)):
        add("part1_fix_cpu", f"direct1c_cpu_{wl}_r{int(rate)}", "direct", "direct", 1, 1,
            wl, "cbr", 64, rate_krps=rate, app_work=True, enable_dma=True, warmup_ms=500, duration_ms=2100)

    # Part 2: 4C eth0 RSS 256 B closed-loop slow-cliff mapping & C_line inverted-U sweep
    add("part2_closed_4c", "card_echo_4c_256_w64", "card_echo", "none", 4, 4, "256", "closed", 64)
    for w in (4, 8, 16, 24, 32, 48, 64, 128):
        add("part2_closed_4c", f"sub_cl0_w{w}", "direct", "direct", 4, 4, "256", "closed", w, credit_lines=0)
    for cl in (40, 48):
        for w in (32, 48, 64, 128):
            add("part2_closed_4c", f"sub_cl{cl}_w{w}", "direct", "direct", 4, 4, "256", "closed", w, credit_lines=cl)
    for cl in (16, 24, 32, 64):
        add("part2_closed_4c", f"sub_cl{cl}_w64", "direct", "direct", 4, 4, "256", "closed", 64, credit_lines=cl)

    # Part 3: Core single-variable experiment (256 B, B=8 micro-bursts, W=64, 2.0..6.0 Mreq/s)
    rates = (2000, 3000, 3500, 4000, 4500, 5000, 5500, 6000)
    for r in rates:
        add("part3_burst_4c", f"sub_cl0_b8_r{r}", "direct", "direct", 4, 4, "256", "burst", 64,
            rate_krps=float(r), burst=8, credit_lines=0)
        add("part3_burst_4c", f"sub_cl40_b8_r{r}", "direct", "direct", 4, 4, "256", "burst", 64,
            rate_krps=float(r), burst=8, credit_lines=40)
        add("part3_burst_4c", f"sub_cl48_b8_r{r}", "direct", "direct", 4, 4, "256", "burst", 64,
            rate_krps=float(r), burst=8, credit_lines=48)
    for r in (4000, 5000):
        add("part3_burst_4c", f"sub_cl64_b8_r{r}", "direct", "direct", 4, 4, "256", "burst", 64,
            rate_krps=float(r), burst=8, credit_lines=64)

    # Part 4: Diagnostic intra-RPC 256 B cell-spacing (`bar4_shared`) check (`C_line=0` vs `48`)
    for w in (64, 128):
        add("part4_stride_diag", f"shr_cl0_w{w}", "direct", "direct", 4, 4, "256", "closed", w,
            credit_lines=0, bar4_shared=True)
        add("part4_stride_diag", f"shr_cl48_w{w}", "direct", "direct", 4, 4, "256", "closed", w,
            credit_lines=48, bar4_shared=True)
    for r in (4000, 4500, 5000, 5500, 6000):
        add("part4_stride_diag", f"shr_cl0_b8_r{r}", "direct", "direct", 4, 4, "256", "burst", 64,
            rate_krps=float(r), burst=8, credit_lines=0, bar4_shared=True)
        add("part4_stride_diag", f"shr_cl48_b8_r{r}", "direct", "direct", 4, 4, "256", "burst", 64,
            rate_krps=float(r), burst=8, credit_lines=48, bar4_shared=True)

    return specs


def group_key(spec):
    return (
        spec["card_mode"],
        spec["card_cores"],
        spec.get("enable_dma", False),
        spec["host_mode"],
        spec.get("bar4_shared", False),
        spec.get("credit_lines", 0),
        spec.get("app_work", False),
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

    stack = StackManager()
    pmu = HostPmu(cpu=40)
    total_runs = REPS * (2 + len(specs))
    done = 0
    failed = 0
    retries_total = 0

    try:
        with open(RUNS_PATH, "w") as out:
            for rep in range(REPS):
                # 1. Idle calibration each rep (Fix 2) — ensure cpu40 is completely idle first!
                stack.stop_host()
                cal_rec = run_idle_cal(pmu, rep)
                out.write(json.dumps(cal_rec) + "\n")
                out.flush()
                done += 1
                print(
                    f"[{done}/{total_runs}] rep={rep} {cal_rec['cfg']}: "
                    f"{cal_rec['cal']['app_work_kps']:.2f} k/s ({cal_rec['cal']['ns_per_unit']:.2f} ns) "
                    f"ok={cal_rec['verified']}",
                    flush=True,
                )

                # 2. Zero-traffic empty-poll check each rep (Fix 1)
                zt_rec = run_zero_traffic_cpu(stack, pmu, rep)
                out.write(json.dumps(zt_rec) + "\n")
                out.flush()
                done += 1
                print(
                    f"[{done}/{total_runs}] rep={rep} {zt_rec['cfg']}: "
                    f"{zt_rec['host']['app_work_kps']:.2f} k/s ghz={zt_rec['pmu']['cpu40_ghz']:.3f} "
                    f"ok={zt_rec['verified']}",
                    flush=True,
                )

                # Alternate unshaped vs shaped group order across reps
                def sort_rank(gk):
                    cm, cc, _dma, _hm, bshr, cl, _aw = gk
                    if cm == "card_echo":
                        return (0, gk)
                    if cc == 1:
                        return (1, gk)
                    # Alternate cl=0 first vs cl>0 first across reps
                    order_cl = (0 if cl == 0 else 1) if (rep % 2 == 0) else (1 if cl == 0 else 0)
                    return (2 + (1 if bshr else 0), order_cl, cl, gk)

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
                        h = rec["host"]
                        p = rec["pmu"]
                        sw = rec["steady_window"]
                        extra = ""
                        if sw is not None:
                            extra = (
                                f" iio={sw['iio_cpu_wr_ratio']:.3f}x cha={sw['cha_wcilf_ratio']:.3f}x"
                                f" cha_q={p['cha_inflight']:.2f}"
                            )
                        elif h is not None and spec.get("app_work"):
                            extra = f" work={h['app_work_kps']:.1f}k/s ddio={p['ddio_hit_pct']:.1f}%"
                        print(
                            f"[{done}/{total_runs}] rep={rep} {spec['cfg']:24s} "
                            f"rate={c['achieved_mrps']:.3f}M/s ({c['achieved_mrps']*4 if spec['workload']=='256' else c['achieved_mrps']:.2f}Ml/s) "
                            f"p50={c['tot_p50_us']:.2f} p99={c['tot_p99_us']:.2f}us "
                            f"hrtt={rec['card']['host_rtt_p50_us']:.2f}/{rec['card']['host_rtt_p99_us']:.2f}us"
                            f"{extra} ok={rec['verified']}",
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
