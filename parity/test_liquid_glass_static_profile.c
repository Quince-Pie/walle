#include "liquid_glass_static_profile.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

static const char clear_hex[] =
    "0000c8430000c84300008040000000000000803f00000000000000000000803f"
    "0000803f0000803f0000c843000000002549923a0000000000000000254992ba"
    "000070c2cdcc4c3d000020430ad7233c000080bf00000000cdcc4c3f00000000"
    "000000000000807f00009642cdcc4c3b00000000000000c1000000000000807f"
    "413c7421c214cd2c8b1a483c5714cd2c6b1a85213f3ccd2cfc3b70b962ac003a"
    "76b22a3864ac003a77b26fb9873c003a1d3a0caed9a000002fa79539e3a00000"
    "32a70aae423a0000000000007b14ae3e003c0038000000b840de00bc00000000"
    "003c00000000003c003c0000000000000000003c00c000bca03c0000c33b0000"
    "003c";

static const char regular_light_hex[] =
    "0000c8430000c843000080400000003f0000803f00000000000000000000803f"
    "0000803f0000803f0000c84300000000abaa2a3a0000000000000000abaa2aba"
    "000070c2cdcc4c3d000020430ad7233c000080bf00000000cdcccc3f00000042"
    "00008c43a10e6a3b00009642cdcc4c3b00000000000000c100000041abaa2a3d"
    "523875b233a59a39acab603637a59a39aeab74b2a3389a39bb3b4bba14ad333b"
    "7cb39d3616ad333b7cb34bba7b3c333b2c3cdcb5b9a80000f7ae493abca80000"
    "f8aedcb5763c00000000803feb51b83e003c0038000000b840de00bc00000000"
    "003c00000038003c003c000000000034cd34003c00c000bc203c0000c33b0000"
    "003c";

static const char regular_dark_hex[] =
    "0000c8430000c843000080400000003f0000803f00000000000000000000803f"
    "0000803f0000803f0000c84300000000abaa2a3a0000000000000000abaa2aba"
    "000070c2cdcc4c3d000020430ad7233c000080bf00000000cdcccc3f00000042"
    "00008c43a10e6a3b00009642cdcc4c3b00000000000000c100000041abaa2a3d"
    "30381fb4a4a6ae2fe6ac7b35a8a6ae2fe6ac1eb49838ae2f263bb9b59ca80000"
    "ceae2439a0a80000ceaeb9b5b63b00006f3959b402a700002cadd03707a70000"
    "2cad59b4dc3900000000803f8fc2753e003c0038000000b840de00bc00000000"
    "003c0000663a003c00bc003c00000034cd34003c00c000bc003c0000c33b0000"
    "003c";

static unsigned hex_nibble(char value)
{
    if (value >= '0' && value <= '9') {
        return (unsigned)(value - '0');
    }
    return (unsigned)(value - 'a') + 10u;
}

static int check_profile(
    enum walle_lg_material material,
    enum walle_lg_appearance appearance,
    const char expected[static WALLE_LG_PROFILE_PAYLOAD_BYTE_COUNT * 2 + 1]
)
{
    uint32_t extent = material == WALLE_LG_MATERIAL_REGULAR ? 1536u : 896u;
    struct walle_lg_static_profile_request request = {
        .material = material,
        .appearance = appearance,
        .width = 800.0f,
        .height = 800.0f,
        .source_virtual_width = extent,
        .source_virtual_height = extent,
    };
    struct walle_lg_profile_payload actual;
    if (strlen(expected) != WALLE_LG_PROFILE_PAYLOAD_BYTE_COUNT * 2
        || !walle_lg_static_profile(&request, &actual)) {
        return 1;
    }
    for (size_t index = 0; index < sizeof actual.byte; ++index) {
        unsigned high = hex_nibble(expected[index * 2]);
        unsigned low = hex_nibble(expected[index * 2 + 1]);
        if (actual.byte[index] != (uint8_t)((high << 4) | low)) {
            fprintf(stderr, "profile byte %zu differs\n", index);
            return 1;
        }
    }
    return 0;
}

int main(void)
{
    int failures = 0;
    failures += check_profile(
        WALLE_LG_MATERIAL_CLEAR,
        WALLE_LG_APPEARANCE_LIGHT,
        clear_hex
    );
    failures += check_profile(
        WALLE_LG_MATERIAL_CLEAR,
        WALLE_LG_APPEARANCE_DARK,
        clear_hex
    );
    failures += check_profile(
        WALLE_LG_MATERIAL_REGULAR,
        WALLE_LG_APPEARANCE_LIGHT,
        regular_light_hex
    );
    failures += check_profile(
        WALLE_LG_MATERIAL_REGULAR,
        WALLE_LG_APPEARANCE_DARK,
        regular_dark_hex
    );

    struct walle_lg_static_profile_request invalid = {
        .material = WALLE_LG_MATERIAL_CLEAR,
        .appearance = WALLE_LG_APPEARANCE_LIGHT,
        .width = 0.0f,
        .height = 800.0f,
        .source_virtual_width = 896,
        .source_virtual_height = 896,
    };
    struct walle_lg_profile_payload payload;
    if (walle_lg_static_profile(&invalid, &payload)) {
        ++failures;
    }

    if (failures == 0) {
        puts("static profile: 1,032/1,032 exact bytes");
    }
    return failures != 0;
}
