import React from "react";
import { Cue, FONT, Pt, SceneDef, along, clamp01, lerp, lin, pathD, prog, useT } from "../lib";
import { Cell, Flow, P, Stage, TAB, Tag, Text, alpha, win } from "./kit";
import { Brace, ends, passGlow, stream } from "./_CcSceneProof";

// <cues>
const cues: Cue[] = [
  [0.60, 9.52, "So far the NIC only echoed messages. Does the gain survive a real network? We wired it to a second server over **100GbE**.", "So far, the nick only echoed messages back. Does the gain survive a real network? We connected it to a second server over a hundred gigabit Ethernet cable."], // 0
  [9.87, 19.25, "Now every request crosses the full path: client NIC, cable, our NIC's Ethernet engine, PCIe, the host, and back.", "Now every request crosses the full path: the client's nick, the cable, our card's Ethernet engine, P C I E, and the host, and then all the way back."], // 1
  [19.60, 33.78, "64-byte round trip: **10.21 µs** with the standard interface, **5.96 µs** with DirectPush: **−4.25 µs** (−42%).", "For sixty-four byte requests, the round trip drops from ten point two one microseconds with the standard interface to five point nine six with DirectPush. That saves four and a quarter microseconds, forty-two percent, with the wire included."], // 2
  [34.13, 44.51, "About **4.3 µs** of that is cable and Ethernet hardware. The host–NIC part itself fell from **5.9 µs to 1.7 µs**.", "About four point three microseconds of that is the fixed cost of the cable and the Ethernet hardware. The host to nick part itself fell from five point nine to one point seven microseconds."], // 3
  [44.86, 54.40, "Echoed by the card alone, with no host, a round trip still takes **3.8–4.3 µs**: the floor of cable and Ethernet engines.", "Echoing on the card alone, with no host, a round trip still takes three point eight to four point three microseconds: the floor of the cable and the Ethernet engines."], // 4
  [54.75, 70.67, "With a cached CQE peek instead of empty polls, the host–NIC part is back under 1 µs: **0.935 µs**. Round trip: **5.29 µs**.", "With a later fix, the card peeks at a cached completion entry instead of slow empty polling. The host to nick part is back under a microsecond, zero point nine three five, and the round trip is down to five point two nine, or four point seven six in a controlled exploration."], // 5
  [71.02, 82.16, "The gain holds at every size: **10.4 → 6.6 µs** at 256 B, **15.1 → 11.9 µs** at 4 KiB.", "The gain holds at every message size: from ten point four to six point six microseconds at two hundred fifty-six bytes, and from fifteen point one to eleven point nine at four kilobytes."], // 6
  [82.51, 94.53, "Under load, the tail stays low: at **2 M requests/s**, the 64 B p99 is **12.6 µs** instead of **26.3 µs**.", "It holds under load, too. At two million requests per second, the ninety-ninth percentile latency for sixty-four byte requests is twelve point six microseconds, instead of twenty-six point three."], // 7
  [94.88, 106.22, "Can **one host core** fill a 100GbE link with small packets? With 64-byte UDP payloads: **99.76 Gbps**, line rate.", "And can a single host core keep a hundred gigabit link full of small packets? With sixty-four byte U D P payloads, one core drives ninety-nine point seven six gigabits per second: line rate."], // 8
  [106.57, 119.27, "The best-tuned standard interface gets about **half** of that. Bare 64 B frames hit the NIC's own ~100 Mpps send limit.", "The best-tuned standard interface reaches only about half of that on one core. And for bare sixty-four byte frames, the limit becomes the card's own transmit path, at about a hundred million packets per second, not the host."], // 9
];
const DUR = 120.67; // <dur>
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
// b4: the card echoes on its own, no host: second server → cable → the NIC's Ethernet engine → back
const R_CECHO: Pt[] = [[XS, LANE_DN], [904, LANE_DN], [888, YP], [904, LANE_UP], [XS, LANE_UP]];
// b5: the host–NIC part alone: the NIC pushes the request into the host, the host answers
const R_HOSTRT: Pt[] = [[904, LANE_DN], [XH, LANE_DN], [XH - 16, YP], [XH, LANE_UP], [904, LANE_UP]];
const ECHO_DUR = 2.3; // whole loop (s), at the speed of the full-path loops
const HOST_DUR = 1.9;
const LOOPS_ECHO: number[] = [];
for (let s = T[4] + 0.3; s + ECHO_DUR <= T[5] + 0.1; s += 2.8) LOOPS_ECHO.push(s);
const LOOPS_HOST: number[] = [];
for (let s = T[5] + 0.3; s + HOST_DUR <= T[6] + 0.1; s += 2.4) LOOPS_HOST.push(s);

