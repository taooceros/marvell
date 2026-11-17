"""MRV-44 review answers (MRV-37): three checks of the summary against the raw MRV-17 data
(logs/mrv17-2026-11-17).

(a) Host cores: total throughput vs total concurrency (H host cores x W per core) for the lease
    build (`LEASE H1`, `LEASE FUNC H2/H4`; cpu41-43 on schedutil, one run per point); write-combining
    with 4 card cores, SDP as the grey reference.
(b) Host CHA: average number of MOVDIR64B requests not yet done in the CHAs of socket 1 (TOR
    occupancy / cycles) vs the request rate, one point per 100 ms interval of the `E1 cha_movdir`
    runs (write-combining, 1 card core; intervals chosen as in e1_analyze.py: busy, minus the first
    two and the last), with the least-squares line through the W=512 intervals.
(c) Card lag (host sent, card progress word not yet past it), median with p10-p90, from the probe
    that reads the card's progress word over BAR4 every 50,000 replies: read first (`SPLIT2 ...
    nomark`, card inline_reflector-mrv17b) vs scan the reply ring first, then read (`LANDED ...
    probe`, card inline_reflector of 11-13). Write-combining, 1 card core.

    uv run --with matplotlib python docs/figures/mrv-44-review.py
"""
import json
import re
import statistics
from pathlib import Path

from mrvplot import plt, save

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs/mrv17-2026-11-17"
runs = [json.loads(line) for line in (LOGS / "runs.jsonl").read_text().splitlines()]
WC, SDP, GREY = "#dc2626", "#9ca3af", "#475569"


def ok(r):
    assert r.get("rc", 0) == 0, r["tag"]
    for h in r["host"]:
        assert h["lost"] == h["bad"] == h["torn"] == 0, r["tag"]


# (a) host cores
lanes = {}  # (path, H) -> {total concurrency: Mmsg/s}
for r in runs:
    m = re.match(r"LEASE (?:FUNC )?H(\d) (wc|sdp mempoll) n4 W(\d+)(?: \(|$)", r["tag"])
    if not m:
        continue
    ok(r)
    hosts, path, w = int(m.group(1)), m.group(2), int(m.group(3))
    assert f"--host-lcores {hosts}" in r["host_cmd"] and "inline_reflector-mrv17-lease" in r["card_cmd"], r["tag"]
    total = re.findall(r"concurrency (\d+) \(\d+ per lcore\): rounds \d+ lost 0 bad 0 torn 0 .*?\| ([\d.]+) Mmsg/s", r["host_out"])
    conc, rate = (int(total[-1][0]), float(total[-1][1])) if hosts > 1 else (w, r["host"][0]["mmsg"])
    assert conc == hosts * w, r["tag"]
    if conc >= 64:  # the W=1 latency points are not about throughput
        lanes.setdefault((path, hosts), {})[conc] = rate


# (b) host CHA, as in e1_analyze.py
def intervals(path):
    rows = {}
    for line in open(path):
        p = line.rstrip("\n").split(";")
        if line.startswith("#") or len(p) < 6 or p[1] != "S1":
            continue
        d = rows.setdefault(float(p[0]), {})
        d[p[5]] = d.get(p[5], 0) + float(p[3])
    out, prev = [], 0.0
    for t, d in rows.items():
        out.append((t - prev, d))
        prev = t
    ins, occ = "uncore_cha/event=0x35,umask=0xc867ff01/", "uncore_cha/event=0x36,umask=0xc867ff01/"
    busy = [(dt, d) for dt, d in out if d.get(ins, 0) > 1e5][2:-1]
    return [(d[ins] / dt / 1e6, d[occ] / (dt * 2.2e9)) for dt, d in busy]


cha = {}
for w in (64, 128, 256, 512, 1024):
    r = next(x for x in runs if x["tag"] == f"E1 cha_movdir wc n1 W{w}")
    ok(r)
    cha[w] = intervals(LOGS / f"perf/e1-cha_movdir-w{w}.csv")
xs, ys = zip(*cha[512])
mx, my = statistics.fmean(xs), statistics.fmean(ys)
slope = sum((x - mx) * (y - my) for x, y in cha[512]) / sum((x - mx) ** 2 for x in xs)
icept = my - slope * mx
corr = statistics.correlation(xs, ys)

