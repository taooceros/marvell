// SPDX-License-Identifier: GPL-2.0
/*
 * bar4_mair: minimal, strictly-scoped ARM64 page-table attribute remapper for
 * MRV-235 (CN106 BAR4 slot 15 polling ring memory attribute experiment).
 *
 * Safety invariants:
 * 1. Only remaps a single 4 MiB physically-contiguous RAM buffer owned and
 *    pinned by the calling root process via pin_user_pages() (the MRV-26
 *    leased BAR4 slot 15 hugepage slice). Never touches MMIO or CSR space.
 * 2. By default (enable_custom_mair=0), performs ZERO MAIR_EL1 modification:
 *    it reports live MAIR_EL1 across all online CPUs and supports standard
 *    MT_NORMAL (0xFF) and standard Linux MT_NORMAL_NC (0x44, pgprot_writecombine).
 * 3. Only when enable_custom_mair=1 AND all online CPUs hold identical MAIR_EL1
 *    AND the chosen slot (restricted to 5..7, outside Linux slots 0..4) is
 *    verified to be 0x00 on every online CPU, it writes custom_mair_attr
 *    (default 0xF4 = Outer WB-RWA, Inner NC) via on_each_cpu() with ISB + TLBI.
 * 4. On rmmod (only reachable after all open fds and mmaps are closed/unmapped),
 *    it flushes TLBs, restores the exact original MAIR_EL1 on every online CPU
 *    via on_each_cpu(), and verifies bit-exact restoration across all CPUs.
 */

#include <linux/cpumask.h>
#include <linux/fs.h>
#include <linux/irqflags.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/smp.h>
#include <linux/uaccess.h>

#include <asm/barrier.h>
#include <asm/pgtable.h>
#include <asm/sysreg.h>
#include <asm/tlbflush.h>

#include "bar4_mair_uapi.h"

#define BAR4_SLOT_PAGES (BAR4_MAIR_SLOT_BYTES >> PAGE_SHIFT)

static bool enable_custom_mair;
module_param(enable_custom_mair, bool, 0444);
MODULE_PARM_DESC(enable_custom_mair,
		 "Program an unused MAIR_EL1 slot (5..7) to custom_mair_attr (default false)");

static unsigned int custom_mair_slot = 5;
module_param(custom_mair_slot, uint, 0444);
MODULE_PARM_DESC(custom_mair_slot,
		 "MAIR_EL1 slot index to use when enable_custom_mair=1 (must be 5..7 and currently 0x00)");

static unsigned int custom_mair_attr = 0xf4;
module_param(custom_mair_attr, uint, 0444);
MODULE_PARM_DESC(custom_mair_attr,
		 "8-bit MAIR attribute byte (default 0xf4 = Outer WB-RWA, Inner NC)");

struct cpu_mair_snap {
	u64 mair[NR_CPUS];
	u64 ttbr0[NR_CPUS];
	u64 ttbr1[NR_CPUS];
};

static u64 g_mair_orig;
static u64 g_mair_curr;
static u32 g_online_cpus;
static bool g_all_equal;
static bool g_custom_active;
static u64 g_ttbr0_cnp;
static u64 g_ttbr1_cnp;

static void read_mair_cb(void *info)
{
	struct cpu_mair_snap *snap = info;
	int cpu = smp_processor_id();

	snap->mair[cpu] = read_sysreg(mair_el1);
	snap->ttbr0[cpu] = read_sysreg(ttbr0_el1);
	snap->ttbr1[cpu] = read_sysreg(ttbr1_el1);
}

static void write_mair_cb(void *info)
{
	u64 val = *(const u64 *)info;

	write_sysreg(val, mair_el1);
	isb();
	local_flush_tlb_all();
}

