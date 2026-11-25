"""Single source of truth for the MmioLatencyBreakdown narration (English, edge-tts en-US-AndrewNeural).

Story spine (a detective story: each chapter cuts the black box once, checks the cut a second way, or exposes a trap):
  M01–M02 THE CASE (627 ns; a stopwatch for one load; one load = one TLP) ·
  M03–M04 FIRST CUT (CHA TOR + Little's law; endpoints as rulers) ·
  M05–M06 SECOND CUT (root-port endpoint; mesh and core clock sweeps) ·
  M07–M10 TRAPS (diluted clock and 2-tick grid; wake-up asymmetry; do-nothing pread; silent PAT downgrade) ·
  M11–M12 VERDICT (six stages, two proofs each; the method and DirectPush).

Every scene is a dict:
  id:     composition id (M01..M12; PcieDatapath keeps S01..S15, CcnicPaperStory C01..C11)
  file:   src/mmio/<file>.tsx
  title:  top-left title (<= 30 chars)
  stage:  index into the MmioLatencyBreakdown rail (0..4)
  source: plain-English provenance line under the title (<= 64 chars; no issue ids on screen)
  cues:   list of (caption, spoken_or_None, fact_src); fact_src points into data/FACTS_MMIO.md

Captions support ONLY **accent** and `code`. Keep them <= ~118 chars (two lines at 31 px).
Spoken text (checked with faster-whisper on 2026-11-25): NIC "nick", PCIe "P C I E", MMIO "M M I O", CHA "C H A",
TOR "T O R" (the bare word is heard as "Tior"), IIO / IRP / TLP / UFS / PMU / SDP / IOBN / PEM spelled with spaces,
M2PCIe "M two P C I E", BAR4 "bar four", CN106 "C N one oh six", DDR5 "DDR five" ("D D R five" is heard as "DD are"),
ZMM "Z M M", RDTSC "R D T S C" (ASR writes "our DTSC"), PAT as is, MOVDIR64B "move dir sixty-four B". Numbers as
words. Avoid "U P I" in speech (heard as "UP").
"""

STORY = """\
A detective story in five chapters. The case: one 8-byte uncached load from host to card memory takes 627 ns, far
more than the ~200 ns quoted for CXL memory. Where do the nanoseconds go?

1. **The case** (M01-M02). A stopwatch for one load (`lfence; rdtsc` bracket, 12.7 ns floor, 0.91 ns grid) and the
   first clue: width costs nothing (4-64 B = one TLP), splitting does (8 x 8 B = 5,038 ns; 4 x 16 B legacy SSE = 5 TLPs).
2. **First cut** (M03-M04). An unnamed CHA encoding counts every uncached read 1:1 down to IIO; the TOR holds
   the entry until the reply returns, so Little's law gives the time below the CHA (495.6 ns of 516.4 ns). The 32-core
   scan maps the mesh (10 ns), eight cores scale 8.0x (no queue). Endpoint rulers on the card (register, L3, DDR5)
   give +103-105 ns and +93-102 ns, and a causality check pins the card's L3 clock at 2 GHz.
3. **Second cut** (M05-M06). A read-only root-port endpoint (205.5 ns) passes a same-CHA gate and a control group;
   register minus root port = +312.1 ns. Mesh and core clock sweeps split the host side: core 10.5-10.9 ns, core-CHA
   mesh 11.2-12.8 ns, CHA-M2PCIe mesh 56.2-58.6 ns, 1 GHz IRP + IIO + root port 125.2 ns; the sweeps agree within
   0.73 ns.
4. **Traps** (M07-M10). The diluted IO clock (0.96-0.99 GHz reported, 1.00 GHz in the loop); the 2-tick grid tilt
   (closure 1.16 -> 0.73 ns); the dozing IIO (18-23 ns, warm difference +290.9 / +295.0 ns); kernel noise in the
   config-read ruler, washed out by a pread that does nothing (cross-link difference +221.8 ns); and the silent PAT
   downgrade of the write-combining mapping (4 TLPs; one 64 B zmm load is 3.95x faster).
5. **Verdict** (M11-M12). Six stages, each measured two ways. The method carries to any server; for our design it says
   never read across PCIe on the fast path: DirectPush uses posted writes both ways.
"""