/** Fractions [k in, k out] of a path spent inside each stage's x-span (sampled once). */
function spansOf(pts: Pt[]): Record<Stg, [number, number][]> {
  const out = {} as Record<Stg, [number, number][]>;
  for (const key of Object.keys(XR) as Stg[]) {
    const [x0, x1] = XR[key];
    const runs: [number, number][] = [];
    let k0 = -1;
    for (let i = 0; i <= 400; i++) {
      const k = i / 400;
      const x = along(pts, k)[0];
      const inside = x >= x0 && x <= x1;
      if (inside && k0 < 0) k0 = k;
      if ((!inside || i === 400) && k0 >= 0) {
        runs.push([k0, k]);
        k0 = -1;
      }
    }
    out[key] = runs;
  }
  return out;
}
const SP_ECHO = spansOf(R_CECHO);
const SP_HOST = spansOf(R_HOSTRT);

/** Glow of a stage while the cells launched at `loops` (each crossing its path in dur s) are inside it. */
function pathGlow(t: number, loops: number[], dur: number, sp: Record<Stg, [number, number][]>, key: Stg) {
  let g = 0;
  for (const ls of loops) for (const [a, b] of sp[key]) g = Math.max(g, passGlow(t, ls + a * dur, ls + b * dur));
  return g;
}

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
// b4: the card's own echo (no host) took 3.8–4.3 µs; b5, a later run with the cached CQE peek: 5.285 µs end to end,
// of which the card-timed host–NIC round trip is 0.935 µs
const X_FLOOR = BX + 3.8 * US;
const ROW3 = 720;
const X_PEEK = BX + 5.285 * US;
const X_PEEK_FIX = X_PEEK - 0.935 * US;
const X_LINE = BX + 100 * GB;

// b6: round trip at every message size (W=1 p50, µs), drawn to scale from the same origin as the b2 bars
const SIZES = [
  { label: "64 B", std: 10.21, dp: 5.96, stdText: "10.21 µs", dpText: "5.96 µs" },
  { label: "256 B", std: 10.38, dp: 6.62, stdText: "10.4", dpText: "6.6" },
  { label: "1 KiB", std: 10.95, dp: 9.84, stdText: "10.9", dpText: "9.8" },
  { label: "4 KiB", std: 15.11, dp: 11.94, stdText: "15.1", dpText: "11.9" },
];
const SZ_US = 66; // px per µs
const SZ_BAR = 32;
const SZ_GAP = 6; // between the two bars of one size
const SZ_GH = 2 * SZ_BAR + SZ_GAP;
const szY = (g: number) => 452 + 104 * g; // top of size g's bar pair
// b7: 64 B p99 (µs) vs offered load (M requests/s); the standard interface was measured up to 2.75 M/s
const LOAD_STD: Pt[] = [[0.5, 14.32], [1.0, 16.42], [1.5, 20.05], [2.0, 26.25], [2.5, 29.05], [2.75, 36.05]];
const LOAD_DP: Pt[] = [[0.5, 7.83], [1.0, 9.21], [1.5, 11.11], [2.0, 12.62], [2.5, 16.4], [3.0, 19.1], [3.5, 22.55]];
const LX = (v: number) => 630 + ((v - 0.5) / 3) * 1000; // 0.5 → 630, 3.5 → 1630
const LY = (v: number) => 818 - 9 * v; // 0 → 818, 40 µs → 458
const LX1 = 1670; // right end of the grid
const PX_STD = LOAD_STD.map(([v, us]): Pt => [LX(v), LY(us)]);
const PX_DP = LOAD_DP.map(([v, us]): Pt => [LX(v), LY(us)]);
/** b7: the two narrated points at 2 M requests/s */
const LIT = [
  { p: [LX(2), LY(12.62)] as Pt, color: P.push, text: "12.6 µs" },
  { p: [LX(2), LY(26.25)] as Pt, color: GREY, text: "26.3 µs" },
];

