#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "liquid_glass_transition_frame.h"

struct producer_fixture
{
    uint32_t                    sample;
    uint32_t                    fraction_bits;
    uint32_t                    scale_bits;
    enum walle_lg_producer_kind kind;
    int32_t                     working_crop[4];
    int32_t                     visible_crop[4];
    int32_t                     scissor[4];
    uint32_t                    vertex_count;
    uint32_t                    index_count;
    uint32_t                    position_bits[8];
    uint32_t                    source_bits[8];
};

static const struct producer_fixture fixtures[] = {
    {
        .sample        = 1,
        .fraction_bits = UINT32_C(0x3f77e0c0),
        .scale_bits    = UINT32_C(0x3e8c2ee0),
        .kind          = WALLE_LG_PRODUCER_DOWNSAMPLE_4,
        .working_crop  = {110, 95, 819, 819},
        .visible_crop  = {110, 95, 819, 819},
        .scissor       = {0, 0, 239, 239},
        .vertex_count  = 4,
        .index_count   = 6,
        .position_bits = {
            0x41f00000, 0x41d00000, 0x437f0000, 0x41d00000,
            0x437f0000, 0x437b0000, 0x41f00000, 0x437b0000,
        },
        .source_bits = {
            0x42db2457, 0x42bdec4c, 0x4468d69d, 0x42bdec4c,
            0x4468d69d, 0x44652f9b, 0x42db2457, 0x44652f9b,
        },
    },
    {
        .sample        = 4,
        .fraction_bits = UINT32_C(0x3f5fdaa0),
        .scale_bits    = UINT32_C(0x3eb03810),
        .kind          = WALLE_LG_PRODUCER_DOWNSAMPLE_4,
        .working_crop  = {133, 72, 819, 819},
        .visible_crop  = {133, 72, 819, 819},
        .scissor       = {0, 0, 297, 297},
        .vertex_count  = 4,
        .index_count   = 6,
        .position_bits = {
            0x42340000, 0x41c00000, 0x43a40000, 0x41c00000,
            0x43a40000, 0x43998000, 0x42340000, 0x43998000,
        },
        .source_bits = {
            0x4302bf14, 0x428b767c, 0x446e3fbf, 0x428b767c,
            0x446e3fbf, 0x445efec9, 0x4302bf14, 0x445efec9,
        },
    },
    {
        .sample        = 8,
        .fraction_bits = UINT32_C(0x3f3f9a60),
        .scale_bits    = UINT32_C(0x3ee09870),
        .kind          = WALLE_LG_PRODUCER_DOWNSAMPLE_4,
        .working_crop  = {162, 42, 820, 820},
        .visible_crop  = {162, 42, 820, 820},
        .scissor       = {0, 0, 374, 374},
        .vertex_count  = 4,
        .index_count   = 6,
        .position_bits = {
            0x428e0000, 0x41900000, 0x43d78000, 0x41900000,
            0x43d78000, 0x43bd8000, 0x428e0000, 0x43bd8000,
        },
        .source_bits = {
            0x4321daff, 0x42242296, 0x4475a1fc, 0x42242296,
            0x4475a1fc, 0x4457ff4b, 0x4321daff, 0x4457ff4b,
        },
    },
    {
        .sample        = 12,
        .fraction_bits = UINT32_C(0x3f1fd910),
        .scale_bits    = UINT32_C(0x3f081d34),
        .kind          = WALLE_LG_PRODUCER_DIRECT,
        .working_crop  = {192, 12, 820, 820},
        .visible_crop  = {192, 12, 820, 820},
        .scissor       = {0, 0, 448, 448},
        .vertex_count  = 4,
        .index_count   = 6,
        .position_bits = {
            0x42cc0000, 0x40c00000, 0x4406c000, 0x40c00000,
            0x4406c000, 0x43dd8000, 0x42cc0000, 0x43dd8000,
        },
        .source_bits = {
            0x433fd6ce, 0x41348df0, 0x447d6f3a, 0x41348df0,
            0x447d6f3a, 0x44504bbe, 0x433fd6ce, 0x44504bbe,
        },
    },
    {
        .sample        = 16,
        .fraction_bits = UINT32_C(0x3eff9040),
        .scale_bits    = UINT32_C(0x3f2029e8),
        .kind          = WALLE_LG_PRODUCER_DIRECT,
        .working_crop  = {222, -18, 820, 820},
        .visible_crop  = {222, 0, 802, 802},
        .scissor       = {0, 0, 512, 512},
        .vertex_count  = 16,
        .index_count   = 24,
        .position_bits = {
            0x430a0000, 0x00000000, 0x44204000, 0x00000000,
            0x44204000, 0x43fb0000, 0x430a0000, 0x43fb0000,
        },
        .source_bits = {
            0x435c9307, 0x00000000, 0x448011a8, 0x00000000,
            0x448011a8, 0x44489843, 0x435c9307, 0x44489843,
        },
    },
    {
        .sample        = 20,
        .fraction_bits = UINT32_C(0x3ebf4960),
        .scale_bits    = UINT32_C(0x3f38447c),
        .kind          = WALLE_LG_PRODUCER_DIRECT,
        .working_crop  = {252, -48, 820, 820},
        .visible_crop  = {252, 0, 772, 772},
        .scissor       = {0, 0, 570, 571},
        .vertex_count  = 16,
        .index_count   = 24,
        .position_bits = {
            0x43350000, 0x00000000, 0x44388000, 0x00000000,
            0x44388000, 0x440b0000, 0x43350000, 0x440b0000,
        },
        .source_bits = {
            0x437b75e3, 0x00000000, 0x44802958, 0x00000000,
            0x44802958, 0x44411c4c, 0x437b75e3, 0x44411c4c,
        },
    },
    {
        .sample        = 24,
        .fraction_bits = UINT32_C(0x3e7eb3c0),
        .scale_bits    = UINT32_C(0x3f503e4c),
        .kind          = WALLE_LG_PRODUCER_DIRECT,
        .working_crop  = {282, -78, 820, 820},
        .visible_crop  = {282, 0, 742, 742},
        .scissor       = {0, 0, 617, 618},
        .vertex_count  = 16,
        .index_count   = 24,
        .position_bits = {
            0x43650000, 0x00000000, 0x44504000, 0x00000000,
            0x44504000, 0x44170000, 0x43650000, 0x44170000,
        },
        .source_bits = {
            0x438cc226, 0x00000000, 0x4480010c, 0x00000000,
            0x4480010c, 0x4439a105, 0x438cc226, 0x4439a105,
        },
    },
    {
        .sample        = 28,
        .fraction_bits = UINT32_C(0x3dfdab00),
        .scale_bits    = UINT32_C(0x3f6837f8),
        .kind          = WALLE_LG_PRODUCER_DIRECT,
        .working_crop  = {312, -108, 820, 820},
        .visible_crop  = {312, 0, 712, 712},
        .scissor       = {0, 0, 659, 660},
        .vertex_count  = 16,
        .index_count   = 24,
        .position_bits = {
            0x438d8000, 0x00000000, 0x44684000, 0x00000000,
            0x44684000, 0x44218000, 0x438d8000, 0x44218000,
        },
        .source_bits = {
            0x439bfdae, 0x00000000, 0x4480046d, 0x00000000,
            0x4480046d, 0x44320a04, 0x439bfdae, 0x44320a04,
        },
    },
};

