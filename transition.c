#include "transition.h"
#include "geometry.h"
#include "scissor.h"
#include "sdf_cache.h"
#include "clip.h"

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
    double   origin[2], direction[2], root_transform[6];
    bool capture_ready;
    double frame_time;
    int32_t cache_bounds[4];
    struct wm_sdf_cache_state cache_state, cache_pending;
    bool cache_pending_valid;
    bool suppress_cache;
    struct walle_transition_geometry last_geometry;
    struct wm_sdf_cache_plan cache_plan, cache_pending_plan, cache_requested_plan;
    struct walle_transition_geometry cache_geometry, cache_pending_geometry;
    double cache_transform[6], cache_pending_transform[6];
    bool cache_retained, cache_pending_retained, cached_sdf;
    int32_t sdf_origin[2];
    int32_t glass_scissor[4];
    struct walle_transition_options options;
    struct wm_recipe*               recipe;
    struct wm_shader_packet         packet;
    struct wm_glass_extent          glass_extent;
    struct wm_capture_plan          capture;
    struct wm_pyramid_plan          pyramid;
    struct walle_vk_surface_plan tint_mask_surface, tint_group_surface, reveal_surface;
    struct walle_vk_surface_plan sdf_surface;
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
    wm_sdf_cache_init(&t->cache_state,false);
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
    t->source_scale = 1;
    t->source_size[0] = width;
    t->source_size[1] = height;
    if (!wm_recipe_glass_extent(t->recipe, &t->glass_extent))
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

static bool element_scissor(struct walle_transition*,double,int32_t[4]);

