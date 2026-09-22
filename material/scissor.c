/* H.glass_dod18a78c65c, gaussian_expansion_factor18a78adc4, aa_round18a7a621c.
 * Compile with -ffp-contract=off. Apple log is the retained exact scalar twin.
 */
#include "scissor.h"
#include "material_internal.h"
#include <float.h>
#include <limits.h>
#include <math.h>
#include <string.h>

double      apple_log(double);
static bool empty(const double r[4])
{
    return !(r[2] > 0 && r[3] > 0);
}
static bool bounded(const double r[4])
{
    return fmax(r[2], r[3]) < DBL_MAX;
}
static bool finite_rect(const double r[4])
{
    return r && isfinite(r[0]) && isfinite(r[1]) && isfinite(r[2]) && isfinite(r[3]);
}
static void rect_union(const double a[4], const double b[4], double out[4])
{
    if (empty(a)) {
        memcpy(out, b, 4 * sizeof(double));
        return;
    }
    if (empty(b)) {
        memcpy(out, a, 4 * sizeof(double));
        return;
    }
    double x = fmin(a[0], b[0]), y = fmin(a[1], b[1]);
    double r[4] = {x, y, fmax(a[0] + a[2], b[0] + b[2]) - x, fmax(a[1] + a[3], b[1] + b[3]) - y};
    memcpy(out, r, sizeof r);
}
static void rect_intersection(const double a[4], const double b[4], double out[4])
{
    double x = fmax(a[0], b[0]), y = fmax(a[1], b[1]);
    double x1   = fmin(a[0] + a[2], b[0] + b[2]);
    double y1   = fmin(a[1] + a[3], b[1] + b[3]);
    double r[4] = {x, y, x1 > x && y1 > y ? x1 - x : 0, x1 > x && y1 > y ? y1 - y : 0};
    memcpy(out, r, sizeof r);
}
double wm_gaussian_expansion(double opacity, bool debug_2_8)
{
    if (debug_2_8)
        return 2.8;
    if (opacity >= .505)
        return fma(opacity, .10101010101010102, 1.598989898989899);
    if (opacity <= .005)
        return 0;
    double t = opacity + -.005;
    t        = t < 0 ? 0 : t;
    if (t + t == 0 || isnan(t))
        return 0;
    double ln = apple_log(t + t), r = fma(ln, .3, 1.65);
    return isfinite(ln) ? (r < 0 ? 0 : r) : 0;
}
bool wm_recipe_glass_extent(const struct wm_recipe* r, struct wm_glass_extent* out)
{
    if (!r || !out)
        return false;
    *out = (struct wm_glass_extent){.shadow_offset  = {r->shadow_offset[0], r->shadow_offset[1]},
                                    .shadow_radius  = r->shadow_radius,
                                    .shadow_opacity = r->shadow_opacity,
                                    .blur_radius    = r->blur_radius * .5,
                                    .bleed_blur_radius
                                    = r->bleed_opacity > 0 ? r->bleed_blur * .5 : 0,
                                    .shadow_contribution = r->shadow_vibrancy,
                                    .outer_refraction    = r->outer_amount};
    return true;
}
bool wm_glass_dod(const struct wm_glass_extent* q,
                  const double                  R[4],
                  const double                  backdrop[4],
                  double                        out[4])
{
    if (!q || !out || !finite_rect(R) || !finite_rect(backdrop))
        return false;
    bool   live = bounded(R) && !empty(R);
    double e    = wm_gaussian_expansion(q->shadow_opacity, false) * q->shadow_radius;
    double S[4], B[4], G[4];
    memcpy(S, R, sizeof S);
    memcpy(B, R, sizeof B);
    memcpy(G, backdrop, sizeof G);
    if (live) {
        double d = -e;
        S[0] += d;
        S[1] += d;
        S[2] = fma(e, 2, R[2]);
        S[3] = fma(e, 2, R[3]);
        if (fmin(S[2], S[3]) <= 0)
            S[2] = S[3] = 0;
    }
    double two_blur = 2 * q->blur_radius;
    double k        = q->bleed_blur_radius < two_blur ? two_blur : q->bleed_blur_radius;
    if (live) {
        double t = k * -2.8;
        B[0] += t;
        B[1] += t;
        B[2] = fma(k, 5.6, R[2]);
        B[3] = fma(k, 5.6, R[3]);
        if (B[2] <= 0 || B[3] <= 0)
            B[2] = B[3] = 0;
    }
    S[0] += q->shadow_offset[0];
    S[1] += q->shadow_offset[1];
    double U[4];
    rect_union(B, S, U);
    if (q->shadow_contribution == 0) {
        double d0 = e - q->outer_refraction, d3 = d0 < 0 ? 0 : d0, P[4];
        memcpy(P, G, sizeof P);
        if (bounded(G) && !empty(G)) {
            double dn = -(double)(float)d3;
            P[0] += dn;
            P[1] += dn;
            P[2] -= dn + dn;
            P[3] = fma(dn, -2, G[3]);
            if (fmin(P[2], P[3]) <= 0)
                P[2] = P[3] = 0;
        }
        P[0] += q->shadow_offset[0];
        P[1] += q->shadow_offset[1];
        rect_union(G, P, G);
    }
    if (empty(U))
        memcpy(out, U, sizeof U);
    else if (empty(G)) {
        out[0] = U[0];
        out[1] = U[1];
        out[2] = out[3] = 0;
    } else
        rect_intersection(U, G, out);
    return finite_rect(out);
}
bool wm_aa_round(const double r[4], bool antialias, int32_t out[4])
{
    if (!out || !finite_rect(r))
        return false;
    if (empty(r)) {
        memset(out, 0, 4 * sizeof(int32_t));
        return true;
    }
    double x = floor(r[0]), y = floor(r[1]);
    double rounded[4] = {x, y, ceil(r[0] + r[2]) - x, ceil(r[1] + r[3]) - y};
    bool   equal      = true;
    for (unsigned i = 0; i < 4; ++i)
        equal = equal && rounded[i] == r[i];
    if (antialias && !equal) {
        rounded[0] -= 1;
        rounded[1] -= 1;
        rounded[2] += 2;
        rounded[3] += 2;
    }
    for (unsigned i = 0; i < 4; ++i) {
        if (!(rounded[i] >= INT32_MIN && rounded[i] <= INT32_MAX))
            return false;
        out[i] = (int32_t)rounded[i];
    }
    return true;
}
bool wm_scissor_transform(
    const double r[4], const double m[6], uint32_t w, uint32_t h, bool antialias, int32_t out[4])
{
    if (!out || !m || !w || !h || w > INT32_MAX || h > INT32_MAX || !finite_rect(r))
        return false;
    for (unsigned i = 0; i < 6; ++i)
        if (!isfinite(m[i]))
            return false;
    if (empty(r)) {
        memset(out, 0, 4 * sizeof(int32_t));
        return true;
    }
    double lo[2] = {INFINITY, INFINITY}, hi[2] = {-INFINITY, -INFINITY};
    for (unsigned y = 0; y < 2; ++y)
        for (unsigned x = 0; x < 2; ++x) {
            double px = r[0] + (x ? r[2] : 0), py = r[1] + (y ? r[3] : 0);
            double v[2] = {(px * m[0] + py * m[2]) + m[4], (px * m[1] + py * m[3]) + m[5]};
            for (unsigned i = 0; i < 2; ++i) {
                lo[i] = fmin(lo[i], v[i]);
                hi[i] = fmax(hi[i], v[i]);
            }
        }
    double transformed[4] = {lo[0], lo[1], hi[0] - lo[0], hi[1] - lo[1]};
    double canvas[4]      = {0, 0, w, h};
    rect_intersection(transformed, canvas, transformed);
    if (!wm_aa_round(transformed, antialias, out))
        return false;
    /* An AA outset may cross the target boundary; intersect the integer result. */
    int64_t x0 = out[0] > 0 ? out[0] : 0, y0 = out[1] > 0 ? out[1] : 0;
    int64_t x1 = (int64_t)out[0] + out[2], y1 = (int64_t)out[1] + out[3];
    x1     = x1 < w ? x1 : w;
    y1     = y1 < h ? y1 : h;
    out[0] = (int32_t)x0;
    out[1] = (int32_t)y0;
    out[2] = (int32_t)(x1 > x0 ? x1 - x0 : 0);
    out[3] = (int32_t)(y1 > y0 ? y1 - y0 : 0);
    return true;
}
