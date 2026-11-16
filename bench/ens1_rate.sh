#!/bin/sh
# Sample ens1 driver counters over SECONDS and print per-second rates.
# usage: ens1_rate.sh SECONDS
S=/sys/class/net/ens1/statistics
T=${1:-10}
read_all() { cat $S/rx_packets $S/rx_bytes $S/rx_dropped $S/rx_missed_errors $S/tx_packets $S/tx_bytes | tr '\n' ' '; }
set -- $(read_all)
a_rp=$1 a_rb=$2 a_rd=$3 a_rm=$4 a_tp=$5 a_tb=$6
sleep "$T"
set -- $(read_all)
awk -v t="$T" -v rp=$(($1 - a_rp)) -v rb=$(($2 - a_rb)) -v rd=$(($3 - a_rd)) -v rm=$(($4 - a_rm)) \
    -v tp=$(($5 - a_tp)) -v tb=$(($6 - a_tb)) 'BEGIN {
	printf "ens1 RX %.3f Mpps %.2f Gbit/s (avg %d B/pkt), dropped %d, missed %d | TX %.3f Mpps %.2f Gbit/s\n",
	       rp / t / 1e6, rb * 8 / t / 1e9, rp ? rb / rp : 0, rd, rm, tp / t / 1e6, tb * 8 / t / 1e9 }'
