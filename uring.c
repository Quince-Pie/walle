#define _GNU_SOURCE

#include "uring.h"

#include <errno.h>
#include <limits.h>
#include <linux/io_uring/query.h>
#include <sched.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

constexpr uint32_t URING_REQUIRED_FEATURES = IORING_FEAT_SINGLE_MMAP | IORING_FEAT_NODROP
                                             | IORING_FEAT_FAST_POLL | IORING_FEAT_POLL_32BITS
                                             | IORING_FEAT_REG_REG_RING;

constexpr uint64_t URING_REQUIRED_SETUP_FLAGS = IORING_SETUP_SINGLE_ISSUER
                                                | IORING_SETUP_DEFER_TASKRUN
                                                | IORING_SETUP_NO_SQARRAY | IORING_SETUP_SUBMIT_ALL;
constexpr uint64_t URING_REQUIRED_ENTER_FLAGS
    = IORING_ENTER_GETEVENTS | IORING_ENTER_REGISTERED_RING;

static long raw_setup(uint32_t entries, struct io_uring_params* params)
{
    long rc = syscall(__NR_io_uring_setup, entries, params);
    return rc < 0 ? -errno : rc;
}

static long raw_enter(int fd, uint32_t to_submit, uint32_t min_complete, uint32_t flags)
{
    long rc = syscall(__NR_io_uring_enter, fd, to_submit, min_complete, flags, nullptr, 0);
    return rc < 0 ? -errno : rc;
}

static long raw_register(int fd, uint32_t opcode, const void* arg, uint32_t count)
{
    long rc = syscall(__NR_io_uring_register, fd, opcode, arg, count);
    return rc < 0 ? -errno : rc;
}

static int query_runtime(void)
{
    struct io_uring_query_opcode opcodes = {};
    struct io_uring_query_hdr    query   = {
             .query_data = (uint64_t)(uintptr_t)&opcodes,
             .query_op   = IO_URING_QUERY_OPCODES,
             .size       = sizeof opcodes,
    };
    long rc = raw_register(-1, IORING_REGISTER_QUERY, &query, 0);
    if (rc < 0)
        return (int)rc;
    if (query.result < 0)
        return query.result;
    if (opcodes.nr_request_opcodes <= IORING_OP_MSG_RING
        || opcodes.nr_register_opcodes <= IORING_REGISTER_SEND_MSG_RING
        || (opcodes.feature_flags & URING_REQUIRED_FEATURES) != URING_REQUIRED_FEATURES
        || (opcodes.ring_setup_flags & URING_REQUIRED_SETUP_FLAGS) != URING_REQUIRED_SETUP_FLAGS
        || (opcodes.enter_flags & URING_REQUIRED_ENTER_FLAGS) != URING_REQUIRED_ENTER_FLAGS
        || (opcodes.sqe_flags & IOSQE_FIXED_FILE) == 0)
        return -EOPNOTSUPP;
    return 0;
}

static bool probe_has(const struct io_uring_probe* probe, uint8_t opcode)
{
    for (uint32_t i = 0; i < probe->ops_len; i++) {
        if (probe->ops[i].op == opcode)
            return (probe->ops[i].flags & IO_URING_OP_SUPPORTED) != 0;
    }
    return false;
}

static int require_opcodes(int ring_fd)
{
    constexpr uint32_t probe_count = 256;
    constexpr uint8_t  required[]  = {
        IORING_OP_READ,
        IORING_OP_POLL_ADD,
        IORING_OP_ASYNC_CANCEL,
        IORING_OP_TIMEOUT,
        IORING_OP_TIMEOUT_REMOVE,
        IORING_OP_MSG_RING,
    };

    size_t bytes = sizeof(struct io_uring_probe) + probe_count * sizeof(struct io_uring_probe_op);
    struct io_uring_probe* probe = calloc(1, bytes);
    if (!probe)
        return -ENOMEM;

    long rc = raw_register(ring_fd, IORING_REGISTER_PROBE, probe, probe_count);
    if (rc < 0) {
        free(probe);
        return (int)rc;
    }

    int result = 0;
    for (size_t i = 0; i < sizeof required / sizeof *required; i++) {
        if (!probe_has(probe, required[i])) {
            result = -EOPNOTSUPP;
            break;
        }
    }
    free(probe);
    return result;
}

static int register_ring(struct uring* ring)
{
    struct io_uring_rsrc_update update = {
        .offset = UINT32_MAX,
        /* REGISTER_RING_FDS is the exceptional rsrc-update operation whose
         * data field carries the fd value itself, not a userspace pointer. */
        .data = (uint64_t)(int64_t)ring->cold.ring_fd,
    };
    long rc = raw_register(ring->cold.ring_fd, IORING_REGISTER_RING_FDS, &update, 1);
    if (rc != 1)
        return rc < 0 ? (int)rc : -EIO;

    ring->hot.enter_fd    = (int32_t)update.offset;
    ring->hot.enter_flags = IORING_ENTER_GETEVENTS | IORING_ENTER_REGISTERED_RING;
    ring->cold.registered = true;
    return 0;
}

