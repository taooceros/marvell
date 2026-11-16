#!/bin/sh
# Create the card's DPI DMA VFs and bind them to vfio-pci: 4 by default, one
# per inline_reflector --dma core plus one for --fetch-dma. Runs at every card
# boot as dpi-vfs.service; safe to run again (only fixes missing bindings).
# Never changes an existing VF count other than 0, since a running DPDK
# process may hold the VFs.
# usage: [NVF=4] /root/dpi_vfs_up.sh
set -eu
PF=0000:06:00.0
NVF=${NVF:-4}
D=/sys/bus/pci/devices/$PF

n=$(cat $D/sriov_numvfs)
if [ "$n" -eq 0 ]; then
    echo "$NVF" > $D/sriov_numvfs
elif [ "$n" -ne "$NVF" ]; then
    echo "$PF has $n VFs, not $NVF; not touching them" >&2
    exit 1
fi
i=0
while [ $i -lt "$NVF" ]; do
    VF=$(basename "$(readlink $D/virtfn$i)")
    P=/sys/bus/pci/devices/$VF
    drv=$(basename "$(readlink $P/driver 2>/dev/null)" 2>/dev/null || true)
    if [ "$drv" != vfio-pci ]; then
        echo vfio-pci > $P/driver_override
        [ -z "$drv" ] || echo $VF > $P/driver/unbind
        echo $VF > /sys/bus/pci/drivers_probe
    fi
    echo "VF$i $VF -> $(basename "$(readlink $P/driver)")"
    i=$((i + 1))
done