static bool snapshot_all_cpus(u64 *out_mair, u32 *out_cnt, u64 *out_cnp0, u64 *out_cnp1)
{
	struct cpu_mair_snap *snap;
	int cpu;
	bool first = true;
	bool equal = true;
	u64 ref = 0;
	u32 cnt = 0;
	u64 cnp0 = 0, cnp1 = 0;

	snap = kzalloc(sizeof(*snap), GFP_KERNEL);
	if (!snap)
		return false;

	on_each_cpu(read_mair_cb, snap, 1);

	for_each_online_cpu(cpu) {
		cnt++;
		if (first) {
			ref = snap->mair[cpu];
			first = false;
		} else if (snap->mair[cpu] != ref) {
			equal = false;
			pr_warn("bar4_mair: CPU%d MAIR_EL1=0x%016llx differs from ref 0x%016llx\n",
				cpu, snap->mair[cpu], ref);
		}
		if (snap->ttbr0[cpu] & TTBR_CNP_BIT)
			cnp0 |= BIT_ULL(cpu);
		if (snap->ttbr1[cpu] & TTBR_CNP_BIT)
			cnp1 |= BIT_ULL(cpu);
	}

	kfree(snap);
	*out_mair = ref;
	*out_cnt = cnt;
	if (out_cnp0)
		*out_cnp0 = cnp0;
	if (out_cnp1)
		*out_cnp1 = cnp1;
	return equal;
}

struct bar4_mair_ctx {
	struct mutex lock;
	bool bound;
	struct page **pages;
	unsigned long nr_pages;
	unsigned long pfn0;
	phys_addr_t phys_base;
};

