import {
  C,
  Card,
  Cue,
  FONT,
  K,
  PacketStream,
  SceneDef,
  Stage,
  clamp01,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import {
  BarChart,
  Brace,
  CellState,
  HBarChart,
  InfPill,
  InfTag,
  KnobBar,
  Meter,
  NextQuestion,
  Rig,
  RingStrip,
  Takeaway,
  rigGeom,
  rigRoutes,
} from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 18.98, "**Finding 1 (One-way ceiling):** Sanity check first — pinned at `3.9 GHz`, return replies OFF vs ON run **113.1 vs 112.2 M/s**. Return traffic is innocent; an earlier **75.6 M/s** drop was unpinned **~2.1 GHz** **[INF]**.", "Finding one: how fast is one-way forward? First, pinned at three point nine gigahertz, turning return replies on gives one hundred thirteen point one versus one hundred twelve point two million. Return traffic is innocent; an earlier drop to seventy-five point six was an unpinned clock, our reading."], // 0
  [19.33, 37.85, "With **2 card receive cores**, pure one-way still stalls at **128.7 M/s**. **Knob 1:** pause when caught up (`< 32 cells`): **0 µs** = **128.7** (`93.6%` empty); **1 µs** = **132.2**; **3 µs leaps +26% to 161.6 M/s** (`24.2 cyc/msg`).", "With two card receive cores, one-way still stalls at one hundred twenty-eight point seven million. Turning one knob, pause when caught up: zero microseconds hits one twenty-eight point seven, one gives one thirty-two point two, and three microseconds leaps twenty-six percent to one hundred sixty-one point six million."], // 1
  [38.20, 54.70, "**Why?** Host counters show **447 writes** queuing **3.47 µs** at `0 µs` (**[INF]** out-of-order landing); `3 µs` lets **~900 cells** settle (`360` queued). Did pausing avoid the **write band**, or free **L3 bandwidth**?", "Host counters show four hundred forty-seven writes queuing three point five microseconds, our reading being out-of-order landing. Three microseconds lets nine hundred cells settle cleanly. Did pausing avoid the active write band, or simply free card L 3 read bandwidth?"], // 2
  [55.05, 69.21, "**Knob 2 (Read target during 3 µs pause):** Cold RAM and old ring slots push card L3 reads to **815–1,033 M/s** (> 1 billion/s), yet forward stays flat at **160.2–162.6 M/s**.", "Keeping the three microsecond pause fixed and varying where the core reads: cold memory and old ring slots push card L 3 requests past one billion per second, yet forward throughput stays flat near one hundred sixty-one million."], // 3
  [69.56, 84.34, "**Only reading ahead inside the active write band** crashes forward to **113.4 M/s** (`472 M/s` L3, snoops `67.9 → 111.9 M`, queue `4.03 µs > 3 µs` **[INF]**). Verdict: **write-band conflict, not L3 bandwidth**.", "Only reading ahead inside the active write band crashes throughput to one hundred thirteen point four million, stretching host queuing past four microseconds. Our reading: snoops or write blocking feed back. Verdict: location, not bandwidth."], // 4
];
const DUR = 85.74; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // ============================================================== Phase 1: Sanity Check (Cue 0)
  const p0 = fade(t, T[0], T[1], 0.45);
  const retOn = prog(t, T[0] + 4.4, 0.9);
  const g0 = rigGeom(0.48, 31, 124);
  const r0 = rigRoutes(g0);

  const b0_off = prog(t, T[0] + 1.4, 0.75);
  const b0_on = prog(t, T[0] + 4.8, 0.75);
  const b0_unpin = prog(t, T[0] + 10.6, 0.75);
  const inf0K = prog(t, T[0] + 10.8, 0.6) * (1 - prog(t, T[1], 0.45));
  const verd0K = prog(t, T[0] + 7.2, 0.6) * (1 - prog(t, T[1], 0.45));

  // ============================================================== Phase 2: Knob 1 — Frontier Pause (Cues 1–2)
  const p12 = prog(t, T[1] + 0.25, 0.45) * (1 - prog(t, T[3], 0.45));
  const p1Right = prog(t, T[1] + 0.35, 0.45) * (1 - prog(t, T[2], 0.45));
  const p2Right = prog(t, T[2] + 0.3, 0.45) * (1 - prog(t, T[3], 0.45));

  const k1Step1 = prog(t, T[1] + 7.2, 0.8);
  const k1Step3 = prog(t, T[1] + 10.5, 0.9);
  const knob1Active = k1Step1 + k1Step3;

  const b1_0 = prog(t, T[1] + 3.2, 0.75);
  const b1_1 = prog(t, T[1] + 7.5, 0.75);
  const b1_3 = prog(t, T[1] + 10.8, 0.8);

  // Ring strip for Cues 1–2: cursor chases frontier at 0 us, steps back behind ~900 settled cells at 3 us
  const settleK = k1Step3;
  const cursorAt = lerp(21.4, 11.2, settleK);
  const ringState12 = (i: number): CellState => {
    if (i < Math.floor(cursorAt)) return "consumed";
    if (i < 22) {
      if (settleK > 0.35) return "landed";
      return i === 21 ? "hole" : "consumed";
    }
    if (i <= 29) {
      if (i === 23 || i === 26) return "hole";
      return "inflight";
    }
    return "empty";
  };

  const inf2K = prog(t, T[2] + 2.2, 0.65) * (1 - prog(t, T[3], 0.45));
  const m0K = prog(t, T[2] + 0.6, 0.75);
  const m1K = prog(t, T[2] + 2.6, 0.75);
  const m3K = prog(t, T[2] + 4.8, 0.75);
  const nextQ2K = prog(t, T[2] + 8.8, 0.65) * (1 - prog(t, T[3], 0.45));

  // ============================================================== Phase 3: Knob 2 — Read Target (Cues 3–4)
  const p34 = prog(t, T[3] + 0.25, 0.45);
  const k2Step1 = prog(t, T[3] + 2.6, 0.7);
  const k2Step2 = prog(t, T[3] + 5.8, 0.7);
  const k2Step3 = prog(t, T[4] + 0.7, 0.75);
  const knob2Active = k2Step1 + k2Step2 + k2Step3;

  const rowKs = [
    prog(t, T[3] + 0.8, 0.65),
    prog(t, T[3] + 2.6, 0.65),
    prog(t, T[3] + 5.0, 0.65),
    prog(t, T[3] + 7.2, 0.65),
    prog(t, T[3] + 9.2, 0.65),
    prog(t, T[4] + 0.9, 0.8),
  ];
  const crashK = prog(t, T[4] + 0.9, 0.7);
  const verd4K = prog(t, T[4] + 4.2, 0.65);

  const ringState34 = (i: number): CellState => {
    if (i <= 7) return "consumed";
    if (i <= 15) return "landed";
    if (i <= 23) return i === 18 || i === 21 ? "hole" : "inflight";
    return "empty";
  };

  const fwdBars = [
    { label: "Idle spin", value: 160.4, color: K.fast, text: "160.4 M/s" },
    { label: "Cold RAM (64 B)", value: 160.4, color: K.fast, text: "160.4 M/s" },
    { label: "Cold RAM (4 KiB)", value: 161.4, color: K.fast, text: "161.4 M/s" },
    { label: "Old ring (behind)", value: 162.3, color: K.fast, text: "162.0–162.6 M/s" },
    { label: "Just behind", value: 160.2, color: K.fast, text: "160.2 M/s" },
    { label: "Write band (ahead)", value: 113.4, color: K.slow, text: "113.4 M/s" },
  ];

  const l3Bars = [
    { label: "", value: 608, color: K.fwd, text: "608 M/s" },
    { label: "", value: 815, color: K.fwd, text: "815 M/s" },
    { label: "", value: 1033, color: C.yellow, text: "1,033 M/s (> 1 billion/s)" },
    { label: "", value: 869, color: K.fwd, text: "790–948 M/s" },
    { label: "", value: 728, color: K.fwd, text: "728 M/s" },
    { label: "", value: 472, color: C.muted, text: "472 M/s" },
  ];

  return (
    <>
      {/* ======================================================== SVG STAGE */}
      <Stage>
        {/* ---------------- Phase 1 SVG: Sanity Check Rig + BarChart ---------------- */}
        {p0 > 0.001 && (
          <g opacity={p0} fontFamily={FONT}>
            <Rig
              g={g0}
              cores={2}
              activeCore={retOn > 0.3 ? Math.floor(rt * 3) % 2 : 0}
              labels={{
                host: ["HOST", "3.9 GHz"],
                card: ["DPU", "2 cores"],
                hcore: ["core 40", "3.9 GHz"],
                wq: ["CHA", "queue"],
                hring: ["reply ring"],
                cring: ["req ring"],
                ccores: ["2 cores"],
                wc: ["WC", "64 B"],
                link: ["PCIe Gen5 ×4"],
              }}
              ringFill={{
                cring: 4 + (Math.floor(rt * 4) % 4),
                hring: retOn > 0.3 ? 3 + (Math.floor(rt * 3) % 4) : 0,
              }}
              lanes={{ fwd: 1, ret: lerp(0.18, 1, retOn) }}
            />
            <PacketStream pts={r0.send} travel={rt * 150} gap={44} size={9} color={K.fwd} />
            <PacketStream pts={r0.fwd} travel={rt * 210} gap={48} size={10} color={K.fwd} />
            {retOn > 0.1 && (
              <g opacity={retOn}>
                <PacketStream pts={r0.reply} travel={rt * 150} gap={46} size={9} color={K.ret} />
                <PacketStream pts={r0.ret} travel={rt * 210} gap={48} size={10} color={K.ret} />
                <PacketStream pts={r0.hpoll} travel={rt * 150} gap={46} size={9} color={K.ret} />
              </g>
            )}

            {/* Right panel backdrop */}
            <rect
              x={956}
              y={202}
              width={904}
              height={530}
              rx={16}
              fill="rgba(20,27,46,0.72)"
              stroke={C.line}
              strokeWidth={2}
            />
            <text x={984} y={240} fontSize={25} fontWeight={800} fill={C.text}>
              Forward Throughput (M/s) · Sanity Check
            </text>
            <BarChart
              x0={996}
              x1={1828}
              y1={296}
              y0={644}
              max={135}
              unit=" M/s"
              size={28}
              labelSize={23}
              barW={132}
              bars={[
                { label: "Replies OFF", sub: "pinned 3.9 GHz", value: 113.1, color: K.fwd, text: "113.1 M/s" },
                { label: "Replies ON", sub: "pinned 3.9 GHz", value: 112.2, color: K.ret, text: "112.2 M/s" },
                { label: "Unpinned", sub: "~2.1 GHz [INF]", value: 75.6, color: K.slow, text: "75.6 M/s" },
              ]}
              k={(i) => [b0_off, b0_on, b0_unpin][i]}
            />
          </g>
        )}

        {/* ---------------- Phase 2 SVG: Knob 1 RingStrip + BarChart + Host CHA Meters ---------------- */}
        {p12 > 0.001 && (
          <g opacity={p12} fontFamily={FONT}>
            {/* Top RingStrip container */}
            <rect
              x={60}
              y={194}
              width={1800}
              height={238}
              rx={16}
              fill="rgba(20,27,46,0.72)"
              stroke={C.line}
              strokeWidth={2}
            />
            <text x={86} y={222} fontSize={21} fontWeight={800} fill={C.muted} letterSpacing={1.2}>
              CARD REQUEST RING (oldest → newest)
            </text>
            <RingStrip
              x={100}
              y={286}
              n={32}
              cw={46}
              ch={52}
              gap={8}
              state={ringState12}
              flicker={rt}
              nextAt={cursorAt}
              nextLabel={settleK > 0.5 ? "next (3 µs pause)" : "next (0 µs chase)"}
              nextColor={settleK > 0.5 ? K.fast : K.slow}
              band={{
                from: 22,
                to: 30,
                label: "active write frontier",
                color: K.inflight,
              }}
              size={22}
            />

            {/* Settled cells brace (appears with 3 us pause) */}
            {settleK > 0.2 && (
              <Brace
                x1={100 + 11 * 54}
                x2={100 + 22 * 54 - 8}
                y={348}
                label="~900 cells settle cleanly (3 µs)"
                color={K.landed}
                size={22}
                opacity={clamp01((settleK - 0.2) / 0.8)}
              />
            )}

            {/* [INF] out-of-order landing tag in Cue 2 */}
            {inf2K > 0.05 && (
              <g opacity={inf2K}>
                <InfTag x={1272} y={392} size={19} />
                <text x={1344} y={412} fontSize={21} fontWeight={700} fill={K.inf}>
                  out-of-order landing (~3.5 µs)
                </text>
              </g>
            )}

            {/* Bottom-Left Panel: Forward Throughput BarChart */}
            <rect
              x={60}
              y={446}
              width={876}
              height={334}
              rx={16}
              fill="rgba(20,27,46,0.72)"
              stroke={C.line}
              strokeWidth={2}
            />
            <text x={86} y={480} fontSize={24} fontWeight={800} fill={C.text}>
              Forward Throughput vs Pause (&lt; 32 cells)
            </text>
            <BarChart
              x0={96}
              x1={900}
              y1={536}
              y0={708}
              max={185}
              unit=" M/s"
              size={27}
              labelSize={22}
              barW={122}
              bars={[
                { label: "0 µs", sub: "93.6% empty", value: 128.7, color: K.slow, text: "128.7 M/s" },
                { label: "1 µs", sub: "near frontier", value: 132.2, color: K.knob, text: "132.2 M/s" },
                { label: "3 µs", sub: "+26% · 24.2 cyc/msg", value: 161.6, color: K.fast, text: "161.6 M/s" },
              ]}
              k={(i) => [b1_0, b1_1, b1_3][i]}
            />

            {/* Bottom-Right Panel (Cue 2): Host CHA Write-Queue Meters */}
            {p2Right > 0.001 && (
              <g opacity={p2Right}>
                <rect
                  x={964}
                  y={446}
                  width={896}
                  height={334}
                  rx={16}
                  fill="rgba(20,27,46,0.72)"
                  stroke={C.line}
                  strokeWidth={2}
                />
                <text x={990} y={480} fontSize={24} fontWeight={800} fill={C.text}>
                  Host CHA Write Queue (in-flight writes · dwell)
                </text>
                <Meter
                  x={992}
                  y={538}
                  w={836}
                  h={26}
                  value={447}
                  max={500}
                  color={K.slow}
                  label="0 µs pause"
                  valueText="447 entries · 3.47 µs"
                  k={m0K}
                  size={23}
                />
                <Meter
                  x={992}
                  y={628}
                  w={836}
                  h={26}
                  value={433}
                  max={500}
                  color={K.knob}
                  label="1 µs pause"
                  valueText="433 entries · 3.28 µs"
                  k={m1K}
                  size={23}
                />
                <Meter
                  x={992}
                  y={718}
                  w={836}
                  h={26}
                  value={360}
                  max={500}
                  color={K.fast}
                  label="3 µs pause"
                  valueText="360 entries · 2.23 µs"
                  k={m3K}
                  size={23}
                />
              </g>
            )}
          </g>
        )}

        {/* ---------------- Phase 3 SVG: Knob 2 Mini Ring + Cold RAM + Dual HBarChart ---------------- */}
        {p34 > 0.001 && (
          <g opacity={p34} fontFamily={FONT}>
            {/* Top-left mini RingStrip container */}
            <rect
              x={60}
              y={192}
              width={1300}
              height={162}
              rx={14}
              fill="rgba(20,27,46,0.72)"
              stroke={C.line}
              strokeWidth={2}
            />
            <text x={906} y={228} fontSize={21} fontWeight={800} fill={K.inflight}>
              active write band (ahead of cursor)
            </text>
            <RingStrip
              x={88}
              y={250}
              n={25}
              cw={42}
              ch={44}
              gap={7}
              state={ringState34}
              flicker={rt}
              nextAt={15.2}
              nextLabel="next"
              nextColor={K.card}
              band={{ from: 16, to: 24, color: K.inflight }}
              probes={[
                { from: 1, to: 7, color: K.fwd, label: "Old ring", k: prog(t, T[3] + 4.5, 0.5) },
                { from: 9, to: 15, color: K.fast, label: "Just behind", k: prog(t, T[3] + 8.5, 0.5) },
                { from: 17, to: 23, color: K.slow, label: "Write band (ahead)", k: crashK },
              ]}
              size={21}
            />

            {/* Top-right Cold RAM box */}
            <rect
              x={1384}
              y={192}
              width={476}
              height={162}
              rx={14}
              fill="rgba(20,27,46,0.72)"
              stroke={k2Step1 > 0.5 && k2Step2 < 0.5 ? K.knob : K.fwd}
              strokeWidth={2.5}
              strokeDasharray="8 6"
            />
            <text x={1622} y={238} textAnchor="middle" fontSize={25} fontWeight={800} fill={C.text}>
              Cold RAM (separate region)
            </text>
            <text x={1622} y={276} textAnchor="middle" fontSize={22} fontWeight={700} fill={K.fwd}>
              815 M/s L3 reads · off-ring
            </text>
            <text x={1622} y={312} textAnchor="middle" fontSize={22} fontWeight={700} fill={K.fast}>
              Forward stays flat: 160.4 M/s
            </text>

            {/* Bottom dual aligned HBarChart container */}
            <rect
              x={60}
              y={368}
              width={1800}
              height={398}
              rx={16}
              fill="rgba(20,27,46,0.72)"
              stroke={C.line}
              strokeWidth={2}
            />
            {/* Subtle row highlight for Row 6 crash in Cue 4 */}
            {crashK > 0.05 && (
              <rect
                x={74}
                y={698}
                width={1772}
                height={56}
                rx={10}
                fill="rgba(249,65,68,0.14)"
                stroke={K.slow}
                strokeWidth={1.5}
                opacity={crashK}
              />
            )}
            {/* Subtle horizontal row dividers */}
            {[1, 2, 3, 4, 5].map((r) => (
              <line
                key={r}
                x1={80}
                y1={412 + r * 57}
                x2={1840}
                y2={412 + r * 57}
                stroke={C.line}
                strokeWidth={1}
                opacity={0.45}
              />
            ))}

            {/* Dual chart headers */}
            <text x={332} y={398} fontSize={24} fontWeight={800} fill={K.fast}>
              Forward Throughput (M/s)
            </text>
            <text x={1110} y={398} fontSize={24} fontWeight={800} fill={K.fwd}>
              Card L3 Requests (M/s)
            </text>

            {/* Left HBarChart: Forward Throughput */}
            <HBarChart
              x0={332}
              x1={860}
              y1={412}
              y0={754}
              max={190}
              size={24}
              labelSize={23}
              barH={32}
              bars={fwdBars}
              k={(i) => rowKs[i]}
            />

            {/* Right HBarChart: Card L3 Requests */}
            <HBarChart
              x0={1110}
              x1={1540}
              y1={412}
              y0={754}
              max={1150}
              size={24}
              labelSize={22}
              barH={32}
              bars={l3Bars}
              k={(i) => rowKs[i]}
            />
          </g>
        )}
      </Stage>

      {/* ======================================================== HTML OVERLAYS */}
      {/* ---------------- Phase 1 HTML ---------------- */}
      {p0 > 0.001 && (
        <>
          <KnobBar
            y={118}
            fixed={["Host 3.9 GHz", "1 recv + 1 reply core", "WC replies"]}
            knob="Return replies"
            options={["OFF", "ON"]}
            active={retOn}
            k={p0}
          />
          {inf0K > 0.001 && (
            <Card
              x={60}
              y={562}
              w={864}
              h={170}
              border={K.slow}
              title="WHY DID AN EARLIER RUN DROP TO 75.6 M/s?"
              titleColor={K.slow}
              style={{ opacity: inf0K }}
            >
              <div style={{ fontSize: 24, color: C.text, lineHeight: 1.4 }}>
                <InfPill size={20} style={{ marginRight: 8 }} />
                Unpinned host sender dropped to <strong style={{ color: K.slow }}>~2.1 GHz</strong> — CPU clock drop,
                not PCIe return traffic.
              </div>
            </Card>
          )}
          {verd0K > 0.001 && (
            <Takeaway x={60} y={754} w={1800} k={verd0K} color={K.fast} label="SANITY CHECK VERDICT" size={28}>
              Return traffic is innocent — <span style={{ color: K.fwd }}>113.1 M/s</span> (OFF) vs{" "}
              <span style={{ color: K.fast }}>112.2 M/s</span> (ON) proves replies do NOT slow forward writes.
            </Takeaway>
          )}
        </>
      )}

      {/* ---------------- Phase 2 HTML ---------------- */}
      {p12 > 0.001 && (
        <>
          <KnobBar
            y={118}
            fixed={["One-way", "3.9 GHz", "2 card cores", "< 32 cells"]}
            knob="Pause when caught up"
            options={["0 µs", "1 µs", "3 µs"]}
            active={knob1Active}
            k={p12}
          />

          {/* Cue 1 right-side summary card (fades out at Cue 2 for CHA meters) */}
          {p1Right > 0.001 && (
            <Card
              x={964}
              y={446}
              w={896}
              h={334}
              border={k1Step3 > 0.5 ? K.fast : C.line}
              title="ONE KNOB → +26% ONE-WAY CEILING"
              titleColor={k1Step3 > 0.5 ? K.fast : K.knob}
              style={{
                opacity: p1Right,
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
              }}
            >
              <div style={{ display: "flex", alignItems: "baseline", gap: 20 }}>
                <span
                  style={{
                    fontSize: 54,
                    fontWeight: 900,
                    color: k1Step3 > 0.5 ? K.fast : K.knob,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {k1Step3 > 0.5 ? "161.6 M/s" : k1Step1 > 0.5 ? "132.2 M/s" : "128.7 M/s"}
                </span>
                <span style={{ fontSize: 25, fontWeight: 700, color: C.muted }}>
                  {k1Step3 > 0.5 ? "+26% · 24.2 cyc/msg at 3 µs pause" : "2 card receive cores · < 32 cells/poll"}
                </span>
              </div>
              <div style={{ fontSize: 24, color: C.text, lineHeight: 1.5 }}>
                <div>
                  • <strong style={{ color: K.slow }}>0 µs:</strong> <strong>128.7 M/s</strong> —{" "}
                  <strong style={{ color: K.slow }}>93.6%</strong> empty polls chasing the active frontier
                </div>
                <div>
                  • <strong style={{ color: K.knob }}>1 µs:</strong> <strong>132.2 M/s</strong> — frontier still landing
                </div>
                <div>
                  • <strong style={{ color: K.fast }}>3 µs:</strong> <strong style={{ color: K.fast }}>161.6 M/s</strong>{" "}
                  — short pause lets incoming cells settle
                </div>
              </div>
            </Card>
          )}

          {/* Cue 2 bottom bridge question */}
          {nextQ2K > 0.001 && (
            <NextQuestion x={60} y={796} w={1800} k={nextQ2K} size={26} label="NEXT QUESTION →">
              Did pausing avoid the active write band — or simply free card L3 read bandwidth?
            </NextQuestion>
          )}
        </>
      )}

      {/* ---------------- Phase 3 HTML ---------------- */}
      {p34 > 0.001 && (
        <>
          <KnobBar
            y={118}
            size={22}
            fixed={["One-way", "3 µs pause", "2 card cores"]}
            knob="Read target"
            options={["Idle spin", "Cold RAM", "Old ring", "Write band"]}
            active={knob2Active}
            k={p34}
          />

          {verd4K > 0.001 && (
            <Takeaway
              x={60}
              y={780}
              w={1800}
              k={verd4K}
              color={K.fast}
              label="VERDICT · WRITE-BAND LOCATION, NOT L3 BANDWIDTH"
              size={25}
            >
              Only reading ahead inside the write band crashes to{" "}
              <span style={{ color: K.slow, fontWeight: 800 }}>113.4 M/s</span> (snoops{" "}
              <span style={{ color: K.knob }}>67.9 → 111.9 M</span>, host queue{" "}
              <span style={{ color: K.slow }}>4.03 µs &gt; 3 µs</span> <InfPill size={19} />).
            </Takeaway>
          )}
        </>
      )}
    </>
  );
}


export const S02: SceneDef = {
  id: "S02",
  title: "Nailed down #1 · Why one-way hits 161.6 M/s",
  stage: 0,
  source: "Single-variable forward tests · 81 verified runs · 0 lost, 0 corrupt",
  dur: DUR,
  cues,
  C: Body,
};
