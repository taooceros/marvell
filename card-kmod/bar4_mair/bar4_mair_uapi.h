/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _BAR4_MAIR_UAPI_H
#define _BAR4_MAIR_UAPI_H

#ifdef __KERNEL__
#include <linux/ioctl.h>
#include <linux/types.h>
#else
#include <stdint.h>
#include <sys/ioctl.h>
#endif

#define BAR4_MAIR_MAGIC 'M'

#define BAR4_MAIR_SLOT_BYTES (4ULL << 20)

/* mmap() file offset selects the ARM64 page-table memory attribute mode:
 *   offset = mode * BAR4_MAIR_SLOT_BYTES
 */
#define BAR4_MAIR_MODE_NORMAL_WB       0ULL /* Standard MT_NORMAL (0xFF), ISH */
#define BAR4_MAIR_MODE_NORMAL_NC       1ULL /* Standard Linux MT_NORMAL_NC (0x44), zero MAIR edit */
#define BAR4_MAIR_MODE_INNER_NC_ISH    2ULL /* Custom MAIR slot (0xF4 Outer-WB, Inner-NC), SH=ISH */
#define BAR4_MAIR_MODE_INNER_NC_OSH    3ULL /* Custom MAIR slot (0xF4 Outer-WB, Inner-NC), SH=OSH */
#define BAR4_MAIR_MODE_INNER_NC_NSH    4ULL /* Custom MAIR slot (0xF4 Outer-WB, Inner-NC), SH=NSH */

struct bar4_mair_info {
	__u64 mair_orig;
	__u64 mair_curr;
	__u32 online_cpus;
	__u32 all_cpus_equal;
	__u32 custom_active;
	__u32 custom_slot;
	__u32 custom_attr;
	__u32 page_size;
	__u64 ttbr0_cnp;
	__u64 ttbr1_cnp;
};

struct bar4_mair_bind {
	__u64 uaddr;    /* Input: 4 MiB-aligned user VA of BAR4 slot 15 buffer */
	__u64 len;      /* Input: must equal BAR4_MAIR_SLOT_BYTES (4 MiB) */
	__u64 phys_out; /* Output: verified contiguous 4 MiB-aligned physical base */
};

struct bar4_mair_translate {
	__u64 uaddr;   /* Input: user VA to inspect via AT S1E0R */
	__u64 par_el1; /* Output: raw PAR_EL1 value (bits 63:56 = ATTR, 8:7 = SH, 0 = F) */
};

#define BAR4_MAIR_IOC_GET_INFO     _IOR(BAR4_MAIR_MAGIC, 1, struct bar4_mair_info)
#define BAR4_MAIR_IOC_BIND         _IOWR(BAR4_MAIR_MAGIC, 2, struct bar4_mair_bind)
#define BAR4_MAIR_IOC_CLEAN_INVAL  _IO(BAR4_MAIR_MAGIC, 3)
#define BAR4_MAIR_IOC_TRANSLATE_VA _IOWR(BAR4_MAIR_MAGIC, 4, struct bar4_mair_translate)

#endif /* _BAR4_MAIR_UAPI_H */
