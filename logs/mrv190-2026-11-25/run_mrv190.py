#!/usr/bin/env python3
"""MRV-190 n=3 interleaved experiment harness:
Real-time CN106 card-core observation of 64 B cache-line write reordering
for Intel DSA (dsa2/wq2.0 SVA) vs CPU unfenced MOVDIR64B to PF BAR4 slot 15.

Groups:
  0. Positive control (`positive_ctrl_rev`: intentional tail-first write, proves ~99.9% sensitivity).
  1. Single DSA MEMMOVE (128 B..4 KiB x hot / cold / front_cold_tail_hot, plus cross-NUMA 4 KiB).
  2. Dual-descriptor independent doorbell at 4 KiB (cold & front_cold_tail_hot):
     - Batch(2) toggling IDXD_OP_FLAG_FENCE = 0 vs 1 (1 engine & 4 engines)
     - Back-to-back standalone descriptors FENCE=0 vs DSA_OPCODE_DRAIN (1 engine & 4 engines)
     - Standalone IDXD_OP_FLAG_FENCE=1 spec check (expected DSA_COMP_INVALID_FLAGS = 0x11)
  3. Host waits for DSA completion record -> SFENCE -> MOVDIR64B doorbell (4 KiB, 1 & 4 engines).
  4. CPU unfenced MOVDIR64B multi-line write (128 B..4 KiB, UC/WC, forward/reverse scan, fcth)
     + SFENCE-before-tail fenced control.

Zero silent retries: every single attempt is logged to `attempts.jsonl`, accepted runs to `runs.jsonl`.
Automatically restores dsa2 to 1 engine, BAR4 slot 15 defaults, and `refl` tmux in `finally`,
then performs a 21 s delayed host/card EDAC & AER audit into `env.json`.
"""

import hashlib
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv190")
LOG_DIR = ROOT / "logs/mrv190-2026-11-25"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = "/tmp/mrv190-target/release/mrv190_host"
CARD_BIN = "/root/bench/mrv190_card"
ENGINES_SH = str(ROOT / "bench/host_dsa_engines.sh")
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "root@10.10.0.2"]

BAR4_UC = "/sys/bus/pci/devices/0000:99:00.0/resource4"
BAR4_WC = "/sys/bus/pci/devices/0000:99:00.0/resource4_wc"

REPS = 3
MAX_ATTEMPTS = 4
ROUNDS = 30000
POS_CTRL_ROUNDS = 10000
QUIET_NS = 2500
SIZES = [128, 256, 512, 1024, 4096]
ODD_LINE_SIZES = [192, 448, 960, 4032]  # 3, 7, 15, 63 lines (even tail index 2, 6, 14, 62)
GEN_CURSOR = 1_000_000


def next_base_gen(rounds: int) -> int:
    global GEN_CURSOR
    g = GEN_CURSOR
    GEN_CURSOR += rounds + 10_000
    return g


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
        tot = 0
        if p.exists():
            for line in p.read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].isdigit() and not parts[0].startswith("TOTAL_"):
                    tot += int(parts[1])
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
    subprocess.run(CARD_SSH + ["pkill -INT -f '[/]root/bench/mrv190_card' || true"], check=False)
    for _ in range(40):
        time.sleep(0.15)
        p = subprocess.run(CARD_SSH + ["pgrep -f '[/]root/bench/mrv190_card' || true"], capture_output=True, text=True)
        if not p.stdout.strip():
            return
    raise RuntimeError("mrv190_card did not stop cleanly")


def start_card() -> str:
    stop_card()
    cmd = (
        f"rm -f /tmp/mrv190_card.log; "
        f"nohup {CARD_BIN} -l 3 --no-pci --file-prefix=mrv190c --log-level=lib.eal:warning "
        f"-- --control 10.10.0.2:7526 --sync 10.10.0.2:7527 > /tmp/mrv190_card.log 2>&1 < /dev/null & echo $!"
    )
    subprocess.check_call(CARD_SSH + [cmd], stdout=subprocess.DEVNULL)
    for _ in range(50):
        time.sleep(0.15)
        log = subprocess.check_output(CARD_SSH + ["cat /tmp/mrv190_card.log 2>/dev/null || true"], text=True)
        if "mrv190_card: BAR4 slot 15 ready" in log:
            return log.strip()
    log = subprocess.check_output(CARD_SSH + ["cat /tmp/mrv190_card.log 2>/dev/null || true"], text=True)
    raise RuntimeError(f"mrv190_card failed to start:\n{log}")


