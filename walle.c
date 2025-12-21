#define _GNU_SOURCE

#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <signal.h>
#include <stdckdint.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#if defined(__has_include) && __has_include(<endian.h>)
#    include <endian.h>
#endif

#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <dirent.h>
#include <fcntl.h>
#include <ini.h>
#include <poll.h>
#include <pthread.h>
#include <pwd.h>
#include <sched.h>
#include <strings.h>
#include <sys/eventfd.h>
#include <sys/inotify.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/timerfd.h>
#include <sys/types.h>
#include <systemd/sd-bus.h>
#include <unistd.h>
#include <vips/vips.h>
#include <wayland-client.h>
#include <wayland-egl.h>
#include <xxhash.h>

#include "protocols/wlr-layer-shell-unstable-v1.h"
#include "shiro.h"

#if defined(NDEBUG)
#    define dbg_print(fmt, ...) ((void)0)
#else
#    define dbg_print(fmt, ...)                                                                    \
        fprintf(stderr,                                                                            \
                "%s %s:%d"                                                                         \
                "%s"                                                                               \
                "%s(): " fmt "\n",                                                                 \
                "[DBG]",                                                                           \
                __FILE__,                                                                          \
                __LINE__,                                                                          \
                " | ",                                                                             \
                __func__ __VA_OPT__(, ) __VA_ARGS__)
#endif

/* -- Constants ----------------------------------------------------------- */

constexpr double GLASS_SATURATION_BOOST = 2.1;
constexpr double GLASS_TINT_OPACITY     = 0.25;
constexpr double GLASS_BLUR_SIGMA       = 42.0;
constexpr int    GLASS_DOWN_FACTOR      = 8;

constexpr float DEFAULT_TRANSITION_DUR = 0.6f;
constexpr int   MAX_POLL_FDS           = 128;
constexpr int   INOTIFY_BUF_LEN        = 4096;

constexpr size_t CACHE_HIGH_WATERMARK    = 512UL * 1024UL * 1024UL;
constexpr size_t CACHE_LOW_WATERMARK     = 384UL * 1024UL * 1024UL;
constexpr int    CACHE_STARTUP_YIELD_SEC = 10;

#ifndef MFD_CLOEXEC
#    define MFD_CLOEXEC 0x0001U
#endif
#ifndef SCHED_IDLE
#    define SCHED_IDLE 5
#endif

static volatile sig_atomic_t g_running = 1;
static XoshiroState          g_rng     = {};

const char VERTEX_SHADER_SRC[] = {
#embed "shaders/vert.glsl" limit(4096) if_empty(0) suffix(, )
    0};

const char FRAGMENT_SHADER_T1_SRC[] = {
#embed "shaders/frag.glsl" limit(16384) if_empty(0) suffix(, )
    0};

/* -- Data Structures ----------------------------------------------------- */

enum wallpaper_mode : uint8_t
{
    MODE_FILL,
    MODE_FIT,
    MODE_STRETCH
};

enum transition_state : uint8_t
{
    T_STATE_IDLE,
    T_STATE_RUNNING
};

typedef enum : uint8_t
{
    F_CONFIGURED    = 1 << 0,
    F_TEX_INIT      = 1 << 1,
    F_BOOT_COMPLETE = 1 << 2,
    F_THREAD_ACTIVE = 1 << 3,
    F_RANDOMIZE     = 1 << 4,
    F_TRANSITION_ON = 1 << 5,
    F_DEAD          = 1 << 6,
    F_INITIALIZED   = 1 << 7
} output_flags_t;

struct wallpaper_item
{
    char*               filename;
    enum wallpaper_mode mode;
    VipsInteresting     crop_strategy;
};

struct item_list
{
    struct wallpaper_item* items;
    size_t                 count;
    size_t                 capacity;
};

struct output_config
{
    struct wl_list   link;
    char*            output_name;
    struct item_list items;
    int              timeout;
    bool             randomize;
    bool             transition_on;
    bool             gamemode;
    float            transition_duration;
};

struct config_parse_ctx
{
    struct wl_list* config_list;
};

struct image_data_buffer
{
    int     fd;
    size_t  buffer_size;
    int32_t width, height, stride;
};

struct render_result
{
    struct image_data_buffer standard;
    struct image_data_buffer glass;
    GLenum                   pixel_format;
    bool                     success;
};

struct wallpaper_state;

struct wallpaper_output
{
    alignas(64) struct
    {
        struct wallpaper_state* state;
        EGLSurface              egl_surface;
        uint64_t                anim_start_ns;

        GLuint vao;
        GLuint tex_A;
        GLuint tex_B;
        GLuint tex_GlassA;
        GLuint tex_GlassB;
        GLuint pbo;

        int32_t width;
        int32_t height;
        float   duration_inv; /* 1.0/duration, NOT duration. MUL is faster than DIV per-frame. */

        enum transition_state t_state;
        output_flags_t        flags;
        uint8_t               _pad[2];
    } render;

    float     t_center_x;
    float     t_center_y;
    float     t_max_radius;
    float     transition_duration;
    int       event_fd;
    int       timer_fd;
    pthread_t render_thread;

    struct render_result async_result;
    struct wl_callback*  frame_callback;

    struct wl_list                link;
    struct wl_output*             wl_output;
    uint32_t                      wl_output_name;
    char*                         name;
    struct wl_surface*            surface;
    struct zwlr_layer_surface_v1* layer_surface;
    struct wl_egl_window*         egl_window;
    GLuint                        vbo;

    struct wallpaper_item* items;
    size_t                 num_items;
    size_t                 current_item_index;
    int                    timeout;
    bool                   gamemode_enabled;

    bool pending_reload;
};

static_assert(sizeof(((struct wallpaper_output*)0)->render) == 64,
              "Render struct must be exactly 64 bytes (1 cache line)");
static_assert(offsetof(struct wallpaper_output, render) == 0,
              "Render struct must be at offset 0 for cache alignment");

struct wallpaper_state
{
    struct wl_display*          display;
    struct wl_registry*         registry;
    struct wl_compositor*       compositor;
    struct zwlr_layer_shell_v1* layer_shell;
    struct wl_shm*              shm;

    struct wl_list outputs;
    struct wl_list output_configs;

    EGLDisplay egl_display;
    EGLConfig  egl_config;
    EGLContext egl_context;
    bool       egl_initialized;

    GLuint shader_program_t1;
    GLint  u_TexA, u_TexGlassA, u_TexB, u_TexGlassB;
    GLint  u_Time, u_CenterPointPixels, u_Resolution, u_MaxRadiusPixels;

    int   inotify_fd;
    int   config_wd;
    char* config_path;
    char* config_dir;
    char* config_filename;

    sd_bus*      bus;
    sd_bus_slot* gamemode_slot;
    bool         gamemode_active;
};

static void initialize_output(struct wallpaper_output* output);
static void apply_config_to_output(struct wallpaper_output* output, struct output_config* config);
static void update_wallpaper(struct wallpaper_output* output);
static void launch_async_render(struct wallpaper_output* output);
static struct output_config* get_config_for_output(struct wallpaper_state* state, const char* name);

/* -- Path Expansion ------------------------------------------------------ */

static float ease_in_out_cubic(float t) [[unsequenced]]
{
    t = fmaxf(0.0f, fminf(1.0f, t));

    if (t < 0.5f) {
        return 4.0f * t * t * t;
    } else {
        float f = -2.0f * t + 2.0f;
        return 1.0f - (f * f * f) * 0.5f;
    }
}

constexpr size_t INITIAL_BUFFER_CAPACITY = 256;
constexpr size_t NAME_SBO_SIZE           = 128;

typedef struct
{
    char*  data;
    size_t size;
    size_t capacity;
} DynamicBuffer;

static bool buffer_init(DynamicBuffer* buf)
{
    *buf      = (DynamicBuffer){};
    buf->data = malloc(INITIAL_BUFFER_CAPACITY);
    if (buf->data == nullptr)
        return false;
    buf->data[0]  = '\0';
    buf->capacity = INITIAL_BUFFER_CAPACITY;
    return true;
}

static void buffer_free(DynamicBuffer* buf)
{
    free(buf->data);
    *buf = (DynamicBuffer){};
}

[[nodiscard]]
static bool buffer_append(DynamicBuffer* buf, const char* src, size_t len)
{
    if (len == 0)
        return true;

    size_t required;
    if (ckd_add(&required, buf->size, len) || ckd_add(&required, required, 1)) {
        errno = EOVERFLOW;
        return false;
    }

    if (required > buf->capacity) {
        size_t new_cap = buf->capacity;
        while (new_cap < required) {
            if (ckd_mul(&new_cap, new_cap, 2)) {
                new_cap = SIZE_MAX;
                if (new_cap < required) {
                    errno = EOVERFLOW;
                    return false;
                }
                break;
            }
        }
        auto new_data = realloc(buf->data, new_cap);
        if (!new_data)
            return false;
        buf->data     = new_data;
        buf->capacity = new_cap;
    }

    memcpy(buf->data + buf->size, src, len);
    buf->size += len;
    buf->data[buf->size] = '\0';
    return true;
}

[[nodiscard]]
static int get_passwd_buffered(uid_t uid, const char* name, struct passwd* pwd, char** buffer)
{
    long   suggested = sysconf(_SC_GETPW_R_SIZE_MAX);
    size_t cur_sz = (suggested > 0 && (size_t)suggested < SIZE_MAX / 2) ? (size_t)suggested : 1024;
    char*  buf    = nullptr;
    int    status = 0;

    do {
        auto new_buf = realloc(buf, cur_sz);
        if (!new_buf) {
            free(buf);
            *buffer = nullptr;
            return ENOMEM;
        }
        buf = new_buf;

        struct passwd* result = nullptr;
        status                = name ? getpwnam_r(name, pwd, buf, cur_sz, &result)
                                     : getpwuid_r(uid, pwd, buf, cur_sz, &result);

        if (result == nullptr && status == 0)
            status = ENOENT;
        if (status == ERANGE) {
            if (ckd_mul(&cur_sz, cur_sz, 2)) {
                status = EOVERFLOW;
                break;
            }
        }
    } while (status == ERANGE);

    if (status == 0) {
        *buffer = buf;
    } else {
        free(buf);
        *buffer = nullptr;
    }
    return status;
}

static inline bool is_valid_var_char(char c) [[unsequenced]]
{
    return isalnum((unsigned char)c) || c == '_';
}

