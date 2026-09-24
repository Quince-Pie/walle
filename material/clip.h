#ifndef WM_CLIP_H
#define WM_CLIP_H
#include "geometry.h"

enum wm_quad_clip_status : int8_t
{
    WM_QUAD_CLIP_INVALID = -1,
    WM_QUAD_CLIP_EMPTY = 0,
    WM_QUAD_CLIP_READY = 1,
};

/* Original emit_one_part_rect -> emit_quad, with one native clip rectangle.
 * Local endpoints and optional UV endpoints are x0,y0,x1,y1, not x,y,w,h.
 * affine={a,b,c,d,tx,ty} supports nonzero uniform axis-aligned transforms,
 * including either/both reflections and identity/translation. clip_bounds
 * is native signed32 x,y,width,height. General rotation/perspective and the
 * nine-part/surface-backed SDF paths have different emitters.
 * Output is TL,TR,BR,BL with triangle indices0,1,2,2,3,0. UVs are local_uv;
 * source_uv is zero. Nonnull output is zeroed for EMPTY/INVALID results. */
enum wm_quad_clip_status wm_clip_rect_quad(const double endpoints[4],
                                          const float uv_endpoints[4],
                                          const double affine[6],
                                          const int32_t clip_bounds[4],
                                          struct wm_vertex out[4]);
#endif
