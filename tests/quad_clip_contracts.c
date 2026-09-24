#include "clip.h"
#include "transition_check.h"
#include <math.h>
#include <stdbit.h>
#include <string.h>

struct fixture_record {
    double endpoints[4], affine[6];
    int32_t clip[4];
    float uv[4];
    uint32_t has_uv, expected_status, native_index, native_flags;
    float vertices[4][4];
};
static_assert(sizeof(struct fixture_record) == 192);
static_assert(__STDC_ENDIAN_NATIVE__ == __STDC_ENDIAN_LITTLE__);

static unsigned invalid_cases;
static void invalid(const double rect[4], const float uv[4], const double affine[6],
                     const int32_t clip[4], bool output)
{
    struct wm_vertex vertices[4], zero[4] = {};
    memset(vertices, 0xa5, sizeof vertices);
    CHECK(wm_clip_rect_quad(rect, uv, affine, clip, output ? vertices : nullptr)
          == WM_QUAD_CLIP_INVALID);
    if (output)
        CHECK(memcmp(vertices, zero, sizeof vertices) == 0);
    ++invalid_cases;
}
static void invalid_inputs(void)
{
    snprintf(test_case, sizeof test_case, "invalid clipping input");
    double rect[4] = {-1,-1,101,61}, affine[6] = {1,0,0,-1,12.25,19.5};
    float uv[4] = {-51,-31,51,31};
    const int32_t clip[4] = {0,0,256,144};
    invalid(nullptr, uv, affine, clip, true);
    invalid(rect, uv, nullptr, clip, true);
    invalid(rect, uv, affine, nullptr, true);
    invalid(rect, uv, affine, clip, false);
    affine[0] = 0;
    invalid(rect, uv, affine, clip, true);
    affine[0] = 2;
    invalid(rect, uv, affine, clip, true);
    affine[0] = 1; affine[1] = .01;
    invalid(rect, uv, affine, clip, true);
    affine[1] = 0; affine[4] = INFINITY;
    invalid(rect, uv, affine, clip, true);
    affine[4] = 12.25; rect[0] = NAN;
    invalid(rect, uv, affine, clip, true);
    rect[0] = -1; uv[0] = NAN;
    invalid(rect, uv, affine, clip, true);
}
int main(int argc, char** argv)
{
    CHECK(argc == 2);
    FILE* input = fopen(argv[1], "rb");
    CHECK(input);
    char magic[8];
    uint32_t count, record_size;
    CHECK(fread(magic, sizeof magic, 1, input) == 1 && memcmp(magic, "WQCLIP01", 8) == 0);
    CHECK(fread(&count, sizeof count, 1, input) == 1 && count == 3109);
    CHECK(fread(&record_size, sizeof record_size, 1, input) == 1 && record_size == sizeof(struct fixture_record));
    unsigned ready = 0, empty = 0, native_scene = 0, reflected = 0, identity = 0;
    for (uint32_t i = 0; i < count; ++i) {
        struct fixture_record fixture;
        CHECK(fread(&fixture, sizeof fixture, 1, input) == 1);
        snprintf(test_case, sizeof test_case, "original clip case%u(flags%u)",
                 fixture.native_index, fixture.native_flags);
        struct wm_vertex vertices[4], zero[4] = {};
        memset(vertices, 0xa5, sizeof vertices);
        enum wm_quad_clip_status result = wm_clip_rect_quad(fixture.endpoints,
            fixture.has_uv ? fixture.uv : nullptr, fixture.affine, fixture.clip, vertices);
        CHECK(result == (enum wm_quad_clip_status)fixture.expected_status);
        if (result == WM_QUAD_CLIP_READY) {
            ++ready;
            for (unsigned v = 0; v < 4; ++v) {
                float actual[4] = {vertices[v].position[0], vertices[v].position[1],
                                    vertices[v].local_uv[0], vertices[v].local_uv[1]};
                if (memcmp(actual, fixture.vertices[v], sizeof actual)) {
                    for (unsigned a = 0; a < 2; ++a)
                        fprintf(stderr, "vertex%u position[%u]=%a expected%a; UV=%a expected%a\n",
                                v, a, vertices[v].position[a], fixture.vertices[v][a],
                                vertices[v].local_uv[a], fixture.vertices[v][a + 2]);
                }
                CHECK(memcmp(actual, fixture.vertices[v], sizeof actual) == 0);
                CHECK(memcmp(vertices[v].source_uv, zero[v].source_uv, sizeof vertices[v].source_uv) == 0);
            }
        } else {
            ++empty;
            CHECK(memcmp(vertices, zero, sizeof vertices) == 0);
        }
        native_scene += fixture.native_index < 21;
        identity += fixture.native_flags == 0;
        reflected += (fixture.native_flags & 3u) != 0;
    }
    CHECK(fgetc(input) == EOF && !ferror(input) && fclose(input) == 0);
    CHECK(native_scene == 21 && identity && reflected && ready && empty);
    invalid_inputs();
    printf("{\"original_cases\":%u,\"native_scene_calls\":%u,\"ready\":%u,"
           "\"empty\":%u,\"identity\":%u,\"reflected\":%u,\"invalid_inputs\":%u}\n",
           count, native_scene, ready, empty, identity, reflected, invalid_cases);
    return 0;
}
