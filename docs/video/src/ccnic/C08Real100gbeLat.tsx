import React from "react";
import { Arrow, C, Card, Cue, FONT, MONO, PacketStream, SceneDef, Stage, clamp01, countUp, fade, lerp, linScale, prog, useRealT, useT } from "../lib";
import { CK, HeroStat, SplitBar, TestbedTopo, VerdictRow, topoGeom, topoRoutes } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 10.42, "Report 5 connects a **real 100GbE physical wire**: **mercury (ConnectX-5) ⇄ 3 m DAC ⇄ CN106 eth0 (RPM0+NIX0) ⇄ saturn**.", "Report five plugs in a real three-meter hundred gigabit Ethernet cable between mercury's ConnectX-five and C N one oh six's physical Ethernet port zero."], // 0
  [10.77, 30.39, "Wire latency doesn't hide the gain: **64 B** RTT drops **10.21 → 5.96 µs** (**−4.25 µs / −41.6%**); **256 B** saves **3.76 µs**; **4 KiB** saves **2.33 µs**.", "The wire never drowns out the gain. Sixty-four byte round trip drops from ten point two one to five point nine six microseconds—saving four point two five microseconds, or forty-one point six percent. Two hundred fifty-six bytes saves three point seven six microseconds, and four kilobytes saves two point three three."], // 1
  [30.74, 48.28, "Minus **4.28 µs** fixed wire+MAC floor (`card_echo`), host-NIC drops **5.93 → 1.68 µs**—yet on-card PCIe read **1.305 µs** instead of **0.89 µs**. Why?", "Subtracting the four point two eight microsecond fixed wire floor cuts the host-NIC part from five point nine three down to one point six eight microseconds. Still, the on-card P C I express hop read one point three zero five microseconds instead of zero point eight nine. Why?"], // 2
  [48.63, 68.19, "**MRV-154** ruled out `mbuf` free (`18.7 ns`): CN106's empty `eth0` poll executes synchronous MMIO **LDADDA**, blocking **581.8 ns** (**1,119×** slower than BAR4 `0.52 ns`)!", "M R V one fifty-four found the culprit: every empty Ethernet port zero receive poll executes a synchronous M M I O atomic instruction, L D add A, stalling the card core for five hundred eighty-one point eight nanoseconds—over one thousand one hundred times slower than a half-nanosecond local B A R four poll."], // 3
  [68.54, 89.16, "The 1:1 loop trapped `W=1` in **2 empty eth0 polls** (`2 × 582 ns`). **--bar4-first** restores **0.935 µs** on-card and cuts **−370 ns** 1:1 end-to-end!", "The one-for-one polling loop always trapped single packets inside two empty Ethernet polls, creating a two-times five hundred eighty-two nanosecond staircase. Prioritizing B A R four snaps the P C I express hop back to zero point nine three five microseconds and cuts three hundred seventy nanoseconds one-to-one off mercury's end-to-end latency."], // 4
];
const DUR = 90.56; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Phase opacities
  const opTopo = fade(t, T[0] - 0.2, T[3], 0.45);
  const opC0Extra = fade(t, T[0] - 0.2, T[1], 0.4);
  const opSplit = fade(t, T[1], T[3], 0.45);
  const opSide1 = fade(t, T[1], T[2], 0.42);
  const opSide2 = fade(t, T[2], T[3], 0.42);
  const opDet = fade(t, T[3], DUR + 5, 0.45);
  const opC3Bot = fade(t, T[3], T[4], 0.42);
  const opC4Bot = fade(t, T[4], DUR + 5, 0.45);

  // Smooth morph of TestbedTopo from full view (Cue 0) to compact top strip (Cues 1-2)
  const kShrink = prog(t, T[1], 0.85);
  const topoS = lerp(0.92, 0.56, kShrink);
  const topoDx = lerp(58, 420, kShrink);
  const topoDy = lerp(120, 65, kShrink);
  const g = topoGeom(topoS, topoDx, topoDy);
  const routes = topoRoutes(g);

  // SplitBar animations (finish <= 1.2s)
  const kSdpBar = prog(t, T[1] + 0.15, 0.9);
  const kDpBar = prog(t, T[1] + 0.45, 0.9);
  const kSpot = prog(t, T[2] + 0.2, 0.75);
  const spotPulse = 0.5 + 0.5 * Math.sin(rt * 7);

  // Cue 3 micro-benchmark bar animations
  const kDet1 = prog(t, T[3] + 0.1, 0.75);
  const kDet2 = prog(t, T[3] + 0.35, 0.75);
  const kDet3 = prog(t, T[3] + 0.6, 0.9);

  // Cue 4 staircase timeline animations
  const kTrackA = prog(t, T[4] + 0.1, 0.95);
  const kTrackB = prog(t, T[4] + 0.55, 0.9);
  const kCut4 = prog(t, T[4] + 0.95, 0.65);

  // SplitBar geometry (Cues 1-2)
  const sbX = 276;
  const sbW = 910;
  const sbMax = 11.2;
  const wireW = (4.28 / sbMax) * sbW;
  const dpHostW = (1.68 / sbMax) * sbW;

  // Timeline x-scale for Cue 4 (0.0 to 1.5 µs)
  const tlX0 = 332;
  const tlX1 = 1236;
  const sx = linScale(0, 1.5, tlX0, tlX1);
  const ticks = [0.0, 0.3, 0.6, 0.9, 1.2, 1.5];

  // Cue 3 micro-bench bars
  const mbTrackX = 468;
  const mbTrackW = 706;
  const mbMax = 620;

  return (
    <>
      <Stage>
        {/* ==================== CUES 0-2: TESTBED TOPOLOGY (MORPHING) ==================== */}
        {opTopo > 0.01 && (
          <g opacity={opTopo}>
            {/* Cue 0 top title banner */}
            {opC0Extra > 0.01 && (
              <g opacity={opC0Extra}>
                <rect x={80} y={138} width={1760} height={58} rx={14} fill="#0e1d26" stroke={CK.peer} strokeWidth={2.2} />
                <text x={110} y={167} dominantBaseline="central" fontSize={24} fontWeight={800} fill={CK.peer} fontFamily={FONT}>
                  Real Dual-Host 100GbE Physical Wire Testbed
                </text>
                <text x={1810} y={167} textAnchor="end" dominantBaseline="central" fontSize={20} fontWeight={700} fill={C.text} fontFamily={MONO}>
                  saturn ⇄ PCIe Gen5 ×4 ⇄ CN106 eth0 ⇄ 3 m 100GbE DAC ⇄ mercury
                </text>
              </g>
            )}

            <TestbedTopo
              g={g}
              showPeer={true}
              hl={
                opSide2 > 0.25
                  ? { cn106: CK.cxl, arm: CK.trap, nix: CK.trap, pem: CK.push, wire: CK.peer }
                  : { wire: CK.peer, nix: CK.peer, mercury: CK.peer, pcie: CK.push }
              }
            />

            {/* Lively bidirectional packets driven by useRealT() */}
            <PacketStream pts={routes.pushH2C} travel={rt * 190 * topoS} gap={95 * topoS} size={Math.max(7, 13 * topoS)} color={CK.push} opacity={0.92} />
            <PacketStream pts={routes.wcC2H} travel={rt * 190 * topoS + 45} gap={95 * topoS} size={Math.max(7, 13 * topoS)} color={CK.ok} opacity={0.92} />
            <PacketStream pts={routes.wireTx} travel={rt * 220 * topoS} gap={72 * topoS} size={Math.max(8, 14 * topoS)} color={CK.peer} opacity={0.95} />
            <PacketStream pts={routes.wireRx} travel={rt * 220 * topoS + 36} gap={72 * topoS} size={Math.max(8, 14 * topoS)} color={CK.cxl} opacity={0.95} />

            {/* Cue 0 bottom testbed summary strip */}
            {opC0Extra > 0.01 && (
              <g opacity={opC0Extra}>
                <rect x={80} y={766} width={560} height={96} rx={14} fill="#11182c" stroke={CK.host} strokeWidth={1.8} />
                <text x={106} y={802} fontSize={22} fontWeight={800} fill={CK.host} fontFamily={FONT}>
                  Host: saturn (Xeon Gold 6438M)
                </text>
                <text x={106} y={836} fontSize={18} fill={C.muted} fontFamily={MONO}>
                  cpu40 @ 3.90 GHz · IOMMU always on · 60 MiB LLC
                </text>

                <rect x={662} y={766} width={596} height={96} rx={14} fill="#11182c" stroke={CK.card} strokeWidth={1.8} />
                <text x={688} y={802} fontSize={22} fontWeight={800} fill={CK.card} fontFamily={FONT}>
                  NIC/DPU: Marvell CN106xx (RPM0 + NIX0)
                </text>
                <text x={688} y={836} fontSize={18} fill={C.muted} fontFamily={MONO}>
                  PCIe Gen5 ×4 · 24 ARM Neoverse-N2 · eth0 100GbE
                </text>

                <rect x={1280} y={766} width={560} height={96} rx={14} fill="#11182c" stroke={CK.peer} strokeWidth={1.8} />
                <text x={1306} y={802} fontSize={22} fontWeight={800} fill={CK.peer} fontFamily={FONT}>
                  Peer: mercury (ConnectX-5 mlx5_2)
                </text>
                <text x={1306} y={836} fontSize={18} fill={C.muted} fontFamily={MONO}>
                  3 m passive 100GbE DAC (No FEC) · MRV-133 348 runs
                </text>
              </g>
            )}
          </g>
        )}

        {/* ==================== CUES 1-2: SPLIT-BAR RTT DECOMPOSITION ==================== */}
        {opSplit > 0.01 && (
          <g opacity={opSplit}>
            {/* Left panel container */}
            <rect x={60} y={468} width={1300} height={398} rx={18} fill="#10172b" stroke={C.line} strokeWidth={2} />
            <text x={88} y={506} fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
              64 B Dual-Host 100GbE RTT Decomposition (W = 1, MRV-133)
            </text>

            {/* Legend top right of panel */}
            <g transform="translate(866, 486)">
              <rect x={0} y={4} width={18} height={18} rx={4} fill={CK.peer} />
              <text x={26} y={19} fontSize={18} fontWeight={700} fill={CK.peer} fontFamily={MONO}>
                Wire+MAC floor
              </text>
              <rect x={200} y={4} width={18} height={18} rx={4} fill={CK.sdp} />
              <text x={226} y={19} fontSize={18} fontWeight={700} fill={CK.sdp} fontFamily={MONO}>
                SDP host-NIC
              </text>
              <rect x={378} y={4} width={18} height={18} rx={4} fill={CK.push} />
              <text x={404} y={19} fontSize={18} fontWeight={700} fill={CK.push} fontFamily={MONO}>
                DirectPush
              </text>
            </g>

            {/* Row 1: SDP */}
            <text x={256} y={564} textAnchor="end" dominantBaseline="central" fontSize={24} fontWeight={800} fill={CK.sdp} fontFamily={FONT}>
              SDP (strong)
            </text>
            <text x={256} y={592} textAnchor="end" dominantBaseline="central" fontSize={18} fill={C.muted} fontFamily={MONO}>
              same CN106 card
            </text>
            <SplitBar
              x={sbX}
              y={544}
              w={sbW}
              h={62}
              max={sbMax}
              segs={[
                { label: "4.28 µs wire+MAC", value: 4.28, color: CK.peer },
                { label: "5.93 µs host-NIC", value: 5.93, color: CK.sdp },
              ]}
              k={kSdpBar}
              totalText={`${countUp(t, T[1] + 0.15, 0.9, 10.21, 2)} µs`}
              totalColor={CK.sdp}
            />

            {/* Row 2: DirectPush */}
            <text x={256} y={662} textAnchor="end" dominantBaseline="central" fontSize={24} fontWeight={800} fill={CK.push} fontFamily={FONT}>
              DirectPush
            </text>
            <text x={256} y={690} textAnchor="end" dominantBaseline="central" fontSize={18} fill={C.muted} fontFamily={MONO}>
              MOVDIR64B + WC
            </text>
            <SplitBar
              x={sbX}
              y={642}
              w={sbW}
              h={62}
              max={sbMax}
              segs={[
                { label: "4.28 µs wire+MAC", value: 4.28, color: CK.peer },
                { label: "1.68 µs", value: 1.68, color: CK.push },
              ]}
              k={kDpBar}
              totalText={`${countUp(t, T[1] + 0.45, 0.9, 5.96, 2)} µs`}
              totalColor={CK.push}
            />

            {/* Vertical dashed wire floor line */}
            <line x1={sbX + wireW} y1={532} x2={sbX + wireW} y2={738} stroke={CK.peer} strokeWidth={2.2} strokeDasharray="7 6" opacity={0.85} />
            <text x={sbX + wireW / 2} y={736} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.peer} fontFamily={MONO}>
              fixed card_echo floor = 4.28 µs
            </text>
            <text x={sbX + wireW + 22} y={736} fontSize={18} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
              host-NIC shrinks 5.93 → 1.68 µs (3.53×)
            </text>

            {/* Cue 2 magnifying spotlight around the 1.68 µs cyan segment */}
            {opSide2 > 0.01 && (
              <g opacity={opSide2 * kSpot}>
                <rect
                  x={sbX + wireW - 6}
                  y={634}
                  width={dpHostW + 12}
                  height={78}
                  rx={12}
                  fill="none"
                  stroke={CK.cxl}
                  strokeWidth={3.5 + 1.2 * spotPulse}
                />
                <circle
                  cx={sbX + wireW + dpHostW * 0.5}
                  cy={634}
                  r={5.5}
                  fill={CK.cxl}
                />
                <line
                  x1={sbX + wireW + dpHostW * 0.5}
                  y1={634}
                  x2={sbX + wireW + dpHostW * 0.5}
                  y2={622}
                  stroke={CK.cxl}
                  strokeWidth={3.2}
                />
                <Arrow
                  x1={sbX + wireW + dpHostW * 0.5}
                  y1={622}
                  x2={1382}
                  y2={622}
                  color={CK.cxl}
                  w={3.2}
                  dash="8 6"
                  k={kSpot}
                />
              </g>
            )}

            {/* Bottom size comparison chips */}
            <rect x={88} y={774} width={388} height={68} rx={12} fill="#141b2e" stroke={CK.ok} strokeWidth={1.6} />
            <text x={110} y={802} fontSize={20} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              256 B: −3.76 µs (−36.2%)
            </text>
            <text x={110} y={827} fontSize={18} fill={C.muted} fontFamily={MONO}>
              DirectPush vs SDP over 100GbE
            </text>

            <rect x={500} y={774} width={404} height={68} rx={12} fill="#141b2e" stroke={CK.dma} strokeWidth={1.6} />
            <text x={522} y={802} fontSize={20} fontWeight={800} fill={CK.dma} fontFamily={MONO}>
              4 KiB: −2.33 µs (DPI DMA)
            </text>
            <text x={522} y={827} fontSize={18} fill={C.muted} fontFamily={MONO}>
              vs strong SDP (−3.17 µs default)
            </text>

            <rect x={928} y={774} width={404} height={68} rx={12} fill="#141b2e" stroke={CK.push} strokeWidth={1.6} />
            <text x={950} y={802} fontSize={20} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              Mix-Geo p99: 96.5 → 30.7 µs
            </text>
            <text x={950} y={827} fontSize={18} fill={C.muted} fontFamily={MONO}>
              64 B tail latency cut −68.2%
            </text>
          </g>
        )}

        {/* ==================== CUES 3-4: MRV-154 DETECTIVE BREAKDOWN (TOP HALF) ==================== */}
        {opDet > 0.01 && (
          <g opacity={opDet}>
            <rect x={60} y={136} width={1272} height={304} rx={18} fill="#10172b" stroke={C.line} strokeWidth={2} />
            <text x={88} y={174} fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
              MRV-154 Micro-Benchmark: Bare CN106 Card Operation Latency (78 runs)
            </text>

            {/* Row 1: BAR4 L1 poll */}
            <g opacity={Math.max(0.1, kDet1)}>
              <text x={mbTrackX - 18} y={222} textAnchor="end" dominantBaseline="central" fontSize={21} fontWeight={700} fill={CK.ok} fontFamily={FONT}>
                BAR4 L1 poll (ldar)
              </text>
              <text x={mbTrackX - 18} y={247} textAnchor="end" dominantBaseline="central" fontSize={18} fill={C.muted} fontFamily={MONO}>
                local ARM L1 cache
              </text>
              <rect x={mbTrackX} y={206} width={mbTrackW} height={48} rx={8} fill="#141b2e" stroke={C.line} strokeWidth={1.2} />
              <rect x={mbTrackX} y={206} width={Math.max(10, (0.52 / mbMax) * mbTrackW) * kDet1} height={48} rx={5} fill={CK.ok} />
              <text x={mbTrackX + 24} y={230} dominantBaseline="central" fontSize={23} fontWeight={900} fill={CK.ok} fontFamily={MONO}>
                0.52 ns
              </text>
              <text x={mbTrackX + mbTrackW - 14} y={230} textAnchor="end" dominantBaseline="central" fontSize={18} fontWeight={700} fill={C.muted} fontFamily={MONO}>
                instant local check
              </text>
            </g>

            {/* Row 2: NPA mbuf free (ruled out) */}
            <g opacity={Math.max(0.1, kDet2)}>
              <text x={mbTrackX - 18} y={294} textAnchor="end" dominantBaseline="central" fontSize={21} fontWeight={700} fill={C.muted} fontFamily={FONT}>
                NPA mbuf free
              </text>
              <text x={mbTrackX - 18} y={319} textAnchor="end" dominantBaseline="central" fontSize={18} fill={C.dim} fontFamily={MONO}>
                defer free: −10 ns
              </text>
              <rect x={mbTrackX} y={278} width={mbTrackW} height={48} rx={8} fill="#141b2e" stroke={C.line} strokeWidth={1.2} />
              <rect x={mbTrackX} y={278} width={(18.7 / mbMax) * mbTrackW * kDet2} height={48} rx={6} fill={C.dim} />
              <text x={mbTrackX + (18.7 / mbMax) * mbTrackW * kDet2 + 16} y={302} dominantBaseline="central" fontSize={22} fontWeight={800} fill={C.muted} fontFamily={MONO}>
                18.7 ns
              </text>
              {/* Subtle strike-through badge */}
              <rect x={mbTrackX + mbTrackW - 234} y={286} width={220} height={32} rx={6} fill="#4a557230" stroke={C.dim} strokeWidth={1.4} />
              <text x={mbTrackX + mbTrackW - 124} y={302} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={C.muted} fontFamily={MONO}>
                NOT THE CULPRIT
              </text>
            </g>

            {/* Row 3: eth0 empty poll LDADDA */}
            <g opacity={Math.max(0.1, kDet3)}>
              <text x={mbTrackX - 18} y={370} textAnchor="end" dominantBaseline="central" fontSize={21} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
                eth0 empty poll
              </text>
              <text x={mbTrackX - 18} y={395} textAnchor="end" dominantBaseline="central" fontSize={18} fill="#ff8f91" fontFamily={MONO}>
                LDADDA CQ_OP_STATUS
              </text>
              <rect x={mbTrackX} y={354} width={mbTrackW} height={54} rx={8} fill="#141b2e" stroke={CK.trap} strokeWidth={2} />
              <rect x={mbTrackX} y={354} width={(581.8 / mbMax) * mbTrackW * kDet3} height={54} rx={8} fill={CK.trap} opacity={0.92} />
              <text
                x={mbTrackX + 20}
                y={381}
                dominantBaseline="central"
                fontSize={25}
                fontWeight={900}
                fill={C.bg}
                fontFamily={MONO}
                opacity={clamp01((kDet3 - 0.25) * 2)}
              >
                {countUp(t, T[3] + 0.6, 0.9, 581.8, 1)} ns
              </text>
              <rect
                x={mbTrackX + (581.8 / mbMax) * mbTrackW - 236}
                y={364}
                width={222}
                height={34}
                rx={8}
                fill={C.bg}
                opacity={clamp01((kDet3 - 0.5) * 2.5)}
              />
              <text
                x={mbTrackX + (581.8 / mbMax) * mbTrackW - 125}
                y={381}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={20}
                fontWeight={900}
                fill={CK.cxl}
                fontFamily={MONO}
                opacity={clamp01((kDet3 - 0.5) * 2.5)}
              >
                1,119× SLOWER!
              </text>
            </g>
          </g>
        )}

        {/* ==================== CUE 4: 2 x 582 ns STAIRCASE TIMELINE (BOTTOM HALF) ==================== */}
        {opC4Bot > 0.01 && (
          <g opacity={opC4Bot}>
            <rect x={60} y={462} width={1300} height={406} rx={18} fill="#10172b" stroke={C.line} strokeWidth={2} />
            <text x={88} y={498} fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
              On-Card PCIe Hop Timeline: Baseline 1:1 Loop vs. --bar4-first (64 B, W = 1)
            </text>

            {/* Time axis */}
            <line x1={tlX0} y1={526} x2={tlX1} y2={526} stroke={C.dim} strokeWidth={2} />
            {ticks.map((v) => (
              <g key={v}>
                <line x1={sx(v)} y1={526} x2={sx(v)} y2={786} stroke={C.line} strokeWidth={1.2} opacity={0.55} />
                <text x={sx(v)} y={518} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
                  {v.toFixed(1)} µs
                </text>
              </g>
            ))}

            {/* Vertical cyan dashed marker where host reply actually lands (~0.90 µs) */}
            <line x1={sx(0.90)} y1={532} x2={sx(0.90)} y2={796} stroke={CK.push} strokeWidth={2.6} strokeDasharray="8 6" />
            <rect x={sx(0.90) - 168} y={796} width={336} height={34} rx={8} fill={`${CK.push}22`} stroke={CK.push} strokeWidth={1.6} />
            <text x={sx(0.90)} y={813} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              Host reply arrives (~0.90 µs)
            </text>

            {/* TRACK A: Baseline 1:1 loop */}
            <g opacity={kTrackA}>
              <text x={316} y={584} textAnchor="end" dominantBaseline="central" fontSize={21} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
                Baseline 1:1 loop
              </text>
              <text x={316} y={612} textAnchor="end" dominantBaseline="central" fontSize={18} fill={C.muted} fontFamily={MONO}>
                2.01 empty eth0/pkt
              </text>

              {/* t0 WC push */}
              <rect x={sx(0)} y={562} width={sx(0.12) - sx(0)} height={68} rx={8} fill={`${CK.card}30`} stroke={CK.card} strokeWidth={2} />
              <text x={(sx(0) + sx(0.12)) / 2} y={596} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.card} fontFamily={MONO}>
                t0 WC
              </text>

              {/* LDADDA #1 */}
              <rect
                x={sx(0.13)}
                y={562}
                width={(sx(0.712) - sx(0.13)) * clamp01(kTrackA * 2)}
                height={68}
                rx={8}
                fill="url(#hatchRed)"
                stroke={CK.trap}
                strokeWidth={2.5}
              />
              <text x={(sx(0.13) + sx(0.712)) / 2} y={585} textAnchor="middle" fontSize={19} fontWeight={900} fill={C.text} fontFamily={MONO} opacity={clamp01(kTrackA * 2 - 0.3)}>
                LDADDA #1 (582 ns)
              </text>
              <text x={(sx(0.13) + sx(0.712)) / 2} y={613} textAnchor="middle" fontSize={18} fill="#ffb3b5" fontFamily={MONO} opacity={clamp01(kTrackA * 2 - 0.3)}>
                empty eth0 poll
              </text>

              {/* LDADDA #2 */}
              <rect
                x={sx(0.722)}
                y={562}
                width={(sx(1.304) - sx(0.722)) * clamp01(kTrackA * 2 - 0.8)}
                height={68}
                rx={8}
                fill="url(#hatchRed)"
                stroke={CK.trap}
                strokeWidth={2.8}
              />
              <text x={(sx(0.722) + sx(1.304)) / 2} y={585} textAnchor="middle" fontSize={19} fontWeight={900} fill={C.text} fontFamily={MONO} opacity={clamp01(kTrackA * 2 - 1.1)}>
                LDADDA #2 (582 ns)
              </text>
              <text x={(sx(0.722) + sx(1.304)) / 2} y={613} textAnchor="middle" fontSize={18} fill={CK.cxl} fontFamily={FONT} fontWeight={700} opacity={clamp01(kTrackA * 2 - 1.1)}>
                reply trapped inside!
              </text>

              {/* 1.305 µs pin */}
              <circle cx={sx(1.305)} cy={596} r={9} fill={CK.trap} opacity={clamp01(kTrackA * 2 - 1.2)} />
              <text x={sx(1.305) + 14} y={584} fontSize={22} fontWeight={900} fill={CK.trap} fontFamily={MONO} opacity={clamp01(kTrackA * 2 - 1.2)}>
                1.305 µs
              </text>
              <text x={sx(1.305) + 14} y={612} fontSize={18} fill={C.muted} fontFamily={MONO} opacity={clamp01(kTrackA * 2 - 1.2)}>
                2×582 ns
              </text>
            </g>

            {/* TRACK B: --bar4-first */}
            <g opacity={kTrackB}>
              <text x={316} y={704} textAnchor="end" dominantBaseline="central" fontSize={21} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                --bar4-first
              </text>
              <text x={316} y={732} textAnchor="end" dominantBaseline="central" fontSize={18} fill={CK.ok} fontFamily={MONO}>
                0.002 empty eth0/pkt
              </text>

              {/* t0 WC push */}
              <rect x={sx(0)} y={682} width={sx(0.12) - sx(0)} height={68} rx={8} fill={`${CK.card}30`} stroke={CK.card} strokeWidth={2} />
              <text x={(sx(0) + sx(0.12)) / 2} y={716} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={800} fill={CK.card} fontFamily={MONO}>
                t0 WC
              </text>

              {/* Tight L1 BAR4 checks */}
              <rect
                x={sx(0.13)}
                y={682}
                width={(sx(0.935) - sx(0.13)) * kTrackB}
                height={68}
                rx={8}
                fill={`${CK.ok}24`}
                stroke={CK.ok}
                strokeWidth={2.5}
              />
              <text x={(sx(0.13) + sx(0.935)) / 2} y={705} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                Tight L1 BAR4 polls (0.52 ns each)
              </text>
              <text x={(sx(0.13) + sx(0.935)) / 2} y={733} textAnchor="middle" fontSize={18} fill={C.text} fontFamily={FONT}>
                zero MMIO stall waiting on PCIe reply
              </text>

              {/* 0.935 µs pin */}
              <circle cx={sx(0.935)} cy={716} r={10} fill={CK.push} stroke={C.bg} strokeWidth={2.5} />
              <text x={sx(0.935) + 16} y={705} fontSize={23} fontWeight={900} fill={CK.push} fontFamily={MONO}>
                0.935 µs
              </text>
              <text x={sx(0.935) + 16} y={733} fontSize={18} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
                caught immediately!
              </text>
            </g>

            {/* −370 ns savings bracket between 0.935 and 1.305 µs */}
            {kCut4 > 0.05 && (
              <g opacity={kCut4}>
                <line x1={sx(0.935)} y1={648} x2={sx(1.305)} y2={648} stroke={CK.ok} strokeWidth={3.5} />
                <line x1={sx(0.935)} y1={638} x2={sx(0.935)} y2={658} stroke={CK.ok} strokeWidth={3} />
                <line x1={sx(1.305)} y1={638} x2={sx(1.305)} y2={658} stroke={CK.ok} strokeWidth={3} />
                <rect x={(sx(0.935) + sx(1.305)) / 2 - 64} y={634} width={128} height={28} rx={7} fill={C.bg} stroke={CK.ok} strokeWidth={2} />
                <text x={(sx(0.935) + sx(1.305)) / 2} y={648} textAnchor="middle" dominantBaseline="central" fontSize={18} fontWeight={900} fill={CK.ok} fontFamily={MONO}>
                  −370 ns
                </text>
              </g>
            )}
          </g>
        )}
      </Stage>

      {/* ==================== HTML OVERLAYS (OUTSIDE <Stage>) ==================== */}

      {/* Cue 1 right panel */}
      {opSide1 > 0.01 && (
        <>
          <HeroStat
            x={1390}
            y={468}
            w={470}
            h={226}
            value="−4.25 µs"
            label="−41.6% Real 100GbE RTT"
            sub="64 B W=1: 10.21 → 5.96 µs (MRV-133)"
            color={CK.ok}
            opacity={opSide1 * kDpBar}
            dy={lerp(18, 0, kDpBar)}
          />
          <Card
            x={1390}
            y={718}
            w={470}
            h={148}
            border={CK.push}
            title="Host-NIC Hop Cut 3.53×"
            titleColor={CK.push}
            style={{ opacity: opSide1 * kDpBar }}
          >
            <div style={{ fontSize: 22, color: C.text, lineHeight: 1.38 }}>
              Minus <span style={{ color: CK.peer, fontFamily: MONO, fontWeight: 800 }}>4.28 µs</span> floor:{" "}
              <span style={{ color: CK.sdp, fontFamily: MONO, fontWeight: 800 }}>5.93</span> →{" "}
              <span style={{ color: CK.push, fontFamily: MONO, fontWeight: 800 }}>1.68 µs</span>.
            </div>
          </Card>
        </>
      )}

      {/* Cue 2 right mystery spotlight card */}
      {opSide2 > 0.01 && (
        <Card
          x={1390}
          y={468}
          w={470}
          h={398}
          border={CK.cxl}
          style={{
            opacity: opSide2 * kSpot,
            transform: `translateY(${lerp(18, 0, kSpot)}px)`,
            background: "linear-gradient(155deg, rgba(32,26,18,0.96), rgba(14,18,34,0.96))",
          }}
        >
          <div
            style={{
              display: "inline-block",
              fontSize: 18,
              fontWeight: 900,
              color: C.bg,
              background: CK.cxl,
              fontFamily: MONO,
              borderRadius: 8,
              padding: "5px 12px",
              marginBottom: 14,
            }}
          >
            THE +410 ns MYSTERY
          </div>
          <div style={{ fontSize: 26, fontWeight: 800, color: C.text, lineHeight: 1.3 }}>
            Why did the on-card PCIe hop read{" "}
            <span style={{ color: CK.trap, fontFamily: MONO }}>1.305 µs</span> instead of{" "}
            <span style={{ color: CK.ok, fontFamily: MONO }}>0.89 µs</span>?
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 22 }}>
            <div style={{ background: "#11182c", border: `1.5px solid ${CK.ok}88`, borderRadius: 10, padding: "10px 16px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 20, color: C.muted }}>Standalone PCIe hop</span>
              <span style={{ fontSize: 24, fontWeight: 900, color: CK.ok, fontFamily: MONO }}>0.89 µs</span>
            </div>
            <div style={{ background: "#24141c", border: `1.5px solid ${CK.trap}`, borderRadius: 10, padding: "10px 16px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 20, color: C.text, fontWeight: 700 }}>Over 100GbE (CNTVCT)</span>
              <span style={{ fontSize: 24, fontWeight: 900, color: CK.trap, fontFamily: MONO }}>1.305 µs</span>
            </div>
          </div>

          <div style={{ fontSize: 19, color: CK.cxl, fontFamily: MONO, fontWeight: 700, marginTop: 18 }}>
            → +410 ns hidden inside CN106!
          </div>
        </Card>
      )}

      {/* Cue 3 & 4 top-right culprit card */}
      {opDet > 0.01 && (
        <Card
          x={1358}
          y={136}
          w={502}
          h={304}
          border={CK.trap}
          title="Root Cause: Synchronous MMIO"
          titleColor={CK.trap}
          style={{ opacity: opDet * kDet3 }}
        >
          <div
            style={{
              background: "#26121a",
              border: `1.5px solid ${CK.trap}`,
              borderRadius: 10,
              padding: "10px 14px",
              fontSize: 20,
              fontWeight: 800,
              color: "#ff9b9d",
              fontFamily: MONO,
              marginTop: 4,
            }}
          >
            LDADDA NIX_LF_CQ_OP_STATUS
          </div>
          <div style={{ fontSize: 21, color: C.text, marginTop: 14, lineHeight: 1.38 }}>
            Every empty <span style={{ fontFamily: MONO, color: CK.peer }}>eth0</span> RX poll stalls the ARM core for{" "}
            <strong style={{ color: CK.trap, fontFamily: MONO }}>581.8 ns</strong> —{" "}
            <strong style={{ color: CK.cxl, fontFamily: MONO }}>1,119×</strong> slower than a local L1{" "}
            <span style={{ fontFamily: MONO, color: CK.ok }}>BAR4</span> poll.
          </div>
          <div style={{ fontSize: 18, color: C.muted, fontFamily: MONO, marginTop: 12 }}>
            Ruled out: 6 µs cold arrivals (0.92 µs)
          </div>
        </Card>
      )}

      {/* Cue 3 bottom explanation */}
      {opC3Bot > 0.01 && (
        <>
          <HeroStat
            x={60}
            y={464}
            w={460}
            h={246}
            value="1,119×"
            label="MMIO vs. Local L1 Poll Gap"
            sub="581.8 ns LDADDA vs 0.52 ns ldar"
            color={CK.trap}
            opacity={opC3Bot * kDet3}
            dy={lerp(18, 0, kDet3)}
          />
          <VerdictRow
            x={552}
            y={464}
            w={1308}
            badge="ASYMMETRY"
            badgeColor={CK.trap}
            title="A naive 1:1 polling loop alternates cheap BAR4 checks with 582 ns MMIO stalls"
            detail="While waiting ~900 ns for saturn's PCIe reply, polling eth0 blind traps the ARM core in back-to-back stalls."
            opacity={opC3Bot * kDet3}
          />
          <VerdictRow
            x={552}
            y={598}
            w={1308}
            badge="RULED OUT"
            badgeColor={C.muted}
            title="NPA mbuf free (18.7 ns) and 6 µs cold-arrival intervals were NOT the bottleneck"
            detail="Deferring mbuf free saved only 10 ns; synthetic 6 µs inter-arrival runs stayed at 0.915–0.925 µs."
            opacity={opC3Bot * kDet2}
          />
          <VerdictRow
            x={60}
            y={738}
            w={1800}
            badge="NEXT"
            badgeColor={CK.ok}
            title="Watch how two back-to-back 582 ns stalls quantize W=1 latency to 1.305 µs — and how --bar4-first breaks the staircase"
            detail="General driver fix via cached CQE peek (cq_ctx->caching = 1) tracked as follow-up backlog MRV-161."
            opacity={opC3Bot * kDet3}
          />
        </>
      )}

      {/* Cue 4 bottom-right savings stats */}
      {opC4Bot > 0.01 && (
        <>
          <HeroStat
            x={1390}
            y={462}
            w={470}
            h={206}
            value="−370 ns"
            label="1:1 Cut on mercury E2E RTT"
            sub="6.025 → 5.655 µs (same-round A/B)"
            color={CK.ok}
            opacity={opC4Bot * kCut4}
            dy={lerp(16, 0, kCut4)}
          />
          <Card
            x={1390}
            y={688}
            w={470}
            h={180}
            border={CK.push}
            title="--bar4-first Verified Gains"
            titleColor={CK.push}
            style={{ opacity: opC4Bot * kTrackB }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 7, fontSize: 19, fontFamily: MONO, color: C.text }}>
              <div>
                64 B on-card: <span style={{ color: CK.trap }}>1.305</span> → <span style={{ color: CK.push, fontWeight: 800 }}>0.935 µs</span>
              </div>
              <div>
                256 B on-card: <span style={{ color: CK.trap }}>1.355</span> → <span style={{ color: CK.push, fontWeight: 800 }}>1.205 µs</span>
              </div>
              <div style={{ fontSize: 18, color: CK.ok }}>
                W=32 auto-bypasses (0 cost)
              </div>
            </div>
          </Card>
        </>
      )}
    </>
  );
}

export const C08: SceneDef = {
  id: "C08",
  title: "5 \u00b7 Real 100GbE & 582 ns Staircase",
  stage: 4,
  source: "MRV-152 \u00b7 MRV-118 (303) \u00b7 MRV-133 (348) \u00b7 MRV-154 (78 runs)",
  dur: DUR,
  cues,
  C: Body,
};
