/* Arithmetic transcription from lg_applepowf.py,lg_trig.py,lg_tintmatrix.py.
 * Compile with -ffp-contract=off; explicit fma/fmaf sites remain fused.
 */
#include "material_math.h"
#include <math.h>
#include <string.h>
#include "powf_tables.h"
#include "trig_tables.h"
#include "log2f_tables.h"
float wm_float(uint32_t u)
{
    float v;
    memcpy(&v, &u, 4);
    return v;
}
uint32_t wm_float_bits(float v)
{
    uint32_t u;
    memcpy(&u, &v, 4);
    return u;
}
double wm_double(uint64_t u)
{
    double v;
    memcpy(&v, &u, 8);
    return v;
}
uint16_t wm_half(float v)
{
    uint32_t u = wm_float_bits(v), sign = (u >> 16) & 0x8000, mag = u & 0x7fffffff;
    if (mag >= 0x7f800000)
        return (uint16_t)(sign | (mag == 0x7f800000 ? 0x7c00 : 0x7e00 | ((mag >> 13) & 0x1ff)));
    int exponent = (int)(mag >> 23) - 127;
    if (exponent > 15)
        return (uint16_t)(sign | 0x7c00);
    if (exponent < -25)
        return (uint16_t)sign;
    if (exponent < -14) {
        uint32_t m     = (mag & 0x7fffff) | 0x800000;
        unsigned shift = (unsigned)(-exponent - 1);
        uint32_t q = m >> shift, rem = m & ((1u << shift) - 1), half = 1u << (shift - 1);
        q += rem > half || (rem == half && (q & 1));
        return (uint16_t)(sign | q);
    }
    uint32_t q = ((uint32_t)(exponent + 15) << 10) | ((mag >> 13) & 0x3ff), rem = mag & 0x1fff;
    q += rem > 0x1000 || (rem == 0x1000 && (q & 1));
    return (uint16_t)(sign | q);
}
float wm_log2f(float x)
{
    /* libsystem_m log2f: finite reduction/polynomial is in Double, followed
     * by one FCVT to Float. Preserve the original special-result words. */
    uint32_t word = wm_float_bits(x), reduced = word - UINT32_C(0x00800000);
    if (reduced >= UINT32_C(0x7f000000)) {
        uint32_t magnitude = word & UINT32_C(0x7fffffff);
        if (magnitude > UINT32_C(0x7f800000))
            return x;
        if (!magnitude)
            return wm_float(UINT32_C(0xff800000));
        if (word & UINT32_C(0x80000000))
            return wm_float(UINT32_C(0x7fc00000));
        if (!(reduced & UINT32_C(0x80000000)))
            return x;
        float scaled = wm_float(word | UINT32_C(0x3f800000)) - 1.f;
        word = wm_float_bits(scaled) - UINT32_C(0x3f000000);
    }
    int32_t exponent = (int32_t)(word + L2_BIAS) >> 23;
    uint32_t mantissa = word & UINT32_C(0x7fffff);
    uint32_t index = (mantissa + UINT32_C(0x10000)) >> 17;
    double residual = fma(wm_float(mantissa | UINT32_C(0x3f800000)),
                          wm_double(L2_TABLE[index][0]), -1.);
    double polynomial = fma(residual, wm_double(L2_POLY[0]), wm_double(L2_POLY[1]));
    polynomial = fma(residual, polynomial, wm_double(L2_POLY[2]));
    polynomial = fma(residual, polynomial, wm_double(L2_POLY[3]));
    return (float)((double)exponent + fma(residual, polynomial, wm_double(L2_TABLE[index][1])));
}
float wm_powf(float x, float y)
{
    uint32_t ix = wm_float_bits(x), iy = wm_float_bits(y), ax = ix & 0x7fffffff,
             ay = iy & 0x7fffffff;
    if (ix == 0x3f800000)
        return x;
    if (iy == 0x3f800000)
        return ax > 0x7f800000 ? wm_float(ix | 0x400000) : x;
    if (!ay)
        return 1;
    if (ay > 0x7f800000)
        return wm_float(iy | 0x400000);
    if (ay == 0x7f800000) {
        if (ax > 0x7f800000)
            return wm_float(ix | 0x400000);
        if (ax == 0x3f800000)
            return 1;
        return ((ax > 0x3f800000) != !!(iy >> 31)) ? INFINITY : 0;
    }
    if (!ax || ax >= 0x7f800000) {
        if (ax > 0x7f800000)
            return wm_float(ix | 0x400000);
        bool     odd  = ay >= 0x3f800000 && ay < 0x4b800000 && y == truncf(y) && ((int)y & 1);
        uint32_t sign = odd ? ix & 0x80000000 : 0;
        uint32_t m    = ax;
        if (iy >> 31)
            m = ax ? 0 : 0x7f800000;
        return wm_float(sign | m);
    }
    uint32_t sign = 0;
    if (ix >> 31) {
        float by = wm_float(ay < 0x4b800000 ? ay : 0x4b800000);
        if (ay < 0x3f800000 || by != truncf(by))
            return wm_float(0x7fc00000);
        sign = ((uint32_t)by & 1) << 31;
        ix   = ax;
    }
    if (ix < 0x800000)
        ix = wm_float_bits(wm_float(ix | 0x3f800000) - 1.f) - 0x3f000000;
    uint32_t tmp = ix - 0x3f338000u, i = (tmp >> 16) & 127, top = tmp & 0xff800000, iz = ix - top;
    int32_t  k  = (int32_t)top >> 16;
    double   r  = fma(wm_float(iz), wm_double(LOGT[2 * i]), -1.);
    double   y0 = wm_double(LOGT[2 * i + 1]) + (double)k, r2 = r * r;
    double   p = fma(r, wm_double(PA[0]), wm_double(PA[1]));
    double   q = fma(r, wm_double(PA[2]), wm_double(PA[3]));
    p          = fma(p, r2, q);
    double l = fma(p, r, y0), yl = (double)y * l;
    yl         = fmin(yl, wm_double(PB[0]));
    yl         = fmax(yl, wm_double(PB[1]));
    int64_t ki = (int64_t)round(yl);
    double  rr = yl - (double)ki;
    double  s  = wm_double(EXPT[(uint64_t)ki & 127] + ((uint64_t)ki << 45));
    double  pe = fma(wm_double(PC[0]), rr, wm_double(PC[1]));
    pe *= rr;
    return wm_float(wm_float_bits((float)fma(pe, s, s)) ^ sign);
}
void wm_sincosf(float angle, float* sn, float* cs)
{
    uint32_t mag = wm_float_bits(angle) & 0x7fffffff;
    int64_t  quadrant;
    double   r;
    if (mag >= TS[1]) {
        if (mag >= TS[0]) {
            *sn = *cs = angle - angle;
            return;
        }
        double   d  = (double)angle + (double)angle;
        unsigned i  = (mag >> 24) - 0x40;
        double   hi = d * wm_double(TR[2 * i]), lo = d * wm_double(TR[2 * i + 1]);
        quadrant = (int64_t)nearbyint(hi);
        r        = (hi - (double)quadrant) + lo;
    } else if (mag < TS[4]) {
        float scale = wm_float(TS[6]), inverse = wm_float(TS[5]);
        *cs = (scale - fabsf(angle)) * inverse;
        *sn = fmaf(angle, scale, angle) * inverse;
        return;
    } else {
        double ratio = (double)angle * wm_double((uint64_t)TS[7] | ((uint64_t)TS[8] << 32));
        quadrant     = (int64_t)nearbyint(ratio);
        r            = ratio - (double)quadrant;
    }
    double z = r * r, p0 = fma(z, z + wm_double(TP[0]), wm_double(TP[6])),
           p1 = fma(z, z + wm_double(TP[1]), wm_double(TP[7]));
    double q0 = fma(z, z + wm_double(TP[2]), wm_double(TP[8])),
           q1 = fma(z, z + wm_double(TP[3]), wm_double(TP[9]));
    float s   = (float)(((r * wm_double(TP[4])) * p0) * q0),
          c   = (float)((wm_double(TP[5]) * p1) * q1);
    if (quadrant & 2) {
        s = -s;
        c = -c;
    }
    if (quadrant & 1) {
        float t = s;
        s       = c;
        c       = -t;
    }
    *sn = s;
    *cs = c;
}
static double scalar_cos(double x)
{
    double z = x * x, p = fma(z, wm_double(TC[2]), wm_double(TC[3])),
           q = fma(z, wm_double(TC[4]), wm_double(TC[5]));
    return fma(z, fma(z * z, p, q), 1.);
}
static double scalar_sin(double x)
{
    double z = x * x, c = x * z, p = fma(z, wm_double(TC[6]), wm_double(TC[7])),
           q = fma(c, wm_double(TC[8]), x);
    return fma(z * c, p, q);
}
float wm_cosf(float angle)
{
    angle      = fabsf(angle);
    uint32_t m = wm_float_bits(angle);
    if (m >= TS[0])
        return angle - angle;
    if (m >= TS[2]) {
        unsigned i  = (m >> 24) - 0x40;
        double   hi = (double)angle * wm_double(TR[2 * i]) + .5,
               lo   = (double)angle * wm_double(TR[2 * i + 1]);
        int64_t n   = (int64_t)nearbyint(hi);
        double  r = (hi - (double)n) + lo, z = r * r,
               p = fma(z, z + wm_double(TC[9]), wm_double(TC[11])),
               q = fma(z, z + wm_double(TC[10]), wm_double(TC[12]));
        return (float)(((n & 1 ? -r : r) * p) * (wm_double(TC[13]) * q));
    }
    if (m < TS[4])
        return (wm_float(TS[6]) - angle) * wm_float(TS[5]);
    if (m < TS[3])
        return (float)scalar_cos(angle);
    int64_t n
        = (int64_t)nearbyint((double)angle * wm_double((uint64_t)TS[7] | ((uint64_t)TS[8] << 32)));
    double r = fma(-(double)n, wm_double(TC[0]), (double)angle);
    if (m >= TS[1])
        r = fma(-(double)n, wm_double(TC[1]), r);
    int64_t q      = n + 1;
    double  result = q & 1 ? scalar_cos(r) : scalar_sin(r);
    return (float)(q & 2 ? -result : result);
}
float wm_srgb_decode(float x)
{
    float a = x > 0 ? x : -x, y;
    if (a <= wm_float(0x3d25aee6))
        y = a * wm_float(0x3d9e8391);
    else if (a == 1)
        y = a;
    else
        y = wm_powf(a * wm_float(0x3f72a76f) + wm_float(0x3d55891a), wm_float(0x4019999a));
    return x > 0 ? y : -y;
}
float wm_srgb_encode(float x)
{
    float a = x > 0 ? x : -x, y;
    if (a <= wm_float(0x3b4d2e1c))
        y = a * wm_float(0x414eb852);
    else if (a == 1)
        y = a;
    else
        y = wm_powf(a, wm_float(0x3ed55555)) * wm_float(0x3f870a3d) + wm_float(0xbd6147ae);
    return x > 0 ? y : -y;
}
static void identity(float m[20])
{
    memset(m, 0, 80);
    m[0] = m[6] = m[12] = m[18] = 1;
}
void wm_matrix_multiply(const float a[20], const float b[20], float out[20])
{
    float c[20];
    for (unsigned i = 0; i < 4; i++)
        for (unsigned j = 0; j < 5; j++) {
            float t
                = ((a[5 * i + 3] * b[15 + j] + a[5 * i + 2] * b[10 + j]) + a[5 * i + 1] * b[5 + j])
                  + a[5 * i] * b[j];
            if (j == 4)
                t += a[5 * i + 4];
            c[5 * i + j] = t;
        }
    memcpy(out, c, 80);
}
static void luma(int inverse, float black, float white, float m[20])
{
    if (white == 1 && black == 0)
        return;
    float l[20];
    identity(l);
    float d = white - black;
    l[0]    = inverse ? d : d == 0 ? wm_float(0x34000000) : 1.f / d;
    l[4]    = inverse ? black : -(l[0] * black);
    wm_matrix_multiply(l, m, m);
}
static void saturation(float sat, float m[20])
{
    if (sat == 1)
        return;
    float s[20];
    identity(s);
    s[6] = s[12] = sat;
    s[9] = s[14] = .5f - sat * .5f;
    wm_matrix_multiply(s, m, m);
}
void wm_ycc_adjust(int inverse, float black, float white, float sat, float m[20])
{
    static const uint32_t ybits[20]
        = {0x3e59b3d0, 0x3f371759, 0x3d93dd98, 0,          0,          0xbdeab368, 0xbec55326,
           0x3f000000, 0,          0x3f000000, 0x3f000000, 0xbee88ce7, 0xbd3b98c8, 0,
           0x3f000000, 0,          0,          0,          0x3f800000, 0};
    static const uint32_t ibits[20]
        = {0x3f800000, 0, 0x3fc9930c, 0,          0xbf49930c, 0x3f800000, 0xbe3fcb92,
           0xbeefaace, 0, 0x3ea7c84b, 0x3f800000, 0x3fed844d, 0,          0,
           0xbf6d844d, 0, 0,          0,          0x3f800000, 0};
    identity(m);
    if (white == 1 && black == 0 && sat == 1)
        return;
    float y[20], iv[20];
    for (unsigned i = 0; i < 20; i++) {
        y[i]  = wm_float(ybits[i]);
        iv[i] = wm_float(ibits[i]);
    }
    wm_matrix_multiply(y, m, m);
    if (inverse) {
        luma(1, black, white, m);
        saturation(sat, m);
    } else {
        saturation(sat, m);
        luma(0, black, white, m);
    }
    wm_matrix_multiply(iv, m, m);
}
static void rgb_hsl(const float rgb[3], double* h, float* sat, float* light)
{
    float r = wm_srgb_encode(rgb[0]), g = wm_srgb_encode(rgb[1]), b = wm_srgb_encode(rgb[2]);
    float t = g < r ? g : r, mn = b < t ? b : t;
    t        = r <= g ? g : r;
    float mx = t <= b ? b : t, sum = mx + mn, L = sum * .5f, H = 0, S = 0;
    if (!(fabsf(mx - mn) < wm_float(0x39b504f3))) {
        float d = mx - mn, den = L > .5f ? (2.f - mx) - mn : sum;
        S = d / den;
        if (mx == r)
            H = (g < b ? 6.f : 0.f) + (g - b) / d;
        else if (mx == g)
            H = (b - r) / d + 2.f;
        else
            H = (r - g) / d + 4.f;
    }
    *h     = (double)(H * 60.f) * wm_double(0x3f91df46a2529d39);
    *sat   = S;
    *light = L;
}
static float hue_rgb(float p, float q, float t)
{
    if (t < 0)
        t += 1;
    if (t > 1)
        t -= 1;
    if (t < wm_float(0x3e2aaaab))
        return p + ((q - p) * 6.f) * t;
    if (t < .5f)
        return q;
    if (t < wm_float(0x3f2aaaab))
        return p + ((q - p) * (wm_float(0x3f2aaaab) - t)) * 6.f;
    return p;
}
static void hsl_rgb(double hue, float sat, float light, float c[4])
{
    float r, g, b;
    if (sat == 0)
        r = g = b = light;
    else {
        float h = (float)((hue * wm_double(0x404ca5dc1a63c1f8)) / 360.);
        float q = light < .5f ? light * (sat + 1.f) : (sat + light) - sat * light,
              p = (light + light) - q;
        r       = hue_rgb(p, q, h + wm_float(0x3eaaaaab));
        g       = hue_rgb(p, q, h);
        b       = hue_rgb(p, q, h + wm_float(0xbeaaaaab));
        r       = fminf(fmaxf(r, 0), 1);
        g       = fminf(fmaxf(g, 0), 1);
        b       = fminf(fmaxf(b, 0), 1);
    }
    c[0] = wm_srgb_decode(r);
    c[1] = wm_srgb_decode(g);
    c[2] = wm_srgb_decode(b);
    c[3] = 1;
}
static void colorize(const float a[4], const float b[4], float m[20])
{
    float t0 = 1.f - b[3], k0 = 1.f - t0, z0 = t0 * 0.f, t1 = 1.f - a[3], k1 = 1.f - t1,
          out[20] = {0};
    for (unsigned i = 0; i < 3; i++) {
        float lo = z0 + k0 * wm_srgb_encode(b[i]), hi = t1 + k1 * wm_srgb_encode(a[i]);
        out[6 * i]     = hi - lo;
        out[5 * i + 4] = lo;
    }
    out[18] = 1;
    wm_matrix_multiply(out, m, m);
}
void wm_tint_matrix(const uint8_t rgba[4], bool dark, bool active, float opacity, float m[20])
{
    float c[4];
    for (unsigned i = 0; i < 3; i++)
        c[i] = wm_srgb_decode((float)((double)rgba[i] / 255.));
    c[3] = (float)((double)rgba[3] / 255.);
    if (!active)
        wm_ycc_adjust(1,
                      dark ? wm_float(0x3dcccccd) : wm_float(0xbdcccccd),
                      dark ? wm_float(0x3f8ccccd) : wm_float(0x3f666666),
                      wm_float(0x3f333333),
                      m);
    else {
        double hue;
        float  S, L;
        rgb_hsl(c, &hue, &S, &L);
        if (S == 0) {
            float lo = L * wm_float(0x3f733333) + (1.f - L) * 0.f,
                  hi = L + (1.f - L) * wm_float(0x3dcccccd);
            wm_ycc_adjust(1, lo, hi, 1, m);
        } else {
            float c0[4], c1[4];
            if (!dark) {
                memcpy(c1, c, 16);
                c1[3] = 1;
                hsl_rgb(hue, S * wm_float(0x3f99999a), L * wm_float(0x3f19999a), c0);
            } else {
                hsl_rgb(hue, S * wm_float(0x3f733333), L * wm_float(0x3f8ccccd), c1);
                memcpy(c0, c, 16);
                c0[3] = 1;
            }
            wm_ycc_adjust(0, wm_float(0x3e19999a), dark ? .75f : 1.f, 0, m);
            colorize(c1, c0, m);
        }
    }
    m[18] = (c[3] * opacity) * m[18];
}

