// based on  xoshiro256++ written by David Blackman and Sebastiano Vigna (vigna@acm.org)
// https://prng.di.unimi.it/xoshiro256plusplus.c
#include "shiro.h"

uint64_t xoshiro256pp_bounded_slow(XoshiroState* rng, uint64_t limit, uint128_t m)
{
    const uint64_t t = (-limit) % limit;

    uint64_t l = (uint64_t)m;

    while (l < t) {
        uint64_t x = xoshiro256pp_next(rng);
        m          = (uint128_t)x * (uint128_t)limit;
        l          = (uint64_t)m;
    }

    return (uint64_t)(m >> 64);
}

void xoshiro256pp_seed(XoshiroState* state, uint64_t seed)
{
    for (auto i = 0; i < 4; ++i) {
        uint64_t z  = (seed += 0x9e3779b97f4a7c15);
        z           = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9;
        z           = (z ^ (z >> 27)) * 0x94d049bb133111eb;
        state->s[i] = z ^ (z >> 31);
    }
}

static void transition_internal(XoshiroState* state)
{
    const uint64_t t = state->s[1] << 17;

    state->s[2] ^= state->s[0];
    state->s[3] ^= state->s[1];
    state->s[1] ^= state->s[2];
    state->s[0] ^= state->s[3];

    state->s[2] ^= t;

    state->s[3] = rotl(state->s[3], 45);
}

void xoshiro256pp_jump(XoshiroState* state)
{
    static constexpr uint64_t JUMP[]
        = {0x180ec6d33cfd0aba, 0xd5a61266f0c9392c, 0xa9582618e03fc9aa, 0x39abdc4529b1661c};

    uint64_t s0 = {};
    uint64_t s1 = {};
    uint64_t s2 = {};
    uint64_t s3 = {};

    for (unsigned long int i = 0; i < sizeof JUMP / sizeof *JUMP; i++) {
        for (int b = 0; b < 64; b++) {
            if (JUMP[i] & (UINT64_C(1) << b)) {
                s0 ^= state->s[0];
                s1 ^= state->s[1];
                s2 ^= state->s[2];
                s3 ^= state->s[3];
            }
            transition_internal(state);
        }
    }

    state->s[0] = s0;
    state->s[1] = s1;
    state->s[2] = s2;
    state->s[3] = s3;
}

void xoshiro256pp_long_jump(XoshiroState* state)
{
    static constexpr uint64_t LONG_JUMP[]
        = {0x76e15d3efefdcbbf, 0xc5004e441c522fb3, 0x77710069854ee241, 0x39109bb02acbe635};

    uint64_t s0 = {};
    uint64_t s1 = {};
    uint64_t s2 = {};
    uint64_t s3 = {};

    for (unsigned long int i = 0; i < sizeof LONG_JUMP / sizeof *LONG_JUMP; i++) {
        for (int b = 0; b < 64; b++) {
            if (LONG_JUMP[i] & (UINT64_C(1) << b)) {
                s0 ^= state->s[0];
                s1 ^= state->s[1];
                s2 ^= state->s[2];
                s3 ^= state->s[3];
            }
            transition_internal(state);
        }
    }

    state->s[0] = s0;
    state->s[1] = s1;
    state->s[2] = s2;
    state->s[3] = s3;
}
