#!/usr/bin/env python3
"""MRV-70 experiment harness: open-loop bidirectional communication vs unidirectional and closed-loop baselines.

All host runs execute on pinned cpu40 (3.9 GHz performance governor, SMT sibling cpu104 idle, uncore 2.2 GHz).
Writes verified records to logs/mrv70-2026-11-18/runs.jsonl.
"""
import datetime
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path("/home/hongtao/dev/marvell-mrv70")
LOG_DIR = ROOT / "logs" / "mrv70-2026-11-18"
RUNS_PATH = LOG_DIR / "runs.jsonl"
STREAM_BIN = ROOT / "testbed" / "target" / "release" / "inline_stream"
PINGPONG_BIN = ROOT / "testbed" / "target" / "release" / "inline_pingpong"
CARD_HOST = "root@10.10.0.2"
CARD_BIN = "/root/bench/inline_reflector.mrv70"

CARD_CONFIGS = {
    "1c_wc_duplex": "-l 3 -a 0002:01:00.2 --file-prefix=inl -- --roles ring --store --store-wc",
    "1p1_wc_split": "-l 3,4 -a 0002:01:00.2 --file-prefix=inl -- --roles ring,source --store --store-wc",
    "2p2_wc_split": "-l 3-6 -a 0002:01:00.2 --file-prefix=inl -- --roles ring,ring,source,source --store --store-wc",
    "4c_wc_duplex": "-l 3-6 -a 0002:01:00.2 --file-prefix=inl -- --roles ring,ring,ring,ring --store --store-wc",
    "1p1_dpi_split": (
        "-l 3,4 -a 0002:01:00.2 -a 0000:06:00.1 -a 0000:06:00.2 --file-prefix=inl -- "
        "--roles ring,source --dma 0000:06:00.1,0000:06:00.2"
    ),
}

PP_RE = re.compile(
    r"window\s+(?P<w>\d+)\s+rounds\s+(?P<measured>\d+)\s+"
    r"lost\s+(?P<lost>\d+)\s+bad\s+(?P<bad>\d+)\s+torn\s+(?P<torn>\d+)\s+late\s+(?P<late>\d+).*?\|\s*"
    r"(?P<mps>[\d.]+)\s+Mmsg/s\s*\|\s*RTT\s+us:\s+min\s+(?P<min>[\d.]+)\s+p50\s+(?P<p50>[\d.]+)\s+"
    r"p90\s+(?P<p90>[\d.]+)\s+p99\s+(?P<p99>[\d.]+)\s+p99\.9\s+(?P<p999>[\d.]+)\s+"
    r"p99\.99\s+(?P<p9999>[\d.]+)\s+max\s+(?P<max>[\d.]+)\s+mean\s+(?P<mean>[\d.]+)\s*\|\s*polls\s+(?P<empty_pct>[\d.]+)%\s+empty"
)


def card_sh(cmd: str, timeout: int = 30) -> str:
    p = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", CARD_HOST, cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if p.returncode != 0:
        raise RuntimeError(f"card_sh failed ({p.returncode}): {cmd}\n{p.stdout}\n{p.stderr}")
    return p.stdout + p.stderr


def start_card(cfg_name: str) -> None:
    flags = CARD_CONFIGS[cfg_name]
    cmd = (
        "tmux send-keys -t inl C-c 2>/dev/null || true; "
        "for i in $(seq 1 20); do pgrep -f inline_reflector >/dev/null || break; sleep 0.1; done; "
        "tmux kill-session -t inl 2>/dev/null || true; "
        "lsmod | grep -q pem_wc || insmod /root/bench/pem_wc.ko; "
        f"{CARD_BIN} --restore; "
        f'tmux new-session -d -s inl "{CARD_BIN} {flags} > /tmp/inl.log 2>&1"; '
        'for i in $(seq 1 40); do grep -q "hosts lease the slot" /tmp/inl.log 2>/dev/null && break; sleep 0.1; done; '
        'grep -q "hosts lease the slot" /tmp/inl.log'
    )
    card_sh(cmd, timeout=30)
    print(f"[card] started {cfg_name}: {flags}", flush=True)


def stop_card() -> str:
    cmd = (
        "tmux send-keys -t inl C-c 2>/dev/null || true; "
        "for i in $(seq 1 20); do pgrep -f inline_reflector >/dev/null || break; sleep 0.1; done; "
        "tmux kill-session -t inl 2>/dev/null || true; "
        "cat /tmp/inl.log 2>/dev/null || true"
    )
    return card_sh(cmd, timeout=30)


