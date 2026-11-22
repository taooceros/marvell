#!/usr/bin/env python3
"""MRV-127 full interleaved experiment harness (pure commercial PCIe NIC, zero DSA, zero SDP per MRV-49).

Fixed commercial hardware baseline:
  - Host: 1 core cpu40 (3.90 GHz, performance), Socket-1 uncore 2.20 GHz, intel_iommu=on,sm_on
  - Card: 1 core core 3 (2.00 GHz) + 2 DPI VFs (0000:06:00.1 FromHost + 0000:06:00.2 ToHost)
  - Memory: 128 MiB Socket-1 2 MiB hugepage retained TX pool (64 x 2 MiB), MAX_DRAIN = 32
  - Zero SDP, zero Intel DSA.

Parts (single-variable design per MRV-82, n = 3 interleaved reps, deterministic seeds per rep):
  - part0_cal     : Idle pure-register compute quantum calibration (batch = 16 ~= 5.30 us >= 4 us)
  - part1_pool    : Sequential vs uniform random 2 MiB page-offset access (4 KiB W=1/16/32, 64 B W=16)
  - part2_cliff   : 256 B & 512 B slow-cliff elimination (dma_threshold 1024/512/256 vs OP_PUSH C_line)
  - part3_openmix : True open-loop independent Poisson mixed load (Shared W=32 / same-credit Shared W=64
                    vs Split (W_small, W_large) + C_line, plus <= 256 B mix threshold trade-off & rand pool)
  - part4_cpu     : DirectPush-only CPU headroom with >= 4 us compute quantum batch (batch = 16)
"""

import json
import pathlib
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = pathlib.Path(__file__).resolve().parent
RUNS_PATH = OUT_DIR / "runs.jsonl"
ENV_PATH = OUT_DIR / "env.json"
HOST_BIN = str(REPO / "testbed/target/release/mrv127_host")
CARD_BIN = "/root/bench/mrv127_card"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

REPS = 3


def sh(cmd, check=True, timeout=180):
    r = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode != 0:
        print(f"CMD FAILED ({r.returncode}): {cmd}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}", file=sys.stderr, flush=True)
        raise subprocess.CalledProcessError(r.returncode, cmd, r.stdout, r.stderr)
    return r


def ssh(script, check=True, timeout=60):
    return sh(CARD_SSH + [script], check=check, timeout=timeout)


def check_host_env():
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    cur = int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())
    uncore = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz")):
        uncore[p.parent.name] = int(p.read_text().strip())
    paranoid = int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    hp_free = int(
        pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/free_hugepages").read_text().strip()
    )
    hp_total = int(
        pathlib.Path("/sys/devices/system/node/node1/hugepages/hugepages-2048kB/nr_hugepages").read_text().strip()
    )
    edac = [
        int(p.read_text().strip())
        for p in sorted(pathlib.Path("/sys/devices/system/edac/mc").glob("mc*/ue_count"))
    ]
    cmdline = pathlib.Path("/proc/cmdline").read_text().strip()
    assert gov == "performance", f"cpu40 governor={gov}"
    assert uncore.get("package_01_die_01", 0) >= 2_190_000, f"uncore={uncore}"
    assert paranoid <= 0, f"perf_event_paranoid={paranoid}"
    assert hp_free >= 256, f"node1 2M hugepages free too low: {hp_free}"
    return {
        "cpu40_governor": gov,
        "cpu40_khz": cur,
        "uncore_khz": uncore,
        "perf_event_paranoid": paranoid,
        "node1_hugepages_2m_free": hp_free,
        "node1_hugepages_2m_total": hp_total,
        "edac_ue_counts": edac,
        "edac_ue_sum": sum(edac),
        "cmdline": cmdline,
    }


def check_card_env():
    out = ssh(
        "echo freq=$(cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 0);"
        "echo pem_wc=$(lsmod | grep -c '^pem_wc ' || true);"
        "echo hp512_free=$(cat /sys/kernel/mm/hugepages/hugepages-524288kB/free_hugepages 2>/dev/null || echo 0);"
        "echo edac_ue=$(grep . /sys/devices/system/edac/*/*/ue_count 2>/dev/null | awk -F: '{s+=$2} END{print s+0}');"
        "echo sei_uer=$(dmesg | grep -c 'EDAC DEVICE.*SEI-UER' || true);"
        "echo eth0_state=$(ip -br link show eth0 | awk '{print $2}');"
        "echo dpi_vfs=$(lspci -D | grep -c '0000:06:00\\.[1-4]' || true)"
    ).stdout
    info = {}
    for line in out.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k] = int(v) if v.isdigit() else v
    return info