static bool handle_tilde_expansion(DynamicBuffer* buf, const char** cursor)
{
    const char* start = *cursor;
    const char* p     = start + 1;
    while (*p != '/' && *p != '\0')
        p++;

    size_t        uname_len = (size_t)(p - (start + 1));
    const char*   home      = nullptr;
    char*         pw_buf    = nullptr;
    struct passwd pwd;

    if (uname_len == 0) {
        home = getenv("HOME");
        if (!home || !*home) {
            if (get_passwd_buffered(getuid(), nullptr, &pwd, &pw_buf) == 0)
                home = pwd.pw_dir;
        }
    } else {
        char  sbo[NAME_SBO_SIZE];
        char* dyn   = nullptr;
        char* uname = sbo;

        if (uname_len + 1 > sizeof(sbo)) {
            dyn = malloc(uname_len + 1);
            if (!dyn)
                return false;
            uname = dyn;
        }
        memcpy(uname, start + 1, uname_len);
        uname[uname_len] = '\0';

        if (get_passwd_buffered(0, uname, &pwd, &pw_buf) == 0)
            home = pwd.pw_dir;
        free(dyn);
    }

    bool ok = true;
    if (home && *home) {
        if (!buffer_append(buf, home, strlen(home)))
            ok = false;
        *cursor = p;
    } else {
        if (!buffer_append(buf, start, (size_t)(p - start)))
            ok = false;
        *cursor = p;
    }
    free(pw_buf);
    return ok;
}

static bool handle_variable_expansion(DynamicBuffer* buf, const char** cursor)
{
    const char* start = *cursor - 1;
    const char* p     = *cursor;
    const char* var_start;
    size_t      var_len;
    bool        braced = false;

    if (*p == '{') {
        braced = true;
        p++;
        var_start = p;
        while (*p != '}' && *p != '\0')
            p++;
        var_len = (size_t)(p - var_start);
        if (*p != '}') {
            if (!buffer_append(buf, start, (size_t)(p - start)))
                return false;
            *cursor = p;
            return true;
        }
    } else {
        var_start = p;
        while (is_valid_var_char(*p))
            p++;
        var_len = (size_t)(p - var_start);
        if (var_len == 0) {
            if (!buffer_append(buf, "$", 1))
                return false;
            *cursor = p;
            return true;
        }
    }

    char  sbo[NAME_SBO_SIZE];
    char* dyn   = nullptr;
    char* vname = sbo;

    if (var_len + 1 > sizeof(sbo)) {
        dyn = malloc(var_len + 1);
        if (!dyn)
            return false;
        vname = dyn;
    }
    memcpy(vname, var_start, var_len);
    vname[var_len] = '\0';

    const char* val = getenv(vname);
    if (val && !buffer_append(buf, val, strlen(val))) {
        free(dyn);
        return false;
    }
    free(dyn);

    *cursor = p + (braced ? 1 : 0);
    return true;
}

[[nodiscard]]
char* expand_path(const char* input)
{
    if (!input)
        return nullptr;

    DynamicBuffer buf;
    if (!buffer_init(&buf))
        return nullptr;

    const char* cursor = input;
    if (*cursor == '~') {
        if (!handle_tilde_expansion(&buf, &cursor)) {
            buffer_free(&buf);
            return nullptr;
        }
    }

    while (*cursor) {
        const char* start = cursor;
        while (*cursor != '$' && *cursor)
            cursor++;

        if (cursor > start) {
            if (!buffer_append(&buf, start, (size_t)(cursor - start))) {
                buffer_free(&buf);
                return nullptr;
            }
        }
        if (*cursor == '$') {
            cursor++;
            if (!handle_variable_expansion(&buf, &cursor)) {
                buffer_free(&buf);
                return nullptr;
            }
        }
    }

    if (buf.capacity > buf.size + 1) {
        auto shrunk = realloc(buf.data, buf.size + 1);
        if (shrunk)
            buf.data = shrunk;
    }
    return buf.data;
}

/* -- Cache Maintenance --------------------------------------------------- */

struct cache_maintenance_entry
{
    char*  path;
    time_t atime;
    off_t  size;
};

static int compare_cache_entries(const void* a, const void* b)
{
    auto ea = (const struct cache_maintenance_entry*)a;
    auto eb = (const struct cache_maintenance_entry*)b;
    return (ea->atime < eb->atime) ? -1 : (ea->atime > eb->atime);
}

[[nodiscard]]
static char* resolve_cache_dir(void)
{
    const char* xdg = getenv("XDG_CACHE_HOME");
    char        dir[PATH_MAX];

    if (xdg) {
        snprintf(dir, sizeof(dir), "%s/walle", xdg);
    } else {
        snprintf(dir, sizeof(dir), "%s/.cache/walle", getenv("HOME"));
    }
    struct stat st;
    if (stat(dir, &st) == -1)
        mkdir(dir, 0700);
    return strdup(dir);
}

static void* cache_maintenance_worker(void* arg)
{
    struct timespec ts = {.tv_sec = CACHE_STARTUP_YIELD_SEC, .tv_nsec = 0};
    nanosleep(&ts, nullptr);

    char* cache_dir = (char*)arg;
    pthread_setname_np(pthread_self(), "walle-gc");

    struct sched_param sp = {0};
    if (sched_setscheduler(0, SCHED_IDLE, &sp) == -1) {
        setpriority(PRIO_PROCESS, 0, 19);
    }

    DIR* d = opendir(cache_dir);
    if (!d) {
        free(cache_dir);
        return nullptr;
    }

    struct cache_maintenance_entry* entries    = nullptr;
    size_t                          count      = 0;
    size_t                          capacity   = 0;
    size_t                          total_size = 0;

    struct dirent* de;
    while ((de = readdir(d)) != nullptr) {
        if (de->d_name[0] == '.')
            continue;
        char* ext = strrchr(de->d_name, '.');
        if (!ext || strcmp(ext, ".bin") != 0)
            continue;

        char full_path[PATH_MAX];
        snprintf(full_path, sizeof(full_path), "%s/%s", cache_dir, de->d_name);

        struct stat st;
        if (stat(full_path, &st) == 0 && S_ISREG(st.st_mode)) {
            if (count >= capacity) {
                size_t new_cap = (capacity == 0) ? 128 : capacity * 2;
                size_t alloc_sz;
                if (ckd_mul(&alloc_sz, new_cap, sizeof(struct cache_maintenance_entry)))
                    break;

                auto new_ptr = realloc(entries, alloc_sz);
                if (!new_ptr)
                    break;
                entries  = new_ptr;
                capacity = new_cap;
            }

            entries[count].path = strdup(full_path);
            if (entries[count].path) {
                entries[count].atime = st.st_atime;
                entries[count].size  = st.st_size;

                size_t next_total;
                if (!ckd_add(&next_total, total_size, (size_t)st.st_size)) {
                    total_size = next_total;
                } else {
                    total_size = SIZE_MAX;
                }
                count++;
            }
        }
    }
    closedir(d);

    if (total_size > CACHE_HIGH_WATERMARK && count > 0) {
        qsort(entries, count, sizeof(struct cache_maintenance_entry), compare_cache_entries);
        for (size_t i = 0; i < count; i++) {
            if (total_size <= CACHE_LOW_WATERMARK)
                break;
            if (unlink(entries[i].path) == 0) {
                size_t next_val;
                if (!ckd_sub(&next_val, total_size, (size_t)entries[i].size)) {
                    total_size = next_val;
                }
            }
        }
    }

    for (size_t i = 0; i < count; i++)
        free(entries[i].path);
    free(entries);
    free(cache_dir);
    return nullptr;
}

static void launch_cache_maintenance_service(void)
{
    char* dir = resolve_cache_dir();
    if (!dir)
        return;
    pthread_t th;
    if (pthread_create(&th, nullptr, cache_maintenance_worker, dir) == 0) {
        pthread_detach(th);
    } else {
        free(dir);
    }
}

/* -- GameMode D-Bus ------------------------------------------------------ */

constexpr char GAMEMODE_BUS_NAME[]    = "org.freedesktop.portal.Desktop";
constexpr char GAMEMODE_PATH[]        = "/org/freedesktop/portal/desktop";
constexpr char GAMEMODE_INTERFACE[]   = "org.freedesktop.portal.GameMode";
constexpr char GAMEMODE_PROPERTY[]    = "Active";
constexpr char DBUS_PROPS_INTERFACE[] = "org.freedesktop.DBus.Properties";

static void toggle_gamemode_timers(struct wallpaper_state* state, bool active)
{
    struct wallpaper_output* o;
    wl_list_for_each(o, &state->outputs, link)
    {
        if ((o->render.flags & F_DEAD) || o->timer_fd < 0 || !o->gamemode_enabled)
            continue;

        struct itimerspec ts = {};

        if (!active && o->timeout > 0) {
            ts.it_interval.tv_sec = o->timeout;
            ts.it_value.tv_sec    = o->timeout;
        }

        if (timerfd_settime(o->timer_fd, 0, &ts, nullptr) < 0) {
            fprintf(
                stderr, "[ERROR] Failed to toggle timer for %s: %s\n", o->name, strerror(errno));
        } else {
            dbg_print(
                "[GAMEMODE] Output '%s': %s", o->name, active ? "DISARMED (Zero-Wakeup)" : "ARMED");
        }
    }
}

static int gamemode_property_changed(sd_bus_message*                m,
                                     void*                          userdata,
                                     [[maybe_unused]] sd_bus_error* ret_error)
{
    auto state = (struct wallpaper_state*)userdata;

    if (sd_bus_message_skip(m, "s") < 0)
        return 0;

    if (sd_bus_message_enter_container(m, 'a', "{sv}") < 0)
        return 0;

    while (sd_bus_message_enter_container(m, 'e', "sv") > 0) {
        const char* prop;
        if (sd_bus_message_read(m, "s", &prop) < 0)
            break;

        if (strcmp(prop, GAMEMODE_PROPERTY) == 0) {
            if (sd_bus_message_enter_container(m, 'v', "b") >= 0) {
                int active_int;
                if (sd_bus_message_read(m, "b", &active_int) >= 0) {
                    bool new_state = (active_int != 0);
                    if (state->gamemode_active != new_state) {
                        state->gamemode_active = new_state;
                        printf("[GAMEMODE] State Change: %s\n", new_state ? "ACTIVE" : "INACTIVE");

                        toggle_gamemode_timers(state, new_state);
                    }
                }
                sd_bus_message_exit_container(m);
            }
        } else {
            sd_bus_message_skip(m, "v");
        }
        sd_bus_message_exit_container(m);
    }
    sd_bus_message_exit_container(m);
    return 0;
}

