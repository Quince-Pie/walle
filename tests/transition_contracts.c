#include "transition.h"

#include "transition_check.h"
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static unsigned pass_order(enum walle_vk_pass pass)
{
    switch (pass) {
        case WALLE_VK_SDF_CACHE: return 0; /* Independent geometry prelude. */
        case WALLE_VK_REVEAL_MASK: return 0;
        case WALLE_VK_REVEAL_IMAGE: return 1;
        case WALLE_VK_REVEAL: return 2;
        case WALLE_VK_GLASS_REGULAR:
        case WALLE_VK_GLASS_CLEAR: return 3;
        case WALLE_VK_TINT_MASK: return 4;
        case WALLE_VK_TINT_GRADIENT: return 5;
        case WALLE_VK_TINT_APPLY_MASK: return 6;
        case WALLE_VK_TINT_COMPOSITE: return 7;
        case WALLE_VK_FACE: return 8;
        case WALLE_VK_HIGHLIGHT: return 9;
        case WALLE_VK_FINISH_IMAGE: return 10;
        case WALLE_VK_PRODUCT_FINISH: return 11;
        case WALLE_VK_PASS_COUNT: break;
    }
    CHECK(false);
    return 0;
}

static void inspect(const struct walle_vk_frame *frame, uint32_t width, uint32_t height)
{
    CHECK(frame);
    if (!frame->draw_count)
        return;
    CHECK(frame->draws);
    if (frame->capture) {
        CHECK(frame->pyramid);
        CHECK(frame->capture->scale > 0 && frame->capture->scale <= 1);
        CHECK(frame->capture->surface[2] > 0 && frame->capture->surface[3] > 0);
        CHECK(frame->capture->texture[0] > 0 && frame->capture->texture[1] > 0);
        CHECK(frame->pyramid->down_count <= 32);
    }
    CHECK(frame->material_opacity >= 0 && frame->material_opacity <= 1);
    /* Empty/collapsed mask and GB DODs may cull those nodes independently. */
    unsigned previous_pass = 0;
    for (size_t i = 0; i < frame->draw_count; ++i) {
        const struct walle_vk_draw *d = &frame->draws[i];
        unsigned current_pass = pass_order(d->pass);
        CHECK(current_pass >= previous_pass);
        previous_pass = current_pass;
        CHECK(d->vertex_count > 0 && d->vertex_count <= 36);
        CHECK(d->index_count > 0 && d->index_count <= 96 && d->index_count % 3 == 0);
        CHECK(d->vertices && d->indices);
        /* Original idle foreground is a zero-alpha VCM operator; see IDLE_FACE.md. */
        CHECK(d->pass != WALLE_VK_FACE);
        CHECK(d->scissor[0] >= 0 && d->scissor[1] >= 0 && d->scissor[2] >= 0 && d->scissor[3] >= 0);
        CHECK((uint64_t)d->scissor[0] + (uint64_t)d->scissor[2] <= width);
        CHECK((uint64_t)d->scissor[1] + (uint64_t)d->scissor[3] <= height);
        for (size_t j = 0; j < d->index_count; ++j)
            CHECK(d->indices[j] < d->vertex_count);
        for (size_t j = 0; j < d->vertex_count; ++j)
            for (unsigned k = 0; k < 2; ++k) {
                CHECK(isfinite(d->vertices[j].position[k]));
                CHECK(isfinite(d->vertices[j].local[k]));
                CHECK(isfinite(d->vertices[j].source_uv[k]));
            }
        if (d->pass == WALLE_VK_GLASS_REGULAR || d->pass == WALLE_VK_GLASS_CLEAR) {
            CHECK(frame->capture && frame->pyramid);
            float mode;
            memcpy(&mode, d->glass + 8, 4);
            CHECK(mode == 4 || mode == -4 || mode == 0);
            CHECK(d->shape_mode == (uint32_t)(int32_t)mode);
        } else if (d->pass == WALLE_VK_REVEAL || d->pass == WALLE_VK_REVEAL_IMAGE
                   || d->pass == WALLE_VK_FINISH_IMAGE || d->pass == WALLE_VK_PRODUCT_FINISH
                   || d->pass == WALLE_VK_TINT_APPLY_MASK || d->pass == WALLE_VK_TINT_COMPOSITE) {
            CHECK(d->shape_mode == 0 || d->shape_mode == 1);
        } else {
            CHECK(d->shape_mode == 0 || d->shape_mode == 4 || d->shape_mode == 5);
        }
    }
}

static struct walle_transition_options valid_options(void)
{
    return (struct walle_transition_options){
        .style = WM_REGULAR, .dark = false, .active = true,
        .motion = WALLE_TRANSITION_SWEEP, .origin = {.5, .5}, .direction = {1, 0},
    };
}

