/* Minimal libibverbs + UDP GSO/sendmmsg + in-process ethtool ioctl shim for
 * MRV-213 (E2) mercury multi-core 100GbE burst generator (zero root required).
 *
 * Supports:
 *   - uc64    : IBV_QPT_UC, 0 B payload -> exact 64 B L2 frames (HW BTH.PSN + ICRC + FCS)
 *   - ud256   : IBV_QPT_UD, 186 B payload -> exact 256 B L2 frames (inline TX + end-to-end verify)
 *   - ud1094  : IBV_QPT_UD, 1024 B payload -> exact 1094 B L2 frames
 *   - udp1500 : Non-root Linux UDP_SEGMENT GSO (with transparent sendmmsg fallback),
 *               1454 B UDP payload -> exact 1500 B L2 frames (zero IP frag on MTU 1500)
 *
 * Build on mercury:
 *   gcc -O3 -Wall -Wextra -Werror -c mrv213_verbs_shim.c -o mrv213_verbs_shim.o
 *   rustc -O -D warnings mrv213_client.rs -C link-arg=mrv213_verbs_shim.o -C link-arg=-libverbs -o mrv213_client
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <infiniband/verbs.h>
#include <linux/ethtool.h>
#include <linux/sockios.h>
#include <net/if.h>
#include <netinet/in.h>
#include <netinet/udp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

#ifndef UDP_SEGMENT
#define UDP_SEGMENT 103
#endif

#define MAX_TX_BATCH 64
#define MAX_GSO_SEGS 44 /* 44 * 1454 = 63,976 B < 65,507 B max IPv4 UDP GSO datagram */

struct mrv213_tx_ctx {
    struct ibv_context *ctx;
    struct ibv_pd *pd;
    int port_num;
    int sgid_idx;
    uint8_t dst_ip[4];
};

struct mrv213_tx_chan {
    struct ibv_cq *cq;
    struct ibv_qp *qp;
    struct ibv_ah *ah; /* non-NULL for UD */
    struct ibv_mr *mr;
    uint8_t *buf;
    uint32_t slot_count;
    uint32_t slot_stride;
    uint32_t max_inline;
    int is_uc;
    uint32_t remote_qpn;
    struct ibv_send_wr wrs[MAX_TX_BATCH];
    struct ibv_sge sges[MAX_TX_BATCH];
    struct ibv_wc wcs[MAX_TX_BATCH];
};

struct mrv213_udp_chan {
    int sock;
    struct sockaddr_in dst_addr;
    uint8_t *buf;
    uint32_t max_segs;
    uint32_t seg_len;
    int use_gso;
    struct mmsghdr msgs[MAX_TX_BATCH];
    struct iovec iovs[MAX_TX_BATCH];
};

struct mrv213_phy_stats {
    uint64_t tx_packets_phy;
    uint64_t tx_bytes_phy;
    uint64_t rx_packets_phy;
    uint64_t rx_bytes_phy;
    uint64_t tx_discards_phy;
    uint64_t rx_crc_errors_phy;
    uint64_t tx_global_pause;
    uint64_t rx_global_pause;
};

struct mrv213_ethtool {
    int sock;
    char ifname[IFNAMSIZ];
    uint32_t n_stats;
    int idx_tx_pkts;
    int idx_tx_bytes;
    int idx_rx_pkts;
    int idx_rx_bytes;
    int idx_tx_disc;
    int idx_rx_crc;
    int idx_tx_pause;
    int idx_rx_pause;
    struct ethtool_stats *buf;
};

