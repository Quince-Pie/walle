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

#include <assert.h>
#include <dirent.h>
#include <fcntl.h>
#include <getopt.h>
#include <ini.h>
#include <liburing.h>
#include <poll.h>
#include <pthread.h>
#include <pwd.h>
#include <sched.h>
#include <stdatomic.h>
#include <strings.h>
#include <sys/eventfd.h>
#include <sys/inotify.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/signalfd.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/timerfd.h>
#include <sys/types.h>
#include <systemd/sd-bus.h>
#include <unistd.h>
#include <vips/vips.h>
#include <wayland-client.h>
#include <xxhash.h>

#include "parity/liquid_glass_reveal_mask_model.h"
#include "protocols/wlr-layer-shell-unstable-v1.h"
#include "shiro.h"
#include "vulkan_renderer.h"

#if defined(WALLE_TRACY)
#    include <tracy/tracy/TracyC.h>
#endif

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

static uint64_t trace_now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * UINT64_C(1'000'000'000) + (uint64_t)ts.tv_nsec;
}

/* Anything on the main thread that eats more than 50 ms is a presentation
 * stall the user can see; say so, with the culprit named. */
static void warn_slow(const char* what, const char* who, uint64_t start_ns)
{
    uint64_t took = trace_now_ns() - start_ns;
    if (took > UINT64_C(50'000'000))
        fprintf(stderr,
                "[SLOW] %s (%s) blocked the main thread for %.1f ms\n",
                what,
                who ? who : "-",
                (double)took / 1e6);
}

/* -- Constants ----------------------------------------------------------- */

#define WALLE_VERSION "0.0.1"

/* Apple's Glass exposes exactly three variants: `regular`, `clear`, and
 * `identity` ("your content remains unaffected as if no glass effect was
 * applied").  Identity therefore composes the reveal with no material at
 * all - the plain mask-weighted crossfade the hardware corpus measures. */
enum glass_variant : uint8_t
{
    GLASS_VARIANT_CLEAR = 0,
    GLASS_VARIANT_REGULAR,
    GLASS_VARIANT_IDENTITY
};

/* Liquid Glass preprocess (parity): the backdrop the material samples.
 *
 * MEASURED DIRECTLY on macOS 26.6.1 (25G76) from a step edge under a
 * full-frame Glass element, so one capture carries every spatial frequency at
 * once and both plateaus are real (analysis/derive_material_blur_kernel.py,
 * artifacts-bleed).  Each candidate kernel is convolved forward against the
 * true finite backdrop with its edges replicated, and compared to the measured
 * interior under a free gain and offset, so nothing is assumed to converge and
 * the material's own transfer cannot leak into the kernel.
 *
 * The kernels, in CAPTURE pixels at 2x backing scale:
 *
 *   clear    0.2174 * gauss(0.7251) + 0.7826 * gauss(4.1829)
 *   regular  w      * gauss(14.188)  + (1 - w) * gauss(329.807)
 *            w = 0.8846 light, 0.5164 dark
 *
 * and the mixture is DIFFERENT FOR CHROMA.  This is the one thing every gray
 * instrument in the corpus is blind to, and it hid for a long time behind
 * that.  A step edge and a sine grating carry no chroma at all, so they
 * measure the luma weight and nothing else - which is why they agree with each
 * other, with three element geometries, and with the numbers above.  The coded
 * field carries MORE chroma than luma, 36 code values against 29, and it reads
 * 0.54 where they read 0.90.  Splitting its residual by component settles it:
 * luma alone wants 0.85 and scores 1.15, chroma alone wants 0.55 and scores
 * 7.91 against the shipped weight.
 *
 *   regular light   wLuma 0.893  wChroma 0.543   8.07 -> 1.84 rms
 *   regular dark    wLuma 0.562  wChroma 0.617   2.62 -> 1.18
 *   clear  (both)   wLuma 0.217  wChroma 0.083   0.58 -> 0.58
 *
 * The fitted LUMA weight is the shipped weight - to three decimals for `clear`
 * and to 0.008 for `regular` in light - so the gray measurements were right
 * about what they could see.  `clear` is the control and behaves like one: its
 * radii are 0.73 and 4.18 px, so there is almost nothing to tell apart.
 *
 * fitting to 0.12 rms / 1.2 max code values (clear) and 0.35 / 1.8 light,
 * 0.72 / 2.6 dark (regular).  The blur happens in sRGB CODE space: fitting
 * clear in linear light instead costs 1.68 rms against 0.12, and the material
 * colour matrices were already measured in that same space.
 *
 * clear's NARROW layer is a Gaussian and not a copy of the source.  It used to
 * ship as a copy - a delta - and that is the one place a step edge could tell:
 * a hard edge splits between the two pixels that straddle it, and a delta puts
 * 14 code values on the wrong side of the split.  The derivation had always
 * said otherwise (bilinear reconstruction over a 1.66 px cell, or the same
 * curve as a 0.725 px Gaussian, both at 0.12 rms against a delta's 1.39), and
 * the 500 px circle's own step edge - a second, independent capture - refits it
 * to 0.733.  Correcting it takes that edge from 14.05 to 2.80 code values.
 *
 * Three things this corrects, all confirmed end to end by rendering walle over
 * the same step and measuring it the same way:
 *
 *   * clear was roughly four times too blurry - 38 code values wrong 5 px from
 *     the edge - because the level selector clamped its radius to 8 px and
 *     picked the same pyramid level as regular, discarding the 3.4:1 ratio
 *     between them that the sine gratings had already measured;
 *   * regular has a SECOND, very wide layer that nothing modelled - Apple's own
 *     transition inputs give it a BLEED stage with a 160-unit blur radius that
 *     clear does not have.  It carries 12% of the light material and 48% of the
 *     dark one, and leaving it out cost 11 code values in dark;
 *   * clear has no wide layer at all, which is what the fitted weight of zero
 *     and its flat far field both say independently.
 *
 * This supersedes the pyramid-level approximation: the AGX2 mip cascade was a
 * guess at the mechanism, and the mechanism is now measured.  It also
 * supersedes the 26.4 figure (sigma ~ 0.032 * window diagonal, 93 px at
 * 2048x2048), which was seven to thirty-four times too much blur for this
 * build.  The radii are ABSOLUTE, not a fraction of the window. */
constexpr double GLASS_CAPTURE_SCALE = 2.0;
/* Capture pixels; divided by GLASS_CAPTURE_SCALE to reach wallpaper points. */
constexpr double GLASS_BLUR_CLEAR_NARROW_WEIGHT    = 0.2174;
constexpr double GLASS_BLUR_CLEAR_NARROW_SIGMA     = 0.7251;
constexpr double GLASS_BLUR_CLEAR_WIDE_SIGMA       = 4.1829;
constexpr double GLASS_BLUR_REGULAR_SIGMA          = 14.188;
constexpr double GLASS_BLUR_REGULAR_WIDE_SIGMA     = 329.807;
constexpr double GLASS_BLUR_REGULAR_WEIGHT_LIGHT   = 0.8846;
constexpr double GLASS_BLUR_REGULAR_WEIGHT_DARK    = 0.5164;
/* The chroma mixture, from the one backdrop that carries chroma. */
constexpr double GLASS_BLUR_CLEAR_CHROMA_WEIGHT    = 0.0880;
constexpr double GLASS_BLUR_REGULAR_CHROMA_LIGHT   = 0.5420;
constexpr double GLASS_BLUR_REGULAR_CHROMA_DARK    = 0.6120;

/* The three weights and two radii the composite blur needs, resolved for one
 * variant, appearance and output scale. */
struct glass_blur_recipe
{
    double sharp_weight;
    double narrow_weight;
    double narrow_sigma;
    double wide_weight;
    double wide_sigma;
    /* The same two radii, mixed differently for the backdrop's CHROMA - see
     * the note above.  Equal to narrow_weight leaves the blur colour-blind,
     * which is what it used to be. */
    double narrow_chroma_weight;
};

[[nodiscard]]
static struct glass_blur_recipe glass_blur_for(enum glass_variant variant,
                                               float              lightness,
                                               int32_t            scale)
{
    double points = (scale > 0 ? (double)scale : 1.0) / GLASS_CAPTURE_SCALE;
    if (variant == GLASS_VARIANT_REGULAR) {
        double narrow = GLASS_BLUR_REGULAR_WEIGHT_DARK
                        + ((double)lightness
                           * (GLASS_BLUR_REGULAR_WEIGHT_LIGHT
                              - GLASS_BLUR_REGULAR_WEIGHT_DARK));
        double chroma = GLASS_BLUR_REGULAR_CHROMA_DARK
                        + ((double)lightness
                           * (GLASS_BLUR_REGULAR_CHROMA_LIGHT
                              - GLASS_BLUR_REGULAR_CHROMA_DARK));
        return (struct glass_blur_recipe){
            .sharp_weight        = 0.0,
            .narrow_weight       = narrow,
            .narrow_chroma_weight = chroma,
            .narrow_sigma  = GLASS_BLUR_REGULAR_SIGMA * points,
            .wide_weight   = 1.0 - narrow,
            .wide_sigma    = GLASS_BLUR_REGULAR_WIDE_SIGMA * points,
        };
    }
    return (struct glass_blur_recipe){
        .sharp_weight         = 0.0,
        .narrow_weight        = GLASS_BLUR_CLEAR_NARROW_WEIGHT,
        .narrow_chroma_weight = GLASS_BLUR_CLEAR_CHROMA_WEIGHT,
        .narrow_sigma  = GLASS_BLUR_CLEAR_NARROW_SIGMA * points,
        .wide_weight   = 1.0 - GLASS_BLUR_CLEAR_NARROW_WEIGHT,
        .wide_sigma    = GLASS_BLUR_CLEAR_WIDE_SIGMA * points,
    };
}

/* The glass layer is baked on the GPU at upload time (six fragment passes,
 * ~15 ms) unless WALLE_GLASS_BAKE=cpu selects the vips replay path - the A/B
 * referee for the GPU implementation, judged by the measured-law score gates
 * rather than byte identity. */
static int glass_bake_on_gpu(void)
{
    static int cached = -1;
    if (cached < 0) {
        const char* mode = getenv("WALLE_GLASS_BAKE");
        cached           = mode == nullptr || strcmp(mode, "cpu") != 0;
    }
    return cached;
}

/* Bump whenever the cached pixel pipeline changes shape (layout, band count,
 * preprocess constants). Hashed into every cache key. */
constexpr uint32_t CACHE_SCHEMA_VERSION = 10;

constexpr float DEFAULT_TRANSITION_DUR = 0.6f;
constexpr int   INOTIFY_BUF_LEN        = 4096;

constexpr uint32_t  REVEAL_PROCESS_CAPTURE_WIDTH       = 2048;
constexpr uint32_t  REVEAL_PROCESS_CAPTURE_HEIGHT      = 2048;
constexpr uint32_t  REVEAL_PROCESS_CAPTURE_STATE_COUNT = 65;
static const double REVEAL_PROCESS_CAPTURE_CENTER_X    = 512.0;
static const double REVEAL_PROCESS_CAPTURE_CENTER_Y    = 614.4;
constexpr double    REVEAL_RADIUS_MARGIN               = 1.03;

/* Apple's ANIMATED reveal runs ahead of its own state parameter.
 *
 * Measured on the authorized M1 (MacBookPro18,2, macOS 26.6.1 build 25G76,
 * Retina at backing scale 2) with the rig's `wallpaper-transition` mode.  The
 * two measurements disagree, and the disagreement IS the finding: read against
 * the exact-state sweeps the radius is maximum_radius * state to under 4 px
 * over sixteen states, so the radius law here is right; read against the
 * presentation clock the same radius covers the frame at 0.636 rather than at
 * 1.  What is wrong is only the mapping from the clock to the state.
 *
 * Fitted over the two `clear` sequences - 71 frames before the radius saturates,
 * and `clear` because it casts no shadow to inflate the radius read:
 *
 *     state(t) = 1.610 * (t - 0.019)        13.3 px rms
 *     state(t) = min(1, 1.624 * t^1.068)    18.7 px rms
 *     state(t) = 1.537 * t                  33.2 px rms
 *     state(t) = t                         422.7 px rms   (what this replaces)
 *
 * The form is not chosen by rms alone.  The bare scale is BIASED - its residual
 * runs +59, +38, +18, +5, -27 px across the timeline - while the shifted line's
 * wanders between -5 and +6 with no trend, which is what a correct model looks
 * like.  And the shift is physical: 0.019 of a one-second animation is 1.2
 * frames at the rig's 61 Hz, i.e. the reveal starts on the frame after the
 * clock does.  So the radius is LINEAR in time after one frame of latency, at
 * 1.61x the rate that would fill the timeline, covering the frame at 0.640.
 *
 * Chosen by end-to-end score, not by fit: over all 242 animated frames the
 * shifted line reads 2.31 full-frame code values and 4.62 in the interior
 * against the power law's 2.38 and 6.65, better on all four sequences.
 *
 * LIVE PATH ONLY.  The process capture is indexed BY state against the retained
 * 65-state corpus, so easing it there would move the very ladder the reveal
 * gate scores. */
constexpr float REVEAL_CLOCK_RATE  = 1.610f;
constexpr float REVEAL_CLOCK_DELAY = 0.019f;

static float reveal_state_from_clock(float clock)
{
    float state = REVEAL_CLOCK_RATE * (clock - REVEAL_CLOCK_DELAY);
    if (!(state > 0.0f))
        return 0.0f;
    return state > 1.0f ? 1.0f : state;
}

constexpr size_t   CACHE_HIGH_WATERMARK    = 512UL * 1024UL * 1024UL;
constexpr size_t   CACHE_LOW_WATERMARK     = 384UL * 1024UL * 1024UL;
constexpr int      CACHE_STARTUP_YIELD_SEC = 10;
constexpr uint32_t GC_RENDER_PERIOD        = 64; /* re-run cache GC every N uploads */

#ifndef MFD_CLOEXEC
#    define MFD_CLOEXEC 0x0001U
#endif
#ifndef SCHED_IDLE
#    define SCHED_IDLE 5
#endif

static XoshiroState g_rng = {};

/* -c/--config override; consulted by get_config_path(). */
static const char* g_config_override = nullptr;

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
    T_STATE_ARMED,
    T_STATE_RUNNING
};

/* HIG "Materials": Liquid Glass "provides two variants — regular and clear".
 * Clear is the zero value: a wallpaper is exactly the "media background"
 * the HIG prescribes the clear variant for ("components that float above
 * media backgrounds — such as photos and videos"). */
/* Apple's material takes the system appearance as an input; AUTO keeps
 * walle's content-luminance stand-in for desktops that expose none. */
enum glass_appearance : uint8_t
{
    GLASS_APPEARANCE_AUTO = 0,
    GLASS_APPEARANCE_DARK,
    GLASS_APPEARANCE_LIGHT
};

typedef enum : uint8_t
{
    F_CONFIGURED    = 1 << 0,
    F_RENDERER_INIT = 1 << 1,
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
    struct wl_list     link;
    char*              output_name;
    struct item_list   items;
    int                timeout;
    bool               randomize;
    bool               transition_on;
    bool               gamemode;
    enum glass_variant    variant;
    enum glass_appearance appearance;
    /* Apple's Glass.tint(Color?): negative red means untinted. */
    float                 tint[3];
    float                 transition_duration;
};

struct config_parse_ctx
{
    struct wl_list* config_list;
    char**          renderer_device_selector;
};

struct image_layer
{
    size_t  offset; /* byte offset within the shared buffer fd */
    size_t  size;
    int32_t width, height;
};

/* Pixels are always tightly packed RGBA8 (sRGB): a single band count keeps
 * texture storage, PBO sizing, and row alignment uniform. */
struct render_result
{
    /* Split cache entries: the standard layer is variant-independent, the
     * glass layer keys on variant/recipe/blur-space.  For identity - which
     * never samples the glass - glass_fd aliases std_fd, so a variant flip
     * to or from identity costs no bake at all.  glass_fd == std_fd must be
     * closed exactly once. */
    int                std_fd;   /* -1 = none */
    int                glass_fd; /* may equal std_fd; -1 = none */
    uint64_t           done_ns;  /* CLOCK_MONOTONIC at worker completion */
    bool               success;
    struct image_layer standard;
    struct image_layer glass;
};

struct wallpaper_state;

struct wallpaper_output
{
    /* Deliberately NOT alignas(64): a type-level alignment requirement makes
     * the wl_list_for_each head-sentinel container_of computation formally
     * UB (UBSan: "misaligned address for type"). Cache-line placement is
     * instead provided best-effort by the aligned_alloc(64) in
     * registry_global. */
    struct
    {
        struct wallpaper_state* state;
        struct walle_vk_output* vk_output;
        uint64_t                anim_start_ns;

        int32_t width;
        int32_t height;
        float   duration_inv; /* 1.0/duration, NOT duration. MUL is faster than DIV per-frame. */

        enum transition_state t_state;
        output_flags_t        flags;
        uint8_t               _pad[26];
    } render;

    float     t_center_x;
    float     t_center_y;
    float     transition_duration;
    int       event_fd;
    int       timer_fd;
    int       slot_event; /* ev_core slot indices; -1 = none */
    int       slot_timer;
    int32_t   scale;      /* wl_output.scale; buffer px = logical px * scale */
    int32_t   logical_w;
    int32_t   logical_h;
    pthread_t render_thread;

    struct render_result async_result;
    struct render_result current_source;
    struct render_result pending_source;
    struct wl_callback*  frame_callback;

    struct wl_list                link;
    struct wl_output*             wl_output;
    uint32_t                      wl_output_name;
    char*                         name;
    struct wl_surface*            surface;
    struct zwlr_layer_surface_v1* layer_surface;

    struct wallpaper_item* items;
    size_t                 num_items;
    size_t                 current_item_index;
    int                    timeout;
    bool                   gamemode_enabled;
    enum glass_variant     glass_variant;
    enum glass_appearance  glass_appearance;
    float                  glass_tint[3];

    bool pending_reload;

    /* Snapshot taken on the main thread immediately before pthread_create
     * (the create is the happens-before edge); the worker must never read
     * render.width/height, which a configure event can mutate mid-render. */
    int32_t            job_w;
    int32_t            job_h;
    enum glass_variant job_variant;
    /* The backdrop blur is baked on the CPU, and its blend weights depend on
     * the appearance and the output scale, so the worker needs both as of the
     * moment the job started rather than whatever they become mid-render. */
    float              job_lightness;
    int32_t            job_scale;

    double reveal_maximum_radius;

    /* Same-trigger transition sync (see the transition_sync_* helpers).
     * Trigger/finalize-time only - never read per frame, so it lives with
     * the cold state: the generation this output was recruited into, whether
     * it still counts toward that group's pending renders, and whether it is
     * armed but withholding its first frame until the group's last render
     * lands. */
    uint32_t sync_gen;
    bool     sync_member;
    bool     sync_held;
    bool     sync_start_ready;

    /* Cold, diagnostic-only state. Keep this outside the frozen render
     * prefix so an absent --reveal-mask-process-capture is layout-neutral. */
    uint8_t* reveal_process_capture_pixels;
    uint8_t* reveal_process_composition_pixels;
    uint32_t reveal_process_capture_state;
    bool     reveal_process_capture_owned;
    bool     reveal_process_capture_active;
};

static_assert(sizeof(((struct wallpaper_output*)0)->render) == 64,
              "Render struct must be exactly 64 bytes (1 cache line)");
static_assert(offsetof(struct wallpaper_output, render) == 0,
              "Render struct must be at offset 0 for cache alignment");

/* -- io_uring Event Core: Types ------------------------------------------ */

/*
 * Every event source is a ONESHOT IORING_OP_POLL_ADD re-armed by a
 * declarative reconciler at the top of each loop turn. Oneshot poll is
 * level-triggered at every arm, so partially drained fds (the Wayland
 * socket above all) can never strand the loop. Multishot poll must NOT be
 * introduced here without a full-drain proof: per io_uring_enter(2),
 * multishot completions after the first are edge-triggered, and
 * wl_display_read_events() performs a single bounded recvmsg with no
 * drain-to-EAGAIN guarantee — leftover bytes plus edge semantics equals a
 * permanent freeze.
 *
 * user_data packs { slot index | generation << 32 | kind << 48 } and never a
 * pointer, so a stale CQE (op canceled, slot reused, output destroyed) is
 * dropped by a generation compare before any state is touched.
 *
 * Kernel matrix: 6.1+ runs SINGLE_ISSUER|DEFER_TASKRUN|SUBMIT_ALL; 6.0 drops
 * DEFER_TASKRUN; 5.19 drops SINGLE_ISSUER; 5.15-5.18 runs a plain ring with
 * the async-cancel fallback — semantics identical throughout. Below 5.15 (or
 * with io_uring disabled) startup fails with a clear diagnostic. Features are
 * probed by init/register results, never by uname sniffing.
 */

enum ev_kind : uint8_t
{
    EV_WL_IN = 0, /* fixed slots 0..4 */
    EV_WL_OUT,
    EV_INOTIFY,
    EV_DBUS,
    EV_SIGNAL,
    EV_FIXED_COUNT,
    EV_TIMER,       /* dynamic, per output */
    EV_RENDER_DONE, /* dynamic, per output */
    EV_CANCEL       /* async-cancel ops themselves; always ignored */
};

