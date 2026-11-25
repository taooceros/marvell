/** Private helpers for M09–M12 (MRV-191): labelled boxes, a bar with a hatched kernel part, the verdict segments. */
import React from "react";
import { P, alpha } from "../ccnic/kit";
import { D, Seg, T as Tx } from "./mk";

/** Rounded box (SVG) with an opaque base; children are drawn on top (labels). */
export function Chip({
  x,
  y,
  w,
  h,
  color = P.sub,
  k = 1,
  dashed = false,
  hatch = false,
  fill = 0.08,
  children,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  color?: string;
  k?: number;
  dashed?: boolean;
  hatch?: boolean;
  fill?: number;
  children?: React.ReactNode;
}) {
  if (k <= 0) return null;
  return (
    <g opacity={k}>
      <rect x={x} y={y} width={w} height={h} rx={14} fill={D.node} />
      <rect x={x} y={y} width={w} height={h} rx={14} fill={alpha(color, fill)} />
      {hatch && <rect x={x} y={y} width={w} height={h} rx={14} fill="url(#ccHatch)" opacity={0.28} />}
      <rect x={x} y={y} width={w} height={h} rx={14} fill="none" stroke={alpha(color, 0.75)} strokeWidth={1.8} strokeDasharray={dashed ? "8 6" : undefined} />
      {children}
    </g>
  );
}

/** Two-line label centred in a box: main text + optional small line under it. */
export function ChipText({
  x,
  y,
  w,
  h,
  text,
  sub,
  color = P.ink,
  size = 26,
  mono = false,
  opacity = 1,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  text: React.ReactNode;
  sub?: React.ReactNode;
  color?: string;
  size?: number;
  mono?: boolean;
  opacity?: number;
}) {
  const cx = x + w / 2;
  const cy = y + h / 2;
  return (
    <>
      <Tx x={cx} y={sub ? cy - 12 : cy + 1} anchor="middle" size={size} mono={mono} color={color} weight={mono ? 500 : 600} opacity={opacity}>
        {text}
      </Tx>
      {sub && (
        <Tx x={cx} y={cy + 18} anchor="middle" size={18} color={P.sub} opacity={opacity}>
          {sub}
        </Tx>
      )}
    </>
  );
}

/**
 * Horizontal bar whose left part is software ("kernel", red hatch) and the rest hardware.
 * `collapse` 0..1 shrinks the kernel part to nothing (the hardware part slides left).
 */
export function KernelBar({
  x,
  y,
  h = 44,
  px,
  kernel,
  hw,
  color,
  grow = 1,
  collapse = 0,
  hwOp = 1,
  labelK = 1,
}: {
  x: number;
  y: number;
  h?: number;
  px: number;
  kernel: number;
  hw: number;
  color: string;
  grow?: number;
  collapse?: number;
  hwOp?: number;
  labelK?: number;
}) {
  if (grow <= 0) return null;
  const kW = Math.max(0, kernel * px * (1 - collapse));
  const hW = hw * px;
  const L = grow * (kW + hW);
  const kd = Math.min(L, kW);
  const hd = Math.max(0, L - kW);
  return (
    <g>
      {kd > 1 && (
        <g>
          <rect x={x} y={y} width={kd} height={h} rx={6} fill={alpha(P.bad, 0.1)} />
          <rect x={x} y={y} width={kd} height={h} rx={6} fill="url(#ccHatch)" opacity={0.5} />
          <rect x={x} y={y} width={kd} height={h} rx={6} fill="none" stroke={alpha(P.bad, 0.6)} strokeWidth={1.5} />
          {kd > 110 && (
            <Tx x={x + kd / 2} y={y + h / 2 + 1} anchor="middle" size={22} weight={700} color={P.bad} opacity={labelK * Math.min(1, (kd - 110) / 30)}>
              kernel
            </Tx>
          )}
        </g>
      )}
      {hd > 2 && <rect x={x + kW + (kW > 1 ? 2 : 0)} y={y} width={Math.max(0, hd - (kW > 1 ? 2 : 0))} height={h} rx={6} fill={color} opacity={0.88 * hwOp} />}
    </g>
  );
}

/** The six verdict stages (means, ns): core, core⇄CHA mesh, CHA⇄M2PCIe mesh, IRP+IIO+root port, link path, card L3. */
export const SIX: Seg[] = [
  { v: 10.7, color: D.core },
  { v: 11.8, color: D.mesh },
  { v: 57.4, color: D.mesh },
  { v: 125.2, color: D.io },
  { v: 312.1, color: D.link },
  { v: 105.2, color: D.card },
];
/** DDR5 extra (only on an L3 miss). */
export const DDR_EXTRA: Seg = { v: 101.6, color: D.ddr, dashed: true };
