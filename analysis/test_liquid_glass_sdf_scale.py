import unittest

import numpy as np

from liquid_glass_lod_cross_match import CandidateBounds
from liquid_glass_sdf_scale import (
    PATCH_SIDE,
    SCALE_DENOMINATOR,
    SCALE_HALF_BITS_MAXIMUM,
    SCALE_HALF_BITS_MINIMUM,
    SCALE_NUMERATOR_MAXIMUM,
    SCALE_NUMERATOR_MINIMUM,
    SITE_COUNT,
    STATE_COUNT,
    _air_profile_scale,
    _candidate_summary,
    _expected_filter_values,
    _expected_scale_state,
    _normalized_circle_prediction,
    _prediction_metrics,
    _spatial_coordinates,
    _unique_radial_fit,
)


class ScaleCatalogTests(unittest.TestCase):
    def test_catalog_covers_every_binary16_value(self) -> None:
        states = [
            _expected_scale_state(index, numerator)
            for index, numerator in enumerate(
                range(
                    SCALE_NUMERATOR_MINIMUM,
                    SCALE_NUMERATOR_MAXIMUM + 1,
                )
            )
        ]

        self.assertEqual(len(states), STATE_COUNT)
        self.assertEqual(
            states[0]["constantBlurOpacityScaleFloat16Bits"],
            f"{SCALE_HALF_BITS_MINIMUM:04x}",
        )
        self.assertEqual(
            states[-1]["constantBlurOpacityScaleFloat16Bits"],
            f"{SCALE_HALF_BITS_MAXIMUM:04x}",
        )
        self.assertEqual(
            states[0]["constantBlurOpacityScale"],
            SCALE_NUMERATOR_MINIMUM / SCALE_DENOMINATOR,
        )
        self.assertEqual(
            states[-1]["constantBlurOpacityScale"],
            1,
        )
        self.assertFalse(states[0]["productionScale"])
        self.assertTrue(states[-1]["productionScale"])

    def test_filter_profile_is_constant(self) -> None:
        values = _expected_filter_values(0.875)

        self.assertEqual(
            [values[f"inputBlurOpacity{index}"] for index in range(5)],
            [0.875] * 5,
        )
        self.assertEqual(values["inputBlurRadius"], 4)
        self.assertEqual(values["inputInnerRefractionAmount"], -60)
        self.assertEqual(values["inputOuterRefractionAmount"], 160)
        self.assertEqual(values["inputRefractionOpacity"], 0)

    def test_pinned_profile_preserves_dormant_maxima(self) -> None:
        state = _expected_scale_state(
            0,
            SCALE_NUMERATOR_MINIMUM,
            pinned_pyramid=True,
        )
        values = _expected_filter_values(
            0.875,
            pinned_pyramid=True,
        )

        self.assertEqual(
            state["name"],
            f"pinned-sdf-scale-half-{SCALE_HALF_BITS_MINIMUM:04x}",
        )
        self.assertTrue(state["pinnedPyramidProfile"])
        self.assertEqual(
            [values[f"inputBlurOpacity{index}"] for index in range(5)],
            [0.875, 0.875, 1, 1, 1],
        )
        self.assertEqual(
            [values[f"inputBlurDistance{index}"] for index in range(5)],
            [-400, -1, 0, 0, 0],
        )


