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
#ifdef __x86_64__
#include <bus_pci_driver.h>
#endif
#include <rte_launch.h>
#include <rte_lcore.h>
#include <rte_mbuf.h>
#include <rte_memory.h>
#include <rte_memzone.h>
#include <rte_mempool.h>

/* rte_errno is a per-lcore TLS variable. */
static inline int dpdk_sys_errno(void) { return rte_errno; }

/* Macros, kept in C so field layout never leaks into Rust. */
static inline uint8_t *dpdk_sys_mbuf_data(struct rte_mbuf *m) { return rte_pktmbuf_mtod(m, uint8_t *); }
static inline uint16_t dpdk_sys_mbuf_data_len(const struct rte_mbuf *m) { return rte_pktmbuf_data_len(m); }
static inline uint32_t dpdk_sys_mbuf_pkt_len(const struct rte_mbuf *m) { return rte_pktmbuf_pkt_len(m); }
static inline uint16_t dpdk_sys_mbuf_nb_segs(const struct rte_mbuf *m) { return m->nb_segs; }
static inline uint64_t dpdk_sys_mbuf_ol_flags(const struct rte_mbuf *m) { return m->ol_flags; }
static inline struct rte_mbuf *dpdk_sys_mbuf_next(struct rte_mbuf *m) { return m->next; }
static inline uint16_t dpdk_sys_mbuf_buf_len(const struct rte_mbuf *m) { return m->buf_len; }
static inline uint16_t dpdk_sys_mbuf_data_off(const struct rte_mbuf *m) { return m->data_off; }
static inline uint8_t *dpdk_sys_mbuf_buf_addr(struct rte_mbuf *m) { return (uint8_t *)m->buf_addr; }
static inline uint64_t dpdk_sys_mbuf_iova(const struct rte_mbuf *m) { return rte_pktmbuf_iova(m); }
static inline uint16_t dpdk_sys_mbuf_default_buf_size(void) { return RTE_MBUF_DEFAULT_BUF_SIZE; }
static inline unsigned int dpdk_sys_max_lcore(void) { return RTE_MAX_LCORE; }
static inline unsigned int dpdk_sys_mempool_in_use_count(const struct rte_mempool *mp) {
	return rte_mempool_in_use_count(mp);
}
static inline int dpdk_sys_pktmbuf_alloc_bulk(struct rte_mempool *mp, struct rte_mbuf **mbufs, unsigned int count) {
	return rte_pktmbuf_alloc_bulk(mp, mbufs, count);
}
static inline void dpdk_sys_pktmbuf_set_len(struct rte_mbuf *m, uint16_t len) {
	m->data_len = len;
	m->pkt_len = len;
}
static inline unsigned int dpdk_sys_mempool_avail_count(const struct rte_mempool *mp) {
	return rte_mempool_avail_count(mp);
}

/* Chain `tail` onto `head` for multi-segment TX frames (MTU=9000 scatter/gather). */
static inline int dpdk_sys_mbuf_chain(struct rte_mbuf *head, struct rte_mbuf *tail) {
	return rte_pktmbuf_chain(head, tail);
}
/* Trim `len` bytes from the end of `m`. */
static inline int dpdk_sys_mbuf_trim(struct rte_mbuf *m, uint16_t len) {
	return rte_pktmbuf_trim(m, len);
}
/* Copy full multi-segment mbuf payload into linear buffer `dst` (up to `cap`). */
static inline uint32_t dpdk_sys_mbuf_read_linear(const struct rte_mbuf *m, uint8_t *dst, uint32_t cap) {
	uint32_t copied = 0;
	const struct rte_mbuf *seg = m;
	while (seg && copied < cap) {
		uint32_t n = seg->data_len;
		if (copied + n > cap)
			n = cap - copied;
		const uint8_t *src = rte_pktmbuf_mtod(seg, const uint8_t *);
		for (uint32_t i = 0; i < n; i++)
			dst[copied + i] = src[i];
		copied += n;
		seg = seg->next;
	}
	return copied;
}

/* Stamp every 8-byte word across the entire data room of every segment in `m`
 * with a pre-free poison pattern to catch any CQE-before-payload DMA race.
 */
static inline void dpdk_sys_mbuf_poison_all_segs(struct rte_mbuf *m, uint64_t tag) {
	struct rte_mbuf *seg = m;
	uint64_t seg_idx = 0;
	while (seg) {
		uint8_t *base = (uint8_t *)seg->buf_addr;
		uint32_t blen = seg->buf_len;
		uint64_t *p = (uint64_t *)base;
		uint32_t nwords = blen >> 3;
		uint64_t pat = 0xDEAD0000BEEF0000ULL ^ (tag << 16) ^ (seg_idx << 8);
		for (uint32_t i = 0; i < nwords; i++) {
			p[i] = pat ^ (uint64_t)i;
		}
		seg = seg->next;
		seg_idx++;
	}
}

/* Physically contiguous, `align`-aligned memzone for buffers a device reaches by address. */
static inline const struct rte_memzone *
dpdk_sys_memzone_reserve_contig(const char *name, size_t len, int socket, unsigned int align)
{
	return rte_memzone_reserve_aligned(name, len, socket, RTE_MEMZONE_IOVA_CONTIG, align);
}
static inline void *dpdk_sys_memzone_addr(const struct rte_memzone *mz) { return mz->addr; }
static inline uint64_t dpdk_sys_memzone_iova(const struct rte_memzone *mz) { return mz->iova; }

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
/* Two-vchan bidirectional dmadev on a single DPI VF:
 *   vchan 0 = ToHost  (RTE_DMA_DIR_MEM_TO_DEV, posted PCIe writes)
 *   vchan 1 = FromHost (RTE_DMA_DIR_DEV_TO_MEM, PCIe reads)
 */
static inline int
dpdk_sys_dma_setup_pcie_bidir(int16_t dev, uint16_t nb_desc, uint8_t coreid, uint8_t pfid,
			      uint16_t vfid)
{
	struct rte_dma_conf conf = { .nb_vchans = 2 };
	struct rte_dma_vchan_conf qto = {
		.direction = RTE_DMA_DIR_MEM_TO_DEV,
		.nb_desc = nb_desc,
	};
	struct rte_dma_vchan_conf qfrom = {
		.direction = RTE_DMA_DIR_DEV_TO_MEM,
		.nb_desc = nb_desc,
	};
	int rc;

	qto.dst_port.port_type = RTE_DMA_PORT_PCIE;
	qto.dst_port.pcie.coreid = coreid;
	qto.dst_port.pcie.pfid = pfid;
	qto.dst_port.pcie.vfen = vfid != 0;
	qto.dst_port.pcie.vfid = vfid;

	qfrom.src_port.port_type = RTE_DMA_PORT_PCIE;
	qfrom.src_port.pcie.coreid = coreid;
	qfrom.src_port.pcie.pfid = pfid;
	qfrom.src_port.pcie.vfen = vfid != 0;
	qfrom.src_port.pcie.vfid = vfid;

	rc = rte_dma_configure(dev, &conf);
	if (rc < 0)
		return rc;
	rc = rte_dma_vchan_setup(dev, 0, &qto);
	if (rc < 0)
		return rc;
	return rte_dma_vchan_setup(dev, 1, &qfrom);
}