static uint32_t float_bits(float value)
{
    uint32_t result;
    memcpy(&result, &value, sizeof result);
    return result;
}

static size_t compare_i32(const int32_t candidate[static 4],
                          const int32_t expected[static 4],
                          const char*   label,
                          uint32_t      sample)
{
    size_t mismatches = 0;
    for (size_t index = 0; index < 4; ++index) {
        if (candidate[index] == expected[index])
            continue;
        fprintf(stderr,
                "sample %02" PRIu32 " %s[%zu]: got %" PRId32 ", expected %" PRId32 "\n",
                sample,
                label,
                index,
                candidate[index],
                expected[index]);
        ++mismatches;
    }
    return mismatches;
}

static size_t compare_primary(const struct walle_lg_dynamic_producer_mesh* mesh,
                              const struct producer_fixture*               fixture)
{
    size_t mismatches = 0;
    for (size_t vertex = 0; vertex < 4; ++vertex) {
        for (size_t axis = 0; axis < 2; ++axis) {
            uint32_t position  = float_bits(mesh->vertices[vertex].position[axis]);
            uint32_t source    = float_bits(mesh->vertices[vertex].source[axis]);
            size_t   component = 2 * vertex + axis;
            if (position != fixture->position_bits[component]) {
                fprintf(stderr,
                        "sample %02" PRIu32 " position[%zu]: got 0x%08" PRIx32
                        ", expected 0x%08" PRIx32 "\n",
                        fixture->sample,
                        component,
                        position,
                        fixture->position_bits[component]);
                ++mismatches;
            }
            if (source != fixture->source_bits[component]) {
                fprintf(stderr,
                        "sample %02" PRIu32 " source[%zu]: got 0x%08" PRIx32
                        ", expected 0x%08" PRIx32 "\n",
                        fixture->sample,
                        component,
                        source,
                        fixture->source_bits[component]);
                ++mismatches;
            }
        }
        if (float_bits(mesh->vertices[vertex].position[2]) != 0
            || float_bits(mesh->vertices[vertex].position[3]) != UINT32_C(0x3f800000)) {
            fprintf(stderr,
                    "sample %02" PRIu32 " primary homogeneous coordinate differs\n",
                    fixture->sample);
            ++mismatches;
        }
    }
    return mismatches;
}