class AirProfileTests(unittest.TestCase):
    def test_profile_endpoints_match_binary16_arithmetic(self) -> None:
        bits = _air_profile_scale(
            np.asarray([-400, -1, 0], dtype=np.float32)
        )

        self.assertEqual(bits.tolist(), [0x3C00, 0x3800, 0x3800])

    def test_spatial_coordinates_cover_each_patch(self) -> None:
        design = {
            "sites": [
                {"x": 100 + index, "y": 200 + index}
                for index in range(SITE_COUNT)
            ]
        }

        x, y = _spatial_coordinates(design, offset=0.5)

        self.assertEqual(x.shape, (SITE_COUNT * PATCH_SIDE**2,))
        self.assertEqual(y.shape, x.shape)
        self.assertEqual(x[0], 60.5)
        self.assertEqual(y[0], 160.5)
        self.assertEqual(x[PATCH_SIDE**2 - 1], 140.5)
        self.assertEqual(y[PATCH_SIDE**2 - 1], 240.5)

    def test_normalized_circle_prediction_stays_in_catalog(self) -> None:
        design = {
            "sites": [
                {"x": 472, "y": 472}
                for _ in range(SITE_COUNT)
            ]
        }
        shape = {
            "centerX": 512,
            "centerY": 512,
            "diameter": 4000,
        }

        bits = _normalized_circle_prediction(
            design,
            shape,
            offset=0,
        )

        self.assertEqual(
            bits.shape,
            (SITE_COUNT * PATCH_SIDE**2,),
        )
        self.assertGreaterEqual(
            int(bits.min()),
            SCALE_HALF_BITS_MINIMUM,
        )
        self.assertLessEqual(
            int(bits.max()),
            SCALE_HALF_BITS_MAXIMUM,
        )


class ExactMatchingReportTests(unittest.TestCase):
    def test_prediction_metrics_use_direct_word_equality(self) -> None:
        spatial_count = 3
        catalog = np.zeros(
            (STATE_COUNT, spatial_count, 2),
            dtype=np.uint64,
        )
        selected = np.asarray([0, 17, STATE_COUNT - 1])
        spatial = np.arange(spatial_count)
        catalog[selected, spatial, 0] = [11, 22, 33]
        catalog[selected, spatial, 1] = [44, 55, 66]
        probe = catalog[selected, spatial].copy()
        predicted = np.asarray(
            SCALE_HALF_BITS_MINIMUM + selected,
            dtype=np.uint16,
        )

        report = _prediction_metrics(
            predicted,
            probe,
            catalog,
        )

        self.assertTrue(report["allSignaturesExact"])
        self.assertEqual(report["exactSignatureMatches"], 3)

        probe[1, 1] ^= np.uint64(1)
        report = _prediction_metrics(
            predicted,
            probe,
            catalog,
        )
        self.assertFalse(report["allSignaturesExact"])
        self.assertEqual(report["exactSignatureMatches"], 2)

    def test_candidate_summary_distinguishes_noncontiguous_sets(self) -> None:
        spatial_count = SITE_COUNT * PATCH_SIDE**2
        count = np.ones((1, spatial_count), dtype=np.uint16)
        lower = np.zeros_like(count)
        upper = np.zeros_like(count)
        count[0, 0] = 2
        upper[0, 0] = 2
        bounds = CandidateBounds(
            count=count,
            lower=lower,
            upper=upper,
        )

        report = _candidate_summary(bounds)

        self.assertEqual(report["ambiguousScaleSignatures"], 1)
        self.assertEqual(
            report["noncontiguousCandidateSignatures"],
            1,
        )
        self.assertFalse(report["allCandidateSetsContiguous"])

    def test_empty_unique_radial_fit_is_finite_json_data(self) -> None:
        spatial_count = SITE_COUNT * PATCH_SIDE**2
        count = np.full(
            (1, spatial_count),
            2,
            dtype=np.uint16,
        )
        bounds = CandidateBounds(
            count=count,
            lower=np.zeros_like(count),
            upper=np.ones_like(count),
        )
        design = {
            "sites": [
                {"x": 472, "y": 472}
                for _ in range(SITE_COUNT)
            ]
        }

        report = _unique_radial_fit(bounds, design)

        self.assertEqual(report["identifiedUniqueSignatures"], 0)
        self.assertIsNone(report["scaleIntercept"])
        self.assertIsNone(report["rootMeanSquareScaleResidual"])


if __name__ == "__main__":
    unittest.main()
