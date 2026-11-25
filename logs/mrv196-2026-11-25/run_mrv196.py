#!/usr/bin/env python3
"""MRV-196 n=3 interleaved hardware experiment harness:
Measures whether high-bandwidth host BAR4 MMIO writes (>= 150 Mpps, 1..8 cores)
degrade co-located Socket 1 memory latency (16 MiB LLC-fit pointer chase,
256 MiB DRAM pointer chase) or memory bandwidth (STREAM Copy / Triad 1c & 6c),
compared against throughput-matched host-RAM cacheable ring writes, NT-store
writes, and real CN106 DPI FromHost pulls, with 100% non-multiplexed host
CHA TOR / IMC / M2PCIe1 / IRP1 / IIO1 / Core and card TAD / DDR5 PMU accounting.

Zero silent retries: every single attempt is logged to `attempts.jsonl`,
accepted runs to `runs.jsonl`. Automatically restores BAR4 slot 15 defaults
and `refl` tmux in `finally`, then runs a 21 s post-restore host/card EDAC &
AER audit into `env.json`.
"""

import hashlib
import json
import pathlib
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv196")
LOG_DIR = ROOT / "logs/mrv196-2026-11-25"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = "/tmp/mrv196-target/release/mrv196_host"
CARD_BIN = "/root/bench/mrv196_card"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

REPS = 3
MAX_ATTEMPTS = 4
DURATION_MS = 1000
WARMUP_MS = 400
MAX_WARMUP_MS = 3500

RUN_TAG_CURSOR = 10_000


def next_run_tag() -> int:
    global RUN_TAG_CURSOR
    RUN_TAG_CURSOR += 1
    return RUN_TAG_CURSOR


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_aer(bdf: str) -> dict:
    out = {}
    for k, fn in [
        ("cor", "aer_dev_correctable"),
        ("nonfatal", "aer_dev_nonfatal"),
        ("fatal", "aer_dev_fatal"),
    ]:
        p = pathlib.Path(f"/sys/bus/pci/devices/{bdf}/{fn}")
        tot = 0
        if p.exists():
            for line in p.read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].isdigit() and not parts[0].startswith("TOTAL_"):
                    tot += int(parts[1])
        out[k] = tot
    for k, fn in [
        ("rp_total_cor", "aer_rootport_total_err_cor"),
        ("rp_total_nonfatal", "aer_rootport_total_err_nonfatal"),
        ("rp_total_fatal", "aer_rootport_total_err_fatal"),
    ]:
        p = pathlib.Path(f"/sys/bus/pci/devices/{bdf}/{fn}")
        if p.exists():
            out[k] = int(p.read_text().strip())
    return out


def read_dimm_map() -> list[dict]:
    dimms = []
    for mc in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*")):
        for dimm in sorted(mc.glob("dimm*")):
            lbl_p = dimm / "dimm_label"
            sz_p = dimm / "size"
            if lbl_p.exists() and sz_p.exists():
                dimms.append(
                    {
                        "mc": mc.name,
                        "dimm": dimm.name,
                        "label": lbl_p.read_text().strip(),
                        "size_mib": int(sz_p.read_text().strip()),
                    }
                )
    return dimms


