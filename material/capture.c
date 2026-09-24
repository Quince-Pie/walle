#include "capture.h"
#include "material_math.h"
#include "material_internal.h"
#include "scissor.h"
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
static bool finite_rect(const double r[4])
{
    return r && isfinite(r[0]) && isfinite(r[1]) && isfinite(r[2]) && isfinite(r[3])
        && r[2] >= 0 && r[3] >= 0 && isfinite(r[0] + r[2]) && isfinite(r[1] + r[3]);
}
static void intersect_source(double r[4], uint32_t w, uint32_t h)
{
    double x = fmax(r[0], 0), y = fmax(r[1], 0);
    double right = fmin(r[0] + r[2], w), bottom = fmin(r[1] + r[3], h);
    r[0] = x;
    r[1] = y;
    r[2] = right > x && bottom > y ? right - x : 0;
    r[3] = right > x && bottom > y ? bottom - y : 0;
}
static void exterior(double r[4])
{
    double x = floor(r[0]), y = floor(r[1]);
    r[2] = ceil(r[0] + r[2]) - x;
    r[3] = ceil(r[1] + r[3]) - y;
    r[0] = x;
    r[1] = y;
}
static bool projection(struct wm_capture_plan* p)
{
    /* MetalContext::update_projection_matrix @18a821f14. */
    float left = (float)p->surface[0], top = (float)p->surface[1];
    float right = (float)((double)p->surface[0] + p->texture[0]);
    float bottom = (float)((double)p->surface[1] + p->texture[1]);
    float ix = 1.f / (right - left), iy = 1.f / (bottom - top);
    if (!isfinite(ix) || !isfinite(iy))
        return false;
    p->projection[0] = ix + ix;
    p->projection[5] = iy * -2.f;
    p->projection[12] = -((right + left) * ix);
    p->projection[13] = (bottom + top) * iy;
    p->projection[15] = 1;
    return true;
}
static bool simple_transform(const double m[6])
{
    if (!m)
        return false;
    for (unsigned i = 0; i < 6; i++)
        if (!isfinite(m[i]))
            return false;
    return m[1] == 0 && m[2] == 0 && m[0] != 0 && fabs(m[0]) == fabs(m[3]);
}
static void transform_rect(double r[4], const double m[6], bool inverse)
{
    double scale = fabs(m[0]);
    if (inverse) {
        /* CA::Rect::unapply_transform simple flags @18a693814..8e0. */
        r[0] -= m[4];
        r[1] -= m[5];
        if (m[3] < 0)
            r[1] = -(r[1] + r[3]);
        if (m[0] < 0)
            r[0] = -(r[0] + r[2]);
        if (scale != 1) {
            double reciprocal = 1. / scale;
            for (unsigned i = 0; i < 4; i++)
                r[i] *= reciprocal;
        }
    } else {
        /* CA::Rect::apply_transform simple flags @18a6619fc..ab4. */
        if (scale != 1)
            for (unsigned i = 0; i < 4; i++)
                r[i] *= scale;
        if (m[0] < 0)
            r[0] = -(r[0] + r[2]);
        if (m[3] < 0)
            r[1] = -(r[1] + r[3]);
        r[0] += m[4];
        r[1] += m[5];
    }
}
bool wm_uniform_rect_transform(const double rectangle[4], const double m[6], double world[4])
{
    if (!world)
        return false;
    double result[4];
    bool valid = finite_rect(rectangle) && simple_transform(m);
    if (valid) {
        memcpy(result, rectangle, sizeof result);
        transform_rect(result, m, false);
        valid = finite_rect(result);
    }
    memset(world, 0, sizeof result);
    if (valid)
        memcpy(world, result, sizeof result);
    return valid;
}
bool wm_backdrop_bounds(const double frame[4], double margin, const double m[6],
                         double local[4], double world[4])
{
    double l[4], w[4];
    bool valid = local && world && finite_rect(frame) && simple_transform(m)
        && isfinite(margin) && margin >= 0;
    if (valid) {
        memcpy(l, frame, sizeof l);
        if (l[2] > 0 && l[3] > 0) {
            /* get_backdrop_bounds: FNEG s; FCVT d; separate width FADD,
             * but FMADD for height, @18a7fb8a8..8d4. */
            double d = (double)-(float)margin;
            l[0] += d;
            l[1] += d;
            l[2] -= d + d;
            l[3] = fma(d, -2., l[3]);
        }
        memcpy(w, l, sizeof w);
        transform_rect(w, m, false);
        valid = finite_rect(l) && finite_rect(w);
    }
    if (local)
        memset(local, 0, sizeof l);
    if (world)
        memset(world, 0, sizeof w);
    if (!valid)
        return false;
    memcpy(local, l, sizeof l);
    memcpy(world, w, sizeof w);
    return true;
}
static void union_rect(double a[4], const double b[4])
{
    if (b[2] <= 0 || b[3] <= 0)
        return;
    if (a[2] <= 0 || a[3] <= 0) {
        memcpy(a, b, 4 * sizeof *a);
        return;
    }
    double x = fmin(a[0], b[0]), y = fmin(a[1], b[1]);
    double right = fmax(a[0] + a[2], b[0] + b[2]);
    double bottom = fmax(a[1] + a[3], b[1] + b[3]);
    a[0] = x;
    a[1] = y;
    a[2] = right - x;
    a[3] = bottom - y;
}
static bool glass_roi(const struct wm_recipe* recipe, double r[4])
{
    /* GlassBackgroundFilter::ROI @18a78c218. All inputs/rectangles are in
     * material-local coordinates; transform only outside this function. */
    if (!finite_rect(r) || r[2] <= 0 || r[3] <= 0)
        return false;
    double x = r[0], y = r[1], w = r[2], h = r[3];
    double d = -(recipe->shadow_radius * wm_gaussian_expansion(recipe->shadow_opacity, false));
    double shadow[4] = {x + d, y + d, w - (d + d), fma(d, -2., h)};
    shadow[0] -= recipe->shadow_offset[0];
    shadow[1] -= recipe->shadow_offset[1];
    double range = recipe->plan.maximum_refraction;
    double refracted[4] = {x - range, y - range, fma(range, 2., w), fma(range, 2., h)};
    double bleed = recipe->bleed_opacity > 0 ? recipe->bleed_blur * .5 : 0;
    double k = bleed < recipe->blur_radius ? recipe->blur_radius : bleed;
    d = k * -2.8;
    r[0] = x + d;
    r[1] = y + d;
    r[2] = fma(k, 5.6, w);
    r[3] = fma(k, 5.6, h);
    union_rect(r, shadow);
    union_rect(r, refracted);
    if (recipe->shadow_vibrancy == 0) {
        r[0] -= 1;
        r[1] -= 1;
        r[2] += 2;
        r[3] += 2;
        if (!finite_rect(r))
            return false;
        exterior(r);
    }
    return finite_rect(r);
}
bool wm_glass_filter_dod(const struct wm_recipe* recipe, const double m[6], int32_t bounds[4])
{
    if (!bounds)
        return false;
    memset(bounds, 0, 4 * sizeof *bounds);
    if (!recipe || !simple_transform(m))
        return false;
    const double frame[4] = {0, 0, recipe->input.width_points, recipe->input.height_points};
    double local[4], world[4], r[4];
    if (!wm_backdrop_bounds(frame, recipe->plan.margin, m, local, world))
        return false;
    memcpy(r, world, sizeof r);
    exterior(r);
    /* Source LayerNode integer DOD, then FilterNode @18a693634..18a69367c. */
    transform_rect(r, m, true);
    struct wm_glass_extent parameters;
    if (!wm_recipe_glass_extent(recipe, &parameters)
        || !wm_glass_dod(&parameters, r, local, r))
        return false;
    transform_rect(r, m, false);
    if (!finite_rect(r))
        return false;
    const float bias = 255.f / 512.f;
    double x = floorf((float)r[0] + bias), y = floorf((float)r[1] + bias);
    double right = floorf((float)(r[0] + r[2]) + bias);
    double bottom = floorf((float)(r[1] + r[3]) + bias);
    double result[4] = {x, y, right - x, bottom - y};
    for (unsigned i = 0; i < 4; i++)
        if (!signed32(result[i]))
            return false;
    for (unsigned i = 0; i < 4; i++)
        bounds[i] = (int32_t)result[i];
    return true;
}
enum wm_capture_status wm_capture_filter_fallback(const struct wm_recipe* recipe,
                                                  const double m[6],
                                                  const int32_t output_roi[4],
                                                  struct wm_capture_plan* out)
{
    if (!out)
        return WM_CAPTURE_INVALID;
    memset(out, 0, sizeof *out);
    if (!recipe || !output_roi || !simple_transform(m))
        return WM_CAPTURE_INVALID;
    if (output_roi[2] <= 0 || output_roi[3] <= 0)
        return WM_CAPTURE_EMPTY;
    int32_t filter_dod[4];
    if (!wm_glass_filter_dod(recipe, m, filter_dod))
        return WM_CAPTURE_INVALID;
    double rx = fmax(output_roi[0], filter_dod[0]);
    double ry = fmax(output_roi[1], filter_dod[1]);
    double rr = fmin((double)output_roi[0] + output_roi[2], (double)filter_dod[0] + filter_dod[2]);
    double rb = fmin((double)output_roi[1] + output_roi[3], (double)filter_dod[1] + filter_dod[3]);
    if (rr <= rx || rb <= ry)
        return WM_CAPTURE_EMPTY;
    struct wm_capture_plan p = {};
    const double frame[4] = {0, 0, recipe->input.width_points, recipe->input.height_points};
    double local[4], dod[4], roi[4];
    if (!wm_backdrop_bounds(frame, recipe->plan.margin, m, local, p.backdrop))
        return WM_CAPTURE_INVALID;
    memcpy(dod, p.backdrop, sizeof dod);
    exterior(dod);
    roi[0] = rx;
    roi[1] = ry;
    roi[2] = rr - rx;
    roi[3] = rb - ry;
    /* FilterNode::propagate_roi @18a693a6c..18a693b0c. */
    transform_rect(roi, m, true);
    if (!glass_roi(recipe, roi))
        return WM_CAPTURE_INVALID;
    transform_rect(roi, m, false);
    if (!finite_rect(roi))
        return WM_CAPTURE_INVALID;
    exterior(roi);
    memcpy(p.clipped, roi, sizeof roi);
    double x = fmax(dod[0], roi[0]), y = fmax(dod[1], roi[1]);
    double right = fmin(dod[0] + dod[2], roi[0] + roi[2]);
    double bottom = fmin(dod[1] + dod[3], roi[1] + roi[3]);
    if (right <= x || bottom <= y)
        return WM_CAPTURE_EMPTY;
    double surface[4] = {x, y, right - x, bottom - y};
    for (unsigned i = 0; i < 4; i++) {
        if (!signed32(surface[i]))
            return WM_CAPTURE_INVALID;
        p.surface[i] = (int32_t)surface[i];
    }
    for (unsigned i = 0; i < 2; i++) {
        if (!round64((uint32_t)p.surface[i + 2], &p.texture[i]))
            return WM_CAPTURE_INVALID;
        /* set_dest @18a854954..970, extend_surface @18a821504..1538. */
        double end = surface[i] + surface[i + 2];
        double extension = fmax(roi[i] + roi[i + 2] - end, 0);
        double extent = fmin(surface[i + 2] + extension + 1, p.texture[i]);
        if (!isfinite(extent) || extent < 1 || extent > UINT32_MAX)
            return WM_CAPTURE_INVALID;
        p.extent[i] = (uint32_t)extent;
    }
    p.scale = 1;
    if (!projection(&p))
        return WM_CAPTURE_INVALID;
    *out = p;
    return WM_CAPTURE_FILTER_FALLBACK;
}
static bool emit_capture_quads(struct wm_capture_plan* p, const double region[4],
                               uint32_t w, uint32_t h)
{
    double r[4];
    memcpy(r, region, sizeof r);
    intersect_source(r, w, h);
    if (r[2] <= 0 || r[3] <= 0)
        return true;
    float s = (float)p->scale, inv = 1.f / s;
    float fx = (float)r[0], fy = (float)r[1];
    float fr = (float)(r[0] + r[2]), fb = (float)(r[1] + r[3]);
    float x0 = floorf(mul(s, fx)), y0 = floorf(mul(s, fy));
    float x1 = ceilf(mul(s, fr)), y1 = ceilf(mul(s, fb));
    /* AArch64 FMSUB here is addend-product, as in H.fmsub32. */
    float u0 = fmaf(fmaf(-s, fx, x0), inv, fx);
    float v0 = fmaf(fmaf(-s, fy, y0), inv, fy);
    float u1 = fmaf(fmaf(-s, fr, x1), inv, fr);
    float v1 = fmaf(fmaf(-s, fb, y1), inv, fb);
    quad(p, x0, y0, x1, y1, u0, v0, u1, v1);
    if (s == 1.f)
        return true;
    bool L = p->clipped[0] == r[0], T = p->clipped[1] == r[1];
    bool R = p->clipped[0] + p->clipped[2] == r[0] + r[2];
    bool B = p->clipped[1] + p->clipped[3] == r[1] + r[3];
    if (L) {
        quad(p, x0 - 1, y0, x0, y1, u0 + .5f, v0, u0 + .5f, v1);
        if (T)
            quad(p, x0 - 1, y0 - 1, x0, y0, u0 + .5f, v0 + .5f, u0 + .5f, v0 + .5f);
    }
    if (T) {
        quad(p, x0, y0 - 1, x1, y0, u0, v0 + .5f, u1, v0 + .5f);
        if (R)
            quad(p, x1, y0 - 1, x1 + 1, y0, u1 - .5f, v0 + .5f, u1 - .5f, v0 + .5f);
    }
    if (R) {
        quad(p, x1, y0, x1 + 1, y1, u1 - .5f, v0, u1 - .5f, v1);
        if (B)
            quad(p, x1, y1, x1 + 1, y1 + 1, u1 - .5f, v1 - .5f, u1 - .5f, v1 - .5f);
    }
    if (B) {
        quad(p, x0, y1, x1, y1 + 1, u0, v1 - .5f, u1, v1 - .5f);
        if (L)
            quad(p, x0 - 1, y1, x0, y1 + 1, u0 + .5f, v1 - .5f, u0 + .5f, v1 - .5f);
    }
    return true;
}
enum wm_capture_status wm_capture_clipped(const struct wm_capture_request* req,
                                          struct wm_capture_plan* out)
{
    if (!out)
        return WM_CAPTURE_INVALID;
    memset(out, 0, sizeof *out);
    if (!req || !req->source_size[0] || !req->source_size[1]
        || req->source_size[0] > INT32_MAX || req->source_size[1] > INT32_MAX
        || !finite_rect(req->frame) || !isfinite(req->margin) || req->margin < 0
        || !isfinite(req->scale) || !isfinite(req->renderer_scale) || req->renderer_scale <= 0
        || (req->region && !finite_rect(req->region)))
        return WM_CAPTURE_INVALID;
    struct wm_capture_plan result = {};
    struct wm_capture_plan* p = &result;
    float sf = fminf(fmaxf((float)req->scale, .01f), 1.f);
    p->scale = sf;
    p->backdrop[0] = req->frame[0] - req->margin;
    p->backdrop[1] = req->frame[1] - req->margin;
    p->backdrop[2] = req->frame[2] + 2 * req->margin;
    p->backdrop[3] = req->frame[3] + 2 * req->margin;
    if (!finite_rect(p->backdrop))
        return WM_CAPTURE_INVALID;
    memcpy(p->clipped, p->backdrop, sizeof p->clipped);
    if (!req->replicate_edges && req->renderer_scale != 1.) {
        double inv = 1. / req->renderer_scale;
        for (unsigned i = 0; i < 4; i++)
            p->clipped[i] *= inv;
        if (!finite_rect(p->clipped))
            return WM_CAPTURE_INVALID;
        if (p->clipped[2] > 0 && p->clipped[3] > 0)
            exterior(p->clipped);
        for (unsigned i = 0; i < 4; i++)
            p->clipped[i] *= req->renderer_scale;
        if (!finite_rect(p->clipped))
            return WM_CAPTURE_INVALID;
    }
    intersect_source(p->clipped, req->source_size[0], req->source_size[1]);
    if (p->clipped[2] <= 0 || p->clipped[3] <= 0)
        return WM_CAPTURE_EMPTY;
    if (!req->replicate_edges) {
        float fx = 255.f / 512.f, fy = req->raster_flipped ? 1.f - fx : fx;
        double x = floorf((float)p->clipped[0] + fx);
        double y = floorf((float)p->clipped[1] + fy);
        double right = floorf((float)(p->clipped[0] + p->clipped[2]) + fx);
        double bottom = floorf((float)(p->clipped[1] + p->clipped[3]) + fy);
        p->clipped[0] = x;
        p->clipped[1] = y;
        p->clipped[2] = right - x;
        p->clipped[3] = bottom - y;
    }
    double x = p->clipped[0] * p->scale, y = p->clipped[1] * p->scale;
    double w = p->clipped[2] * p->scale, h = p->clipped[3] * p->scale;
    double x0 = req->replicate_edges ? ceil(x) : floor(x);
    double y0 = req->replicate_edges ? ceil(y) : floor(y);
    double x1 = req->replicate_edges ? floor(x + w) : ceil(x + w);
    double y1 = req->replicate_edges ? floor(y + h) : ceil(y + h);
    if (x1 <= x0 || y1 <= y0)
        return WM_CAPTURE_EMPTY;
    if (!signed32(x0) || !signed32(y0) || !signed32(x1 - x0) || !signed32(y1 - y0))
        return WM_CAPTURE_INVALID;
    p->surface[0] = (int32_t)x0;
    p->surface[1] = (int32_t)y0;
    p->surface[2] = (int32_t)(x1 - x0);
    p->surface[3] = (int32_t)(y1 - y0);
    for (unsigned i = 0; i < 2; i++) {
        uint32_t size = (uint32_t)p->surface[i + 2];
        if (!round64(size, &p->texture[i]))
            return WM_CAPTURE_INVALID;
        p->extent[i] = size + 17 < p->texture[i] ? size + 17 : p->texture[i];
    }
    double band[4];
    memcpy(band, req->region ? req->region : p->clipped, sizeof band);
    if (!req->region)
        exterior(band);
    if (!emit_capture_quads(p, band, req->source_size[0], req->source_size[1]))
        return WM_CAPTURE_INVALID;
    if (!projection(p))
        return WM_CAPTURE_INVALID;
    p->texture_matrix[0] = 1.f / (float)req->source_size[0];
    p->texture_matrix[1] = 1.f / (float)req->source_size[1];
    if (sf < .5f) {
        float t = sqrtf(fminf(fmaf(sf, sf >= .25f ? -4.f : -8.f, 2.f), 1.f));
        if (sf < .25f)
            t = fmaf(t, .5f, .5f);
        float a = mul(t, p->texture_matrix[0]), b = mul(t, p->texture_matrix[1]);
        const float four[8] = {-a, b, a, b, -a, -b, a, -b};
        const float eight[8] = {a, b, a, mul(b, 3.f), mul(a, 3.f), b, mul(a, 3.f), mul(b, 3.f)};
        memcpy(p->taps, sf >= .25f ? four : eight, sizeof four);
        p->tap_count = sf >= .25f ? 4 : 8;
    }
    *out = result;
    return WM_CAPTURE_READY;
}
bool wm_capture_full(uint32_t w, uint32_t h, double scale, bool replicate,
                     struct wm_capture_plan* out)
{
    struct wm_capture_request request = {.source_size = {w, h}, .frame = {0, 0, w, h},
        .scale = scale, .renderer_scale = 1, .replicate_edges = replicate};
    return wm_capture_clipped(&request, out) == WM_CAPTURE_READY;
}

