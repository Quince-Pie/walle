#ifndef XOSHIRO256PP_H
#define XOSHIRO256PP_H

// based on  xoshiro256++ written by David Blackman and Sebastiano Vigna (vigna@acm.org)
// https://prng.di.unimi.it/xoshiro256plusplus.c

#include <stdbit.h> // C23: For stdc_has_single_bit
#include <stddef.h>
#include <stdint.h>

typedef unsigned _BitInt(128) uint128_t;

typedef struct
{
    uint64_t s[4];
} XoshiroState;

void xoshiro256pp_seed(XoshiroState* state, uint64_t seed);
void xoshiro256pp_jump(XoshiroState* state);
void xoshiro256pp_long_jump(XoshiroState* state);

[[nodiscard("Internal function: result must be returned by the fast path")]]
uint64_t xoshiro256pp_bounded_slow(XoshiroState* rng, uint64_t limit, uint128_t m);

static inline uint64_t rotl(const uint64_t x, int k)
{
    return (x << k) | (x >> (64 - k));
}

static inline uint64_t xoshiro256pp_next(XoshiroState* state)
{
    const uint64_t result = rotl(state->s[0] + state->s[3], 23) + state->s[0];

    const uint64_t t = state->s[1] << 17;

    state->s[2] ^= state->s[0];
    state->s[3] ^= state->s[1];
    state->s[1] ^= state->s[2];
    state->s[0] ^= state->s[3];

    state->s[2] ^= t;

    state->s[3] = rotl(state->s[3], 45);

    return result;
}

static inline uint64_t xoshiro256pp_bounded(XoshiroState* rng, uint64_t limit)
{
    if (limit == 0)
        return 0;
    if (stdc_has_single_bit(limit)) {
        return xoshiro256pp_next(rng) & (limit - 1);
    }

    uint64_t  x = xoshiro256pp_next(rng);
    uint128_t m = (uint128_t)x * (uint128_t)limit;

    uint64_t l = (uint64_t)m;

    if (l < limit) {
        return xoshiro256pp_bounded_slow(rng, limit, m);
    }

    return (uint64_t)(m >> 64);
}

#endif // XOSHIRO256PP_H