static inline int
dpdk_sys_eth_dev_configure_rss(uint16_t port_id, uint16_t nb_rx_q, uint16_t nb_tx_q, bool enable_rss, uint64_t *applied_hf)
{
	struct rte_eth_conf conf;
	memset(&conf, 0, sizeof(conf));
	uint64_t hf = 0;
	if (enable_rss && nb_rx_q > 1) {
		struct rte_eth_dev_info info;
		int rc = rte_eth_dev_info_get(port_id, &info);
		if (rc < 0)
			return rc;
		uint64_t want = RTE_ETH_RSS_NONFRAG_IPV4_UDP | RTE_ETH_RSS_IPV4;
		hf = want & info.flow_type_rss_offloads;
		conf.rxmode.mq_mode = RTE_ETH_MQ_RX_RSS;
		conf.rx_adv_conf.rss_conf.rss_key = NULL;
		conf.rx_adv_conf.rss_conf.rss_key_len = 0;
		conf.rx_adv_conf.rss_conf.rss_hf = hf;
	}
	if (applied_hf)
		*applied_hf = hf;
	return rte_eth_dev_configure(port_id, nb_rx_q, nb_tx_q, &conf);
}
/* Extended port configuration helper (MTU, loopback, scatter, RX cksum, TX multi-seg). */
static inline int
dpdk_sys_eth_dev_configure_ex(uint16_t port_id, uint16_t nb_rx_q, uint16_t nb_tx_q,
			      uint32_t mtu, int lpbk_mode, uint64_t rx_offloads,
			      uint64_t tx_offloads)
{
	struct rte_eth_conf conf;
	memset(&conf, 0, sizeof(conf));
	conf.lpbk_mode = (uint32_t)lpbk_mode;
	conf.rxmode.mtu = mtu;
	conf.rxmode.offloads = rx_offloads;
	conf.txmode.offloads = tx_offloads;
	return rte_eth_dev_configure(port_id, nb_rx_q, nb_tx_q, &conf);
}

static inline uint64_t dpdk_sys_rx_offload_scatter(void) { return RTE_ETH_RX_OFFLOAD_SCATTER; }
static inline uint64_t dpdk_sys_rx_offload_udp_cksum(void) { return RTE_ETH_RX_OFFLOAD_UDP_CKSUM; }
static inline uint64_t dpdk_sys_rx_offload_ipv4_cksum(void) { return RTE_ETH_RX_OFFLOAD_IPV4_CKSUM; }
static inline uint64_t dpdk_sys_rx_offload_keep_crc(void) { return RTE_ETH_RX_OFFLOAD_KEEP_CRC; }
static inline uint64_t dpdk_sys_tx_offload_multi_segs(void) { return RTE_ETH_TX_OFFLOAD_MULTI_SEGS; }

static inline int
dpdk_sys_eth_dev_configure_fast(uint16_t port_id, uint16_t nb_rx_q, uint16_t nb_tx_q,
				bool enable_rss, bool fast_free, uint64_t *applied_hf,
				uint64_t *applied_tx_offloads)
{
	struct rte_eth_conf conf;
	struct rte_eth_dev_info info;
	memset(&conf, 0, sizeof(conf));
	int rc = rte_eth_dev_info_get(port_id, &info);
	if (rc < 0)
		return rc;
	uint64_t hf = 0;
	if (enable_rss && nb_rx_q > 1) {
		uint64_t want = RTE_ETH_RSS_NONFRAG_IPV4_UDP | RTE_ETH_RSS_IPV4;
		hf = want & info.flow_type_rss_offloads;
		conf.rxmode.mq_mode = RTE_ETH_MQ_RX_RSS;
		conf.rx_adv_conf.rss_conf.rss_key = NULL;
		conf.rx_adv_conf.rss_conf.rss_key_len = 0;
		conf.rx_adv_conf.rss_conf.rss_hf = hf;
	}
	uint64_t tx_off = 0;
	if (fast_free && (info.tx_offload_capa & RTE_ETH_TX_OFFLOAD_MBUF_FAST_FREE)) {
		tx_off |= RTE_ETH_TX_OFFLOAD_MBUF_FAST_FREE;
	}
	conf.txmode.offloads = tx_off;
	if (applied_hf)
		*applied_hf = hf;
	if (applied_tx_offloads)
		*applied_tx_offloads = tx_off;
	return rte_eth_dev_configure(port_id, nb_rx_q, nb_tx_q, &conf);
}

static inline int
dpdk_sys_eth_dev_tx_queue_setup_fast(uint16_t port_id, uint16_t tx_queue_id, uint16_t nb_tx_desc,
				     unsigned int socket_id, uint64_t tx_offloads)
{
	struct rte_eth_dev_info info;
	int rc = rte_eth_dev_info_get(port_id, &info);
	if (rc < 0)
		return rc;
	struct rte_eth_txconf txq_conf = info.default_txconf;
	txq_conf.offloads = tx_offloads;
	return rte_eth_tx_queue_setup(port_id, tx_queue_id, nb_tx_desc, socket_id, &txq_conf);
}

static inline int dpdk_sys_eth_disable_flow_ctrl(uint16_t port_id)
{
	struct rte_eth_fc_conf fc;
	memset(&fc, 0, sizeof(fc));
	int rc = rte_eth_dev_flow_ctrl_get(port_id, &fc);
	if (rc < 0)
		return rc;
	fc.mode = RTE_ETH_FC_NONE;
	fc.autoneg = 0;
	return rte_eth_dev_flow_ctrl_set(port_id, &fc);
}

/* BAR0 virtual address of a started PCI ethdev port (x86_64 host MMIO probes). */
static inline void *dpdk_sys_port_bar0(uint16_t port)
{
#ifdef __x86_64__
	struct rte_eth_dev_info info;
	if (rte_eth_dev_info_get(port, &info) != 0 || info.device == NULL)
		return NULL;
	return RTE_DEV_TO_PCI(info.device)->mem_resource[0].addr;
#else
	(void)port;
	return NULL;
#endif
}

/* ---------------------------------------------------------------------------
 * MRV-161 / MRV-170: CN106 NIX0 coherent CQE peek / direct / hardened direct
 * helpers (opt-in experiment wrappers; zero modifications to marvell-dpdk).
 *
 * Layout of `struct cn10k_eth_rxq` prefix verified byte-for-byte against
 * sysroot-card `dpdk-24.11-cn10k` (`librte_net_cnxk.a`) and
 * `marvell-dpdk/drivers/net/cnxk/cn10k_rxtx.h:70-87`.
 * --------------------------------------------------------------------------- */
struct dpdk_sys_cn10k_rxq_info {
	uint64_t mbuf_initializer;
	uint64_t desc;
	uint64_t cq_door;
	uint64_t wdata;
	uint64_t cq_status;
	uint32_t head;
	uint32_t qmask;
	uint32_t available;
	uint16_t data_off;
	uint32_t init_nonzero_w0;
	uint32_t init_nonzero_w9;
	uint32_t init_nonzero_w10_15;
};