/** Glow of a stage while one request (network → host) and its reply (host → network) cross it; the loop starts at ls. */
function loopGlow(t: number, ls: number, key: Stg) {
  const [x0, x1] = XR[key];
  const span = XS - XH;
  const frac = (d: number) => Math.min(span, Math.max(0, d)) / span;
  const tq = (x: number) => ls + frac(XS - x) * DQ;
  const tr = (x: number) => ls + DQ + DWELL + frac(x - XH) * DQ;
  return Math.max(passGlow(t, tq(x1), tq(x0)), passGlow(t, tr(x0), tr(x1)));
}

/** The part of a left-to-right polyline with x ≤ xc (the sweep that draws the b5 curves). */
function upTo(pts: Pt[], xc: number): Pt[] {
  const out: Pt[] = [];
  for (let i = 0; i < pts.length; i++) {
    const [x, y] = pts[i];
    if (x <= xc) {
      out.push(pts[i]);
      continue;
    }
    if (i > 0) out.push([xc, lerp(pts[i - 1][1], y, (xc - pts[i - 1][0]) / (x - pts[i - 1][0]))]);
    break;
  }
  return out;
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
  const cableLabel = prog(t, T[0] + 5.6, 0.6) * (1 - prog(t, T[6], 0.6));
  const yc = lerp(Y_HOME, Y_UP, prog(t, T[2] + 0.1, 0.9));
  const echoK = lin(t, T[0] + 1.3, 2.0);

  // stage glows: the b1 request loop, the b3 fixed vs host–NIC split, the b4 echo and b5 host–NIC loops,
  // b8–b9 line rate from one host core
  const g1 = (key: Stg) => Math.max(...LOOPS_B1.map((ls) => loopGlow(t, ls, key)));
  const gE = (key: Stg) => pathGlow(t, LOOPS_ECHO, ECHO_DUR, SP_ECHO, key);
  const gH = (key: Stg) => pathGlow(t, LOOPS_HOST, HOST_DUR, SP_HOST, key);
  const tint = prog(t, T[3] + 1.4, 0.7) * (1 - prog(t, T[6], 0.5));
  // b4 dwells on the fixed share (the host side steps back), b5 on the host–NIC share (the fixed side steps back)
  const fEcho = win(t, T[4] + 0.1, T[5]);
  const fPeek = win(t, T[5] + 0.1, T[6]);
  const dimH = 1 - 0.62 * fEcho;
  const tintH = tint * (1 - fEcho);
  const tintF = tint * (1 - 0.6 * fPeek);
  const rate = prog(t, T[8] + 1.0, 1.4);
  const core = prog(t, T[8] + 0.8, 0.6);
  const glow: Record<Stg, number> = {
    host: Math.max(g1("host"), 0.7 * tintH, core, gH("host")),
    pcie: Math.max(g1("pcie"), 0.7 * tintH, 0.6 * rate, gH("pcie")),
    nic: Math.max(g1("nic"), 0.45 * rate, gE("nic"), gH("nic")),
    cable: Math.max(g1("cable"), 0.6 * tintF, 0.7 * rate, gE("cable")),
    srv: Math.max(g1("srv"), 0.6 * tintF, 0.45 * rate, gE("srv")),
  };
  const split3 = t > T[3] && t < T[6] + 0.6;
  const trail = prog(t, LOOPS_B1[0], 0.3) * (1 - 0.5 * prog(t, T[2], 0.8)) * (1 - prog(t, T[4], 0.6));

  // ---------------------------------------------------------------- latency bars (b2–b3), the floor (b4), the host–NIC part (b5)
  const lat = prog(t, T[2] + 0.6, 0.7) * (1 - prog(t, T[6], 0.6));
  const k1 = prog(t, T[2] + 1.4, 1.2);
  const k2 = prog(t, T[2] + 5.4, 1.0);
  const w1 = (X_STD - BX) * k1;
  const w2 = (X_DP - BX) * k2;
  const totals = 1 - prog(t, T[3] + 0.2, 0.5);
  const saved = prog(t, T[2] + 8.8, 0.7) * (1 - prog(t, T[3], 0.5));
  const split = prog(t, T[3] + 0.5, 0.9);
  const heads = prog(t, T[3] + 1.0, 0.6);
  const parts = prog(t, T[3] + 5.2, 0.6);
  // b4: the host–NIC segments step back, a band marks the card's own echo and the heading takes its range (in turn)
  const hnDim = 1 - 0.55 * fEcho;
  const band = prog(t, T[4] + 3.9, 0.5) * (1 - 0.5 * fPeek);
  const numOld = 1 - prog(t, T[4] + 3.6, 0.25);
  const numNew = prog(t, T[4] + 3.85, 0.25);
  // b5: rows 1–2 step back; row 3 grows, then its host–NIC part, the total over the old bar's ghost, the exploratory arm
  const rowsDim = 1 - 0.45 * fPeek;
  const r3 = prog(t, T[5] + 1.2, 0.5);
  const k3 = prog(t, T[5] + 1.6, 1.2);
  const w3 = (X_PEEK - BX) * k3;
  const hn3 = prog(t, T[5] + 7.3, 0.5);
  const ghost = prog(t, T[5] + 10.0, 0.5);
  const tot3 = prog(t, T[5] + 10.6, 0.5);
  const expl = prog(t, T[5] + 12.6, 0.5);

  // ---------------------------------------------------------------- every message size (b6)
  const sizes = win(t, T[6], T[7]);
  // the narrated size stays bright while the others dim: 256 B over its span (+2.2–6.0), 4 KiB over its own (+7.2–10.6)
  const focus = [0, win(t, T[6] + 2.2, T[6] + 5.5), 0, win(t, T[6] + 7.2, T[6] + 10.1)];
  const dimOthers = focus[1] + focus[3]; // the two windows never overlap
  const legend = win(t, T[6] + 0.5, T[8]);

  // ---------------------------------------------------------------- tail under load (b7)
  const load = win(t, T[7] + 0.4, T[8]);
  const sweep = lerp(LX(0.5), LX(3.5), prog(t, T[7] + 0.45, 1.6));
  const guide = prog(t, T[7] + 1.9, 0.5);
  const lit = [prog(t, T[7] + 7.5, 0.5), prog(t, T[7] + 10.2, 0.5)];

  // ---------------------------------------------------------------- throughput bars (b8–b9)
  const thr = prog(t, T[8] + 0.6, 0.7);
  const marker = prog(t, T[8] + 2.4, 0.6);
  const rowB = lerp(540, ROW2, prog(t, T[9] + 0.1, 0.8));
  const kB = prog(t, T[8] + 4.6, 1.6);
  const rowA = prog(t, T[9] + 0.4, 0.6);
  const kA = prog(t, T[9] + 0.8, 1.2);

  return (
    <>
      <Stage>
        {/* ---------------- topology row */}
        <g transform={`translate(0 ${yc})`} fontFamily={FONT}>
          <g opacity={opPcie * dimH}>
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
          <Box x0={XR.host[0]} x1={XR.host[1]} color={P.host} kicker="HOST" name="Xeon" opacity={opHost * dimH} g={glow.host} gcol={split3 ? P.push : P.host} />
          <Box x0={XR.nic[0]} x1={XR.nic[1]} color={P.nic} kicker="NIC · ETHERNET ENGINE" name="Marvell CN106" opacity={opNic} g={glow.nic} gcol={P.nic} />
          <Box x0={XR.srv[0]} x1={XR.srv[1]} color={P.sub} kicker="SECOND SERVER" name="ConnectX-5" opacity={opSrv} g={glow.srv} gcol={split3 ? GREY : P.sub} />
          {/* ports where each link plugs into a machine: PCIe slots (tall), Ethernet ports (short) */}
          {(
            [
              [XR.pcie[0], 46, Math.min(opHost, opPcie) * dimH],
              [XR.pcie[1], 46, Math.min(opNic, opPcie) * dimH],
              [XR.cable[0], 34, cableEnd > XR.cable[0] + 0.5 ? opNic : 0],
              [XR.cable[1], 34, opSrv],
            ] as [number, number, number][]
          ).map(([x, h, op]) => (
            <rect key={x} x={x - 6} y={YP - h / 2} width={12} height={h} rx={4} fill={P.bg1} stroke={alpha(P.sub, 0.55)} strokeWidth={1.5} opacity={op} />
          ))}
          {/* b3–b5: which stages form the host–NIC share (teal) and the fixed share (grey); b4 dwells on the fixed, b5 on the host–NIC */}
          <Brace x0={XR.host[0] + 8} x1={862} y={BOX_H / 2 + 18} color={P.push} k={tint} opacity={tint * (1 - 0.7 * fEcho)} />
          <Brace x0={878} x1={XR.srv[1] - 8} y={BOX_H / 2 + 18} color={GREY} k={tint} opacity={tint * (1 - 0.55 * fPeek)} />
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
          {/* b4: the card's own echo, no host; b5: the host–NIC part alone */}
          {LOOPS_ECHO.map((ls) => {
            const k = (t - ls) / ECHO_DUR;
            return <Cell key={`e${ls}`} pts={R_CECHO} k={k} color={GREY} w={30} h={18} opacity={ends(k, 0.03, 0.03)} />;
          })}
          {LOOPS_HOST.map((ls) => {
            const k = (t - ls) / HOST_DUR;
            return <Cell key={`h${ls}`} pts={R_HOSTRT} k={k} color={P.push} w={30} h={18} opacity={ends(k, 0.03, 0.03)} />;
          })}
          {/* b8–b9: one host core keeps the link full */}
          {stream(t, T[8] + 1.0, 1e9, 0.085, DQ).map(({ i, k }) => (
            <Cell key={i} pts={R_REP} k={k} color={P.push} w={22} h={15} opacity={ends(k, 0.04, 0.05)} />
          ))}
        </g>

        {/* ---------------- latency: 64-byte round trip (b2), split into fixed and host–NIC shares (b3) */}
        <g opacity={lat * rowsDim} fontFamily={FONT}>
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
            <rect x={X_FIX + 2} y={ROW1} width={X_STD - X_FIX - 2} height={BAR_H} fill={GREY} opacity={0.92 * split * hnDim} />
          </g>
          <g clipPath="url(#c10row2)">
            <rect x={BX} y={ROW2} width={X_DP - BX} height={BAR_H} fill={P.push} opacity={0.92 * (1 - split)} />
            <rect x={BX} y={ROW2} width={X_FIX - BX} height={BAR_H} fill={FIX} opacity={split} />
            <rect x={X_FIX + 2} y={ROW2} width={X_DP - X_FIX - 2} height={BAR_H} fill={P.push} opacity={0.92 * split * hnDim} />
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
          <text x={(X_FIX + X_STD) / 2} y={ROW1 + BAR_H / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={700} fill={P.bg0} style={TAB} opacity={parts * hnDim}>
            5.9 µs
          </text>
          <text x={(X_FIX + X_DP) / 2} y={ROW2 + BAR_H / 2 + 1} textAnchor="middle" dominantBaseline="central" fontSize={26} fontWeight={700} fill={P.bg0} style={TAB} opacity={parts * hnDim}>
            1.7 µs
          </text>
        </g>

        {/* ---------------- the card's own echo, no host (b4); a later run with the cached CQE peek (b5) */}
        <g opacity={lat} fontFamily={FONT}>
          <defs>
            <clipPath id="c10row3">
              <rect x={BX} y={ROW3} width={Math.max(0, w3)} height={BAR_H} rx={BAR_H / 2} />
            </clipPath>
            <clipPath id="c10ghost">
              <rect x={X_PEEK - BAR_H / 2} y={ROW3 - 4} width={X_DP - X_PEEK + BAR_H / 2 + 6} height={BAR_H + 8} />
            </clipPath>
          </defs>
          <g opacity={band}>
            <rect x={X_FLOOR} y={ROW1 - 16} width={X_FIX - X_FLOOR} height={ROW2 + BAR_H + 30 - ROW1} fill={alpha(P.ink, 0.07)} />
            <line x1={X_FLOOR} y1={ROW1 - 16} x2={X_FLOOR} y2={ROW2 + BAR_H + 14} stroke={alpha(P.ink, 0.55)} strokeWidth={1.5} strokeDasharray="5 5" />
          </g>
          <g opacity={r3}>
            <text x={LAB_X} y={ROW3 + BAR_H / 2 + 1} textAnchor="end" dominantBaseline="central" fontSize={27} fill={P.ink}>
              + cached CQE peek
            </text>
            <text x={LAB_X} y={ROW3 + BAR_H + 22} textAnchor="end" dominantBaseline="central" fontSize={15} letterSpacing={2.4} fontWeight={600} fill={P.sub}>
              LATER RUN
            </text>
          </g>
          <g clipPath="url(#c10ghost)" opacity={ghost}>
            <rect x={BX} y={ROW3} width={X_DP - BX} height={BAR_H} rx={BAR_H / 2} fill="none" stroke={alpha(P.push, 0.6)} strokeWidth={1.5} strokeDasharray="6 6" />
          </g>
          <g clipPath="url(#c10row3)">
            <rect x={BX} y={ROW3} width={X_PEEK_FIX - BX} height={BAR_H} fill={FIX} />
            <rect x={X_PEEK_FIX + 2} y={ROW3} width={X_PEEK - X_PEEK_FIX - 2} height={BAR_H} fill={P.push} opacity={0.92} />
          </g>
          <g opacity={hn3}>
            <line x1={(X_PEEK_FIX + X_PEEK) / 2} y1={ROW3 + BAR_H + 4} x2={(X_PEEK_FIX + X_PEEK) / 2} y2={ROW3 + BAR_H + 16} stroke={P.push} strokeWidth={2} />
            <text x={(X_PEEK_FIX + X_PEEK) / 2} y={ROW3 + BAR_H + 38} textAnchor="middle" dominantBaseline="central" fontSize={32} fontWeight={700} fill={P.push} style={TAB}>
              0.935 µs
            </text>
          </g>
          <text x={X_DP + 18} y={ROW3 + BAR_H / 2 + 1} dominantBaseline="central" fontSize={30} fontWeight={700} fill={P.push} style={TAB} opacity={tot3}>
            5.29 µs
          </text>
        </g>

        {/* ---------------- round trip at every message size (b6): the narrated size stays bright */}
        <g opacity={sizes} fontFamily={FONT}>
          {[1, 3].map((g) => (
            <rect key={g} x={452} y={szY(g) - 13} width={1262} height={SZ_GH + 26} rx={18} fill={alpha(P.ink, 0.035)} stroke={alpha(P.ink, 0.1)} strokeWidth={1.5} opacity={focus[g]} />
          ))}
          {SIZES.map((r, g) => {
            const y = szY(g);
            const kS = prog(t, T[6] + 0.3 + 0.22 * g, 0.9);
            const kD = prog(t, T[6] + 0.42 + 0.22 * g, 0.9);
            const wS = r.std * SZ_US * kS;
            const wD = r.dp * SZ_US * kD;
            return (
              <g key={r.label} opacity={1 - 0.62 * (dimOthers - focus[g])}>
                <text x={LAB_X} y={y + SZ_GH / 2 + 1} textAnchor="end" dominantBaseline="central" fontSize={27} fill={P.ink} opacity={prog(t, T[6] + 0.5 + 0.22 * g, 0.5)}>
                  {r.label}
                </text>
                <rect x={BX} y={y} width={wS} height={SZ_BAR} rx={SZ_BAR / 2} fill={GREY} opacity={0.92} />
                <rect x={BX} y={y + SZ_BAR + SZ_GAP} width={wD} height={SZ_BAR} rx={SZ_BAR / 2} fill={P.push} opacity={0.92} />
                <text x={BX + wS + 16} y={y + SZ_BAR / 2 + 1} dominantBaseline="central" fontSize={26} fontWeight={700} fill={GREY} style={TAB} opacity={clamp01((kS - 0.6) / 0.4)}>
                  {r.stdText}
                </text>
                <text x={BX + wD + 16} y={y + SZ_BAR + SZ_GAP + SZ_BAR / 2 + 1} dominantBaseline="central" fontSize={26} fontWeight={700} fill={P.push} style={TAB} opacity={clamp01((kD - 0.6) / 0.4)}>
                  {r.dpText}
                </text>
              </g>
            );
          })}
        </g>

        {/* ---------------- 64 B p99 vs offered load (b7): two curves, then the two points at 2 M requests/s */}
        <g opacity={load} fontFamily={FONT}>
          {[0, 20, 40].map((v) => (
            <g key={v}>
              <line x1={BX} y1={LY(v)} x2={LX1} y2={LY(v)} stroke={v ? P.hair : P.hair2} strokeWidth={v ? 1.5 : 2} />
              <text x={LAB_X} y={LY(v) + 1} textAnchor="end" dominantBaseline="central" fontSize={22} fill={P.sub} style={TAB}>
                {v === 40 ? "40 µs" : v}
              </text>
            </g>
          ))}
          {[1, 2, 3].map((v) => (
            <g key={v}>
              <line x1={LX(v)} y1={LY(0)} x2={LX(v)} y2={LY(0) + 8} stroke={P.hair2} strokeWidth={2} />
              <text x={LX(v)} y={LY(0) + 30} textAnchor="middle" dominantBaseline="central" fontSize={22} fill={P.sub} style={TAB}>
                {v}
              </text>
            </g>
          ))}
          <text x={LX1} y={LY(0) + 30} textAnchor="end" dominantBaseline="central" fontSize={22} fill={P.sub}>
            M requests/s
          </text>
          <line x1={LX(2)} y1={LY(0)} x2={LX(2)} y2={LY(40)} stroke={alpha(P.ink, 0.55)} strokeWidth={1.5} strokeDasharray="6 6" opacity={guide} />
          {([[PX_STD, GREY], [PX_DP, P.push]] as [Pt[], string][]).map(([pts, c]) => {
            const drawn = upTo(pts, sweep);
            return (
              <g key={c}>
                {drawn.length > 1 && <path d={pathD(drawn)} fill="none" stroke={c} strokeWidth={4} strokeLinecap="round" strokeLinejoin="round" />}
                {pts.map(([x, y]) => (
                  <circle key={x} cx={x} cy={y} r={5.5} fill={c} opacity={clamp01((sweep - x + 12) / 24)} />
                ))}
              </g>
            );
          })}
          {LIT.map((d, i) => (
            <g key={d.text} opacity={lit[i]}>
              <circle cx={d.p[0]} cy={d.p[1]} r={20} fill={alpha(d.color, 0.35)} filter="url(#ccSoft)" />
              <circle cx={d.p[0]} cy={d.p[1]} r={11} fill="none" stroke={d.color} strokeWidth={2.5} />
              <text x={d.p[0] - 22} y={d.p[1] - 32} textAnchor="end" dominantBaseline="central" fontSize={30} fontWeight={700} fill={d.color} style={TAB}>
                {d.text}
              </text>
            </g>
          ))}
        </g>

        {/* ---------------- throughput from one host core (b8–b9) */}
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
      <Tag x={1219} y={yc + BOX_H / 2 + 30} color={GREY} opacity={win(t, T[4] + 1.5, T[5])}>
        card echo · no host
      </Tag>

      {/* latency overlays */}
      <Text x={KICK_X} y={398} size={17} color={P.sub} opacity={lat} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        64-BYTE ROUND TRIP · LOWER IS BETTER
      </Text>
      <Text x={BX} y={ROW1 - 44} size={21} color={P.sub} opacity={lat * heads}>
        cable + Ethernet hardware{" "}
        <span style={{ position: "relative", color: P.ink, fontWeight: 700, opacity: 1 - 0.5 * fPeek, ...TAB }}>
          <span style={{ opacity: numOld }}>≈4.3 µs</span>
          <span style={{ position: "absolute", left: 0, top: 0, whiteSpace: "nowrap", opacity: numNew }}>3.8–4.3 µs</span>
        </span>
      </Text>
      <Text x={X_FIX + 16} y={ROW1 - 44} size={21} color={P.push} weight={600} opacity={lat * heads}>
        host–NIC part
      </Text>
      <div style={{ position: "absolute", left: (X_DP + X_STD) / 2 - 300, top: ROW2 + BAR_H + 36, width: 600, textAlign: "center", fontFamily: FONT, opacity: lat * saved }}>
        <span style={{ fontSize: 44, fontWeight: 800, color: P.gold, letterSpacing: -1, ...TAB }}>−4.25 µs</span>
        <span style={{ fontSize: 30, fontWeight: 700, color: P.gold, marginLeft: 12, ...TAB }}>(−42%)</span>
        <div style={{ fontSize: 20, color: P.sub, marginTop: 6 }}>with the wire included</div>
      </div>
      <div style={{ position: "absolute", left: X_DP + 170, top: ROW3 + BAR_H / 2 + 1, transform: "translateY(-50%)", display: "flex", alignItems: "center", gap: 14, fontFamily: FONT, whiteSpace: "nowrap", opacity: lat * expl }}>
        <span style={{ fontSize: 15, fontWeight: 600, letterSpacing: 1.6, textTransform: "uppercase", color: P.sub, border: `1px dashed ${alpha(P.sub, 0.7)}`, borderRadius: 999, padding: "5px 14px 4px" }}>
          exploratory · no LDADDA
        </span>
        <span style={{ fontSize: 26, fontWeight: 700, color: P.sub, ...TAB }}>4.76 µs</span>
      </div>

      {/* message sizes (b6) and load (b7) overlays: the kickers take turns, one legend serves both charts */}
      <Text x={KICK_X} y={398} size={17} color={P.sub} opacity={win(t, T[6] + 0.5, T[7])} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        ROUND TRIP · W=1 · p50 · LOWER IS BETTER
      </Text>
      <Text x={KICK_X} y={398} size={17} color={P.sub} opacity={win(t, T[7] + 0.5, T[8])} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        64 B · p99 vs OFFERED LOAD
      </Text>
      <div style={{ position: "absolute", left: 1100, top: 396, width: 600, display: "flex", justifyContent: "flex-end", gap: 36, fontFamily: FONT, fontSize: 22, color: P.ink, opacity: legend }}>
        {([["standard interface", GREY], ["DirectPush", P.push]] as const).map(([name, c]) => (
          <div key={name} style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ width: 28, height: 12, borderRadius: 6, background: c }} />
            {name}
          </div>
        ))}
      </div>

      {/* throughput overlays */}
      <Text x={KICK_X} y={398} size={17} color={P.sub} opacity={thr * prog(t, T[8] + 3.8, 0.6)} style={{ letterSpacing: 2.6, fontWeight: 600 }}>
        64-BYTE UDP PAYLOADS · HIGHER IS BETTER
      </Text>
      <Text x={KICK_X} y={700} w={1400} size={20} color={P.sub} opacity={thr}>
        <span style={{ opacity: prog(t, T[9] + 4.4, 0.7) }}>bare 64 B frames: 99.2 Mpps</span>
        <span style={{ opacity: prog(t, T[9] + 9.1, 0.7) }}>, at the NIC's own send limit (~100 Mpps)</span>
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
