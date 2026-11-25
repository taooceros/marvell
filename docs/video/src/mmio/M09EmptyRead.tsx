import React from "react";
import { Cue, SceneDef, lin, prog, useT } from "../lib";
import { Cell, Flow, P, Stage, alpha, win } from "../ccnic/kit";
import { Badge, Cross, D, Dim, MmDefs, T as Tx } from "./mk";
import { Chip, KernelBar } from "./_Mrv191M09to12Opus55";

// <cues>
const cues: Cue[] = [
  [0.60, 9.70, "Trap four: round one sized the link with sysfs config reads, **+209 ns** card minus root port. But each read also runs kernel code.", "Trap four. Round one sized the link with config reads through sysfs: two hundred nine nanoseconds. But each such read also runs kernel code."], // 0
  [10.05, 19.89, "The trick: `pread` at offset 64. Users may read only 64 bytes, so it returns nothing, after doing all the kernel's work.", "The fix is a read that does nothing. Users may read only sixty-four bytes, so a p read at offset sixty-four returns nothing, after all the kernel's work."], // 1
  [20.24, 28.62, "IO counters prove it never reaches hardware: **0**. Yet\u00a0root\u00a0port\u00a0and\u00a0VF\u00a0pay **+17.3** and **+20.9 ns** in power management.", "The I O counters show it never reaches hardware. Yet the root port and the virtual function pay about twenty nanoseconds more, in power management."], // 2
  [28.97, 35.95, "Subtract the empty read from the real one, and\u00a0a fake\u00a0**19.1 ns**\u00a0gap between the card's two functions vanishes.", "Subtract the empty read from the real one, and a fake nineteen nanosecond gap between the card's two functions disappears."], // 3
  [36.30, 45.24, "Device minus device cancels the shared locks too: across\u00a0the link, **ΔH = +221.8 ns**, about 90 ns below the register path.", "Device minus device cancels the shared locks too: two hundred twenty-two nanoseconds across the link, ninety less than the deeper register path. It fits."], // 4
];
const DUR = 46.64; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

// ---- one ns scale for c0–c3 (to scale); c4 zooms in
const X0 = 520;
const PX = 1.35;
const sx = (v: number) => X0 + v * PX;
const PX2 = 3.6;
const LBL = X0 - 24;

// ---- c1: the empty read's path
const CH = { x: 280, y: 200, w: 300, h: 84, gap: 80 };
const CHAIN = ["syscall", "kernfs", "runtime PM", "hardware read"];
const chX = (i: number) => CH.x + i * (CH.w + CH.gap);
const chC = (i: number) => chX(i) + CH.w / 2;
const CY = CH.y + CH.h / 2;
const RET_Y = 330;

// ---- c2: T(64) on seven devices (ns, median of 3 run p50s): five alike, then root port and VF0 with extra runtime-PM time
const BASE5 = [227.27, 227.27, 227.27, 228.18, 229.09];
const ROW0 = 452;
const ROWD = 40;
const ROWH = 26;
const BADGES = ["IIO 0", "IRP 0", "M2PCIe 0"];

function RowLabel({ y, h, text, sub, opacity = 1 }: { y: number; h: number; text: string; sub?: string; opacity?: number }) {
  const cy = y + h / 2;
  return (
    <>
      <Tx x={LBL} y={sub ? cy - 12 : cy + 1} anchor="end" size={26} opacity={opacity}>
        {text}
      </Tx>
      {sub && (
        <Tx x={LBL} y={cy + 16} anchor="end" size={20} color={P.sub} opacity={opacity}>
          {sub}
        </Tx>
      )}
    </>
  );
}