def read_host_env() -> dict:
    uncore = {}
    for pkg in ("package_00_die_00", "package_01_die_01"):
        base = pathlib.Path(f"/sys/devices/system/cpu/intel_uncore_frequency/{pkg}")
        uncore[pkg] = {
            k: int((base / f"{k}_freq_khz").read_text().strip())
            for k in ("min", "max", "current")
        }
    edac_ce, edac_ue = {}, {}
    for mc in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*")):
        edac_ce[mc.name] = int((mc / "ce_count").read_text().strip())
        edac_ue[mc.name] = int((mc / "ue_count").read_text().strip())
    # Honest DMAR / dmesg check (non-root user cannot read dmesg if dmesg_restrict=1)
    dmesg_p = subprocess.run(["dmesg"], capture_output=True, text=True)
    if dmesg_p.returncode == 0:
        dmar_hits = [
            ln
            for ln in dmesg_p.stdout.splitlines()
            if any(tok in ln for tok in ("DMAR:", "DRHD", "IOMMU fault", "fault reason"))
        ]
        dmar_audit = {"readable": True, "dmar_lines": len(dmar_hits), "tail": dmar_hits[-5:]}
    else:
        dmar_audit = {
            "readable": False,
            "reason": dmesg_p.stderr.strip(),
            "note": "host dmesg restricted for non-root; IOMMU/PCIe health verified via 0000:98:01.0 & 0000:99:00.0 AER, host/card EDAC ue=0, and card DPI failed=0",
        }
    return {
        "cpu40_gov": pathlib.Path(
            "/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor"
        ).read_text().strip(),
        "cpu40_cur_khz": int(
            pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq")
            .read_text()
            .strip()
        ),
        "cpu40_max_khz": int(
            pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_max_freq")
            .read_text()
            .strip()
        ),
        "uncore_khz": uncore,
        "perf_event_paranoid": int(
            pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip()
        ),
        "numa_balancing": int(
            pathlib.Path("/proc/sys/kernel/numa_balancing").read_text().strip()
        ),
        "dimm_map": read_dimm_map(),
        "rp_98_01_aer": read_aer("0000:98:01.0"),
        "cn106_aer": read_aer("0000:99:00.0"),
        "host_edac_ce": edac_ce,
        "host_edac_ue": edac_ue,
        "host_dmar_audit": dmar_audit,
        "host_bin_sha256": sha256_file(HOST_BIN),
    }


def read_card_env() -> dict:
    cmd = (
        "cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 2000000; "
        "lsmod | grep -c '^pem_wc ' || true; "
        "cat /sys/devices/system/edac/mc/mc0/ce_count; "
        "cat /sys/devices/system/edac/mc/mc0/ue_count; "
        "dmesg | grep -ciE 'SEI-UER|SError' || true; "
        "dmesg | grep -ciE 'smmu|iommu.*fault' || true; "
        "basename $(readlink -f /sys/class/net/eth0/device/driver 2>/dev/null) 2>/dev/null || echo unknown; "
        "ethtool eth0 2>/dev/null | awk '/Speed:/{s=$2} /Link detected:/{l=$3} END{print s \"_\" l}'; "
        "tmux has-session -t refl 2>/dev/null && echo running || echo stopped; "
        f"sha256sum {CARD_BIN} | awk '{{print $1}}'"
    )
    out = subprocess.check_output(CARD_SSH + [cmd], text=True).strip().splitlines()
    return {
        "card_cpu3_khz": int(out[0]),
        "pem_wc_loaded": int(out[1]),
        "mc0_ce": int(out[2]),
        "mc0_ue": int(out[3]),
        "sei_uer": int(out[4]),
        "smmu_fault_lines": int(out[5]),
        "eth0_driver": out[6],
        "eth0_link": out[7],
        "refl_tmux": out[8],
        "card_bin_sha256": out[9],
    }


def stop_refl():
    subprocess.run(
        CARD_SSH
        + ["tmux kill-session -t refl 2>/dev/null || true; pkill -INT -f '[/]root/bench/reflector-rs' || true"],
        check=False,
    )
    for _ in range(30):
        time.sleep(0.15)
        p = subprocess.run(
            CARD_SSH + ["pgrep -f '[/]root/bench/reflector-rs' || true"],
            capture_output=True,
            text=True,
        )
        if not p.stdout.strip():
            return
    raise RuntimeError("reflector-rs did not stop cleanly")


def start_refl() -> str:
    stop_refl()
    subprocess.check_call(
        CARD_SSH
        + [
            "tmux new-session -d -s refl /root/bench/reflector-rs -l 3 -a 0002:01:00.2 --file-prefix=refl"
        ]
    )
    for _ in range(45):
        time.sleep(0.2)
        pane = subprocess.check_output(
            CARD_SSH + ["tmux capture-pane -t refl -p 2>/dev/null || true"], text=True
        )
        if "reflector running on port 0" in pane:
            return pane.strip()
    pane = subprocess.check_output(
        CARD_SSH + ["tmux capture-pane -t refl -p 2>/dev/null || true"], text=True
    )
    raise RuntimeError(f"refl failed to come up:\n{pane}")


