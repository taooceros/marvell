import React from "react";
import { C, Cue, K, Pt, SceneDef, Stage, fade, lerp, partial, pathD, polyLen, pop, prog, useRealT, useT } from "../lib";
import { Datapath, dpGeom } from "./pcie";
import { Box, Check, Cross, Tx } from "./_SceneWrap";

// <cues>
const cues: Cue[] = [
  [0.60, 7.10, "Every reply is matched to an **outstanding request**: its sequence number and its exact send timestamp.", "Every reply is matched to an outstanding request: its sequence number, and its exact send timestamp."], // 0
  [7.45, 15.91, "It must also carry the **trailing seq copy** and the right **payload pattern**, or it counts as **torn** or **bad**.", "It must also carry the trailing sequence copy and the right payload pattern. Otherwise it counts as torn, or bad, and the run fails."], // 1
  [16.26, 24.76, "Every run in this video: lost **0**, bad **0**, torn **0**, and the card's count equals the host's exactly.", "Across every run in this video: zero lost, zero bad, zero torn, and the card's count equals the host's exactly."], // 2
  [25.11, 33.39, "Do the checks fire? Corrupt every 1000th payload on purpose: the card flags **100**, the host flags **100**.", "Do the checks actually fire? We corrupted every thousandth payload on purpose. The card flagged a hundred, and the host flagged the same hundred."], // 3
  [33.74, 40.44, "And the link stays clean: host **AER** error counters read **0** for the card and its root port.", "And the link stays clean: the host's error reporting counters read zero, for both the card and its root port."], // 4
];
const DUR = 41.84; // <dur>
// </cues>

// ---------------------------------------------------------------- layout
const G = dpGeom(0.62, 365, 47); // host 402–799 · link 799–1121 · card 1121–1518 · y 140–574
const LINK_Y = G.pt([0, 480])[1];
const HIDE = { core: 0, hmem: 0, sdpRing: 0, replyRing: 0, sdp: 0, dpi: 0, cores: 0, cmem: 0, reqRing: 0, ctrl: 0, pem: 0 };

// outstanding-request table (host side, left)
const TB = { x: 60, y: 140, w: 780, h: 440 };
const ROWS = ["n", "n+1", "n+2", "n+3", "n+4"];
const MATCH = 1;
const rowY = (i: number) => 245 + i * 64;
const ROW_H = 54;

// reply cell anatomy (right): 64 B = seq 8 · send TSC 8 · tag 8 · pattern 32 · seq copy 8
const FIELDS: { name: string; bytes: number }[] = [
  { name: "seq", bytes: 8 },
  { name: "send TSC", bytes: 8 },
  { name: "tag", bytes: 8 },
  { name: "payload pattern · 32 B", bytes: 32 },
  { name: "seq copy", bytes: 8 },
];
const fieldX = (x: number, w: number, i: number) => x + (w * FIELDS.slice(0, i).reduce((s, f) => s + f.bytes, 0)) / 64;
const fieldW = (w: number, i: number) => (w * FIELDS[i].bytes) / 64;

type CellOpts = {
  vals?: (string | null)[];
  hl?: (number | null)[]; // per-field highlight colour weight 0..1 (K.ok)
  bad?: { field: number; color: string; k: number }[];
  stale?: number; // 0..1: second half of the pattern greyed (torn)
  badStripe?: number; // 0..1: one red stripe in the pattern
};

