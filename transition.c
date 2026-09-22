#include "transition.h"
#include "geometry.h"
#include "scissor.h"

#include <float.h>
#include <math.h>
#include <stddef.h>
#include <stdbit.h>
#include <stdlib.h>
#include <string.h>

/* Application motion constants; these do not alter material coefficients. */
constexpr size_t DRAW_CAPACITY = 32;
constexpr double FADE_START    = 0.80;
constexpr double MOTION_END    = 0.92;
constexpr double PI            = 0x1.921fb54442d18p+1;
static_assert(__STDC_ENDIAN_NATIVE__ == __STDC_ENDIAN_LITTLE__);
static_assert(sizeof(struct wm_vertex) == sizeof(struct walle_vk_vertex));
static_assert(offsetof(struct wm_vertex, local_uv) == offsetof(struct walle_vk_vertex, local));
static_assert(offsetof(struct wm_vertex, source_uv) == offsetof(struct walle_vk_vertex, source_uv));

struct walle_transition
{
    uint32_t width, height;
    double   backing_scale, local_radius, path_start, path_end, lens_end_scale;
    double   origin[2], direction[2];
    struct walle_transition_options options;
    struct wm_recipe*               recipe;
    struct wm_shader_packet         packet;
    struct wm_glass_extent          glass_extent;
    struct wm_capture_plan          capture;
    struct wm_pyramid_plan          pyramid;
    double                          source_scale, source_origin[2];
    uint32_t                        source_size[2];
    struct walle_vk_frame           frame;
    struct walle_vk_draw            draws[DRAW_CAPACITY];
    struct walle_vk_vertex          vertices[DRAW_CAPACITY][36];
    uint32_t                        indices[DRAW_CAPACITY][96];
};

static double clamp01(double x)
{
    return x < 0 ? 0 : x > 1 ? 1 : x;
}

static double ease(double t)
{
    t = clamp01(t);
    return clamp01(t * t * t * (10 + t * (-15 + t * 6)));
}

static void store_float(uint8_t* bytes, size_t offset, float value)
{
    memcpy(bytes + offset, &value, sizeof value);
}

