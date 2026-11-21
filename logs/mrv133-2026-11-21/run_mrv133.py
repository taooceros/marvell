#!/usr/bin/env python3
"""MRV-133 full interleaved 3-machine 100GbE experiment harness.

Real 2-machine 100GbE end-to-end RPC/KV evaluation for:
  "CC-NIC does not need Cache Coherence" (OSDI §5.5 camera-ready upgrade of MRV-118)

Hardware topology (all frequencies pinned, ZERO Intel DSA):
  - Client  : mercury (Xeon 6438M, NUMA 0 cpu0, ConnectX-5 MCX516A-CCAT mlx5_2 /
              ens6f1np1 10.10.100.1, non-root IBV_QPT_UD RoCEv2 hardware bypass)
  - Wire    : 3m Amphenol NDAAFF-C103 100GbE passive DAC (100000Mb/s Full, No FEC)
  - SmartNIC: CN106 (Neoverse N2 core 3 @ 2.00 GHz, physical eth0 0002:02:00.0
              RPM0 100G MTI MAC/PCS/SerDes + NIX0 + NPC + NPA; 2 DPI VFs 06:00.1/.2)
  - Server  : saturn (Xeon 6438M, NUMA 1 cpu40 @ 3.90 GHz performance, Socket-1
              uncore 2.20 GHz, intel_iommu=on,sm_on, 128 MiB 2 MiB-hugepage
              retained TX/KV pool)

Single variable switched across runs (MRV-82):
  - card_echo  : pure CN106 eth0 hardware reflector (measures CX5+wire+RPM0+NIX0 floor)
  - sdp        : traditional SDP descriptor ring (MTU 9000, default rx_poll_mem=0)
  - sdp_rpm    : SDP strong control #1 (rx_poll_mem=1)
  - sdp_rpm_zc : SDP strong control #2 (rx_poll_mem=1 + zero-copy in-place reply)
  - direct     : pure-NIC DirectPush (<=256 B PEM0 WC + MOVDIR64B) + Native DPI DMA (>=1 KiB)
                 (+ optional 256 B burst-cap / threshold controls)
"""

import ctypes
import fcntl
import json
import os
import pathlib
import re
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone

OUT_DIR = pathlib.Path("/home/hongtao/dev/marvell-mrv133/logs/mrv133-2026-11-21")
RUNS_PATH = OUT_DIR / "runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"

HOST_BIN = "/home/hongtao/dev/marvell-mrv133/testbed/target/release/mrv133_host"
CARD_BIN = "/root/bench/mrv133_card"
MERC_BIN = "/tmp/mrv133/mrv133_client"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]
MERC_SSH = ["ssh", "-o", "BatchMode=yes", "mercury"]

REPS = 3


# ---------------------------------------------------------------------------
# 100% Non-Multiplexed Direct Sapphire Rapids Core + Socket-1 Uncore PMUs
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
        ("__reserved_2", ctypes.c_uint16),
        ("aux_sample_size", ctypes.c_uint32),
        ("__reserved_3", ctypes.c_uint32),
        ("sig_data", ctypes.c_uint64),
        ("config3", ctypes.c_uint64),
    ]


LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
SYS_PERF_EVENT_OPEN = 298  # x86_64
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
        attr = PerfEventAttr()
        attr.type = ptype
        attr.size = ctypes.sizeof(PerfEventAttr)
        attr.config = config
        attr.read_format = PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING
        attr.flags = 1  # disabled initially
        fd = LIBC.syscall(
            SYS_PERF_EVENT_OPEN,
            ctypes.byref(attr),
            ctypes.c_int(-1),
            ctypes.c_int(cpu),
            ctypes.c_int(-1),
            ctypes.c_ulong(0),
        )
        if fd < 0:
            err = ctypes.get_errno()
            raise OSError(err, f"perf_event_open(type={ptype}, cfg={config:#x}, cpu={cpu}): {os.strerror(err)}")
        self.fd = int(fd)
        fcntl.ioctl(self.fd, PERF_EVENT_IOC_ENABLE, 0)

    def read(self):
        raw = os.read(self.fd, 24)
        return struct.unpack("QQQ", raw)  # (val, enabled_ns, running_ns)

    def close(self):
        try:
            fcntl.ioctl(self.fd, PERF_EVENT_IOC_DISABLE, 0)
        finally:
            os.close(self.fd)


