/* Exact finite scalar source geometry from lg_host.py,18a85e900/18a8a27cc. */
#include "geometry.h"
#include <math.h>
#include <string.h>
static const double EXTENT = 1.528665, K1 = -1.4457785175867517, K2 = 2.8915570351735034;
static double       maxd(double a, double b)
{
    return a > b ? a : b;
}
static double mind(double a, double b)
{
    return a < b ? a : b;
}
static double circularity(double v, double e)
{
    double z = v * K1 / e + K2;
    return mind(maxd(z, 0), 1);
}
float wm_sdf_scale(const double M[4])
{
    if (!M)
        return 1.f;
    float a = (float)M[0], b = (float)M[1], c = (float)M[2], d = (float)M[3];
    float det = fmaf(-d, -a, -(c * b));
    float L   = fmaxf(sqrtf(fmaf(d, d, b * b)), sqrtf(fmaf(c, c, a * a)));
    return (1.f / L) * det;
}
bool wm_sdf_arguments(double       w,
                      double       h,
                      const double M[4],
                      double       radius,
                      bool         continuous,
                      double       oval,
                      double       outset,
                      float        out[12])
{
    if (!out || !isfinite(w) || !isfinite(h) || !isfinite(radius) || !isfinite(outset) || w <= 0
        || h <= 0)
        return false;
    float s = 1, tr[4] = {1, 0, 0, 1};
    if (M) {
        float a = (float)M[0], b = (float)M[1], c = (float)M[2], d = (float)M[3];
        float det = fmaf(-d, -a, -(c * b));
        float L   = fmaxf(sqrtf(fmaf(d, d, b * b)), sqrtf(fmaf(c, c, a * a)));
        float inv = 1.f / L;
        s         = inv * det;
        tr[0]     = inv * d;
        tr[1]     = -(b * inv);
        tr[2]     = -(c * inv);
        tr[3]     = inv * a;
    }
    if (!isfinite(s) || s == 0)
        return false;
    double grow = (double)(-(float)outset / s), W = w - 2 * grow, H = h - 2 * grow;
    float  P0 = (float)(W * (double)s) * .5f, P1 = (float)(H * (double)s) * .5f;
    out[0] = P0 - (float)outset;
    out[1] = P1 - (float)outset;
    out[2] = 4;
    out[3] = (float)oval;
    memcpy(out + 4, tr, 16);
    float  r = (float)radius;
    double R = (r >= .01f ? (double)r : (double).01f) * (double)s;
    out[8]   = continuous ? (float)circularity((double)(out[0] + out[0]), R * EXTENT) : 1;
    out[9]   = continuous ? (float)circularity((double)(out[1] + out[1]), R * EXTENT) : 1;
    out[10]  = (float)R;
    out[11]  = 0;
    return true;
}
static const uint32_t C4[] = {0, 1, 5, 4, 3, 7, 6, 2, 10, 11, 15, 14, 9, 13, 12, 8};
static const uint32_t E4[] = {1, 2, 6, 5, 4, 5, 9, 8, 6, 7, 11, 10, 9, 10, 14, 13, 5, 6, 10, 9};
static const uint32_t CROSS6[]
    = {14, 15, 21, 20, 8, 9, 15, 14, 20, 21, 27, 26, 13, 14, 20, 19, 15, 16, 22, 21};
static const uint32_t C6[] = {7, 8, 14, 13, 9, 10, 16, 15, 21, 22, 28, 27, 19, 20, 26, 25};
static const uint32_t R6[]
    = {2,  3,  9,  8,  26, 27, 33, 32, 12, 13, 19, 18, 16, 17, 23, 22, 0,  1,  7,  6,  1,  2,
       8,  7,  6,  7,  13, 12, 4,  5,  11, 10, 3,  4,  10, 9,  10, 11, 17, 16, 28, 29, 35, 34,
       27, 28, 34, 33, 22, 23, 29, 28, 24, 25, 31, 30, 25, 26, 32, 31, 18, 19, 25, 24};
static const uint32_t R4[] = {0, 1, 5,  4,  1, 2, 6,  5,  2, 3,  7,  6,  4,  5,  9,  8,
                              6, 7, 11, 10, 8, 9, 13, 12, 9, 10, 14, 13, 10, 11, 15, 14};
