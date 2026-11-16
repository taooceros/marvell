// SPDX-License-Identifier: GPL-2.0
/*
 * pem_wc: map OCTEON 10 (CN10K) PEM0 outbound regions into user space as
 * write-combining (Normal Non-cacheable) memory, so that a core's contiguous
 * stores may be gathered into one large PCIe write. /dev/mem maps them
 * Device-nGnRnE, which the core never gathers (every store becomes its own
 * <= 16 B write).
 *
 * PEM0 outbound region i (PEM0_REG_NORM_ACC(i)) is the 2 GiB of core physical
 * address space at PEM0_OUTBOUND_BASE + i * 2 GiB. mmap() /dev/pem_wc with
 * MAP_SHARED at file offset i * 2 GiB + off; only regions first_region..255
 * may be mapped. Store only: Normal memory permits speculative reads, so map a
 * region only while it points at host memory the host has mapped.
 */
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>

#define PEM0_OUTBOUND_BASE 0x880000000000ULL
#define REGION_SHIFT 31
#define REGIONS 256

static unsigned int first_region = 248;
module_param(first_region, uint, 0444);
MODULE_PARM_DESC(first_region, "lowest PEM0 outbound region that may be mapped (default 248)");

static int pem_wc_mmap(struct file *file, struct vm_area_struct *vma)
{
	u64 off = (u64)vma->vm_pgoff << PAGE_SHIFT;
	u64 len = vma->vm_end - vma->vm_start;
	u64 lo = (u64)first_region << REGION_SHIFT;
	u64 hi = (u64)REGIONS << REGION_SHIFT;

	if (!(vma->vm_flags & VM_SHARED))
		return -EINVAL;
	if (off < lo || off >= hi || len > hi - off)
		return -EINVAL;
	vma->vm_page_prot = pgprot_writecombine(vma->vm_page_prot);
	return io_remap_pfn_range(vma, vma->vm_start, (PEM0_OUTBOUND_BASE + off) >> PAGE_SHIFT, len,
				  vma->vm_page_prot);
}

static const struct file_operations pem_wc_fops = {
	.owner = THIS_MODULE,
	.mmap = pem_wc_mmap,
};

static struct miscdevice pem_wc_dev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "pem_wc",
	.fops = &pem_wc_fops,
	.mode = 0600,
};

module_misc_device(pem_wc_dev);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Write-combining mmap of CN10K PEM0 outbound regions");
