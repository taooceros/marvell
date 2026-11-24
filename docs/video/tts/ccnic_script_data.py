"""Single source of truth for the CcnicPaperStory narration (English, edge-tts en-US-AndrewNeural).

Story spine (one argument, each chapter answers the question the previous one leaves open):
  C01 thesis · C02–C03 the debate (the PCIe interface tax; CC-NIC's bet and the real question) ·
  C04–C05 coherence is NOT NEEDED (the slow-MMIO premise is a fence artifact; host-NIC traffic is a
  hand-off, so push each message into the reader's cache) · C06 coherence is NOT FREE (retention tax,
  isolation) · C07–C09 what REAL HARDWARE adds (credits against bridge collapse; NIC DMA for bulk;
  NIC-held bursts) · C10 END TO END over real 100GbE · C11 takeaway and limits.

Every scene is a dict:
  id:     composition id (C01..C11; PcieDatapath keeps S01..S15)
  file:   src/ccnic/<file>.tsx
  title:  top-left title (<= 30 chars)
  stage:  index into the CcnicPaperStory rail (0..5), or None to hide the rail
  source: plain-English provenance line under the title (<= 64 chars; no issue ids on screen)
  cues:   list of (caption, spoken_or_None, fact_src); fact_src points into data/FACTS_CCNIC.md

Captions support ONLY **accent** and `code`. Keep them <= ~115 chars (two lines at 32 px).
Spoken text: NIC is "nick" ("nicks", "C C nick", "C X L nick"), PCIe is "P C I E", MOVDIR64B is "move dir sixty-four B";
other acronyms are spelled with spaces ("D M A", "I O M M U", "D D I O", "D RAM") and numbers as words. Avoid "nick's"
right before a consonant sound (heard as "next"): say "the card's" there.
"""

STORY = """\
One argument, told as a chain of questions (each chapter answers the question the previous one leaves open):

1. **The debate** (C02-C03). Today's descriptor-ring NICs make every message cross PCIe several times (2.1-3.8 us
   round trips). CC-NIC's fix is to put the NIC in the CPU's coherence domain. But its own control shows the old
   interface on a coherent link moves fewer packets than plain PCIe, and the real CXL NIC is fastest with mostly
   non-coherent pushes: was the win coherence, or the hand-over?
2. **Coherence is not needed** (C04-C05). The "MMIO is slow" premise is a fence-per-write artifact (8.1 -> 175.2 M/s on
   one core). Order is kept by the reader instead: one 64 B write per message, sequence numbers at both ends, slots read
   in sequence (0 torn in > 2.5 billion); coherence would not remove that check (CC-NIC still sets a ready flag last).
   Host-NIC traffic is a hand-off, so push each message into the reader's cache in both directions and poll
   locally: 897 ns round trip, 5.2x faster than the same card's descriptor interface, IOMMU on.
3. **Coherence is not free** (C06). Coherent NICs pay invalidations when the host reuses retained buffers (CC-NIC:
   -20%); push keeps nothing to invalidate (pool 64 KiB -> 128 MiB: -0.9%) and keeps the IOMMU.
4. **What real hardware adds** (C07-C09). A real NIC's inbound bridge can collapse under hard polling (~13 M/s) ->
   host credits (p99 44.5 -> 7.0 us); bulk payloads go by the NIC's own DMA (one fence per message; payload, then
   completion, kept in order by PCIe) with separate credit windows; the NIC,
   being the writer, keeps receive bursts off the host (-98.9% host DRAM traffic).
5. **End to end** (C10). Over a real 100GbE link the gain survives at every size and under load (64 B: 10.21 -> 5.96 us;
   p99 at 2 M/s: 26.3 -> 12.6 us), and one host core fills the link.
6. **Takeaway** (C11). The speedup is in the hand-over; what coherence is still for; related work; next step.
"""

