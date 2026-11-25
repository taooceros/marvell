#!/usr/bin/env python3
"""MRV-187 n=3 interleaved experiment harness: Intel DSA (wq2.0 SVA) direct MEMMOVE (>64 B)
into CN106 PF BAR4 slot 15 on saturn (Xeon Gold 6438M SPR + PCIe Gen5 x4 CN106).

Safety & rigor invariants:
- BAR4 slot 15 is strictly accessed under the MRV-26 lease (`mrv187_card` on 10.10.0.2:7526).
- Zero silent retries: every attempt (accepted or rejected) is recorded in `attempts.jsonl`;
  accepted verified runs are recorded in `runs.jsonl`.
- Quality gates on every accepted run: rc == 0, err_ops == 0, rb_mismatches == 0,
  verify.bad_bytes == 0, verify.bad_blocks == 0, swerror_zero == True,
  host_pmu_min_pct == 100.0, card_pmu.min_pct == 100.0 (when present),
  cpu_ghz >= 3.85, page_faults == 0.
- `try ... finally` unconditionally restores `dsa2/wq2.0` to 1 engine, stops `mrv187_card`,
  runs `mrv187_card --restore`, restarts `refl` tmux (`reflector-rs`), waits 21 s for EDAC
  audit, and writes pre/post `env.json`.
"""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv187")
LOG_DIR = ROOT / "logs/mrv187-2026-11-25"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = "/tmp/mrv187-target/release/mrv187_host"
CARD_BIN = "/root/bench/mrv187_card"
ENGINES_SH = str(ROOT / "bench/host_dsa_engines.sh")
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

BAR4_UC = "/sys/bus/pci/devices/0000:99:00.0/resource4"
BAR4_WC = "/sys/bus/pci/devices/0000:99:00.0/resource4_wc"

REPS = 3
MAX_ATTEMPTS = 4
DURATION_MS = 600
LAT_ROUNDS = 4000
LAT_WARMUP = 500
ALL_SIZES = [64, 128, 256, 512, 1024, 2048, 4096, 16384, 65536]
UNALIGNED_SIZES = [96, 192, 300]

SEED_COUNTER = 0x187A_0000


def next_seed() -> int:
    global SEED_COUNTER
    SEED_COUNTER += 1
    return SEED_COUNTER


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
    for k, fn in [("cor", "aer_dev_correctable"), ("nonfatal", "aer_dev_nonfatal"), ("fatal", "aer_dev_fatal")]:
        p = pathlib.Path(f"/sys/bus/pci/devices/{bdf}/{fn}")
        tot = -1
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith("TOTAL_ERR_"):
                    tot = int(line.split()[1])
        out[k] = tot
    return out


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
    active_eng = sum(
        1 for i in range(4)
        if pathlib.Path(f"/sys/bus/dsa/devices/engine2.{i}/group_id").read_text().strip() == "0"
    )
    return {
        "cpu40_gov": pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip(),
        "cpu40_cur_khz": int(pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip()),
        "uncore_khz": uncore,
        "perf_event_paranoid": int(pathlib.Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip()),
        "dsa2_state": pathlib.Path("/sys/bus/dsa/devices/dsa2/state").read_text().strip(),
        "dsa2_pasid_enabled": int(pathlib.Path("/sys/bus/dsa/devices/dsa2/pasid_enabled").read_text().strip()),
        "dsa2_swerror": pathlib.Path("/sys/bus/dsa/devices/dsa2/errors").read_text().strip(),
        "dsa2_active_engines_group0": active_eng,
        "wq2_0_mode": pathlib.Path("/sys/bus/dsa/devices/wq2.0/mode").read_text().strip(),
        "wq2_0_type": pathlib.Path("/sys/bus/dsa/devices/wq2.0/type").read_text().strip(),
        "wq2_0_size": int(pathlib.Path("/sys/bus/dsa/devices/wq2.0/size").read_text().strip()),
        "cn106_aer": read_aer("0000:99:00.0"),
        "dsa2_aer": read_aer("0000:f2:01.0"),
        "rp_98_01_aer": read_aer("0000:98:01.0"),
        "host_edac_ce": edac_ce,
        "host_edac_ue": edac_ue,
        "host_bin_sha256": sha256_file(HOST_BIN),
    }