static int bar4_mair_open(struct inode *inode, struct file *file)
{
	struct bar4_mair_ctx *ctx;

	ctx = kzalloc(sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;

	mutex_init(&ctx->lock);
	file->private_data = ctx;
	return 0;
}

static int bar4_mair_release(struct inode *inode, struct file *file)
{
	struct bar4_mair_ctx *ctx = file->private_data;

	if (!ctx)
		return 0;

	if (ctx->bound && ctx->pages) {
		unpin_user_pages(ctx->pages, ctx->nr_pages);
		kvfree(ctx->pages);
		ctx->pages = NULL;
		ctx->bound = false;
	}
	kfree(ctx);
	return 0;
}

static void clean_inval_pages_poc(struct page **pages, unsigned long nr_pages)
{
	unsigned long i, off;
	unsigned long ctr, dminline;

	ctr = read_cpuid_cachetype();
	dminline = 4UL << ((ctr >> 16) & 0xf);
	if (dminline < 16 || dminline > PAGE_SIZE)
		dminline = 64;

	for (i = 0; i < nr_pages; i++) {
		unsigned long kaddr = (unsigned long)page_address(pages[i]);

		for (off = 0; off < PAGE_SIZE; off += dminline)
			asm volatile("dc civac, %0" : : "r"(kaddr + off) : "memory");
	}
	dsb(sy);
	isb();
}

static long bar4_mair_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
	struct bar4_mair_ctx *ctx = file->private_data;
	int ret = 0;

	switch (cmd) {
	case BAR4_MAIR_IOC_GET_INFO: {
		struct bar4_mair_info info = {
			.mair_orig = g_mair_orig,
			.mair_curr = g_mair_curr,
			.online_cpus = g_online_cpus,
			.all_cpus_equal = g_all_equal ? 1 : 0,
			.custom_active = g_custom_active ? 1 : 0,
			.custom_slot = custom_mair_slot,
			.custom_attr = custom_mair_attr,
			.page_size = PAGE_SIZE,
			.ttbr0_cnp = g_ttbr0_cnp,
			.ttbr1_cnp = g_ttbr1_cnp,
		};

		if (copy_to_user((void __user *)arg, &info, sizeof(info)))
			return -EFAULT;
		return 0;
	}

	case BAR4_MAIR_IOC_BIND: {
		struct bar4_mair_bind req;
		struct page **pages;
		long pinned;
		unsigned long i, pfn0;

		if (copy_from_user(&req, (void __user *)arg, sizeof(req)))
			return -EFAULT;

		if (req.len != BAR4_MAIR_SLOT_BYTES)
			return -EINVAL;
		if ((req.uaddr & (BAR4_MAIR_SLOT_BYTES - 1)) != 0)
			return -EINVAL;

		mutex_lock(&ctx->lock);
		if (ctx->bound) {
			mutex_unlock(&ctx->lock);
			return -EBUSY;
		}

		pages = kvcalloc(BAR4_SLOT_PAGES, sizeof(*pages), GFP_KERNEL);
		if (!pages) {
			mutex_unlock(&ctx->lock);
			return -ENOMEM;
		}

		mmap_read_lock(current->mm);
		pinned = pin_user_pages(req.uaddr, BAR4_SLOT_PAGES,
					FOLL_WRITE | FOLL_LONGTERM, pages);
		mmap_read_unlock(current->mm);

		if (pinned != (long)BAR4_SLOT_PAGES) {
			if (pinned > 0)
				unpin_user_pages(pages, pinned);
			kvfree(pages);
			mutex_unlock(&ctx->lock);
			return pinned < 0 ? pinned : -EFAULT;
		}

		pfn0 = page_to_pfn(pages[0]);
		if (((u64)pfn0 << PAGE_SHIFT) & (BAR4_MAIR_SLOT_BYTES - 1)) {
			ret = -EINVAL;
			goto bind_err;
		}
		for (i = 0; i < BAR4_SLOT_PAGES; i++) {
			if (!pfn_valid(pfn0 + i) || page_to_pfn(pages[i]) != pfn0 + i) {
				ret = -EINVAL;
				goto bind_err;
			}
		}

		ctx->pages = pages;
		ctx->nr_pages = BAR4_SLOT_PAGES;
		ctx->pfn0 = pfn0;
		ctx->phys_base = (phys_addr_t)pfn0 << PAGE_SHIFT;
		ctx->bound = true;

		req.phys_out = (u64)ctx->phys_base;
		mutex_unlock(&ctx->lock);

		if (copy_to_user((void __user *)arg, &req, sizeof(req)))
			return -EFAULT;
		return 0;

bind_err:
		unpin_user_pages(pages, BAR4_SLOT_PAGES);
		kvfree(pages);
		mutex_unlock(&ctx->lock);
		return ret;
	}

	case BAR4_MAIR_IOC_CLEAN_INVAL: {
		mutex_lock(&ctx->lock);
		if (!ctx->bound) {
			mutex_unlock(&ctx->lock);
			return -EINVAL;
		}
		clean_inval_pages_poc(ctx->pages, ctx->nr_pages);
		mutex_unlock(&ctx->lock);
		return 0;
	}

	case BAR4_MAIR_IOC_TRANSLATE_VA: {
		struct bar4_mair_translate tr;
		unsigned long flags;
		u64 par;

		if (copy_from_user(&tr, (void __user *)arg, sizeof(tr)))
			return -EFAULT;

		local_irq_save(flags);
		asm volatile(
			"at s1e0r, %1\n"
			"isb\n"
			"mrs %0, par_el1\n"
			: "=r"(par)
			: "r"(tr.uaddr)
			: "memory"
		);
		local_irq_restore(flags);

		tr.par_el1 = par;
		if (copy_to_user((void __user *)arg, &tr, sizeof(tr)))
			return -EFAULT;
		return 0;
	}

	default:
		return -ENOTTY;
	}
}