struct mrv213_tx_ctx *mrv213_open(const char *dev_name, int sgid_idx, const char *dst_ip_str) {
    int n = 0;
    struct ibv_device **list = ibv_get_device_list(&n);
    if (!list) return NULL;
    struct ibv_device *dev = NULL;
    for (int i = 0; i < n; i++) {
        if (strcmp(ibv_get_device_name(list[i]), dev_name) == 0) {
            dev = list[i];
            break;
        }
    }
    if (!dev) {
        ibv_free_device_list(list);
        return NULL;
    }
    struct ibv_context *ctx = ibv_open_device(dev);
    ibv_free_device_list(list);
    if (!ctx) return NULL;
    struct ibv_pd *pd = ibv_alloc_pd(ctx);
    if (!pd) {
        ibv_close_device(ctx);
        return NULL;
    }
    struct mrv213_tx_ctx *c = calloc(1, sizeof(*c));
    if (!c) {
        ibv_dealloc_pd(pd);
        ibv_close_device(ctx);
        return NULL;
    }
    c->ctx = ctx;
    c->pd = pd;
    c->port_num = 1;
    c->sgid_idx = sgid_idx;
    if (inet_pton(AF_INET, dst_ip_str, c->dst_ip) != 1) {
        ibv_dealloc_pd(pd);
        ibv_close_device(ctx);
        free(c);
        return NULL;
    }
    return c;
}

void mrv213_close(struct mrv213_tx_ctx *c) {
    if (!c) return;
    if (c->pd) ibv_dealloc_pd(c->pd);
    if (c->ctx) ibv_close_device(c->ctx);
    free(c);
}

static void fill_roce_dgid(union ibv_gid *dgid, const uint8_t ip[4]) {
    memset(dgid, 0, sizeof(*dgid));
    dgid->raw[10] = 0xff;
    dgid->raw[11] = 0xff;
    memcpy(&dgid->raw[12], ip, 4);
}

struct mrv213_tx_chan *mrv213_chan_create(
    struct mrv213_tx_ctx *c,
    int is_uc,
    uint32_t flow_label,
    uint32_t remote_qpn,
    uint32_t slot_count,
    uint32_t slot_stride,
    uint32_t want_inline
) {
    struct mrv213_tx_chan *ch = calloc(1, sizeof(*ch));
    if (!ch) return NULL;
    ch->is_uc = is_uc;
    ch->remote_qpn = remote_qpn;
    ch->slot_count = slot_count;
    ch->slot_stride = slot_stride;

    ch->cq = ibv_create_cq(c->ctx, 4096, NULL, NULL, 0);
    if (!ch->cq) goto fail;

    uint32_t try_inl[4] = { want_inline, 188, 128, 0 };
    for (int k = 0; k < 4; k++) {
        uint32_t inl = try_inl[k];
        if (k > 0 && inl >= want_inline) continue;
        struct ibv_qp_init_attr init;
        memset(&init, 0, sizeof(init));
        init.send_cq = ch->cq;
        init.recv_cq = ch->cq;
        init.qp_type = is_uc ? IBV_QPT_UC : IBV_QPT_UD;
        init.sq_sig_all = 0;
        init.cap.max_send_wr = 2048;
        init.cap.max_recv_wr = 16;
        init.cap.max_send_sge = 1;
        init.cap.max_recv_sge = 1;
        init.cap.max_inline_data = inl;
        ch->qp = ibv_create_qp(c->pd, &init);
        if (ch->qp) {
            ch->max_inline = init.cap.max_inline_data;
            break;
        }
    }
    if (!ch->qp) goto fail;

    struct ibv_qp_attr a;
    memset(&a, 0, sizeof(a));
    a.qp_state = IBV_QPS_INIT;
    a.pkey_index = 0;
    a.port_num = c->port_num;
    if (is_uc) {
        a.qp_access_flags = 0;
        if (ibv_modify_qp(ch->qp, &a, IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_ACCESS_FLAGS))
            goto fail;
        memset(&a, 0, sizeof(a));
        a.qp_state = IBV_QPS_RTR;
        a.path_mtu = IBV_MTU_1024;
        a.dest_qp_num = remote_qpn;
        a.rq_psn = 0;
        a.ah_attr.is_global = 1;
        a.ah_attr.port_num = c->port_num;
        a.ah_attr.grh.sgid_index = c->sgid_idx;
        a.ah_attr.grh.hop_limit = 64;
        a.ah_attr.grh.flow_label = flow_label & 0xfffff;
        fill_roce_dgid(&a.ah_attr.grh.dgid, c->dst_ip);
        if (ibv_modify_qp(ch->qp, &a, IBV_QP_STATE | IBV_QP_AV | IBV_QP_PATH_MTU | IBV_QP_DEST_QPN | IBV_QP_RQ_PSN))
            goto fail;
    } else {
        a.qkey = 0x11111111;
        if (ibv_modify_qp(ch->qp, &a, IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_QKEY))
            goto fail;
        memset(&a, 0, sizeof(a));
        a.qp_state = IBV_QPS_RTR;
        if (ibv_modify_qp(ch->qp, &a, IBV_QP_STATE))
            goto fail;
        struct ibv_ah_attr ah;
        memset(&ah, 0, sizeof(ah));
        ah.is_global = 1;
        ah.port_num = c->port_num;
        ah.grh.sgid_index = c->sgid_idx;
        ah.grh.hop_limit = 64;
        ah.grh.flow_label = flow_label & 0xfffff;
        fill_roce_dgid(&ah.grh.dgid, c->dst_ip);
        ch->ah = ibv_create_ah(c->pd, &ah);
        if (!ch->ah) goto fail;
    }

    memset(&a, 0, sizeof(a));
    a.qp_state = IBV_QPS_RTS;
    a.sq_psn = 0;
    if (ibv_modify_qp(ch->qp, &a, IBV_QP_STATE | IBV_QP_SQ_PSN))
        goto fail;

    size_t total = (size_t)slot_count * (size_t)slot_stride;
    if (total < 4096) total = 4096;
    if (posix_memalign((void **)&ch->buf, 4096, total) != 0) goto fail;
    memset(ch->buf, 0x3c, total);
    ch->mr = ibv_reg_mr(c->pd, ch->buf, total, IBV_ACCESS_LOCAL_WRITE);
    if (!ch->mr) goto fail;

    for (int i = 0; i < MAX_TX_BATCH; i++) {
        memset(&ch->wrs[i], 0, sizeof(ch->wrs[i]));
        ch->wrs[i].opcode = IBV_WR_SEND;
        ch->sges[i].lkey = ch->mr->lkey;
        if (!ch->is_uc) {
            ch->wrs[i].wr.ud.ah = ch->ah;
            ch->wrs[i].wr.ud.remote_qpn = ch->remote_qpn;
            ch->wrs[i].wr.ud.remote_qkey = 0x11111111;
        }
    }
    return ch;

fail:
    if (ch->mr) ibv_dereg_mr(ch->mr);
    if (ch->buf) free(ch->buf);
    if (ch->ah) ibv_destroy_ah(ch->ah);
    if (ch->qp) ibv_destroy_qp(ch->qp);
    if (ch->cq) ibv_destroy_cq(ch->cq);
    free(ch);
    return NULL;
}

