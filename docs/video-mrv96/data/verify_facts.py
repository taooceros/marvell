"""Recompute every on-screen number of the MRV-96 video from the raw runs (read-only).

    uv run -q --with matplotlib python docs/video-mrv96/data/verify_facts.py

Reads logs/mrv{24,50,53,54,55,61,70,76}-*/ of this checkout. Every selected run is checked for
lost = bad = torn = 0 (and the card-side partial/corrupt counters where the run records them);
the script stops on the first run that fails. The MRV-53 slope/phase numbers reuse the analysis
functions of docs/figures/mrv-53-slopes.py and mrv-53-phases.py, and MRV-24 the run selection of
docs/figures/mrv-24-summary.py, with their plotting helper stubbed out so nothing is written.
"""

import importlib
import json
import re
import statistics as st
import sys
import types
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
LOGS = ROOT / "logs"
FIG = ROOT / "docs" / "figures"
sys.modules["mrvplot"] = types.SimpleNamespace(plt=plt, save=lambda fig, name: f"(not saved) {name}")
sys.path.insert(0, str(FIG))


def jsonl(p):
    return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]


def med(xs):
    return st.median(list(xs))


def show(tag, value, note=""):
    print(f"{tag:<34} {value}{'   # ' + note if note else ''}")


def mrv24():
    m = importlib.import_module("mrv-24-summary")
    wc = [r for r in m.RUNS if r["cfg"] == (1, 1, "wc")]  # 1 receive + 1 write card core, WC replies
    alone = [r["rate"] for r in wc if r["dir"] == "h2c" and r["sender"] in m.FIXED]
    bidir = [r["rate"] for r in wc if r["dir"] == "bidir" and r["sender"] in m.FIXED]
    slow = [r for r in wc if r["dir"] == "bidir" and r["sender"] not in m.FIXED and r["ghz"] and r["ghz"] < m.SLOW_GHZ]
    print("== MRV-24 / MRV-84 (WC 1 recv + 1 write card core; verified by mrv-24-summary.verified)")
    show("h2c alone, sender 3.9 GHz", f"{med(alone):.1f} M/s (n={len(alone)}, {min(alone)}-{max(alone)})")
    show("bidir, sender 3.9 GHz", f"{med(bidir):.1f} M/s (n={len(bidir)}, {min(bidir)}-{max(bidir)})")
    ghz = [r["ghz"] for r in slow]
    show("bidir, sender unpinned", f"{med(r['rate'] for r in slow):.1f} M/s (n={len(slow)}) at {min(ghz):.2f}-{max(ghz):.2f} GHz")


def mrv61():
    rs = jsonl(LOGS / "mrv61-2026-11-18/runs.jsonl")
    for r in rs:
        h, c = r["host"], r["card"]
        assert h["bad"] == 0 and h["torn"] == 0 and h["late"] == 0, r["id"]
        assert c["sunk"] == r["cells"] and c["corrupt"] == 0 and c["partial"] == 0, r["id"]
    print(f"== MRV-61 / MRV-85 (h2c only, 2 card ring cores, 2e8 cells per run; {len(rs)} runs verified)")
    for gap, modes in ((0, ("none",)), (1000, ("spin",)), (1000, ("spin", "private", "ring")), (3000, ("spin",))):
        sub = [r for r in rs if r["dir"] == "h2c" and r["poll_gap_ns"] == gap and r["poll_gap_mode"] in modes]
        took = [r["host"]["card_took_mps"] for r in sub]
        cha = med(r["perf"]["cha_mean_entries"] for r in sub)
        res = med(r["perf"]["cha_residency_ns"] for r in sub) / 1e3
        cyc = med(r["perf"]["core_all"]["CPU40"]["cycles_per_cell"] for r in sub)
        cores = [c for r in sub for c in r["card"]["ring_cores"]]
        empty = st.fmean(c["empty_poll_pct"] for c in cores)
        per_poll = st.fmean(c["cells_per_busy_poll"] for c in cores)
        per_pause = st.fmean(c["cells"] / c["pauses"] for c in cores) if all(c["pauses"] for c in cores) else 0
        show(f"pause {gap} ns {'/'.join(modes)}",
             f"{med(took):.1f} M/s ({min(took)}-{max(took)}, n={len(took)}) | CHA {cha:.0f} entries {res:.2f} us"
             f" | cpu40 {cyc:.1f} cyc/cell | empty polls {empty:.1f}% | {per_poll:.1f} cells/busy poll"
             + (f" | {per_pause:.0f} cells per pause" if per_pause else ""))


