#!/bin/sh
# Root-only host setup for MRV-95 (CN106 card -> host Intel DSA on socket 1):
#   up:     enable dsa2 (0000:f2:01.0, NUMA 1) with one dedicated user WQ
#           (wq2.0, size 128, engine2.0 in group2.0) and hand wq2.0 + BAR2
#           (resource2, portal page at +0x1000 = HPA 0x237ffff21000) to
#           $SUDO_USER so the host testbed process can bind SVA PASID on
#           /dev/dsa/wq2.0 and map the portal page into VF0's VFIO IOMMU
#           domain (0000:99:02.0) without CAP_SYS_RAWIO.
#   down:   disable dsa2 and return resource2 to root (dsa-lab resting state).
#   status: print dsa2 state, wq2.0 mode/size, SWERROR and resource2 owner.
# usage: sudo ./bench/host_dsa.sh up|down|status
set -eu

DEV=dsa2
PCI=0000:f2:01.0
WQ=wq2.0
DSA_SYS=/sys/bus/dsa/devices/$DEV
WQ_SYS=/sys/bus/dsa/devices/$WQ
RES2=/sys/bus/pci/devices/$PCI/resource2
HOLDER=/var/tmp/dsa-lab/side-B.holder

status() {
	echo "$DEV ($PCI, NUMA $(cat $DSA_SYS/numa_node)): state=$(cat $DSA_SYS/state) pasid_enabled=$(cat $DSA_SYS/pasid_enabled) clients=$(cat $DSA_SYS/clients)"
	echo "  swerror: $(cat $DSA_SYS/errors)"
	if [ -e "$WQ_SYS/state" ]; then
		echo "  $WQ: state=$(cat $WQ_SYS/state) mode=$(cat $WQ_SYS/mode) type=$(cat $WQ_SYS/type) size=$(cat $WQ_SYS/size) group=$(cat $WQ_SYS/group_id)"
	fi
	ls -l /dev/dsa/$WQ "$RES2" 2>/dev/null || ls -l "$RES2"
}

case "${1:-up}" in
up)
	U=${SUDO_USER:?run with sudo}
	if [ -e "$HOLDER" ]; then
		echo "host_dsa.sh: dsa-lab holds side-B ($HOLDER); refusing to reconfigure $DEV" >&2
		cat "$HOLDER" >&2
		exit 1
	fi
	accel-config disable-device "$DEV" 2>/dev/null || true
	CONF=$(mktemp "${TMPDIR:-/tmp}/mrv95-dsa2.XXXXXX.conf")
	trap 'rm -f "$CONF"' EXIT
	cat >"$CONF" <<'EOF'
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
        "grouped_engines":[
          {
            "dev":"engine2.0",
            "group_id":0
          }
        ]
      }
    ]
  }
]
EOF
	chmod 0644 "$CONF"
	accel-config load-config -c "$CONF" -e
	chown "$U:dsa" "/dev/dsa/$WQ"
	chmod 0660 "/dev/dsa/$WQ"
	chown "$U" "$RES2"
	status
	;;
down)
	if [ -e "$HOLDER" ]; then
		echo "host_dsa.sh: dsa-lab holds side-B ($HOLDER); refusing to disable $DEV" >&2
		cat "$HOLDER" >&2
		exit 1
	fi
	accel-config disable-wq "$DEV/$WQ" 2>/dev/null || true
	accel-config disable-device "$DEV" 2>/dev/null || true
	chown root:root "$RES2"
	status
	;;
status)
	status
	;;
*)
	echo "usage: sudo $0 up|down|status" >&2
	exit 2
	;;
esac