function ReplyCell({ x, y, w, h, o, opts }: { x: number; y: number; w: number; h: number; o: number; opts: CellOpts }) {
  if (o <= 0.001) return null;
  const pi = 3;
  const px = fieldX(x, w, pi);
  const pw = fieldW(w, pi);
  const sw = pw / 8;
  return (
    <g opacity={o}>
      <rect x={x} y={y} width={w} height={h} rx={10} fill={`${K.rep}14`} stroke={K.rep} strokeWidth={2.5} />
      {/* pattern stripes (4 B each) */}
      {Array.from({ length: 8 }, (_, j) => {
        const stale = (opts.stale ?? 0) * (j >= 4 ? 1 : 0);
        const red = (opts.badStripe ?? 0) * (j === 5 ? 1 : 0);
        const base = j % 2 ? `${K.rep}55` : `${K.rep}2a`;
        return (
          <g key={j}>
            <rect x={px + j * sw + 3} y={y + 8} width={sw - 6} height={h - 16} rx={4} fill={base} />
            {stale > 0 && <rect x={px + j * sw + 3} y={y + 8} width={sw - 6} height={h - 16} rx={4} fill="#3b4258" opacity={stale} />}
            {red > 0 && <rect x={px + j * sw + 3} y={y + 8} width={sw - 6} height={h - 16} rx={4} fill={K.bad} opacity={red} />}
          </g>
        );
      })}
      {FIELDS.map((_, i) => {
        const fx = fieldX(x, w, i);
        const fw = fieldW(w, i);
        const hl = opts.hl?.[i] ?? 0;
        const bad = opts.bad?.find((b) => b.field === i);
        const v = opts.vals?.[i];
        return (
          <g key={i}>
            {i > 0 && <line x1={fx} y1={y} x2={fx} y2={y + h} stroke={K.rep} strokeWidth={2} opacity={0.7} />}
            {hl > 0 && <rect x={fx + 2} y={y + 2} width={fw - 4} height={h - 4} rx={8} fill={`${K.ok}22`} stroke={K.ok} strokeWidth={3.5} opacity={hl} filter="url(#glow)" />}
            {bad && bad.k > 0 && <rect x={fx + 2} y={y + 2} width={fw - 4} height={h - 4} rx={8} fill={`${bad.color}40`} stroke={bad.color} strokeWidth={3.5} opacity={bad.k} />}
            {v && (
              <Tx x={fx + fw / 2} y={y + h / 2} size={22} mono anchor="middle" weight={700} color={bad && bad.k > 0.5 ? bad.color : C.text}>
                {v}
              </Tx>
            )}
          </g>
        );
      })}
    </g>
  );
}