struct dpdk_sys_cq_oracle {
	uint64_t raw_reg;
	uint32_t hw_head;
	uint32_t hw_tail;
	uint32_t sw_head;
	uint32_t sw_avail;
	uint32_t cq_err;
	uint32_t op_err;
	uint32_t free_dirty_w0;
	uint32_t free_dirty_w9;
	uint32_t free_dirty_w10_15;
	uint32_t hw_pending;
};

struct dpdk_sys_cqe_peek_stats {
	uint64_t calls;
	uint64_t peek_empty;
	uint64_t peek_hit;
	uint64_t peek_hit_ldadda_zero;
	uint64_t ldadda_calls;
	uint64_t ldadda_ticks_total;
	uint64_t pkts_popped;
	uint64_t w9_zero_on_w0_hit;
	uint64_t w2_zero_on_w0_hit;
	uint64_t w9_wait_retries;
	uint64_t cqe_type_hist[16];
	/* MRV-170 hardened direct safety counters */
	uint64_t mseg_pkts;
	uint64_t mseg_total_segs;
	uint64_t w10_15_zero_on_w9_hit;
	uint64_t w10_15_wait_retries;
	uint64_t fallback_ldadda;
	uint64_t w0_ahead_of_tail;
	uint64_t non_rx_cqes;
	uint64_t err_rx_cqes;
	uint64_t zero_seg_err_cqes;
	uint64_t err_pkts_freed;
	uint64_t last_err_w0;
	uint64_t last_err_w1;
	uint64_t last_err_w8;
	uint64_t last_err_w9;
	uint64_t watchdog_ldadda;
	uint64_t watchdog_head_mismatch;
	uint64_t cq_err_seen;
	uint64_t op_err_seen;
	uint64_t pkts_since_watchdog;
	uint32_t fatal_cq_err;
	uint32_t _pad;
};

#ifdef __aarch64__
struct dpdk_sys_cn10k_rxq {
	uint64_t mbuf_initializer; /* +0x00 */
	uintptr_t desc;            /* +0x08 */
	void *lookup_mem;          /* +0x10 */
	uintptr_t cq_door;         /* +0x18 */
	uint64_t wdata;            /* +0x20 */
	int64_t *cq_status;        /* +0x28 */
	uint32_t head;             /* +0x30 */
	uint32_t qmask;            /* +0x34 */
	uint32_t available;        /* +0x38 */
	uint16_t data_off;         /* +0x3c */
};

static inline int
dpdk_sys_cn10k_rx_drop_re_set(uint16_t port_id, int ena)
{
	(void)port_id;
	(void)ena;
	return -95; /* -EOPNOTSUPP without private ethdev_driver.h */
}

static inline void
dpdk_sys_zero_cqe_full(uint64_t *cq)
{
	/* Zero all 16 x u64 (128 B = 2 cache lines) using paired zero stores. */
	asm volatile(
		"stp xzr, xzr, [%0, #0]\n\t"
		"stp xzr, xzr, [%0, #16]\n\t"
		"stp xzr, xzr, [%0, #32]\n\t"
		"stp xzr, xzr, [%0, #48]\n\t"
		"stp xzr, xzr, [%0, #64]\n\t"
		"stp xzr, xzr, [%0, #80]\n\t"
		"stp xzr, xzr, [%0, #96]\n\t"
		"stp xzr, xzr, [%0, #112]\n\t"
		:
		: "r"(cq)
		: "memory");
}

static inline uint64_t
dpdk_sys_ldadda_cq_status(struct dpdk_sys_cn10k_rxq *rxq)
{
	uint64_t reg;
	uint64_t wdata = rxq->wdata;
	asm volatile("ldadda %x[i], %x[r], [%[b]]"
		     : [r] "=r"(reg), "+m"(*rxq->cq_status)
		     : [i] "r"(wdata), [b] "r"(rxq->cq_status)
		     : "memory");
	return reg;
}

static inline int
dpdk_sys_cn10k_rxq_inspect(uint16_t port_id, uint16_t queue_id,
			   struct dpdk_sys_cn10k_rxq_info *out)
{
	struct rte_eth_fp_ops *p = &rte_eth_fp_ops[port_id];
	struct dpdk_sys_cn10k_rxq *rxq = (struct dpdk_sys_cn10k_rxq *)p->rxq.data[queue_id];
	if (!rxq || !out)
		return -1;
	out->mbuf_initializer = rxq->mbuf_initializer;
	out->desc = (uint64_t)rxq->desc;
	out->cq_door = (uint64_t)rxq->cq_door;
	out->wdata = rxq->wdata;
	out->cq_status = (uint64_t)(uintptr_t)rxq->cq_status;
	out->head = rxq->head;
	out->qmask = rxq->qmask;
	out->available = rxq->available;
	out->data_off = rxq->data_off;
	out->init_nonzero_w0 = 0;
	out->init_nonzero_w9 = 0;
	out->init_nonzero_w10_15 = 0;
	for (uint32_t i = 0; i <= rxq->qmask; i++) {
		const uint64_t *cq = (const uint64_t *)(rxq->desc + ((uintptr_t)i << 7));
		if (cq[0] != 0)
			out->init_nonzero_w0++;
		if (cq[9] != 0)
			out->init_nonzero_w9++;
		for (int w = 10; w < 16; w++) {
			if (cq[w] != 0) {
				out->init_nonzero_w10_15++;
				break;
			}
		}
	}
	return 0;
}

/* Oracle check via LDADDA NIX_LF_CQ_OP_STATUS + scan of all free slots [hw_tail..hw_head). */
static inline int
dpdk_sys_cn10k_cq_oracle_check(uint16_t port_id, uint16_t queue_id,
			       struct dpdk_sys_cq_oracle *out)
{
	struct dpdk_sys_cn10k_rxq *rxq =
		(struct dpdk_sys_cn10k_rxq *)rte_eth_fp_ops[port_id].rxq.data[queue_id];
	if (!rxq || !out)
		return -1;
	uint64_t reg = dpdk_sys_ldadda_cq_status(rxq);
	uint32_t qmask = rxq->qmask;
	uint32_t hw_tail = (uint32_t)(reg & 0xFFFFFu);
	uint32_t hw_head = (uint32_t)((reg >> 20) & 0xFFFFFu);
	uint32_t pending = (hw_tail < hw_head) ? (hw_tail - hw_head + qmask + 1) : (hw_tail - hw_head);

	out->raw_reg = reg;
	out->hw_head = hw_head;
	out->hw_tail = hw_tail;
	out->sw_head = rxq->head;
	out->sw_avail = rxq->available;
	out->cq_err = (uint32_t)((reg >> 46) & 1ULL);
	out->op_err = (uint32_t)((reg >> 63) & 1ULL);
	out->hw_pending = pending;
	out->free_dirty_w0 = 0;
	out->free_dirty_w9 = 0;
	out->free_dirty_w10_15 = 0;