uint8_t *mrv213_chan_buf(struct mrv213_tx_chan *ch) { return ch->buf; }
uint32_t mrv213_chan_max_inline(struct mrv213_tx_chan *ch) { return ch->max_inline; }

void mrv213_chan_destroy(struct mrv213_tx_chan *ch) {
    if (!ch) return;
    if (ch->mr) ibv_dereg_mr(ch->mr);
    if (ch->buf) free(ch->buf);
    if (ch->ah) ibv_destroy_ah(ch->ah);
    if (ch->qp) ibv_destroy_qp(ch->qp);
    if (ch->cq) ibv_destroy_cq(ch->cq);
    free(ch);
}

int mrv213_chan_post_batch(
    struct mrv213_tx_chan *ch,
    uint32_t start_slot,
    uint32_t slot_mask,
    uint32_t count,
    uint32_t payload_len,
    uint64_t wr_id_tag
) {
    if (count == 0 || count > MAX_TX_BATCH) return -EINVAL;
    unsigned int base_flags = (payload_len <= ch->max_inline) ? IBV_SEND_INLINE : 0;
    if (payload_len > 0) {
        for (uint32_t i = 0; i < count; i++) {
            uint32_t slot = (start_slot + i) & slot_mask;
            struct ibv_send_wr *w = &ch->wrs[i];
            ch->sges[i].addr = (uintptr_t)(ch->buf + (size_t)slot * ch->slot_stride);
            ch->sges[i].length = payload_len;
            w->sg_list = &ch->sges[i];
            w->num_sge = 1;
            w->wr_id = wr_id_tag;
            w->next = (i + 1 < count) ? &ch->wrs[i + 1] : NULL;
            w->send_flags = base_flags | ((i + 1 == count) ? IBV_SEND_SIGNALED : 0);
        }
    } else {
        for (uint32_t i = 0; i < count; i++) {
            struct ibv_send_wr *w = &ch->wrs[i];
            w->sg_list = NULL;
            w->num_sge = 0;
            w->wr_id = wr_id_tag;
            w->next = (i + 1 < count) ? &ch->wrs[i + 1] : NULL;
            w->send_flags = base_flags | ((i + 1 == count) ? IBV_SEND_SIGNALED : 0);
        }
    }
    struct ibv_send_wr *bad = NULL;
    return ibv_post_send(ch->qp, &ch->wrs[0], &bad);
}

