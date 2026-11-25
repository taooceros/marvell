import React from "react";
import { Cue, SceneDef, Pt, clamp01, lerp, lin, prog, useT } from "../lib";
import { BigStat, Cell, P, Stage, Tag, alpha, win } from "../ccnic/kit";
import { D, Dim, MmDefs, T as Tx, TimeBar } from "./mk";

// <cues>
const cues: Cue[] = [
  [0.60, 6.94, "Trap three came from an interference test: one core\u00a0keeps\u00a0reading\u00a0the root\u00a0port, while another times the card register.", "Trap three came from an interference test: one core keeps reading the root port, while another times the card register."], // 0
  [7.29, 15.49, "Root-port reads never leave the host chip, yet\u00a0the\u00a0card\u00a0read\u00a0got\u00a0**22.7 ns\u00a0faster**. Someone else's traffic made us faster?", "Root-port reads never leave the host chip, yet the card read got twenty-two point seven nanoseconds faster. Someone else's traffic made us faster?"], // 1
  [15.84, 24.90, "Alone, a card read starts only every ~541 ns, and\u00a0the IIO\u00a0idles\u00a0long\u00a0enough\u00a0to\u00a0doze. Root-port reads every ~233 ns keep it awake.", "Alone, a card read starts only every five hundred forty nanoseconds, and the I I O sits idle long enough to doze. Root-port reads keep it awake."], // 2
  [25.25, 32.35, "So the isolated difference of +312.1 ns includes **18–23 ns** of wake-up that only the card path pays.", "So the isolated three hundred twelve nanoseconds include eighteen to twenty-three of wake-up that only the card path pays."], // 3
  [32.70, 39.96, "Keep the IIO awake for both: card register minus root port is **+290.9 ns** median, **+295.0 ns** mean.", "Keep it awake for both, and the difference is two hundred ninety-one nanoseconds at the median, two hundred ninety-five on average."], // 4
];
const DUR = 41.36; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// ---- the schematic: two readers, one shared IO block
const CPU = { x: 140, y: 188, w: 210, h: 80 };
const OTH = { x: 140, y: 380, w: 210, h: 80 };
const IIO = { x: 760, y: 172, w: 250, h: 304 };
const REG = { x: 1560, y: 188, w: 230, h: 80 };
const CARD_TRIP: Pt[] = [
  [CPU.x + CPU.w, 216],
  [REG.x + 30, 216],
  [REG.x + 30, 240],
  [CPU.x + CPU.w, 240],
];
const RP_TRIP: Pt[] = [
  [OTH.x + OTH.w, 408],
  [890, 408],
  [890, 432],
  [OTH.x + OTH.w, 432],
];
// ---- c1: cpu40's register read, alone vs with a root-port reader (p50, ns)
const BX = 560;
const bx = (v: number) => BX + (v / 517.3) * 1000;
// ---- c2: IIO activity timeline (SCHEMATIC): card read every ~541 ns, root-port read every ~233 ns
const TLX = { x0: 420, x1: 1700, card: 427, root: 184, cw: 70, rw: 46 };
const CARD_X = [0, 1, 2].map((j) => TLX.x0 + j * TLX.card);
const ROOT_X = [0, 1, 2, 3, 4, 5, 6].map((j) => TLX.x0 + 90 + j * TLX.root);
// ---- c3/c4: card register − root port
const WB = { x: 420, y: 650, w: 1200, h: 50, max: 312.1 };

function Box({ r, label, sub, color, k = 1 }: { r: { x: number; y: number; w: number; h: number }; label: string; sub?: string; color: string; k?: number }) {
  return (
    <g>
      <rect x={r.x - 7} y={r.y - 7} width={r.w + 14} height={r.h + 14} rx={18} fill={alpha(color, 0.12 * k)} />
      <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={14} fill={D.node} />
      <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={14} fill={alpha(color, 0.08 + 0.1 * k)} stroke={alpha(color, 0.55 + 0.4 * k)} strokeWidth={1.5 + k} />
      <Tx x={r.x + r.w / 2} y={r.y + r.h / 2 + (sub ? -11 : 0)} anchor="middle" size={26} weight={600}>
        {label}
      </Tx>
      {sub && (
        <Tx x={r.x + r.w / 2} y={r.y + r.h / 2 + 20} anchor="middle" size={18} color={P.sub}>
          {sub}
        </Tx>
      )}
    </g>
  );
}