def stop_card_server():
    ssh(
        "tmux send-keys -t m127 C-c 2>/dev/null || true; "
        "tmux send-keys -t refl C-c 2>/dev/null || true; "
        "sleep 0.8; "
        "tmux kill-session -t m127 2>/dev/null || true; "
        "tmux kill-session -t refl 2>/dev/null || true; "
        "sleep 0.3"
    )


def start_card_direct():
    stop_card_server()
    ssh(
        "lsmod | grep -q '^pem_wc ' || insmod /root/bench/pem_wc.ko; "
        f'tmux new-session -d -s m127 "{CARD_BIN} -l 3 -a 0002:01:00.2 -a 0000:06:00.1 -a 0000:06:00.2 --file-prefix=m127 --"; '
        'for i in $(seq 1 40); do '
        '  if tmux capture-pane -pt m127 | grep -q "direct ready"; then break; fi; '
        '  sleep 0.2; '
        'done; '
        'tmux capture-pane -pt m127 | grep "direct ready"'
    )


def run_calibration(rep):
    cmd = [
        HOST_BIN, "-l", "40", "--file-prefix=mrv127h",
        "-a", "0000:99:02.0", "--socket-mem=0,512", "--iova-mode=va", "--",
        "--calibrate-app-work-ms", "500", "--app-work-batch", "16", "--pmu",
    ]
    res = sh(cmd, timeout=60)
    for line in res.stdout.splitlines():
        if line.startswith("MRV127_CAL "):
            rec = json.loads(line[len("MRV127_CAL "):])
            rec["part"] = "part0_cal"
            rec["cfg"] = "app_work_idle_cal_b16"
            rec["rep"] = rep
            rec["ts_utc"] = datetime.now(timezone.utc).isoformat()
            rec["cmd"] = " ".join(cmd)
            assert rec["host_pmu"]["page_faults"] == 0, f"cal page_faults={rec['host_pmu']['page_faults']}"
            assert rec["host_pmu"]["min_running_pct"] >= 99.9, f"cal pmu pct={rec['host_pmu']['min_running_pct']}"
            rec["verified"] = True
            return rec
    raise RuntimeError(f"No MRV127_CAL line:\n{res.stdout}\n{res.stderr}")


def mk_closed(part, cfg, size, window, dma_threshold, pool_mode, small_line_cap=0, rounds=200_000, warmup=20_000):
    return {
        "part": part,
        "cfg": cfg,
        "mode": "closed",
        "size": size,
        "window": window,
        "dma_threshold": dma_threshold,
        "pool_mode": pool_mode,
        "small_line_cap": small_line_cap,
        "rounds": rounds,
        "warmup": warmup,
    }


def mk_open(
    part,
    cfg,
    credit_mode,
    small_rate,
    large_rate,
    w_shared=32,
    w_small=16,
    w_large=32,
    small_line_cap=0,
    dma_threshold=512,
    pool_mode="rand-large",
    small_profile="64",
    large_size=4096,
    warmup_ms=50,
    measure_ms=250,
    app_work_batch=0,
):
    return {
        "part": part,
        "cfg": cfg,
        "mode": "open-mix",
        "credit_mode": credit_mode,
        "small_rate": small_rate,
        "large_rate": large_rate,
        "w_shared": w_shared,
        "w_small": w_small,
        "w_large": w_large,
        "small_line_cap": small_line_cap,
        "dma_threshold": dma_threshold,
        "pool_mode": pool_mode,
        "small_profile": small_profile,
        "large_size": large_size,
        "warmup_ms": warmup_ms,
        "measure_ms": measure_ms,
        "app_work_batch": app_work_batch,
    }