static int bar4_mair_mmap(struct file *file, struct vm_area_struct *vma)
{
	struct bar4_mair_ctx *ctx = file->private_data;
	u64 off = (u64)vma->vm_pgoff << PAGE_SHIFT;
	u64 len = vma->vm_end - vma->vm_start;
	u64 mode, sub_off;
	pgprot_t prot;
	unsigned long pfn;
	int ret;

	if (!(vma->vm_flags & VM_SHARED))
		return -EINVAL;
	if (len == 0 || len > BAR4_MAIR_SLOT_BYTES)
		return -EINVAL;

	mode = off / BAR4_MAIR_SLOT_BYTES;
	sub_off = off % BAR4_MAIR_SLOT_BYTES;
	if (sub_off + len > BAR4_MAIR_SLOT_BYTES)
		return -EINVAL;

	mutex_lock(&ctx->lock);
	if (!ctx->bound) {
		mutex_unlock(&ctx->lock);
		return -EINVAL;
	}
	pfn = ctx->pfn0 + (sub_off >> PAGE_SHIFT);

	prot = vma->vm_page_prot;
	switch (mode) {
	case BAR4_MAIR_MODE_NORMAL_WB:
		/* Standard MT_NORMAL (0xFF), ISH */
		prot = __pgprot_modify(prot, PTE_ATTRINDX_MASK,
				       PTE_ATTRINDX(MT_NORMAL) | PTE_PXN | PTE_UXN);
		break;

	case BAR4_MAIR_MODE_NORMAL_NC:
		/* Standard Linux MT_NORMAL_NC (0x44), ZERO MAIR modification */
		prot = pgprot_writecombine(prot);
		break;

	case BAR4_MAIR_MODE_INNER_NC_ISH:
		if (!g_custom_active) {
			mutex_unlock(&ctx->lock);
			return -EPERM;
		}
		prot = __pgprot_modify(prot, PTE_ATTRINDX_MASK | PTE_SHARED,
				       PTE_ATTRINDX(custom_mair_slot) |
				       PTE_SHARED | PTE_PXN | PTE_UXN);
		break;

	case BAR4_MAIR_MODE_INNER_NC_OSH:
		if (!g_custom_active) {
			mutex_unlock(&ctx->lock);
			return -EPERM;
		}
		prot = __pgprot_modify(prot, PTE_ATTRINDX_MASK | PTE_SHARED,
				       PTE_ATTRINDX(custom_mair_slot) |
				       (_AT(pteval_t, 2) << 8) | PTE_PXN | PTE_UXN);
		break;

	case BAR4_MAIR_MODE_INNER_NC_NSH:
		if (!g_custom_active) {
			mutex_unlock(&ctx->lock);
			return -EPERM;
		}
		prot = __pgprot_modify(prot, PTE_ATTRINDX_MASK | PTE_SHARED,
				       PTE_ATTRINDX(custom_mair_slot) |
				       PTE_PXN | PTE_UXN);
		break;

	default:
		mutex_unlock(&ctx->lock);
		return -EINVAL;
	}

	vma->vm_page_prot = prot;
	ret = remap_pfn_range(vma, vma->vm_start, pfn, len, prot);
	mutex_unlock(&ctx->lock);
	return ret;
}

static ssize_t bar4_mair_read(struct file *file, char __user *buf, size_t count, loff_t *ppos)
{
	char tmp[256];
	int len;

	len = scnprintf(tmp, sizeof(tmp),
			"online_cpus=%u all_equal=%u mair_orig=0x%016llx mair_curr=0x%016llx custom_active=%u custom_slot=%u custom_attr=0x%02x page_size=%lu ttbr0_cnp=0x%llx ttbr1_cnp=0x%llx\n",
			g_online_cpus, g_all_equal ? 1 : 0,
			g_mair_orig, g_mair_curr,
			g_custom_active ? 1 : 0, custom_mair_slot, custom_mair_attr,
			(unsigned long)PAGE_SIZE, g_ttbr0_cnp, g_ttbr1_cnp);
	return simple_read_from_buffer(buf, count, ppos, tmp, len);
}

static const struct file_operations bar4_mair_fops = {
	.owner = THIS_MODULE,
	.open = bar4_mair_open,
	.release = bar4_mair_release,
	.read = bar4_mair_read,
	.unlocked_ioctl = bar4_mair_ioctl,
	.mmap = bar4_mair_mmap,
};

static struct miscdevice bar4_mair_dev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "bar4_mair",
	.fops = &bar4_mair_fops,
	.mode = 0600,
};

