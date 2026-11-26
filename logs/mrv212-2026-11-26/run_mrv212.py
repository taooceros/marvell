#!/usr/bin/env python3
"""
MRV-212 (E1: NO-SFENCE BAR4 MMIO credit signaling) full interleaved n=3 runner.

Controls covered (strictly on Socket 1 cpu32..63; zero Socket-0 interference):
- A: 5-tier credit signaling CPU cost (calibrate-empty-rx, micro-signal body=512 & 32,
     rate-matched rx-stream 1500B@5Mpps & 64B@15Mpps with --app-work, max-rate 64B),
     direct control-loop RTT tau_rtt & 100GbE BDP lower bound (64B & 1500B),
     and zero-load W=1 ping-pong latency increment (B=32 & B=1, 64B & 1500B).
- B: Socket-1 neighbor interference across 4 victims (llc-chase K=32, dram-chase K=1 & K=8,
     stream-triad 6c): idle, l1-spin (1c/8c), realistic NO-SFENCE credit rates
     (1c@0.5/3.5/7.0 Mpps, 4c@7.0 Mpps, 8c@7.0 Mpps, lines=1), plus MRV-196 Opus 5.5 audit
     1c unthrottled SFENCE decoupling ladder (sfence_batch=512/2048/8192/0, lines=4096).
- C: Static invariant-IOVA ring (pool=512 reverse LIFO) vs dynamic mbuf rebind + poison
     (aligned post-refill B=64 and unaligned B=32), 2 s long runs at full speed (64B & 1500B).
- D: Core ablation of native SDP SLIST_DBELL/WMARK backpressure + card elastic FIFO vs
     explicit NO-SFENCE BAR4 credit gating:
     D1 single-queue (1500B@5Mpps & 64B@15Mpps, 1ms/10ms pause, strict fifo_full==0)
     D2 dual-queue (Q0 healthy 3.0 Mpps + Q1 8ms/10ms paused 1.5 Mpps, independent card pools,
     strict q0_fifo_full==0 && q1_fifo_full==0).

Every attempt (pass or fail) is recorded in attempts.jsonl; gate-passing runs go into runs.jsonl.
Restores card/host baseline (BAR4 slot 15, refl tmux, eth0 100G UP, pem_wc unloaded, clocks)
and waits 22 s for post-run EDAC/AER zero-delta audit into env.json.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent
ATTEMPTS_PATH = LOG_DIR / "attempts.jsonl"
RUNS_PATH = LOG_DIR / "runs.jsonl"
ENV_PATH = LOG_DIR / "env.json"

HOST_BIN = "/tmp/mrv212-target/release/mrv212_host"
INTERF_BIN = "/tmp/mrv212-target/release/mrv212_interf"
CARD_BIN_LOCAL = "/tmp/mrv212-target/aarch64-unknown-linux-gnu/release/mrv212_card"
CARD_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@10.10.0.2"]

EAL_1Q = [
    HOST_BIN,
    "-l", "40",
    "-a", "0000:99:02.0,ism_enable=1,allow_small_oq=1",
    "--file-prefix=mrv212h",
    "--log-level=lib.eal:warning",
    "--",
]
EAL_2Q = [
    HOST_BIN,
    "-l", "40,41",
    "-a", "0000:99:02.0,ism_enable=1,allow_small_oq=1",
    "--file-prefix=mrv212h",
    "--log-level=lib.eal:warning",
    "--",
]

REPS = 3
MAX_ATTEMPTS_PER_RUN = 3
RUN_GEN = 1000


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sh(cmd: str, check: bool = True) -> str:
    r = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed ({r.returncode}): {cmd}\nstderr: {r.stderr}")
    return r.stdout.strip()


def card_sh(cmd: str, check: bool = True) -> str:
    r = subprocess.run(CARD_SSH + [cmd], text=True, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"card cmd failed ({r.returncode}): {cmd}\nstderr: {r.stderr}")
    return r.stdout.strip()


def collect_hw_audit() -> dict:
    host_edac = {}
    for p in sorted(Path("/sys/devices/system/edac/mc").glob("mc*/ue_count")):
        host_edac[p.parent.name] = int(p.read_text().strip())

    def parse_aer_total(path: str) -> int:
        txt = Path(path).read_text()
        for line in txt.splitlines():
            if line.startswith("TOTAL_ERR_"):
                return int(line.split()[-1])
        return -1

    rp_aer = {
        "cor": parse_aer_total("/sys/bus/pci/devices/0000:98:01.0/aer_dev_correctable"),
        "nonfatal": parse_aer_total("/sys/bus/pci/devices/0000:98:01.0/aer_dev_nonfatal"),
        "fatal": parse_aer_total("/sys/bus/pci/devices/0000:98:01.0/aer_dev_fatal"),
    }
    uncore = {}
    for p in sorted(Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz")):
        uncore[p.parent.name] = int(p.read_text().strip())

    cpu40_gov = Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    cpu40_khz = int(Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_cur_freq").read_text().strip())

    card_raw = card_sh(
        "echo ETH0=$(ip -br link show eth0 | awk '{print $2}'); "
        "echo ETH0_SPEED=$(ethtool eth0 2>/dev/null | awk '/Speed:/{print $2}'); "
        "echo PEM_WC=$(lsmod | grep -c '^pem_wc ' || true); "
        "echo REFL=$(tmux has-session -t refl 2>/dev/null && echo running || echo stopped); "
        "for f in /sys/devices/system/edac/mc/mc0/ue_count "
        "/sys/devices/system/edac/llc/llc0/ue_count "
        "/sys/devices/system/edac/mdc/mdc0/ue_count "
        "/sys/devices/system/edac/gic/gic0/ue_count "
        "/sys/devices/system/edac/ea/ea0/ue_count; do "
        "echo EDAC_$(basename $(dirname $f))=$(cat $f); done; "
        "echo EDAC_CPU_SUM=$(awk '{s+=$1} END{print s+0}' /sys/devices/system/edac/cpu/cpu*/ue_count)"
    )
    card_info = {}
    for line in card_raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            card_info[k] = int(v) if v.isdigit() else v

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host_edac_ue": host_edac,
        "host_rp_aer": rp_aer,
        "host_uncore_khz": uncore,
        "host_cpu40_gov": cpu40_gov,
        "host_cpu40_khz": cpu40_khz,
        "card": card_info,
    }


def stop_card_server() -> None:
    card_sh(
        "pkill -INT -f '[/]root/bench/mrv212_card' 2>/dev/null || true; "
        "for i in $(seq 1 30); do pgrep -f '[/]root/bench/mrv212_card' >/dev/null || break; sleep 0.2; done; "
        "pkill -9 -f '[/]root/bench/mrv212_card' 2>/dev/null || true; "
        "rm -f /dev/hugepages/mrv212cmap_*",
        check=False,
    )


def start_card_server(queues: int, with_dpi: bool, pool_size: int = 131071) -> None:
    stop_card_server()
    card_sh("tmux send-keys -t refl C-c 2>/dev/null || true; tmux kill-session -t refl 2>/dev/null || true", check=False)
    dpi_eal = "-a 0000:06:00.1 " if with_dpi else ""
    dpi_app = "--dpi-vf 0000:06:00.1 " if with_dpi else ""
    cmd = (
        f"rm -f /tmp/mrv212_card.log /dev/hugepages/mrv212cmap_*; "
        f"nohup /root/bench/mrv212_card -l 3,4 -a 0002:01:00.2 {dpi_eal}"
        f"--file-prefix=mrv212c --log-level=lib.eal:warning -- "
        f"--queues {queues} --tx-desc 512 --rx-desc 512 --pool-size {pool_size} {dpi_app}"
        f"> /tmp/mrv212_card.log 2>&1 < /dev/null & echo $!"
    )
    pid = card_sh(cmd)
    for _ in range(40):
        time.sleep(0.25)
        out = card_sh("cat /tmp/mrv212_card.log 2>/dev/null || true", check=False)
        if "mrv212_card BAR4 slot 15 ready" in out:
            log(f"Card server started (pid={pid}, queues={queues}, dpi={with_dpi}, pool={pool_size})")
            return
    out = card_sh("cat /tmp/mrv212_card.log 2>/dev/null || true", check=False)
    raise RuntimeError(f"mrv212_card failed to become ready:\n{out}")


def restore_baseline_and_audit() -> dict:
    log("Restoring card/host baseline...")
    stop_card_server()
    restore_out = card_sh("/root/bench/mrv212_card --restore")
    log(f"Card --restore: {restore_out}")
    card_sh(
        "if lsmod | grep -q '^pem_wc '; then rmmod pem_wc; fi; "
        "tmux kill-session -t refl 2>/dev/null || true; "
        "pkill -INT -f '[/]root/bench/reflector-rs' 2>/dev/null || true; sleep 0.3; "
        "tmux new-session -d -s refl /root/bench/reflector-rs -l 3 -a 0002:01:00.2 --file-prefix=refl"
    )
    refl_ready = False
    for _ in range(30):
        time.sleep(0.25)
        pane = card_sh("tmux capture-pane -t refl -p 2>/dev/null || true", check=False)
        if "reflector running on port 0" in pane:
            refl_ready = True
            break
    if not refl_ready:
        pane = card_sh("tmux capture-pane -t refl -p 2>/dev/null || true", check=False)
        raise RuntimeError(f"refl tmux did not reach ready state:\n{pane}")
    log("Card refl tmux restored (reflector running on port 0). Sleeping 22 s for EDAC/AER audit...")
    time.sleep(22)
    post = collect_hw_audit()
    post["card_restore_msg"] = restore_out
    return post


def append_jsonl(path: Path, obj: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, separators=(",", ":")) + "\n")


def check_gate(group: str, rec: dict, expect_lossless: bool, expect_fifo: bool = False) -> list[str]:
    errs = []
    if group == "B":
        if rec.get("host_pmu_min_pct", 0.0) < 100.0:
            errs.append(f"host_pmu_min_pct={rec.get('host_pmu_min_pct')}")
        if rec.get("card_pmu", {}).get("min_pct", 0.0) < 100.0:
            errs.append(f"card_pmu_min_pct={rec.get('card_pmu', {}).get('min_pct')}")
        if rec.get("aggr_page_faults", 0) != 0:
            errs.append(f"aggr_pf={rec.get('aggr_page_faults')}")
        v = rec.get("victim_res", {})
        if v.get("page_faults", 0) != 0:
            errs.append(f"victim_pf={v.get('page_faults')}")
        if v.get("bad_nodes", 0) != 0 or v.get("verify_errors", 0) != 0:
            errs.append(f"victim_verify_err={v}")
        ver = rec.get("verify", {})
        if ver.get("bad", 0) != 0 or ver.get("torn", 0) != 0 or ver.get("zero", 0) != 0:
            errs.append(f"bar4_verify={ver}")
        return errs

    h_pmu = rec.get("host_pmu", {})
    if h_pmu.get("min_pct", 0.0) < 100.0:
        errs.append(f"host_pmu_min_pct={h_pmu.get('min_pct')}")
    if "card_pmu" in rec and rec["card_pmu"]:
        if rec["card_pmu"].get("min_pct", 0.0) < 100.0:
            errs.append(f"card_pmu_min_pct={rec['card_pmu'].get('min_pct')}")

    mode = rec.get("mode")
    if mode == "dual-queue":
        if rec.get("q0_page_faults", 0) != 0 or rec.get("q1_page_faults", 0) != 0:
            errs.append(f"pf=({rec.get('q0_page_faults')},{rec.get('q1_page_faults')})")
        if rec.get("q0_bad", 0) != 0 or rec.get("q0_torn", 0) != 0 or rec.get("q1_bad", 0) != 0 or rec.get("q1_torn", 0) != 0:
            errs.append("bad_or_torn")
        if rec.get("q0_lost", 0) != 0 or rec.get("q0_ooo", 0) != 0:
            errs.append(f"q0_loss=({rec.get('q0_lost')},{rec.get('q0_ooo')})")
        if expect_lossless and (rec.get("q1_lost", 0) != 0 or rec.get("q1_ooo", 0) != 0 or rec.get("q1_meas_seq_gap_lost", 0) != 0):
            errs.append(f"q1_loss=({rec.get('q1_lost')},{rec.get('q1_ooo')},{rec.get('q1_meas_seq_gap_lost')})")
        cs = rec.get("card_stats", {})
        if cs.get("credit_torn", 0) != 0 or cs.get("alloc_fail", 0) != 0:
            errs.append(f"card_err=({cs.get('credit_torn')},{cs.get('alloc_fail')})")
        if expect_fifo and (cs.get("q0_fifo_full", 0) != 0 or cs.get("q1_fifo_full", 0) != 0):
            errs.append(f"fifo_full=({cs.get('q0_fifo_full')},{cs.get('q1_fifo_full')})")
        return errs

    if rec.get("page_faults", 0) != 0:
        errs.append(f"pf={rec.get('page_faults')}")
    if rec.get("bad", 0) != 0 or rec.get("torn", 0) != 0 or rec.get("total_bad", 0) != 0 or rec.get("total_torn", 0) != 0:
        errs.append("bad_or_torn")
    cs = rec.get("card_stats", {})
    if cs:
        if cs.get("credit_torn", 0) != 0 or cs.get("alloc_fail", 0) != 0 or cs.get("dpi_failed", 0) != 0:
            errs.append(f"card_err=(torn={cs.get('credit_torn')},alloc={cs.get('alloc_fail')},dpi={cs.get('dpi_failed')})")
        if expect_fifo and (cs.get("q0_fifo_full", 0) != 0 or cs.get("q1_fifo_full", 0) != 0):
            errs.append(f"fifo_full={cs.get('q0_fifo_full')}")
    if mode in ("credit-rtt", "pingpong"):
        if rec.get("ooo", 0) != 0 or rec.get("lost", 0) != 0:
            errs.append(f"ooo={rec.get('ooo')},lost={rec.get('lost')}")
    elif mode == "rx-stream":
        if expect_lossless:
            if rec.get("lost", 0) != 0 or rec.get("meas_seq_gap_lost", 0) != 0 or rec.get("ooo", 0) != 0 or rec.get("total_ooo", 0) != 0:
                errs.append(
                    f"loss=(lost={rec.get('lost')},m_gap={rec.get('meas_seq_gap_lost')},ooo={rec.get('ooo')},tot_ooo={rec.get('total_ooo')})"
                )
    return errs


DONE_RUNS: dict[tuple[str, str, int], dict] = {}


def exec_run(
    group: str,
    tag: str,
    rep: int,
    cmd: list[str],
    expect_lossless: bool,
    expect_fifo: bool = False,
) -> dict:
    global RUN_GEN
    key = (group, tag, rep)
    if key in DONE_RUNS:
        return DONE_RUNS[key]

    for attempt in range(1, MAX_ATTEMPTS_PER_RUN + 1):
        RUN_GEN += 1
        full_cmd = list(cmd)
        if "--run-gen" in full_cmd:
            idx = full_cmd.index("--run-gen")
            full_cmd[idx + 1] = str(RUN_GEN)
        elif "--run-tag" in full_cmd:
            idx = full_cmd.index("--run-tag")
            full_cmd[idx + 1] = str(RUN_GEN)

        t0 = time.monotonic()
        r = subprocess.run(full_cmd, text=True, capture_output=True)
        dt = time.monotonic() - t0
        raw_lines = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")]
        if r.returncode != 0 or not raw_lines:
            entry = {
                "group": group,
                "tag": tag,
                "rep": rep,
                "attempt": attempt,
                "run_gen": RUN_GEN,
                "ok": False,
                "rc": r.returncode,
                "wall_s": round(dt, 3),
                "gate_errors": [f"exec_failed(rc={r.returncode})"],
                "stderr_tail": r.stderr[-400:],
            }
            append_jsonl(ATTEMPTS_PATH, entry)
            log(f"  [FAIL] {group}/{tag} rep={rep} att={attempt} rc={r.returncode}: {r.stderr.strip()[-180:]}")
            time.sleep(0.5)
            continue

        rec = json.loads(raw_lines[-1])
        errs = check_gate(group, rec, expect_lossless, expect_fifo)
        wrapped = {
            "group": group,
            "tag": tag,
            "rep": rep,
            "attempt": attempt,
            "run_gen": RUN_GEN,
            "ok": len(errs) == 0,
            "gate_errors": errs,
            "wall_s": round(dt, 3),
            "data": rec,
        }
        append_jsonl(ATTEMPTS_PATH, wrapped)
        if not errs:
            append_jsonl(RUNS_PATH, wrapped)
            DONE_RUNS[key] = rec
            return rec
        log(f"  [RETRY] {group}/{tag} rep={rep} att={attempt} gate={errs}")
        time.sleep(0.3)

    raise RuntimeError(f"Exhausted {MAX_ATTEMPTS_PER_RUN} attempts for {group}/{tag} rep={rep}")


def main() -> None:
    global RUN_GEN
    resume = "--resume" in sys.argv[1:]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if resume and RUNS_PATH.exists():
        for line in RUNS_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            w = json.loads(line)
            DONE_RUNS[(w["group"], w["tag"], w["rep"])] = w["data"]
            RUN_GEN = max(RUN_GEN, int(w.get("run_gen", 1000)) + 10)
        log(f"Resuming with {len(DONE_RUNS)} already-passed runs from {RUNS_PATH} (next RUN_GEN>{RUN_GEN})")
    else:
        for p in (ATTEMPTS_PATH, RUNS_PATH):
            if p.exists():
                p.unlink()

    pre_env = collect_hw_audit()
    env_doc = {
        "issue": "MRV-212",
        "workspace": "/home/hongtao/dev/marvell-mrv212",
        "jj_code_parent": sh("cd /home/hongtao/dev/marvell-mrv212 && jj log -r '@-' --no-graph -T 'change_id.short() ++ \" \" ++ commit_id.short() ++ \" \" ++ description.first_line()'"),
        "host_kernel": sh("uname -r"),
        "card_kernel": card_sh("uname -r"),
        "binaries_sha256": {
            "mrv212_host": sha256_file(HOST_BIN),
            "mrv212_interf": sha256_file(INTERF_BIN),
            "mrv212_card": sha256_file(CARD_BIN_LOCAL),
        },
        "pre_audit": pre_env,
    }
    ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n")
    log(f"Pre-audit saved. Host EDAC={pre_env['host_edac_ue']}, AER={pre_env['host_rp_aer']}, Card={pre_env['card']}")

    t_campaign = time.monotonic()
    try:
        # =====================================================================
        # PHASE 1: Single-queue card server (Controls A, B, C, D1)
        # =====================================================================
        start_card_server(queues=1, with_dpi=True, pool_size=131071)

        # ---- Control A0-cal: Calibrate empty SDP RX + app_work_step (n=3, matched to A1 rx_desc=2048) ----
        log("=== Control A0-cal: calibrate-empty-rx --app-work (n=3) ===")
        cal_vals = []
        for rep in range(REPS):
            rec = exec_run(
                "A0_cal", "calibrate_empty_rx_app_work", rep,
                EAL_1Q + ["--mode", "calibrate-empty-rx", "--rx-desc", "2048", "--pool-size", "4095", "--app-work", "--duration-ms", "1000"],
                expect_lossless=True,
            )
            cal_vals.append(rec["cycles_per_poll"])
            log(f"  cal rep={rep}: {rec['cycles_per_poll']:.4f} cyc/poll ({rec['ns_per_poll']:.3f} ns/poll)")
        cal_vals_sorted = sorted(cal_vals)
        work_cyc_per_poll = cal_vals_sorted[len(cal_vals_sorted) // 2]
        env_doc["calibrated_empty_rx_app_work_cycles_per_poll"] = round(work_cyc_per_poll, 4)
        log(f"  -> Median empty-rx+app_work = {work_cyc_per_poll:.4f} cyc/poll")

        # ---- Control A: 5-tier micro-signal, rate-matched rx-stream, credit-rtt, ping-pong (n=3 interleaved) ----
        credit_modes_5 = ["off", "bar4-nofence", "bar4-sfence", "bar4-uc64", "hostmem-dpi"]
        credit_modes_4 = ["bar4-nofence", "bar4-sfence", "bar4-uc64", "hostmem-dpi"]

        log("=== Control A: 5-tier CPU cost, tau_rtt/BDP, and W=1 ping-pong (n=3 interleaved) ===")
        for rep in range(REPS):
            order5 = credit_modes_5 if rep % 2 == 0 else list(reversed(credit_modes_5))
            order4 = credit_modes_4 if rep % 2 == 0 else list(reversed(credit_modes_4))

            # A0 micro-signal (body=512 realistic B=32 spacing + body=32 CHA serialization diagnostic)
            for body in (512, 32):
                for cm in order5:
                    rec = exec_run(
                        "A0_micro", f"micro_b{body}_{cm}", rep,
                        EAL_1Q + [
                            "--mode", "micro-signal", "--credit", cm,
                            "--body-iters", str(body), "--credit-batch", "32",
                            "--rounds", "200000", "--run-gen", "0",
                        ],
                        expect_lossless=True,
                    )
                    log(f"  A0 rep={rep} body={body:3d} {cm:12s}: {rec['cycles_per_batch']:7.2f} cyc/b, sb={rec['sb_stalls_per_batch']:6.2f}")

            # A1 rate-matched rx-stream with --app-work (1500B@5.0Mpps & 64B@15.0Mpps).
            # Uses rx_desc=2048 across all 5 credit modes; `off` is gated on bad/torn/pf/PMU
            # with any OS-tick loss recorded honestly rather than aborted.
            for fsz, rate, use_aw in [(1500, 5.0, True), (64, 15.0, True)]:
                for cm in order5:
                    cmd = EAL_1Q + [
                        "--mode", "rx-stream", "--credit", cm,
                        "--frame-size", str(fsz), "--rx-desc", "2048", "--pool-size", "4095",
                        "--r-eff", "256", "--credit-batch", "32", "--target-mpps", f"{rate:.1f}",
                        "--duration-ms", "1000", "--warmup-ms", "300", "--run-gen", "0",
                    ]
                    if use_aw:
                        cmd += ["--app-work", "--work-cycles-per-step", f"{work_cyc_per_poll:.4f}"]
                    rec = exec_run(
                        "A1_stream", f"stream_{fsz}B_{int(rate)}M_{cm}", rep,
                        cmd,
                        expect_lossless=(cm != "off"),
                    )
                    log(
                        f"  A1 rep={rep} {fsz:4d}B@{rate:4.1f}M {cm:12s}: mpps={rec['rx_mpps']:6.3f} "
                        f"net={rec['net_cyc_per_pkt']:6.2f} gross={rec['gross_cyc_per_pkt']:6.2f} "
                        f"avg_b={rec['avg_batch']:5.2f} lost={rec['lost']} m_gap={rec['meas_seq_gap_lost']}"
                    )

            # A2 direct control-loop RTT (tau_rtt) & 100GbE BDP lower bound
            for fsz in (64, 1500):
                for cm in order4:
                    rec = exec_run(
                        "A2_rtt", f"credit_rtt_{fsz}B_{cm}", rep,
                        EAL_1Q + [
                            "--mode", "credit-rtt", "--credit", cm,
                            "--frame-size", str(fsz), "--rx-desc", "512",
                            "--rounds", "50000", "--warmup-rounds", "5000", "--run-gen", "0",
                        ],
                        expect_lossless=True,
                    )
                    log(
                        f"  A2_rtt rep={rep} {fsz:4d}B {cm:12s}: p50={rec['p50_us']:.3f}us p99={rec['p99_us']:.3f}us "
                        f"bdp_p50={rec['bdp_100gbe_p50_kib']:.2f}KiB"
                    )

            # A2 zero-load W=1 ping-pong latency increment (B=32 realistic + B=1 worst-case)
            for fsz in (64, 1500):
                for cb in (32, 1):
                    for cm in order5:
                        rec = exec_run(
                            "A2_pp", f"pingpong_{fsz}B_B{cb}_{cm}", rep,
                            EAL_1Q + [
                                "--mode", "ping-pong", "--credit", cm,
                                "--frame-size", str(fsz), "--rx-desc", "512",
                                "--r-eff", "256", "--credit-batch", str(cb),
                                "--rounds", "20000", "--warmup-rounds", "5000", "--run-gen", "0",
                            ],
                            expect_lossless=True,
                        )
                        log(f"  A2_pp rep={rep} {fsz:4d}B B={cb:2d} {cm:12s}: p50={rec['p50_us']:.3f}us p99={rec['p99_us']:.3f}us")

        # ---- Control C: Static Inplace Ring vs Dynamic mbuf Rebind (2 s full-speed, n=3 interleaved) ----
        log("=== Control C: Static inplace vs dynamic mbuf rebind data safety (n=3 interleaved) ===")
        c_arms = [
            ("static_inplace_B32", ["--recycle", "static-inplace", "--pool-size", "512", "--pool-cache", "128", "--credit-batch", "32"]),
            ("dynamic_aligned_poison_B64", ["--recycle", "dynamic", "--pool-size", "1023", "--pool-cache", "128", "--poison", "--align-refill", "--credit-batch", "64"]),
            ("dynamic_unaligned_poison_B32", ["--recycle", "dynamic", "--pool-size", "1023", "--pool-cache", "128", "--poison", "--credit-batch", "32"]),
        ]
        for rep in range(REPS):
            arms = c_arms if rep % 2 == 0 else list(reversed(c_arms))
            for fsz in (64, 1500):
                for name, extra in arms:
                    rec = exec_run(
                        "C", f"{fsz}B_{name}", rep,
                        EAL_1Q + [
                            "--mode", "rx-stream", "--credit", "bar4-nofence",
                            "--frame-size", str(fsz), "--rx-desc", "512", "--r-eff", "256",
                            "--target-mpps", "0", "--duration-ms", "2000", "--warmup-ms", "300",
                            "--run-gen", "0",
                        ] + extra,
                        expect_lossless=True,
                    )
                    log(
                        f"  C rep={rep} {fsz:4d}B {name:28s}: mpps={rec['rx_mpps']:6.3f} gbps={rec['rx_gbps']:5.2f} "
                        f"iova_same={rec['iova_unchanged_pct']:6.2f}% lost={rec['lost']} bad={rec['bad']} torn={rec['torn']}"
                    )

        # ---- Control D1: Native SDP SLIST_DBELL/WMARK vs Explicit NO-SFENCE BAR4 Credit (n=3 interleaved) ----
        log("=== Control D1: Single-queue native-bp vs bar4-nofence ablation (n=3 interleaved) ===")
        d1_specs = [
            # 1500B @ 5.0 Mpps (60.8 Gbps steady-state lossless; 1ms pause / 10ms awake)
            (1500, 5.0, "native_bp_d256", "native-bp", 256, 256, 0, False),
            (1500, 5.0, "native_bp_d512", "native-bp", 512, 256, 0, False),
            (1500, 5.0, "bar4_nofence_R256", "bar4-nofence", 512, 256, 0, True),
            (1500, 5.0, "bar4_nofence_R128", "bar4-nofence", 512, 128, 0, True),
            (1500, 5.0, "bar4_nofence_R64", "bar4-nofence", 512, 64, 0, True),
            (1500, 5.0, "bar4_nofence_96KiB", "bar4-nofence", 512, 256, 98304, True),
            # 64B @ 15.0 Mpps (10.1 Gbps steady-state lossless with card FIFO; 1ms pause / 10ms awake)
            (64, 15.0, "native_bp_d256", "native-bp", 256, 256, 0, False),
            (64, 15.0, "native_bp_d512", "native-bp", 512, 256, 0, False),
            (64, 15.0, "bar4_nofence_R256", "bar4-nofence", 512, 256, 0, True),
            (64, 15.0, "bar4_nofence_R128", "bar4-nofence", 512, 128, 0, True),
            (64, 15.0, "bar4_nofence_R96", "bar4-nofence", 512, 96, 0, True),
            (64, 15.0, "bar4_nofence_8KiB", "bar4-nofence", 512, 256, 8192, True),
        ]
        for rep in range(REPS):
            specs = d1_specs if rep % 2 == 0 else list(reversed(d1_specs))
            for fsz, rate, name, cm, rx_d, reff, reff_b, lossless in specs:
                rec = exec_run(
                    "D1", f"{fsz}B_{int(rate)}M_{name}", rep,
                    EAL_1Q + [
                        "--mode", "rx-stream", "--credit", cm,
                        "--use-fifo", "--fifo-cap", "131072",
                        "--frame-size", str(fsz), "--rx-desc", str(rx_d),
                        "--r-eff", str(reff), "--r-eff-bytes", str(reff_b),
                        "--credit-batch", "32", "--target-mpps", f"{rate:.1f}",
                        "--pause-every-ms", "10", "--pause-dur-us", "1000",
                        "--duration-ms", "1000", "--warmup-ms", "300", "--run-gen", "0",
                    ],
                    expect_lossless=lossless,
                    expect_fifo=True,
                )
                cs = rec["card_stats"]
                log(
                    f"  D1 rep={rep} {fsz:4d}B {name:18s}: mpps={rec['rx_mpps']:6.3f} gbps={rec['rx_gbps']:5.2f} "
                    f"lost={rec['lost']:7d} m_gap={rec['meas_seq_gap_lost']:7d} dr_max={rec['pause_drained_max']:3d} "
                    f"fifo_pk={cs['q0_fifo_peak']:5d} fifo_full={cs['q0_fifo_full']}"
                )

        # ---- Control B: Socket-1 Neighbor Interference + MRV-196 Opus 5.5 Audit (n=3 interleaved) ----
        log("=== Control B: Socket-1 neighbor interference & MRV-196 Opus audit (n=3 interleaved) ===")
        b_victims = [
            ("llc_k32", "llc-chase", "40", 32),
            ("dram_k1", "dram-chase", "40", 1),
            ("dram_k8", "dram-chase", "40", 8),
            ("stream_6c", "stream-triad", "40,41,42,43,44,45", 0),
        ]
        b_aggrs = [
            ("idle", "idle", "52", 0.0, 0, 1),
            ("l1_spin_1c", "l1-spin", "52", 0.0, 0, 1),
            ("l1_spin_8c", "l1-spin", "52,53,54,55,56,57,58,59", 0.0, 0, 1),
            ("bar4_1c_0.5M_nf", "bar4", "52", 0.5, 0, 1),
            ("bar4_1c_3.5M_nf", "bar4", "52", 3.5, 0, 1),
            ("bar4_1c_7.0M_nf", "bar4", "52", 7.0, 0, 1),
            ("bar4_4c_7.0M_nf", "bar4", "52,53,54,55", 7.0, 0, 1),
            ("bar4_8c_7.0M_nf", "bar4", "52,53,54,55,56,57,58,59", 7.0, 0, 1),
            ("opus_1c_full_sf512", "bar4", "52", 0.0, 512, 4096),
            ("opus_1c_full_sf2048", "bar4", "52", 0.0, 2048, 4096),
            ("opus_1c_full_sf8192", "bar4", "52", 0.0, 8192, 4096),
            ("opus_1c_full_nf", "bar4", "52", 0.0, 0, 4096),
        ]
        for rep in range(REPS):
            aggrs = b_aggrs if rep % 2 == 0 else list(reversed(b_aggrs))
            for vtag, vkind, vcpus, ck in b_victims:
                for atag, amode, acpus, rate, sfb, lpt in aggrs:
                    cmd = [
                        INTERF_BIN,
                        "--victim", vkind, "--aggr", amode,
                        "--victim-cpus", vcpus, "--aggr-cpus", acpus,
                        "--target-mpps", f"{rate:.2f}",
                        "--sfence-batch", str(sfb),
                        "--lines-per-thread", str(lpt),
                        "--duration-ms", "1000", "--warmup-ms", "400",
                        "--run-tag", "0",
                    ]
                    if ck > 0:
                        cmd += ["--chase-k", str(ck)]
                    rec = exec_run("B", f"{vtag}__{atag}", rep, cmd, expect_lossless=True)
                    vr = rec["victim_res"]
                    if vkind == "stream-triad":
                        metric = f"bw={vr['mean_gb_s']:.2f}GB/s"
                    else:
                        metric = f"mean={vr['net_mean_ns']:.2f}ns p99={vr['p99_ns']:.2f}ns"
                    log(
                        f"  B rep={rep} {vtag:9s} {atag:19s}: {metric:26s} "
                        f"rate={rec['actual_mpps']:6.2f}M cha={rec['cha_occ_all']:6.2f} m2p={rec['m2p_ne_pct']:5.1f}%"
                    )

        # =====================================================================
        # PHASE 2: Dual-queue card server (Control D2)
        # =====================================================================
        start_card_server(queues=2, with_dpi=False, pool_size=131071)
        log("=== Control D2: Dual-queue Q0 healthy (3.0 Mpps) + Q1 8ms/10ms paused (1.5 Mpps) (n=3 interleaved) ===")
        d2_specs = [
            ("native_bp_d512", "native-bp", 512, 256, False),
            ("bar4_nofence_R128", "bar4-nofence", 512, 128, True),
            ("bar4_nofence_R256", "bar4-nofence", 512, 256, True),
        ]
        for rep in range(REPS):
            specs = d2_specs if rep % 2 == 0 else list(reversed(d2_specs))
            for name, cm, rx_d, reff, lossless in specs:
                rec = exec_run(
                    "D2", f"1500B_{name}", rep,
                    EAL_2Q + [
                        "--mode", "dual-queue", "--credit", cm,
                        "--use-fifo", "--fifo-cap", "131072",
                        "--frame-size", "1500", "--rx-desc", str(rx_d),
                        "--r-eff", str(reff), "--credit-batch", "32",
                        "--target-mpps", "4.5", "--q1-rate-frac", "0.5",
                        "--q1-period-ms", "10", "--q1-pause-us", "8000",
                        "--duration-ms", "1000", "--warmup-ms", "300", "--run-gen", "0",
                    ],
                    expect_lossless=lossless,
                    expect_fifo=True,
                )
                cs = rec["card_stats"]
                log(
                    f"  D2 rep={rep} {name:18s}: Q0={rec['q0_mpps']:.3f}M(lost={rec['q0_lost']},p99={rec['q0_gap_p99_us']:.2f}us) "
                    f"Q1={rec['q1_mpps']:.3f}M(lost={rec['q1_lost']},m_gap={rec['q1_meas_seq_gap_lost']},dr_max={rec['q1_drained_max']}) "
                    f"fifo_pk=({cs['q0_fifo_peak']},{cs['q1_fifo_peak']}) fifo_full=({cs['q0_fifo_full']},{cs['q1_fifo_full']})"
                )

    finally:
        post_env = restore_baseline_and_audit()
        env_doc["post_audit"] = post_env
        env_doc["campaign_wall_s"] = round(time.monotonic() - t_campaign, 2)

        # Compute exact EDAC/AER deltas
        pre_c = pre_env["card"]
        post_c = post_env["card"]
        deltas = {
            "host_edac_ue_delta": {k: post_env["host_edac_ue"][k] - pre_env["host_edac_ue"][k] for k in pre_env["host_edac_ue"]},
            "host_rp_aer_delta": {k: post_env["host_rp_aer"][k] - pre_env["host_rp_aer"][k] for k in pre_env["host_rp_aer"]},
            "card_edac_delta": {
                k: post_c[k] - pre_c[k]
                for k in pre_c
                if k.startswith("EDAC_") and isinstance(pre_c[k], int)
            },
        }
        env_doc["edac_aer_deltas"] = deltas
        all_zero = (
            all(v == 0 for v in deltas["host_edac_ue_delta"].values())
            and all(v == 0 for v in deltas["host_rp_aer_delta"].values())
            and all(v == 0 for v in deltas["card_edac_delta"].values())
        )
        baseline_ok = (
            all_zero
            and post_c.get("ETH0") == "UP"
            and post_c.get("PEM_WC") == 0
            and post_c.get("REFL") == "running"
            and post_env["host_cpu40_gov"] == "performance"
            and all(v == 2200000 for v in post_env["host_uncore_khz"].values())
        )
        env_doc["baseline_restored_ok"] = baseline_ok
        ENV_PATH.write_text(json.dumps(env_doc, indent=2) + "\n")
        log(f"Post-audit saved to {ENV_PATH}. baseline_restored_ok={baseline_ok}, deltas={deltas}")


if __name__ == "__main__":
    main()
