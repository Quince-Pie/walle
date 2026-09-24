/* QuartzCore emit_quad18a6639f4, simple_texcoords18a68e704. */
#include "clip.h"
#include <math.h>
#include <string.h>

static void interpolate(float uv[4][2], unsigned destination, unsigned other, float t)
{
    for (unsigned axis = 0; axis < 2; ++axis) {
        float from = uv[destination][axis];
        float difference = uv[other][axis] - from;
        uv[destination][axis] = fmaf(difference, t, from);
    }
}

enum wm_quad_clip_status wm_clip_rect_quad(const double rect[4], const float coords[4],
                                          const double m[6], const int32_t clip[4],
                                          struct wm_vertex out[4])
{
    if (!out)
        return WM_QUAD_CLIP_INVALID;
    memset(out, 0, 4 * sizeof *out);
    if (!rect || !m || !clip)
        return WM_QUAD_CLIP_INVALID;
    for (unsigned i = 0; i < 4; ++i)
        if (!isfinite(rect[i]) || (coords && !isfinite(coords[i])))
            return WM_QUAD_CLIP_INVALID;
    for (unsigned i = 0; i < 6; ++i)
        if (!isfinite(m[i]))
            return WM_QUAD_CLIP_INVALID;
    if (m[1] != 0 || m[2] != 0 || m[0] == 0 || fabs(m[0]) != fabs(m[3]))
        return WM_QUAD_CLIP_INVALID;
    if (rect[2] <= rect[0] || rect[3] <= rect[1] || clip[2] <= 0 || clip[3] <= 0)
        return WM_QUAD_CLIP_EMPTY;
    /* Integer endpoints are added in Double, not signed32 arithmetic. */
    double limits[4] = {clip[0], clip[1], (double)clip[0] + clip[2],
                                          (double)clip[1] + clip[3]};
    float uv[4][2] = {};
    float position[4];
    if (m[0] == 1 && m[3] == 1) {
        /* Flags&0x1f==0:18a663c50..c84. Translation precedes the first
         * Float narrowing. Subsequent denominators use Float subtraction. */
        for (unsigned i = 0; i < 4; ++i) {
            position[i] = (float)(rect[i] + m[4 + (i & 1)]);
            if (!isfinite(position[i]))
                return WM_QUAD_CLIP_INVALID;
        }
        if (!(position[2] > position[0] && position[3] > position[1]
              && position[2] > limits[0] && limits[2] > position[0]
              && position[3] > limits[1] && limits[3] > position[1]))
            return WM_QUAD_CLIP_EMPTY;
        float endpoints[4] = {};
        if (coords)
            memcpy(endpoints, coords, sizeof endpoints);
        /*18a664508..5a4: left,right,top,bottom, each using the already
         * clipped opposite endpoint in its denominator and Float UV delta. */
        for (unsigned axis = 0; axis < 2; ++axis) {
            if ((double)position[axis] < limits[axis]) {
                if (coords) {
                    float span = position[axis + 2] - position[axis];
                    float t = (float)((limits[axis] - (double)position[axis]) / (double)span);
                    float delta = endpoints[axis + 2] - endpoints[axis];
                    endpoints[axis] = fmaf(delta, t, endpoints[axis]);
                }
                position[axis] = (float)limits[axis];
            }
            if ((double)position[axis + 2] > limits[axis + 2]) {
                if (coords) {
                    float span = position[axis + 2] - position[axis];
                    float t = (float)(((double)position[axis + 2] - limits[axis + 2]) / (double)span);
                    float delta = endpoints[axis] - endpoints[axis + 2];
                    endpoints[axis + 2] = fmaf(delta, t, endpoints[axis + 2]);
                }
                position[axis + 2] = (float)limits[axis + 2];
            }
        }
        uv[0][0] = uv[3][0] = endpoints[0];
        uv[0][1] = uv[1][1] = endpoints[1];
        uv[1][0] = uv[2][0] = endpoints[2];
        uv[2][1] = uv[3][1] = endpoints[3];
    } else {
        /* Simple scale/reflection:18a663a50..af8 retains Double endpoints
         * until emission. FMUL, optional FNEG, then separate FADD. */
        double scale = fabs(m[0]), transformed[4], bounds[4];
        for (unsigned i = 0; i < 4; ++i) {
            unsigned axis = i & 1;
            double value = scale == 1 ? rect[i] : scale * rect[i];
            if (m[axis == 0 ? 0 : 3] < 0)
                value = -value;
            transformed[i] = value + m[4 + axis];
            if (!isfinite(transformed[i]))
                return WM_QUAD_CLIP_INVALID;
        }
        for (unsigned axis = 0; axis < 2; ++axis) {
            bool reversed = transformed[axis + 2] < transformed[axis];
            bounds[axis] = transformed[axis + (reversed ? 2 : 0)];
            bounds[axis + 2] = transformed[axis + (reversed ? 0 : 2)];
        }
        if (!(bounds[2] > limits[0] && bounds[0] < limits[2]
              && bounds[3] > limits[1] && bounds[1] < limits[3]))
            return WM_QUAD_CLIP_EMPTY;
        if (coords) {
            /* simple_texcoords permutes bits before clipping; retain all
             * corners because the later constant-lane FMAs affect signed0. */
            unsigned x0 = m[0] < 0 ? 2 : 0, x1 = m[0] < 0 ? 0 : 2;
            unsigned y0 = m[3] < 0 ? 3 : 1, y1 = m[3] < 0 ? 1 : 3;
            uv[0][0] = uv[3][0] = coords[x0];
            uv[1][0] = uv[2][0] = coords[x1];
            uv[0][1] = uv[1][1] = coords[y0];
            uv[2][1] = uv[3][1] = coords[y1];
        }
        constexpr unsigned corners[4][4] = {{0,1,3,2}, {1,0,2,3}, {0,3,1,2}, {3,0,2,1}};
        for (unsigned edge = 0; edge < 4; ++edge) {
            unsigned axis = edge / 2;
            bool high = (edge & 1) != 0;
            unsigned endpoint = axis + (high ? 2 : 0);
            bool clipped = high ? bounds[endpoint] > limits[endpoint]
                                : bounds[endpoint] < limits[endpoint];
            if (!clipped)
                continue;
            double distance = high ? bounds[endpoint] - limits[endpoint]
                                   : limits[endpoint] - bounds[endpoint];
            float t = (float)(distance / (bounds[axis + 2] - bounds[axis]));
            if (coords) {
                interpolate(uv, corners[edge][0], corners[edge][1], t);
                interpolate(uv, corners[edge][2], corners[edge][3], t);
            }
            bounds[endpoint] = limits[endpoint];
        }
        for (unsigned i = 0; i < 4; ++i)
            position[i] = (float)bounds[i];
    }
    for (unsigned i = 0; i < 4; ++i) {
        out[i].position[0] = position[i == 1 || i == 2 ? 2 : 0];
        out[i].position[1] = position[i >= 2 ? 3 : 1];
        memcpy(out[i].local_uv, uv[i], sizeof out[i].local_uv);
    }
    return WM_QUAD_CLIP_READY;
}