def build_specs():
    specs = []

    # ---- Part 1: Sequential vs Random 2 MiB Page-Offset KV Access ----
    for pool in ("seq", "rand"):
        specs.append(mk_closed("part1_pool", f"p1_4k_w1_{pool}",  4096, 1,  1024, pool, rounds=50_000, warmup=5_000))
        specs.append(mk_closed("part1_pool", f"p1_4k_w16_{pool}", 4096, 16, 1024, pool, rounds=200_000))
        specs.append(mk_closed("part1_pool", f"p1_4k_w32_{pool}", 4096, 32, 1024, pool, rounds=200_000))
        specs.append(mk_closed("part1_pool", f"p1_64_w16_{pool}", 64,   16, 1024, pool, rounds=200_000))

    # ---- Part 2: 256 B & 512 B Slow-Cliff Elimination ----
    # Sequential baseline (matches MRV-117) across C_line and dma_threshold
    specs.append(mk_closed("part2_cliff", "p2_256_w32_t1024_uncap_seq", 256, 32, 1024, "seq", small_line_cap=0))
    for cl in (24, 32, 40, 48, 64, 80, 96):
        specs.append(mk_closed("part2_cliff", f"p2_256_w32_t512_cl{cl}_seq", 256, 32, 512, "seq", small_line_cap=cl))
    specs.append(mk_closed("part2_cliff", "p2_256_w32_t256_dma_seq", 256, 32, 256, "seq", small_line_cap=0))

    # Random-page parity on key 256 B points + W=1 latency + 512 B threshold jump
    specs.append(mk_closed("part2_cliff", "p2_256_w32_t1024_uncap_rand", 256, 32, 1024, "rand", small_line_cap=0))
    specs.append(mk_closed("part2_cliff", "p2_256_w32_t512_cl32_rand",   256, 32, 512,  "rand", small_line_cap=32))
    specs.append(mk_closed("part2_cliff", "p2_256_w32_t512_cl48_rand",   256, 32, 512,  "rand", small_line_cap=48))
    specs.append(mk_closed("part2_cliff", "p2_256_w32_t256_dma_rand",    256, 32, 256,  "rand", small_line_cap=0))
    specs.append(mk_closed("part2_cliff", "p2_256_w1_t512_push_rand",    256, 1,  512,  "rand", rounds=50_000, warmup=5_000))
    specs.append(mk_closed("part2_cliff", "p2_256_w1_t256_dma_rand",     256, 1,  256,  "rand", rounds=50_000, warmup=5_000))
    specs.append(mk_closed("part2_cliff", "p2_512_w32_t1024_push_rand",  512, 32, 1024, "rand", small_line_cap=0))
    specs.append(mk_closed("part2_cliff", "p2_512_w32_t512_dma_rand",    512, 32, 512,  "rand", small_line_cap=0))

    # ---- Part 3A: Open-loop Poisson small-rate sweep (64 B + 4 KiB rand @ 1.5 M/s) ----
    # Plus pure 64 B @ 8 M/s anchor and 8 M + 1.0 M/s
    specs.append(mk_open("part3_openmix", "p3_8m_0m_spl16", "split", 8.0, 0.0, w_small=16, w_large=32, small_line_cap=32, dma_threshold=512))
    specs.append(mk_open("part3_openmix", "p3_8m_0m_spl32", "split", 8.0, 0.0, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512))

    specs.append(mk_open("part3_openmix", "p3_8m_1p0m_shr32", "shared", 8.0, 1.0, w_shared=32, dma_threshold=1024))
    specs.append(mk_open("part3_openmix", "p3_8m_1p0m_spl16", "split",  8.0, 1.0, w_small=16, w_large=32, small_line_cap=32, dma_threshold=512))
    specs.append(mk_open("part3_openmix", "p3_8m_1p0m_spl32", "split",  8.0, 1.0, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512))
    for sm_r in (6.0, 8.0, 10.0, 12.0):
        tag = f"{int(sm_r)}m_1p5m"
        specs.append(mk_open("part3_openmix", f"p3_{tag}_shr32", "shared", sm_r, 1.5, w_shared=32, dma_threshold=1024))
        specs.append(mk_open("part3_openmix", f"p3_{tag}_spl16", "split",  sm_r, 1.5, w_small=16, w_large=32, small_line_cap=32, dma_threshold=512))
        specs.append(mk_open("part3_openmix", f"p3_{tag}_spl32", "split",  sm_r, 1.5, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512))
    specs.append(mk_open("part3_openmix", "p3_8m_1p5m_spl24",  "split",  8.0,  1.5, w_small=24, w_large=32, small_line_cap=32, dma_threshold=512))
    specs.append(mk_open("part3_openmix", "p3_10m_1p5m_spl24", "split",  10.0, 1.5, w_small=24, w_large=32, small_line_cap=32, dma_threshold=512))
    specs.append(mk_open("part3_openmix", "p3_8m_1p5m_shr64",  "shared", 8.0,  1.5, w_shared=64, dma_threshold=1024))
    specs.append(mk_open("part3_openmix", "p3_12m_1p5m_shr64", "shared", 12.0, 1.5, w_shared=64, dma_threshold=1024))

    # ---- Part 3B: Open-loop Poisson large-rate saturation sweep (64 B @ 8.0 M/s + 4 KiB rand 2.0/2.2/2.5 M/s) ----
    for lg_r, ltag in ((2.0, "2p0m"), (2.2, "2p2m"), (2.5, "2p5m")):
        specs.append(mk_open("part3_openmix", f"p3_8m_{ltag}_shr32", "shared", 8.0, lg_r, w_shared=32, dma_threshold=1024))
        specs.append(mk_open("part3_openmix", f"p3_8m_{ltag}_shr64", "shared", 8.0, lg_r, w_shared=64, dma_threshold=1024))
        specs.append(mk_open("part3_openmix", f"p3_8m_{ltag}_spl16", "split",  8.0, lg_r, w_small=16, w_large=32, small_line_cap=32, dma_threshold=512))
        specs.append(mk_open("part3_openmix", f"p3_8m_{ltag}_spl32", "split",  8.0, lg_r, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512))

    # ---- Part 3C: Mixed <= 256 B small stream (75% 64 B + 25% 256 B) + 4 KiB rand @ 1.5 M/s ----
    for sm_r in (6.0, 8.0):
        tag = f"mix256_{int(sm_r)}m_1p5m"
        specs.append(mk_open("part3_openmix", f"p3_{tag}_shr32_t1024",     "shared", sm_r, 1.5, w_shared=32, dma_threshold=1024, small_profile="mix64-256"))
        specs.append(mk_open("part3_openmix", f"p3_{tag}_spl32_t512_cl32", "split",  sm_r, 1.5, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512, small_profile="mix64-256"))
        specs.append(mk_open("part3_openmix", f"p3_{tag}_spl32_t512_cl48", "split",  sm_r, 1.5, w_small=32, w_large=32, small_line_cap=48, dma_threshold=512, small_profile="mix64-256"))
        specs.append(mk_open("part3_openmix", f"p3_{tag}_spl32_t256",      "split",  sm_r, 1.5, w_small=32, w_large=32, small_line_cap=32, dma_threshold=256, small_profile="mix64-256"))

    # ---- Part 3D: Full-random pool sensitivity (even 64 B headers random in 128 MiB) ----
    specs.append(mk_open("part3_openmix", "p3_6m_1p5m_spl32_randall", "split", 6.0, 1.5, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512, pool_mode="rand"))

    # ---- Part 4: DirectPush-only CPU headroom with >= 4 us compute quantum batch (batch=16 ~= 5.30 us) ----
    specs.append(mk_open("part4_cpu", "p4_aw0_0m_1p0m_spl32",  "split",  0.0, 1.0, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512, app_work_batch=0))
    specs.append(mk_open("part4_cpu", "p4_aw16_0m_1p0m_spl32", "split",  0.0, 1.0, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512, app_work_batch=16))
    specs.append(mk_open("part4_cpu", "p4_aw0_4m_1p0m_spl32",  "split",  4.0, 1.0, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512, app_work_batch=0))
    specs.append(mk_open("part4_cpu", "p4_aw16_4m_1p0m_shr32", "shared", 4.0, 1.0, w_shared=32, dma_threshold=1024, app_work_batch=16))
    specs.append(mk_open("part4_cpu", "p4_aw16_4m_1p0m_spl32", "split",  4.0, 1.0, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512, app_work_batch=16))
    specs.append(mk_open("part4_cpu", "p4_aw0_4m_1p5m_spl32",  "split",  4.0, 1.5, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512, app_work_batch=0))
    specs.append(mk_open("part4_cpu", "p4_aw16_4m_1p5m_shr32", "shared", 4.0, 1.5, w_shared=32, dma_threshold=1024, app_work_batch=16))
    specs.append(mk_open("part4_cpu", "p4_aw16_4m_1p5m_spl32", "split",  4.0, 1.5, w_small=32, w_large=32, small_line_cap=32, dma_threshold=512, app_work_batch=16))

    return specs


