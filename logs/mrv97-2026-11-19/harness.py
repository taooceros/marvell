#!/usr/bin/env python3
"""MRV-97 non-root single-variable W=1 cache & DDIO experiment harness.

Fixed baseline (MRV-82):
  W = 1, 64 B cell, batch = 1, host cpu40 (3.9 GHz), uncore 2.2 GHz,
  MOVDIR64B -> BAR4 slot 15 (resource4), 1 card core (cpu 3, 2.0 GHz),
  PEM0 write-combining (WC) reply into 256 KiB host ring.

Single-variable controls:
  - base:              baseline W=1 (host L1D src, card L3 hot, host DDIO LLC hot)
  - A_cold_src:        CLFLUSHOPT+MFENCE on MOVDIR64B src line before t_send
  - B0_card_hot_wait:  card waits 2500 ns after reply k, then times reading cell k+1 (hot L3)
  - B1_card_cold_pre:  same as B0, plus DC CIVAC (128 B) on cell k+1 right after reply k (cold DRAM before host write)
  - B2_card_dram_ctrl: same as B0, positive control: DC CIVAC (128 B) after arrival, then time DRAM read
  - C0_host_hot_wait:  host waits 2500 ns after MOVDIR64B, then times reading reply (hot DDIO LLC)
  - C1_host_cold_pre:  same as C0, plus CLFLUSHOPT+MFENCE on reply line before t_send (DDIO cold-line allocation test)
  - C2_host_dram_ctrl: same as C0, positive control: CLFLUSHOPT+MFENCE after reply lands, then time DRAM read
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv97")
LOG_DIR = ROOT / "logs/mrv97-2026-11-19"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
HOST_BIN = ROOT / "testbed/target/release/inline_pingpong"
CARD_BIN = "/root/bench/inline_reflector.mrv97"
PERF_BIN = "/nix/store/hln3gq5s0pajpqlwcffqadzxglqfhx29-perf-linux-7.3.4/bin/perf"

WAIT_NS = 2500

CONDITIONS = [
    # (name, group, host_probe, card_probe)
    ("base",              "base", "base",              "none"),
    ("A_cold_src",        "A",    "cold-src",          "none"),
    ("B0_card_hot_wait",  "B",    "base",              "hot-wait"),
    ("B1_card_cold_pre",  "B",    "base",              "cold-before"),
    ("B2_card_dram_ctrl", "B",    "base",              "dram-ctrl"),
    ("C0_host_hot_wait",  "C",    "hot-wait",          "none"),
    ("C1_host_cold_pre",  "C",    "cold-reply-before", "none"),
    ("C2_host_dram_ctrl", "C",    "dram-reply-ctrl",   "none"),
]

CHA_EVENTS = ",".join([
    "unc_cha_tor_inserts.io_itom",
    "unc_cha_tor_inserts.io_hit_itom",
    "unc_cha_tor_inserts.io_miss_itom",
    "unc_cha_tor_inserts.io_wbmtoi",
    "uncore_iio_1/event=0x84,ch_mask=0xff,fc_mask=7,umask=1/",
])

CORE_EVENTS = ",".join([
    "cycles",
    "ref-cycles",
    "mem_load_retired.l3_hit",
    "mem_load_retired.l3_miss",
])


def check_host_clocks():
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    if gov != "performance":
        raise RuntimeError(f"cpu40 governor is {gov!r}, expected 'performance'")
    uncore = {}
    for p in sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz")):
        khz = int(p.read_text().strip())
        uncore[p.parent.name] = khz
        if khz != 2_200_000:
            raise RuntimeError(f"uncore {p.parent.name} is {khz} kHz, expected 2200000")
    return {"cpu40_gov": gov, "uncore_khz": uncore}


def start_card(card_probe: str, warmup: int, rounds: int) -> str:
    pmu_w = max(warmup - 10, 0)
    pmu_r = rounds + (warmup - pmu_w)
    card_cmd = (
        f"{CARD_BIN} -l 3 -a 0002:01:00.2 --file-prefix=inl -- "
        f"--store --store-wc --card-probe {card_probe} --card-wait-ns {WAIT_NS} "
        f"--pmu-warmup {pmu_w} --pmu-rounds {pmu_r}"
    )
    script = f"""