void wm_ca_color_matrix_concat(const float a[20], const float b[20], float out[20])
{
    /* CA::concat @18a77f2b4..18a77f4f8. Preserve each emitted lane order. */
    float c[20];
    c[0] = fmaf(b[0], a[0], fmaf(b[5], a[1], fmaf(b[10], a[2], (b[15] * a[3]))));
    c[1] = fmaf(b[1], a[0], fmaf(b[6], a[1], fmaf(b[11], a[2], (b[16] * a[3]))));
    c[2] = fmaf(b[2], a[0], fmaf(b[7], a[1], fmaf(b[12], a[2], (b[17] * a[3]))));
    c[3] = fmaf(b[3], a[0], fmaf(b[8], a[1], fmaf(b[13], a[2], (b[18] * a[3]))));
    c[4] = fmaf(b[4], a[0], fmaf(b[9], a[1], fmaf(b[14], a[2], fmaf(b[19], a[3], a[4]))));
    c[5] = fmaf(b[0], a[5], fmaf(a[6], b[5], fmaf(a[7], b[10], (a[8] * b[15]))));
    c[6] = fmaf(b[1], a[5], fmaf(b[6], a[6], fmaf(b[11], a[7], (b[16] * a[8]))));
    c[7] = fmaf(b[2], a[5], fmaf(a[6], b[7], fmaf(b[12], a[7], (b[17] * a[8]))));
    c[8] = fmaf(b[3], a[5], fmaf(a[7], b[13], fmaf(b[8], a[6], (b[18] * a[8]))));
    c[9] = fmaf(b[4], a[5], fmaf(a[8], b[19], fmaf(b[9], a[6], fmaf(b[14], a[7], a[9]))));
    c[10] = fmaf(a[10], b[0], fmaf(b[5], a[11], fmaf(a[12], b[10], (a[13] * b[15]))));
    c[11] = fmaf(a[10], b[1], fmaf(b[6], a[11], fmaf(b[11], a[12], (b[16] * a[13]))));
    c[12] = fmaf(b[7], a[11], fmaf(b[2], a[10], fmaf(b[12], a[12], (b[17] * a[13]))));
    c[13] = fmaf(b[8], a[11], fmaf(a[12], b[13], fmaf(b[3], a[10], (b[18] * a[13]))));
    c[14] = fmaf(b[9], a[11], fmaf(a[13], b[19], fmaf(b[4], a[10], fmaf(b[14], a[12], a[14]))));
    c[15] = fmaf(a[15], b[0], fmaf(a[16], b[5], fmaf(b[10], a[17], (a[18] * b[15]))));
    c[16] = fmaf(a[15], b[1], fmaf(b[11], a[17], fmaf(b[6], a[16], (b[16] * a[18]))));
    c[17] = fmaf(a[16], b[7], fmaf(b[12], a[17], fmaf(b[2], a[15], (b[17] * a[18]))));
    c[18] = fmaf(b[13], a[17], fmaf(b[8], a[16], fmaf(b[3], a[15], (b[18] * a[18]))));
    c[19] = fmaf(b[14], a[17], fmaf(a[18], b[19], fmaf(b[9], a[16], fmaf(b[4], a[15], a[19]))));
    memcpy(out, c, sizeof c);
}