static void unregister_ring(struct uring* ring)
{
    if (!ring->cold.registered || ring->cold.ring_fd < 0)
        return;

    struct io_uring_rsrc_update update = {.offset = (uint32_t)ring->hot.enter_fd};
    (void)raw_register(ring->cold.ring_fd, IORING_UNREGISTER_RING_FDS, &update, 1);
    ring->cold.registered = false;
}

static int register_sparse_files(struct uring* ring, uint32_t count)
{
    if (count == 0)
        return 0;
    struct io_uring_rsrc_register resources = {
        .nr    = count,
        .flags = IORING_RSRC_REGISTER_SPARSE,
    };
    long rc = raw_register(ring->hot.enter_fd,
                           IORING_REGISTER_FILES2 | IORING_REGISTER_USE_REGISTERED_RING,
                           &resources,
                           sizeof resources);
    if (rc < 0)
        return (int)rc;
    ring->cold.files_registered = true;
    ring->cold.file_count       = count;
    return 0;
}

static void unregister_files(struct uring* ring)
{
    if (!ring->cold.files_registered || ring->cold.ring_fd < 0)
        return;
    (void)raw_register(ring->cold.ring_fd, IORING_UNREGISTER_FILES, nullptr, 0);
    ring->cold.files_registered = false;
    ring->cold.file_count       = 0;
}

static int advise_mapping(void* mapping, size_t size)
{
    if (madvise(mapping, size, MADV_DONTFORK) < 0)
        return -errno;
    if (madvise(mapping, size, MADV_DONTDUMP) < 0)
        return -errno;
    return 0;
}

int uring_init(struct uring* ring, uint32_t entries, uint32_t fixed_files)
{
    *ring = (struct uring){.cold = {.ring_fd = -1}};

    int query = query_runtime();
    if (query < 0)
        return query;

    struct io_uring_params params = {
        .flags = (uint32_t)URING_REQUIRED_SETUP_FLAGS,
    };
    long setup_rc = raw_setup(entries, &params);
    if (setup_rc < 0)
        return (int)setup_rc;
    ring->cold.ring_fd = (int)setup_rc;

    if ((params.features & URING_REQUIRED_FEATURES) != URING_REQUIRED_FEATURES) {
        uring_exit(ring);
        return -EOPNOTSUPP;
    }
    ring->cold.features = params.features;

    size_t sq_ring_size  = params.sq_off.array;
    size_t cq_ring_size  = params.cq_off.cqes + params.cq_entries * sizeof(struct io_uring_cqe);
    ring->cold.ring_size = sq_ring_size > cq_ring_size ? sq_ring_size : cq_ring_size;
    ring->cold.sqe_size  = params.sq_entries * sizeof(struct io_uring_sqe);

    ring->cold.ring_mem = mmap(nullptr,
                               ring->cold.ring_size,
                               PROT_READ | PROT_WRITE,
                               MAP_SHARED,
                               ring->cold.ring_fd,
                               IORING_OFF_SQ_RING);
    if (ring->cold.ring_mem == MAP_FAILED) {
        int error           = -errno;
        ring->cold.ring_mem = nullptr;
        uring_exit(ring);
        return error;
    }

    ring->cold.sqe_mem = mmap(nullptr,
                              ring->cold.sqe_size,
                              PROT_READ | PROT_WRITE,
                              MAP_SHARED,
                              ring->cold.ring_fd,
                              IORING_OFF_SQES);
    if (ring->cold.sqe_mem == MAP_FAILED) {
        int error          = -errno;
        ring->cold.sqe_mem = nullptr;
        uring_exit(ring);
        return error;
    }

    int advice = advise_mapping(ring->cold.ring_mem, ring->cold.ring_size);
    if (advice == 0)
        advice = advise_mapping(ring->cold.sqe_mem, ring->cold.sqe_size);
    if (advice < 0) {
        uring_exit(ring);
        return advice;
    }

    char* base              = ring->cold.ring_mem;
    ring->hot.sqes          = ring->cold.sqe_mem;
    ring->hot.sq_khead      = (_Atomic uint32_t*)(void*)(base + params.sq_off.head);
    ring->hot.sq_ktail      = (_Atomic uint32_t*)(void*)(base + params.sq_off.tail);
    ring->hot.sq_kflags     = (_Atomic uint32_t*)(void*)(base + params.sq_off.flags);
    ring->cold.sq_kdropped  = (_Atomic uint32_t*)(void*)(base + params.sq_off.dropped);
    ring->hot.cq_khead      = (_Atomic uint32_t*)(void*)(base + params.cq_off.head);
    ring->hot.cq_ktail      = (_Atomic uint32_t*)(void*)(base + params.cq_off.tail);
    ring->cold.cq_koverflow = (_Atomic uint32_t*)(void*)(base + params.cq_off.overflow);
    ring->hot.cqes          = (struct io_uring_cqe*)(void*)(base + params.cq_off.cqes);

    ring->hot.sq_mask          = *(uint32_t*)(void*)(base + params.sq_off.ring_mask);
    ring->hot.sq_entries       = *(uint32_t*)(void*)(base + params.sq_off.ring_entries);
    ring->hot.cq_mask          = *(uint32_t*)(void*)(base + params.cq_off.ring_mask);
    ring->hot.cq_entries       = *(uint32_t*)(void*)(base + params.cq_off.ring_entries);
    ring->hot.sq_head          = atomic_load_explicit(ring->hot.sq_khead, memory_order_acquire);
    ring->hot.sq_tail          = atomic_load_explicit(ring->hot.sq_ktail, memory_order_acquire);
    ring->hot.cq_head          = atomic_load_explicit(ring->hot.cq_khead, memory_order_acquire);
    ring->hot.cq_tail          = ring->hot.cq_head;
    ring->cold.last_sq_dropped = atomic_load_explicit(ring->cold.sq_kdropped, memory_order_relaxed);
    ring->cold.last_cq_overflow
        = atomic_load_explicit(ring->cold.cq_koverflow, memory_order_relaxed);

    int probe = require_opcodes(ring->cold.ring_fd);
    if (probe < 0) {
        uring_exit(ring);
        return probe;
    }
    int registration = register_ring(ring);
    if (registration < 0) {
        uring_exit(ring);
        return registration;
    }
    int files = register_sparse_files(ring, fixed_files);
    if (files < 0) {
        uring_exit(ring);
        return files;
    }
    return 0;
}