def mrv76():
    rs = jsonl(LOGS / "mrv76-2026-11-18/runs.jsonl")
    for r in rs:
        h, c = r["host"], r["card"]
        assert h["bad"] == 0 and h["torn"] == 0 and h["late"] == 0, r["id"]
        assert c["sunk"] == r["cells"] and c["corrupt"] == 0 and c["partial"] == 0 and c["unroutable"] == 0, r["id"]
    print(f"== MRV-76 (h2c, 2 card ring cores, pause 3 us, vary what the core reads in the pause; {len(rs)} runs verified)")
    for d, gap, mode in (("h2c", 0, "none"), ("h2c", 3000, "spin"), ("h2c", 3000, "cold-private"),
                         ("h2c", 3000, "cold-private-4k"), ("h2c", 3000, "far-ring"), ("h2c", 3000, "far-ring-4k"),
                         ("h2c", 3000, "near-ring"), ("h2c", 3000, "next-1"), ("h2c", 3000, "write-band")):
        sub = [r for r in rs if r["dir"] == d and r["poll_gap_ns"] == gap and r["poll_gap_mode"] == mode]
        took = [r["host"]["card_took_mps"] for r in sub]

        def rate(r, ev):
            x = r["card"]["pmu"]["tad"][ev]
            return x["val"] / (x["enabled_ns"] / 1e9) / 1e6

        req = med(rate(r, "tad_req_msh_in_any") for r in sub)
        snp = med(r["card"]["pmu"]["tad"]["tad_snp_msh_out_any"]["val"] for r in sub) / 1e6
        res = med(r["perf"]["cha_residency_ns"] for r in sub) / 1e3
        show(f"{mode} ({gap} ns)", f"{med(took):.2f} M/s ({min(took)}-{max(took)}, n={len(took)}) | TAD req {req:.0f} M/s"
             f" | TAD snoops {snp:.1f} M | CHA residency {res:.2f} us")


def mrv50():
    rs = {r["id"]: r for r in jsonl(LOGS / "mrv50-2026-11-17/runs.jsonl")}
    print("== MRV-50 / MRV-86 (closed loop, WC 4 card cores, card-side stamps; segment p50 from stamp_summary)")
    for i in (188, 189, 190, 191, 113, 114, 99, 100, 101, 102):
        r = rs[i]
        h = r["host"]
        assert r["rc"] == 0 and h["lost"] == 0 and h["bad"] == 0 and h["torn"] == 0, i
        line = f"{r['tag']:<32} {h['mmsg']:6.3f} M/s RTT p50 {h['rtt_p50']:.2f} us"
        s = r.get("stamp_summary")
        if s:
            line += (f" | h2c {s['h2c_sync_ns']['p50'] / 1e3:.2f} card {s['card_tot_ns']['p50'] / 1e3:.2f}"
                     f" c2h {s['c2h_sync_ns']['p50'] / 1e3:.2f} us | host send gap p50 {s['d_send_ns']['p50']:.1f} ns"
                     f" | card arrival gap mean {s['d_card_in_ns']['mean']:.1f} ns"
                     f" | Little h2c in flight {h['mmsg'] * s['h2c_sync_ns']['p50'] / 1e3:.1f}")
        show(f"run {i}", line)


def mrv53():
    sl = importlib.import_module("mrv-53-slopes")
    ph = importlib.import_module("mrv-53-phases")
    warm = sl.analyse(sl.load(), warm=True)
    print("== MRV-53 / MRV-88 (closed loop WC; +delta busy-wait slopes, warm runs id >= 210; 2e7 msgs per W=64/1024 run)")
    for key in (("WC1", 1), ("WC1", 1024), ("WC4", 64), ("WC4", 1024)):
        d = warm[key]
        host = {}
        for x, y in d["host"]:
            host.setdefault(round(x), []).append(y)
        card = {}
        for x, y in d["card"]:
            card.setdefault(round(x), []).append(y)
        show(f"{key[0]} W={key[1]} base", f"{d['b']:.1f} ns/msg = {1e3 / d['b']:.2f} M/s | host slope {d['host_fit']:.2f}"
             f" card slope {d['card_fit']:.2f}")
        show("   host +delta -> ns/msg", ", ".join(f"+{x}: {st.fmean(v):.1f}" for x, v in sorted(host.items())))
        show("   card +delta -> ns/msg", ", ".join(f"+{x}: {st.fmean(v):.1f}" for x, v in sorted(card.items())))
    phases = ph.analyse_phases(ph.load_all())
    for key in (("WC1", 1024, "steady"), ("WC4", 1024, "steady"), ("WC4", 64, "steady")):
        d = phases[key]
        show(f"phases {key[0]} W={key[1]}", f"{d['untimed_mmsg']:.1f} M/s | host send {d['send_ns']:.1f} ns + busy poll"
             f" {d['bpoll_ns']:.1f} ns per msg")


