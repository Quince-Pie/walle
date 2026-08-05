#ifndef WALLE_URING_H
#define WALLE_URING_H

#include <linux/io_uring.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>

struct uring_hot
{
    alignas(64) struct io_uring_sqe* sqes;
    _Atomic uint32_t* sq_ktail;
    _Atomic uint32_t* sq_khead;
    uint32_t          sq_tail;
    uint32_t          sq_head;
    uint32_t          sq_mask;
    uint32_t          sq_entries;
    int32_t           enter_fd;
    uint32_t          enter_flags;
    _Atomic uint32_t* sq_kflags;
    uint64_t          sq_reserved;

    struct io_uring_cqe* cqes;
    _Atomic uint32_t*    cq_khead;
    _Atomic uint32_t*    cq_ktail;
    uint32_t             cq_head;
    uint32_t             cq_tail;
    uint32_t             cq_mask;
    uint32_t             cq_entries;
    uint64_t             cq_reserved[3];
};

struct uring_cold
{
    int               ring_fd;
    uint32_t          features;
    _Atomic uint32_t* sq_kdropped;
    _Atomic uint32_t* cq_koverflow;
    uint32_t          last_sq_dropped;
    uint32_t          last_cq_overflow;
    void*             ring_mem;
    size_t            ring_size;
    void*             sqe_mem;
    size_t            sqe_size;
    bool              registered;
    bool              files_registered;
    uint32_t          file_count;
};

struct uring
{
    struct uring_hot  hot;
    struct uring_cold cold;
};

static_assert(ATOMIC_INT_LOCK_FREE == 2);
static_assert(sizeof(uint32_t) == 4);
static_assert(sizeof(_Atomic uint32_t) == sizeof(uint32_t));
static_assert(alignof(_Atomic uint32_t) == alignof(uint32_t));
static_assert(alignof(struct uring_hot) == 64);
static_assert(sizeof(struct uring_hot) == 128);
static_assert(offsetof(struct uring_hot, enter_fd) == 40);
static_assert(offsetof(struct uring_hot, cqes) == 64);
static_assert(sizeof(struct io_uring_sqe) == 64);
static_assert(sizeof(struct io_uring_cqe) == 16);

[[nodiscard]]
int uring_init(struct uring* ring, uint32_t entries, uint32_t fixed_files);

void uring_exit(struct uring* ring);

[[nodiscard]]
long uring_enter(struct uring* ring, uint32_t min_complete);

[[nodiscard]]
int uring_check_health(struct uring* ring);

[[nodiscard]]
int uring_notify(int ring_fd, uint64_t user_data);

[[nodiscard]]
int uring_update_files(struct uring* ring, uint32_t offset, const int* files, uint32_t count);

static inline int uring_real_fd(const struct uring* ring)
{
    return ring->cold.ring_fd;
}

static inline uint32_t uring_sq_space(const struct uring_hot* ring)
{
    return ring->sq_entries - (ring->sq_tail - ring->sq_head);
}

static inline struct io_uring_sqe* uring_get_sqe(struct uring_hot* ring)
{
    if (uring_sq_space(ring) == 0)
        return nullptr;

    struct io_uring_sqe* sqe = &ring->sqes[ring->sq_tail++ & ring->sq_mask];
    *sqe                     = (struct io_uring_sqe){};
    return sqe;
}

static inline uint32_t uring_cq_begin(struct uring_hot* ring)
{
    ring->cq_tail = atomic_load_explicit(ring->cq_ktail, memory_order_acquire);
    return ring->cq_tail - ring->cq_head;
}

static inline const struct io_uring_cqe* uring_cqe_at(const struct uring_hot* ring,
                                                      uint32_t                ordinal)
{
    return &ring->cqes[(ring->cq_head + ordinal) & ring->cq_mask];
}

static inline void uring_cq_commit(struct uring_hot* ring, uint32_t count)
{
    if (count == 0)
        return;
    ring->cq_head += count;
    atomic_store_explicit(ring->cq_khead, ring->cq_head, memory_order_release);
}

#endif
