#include "liquid_glass_reveal_stage.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

static int failures;

static void check(bool condition, const char* message)
{
    if (condition)
        return;
    fprintf(stderr, "FAIL: %s\n", message);
    ++failures;
}

static bool texture_equal(const struct walle_lg_gl_texture_view* left,
                          const struct walle_lg_gl_texture_view* right)
{
    return left->texture == right->texture && left->width == right->width
           && left->height == right->height;
}

static bool route_is_empty(const struct walle_lg_gl_frame_inputs* route)
{
    return route->pyramid_source.texture == 0 && route->pyramid_source.width == 0
           && route->pyramid_source.height == 0 && route->destination.texture == 0
           && route->destination.width == 0 && route->destination.height == 0
           && !route->reveal_applied;
}

static struct walle_lg_gl_frame_inputs poisoned_route(void)
{
    return (struct walle_lg_gl_frame_inputs){
        .pyramid_source = {.texture = 1, .width = 2, .height = 3},
        .destination    = {.texture = 4, .width = 5, .height = 6},
        .reveal_applied = true,
    };
}

int main(void)
{
    const struct walle_lg_gl_texture_view base_source = {
        .texture = UINT32_C(0x10203040),
        .width   = 2048,
        .height  = 1152,
    };
    const struct walle_lg_gl_texture_view base_destination = {
        .texture = UINT32_C(0x50607080),
        .width   = 2048,
        .height  = 1152,
    };
    struct walle_lg_gl_frame_inputs route;
    max_align_t                     foreign_authority_storage = {};
    const struct walle_lg_reveal_stage_authority* forged_authority
        = (const void*)&foreign_authority_storage;

    check(walle_lg_reveal_stage_authority_acquire() == nullptr,
          "production authority must be unavailable before selector/holdout approval");
    check(walle_lg_reveal_stage_route(&base_source, &base_destination, nullptr, &route),
          "null request must select bypass");
    check(texture_equal(&route.pyramid_source, &base_source),
          "bypass must preserve every pyramid source descriptor field");
    check(texture_equal(&route.destination, &base_destination),
          "bypass must preserve every destination descriptor field");
    check(!route.reveal_applied, "bypass must report no reveal");

    const struct walle_lg_reveal_stage_request disabled = {
        .intent      = WALLE_LG_REVEAL_STAGE_DISABLED,
        .composition = {.texture = UINT32_MAX, .width = 1, .height = 1},
        .authority   = forged_authority,
    };
    check(walle_lg_reveal_stage_route(&base_source, &base_destination, &disabled, &route),
          "disabled request must ignore dormant reveal fields");
    check(texture_equal(&route.pyramid_source, &base_source)
              && texture_equal(&route.destination, &base_destination) && !route.reveal_applied,
          "disabled request must remain an exact identity route");

    const struct walle_lg_reveal_stage_request unauthorized = {
        .intent      = WALLE_LG_REVEAL_STAGE_EXACT,
        .composition = {.texture = 7, .width = 2048, .height = 1152},
        .authority   = nullptr,
    };
    check(!walle_lg_reveal_stage_route(
              &base_source, &base_destination, &unauthorized, &route),
          "exact request must fail closed without authority");
    check(route_is_empty(&route),
          "rejected request must not leak a partially usable route");

    struct walle_lg_reveal_stage_request forged = unauthorized;
    forged.authority                            = forged_authority;
    route                                       = poisoned_route();
    check(!walle_lg_reveal_stage_route(&base_source, &base_destination, &forged, &route),
          "a correctly aligned foreign object must not forge authority by address");
    check(route_is_empty(&route), "forged authority rejection must clear the route");

    const struct walle_lg_reveal_stage_authority* testing_authority
        = walle_lg_reveal_stage_testing_authority();
    struct walle_lg_reveal_stage_request approved = unauthorized;
    approved.authority                            = testing_authority;
    check(walle_lg_reveal_stage_route(&base_source, &base_destination, &approved, &route),
          "approved exact request must route");
    check(route.reveal_applied, "approved exact request must report reveal");
    check(texture_equal(&route.pyramid_source, &approved.composition),
          "approved composition must feed pyramid construction");
    check(texture_equal(&route.destination, &approved.composition),
          "approved composition must feed DestinationTexture");
    check(route.pyramid_source.texture == route.destination.texture,
          "both consumers must receive one texture object");

    approved.composition.width = 2047;
    route                      = poisoned_route();
    check(!walle_lg_reveal_stage_route(&base_source, &base_destination, &approved, &route),
          "composition extent mismatch must fail closed");
    check(route_is_empty(&route), "extent rejection must clear the route");
    approved.composition.width  = 2048;
    approved.composition.texture = 0;
    route                        = poisoned_route();
    check(!walle_lg_reveal_stage_route(&base_source, &base_destination, &approved, &route),
          "zero composition texture must fail closed");
    check(route_is_empty(&route), "zero-texture rejection must clear the route");
    approved.composition.texture = 7;
    approved.intent = (enum walle_lg_reveal_stage_intent)UINT8_C(255);
    route           = poisoned_route();
    check(!walle_lg_reveal_stage_route(&base_source, &base_destination, &approved, &route),
          "unknown intent must fail closed");
    check(route_is_empty(&route), "unknown-intent rejection must clear the route");

    route = poisoned_route();
    check(!walle_lg_reveal_stage_route(nullptr, &base_destination, nullptr, &route),
          "missing pyramid descriptor must fail closed");
    check(route_is_empty(&route), "missing pyramid descriptor must clear the route");
    route = poisoned_route();
    check(!walle_lg_reveal_stage_route(&base_source, nullptr, nullptr, &route),
          "missing destination descriptor must fail closed");
    check(route_is_empty(&route), "missing destination descriptor must clear the route");
    check(!walle_lg_reveal_stage_route(&base_source, &base_destination, nullptr, nullptr),
          "missing result storage must fail without dereferencing it");

    if (failures != 0)
        return 1;
    puts("reveal stage: bypass exact; authority closed; approved fan-out exact");
    return 0;
}
