#!/bin/sh
# Root-only host power knobs for latency experiments on the polling core.
#   observe: let everyone read the current uncore frequency (no change to it)
#   pin:     observe + uncore min = max on both packages, performance governor
#            on the polling core $CPU (default 40) and its SMT sibling
#   restore: firmware uncore range back, schedutil governor back
# usage: sudo ./host_power.sh observe|pin|restore
set -eu
CPU=${CPU:-40}
U=/sys/devices/system/cpu/intel_uncore_frequency
cpus="$CPU $((CPU + 64))"

case "${1:-}" in
observe)
	chmod a+r $U/*/current_freq_khz
	;;
pin)
	chmod a+r $U/*/current_freq_khz
	for d in $U/*/; do
		max=$(cat $d/initial_max_freq_khz)
		echo "$max" > $d/max_freq_khz
		echo "$max" > $d/min_freq_khz
	done
	for c in $cpus; do echo performance > /sys/devices/system/cpu/cpu$c/cpufreq/scaling_governor; done
	;;
restore)
	for d in $U/*/; do
		echo "$(cat $d/initial_min_freq_khz)" > $d/min_freq_khz
		echo "$(cat $d/initial_max_freq_khz)" > $d/max_freq_khz
	done
	for c in $cpus; do echo schedutil > /sys/devices/system/cpu/cpu$c/cpufreq/scaling_governor; done
	;;
*)
	echo "usage: $0 observe|pin|restore" >&2
	exit 2
	;;
esac
for d in $U/*/; do
	echo "$(basename $d): uncore $(cat $d/min_freq_khz)-$(cat $d/max_freq_khz) kHz, now $(cat $d/current_freq_khz) kHz"
done
for c in $cpus; do echo "cpu$c: $(cat /sys/devices/system/cpu/cpu$c/cpufreq/scaling_governor)"; done