int mrv213_chan_poll_cq(struct mrv213_tx_chan *ch, int max_wc, uint64_t *err_out) {
    if (max_wc > MAX_TX_BATCH) max_wc = MAX_TX_BATCH;
    int n = ibv_poll_cq(ch->cq, max_wc, ch->wcs);
    uint64_t errs = 0;
    for (int i = 0; i < n; i++) {
        if (ch->wcs[i].status != IBV_WC_SUCCESS) errs++;
    }
    if (err_out) *err_out += errs;
    return n;
}

/* Non-root Linux UDP GSO (`UDP_SEGMENT`) + `sendmmsg` fallback channel for exact 1500 B L2 frames */
struct mrv213_udp_chan *mrv213_udp_create(
    const char *src_ip_str,
    uint16_t src_port,
    const char *dst_ip_str,
    uint16_t dst_port,
    uint32_t seg_len,
    int want_gso
) {
    struct mrv213_udp_chan *u = calloc(1, sizeof(*u));
    if (!u) return NULL;
    u->sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (u->sock < 0) { free(u); return NULL; }

    int sndbuf = 16 * 1024 * 1024;
    (void)setsockopt(u->sock, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));
    int reuse = 1;
    (void)setsockopt(u->sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    struct sockaddr_in local;
    memset(&local, 0, sizeof(local));
    local.sin_family = AF_INET;
    local.sin_port = htons(src_port);
    if (src_ip_str && src_ip_str[0] != '\0') {
        if (inet_pton(AF_INET, src_ip_str, &local.sin_addr) != 1) goto fail;
    } else {
        local.sin_addr.s_addr = INADDR_ANY;
    }
    if (bind(u->sock, (struct sockaddr *)&local, sizeof(local)) < 0) goto fail;

    memset(&u->dst_addr, 0, sizeof(u->dst_addr));
    u->dst_addr.sin_family = AF_INET;
    u->dst_addr.sin_port = htons(dst_port);
    if (inet_pton(AF_INET, dst_ip_str, &u->dst_addr.sin_addr) != 1) goto fail;
    if (connect(u->sock, (struct sockaddr *)&u->dst_addr, sizeof(u->dst_addr)) < 0) goto fail;

    u->seg_len = seg_len;
    u->max_segs = MAX_TX_BATCH;
    size_t buf_sz = (size_t)u->max_segs * (size_t)seg_len;
    if (posix_memalign((void **)&u->buf, 4096, buf_sz) != 0) goto fail;
    memset(u->buf, 0x3c, buf_sz);

    u->use_gso = want_gso ? 1 : 0;
    for (uint32_t i = 0; i < MAX_TX_BATCH; i++) {
        u->iovs[i].iov_base = u->buf + (size_t)i * seg_len;
        u->iovs[i].iov_len = seg_len;
        memset(&u->msgs[i], 0, sizeof(u->msgs[i]));
        u->msgs[i].msg_hdr.msg_iov = &u->iovs[i];
        u->msgs[i].msg_hdr.msg_iovlen = 1;
    }
    return u;

fail:
    if (u->buf) free(u->buf);
    if (u->sock >= 0) close(u->sock);
    free(u);
    return NULL;
}