SCENES = [
    # ------------------------------------------------------------------ C01 thesis
    dict(
        id="C01",
        file="C01Thesis",
        title="",
        stage=None,
        source="",
        cues=[
            (
                "**CC-NIC does not need cache coherence**: how should a host CPU and its network card talk?",
                "This is the story behind our paper, C C nick does not need cache coherence. It is about one question: how should a host C P U and its network card, the nick, talk to each other?",
                "F-THESIS",
            ),
            (
                "A recent line of work says fast NICs must join the CPU's **cache-coherence domain**. We argue the opposite.",
                "A recent line of work argues that fast nicks must join the C P U's cache coherence domain, over links like C X L. We argue the opposite.",
                "F-CCNIC-ABS / F-CXLNIC",
            ),
            (
                "The speedup comes from **how data is handed over**, not from coherence. Plain PCIe can already do that hand-over.",
                "The speedup comes from how data is handed from one side to the other, not from coherence itself. And plain P C I E, the link inside every server today, can already do that hand-over.",
                "F-THESIS",
            ),
            (
                "Everything is measured on real hardware: a Xeon host and a **Marvell CN106** NIC on PCIe Gen5, IOMMU on.",
                "Everything here is measured on real hardware: an Intel Xeon host and a Marvell C N one oh six nick over P C I E gen five, with the I O M M U enabled.",
                "F-TESTBED",
            ),
            (
                "The plan: the debate → why coherence is **not needed** → why it is **not free** → what real hardware adds → end to end.",
                "Here is the plan. First, the debate. Then why coherence is not needed, and why it is not free. Then what real hardware taught us, and finally the end-to-end result.",
                "—",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C02 the interface tax
    dict(
        id="C02",
        file="C02InterfaceTax",
        title="The interface tax",
        stage=0,
        source="Latency numbers: CC-NIC (ASPLOS '24) §5.3, CX6 and E810 NICs",
        cues=[
            (
                "Every request a server handles crosses the boundary between the **host CPU** and the **network card**.",
                "Every request a server handles crosses the boundary between the host C P U and the nick, once on the way in and once on the way out.",
                "—",
            ),
            (
                "Today that boundary is **PCIe**, and NICs talk across it through descriptor rings that live in host memory.",
                "Today that boundary is P C I E, and nicks talk across it through descriptor rings that live in host memory.",
                "F-PCIE-IF",
            ),
            (
                "To send, the CPU writes a descriptor and rings a **doorbell**. The NIC then **reads** the descriptor, then the payload.",
                "To send a message, the C P U writes a descriptor and rings a doorbell on the nick. The nick then reads the descriptor, and then the payload, back across the link.",
                "F-PCIE-IF",
            ),
            (
                "Each read is a **full round trip** over the link, so one message pays several crossings before it even leaves.",
                "Each of those reads is a full round trip over P C I E. So a single message pays for several crossings before it even leaves the machine.",
                "F-PCIE-IF",
            ),
            (
                "Receiving mirrors it: the NIC writes the packet, then a **completion**, and the CPU keeps checking for it.",
                "Receiving works the same way in reverse. The nick writes the packet into host memory, then a completion record, and the C P U keeps checking for it.",
                "F-PCIE-IF",
            ),
            (
                "A 64-byte round trip through today's NICs takes **2.1–3.8 µs**. For microsecond services, the interface is the bottleneck.",
                "The result: a sixty-four byte round trip through a modern nick takes two point one to three point eight microseconds. For microsecond-scale services, the interface itself is the bottleneck.",
                "F-CCNIC-LAT",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C03 CC-NIC's bet
    dict(
        id="C03",
        file="C03CoherentBet",
        title="CC-NIC's bet",
        stage=0,
        source="CC-NIC (ASPLOS '24) §4, §5.2–5.3 · CXL-NIC (MICRO '25) §7",
        cues=[
            (
                "**CC-NIC** (ASPLOS '24) proposed a fix: move the NIC inside the CPU's **cache-coherence domain**.",
                "In twenty twenty four, the C C nick paper proposed a fix: move the nick inside the C P U's cache coherence domain.",
                "F-CCNIC-ABS",
            ),
            (
                "Host and NIC then share cache lines, and coherence hardware moves each line to whoever touches it next.",
                "Host and nick would then share cache lines, and the coherence hardware moves each line to whichever side touches it next. Just loads and stores: no doorbells, and no D M A.",
                "F-CCNIC-ABS",
            ),
            (
                "Modeled on a two-socket Xeon, one socket playing the NIC: **490–650 ns**, about **77% lower** than PCIe NICs.",
                "They modeled it on a two-socket Xeon server, with one socket playing the nick, and measured four hundred ninety to six hundred fifty nanoseconds, about seventy-seven percent lower than P C I E nicks.",
                "F-CCNIC-EMU / F-CCNIC-LAT",
            ),
            (
                "Yet in their own control, the **old interface over the coherent link** moved fewer packets than plain PCIe.",
                "But one of their own experiments is telling. When they ran the old nick interface over the coherent link, unchanged, it delivered a lower packet rate than the same nick on plain P C I E.",
                "F-CCNIC-UNOPT",
            ),
            (
                "The win came with a **redesigned interface**: signals packed into the descriptors, rings placed with whoever writes them.",
                "In other words, the gain arrived together with a redesigned interface: signals packed into the descriptors themselves, and rings placed in the memory of whoever writes them.",
                "F-CCNIC-IF",
            ),
            (
                "A real coherent NIC (**CXL-NIC**, MICRO '25) was fastest using **mostly non-coherent pushes**: about **1.3 µs**.",
                "And when a later paper built a real coherent nick on a C X L F P G A, its fastest design relied mostly on non-coherent requests, pushing packets straight into the host cache, at about one point three microseconds.",
                "F-CXLNIC",
            ),
            (
                "So the real question: does the speedup come from **coherence**, or from **how data is handed over**?",
                "So the real question is this. Does the speedup come from coherence, or from redesigning how data is handed over? If it is the hand-over, we may not need coherence at all.",
                "F-THESIS",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C04 the fence artifact
    dict(
        id="C04",
        file="C04FenceArtifact",
        title="Is PCIe really slow?",
        stage=1,
        source="Measured: 1 host core · 64 B writes · batch 1 · PCIe Gen5 ×4",
        cues=[
            (
                "Both coherent-NIC papers start from one premise: CPU writes into device memory (**MMIO**) are slow.",
                "Both coherent nick papers start from the same premise: when the C P U writes directly into device memory, called M M I O, it is slow, and it only works with large batches.",
                "F-PREMISE",
            ),
            (
                "But that measurement put a **fence after every write**: the core must wait until each write drains.",
                "But that measurement placed a fence after every single write. A fence makes the core stop and wait until each write has fully drained before it may issue the next one.",
                "F-PREMISE",
            ),
            (
                "We reproduce it: one core, 64-byte writes, a fence after each → **8.1 million writes/s**.",
                "We can reproduce their number. One core writing sixty-four byte messages, with a fence after each one, reaches only eight point one million writes per second.",
                "F-FENCE",
            ),
            (
                "Drop the per-write fence and writes overlap in flight: the same core reaches **175.2 M/s**, **97.9%** of the link.",
                "Now remove the fence after each write, and the writes overlap in flight. The same single core reaches one hundred seventy-five million writes per second: ninety-seven point nine percent of what the link can carry.",
                "F-FENCE",
            ),
            (
                "The link was never the bottleneck: **the fence was**. But without it, what keeps messages **in order**?",
                "So the link was never the bottleneck. The fence was. But a fence is there for a reason: ordering. Without it, could the reader see messages out of order, or half written?",
                "F-FENCE / F-ORDER",
            ),
            (
                "First, a small message is **one 64-byte write**: `MOVDIR64B` sends all 64 bytes as a single unit.",
                "First, a small message is a single sixty-four byte write. The move dir sixty-four B instruction sends all sixty-four bytes as one unit, never in pieces.",
                "F-ORDER-ATOMIC",
            ),
            (
                "Each message carries its **sequence number at both ends**. The reader takes it only when **both match**.",
                "Second, every message carries its sequence number at both ends. The reader takes a message only when head and tail match. Until then, it simply polls again.",
                "F-ORDER",
            ),
            (
                "Write-combined replies and longer messages may land **in pieces, in any order**: the reader waits for **every part**.",
                "A write-combined reply, or a message longer than one line, may land in pieces, in any order. The reader waits until every part checks out: no fence per line, and no half-read message.",
                "F-ORDER-PARTS",
            ),
            (
                "Messages may also **overtake each other**. The reader goes **strictly in sequence**: an early one waits its turn.",
                "Third, messages may overtake each other: forty-three can land before forty-two. The reader goes strictly in sequence, so forty-three just waits its turn.",
                "F-ORDER-SEQ",
            ),
            (
                "Across all our runs, **more than 2.5 billion** messages and frames: not one was read torn.",
                "Across all of our runs, more than two and a half billion messages and frames, not a single one was read torn.",
                "F-ORDER",
            ),
            (
                "Coherence would not remove this check: CC-NIC's descriptors still carry a **ready flag, written last**.",
                "And coherence would not remove this check. It keeps each cache line up to date, not whole messages, so C C nick's own descriptors still carry a ready flag, written last. Ordering is the reader's job either way.",
                "F-ORDER-COH",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C05 push, don't pull
    dict(
        id="C05",
        file="C05PushHandoff",
        title="Push, don't share",
        stage=1,
        source="Measured: 64 B round trip · 1 host core + 1 NIC core · IOMMU on",
        cues=[
            (
                "What is host–NIC traffic, really? **One side writes, the other reads**, then the buffer changes hands.",
                "Look at what host to nick traffic actually is. One side writes a message, the other side reads it, and then the buffer changes hands. Nothing is shared back and forth. It is a hand-off.",
                "F-THESIS",
            ),
            (
                "A hand-off doesn't need coherence. It needs each write to **land in the reader's cache**.",
                "A hand-off does not need a protocol that tracks every copy of every cache line. It only needs each write to land directly in the reader's cache.",
                "F-THESIS",
            ),
            (
                "PCIe already does this: a 64-byte host write lands in the **NIC's cache** — 99.94% of reads hit.",
                "And P C I E already does this. A sixty-four byte write from the host lands in the nick's own cache: ninety-nine point nine four percent of its reads hit there.",
                "F-CACHES",
            ),
            (
                "Replies go the same way: the NIC's write lands in the **host's cache** (Intel DDIO). Each side polls only its own cache.",
                "Replies travel the same way in reverse: what the nick writes lands in the host's last level cache, through Intel's D D I O. So each side only ever polls its own cache, and nothing is read across the link.",
                "F-CACHES",
            ),
            (
                "We call it **DirectPush**: one write per message, no doorbell, no read across the link. Round trip: **897 ns**.",
                "We call this DirectPush: one write per message in each direction, with no doorbell and no read across the link. A sixty-four byte round trip takes eight hundred ninety-seven nanoseconds.",
                "F-RTT",
            ),
            (
                "Same card, same link, standard descriptor interface: **4.6 µs**. Changing only the interface: **5.2× faster**.",
                "On the very same card and link, the standard descriptor interface takes four point six microseconds. Changing only the interface makes the round trip five point two times faster.",
                "F-SAMECARD",
            ),
            (
                "For scale: real CXL coherent NIC **~1.3–2.1 µs**, no IOMMU. CC-NIC's 490 ns was **emulated** on two CPUs.",
                "For scale, the real C X L coherent nick measures about one point three to two point one microseconds, without I O M M U protection. C C nick's faster numbers were emulated with two C P U sockets, and we do not claim to beat them.",
                "F-CXLNIC / F-CCNIC-EMU",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C06 coherence is not free
    dict(
        id="C06",
        file="C06CoherenceTax",
        title="Coherence is not free",
        stage=2,
        source="CC-NIC Fig. 15 · measured: closed loop, 64 B, pool 64 KiB → 128 MiB",
        cues=[
            (
                "Coherence isn't only unnecessary here. It also carries a **cost** that push simply avoids.",
                "Coherence is not only unnecessary here. It also carries a cost that push simply avoids.",
                "—",
            ),
            (
                "A coherent NIC caches host buffers it has read. When the host reuses one, each copy must be **invalidated over the link**.",
                "A coherent nick holds copies of the host buffers it has read in its own cache. When the host writes to those buffers again, each stale copy must first be invalidated across the link.",
                "F-RETAIN-CCNIC",
            ),
            (
                "Real stacks keep sent buffers: TCP holds each one until it is acked. CC-NIC measured **−20%** throughput from that alone.",
                "Real network stacks keep sent buffers around. T C P, for example, holds every sent packet until it is acknowledged. C C nick measured a twenty percent throughput drop from that alone.",
                "F-RETAIN-CCNIC",
            ),
            (
                "With push, data arrives **by value**: the NIC never caches host memory, so there is nothing to invalidate.",
                "With push, data arrives by value. The nick never caches host memory, so there is never anything to invalidate.",
                "F-RETAIN",
            ),
            (
                "It frees the sender too: a pushed buffer can be **reused at once**. One core rewriting one 64 B buffer: **174.8 M/s**.",
                "Push frees the sender, too. Since the nick never reads a buffer back, the C P U can reuse it the moment it is pushed. One core rewriting a single sixty-four byte buffer still pushes one hundred seventy-four point eight million messages per second.",
                "F-REUSE",
            ),
            (
                "We grew the retained pool **2,000×**, from 64 KiB to 128 MiB, past the 60 MiB host cache. Throughput: **−0.9%**.",
                "We grew the retained buffer pool two thousand times, from sixty-four kilobytes to one hundred twenty-eight megabytes, larger than the entire host cache. Throughput moved by less than one percent.",
                "F-RETAIN",
            ),
            (
                "Isolation stays too: every run kept the **IOMMU on**. CC-NIC leaves it to future work; CXL devices bypass it.",
                "Isolation stays, too. Every run here kept the I O M M U on. C C nick leaves protection to future work, and the C X L nick paper notes that C X L devices are not protected by the I O M M U the way P C I E devices are.",
                "F-IOMMU",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C07 real hardware: credits
    dict(
        id="C07",
        file="C07Credits",
        title="Real hardware pushes back",
        stage=3,
        source="Measured: 64 B messages · open and closed loop · IOMMU on",
        cues=[
            (
                "Emulating a NIC with a second CPU hides one thing: a real NIC sits behind **I/O bridges**, and bridges can jam.",
                "Emulating a nick with a second C P U hides one thing. A real nick sits behind I O bridges, and bridges have failure modes of their own.",
                "F-BRIDGE",
            ),
            (
                "With many messages in flight while the NIC polls hard, its inbound bridge drops into **single file**: ~13 M/s.",
                "When many messages are in flight while the card's cores poll very hard, the card's inbound bridge drops into single file, letting through one write about every seventy-six nanoseconds. Throughput collapses to about thirteen million messages per second.",
                "F-BRIDGE",
            ),
            (
                "Counters showed the caches nearly empty: the jam sat **inside the bridge**, set off by polling empty slots.",
                "Hardware counters showed the queues on both sides were nearly empty. The jam sat inside the bridge itself, set off when card cores kept polling slot after empty slot while writes streamed in.",
                "F-BRIDGE",
            ),
            (
                "Not a link limit: push alone hits 175 M/s. It is a **congestion collapse**, and tails explode: p99 **44.5 µs**.",
                "That is not the link's limit; push alone reaches one hundred seventy-five million. It is a congestion collapse. Requests pile up behind the bridge, and at twenty million requests per second, the ninety-ninth percentile latency explodes to forty-four point five microseconds.",
                "F-CREDIT",
            ),
            (
                "The fix is classic: **credits**. The host caps how many messages may be in flight, like a TCP window.",
                "The fix is a classic one: credits. The host simply caps how many messages may be in flight at once, much like a T C P window.",
                "F-CREDIT",
            ),
            (
                "Same 20 M/s load, now with credits: p99 falls from **44.5 µs to 7.0 µs**, and the host-side queue drains.",
                "Under the same twenty million per second load, with credits the tail falls from forty-four point five to seven microseconds, and the queue that had built up inside the host drains away.",
                "F-CREDIT",
            ),
            (
                "Honest limit: host credits alone can't hold p99 under **2 µs** at 20 M/s. That needs the NIC to back off as well.",
                "One honest limit: host credits alone cannot hold the tail under two microseconds at twenty million per second. That needs the nick to back off on demand as well, which is our next step.",
                "F-CREDIT-LIMIT",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C08 big messages: NIC DMA
    dict(
        id="C08",
        file="C08BulkDma",
        title="Big messages: let the NIC move them",
        stage=3,
        source="Measured: 1 host core + 1 NIC core · 64 B – 4 KiB messages",
        cues=[
            (
                "Push is ideal for small messages. But a 4 KiB payload would cost the CPU **64 separate line writes**.",
                "Pushing is ideal for small messages. But a four kilobyte payload would cost the C P U sixty-four separate line writes, burning cycles the application needs.",
                "F-BULK",
            ),
            (
                "So large payloads take another road: push **one 64-byte command**, and the NIC's own **DMA engine** moves the bytes.",
                "So large payloads take a different road. The host pushes a single sixty-four byte command, and the card's own D M A engine, which every nick already has, moves the bytes. No extra accelerator is needed.",
                "F-BULK",
            ),
            (
                "Ordering stays cheap: **one fence per message**, before the command, so the DMA never reads stale data.",
                "Ordering stays cheap here too. The host fences once, after writing the payload and before the command, so the D M A engine never reads stale data. That is one fence per message, not one per line.",
                "F-ORDER-DMA",
            ),
            (
                "On the way back, the NIC writes the **payload first**, then a **64-byte completion**. PCIe keeps them in order.",
                "On the way back, the D M A engine writes the payload first, then a sixty-four byte completion, on the same path. P C I E keeps these writes in order, so once the host sees the completion, the whole payload is already there.",
                "F-ORDER-DMA",
            ),
            (
                "Up to 256 B: pushed inline. From 512 B: by DMA. At 4 KiB, the host's CPU share drops **46.6% → 16.3%**.",
                "Messages up to two hundred fifty-six bytes are pushed inline; from five hundred twelve bytes up, they go by D M A. At four kilobytes, the host's C P U share drops from forty-six point six to sixteen point three percent.",
                "F-BULK",
            ),
            (
                "Mixing needs care: with **one shared credit pool**, a bulk flood starves small messages. Their p99: **40.5 ms**.",
                "Mixing the two needs care. If small and large messages share one credit pool, a flood of bulk transfers starves the small ones, and their tail latency reaches forty point five milliseconds.",
                "F-HOL",
            ),
            (
                "Give each class **its own credit window**, and small messages stay at **2.75 µs** under the same flood.",
                "Give each class its own credit window, and the small messages stay at two point seven five microseconds under exactly the same flood.",
                "F-HOL",
            ),
            (
                "Against a fairly tuned standard interface, DirectPush needs **1.5–2.1× fewer** host CPU cycles per packet.",
                "And against the card's standard interface, tuned as fairly as we could, DirectPush needs one point five to two point one times fewer host C P U cycles per packet. For large packets, much of that saving is software overhead; for small ones, it is the interface itself.",
                "F-CPU",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C09 bursts: active NIC
    dict(
        id="C09",
        file="C09ActiveNic",
        title="Bursts: keep the backlog on the NIC",
        stage=3,
        source="Measured: 64 B bursts at 100 M/s · 1 host core + 1 NIC core",
        cues=[
            (
                "Push has a bonus: the **NIC is the writer**, so it decides where data lands, even during a receive burst.",
                "Push has a bonus. Because the nick is the one writing, it decides where data lands, and that matters most during a receive burst.",
                "F-BURST",
            ),
            (
                "A passive NIC pours the burst into a big host ring. At 128 MiB, it overflows the host cache and spills to DRAM.",
                "A passive nick pours the whole burst into a big ring in host memory. At one hundred twenty-eight megabytes, that overflows the host cache, and the data spills to D RAM.",
                "F-BURST",
            ),
            (
                "An **active NIC** keeps the backlog in its own memory and refills a small **256 KiB** host ring as credits return.",
                "An active nick instead keeps the backlog in its own memory, and refills a small, two hundred fifty-six kilobyte host ring as the host returns credits.",
                "F-BURST",
            ),
            (
                "Same 128 MiB burst: host cache misses **91.4% → 0%**, host DRAM traffic **−98.9%**.",
                "With the same one hundred twenty-eight megabyte burst, the host's cache misses fall from ninety-one percent to zero, and host D RAM traffic falls by ninety-eight point nine percent.",
                "F-BURST",
            ),
            (
                "Bursts up to 32 MiB fit in the NIC's **48 MiB cache** and touch no DRAM at all; larger ones go to the **NIC's own DRAM**.",
                "Bursts up to thirty-two megabytes fit entirely in the card's forty-eight megabyte cache, and touch no D RAM at all. Larger bursts land in the card's own D RAM, not the host's.",
                "F-BURST",
            ),
            (
                "With no burst at all, the extra bookkeeping costs just **6 ns** per round trip.",
                "And when there is no burst, the extra credit bookkeeping costs only six nanoseconds per round trip.",
                "F-BURST",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C10 end to end over 100GbE
    dict(
        id="C10",
        file="C10EndToEnd",
        title="End to end over 100GbE",
        stage=4,
        source="Measured: mercury (ConnectX-5) ⇄ 3 m 100GbE ⇄ CN106 ⇄ saturn",
        cues=[
            (
                "So far the NIC only echoed messages. Does the gain survive a real network? We wired it to a second server over **100GbE**.",
                "So far, the nick only echoed messages back. Does the gain survive a real network? We connected it to a second server over a hundred gigabit Ethernet cable.",
                "F-E2E",
            ),
            (
                "Now every request crosses the full path: client NIC, cable, our NIC's Ethernet engine, PCIe, the host, and back.",
                "Now every request crosses the full path: the client's nick, the cable, our card's Ethernet engine, P C I E, and the host, and then all the way back.",
                "F-E2E",
            ),
            (
                "64-byte round trip: **10.21 µs** with the standard interface, **5.96 µs** with DirectPush: **−4.25 µs** (−42%).",
                "For sixty-four byte requests, the round trip drops from ten point two one microseconds with the standard interface to five point nine six with DirectPush. That saves four and a quarter microseconds, forty-two percent, with the wire included.",
                "F-E2E",
            ),
            (
                "About **4.3 µs** of that is cable and Ethernet hardware. The host–NIC part itself fell from **5.9 µs to 1.7 µs**.",
                "About four point three microseconds of that is the fixed cost of the cable and the Ethernet hardware. The host to nick part itself fell from five point nine to one point seven microseconds.",
                "F-E2E",
            ),
            (
                "The gain holds at every size: **10.4 → 6.6 µs** at 256 B, **15.1 → 11.9 µs** at 4 KiB.",
                "The gain holds at every message size: from ten point four to six point six microseconds at two hundred fifty-six bytes, and from fifteen point one to eleven point nine at four kilobytes.",
                "F-E2E-SIZES",
            ),
            (
                "Under load, the tail stays low: at **2 M requests/s**, the 64 B p99 is **12.6 µs** instead of **26.3 µs**.",
                "It holds under load, too. At two million requests per second, the ninety-ninth percentile latency for sixty-four byte requests is twelve point six microseconds, instead of twenty-six point three.",
                "F-E2E-LOAD",
            ),
            (
                "Can **one host core** fill a 100GbE link with small packets? With 64-byte UDP payloads: **99.76 Gbps**, line rate.",
                "And can a single host core keep a hundred gigabit link full of small packets? With sixty-four byte U D P payloads, one core drives ninety-nine point seven six gigabits per second: line rate.",
                "F-1CORE",
            ),
            (
                "The best-tuned standard interface gets about **half** of that. Bare 64 B frames hit the NIC's own ~100 Mpps send limit.",
                "The best-tuned standard interface reaches only about half of that on one core. And for bare sixty-four byte frames, the limit becomes the card's own transmit path, at about a hundred million packets per second, not the host.",
                "F-1CORE",
            ),
        ],
    ),
    # ------------------------------------------------------------------ C11 takeaway
    dict(
        id="C11",
        file="C11Takeaway",
        title="Takeaway",
        stage=5,
        source="",
        cues=[
            (
                "Back to the question: the speedup came from **putting data in the reader's cache without round trips**.",
                "So, back to our question. The speedup came from putting each message into the reader's cache, with no round trips. Coherence was one way to get there, not the reason it works.",
                "F-THESIS",
            ),
            (
                "Plain PCIe gets there with **writes pushed both ways**, **credits** against congestion, and the **NIC's DMA** for bulk.",
                "Plain P C I E gets there with three simple pieces: writes pushed in both directions, credits to avoid congestion, and the card's own D M A for bulk data.",
                "F-THESIS",
            ),
            (
                "The result: sub-µs round trips, line rate from one core, no coherence tax, and the IOMMU stays on.",
                "What we get: sub-microsecond round trips, line rate from a single core, no coherence tax on retained buffers, and full I O M M U isolation, on hardware you can buy today.",
                "F-RTT / F-1CORE / F-RETAIN / F-IOMMU",
            ),
            (
                "Coherence still matters for **truly shared state**, such as atomics on a shared buffer allocator. We don't claim that.",
                "Coherence still matters where host and nick truly share mutable state, such as atomic operations on a shared buffer allocator. P C I E cannot do that, and we do not claim it can.",
                "F-SCOPE",
            ),
            (
                "Related work orders device writes at the receiver with new PCIe/CPU extensions. **Unmodified hardware** suffices.",
                "This builds on recent work that also orders device writes at the receiver, but proposed new P C I E and C P U extensions to do it. We show that unmodified hardware is already enough, and what it takes to make it robust.",
                "F-RELATED",
            ),
            (
                "Open next step: let the NIC **back off on demand**, to keep sub-2 µs tails at higher load.",
                "The main open step is on the nick side: backing off on demand, so tails stay under two microseconds at higher load.",
                "F-CREDIT-LIMIT",
            ),
            (
                "**CC-NIC does not need cache coherence.** It needs a better hand-over, and PCIe already provides one.",
                "Our one-sentence takeaway: C C nick does not need cache coherence. It needs a better hand-over, and plain P C I E already provides one.",
                "F-THESIS",
            ),
        ],
    ),
]
