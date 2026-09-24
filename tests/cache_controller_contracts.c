/* Public-controller checks against retained original QuartzCore observations.
 * The linker wrapper observes the real controller-to-cache call boundary. */
#include "transition.h"
#include "sdf_cache.h"
#include "transition_check.h"
#include <math.h>
#include <string.h>

struct native_update {
    unsigned case_index, owner;
    bool new_owner, nested_disabled, eligible, changed;
    double time, transform[16], ring[4], last_change;
    int32_t bounds[4], stored_bounds[4];
    uint16_t elements;
    uint64_t copies;
};
struct native_frame {
    double progress, time;
    double declared_transform[16];
    unsigned endpoint;
    bool has_state, cached, redraw, retain;
    struct native_update state;
    int32_t birth[4], current[4];
    unsigned native_copy_render_count;
};
struct native_case {
    const char* name;
    uint32_t width, height;
    double backing_scale, local_radius;
    struct walle_transition_options options;
    struct native_frame frames[25];
};
#include "cache_native_data.h"

static const struct native_frame* observed_frame;
static bool observe = true;
static unsigned observed_calls, checked_controller_states, pack_failures;
static unsigned recovery_cases;
static struct wm_sdf_cache_state last_observed_state;

static void integers(const int32_t* actual, const int32_t* expected, size_t count,
                     const char* field)
{
    for (size_t i = 0; i < count; ++i) {
        if (actual[i] != expected[i])
            fprintf(stderr, "%s[%zu]: got %d expected %d\n", field, i, actual[i], expected[i]);
        CHECK(actual[i] == expected[i]);
    }
}
static void doubles(const double* actual, const double* expected, size_t count,
                    const char* field)
{
    for (size_t i = 0; i < count; ++i) {
        if (memcmp(actual + i, expected + i, sizeof *actual))
            fprintf(stderr, "%s[%zu]: got %a expected %a\n", field, i, actual[i], expected[i]);
        CHECK(memcmp(actual + i, expected + i, sizeof *actual) == 0);
    }
}
static void state_output(const struct wm_sdf_cache_state* state,
                         const struct native_update* expected, const double transform[16])
{
    integers(state->bounds, expected->stored_bounds, 4, "stored bounds");
    doubles(state->durations, expected->ring, 4, "duration ring");
    doubles(&state->last_change, &expected->last_change, 1, "last change");
    doubles(state->transform, transform, 16, "stored transform");
    CHECK(state->eligible == expected->eligible);
    CHECK(state->changed == expected->changed);
    CHECK(state->nested_disabled == expected->nested_disabled);
}
bool __real_wm_sdf_cache_advance(struct wm_sdf_cache_state*, double, const double[16],
                                const int32_t[4], uint16_t, uint64_t, bool);
bool __wrap_wm_sdf_cache_advance(struct wm_sdf_cache_state* state, double time,
                                const double transform[16], const int32_t bounds[4],
                                uint16_t elements, uint64_t copies, bool disabled)
{
    ++observed_calls;
    if (observe) {
        CHECK(observed_frame && observed_frame->has_state && observed_calls == 1);
        const struct native_update* expected = &observed_frame->state;
        doubles(&time, &expected->time, 1, "scene clock");
        /* The v9 oracle transports every declared binary64 input exactly.
         * Assert both the product declaration and original function input. */
        doubles(transform, observed_frame->declared_transform, 16, "controller transform");
        doubles(transform, expected->transform, 16, "original native transform");
        integers(bounds, expected->bounds, 4, "controller bounds");
        CHECK(elements == expected->elements && copies == expected->copies && !disabled);
    }
    bool result = __real_wm_sdf_cache_advance(state, time, transform, bounds,
                                              elements, copies, disabled);
    last_observed_state = *state;
    if (observe) {
        state_output(state, &observed_frame->state, observed_frame->state.transform);
        CHECK(result == observed_frame->state.eligible);
        ++checked_controller_states;
    }
    return result;
}

