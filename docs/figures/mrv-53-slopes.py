"""MRV-53 check 1: how much the time per message (1 / throughput) grows when the host or the card
busy-waits a known extra time delta per message, and how throughput behaves when the card holds
each reply back by D = 0 / 0.5 / 1 / 2 us without busy-waiting. Reads only the raw runs in
logs/mrv53-2026-11-17/runs.jsonl (group "mrv53"); prints the table and draws docs/figures/mrv-53-slopes.png.

    uv run --with matplotlib python docs/figures/mrv-53-slopes.py
"""

import json
import re
import statistics as st
import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent))
import mrvplot  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / 'logs/mrv53-2026-11-17/runs.jsonl'
BLUE, ORANGE = '#1f77b4', '#d95f02'

CONFIG_ORDER = ['WC1', 'WC4', 'ST1', 'ST4', 'DPI1', 'DPI4']
CONFIG_NAME = {
    'WC1': '写合并 1核',
    'WC4': '写合并 4核',
    'ST1': '直写 1核',
    'ST4': '直写 4核',
    'DPI1': 'DPI 1核',
    'DPI4': 'DPI 4核',
}
CORES = {'WC1': 1, 'WC4': 4, 'ST1': 1, 'ST4': 4, 'DPI1': 1, 'DPI4': 4}
HOLDS = (0, 500, 1000, 2000)
WARM_S = 7.0  # runs that start earlier after the card program than this are the start-up state


HOST_RE = re.compile(
    r'window (\d+) rounds (\d+) lost (\d+) bad (\d+) torn (\d+) late (\d+) \(max ([\d.]+) us\) \| ([\d.]+) Mmsg/s'
    r' \| RTT us: min ([\d.]+) p50 ([\d.]+) p90 ([\d.]+) p99 ([\d.]+) p99\.9 ([\d.]+) p99\.99 ([\d.]+)'
    r' max ([\d.]+) mean ([\d.]+) \| polls (\d+)% empty'
)
HOST_KEYS = [
    'window', 'rounds', 'lost', 'bad', 'torn', 'late', 'late_max_us', 'mmsg',
    'rtt_min', 'rtt_p50', 'rtt_p90', 'rtt_p99', 'rtt_p999', 'rtt_p9999', 'rtt_max', 'rtt_mean', 'empty_pct',
]


def load():
    runs = []
    for line in RUNS.read_text().splitlines():
        e = json.loads(line)
        if e.get('group') != 'mrv53' or e.get('rc') != 0:
            continue
        if not isinstance(e.get('host'), dict):
            m = HOST_RE.search((RUNS.parent / 'out' / f'{e["id"]:04d}.host').read_text())
            e['hostbin'], e['host'] = e['host'], {k: float(v) for k, v in zip(HOST_KEYS, m.groups())}
        if e['host']['lost'] or e['host']['bad'] or e['host']['torn']:
            raise SystemExit(f'run {e["id"]} did not verify')
        runs.append(e)
    return runs


# Runs before this id used superseded binaries (unfenced busy waits, hold without its control).
FIRST_ID = 210
HOST_DELAY_RE = re.compile(r'host delay (\d+) ns: (\d+) rounds, measured ([\d.]+) ns')
CARD_DELAY_RE = re.compile(r'inline_reflector: delay (\d+) ns: (\d+) rounds, measured ([\d.]+) ns')


def achieved_host_delay(e):
    """The measured fenced delay this run used (0 = none)."""
    if not (e.get('host_extra') or '').startswith('--host-delay-ns'):
        return 0.0
    m = HOST_DELAY_RE.search((RUNS.parent / 'out' / f'{e["id"]:04d}.host').read_text())
    return float(m.group(3))


def achieved_card_delay(e):
    p = RUNS.parent / 'out' / f'card-s{e["session"]:03d}.txt'
    if not p.exists():
        return None
    m = CARD_DELAY_RE.search(p.read_text())
    return float(m.group(3)) if m else None


def ns_per_msg(e):
    return 1e3 / e['host']['mmsg']