	/* Free slots are [hw_tail .. hw_head) mod (qmask+1); if pending==0, all slots are free. */
	uint32_t total = qmask + 1;
	uint32_t nfree = total - pending;
	for (uint32_t k = 0; k < nfree; k++) {
		uint32_t idx = (hw_tail + k) & qmask;
		const uint64_t *cq = (const uint64_t *)(rxq->desc + ((uintptr_t)idx << 7));
		uint64_t w0, w9;
		asm volatile("ldar %0, [%1]" : "=r"(w0) : "r"(cq) : "memory");
		asm volatile("ldar %0, [%1]" : "=r"(w9) : "r"(cq + 9) : "memory");
		if (w0 != 0)
			out->free_dirty_w0++;
		if (w9 != 0)
			out->free_dirty_w9++;
		for (int w = 10; w < 16; w++) {
			if (cq[w] != 0) {
				out->free_dirty_w10_15++;
				break;
			}
		}
	}
	return 0;
}

/* Sync sw_head with authoritative HW head and scrub all free slots [hw_tail..hw_head) across 128 B.
 * Must be called at startup and after any dev_stop/dev_start/rx_queue_stop/flush transition.
 */
static inline int
dpdk_sys_cn10k_cqe_direct_sync(uint16_t port_id, uint16_t queue_id,
			       struct dpdk_sys_cq_oracle *out)
{
	struct dpdk_sys_cn10k_rxq *rxq =
		(struct dpdk_sys_cn10k_rxq *)rte_eth_fp_ops[port_id].rxq.data[queue_id];
	if (!rxq)
		return -1;
	struct dpdk_sys_cq_oracle tmp;
	if (!out)
		out = &tmp;
	dpdk_sys_cn10k_cq_oracle_check(port_id, queue_id, out);
	if (out->cq_err || out->op_err)
		return -2;
	rxq->head = out->hw_head;
	rxq->available = 0;

	uint32_t qmask = rxq->qmask;
	uint32_t total = qmask + 1;
	uint32_t nfree = total - out->hw_pending;
	for (uint32_t k = 0; k < nfree; k++) {
		uint32_t idx = (out->hw_tail + k) & qmask;
		uint64_t *cq = (uint64_t *)(rxq->desc + ((uintptr_t)idx << 7));
		dpdk_sys_zero_cqe_full(cq);
	}
	asm volatile("dmb oshst" ::: "memory");
	return 0;
}

/* Read raw 16 words of CQE at slot `slot_idx` (mod qsize). */
static inline int
dpdk_sys_cn10k_cq_read_slot(uint16_t port_id, uint16_t queue_id, uint32_t slot_idx, uint64_t out[16])
{
	struct dpdk_sys_cn10k_rxq *rxq =
		(struct dpdk_sys_cn10k_rxq *)rte_eth_fp_ops[port_id].rxq.data[queue_id];
	if (!rxq || !out)
		return -1;
	uint32_t idx = slot_idx & rxq->qmask;
	const uint64_t *cq = (const uint64_t *)(rxq->desc + ((uintptr_t)idx << 7));
	for (int i = 0; i < 16; i++) {
		asm volatile("ldar %0, [%1]" : "=r"(out[i]) : "r"(cq + i) : "memory");
	}
	return 0;
}

/* Inject synthetic CQE at current rxq->head (ONLY for controlled isolated B2 deadlock unit test
 * on a stopped/idle RQ where HW will not write to this slot).
 */
static inline int
dpdk_sys_cn10k_cq_write_slot(uint16_t port_id, uint16_t queue_id, uint32_t slot_idx,
			     const uint64_t words[16])
{
	struct dpdk_sys_cn10k_rxq *rxq =
		(struct dpdk_sys_cn10k_rxq *)rte_eth_fp_ops[port_id].rxq.data[queue_id];
	if (!rxq || !words)
		return -1;
	uint32_t idx = slot_idx & rxq->qmask;
	uint64_t *cq = (uint64_t *)(rxq->desc + ((uintptr_t)idx << 7));
	for (int i = 0; i < 16; i++)
		cq[i] = words[i];
	asm volatile("dmb oshst" ::: "memory");
	return 0;
}

/* Controlled destructive test (B3 isolated sub-test): ring cq_door with +1 on an empty CQ to
 * trigger hardware NIX_CQERRINT_DOOR_ERR and inspect whether CQ_OP_STATUS latches CQ_ERR (bit 46).
 */
static inline uint64_t
dpdk_sys_cn10k_cq_trigger_door_err(uint16_t port_id, uint16_t queue_id)
{
	struct dpdk_sys_cn10k_rxq *rxq =
		(struct dpdk_sys_cn10k_rxq *)rte_eth_fp_ops[port_id].rxq.data[queue_id];
	if (!rxq)
		return 0;
	*(volatile uint64_t *)rxq->cq_door = rxq->wdata | 1ULL;
	asm volatile("dsb osh" ::: "memory");
	return dpdk_sys_ldadda_cq_status(rxq);
}

static inline int
dpdk_sys_eth_rx_burst_mode_str(uint16_t port_id, uint16_t queue_id, char *buf, size_t len)
{
	struct rte_eth_burst_mode mode;
	int rc = rte_eth_rx_burst_mode_get(port_id, queue_id, &mode);
	if (rc != 0) {
		if (buf && len > 0)
			buf[0] = '\0';
		return rc;
	}
	if (buf && len > 0) {
		size_t n = 0;
		while (n + 1 < len && n < sizeof(mode.info) && mode.info[n] != '\0') {
			buf[n] = mode.info[n];
			n++;
		}
		buf[n] = '\0';
	}
	return 0;
}

/* Helper: extract multi-segment mbuf chain from a validated CN10K RX CQE (segs 1..6 across w8..w15).
 * Returns head mbuf, or NULL if seg1_addr == 0 or segs == 0.
 * If `check_vis != 0`, uses `ldar` on w10..w15 and counts any transient zero segment address.
 */
static inline struct rte_mbuf *
dpdk_sys_cn10k_extract_mbuf(const uint64_t *cq, uint64_t mbuf_init, uint64_t data_off,
			    uint64_t w1, uint64_t w2, uint64_t w8, uint64_t w9,
			    struct dpdk_sys_cqe_peek_stats *st, int *seg_zero_flag)
{
	uint16_t pkt_len = (uint16_t)(w2 & 0xFFFFu) + 1;
	uint8_t nb_segs = (uint8_t)((w8 >> 48) & 0x3u);
	uint8_t desc_sizem1 = (uint8_t)((w1 >> 12) & 0x1Fu);

	if (unlikely(nb_segs == 0 || w9 == 0)) {
		if (seg_zero_flag)
			*seg_zero_flag = 1;
		return NULL;
	}

	struct rte_mbuf *head = (struct rte_mbuf *)(uintptr_t)(w9 - data_off);
	*(uint64_t *)&head->rearm_data = mbuf_init;
	head->ol_flags = 0;
	head->packet_type = 0;
	head->pkt_len = pkt_len;
	head->next = NULL;

	if (likely(nb_segs == 1 && desc_sizem1 == 0)) {
		head->data_len = pkt_len;
		return head;
	}

	/* Multi-segment path: exact match to marvell-dpdk cn10k_rx.h nix_cqe_xtract_mseg */
	uint64_t sg = w8;
	head->data_len = (uint16_t)(sg & 0xFFFFu);
	head->nb_segs = nb_segs;
	uint64_t later_skip = (uint64_t)((uintptr_t)head->buf_addr - (uintptr_t)head);
	uint64_t later_rearm = mbuf_init & ~0xFFFFULL;