static void check_frame(const struct walle_vk_frame* frame, const struct native_frame* expected)
{
    CHECK(frame);
    CHECK(frame->plain_incoming == (expected->endpoint == 2));
    CHECK((frame->sdf_surface != nullptr) == expected->cached);
    CHECK(frame->sdf_redraw == expected->redraw);
    CHECK(frame->sdf_retain == expected->retain);
    if (expected->endpoint != 1)
        CHECK(frame->draw_count == 0 && !frame->capture);
    if (expected->cached) {
        integers(frame->sdf_surface->rect, expected->birth, 4, "creation surface");
        for (unsigned a = 0; a < 2; ++a) {
            CHECK(frame->sdf_surface->texture[a] == (uint32_t)expected->birth[a + 2]);
            CHECK(frame->sdf_surface->extent[a] == frame->sdf_surface->texture[a]);
        }
    }
    unsigned cached_draws = 0, render_draws = 0, effect_origins = 0;
    for (size_t i = 0; i < frame->draw_count; ++i) {
        const struct walle_vk_draw* draw = &frame->draws[i];
        if (draw->pass == WALLE_VK_SDF_CACHE) {
            ++render_draws;
            integers(draw->scissor, expected->birth, 4, "SDF render scissor");
        }
        if (!draw->cached_sdf)
            continue;
        ++cached_draws;
        CHECK(expected->cached && draw->vertex_count == 4);
        CHECK(draw->pass == WALLE_VK_GLASS_REGULAR || draw->pass == WALLE_VK_GLASS_CLEAR
              || draw->pass == WALLE_VK_HIGHLIGHT || draw->pass == WALLE_VK_TINT_MASK);
        if (draw->pass == WALLE_VK_HIGHLIGHT || draw->pass == WALLE_VK_TINT_MASK) {
            ++effect_origins;
            CHECK(draw->vertices[0].position[0] == (float)expected->current[0]);
            CHECK(draw->vertices[0].position[1] == (float)expected->current[1]);
            CHECK(draw->vertices[2].position[0]
                  == (float)((double)expected->current[0] + expected->current[2]));
            CHECK(draw->vertices[2].position[1]
                  == (float)((double)expected->current[1] + expected->current[3]));
        }
    }
    CHECK((render_draws != 0) == expected->redraw);
    CHECK(cached_draws == (expected->cached ? expected->state.copies : 0));
    CHECK((effect_origins != 0) == expected->cached);
}
static const struct walle_vk_frame* build(struct walle_transition* transition,
                                         const struct native_frame* expected)
{
    observed_frame = expected;
    observed_calls = 0;
    const struct walle_vk_frame* frame = nullptr;
    CHECK(walle_transition_build(transition, expected->progress, expected->time, false, &frame));
    if (observe)
        CHECK(observed_calls == (unsigned)expected->has_state);
    check_frame(frame, expected);
    return frame;
}
static struct walle_transition* create(const struct native_case* fixture)
{
    struct walle_transition* transition = nullptr;
    CHECK(walle_transition_create(fixture->width, fixture->height, fixture->backing_scale,
                                    &fixture->options, &transition));
    return transition;
}

static uint64_t mix(uint64_t hash, const void* bytes, size_t count)
{
    const uint8_t* p = bytes;
    for (size_t i = 0; i < count; ++i)
        hash = (hash ^ p[i]) * UINT64_C(1099511628211);
    return hash;
}
static uint64_t fingerprint(const struct walle_vk_frame* frame)
{
    uint64_t hash = UINT64_C(14695981039346656037);
    hash = mix(hash, &frame->draw_count, sizeof frame->draw_count);
    hash = mix(hash, &frame->material_opacity, sizeof frame->material_opacity);
    hash = mix(hash, &frame->sdf_redraw, sizeof frame->sdf_redraw);
    hash = mix(hash, &frame->sdf_retain, sizeof frame->sdf_retain);
    hash = mix(hash, &frame->plain_incoming, sizeof frame->plain_incoming);
    const struct walle_vk_surface_plan* surfaces[] = {frame->sdf_surface,
        frame->reveal_surface, frame->tint_mask_surface, frame->tint_group_surface};
    for (size_t i = 0; i < sizeof surfaces / sizeof *surfaces; ++i) {
        bool present = surfaces[i] != nullptr;
        hash = mix(hash, &present, sizeof present);
        if (present) {
            hash = mix(hash, surfaces[i]->rect, sizeof surfaces[i]->rect);
            hash = mix(hash, surfaces[i]->texture, sizeof surfaces[i]->texture);
            hash = mix(hash, surfaces[i]->extent, sizeof surfaces[i]->extent);
        }
    }
    for (size_t i = 0; i < frame->draw_count; ++i) {
        const struct walle_vk_draw* draw = &frame->draws[i];
        hash = mix(hash, &draw->pass, sizeof draw->pass);
        hash = mix(hash, &draw->cached_sdf, sizeof draw->cached_sdf);
        hash = mix(hash, &draw->shape_mode, sizeof draw->shape_mode);
        hash = mix(hash, draw->scissor, sizeof draw->scissor);
        hash = mix(hash, draw->glass, sizeof draw->glass);
        hash = mix(hash, draw->effect, sizeof draw->effect);
        hash = mix(hash, draw->vertices, draw->vertex_count * sizeof *draw->vertices);
        hash = mix(hash, draw->indices, draw->index_count * sizeof *draw->indices);
    }
    return hash;
}