def analyse(runs, warm=True):
    """Per (config, W): baseline, card-delay, host-delay and hold numbers from like-conditioned runs.
    Points are (achieved delay ns, ns per message)."""
    out = {}
    for e in runs:
        if e['id'] < FIRST_ID or 'config' not in e or (e['since_card_start_s'] >= WARM_S) != warm:
            continue
        key = (e['config'], e['W'])
        d = out.setdefault(key, {'base': [], 'prod': [], 'card': [], 'host': [], 'hold': {}, 'rtt0': []})
        y = ns_per_msg(e)
        if e['card'] == 'C0':
            d['prod'].append(y)
        elif e['card_knob'] == 'none' and e['hostbin'] == 'H0':
            d['base'].append(y)
            d['rtt0'].append(e['host']['rtt_mean'])
        elif e['card_knob'] == 'none' and e['hostbin'] == 'H1':
            d['host'].append((achieved_host_delay(e), y))
        elif e['card_knob'] == 'delay' and e['hostbin'] == 'H0':
            cd = achieved_card_delay(e)
            if cd is not None:
                d['card'].append((cd, y))
        elif e['card_knob'] == 'hold' and e['hostbin'] == 'H0':
            d['hold'].setdefault(e['card_knob_ns'], []).append(y)
    for (config, w), d in out.items():
        d['b'] = st.fmean(d['base']) if d['base'] else float('nan')
        d['b_sd'] = st.stdev(d['base']) if len(d['base']) > 1 else 0.0
        d['rtt_base'] = st.fmean(d['rtt0']) if d['rtt0'] else float('nan')
        h0 = [y for x, y in d['host'] if x == 0]
        d['h0'] = st.fmean(h0) if h0 else d['b']
        d['h0_sd'] = st.stdev(h0) if len(h0) > 1 else d['b_sd']
        d['card_fit'], d['card_jumps'] = fit(d['card'], d['b'], d['b_sd'])
        d['host_fit'], d['host_jumps'] = fit([p for p in d['host'] if p[0] > 0], d['h0'], d['h0_sd'])
    return out


def fit(points, base, base_sd):
    """Slope of (ns per message - base) on the added delay, through 0, over the points that stay in the
    base state. In a fixed regime, adding busy-wait x >= 0 increases ns/msg by between 0 (within -tol)
    and x + 4.5 ns (the one-time ISB/LFENCE pipeline serialization cost measured at W=1). A point that
    speeds up (dy < -tol) or jumps above x + 8.5 + tol is a state transition: reported, not fitted."""
    tol = 3 * base_sd + 0.01 * base + 1.5
    kept, jumps = [], []
    for x, y in points:
        dy = y - base
        (kept if -tol <= dy <= x + 8.5 + tol else jumps).append((x, y))
    sxx = sum(x * x for x, _ in kept)
    s = sum(x * (y - base) for x, y in kept) / sxx if sxx else float('nan')
    return s, jumps


def table(res):
    for (config, w), d in sorted(res.items(), key=lambda kv: (CONFIG_ORDER.index(kv[0][0]), kv[0][1])):
        def m(ys):
            return f'{st.fmean(ys):7.1f}' if ys else '    -  '

        def pts(ps):
            return ' '.join(f'{x:.0f}:{y:.1f}' for x, y in sorted(ps))

        print(
            f'{config:5s} W={w:<5d} base {m(d["base"])} ns ({1e3 / d["b"]:5.1f} M/s, RTT {d["rtt_base"]:5.2f} us, sd {d["b_sd"]:.1f}, n={len(d["base"])})'
            f' product card {m(d["prod"])}'
        )
        print(
            f'      card δ  slope {d["card_fit"]:5.2f} (x{CORES[config]} cores {d["card_fit"] * CORES[config]:5.2f})'
            f' jumps {len(d["card_jumps"])}/{len(d["card"])}  [{pts(d["card"])}]'
        )
        print(
            f'      host δ  slope {d["host_fit"]:5.2f}  base H1 {d["h0"]:.1f}'
            f' jumps {len(d["host_jumps"])}/{len([p for p in d["host"] if p[0] > 0])}  [{pts(d["host"])}]'
        )
        print('      hold ns ' + ' '.join(f'{k}:{m(d["hold"].get(k, []))}' for k in HOLDS))
        print(
            '      hold M/s '
            + ' '.join(
                f'{k}:{1e3 / st.fmean(d["hold"][k]):5.2f}' if d['hold'].get(k) else f'{k}:  -  ' for k in HOLDS
            )
        )


