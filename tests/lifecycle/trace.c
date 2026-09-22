/* Test-only observation of existing app threads/timers. No renderer is linked. */
#define main lifecycle_unused_app_main
#include "../../walle.c"
#undef main
#include <dlfcn.h>
#include <link.h>

static int trace_fd = -1;
static uintptr_t executable_base, worker_offset;
static int (*next_create)(pthread_t *, const pthread_attr_t *, void *(*)(void *), void *);
static int (*next_join)(pthread_t, void **);
static ssize_t (*next_read)(int, void *, size_t);
static int (*next_timer_create)(int, int);
static int (*next_close)(int);
static _Atomic bool timer_fds[4096];
static pthread_mutex_t trace_mutex = PTHREAD_MUTEX_INITIALIZER;
static struct {pthread_t thread; struct wallpaper_output *output; bool used;} workers[16];

static uint64_t stamp(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
}
static void emit(const char *text)
{
    if (trace_fd >= 0) (void)syscall(SYS_write, trace_fd, text, strlen(text));
}
static int find_executable(struct dl_phdr_info *info, size_t size, void *data)
{
    (void)size; (void)data;
    if (!info->dlpi_name || !*info->dlpi_name) { executable_base = info->dlpi_addr; return 1; }
    return 0;
}
static void output_event(const char *event, const struct wallpaper_output *o, bool success)
{
    char line[768];
    snprintf(line, sizeof line,
        "{\"event\":\"%s\",\"ns\":%llu,\"output\":%llu,\"index\":%zu,\"items\":%zu,"
        "\"width\":%d,\"height\":%d,\"scale\":%d,\"state\":%u,\"flags\":%u,"
        "\"variant\":%u,\"motion\":%u,\"appearance\":%u,\"tinted\":%s,"
        "\"rgba\":[%u,%u,%u,%u],\"duration\":%.9g,\"success\":%s}\n",
        event, (unsigned long long)stamp(), (unsigned long long)(uintptr_t)o,
        o->current_item_index, o->num_items, o->job_w, o->job_h, o->configured_scale,
        (unsigned)o->render.t_state, (unsigned)o->render.flags, (unsigned)o->glass_variant,
        (unsigned)o->glass_motion, (unsigned)o->glass_appearance,
        o->glass_tint.present ? "true" : "false", o->glass_tint.srgb[0], o->glass_tint.srgb[1],
        o->glass_tint.srgb[2], o->glass_tint.srgb[3], (double)o->transition_duration,
        success ? "true" : "false");
    emit(line);
}
[[gnu::constructor]] static void setup_trace(void)
{
    void *symbol = dlsym(RTLD_NEXT, "pthread_create"); memcpy(&next_create, &symbol, sizeof symbol);
    symbol = dlsym(RTLD_NEXT, "pthread_join"); memcpy(&next_join, &symbol, sizeof symbol);
    symbol = dlsym(RTLD_NEXT, "read"); memcpy(&next_read, &symbol, sizeof symbol);
    symbol = dlsym(RTLD_NEXT, "timerfd_create"); memcpy(&next_timer_create, &symbol, sizeof symbol);
    symbol = dlsym(RTLD_NEXT, "close"); memcpy(&next_close, &symbol, sizeof symbol);
    const char *path = getenv("WALLE_LIFECYCLE_TRACE"), *offset = getenv("WALLE_WORKER_OFFSET");
    if (path && offset) {
        worker_offset = (uintptr_t)strtoull(offset, nullptr, 16);
        trace_fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
        dl_iterate_phdr(find_executable, nullptr);
        setvbuf(stdout, nullptr, _IOLBF, 0);
        char line[160];
        snprintf(line, sizeof line, "{\"event\":\"trace_ready\",\"ns\":%llu,\"pid\":%d}\n",
                 (unsigned long long)stamp(), getpid());
        emit(line);
    }
}
[[gnu::visibility("default")]] int pthread_create(pthread_t *thread, const pthread_attr_t *attr,
                                                  void *(*start)(void *), void *arg)
{
    if (!next_create) { void *symbol=dlsym(RTLD_NEXT,"pthread_create"); memcpy(&next_create,&symbol,sizeof symbol); }
    if (!next_create) return ENOSYS;
    bool ours = trace_fd >= 0 && (uintptr_t)start == executable_base + worker_offset;
    if (ours) output_event("decode_start", arg, true);
    int result = next_create(thread, attr, start, arg);
    if (ours && result == 0) {
        pthread_mutex_lock(&trace_mutex);
        for (unsigned i = 0; i < 16; ++i) if (!workers[i].used) {
            workers[i].thread = *thread; workers[i].output = arg; workers[i].used = true; break;
        }
        pthread_mutex_unlock(&trace_mutex);
    }
    return result;
}
[[gnu::visibility("default")]] int pthread_join(pthread_t thread, void **result)
{
    if (!next_join) { void *symbol=dlsym(RTLD_NEXT,"pthread_join"); memcpy(&next_join,&symbol,sizeof symbol); }
    if (!next_join) return ENOSYS;
    struct wallpaper_output *output = nullptr;
    pthread_mutex_lock(&trace_mutex);
    for (unsigned i = 0; i < 16; ++i) if (workers[i].used && pthread_equal(workers[i].thread, thread)) {
        output = workers[i].output; workers[i].used = false; break;
    }
    pthread_mutex_unlock(&trace_mutex);
    int status = next_join(thread, result);
    if (output && status == 0) output_event("decode_join", output, output->async_result.success);
    return status;
}
[[gnu::visibility("default")]] int timerfd_create(int clock, int flags)
{
    int fd = next_timer_create ? next_timer_create(clock, flags) : (int)syscall(SYS_timerfd_create, clock, flags);
    if (fd >= 0 && fd < 4096) atomic_store(&timer_fds[fd],true);
    return fd;
}
[[gnu::visibility("default")]] ssize_t read(int fd, void *data, size_t size)
{
    ssize_t result = next_read ? next_read(fd, data, size) : syscall(SYS_read, fd, data, size);
    if (result == 8 && fd >= 0 && fd < 4096 && atomic_load(&timer_fds[fd])) {
        uint64_t ticks; memcpy(&ticks, data, sizeof ticks);
        char line[192];
        snprintf(line, sizeof line, "{\"event\":\"timer_read\",\"ns\":%llu,\"fd\":%d,\"expirations\":%llu}\n",
                 (unsigned long long)stamp(), fd, (unsigned long long)ticks);
        emit(line);
    }
    return result;
}
[[gnu::visibility("default")]] int close(int fd)
{
    if(fd>=0&&fd<4096)atomic_store(&timer_fds[fd],false);
    return next_close?next_close(fd):(int)syscall(SYS_close,fd);
}
