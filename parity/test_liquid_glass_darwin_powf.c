#include "liquid_glass_darwin_powf.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct test_case {
    uint32_t base_bits;
    uint32_t expected_bits;
};

static float float_from_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static uint32_t float_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

int main(void)
{
    static constexpr struct test_case cases[] = {
        { UINT32_C(0x3f919b07), UINT32_C(0x3fae650b) },
        { UINT32_C(0x3f89ab32), UINT32_C(0x3f9871b5) },
    };

    float result;
    if (walle_lg_darwin_powf_2_4(0.0f, &result)
        || walle_lg_darwin_powf_2_4(-1.0f, &result)
        || walle_lg_darwin_powf_2_4(1.0f, nullptr)) {
        fputs("Darwin powf domain contract differs\n", stderr);
        return 1;
    }

    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); ++index) {
        if (!walle_lg_darwin_powf_2_4(
                float_from_bits(cases[index].base_bits), &result
            )
            || float_bits(result) != cases[index].expected_bits) {
            fprintf(
                stderr,
                "Darwin powf case %zu: expected %08x, got %08x\n",
                index,
                cases[index].expected_bits,
                float_bits(result)
            );
            return 1;
        }
    }

    printf("Darwin powf calibration sentinels: %zu/%zu exact words\n",
           sizeof(cases) / sizeof(cases[0]),
           sizeof(cases) / sizeof(cases[0]));
    return 0;
}