	const uint64_t *eol = (cq + 8) + (((uint64_t)desc_sizem1 + 1ULL) << 1);
	if (eol > cq + 16)
		eol = cq + 16;

	sg >>= 16;
	const uint64_t *iova_list = cq + 10; /* Skip w8 (sg1) and w9 (seg1_addr) */
	uint8_t rem = nb_segs - 1;
	struct rte_mbuf *cur = head;
	uint16_t total_segs = nb_segs;

	while (rem > 0 && iova_list < eol) {
		uint64_t seg_iova;
		asm volatile("ldar %0, [%1]" : "=r"(seg_iova) : "r"(iova_list) : "memory");
		if (unlikely(seg_iova == 0)) {
			if (st)
				st->w10_15_zero_on_w9_hit++;
			/* Bounded spin in case w10..w15 trailed w9 */
			int spun = 0;
			while (seg_iova == 0 && spun < 32) {
				if (st)
					st->w10_15_wait_retries++;
				asm volatile("yield" ::: "memory");
				asm volatile("ldar %0, [%1]" : "=r"(seg_iova) : "r"(iova_list) : "memory");
				spun++;
			}
			if (seg_iova == 0) {
				/* Abort cleanly: unlink partial chain, caller falls back to LDADDA */
				cur->next = NULL;
				if (seg_zero_flag)
					*seg_zero_flag = 1;
				return NULL;
			}
		}
		struct rte_mbuf *nxt = (struct rte_mbuf *)(uintptr_t)(seg_iova - later_skip);
		cur->next = nxt;
		cur = nxt;
		*(uint64_t *)&cur->rearm_data = later_rearm;
		cur->ol_flags = 0;
		cur->packet_type = 0;
		cur->data_len = (uint16_t)(sg & 0xFFFFu);
		cur->next = NULL;

		sg >>= 16;
		rem--;
		iova_list++;

		if (rem == 0 && (iova_list + 1) < eol) {
			uint64_t sg2;
			asm volatile("ldar %0, [%1]" : "=r"(sg2) : "r"(iova_list) : "memory");
			uint8_t n2 = (uint8_t)((sg2 >> 48) & 0x3u);
			if (n2 == 0) {
				if (st)
					st->w10_15_zero_on_w9_hit++;
				int spun = 0;
				while (n2 == 0 && spun < 32) {
					if (st)
						st->w10_15_wait_retries++;
					asm volatile("yield" ::: "memory");
					asm volatile("ldar %0, [%1]" : "=r"(sg2) : "r"(iova_list) : "memory");
					n2 = (uint8_t)((sg2 >> 48) & 0x3u);
					spun++;
				}
				if (n2 == 0) {
					cur->next = NULL;
					if (seg_zero_flag)
						*seg_zero_flag = 1;
					return NULL;
				}
			}
			sg = sg2;
			rem = n2;
			total_segs += n2;
			head->nb_segs = total_segs;
			iova_list++;
		}
	}

	if (st) {
		st->mseg_pkts++;
		st->mseg_total_segs += total_segs;
	}
	return head;
}

/* MRV-161 `--cqe-peek` upgraded with multi-seg support + full 128 B clear so apples-to-apples
 * comparisons against `--cqe-direct-safe` hold on multi-seg too.
 */
static inline uint16_t
dpdk_sys_cn10k_rx_burst_cqe_peek(uint16_t port_id, uint16_t queue_id,
				 struct rte_mbuf **rx_pkts, uint16_t pkts,
				 struct dpdk_sys_cqe_peek_stats *st, int diag)
{
	struct dpdk_sys_cn10k_rxq *rxq =
		(struct dpdk_sys_cn10k_rxq *)rte_eth_fp_ops[port_id].rxq.data[queue_id];
	const uintptr_t desc = rxq->desc;
	const uint32_t qmask = rxq->qmask;
	uint32_t head = rxq->head;
	uint32_t available = rxq->available;

	if (st)
		st->calls++;

	if (available == 0) {
		const uint64_t *cqe = (const uint64_t *)(desc + ((uintptr_t)head << 7));
		uint64_t w0;
		asm volatile("ldar %0, [%1]" : "=r"(w0) : "r"(cqe) : "memory");
		uint64_t ctype = w0 >> 60;
		if (ctype == 0) {
			if (st)
				st->peek_empty++;
			return 0;
		}
		if (st) {
			st->peek_hit++;
			if (diag) {
				st->cqe_type_hist[ctype & 0xf]++;
				uint64_t w9_pre, w2_pre;
				asm volatile("ldar %0, [%1]" : "=r"(w9_pre) : "r"(cqe + 9) : "memory");
				asm volatile("ldar %0, [%1]" : "=r"(w2_pre) : "r"(cqe + 2) : "memory");
				if (w9_pre == 0)
					st->w9_zero_on_w0_hit++;
				if ((w2_pre & 0xffffu) == 0)
					st->w2_zero_on_w0_hit++;
			}
		}
	}

	const uint64_t wdata = rxq->wdata;
	if (available < pkts) {
		int need_ldadda = (available == 0);
		if (!need_ldadda) {
			const uint64_t *next_cqe =
				(const uint64_t *)(desc + ((uintptr_t)((head + available) & qmask) << 7));
			uint64_t nw0;
			asm volatile("ldar %0, [%1]" : "=r"(nw0) : "r"(next_cqe) : "memory");
			need_ldadda = ((nw0 >> 60) != 0);
		}
		if (need_ldadda) {
			uint64_t tl0 = 0;
			if (diag && st)
				asm volatile("dmb ish\nisb\nmrs %0, cntvct_el0" : "=r"(tl0) :: "memory");
			uint64_t reg = dpdk_sys_ldadda_cq_status(rxq);
			if (st) {
				st->ldadda_calls++;
				if (diag) {
					uint64_t tl1;
					asm volatile("dmb ish\nisb\nmrs %0, cntvct_el0" : "=r"(tl1) :: "memory");
					st->ldadda_ticks_total += (tl1 - tl0);
				}
			}
			if ((reg & (1ULL << 63)) || (reg & (1ULL << 46))) {
				if (st) {
					if (reg & (1ULL << 46)) st->cq_err_seen++;
					if (reg & (1ULL << 63)) st->op_err_seen++;
					st->fatal_cq_err = 1;
				}
				return 0;
			}
			uint64_t tail = reg & 0xFFFFFu;
			uint64_t hw_head = (reg >> 20) & 0xFFFFFu;
			available = (tail < hw_head) ? (uint32_t)(tail - hw_head + qmask + 1)
						     : (uint32_t)(tail - hw_head);
			rxq->available = available;
			if (available == 0) {
				if (st)
					st->peek_hit_ldadda_zero++;
				return 0;
			}
		}
	}

	uint16_t nb_pkts = (pkts < available) ? pkts : (uint16_t)available;
	const uint64_t mbuf_init = rxq->mbuf_initializer;
	const uint64_t data_off = rxq->data_off;
	uint16_t out_pkts = 0;