static void same_geometry(const struct walle_transition_geometry *a,
                          const struct walle_transition_geometry *b)
{
    CHECK(a->center[0] == b->center[0] && a->center[1] == b->center[1]);
    CHECK(a->radius == b->radius && a->scale == b->scale);
    CHECK(a->material_opacity == b->material_opacity);
}

static size_t invalid_contracts(void)
{
    struct walle_transition_options good = valid_options();
    struct walle_transition *stable = nullptr;
    CHECK(walle_transition_create(641, 421, 1.5, &good, &stable));
    const struct walle_vk_frame *retained;
    CHECK(walle_transition_build(stable, .5, 0, false, &retained));
    CHECK(retained->draw_count > 0);
    size_t retained_count = retained->draw_count;
    float retained_x = retained->draws[0].vertices[0].position[0];
    struct walle_transition_geometry before, after;
    CHECK(walle_transition_geometry_at(stable, .5, &before));
    constexpr double bad_scale[] = {0, -1, INFINITY, -INFINITY, NAN, DBL_MIN};
    constexpr uint32_t bad_size[][2] = {{0, 1}, {1, 0}, {UINT32_MAX, 1}, {1, UINT32_MAX}};
    size_t cases = 0;
    for (unsigned k = 0; k < 22; ++k) {
        struct walle_transition_options options = good;
        uint32_t width = 641, height = 421;
        double backing = 1.5;
        if (k < 6) backing = bad_scale[k];
        else if (k < 10) { width = bad_size[k-6][0]; height = bad_size[k-6][1]; }
        else switch (k) {
            case 10: options.style = (enum wm_style)255; break;
            case 11: options.motion = (enum walle_transition_motion)255; break;
            case 12: options.origin[0] = -.01; break;
            case 13: options.origin[1] = 1.01; break;
            case 14: options.origin[0] = NAN; break;
            case 15: options.origin[1] = INFINITY; break;
            case 16: options.direction[0] = options.direction[1] = 0; break;
            case 17: options.direction[0] = NAN; break;
            case 18: options.direction[1] = INFINITY; break;
            case 19: options.direction[0] = options.direction[1] = DBL_MAX; break;
            case 20: options.direction[0] = -INFINITY; break;
            case 21: options.origin[1] = -INFINITY; break;
        }
        snprintf(test_case, sizeof test_case, "invalid configuration %u, atomic update", k);
        struct walle_transition *created = nullptr;
        CHECK(!walle_transition_create(width, height, backing, &options, &created));
        CHECK(created == nullptr);
        CHECK(!walle_transition_update(stable, width, height, backing, &options));
        CHECK(retained->draw_count == retained_count);
        CHECK(retained->draws[0].vertices[0].position[0] == retained_x);
        CHECK(walle_transition_geometry_at(stable, .5, &after));
        same_geometry(&before, &after);
        ++cases;
    }
    snprintf(test_case, sizeof test_case, "null arguments and nonfinite progress");
    struct walle_transition *created = nullptr;
    CHECK(!walle_transition_create(100, 100, 1, nullptr, &created));
    CHECK(created == nullptr);
    CHECK(!walle_transition_create(100, 100, 1, &good, nullptr));
    CHECK(!walle_transition_update(nullptr, 100, 100, 1, &good));
    CHECK(!walle_transition_update(stable, 100, 100, 1, nullptr));
    CHECK(!walle_transition_geometry_at(nullptr, .5, &after));
    CHECK(!walle_transition_geometry_at(stable, .5, nullptr));
    CHECK(!walle_transition_geometry_at(stable, NAN, &after));
    CHECK(!walle_transition_build(nullptr, .5, 0, false, &retained));
    CHECK(!walle_transition_build(stable, .5, 0, false, nullptr));
    CHECK(!walle_transition_build(stable, INFINITY, 0, false, &retained));
    CHECK(!walle_transition_build(stable, -INFINITY, 0, false, &retained));
    CHECK(!walle_transition_build(stable, NAN, 0, false, &retained));
    CHECK(walle_transition_build(stable, -10, 0, false, &retained));
    CHECK(!retained->plain_incoming && retained->draw_count == 0);
    CHECK(walle_transition_build(stable, 10, 0, false, &retained));
    CHECK(retained->plain_incoming && retained->draw_count == 0);
    walle_transition_destroy(nullptr);
    walle_transition_destroy(stable);
    /* Native packet limits remain checked at the source-planning boundary.
     * The old thin-output rejection relied on the now-removed scale override. */
    struct wm_capture_plan capture;
    struct wm_pyramid_plan pyramid;
    CHECK(wm_capture_full(32769,64,1,true,&capture));
    CHECK(!wm_pyramid_build(&capture,1,80,1,&pyramid));
    ++cases;
    struct walle_transition_options inactive=good;
    inactive.active=false;
    struct walle_transition* inactive_transition=nullptr;
    CHECK(walle_transition_create(641,421,1.5,&inactive,&inactive_transition));
    CHECK(walle_transition_build(inactive_transition,.5, 0, false,&retained));
    inspect(retained,641,421);
    walle_transition_destroy(inactive_transition);
    return cases;
}

