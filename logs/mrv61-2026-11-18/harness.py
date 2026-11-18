#!/usr/bin/env python3
"""MRV-61 experiment harness: does card ring-core polling at the write frontier hold h2c MOVDIR64B at ~126 M/s?"""
import collections
import json
import pathlib
import re
import subprocess
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv61")
LOG_DIR = ROOT / "logs/mrv61-2026-11-18"
RUNS_JSONL = LOG_DIR / "runs.jsonl"
HOST_BIN = ROOT / "testbed/target/release/inline_stream"
CARD_BIN = "/root/bench/inline_reflector.mrv61"
N_CELLS = 200_000_000
UNCORE_HZ = 2.2e9

PERF_EVENTS = ",".join([
    "cpu/cycles/",
    "cpu/ref-cycles/",
    "cpu/instructions/",
    "uncore_cha/event=0x35,umask=0xc867ff01,name=cha_ins_wcilf/",
    "uncore_cha/event=0x36,umask=0xc867ff01,name=cha_occ_wcilf/",
    "uncore_cha_0/event=0x1,name=cha0_clk/",
    "uncore_iio_1/event=0xc1,ch_mask=0xff,fc_mask=7,umask=1,name=iio1_cpu_wr/",
    "uncore_iio_1/event=0x84,ch_mask=0xff,fc_mask=7,umask=1,name=iio1_inb_wr/",
])


def check_clocks():
    gov = pathlib.Path("/sys/devices/system/cpu/cpu40/cpufreq/scaling_governor").read_text().strip()
    if gov != "performance":
        raise RuntimeError(f"cpu40 governor is {gov!r}, expected 'performance'")
    uncore_files = sorted(pathlib.Path("/sys/devices/system/cpu/intel_uncore_frequency").glob("*/current_freq_khz"))
    uncore_khz = {p.parent.name: int(p.read_text().strip()) for p in uncore_files}
    for name, khz in uncore_khz.items():
        if khz != 2_200_000:
            raise RuntimeError(f"uncore {name} is {khz} kHz, expected 2200000")
    return {"cpu40_governor": gov, "uncore_khz": uncore_khz}


def start_card(gap_ns: int, mode: str) -> str:
    card_cmd = (
        f"{CARD_BIN} -l 3-6 -a 0002:01:00.2 --file-prefix=inl -- "
        f"--roles ring,ring,source,source --store --store-wc "
        f"--poll-gap-ns {gap_ns} --poll-gap-mode {mode}"
    )
    script = f"""
pkill -INT -f inline_reflector 2>/dev/null || true
for _ in $(seq 1 20); do pgrep -f inline_reflector >/dev/null || break; sleep 0.05; done
rm -f /tmp/mrv61_card.log
nohup {card_cmd} > /tmp/mrv61_card.log 2>&1 &
for _ in $(seq 1 50); do
    grep -q "hosts lease the slot at" /tmp/mrv61_card.log 2>/dev/null && exit 0
    sleep 0.1
done
cat /tmp/mrv61_card.log >&2
exit 1
"""
    subprocess.run(["ssh", "root@10.10.0.2", script], check=True)
    return card_cmd


