"""Paced card -> host stream: the host takes each cell as it lands (eager) or reads nothing until the
card is done (blast). Same card binary and pace; one variable: whether the host reads while it lands."""
import re
import sys

sys.path.insert(0, "/tmp/mrv17")
import harness as H

CARD_X = "/root/bench/inline_reflector-mrv17b"
STREAM = "/tmp/mrv17/tx/release/inline_stream"
PERF = H.PERF
EVS = ("uncore_irp_1/event=0x12,umask=0x78/,uncore_irp_1/event=0x1f,umask=0x10/,"
       "uncore_iio_1/event=0x84,ch_mask=0xff,fc_mask=7,umask=1/")


def run(gap, mode, cells, rep):
    tag = f"C2HBLAST gap{gap} {mode} rep{rep}"
    card_args = f"-l 3 -a 0002:01:00.2 --file-prefix=inl -- --store --store-wc --source-gap-ns {gap}"
    csv = f"/tmp/mrv17/c2hblast-gap{gap}-{mode}-rep{rep}.csv"
    perf = f"{PERF} stat -a --per-socket -x ';' -o {csv} -e {EVS}"
    H.card_start(card_args, CARD_X)
    try:
        extra = "--blast" if mode == "blast" else "--lag-us 0"
        cmd, rc, out, secs = H.host_run(f"--dir c2h --cells {cells} {extra}", perf=perf, host_bin=STREAM)
    finally:
        log = H.card_stop()
    card_rate = re.search(r"stream writing rate per core \(credit waits excluded\): ([\d.]+) M/s", log)
    host = re.search(r"(host took them at [^;]+|host reading nothing: .*)", out)
    tot = {}
    for line in open(csv):
        p = line.rstrip("\n").split(";")
        if len(p) > 5 and p[0] == "S1":
            try:
                tot[p[4]] = float(p[2])
            except ValueError:
                pass
    wr = next((v for k, v in tot.items() if "0x84" in k), 0.0)
    hitm = next((v for k, v in tot.items() if "0x78" in k), 0.0)
    lost = next((v for k, v in tot.items() if "umask=0x10" in k and "irp" in k), 0.0)
    rec = dict(tag=tag, card_cmd=f"{CARD_X} {card_args}", host_cmd=cmd, rc=rc, host=[], host_out=out[-2000:],
               card_log=log[-1500:], perf=tot, card_rate=float(card_rate.group(1)) if card_rate else None,
               host_line=host.group(1) if host else None)
    H.record(rec)
    print(f"{tag}: card writes {rec['card_rate']} M/s | host: {rec['host_line']} | inbound writes {wr/1e6:.2f}M, "
          f"hitM/write {hitm/max(wr, 1):.3f}, lostfwd/write {lost/max(wr, 1):.4f} | rc {rc}", flush=True)
    return rec


if __name__ == "__main__":
    gaps = [int(g) for g in sys.argv[1].split(",")]
    cells = int(sys.argv[2])
    reps = int(sys.argv[3])
    for rep in range(reps):
        for gap in gaps:
            for mode in (("eager", "blast") if rep % 2 == 0 else ("blast", "eager")):
                run(gap, mode, cells, rep)
