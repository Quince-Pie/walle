/* Include the current candidate; only selected CPU helpers survive --gc-sections. */
#define main walle_app_main_unused
#ifndef WALLE_APP_SOURCE
#define WALLE_APP_SOURCE "../walle.c"
#endif
#include WALLE_APP_SOURCE
#undef main
#include <float.h>

static unsigned checks;
#define CHECK(expression) do { \
    ++checks; \
    if (!(expression)) { \
        fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #expression); \
        exit(1); \
    } \
} while (false)

static double observed_progress;
static unsigned builds, aborts, destroys;
static unsigned char fake_transition, fake_output;

/* Device entry points fail closed. The frame test intentionally stops after
 * recording the source-calculated progress, before rendering or display work. */
bool walle_transition_build(struct walle_transition *t, double progress, double scene_time, bool first,
                            const struct walle_vk_frame **out)
{
    (void)t; (void)first; (void)out; (void)scene_time;
    observed_progress = progress;
    ++builds;
    return false;
}
void walle_transition_commit(struct walle_transition *t) { (void)t; abort(); }
bool walle_transition_recover_analytic(struct walle_transition* t,const struct walle_vk_frame** frame)
{ (void)t; (void)frame; abort(); }
void walle_transition_destroy(struct walle_transition *t) { (void)t; ++destroys; }
void walle_vk_output_abort_transition(struct walle_vk_output *o) { (void)o; ++aborts; }
void walle_vk_output_promote(struct walle_vk_output *o) { (void)o; abort(); }
void walle_vk_output_destroy(struct walle_vk_output *o) { (void)o; abort(); }
bool walle_vk_output_resize(struct walle_vk_output *o, uint32_t w, uint32_t h)
{ (void)o; (void)w; (void)h; abort(); }
bool walle_vk_output_create(struct walle_vk_renderer *r, struct wl_surface *s,
                            uint32_t w, uint32_t h, bool readback, struct walle_vk_output **out)
{ (void)r; (void)s; (void)w; (void)h; (void)readback; (void)out; abort(); }
uint32_t walle_vk_renderer_max_image_dimension(const struct walle_vk_renderer *r)
{ (void)r; abort(); }
bool walle_vk_renderer_bind_linux_dmabuf(struct walle_vk_renderer *r, struct wl_registry *registry,
                                        uint32_t name, uint32_t version)
{ (void)r; (void)registry; (void)name; (void)version; abort(); }
enum walle_vk_frame_status walle_vk_output_render(struct walle_vk_output *o,
                                                 const struct walle_vk_frame *f)
{
    (void)o; (void)f; abort();
}
int __wrap_clock_gettime(clockid_t clock, struct timespec *value)
{
    CHECK(clock == CLOCK_MONOTONIC);
    *value = (struct timespec){.tv_sec = 100, .tv_nsec = 0};
    return 0;
}
/* Any accidental Wayland marshal/display work fails this CPU-only suite. */
struct wl_proxy *__wrap_wl_proxy_marshal_flags(struct wl_proxy *proxy, uint32_t opcode,
    const struct wl_interface *interface, uint32_t version, uint32_t flags, ...)
{
    (void)proxy; (void)opcode; (void)interface; (void)version; (void)flags;
    abort();
}
void __wrap_wl_proxy_destroy(struct wl_proxy *proxy) { (void)proxy; abort(); }

static void free_configs(struct wl_list *list)
{
    struct output_config *item, *next;
    wl_list_for_each_safe(item, next, list, link) {
        wl_list_remove(&item->link);
        free_item_list(&item->items);
        free(item->output_name);
        free(item);
    }
}

static void check_config_contracts(void)
{
    struct wm_tint tint;
    CHECK(parse_tint_setting("#12aBcF", &tint));
    CHECK(tint.present && tint.srgb[0] == 0x12 && tint.srgb[1] == 0xab && tint.srgb[2] == 0xcf && tint.srgb[3] == 255);
    CHECK(parse_tint_setting("#DEaDbe00", &tint));
    CHECK(tint.present && tint.srgb[0] == 0xde && tint.srgb[1] == 0xad && tint.srgb[2] == 0xbe && tint.srgb[3] == 0);
    CHECK(parse_tint_setting("#0102037F", &tint));
    CHECK(tint.present && tint.srgb[3] == 127);
    CHECK(parse_tint_setting("NoNe", &tint));
    CHECK(!tint.present);
    const char *bad[] = {"", "#", "#123", "123456", "#12345", "#1234567", "#123456789", "#00gg00", "#000000zz", "red", " #123456"};
    for (size_t i = 0; i < sizeof bad / sizeof *bad; ++i)
        CHECK(!parse_tint_setting(bad[i], &tint));

    struct wl_list configs; wl_list_init(&configs);
    struct config_parse_ctx context = {.config_list = &configs};
    CHECK(config_handler(&context, "default", "transition_appearance", "auto"));
    CHECK(config_handler(&context, "default", "transition_appearance", "DARK"));
    CHECK(config_handler(&context, "default", "transition_appearance", "light"));
    CHECK(!config_handler(&context, "default", "transition_appearance", "system"));
    CHECK(config_handler(&context, "default", "transition_motion", "sweep"));
    CHECK(config_handler(&context, "default", "transition_motion", "LENS"));
    CHECK(!config_handler(&context, "default", "transition_motion", "fade"));
    CHECK(config_handler(&context, "default", "transition_tint", "#12345600"));
    CHECK(!config_handler(&context, "default", "transition_tint", "#12345G"));
    free_configs(&configs);

    /* Exercise the actual INI parser too: '#' must survive as a value. */
    wl_list_init(&configs);
    CHECK(ini_parse_string("[default]\ntransition_tint=#12345600\ntransition_motion=lens\ntransition_appearance=dark\n", config_handler, &context) == 0);
    struct output_config *parsed = wl_container_of(configs.next, parsed, link);
    CHECK(parsed->tint.present && parsed->tint.srgb[3] == 0);
    CHECK(parsed->motion == WALLE_TRANSITION_LENS && parsed->appearance == GLASS_APPEARANCE_DARK);
    free_configs(&configs);
    const char *invalid_ini[] = {
        "[default]\ntransition_tint=#12345G\n",
        "[default]\ntransition_motion=fade\n",
        "[default]\ntransition_appearance=system\n"
    };
    for (size_t i = 0; i < sizeof invalid_ini / sizeof *invalid_ini; ++i) {
        wl_list_init(&configs);
        CHECK(ini_parse_string(invalid_ini[i], config_handler, &context) == 2);
        free_configs(&configs);
    }
}

