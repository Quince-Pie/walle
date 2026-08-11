#include "liquid_glass_reveal_stage.h"

#include <stddef.h>
#include <stdint.h>

struct walle_lg_reveal_stage_authority
{
    uint64_t seal[2];
};

struct reveal_authority_evidence
{
    bool     exact_selector_approved;
    bool     walle_composition_holdout_approved;
    uint64_t holdout_checked_pixels;
    uint64_t holdout_mismatched_pixels;
    char     selector_result_sha256[65];
    char     holdout_result_sha256[65];
};

/*
 * Fail-closed production authority.  These fields may change only after the
 * portable selector is frozen and a separately preregistered holdout reports
 * zero mismatched pixels.  Merely implementing a candidate does not make an
 * authority capability obtainable.
 */
static const struct reveal_authority_evidence approved_evidence = {
    .exact_selector_approved            = false,
    .walle_composition_holdout_approved = false,
    .holdout_checked_pixels             = 0,
    .holdout_mismatched_pixels          = UINT64_MAX,
    .selector_result_sha256             = "",
    .holdout_result_sha256              = "",
};

static const struct walle_lg_reveal_stage_authority approved_authority = {
    .seal = {UINT64_C(0x7eece9bc825d5b11), UINT64_C(0xc84806477e7035d3)},
};

static bool is_lower_hex_sha256(const char digest[static 65])
{
    for (size_t index = 0; index < 64; ++index) {
        char value = digest[index];
        if (!((value >= '0' && value <= '9') || (value >= 'a' && value <= 'f')))
            return false;
    }
    return digest[64] == '\0';
}

static bool production_authority_ready(void)
{
    return approved_evidence.exact_selector_approved
           && approved_evidence.walle_composition_holdout_approved
           && approved_evidence.holdout_checked_pixels > 0
           && approved_evidence.holdout_mismatched_pixels == 0
           && is_lower_hex_sha256(approved_evidence.selector_result_sha256)
           && is_lower_hex_sha256(approved_evidence.holdout_result_sha256);
}

const struct walle_lg_reveal_stage_authority*
walle_lg_reveal_stage_authority_acquire(void)
{
    return production_authority_ready() ? &approved_authority : nullptr;
}

#if defined(WALLE_LG_REVEAL_STAGE_TESTING)
static const struct walle_lg_reveal_stage_authority testing_authority = {
    .seal = {UINT64_C(0x3eb7b92ecbed19ed), UINT64_C(0x9a4ef611050ba789)},
};

const struct walle_lg_reveal_stage_authority*
walle_lg_reveal_stage_testing_authority(void)
{
    return &testing_authority;
}
#endif

static bool authority_is_approved(const struct walle_lg_reveal_stage_authority* authority)
{
    /* Do not call the exported acquire() symbol here: an interposable ELF
     * definition must never be able to mint authority for the guard itself. */
    if (production_authority_ready() && authority == &approved_authority)
        return true;
#if defined(WALLE_LG_REVEAL_STAGE_TESTING)
    return authority == &testing_authority;
#else
    return false;
#endif
}

bool walle_lg_reveal_stage_route(
    const struct walle_lg_gl_texture_view* base_pyramid_source,
    const struct walle_lg_gl_texture_view* base_destination,
    const struct walle_lg_reveal_stage_request* request,
    struct walle_lg_gl_frame_inputs* result)
{
    if (result == nullptr)
        return false;

    *result = (struct walle_lg_gl_frame_inputs){};
    if (base_pyramid_source == nullptr || base_destination == nullptr)
        return false;

    if (request == nullptr || request->intent == WALLE_LG_REVEAL_STAGE_DISABLED) {
        result->pyramid_source = *base_pyramid_source;
        result->destination    = *base_destination;
        result->reveal_applied = false;
        return true;
    }

    if (request->intent != WALLE_LG_REVEAL_STAGE_EXACT
        || !authority_is_approved(request->authority)
        || request->composition.texture == 0 || request->composition.width == 0
        || request->composition.height == 0
        || request->composition.width != base_pyramid_source->width
        || request->composition.height != base_pyramid_source->height
        || request->composition.width != base_destination->width
        || request->composition.height != base_destination->height) {
        return false;
    }

    result->pyramid_source = request->composition;
    result->destination    = request->composition;
    result->reveal_applied = true;
    return true;
}