def mrv70():
    rs = jsonl(LOGS / "mrv70-2026-11-18/runs.jsonl")
    for r in rs:
        assert r["rc"] == 0 and r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
    print(f"== MRV-70 / MRV-87 (1 host core cpu40; 300 ms runs, 3 reps; {len(rs)} runs verified)")

    def wcmd(r):
        m = re.search(r"-w (\d+)", r["cmd"])
        return int(m.group(1)) if m else None

    for cfg in ("1c_wc_duplex", "4c_wc_duplex"):
        for w in (32, 64, 256, 1024):
            v = [r["h2c_win_mps"] for r in rs if r["series"] == "closed_loop" and r["card_cfg"] == cfg and wcmd(r) == w]
            show(f"closed {cfg} W={w}", f"{med(v):.1f} M/s ({min(v)}-{max(v)})")
    for cfg in ("1c_wc_duplex", "1p1_wc_split", "1p1_dpi_split", "2p2_wc_split", "4c_wc_duplex"):
        for rate in (0, 13.5, 20, 35):
            sub = [r for r in rs if r["series"] == "sym_bidir" and r["card_cfg"] == cfg and r["h2c_target_mps"] == rate]
            hw = [r["h2c_win_mps"] for r in sub]
            cw = [r["c2h_win_mps"] for r in sub]
            tot = [a + b for a, b in zip(hw, cw)]
            q = lambda k, p: med(r[k][p] for r in sub)  # noqa: E731
            show(f"duplex {cfg} R={rate}", f"h2c {med(hw):.1f} c2h {med(cw):.1f} sum {med(tot):.1f} ({min(tot):.1f}-{max(tot):.1f})"
                 f" | h2c p50 {q('h2c_tot_us', 'p50'):.2f} p90 {q('h2c_tot_us', 'p90'):.2f} p99 {q('h2c_tot_us', 'p99'):.1f}"
                 f" | c2h p50 {q('c2h_tot_us', 'p50'):.2f} p99 {q('c2h_tot_us', 'p99'):.1f} us")
    for cfg in ("1c_wc_duplex", "4c_wc_duplex"):
        for rate in (5, 10, 13.5, 20, 35):
            sub = [r for r in rs if r["series"] == "open_echo" and r["card_cfg"] == cfg and r["h2c_target_mps"] == rate]
            show(f"echo {cfg} R={rate}", f"RTT p50 {med(r['echo_rtt_us']['p50'] for r in sub):.2f}"
                 f" p99 {med(r['echo_rtt_us']['p99'] for r in sub):.2f} us")


def mrv54():
    rs = jsonl(LOGS / "mrv54-2026-11-18/runs.jsonl")
    for r in rs:
        h = r["host"]
        assert r["rc"] == 0 and h["lost"] == 0 and h["bad"] == 0 and h["torn"] == 0
    print(f"== MRV-54 / MRV-89 (closed loop WC 4 card cores, 2e7 msgs x 4 passes; {len(rs)} runs verified)")
    for w in (64, 1024):
        for g in ("base", "base-interleaved"):
            sub = [r for r in rs if r["window"] == w and r["group"] == g]
            show(f"W={w} {g}", f"{st.fmean(r['host']['mmsg'] for r in sub):.2f} M/s | ItoM/reply"
                 f" {st.fmean(r['iio']['itom_per_reply'] for r in sub):.4f} | ItoM share of line writes"
                 f" {100 * st.fmean(r['iio']['itom_of_line_writes'] for r in sub):.2f}%")