static size_t extended_contracts(void)
{
    /* CPU packet construction can exceed a particular GPU's dimensions. The
     * capture/blur constructors must reject unrepresentable source fields;
     * there is no arbitrary16K output-size limit. Device limits are separate. */
    constexpr uint32_t sizes[][2] = {{16385, 1024}, {32768, 4096}, {65536, 65536}};
    constexpr double scales[] = {1, 1.25, 3};
    constexpr double samples[] = {0, 1e-12, .25, .5, .75, .999999999999, 1};
    size_t cases = 0;
    for (unsigned s = 0; s < sizeof sizes / sizeof *sizes; ++s)
        for (unsigned style = 0; style < 2; ++style)
            for (unsigned motion = 0; motion < 2; ++motion) {
                snprintf(test_case, sizeof test_case, "wide configuration %ux%u style%u motion%u",
                         sizes[s][0], sizes[s][1], style, motion);
                struct walle_transition_options options = valid_options();
                options.style = (enum wm_style)style;
                options.motion = (enum walle_transition_motion)motion;
                options.tint = (struct wm_tint){.present = true, .srgb = {0, 0, 0, 0}};
                options.origin[0] = .2;
                options.origin[1] = .8;
                options.direction[0] = -3;
                options.direction[1] = 4;
                struct walle_transition *t = nullptr;
                CHECK(walle_transition_create(sizes[s][0], sizes[s][1], scales[s], &options, &t));
                for (unsigned p = 0; p < sizeof samples / sizeof *samples; ++p) {
                    const struct walle_vk_frame *frame;
                    CHECK(walle_transition_build(t, samples[p], 0, false, &frame));
                    inspect(frame, sizes[s][0], sizes[s][1]);
                }
                /* A successful update adopts the new output and origin. This
                 * assertion uses the public lens-center contract, not easing. */
                options.motion = WALLE_TRANSITION_LENS;
                options.origin[0] = .25;
                options.origin[1] = .75;
                CHECK(walle_transition_update(t, 333, 199, 1.25, &options));
                struct walle_transition_geometry g;
                CHECK(walle_transition_geometry_at(t, .5, &g));
                CHECK(fabs(g.center[0] - .25 * 333 / 1.25) < 1e-12);
                CHECK(fabs(g.center[1] - .75 * 199 / 1.25) < 1e-12);
                const struct walle_vk_frame *frame;
                CHECK(walle_transition_build(t, .5, 0, false, &frame));
                inspect(frame, 333, 199);
                walle_transition_destroy(t);
                ++cases;
            }
    return cases;
}