void wm_ca_ycc_composite(float white, float black, float saturation, const float fill[4], float out[20])
{
    static const float m1[20] = {.212599993f,
                                 .715200007f,
                                 .0722000003f,
                                 0,
                                 0,
                                 -.114600003f,
                                 -.385399997f,
                                 .5f,
                                 0,
                                 .5f,
                                 .5f,
                                 -.4542f,
                                 -.0458000004f,
                                 0,
                                 .5f,
                                 0,
                                 0,
                                 0,
                                 1,
                                 0};
    static const float m2[20] = {1,
                                 0,
                                 1.57480001f,
                                 0,
                                 -.787400007f,
                                 1,
                                 -.187324002f,
                                 -.468124002f,
                                 0,
                                 .32772401f,
                                 1,
                                 1.8556f,
                                 0,
                                 0,
                                 -.9278f,
                                 0,
                                 0,
                                 0,
                                 1,
                                 0};
    float              a[20], b[20], c[20];
    memset(a, 0, sizeof a);
    memset(b, 0, sizeof b);
    a[0] = a[6] = a[12] = a[18] = 1.f;
    b[0] = b[6] = b[12] = b[18] = 1.f;
    a[0] = white - black;
    a[4] = black;
    b[6] = b[12] = saturation;
    b[9] = b[14] = (float)fma(-(double)saturation, .5, .5);
    wm_ca_color_matrix_concat(a, m1, c);
    wm_ca_color_matrix_concat(b, c, c);
    wm_ca_color_matrix_concat(m2, c, c);
    float k = 1.f - fill[3];
    for (unsigned i = 0; i < 20; i++)
        c[i] *= k;
    c[4] += fill[0];
    c[9] += fill[1];
    c[14] += fill[2];
    c[18] += fill[3];
    memcpy(out, c, sizeof c);
}