struct ev_slot
{
    int                      fd;        /* -1 = slot free */
    uint32_t                 want_mask; /* poll mask to keep armed; 0 = idle */
    uint32_t                 armed_mask;
    struct wallpaper_output* owner;
    enum ev_kind             kind;
    bool                     pending; /* SQE in flight, CQE not yet consumed */
    bool                     zombie;  /* async-canceled; awaiting stale CQE before reuse */
    uint16_t                 gen;
};

/* Slot storage grows in fixed-size chunks with STABLE addresses — never
 * realloc: a moving buffer would invalidate every held ev_slot* and is the
 * kind of latent use-after-free no generation tag can catch. 64 chunks of 32
 * slots = 2048 slots = 1000+ outputs; exhaustion is loud, not silent. */
constexpr size_t EV_SLOT_CHUNK_SZ = 32;
constexpr size_t EV_SLOT_CHUNKS   = 64;

struct ev_core
{
    struct io_uring ring;
    struct ev_slot* chunks[EV_SLOT_CHUNKS];
    size_t          n_slots;
    bool            ring_ok;
    bool            sync_cancel_ok;
    pid_t           owner_tid; /* SINGLE_ISSUER discipline assert */
};

struct wallpaper_state
{
    struct wl_display*          display;
    struct wl_registry*         registry;
    struct wl_compositor*       compositor;
    struct zwlr_layer_shell_v1* layer_shell;
    struct wl_shm*              shm;

    struct wl_list outputs;
    struct wl_list output_configs;

    struct walle_vk_renderer* vk_renderer;
    uint32_t                  vk_max_image_dimension;
    bool                      globals_ready;

    bool     reveal_process_capture;
    /* --reveal-mask-process-capture-progress: capture one state at this exact
     * progress instead of the 65-state k/64 ladder, so hardware samples that
     * fall between ladder states can be scored. */
    bool     reveal_process_capture_single;
    /* Explicit progress ladder (comma-separated on the command line): one
     * captured state per value, in order. */
    float*   reveal_process_capture_progress_values;
    uint32_t reveal_process_capture_progress_count;
    /* argv-owned; echoed verbatim so the marker states the exact request. */
    const char* reveal_process_capture_progress_text;
    /* --reveal-mask-process-capture-presentation: capture with the ANIMATING
     * geometry rather than the rounded one, so the live path can be scored
     * against the hardware's live frames.  The default is off, because the
     * 65-state ladder is the rounded path and is byte-exact BECAUSE of it. */
    bool     reveal_process_capture_presentation;
    /* --reveal-mask-process-capture-material-progress: drive the MATERIAL's
     * clock from a separate value, leaving the geometry on the reveal
     * progress.  One number normally does both, which ties the element's
     * radius to how thick the material is, and the capture rig's own elements
     * are fully materialized at radii the tied mapping cannot reach.  Negative
     * means tied, which is the default and what every gate uses. */
    float    reveal_process_capture_material_progress;
    /* A comma-separated LIST is accepted too, one value per captured state, so
     * a run can hold the geometry on one ladder and the material on another.
     * That is what scoring walle against Apple's animated wallpaper transition
     * needs: the geometry follows the measured clock-to-state mapping while the
     * material stays on the raw clock its own law was fitted against.  Null
     * means the single value above applies. */
    float*   reveal_process_capture_material_values;
    uint32_t reveal_process_capture_material_count;
    /* --reveal-mask-process-capture-backing-scale: how many DEVICE PIXELS the
     * capture has per point.  The capture is always 2048x2048 device pixels;
     * this says what those pixels mean, and the material's radii are absolute
     * in device pixels at a 2x backing scale - which is the scale the whole
     * corpus was captured at.  Default 1, which is what every gate uses. */
    int32_t  reveal_process_capture_backing_scale;
    bool     reveal_process_capture_output_claimed;
    bool     reveal_process_capture_complete;
    int      reveal_process_capture_status;
    int      reveal_process_capture_directory_fd;
    uint32_t reveal_process_capture_swap_count;
    uint32_t reveal_process_capture_callback_count;

    int   inotify_fd;
    int   config_wd;
    char* config_path;
    char* config_dir;
    char* config_filename;

    sd_bus*      bus;
    /* Desktop appearance preference, refreshed with the bus connection. */
    enum glass_appearance portal_appearance;
    sd_bus_slot* gamemode_slot;
    bool         gamemode_active;

    struct ev_core ev;
    int            signal_fd;
    bool           shutting_down;
    uint32_t       renders_since_gc;

    /* Same-trigger transition sync: current group generation, how many of its
     * renders are still in flight, and whether this loop turn already opened
     * a group (all triggers in one turn share one group). */
    uint32_t transition_sync_gen;
    int      transition_sync_pending;
    bool     transition_sync_turn_open;
    bool     transition_sync_starts_ready;
};

static void initialize_output(struct wallpaper_output* output);
static void apply_config_to_output(struct wallpaper_output* output, struct output_config* config);
static void update_wallpaper(struct wallpaper_output* output);
static void launch_async_render(struct wallpaper_output* output);
static struct output_config* get_config_for_output(struct wallpaper_state* state, const char* name);
static void                  launch_cache_maintenance_service(void);

static void release_render_result(struct render_result* result)
{
    if (result->glass_fd >= 0 && result->glass_fd != result->std_fd)
        close(result->glass_fd);
    if (result->std_fd >= 0)
        close(result->std_fd);
    *result = (struct render_result){.std_fd = -1, .glass_fd = -1};
}

/* Applied when an output's section disappears on hot reload: empties the
 * item list, disarms rotation, keeps the last frame on screen. */
static struct output_config g_frozen_config
    = {.transition_duration = DEFAULT_TRANSITION_DUR, .gamemode = true};

/* -- io_uring Event Core: Implementation ---------------------------------- */

static inline uint64_t ev_pack(uint32_t idx, uint16_t gen, enum ev_kind kind)
{
    return (uint64_t)idx | ((uint64_t)gen << 32) | ((uint64_t)kind << 48);
}

static inline uint32_t ev_ud_idx(uint64_t ud)
{
    return (uint32_t)ud;
}

static inline uint16_t ev_ud_gen(uint64_t ud)
{
    return (uint16_t)(ud >> 32);
}

#if defined(NDEBUG)
#    define EV_ASSERT_OWNER(core) ((void)0)
#else
#    define EV_ASSERT_OWNER(core) assert((core)->owner_tid == gettid())
#endif

static inline struct ev_slot* ev_slot_at(struct ev_core* core, size_t idx)
{
    return &core->chunks[idx / EV_SLOT_CHUNK_SZ][idx % EV_SLOT_CHUNK_SZ];
}

/* Allocate (calloc) one more chunk of slots, all marked free. */
[[nodiscard]]
static bool ev_add_chunk(struct ev_core* core, size_t chunk)
{
    if (chunk >= EV_SLOT_CHUNKS)
        return false;
    struct ev_slot* c = calloc(EV_SLOT_CHUNK_SZ, sizeof(struct ev_slot));
    if (!c)
        return false;
    for (size_t i = 0; i < EV_SLOT_CHUNK_SZ; i++)
        c[i].fd = -1;
    core->chunks[chunk] = c;
    return true;
}

[[nodiscard]]
static bool ev_init(struct ev_core* core)
{
    *core           = (struct ev_core){};
    core->owner_tid = gettid();

    /* Flag ladder, retried on -EINVAL: 6.1+ / 6.0 / 5.19 / 5.15. */
    static const unsigned LADDER[] = {
        IORING_SETUP_SINGLE_ISSUER | IORING_SETUP_DEFER_TASKRUN | IORING_SETUP_SUBMIT_ALL,
        IORING_SETUP_SINGLE_ISSUER | IORING_SETUP_COOP_TASKRUN | IORING_SETUP_SUBMIT_ALL,
        IORING_SETUP_COOP_TASKRUN,
        0,
    };

    int                    r = -EINVAL;
    struct io_uring_params p;
    for (size_t i = 0; i < sizeof LADDER / sizeof *LADDER; i++) {
        memset(&p, 0, sizeof(p));
        p.flags      = LADDER[i] | IORING_SETUP_CQSIZE;
        p.cq_entries = 256;
        r            = io_uring_queue_init_params(64, &core->ring, &p);
        if (r != -EINVAL)
            break;
    }
    if (r < 0) {
        const char* why = "io_uring_queue_init failed";
        if (r == -ENOSYS)
            why = "kernel lacks io_uring";
        else if (r == -EPERM)
            why = "io_uring disabled (kernel.io_uring_disabled sysctl (6.6+) or seccomp)";
        else if (r == -EINVAL)
            why = "kernel too old for io_uring (< 5.5)";
        fprintf(stderr, "[FATAL] %s: %s\n", why, strerror(-r));
        return false;
    }
    if (!(p.features & IORING_FEAT_NODROP) || !(p.features & IORING_FEAT_EXT_ARG)) {
        fprintf(stderr, "[FATAL] io_uring lacks NODROP/EXT_ARG; kernel >= 5.15 required.\n");
        io_uring_queue_exit(&core->ring);
        return false;
    }
    (void)io_uring_register_ring_fd(&core->ring); /* best effort */

    /* Probe IORING_REGISTER_SYNC_CANCEL (kernel 6.0): a guaranteed-no-match
     * cancel returns -ENOENT when supported, -EINVAL when not. */
    struct io_uring_sync_cancel_reg probe = {.addr = ~0ULL, .fd = -1};
    core->sync_cancel_ok = (io_uring_register_sync_cancel(&core->ring, &probe) != -EINVAL);

    if (!ev_add_chunk(core, 0)) {
        io_uring_queue_exit(&core->ring);
        return false;
    }
    core->n_slots = EV_FIXED_COUNT;
    for (size_t i = 0; i < EV_FIXED_COUNT; i++)
        ev_slot_at(core, i)->kind = (enum ev_kind)i;
    core->ring_ok = true;
    return true;
}

static struct io_uring_sqe* ev_get_sqe(struct ev_core* core)
{
    struct io_uring_sqe* sqe = io_uring_get_sqe(&core->ring);
    if (!sqe) {
        (void)io_uring_submit(&core->ring);
        sqe = io_uring_get_sqe(&core->ring);
    }
    return sqe;
}

[[nodiscard]]
static int ev_slot_alloc(
    struct ev_core* core, enum ev_kind kind, int fd, uint32_t mask, struct wallpaper_output* owner)
{
    EV_ASSERT_OWNER(core);
    size_t idx = SIZE_MAX;
    for (size_t i = EV_FIXED_COUNT; i < core->n_slots; i++) {
        struct ev_slot* c = ev_slot_at(core, i);
        if (c->fd < 0 && !c->pending && !c->zombie) {
            idx = i;
            break;
        }
    }
    if (idx == SIZE_MAX) {
        size_t chunk = core->n_slots / EV_SLOT_CHUNK_SZ;
        if (core->n_slots % EV_SLOT_CHUNK_SZ == 0 && !core->chunks[chunk]
            && !ev_add_chunk(core, chunk)) {
            fprintf(stderr, "[EV] slot pool exhausted (%zu slots)\n", core->n_slots);
            return -1;
        }
        idx = core->n_slots++;
    }
    struct ev_slot* s        = ev_slot_at(core, idx);
    uint16_t        keep_gen = s->gen;
    *s                       = (struct ev_slot){
                              .fd = fd, .want_mask = mask, .owner = owner, .kind = kind, .gen = keep_gen};
    return (int)idx;
}

/* Invalidate the slot's in-flight op (if any). After return, no CQE for the
 * old op can reach a handler: the generation bump makes it inert. On the
 * sync-cancel path (kernel 6.0+) the op is fully quiesced on return; on the
 * fallback path the slot stays a zombie (unreusable) until the stale CQE is
 * consumed by the dispatcher. */
static void ev_slot_cancel(struct ev_core* core, size_t idx)
{
    EV_ASSERT_OWNER(core);
    struct ev_slot* s  = ev_slot_at(core, idx);
    uint64_t        ud = ev_pack((uint32_t)idx, s->gen, s->kind);
    s->gen++;
    s->want_mask = 0;
    if (!s->pending)
        return;

    if (core->sync_cancel_ok) {
        struct io_uring_sync_cancel_reg reg
            = {.addr = ud, .fd = -1, .timeout = {.tv_sec = 1, .tv_nsec = 0}};
        int r = io_uring_register_sync_cancel(&core->ring, &reg);
        if (r == -ETIME) {
            fprintf(stderr, "[EV] sync cancel timed out (slot %zu); retrying\n", idx);
            r = io_uring_register_sync_cancel(&core->ring, &reg);
        }
        /* 0 / -ENOENT / -EALREADY all converge: the op is done or its CQE is
         * already queued, and the gen bump made that CQE inert. */
        (void)r;
        s->pending = false;
    } else {
        struct io_uring_sqe* sqe = ev_get_sqe(core);
        if (sqe) {
            io_uring_prep_cancel64(sqe, ud, 0);
            io_uring_sqe_set_data64(sqe, ev_pack(UINT32_MAX, 0, EV_CANCEL));
            (void)io_uring_submit(&core->ring);
        }
        s->zombie = true;
    }
}

/* Cancel + close + free. The output-side fd mirror must be reset by the
 * caller; slots own their fds. */
static void ev_slot_release(struct ev_core* core, size_t idx)
{
    struct ev_slot* s = ev_slot_at(core, idx);
    if (s->fd < 0 && !s->pending)
        return;
    ev_slot_cancel(core, idx);
    if (s->fd >= 0)
        close(s->fd);
    s->fd        = -1;
    s->owner     = nullptr;
    s->want_mask = 0;
}

/* Declarative re-arm: handlers only clear `pending`; nothing arms an SQE
 * outside this function. */
static void ev_reconcile(struct ev_core* core)
{
    EV_ASSERT_OWNER(core);
    for (size_t i = 0; i < core->n_slots; i++) {
        struct ev_slot* s = ev_slot_at(core, i);
        if (s->fd < 0 || s->pending || !s->want_mask)
            continue;
        struct io_uring_sqe* sqe = ev_get_sqe(core);
        if (!sqe) {
            fprintf(stderr, "[EV] SQ exhausted; slot %zu deferred one turn\n", i);
            continue;
        }
        io_uring_prep_poll_add(sqe, s->fd, s->want_mask);
        io_uring_sqe_set_data64(sqe, ev_pack((uint32_t)i, s->gen, s->kind));
        s->pending    = true;
        s->armed_mask = s->want_mask;
    }
}

static void ev_exit(struct ev_core* core)
{
    if (core->ring_ok)
        io_uring_queue_exit(&core->ring);
    core->ring_ok = false;
    for (size_t i = 0; i < EV_SLOT_CHUNKS; i++) {
        free(core->chunks[i]);
        core->chunks[i] = nullptr;
    }
}

/* -- Path Expansion ------------------------------------------------------ */

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
    time_t mtime; /* LRU key: bumped via futimens() on every cache hit */
    off_t  size;
};

static _Atomic bool g_gc_running = false;

static int compare_cache_entries(const void* a, const void* b)
{
    auto ea = (const struct cache_maintenance_entry*)a;
    auto eb = (const struct cache_maintenance_entry*)b;
    return (ea->mtime < eb->mtime) ? -1 : (ea->mtime > eb->mtime);
}

/* $XDG_CACHE_HOME/walle (or ~/.cache/walle), created if missing. Never
 * passes an unset HOME to a format string; falls back to the passwd db. */
[[nodiscard]]
static char* resolve_cache_dir(void)
{
    char        dir[PATH_MAX];
    const char* xdg = getenv("XDG_CACHE_HOME");

    if (xdg && *xdg) {
        if (snprintf(dir, sizeof(dir), "%s/walle", xdg) >= (int)sizeof(dir))
            return nullptr;
        mkdir(dir, 0700);
    } else {
        const char*   home   = getenv("HOME");
        char*         pw_buf = nullptr;
        struct passwd pwd;
        if (!home || !*home) {
            if (get_passwd_buffered(getuid(), nullptr, &pwd, &pw_buf) == 0)
                home = pwd.pw_dir;
        }
        if (!home || !*home) {
            free(pw_buf);
            return nullptr;
        }
        char parent[PATH_MAX];
        if (snprintf(parent, sizeof(parent), "%s/.cache", home) >= (int)sizeof(parent)
            || snprintf(dir, sizeof(dir), "%s/.cache/walle", home) >= (int)sizeof(dir)) {
            free(pw_buf);
            return nullptr;
        }
        free(pw_buf);
        mkdir(parent, 0700);
        mkdir(dir, 0700);
    }

    struct stat st;
    if (stat(dir, &st) == -1 || !S_ISDIR(st.st_mode)) {
        fprintf(stderr, "[CACHE] Unusable cache dir '%s'\n", dir);
        return nullptr;
    }
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
        atomic_store(&g_gc_running, false);
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
                entries[count].mtime = st.st_mtime;
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
    atomic_store(&g_gc_running, false);
    return nullptr;
}

/* Idempotent and re-runnable: called at startup and every GC_RENDER_PERIOD
 * uploads, so a long-lived daemon keeps honoring the watermark. */
static void launch_cache_maintenance_service(void)
{
    bool expected = false;
    if (!atomic_compare_exchange_strong(&g_gc_running, &expected, true))
        return;
    char* dir = resolve_cache_dir();
    if (!dir) {
        atomic_store(&g_gc_running, false);
        return;
    }
    pthread_t th;
    if (pthread_create(&th, nullptr, cache_maintenance_worker, dir) == 0) {
        pthread_detach(th);
    } else {
        free(dir);
        atomic_store(&g_gc_running, false);
    }
}

/* -- GameMode D-Bus ------------------------------------------------------ */

constexpr char GAMEMODE_BUS_NAME[]    = "org.freedesktop.portal.Desktop";
constexpr char GAMEMODE_PATH[]        = "/org/freedesktop/portal/desktop";
constexpr char GAMEMODE_INTERFACE[]   = "org.freedesktop.portal.GameMode";
constexpr char GAMEMODE_PROPERTY[]    = "Active";
constexpr char DBUS_PROPS_INTERFACE[] = "org.freedesktop.DBus.Properties";

/* Arm on a whole-second absolute grid so outputs sharing a rotation period
 * expire in a single wakeup (documented <1 s first-fire shift). Disarm zeroes
 * the timer entirely: zero wakeups while GameMode is active. */
static void arm_rotation_timer(struct wallpaper_output* o, bool disarm)
{
    if (o->timer_fd < 0)
        return;
    struct itimerspec ts    = {};
    int               flags = 0;
    if (!disarm && o->timeout > 0) {
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        ts.it_value.tv_sec    = now.tv_sec + o->timeout + (now.tv_nsec > 0 ? 1 : 0);
        ts.it_interval.tv_sec = o->timeout;
        flags                 = TFD_TIMER_ABSTIME;
    }
    if (timerfd_settime(o->timer_fd, flags, &ts, nullptr) < 0) {
        fprintf(stderr, "[ERROR] timerfd_settime for %s: %s\n", o->name, strerror(errno));
    }
}

