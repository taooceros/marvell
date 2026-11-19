import {
  C,
  Cue,
  FONT,
  K,
  MONO,
  PacketStream,
  SceneDef,
  Stage,
  appear,
  prog,
  useRealT,
  useT,
} from "../lib";

// <cues>
const cues: Cue[] = [
  [0.60, 17.84, "**Stage 1 (Direct Submit)**: two-layer `vmem` (`dmar0` + `dmar6` SVA) + DPI 64 B atomic write reaches **29.53 GB/s** with zero host forwarding, but leaves a `1.21 µs` read-back floor.", "Recapping our three single-variable stages: stage one wired two-layer virtual mapping with D P I sixty-four-byte atomic writes, hitting twenty-nine point five three gigabytes per second with zero host forwarding, but leaving a one point two one microsecond read-back floor."], // 0
  [18.19, 35.89, "**Stage 2 (Reverse P2P)**: 32 B completions straight to card `BAR4` slot 15 cut `W = 1` loops to **1.27 / 2.17 µs** (jitter **163 ns**), while exposing the `131 ns` drain bubble.", "Stage two added reverse P 2 P completions into card B A R four slot fifteen, cutting single-op loops to one point two seven and two point one seven microseconds with one hundred sixty-three nanoseconds of jitter, while exposing the one hundred thirty-one nanosecond drain bubble."], // 1
  [36.24, 50.96, "**Stage 3 (DSA Batch B = 4 / 16)**: sharing one reverse completion erases the bubble, lifting throughput to **31.32 GB/s** with **full bandwidth + tight tails**.", "Stage three shared one reverse completion across a D S A Batch, erasing the bubble and pushing single-engine throughput to thirty-one point three two gigabytes per second—delivering both full bandwidth and tight latency tails."], // 2
  [51.31, 67.85, "Three guardrails: map the portal **FLAG_WRITE only**, ban `pem_wc` (pure **DPI atomic**), and isolate SVA `PASID` in a **minimal sleeping process** with a `BAR4` lease.", "Three guardrails for production: map the portal write-only, ban card write-combining in favor of pure D P I atomic writes, and isolate the S V A P A S I D inside a minimal sleeping process guarded by a B A R four lease."], // 3
  [68.20, 85.22, "Next: benchmark **> 128 MiB cold DRAM** rings and multi-engine scaling—our zero-host-forwarding DPU <-> Xeon DSA bidirectional P2P loop is live!", "Next, we will measure cold D R A M working sets larger than one hundred twenty-eight megabytes and scale across multiple engines. Our zero-host-forwarding D P U to Xeon D S A bidirectional P 2 P loop is now end-to-end proven. Thanks for watching!"], // 4
];
const DUR = 86.62; // <dur>
// </cues>

const STAGES_DATA = [
  {
    idx: 0,
    tag: "Stage 1 · Direct Submit",
    color: K.fwd,
    mech: "2-layer vmem (dmar0 + dmar6 SVA) + DPI 64 B atomic write",
    bw: "29.53 GB/s",
    bwSub: "Hot LLC · CC = 1",
    lat: "W = 1 4K: 3.22 µs",
    latSub: "Host Memzone comp",
    note: "Bottleneck: 1.21 µs DPI read-back floor",
    noteColor: K.bubble,
  },
  {
    idx: 1,
    tag: "Stage 2 · Reverse P2P",
    color: K.rev,
    mech: "32 B completion direct to Card BAR4 Slot 15 (CN106 L3 TAD)",
    bw: "NOP 23.01 M/s",
    bwSub: "4K plateau 15.3 GB/s",
    lat: "1.27 / 2.17 µs",
    latSub: "Jitter p99−p50: 163 ns",
    note: "Exposes: 131 ns per-op drain bubble",
    noteColor: K.wc,
  },
  {
    idx: 2,
    tag: "Stage 3 · DSA Batch B=4/16",
    color: K.batch,
    mech: "B copies share 1 reverse-P2P completion (RCR = 0)",
    bw: "31.13–31.32 GB/s",
    bwSub: "2.05× · Single Engine",
    lat: "B=4: 2.84 µs",
    latSub: "B=16 loop: 4.53 µs",
    note: "Full BW + Tight Tails · 0 Drops",
    noteColor: K.rev,
  },
];