/* compute_variable_blur_parameters @18a72126c. This finite-bounds form is
 * called only after the signed16 source clamp has been checked. Native infinite
 * CA::Bounds and saturated/narrowing output are not representable live plans. */
struct variable_blur_parameters
{
    float r0, r1;
    uint32_t minimum, levels;
    float alignment;
    int32_t bounds[4];
    double rect[4];
};
static bool variable_blur_parameters(uint32_t width, uint32_t height,
                                     const int32_t bounds[4], float r0, float r1,
                                     struct variable_blur_parameters* out)
{
    float R0 = mul(r0, 1.6f), R1 = mul(r1, 1.6f);
    if (!isfinite(R0) || !isfinite(R1))
        return false;
    uint32_t maximum = width > height ? width : height;
    if (!maximum)
        return false;
    /* UCVTF and all log/FRINT/FADD operands are Float, not Double. */
    uint32_t levels_size = (uint32_t)(floorf(wm_log2f((float)maximum)) + 1.f);
    uint32_t levels_blur = (uint32_t)(fmaxf(ceilf(wm_log2f(R1)), 0.f) + 1.f);
    if (levels_blur == 1 && R1 != 0)
        levels_blur = 2;
    uint32_t levels = levels_blur < levels_size ? levels_blur : levels_size;
    float alignment = (float)(1u << (levels < 7 ? levels : 7));
    double x = bounds[0], y = bounds[1], w = bounds[2], h = bounds[3];
    if (fmin(w, h) > 0) {
        double d = (double)-r1;
        double inset = 2.8 * d;
        x += inset;
        y += inset;
        w = fma(d, -5.6, w);
        h = fma(d, -5.6, h);
    }
    double inverse = (double)(1.f / alignment);
    if (inverse != 1.) {
        x *= inverse;
        y *= inverse;
        w *= inverse;
        h *= inverse;
    }
    if (fmin(w, h) > 0) {
        double x1 = ceil(x + w), y1 = ceil(y + h);
        x = floor(x);
        y = floor(y);
        w = x1 - x;
        h = y1 - y;
    }
    if (levels) {
        x *= alignment;
        y *= alignment;
        w *= alignment;
        h *= alignment;
    }
    /* The native to-bounds branch uses its infinite sentinel at 2^30-1.
     * Such a result cannot fit this renderer's native ushort destinations. */
    if (!isfinite(w) || !isfinite(h) || fmax(w, h) >= 1073741823.)
        return false;
    /* FCVTZS after adding copysign(.5), not an abstract nearest operation. */
    double bx = trunc(x + copysign(.5, x));
    double by = trunc(y + copysign(.5, y));
    double right = x + w, bottom = y + h;
    double bw = trunc(right + copysign(.5, right)) - bx;
    double bh = trunc(bottom + copysign(.5, bottom)) - by;
    if (!signed32(bx) || !signed32(by) || !signed32(bw) || !signed32(bh))
        return false;
    uint32_t lm = (uint32_t)fmaxf(floorf(wm_log2f(R0)), 0.f);
    uint32_t highest = levels ? levels - 1 : 0;
    *out = (struct variable_blur_parameters){
        .r0 = R0, .r1 = R1, .minimum = highest < lm ? highest : lm,
        .levels = levels, .alignment = alignment,
        .bounds = {(int32_t)bx, (int32_t)by, (int32_t)bw, (int32_t)bh},
        .rect = {x, y, w, h}};
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
    /* prepare_blur_mipmap @18a902ab4..ad0: scale product narrows first. */
    double combined = transform_scale * (double)(float)cap->scale;
    if (!isfinite(combined) || combined > FLT_MAX)
        return false;
    float ss = (float)combined;
    float r0 = mul(rmin, ss), r1 = mul(rmax, ss);
    if (!isfinite(r0) || !isfinite(r1))
        return false;
    struct variable_blur_parameters vb;
    if (!variable_blur_parameters((uint32_t)cap->surface[2], (uint32_t)cap->surface[3],
                                  cap->surface, r0, r1, &vb))
        return false;
    const int32_t* b = vb.bounds;
    uint32_t levels = vb.levels;
    bool no_base = vb.minimum != 0;
    p->no_base      = no_base;
    p->mip_count    = levels - (no_base ? 1u : 0u);
    for (unsigned i = 0; i < 4; i++)
        p->bounds[i] = no_base ? b[i] / 2 : b[i];
    if (p->bounds[2] <= 0 || p->bounds[3] <= 0)
        return false;
    if (!round64((uint32_t)p->bounds[2], &p->texture[0])
        || !round64((uint32_t)p->bounds[3], &p->texture[1]))
        return false;
    p->sample_scale = (double)mul((float)cap->scale, no_base ? .5f : 1.f);
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