static bool sdf_draws(struct walle_transition*                t,
                      const struct walle_transition_geometry* g,
                      enum walle_vk_pass                      pass,
                      double                                  pad,
                      double                                  maximum,
                      bool                                    glass_surface)
{
    /* The circle is unchanged inside its material root. The root's affine
     * applies to the whole object, including backdrop, effects and capture. */
    const double* element_matrix = nullptr;
    float element_scale = 1;
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
        offset[0] = t->packet.plan.shadow_offset[0];
        offset[1] = t->packet.plan.shadow_offset[1];
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
    bool one_part=!glass_surface && grid.nx==2 && grid.ny==2;
    bool empty_clip=false;
    uint32_t count;
    constexpr uint32_t quad_indices[6]={0,1,2,2,3,0};
    if(one_part) {
        if(grid.group_count!=1) return false;
        double endpoints[4]={grid.x[0],grid.y[0],grid.x[1],grid.y[1]};
        float uv[4]={grid.sx[0],grid.sy[0],grid.sx[1],grid.sy[1]};
        int32_t clip[4];
        if(pass==WALLE_VK_SDF_CACHE)
            memcpy(clip,t->cache_pending_plan.child_dod,sizeof clip);
        else if(!element_scissor(t,pad,clip)) return false;
        enum wm_quad_clip_status status=wm_clip_rect_quad(endpoints,uv,affine,clip,vertices);
        if(status==WM_QUAD_CLIP_INVALID) return false;
        empty_clip=status==WM_QUAD_CLIP_EMPTY;
        count=4;
        /* Keep an empty target pass so masks/cache/gradient surfaces still
         * receive their native clear even when CPU clipping removes the quad. */
    } else count=wm_sdf_vertices(
        &grid,affine,t->source_scale,t->source_origin,t->source_size,vertices);
    for (uint32_t i = 0; i < grid.group_count; ++i) {
        const struct wm_mesh_group* group = &grid.groups[i];
        uint32_t                    mode  = (uint32_t)(int32_t)group->mode;
        struct walle_vk_draw*       d
            = append(t,pass,vertices,count,one_part?quad_indices:group->indices,
                       one_part?6:group->index_count,mode);
        if (!d)
            return false;
        if (pass == WALLE_VK_SDF_CACHE)
            memcpy(d->scissor,t->sdf_surface.rect,sizeof d->scissor);
        if(empty_clip) d->scissor[2]=d->scissor[3]=0;
        if (glass_surface) {
            memcpy(d->glass, arguments, sizeof arguments);
            store_float(d->glass, 8, group->mode);
            memcpy(d->glass + 48, t->packet.glass_lph, sizeof t->packet.glass_lph);
            const int32_t* scissor=t->glass_scissor;
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

static bool offscreen_surface(const int32_t rect[4], const int32_t parent[4],
                              struct walle_vk_surface_plan* surface)
{
    *surface = (struct walle_vk_surface_plan){};
    if (rect[2] <= 0 || rect[3] <= 0) return true;
    memcpy(surface->rect, rect, sizeof surface->rect);
    for (unsigned a = 0; a < 2; ++a) {
        uint64_t rounded = ((uint64_t)(uint32_t)rect[a + 2] + 63) & ~UINT64_C(63);
        if (rounded > UINT32_MAX) return false;
        surface->texture[a] = (uint32_t)rounded;
        int64_t extra = ((int64_t)parent[a] + parent[a + 2])
                          - ((int64_t)rect[a] + rect[a + 2]);
        if (extra < 0) extra = 0;
        uint64_t extent = (uint64_t)(uint32_t)rect[a + 2] + (uint64_t)extra + 1;
        surface->extent[a] = extent < rounded ? (uint32_t)extent : (uint32_t)rounded;
    }
    return true;
}

static bool element_bounds(struct walle_transition* t, double pad, double world[4])
{
    double base[4] = {0,0,2*t->local_radius,2*t->local_radius};
    if (!wm_uniform_rect_transform(base,t->root_transform,world)) return false;
    /* LayerNode::compute_dod @18a68f54c..64c transforms the base first.
     * Target-space Float padding follows, preserving each Double rounding. */
    double delta = -fabs(t->root_transform[0]) * (double)(float)pad;
    world[0] += delta; world[1] += delta;
    world[2] = delta * -2. + world[2];
    world[3] = delta * -2. + world[3];
    return true;
}
static bool element_scissor(struct walle_transition* t, double pad, int32_t rect[4])
{
    double world[4];
    constexpr double identity[6] = {1,0,0,1,0,0};
    return element_bounds(t,pad,world)
        && wm_scissor_transform(world,identity,t->width,t->height,false,rect);
}
static void clip_scissor(const struct walle_transition* t, int32_t rect[4])
{
    for (unsigned a=0;a<2;++a) {
        int64_t lo=rect[a],hi=lo+rect[a+2],limit=a?t->height:t->width;
        if(lo<0)lo=0;
        if(lo>limit)lo=limit;
        if(hi>limit)hi=limit;
        rect[a]=(int32_t)lo;
        rect[a+2]=hi>lo?(int32_t)(hi-lo):0;
    }
}
static bool cached_effect_scissor(struct walle_transition* t, double own_pad, int32_t rect[4])
{
    if (!wm_sdf_cache_copy_dod(t->cache_requested_plan.child_dod,fabs(t->root_transform[0]),
                               t->cache_requested_plan.padding,(float)own_pad,rect)) return false;
    clip_scissor(t,rect);
    return true;
}
static bool prepare_tint_surfaces(struct walle_transition* t)
{
    const struct wm_plan_parameters* p = &t->packet.plan;
    int32_t mask[4], gradient[4], group[4];
    const double pads[2] = {p->tint_mask_pad, p->tint_gradient_pad};
    int32_t* rects[2] = {mask, gradient};
    for (unsigned i = 0; i < 2; ++i) {
        if (!(i==0 && t->cached_sdf ? cached_effect_scissor(t,pads[i],rects[i])
                                    : element_scissor(t,pads[i],rects[i])))
            return false;
    }
    bool m = mask[2] > 0 && mask[3] > 0, g = gradient[2] > 0 && gradient[3] > 0;
    if (!m) memcpy(group, gradient, sizeof group);
    else if (!g) memcpy(group, mask, sizeof group);
    else for (unsigned a = 0; a < 2; ++a) {
        group[a] = mask[a] < gradient[a] ? mask[a] : gradient[a];
        int64_t end = (int64_t)mask[a] + mask[a + 2];
        int64_t other = (int64_t)gradient[a] + gradient[a + 2];
        if (other > end) end = other;
        group[a + 2] = (int32_t)(end - group[a]);
    }
    int32_t canvas[4] = {0, 0, (int32_t)t->width, (int32_t)t->height};
    return offscreen_surface(mask, group, &t->tint_mask_surface)
        && offscreen_surface(group, canvas, &t->tint_group_surface);
}

static bool surface_quad(struct walle_transition* t, enum walle_vk_pass pass,
                         const int32_t rect[4], const struct walle_vk_surface_plan* source,
                         uint32_t mode)
{
    if (rect[2] <= 0 || rect[3] <= 0) return true;
    float x0 = (float)rect[0], y0 = (float)rect[1];
    float x1 = (float)((double)rect[0] + rect[2]), y1 = (float)((double)rect[1] + rect[3]);
    float u0 = 0, v0 = 0, u1 = 0, v1 = 0;
    if (source) {
        float sx = 1.f / (float)source->texture[0], sy = 1.f / (float)source->texture[1];
        u0 = (float)((double)rect[0] - source->rect[0]) * sx;
        v0 = (float)((double)rect[1] - source->rect[1]) * sy;
        u1 = (float)((double)rect[0] + rect[2] - source->rect[0]) * sx;
        v1 = (float)((double)rect[1] + rect[3] - source->rect[1]) * sy;
    }
    struct wm_vertex v[4] = {{{x0,y0},{0,0},{u0,v0}}, {{x1,y0},{0,0},{u1,v0}},
                             {{x1,y1},{0,0},{u1,v1}}, {{x0,y1},{0,0},{u0,v1}}};
    constexpr uint32_t indices[6] = {0,1,2,0,2,3};
    return append(t, pass, v, 4, indices, 6, mode) != nullptr;
}

static bool prepare_reveal_surface(struct walle_transition* t)
{
    double local[4] = {-1, -1, 2 * (t->local_radius + 1), 2 * (t->local_radius + 1)};
    int32_t rect[4], soft[4];
    if (!element_scissor(t,1,rect)) return false;
    /* Updater::aa_round @18a7a61cc is applied to the transformed padded
     * circle BEFORE output clipping. Its soft ROI is retained independently
     * of the logical integer DOD, then set_dest derives the pass extension. */
    double world[4];
    if (!wm_uniform_rect_transform(local,t->root_transform,world)
        || !wm_aa_round(world,true,soft)) return false;
    for (unsigned i=0;i<2;++i) {
        int64_t lo=soft[i], hi=(int64_t)soft[i]+soft[i+2];
        int64_t limit=i?t->height:t->width;
        if(lo<0)lo=0;
        if(hi>limit)hi=limit;
        if(lo>limit)lo=limit;
        soft[i]=(int32_t)lo;
        soft[i+2]=hi>lo?(int32_t)(hi-lo):0;
    }
    return offscreen_surface(rect, soft, &t->reveal_surface);
}
static bool masked_image_draw(struct walle_transition* t, bool finish, float opacity)
{
    const int32_t* rect = t->reveal_surface.rect;
    struct walle_vk_surface_plan incoming = {.rect={0,0,(int32_t)t->width,(int32_t)t->height},
        .texture={t->width,t->height}};
    enum walle_vk_pass image = finish ? WALLE_VK_FINISH_IMAGE : WALLE_VK_REVEAL_IMAGE;
    if (!surface_quad(t, image, rect, &incoming, 1)) return false;
    /* This source image is already output-sized and uses native nearest
     * sampling at 1:1. Preserve its exact global texel mapping independently
     * of the native CGImage's bottom-up storage and normalized-UV rounding. */
    uint32_t origin[2]={(uint32_t)rect[0],(uint32_t)rect[1]};
    memcpy(t->draws[t->frame.draw_count-1].effect,origin,sizeof origin);
    /* CALayer opacity byte: Float clamp, FMADD*255+.5, integer conversion;
     * Render::Layer expands by Float(1/255) before the vertex half narrowing. */
    float value = fminf(fmaxf(opacity,0),1);
    uint8_t encoded = (uint8_t)(int32_t)fmaf(value,255,.5f);
    float expanded = (float)encoded * 0.003921569f;
    store_float(t->draws[t->frame.draw_count-1].effect,176,expanded);
    return surface_quad(t, finish ? WALLE_VK_PRODUCT_FINISH : WALLE_VK_REVEAL,
                        rect,&t->reveal_surface,1);
}

static bool tint_composite(struct walle_transition* t)
{
    const struct walle_vk_surface_plan* mask = &t->tint_mask_surface;
    const struct walle_vk_surface_plan* group = &t->tint_group_surface;
    const int32_t* m = mask->rect;
    const int32_t* g = group->rect;
    if (!surface_quad(t, WALLE_VK_TINT_APPLY_MASK, m, mask, 1)) return false;
    /* emit_combine's transparent outside pieces: top, left, right, bottom. */
    int32_t pieces[4][4] = {
        {g[0], g[1], g[2], m[1] - g[1]},
        {g[0], m[1], m[0] - g[0], m[3]},
        {m[0]+m[2], m[1], g[0]+g[2]-(m[0]+m[2]), m[3]},
        {g[0], m[1]+m[3], g[2], g[1]+g[3]-(m[1]+m[3])},
    };
    for (unsigned i = 0; i < 4; ++i)
        if (!surface_quad(t, WALLE_VK_TINT_APPLY_MASK, pieces[i], nullptr, 0)) return false;
    return surface_quad(t, WALLE_VK_TINT_COMPOSITE, g, group, 1);
}

static bool prepare_capture(struct walle_transition* t,
                            const struct walle_transition_geometry* g)
{
    double scale = t->backing_scale * g->scale;
    double radius = t->local_radius;
    if (!isfinite(scale) || scale <= 0)
        return false;
    double affine[6] = {scale, 0, 0, -scale,
        t->backing_scale * g->center[0] - scale * radius,
        t->backing_scale * g->center[1] + scale * radius};
    memcpy(t->root_transform, affine, sizeof affine);
    const struct wm_plan_parameters* plan = &t->packet.plan;
    double base_bounds[4] = {0,0,2*radius,2*radius};
    double backdrop[4], world_backdrop[4];
    if (!wm_backdrop_bounds(base_bounds,plan->margin,affine,backdrop,world_backdrop)) return false;
    double outer = (double)((float)plan->smoothness + (float)plan->output_maximum);
    double content[2][4] = {{0, 0, 2 * radius, 2 * radius},
                            {-outer, -outer, 2 * (radius + outer), 2 * (radius + outer)}};
    /* Updater::FilterOp applies the first glass DOD to get_backdrop_bounds
     * (already expanded by margin), not the unexpanded element bounds. */
    memcpy(content[0],backdrop,sizeof backdrop);
    int32_t regions[2][4];
    for (unsigned i = 0; i < 2; ++i) {
        double dod[4];
        if (!wm_glass_dod(&t->glass_extent, content[i], backdrop, dod)
            || !wm_scissor_transform(dod, affine, t->width, t->height, true, regions[i]))
            return false;
    }
    /* Shared SDFState receives the element contribution, not the broader
     * first backdrop contribution. Original moving-scene traces distinguish
     * them once screen clipping and the giant-circle margin are involved. */
    memcpy(t->cache_bounds,regions[1],sizeof t->cache_bounds);
    double region[4] = {0};
    for (unsigned i = 0; i < 2; ++i) {
        const int32_t* r = regions[i];
        if (r[2] <= 0 || r[3] <= 0) continue;
        if (region[2] <= 0 || region[3] <= 0) {
            for (unsigned j = 0; j < 4; ++j) region[j] = r[j];
        } else {
            double x = fmin(region[0], r[0]), y = fmin(region[1], r[1]);
            region[2] = fmax(region[0] + region[2], (double)r[0] + r[2]) - x;
            region[3] = fmax(region[1] + region[3], (double)r[1] + r[3]) - y;
            region[0] = x; region[1] = y;
        }
    }
    struct wm_capture_request request = {
        .source_size = {t->width, t->height},
        .frame = {world_backdrop[0],world_backdrop[1],world_backdrop[2],world_backdrop[3]},
        .margin = 0, .scale = plan->backdrop_scale,
        .renderer_scale = 1, .replicate_edges = plan->maximum_refraction > 0,
        .raster_flipped = false, .region = region,
    };
    if (!wm_glass_filter_dod(t->recipe,affine,t->glass_scissor)) return false;
    for (unsigned i=0;i<2;++i) {
        int64_t lo=t->glass_scissor[i], hi=lo+t->glass_scissor[i+2];
        int64_t limit=i?t->height:t->width;
        if(lo<0)lo=0;
        if(hi>limit)hi=limit;
        if(lo>limit)lo=limit;
        t->glass_scissor[i]=(int32_t)lo;
        t->glass_scissor[i+2]=hi>lo?(int32_t)(hi-lo):0;
    }
    enum wm_capture_status status = WM_CAPTURE_EMPTY;
    if(t->glass_scissor[2]>0 && t->glass_scissor[3]>0) {
        status=wm_capture_clipped(&request,&t->capture);
        if(status==WM_CAPTURE_EMPTY)
            status=wm_capture_filter_fallback(t->recipe,affine,t->glass_scissor,&t->capture);
        if(status==WM_CAPTURE_INVALID)return false;
    } else t->capture=(struct wm_capture_plan){};
    t->capture_ready=status==WM_CAPTURE_READY || status==WM_CAPTURE_FILTER_FALLBACK;
    t->pyramid = (struct wm_pyramid_plan){};
    t->source_origin[0] = t->source_origin[1] = 0;
    t->source_size[0] = t->width; t->source_size[1] = t->height;
    t->source_scale = 1;
    if (t->capture_ready) {
        if (status == WM_CAPTURE_READY
            && !wm_pyramid_build(&t->capture, (float)plan->blur_min, (float)plan->blur_max,
                                  scale, &t->pyramid)) return false;
        bool pyramid = t->pyramid.mip_count != 0;
        t->source_scale = pyramid ? t->pyramid.sample_scale : t->capture.scale;
        for (unsigned i = 0; i < 2; ++i) {
            t->source_size[i] = pyramid ? t->pyramid.texture[i] : t->capture.texture[i];
            t->source_origin[i] = pyramid ? t->pyramid.bounds[i] : t->capture.surface[i];
        }
    }
    struct wm_render_domain domain = {
        .source_width = t->source_size[0], .source_height = t->source_size[1],
        .source_scale = t->source_scale, .transform = {scale, 0, 0, -scale},
        .headroom = 1, .gamma = 2.2, .global_light = false, .light_angle = PI / 2,
        .light_opacity = nan(""), .light_spread = nan(""), .light_height = nan(""),
    };
    return wm_recipe_pack_glass(t->recipe,&domain,t->packet.glass_lph,
                                &t->packet.glass_texture_function);
}

static struct wm_render_domain glass_domain(const struct walle_transition* t)
{
    return (struct wm_render_domain){
        .source_width=t->source_size[0],.source_height=t->source_size[1],
        .source_scale=t->source_scale,
        .transform={t->root_transform[0],0,0,t->root_transform[3]},
        .headroom=1,.gamma=2.2,.light_angle=PI/2,
        .light_opacity=NAN,.light_spread=NAN,.light_height=NAN,
    };
}
static bool prepare_sdf_cache(struct walle_transition* t, const struct walle_transition_geometry* g)
{
    const struct wm_plan_parameters* p=&t->packet.plan;
    t->cache_pending=t->cache_state;
    t->cache_pending_plan=t->cache_plan;
    t->cache_pending_geometry=t->cache_geometry;
    memcpy(t->cache_pending_transform,t->cache_transform,sizeof t->cache_transform);
    t->cache_pending_retained=t->cache_retained;
    t->cached_sdf=false;
    t->cache_pending_valid=true;
    if (t->cache_bounds[2]<=0 || t->cache_bounds[3]<=0) return true;
    const double* m=t->root_transform;
    double matrix[16]={m[0],m[1],0,0,m[2],m[3],0,0,0,0,1,0,m[4],m[5],0,1};
    /* One owner plus the highlight copy and optional tint-fill copy. The
     * gradient and product reveal each own separate single-copy SDF states. */
    uint64_t copies=1u+(uint64_t)p->has_highlight+(uint64_t)p->has_tint;
    if (!wm_sdf_cache_advance(&t->cache_pending,t->frame_time,matrix,t->cache_bounds,1,copies,false))
        return true;
    if(t->suppress_cache) {
        t->cache_pending_retained=false;
        return true;
    }
    float pad=(float)p->smoothness+(float)p->output_maximum;
    if (p->has_highlight) pad=fmaxf(pad,(float)p->highlight_pad);
    if (p->has_tint) pad=fmaxf(pad,(float)p->tint_mask_pad);
    double local[4]={0,0,2*t->local_radius,2*t->local_radius};
    if (!wm_sdf_cache_plan_build(local,m,pad,p->shadow_offset,&t->cache_requested_plan)) return false;
    bool same_linear=true;
    for (unsigned i=0;i<4;++i) same_linear=same_linear && m[i]==t->cache_transform[i];
    /* retain_surface18a85bcfc..d00 skips cache lookup when this frame's
     * SDFState changed flag is set, even when the old surface contains it. */
    bool hit=t->cache_retained && !t->cache_pending.changed && same_linear
        && wm_sdf_cache_plan_contains(&t->cache_plan,&t->cache_requested_plan);
    if (!hit) {
        t->cache_pending_plan=t->cache_requested_plan;
        t->cache_pending_geometry=*g;
        memcpy(t->cache_pending_transform,m,sizeof t->cache_pending_transform);
        /* MetalContext::update18a646c28..2c sets Context+0x3d8 to192MiB.
         * A retention miss still uses a transient texture SDF this frame. */
        int32_t cost=t->cache_pending_plan.native_cost;
        t->cache_pending_retained=cost>=0 && (uint64_t)cost<=UINT64_C(0x0c000000);
    }
    if (!wm_sdf_cache_reuse_origin(local,m,t->cache_pending_plan.relative_origin,t->sdf_origin)) return false;
    t->sdf_surface=(struct walle_vk_surface_plan){};
    memcpy(t->sdf_surface.rect,t->cache_pending_plan.surface,sizeof t->sdf_surface.rect);
    memcpy(t->sdf_surface.texture,t->cache_pending_plan.texture,sizeof t->sdf_surface.texture);
    memcpy(t->sdf_surface.extent,t->cache_pending_plan.texture,sizeof t->sdf_surface.extent);
    t->frame.sdf_surface=&t->sdf_surface;
    t->frame.sdf_redraw=!hit;
    t->frame.sdf_retain=t->cache_pending_retained;
    t->cached_sdf=true;
    struct wm_render_domain domain=glass_domain(t);
    return wm_recipe_pack_glass_texture(t->recipe,&domain,1,t->sdf_surface.texture,
                                        t->packet.glass_lph,&t->packet.glass_texture_function);
}
static bool cached_glass_draw(struct walle_transition* t, enum walle_vk_pass pass)
{
    struct wm_render_domain domain=glass_domain(t);
    const int32_t* bounds=t->pyramid.mip_count ? t->pyramid.bounds : t->capture.surface;
    struct wm_vertex v[4];
    if (!wm_sdf_cache_glass_quad(t->recipe,&domain,bounds,t->sdf_origin,t->sdf_surface.texture,1,1,v))
        return false;
    constexpr uint32_t indices[6]={0,1,2,2,3,0};
    struct walle_vk_draw* d=append(t,pass,v,4,indices,6,0);
    if (!d) return false;
    d->cached_sdf=true;
    memcpy(d->glass+48,t->packet.glass_lph,sizeof t->packet.glass_lph);
    memcpy(d->scissor,t->glass_scissor,sizeof d->scissor);
    return true;
}
static bool cached_effect_draw(struct walle_transition* t, enum walle_vk_pass pass, double pad)
{
    float x=(float)t->sdf_origin[0], y=(float)t->sdf_origin[1];
    float right=(float)((double)t->sdf_origin[0]+t->sdf_surface.texture[0]);
    float bottom=(float)((double)t->sdf_origin[1]+t->sdf_surface.texture[1]);
    float u=(float)t->sdf_surface.texture[0]*(1.f/(float)t->sdf_surface.texture[0]);
    float v=(float)t->sdf_surface.texture[1]*(1.f/(float)t->sdf_surface.texture[1]);
    struct wm_vertex vertices[4]={{{x,y},{0,0},{0,0}},{{right,y},{u,0},{0,0}},
                                  {{right,bottom},{u,v},{0,0}},{{x,bottom},{0,v},{0,0}}};
    constexpr uint32_t indices[6]={0,1,2,2,3,0};
    struct walle_vk_draw* d=append(t,pass,vertices,4,indices,6,0);
    if (!d || !cached_effect_scissor(t,pad,d->scissor)) return false;
    d->cached_sdf=true;
    if(pass==WALLE_VK_HIGHLIGHT) {
        memcpy(d->effect+48,t->packet.highlight_vcm,48);
        memcpy(d->effect+96,t->packet.key_fill,40);
    } else memcpy(d->effect+160,t->packet.tint_mask_fill,16);
    return true;
}

static bool build_geometry(struct walle_transition* t,
                           const struct walle_transition_geometry* g,
                           const struct walle_vk_frame** result)
{
    t->last_geometry=*g;
    /* A zero root scale is a collapsed shape. Positive subpixel geometry is
     * submitted to native-shaped coverage; no invented radius cutoff. */
    if (g->scale == 0) {
        *result = &t->frame;
        return true;
    }
    if (!prepare_capture(t, g))
        return false;
    t->frame.draws                     = t->draws;
    t->frame.capture                   = t->capture_ready ? &t->capture : nullptr;
    t->frame.pyramid                   = t->capture_ready ? &t->pyramid : nullptr;
    t->frame.material_opacity          = (float)g->material_opacity;
    const struct wm_plan_parameters* p = &t->packet.plan;
    if (!prepare_sdf_cache(t,g)) return false;
    if (t->frame.sdf_redraw
        && !sdf_draws(t,&t->cache_pending_geometry,WALLE_VK_SDF_CACHE,
                      t->cache_pending_plan.padding,4096,false)) return false;
    if (!prepare_reveal_surface(t)) return false;
    bool reveal = t->reveal_surface.rect[2] > 0 && t->reveal_surface.rect[3] > 0;
    if (reveal) {
        t->frame.reveal_surface = &t->reveal_surface;
        if (!sdf_draws(t,g,WALLE_VK_REVEAL_MASK,1,4096,false)
            || !masked_image_draw(t,false,1)) return false;
    }
    enum walle_vk_pass glass
        = (t->packet.glass_texture_function == 0x47 || t->packet.glass_texture_function == 0x46)
              ? WALLE_VK_GLASS_CLEAR : WALLE_VK_GLASS_REGULAR;
    double inner = p->smoothness - p->output_minimum;
    if (t->capture_ready && !(t->cached_sdf ? cached_glass_draw(t,glass)
                                          : sdf_draws(t,g,glass,0,inner,true)))
        return false;
    if (p->has_tint && p->tint_group_opacity != 0) {
        if (!prepare_tint_surfaces(t)) return false;
    }
    if (p->has_tint && p->tint_group_opacity != 0
        && t->tint_mask_surface.rect[2] > 0 && t->tint_mask_surface.rect[3] > 0) {
        t->frame.tint_mask_surface = &t->tint_mask_surface;
        t->frame.tint_group_surface = &t->tint_group_surface;
        t->frame.tint_ramp_rgba16f = t->packet.tint_ramp_rgba16f;
        if (!(t->cached_sdf ? cached_effect_draw(t,WALLE_VK_TINT_MASK,p->tint_mask_pad)
                           : sdf_draws(t,g,WALLE_VK_TINT_MASK,p->tint_mask_pad,p->tint_mask_maximum,false))
            || !sdf_draws(t,
                          g,
                          WALLE_VK_TINT_GRADIENT,
                          p->tint_gradient_pad,
                          p->tint_gradient_maximum,
                          false)
            || !tint_composite(t))
            return false;
    }
    /* The extracted idle foreground has an identically zero VCM alpha row,
     * independent of style, appearance, activity and byte tint. Its native
     * finite-coverage face is a pure identity on this opaque SDR destination.
     * Omit that pass; do not substitute a different face tessellation.
     * Source/domain argument: material/IDLE_FACE.md. The native packet is kept. */
    if (p->has_highlight && p->highlight_opacity != 0
        && !(t->cached_sdf ? cached_effect_draw(t,WALLE_VK_HIGHLIGHT,p->highlight_pad)
                          : sdf_draws(t,g,WALLE_VK_HIGHLIGHT,p->highlight_pad,p->highlight_maximum,false)))
        return false;
    if (reveal && g->material_opacity < 1
        && !masked_image_draw(t,true,(float)(1-g->material_opacity))) return false;
    *result = &t->frame;
    return true;
}

bool walle_transition_build(struct walle_transition* t, double progress, double scene_time, bool first_boot,
                            const struct walle_vk_frame** result)
{
    if (!t || !result || !isfinite(progress) || !isfinite(scene_time)) return false;
    *result = nullptr;
    t->cache_pending_valid=false;
    t->suppress_cache=false;
    t->frame_time=scene_time;
    t->frame = (struct walle_vk_frame){};
    if (first_boot || progress >= 1) {
        t->frame.plain_incoming = true; *result = &t->frame; return true;
    }
    if (progress <= 0) { *result = &t->frame; return true; }
    struct walle_transition_geometry g;
    if (!walle_transition_geometry_at(t, progress, &g)) return false;
    return build_geometry(t, &g, result);
}

bool walle_transition_recover_analytic(struct walle_transition* t,const struct walle_vk_frame** result)
{
    if(!t || !result || !t->cache_pending_valid || !t->frame.sdf_surface || t->suppress_cache)
        return false;
    *result=nullptr;
    /* REPLAN means the renderer no longer has usable retained SDF storage.
     * Invalidate that ownership fact even if the analytic submission retries;
     * the eligibility clock itself still commits only on successful rendering. */
    t->cache_retained=false;
    t->suppress_cache=true;
    t->frame=(struct walle_vk_frame){};
    return build_geometry(t,&t->last_geometry,result);
}

void walle_transition_commit(struct walle_transition* t)
{
    if (t && t->cache_pending_valid) {
        t->cache_state=t->cache_pending;
        t->cache_plan=t->cache_pending_plan;
        t->cache_geometry=t->cache_pending_geometry;
        memcpy(t->cache_transform,t->cache_pending_transform,sizeof t->cache_transform);
        t->cache_retained=t->cache_pending_retained;
        t->cache_pending_valid=false;
    }
}