def read_card_env() -> dict:
    cmd = (
        "cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq 2>/dev/null || echo 2000000; "
        "lsmod | grep -c '^pem_wc ' || true; "
        "cat /sys/devices/system/edac/mc/mc0/ce_count; "
        "cat /sys/devices/system/edac/mc/mc0/ue_count; "
        "dmesg | grep -ciE 'SEI-UER|SError' || true; "
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
        "eth0_driver": out[5],
        "eth0_link": out[6],
        "refl_tmux": out[7],
        "card_bin_sha256": out[8],
    }


def set_dsa_engines(n: int):
    subprocess.check_call([ENGINES_SH, str(n)], stdout=subprocess.DEVNULL)
    active = sum(
        1 for i in range(4)
        if pathlib.Path(f"/sys/bus/dsa/devices/engine2.{i}/group_id").read_text().strip() == "0"
    )
    if active != n:
        raise RuntimeError(f"expected {n} active dsa2 engines, got {active}")


def stop_refl():
    subprocess.run(
        CARD_SSH + ["tmux kill-session -t refl 2>/dev/null || true; pkill -INT -f '[/]root/bench/reflector-rs' || true"],
        check=False,
    )
    for _ in range(30):
        time.sleep(0.15)
        p = subprocess.run(CARD_SSH + ["pgrep -f '[/]root/bench/reflector-rs' || true"], capture_output=True, text=True)
        if not p.stdout.strip():
            return
    raise RuntimeError("reflector-rs did not stop cleanly")


def start_refl() -> str:
    stop_refl()
    subprocess.check_call(
        CARD_SSH + ["tmux new-session -d -s refl /root/bench/reflector-rs -l 3 -a 0002:01:00.2 --file-prefix=refl"]
    )
    for _ in range(40):
        time.sleep(0.2)
        pane = subprocess.check_output(CARD_SSH + ["tmux capture-pane -t refl -p 2>/dev/null || true"], text=True)
        if "reflector running on port 0" in pane:
            return pane.strip()
    pane = subprocess.check_output(CARD_SSH + ["tmux capture-pane -t refl -p 2>/dev/null || true"], text=True)
    raise RuntimeError(f"refl failed to come up:\n{pane}")


def stop_card():
    subprocess.run(CARD_SSH + ["pkill -INT -f '[/]root/bench/mrv187_card' || true"], check=False)
    for _ in range(40):
        time.sleep(0.15)
        p = subprocess.run(CARD_SSH + ["pgrep -f '[/]root/bench/mrv187_card' || true"], capture_output=True, text=True)
        if not p.stdout.strip():
            return
    raise RuntimeError("mrv187_card did not stop cleanly")


def start_card() -> str:
    stop_card()
    cmd = (
        f"rm -f /tmp/mrv187_card.log; "
        f"nohup {CARD_BIN} -l 3 --no-pci --file-prefix=mrv187c --log-level=lib.eal:warning "
        f"-- --control 10.10.0.2:7526 --sync 10.10.0.2:7527 > /tmp/mrv187_card.log 2>&1 < /dev/null & echo $!"
    )
    subprocess.check_call(CARD_SSH + [cmd], stdout=subprocess.DEVNULL)
    for _ in range(50):
        time.sleep(0.15)
        log = subprocess.check_output(CARD_SSH + ["cat /tmp/mrv187_card.log 2>/dev/null || true"], text=True)
        if "mrv187_card: BAR4 slot 15 ready" in log:
            return log.strip()
    log = subprocess.check_output(CARD_SSH + ["cat /tmp/mrv187_card.log 2>/dev/null || true"], text=True)
    raise RuntimeError(f"mrv187_card failed to start:\n{log}")


