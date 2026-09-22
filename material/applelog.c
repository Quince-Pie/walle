/* applelog.c: C twin of Apple libsystem_m `log` (double), macOS 26.6.1 25G76 arm64, unslid
   0x1902b4180. Literal transcription of the disassembly (log_dis.txt, lldb `disassemble -n log`);
   tables applelog_tables.h. Build with -ffp-contract=off: every fused operation of the original is
   an explicit fma(). */
#include <math.h>
#include <stdint.h>
#include <string.h>
#include "applelog_tables.h"
static inline double al_d(uint64_t u)
{
    double d;
    memcpy(&d, &u, 8);
    return d;
}
static inline uint64_t al_u(double d)
{
    uint64_t u;
    memcpy(&u, &d, 8);
    return u;
}

double apple_log(double x)
{
    uint64_t ix = al_u(x);
    if (ix - 0x0010000000000000ULL >= 0x7fe0000000000000ULL) { /* +20..+32: b.hs +248 */
        if (x != x)
            return x;                                          /* +252 b.vs: return d0 */
        if (x == 0.0) {
            volatile double z = 0.0;
            return -1.0 / z;
        } /* +316: -1 / 0 */
        if (x < 0.0) {
            double i = al_d(AL_INF);
            return i - i;
        }                                                  /* +300: inf - inf */
        if ((int64_t)(ix - 0x0010000000000000ULL) >= 0)
            return x;                                      /* +264 tst x2, b.pl: +inf */
        double t = al_d(ix | 0x3ff0000000000000ULL) - 1.0; /* +272..+280: subnormal rescale */
        ix       = al_u(t) + 0xc020000000000000ULL;        /* +284..+292: add -0x3fe0000000000000 */
    }
    int64_t         k    = (int64_t)(AL_C_K_OFFSET + ix) >> 52; /* +36..+44 */
    uint64_t        mant = ix & 0xfffffffffffffULL;             /* +52 */
    uint64_t        idx  = (mant + 0x100000000000ULL) >> 45;    /* +56..+60 */
    double          m    = al_d(mant | 0x3ff0000000000000ULL);  /* +64..+72 */
    const uint64_t* E    = AL_TABLE[idx];
    double          c = al_d(E[0]), hi = al_d(E[1]);
    double          r   = fma(m, c, -1.0); /* +100 fnmsub d1, d0, d2, d1 */
    double          p16 = fma(r, al_d(AL_POLY[1]), al_d(AL_POLY[0])); /* +104 */
    double          p19 = r + al_d(AL_POLY[3]);                       /* +108 */
    double          p21 = r + al_d(AL_POLY[5]);                       /* +112 */
    double          r2  = r * r;                                      /* +116 */
    double          p18 = fma(r, p19, al_d(AL_POLY[2]));              /* +120 */
    double          p20 = fma(r, p21, al_d(AL_POLY[4]));              /* +124 */
    double          q   = r2 * p16;
    q                   = q * p18;
    q                   = q * p20;                /* +128..+136 */
    if (k != 0) {                                 /* +140 cbz x3 */
        double lo = al_d(E[2]);
        double dk = (double)k;                    /* +152 scvtf */
        lo        = fma(dk, al_d(AL_LN2_LO), lo); /* +160 fmadd d2, d0, d7, d2 */
        hi        = fma(dk, al_d(AL_LN2_HI), hi); /* +164 fmadd d3, d0, d6, d3 */
        lo        = lo + q;                       /* +168 */
        double t  = r + lo;                       /* +172 */
        return hi + t;                            /* +176 */
    } else {
        double lo  = al_d(E[2]);                  /* +188 */
        double p   = c * m;                       /* +196 fmul d5, d2, d0 */
        double d6  = p - 1.0;                     /* +204 */
        double e   = fma(c, m, -p);               /* +208 fnmsub d5, d2, d0, d5 */
        double rh  = al_d(al_u(d6) & AL_MASK);    /* +212 and.8b */
        double t   = d6 - rh;                     /* +216 */
        t          = e + t;                       /* +220 */
        double res = rh + hi;                     /* +224 */
        t          = t + lo;                      /* +228 */
        t          = t + q;                       /* +232 */
        return res + t;                           /* +236 */
    }
}
