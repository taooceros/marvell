import React from "react";
import {
  C,
  Cue,
  FONT,
  MONO,
  PacketStream,
  QueueGauge,
  SceneDef,
  Stage,
  clamp01,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import { BoundaryBar, CK, HBarChart, HeroStat, QuestionBanner } from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 12.86, "**Question 3**: Idle RTT is **0.9 µs**—but **what breaks at high concurrency** on a real DPU, and how do we tame tail latency?", "Question three: idle latency is under a microsecond and raw push hits one hundred seventy-five million per second. So what breaks when we crank up high concurrency on a real D P U, and how do we tame tail latency?"], // 0
  [13.21, 27.09, "Real DPU silicon exposed a **hardware contention cliff**: relentless empty NIC polling collides with concurrent inbound writes.", "Real D P U silicon exposed a hardware cliff invisible to two-socket emulators. When card cores relentlessly poll empty slots during heavy inbound writes, the card's inbound bridge serializes writes down to roughly thirteen million per second."], // 1
  [27.44, 38.50, "**~13 M/s** is **not** the link ceiling (push hits **175 M/s**, 1-core RPC **50.4 M/s**)—it is congestion collapse when bursts pile up.", "Crucially, thirteen million is not the channel cap—raw push hits one hundred seventy-five million and one-core R P C hits fifty million. It is congestion collapse when unpaced bursts queue up."], // 2
  [38.85, 50.95, "Without pacing at **16–30 M/s**, host queues back up (**48–248** entries), exploding open-loop **p99** tail latency to **44–51 µs**.", "Without pacing at sixteen to thirty million per second, backpressure fills forty-eight to two hundred forty-eight host queue entries, exploding p ninety-nine tail latency to forty-four to fifty-one microseconds."], // 3
  [51.30, 65.80, "A **host credit cap** (`32–48` in-flight) clears queues (**1–3** entries), cuts **p99 6–64×** (**44.5 → 7.0 µs** Poisson), and lifts 4C **+43%**.", "A simple host credit gate capping thirty-two to forty-eight requests in flight drains queues to one to three entries, cuts tail latency six to sixty-four times—down to seven microseconds under Poisson load—and lifts four-core throughput forty-three percent."], // 4
  [66.15, 79.73, "**Honest boundary**: host-only credits hold **≤ 2 µs** up to **13.5 M/s** (1C) / **10 M/s** (4C); faster sub-2 µs needs **on-demand card backoff**.", "Honest boundary: host-only credits hold under two microseconds up to thirteen point five million on one core or ten million on four. Going faster at sub-two microseconds requires on-demand card backoff after busy bursts."], // 5
];
const DUR = 81.13; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Beat opacities (clean crossfades, zero text collision)
  const op0 = fade(t, T[0], T[1] - 0.1, 0.5);
  const op1 = fade(t, T[1], T[2] - 0.1, 0.5);
  const op2 = fade(t, T[2], T[3] - 0.1, 0.5);
  const op34 = fade(t, T[3], T[5] - 0.1, 0.5);
  const op3Only = fade(t, T[3], T[4] - 0.12, 0.45);
  const op4Only = fade(t, T[4] + 0.15, T[5] - 0.1, 0.45);
  const op5 = fade(t, T[5] + 0.05, DUR, 0.5);

  // Cue 3 -> 4 credit gate morph
  const kGate = prog(t, T[4], 0.75);
  const fillUnpaced = lerp(0.22, 0.90, prog(t, T[3] + 0.15, 1.25)) + 0.04 * Math.sin(rt * 5.0);
  const queueLevel = clamp01(lerp(fillUnpaced, 0.09 + 0.02 * Math.sin(rt * 3.0), kGate));

  // Cue 2 bar animations (settle within 1.2s)
  const kb0 = prog(t, T[2] + 0.15, 0.9);
  const kb1 = prog(t, T[2] + 0.35, 0.9);
  const kb2 = prog(t, T[2] + 0.55, 0.9);

  // Ambient pulse
  const pulse = 0.5 + 0.5 * Math.sin(rt * 5.5);

  return (
    <>
      <Stage>
        {/* ==================== CUE 0: Anchor Metrics -> Heavy Concurrency ? ==================== */}
        {op0 > 0.01 && (
          <g opacity={op0}>
            {/* Converging arrows from green anchor pills to orange ? card */}
            <path d="M 635 505 C 780 505, 830 575, 1020 575" fill="none" stroke={CK.ok} strokeWidth={4} strokeDasharray="10 8" opacity={0.75} />
            <path d="M 635 660 C 780 660, 830 590, 1020 590" fill="none" stroke={CK.ok} strokeWidth={4} strokeDasharray="10 8" opacity={0.75} />
            <polygon points="1032,582 1010,570 1010,594" fill={CK.card} />
            <text x={855} y={512} textAnchor="middle" fontSize={21} fontWeight={700} fill={CK.card} fontFamily={MONO}>
              Scale up load?
            </text>
          </g>
        )}

        {/* ==================== CUE 1: Inbound Bridge Contention Cliff Schematic ==================== */}
        {op1 > 0.01 && (
          <g opacity={op1}>
            {/* Host Cores */}
            <rect x={95} y={220} width={315} height={385} rx={20} fill="#11182c" stroke={CK.host} strokeWidth={2.5} />
            <text x={252} y={272} textAnchor="middle" fontSize={28} fontWeight={800} fill={CK.host} fontFamily={FONT}>
              Host Cores
            </text>
            {[0, 1, 2, 3].map((i) => (
              <g key={i}>
                <rect x={135} y={306 + i * 66} width={235} height={48} rx={10} fill="#1a2542" stroke={CK.push} strokeWidth={1.8} />
                <text x={252} y={331 + i * 66} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={700} fill={C.text} fontFamily={MONO}>
                  Core {i} · Direct Push
                </text>
              </g>
            ))}

            {/* Inbound write stream: Host -> Bridge */}
            <line x1={410} y1={412} x2={720} y2={412} stroke={CK.push} strokeWidth={12} opacity={0.18} />
            <line x1={410} y1={412} x2={720} y2={412} stroke={CK.push} strokeWidth={4.5} opacity={0.8} />
            <PacketStream pts={[[410, 412], [720, 412]]} travel={rt * 190} gap={48} size={14} color={CK.push} />
            <text x={565} y={372} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.push} fontFamily={FONT}>
              Heavy Inbound Writes
            </text>
            <text x={565} y={452} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={MONO}>
              PCIe Gen5 ×4
            </text>

            {/* Center: NIC Inbound PCIe Bridge with pulsing collision zone */}
            <rect x={725} y={190} width={470} height={445} rx={22} fill="#191322" stroke={CK.trap} strokeWidth={3} />
            <text x={960} y={240} textAnchor="middle" fontSize={27} fontWeight={800} fill={C.text} fontFamily={FONT}>
              NIC Inbound PCIe Bridge
            </text>

            {/* Pulsing collision rings */}
            <circle cx={960} cy={412} r={88 + 16 * pulse} fill={CK.trap} opacity={0.12 + 0.1 * pulse} />
            <circle cx={960} cy={412} r={58 + 10 * pulse} fill={CK.trap} opacity={0.22 + 0.14 * pulse} stroke={CK.trap} strokeWidth={3} />
            <text x={960} y={400} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
              HEAD-ON
            </text>
            <text x={960} y={430} textAnchor="middle" dominantBaseline="central" fontSize={22} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              COLLISION
            </text>

            {/* Contention Cliff Badge */}
            <rect x={758} y={536} width={404} height={68} rx={14} fill="#2b1219" stroke={CK.trap} strokeWidth={2.4} />
            <text x={960} y={562} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
              Inbound Bridge Contention Cliff
            </text>
            <text x={960} y={588} textAnchor="middle" dominantBaseline="central" fontSize={21} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              Serializes down to ~13 M/s
            </text>

            {/* Relentless empty polling stream: NIC Cores -> Bridge */}
            <line x1={1510} y1={412} x2={1200} y2={412} stroke={CK.trap} strokeWidth={12} opacity={0.18} />
            <line x1={1510} y1={412} x2={1200} y2={412} stroke={CK.trap} strokeWidth={4.5} opacity={0.8} />
            <PacketStream pts={[[1510, 412], [1200, 412]]} travel={rt * 220} gap={44} size={13} color={CK.trap} />
            <text x={1355} y={372} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
              Empty Polling Loop
            </text>
            <text x={1355} y={452} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={MONO}>
              Relentless card reads
            </text>

            {/* NIC Cores */}
            <rect x={1510} y={220} width={315} height={385} rx={20} fill="#14182a" stroke={CK.card} strokeWidth={2.5} />
            <text x={1667} y={272} textAnchor="middle" fontSize={28} fontWeight={800} fill={CK.card} fontFamily={FONT}>
              NIC Cores (DPU)
            </text>
            {[0, 1, 2, 3].map((i) => (
              <g key={i}>
                <rect x={1550} y={306 + i * 66} width={235} height={48} rx={10} fill="#261824" stroke={CK.trap} strokeWidth={1.8} />
                <text x={1667} y={331 + i * 66} textAnchor="middle" dominantBaseline="central" fontSize={20} fontWeight={700} fill={C.text} fontFamily={MONO}>
                  Card Core {i} · Poll
                </text>
              </g>
            ))}

            {/* Bottom Silicon Note Pill */}
            <rect x={370} y={718} width={1180} height={64} rx={16} fill="#141b2e" stroke={CK.cxl} strokeWidth={2} />
            <text x={960} y={751} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={700} fill={CK.cxl} fontFamily={FONT}>
              Only visible on real DPU silicon — invisible on 2-socket CPU emulators
            </text>
          </g>
        )}

        {/* ==================== CUE 2: HBarChart Dispelling the Myth ==================== */}
        {op2 > 0.01 && (
          <g opacity={op2}>
            <text x={120} y={196} fontSize={36} fontWeight={800} fill={C.text} fontFamily={FONT}>
              ~13 M/s Is NOT the Channel Speed Limit
            </text>
            <text x={120} y={238} fontSize={23} fill={C.muted} fontFamily={FONT}>
              The PCIe link has massive headroom — the cliff is congestion collapse when unpaced bursts pile up.
            </text>

            <rect x={100} y={280} width={1720} height={415} rx={22} fill="#11182c" stroke={C.line} strokeWidth={2} />
            <HBarChart
              x={135}
              y={330}
              w={1650}
              labelW={360}
              barH={76}
              gap={44}
              max={190}
              rows={[
                {
                  label: "Raw 1-Core Direct Push",
                  sub: "Unidirectional ceiling",
                  value: 175,
                  valueText: "175 M/s",
                  color: CK.ok,
                  k: kb0,
                },
                {
                  label: "1-Core Closed-Loop RPC",
                  sub: "Round-trip request-reply",
                  value: 50.4,
                  valueText: "50.4 M/s",
                  color: CK.push,
                  k: kb1,
                },
                {
                  label: "Unpaced Contention Cliff",
                  sub: "Empty polling + bursts",
                  value: 13,
                  valueText: "~13 M/s",
                  color: CK.trap,
                  k: kb2,
                  tag: "Polling collision collapse",
                },
              ]}
            />

            <rect x={230} y={735} width={1460} height={66} rx={16} fill="#1b1526" stroke={CK.trap} strokeWidth={2} />
            <text x={960} y={769} textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Cause: unpaced open-loop bursts collide with empty NIC polling and trigger queueing collapse
            </text>
          </g>
        )}

        {/* ==================== CUE 3 & 4: Morphing Pipeline (Unpaced -> Credit Gate) ==================== */}
        {op34 > 0.01 && (
          <g opacity={op34}>
            {/* Section Title */}
            <text x={100} y={182} fontSize={32} fontWeight={800} fill={kGate > 0.5 ? CK.ok : CK.trap} fontFamily={FONT}>
              {kGate > 0.5
                ? "Solution: Host In-Flight Credit Gate (32–48 Slots)"
                : "Unpaced Open-Loop Collapse (16–30 M/s)"}
            </text>

            {/* Pipeline Panel Background */}
            <rect x={90} y={210} width={1100} height={540} rx={22} fill="#11182c" stroke={kGate > 0.5 ? CK.ok : CK.trap} strokeWidth={2.2} />

            {/* Host Core */}
            <rect x={125} y={375} width={165} height={150} rx={16} fill="#19233e" stroke={CK.host} strokeWidth={2.2} />
            <text x={207} y={436} textAnchor="middle" fontSize={24} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host
            </text>
            <text x={207} y={468} textAnchor="middle" fontSize={20} fill={C.muted} fontFamily={FONT}>
              App Cores
            </text>

            {/* Arrow Core -> Queue */}
            <line x1={290} y1={450} x2={365} y2={450} stroke={CK.push} strokeWidth={4} />
            <polygon points="373,450 356,441 356,459" fill={CK.push} />

            {/* Host Memory Queues (QueueGauge) */}
            <QueueGauge
              x={380}
              y={300}
              w={120}
              h={300}
              level={queueLevel}
              color={CK.ok}
              hot={CK.trap}
              hotAt={0.45}
              slots={10}
              label="Host Memory Queues"
              fontSize={21}
            />
            {/* Dynamic queue depth badge above gauge */}
            <rect
              x={338}
              y={238}
              width={204}
              height={44}
              rx={10}
              fill={kGate > 0.5 ? "#102826" : "#2c131c"}
              stroke={kGate > 0.5 ? CK.ok : CK.trap}
              strokeWidth={2}
            />
            <text
              x={440}
              y={261}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={20}
              fontWeight={800}
              fill={kGate > 0.5 ? CK.ok : CK.trap}
              fontFamily={MONO}
            >
              {kGate > 0.5 ? "1–3 entries" : "48–248 queued"}
            </text>

            {/* Link from Queue -> NIC */}
            <line x1={500} y1={450} x2={915} y2={450} stroke={CK.push} strokeWidth={4.5} opacity={0.6} />
            <PacketStream
              pts={[[500, 450], [915, 450]]}
              travel={rt * (kGate > 0.5 ? 185 : 90)}
              gap={kGate > 0.5 ? 56 : 78}
              size={13}
              color={kGate > 0.5 ? CK.ok : CK.push}
            />

            {/* Backpressure arrows (Cue 3 only, fades out as Credit Gate slides in) */}
            {1 - kGate > 0.02 && (
              <g opacity={1 - kGate}>
                <line x1={910} y1={525} x2={522} y2={525} stroke={CK.trap} strokeWidth={4} strokeDasharray="10 7" />
                <polygon points="512,525 532,515 532,535" fill={CK.trap} />
                <PacketStream pts={[[910, 525], [520, 525]]} travel={rt * 160} gap={74} size={12} color={CK.trap} />
                <text x={715} y={562} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
                  ← Backpressure Stall ←
                </text>
              </g>
            )}

            {/* Credit Gate sliding in (Cue 4) */}
            {kGate > 0.01 && (
              <g opacity={kGate} transform={`translate(0, ${lerp(-50, 0, kGate)})`}>
                <rect x={585} y={300} width={250} height={300} rx={18} fill="#0e262b" stroke={CK.push} strokeWidth={3} />
                <rect x={585} y={300} width={250} height={54} rx={18} fill={`${CK.push}28`} />
                <text x={710} y={334} textAnchor="middle" fontSize={21} fontWeight={800} fill={CK.push} fontFamily={MONO}>
                  CREDIT GATE
                </text>
                <text x={710} y={405} textAnchor="middle" fontSize={36} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
                  32–48
                </text>
                <text x={710} y={444} textAnchor="middle" fontSize={21} fontWeight={700} fill={C.text} fontFamily={FONT}>
                  In-Flight Slots
                </text>
                {/* Small slot pills */}
                {[0, 1, 2, 3, 4, 5].map((i) => (
                  <rect
                    key={i}
                    x={615 + i * 32}
                    y={480}
                    width={24}
                    height={36}
                    rx={5}
                    fill={i < 4 ? CK.ok : "#1b2c38"}
                    stroke={CK.push}
                    strokeWidth={1.5}
                  />
                ))}
                <text x={710} y={558} textAnchor="middle" fontSize={18} fontWeight={700} fill={CK.ok} fontFamily={MONO}>
                  Paces before PCIe
                </text>
              </g>
            )}

            {/* NIC Inbound Bridge (Right of Pipeline) */}
            <rect x={920} y={325} width={235} height={250} rx={18} fill="#161c30" stroke={kGate > 0.5 ? CK.ok : CK.card} strokeWidth={2.5} />
            <text x={1037} y={428} textAnchor="middle" fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
              NIC Bridge
            </text>
            <text x={1037} y={464} textAnchor="middle" fontSize={20} fontWeight={700} fill={kGate > 0.5 ? CK.ok : CK.trap} fontFamily={MONO}>
              {kGate > 0.5 ? "Smooth · No Stall" : "Congested"}
            </text>
          </g>
        )}

        {/* ==================== CUE 5: Compact Architecture + Safe-Zone Comparison ==================== */}
        {op5 > 0.01 && (
          <g opacity={op5}>
            {/* Compact top credit-paced pipeline strip (y = 148..390) */}
            <rect x={90} y={148} width={1740} height={235} rx={20} fill="#11182c" stroke={C.line} strokeWidth={2} />
            <text x={125} y={192} fontSize={24} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              CREDIT-PACED ARCHITECTURE · STEADY STATE
            </text>

            {/* Nodes along top strip */}
            <rect x={135} y={230} width={235} height={112} rx={14} fill="#19233e" stroke={CK.host} strokeWidth={2} />
            <text x={252} y={280} textAnchor="middle" fontSize={24} fontWeight={700} fill={C.text} fontFamily={FONT}>
              Host Cores
            </text>
            <text x={252} y={312} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
              Open-loop traffic
            </text>

            <line x1={370} y1={286} x2={475} y2={286} stroke={CK.ok} strokeWidth={4} />
            <PacketStream pts={[[370, 286], [475, 286]]} travel={rt * 120} gap={48} size={11} color={CK.ok} />

            <rect x={480} y={230} width={265} height={112} rx={14} fill="#102728" stroke={CK.ok} strokeWidth={2.2} />
            <text x={612} y={276} textAnchor="middle" fontSize={23} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
              Host Memory Queues
            </text>
            <text x={612} y={312} textAnchor="middle" fontSize={20} fontWeight={700} fill={C.text} fontFamily={MONO}>
              1–3 entries (clear)
            </text>

            <line x1={745} y1={286} x2={855} y2={286} stroke={CK.push} strokeWidth={4} />
            <PacketStream pts={[[745, 286], [855, 286]]} travel={rt * 120} gap={48} size={11} color={CK.push} />

            <rect x={860} y={230} width={340} height={112} rx={14} fill="#0e2630" stroke={CK.push} strokeWidth={2.5} />
            <text x={1030} y={276} textAnchor="middle" fontSize={23} fontWeight={800} fill={CK.push} fontFamily={FONT}>
              Host Credit Gate
            </text>
            <text x={1030} y={312} textAnchor="middle" fontSize={20} fontWeight={700} fill={C.text} fontFamily={MONO}>
              32–48 in-flight cap
            </text>

            <line x1={1200} y1={286} x2={1355} y2={286} stroke={CK.ok} strokeWidth={4} />
            <PacketStream pts={[[1200, 286], [1355, 286]]} travel={rt * 140} gap={52} size={11} color={CK.ok} />
            <text x={1277} y={260} textAnchor="middle" fontSize={18} fontWeight={700} fill={C.muted} fontFamily={MONO}>
              PCIe Gen5 ×4
            </text>

            <rect x={1360} y={230} width={425} height={112} rx={14} fill="#14232c" stroke={CK.card} strokeWidth={2.2} />
            <text x={1572} y={276} textAnchor="middle" fontSize={24} fontWeight={800} fill={CK.card} fontFamily={FONT}>
              CN106 DPU / NIC
            </text>
            <text x={1572} y={312} textAnchor="middle" fontSize={19} fill={C.muted} fontFamily={FONT}>
              Clean inbound pacing · No collapse
            </text>

            {/* Middle Horizontal Safe-Zone Comparison (y = 430..700) */}
            {/* Left: Host-Only Credits Safe Zone */}
            <rect x={90} y={435} width={885} height={260} rx={20} fill="#0f2324" stroke={CK.ok} strokeWidth={2.5} />
            <rect x={90} y={435} width={885} height={58} rx={20} fill={`${CK.ok}20`} />
            <text x={125} y={472} fontSize={23} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              HOST-ONLY CREDITS · ≤ 2 µs SAFE ZONE
            </text>

            {/* 1C & 4C Pills */}
            <rect x={130} y={525} width={385} height={132} rx={16} fill="#132c2d" stroke={CK.ok} strokeWidth={2} />
            <text x={322} y={576} textAnchor="middle" fontSize={44} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              ≤ 13.5 M/s
            </text>
            <text x={322} y={626} textAnchor="middle" fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
              1 Core · ≤ 2 µs p99
            </text>

            <rect x={550} y={525} width={385} height={132} rx={16} fill="#132c2d" stroke={CK.ok} strokeWidth={2} />
            <text x={742} y={576} textAnchor="middle" fontSize={44} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              ≤ 10 M/s
            </text>
            <text x={742} y={626} textAnchor="middle" fontSize={22} fontWeight={700} fill={C.text} fontFamily={FONT}>
              4 Cores · ≤ 2 µs p99
            </text>

            {/* Transition Arrow */}
            <line x1={985} y1={565} x2={1120} y2={565} stroke={CK.cxl} strokeWidth={5} />
            <polygon points="1132,565 1110,552 1110,578" fill={CK.cxl} />
            <text x={1055} y={536} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              Faster
            </text>
            <text x={1055} y={600} textAnchor="middle" fontSize={19} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              @ ≤ 2 µs
            </text>

            {/* Right: On-Demand Card Backoff */}
            <rect x={1140} y={435} width={690} height={260} rx={20} fill="#211d16" stroke={CK.cxl} strokeWidth={2.5} />
            <rect x={1140} y={435} width={690} height={58} rx={20} fill={`${CK.cxl}22`} />
            <text x={1175} y={472} fontSize={23} fontWeight={800} fill={CK.cxl} fontFamily={MONO}>
              FASTER @ ≤ 2 µs · ON-DEMAND CARD BACKOFF
            </text>
            <text x={1175} y={554} fontSize={27} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Yield empty polling after busy bursts
            </text>
            <text x={1175} y={602} fontSize={22} fill={C.muted} fontFamily={FONT}>
              Lets inbound writes drain through the NIC bridge
            </text>
            <text x={1175} y={638} fontSize={22} fill={C.muted} fontFamily={FONT}>
              without head-on polling collisions.
            </text>
          </g>
        )}
      </Stage>

      {/* ==================== HTML OVERLAYS (OUTSIDE <Stage>) ==================== */}

      {/* Cue 0: QuestionBanner + Anchor Pills + Orange ? Card */}
      {op0 > 0.01 && (
        <>
          <QuestionBanner
            num="QUESTION 3"
            question="What breaks at high concurrency on a real DPU — and how do we tame tail latency?"
            sub="Idle RTT is 0.9 µs and raw push hits 175 M/s — does that hold under heavy concurrent load?"
            color={CK.card}
            y={165}
            opacity={op0}
            dy={lerp(20, 0, prog(t, T[0], 0.5))}
          />

          <HeroStat
            x={145}
            y={435}
            w={480}
            h={140}
            value="0.9 µs"
            label="Idle Round-Trip Latency"
            color={CK.ok}
            opacity={op0}
            dy={lerp(20, 0, prog(t, T[0] + 0.2, 0.5))}
          />
          <HeroStat
            x={145}
            y={600}
            w={480}
            h={140}
            value="175 M/s"
            label="Raw 1-Core Direct Push"
            color={CK.ok}
            opacity={op0}
            dy={lerp(20, 0, prog(t, T[0] + 0.35, 0.5))}
          />

          <div
            style={{
              position: "absolute",
              left: 1045,
              top: 445,
              width: 720,
              height: 280,
              opacity: op0,
              transform: `translateY(${lerp(20, 0, prog(t, T[0] + 0.45, 0.5))}px)`,
              background: "linear-gradient(150deg, rgba(36,24,18,0.96), rgba(16,18,32,0.95))",
              border: `2.5px solid ${CK.card}`,
              borderRadius: 22,
              padding: "28px 34px",
              boxSizing: "border-box",
              display: "flex",
              alignItems: "center",
              gap: 28,
              boxShadow: "0 18px 44px rgba(0,0,0,0.45)",
            }}
          >
            <div
              style={{
                width: 112,
                height: 112,
                borderRadius: 24,
                background: `${CK.card}24`,
                border: `2.5px solid ${CK.card}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 68,
                fontWeight: 800,
                color: CK.card,
                fontFamily: MONO,
                flexShrink: 0,
              }}
            >
              ?
            </div>
            <div>
              <div style={{ fontSize: 32, fontWeight: 800, color: C.text, fontFamily: FONT, lineHeight: 1.2 }}>
                Heavy Open-Loop Concurrency
              </div>
              <div style={{ fontSize: 22, color: C.muted, marginTop: 12, fontFamily: FONT, lineHeight: 1.4 }}>
                Multi-core bursts · Unpaced arrivals · Real CN106 DPU silicon
              </div>
            </div>
          </div>
        </>
      )}

      {/* Cue 3 Right-Side Collapse Stat */}
      {op3Only > 0.01 && (
        <>
          <HeroStat
            x={1240}
            y={235}
            w={590}
            h={245}
            value="44–51 µs"
            label="Open-Loop p99 Tail Latency"
            sub="at 16–30 M/s unpaced load"
            color={CK.trap}
            opacity={op3Only}
            dy={lerp(20, 0, prog(t, T[3], 0.5))}
          />
          <div
            style={{
              position: "absolute",
              left: 1240,
              top: 520,
              width: 590,
              opacity: op3Only,
              background: "rgba(32,16,26,0.92)",
              border: `1.5px solid ${CK.trap}88`,
              borderLeft: `6px solid ${CK.trap}`,
              borderRadius: 16,
              padding: "22px 26px",
              boxSizing: "border-box",
            }}
          >
            <div style={{ fontSize: 24, fontWeight: 800, color: CK.trap, fontFamily: FONT }}>
              Queueing Collapse Before PCIe
            </div>
            <div style={{ fontSize: 21, color: C.muted, marginTop: 10, lineHeight: 1.4, fontFamily: FONT }}>
              Backpressure from the NIC bridge fills 48–248 host memory queue entries, causing tail latency to explode.
            </div>
          </div>
        </>
      )}

      {/* Cue 4 Right-Side Credit Gate Wins */}
      {op4Only > 0.01 && (
        <>
          <HeroStat
            x={1240}
            y={215}
            w={590}
            h={240}
            value="6–64×"
            label="Lower p99 Tail Latency"
            sub="44.5 µs → 7.0 µs (Poisson)"
            color={CK.ok}
            opacity={op4Only}
            dy={lerp(20, 0, prog(t, T[4] + 0.15, 0.5))}
          />
          <HeroStat
            x={1240}
            y={490}
            w={590}
            h={235}
            value="+43%"
            label="4-Core Steady Throughput"
            sub="Eliminates multi-core collapse"
            color={CK.push}
            opacity={op4Only}
            dy={lerp(24, 0, prog(t, T[4] + 0.3, 0.5))}
          />
        </>
      )}

      {/* Cue 5 Bottom Honest Boundary Bar */}
      {op5 > 0.01 && (
        <BoundaryBar
          x={80}
          y={758}
          w={1760}
          badge="HONEST BOUNDARY"
          text="Host-only credits guarantee ≤ 2 µs up to 13.5 M/s (1C) / 10 M/s (4C); pushing faster requires on-demand NIC backoff after busy bursts."
          color={CK.cxl}
          opacity={op5}
          dy={lerp(16, 0, prog(t, T[5] + 0.25, 0.5))}
        />
      )}
    </>
  );
}

export const C04: SceneDef = {
  id: "C04",
  title: "3 \u00b7 Taming Tail Latency",
  stage: 2,
  source: "High-Concurrency Open-Loop Traffic \u00b7 Host In-Flight Credit Gate",
  dur: DUR,
  cues,
  C: Body,
};