static size_t compare_topology(const struct walle_lg_dynamic_producer_mesh* mesh,
                               const struct producer_fixture*               fixture)
{
    size_t mismatches = 0;
    if (mesh->kind != fixture->kind) {
        fprintf(stderr, "sample %02" PRIu32 " producer kind differs\n", fixture->sample);
        ++mismatches;
    }
    if (mesh->vertex_count != fixture->vertex_count) {
        fprintf(stderr,
                "sample %02" PRIu32 " vertex count: got %" PRIu32 ", expected %" PRIu32 "\n",
                fixture->sample,
                mesh->vertex_count,
                fixture->vertex_count);
        ++mismatches;
    }
    if (mesh->index_count != fixture->index_count) {
        fprintf(stderr,
                "sample %02" PRIu32 " index count: got %" PRIu32 ", expected %" PRIu32 "\n",
                fixture->sample,
                mesh->index_count,
                fixture->index_count);
        ++mismatches;
    }
    constexpr uint16_t quad[6] = {0, 1, 2, 2, 3, 0};
    uint32_t           compared_indices
        = mesh->index_count < fixture->index_count ? mesh->index_count : fixture->index_count;
    for (uint32_t index = 0; index < compared_indices; ++index) {
        uint16_t expected = (uint16_t)(4 * (index / 6) + quad[index % 6]);
        if (mesh->indices[index] == expected)
            continue;
        fprintf(stderr,
                "sample %02" PRIu32 " index[%" PRIu32 "]: got %" PRIu16 ", expected %" PRIu16 "\n",
                fixture->sample,
                index,
                mesh->indices[index],
                expected);
        ++mismatches;
    }
    return mismatches;
}

int main(void)
{
    size_t mismatches = 0;
    for (size_t index = 0; index < sizeof fixtures / sizeof fixtures[0]; ++index) {
        const struct producer_fixture* fixture = &fixtures[index];
        float                          fraction;
        memcpy(&fraction, &fixture->fraction_bits, sizeof fraction);
        struct walle_lg_transition_frame_request request = {
            .material             = WALLE_LG_MATERIAL_REGULAR,
            .appearance           = WALLE_LG_APPEARANCE_DARK,
            .window_width         = 1024,
            .window_height        = 1024,
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
        if (float_bits(frame.backdrop_scale) != fixture->scale_bits) {
            fprintf(stderr,
                    "sample %02" PRIu32 " scale: got 0x%08" PRIx32 ", expected 0x%08" PRIx32 "\n",
                    fixture->sample,
                    float_bits(frame.backdrop_scale),
                    fixture->scale_bits);
            ++mismatches;
        }
        mismatches += compare_i32(frame.producer_mesh.working_crop,
                                  fixture->working_crop,
                                  "workingCrop",
                                  fixture->sample);
        mismatches += compare_i32(frame.producer_mesh.visible_crop,
                                  fixture->visible_crop,
                                  "visibleCrop",
                                  fixture->sample);
        mismatches += compare_i32(
            frame.producer_mesh.scissor, fixture->scissor, "scissor", fixture->sample);
        mismatches += compare_topology(&frame.producer_mesh, fixture);
        mismatches += compare_primary(&frame.producer_mesh, fixture);
    }

    printf("dynamicProducerCases=%zu\n", sizeof fixtures / sizeof fixtures[0]);
    printf("checkedCropComponents=%zu\n", sizeof fixtures / sizeof fixtures[0] * 8u);
    printf("checkedScissorComponents=%zu\n", sizeof fixtures / sizeof fixtures[0] * 4u);
    printf("checkedPrimaryF32Components=%zu\n", sizeof fixtures / sizeof fixtures[0] * 20u);
    printf("mismatchedComponents=%zu\n", mismatches);
    printf("exact=%s\n", mismatches == 0 ? "true" : "false");
    return mismatches == 0 ? 0 : 1;
}