static int on_gamemode_initial_state(sd_bus_message* m, void* userdata, sd_bus_error* ret_error)
{
    (void)ret_error;
    auto state = (struct wallpaper_state*)userdata;
    if (!m || sd_bus_message_is_method_error(m, nullptr))
        return 0;

    int active = 0;
    if (sd_bus_message_enter_container(m, 'v', "b") >= 0) {
        sd_bus_message_read(m, "b", &active);
        sd_bus_message_exit_container(m);
    }

    bool new_state = (active != 0);
    if (state->gamemode_active != new_state) {
        state->gamemode_active = new_state;
        printf("[GAMEMODE] Startup State: %s\n", new_state ? "ACTIVE" : "INACTIVE");
        toggle_gamemode_timers(state, new_state);
    }
    return 0;
}

[[nodiscard]]
static bool gamemode_init(struct wallpaper_state* state)
{
    if (sd_bus_open_user(&state->bus) < 0) {
        fprintf(stderr, "[GAMEMODE] Failed to connect to session bus.\n");
        return false;
    }

    char match_rule[512];
    snprintf(match_rule,
             sizeof(match_rule),
             "type='signal',"
             "sender='%s',"
             "path='%s',"
             "interface='%s',"
             "member='PropertiesChanged',"
             "arg0='%s'", /* Kernel-side filter. Removes this and you get wakeups for ALL portal
                             properties. */
             GAMEMODE_BUS_NAME,
             GAMEMODE_PATH,
             DBUS_PROPS_INTERFACE,
             GAMEMODE_INTERFACE);

    int r = sd_bus_add_match(
        state->bus, &state->gamemode_slot, match_rule, gamemode_property_changed, state);
    if (r < 0)
        return false;

    r = sd_bus_call_method_async(state->bus,
                                 nullptr,
                                 GAMEMODE_BUS_NAME,
                                 GAMEMODE_PATH,
                                 DBUS_PROPS_INTERFACE,
                                 "Get",
                                 on_gamemode_initial_state,
                                 state,
                                 "ss",
                                 GAMEMODE_INTERFACE,
                                 GAMEMODE_PROPERTY);

    return true;
}

static void gamemode_cleanup(struct wallpaper_state* state)
{
    if (state->gamemode_slot) {
        sd_bus_slot_unref(state->gamemode_slot);
        state->gamemode_slot = nullptr;
    }
    if (state->bus) {
        sd_bus_unref(state->bus);
        state->bus = nullptr;
    }
}

/* -- OpenGL -------------------------------------------------------------- */

static void init_gl_resources(struct wallpaper_state* state)
{
    GLuint      vs    = glCreateShader(GL_VERTEX_SHADER);
    const char* v_src = VERTEX_SHADER_SRC;
    glShaderSource(vs, 1, &v_src, nullptr);
    glCompileShader(vs);

    GLuint      fs    = glCreateShader(GL_FRAGMENT_SHADER);
    const char* f_src = FRAGMENT_SHADER_T1_SRC;
    glShaderSource(fs, 1, &f_src, nullptr);
    glCompileShader(fs);

    state->shader_program_t1 = glCreateProgram();
    glAttachShader(state->shader_program_t1, vs);
    glAttachShader(state->shader_program_t1, fs);
    glLinkProgram(state->shader_program_t1);

    glDeleteShader(vs);
    glDeleteShader(fs);

    glUseProgram(state->shader_program_t1);
    state->u_TexA      = glGetUniformLocation(state->shader_program_t1, "TexA");
    state->u_TexGlassA = glGetUniformLocation(state->shader_program_t1, "TexGlassA");
    state->u_TexB      = glGetUniformLocation(state->shader_program_t1, "TexB");
    state->u_TexGlassB = glGetUniformLocation(state->shader_program_t1, "TexGlassB");
    state->u_Time      = glGetUniformLocation(state->shader_program_t1, "Time");
    state->u_CenterPointPixels
        = glGetUniformLocation(state->shader_program_t1, "CenterPointPixels");
    state->u_Resolution      = glGetUniformLocation(state->shader_program_t1, "Resolution");
    state->u_MaxRadiusPixels = glGetUniformLocation(state->shader_program_t1, "MaxRadiusPixels");
    glUseProgram(0);
}

static void init_output_gl(struct wallpaper_output* output)
{
    if (!output->render.state->egl_initialized)
        return;

    eglMakeCurrent(output->render.state->egl_display,
                   output->render.egl_surface,
                   output->render.egl_surface,
                   output->render.state->egl_context);

    float vertices[] = {-1.0f,
                        -1.0f,
                        0.0f,
                        0.0f,
                        1.0f,
                        -1.0f,
                        1.0f,
                        0.0f,
                        -1.0f,
                        1.0f,
                        0.0f,
                        1.0f,
                        1.0f,
                        1.0f,
                        1.0f,
                        1.0f};

    glGenVertexArrays(1, &output->render.vao);
    glGenBuffers(1, &output->vbo);
    glGenBuffers(1, &output->render.pbo);

    glBindVertexArray(output->render.vao);
    glBindBuffer(GL_ARRAY_BUFFER, output->vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(vertices), vertices, GL_STATIC_DRAW);

    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void*)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void*)(2 * sizeof(float)));

    glGenTextures(1, &output->render.tex_A);
    glGenTextures(1, &output->render.tex_GlassA);
    glGenTextures(1, &output->render.tex_B);
    glGenTextures(1, &output->render.tex_GlassB);

    GLuint texs[] = {output->render.tex_A,
                     output->render.tex_GlassA,
                     output->render.tex_B,
                     output->render.tex_GlassB};
    for (int i = 0; i < 4; i++) {
        glBindTexture(GL_TEXTURE_2D, texs[i]);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    }

    output->render.flags |= F_TEX_INIT;
    eglMakeCurrent(
        output->render.state->egl_display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
}

[[nodiscard]]
static char* get_cache_filename(uint64_t hash)
{
    const char* xdg = getenv("XDG_CACHE_HOME");
    char        dir[PATH_MAX];
    if (xdg)
        snprintf(dir, sizeof(dir), "%s/walle", xdg);
    else
        snprintf(dir, sizeof(dir), "%s/.cache/walle", getenv("HOME"));
    mkdir(dir, 0700);

    char* path;
    if (asprintf(&path, "%s/%016w64x.bin", dir, hash) < 0)
        return nullptr;
    return path;
}

static inline void cleanup_vips_thread(void)
{
    vips_error_clear();
    vips_thread_shutdown();
}

static int write_pipeline_to_buffer_direct(VipsImage* in, void* dest, size_t len)
{
    size_t line_size = VIPS_IMAGE_SIZEOF_LINE(in);
    size_t total_size;
    if (ckd_mul(&total_size, line_size, (size_t)in->Ysize))
        return -1;
    if (total_size > len)
        return -1;

    VipsRegion* region = vips_region_new(in);
    if (!region)
        return -1;

    constexpr int  chunk_height = 128;
    unsigned char* dest_ptr     = (unsigned char*)dest;

    for (int y = 0; y < in->Ysize; y += chunk_height) {
        int h = (y + chunk_height > in->Ysize) ? (in->Ysize - y) : chunk_height;

        VipsRect rect = {.left = 0, .top = y, .width = in->Xsize, .height = h};
        if (vips_region_prepare(region, &rect)) {
            g_object_unref(region);
            return -1;
        }

        for (int row = 0; row < h; row++) {
            VipsPel* src = VIPS_REGION_ADDR(region, 0, y + row);
            memcpy(dest_ptr + ((y + row) * line_size), src, line_size);
        }
    }
    g_object_unref(region);
    return 0;
}

[[nodiscard]]
static VipsImage* apply_liquid_glass_effect_vips(VipsImage* input, double sigma)
{
    VipsImage *curr = input, *temp = nullptr;
    g_object_ref(curr);

    if (vips_gaussblur(curr, &temp, sigma, nullptr))
        goto err;
    g_object_unref(curr);
    curr = temp;

    if (vips_colourspace(curr, &temp, VIPS_INTERPRETATION_HSV, nullptr))
        goto err;
    g_object_unref(curr);
    curr = temp;

    double m[]   = {1.0, GLASS_SATURATION_BOOST, 1.0, 1.0};
    double o[]   = {0.0, 0.0, 0.0, 0.0};
    int    bands = vips_image_get_bands(curr);
    if (vips_linear(curr, &temp, m, o, bands, nullptr))
        goto err;
    g_object_unref(curr);
    curr = temp;

    if (vips_colourspace(curr, &temp, VIPS_INTERPRETATION_sRGB, nullptr))
        goto err;
    g_object_unref(curr);
    curr = temp;

    double mult = 1.0 - GLASS_TINT_OPACITY;
    double off  = 255.0 * GLASS_TINT_OPACITY;
    double mt[] = {mult, mult, mult, 1.0};
    double ot[] = {off, off, off, 0.0};

    if (vips_linear(curr, &temp, mt, ot, bands, nullptr))
        goto err;
    g_object_unref(curr);
    curr = temp;

    if (vips_cast(curr, &temp, VIPS_FORMAT_UCHAR, nullptr))
        goto err;
    g_object_unref(curr);
    curr = temp;

    return curr;
err:
    if (curr)
        g_object_unref(curr);
    return nullptr;
}

/* -- Render Thread ------------------------------------------------------- */

