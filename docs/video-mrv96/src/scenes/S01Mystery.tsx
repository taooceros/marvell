import React from "react";
import {
  Arrow,
  C,
  Card,
  Cue,
  FONT,
  K,
  Link,
  MONO,
  PacketStream,
  Pill,
  SceneDef,
  Stage,
  Txt,
  clamp01,
  fade,
  lerp,
  prog,
  useRealT,
  useT,
} from "../lib";
import { InfPill, Rig, rigGeom, rigRoutes } from "./kit";

// <cues>
const cues: Cue[] = [
  [0.60, 13.34, "An **Intel Xeon** host (one core pinned at **3.9 GHz**) talks to a **Marvell OCTEON 10 DPU** (24 Arm cores at **2.0 GHz**) over **PCIe Gen5 ×4**.", "An Intel Xeon host, one core pinned at three point nine gigahertz, talks to a Marvell Octeon ten D P U with twenty-four Arm cores at two gigahertz over P C I e Gen five by four."], // 0
  [13.69, 24.83, "Forward: one `MOVDIR64B` instruction writes a **64-byte** message directly into a card-memory ring over PCIe — no doorbell, no DMA.", "Forward: one move dir sixty-four B instruction writes a sixty-four byte message straight into a card-memory ring over P C I e. No doorbell, no D M A."], // 1
  [25.18, 37.66, "Return: a card core polls the ring, checks sequence tags, and sends a **64-byte** reply to host memory via **write-combining** (`4 × 16 B`).", "Return: a card core polls the ring, checks sequence tags, and writes a sixty-four byte reply into host memory with write combining: four sixteen-byte stores merged into one P C I e write."], // 2
  [38.01, 51.55, "The mystery: one way, one host core hits **161.6 M/s**. Close the loop (`W = 64`, 4 card cores), and the same path collapses **12× to ~13 M/s**.", "Here is the mystery. One way, one host core pushes one hundred sixty-one point six million messages per second. Close the loop at window sixty-four with four card cores, and it collapses twelve times to about thirteen million."], // 3
  [51.90, 63.48, "Our method (**Occam's razor**): fix this fastest baseline, turn **one knob per experiment**, verify every message (**0 lost, 0 corrupt**), and tag **[INF]**.", "Our method: Occam's razor. Fix this fastest baseline, turn one knob per experiment, verify every message for zero loss or corruption, and tag interpretations as our reading."], // 4
  [63.83, 73.83, "Our 4-step hunt: **rule out return traffic** & solve **one-way**, dissect the **13 M/s loop**, clear **two 4-core suspects**, then **break the loop**.", "Our four-step hunt: sanity-check return traffic and solve one-way, step inside the thirteen million loop, clear two four-core suspects, and finally break the loop."], // 5
];
const DUR = 75.23; // <dur>
// </cues>

const T = cues.map((c) => c[0]);

