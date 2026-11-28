#!/bin/sh
# Root-only host setup for MRV-215 (Linux kernel octeon_ep_vf data-plane VF
# control benchmark on saturn):
#   up           load octeon_ep_vf, stop irqbalance, unbind data VF 0000:99:02.0
#                from vfio-pci, bind to octeon_ep_vf, configure 10.10.200.1/24
#                (MTU 9000 fallback 1500), disable RPS, pin VF IRQs to cpu40,
#                raise socket buffers, set busy_poll/busy_read=0.
#   busypoll-off set net.core.busy_poll=0 and net.core.busy_read=0, pin VF IRQs.
#   busypoll-on  set net.core.busy_poll=50 and net.core.busy_read=50, pin VF IRQs.
#   pin-irqs     re-pin all verified data VF MSI-X IRQs to cpu40.
#   down         bring down ONLY data VF 0000:99:02.0, rebind to vfio-pci,
#                restore /dev/vfio/<group> owner, restore sysctls & irqbalance,
#                rmmod octeon_ep_vf (if loaded by us).
#   status       print current VF/ens1/module/sysctl/irqbalance state (read-only).
#
# IRONCLAD SAFETY: NEVER touches PF 0000:99:00.0, ens1 (10.10.0.1/24), or sdp1-0.
# usage: sudo ./bench/host_vf_kernel.sh up|busypoll-off|busypoll-on|pin-irqs|down|status
set -eu

PF_BDF="0000:99:00.0"
VF_BDF="0000:99:02.0"
VF_SYS="/sys/bus/pci/devices/${VF_BDF}"
DATA_CPU=40
VF_HOST_CIDR="10.10.200.1/24"
KO_INSTALLED="/usr/local/lib/mrv215-octeon_ep_vf.ko"
KO_BUILD="/tmp/mrv215-octeon-ep-vf-build/octeon_ep_vf.ko"
STATE_FILE="/run/mrv215_saved_state.json"
DEFAULT_OWNER="hongtao"

die() {
	echo "host_vf_kernel.sh ERROR: $*" >&2
	exit 1
}

verify_safe_vf() {
	[ "$VF_BDF" != "$PF_BDF" ] || die "refusing PF $PF_BDF"
	[ -d "$VF_SYS" ] || die "VF sysfs missing: $VF_SYS"
	[ -L "$VF_SYS/physfn" ] || die "$VF_BDF has no physfn (not an SR-IOV VF)"
	physfn=$(basename "$(readlink -f "$VF_SYS/physfn")")
	[ "$physfn" = "$PF_BDF" ] || die "$VF_BDF physfn=$physfn != $PF_BDF"
	vendor=$(cat "$VF_SYS/vendor")
	device=$(cat "$VF_SYS/device")
	[ "$vendor" = "0x177d" ] || die "$VF_BDF vendor=$vendor != 0x177d"
	case "$device" in
	0xb903|0xba03|0xb900|0xba00) ;;
	*) die "$VF_BDF unexpected device=$device" ;;
	esac
}

verify_safe_iface() {
	iface="$1"
	case "$iface" in
	""|ens1|sdp1-0|lo) die "SAFETY REFUSED: forbidden interface '$iface'" ;;
	esac
	[ -e "/sys/class/net/$iface/device" ] || die "interface '$iface' has no PCI device"
	dev_bdf=$(basename "$(readlink -f "/sys/class/net/$iface/device")")
	[ "$dev_bdf" = "$VF_BDF" ] || die "SAFETY REFUSED: interface '$iface' belongs to $dev_bdf != $VF_BDF"
}

vf_driver() {
	if [ -L "$VF_SYS/driver" ]; then
		basename "$(readlink -f "$VF_SYS/driver")"
	else
		echo "none"
	fi
}

vf_iommu_group() {
	if [ -L "$VF_SYS/iommu_group" ]; then
		basename "$(readlink -f "$VF_SYS/iommu_group")"
	else
		echo ""
	fi
}

