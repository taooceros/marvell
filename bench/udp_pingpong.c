// UDP ping-pong RTT client for a DPDK 5tswap/icmp reflector on the card.
// usage: udp_pingpong DST_IP DST_PORT ROUNDS PAYLOAD_BYTES
// Busy-polls the reply; sends with MSG_CONFIRM so the neighbour entry stays
// REACHABLE (the DPDK reflector does not answer ARP).
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>

static inline uint64_t now_ns(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC_RAW, &t);
	return (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec;
}

static int cmp_u64(const void *a, const void *b)
{
	uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
	return x < y ? -1 : x > y;
}

int main(int argc, char **argv)
{
	if (argc != 5) {
		fprintf(stderr, "usage: %s DST_IP DST_PORT ROUNDS PAYLOAD_BYTES\n", argv[0]);
		return 2;
	}
	long rounds = atol(argv[3]), warmup = 1000;
	int size = atoi(argv[4]);
	if (size < 8 || size > 1472) {
		fprintf(stderr, "payload must be 8..1472\n");
		return 2;
	}
	int s = socket(AF_INET, SOCK_DGRAM, 0);
	struct sockaddr_in dst = { .sin_family = AF_INET, .sin_port = htons(atoi(argv[2])) };
	if (s < 0 || inet_pton(AF_INET, argv[1], &dst.sin_addr) != 1 ||
	    connect(s, (struct sockaddr *)&dst, sizeof(dst))) {
		perror("socket/connect");
		return 1;
	}
	uint64_t *rtt = malloc(rounds * sizeof(*rtt));
	char tx[1500] = { 0 }, rx[2048];
	long got = 0, lost = 0, ok = 0;
	for (long i = -warmup; i < rounds; i++) {
		uint64_t seq = (uint64_t)i, t0 = now_ns();
		memcpy(tx, &seq, sizeof(seq));
		if (send(s, tx, size, MSG_CONFIRM) != size) {
			perror("send");
			return 1;
		}
		for (;;) {
			ssize_t r = recv(s, rx, sizeof(rx), MSG_DONTWAIT);
			if (r >= 8 && !memcmp(rx, &seq, sizeof(seq)))
				break;
			if (now_ns() - t0 > 100000000ull) { /* 100 ms: count as lost */
				if (++lost >= 20 && lost > ok) {
					printf("%ld of the first tries lost; is the reflector up?\n", lost);
					return 1;
				}
				goto next;
			}
		}
		ok++;
		if (i >= 0)
			rtt[got++] = now_ns() - t0;
next:;
	}
	if (!got) {
		printf("no replies (lost %ld)\n", lost);
		return 1;
	}
	qsort(rtt, got, sizeof(*rtt), cmp_u64);
	double sum = 0;
	for (long i = 0; i < got; i++)
		sum += rtt[i];
#define P(q) (rtt[(long)((q) * (got - 1))] / 1000.0)
	printf("payload %dB rounds %ld lost %ld | RTT us: min %.2f p50 %.2f p90 %.2f p99 %.2f p99.9 %.2f max %.2f mean %.2f\n",
	       size, got, lost, rtt[0] / 1000.0, P(0.5), P(0.9), P(0.99), P(0.999),
	       rtt[got - 1] / 1000.0, sum / got / 1000.0);
	return 0;
}