def append_run(rec: dict) -> None:
    rec["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(RUNS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run_duplex(
    series: str,
    cfg_name: str,
    rep: int,
    h2c_mps: float,
    c2h_mps: float,
    duration_ms: int = 300,
    echo: bool = False,
    h2c_burst: int = 16,
    poll_batch: int = 16,
    cells: int = 30_000_000,
) -> dict:
    cmd = [
        str(STREAM_BIN),
        "-l",
        "40",
        "-a",
        "0000:99:02.0",
        "--file-prefix=mrv70",
        "--log-level=lib.eal:error",
        "--",
        "--dir",
        "duplex",
        "--h2c-mps",
        str(h2c_mps),
        "--c2h-mps",
        str(c2h_mps),
        "--duration-ms",
        str(duration_ms),
        "--h2c-burst",
        str(h2c_burst),
        "--poll-batch",
        str(poll_batch),
        "-n",
        str(cells),
    ]
    if echo:
        cmd.append("--echo")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    out = p.stdout + "\n" + p.stderr
    if p.returncode != 0:
        raise RuntimeError(f"duplex failed ({p.returncode}): {' '.join(cmd)}\n{out}")
    mrv_line = None
    for line in out.splitlines():
        if line.startswith("MRV70 "):
            mrv_line = json.loads(line[6:])
    if mrv_line is None:
        raise RuntimeError(f"missing MRV70 line in output:\n{out}")
    assert mrv_line["lost"] == 0 and mrv_line["bad"] == 0 and mrv_line["torn"] == 0, f"integrity failure: {mrv_line}"
    rec = {
        "series": series,
        "card_cfg": cfg_name,
        "card_flags": CARD_CONFIGS[cfg_name],
        "rep": rep,
        "mode": "open_echo" if echo else "open_duplex",
        "cmd": " ".join(cmd),
        "rc": p.returncode,
        **mrv_line,
    }
    append_run(rec)
    h_p50 = mrv_line["h2c_tot_us"]["p50"]
    c_p50 = mrv_line["echo_tot_us"]["p50"] if echo else mrv_line["c2h_tot_us"]["p50"]
    print(
        f"  [{series}] {cfg_name} r#{rep} echo={echo} target=({h2c_mps},{c2h_mps}) -> "
        f"win=({mrv_line['h2c_win_mps']:.2f},{mrv_line['c2h_win_mps']:.2f}) M/s "
        f"h2c_p50={h_p50} us c2h/echo_p50={c_p50} us",
        flush=True,
    )
    return rec


