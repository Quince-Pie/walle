/* Public regular/clear fixed-appearance specialization of the extracted source.
 * Unevaluated SpecV1 definitions remain maps; dimensions are evaluated at runtime.
 */
#include "material_internal.h"
#include "material_math.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "material_specs.h"
double        apple_log(double);
static double clampd(double x, double a, double b)
{
    x = x >= a ? x : a;
    return x > b ? b : x;
}
static double map_position(double x, double lo, double hi)
{
    double w = sqrt((hi - lo) * (hi - lo));
    if (!(w > 0x1p-26))
        return 0;
    double a = sqrt((x - lo) * (x - lo));
    return (x < lo ? -a : a) / w;
}
static double smooth(double x)
{
    return (x * x) * (3. - (x + x));
}
static double mapd(const struct wm_map* m, double x)
{
    double t = map_position(x, m->in[0], m->in[1]);
    if (!m->has_upper || t <= 1) {
        double s = clampd(m->reverse ? 1 - t : t, 0, 1);
        return m->out[0] + (m->out[1] - m->out[0]) * s;
    }
    double t2 = map_position(x, m->in[1], m->in[1] + m->transition);
    if (!m->reverse)
        return m->out[1] + (m->upper - m->out[1]) * smooth(clampd(t2, 0, 1));
    return m->upper + (m->out[0] - m->upper) * smooth(clampd(1 - t2, 0, 1));
}
static float mapf(const struct wm_map* m, double x)
{
    double t  = map_position(x, m->in[0], m->in[1]);
    float  lo = (float)m->out[0], hi = (float)m->out[1];
    if (!m->has_upper || t <= 1) {
        float s = (float)clampd(m->reverse ? 1 - t : t, 0, 1);
        return lo + (hi - lo) * s;
    }
    double t2 = map_position(x, m->in[1], m->in[1] + m->transition);
    float  up = (float)m->upper;
    if (!m->reverse)
        return hi + (up - hi) * (float)smooth(clampd(t2, 0, 1));
    return up + (lo - up) * (float)smooth(clampd(1 - t2, 0, 1));
}
static double iom(const struct wm_iom* v, double D)
{
    return v->mapped ? mapd(&v->map, D) : v->value;
}
static double shadow_extent(float opacity)
{
    double d = opacity;
    if (d >= .505)
        return ((fmin(d, 1.) + -.505) / .495) * .05 + 1.65;
    if (d <= .005)
        return 0;
    d = fmax(d + -.005, 0);
    d += d;
    d = apple_log(d) * .3 + 1.65;
    return d < 0 ? 0 : d;
}
static void identity(float m[20])
{
    memset(m, 0, 80);
    m[0] = m[6] = m[12] = m[18] = 1;
}
static void ycc_matrix(const struct wm_ycc* y, float m[20])
{
    wm_ycc_adjust(1, y->black, y->white, y->saturation, m);
    if (y->mask & 1) {
        float a = y->fill[0][3], na = 1.f - a, enc[3];
        for (unsigned i = 0; i < 3; i++)
            enc[i] = wm_srgb_encode(y->fill[0][i]);
        for (unsigned k = 0; k < 20; k++) {
            if (k == 18)
                m[k] = a + m[k] * na;
            else {
                float v = k % 5 == 4 && k < 15 ? enc[k / 5] : 0;
                m[k]    = a * v + na * m[k];
            }
        }
    }
    if (y->mask & 2) {
        float a[20];
        identity(a);
        for (unsigned i = 0; i < 3; i++) {
            float x  = y->fill[1][3] * wm_srgb_encode(y->fill[1][i]);
            x        = x > .99609375f ? .99609375f : x;
            a[6 * i] = x / (1.f - x) + 1.f;
        }
        wm_matrix_multiply(a, m, m);
    }
    if (y->mask & 4) {
        float a[20];
        identity(a);
        float alpha = y->fill[2][3], na = 1 - alpha;
        for (unsigned i = 0; i < 3; i++) {
            float v      = alpha + na * wm_srgb_encode(y->fill[2][i]),
                  k      = v < .00390625f ? 9999.f : 1.f / v;
            a[6 * i]     = k;
            a[5 * i + 4] = 1 - k;
        }
        wm_matrix_multiply(a, m, m);
    }
    for (unsigned i = 0; i < 20; i++) {
        float v = m[i], eps = wm_float(0x2b8cbccc), q = wm_float(0x38d1b717);
        if (-eps < v && v < eps)
            v = 0;
        float r = v / q;
        m[i]    = roundf(r) * q;
    }
}
static void adjust_blur(struct wm_recipe* r)
{
    double v = r->outer_amount;
    if (v == 0 || r->outer_height == 0 || r->refraction_opacity == 0)
        v = 0;
    double* d = r->blur_distances;
    float*  o = r->blur_opacities;
    if (!(v < d[4]))
        return;
    unsigned i  = v < d[3] ? 2 : 3;
    double   lo = d[i], hi = d[i + 1], dv;
    float    olo = o[i], ohi = o[i + 1], s;
    if (fabs(hi - lo) > 0x1p-26) {
        double t = (double)(ohi - olo) / (hi - lo);
        s        = (float)((double)olo + (double)(float)((v - lo) * t));
        dv       = v;
    } else if (hi < v) {
        dv = hi;
        s  = ohi;
    } else {
        dv = lo;
        s  = olo;
    }
    if (i == 2) {
        o[3] = s;
        d[3] = dv;
        d[4] = dv;
    } else {
        d[4] = dv;
        o[4] = s;
    }
}
static void ranges(struct wm_recipe* r)
{
    double hi = 1.;
    if (r->shadow_opacity != 0 && r->shadow_radius > 0)
        hi = fmax(hi, r->shadow_radius * shadow_extent(r->shadow_opacity));
    if (r->blur_radius != 0) {
        double* d   = r->blur_distances;
        float*  o   = r->blur_opacities;
        double  lo  = d[0] < d[1] && o[0] == o[1] ? d[1] : d[0],
               last = d[3] < d[4] && o[3] == o[4] ? d[3] : d[4];
        bool le     = last <= lo;
        if (le) {
            last = d[4];
            lo   = d[0];
        }
        if (lo < last)
            hi = fmax(hi, last + 1.);
    }
    if (r->refraction_opacity != 0 && r->refraction_distances[0] <= r->refraction_distances[1])
        hi = fmax(hi, r->refraction_distances[1] + 1.);
    r->plan.output_minimum = -10000;
    r->plan.output_maximum = hi;
    double sh              = 0;
    if (r->shadow_opacity > 0 && r->shadow_vibrancy > 0) {
        sh = fmax(fabs(r->shadow_amount), r->shadow_radius * shadow_extent(r->shadow_opacity));
        sh += fmax(fabs(r->shadow_offset[0]), fabs(r->shadow_offset[1]));
    }
    double blur = 0;
    for (unsigned i = 0; i < 5; i++)
        if (r->blur_distances[i] >= 0)
            blur = fmax(blur, r->blur_distances[i]);
    if (r->blur_radius <= 0)
        blur *= 0;
    double refr
        = fmax(r->inner_amount, r->refraction_opacity > 0 ? r->outer_amount : r->outer_amount * 0.);
    double bleed   = r->bleed_opacity > 0 ? fabs(r->bleed_amount) : 0;
    r->plan.margin = fmax(fmax(sh, blur), fmax(refr, bleed));
}
static void inactive_spec(struct wm_spec* s, uint8_t style, bool dark)
{
    /* Default regular: SpecV1 sub_240963900, then the regular recipe's
     * non-key tail @240965670. Default clear: post_a::inactive @240916330.
     * Both routes retain the inner refraction and hide the highlights. */
    s->shadow.opacity.out[0] = s->shadow.opacity.out[1] = 0;
    s->refraction.outer_height = s->refraction.outer_amount = 0;
    s->refraction.outer_opacity = 0;
    s->bleed.opacity.out[0] = s->bleed.opacity.out[1] = 0;
    for (unsigned i = 0; i < 5; i++) {
        s->blur.distances[i] = 0;
        s->blur.distance_kinds[i] = 0;
        s->blur.opacities[i] = 1;
    }
    s->highlight.key_opacity = s->highlight.fill_opacity = 0;
    if (style == WM_REGULAR) {
        s->blur.radius.out[0] = 2;
        s->blur.radius.out[1] = 4;
        if (!dark)
            s->face.ycc.white = .95f;
        s->face.ycc.fill[0][3] = .35f;
    } else {
        s->blur.radius = (struct wm_map){.in = {48, 120}, .out = {5, 10}};
        s->face.ycc = (struct wm_ycc){.black = dark ? .05f : .2f,
                                     .white = dark ? .8f : .95f,
                                     .saturation = 1,
                                     .fill = {{1, 1, 1, dark ? .05f : .1f}}, .mask = 1};
    }
}
bool wm_recipe_update(struct wm_recipe* r, const struct wm_material_input* in)
{
    if (!r || !in || in->style > WM_CLEAR || !isfinite(in->width_points)
        || !isfinite(in->height_points) || in->width_points <= 0 || in->height_points <= 0
        || !isfinite(in->backing_scale) || in->backing_scale <= 0)
        return false;
    memset(r, 0, sizeof *r);
    r->input                = *in;
    r->spec                 = wm_specs[in->style * 2 + (in->dark ? 1 : 0)];
    if (!in->active)
        inactive_spec(&r->spec, in->style, in->dark);
    r->D                    = fmin(in->width_points, in->height_points);
    r->pixel_length         = 1. / in->backing_scale;
    const struct wm_spec* s = &r->spec;
    double                D = r->D;
    memcpy(r->shadow_offset, s->shadow.offset, 16);
    r->shadow_amount   = fmin(s->shadow.amount * D, 75.);
    r->shadow_height   = s->shadow.height * D;
    r->shadow_blur     = s->shadow.blur_radius;
    r->shadow_radius   = s->shadow.radius;
    r->shadow_opacity  = mapf(&s->shadow.opacity, D);
    r->shadow_vibrancy = mapd(&s->shadow.vibrancy, D);
    double b           = mapd(&s->blur.radius, D);
    r->blur_radius     = b + b;
    for (unsigned i = 0; i < 5; i++) {
        r->blur_distances[i]
            = s->blur.distance_kinds[i] ? s->blur.distances[i] * D : s->blur.distances[i];
        r->blur_opacities[i] = s->blur.opacities[i];
    }
    r->inner_height            = clampd(s->refraction.inner_height * D,
                             s->refraction.inner_height_range[0],
                             s->refraction.inner_height_range[1]);
    r->inner_amount            = clampd(s->refraction.inner_amount * D,
                             s->refraction.inner_amount_range[0],
                             s->refraction.inner_amount_range[1]);
    r->inner_amount            = fmin(r->inner_amount, 0);
    r->outer_height            = s->refraction.outer_height * D;
    r->outer_amount            = D * s->refraction.outer_amount;
    r->refraction_distances[0] = -1.;
    r->refraction_distances[1] = r->pixel_length + -1.;
    r->refraction_opacity      = s->refraction.outer_opacity;
    r->bleed_amount            = s->bleed.amount * D;
    r->bleed_height            = s->bleed.height * D;
    r->bleed_blur              = fmin(s->bleed.blur_radius * D, s->bleed.maximum_blur);
    r->bleed_opacity           = mapf(&s->bleed.opacity, D);
    if (in->style == WM_CLEAR) {
        r->shadow_opacity  = 0;
        r->bleed_amount    = 0;
        r->bleed_height    = 0;
        r->shadow_vibrancy = 0;
    }
    if (r->shadow_opacity == 0) {
        r->shadow_blur *= 0;
        r->shadow_radius *= 0;
    }
    if (r->bleed_height == 0 || r->bleed_amount == 0 || r->bleed_opacity == 0) {
        r->shadow_vibrancy *= 0;
        r->bleed_blur *= 0;
        r->bleed_opacity *= 0;
    }
    if (r->shadow_vibrancy == 0)
        r->shadow_blur = 0;
    adjust_blur(r);
    const struct wm_map sdr = {.in = {48, 160}, .out = {0x1.47ae14p-4, 0x1.eb851ep-3}};
    r->sdr_shadow           = in->active ? mapf(&sdr, D) : 0;
    r->key_height           = iom(&s->highlight.key_height, D);
    r->fill_height          = iom(&s->highlight.fill_height, D);
    r->spread               = iom(&s->highlight.spread, D);
    ycc_matrix(&s->highlight.key_ycc, r->highlight_matrix);
    float light = in->dark ? wm_srgb_encode(wm_float(0x3f800002)) : 0.f, inv = 1.f - light;
    wm_ycc_adjust(1, light * .95f + inv * 0.f, light + inv * .1f, 1, r->face_matrix);
    r->face_matrix[18] = 0;
    if (in->tint.present)
        wm_tint_matrix(in->tint.srgb, in->dark, in->active, 1, r->tint_matrix);
    r->plan.backdrop_scale     = s->backdrop_scale;
    r->plan.smoothness         = 0;
    r->plan.ovalization        = r->bleed_opacity > 0 ? .5 : 0;
    r->plan.has_backdrop       = true;
    r->plan.has_highlight      = true;
    r->plan.has_tint           = in->tint.present;
    r->plan.tracks_luma        = false;
    r->plan.face_opacity       = 1;
    r->plan.highlight_opacity  = fabsf(s->highlight.key_opacity) < .00034526698f ? 0 : 1;
    r->plan.tint_group_opacity = 1;
    r->plan.highlight_pad      = 1;
    r->plan.highlight_maximum  = fmaxf((float)r->key_height, (float)r->fill_height);
    r->plan.shadow_grow        = (double)(float)(2. * r->shadow_radius);
    memcpy(r->plan.shadow_offset, r->shadow_offset, 16);
    r->plan.maximum_refraction    = fmax(fmax(fabs(r->bleed_amount), fabs(r->inner_amount)),
                                      fmax(fabs(r->outer_amount), fabs(r->shadow_amount)));
    r->plan.tint_mask_pad         = 1;
    r->plan.tint_mask_maximum     = INFINITY;
    r->plan.tint_gradient_pad     = 10;
    r->plan.tint_gradient_maximum = INFINITY;
    r->plan.tint_distances[0]     = -1;
    r->plan.tint_distances[2]     = 10;
    float sh                      = r->shadow_opacity > 0 ? (float)(r->shadow_blur * .5) : 0,
          bl                      = r->bleed_opacity > 0 ? (float)(r->bleed_blur * .5) : 0,
          br                      = (float)(r->blur_radius * .5);
    r->plan.blur_max              = fmaxf(fmaxf(sh, bl), br);
    bool uniform = r->shadow_opacity <= 0 && r->bleed_opacity <= 0
        && r->blur_opacities[0] == 1 && r->blur_opacities[2] == 1
        && r->blur_opacities[3] == 1 && r->blur_opacities[4] == 1;
    r->plan.blur_min = uniform ? br : fminf(fminf(sh, bl), r->blur_opacities[3]);
    ranges(r);
    return true;
}
struct wm_recipe* wm_recipe_create(const struct wm_material_input* in)
{
    struct wm_recipe* r = malloc(sizeof *r);
    if (r && !wm_recipe_update(r, in)) {
        free(r);
        r = nullptr;
    }
    return r;
}
void wm_recipe_destroy(struct wm_recipe* r)
{
    free(r);
}