def stop_card():
    subprocess.run(
        CARD_SSH + ["pkill -INT -f '[/]root/bench/mrv196_card' || true"], check=False
    )
    for _ in range(50):
        time.sleep(0.15)
        p = subprocess.run(
            CARD_SSH + ["pgrep -f '[/]root/bench/mrv196_card' || true"],
            capture_output=True,
            text=True,
        )
        if not p.stdout.strip():
            return
    raise RuntimeError("mrv196_card did not stop cleanly")


def start_card() -> str:
    stop_card()
    stop_refl()
    cmd = (
        f"rm -f /tmp/mrv196_card.log; "
        f"nohup {CARD_BIN} -l 3,4 -a 0002:01:00.2 -a 0000:06:00.1 --file-prefix=mrv196c "
        f"--log-level=lib.eal:warning -- --control 10.10.0.2:7526 --sync 10.10.0.2:7527 "
        f"--dpi-vf 0000:06:00.1 > /tmp/mrv196_card.log 2>&1 < /dev/null & echo $!"
    )
    subprocess.check_call(CARD_SSH + [cmd], stdout=subprocess.DEVNULL)
    for _ in range(60):
        time.sleep(0.15)
        log = subprocess.check_output(
            CARD_SSH + ["cat /tmp/mrv196_card.log 2>/dev/null || true"], text=True
        )
        if "mrv196_card: BAR4 slot 15 ready" in log:
            return log.strip()
    log = subprocess.check_output(
        CARD_SSH + ["cat /tmp/mrv196_card.log 2>/dev/null || true"], text=True
    )
    raise RuntimeError(f"mrv196_card failed to start:\n{log}")