set -e
pkill -INT -f inline_reflector 2>/dev/null || true
for _ in $(seq 1 30); do pgrep -f inline_reflector >/dev/null || break; sleep 0.05; done
rm -f /tmp/mrv97_card.log
nohup {card_cmd} > /tmp/mrv97_card.log 2>&1 &
for _ in $(seq 1 60); do
    grep -q "hosts lease the slot at" /tmp/mrv97_card.log 2>/dev/null && exit 0
    sleep 0.1
done
cat /tmp/mrv97_card.log >&2
exit 1
"""
    subprocess.run(["ssh", "-o", "BatchMode=yes", "root@10.10.0.2", script], check=True)
    return card_cmd


def stop_card() -> tuple[dict, str]:
    script = """
pkill -INT -f inline_reflector.mrv97 2>/dev/null || true
for _ in $(seq 1 60); do pgrep -f inline_reflector.mrv97 >/dev/null || break; sleep 0.05; done
cat /tmp/mrv97_card.log
"""
    res = subprocess.run(["ssh", "-o", "BatchMode=yes", "root@10.10.0.2", script], check=True, capture_output=True, text=True)
    card_json = {}
    for line in res.stdout.splitlines():
        if line.startswith("MRV97_CARD_JSON "):
            card_json = json.loads(line[len("MRV97_CARD_JSON "):])
    return card_json, res.stdout


def parse_perf_csv(path: pathlib.Path, per_socket: bool) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split(";")
        if per_socket:
            # socket,cpus,val,unit,event,enabled_ns,pct,...
            if len(parts) < 7:
                continue
            sock, _cpus, val_s, _unit, ev, _ena, pct_s = parts[:7]
            try:
                val = float(val_s)
                pct = float(pct_s)
            except ValueError:
                continue
            out.setdefault(sock, {})[ev] = {"val": val, "pct": pct}
        else:
            # val,unit,event,enabled_ns,pct,...
            if len(parts) < 5:
                continue
            val_s, _unit, ev, _ena, pct_s = parts[:5]
            try:
                val = float(val_s)
                pct = float(pct_s)
            except ValueError:
                continue
            out[ev] = {"val": val, "pct": pct}
    return out


def run_one(run_id: str, rep: int, cond_name: str, group: str, host_probe: str, card_probe: str, warmup: int, rounds: int) -> dict:
    clocks_before = check_host_clocks()
    card_cmd = start_card(card_probe, warmup, rounds)
    cha_csv = pathlib.Path("/tmp/mrv97_cha.csv")
    core_csv = pathlib.Path("/tmp/mrv97_core.csv")
    cha_csv.unlink(missing_ok=True)
    core_csv.unlink(missing_ok=True)

    host_cmd = [
        PERF_BIN, "stat", "-a", "--per-socket", "-x;", "-o", str(cha_csv), "-e", CHA_EVENTS, "--",
        PERF_BIN, "stat", "-C", "40", "-x;", "-o", str(core_csv), "-e", CORE_EVENTS, "--",
        str(HOST_BIN), "-l", "40", "-a", "0000:99:02.0", "--file-prefix=mrv97", "--",
        "--reply", "ring", "-w", "1", "-n", str(rounds), "--warmup", str(warmup),
        "--cache-probe", host_probe, "--wait-ns", str(WAIT_NS),
    ]
    t0 = time.monotonic()
    res = subprocess.run(host_cmd, capture_output=True, text=True)
    elapsed = time.monotonic() - t0
    card_json, card_log = stop_card()
    clocks_after = check_host_clocks()

    if res.returncode != 0:
        raise RuntimeError(f"host failed rc={res.returncode}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}\nCARD:\n{card_log}")

    host_json = {}
    for line in res.stdout.splitlines():
        if line.startswith("MRV97_HOST_JSON "):
            host_json = json.loads(line[len("MRV97_HOST_JSON "):])
    if not host_json:
        raise RuntimeError(f"missing MRV97_HOST_JSON\nSTDOUT:\n{res.stdout}")

    cha_perf = parse_perf_csv(cha_csv, per_socket=True)
    core_perf = parse_perf_csv(core_csv, per_socket=False)
    s1 = cha_perf.get("S1", {})

    total_rounds = warmup + rounds
    cyc = core_perf.get("cycles", {}).get("val", 0.0)
    ref = core_perf.get("ref-cycles", {}).get("val", 1.0)
    cpu40_ghz = cyc / ref * 2.2 if ref > 0 else 0.0

    card_cyc = card_json.get("core", {}).get("cpu_cycles", {}).get("val", 0)
    card_win_ns = card_json.get("window_ns", 1)
    card_ghz = card_cyc / card_win_ns if card_win_ns > 0 else 0.0

    rec = {
        "id": run_id,
        "rep": rep,
        "cond": cond_name,
        "group": group,
        "host_probe": host_probe,
        "card_probe": card_probe,
        "wait_ns": WAIT_NS,
        "warmup": warmup,
        "rounds": rounds,
        "total_rounds": total_rounds,
        "elapsed_s": round(elapsed, 4),
        "clocks": {
            "before": clocks_before,
            "after": clocks_after,
            "cpu40_ghz": round(cpu40_ghz, 4),
            "card_cpu3_ghz": round(card_ghz, 4),
        },
        "host": host_json,
        "card": card_json,
        "host_perf_s1": s1,
        "host_perf_cpu40": core_perf,
        "card_cmd": card_cmd,
        "host_cmd": " ".join(host_cmd),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Integrity checks
    assert host_json["lost"] == 0 and host_json["bad"] == 0 and host_json["torn"] == 0, f"integrity fail: {host_json}"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=200_000)
    ap.add_argument("--warmup", type=int, default=70_000)
    ap.add_argument("--out", type=str, default=str(RUNS_JSONL))
    args = ap.parse_args()

    reps = 1 if args.smoke else args.reps
    rounds = 30_000 if args.smoke else args.rounds
    warmup = 70_000 if args.smoke else args.warmup
    out_path = pathlib.Path("/tmp/mrv97_smoke.jsonl" if args.smoke else args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.smoke:
        out_path.unlink(missing_ok=True)

    idx = 0
    for rep in range(1, reps + 1):
        # Alternate forward/reverse order across reps to cancel drift
        order = CONDITIONS if rep % 2 == 1 else list(reversed(CONDITIONS))
        for cond_name, group, host_probe, card_probe in order:
            idx += 1
            run_id = f"R{idx:02d}"
            rec = run_one(run_id, rep, cond_name, group, host_probe, card_probe, warmup, rounds)
            with out_path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            h = rec["host"]
            c = rec["card"]
            s1 = rec["host_perf_s1"]
            c40 = rec["host_perf_cpu40"]
            tot = rec["total_rounds"]
            pr = max(c.get("pmu_rounds", 1), 1)
            tad = c.get("tad", {})
            cc = c.get("core", {})
            print(
                f"{run_id} rep={rep} {cond_name:18s} | "
                f"RTT p50={h['rtt_ns']['p50']:7.1f} p99={h['rtt_ns']['p99']:7.1f} | "
                f"hRd={h['read_ns']['p50']:5.1f} cRd={c.get('card_read_ns',{}).get('p50',0):5.1f} | "
                f"hL3m={c40.get('mem_load_retired.l3_miss',{}).get('val',0)/tot:4.2f} "
                f"hHitI={s1.get('unc_cha_tor_inserts.io_hit_itom',{}).get('val',0)/tot:4.2f} "
                f"hMisI={s1.get('unc_cha_tor_inserts.io_miss_itom',{}).get('val',0)/tot:4.2f} "
                f"hWb={s1.get('unc_cha_tor_inserts.io_wbmtoi',{}).get('val',0)/tot:4.2f} | "
                f"cL3m={cc.get('ll_cache_miss_rd',{}).get('val',0)/pr:4.2f} "
                f"cDssR={tad.get('tad_req_msh_out_dss_rd',{}).get('val',0)/pr:4.2f} "
                f"cAllc={tad.get('tad_alloc_any',{}).get('val',0)/pr:4.2f} | "
                f"ghz={rec['clocks']['cpu40_ghz']:.2f}/{rec['clocks']['card_cpu3_ghz']:.2f} "
                f"late={h['late_wait']}/{c.get('card_late_wait',0)}",
                flush=True,
            )


if __name__ == "__main__":
    main()