def mrv55():
    rs = jsonl(LOGS / "mrv55-2026-11-18/runs.jsonl")
    for r in rs:
        assert r["lost"] == 0 and r["bad"] == 0 and r["torn"] == 0
    print(f"== MRV-55 / MRV-90 (two card cores each write their own 64 B cell, 1 s x 5; {len(rs)} runs verified)")
    idle = st.fmean(r["tad"]["tad_snp_msh_out_any"]["per_s"] / 1e6 for r in rs if r["mode"] == "idle")
    show("idle TAD snoops", f"{idle:.2f} M/s")
    for d in (0, 64, 128, 256, 4096):
        sub = [r for r in rs if r["mode"] == "write_only" and r["dist_bytes"] == d]
        c0 = st.fmean(r["core0_mops"] for r in sub)
        c1 = st.fmean(r["core1_mops"] for r in sub)
        snp = st.fmean(r["tad"]["tad_snp_msh_out_any"]["per_s"] / 1e6 for r in sub)
        show(f"write_only d={d} B", f"per core {(c0 + c1) / 2:.1f} M/s (core3 {c0:.1f}, core4 {c1:.1f}) | snoops {snp:.2f} M/s")

def _log_path(rel: str) -> Path:
    for base in (LOGS, ROOT.parent / "marvell/logs", ROOT.parent / "marvell-mrv106/logs"):
        p = base / rel
        if p.exists():
            return p
    raise FileNotFoundError(rel)


def mrv97():
    rs = jsonl(_log_path("mrv97-2026-11-19/runs.jsonl"))
    for r in rs:
        assert r["host"]["lost"] == 0 and r["host"]["bad"] == 0 and r["host"]["torn"] == 0
    print(f"== MRV-97 (W=1 897 ns baseline & 3 cache/DDIO shortcuts; {len(rs)} runs verified)")
    by = {}
    for r in rs:
        by.setdefault(r["cond"], []).append(r)
    hi = lambda c, fn: med(fn(r) for r in by[c] if r["clocks"]["cpu40_ghz"] >= 3.85)
    base = hi("base", lambda r: r["host"]["rtt_ns"]["p50"])
    cold = hi("A_cold_src", lambda r: r["host"]["rtt_ns"]["p50"])
    show("base W=1 RTT p50", f"{base:.1f} ns")
    show("cold host src RTT p50", f"{cold:.1f} ns ({cold - base:+.1f} ns)")
    b0 = hi("B0_card_hot_wait", lambda r: r["card"]["card_read_ns"]["p50"])
    b1 = hi("B1_card_cold_pre", lambda r: r["card"]["card_read_ns"]["p50"])
    b2 = hi("B2_card_dram_ctrl", lambda r: r["card"]["card_read_ns"]["p50"])
    tad0 = hi("B0_card_hot_wait", lambda r: r["card"]["tad"]["tad_alloc_ltg"]["val"] / r["card"]["pmu_rounds"])
    tad1 = hi("B1_card_cold_pre", lambda r: r["card"]["tad"]["tad_alloc_ltg"]["val"] / r["card"]["pmu_rounds"])
    show("card L3 read (hot/cold/DRAM)", f"{b0:.1f} / {b1:.1f} / {b2:.1f} ns (DRAM {b2 - b0:+.1f} ns) | tad_alloc {tad0:.2f} -> {tad1:.2f} ({tad1 - tad0:+.2f})")
    c0 = hi("C0_host_hot_wait", lambda r: r["host"]["read_ns"]["p50"])
    c1 = hi("C1_host_cold_pre", lambda r: r["host"]["read_ns"]["p50"])
    c2 = hi("C2_host_dram_ctrl", lambda r: r["host"]["read_ns"]["p50"])
    ihit = hi("C0_host_hot_wait", lambda r: r["host_perf_s1"]["unc_cha_tor_inserts.io_hit_itom"]["val"] / max(1, r["host_perf_s1"]["unc_cha_tor_inserts.io_itom"]["val"]))
    show("host DDIO read (hot/cold/DRAM)", f"{c0:.1f} / {c1:.1f} / {c2:.1f} ns (DRAM {c2 - c0:+.1f} ns) | io_hit_itom {ihit:.2f}")