def run_pingpong(series: str, cfg_name: str, rep: int, window: int, rounds: int = 3_000_000) -> dict:
    cmd = [
        str(PINGPONG_BIN),
        "-l",
        "40",
        "-a",
        "0000:99:02.0",
        "--file-prefix=mrv70",
        "--log-level=lib.eal:error",
        "--",
        "--reply",
        "ring",
        "-w",
        str(window),
        "-n",
        str(rounds),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    out = p.stdout + "\n" + p.stderr
    if p.returncode != 0:
        raise RuntimeError(f"pingpong failed ({p.returncode}): {' '.join(cmd)}\n{out}")
    m = PP_RE.search(out)
    if not m:
        raise RuntimeError(f"failed to parse pingpong output:\n{out}")
    d = m.groupdict()
    lost, bad, torn = int(d["lost"]), int(d["bad"]), int(d["torn"])
    assert lost == 0 and bad == 0 and torn == 0
    rec = {
        "series": series,
        "card_cfg": cfg_name,
        "card_flags": CARD_CONFIGS[cfg_name],
        "rep": rep,
        "mode": "closed_loop",
        "cmd": " ".join(cmd),
        "rc": p.returncode,
        "window": int(d["w"]),
        "measured": int(d["measured"]),
        "lost": lost,
        "bad": bad,
        "torn": torn,
        "late": int(d["late"]),
        "h2c_win_mps": float(d["mps"]),
        "c2h_win_mps": float(d["mps"]),
        "empty_pct": float(d["empty_pct"]),
        "rtt_us": {
            "min": float(d["min"]),
            "p50": float(d["p50"]),
            "p90": float(d["p90"]),
            "p99": float(d["p99"]),
            "p999": float(d["p999"]),
            "max": float(d["max"]),
            "mean": float(d["mean"]),
        },
    }
    append_run(rec)
    print(
        f"  [{series}] {cfg_name} r#{rep} W={window} -> {rec['h2c_win_mps']:.2f} M/s "
        f"rtt_p50={rec['rtt_us']['p50']:.2f} us p99={rec['rtt_us']['p99']:.2f} us",
        flush=True,
    )
    return rec


def main() -> None:
    sym_rates = [5.0, 10.0, 13.5, 20.0, 25.0, 30.0, 35.0, 40.0, 50.0, 60.0, 0.0]
    uni_rates = [10.0, 13.5, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0, 0.0]
    asym_rates = [13.5, 25.0, 40.0, 60.0]

    # 1. 1c_wc_duplex: symmetric bidir, unidirectional baselines, asymmetric grid, open echo, closed-loop W
    start_card("1c_wc_duplex")
    for rep in range(3):
        for r in sym_rates:
            run_duplex("sym_bidir", "1c_wc_duplex", rep, r, r, cells=25_000_000)
        for r in uni_rates:
            run_duplex("uni_h2c", "1c_wc_duplex", rep, r, -1.0, cells=25_000_000)
            run_duplex("uni_c2h", "1c_wc_duplex", rep, -1.0, r, cells=25_000_000)
        for r in [5.0, 10.0, 13.5, 20.0, 25.0, 30.0, 35.0, 40.0, 50.0]:
            run_duplex("open_echo", "1c_wc_duplex", rep, r, 0.0, echo=True)
        for w in [1, 16, 32, 64, 256, 1024]:
            run_pingpong("closed_loop", "1c_wc_duplex", rep, w)
        for rh in asym_rates:
            for rc in asym_rates:
                if rh == rc:
                    continue
                hb = max(4, min(32, round(16 * rh / max(rh, rc))))
                pb = max(4, min(32, round(16 * rc / max(rh, rc))))
                run_duplex("asym_grid", "1c_wc_duplex", rep, rh, rc, h2c_burst=hb, poll_batch=pb)

    # 2. 1p1_wc_split: symmetric bidir, unidirectional baselines, asymmetric rate matrix
    start_card("1p1_wc_split")
    for rep in range(3):
        for r in sym_rates:
            run_duplex("sym_bidir", "1p1_wc_split", rep, r, r, cells=25_000_000)
        for r in uni_rates:
            run_duplex("uni_h2c", "1p1_wc_split", rep, r, -1.0, cells=25_000_000)
            run_duplex("uni_c2h", "1p1_wc_split", rep, -1.0, r, cells=25_000_000)
        for rh in asym_rates:
            for rc in asym_rates:
                if rh == rc:
                    continue
                hb = max(4, min(32, round(16 * rh / max(rh, rc))))
                pb = max(4, min(32, round(16 * rc / max(rh, rc))))
                run_duplex("asym_grid", "1p1_wc_split", rep, rh, rc, h2c_burst=hb, poll_batch=pb)

    # 3. 2p2_wc_split: symmetric bidir & unidirectional baselines
    start_card("2p2_wc_split")
    for rep in range(3):
        for r in sym_rates:
            run_duplex("sym_bidir", "2p2_wc_split", rep, r, r, cells=20_000_000)
        for r in [13.5, 25.0, 40.0, 0.0]:
            run_duplex("uni_h2c", "2p2_wc_split", rep, r, -1.0, cells=25_000_000)
            run_duplex("uni_c2h", "2p2_wc_split", rep, -1.0, r, cells=25_000_000)

    # 4. 4c_wc_duplex: symmetric bidir, open echo, closed-loop W
    start_card("4c_wc_duplex")
    for rep in range(3):
        for r in sym_rates:
            run_duplex("sym_bidir", "4c_wc_duplex", rep, r, r, cells=20_000_000)
        for r in [5.0, 10.0, 13.5, 20.0, 25.0, 30.0, 35.0, 40.0, 50.0]:
            run_duplex("open_echo", "4c_wc_duplex", rep, r, 0.0, echo=True)
        for w in [1, 16, 32, 64, 256, 1024]:
            run_pingpong("closed_loop", "4c_wc_duplex", rep, w)

    # 5. 1p1_dpi_split: symmetric bidir & unidirectional baselines
    start_card("1p1_dpi_split")
    for rep in range(3):
        for r in sym_rates:
            run_duplex("sym_bidir", "1p1_dpi_split", rep, r, r, cells=20_000_000)
        for r in [13.5, 25.0, 40.0, 0.0]:
            run_duplex("uni_h2c", "1p1_dpi_split", rep, r, -1.0, cells=20_000_000)
            run_duplex("uni_c2h", "1p1_dpi_split", rep, -1.0, r, cells=20_000_000)

    stop_card()
    print("ALL MRV-70 RUNS COMPLETED SUCCESSFULLY", flush=True)


if __name__ == "__main__":
    main()
