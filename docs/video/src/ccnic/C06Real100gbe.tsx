import React from "react";
import {
  Arrow,
  C,
  Cue,
  FONT,
  MONO,
  PacketStream,
  Pt,
  SceneDef,
  Stage,
  countUp,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import {
  BoundaryBar,
  CK,
  HBarChart,
  HBarRow,
  HeroStat,
  QuestionBanner,
  TestbedTopo,
  topoGeom,
  topoRoutes,
} from "./shared";

// <cues>
const cues: Cue[] = [
  [0.60, 9.50, "**Question 5**: Do these gains survive a **real 100GbE cable**—and can **one single host core** actually saturate **100 Gbps** end-to-end?", "Question five: do these gains still matter over a real hundred gigabit Ethernet cable, and can strictly one host core saturate hundred gigabit line rate?"], // 0
  [9.85, 26.45, "Over **saturn → CN106 → 3 m DAC → mercury (ConnectX-5)**: end-to-end **64 B** RTT drops **10.21 → 5.96 µs** (**−4.25 µs / −41.6%**).", "Connecting saturn through C N one oh six and a three-meter hundred gigabit cable to mercury's ConnectX-five, real wire latency never hides the gain. Sixty-four byte round trip drops from ten point two one to five point nine six microseconds—saving forty-one point six percent."], // 1
  [26.80, 42.28, "Every size wins on wire: **256 B** saves **3.76 µs** (**−36%**), **4 KiB** saves **2.33 µs**, and `Mix-Geo` 64 B **p99** drops **68%** (**96.5 → 30.7 µs**).", "Every packet size wins over the wire: two hundred fifty-six bytes saves three point seven six microseconds, four kilobytes saves two point three three, and mixed sixty-four byte tail latency drops sixty-eight percent to thirty point seven microseconds."], // 2
  [42.63, 57.11, "Polling cheap local host rings ahead of costly Ethernet status checks trims another **−370 ns** end-to-end (**6.03 → 5.66 µs**, **0.94 µs** on-card).", "One clean card-side scheduling rule—polling cheap local host rings before expensive Ethernet port status checks—trims another three hundred seventy nanoseconds directly off end-to-end latency, restoring zero point nine four microseconds on-card."], // 3
  [57.46, 74.16, "**1-core 100GbE** test: into the NIC, **1 host core** pushes **161.4 Mpps**—above 100GbE's 64 B cap (**148.8 Mpps**)—vs optimal SDP's **50.5 Mpps**.", "Next, can one single host core drive hundred gigabit line rate? Across P C I express, one DirectPush core sustains one hundred sixty-one point four million packets per second—above hundred gigabit's sixty-four byte limit—while optimal S D P stalls at fifty point five million."], // 4
  [74.51, 92.23, "End-to-end into `mercury` PHY on **1 host core**: **64 B UDP** hits **99.76 Gbps**, **64 B RoCEv2** **99.68 Gbps** (**1.96×** SDP), **256 B** **99.86 Gbps**!", "From end to end into mercury's physical port on one host core, sixty-four byte U D P hits ninety-nine point seven six gigabits, sixty-four byte Rocky v two hits ninety-nine point six eight—nearly twice optimal S D P—and two hundred fifty-six bytes hits ninety-nine point eight six gigabits."], // 5
  [92.58, 112.00, "**Honest ceilings**: bare 64 B caps at **99.2 Mpps** (`66.7 Gbps`) on CN106's default single TX scheduler; **1–4 KiB** hits **94.0–96.5 Gbps**.", "Two honest hardware ceilings: bare sixty-four byte frames cap at ninety-nine point two million packets per second on this card's default single scheduler—hitting full hundred gigabits from one hundred six byte frames up—while one to four kilobyte D M A reaches ninety-four to ninety-six point five gigabits at the P C I express ceiling."], // 6
];
const DUR = 113.40; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const realT = useRealT();

  // Cue opacities (non-overlapping crossfades for sequential slots)
  const o0 = fade(t, T[0], T[1], 0.5);
  const oStrip = fade(t, T[1], T[3], 0.5);
  const o1 = fade(t, T[1], T[2] - 0.1, 0.45);
  const o2 = fade(t, T[2] + 0.1, T[3], 0.45);
  const o3 = fade(t, T[3], T[4], 0.5);
  const o4 = fade(t, T[4], T[5], 0.5);
  const o56 = fade(t, T[5], DUR + 2, 0.5);
  const c6K = prog(t, T[6], 0.65);
  const o5Note = fade(t, T[5] + 0.4, T[6] - 0.1, 0.45);
  const o6 = fade(t, T[6] + 0.12, DUR + 2, 0.45);

  // Cue 0 topology
  const g0 = topoGeom(0.86, 95, 290);
  const r0 = topoRoutes(g0);

  // Cue 1-2 compact topology strip routes
  const stripTx: Pt[] = [
    [390, 200],
    [1410, 200],
  ];
  const stripRx: Pt[] = [
    [1410, 224],
    [390, 224],
  ];

  // Cue 1 RTT chart
  const rttRows: HBarRow[] = [
    {
      label: "Stock SDP (100GbE)",
      value: 10.21,
      valueText: "10.21 µs",
      color: CK.sdp,
      k: prog(t, T[1] + 0.25, 1.0),
    },
    {
      label: "DirectPush (100GbE)",
      value: 5.96,
      valueText: "5.96 µs",
      color: CK.ok,
      k: prog(t, T[1] + 0.55, 1.0),
    },
  ];
  const rttPctStr = `−${countUp(t, T[1] + 0.3, 1.1, 41.6, 1)}%`;

  // Cue 4 1-Core Injection chart
  const injRows: HBarRow[] = [
    {
      label: "DirectPush (1 Host Core)",
      sub: "inline 64 B push",
      value: 161.4,
      valueText: "161.4 Mpps",
      color: CK.ok,
      k: prog(t, T[4] + 0.25, 1.0),
    },
    {
      label: "Optimal SDP (1 Host Core)",
      sub: "CPU-bottlenecked",
      value: 50.5,
      valueText: "50.5 Mpps",
      color: CK.sdp,
      k: prog(t, T[4] + 0.6, 1.0),
    },
  ];

  // Cue 5-6 100GbE Wire Throughput chart (morphs compact in Cue 6)
  const wireRows: HBarRow[] = [
    {
      label: "64 B UDP (1 Core DirectPush)",
      value: 99.76,
      valueText: "99.76 Gbps",
      color: CK.ok,
      k: prog(t, T[5] + 0.2, 0.95),
    },
    {
      label: "64 B RoCEv2 (1 Core DirectPush)",
      sub: "1.96× Optimal SDP",
      value: 99.68,
      valueText: "99.68 Gbps",
      color: CK.ok,
      k: prog(t, T[5] + 0.45, 0.95),
    },
    {
      label: "256 B RoCEv2 (1 Core DirectPush)",
      value: 99.86,
      valueText: "99.86 Gbps",
      color: CK.ok,
      k: prog(t, T[5] + 0.7, 0.95),
    },
    {
      label: "64 B RoCEv2 (1 Core Optimal SDP)",
      value: 50.94,
      valueText: "50.94 Gbps",
      color: CK.sdp,
      k: prog(t, T[5] + 0.95, 0.95),
    },
  ];

  const chart56TitleY = lerp(172, 166, c6K);
  const chart56Y = lerp(262, 238, c6K);
  const chart56BarH = lerp(64, 56, c6K);
  const chart56Gap = lerp(30, 22, c6K);

  // Cue 3 pulse for fast local poll
  const pollPulse = 0.65 + 0.35 * Math.sin(realT * 6.0);

  return (
    <>
      <Stage>
        {/* ================= CUE 0: Full 2-Host 100GbE Testbed ================= */}
        {o0 > 0.01 && (
          <g opacity={o0}>
            <TestbedTopo
              g={g0}
              showPeer={true}
              hl={{ wire: CK.peer, mercury: CK.peer, nix: CK.ok }}
            />
            <PacketStream pts={r0.wireTx} travel={realT * 130} gap={80} size={11} color={CK.ok} />
            <PacketStream pts={r0.wireRx} travel={realT * 130} gap={80} size={11} color={CK.peer} />
          </g>
        )}

        {/* ================= CUES 1-2: Compact 3-Node Topology Strip ================= */}
        {oStrip > 0.01 && (
          <g opacity={oStrip}>
            <rect
              x={90}
              y={144}
              width={1740}
              height={136}
              rx={18}
              fill="#10182b"
              stroke={C.line}
              strokeWidth={1.6}
            />

            {/* Connecting links & live round-trip packets */}
            <line x1={390} y1={200} x2={1410} y2={200} stroke={CK.ok} strokeWidth={4} opacity={0.65} />
            <line x1={390} y1={224} x2={1410} y2={224} stroke={CK.peer} strokeWidth={4} opacity={0.65} />
            <PacketStream pts={stripTx} travel={realT * 185} gap={140} size={10} color={CK.ok} />
            <PacketStream pts={stripRx} travel={realT * 185} gap={140} size={10} color={CK.peer} />

            {/* Node 1: saturn */}
            <rect x={120} y={168} width={270} height={88} rx={14} fill="#16213a" stroke={CK.host} strokeWidth={2.2} />
            <text x={255} y={204} textAnchor="middle" fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
              saturn
            </text>
            <text x={255} y={236} textAnchor="middle" fontSize={18} fill={CK.host} fontFamily={MONO}>
              Host Core
            </text>

            {/* Node 2: CN106 */}
            <rect x={535} y={168} width={275} height={88} rx={14} fill="#16213a" stroke={CK.card} strokeWidth={2.2} />
            <text x={672} y={204} textAnchor="middle" fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
              CN106
            </text>
            <text x={672} y={236} textAnchor="middle" fontSize={18} fill={CK.card} fontFamily={MONO}>
              DirectPush NIC
            </text>

            {/* Node 3: 3 m 100GbE DAC */}
            <rect x={950} y={168} width={320} height={88} rx={14} fill="#12272f" stroke={CK.peer} strokeWidth={2.2} />
            <text x={1110} y={204} textAnchor="middle" fontSize={24} fontWeight={800} fill={CK.peer} fontFamily={MONO}>
              3 m 100GbE DAC
            </text>
            <text x={1110} y={236} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={FONT}>
              live wire round trip
            </text>

            {/* Node 4: mercury */}
            <rect x={1410} y={168} width={390} height={88} rx={14} fill="#16213a" stroke={CK.peer} strokeWidth={2.2} />
            <text x={1605} y={204} textAnchor="middle" fontSize={25} fontWeight={800} fill={C.text} fontFamily={FONT}>
              mercury
            </text>
            <text x={1605} y={236} textAnchor="middle" fontSize={18} fill={CK.peer} fontFamily={MONO}>
              ConnectX-5 Peer
            </text>
          </g>
        )}

        {/* ================= CUE 1: 64 B Two-Host End-to-End RTT ================= */}
        {o1 > 0.01 && (
          <g opacity={o1}>
            <rect
              x={90}
              y={330}
              width={1220}
              height={460}
              rx={20}
              fill="#10182b"
              stroke={C.line}
              strokeWidth={1.8}
            />
            <text x={125} y={390} fontSize={30} fontWeight={800} fill={C.text} fontFamily={FONT}>
              64 B Two-Host End-to-End RTT over 100GbE
            </text>
            <HBarChart x={115} y={465} w={1165} labelW={310} barH={68} gap={44} max={12} rows={rttRows} />
            <text x={125} y={745} fontSize={22} fill={C.muted} fontFamily={FONT}>
              Real 3 m 100GbE cable latency never hides the host-NIC speedup
            </text>
          </g>
        )}

        {/* ================= CUE 3: Card Polling Rule Diagram ================= */}
        {o3 > 0.01 && (
          <g opacity={o3}>
            <text x={100} y={176} fontSize={32} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Card Polling Rule: Prioritize Cheap Local Host Ring
            </text>

            {/* ---- Top Row: Old 1:1 Loop ---- */}
            <rect x={100} y={215} width={1720} height={250} rx={20} fill="#10182b" stroke={`${CK.trap}66`} strokeWidth={2} />
            <text x={135} y={262} fontSize={25} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
              Old 1:1 Loop — Interleaving Expensive Port Checks
            </text>

            {/* Blocks */}
            <rect x={135} y={295} width={310} height={110} rx={14} fill={`${CK.push}20`} stroke={CK.push} strokeWidth={2.2} />
            <text x={290} y={342} textAnchor="middle" fontSize={22} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Check Local
            </text>
            <text x={290} y={374} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.push} fontFamily={FONT}>
              Host Ring
            </text>

            <Arrow x1={452} y1={350} x2={494} y2={350} color={C.muted} w={3.5} head={12} />

            {/* Expensive Ethernet Port Status Poll (red wait gap) */}
            <rect x={502} y={295} width={520} height={110} rx={14} fill="url(#hatchRed)" stroke={CK.trap} strokeWidth={2.6} />
            <rect x={532} y={316} width={460} height={68} rx={10} fill="#1a1220ee" />
            <text x={762} y={343} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.trap} fontFamily={FONT}>
              Expensive Ethernet Port Status Poll
            </text>
            <text x={762} y={371} textAnchor="middle" fontSize={18} fill={C.muted} fontFamily={MONO}>
              red wait gap stalls every packet
            </text>

            <Arrow x1={1030} y1={350} x2={1072} y2={350} color={C.muted} w={3.5} head={12} />

            <rect x={1080} y={295} width={260} height={110} rx={14} fill={`${CK.push}20`} stroke={CK.push} strokeWidth={2.2} />
            <text x={1210} y={342} textAnchor="middle" fontSize={22} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Check Local
            </text>
            <text x={1210} y={374} textAnchor="middle" fontSize={22} fontWeight={800} fill={CK.push} fontFamily={FONT}>
              Host Ring
            </text>

            {/* Right outcome box: 6.03 us */}
            <rect x={1395} y={285} width={390} height={130} rx={16} fill="#1e1526" stroke={CK.trap} strokeWidth={2.4} />
            <text x={1590} y={344} textAnchor="middle" fontSize={46} fontWeight={800} fill={CK.trap} fontFamily={MONO}>
              6.03 µs
            </text>
            <text x={1590} y={388} textAnchor="middle" fontSize={20} fill={C.muted} fontFamily={FONT}>
              end-to-end 64 B RTT
            </text>

            {/* ---- Bottom Row: Prioritize Cheap Local Ring ---- */}
            <rect x={100} y={505} width={1720} height={295} rx={20} fill="#10182b" stroke={`${CK.ok}88`} strokeWidth={2.2} />
            <text x={135} y={554} fontSize={25} fontWeight={800} fill={CK.ok} fontFamily={FONT}>
              Prioritize Cheap Local Ring — Fast Loop First
            </text>

            {/* Fast local ring blocks */}
            <rect
              x={135}
              y={590}
              width={355}
              height={110}
              rx={14}
              fill={`${CK.ok}22`}
              stroke={CK.ok}
              strokeWidth={2.6}
              opacity={pollPulse}
            />
            <text x={312} y={636} textAnchor="middle" fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Poll Local Host Ring
            </text>
            <text x={312} y={668} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              fast path first
            </text>

            <Arrow x1={498} y1={645} x2={540} y2={645} color={CK.ok} w={4} head={13} />

            <rect x={548} y={590} width={355} height={110} rx={14} fill={`${CK.ok}22`} stroke={CK.ok} strokeWidth={2.6} />
            <text x={725} y={636} textAnchor="middle" fontSize={23} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Poll Local Host Ring
            </text>
            <text x={725} y={668} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              zero port stall
            </text>

            <Arrow x1={911} y1={645} x2={952} y2={645} color={C.muted} w={3.5} head={12} />

            <rect x={960} y={604} width={170} height={82} rx={12} fill="#172036" stroke={C.line} strokeWidth={1.8} />
            <text x={1045} y={638} textAnchor="middle" fontSize={18} fontWeight={700} fill={C.muted} fontFamily={FONT}>
              Port Check
            </text>
            <text x={1045} y={664} textAnchor="middle" fontSize={16} fill={C.dim} fontFamily={MONO}>
              amortized
            </text>

            {/* Saved time callout */}
            <rect x={1156} y={604} width={206} height={82} rx={14} fill={`${CK.ok}22`} stroke={CK.ok} strokeWidth={2.2} />
            <text x={1259} y={638} textAnchor="middle" fontSize={28} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              −370 ns
            </text>
            <text x={1259} y={667} textAnchor="middle" fontSize={17} fill={C.text} fontFamily={FONT}>
              trimmed off wire
            </text>

            {/* Right outcome box: 5.66 us + 0.94 us on-card */}
            <rect x={1395} y={576} width={390} height={164} rx={16} fill="#11242b" stroke={CK.ok} strokeWidth={2.6} />
            <text x={1590} y={632} textAnchor="middle" fontSize={48} fontWeight={800} fill={CK.ok} fontFamily={MONO}>
              5.66 µs
            </text>
            <text x={1590} y={674} textAnchor="middle" fontSize={20} fontWeight={700} fill={C.text} fontFamily={MONO}>
              6.03 → 5.66 µs (−370 ns)
            </text>
            <text x={1590} y={712} textAnchor="middle" fontSize={20} fontWeight={800} fill={CK.push} fontFamily={MONO}>
              0.94 µs on-card restored
            </text>
          </g>
        )}

        {/* ================= CUE 4: 1 Host Core Injection Rate ================= */}
        {o4 > 0.01 && (
          <g opacity={o4}>
            <text x={100} y={182} fontSize={32} fontWeight={800} fill={C.text} fontFamily={FONT}>
              Host → NIC Packet Injection Rate (Strictly 1 Host Core)
            </text>
            <text x={100} y={226} fontSize={23} fill={C.muted} fontFamily={FONT}>
              Can a single host core feed packets faster than 100GbE&apos;s 64 B wire limit?
            </text>

            <HBarChart
              x={100}
              y={325}
              w={1720}
              labelW={420}
              barH={76}
              gap={44}
              max={180}
              rows={injRows}
              refLine={{ value: 148.8, label: "100GbE 64 B Wire Max (148.8 Mpps)", color: CK.cxl }}
            />
          </g>
        )}

        {/* ================= CUES 5-6: End-to-End 1-Core 100GbE Wire Throughput ================= */}
        {o56 > 0.01 && (
          <g opacity={o56}>
            <text x={100} y={chart56TitleY} fontSize={lerp(31, 27, c6K)} fontWeight={800} fill={C.text} fontFamily={FONT}>
              End-to-End 1-Core 100GbE Wire Throughput (Measured at mercury Physical Port)
            </text>
            <HBarChart
              x={100}
              y={chart56Y}
              w={1720}
              labelW={460}
              barH={chart56BarH}
              gap={chart56Gap}
              max={105}
              rows={wireRows}
              refLine={{ value: 100, label: "100 Gbps Line Rate", color: CK.ok }}
            />
          </g>
        )}
      </Stage>

      {/* ================= HTML OVERLAYS (strictly outside <Stage>) ================= */}

      {/* Cue 0 Question Banner */}
      {o0 > 0.01 && (
        <QuestionBanner
          num="QUESTION 5"
          question="Do these gains survive a real 100GbE cable — and can 1 host core hit 100 Gbps?"
          sub="Moving from single-host PCIe tests to a live two-host 100GbE link."
          color={CK.ok}
          y={155}
          opacity={o0}
          dy={lerp(18, 0, prog(t, T[0], 0.6))}
        />
      )}

      {/* Cue 1 Right HeroStat */}
      {o1 > 0.01 && (
        <HeroStat
          x={1355}
          y={395}
          w={475}
          h={330}
          value={rttPctStr}
          label="−4.25 µs saved end-to-end"
          sub="10.21 µs → 5.96 µs (64 B RTT)"
          color={CK.ok}
          opacity={o1}
          dy={lerp(18, 0, prog(t, T[1] + 0.2, 0.55))}
        />
      )}

      {/* Cue 2 Three-Card Grid: Every Packet Size Wins */}
      {o2 > 0.01 && (
        <>
          <div
            style={{
              position: "absolute",
              left: 95,
              top: 328,
              fontSize: 30,
              fontWeight: 800,
              color: C.text,
              opacity: o2,
            }}
          >
            Every Packet Size Wins Over Real 100GbE
          </div>

          {[
            {
              x: 95,
              badge: "256 B RTT",
              stat: "−3.76 µs",
              hl: "−36% faster",
              sub: "Inline direct push survives the real wire hop",
              color: CK.push,
              delay: 0.1,
            },
            {
              x: 688,
              badge: "4 KiB RTT (DMA)",
              stat: "−2.33 µs",
              hl: "vs strong SDP",
              sub: "Native NIC DMA keeps bulk transfers lean",
              color: CK.dma,
              delay: 0.25,
            },
            {
              x: 1280,
              badge: "MIX-GEO 64 B p99",
              stat: "−68%",
              hl: "96.5 → 30.7 µs",
              sub: "Tail latency stays tight under mixed load",
              color: CK.ok,
              delay: 0.4,
            },
          ].map((c, idx) => (
            <div
              key={idx}
              style={{
                position: "absolute",
                left: c.x,
                top: 400,
                width: 545,
                height: 390,
                opacity: o2,
                transform: `translateY(${lerp(20, 0, prog(t, T[2] + c.delay, 0.55))}px)`,
                background: "linear-gradient(155deg, rgba(20,27,46,0.96), rgba(11,16,32,0.94))",
                border: `2px solid ${c.color}aa`,
                borderTop: `8px solid ${c.color}`,
                borderRadius: 20,
                padding: "28px 32px",
                boxSizing: "border-box",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
              }}
            >
              <div>
                <span
                  style={{
                    fontSize: 19,
                    fontWeight: 800,
                    color: c.color,
                    fontFamily: MONO,
                    background: `${c.color}1e`,
                    border: `1.5px solid ${c.color}88`,
                    borderRadius: 8,
                    padding: "6px 14px",
                  }}
                >
                  {c.badge}
                </span>
                <div style={{ fontSize: 64, fontWeight: 800, color: c.color, fontFamily: MONO, marginTop: 32, lineHeight: 1.05 }}>
                  {c.stat}
                </div>
                <div style={{ fontSize: 28, fontWeight: 800, color: C.text, marginTop: 14 }}>
                  {c.hl}
                </div>
              </div>
              <div style={{ fontSize: 21, color: C.muted, lineHeight: 1.4 }}>
                {c.sub}
              </div>
            </div>
          ))}
        </>
      )}

      {/* Cue 4 Bottom Callout */}
      {o4 > 0.01 && (
        <BoundaryBar
          badge="1 CORE > 100GbE CAP"
          text="Strictly 1 host core pushes 161.4 Mpps — exceeding 100GbE's 148.8 Mpps 64 B wire cap (vs 50.5 Mpps for Optimal SDP)."
          color={CK.ok}
          x={100}
          y={660}
          w={1720}
          opacity={o4}
          dy={lerp(16, 0, prog(t, T[4] + 0.4, 0.55))}
        />
      )}

      {/* Cue 5 Bottom Callout */}
      {o5Note > 0.01 && (
        <BoundaryBar
          badge="1-CORE LINE RATE"
          text="1 host core saturates 100GbE into mercury PHY: 99.76 Gbps UDP, 99.68 Gbps RoCEv2 (1.96× SDP), 99.86 Gbps at 256 B."
          color={CK.ok}
          x={100}
          y={715}
          w={1720}
          opacity={o5Note}
        />
      )}

      {/* Cue 6 Two Ceiling Cards + BoundaryBar */}
      {o6 > 0.01 && (
        <>
          <div
            style={{
              position: "absolute",
              left: 100,
              top: 595,
              width: 840,
              height: 170,
              opacity: o6,
              transform: `translateY(${lerp(18, 0, prog(t, T[6] + 0.1, 0.55))}px)`,
              background: "linear-gradient(155deg, rgba(22,28,48,0.96), rgba(11,16,32,0.94))",
              border: `2px solid ${CK.cxl}99`,
              borderLeft: `8px solid ${CK.cxl}`,
              borderRadius: 18,
              padding: "16px 26px",
              boxSizing: "border-box",
            }}
          >
            <div style={{ fontSize: 20, fontWeight: 800, color: CK.cxl, fontFamily: MONO }}>
              CEILING 1 · BARE 64 B ETHERNET
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 14, marginTop: 8 }}>
              <span style={{ fontSize: 42, fontWeight: 800, color: C.text, fontFamily: MONO }}>99.2 Mpps</span>
              <span style={{ fontSize: 24, fontWeight: 700, color: CK.cxl, fontFamily: MONO }}>(66.7 Gbps)</span>
            </div>
            <div style={{ fontSize: 20, color: C.muted, marginTop: 8, lineHeight: 1.3 }}>
              Card&apos;s default single TX scheduler; hits full 100 Gbps from 106 B frames up.
            </div>
          </div>

          <div
            style={{
              position: "absolute",
              left: 980,
              top: 595,
              width: 840,
              height: 170,
              opacity: o6,
              transform: `translateY(${lerp(18, 0, prog(t, T[6] + 0.22, 0.55))}px)`,
              background: "linear-gradient(155deg, rgba(22,28,48,0.96), rgba(11,16,32,0.94))",
              border: `2px solid ${CK.dma}99`,
              borderLeft: `8px solid ${CK.dma}`,
              borderRadius: 18,
              padding: "16px 26px",
              boxSizing: "border-box",
            }}
          >
            <div style={{ fontSize: 20, fontWeight: 800, color: CK.dma, fontFamily: MONO }}>
              CEILING 2 · 1–4 KiB NATIVE DMA
            </div>
            <div style={{ fontSize: 42, fontWeight: 800, color: C.text, fontFamily: MONO, marginTop: 8 }}>
              94.0–96.5 Gbps
            </div>
            <div style={{ fontSize: 20, color: C.muted, marginTop: 8, lineHeight: 1.3 }}>
              Hits the host → card PCIe payload bandwidth ceiling.
            </div>
          </div>

          <BoundaryBar
            badge="HONEST CEILINGS"
            text="1 core saturates 100GbE from 106 B frames up; bare 64 B caps at 99.2 Mpps (66.7 Gbps) and 1–4 KiB DMA hits 94.0–96.5 Gbps."
            color={CK.cxl}
            x={100}
            y={782}
            w={1720}
            opacity={o6}
          />
        </>
      )}
    </>
  );
}

export const C06: SceneDef = {
  id: "C06",
  title: "5 \u00b7 Real 100GbE & 1-Core Line Rate",
  stage: 4,
  source: "Two-Host 100GbE: saturn \u21c4 CN106 \u21c4 3 m DAC \u21c4 mercury (ConnectX-5)",
  dur: DUR,
  cues,
  C: Body,
};
