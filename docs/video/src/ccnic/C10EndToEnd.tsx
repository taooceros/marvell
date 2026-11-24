import React from "react";
import { Cue, FONT, Pt, SceneDef, clamp01, lerp, lin, prog, useT } from "../lib";
import { Cell, Flow, P, Stage, TAB, Tag, Text, alpha } from "./kit";
import { Brace, ends, passGlow, stream } from "./_CcSceneProof";

// <cues>
const cues: Cue[] = [
  [0.60, 9.64, "So far the NIC only echoed messages. Does the gain survive a real network? We wired it to a second server over **100GbE**.", "So far, the card only echoed messages back. Does the gain survive a real network? We connected it to a second server over a hundred gigabit Ethernet cable."], // 0
  [9.99, 19.83, "Now every request crosses the full path: client NIC, cable, our NIC's Ethernet engine, PCIe, the host, and back.", "Now every request crosses the full path: the client's card, the cable, our card's Ethernet engine, P C I express, and the host, and then all the way back."], // 1
  [20.18, 34.36, "64-byte round trip: **10.21 µs** with the standard interface, **5.96 µs** with DirectPush: **−4.25 µs** (−42%).", "For sixty-four byte requests, the round trip drops from ten point two one microseconds with the standard interface to five point nine six with DirectPush. That saves four and a quarter microseconds, forty-two percent, with the wire included."], // 2
  [34.71, 45.33, "About **4.3 µs** of that is cable and Ethernet hardware. The host–NIC part itself fell from **5.9 µs to 1.7 µs**.", "About four point three microseconds of that is the fixed cost of the cable and the Ethernet hardware. The host to card part itself fell from five point nine to one point seven microseconds."], // 3
  [45.68, 57.02, "Can **one host core** fill a 100GbE link with small packets? With 64-byte UDP payloads: **99.76 Gbps**, line rate.", "And can a single host core keep a hundred gigabit link full of small packets? With sixty-four byte U D P payloads, one core drives ninety-nine point seven six gigabits per second: line rate."], // 4
  [57.37, 67.65, "The best-tuned standard interface gets about **half** of that. For bare 64 B frames, the NIC's own scheduler is the limit.", "The best-tuned standard interface reaches only about half of that on one core. And for bare sixty-four byte frames, the limit becomes the card's own transmit scheduler, not the host."], // 5
];
const DUR = 69.05; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

type Stg = "host" | "pcie" | "nic" | "cable" | "srv";
/** x-span of each stage of the end-to-end path (host on the left, as in the running Host ⇄ NIC diagram) */
const XR: Record<Stg, [number, number]> = { host: [110, 430], pcie: [430, 620], nic: [620, 1110], cable: [1110, 1390], srv: [1390, 1810] };
const BOX_H = 150;
/** path height inside the row (local y, 0 = row centre): host → network rides the upper lane, network → host the lower */
const YP = 28;
const LANE_UP = YP - 10;
const LANE_DN = YP + 10;
const XH = 265; // a request turns around inside the host …
const XS = 1690; // … and starts inside the second server
const Y_HOME = 470;
const Y_UP = 262;
const DQ = 2.0; // one crossing of the whole path (s)
const DWELL = 0.25;
const LOOP = 2 * DQ + 2 * DWELL;
const R_REQ: Pt[] = [[XS, LANE_DN], [XH, LANE_DN]];
const R_REP: Pt[] = [[XH, LANE_UP], [XS, LANE_UP]];
const R_ECHO: Pt[] = [[XH, LANE_UP], [880, LANE_UP], [896, YP], [880, LANE_DN], [XH, LANE_DN]];
const LOOPS_B1 = [T[1] + 0.5, T[1] + 0.5 + LOOP];
const LOOPS_AMB: number[] = [];
for (let s = T[2] + 1.6; s + LOOP <= T[4] + 0.3; s += LOOP) LOOPS_AMB.push(s);

