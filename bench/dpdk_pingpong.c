// Windowed DPDK ping-pong client (host side) against a card-side reflector
// (testpmd macswap). Batch = 1 on submission: every request is its own
// rte_eth_tx_burst() call (one doorbell). Concurrency = window W: at most W
// requests are outstanding; each reply immediately releases one new request.
//
// usage: dpdk_pingpong [EAL args] -- -d PEER_MAC [-n ROUNDS] [-w WINDOW] [-s FRAME_BYTES]
#include <getopt.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <rte_cycles.h>
#include <rte_eal.h>
#include <rte_ethdev.h>
#include <rte_ether.h>
#include <rte_mbuf.h>
#include <rte_io.h>
#include <bus_pci_driver.h>

#define ETHERTYPE_PP 0x88b5 /* IEEE local experimental */
#define WARMUP 20000
#define NB_DESC 2048 /* RX/TX ring size (octeon_ep minimum); window <= NB_DESC / 2 */

struct pp_hdr {
	uint64_t seq;
	uint64_t tsc;
};

static int cmp_u64(const void *a, const void *b)
{
	uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
	return x < y ? -1 : x > y;
}

static struct rte_mbuf *make_req(struct rte_mempool *mp, const struct rte_ether_addr *src,
				 const struct rte_ether_addr *dst, uint16_t len, uint64_t seq)
{
	struct rte_mbuf *m = rte_pktmbuf_alloc(mp);
	if (!m)
		rte_exit(EXIT_FAILURE, "mbuf alloc failed\n");
	char *p = rte_pktmbuf_append(m, len);
	struct rte_ether_hdr *eh = (struct rte_ether_hdr *)p;
	rte_ether_addr_copy(dst, &eh->dst_addr);
	rte_ether_addr_copy(src, &eh->src_addr);
	eh->ether_type = rte_cpu_to_be_16(ETHERTYPE_PP);
	struct pp_hdr *h = (struct pp_hdr *)(eh + 1);
	h->seq = seq;
	h->tsc = rte_rdtsc();
	return m;
}