static void fill_color(const struct wm_ycc* y, float out[4])
{
    memset(out, 0, 16);
    if (!(y->mask & 1))
        return;
    float a = y->fill[0][3];
    for (unsigned i = 0; i < 3; i++)
        out[i] = wm_srgb_encode(y->fill[0][i]) * a;
    out[3] = a;
}
static void composite_ycc(const struct wm_ycc* y, const float fill[4], uint8_t out[24])
{
    float c[20];
    wm_ca_ycc_composite(y->white, y->black, y->saturation, fill, c);
    unsigned n = 0;
    for (unsigned i = 0; i < 3; i++)
        for (unsigned j = 0; j < 4; j++) {
            uint16_t h = wm_half(c[i * 5 + (j == 3 ? 4 : j)]);
            memcpy(out + 2 * n++, &h, 2);
        }
}
static void pack_matrix(const float m[20], float clamp, float gamma, uint8_t out[48])
{
    uint16_t words[24] = {0};
    for (unsigned j = 0; j < 5; j++)
        for (unsigned i = 0; i < 4; i++)
            words[4 * j + i] = wm_half(m[5 * i + j]);
    words[20] = wm_half(clamp != 0 ? wm_powf(clamp, 1.f / gamma) : 0);
    memcpy(out, words, 48);
}
static double timing(const double cp[4], double x, double eps)
{
    double d4 = cp[0] * 3, dx = cp[2] - cp[0], d5 = fma(dx, 3, -d4), d6 = fma(-dx, 3, 1),
           d16 = d6 * 3, d17 = d5 + d5, t = x;
    bool done = false;
    for (unsigned i = 0; i < 8; i++) {
        double v = fma(fma(fma(t, d6, d5), t, d4), t, -x);
        if (fabs(v) < eps) {
            done = true;
            break;
        }
        double d = fma(fma(d16, t, d17), t, d4);
        if (fabs(d) < 1e-6)
            break;
        t -= v / d;
    }
    if (!done) {
        if (x < 0)
            t = 0;
        else if (x > 1)
            t = 1;
        else {
            double lo = 0, hi = 1;
            t = x;
            for (unsigned i = 0; i < 1024; i++) {
                double v = fma(fma(fma(t, d6, d5), t, d4), t, -x);
                if (fabs(v) < eps)
                    break;
                if (v < 0)
                    lo = t;
                else
                    hi = t;
                t = fma(hi - lo, .5, lo);
                if (!(lo < hi))
                    break;
            }
        }
    }
    double dy = cp[3] - cp[1], e1 = fma(dy, 3, -1), e2 = cp[1] * 3, e0 = fma(dy, 3, -e2);
    e0 = fma(-t, e1, e0);
    e0 = fma(e0, t, e2);
    return e0 * t;
}
static void tint_ramp(uint8_t bytes[2048])
{
    const float  locations[3] = {-1, 0, 10};
    const double cps[2][4]
        = {{0, 0, 1, 1}, {(double)0.60938f, (double)0.00663f, (double)0.47124f, (double)0.99115f}};
    float step = (locations[2] - locations[0]) / 256.f,
          x = (float)fma((double)step, .5, (double)locations[0]), end = locations[0], start = end,
          inv  = INFINITY;
    unsigned k = 0, i0 = 0, i1 = 0, curve = 0, next = 0;
    for (unsigned i = 0; i < 256; i++) {
        while (!(x < end)) {
            i0 = i1;
            k++;
            float    ne = 2;
            unsigned nxt;
            if (k >= 3) {
                end = 2;
                nxt = next;
                i1  = i0;
            } else {
                ne        = locations[k];
                float seg = ne - end;
                i1        = i0 + 1;
                nxt       = next + 1;
                inv       = 1.f / seg;
            }
            curve = next;
            start = end;
            next  = nxt;
            end   = ne;
        }
        float alpha;
        if (!(end > start))
            alpha = i0 == 2 ? 0 : 1;
        else {
            float t  = inv * (x - start);
            t        = (float)timing(cps[curve], t, 1e-5);
            float a0 = i0 == 2 ? 0 : 1, a1 = i1 == 2 ? 0 : 1;
            alpha = fmaf(a1 - a0, t, a0);
        }
        uint16_t h = wm_half(alpha);
        for (unsigned c = 0; c < 4; c++)
            memcpy(bytes + (i * 4 + c) * 2, &h, 2);
        x += step;
    }
}
bool wm_recipe_pack_glass(const struct wm_recipe* r, const struct wm_render_domain* d,
                           uint8_t glass_lph[216], uint32_t* texture_function)
{
    if (!r || !d || !glass_lph || !texture_function || !d->source_width || !d->source_height
        || d->source_scale <= 0 || d->gamma <= 0) {
        if (glass_lph)
            memset(glass_lph, 0, 216);
        if (texture_function)
            *texture_function = 0;
        return false;
    }
    float sc    = (float)d->source_scale,
          mf[4] = {(float)d->transform[0],
                   (float)d->transform[2],
                   (float)d->transform[1],
                   (float)d->transform[3]},
          f[20];
    for (unsigned i = 0; i < 4; i++)
        f[i] = (mf[i] * sc / (float)(i < 2 ? d->source_width : d->source_height))
               + mf[(i + 2) % 4] * 0.f;
    double a = d->transform[2] * d->transform[2] + d->transform[3] * d->transform[3],
           b = d->transform[0] * d->transform[0] + d->transform[1] * d->transform[1];
    if (a != 1 || b != 1) {
        a = sqrt(a);
        b = sqrt(b);
    }
    float k = (float)((b > a ? b : a) * (double)sc) * 1.6f;
    f[4]    = (float)r->inner_amount;
    f[5]    = (float)(1. / r->inner_height);
    f[6]    = (float)r->outer_amount;
    f[7]    = (float)(1. / r->outer_height);
    f[8]    = (float)r->refraction_distances[0];
    f[9]    = (float)r->refraction_distances[1];
    f[10]   = (float)(r->blur_radius * .5) * k;
    f[11]   = (float)(r->bleed_opacity > 0 ? r->bleed_blur * .5 : 0) * k;
    f[12]   = (float)r->bleed_amount;
    f[13]   = (float)(1. / r->bleed_height);
    f[14]   = (float)r->shadow_amount;
    f[15]   = (float)(1. / r->shadow_height);
    f[16]   = -(float)r->shadow_offset[0];
    f[17]   = -(float)r->shadow_offset[1];
    f[18]   = k * (float)(r->shadow_opacity > 0 ? r->shadow_blur * .5 : 0);
    f[19]   = (float)(1. / r->shadow_radius);
    memcpy(glass_lph, f, 80);
    float hr = (float)d->headroom, m = hr > 9999.f ? 9999.f : hr;
    float t = (m < 1 ? 0 : m - 1.f) / (9999.f - 1.f), face[4], bleed[4], shadow[4];
    fill_color(&r->spec.face.ycc, face);
    fill_color(&r->spec.bleed.ycc, bleed);
    fill_color(&r->spec.shadow.ycc, shadow);
    if (r->sdr_shadow > 0)
        shadow[3] += fmaf(-t, r->sdr_shadow, r->sdr_shadow);
    composite_ycc(&r->spec.face.ycc, face, glass_lph + 80);
    composite_ycc(&r->spec.bleed.ycc, bleed, glass_lph + 104);
    composite_ycc(&r->spec.shadow.ycc, shadow, glass_lph + 128);
    float extra[2] = {(float)r->shadow_vibrancy, shadow[3]};
    memcpy(glass_lph + 152, extra, 8);
    float blur_opacity = r->spec.blur.opacity;
    float bo[4] = {r->blur_opacities[0] * blur_opacity,
                   r->blur_opacities[1] * blur_opacity,
                   r->blur_opacities[2] * blur_opacity,
                   r->blur_opacities[3] * blur_opacity};
    float clamp = fmaxf(wm_srgb_decode(r->spec.face.ycc.white), 1.f),
          h[28] = {bo[0],
                   bo[0] - bo[1],
                   bo[1] - bo[2],
                   bo[2] - bo[3],
                   (float)r->blur_distances[0],
                   (float)r->blur_distances[1],
                   (float)r->blur_distances[2],
                   (float)r->blur_distances[3],
                   1,
                   0,
                   r->bleed_opacity,
                   r->spec.face.opacity,
                   r->spec.bleed.darken ? 1 : -1,
                   r->spec.bleed.darken ? 0 : 1,
                   0,
                   r->shadow_opacity,
                   r->refraction_opacity,
                   r->input.active ? 1.f - t : 0,
                   r->input.active ? (float)(r->pixel_length * -2) : 0,
                   r->input.active ? (float)-r->pixel_length : 0,
                   wm_powf(clamp, 1.f / (float)d->gamma),
                   0,
                   r->input.active ? .97f : 1,
                   0,
                   1,
                   0,
                   0,
                   0};
    for (unsigned i = 0; i < 28; i++) {
        uint16_t v = wm_half(h[i]);
        memcpy(glass_lph + 160 + 2 * i, &v, 2);
    }
    *texture_function = r->bleed_opacity > 0 ? 0x45 : 0x47;
    return true;
}