static void* render_thread_worker(void* arg)
{
    auto output = (struct wallpaper_output*)arg;
    char thread_name[16];
    snprintf(thread_name, sizeof(thread_name), "wrk-%s", output->name ? output->name : "anon");
    pthread_setname_np(pthread_self(), thread_name);

    struct render_result result    = {.success = false, .standard.fd = -1, .glass.fd = -1};
    auto                 item      = &output->items[output->current_item_index];
    int                  w         = output->render.width;
    int                  h         = output->render.height;
    long                 page_size = sysconf(_SC_PAGESIZE);

    VipsImage* header
        = vips_image_new_from_file(item->filename, "access", VIPS_ACCESS_SEQUENTIAL, nullptr);
    if (!header) {
        vips_error_clear();
        goto finalize;
    }
    int bands = vips_image_hasalpha(header) ? 4 : 3;

    int interlaced = 0;
    if (vips_image_get_int(header, "interlaced", &interlaced) == 0 && interlaced) {
        fprintf(stderr,
                "[MEMORY WARNING] '%s' is interlaced/progressive - may cause memory spike\n",
                item->filename);
    }
    g_object_unref(header);

    size_t raw_sz, glass_sz, total_sz;
    if (ckd_mul(&raw_sz, (size_t)w * h, bands))
        goto finalize;

    size_t aligned_raw_sz = (raw_sz + (page_size - 1)) & ~(page_size - 1);

    int gw = w / GLASS_DOWN_FACTOR;
    if (gw < 1)
        gw = 1;
    int gh = h / GLASS_DOWN_FACTOR;
    if (gh < 1)
        gh = 1;

    if (ckd_mul(&glass_sz, (size_t)gw * gh, bands))
        goto finalize;
    if (ckd_add(&total_sz, aligned_raw_sz, glass_sz))
        goto finalize;

    struct stat st;
    if (stat(item->filename, &st))
        goto finalize;
    XXH64_state_t* xxh = XXH64_createState();
    if (!xxh)
        goto finalize;
    XXH64_reset(xxh, 0);
    XXH64_update(xxh, item->filename, strlen(item->filename));
    XXH64_update(xxh, &st.st_mtime, sizeof(st.st_mtime));
    XXH64_update(xxh, &w, sizeof(w));
    XXH64_update(xxh, &h, sizeof(h));
    XXH64_update(xxh, &bands, sizeof(bands));
    XXH64_update(xxh, &GLASS_BLUR_SIGMA, sizeof(GLASS_BLUR_SIGMA));
    XXH64_update(xxh, &GLASS_DOWN_FACTOR, sizeof(GLASS_DOWN_FACTOR));
    XXH64_update(xxh, &item->mode, sizeof(item->mode));
    if (item->mode == MODE_FILL) {
        XXH64_update(xxh, &item->crop_strategy, sizeof(item->crop_strategy));
    }
    uint64_t hash = XXH64_digest(xxh);
    XXH64_freeState(xxh);

    char* cpath = get_cache_filename(hash);
    if (!cpath)
        goto finalize;

    int fd = open(cpath, O_RDONLY);
    if (fd >= 0) {
        struct stat cst;
        if (fstat(fd, &cst) == 0 && (size_t)cst.st_size == total_sz) {
            result.standard = (struct image_data_buffer){
                .fd = fd, .buffer_size = raw_sz, .width = w, .height = h, .stride = 0};
            result.glass = (struct image_data_buffer){
                .fd = dup(fd), .buffer_size = glass_sz, .width = gw, .height = gh, .stride = 0};
            result.pixel_format = (bands == 4) ? GL_RGBA : GL_RGB;
            result.success      = true;
            free(cpath);
            goto finalize;
        }
        close(fd);
    }

    fd = open(cpath, O_RDWR | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) {
        free(cpath);
        goto finalize;
    }

    if (posix_fallocate(fd, 0, total_sz) != 0) {
        if (ftruncate(fd, total_sz) < 0) {
            close(fd);
            free(cpath);
            goto finalize;
        }
    }

    uint8_t* map = mmap(nullptr, total_sz, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (map == MAP_FAILED) {
        close(fd);
        free(cpath);
        goto finalize;
    }

    madvise(map, total_sz, MADV_SEQUENTIAL);

    VipsImage *img = nullptr, *tmp = nullptr;

    switch (item->mode) {
        case MODE_FILL:
            if (vips_thumbnail(item->filename,
                               &img,
                               w,
                               "height",
                               h,
                               "crop",
                               item->crop_strategy,
                               "no_rotate",
                               TRUE,
                               nullptr)) {
                img = nullptr;
            }
            break;
        case MODE_FIT:
            if (vips_thumbnail(item->filename,
                               &img,
                               w,
                               "height",
                               h,
                               "size",
                               VIPS_SIZE_BOTH,
                               "no_rotate",
                               TRUE,
                               nullptr)) {
                img = nullptr;
                break;
            }
            if (vips_image_get_width(img) < w || vips_image_get_height(img) < h) {
                int left = (w - vips_image_get_width(img)) / 2;
                int top  = (h - vips_image_get_height(img)) / 2;
                if (vips_embed(img, &tmp, left, top, w, h, "extend", VIPS_EXTEND_BLACK, nullptr)) {
                    g_object_unref(img);
                    img = nullptr;
                    break;
                }
                g_object_unref(img);
                img = tmp;
            }
            break;
        case MODE_STRETCH:
            if (vips_thumbnail(item->filename,
                               &img,
                               w,
                               "height",
                               h,
                               "size",
                               VIPS_SIZE_FORCE,
                               "no_rotate",
                               TRUE,
                               nullptr)) {
                img = nullptr;
            }
            break;
    }

    if (!img)
        goto vips_err;

    if (vips_colourspace(img, &tmp, VIPS_INTERPRETATION_sRGB, nullptr))
        goto vips_err;
    g_object_unref(img);
    img = tmp;

    if (vips_image_get_bands(img) != bands) {
        if (bands == 4) {
            if (vips_addalpha(img, &tmp, nullptr)) {
                g_object_unref(img);
                goto vips_err;
            }
        } else {
            if (vips_extract_band(img, &tmp, 0, "n", 3, nullptr)) {
                g_object_unref(img);
                goto vips_err;
            }
        }
        g_object_unref(img);
        img = tmp;
    }

    if (vips_image_get_format(img) != VIPS_FORMAT_UCHAR) {
        if (vips_cast(img, &tmp, VIPS_FORMAT_UCHAR, nullptr)) {
            g_object_unref(img);
            goto vips_err;
        }
        g_object_unref(img);
        img = tmp;
    }

    if (write_pipeline_to_buffer_direct(img, map, raw_sz) != 0) {
        g_object_unref(img);
        goto vips_err;
    }

    /* Wrap mmap'd standard layer as source for glass. Single decode, not two. */
    VipsImage* from_map = vips_image_new_from_memory(map, raw_sz, w, h, bands, VIPS_FORMAT_UCHAR);
    if (!from_map) {
        g_object_unref(img);
        goto vips_err;
    }

    double scale_x = (double)gw / (double)w;
    double scale_y = (double)gh / (double)h;
    if (vips_resize(from_map, &tmp, scale_x, "vscale", scale_y, nullptr)) {
        g_object_unref(from_map);
        g_object_unref(img);
        goto vips_err;
    }
    g_object_unref(from_map);
    VipsImage* g_in = tmp;

    VipsImage* g_out = apply_liquid_glass_effect_vips(g_in, GLASS_BLUR_SIGMA / GLASS_DOWN_FACTOR);
    g_object_unref(g_in);
    if (!g_out) {
        g_object_unref(img);
        goto vips_err;
    }

    write_pipeline_to_buffer_direct(g_out, map + aligned_raw_sz, glass_sz);
    g_object_unref(g_out);
    g_object_unref(img);

    munmap(map, total_sz);
    result.standard = (struct image_data_buffer){
        .fd = fd, .buffer_size = raw_sz, .width = w, .height = h, .stride = 0};
    result.glass = (struct image_data_buffer){
        .fd = dup(fd), .buffer_size = glass_sz, .width = gw, .height = gh, .stride = 0};
    result.pixel_format = (bands == 4) ? GL_RGBA : GL_RGB;
    result.success      = true;
    free(cpath);
    goto finalize;

vips_err:
    if (img)
        g_object_unref(img);
    munmap(map, total_sz);
    close(fd);
    unlink(cpath);
    free(cpath);

finalize:
    cleanup_vips_thread();

    [[maybe_unused]]
    size_t vips_mem
        = vips_tracked_get_mem();
    [[maybe_unused]]
    size_t vips_mem_hw
        = vips_tracked_get_mem_highwater();
    [[maybe_unused]]
    int vips_allocs
        = vips_tracked_get_allocs();

    output->async_result = result;
    uint64_t sig         = 1;
    if (write(output->event_fd, &sig, sizeof(sig)) != sizeof(sig)) {
    }
    return nullptr;
}

/* -- Frame Loop ---------------------------------------------------------- */

static void frame_callback_handler(void* data, struct wl_callback* callback, uint32_t time);
static const struct wl_callback_listener frame_listener = {.done = frame_callback_handler};

static void render_frame(struct wallpaper_output* output)
{
    if ((output->render.flags & F_DEAD) || output->render.egl_surface == EGL_NO_SURFACE)
        return;
    if (output->render.t_state == T_STATE_IDLE)
        return;

    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);

    uint64_t now_ns  = (uint64_t)now.tv_sec * 1000000000ULL + (uint64_t)now.tv_nsec;
    float    elapsed = (float)(now_ns - output->render.anim_start_ns) * 1e-9f;

    float t_norm   = elapsed * output->render.duration_inv;
    bool  finished = false;

    if (t_norm >= 1.0f) {
        t_norm   = 1.0f;
        finished = true;
    }

    float t_input = ease_in_out_cubic(t_norm);

    if (!eglMakeCurrent(output->render.state->egl_display,
                        output->render.egl_surface,
                        output->render.egl_surface,
                        output->render.state->egl_context))
        return;

    glViewport(0, 0, output->render.width, output->render.height);
    glUseProgram(output->render.state->shader_program_t1);

    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, output->render.tex_A);
    glUniform1i(output->render.state->u_TexA, 0);

    glActiveTexture(GL_TEXTURE1);
    glBindTexture(GL_TEXTURE_2D, output->render.tex_GlassA);
    glUniform1i(output->render.state->u_TexGlassA, 1);

    glActiveTexture(GL_TEXTURE2);
    glBindTexture(GL_TEXTURE_2D, output->render.tex_B);
    glUniform1i(output->render.state->u_TexB, 2);

    glActiveTexture(GL_TEXTURE3);
    glBindTexture(GL_TEXTURE_2D, output->render.tex_GlassB);
    glUniform1i(output->render.state->u_TexGlassB, 3);

    glUniform1f(output->render.state->u_Time, t_input);
    glUniform2f(output->render.state->u_Resolution,
                (float)output->render.width,
                (float)output->render.height);
    glUniform2f(output->render.state->u_CenterPointPixels, output->t_center_x, output->t_center_y);
    glUniform1f(output->render.state->u_MaxRadiusPixels, output->t_max_radius);

    glBindVertexArray(output->render.vao);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    eglSwapBuffers(output->render.state->egl_display, output->render.egl_surface);

    if (finished) {
        output->render.t_state = T_STATE_IDLE;
        if (output->frame_callback) {
            wl_callback_destroy(output->frame_callback);
            output->frame_callback = nullptr;
        }

        /* Swap A<->B so next transition blends FROM current TO new. */
        GLuint temp          = output->render.tex_A;
        output->render.tex_A = output->render.tex_B;
        output->render.tex_B = temp;

        temp                      = output->render.tex_GlassA;
        output->render.tex_GlassA = output->render.tex_GlassB;
        output->render.tex_GlassB = temp;
    } else {
        if (output->frame_callback)
            wl_callback_destroy(output->frame_callback);
        if (!(output->render.flags & F_DEAD) && output->surface) {
            output->frame_callback = wl_surface_frame(output->surface);
            wl_callback_add_listener(output->frame_callback, &frame_listener, output);
            wl_surface_commit(output->surface);
        }
    }
}