const GUARDRAILS = [
  {
    num: "01",
    title: "Write-Only Portal",
    code: "VFIO_DMA_MAP_FLAG_WRITE",
    desc: "Map the portal page write-only (no read) so the card can only issue 64 B Posted MWr.",
    color: K.fwd,
  },
  {
    num: "02",
    title: "Zero pem_wc · Pure DPI",
    code: "Pure DPI 64 B Atomic MWr",
    desc: "Ban ARM write-combining; use pure DPI 64 B atomic submits to prevent SWERROR = 0 drops.",
    color: K.wc,
  },
  {
    num: "03",
    title: "SVA Isolation + BAR4 Lease",
    code: "Minimal Holder Process + Slot 15 Lease",
    desc: "Isolate dmar6 SVA PASID in a minimal sleeping process with a BAR4 Slot 15 lease handshake.",
    color: K.dsa,
  },
];

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  const kGuard = prog(t, T[3], 0.6);
  const kClose = prog(t, T[4], 0.7);

  return (
    <>
      {/* Subtle ambient SVG motion behind the HTML cards */}
      <Stage>
        {/* Animated connector rail on the left edge of the 3-stage table */}
        <line
          x1={44}
          y1={248}
          x2={44}
          y2={248 + 256 * prog(t, T[0], 0.8)}
          stroke={C.line}
          strokeWidth={4}
          strokeLinecap="round"
        />
        {STAGES_DATA.map((s, i) => {
          const kRow = prog(t, T[i], 0.5);
          const cy = 248 + i * 128;
          return (
            <circle
              key={s.idx}
              cx={44}
              cy={cy}
              r={9 * kRow}
              fill={s.color}
              opacity={kRow}
            />
          );
        })}

        {/* Ambient bidirectional P2P pulse along the closing banner when T[4] activates */}
        {kClose > 0.01 && (
          <PacketStream
            pts={[[80, 774], [1840, 774]]}
            travel={rt * 180}
            gap={220}
            size={8}
            color={K.rev}
            opacity={0.75 * kClose}
          />
        )}
      </Stage>

      {/* ================= 3-STAGE EVOLUTION COMPARISON TABLE (y = 136..568) ================= */}
      <div
        style={{
          position: "absolute",
          left: 64,
          top: 136,
          width: 1792,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        {/* Header Bar */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1.45fr 2.05fr 1.25fr 1.4fr 1.65fr",
            padding: "11px 24px",
            borderRadius: 12,
            background: "rgba(255,255,255,0.05)",
            border: `1.5px solid ${C.line}`,
            fontFamily: FONT,
            fontSize: 19,
            fontWeight: 800,
            color: C.muted,
          }}
        >
          <div>Evolution Stage</div>
          <div>Core Mechanism</div>
          <div>Sustained BW</div>
          <div>W = 1 Latency / Jitter</div>
          <div>Key Takeaway / Bottleneck</div>
        </div>

        {/* 3 Progressive Rows */}
        {STAGES_DATA.map((s, i) => {
          const isActive = t >= T[i] && (i === 2 ? t < T[3] : t < T[i + 1]);
          const rowStyle = appear(t, T[i]);
          return (
            <div
              key={s.idx}
              style={{
                display: "grid",
                gridTemplateColumns: "1.45fr 2.05fr 1.25fr 1.4fr 1.65fr",
                alignItems: "center",
                height: 116,
                padding: "0 24px",
                borderRadius: 16,
                background: isActive ? `${s.color}16` : C.panel,
                border: `2.2px solid ${isActive ? s.color : `${s.color}55`}`,
                boxShadow: isActive ? `0 0 24px ${s.color}28` : "none",
                ...rowStyle,
              }}
            >
              {/* Stage badge */}
              <div style={{ fontFamily: FONT, fontSize: 21, fontWeight: 900, color: s.color, paddingRight: 12 }}>
                {s.tag}
              </div>

              {/* Mechanism */}
              <div style={{ fontFamily: FONT, fontSize: 19, fontWeight: 600, color: C.text, lineHeight: 1.35, paddingRight: 16 }}>
                {s.mech}
              </div>

              {/* Throughput */}
              <div>
                <div style={{ fontFamily: MONO, fontSize: 23, fontWeight: 900, color: i === 2 ? K.batch : C.text }}>
                  {s.bw}
                </div>
                <div style={{ fontFamily: MONO, fontSize: 16, color: C.muted, marginTop: 3 }}>
                  {s.bwSub}
                </div>
              </div>

              {/* Latency */}
              <div>
                <div style={{ fontFamily: MONO, fontSize: 21, fontWeight: 800, color: i === 1 || i === 2 ? K.rev : C.text }}>
                  {s.lat}
                </div>
                <div style={{ fontFamily: MONO, fontSize: 16, color: C.muted, marginTop: 3 }}>
                  {s.latSub}
                </div>
              </div>

              {/* Conclusion / Bottleneck */}
              <div
                style={{
                  fontFamily: FONT,
                  fontSize: 19,
                  fontWeight: 800,
                  color: s.noteColor,
                  padding: "10px 14px",
                  borderRadius: 10,
                  background: `${s.noteColor}14`,
                  border: `1.5px solid ${s.noteColor}55`,
                  textAlign: "center",
                  lineHeight: 1.3,
                }}
              >
                {s.note}
              </div>
            </div>
          );
        })}
      </div>

      {/* ================= 3 ENGINEERING GUARDRAIL CARDS (T[3], y = 590..754) ================= */}
      {kGuard > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 64,
            top: 590,
            width: 1792,
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 24,
          }}
        >
          {GUARDRAILS.map((g, i) => (
            <div
              key={g.num}
              style={{
                height: 162,
                padding: "16px 22px",
                borderRadius: 16,
                background: C.panel,
                border: `2px solid ${g.color}88`,
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                ...appear(t, T[3] + i * 0.22),
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span
                  style={{
                    fontFamily: MONO,
                    fontSize: 17,
                    fontWeight: 900,
                    color: C.bg,
                    background: g.color,
                    padding: "3px 10px",
                    borderRadius: 8,
                    whiteSpace: "nowrap",
                  }}
                >
                  RULE {g.num}
                </span>
                <span style={{ fontFamily: FONT, fontSize: 22, fontWeight: 900, color: g.color }}>
                  {g.title}
                </span>
              </div>

              <div
                style={{
                  fontFamily: MONO,
                  fontSize: 18,
                  fontWeight: 700,
                  color: C.text,
                  background: "rgba(255,255,255,0.05)",
                  padding: "6px 12px",
                  borderRadius: 8,
                }}
              >
                {g.code}
              </div>

              <div style={{ fontFamily: FONT, fontSize: 18, color: C.muted, lineHeight: 1.35 }}>
                {g.desc}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ================= BOTTOM CLOSING BANNER (T[4], y = 774..862) ================= */}
      {kClose > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 64,
            top: 774,
            width: 1792,
            height: 86,
            padding: "0 28px",
            borderRadius: 16,
            background: "linear-gradient(90deg, rgba(67,197,158,0.18), rgba(76,201,240,0.16), rgba(249,199,79,0.16))",
            border: `2.5px solid ${K.rev}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            ...appear(t, T[4]),
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            <span
              style={{
                fontFamily: FONT,
                fontSize: 20,
                fontWeight: 900,
                color: C.bg,
                background: K.batch,
                padding: "6px 16px",
                borderRadius: 10,
              }}
            >
              NEXT
            </span>
            <span style={{ fontFamily: FONT, fontSize: 22, fontWeight: 800, color: C.text }}>
              <span style={{ fontFamily: MONO, color: K.batch }}>&gt; 128 MiB</span> Cold DRAM Rings + Multi-Engine Scaling
            </span>
          </div>

          <div style={{ fontFamily: FONT, fontSize: 22, fontWeight: 900, color: K.rev }}>
            CN106 DPU ⇄ Intel DSA Bidirectional P2P Loop Proven
          </div>
        </div>
      )}
    </>
  );
}

export const S07: SceneDef = {
  id: "S07",
  title: "Summary & Engineering Guardrails",
  stage: 5,
  source: "3-Stage Evolution · Guardrails · Cold DRAM Next",
  dur: DUR,
  cues,
  C: Body,
};
