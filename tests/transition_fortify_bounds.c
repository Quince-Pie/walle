#include "geometry.h"
#include "transition_check.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

#if !defined(_FORTIFY_SOURCE) || _FORTIFY_SOURCE < 3
#error "This target must be built with Fortify level3"
#endif
#if !defined(__OPTIMIZE__)
#error "Fortify coverage requires compiler optimization"
#endif

struct guarded_grid {
    unsigned char before[64];
    struct wm_sdf_grid grid;
    unsigned char after[64];
};

static_assert(sizeof(((struct wm_sdf_grid *)nullptr)->x) == 6 * sizeof(double));
static_assert(sizeof(((struct wm_sdf_grid *)nullptr)->y) == 6 * sizeof(double));
static_assert(sizeof(((struct wm_sdf_grid *)nullptr)->sx) == 6 * sizeof(float));
static_assert(sizeof(((struct wm_sdf_grid *)nullptr)->sy) == 6 * sizeof(float));

int main(void)
{
    unsigned cases = 0;
    snprintf(test_case, sizeof test_case, "Fortify3 six-coordinate shadow/corner bounds");
    for (unsigned i = 1; i <= 1000; ++i) {
        struct guarded_grid storage;
        memset(&storage, 0xa5, sizeof storage);
        double width = 200 + i * .125, height = 120 + i * .0625;
        double offset[2] = {-3, 8};
        CHECK(wm_sdf_grid_build(width, height, 20, false, 1, 40, 12,
                                  offset, true, 1, &storage.grid));
        CHECK(storage.grid.nx == 6 && storage.grid.ny == 6 && storage.grid.group_count == 3);
        for (unsigned j = 0; j < 64; ++j)
            CHECK(storage.before[j] == 0xa5 && storage.after[j] == 0xa5);
        for (unsigned j = 0; j < 6; ++j) {
            CHECK(isfinite(storage.grid.x[j]) && isfinite(storage.grid.y[j]));
            CHECK(isfinite(storage.grid.sx[j]) && isfinite(storage.grid.sy[j]));
            if (j) {
                CHECK(storage.grid.x[j] >= storage.grid.x[j-1]);
                CHECK(storage.grid.y[j] >= storage.grid.y[j-1]);
            }
        }
        ++cases;
    }
    printf("PASS %u Fortify3 controls through six-coordinate shadow/corner branch\n", cases);
}