static void frame_callback_handler(void* data, struct wl_callback* callback, uint32_t time)
{
    (void)time;
    (void)callback;
    render_frame((struct wallpaper_output*)data);
}

static void upload_texture(
    struct wallpaper_output* o, GLuint tex, struct image_data_buffer* b, off_t off, GLenum fmt)
{
    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, o->render.pbo);
    glBufferData(GL_PIXEL_UNPACK_BUFFER, b->buffer_size, nullptr, GL_STREAM_DRAW);

    void* ptr = glMapBufferRange(
        GL_PIXEL_UNPACK_BUFFER, 0, b->buffer_size, GL_MAP_WRITE_BIT | GL_MAP_INVALIDATE_BUFFER_BIT);
    if (ptr) {
        ssize_t n = pread(b->fd, ptr, b->buffer_size, off);
        if (n < 0 || (size_t)n != b->buffer_size) {
            fprintf(stderr, "[GLES] PBO read failed or partial: %zd/%zu\n", n, b->buffer_size);
        }
        glUnmapBuffer(GL_PIXEL_UNPACK_BUFFER);
        glBindTexture(GL_TEXTURE_2D, tex);
        GLint internal_fmt = (fmt == GL_RGB) ? GL_RGB8 : GL_RGBA8;
        glTexImage2D(
            GL_TEXTURE_2D, 0, internal_fmt, b->width, b->height, 0, fmt, GL_UNSIGNED_BYTE, 0);
    }
    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);
}

