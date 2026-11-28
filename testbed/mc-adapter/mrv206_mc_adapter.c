/*
 * Minimal transport adapter embedding 100% unmodified upstream Memcached 1.6.x
 * (`github.com/memcached/memcached`) for MRV-206.
 *
 * ZERO lines of upstream Memcached source are modified: we include upstream
 * `memcached.c` with `main` renamed so all core subsystems (`proto_bin.c`,
 * `items.c`, `slabs.c`, `assoc.c`, `murmur3_hash.c`, `thread.c`, `stats`)
 * execute verbatim while only the lowest-level UDP recvfrom/sendmsg boundary
 * is fed by `mrv206_host` (DPDK SDP fair-full or DirectPush).
 */
#define main memcached_upstream_main
#include "memcached.c"
#undef main

#include <malloc.h>
#include <sched.h>
#include <sys/eventfd.h>
#include <sys/mman.h>
#include "mrv206_mc_adapter.h"
#define MRV206_MAX_THREADS 8
static conn *g_mc_conns[MRV206_MAX_THREADS] = {0};
static int g_dummy_efds[MRV206_MAX_THREADS] = {-1,-1,-1,-1,-1,-1,-1,-1};
static int g_mc_nthreads = 0;
static int g_mc_inited = 0;
static int g_lru_maint_started = 0;
static void *g_slab_mem = NULL;
static size_t g_slab_mem_size = 0;
static void pin_current_thread_cpu(int cpu) {
    if (cpu < 0) return;
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    (void)sched_setaffinity(0, sizeof(set), &set);
}