function Body() {
  const t = useT();
  // c0: both readers run for the whole scene; one round trip in flight per reader
  const dia = prog(t, T[0] + 0.2, 0.6);
  const cardStart = T[0] + 0.9;
  const rpStart = T[0] + 1.5;
  const cardK = t > cardStart ? ((t - cardStart) % 2.8) / 2.6 : -1;
  const rpK = t > rpStart ? ((t - rpStart) % 1.2) / 1.05 : -1;
  const ioWarm = prog(t, T[4] + 0.4, 0.6);
  // c1: the bar shrinks
  const c1 = win(t, T[1] + 0.3, T[2]);
  const grow1 = prog(t, T[1] + 0.4, 0.9);
  const copyK = prog(t, T[1] + 1.8, 0.5);
  const shrink = prog(t, T[1] + 2.4, 0.9);
  const newLab = prog(t, T[1] + 3.3, 0.4);
  const bigK = prog(t, T[1] + 3.5, 0.5);
  const qK = prog(t, T[1] + 5.4, 0.5);
  // c2: the IIO dozes between card reads
  const c2 = win(t, T[2] + 0.5, T[3]);
  const scanX = lerp(TLX.x0, TLX.x1, lin(t, T[2] + 0.8, 3.0));
  const zzK = prog(t, T[2] + 2.6, 0.6);
  const row2 = prog(t, T[2] + 4.2, 0.5);
  const scan2X = lerp(TLX.x0, TLX.x1, lin(t, T[2] + 4.4, 2.4));
  const infK = prog(t, T[2] + 6.6, 0.5);
  // c3: wake-up slice inside +312.1 ns; c4: warm, the slice is gone
  const c3 = prog(t, T[3] + 0.5, 0.5);
  const wbGrow = prog(t, T[3] + 0.6, 1.1);
  const sliceLab = win(t, T[3] + 2.2, T[4]);
  const warm = prog(t, T[4] + 0.4, 1.0);
  const statK = prog(t, T[4] + 1.6, 0.6);
  const meanK = prog(t, T[4] + 3.6, 0.6);
  const slice = 20.5 * (1 - warm);
  const sliceW = (slice / WB.max) * WB.w;
  const zz = (x: number, y: number, k: number, key: string) => (
    <Tx key={key} x={x} y={y} anchor="middle" size={26} weight={700} color={P.faint} opacity={k * (0.6 + 0.4 * Math.sin(t * 3 + x))}>
      Zz
    </Tx>
  );
  return (
    <>
      <Stage>
        <MmDefs />
        <defs>
          <clipPath id="m8Scan1">
            <rect x={TLX.x0 - 2} y={600} width={Math.max(0, scanX - TLX.x0 + 2)} height={90} />
          </clipPath>
          <clipPath id="m8Scan2">
            <rect x={TLX.x0 - 2} y={740} width={Math.max(0, scan2X - TLX.x0 + 2)} height={80} />
          </clipPath>
        </defs>
        {/* ---------------- the schematic (all cues) */}
        <g opacity={dia}>
          <Tx x={REG.x} y={REG.y + REG.h + 40} size={18} color={P.faint} spacing={2}>
            SCHEMATIC
          </Tx>
          {/* paths */}
          <line x1={CPU.x + CPU.w} y1={228} x2={IIO.x} y2={228} stroke={P.hair2} strokeWidth={1.5} />
          <line x1={OTH.x + OTH.w} y1={420} x2={IIO.x} y2={420} stroke={P.hair2} strokeWidth={1.5} />
          {[-15, -5, 5, 15].map((o) => (
            <line key={o} x1={IIO.x + IIO.w} y1={228 + o} x2={REG.x} y2={228 + o} stroke={alpha(D.link, 0.5)} strokeWidth={2} />
          ))}
          <Tx x={(IIO.x + IIO.w + REG.x) / 2} y={186} anchor="middle" size={20} color={D.link} weight={600}>
            PCIe link → card
          </Tx>
          <Box r={IIO} label="IIO₁ · root port" color={D.io} k={0.3 + 0.7 * ioWarm} />
          <Tx x={IIO.x + IIO.w / 2} y={IIO.y + 26} anchor="middle" size={18} color={P.sub} spacing={1.5}>
            SHARED
          </Tx>
          <Cell pts={CARD_TRIP} k={cardK} color={D.link} w={30} h={20} />
          <Cell pts={RP_TRIP} k={rpK} color={D.io} w={30} h={20} />
          <Box r={CPU} label="cpu40" sub="times the card read" color={D.core} k={0.4} />
          <Box r={OTH} label="another core" sub="reads the root port" color={D.core} k={0.15} />
          <Box r={REG} label="card register" sub="SDP CSR" color={D.card} k={0.2} />
        </g>
        {/* ---------------- c1: cpu40's bar shrinks */}
        <g opacity={c1}>
          <Tx x={BX} y={570} size={20} color={P.sub}>
            cpu40 → card register (p50)
          </Tx>
          <Tx x={BX - 24} y={630} anchor="end" size={24}>
            alone
          </Tx>
          <rect x={BX} y={610} width={Math.max(0, (bx(517.3) - BX) * grow1)} height={40} rx={6} fill={D.link} opacity={0.88} />
          <Tx x={bx(517.3) + 20} y={630} size={28} weight={700} color={D.link} opacity={clamp01((grow1 - 0.6) / 0.4)}>
            517.3 ns
          </Tx>
          <g opacity={copyK}>
            <Tx x={BX - 24} y={710} anchor="end" size={24}>
              + root-port reader
            </Tx>
            <rect x={bx(494.6)} y={690} width={bx(517.3) - bx(494.6)} height={40} rx={6} fill="none" stroke={alpha(D.link, 0.6)} strokeWidth={1.5} strokeDasharray="5 4" opacity={shrink} />
            <rect x={BX} y={690} width={lerp(bx(517.3), bx(494.6), shrink) - BX} height={40} rx={6} fill={D.link} opacity={0.88} />
          </g>
          <line x1={bx(494.6)} y1={600} x2={bx(494.6)} y2={742} stroke={alpha(P.push, 0.7)} strokeWidth={1.5} strokeDasharray="4 4" opacity={newLab} />
          <Tx x={bx(517.3) + 20} y={710} size={28} weight={700} color={P.push} opacity={newLab}>
            494.6 ns
          </Tx>
          <Tx x={1700} y={800} anchor="middle" size={110} weight={800} color={P.bad} opacity={qK}>
            ?
          </Tx>
        </g>
        {/* ---------------- c2: IIO activity, alone vs with the neighbour */}
        <g opacity={c2}>
          <Tx x={400} y={650} anchor="end" size={24}>
            alone
          </Tx>
          <rect x={TLX.x0} y={630} width={TLX.x1 - TLX.x0} height={40} rx={6} fill={alpha(P.faint, 0.08)} stroke={P.hair2} strokeWidth={1} />
          <g clipPath="url(#m8Scan1)">
            {CARD_X.map((x) => (
              <rect key={x} x={x} y={630} width={TLX.cw} height={40} rx={6} fill={D.link} opacity={0.9} />
            ))}
          </g>
          {CARD_X.map((x, j) => zz(x + TLX.cw + (j < 2 ? TLX.card - TLX.cw : TLX.x1 - x - TLX.cw) / 2, 651, zzK, `z${j}`))}
          <Dim x0={CARD_X[0]} x1={CARD_X[1]} y={606} label="~541 ns" color={D.link} size={22} k={prog(t, T[2] + 2.0, 0.6)} />
          <g opacity={row2}>
            <Tx x={400} y={780} anchor="end" size={24}>
              with neighbour
            </Tx>
            <rect x={TLX.x0} y={760} width={TLX.x1 - TLX.x0} height={40} rx={6} fill={alpha(D.io, 0.06)} stroke={P.hair2} strokeWidth={1} />
            <g clipPath="url(#m8Scan2)">
              {CARD_X.map((x) => (
                <rect key={x} x={x} y={760} width={TLX.cw} height={20} rx={4} fill={D.link} opacity={0.9} />
              ))}
              {ROOT_X.map((x) => (
                <rect key={x} x={x} y={780} width={TLX.rw} height={20} rx={4} fill={D.io} opacity={0.9} />
              ))}
            </g>
            <Dim x0={ROOT_X[0]} x1={ROOT_X[1]} y={736} label="~233 ns" color={D.io} size={22} k={prog(t, T[2] + 6.0, 0.6)} />
          </g>
          <g opacity={prog(t, T[2] + 1.0, 0.5)}>
            <rect x={1420} y={570} width={22} height={14} rx={3} fill={D.link} />
            <Tx x={1452} y={577} size={18} color={P.sub}>
              card read
            </Tx>
            <rect x={1560} y={570} width={22} height={14} rx={3} fill={D.io} opacity={row2} />
            <Tx x={1592} y={577} size={18} color={P.sub} opacity={row2}>
              root-port read
            </Tx>
          </g>
          <Tx x={TLX.x1} y={836} anchor="end" size={18} color={P.faint} spacing={2}>
            SCHEMATIC
          </Tx>
        </g>
        {/* ---------------- c3 / c4: card register − root port */}
        <g opacity={c3}>
          <Tx x={WB.x} y={560} size={20} color={P.sub}>
            card register − root port
          </Tx>
          <TimeBar
            x={WB.x}
            y={WB.y}
            w={WB.w}
            h={WB.h}
            max={WB.max}
            segs={[
              { v: Math.max(0.001, slice), color: D.link, reveal: 0.3 },
              { v: lerp(291.6, 290.9, warm), color: D.link },
            ]}
            grow={wbGrow}
          />
          <rect x={WB.x + 1} y={WB.y} width={Math.max(0, sliceW - 2)} height={WB.h} rx={6} fill="none" stroke={P.gold} strokeWidth={2} strokeDasharray="5 4" opacity={sliceLab} />
          <rect x={WB.x + WB.w - (WB.w * 20.5) / WB.max} y={WB.y} width={(WB.w * 20.5) / WB.max} height={WB.h} rx={6} fill="none" stroke={alpha(D.link, 0.6)} strokeWidth={1.5} strokeDasharray="5 4" opacity={warm} />
          <Dim x0={WB.x} x1={WB.x + WB.w} y={WB.y - 24} label="+312.1 ns" color={D.link} size={30} k={prog(t, T[3] + 1.2, 0.6) * (1 - warm)} />
          <g opacity={sliceLab}>
            <line x1={WB.x + sliceW / 2} y1={WB.y + WB.h + 4} x2={WB.x + sliceW / 2} y2={WB.y + WB.h + 40} stroke={P.gold} strokeWidth={1.5} />
            <Tx x={WB.x + sliceW / 2 - 12} y={WB.y + WB.h + 62} size={26} weight={700} color={P.gold}>
              18–23 ns wake-up
            </Tx>
            <Tx x={WB.x + sliceW / 2 - 12} y={WB.y + WB.h + 94} size={20} color={P.sub}>
              only the card path pays it
            </Tx>
          </g>
          <Tx x={WB.x + WB.w} y={560} anchor="end" size={20} color={P.push} opacity={warm}>
            warm: IO stack kept busy
          </Tx>
        </g>
      </Stage>
      <BigStat x={1180} y={760} w={420} value="−22.7" unit="ns" size={72} color={P.push} label="faster" opacity={c1 * bigK} />
      <BigStat x={WB.x} y={730} w={520} value="+290.9" unit="ns" size={88} color={P.gold} label="median" opacity={c3 * statK} />
      <BigStat x={1000} y={752} w={420} value="+295.0" unit="ns" size={56} color={D.link} label="mean" opacity={c3 * meanK} />
      <Tag x={TLX.x0} y={826} color={P.bad} size={16} opacity={c2 * infK}>
        [INF] idle gating in IIO / root port
      </Tag>
    </>
  );
}

export const M08: SceneDef = {
  id: "M08",
  title: "Trap: the IO stack dozes",
  stage: 3,
  source: "Interference runs: one core reads the root port, cpu40 times",
  dur: DUR,
  cues,
  C: Body,
};
