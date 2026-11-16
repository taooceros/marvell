// Multi-threaded UDP sender (sendmmsg) for host -> card bandwidth tests.
// usage: udp_blast DST_IP DST_PORT PAYLOAD_BYTES SECONDS CPU[,CPU...]
// One thread + socket per CPU (distinct source ports -> distinct flows).
// MSG_CONFIRM keeps the neighbour entry REACHABLE without ARP replies.
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>

#define BATCH 64

static struct sockaddr_in dst;
static int payload;
static volatile int stop;

struct worker {
	pthread_t th;
	int cpu;
	uint64_t pkts;
};

static void *run(void *arg)
{
	struct worker *w = arg;
	cpu_set_t set;
	CPU_ZERO(&set);
	CPU_SET(w->cpu, &set);
	pthread_setaffinity_np(pthread_self(), sizeof(set), &set);
	int s = socket(AF_INET, SOCK_DGRAM, 0), sz = 16 << 20;
	setsockopt(s, SOL_SOCKET, SO_SNDBUF, &sz, sizeof(sz));
	if (connect(s, (struct sockaddr *)&dst, sizeof(dst))) {
		perror("connect");
		return NULL;
	}
	static char buf[1500];
	struct iovec iov[BATCH];
	struct mmsghdr msg[BATCH];
	memset(msg, 0, sizeof(msg));
	for (int i = 0; i < BATCH; i++) {
		iov[i].iov_base = buf;
		iov[i].iov_len = payload;
		msg[i].msg_hdr.msg_iov = &iov[i];
		msg[i].msg_hdr.msg_iovlen = 1;
	}
	while (!stop) {
		int n = sendmmsg(s, msg, BATCH, MSG_CONFIRM);
		if (n > 0)
			w->pkts += n;
	}
	return NULL;
}

int main(int argc, char **argv)
{
	if (argc != 6) {
		fprintf(stderr, "usage: %s DST_IP DST_PORT PAYLOAD_BYTES SECONDS CPU[,CPU...]\n", argv[0]);
		return 2;
	}
	dst.sin_family = AF_INET;
	dst.sin_port = htons(atoi(argv[2]));
	inet_pton(AF_INET, argv[1], &dst.sin_addr);
	payload = atoi(argv[3]);
	int secs = atoi(argv[4]), n = 0;
	struct worker w[256];
	for (char *tok = strtok(argv[5], ","); tok && n < 256; tok = strtok(NULL, ","))
		w[n++] = (struct worker){ .cpu = atoi(tok) };
	struct timespec t0, t1;
	clock_gettime(CLOCK_MONOTONIC, &t0);
	for (int i = 0; i < n; i++)
		pthread_create(&w[i].th, NULL, run, &w[i]);
	struct timespec d = { .tv_sec = secs };
	nanosleep(&d, NULL);
	stop = 1;
	uint64_t tot = 0;
	for (int i = 0; i < n; i++) {
		pthread_join(w[i].th, NULL);
		tot += w[i].pkts;
	}
	clock_gettime(CLOCK_MONOTONIC, &t1);
	double el = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;
	double pps = tot / el, l2 = payload + 42; /* Ethernet+IP+UDP headers, no FCS */
	printf("payload %dB threads %d: sent %.3f Mpps, %.2f Gbit/s L2 (%.2f Gbit/s payload)\n",
	       payload, n, pps / 1e6, pps * l2 * 8 / 1e9, pps * payload * 8 / 1e9);
	return 0;
}
