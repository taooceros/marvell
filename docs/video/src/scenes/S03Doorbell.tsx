import {
  Arrow,
  Axes,
  Bars,
  C,
  Cue,
  FONT,
  K,
  Link,
  MONO,
  Packet,
  PacketStream,
  Pill,
  Pt,
  SceneDef,
  Series,
  Stage,
  Stat,
  Txt,
  appear,
  clamp01,
  countUp,
  fade,
  lerp,
  linScale,
  logScale,
  prog,
  useT,
} from "../lib";
import { Datapath, InfTag, dpGeom, ringCell, routes } from "./pcie";

// <cues>
const cues: Cue[] = [
  [0.60, 7.90, "**SDP** is the card's packet engine: descriptor rings on both sides, and a **doorbell** register that says “new work”.", "S D P is the card's packet engine. Each side keeps descriptor rings, and a doorbell register announces new work."], // 0
  [8.25, 20.01, "W=1 round trip: **4.85 µs** p50. The rate grows with W, then flattens at **7.2–7.7 M msg/s** from W=128.", "At a window of one, the median round trip is four point eight five microseconds. The rate grows with the window, then flattens at seven point two to seven point seven million messages per second."], // 1
  [20.36, 30.60, "One core sending one packet per doorbell: **8.36 M pkt/s**, about **120 ns** each.", "Why so low? One host core, sending one packet per doorbell, manages eight point three six million packets per second. That's about a hundred and twenty nanoseconds each."], // 2
  [30.95, 38.75, "Over 1, 2, 4, 8 TX queues: **8.36 / 7.32 / 6.59 / 7.06 M pkt/s**. Separate doorbells don't overlap.", "Spreading the packets over one, two, four, or eight queues doesn't help at all. Separate doorbells still don't overlap."], // 3
  [39.10, 46.68, "**[INF]** Each doorbell is an **8-byte uncached store**, and the core issues them **one at a time**.", "Our reading: each doorbell is an eight byte uncached store across the link, and a core issues them strictly one at a time."], // 4
  [47.03, 56.25, "Batching amortizes it: **32** per doorbell reaches **50.4 M pkt/s**. But a request–reply loop sends one at a time.", "Batching does amortize it. Thirty-two packets per doorbell reach fifty million packets per second. But a request and reply loop sends one message at a time."], // 5
  [56.60, 62.78, "So: skip the descriptor ring and its doorbell, and write the message **straight into card memory**.", "So the plan is to skip the descriptor ring and its doorbell, and write the message straight into card memory."], // 6
];
const DUR = 64.18; // <dur>
// </cues>

/** Packet progress inside a repeating cycle: 0 before a, eased 0→1 over d, gone shortly after. */
const cyc = (ph: number, a: number, d: number) => (ph < a || ph > a + d + 0.3 ? 0 : prog(ph, a, d));
const mod = (x: number, p: number) => ((x % p) + p) % p;

/** Red ✗ centred at (x, y). */
function Cross({ x, y, s = 18, opacity = 1 }: { x: number; y: number; s?: number; opacity?: number }) {
  if (opacity <= 0) return null;
  return (
    <g opacity={opacity} stroke={K.bad} strokeWidth={6} strokeLinecap="round">
      <line x1={x - s} y1={y - s} x2={x + s} y2={y + s} />
      <line x1={x - s} y1={y + s} x2={x + s} y2={y - s} />
    </g>
  );
}

// Bottom-left timeline (cues 2–5): one host core on a ns axis.
const TX0 = 210;
const PXNS = 880 / 960;
const BW = 120 * PXNS; // one doorbell ≈ 120 ns
const NB = 8;
const qRow = (q: number) => 657 + q * 36;

// Right-panel chart (cue 1): message rate vs window, log x.
const sx = logScale(1, 512, 1260, 1840);
const sy = linScale(0, 10, 780, 200);
const RISE: [number, number][] = [1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 28, 32].map((w) => [
  w,
  0.2 + ((w - 1) * (5.06 - 0.2)) / 31,
]);

