import unittest

import numpy as np

from liquid_glass_resource_confound import (
    LOD_DENOMINATOR,
    RADIUS_ONE_GRID_STATES,
    half_endpoint_candidate_count,
    native_rgb8,
    rgba8_endpoint_candidate_count,
)
from liquid_glass_sampler_probe import half_round_ties_up


class FixedEndpointCandidateTests(unittest.TestCase):
    def test_known_half_endpoint_pair_is_retained(self) -> None:
        numerators = np.arange(
            RADIUS_ONE_GRID_STATES,
            dtype=np.float64,
        )
        first = np.float16(0.5)
        second = np.float16(0.625)
        exact = (
            (LOD_DENOMINATOR - numerators) * float(first)
            + numerators * float(second)
        ) / LOD_DENOMINATOR
        observations = native_rgb8(
            half_round_ties_up(exact)
        )

        self.assertGreater(
            half_endpoint_candidate_count(observations),
            0,
        )

    def test_known_rgba8_endpoint_pair_is_retained(self) -> None:
        numerators = np.arange(
            RADIUS_ONE_GRID_STATES,
            dtype=np.float64,
        )
        exact_codes = (
            (LOD_DENOMINATOR - numerators) * 128
            + numerators * 160
        ) / LOD_DENOMINATOR
        fixed_codes = np.floor(
            exact_codes * 16 + 0.5
        ) / 16
        observations = native_rgb8(
            (fixed_codes / 255).astype(np.float16)
        )

        self.assertGreater(
            rgba8_endpoint_candidate_count(observations),
            0,
        )


if __name__ == "__main__":
    unittest.main()