void uring_exit(struct uring* ring)
{
    unregister_ring(ring);
    unregister_files(ring);
    if (ring->cold.sqe_mem)
        munmap(ring->cold.sqe_mem, ring->cold.sqe_size);
    if (ring->cold.ring_mem)
        munmap(ring->cold.ring_mem, ring->cold.ring_size);
    if (ring->cold.ring_fd >= 0)
        close(ring->cold.ring_fd);
    *ring = (struct uring){.cold = {.ring_fd = -1}};
}

int uring_update_files(struct uring* ring, uint32_t offset, const int* files, uint32_t count)
{
    if (!ring->cold.files_registered || count == 0 || offset > ring->cold.file_count
        || count > ring->cold.file_count - offset)
        return -EINVAL;

    struct io_uring_rsrc_update update = {
        .offset = offset,
        .data   = (uint64_t)(uintptr_t)files,
    };
    long rc = raw_register(ring->hot.enter_fd,
                           IORING_REGISTER_FILES_UPDATE | IORING_REGISTER_USE_REGISTERED_RING,
                           &update,
                           count);
    if (rc != count)
        return rc < 0 ? (int)rc : -EIO;
    return 0;
}

long uring_enter(struct uring* ring, uint32_t min_complete)
{
    struct uring_hot* hot  = &ring->hot;
    uint32_t          tail = hot->sq_tail;

    atomic_store_explicit(hot->sq_ktail, tail, memory_order_release);
    uint32_t to_submit = tail - hot->sq_head;
    long     rc        = raw_enter(hot->enter_fd, to_submit, min_complete, hot->enter_flags);
    hot->sq_head       = atomic_load_explicit(hot->sq_khead, memory_order_acquire);
    return rc;
}

int uring_check_health(struct uring* ring)
{
    uint32_t sq_dropped  = atomic_load_explicit(ring->cold.sq_kdropped, memory_order_relaxed);
    uint32_t cq_overflow = atomic_load_explicit(ring->cold.cq_koverflow, memory_order_relaxed);
    uint32_t sq_flags    = atomic_load_explicit(ring->hot.sq_kflags, memory_order_relaxed);

    if (sq_dropped != ring->cold.last_sq_dropped) {
        ring->cold.last_sq_dropped = sq_dropped;
        return -ENOBUFS;
    }
    if (cq_overflow != ring->cold.last_cq_overflow) {
        ring->cold.last_cq_overflow = cq_overflow;
        return -EOVERFLOW;
    }
    if ((sq_flags & IORING_SQ_CQ_OVERFLOW) != 0)
        return -EAGAIN;
    return 0;
}

int uring_notify(int ring_fd, uint64_t user_data)
{
    struct io_uring_sqe message = {
        .opcode    = IORING_OP_MSG_RING,
        .fd        = ring_fd,
        .off       = user_data,
        .addr      = IORING_MSG_DATA,
        .len       = 0,
        .user_data = 0,
    };

    for (unsigned attempt = 0;; attempt++) {
        long rc = raw_register(-1, IORING_REGISTER_SEND_MSG_RING, &message, 1);
        if (rc >= 0)
            return 0;
        if (rc == -EINTR)
            continue;
        if ((rc == -EAGAIN || rc == -ENOMEM) && attempt < 64) {
            sched_yield();
            continue;
        }
        return (int)rc;
    }
}
