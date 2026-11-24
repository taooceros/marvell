/**
 * Private helpers for C02–C04 ("the debate"): HostNic-styled SVG captions that can crossfade (relabels),
 * polyline timing, and a width estimate for the kit's Tag pill (right / centre alignment).
 */
import React from "react";
import { FONT, Pt } from "../lib";
import { HnGeom, P, Rect, alpha, cx, cy } from "./kit";

/** Maps default-layout (s = 1) points through a geometry. */
export const mapPts = (g: HnGeom, pts: [number, number][]): Pt[] => pts.map(([x, y]) => g.at(x, y));

/** Fraction along `pts` where the polyline first passes x on a horizontal segment (0.5 if it never does). */
export function fracAtX(pts: Pt[], x: number): number {
  let total = 0;
  for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  if (total <= 0) return 0.5;
  let acc = 0;
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1];
    const [x1, y1] = pts[i];
    if (Math.abs(y0 - y1) < 0.01 && x0 !== x1 && (x - x0) * (x - x1) <= 0) return (acc + Math.abs(x - x0)) / total;
    acc += Math.hypot(x1 - x0, y1 - y0);
  }
  return 0.5;
}

/** Approximate rendered width (px) of a kit `Tag` with this text (uppercase, letter-spaced, padded pill). */
export function tagW(text: string, size = 15): number {
  let em = 0;
  for (const ch of text.toUpperCase()) {
    if (" .,:;'’|".includes(ch)) em += 0.3;
    else if ("MW".includes(ch)) em += 0.9;
    else if ("I1".includes(ch)) em += 0.36;
    else em += 0.68;
  }
  return em * size + text.length * 1.6 + 30;
}

/** Part caption in HostNic's style (centred in the part rect); draw it yourself when the label must crossfade. */
export function PartText({ r, s, children, opacity = 1, bold = false }: { r: Rect; s: number; children: React.ReactNode; opacity?: number; bold?: boolean }) {
  if (opacity <= 0) return null;
  return (
    <text
      x={cx(r)}
      y={cy(r) + 1}
      textAnchor="middle"
      dominantBaseline="central"
      fontSize={Math.max(10, 24 * s)}
      fontWeight={bold ? 600 : 400}
      fill={P.ink}
      opacity={opacity}
      fontFamily={FONT}
    >
      {children}
    </text>
  );
}

/** Box title in HostNic's style (top-left of the host / NIC box). */
export function BoxTitle({ r, s, color, children, opacity = 1 }: { r: Rect; s: number; color: string; children: React.ReactNode; opacity?: number }) {
  if (opacity <= 0) return null;
  return (
    <text x={r.x + 22 * s} y={r.y + 32 * s} fontSize={Math.max(10, 17 * s)} letterSpacing={2.4} fontWeight={600} fill={alpha(color, 0.9)} opacity={opacity} fontFamily={FONT}>
      {children}
    </text>
  );
}

/** Link caption in HostNic's style (centred above the link). */
export function LinkText({ g, color, children, opacity = 1 }: { g: HnGeom; color: string; children: React.ReactNode; opacity?: number }) {
  if (opacity <= 0) return null;
  return (
    <text
      x={(g.linkX0 + g.linkX1) / 2}
      y={g.mid - 48 * g.s}
      textAnchor="middle"
      fontSize={Math.max(10, 17 * g.s)}
      letterSpacing={2}
      fontWeight={600}
      fill={color}
      opacity={opacity}
      fontFamily={FONT}
    >
      {children}
    </text>
  );
}