# (c) card lag
lag = {}
for kind, tagfmt, card in (("read", "SPLIT2 wc n1 W{w} nomark rep{k}", "inline_reflector-mrv17b"),
                           ("scan", "LANDED wc n1 W{w} probe rep{k}", "inline_reflector")):
    for w in (64, 128, 256, 512, 1024):
        vals = []
        for k in (0, 1):
            tag = tagfmt.format(w=w, k=k)
            r = next(x for x in runs if x["tag"] == tag)
            ok(r)
            assert r["card_cmd"].split()[0].endswith("/" + card), tag
            text = (LOGS / "out" / (tag.replace(" ", "_") + ".txt")).read_text()
            fields, samples = re.search(r"card-next raw \(([^)]*)\): (.*)", text).groups()
            names = fields.split(",")
            for s in samples.split():
                v = dict(zip(names, map(int, s.split(","))))
                vals.append(v["sent"] - v["acked"])
        vals.sort()
        lag[(kind, w)] = (vals[len(vals) // 10], statistics.median(vals), vals[len(vals) * 9 // 10])

fig, (a, b, c) = plt.subplots(1, 3, figsize=(15, 4.6))
for (path, hosts), pts in sorted(lanes.items()):
    color = WC if path == "wc" else SDP
    marker = {1: "o", 2: "s", 4: "D"}[hosts]
    cs = sorted(pts)
    a.plot(cs, [pts[x] for x in cs], marker=marker, ms=5, color=color, lw=1.4 if path == "wc" else 1.0,
           linestyle="-" if path == "wc" else "--", label=f"{'写合并' if path == 'wc' else 'SDP 参照'}，主机 {hosts} 核")
a.set_xscale("log", base=2)
a.set_xticks([64, 128, 256, 512, 1024], labels=["64", "128", "256", "512", "1024"])
a.set_xlabel("总并发（主机核数 × 每核 W）")
a.set_ylabel("吞吐 (Mmsg/s)")
a.set_title("(a) 加主机核：卡 4 核，租约版\n（核 41–43 没固定频率，每点一次）", fontsize=9.5)
a.legend(fontsize=7.5, loc="upper left")

shade = {64: "#cbd5e1", 128: "#94a3b8", 256: "#64748b", 512: WC, 1024: "#0f172a"}
for w, pts in cha.items():
    b.scatter(*zip(*pts), s=14, color=shade[w], label=f"W={w}", zorder=3)
line_x = [13.1, max(xs)]
b.plot(line_x, [icept + slope * x for x in line_x], color=WC, lw=1, linestyle="--", zorder=2)
b.annotate(f"外推 ≈{icept + slope * 13.1:.0f}", (13.1, icept + slope * 13.1), xytext=(16, 92), fontsize=9, color=WC,
           arrowprops=dict(arrowstyle="-", color=WC, lw=0.8))
b.set_xlabel("这 100 ms 的吞吐 (Mmsg/s)")
b.set_ylabel("CHA 里还没完成的 MOVDIR64B（平均条数）")
b.set_title(f"(b) 主机 CHA 里等着的请求，写合并卡 1 核\n（W=512 的 {len(xs)} 段：r = {corr:.3f}）", fontsize=9.5)
b.legend(fontsize=7.5, loc="upper right")

ws = [64, 128, 256, 512, 1024]
for kind, color, label, dx in (("read", GREY, "直接读卡进度（卡上 mrv17b 版）", 0.94), ("scan", WC, "先扫回复环、再读（卡上 11-13 原版）", 1.06)):
    lo, med, hi = zip(*(lag[(kind, w)] for w in ws))
    xpos = [w * dx for w in ws]
    c.errorbar(xpos, med, yerr=[[m - l for m, l in zip(med, lo)], [h - m for m, h in zip(med, hi)]], fmt="o", ms=5,
               color=color, capsize=3, label=label)
c.set_xscale("log", base=2)
c.set_xticks(ws, labels=[str(w) for w in ws])
c.set_xlabel("窗口 W")
c.set_ylabel("主机发了、读回的卡进度还没到的条数（中位，p10–p90）")
c.set_title("(c) 读回的卡进度落后几条，写合并卡 1 核\n（每 5 万条经 BAR4 读一次卡的进度字）", fontsize=9.5)
c.legend(fontsize=7.5, loc="upper left")
fig.suptitle("MRV-44 评审核对：主机核 40 固定 3.9 GHz（(a) 里另加的核 41–43 没固定），uncore 2.2 GHz，64 B，批大小 1；所有点 lost/bad/torn 全 0", fontsize=9.5)
print(save(fig, "mrv-44-review"))
print("W512 fit", round(icept, 1), round(slope, 2), "r", round(corr, 4), "at 13.1:", round(icept + slope * 13.1))
print({k: v for k, v in sorted(lanes.items())})
print({k: v for k, v in sorted(lag.items())})