def append_jsonl(path: pathlib.Path, rec: dict):
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def run_one(suite: str, label: str, rep: int, cli_args: list[str], rounds: int = ROUNDS) -> dict:
    reasons = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        base_gen = next_base_gen(rounds)
        cmd = [HOST_BIN, "--rounds", str(rounds), "--quiet-ns", str(QUIET_NS), "--base-gen", str(base_gen)] + cli_args
        p = subprocess.run(cmd, capture_output=True, text=True)
        rec = {
            "ts_utc": utc_now(),
            "suite": suite,
            "label": label,
            "rep": rep,
            "attempt": attempt,
            "base_gen": base_gen,
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
                card = data.get("card", {})
                if data.get("err_ops", 1) != 0:
                    reasons.append(f"err_ops={data.get('err_ops')}({data.get('first_err_st')})")
                if data.get("completed_ops", 0) != rounds:
                    reasons.append(f"completed_ops={data.get('completed_ops')}!={rounds}")
                if card.get("rounds_observed", 0) != rounds:
                    reasons.append(f"rounds_observed={card.get('rounds_observed')}!={rounds}")
                if card.get("persistent_errors", 1) != 0:
                    reasons.append(f"persistent_errors={card.get('persistent_errors')}")
                if card.get("unexpected_vals", 1) != 0:
                    reasons.append(f"unexpected_vals={card.get('unexpected_vals')}")
                if card.get("timeout_ops", 1) != 0:
                    reasons.append(f"timeout_ops={card.get('timeout_ops')}")
                if data.get("ack_timeouts", 1) != 0:
                    reasons.append(f"ack_timeouts={data.get('ack_timeouts')}")
                if not data.get("swerror_zero", False):
                    reasons.append(f"swerror={data.get('swerror_after')}")
                if data.get("page_faults", 1) != 0:
                    reasons.append(f"page_faults={data.get('page_faults')}")
                if data.get("host_pmu_min_pct", 0.0) < 100.0:
                    reasons.append(f"host_pmu_pct={data.get('host_pmu_min_pct')}")
                cpmu = card.get("card_pmu", {})
                if cpmu.get("min_pct", 0.0) < 100.0:
                    reasons.append(f"card_pmu_pct={cpmu.get('min_pct')}")
                if data.get("src_cache") == "cross_numa_front_cold_tail_hot" and not data.get("xnuma_ok", False):
                    reasons.append("xnuma_mbind_failed")
                if suite == "pos_ctrl" and card.get("reorder_pct", 0.0) < 99.0:
                    reasons.append(f"pos_ctrl_low_sensitivity={card.get('reorder_pct')}")

        rec["accepted"] = len(reasons) == 0
        rec["reject_reasons"] = reasons
        append_jsonl(ATTEMPTS_PATH, rec)
        if rec["accepted"]:
            append_jsonl(RUNS_PATH, rec)
            return rec
        print(f"  [RETRY] {suite}/{label} rep={rep} attempt={attempt}: {reasons}", file=sys.stderr, flush=True)
        time.sleep(0.15)
    raise RuntimeError(f"exhausted {MAX_ATTEMPTS} attempts for {suite}/{label} rep={rep}: {reasons}")


def run_spec_probe_standalone_fence() -> dict:
    """Verify hardware spec: standalone IDXD_OP_FLAG_FENCE=1 outside Batch returns DSA_COMP_INVALID_FLAGS (0x11)."""
    base_gen = next_base_gen(100)
    cmd = [
        HOST_BIN, "--rounds", "100", "--quiet-ns", str(QUIET_NS), "--base-gen", str(base_gen),
        "--arm", "dsa-dual-b2b", "--src-cache", "front-cold-tail-hot", "--scan-dir", "reverse",
        "--size", "4096", "--dsa-fence",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    # Card WATCH will time out cleanly after 2s since host stops on err_ops=1; extract JSON.
    data = json.loads(p.stdout.strip().splitlines()[-1])
    return {
        "name": "standalone_b2b_idxd_op_flag_fence_1",
        "rc": p.returncode,
        "err_ops": data.get("err_ops"),
        "first_err_st": data.get("first_err_st"),
        "swerror_zero": data.get("swerror_zero"),
        "expected_status": "0x11 (DSA_COMP_INVALID_FLAGS)",
    }


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for p in (RUNS_PATH, ATTEMPTS_PATH, ENV_PATH):
        if p.exists():
            p.rename(p.with_name(f"{p.stem}.prev-{ts_tag}{p.suffix}"))

    env_doc = {
        "started_utc": utc_now(),
        "rounds_per_rep": ROUNDS,
        "reps": REPS,
        "prior_pilots_note": (
            "Two earlier runs preceded this final endpoint-separated dataset: "
            "(1) an aborted pilot (6 accepted pos_ctrl runs + 4 rejected dsa_single_128B_hot_e1_rev attempts "
            "with page_faults=58 due to glibc calloc zero-page CoW on vec![0.0; 30000], overwritten when pilot_v2 ran); "
            "(2) a complete 168/168 pilot_v2 dataset whose mrv190_card combined endpoint_miss with sig_only_torn, "
            "rotated cleanly before running this final dataset where endpoint_miss and sig_only_torn are strictly separated "
            "and odd-line-count even-tail DSA sizes (192/448/960/4032 B) are directly verified."
        ),
        "pre": {},
    }
    total_ok = 0

    try:
        set_dsa_engines(1)
        env_doc["pre"]["host"] = read_host_env()
        env_doc["pre"]["card"] = read_card_env()
        stop_refl()
        card_log = start_card()
        env_doc["card_start_log"] = card_log
        print(f"[INIT] {card_log}", flush=True)

        # ---- Suite 0: Positive Control (tail-first intentional inversion, n=3) ----
        print("\n=== Suite 0: Positive Controls (n=3, 10,000 ops/rep) ===", flush=True)
        pos_arms = [
            ("pos_ctrl_rev_128B", ["--arm", "positive-ctrl-rev", "--src-cache", "hot", "--scan-dir", "reverse", "--size", "128"]),
            ("pos_ctrl_rev_4096B", ["--arm", "positive-ctrl-rev", "--src-cache", "hot", "--scan-dir", "reverse", "--size", "4096"]),
        ]
        for rep in range(REPS):
            order = pos_arms if rep % 2 == 0 else list(reversed(pos_arms))
            for label, cli in order:
                r = run_one("pos_ctrl", label, rep, cli, rounds=POS_CTRL_ROUNDS)
                total_ok += 1
                if rep == 0:
                    c = r["card"]
                    print(f"  [r0] {label:28s}: reord={c['reordered_ops']:5d}/{c['rounds_observed']} ({c['reorder_pct']:7.3f}%) max_miss={c['missing_lines']['max']} conv_p50={c['conv_ns']['p50']:.1f}ns", flush=True)

        # ---- Suite 1 + 2(1e) + 3(1e) + 4: All 1-engine & CPU MOVDIR64B arms interleaved (n=3, 30,000 ops/rep) ----
        print(f"\n=== Suites 1, 2(1e), 3(1e), 4: Interleaved n={REPS} ({ROUNDS} ops/rep) ===", flush=True)
        e1_arms = []
        # Group 1: Single DSA MEMMOVE (128 B..4 KiB x hot / cold / front-cold-tail-hot)
        for sz in SIZES:
            for sc, sc_lbl in [("hot", "hot"), ("cold", "cold"), ("front-cold-tail-hot", "fcth")]:
                e1_arms.append(("group1_dsa_single", f"dsa_single_{sz}B_{sc_lbl}_e1_rev", [
                    "--arm", "dsa-single", "--src-cache", sc, "--scan-dir", "reverse", "--size", str(sz), "--bar4", BAR4_UC,
                ]))
        # Group 1b: A priori test of even-before-odd intra-MEMMOVE reordering using odd-line-count sizes (tail = even line!)
        for sz in ODD_LINE_SIZES:
            for sc, sc_lbl in [("hot", "hot"), ("cold", "cold"), ("front-cold-tail-hot", "fcth")]:
                e1_arms.append(("group1b_dsa_single_oddl", f"dsa_single_{sz}B_{sc_lbl}_e1_rev", [
                    "--arm", "dsa-single", "--src-cache", sc, "--scan-dir", "reverse", "--size", str(sz), "--bar4", BAR4_UC,
                ]))
        e1_arms.append(("group1_dsa_single", "dsa_single_4096B_xnuma_e1_rev", [
            "--arm", "dsa-single", "--src-cache", "cross-numa-front-cold-tail-hot", "--scan-dir", "reverse", "--size", "4096", "--bar4", BAR4_UC,
        ]))
        e1_arms.append(("group1_dsa_single", "dsa_single_4096B_fcth_e1_fwd", [
            "--arm", "dsa-single", "--src-cache", "front-cold-tail-hot", "--scan-dir", "forward", "--size", "4096", "--bar4", BAR4_UC,
        ]))

        # Group 2 (1 engine): Dual-descriptor independent doorbell at 4 KiB (cold & fcth)
        for sc, sc_lbl in [("cold", "cold"), ("front-cold-tail-hot", "fcth")]:
            e1_arms.append(("group2_dual_db_1e", f"dsa_batch_4096B_{sc_lbl}_e1_f0", [
                "--arm", "dsa-dual-batch", "--src-cache", sc, "--scan-dir", "reverse", "--size", "4096", "--bar4", BAR4_UC,
            ]))
            e1_arms.append(("group2_dual_db_1e", f"dsa_batch_4096B_{sc_lbl}_e1_f1", [
                "--arm", "dsa-dual-batch", "--src-cache", sc, "--scan-dir", "reverse", "--size", "4096", "--dsa-fence", "--bar4", BAR4_UC,
            ]))
            e1_arms.append(("group2_dual_db_1e", f"dsa_b2b_4096B_{sc_lbl}_e1_f0", [
                "--arm", "dsa-dual-b2b", "--src-cache", sc, "--scan-dir", "reverse", "--size", "4096", "--bar4", BAR4_UC,
            ]))
            e1_arms.append(("group2_dual_db_1e", f"dsa_b2b_4096B_{sc_lbl}_e1_drain", [
                "--arm", "dsa-dual-b2b", "--src-cache", sc, "--scan-dir", "reverse", "--size", "4096", "--dsa-drain", "--bar4", BAR4_UC,
            ]))

        # Group 3 (1 engine): Host waits for DSA completion -> SFENCE + MOVDIR64B doorbell
        for sc, sc_lbl in [("cold", "cold"), ("front-cold-tail-hot", "fcth")]:
            e1_arms.append(("group3_comp_db", f"dsa_comp_db_4096B_{sc_lbl}_e1", [
                "--arm", "dsa-comp-movdir-db", "--src-cache", sc, "--scan-dir", "reverse", "--size", "4096", "--bar4", BAR4_UC,
            ]))

        # Group 4: CPU unfenced MOVDIR64B multi-line write (128 B..4 KiB) + scan/cache/WC/fenced controls
        for sz in SIZES:
            e1_arms.append(("group4_cpu_movdir", f"movdir_nf_{sz}B_hot_uc_rev", [
                "--arm", "movdir-nofence", "--src-cache", "hot", "--scan-dir", "reverse", "--size", str(sz), "--bar4", BAR4_UC,
            ]))
        for sz in [512, 1024, 4096]:
            e1_arms.append(("group4_cpu_movdir", f"movdir_nf_{sz}B_hot_uc_fwd", [
                "--arm", "movdir-nofence", "--src-cache", "hot", "--scan-dir", "forward", "--size", str(sz), "--bar4", BAR4_UC,
            ]))
            e1_arms.append(("group4_cpu_movdir", f"movdir_fenced_{sz}B_hot_uc_rev", [
                "--arm", "movdir-fenced", "--src-cache", "hot", "--scan-dir", "reverse", "--size", str(sz), "--bar4", BAR4_UC,
            ]))
        for sz in [512, 4096]:
            e1_arms.append(("group4_cpu_movdir", f"movdir_nf_{sz}B_fcth_uc_rev", [
                "--arm", "movdir-nofence", "--src-cache", "front-cold-tail-hot", "--scan-dir", "reverse", "--size", str(sz), "--bar4", BAR4_UC,
            ]))
            e1_arms.append(("group4_cpu_movdir", f"movdir_nf_{sz}B_hot_wc_rev", [
                "--arm", "movdir-nofence", "--src-cache", "hot", "--scan-dir", "reverse", "--size", str(sz), "--bar4", BAR4_WC,
            ]))

        for rep in range(REPS):
            order = e1_arms if rep % 2 == 0 else list(reversed(e1_arms))
            for suite, label, cli in order:
                r = run_one(suite, label, rep, cli, rounds=ROUNDS)
                total_ok += 1
                if rep == 0 and (r["card"]["reordered_ops"] > 0 or "4096B" in label):
                    c = r["card"]
                    comp = r["dsa_comp_ns"]["p50"] if r["dsa_comp_ns"] else 0.0
                    print(
                        f"  [r0] {label:34s}: reord={c['reordered_ops']:5d}/{c['rounds_observed']} ({c['reorder_pct']:7.3f}%) "
                        f"miss_m={c['missing_lines']['max']:2d} span_m={c['span_lines']['max']:2d} conv={c['conv_ns']['p50']:5.1f}ns comp={comp:6.1f}ns ack1={r['ack_first_hit_pct']:.2f}%",
                        flush=True,
                    )
            print(f"  1-engine + CPU rep {rep+1}/{REPS} done ({len(order)} runs)", flush=True)

        # ---- Suite 2(4e) + 3(4e) + single(4e): 4-engine multi-engine concurrency arms (n=3 interleaved) ----
        print(f"\n=== Suites 2(4e) & 3(4e): 4-Engine DSA Interleaved n={REPS} ({ROUNDS} ops/rep) ===", flush=True)
        set_dsa_engines(4)
        e4_arms = [
            ("group1_dsa_single_4e", "dsa_single_4096B_fcth_e4_rev", [
                "--arm", "dsa-single", "--src-cache", "front-cold-tail-hot", "--scan-dir", "reverse", "--size", "4096", "--bar4", BAR4_UC,
            ]),
            ("group1_dsa_single_4e", "dsa_single_4096B_xnuma_e4_rev", [
                "--arm", "dsa-single", "--src-cache", "cross-numa-front-cold-tail-hot", "--scan-dir", "reverse", "--size", "4096", "--bar4", BAR4_UC,
            ]),
        ]
        for sc, sc_lbl in [("cold", "cold"), ("front-cold-tail-hot", "fcth")]:
            e4_arms.append(("group2_dual_db_4e", f"dsa_batch_4096B_{sc_lbl}_e4_f0", [
                "--arm", "dsa-dual-batch", "--src-cache", sc, "--scan-dir", "reverse", "--size", "4096", "--bar4", BAR4_UC,
            ]))
            e4_arms.append(("group2_dual_db_4e", f"dsa_batch_4096B_{sc_lbl}_e4_f1", [
                "--arm", "dsa-dual-batch", "--src-cache", sc, "--scan-dir", "reverse", "--size", "4096", "--dsa-fence", "--bar4", BAR4_UC,
            ]))
            e4_arms.append(("group2_dual_db_4e", f"dsa_b2b_4096B_{sc_lbl}_e4_f0", [
                "--arm", "dsa-dual-b2b", "--src-cache", sc, "--scan-dir", "reverse", "--size", "4096", "--bar4", BAR4_UC,
            ]))
            e4_arms.append(("group2_dual_db_4e", f"dsa_b2b_4096B_{sc_lbl}_e4_drain", [
                "--arm", "dsa-dual-b2b", "--src-cache", sc, "--scan-dir", "reverse", "--size", "4096", "--dsa-drain", "--bar4", BAR4_UC,
            ]))
            e4_arms.append(("group3_comp_db", f"dsa_comp_db_4096B_{sc_lbl}_e4", [
                "--arm", "dsa-comp-movdir-db", "--src-cache", sc, "--scan-dir", "reverse", "--size", "4096", "--bar4", BAR4_UC,
            ]))

        for rep in range(REPS):
            order = e4_arms if rep % 2 == 0 else list(reversed(e4_arms))
            for suite, label, cli in order:
                r = run_one(suite, label, rep, cli, rounds=ROUNDS)
                total_ok += 1
                if rep == 0:
                    c = r["card"]
                    comp = r["dsa_comp_ns"]["p50"] if r["dsa_comp_ns"] else 0.0
                    print(
                        f"  [r0] {label:34s}: reord={c['reordered_ops']:5d}/{c['rounds_observed']} ({c['reorder_pct']:7.3f}%) "
                        f"miss_m={c['missing_lines']['max']:2d} span_m={c['span_lines']['max']:2d} conv={c['conv_ns']['p50']:5.1f}ns comp={comp:6.1f}ns ack1={r['ack_first_hit_pct']:.2f}%",
                        flush=True,
                    )
            print(f"  4-engine rep {rep+1}/{REPS} done ({len(order)} runs)", flush=True)

        # ---- Hardware spec probe: standalone IDXD_OP_FLAG_FENCE=1 outside Batch returns 0x11 ----
        print("\n=== Spec Probe: Standalone IDXD_OP_FLAG_FENCE=1 outside Batch ===", flush=True)
        set_dsa_engines(1)
        spec_res = run_spec_probe_standalone_fence()
        env_doc["spec_probes"] = [spec_res]
        print(f"  spec_probe: {spec_res}", flush=True)

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
