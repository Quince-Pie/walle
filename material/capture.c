#include "capture.h"
#include <float.h>
#include <limits.h>
#include <math.h>
#include <string.h>

static bool round64(uint32_t x, uint32_t* out)
{
    uint64_t rounded = ((uint64_t)x + 63u) & ~UINT64_C(63);
    if (rounded > UINT32_MAX)
        return false;
    *out = (uint32_t)rounded;
    return true;
}
static bool signed32(double x)
{
    return isfinite(x) && x >= INT32_MIN && x <= INT32_MAX;
}
static bool signed16(int64_t x)
{
    return x >= INT16_MIN && x <= INT16_MAX;
}
static uint32_t halve(uint32_t x)
{
    return x > 3 ? x / 2 : 1;
}
static float mul(float a, float b)
{
    return a * b;
}
static void quad(struct wm_capture_plan* p,
                 float                   x0,
                 float                   y0,
                 float                   x1,
                 float                   y1,
                 float                   u0,
                 float                   v0,
                 float                   u1,
                 float                   v1)
{
    p->quads[p->quad_count++] = (struct wm_capture_quad){{x0, y0, x1, y1}, {u0, v0, u1, v1}};
}
bool wm_capture_full(
    uint32_t w, uint32_t h, double scale, bool replicate, struct wm_capture_plan* out)
{
    if (!out)
        return false;
    memset(out, 0, sizeof *out);
    if (!w || !h || !isfinite(scale))
        return false;
    struct wm_capture_plan  result = {};
    struct wm_capture_plan* p      = &result;
    double                  s      = fmin(fmax(scale, .01), 1.);
    double fw = replicate ? floor(w * s) : ceil(w * s), fh = replicate ? floor(h * s) : ceil(h * s);
    if (!signed32(fw) || !signed32(fh))
        return false;
    int32_t sw = (int32_t)fw;
    int32_t sh = (int32_t)fh;
    if (sw <= 0 || sh <= 0)
        return false;
    p->scale      = s;
    p->surface[2] = sw;
    p->surface[3] = sh;
    if (!round64((uint32_t)sw, &p->texture[0]) || !round64((uint32_t)sh, &p->texture[1]))
        return false;
    p->extent[0] = (uint32_t)sw + 17 < p->texture[0] ? (uint32_t)sw + 17 : p->texture[0];
    p->extent[1] = (uint32_t)sh + 17 < p->texture[1] ? (uint32_t)sh + 17 : p->texture[1];
    float sf = (float)s, inv = 1.f / sf;
    float x1 = (float)ceil(mul(sf, (float)w)), y1 = (float)ceil(mul(sf, (float)h));
    /* AArch64 FMSUB here is addend-product, as in H.fmsub32. */
    float u1 = fmaf(fmaf(-sf, (float)w, x1), inv, (float)w);
    float v1 = fmaf(fmaf(-sf, (float)h, y1), inv, (float)h);
    quad(p, 0, 0, x1, y1, 0, 0, u1, v1);
    if (s != 1.) {
        quad(p, -1, 0, 0, y1, .5f, 0, .5f, v1);
        quad(p, -1, -1, 0, 0, .5f, .5f, .5f, .5f);
        quad(p, 0, -1, x1, 0, 0, .5f, u1, .5f);
        quad(p, x1, -1, x1 + 1, 0, u1 - .5f, .5f, u1 - .5f, .5f);
        quad(p, x1, 0, x1 + 1, y1, u1 - .5f, 0, u1 - .5f, v1);
        quad(p, x1, y1, x1 + 1, y1 + 1, u1 - .5f, v1 - .5f, u1 - .5f, v1 - .5f);
        quad(p, 0, y1, x1, y1 + 1, 0, v1 - .5f, u1, v1 - .5f);
        quad(p, -1, y1, 0, y1 + 1, .5f, v1 - .5f, .5f, v1 - .5f);
    }
    float ix = 1.f / (float)p->texture[0], iy = 1.f / (float)p->texture[1];
    p->projection[0]     = ix + ix;
    p->projection[5]     = iy * -2.f;
    p->projection[12]    = -((float)p->texture[0] * ix);
    p->projection[13]    = (float)p->texture[1] * iy;
    p->projection[15]    = 1;
    p->texture_matrix[0] = (float)(1. / w);
    p->texture_matrix[1] = (float)(1. / h);
    if (s < .5) {
        double t = s >= .25 ? sqrt(fmin(2. - 4. * s, 1.)) : .5 * sqrt(fmin(2. - 8. * s, 1.)) + .5;
        double offsets[8];
        if (s >= .25) {
            double a[8] = {-t, t, t, t, -t, -t, t, -t};
            memcpy(offsets, a, sizeof a);
            p->tap_count = 4;
        } else {
            double a[8] = {t, t, t, 3 * t, 3 * t, t, 3 * t, 3 * t};
            memcpy(offsets, a, sizeof a);
            p->tap_count = 8;
        }
        for (unsigned i = 0; i < 4; i++) {
            p->taps[2 * i]     = (float)(offsets[2 * i] / w);
            p->taps[2 * i + 1] = (float)(offsets[2 * i + 1] / h);
        }
    }
    *out = *p;
    return true;
}

