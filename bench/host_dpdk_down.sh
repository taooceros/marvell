#!/bin/sh
# Undo host_dpdk_up.sh: remove the VFs (the PF / ens1 stays up), free hugepages.
# usage: sudo ./host_dpdk_down.sh
set -eu
D=/sys/bus/pci/devices/0000:99:00.0
echo 0 > $D/sriov_numvfs
echo 1 > $D/sriov_drivers_autoprobe
echo 0 > /sys/devices/system/node/node1/hugepages/hugepages-2048kB/nr_hugepages
chown root /dev/hugepages
cat $D/sriov_numvfs
grep -E 'HugePages_(Total|Free)' /proc/meminfo
