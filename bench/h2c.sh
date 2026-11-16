#!/bin/sh
# Host -> card bandwidth: udp_blast for 10 s while testpmd (rxonly) counts.
# testpmd's Rx-pps/Rx-bps are deltas between two "show port stats" calls,
# so take one at t=3 s and read the second at t=8 s (5 s window).
# usage: h2c.sh PAYLOAD CPU_LIST
cd "$(dirname "$0")"
./udp_blast 10.10.0.2 9000 "$1" 10 "$2" &
sleep 3
python3 -u /tmp/zjl.py '[["cmd","show port stats 0","Rx-bps.*testpmd> ",15]]' >/dev/null
./ens1_rate.sh 5 | sed 's/^/  host /'
python3 -u /tmp/zjl.py '[["cmd","show port stats 0","Rx-bps.*testpmd> ",15]]' |
	grep -E 'RX-missed|RX-nombuf|TX-errors|Rx-pps|Tx-pps' | tr -s ' ' | sed 's/^/  testpmd/'
wait
