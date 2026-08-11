#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

#include "liquid_glass_pyramid.h"

struct fixture_case
{
    uint32_t sample;
    uint32_t fraction_bits;
    uint32_t producer_width;
    uint32_t producer_height;
    uint32_t texture_index;
    uint32_t base_width;
    uint32_t base_height;
};

static const struct fixture_case cases[] = {
    {1, UINT32_C(0x3f77e0c0), 256, 256, 0, 512, 512},
    {4, UINT32_C(0x3f5fdaa0), 320, 320, 0, 640, 512},
    {8, UINT32_C(0x3f3f9a60), 384, 384, 0, 640, 640},
    {12, UINT32_C(0x3f1fd910), 448, 448, 1, 640, 768},
    {16, UINT32_C(0x3eff9040), 512, 512, 1, 768, 768},
    {20, UINT32_C(0x3ebf4960), 576, 576, 1, 896, 768},
    {24, UINT32_C(0x3e7eb3c0), 640, 640, 1, 768, 768},
    {28, UINT32_C(0x3dfdab00), 704, 704, 1, 704, 704},
};

static bool load_file(const char* path, void* destination, size_t size)
{
    FILE* stream = fopen(path, "rb");
    if (stream == nullptr) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        return false;
    }
    bool success = fread(destination, 1, size, stream) == size && fgetc(stream) == EOF;
    if (!success)
        fprintf(stderr, "%s: unexpected byte count\n", path);
    if (fclose(stream) != 0) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        success = false;
    }
    return success;
}

static unsigned char* load_allocated_file(const char* path, size_t size)
{
    unsigned char* result = malloc(size);
    if (result == nullptr || !load_file(path, result, size)) {
        free(result);
        return nullptr;
    }
    return result;
}

static bool load_compressed(const char* path, void* destination, size_t size)
{
    FILE* stream = fopen(path, "rb");
    if (stream == nullptr) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        return false;
    }
    if (fseek(stream, 0, SEEK_END) != 0) {
        fclose(stream);
        return false;
    }
    long compressed_size = ftell(stream);
    if (compressed_size <= 0 || fseek(stream, 0, SEEK_SET) != 0) {
        fclose(stream);
        return false;
    }
    unsigned char* compressed = malloc((size_t)compressed_size);
    if (compressed == nullptr
        || fread(compressed, 1, (size_t)compressed_size, stream) != (size_t)compressed_size
        || fclose(stream) != 0) {
        free(compressed);
        return false;
    }
    uLongf output_size = size;
    int    status      = uncompress(destination, &output_size, compressed, (uLong)compressed_size);
    free(compressed);
    if (status != Z_OK || output_size != size) {
        fprintf(stderr, "%s: zlib payload differs\n", path);
        return false;
    }
    return true;
}

static size_t compare_payload(const unsigned char* candidate,
                              const unsigned char* expected,
                              size_t               size,
                              const char*          label,
                              uint32_t             sample)
{
    size_t mismatches = 0;
    for (size_t byte = 0; byte < size; ++byte) {
        if (candidate[byte] == expected[byte])
            continue;
        if (mismatches < 8) {
            fprintf(stderr,
                    "sample %02" PRIu32 " %s byte %zu: got %02x, expected %02x\n",
                    sample,
                    label,
                    byte,
                    candidate[byte],
                    expected[byte]);
        }
        ++mismatches;
    }
    return mismatches;
}

static size_t compare_active_producer(const struct walle_lg_pyramid_level* candidate,
                                      const unsigned char*                 expected,
                                      uint32_t                             active_width,
                                      uint32_t                             active_height,
                                      uint32_t                             sample,
                                      size_t*                              checked)
{
    size_t mismatches = 0;
    size_t row_bytes  = (size_t)active_width * 4u;
    for (uint32_t y = 0; y < active_height; ++y) {
        const unsigned char* actual_row   = candidate->bgra8 + (size_t)y * candidate->width * 4u;
        const unsigned char* expected_row = expected + (size_t)y * candidate->width * 4u;
        char                 label[64];
        snprintf(label, sizeof label, "producer row %" PRIu32, y);
        mismatches += compare_payload(actual_row, expected_row, row_bytes, label, sample);
        *checked += row_bytes;
    }
    return mismatches;
}

static bool artifact_path(char        result[static 4096],
                          const char* root,
                          const char* format,
                          uint32_t    sample,
                          uint32_t    texture_index,
                          uint32_t    width,
                          uint32_t    height,
                          uint32_t    level)
{
    int count;
    if (level == 0) {
        count = snprintf(result,
                         4096,
                         "%s/sdf-generator-transition-background-uniform-%02" PRIu32
                         "-texture-%03" PRIu32 "-pf80-%" PRIu32 "x%" PRIu32 ".raw",
                         root,
                         sample,
                         texture_index,
                         width,
                         height);
    } else {
        count
            = snprintf(result,
                       4096,
                       "%s/sdf-generator-transition-background-uniform-%02" PRIu32
                       "-texture-%03" PRIu32 "-pf80-%" PRIu32 "x%" PRIu32 "-mip-%02" PRIu32 ".raw",
                       root,
                       sample,
                       texture_index,
                       width,
                       height,
                       level);
    }
    (void)format;
    return count >= 0 && count < 4096;
}

