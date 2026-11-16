/* Headers bindgen parses. DPDK's fast path (rx/tx burst, mbuf alloc/free,
 * rdtsc) is static inline; bindgen's wrap_static_fns turns every allowlisted
 * static inline function, including the helpers below, into an exported C
 * symbol that build.rs compiles with DPDK's own CFLAGS.
 */
#include <rte_cycles.h>
#include <rte_dmadev.h>
#include <rte_eal.h>
#include <rte_errno.h>
#include <rte_ethdev.h>
#include <rte_launch.h>
#include <rte_lcore.h>
#include <rte_mbuf.h>
#include <rte_memory.h>
#include <rte_memzone.h>

/* rte_errno is a per-lcore TLS variable. */
static inline int dpdk_sys_errno(void) { return rte_errno; }

/* Macros, kept in C so field layout never leaks into Rust. */
static inline uint8_t *dpdk_sys_mbuf_data(struct rte_mbuf *m) { return rte_pktmbuf_mtod(m, uint8_t *); }
static inline uint16_t dpdk_sys_mbuf_data_len(const struct rte_mbuf *m) { return rte_pktmbuf_data_len(m); }
static inline uint16_t dpdk_sys_mbuf_default_buf_size(void) { return RTE_MBUF_DEFAULT_BUF_SIZE; }
static inline unsigned int dpdk_sys_max_lcore(void) { return RTE_MAX_LCORE; }

/* Physically contiguous, `align`-aligned memzone for buffers a device reaches by address. */
static inline const struct rte_memzone *
dpdk_sys_memzone_reserve_contig(const char *name, size_t len, int socket, unsigned int align)
{
	return rte_memzone_reserve_aligned(name, len, socket, RTE_MEMZONE_IOVA_CONTIG, align);
}
static inline void *dpdk_sys_memzone_addr(const struct rte_memzone *mz) { return mz->addr; }
static inline uint64_t dpdk_sys_memzone_iova(const struct rte_memzone *mz) { return mz->iova; }

/* One-vchan dmadev between local memory and the memory of a PCIe host behind
 * PCIe core `coreid`, acting as host function (pfid, vfid); vfid 0 = the PF
 * itself. `to_host`: copies write the host (MEM_TO_DEV, posted writes);
 * otherwise they read it (DEV_TO_MEM, PCIe reads). The port is a bitfield
 * union, so it is filled here rather than in Rust.
 */
static inline int
dpdk_sys_dma_setup_pcie(int16_t dev, uint16_t nb_desc, bool to_host, uint8_t coreid, uint8_t pfid,
			uint16_t vfid)
{
	struct rte_dma_conf conf = { .nb_vchans = 1 };
	struct rte_dma_vchan_conf qconf = {
		.direction = to_host ? RTE_DMA_DIR_MEM_TO_DEV : RTE_DMA_DIR_DEV_TO_MEM,
		.nb_desc = nb_desc,
	};
	struct rte_dma_port_param *port = to_host ? &qconf.dst_port : &qconf.src_port;
	int rc;

	port->port_type = RTE_DMA_PORT_PCIE;
	port->pcie.coreid = coreid;
	port->pcie.pfid = pfid;
	port->pcie.vfen = vfid != 0;
	port->pcie.vfid = vfid;
	rc = rte_dma_configure(dev, &conf);
	return rc < 0 ? rc : rte_dma_vchan_setup(dev, 0, &qconf);
}
