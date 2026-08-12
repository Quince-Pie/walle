#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "uring.h"

#define CHECK(condition) check((condition), #condition, __LINE__)

static void check(bool condition, const char* expression, int line)
{
    if (condition)
        return;
    fprintf(stderr, "uring smoke failure at line %d: %s\n", line, expression);
    exit(EXIT_FAILURE);
}

static const struct io_uring_cqe* wait_one(struct uring* ring)
{
    for (;;) {
        long rc = uring_enter(ring, 1);
        CHECK(rc >= 0);
        if (uring_cq_begin(&ring->hot) != 0)
            return uring_cqe_at(&ring->hot, 0);
    }
}

struct notify_context
{
    struct uring* ring;
    int           result;
};

static void* notify_worker(void* argument)
{
    struct notify_context* context = argument;
    context->result                = uring_notify(uring_real_fd(context->ring), 0x3333);
    return nullptr;
}

int main(void)
{
    struct uring ring;
    int          rc = uring_init(&ring, 256, 1);
    if (rc < 0) {
        fprintf(stderr, "uring_init: %s\n", strerror(-rc));
        return 1;
    }
    CHECK(ring.hot.sq_entries == 256);
    CHECK(ring.hot.cq_entries == 512);

    int pipefd[2];
    CHECK(pipe2(pipefd, O_CLOEXEC | O_NONBLOCK) == 0);
    CHECK(uring_update_files(&ring, 0, &pipefd[0], 1) == 0);
    CHECK(write(pipefd[1], "raw", 3) == 3);

    char                 buffer[4] = {};
    struct io_uring_sqe* sqe       = uring_get_sqe(&ring.hot);
    CHECK(sqe);
    sqe->opcode    = IORING_OP_READ;
    sqe->flags     = IOSQE_FIXED_FILE;
    sqe->fd        = 0;
    sqe->addr      = (uint64_t)(uintptr_t)buffer;
    sqe->len       = 3;
    sqe->user_data = 0x1111;

    const struct io_uring_cqe* cqe = wait_one(&ring);
    CHECK(cqe->user_data == 0x1111);
    CHECK(cqe->res == 3);
    CHECK(memcmp(buffer, "raw", 3) == 0);
    uring_cq_commit(&ring.hot, 1);

    struct timespec now;
    CHECK(clock_gettime(CLOCK_MONOTONIC, &now) == 0);
    struct __kernel_timespec timeout = {
        .tv_sec  = now.tv_sec,
        .tv_nsec = now.tv_nsec + 1'000'000,
    };
    if (timeout.tv_nsec >= 1'000'000'000) {
        timeout.tv_sec++;
        timeout.tv_nsec -= 1'000'000'000;
    }

    sqe = uring_get_sqe(&ring.hot);
    CHECK(sqe);
    sqe->opcode        = IORING_OP_TIMEOUT;
    sqe->fd            = -1;
    sqe->addr          = (uint64_t)(uintptr_t)&timeout;
    sqe->len           = 1;
    sqe->timeout_flags = IORING_TIMEOUT_ABS | IORING_TIMEOUT_ETIME_SUCCESS;
    sqe->user_data     = 0x2222;

    cqe = wait_one(&ring);
    CHECK(cqe->user_data == 0x2222);
    CHECK(cqe->res == -ETIME);
    uring_cq_commit(&ring.hot, 1);

    struct notify_context context = {.ring = &ring};
    pthread_t             worker;
    CHECK(pthread_create(&worker, nullptr, notify_worker, &context) == 0);
    CHECK(pthread_join(worker, nullptr) == 0);
    CHECK(context.result == 0);
    cqe = wait_one(&ring);
    CHECK(cqe->user_data == 0x3333);
    CHECK(cqe->res == 0);
    uring_cq_commit(&ring.hot, 1);

    CHECK(uring_check_health(&ring) == 0);
    close(pipefd[0]);
    close(pipefd[1]);
    uring_exit(&ring);
    return 0;
}