discover_vf_iface() {
	wait_max="${1:-0}"
	i=0
	while :; do
		if [ -d "$VF_SYS/net" ]; then
			for d in "$VF_SYS/net"/*; do
				[ -d "$d" ] || continue
				cand=$(basename "$d")
				verify_safe_iface "$cand"
				echo "$cand"
				return 0
			done
		fi
		[ "$i" -lt "$wait_max" ] || return 1
		i=$((i + 1))
		sleep 0.1
	done
}

pin_vf_irqs() {
	iface="$1"
	verify_safe_iface "$iface"
	irqs=$(awk -v pfx="${iface}-q" -v exact="$iface" '
		NR > 1 {
			irq = $1
			sub(/:$/, "", irq)
			lbl = $NF
			if (irq ~ /^[0-9]+$/ && (lbl == exact || index(lbl, pfx) == 1)) {
				print irq
			}
		}
	' /proc/interrupts)
	[ -n "$irqs" ] || die "no MSI-X IRQs found in /proc/interrupts for $iface"
	for irq in $irqs; do
		echo "$DATA_CPU" > "/proc/irq/$irq/smp_affinity_list"
		got=$(cat "/proc/irq/$irq/smp_affinity_list")
		[ "$got" = "$DATA_CPU" ] || die "IRQ $irq affinity '$got' != '$DATA_CPU'"
	done
}

save_state_if_absent() {
	loaded_mod="$1"
	[ ! -f "$STATE_FILE" ] || return 0
	owner="${SUDO_USER:-$DEFAULT_OWNER}"
	[ "$owner" != "root" ] || owner="$DEFAULT_OWNER"
	irqbal="false"
	if [ "$(systemctl is-active irqbalance 2>/dev/null || true)" = "active" ]; then
		irqbal="true"
	fi
	rmem_def=$(sysctl -n net.core.rmem_default)
	rmem_max=$(sysctl -n net.core.rmem_max)
	wmem_def=$(sysctl -n net.core.wmem_default)
	wmem_max=$(sysctl -n net.core.wmem_max)
	backlog=$(sysctl -n net.core.netdev_max_backlog)
	bpoll=$(sysctl -n net.core.busy_poll)
	bread=$(sysctl -n net.core.busy_read)
	cat >"$STATE_FILE" <<EOF
{
  "vf_bdf": "${VF_BDF}",
  "user": "${owner}",
  "irqbalance_was_active": ${irqbal},
  "loaded_octeon_ep_vf": ${loaded_mod},
  "sysctls": {
    "net.core.rmem_default": "${rmem_def}",
    "net.core.rmem_max": "${rmem_max}",
    "net.core.wmem_default": "${wmem_def}",
    "net.core.wmem_max": "${wmem_max}",
    "net.core.netdev_max_backlog": "${backlog}",
    "net.core.busy_poll": "${bpoll}",
    "net.core.busy_read": "${bread}"
  }
}
EOF
	chmod 0644 "$STATE_FILE"
}

restore_saved_host_knobs() {
	owner="$DEFAULT_OWNER"
	irqbal="false"
	should_rmmod="false"
	if [ -f "$STATE_FILE" ]; then
		eval "$(python3 - "$STATE_FILE" <<'PY'
import json, shlex, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    d = {}
print("owner=" + shlex.quote(str(d.get("user") or "hongtao")))
print("irqbal=" + ("true" if d.get("irqbalance_was_active") else "false"))
print("should_rmmod=" + ("true" if d.get("loaded_octeon_ep_vf") else "false"))
for k, v in (d.get("sysctls") or {}).items():
    print(f"sysctl -q -w {shlex.quote(str(k))}={shlex.quote(str(v))} || true")
PY
)"
	else
		sysctl -q -w net.core.busy_poll=0 || true
		sysctl -q -w net.core.busy_read=0 || true
		sysctl -q -w net.core.rmem_default=212992 || true
		sysctl -q -w net.core.rmem_max=212992 || true
		sysctl -q -w net.core.wmem_default=212992 || true
		sysctl -q -w net.core.wmem_max=212992 || true
		sysctl -q -w net.core.netdev_max_backlog=1000 || true
	fi

	if [ "$should_rmmod" = "true" ] && [ -d /sys/module/octeon_ep_vf ]; then
		rmmod octeon_ep_vf 2>/dev/null || true
	elif [ -d /sys/module/octeon_ep_vf ] && [ "$(vf_driver)" = "vfio-pci" ]; then
		rmmod octeon_ep_vf 2>/dev/null || true
	fi

	if [ "$irqbal" = "true" ]; then
		systemctl start irqbalance 2>/dev/null || true
	fi

	rm -f "$STATE_FILE"
}

print_status() {
	verify_safe_vf
	drv=$(vf_driver)
	grp=$(vf_iommu_group)
	iface=$(discover_vf_iface 0 2>/dev/null || echo "none")
	mod="no"
	[ ! -d /sys/module/octeon_ep_vf ] || mod="yes"
	echo "vf_bdf=$VF_BDF driver=$drv iommu_group=${grp:-none} iface=$iface octeon_ep_vf_loaded=$mod"
	if [ -n "$grp" ] && [ -e "/dev/vfio/$grp" ]; then
		echo "vfio_dev=$(ls -l "/dev/vfio/$grp")"
	fi
	if [ "$iface" != "none" ]; then
		echo "vf_addr=$(ip -br addr show "$iface" 2>/dev/null || true)"
		echo "vf_mtu=$(cat "/sys/class/net/$iface/mtu" 2>/dev/null || true)"
		echo "vf_mac=$(cat "/sys/class/net/$iface/address" 2>/dev/null || true)"
	fi
	echo "ens1=$(ip -br addr show ens1 2>/dev/null || echo missing)"
	echo "busy_poll=$(sysctl -n net.core.busy_poll) busy_read=$(sysctl -n net.core.busy_read)"
	echo "rmem_max=$(sysctl -n net.core.rmem_max) wmem_max=$(sysctl -n net.core.wmem_max) backlog=$(sysctl -n net.core.netdev_max_backlog)"
	echo "irqbalance=$(systemctl is-active irqbalance 2>/dev/null || true)"
}

require_root() {
	[ "$(id -u)" -eq 0 ] || die "must run as root (use sudo)"
}

cmd_up() {
	require_root
	verify_safe_vf

	grp=$(vf_iommu_group)
	if [ -n "$grp" ] && [ -e "/dev/vfio/$grp" ]; then
		if fuser "/dev/vfio/$grp" >/dev/null 2>&1; then
			die "SAFETY REFUSED: /dev/vfio/$grp is currently open by a process"
		fi
	fi

	loaded_mod="false"
	if [ ! -d /sys/module/octeon_ep_vf ]; then
		if modinfo octeon_ep_vf >/dev/null 2>&1; then
			modprobe octeon_ep_vf
		else
			case "$0" in
			*/mrv215-vf-ctl) ko_to_load="$KO_INSTALLED" ;;
			*)
				if [ -f "$KO_INSTALLED" ]; then
					ko_to_load="$KO_INSTALLED"
				else
					ko_to_load="$KO_BUILD"
				fi
				;;
			esac
			[ -f "$ko_to_load" ] || die "missing built kernel module: $ko_to_load"
			kver=$(uname -r)
			vm=$(modinfo -F vermagic "$ko_to_load" | cut -d' ' -f1)
			[ "$vm" = "$kver" ] || die "$ko_to_load vermagic '$vm' != kernel '$kver'"
			insmod "$ko_to_load"
		fi
		[ -d /sys/module/octeon_ep_vf ] || die "octeon_ep_vf failed to load"
		loaded_mod="true"
	fi

	save_state_if_absent "$loaded_mod"

	if [ "$(systemctl is-active irqbalance 2>/dev/null || true)" = "active" ]; then
		systemctl stop irqbalance
	fi

	cur_drv=$(vf_driver)
	if [ "$cur_drv" != "octeon_ep_vf" ]; then
		if [ "$cur_drv" != "none" ]; then
			echo "$VF_BDF" > "/sys/bus/pci/drivers/$cur_drv/unbind"
		fi
		echo "octeon_ep_vf" > "$VF_SYS/driver_override"
		echo "$VF_BDF" > /sys/bus/pci/drivers_probe
	fi

	iface=$(discover_vf_iface 100) || die "timed out waiting for VF netdev under $VF_SYS/net"
	verify_safe_iface "$iface"

	ip addr flush dev "$iface"
	if ! ip link set dev "$iface" mtu 9000 2>/dev/null; then
		ip link set dev "$iface" mtu 1500
	fi
	ip addr replace "$VF_HOST_CIDR" dev "$iface"
	ip link set dev "$iface" up

	i=0
	while [ "$i" -lt 40 ]; do
		if [ "$(cat "/sys/class/net/$iface/carrier" 2>/dev/null || echo 0)" = "1" ]; then
			break
		fi
		i=$((i + 1))
		sleep 0.1
	done

	for rps in /sys/class/net/"$iface"/queues/rx-*/rps_cpus; do
		[ -w "$rps" ] || continue
		echo 0 > "$rps" 2>/dev/null || true
	done

	sysctl -q -w net.core.rmem_default=16777216
	sysctl -q -w net.core.rmem_max=16777216
	sysctl -q -w net.core.wmem_default=16777216
	sysctl -q -w net.core.wmem_max=16777216
	sysctl -q -w net.core.netdev_max_backlog=16384
	sysctl -q -w net.core.busy_poll=0
	sysctl -q -w net.core.busy_read=0

	pin_vf_irqs "$iface"
	print_status
}

