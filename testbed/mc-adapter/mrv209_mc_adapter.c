/*
 * Minimal transport adapter embedding 100% unmodified upstream Memcached 1.6.x
 * (`github.com/memcached/memcached`) for MRV-209 (MRV-203 E5).
 *
 * ZERO lines of upstream Memcached source are modified and ZERO third-party
 * upstream files are tracked in git: we compile directly against the untouched
 * upstream checkout in `/tmp/mrv206-upstream/memcached` with `main` renamed so
 * all core subsystems (`proto_bin.c`, `items.c`, `slabs.c`, `assoc.c`,
 * `murmur3_hash.c`, `thread.c`, `stats`) execute verbatim while only the
 * lowest-level UDP recvfrom/sendmsg boundary is fed by `mrv209_host` over
 * standard DPDK SDP.
 */
#define main memcached_upstream_main
#include "memcached.c"
#undef main

#include <malloc.h>
#include <sched.h>
#include <sys/eventfd.h>
#include <sys/mman.h>
#include "mrv209_mc_adapter.h"

static conn *g_mc_conn = NULL;
static int g_dummy_efd = -1;
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

int mrv209_mc_init(size_t maxbytes, uint32_t hashpower_init, int maint_cpu) {
    if (g_mc_inited) return 0;

    /* Lock glibc brk heap and pre-fault a 16 MiB arena upfront so slab_list
     * reallocs, logger, and stats incur zero minor page faults in the hot loop. */
    (void)mallopt(M_TRIM_THRESHOLD, -1);
    (void)mallopt(M_MMAP_MAX, 0);
    void *heap_warm = malloc(16UL * 1024UL * 1024UL);
    if (heap_warm) {
        memset(heap_warm, 0, 16UL * 1024UL * 1024UL);
        (void)mlock(heap_warm, 16UL * 1024UL * 1024UL);
        free(heap_warm);
    }

    if (!sanitycheck()) {
        fprintf(stderr, "mrv209_mc_init: upstream sanitycheck() failed\n");
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
        fprintf(stderr, "mrv209_mc_init: hash_init(MURMUR3_HASH) failed\n");
        return -2;
    }

    struct event_config *ev_config = event_config_new();
    event_config_set_flag(ev_config, EVENT_BASE_FLAG_NOLOCK);
    main_base = event_base_new_with_config(ev_config);
    event_config_free(ev_config);
    if (!main_base) {
        fprintf(stderr, "mrv209_mc_init: event_base_new failed\n");
        return -3;
    }

    stats_init();
    logger_init();
    logger_create();
    conn_init();
    assoc_init(settings.hashpower_init);

    g_slab_mem = mmap(NULL, settings.maxbytes, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
    if (g_slab_mem == MAP_FAILED) {
        g_slab_mem = NULL;
        perror("mrv209_mc_init: mmap slab pool");
        return -6;
    }
    g_slab_mem_size = settings.maxbytes;
    memset(g_slab_mem, 0, settings.maxbytes);
    if (mlock(g_slab_mem, settings.maxbytes) != 0) {
        perror("mrv209_mc_init: mlock slab pool (continuing with pre-touched pages)");
    }
    slabs_init(settings.maxbytes, settings.factor, true, NULL, g_slab_mem, false);
    slabs_prefill_global();

    cpu_set_t orig_mask;
    int have_orig = (sched_getaffinity(0, sizeof(orig_mask), &orig_mask) == 0);
    if (maint_cpu >= 0) {
        pin_current_thread_cpu(maint_cpu);
    }

    memcached_thread_init(1, NULL);
    init_lru_crawler(NULL);

    if (start_lru_maintainer_thread(NULL) == 0) {
        g_lru_maint_started = 1;
    }

    if (have_orig) {
        (void)sched_setaffinity(0, sizeof(orig_mask), &orig_mask);
    }

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

    g_dummy_efd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    if (g_dummy_efd < 0) {
        perror("mrv209_mc_init: eventfd");
        return -4;
    }
    LIBEVENT_THREAD *t0 = get_worker_thread(0);
    g_mc_conn = conn_new(g_dummy_efd, conn_read, 0, UDP_READ_BUFFER_SIZE,
                         udp_transport, main_base, NULL, 0, binary_prot);
    if (!g_mc_conn) {
        fprintf(stderr, "mrv209_mc_init: conn_new failed\n");
        return -5;
    }
    g_mc_conn->thread = t0;
    g_mc_conn->protocol = binary_prot;
    g_mc_conn->try_read_command = try_read_command_binary;

    g_mc_inited = 1;
    return 0;
}

int mrv209_mc_exec(const uint8_t *udp_req, size_t udp_req_len, mrv209_mc_reply_t *out) {
    conn *c = g_mc_conn;
    if (!c || !udp_req || !out || udp_req_len < 8 + sizeof(protocol_binary_request_header)) {
        return -1;
    }

    uint16_t req_id = (uint16_t)(((uint16_t)udp_req[0] << 8) | (uint16_t)udp_req[1]);
    if (udp_req[4] != 0 || udp_req[5] != 1) {
        return -2;
    }
    c->request_id = req_id;

    pthread_mutex_lock(&c->thread->stats.mutex);
    c->thread->stats.bytes_read += udp_req_len;
    pthread_mutex_unlock(&c->thread->stats.mutex);

    c->rcurr = (char *)(udp_req + 8);
    c->rbytes = (int)(udp_req_len - 8);
    c->state = conn_parse_cmd;
    c->rlbytes = 0;
    c->sbytes = 0;

    int rc = try_read_command_binary(c);
    if (rc <= 0) {
        conn_release_items(c);
        return -3;
    }

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

    mc_resp *resp = c->resp_head;
    if (!resp) {
        return -6;
    }

    uint32_t iovcnt = 0;
    uint32_t bin_len = 0;
    for (uint8_t i = 0; i < resp->iovcnt && iovcnt < MRV209_MC_MAX_IOVS; i++) {
        if (resp->iov[i].iov_len > 0) {
            out->iov[iovcnt] = resp->iov[i];
            bin_len += (uint32_t)resp->iov[i].iov_len;
            iovcnt++;
        }
    }

    resp->request_id = req_id;
    resp->tosend = bin_len;
    resp->udp_total = 0;
    resp->udp_sequence = 0;
    uint32_t dgrams = (bin_len == 0) ? 1U : ((bin_len + UDP_DATA_SIZE - 1U) / UDP_DATA_SIZE);
    if (dgrams > MRV209_MC_MAX_UDP_DGRAMS) {
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

void mrv209_mc_release(void) {
    if (g_mc_conn) {
        conn_release_items(g_mc_conn);
    }
}

void mrv209_mc_tick_clock(void) {
    if (!g_mc_inited) return;
#if defined(HAVE_CLOCK_GETTIME) && defined(CLOCK_MONOTONIC)
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0) {
        current_time = (rel_time_t)(ts.tv_sec - monotonic_start);
    }
#endif
}

static void mrv209_item_stat_cb(const char *key, const uint16_t klen,
                                const char *val, const uint32_t vlen,
                                const void *cookie) {
    mrv209_mc_stats_t *st = (mrv209_mc_stats_t *)cookie;
    if (klen == 9 && memcmp(key, "evictions", 9) == 0) {
        st->evictions = strtoull(val, NULL, 10);
    } else if (klen == 9 && memcmp(key, "reclaimed", 9) == 0) {
        st->reclaimed = strtoull(val, NULL, 10);
    }
    (void)vlen;
}

void mrv209_mc_get_stats(mrv209_mc_stats_t *st) {
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

    item_stats_totals(mrv209_item_stat_cb, st);
}

void mrv209_mc_reset_stats(void) {
    if (!g_mc_inited) return;
    threadlocal_stats_reset();
}

void mrv209_mc_shutdown(void) {
    if (!g_mc_inited) return;
    if (g_lru_maint_started) {
        stop_lru_maintainer_thread();
        g_lru_maint_started = 0;
    }
    if (g_mc_conn) {
        conn_release_items(g_mc_conn);
        g_mc_conn = NULL;
    }
    if (g_dummy_efd >= 0) {
        close(g_dummy_efd);
        g_dummy_efd = -1;
    }
    if (g_slab_mem) {
        (void)munlock(g_slab_mem, g_slab_mem_size);
        (void)munmap(g_slab_mem, g_slab_mem_size);
        g_slab_mem = NULL;
        g_slab_mem_size = 0;
    }
    g_mc_inited = 0;
}

const char *mrv209_mc_version(void) {
    return VERSION;
}