uint8_t *mrv213_udp_buf(struct mrv213_udp_chan *u) { return u->buf; }
int mrv213_udp_gso_active(struct mrv213_udp_chan *u) { return u->use_gso; }

int mrv213_udp_send_batch(struct mrv213_udp_chan *u, uint32_t count) {
    if (count == 0 || count > MAX_TX_BATCH) return -EINVAL;
    uint32_t sent_pkts = 0;

    if (u->use_gso && count > 1) {
        while (sent_pkts < count) {
            uint32_t n = count - sent_pkts;
            if (n > MAX_GSO_SEGS) n = MAX_GSO_SEGS;
            if (n == 1) {
                ssize_t r = send(u->sock, u->buf + (size_t)sent_pkts * u->seg_len, u->seg_len, 0);
                if (r < 0) return -errno;
                sent_pkts += 1;
                continue;
            }
            struct iovec iov;
            iov.iov_base = u->buf + (size_t)sent_pkts * u->seg_len;
            iov.iov_len = (size_t)n * u->seg_len;

            char cbuf[CMSG_SPACE(sizeof(uint16_t))];
            memset(cbuf, 0, sizeof(cbuf));
            struct msghdr msg;
            memset(&msg, 0, sizeof(msg));
            msg.msg_iov = &iov;
            msg.msg_iovlen = 1;
            msg.msg_control = cbuf;
            msg.msg_controllen = sizeof(cbuf);

            struct cmsghdr *cm = CMSG_FIRSTHDR(&msg);
            cm->cmsg_level = SOL_UDP;
            cm->cmsg_type = UDP_SEGMENT;
            cm->cmsg_len = CMSG_LEN(sizeof(uint16_t));
            uint16_t gso_sz = (uint16_t)u->seg_len;
            memcpy(CMSG_DATA(cm), &gso_sz, sizeof(gso_sz));

            ssize_t r = sendmsg(u->sock, &msg, 0);
            if (r < 0) {
                if (errno == EINVAL || errno == EIO || errno == ENOPROTOOPT || errno == EOPNOTSUPP) {
                    /* Kernel/NIC rejected UDP_SEGMENT: disable GSO and fall back cleanly to sendmmsg */
                    u->use_gso = 0;
                    break;
                }
                if (errno == EAGAIN || errno == ENOBUFS) {
                    continue;
                }
                return -errno;
            }
            sent_pkts += n;
        }
    }

    while (sent_pkts < count) {
        int r = sendmmsg(u->sock, &u->msgs[sent_pkts], count - sent_pkts, 0);
        if (r < 0) {
            if (errno == EAGAIN || errno == ENOBUFS) {
                continue;
            }
            return -errno;
        }
        if (r == 0) return -EIO;
        sent_pkts += (uint32_t)r;
    }
    return (int)sent_pkts;
}

void mrv213_udp_destroy(struct mrv213_udp_chan *u) {
    if (!u) return;
    if (u->buf) free(u->buf);
    if (u->sock >= 0) close(u->sock);
    free(u);
}

/* In-process ETHTOOL_GSTATS reader (~100 us, zero subprocess fork overhead) */
struct mrv213_ethtool *mrv213_ethtool_open(const char *ifname) {
    struct mrv213_ethtool *et = calloc(1, sizeof(*et));
    if (!et) return NULL;
    et->sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (et->sock < 0) { free(et); return NULL; }
    snprintf(et->ifname, sizeof(et->ifname), "%s", ifname);
    et->idx_tx_pkts = et->idx_tx_bytes = et->idx_rx_pkts = et->idx_rx_bytes = -1;
    et->idx_tx_disc = et->idx_rx_crc = et->idx_tx_pause = et->idx_rx_pause = -1;

    struct {
        struct ethtool_sset_info hdr;
        uint32_t data[1];
    } sset;
    memset(&sset, 0, sizeof(sset));
    sset.hdr.cmd = ETHTOOL_GSSET_INFO;
    sset.hdr.sset_mask = 1ULL << ETH_SS_STATS;
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    snprintf(ifr.ifr_name, sizeof(ifr.ifr_name), "%s", ifname);
    ifr.ifr_data = (char *)&sset;
    if (ioctl(et->sock, SIOCETHTOOL, &ifr) < 0 || !sset.hdr.sset_mask) {
        close(et->sock); free(et); return NULL;
    }
    et->n_stats = sset.data[0];