cmd_busypoll() {
	val="$1"
	require_root
	verify_safe_vf
	[ "$(vf_driver)" = "octeon_ep_vf" ] || die "$VF_BDF is not bound to octeon_ep_vf"
	iface=$(discover_vf_iface 10) || die "no VF netdev found for $VF_BDF"
	sysctl -q -w "net.core.busy_poll=$val"
	sysctl -q -w "net.core.busy_read=$val"
	pin_vf_irqs "$iface"
	print_status
}

cmd_pin_irqs() {
	require_root
	verify_safe_vf
	[ "$(vf_driver)" = "octeon_ep_vf" ] || die "$VF_BDF is not bound to octeon_ep_vf"
	iface=$(discover_vf_iface 10) || die "no VF netdev found for $VF_BDF"
	pin_vf_irqs "$iface"
	print_status
}

cmd_down() {
	require_root
	verify_safe_vf

	rebind_rc=0
	if [ -d "$VF_SYS/net" ]; then
		for d in "$VF_SYS/net"/*; do
			[ -d "$d" ] || continue
			iface=$(basename "$d")
			verify_safe_iface "$iface"
			ip neigh flush dev "$iface" 2>/dev/null || true
			ip addr flush dev "$iface" 2>/dev/null || true
			ip link set dev "$iface" down 2>/dev/null || true
		done
	fi

	cur_drv=$(vf_driver)
	if [ "$cur_drv" != "vfio-pci" ]; then
		if [ "$cur_drv" != "none" ]; then
			echo "$VF_BDF" > "/sys/bus/pci/drivers/$cur_drv/unbind" || rebind_rc=1
		fi
		echo "vfio-pci" > "$VF_SYS/driver_override" || rebind_rc=1
		echo "$VF_BDF" > /sys/bus/pci/drivers_probe || rebind_rc=1
	fi

	owner="$DEFAULT_OWNER"
	if [ -f "$STATE_FILE" ]; then
		owner=$(python3 - "$STATE_FILE" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("user") or "hongtao")
except Exception:
    print("hongtao")
PY
)
	fi

	if [ "$(vf_driver)" = "vfio-pci" ]; then
		grp=$(vf_iommu_group)
		if [ -n "$grp" ] && [ -e "/dev/vfio/$grp" ]; then
			chown "$owner" "/dev/vfio/$grp" || rebind_rc=1
		fi
	else
		rebind_rc=1
	fi

	restore_saved_host_knobs
	print_status
	[ "$rebind_rc" -eq 0 ] || die "failed to restore $VF_BDF to vfio-pci"
}

case "${1:-}" in
up)           cmd_up ;;
busypoll-off) cmd_busypoll 0 ;;
busypoll-on)  cmd_busypoll 50 ;;
pin-irqs)     cmd_pin_irqs ;;
down)         cmd_down ;;
status)       print_status ;;
*)
	echo "usage: sudo $0 up|busypoll-off|busypoll-on|pin-irqs|down|status" >&2
	exit 2
	;;
esac
