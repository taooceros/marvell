"""MRV-17 run harness: one fresh card reflector per host run, results appended to runs.jsonl.

Import it (eval) or run a plan file: python3 harness.py plan.json
"""
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import time

CARD = "root@10.10.0.2"
RUNS = "/tmp/mrv17/runs.jsonl"
PERF = "/nix/store/hln3gq5s0pajpqlwcffqadzxglqfhx29-perf-linux-7.3.4/bin/perf"
HOST_OFFICIAL = "/home/hongtao/dev/marvell/testbed/target/release/inline_pingpong"
CARD_ORIG = "/root/bench/inline_reflector"
WCN1 = "-l 3 -a 0002:01:00.2 --file-prefix=inl -- --store --store-wc"
WCN4 = "-l 3-6 -a 0002:01:00.2 --file-prefix=inl -- --store --store-wc"


def card_sh(cmd, timeout=60):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", CARD, cmd], capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def card_alive():
    return card_sh("pgrep -f '[i]nline_reflector|[r]eflector-rs' >/dev/null && echo UP || echo DEAD")[1].strip().endswith("UP")


def card_start(args, binary=CARD_ORIG, wait=40):
    rc, out = card_sh("pgrep -fa '[i]nline_reflector|[r]eflector-rs' || true")
    if out.strip():
        raise RuntimeError("card busy: " + out.strip())
    inner = f"{binary} {args} > /tmp/mrv17-refl.log 2>&1"
    card_sh(f"rm -f /tmp/mrv17-refl.log; tmux new-session -d -s mrv17 {shlex.quote(inner)}")
    t0 = time.time()
    while time.time() - t0 < wait:
        rc, log = card_sh("cat /tmp/mrv17-refl.log 2>/dev/null; pgrep -f '[i]nline_reflector' >/dev/null || echo DEAD")
        if "BAR4 slot" in log:
            return log
        if log.strip().endswith("DEAD"):
            raise RuntimeError("reflector died:\n" + log)
        time.sleep(0.5)
    raise RuntimeError("reflector not ready:\n" + log)


def card_stop(wait=30):
    card_sh("tmux send-keys -t mrv17 C-c 2>/dev/null || true")
    t0 = time.time()
    while time.time() - t0 < wait:
        rc, out = card_sh("pgrep -f '[i]nline_reflector' >/dev/null && echo UP || echo DEAD")
        if out.strip().endswith("DEAD"):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("reflector did not stop")
    rc, log = card_sh("cat /tmp/mrv17-refl.log; tmux kill-session -t mrv17 2>/dev/null; true")
    return log


def host_run(app_args, lcores="40", eal_extra="-a 0000:99:02.0", perf=None, host_bin=HOST_OFFICIAL, timeout=600):
    cmd = f"{host_bin} -l {lcores} {eal_extra} --file-prefix=mrv17 -- {app_args}"
    if perf:
        cmd = f"{perf} -- {cmd}"
    t0 = time.time()
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return cmd, p.returncode, p.stdout + p.stderr, time.time() - t0


def num(s):
    return float(s)


LINE = re.compile(
    r"window (?P<window>\d+) rounds (?P<rounds>\d+) lost (?P<lost>\d+) bad (?P<bad>\d+) torn (?P<torn>\d+) "
    r"late (?P<late>\d+) \(max (?P<late_max>[\d.]+) us\) \| (?P<mmsg>[\d.]+) Mmsg/s \| RTT us: min (?P<min>[\d.]+) "
    r"p50 (?P<p50>[\d.]+) p90 (?P<p90>[\d.]+) p99 (?P<p99>[\d.]+) p99\.9 (?P<p999>[\d.]+) p99\.99 (?P<p9999>[\d.]+) "
    r"max (?P<max>[\d.]+) mean (?P<mean>[\d.]+) \| polls (?P<empty_pct>\d+)% empty"
)


def parse_host(out):
    rows = []
    for line in out.splitlines():
        m = LINE.search(line)
        if m:
            d = {k: num(v) for k, v in m.groupdict().items()}
            d["line"] = line
            rows.append(d)
    return rows


def parse_card(log):
    d = {}
    m = re.search(r"reflected (\d+)", log)
    if m:
        d["reflected"] = int(m.group(1))
    m = re.search(r"partial cells seen (\d+), corrupt payloads (\d+)", log)
    if m:
        d["partial"], d["corrupt"] = int(m.group(1)), int(m.group(2))
    d["per_core"] = re.findall(r"ring (\d+) cells, ([\d.]+) per busy poll(?:[^\n]*?(\d+)% of polls empty)?", log)
    return d


def record(r):
    r["time"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with open(RUNS, "a") as f:
        f.write(json.dumps(r) + "\n")
        f.flush()


def one(tag, card_args, app_args, perf=None, binary=CARD_ORIG, host_bin=HOST_OFFICIAL, lcores="40", eal_extra="-a 0000:99:02.0"):
    """Fresh reflector per row: card stats belong to this host run only."""
    card_start(card_args, binary)
    try:
        cmd, rc, out, secs = host_run(app_args, lcores, eal_extra, perf, host_bin)
    finally:
        log = card_stop()
    os.makedirs("/tmp/mrv17/out", exist_ok=True)
    out_path = "/tmp/mrv17/out/" + re.sub(r"[^A-Za-z0-9_.-]+", "_", tag) + ".txt"
    with open(out_path, "w") as f:
        f.write(out)
    keep = "\n".join(l for l in out.splitlines() if not l.startswith("card-next raw"))
    r = dict(tag=tag, card_cmd=f"{binary} {card_args}", host_cmd=cmd, rc=rc, secs=round(secs, 2),
             host=parse_host(out), host_out=keep[-4000:], out_path=out_path, card=parse_card(log), card_log=log[-3000:])
    record(r)
    if rc != 0 or not r["host"]:
        raise RuntimeError(f"{tag}: host rc {rc}\n{out[-2000:]}")
    return r


def brief(r):
    h = r["host"][0]
    return (f"{r['tag']}: {h['mmsg']:.3f} Mmsg/s p50 {h['p50']} p99 {h['p99']} mean {h['mean']} empty {h['empty_pct']:.0f}% "
            f"lost {h['lost']:.0f} bad {h['bad']:.0f} torn {h['torn']:.0f}")


if __name__ == "__main__":
    # plan: [{"tag":..., "card_args":..., "app_args":..., "perf":..., "binary":..., "host_bin":...}, ...]
    # rows whose tag is already in RUNS (with a host result) are skipped.
    plan = json.load(open(sys.argv[1]))
    done = set()
    try:
        for line in open(RUNS):
            x = json.loads(line)
            if x.get("host") and x.get("rc") == 0:
                done.add(x["tag"])
    except FileNotFoundError:
        pass
    failed = []
    for row in plan:
        if row["tag"] in done:
            print("skip", row["tag"], flush=True)
            continue
        ok = False
        for attempt in range(2):
            try:
                r = one(**row)
                print(brief(r), flush=True)
                ok = True
                break
            except Exception as e:  # one retry, then move on
                print("FAIL", row["tag"], repr(e)[:400], flush=True)
                record(dict(tag=row["tag"] + " FAILED", error=repr(e)[:2000]))
                try:
                    if card_alive():
                        card_stop()
                except Exception:
                    pass
        if not ok:
            failed.append(row["tag"])
    if failed:
        print("MISSING ROWS:", failed, flush=True)
        sys.exit(1)
    print("ALL ROWS OK", flush=True)