static bool prepare(struct walle_transition*               t,
                    uint32_t                               width,
                    uint32_t                               height,
                    double                                 backing_scale,
                    const struct walle_transition_options* options)
{
    /* Scissors carry signed32 coordinates; capture/blur constructors check
     * their own source packet fields. Device limits belong to the renderer. */
    if (!t || !options || !width || !height || width > (uint32_t)INT32_MAX
        || height > (uint32_t)INT32_MAX || !isfinite(backing_scale) || backing_scale <= 0
        || (options->style != WM_REGULAR && options->style != WM_CLEAR)
        || (options->motion != WALLE_TRANSITION_SWEEP && options->motion != WALLE_TRANSITION_LENS))
        return false;
    for (unsigned i = 0; i < 2; ++i)
        if (!isfinite(options->origin[i]) || options->origin[i] < 0 || options->origin[i] > 1
            || !isfinite(options->direction[i]))
            return false;
    double length = hypot(options->direction[0], options->direction[1]);
    if (!isfinite(length) || length == 0)
        return false;
    double w = width / backing_scale, h = height / backing_scale;
    double diagonal = hypot(w, h);
    /* Source sdf_fill clamps fwidth to1 root point. A circle distance is
     * 1-Lipschitz, so fwidth is also <=2/backing_scale. Half that support
     * is the inward clearance required for full source coverage. Inflate
     * the fixed nominal diagonal radius by this geometric AA margin. */
    double aa_margin = 0.5 * fmin(1, 2 / backing_scale);
    double radius    = diagonal + aa_margin;
    if (!isfinite(radius) || radius <= 0 || radius > FLT_MAX / 4)
        return false;
    t->width         = width;
    t->height        = height;
    t->backing_scale = backing_scale;
    t->local_radius  = radius;
    t->options       = *options;
    for (unsigned i = 0; i < 2; ++i) {
        t->direction[i] = options->direction[i] / length;
        t->origin[i]    = options->origin[i] * (i ? h : w);
    }
    struct wm_material_input input = {
        .style         = options->style,
        .dark          = options->dark,
        .active        = options->active,
        .width_points  = radius * 2,
        .height_points = radius * 2,
        .backing_scale = backing_scale,
        .tint          = options->tint,
    };
    t->recipe = wm_recipe_create(&input);
    if (!t->recipe)
        return false;
    struct wm_render_domain domain = {
        .source_width  = width,
        .source_height = height,
        .source_scale  = 1,
        .transform     = {backing_scale, 0, 0, -backing_scale},
        .headroom      = 1,
        .gamma         = 2.2,
        .global_light  = false,
        .light_angle   = PI / 2,
        .light_opacity = nan(""),
        .light_spread  = nan(""),
        .light_height  = nan(""),
    };
    if (!wm_recipe_pack(t->recipe, &domain, &t->packet) || !t->packet.plan.has_backdrop)
        return false;
    bool replicate = t->packet.plan.maximum_refraction > 0;
    /* Application full-image policy: retain the source preferred scale except
     * when it would create an empty capture on a positive tiny output. */
    double minimum_capture_scale = 1.0 / (width < height ? width : height);
    double capture_scale = fmin(1, fmax(t->packet.plan.backdrop_scale, minimum_capture_scale));
    if (!wm_capture_full(width, height, capture_scale, replicate, &t->capture)
        || !wm_pyramid_build(&t->capture,
                             (float)t->packet.plan.blur_min,
                             (float)t->packet.plan.blur_max,
                             backing_scale,
                             &t->pyramid))
        return false;
    bool pyramid    = t->pyramid.mip_count != 0;
    t->source_scale = pyramid ? t->pyramid.sample_scale : t->capture.scale;
    for (unsigned i = 0; i < 2; ++i) {
        t->source_size[i]   = pyramid ? t->pyramid.texture[i] : t->capture.texture[i];
        t->source_origin[i] = pyramid ? t->pyramid.bounds[i] : t->capture.surface[i];
    }
    domain.source_width  = t->source_size[0];
    domain.source_height = t->source_size[1];
    domain.source_scale  = t->source_scale;
    if (!wm_recipe_pack(t->recipe, &domain, &t->packet)
        || !wm_recipe_glass_extent(t->recipe, &t->glass_extent))
        return false;
    /* Face/group opacity is not encoded in their matrices. Selected source
     * recipes use binary layer visibility; never silently ignore another value. */
    if ((t->packet.plan.face_opacity != 0 && t->packet.plan.face_opacity != 1)
        || (t->packet.plan.tint_group_opacity != 0 && t->packet.plan.tint_group_opacity != 1))
        return false;
    double minimum = 0, latest_entry = -DBL_MAX, farthest = 0;
    for (unsigned y = 0; y < 2; ++y)
        for (unsigned x = 0; x < 2; ++x) {
            double qx = x * w - t->origin[0], qy = y * h - t->origin[1];
            double projection    = qx * t->direction[0] + qy * t->direction[1];
            double perpendicular = -qx * t->direction[1] + qy * t->direction[0];
            double radicand      = fmax(0, diagonal * diagonal - perpendicular * perpendicular);
            latest_entry         = fmax(latest_entry, projection - sqrt(radicand));
            farthest             = fmax(farthest, hypot(qx, qy));
            if (projection < minimum)
                minimum = projection;
        }
    double guard = t->packet.plan.shadow_grow
                   + hypot(t->packet.plan.shadow_offset[0], t->packet.plan.shadow_offset[1])
                   + fmax(t->packet.plan.highlight_pad, 1) + 2 / backing_scale;
    t->path_start     = minimum - radius - guard;
    t->path_end       = latest_entry;
    t->lens_end_scale = (farthest + aa_margin) / radius;
    return true;
}

bool walle_transition_create(uint32_t                               width,
                             uint32_t                               height,
                             double                                 backing_scale,
                             const struct walle_transition_options* options,
                             struct walle_transition**              result)
{
    if (!result)
        return false;
    *result                    = nullptr;
    struct walle_transition* t = calloc(1, sizeof *t);
    if (!t)
        return false;
    if (!prepare(t, width, height, backing_scale, options)) {
        walle_transition_destroy(t);
        return false;
    }
    *result = t;
    return true;
}

