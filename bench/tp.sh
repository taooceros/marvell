#!/bin/sh
# Drive the two interactive testpmds running in tmux sessions:
#   host: tmux session "htp" (log /tmp/htp.log), card: tmux session "tp" on 10.10.0.2 (log /tmp/tp.log)
# usage: tp.sh host|card "testpmd command" [wait_s]   -> prints the log lines the command produced
side=$1 cmd=$2 wait=${3:-1}
case $side in
host)
	off=$(stat -c %s /tmp/htp.log)
	tmux send-keys -t htp "$cmd" Enter
	sleep "$wait"
	tail -c +$((off + 1)) /tmp/htp.log ;;
card)
	ssh -o BatchMode=yes root@10.10.0.2 "off=\$(stat -c %s /tmp/tp.log); tmux send-keys -t tp '$cmd' Enter; sleep $wait; tail -c +\$((off + 1)) /tmp/tp.log" ;;
*)
	echo "usage: $0 host|card cmd [wait]" >&2; exit 2 ;;
esac