int main(int argc, char **argv)
{
	int ret = rte_eal_init(argc, argv);
	if (ret < 0)
		rte_exit(EXIT_FAILURE, "EAL init failed\n");
	argc -= ret;
	argv += ret;

	long rounds = 1000000, window = 1;
	uint16_t len = 64;
	struct rte_ether_addr peer;
	int have_peer = 0, mmio_probe = 0, opt;
	while ((opt = getopt(argc, argv, "d:n:w:s:m")) != -1) {
		switch (opt) {
		case 'd': have_peer = rte_ether_unformat_addr(optarg, &peer) == 0; break;
		case 'n': rounds = atol(optarg); break;
		case 'w': window = atol(optarg); break;
		case 's': len = atoi(optarg); break;
		case 'm': mmio_probe = 1; break;
		default: rte_exit(EXIT_FAILURE, "bad args\n");
		}
	}
	if (!have_peer || window < 1 || window > NB_DESC / 2 || len < 60 || len > 1514)
		rte_exit(EXIT_FAILURE, "need -d PEER_MAC, 1 <= -w <= %d, 60 <= -s <= 1514\n", NB_DESC / 2);

	uint16_t port = 0;
	int socket = rte_eth_dev_socket_id(port);
	struct rte_mempool *mp = rte_pktmbuf_pool_create("pp", 8191, 256, 0,
							 RTE_MBUF_DEFAULT_BUF_SIZE, socket);
	struct rte_eth_conf conf = { 0 };
	if (!mp || rte_eth_dev_configure(port, 1, 1, &conf) ||
	    rte_eth_rx_queue_setup(port, 0, NB_DESC, socket, NULL, mp) ||
	    rte_eth_tx_queue_setup(port, 0, NB_DESC, socket, NULL) || rte_eth_dev_start(port))
		rte_exit(EXIT_FAILURE, "port setup failed\n");
	struct rte_ether_addr me;
	rte_eth_macaddr_get(port, &me);

	if (mmio_probe) {
		/* Time non-posted reads of the VF's ring-0 OUT_CNTS register
		 * (BAR0 + 0x10100, what the PMD polls when ism_enable=0): one
		 * host -> device -> host PCIe round trip each.
		 */
		struct rte_eth_dev_info info;
		if (rte_eth_dev_info_get(port, &info))
			rte_exit(EXIT_FAILURE, "dev_info failed\n");
		volatile uint8_t *bar = RTE_DEV_TO_PCI(info.device)->mem_resource[0].addr;
		enum { N = 100000 };
		uint64_t *t = malloc(N * sizeof(*t)), hz = rte_get_tsc_hz();
		for (int i = 0; i < N; i++) {
			uint64_t t0 = rte_rdtsc_precise();
			(void)rte_read32(bar + 0x10100);
			t[i] = rte_rdtsc_precise() - t0;
		}
		qsort(t, N, sizeof(*t), cmp_u64);
		printf("MMIO read BAR0+0x10100: min %.0f p50 %.0f p99 %.0f ns\n",
		       t[0] * 1e9 / hz, t[N / 2] * 1e9 / hz, t[N * 99 / 100] * 1e9 / hz);
	}

	long total = WARMUP + rounds, sent = 0, done = 0, got = 0;
	uint64_t *rtt = malloc(rounds * sizeof(*rtt));
	uint64_t hz = rte_get_tsc_hz(), t_start = 0, last_rx = rte_rdtsc();
	long stale = 0;
	struct rte_mbuf *rx[32];

	while (done < total) {
		while (sent < total && sent - done < window) {
			struct rte_mbuf *m = make_req(mp, &me, &peer, len, sent);
			while (rte_eth_tx_burst(port, 0, &m, 1) == 0)
				;
			sent++;
		}
		uint16_t n = rte_eth_rx_burst(port, 0, rx, 32);
		uint64_t now = rte_rdtsc();
		for (uint16_t i = 0; i < n; i++) {
			struct rte_ether_hdr *eh = rte_pktmbuf_mtod(rx[i], struct rte_ether_hdr *);
			if (eh->ether_type == rte_cpu_to_be_16(ETHERTYPE_PP)) {
				struct pp_hdr *h = (struct pp_hdr *)(eh + 1);
				if ((long)h->seq >= WARMUP) {
					if (got == 0)
						t_start = h->tsc;
					rtt[got++] = now - h->tsc;
				}
				done++;
			}
			rte_pktmbuf_free(rx[i]);
		}
		if (n) {
			last_rx = now;
		} else if (now - last_rx > hz / 10) { /* 100 ms silence: declare outstanding lost */
			stale += sent - done;
			done = sent;
			last_rx = now;
			if (stale > 1000)
				rte_exit(EXIT_FAILURE, "no replies (lost %ld); is the reflector running?\n", stale);
		}
	}
	double el = (double)(rte_rdtsc() - t_start) / hz;
	qsort(rtt, got, sizeof(*rtt), cmp_u64);
	double ns = 1e9 / hz, sum = 0;
	for (long i = 0; i < got; i++)
		sum += rtt[i];
#define P(q) (rtt[(long)((q) * (got - 1))] * ns / 1000)
	printf("frame %uB window %ld rounds %ld lost %ld | %.3f Mmsg/s | RTT us: min %.2f p50 %.2f p90 %.2f p99 %.2f p99.9 %.2f max %.2f mean %.2f\n",
	       len, window, got, stale, got / el / 1e6, rtt[0] * ns / 1000, P(0.5), P(0.9), P(0.99),
	       P(0.999), rtt[got - 1] * ns / 1000, sum / got * ns / 1000);
	rte_eth_dev_stop(port);
	rte_eal_cleanup();
	return 0;
}