bool walle_transition_update(struct walle_transition*               t,
                             uint32_t                               width,
                             uint32_t                               height,
                             double                                 backing_scale,
                             const struct walle_transition_options* options)
{
    if (!t)
        return false;
    struct walle_transition* next;
    if (!walle_transition_create(width, height, backing_scale, options, &next))
        return false;
    wm_recipe_destroy(t->recipe);
    *t = *next;
    free(next);
    t->frame = (struct walle_vk_frame){};
    return true;
}

void walle_transition_destroy(struct walle_transition* t)
{
    if (t) {
        wm_recipe_destroy(t->recipe);
        free(t);
    }
}

bool walle_transition_geometry_at(const struct walle_transition*    t,
                                  double                            progress,
                                  struct walle_transition_geometry* g)
{
    if (!t || !g || !isfinite(progress))
        return false;
    double p = clamp01(progress), motion = ease(p / MOTION_END);
    double distance = t->options.motion == WALLE_TRANSITION_SWEEP
                          ? t->path_start + (t->path_end - t->path_start) * motion
                          : 0;
    g->scale        = t->options.motion == WALLE_TRANSITION_LENS ? motion * t->lens_end_scale : 1;
    g->radius       = t->local_radius * g->scale;
    g->center[0]    = t->origin[0] + t->direction[0] * distance;
    g->center[1]    = t->origin[1] + t->direction[1] * distance;
    g->material_opacity = 1 - ease((p - FADE_START) / (1 - FADE_START));
    return true;
}

static struct walle_vk_draw* append(struct walle_transition* t,
                                    enum walle_vk_pass       pass,
                                    const struct wm_vertex*  vertices,
                                    uint32_t                 vertex_count,
                                    const uint32_t*          indices,
                                    uint32_t                 index_count,
                                    uint32_t                 mode)
{
    if (t->frame.draw_count == DRAW_CAPACITY || !vertex_count || vertex_count > 36 || !index_count
        || index_count > 96 || index_count % 3)
        return nullptr;
    double lo[2] = {t->width, t->height}, hi[2] = {0, 0};
    for (uint32_t i = 0; i < vertex_count; ++i)
        for (unsigned a = 0; a < 2; ++a) {
            double value = vertices[i].position[a];
            if (!isfinite(value))
                return nullptr;
            lo[a] = fmin(lo[a], value);
            hi[a] = fmax(hi[a], value);
        }
    for (uint32_t i = 0; i < index_count; ++i)
        if (indices[i] >= vertex_count)
            return nullptr;
    size_t                slot = t->frame.draw_count++;
    struct walle_vk_draw* d    = &t->draws[slot];
    *d                         = (struct walle_vk_draw){.pass         = pass,
                                                        .vertex_count = vertex_count,
                                                        .index_count  = index_count,
                                                        .shape_mode   = mode,
                                                        .edr_scale    = 1};
    memcpy(t->vertices[slot], vertices, vertex_count * sizeof *vertices);
    memcpy(t->indices[slot], indices, index_count * sizeof *indices);
    d->vertices = t->vertices[slot];
    d->indices  = t->indices[slot];
    for (unsigned a = 0; a < 2; ++a) {
        double  limit     = a ? t->height : t->width;
        int32_t start     = (int32_t)fmax(0, fmin(limit, floor(lo[a])));
        int32_t end       = (int32_t)fmax(0, fmin(limit, ceil(hi[a])));
        d->scissor[a]     = start;
        d->scissor[a + 2] = end > start ? end - start : 0;
    }
    return d;
}

static void white_fill(uint8_t bytes[16])
{
    memset(bytes, 0, 16);
    constexpr uint16_t white[4] = {0x3c00, 0x3c00, 0x3c00, 0x3c00};
    memcpy(bytes + 8, white, sizeof white);
}