static void check_appearance(void)
{
    struct wallpaper_state state = {};
    const uint32_t values[] = {1, 0, 1, 2, 1, 3, 1, UINT32_MAX};
    for (size_t i = 0; i < sizeof values / sizeof *values; ++i) {
        accept_desktop_appearance(&state, values[i]);
        CHECK(state.desktop_dark == (values[i] == 1));
    }
}

static void check_coalescing(void)
{
    struct wallpaper_state state = {};
    struct wallpaper_output output = {.name = "test", .timer_fd = -1, .num_items = 3,
        .current_item_index = 1, .gamemode_enabled = true,
        .render = {.state = &state, .t_state = T_STATE_IDLE}};
    wl_list_init(&state.outputs); wl_list_insert(&state.outputs, &output.link);
    for (unsigned busy = 0; busy < 4; ++busy) {
        output.render.flags = busy == 0 ? F_THREAD_ACTIVE : 0;
        output.render.t_state = busy == 1 ? T_STATE_ARMED : busy == 2 ? T_STATE_RUNNING : T_STATE_IDLE;
        state.gamemode_active = busy == 3;
        output.pending_cycle = false;
        for (unsigned repeat = 0; repeat < 5; ++repeat) {
            update_wallpaper(&output);
            CHECK(output.current_item_index == 1 && output.pending_cycle);
        }
    }
    state.gamemode_active = false;
    output.render.t_state = T_STATE_IDLE;
    toggle_gamemode_timers(&state, false);
    CHECK(!output.pending_cycle && output.current_item_index == 2);
    CHECK(!(output.render.flags & F_THREAD_ACTIVE)); /* unconfigured test surface */
    output.num_items = 1; output.current_item_index = 0; output.pending_cycle = true;
    toggle_gamemode_timers(&state, false);
    CHECK(!output.pending_cycle && output.current_item_index == 0);
    output.num_items = 0; output.current_item_index = SIZE_MAX; output.pending_cycle = true;
    update_wallpaper(&output);
    CHECK(!output.pending_cycle && output.current_item_index == SIZE_MAX);
    wl_list_remove(&output.link);
}

static void check_tiny_duration(void)
{
    /* Hex input is an exactly representable positive subnormal. strtof need
     * not signal underflow for this exact conversion; the parser accepts it. */
    float tiny = parse_duration_setting("0x1p-148");
    CHECK(tiny > 0 && tiny < FLT_MIN);
    struct wallpaper_state state = {};
    struct wallpaper_output output = {.name = "intentional-stop-before-GPU",
        .pending_source = {.fd = -1}, .current_source = {.fd = -1},
        .transition = (struct walle_transition *)&fake_transition,
        .transition_duration = 600, /* A later config value must not replace the snapshot. */
        .render = {.state = &state, .vk_output = (struct walle_vk_output *)&fake_output,
            .flags = F_BOOT_COMPLETE, .t_state = T_STATE_ARMED, .duration_seconds = tiny}};
    CHECK(render_frame(&output) == RENDER_FRAME_FAILED);
    CHECK(builds == 1 && isfinite(observed_progress) && observed_progress == 0);
    CHECK(output.render.t_state == T_STATE_IDLE && aborts == 1 && destroys == 1);
    output.transition = (struct walle_transition *)&fake_transition;
    output.render.t_state = T_STATE_RUNNING;
    output.render.anim_start_ns = UINT64_C(99'000'000'000);
    CHECK(render_frame(&output) == RENDER_FRAME_FAILED);
    CHECK(builds == 2 && isfinite(observed_progress) && observed_progress == 1);
}

int main(void)
{
    check_config_contracts();
    check_appearance();
    check_coalescing();
    check_tiny_duration();
    printf("{\"assertions\":%u,\"failures\":0,\"device_calls\":0,\"display_calls\":0}\n", checks);
    return 0;
}