SCENES = [
    dict(
        id="M01",
        file="M01Case",
        title="627 nanoseconds",
        stage=0,
        source="Xeon 6438M host ⇄ Marvell CN106 card · PCIe Gen5 ×4",
        cues=[
            (
                "One load instruction. Eight bytes of a network card's memory. **627 ns** until the core has them.",
                "One load instruction. Eight bytes of a network card's memory. Six hundred twenty-seven nanoseconds until the core has them.",
                "A3",
            ),
            (
                "CXL memory reads are quoted at **~170–250 ns** (industry figure, not measured here). Why is this read so slow?",
                "C X L memory reads are quoted at around two hundred nanoseconds. So why is this P C I E read so slow?",
                "A7",
            ),
            (
                "On the way it crosses about a dozen blocks on two chips. To software, it is one black box.",
                "On the way, it crosses about a dozen blocks on two chips. To software, it is one black box.",
                "B1",
            ),
            (
                "Our rules: no hardware changes, no kernel patches, no root shell. Just a stopwatch, the chips' counters, and six `chown`s.",
                "Our rules: no hardware changes, no kernel patches, no root shell. Just a stopwatch, the chips' own counters, and six file permissions.",
                "C1",
            ),
            (
                "Three rounds of measurement cut it into **six stages**, each checked two independent ways. Here is how.",
                "Three rounds of measurement cut it into six stages, each checked two independent ways. Here is how, and the traps along the way.",
                "E",
            ),
        ],
    ),
    dict(
        id="M02",
        file="M02Stopwatch",
        title="A stopwatch for one load",
        stage=0,
        source="45 runs × 100,000 reads · cpu40 3.9 GHz · mesh 2.2 GHz",
        cues=[
            (
                "First, a stopwatch for one load: `lfence; rdtsc` on both sides, so nothing starts early or finishes late.",
                "First, a stopwatch for one load: L fence and R D T S C on both sides, so nothing starts early or finishes late.",
                "A1",
            ),
            (
                "The empty bracket costs **12.7 ns**, subtracted from every read: 100,000 reads per run, every reply checked.",
                "The empty bracket costs twelve point seven nanoseconds, and we subtract it from every read, a hundred thousand reads per run.",
                "A1",
            ),
            (
                "The counter runs at 2.2 GHz and moves two ticks at a time: every reading lands on a **0.91 ns** grid.",
                "The counter moves two ticks at a time, so every reading lands on a zero point nine one nanosecond grid. Remember that grid.",
                "A1",
            ),
            (
                "Clue one: width costs nothing. 4 to 32 bytes all take **627 ns** and exactly **one** PCIe read packet, one TLP.",
                "Clue one: the width costs nothing. Four to thirty-two bytes all take six hundred twenty-seven nanoseconds, and exactly one P C I E read packet, one T L P.",
                "A3",
            ),
            (
                "A 64-byte `zmm` load is still one TLP. Eight 8-byte loads are eight round trips in a row: **5,038 ns**.",
                "A sixty-four byte Z M M load is still one T L P. Eight small loads cost eight round trips in a row: over five thousand nanoseconds.",
                "A3, A4",
            ),
            (
                "Four 16-byte loads sent **five** TLPs, and the card saw all five. Uncached loads never merge, and a replayed load is real.",
                "Four legacy sixteen-byte loads sent five T L Ps, and the card saw all five. Uncached loads never merge, and a replayed load is a real read too.",
                "A5",
            ),
        ],
    ),
    dict(
        id="M03",
        file="M03TorClock",
        title="A stopwatch inside the CPU",
        stage=1,
        source="249 runs · host CHA, M2PCIe, IRP, IIO counters, unmultiplexed",
        cues=[
            (
                "Inside the host chip, each uncached read is handled by one **CHA** and tracked in its table of requests, the **TOR**.",
                "Inside the host chip, each uncached read is handled by one caching agent, the C H A, and tracked in its table of requests, the T O R.",
                "B1",
            ),
            (
                "No public event counts these small reads. An unnamed encoding does, and it matches every counter below it **1 : 1**.",
                "No public event counts small reads like these. An unnamed encoding does, and it matches every counter below it, one for one.",
                "B1",
            ),
            (
                "The TOR keeps the entry until the reply returns. Little's law: occupancy ÷ reads = time spent below the CHA.",
                "That table keeps the entry until the reply returns. So Little's law turns two counters into a time: occupancy divided by reads.",
                "B2",
            ),
            (
                "Card register read: **516.4 ns**. Below the CHA: **495.6 ns**. Core and mesh outside it: only **21.8 ns**.",
                "Reading a card register takes five hundred sixteen nanoseconds. Four hundred ninety-six are below the C H A; the core and mesh take only twenty-two.",
                "B3",
            ),
            (
                "From all 32 cores, distance moves the total by **10 ns**. Where the TOR frees early, the time reappears outside it.",
                "From all thirty-two cores, distance moves the total by just ten nanoseconds. And where the table lets go early, the time reappears outside it.",
                "B4",
            ),
            (
                "Eight cores reading at once scale **8.0×**: nothing queues. And one core never overlaps two uncached reads.",
                "Eight cores reading at once go eight times faster, so nothing queues. And one core never overlaps two uncached reads.",
                "B5",
            ),
        ],
    ),
    dict(
        id="M04",
        file="M04Rulers",
        title="Rulers at the far end",
        stage=1,
        source="Same host path, three card endpoints · card TAD and DDR counters",
        cues=[
            (
                "Second trick: change only where the read lands. Same host path, three endpoints on the card; subtract, and the shared part cancels.",
                "Second trick: change only where the read lands. Same host path, three endpoints on the card. Subtract, and the shared part cancels.",
                "B6",
            ),
            (
                "A card register: **516.4 ns**. Card memory in its 48 MiB L3: **621.8 ns**. Flushed to DDR5: **715.5 ns**.",
                "A card register: five hundred sixteen nanoseconds. Card memory in its L three: six hundred twenty-two. Flushed to DDR five: seven hundred sixteen.",
                "A2, A6",
            ),
            (
                "So the BAR4 window, IOBN bridge and L3 add **+103.1–105.5 ns**; an L3 miss to DDR5 adds **+92.7–101.6 ns**.",
                "So reaching the L three adds about a hundred and four nanoseconds, and an L three miss adds ninety-three to a hundred and two.",
                "B6, C7",
            ),
            (
                "The card's counters agree: **1.00** L3 miss and **2.01** DDR5 bursts per read: one 128-byte line, two 64-byte bursts.",
                "The card's counters agree: one L three miss per read, refilled by a pair of sixty-four byte DDR five bursts.",
                "A6",
            ),
            (
                "Causality check: at 1 GHz the card's miss would last **161.6 ns**, more than the host's whole **+101.6 ns**. So it runs at 2 GHz.",
                "A causality check pins the card's clock. At one gigahertz the miss alone would take longer than the host's whole delay. So it must run at two gigahertz.",
                "B7",
            ),
            (
                "Four pieces now. But the biggest, **~494 ns** from the host's CHA to the card, is still dark.",
                "Four pieces. But the biggest, about four hundred ninety-four nanoseconds from the host's C H A to the card, is still dark.",
                "B3",
            ),
        ],
    ),
    dict(
        id="M05",
        file="M05RootPort",
        title="An endpoint inside the host",
        stage=2,
        source="Root port 98:01.0 BAR0, read-only mmap · 408 runs, 0 errors",
        cues=[
            (
                "Round two: six `chown`s, still no root shell. Now we may map the root port's own registers, read-only, one address.",
                "Round two: six file permissions, still no root shell. Now we may map the root port's own registers, read only, one address.",
                "C1",
            ),
            (
                "A read to the root port turns around inside it: **205.5 ns**. Same core, mesh and IIO, but no link and no card.",
                "Reading the root port, the request turns around inside it after two hundred five nanoseconds. Same core and mesh, same I R P and I I O, but no link and no card.",
                "C2",
            ),
            (
                "A reviewer's warning: matching counters don't prove a shared path. A config read that never leaves the port counts the same.",
                "But a reviewer warned us: matching counters do not prove a shared path. A config read that never leaves the root port counts the same.",
                "C3",
            ),
            (
                "So a gate first: on the same CHA, root port and card memory spend the same time outside the TOR: **21.2** vs **22.3 ns**.",
                "So it had to pass a gate: on the same C H A, it must spend the same time outside the T O R as a real card read. It does.",
                "C4",
            ),
            (
                "Then a control group: many cores on one address stall at 1 / TOR on **both** paths. The limit is the CHA's, not the port's.",
                "And a control group: many cores on one address stall on the card register just like on the root port. That limit comes from the C H A.",
                "C5",
            ),
            (
                "Card register minus root port: **+312.1 ns**. That is the host's PCIe layers, the link, PEM0, and the path to the register.",
                "Card register minus root port: three hundred twelve nanoseconds. The host's P C I E layers, the link, P E M zero, and the path to the register.",
                "C6",
            ),
        ],
    ),
    dict(
        id="M06",
        file="M06ClockSweep",
        title="Sweep the clocks",
        stage=2,
        source="Mesh 0.8–2.2 GHz and core 1.5–3.9 GHz sweeps · 4 endpoints",
        cues=[
            (
                "Third trick: change one clock and watch what stretches. A stage on that clock takes **cycles ÷ frequency**.",
                "Third trick: change one clock and watch what stretches. A stage on that clock takes a fixed number of cycles, divided by the frequency.",
                "C7",
            ),
            (
                "Sweep the mesh, 0.8 to 2.2 GHz. The CHA and M2PCIe₁ follow. IRP₁ and IIO₁ don't: they run on their own **1 GHz** clock.",
                "Sweep the mesh, and the C H A and M two P C I E follow it. I R P and I I O do not: they run on their own one gigahertz clock.",
                "C7, D1",
            ),
            (
                "TOR time is a straight line in 1/f. Slope: **56.2–58.6 ns** of mesh. Intercept: **125.2 ns** for IRP₁, IIO₁ and the root port.",
                "Plot it against one over f, and it is a straight line: fifty-six to fifty-nine nanoseconds of mesh, plus a fixed hundred twenty-five for I R P, I I O and the root port.",
                "C7",
            ),
            (
                "Then sweep the core, 1.5 to 3.9 GHz. Its own share outside the TOR: **41–43 cycles**, or **10.5–10.9 ns**.",
                "Then sweep the core, from one point five to three point nine gigahertz. Its own share is forty-one to forty-three cycles: ten and a half nanoseconds.",
                "C8",
            ),
            (
                "Cross-check: what one sweep calls fixed, the other sees as slope. Core-to-CHA mesh: **11.2–12.8 ns**. Mismatch: **≤ 0.73 ns**.",
                "Cross-check: what one sweep calls fixed, the other must see as slope. The core to C H A mesh is eleven to thirteen nanoseconds, and they agree within zero point seven three.",
                "C8, D2",
            ),
            (
                "Two clocks, two sweeps, each stage measured twice. The host side is now four clean pieces.",
                "Two clocks, two sweeps, every stage measured twice: the host side is four clean pieces. But getting here meant dodging five traps.",
                "C8",
            ),
        ],
    ),
    dict(
        id="M07",
        file="M07ClockGrid",
        title="Traps: the clock and the grid",
        stage=3,
        source="IO clock from in-loop cycles ÷ wall time · floor_mean basis",
        cues=[
            (
                "Trap one: the IIO clock first read **0.96–0.99 GHz**, lower in short runs than in long ones. Real clocks don't do that.",
                "Trap one. The clock shared by I R P and I I O first read zero point nine six to zero point nine nine gigahertz, lower in short runs than in long ones. Real clocks don't do that.",
                "D1",
            ),
            (
                "After each run, the harness read 65 other counters first: **260–500 µs** with no reads in flight. The idle clock gated down.",
                "The culprit was our harness: after each run it read sixty-five other counters first, with no reads in flight, and the idle clock gated down.",
                "D1",
            ),
            (
                "Count cycles inside the timed loop only, and the clock is flat: **1.00 GHz** at every endpoint and every sweep point.",
                "Count only the cycles inside the timed loop, and the clock is flat: one point zero zero gigahertz, everywhere.",
                "D1",
            ),
            (
                "Trap two: the 0.91 ns grid. A grid-snapped median floor, taken from averaged reads, tilted the core fit by **~0.9 ns**.",
                "Trap two is the grid. We subtracted a grid-snapped median from averaged reads, and across core clocks that tilted the fit by almost a nanosecond.",
                "D2",
            ),
            (
                "Subtract a mean from a mean, and the two sweeps agree even better: worst mismatch **1.16 → 0.73 ns**.",
                "Subtract a mean from a mean instead, and the worst mismatch between the sweeps drops from one point one six to zero point seven three nanoseconds.",
                "D2",
            ),
        ],
    ),
    dict(
        id="M08",
        file="M08WakeUp",
        title="Trap: the IIO dozes",
        stage=3,
        source="Interference runs: one core reads the root port, cpu40 times",
        cues=[
            (
                "Trap three came from an interference test: one core keeps reading the root port, while another times the card register.",
                "Trap three came from an interference test: one core keeps reading the root port, while another times the card register.",
                "C10",
            ),
            (
                "Root-port reads never leave the host chip, yet the card read got **22.7 ns faster**. Someone else's traffic made us faster?",
                "Root-port reads never leave the host chip, yet the card read got twenty-two point seven nanoseconds faster. Someone else's traffic made us faster?",
                "C10",
            ),
            (
                "Alone, a card read starts only every ~541 ns, and the IIO idles long enough to doze. Root-port reads every ~233 ns keep it awake.",
                "Alone, a card read starts only every five hundred forty nanoseconds, and the I I O sits idle long enough to doze. Root-port reads keep it awake.",
                "C10",
            ),
            (
                "So the isolated difference of +312.1 ns includes **18–23 ns** of wake-up that only the card path pays.",
                "So the isolated three hundred twelve nanoseconds include eighteen to twenty-three of wake-up that only the card path pays.",
                "C11",
            ),
            (
                "Keep the IIO awake for both: card register minus root port is **+290.9 ns** median, **+295.0 ns** mean.",
                "Keep it awake for both, and the difference is two hundred ninety-one nanoseconds at the median, two hundred ninety-five on average.",
                "C11",
            ),
        ],
    ),
    dict(
        id="M09",
        file="M09EmptyRead",
        title="Trap: the kernel in the ruler",
        stage=3,
        source="sysfs config reads at offset 0 vs 64 · 7 devices · IO counters",
        cues=[
            (
                "Trap four: round one sized the link with sysfs config reads, **+209 ns** card minus root port. But each read also runs kernel code.",
                "Trap four. Round one sized the link with config reads through sysfs: two hundred nine nanoseconds. But each such read also runs kernel code.",
                "B8",
            ),
            (
                "The trick: `pread` at offset 64. Users may read only 64 bytes, so it returns nothing, after doing all the kernel's work.",
                "The fix is a read that does nothing. Users may read only sixty-four bytes, so a p read at offset sixty-four returns nothing, after all the kernel's work.",
                "C12",
            ),
            (
                "IO counters prove it never reaches hardware: **0**. Yet root port and VF pay **+17.3** and **+20.9 ns** in power management.",
                "The I O counters show it never reaches hardware. Yet the root port and the virtual function pay about twenty nanoseconds more, in power management.",
                "C12",
            ),
            (
                "Subtract the empty read from the real one, and a fake **19.1 ns** gap between the card's two functions vanishes.",
                "Subtract the empty read from the real one, and a fake nineteen nanosecond gap between the card's two functions disappears.",
                "C12",
            ),
            (
                "Device minus device cancels the shared locks too: across the link, **ΔH = +221.8 ns**, about 90 ns below the register path.",
                "Device minus device cancels the shared locks too: two hundred twenty-two nanoseconds across the link, ninety less than the deeper register path. It fits.",
                "C12",
            ),
        ],
    ),
    dict(
        id="M10",
        file="M10SilentWc",
        title="Trap: the silent downgrade",
        stage=3,
        source="resource4_wc vs UC · driver octep_main.c · Linux x86 PAT",
        cues=[
            (
                "Trap five: we mapped card memory write-combining, `resource4_wc`, hoping four 16-byte streaming loads would merge.",
                "Trap five. We mapped card memory as write-combining, hoping four sixteen-byte streaming loads would merge into one read.",
                "C13",
            ),
            (
                "They didn't: still **4 TLPs** and **2,484.6 ns**, exactly like uncached. No error, no warning.",
                "They did not: still four T L Ps, almost two and a half microseconds, exactly like uncached. No error, no warning.",
                "C13",
            ),
            (
                "Root cause: the driver `ioremap()`s all 64 MiB of BAR4 as uncached at load time, and Linux PAT silently matches it.",
                "The octeon E P driver maps all of bar four as uncached at load time, so Linux PAT quietly downgrades our write-combining mapping. Not even a log line.",
                "C13",
            ),
            (
                "What works today: one 64-byte `zmm` load, **1 TLP**, **628.2 ns**, **3.95×** faster than four small loads.",
                "What works today is one sixty-four byte Z M M load: a single T L P, six hundred twenty-eight nanoseconds, nearly four times faster.",
                "C13",
            ),
        ],
    ),
    dict(
        id="M11",
        file="M11Verdict",
        title="Six stages, two proofs each",
        stage=4,
        source="8-byte read into card L3 · means unless marked",
        cues=[
            (
                "Now the whole read, stage by stage: eight bytes from the card's L3.",
                "Now the whole read, stage by stage: eight bytes from the card's L three.",
                "E",
            ),
            (
                "Host chip, core to root port: **~205 ns**. More than half is the 1 GHz IRP₁ + IIO₁ and the root port: **125.2 ns**.",
                "Inside the host chip, core to root port: about two hundred five nanoseconds, and more than half of it is the one gigahertz I R P, I I O and root port.",
                "C2, C7",
            ),
            (
                "PCIe layers, link, PEM0 and the register path: **291–312 ns**, the biggest piece, depending on whether the IIO is awake.",
                "Then the P C I E layers, the link, P E M zero and the register path: two hundred ninety-one to three hundred twelve nanoseconds, the biggest piece.",
                "C6, C11",
            ),
            (
                "Inside the card: **+103–105 ns** through the BAR4 window to L3, and **+93–102 ns** more on a miss to DDR5.",
                "Inside the card: about a hundred and four more to reach the L three, and ninety-three to a hundred and two more for DDR five.",
                "B6, C7",
            ),
            (
                "Six stages, host core to card memory, each measured two independent ways, with nothing but software and the chips' own counters.",
                "Six stages, from the host core to the card's memory, each measured two independent ways, with nothing but software and the chips' own counters.",
                "E",
            ),
        ],
    ),
    dict(
        id="M12",
        file="M12Lessons",
        title="What it buys us",
        stage=4,
        source="Method summary · DirectPush measured on the same testbed",
        cues=[
            (
                "The method travels: endpoints as rulers, Little's law on counters, clock sweeps, and reads that do nothing.",
                "The method travels to any server: use endpoints as rulers, apply Little's law to counters, sweep the clocks, and cancel software with reads that do nothing.",
                "B2, C7, C12",
            ),
            (
                "And distrust every neat number: measure it twice, add a control group, know your timer's grid.",
                "And distrust every neat number: measure twice, add a control group, and know your timer's grid.",
                "C5, D2",
            ),
            (
                "For our design the lesson is blunt: a read across PCIe costs **~620 ns** while the core waits. Our fast path never reads across.",
                "For our design the lesson is blunt: a read across P C I E costs over six hundred nanoseconds while the core waits. So our fast path never reads across.",
                "B5, F1",
            ),
            (
                "DirectPush uses posted writes both ways: the host pushes with `MOVDIR64B`, the card pushes back, each side reads only its own cache.",
                "Direct Push sends posted writes both ways: the host pushes with move dir sixty-four B, the card pushes back, and each side only reads its own cache.",
                "F1",
            ),
            (
                "A full request and reply then takes **~0.9 µs**, and most of it is the same physical round trip we just took apart.",
                "A full request and reply then takes about nine tenths of a microsecond, most of it the same physical round trip we just took apart.",
                "F1",
            ),
            (
                "627 ns, six stages, each proved twice. Next time a device feels slow, open it up the same way.",
                "Six hundred twenty-seven nanoseconds, six stages, each proved twice. When a device feels slow, open it up the same way.",
                "E",
            ),
        ],
    ),
]