	for (uint16_t i = 0; i < nb_pkts; i++) {
		uint64_t *cq = (uint64_t *)(desc + ((uintptr_t)head << 7));
		if (i + 2 < nb_pkts) {
			__builtin_prefetch(
				(const void *)(desc + ((uintptr_t)((head + 2) & qmask) << 7)), 0, 0);
		}
		uint64_t w0 = cq[0];
		uint64_t ctype = w0 >> 60;
		uint64_t w1 = cq[1];
		uint64_t w2 = cq[2];
		uint64_t w8 = cq[8];
		uint64_t w9 = cq[9];
		if (unlikely(ctype != 1)) {
			/* Fail-stop on non-RX CQE: do NOT blindly clear/doorbell unknown type! */
			if (st) {
				st->non_rx_cqes++;
				st->fatal_cq_err = 2;
				st->last_err_w0 = w0;
				st->last_err_w1 = w1;
				st->last_err_w8 = w8;
				st->last_err_w9 = w9;
			}
			nb_pkts = i;
			break;
		}
		if (likely(w9 != 0)) {
			int seg_zero = 0;
			struct rte_mbuf *m = dpdk_sys_cn10k_extract_mbuf(
				cq, mbuf_init, data_off, w1, w2, w8, w9, st, &seg_zero);
			if (unlikely(!m || seg_zero)) {
				if (st)
					st->fatal_cq_err = 3;
				nb_pkts = i;
				break;
			}
			rx_pkts[out_pkts++] = m;
			__builtin_prefetch(m, 1, 3);
		} else if (st) {
			st->zero_seg_err_cqes++;
		}
		dpdk_sys_zero_cqe_full(cq);
		head = (head + 1) & qmask;
	}

	if (nb_pkts > 0) {
		asm volatile("dmb oshst" ::: "memory");
		rxq->head = head;
		rxq->available = available - nb_pkts;
		*(volatile uint64_t *)rxq->cq_door = wdata | nb_pkts;
	}
	if (st)
		st->pkts_popped += out_pkts;
	return out_pkts;
}

/* MRV-161 NAIVE `--cqe-direct` (kept verbatim for falsifiable reproduction of B1/B2/B4 hazards:
 * single-seg only, clears only cq[0]/cq[9], `if (buff == 0) break;` with no LDADDA fallback).
 */
static inline uint16_t
dpdk_sys_cn10k_rx_burst_cqe_direct(uint16_t port_id, uint16_t queue_id,
				   struct rte_mbuf **rx_pkts, uint16_t pkts,
				   struct dpdk_sys_cqe_peek_stats *st)
{
	struct dpdk_sys_cn10k_rxq *rxq =
		(struct dpdk_sys_cn10k_rxq *)rte_eth_fp_ops[port_id].rxq.data[queue_id];
	const uintptr_t desc = rxq->desc;
	const uint32_t qmask = rxq->qmask;
	const uint64_t mbuf_init = rxq->mbuf_initializer;
	const uint64_t data_off = rxq->data_off;
	uint32_t head = rxq->head;
	uint16_t packets = 0;

	if (st)
		st->calls++;

	while (packets < pkts) {
		uint64_t *cq = (uint64_t *)(desc + ((uintptr_t)head << 7));
		uint64_t w0;
		asm volatile("ldar %0, [%1]" : "=r"(w0) : "r"(cq) : "memory");
		uint64_t ctype = w0 >> 60;
		if (ctype == 0) {
			if (packets == 0 && st)
				st->peek_empty++;
			break;
		}
		uint64_t buff;
		asm volatile("ldar %0, [%1]" : "=r"(buff) : "r"(cq + 9) : "memory");
		if (buff == 0) {
			if (st)
				st->w9_wait_retries++;
			break;
		}
		if (st)
			st->cqe_type_hist[ctype & 0xf]++;
		uint16_t len = *(const volatile uint16_t *)((const uint8_t *)cq + 16) + 1;
		struct rte_mbuf *m = (struct rte_mbuf *)(uintptr_t)(buff - data_off);

		*(uint64_t *)&m->rearm_data = mbuf_init;
		m->ol_flags = 0;
		m->packet_type = 0;
		m->pkt_len = len;
		m->data_len = len;
		rx_pkts[packets++] = m;
		__builtin_prefetch(m, 1, 3);

		cq[0] = 0;
		cq[9] = 0;
		head = (head + 1) & qmask;
	}

	if (packets > 0) {
		asm volatile("dmb oshst" ::: "memory");
		rxq->head = head;
		rxq->available = 0;
		*(volatile uint64_t *)rxq->cq_door = rxq->wdata | packets;
		if (st) {
			st->peek_hit++;
			st->pkts_popped += packets;
		}
	}
	return packets;
}

/* MRV-170 HARDENED `--cqe-direct-safe`:
 * Flags:
 *   bit 0 (0x1): full 128 B CQE clear (`dpdk_sys_zero_cqe_full`) instead of minimal clear
 *   bit 1 (0x2): drop & free error packets (`errlev/errcode != 0`) internally instead of passing up
 * `watchdog_interval`: if > 0, issue 1 LDADDA health check before polling once every N popped pkts
 *                      to guarantee bounded detection of CQ_ERR / OP_ERR / head desync.
 */