int mrv206_mc_init_mt(size_t maxbytes, uint32_t hashpower_init, int maint_cpu, int n_threads) {
    if (g_mc_inited) return 0;
    if (n_threads < 1) n_threads = 1;
    if (n_threads > MRV206_MAX_THREADS) n_threads = MRV206_MAX_THREADS;

    /* Lock glibc brk heap (disable trimming & mmap threshold) and pre-fault a 16 MiB
     * arena upfront so slab_list reallocs, logger, and stats incur zero minor page faults. */
    (void)mallopt(M_TRIM_THRESHOLD, -1);
    (void)mallopt(M_MMAP_MAX, 0);
    void *heap_warm = malloc(16UL * 1024UL * 1024UL);
    if (heap_warm) {
        memset(heap_warm, 0, 16UL * 1024UL * 1024UL);
        (void)mlock(heap_warm, 16UL * 1024UL * 1024UL);
        free(heap_warm);
    }

    if (!sanitycheck()) {
        fprintf(stderr, "mrv206_mc_init: upstream sanitycheck() failed\n");
        return -1;
    }

    settings_init();
    settings.maxbytes = maxbytes > 0 ? maxbytes : (256UL * 1024UL * 1024UL);
    settings.num_threads = 1;
    settings.num_threads_per_udp = 1;
    settings.binding_protocol = binary_prot;
    settings.use_cas = true;
    settings.evict_to_free = 1;
    settings.hashpower_init = hashpower_init > 0 ? (int)hashpower_init : 16;
    settings.lru_segmented = true;
    settings.lru_maintainer_thread = true;
    settings.lru_crawler = false;
    settings.slab_reassign = false;
    settings.slab_automove = 0;
    settings.verbose = 0;
    settings.flush_enabled = true;
    settings.maxconns = 1024;
    settings.maxconns_fast = true;

    if (hash_init(MURMUR3_HASH) != 0) {
        fprintf(stderr, "mrv206_mc_init: hash_init(MURMUR3_HASH) failed\n");
        return -2;
    }

    struct event_config *ev_config = event_config_new();
    event_config_set_flag(ev_config, EVENT_BASE_FLAG_NOLOCK);
    main_base = event_base_new_with_config(ev_config);
    event_config_free(ev_config);
    if (!main_base) {
        fprintf(stderr, "mrv206_mc_init: event_base_new failed\n");
        return -3;
    }

    stats_init();
    logger_init();
    logger_create();
    conn_init();
    assoc_init(settings.hashpower_init);

    /* Preallocate, pre-touch, and mlock full slab memory upfront on caller's NUMA node
     * so the hot loop takes strictly zero minor page faults across all slab classes. */
    g_slab_mem = mmap(NULL, settings.maxbytes, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
    if (g_slab_mem == MAP_FAILED) {
        g_slab_mem = NULL;
        perror("mrv206_mc_init: mmap slab pool");
        return -6;
    }
    g_slab_mem_size = settings.maxbytes;
    memset(g_slab_mem, 0, settings.maxbytes);
    if (mlock(g_slab_mem, settings.maxbytes) != 0) {
        perror("mrv206_mc_init: mlock slab pool (continuing with pre-touched pages)");
    }
    slabs_init(settings.maxbytes, settings.factor, true, NULL, g_slab_mem, false);
    slabs_prefill_global();
    /* Save caller affinity so background threads inherit `maint_cpu` (off data core cpu40). */
    cpu_set_t orig_mask;
    int have_orig = (sched_getaffinity(0, sizeof(orig_mask), &orig_mask) == 0);
    if (maint_cpu >= 0) {
        pin_current_thread_cpu(maint_cpu);
    }

    settings.num_threads = n_threads;
    memcached_thread_init(n_threads, NULL);
    init_lru_crawler(NULL);

    if (start_lru_maintainer_thread(NULL) == 0) {
        g_lru_maint_started = 1;
    }

    if (have_orig) {
        (void)sched_setaffinity(0, sizeof(orig_mask), &orig_mask);
    }

    /* Initialize monotonic clock identical to upstream memcached.c:main(). */
#if defined(HAVE_CLOCK_GETTIME) && defined(CLOCK_MONOTONIC)
    {
        struct timespec ts;
        if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0) {
            monotonic = true;
            monotonic_start = ts.tv_sec - (ITEM_UPDATE_INTERVAL + 2);
        }
    }
#endif
    clock_handler(0, 0, 0);
    uriencode_init();

    /* Allocate `n_threads` genuine upstream UDP/binary conns bound to worker threads 0..n_threads. */
    for (int i = 0; i < n_threads; i++) {
        g_dummy_efds[i] = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
        if (g_dummy_efds[i] < 0) {
            perror("mrv206_mc_init_mt: eventfd");
            return -4;
        }
        LIBEVENT_THREAD *ti = get_worker_thread(i);
        g_mc_conns[i] = conn_new(g_dummy_efds[i], conn_read, 0, UDP_READ_BUFFER_SIZE,
                                 udp_transport, main_base, NULL, 0, binary_prot);
        if (!g_mc_conns[i]) {
            fprintf(stderr, "mrv206_mc_init_mt: conn_new(%d) failed\n", i);
            return -5;
        }
        g_mc_conns[i]->thread = ti;
        g_mc_conns[i]->protocol = binary_prot;
        g_mc_conns[i]->try_read_command = try_read_command_binary;
    }
    g_mc_nthreads = n_threads;
    g_mc_inited = 1;
    return 0;
}

int mrv206_mc_init(size_t maxbytes, uint32_t hashpower_init, int maint_cpu) {
    return mrv206_mc_init_mt(maxbytes, hashpower_init, maint_cpu, 1);
}