function Body() {
  const t = useT();
  const T = cues.map((c) => c[0]);

  // datapath morph: full (c0) → shrunk top-left (c1–c5) → full (c6)
  const ks = prog(t, T[1], 0.9) * (1 - prog(t, T[6] + 0.3, 0.9));
  const g = dpGeom(lerp(1, 0.6, ks), lerp(0, -10, ks), lerp(0, 40, ks));
  const r = routes(g);

  const o0 = 1 - prog(t, T[1], 0.5);
  const ph = mod(t - T[1], 3.2);
  const oLoop = fade(t, T[1] + 0.9, T[2]);
  const oStream = fade(t, T[2] + 0.2, T[6]);
  const o4 = fade(t, T[4] + 0.2, T[5]);
  const o6 = prog(t, T[6] + 0.6, 0.6);
  const sdpDim = 1 - 0.7 * prog(t, T[6] + 0.3, 0.8);

  // c6: inline writes land in request cells 0..2
  const land = [0, 1, 2].map((i) => T[6] + 2.0 + i * 0.7);
  const nFill = land.filter((a) => t >= a + 0.9).length;
  const toCell = (i: number): Pt[] => {
    const c = ringCell(g, "reqRing", i);
    const p = r.inline;
    return [...p.slice(0, 4), [p[4][0], c[1]], c];
  };
  // cross the doorbell where it runs alone: up the card bus towards SDP
  const xMid = g.pt([1357, 335]);

  // timeline state
  const kq = prog(t, T[3] + 0.2, 0.8) * (1 - prog(t, T[4], 0.8)); // split into 4 queue rows
  const k5 = prog(t, T[5] + 0.1, 0.7); // batching view
  const rowY = lerp(700, 672, k5);
  const oTL = fade(t, T[2] + 0.1, T[6]);
  const oCur = fade(t, T[4] + 0.5, T[5]);
  const cur = TX0 + mod((t - T[4]) * 260, 880);
  const gx = TX0 + 3 * BW + 30;

  // chart (c1)
  const o1 = fade(t, T[1] + 0.6, T[2]);
  const kBand = prog(t, T[1] + 6.8, 0.8);

  // zoom (c4): stores leave the core strictly one at a time
  const oz = fade(t, T[4] + 0.3, T[5]);
  const zp = mod(t - T[4] - 0.6, 1.3);
  const zk = prog(zp, 0, 0.85);
  const zo = zp < 1.0 ? 1 : 1 - (zp - 1.0) / 0.3;
  const gxz = lerp(1406, 1612 - 128, zk);

  // batch bars highlight (c5)
  const hl5 = prog(t, T[5] + 5.0, 0.6);

  return (
    <>
      <Stage>
        <Datapath
          g={g}
          show={{ replyRing: 0, ctrl: 0, reqRing: prog(t, T[6] + 0.8, 0.8), sdp: sdpDim, sdpRing: sdpDim }}
          hl={{ core: K.host, sdp: K.sdp, sdpRing: K.sdp, reqRing: K.inline }}
          dimOthers={0.6}
          labels={{
            sdp: ["SDP", "packet engine · doorbells"],
            sdpRing: ["SDP descriptor rings", "TX + RX"],
            reqRing: ["Request cells", "in card DRAM"],
          }}
          cellFill={{ reqRing: (i) => (i < nFill ? K.req : undefined) }}
          cores={4}
          activeCores={1}
        />

        {/* c0: doorbell → SDP fetches the frame → reply into the host ring */}
        <g opacity={o0}>
          <Packet pts={r.doorbell} k={prog(t, T[0] + 0.9, 1.3)} color={K.uc} />
          <Packet pts={r.sdpIn} k={prog(t, T[0] + 2.6, 1.4)} color={K.req} />
          <Packet pts={r.sdpReply} k={prog(t, T[0] + 4.3, 1.4)} color={K.rep} />
        </g>

        {/* c1: the ping-pong loop keeps running on the small diagram */}
        <g opacity={oLoop}>
          <Packet pts={r.doorbell} k={cyc(ph, 0.0, 0.8)} color={K.uc} r={8} />
          <Packet pts={r.sdpIn} k={cyc(ph, 0.9, 0.8)} color={K.req} r={8} />
          <Packet pts={r.sdpReply} k={cyc(ph, 1.9, 0.9)} color={K.rep} r={8} />
        </g>

        {/* c2–c5: one doorbell per packet, streaming */}
        <PacketStream pts={r.doorbell} travel={(t - T[2]) * 240} gap={80} size={12} color={K.uc} opacity={oStream} />
        <PacketStream pts={r.sdpIn} travel={(t - T[2]) * 240 - 40} gap={80} size={12} color={K.req} opacity={oStream * 0.8} />

        {/* c4: the doorbell itself */}
        <Link pts={r.doorbell} color={K.uc} w={5} glow opacity={o4} />
        <InfTag x={424} y={148} opacity={o4} />
        <Arrow x1={440} y1={226} x2={440} y2={298} color={K.uc} w={3} head={12} opacity={o4} />

        {/* c6: SDP path crossed out, inline write into card memory */}
        <g opacity={o6}>
          <Link pts={r.doorbell} color={K.uc} w={4} dash="10 10" opacity={0.55} />
          <Cross x={xMid[0]} y={xMid[1]} s={13} />
        </g>
        <Link
          pts={r.inline}
          k={prog(t, T[6] + 1.0, 1.0)}
          color={K.inline}
          w={7}
          glow
          opacity={0.75 + 0.25 * Math.sin((t - T[6]) * 5)}
        />
        {land.map((a, i) => (
          <g key={i} opacity={t < a + 1.3 ? 1 : 0}>
            <Packet pts={toCell(i)} k={prog(t, a, 0.9)} color={K.req} trail={false} />
          </g>
        ))}

        {/* c1 chart: message rate vs W */}
        <g opacity={o1} fontFamily={FONT}>
          <Axes
            x0={1260}
            x1={1840}
            y0={780}
            y1={200}
            sx={sx}
            sy={sy}
            xticks={[1, 2, 4, 8, 16, 32, 64, 128, 256, 512].map((v) => [v, String(v)] as [number, string])}
            yticks={[0, 2, 4, 6, 8, 10].map((v) => [v, String(v)] as [number, string])}
            xlabel="window W (messages in flight, log scale)"
            ylabel="M msg/s"
          />
          <rect
            x={sx(128)}
            y={sy(7.7)}
            width={(sx(512) - sx(128)) * kBand}
            height={sy(7.2) - sy(7.7)}
            fill={K.sdp}
            fillOpacity={0.45}
            stroke={K.sdp}
            strokeWidth={2}
          />
          <Series pts={RISE} sx={sx} sy={sy} k={prog(t, T[1] + 4.0, 2.2)} color={K.sdp} dots={false} />
          <Series pts={[[32, 5.06], [128, 7.45]]} sx={sx} sy={sy} k={prog(t, T[1] + 6.2, 0.7)} color={K.sdp} dash="10 8" dots={false} />
          <circle cx={sx(1)} cy={sy(0.2)} r={9} fill={K.sdp} opacity={prog(t, T[1] + 1.0, 0.4)} />
          <g opacity={prog(t, T[1] + 6.2, 0.4)}>
            <circle cx={sx(32)} cy={sy(5.06)} r={8} fill={K.sdp} />
            <text x={sx(32) - 14} y={sy(5.06) - 16} textAnchor="end" fontSize={24} fontWeight={700} fill={K.sdp}>
              5.06
            </text>
          </g>
          <g opacity={prog(t, T[1] + 1.2, 0.5)}>
            <line x1={1290} y1={548} x2={sx(1) + 4} y2={sy(0.2) - 12} stroke={C.muted} strokeWidth={2} />
            <text x={1285} y={480} fontSize={24} fill={C.muted}>
              W = 1
            </text>
            <text x={1285} y={516} fontSize={28} fontWeight={800} fill={K.sdp}>
              RTT p50 4.85 µs
            </text>
          </g>
          <g opacity={kBand}>
            <text x={1840} y={258} textAnchor="end" fontSize={28} fontWeight={800} fill={K.sdp}>
              7.2–7.7 M msg/s
            </text>
            <text x={1840} y={294} textAnchor="end" fontSize={24} fill={C.muted}>
              plateau, W ≥ 128
            </text>
          </g>
        </g>

        {/* c3 bars: TX queues */}
        <g opacity={fade(t, T[3] + 0.1, T[4])} fontFamily={FONT}>
          <Bars
            x0={1220}
            x1={1840}
            y0={780}
            y1={300}
            max={10}
            size={28}
            data={[8.36, 7.32, 6.59, 7.06].map((v, i) => ({ label: String([1, 2, 4, 8][i]), value: v, color: K.sdp, text: v.toFixed(2) }))}
            k={[0, 1, 2, 3].map((i) => prog(t, T[3] + 0.2 + i * 0.25, 0.5))}
          />
          <text x={1530} y={866} textAnchor="middle" fontSize={24} fill={C.muted}>
            TX queues (each with its own doorbell)
          </text>
        </g>

        {/* c4 zoom: one 8-byte store at a time */}
        <g opacity={oz} fontFamily={FONT} transform="translate(0 90)">
          <rect x={1190} y={290} width={210} height={130} rx={16} fill={`${K.host}22`} stroke={K.host} strokeWidth={3} />
          <text x={1295} y={355} textAnchor="middle" dominantBaseline="central" fontSize={30} fontWeight={800} fill={C.text}>
            CPU core
          </text>
          <rect x={1620} y={290} width={230} height={130} rx={16} fill={`${K.sdp}22`} stroke={K.sdp} strokeWidth={3} />
          <text x={1735} y={336} textAnchor="middle" dominantBaseline="central" fontSize={28} fontWeight={800} fill={K.sdp}>
            doorbell
          </text>
          <text x={1735} y={378} textAnchor="middle" dominantBaseline="central" fontSize={22} fontFamily={MONO} fill={C.muted}>
            SDP · BAR0
          </text>
          <line x1={1400} y1={355} x2={1620} y2={355} stroke={C.line} strokeWidth={3} strokeDasharray="8 8" />
          {t > T[4] + 0.6 && (
            <g opacity={zo}>
              {Array.from({ length: 8 }, (_, i) => (
                <rect key={i} x={gxz + i * 16} y={341} width={13} height={28} rx={3} fill={K.uc} />
              ))}
              <text x={gxz + 64} y={322} textAnchor="middle" fontSize={24} fontWeight={800} fill={K.uc}>
                8 B
              </text>
            </g>
          )}
        </g>

        {/* c5 bars: packets per doorbell */}
        <g opacity={fade(t, T[5] + 0.1, T[6])} fontFamily={FONT}>
          <Bars
            x0={1220}
            x1={1840}
            y0={780}
            y1={260}
            max={60}
            size={28}
            data={[
              { label: "1", value: 8.36, color: K.sdp, text: "8.36" },
              { label: "8", value: 36.7, color: K.sdp, text: "36.7" },
              { label: "32", value: 50.4, color: K.sdp, text: "50.4" },
            ]}
            k={[0, 1, 2].map((i) => prog(t, T[5] + 0.2 + i * 0.6, 0.7))}
          />
          <text x={1530} y={866} textAnchor="middle" fontSize={24} fill={C.muted}>
            packets per doorbell (batch)
          </text>
          <rect
            x={1250}
            y={650}
            width={146}
            height={144}
            rx={14}
            fill="none"
            stroke={K.uc}
            strokeWidth={4}
            strokeDasharray="12 8"
            opacity={hl5}
            filter="url(#glow)"
          />
        </g>

        {/* bottom-left timeline (c2–c5) */}
        <g opacity={oTL} fontFamily={FONT}>
          <line x1={TX0} y1={800} x2={TX0 + 880} y2={800} stroke={C.dim} strokeWidth={2.5} />
          {[0, 240, 480, 720, 960].map((ns) => (
            <g key={ns}>
              <line x1={TX0 + ns * PXNS} y1={800} x2={TX0 + ns * PXNS} y2={809} stroke={C.dim} strokeWidth={2} />
              <text x={TX0 + ns * PXNS} y={835} textAnchor="middle" fontSize={22} fill={C.muted}>
                {ns}
              </text>
            </g>
          ))}
          <text x={TX0 + 880} y={868} textAnchor="end" fontSize={22} fill={C.muted}>
            time (ns)
          </text>
          <text x={60} y={rowY} dominantBaseline="central" fontSize={24} fontWeight={700} fill={K.host} opacity={clamp01(1 - 2 * kq) * (1 - k5)}>
            host core
          </text>
          {[0, 1, 2, 3].map((q) => (
            <text key={q} x={60} y={qRow(q)} dominantBaseline="central" fontSize={24} fill={C.muted} opacity={clamp01(2 * kq - 1)}>
              queue {q}
            </text>
          ))}
          <g opacity={k5} fontSize={24} fontWeight={700}>
            <text x={60} y={rowY} dominantBaseline="central" fill={C.text}>
              batch 1
            </text>
            <text x={60} y={762} dominantBaseline="central" fill={C.text}>
              batch 32
            </text>
          </g>

          {Array.from({ length: NB }, (_, j) => {
            const kw = prog(t, T[2] + 0.3 + j * 0.35, 0.3);
            if (kw <= 0) return null;
            const q = j % 4;
            const cy = lerp(rowY, qRow(q), kq);
            const h = lerp(50, 30, kq);
            const x = TX0 + j * BW + 2;
            const w = (BW - 4) * kw;
            const lit = oCur * clamp01(1 - Math.abs(cur - (x + BW / 2)) / BW);
            return (
              <g key={j}>
                <rect x={x} y={cy - h / 2} width={w} height={h} rx={6} fill={K.uc} fillOpacity={0.28 + 0.5 * lit} stroke={K.uc} strokeWidth={2} />
                <g textAnchor="middle" dominantBaseline="central" fontSize={20} fill={C.text}>
                  <text x={x + BW / 2} y={cy} opacity={kw * fade(t, T[2] + 0.3, T[3])}>
                    doorbell
                  </text>
                  <text x={x + BW / 2} y={cy} opacity={kq}>
                    q{q}
                  </text>
                  <text x={x + BW / 2} y={cy} opacity={fade(t, T[4] + 0.8, T[5])}>
                    store
                  </text>
                </g>
                <rect x={x + 5} y={cy - h / 2 + 6} width={9} height={h - 12} rx={2} fill={K.req} opacity={k5} />
              </g>
            );
          })}

          {/* c2: 120 ns bracket */}
          <g opacity={fade(t, T[2] + 0.9, T[3])}>
            <path d={`M${TX0 + 2} 672 V662 H${TX0 + BW - 2} V672`} stroke={K.timer} fill="none" strokeWidth={2.5} />
            <text x={TX0 + BW / 2} y={652} textAnchor="middle" fontSize={24} fontWeight={700} fill={K.timer}>
              120 ns
            </text>
          </g>

          {/* c4: sweeping "now", and a second store that never overlaps */}
          <line x1={cur} x2={cur} y1={664} y2={736} stroke={K.timer} strokeWidth={3} opacity={oCur} />
          <g opacity={fade(t, T[4] + 1.8, T[5])}>
            <rect x={gx} y={742} width={BW - 4} height={40} rx={6} fill="none" stroke={K.uc} strokeWidth={2} strokeDasharray="7 6" />
            <text x={gx + (BW - 4) / 2} y={762} textAnchor="middle" dominantBaseline="central" fontSize={20} fill={K.uc}>
              store?
            </text>
            <Cross x={gx + (BW - 4) / 2} y={762} s={15} opacity={prog(t, T[4] + 3.0, 0.4)} />
            <g opacity={prog(t, T[4] + 3.0, 0.5)}>
              <InfTag x={gx + BW + 14} y={747} />
              <text x={gx + BW + 96} y={762} dominantBaseline="central" fontSize={26} fontWeight={700} fill={K.uc}>
                one at a time
              </text>
            </g>
          </g>

          {/* c5: batch 32 = one doorbell after 32 packets */}
          <g opacity={k5}>
            {Array.from({ length: 32 }, (_, i) => (
              <rect key={i} x={TX0 + 4 + i * 14.6} y={746} width={9} height={32} rx={2} fill={K.req} opacity={prog(t, T[5] + 0.4 + i * 0.04, 0.2)} />
            ))}
            <rect
              x={TX0 + 6 + 32 * 14.6}
              y={742}
              width={BW - 4}
              height={40}
              rx={6}
              fill={K.uc}
              fillOpacity={0.28}
              stroke={K.uc}
              strokeWidth={2}
              opacity={prog(t, T[5] + 1.8, 0.4)}
            />
            <rect x={790} y={591} width={18} height={18} rx={3} fill={K.req} />
            <text x={816} y={600} dominantBaseline="central" fontSize={24} fill={C.muted}>
              packet
            </text>
            <rect x={930} y={591} width={18} height={18} rx={3} fill={K.uc} />
            <text x={956} y={600} dominantBaseline="central" fontSize={24} fill={C.muted}>
              doorbell
            </text>
          </g>
        </g>
      </Stage>

      {/* c0 */}
      <Pill x={735} y={318} color={K.uc} size={26} style={appear(t, T[0] + 1.2, T[1])}>
        doorbell: new work
      </Pill>

      {/* c1 headline numbers */}
      <Stat
        value={`${countUp(t, T[1] + 1.0, 1.2, 4.85, 2)} µs`}
        label="W = 1 round trip, p50"
        color={K.sdp}
        size={64}
        style={{ position: "absolute", left: 60, top: 625, width: 470, ...appear(t, T[1] + 1.0, T[2]) }}
      />
      <Stat
        value="7.2–7.7"
        label="M msg/s, plateau from W = 128"
        color={K.sdp}
        size={64}
        style={{ position: "absolute", left: 570, top: 625, width: 520, ...appear(t, T[1] + 6.8, T[2]) }}
      />

      {/* right-panel titles, one per beat */}
      <Txt x={1180} y={120} size={26} weight={700} style={appear(t, T[1] + 0.6, T[2])}>
        SDP ping-pong: message rate vs window
      </Txt>
      <Txt x={1180} y={120} size={26} weight={700} style={appear(t, T[3] + 0.1, T[4])}>
        One host core, 1 → 8 TX queues · M pkt/s
      </Txt>
      <Txt x={1180} y={120} size={26} weight={700} style={appear(t, T[4] + 0.3, T[5])}>
        One doorbell, up close
      </Txt>
      <Txt x={1180} y={120} size={26} weight={700} style={appear(t, T[5] + 0.1, T[6])}>
        Packets per doorbell · one way, 1 core · M pkt/s
      </Txt>

      {/* c2 headline */}
      <Stat
        value={
          <>
            {countUp(t, T[2] + 0.3, 1.0, 8.36, 2)}
            <span style={{ fontSize: 44 }}> M pkt/s</span>
          </>
        }
        label={
          <>
            ≈ <b style={{ color: K.timer }}>120 ns</b> per packet
            <br />
            one host core · batch 1 · one way
          </>
        }
        color={K.uc}
        style={{ position: "absolute", left: 1190, top: 320, width: 670, ...appear(t, T[2] + 0.25, T[3]) }}
      />

      {/* c3 */}
      <Pill x={1240} y={186} color={K.uc} style={appear(t, T[3] + 2.4, T[4])}>
        separate doorbells don't overlap
      </Pill>

      {/* c4: label on the doorbell + zoom text */}
      <Txt x={424} y={184} size={24} weight={700} color={K.uc} style={{ opacity: o4 }}>
        8-byte uncached store
      </Txt>
      <Txt x={1190} y={560} size={26} style={{ ...appear(t, T[4] + 0.8, T[5]), fontFamily: MONO }}>
        rte_write64(…) → BAR0 doorbell
      </Txt>
      <Txt x={1190} y={620} w={660} size={30} color={C.text} style={appear(t, T[4] + 1.6, T[5])}>
        <span style={{ color: K.inf, fontFamily: MONO, fontWeight: 700, fontSize: 24 }}>[INF]</span> a core issues these
        stores <b style={{ color: K.uc }}>strictly one at a time</b>, ≈ 120 ns each
      </Txt>

      {/* c5 */}
      <Pill x={1170} y={598} color={K.uc} style={appear(t, T[5] + 5.0, T[6])}>
        request–reply loop
      </Pill>

      {/* bottom-left titles */}
      <Txt x={60} y={580} size={26} weight={700} style={appear(t, T[2] + 0.1, T[5])}>
        One host core, one packet per doorbell
      </Txt>
      <Txt x={60} y={580} size={26} weight={700} style={appear(t, T[5] + 0.3, T[6])}>
        Batch 1 vs 32 <span style={{ color: C.muted, fontWeight: 400, fontStyle: "italic" }}>(schematic)</span>
      </Txt>

      {/* c6 */}
      <Txt x={720} y={585} w={480} size={34} weight={800} color={K.inline} style={appear(t, T[6] + 1.6)}>
        write straight into card memory
      </Txt>
    </>
  );
}

export const S03: SceneDef = {
  id: "S03",
  title: "Baseline: SDP and its doorbell",
  stage: 0,
  source: "pingpong · bench/batch_sweep.sh · octeon_ep PMD",
  dur: DUR,
  cues,
  C: Body,
};
