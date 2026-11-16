//! Host-side ping-pong client over the SDP VF. Batch = 1: every request is its
//! own `rte_eth_tx_burst` call (one doorbell). Concurrency = `--window`.
//!
//! usage: pingpong [EAL args] -- --peer MAC [--rounds N] [--window W] [--frame BYTES]

use std::error::Error;

use clap::Parser;
use dpdk::{Eal, Mempool, Port};
use testbed::closed_loop::{ClosedLoop, FrameReplies};
use testbed::{MIN_FRAME, Mac, write_request};

const PORT: u16 = 0;
const QUEUE: u16 = 0;
const DESC: u16 = 2048; // octeon_ep minimum

#[derive(Parser)]
struct Args {
    /// MAC of the reflector.
    #[arg(long, short = 'd')]
    peer: Mac,
    /// Measured round trips (after warmup).
    #[arg(long, short = 'n', default_value_t = 1_000_000)]
    rounds: u64,
    /// Requests kept outstanding.
    #[arg(long, short = 'w', default_value_t = 1, value_parser = clap::value_parser!(u64).range(1..=1024))]
    window: u64,
    /// Ethernet frame size without FCS.
    #[arg(long, short = 's', default_value_t = 64, value_parser = clap::value_parser!(u16).range(MIN_FRAME as i64..=1514))]
    frame: u16,
    #[arg(long, default_value_t = 20_000)]
    warmup: u64,
}

fn main() -> Result<(), Box<dyn Error>> {
    let (eal, app_args) = Eal::init(std::env::args())?;
    let args = Args::parse_from(app_args);

    let pool = Mempool::new(&eal, "pingpong", 8191, Port::socket_of(&eal, PORT))?;
    let port = Port::start(&eal, PORT, &pool, 1, DESC)?;
    let me = Mac(port.mac());

    let run = ClosedLoop { rounds: args.rounds, window: args.window, warmup: args.warmup };
    let outcome = run.run(&eal, &mut FrameReplies::new(&eal, &port, 1), |seq, tsc| {
        let mut req = pool.alloc().ok_or("mbuf pool exhausted")?;
        let frame = req.append(args.frame).ok_or("frame larger than mbuf")?;
        write_request(frame, args.peer, me, seq, tsc);
        let mut req = Some(req);
        while let Some(r) = req.take() {
            req = port.send(QUEUE, r).err(); // ring full: retry
        }
        Ok(())
    })?;
    println!("sdp frame {}B {outcome}", args.frame);
    outcome.check()?;
    Ok(())
}