static void native_states(void)
{
    struct wm_sdf_cache_state states[4][16] = {};
    for (size_t i = 0; i < sizeof native_updates / sizeof *native_updates; ++i) {
        const struct native_update* expected = &native_updates[i];
        snprintf(test_case, sizeof test_case, "native state update %zu", i);
        CHECK(expected->case_index < 4 && expected->owner < 16);
        struct wm_sdf_cache_state* state = &states[expected->case_index][expected->owner];
        if (expected->new_owner)
            wm_sdf_cache_init(state, expected->nested_disabled);
        CHECK(__real_wm_sdf_cache_advance(state, expected->time, expected->transform,
                                          expected->bounds, expected->elements,
                                          expected->copies, false) == expected->eligible);
        state_output(state, expected, expected->transform);
    }
}
static void native_sequences(void)
{
    for (size_t c = 0; c < sizeof native_cases / sizeof *native_cases; ++c) {
        const struct native_case* fixture = &native_cases[c];
        struct walle_transition* transition = create(fixture);
        for (unsigned i = 0; i < 25; ++i) {
            snprintf(test_case, sizeof test_case, "%s frame %u", fixture->name, i);
            uint64_t first = fingerprint(build(transition, &fixture->frames[i]));
            /* A render RETRY calls build again without committing the first
             * candidate. Both emitted data and native state must repeat. */
            CHECK(first == fingerprint(build(transition, &fixture->frames[i])));
            walle_transition_commit(transition);
            walle_transition_commit(transition); /* Repeated commit is inert. */
        }
        walle_transition_destroy(transition);
    }
}
static void retry_and_reset(void)
{
    const struct native_case* fixture = &native_cases[0];
    struct walle_transition* transition = create(fixture);
    for (unsigned i = 0; i < 16; ++i) {
        snprintf(test_case, sizeof test_case, "retry setup frame %u", i);
        (void)build(transition, &fixture->frames[i]);
        walle_transition_commit(transition);
    }
    snprintf(test_case, sizeof test_case, "discard first cache candidate");
    uint64_t first = fingerprint(build(transition, &fixture->frames[16]));
    struct native_frame later = fixture->frames[17];
    later.redraw = true;
    memcpy(later.birth, later.current, sizeof later.birth);
    observe = false; /* Frame16 was not committed, so this ring is a new history. */
    (void)build(transition, &later);
    observe = true;
    CHECK(first == fingerprint(build(transition, &fixture->frames[16])));
    walle_transition_commit(transition);
    (void)build(transition, &fixture->frames[17]);
    walle_transition_commit(transition);

    snprintf(test_case, sizeof test_case, "failed update preserves retained owner");
    CHECK(!walle_transition_update(transition, 0, fixture->height, fixture->backing_scale,
                                     &fixture->options));
    (void)build(transition, &fixture->frames[18]);
    walle_transition_commit(transition);

    snprintf(test_case, sizeof test_case, "same-context reset drops cache history");
    CHECK(walle_transition_update(transition, fixture->width, fixture->height,
                                    fixture->backing_scale, &fixture->options));
    for (unsigned i = 0; i < 18; ++i) {
        snprintf(test_case, sizeof test_case, "same-context replay frame %u", i);
        (void)build(transition, &fixture->frames[i]);
        walle_transition_commit(transition);
    }
    fixture = &native_cases[1];
    snprintf(test_case, sizeof test_case, "new-context reset");
    CHECK(walle_transition_update(transition, fixture->width, fixture->height,
                                    fixture->backing_scale, &fixture->options));
    for (unsigned i = 0; i < 25; ++i) {
        snprintf(test_case, sizeof test_case, "lens context replay frame %u", i);
        (void)build(transition, &fixture->frames[i]);
        walle_transition_commit(transition);
    }
    walle_transition_destroy(transition);
}
static void invalid_clock(void)
{
    const struct native_case* fixture = &native_cases[0];
    struct walle_transition* transition = create(fixture);
    for (unsigned i = 0; i < 16; ++i) {
        (void)build(transition, &fixture->frames[i]);
        walle_transition_commit(transition);
    }
    snprintf(test_case, sizeof test_case, "nonfinite clock cannot advance history");
    const double clocks[] = {NAN, INFINITY, -INFINITY};
    const double progress[] = {0, fixture->frames[16].progress, 1};
    for (unsigned c = 0; c < 3; ++c)
        for (unsigned p = 0; p < 3; ++p) {
            observed_frame = nullptr;
            const struct walle_vk_frame* frame = nullptr;
            CHECK(!walle_transition_build(transition, progress[p], clocks[c], false, &frame));
            CHECK(!walle_transition_build(transition, progress[p], clocks[c], true, &frame));
        }
    (void)build(transition, &fixture->frames[16]);
    walle_transition_commit(transition);
    (void)build(transition, &fixture->frames[17]);
    walle_transition_destroy(transition);
}
static void check_pack_failure(const struct wm_recipe* recipe,
                                const struct wm_render_domain* domain, float scale,
                                const uint32_t* size, bool output_bytes, bool output_function)
{
    uint8_t bytes[216], zero[216] = {};
    uint32_t function = UINT32_MAX;
    memset(bytes, 0xa5, sizeof bytes);
    CHECK(!wm_recipe_pack_glass_texture(recipe, domain, scale, size,
                                         output_bytes ? bytes : nullptr,
                                         output_function ? &function : nullptr));
    if (output_bytes)
        CHECK(memcmp(bytes, zero, sizeof bytes) == 0);
    if (output_function)
        CHECK(function == 0);
    ++pack_failures;
}
static void texture_pack_failure(void)
{
    snprintf(test_case, sizeof test_case, "texture packer zeroes failure outputs");
    struct wm_material_input input = {.style=WM_REGULAR,.dark=true,.active=true,
        .width_points=512,.height_points=512,.backing_scale=1};
    struct wm_recipe* recipe = wm_recipe_create(&input);
    CHECK(recipe);
    struct wm_render_domain domain = {.source_width=64,.source_height=64,.source_scale=.25,
        .transform={1,0,0,-1},.headroom=1,.gamma=2.2,.light_angle=1,
        .light_opacity=NAN,.light_spread=NAN,.light_height=NAN};
    const uint32_t size[2] = {64,64}, zero_x[2] = {0,64}, zero_y[2] = {64,0};
    check_pack_failure(recipe, &domain, 1, nullptr, true, true);
    check_pack_failure(recipe, &domain, 1, zero_x, true, true);
    check_pack_failure(recipe, &domain, 1, zero_y, true, true);
    const float scales[] = {0,-1,NAN,INFINITY};
    for (unsigned i = 0; i < 4; ++i)
        check_pack_failure(recipe, &domain, scales[i], size, true, true);
    check_pack_failure(nullptr, &domain, 1, size, true, true);
    check_pack_failure(recipe, nullptr, 1, size, true, true);
    check_pack_failure(recipe, &domain, 1, size, false, true);
    check_pack_failure(recipe, &domain, 1, size, true, false);
    domain.source_width = 0;
    check_pack_failure(recipe, &domain, 1, size, true, true);
    wm_recipe_destroy(recipe);
}
static void same_surface(const struct walle_vk_surface_plan* a,
                          const struct walle_vk_surface_plan* b)
{
    CHECK((a != nullptr) == (b != nullptr));
    if (!a) return;
    integers(a->rect, b->rect, 4, "analytic surface bounds");
    CHECK(memcmp(a->texture, b->texture, sizeof a->texture) == 0);
    CHECK(memcmp(a->extent, b->extent, sizeof a->extent) == 0);
}
static void same_analytic_frame(const struct walle_vk_frame* actual,
                                const struct walle_vk_frame* reference)
{
    CHECK(actual && reference && !actual->sdf_surface && !reference->sdf_surface);
    CHECK(!actual->sdf_redraw && !actual->sdf_retain);
    CHECK(actual->draw_count == reference->draw_count);
    CHECK(actual->plain_incoming == reference->plain_incoming);
    CHECK(memcmp(&actual->material_opacity, &reference->material_opacity,
                  sizeof actual->material_opacity) == 0);
    same_surface(actual->reveal_surface, reference->reveal_surface);
    same_surface(actual->tint_mask_surface, reference->tint_mask_surface);
    same_surface(actual->tint_group_surface, reference->tint_group_surface);
    CHECK((actual->tint_ramp_rgba16f != nullptr) == (reference->tint_ramp_rgba16f != nullptr));
    if (actual->tint_ramp_rgba16f)
        CHECK(memcmp(actual->tint_ramp_rgba16f, reference->tint_ramp_rgba16f, 2048) == 0);
    unsigned glass = 0;
    for (size_t i = 0; i < actual->draw_count; ++i) {
        const struct walle_vk_draw *a = &actual->draws[i], *b = &reference->draws[i];
        CHECK(a->pass == b->pass && a->shape_mode == b->shape_mode);
        CHECK(a->pass != WALLE_VK_SDF_CACHE && !a->cached_sdf && !b->cached_sdf);
        CHECK(a->vertex_count == b->vertex_count && a->index_count == b->index_count);
        CHECK(memcmp(&a->edr_scale, &b->edr_scale, sizeof a->edr_scale) == 0);
        CHECK(memcmp(a->scissor, b->scissor, sizeof a->scissor) == 0);
        CHECK(memcmp(a->glass, b->glass, sizeof a->glass) == 0);
        CHECK(memcmp(a->effect, b->effect, sizeof a->effect) == 0);
        CHECK(memcmp(a->vertices, b->vertices, a->vertex_count * sizeof *a->vertices) == 0);
        CHECK(memcmp(a->indices, b->indices, a->index_count * sizeof *a->indices) == 0);
        glass += a->pass == WALLE_VK_GLASS_REGULAR || a->pass == WALLE_VK_GLASS_CLEAR;
    }
    CHECK(glass != 0);
}
static void analytic_recovery(void)
{
    constexpr unsigned scene_indices[] = {0,0,2,3};
    constexpr unsigned target_frames[] = {16,17,23,23};
    for (unsigned test = 0; test < 4; ++test) {
        const struct native_case* fixture = &native_cases[scene_indices[test]];
        unsigned target = target_frames[test];
        struct walle_transition *transition = create(fixture), *reference = create(fixture);
        snprintf(test_case, sizeof test_case, "analytic recovery %s frame%u", fixture->name, target);
        const struct walle_vk_frame* recovered = nullptr;
        CHECK(!walle_transition_recover_analytic(transition, &recovered));
        for (unsigned i = 0; i < target; ++i) {
            (void)build(transition, &fixture->frames[i]);
            walle_transition_commit(transition);
        }
        const struct native_frame* expected = &fixture->frames[target];
        (void)build(transition, expected);
        /* Independent owner at this same declared pose/time has no eligible
         * cache history. Its ordinary analytic packets and complete draw
         * spans are the recovery target, without invoking the recovery API. */
        observe = false;
        const struct walle_vk_frame* analytic = nullptr;
        CHECK(walle_transition_build(reference, expected->progress, expected->time, false, &analytic));
        observe = true;
        observed_frame = expected;
        observed_calls = 0;
        CHECK(walle_transition_recover_analytic(transition, &recovered));
        CHECK(observed_calls == 1); /* wrapper checks the exact native ring */
        same_analytic_frame(recovered, analytic);
        uint64_t hash = fingerprint(recovered);
        const struct walle_vk_frame* invalid = nullptr;
        CHECK(!walle_transition_recover_analytic(transition, &invalid));
        CHECK(hash == fingerprint(recovered));

        /* RETRY preserves the eligibility clock, while REPLAN has already
         * invalidated the renderer's retained storage. A new build must
         * recreate the current source and can recover once again. */
        struct native_frame retry = *expected;
        retry.redraw = true;
        memcpy(retry.birth, retry.current, sizeof retry.birth);
        (void)build(transition, &retry);
        observed_calls = 0;
        CHECK(walle_transition_recover_analytic(transition, &recovered));
        CHECK(observed_calls == 1);
        same_analytic_frame(recovered, analytic);
        walle_transition_commit(transition);
        CHECK(!walle_transition_recover_analytic(transition, &invalid));

        if (target + 1 < 24) {
            struct native_frame next = fixture->frames[target + 1];
            /* A recovered commit clears retained ownership but advances the
             * original eligibility ring only once. The next eligible frame
             * must recreate its source at its current allocation origin. */
            next.redraw = true;
            memcpy(next.birth, next.current, sizeof next.birth);
            (void)build(transition, &next);
            walle_transition_commit(transition);
        }
        CHECK(walle_transition_update(transition, fixture->width, fixture->height,
                                        fixture->backing_scale, &fixture->options));
        CHECK(!walle_transition_recover_analytic(transition, &invalid));
        observe = false;
        CHECK(walle_transition_build(transition, expected->progress, expected->time, false, &recovered));
        observe = true;
        same_analytic_frame(recovered, analytic);
        CHECK(!walle_transition_recover_analytic(transition, &invalid));
        walle_transition_destroy(reference);
        walle_transition_destroy(transition);
        ++recovery_cases;
    }
}
static void recovery_material_options(void)
{
    for (unsigned style = 0; style < 2; ++style)
        for (unsigned dark = 0; dark < 2; ++dark)
            for (unsigned active = 0; active < 2; ++active)
                for (unsigned tint = 0; tint < 2; ++tint) {
                    snprintf(test_case, sizeof test_case, "recovery material style%u dark%u active%u tint%u",
                             style, dark, active, tint);
                    struct walle_transition_options options = native_cases[0].options;
                    options.style = (enum wm_style)style;
                    options.dark = dark != 0;
                    options.active = active != 0;
                    options.tint.present = tint != 0;
                    struct walle_transition *transition = nullptr, *reference = nullptr;
                    CHECK(walle_transition_create(256,144,1,&options,&transition));
                    CHECK(walle_transition_create(256,144,1,&options,&reference));
                    observe = false;
                    const struct walle_vk_frame* frame = nullptr;
                    for (unsigned i = 0; i < 8; ++i) {
                        CHECK(walle_transition_build(transition,.65,1000. + .2 * i,false,&frame));
                        walle_transition_commit(transition);
                    }
                    CHECK(walle_transition_build(transition,.65,1001.6,false,&frame));
                    CHECK(frame->sdf_surface);
                    struct wm_sdf_cache_state pending = last_observed_state;
                    const struct walle_vk_frame* recovered = nullptr;
                    observed_calls = 0;
                    CHECK(walle_transition_recover_analytic(transition,&recovered));
                    CHECK(observed_calls == 1);
                    CHECK(memcmp(&pending,&last_observed_state,sizeof pending) == 0);
                    const struct walle_vk_frame* analytic = nullptr;
                    CHECK(walle_transition_build(reference,.65,1001.6,false,&analytic));
                    same_analytic_frame(recovered,analytic);
                    walle_transition_commit(transition);
                    CHECK(!walle_transition_recover_analytic(transition,&recovered));
                    walle_transition_destroy(reference);
                    walle_transition_destroy(transition);
                    observe = true;
                    ++recovery_cases;
                }
}
int main(void)
{
    native_states();
    native_sequences();
    retry_and_reset();
    invalid_clock();
    texture_pack_failure();
    analytic_recovery();
    recovery_material_options();
    printf("{\"native_frames\":100,\"native_state_updates\":250,"
           "\"native_controller_bounds\":92,\"repeated_retry_frames\":100,"
           "\"checked_controller_states\":%u,\"clock_rejections\":18,"
           "\"texture_pack_failures\":%u,\"reset_paths\":2,\"analytic_recovery_cases\":%u}\n",
           checked_controller_states, pack_failures, recovery_cases);
    return 0;
}