static bool sdf_draws(struct walle_transition*                t,
                      const struct walle_transition_geometry* g,
                      enum walle_vk_pass                      pass,
                      double                                  pad,
                      double                                  maximum,
                      bool                                    glass_surface)
{
    double        matrix[4]      = {g->scale, 0, 0, g->scale};
    const double* element_matrix = g->scale == 1 ? nullptr : matrix;
    float         element_scale  = wm_sdf_scale(element_matrix);
    float         arguments[12];
    double        outset = fmin(pad, 4096), radius = t->local_radius;
    if (!(element_scale > 0)
        || !wm_sdf_arguments(radius * 2,
                             radius * 2,
                             element_matrix,
                             radius,
                             false,
                             t->packet.plan.ovalization,
                             outset,
                             arguments)
        || arguments[0] <= 0 || arguments[1] <= 0)
        return false;
    double offset[2] = {0, 0};
    double shadow    = 0;
    if (glass_surface) {
        shadow = t->packet.plan.shadow_grow;
        /* Recipe offsets are root points; geometry expects local element units. */
        offset[0] = t->packet.plan.shadow_offset[0] / g->scale;
        offset[1] = t->packet.plan.shadow_offset[1] / g->scale;
    }
    struct wm_sdf_grid grid;
    if (!wm_sdf_grid_build(radius * 2,
                           radius * 2,
                           radius,
                           false,
                           outset,
                           fmin(maximum, 4096),
                           shadow,
                           offset,
                           glass_surface,
                           element_scale,
                           &grid))
        return false;
    double           scale     = t->backing_scale * g->scale;
    double           affine[6] = {scale,
                                  0,
                                  0,
                                  -scale,
                                  t->backing_scale * g->center[0] - scale * radius,
                                  t->backing_scale * g->center[1] + scale * radius};
    struct wm_vertex vertices[36];
    uint32_t         count = wm_sdf_vertices(
        &grid, affine, t->source_scale, t->source_origin, t->source_size, vertices);
    for (uint32_t i = 0; i < grid.group_count; ++i) {
        const struct wm_mesh_group* group = &grid.groups[i];
        uint32_t                    mode  = (uint32_t)(int32_t)group->mode;
        struct walle_vk_draw*       d
            = append(t, pass, vertices, count, group->indices, group->index_count, mode);
        if (!d)
            return false;
        if (glass_surface) {
            memcpy(d->glass, arguments, sizeof arguments);
            store_float(d->glass, 8, group->mode);
            memcpy(d->glass + 48, t->packet.glass_lph, sizeof t->packet.glass_lph);
            double content[4] = {
                g->center[0] - g->radius, -g->center[1] - g->radius, 2 * g->radius, 2 * g->radius};
            double  backdrop[4]  = {0,
                                    -(double)t->height / t->backing_scale,
                                    t->width / t->backing_scale,
                                    t->height / t->backing_scale};
            double  transform[6] = {t->backing_scale, 0, 0, -t->backing_scale, 0, 0};
            double  dod[4];
            int32_t scissor[4];
            if (!wm_glass_dod(&t->glass_extent, content, backdrop, dod)
                || !wm_scissor_transform(dod, transform, t->width, t->height, false, scissor))
                return false;
            for (unsigned axis = 0; axis < 2; ++axis) {
                int32_t start = d->scissor[axis] > scissor[axis] ? d->scissor[axis] : scissor[axis];
                int32_t end0  = d->scissor[axis] + d->scissor[axis + 2];
                int32_t end1  = scissor[axis] + scissor[axis + 2];
                int32_t end   = end0 < end1 ? end0 : end1;
                d->scissor[axis]     = start;
                d->scissor[axis + 2] = end > start ? end - start : 0;
            }
        } else {
            memcpy(d->effect, arguments, sizeof arguments);
            if (pass == WALLE_VK_HIGHLIGHT) {
                memcpy(d->effect + 48, t->packet.highlight_vcm, 48);
                memcpy(d->effect + 96, t->packet.key_fill, 40);
            } else if (pass == WALLE_VK_TINT_GRADIENT) {
                memcpy(d->effect + 48, t->packet.tint_vcm, 48);
                memcpy(d->effect + 136, t->packet.tint_gradient, 24);
            } else if (pass == WALLE_VK_TINT_MASK) {
                memcpy(d->effect + 160, t->packet.tint_mask_fill, 16);
            } else {
                white_fill(d->effect + 160);
            }
        }
    }
    return true;
}

