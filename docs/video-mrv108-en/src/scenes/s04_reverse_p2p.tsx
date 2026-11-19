import {
  C,
  Cue,
  FONT,
  K,
  MONO,
  PacketStream,
  SceneDef,
  Stage,
  countUp,
  fade,
  prog,
  useRealT,
  useT,
} from "../lib";
import { HwBox, PathTag, PolyArrow, StatusBadge, TimeBlock } from "./dsa";

// <cues>
const cues: Cue[] = [
  [0.60, 14.12, "Breaking down `W = 1 NOP (1.98 µs)` reveals a huge floor: because completions sit in host RAM, each 32 B DPI sync read-back costs **1.21 µs** alone!", "Breaking down single-op latency reveals a glaring floor. Because completions live in host memory, every thirty-two-byte synchronous D P I read-back costs one point two one microseconds all by itself."], // 0
  [14.47, 33.21, "That **1.21 µs** is **61%** of `W = 1 NOP`; any `4 KiB` copy missing Poll #1 waits a full extra round, causing **1,119 ns** jitter and a **3.13 µs** loop.", "That one point two one microseconds is sixty-one percent of N O P latency. Worse, any four-kilobyte copy that barely misses the first poll waits a whole extra round, creating one thousand one hundred nineteen nanoseconds of quantization jitter and a three point one three microsecond loop."], // 1
  [33.56, 47.20, "Single-variable change: keep pure DPI submit, write-only portal, and `CC = 0` fixed—only move `completion_addr` from Host Memzone to **Card PF BAR4 slot 15**.", "To eliminate read-back cleanly, we change just one variable: keeping pure D P I submit, write-only portal, and C C zero fixed, we point the completion address at card P F B A R four slot fifteen."], // 2
  [47.55, 60.59, "Under a safety lease, the sleeping host `mmap`s `BAR4` slot 15; `io_remap_pfn_range` pre-populates all **1,024** `VM_PFNMAP` PTEs (`HPA 0x223ff3c00000`).", "Protected by a safety lease handshake, the sleeping host process memory-maps B A R four slot fifteen, and the kernel synchronously pre-populates all one thousand twenty-four four-kilobyte page table entries."], // 3
  [60.94, 81.36, "`dsa2` hits `dmar6` with zero page faults and pushes 32 B completions via **reverse PCIe P2P** straight into card **L3 (TAD)**; the core polls locally via `LDARB` (`~1 ns` clear).", "D S A two hits d m a r six with zero page faults and pushes thirty-two-byte completions via reverse P C I e P 2 P straight into card L 3 cache. The card core polls locally with L D A R B and clears in about one nanosecond, eliminating D P I read-backs completely."], // 4
  [81.71, 102.63, "All **102** runs zero-error: `W = 1` loops drop to **1.27 µs** (`NOP`, `-35.1%`) and **2.17 µs** (`4 KiB`, `-30.6%`), jitter falls to **163 ns**, and `W = 96 NOP` hits **23.01 M/s**!", "Across one hundred two zero-error runs, single-in-flight loops drop to one point two seven microseconds for N O P and two point one seven microseconds for four-kilobyte copies, tail jitter plunges to one hundred sixty-three nanoseconds, and high-concurrency N O P surges to twenty-three point zero one million ops per second."], // 5
];
const DUR = 104.03; // <dur>
// </cues>