int mrv206_mc_exec_tid(int tid, const uint8_t *udp_req, size_t udp_req_len, mrv206_mc_reply_t *out) {
    if (tid < 0 || tid >= g_mc_nthreads) return -1;
    conn *c = g_mc_conns[tid];
    if (!c || !udp_req || !out || udp_req_len < 8 + sizeof(protocol_binary_request_header)) {
        return -1;
    }

    /* 1. Standard 8-byte Memcached UDP header parsing (identical to try_read_udp). */
    uint16_t req_id = (uint16_t)(((uint16_t)udp_req[0] << 8) | (uint16_t)udp_req[1]);
    if (udp_req[4] != 0 || udp_req[5] != 1) {
        return -2;
    }
    c->request_id = req_id;

    pthread_mutex_lock(&c->thread->stats.mutex);
    c->thread->stats.bytes_read += udp_req_len;
    pthread_mutex_unlock(&c->thread->stats.mutex);

    /* Point conn directly at incoming binary payload (zero copy on RX). */
    c->rcurr = (char *)(udp_req + 8);
    c->rbytes = (int)(udp_req_len - 8);
    c->state = conn_parse_cmd;
    c->rlbytes = 0;
    c->sbytes = 0;

    /* 2. Run 100% unmodified upstream binary protocol parser + command dispatcher. */
    int rc = try_read_command_binary(c);
    if (rc <= 0) {
        conn_release_items(c);
        return -3;
    }

    /* 3. For mutations (SET/ADD/REPLACE/APPEND/PREPEND), run upstream conn_nread
     *    value copy into allocated Memcached slab item + complete_nread_binary(). */
    if (c->state == conn_nread) {
        if (c->rlbytes > 0) {
            if (c->rbytes < c->rlbytes) {
                conn_release_items(c);
                return -4;
            }
            if (c->item_malloced || ((((item *)c->item)->it_flags & ITEM_CHUNKED) == 0)) {
                memcpy(c->ritem, c->rcurr, (size_t)c->rlbytes);
                c->ritem += c->rlbytes;
                c->rcurr += c->rlbytes;
                c->rbytes -= c->rlbytes;
                c->rlbytes = 0;
            } else {
                int r = read_into_chunked_item(c);
                if (r < 0 || c->rlbytes != 0) {
                    conn_release_items(c);
                    return -5;
                }
            }
        }
        complete_nread_binary(c);
    } else if (c->state == conn_swallow) {
        c->sbytes = 0;
        c->rbytes = 0;
    }

    /* 4. Collect upstream response from c->resp_head (zero-copy scatter-gather IOVs). */
    mc_resp *resp = c->resp_head;
    if (!resp) {
        return -6;
    }

    uint32_t iovcnt = 0;
    uint32_t bin_len = 0;
    for (uint8_t i = 0; i < resp->iovcnt && iovcnt < MRV206_MC_MAX_IOVS; i++) {
        if (resp->iov[i].iov_len > 0) {
            out->iov[iovcnt] = resp->iov[i];
            bin_len += (uint32_t)resp->iov[i].iov_len;
            iovcnt++;
        }
    }

    /* Build standard 8-byte Memcached UDP header(s) verbatim via upstream build_udp_header()
     * (UDP_DATA_SIZE = 1392 B -> 1 datagram for <= 1392 B, 3 datagrams seq=0..2 for 3840 B GET). */
    resp->request_id = req_id;
    resp->tosend = bin_len;
    resp->udp_total = 0;
    resp->udp_sequence = 0;
    uint32_t dgrams = (bin_len == 0) ? 1U : ((bin_len + UDP_DATA_SIZE - 1U) / UDP_DATA_SIZE);
    if (dgrams > MRV206_MC_MAX_UDP_DGRAMS) {
        conn_release_items(c);
        return -7;
    }
    for (uint32_t d = 0; d < dgrams; d++) {
        build_udp_header(out->udp_hdrs[d], resp);
    }

    out->udp_dgram_cnt = dgrams;
    out->iovcnt = iovcnt;
    out->bin_len = bin_len;
    out->total_len = dgrams * UDP_HEADER_SIZE + bin_len;
    out->_pad = 0;
    if (iovcnt > 0 && out->iov[0].iov_len >= sizeof(protocol_binary_response_header)) {
        const protocol_binary_response_header *hdr =
            (const protocol_binary_response_header *)out->iov[0].iov_base;
        out->status = ntohs(hdr->response.status);
        out->opcode = hdr->response.opcode;
    } else {
        out->status = 0xffff;
        out->opcode = 0xff;
    }

    pthread_mutex_lock(&c->thread->stats.mutex);
    c->thread->stats.bytes_written += out->total_len;
    pthread_mutex_unlock(&c->thread->stats.mutex);

    return 0;
}

