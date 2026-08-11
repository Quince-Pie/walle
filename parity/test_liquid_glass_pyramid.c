#include "liquid_glass_pyramid.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static constexpr unsigned char fixture[] = {
#embed "static_regular_pyramid_v1_fixture.bin"
};

static uint32_t load_u32(const unsigned char *bytes)
{
    return (uint32_t)bytes[0]
        | (uint32_t)bytes[1] << 8
        | (uint32_t)bytes[2] << 16
        | (uint32_t)bytes[3] << 24;
}

static unsigned char *diagnostic_wallpaper(size_t *byte_count)
{
    constexpr uint32_t width = 1024;
    constexpr uint32_t height = 1024;
    *byte_count = (size_t)width * height * 4u;
    unsigned char *pixels = malloc(*byte_count);
    if (pixels == nullptr) {
        return nullptr;
    }
    memset(pixels, 255, *byte_count);
    for (uint32_t y = 0; y < height; ++y) {
        for (uint32_t x = 0; x < width; ++x) {
            uint32_t hash = x * UINT32_C(0x045d9f3b) ^ y * UINT32_C(0x119de1f3);
            size_t offset = ((size_t)y * width + x) * 4u;
            pixels[offset] = (unsigned char)hash;
            pixels[offset + 1] = (unsigned char)(hash >> 8);
            pixels[offset + 2] = (unsigned char)(hash >> 16);
        }
    }
    return pixels;
}

int main(void)
{
    static constexpr unsigned char magic[] = "WLGSPV1";
    static constexpr size_t header_size = 120;
    static constexpr uint32_t level_count = 6;
    static constexpr uint32_t payload_bytes = 546000;
    if (sizeof(fixture) != header_size + payload_bytes
        || memcmp(fixture, magic, sizeof(magic)) != 0
        || load_u32(fixture + 8) != 1
        || load_u32(fixture + 12) != level_count
        || load_u32(fixture + 16) != header_size
        || load_u32(fixture + 20) != payload_bytes) {
        fputs("static regular pyramid fixture contract differs\n", stderr);
        return 1;
    }

    size_t wallpaper_byte_count;
    unsigned char *wallpaper = diagnostic_wallpaper(&wallpaper_byte_count);
    if (wallpaper == nullptr) {
        fputs("diagnostic wallpaper allocation failed\n", stderr);
        return 1;
    }
    const struct walle_lg_static_regular_request request = {
        .diameter = 377,
        .center_x = 301.25,
        .center_y = 699.75,
        .window_width = 1024,
        .window_height = 1024,
    };
    struct walle_lg_pyramid pyramid;
    bool built = walle_lg_build_static_regular_pyramid(
        wallpaper,
        wallpaper_byte_count,
        &request,
        &pyramid
    );
    free(wallpaper);
    if (!built || pyramid.level_count != level_count) {
        fputs("static regular pyramid construction failed\n", stderr);
        return 1;
    }

    uint32_t compared = 0;
    uint32_t mismatched = 0;
    for (uint32_t level = 0; level < level_count; ++level) {
        const unsigned char *descriptor = fixture + 24u + (size_t)level * 16u;
        uint32_t width = load_u32(descriptor);
        uint32_t height = load_u32(descriptor + 4);
        uint32_t offset = load_u32(descriptor + 8);
        uint32_t byte_count = load_u32(descriptor + 12);
        const struct walle_lg_pyramid_level *actual = &pyramid.levels[level];
        if (actual->width != width || actual->height != height
            || actual->byte_count != byte_count
            || (size_t)offset + byte_count > sizeof(fixture)) {
            fprintf(stderr, "static regular pyramid level %u layout differs\n", level);
            walle_lg_destroy_pyramid(&pyramid);
            return 1;
        }
        const unsigned char *expected = fixture + offset;
        for (uint32_t byte = 0; byte < byte_count; ++byte) {
            ++compared;
            if (actual->bgra8[byte] != expected[byte]) {
                if (mismatched < 16) {
                    fprintf(stderr,
                            "static regular pyramid level %u byte %u: expected %02x, got %02x\n",
                            level,
                            byte,
                            expected[byte],
                            actual->bgra8[byte]);
                }
                ++mismatched;
            }
        }
    }
    walle_lg_destroy_pyramid(&pyramid);
    printf("static regular pyramid: %u/%u exact bytes\n",
           compared - mismatched,
           compared);
    return mismatched == 0 ? 0 : 1;
}
