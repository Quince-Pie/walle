#!/usr/bin/env python3
"""Tests for Apple raster reciprocal tomography analysis."""

import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_tomography as tomography


class RasterTomographyTests(unittest.TestCase):
    def test_signed_rounding_interval_mirrors_negative_values(
        self,
    ) -> None:
        positive = tomography.signed_float32_rounding_interval(
            0x3F000000
        )
        negative = tomography.signed_float32_rounding_interval(
            0xBF000000
        )
        self.assertEqual(negative, (-positive[1], -positive[0]))
        zero = tomography.signed_float32_rounding_interval(0)
        self.assertEqual(
            zero,
            (
                -raster.power_of_two(-150),
                raster.power_of_two(-150),
            ),
        )

    def test_exact_pull_constant_recovers_fused_plane(self) -> None:
        slope_bits = 0x3B800001
        constant_bits = 0x3D000003
        slope = raster.bits_float32(slope_bits)
        constant = raster.bits_float32(constant_bits)
        positions = [
            coordinate + offset
            for coordinate in range(4, 28)
            for offset in (0.0, tomography.PULL_OFFSET)
        ]
        targets = [
            raster.pull_iterator_bits(
                position,
                slope,
                constant,
            )
            for position in positions
        ]
        recovered = tomography.exact_pull_constant(
            positions,
            targets,
            slope_bits,
        )
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered[0], constant_bits)

    def test_loader_keeps_holdout_closed_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [
                {"name": "train", "role": "discovery"},
                {"name": "test", "role": "holdout"},
            ]
            (root / "manifest.json").write_text(json.dumps({
                "schemaVersion": 8,
                "reciprocalTomographyCases": records,
            }))
            selected = tomography.load_tomography_cases(root)
            self.assertEqual(
                [case.name for case in selected],
                ["train"],
            )
            self.assertEqual(
                [
                    case.name
                    for case in tomography.load_tomography_cases(
                        root,
                        role="holdout",
                    )
                ],
                ["test"],
            )

    def test_staged_product_uses_27_bit_intermediate(
        self,
    ) -> None:
        case = tomography.TomographyCase(
            root=Path("."),
            record={
                "name": "synthetic",
                "role": "discovery",
                "crop": {
                    "width": 67,
                    "height": 71,
                    "originX": 3,
                    "originY": 5,
                },
                "deltaNumerators": [52625],
                "deltaDenominator": 65536,
            },
        )
        reciprocal = raster.quantize_binary_significand(
            Fraction(1, 67 * 71),
            29,
        )
        provisional = tomography.TomographySlope(
            case=case,
            delta_index=0,
            axis="x",
            primitive=0,
            accepted_bits=frozenset({1}),
        )
        expected = tomography.staged_product_bits(
            provisional,
            reciprocal,
            product_precision_bits=27,
            product_rounding="nearest-even",
        )
        observation = tomography.TomographySlope(
            case=case,
            delta_index=0,
            axis="x",
            primitive=0,
            accepted_bits=frozenset({expected}),
        )
        self.assertEqual(
            tomography.staged_product_bits(
                observation,
                reciprocal,
                product_precision_bits=27,
                product_rounding="nearest-even",
            ),
            expected,
        )

    def test_staged_matching_offsets_requires_observations(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "slope observations are required",
        ):
            tomography.staged_matching_offsets(
                [],
                reciprocal_precision_bits=29,
            )


if __name__ == "__main__":
    unittest.main()