int mrv206_mc_exec(const uint8_t *udp_req, size_t udp_req_len, mrv206_mc_reply_t *out) {
    return mrv206_mc_exec_tid(0, udp_req, udp_req_len, out);
}

void mrv206_mc_release_tid(int tid) {
    if (tid >= 0 && tid < g_mc_nthreads && g_mc_conns[tid]) {
        conn_release_items(g_mc_conns[tid]);
    }
}

void mrv206_mc_release(void) {
    mrv206_mc_release_tid(0);
}

void mrv206_mc_tick_clock(void) {
    if (!g_mc_inited) return;
#if defined(HAVE_CLOCK_GETTIME) && defined(CLOCK_MONOTONIC)
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0) {
        current_time = (rel_time_t)(ts.tv_sec - monotonic_start);
    }
#endif
}

static void mrv206_item_stat_cb(const char *key, const uint16_t klen,
                                const char *val, const uint32_t vlen,
                                const void *cookie) {
    mrv206_mc_stats_t *st = (mrv206_mc_stats_t *)cookie;
    if (klen == 9 && memcmp(key, "evictions", 9) == 0) {
        st->evictions = strtoull(val, NULL, 10);
    } else if (klen == 9 && memcmp(key, "reclaimed", 9) == 0) {
        st->reclaimed = strtoull(val, NULL, 10);
    }
    (void)vlen;
}

void mrv206_mc_get_stats(mrv206_mc_stats_t *st) {
    if (!st) return;
    memset(st, 0, sizeof(*st));
    if (!g_mc_inited) return;

    struct thread_stats ts;
    struct slab_stats ss;
    threadlocal_stats_aggregate(&ts);
    slab_stats_aggregate(&ts, &ss);

    st->get_cmds = ts.get_cmds;
    st->get_hits = ss.get_hits;
    st->get_misses = ts.get_misses;
    st->set_cmds = ss.set_cmds;
    st->delete_hits = ss.delete_hits;
    st->delete_misses = ts.delete_misses;
    st->bytes_read = ts.bytes_read;
    st->bytes_written = ts.bytes_written;

    STATS_LOCK();
    st->curr_items = stats_state.curr_items;
    st->total_items = stats.total_items;
    st->bytes_stored = stats_state.curr_bytes;
    st->hashpower = hashpower;
    STATS_UNLOCK();
    st->maxbytes_mib = (uint32_t)(settings.maxbytes / (1024UL * 1024UL));

    item_stats_totals(mrv206_item_stat_cb, st);
}

void mrv206_mc_reset_stats(void) {
    if (!g_mc_inited) return;
    threadlocal_stats_reset();
}

void mrv206_mc_shutdown(void) {
    if (!g_mc_inited) return;
    if (g_lru_maint_started) {
        stop_lru_maintainer_thread();
        g_lru_maint_started = 0;
    }
    for (int i = 0; i < g_mc_nthreads; i++) {
        if (g_mc_conns[i]) {
            conn_release_items(g_mc_conns[i]);
            g_mc_conns[i] = NULL;
        }
        if (g_dummy_efds[i] >= 0) {
            close(g_dummy_efds[i]);
            g_dummy_efds[i] = -1;
        }
    }
    g_mc_nthreads = 0;
    if (g_slab_mem) {
        (void)munlock(g_slab_mem, g_slab_mem_size);
        (void)munmap(g_slab_mem, g_slab_mem_size);
        g_slab_mem = NULL;
        g_slab_mem_size = 0;
    }
    g_mc_inited = 0;
}

const char *mrv206_mc_version(void) {
    return VERSION;
}
