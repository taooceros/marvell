"""E1: per-domain numbers from the perf -I 100 CSVs (';' separated, socket S1 for uncore)."""
import collections
import glob
import json
import re
import statistics
import sys

NAMES = {
    "uncore_cha/event=0x35,umask=0xc867ff01/": "movdir_ins", "uncore_cha/event=0x36,umask=0xc867ff01/": "movdir_occ",
    "uncore_cha/event=0x35,umask=0xcc43ff04/": "itom_ins", "uncore_cha/event=0x36,umask=0xcc43ff04/": "itom_occ",
    "uncore_iio_1/event=0x84,ch_mask=0xff,fc_mask=7,umask=1/": "iio_in_wr",
    "uncore_iio_1/event=0xc1,ch_mask=0xff,fc_mask=7,umask=1/": "iio_out_wr",
    "uncore_iio_1/event=0x86,ch_mask=0xff,fc_mask=7,umask=0x10/": "arb_req_wr",
    "uncore_iio_1/event=0x87,ch_mask=0xff,fc_mask=7,umask=0x10/": "arb_won_wr",
    "uncore_irp_1/event=0x1/": "irp_clk", "uncore_irp_1/event=0x11,umask=8/": "irp_wr_pref",
    "uncore_irp_1/event=0x1f,umask=0x10/": "lost_fwd", "uncore_irp_1/event=0x12,umask=0x78/": "snp_hit_m",
    "uncore_irp_1/event=0x12,umask=0x7e/": "snp_hit", "uncore_irp_1/event=0x12,umask=0x71/": "snp_miss",
    "uncore_m2pcie_1/event=0x1/": "m2p_clk", "uncore_m2pcie_1/event=0x11,umask=0x80/": "m2p_rxc_ins",
    "uncore_m2pcie_1/event=0x34,umask=0x10/": "m2p_rej_ncb", "uncore_m2pcie_1/event=0x34,umask=0x20/": "m2p_rej_ncs",
}
# arrival counter per group (messages per interval), None = no per-interval rate in the group
RATE = {"cha_movdir": "movdir_ins", "cha_itom": "itom_ins", "iio": "iio_out_wr", "irp_clk": "irp_wr_pref",
        "irp_m": None, "irp_snp": None, "m2p": "m2p_rxc_ins", "core": "iio_out_wr",
        "irp_m+r": "iio_out_wr", "irp_snp+r": "iio_out_wr"}
UNCORE_GHZ = 2.2


def parse(path):
    rows, minrun = collections.OrderedDict(), 100.0
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        p = line.rstrip("\n").split(";")
        try:
            t = float(p[0])
        except ValueError:
            continue
        if p[1].startswith("S"):
            if p[1] != "S1":
                continue
            val, ev, rest = p[3], p[5], p[6:]
        else:
            val, ev, rest = p[1], p[3], p[4:]
        try:
            v = float(val)
        except ValueError:
            continue
        name = NAMES.get(ev, ev)
        d = rows.setdefault(t, {})
        d[name] = d.get(name, 0) + v
        try:
            minrun = min(minrun, float(rest[1]))
        except (IndexError, ValueError):
            pass
    out, prev = [], 0.0
    for t, d in rows.items():
        out.append((t, t - prev, d))
        prev = t
    return out, minrun


def load(group, w, suffix=""):
    base = f"/tmp/mrv17/e1-{group.replace('+', '_')}-w{w}{suffix}"
    if group == "core":
        a, ra = parse(base + "-core.csv")
        b, rb = parse(base + "-unc.csv")
        bt = [t for t, _, _ in b]
        merged = []
        for t, dt, d in a:
            j = min(range(len(bt)), key=lambda i: abs(bt[i] - t))
            merged.append((t, dt, {**d, **b[j][2]}))
        return merged, min(ra, rb)
    return parse(base + ".csv")


def steady(iv, key):
    """Intervals carrying traffic, minus the first two and the last one (start-up, tail)."""
    busy = [x for x in iv if x[2].get(key, 0) > 1e5]
    return busy[2:-1]


def classify(rate):
    return "slow" if rate < 20 else "fast" if rate > 30 else "mid"


