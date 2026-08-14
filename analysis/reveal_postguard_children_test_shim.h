#ifndef WALLE_ANALYSIS_REVEAL_POSTGUARD_CHILDREN_TEST_SHIM_H
#define WALLE_ANALYSIS_REVEAL_POSTGUARD_CHILDREN_TEST_SHIM_H

#include <stdint.h>

[[nodiscard]]
uint32_t walle_test_postguard_construct(uint8_t         family,
                                        uint32_t        width,
                                        uint32_t        height,
                                        uint32_t        vertex_count,
                                        uint32_t        index_count,
                                        const uint32_t* vertex_bits,
                                        const uint16_t* indices,
                                        uint32_t        child_capacity,
                                        uint32_t        guard_bits[4],
                                        uint32_t*       child_count,
                                        uint32_t*       child_bits,
                                        uint8_t*        child_metadata);

#endif