static inline uint16_t
dpdk_sys_cn10k_rx_burst_cqe_direct_safe(uint16_t port_id, uint16_t queue_id,
					struct rte_mbuf **rx_pkts, uint16_t pkts,
					struct dpdk_sys_cqe_peek_stats *st,
					uint32_t flags, uint64_t watchdog_interval)
{
	struct dpdk_sys_cn10k_rxq *rxq =
		(struct dpdk_sys_cn10k_rxq *)rte_eth_fp_ops[port_id].rxq.data[queue_id];
	const uintptr_t desc = rxq->desc;
	const uint32_t qmask = rxq->qmask;
	const uint64_t mbuf_init = rxq->mbuf_initializer;
	const uint64_t data_off = rxq->data_off;
	const int full_clear = (flags & 1u) != 0;
	const int drop_err = (flags & 2u) != 0;
	uint32_t head = rxq->head;
	uint16_t packets = 0;
	uint16_t consumed = 0;

	if (st) {
		st->calls++;
		/* Periodic low-frequency CQ_OP_STATUS watchdog BEFORE polling:
		 * previous cq_door has already been issued, so hw_head == rxq->head.
		 */
		if (unlikely(watchdog_interval > 0 && st->pkts_since_watchdog >= watchdog_interval)) {
			st->pkts_since_watchdog = 0;
			uint64_t reg = dpdk_sys_ldadda_cq_status(rxq);
			st->watchdog_ldadda++;
			if (unlikely(reg & (1ULL << 46))) {
				st->cq_err_seen++;
				st->fatal_cq_err = 1;
				return 0;
			}
			if (unlikely(reg & (1ULL << 63))) {
				st->op_err_seen++;
				st->fatal_cq_err = 1;
				return 0;
			}
			uint32_t hw_head = (uint32_t)((reg >> 20) & 0xFFFFFu);
			if (unlikely(hw_head != head)) {
				st->watchdog_head_mismatch++;
				st->fatal_cq_err = 1;
				return 0;
			}
		}
	}

	while (packets < pkts) {
		uint64_t *cq = (uint64_t *)(desc + ((uintptr_t)head << 7));
		uint64_t w0;
		asm volatile("ldar %0, [%1]" : "=r"(w0) : "r"(cq) : "memory");
		uint64_t ctype = w0 >> 60;
		if (ctype == 0) {
			if (consumed == 0 && st)
				st->peek_empty++;
			break;
		}

		/* Fast path: healthy single- or multi-seg NIX_XQE_TYPE_RX (1) */
		uint64_t w9 = 0;
		if (likely(ctype == 1)) {
			asm volatile("ldar %0, [%1]" : "=r"(w9) : "r"(cq + 9) : "memory");
		}

		uint64_t w1 = *(const volatile uint64_t *)(cq + 1);
		uint64_t w2 = *(const volatile uint64_t *)(cq + 2);
		uint64_t w8 = *(const volatile uint64_t *)(cq + 8);
		uint64_t err_bits = (w1 >> 20) & 0xFFFULL; /* errlev:4 | errcode:8 */
		uint64_t segs1 = (w8 >> 48) & 0x3ULL;

		if (likely(ctype == 1 && w9 != 0 && err_bits == 0 && segs1 != 0)) {
			if (st)
				st->cqe_type_hist[1]++;
			uint64_t desc_sizem1 = (w1 >> 12) & 0x1FULL;
			if (likely(segs1 == 1 && desc_sizem1 == 0)) {
				uint16_t len = (uint16_t)(w2 & 0xFFFFu) + 1;
				struct rte_mbuf *m = (struct rte_mbuf *)(uintptr_t)(w9 - data_off);
				*(uint64_t *)&m->rearm_data = mbuf_init;
				m->ol_flags = 0;
				m->packet_type = 0;
				m->pkt_len = len;
				m->data_len = len;
				m->next = NULL;
				rx_pkts[packets++] = m;
				__builtin_prefetch(m, 1, 3);
				if (full_clear) {
					dpdk_sys_zero_cqe_full(cq);
				} else {
					cq[0] = 0;
					cq[9] = 0;
				}
				head = (head + 1) & qmask;
				consumed++;
				continue;
			} else {
				/* Multi-segment healthy RX (segs 2..6 across w8..w15) */
				int seg_zero = 0;
				struct rte_mbuf *m = dpdk_sys_cn10k_extract_mbuf(
					cq, mbuf_init, data_off, w1, w2, w8, w9, st, &seg_zero);
				if (likely(m != NULL && !seg_zero)) {
					rx_pkts[packets++] = m;
					__builtin_prefetch(m, 1, 3);
					dpdk_sys_zero_cqe_full(cq);
					head = (head + 1) & qmask;
					consumed++;
					continue;
				}
				/* If a later segment pointer was still 0 after bounded spin, fall
				 * through to authoritative LDADDA fallback below!
				 */
			}
		}

		/* ---------------- Slow / Anomalous Path ----------------
		 * Reached ONLY when:
		 *   - ctype != 1 (non-RX CQE such as SEND / IPSEC / VWQE)
		 *   - w9 == 0 (either Line 1 trailed Line 0 OR real 0-segment error CQE)
		 *   - errlev/errcode != 0 (bad FCS/L2/L3/L4/MEMOUT/BUFS_OFLOW)
		 *   - segs1 == 0 or multi-seg pointer still 0 after spin
		 */
		if (ctype == 1 && err_bits == 0 && w9 == 0) {
			/* Bounded spin in case Line 1 simply trailed Line 0 by a few ns */
			if (st)
				st->w9_zero_on_w0_hit++;
			for (int sp = 0; sp < 32; sp++) {
				if (st)
					st->w9_wait_retries++;
				asm volatile("yield" ::: "memory");
				asm volatile("ldar %0, [%1]" : "=r"(w9) : "r"(cq + 9) : "memory");
				if (w9 != 0)
					break;
			}
			if (w9 != 0) {
				/* Line 1 arrived within bounded spin; re-run iteration! */
				continue;
			}
		}

		/* First flush any healthy packets already popped before issuing LDADDA */
		if (consumed > 0) {
			asm volatile("dmb oshst" ::: "memory");
			rxq->head = head;
			rxq->available = 0;
			*(volatile uint64_t *)rxq->cq_door = rxq->wdata | consumed;
			if (st)
				st->pkts_since_watchdog += consumed;
			consumed = 0;
		}

		/* Authoritative LDADDA NIX_LF_CQ_OP_STATUS serialization fallback */
		uint64_t reg = dpdk_sys_ldadda_cq_status(rxq);
		if (st)
			st->fallback_ldadda++;

		if (unlikely(reg & (1ULL << 46))) {
			if (st) {
				st->cq_err_seen++;
				st->fatal_cq_err = 1;
				if (packets > 0) st->peek_hit++;
				st->pkts_popped += packets;
			}
			return packets;
		}
		if (unlikely(reg & (1ULL << 63))) {
			if (st) {
				st->op_err_seen++;
				st->fatal_cq_err = 1;
				if (packets > 0) st->peek_hit++;
				st->pkts_popped += packets;
			}
			return packets;
		}

		uint32_t hw_tail = (uint32_t)(reg & 0xFFFFFu);
		uint32_t hw_head = (uint32_t)((reg >> 20) & 0xFFFFFu);
		uint32_t hw_avail = (hw_tail < hw_head) ? (hw_tail - hw_head + qmask + 1)
							: (hw_tail - hw_head);
		if (hw_avail == 0) {
			/* CQE not yet committed to hw_tail (or synthetic test on stopped queue) */
			if (st) {
				st->w0_ahead_of_tail++;
				if (packets > 0) st->peek_hit++;
				st->pkts_popped += packets;
			}
			return packets;
		}

		/* Hardware has committed this slot; re-read all words with acquire semantics */
		asm volatile("ldar %0, [%1]" : "=r"(w0) : "r"(cq) : "memory");
		asm volatile("ldar %0, [%1]" : "=r"(w1) : "r"(cq + 1) : "memory");
		asm volatile("ldar %0, [%1]" : "=r"(w2) : "r"(cq + 2) : "memory");
		asm volatile("ldar %0, [%1]" : "=r"(w8) : "r"(cq + 8) : "memory");
		asm volatile("ldar %0, [%1]" : "=r"(w9) : "r"(cq + 9) : "memory");
		ctype = w0 >> 60;
		err_bits = (w1 >> 20) & 0xFFFULL;
		segs1 = (w8 >> 48) & 0x3ULL;

		if (st) {
			st->cqe_type_hist[ctype & 0xf]++;
			st->last_err_w0 = w0;
			st->last_err_w1 = w1;
			st->last_err_w8 = w8;
			st->last_err_w9 = w9;
		}

		if (ctype != 1) {
			/* Fail-stop on unknown/non-RX CQE: NEVER blindly clear/doorbell!
			 * Caller must fall back to stock rte_eth_rx_burst() or abort queue
			 * so type-specific resource ownership (IPsec/VWQE/SEND) is preserved.
			 */
			if (st) {
				st->non_rx_cqes++;
				st->fatal_cq_err = 2;
				if (packets > 0) st->peek_hit++;
				st->pkts_popped += packets;
			}
			return packets;
		} else if (segs1 == 0 || w9 == 0) {
			/* Zero-segment RX error CQE (0 NPA buffers attached):
			 * safe to consume without dereferencing buff - data_off!
			 */
			if (st) {
				st->err_rx_cqes++;
				st->zero_seg_err_cqes++;
			}
		} else {
			/* Error RX CQE WITH valid mbuf segments (e.g. OL3_LEN / OL4_LEN / OL4_CHK / RE_FCS):
			 * extract ALL segments so we NEVER leak NPA buffers!
			 */
			if (err_bits != 0 && st)
				st->err_rx_cqes++;
			int seg_zero = 0;
			struct rte_mbuf *m = dpdk_sys_cn10k_extract_mbuf(
				cq, mbuf_init, data_off, w1, w2, w8, w9, st, &seg_zero);
			if (unlikely(!m || seg_zero)) {
				/* Fail-stop on partial/NULL segment extraction for segs1>0:
				 * NEVER clear or ring cq_door, so attached NPA buffers are not leaked!
				 */
				if (st) {
					st->fatal_cq_err = 3;
					if (packets > 0) st->peek_hit++;
					st->pkts_popped += packets;
				}
				return packets;
			}
			if (err_bits != 0 && drop_err) {
				rte_pktmbuf_free(m);
				if (st)
					st->err_pkts_freed++;
			} else {
				if (err_bits != 0)
					m->ol_flags |= RTE_MBUF_F_RX_L4_CKSUM_BAD;
				rx_pkts[packets++] = m;
			}
		}

		/* Clear full 128 B CQE, advance head by 1, ring cq_door immediately */
		dpdk_sys_zero_cqe_full(cq);
		asm volatile("dmb oshst" ::: "memory");
		head = (head + 1) & qmask;
		rxq->head = head;
		rxq->available = 0;
		*(volatile uint64_t *)rxq->cq_door = rxq->wdata | 1ULL;
		if (st)
			st->pkts_since_watchdog++;
	}

	if (consumed > 0) {
		asm volatile("dmb oshst" ::: "memory");
		rxq->head = head;
		rxq->available = 0;
		*(volatile uint64_t *)rxq->cq_door = rxq->wdata | consumed;
		if (st)
			st->pkts_since_watchdog += consumed;
	}
	if (st && packets > 0) {
		st->peek_hit++;
		st->pkts_popped += packets;
	}
	return packets;
}

