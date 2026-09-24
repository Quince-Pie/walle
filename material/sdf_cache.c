/* Original SDF cache host decisions; no allocation or GPU resource ownership. */
#include "sdf_cache.h"
#include "capture.h"
#include "geometry.h"
#include "material_internal.h"
#include <limits.h>
#include <math.h>
#include <string.h>

void wm_sdf_cache_init(struct wm_sdf_cache_state* state, bool nested_disabled)
{
    if (!state)
        return;
    memset(state, 0, sizeof *state);
    state->transform[0] = state->transform[5] = state->transform[10] = state->transform[15] = 1;
    state->nested_disabled = nested_disabled;
}
static uint32_t difference(int32_t a, int32_t b)
{
    /* SUBS/CNEG w, unsigned CMP; retain the native INT_MIN magnitude. */
    uint32_t value = (uint32_t)a - (uint32_t)b;
    return value & UINT32_C(0x80000000) ? 0u - value : value;
}
bool wm_sdf_cache_advance(struct wm_sdf_cache_state* state, double time,
                           const double transform[16], const int32_t bounds[4],
                           uint16_t elements, uint64_t copies, bool disable_cache)
{
    if (!state || !transform || !bounds || state->index > 3)
        return false;
    /* SDFState::update_cache_eligible @18a877da0..f40. */
    if (state->nested_disabled) {
        state->eligible = false;
        return false;
    }
    bool resized = difference(state->bounds[2], bounds[2]) >= 2
        || difference(state->bounds[3], bounds[3]) >= 2;
    if (resized)
        memcpy(state->bounds, bounds, sizeof state->bounds);
    bool translated = state->transform[15] == transform[15];
    for (unsigned i = 0; i < 12; i++)
        translated = translated && state->transform[i] == transform[i];
    bool changed = resized || !translated;
    memcpy(state->transform, transform, sizeof state->transform);
    state->offset[0] = state->offset[1] = 0;
    state->durations[state->index] = time - state->last_change;
    state->index = (state->index + 1) & 3;
    if (changed)
        state->last_change = time;
    bool stable = true;
    for (unsigned i = 0; i < 4; i++)
        /* ARM HI accepts unordered; final LS rejects ordered <= only. */
        stable = stable && !(state->durations[i] <= .334);
    state->eligible = stable && elements >= 1 && copies >= 2 && !disable_cache;
    state->changed = changed;
    return state->eligible;
}
static bool fit32(double value)
{
    return isfinite(value) && value >= INT32_MIN && value <= INT32_MAX;
}
static bool anchor(const double bounds[4], const double m[6], int32_t out[2])
{
    double r[4];
    if (!wm_uniform_rect_transform(bounds, m, r))
        return false;
    for (unsigned i = 0; i < 2; i++) {
        double value = round(r[i]); /* FRINTA, then FCVTZS @18a85bb80. */
        if (!fit32(value))
            return false;
        out[i] = (int32_t)value;
    }
    return true;
}
bool wm_sdf_cache_plan_build(const double bounds[4], const double m[6], float pad,
                              const double offset[2], struct wm_sdf_cache_plan* out)
{
    if (!out)
        return false;
    memset(out, 0, sizeof *out);
    if (!offset || !isfinite(offset[0]) || !isfinite(offset[1]) || !isfinite(pad) || pad < 0)
        return false;
    struct wm_sdf_cache_plan p = {.padding = pad};
    double r[4];
    if (!wm_uniform_rect_transform(bounds, m, r) || r[2] <= 0 || r[3] <= 0
        || !anchor(bounds, m, p.anchor))
        return false;
    /* Element DOD @18a68f604..6a0: transform first, then separate target
     * padding multiplies/adds; AA exterior, then Float raster-round. */
    double scale = fabs(m[0]), d = -scale * (double)pad;
    r[0] += d; r[1] += d; r[2] = d * -2. + r[2]; r[3] = d * -2. + r[3];
    double right = ceil(r[0] + r[2]), bottom = ceil(r[1] + r[3]);
    r[0] = floor(r[0]); r[1] = floor(r[1]);
    r[2] = right - r[0]; r[3] = bottom - r[1];
    float bias = 255.f / 512.f;
    double edges[4] = {floorf((float)r[0] + bias), floorf((float)r[1] + bias),
        floorf((float)(r[0] + r[2]) + bias), floorf((float)(r[1] + r[3]) + bias)};
    r[0] = edges[0]; r[1] = edges[1]; r[2] = edges[2] - edges[0]; r[3] = edges[3] - edges[1];
    for (unsigned i = 0; i < 4; i++) {
        if (!fit32(r[i]))
            return false;
        p.child_dod[i] = (int32_t)r[i];
    }
    if (p.child_dod[2] <= 0 || p.child_dod[3] <= 0)
        return false;
    /* retain_surface @18a85bbbc..bcf8: grow by1, union ceil(offset*scale),
     * then round allocation bounds themselves to64. */
    for (unsigned i = 0; i < 2; i++) {
        double shift = ceil(offset[i] * scale);
        double position = r[i] - 1 + fmin(shift, 0);
        double size = r[i + 2] + 2 + fabs(shift);
        if (!fit32(position) || !fit32(size) || size <= 0)
            return false;
        p.requested[i] = p.surface[i] = (int32_t)position;
        p.requested[i + 2] = (int32_t)size;
        uint64_t allocation = ((uint64_t)(uint32_t)p.requested[i + 2] + 63) & ~UINT64_C(63);
        if (allocation > INT32_MAX)
            return false;
        p.texture[i] = (uint32_t)allocation;
        p.surface[i + 2] = (int32_t)allocation;
        double relative = position - p.anchor[i];
        double cached = trunc(position - m[4 + i]);
        if (!fit32(relative) || !fit32(cached))
            return false;
        p.relative_origin[i] = (int32_t)relative;
        p.cache_bounds[i] = (int32_t)cached;
        p.cache_bounds[i + 2] = p.requested[i + 2];
    }
    uint32_t cost = p.texture[0] * p.texture[1] * 8u;
    memcpy(&p.native_cost, &cost, sizeof cost); /* Original signed32 cost. */
    *out = p;
    return true;
}
bool wm_sdf_cache_reuse_origin(const double bounds[4], const double m[6],
                                const int32_t relative[2], int32_t out[2])
{
    if (!out)
        return false;
    out[0] = out[1] = 0;
    int32_t origin[2];
    if (!relative || !anchor(bounds, m, origin))
        return false;
    int64_t x = (int64_t)origin[0] + relative[0], y = (int64_t)origin[1] + relative[1];
    if (x < INT32_MIN || x > INT32_MAX || y < INT32_MIN || y > INT32_MAX)
        return false;
    out[0] = (int32_t)x; out[1] = (int32_t)y;
    return true;
}
bool wm_sdf_cache_copy_dod(const int32_t child[4], double scale, float cache_pad,
                            float own_pad, int32_t out[4])
{
    if (!child || !out || !isfinite(scale) || scale < 0 || !isfinite(cache_pad) || !isfinite(own_pad))
        return false;
    memcpy(out, child, 4 * sizeof *out);
    if (child[2] <= 0 || child[3] <= 0 || child[2] > 0x3ffffffe || child[3] > 0x3ffffffe)
        return true;
    float d = cache_pad - own_pad;
    double inset = trunc(scale * (double)d);
    if (!fit32(inset))
        return false;
    int64_t x = (int64_t)child[0] + (int32_t)inset, y = (int64_t)child[1] + (int32_t)inset;
    int64_t w = (int64_t)child[2] - 2 * (int64_t)(int32_t)inset;
    int64_t h = (int64_t)child[3] - 2 * (int64_t)(int32_t)inset;
    if (x < INT32_MIN || x > INT32_MAX || y < INT32_MIN || y > INT32_MAX
        || w > INT32_MAX || h > INT32_MAX)
        return false;
    out[0] = (int32_t)x; out[1] = (int32_t)y;
    out[2] = w < 1 || h <= 0 ? 0 : (int32_t)w;
    out[3] = w < 1 || h <= 0 ? 0 : (int32_t)h;
    return true;
}
bool wm_sdf_cache_plan_contains(const struct wm_sdf_cache_plan* stored,
                                 const struct wm_sdf_cache_plan* requested)
{
    if (!stored || !requested)
        return false;
    return wm_sdf_cache_contains(stored->cache_bounds, requested->cache_bounds);
}
static int32_t wrap_add(int32_t a, int32_t b)
{
    uint32_t value = (uint32_t)a + (uint32_t)b;
    int32_t result;
    memcpy(&result, &value, sizeof result);
    return result;
}
bool wm_sdf_cache_contains(const int32_t cached[4], const int32_t requested[4])
{
    if (!cached || !requested)
        return false;
    int32_t outer[4];
    memcpy(outer, cached, sizeof outer);
    if (cached[2] >= 1 && cached[3] >= 1 && cached[2] <= 0x3ffffffe && cached[3] <= 0x3ffffffe) {
        outer[0] = wrap_add(outer[0], -1); outer[1] = wrap_add(outer[1], -1);
        outer[2] += 2; outer[3] += 2;
    }
    for (unsigned i = 0; i < 2; i++)
        if (outer[i + 2] <= 0 || requested[i + 2] < 0 || requested[i] < outer[i]
            || wrap_add(outer[i], outer[i + 2]) < wrap_add(requested[i], requested[i + 2]))
            return false;
    return true;
}
bool wm_recipe_pack_glass_texture(const struct wm_recipe* recipe, const struct wm_render_domain* domain,
                                    float sdf_scale, const uint32_t size[2], uint8_t bytes[216], uint32_t* function)
{
    if (!size || !size[0] || !size[1] || !isfinite(sdf_scale) || sdf_scale <= 0) {
        if (bytes)
            memset(bytes, 0, 216);
        if (function)
            *function = 0;
        return false;
    }
    if (!wm_recipe_pack_glass(recipe, domain, bytes, function))
        return false;
    float a = (float)domain->transform[0], b = (float)domain->transform[1];
    float c = (float)domain->transform[2], d = (float)domain->transform[3];
    double x = recipe->shadow_offset[0], y = recipe->shadow_offset[1];
    float off[2] = {(float)fma((double)a, x, y * (double)c),
                     (float)fma((double)d, y, x * (double)b)};
    for (unsigned i = 0; i < 2; i++)
        off[i] = (off[i] * -sdf_scale) / (float)size[i];
    memcpy(bytes + 64, off, sizeof off);
    *function -= 1;
    return true;
}
bool wm_sdf_cache_glass_quad(const struct wm_recipe* recipe, const struct wm_render_domain* domain,
                              const int32_t source[4], const int32_t origin[2], const uint32_t size[2],
                              float sdf_scale, float render_scale, struct wm_vertex out[4])
{
    if (!out)
        return false;
    memset(out, 0, 4 * sizeof *out);
    if (!recipe || !domain || !source || !origin || !size || !size[0] || !size[1]
        || !domain->source_width || !domain->source_height || domain->source_scale <= 0
        || sdf_scale <= 0 || render_scale <= 0 || source[2] <= 0 || source[3] <= 0)
        return false;
    float k = render_scale / (float)domain->source_scale;
    float s = sdf_scale / (float)domain->source_scale;
    float x0, y0, x1, y1, u0, v0, u1, v1, a, b, c, d;
    if ((float)recipe->shadow_vibrancy != 0) {
        x0 = k * (float)source[0]; y0 = k * (float)source[1];
        x1 = k * (float)((double)source[0] + source[2]);
        y1 = k * (float)((double)source[1] + source[3]);
        u0 = v0 = 0; u1 = (float)source[2]; v1 = (float)source[3];
        a = fmaf(s, (float)source[0], -(float)origin[0]);
        b = fmaf(s, (float)source[1], -(float)origin[1]);
        c = fmaf(s, (float)((double)source[0] + source[2]), -(float)origin[0]);
        d = fmaf(s, (float)((double)source[1] + source[3]), -(float)origin[1]);
    } else {
        double x = (float)source[0] * k, y = (float)source[1] * k;
        double w = (float)source[2] * k, h = (float)source[3] * k;
        double outer = fabs((float)recipe->outer_amount), radius = recipe->shadow_radius;
        double growth = fma(radius, 5.6, -outer) < 0 ? -0. : fma(-radius, 5.6, outer);
        double gx = x + growth, gy = y + growth, gw = w - (growth + growth), gh = fma(growth, -2., h);
        const double* m = domain->transform;
        const double* off = recipe->shadow_offset;
        gx += (float)fma(m[1], off[1], off[0] * m[0]);
        gy += (float)fma(m[2], off[0], off[1] * m[3]);
        double xx = fmin(x, gx), yy = fmin(y, gy);
        w = fmax(x + w, gx + gw) - xx; h = fmax(y + h, gy + gh) - yy;
        x = xx; y = yy;
        double inverse = 1. / (double)k;
        x0 = (float)x; y0 = (float)y; x1 = (float)(x + w); y1 = (float)(y + h);
        double ux = fma(x, inverse, -(double)(float)source[0]);
        double vy = fma(y, inverse, -(double)(float)source[1]);
        u0 = (float)ux; v0 = (float)vy; u1 = (float)fma(w, inverse, ux); v1 = (float)fma(h, inverse, vy);
        double dx = inverse * x, dy = inverse * y;
        a = (float)fma(dx, (double)s, -(double)(float)origin[0]);
        b = (float)fma(dy, (double)s, -(double)(float)origin[1]);
        c = (float)fma(fma(w, inverse, dx), (double)s, -(double)(float)origin[0]);
        d = (float)fma(fma(h, inverse, dy), (double)s, -(double)(float)origin[1]);
    }
    float sx = 1.f / (float)domain->source_width, sy = 1.f / (float)domain->source_height;
    float cx = 1.f / (float)size[0], cy = 1.f / (float)size[1];
    struct wm_vertex v[4] = {{{x0,y0},{a*cx,b*cy},{u0*sx,v0*sy}},
        {{x1,y0},{c*cx,b*cy},{u1*sx,v0*sy}}, {{x1,y1},{c*cx,d*cy},{u1*sx,v1*sy}},
        {{x0,y1},{a*cx,d*cy},{u0*sx,v1*sy}}};
    memcpy(out, v, sizeof v);
    return true;
}