def append_jsonl(path: pathlib.Path, rec: dict):
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def build_arms() -> list[dict]:
    """Return all unique experiment arms across Matrix A, B, and C."""
    arms: list[dict] = []

    def add(
        suite: str,
        label: str,
        victim: str,
        vcpus: str,
        aggr: str,
        acpus: str,
        *,
        target_mpps: float = 0.0,
        bar4_batch: int = 512,
        ram_batch: int = 128,
        ram_ring_mib: int = 256,
        dpi_ring_mib: int = 16,
        cha_occ: str = "all",
        chase_k: int = 0,
    ):
        arms.append(
            {
                "suite": suite,
                "label": label,
                "victim": victim,
                "vcpus": vcpus,
                "aggr": aggr,
                "acpus": acpus,
                "target_mpps": target_mpps,
                "bar4_batch": bar4_batch,
                "ram_batch": ram_batch,
                "ram_ring_mib": ram_ring_mib,
                "dpi_ring_mib": dpi_ring_mib,
                "cha_occ": cha_occ,
                "chase_k": chase_k,
            }
        )

    c1 = "52"
    c2 = "52,53"
    c4 = "52,53,54,55"
    c6 = "52,53,54,55,56,57"
    c8 = "52,53,54,55,56,57,58,59"

    primary_victims = [
        ("llc", "llc-chase", "40"),
        ("dram", "dram-chase", "40"),
        ("t1c", "stream-triad", "40"),
        ("t6c", "stream-triad", "40,41,42,43,44,45"),
    ]

    # ---- Matrix A: BAR4 MMIO rate ladder & multi-core CHA TOR backpressure cliff (cha_occ=all) ----
    matrix_a_aggrs = [
        ("idle", "idle", "", 0.0, 512),
        ("l1spin_1c", "l1-spin", c1, 0.0, 512),
        ("l1spin_4c", "l1-spin", c4, 0.0, 512),
        ("l1spin_6c", "l1-spin", c6, 0.0, 512),
        ("l1spin_8c", "l1-spin", c8, 0.0, 512),
        ("bar4_1c_50m_b512", "bar4", c1, 50.0, 512),
        ("bar4_1c_100m_b512", "bar4", c1, 100.0, 512),
        ("bar4_1c_160m_b512", "bar4", c1, 160.0, 512),
        ("bar4_1c_max_b512", "bar4", c1, 0.0, 512),
        ("bar4_1c_max_b128", "bar4", c1, 0.0, 128),
        ("bar4_2c_max_b512", "bar4", c2, 0.0, 512),
        ("bar4_4c_max_b512", "bar4", c4, 0.0, 512),
        ("bar4_6c_max_b512", "bar4", c6, 0.0, 512),
        ("bar4_8c_max_b512", "bar4", c8, 0.0, 512),
        ("bar4_8c_165m_b512", "bar4", c8, 165.0, 512),
        ("bar4_4c_max_b128", "bar4", c4, 0.0, 128),
        ("bar4_6c_max_b128", "bar4", c6, 0.0, 128),
        ("bar4_8c_max_b128", "bar4", c8, 0.0, 128),
        ("bar4_8c_165m_b128", "bar4", c8, 165.0, 128),
    ]
    for vtag, vkind, vcpus in primary_victims:
        for atag, amode, acpus, tgt, bsz in matrix_a_aggrs:
            add(
                "A_ladder",
                f"{vtag}__{atag}",
                vkind,
                vcpus,
                amode,
                acpus,
                target_mpps=tgt,
                bar4_batch=bsz,
                cha_occ="all",
            )

    # Stream-Copy 6c confirmation across key Matrix A points
    for atag, amode, acpus, tgt, bsz in [
        ("idle", "idle", "", 0.0, 512),
        ("l1spin_8c", "l1-spin", c8, 0.0, 512),
        ("bar4_1c_160m_b512", "bar4", c1, 160.0, 512),
        ("bar4_1c_max_b512", "bar4", c1, 0.0, 512),
        ("bar4_4c_max_b512", "bar4", c4, 0.0, 512),
        ("bar4_8c_max_b512", "bar4", c8, 0.0, 512),
        ("bar4_8c_165m_b128", "bar4", c8, 165.0, 128),
    ]:
        add(
            "A_copy6c",
            f"c6c__{atag}",
            "stream-copy",
            "40,41,42,43,44,45",
            amode,
            acpus,
            target_mpps=tgt,
            bar4_batch=bsz,
            cha_occ="all",
        )

    # Same-core SMT diagnostic (victim cpu40 vs sibling cpu104)
    for vtag, vkind in [("llc", "llc-chase"), ("dram", "dram-chase"), ("t1c", "stream-triad")]:
        add("A_smt", f"{vtag}__smt_l1spin", vkind, "40", "l1-spin", "104", cha_occ="all")
        add(
            "A_smt",
            f"{vtag}__smt_bar4_160m_b512",
            vkind,
            "40",
            "bar4",
            "104",
            target_mpps=160.0,
            bar4_batch=512,
            cha_occ="all",
        )
        add(
            "A_smt",
            f"{vtag}__smt_bar4_max_b512",
            vkind,
            "40",
            "bar4",
            "104",
            target_mpps=0.0,
            bar4_batch=512,
            cha_occ="all",
        )

    # ---- Matrix B: Four-path physical control (~160 Mpps / 10.24 GB/s + 4c matched/max) ----
    matrix_b_aggrs = [
        ("dpi_pull_16m_1c_160m", "dpi-pull", c1, 160.0, 256, 16),
        ("dpi_pull_256m_1c_160m", "dpi-pull", c1, 160.0, 256, 256),
        ("dpi_pull_256m_4c_160m", "dpi-pull", c4, 160.0, 256, 256),
        ("ram_wb_256m_1c_160m", "ram-wb", c1, 160.0, 256, 16),
        ("ram_nt_256m_1c_160m", "ram-nt", c1, 160.0, 256, 16),
        ("ram_wb_256m_4c_160m", "ram-wb", c4, 160.0, 256, 16),
        ("ram_nt_256m_4c_160m", "ram-nt", c4, 160.0, 256, 16),
        ("ram_wb_256m_4c_max", "ram-wb", c4, 0.0, 256, 16),
        ("ram_nt_256m_4c_max", "ram-nt", c4, 0.0, 256, 16),
    ]
    for vtag, vkind, vcpus in primary_victims:
        for atag, amode, acpus, tgt, r_mib, d_mib in matrix_b_aggrs:
            add(
                "B_paths",
                f"{vtag}__{atag}",
                vkind,
                vcpus,
                amode,
                acpus,
                target_mpps=tgt,
                ram_batch=128,
                ram_ring_mib=r_mib,
                dpi_ring_mib=d_mib,
                cha_occ="all",
            )

    for atag, amode, acpus, tgt, r_mib, d_mib in [
        ("dpi_pull_16m_1c_160m", "dpi-pull", c1, 160.0, 256, 16),
        ("dpi_pull_256m_1c_160m", "dpi-pull", c1, 160.0, 256, 256),
        ("ram_wb_256m_1c_160m", "ram-wb", c1, 160.0, 256, 16),
        ("ram_nt_256m_1c_160m", "ram-nt", c1, 160.0, 256, 16),
    ]:
        add(
            "B_copy6c",
            f"c6c__{atag}",
            "stream-copy",
            "40,41,42,43,44,45",
            amode,
            acpus,
            target_mpps=tgt,
            ram_batch=128,
            ram_ring_mib=r_mib,
            dpi_ring_mib=d_mib,
            cha_occ="all",
        )

    # ---- Matrix C: Non-mux CHA TOR decomposition (wcilf & miss-drd) + K=1 DRAM spot check ----
    for vtag, vkind, vcpus in [("llc", "llc-chase", "40"), ("t6c", "stream-triad", "40,41,42,43,44,45")]:
        for atag, amode, acpus, tgt, bsz in [
            ("idle", "idle", "", 0.0, 512),
            ("bar4_1c_50m_b512", "bar4", c1, 50.0, 512),
            ("bar4_1c_100m_b512", "bar4", c1, 100.0, 512),
            ("bar4_1c_160m_b512", "bar4", c1, 160.0, 512),
            ("bar4_1c_max_b512", "bar4", c1, 0.0, 512),
            ("bar4_4c_max_b512", "bar4", c4, 0.0, 512),
            ("bar4_8c_max_b512", "bar4", c8, 0.0, 512),
            ("bar4_8c_max_b128", "bar4", c8, 0.0, 128),
            ("bar4_8c_165m_b128", "bar4", c8, 165.0, 128),
        ]:
            add(
                "C_wcilf",
                f"{vtag}__{atag}__wcilf",
                vkind,
                vcpus,
                amode,
                acpus,
                target_mpps=tgt,
                bar4_batch=bsz,
                cha_occ="wcilf",
            )

    for vtag, vkind, vcpus in [("dram", "dram-chase", "40"), ("t6c", "stream-triad", "40,41,42,43,44,45")]:
        for atag, amode, acpus, tgt, bsz, d_mib in [
            ("idle", "idle", "", 0.0, 512, 16),
            ("bar4_1c_160m_b512", "bar4", c1, 160.0, 512, 16),
            ("bar4_4c_max_b512", "bar4", c4, 0.0, 512, 16),
            ("bar4_8c_max_b512", "bar4", c8, 0.0, 512, 16),
            ("bar4_8c_165m_b128", "bar4", c8, 165.0, 128, 16),
            ("dpi_pull_256m_1c_160m", "dpi-pull", c1, 160.0, 512, 256),
            ("ram_wb_256m_1c_160m", "ram-wb", c1, 160.0, 512, 16),
            ("ram_nt_256m_1c_160m", "ram-nt", c1, 160.0, 512, 16),
        ]:
            add(
                "C_missdrd",
                f"{vtag}__{atag}__missdrd",
                vkind,
                vcpus,
                amode,
                acpus,
                target_mpps=tgt,
                bar4_batch=bsz,
                dpi_ring_mib=d_mib,
                cha_occ="miss-drd",
            )

    # K=1 single-hop DRAM pointer-chase spot check (Item 11)
    for atag, amode, acpus, tgt, bsz, d_mib in [
        ("idle", "idle", "", 0.0, 512, 16),
        ("bar4_1c_160m_b512", "bar4", c1, 160.0, 512, 16),
        ("bar4_8c_max_b512", "bar4", c8, 0.0, 512, 16),
        ("bar4_8c_165m_b128", "bar4", c8, 165.0, 128, 16),
        ("dpi_pull_256m_1c_160m", "dpi-pull", c1, 160.0, 512, 256),
        ("ram_wb_256m_1c_160m", "ram-wb", c1, 160.0, 512, 16),
    ]:
        add(
            "C_dram_k1",
            f"dram_k1__{atag}",
            "dram-chase",
            "40",
            amode,
            acpus,
            target_mpps=tgt,
            bar4_batch=bsz,
            dpi_ring_mib=d_mib,
            cha_occ="all",
            chase_k=1,
        )

    return arms