function Body() {
  const t = useT();
  const rt = useRealT();

  // Rig geometry: centered horizontally in 1920, sitting cleanly in y = 170..777
  const g = rigGeom(0.92, 76.8, -14);
  const routes = rigRoutes(g);

  // Phase envelopes
  const rigOp = fade(t, T[0], T[3] - 0.05, 0.45);
  const showHost = prog(t, T[0] + 0.1, 0.55);
  const showLink = prog(t, T[0] + 1.4, 0.55);
  const showCard = prog(t, T[0] + 2.2, 0.55);

  const kCue0Only = fade(t, T[0] + 2.6, T[1] - 0.1, 0.4);
  const kFwd = fade(t, T[1], T[2] - 0.1, 0.45);
  const kRet = fade(t, T[2], T[3] - 0.05, 0.45);
  const kDim = clamp01(kFwd + kRet) * 0.65;

  // Mystery comparison (Cue 3 stays visible through Cue 5)
  const kMystery = prog(t, T[3] + 0.35, 0.55);
  const kMysteryLeft = prog(t, T[3] + 0.45, 0.55);
  const kMysteryRight = prog(t, T[3] + 3.2, 0.55);
  const kMysteryBadge = prog(t, T[3] + 4.4, 0.55);
  const kMysterySub = fade(t, T[3] + 5.2, T[4] - 0.1, 0.4);
  const mysteryDim = lerp(1, 0.88, prog(t, T[4] + 0.2, 0.5));

  // Cue 4: Occam's razor method cards
  const kMethod = fade(t, T[4] + 0.25, T[5] - 0.1, 0.45);
  const kM1 = fade(t, T[4] + 0.4, T[5] - 0.1, 0.45);
  const kM2 = fade(t, T[4] + 2.6, T[5] - 0.1, 0.45);
  const kM3 = fade(t, T[4] + 5.2, T[5] - 0.1, 0.45);

  // Cue 5: 4-step detective roadmap
  const kRoad = prog(t, T[5] + 0.25, 0.45);
  const kS1 = prog(t, T[5] + 0.4, 0.45);
  const kS2 = prog(t, T[5] + 2.1, 0.45);
  const kS3 = prog(t, T[5] + 4.0, 0.45);
  const kS4 = prog(t, T[5] + 5.8, 0.45);

  // Subtle ambient motion for Cue 0 pulse & Cue 1/2 rings
  const pulse = 0.5 + 0.5 * Math.sin(rt * 2.8);
  const cringFill = kFwd > 0.05 ? 3 + Math.floor((rt * 3.5) % 5) : 3;
  const hringFill = kRet > 0.05 ? 3 + Math.floor((rt * 3.2) % 5) : 2;
  const activeCore = kRet > 0.05 ? 0 : Math.floor((rt * 2.0) % 4);

  const fwdLaneOp = showLink * lerp(0.85, 1, kFwd) * lerp(1, 0.28, kRet);
  const retLaneOp = showLink * lerp(0.85, 0.25, kFwd) * lerp(1, 1, kRet);

  // Bar widths inside the Cue 3 mystery cards (track width = 720)
  const trackW = 720;
  const oneWayBarW = trackW * prog(t, T[3] + 1.0, 0.75);
  const loopBarW = trackW * (13 / 161.6) * prog(t, T[3] + 3.6, 0.75);

  const steps = [
    {
      num: "1",
      title: "Sanity check & One-way",
      sub: "Rule out return traffic & find the one-way ceiling",
      color: K.fwd,
      k: kS1,
    },
    {
      num: "2",
      title: "Inside the ~13 M/s loop",
      sub: "Measure where closed-loop time actually goes",
      color: K.card,
      k: kS2,
    },
    {
      num: "3",
      title: "Clear 2 four-core suspects",
      sub: "Test multi-core ideas on the card one knob at a time",
      color: K.probe,
      k: kS3,
    },
    {
      num: "4",
      title: "Break the loop",
      sub: "Remove the true bottleneck & recover speed",
      color: K.fast,
      k: kS4,
    },
  ];

  return (
    <>
      <Stage>
        {/* Cues 0–2: Hardware testbed & direct forward/return paths */}
        {rigOp > 0.01 && (
          <g opacity={rigOp}>
            {/* Subtle ambient pulse behind PCIe link during Cue 0 */}
            {kCue0Only > 0.01 && (
              <rect
                x={g.r.link.x + 12}
                y={g.r.link.y + 12}
                width={g.r.link.w - 24}
                height={g.r.link.h - 24}
                rx={18}
                fill="none"
                stroke={K.fwd}
                strokeWidth={3}
                opacity={kCue0Only * (0.14 + 0.22 * pulse)}
              />
            )}

            <Rig
              g={g}
              show={{
                host: showHost,
                hcore: showHost,
                wq: showHost,
                hring: showHost,
                link: showLink,
                card: showCard,
                cring: showCard,
                ccores: showCard,
                wc: showCard,
              }}
              hl={{
                hcore: kFwd,
                wq: kFwd,
                cring: kFwd,
                ccores: kRet,
                wc: kRet,
                hring: kRet,
              }}
              dim={kDim}
              labels={{
                ccores: kRet > 0.3 ? ["Arm core", "2.0 GHz"] : ["24 Arm cores", "2.0 GHz"],
              }}
              cores={kRet > 0.3 ? 1 : 4}
              activeCore={activeCore}
              ringFill={{ cring: cringFill, hring: hringFill }}
              lanes={{ fwd: fwdLaneOp, ret: retLaneOp }}
            />

            {/* Cue 1: Active forward path lines & cyan packet stream */}
            {kFwd > 0.01 && (
              <g opacity={kFwd}>
                <Link pts={routes.send} color={K.fwd} w={5} opacity={0.85} />
                <PacketStream pts={routes.send} travel={rt * 250} gap={54} size={14} color={K.fwd} />
                <PacketStream pts={routes.fwd} travel={rt * 290} gap={58} size={15} color={K.fwd} />
              </g>
            )}

            {/* Cue 2: Active return path lines & green packet stream */}
            {kRet > 0.01 && (
              <g opacity={kRet}>
                <Link pts={routes.poll} color={K.ret} w={5} opacity={0.75} />
                <Link pts={routes.reply} color={K.ret} w={5} opacity={0.85} />
                <Link pts={routes.hpoll} color={K.ret} w={5} opacity={0.75} />
                <PacketStream pts={routes.poll} travel={rt * 210} gap={64} size={13} color={K.ret} />
                <PacketStream pts={routes.reply} travel={rt * 220} gap={58} size={14} color={K.ret} />
                <PacketStream pts={routes.ret} travel={rt * 270} gap={58} size={15} color={K.ret} />
                <PacketStream pts={routes.hpoll} travel={rt * 210} gap={64} size={13} color={K.ret} />
              </g>
            )}
          </g>
        )}

        {/* Cue 5: Connecting arrows between the 4 detective steps */}
        {kRoad > 0.01 && (
          <g opacity={kRoad}>
            <Arrow x1={478} y1={746} x2={518} y2={746} color={K.fwd} w={4} head={13} k={kS2} />
            <Arrow x1={940} y1={746} x2={980} y2={746} color={K.card} w={4} head={13} k={kS3} />
            <Arrow x1={1402} y1={746} x2={1442} y2={746} color={K.probe} w={4} head={13} k={kS4} />
          </g>
        )}
      </Stage>

      {/* Cue 0: Hardware spec strip below the Rig */}
      {kCue0Only > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 132,
            top: 804,
            width: 1656,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 20,
            opacity: kCue0Only,
            transform: `translateY(${(1 - kCue0Only) * 14}px)`,
            fontFamily: FONT,
          }}
        >
          <div
            style={{
              flex: 1,
              background: C.panel,
              border: `2px solid ${K.host}`,
              borderRadius: 14,
              padding: "12px 20px",
              fontSize: 24,
              color: C.text,
              textAlign: "center",
            }}
          >
            <strong style={{ color: K.host }}>Intel Xeon</strong> · core 40 pinned{" "}
            <strong style={{ color: C.text }}>3.9 GHz</strong>
          </div>
          <div
            style={{
              flex: 1,
              background: C.panel,
              border: `2px solid ${K.fwd}`,
              borderRadius: 14,
              padding: "12px 20px",
              fontSize: 24,
              color: C.text,
              textAlign: "center",
            }}
          >
            <strong style={{ color: K.fwd }}>PCIe Gen5 ×4</strong> · direct{" "}
            <strong style={{ color: C.text }}>64-byte</strong> messages
          </div>
          <div
            style={{
              flex: 1,
              background: C.panel,
              border: `2px solid ${K.card}`,
              borderRadius: 14,
              padding: "12px 20px",
              fontSize: 24,
              color: C.text,
              textAlign: "center",
            }}
          >
            <strong style={{ color: K.card }}>OCTEON 10 DPU</strong> ·{" "}
            <strong style={{ color: C.text }}>24 Arm cores · 2.0 GHz</strong>
          </div>
        </div>
      )}

      {/* Cue 1: Forward path callout above the forward PCIe lane */}
      {kFwd > 0.01 && (
        <Card
          x={624}
          y={170}
          w={672}
          border={K.fwd}
          style={{
            padding: "14px 22px",
            opacity: kFwd,
            transform: `translateY(${(1 - kFwd) * -12}px)`,
            textAlign: "center",
            background: "rgba(20,27,46,0.95)",
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 26, fontWeight: 800, color: K.fwd }}>
            1 × MOVDIR64B → 64 B cell
          </div>
          <div style={{ fontSize: 24, color: C.text, marginTop: 4 }}>
            Direct write into card ring · <strong>no doorbell, no DMA</strong>
          </div>
        </Card>
      )}

      {/* Cue 2: Return path callout below the return PCIe lane */}
      {kRet > 0.01 && (
        <Card
          x={618}
          y={702}
          w={684}
          border={K.ret}
          style={{
            padding: "14px 20px",
            opacity: kRet,
            transform: `translateY(${(1 - kRet) * 12}px)`,
            textAlign: "center",
            background: "rgba(20,27,46,0.95)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              fontFamily: MONO,
              fontSize: 22,
              fontWeight: 800,
              color: K.ret,
            }}
          >
            {[0, 1, 2, 3].map((i) => (
              <span
                key={i}
                style={{
                  background: "rgba(67,197,158,0.16)",
                  border: `1.5px solid ${K.ret}`,
                  borderRadius: 6,
                  padding: "2px 8px",
                }}
              >
                16 B
              </span>
            ))}
            <span style={{ color: C.muted, padding: "0 4px" }}>→</span>
            <span style={{ fontSize: 25 }}>64 B WC write</span>
          </div>
          <div style={{ fontSize: 22, color: C.text, marginTop: 8 }}>
            Card polls ring · checks seq tags · <strong>4 × 16 B</strong> reply to host
          </div>
        </Card>
      )}

      {/* Cues 3–5: Top half — The 12× Mystery comparison */}
      {kMystery > 0.01 && (
        <div style={{ opacity: kMystery * mysteryDim }}>
          <Txt
            x={60}
            y={132}
            w={1800}
            size={22}
            weight={800}
            color={K.knob}
            align="center"
            style={{ letterSpacing: 2.5 }}
          >
            THE 12× MYSTERY · SAME HARDWARE · SAME 64-BYTE DIRECT PATH
          </Txt>

          {/* Left card: One-way 161.6 M/s */}
          <Card
            x={60}
            y={174}
            w={780}
            h={382}
            border={K.fwd}
            style={{
              opacity: kMysteryLeft,
              transform: `translateY(${(1 - kMysteryLeft) * 18}px)`,
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              padding: "24px 30px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 24, fontWeight: 800, letterSpacing: 1.5, color: K.fwd }}>
                ONE-WAY DIRECT WRITE
              </span>
              <span
                style={{
                  fontFamily: MONO,
                  fontSize: 21,
                  color: K.fwd,
                  background: "rgba(76,201,240,0.12)",
                  border: `1.5px solid ${K.fwd}`,
                  borderRadius: 999,
                  padding: "3px 14px",
                }}
              >
                1 host core
              </span>
            </div>

            <div style={{ textAlign: "center", margin: "8px 0" }}>
              <div
                style={{
                  fontSize: 96,
                  fontWeight: 900,
                  color: K.fwd,
                  lineHeight: 1.0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                161.6 M/s
              </div>
              <div style={{ fontSize: 26, color: C.muted, marginTop: 12 }}>
                Direct <span style={{ fontFamily: MONO, color: C.text }}>MOVDIR64B</span> into card ring
              </div>
            </div>

            <div>
              <div
                style={{
                  width: trackW,
                  height: 28,
                  background: "#0f1528",
                  border: `1.5px solid ${C.line}`,
                  borderRadius: 14,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: oneWayBarW,
                    height: "100%",
                    background: `linear-gradient(90deg, ${K.fwd}, ${K.fast})`,
                    borderRadius: 14,
                  }}
                />
              </div>
            </div>
          </Card>

          {/* Center 12× badge */}
          <div
            style={{
              position: "absolute",
              left: 856,
              top: 264,
              width: 208,
              height: 202,
              background: "rgba(249,199,79,0.12)",
              border: `3px solid ${K.knob}`,
              borderRadius: 24,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              opacity: kMysteryBadge,
              transform: `scale(${lerp(0.82, 1, kMysteryBadge)})`,
              boxShadow: "0 0 36px rgba(249,199,79,0.22)",
              fontFamily: FONT,
            }}
          >
            <div
              style={{
                fontSize: 74,
                fontWeight: 900,
                color: K.knob,
                lineHeight: 1,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              12×
            </div>
            <div style={{ fontSize: 24, fontWeight: 800, color: K.knob, letterSpacing: 2, marginTop: 8 }}>
              SLOWER
            </div>
          </div>

          {/* Right card: Closed loop ~13 M/s */}
          <Card
            x={1080}
            y={174}
            w={780}
            h={382}
            border={K.slow}
            style={{
              opacity: kMysteryRight,
              transform: `translateY(${(1 - kMysteryRight) * 18}px)`,
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              padding: "24px 30px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 24, fontWeight: 800, letterSpacing: 1.5, color: K.slow }}>
                CLOSED LOOP (REQ + REPLY)
              </span>
              <span
                style={{
                  fontFamily: MONO,
                  fontSize: 21,
                  color: K.slow,
                  background: "rgba(249,65,68,0.12)",
                  border: `1.5px solid ${K.slow}`,
                  borderRadius: 999,
                  padding: "3px 14px",
                }}
              >
                W = 64 · 4 card cores
              </span>
            </div>

            <div style={{ textAlign: "center", margin: "8px 0" }}>
              <div
                style={{
                  fontSize: 96,
                  fontWeight: 900,
                  color: K.slow,
                  lineHeight: 1.0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                ~13 M/s
              </div>
              <div style={{ fontSize: 26, color: C.muted, marginTop: 12 }}>
                Same host core · same DPU · same PCIe Gen5 ×4 path
              </div>
            </div>

            <div>
              <div
                style={{
                  width: trackW,
                  height: 28,
                  background: "#0f1528",
                  border: `1.5px solid ${C.line}`,
                  borderRadius: 14,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: Math.max(loopBarW > 0 ? 18 : 0, loopBarW),
                    height: "100%",
                    background: K.slow,
                    borderRadius: 14,
                  }}
                />
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* Cue 3 lower prompt (fades out before Cue 4) */}
      {kMysterySub > 0.01 && (
        <div
          style={{
            position: "absolute",
            left: 260,
            top: 632,
            width: 1400,
            background: "rgba(249,199,79,0.08)",
            border: `2px solid ${K.knob}`,
            borderRadius: 18,
            padding: "24px 36px",
            textAlign: "center",
            fontFamily: FONT,
            fontSize: 32,
            fontWeight: 700,
            color: C.text,
            opacity: kMysterySub,
            transform: `translateY(${(1 - kMysterySub) * 14}px)`,
          }}
        >
          Same Xeon core. Same OCTEON 10 DPU. Same PCIe Gen5 ×4 path —{" "}
          <span style={{ color: K.knob }}>why does closing the loop collapse 12×?</span>
        </div>
      )}

      {/* Cue 4: Occam's razor method (3 horizontal cards) */}
      {kMethod > 0.01 && (
        <>
          <Txt
            x={60}
            y={590}
            w={1800}
            size={22}
            weight={800}
            color={C.muted}
            style={{ letterSpacing: 2.5, opacity: kMethod }}
          >
            OUR METHOD · OCCAM&apos;S RAZOR
          </Txt>

          <Card
            x={60}
            y={630}
            w={580}
            h={232}
            border={K.fixed}
            style={{
              opacity: kM1,
              transform: `translateY(${(1 - kM1) * 18}px)`,
              padding: "20px 26px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
              <Pill
                x={0}
                y={0}
                color={K.fixed}
                size={21}
                solid
                style={{ position: "static", padding: "2px 12px" }}
              >
                1
              </Pill>
              <span style={{ fontSize: 27, fontWeight: 800, color: C.text }}>Fixed fastest baseline</span>
            </div>
            <div style={{ fontSize: 24, color: C.muted, lineHeight: 1.4 }}>
              Pin clocks and lock in the fastest direct path:
            </div>
            <div
              style={{
                marginTop: 14,
                fontFamily: MONO,
                fontSize: 23,
                fontWeight: 700,
                color: K.fwd,
                background: "#0f1528",
                border: `1.5px solid ${C.line}`,
                borderRadius: 10,
                padding: "8px 14px",
                display: "inline-block",
              }}
            >
              MOVDIR64B + WC (64 B)
            </div>
          </Card>

          <Card
            x={670}
            y={630}
            w={580}
            h={232}
            border={K.knob}
            style={{
              opacity: kM2,
              transform: `translateY(${(1 - kM2) * 18}px)`,
              padding: "20px 26px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
              <Pill
                x={0}
                y={0}
                color={K.knob}
                size={21}
                solid
                style={{ position: "static", padding: "2px 12px" }}
              >
                2
              </Pill>
              <span style={{ fontSize: 27, fontWeight: 800, color: K.knob }}>One knob per experiment</span>
            </div>
            <div style={{ fontSize: 24, color: C.text, lineHeight: 1.45 }}>
              Change <strong>one variable at a time</strong> — hold every other parameter fixed so every cause is
              isolated cleanly.
            </div>
          </Card>

          <Card
            x={1280}
            y={630}
            w={580}
            h={232}
            border={K.fast}
            style={{
              opacity: kM3,
              transform: `translateY(${(1 - kM3) * 18}px)`,
              padding: "20px 26px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
              <Pill
                x={0}
                y={0}
                color={K.fast}
                size={21}
                solid
                style={{ position: "static", padding: "2px 12px" }}
              >
                3
              </Pill>
              <span style={{ fontSize: 27, fontWeight: 800, color: K.fast }}>Verified data</span>
            </div>
            <div style={{ fontSize: 24, color: C.text, lineHeight: 1.4 }}>
              Every message checked:{" "}
              <strong style={{ color: K.fast, fontFamily: MONO, whiteSpace: "nowrap" }}>0 lost, 0 corrupt</strong>.
            </div>
            <div style={{ fontSize: 23, color: C.muted, marginTop: 14, display: "flex", alignItems: "center", gap: 10 }}>
              <InfPill size={20} />
              <span>marks every interpretation</span>
            </div>
          </Card>
        </>
      )}

      {/* Cue 5: 4-step detective roadmap */}
      {kRoad > 0.01 && (
        <>
          <Txt
            x={60}
            y={590}
            w={1800}
            size={22}
            weight={800}
            color={C.muted}
            style={{ letterSpacing: 2.5, opacity: kRoad }}
          >
            OUR 4-STEP DETECTIVE HUNT
          </Txt>

          {steps.map((s, idx) => {
            const x = 60 + idx * (414 + 48);
            return (
              <Card
                key={s.num}
                x={x}
                y={630}
                w={414}
                h={232}
                border={s.color}
                style={{
                  opacity: s.k,
                  transform: `translateY(${(1 - s.k) * 18}px)`,
                  padding: "18px 22px",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span
                    style={{
                      fontFamily: MONO,
                      fontSize: 20,
                      fontWeight: 800,
                      color: C.bg,
                      background: s.color,
                      borderRadius: 8,
                      padding: "2px 11px",
                    }}
                  >
                    STEP {s.num}
                  </span>
                </div>
                <div style={{ fontSize: 26, fontWeight: 800, color: s.color, lineHeight: 1.25 }}>
                  {s.title}
                </div>
                <div style={{ fontSize: 22, color: C.muted, lineHeight: 1.35 }}>{s.sub}</div>
              </Card>
            );
          })}
        </>
      )}
    </>
  );
}

export const S01: SceneDef = {
  id: "S01",
  title: "The direct path & the 12× mystery",
  stage: undefined,
  source: "Xeon host ⇄ OCTEON 10 DPU · PCIe Gen5 ×4 · 64-byte messages",
  dur: DUR,
  cues,
  C: Body,
};
