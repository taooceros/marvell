#!/bin/sh
# Root-only host setup so DPDK (octeon_ep VF PMD) can run as the invoking user:
# create NVF SR-IOV VFs on the octeon_ep PF without auto-probing a kernel VF
# driver, bind them to vfio-pci, hand the vfio groups and 2 MiB hugepages on
# NUMA node 1 (the device's node) to $SUDO_USER, and the PF's BAR4 (card DRAM
# window, used by testbed inline_pingpong) too.
# usage: sudo NVF=1 ./host_dpdk_up.sh
set -eu
PF=0000:99:00.0
NVF=${NVF:-1}
U=${SUDO_USER:?run with sudo}
D=/sys/bus/pci/devices/$PF

modprobe vfio-pci
echo 0 > $D/sriov_drivers_autoprobe
if [ "$(cat $D/sriov_numvfs)" -lt "$NVF" ]; then
	[ "$(cat $D/sriov_numvfs)" -eq 0 ] || echo 0 > $D/sriov_numvfs
	echo "$NVF" > $D/sriov_numvfs
fi
i=0
while [ $i -lt "$NVF" ]; do
	VF=$(basename "$(readlink $D/virtfn$i)")
	echo vfio-pci > /sys/bus/pci/devices/$VF/driver_override
	[ -e /sys/bus/pci/devices/$VF/driver ] || echo $VF > /sys/bus/pci/drivers_probe
	G=$(basename "$(readlink /sys/bus/pci/devices/$VF/iommu_group)")
	chown "$U" /dev/vfio/$G
	echo "VF$i $VF -> $(basename "$(readlink /sys/bus/pci/devices/$VF/driver)"), /dev/vfio/$G owned by $U"
	i=$((i + 1))
done
echo 2048 > /sys/devices/system/node/node1/hugepages/hugepages-2048kB/nr_hugepages
chown "$U" /dev/hugepages
grep -E 'HugePages_(Total|Free)' /proc/meminfo
chown "$U" $D/resource4
ls -l $D/resource4