def run_one(arm: dict, rep: int) -> dict:
    n_v = len([x for x in arm["vcpus"].split(",") if x.strip()])
    n_a = len([x for x in arm["acpus"].split(",") if x.strip()])
    total_cores = n_v + n_a
    # SPR 6438M: 1-9 active cores hold 3.85-3.90 GHz; 10-14 active cores hold 3.48-3.68 GHz turbo bin
    min_ghz_arg = 3.80 if total_cores <= 9 else 3.45
    min_ghz_gate = 3.75 if total_cores <= 9 else 3.40

    reasons: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        run_tag = next_run_tag()
        cmd = [
            HOST_BIN,
            "--victim",
            arm["victim"],
            "--aggr",
            arm["aggr"],
            "--victim-cpus",
            arm["vcpus"],
            "--target-mpps",
            f"{arm['target_mpps']:.2f}",
            "--bar4-sfence-batch",
            str(arm["bar4_batch"]),
            "--ram-batch",
            str(arm["ram_batch"]),
            "--ram-ring-mib",
            str(arm["ram_ring_mib"]),
            "--dpi-ring-mib",
            str(arm["dpi_ring_mib"]),
            "--cha-occ",
            arm["cha_occ"],
            "--duration-ms",
            str(DURATION_MS),
            "--warmup-ms",
            str(WARMUP_MS),
            "--max-warmup-ms",
            str(MAX_WARMUP_MS),
            "--min-ghz",
            f"{min_ghz_arg:.2f}",
            "--run-tag",
            str(run_tag),
        ]
        if arm["acpus"]:
            cmd += ["--aggr-cpus", arm["acpus"]]
        if arm["chase_k"] > 0:
            cmd += ["--chase-k", str(arm["chase_k"])]

        p = subprocess.run(cmd, capture_output=True, text=True)
        rec = {
            "ts_utc": utc_now(),
            "suite": arm["suite"],
            "label": arm["label"],
            "rep": rep,
            "attempt": attempt,
            "run_tag": run_tag,
            "rc": p.returncode,
            "cmd": " ".join(cmd),
        }
        reasons = []
        if p.returncode != 0:
            rec["stderr"] = p.stderr.strip()[:500]
            reasons.append(f"rc={p.returncode}")
        else:
            try:
                data = json.loads(p.stdout.strip().splitlines()[-1])
                rec.update(data)
            except Exception as e:
                rec["stdout_tail"] = p.stdout[-400:]
                reasons.append(f"json_parse:{e}")
            else:
                if data.get("host_pmu_min_pct", 0.0) < 99.99:
                    reasons.append(f"host_pmu_mux={data.get('host_pmu_min_pct')}")
                if data.get("page_faults", -1) != 0:
                    reasons.append(f"page_faults={data.get('page_faults')}")
                if data.get("victim_page_faults", -1) != 0:
                    reasons.append(f"victim_page_faults={data.get('victim_page_faults')}")
                if data.get("aggr_page_faults", -1) != 0:
                    reasons.append(f"aggr_page_faults={data.get('aggr_page_faults')}")
                if data.get("ram_verify_errors", -1) != 0:
                    reasons.append(f"ram_verify_errors={data.get('ram_verify_errors')}")
                if data.get("victim_min_ghz", 0.0) < min_ghz_gate:
                    reasons.append(f"victim_min_ghz={data.get('victim_min_ghz')}<{min_ghz_gate}")
                if n_a > 0 and data.get("aggr_min_ghz", 0.0) < min_ghz_gate:
                    reasons.append(f"aggr_min_ghz={data.get('aggr_min_ghz')}<{min_ghz_gate}")

                chase = data.get("chase")
                if arm["victim"] in ("llc-chase", "dram-chase"):
                    if not chase:
                        reasons.append("missing_chase")
                    else:
                        if chase.get("bad_nodes", -1) != 0:
                            reasons.append(f"bad_nodes={chase.get('bad_nodes')}")
                        if chase.get("total_chunks", 0) < 1000:
                            reasons.append(f"low_chunks={chase.get('total_chunks')}")

                stream = data.get("stream")
                if arm["victim"] in ("stream-copy", "stream-triad"):
                    if not stream:
                        reasons.append("missing_stream")
                    else:
                        if stream.get("verify_errors", -1) != 0:
                            reasons.append(f"stream_verify_errors={stream.get('verify_errors')}")
                        if stream.get("passes", 0) < 5:
                            reasons.append(f"low_passes={stream.get('passes')}")

                if arm["aggr"] == "bar4":
                    cp = data.get("card_pmu") or {}
                    if cp.get("min_pct", 0.0) < 99.99:
                        reasons.append(f"card_pmu_mux={cp.get('min_pct')}")
                    cv = data.get("card_verify") or {}
                    exp_lines = n_a * 4096
                    if (
                        cv.get("checked_lines") != exp_lines
                        or cv.get("zero_lines", -1) != 0
                        or cv.get("torn_lines", -1) != 0
                        or cv.get("bad_lines", -1) != 0
                    ):
                        reasons.append(f"card_verify={cv}")

                if arm["aggr"] == "dpi-pull":
                    cp = data.get("card_pmu") or {}
                    if cp.get("min_pct", 0.0) < 99.99:
                        reasons.append(f"card_pmu_mux={cp.get('min_pct')}")
                    ds = data.get("dpi_stats") or {}
                    if not ds.get("ok") or ds.get("failed", -1) != 0 or ds.get("copies", 0) <= 0:
                        reasons.append(f"dpi_stats={ds}")

        rec["accepted"] = len(reasons) == 0
        rec["reject_reasons"] = reasons
        append_jsonl(ATTEMPTS_PATH, rec)
        if rec["accepted"]:
            append_jsonl(RUNS_PATH, rec)
            return rec
        print(
            f"  [RETRY] rep={rep} {arm['suite']}/{arm['label']} attempt={attempt} reasons={reasons}",
            flush=True,
        )
        time.sleep(0.2)

    raise RuntimeError(
        f"exhausted {MAX_ATTEMPTS} attempts for {arm['suite']}/{arm['label']} rep={rep}: {reasons}"
    )


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for p in (RUNS_PATH, ATTEMPTS_PATH):
        if p.exists():
            p.unlink()

    code_rev = subprocess.check_output(
        ["jj", "log", "-r", "@-", "--no-graph", "-T", 'change_id.short() ++ " " ++ commit_id.short() ++ " " ++ description.first_line()'],
        cwd=str(ROOT),
        text=True,
    ).strip()

    pre_host = read_host_env()
    pre_card = read_card_env()

    env_record = {
        "issue": "MRV-196",
        "started_utc": utc_now(),
        "code_commit": code_rev,
        "duration_ms": DURATION_MS,
        "warmup_ms": WARMUP_MS,
        "reps": REPS,
        "pre_host": pre_host,
        "pre_card": pre_card,
    }

    arms = build_arms()
    total_runs = len(arms) * REPS
    print(f"MRV-196 campaign: {len(arms)} unique arms x {REPS} reps = {total_runs} verified 1 s runs", flush=True)

    card_start_log = ""
    restore_out = ""
    refl_pane = ""
    campaign_err = None
    t_start = time.time()
    completed = 0

    try:
        card_start_log = start_card()
        print(f"Card started: {card_start_log.splitlines()[-1]}", flush=True)

        rng = random.Random(196)
        for rep in range(1, REPS + 1):
            order = list(arms)
            if rep == 2:
                order.reverse()
            elif rep == 3:
                rng.shuffle(order)
            t_rep = time.time()
            print(f"=== Rep {rep}/{REPS} ({len(order)} arms) ===", flush=True)
            for idx, arm in enumerate(order, 1):
                rec = run_one(arm, rep)
                completed += 1
                if idx % 20 == 0 or idx == len(order):
                    el = time.time() - t_start
                    rate = completed / max(el, 1e-6)
                    eta = (total_runs - completed) / max(rate, 1e-6)
                    v = rec.get("chase") or rec.get("stream") or {}
                    vval = (
                        f"mean={v['net_mean_ns']:.2f}ns p99={v['p99_ns']:.1f}ns"
                        if "net_mean_ns" in v
                        else f"bw={v.get('mean_gb_s', 0):.2f}GB/s"
                    )
                    print(
                        f"  [{completed:3d}/{total_runs}] rep={rep} {arm['suite']}/{arm['label']:34s} "
                        f"mpps={rec['aggr_mpps']:6.2f} cha={rec['cha']['occ_entries']:5.1f} "
                        f"imc={rec['imc']['tot_gb_s']:5.2f} {vval} (elapsed={el:.0f}s eta={eta:.0f}s)",
                        flush=True,
                    )
            print(f"=== Rep {rep}/{REPS} done in {time.time() - t_rep:.1f}s ===", flush=True)
    except Exception as e:
        campaign_err = repr(e)
        print(f"ERROR during campaign: {e}", file=sys.stderr, flush=True)
        raise
    finally:
        print("Restoring card baseline...", flush=True)
        try:
            stop_card()
        except Exception as e:
            print(f"WARN stop_card: {e}", file=sys.stderr, flush=True)
        restore_out = subprocess.check_output(
            CARD_SSH + [f"{CARD_BIN} --restore"], text=True
        ).strip()
        print(f"BAR4 restore: {restore_out}", flush=True)
        refl_pane = start_refl()
        print("refl tmux restarted; sleeping 21 s for delayed EDAC & AER audit...", flush=True)
        time.sleep(21)
        post_host = read_host_env()
        post_card = read_card_env()
        env_record.update(
            {
                "finished_utc": utc_now(),
                "elapsed_s": round(time.time() - t_start, 2),
                "unique_arms": len(arms),
                "planned_runs": total_runs,
                "completed_runs": completed,
                "card_start_log": card_start_log,
                "bar4_restore_output": restore_out,
                "refl_restart_pane": refl_pane,
                "post_restore_host": post_host,
                "post_restore_card": post_card,
                "campaign_error": campaign_err,
            }
        )
        ENV_PATH.write_text(json.dumps(env_record, indent=2) + "\n")
        print(
            f"Post-restore audit saved to {ENV_PATH}: "
            f"host_edac_ue={post_host['host_edac_ue']} "
            f"rp_98_01_aer={post_host['rp_98_01_aer']} "
            f"cn106_aer={post_host['cn106_aer']} "
            f"card_mc0_ue={post_card['mc0_ue']} card_sei_uer={post_card['sei_uer']} "
            f"eth0={post_card['eth0_driver']}/{post_card['eth0_link']} refl={post_card['refl_tmux']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