def stop_card() -> list[str]:
    script = """
pkill -INT -f inline_reflector.mrv61 2>/dev/null || true
for _ in $(seq 1 50); do pgrep -f inline_reflector.mrv61 >/dev/null || break; sleep 0.05; done
cat /tmp/mrv61_card.log
"""
    res = subprocess.run(["ssh", "root@10.10.0.2", script], check=True, capture_output=True, text=True)
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def parse_perf(csv_text: str):
    by_t = collections.OrderedDict()
    min_pct = 100.0
    for line in csv_text.splitlines():
        parts = line.strip().split(";")
        if len(parts) < 7 or parts[0].startswith("#"):
            continue
        t_str, cpu, val_str, _, ev, ena_str, pct_str = parts[:7]
        try:
            t = float(t_str)
            val = float(val_str)
            ena = float(ena_str)
            pct = float(pct_str)
        except ValueError:
            continue
        min_pct = min(min_pct, pct)
        win = by_t.setdefault(t, {
            "t": t,
            "uncore": collections.defaultdict(float),
            "uncore_ena_ns": collections.defaultdict(float),
            "core": collections.defaultdict(lambda: collections.defaultdict(float)),
        })
        if ev.startswith("cpu/"):
            win["core"][cpu][ev] += val
            win["core"][cpu]["ena_ns"] = ena
        else:
            win["uncore"][ev] += val
            win["uncore_ena_ns"][ev] = max(win["uncore_ena_ns"][ev], ena)

    tot_all = collections.defaultdict(float)
    tot_active = collections.defaultdict(float)
    core_all = collections.defaultdict(lambda: collections.defaultdict(float))
    core_active = collections.defaultdict(lambda: collections.defaultdict(float))
    active_intervals = []

    for t, win in by_t.items():
        u = win["uncore"]
        for k, v in u.items():
            tot_all[k] += v
        for cpu, cdict in win["core"].items():
            for k, v in cdict.items():
                core_all[cpu][k] += v
        is_active = u.get("cha_ins_wcilf", 0.0) > 1_000_000
        if is_active:
            for k, v in u.items():
                tot_active[k] += v
            for cpu, cdict in win["core"].items():
                for k, v in cdict.items():
                    core_active[cpu][k] += v
        active_intervals.append({
            "t": round(t, 4),
            "active": is_active,
            "cha_ins_wcilf": u.get("cha_ins_wcilf", 0.0),
            "cha_occ_wcilf": u.get("cha_occ_wcilf", 0.0),
            "cha0_clk": u.get("cha0_clk", 0.0),
            "iio1_cpu_wr": u.get("iio1_cpu_wr", 0.0),
            "iio1_inb_wr": u.get("iio1_inb_wr", 0.0),
            "cpu40_cycles": win["core"]["CPU40"].get("cpu/cycles/", 0.0),
            "cpu40_ref_cycles": win["core"]["CPU40"].get("cpu/ref-cycles/", 0.0),
            "cpu40_instr": win["core"]["CPU40"].get("cpu/instructions/", 0.0),
            "cpu41_cycles": win["core"]["CPU41"].get("cpu/cycles/", 0.0),
            "cpu41_ref_cycles": win["core"]["CPU41"].get("cpu/ref-cycles/", 0.0),
            "cpu41_instr": win["core"]["CPU41"].get("cpu/instructions/", 0.0),
        })

    def summarize_core(cdict):
        out = {}
        for cpu, d in cdict.items():
            cyc = d.get("cpu/cycles/", 0.0)
            ref = d.get("cpu/ref-cycles/", 0.0)
            ins = d.get("cpu/instructions/", 0.0)
            out[cpu] = {
                "cycles": cyc,
                "ref_cycles": ref,
                "instr": ins,
                "ghz": (cyc / ref * 2.2) if ref > 0 else 0.0,
                "cycles_per_cell": cyc / N_CELLS,
                "instr_per_cell": ins / N_CELLS,
            }
        return out

    return {
        "min_pct_enabled": min_pct,
        "tot_all": dict(tot_all),
        "tot_active": dict(tot_active),
        "core_all": summarize_core(core_all),
        "core_active": summarize_core(core_active),
        "intervals": active_intervals,
    }


def parse_host_out(line: str, direction: str):
    if direction == "h2c":
        m = re.search(
            r"host issued at ([\d.]+) M cells/s .* card took all at ([\d.]+) M cells/s .* "
            r"\(card_next polls: (\d+) in send, (\d+) in wait, (\d+) BAR4 reads\)",
            line,
        )
        if not m:
            raise ValueError(f"unexpected h2c output: {line}")
        return {
            "host_issued_mps": float(m.group(1)),
            "card_took_mps": float(m.group(2)),
            "card_next_send": int(m.group(3)),
            "card_next_wait": int(m.group(4)),
            "bar4_reads": int(m.group(5)),
            "bad": 0,
            "torn": 0,
            "late": 0,
        }
    else:
        m = re.search(
            r"h2c via Movdir card took all at ([\d.]+) M cells/s .* "
            r"\(card_next polls: (\d+) in send, (\d+) in wait, (\d+) BAR4 reads\) \| "
            r"c2h \d+ card cores -> 1 host core at ([\d.]+) M cells/s .* "
            r"\(bad (\d+) torn (\d+) late (\d+)\)",
            line,
        )
        if not m:
            raise ValueError(f"unexpected bidir output: {line}")
        return {
            "host_issued_mps": float(m.group(1)),
            "card_took_mps": float(m.group(1)),
            "card_next_send": int(m.group(2)),
            "card_next_wait": int(m.group(3)),
            "bar4_reads": int(m.group(4)),
            "c2h_mps": float(m.group(5)),
            "bad": int(m.group(6)),
            "torn": int(m.group(7)),
            "late": int(m.group(8)),
        }