def summarize(group, w, suffix=""):
    iv, minrun = load(group, w, suffix)
    key = RATE[group]
    res = {"minrun": minrun}
    if key is None:
        tot = collections.Counter()
        for _, _, d in iv:
            tot.update(d)
        res["total"] = dict(tot)
        return res
    by = collections.defaultdict(list)
    for t, dt, d in steady(iv, key):
        rate = d[key] / dt / 1e6
        by[classify(rate)].append((rate, dt, d))
    for cls, xs in by.items():
        agg = collections.Counter()
        for _, _, d in xs:
            agg.update(d)
        n = agg[key]
        m = {"intervals": len(xs), "rate": statistics.median(r for r, _, _ in xs),
             "rate_min": min(r for r, _, _ in xs), "rate_max": max(r for r, _, _ in xs)}
        if group == "cha_movdir":
            m["movdir_ns"] = agg["movdir_occ"] / n / UNCORE_GHZ
        if group == "cha_itom":
            m["itom_ns"] = agg["itom_occ"] / n / UNCORE_GHZ
        if group == "iio":
            m["in_per_req"] = agg["iio_in_wr"] / n
            m["arb_req_per_wr"] = agg["arb_req_wr"] / agg["iio_in_wr"]
            m["arb_won_per_wr"] = agg["arb_won_wr"] / agg["iio_in_wr"]
        if group == "irp_clk":
            m["irp_ghz"] = agg["irp_clk"] / sum(dt for _, dt, _ in xs) / 1e9
        if group == "m2p":
            m["m2p_ghz"] = agg["m2p_clk"] / sum(dt for _, dt, _ in xs) / 1e9
            m["rej_ncb_per_req"] = agg["m2p_rej_ncb"] / n
            m["rej_ncs_per_req"] = agg["m2p_rej_ncs"] / n
        if group in ("irp_m+r", "irp_snp+r"):
            for k in ("lost_fwd", "snp_hit_m", "snp_hit", "snp_miss"):
                if k in agg:
                    m[k + "_per_msg"] = agg[k] / n
        if group == "core":
            m["cyc"] = agg["cycles"] / n
            m["ins"] = agg["instructions"] / n
            m["ipc"] = agg["instructions"] / agg["cycles"]
            m["xsnp_fwd"] = agg["mem_load_l3_hit_retired.xsnp_fwd"] / n
            m["xsnp_no_fwd"] = agg["mem_load_l3_hit_retired.xsnp_no_fwd"] / n
            m["l3_miss"] = agg["mem_load_retired.l3_miss"] / n
            m["sb_stall_cyc"] = agg["resource_stalls.sb"] / n
        res[cls] = m
    return res


if __name__ == "__main__":
    runs = {}
    for line in open("/tmp/mrv17/runs.jsonl"):
        x = json.loads(line)
        if x["tag"].startswith("E1 ") and x.get("host") and x.get("rc") == 0:
            runs[x["tag"]] = x
    out = {}
    for tag, x in runs.items():
        m = re.match(r"E1 (\S+) wc n1 W(\d+)( r2)?$", tag)
        if not m:
            continue
        g, w, r2 = m.group(1), int(m.group(2)), m.group(3)
        if (g, w) in out and not r2:
            continue  # a rerun (r2) replaces the original row
        try:
            s = summarize(g, w, "-r2" if r2 else "")
        except FileNotFoundError as e:
            print("missing csv", tag, e, file=sys.stderr)
            continue
        h = x["host"][0]
        s["host"] = {k: h[k] for k in ("mmsg", "p50", "p99", "mean", "empty_pct", "lost", "bad", "torn", "rounds")}
        out[(g, w)] = s
    json.dump({f"{g} {w}": v for (g, w), v in sorted(out.items())}, open("/tmp/mrv17/e1-summary.json", "w"), indent=1)
    for (g, w), v in sorted(out.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        print(w, g, json.dumps({k: (round(x, 3) if isinstance(x, float) else x) for k, x in v.items() if k != "total"}, default=str)[:600])
        if "total" in v:
            msgs = v["host"]["rounds"] + 20000
            print("     run totals per message:", {k: round(c / msgs, 3) for k, c in v["total"].items()})