const GREY = "#8f9ab3"; // the standard interface (as in C05)
const FIX = "#56617a"; // fixed share: cable + Ethernet hardware
// bars: label column right edge, bar origin, scales (drawn to the measured values)
const LAB_X = 566;
const KICK_X = 312;
const BX = 590;
const US = 95; // px per µs
const GB = 9.6; // px per Gbps
const ROW1 = 480;
const ROW2 = 600;
const BAR_H = 46;
const X_FIX = BX + 4.3 * US;
const X_STD = BX + 10.21 * US;
const X_DP = BX + 5.96 * US;
const X_LINE = BX + 100 * GB;

/** Glow of a stage while one request (network → host) and its reply (host → network) cross it; the loop starts at ls. */
function loopGlow(t: number, ls: number, key: Stg) {
  const [x0, x1] = XR[key];
  const span = XS - XH;
  const frac = (d: number) => Math.min(span, Math.max(0, d)) / span;
  const tq = (x: number) => ls + frac(XS - x) * DQ;
  const tr = (x: number) => ls + DQ + DWELL + frac(x - XH) * DQ;
  return Math.max(passGlow(t, tq(x1), tq(x0)), passGlow(t, tr(x0), tr(x1)));
}

/** One machine of the topology row (SVG, local coordinates). */
function Box({ x0, x1, color, kicker, name, opacity, g, gcol }: { x0: number; x1: number; color: string; kicker: string; name: string; opacity: number; g: number; gcol: string }) {
  if (opacity <= 0) return null;
  const y0 = -BOX_H / 2;
  const w = x1 - x0;
  return (
    <g opacity={opacity}>
      {g > 0.01 && <rect x={x0 - 6} y={y0 - 6} width={w + 12} height={BOX_H + 12} rx={24} fill={alpha(gcol, 0.2 * g)} filter="url(#ccSoft)" />}
      <rect x={x0} y={y0} width={w} height={BOX_H} rx={20} fill="rgba(255,255,255,0.022)" stroke={alpha(color, 0.42 + 0.45 * g)} strokeWidth={1.5 + g} />
      {g > 0.01 && <rect x={x0} y={y0} width={w} height={BOX_H} rx={20} fill={alpha(gcol, 0.09 * g)} />}
      <text x={x0 + 24} y={y0 + 33} fontSize={16} letterSpacing={2.4} fontWeight={600} fill={color}>
        {kicker}
      </text>
      <text x={x0 + 24} y={-6} fontSize={30} fontWeight={600} fill={P.ink}>
        {name}
      </text>
    </g>
  );
}