/* Bare empty-poll microbenchmark of forced w0+w9 ldar reads and full 128 B clear cost. */
static inline uint64_t
dpdk_sys_cn10k_cqe_peek_w0_w9_bench(uint16_t port_id, uint16_t queue_id, uint64_t iters)
{
	struct dpdk_sys_cn10k_rxq *rxq =
		(struct dpdk_sys_cn10k_rxq *)rte_eth_fp_ops[port_id].rxq.data[queue_id];
	const uint64_t *cq = (const uint64_t *)(rxq->desc + ((uintptr_t)rxq->head << 7));
	uint64_t nz = 0;
	for (uint64_t i = 0; i < iters; i++) {
		uint64_t w0, w9;
		asm volatile("ldar %0, [%1]" : "=r"(w0) : "r"(cq) : "memory");
		asm volatile("ldar %0, [%1]" : "=r"(w9) : "r"(cq + 9) : "memory");
		if ((w0 >> 60) != 0 || w9 != 0)
			nz++;
	}
	return nz;
}

/* Microbenchmark: 2-word clear vs full 128 B clear on an idle CQE slot (never writes cq_door). */
static inline void
dpdk_sys_cn10k_cqe_clear_bench(uint16_t port_id, uint16_t queue_id, uint64_t iters, int full)
{
	struct dpdk_sys_cn10k_rxq *rxq =
		(struct dpdk_sys_cn10k_rxq *)rte_eth_fp_ops[port_id].rxq.data[queue_id];
	uint64_t *cq = (uint64_t *)(rxq->desc + ((uintptr_t)rxq->head << 7));
	for (uint64_t i = 0; i < iters; i++) {
		if (full) {
			dpdk_sys_zero_cqe_full(cq);
		} else {
			cq[0] = 0;
			cq[9] = 0;
			asm volatile("" ::: "memory");
		}
		asm volatile("dmb oshst" ::: "memory");
	}
}
#else
static inline int dpdk_sys_cn10k_rx_drop_re_set(uint16_t p, int e) { (void)p; (void)e; return -1; }
static inline int dpdk_sys_cn10k_rxq_inspect(uint16_t p, uint16_t q, struct dpdk_sys_cn10k_rxq_info *o) { (void)p; (void)q; (void)o; return -1; }
static inline int dpdk_sys_cn10k_cq_oracle_check(uint16_t p, uint16_t q, struct dpdk_sys_cq_oracle *o) { (void)p; (void)q; (void)o; return -1; }
static inline int dpdk_sys_cn10k_cqe_direct_sync(uint16_t p, uint16_t q, struct dpdk_sys_cq_oracle *o) { (void)p; (void)q; (void)o; return -1; }
static inline int dpdk_sys_cn10k_cq_read_slot(uint16_t p, uint16_t q, uint32_t s, uint64_t o[16]) { (void)p; (void)q; (void)s; (void)o; return -1; }
static inline int dpdk_sys_cn10k_cq_write_slot(uint16_t p, uint16_t q, uint32_t s, const uint64_t w[16]) { (void)p; (void)q; (void)s; (void)w; return -1; }
static inline uint64_t dpdk_sys_cn10k_cq_trigger_door_err(uint16_t p, uint16_t q) { (void)p; (void)q; return 0; }
static inline int dpdk_sys_eth_rx_burst_mode_str(uint16_t p, uint16_t q, char *b, size_t l) { (void)p; (void)q; if (b && l) b[0] = 0; return -1; }
static inline uint16_t dpdk_sys_cn10k_rx_burst_cqe_peek(uint16_t p, uint16_t q, struct rte_mbuf **r, uint16_t k, struct dpdk_sys_cqe_peek_stats *s, int d) { (void)p;(void)q;(void)r;(void)k;(void)s;(void)d; return 0; }
static inline uint16_t dpdk_sys_cn10k_rx_burst_cqe_direct(uint16_t p, uint16_t q, struct rte_mbuf **r, uint16_t k, struct dpdk_sys_cqe_peek_stats *s) { (void)p;(void)q;(void)r;(void)k;(void)s; return 0; }
static inline uint16_t dpdk_sys_cn10k_rx_burst_cqe_direct_safe(uint16_t p, uint16_t q, struct rte_mbuf **r, uint16_t k, struct dpdk_sys_cqe_peek_stats *s, uint32_t f, uint64_t w) { (void)p;(void)q;(void)r;(void)k;(void)s;(void)f;(void)w; return 0; }
static inline uint64_t dpdk_sys_cn10k_cqe_peek_w0_w9_bench(uint16_t p, uint16_t q, uint64_t i) { (void)p;(void)q;(void)i; return 0; }
static inline void dpdk_sys_cn10k_cqe_clear_bench(uint16_t p, uint16_t q, uint64_t i, int f) { (void)p;(void)q;(void)i;(void)f; }
#endif
