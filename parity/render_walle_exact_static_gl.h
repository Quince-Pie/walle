#ifndef WALLE_RENDER_EXACT_STATIC_GL_H
#define WALLE_RENDER_EXACT_STATIC_GL_H

#include <EGL/egl.h>
#include <wayland-client.h>

int walle_exact_static_gl_render_current(EGLDisplay         display,
                                         EGLSurface         surface,
                                         struct wl_display* wayland_display,
                                         const char*        fixture_directory,
                                         const char*        vertex_shader,
                                         const char*        fragment_shader,
                                         const char*        intrinsic_table);

#endif