def append_jsonl(path: pathlib.Path, rec: dict):
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def run_one(suite: str, label: str, rep: int, cli_args: list[str], require_full_verify: bool = False) -> dict:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        seed = next_seed()
        cmd = [HOST_BIN] + cli_args + ["--seed", str(seed)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        rec = {
            "ts_utc": utc_now(),
            "suite": suite,
            "label": label,
            "rep": rep,
            "attempt": attempt,
            "seed": seed,
            "rc": p.returncode,
            "cmd": " ".join(cmd),
        }
        reasons = []
        if p.returncode != 0:
            rec["stderr"] = p.stderr.strip()[:400]
            reasons.append(f"rc={p.returncode}")
        else:
            try:
                data = json.loads(p.stdout.strip().splitlines()[-1])
                rec.update(data)
            except Exception as e:
                rec["stdout_tail"] = p.stdout[-400:]
                reasons.append(f"json_parse:{e}")
            else:
                if data.get("err_ops", 1) != 0:
                    reasons.append(f"err_ops={data.get('err_ops')}")
                if data.get("rb_mismatches", 0) != 0:
                    reasons.append(f"rb_mismatches={data.get('rb_mismatches')}")
                ver = data.get("verify", {})
                if ver.get("bad_bytes", 1) != 0 or ver.get("bad_blocks", 1) != 0:
                    reasons.append(f"verify_bad={ver.get('bad_blocks')}/{ver.get('bad_bytes')}")
                if require_full_verify and ver.get("ok_blocks", 0) != data.get("completed_ops", -1):
                    reasons.append(f"full_verify_mismatch={ver.get('ok_blocks')}!={data.get('completed_ops')}")
                if not data.get("swerror_zero", False):
                    reasons.append(f"swerror={data.get('swerror_after')}")
                if data.get("host_pmu_min_pct", 0.0) < 100.0:
                    reasons.append(f"host_pmu_pct={data.get('host_pmu_min_pct')}")
                cpmu = data.get("card_pmu")
                if isinstance(cpmu, dict) and cpmu.get("min_pct", 0.0) < 100.0:
                    reasons.append(f"card_pmu_pct={cpmu.get('min_pct')}")
                if data.get("cpu_ghz", 0.0) < 3.85:
                    reasons.append(f"cpu_ghz={data.get('cpu_ghz')}")
                if data.get("page_faults", 1) != 0:
                    reasons.append(f"page_faults={data.get('page_faults')}")

        rec["accepted"] = len(reasons) == 0
        rec["reject_reasons"] = reasons
        append_jsonl(ATTEMPTS_PATH, rec)
        if rec["accepted"]:
            append_jsonl(RUNS_PATH, rec)
            return rec
        print(f"  [RETRY] {suite}/{label} rep={rep} attempt={attempt}: {reasons}", file=sys.stderr, flush=True)
        time.sleep(0.1)
    raise RuntimeError(f"exhausted {MAX_ATTEMPTS} attempts for {suite}/{label} rep={rep}: {reasons}")


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for p in (RUNS_PATH, ATTEMPTS_PATH):
        if p.exists():
            p.unlink()

    env_doc = {"started_utc": utc_now(), "pre": {}}
    total_ok = 0

    try:
        set_dsa_engines(1)
        env_doc["pre"]["host"] = read_host_env()
        env_doc["pre"]["card"] = read_card_env()
        stop_refl()
        card_log = start_card()
        env_doc["card_start_log"] = card_log
        print(f"[INIT] {card_log}", flush=True)

        # ---- Suite 0: No-Reuse Full-Byte Integrity Stream (every single byte verified on card) ----
        print("\n=== Suite 0: No-Reuse Full-Byte Card Integrity Stream ===", flush=True)
        for sz in ALL_SIZES + UNALIGNED_SIZES:
            chunks = 4 if sz <= 4096 else 8
            r = run_one(
                "verify_stream",
                f"dsa_bar4_uc_{sz}B",
                0,
                ["--mode", "verify-stream", "--engine", "dsa", "--bar4", BAR4_UC, "--size", str(sz), "--window", "32", "--chunks", str(chunks), "--max-chunk-slots", "2048"],
                require_full_verify=True,
            )
            total_ok += 1
            print(
                f"  verify_stream UC {sz:5d} B: ok={r['verify']['ok_blocks']} ({r['verify']['verified_bytes']/1e6:.2f} MB) "
                f"bad=0 B/tlp={r['host_pmu']['bytes_per_tlp']:.1f} tlp/op={r['host_pmu']['tlps_per_op']:.3f}",
                flush=True,
            )
        for sz in [64, 4096, 65536]:
            r = run_one(
                "verify_stream",
                f"dsa_bar4_wc_{sz}B",
                0,
                ["--mode", "verify-stream", "--engine", "dsa", "--bar4", BAR4_WC, "--size", str(sz), "--window", "32", "--chunks", "4", "--max-chunk-slots", "2048"],
                require_full_verify=True,
            )
            total_ok += 1
            print(
                f"  verify_stream WC {sz:5d} B: ok={r['verify']['ok_blocks']} ({r['verify']['verified_bytes']/1e6:.2f} MB) "
                f"bad=0 B/tlp={r['host_pmu']['bytes_per_tlp']:.1f} tlp/op={r['host_pmu']['tlps_per_op']:.3f}",
                flush=True,
            )

        # ---- Suite 1: W=1 Latency Ladder (n=3 interleaved) ----
        print("\n=== Suite 1: W=1 Closed-Loop Latency (n=3 interleaved) ===", flush=True)
        lat_arms = []
        for sz in ALL_SIZES:
            lat_arms.append((f"dsa_bar4_{sz}B", ["--mode", "lat", "--engine", "dsa", "--bar4", BAR4_UC, "--size", str(sz), "--rounds", str(LAT_ROUNDS), "--warmup", str(LAT_WARMUP)]))
            lat_arms.append((f"dsa_host_{sz}B", ["--mode", "lat", "--engine", "dsa-host", "--size", str(sz), "--rounds", str(LAT_ROUNDS), "--warmup", str(LAT_WARMUP)]))
            lat_arms.append((f"movdir_bar4_{sz}B", ["--mode", "lat", "--engine", "movdir", "--bar4", BAR4_UC, "--size", str(sz), "--rounds", str(LAT_ROUNDS), "--warmup", str(LAT_WARMUP)]))
        for sz in [64, 4096, 65536]:
            lat_arms.append((f"dsa_bar4_rbf_{sz}B", ["--mode", "lat", "--engine", "dsa", "--bar4", BAR4_UC, "--size", str(sz), "--rounds", str(LAT_ROUNDS), "--warmup", str(LAT_WARMUP), "--readback-fence"]))
            lat_arms.append((f"movdir_bar4_rbf_{sz}B", ["--mode", "lat", "--engine", "movdir", "--bar4", BAR4_UC, "--size", str(sz), "--rounds", str(LAT_ROUNDS), "--warmup", str(LAT_WARMUP), "--readback-fence"]))
        for rep in range(REPS):
            order = lat_arms if rep % 2 == 0 else list(reversed(lat_arms))
            for label, cli in order:
                r = run_one("lat", label, rep, cli)
                total_ok += 1
            print(f"  lat rep {rep+1}/{REPS} done ({len(order)} runs)", flush=True)

        # ---- Suite 2: High-Concurrency Rate & TLP Size Sweep (n=3 interleaved, 1 engine) ----
        print("\n=== Suite 2: Sustained Throughput & TLP Sizing (n=3 interleaved, 1 engine) ===", flush=True)
        rate_arms = []
        for sz in ALL_SIZES:
            rate_arms.append((f"dsa_bar4_uc_{sz}B_w32", ["--mode", "rate", "--engine", "dsa", "--bar4", BAR4_UC, "--size", str(sz), "--window", "32", "--batch", "1", "--duration-ms", str(DURATION_MS)]))
            rate_arms.append((f"dsa_host_{sz}B_w32", ["--mode", "rate", "--engine", "dsa-host", "--size", str(sz), "--window", "32", "--batch", "1", "--duration-ms", str(DURATION_MS)]))
            rate_arms.append((f"movdir_bar4_uc_{sz}B_sf0", ["--mode", "rate", "--engine", "movdir", "--bar4", BAR4_UC, "--size", str(sz), "--window", "32", "--sfence-every", "0", "--duration-ms", str(DURATION_MS)]))
        for sz in [64, 512, 4096, 65536]:
            rate_arms.append((f"movdir_bar4_uc_{sz}B_sf1", ["--mode", "rate", "--engine", "movdir", "--bar4", BAR4_UC, "--size", str(sz), "--window", "32", "--sfence-every", "1", "--duration-ms", str(DURATION_MS)]))
        for rep in range(REPS):
            order = rate_arms if rep % 2 == 0 else list(reversed(rate_arms))
            for label, cli in order:
                r = run_one("rate_size", label, rep, cli)
                total_ok += 1
                if rep == 0 and ("dsa_bar4_uc_" in label or "movdir_bar4_uc_4096B" in label or "dsa_host_4096B" in label):
                    print(
                        f"  [r0] {label:26s}: {r['gb_s']:7.3f} GB/s ({r['mops_s']:6.3f} M/s) "
                        f"B/tlp={r['host_pmu']['bytes_per_tlp']:5.1f} tlp/op={r['host_pmu']['tlps_per_op']:6.2f} "
                        f"cyc/op={r['host_pmu']['cyc_per_op']:6.1f}",
                        flush=True,
                    )
            print(f"  rate_size rep {rep+1}/{REPS} done ({len(order)} runs)", flush=True)

        # ---- Suite 3A/3B/3C: Single-Variable Mechanism Controls (1 engine, n=3 interleaved) ----
        print("\n=== Suite 3A/3B/3C: UC vs WC, W-Sweep, Batch B=8 & Comp=BAR4 (1 engine, n=3) ===", flush=True)
        ctrl_arms = []
        # 3A: resource4_wc
        for sz in [64, 512, 4096, 65536]:
            ctrl_arms.append(("uc_vs_wc", f"dsa_bar4_wc_{sz}B_w32", ["--mode", "rate", "--engine", "dsa", "--bar4", BAR4_WC, "--size", str(sz), "--window", "32", "--batch", "1", "--duration-ms", str(DURATION_MS)]))
        # 3B: Concurrency W sweep at 64 B and 4 KiB
        for sz in [64, 4096]:
            for w in [1, 2, 4, 8, 16, 32, 48]:
                ctrl_arms.append(("w_sweep", f"dsa_bar4_{sz}B_w{w}", ["--mode", "rate", "--engine", "dsa", "--bar4", BAR4_UC, "--size", str(sz), "--window", str(w), "--batch", "1", "--duration-ms", str(DURATION_MS)]))
        # 3C: Batch B=8 (same W_ops=32 => W_sub=4, B=8) and CompTarget=BAR4 (W_sub=32, B=1)
        for sz in [64, 256, 512, 1024, 4096]:
            ctrl_arms.append(("batch_comp", f"dsa_bar4_{sz}B_b8_w4", ["--mode", "rate", "--engine", "dsa", "--bar4", BAR4_UC, "--size", str(sz), "--window", "4", "--batch", "8", "--duration-ms", str(DURATION_MS)]))
        for sz in [64, 512, 4096]:
            ctrl_arms.append(("batch_comp", f"dsa_bar4_{sz}B_compbar4_w32", ["--mode", "rate", "--engine", "dsa", "--bar4", BAR4_UC, "--comp-target", "bar4", "--size", str(sz), "--window", "32", "--batch", "1", "--duration-ms", str(DURATION_MS)]))
        for rep in range(REPS):
            order = ctrl_arms if rep % 2 == 0 else list(reversed(ctrl_arms))
            for suite, label, cli in order:
                r = run_one(suite, label, rep, cli)
                total_ok += 1
                if rep == 0 and suite in ("uc_vs_wc", "batch_comp"):
                    print(
                        f"  [r0] {suite:10s} {label:28s}: {r['gb_s']:7.3f} GB/s ({r['mops_s']:6.3f} M/s) "
                        f"B/tlp={r['host_pmu']['bytes_per_tlp']:5.1f} tlp/op={r['host_pmu']['tlps_per_op']:6.2f}",
                        flush=True,
                    )
            print(f"  controls rep {rep+1}/{REPS} done ({len(order)} runs)", flush=True)

        # ---- Suite 3D: Multi-Engine Scaling (2 engines and 4 engines via passwordless accel-config) ----
        print("\n=== Suite 3D: DSA Engine Scaling (2 vs 4 engines, n=3 interleaved) ===", flush=True)
        for n_eng in [2, 4]:
            set_dsa_engines(n_eng)
            print(f"  [ENGINES] switched dsa2/group2.0 to {n_eng} engines", flush=True)
            eng_arms = []
            for sz in [64, 256, 512, 1024, 4096, 65536]:
                eng_arms.append((f"dsa_bar4_e{n_eng}_{sz}B_b1_w32", ["--mode", "rate", "--engine", "dsa", "--bar4", BAR4_UC, "--size", str(sz), "--window", "32", "--batch", "1", "--duration-ms", str(DURATION_MS)]))
            for sz in [64, 256, 512, 4096]:
                eng_arms.append((f"dsa_bar4_e{n_eng}_{sz}B_b8_w4", ["--mode", "rate", "--engine", "dsa", "--bar4", BAR4_UC, "--size", str(sz), "--window", "4", "--batch", "8", "--duration-ms", str(DURATION_MS)]))
            for sz in [64, 4096]:
                eng_arms.append((f"dsa_host_e{n_eng}_{sz}B_b1_w32", ["--mode", "rate", "--engine", "dsa-host", "--size", str(sz), "--window", "32", "--batch", "1", "--duration-ms", str(DURATION_MS)]))
            for rep in range(REPS):
                order = eng_arms if rep % 2 == 0 else list(reversed(eng_arms))
                for label, cli in order:
                    r = run_one("engines", label, rep, cli)
                    total_ok += 1
                    if rep == 0:
                        print(
                            f"  [r0] {label:28s}: {r['gb_s']:7.3f} GB/s ({r['mops_s']:6.3f} M/s) "
                            f"B/tlp={r['host_pmu']['bytes_per_tlp']:5.1f}",
                            flush=True,
                        )
                print(f"  engines={n_eng} rep {rep+1}/{REPS} done ({len(order)} runs)", flush=True)

        env_doc["status"] = "completed"
        env_doc["accepted_runs"] = total_ok
        print(f"\n[DONE] All {total_ok} verified runs completed cleanly!", flush=True)

    finally:
        print("\n[RESTORE] Restoring dsa2 to 1 engine, BAR4 slot 15, and refl tmux...", flush=True)
        restore_notes = []
        try:
            set_dsa_engines(1)
            restore_notes.append("dsa2_engines=1")
        except Exception as e:
            restore_notes.append(f"dsa2_engines_err={e}")
        try:
            stop_card()
            out = subprocess.check_output(CARD_SSH + [f"{CARD_BIN} --restore"], text=True).strip()
            restore_notes.append(f"card_restore={out}")
        except Exception as e:
            restore_notes.append(f"card_restore_err={e}")
        try:
            pane = start_refl()
            restore_notes.append("refl=running")
            env_doc["refl_pane_tail"] = pane.splitlines()[-5:]
        except Exception as e:
            restore_notes.append(f"refl_err={e}")

        print("[RESTORE] Sleeping 21 s for delayed EDAC/AER check...", flush=True)
        time.sleep(21)
        env_doc["restore_notes"] = restore_notes
        env_doc["finished_utc"] = utc_now()
        try:
            env_doc["post"] = {
                "host": read_host_env(),
                "card": read_card_env(),
            }
        except Exception as e:
            env_doc["post_err"] = str(e)
        ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n")
        print(f"[RESTORE] Wrote {ENV_PATH}; summary: {restore_notes}", flush=True)


if __name__ == "__main__":
    main()