def mrv95():
    rs = jsonl(_log_path("mrv95-2026-11-19/runs.jsonl"))
    print(f"== MRV-95 (Card DPI DMA vs WC -> host Intel DSA dsa2/wq2.0; {len(rs)} rows)")
    dpi_ops = 0
    for r in rs:
        if r["submitter"] == "card_dpi_dma":
            assert r.get("lost", 0) == 0 and r.get("bad", 0) == 0
            dpi_ops += r.get("ops", r.get("rounds", 0))
    show("card_dpi_dma total verified ops", f"{dpi_ops:,} (0 lost, 0 bad)")
    on_die_nop = med(r["p50_ns"] for r in rs if r["submitter"] == "host_movdir64b" and r["mode"] == "w1_lat" and r["op"] == "nop")
    show("host on-die DSA2 NOP p50", f"{on_die_nop:.1f} ns")
    for sub in ("card_pem_wc_none", "card_pem_wc_dgh", "card_pem_wc_dsb"):
        rows = [r for r in rs if r["mode"] == "w1_lat" and r["submitter"] == sub]
        bad, n = sum(r["bad"] for r in rows), sum(r["rounds"] for r in rows)
        show(f"{sub} silent drops", f"{bad}/{n} ({100 * bad / n:.3f}%)")


def mrv99():
    rs = jsonl(_log_path("mrv99-2026-11-19/runs.jsonl"))
    for r in rs:
        assert r.get("lost", 0) == 0 and r.get("bad", 0) == 0
    print(f"== MRV-99 (Reverse P2P completion -> Card BAR4 vs Host RAM; {len(rs)} runs verified)")
    for op, sz in (("nop", 0), ("memmove", 4096)):
        h = med(r["cycle_ns"] / 1000.0 for r in rs if r["mode"] == "rate" and r["window"] == 1 and r["op"] == op and r["size"] == sz and r["comp_target"] == "host_memzone")
        c = med(r["cycle_ns"] / 1000.0 for r in rs if r["mode"] == "rate" and r["window"] == 1 and r["op"] == op and r["size"] == sz and r["comp_target"] == "card_bar4")
        show(f"W=1 period {op} {sz}B", f"host {h:.2f} us -> BAR4 {c:.2f} us")
    for tgt in ("host_memzone", "card_bar4"):
        sub = [r for r in rs if r["mode"] == "w1_lat" and r["op"] == "memmove" and r["size"] == 4096 and r["comp_target"] == tgt]
        p50, p99 = med(r["p50_ns"] for r in sub), med(r["p99_ns"] for r in sub)
        show(f"w1_lat 4KiB {tgt}", f"p50 {p50 / 1000:.2f} us | p99 {p99 / 1000:.2f} us | jitter {p99 - p50:.0f} ns")


def mrv106():
    rs = jsonl(_log_path("mrv106-2026-11-19/runs.jsonl"))
    for r in rs:
        assert r.get("lost", 0) == 0 and r.get("bad", 0) == 0 and r.get("dyn_lost", 0) == 0 and r.get("dyn_bad", 0) == 0
    print(f"== MRV-106 (DSA Batch B=1..32, 4 KiB, W=64 ops, hot 512 KiB src, CC=0; {len(rs)} runs verified)")
    for tgt, bs in (("card_bar4", 1), ("card_bar4", 2), ("card_bar4", 4), ("card_bar4", 8), ("card_bar4", 16), ("card_bar4", 32), ("host_memzone", 1), ("host_memzone", 16)):
        sub = [r for r in rs if r["mode"] == "rate" and r["comp_target"] == tgt and r["batch_size"] == bs]
        show(f"rate {tgt} B={bs}", f"{med(r['gb_s'] for r in sub):.2f} GB/s | {med(r['cycle_ns'] for r in sub):.1f} ns/op")
    for tgt in ("card_bar4", "host_memzone"):
        sub = [r for r in rs if r["mode"] == "w1_lat" and r["comp_target"] == tgt and r["batch_size"] == 16]
        show(f"w1_lat B=16 {tgt}", f"p50 {med(r['p50_ns'] for r in sub) / 1000:.2f} us | p99 {med(r['p99_ns'] for r in sub) / 1000:.2f} us")


if __name__ == "__main__":
    for fn in (mrv24, mrv61, mrv76, mrv50, mrv53, mrv70, mrv54, mrv55, mrv97, mrv95, mrv99, mrv106):
        fn()