    size_t str_sz = sizeof(struct ethtool_gstrings) + (size_t)et->n_stats * ETH_GSTRING_LEN;
    struct ethtool_gstrings *gstr = calloc(1, str_sz);
    if (!gstr) { close(et->sock); free(et); return NULL; }
    gstr->cmd = ETHTOOL_GSTRINGS;
    gstr->string_set = ETH_SS_STATS;
    gstr->len = et->n_stats;
    ifr.ifr_data = (char *)gstr;
    if (ioctl(et->sock, SIOCETHTOOL, &ifr) < 0) {
        free(gstr); close(et->sock); free(et); return NULL;
    }
    for (uint32_t i = 0; i < et->n_stats; i++) {
        const char *name = (const char *)&gstr->data[i * ETH_GSTRING_LEN];
        if (strcmp(name, "tx_packets_phy") == 0) et->idx_tx_pkts = (int)i;
        else if (strcmp(name, "tx_bytes_phy") == 0) et->idx_tx_bytes = (int)i;
        else if (strcmp(name, "rx_packets_phy") == 0) et->idx_rx_pkts = (int)i;
        else if (strcmp(name, "rx_bytes_phy") == 0) et->idx_rx_bytes = (int)i;
        else if (strcmp(name, "tx_discards_phy") == 0) et->idx_tx_disc = (int)i;
        else if (strcmp(name, "rx_crc_errors_phy") == 0) et->idx_rx_crc = (int)i;
        else if (strcmp(name, "tx_global_pause") == 0) et->idx_tx_pause = (int)i;
        else if (strcmp(name, "rx_global_pause") == 0) et->idx_rx_pause = (int)i;
    }
    free(gstr);

    size_t st_sz = sizeof(struct ethtool_stats) + (size_t)et->n_stats * sizeof(uint64_t);
    et->buf = calloc(1, st_sz);
    if (!et->buf) { close(et->sock); free(et); return NULL; }
    et->buf->cmd = ETHTOOL_GSTATS;
    et->buf->n_stats = et->n_stats;
    return et;
}

int mrv213_ethtool_read(struct mrv213_ethtool *et, struct mrv213_phy_stats *out) {
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    snprintf(ifr.ifr_name, sizeof(ifr.ifr_name), "%s", et->ifname);
    et->buf->cmd = ETHTOOL_GSTATS;
    et->buf->n_stats = et->n_stats;
    ifr.ifr_data = (char *)et->buf;
    if (ioctl(et->sock, SIOCETHTOOL, &ifr) < 0) return -errno;
    memset(out, 0, sizeof(*out));
    if (et->idx_tx_pkts >= 0) out->tx_packets_phy = et->buf->data[et->idx_tx_pkts];
    if (et->idx_tx_bytes >= 0) out->tx_bytes_phy = et->buf->data[et->idx_tx_bytes];
    if (et->idx_rx_pkts >= 0) out->rx_packets_phy = et->buf->data[et->idx_rx_pkts];
    if (et->idx_rx_bytes >= 0) out->rx_bytes_phy = et->buf->data[et->idx_rx_bytes];
    if (et->idx_tx_disc >= 0) out->tx_discards_phy = et->buf->data[et->idx_tx_disc];
    if (et->idx_rx_crc >= 0) out->rx_crc_errors_phy = et->buf->data[et->idx_rx_crc];
    if (et->idx_tx_pause >= 0) out->tx_global_pause = et->buf->data[et->idx_tx_pause];
    if (et->idx_rx_pause >= 0) out->rx_global_pause = et->buf->data[et->idx_rx_pause];
    return 0;
}

void mrv213_ethtool_close(struct mrv213_ethtool *et) {
    if (!et) return;
    if (et->buf) free(et->buf);
    if (et->sock >= 0) close(et->sock);
    free(et);
}