function Body() {
  const t = useT();
  // c0: card and root-port config reads, each with a kernel part
  const bars0 = prog(t, T[0] + 0.2, 0.5) * (1 - prog(t, T[2], 0.5));
  const hwDim = 1 - 0.6 * prog(t, T[1] + 0.2, 0.6);
  const dimK = prog(t, T[0] + 3.0, 0.8);
  const dimOp = 1 - prog(t, T[1], 0.5);
  // c1: chain; the hardware step is skipped
  const chain = win(t, T[1] + 0.1, T[3]);
  const crossK = prog(t, T[1] + 3.6, 0.4);
  const cellK = lin(t, T[1] + 1.2, 3.0);
  const retK = prog(t, T[1] + 4.0, 0.9);
  const cellPts: [number, number][] = [
    [CH.x - 60, CY],
    [chC(2), CY],
    [chC(2), RET_Y],
    [chC(0), RET_Y],
    [chC(0), CH.y + CH.h],
  ];
  // c2: seven empty reads
  const rows = win(t, T[2] + 0.6, T[3]);
  const bw = BADGES.map((b) => b.length * 20 * 0.62 + 22);
  const bTot = bw.reduce((a, w) => a + w, 0) + 12 * (BADGES.length - 1);
  let bCur = chC(3) - bTot / 2;
  const bX = bw.map((w) => {
    const x = bCur + w / 2;
    bCur += w + 12;
    return x;
  });
  // c3: H = T(0) − T(64)
  const c3 = win(t, T[3] + 0.5, T[4]);
  const col = prog(t, T[3] + 2.8, 1.0);
  const pfEnd = X0 + (228 * (1 - col) + 679.1) * PX;
  const vfEnd = X0 + (248.9 * (1 - col) + 677.3) * PX;
  const kB = prog(t, T[3] + 4.0, 0.5);
  // c4: device minus device
  const c4 = prog(t, T[4] + 0.5, 0.5);
  const regEnd = X0 + 312.1 * PX2;
  const dhEnd = X0 + 221.8 * PX2;
  return (
    <Stage>
      <MmDefs />
      {/* ---------------- c0: two config reads, to scale */}
      <g opacity={bars0}>
        <RowLabel y={470} h={44} text="card" sub="99:00.0" />
        <RowLabel y={560} h={44} text="root port" sub="98:01.0" />
        <KernelBar x={X0} y={470} px={PX} kernel={228} hw={893.6 - 228} color={D.card} grow={prog(t, T[0] + 0.4, 1.2)} hwOp={hwDim} labelK={prog(t, T[0] + 2.0, 0.5)} />
        <KernelBar x={X0} y={560} px={PX} kernel={244.5} hw={684.6 - 244.5} color={D.io} grow={prog(t, T[0] + 0.9, 1.2)} hwOp={hwDim} labelK={prog(t, T[0] + 2.0, 0.5)} />
        <g opacity={dimOp * dimK}>
          <line x1={sx(893.6)} y1={520} x2={sx(893.6)} y2={652} stroke={alpha(P.gold, 0.6)} strokeWidth={1.5} strokeDasharray="5 5" />
          <line x1={sx(684.6)} y1={610} x2={sx(684.6)} y2={652} stroke={alpha(P.gold, 0.6)} strokeWidth={1.5} strokeDasharray="5 5" />
        </g>
        <g opacity={dimOp}>
          <Dim x0={sx(684.6)} x1={sx(893.6)} y={652} label="+209 ns" dir={1} size={30} k={dimK} />
        </g>
      </g>
      {/* ---------------- c1: the read that does nothing */}
      <g opacity={chain}>
        <Tx x={CH.x} y={164} size={26} mono opacity={prog(t, T[1] + 0.2, 0.5)}>
          pread(fd, buf, 4, 64)
        </Tx>
        <Tx x={chX(3) + CH.w} y={164} anchor="end" size={18} color={P.faint} spacing={2}>
          SCHEMATIC
        </Tx>
        {cellK > 0 && cellK < 1 && <Cell pts={cellPts} k={cellK} color={P.gold} w={30} h={20} />}
        {CHAIN.slice(1).map((_, i) => (
          <Flow
            key={i}
            pts={[
              [chX(i) + CH.w + 8, CY],
              [chX(i + 1) - 8, CY],
            ]}
            k={prog(t, T[1] + 0.6 + i * 0.35, 0.35)}
            color={P.sub}
            w={2.5}
            head={10}
            dash={i === 2 ? "6 6" : undefined}
            opacity={i === 2 ? 1 - 0.6 * crossK : 1}
          />
        ))}
        {CHAIN.map((s, i) => {
          const k = prog(t, T[1] + 0.4 + i * 0.35, 0.4);
          const hw = i === 3;
          return (
            <Chip key={s} x={chX(i)} y={CH.y} w={CH.w} h={CH.h} color={hw ? D.link : P.bad} hatch={!hw} fill={hw ? 0.08 : 0.06} dashed={hw && crossK > 0.5} k={k * (hw ? 1 - 0.4 * crossK : 1)}>
              <Tx x={chC(i)} y={hw ? CY - 14 : CY + 1} anchor="middle" size={28} weight={600} color={hw && crossK > 0.5 ? P.sub : P.ink}>
                {s}
              </Tx>
            </Chip>
          );
        })}
        <Cross x={chC(3)} y={CY + 20} s={1.3} k={crossK * prog(t, T[1] + 1.6, 0.4)} />
        <Flow
          pts={[
            [chC(2), CH.y + CH.h + 4],
            [chC(2), RET_Y],
            [chC(0), RET_Y],
            [chC(0), CH.y + CH.h + 6],
          ]}
          k={retK}
          color={P.gold}
          w={2.5}
          head={11}
        />
        <Tx x={chC(1)} y={RET_Y + 28} anchor="middle" size={28} weight={700} color={P.gold} opacity={prog(t, T[1] + 4.6, 0.5)}>
          returns 0 bytes
        </Tx>
      </g>
      {/* ---------------- c2: IO counters stay at zero; seven empty reads */}
      <g opacity={win(t, T[2] + 0.2, T[3])}>
        {BADGES.map((b, i) => (
          <Badge key={b} x={bX[i]} y={RET_Y + 6} text={b} size={20} color={P.push} k={prog(t, T[2] + 0.4 + i * 0.3, 0.4)} />
        ))}
        <Tx x={chC(3)} y={RET_Y + 44} anchor="middle" size={18} color={P.sub} opacity={prog(t, T[2] + 1.4, 0.5)}>
          IO counters
        </Tx>
      </g>
      <g opacity={rows}>
        <Tx x={X0} y={420} size={24} color={P.sub}>
          T(64) · the empty read
        </Tx>
        <RowLabel y={ROW0 + 2 * ROWD} h={ROWH} text="five devices" />
        {BASE5.map((v, i) => (
          <rect key={i} x={X0} y={ROW0 + i * ROWD} width={Math.max(0, (sx(v) - X0) * prog(t, T[2] + 0.8 + i * 0.12, 0.8))} height={ROWH} rx={5} fill={alpha(P.sub, 0.55)} />
        ))}
        <g opacity={prog(t, T[2] + 1.8, 0.5)}>
          <path d={`M${sx(229.1) + 14} ${ROW0} h10 V${ROW0 + 4 * ROWD + ROWH} h-10`} fill="none" stroke={P.sub} strokeWidth={1.5} />
          <Tx x={sx(229.1) + 36} y={ROW0 + 2 * ROWD + ROWH / 2} size={28} weight={700}>
            227.3–229.1 ns
          </Tx>
        </g>
        {[
          { label: "root port", base: 227.2, ext: 17.3, txt: "+17.3 ns" },
          { label: "VF0", base: 227.3, ext: 20.9, txt: "+20.9 ns" },
        ].map((r, j) => {
          const y = ROW0 + (5 + j) * ROWD;
          const g0 = prog(t, T[2] + 1.4 + j * 0.12, 0.8);
          const ge = prog(t, T[2] + 2.8 + j * 0.4, 0.6);
          return (
            <g key={r.label}>
              <RowLabel y={y} h={ROWH} text={r.label} opacity={g0} />
              <rect x={X0} y={y} width={Math.max(0, (sx(r.base) - X0) * g0)} height={ROWH} rx={5} fill={alpha(P.sub, 0.55)} />
              <rect x={sx(r.base) + 2} y={y} width={Math.max(0, (r.ext * PX - 2) * ge)} height={ROWH} rx={4} fill={P.bad} opacity={0.9} />
              <Tx x={sx(r.base + r.ext) + 16} y={y + ROWH / 2 + 1} size={26} weight={700} color={P.bad} opacity={prog(t, T[2] + 3.2 + j * 0.4, 0.4)}>
                {r.txt}
              </Tx>
            </g>
          );
        })}
        <g opacity={prog(t, T[2] + 4.2, 0.5)}>
          <path d={`M${sx(248.2) + 136} ${ROW0 + 5 * ROWD} h10 V${ROW0 + 6 * ROWD + ROWH} h-10`} fill="none" stroke={P.bad} strokeWidth={1.5} />
          <Tx x={sx(248.2) + 160} y={ROW0 + 5.5 * ROWD + ROWH / 2} size={26} weight={600} color={P.bad}>
            runtime PM
          </Tx>
        </g>
      </g>
      {/* ---------------- c3: subtract the empty read */}
      <g opacity={c3}>
        <Tx x={X0} y={236} size={34} mono weight={600} color={P.gold}>
          H = T(0) − T(64)
        </Tx>
        <RowLabel y={380} h={44} text="PF" sub="physical function" />
        <RowLabel y={470} h={44} text="VF" sub="virtual function" />
        <KernelBar x={X0} y={380} px={PX} kernel={228} hw={679.1} color={D.card} grow={prog(t, T[3] + 0.6, 1.0)} collapse={col} />
        <KernelBar x={X0} y={470} px={PX} kernel={248.9} hw={677.3} color={D.card} grow={prog(t, T[3] + 0.85, 1.0)} collapse={col} />
        <g opacity={prog(t, T[3] + 1.9, 0.5)}>
          <line x1={pfEnd} y1={372} x2={pfEnd} y2={548} stroke={alpha(P.sub, 0.7)} strokeWidth={1.5} strokeDasharray="5 5" />
          <line x1={vfEnd} y1={462} x2={vfEnd} y2={548} stroke={alpha(P.sub, 0.7)} strokeWidth={1.5} strokeDasharray="5 5" />
          <Tx x={Math.min(pfEnd, vfEnd) - 16 - 124 * kB} y={574} anchor="end" size={28} weight={700} color={P.bad}>
            19.1 ns gap
          </Tx>
          <Tx x={Math.min(pfEnd, vfEnd) - 16} y={574} anchor="end" size={28} weight={700} color={P.push} opacity={kB}>
            → 1.8 ns
          </Tx>
        </g>
        <Tx x={pfEnd + 18} y={403} size={28} weight={700} color={D.card} opacity={prog(t, T[3] + 3.8, 0.5)}>
          679.1 ns
        </Tx>
        <Tx x={vfEnd + 18} y={493} size={28} weight={700} color={D.card} opacity={prog(t, T[3] + 3.8, 0.5)}>
          677.3 ns
        </Tx>
      </g>
      {/* ---------------- c4: device minus device */}
      <g opacity={c4}>
        <Tx x={X0} y={236} size={34} mono weight={600} color={P.gold}>
          ΔH = H(99:00.0) − H(98:01.0)
        </Tx>
        <RowLabel y={380} h={44} text="register path" sub="MMIO read" />
        <RowLabel y={470} h={44} text="across the link" sub="config read" />
        <rect x={X0} y={380} width={Math.max(0, (regEnd - X0) * prog(t, T[4] + 0.6, 1.0))} height={44} rx={6} fill={D.link} opacity={0.88} />
        <rect x={X0} y={470} width={Math.max(0, (dhEnd - X0) * prog(t, T[4] + 1.0, 1.0))} height={44} rx={6} fill={D.link} opacity={0.6} />
        <Tx x={(X0 + regEnd) / 2} y={403} anchor="middle" size={26} weight={800} color={P.bg0} opacity={prog(t, T[4] + 1.5, 0.4)}>
          +312.1 ns
        </Tx>
        <Tx x={(X0 + dhEnd) / 2} y={493} anchor="middle" size={26} weight={800} color={P.bg0} opacity={prog(t, T[4] + 1.9, 0.4)}>
          ΔH = +221.8 ns
        </Tx>
        <g opacity={prog(t, T[4] + 2.6, 0.5)}>
          <line x1={regEnd} y1={430} x2={regEnd} y2={600} stroke={alpha(P.gold, 0.6)} strokeWidth={1.5} strokeDasharray="5 5" />
          <line x1={dhEnd} y1={520} x2={dhEnd} y2={600} stroke={alpha(P.gold, 0.6)} strokeWidth={1.5} strokeDasharray="5 5" />
        </g>
        <Dim x0={dhEnd} x1={regEnd} y={600} label="~90 ns" dir={1} size={30} k={prog(t, T[4] + 2.8, 0.8)} />
        <Tx x={(dhEnd + regEnd) / 2} y={680} anchor="middle" size={20} color={P.sub} opacity={prog(t, T[4] + 3.8, 0.5)}>
          PEM0 → SDP · depth · wake-up [INF]
        </Tx>
      </g>
    </Stage>
  );
}

export const M09: SceneDef = {
  id: "M09",
  title: "Trap: the kernel in the ruler",
  stage: 3,
  source: "sysfs config reads at offset 0 vs 64 \u00b7 7 devices \u00b7 IO counters",
  dur: DUR,
  cues,
  C: Body,
};