def figure(warm, cold):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.5, 4.8), gridspec_kw={'width_ratios': [1.35, 1.0]})

    # Build ordered items for Panel (a): include WC1 W=64 start-up + all warm keys
    items = []
    for c in CONFIG_ORDER:
        for w in (1, 64, 1024):
            if c == 'WC1' and w == 64 and ('WC1', 64) in cold:
                items.append((f'{CONFIG_NAME[c]}\nW=64(起)', c, 64, cold[('WC1', 64)]))
            if (c, w) in warm:
                items.append((f'{CONFIG_NAME[c]}\nW={w}', c, w, warm[(c, w)]))

    xs = list(range(len(items)))
    ax1.axhline(1.0, color='0.55', lw=0.9, ls='--')
    ax1.axhline(0.0, color='0.55', lw=0.9)

    for off, side, color, marker, label in (
        (-0.14, 'host', BLUE, 'o', '主机每条多忙等 δ（δ = 10/20/40 ns）'),
        (0.14, 'card', ORANGE, 's', '卡每条回复多忙等 δ × 卡核数（δ = 31/45/60 ns）'),
    ):
        ys = []
        for _, c, w, d in items:
            mult = CORES[c] if side == 'card' else 1
            ys.append(d[f'{side}_fit'] * mult)
        ax1.plot([x + off for x in xs], ys, marker, color=color, markersize=6.5, label=label)
        for x, (_, c, w, d), y in zip(xs, items, ys):
            if d[f'{side}_jumps']:
                y_jmp = 1.38 if side == 'host' else 1.27
                ax1.annotate('跳', (x + off, y_jmp), color=color, ha='center', fontsize=8.5)

    ax1.set_xticks(xs, [it[0] for it in items], fontsize=7.2, rotation=25, ha='right')
    ax1.set_ylabel('每条消息间隔增量 ÷ δ（斜率）')
    ax1.set_ylim(-0.28, 1.48)
    ax1.set_title('(a) 忙等延迟 δ 的斜率（≈1 在关键路径，≈0 被掩盖，“跳” = 触发状态跳变）', fontsize=9.5)
    ax1.legend(frameon=False, loc='lower left', fontsize=8)

    # Panel (b): Non-busy hold D = 0, 0.5, 1.0, 2.0 us (throughput Mmsg/s vs D us) for W=1024 and W=64
    ds_us = [h / 1e3 for h in HOLDS]
    palette = {
        'WC1': '#1f77b4',
        'WC4': '#2563eb',
        'ST1': '#d95f02',
        'ST4': '#ea580c',
        'DPI1': '#16a34a',
        'DPI4': '#059669',
    }
    for c in CONFIG_ORDER:
        for w, ls, mk in ((1024, '-', 'o'), (64, '--', '^')):
            if (c, w) not in warm:
                continue
            d = warm[(c, w)]
            if not all(h in d['hold'] for h in HOLDS):
                continue
            rates = [1e3 / st.fmean(d['hold'][h]) for h in HOLDS]
            lbl = f'{CONFIG_NAME[c]} W={w}' if w == 1024 or c in ('WC1', 'WC4', 'DPI4') else None
            ax2.plot(ds_us, rates, f'{ls}{mk}', color=palette[c], markersize=5, lw=1.5, alpha=0.9 if w == 1024 else 0.65, label=lbl)

    ax2.set_xticks(ds_us, ['0\n(队列对照)', '0.5', '1.0', '2.0'])
    ax2.set_xlabel('卡上非忙等延后发送时间 D (µs)')
    ax2.set_ylabel('实测吞吐 (Mmsg/s)')
    ax2.set_ylim(0, 42)
    ax2.set_title('(b) 非忙等延后发送 D：窗口够大（实线 W=1024）时吞吐不变', fontsize=9.5)
    ax2.legend(frameon=False, loc='upper right', fontsize=7.2, ncol=2)

    fig.suptitle('MRV-53 检查 1：受控忙等延迟 δ 与非忙等延后发送 D（64 B，主机批 1，核 40 @ 3.9 GHz，uncore 2.2 GHz，卡核 2.0 GHz）', fontsize=10)
    return mrvplot.save(fig, 'mrv-53-slopes')


if __name__ == '__main__':
    runs = load()
    warm = analyse(runs, warm=True)
    print('== steady (runs start >= %.0f s after the card program)' % WARM_S)
    table(warm)
    cold = analyse(runs, warm=False)
    if cold:
        print('== start-up (runs start < %.0f s after the card program)' % WARM_S)
        table(cold)
    print('wrote', figure(warm, cold))