/** Red status pill (SVG). */
function BadPill({ x, y, w, label, k }: { x: number; y: number; w: number; label: string; k: number }) {
  if (k <= 0.001) return null;
  return (
    <g transform={`translate(${x + w / 2} ${y}) scale(${k})`}>
      <rect x={-w / 2} y={-22} width={w} height={44} rx={22} fill={`${K.bad}33`} stroke={K.bad} strokeWidth={2.5} />
      <Tx x={0} y={0} size={26} weight={800} anchor="middle" color={K.bad}>
        {label}
      </Tx>
    </g>
  );
}

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  // ---------------------------------------------------- region A1 (c0–c2): table + reply-cell checks
  const a1 = 1 - prog(t, T[3], 0.35);
  const rightDim = 1 - 0.6 * prog(t, T[2], 0.6);
  // c0–c1 use the full band; region A compresses upward when the run counters arrive (c2)
  const st = lerp(1.42, 1, prog(t, T[2], 0.8));
  /** Stretched y (region A1 only). */
  const Y = (y: number) => 140 + (y - 140) * st;
  const rh = lerp(64, ROW_H, prog(t, T[2], 0.8));
  const tableIn = prog(t, 0.15, 0.6);
  const cellIn = prog(t, T[0] + 0.4, 1.4);
  const cellX = lerp(1100, 900, cellIn);
  const conn = prog(t, T[0] + 2.5, 0.7);
  const connPts: Pt[] = [
    [900, Y(225) + 35],
    [868, Y(225) + 35],
    [868, Y(rowY(MATCH)) + rh / 2],
    [822, Y(rowY(MATCH)) + rh / 2],
  ];

  // c1 timings
  const chk1 = T[1] + 0.4;
  const chk2 = T[1] + 2.6;
  const tornT = T[1] + 4.4;
  const badT = T[1] + 5.5;
  const failT = T[1] + 6.8;

  // ---------------------------------------------------- region B (c2–): run counters
  // normal-run strip: in for c2, fully out during the fault run (c3), back for c4
  const stripOp = prog(t, T[2], 0.6) * (1 - prog(t, T[3], 0.35)) + prog(t, T[4] + 0.3, 0.4);
  const boxes = [
    { x: 60, label: "lost", at: T[2] + 1.6 },
    { x: 510, label: "bad", at: T[2] + 2.4 },
    { x: 960, label: "torn", at: T[2] + 3.2 },
  ];
  const eqBox = { x: 1410, w: 450, at: T[2] + 4.2 };
  const barK = prog(t, T[2] + 4.6, 2.0);
  const eqK = pop(t, T[2] + 6.7, 0.5);

  // ---------------------------------------------------- region A2 (c3–c4): fault injection, then AER
  const a2 = prog(t, T[3] + 0.3, 0.4);
  const fault = a2 * (1 - prog(t, T[4], 0.35)); // c3-only labels
  const aer = prog(t, T[4] + 0.3, 0.4);
  // c3 fault stream card → host: groups of unlabelled cells with the middle one corrupted ("1000th"), then "…" for
  // the cells not drawn. It crossfades out at T4; c4 starts a fresh, all-clean stream.
  const SPEED = 320;
  const GAP = 84;
  const travel = Math.max(0, rt - T[3]) * SPEED;
  const travelClean = Math.max(0, rt - T[4]) * SPEED;
  const streamPts: Pt[] = [
    [1480, LINK_Y],
    [440, LINK_Y],
  ];
  const L = polyLen(streamPts);
  // fault-stream item idx: 5 cells then one "…" gap marker per group; cell 2 of each group is the corrupted one
  const isRed = (idx: number) => idx % 6 === 2;
  // most recent red cell that left the card / reached the host (for flashes)
  const flash = (dist: number) => {
    const i = Math.floor((travel - dist) / GAP);
    const r = i - ((((i - 2) % 6) + 6) % 6);
    if (r < 0) return 0;
    const d = travel - r * GAP - dist;
    return Math.max(0, 1 - d / (GAP * 3));
  };
  const hostFlash = flash(L) * fault;
  const cardFlash = flash(0) * fault;
  const streamCells = (tr: number, faulty: boolean) => {
    const items: React.ReactNode[] = [];
    for (let idx = Math.floor(tr / GAP); idx >= 0; idx--) {
      const d = tr - idx * GAP;
      if (d > L) break;
      const x = streamPts[0][0] - d;
      const o = Math.min(1, d / 40, (L - d) / 40);
      if (faulty && idx % 6 === 5) {
        items.push(
          <g key={idx} opacity={o}>
            {[-14, 0, 14].map((dx) => (
              <circle key={dx} cx={x + dx} cy={LINK_Y} r={4.5} fill={C.muted} />
            ))}
          </g>,
        );
        continue;
      }
      const red = faulty && isRed(idx);
      const col = red ? K.bad : K.rep;
      items.push(
        <g key={idx} opacity={o}>
          <rect x={x - 22} y={LINK_Y - 20} width={44} height={40} rx={8} fill={`${col}${red ? "88" : "2a"}`} stroke={col} strokeWidth={2.5} />
          {red && (
            <Tx x={x} y={LINK_Y + 46} size={22} mono weight={700} anchor="middle" color={K.bad}>
              1000th
            </Tx>
          )}
        </g>,
      );
    }
    return items;
  };
  const flagged = Math.round(100 * prog(t, T[3] + 1.2, 5.6));
  const cnt = String(flagged);
  const eq100 = fade(t, T[3] + 7.0, T[4]);
  const linkOk = prog(t, T[4] + 1.0, 0.8);

  const counterPanel = (x: number, side: "host" | "card") => {
    const col = side === "host" ? K.host : K.card;
    const cx = x + 160;
    return (
      <g key={side}>
        <Box x={x} y={150} w={320} h={385} stroke={col} opacity={a2} />
        {/* c3: fault counter */}
        <g opacity={fault}>
          <Tx x={cx} y={195} size={30} weight={800} anchor="middle" color={col}>
            {side === "host" ? "Host" : "Card"}
          </Tx>
          <Tx x={cx} y={238} size={26} anchor="middle" color={C.muted}>
            {side === "host" ? "flagged bad" : "corrupted"}
          </Tx>
          <Tx x={cx} y={350} size={110} weight={800} anchor="middle" color={K.bad}>
            {cnt}
          </Tx>
          <Tx x={cx} y={450} size={24} anchor="middle" color={C.muted}>
            {side === "host" ? "trailing copy · pattern" : "every 1000th payload"}
          </Tx>
        </g>
        {/* c4: AER counters */}
        <g opacity={aer}>
          <Tx x={cx} y={195} size={30} weight={800} anchor="middle" color={col}>
            {side === "host" ? "Root port AER" : "Card PF AER"}
          </Tx>
          <Tx x={cx} y={236} size={22} mono anchor="middle" color={C.muted}>
            {side === "host" ? "0000:98:01.0" : "0000:99:00.0"}
          </Tx>
          <line x1={x + 24} y1={268} x2={x + 296} y2={268} stroke={C.line} strokeWidth={2} />
          {["correctable", "non-fatal", "fatal"].map((lab, j) => {
            const y = 318 + j * 76;
            const at = T[4] + 0.5 + 0.35 * j;
            const k = prog(t, at, 0.5);
            return (
              <g key={lab}>
                <Tx x={x + 24} y={y} size={26} color={C.text} opacity={k}>
                  {lab}
                </Tx>
                <Tx x={x + 250} y={y} size={48} weight={800} anchor="end" color={K.ok} opacity={k}>
                  0
                </Tx>
                <Check x={x + 284} y={y} k={pop(t, at + 0.2, 0.5)} color={K.ok} r={15} />
              </g>
            );
          })}
        </g>
      </g>
    );
  };

  return (
    <Stage>
      {/* ================= region A1 ================= */}
      <g opacity={a1}>
        {/* outstanding-request table */}
        <g opacity={tableIn}>
          <Box x={TB.x} y={TB.y} w={TB.w} h={Y(TB.y + TB.h) - TB.y} stroke={K.host} />
          <Tx x={84} y={Y(176)} size={28} weight={800} color={K.host}>
            Host · outstanding requests
          </Tx>
          <Tx x={100} y={Y(216)} size={24} color={C.muted}>
            seq
          </Tx>
          <Tx x={240} y={Y(216)} size={24} color={C.muted}>
            send TSC
          </Tx>
          <Tx x={470} y={Y(216)} size={24} color={C.muted}>
            status
          </Tx>
          {ROWS.map((s, i) => {
            const y = Y(rowY(i));
            const rin = prog(t, 0.3 + 0.15 * i, 0.5);
            const at = i === MATCH ? T[0] + 3.2 : T[2] + 0.4 + 0.3 * (i < MATCH ? i : i - 1);
            const m = prog(t, at, 0.5);
            return (
              <g key={s} opacity={rin}>
                <rect x={80} y={y} width={740} height={rh} rx={10} fill={m > 0 ? `${K.ok}1c` : "rgba(139,149,173,0.06)"} stroke={m > 0 ? K.ok : C.line} strokeWidth={m > 0 ? 2.5 : 1.5} strokeOpacity={m > 0 ? m : 1} />
                <Tx x={100} y={y + rh / 2} size={26} mono weight={700}>
                  {s}
                </Tx>
                <Tx x={240} y={y + rh / 2} size={26} mono color={K.timer}>
                  {`tsc(${s})`}
                </Tx>
                <Tx x={470} y={y + rh / 2} size={24} italic color={C.muted} opacity={1 - m}>
                  in flight…
                </Tx>
                <Check x={490} y={y + rh / 2} k={pop(t, at, 0.5)} color={K.ok} r={15} />
                <Tx x={518} y={y + rh / 2} size={i === MATCH ? 22 : 24} mono={i === MATCH} weight={700} color={K.ok} opacity={m}>
                  {i === MATCH ? "RTT = now − tsc(n+1)" : "matched"}
                </Tx>
              </g>
            );
          })}
        </g>

        {/* connector reply → matching row */}
        {conn > 0 && <path d={pathD(partial(connPts, conn))} fill="none" stroke={K.ok} strokeWidth={3} opacity={rightDim} />}

        {/* reply cell anatomy */}
        <g opacity={rightDim}>
          <g opacity={cellIn}>
            <Tx x={cellX} y={Y(160)} size={28} weight={800} color={K.rep}>
              Reply cell · 64 B
            </Tx>
            <Tx x={1860} y={Y(160)} size={24} anchor="end" color={C.muted}>
              from the card
            </Tx>
            {FIELDS.map((f, i) => (
              <Tx key={f.name} x={fieldX(cellX, 960, i) + fieldW(960, i) / 2} y={Y(202)} size={24} anchor="middle" color={C.muted}>
                {f.name}
              </Tx>
            ))}
          </g>
          <ReplyCell
            x={cellX}
            y={Y(225)}
            w={960}
            h={70}
            o={cellIn}
            opts={{
              vals: ["n+1", "tsc(n+1)", "tag", "f(seq)", "n+1"],
              hl: [conn * (1 - prog(t, T[1], 0.5)) + prog(t, chk1, 0.5) * (1 - prog(t, chk2, 0.5)), conn * (1 - prog(t, T[1], 0.5)), 0, prog(t, chk2, 0.5) * (1 - prog(t, tornT, 0.5)), prog(t, chk1, 0.5) * (1 - prog(t, chk2, 0.5))],
            }}
          />
          {/* checks */}
          <g opacity={prog(t, chk1, 0.5)}>
            <Check x={918} y={Y(335)} k={pop(t, chk1 + 0.6, 0.5)} color={K.ok} />
            <Tx x={948} y={Y(335)} size={26}>
              trailing <tspan fill={K.ok} fontWeight={700}>seq copy</tspan> = seq
            </Tx>
          </g>
          <g opacity={prog(t, chk2, 0.5)}>
            <Check x={1338} y={Y(335)} k={pop(t, chk2 + 0.6, 0.5)} color={K.ok} />
            <Tx x={1368} y={Y(335)} size={26}>
              <tspan fill={K.ok} fontWeight={700}>payload pattern</tspan> = f(seq)
            </Tx>
          </g>
          {/* failing variants */}
          <g opacity={prog(t, tornT, 0.5)}>
            <ReplyCell
              x={900}
              y={Y(398)}
              w={600}
              h={52}
              o={1}
              opts={{
                vals: ["n+3", null, null, null, "old"],
                stale: prog(t, tornT + 0.4, 0.6),
                bad: [{ field: 4, color: K.bad, k: prog(t, tornT + 0.4, 0.6) }],
              }}
            />
            <BadPill x={1525} y={Y(398) + 26} w={110} label="torn" k={pop(t, tornT + 0.8, 0.5)} />
          </g>
          <g opacity={prog(t, badT, 0.5)}>
            <ReplyCell
              x={900}
              y={Y(478)}
              w={600}
              h={52}
              o={1}
              opts={{
                vals: ["n+4", null, null, null, "n+4"],
                badStripe: prog(t, badT + 0.3, 0.5),
              }}
            />
            <BadPill x={1525} y={Y(478) + 26} w={110} label="bad" k={pop(t, badT + 0.7, 0.5)} />
          </g>
          <g opacity={prog(t, failT, 0.5)}>
            <path d={`M1640 ${Y(398) + 26} L1662 ${Y(398) + 26} L1662 ${Y(478) + 26} L1640 ${Y(478) + 26} M1662 ${Y(438) + 26} L1676 ${Y(438) + 26}`} fill="none" stroke={K.bad} strokeWidth={3} />
            <Box x={1676} y={Y(438) + 26 - 50} w={184} h={100} stroke={K.bad} fill={`${K.bad}22`} sw={3} glow={prog(t, failT, 0.5) > 0.5} />
            <Cross x={1768} y={Y(438) + 6} k={pop(t, failT + 0.2, 0.5)} color={K.bad} r={18} />
            <Tx x={1768} y={Y(438) + 50} size={30} weight={800} anchor="middle" color={K.bad}>
              run fails
            </Tx>
          </g>
          <Tx x={900} y={Y(562)} size={24} italic color={C.muted} opacity={prog(t, tornT, 0.5)}>
            torn = tail from an older write · bad = pattern mismatch
          </Tx>
        </g>
      </g>

      {/* ================= region A2: same host ⇄ card drawing ================= */}
      {a2 > 0.001 && (
        <g>
          <Datapath g={G} show={HIDE} labels={{ host: ["", undefined], card: ["", undefined], link: ["", undefined] }} opacity={a2} linkGlow={0.5} />
          <g opacity={a2}>
            <Tx x={G.r.host.x + 20} y={180} size={28} weight={800} color={K.host}>
              HOST · saturn
            </Tx>
            <Tx x={G.r.card.x + 20} y={180} size={28} weight={800} color={K.card}>
              CARD · CN106xx
            </Tx>
            <Tx x={G.r.link.x + G.r.link.w / 2} y={G.r.link.y - 26} size={26} weight={700} anchor="middle" color={K.link}>
              PCIe Gen5 x4
            </Tx>
          </g>
          <Tx x={G.r.card.x + 20} y={222} size={22} mono color={K.bad} opacity={fault}>
            --corrupt-every 1000
          </Tx>
          <Tx x={G.r.host.x + 20} y={222} size={24} color={C.muted} opacity={fault}>
            checks every reply
          </Tx>
          {/* green "clean link" glow */}
          <rect x={G.r.link.x} y={G.r.link.y} width={G.r.link.w} height={G.r.link.h} fill={`${K.ok}22`} stroke={K.ok} strokeWidth={3} opacity={linkOk * a2} filter="url(#glow)" />
          <g opacity={fault}>{streamCells(travel, true)}</g>
          <g opacity={aer}>{streamCells(travelClean, false)}</g>
          {/* corruption at the card, detection at the host */}
          <g opacity={cardFlash}>
            <circle cx={1480} cy={LINK_Y} r={16 + 30 * (1 - cardFlash)} fill="none" stroke={K.bad} strokeWidth={3} />
            <Cross x={1480} y={LINK_Y - 52} color={K.bad} r={18} />
          </g>
          <g opacity={hostFlash}>
            <circle cx={440} cy={LINK_Y} r={16 + 30 * (1 - hostFlash)} fill="none" stroke={K.bad} strokeWidth={3} />
            <Cross x={440} y={LINK_Y - 52} color={K.bad} r={18} />
          </g>
          {/* card 100 = host 100 */}
          <g opacity={eq100}>
            <Check x={882} y={448} k={pop(t, T[3] + 7.2, 0.5)} color={K.ok} r={18} />
            <Tx x={910} y={448} size={32} weight={800} color={K.ok}>
              100 = 100
            </Tx>
          </g>
          <Tx x={960} y={520} size={22} italic anchor="middle" color={C.muted} opacity={fault}>
            “…” = cells not drawn
          </Tx>
          {/* c4: root port and card PF on the link ends */}
          <g opacity={aer}>
            <path d={`M${692} ${LINK_Y + 35} L692 470 L380 470`} fill="none" stroke={K.host} strokeWidth={2.5} strokeDasharray="8 6" />
            <path d={`M${1227} ${LINK_Y + 35} L1227 470 L1540 470`} fill="none" stroke={K.card} strokeWidth={2.5} strokeDasharray="8 6" />
            <Box x={588} y={LINK_Y - 35} w={209} h={70} stroke={K.host} fill={C.panel} sw={3} rx={12} />
            <Tx x={692} y={LINK_Y} size={26} weight={800} anchor="middle" color={K.host}>
              Root port
            </Tx>
            <Box x={1123} y={LINK_Y - 35} w={209} h={70} stroke={K.card} fill={C.panel} sw={3} rx={12} />
            <Tx x={1227} y={LINK_Y} size={26} weight={800} anchor="middle" color={K.card}>
              Card PF
            </Tx>
            <g opacity={linkOk}>
              <Check x={850} y={438} k={pop(t, T[4] + 1.2, 0.5)} color={K.ok} r={17} />
              <Tx x={878} y={438} size={30} weight={800} color={K.ok}>
                link clean
              </Tx>
            </g>
          </g>
          {counterPanel(60, "host")}
          {counterPanel(1540, "card")}
        </g>
      )}

      {/* ================= region B during c3: fault-run tally ================= */}
      <g opacity={fault}>
        <Tx x={60} y={612} size={26} weight={700} color={C.muted}>
          Fault-injection run · each mark = one corrupted payload
        </Tx>
        <Box x={60} y={640} w={1800} h={230} stroke={C.line} />
        {(
          [
            ["card", "corrupted", K.card, 705],
            ["host", "flagged bad", K.host, 805],
          ] as const
        ).map(([side, what, col, y]) => (
          <g key={side}>
            <Tx x={88} y={y - 14} size={28} weight={800} color={col}>
              {side}
            </Tx>
            <Tx x={88} y={y + 20} size={24} color={C.muted}>
              {what}
            </Tx>
            {Array.from({ length: 100 }, (_, i) => {
              const on = i < flagged;
              return <rect key={i} x={300 + i * 14.2} y={y - 24} width={9} height={48} rx={3} fill={on ? K.bad : "transparent"} stroke={on ? K.bad : C.dim} strokeWidth={1.5} />;
            })}
            <Tx x={1838} y={y} size={44} weight={800} anchor="end" color={K.bad}>
              {cnt}
            </Tx>
          </g>
        ))}
      </g>

      {/* ================= region B: every run ================= */}
      <g opacity={stripOp}>
        <Tx x={60} y={612} size={26} weight={700} color={C.muted}>
          Every run in this video
        </Tx>
        {boxes.map((b) => {
          const k = prog(t, b.at, 0.5);
          return (
            <g key={b.label} opacity={k} transform={`translate(0 ${(1 - k) * 20})`}>
              <Box x={b.x} y={640} w={420} h={230} stroke={k > 0.5 ? K.ok : C.line} />
              <Tx x={b.x + 210} y={735} size={120} weight={800} anchor="middle" color={K.ok}>
                0
              </Tx>
              <Tx x={b.x + 210} y={828} size={32} weight={700} anchor="middle" color={C.text}>
                {b.label}
              </Tx>
              <Check x={b.x + 380} y={680} k={pop(t, b.at + 0.3, 0.5)} color={K.ok} r={18} />
            </g>
          );
        })}
        <g opacity={prog(t, eqBox.at, 0.5)}>
          <Box x={eqBox.x} y={640} w={eqBox.w} h={230} stroke={eqK > 0.5 ? K.ok : C.line} />
          {(
            [
              ["card", K.card, 700],
              ["host", K.host, 760],
            ] as const
          ).map(([lab, col, y]) => (
            <g key={lab}>
              <Tx x={1436} y={y} size={26} weight={700} color={col}>
                {lab}
              </Tx>
              <rect x={1520} y={y - 16} width={260 * barK} height={32} rx={6} fill={`${col}55`} stroke={col} strokeWidth={2} />
            </g>
          ))}
          <Tx x={1818} y={730} size={52} weight={800} anchor="middle" color={K.ok} opacity={Math.min(1, eqK)}>
            =
          </Tx>
          <Tx x={1635} y={828} size={28} weight={800} anchor="middle" color={K.ok} opacity={prog(t, T[2] + 6.7, 0.5)}>
            card count = host count
          </Tx>
        </g>
      </g>
    </Stage>
  );
}

export const S14: SceneDef = {
  id: "S14",
  title: "Why trust these numbers",
  stage: 4,
  source: "testbed::closed_loop · --corrupt-every 1000",
  dur: DUR,
  cues,
  C: Body,
};