int main(int argc, char** argv)
{
    const char* artifact_root
        = argc > 1 ? argv[1] : "artifacts/local-walle-regular-controlled-backdrop-1cd9af4-run1-v1";
    const char*      calibration_root = argc > 2 ? argv[2] : "lg-test/Analysis";
    constexpr size_t selector_count   = 2'097'153;
    uint32_t*        selectors        = malloc(selector_count * sizeof(*selectors));
    char             path[4096];
    if (selectors == nullptr
        || snprintf(path,
                    sizeof path,
                    "%s/raster_fractional_subpixel_resolved_selectors.zlib",
                    calibration_root)
               < 0
        || !load_compressed(path, selectors, selector_count * sizeof(*selectors))) {
        free(selectors);
        return 1;
    }
    struct walle_lg_raster_calibration calibration = {
        .base_selectors      = selectors,
        .base_selector_count = selector_count,
    };

    constexpr uint32_t source_width  = 1024;
    constexpr uint32_t source_height = 1024;
    constexpr size_t   source_bytes  = (size_t)source_width * source_height * 4u;
    int                input_count   = snprintf(path,
                               sizeof path,
                               "%s/transition-background-uniform-01-dynamic-backdrop-"
                                                "producer-input-0-bgra8.raw",
                               artifact_root);
    unsigned char*     source        = input_count >= 0 && input_count < (int)sizeof path
                                           ? load_allocated_file(path, source_bytes)
                                           : nullptr;
    if (source == nullptr) {
        free(selectors);
        return 1;
    }

    size_t producer_checked = 0;
    size_t pyramid_checked  = 0;
    size_t mismatches       = 0;
    size_t built_cases      = 0;
    for (size_t case_index = 0; case_index < sizeof cases / sizeof cases[0]; ++case_index) {
        const struct fixture_case* fixture = &cases[case_index];
        float                      fraction;
        memcpy(&fraction, &fixture->fraction_bits, sizeof fraction);
        struct walle_lg_transition_frame_request request = {
            .material             = WALLE_LG_MATERIAL_REGULAR,
            .appearance           = WALLE_LG_APPEARANCE_DARK,
            .window_width         = source_width,
            .window_height        = source_height,
            .diameter             = 480,
            .center_x             = 512.0,
            .center_y             = 512.0,
            .visible_fraction     = fraction,
            .sdf_enclosure_radius = 0x1.53b608p+5,
        };
        struct walle_lg_transition_frame frame;
        if (!walle_lg_transition_frame_construct(&request, &frame)) {
            fprintf(stderr, "sample %02" PRIu32 ": frame construction failed\n", fixture->sample);
            ++mismatches;
            continue;
        }
        struct walle_lg_dynamic_regular_backdrop backdrop;
        if (!walle_lg_build_dynamic_regular_backdrop(source,
                                                     source_bytes,
                                                     source_width,
                                                     source_height,
                                                     &frame,
                                                     &calibration,
                                                     &backdrop)) {
            fprintf(
                stderr, "sample %02" PRIu32 ": backdrop construction failed\n", fixture->sample);
            ++mismatches;
            continue;
        }
        ++built_cases;
        if (backdrop.producer.width != fixture->producer_width
            || backdrop.producer.height != fixture->producer_height
            || backdrop.pyramid.levels[0].width != fixture->base_width
            || backdrop.pyramid.levels[0].height != fixture->base_height) {
            fprintf(stderr, "sample %02" PRIu32 ": backdrop extent differs\n", fixture->sample);
            ++mismatches;
            walle_lg_destroy_dynamic_regular_backdrop(&backdrop);
            continue;
        }

        int            producer_count = snprintf(path,
                                      sizeof path,
                                      "%s/transition-background-uniform-%02" PRIu32
                                      "-dynamic-backdrop-producer-output-0-bgra8.raw",
                                      artifact_root,
                                      fixture->sample);
        unsigned char* expected_producer
            = producer_count >= 0 && producer_count < (int)sizeof path
                  ? load_allocated_file(path, backdrop.producer.byte_count)
                  : nullptr;
        if (expected_producer == nullptr) {
            ++mismatches;
            walle_lg_destroy_dynamic_regular_backdrop(&backdrop);
            continue;
        }
        mismatches += compare_active_producer(&backdrop.producer,
                                              expected_producer,
                                              frame.producer.active_extent[0],
                                              frame.producer.active_extent[1],
                                              fixture->sample,
                                              &producer_checked);
        free(expected_producer);

        for (uint32_t level = 0; level < backdrop.pyramid.level_count; ++level) {
            const struct walle_lg_pyramid_level* candidate = &backdrop.pyramid.levels[level];
            if (!artifact_path(path,
                               artifact_root,
                               "",
                               fixture->sample,
                               fixture->texture_index,
                               fixture->base_width,
                               fixture->base_height,
                               level)) {
                ++mismatches;
                break;
            }
            unsigned char* expected = load_allocated_file(path, candidate->byte_count);
            if (expected == nullptr) {
                ++mismatches;
                break;
            }
            char label[64];
            snprintf(label, sizeof label, "pyramid level %" PRIu32, level);
            mismatches += compare_payload(
                candidate->bgra8, expected, candidate->byte_count, label, fixture->sample);
            pyramid_checked += candidate->byte_count;
            free(expected);
        }
        walle_lg_destroy_dynamic_regular_backdrop(&backdrop);
    }

    free(source);
    free(selectors);
    printf("dynamicBackdropCases=%zu\n", built_cases);
    printf("checkedProducerBytes=%zu\n", producer_checked);
    printf("checkedPyramidBytes=%zu\n", pyramid_checked);
    printf("mismatchedBytes=%zu\n", mismatches);
    printf("exact=%s\n",
           mismatches == 0 && built_cases == sizeof cases / sizeof cases[0] ? "true" : "false");
    return mismatches == 0 && built_cases == sizeof cases / sizeof cases[0] ? 0 : 1;
}
