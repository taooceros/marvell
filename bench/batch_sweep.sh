#!/bin/sh
# Sweep testpmd burst size (= packets per TX doorbell on the sender) for both
# directions with the testpmds already started by tp_start.sh NQ.
# Sender: txonly 64 B, open loop (outstanding packets bounded only by ring size).
# Receiver: rxonly with the same burst unless RBURST is given. Rate = receiver
# Rx-pps over a 5 s window.
# usage: batch_sweep.sh NQ "BURSTS" [RBURST]
cd "$(dirname "$0")"
NQ=$1
BURSTS=${2:-"1 8 32"}
RBURST=$3
stat() { ./tp.sh "$1" "show port stats 0"; }
field() { grep -aE "$1" | tail -1 | awk -v k="$2" '{for (i = 1; i < NF; i++) if ($i == k) print $(i + 1)}'; }
for dir in h2c c2h; do
	if [ $dir = h2c ]; then snd=host rcv=card; else snd=card rcv=host; fi
	for b in $BURSTS; do
		rb=${RBURST:-$b}
		./tp.sh $snd "set burst $b" 0.3 >/dev/null
		./tp.sh $rcv "set burst $rb" 0.3 >/dev/null
		for s in $snd $rcv; do ./tp.sh $s "clear port stats all" 0.3 >/dev/null; done
		./tp.sh $rcv "set fwd rxonly" 0.3 >/dev/null
		./tp.sh $snd "set fwd txonly" 0.3 >/dev/null
		./tp.sh $rcv start 0.5 >/dev/null
		./tp.sh $snd start 3 >/dev/null
		stat $rcv >/dev/null; stat $snd >/dev/null
		sleep 5
		r=$(stat $rcv); t=$(stat $snd)
		rx=$(echo "$r" | field 'Rx-pps' 'Rx-pps:')
		missed=$(echo "$r" | field 'RX-missed' 'RX-missed:')
		tx=$(echo "$t" | field 'Tx-pps' 'Tx-pps:')
		./tp.sh $snd stop 1 >/dev/null
		./tp.sh $rcv stop 1 >/dev/null
		printf '%-4s nq=%-2s sender-burst=%-3s rcv-burst=%-3s rx %7.2f Mpps  sender tx %7.2f Mpps  rx-missed-total %s\n' \
			$dir "$NQ" "$b" "$rb" "$(echo "$rx / 1000000" | bc -l)" "$(echo "$tx / 1000000" | bc -l)" "$missed"
	done
done
