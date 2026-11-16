// Minimal DPDK reflector for the card side of dpdk_pingpong: one queue, one
// lcore, receive a burst, swap Ethernet src/dst, transmit the same burst at once.
// usage: dpdk_reflector [EAL args]   (port 0 = the SDP VF given with -a)
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>

#include <rte_eal.h>
#include <rte_ethdev.h>
#include <rte_ether.h>
#include <rte_mbuf.h>

#define NB_DESC 2048

static volatile int quit;

static void on_signal(int sig)
{
	(void)sig;
	quit = 1;
}

int main(int argc, char **argv)
{
	if (rte_eal_init(argc, argv) < 0)
		rte_exit(EXIT_FAILURE, "EAL init failed\n");
	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);

	uint16_t port = 0;
	int socket = rte_eth_dev_socket_id(port);
	struct rte_mempool *mp = rte_pktmbuf_pool_create("refl", 16383, 256, 0,
							 RTE_MBUF_DEFAULT_BUF_SIZE, socket);
	struct rte_eth_conf conf = { 0 };
	if (!mp || rte_eth_dev_configure(port, 1, 1, &conf) ||
	    rte_eth_rx_queue_setup(port, 0, NB_DESC, socket, NULL, mp) ||
	    rte_eth_tx_queue_setup(port, 0, NB_DESC, socket, NULL) || rte_eth_dev_start(port))
		rte_exit(EXIT_FAILURE, "port setup failed\n");
	rte_eth_promiscuous_enable(port);
	printf("reflector running on port %u\n", port);
	fflush(stdout);

	uint64_t pkts = 0, drops = 0;
	struct rte_mbuf *b[32];
	while (!quit) {
		uint16_t n = rte_eth_rx_burst(port, 0, b, 32);
		if (!n)
			continue;
		for (uint16_t i = 0; i < n; i++) {
			struct rte_ether_hdr *eh = rte_pktmbuf_mtod(b[i], struct rte_ether_hdr *);
			struct rte_ether_addr t = eh->dst_addr;
			eh->dst_addr = eh->src_addr;
			eh->src_addr = t;
		}
		uint16_t s = rte_eth_tx_burst(port, 0, b, n);
		pkts += s;
		if (s < n) {
			drops += n - s;
			rte_pktmbuf_free_bulk(&b[s], n - s);
		}
	}
	printf("reflected %lu, tx drops %lu\n", (unsigned long)pkts, (unsigned long)drops);
	rte_eth_dev_stop(port);
	rte_eal_cleanup();
	return 0;
}
