//! Card-side reflector for `pingpong`: one queue, one lcore; each received
//! burst gets its MACs swapped and goes straight back out in one doorbell.
//!
//! usage: reflector [EAL args]   (port 0 = the SDP VF given with -a)

use std::error::Error;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use dpdk::{Burst, Eal, Mempool, Port};
use testbed::swap_macs;

const PORT: u16 = 0;
const QUEUE: u16 = 0;
const DESC: u16 = 2048;

fn main() -> Result<(), Box<dyn Error>> {
    let (eal, _) = Eal::init(std::env::args())?;
    let quit = Arc::new(AtomicBool::new(false));
    let q = quit.clone();
    ctrlc::set_handler(move || q.store(true, Ordering::Relaxed))?;

    let pool = Mempool::new(&eal, "reflector", 16383, Port::socket_of(&eal, PORT))?;
    let port = Port::start(&eal, PORT, &pool, 1, DESC)?;
    if let Err(e) = port.promiscuous() {
        eprintln!("{e} (ignored)");
    }
    println!("reflector running on port {PORT}");

    let (mut reflected, mut dropped) = (0u64, 0u64);
    let mut burst: Burst = Burst::new();
    while !quit.load(Ordering::Relaxed) {
        if port.rx(QUEUE, &mut burst) == 0 {
            continue;
        }
        for pkt in burst.as_mut_slice() {
            swap_macs(pkt.data_mut());
        }
        reflected += port.tx(QUEUE, &mut burst) as u64;
        dropped += burst.len() as u64;
        burst.clear();
    }
    println!("reflected {reflected}, tx drops {dropped}");
    Ok(())
}