bool wm_pyramid_build(const struct wm_capture_plan* cap,
                      float                         rmin,
                      float                         rmax,
                      double                        transform_scale,
                      struct wm_pyramid_plan*       out)
{
    if (!out)
        return false;
    memset(out, 0, sizeof *out);
    if (!cap || !isfinite(rmin) || !isfinite(rmax) || rmin < 0 || rmax < 0
        || !isfinite(transform_scale) || transform_scale <= 0)
        return false;
    if (rmax == 0)
        return true;
    if (!isfinite(cap->scale) || cap->scale <= 0 || cap->surface[2] <= 0 || cap->surface[3] <= 0)
        return false;
    /* BlurCopyBaseMipUniforms stores the capture clamp as signed16. */
    if (!signed16((int64_t)cap->surface[2] - 1) || !signed16((int64_t)cap->surface[3] - 1))
        return false;
    struct wm_pyramid_plan  result = {};
    struct wm_pyramid_plan* p      = &result;
    double                  ss     = transform_scale * cap->scale;
    if (!isfinite(ss) || ss <= 0 || rmin * ss > FLT_MAX || rmax * ss > FLT_MAX)
        return false;
    float r0 = (float)(rmin * ss), r1 = (float)(rmax * ss);
    if ((double)r0 * 1.6f > FLT_MAX || (double)r1 * 1.6f > FLT_MAX)
        return false;
    float    R0 = mul(r0, 1.6f), R1 = mul(r1, 1.6f);
    uint32_t maximum
        = (uint32_t)(cap->surface[2] > cap->surface[3] ? cap->surface[2] : cap->surface[3]);
    if (!maximum)
        return false;
    int levels_size = (int)floor(log2(maximum)) + 1;
    int levels_blur = R1 > 0 ? (int)fmax(ceil(log2(R1)), 0) + 1 : 1;
    if (levels_blur == 1 && R1 != 0)
        levels_blur = 2;
    int    levels = levels_blur < levels_size ? levels_blur : levels_size;
    double align  = (double)(1u << (levels < 7 ? levels : 7));
    double x = cap->surface[0], y = cap->surface[1], w = cap->surface[2], h = cap->surface[3];
    if (fmin(w, h) > 0) {
        double d = -(double)r1;
        x += 2.8 * d;
        y += 2.8 * d;
        w += -5.6 * d;
        h += -5.6 * d;
    }
    if (levels) {
        x /= align;
        y /= align;
        w /= align;
        h /= align;
        double x0 = floor(x), y0 = floor(y), x1 = ceil(x + w), y1 = ceil(y + h);
        x = x0 * align;
        y = y0 * align;
        w = (x1 - x0) * align;
        h = (y1 - y0) * align;
    }
    double bx = round(x), by = round(y), bw = round(x + w) - bx, bh = round(y + h) - by;
    if (!signed32(bx) || !signed32(by) || !signed32(bw) || !signed32(bh))
        return false;
    int32_t b[4]    = {(int32_t)bx, (int32_t)by, (int32_t)bw, (int32_t)bh};
    int     lm      = R0 > 0 ? (int)fmax(floor(log2(R0)), 0) : 0;
    int     minimum = levels - 1 < lm ? levels - 1 : lm;
    bool    no_base = minimum != 0;
    p->no_base      = no_base;
    p->mip_count    = (uint32_t)(levels - (no_base ? 1 : 0));
    for (unsigned i = 0; i < 4; i++)
        p->bounds[i] = no_base ? b[i] / 2 : b[i];
    if (p->bounds[2] <= 0 || p->bounds[3] <= 0)
        return false;
    if (!round64((uint32_t)p->bounds[2], &p->texture[0])
        || !round64((uint32_t)p->bounds[3], &p->texture[1]))
        return false;
    p->sample_scale = cap->scale * (no_base ? .5 : 1.);
    int64_t base[2] = {(int64_t)b[0] - cap->surface[0], (int64_t)b[1] - cap->surface[1]};
    if (!signed16(base[0]) || !signed16(base[1]))
        return false;
    p->coordinate_base[0]  = (int32_t)base[0];
    p->coordinate_base[1]  = (int32_t)base[1];
    p->coordinate_clamp[2] = cap->surface[2] - 1;
    p->coordinate_clamp[3] = cap->surface[3] - 1;
    for (unsigned i = 0; i < 2; i++) {
        uint64_t dst0 = no_base ? (uint64_t)2 * p->texture[i] : p->texture[i];
        uint64_t dst1 = no_base ? p->texture[i] : halve(p->texture[i]);
        if (dst0 > UINT16_MAX || dst1 > UINT16_MAX)
            return false;
        p->dst0[i]   = (uint32_t)dst0;
        p->dst1[i]   = (uint32_t)dst1;
        p->groups[i] = (p->dst0[i] + 31) >> 5;
    }
    p->dst1_level = no_base ? 0 : 1;
    uint32_t dw = p->dst1[0], dh = p->dst1[1];
    for (uint32_t level = p->dst1_level + 1; level < p->mip_count; level++) {
        dw                       = halve(dw);
        dh                       = halve(dh);
        p->down[p->down_count++] = (struct wm_blur_downsample){level - 1,
                                                               level,
                                                               dw,
                                                               dh,
                                                               (dw + 15) >> 4,
                                                               (dh + 31) >> 5,
                                                               (float)(1. / dw),
                                                               (float)(1. / dh)};
    }
    *out = *p;
    return true;
}