static void finalize_render(struct wallpaper_output* output)
{
    if (!(output->render.flags & F_THREAD_ACTIVE))
        return;
    pthread_join(output->render_thread, nullptr);
    output->render.flags &= ~F_THREAD_ACTIVE;

    if (output->render.flags & F_DEAD) {
        if (output->event_fd >= 0) {
            close(output->event_fd);
            output->event_fd = -1;
        }
    }

    struct render_result res = output->async_result;
    output->async_result     = (struct render_result){.standard.fd = -1, .glass.fd = -1};

    if (output->render.flags & F_DEAD) {
        if (res.standard.fd >= 0)
            close(res.standard.fd);
        if (res.glass.fd >= 0)
            close(res.glass.fd);
        return;
    }

    /* Deferred reload: config changed while render was in-flight. Discard stale result. */
    if (output->pending_reload) {
        output->pending_reload    = false;
        struct output_config* cfg = get_config_for_output(output->render.state, output->name);
        if (cfg) {
            apply_config_to_output(output, cfg);
            update_wallpaper(output);
            if (res.standard.fd >= 0)
                close(res.standard.fd);
            if (res.glass.fd >= 0)
                close(res.glass.fd);
            return;
        }
    }

    if (!res.success || output->render.egl_surface == EGL_NO_SURFACE) {
        if (res.standard.fd >= 0)
            close(res.standard.fd);
        if (res.glass.fd >= 0)
            close(res.glass.fd);
        return;
    }

    if (res.standard.width != output->render.width
        || res.standard.height != output->render.height) {
        close(res.standard.fd);
        close(res.glass.fd);
        return;
    }

    eglMakeCurrent(output->render.state->egl_display,
                   output->render.egl_surface,
                   output->render.egl_surface,
                   output->render.state->egl_context);

    long   pg  = sysconf(_SC_PAGESIZE);
    size_t off = (res.standard.buffer_size + (pg - 1))
                 & ~(pg - 1); /* Glass layer starts at page boundary in cache file */

    upload_texture(output, output->render.tex_B, &res.standard, 0, res.pixel_format);
    upload_texture(output, output->render.tex_GlassB, &res.glass, off, res.pixel_format);

    bool first_boot = !(output->render.flags & F_BOOT_COMPLETE);
    if (first_boot) {
        upload_texture(output, output->render.tex_A, &res.standard, 0, res.pixel_format);
        upload_texture(output, output->render.tex_GlassA, &res.glass, off, res.pixel_format);
        output->render.flags |= F_BOOT_COMPLETE;
    }

    close(res.standard.fd);
    close(res.glass.fd);

    glUseProgram(output->render.state->shader_program_t1);
    glUniform2f(output->render.state->u_Resolution,
                (float)output->render.width,
                (float)output->render.height);

    if ((output->render.flags & F_TRANSITION_ON) && !first_boot) {
        output->render.t_state = T_STATE_RUNNING;

        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        output->render.anim_start_ns = (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;

        int cx = (int)xoshiro256pp_bounded(&g_rng, output->render.width / 2)
                 + output->render.width / 4;
        int cy = (int)xoshiro256pp_bounded(&g_rng, output->render.height / 2)
                 + output->render.height / 4;
        output->t_center_x = (float)cx;
        output->t_center_y = (float)(output->render.height - cy); /* GL Y-axis is bottom-up */

        float d1 = hypotf((float)cx, output->t_center_y);
        float d2 = hypotf((float)(output->render.width - cx), output->t_center_y);
        float d3 = hypotf((float)cx, (float)(output->render.height - output->t_center_y));
        float d4 = hypotf((float)(output->render.width - cx),
                          (float)(output->render.height - output->t_center_y));
        output->t_max_radius = fmaxf(d1, fmaxf(d2, fmaxf(d3, d4)));

        float duration              = output->transition_duration > 0 ? output->transition_duration
                                                                      : DEFAULT_TRANSITION_DUR;
        output->render.duration_inv = 1.0f / duration;
    } else {
        output->render.t_state = T_STATE_RUNNING;

        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        float duration              = output->transition_duration > 0 ? output->transition_duration
                                                                      : DEFAULT_TRANSITION_DUR;
        output->render.duration_inv = 1.0f / duration;

        uint64_t offset_ns           = (uint64_t)(duration + 1.0f) * 1000000000ULL;
        uint64_t now_ns              = (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
        output->render.anim_start_ns = now_ns - offset_ns;

        output->t_max_radius = 1.0f;
    }

    glUniform1f(output->render.state->u_MaxRadiusPixels, output->t_max_radius);
    glUseProgram(0);

    if (first_boot) {
        render_frame(output);
    } else {
        if (output->frame_callback)
            wl_callback_destroy(output->frame_callback);
        output->frame_callback = wl_surface_frame(output->surface);
        wl_callback_add_listener(output->frame_callback, &frame_listener, output);
        wl_surface_commit(output->surface);
    }
}

/* -- Output Lifecycle ---------------------------------------------------- */

static void destroy_output(struct wallpaper_output* o)
{
    if (!o || (o->render.flags & F_DEAD))
        return;
    o->render.flags |= F_DEAD;

    dbg_print("[INFO] Deactivating output: '%s' (ID: %u)\n",
              o->name ? o->name : "unknown",
              o->wl_output_name);

    if (o->timer_fd >= 0) {
        close(o->timer_fd);
        o->timer_fd = -1;
    }

    if (!(o->render.flags & F_THREAD_ACTIVE)) {
        if (o->event_fd >= 0) {
            close(o->event_fd);
            o->event_fd = -1;
        }
        if (o->async_result.standard.fd >= 0) {
            close(o->async_result.standard.fd);
            o->async_result.standard.fd = -1;
        }
        if (o->async_result.glass.fd >= 0) {
            close(o->async_result.glass.fd);
            o->async_result.glass.fd = -1;
        }
    } else {
        dbg_print("[INFO] Render thread active on '%s'. Deferring cleanup.\n", o->name);
    }

    if (o->render.state->egl_initialized) {
        EGLDisplay display = o->render.state->egl_display;
        EGLContext context = o->render.state->egl_context;

        if (o->render.flags & F_TEX_INIT) {
            if (eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context)) {
                glDeleteTextures(1, &o->render.tex_A);
                glDeleteTextures(1, &o->render.tex_GlassA);
                glDeleteTextures(1, &o->render.tex_B);
                glDeleteTextures(1, &o->render.tex_GlassB);
                glDeleteBuffers(1, &o->vbo);
                glDeleteBuffers(1, &o->render.pbo);
                glDeleteVertexArrays(1, &o->render.vao);
                o->render.flags &= ~F_TEX_INIT;
                eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            }
        }

        if (o->render.egl_surface != EGL_NO_SURFACE) {
            if (eglGetCurrentSurface(EGL_READ) == o->render.egl_surface
                || eglGetCurrentSurface(EGL_DRAW) == o->render.egl_surface) {
                eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            }
            eglDestroySurface(display, o->render.egl_surface);
            o->render.egl_surface = EGL_NO_SURFACE;
        }
    }

    if (o->egl_window) {
        wl_egl_window_destroy(o->egl_window);
        o->egl_window = nullptr;
    }
    if (o->frame_callback) {
        wl_callback_destroy(o->frame_callback);
        o->frame_callback = nullptr;
    }
    if (o->layer_surface) {
        zwlr_layer_surface_v1_destroy(o->layer_surface);
        o->layer_surface = nullptr;
    }
    if (o->surface) {
        wl_surface_destroy(o->surface);
        o->surface = nullptr;
    }
    if (o->wl_output) {
        wl_output_destroy(o->wl_output);
        o->wl_output = nullptr;
    }
}

static void launch_async_render(struct wallpaper_output* o)
{
    if ((o->render.flags & F_DEAD) || !(o->render.flags & F_CONFIGURED)
        || (o->render.flags & F_THREAD_ACTIVE) || o->num_items == 0)
        return;
    uint64_t c;
    while (read(o->event_fd, &c, sizeof(c)) > 0)
        ;
    if (pthread_create(&o->render_thread, nullptr, render_thread_worker, o) == 0)
        o->render.flags |= F_THREAD_ACTIVE;
}

static void update_wallpaper(struct wallpaper_output* o)
{
    if ((o->render.flags & F_DEAD) || (o->render.flags & F_THREAD_ACTIVE))
        return;
    if (o->render.flags & F_RANDOMIZE) {
        size_t next;
        do {
            next = (size_t)xoshiro256pp_bounded(&g_rng, o->num_items);
        } while (next == o->current_item_index && o->num_items > 1);
        o->current_item_index = next;
    } else {
        o->current_item_index = (o->current_item_index + 1) % o->num_items;
    }
    launch_async_render(o);
}

/* -- Item List ----------------------------------------------------------- */

static void init_item_list(struct item_list* list)
{
    *list = (struct item_list){};
}

static void free_item_list(struct item_list* list)
{
    for (size_t i = 0; i < list->count; i++)
        free(list->items[i].filename);
    free(list->items);
    init_item_list(list);
}

[[nodiscard]]
static bool duplicate_item_list(const struct item_list* src, struct item_list* dst)
{
    if (src->count == 0) {
        *dst = (struct item_list){};
        return true;
    }

    size_t alloc_sz;
    if (ckd_mul(&alloc_sz, src->count, sizeof(struct wallpaper_item))) {
        errno = EOVERFLOW;
        return false;
    }

    dst->items = malloc(alloc_sz);
    if (!dst->items)
        return false;
    dst->count    = 0;
    dst->capacity = src->count;

    for (size_t i = 0; i < src->count; i++) {
        dst->items[i]          = src->items[i];
        dst->items[i].filename = strdup(src->items[i].filename);
        if (!dst->items[i].filename) {
            free_item_list(dst);
            return false;
        }
        dst->count++;
    }
    return true;
}

[[nodiscard]]
static bool add_item_to_list(struct item_list*   list,
                             const char*         filename,
                             enum wallpaper_mode mode,
                             VipsInteresting     strategy)
{
    if (list->count >= list->capacity) {
        size_t new_cap = list->capacity == 0 ? 16 : list->capacity * 2;
        if (new_cap <= list->capacity)
            return false;
        size_t new_sz;
        if (ckd_mul(&new_sz, new_cap, sizeof(struct wallpaper_item)))
            return false;
        auto new_items = realloc(list->items, new_sz);
        if (!new_items)
            return false;
        list->items    = new_items;
        list->capacity = new_cap;
    }
    auto dup_fn = strdup(filename);
    if (!dup_fn)
        return false;
    list->items[list->count++]
        = (struct wallpaper_item){.filename = dup_fn, .mode = mode, .crop_strategy = strategy};
    return true;
}

/* -- Configuration Parsing ----------------------------------------------- */

static bool is_directory(const char* path)
{
    struct stat st;
    return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

static bool is_supported_image(const char* filename)
{
    return vips_foreign_find_load(filename) != nullptr;
}

static int scan_directory(struct item_list*   list,
                          const char*         path,
                          enum wallpaper_mode mode,
                          VipsInteresting     strategy)
{
    DIR* dir = opendir(path);
    if (!dir)
        return 0;

    int            added = 0;
    struct dirent* entry;
    while ((entry = readdir(dir)) != nullptr) {
        if (entry->d_name[0] == '.')
            continue;

        char full_path[PATH_MAX];
        int  len = snprintf(full_path, sizeof(full_path), "%s/%s", path, entry->d_name);
        if (len < 0 || len >= (int)sizeof(full_path))
            continue;

        bool is_file = false;
#ifdef DT_REG
        if (entry->d_type == DT_REG)
            is_file = true;
        else if (entry->d_type == DT_UNKNOWN || entry->d_type == DT_LNK)
#endif
        {
            struct stat st;
            if (stat(full_path, &st) == 0 && S_ISREG(st.st_mode))
                is_file = true;
        }

        if (is_file && is_supported_image(full_path)) {
            if (!add_item_to_list(list, full_path, mode, strategy)) {
                closedir(dir);
                return -1;
            }
            added++;
        }
    }
    closedir(dir);
    return added;
}

static char* trim_whitespace(char* str)
{
    if (!str)
        return nullptr;
    while (isspace((unsigned char)*str))
        str++;
    if (*str == 0)
        return str;
    char* end = str + strlen(str) - 1;
    while (end > str && isspace((unsigned char)*end))
        end--;
    end[1] = '\0';
    return str;
}

[[nodiscard]]
static char* get_config_path(void)
{
    auto cfg_home = getenv("XDG_CONFIG_HOME");
    auto home     = getenv("HOME");
    char path[PATH_MAX];

    if (cfg_home && *cfg_home) {
        snprintf(path, sizeof(path), "%s/walle/config.ini", cfg_home);
        if (access(path, R_OK) == 0)
            return strdup(path);
    }
    if (home && *home) {
        snprintf(path, sizeof(path), "%s/.config/walle/config.ini", home);
        if (access(path, R_OK) == 0)
            return strdup(path);
    }
    if (access("config.ini", R_OK) == 0)
        return strdup("config.ini");
    return nullptr;
}

static enum wallpaper_mode parse_mode_prefix(char** path_ptr, VipsInteresting* strategy)
{
    char*               path = *path_ptr;
    enum wallpaper_mode mode = MODE_FILL;
    *strategy                = VIPS_INTERESTING_ENTROPY;

    if (strncasecmp(path, "FIT:", 4) == 0) {
        mode = MODE_FIT;
        *path_ptr += 4;
    } else if (strncasecmp(path, "STRETCH:", 8) == 0) {
        mode = MODE_STRETCH;
        *path_ptr += 8;
    } else if (strncasecmp(path, "FILL_ATTENTION:", 15) == 0) {
        *strategy = VIPS_INTERESTING_ATTENTION;
        *path_ptr += 15;
    } else if (strncasecmp(path, "FILL_ENTROPY:", 13) == 0) {
        *strategy = VIPS_INTERESTING_ENTROPY;
        *path_ptr += 13;
    } else if (strncasecmp(path, "FILL_CENTER:", 12) == 0
               || strncasecmp(path, "FILL_CENTRE:", 12) == 0) {
        *strategy = VIPS_INTERESTING_CENTRE;
        *path_ptr += 12;
    } else if (strncasecmp(path, "FILL_HIGH:", 10) == 0) {
        *strategy = VIPS_INTERESTING_HIGH;
        *path_ptr += 10;
    } else if (strncasecmp(path, "FILL_LOW:", 9) == 0) {
        *strategy = VIPS_INTERESTING_LOW;
        *path_ptr += 9;
    } else if (strncasecmp(path, "FILL:", 5) == 0) {
        *path_ptr += 5;
    }

    *path_ptr = trim_whitespace(*path_ptr);
    return mode;
}

static bool process_single_config_entry(struct item_list* list, const char* value)
{
    if (!value || !*value)
        return true;
    auto copy = strdup(value);
    if (!copy)
        return false;

    char* trimmed = trim_whitespace(copy);
    if (!*trimmed) {
        free(copy);
        return true;
    }

    VipsInteresting     strategy;
    enum wallpaper_mode mode = parse_mode_prefix(&trimmed, &strategy);
    if (!*trimmed) {
        free(copy);
        return true;
    }

    char*       expanded   = expand_path(trimmed);
    const char* final_path = expanded ? expanded : trimmed;

    bool ok = true;
    if (is_directory(final_path)) {
        if (scan_directory(list, final_path, mode, strategy) < 0)
            ok = false;
    } else {
        if (!add_item_to_list(list, final_path, mode, strategy))
            ok = false;
    }

    free(copy);
    free(expanded);
    return ok;
}

static struct output_config* get_or_create_config_in_list(struct wl_list* list, const char* name)
{
    struct output_config* oc;
    wl_list_for_each(oc, list, link)
    {
        if (strcmp(oc->output_name, name) == 0)
            return oc;
    }

    auto new_oc = (struct output_config*)malloc(sizeof(struct output_config));
    if (!new_oc)
        return nullptr;
    *new_oc = (struct output_config){
        .transition_on = false, .gamemode = true, .transition_duration = DEFAULT_TRANSITION_DUR};
    new_oc->output_name = strdup(name);
    init_item_list(&new_oc->items);
    wl_list_insert(list, &new_oc->link);
    return new_oc;
}

static int config_handler(void* user, const char* section, const char* name, const char* value)
{
    auto ctx = (struct config_parse_ctx*)user;
    auto oc  = get_or_create_config_in_list(ctx->config_list, section);
    if (!oc)
        return 0;

    if (strcasecmp(name, "files") == 0 || strcasecmp(name, "paths") == 0) {
        process_single_config_entry(&oc->items, value);
    } else if (strcasecmp(name, "timeout") == 0) {
        oc->timeout = atoi(value);
    } else if (strcasecmp(name, "randomize") == 0) {
        oc->randomize = (strcmp(value, "true") == 0 || strcmp(value, "1") == 0);
    } else if (strcasecmp(name, "transition") == 0) {
        oc->transition_on = (strcmp(value, "true") == 0 || strcmp(value, "1") == 0);
    } else if (strcasecmp(name, "transition_duration") == 0) {
        oc->transition_duration = strtof(value, nullptr);
    } else if (strcasecmp(name, "gamemode") == 0) {
        oc->gamemode = (strcmp(value, "true") == 0 || strcmp(value, "1") == 0);
    }
    return 1;
}

/* -- Config Application -------------------------------------------------- */

static void apply_config_to_output(struct wallpaper_output* output, struct output_config* config)
{
    printf("[HOT RELOAD] Updating output '%s'...\n", output->name);

    struct item_list new_items = {};
    if (duplicate_item_list(&config->items, &new_items)) {
        for (size_t i = 0; i < output->num_items; i++)
            free(output->items[i].filename);
        free(output->items);
        output->items     = new_items.items;
        output->num_items = new_items.count;
    }

    output->timeout          = config->timeout;
    output->gamemode_enabled = config->gamemode;
    if (config->randomize)
        output->render.flags |= F_RANDOMIZE;
    else
        output->render.flags &= ~F_RANDOMIZE;
    if (config->transition_on)
        output->render.flags |= F_TRANSITION_ON;
    else
        output->render.flags &= ~F_TRANSITION_ON;
    output->transition_duration = config->transition_duration;

    if (output->current_item_index >= output->num_items)
        output->current_item_index = 0;

    if (output->timer_fd >= 0) {
        close(output->timer_fd);
        output->timer_fd = -1;
    }
    if (output->num_items > 1 && output->timeout > 0) {
        output->timer_fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);

        if (output->render.state->gamemode_active && output->gamemode_enabled) {
            struct itimerspec ts = {};
            timerfd_settime(output->timer_fd, 0, &ts, nullptr);
        } else {
            struct itimerspec ts
                = {.it_interval = {output->timeout, 0}, .it_value = {output->timeout, 0}};
            timerfd_settime(output->timer_fd, 0, &ts, nullptr);
        }
    }
}

static void reload_global_config(struct wallpaper_state* state)
{
    if (!state->config_path)
        return;
    printf("[CONFIG] Detected change. Reloading...\n");

    struct wl_list new_configs;
    wl_list_init(&new_configs);
    struct config_parse_ctx ctx = {.config_list = &new_configs};

    if (ini_parse(state->config_path, config_handler, &ctx) < 0) {
        fprintf(stderr, "[CONFIG] Parse failed. Keeping old config.\n");
        struct output_config *oc, *tmp;
        wl_list_for_each_safe(oc, tmp, &new_configs, link)
        {
            wl_list_remove(&oc->link);
            free_item_list(&oc->items);
            free(oc->output_name);
            free(oc);
        }
        return;
    }

    struct output_config *oc, *tmp;
    wl_list_for_each_safe(oc, tmp, &state->output_configs, link)
    {
        wl_list_remove(&oc->link);
        free_item_list(&oc->items);
        free(oc->output_name);
        free(oc);
    }
    wl_list_insert_list(&state->output_configs, &new_configs);

    struct wallpaper_output* output;
    wl_list_for_each(output, &state->outputs, link)
    {
        if (output->render.flags & F_DEAD)
            continue;

        struct output_config* cfg = get_config_for_output(output->render.state, output->name);
        if (cfg) {
            if (output->render.flags & F_THREAD_ACTIVE) {
                output->pending_reload = true;
            } else {
                apply_config_to_output(output, cfg);
                update_wallpaper(output);
            }
        }
    }
}

/* -- Wayland Listeners --------------------------------------------------- */

static struct output_config* get_config_for_output(struct wallpaper_state* state, const char* name)
{
    struct output_config *iter, *fallback = nullptr;
    wl_list_for_each(iter, &state->output_configs, link)
    {
        if (strcmp(iter->output_name, name) == 0)
            return iter;
        if (!fallback
            && (strcmp(iter->output_name, "default") == 0 || strcmp(iter->output_name, "*") == 0))
            fallback = iter;
    }
    return fallback;
}

static void layer_surface_configure(
    void* data, struct zwlr_layer_surface_v1* surf, uint32_t serial, uint32_t w, uint32_t h)
{
    dbg_print("layer_surface_configure: %ux%u\n", w, h);
    auto output = (struct wallpaper_output*)data;

    if (output->render.flags & F_DEAD) {
        zwlr_layer_surface_v1_ack_configure(surf, serial);
        return;
    }

    output->render.width  = w;
    output->render.height = h;
    output->render.flags |= F_CONFIGURED;

    zwlr_layer_surface_v1_ack_configure(surf, serial);

    if (output->render.state->egl_initialized) {
        if (output->egl_window) {
            wl_egl_window_resize(output->egl_window, w, h, 0, 0);
        } else {
            output->egl_window = wl_egl_window_create(output->surface, w, h);
            output->render.egl_surface
                = eglCreateWindowSurface(output->render.state->egl_display,
                                         output->render.state->egl_config,
                                         (EGLNativeWindowType)output->egl_window,
                                         nullptr);
            init_output_gl(output);
        }
    }

    launch_async_render(output);
}

static void layer_surface_closed(void* data, struct zwlr_layer_surface_v1* surf)
{
    (void)surf;
    auto output = (struct wallpaper_output*)data;
    printf("[INFO] Layer surface closed by compositor for output: %s\n", output->name);
    destroy_output(output);
}

static const struct zwlr_layer_surface_v1_listener layer_surface_listener
    = {.configure = layer_surface_configure, .closed = layer_surface_closed};

static void output_handle_geometry(void*,
                                   struct wl_output*,
                                   int32_t,
                                   int32_t,
                                   int32_t,
                                   int32_t,
                                   int32_t,
                                   const char*,
                                   const char*,
                                   int32_t)
{
}
static void output_handle_mode(void*, struct wl_output*, uint32_t, int32_t, int32_t, int32_t)
{
}
static void output_handle_scale(void*, struct wl_output*, int32_t)
{
}
static void output_handle_description(void*, struct wl_output*, const char*)
{
}

static void output_handle_name(void* data, struct wl_output*, const char* name)
{
    auto output = (struct wallpaper_output*)data;
    free(output->name);
    output->name = strdup(name);
    dbg_print("Discovered output: '%s'\n", name);
}

static void output_handle_done(void* data, struct wl_output* wl_output)
{
    (void)wl_output;
    initialize_output((struct wallpaper_output*)data);
}

static const struct wl_output_listener output_listener = {
    .geometry    = output_handle_geometry,
    .mode        = output_handle_mode,
    .done        = output_handle_done,
    .scale       = output_handle_scale,
    .name        = output_handle_name,
    .description = output_handle_description,
};

static void initialize_output(struct wallpaper_output* output)
{
    if ((output->render.flags & F_DEAD) || (output->render.flags & F_INITIALIZED))
        return;
    if (!output->name)
        return;

    struct wallpaper_state* state = output->render.state;

    struct output_config* config = get_config_for_output(state, output->name);

    if (config && config->items.count > 0) {
        printf("[CONFIG] Applying config [%s] to output %s (%zu items)\n",
               config->output_name,
               output->name,
               config->items.count);

        struct item_list dup_list = {};
        if (!duplicate_item_list(&config->items, &dup_list)) {
            fprintf(stderr, "[FATAL] OOM duplicating item list for %s\n", output->name);
            output->render.flags |= F_INITIALIZED;
            return;
        }

        output->items            = dup_list.items;
        output->num_items        = dup_list.count;
        output->timeout          = config->timeout;
        output->gamemode_enabled = config->gamemode;
        if (config->randomize)
            output->render.flags |= F_RANDOMIZE;
        if (config->transition_on)
            output->render.flags |= F_TRANSITION_ON;
        output->transition_duration = config->transition_duration;

        if (output->render.flags & F_RANDOMIZE) {
            output->current_item_index = (size_t)xoshiro256pp_bounded(&g_rng, output->num_items);
        } else {
            output->current_item_index = 0;
        }
    } else {
        printf("[INFO] No configuration for output: %s. Inactive.\n", output->name);
        output->render.flags |= F_INITIALIZED;
        return;
    }

    if (output->event_fd < 0) {
        output->event_fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        if (output->event_fd < 0) {
            fprintf(stderr, "[ERROR] eventfd failed for %s: %s\n", output->name, strerror(errno));
            goto init_failed;
        }
    }

    output->timer_fd = -1;
    if (output->timeout > 0 && output->num_items > 1) {
        output->timer_fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);

        if (state->gamemode_active && output->gamemode_enabled) {
            struct itimerspec ts = {};
            timerfd_settime(output->timer_fd, 0, &ts, nullptr);
        } else {
            struct itimerspec ts
                = {.it_interval = {output->timeout, 0}, .it_value = {output->timeout, 0}};
            timerfd_settime(output->timer_fd, 0, &ts, nullptr);
        }
    }

    if (!state->compositor || !state->layer_shell) {
        fprintf(stderr, "[ERROR] Missing Wayland globals for %s\n", output->name);
        goto init_failed;
    }

    output->surface = wl_compositor_create_surface(state->compositor);
    if (!output->surface) {
        fprintf(stderr, "[ERROR] wl_surface creation failed for %s\n", output->name);
        goto init_failed;
    }

    output->layer_surface
        = zwlr_layer_shell_v1_get_layer_surface(state->layer_shell,
                                                output->surface,
                                                output->wl_output,
                                                ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND,
                                                "walle");

    if (!output->layer_surface) {
        fprintf(stderr, "[ERROR] layer_surface creation failed for %s\n", output->name);
        goto init_failed;
    }

    zwlr_layer_surface_v1_set_anchor(output->layer_surface, 15);
    zwlr_layer_surface_v1_set_exclusive_zone(output->layer_surface, -1);
    zwlr_layer_surface_v1_add_listener(output->layer_surface, &layer_surface_listener, output);

    wl_surface_commit(output->surface);
    output->render.flags |= F_INITIALIZED;
    printf("[INFO] Output initialized: '%s'\n", output->name);
    return;

init_failed:
    destroy_output(output);
}

