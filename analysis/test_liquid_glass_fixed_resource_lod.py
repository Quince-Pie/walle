import unittest

import numpy as np

from liquid_glass_fixed_resource_lod import (
    RADIUS_FOUR_SCALE_ONE_STATE,
    STATE_COUNT,
    _constant_values,
    _expected_state_catalog,
    response_signatures,
)
from liquid_glass_lod_sweep import (
    AMPLITUDES,
    CHANNELS,
    PATCH_SIDE,
    SITE_COUNT,
)


class FixedResourceCatalogTests(unittest.TestCase):
    def test_catalog_has_expected_resource_boundaries(self) -> None:
        states = _expected_state_catalog()
        self.assertEqual(len(states), STATE_COUNT)
        self.assertEqual(states[0]["resourceBlurRadius"], 1)
        self.assertEqual(states[38]["targetEffectiveBlurRadius"], 1)
        self.assertTrue(states[38]["productionEffectiveRadius"])
        self.assertEqual(states[39]["resourceBlurRadius"], 4)
        self.assertEqual(
            states[RADIUS_FOUR_SCALE_ONE_STATE][
                "targetEffectiveBlurRadius"
            ],
            4,
        )

    def test_constant_values_hold_every_blur_opacity_equal(self) -> None:
        values = _constant_values(
            resource_radius=4,
            scale=0.625,
        )
        self.assertEqual(
            {values[f"inputBlurOpacity{index}"] for index in range(5)},
            {0.625},
        )
        self.assertEqual(values["inputBlurRadius"], 4)
        self.assertEqual(values["inputInnerRefractionAmount"], -60)
        self.assertEqual(values["inputOuterRefractionAmount"], 160)
        self.assertEqual(values["inputRefractionOpacity"], 0)

    def test_response_signatures_preserve_every_native_byte(
        self,
    ) -> None:
        stream = np.arange(
            len(AMPLITUDES)
            * 2
            * SITE_COUNT
            * PATCH_SIDE
            * PATCH_SIDE
            * CHANNELS,
            dtype=np.uint8,
        ).reshape(
            len(AMPLITUDES),
            2,
            SITE_COUNT,
            PATCH_SIDE,
            PATCH_SIDE,
            CHANNELS,
        )
        signatures = response_signatures(stream)
        self.assertEqual(
            signatures.shape,
            (
                2,
                SITE_COUNT * PATCH_SIDE**2,
                len(AMPLITUDES) * CHANNELS,
            ),
        )
        reconstructed = np.transpose(
            signatures.reshape(
                2,
                SITE_COUNT,
                PATCH_SIDE,
                PATCH_SIDE,
                len(AMPLITUDES),
                CHANNELS,
            ),
            (4, 0, 1, 2, 3, 5),
        )
        np.testing.assert_array_equal(reconstructed, stream)


if __name__ == "__main__":
    unittest.main()
