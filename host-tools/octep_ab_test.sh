#!/bin/bash
# A/B test: does pcie_marvell_cnxk_ep's BAR4 index-7 override break the host
# control mailbox? Run with sudo. The card-side operator flips BAR4_INDEX7
# between runs and signals via flag files in /tmp.
#   A: index 7 = module page (as shipped)    B: index 7 = 0x2fef1 (restored)
set -u
KO=/home/hongtao/dev/marvell/pcie_ep_octeon_host/drivers/octeon_ep/octeon_ep.ko
OUT=/tmp/octep_ab.log
: > $OUT; chmod 644 $OUT

step() { echo "== $* ($(date +%T))" | tee -a $OUT; }
waitflag() { step "waiting for $1"; while [ ! -e "$1" ]; do sleep 1; done; }
load_and_log() {
    local n; n=$(dmesg | wc -l)
    insmod $KO; sleep 8
    dmesg | tail -n +$((n + 1)) | grep -iE "octep|octeon_ep|99:00" | tee -a $OUT
}

step "rmmod (baseline)"; rmmod octeon_ep 2>&1 | tee -a $OUT
touch /tmp/ab_rmmod_done
waitflag /tmp/ab_go_A
step "run A"; load_and_log
rmmod octeon_ep 2>&1 | tee -a $OUT
touch /tmp/ab_A_done
waitflag /tmp/ab_go_B
step "run B"; load_and_log
ip link set ens1 up && ip addr add 10.10.0.1/24 dev ens1
step "B done"; ip -br addr show ens1 | tee -a $OUT