static void registry_global(void*                     data,
                            struct wl_registry*       reg,
                            uint32_t                  name,
                            const char*               interface,
                            [[maybe_unused]] uint32_t ver)
{
    auto state = (struct wallpaper_state*)data;

    if (strcmp(interface, wl_compositor_interface.name) == 0) {
        state->compositor = wl_registry_bind(reg, name, &wl_compositor_interface, 4);
    } else if (strcmp(interface, zwlr_layer_shell_v1_interface.name) == 0) {
        state->layer_shell = wl_registry_bind(reg, name, &zwlr_layer_shell_v1_interface, 1);
    } else if (strcmp(interface, wl_output_interface.name) == 0) {
        auto o = (struct wallpaper_output*)malloc(sizeof(struct wallpaper_output));
        if (!o) {
            fprintf(stderr, "FATAL: malloc failed\n");
            return;
        }
        *o = (struct wallpaper_output){.render   = {.state = state, .egl_surface = EGL_NO_SURFACE},
                                       .timer_fd = -1,
                                       .event_fd = -1,
                                       .wl_output_name = name,
                                       .async_result   = {.standard.fd = -1, .glass.fd = -1}};
        o->wl_output = wl_registry_bind(reg, name, &wl_output_interface, 4);
        wl_output_add_listener(o->wl_output, &output_listener, o);
        wl_list_insert(&state->outputs, &o->link);
    }
}

static void registry_global_remove(void* data, struct wl_registry* reg, uint32_t name)
{
    (void)reg;
    auto                     state = (struct wallpaper_state*)data;
    struct wallpaper_output* o;
    wl_list_for_each(o, &state->outputs, link)
    {
        if (o->wl_output_name == name) {
            destroy_output(o);
            return;
        }
    }
}

static const struct wl_registry_listener registry_listener
    = {.global = registry_global, .global_remove = registry_global_remove};

/* -- Main ---------------------------------------------------------------- */

static void signal_handler(int _)
{
    (void)_;
    g_running = 0;
}

