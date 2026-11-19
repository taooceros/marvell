#!/usr/bin/env bash
# Reversible host helper for MRV-114 (1 vs 2 vs 4 DSA engines on dsa2/group2.0):
#   status: non-root read-only check of dsa2, wq2.0, engine2.0..2.3 group_id,
#           active engine count in group2.0, swerror, and device permissions.
#   1|2|4:  disable dsa2/wq2.0 & dsa2, load a dsa2-only accel-config JSON with
#           engine2.0..engine2.(N-1) bound to group2.0 (preserving
#           mode=dedicated type=user size=128 group=0 and leaving other DSA
#           devices untouched). Uses passwordless `sudo -n /usr/bin/accel-config`
#           when run as non-root; udev (/etc/udev/rules.d/99-idxd-wq.rules)
#           keeps /dev/dsa/wq2.0 at root:dsa 0660 and PCI resource2 keeps its
#           owner across disable/load-config.
# usage:
#   ./bench/host_dsa_engines.sh status
#   ./bench/host_dsa_engines.sh 1|2|4
set -euo pipefail

DEV=dsa2
PCI=0000:f2:01.0
WQ=wq2.0
DSA_SYS="/sys/bus/dsa/devices/$DEV"
WQ_SYS="/sys/bus/dsa/devices/$WQ"
RES2="/sys/bus/pci/devices/$PCI/resource2"
HOLDER=/var/tmp/dsa-lab/side-B.holder
ACCEL=/usr/bin/accel-config

run_accel() {
	if [ "$(id -u)" -eq 0 ]; then
		"$ACCEL" "$@"
	else
		sudo -n "$ACCEL" "$@"
	fi
}

status() {
	local active=0
	local eng_summary=""
	local i gid
	for i in 0 1 2 3; do
		gid=$(cat "/sys/bus/dsa/devices/engine2.$i/group_id")
		eng_summary="${eng_summary} engine2.${i}=${gid}"
		if [ "$gid" = "0" ]; then
			active=$((active + 1))
		fi
	done
	echo "$DEV ($PCI, NUMA $(cat "$DSA_SYS/numa_node")): state=$(cat "$DSA_SYS/state") pasid_enabled=$(cat "$DSA_SYS/pasid_enabled") clients=$(cat "$DSA_SYS/clients")"
	echo "  active_engines_group0: $active (${eng_summary# })"
	if [ -e "$WQ_SYS/state" ]; then
		echo "  $WQ: state=$(cat "$WQ_SYS/state") mode=$(cat "$WQ_SYS/mode") type=$(cat "$WQ_SYS/type") size=$(cat "$WQ_SYS/size") group=$(cat "$WQ_SYS/group_id") clients=$(cat "$WQ_SYS/clients")"
	fi
	echo "  swerror: $(cat "$DSA_SYS/errors")"
	ls -l "/dev/dsa/$WQ" "$RES2" 2>/dev/null || ls -l "$RES2"
}

set_engines() {
	local n="$1"

	if [ -e "$HOLDER" ]; then
		echo "host_dsa_engines.sh: dsa-lab holds side-B ($HOLDER); refusing to reconfigure $DEV" >&2
		cat "$HOLDER" >&2
		exit 1
	fi
	local clients
	clients=$(cat "$DSA_SYS/clients")
	if [ "$clients" != "0" ]; then
		echo "host_dsa_engines.sh: $DEV still has clients=$clients; stop active users first" >&2
		exit 1
	fi

	run_accel disable-wq "$DEV/$WQ" 2>/dev/null || true
	run_accel disable-device "$DEV" 2>/dev/null || true

	local conf
	conf=$(mktemp "${TMPDIR:-/tmp}/mrv114-dsa2-${n}eng.XXXXXX.conf")
	trap 'rm -f "${conf:-}"' EXIT

	local grouped=""
	local ungrouped=""
	local i
	for i in 0 1 2 3; do
		if [ "$i" -lt "$n" ]; then
			if [ -n "$grouped" ]; then grouped="${grouped},"; fi
			grouped="${grouped}
          {
            \"dev\":\"engine2.${i}\",
            \"group_id\":0
          }"
		else
			if [ -n "$ungrouped" ]; then ungrouped="${ungrouped},"; fi
			ungrouped="${ungrouped}
      {
        \"dev\":\"engine2.${i}\",
        \"group_id\":-1
      }"
		fi
	done

	local ungrouped_block=""
	if [ -n "$ungrouped" ]; then
		ungrouped_block=",
    \"ungrouped_engines\":[${ungrouped}
    ]"
	fi

	cat >"$conf" <<EOF
[
  {
    "dev":"dsa2",
    "read_buffer_limit":0,
    "groups":[
      {
        "dev":"group2.0",
        "read_buffers_reserved":0,
        "use_read_buffer_limit":0,
        "read_buffers_allowed":96,
        "grouped_workqueues":[
          {
            "dev":"wq2.0",
            "mode":"dedicated",
            "size":128,
            "group_id":0,
            "priority":10,
            "block_on_fault":0,
            "max_batch_size":1024,
            "max_transfer_size":1073741824,
            "type":"user",
            "name":"mrv95",
            "driver_name":"user",
            "threshold":0,
            "ats_disable":-2
          }
        ],
        "grouped_engines":[${grouped}
        ]
      }
    ]${ungrouped_block}
  }
]
EOF
	chmod 0644 "$conf"
	run_accel load-config -c "$conf" -e

	# If invoked as root, also ensure permissions match bench/host_dsa.sh up.
	if [ "$(id -u)" -eq 0 ]; then
		local u="${SUDO_USER:-hongtao}"
		chown "$u:dsa" "/dev/dsa/$WQ"
		chmod 0660 "/dev/dsa/$WQ"
		chown "$u" "$RES2"
		chmod 0600 "$RES2"
	fi

	# Wait briefly for udev rule to set /dev/dsa/wq2.0 group=dsa mode=0660.
	local try
	for try in 1 2 3 4 5 6 7 8 9 10; do
		if [ -r "/dev/dsa/$WQ" ] && [ -w "/dev/dsa/$WQ" ]; then
			break
		fi
		sleep 0.1
	done

	status
}

case "${1:-status}" in
status)
	status
	;;
1|2|4)
	set_engines "$1"
	;;
*)
	echo "usage: $0 status | $0 1|2|4" >&2
	exit 2
	;;
esac