def parse_card_out(lines: list[str], direction: str):
    refl_line = next((l for l in lines if l.startswith("reflected ")), None)
    per_core_line = next((l for l in lines if l.startswith("per core: ")), None)
    if not refl_line or not per_core_line:
        raise ValueError(f"missing card summary lines in: {lines}")
    m_refl = re.search(
        r"partial cells seen (\d+), corrupt payloads (\d+), no ring writer for (\d+); "
        r"streams: sunk (\d+), sourced (\d+)",
        refl_line,
    )
    if not m_refl:
        raise ValueError(f"unexpected reflected line: {refl_line}")
    partial, corrupt, unroutable, sunk, sourced = map(int, m_refl.groups())
    if partial != 0 or corrupt != 0 or unroutable != 0:
        raise RuntimeError(f"card integrity error: {refl_line}")
    if sunk != N_CELLS:
        raise RuntimeError(f"card sunk {sunk} != {N_CELLS}")
    if direction == "bidir" and sourced != N_CELLS:
        raise RuntimeError(f"card sourced {sourced} != {N_CELLS}")

    ring_matches = list(re.finditer(
        r"ring (\d+) cells, ([\d.]+) per busy poll, ([\d.]+)% of polls empty while serving, (\d+) pauses \((\d+) busy polls\)",
        per_core_line,
    ))
    if len(ring_matches) != 2:
        raise ValueError(f"expected 2 ring cores in: {per_core_line}")
    rings = []
    for rm in ring_matches:
        rings.append({
            "cells": int(rm.group(1)),
            "cells_per_busy_poll": float(rm.group(2)),
            "empty_poll_pct": float(rm.group(3)),
            "pauses": int(rm.group(4)),
            "busy_polls": int(rm.group(5)),
        })
    return {
        "reflected_line": refl_line,
        "per_core_line": per_core_line,
        "partial": partial,
        "corrupt": corrupt,
        "unroutable": unroutable,
        "sunk": sunk,
        "sourced": sourced,
        "ring_cores": rings,
        "mean_cells_per_busy_poll": (rings[0]["cells_per_busy_poll"] + rings[1]["cells_per_busy_poll"]) / 2.0,
    }