int main(int argc, char* argv[])
{
    (void)argc;

    signal(SIGPIPE, SIG_IGN);

    if (VIPS_INIT(argv[0]))
        vips_error_exit(nullptr);
    vips_cache_set_max(0);
    vips_cache_set_max_mem(0);
    vips_cache_set_max_files(0);
    vips_concurrency_set(1);

    xoshiro256pp_seed(&g_rng, (uint64_t)time(nullptr) ^ (uint64_t)getpid());

    launch_cache_maintenance_service();

    struct wallpaper_state state = {};
    wl_list_init(&state.outputs);
    wl_list_init(&state.output_configs);

    struct sigaction sa = {};
    sa.sa_handler       = signal_handler;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, nullptr);
    sigaction(SIGTERM, &sa, nullptr);

    state.display = wl_display_connect(nullptr);
    if (!state.display)
        return 1;

    state.egl_display = eglGetDisplay((EGLNativeDisplayType)state.display);
    if (state.egl_display == EGL_NO_DISPLAY)
        return 1;

    EGLint major, minor;
    if (!eglInitialize(state.egl_display, &major, &minor))
        return 1;

    EGLint config_attribs[] = {EGL_SURFACE_TYPE,
                               EGL_WINDOW_BIT,
                               EGL_RED_SIZE,
                               8,
                               EGL_GREEN_SIZE,
                               8,
                               EGL_BLUE_SIZE,
                               8,
                               EGL_ALPHA_SIZE,
                               8,
                               EGL_RENDERABLE_TYPE,
                               EGL_OPENGL_ES3_BIT,
                               EGL_NONE};
    EGLint n_config;
    if (!eglChooseConfig(state.egl_display, config_attribs, &state.egl_config, 1, &n_config)
        || n_config != 1)
        return 1;

    if (!eglBindAPI(EGL_OPENGL_ES_API))
        return 1;

    EGLint ctx_attribs[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
    state.egl_context
        = eglCreateContext(state.egl_display, state.egl_config, EGL_NO_CONTEXT, ctx_attribs);
    state.egl_initialized = (state.egl_context != EGL_NO_CONTEXT);

    if (state.egl_initialized) {
        if (eglMakeCurrent(state.egl_display, EGL_NO_SURFACE, EGL_NO_SURFACE, state.egl_context)) {
            init_gl_resources(&state);
            eglMakeCurrent(state.egl_display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        }
    }

    state.config_path = get_config_path();
    if (state.config_path) {
        struct config_parse_ctx ctx = {.config_list = &state.output_configs};
        if (ini_parse(state.config_path, config_handler, &ctx) < 0) {
            fprintf(stderr, "FATAL: Could not parse config file: %s\n", state.config_path);
            return 1;
        }

        state.inotify_fd = inotify_init1(IN_CLOEXEC | IN_NONBLOCK);
        if (state.inotify_fd >= 0) {
            char* tmp = strdup(state.config_path);
            if (tmp) {
                char* last_slash = strrchr(tmp, '/');
                if (last_slash) {
                    *last_slash           = '\0';
                    state.config_dir      = strdup(tmp);
                    state.config_filename = strdup(last_slash + 1);
                } else {
                    state.config_dir      = strdup(".");
                    state.config_filename = strdup(tmp);
                }
                free(tmp);
                state.config_wd = inotify_add_watch(
                    state.inotify_fd, state.config_dir, IN_CLOSE_WRITE | IN_MOVED_TO);
            }
        }
    } else {
        fprintf(stderr, "FATAL: Configuration file 'config.ini' not found.\n");
        return 1;
    }

    if (!gamemode_init(&state)) {
        fprintf(stderr, "[GAMEMODE] Portal unavailable. Continuing without GameMode support.\n");
    }

    state.registry = wl_display_get_registry(state.display);
    wl_registry_add_listener(state.registry, &registry_listener, &state);
    wl_display_roundtrip(state.display);
    wl_display_roundtrip(state.display);

    struct pollfd            fds[MAX_POLL_FDS];
    struct wallpaper_output* map[MAX_POLL_FDS];

    while (g_running) {
        struct wallpaper_output *output, *tmp_output;
        wl_list_for_each_safe(output, tmp_output, &state.outputs, link)
        {
            if ((output->render.flags & F_DEAD) && !(output->render.flags & F_THREAD_ACTIVE)) {
                dbg_print("Freeing memory for dead output: '%s'\n",
                          output->name ? output->name : "unknown");
                wl_list_remove(&output->link);
                for (size_t i = 0; i < output->num_items; i++)
                    free(output->items[i].filename);
                free(output->items);
                free(output->name);
                if (output->event_fd >= 0)
                    close(output->event_fd);
                free(output);
            }
        }

        if (wl_list_empty(&state.outputs) && wl_list_empty(&state.output_configs)) {
            printf("[INFO] No configured outputs remaining. Terminating.\n");
            break;
        }

        while (wl_display_prepare_read(state.display) != 0)
            wl_display_dispatch_pending(state.display);

        if (wl_display_flush(state.display) < 0 && errno != EAGAIN) {
            if (errno != EPIPE)
                fprintf(stderr, "wl_display_flush failed: %s\n", strerror(errno));
            break;
        }

        fds[0]  = (struct pollfd){.fd = wl_display_get_fd(state.display), .events = POLLIN};
        int idx = 1;

        int inotify_poll_idx = -1;
        if (state.inotify_fd >= 0) {
            inotify_poll_idx = idx;
            fds[idx]         = (struct pollfd){.fd = state.inotify_fd, .events = POLLIN};
            map[idx++]       = nullptr;
        }

        int      dbus_poll_idx     = -1;
        uint64_t dbus_timeout_usec = UINT64_MAX;

        if (state.bus) {
            int fd     = sd_bus_get_fd(state.bus);
            int events = sd_bus_get_events(state.bus);
            sd_bus_get_timeout(state.bus, &dbus_timeout_usec);

            if (fd >= 0) {
                dbus_poll_idx = idx;
                fds[idx]      = (struct pollfd){.fd = fd, .events = (short)events};
                map[idx++]    = nullptr;
            }
        }

        wl_list_for_each(output, &state.outputs, link)
        {
            if (idx < MAX_POLL_FDS && output->event_fd >= 0) {
                fds[idx]   = (struct pollfd){.fd = output->event_fd, .events = POLLIN};
                map[idx++] = output;
            }
            if (!(output->render.flags & F_DEAD) && output->timer_fd >= 0 && idx < MAX_POLL_FDS) {
                fds[idx]   = (struct pollfd){.fd = output->timer_fd, .events = POLLIN};
                map[idx++] = output;
            }
        }

        int poll_timeout = -1;
        if (dbus_timeout_usec != UINT64_MAX) {
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            uint64_t now_usec = (uint64_t)now.tv_sec * 1000000ULL + (uint64_t)now.tv_nsec / 1000ULL;

            if (dbus_timeout_usec > now_usec) {
                uint64_t diff = dbus_timeout_usec - now_usec;
                poll_timeout  = (diff > (uint64_t)INT_MAX) ? INT_MAX : (int)(diff / 1000ULL);
            } else {
                poll_timeout = 0;
            }
        }

        if (poll(fds, idx, poll_timeout) < 0) {
            if (errno == EINTR) {
                wl_display_cancel_read(state.display);
                continue;
            }
            perror("poll");
            break;
        }

        if (fds[0].revents & (POLLIN | POLLHUP | POLLERR)) {
            if (wl_display_read_events(state.display) == -1
                || wl_display_dispatch_pending(state.display) == -1) {
                fprintf(stderr, "[Wayland] Connection error.\n");
                break;
            }
        } else {
            wl_display_cancel_read(state.display);
        }

        if (inotify_poll_idx >= 0 && (fds[inotify_poll_idx].revents & POLLIN)) {
            alignas(struct inotify_event) char buf[INOTIFY_BUF_LEN];
            ssize_t                            len;
            while ((len = read(state.inotify_fd, buf, sizeof(buf))) > 0) {
                const struct inotify_event* ev;
                for (char* ptr = buf; ptr < buf + len;
                     ptr += sizeof(struct inotify_event) + ev->len) {
                    ev = (const struct inotify_event*)ptr;
                    if (ev->len > 0 && strcmp(ev->name, state.config_filename) == 0) {
                        reload_global_config(&state);
                    }
                }
            }
        }

        if (dbus_poll_idx >= 0) {
            if ((fds[dbus_poll_idx].revents & (POLLIN | POLLOUT | POLLERR)) || poll_timeout == 0) {
                while (sd_bus_process(state.bus, nullptr) > 0)
                    ;
            }
        }

        int start_idx = 1;
        if (inotify_poll_idx >= 0)
            start_idx++;
        if (dbus_poll_idx >= 0)
            start_idx++;
        for (int i = start_idx; i < idx; i++) {
            if (fds[i].revents & (POLLIN | POLLHUP | POLLERR)) {
                struct wallpaper_output* cur = map[i];
                uint64_t                 u;
                ssize_t                  n_read = read(fds[i].fd, &u, sizeof(u));
                if (n_read != (ssize_t)sizeof(u))
                    continue;

                if (fds[i].fd == cur->event_fd) {
                    finalize_render(cur);
                } else if (fds[i].fd == cur->timer_fd) {
                    if (!(cur->render.flags & F_DEAD)) {
                        update_wallpaper(cur);
                    }
                }
            }
        }
    }

    struct wallpaper_output *output, *tmp_output;
    wl_list_for_each_safe(output, tmp_output, &state.outputs, link)
    {
        destroy_output(output);

        if (output->render.flags & F_THREAD_ACTIVE) {
            pthread_join(output->render_thread, nullptr);
            output->render.flags &= ~F_THREAD_ACTIVE;
            if (output->event_fd >= 0)
                close(output->event_fd);
            if (output->async_result.standard.fd >= 0)
                close(output->async_result.standard.fd);
            if (output->async_result.glass.fd >= 0)
                close(output->async_result.glass.fd);
        }

        wl_list_remove(&output->link);
        for (size_t i = 0; i < output->num_items; i++)
            free(output->items[i].filename);
        free(output->items);
        free(output->name);
        free(output);
    }

    struct output_config *oc, *tmp_oc;
    wl_list_for_each_safe(oc, tmp_oc, &state.output_configs, link)
    {
        wl_list_remove(&oc->link);
        free_item_list(&oc->items);
        free(oc->output_name);
        free(oc);
    }

    if (state.inotify_fd >= 0)
        close(state.inotify_fd);
    free(state.config_path);
    free(state.config_dir);
    free(state.config_filename);

    gamemode_cleanup(&state);

    if (state.egl_initialized) {
        eglMakeCurrent(state.egl_display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        eglDestroyContext(state.egl_display, state.egl_context);
        eglTerminate(state.egl_display);
    }

    if (state.layer_shell)
        zwlr_layer_shell_v1_destroy(state.layer_shell);
    if (state.compositor)
        wl_compositor_destroy(state.compositor);
    if (state.registry)
        wl_registry_destroy(state.registry);
    if (state.display)
        wl_display_disconnect(state.display);

    vips_shutdown();
    return 0;
}