bool wm_recipe_pack(const struct wm_recipe*        r,
                    const struct wm_render_domain* d,
                    struct wm_shader_packet*       o)
{
    if (!r || !d || !o || !d->source_width || !d->source_height || d->source_scale <= 0
        || d->gamma <= 0)
        return false;
    memset(o, 0, sizeof *o);
    o->plan     = r->plan;
    if (!wm_recipe_pack_glass(r, d, o->glass_lph, &o->glass_texture_function))
        return false;
    pack_matrix(r->face_matrix, 0, (float)d->gamma, o->face_vcm);
    pack_matrix(r->highlight_matrix, 1, (float)d->gamma, o->highlight_vcm);
    if (r->input.tint.present)
        pack_matrix(r->tint_matrix, 0, (float)d->gamma, o->tint_vcm);
    double ka = r->spec.highlight.key_offset + (-0x1.921fb54442d18p-1),
           fa = r->spec.highlight.fill_offset + (0x1.921fb54442d18p+1 + -0x1.921fb54442d18p-1);
    double kh = r->key_height, fh = r->fill_height, ks = r->spread, fs = r->spread;
    float  opacity = 1;
    if (d->global_light) {
        ka      = (float)((d->light_angle + 0x1.921fb54442d18p+0) + (double)(float)ka);
        fa      = (float)((d->light_angle + 0x1.921fb54442d18p+0) + (double)(float)fa);
        opacity = isnan(d->light_opacity) ? 1 : (float)clampd(d->light_opacity, 0, 1);
        if (!isnan(d->light_height))
            kh = fh = d->light_height;
        if (!isnan(d->light_spread))
            ks = fs = d->light_spread;
    }
    float sk, ck, sf, cf;
    wm_sincosf((float)ka, &sk, &ck);
    wm_sincosf((float)fa, &sf, &cf);
    float effect[20] = {(float)kh,
                        wm_cosf((float)ks),
                        (float)(1. / (double)(float)r->spec.highlight.amount - 2),
                        sk,
                        -ck,
                        (float)fh,
                        wm_cosf((float)fs),
                        (float)(1. / (double)(float)r->spec.highlight.amount - 2),
                        sf,
                        -cf,
                        (float)r->spec.highlight.curvature,
                        0};
    for (unsigned i = 0; i < 4; i++) {
        effect[12 + i]
            = (r->spec.highlight.key_opacity * (float)r->plan.highlight_opacity) * opacity;
        effect[16 + i]
            = (r->spec.highlight.fill_opacity * (float)r->plan.highlight_opacity) * opacity;
    }
    for (unsigned i = 0; i < 20; i++) {
        uint16_t v = wm_half(effect[i]);
        memcpy(o->key_fill + 2 * i, &v, 2);
    }
    float grad[4] = {0, -1, 10, -1.f / 11.f};
    memcpy(o->tint_gradient, grad, 16);
    for (unsigned i = 0; i < 4; i++) {
        uint16_t one = 0x3c00;
        memcpy(o->tint_gradient + 16 + 2 * i, &one, 2);
        memcpy(o->tint_mask_fill + 8 + 2 * i, &one, 2);
    }
    tint_ramp(o->tint_ramp_rgba16f);
    return true;
}