def run_one(run_id: str, rep: int, direction: str, gap_ns: int, mode: str):
    clocks = check_clocks()
    card_mode_arg = "spin" if mode == "none" else mode
    card_cmd = start_card(gap_ns, card_mode_arg)

    if direction == "h2c":
        host_args = [
            str(HOST_BIN),
            "-l", "40",
            "-a", "0000:99:02.0",
            "--file-prefix=mrv61",
            "--log-level=lib.eal:error",
            "--",
            "--dir", "h2c",
            "-n", str(N_CELLS),
        ]
    else:
        host_args = [
            str(HOST_BIN),
            "-l", "40,41",
            "--main-lcore", "41",
            "-a", "0000:99:02.0",
            "--file-prefix=mrv61",
            "--log-level=lib.eal:error",
            "--",
            "--dir", "bidir",
            "-n", str(N_CELLS),
        ]

    perf_cmd = [
        "nix", "shell", "nixpkgs#perf", "-c",
        "perf", "stat", "-I", "100", "-x", ";", "-A", "-C", "32,40,41",
        "-e", PERF_EVENTS,
        "--",
    ] + host_args

    t0 = time.time()
    proc = subprocess.run(perf_cmd, capture_output=True, text=True)
    elapsed = time.time() - t0
    card_lines = stop_card()

    if proc.returncode != 0:
        raise RuntimeError(f"host command failed ({proc.returncode}): stdout={proc.stdout} stderr={proc.stderr}")

    host_out_lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    if not host_out_lines:
        raise RuntimeError(f"empty host stdout; stderr={proc.stderr}")

    host_parsed = parse_host_out(host_out_lines[-1], direction)
    if host_parsed["bad"] != 0 or host_parsed["torn"] != 0:
        raise RuntimeError(f"host bad/torn non-zero: {host_parsed}")

    card_parsed = parse_card_out(card_lines, direction)
    perf_parsed = parse_perf(proc.stderr)
    if perf_parsed["min_pct_enabled"] < 99.9:
        raise RuntimeError(f"counter multiplexing detected: min_pct_enabled={perf_parsed['min_pct_enabled']}")

    tot = perf_parsed["tot_all"]
    h2c_rate = host_parsed["card_took_mps"]
    window_s = tot["cha_ins_wcilf"] / (h2c_rate * 1e6)
    cha_residency_ns = tot["cha_occ_wcilf"] / tot["cha_ins_wcilf"] / UNCORE_HZ * 1e9
    cha_mean_entries = tot["cha_occ_wcilf"] / (UNCORE_HZ * window_s)

    record = {
        "id": run_id,
        "rep": rep,
        "dir": direction,
        "poll_gap_ns": gap_ns,
        "poll_gap_mode": mode,
        "cells": N_CELLS,
        "cell_bytes": 64,
        "batch": 1,
        "clocks": clocks,
        "card_cmd": card_cmd,
        "host_cmd": " ".join(host_args),
        "host_out": host_out_lines[-1],
        "host": host_parsed,
        "card": card_parsed,
        "perf": {
            "min_pct_enabled": perf_parsed["min_pct_enabled"],
            "tot_all": perf_parsed["tot_all"],
            "tot_active": perf_parsed["tot_active"],
            "core_all": perf_parsed["core_all"],
            "core_active": perf_parsed["core_active"],
            "cha_residency_ns": cha_residency_ns,
            "cha_mean_entries": cha_mean_entries,
            "intervals": perf_parsed["intervals"],
        },
        "elapsed_s": round(elapsed, 3),
        "ts": time.time(),
    }
    print(
        f"[{run_id}] dir={direction:5s} gap={gap_ns:4d}ns mode={mode:7s} | "
        f"h2c={host_parsed['card_took_mps']:6.2f} M/s | "
        f"batch={card_parsed['ring_cores'][0]['cells_per_busy_poll']:.1f}/{card_parsed['ring_cores'][1]['cells_per_busy_poll']:.1f} "
        f"(pauses={card_parsed['ring_cores'][0]['pauses']}/{card_parsed['ring_cores'][1]['pauses']}) | "
        f"card_next={host_parsed['card_next_send']}+{host_parsed['card_next_wait']} ({host_parsed['bar4_reads']} BAR4) | "
        f"cpu40={perf_parsed['core_all']['CPU40']['cycles_per_cell']:.2f} cyc/{perf_parsed['core_all']['CPU40']['instr_per_cell']:.2f} ins "
        f"({perf_parsed['core_all']['CPU40']['ghz']:.2f} GHz) | "
        f"CHA={cha_mean_entries:.1f} entries ({cha_residency_ns:.0f} ns, pct={perf_parsed['min_pct_enabled']:.0f}%)",
        flush=True,
    )
    return record


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    schedule = []
    idx = 1
    for rep in (1, 2, 3):
        schedule.append((f"R{idx:02d}", rep, "h2c", 0, "none")); idx += 1
        for mode in ("spin", "private", "ring"):
            schedule.append((f"R{idx:02d}", rep, "h2c", 1000, mode)); idx += 1
        schedule.append((f"R{idx:02d}", rep, "h2c", 0, "none")); idx += 1
        for mode in ("spin", "private", "ring"):
            schedule.append((f"R{idx:02d}", rep, "h2c", 3000, mode)); idx += 1
        schedule.append((f"R{idx:02d}", rep, "bidir", 0, "none")); idx += 1

    with RUNS_JSONL.open("w") as f:
        for run_id, rep, direction, gap_ns, mode in schedule:
            rec = run_one(run_id, rep, direction, gap_ns, mode)
            f.write(json.dumps(rec) + "\n")
            f.flush()


if __name__ == "__main__":
    main()
