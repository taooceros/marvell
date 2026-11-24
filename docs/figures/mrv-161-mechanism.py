#!/usr/bin/env python3
"""MRV-161 Figure 1: Ultra-clean, nearly wordless mechanism SVG generated from runs.jsonl.

Follows MRV-46 strictly: short labels + large key numbers, zero sentences/captions/footers,
greys plus one accent colour (#1d6fa5). All numbers computed live from runs.jsonl.
"""
import json
import statistics
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs/mrv161-2026-11-24/runs.jsonl"
HERE = Path(__file__).resolve().parent


def load_runs():
    rows = []
    for line in RUNS.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        assert r.get("verified") is True, f"unverified run: {r.get('cfg')}"
        rows.append(r)
    return rows


def med(rows, cfg, getter):
    vals = [getter(r) for r in rows if r["cfg"] == cfg]
    assert len(vals) == 3, f"expected 3 reps for {cfg}, got {len(vals)}"
    return statistics.median(vals)


def main():
    rows = load_runs()

    ldadda_empty_ns = med(rows, "cqe_probe_bare", lambda r: r["probe"]["eth_rx_empty_bare_ns"])
    peek_empty_ns = med(rows, "cqe_probe_bare", lambda r: r["probe"]["cqe_peek_empty_bare_ns"])
    direct_empty_ns = med(rows, "cqe_probe_bare", lambda r: r["probe"]["cqe_direct_empty_bare_ns"])
    ldadda_hit_ns = med(rows, "hw_gate_cqe_peek_diag_64_w1", lambda r: r["card"]["cqe_peek"]["ldadda_mean_ns"])

    speedup_peek = ldadda_empty_ns / peek_empty_ns
    speedup_direct = ldadda_empty_ns / direct_empty_ns

    w1_card_def = med(rows, "direct_default_64_w1", lambda r: r["card"]["host_rtt_p50_us"])
    w1_card_b4 = med(rows, "direct_bar4_first_64_w1", lambda r: r["card"]["host_rtt_p50_us"])
    w1_card_peek = med(rows, "direct_cqe_peek_64_w1", lambda r: r["card"]["host_rtt_p50_us"])
    w1_merc_dir = med(rows, "direct_cqe_direct_64_w1", lambda r: r["client"]["tot_p50_us"])

    w4_p99_def = med(rows, "direct_default_64_w4", lambda r: r["client"]["tot_p99_us"])
    w4_p99_b4 = med(rows, "direct_bar4_first_64_w4", lambda r: r["client"]["tot_p99_us"])
    w4_p99_peek = med(rows, "direct_cqe_peek_64_w4", lambda r: r["client"]["tot_p99_us"])

    w8_rate_def = med(rows, "direct_default_64_w8", lambda r: r["client"]["achieved_mrps"])
    w8_rate_b4 = med(rows, "direct_bar4_first_64_w8", lambda r: r["client"]["achieved_mrps"])
    w8_rate_dir = med(rows, "direct_cqe_direct_64_w8", lambda r: r["client"]["achieved_mrps"])

    b4_w4_p99_delta = w4_p99_b4 - w4_p99_def
    peek_vs_b4_w4_p99 = w4_p99_peek - w4_p99_b4
    b4_w8_pct = (w8_rate_b4 / w8_rate_def - 1.0) * 100.0
    dir_w8_pct = (w8_rate_dir / w8_rate_def - 1.0) * 100.0

    def fmt_signed(val, fmt):
        s = fmt.format(abs(val))
        return f"+{s}" if val >= 0 else f"\u2212{s}"

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1150 440" width="1150" height="440">
  <defs>
    <style>
      .f {{ font-family: "DejaVu Sans", "Droid Sans Fallback", sans-serif; }}
      .colhdr {{ font-size: 13px; font-weight: 700; fill: #64748b; }}
      .arm {{ font-size: 16px; font-weight: 700; fill: #1e293b; }}
      .arm-acc {{ font-size: 16px; font-weight: 700; fill: #1d6fa5; }}
      .tag {{ font-size: 11.5px; font-weight: 600; fill: #64748b; }}
      .tag-acc {{ font-size: 11.5px; font-weight: 700; fill: #1d6fa5; }}
      .box-t {{ font-size: 13px; font-weight: 600; fill: #1e293b; }}
      .box-tw {{ font-size: 13px; font-weight: 700; fill: #ffffff; }}
      .box-ta {{ font-size: 13px; font-weight: 700; fill: #1d6fa5; }}
      .chip {{ font-size: 11.5px; font-weight: 700; fill: #475569; }}
      .chip-acc {{ font-size: 11.5px; font-weight: 700; fill: #1d6fa5; }}
      .big {{ font-size: 24px; font-weight: 800; fill: #334155; }}
      .big-acc {{ font-size: 25px; font-weight: 800; fill: #1d6fa5; }}
      .metric-sub {{ font-size: 12px; fill: #475569; }}
    </style>
    <pattern id="hatch" width="8" height="8" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">
      <rect width="8" height="8" fill="#f1f5f9"/>
      <line x1="0" y1="0" x2="0" y2="8" stroke="#94a3b8" stroke-width="2.2"/>
    </pattern>
    <marker id="arr-g" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
      <path d="M 0 1 L 8 5 L 0 9 z" fill="#64748b"/>
    </marker>
    <marker id="arr-a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
      <path d="M 0 1 L 8 5 L 0 9 z" fill="#1d6fa5"/>
    </marker>
  </defs>

  <rect x="0" y="0" width="1150" height="440" fill="#ffffff"/>

  <!-- Column headers -->
  <text x="28" y="34" class="f colhdr">策略</text>
  <text x="192" y="34" class="f colhdr">空轮询（同步盯 BAR4）</text>
  <text x="560" y="34" class="f colhdr">新包到达（eth0 取包）</text>
  <text x="892" y="34" class="f colhdr">实测锚点</text>
  <line x1="28" y1="46" x2="1122" y2="46" stroke="#cbd5e1" stroke-width="1.5"/>

  <!-- ROW 1: default -->
  <g transform="translate(0, 62)">
    <text x="28" y="30" class="f arm">default</text>
    <text x="28" y="50" class="f tag">原装基线</text>

    <rect x="192" y="8" width="156" height="38" rx="5" fill="#475569"/>
    <text x="270" y="32" text-anchor="middle" class="f box-tw">LDADDA {ldadda_empty_ns:.1f} ns</text>
    <rect x="356" y="8" width="156" height="38" rx="5" fill="#64748b"/>
    <text x="434" y="32" text-anchor="middle" class="f box-tw">LDADDA {ldadda_empty_ns:.1f} ns</text>
    <text x="352" y="64" text-anchor="middle" class="f chip">BAR4 阻塞</text>

    <line x1="522" y1="27" x2="550" y2="27" stroke="#64748b" stroke-width="1.8" marker-end="url(#arr-g)"/>

    <rect x="560" y="8" width="144" height="38" rx="5" fill="#64748b"/>
    <text x="632" y="32" text-anchor="middle" class="f box-tw">LDADDA {ldadda_hit_ns:.0f} ns</text>
    <line x1="704" y1="27" x2="730" y2="27" stroke="#64748b" stroke-width="1.8" marker-end="url(#arr-g)"/>
    <rect x="736" y="8" width="124" height="38" rx="5" fill="#f1f5f9" stroke="#94a3b8" stroke-width="1.2"/>
    <text x="798" y="32" text-anchor="middle" class="f box-t">取包 + cq_door</text>

    <text x="892" y="32" class="f big">{ldadda_empty_ns:.1f} ns</text>
    <text x="892" y="54" class="f metric-sub">W=1 卡内 {w1_card_def:.3f} µs</text>
  </g>
  <line x1="28" y1="142" x2="1122" y2="142" stroke="#e2e8f0" stroke-width="1"/>

  <!-- ROW 2: bar4-first -->
  <g transform="translate(0, 154)">
    <text x="28" y="30" class="f arm">--bar4-first</text>
    <text x="28" y="50" class="f tag">盲窗暂缓</text>

    <rect x="192" y="8" width="320" height="38" rx="5" fill="url(#hatch)" stroke="#64748b" stroke-width="1.5"/>
    <text x="352" y="32" text-anchor="middle" class="f box-t">屏蔽 eth0（{w1_card_b4:.3f}–2.50 µs）</text>
    <line x1="286" y1="68" x2="286" y2="51" stroke="#475569" stroke-width="2" marker-end="url(#arr-g)"/>
    <text x="366" y="65" text-anchor="middle" class="f chip">新包撞窗</text>

    <line x1="522" y1="27" x2="550" y2="27" stroke="#64748b" stroke-width="1.8" marker-end="url(#arr-g)"/>

    <rect x="560" y="8" width="144" height="38" rx="5" fill="#64748b"/>
    <text x="632" y="32" text-anchor="middle" class="f box-tw">LDADDA {ldadda_hit_ns:.0f} ns</text>
    <line x1="704" y1="27" x2="730" y2="27" stroke="#64748b" stroke-width="1.8" marker-end="url(#arr-g)"/>
    <rect x="736" y="8" width="124" height="38" rx="5" fill="#f1f5f9" stroke="#94a3b8" stroke-width="1.2"/>
    <text x="798" y="32" text-anchor="middle" class="f box-t">取包 + cq_door</text>

    <text x="892" y="32" class="f big">{fmt_signed(b4_w4_p99_delta, "{:.2f}")} µs</text>
    <text x="892" y="54" class="f metric-sub">W=4 p99 · W=8 {fmt_signed(b4_w8_pct, "{:.1f}")}%</text>
  </g>
  <line x1="28" y1="234" x2="1122" y2="234" stroke="#e2e8f0" stroke-width="1"/>

  <!-- ROW 3: cqe-peek (MAIN ACCENT) -->
  <rect x="16" y="244" width="1118" height="84" rx="8" fill="#f0f7fc" stroke="#1d6fa5" stroke-width="1.6"/>
  <g transform="translate(0, 250)">
    <text x="28" y="30" class="f arm-acc">--cqe-peek</text>
    <text x="28" y="50" class="f tag-acc">主方案</text>

    <rect x="192" y="8" width="116" height="38" rx="5" fill="#1d6fa5"/>
    <text x="250" y="32" text-anchor="middle" class="f box-tw">ldar {peek_empty_ns:.2f} ns</text>
    <line x1="308" y1="27" x2="334" y2="27" stroke="#1d6fa5" stroke-width="2" marker-end="url(#arr-a)"/>
    <rect x="340" y="8" width="172" height="38" rx="5" fill="#ffffff" stroke="#1d6fa5" stroke-width="1.5"/>
    <text x="426" y="32" text-anchor="middle" class="f box-ta">无盲窗 · BAR4 零阻塞</text>
    <text x="250" y="64" text-anchor="middle" class="f chip-acc">快 {speedup_peek:.0f}×</text>

    <line x1="522" y1="27" x2="550" y2="27" stroke="#1d6fa5" stroke-width="2" marker-end="url(#arr-a)"/>

    <rect x="560" y="8" width="136" height="38" rx="5" fill="#475569"/>
    <text x="628" y="32" text-anchor="middle" class="f box-tw">按需 LDADDA</text>
    <line x1="696" y1="27" x2="718" y2="27" stroke="#1d6fa5" stroke-width="1.8" marker-end="url(#arr-a)"/>
    <rect x="724" y="8" width="144" height="38" rx="5" fill="#ffffff" stroke="#1d6fa5" stroke-width="1.4"/>
    <text x="796" y="32" text-anchor="middle" class="f box-ta">清零 + cq_door</text>

    <text x="892" y="32" class="f big-acc">{peek_empty_ns:.2f} ns</text>
    <text x="892" y="54" class="f chip-acc">W=1 {w1_card_peek:.3f} µs · W=4 {fmt_signed(peek_vs_b4_w4_p99, "{:.2f}")} µs</text>
  </g>

  <!-- ROW 4: cqe-direct (EXPLORATORY) -->
  <rect x="16" y="340" width="1118" height="84" rx="8" fill="#ffffff" stroke="#1d6fa5" stroke-width="1.4" stroke-dasharray="6,4"/>
  <g transform="translate(0, 346)">
    <text x="28" y="30" class="f arm-acc">--cqe-direct</text>
    <text x="28" y="50" class="f tag">探索臂</text>

    <rect x="192" y="8" width="116" height="38" rx="5" fill="#e8f1f8" stroke="#1d6fa5" stroke-width="1.4"/>
    <text x="250" y="32" text-anchor="middle" class="f box-ta">ldar {direct_empty_ns:.2f} ns</text>
    <line x1="308" y1="27" x2="334" y2="27" stroke="#1d6fa5" stroke-width="1.8" marker-end="url(#arr-a)"/>
    <rect x="340" y="8" width="172" height="38" rx="5" fill="#ffffff" stroke="#1d6fa5" stroke-width="1.3" stroke-dasharray="4,3"/>
    <text x="426" y="32" text-anchor="middle" class="f box-ta">无盲窗 · BAR4 零阻塞</text>
    <text x="250" y="64" text-anchor="middle" class="f chip-acc">快 {speedup_direct:.0f}×</text>

    <line x1="522" y1="27" x2="550" y2="27" stroke="#1d6fa5" stroke-width="2" marker-end="url(#arr-a)"/>

    <rect x="560" y="8" width="136" height="38" rx="5" fill="#1d6fa5"/>
    <text x="628" y="32" text-anchor="middle" class="f box-tw">ldar w0+w9 直取</text>
    <line x1="696" y1="27" x2="718" y2="27" stroke="#1d6fa5" stroke-width="1.8" marker-end="url(#arr-a)"/>
    <rect x="724" y="8" width="144" height="38" rx="5" fill="#e8f1f8" stroke="#1d6fa5" stroke-width="1.4"/>
    <text x="796" y="32" text-anchor="middle" class="f box-ta">清零 + cq_door</text>

    <text x="892" y="32" class="f big-acc">0 LDADDA</text>
    <text x="892" y="54" class="f chip-acc">W=1 {w1_merc_dir:.3f} µs · W=8 {fmt_signed(dir_w8_pct, "{:.1f}")}%</text>
  </g>
</svg>
'''
    svg_path = HERE / "mrv-161-mechanism.svg"
    png_path = HERE / "mrv-161-mechanism.png"
    svg_path.write_text(svg, encoding="utf-8")
    subprocess.run(
        ["nix", "shell", "nixpkgs#librsvg", "-c", "rsvg-convert", "-z", "2", "-b", "white", str(svg_path), "-o", str(png_path)],
        check=True,
    )
    print(f"Generated {svg_path} and {png_path}")


if __name__ == "__main__":
    main()
