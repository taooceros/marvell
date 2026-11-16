#!/bin/sh
# (Re)start both interactive testpmds in tmux, logging through pipe-pane (pty
# output stays line-buffered, unlike `| tee`).
#   host: VF0 0000:99:02.0, main lcore 32 + forwarding lcores 33-40 (NUMA node 1)
#   card: sdp1-1 0002:01:00.2, main lcore 2 + forwarding lcores 3-10
# usage: tp_start.sh [NQ]   (queues and forwarding cores per side, default 8)
NQ=${1:-8}
T=$HOME/dev/marvell/marvell-dpdk/build-host/app/dpdk-testpmd
HOST_MAC=00:00:00:01:01:01
CARD_MAC=E6:C5:A1:F0:3A:A2

ssh -o BatchMode=yes root@10.10.0.2 "tmux kill-session -t tp 2>/dev/null; sleep 1; : > /tmp/tp.log
tmux new-session -d -s tp -x 220 -y 50 'dpdk-testpmd -l 2-$((2 + NQ)) -a 0002:01:00.2 --file-prefix=bench -- -i --rxq=$NQ --txq=$NQ --nb-cores=$NQ --eth-peer=0,$HOST_MAC --tx-ip=10.10.0.12,10.10.0.11 --tx-udp=9000,9000'
tmux pipe-pane -o -t tp 'cat >> /tmp/tp.log'"

tmux kill-session -t htp 2>/dev/null
sleep 1
: > /tmp/htp.log
tmux new-session -d -s htp -x 220 -y 50 "$T -l 32-$((32 + NQ)) --file-prefix=hbench -a 0000:99:02.0 --socket-mem=0,2048 --iova-mode=va -- -i --no-mlockall --rxq=$NQ --txq=$NQ --nb-cores=$NQ --eth-peer=0,$CARD_MAC --tx-ip=10.10.0.11,10.10.0.12 --tx-udp=9000,9000"
tmux pipe-pane -o -t htp 'cat >> /tmp/htp.log'
sleep 8
grep -a 'Port 0:' /tmp/htp.log
ssh -o BatchMode=yes root@10.10.0.2 "grep -a 'Port 0:' /tmp/tp.log"
