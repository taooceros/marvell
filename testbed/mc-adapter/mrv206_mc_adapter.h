#ifndef MRV206_MC_ADAPTER_H
#define MRV206_MC_ADAPTER_H

#include <stddef.h>
#include <stdint.h>
#include <sys/uio.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MRV206_MC_MAX_IOVS 8
#define MRV206_MC_MAX_UDP_DGRAMS 4
#define MRV206_MC_UDP_HDR_SIZE 8
#define MRV206_MC_UDP_DATA_SIZE 1392
#define MRV206_MC_UDP_MAX_PAYLOAD 1400

typedef struct {
    uint8_t udp_hdrs[MRV206_MC_MAX_UDP_DGRAMS][8];
    uint32_t udp_dgram_cnt; /* 1 for <= 1392 B, ceil(bin_len / 1392) via upstream build_udp_header() */
    struct iovec iov[MRV206_MC_MAX_IOVS];
    uint32_t iovcnt;
    uint32_t bin_len;       /* bytes across iov[0..iovcnt] (excluding UDP headers) */
    uint32_t total_len;     /* udp_dgram_cnt * 8 + bin_len */
    uint16_t status;        /* host-order protocol_binary_response_status */
    uint8_t opcode;         /* protocol_binary_command */
    uint8_t _pad;
} mrv206_mc_reply_t;

typedef struct {
    uint64_t get_cmds;
    uint64_t get_hits;
    uint64_t get_misses;
    uint64_t set_cmds;
    uint64_t delete_hits;
    uint64_t delete_misses;
    uint64_t evictions;
    uint64_t reclaimed;
    uint64_t curr_items;
    uint64_t total_items;
    uint64_t bytes_stored;
    uint64_t bytes_read;
    uint64_t bytes_written;
    uint32_t hashpower;
    uint32_t maxbytes_mib;
} mrv206_mc_stats_t;

/* Initialize 100% unmodified upstream Memcached 1.6.x core subsystems.
 * Must be called on the NUMA node where slab memory should reside.
 * Background maintenance threads (if started) are pinned to `maint_cpu` (>= 0).
 * Returns 0 on success.
 */
int mrv206_mc_init(size_t maxbytes, uint32_t hashpower, int maint_cpu);
int mrv206_mc_init_mt(size_t maxbytes, uint32_t hashpower, int maint_cpu, int n_threads);

/* Execute one genuine Memcached Binary UDP datagram (`8 B UDP hdr + binary req`)
 * through unmodified upstream `proto_bin.c` + `items.c` + `slabs.c` + `assoc.c`.
 * On success (returns 0), `out` describes the response UDP header + zero-copy
 * scatter-gather IOVs pointing directly into `mc_resp` and Memcached slab memory.
 * Caller MUST invoke `mrv206_mc_release()` after copying out the reply.
 */
int mrv206_mc_exec(const uint8_t *udp_req, size_t udp_req_len, mrv206_mc_reply_t *out);
int mrv206_mc_exec_tid(int tid, const uint8_t *udp_req, size_t udp_req_len, mrv206_mc_reply_t *out);

/* Release the active upstream `mc_resp` and decrement item refcount via
 * upstream `conn_release_items(c)`.
 */
void mrv206_mc_release(void);
void mrv206_mc_release_tid(int tid);

/* Update Memcached's monotonic `current_time` (call periodically from ctrl thread). */
void mrv206_mc_tick_clock(void);

/* Read/reset aggregated Memcached engine statistics. */
void mrv206_mc_get_stats(mrv206_mc_stats_t *st);
void mrv206_mc_reset_stats(void);

/* Cleanly stop Memcached threads on server shutdown. */
void mrv206_mc_shutdown(void);

/* Return upstream Memcached version string (e.g. "1.6.45"). */
const char *mrv206_mc_version(void);

#ifdef __cplusplus
}
#endif

#endif /* MRV206_MC_ADAPTER_H */