int main(void)
{
    constexpr uint32_t sizes[][2] = {{320,180},{641,421},{1920,1080},{3840,2160},{1080,1920},
                                    {1,1},{1,64},{64,1},{2,3}};
    constexpr double origins[][2] = {{0.5,0.5},{0,0},{1,1},{0.2,0.8}};
    constexpr double directions[][2] = {{1,0},{-1,0},{0,1},{0.6,0.8}};
    size_t frames = 0, trajectories = 0;
    for (unsigned size = 0; size < sizeof sizes / sizeof *sizes; ++size)
        for (unsigned origin = 0; origin < 4; ++origin)
            for (unsigned motion = 0; motion < 2; ++motion)
                for (unsigned style = 0; style < 2; ++style) {
                    snprintf(test_case, sizeof test_case, "trajectory %ux%u, origin %u, motion %u, style %u",
                             sizes[size][0], sizes[size][1], origin, motion, style);
                    struct walle_transition_options options = {
                        .style = (enum wm_style)style, .dark = (origin & 1) != 0, .active = true,
                        .tint = {.present = origin > 1, .srgb = {32,112,218,180}},
                        .motion = (enum walle_transition_motion)motion,
                    };
                    memcpy(options.origin, origins[origin], sizeof options.origin);
                    memcpy(options.direction, directions[origin], sizeof options.direction);
                    double backing = size & 1 ? 1.5 : 1;
                    struct walle_transition *t = nullptr;
                    CHECK(walle_transition_create(sizes[size][0], sizes[size][1], backing, &options, &t));
                    const struct walle_vk_frame *frame;
                    CHECK(walle_transition_build(t, 0, 0, false, &frame));
                    CHECK(frame->draw_count == 0 && !frame->plain_incoming && !frame->capture);
                    CHECK(walle_transition_build(t, 1, 0, false, &frame));
                    CHECK(frame->draw_count == 0 && frame->plain_incoming && !frame->capture);
                    CHECK(walle_transition_build(t, 0.3, 0, true, &frame));
                    CHECK(frame->draw_count == 0 && frame->plain_incoming);
                    CHECK(!walle_transition_build(t, nan(""), 0, false, &frame));
                    constexpr double close_values[] = {1e-15,1e-9,1e-6,0.919999999,0.999,0.999999,0.999999999999};
                    for (unsigned close = 0; close < sizeof close_values / sizeof *close_values; ++close) {
                        CHECK(walle_transition_build(t, close_values[close], 0, false, &frame));
                        inspect(frame, sizes[size][0], sizes[size][1]);
                    }
                    struct walle_transition_geometry last;
                    CHECK(walle_transition_geometry_at(t, 0, &last));
                    bool covered[9] = {};
                    double previous_opacity = 1;
                    for (unsigned step = 1; step <= 200; ++step) {
                        double p = (double)step / 200;
                        struct walle_transition_geometry g;
                        CHECK(walle_transition_geometry_at(t, p, &g));
                        CHECK(g.material_opacity <= previous_opacity + 1e-12);
                        previous_opacity = g.material_opacity;
                        CHECK(isfinite(g.radius) && g.radius >= 0);
                        if (motion == WALLE_TRANSITION_SWEEP)
                            CHECK(g.radius == last.radius);
                        else {
                            CHECK(g.radius >= last.radius);
                            CHECK(fabs(g.center[0] - options.origin[0] * sizes[size][0] / backing) < 1e-9);
                            CHECK(fabs(g.center[1] - options.origin[1] * sizes[size][1] / backing) < 1e-9);
                        }
                        if (motion == WALLE_TRANSITION_SWEEP) {
                            double dx = g.center[0] - last.center[0], dy = g.center[1] - last.center[1];
                            CHECK(dx * options.direction[0] + dy * options.direction[1] >= -1e-9);
                            CHECK(fabs(dx * options.direction[1] - dy * options.direction[0]) < 1e-9);
                        }
                        for (unsigned y = 0; y < 3; ++y)
                            for (unsigned x = 0; x < 3; ++x) {
                                double px = x * (sizes[size][0] / backing) / 2;
                                double py = y * (sizes[size][1] / backing) / 2;
                                bool inside = hypot(px-g.center[0],py-g.center[1]) <= g.radius + 1e-9;
                                CHECK(!covered[y*3+x] || inside);
                                covered[y*3+x] = inside;
                            }
                        CHECK(walle_transition_build(t, p, 0, false, &frame));
                        inspect(frame, sizes[size][0], sizes[size][1]);
                        last = g;
                        ++frames;
                    }
                    for (unsigned i = 0; i < 9; ++i)
                        CHECK(covered[i]);
                    CHECK(last.material_opacity == 0);
                    double aa_margin = 0.5 * fmin(1, 2 / backing);
                    for (unsigned y = 0; y < 2; ++y)
                        for (unsigned x = 0; x < 2; ++x) {
                            double px = x * sizes[size][0] / backing;
                            double py = y * sizes[size][1] / backing;
                            double clearance = last.radius - hypot(px-last.center[0],py-last.center[1]);
                            CHECK(clearance >= aa_margin - 1e-8);
                        }
                    /* Continuous endpoint trajectory and atomic invalid update. */
                    struct walle_transition_geometry a, b;
                    CHECK(walle_transition_geometry_at(t, 0.5-1e-7, &a));
                    CHECK(walle_transition_geometry_at(t, 0.5+1e-7, &b));
                    CHECK(hypot(a.center[0]-b.center[0],a.center[1]-b.center[1]) < 0.1);
                    CHECK(fabs(a.radius-b.radius) < 0.1);
                    CHECK(!walle_transition_update(t, 0, 100, 1, &options));
                    CHECK(walle_transition_build(t, 0.5, 0, false, &frame));
                    inspect(frame, sizes[size][0], sizes[size][1]);
                    walle_transition_destroy(t);
                    ++trajectories;
                }
    size_t invalid = invalid_contracts();
    size_t extended = extended_contracts();
    printf("PASS %zu trajectories, %zu generated frames; %zu invalid configurations and %zu wide/update cases; endpoints, monotonic coverage, continuity, indices and finite vertices\n",
           trajectories, frames, invalid, extended);
    return 0;
}
