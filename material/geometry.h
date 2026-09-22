#ifndef WM_GEOMETRY_H
#define WM_GEOMETRY_H
#include <stdint.h>

struct wm_vertex
{
    float position[2], local_uv[2], source_uv[2];
};
struct wm_mesh_group
{
    uint32_t index_count;
    float    mode;
    uint32_t image_function;
    uint32_t indices[96];
};
struct wm_sdf_grid
{
    uint32_t             nx, ny, group_count;
    double               x[6], y[6];
    float                sx[6], sy[6];
    struct wm_mesh_group groups[3];
};
struct wm_face_grid
{
    double x[4], y[4];
    float  u[4], v[4], circularity[2];
};

/* Source emit_sdf_bounds_internal; matrix is optional element→SDF-root2x2.
 * Keep the application screen-Y reflection separate: apply to vertex positions
 * and the glass displacement domain. For a positive uniform lens scale use
 * matrix={s,0,0,s}; pass wm_sdf_scale(matrix), including its Float rounding,
 * to wm_sdf_grid_build.
 */
bool  wm_sdf_arguments(double       width,
                       double       height,
                       const double matrix[4],
                       double       radius,
                       bool         continuous,
                       double       ovalization,
                       double       outset,
                       float        output[12]);
float wm_sdf_scale(const double matrix[4]);
bool  wm_sdf_grid_build(double              width,
                        double              height,
                        double              radius,
                        bool                continuous,
                        double              outset,
                        double              maximum_distance,
                        double              shadow_grow,
                        const double        shadow_offset[2],
                        bool                glass_surface,
                        double              scale,
                        struct wm_sdf_grid* out);
/* Grid local positions are0..width/height. affine={a,b,c,d,tx,ty} maps those
 * to target pixels. source UV is the native normalized H.sdf_src_uv result.
 */
uint32_t wm_sdf_vertices(const struct wm_sdf_grid* grid,
                         const double              affine[6],
                         double                    sample_scale,
                         const double              source_origin[2],
                         const uint32_t            source_size[2],
                         struct wm_vertex          output[36]);
bool     wm_face_grid_build(
        double width, double height, double radius, bool continuous, struct wm_face_grid* out);
static_assert(sizeof(struct wm_vertex) == 24);
#endif