def run_one(spec, rep):
    seed = 0x1270_2026_1122 + rep * 0x10007
    cmd = [
        HOST_BIN, "-l", "40", "--file-prefix=mrv127h",
        "-a", "0000:99:02.0", "--socket-mem=0,512", "--iova-mode=va", "--",
        "--mode", spec["mode"],
        "--pool-mode", spec["pool_mode"],
        "--dma-threshold", str(spec["dma_threshold"]),
        "--small-line-cap", str(spec["small_line_cap"]),
        "--seed", str(seed),
        "--pmu",
    ]
    if spec["mode"] == "closed":
        cmd += [
            "--size", str(spec["size"]),
            "--window", str(spec["window"]),
            "--rounds", str(spec["rounds"]),
            "--warmup", str(spec["warmup"]),
        ]
    else:
        cmd += [
            "--credit-mode", spec["credit_mode"],
            "--small-rate-mrps", f"{spec['small_rate']:.3f}",
            "--large-rate-mrps", f"{spec['large_rate']:.3f}",
            "--w-shared", str(spec["w_shared"]),
            "--w-small", str(spec["w_small"]),
            "--w-large", str(spec["w_large"]),
            "--small-profile", spec["small_profile"],
            "--large-size", str(spec["large_size"]),
            "--warmup-ms", str(spec["warmup_ms"]),
            "--measure-ms", str(spec["measure_ms"]),
            "--app-work-batch", str(spec["app_work_batch"]),
        ]

    res = sh(cmd, timeout=90)
    payload = None
    for line in res.stdout.splitlines():
        if line.startswith("MRV127 "):
            payload = json.loads(line[len("MRV127 "):])
            break
    if payload is None:
        raise RuntimeError(f"No MRV127 output for {spec['cfg']} rep={rep}:\n{res.stdout}\n{res.stderr}")

    payload["part"] = spec["part"]
    payload["cfg"] = spec["cfg"]
    payload["rep"] = rep
    payload["seed"] = seed
    payload["ts_utc"] = datetime.now(timezone.utc).isoformat()
    payload["cmd"] = " ".join(cmd)

    # Strict verification per MRV-127 criteria
    errs = []
    for k in ("lost", "bad", "torn"):
        if payload[k] != 0:
            errs.append(f"{k}={payload[k]}")
    hp = payload["host_pmu"]
    if hp["page_faults"] != 0:
        errs.append(f"page_faults={hp['page_faults']}")
    if hp["min_running_pct"] < 99.9:
        errs.append(f"pmu_running_pct={hp['min_running_pct']}")
    if payload["pool_page_kb"] != 2048 or payload["pool_numa_n0"] != 0 or payload["pool_numa_n1"] != 64:
        errs.append(f"pool_numa=({payload['pool_page_kb']},{payload['pool_numa_n0']},{payload['pool_numa_n1']})")
    cs = payload["card_stats"]
    if cs["corrupt"] != 0 or cs["dma_failed"] != 0:
        errs.append(f"card_err={cs}")
    card_ops = cs["push_ops"] + cs["dma_small_ops"] + cs["dma_large_ops"]
    if card_ops != payload["sent_total"]:
        errs.append(f"card_ops={card_ops}!=sent_total={payload['sent_total']}")
    if payload["measured"] != payload["sent_measured"]:
        errs.append(f"measured={payload['measured']}!=sent_measured={payload['sent_measured']}")
    if spec["mode"] == "closed" and spec["window"] == 1:
        mean_us = payload["lat_all_tot_us"]["mean"]
        ratio = mean_us * payload["req_per_s"] / 1e6
        if not (0.90 <= ratio <= 1.03):
            errs.append(f"W=1 RTT*rate={ratio:.4f}")
    payload["verified"] = len(errs) == 0
    payload["verify_errors"] = errs
    if errs:
        raise RuntimeError(f"Verification failed on {spec['cfg']} rep={rep}: {errs}")
    return payload


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if RUNS_PATH.exists():
        RUNS_PATH.unlink()

    host_before = check_host_env()
    card_before = check_card_env()
    specs = build_specs()
    total_runs = REPS * (1 + len(specs))
    print(
        f"MRV-127 starting: {REPS} interleaved reps x (1 cal + {len(specs)} configs) = {total_runs} runs",
        flush=True,
    )

    start_card_direct()
    run_id = 0
    t_start = time.time()
    cal_idle_rates = []

    with RUNS_PATH.open("w") as f:
        for rep in range(REPS):
            run_id += 1
            cal = run_calibration(rep)
            cal["run_id"] = run_id
            cal_idle_rates.append(cal["units_per_s"])
            f.write(json.dumps(cal) + "\n")
            f.flush()
            print(
                f"[{run_id:3d}/{total_runs}] rep={rep} CAL {cal['units_per_s']/1e6:.3f} M/s "
                f"(b16={cal['batch_us']:.2f}us) ghz={cal['host_pmu']['ghz']:.2f}",
                flush=True,
            )

            # Deterministic interleaved shuffle per rep to cancel thermal/order drift
            rep_specs = list(specs)
            random.Random(12700 + rep).shuffle(rep_specs)

            for spec in rep_specs:
                run_id += 1
                rec = run_one(spec, rep)
                rec["run_id"] = run_id
                f.write(json.dumps(rec) + "\n")
                f.flush()
                hp = rec["host_pmu"]
                st = rec["lat_small_tot_us"] or {}
                sw = rec["lat_small_wire_us"] or {}
                lt = rec["lat_large_tot_us"] or {}
                extra = ""
                if rec["app_work_batch"] > 0:
                    net_pct = (1.0 - rec["app_work_per_s"] / cal_idle_rates[-1]) * 100.0
                    extra = f" | net_cpu={net_pct:5.1f}%"
                print(
                    f"[{run_id:3d}/{total_runs}] rep={rep} {spec['cfg']:28s} | "
                    f"mreq={rec['mreq_s']:6.3f} (sm={rec['achieved_small_mrps']:5.2f} lg={rec['achieved_large_mrps']:4.2f}) "
                    f"bi={rec['bidir_gbps']:5.2f}G | sm_tot={st.get('p50',0):6.2f}/{st.get('p99',0):8.2f} "
                    f"sm_w={sw.get('p99',0):5.2f} lg_tot={lt.get('p99',0):8.2f} | ghz={hp['ghz']:.2f}{extra}",
                    flush=True,
                )

    stop_card_server()
    ssh(f"{CARD_BIN} --restore")
    time.sleep(1.0)
    host_after = check_host_env()
    card_after = check_card_env()
    elapsed = time.time() - t_start
    env_doc = {
        "issue": "MRV-127",
        "date": "2026-11-22",
        "elapsed_s": round(elapsed, 2),
        "total_runs": run_id,
        "reps": REPS,
        "cal_idle_units_per_s": cal_idle_rates,
        "host_before": host_before,
        "host_after": host_after,
        "host_edac_ue_delta": host_after["edac_ue_sum"] - host_before["edac_ue_sum"],
        "card_before": card_before,
        "card_after": card_after,
        "card_edac_ue_delta": card_after["edac_ue"] - card_before["edac_ue"],
        "card_sei_uer_delta": card_after["sei_uer"] - card_before["sei_uer"],
    }
    ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n")
    print(
        f"MRV-127 COMPLETE: {run_id} verified runs in {elapsed:.1f}s | "
        f"host_edac_delta={env_doc['host_edac_ue_delta']} card_edac_delta={env_doc['card_edac_ue_delta']} "
        f"card_sei_delta={env_doc['card_sei_uer_delta']}",
        flush=True,
    )
    assert env_doc["host_edac_ue_delta"] == 0
    assert env_doc["card_edac_ue_delta"] == 0
    assert env_doc["card_sei_uer_delta"] == 0


if __name__ == "__main__":
    main()