function Body() {
  const t = useT();
  // ---------------------------------------------------------------- topology (b0–b1), then a context strip (b2 on)
  const opHost = prog(t, T[0] + 0.2, 0.7);
  const opPcie = prog(t, T[0] + 0.5, 0.7);
  const opNic = prog(t, T[0] + 0.8, 0.7);
  const cableEnd = lerp(XR.cable[0], XR.cable[1], prog(t, T[0] + 3.9, 1.0));
  const opSrv = prog(t, T[0] + 4.7, 0.7);
  const cableLabel = prog(t, T[0] + 5.6, 0.6) * (1 - prog(t, T[4], 0.6));
  const yc = lerp(Y_HOME, Y_UP, prog(t, T[2] + 0.1, 0.9));
  const echoK = lin(t, T[0] + 1.3, 2.0);

  // stage glows: the b1 request loop, the b3 fixed vs host–NIC split, b4–b5 line rate from one host core
  const g1 = (key: Stg) => Math.max(...LOOPS_B1.map((ls) => loopGlow(t, ls, key)));
  const tint = prog(t, T[3] + 1.4, 0.7) * (1 - prog(t, T[4], 0.5));
  const rate = prog(t, T[4] + 1.0, 1.4);
  const core = prog(t, T[4] + 0.8, 0.6);
  const glow: Record<Stg, number> = {
    host: Math.max(g1("host"), 0.7 * tint, core),
    pcie: Math.max(g1("pcie"), 0.7 * tint, 0.6 * rate),
    nic: Math.max(g1("nic"), 0.45 * rate),
    cable: Math.max(g1("cable"), 0.6 * tint, 0.7 * rate),
    srv: Math.max(g1("srv"), 0.6 * tint, 0.45 * rate),
  };
  const split3 = t > T[3] && t < T[4] + 0.6;
  const trail = prog(t, LOOPS_B1[0], 0.3) * (1 - 0.5 * prog(t, T[2], 0.8)) * (1 - prog(t, T[4], 0.6));

  // ---------------------------------------------------------------- latency bars (b2–b3)
  const lat = prog(t, T[2] + 0.6, 0.7) * (1 - prog(t, T[4], 0.6));
  const k1 = prog(t, T[2] + 1.4, 1.2);
  const k2 = prog(t, T[2] + 5.4, 1.0);
  const w1 = (X_STD - BX) * k1;
  const w2 = (X_DP - BX) * k2;
  const totals = 1 - prog(t, T[3] + 0.2, 0.5);
  const saved = prog(t, T[2] + 8.8, 0.7) * (1 - prog(t, T[3], 0.5));
  const split = prog(t, T[3] + 0.5, 0.9);
  const heads = prog(t, T[3] + 1.0, 0.6);
  const parts = prog(t, T[3] + 5.2, 0.6);

  // ---------------------------------------------------------------- throughput bars (b4–b5)
  const thr = prog(t, T[4] + 0.6, 0.7);
  const marker = prog(t, T[4] + 2.4, 0.6);
  const rowB = lerp(540, ROW2, prog(t, T[5] + 0.1, 0.8));
  const kB = prog(t, T[4] + 4.6, 1.6);
  const rowA = prog(t, T[5] + 0.4, 0.6);
  const kA = prog(t, T[5] + 0.8, 1.2);

  return (
    <>
      <Stage>
        {/* ---------------- topology row */}
        <g transform={`translate(0 ${yc})`} fontFamily={FONT}>
          <g opacity={opPcie}>
            {[-15, -5, 5, 15].map((o) => (
              <line key={o} x1={XR.pcie[0]} y1={YP + o} x2={XR.pcie[1]} y2={YP + o} stroke={glow.pcie > 0.01 ? alpha(P.push, 0.3 + 0.55 * glow.pcie) : P.link} strokeWidth={2} />
            ))}
            <text x={(XR.pcie[0] + XR.pcie[1]) / 2} y={-14} textAnchor="middle" fontSize={20} letterSpacing={2} fontWeight={600} fill={P.sub}>
              PCIe
            </text>
          </g>
          {cableEnd > XR.cable[0] + 0.5 && (
            <g>
              {glow.cable > 0.01 && <line x1={XR.cable[0]} y1={YP} x2={cableEnd} y2={YP} stroke={alpha(split3 ? GREY : P.push, 0.25 * glow.cable)} strokeWidth={38} />}
              <line x1={XR.cable[0]} y1={YP} x2={cableEnd} y2={YP} stroke={alpha(P.sub, 0.2)} strokeWidth={24} />
              <line x1={XR.cable[0]} y1={YP} x2={cableEnd} y2={YP} stroke={alpha(P.sub, 0.6)} strokeWidth={2} />
              <text x={(XR.cable[0] + XR.cable[1]) / 2} y={-14} textAnchor="middle" fontSize={20} letterSpacing={1} fontWeight={600} fill={P.sub} opacity={cableLabel}>
                100GbE · 3 m cable
              </text>
            </g>
          )}
          <Box x0={XR.host[0]} x1={XR.host[1]} color={P.host} kicker="HOST" name="Xeon" opacity={opHost} g={glow.host} gcol={split3 ? P.push : P.host} />
          <Box x0={XR.nic[0]} x1={XR.nic[1]} color={P.nic} kicker="NIC · ETHERNET ENGINE" name="Marvell CN106" opacity={opNic} g={glow.nic} gcol={P.nic} />
          <Box x0={XR.srv[0]} x1={XR.srv[1]} color={P.sub} kicker="SECOND SERVER" name="ConnectX-5" opacity={opSrv} g={glow.srv} gcol={split3 ? GREY : P.sub} />
          {/* ports where each link plugs into a machine: PCIe slots (tall), Ethernet ports (short) */}
          {(
            [
              [XR.pcie[0], 46, Math.min(opHost, opPcie)],
              [XR.pcie[1], 46, Math.min(opNic, opPcie)],
              [XR.cable[0], 34, cableEnd > XR.cable[0] + 0.5 ? opNic : 0],
              [XR.cable[1], 34, opSrv],
            ] as [number, number, number][]
          ).map(([x, h, op]) => (
            <rect key={x} x={x - 6} y={YP - h / 2} width={12} height={h} rx={4} fill={P.bg1} stroke={alpha(P.sub, 0.55)} strokeWidth={1.5} opacity={op} />
          ))}
          {/* b3: which stages form the host–NIC share (teal) and the fixed share (grey) */}
          <Brace x0={XR.host[0] + 8} x1={862} y={BOX_H / 2 + 18} color={P.push} k={tint} opacity={tint} />
          <Brace x0={878} x1={XR.srv[1] - 8} y={BOX_H / 2 + 18} color={GREY} k={tint} opacity={tint} />
          {/* the full path, drawn by the first request loop */}
          <Flow pts={R_REQ} k={lin(t, LOOPS_B1[0], DQ)} color={alpha(P.push, 0.5)} w={2} head={10} opacity={trail} />
          <Flow pts={R_REP} k={lin(t, LOOPS_B1[0] + DQ + DWELL, DQ)} color={alpha(P.push, 0.5)} w={2} head={10} opacity={trail} />
          <Cell pts={R_ECHO} k={echoK > 0 && echoK < 1 ? echoK : -1} color={P.push} w={30} h={18} opacity={ends(echoK, 0.04, 0.04)} />
          {[...LOOPS_B1, ...LOOPS_AMB].map((ls, j) => {
            const kq = (t - ls) / DQ;
            const kr = (t - ls - DQ - DWELL) / DQ;
            return (
              <g key={j}>
                <Cell pts={R_REQ} k={kq} color={P.push} w={30} h={18} opacity={ends(kq, 0.03, 0.03)} />
                <Cell pts={R_REP} k={kr} color={P.push} w={30} h={18} opacity={ends(kr, 0.03, 0.03)} />
              </g>
            );
          })}
          {/* b4–b5: one host core keeps the link full */}
          {stream(t, T[4] + 1.0, 1e9, 0.085, DQ).map(({ i, k }) => (
            <Cell key={i} pts={R_REP} k={k} color={P.push} w={22} h={15} opacity={ends(k, 0.04, 0.05)} />
          ))}
        </g>

        {/* ---------------- latency: 64-byte round trip (b2), split into fixed and host–NIC shares (b3) */}
        <g opacity={lat} fontFamily={FONT}>
          <defs>
            <clipPath id="c10row1">
              <rect x={BX} y={ROW1} width={Math.max(0, w1)} height={BAR_H} rx={BAR_H / 2} />
            </clipPath>
            <clipPath id="c10row2">
              <rect x={BX} y={ROW2} width={Math.max(0, w2)} height={BAR_H} rx={BAR_H / 2} />
            </clipPath>
          </defs>
          <text x={LAB_X} y={ROW1 + BAR_H / 2 + 1} textAnchor="end" dominantBaseline="central" fontSize={27} fill={P.ink}>
            Standard interface
          </text>
          <text x={LAB_X} y={ROW2 + BAR_H / 2 + 1} textAnchor="end" dominantBaseline="central" fontSize={27} fill={P.ink}>
            DirectPush
          </text>
          <g clipPath="url(#c10row1)">
            <rect x={BX} y={ROW1} width={X_STD - BX} height={BAR_H} fill={GREY} opacity={0.92 * (1 - split)} />
            <rect x={BX} y={ROW1} width={X_FIX - BX} height={BAR_H} fill={FIX} opacity={split} />
            <rect x={X_FIX + 2} y={ROW1} width={X_STD - X_FIX - 2} height={BAR_H} fill={GREY} opacity={0.92 * split} />
          </g>
          <g clipPath="url(#c10row2)">
            <rect x={BX} y={ROW2} width={X_DP - BX} height={BAR_H} fill={P.push} opacity={0.92 * (1 - split)} />
            <rect x={BX} y={ROW2} width={X_FIX - BX} height={BAR_H} fill={FIX} opacity={split} />
            <rect x={X_FIX + 2} y={ROW2} width={X_DP - X_FIX - 2} height={BAR_H} fill={P.push} opacity={0.92 * split} />
          </g>
          <text x={BX + w1 + 18} y={ROW1 + BAR_H / 2 + 1} dominantBaseline="central" fontSize={30} fontWeight={700} fill={GREY} style={TAB} opacity={clamp01((k1 - 0.6) / 0.4) * totals}>
            10.21 µs
          </text>
          <text x={BX + w2 + 18} y={ROW2 + BAR_H / 2 + 1} dominantBaseline="central" fontSize={30} fontWeight={700} fill={P.push} style={TAB} opacity={clamp01((k2 - 0.6) / 0.4) * totals}>
            5.96 µs
          </text>
          {/* b2: what DirectPush saves, wire included */}
          <g opacity={saved}>
            <line x1={X_STD} y1={ROW1 + BAR_H + 6} x2={X_STD} y2={ROW2 + BAR_H + 12} stroke={alpha(P.gold, 0.6)} strokeWidth={1.5} strokeDasharray="4 5" />
            <Brace x0={X_DP} x1={X_STD} y={ROW2 + BAR_H + 22} color={P.gold} k={saved} />
          </g>
          {/* b3: the same fixed share in both bars */}
          <line x1={X_FIX} y1={ROW1 - 16} x2={X_FIX} y2={ROW2 + BAR_H + 14} stroke={alpha(P.ink, 0.55)} strokeWidth={1.5} strokeDasharray="5 5" opacity={split} />
          <text x={(X_FIX + X_STD) / 2} y={ROW1 + BAR_H / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={700} fill={P.bg0} style={TAB} opacity={parts}>
            5.9 µs
          </text>
          <text x={(X_FIX + X_DP) / 2} y={ROW2 + BAR_H / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={700} fill={P.bg0} style={TAB} opacity={parts}>
            1.7 µs
          </text>
        </g>

        {/* ---------------- throughput from one host core (b4–b5) */}
        <g opacity={thr} fontFamily={FONT}>
          <g opacity={marker}>
            <line x1={X_LINE} y1={458} x2={X_LINE} y2={664} stroke={alpha(P.ink, 0.6)} strokeWidth={2} strokeDasharray="6 6" />
            <text x={X_LINE} y={444} textAnchor="middle" fontSize={20} fontWeight={600} fill={P.ink}>
              100 Gbps line rate
            </text>
          </g>
          <g opacity={rowA}>
            <text x={LAB_X} y={ROW1 + BAR_H / 2 + 1} textAnchor="end" dominantBaseline="central" fontSize={27} fill={P.ink}>
              Standard interface
            </text>
            <rect x={BX} y={ROW1} width={X_LINE - BX} height={BAR_H} rx={BAR_H / 2} fill="rgba(255,255,255,0.035)" />
            <rect x={BX} y={ROW1} width={Math.max(0, 52.6 * GB * kA)} height={BAR_H} rx={BAR_H / 2} fill={GREY} opacity={0.92} />
            <text x={BX + 52.6 * GB * kA + 18} y={ROW1 + BAR_H / 2 + 1} dominantBaseline="central" fontSize={32} fontWeight={700} fill={GREY} style={TAB} opacity={clamp01((kA - 0.6) / 0.4)}>
              52.6 Gbps
            </text>
          </g>
          <text x={LAB_X} y={rowB + BAR_H / 2 + 1} textAnchor="end" dominantBaseline="central" fontSize={27} fill={P.ink}>
            DirectPush
          </text>
          <rect x={BX} y={rowB} width={X_LINE - BX} height={BAR_H} rx={BAR_H / 2} fill="rgba(255,255,255,0.035)" />
          <rect x={BX} y={rowB} width={Math.max(0, 99.76 * GB * kB)} height={BAR_H} rx={BAR_H / 2} fill={P.push} opacity={0.92} />
          <text x={X_LINE + 22} y={rowB + BAR_H / 2 + 2} dominantBaseline="central" fill={P.push} fontWeight={800} style={TAB} opacity={clamp01((kB - 0.7) / 0.3)}>
            <tspan fontSize={44}>99.76</tspan>
            <tspan fontSize={26} fontWeight={700} dx={8}>
              Gbps
            </tspan>
          </text>
        </g>
      </Stage>

      {/* topology tags */}
      <Tag x={1676} y={yc + BOX_H / 2 + 14} opacity={opSrv * (1 - prog(t, T[2], 0.5))}>
        schematic
      </Tag>
      <Tag x={182} y={yc + BOX_H / 2 + 14} color={P.host} opacity={core}>
        one host core
      </Tag>

      {/* latency overlays */}
      <Text x={KICK_X} y={398} size={17} color={P.sub} opacity={lat} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        64-BYTE ROUND TRIP · LOWER IS BETTER
      </Text>
      <Text x={BX} y={ROW1 - 44} size={21} color={P.sub} opacity={lat * heads}>
        cable + Ethernet hardware <span style={{ color: P.ink, fontWeight: 700, ...TAB }}>≈4.3 µs</span>
      </Text>
      <Text x={X_FIX + 16} y={ROW1 - 44} size={21} color={P.push} weight={600} opacity={lat * heads}>
        host–NIC part
      </Text>
      <div style={{ position: "absolute", left: (X_DP + X_STD) / 2 - 300, top: ROW2 + BAR_H + 36, width: 600, textAlign: "center", fontFamily: FONT, opacity: lat * saved }}>
        <span style={{ fontSize: 44, fontWeight: 800, color: P.gold, letterSpacing: -1, ...TAB }}>−4.25 µs</span>
        <span style={{ fontSize: 30, fontWeight: 700, color: P.gold, marginLeft: 12, ...TAB }}>(−42%)</span>
        <div style={{ fontSize: 20, color: P.sub, marginTop: 6 }}>with the wire included</div>
      </div>

      {/* throughput overlays */}
      <Text x={KICK_X} y={398} size={17} color={P.sub} opacity={thr * prog(t, T[4] + 3.8, 0.6)} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        64-BYTE UDP PAYLOADS · HIGHER IS BETTER
      </Text>
      <Text x={KICK_X} y={700} w={1400} size={20} color={P.sub} opacity={thr * prog(t, T[5] + 4.4, 0.7)}>
        bare 64 B frames: 99.2 Mpps, at the NIC's own transmit-scheduler limit (99.8 Mpps)
      </Text>
    </>
  );
}

export const C10: SceneDef = {
  id: "C10",
  title: "End to end over 100GbE",
  stage: 4,
  source: "Measured: mercury (ConnectX-5) \u21c4 3 m 100GbE \u21c4 CN106 \u21c4 saturn",
  dur: DUR,
  cues,
  C: Body,
};