static void toggle_gamemode_timers(struct wallpaper_state* state, bool active)
{
    struct wallpaper_output* o;
    wl_list_for_each(o, &state->outputs, link)
    {
        if ((o->render.flags & F_DEAD) || o->timer_fd < 0 || !o->gamemode_enabled)
            continue;
        arm_rotation_timer(o, active);
        dbg_print(
            "[GAMEMODE] Output '%s': %s", o->name, active ? "DISARMED (Zero-Wakeup)" : "ARMED");
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

/*
 * xdg-desktop-portal Settings: org.freedesktop.appearance color-scheme is the
 * desktop's equivalent of the macOS system appearance that Apple's material
 * reads from the environment.  0 = no preference, 1 = prefer dark,
 * 2 = prefer light.  Returns AUTO when the portal is absent or says nothing,
 * so walle falls back to the content-luminance stand-in.
 */
[[nodiscard]]
static enum glass_appearance portal_color_scheme(sd_bus* bus)
{
    if (bus == nullptr)
        return GLASS_APPEARANCE_AUTO;
    sd_bus_error   error   = SD_BUS_ERROR_NULL;
    sd_bus_message* reply  = nullptr;
    enum glass_appearance result = GLASS_APPEARANCE_AUTO;
    if (sd_bus_call_method(bus,
                           "org.freedesktop.portal.Desktop",
                           "/org/freedesktop/portal/desktop",
                           "org.freedesktop.portal.Settings",
                           "ReadOne",
                           &error,
                           &reply,
                           "ss",
                           "org.freedesktop.appearance",
                           "color-scheme")
        >= 0) {
        uint32_t scheme = 0;
        if (sd_bus_message_enter_container(reply, 'v', "u") >= 0
            && sd_bus_message_read(reply, "u", &scheme) >= 0) {
            if (scheme == 1)
                result = GLASS_APPEARANCE_DARK;
            else if (scheme == 2)
                result = GLASS_APPEARANCE_LIGHT;
        }
    }
    sd_bus_error_free(&error);
    if (reply)
        sd_bus_message_unref(reply);
    return result;
}

[[nodiscard]]
static bool gamemode_init(struct wallpaper_state* state)
{
    if (sd_bus_open_user(&state->bus) < 0) {
        fprintf(stderr, "[GAMEMODE] Failed to connect to session bus.\n");
        return false;
    }

    state->portal_appearance = portal_color_scheme(state->bus);

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
    if (r < 0) {
        sd_bus_unref(state->bus);
        state->bus = nullptr;
        return false;
    }

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

/* Bus died (broker restart, session teardown). Tear the connection down and,
 * crucially, re-arm any rotation timers a stale gamemode_active=true had
 * disarmed — otherwise rotation stays dead until the daemon restarts. */
static void gamemode_handle_disconnect(struct wallpaper_state* state, int err)
{
    fprintf(
        stderr, "[GAMEMODE] Session bus lost (%s); continuing without GameMode.\n", strerror(-err));
    gamemode_cleanup(state);
    if (state->gamemode_active) {
        state->gamemode_active = false;
        toggle_gamemode_timers(state, false);
    }
}

/* Rendering is Vulkan-only. Shader modules are generated offline from Slang and embedded by
 * vulkan_renderer.c. */

static char* get_cache_filename(uint64_t hash, char** dir_out)
{
    char* dir = resolve_cache_dir();
    if (!dir)
        return nullptr;
    char* path = nullptr;
    if (asprintf(&path, "%s/%016w64x.bin", dir, hash) < 0)
        path = nullptr;
    if (dir_out && path)
        *dir_out = dir;
    else
        free(dir);
    return path;
}

static inline void cleanup_vips_thread(void)
{
    vips_error_clear();
    vips_thread_shutdown();
}

/* Build the backdrop the material samples: the measured mixture of the sharp
 * image and one or two Gaussians, evaluated in sRGB CODE space because that is
 * where the step edge says the compositor blurs.
 *
 * The blur is done on a float copy so the two Gaussians and the sharp term are
 * summed at full precision and rounded once, rather than once per stage.
 * Edges are extended by copying the border before blurring and cropped after,
 * which is how the compositor clamps them - and it is not optional at the wide
 * radius, where a 165 pt kernel reaches a sixth of the way across a 4K
 * wallpaper and any other edge rule is visible along the whole border. */
/* The blur operates in the DISPLAY'S code space, not sRGB code space.
 *
 * Every gray instrument in the corpus - step edges, sine gratings - is
 * blind to the difference: R=G=B is a fixed point of the primary matrix, so
 * "sRGB code space" was only ever measured up to a change of primaries.
 * Chroma content is where the two spaces separate, which is exactly where
 * the coded-field-versus-step-edge weight dispute lived, and a flat field
 * cannot see it at all (blur of a constant round-trips exactly), which is
 * why the 528-case grid stayed clean while real wallpapers read a
 * channel-structured residual.
 *
 * Decided by a fit-free single-variable A/B against the M1 wallpaper-
 * transition captures (matrix taken from the capture display's ICC, nothing
 * tuned): settled-sweep interior regular/light 4.93 -> 3.79 and
 * regular/dark 3.44 -> 2.95 code values, clear controls 1.22/1.24 ->
 * 1.21/1.22, and the full 242-frame animated holdout 2.31 -> 2.17
 * full-frame, 4.62 -> 4.36 interior, worst frame 7.47 -> 6.40.  Improvement
 * on every instrument, regression on none.
 *
 * WALLE_BLUR_SPACE=srgb restores the previous behaviour for A/B replays. */
static int glass_blur_space_panel(void)
{
    static int cached = -1;
    if (cached < 0) {
        const char* space = getenv("WALLE_BLUR_SPACE");
        cached            = space == nullptr || strcmp(space, "srgb") != 0;
    }
    return cached;
}

/* sRGB-linear -> panel-linear colorants derived from the capture host's ICC
 * (Color LCD-37D8832A...icc, D50 PCS), and its inverse. */
static const double kGlassToPanelLinear[9] = {
    0.8225172, 0.1774401, -0.0000221,
    0.0331941, 0.9667933, -0.0000244,
    0.0171003, 0.0724382, 0.9108519,
};
static const double kGlassFromPanelLinear[9] = {
    1.2248519, -0.2248045, 0.0000237,
    -0.0420549, 1.0420637, 0.0000269,
    -0.0196507, -0.0786527, 1.0978707,
};

/* Fill the GPU bake descriptor for one output's variant/appearance/scale.
 * Returns false for identity (no material: the upload copies the standard
 * bytes) and for the WALLE_GLASS_BAKE=cpu replay. */
[[nodiscard]]
static bool glass_bake_descriptor(enum glass_variant          variant,
                                  float                       lightness,
                                  int32_t                     scale,
                                  struct walle_vk_glass_bake* out)
{
    if (variant == GLASS_VARIANT_IDENTITY || !glass_bake_on_gpu())
        return false;
    struct glass_blur_recipe recipe = glass_blur_for(variant, lightness, scale);
    *out = (struct walle_vk_glass_bake){
        .narrow_sigma         = (float)recipe.narrow_sigma,
        .wide_sigma           = (float)recipe.wide_sigma,
        .narrow_weight        = (float)recipe.narrow_weight,
        .narrow_chroma_weight = (float)recipe.narrow_chroma_weight,
        .panel_space          = glass_blur_space_panel() != 0,
    };
    /* The measured wide mechanism for `regular` (session 193): a five-level
     * gauss5 mip chain at the 2x capture scale, one fewer level per halving
     * of the output's pixels-per-point, with the fitted per-appearance
     * narrow mix.  WALLE_GLASS_WIDE=gauss replays the Gaussian stand-in. */
    /* OFF by default: the chain kernel is measured-correct at the edge
     * (-38%/-52% residual) but the shipped transfer and chroma laws were
     * fitted on the Gaussian's output statistics and compensate its shape
     * error - end-to-end the swap reads 3.21 coded until those laws are
     * re-derived on the chain (the campaign-3 refit).  WALLE_GLASS_WIDE=chain
     * enables it for that work. */
    /* The wide-field mechanism.  Default is `warp` (session 194): the
     * shipped two-Gaussian mixture with the far field computed in a
     * power-warped copy of code space - the law behind the appearance-keyed
     * step-edge asymmetry that survives the exact flat-table inversion with
     * registration pinned by the clear controls.  Referees: Apple settled
     * static edges light 2.31->0.92 / dark 4.32->2.30 rms; natural holdout
     * 0.86/1.31/1.83 -> 0.85/1.29/1.79; coded tied 1.37/2.13 (worst
     * 3.99->3.98); checkers, flats and clear are fixed points.
     * WALLE_GLASS_WIDE=gauss replays the un-warped mixture; chain/cascade
     * are the falsified/experimental variants kept for A/B. */
    static int wide_mode = -1; /* 0 gauss, 1 chain, 2 cascade, 3 warp (default),
                                * 4 flipcube */
    if (wide_mode < 0) {
        const char* mode = getenv("WALLE_GLASS_WIDE");
        wide_mode        = mode == nullptr              ? 3
                           : strcmp(mode, "gauss") == 0    ? 0
                           : strcmp(mode, "chain") == 0    ? 1
                           : strcmp(mode, "cascade") == 0  ? 2
                           : strcmp(mode, "warp") == 0     ? 3
                           : strcmp(mode, "flipcube") == 0 ? 4
                                                           : 3;
    }
    if (wide_mode == 1 && variant == GLASS_VARIANT_REGULAR) {
        double points = (scale > 0 ? (double)scale : 1.0) / GLASS_CAPTURE_SCALE;
        int    levels = 5 + (int)lround(log2(points));
        out->chain_levels       = levels < 1 ? 1 : levels;
        out->chain_coarse_sigma = (float)(lightness > 0.5 ? 0.25 : 3.25);
        out->narrow_weight      = 0.61f;
        out->narrow_sigma       = (float)((lightness > 0.5 ? 10.5 : 12.0) * points);
    }
    /* Cascade-warp (session 194): the step-edge asymmetry that survives the
     * exact flat-table inversion with registration pinned by the clear
     * controls.  The wide field runs in a power-warped copy of code space,
     * per appearance; the checker interiors (uniform fixed points) stay
     * exact.  Constants from analysis/fit_backdrop_space_mixture.py at
     * shift=0: light p=0.45 w=0.87 sn=14.5 sw=420; dark p=1.55 w=0.55
     * sn=14.0 sw=250 (capture px, edge rms 2.30->0.70 / 4.31->1.12). */
    if (wide_mode == 2 && variant == GLASS_VARIANT_REGULAR) {
        double points = (scale > 0 ? (double)scale : 1.0) / GLASS_CAPTURE_SCALE;
        bool   light  = lightness > 0.5;
        out->cascade_exponent = light ? 0.45f : 1.55f;
        out->narrow_weight    = light ? 0.87f : 0.55f;
        out->narrow_sigma     = (float)((light ? 14.5 : 14.0) * points);
        out->wide_sigma       = (float)((light ? 420.0 : 250.0) * points);
    }
    /* The shipped default: mixture constants untouched, ONLY the far-field
     * warp added (the single-variable ship, like panel space).  p refit on
     * the edges under the shipped constants: light 0.40 (rms 2.31->0.92),
     * dark 1.34 (4.32->2.30); non-regressing on every referee. */
    if (wide_mode == 3 && variant == GLASS_VARIANT_REGULAR)
        out->cascade_exponent = lightness > 0.5 ? 0.40f : 1.34f;
    /* Light's warp is NAMED (session 195): the flipped cube 1-(1-v)^3 -
     * the same power law on the inverted signal - matching the
     * nonparametric extraction at its floor (edge rms 0.77, slant holdout
     * 0.96, coded-light inside 3.64->3.44) BUT the natural holdout's light
     * sequences regress 0.918/1.382->0.947/1.427, so it stays behind
     * WALLE_GLASS_WIDE=flipcube until the disagreement is understood.
     * Dark's form is not yet named (extracted LUT with a robust u~0.65
     * plateau reaches edge 0.79 / slant 1.10; the power 1.34 ships). */
    if (wide_mode == 4 && variant == GLASS_VARIANT_REGULAR) {
        bool light            = lightness > 0.5;
        out->cascade_exponent = light ? 3.0f : 1.34f;
        out->cascade_flip     = light;
    }
    for (int i = 0; i < 9; ++i) {
        out->to_panel[i]   = (float)kGlassToPanelLinear[i];
        out->from_panel[i] = (float)kGlassFromPanelLinear[i];
    }
    return true;
}

/* Convert a float RGBA image between code spaces that share the sRGB transfer
 * curve: decode the piecewise EOTF on the 0..255 code scale, apply the 3x3 in
 * linear light (alpha untouched), clamp the tiny out-of-gamut negatives, and
 * re-encode.  Returns a new reference or nullptr. */
[[nodiscard]]
/* Decode sRGB-encoded code values (float, 0..255) to linear light: the lazy
 * subgraph shared by the general converter and the ramp that builds the
 * forward LUT below. */
static VipsImage* glass_srgb_decode_graph(VipsImage* in)
{
    VipsImage* result = nullptr;
    VipsImage *less = nullptr, *lin_a = nullptr, *lin_b = nullptr, *pre = nullptr;
    if (vips_relational_const1(in, &less, VIPS_OPERATION_RELATIONAL_LESSEQ,
                               0.04045 * 255.0, nullptr))
        goto out;
    if (vips_linear1(in, &lin_a, 1.0 / (12.92 * 255.0), 0.0, nullptr))
        goto out;
    if (vips_linear1(in, &pre, 1.0 / (1.055 * 255.0), 0.055 / 1.055, nullptr))
        goto out;
    if (vips_math2_const1(pre, &lin_b, VIPS_OPERATION_MATH2_POW, 2.4, nullptr))
        goto out;
    (void)vips_ifthenelse(less, lin_a, lin_b, &result, nullptr);
out:
    if (less) g_object_unref(less);
    if (lin_a) g_object_unref(lin_a);
    if (lin_b) g_object_unref(lin_b);
    if (pre) g_object_unref(pre);
    return result;
}

/* Linear light -> colorant matrix -> clamp at zero -> sRGB-curve encode, then
 * materialize the whole conversion once.  Left lazy, the two blur branches
 * re-evaluate this graph per region, and the first experiment run drove walle
 * to a 75 GB RSS OOM kill. */
static VipsImage* glass_encode_from_linear(VipsImage* lin, const double matrix[9])
{
    VipsImage* result = nullptr;
    VipsImage *mat = nullptr, *mixed = nullptr, *clamped = nullptr;
    VipsImage *enc_le = nullptr, *enc_a = nullptr, *root = nullptr, *enc_b = nullptr;

    {
        double m[16] = {matrix[0], matrix[1], matrix[2], 0.0,
                        matrix[3], matrix[4], matrix[5], 0.0,
                        matrix[6], matrix[7], matrix[8], 0.0,
                        0.0,       0.0,       0.0,       1.0};
        mat = vips_image_new_matrixv(4, 4,
            m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7],
            m[8], m[9], m[10], m[11], m[12], m[13], m[14], m[15]);
        if (mat == nullptr || vips_recomb(lin, &mixed, mat, nullptr))
            goto out;
    }
    /* Alpha rides through the EOTF math with the color bands; the matrix row
     * keeps it proportional, and the backdrop's alpha is constant 255
     * everywhere, so the round trip returns it exactly. */
    {
        VipsImage* zero_cmp = nullptr;
        VipsImage* zeros    = nullptr;
        if (vips_relational_const1(mixed, &zero_cmp,
                                   VIPS_OPERATION_RELATIONAL_LESS, 0.0, nullptr))
            goto out;
        if (vips_linear1(mixed, &zeros, 0.0, 0.0, nullptr)) {
            g_object_unref(zero_cmp);
            goto out;
        }
        int failure = vips_ifthenelse(zero_cmp, zeros, mixed, &clamped, nullptr);
        g_object_unref(zero_cmp);
        g_object_unref(zeros);
        if (failure)
            goto out;
    }
    if (vips_relational_const1(clamped, &enc_le, VIPS_OPERATION_RELATIONAL_LESSEQ,
                               0.0031308, nullptr))
        goto out;
    if (vips_linear1(clamped, &enc_a, 12.92 * 255.0, 0.0, nullptr))
        goto out;
    if (vips_math2_const1(clamped, &root, VIPS_OPERATION_MATH2_POW, 1.0 / 2.4, nullptr))
        goto out;
    if (vips_linear1(root, &enc_b, 1.055 * 255.0, -0.055 * 255.0, nullptr))
        goto out;
    {
        VipsImage* lazy = nullptr;
        if (vips_ifthenelse(enc_le, enc_a, enc_b, &lazy, nullptr))
            goto out;
        result = vips_image_copy_memory(lazy);
        g_object_unref(lazy);
    }
out:
    if (mat) g_object_unref(mat);
    if (mixed) g_object_unref(mixed);
    if (clamped) g_object_unref(clamped);
    if (enc_le) g_object_unref(enc_le);
    if (enc_a) g_object_unref(enc_a);
    if (root) g_object_unref(root);
    if (enc_b) g_object_unref(enc_b);
    return result;
}

static VipsImage* glass_convert_code_space(VipsImage* in, const double matrix[9])
{
    VipsImage* lin = glass_srgb_decode_graph(in);
    if (lin == nullptr)
        return nullptr;
    VipsImage* result = glass_encode_from_linear(lin, matrix);
    g_object_unref(lin);
    return result;
}

/* The forward conversion's decode acts on QUANTIZED inputs - the standard
 * layer is uchar - so it is a 256-entry table, not a per-pixel pow.  The
 * table is EXTRACTED FROM THE DECODE GRAPH ITSELF by running it on a ramp,
 * which makes the LUT path bit-identical to the graph it replaces by
 * construction (and the cache-entry sha256 A/B verifies it end to end). */
static float          g_srgb_decode_lut[256];
static bool           g_srgb_decode_lut_ok = false;
static pthread_once_t g_srgb_decode_lut_once = PTHREAD_ONCE_INIT;

static void glass_srgb_decode_lut_build(void)
{
    static uint8_t ramp[256];
    for (int i = 0; i < 256; i++)
        ramp[i] = (uint8_t)i;
    VipsImage* src = vips_image_new_from_memory(ramp, sizeof ramp, 256, 1, 1,
                                                VIPS_FORMAT_UCHAR);
    if (src == nullptr)
        return;
    VipsImage* asfloat = nullptr;
    if (vips_cast(src, &asfloat, VIPS_FORMAT_FLOAT, nullptr)) {
        g_object_unref(src);
        return;
    }
    g_object_unref(src);
    VipsImage* lin = glass_srgb_decode_graph(asfloat);
    g_object_unref(asfloat);
    if (lin == nullptr)
        return;
    VipsImage* solid = vips_image_copy_memory(lin);
    g_object_unref(lin);
    if (solid == nullptr)
        return;
    memcpy(g_srgb_decode_lut, VIPS_IMAGE_ADDR(solid, 0, 0), sizeof g_srgb_decode_lut);
    g_object_unref(solid);
    g_srgb_decode_lut_ok = true;
}

/* Forward conversion for a uchar source: table decode, then the shared
 * matrix+encode.  Falls back to the general path for non-uchar sources. */
static VipsImage* glass_convert_code_space_lut(VipsImage* uchar_in, const double matrix[9])
{
    pthread_once(&g_srgb_decode_lut_once, glass_srgb_decode_lut_build);
    if (!g_srgb_decode_lut_ok || vips_image_get_format(uchar_in) != VIPS_FORMAT_UCHAR) {
        VipsImage* asfloat = nullptr;
        if (vips_cast(uchar_in, &asfloat, VIPS_FORMAT_FLOAT, nullptr))
            return nullptr;
        VipsImage* result = glass_convert_code_space(asfloat, matrix);
        g_object_unref(asfloat);
        return result;
    }
    VipsImage* lut = vips_image_new_from_memory(g_srgb_decode_lut,
                                                sizeof g_srgb_decode_lut, 256, 1, 1,
                                                VIPS_FORMAT_FLOAT);
    if (lut == nullptr)
        return nullptr;
    VipsImage* lin = nullptr;
    int failure = vips_maplut(uchar_in, &lin, lut, nullptr);
    g_object_unref(lut);
    if (failure)
        return nullptr;
    VipsImage* result = glass_encode_from_linear(lin, matrix);
    g_object_unref(lin);
    return result;
}

[[nodiscard]]
static VipsImage* glass_blur_image(VipsImage* source, const struct glass_blur_recipe* recipe)
{
    VipsImage* value = nullptr;
    if (glass_blur_space_panel()) {
        /* Table-decoded forward conversion: one pow pass fewer, bytes
         * identical (the table comes from the decode graph itself). */
        value = glass_convert_code_space_lut(source, kGlassToPanelLinear);
        if (value == nullptr)
            return nullptr;
    } else if (vips_cast(source, &value, VIPS_FORMAT_FLOAT, nullptr)) {
        return nullptr;
    }

    VipsImage* total     = nullptr;
    VipsImage* layers[2] = {nullptr, nullptr};
    if (recipe->sharp_weight > 0.0) {
        if (vips_linear1(value, &total, recipe->sharp_weight, 0.0, nullptr)) {
            g_object_unref(value);
            return nullptr;
        }
    }

    const double weights[2] = {recipe->narrow_weight, recipe->wide_weight};
    const double sigmas[2]  = {recipe->narrow_sigma, recipe->wide_sigma};
    for (int index = 0; index < 2; ++index) {
        if (!(weights[index] > 0.0) || !(sigmas[index] > 0.0))
            continue;
        /* A direct convolution at the wide radius is a 991-tap mask over every
         * pixel, which is minutes per wallpaper.  Reduce first instead: a
         * kernel this broad has nothing above the reduced Nyquist to lose, and
         * the reduce/expand pair adds under 0.05 px in quadrature to a 165 px
         * sigma.  The narrow radii convolve directly, where the mask is small
         * and the detail is the whole point. */
        double reduction = 1.0;
        while (sigmas[index] / reduction > 24.0)
            reduction *= 2.0;

        VipsImage* stage = value;
        g_object_ref(stage);
        if (reduction > 1.0) {
            VipsImage* small = nullptr;
            if (vips_reduce(stage, &small, reduction, reduction, nullptr)) {
                g_object_unref(stage);
                goto failed;
            }
            g_object_unref(stage);
            stage = small;
        }

        /* Three sigma of margin holds all but 0.3% of the kernel; vips's own
         * min_ampl cutoff trims the rest.  Extending by copying the border is
         * how the compositor clamps, and at the wide radius it is not optional:
         * the kernel reaches a sixth of the way across a 4K wallpaper, so any
         * other edge rule is visible along the whole border. */
        double     sigma  = sigmas[index] / reduction;
        int        margin = (int)ceil(sigma * 3.0);
        VipsImage* padded = nullptr;
        int        failure = vips_embed(stage,
                                 &padded,
                                 margin,
                                 margin,
                                 stage->Xsize + 2 * margin,
                                 stage->Ysize + 2 * margin,
                                 "extend",
                                 VIPS_EXTEND_COPY,
                                 nullptr);
        g_object_unref(stage);
        if (failure)
            goto failed;

        VipsImage* blurred = nullptr;
        failure = vips_gaussblur(padded, &blurred, sigma, "min_ampl", 0.001, "precision",
                                 VIPS_PRECISION_FLOAT, nullptr);
        g_object_unref(padded);
        if (failure)
            goto failed;

        VipsImage* cropped = nullptr;
        failure = vips_crop(blurred, &cropped, margin, margin, blurred->Xsize - 2 * margin,
                            blurred->Ysize - 2 * margin, nullptr);
        g_object_unref(blurred);
        if (failure)
            goto failed;

        if (reduction > 1.0) {
            VipsImage* expanded = nullptr;
            failure = vips_resize(cropped, &expanded, (double)value->Xsize / cropped->Xsize,
                                  "vscale", (double)value->Ysize / cropped->Ysize, nullptr);
            g_object_unref(cropped);
            if (failure)
                goto failed;
            cropped = expanded;
        }

        /* Materialize each layer ONCE.  Both layers are referenced twice
         * below - the chroma-weighted sum and the luma-correction difference
         * - and with the operation cache disabled a lazy layer re-runs its
         * whole convolution pipeline per reference.  Same bytes, half the
         * blur work. */
        VipsImage* solid = vips_image_copy_memory(cropped);
        g_object_unref(cropped);
        if (solid == nullptr)
            goto failed;
        layers[index] = solid;
    }
    g_object_unref(value);

    /* Mix at the CHROMA weight, then add back what luma wants differently.
     *
     *   out = wC*near + (1-wC)*far + (wL - wC) * luma(near - far)
     *
     * which is the two-subspace mixture rearranged so that only one extra
     * single-band image is ever built.  Where the two weights are equal - and
     * for `clear`, where they nearly are - the correction is nothing. */
    for (int index = 0; index < 2; ++index) {
        if (layers[index] == nullptr)
            continue;
        double weight = index == 0 ? recipe->narrow_chroma_weight
                                   : 1.0 - recipe->narrow_chroma_weight;
        VipsImage* scaled = nullptr;
        if (vips_linear1(layers[index], &scaled, weight, 0.0, nullptr))
            goto failed;
        if (total == nullptr) {
            total = scaled;
            continue;
        }
        VipsImage* summed = nullptr;
        int failure = vips_add(total, scaled, &summed, nullptr);
        g_object_unref(scaled);
        if (failure)
            goto failed;
        g_object_unref(total);
        total = summed;
    }
    if (total == nullptr)
        goto failed;

    double lift = recipe->narrow_weight - recipe->narrow_chroma_weight;
    if (layers[0] != nullptr && layers[1] != nullptr && fabs(lift) > 1e-9) {
        VipsImage* difference = nullptr;
        if (vips_subtract(layers[0], layers[1], &difference, nullptr))
            goto failed;

        /* luma(near - far), built band by band rather than by a recombination
         * matrix.  The backdrop carries alpha, so the matrix has to be square
         * and its orientation matters; spelling the sum out cannot be wrong
         * about either, and the check that it is right is that a GRAY backdrop
         * must come out exactly where it did before - there, luma(D) is D and
         * the whole correction collapses back into the single-weight mix. */
        static const double kLuma[3] = {0.2126, 0.7152, 0.0722};
        VipsImage*          band     = nullptr;
        int                 failure  = 0;
        for (int channel = 0; channel < 3 && !failure; ++channel) {
            VipsImage* extracted = nullptr;
            if (vips_extract_band(difference, &extracted, channel, nullptr)) {
                failure = 1;
                break;
            }
            VipsImage* weighted = nullptr;
            failure = vips_linear1(extracted, &weighted, kLuma[channel], 0.0,
                                   nullptr);
            g_object_unref(extracted);
            if (failure)
                break;
            if (band == nullptr) {
                band = weighted;
                continue;
            }
            VipsImage* added = nullptr;
            failure          = vips_add(band, weighted, &added, nullptr);
            g_object_unref(weighted);
            if (failure)
                break;
            g_object_unref(band);
            band = added;
        }
        g_object_unref(difference);
        if (failure || band == nullptr) {
            if (band != nullptr)
                g_object_unref(band);
            goto failed;
        }

        /* One band per band of the mix, so nothing has to broadcast. */
        VipsImage* wide = nullptr;
        {
            VipsImage* copies[4] = {band, band, band, band};
            failure = vips_bandjoin(copies, &wide, total->Bands, nullptr);
        }
        g_object_unref(band);
        if (failure)
            goto failed;

        VipsImage* correction = nullptr;
        failure = vips_linear1(wide, &correction, lift, 0.0, nullptr);
        g_object_unref(wide);
        if (failure)
            goto failed;

        VipsImage* summed = nullptr;
        failure           = vips_add(total, correction, &summed, nullptr);
        g_object_unref(correction);
        if (failure)
            goto failed;
        g_object_unref(total);
        total = summed;
    }
    for (int index = 0; index < 2; ++index)
        if (layers[index] != nullptr)
            g_object_unref(layers[index]);

    if (glass_blur_space_panel()) {
        VipsImage* back = glass_convert_code_space(total, kGlassFromPanelLinear);
        g_object_unref(total);
        if (back == nullptr)
            return nullptr;
        total = back;
    }

    /* One rounding, at the end.  vips_cast to uchar rounds to nearest and
     * clamps, which is what the captured 8-bit output does. */
    VipsImage* result = nullptr;
    if (vips_cast(total, &result, VIPS_FORMAT_UCHAR, nullptr)) {
        g_object_unref(total);
        return nullptr;
    }
    g_object_unref(total);
    return result;

failed:
    fprintf(stderr, "[GLASS] blur failed: %s\n", vips_error_buffer());
    vips_error_clear();
    for (int index = 0; index < 2; ++index)
        if (layers[index] != nullptr)
            g_object_unref(layers[index]);
    if (total != nullptr)
        g_object_unref(total);
    return nullptr;
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

/* -- Render Thread ------------------------------------------------------- */

static void* render_thread_worker(void* arg)
{
#if defined(WALLE_TRACY)
    TracyCZoneN(tracy_prepare_wallpaper, "prepare wallpaper", true);
#endif
    auto output = (struct wallpaper_output*)arg;
    char thread_name[16];
    snprintf(thread_name, sizeof(thread_name), "wrk-%s", output->name ? output->name : "anon");
    pthread_setname_np(pthread_self(), thread_name);


#if !defined(NDEBUG)
    /* Canary: SIGINT/SIGTERM must have been blocked process-wide before any
     * thread spawned, or the signalfd shutdown protocol is broken. */
    sigset_t sigcur;
    pthread_sigmask(SIG_SETMASK, nullptr, &sigcur);
    assert(sigismember(&sigcur, SIGINT) && sigismember(&sigcur, SIGTERM));
#endif

    struct render_result result  = {.success = false, .std_fd = -1, .glass_fd = -1};
    auto                 item    = &output->items[output->current_item_index];
    uint64_t             t_begin = trace_now_ns();
    uint64_t             t_std = 0, t_glass = 0;
    bool                 std_hit = false, glass_hit = false;
    int                  w       = output->job_w;
    int                  h       = output->job_h;
    enum glass_variant   variant = output->job_variant;

    /* The backdrop the material samples, at the measured radii.  It is stored
     * at full resolution because clear's mixture carries 19% of the sharp
     * image, which a reduced level cannot represent.  Identity never samples
     * it, so identity aliases the standard layer and skips the bake. */
    bool need_glass = variant != GLASS_VARIANT_IDENTITY && !glass_bake_on_gpu();
    struct glass_blur_recipe blur
        = glass_blur_for(variant, output->job_lightness, output->job_scale);
    char*      cpath              = nullptr; /* standard entry */
    char*      glass_cpath        = nullptr; /* glass entry */
    char*      cdir               = nullptr;
    char*      glass_cdir         = nullptr;
    int        fd                 = -1; /* standard entry fd */
    int        glass_fd           = -1;
    bool       cache_backed       = false;
    bool       glass_cache_backed = false;
    uint8_t*   map                = nullptr; /* standard pixels */
    uint8_t*   glass_map          = nullptr;
    VipsImage *img = nullptr, *tmp = nullptr;

    /* Always decode to RGBA: one band count keeps texture storage, PBO sizing,
     * and row alignment uniform for every image and output. */
    constexpr int bands = 4;

    VipsImage* header
        = vips_image_new_from_file(item->filename, "access", VIPS_ACCESS_SEQUENTIAL, nullptr);
    if (!header) {
        vips_error_clear();
        goto finalize;
    }

    int interlaced = 0;
    if (vips_image_get_int(header, "interlaced", &interlaced) == 0 && interlaced) {
        fprintf(stderr,
                "[MEMORY WARNING] '%s' is interlaced/progressive - may cause memory spike\n",
                item->filename);
    }
    g_object_unref(header);

    size_t raw_sz, glass_sz;
    if (ckd_mul(&raw_sz, (size_t)w * h, bands))
        goto finalize;
    glass_sz = raw_sz;

    struct stat st;
    if (stat(item->filename, &st))
        goto finalize;

    /* Two content-addressed entries per item.  The STANDARD entry keys on the
     * source and geometry alone, so every variant and appearance shares one
     * decode+crop.  The GLASS entry continues the same hash stream with the
     * variant, the recipe, and the blur space.  A variant change therefore
     * re-bakes at most the glass - never the decode, the crop, or the
     * standard bytes - and identity re-bakes nothing. */
    uint64_t std_hash, glass_hash = 0;
    {
        XXH64_state_t* xxh = XXH64_createState();
        if (!xxh)
            goto finalize;
        uint8_t kind = 0;
        XXH64_reset(xxh, 0);
        XXH64_update(xxh, &CACHE_SCHEMA_VERSION, sizeof(CACHE_SCHEMA_VERSION));
        XXH64_update(xxh, &kind, sizeof kind);
        XXH64_update(xxh, item->filename, strlen(item->filename));
        XXH64_update(xxh, &st.st_mtim, sizeof(st.st_mtim));
        XXH64_update(xxh, &st.st_size, sizeof(st.st_size));
        XXH64_update(xxh, &w, sizeof(w));
        XXH64_update(xxh, &h, sizeof(h));
        XXH64_update(xxh, &item->mode, sizeof(item->mode));
        if (item->mode == MODE_FILL)
            XXH64_update(xxh, &item->crop_strategy, sizeof(item->crop_strategy));
        std_hash = XXH64_digest(xxh);
        if (need_glass) {
            kind = 1;
            XXH64_update(xxh, &kind, sizeof kind);
            XXH64_update(xxh, &variant, sizeof(variant));
            /* The whole recipe, because the appearance moves the blend weights
             * and an entry built for the other appearance is a different
             * image. */
            XXH64_update(xxh, &blur, sizeof(blur));
            /* The blur space is part of the pixel pipeline: a panel-code-space
             * bake and an sRGB replay must never share a cache entry. */
            int blur_space_panel = glass_blur_space_panel();
            XXH64_update(xxh, &blur_space_panel, sizeof(blur_space_panel));
            glass_hash = XXH64_digest(xxh);
        }
        XXH64_freeState(xxh);
    }

    cpath = get_cache_filename(std_hash, &cdir);
    if (need_glass)
        glass_cpath = get_cache_filename(glass_hash, &glass_cdir);

    if (cpath) {
        int cfd = open(cpath, O_RDONLY | O_CLOEXEC);
        if (cfd >= 0) {
            struct stat cst;
            if (fstat(cfd, &cst) == 0 && (size_t)cst.st_size == raw_sz) {
                futimens(cfd, nullptr); /* LRU bump for the mtime-keyed GC */
                posix_fadvise(cfd, 0, 0, POSIX_FADV_WILLNEED);
                fd = cfd;
            } else {
                /* Wrong size = torn/stale entry: remove it so it can be
                 * republished instead of failing validation forever. */
                close(cfd);
                unlink(cpath);
            }
        }
    }
    if (need_glass && glass_cpath) {
        int cfd = open(glass_cpath, O_RDONLY | O_CLOEXEC);
        if (cfd >= 0) {
            struct stat cst;
            if (fstat(cfd, &cst) == 0 && (size_t)cst.st_size == glass_sz) {
                futimens(cfd, nullptr);
                posix_fadvise(cfd, 0, 0, POSIX_FADV_WILLNEED);
                glass_fd = cfd;
            } else {
                close(cfd);
                unlink(glass_cpath);
            }
        }
    }
    std_hit   = fd >= 0;
    glass_hit = glass_fd >= 0;
    if (fd >= 0 && (!need_glass || glass_fd >= 0))
        goto publish_result; /* full hit: no decode, no blur */

    if (fd >= 0) {
        /* Standard hit, glass missing: map the cached standard as the blur
         * input and skip the decode pipeline entirely. */
        map = mmap(nullptr, raw_sz, PROT_READ, MAP_SHARED, fd, 0);
        if (map == MAP_FAILED) {
            map = nullptr;
            goto finalize_close;
        }
        goto bake_glass;
    }

    /* Build into an anonymous file and publish atomically on success, so a
     * crash mid-write can never leave a half-written cache entry and
     * same-key sibling renders never observe in-progress bytes. */
    if (cdir) {
        fd           = open(cdir, O_TMPFILE | O_RDWR | O_CLOEXEC, 0600);
        cache_backed = fd >= 0;
    }
    if (fd < 0) {
        fd           = (int)memfd_create("walle-standard", MFD_CLOEXEC);
        cache_backed = false;
    }
    if (fd < 0)
        goto finalize;

    int fal;
    do {
        fal = posix_fallocate(fd, 0, (off_t)raw_sz);
    } while (fal == EINTR);
    if (fal != 0) {
        /* No ftruncate fallback: a sparse file whose blocks cannot be
         * allocated turns the mmap writes below into SIGBUS. */
        goto finalize_close;
    }

    map = mmap(nullptr, raw_sz, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (map == MAP_FAILED) {
        map = nullptr;
        goto finalize_close;
    }

    madvise(map, raw_sz, MADV_SEQUENTIAL);

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

    /* Normalize to exactly RGBA. */
    if (vips_image_get_bands(img) < 4) {
        if (vips_addalpha(img, &tmp, nullptr))
            goto vips_err;
        g_object_unref(img);
        img = tmp;
    } else if (vips_image_get_bands(img) > 4) {
        if (vips_extract_band(img, &tmp, 0, "n", 4, nullptr))
            goto vips_err;
        g_object_unref(img);
        img = tmp;
    }

    if (vips_image_get_format(img) != VIPS_FORMAT_UCHAR) {
        if (vips_cast(img, &tmp, VIPS_FORMAT_UCHAR, nullptr))
            goto vips_err;
        g_object_unref(img);
        img = tmp;
    }

    if (write_pipeline_to_buffer_direct(img, map, raw_sz) != 0)
        goto vips_err;

    g_object_unref(img);
    img = nullptr;

    /* Publish the standard entry atomically; a racing sibling render of the
     * same key produced identical bytes, so EEXIST is a win, not an error. */
    if (cache_backed && cpath) {
        char proc[64];
        snprintf(proc, sizeof(proc), "/proc/self/fd/%d", fd);
        if (linkat(AT_FDCWD, proc, AT_FDCWD, cpath, AT_SYMLINK_FOLLOW) < 0 && errno != EEXIST)
            dbg_print("cache publish failed: %s", strerror(errno));
    }

bake_glass:
    t_std = trace_now_ns();
    if (need_glass && glass_fd < 0) {
        /* The measured backdrop, built from the standard bytes just written
         * (or mapped straight from a cached standard entry): the wallpaper is
         * decoded exactly once ever, and on a standard hit not at all. */
        if (glass_cdir) {
            glass_fd           = open(glass_cdir, O_TMPFILE | O_RDWR | O_CLOEXEC, 0600);
            glass_cache_backed = glass_fd >= 0;
        }
        if (glass_fd < 0) {
            glass_fd           = (int)memfd_create("walle-glass", MFD_CLOEXEC);
            glass_cache_backed = false;
        }
        if (glass_fd < 0)
            goto vips_err;
        int gfal;
        do {
            gfal = posix_fallocate(glass_fd, 0, (off_t)glass_sz);
        } while (gfal == EINTR);
        if (gfal != 0)
            goto vips_err;
        glass_map = mmap(nullptr, glass_sz, PROT_READ | PROT_WRITE, MAP_SHARED, glass_fd, 0);
        if (glass_map == MAP_FAILED) {
            glass_map = nullptr;
            goto vips_err;
        }
        madvise(glass_map, glass_sz, MADV_SEQUENTIAL);

        VipsImage* decoded
            = vips_image_new_from_memory(map, raw_sz, w, h, bands, VIPS_FORMAT_UCHAR);
        if (!decoded)
            goto vips_err;
        VipsImage* glass = glass_blur_image(decoded, &blur);
        g_object_unref(decoded);
        if (!glass)
            goto vips_err;
        int written = write_pipeline_to_buffer_direct(glass, glass_map, glass_sz);
        g_object_unref(glass);
        if (written != 0)
            goto vips_err;
        munmap(glass_map, glass_sz);
        glass_map = nullptr;

        if (glass_cache_backed && glass_cpath) {
            char proc[64];
            snprintf(proc, sizeof(proc), "/proc/self/fd/%d", glass_fd);
            if (linkat(AT_FDCWD, proc, AT_FDCWD, glass_cpath, AT_SYMLINK_FOLLOW) < 0
                && errno != EEXIST)
                dbg_print("cache publish failed: %s", strerror(errno));
        }
    }

    t_glass = trace_now_ns();
    if (map) {
        munmap(map, raw_sz);
        map = nullptr;
    }

publish_result:
    result.std_fd   = fd;
    fd              = -1; /* ownership moved into result */
    result.glass_fd = need_glass ? glass_fd : result.std_fd;
    glass_fd        = -1;
    result.standard = (struct image_layer){.offset = 0, .size = raw_sz, .width = w, .height = h};
    result.glass    = (struct image_layer){.offset = 0, .size = glass_sz, .width = w, .height = h};
    result.success  = true;
    goto finalize;

vips_err:
    if (img)
        g_object_unref(img);
finalize_close:
    if (map)
        munmap(map, raw_sz);
    if (glass_map)
        munmap(glass_map, glass_sz);
    if (fd >= 0) {
        close(fd); /* tmpfiles are anonymous until linked: nothing torn */
        fd = -1;
    }
    if (glass_fd >= 0) {
        close(glass_fd);
        glass_fd = -1;
    }

finalize:
    free(cpath);
    free(cdir);
    free(glass_cpath);
    free(glass_cdir);
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

    result.done_ns = trace_now_ns();
    {
        double total_ms = (double)(result.done_ns - t_begin) / 1e6;
        if (total_ms > 250.0) {
            double std_ms   = t_std ? (double)(t_std - t_begin) / 1e6 : 0.0;
            double glass_ms = (t_glass && t_std) ? (double)(t_glass - t_std) / 1e6 : 0.0;
            fprintf(stderr,
                    "[BAKE] %s '%s' variant=%d std=%s glass=%s took %.0f ms"
                    " (standard %.0f, glass %.0f)\n",
                    output->name ? output->name : "-",
                    item->filename,
                    (int)variant,
                    std_hit ? "hit" : "bake",
                    !need_glass ? "none" : (glass_hit ? "hit" : "bake"),
                    total_ms,
                    std_ms,
                    glass_ms);
        }
    }
    output->async_result = result;
    uint64_t sig         = 1;
    if (write(output->event_fd, &sig, sizeof(sig)) != sizeof(sig)) {
    }
#if defined(WALLE_TRACY)
    TracyCZoneValue(tracy_prepare_wallpaper,
                    result.success ? result.standard.size + result.glass.size : 0);
    TracyCZoneEnd(tracy_prepare_wallpaper);
#endif
    return nullptr;
}

/* -- Frame Loop ---------------------------------------------------------- */

/* Resolve the appearance the shader should use: explicit config wins, then
 * the desktop portal, then the content-luminance stand-in (-1). */
[[nodiscard]]
/* The material's appearance, as one value for the whole output.
 *
 * It is deliberately NOT derived from content luminance any more.  Apple takes
 * the appearance from the system setting; there is no content-derived path in
 * the hardware, and walle's old smoothstep over a screen-wide wash predates the
 * portal being wired up and never had a measurement behind it.  It also cannot
 * survive the backdrop blur being baked on the CPU: the blend between the
 * material's narrow and wide layers depends on the appearance, so a per-pixel
 * appearance would mean a per-pixel backdrop, and the baked backdrop and the
 * shader's chosen colour matrices could disagree on the same pixel.
 *
 * Config wins, then the desktop portal, then dark - which is the only value
 * left once neither of the two authorities answers. */
static float glass_appearance_value(const struct wallpaper_output* output)
{
    enum glass_appearance appearance = output->glass_appearance;
    if (appearance == GLASS_APPEARANCE_AUTO && output->render.state != nullptr)
        appearance = output->render.state->portal_appearance;
    return appearance == GLASS_APPEARANCE_LIGHT ? 1.0f : 0.0f;
}

static void frame_callback_handler(void* data, struct wl_callback* callback, uint32_t time);
static const struct wl_callback_listener frame_listener = {.done = frame_callback_handler};

static void reveal_process_capture_fail(struct wallpaper_state* state, const char* reason)
{
    if (!state->reveal_process_capture || state->reveal_process_capture_complete)
        return;
    fprintf(stderr, "[REVEAL CAPTURE] Failed: %s\n", reason);
    state->reveal_process_capture_status   = 1;
    state->reveal_process_capture_complete = true;
}

[[nodiscard]]
static bool write_all_bytes(int fd, const uint8_t* bytes, size_t byte_count)
{
    while (byte_count > 0) {
        ssize_t written = write(fd, bytes, byte_count);
        if (written < 0) {
            if (errno == EINTR)
                continue;
            return false;
        }
        if (written == 0) {
            errno = EIO;
            return false;
        }
        bytes += (size_t)written;
        byte_count -= (size_t)written;
    }
    return true;
}

[[nodiscard]]
static bool write_reveal_process_capture(struct wallpaper_output* output)
{
    struct wallpaper_state* state = output->render.state;
    char                    name[sizeof "state-0000.r8"];
    int                     name_length
        = snprintf(name, sizeof(name), "state-%04u.r8", output->reveal_process_capture_state);
    if (name_length < 0 || (size_t)name_length >= sizeof(name)) {
        errno = EOVERFLOW;
        return false;
    }

    int fd = openat(state->reveal_process_capture_directory_fd,
                    name,
                    O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
                    S_IRUSR | S_IWUSR);
    if (fd < 0)
        return false;

    bool success
        = write_all_bytes(fd,
                          output->reveal_process_capture_pixels,
                          (size_t)REVEAL_PROCESS_CAPTURE_WIDTH * REVEAL_PROCESS_CAPTURE_HEIGHT);

    int saved_errno = errno;
    if (close(fd) < 0 && success) {
        success     = false;
        saved_errno = errno;
    }
    if (!success)
        (void)unlinkat(state->reveal_process_capture_directory_fd, name, 0);
    errno = saved_errno;
    return success;
}

[[nodiscard]]
static bool write_reveal_process_composition(struct wallpaper_output* output)
{
    char                    name[40];
    struct wallpaper_state* state = output->render.state;
    snprintf(name,
             sizeof name,
             "composition-state-%04u.bgra",
             output->reveal_process_capture_state);
    int fd = openat(state->reveal_process_capture_directory_fd,
                    name,
                    O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
                    S_IRUSR | S_IWUSR);
    if (fd < 0)
        return false;

    bool success
        = write_all_bytes(fd,
                          output->reveal_process_composition_pixels,
                          (size_t)REVEAL_PROCESS_CAPTURE_WIDTH * REVEAL_PROCESS_CAPTURE_HEIGHT * 4);
    int saved_errno = errno;
    if (close(fd) < 0 && success) {
        success     = false;
        saved_errno = errno;
    }
    if (!success)
        (void)unlinkat(state->reveal_process_capture_directory_fd, name, 0);
    errno = saved_errno;
    return success;
}

static void stop_failed_transition(struct wallpaper_output* output, const char* reason)
{
    fprintf(stderr, "[Vulkan] Transition stopped for %s: %s\n", output->name, reason);
    reveal_process_capture_fail(output->render.state, reason);
    if (output->frame_callback) {
        wl_callback_destroy(output->frame_callback);
        output->frame_callback = nullptr;
    }
    output->render.t_state = T_STATE_IDLE;
    walle_vk_output_abort_transition(output->render.vk_output);
    release_render_result(&output->pending_source);
}

enum render_frame_result : uint8_t
{
    RENDER_FRAME_FAILED,
    RENDER_FRAME_PRESENTED,
    RENDER_FRAME_RETRY,
};

[[nodiscard]]
static enum render_frame_result render_frame(struct wallpaper_output* output)
{
    if ((output->render.flags & F_DEAD) || !output->render.vk_output
        || output->render.t_state == T_STATE_IDLE)
        return RENDER_FRAME_FAILED;

    struct wallpaper_state* state           = output->render.state;
    bool                    process_capture = output->reveal_process_capture_active;
#if defined(WALLE_TRACY)
    TracyCZoneN(tracy_transition_frame, "Vulkan transition frame", true);
#endif

    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    uint64_t now_ns = (uint64_t)now.tv_sec * UINT64_C(1'000'000'000) + (uint64_t)now.tv_nsec;
    if (output->render.t_state == T_STATE_ARMED) {
        output->render.anim_start_ns = now_ns;
        output->render.t_state       = T_STATE_RUNNING;
    }
    float elapsed = (float)(now_ns - output->render.anim_start_ns) * 1e-9f;
    float progress;
    bool  finished;
    if (process_capture && state->reveal_process_capture_single) {
        progress = state->reveal_process_capture_progress_values
                       [output->reveal_process_capture_state];
        finished = output->reveal_process_capture_state
                   == state->reveal_process_capture_progress_count - 1u;
    } else if (process_capture) {
        progress = (float)output->reveal_process_capture_state
                   / (float)(REVEAL_PROCESS_CAPTURE_STATE_COUNT - 1);
        finished = output->reveal_process_capture_state == REVEAL_PROCESS_CAPTURE_STATE_COUNT - 1;
    } else {
        progress = elapsed * output->render.duration_inv;
        finished = progress >= 1.0f;
        if (finished)
            progress = 1.0f;
    }

    /* The geometry runs on Apple's measured clock-to-state mapping; the
     * material keeps the raw linear clock, which is the clock its own
     * dematerialize law was fitted against.  The capture path passes the state
     * through untouched - see reveal_state_from_clock. */
    float reveal_state = process_capture ? progress : reveal_state_from_clock(progress);

    struct walle_lg_reveal_mask_geometry      geometry = {};
    const struct walle_lg_reveal_mask_request request  = {
         .target_width   = (uint32_t)output->render.width,
         .target_height  = (uint32_t)output->render.height,
         .center_x       = process_capture ? REVEAL_PROCESS_CAPTURE_CENTER_X : output->t_center_x,
         .center_y       = process_capture ? REVEAL_PROCESS_CAPTURE_CENTER_Y : output->t_center_y,
         .maximum_radius = output->reveal_maximum_radius,
         .progress       = reveal_state,
         /* The live transition is an ANIMATION, so its circle interpolates
          * between the two rounded endpoint rects; the process capture drives
          * an explicit progress, which is the rounded rect at that progress and
          * is what the byte-exact ladder scores. */
         .presentation_geometry
         = !process_capture || state->reveal_process_capture_presentation,
    };
    if (!walle_lg_reveal_mask_geometry_construct(&request, &geometry)) {
        stop_failed_transition(output, "public reveal geometry construction failed");
#if defined(WALLE_TRACY)
        TracyCZoneEnd(tracy_transition_frame);
#endif
        return RENDER_FRAME_FAILED;
    }

    bool                  first_boot = !(output->render.flags & F_BOOT_COMPLETE);
    /* The material's clock, which is the reveal progress unless the capture
      * asked to drive it separately - see the field's comment. */
    float material_progress = progress;
    if (process_capture) {
        if (state->reveal_process_capture_material_count > 0u)
            material_progress
                = state->reveal_process_capture_material_values
                      [output->reveal_process_capture_state
                       % state->reveal_process_capture_material_count];
        else if (state->reveal_process_capture_material_progress >= 0.0f)
            material_progress = state->reveal_process_capture_material_progress;
    }
    struct walle_vk_frame frame      = {
             .geometry          = &geometry,
             .progress          = material_progress,
             .variant           = output->glass_variant == GLASS_VARIANT_REGULAR ? 1.0f : 0.0f,
             .center_top_left_x = geometry.circle.center[0],
             .center_top_left_y = geometry.circle.center[1],
             .radius            = geometry.circle.radius,
             .first_boot        = first_boot,
             .appearance = glass_appearance_value(output),
             .output_scale
             = process_capture
                   ? (float)state->reveal_process_capture_backing_scale
                   : (float)(output->scale > 0 ? output->scale : 1),
             .tint = {output->glass_tint[0], output->glass_tint[1], output->glass_tint[2]},
             /* Apple's `identity` variant leaves content unaffected, which is
              * exactly the mask-weighted crossfade the hardware corpus
              * measures - so it shares the gate's composition path.  The gate
              * scores that path; WALLE_COMPOSE_MATERIAL captures the shipped
              * Liquid Glass material instead, for visual inspection. */
             .apple_reveal_blend
        = output->glass_variant == GLASS_VARIANT_IDENTITY
          || (process_capture && getenv("WALLE_COMPOSE_MATERIAL") == nullptr),
             .mask_readback     = process_capture ? output->reveal_process_capture_pixels : nullptr,
             .mask_readback_size
        = process_capture ? (size_t)REVEAL_PROCESS_CAPTURE_WIDTH * REVEAL_PROCESS_CAPTURE_HEIGHT
                               : 0,
             .composition_readback
        = process_capture ? output->reveal_process_composition_pixels : nullptr,
             .composition_readback_size
        = process_capture
                   ? (size_t)REVEAL_PROCESS_CAPTURE_WIDTH * REVEAL_PROCESS_CAPTURE_HEIGHT * 4
                   : 0,
    };
#if defined(WALLE_TRACY)
    TracyCZoneN(tracy_present, "Vulkan render and present", true);
#endif
    enum walle_vk_frame_status status = walle_vk_output_render(output->render.vk_output, &frame);
#if defined(WALLE_TRACY)
    TracyCZoneEnd(tracy_present);
#endif
    if (status == WALLE_VK_FRAME_FATAL) {
        stop_failed_transition(output, "Vulkan reveal/composition/present failed");
#if defined(WALLE_TRACY)
        TracyCZoneEnd(tracy_transition_frame);
#endif
        return RENDER_FRAME_FAILED;
    }
    if (status == WALLE_VK_FRAME_RETRY) {
        if (output->frame_callback)
            wl_callback_destroy(output->frame_callback);
        output->frame_callback = wl_surface_frame(output->surface);
        wl_callback_add_listener(output->frame_callback, &frame_listener, output);
        wl_surface_commit(output->surface);
#if defined(WALLE_TRACY)
        TracyCZoneEnd(tracy_transition_frame);
#endif
        return RENDER_FRAME_RETRY;
    }

    if (process_capture) {
        if (!write_reveal_process_capture(output)) {
            char reason[160];
            snprintf(reason,
                     sizeof reason,
                     "could not create state-%04u.r8: %s",
                     output->reveal_process_capture_state,
                     strerror(errno));
            stop_failed_transition(output, reason);
#if defined(WALLE_TRACY)
            TracyCZoneEnd(tracy_transition_frame);
#endif
            return RENDER_FRAME_FAILED;
        }
        if (!write_reveal_process_composition(output)) {
            stop_failed_transition(output, "could not create the composition state file");
#if defined(WALLE_TRACY)
            TracyCZoneEnd(tracy_transition_frame);
#endif
            return RENDER_FRAME_FAILED;
        }
        ++state->reveal_process_capture_swap_count;
        if (!finished)
            ++output->reveal_process_capture_state;
    }
#if defined(WALLE_TRACY)
    TracyCPlotF("transition progress", progress);
    TracyCFrameMarkNamed("Walle Vulkan transition frame");
#endif

    if (finished) {
        output->render.t_state = T_STATE_IDLE;
        if (output->frame_callback) {
            wl_callback_destroy(output->frame_callback);
            output->frame_callback = nullptr;
        }
        wl_surface_commit(output->surface);
        walle_vk_output_promote(output->render.vk_output);
        release_render_result(&output->current_source);
        output->current_source = output->pending_source;
        output->pending_source = (struct render_result){.std_fd = -1, .glass_fd = -1};
    } else {
        if (output->frame_callback)
            wl_callback_destroy(output->frame_callback);
        output->frame_callback = wl_surface_frame(output->surface);
        wl_callback_add_listener(output->frame_callback, &frame_listener, output);
        wl_surface_commit(output->surface);
    }
    if (process_capture && finished) {
        state->reveal_process_capture_status   = 0;
        state->reveal_process_capture_complete = true;
        char progress_law[96];
        if (state->reveal_process_capture_single) {
            snprintf(progress_law,
                     sizeof progress_law,
                     "explicit=%s",
                     state->reveal_process_capture_progress_text);
        } else {
            snprintf(progress_law, sizeof progress_law, "state/64");
        }
        printf(
            "walleExecutableProcessRendered=true\n"
            "walleLayerShellSurfaceRendered=true\n"
            "walleRenderer=Vulkan-1.4-Slang-SPIR-V-1.6\n"
            "revealMaskProcessCaptureStates=%u\n"
            "revealMaskProcessCaptureSwaps=%u\n"
            "revealMaskProcessCaptureCallbacks=%u\n"
            "revealMaskProcessCaptureDimensions=2048x2048\n"
            "revealMaskProcessCaptureCenterTopLeft=512.0,614.4\n"
            "revealMaskProcessCaptureProgress=%s\n"
            "revealMaskProcessCaptureFormat=R8-top-left-row-major\n"
            "compositionProcessCaptureStates=%u\n"
            "compositionProcessCaptureFormat=BGRA8-top-left-row-major\n"
            "revealMaskProcessCaptureComplete=true\n",
            state->reveal_process_capture_single
                ? state->reveal_process_capture_progress_count
                : REVEAL_PROCESS_CAPTURE_STATE_COUNT,
            state->reveal_process_capture_swap_count,
            state->reveal_process_capture_callback_count,
            progress_law,
            state->reveal_process_capture_single
                ? state->reveal_process_capture_progress_count
                : REVEAL_PROCESS_CAPTURE_STATE_COUNT);
        fflush(stdout);
    }
#if defined(WALLE_TRACY)
    TracyCZoneEnd(tracy_transition_frame);
#endif
    return RENDER_FRAME_PRESENTED;
}

static void frame_callback_handler(void* data, struct wl_callback* callback, uint32_t time)
{
    (void)time;
    (void)callback;
    auto output = (struct wallpaper_output*)data;
    if (output->reveal_process_capture_active)
        ++output->render.state->reveal_process_capture_callback_count;
    if (render_frame(output) == RENDER_FRAME_FAILED && output->reveal_process_capture_owned)
        reveal_process_capture_fail(output->render.state, "capture frame callback did not render");
}

/* -- Same-trigger transition sync ----------------------------------------
 *
 * Outputs re-rendered by one trigger (a config reload, rotation timers firing
 * in the same loop turn) start their transitions TOGETHER.  Without this, each
 * output starts the moment its own render lands, and a cache hit on one output
 * against a cold bake on the other reads as the two displays desynchronizing -
 * a variant change re-keys the bake cache, so the changed output lags every
 * trigger until its whole list has been re-baked once.  Members arm as usual
 * but withhold the first frame until the group's last render arrives. */

/* Releasing a group does NOT render: event handlers (finalize, the reload
 * loop) must never run GPU submits or fence waits inline.  Held outputs are
 * queued and the main loop flushes them once per turn, after every handler
 * has run. */
static void transition_sync_start_held(struct wallpaper_state* state)
{
    struct wallpaper_output* o;
    wl_list_for_each(o, &state->outputs, link)
    {
        if (!o->sync_held)
            continue;
        o->sync_held                        = false;
        o->sync_start_ready                 = true;
        state->transition_sync_starts_ready = true;
    }
}

/* One call per main-loop turn, after all event handlers. */
static void transition_sync_flush_starts(struct wallpaper_state* state)
{
    if (!state->transition_sync_starts_ready)
        return;
    state->transition_sync_starts_ready = false;
    size_t                   started    = 0;
    struct wallpaper_output* o;
    wl_list_for_each(o, &state->outputs, link)
    {
        if (!o->sync_start_ready)
            continue;
        o->sync_start_ready = false;
        if (o->render.flags & F_DEAD)
            continue;
        if (o->frame_callback) {
            wl_callback_destroy(o->frame_callback);
            o->frame_callback = nullptr;
        }
        uint64_t t = trace_now_ns();
        (void)render_frame(o);
        warn_slow("first transition frame", o->name, t);
        started++;
    }
    if (started > 1)
        printf("[SYNC] %zu transitions started together\n", started);
}

static void transition_sync_open_turn_group(struct wallpaper_state* state)
{
    if (state->transition_sync_turn_open)
        return;
    state->transition_sync_turn_open = true;
    state->transition_sync_gen++;
    /* A new trigger supersedes a group still waiting on a straggler: release
     * what is held and drop stale memberships so nothing waits on the old
     * generation. */
    struct wallpaper_output* o;
    wl_list_for_each(o, &state->outputs, link)
    {
        if (o->sync_member && o->sync_gen != state->transition_sync_gen)
            o->sync_member = false;
    }
    transition_sync_start_held(state);
    state->transition_sync_pending = 0;
}

static void transition_sync_mark(struct wallpaper_state* state, struct wallpaper_output* output)
{
    transition_sync_open_turn_group(state);
    output->sync_gen    = state->transition_sync_gen;
    output->sync_member = true;
    state->transition_sync_pending++;
}

/* The output leaves its group without holding (its render failed, it was
 * torn down, or it swaps without a transition).  Completes the group if this
 * was the last pending render. */
static void transition_sync_unmark(struct wallpaper_state* state, struct wallpaper_output* output)
{
    if (!output->sync_member)
        return;
    output->sync_member = false;
    if (output->sync_gen != state->transition_sync_gen)
        return;
    if (--state->transition_sync_pending <= 0)
        transition_sync_start_held(state);
}

/* Called at arm time.  Returns true when the sync machinery owns the first
 * frame - the output stays held, or its arrival completed the group and
 * transition_sync_start_held submitted for everyone just now.  Returns false
 * when the caller should submit normally (never a current-group member, or a
 * capture output). */
[[nodiscard]]
static bool transition_sync_hold(struct wallpaper_state* state, struct wallpaper_output* output)
{
    if (!output->sync_member || output->sync_gen != state->transition_sync_gen
        || output->reveal_process_capture_owned) {
        transition_sync_unmark(state, output);
        return false;
    }
    output->sync_member = false;
    output->sync_held   = true;
    if (--state->transition_sync_pending <= 0)
        transition_sync_start_held(state); /* clears sync_held, frames submitted */
    return true;
}

static void set_reveal_origin(struct wallpaper_output* output,
                              double                   center_top_left_x,
                              double                   center_top_left_y)
{
    output->t_center_x = (float)center_top_left_x;
    output->t_center_y = (float)center_top_left_y;

    double d1 = hypot(center_top_left_x, center_top_left_y);
    double d2 = hypot((double)output->render.width - center_top_left_x, center_top_left_y);
    double d3 = hypot(center_top_left_x, (double)output->render.height - center_top_left_y);
    double d4 = hypot((double)output->render.width - center_top_left_x,
                      (double)output->render.height - center_top_left_y);
    output->reveal_maximum_radius = fmax(d1, fmax(d2, fmax(d3, d4))) * REVEAL_RADIUS_MARGIN;
}

static void finalize_render(struct wallpaper_output* output)
{
    if (!(output->render.flags & F_THREAD_ACTIVE))
        return;
    pthread_join(output->render_thread, nullptr);
    output->render.flags &= ~F_THREAD_ACTIVE;

    struct wallpaper_state* state = output->render.state;

    struct render_result res = output->async_result;
    output->async_result     = (struct render_result){.std_fd = -1, .glass_fd = -1};
    if (res.done_ns)
        warn_slow("render completion dispatch", output->name, res.done_ns);

    if (output->render.flags & F_DEAD) {
        transition_sync_unmark(state, output);
        release_render_result(&res);
        /* Deferred teardown: the thread is joined, its completion CQE is
         * consumed — the event slot (and its fd) can go now. */
        if (output->slot_event >= 0) {
            ev_slot_release(&state->ev, (size_t)output->slot_event);
            output->slot_event = -1;
            output->event_fd   = -1;
        }
        return;
    }

    /* Deferred reload: config changed while render was in-flight. Discard
     * the stale result; a section that vanished freezes the output. */
    if (output->pending_reload) {
        if (output->reveal_process_capture_owned) {
            release_render_result(&res);
            reveal_process_capture_fail(state, "configuration changed during capture");
            return;
        }
        output->pending_reload    = false;
        struct output_config* cfg = get_config_for_output(state, output->name);
        if (!cfg || cfg->items.count == 0) {
            printf("[CONFIG] Output '%s' no longer configured; freezing.\n", output->name);
            cfg = &g_frozen_config;
        }
        apply_config_to_output(output, cfg);
        update_wallpaper(output);
        release_render_result(&res);
        return;
    }

    if (!res.success) {
        fprintf(stderr,
                "[RENDER] wallpaper preparation failed for '%s' (%s); keeping "
                "the current wallpaper\n",
                output->num_items ? output->items[output->current_item_index].filename
                                  : "<empty list>",
                output->name ? output->name : "-");
        transition_sync_unmark(state, output);
        release_render_result(&res);
        if (output->reveal_process_capture_owned)
            reveal_process_capture_fail(state, "wallpaper preparation failed");
        return;
    }

    if (res.standard.width != output->render.width
        || res.standard.height != output->render.height) {
        release_render_result(&res);
        if (output->reveal_process_capture_owned) {
            reveal_process_capture_fail(state, "surface size changed during capture");
            return;
        }
        /* The surface was reconfigured while this render was in flight;
         * relaunch at the current size instead of dropping the redraw. */
        launch_async_render(output);
        if (!(output->render.flags & F_THREAD_ACTIVE))
            transition_sync_unmark(state, output);
        return;
    }

    bool                              first_boot = !(output->render.flags & F_BOOT_COMPLETE);
    const struct walle_vk_image_layer standard   = {
          .offset = res.standard.offset,
          .size   = res.standard.size,
          .width  = res.standard.width,
          .height = res.standard.height,
    };
    const struct walle_vk_image_layer glass = {
        .offset = res.glass.offset,
        .size   = res.glass.size,
        .width  = res.glass.width,
        .height = res.glass.height,
    };
    bool restored = first_boot;
    if (!first_boot && output->current_source.std_fd >= 0) {
        const struct walle_vk_image_layer current_standard = {
            .offset = output->current_source.standard.offset,
            .size   = output->current_source.standard.size,
            .width  = output->current_source.standard.width,
            .height = output->current_source.standard.height,
        };
        const struct walle_vk_image_layer current_glass = {
            .offset = output->current_source.glass.offset,
            .size   = output->current_source.glass.size,
            .width  = output->current_source.glass.width,
            .height = output->current_source.glass.height,
        };
        uint64_t t_restore = trace_now_ns();
        restored = output->render.vk_output
                   && walle_vk_output_restore_current(output->render.vk_output,
                                                      output->current_source.std_fd,
                                                      &current_standard,
                                                      output->current_source.glass_fd,
                                                      &current_glass);
        warn_slow("current-wallpaper restore", output->name, t_restore);
    }
    struct walle_vk_glass_bake bake;
    bool                       gpu_bake = glass_bake_descriptor(
        output->glass_variant,
        glass_appearance_value(output),
        output->reveal_process_capture_owned
            ? state->reveal_process_capture_backing_scale
            : (output->scale > 0 ? output->scale : 1),
        &bake);
    uint64_t t_upload  = trace_now_ns();
    bool     uploaded = restored && output->render.vk_output
                    && walle_vk_output_upload(output->render.vk_output,
                                              res.std_fd,
                                              &standard,
                                              res.glass_fd,
                                              &glass,
                                              gpu_bake ? &bake : nullptr);
    warn_slow("wallpaper upload", output->name, t_upload);

    if (!uploaded) {
        transition_sync_unmark(state, output);
        release_render_result(&res);
        walle_vk_output_abort_transition(output->render.vk_output);
        if (output->reveal_process_capture_owned)
            reveal_process_capture_fail(state, "wallpaper texture upload failed");
        return;
    }
    release_render_result(&output->pending_source);
    output->pending_source = res;
    res                    = (struct render_result){.std_fd = -1, .glass_fd = -1};

    /* Keep the cache watermark honored on long-lived daemons. */
    if (++state->renders_since_gc >= GC_RENDER_PERIOD) {
        state->renders_since_gc = 0;
        launch_cache_maintenance_service();
    }

    bool sync_held = false;
    if ((output->render.flags & F_TRANSITION_ON) && !first_boot) {
        output->render.t_state = T_STATE_ARMED;

        if (output->reveal_process_capture_owned) {
            output->reveal_process_capture_state  = 0;
            output->reveal_process_capture_active = true;
            set_reveal_origin(
                output, REVEAL_PROCESS_CAPTURE_CENTER_X, REVEAL_PROCESS_CAPTURE_CENTER_Y);
        } else {
            int cx = (int)xoshiro256pp_bounded(&g_rng, output->render.width / 2)
                     + output->render.width / 4;
            int cy = (int)xoshiro256pp_bounded(&g_rng, output->render.height / 2)
                     + output->render.height / 4;

            /* Debug/measurement override: WALLE_DEBUG_CENTER="x,y" pins the
             * transition origin (buffer px, top-left origin) so external
             * instrumentation can align with the circle deterministically. */
            const char* dbg_center = getenv("WALLE_DEBUG_CENTER");
            if (dbg_center) {
                int dx_, dy_;
                if (sscanf(dbg_center, "%d,%d", &dx_, &dy_) == 2) {
                    cx = dx_;
                    cy = dy_;
                }
            }

            set_reveal_origin(output, (double)cx, (double)cy);
        }

        float duration              = output->transition_duration > 0 ? output->transition_duration
                                                                      : DEFAULT_TRANSITION_DUR;
        output->render.duration_inv = 1.0f / duration;
        sync_held                   = transition_sync_hold(state, output);
    } else {
        transition_sync_unmark(state, output);
        output->render.t_state = T_STATE_RUNNING;

        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        float duration              = output->transition_duration > 0 ? output->transition_duration
                                                                      : DEFAULT_TRANSITION_DUR;
        output->render.duration_inv = 1.0f / duration;

        uint64_t offset_ns           = (uint64_t)(duration + 1.0f) * 1000000000ULL;
        uint64_t now_ns              = (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
        output->render.anim_start_ns = now_ns - offset_ns;

        /* Single frame at Time=1: the circle mask must already cover the
         * whole screen, because nothing outside the circle ever fades to the
         * incoming image (a tiny radius would leave the OLD wallpaper up). */
        set_reveal_origin(
            output, (double)output->render.width * 0.5, (double)output->render.height * 0.5);
    }

    if (first_boot) {
        enum render_frame_result result = render_frame(output);
        if (result == RENDER_FRAME_PRESENTED) {
            output->render.flags |= F_BOOT_COMPLETE;
            if (output->reveal_process_capture_owned)
                update_wallpaper(output);
        } else if (result == RENDER_FRAME_FAILED && output->reveal_process_capture_owned)
            reveal_process_capture_fail(state, "capture first-boot frame did not render");
    } else if (!sync_held) {
        if (output->frame_callback)
            wl_callback_destroy(output->frame_callback);
        output->frame_callback = nullptr;
        /* Submit the first frame immediately. Waiting for a callback on a
         * newly recreated surface can defer visible work until the compositor
         * next happens to repaint an otherwise static background. */
        uint64_t t_first = trace_now_ns();
        if (render_frame(output) == RENDER_FRAME_FAILED && output->reveal_process_capture_owned)
            reveal_process_capture_fail(state, "initial capture state did not render");
        warn_slow("first transition frame", output->name, t_first);
    }
}

/* -- Output Lifecycle ---------------------------------------------------- */

static void destroy_output(struct wallpaper_output* o)
{
    if (!o || (o->render.flags & F_DEAD))
        return;
    o->render.flags |= F_DEAD;

    dbg_print("[INFO] Deactivating output: '%s' (ID: %u)",
              o->name ? o->name : "unknown",
              o->wl_output_name);

    struct wallpaper_state* state = o->render.state;
    if (o->reveal_process_capture_owned)
        reveal_process_capture_fail(state, "capture output was removed before completion");
    o->reveal_process_capture_active = false;
    free(o->reveal_process_capture_pixels);
    o->reveal_process_capture_pixels = nullptr;
    free(o->reveal_process_composition_pixels);
    o->reveal_process_composition_pixels = nullptr;
    release_render_result(&o->current_source);
    release_render_result(&o->pending_source);

    /* Slots own their fds: cancel-then-close makes a CQE-after-free
     * impossible (generation bump) before the fd is released. */
    if (o->slot_timer >= 0) {
        ev_slot_release(&state->ev, (size_t)o->slot_timer);
        o->slot_timer = -1;
        o->timer_fd   = -1;
    }

    if (!(o->render.flags & F_THREAD_ACTIVE)) {
        if (o->slot_event >= 0) {
            ev_slot_release(&state->ev, (size_t)o->slot_event);
            o->slot_event = -1;
            o->event_fd   = -1;
        }
        release_render_result(&o->async_result);
    } else {
        /* Render thread in flight: keep the event slot armed so its
         * completion CQE drives finalize_render's join + deferred cleanup. */
        dbg_print("[INFO] Render thread active on '%s'. Deferring cleanup.", o->name);
    }

    if (o->render.vk_output) {
        walle_vk_output_destroy(o->render.vk_output);
        o->render.vk_output = nullptr;
        o->render.flags &= ~F_RENDERER_INIT;
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
    o->job_w         = o->render.width;
    o->job_h         = o->render.height;
    o->job_variant   = o->glass_variant;
    o->job_lightness = glass_appearance_value(o);
    o->job_scale     = o->render.state->reveal_process_capture
                           ? o->render.state->reveal_process_capture_backing_scale
                           : (o->scale > 0 ? o->scale : 1);
    if (pthread_create(&o->render_thread, nullptr, render_thread_worker, o) == 0) {
        o->render.flags |= F_THREAD_ACTIVE;
    } else if (o->reveal_process_capture_owned) {
        reveal_process_capture_fail(o->render.state, "could not start wallpaper preparation");
    }
}

static void update_wallpaper(struct wallpaper_output* o)
{
    if ((o->render.flags & F_DEAD) || (o->render.flags & F_THREAD_ACTIVE))
        return;
    if (o->num_items == 0)
        return; /* hot reload can empty the list; %0 below would be UB */
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
    if (o->render.flags & F_THREAD_ACTIVE)
        transition_sync_mark(o->render.state, o);
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
    if (g_config_override) {
        if (access(g_config_override, R_OK) == 0)
            return strdup(g_config_override);
        fprintf(
            stderr, "FATAL: config '%s' is not readable: %s\n", g_config_override, strerror(errno));
        return nullptr;
    }

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

    if (!*final_path) { /* e.g. "files = $UNSET_VAR" expands to nothing */
        free(copy);
        free(expanded);
        return true;
    }

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
    *new_oc = (struct output_config){.transition_on       = false,
                                     .gamemode            = true,
                                     .transition_duration = DEFAULT_TRANSITION_DUR,
                                     /* Untinted: a zeroed tint would read as
                                      * "tinted black", not "no tint". */
                                     .tint                = {-1.0f, 0.0f, 0.0f}};
    new_oc->output_name = strdup(name);
    if (!new_oc->output_name) {
        free(new_oc);
        return nullptr;
    }
    init_item_list(&new_oc->items);
    wl_list_insert(list, &new_oc->link);
    return new_oc;
}

static int parse_int_setting(const char* value, int lo, int hi, int fallback)
{
    errno     = 0;
    char* end = nullptr;
    long  v   = strtol(value, &end, 10);
    if (errno != 0 || end == value || v < lo || v > hi) {
        fprintf(stderr,
                "[CONFIG] Invalid integer '%s' (allowed %d..%d); using %d\n",
                value,
                lo,
                hi,
                fallback);
        return fallback;
    }
    return (int)v;
}

static float parse_duration_setting(const char* value)
{
    errno     = 0;
    char* end = nullptr;
    float v   = strtof(value, &end);
    if (errno != 0 || end == value || !isfinite(v) || v <= 0.0f || v > 600.0f) {
        fprintf(stderr,
                "[CONFIG] Invalid transition_duration '%s'; using %.2fs\n",
                value,
                (double)DEFAULT_TRANSITION_DUR);
        return DEFAULT_TRANSITION_DUR;
    }
    return v;
}

static int config_handler(void* user, const char* section, const char* name, const char* value)
{
    auto ctx = (struct config_parse_ctx*)user;
    if (strcasecmp(section, "walle") == 0) {
        if (ctx->renderer_device_selector && strcasecmp(name, "vulkan_device") == 0) {
            char* selector = strdup(value);
            if (!selector)
                return 0;
            free(*ctx->renderer_device_selector);
            *ctx->renderer_device_selector = selector;
        }
        return 1;
    }
    auto oc = get_or_create_config_in_list(ctx->config_list, section);
    if (!oc)
        return 0;

    if (strcasecmp(name, "files") == 0 || strcasecmp(name, "paths") == 0) {
        process_single_config_entry(&oc->items, value);
    } else if (strcasecmp(name, "timeout") == 0) {
        oc->timeout = parse_int_setting(value, 0, 366 * 24 * 3600, 0);
    } else if (strcasecmp(name, "randomize") == 0) {
        oc->randomize = (strcmp(value, "true") == 0 || strcmp(value, "1") == 0);
    } else if (strcasecmp(name, "transition") == 0) {
        oc->transition_on = (strcmp(value, "true") == 0 || strcmp(value, "1") == 0);
    } else if (strcasecmp(name, "transition_duration") == 0) {
        oc->transition_duration = parse_duration_setting(value);
    } else if (strcasecmp(name, "tint") == 0) {
        unsigned r, g, b;
        if (strcasecmp(value, "none") == 0) {
            oc->tint[0] = -1.0f;
        } else if (sscanf(value, " #%2x%2x%2x", &r, &g, &b) == 3
                   || sscanf(value, " %2x%2x%2x", &r, &g, &b) == 3) {
            oc->tint[0] = (float)r / 255.0f;
            oc->tint[1] = (float)g / 255.0f;
            oc->tint[2] = (float)b / 255.0f;
        } else {
            fprintf(stderr, "[CONFIG] Unknown tint '%s' (#RRGGBB|none); using none\n", value);
            oc->tint[0] = -1.0f;
        }
    } else if (strcasecmp(name, "appearance") == 0) {
        if (strcasecmp(value, "light") == 0) {
            oc->appearance = GLASS_APPEARANCE_LIGHT;
        } else if (strcasecmp(value, "dark") == 0) {
            oc->appearance = GLASS_APPEARANCE_DARK;
        } else if (strcasecmp(value, "auto") == 0) {
            oc->appearance = GLASS_APPEARANCE_AUTO;
        } else {
            fprintf(stderr,
                    "[CONFIG] Unknown appearance '%s' (light|dark|auto); using auto\n",
                    value);
            oc->appearance = GLASS_APPEARANCE_AUTO;
        }
    } else if (strcasecmp(name, "transition_variant") == 0) {
        if (strcasecmp(value, "regular") == 0) {
            oc->variant = GLASS_VARIANT_REGULAR;
        } else if (strcasecmp(value, "identity") == 0) {
            oc->variant = GLASS_VARIANT_IDENTITY;
        } else if (strcasecmp(value, "clear") == 0) {
            oc->variant = GLASS_VARIANT_CLEAR;
        } else {
            fprintf(stderr,
                    "[CONFIG] Unknown transition_variant '%s' (regular|clear|identity); "
                    "using clear\n",
                    value);
            oc->variant = GLASS_VARIANT_CLEAR;
        }
    } else if (strcasecmp(name, "gamemode") == 0) {
        oc->gamemode = (strcmp(value, "true") == 0 || strcmp(value, "1") == 0);
    }
    return 1;
}

/* -- Config Application -------------------------------------------------- */

/* (Re)create the rotation timerfd and its event-core slot. TFD_NONBLOCK is
 * load-bearing: a disarm/re-arm in the same loop turn as an expiry must make
 * the drain read return EAGAIN instead of blocking the daemon. */
static void setup_rotation_timer(struct wallpaper_output* o)
{
    struct wallpaper_state* state = o->render.state;
    if (o->slot_timer >= 0) {
        ev_slot_release(&state->ev, (size_t)o->slot_timer);
        o->slot_timer = -1;
        o->timer_fd   = -1;
    }
    if (o->num_items <= 1 || o->timeout <= 0)
        return;

    int tfd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC | TFD_NONBLOCK);
    if (tfd < 0) {
        fprintf(stderr, "[ERROR] timerfd_create for %s: %s\n", o->name, strerror(errno));
        return;
    }
    int slot = ev_slot_alloc(&state->ev, EV_TIMER, tfd, POLLIN, o);
    if (slot < 0) {
        close(tfd);
        return;
    }
    o->timer_fd   = tfd;
    o->slot_timer = slot;
    arm_rotation_timer(o, state->gamemode_active && o->gamemode_enabled);
}

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
    output->glass_variant       = config->variant;
    output->glass_appearance    = config->appearance;
    memcpy(output->glass_tint, config->tint, sizeof output->glass_tint);

    if (output->current_item_index >= output->num_items)
        output->current_item_index = 0;

    setup_rotation_timer(output);
}

static void reload_global_config(struct wallpaper_state* state)
{
    if (!state->config_path)
        return;
    printf("[CONFIG] Detected change. Reloading...\n");

    struct wl_list new_configs;
    wl_list_init(&new_configs);
    struct config_parse_ctx ctx = {.config_list = &new_configs};

    int perr = ini_parse(state->config_path, config_handler, &ctx);
    if (perr != 0) {
        if (perr > 0)
            fprintf(stderr, "[CONFIG] Parse error at line %d. Keeping old config.\n", perr);
        else
            fprintf(stderr, "[CONFIG] Could not read config. Keeping old config.\n");
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
        if (cfg && cfg->items.count > 0) {
            if (!output->surface && (output->render.flags & F_INITIALIZED)) {
                /* Had no usable config at discovery and was parked without a
                 * surface: a hot-added section must activate it now. */
                output->render.flags &= ~F_INITIALIZED;
                initialize_output(output);
            } else if (output->render.flags & F_THREAD_ACTIVE) {
                output->pending_reload = true;
            } else {
                uint64_t t = trace_now_ns();
                apply_config_to_output(output, cfg);
                update_wallpaper(output);
                warn_slow("reload update", output->name, t);
            }
        } else if (output->surface) {
            /* Section removed or emptied: stop rotating, keep the last
             * frame. NEVER free the item list under an active render thread
             * — defer to finalize_render like any other reload. */
            if (output->render.flags & F_THREAD_ACTIVE) {
                output->pending_reload = true;
            } else {
                printf("[CONFIG] Output '%s' no longer configured; freezing.\n", output->name);
                apply_config_to_output(output, &g_frozen_config);
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

[[nodiscard]]
static bool scaled_buffer_dimensions(uint32_t logical_width,
                                     uint32_t logical_height,
                                     int32_t  scale,
                                     uint32_t maximum_image_dimension,
                                     int32_t* buffer_width,
                                     int32_t* buffer_height)
{
    int64_t scaled_width;
    int64_t scaled_height;
    if (logical_width == 0 || logical_height == 0 || logical_width > INT32_MAX
        || logical_height > INT32_MAX || scale <= 0 || maximum_image_dimension == 0
        || ckd_mul(&scaled_width, (int64_t)logical_width, (int64_t)scale)
        || ckd_mul(&scaled_height, (int64_t)logical_height, (int64_t)scale)
        || scaled_width > INT32_MAX || scaled_height > INT32_MAX
        || scaled_width > maximum_image_dimension || scaled_height > maximum_image_dimension) {
        return false;
    }
    *buffer_width  = (int32_t)scaled_width;
    *buffer_height = (int32_t)scaled_height;
    return true;
}

static void layer_surface_configure(
    void* data, struct zwlr_layer_surface_v1* surf, uint32_t serial, uint32_t w, uint32_t h)
{
    auto output = (struct wallpaper_output*)data;
    dbg_print("layer_surface_configure: %ux%u", w, h);
    zwlr_layer_surface_v1_ack_configure(surf, serial);
    if (output->render.flags & F_DEAD)
        return;

    struct wallpaper_state* state = output->render.state;
    if (w == 0 || h == 0) {
        if (state->reveal_process_capture) {
            reveal_process_capture_fail(state, "compositor returned a zero-sized capture surface");
            destroy_output(output);
        }
        return;
    }
    bool process_capture = state->reveal_process_capture;
    if (process_capture
        && (w != REVEAL_PROCESS_CAPTURE_WIDTH || h != REVEAL_PROCESS_CAPTURE_HEIGHT)) {
        reveal_process_capture_fail(state, "compositor rejected the canonical 2048x2048 size");
        destroy_output(output);
        return;
    }
    if (process_capture && (output->render.flags & F_CONFIGURED))
        return;

    int32_t scale = process_capture ? 1 : (output->scale > 0 ? output->scale : 1);
    int32_t buffer_width;
    int32_t buffer_height;
    if (!scaled_buffer_dimensions(
            w, h, scale, state->vk_max_image_dimension, &buffer_width, &buffer_height)) {
        fprintf(stderr,
                "[Vulkan] Rejecting unsupported layer size %ux%u at scale %d for %s.\n",
                w,
                h,
                scale,
                output->name);
        reveal_process_capture_fail(state, "capture surface dimensions exceed Vulkan limits");
        destroy_output(output);
        return;
    }

    bool size_changed
        = output->render.width != buffer_width || output->render.height != buffer_height;
    output->logical_w     = (int32_t)w;
    output->logical_h     = (int32_t)h;
    output->render.width  = buffer_width;
    output->render.height = buffer_height;
    output->render.flags |= F_CONFIGURED;
    if (wl_proxy_get_version((struct wl_proxy*)output->surface)
        >= WL_SURFACE_SET_BUFFER_SCALE_SINCE_VERSION)
        wl_surface_set_buffer_scale(output->surface, scale);

    bool renderer_ready;
    if (!output->render.vk_output) {
        renderer_ready = walle_vk_output_create(state->vk_renderer,
                                                output->surface,
                                                (uint32_t)buffer_width,
                                                (uint32_t)buffer_height,
                                                process_capture,
                                                &output->render.vk_output);
        if (renderer_ready) {
            output->render.flags |= F_RENDERER_INIT;
            state->vk_max_image_dimension
                = walle_vk_renderer_max_image_dimension(state->vk_renderer);
        }
    } else {
        renderer_ready = !size_changed
                         || walle_vk_output_resize(output->render.vk_output,
                                                   (uint32_t)buffer_width,
                                                   (uint32_t)buffer_height);
        if (size_changed)
            output->render.flags &= ~F_BOOT_COMPLETE;
    }
    if (!renderer_ready) {
        reveal_process_capture_fail(state, "Vulkan Wayland output creation/resize failed");
        destroy_output(output);
        return;
    }

    if (process_capture && !output->reveal_process_capture_pixels) {
        size_t byte_count;
        if (ckd_mul(&byte_count,
                    (size_t)REVEAL_PROCESS_CAPTURE_WIDTH,
                    (size_t)REVEAL_PROCESS_CAPTURE_HEIGHT)) {
            reveal_process_capture_fail(state, "capture buffer size overflow");
            destroy_output(output);
            return;
        }
        output->reveal_process_capture_pixels = malloc(byte_count);
        if (!output->reveal_process_capture_pixels) {
            reveal_process_capture_fail(state, "could not allocate the R8 readback buffer");
            destroy_output(output);
            return;
        }
        if (ckd_mul(&byte_count, byte_count, (size_t)4)) {
            reveal_process_capture_fail(state, "composition capture buffer size overflow");
            destroy_output(output);
            return;
        }
        output->reveal_process_composition_pixels = malloc(byte_count);
        if (!output->reveal_process_composition_pixels) {
            reveal_process_capture_fail(state, "could not allocate the BGRA8 readback buffer");
            destroy_output(output);
            return;
        }
    }
    if (!state->reveal_process_capture_complete)
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
static void output_handle_scale(void* data, struct wl_output*, int32_t factor)
{
    auto output = (struct wallpaper_output*)data;
    if (factor > 0)
        output->scale = factor;
}
static void output_handle_description(void*, struct wl_output*, const char*)
{
}

static void output_handle_name(void* data, struct wl_output*, const char* name)
{
    auto output = (struct wallpaper_output*)data;
    free(output->name);
    output->name = strdup(name);
    dbg_print("Discovered output: '%s'", name);
}

static void output_handle_done(void* data, struct wl_output* wl_output)
{
    (void)wl_output;
    auto output = (struct wallpaper_output*)data;
    if (!output->render.state->globals_ready)
        return;
    initialize_output(output);

    /* Scale changed after init (display settings): rescale the buffer. */
    if (!output->render.state->reveal_process_capture && !(output->render.flags & F_DEAD)
        && output->surface && output->render.vk_output && output->logical_w > 0) {
        int32_t scale = output->scale > 0 ? output->scale : 1;
        int32_t bw;
        int32_t bh;
        if (!scaled_buffer_dimensions((uint32_t)output->logical_w,
                                      (uint32_t)output->logical_h,
                                      scale,
                                      output->render.state->vk_max_image_dimension,
                                      &bw,
                                      &bh)) {
            fprintf(
                stderr, "[Vulkan] Rejecting unsupported scaled layer size for %s.\n", output->name);
            destroy_output(output);
            return;
        }
        if (bw != output->render.width || bh != output->render.height) {
            if (wl_proxy_get_version((struct wl_proxy*)output->surface)
                >= WL_SURFACE_SET_BUFFER_SCALE_SINCE_VERSION)
                wl_surface_set_buffer_scale(output->surface, scale);
            if (!walle_vk_output_resize(output->render.vk_output, (uint32_t)bw, (uint32_t)bh)) {
                destroy_output(output);
                return;
            }
            output->render.width  = bw;
            output->render.height = bh;
            output->render.flags &= ~F_BOOT_COMPLETE;
            launch_async_render(output);
        }
    }
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
    if (state->shutting_down)
        return;

    if (state->reveal_process_capture) {
        if (state->reveal_process_capture_output_claimed) {
            output->render.flags |= F_INITIALIZED;
            return;
        }
        state->reveal_process_capture_output_claimed = true;
        output->reveal_process_capture_owned         = true;
    }

    struct output_config* config = get_config_for_output(state, output->name);

    if (config && config->items.count > 0) {
        if (state->reveal_process_capture && config->items.count != 2) {
            fprintf(stderr,
                    "[REVEAL CAPTURE] The selected config must contain exactly two "
                    "wallpapers (A then B).\n");
            output->render.flags |= F_INITIALIZED;
            reveal_process_capture_fail(state, "capture config does not contain two wallpapers");
            return;
        }
        printf("[CONFIG] Applying config [%s] to output %s (%zu items)\n",
               config->output_name,
               output->name,
               config->items.count);

        struct item_list dup_list = {};
        if (!duplicate_item_list(&config->items, &dup_list)) {
            fprintf(stderr, "[FATAL] OOM duplicating item list for %s\n", output->name);
            output->render.flags |= F_INITIALIZED;
            reveal_process_capture_fail(state, "could not duplicate capture wallpaper list");
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
        output->glass_variant       = config->variant;
        output->glass_appearance    = config->appearance;
        memcpy(output->glass_tint, config->tint, sizeof output->glass_tint);
    memcpy(output->glass_tint, config->tint, sizeof output->glass_tint);
    output->glass_appearance    = config->appearance;
    memcpy(output->glass_tint, config->tint, sizeof output->glass_tint);

        if (state->reveal_process_capture) {
            output->timeout          = 0;
            output->gamemode_enabled = false;
            output->render.flags &= ~F_RANDOMIZE;
            output->render.flags |= F_TRANSITION_ON;
            output->current_item_index = 0;
        } else if (output->render.flags & F_RANDOMIZE) {
            output->current_item_index = (size_t)xoshiro256pp_bounded(&g_rng, output->num_items);
        } else {
            output->current_item_index = 0;
        }
    } else {
        printf("[INFO] No configuration for output: %s. Inactive.\n", output->name);
        output->render.flags |= F_INITIALIZED;
        reveal_process_capture_fail(state, "no configured wallpapers for capture output");
        return;
    }

    if (output->event_fd < 0) {
        int efd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        if (efd < 0) {
            fprintf(stderr, "[ERROR] eventfd failed for %s: %s\n", output->name, strerror(errno));
            goto init_failed;
        }
        int slot = ev_slot_alloc(&state->ev, EV_RENDER_DONE, efd, POLLIN, output);
        if (slot < 0) {
            close(efd);
            goto init_failed;
        }
        output->event_fd   = efd;
        output->slot_event = slot;
    }

    setup_rotation_timer(output);

    if (!state->compositor || !state->layer_shell) {
        fprintf(stderr, "[ERROR] Missing Wayland globals for %s\n", output->name);
        goto init_failed;
    }

    output->surface = wl_compositor_create_surface(state->compositor);
    if (!output->surface) {
        fprintf(stderr, "[ERROR] wl_surface creation failed for %s\n", output->name);
        goto init_failed;
    }

    /* A wallpaper is always fully opaque. Declaring that lets the compositor
     * skip alpha-blending the entire background plane every repaint and use
     * the surface for occlusion culling. */
    struct wl_region* opaque = wl_compositor_create_region(state->compositor);
    if (opaque) {
        wl_region_add(opaque, 0, 0, INT32_MAX, INT32_MAX);
        wl_surface_set_opaque_region(output->surface, opaque);
        wl_region_destroy(opaque);
    }

    if (!state->reveal_process_capture && output->scale > 1
        && wl_proxy_get_version((struct wl_proxy*)output->surface)
               >= WL_SURFACE_SET_BUFFER_SCALE_SINCE_VERSION)
        wl_surface_set_buffer_scale(output->surface, output->scale);

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

    if (state->reveal_process_capture) {
        zwlr_layer_surface_v1_set_size(
            output->layer_surface, REVEAL_PROCESS_CAPTURE_WIDTH, REVEAL_PROCESS_CAPTURE_HEIGHT);
        zwlr_layer_surface_v1_set_anchor(output->layer_surface,
                                         ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP
                                             | ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT);
    } else {
        zwlr_layer_surface_v1_set_anchor(
            output->layer_surface,
            ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP | ZWLR_LAYER_SURFACE_V1_ANCHOR_BOTTOM
                | ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT | ZWLR_LAYER_SURFACE_V1_ANCHOR_RIGHT);
    }
    zwlr_layer_surface_v1_set_exclusive_zone(output->layer_surface, -1);
    zwlr_layer_surface_v1_add_listener(output->layer_surface, &layer_surface_listener, output);

    wl_surface_commit(output->surface);
    output->render.flags |= F_INITIALIZED;
    printf("[INFO] Output initialized: '%s'\n", output->name);
    return;

init_failed:
    destroy_output(output);
}

static void registry_global(
    void* data, struct wl_registry* reg, uint32_t name, const char* interface, uint32_t ver)
{
    auto state = (struct wallpaper_state*)data;

    /* Never bind above the advertised version: the server answers that with
     * a fatal "invalid version for global" protocol error. */
    if (strcmp(interface, wl_compositor_interface.name) == 0) {
        state->compositor
            = wl_registry_bind(reg, name, &wl_compositor_interface, ver < 4 ? ver : 4);
    } else if (strcmp(interface, zwlr_layer_shell_v1_interface.name) == 0) {
        /* v3+ makes the destroy request legal at shutdown. */
        state->layer_shell
            = wl_registry_bind(reg, name, &zwlr_layer_shell_v1_interface, ver < 3 ? ver : 3);
    } else if (strcmp(interface, "zwp_linux_dmabuf_v1") == 0) {
        if (!walle_vk_renderer_bind_linux_dmabuf(state->vk_renderer, reg, name, ver))
            fprintf(stderr, "FATAL: could not bind modern linux-dmabuf feedback.\n");
    } else if (strcmp(interface, wl_output_interface.name) == 0) {
        if (state->shutting_down)
            return; /* a hotplug mid-shutdown must not resurrect the daemon */
        /* 64 here is a cache-line placement optimization for the hot render
         * block, not a type requirement (see the struct definition). */
        constexpr size_t out_align = 64;
        constexpr size_t out_size
            = (sizeof(struct wallpaper_output) + out_align - 1) & ~(out_align - 1);
        auto o = (struct wallpaper_output*)aligned_alloc(out_align, out_size);
        if (!o) {
            fprintf(stderr, "FATAL: aligned_alloc failed\n");
            return;
        }
        uint32_t v   = ver < 4 ? ver : 4;
        *o           = (struct wallpaper_output){.render         = {.state = state},
                                                 .timer_fd       = -1,
                                                 .event_fd       = -1,
                                                 .slot_event     = -1,
                                                 .slot_timer     = -1,
                                                 .scale          = 1,
                                                 .wl_output_name = name,
                                                 .async_result   = {.std_fd = -1, .glass_fd = -1},
                                                 .current_source = {.std_fd = -1, .glass_fd = -1},
                                                 .pending_source = {.std_fd = -1, .glass_fd = -1}};
        o->wl_output = wl_registry_bind(reg, name, &wl_output_interface, v);
        wl_output_add_listener(o->wl_output, &output_listener, o);
        wl_list_insert(&state->outputs, &o->link);
        if (v < 4) {
            /* No wl_output.name event before v4: this output can only match
             * the 'default'/'*' config section. */
            fprintf(stderr,
                    "[WARN] wl_output v%u has no name event; using the 'default'/'*' section.\n",
                    v);
            o->name = strdup("");
        }
        /* No done event at v1 either. During the startup burst layer_shell
         * may not be bound yet — the post-roundtrip sweep in main() picks
         * those up; hotplugged v1 outputs initialize immediately. */
        if (v < 2 && state->layer_shell)
            initialize_output(o);
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

[[nodiscard]]
static int open_empty_reveal_process_capture_directory(const char* path)
{
    int directory_fd = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (directory_fd < 0)
        return -1;

    int scan_fd = fcntl(directory_fd, F_DUPFD_CLOEXEC, 0);
    if (scan_fd < 0) {
        int saved_errno = errno;
        close(directory_fd);
        errno = saved_errno;
        return -1;
    }
    DIR* directory = fdopendir(scan_fd);
    if (!directory) {
        int saved_errno = errno;
        close(scan_fd);
        close(directory_fd);
        errno = saved_errno;
        return -1;
    }

    bool empty = true;
    errno      = 0;
    for (struct dirent* entry; (entry = readdir(directory)) != nullptr;) {
        if (strcmp(entry->d_name, ".") != 0 && strcmp(entry->d_name, "..") != 0) {
            empty = false;
            break;
        }
    }
    int saved_errno = errno;
    if (closedir(directory) < 0 && saved_errno == 0)
        saved_errno = errno;
    if (!empty && saved_errno == 0)
        saved_errno = ENOTEMPTY;
    if (saved_errno != 0) {
        close(directory_fd);
        errno = saved_errno;
        return -1;
    }
    return directory_fd;
}

static void print_usage(const char* argv0)
{
    printf(
        "Usage: %s [OPTIONS]\n"
        "\n"
        "  -c, --config <path>  use this config file instead of the XDG lookup\n"
        "      --vulkan-device <selector>\n"
        "                         auto, discrete, integrated, device index, or name substring\n"
        "  -h, --help           show this help and exit\n"
        "  -V, --version        print version and exit\n"
        "\n"
        "Reveal-mask process diagnostic:\n"
        "      --reveal-mask-process-capture <empty-directory>\n"
        "          write state-0000.r8..state-0064.r8 and composition-state-NNNN.bgra\n"
        "          (2048x2048, top-left), then exit\n"
        "      --reveal-mask-process-capture-progress <v[,v...]>\n"
        "          capture one state per explicit progress value in [0,1] instead\n"
        "          of the 65-state ladder\n"
        "      --reveal-mask-process-capture-presentation\n"
        "          capture with the animating geometry instead of the rounded one\n"
        "      --reveal-mask-process-capture-material-progress <v[,v...]>\n"
        "          drive the material's clock from <v> while the geometry stays on\n"
        "          the reveal progress, so a fully materialized element can be\n"
        "          captured at any radius; a list gives one value per captured\n"
        "          state and must match the capture progress list\n"
        "      --reveal-mask-process-capture-backing-scale <1..4>\n"
        "          device pixels per point for the 2048x2048 capture; the\n"
        "          material's radii are absolute at the corpus's 2x scale\n"
        "\n"
        "Renderer: Vulkan 1.4, offline Slang/SPIR-V 1.6 (no fallback).\n",
        argv0);
}

/* Watch the config's parent directory; editors replace files via rename, so
 * watching the file itself would miss most saves. */
static void add_config_watch(struct wallpaper_state* state)
{
    state->config_wd
        = inotify_add_watch(state->inotify_fd, state->config_dir, IN_CLOSE_WRITE | IN_MOVED_TO);
    if (state->config_wd < 0)
        fprintf(stderr,
                "[CONFIG] inotify watch on '%s' failed: %s (hot reload disabled)\n",
                state->config_dir,
                strerror(errno));
}

/* Drain the inotify fd; true when the config file changed. Re-establishes
 * the watch if the directory itself was replaced (stow/chezmoi deploys). */
static bool drain_inotify(struct wallpaper_state* state)
{
    bool                               dirty = false;
    alignas(struct inotify_event) char buf[INOTIFY_BUF_LEN];
    ssize_t                            len;
    while ((len = read(state->inotify_fd, buf, sizeof(buf))) > 0) {
        for (char* ptr = buf; ptr < buf + len;) {
            const struct inotify_event* ev = (const struct inotify_event*)ptr;
            if (ev->mask & IN_IGNORED) {
                add_config_watch(state);
                dirty = true;
            } else if (ev->len > 0 && state->config_filename
                       && strcmp(ev->name, state->config_filename) == 0) {
                dirty = true;
            }
            ptr += sizeof(struct inotify_event) + ev->len;
        }
    }
    return dirty;
}

/* Stage-1 shutdown: tear surfaces down but keep the loop running so
 * in-flight render threads are joined via their completion CQEs. */
static void begin_shutdown(struct wallpaper_state* state)
{
    if (state->shutting_down)
        return;
    printf("\n[INFO] Shutting down...\n");
    state->shutting_down = true;
    struct wallpaper_output* o;
    wl_list_for_each(o, &state->outputs, link)
    {
        destroy_output(o);
    }
}

static inline uint64_t monotonic_usec(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000000ULL + (uint64_t)now.tv_nsec / 1000ULL;
}

int main(int argc, char* argv[])
{
    enum
    {
        OPT_REVEAL_MASK_PROCESS_CAPTURE = 256,
        OPT_REVEAL_MASK_PROCESS_CAPTURE_PROGRESS,
        OPT_REVEAL_MASK_PROCESS_CAPTURE_PRESENTATION,
        OPT_REVEAL_MASK_PROCESS_CAPTURE_MATERIAL_PROGRESS,
        OPT_REVEAL_MASK_PROCESS_CAPTURE_BACKING_SCALE,
        OPT_VULKAN_DEVICE,
    };
    static const struct option LONG_OPTS[] = {
        {"config", required_argument, nullptr, 'c'},
        {"help", no_argument, nullptr, 'h'},
        {"version", no_argument, nullptr, 'V'},
        {"vulkan-device", required_argument, nullptr, OPT_VULKAN_DEVICE},
        {"reveal-mask-process-capture",
         required_argument,
         nullptr,
         OPT_REVEAL_MASK_PROCESS_CAPTURE},
        {"reveal-mask-process-capture-progress",
         required_argument,
         nullptr,
         OPT_REVEAL_MASK_PROCESS_CAPTURE_PROGRESS},
        {"reveal-mask-process-capture-presentation",
         no_argument,
         nullptr,
         OPT_REVEAL_MASK_PROCESS_CAPTURE_PRESENTATION},
        {"reveal-mask-process-capture-material-progress",
         required_argument,
         nullptr,
         OPT_REVEAL_MASK_PROCESS_CAPTURE_MATERIAL_PROGRESS},
        {"reveal-mask-process-capture-backing-scale",
         required_argument,
         nullptr,
         OPT_REVEAL_MASK_PROCESS_CAPTURE_BACKING_SCALE},
        {},
    };
    const char* reveal_process_capture_directory  = nullptr;
    bool        reveal_process_capture_presentation = false;
    const char* reveal_process_capture_progress  = nullptr;
    /* Negative means the material's clock stays tied to the reveal progress. */
    float       reveal_process_capture_material_progress = -1.0f;
    const char* reveal_process_capture_material_text     = nullptr;
    int32_t reveal_process_capture_backing_scale = 1;
    const char* vulkan_device_selector           = getenv("WALLE_VK_DEVICE");
    bool        vulkan_device_selector_locked = vulkan_device_selector && *vulkan_device_selector;
    for (int opt; (opt = getopt_long(argc, argv, "c:hV", LONG_OPTS, nullptr)) != -1;) {
        switch (opt) {
            case 'c':
                g_config_override = optarg;
                break;
            case 'h':
                print_usage(argv[0]);
                return 0;
            case 'V':
                printf("walle %s\n", WALLE_VERSION);
                return 0;
            case OPT_VULKAN_DEVICE:
                vulkan_device_selector        = optarg;
                vulkan_device_selector_locked = true;
                break;
            case OPT_REVEAL_MASK_PROCESS_CAPTURE:
                reveal_process_capture_directory = optarg;
                break;
            case OPT_REVEAL_MASK_PROCESS_CAPTURE_PRESENTATION:
                reveal_process_capture_presentation = true;
                break;
            case OPT_REVEAL_MASK_PROCESS_CAPTURE_PROGRESS:
                reveal_process_capture_progress = optarg;
                break;
            case OPT_REVEAL_MASK_PROCESS_CAPTURE_MATERIAL_PROGRESS:
                /* One value, or a comma-separated ladder of them - parsed
                 * below, once, alongside the progress ladder it pairs with. */
                reveal_process_capture_material_text = optarg;
                break;
            case OPT_REVEAL_MASK_PROCESS_CAPTURE_BACKING_SCALE: {
                char* end = nullptr;
                long value = strtol(optarg, &end, 10);
                if (end == optarg || *end != '\0' || value < 1 || value > 4) {
                    fprintf(stderr,
                            "--reveal-mask-process-capture-backing-scale "
                            "requires an integer in [1,4], got \"%s\"\n",
                            optarg);
                    return 1;
                }
                reveal_process_capture_backing_scale = (int32_t)value;
                break;
            }
            default:
                print_usage(argv[0]);
                return 1;
        }
    }
    if (optind < argc) {
        fprintf(stderr, "Unexpected argument: %s\n", argv[optind]);
        print_usage(argv[0]);
        return 1;
    }
    /* Block SIGINT/SIGTERM before ANY thread exists (vips spawns workers,
     * which inherit the mask); both are consumed via signalfd in the loop. */
    sigset_t sigmask;
    sigemptyset(&sigmask);
    sigaddset(&sigmask, SIGINT);
    sigaddset(&sigmask, SIGTERM);
    if (sigprocmask(SIG_BLOCK, &sigmask, nullptr) < 0) {
        perror("sigprocmask");
        return 1;
    }
    signal(SIGPIPE, SIG_IGN);

    if (reveal_process_capture_progress != nullptr && reveal_process_capture_directory == nullptr) {
        fprintf(stderr,
                "--reveal-mask-process-capture-progress requires "
                "--reveal-mask-process-capture\n");
        return 1;
    }
    float*   reveal_capture_progress_values = nullptr;
    uint32_t reveal_capture_progress_count  = 0;
    if (reveal_process_capture_progress != nullptr) {
        for (const char* cursor = reveal_process_capture_progress;; ) {
            char*  end   = nullptr;
            double value = strtod(cursor, &end);
            if (end == cursor || !(value >= 0.0) || !(value <= 1.0)
                || (*end != '\0' && *end != ',')) {
                fprintf(stderr,
                        "Invalid capture progress '%s': expected comma-separated "
                        "values in [0, 1]\n",
                        reveal_process_capture_progress);
                free(reveal_capture_progress_values);
                return 1;
            }
            float* grown = realloc(reveal_capture_progress_values,
                                   (reveal_capture_progress_count + 1u) * sizeof(float));
            if (grown == nullptr) {
                free(reveal_capture_progress_values);
                fprintf(stderr, "Out of memory parsing capture progress list\n");
                return 1;
            }
            reveal_capture_progress_values                                 = grown;
            reveal_capture_progress_values[reveal_capture_progress_count++] = (float)value;
            if (*end == '\0')
                break;
            cursor = end + 1;
        }
    }

    /* The material clock: one value, or one per captured state.  A single value
     * keeps the historical field so every existing gate is byte-identical. */
    float*   reveal_capture_material_values = nullptr;
    uint32_t reveal_capture_material_count  = 0;
    if (reveal_process_capture_material_text != nullptr) {
        for (const char* cursor = reveal_process_capture_material_text;;) {
            char*  end   = nullptr;
            double value = strtod(cursor, &end);
            if (end == cursor || !(value >= 0.0) || !(value <= 1.0)
                || (*end != '\0' && *end != ',')) {
                fprintf(stderr,
                        "--reveal-mask-process-capture-material-progress requires "
                        "comma-separated values in [0, 1], got \"%s\"\n",
                        reveal_process_capture_material_text);
                free(reveal_capture_progress_values);
                free(reveal_capture_material_values);
                return 1;
            }
            float* grown = realloc(reveal_capture_material_values,
                                   (reveal_capture_material_count + 1u) * sizeof(float));
            if (grown == nullptr) {
                free(reveal_capture_progress_values);
                free(reveal_capture_material_values);
                fprintf(stderr, "Out of memory parsing the material clock list\n");
                return 1;
            }
            reveal_capture_material_values                               = grown;
            reveal_capture_material_values[reveal_capture_material_count++] = (float)value;
            if (*end == '\0')
                break;
            cursor = end + 1;
        }
        if (reveal_capture_material_count == 1u) {
            reveal_process_capture_material_progress = reveal_capture_material_values[0];
            free(reveal_capture_material_values);
            reveal_capture_material_values = nullptr;
            reveal_capture_material_count  = 0;
        } else if (reveal_capture_progress_count != 0
                   && reveal_capture_material_count != reveal_capture_progress_count) {
            fprintf(stderr,
                    "--reveal-mask-process-capture-material-progress lists %u values "
                    "for %u capture progress values\n",
                    reveal_capture_material_count, reveal_capture_progress_count);
            free(reveal_capture_progress_values);
            free(reveal_capture_material_values);
            return 1;
        }
    }

    struct wallpaper_state state = {
        .reveal_process_capture              = reveal_process_capture_directory != nullptr,
        .reveal_process_capture_presentation = reveal_process_capture_presentation,
        .reveal_process_capture_material_progress
        = reveal_process_capture_material_progress,
        .reveal_process_capture_material_values = reveal_capture_material_values,
        .reveal_process_capture_material_count  = reveal_capture_material_count,
        .reveal_process_capture_backing_scale = reveal_process_capture_backing_scale,
        .reveal_process_capture_single       = reveal_process_capture_progress != nullptr,
        .reveal_process_capture_progress_values = reveal_capture_progress_values,
        .reveal_process_capture_progress_count  = reveal_capture_progress_count,
        .reveal_process_capture_progress_text   = reveal_process_capture_progress,
        .reveal_process_capture_directory_fd = -1,
        .signal_fd                           = -1,
        .inotify_fd                          = -1,
        .vk_max_image_dimension              = UINT32_MAX,
    };
    char* config_device_selector = nullptr;
    int   rc                     = 0;

    if (state.reveal_process_capture) {
        state.reveal_process_capture_directory_fd
            = open_empty_reveal_process_capture_directory(reveal_process_capture_directory);
        if (state.reveal_process_capture_directory_fd < 0) {
            fprintf(stderr,
                    "Could not open empty reveal capture directory '%s': %s\n",
                    reveal_process_capture_directory,
                    strerror(errno));
            return 1;
        }
    }

    state.signal_fd = signalfd(-1, &sigmask, SFD_CLOEXEC | SFD_NONBLOCK);
    if (state.signal_fd < 0) {
        perror("signalfd");
        if (state.reveal_process_capture_directory_fd >= 0)
            close(state.reveal_process_capture_directory_fd);
        return 1;
    }

    if (VIPS_INIT(argv[0]))
        vips_error_exit(nullptr);
    vips_cache_set_max(0);
    vips_cache_set_max_mem(0);
    vips_cache_set_max_files(0);
    /* Bake parallelism, redesigned 2026-08-19.  The founding commit pinned
     * vips to ONE core so a wallpaper daemon could never hog the machine -
     * the right instinct through the wrong mechanism: it made every cold
     * bake slow always (13 s measured under load) instead of polite.
     * Politeness now comes from the scheduler: every bake thread runs at
     * nice 19 (see render_thread_worker), where the kernel gives it ~1.5%
     * weight against any foreground work - a compile loses nothing to a
     * bake - while an idle machine still bakes at full parallel speed.
     * The worker count is capped where the memory-bound convolutions stop
     * scaling anyway; threading is byte-identical (A/B sha256 verified:
     * region-parallel ops, no cross-pixel accumulation). */
    vips_concurrency_set(4); /* bounded by construction: four workers and a
                                * sub-second algorithm, not a machine-wide
                                * grab and not a single-core crawl */

    xoshiro256pp_seed(&g_rng, (uint64_t)time(nullptr) ^ (uint64_t)getpid());

    if (!state.reveal_process_capture)
        launch_cache_maintenance_service();

    wl_list_init(&state.outputs);
    wl_list_init(&state.output_configs);

    if (!ev_init(&state.ev)) {
        rc = 1;
        goto teardown;
    }
    ev_slot_at(&state.ev, EV_SIGNAL)->fd        = state.signal_fd;
    ev_slot_at(&state.ev, EV_SIGNAL)->want_mask = POLLIN;

    state.display = wl_display_connect(nullptr);
    if (!state.display) {
        fprintf(stderr, "FATAL: cannot connect to a Wayland display.\n");
        rc = 1;
        goto teardown;
    }
    ev_slot_at(&state.ev, EV_WL_IN)->fd        = wl_display_get_fd(state.display);
    ev_slot_at(&state.ev, EV_WL_IN)->want_mask = POLLIN;
    /* Armed only while a flush is blocked on EAGAIN. */
    ev_slot_at(&state.ev, EV_WL_OUT)->fd = wl_display_get_fd(state.display);

    {
        state.config_path = get_config_path();
        if (state.config_path) {
            struct config_parse_ctx ctx = {
                .config_list = &state.output_configs,
                .renderer_device_selector
                = vulkan_device_selector_locked ? nullptr : &config_device_selector,
            };
            int perr = ini_parse(state.config_path, config_handler, &ctx);
            if (perr != 0) {
                if (perr > 0)
                    fprintf(
                        stderr, "FATAL: config parse error at %s:%d\n", state.config_path, perr);
                else
                    fprintf(stderr, "FATAL: Could not parse config file: %s\n", state.config_path);
                rc = 1;
                goto teardown;
            }

            if (!state.reveal_process_capture)
                state.inotify_fd = inotify_init1(IN_CLOEXEC | IN_NONBLOCK);
            if (!state.reveal_process_capture && state.inotify_fd >= 0) {
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
                    if (state.config_dir && state.config_filename) {
                        add_config_watch(&state);
                        ev_slot_at(&state.ev, EV_INOTIFY)->fd        = state.inotify_fd;
                        ev_slot_at(&state.ev, EV_INOTIFY)->want_mask = POLLIN;
                    }
                }
            }
        } else {
            fprintf(stderr, "FATAL: Configuration file 'config.ini' not found.\n");
            rc = 1;
            goto teardown;
        }

        const char* selected_device
            = vulkan_device_selector_locked ? vulkan_device_selector : config_device_selector;
        if (!walle_vk_renderer_create(state.display, selected_device, &state.vk_renderer)) {
            fprintf(stderr, "FATAL: Vulkan 1.4 renderer initialization failed.\n");
            rc = 1;
            goto teardown;
        }

        if (!state.reveal_process_capture && !gamemode_init(&state)) {
            fprintf(stderr,
                    "[GAMEMODE] Portal unavailable. Continuing without GameMode support.\n");
        }
    }

    state.registry = wl_display_get_registry(state.display);
    wl_registry_add_listener(state.registry, &registry_listener, &state);
    wl_display_roundtrip(state.display);
    wl_display_roundtrip(state.display);

    if (!state.compositor) {
        fprintf(stderr, "FATAL: compositor lacks wl_compositor?!\n");
        rc = 1;
        goto teardown;
    }
    if (!state.layer_shell) {
        fprintf(stderr,
                "FATAL: compositor does not support zwlr_layer_shell_v1; "
                "walle cannot place background surfaces on it.\n");
        rc = 1;
        goto teardown;
    }
    if (!walle_vk_renderer_linux_dmabuf_ready(state.vk_renderer)) {
        fprintf(stderr,
                "FATAL: compositor lacks usable linux-dmabuf v4 feedback; "
                "Walle requires adaptive direct Vulkan presentation.\n");
        rc = 1;
        goto teardown;
    }
    state.globals_ready = true;

    /* Outputs whose events all arrived within the initial burst (e.g. a v1
     * wl_output announced before the layer shell) initialize here, once all
     * globals are guaranteed bound. Guarded by F_INITIALIZED: a no-op for
     * outputs the done-event path already handled. */
    struct wallpaper_output* boot_output;
    wl_list_for_each(boot_output, &state.outputs, link)
    {
        initialize_output(boot_output);
    }

    bool running  = true;
    bool wl_error = false;

    while (running) {
        /* All update triggers dispatched within one loop turn share one
         * transition-sync group; the flag re-opens lazily per turn. */
        state.transition_sync_turn_open = false;

        /* Reap outputs whose teardown fully completed: dead, thread joined,
         * and both event-core slots released (no CQE can reference them). */
        struct wallpaper_output *output, *tmp_output;
        wl_list_for_each_safe(output, tmp_output, &state.outputs, link)
        {
            if ((output->render.flags & F_DEAD) && !(output->render.flags & F_THREAD_ACTIVE)
                && output->slot_event < 0 && output->slot_timer < 0) {
                dbg_print("Freeing memory for dead output: '%s'",
                          output->name ? output->name : "unknown");
                wl_list_remove(&output->link);
                for (size_t i = 0; i < output->num_items; i++)
                    free(output->items[i].filename);
                free(output->items);
                free(output->name);
                free(output);
            }
        }

        if (state.reveal_process_capture_complete && !state.shutting_down) {
            rc = state.reveal_process_capture_status;
            begin_shutdown(&state);
        }

        if (state.shutting_down && wl_list_empty(&state.outputs))
            break;

        while (wl_display_prepare_read(state.display) != 0)
            wl_display_dispatch_pending(state.display);

        int fl;
        do {
            fl = wl_display_flush(state.display);
        } while (fl < 0 && errno == EINTR);
        bool flush_blocked = (fl < 0 && errno == EAGAIN);
        if (fl < 0 && !flush_blocked) {
            if (errno != EPIPE)
                fprintf(stderr, "wl_display_flush failed: %s\n", strerror(errno));
            wl_display_cancel_read(state.display);
            wl_error = true;
            break;
        }
        /* Blocked flush: watch writability, retry at the next loop top. */
        ev_slot_at(&state.ev, EV_WL_OUT)->want_mask = flush_blocked ? POLLOUT : 0;

        /* The D-Bus slot's fd and mask are dynamic; a mask change on an
         * armed op needs cancel + rearm (oneshot makes this race-free). */
        uint64_t dbus_deadline = UINT64_MAX;
        {
            struct ev_slot* ds  = ev_slot_at(&state.ev, EV_DBUS);
            int             bfd = state.bus ? sd_bus_get_fd(state.bus) : -1;
            uint32_t        bev = 0;
            if (state.bus && bfd >= 0) {
                int e = sd_bus_get_events(state.bus);
                if (e > 0)
                    bev = (uint32_t)e;
                (void)sd_bus_get_timeout(state.bus, &dbus_deadline);
            }
            if (ds->pending && (ds->fd != bfd || ds->armed_mask != bev))
                ev_slot_cancel(&state.ev, EV_DBUS);
            ds->fd        = bfd;
            ds->want_mask = bev;
        }

        ev_reconcile(&state.ev);

        /* Single wait point. The sd-bus deadline bounds the sleep via
         * EXT_ARG (nanosecond-precision, no timeout SQE, no rounding spin). */
        struct __kernel_timespec  ts;
        struct __kernel_timespec* tsp = nullptr;
        if (dbus_deadline != UINT64_MAX) {
            uint64_t now_usec = monotonic_usec();
            uint64_t rel      = dbus_deadline > now_usec ? dbus_deadline - now_usec : 0;
            ts.tv_sec         = (long long)(rel / 1000000ULL);
            ts.tv_nsec        = (long long)((rel % 1000000ULL) * 1000ULL);
            tsp               = &ts;
        }

        struct io_uring_cqe* wait_cqe = nullptr;
        int wr = io_uring_submit_and_wait_timeout(&state.ev.ring, &wait_cqe, 1, tsp, nullptr);
        if (wr < 0 && wr != -ETIME && wr != -EINTR && wr != -EAGAIN) {
            fprintf(stderr, "[EV] submit_and_wait: %s\n", strerror(-wr));
            wl_display_cancel_read(state.display);
            wl_error = true;
            break;
        }

        if (io_uring_cq_has_overflow(&state.ev.ring)) {
            /* NODROP guarantees delivery, but overflow costs syscalls and
             * indicates the CQ sizing assumption broke — say so loudly. */
            fprintf(stderr, "[EV] CQ overflow — event burst exceeded CQ sizing.\n");
            (void)io_uring_get_events(&state.ev.ring);
        }

        /* Phase 1: resolve the prepare_read intent BEFORE any handler runs
         * (handlers may call into libwayland). Non-consuming CQ scan. */
        bool wl_readable = false;
        {
            unsigned             scan_head;
            struct io_uring_cqe* scan_cqe;
            io_uring_for_each_cqe(&state.ev.ring, scan_head, scan_cqe)
            {
                uint64_t ud = io_uring_cqe_get_data64(scan_cqe);
                if (ev_ud_idx(ud) == EV_WL_IN
                    && ev_ud_gen(ud) == ev_slot_at(&state.ev, EV_WL_IN)->gen && scan_cqe->res > 0) {
                    wl_readable = true;
                    break;
                }
            }
        }
        if (wl_readable) {
            if (wl_display_read_events(state.display) == -1)
                wl_error = true;
        } else {
            wl_display_cancel_read(state.display);
        }
        if (!wl_error && wl_display_dispatch_pending(state.display) == -1)
            wl_error = true;
        if (wl_error) {
            fprintf(stderr, "[Wayland] Connection error.\n");
            break;
        }

        /* Phase 2: consume the whole CQ, recording live events; dispatch
         * only after cq_advance so handlers may freely cancel/re-arm/free
         * slots without invalidating the iteration. */
        struct fired_event
        {
            enum ev_kind             kind;
            struct wallpaper_output* owner;
        } fired[256];
        size_t               n_fired = 0;
        unsigned             head;
        unsigned             seen = 0;
        struct io_uring_cqe* cqe;
        io_uring_for_each_cqe(&state.ev.ring, head, cqe)
        {
            seen++;
            uint64_t ud  = io_uring_cqe_get_data64(cqe);
            uint32_t idx = ev_ud_idx(ud);
            if (idx == UINT32_MAX || idx >= state.ev.n_slots)
                continue; /* cancel-op completions */
            struct ev_slot* s = ev_slot_at(&state.ev, idx);
            if (ev_ud_gen(ud) != s->gen) {
                /* Stale: generation moved on. If this was the zombie's CQE,
                 * the slot becomes reusable now. */
                if (s->zombie) {
                    s->zombie  = false;
                    s->pending = false;
                }
                continue;
            }
            s->pending = false;
            if (cqe->res < 0) {
                if (cqe->res != -ECANCELED) {
                    fprintf(stderr,
                            "[EV] poll (kind %d) failed: %s — disarming\n",
                            (int)s->kind,
                            strerror(-cqe->res));
                    s->want_mask = 0;
                }
                continue;
            }
            if (n_fired < sizeof fired / sizeof *fired)
                fired[n_fired++] = (struct fired_event){.kind = s->kind, .owner = s->owner};
        }
        io_uring_cq_advance(&state.ev.ring, seen);

        bool dbus_fired   = false;
        bool config_dirty = false;
        for (size_t i = 0; i < n_fired; i++) {
            struct wallpaper_output* o = fired[i].owner;
            switch (fired[i].kind) {
                case EV_SIGNAL: {
                    struct signalfd_siginfo si;
                    while (read(state.signal_fd, &si, sizeof(si)) == (ssize_t)sizeof(si)) {
                    }
                    if (state.shutting_down) {
                        /* Second signal: restore default disposition (the
                         * third force-kills) and stop waiting for threads. */
                        sigprocmask(SIG_UNBLOCK, &sigmask, nullptr);
                        running = false;
                    } else {
                        begin_shutdown(&state);
                    }
                    break;
                }
                case EV_INOTIFY:
                    if (drain_inotify(&state))
                        config_dirty = true;
                    break;
                case EV_DBUS:
                    dbus_fired = true;
                    break;
                case EV_TIMER: {
                    uint64_t expirations;
                    if (o && o->timer_fd >= 0
                        && read(o->timer_fd, &expirations, sizeof(expirations))
                               == (ssize_t)sizeof(expirations)) {
                        if (!(o->render.flags & F_DEAD))
                            update_wallpaper(o);
                    } /* EAGAIN: disarmed/re-armed this turn — spurious */
                    break;
                }
                case EV_RENDER_DONE: {
                    uint64_t sig;
                    if (o && o->event_fd >= 0
                        && read(o->event_fd, &sig, sizeof(sig)) == (ssize_t)sizeof(sig))
                        finalize_render(o);
                    break;
                }
                case EV_WL_IN:  /* handled in phase 1 */
                case EV_WL_OUT: /* flush retried at loop top */
                default:
                    break;
            }
        }

        /* Coalesced: one reload per turn, however many inotify events hit. */
        if (config_dirty)
            reload_global_config(&state);

        transition_sync_flush_starts(&state);

        if (state.bus
            && (dbus_fired || (dbus_deadline != UINT64_MAX && monotonic_usec() >= dbus_deadline))) {
            int pr;
            while ((pr = sd_bus_process(state.bus, nullptr)) > 0)
                ;
            if (pr < 0) {
                ev_slot_cancel(&state.ev, EV_DBUS);
                ev_slot_at(&state.ev, EV_DBUS)->fd = -1;
                gamemode_handle_disconnect(&state, pr);
            }
        }
    }

    if (wl_error)
        rc = 1;

teardown:
    /* Stage 2: hard stop. Join stragglers, then tear the ring down BEFORE
     * closing the fds it may still reference. Every resource below is
     * null/-1-guarded, so this path is also the early-fatal exit — a failed
     * startup must be just as leak-free as a clean shutdown. */
    struct wallpaper_output *output, *tmp_output;
    wl_list_for_each_safe(output, tmp_output, &state.outputs, link)
    {
        destroy_output(output);

        if (output->render.flags & F_THREAD_ACTIVE) {
            pthread_join(output->render_thread, nullptr);
            output->render.flags &= ~F_THREAD_ACTIVE;
            release_render_result(&output->async_result);
            if (output->slot_event >= 0) {
                ev_slot_release(&state.ev, (size_t)output->slot_event);
                output->slot_event = -1;
            }
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

    ev_exit(&state.ev);

    if (state.inotify_fd >= 0)
        close(state.inotify_fd);
    if (state.signal_fd >= 0)
        close(state.signal_fd);
    if (state.reveal_process_capture_directory_fd >= 0)
        close(state.reveal_process_capture_directory_fd);
    free(state.config_path);
    free(state.config_dir);
    free(state.config_filename);
    free(config_device_selector);

    gamemode_cleanup(&state);

    walle_vk_renderer_destroy(state.vk_renderer);
    state.vk_renderer = nullptr;

    if (state.layer_shell) {
        /* The destroy request only exists since v3; on an older bind just
         * drop the client-side proxy. */
        if (wl_proxy_get_version((struct wl_proxy*)state.layer_shell) >= 3)
            zwlr_layer_shell_v1_destroy(state.layer_shell);
        else
            wl_proxy_destroy((struct wl_proxy*)state.layer_shell);
    }
    if (state.compositor)
        wl_compositor_destroy(state.compositor);
    if (state.registry)
        wl_registry_destroy(state.registry);
    if (state.display)
        wl_display_disconnect(state.display);

    vips_shutdown();
    return rc;
}
