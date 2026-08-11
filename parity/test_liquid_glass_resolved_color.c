#include "liquid_glass_resolved_color.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static constexpr unsigned char fixture[] = {
#embed "resolved_color_v1_fixture.bin"
};

static uint32_t load_u32(const unsigned char *bytes)
{
    return (uint32_t)bytes[0]
        | (uint32_t)bytes[1] << 8
        | (uint32_t)bytes[2] << 16
        | (uint32_t)bytes[3] << 24;
}

static uint64_t load_u64(const unsigned char *bytes)
{
    return (uint64_t)load_u32(bytes) | (uint64_t)load_u32(bytes + 4) << 32;
}

static float float_from_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static double double_from_bits(uint64_t bits)
{
    double value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static uint32_t float_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static struct walle_lg_resolved_color load_color(const unsigned char *bytes)
{
    struct walle_lg_resolved_color result;
    for (size_t index = 0; index < 4; ++index) {
        result.linear_rgba[index] = float_from_bits(load_u32(bytes + 4 * index));
    }
    return result;
}

static bool compare_components(
    const float actual[static 4],
    const unsigned char *expected,
    uint32_t sample,
    const char *stage,
    uint32_t *comparisons,
    uint32_t *mismatches
)
{
    bool exact = true;
    for (size_t index = 0; index < 4; ++index) {
        uint32_t expected_bits = load_u32(expected + 4 * index);
        uint32_t actual_bits = float_bits(actual[index]);
        ++*comparisons;
        if (actual_bits != expected_bits) {
            if (*mismatches < 16) {
                fprintf(stderr,
                        "resolved-color sample %u %s[%zu]: expected %08x, got %08x\n",
                        sample,
                        stage,
                        index,
                        expected_bits,
                        actual_bits);
            }
            ++*mismatches;
            exact = false;
        }
    }
    return exact;
}

int main(void)
{
    static constexpr unsigned char magic[] = "WLGRCV1";
    static constexpr size_t header_size = 24;
    static constexpr size_t record_size = 104;
    static constexpr uint32_t expected_samples = 205;

    if (sizeof(fixture) != header_size + record_size * expected_samples
        || memcmp(fixture, magic, sizeof(magic)) != 0
        || load_u32(fixture + 8) != 1
        || load_u32(fixture + 12) != expected_samples
        || load_u32(fixture + 16) != record_size) {
        fputs("resolved-color fixture contract differs\n", stderr);
        return 1;
    }

    uint32_t comparisons = 0;
    uint32_t mismatches = 0;
    for (uint32_t sample = 0; sample < expected_samples; ++sample) {
        const unsigned char *record = fixture + header_size + record_size * sample;
        struct walle_lg_resolved_color from = load_color(record);
        struct walle_lg_resolved_color to = load_color(record + 16);
        double fraction = double_from_bits(load_u64(record + 32));
        float from_public[4];
        float to_public[4];
        struct walle_lg_resolved_color mixed;
        if (!walle_lg_resolved_color_public_components(&from, from_public)
            || !walle_lg_resolved_color_public_components(&to, to_public)
            || !walle_lg_mix_resolved_color(&from, &to, fraction, &mixed)) {
            fprintf(stderr, "resolved-color sample %u was rejected\n", sample);
            return 1;
        }
        (void)compare_components(
            from_public, record + 40, sample, "from-public", &comparisons, &mismatches
        );
        (void)compare_components(
            to_public, record + 56, sample, "to-public", &comparisons, &mismatches
        );

        float expected_mixed_public[4];
        float from_weight = (float)(1.0 - fraction);
        float to_weight = (float)fraction;
        for (size_t index = 0; index < 4; ++index) {
            volatile float left = from_public[index] * from_weight;
            volatile float right = to_public[index] * to_weight;
            volatile float sum = left + right;
            expected_mixed_public[index] = sum;
        }
        (void)compare_components(
            expected_mixed_public,
            record + 72,
            sample,
            "mixed-public",
            &comparisons,
            &mismatches
        );
        (void)compare_components(
            mixed.linear_rgba,
            record + 88,
            sample,
            "output-raw",
            &comparisons,
            &mismatches
        );
    }

    printf("resolved-color transfer: %u/%u exact words\n",
           comparisons - mismatches,
           comparisons);
    return mismatches == 0 ? 0 : 1;
}
