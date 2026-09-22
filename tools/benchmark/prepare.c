#define _GNU_SOURCE
#include <errno.h>
#include <stdckdint.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <vips/vips.h>

int main(int argc, char** argv)
{
    if (argc != 5) {
        fprintf(stderr, "usage: prepare INPUT WIDTH HEIGHT OUTPUT.rgba\n");
        return 2;
    }
    char* end;
    errno           = 0;
    unsigned long w = strtoul(argv[2], &end, 10);
    if (errno || *end || !w || w > 16384)
        return 2;
    errno           = 0;
    unsigned long h = strtoul(argv[3], &end, 10);
    if (errno || *end || !h || h > 16384)
        return 2;
    if (VIPS_INIT(argv[0]))
        return 1;
    VipsImage *image = nullptr, *next = nullptr;
    bool       ok = vips_thumbnail(argv[1],
                                   &image,
                                   (int)w,
                                   "height",
                                   (int)h,
                                   "crop",
                                   VIPS_INTERESTING_CENTRE,
                                   "no_rotate",
                                   TRUE,
                                   nullptr)
                    == 0;
    if (ok) {
        ok = vips_colourspace(image, &next, VIPS_INTERPRETATION_sRGB, nullptr) == 0;
        g_object_unref(image);
        image = next;
        next  = nullptr;
    }
    if (ok && vips_image_hasalpha(image)) {
        double           black[3]   = {0, 0, 0};
        VipsArrayDouble* background = vips_array_double_new(black, 3);
        ok = vips_flatten(image, &next, "background", background, nullptr) == 0;
        vips_area_unref((VipsArea*)background);
        g_object_unref(image);
        image = next;
        next  = nullptr;
    }
    if (ok) {
        ok = vips_addalpha(image, &next, nullptr) == 0;
        g_object_unref(image);
        image = next;
        next  = nullptr;
    }
    if (ok && vips_image_get_format(image) != VIPS_FORMAT_UCHAR) {
        ok = vips_cast(image, &next, VIPS_FORMAT_UCHAR, nullptr) == 0;
        g_object_unref(image);
        image = next;
        next  = nullptr;
    }
    size_t expected = 0, bytes = 0;
    void*  pixels = nullptr;
    ok = ok && !ckd_mul(&expected, (size_t)w, (size_t)h) && !ckd_mul(&expected, expected, (size_t)4)
         && vips_image_get_width(image) == (int)w && vips_image_get_height(image) == (int)h
         && vips_image_get_bands(image) == 4;
    if (ok) {
        pixels = vips_image_write_to_memory(image, &bytes);
        ok     = pixels && bytes == expected;
    }
    if (ok) {
        FILE* out = fopen(argv[4], "wb");
        ok        = out != nullptr;
        if (out) {
            ok = fwrite(pixels, 1, bytes, out) == bytes;
            ok = fclose(out) == 0 && ok;
        }
    }
    if (!ok)
        fprintf(stderr, "image preparation failed: %s\n", vips_error_buffer());
    if (pixels)
        g_free(pixels);
    if (image)
        g_object_unref(image);
    vips_shutdown();
    return ok ? 0 : 1;
}