function Body() {
  const t = useT();
  const rt = useRealT();
  const T = cues.map((c) => c[0]);

  // Beat opacities (strictly crossfaded, zero overlap)
  const opB01 = fade(t, T[0], T[2] - 0.1, 0.5);
  const kBar0 = prog(t, T[0] + 0.3, 1.0);
  const opB1 = fade(t, T[1], T[2] - 0.1, 0.5);
  const kPoll1 = prog(t, T[1] + 0.2, 0.7);
  const kPoll2 = prog(t, T[1] + 1.0, 0.8);

  const opB2 = fade(t, T[2], T[3] - 0.1, 0.5);
  const kB2Arr = prog(t, T[2] + 0.5, 0.9);

  const opB3 = fade(t, T[3], T[4] - 0.1, 0.5);
  const kB3Step1 = prog(t, T[3] + 0.2, 0.7);
  const kB3Step2 = prog(t, T[3] + 1.0, 0.8);
  const kB3Step3 = prog(t, T[3] + 1.8, 0.8);

  const opB4 = fade(t, T[4], T[5] - 0.1, 0.5);
  const kB4Rev = prog(t, T[4] + 0.3, 1.0);
  const kB4Cross = prog(t, T[4] + 1.6, 0.6);

  const opB5 = prog(t, T[5], 0.6);
  const kB5Bars = prog(t, T[5] + 0.4, 1.1);

  // Beat 4 reverse P2P polyline: dsa2 -> dmar6 -> PCIe -> BAR4 slot 15 -> L3 TAD -> Card Core
  const revPts: [number, number][] = [
    [1480, 320],
    [1175, 320],
    [740, 320],
    [450, 320],
  ];
  const oldPollPts: [number, number][] = [
    [450, 620],
    [1480, 620],
  ];

  const metrics = [
    {
      title: "W = 1 NOP Loop (1/rate)",
      before: "1.96 µs",
      after: `${countUp(t, T[5] + 0.3, 1.1, 1.27, 2, 1.96)} µs`,
      delta: "-35.1%",
      beforeFrac: 1.0,
      afterFrac: 1.27 / 1.96,
      color: K.rev,
    },
    {
      title: "W = 1 4 KiB Loop (1/rate)",
      before: "3.13 µs",
      after: `${countUp(t, T[5] + 0.4, 1.1, 2.17, 2, 3.13)} µs`,
      delta: "-30.6%",
      beforeFrac: 1.0,
      afterFrac: 2.17 / 3.13,
      color: K.rev,
    },
    {
      title: "4 KiB Tail Jitter (p99 - p50)",
      before: "1,119 ns",
      after: `${countUp(t, T[5] + 0.5, 1.1, 163, 0, 1119)} ns`,
      delta: "-85.4%",
      beforeFrac: 1.0,
      afterFrac: 163 / 1119,
      color: C.yellow,
    },
    {
      title: "W = 96 NOP Throughput",
      before: "14.22 M/s",
      after: `${countUp(t, T[5] + 0.6, 1.1, 23.01, 2, 14.22)} M/s`,
      delta: "+61.9%",
      beforeFrac: 14.22 / 23.01,
      afterFrac: 1.0,
      color: C.cyan,
    },
  ];

  return (
    <>
      <Stage>
        {/* ==================== BEAT 0 & 1: Latency Floor & Polling Step ==================== */}
        {opB01 > 0.01 && (
          <g opacity={opB01}>
            {/* Top section: W=1 NOP (1.98 us) breakdown bar */}
            <rect x={70} y={145} width={1780} height={265} rx={16} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={106} y={190} fill={C.text} fontSize={26} fontWeight={700} fontFamily={FONT}>
              W = 1 NOP Latency Breakdown (Host Memzone Baseline · 1.98 µs total)
            </text>
            <text x={1814} y={190} textAnchor="end" fill={K.bubble} fontSize={24} fontWeight={700} fontFamily={MONO}>
              poll_only = 1,212 ns (61%)
            </text>

            {/* Stacked horizontal bar: x = 106..1814 (width 1708) */}
            {/* 0.77 us submit + DSA (39%) -> 666px; 1.21 us DPI read-back (61%) -> 1042px */}
            <TimeBlock
              x={106}
              y={220}
              w={666 * kBar0}
              h={96}
              label="Forward DPI Submit + DSA Exec"
              sub="~0.77 µs (39%)"
              color={K.fwd}
            />
            <TimeBlock
              x={106 + 666 * kBar0}
              y={220}
              w={1042 * kBar0}
              h={96}
              label="Card DPI 32 B Sync Read-Back (Host Memzone)"
              sub="1.21 µs (1,212 ns · 61% floor!)"
              color={K.bubble}
              hatch
            />

            {/* Dimension bracket underneath */}
            <line x1={106} y1={340} x2={1814} y2={340} stroke={C.dim} strokeWidth={2} />
            <line x1={106} y1={330} x2={106} y2={350} stroke={C.dim} strokeWidth={2} />
            <line x1={1814} y1={330} x2={1814} y2={350} stroke={C.dim} strokeWidth={2} />
            <text x={960} y={376} textAnchor="middle" fill={C.muted} fontSize={22} fontFamily={MONO}>
              0.00 µs ─────────────── 0.77 µs ─────────────────────────────── 1.98 µs (W=1 NOP p50)
            </text>

            {/* Bottom section (Beat 1): 4 KiB MEMMOVE Polling Quantization Step */}
            {opB1 > 0.01 && (
              <g opacity={opB1}>
                <rect x={70} y={440} width={1780} height={415} rx={16} fill={C.panel} stroke={K.bubble} strokeWidth={2} />
                <text x={106} y={486} fill={C.text} fontSize={25} fontWeight={700} fontFamily={FONT}>
                  4 KiB MEMMOVE Polling Quantization: Miss Poll #1 → Wait a Full Extra Round!
                </text>
                <text x={1814} y={486} textAnchor="end" fill={C.yellow} fontSize={23} fontWeight={700} fontFamily={MONO}>
                  p99 = 3.30 µs · Jitter = 1,119 ns
                </text>

                {/* Track 1: DSA execution (0..~1.5 us) */}
                <text x={106} y={556} fill={C.muted} fontSize={20} fontWeight={600} fontFamily={FONT}>
                  Host DSA Copy
                </text>
                <TimeBlock
                  x={290}
                  y={522}
                  w={620 * kPoll1}
                  h={58}
                  label="4 KiB MEMMOVE Completes"
                  color={K.dsa}
                />

                {/* Track 2: Card DPI Polling Rounds */}
                <text x={106} y={648} fill={C.muted} fontSize={20} fontWeight={600} fontFamily={FONT}>
                  Card DPI Poll
                </text>
                <TimeBlock
                  x={290}
                  y={608}
                  w={540 * kPoll1}
                  h={72}
                  label="Poll #1 (1.21 µs)"
                  sub="Not ready · Miss!"
                  color={C.orange}
                  dashed
                />
                <TimeBlock
                  x={842}
                  y={608}
                  w={540 * kPoll2}
                  h={72}
                  label="Poll #2 (+1.21 µs)"
                  sub="Next step hit → p99 = 3.30 µs"
                  color={K.bubble}
                  hatch
                />

                {/* Vertical miss marker */}
                {kPoll1 > 0.8 && (
                  <g opacity={prog(t, T[1] + 0.8, 0.4)}>
                    <line x1={830} y1={514} x2={830} y2={705} stroke={C.yellow} strokeWidth={3} strokeDasharray="8 6" />
                    <PathTag cx={830} cy={726} text="Missed Poll #1!" color={C.yellow} size={20} />
                  </g>
                )}

                {/* Right callout box */}
                <g opacity={kPoll2}>
                  <rect x={1425} y={530} width={385} height={280} rx={14} fill="#1b1420" stroke={K.bubble} strokeWidth={2} />
                  <text x={1450} y={576} fill={K.bubble} fontSize={24} fontWeight={800} fontFamily={FONT}>
                    Host Memzone Cost
                  </text>
                  <text x={1450} y={626} fill={C.text} fontSize={21} fontFamily={MONO}>
                    W=1 Loop 1/rate: 3.13 µs
                  </text>
                  <text x={1450} y={668} fill={C.text} fontSize={21} fontFamily={MONO}>
                    p50 / p99: 2.18 / 3.30 µs
                  </text>
                  <text x={1450} y={712} fill={C.yellow} fontSize={23} fontWeight={700} fontFamily={MONO}>
                    Tail Jitter: 1,119 ns
                  </text>
                  <text x={1450} y={760} fill={C.muted} fontSize={19} fontFamily={FONT}>
                    Root: Card PCIe read-back
                  </text>
                </g>

                {/* Bottom time axis */}
                <line x1={290} y1={785} x2={1382} y2={785} stroke={C.dim} strokeWidth={2} />
                <text x={290} y={818} fill={C.muted} fontSize={20} fontFamily={MONO}>0 µs</text>
                <text x={830} y={818} textAnchor="middle" fill={C.orange} fontSize={20} fontFamily={MONO}>1.21 µs</text>
                <text x={1382} y={818} textAnchor="end" fill={K.bubble} fontSize={20} fontFamily={MONO}>3.30 µs (p99)</text>
              </g>
            )}
          </g>
        )}

        {/* ==================== BEAT 2: Single-Variable Comparison ==================== */}
        {opB2 > 0.01 && (
          <g opacity={opB2}>
            {/* Fixed control invariants banner */}
            <rect x={70} y={142} width={1780} height={64} rx={12} fill="#111a2e" stroke={C.cyan} strokeWidth={2} />
            <text x={960} y={182} textAnchor="middle" fill={C.cyan} fontSize={21} fontWeight={700} fontFamily={MONO}>
              Single-Variable Control: Fixed Pure DPI (06:00.2) · VFIO_DMA_MAP_FLAG_WRITE · dsa2/wq2.0 · CC = 0
            </text>

            {/* TOP ROW: Control Group (Host Memzone) */}
            <rect x={70} y={232} width={1780} height={285} rx={16} fill={C.panel} stroke={K.hostComp} strokeWidth={2} />
            <text x={106} y={276} fill={K.hostComp} fontSize={26} fontWeight={800} fontFamily={FONT}>
              Control: Host Memzone (Old Path)
            </text>

            <HwBox x={110} y={295} w={300} h={155} title="CN106 DPI" sub="06:00.2 write desc" sub2="06:00.1 read comp" accent={K.card} />
            <HwBox x={770} y={295} w={300} h={155} title="Intel dsa2 / wq2.0" sub="Single-engine copy" sub2="CC = 0" accent={K.dsa} />
            <HwBox x={1410} y={295} w={390} h={155} title="Host Memzone" sub="completion_addr in Host RAM" sub2="Card 32 B DPI read-back (1.21 µs)" accent={K.bubble} dashed />

            <PolyArrow pts={[[410, 365], [770, 365]]} color={K.fwd} w={4} k={kB2Arr} />
            <PathTag cx={590} cy={338} text="64 B Atomic Submit" color={K.fwd} size={18} />
            <PolyArrow pts={[[1070, 365], [1410, 365]]} color={K.hostComp} w={4} k={kB2Arr} />
            <PathTag cx={1240} cy={338} text="32 B Comp Write" color={K.hostComp} size={18} />
            <PolyArrow pts={[[1605, 450], [1605, 488], [260, 488], [260, 450]]} color={K.bubble} w={4} dash="10 8" k={kB2Arr} />
            <PathTag cx={920} cy={488} text="Card DPI 32 B Sync Read-Back (1.21 µs Floor)" color={K.bubble} size={19} />

            {/* BOTTOM ROW: Experiment Group (Card BAR4 Slot 15) */}
            <rect x={70} y={548} width={1780} height={305} rx={16} fill="#0e1e1c" stroke={K.rev} strokeWidth={3} />
            <text x={106} y={592} fill={K.rev} fontSize={26} fontWeight={800} fontFamily={FONT}>
              Experiment: Card PF BAR4 Slot 15 (Only Variable Changed!)
            </text>

            <HwBox x={110} y={645} w={300} h={170} title="CN106 DPI" sub="06:00.2 write desc" sub2="Local LDARB poll" accent={K.card} active />
            <HwBox x={470} y={645} w={340} h={170} title="Card PF BAR4 Slot 15" sub="completion_addr points here" sub2="Direct to CN106 L3 (TAD)" accent={K.rev} active badge="Only Change" />
            <HwBox x={1160} y={645} w={300} h={170} title="Intel dsa2 / wq2.0" sub="Single-engine copy" sub2="CC = 0 (Unchanged)" accent={K.dsa} active />

            <PolyArrow pts={[[260, 645], [260, 614], [1310, 614], [1310, 645]]} color={K.fwd} w={4} k={kB2Arr} />
            <PathTag cx={960} cy={614} text="64 B Atomic Submit (Unchanged)" color={K.fwd} size={18} />
            <PolyArrow pts={[[1160, 756], [810, 756]]} color={K.rev} w={5} halo k={kB2Arr} />
            <PathTag cx={985} cy={792} text="Reverse P2P 32 B Completion" color={K.rev} size={19} />
            <PolyArrow pts={[[470, 756], [410, 756]]} color={K.rev} w={4} k={kB2Arr} />

            {/* Right highlight box */}
            <rect x={1510} y={626} width={300} height={180} rx={14} fill="#102924" stroke={K.rev} strokeWidth={2} />
            <text x={1660} y={678} textAnchor="middle" fill={K.rev} fontSize={24} fontWeight={800} fontFamily={FONT}>
              Single Variable
            </text>
            <text x={1660} y={722} textAnchor="middle" fill={C.text} fontSize={21} fontFamily={MONO}>
              completion_addr
            </text>
            <text x={1660} y={762} textAnchor="middle" fill={C.yellow} fontSize={20} fontWeight={700} fontFamily={FONT}>
              Host Mem → Card BAR4
            </text>
          </g>
        )}

        {/* ==================== BEAT 3: Lease Handshake + io_remap_pfn_range ==================== */}
        {opB3 > 0.01 && (
          <g opacity={opB3}>
            <rect x={70} y={145} width={1780} height={705} rx={16} fill={C.panel} stroke={C.line} strokeWidth={2} />
            <text x={106} y={194} fill={C.text} fontSize={25} fontWeight={800} fontFamily={FONT}>
              One-Time Control Plane Setup: Lease Handshake + 1,024 Pre-Populated VM_PFNMAP PTEs (0 Faults)
            </text>

            {/* Step 1: Card PF BAR4 Slot 15 */}
            <HwBox
              x={110}
              y={245}
              w={370}
              h={230}
              title="1. Card PF 0000:99:00.0"
              sub="BAR4 = 16 x 4 MiB slots"
              sub2="Slot 15 Lease · HPA 0x223ff3c00000"
              accent={K.card}
              active={kB3Step1 > 0.5}
              badge="Safety Lease"
            />

            {/* Step 2: Sleeping Host Holder Process */}
            <HwBox
              x={645}
              y={245}
              w={460}
              h={230}
              title="2. Sleeping Host mmap(BAR4 Slot 15)"
              sub="io_remap_pfn_range() pre-populates"
              sub2="1,024 x 4 KiB VM_PFNMAP PTEs"
              accent={C.cyan}
              active={kB3Step2 > 0.5}
              sleeping
            />

            {/* Step 3: dmar6 SVA Page Table */}
            <HwBox
              x={1275}
              y={245}
              w={535}
              h={230}
              title="3. IOMMU dmar6 (SVA PASID)"
              sub="Shares sleeping mm_struct PTEs"
              sub2="dsa2 hits BAR4 Slot 15 · 0 Faults!"
              accent={K.iommu6}
              active={kB3Step3 > 0.5}
              badge="0 Page Faults"
            />

            {/* Arrows between steps */}
            <PolyArrow pts={[[480, 360], [645, 360]]} color={K.card} w={4} k={kB3Step2} />
            <PathTag cx={562} cy={325} text="Lease Grant" color={K.card} size={18} />

            <PolyArrow pts={[[1105, 360], [1275, 360]]} color={K.iommu6} w={4} k={kB3Step3} />
            <PathTag cx={1190} cy={325} text="SVA Bind" color={K.iommu6} size={18} />

            {/* Bottom visual: 1,024 pre-populated PTEs strip */}
            <g opacity={kB3Step2}>
              <rect x={110} y={535} width={1700} height={270} rx={14} fill="#0e1526" stroke={K.iommu6} strokeWidth={2} />
              <text x={145} y={582} fill={C.purple} fontSize={23} fontWeight={700} fontFamily={FONT}>
                1,024 x 4 KiB VM_PFNMAP PTEs Pre-Populated by io_remap_pfn_range (4 MiB BAR4 Window)
              </text>
              <text x={1775} y={582} textAnchor="end" fill={K.rev} fontSize={22} fontWeight={700} fontFamily={MONO}>
                HPA: 0x223ff3c00000
              </text>

              {/* Grid of PTE cells */}
              {Array.from({ length: 16 }).map((_, i) => {
                const cellK = prog(t, T[3] + 1.0 + i * 0.06, 0.35);
                const cx = 145 + i * 102;
                return (
                  <g key={i} opacity={cellK}>
                    <rect
                      x={cx}
                      y={612}
                      width={92}
                      height={74}
                      rx={8}
                      fill={i === 15 ? "#14332a" : "#162038"}
                      stroke={i === 15 ? K.rev : C.purple}
                      strokeWidth={2}
                    />
                    <text x={cx + 46} y={644} textAnchor="middle" fill={C.text} fontSize={18} fontWeight={700} fontFamily={MONO}>
                      {i === 15 ? "PTE#1023" : `PTE#${i}`}
                    </text>
                    <text x={cx + 46} y={670} textAnchor="middle" fill={K.rev} fontSize={16} fontFamily={MONO}>
                      4 KiB
                    </text>
                  </g>
                );
              })}

              <text x={145} y={735} fill={C.muted} fontSize={20} fontFamily={FONT}>
                Why pre-populate? With <tspan fill={C.text} fontWeight={700}>block_on_fault = 0</tspan>, dsa2 errors on unmapped pages; pre-filling all 1,024 PTEs keeps host CPU asleep with 0 faults!
              </text>
              <text x={145} y={778} fill={K.rev} fontSize={20} fontFamily={MONO}>
                open("/dev/dsa/wq2.0") bind PASID → mmap(BAR4 slot 15) → sleep → 0 Host CPU Forwarding
              </text>
            </g>
          </g>
        )}

        {/* ==================== BEAT 4: Reverse PCIe P2P Datapath ==================== */}
        {opB4 > 0.01 && (
          <g opacity={opB4}>
            {/* Card Side */}
            <rect x={70} y={145} width={580} height={705} rx={18} fill="#141320" stroke={K.card} strokeWidth={2.5} />
            <text x={100} y={190} fill={K.card} fontSize={26} fontWeight={800} fontFamily={FONT}>
              Marvell CN106 DPU (Card)
            </text>

            {/* Host Side */}
            <rect x={1110} y={145} width={740} height={705} rx={18} fill="#11182c" stroke={K.host} strokeWidth={2.5} />
            <text x={1140} y={190} fill={K.host} fontSize={26} fontWeight={800} fontFamily={FONT}>
              Intel Xeon Host (saturn · NUMA 1)
            </text>

            {/* PCIe Gen5 x4 Center Bus */}
            <rect x={715} y={145} width={330} height={705} rx={16} fill="#0d1726" stroke={K.link} strokeWidth={2} strokeDasharray="10 6" />
            <text x={880} y={190} textAnchor="middle" fill={K.link} fontSize={24} fontWeight={800} fontFamily={MONO}>
              PCIe Gen5 x4
            </text>
            <text x={880} y={222} textAnchor="middle" fill={C.muted} fontSize={19} fontFamily={FONT}>
              Same-Socket Bi-Dir P2P
            </text>

            {/* Top Path: NEW Reverse P2P (Glowing Green) */}
            <HwBox x={1480} y={240} w={330} h={160} title="Intel dsa2 / wq2.0" sub="Pushes 32 B completion" sub2="Reverse P2P MWr" accent={K.dsa} active />
            <HwBox x={1145} y={240} w={270} h={160} title="IOMMU dmar6" sub="SVA PTE direct hit" sub2="0 Page Faults" accent={K.iommu6} active />
            <HwBox x={105} y={240} w={345} h={160} title="Card L3 (TAD) / BAR4" sub="32 B comp lands in L3" sub2="Bypasses card DRAM" accent={K.rev} active badge="Direct L3" />

            <PolyArrow pts={revPts} color={K.rev} w={6} halo k={kB4Rev} />
            {kB4Rev > 0.7 && (
              <PacketStream pts={revPts} travel={rt * 190} gap={140} size={14} color={K.rev} />
            )}
            <PathTag cx={880} cy={282} text="Reverse P2P · 32 B Completion" color={K.rev} size={19} />

            {/* Card Core Local Poll */}
            <HwBox x={105} y={445} w={510} h={145} title="CN106 lcore 3 Local Poll" sub="Local LDARB poll in L3 · ~1 ns clear" sub2="0 PCIe reads · 0 clear writes!" accent={K.rev} active />
            <PolyArrow pts={[[275, 400], [275, 445]]} color={K.rev} w={5} k={kB4Rev} />

            {/* Bottom Path: OLD DPI Read-back (Crossed out with Red X) */}
            <HwBox x={105} y={635} w={345} h={145} title="DPI 06:00.1 (FromHost)" sub="Old: 32 B sync read-back" sub2="Eliminated!" accent={K.bubble} dim />
            <HwBox x={1480} y={635} w={330} h={145} title="Host Memzone" sub="Old completion addr" sub2="No longer polled" accent={K.hostComp} dim />

            <PolyArrow pts={oldPollPts} color={K.bubble} w={4} dash="10 8" opacity={0.45} />
            {kB4Cross > 0.05 && (
              <g opacity={kB4Cross}>
                <StatusBadge cx={880} cy={620} ok={false} r={34} />
                <PathTag cx={880} cy={685} text="0 DPI Read-Backs · 0 Clear Writes (-1.21 µs!)" color={K.bubble} size={20} />
              </g>
            )}
          </g>
        )}

        {/* ==================== BEAT 5: Before / After 4-Metric Comparison ==================== */}
        {opB5 > 0.01 && (
          <g opacity={opB5}>
            {/* Top summary badge */}
            <rect x={70} y={142} width={1780} height={64} rx={12} fill="#0f2622" stroke={K.rev} strokeWidth={2.5} />
            <text x={960} y={183} textAnchor="middle" fill={K.rev} fontSize={25} fontWeight={800} fontFamily={FONT}>
              Reverse P2P Verified: 102 Runs · 0 Drops · 0 Mismatches · SWERROR = 0
            </text>

            {/* 4 Metric Cards (2x2 grid) */}
            {metrics.map((m, idx) => {
              const col = idx % 2;
              const row = Math.floor(idx / 2);
              const x = 70 + col * 905;
              const y = 232 + row * 315;
              const w = 875;
              const h = 290;
              const maxBarW = 450;
              return (
                <g key={m.title}>
                  <rect x={x} y={y} width={w} height={h} rx={16} fill={C.panel} stroke={m.color} strokeWidth={2.5} />
                  <text x={x + 32} y={y + 48} fill={C.text} fontSize={26} fontWeight={700} fontFamily={FONT}>
                    {m.title}
                  </text>
                  {/* Delta badge */}
                  <rect x={x + w - 165} y={y + 18} width={135} height={42} rx={10} fill="#102924" stroke={m.color} strokeWidth={2} />
                  <text x={x + w - 97} y={y + 46} textAnchor="middle" fill={m.color} fontSize={24} fontWeight={800} fontFamily={MONO}>
                    {m.delta}
                  </text>

                  {/* Before bar */}
                  <text x={x + 32} y={y + 114} fill={C.muted} fontSize={21} fontFamily={FONT}>
                    Host Memzone
                  </text>
                  <rect x={x + 205} y={y + 88} width={maxBarW * m.beforeFrac * kB5Bars} height={38} rx={8} fill={K.hostComp} opacity={0.55} />
                  <text x={x + 220 + maxBarW * m.beforeFrac * kB5Bars} y={y + 115} fill={C.muted} fontSize={22} fontWeight={700} fontFamily={MONO}>
                    {m.before}
                  </text>

                  {/* After bar */}
                  <text x={x + 32} y={y + 190} fill={m.color} fontSize={21} fontWeight={700} fontFamily={FONT}>
                    Card BAR4 P2P
                  </text>
                  <rect x={x + 205} y={y + 162} width={maxBarW * m.afterFrac * kB5Bars} height={44} rx={8} fill={m.color} />
                  <text x={x + 220 + maxBarW * m.afterFrac * kB5Bars} y={y + 193} fill={m.color} fontSize={28} fontWeight={800} fontFamily={MONO}>
                    {m.after}
                  </text>

                  {/* Bottom divider hint */}
                  <text x={x + 32} y={y + 256} fill={C.dim} fontSize={19} fontFamily={MONO}>
                    {idx === 2
                      ? "Eliminates 1.21 µs DPI polling quantization step"
                      : idx === 3
                        ? "43.5 ns/op · Reverse P2P throughput surge"
                        : "Single-in-flight symmetric loop 1 / rate(W=1)"}
                  </text>
                </g>
              );
            })}
          </g>
        )}
      </Stage>
    </>
  );
}

export const S04: SceneDef = {
  id: "S04",
  title: "Breakthrough 2: Reverse P2P to Card BAR4",
  stage: 3,
  source: "Single Variable: comp Host Memzone -> Card BAR4 Slot 15",
  dur: DUR,
  cues,
  C: Body,
};
