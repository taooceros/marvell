#!/bin/sh
# Root-only host settings for the card control path that survive a host
# reboot (MRV-19):
#   - octeon_ep loads at boot: the out-of-tree module built in
#     pcie_ep_octeon_host/drivers/octeon_ep is copied to
#     /lib/modules/$(uname -r)/updates/ (depmod; udev loads it for the PF, and
#     /etc/modules-load.d/octeon_ep.conf names it too). Its probe waits for the
#     card's octep_cp_agent, so host and card may come up in either order.
#     After rebuilding the module, run install again.
#   - ens1 gets 10.10.0.1/24 from systemd-networkd, also while the card is
#     down (no carrier); boot does not wait for it.
#   - kernel.perf_event_paranoid = 0 (uncore perf counters as non-root).
# install writes the files and applies them now (ens1 keeps its address);
# remove deletes them and leaves the running state alone until the next boot.
#
# reload: after the card rebooted on its own or from its shell, ens1 stays
# down (octeon_ep only turns the carrier off when the card's heartbeat stops
# and never sets the device up again). Removing the driver from that state
# chip-resets the card once more (octep_soft_reset_cnxk_pf); the new probe
# waits for the card's agent (octep-cp-agent.service, about 1 min) and creates
# ens1. The PF's VFs go away with the driver, so host_dpdk_up.sh makes the
# same number again. Stop host DPDK processes first.
# usage: sudo ./host_boot_persist.sh install|remove|reload
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
KO=$REPO/pcie_ep_octeon_host/drivers/octeon_ep/octeon_ep.ko
PF=/sys/bus/pci/devices/0000:99:00.0
KVER=$(uname -r)
MOD=/lib/modules/$KVER/updates/octeon_ep.ko
LOAD=/etc/modules-load.d/octeon_ep.conf
NET=/etc/systemd/network/10-octeon-ens1.network
SYSCTL=/etc/sysctl.d/60-perf-event-paranoid.conf

case "${1:-}" in
install)
	[ "$(modinfo -F vermagic "$KO" | cut -d' ' -f1)" = "$KVER" ] ||
		{ echo "$KO is not built for $KVER" >&2; exit 1; }
	install -D -m 644 "$KO" $MOD
	depmod -a "$KVER"
	echo octeon_ep > $LOAD
	cat > $NET <<'EOF'
# bench/host_boot_persist.sh (MRV-19): host end of the card control path
# (card sdp1-0 10.10.0.2/24).
[Match]
Name=ens1

[Link]
RequiredForOnline=no

[Network]
Address=10.10.0.1/24
ConfigureWithoutCarrier=yes
IgnoreCarrierLoss=yes
LinkLocalAddressing=ipv6
IPv6AcceptRA=no
EOF
	networkctl reload
	echo 'kernel.perf_event_paranoid = 0' > $SYSCTL
	sysctl -q -p $SYSCTL
	;;
remove)
	rm -f $MOD $LOAD $NET $SYSCTL
	depmod -a "$KVER"
	;;
reload)
	nvf=$(cat $PF/sriov_numvfs)
	for g in $PF/virtfn*/iommu_group; do
		[ -e "$g" ] || continue
		! fuser -s /dev/vfio/"$(basename "$(readlink "$g")")" ||
			{ echo "a process has a host VF open; stop it first" >&2; exit 1; }
	done
	[ ! -e /sys/module/octeon_ep ] || rmmod octeon_ep
	if modinfo -n octeon_ep >/dev/null 2>&1; then modprobe octeon_ep; else insmod "$KO"; fi
	i=0
	until [ -e $PF/net/ens1 ]; do
		i=$((i + 1))
		[ $i -le 300 ] || { echo "ens1 did not appear within 300 s" >&2; exit 1; }
		sleep 1
	done
	[ -e $NET ] || { ip addr replace 10.10.0.1/24 dev ens1; ip link set ens1 up; }
	[ "$nvf" -eq 0 ] || NVF=$nvf sh "$REPO/bench/host_dpdk_up.sh"
	;;
*)
	echo "usage: $0 install|remove|reload" >&2
	exit 2
	;;
esac
echo "module: $(modinfo -n octeon_ep 2>/dev/null || echo 'not installed')"
echo "loaded: $(cat /sys/module/octeon_ep/srcversion 2>/dev/null || echo no)"
ls $LOAD $NET $SYSCTL 2>/dev/null || true
sysctl kernel.perf_event_paranoid
ip -br addr show ens1 2>/dev/null || echo "ens1: absent"