static int __init bar4_mair_init(void)
{
	int ret;

	g_all_equal = snapshot_all_cpus(&g_mair_orig, &g_online_cpus,
					&g_ttbr0_cnp, &g_ttbr1_cnp);
	g_mair_curr = g_mair_orig;
	g_custom_active = false;

	pr_info("bar4_mair: live MAIR_EL1=0x%016llx online_cpus=%u all_equal=%d ttbr0_cnp=0x%llx ttbr1_cnp=0x%llx\n",
		g_mair_orig, g_online_cpus, g_all_equal, g_ttbr0_cnp, g_ttbr1_cnp);

	if (enable_custom_mair) {
		u8 cur_byte;
		u64 mair_new, verify_mair;
		u32 verify_cnt;
		bool verify_eq;

		if (!g_all_equal) {
			pr_err("bar4_mair: refusing custom MAIR: CPUs have unequal MAIR_EL1\n");
			return -EPERM;
		}
		if (custom_mair_slot < 5 || custom_mair_slot > 7) {
			pr_err("bar4_mair: refusing custom_mair_slot=%u (must be 5..7)\n",
			       custom_mair_slot);
			return -EINVAL;
		}
		if (custom_mair_attr > 0xff) {
			pr_err("bar4_mair: invalid custom_mair_attr=0x%x\n", custom_mair_attr);
			return -EINVAL;
		}
		cur_byte = (g_mair_orig >> (custom_mair_slot * 8)) & 0xff;
		if (cur_byte != 0x00) {
			pr_err("bar4_mair: refusing custom_mair_slot=%u: slot in use (0x%02x != 0x00)\n",
			       custom_mair_slot, cur_byte);
			return -EBUSY;
		}

		mair_new = (g_mair_orig & ~(0xffULL << (custom_mair_slot * 8))) |
			   ((u64)custom_mair_attr << (custom_mair_slot * 8));

		on_each_cpu(write_mair_cb, &mair_new, 1);
		flush_tlb_all();

		verify_eq = snapshot_all_cpus(&verify_mair, &verify_cnt, NULL, NULL);
		if (!verify_eq || verify_mair != mair_new) {
			pr_err("bar4_mair: verify after write failed (eq=%d val=0x%016llx), restoring!\n",
			       verify_eq, verify_mair);
			on_each_cpu(write_mair_cb, &g_mair_orig, 1);
			flush_tlb_all();
			return -EIO;
		}

		g_mair_curr = mair_new;
		g_custom_active = true;
		pr_info("bar4_mair: programmed slot %u = 0x%02x -> MAIR_EL1=0x%016llx on all %u CPUs\n",
			custom_mair_slot, custom_mair_attr, g_mair_curr, verify_cnt);
	}

	ret = misc_register(&bar4_mair_dev);
	if (ret) {
		if (g_custom_active) {
			flush_tlb_all();
			on_each_cpu(write_mair_cb, &g_mair_orig, 1);
			flush_tlb_all();
			g_custom_active = false;
		}
		return ret;
	}
	return 0;
}

static void __exit bar4_mair_exit(void)
{
	misc_deregister(&bar4_mair_dev);

	if (g_custom_active) {
		u64 verify_mair = 0;
		u32 verify_cnt = 0;
		bool verify_eq;

		flush_tlb_all();
		on_each_cpu(write_mair_cb, &g_mair_orig, 1);
		flush_tlb_all();

		verify_eq = snapshot_all_cpus(&verify_mair, &verify_cnt, NULL, NULL);
		g_custom_active = false;
		g_mair_curr = verify_mair;

		pr_info("bar4_mair: unloaded; restored MAIR_EL1=0x%016llx on all %u CPUs (equal=%d exact_match=%d)\n",
			verify_mair, verify_cnt, verify_eq, verify_mair == g_mair_orig);
	} else {
		pr_info("bar4_mair: unloaded (MAIR_EL1 untouched: 0x%016llx)\n", g_mair_orig);
	}
}

module_init(bar4_mair_init);
module_exit(bar4_mair_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Scoped BAR4 slot 15 ARM64 MAIR page-table attribute remapper for MRV-235");