class HostPmu:
    def __init__(self, cpu: int = 40):
        self.cpu = cpu
        self.fds = {}
        self.fds["cycles"] = PerfFd(0, 0, cpu)
        self.fds["instr"] = PerfFd(0, 1, cpu)
        self.fds["ref_cycles"] = PerfFd(0, 9, cpu)
        self.fds["llc_loads"] = PerfFd(3, 0x00002, cpu)
        self.fds["llc_load_misses"] = PerfFd(3, 0x10002, cpu)
        self.fds["mesh_clk"] = PerfFd(_pmu_type("uncore_m2pcie_1"), 0x01, SOCKET1_UNCORE_CPU)
        iio1_t = _pmu_type("uncore_iio_1")
        self.fds["iio1_cpu_wr"] = PerfFd(iio1_t, 0x0007_0FF0_0000_01C1, SOCKET1_UNCORE_CPU)
        self.fds["iio1_inb_wr"] = PerfFd(iio1_t, 0x0007_0FF0_0000_0184, SOCKET1_UNCORE_CPU)
        self.fds["iio1_inb_rd"] = PerfFd(iio1_t, 0x0007_0FF0_0000_0484, SOCKET1_UNCORE_CPU)
        self.cha_wcilf = []
        self.cha_io_hit = []
        self.cha_io_miss = []
        for i in range(32):
            t = _pmu_type(f"uncore_cha_{i}")
            self.cha_wcilf.append(PerfFd(t, 0x00C8_67FF_0000_0135, SOCKET1_UNCORE_CPU))
            self.cha_io_hit.append(PerfFd(t, 0x00CC_43FD_0000_0435, SOCKET1_UNCORE_CPU))
            self.cha_io_miss.append(PerfFd(t, 0x00CC_43FE_0000_0435, SOCKET1_UNCORE_CPU))

    def snap(self):
        return {
            "t": time.monotonic(),
            "s": {k: fd.read() for k, fd in self.fds.items()},
            "wcilf": [fd.read() for fd in self.cha_wcilf],
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
        wcilf = sum(d1(x, y) for x, y in zip(a["wcilf"], b["wcilf"]))
        io_hit = sum(d1(x, y) for x, y in zip(a["io_hit"], b["io_hit"]))
        io_miss = sum(d1(x, y) for x, y in zip(a["io_miss"], b["io_miss"]))
        itom = io_hit + io_miss
        ddio_pct = (io_hit * 100.0 / itom) if itom > 0 else 100.0
        cpu_ghz = (s["cycles"] / s["ref_cycles"]) * (REF_HZ / 1e9) if s["ref_cycles"] > 0 else 0.0
        mesh_ghz = (s["mesh_clk"] / dt) / 1e9
        return {
            "dt_s": round(dt, 6),
            "min_running_pct": round(min_pct, 3),
            "cpu40_ghz": round(cpu_ghz, 4),
            "mesh_ghz": round(mesh_ghz, 4),
            "cycles": s["cycles"],
            "instr": s["instr"],
            "llc_loads": s["llc_loads"],
            "llc_load_misses": s["llc_load_misses"],
            "iio1_cpu_wr": s["iio1_cpu_wr"],
            "iio1_inb_wr": s["iio1_inb_wr"],
            "iio1_inb_rd": s["iio1_inb_rd"],
            "cha_ia_wcilf": wcilf,
            "cha_io_hit_itom": io_hit,
            "cha_io_miss_itom": io_miss,
            "ddio_hit_pct": round(ddio_pct, 4),
        }

    def close(self):
        for fd in self.fds.values():
            fd.close()
        for lst in (self.cha_wcilf, self.cha_io_hit, self.cha_io_miss):
            for fd in lst:
                fd.close()


# ---------------------------------------------------------------------------
# Subprocess & TCP helpers
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
            sh(["rm", "-f", "/dev/hugepages/m133hmap_*"], check=False)

    def stop_card(self):
        self.stop_host()
        ssh_card(
            "tmux send-keys -t m133 C-c 2>/dev/null || true; sleep 0.6; "
            "tmux kill-session -t m133 2>/dev/null || true; sleep 0.2; rm -f /dev/hugepages/m133*"
        )
        self.card_key = None

    def ensure_card(self, card_mode: str, dma_threshold: int = 1024):
        key = (card_mode, dma_threshold)
        if self.card_key == key:
            return
        self.stop_card()
        ssh_card(
            "ip link set eth0 down 2>/dev/null || true; "
            "drv=$(basename $(readlink -f /sys/bus/pci/devices/0002:02:00.0/driver) 2>/dev/null || echo none); "
            'if [ "$drv" != "vfio-pci" ]; then dpdk-devbind.py -b vfio-pci 0002:02:00.0; fi; '
            "drv2=$(basename $(readlink -f /sys/bus/pci/devices/0002:01:00.2/driver) 2>/dev/null || echo none); "
            'if [ "$drv2" != "vfio-pci" ]; then sh /root/bench/dpdk_up.sh 0002:01:00.2; fi'
        )
        if card_mode == "card_echo":
            ready_tok = "card_echo ready"
            cmd = f"{CARD_BIN} -l 3 -a 0002:02:00.0 --file-prefix=m133 -- --mode card-echo"
            prep = ""
        elif card_mode == "sdp":
            ready_tok = "sdp ready"
            cmd = f"{CARD_BIN} -l 3 -a 0002:02:00.0 -a 0002:01:00.2 --file-prefix=m133 -- --mode sdp"
            prep = ""
        elif card_mode == "direct":
            ready_tok = "direct ready"
            cmd = (
                f"{CARD_BIN} -l 3 -a 0002:02:00.0 -a 0000:06:00.1 -a 0000:06:00.2 "
                f"--file-prefix=m133 -- --mode direct --dma-threshold {dma_threshold}"
            )
            prep = "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "
        else:
            raise ValueError(card_mode)

        ssh_card(
            f"{prep}"
            f'tmux new-session -d -s m133 "{cmd} > /tmp/m133_card.log 2>&1"; '
            f'for i in $(seq 1 50); do grep -q "MRV133_CARD {ready_tok}" /tmp/m133_card.log && break; sleep 0.2; done; '
            f'grep "MRV133_CARD {ready_tok}" /tmp/m133_card.log'
        )
        self.card_key = key

    def ensure_host(self, host_mode: str, rx_poll_mem: bool, zero_copy: bool,
                    app_work: bool, dma_threshold: int = 1024, burst_cap_lines: int = 0):
        if host_mode == "none":
            self.stop_host()
            return
        key = (host_mode, rx_poll_mem, zero_copy, app_work, dma_threshold, burst_cap_lines)
        if self.host_key == key and self.host_proc is not None and self.host_proc.poll() is None:
            return
        self.stop_host()
        dev_arg = "0000:99:02.0,rx_poll_mem=1" if rx_poll_mem else "0000:99:02.0"
        cmd = [
            HOST_BIN, "-l", "40", "-a", dev_arg,
            "--socket-mem=0,1024", "--iova-mode=va", "--file-prefix=m133h", "--",
            "--mode", host_mode,
            "--dma-threshold", str(dma_threshold),
            "--burst-cap-lines", str(burst_cap_lines),
        ]
        if rx_poll_mem:
            cmd.append("--rx-poll-mem")
        if zero_copy:
            cmd.append("--zero-copy")
        if app_work:
            cmd.append("--app-work")
        logf = open("/tmp/m133_host_run.log", "w")
        self.host_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        logf.close()
        for _ in range(60):
            if self.host_proc.poll() is not None:
                err = pathlib.Path("/tmp/m133_host_run.log").read_text()
                raise RuntimeError(f"mrv133_host exited early:\n{err}")
            txt = pathlib.Path("/tmp/m133_host_run.log").read_text()
            if "MRV133_HOST READY" in txt:
                self.host_key = key
                return
            time.sleep(0.1)
        raise RuntimeError(f"mrv133_host timeout:\n{pathlib.Path('/tmp/m133_host_run.log').read_text()}")


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
    ok = pm["min_running_pct"] >= 99.9 and pm["cpu40_ghz"] >= 3.85
    return {
        "part": "part0_cal",
        "cfg": "app_work_idle_cal",
        "rep": rep,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "cal": cal,
        "pmu": pm,
        "verified": ok,
        "verify_errors": [] if ok else [f"pmu={pm}"],
    }


def run_rpc_spec(stack: StackManager, pmu: HostPmu, spec: dict, rep: int):
    card_mode = spec["card_mode"]
    host_mode = spec["host_mode"]
    dma_thr = spec.get("dma_threshold", 1024)
    burst_cap = spec.get("burst_cap_lines", 0)
    rx_poll_mem = spec.get("rx_poll_mem", False)
    zero_copy = spec.get("zero_copy", False)
    app_work = spec.get("app_work", False)

    stack.ensure_card(card_mode, dma_threshold=dma_thr)
    stack.ensure_host(
        host_mode,
        rx_poll_mem=rx_poll_mem,
        zero_copy=zero_copy,
        app_work=app_work,
        dma_threshold=dma_thr,
        burst_cap_lines=burst_cap,
    )

    seed = 0x1330_2026_1121 + rep * 100003 + (abs(hash(spec["cfg"])) & 0xFFFF)

    if spec["drive"] == "cbr":
        # Part 4 steady-state CBR measurement: sync RESET + PMU strictly inside [0.5s, 2.0s] of 2.6s CBR
        mcmd = (
            f"{MERC_BIN} --mode cbr --workload {spec['workload']} --window {spec['window']} "
            f"--rate-krps {spec['rate_krps']:.3f} --warmup-ms 500 --duration-ms 2100 --seed {seed}"
        )
        proc = subprocess.Popen(MERC_SSH + [mcmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # Wait for WARMUP_DONE
        got_wu = False
        t_wait = time.monotonic()
        while time.monotonic() - t_wait < 10.0:
            line = proc.stdout.readline()
            if not line:
                break
            if "WARMUP_DONE" in line:
                got_wu = True
                break
        if not got_wu:
            proc.kill()
            raise RuntimeError(f"CBR client missed WARMUP_DONE for {spec['cfg']}")

        tcp_cmd("10.10.0.2", 7529, "RESET")
        tcp_cmd("127.0.0.1", 7531, "RESET")
        s0 = pmu.snap()
        time.sleep(1.5)
        s1 = pmu.snap()
        host_st = tcp_cmd("127.0.0.1", 7531, "STATS")
        card_st = tcp_cmd("10.10.0.2", 7529, "STATS")
        pm = pmu.delta(s0, s1)
        out_rest, err_rest = proc.communicate(timeout=15)
        client_st = None
        for line in out_rest.splitlines():
            if line.startswith("MRV133_CLIENT_JSON "):
                client_st = json.loads(line[len("MRV133_CLIENT_JSON "):])
                break
        if client_st is None:
            raise RuntimeError(f"CBR client crashed ({proc.returncode}) {spec['cfg']}:\n{out_rest}\n{err_rest}")
    else:
        # Part 1 / 2 / 3 exact packet-count verification
        tcp_cmd("10.10.0.2", 7529, "RESET")
        if host_mode != "none":
            tcp_cmd("127.0.0.1", 7531, "RESET")
        mcmd = (
            f"{MERC_BIN} --mode {spec['drive']} --workload {spec['workload']} --window {spec['window']} "
            f"--warmup {spec['warmup']} --rounds {spec['rounds']} --seed {seed}"
        )
        if spec["drive"] in ("poisson", "burst"):
            mcmd += f" --rate-krps {spec['rate_krps']:.3f}"
        if spec["drive"] == "burst":
            mcmd += f" --burst {spec.get('burst', 8)}"

        s0 = pmu.snap()
        res = ssh_merc(mcmd, timeout=90)
        s1 = pmu.snap()
        pm = pmu.delta(s0, s1)
        card_st = tcp_cmd("10.10.0.2", 7529, "STATS")
        host_st = tcp_cmd("127.0.0.1", 7531, "STATS") if host_mode != "none" else None

        client_st = None
        for line in res.stdout.splitlines():
            if line.startswith("MRV133_CLIENT_JSON "):
                client_st = json.loads(line[len("MRV133_CLIENT_JSON "):])
                break
        assert client_st is not None, res.stdout

    # Strict verification gate
    errs = []
    if not client_st.get("verified", False):
        errs.append(f"client_unverified lost={client_st['lost']} bad={client_st['bad']} torn={client_st['torn']}")
    for k in ("bad_wire", "bad_host", "torn_bar4", "dma_failed", "eth_tx_drops", "sdp_tx_drops",
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
    if host_st is not None:
        if host_st["bad"] != 0 or host_st["torn"] != 0 or host_st["tx_drops"] != 0:
            errs.append(f"host_err bad={host_st['bad']} torn={host_st['torn']} drops={host_st['tx_drops']}")
        if "N0=0," not in host_st.get("tx_pool_numa", ""):
            errs.append(f"tx_pool_numa={host_st.get('tx_pool_numa')}")
    if pm["min_running_pct"] < 99.9:
        errs.append(f"pmu_pct={pm['min_running_pct']}")
    if pm["mesh_ghz"] < 2.15:
        errs.append(f"mesh_ghz={pm['mesh_ghz']}")
    if host_mode != "none" and pm["cpu40_ghz"] < 3.70:
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
        "verified": len(errs) == 0,
        "verify_errors": errs,
    }


def build_specs():
    specs = []

    def add(part, cfg, card_mode, host_mode, workload, drive, window,
            warmup=5000, rounds=20000, rate_krps=0.0, burst=8,
            rx_poll_mem=False, zero_copy=False, app_work=False,
            dma_threshold=1024, burst_cap_lines=0):
        specs.append({
            "part": part,
            "cfg": cfg,
            "card_mode": card_mode,
            "host_mode": host_mode,
            "workload": workload,
            "drive": drive,
            "window": window,
            "warmup": warmup,
            "rounds": rounds,
            "rate_krps": rate_krps,
            "burst": burst,
            "rx_poll_mem": rx_poll_mem,
            "zero_copy": zero_copy,
            "app_work": app_work,
            "dma_threshold": dma_threshold,
            "burst_cap_lines": burst_cap_lines,
        })

    # Part 1: Unloaded W=1 real 2-machine latency breakdown (64, 256, 1024, 4096 B + Mix-Ads/Geo)
    for wl in ("64", "256", "1024", "4096"):
        add("part1_decomp", f"card_echo_{wl}_w1", "card_echo", "none", wl, "closed", 1, warmup=5000, rounds=20000)
        add("part1_decomp", f"sdp_{wl}_w1", "sdp", "sdp", wl, "closed", 1, warmup=5000, rounds=20000)
        add("part1_decomp", f"sdp_rpm_{wl}_w1", "sdp", "sdp", wl, "closed", 1, warmup=5000, rounds=20000, rx_poll_mem=True)
        add("part1_decomp", f"direct_{wl}_w1", "direct", "direct", wl, "closed", 1, warmup=5000, rounds=20000)
    for wl in ("mix_ads", "mix_geo"):
        add("part1_decomp", f"sdp_{wl}_w1", "sdp", "sdp", wl, "closed", 1, warmup=3000, rounds=15000)
        add("part1_decomp", f"direct_{wl}_w1", "direct", "direct", wl, "closed", 1, warmup=3000, rounds=15000)

    # Part 2: Closed-loop loaded throughput & latency over real 100GbE
    add("part2_closed", "card_echo_64_w64", "card_echo", "none", "64", "closed", 64, warmup=20000, rounds=200000)
    for w in (8, 16, 32, 64):
        add("part2_closed", f"sdp_64_w{w}", "sdp", "sdp", "64", "closed", w, warmup=15000, rounds=150000)
        add("part2_closed", f"direct_64_w{w}", "direct", "direct", "64", "closed", w, warmup=15000, rounds=150000)
    add("part2_closed", "sdp_rpm_64_w64", "sdp", "sdp", "64", "closed", 64, warmup=15000, rounds=150000, rx_poll_mem=True)

    # 256 B loaded (4 lines/RPC) + shaping/threshold controls (explicitly addressing MRV-128)
    for w in (16, 32, 64):
        add("part2_closed", f"sdp_256_w{w}", "sdp", "sdp", "256", "closed", w, warmup=10000, rounds=100000)
        add("part2_closed", f"direct_256_w{w}", "direct", "direct", "256", "closed", w, warmup=10000, rounds=100000)
    add("part2_closed", "direct_256_bcap8_w64", "direct", "direct", "256", "closed", 64, warmup=10000, rounds=100000, burst_cap_lines=8)
    add("part2_closed", "direct_256_dma256_w64", "direct", "direct", "256", "closed", 64, warmup=10000, rounds=100000, dma_threshold=256)

    # 1024 B (1-frame bulk) & 4096 B (4x1KiB wire -> 4KiB host)
    for wl, rds in (("1024", 80000), ("4096", 40000)):
        for w in (8, 16, 32):
            add("part2_closed", f"sdp_{wl}_w{w}", "sdp", "sdp", wl, "closed", w, warmup=5000, rounds=rds)
            add("part2_closed", f"direct_{wl}_w{w}", "direct", "direct", wl, "closed", w, warmup=5000, rounds=rds)
        add("part2_closed", f"sdp_rpm_zc_{wl}_w32", "sdp", "sdp", wl, "closed", 32, warmup=5000, rounds=rds,
            rx_poll_mem=True, zero_copy=True)

    # Mix-Ads (90/10) & Mix-Geo (50/50) closed-loop
    for wl, rds in (("mix_ads", 80000), ("mix_geo", 50000)):
        for w in (8, 16, 32):
            add("part2_closed", f"sdp_{wl}_w{w}", "sdp", "sdp", wl, "closed", w, warmup=5000, rounds=rds)
            add("part2_closed", f"direct_{wl}_w{w}", "direct", "direct", wl, "closed", w, warmup=5000, rounds=rds)

    # Part 3: Realistic remote mercury open-loop Poisson & micro-burst (B=8, W=64)
    for r in (500, 1000, 1500, 2000, 2500, 2750):
        add("part3_openloop", f"sdp_64_pois_r{r}", "sdp", "sdp", "64", "poisson", 64, warmup=10000, rounds=100000, rate_krps=float(r))
    for r in (500, 1000, 1500, 2000, 2500, 3000, 3500):
        add("part3_openloop", f"direct_64_pois_r{r}", "direct", "direct", "64", "poisson", 64, warmup=10000, rounds=100000, rate_krps=float(r))
    for r in (1000, 2000):
        add("part3_openloop", f"sdp_64_b8_r{r}", "sdp", "sdp", "64", "burst", 64, warmup=10000, rounds=100000, rate_krps=float(r), burst=8)
    for r in (1000, 2000, 3000):
        add("part3_openloop", f"direct_64_b8_r{r}", "direct", "direct", "64", "burst", 64, warmup=10000, rounds=100000, rate_krps=float(r), burst=8)

    for r in (500, 1000, 1500):
        add("part3_openloop", f"sdp_mix_ads_pois_r{r}", "sdp", "sdp", "mix_ads", "poisson", 64, warmup=5000, rounds=60000, rate_krps=float(r))
    for r in (500, 1000, 1500, 2000):
        add("part3_openloop", f"direct_mix_ads_pois_r{r}", "direct", "direct", "mix_ads", "poisson", 64, warmup=5000, rounds=60000, rate_krps=float(r))
    for r in (1000, 1500):
        add("part3_openloop", f"sdp_mix_ads_b8_r{r}", "sdp", "sdp", "mix_ads", "burst", 64, warmup=5000, rounds=60000, rate_krps=float(r), burst=8)
        add("part3_openloop", f"direct_mix_ads_b8_r{r}", "direct", "direct", "mix_ads", "burst", 64, warmup=5000, rounds=60000, rate_krps=float(r), burst=8)

    for r in (300, 600, 900):
        add("part3_openloop", f"sdp_mix_geo_pois_r{r}", "sdp", "sdp", "mix_geo", "poisson", 64, warmup=4000, rounds=40000, rate_krps=float(r))
    for r in (300, 600, 900, 1200):
        add("part3_openloop", f"direct_mix_geo_pois_r{r}", "direct", "direct", "mix_geo", "poisson", 64, warmup=4000, rounds=40000, rate_krps=float(r))

    # Part 4: Matched-throughput host CPU headroom (--app-work + 100% PMU)
    # EVERY bulk/mixed strong control turns on BOTH rx_poll_mem=1 AND zero-copy!
    cpu_cases = [
        ("64", 1500.0, False),
        ("1024", 800.0, True),
        ("4096", 500.0, True),
        ("mix_ads", 1200.0, True),
        ("mix_geo", 600.0, True),
    ]
    for wl, rate, has_zc in cpu_cases:
        rk = int(rate)
        add("part4_cpu", f"sdp_cpu_{wl}_r{rk}", "sdp", "sdp", wl, "cbr", 64, rate_krps=rate, app_work=True)
        add("part4_cpu", f"sdp_rpm_cpu_{wl}_r{rk}", "sdp", "sdp", wl, "cbr", 64, rate_krps=rate,
            rx_poll_mem=True, zero_copy=has_zc, app_work=True)
        add("part4_cpu", f"direct_cpu_{wl}_r{rk}", "direct", "direct", wl, "cbr", 64, rate_krps=rate, app_work=True)

    return specs


def group_key(spec):
    return (
        spec["card_mode"],
        spec.get("dma_threshold", 1024),
        spec["host_mode"],
        spec.get("rx_poll_mem", False),
        spec.get("zero_copy", False),
        spec.get("app_work", False),
        spec.get("burst_cap_lines", 0),
    )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    env_before = check_env_all()
    print(f"ENV BEFORE: {json.dumps(env_before)}", flush=True)

    specs = build_specs()
    # Group specs by (card_mode, host_mode, ...) inside each rep to avoid unnecessary EAL restarts,
    # while alternating SDP vs DirectPush group order across reps (rep 0: echo->sdp->direct, rep 1: direct->sdp->echo, rep 2: sdp->direct->echo)
    groups = {}
    for s in specs:
        groups.setdefault(group_key(s), []).append(s)
    group_keys = list(groups.keys())

    stack = StackManager()
    pmu = HostPmu(cpu=40)
    total_runs = REPS * (1 + len(specs))
    done = 0
    failed = 0

    try:
        with open(RUNS_PATH, "w") as out:
            for rep in range(REPS):
                # 1. Idle calibration each rep
                cal_rec = run_idle_cal(pmu, rep)
                out.write(json.dumps(cal_rec) + "\n")
                out.flush()
                done += 1
                print(
                    f"[{done}/{total_runs}] rep={rep} {cal_rec['cfg']}: "
                    f"{cal_rec['cal']['app_work_kps']:.2f} k/s ({cal_rec['cal']['ns_per_unit']:.1f} ns) "
                    f"cpu={cal_rec['pmu']['cpu40_ghz']:.3f}GHz ok={cal_rec['verified']}",
                    flush=True,
                )

                # Order groups alternating sdp vs direct across replicates
                def sort_rank(gk):
                    cm = gk[0]
                    if rep % 3 == 0:
                        base = {"card_echo": 0, "sdp": 1, "direct": 2}[cm]
                    elif rep % 3 == 1:
                        base = {"direct": 0, "sdp": 1, "card_echo": 2}[cm]
                    else:
                        base = {"sdp": 0, "direct": 1, "card_echo": 2}[cm]
                    return (base, gk)

                for gk in sorted(group_keys, key=sort_rank):
                    for spec in groups[gk]:
                        rec = run_rpc_spec(stack, pmu, spec, rep)
                        # Single clean retry if transient hiccup
                        if not rec["verified"]:
                            print(f"  RETRY rep={rep} {spec['cfg']} errs={rec['verify_errors']}", flush=True)
                            rec = run_rpc_spec(stack, pmu, spec, rep)
                        out.write(json.dumps(rec) + "\n")
                        out.flush()
                        done += 1
                        if not rec["verified"]:
                            failed += 1
                        c = rec["client"]
                        h = rec["host"]
                        p = rec["pmu"]
                        extra = ""
                        if h is not None and spec.get("app_work"):
                            extra = f" work={h['app_work_kps']:.1f}k/s ddio={p['ddio_hit_pct']:.1f}%"
                        print(
                            f"[{done}/{total_runs}] rep={rep} {spec['cfg']:28s} "
                            f"rate={c['achieved_mrps']:.3f}M/s p50={c['tot_p50_us']:.2f} "
                            f"p99={c['tot_p99_us']:.2f}us card_hrtt={rec['card']['host_rtt_p50_us']:.2f}us"
                            f"{extra} ok={rec['verified']}",
                            flush=True,
                        )
    finally:
        stack.stop_card()
        pmu.close()

    env_after = check_env_all()
    ENV_PATH.write_text(json.dumps({"before": env_before, "after": env_after, "total_runs": done, "failed": failed}, indent=2))
    print(f"DONE total={done} failed={failed}", flush=True)
    print(f"ENV AFTER: {json.dumps(env_after)}", flush=True)
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