static void           group(struct wm_sdf_grid* o, const uint32_t* q, unsigned n, float mode)
{
    struct wm_mesh_group* g = o->groups + o->group_count++;
    g->mode                 = mode;
    if (mode == 21) {
        g->mode           = 4;
        g->image_function = 21;
    } else if (mode == 20) {
        g->mode           = 0;
        g->image_function = 20;
    }
    for (unsigned i = 0; i < n; i += 4) {
        uint32_t t[6] = {q[i], q[i + 1], q[i + 2], q[i + 2], q[i + 3], q[i]};
        memcpy(g->indices + g->index_count, t, sizeof t);
        g->index_count += 6;
    }
}
bool wm_sdf_grid_build(double              w,
                       double              h,
                       double              radius,
                       bool                continuous,
                       double              outset,
                       double              maximum,
                       double              grow,
                       const double        offset[2],
                       bool                surface,
                       double              scale,
                       struct wm_sdf_grid* o)
{
    if (!o || !offset || w <= 0 || h <= 0 || scale <= 0 || isnan(maximum) || maximum < 0
        || !isfinite(w + h + radius + outset + grow + scale + offset[0] + offset[1]))
        return false;
    memset(o, 0, sizeof *o);
    float  sf     = (float)scale;
    double g      = (double)(-(float)outset / sf);
    double R1[4]  = {g, g, w - 2 * g, h - 2 * g};
    float  P[2]   = {(float)(R1[2] * (double)sf) * .5f, (float)(R1[3] * (double)sf) * .5f};
    float  r      = (float)radius;
    double extent = (r >= .01f ? (double)r : (double).01f) * (double)sf;
    if (continuous)
        extent *= EXTENT;
    double ext    = extent + (double)(float)outset;
    double R2[4]  = {offset[0] - grow / scale,
                     offset[1] - grow / scale,
                     w + 2 * grow / scale,
                     h + 2 * grow / scale};
    bool   shadow = maxd(maxd(grow, offset[0]), offset[1]) > 0;
    double x1 = R1[2] + R1[0], y1 = R1[3] + R1[1];
    double ox0 = (float)mind(R1[0], R2[0]), oy0 = (float)mind(R1[1], R2[1]),
           ox1 = (float)maxd(R2[2] + R2[0], x1), oy1 = (float)maxd(R2[3] + R2[1], y1);
    if (!(ext >= (double)fmaxf(P[0], P[1]))) {
        float ex  = ext < (double)P[0] ? (float)ext : P[0],
              ey  = ext < (double)P[1] ? (float)ext : P[1];
        double dx = (double)(ex / sf), dy = (double)(ey / sf);
        double ix[4] = {R1[0], R1[0] + dx, x1 - dx, x1}, iy[4] = {R1[1], R1[1] + dy, y1 - dy, y1};
        float  sx[4] = {-P[0], -P[0] + ex, P[0] - ex, P[0]},
              sy[4]  = {-P[1], -P[1] + ey, P[1] - ey, P[1]};
        if (shadow && surface) {
            o->nx = o->ny = 6;
            o->x[0]       = ox0;
            o->y[0]       = oy0;
            o->x[5]       = ox1;
            o->y[5]       = oy1;
            memcpy(o->x + 1, ix, sizeof ix);
            memcpy(o->y + 1, iy, sizeof iy);
            memcpy(o->sx + 1, sx, sizeof sx);
            memcpy(o->sy + 1, sy, sizeof sy);
            o->sx[0] = (float)fma(ox0 - R1[0], sf, -P[0]);
            o->sy[0] = (float)fma(oy0 - R1[1], sf, -P[1]);
            o->sx[5] = (float)fma(ox1 - x1, sf, P[0]);
            o->sy[5] = (float)fma(oy1 - y1, sf, P[1]);
            group(o, C6, 16, 4);
            group(o, R6, 64, -4);
            group(o, CROSS6, 20, 0);
        } else {
            o->nx = o->ny = 4;
            memcpy(o->x, ix, sizeof ix);
            memcpy(o->y, iy, sizeof iy);
            memcpy(o->sx, sx, sizeof sx);
            memcpy(o->sy, sy, sizeof sy);
            group(o, C4, 16, surface ? 4 : 21);
            group(o, E4, extent >= maximum ? 16 : 20, surface ? 0 : 20);
        }
    } else if (surface && shadow) {
        o->nx = o->ny = 4;
        double xs[4] = {ox0, R1[0], x1, ox1}, ys[4] = {oy0, R1[1], y1, oy1};
        memcpy(o->x, xs, sizeof xs);
        memcpy(o->y, ys, sizeof ys);
        float sx[4]
            = {(float)fma(ox0 - R1[0], sf, -P[0]), -P[0], P[0], (float)fma(ox1 - x1, sf, P[0])},
            sy[4]
            = {(float)fma(oy0 - R1[1], sf, -P[1]), -P[1], P[1], (float)fma(oy1 - y1, sf, P[1])};
        memcpy(o->sx, sx, sizeof sx);
        memcpy(o->sy, sy, sizeof sy);
        const uint32_t center[4] = {5, 6, 10, 9};
        group(o, center, 4, 4);
        group(o, R4, 32, -4);
    } else {
        o->nx = o->ny       = 2;
        o->x[0]             = R1[0];
        o->x[1]             = x1;
        o->y[0]             = R1[1];
        o->y[1]             = y1;
        o->sx[0]            = -P[0];
        o->sx[1]            = P[0];
        o->sy[0]            = -P[1];
        o->sy[1]            = P[1];
        const uint32_t q[4] = {0, 1, 3, 2};
        group(o, q, 4, surface ? 4 : 21);
    }
    return true;
}
uint32_t wm_sdf_vertices(const struct wm_sdf_grid* g,
                         const double              a[6],
                         double                    sample_scale,
                         const double              origin[2],
                         const uint32_t            size[2],
                         struct wm_vertex          out[36])
{
    if (!g || !a || !origin || !size || !out || !size[0] || !size[1])
        return 0;
    unsigned n = 0;
    for (unsigned y = 0; y < g->ny; y++)
        for (unsigned x = 0; x < g->nx; x++) {
            struct wm_vertex* v = out + n++;
            v->position[0]      = (float)((g->x[x] * a[0] + g->y[y] * a[2]) + a[4]);
            v->position[1]      = (float)((g->x[x] * a[1] + g->y[y] * a[3]) + a[5]);
            v->local_uv[0]      = g->sx[x];
            v->local_uv[1]      = g->sy[y];
            for (unsigned k = 0; k < 2; k++)
                v->source_uv[k] = fmaf(v->position[k], (float)sample_scale, -(float)origin[k])
                                  * (1.f / (float)size[k]);
        }
    return n;
}
bool wm_face_grid_build(double w, double h, double radius, bool continuous, struct wm_face_grid* o)
{
    if (!o || !isfinite(w + h + radius) || w <= 0 || h <= 0)
        return false;
    double e = radius * (continuous ? EXTENT : 1.);
    if (e < 1)
        return false;
    memset(o, 0, sizeof *o);
    double x[4] = {0, e, w - e, w}, y[4] = {0, e, h - e, h};
    memcpy(o->x, x, sizeof x);
    memcpy(o->y, y, sizeof y);
    float uv[4] = {-1, 0, 0, 1};
    memcpy(o->u, uv, sizeof uv);
    memcpy(o->v, uv, sizeof uv);
    if (continuous) {
        o->circularity[0] = (float)circularity(w, e);
        o->circularity[1] = (float)circularity(h, e);
    }
    for (unsigned k = 0; k < 2; k++) {
        double* p = k ? o->y : o->x;
        float*  t = k ? o->v : o->u;
        if (p[1] > p[2]) {
            double mid = (float)((p[2] + p[1]) * .5);
            float  f   = (float)((mid - p[0]) / (p[1] - p[0]));
            t[1]       = (t[1] - t[0]) * f + t[0];
            t[2]       = (t[2] - t[3]) * f + t[3];
            p[1] = p[2] = mid;
        }
    }
    return true;
}