static bool face_draw(struct walle_transition* t, const struct walle_transition_geometry* g)
{
    /* Native circular one-part face. Its normalized image coordinates are
     * circle_image10, not the continuous-corner image11 parameterization. */
    double           radius = g->radius * t->backing_scale;
    double           cx = g->center[0] * t->backing_scale, cy = g->center[1] * t->backing_scale;
    struct wm_vertex v[4] = {
        {{(float)(cx - radius), (float)(cy + radius)}, {-1, -1}, {0, 0}},
        {{(float)(cx + radius), (float)(cy + radius)}, {1, -1}, {0, 0}},
        {{(float)(cx + radius), (float)(cy - radius)}, {1, 1}, {0, 0}},
        {{(float)(cx - radius), (float)(cy - radius)}, {-1, 1}, {0, 0}},
    };
    constexpr uint32_t    indices[6] = {0, 1, 2, 2, 3, 0};
    struct walle_vk_draw* d          = append(t, WALLE_VK_FACE, v, 4, indices, 6, 10);
    if (!d)
        return false;
    memcpy(d->effect + 48, t->packet.face_vcm, 48);
    return true;
}

static bool tint_composite(struct walle_transition* t)
{
    float              w = (float)t->width, h = (float)t->height;
    struct wm_vertex   v[4]       = {{{0, 0}, {0, 0}, {0, 0}},
                                     {{w, 0}, {0, 0}, {1, 0}},
                                     {{w, h}, {0, 0}, {1, 1}},
                                     {{0, h}, {0, 0}, {0, 1}}};
    constexpr uint32_t indices[6] = {0, 1, 2, 2, 3, 0};
    return append(t, WALLE_VK_TINT_COMPOSITE, v, 4, indices, 6, 0) != nullptr;
}

bool walle_transition_build(struct walle_transition*      t,
                            double                        progress,
                            bool                          first_boot,
                            const struct walle_vk_frame** result)
{
    if (!t || !result || !isfinite(progress))
        return false;
    *result  = nullptr;
    t->frame = (struct walle_vk_frame){};
    if (first_boot || progress >= 1) {
        t->frame.plain_incoming = true;
        *result                 = &t->frame;
        return true;
    }
    if (progress <= 0) {
        *result = &t->frame;
        return true;
    }
    struct walle_transition_geometry g;
    if (!walle_transition_geometry_at(t, progress, &g))
        return false;
    /* At sub-float geometry, padding subtraction can make the SDF half-size
     * zero. No singular source evaluation is submitted for that invisible span. */
    if (g.radius * t->backing_scale < 0.001) {
        *result = &t->frame;
        return true;
    }
    t->frame.draws                     = t->draws;
    t->frame.capture                   = &t->capture;
    t->frame.pyramid                   = &t->pyramid;
    t->frame.material_opacity          = (float)g.material_opacity;
    const struct wm_plan_parameters* p = &t->packet.plan;
    if (!sdf_draws(t, &g, WALLE_VK_REVEAL, 1, 4096, false))
        return false;
    enum walle_vk_pass glass
        = t->packet.glass_texture_function == 0x47 ? WALLE_VK_GLASS_CLEAR : WALLE_VK_GLASS_REGULAR;
    double inner = p->smoothness - p->output_minimum;
    if (!sdf_draws(t, &g, glass, 0, inner, true))
        return false;
    if (p->has_tint && p->tint_group_opacity != 0) {
        t->frame.tint_ramp_rgba16f = t->packet.tint_ramp_rgba16f;
        if (!sdf_draws(t, &g, WALLE_VK_TINT_MASK, p->tint_mask_pad, p->tint_mask_maximum, false)
            || !sdf_draws(t,
                          &g,
                          WALLE_VK_TINT_GRADIENT,
                          p->tint_gradient_pad,
                          p->tint_gradient_maximum,
                          false)
            || !tint_composite(t))
            return false;
    }
    if (p->face_opacity != 0 && !face_draw(t, &g))
        return false;
    if (p->has_highlight && p->highlight_opacity != 0
        && !sdf_draws(t, &g, WALLE_VK_HIGHLIGHT, p->highlight_pad, p->highlight_maximum, false))
        return false;
    if (g.material_opacity < 1 && !sdf_draws(t, &g, WALLE_VK_PRODUCT_FINISH, 1, 4096, false))
        return false;
    *result = &t->frame;
    return true;
}
