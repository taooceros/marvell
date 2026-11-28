/* Thin C FFI shim around libibverbs static-inline fast-path functions
 * (ibv_post_send / ibv_post_recv / ibv_poll_cq) for multi-QP mrv211_client on mercury.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include <infiniband/verbs.h>

typedef struct {
    struct ibv_context *ctx;
    struct ibv_pd *pd;
    struct ibv_cq *send_cq;
    struct ibv_cq *recv_cq;
    struct ibv_qp *qp;
    struct ibv_ah *ah;
    struct ibv_mr *rx_mr;
    struct ibv_mr *tx_mr;
    uint8_t *rx_buf;
    uint8_t *tx_buf;
    uint32_t local_qpn;
    uint32_t max_inline_data;
    uint32_t rx_slots;
    uint32_t rx_stride;
    uint32_t tx_slots;
    uint32_t tx_stride;
    uint32_t remote_qpn;
    uint32_t remote_qkey;
    uint64_t unsignaled_cnt;
    int sgid_index;
    uint8_t dst_ip[4];
} UdClient;

static struct ibv_ah *make_ah(struct ibv_pd *pd, int sgid_index, const uint8_t dst_ip[4], uint32_t flow_label) {
    struct ibv_ah_attr aha;
    memset(&aha, 0, sizeof(aha));
    aha.is_global = 1;
    aha.port_num = 1;
    aha.grh.sgid_index = sgid_index;
    aha.grh.flow_label = flow_label & 0xfffff;
    aha.grh.hop_limit = 64;
    uint8_t dgid[16] = {0,0,0,0, 0,0,0,0, 0,0,0xff,0xff, dst_ip[0], dst_ip[1], dst_ip[2], dst_ip[3]};
    memcpy(aha.grh.dgid.raw, dgid, 16);
    return ibv_create_ah(pd, &aha);
}

UdClient *ud_open(const char *dev_name, int sgid_index, const uint8_t dst_ip[4],
                  uint32_t remote_qpn, uint32_t remote_qkey, uint32_t flow_label,
                  uint32_t rx_slots, uint32_t rx_stride,
                  uint32_t tx_slots, uint32_t tx_stride) {
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
    if (!dev) { ibv_free_device_list(list); return NULL; }
    UdClient *u = calloc(1, sizeof(*u));
    if (!u) { ibv_free_device_list(list); return NULL; }
    u->ctx = ibv_open_device(dev);
    ibv_free_device_list(list);
    if (!u->ctx) { free(u); return NULL; }

    u->pd = ibv_alloc_pd(u->ctx);
    u->send_cq = ibv_create_cq(u->ctx, 4096, NULL, NULL, 0);
    u->recv_cq = ibv_create_cq(u->ctx, 4096, NULL, NULL, 0);

    struct ibv_qp_init_attr qa;
    memset(&qa, 0, sizeof(qa));
    qa.send_cq = u->send_cq;
    qa.recv_cq = u->recv_cq;
    qa.qp_type = IBV_QPT_UD;
    qa.cap.max_send_wr = 2048;
    qa.cap.max_recv_wr = rx_slots;
    qa.cap.max_send_sge = 1;
    qa.cap.max_recv_sge = 1;
    qa.cap.max_inline_data = 256;
    u->qp = ibv_create_qp(u->pd, &qa);
    if (!u->qp) {
        qa.cap.max_inline_data = 64;
        u->qp = ibv_create_qp(u->pd, &qa);
    }
    if (!u->qp) {
        qa.cap.max_inline_data = 0;
        u->qp = ibv_create_qp(u->pd, &qa);
    }
    if (!u->qp) { fprintf(stderr, "ibv_create_qp failed: %d\n", errno); return NULL; }
    u->max_inline_data = qa.cap.max_inline_data;
    u->local_qpn = u->qp->qp_num;
    u->remote_qpn = remote_qpn;
    u->remote_qkey = remote_qkey;
    u->rx_slots = rx_slots;
    u->rx_stride = rx_stride;
    u->tx_slots = tx_slots;
    u->tx_stride = tx_stride;
    u->sgid_index = sgid_index;
    memcpy(u->dst_ip, dst_ip, 4);

    struct ibv_qp_attr mod;
    memset(&mod, 0, sizeof(mod));
    mod.qp_state = IBV_QPS_INIT;
    mod.pkey_index = 0;
    mod.port_num = 1;
    mod.qkey = remote_qkey;
    if (ibv_modify_qp(u->qp, &mod, IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_QKEY)) return NULL;
    memset(&mod, 0, sizeof(mod));
    mod.qp_state = IBV_QPS_RTR;
    if (ibv_modify_qp(u->qp, &mod, IBV_QP_STATE)) return NULL;
    memset(&mod, 0, sizeof(mod));
    mod.qp_state = IBV_QPS_RTS;
    mod.sq_psn = 0;
    if (ibv_modify_qp(u->qp, &mod, IBV_QP_STATE | IBV_QP_SQ_PSN)) return NULL;

    size_t rx_sz = (size_t)rx_slots * rx_stride;
    size_t tx_sz = (size_t)tx_slots * tx_stride;
    if (posix_memalign((void **)&u->rx_buf, 4096, rx_sz)) return NULL;
    if (posix_memalign((void **)&u->tx_buf, 4096, tx_sz)) return NULL;
    memset(u->rx_buf, 0, rx_sz);
    memset(u->tx_buf, 0, tx_sz);
    u->rx_mr = ibv_reg_mr(u->pd, u->rx_buf, rx_sz, IBV_ACCESS_LOCAL_WRITE);
    u->tx_mr = ibv_reg_mr(u->pd, u->tx_buf, tx_sz, IBV_ACCESS_LOCAL_WRITE);
    if (!u->rx_mr || !u->tx_mr) return NULL;

    u->ah = make_ah(u->pd, sgid_index, dst_ip, flow_label);
    if (!u->ah) return NULL;

    for (uint32_t i = 0; i < rx_slots; i++) {
        struct ibv_sge sge = { .addr = (uintptr_t)(u->rx_buf + (size_t)i * rx_stride), .length = rx_stride, .lkey = u->rx_mr->lkey };
        struct ibv_recv_wr wr = { .wr_id = i, .sg_list = &sge, .num_sge = 1 }, *bad = NULL;
        if (ibv_post_recv(u->qp, &wr, &bad)) return NULL;
    }
    return u;
}

int ud_set_flow(UdClient *u, uint32_t flow_label, uint32_t remote_qpn) {
    struct ibv_ah *new_ah = make_ah(u->pd, u->sgid_index, u->dst_ip, flow_label);
    if (!new_ah) return -1;
    if (u->ah) ibv_destroy_ah(u->ah);
    u->ah = new_ah;
    u->remote_qpn = remote_qpn;
    return 0;
}

uint8_t *ud_rx_buf(UdClient *u) { return u->rx_buf; }
uint8_t *ud_tx_buf(UdClient *u) { return u->tx_buf; }
uint32_t ud_local_qpn(UdClient *u) { return u->local_qpn; }
uint32_t ud_max_inline(UdClient *u) { return u->max_inline_data; }

int ud_post_send_batch(UdClient *u, const uint32_t *tx_slots, const uint32_t *lens, int count, int force_sig) {
    struct ibv_sge sges[64];
    struct ibv_send_wr wrs[64];
    if (count <= 0) return 0;
    if (count > 64) count = 64;
    for (int i = 0; i < count; i++) {
        uint32_t tx_slot = tx_slots[i];
        uint32_t len = lens[i];
        uint8_t *base = u->tx_buf + (size_t)tx_slot * u->tx_stride;
        int can_inline = (len <= u->max_inline_data);
        sges[i].addr = (uintptr_t)base;
        sges[i].length = len;
        sges[i].lkey = u->tx_mr->lkey;
        memset(&wrs[i], 0, sizeof(wrs[i]));
        wrs[i].wr_id = tx_slot;
        wrs[i].sg_list = &sges[i];
        wrs[i].num_sge = 1;
        wrs[i].opcode = IBV_WR_SEND;
        wrs[i].wr.ud.ah = u->ah;
        wrs[i].wr.ud.remote_qpn = u->remote_qpn;
        wrs[i].wr.ud.remote_qkey = u->remote_qkey;
        unsigned int flags = can_inline ? IBV_SEND_INLINE : 0;
        u->unsignaled_cnt++;
        if (force_sig || u->unsignaled_cnt >= 64) {
            flags |= IBV_SEND_SIGNALED;
            u->unsignaled_cnt = 0;
        }
        wrs[i].send_flags = flags;
        wrs[i].next = (i + 1 < count) ? &wrs[i + 1] : NULL;
    }
    struct ibv_send_wr *bad = NULL;
    return ibv_post_send(u->qp, &wrs[0], &bad);
}

int ud_drain_send_cq(UdClient *u) {
    struct ibv_wc wcs[32];
    int n = ibv_poll_cq(u->send_cq, 32, wcs);
    for (int i = 0; i < n; i++) {
        if (wcs[i].status != IBV_WC_SUCCESS) return -1;
    }
    return n;
}

typedef struct {
    uint32_t slot;
    uint32_t byte_len;
    int32_t status;
} RxComp;

int ud_poll_rx(UdClient *u, RxComp *out, int max_n) {
    struct ibv_wc wcs[32];
    if (max_n > 32) max_n = 32;
    int n = ibv_poll_cq(u->recv_cq, max_n, wcs);
    for (int i = 0; i < n; i++) {
        out[i].slot = (uint32_t)wcs[i].wr_id;
        out[i].byte_len = wcs[i].byte_len;
        out[i].status = (int32_t)wcs[i].status;
    }
    return n;
}

int ud_repost_rx_batch(UdClient *u, const uint32_t *slots, int count) {
    if (count <= 0) return 0;
    struct ibv_sge sges[32];
    struct ibv_recv_wr wrs[32];
    if (count > 32) count = 32;
    for (int i = 0; i < count; i++) {
        uint32_t s = slots[i];
        sges[i].addr = (uintptr_t)(u->rx_buf + (size_t)s * u->rx_stride);
        sges[i].length = u->rx_stride;
        sges[i].lkey = u->rx_mr->lkey;
        wrs[i].wr_id = s;
        wrs[i].sg_list = &sges[i];
        wrs[i].num_sge = 1;
        wrs[i].next = (i + 1 < count) ? &wrs[i + 1] : NULL;
    }
    struct ibv_recv_wr *bad = NULL;
    return ibv_post_recv(u->qp, &wrs[0], &bad);
}

void ud_close(UdClient *u) {
    if (!u) return;
    if (u->ah) ibv_destroy_ah(u->ah);
    if (u->qp) ibv_destroy_qp(u->qp);
    if (u->rx_mr) ibv_dereg_mr(u->rx_mr);
    if (u->tx_mr) ibv_dereg_mr(u->tx_mr);
    if (u->send_cq) ibv_destroy_cq(u->send_cq);
    if (u->recv_cq) ibv_destroy_cq(u->recv_cq);
    if (u->pd) ibv_dealloc_pd(u->pd);
    if (u->ctx) ibv_close_device(u->ctx);
    free(u->rx_buf);
    free(u->tx_buf);
    free(u);
}
