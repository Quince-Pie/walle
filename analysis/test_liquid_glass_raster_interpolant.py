#!/usr/bin/env python3
"""Tests for the recovered Apple AGX raster iterator."""

import json
import math
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import numpy as np

import liquid_glass_raster_interpolant as raster


class RasterIteratorTests(unittest.TestCase):
    def test_exact_binary_helpers_round_ties_to_even(self) -> None:
        self.assertEqual(
            raster.float32_bits_fraction(0x3F800000),
            Fraction(1),
        )
        self.assertEqual(
            raster.float32_bits_fraction(0xBAA3D70A),
            -Fraction(5368709, 1 << 32),
        )
        halfway_to_odd = Fraction(17, 16)
        halfway_to_even = Fraction(19, 16)
        self.assertEqual(
            raster.quantize_binary_significand(
                halfway_to_odd,
                4,
            ),
            Fraction(1),
        )
        self.assertEqual(
            raster.quantize_binary_significand(
                halfway_to_even,
                4,
            ),
            Fraction(5, 4),
        )

    def test_exact_fraction_rounds_to_binary32(self) -> None:
        for bits in (
            0x3F800000,
            0x3AA3D70A,
            0x3BCE168B,
            0xBAA3D70A,
        ):
            self.assertEqual(
                raster.round_fraction_to_float32_bits(
                    raster.float32_bits_fraction(bits)
                ),
                bits,
            )
        midpoint = (
            raster.float32_bits_fraction(0x3F800000)
            + raster.float32_bits_fraction(0x3F800001)
        ) / 2
        self.assertEqual(
            raster.round_fraction_to_float32_bits(midpoint),
            0x3F800000,
        )

    def test_27_bit_inverse_area_candidate_and_falsification(
        self,
    ) -> None:
        reciprocal = raster.quantize_binary_significand(
            Fraction(1, 193 * 159),
            27,
        )
        case = raster.ProbeCase(
            root=Path("."),
            record={
                "name": "zero-based",
                "crop": {
                    "width": 193,
                    "height": 159,
                    "originX": 63,
                    "originY": 32,
                },
                "sourceEndpointBits": {
                    "left": "0x00000000",
                    "right": "0x3a800000",
                    "top": "0x00000000",
                    "bottom": "0x3c800000",
                },
            },
        )
        expected = {
            ("basis", "x"): 0x3BA9C84A,
            ("basis", "y"): 0x3BCE168B,
            ("source", "x"): 0x36A9C84A,
            ("source", "y"): 0x38CE168B,
        }
        for (kind, axis), bits in expected.items():
            observation = raster.SlopeObservation(
                case=case,
                axis=axis,
                primitive=0,
                kind=kind,
                accepted_magnitude_bits=frozenset({bits}),
            )
            self.assertEqual(
                raster.predicted_slope_bits(
                    observation,
                    reciprocal,
                ),
                bits,
            )

        falsifier = raster.ProbeCase(
            root=Path("."),
            record={
                "name": "non-power-rectangle",
                "crop": {
                    "width": 503,
                    "height": 377,
                    "originX": 37,
                    "originY": 73,
                },
                "sourceEndpointBits": {
                    "left": "0xbe800000",
                    "right": "0x3fa00000",
                    "top": "0x3d800000",
                    "bottom": "0x3f700000",
                },
            },
        )
        reciprocal = raster.quantize_binary_significand(
            Fraction(1, 503 * 377),
            27,
        )
        observation = raster.SlopeObservation(
            case=falsifier,
            axis="y",
            primitive=0,
            kind="source",
            accepted_magnitude_bits=frozenset({0x3B181B2A}),
        )
        self.assertEqual(
            raster.predicted_slope_bits(observation, reciprocal),
            0x3B181B29,
        )
        self.assertNotIn(
            raster.predicted_slope_bits(observation, reciprocal),
            observation.accepted_magnitude_bits,
        )

    def test_loads_pull_model_probe_schemas_and_rejects_older_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            for schema in (
                5,
                6,
                7,
                8,
                9,
                10,
                11,
                12,
                13,
                14,
                15,
            ):
                manifest.write_text(json.dumps({
                    "schemaVersion": schema,
                    "cases": [],
                }))
                self.assertEqual(raster.load_probe_cases(root), [])
            manifest.write_text(json.dumps({
                "schemaVersion": 4,
                "cases": [],
            }))
            with self.assertRaisesRegex(
                ValueError,
                "schema 5 through 15",
            ):
                raster.load_probe_cases(root)

    def test_round_toward_zero_differs_from_nearest(self) -> None:
        positive = 1.0 + 3.0 * 2.0**-25
        negative = -positive
        self.assertGreater(float(np.float32(positive)), positive)
        self.assertLess(float(np.float32(negative)), negative)
        self.assertEqual(
            raster.round_toward_zero_float32(positive),
            1.0,
        )
        self.assertEqual(
            raster.round_toward_zero_float32(negative),
            -1.0,
        )

    def test_pull_model_is_fused_round_to_nearest(self) -> None:
        slope = raster.float32(1.0 / 800.0)
        constant = raster.float32(0.02)
        position = 16.9375
        expected = raster.float32(
            math.fma(position, slope, constant)
        )
        self.assertEqual(
            raster.pull_iterator_bits(position, slope, constant),
            raster.float32_bits(expected),
        )

    def test_recovers_unique_tile_constant(self) -> None:
        slope = raster.float32(1.0 / 800.0)
        constant = raster.float32(0.02)
        positions = [
            coordinate + offset
            for coordinate in range(16, 32)
            for offset in raster.OFFSETS_FOUR
        ]
        targets = [
            raster.pull_iterator_bits(position, slope, constant)
            for position in positions
        ]
        self.assertEqual(
            raster.recover_constant_bits(
                positions,
                targets,
                slope,
                rounding="nearest",
            ),
            [raster.float32_bits(constant)],
        )

    def test_source_gradient_scales_endpoints_separately(self) -> None:
        low = raster.bits_float32(0x3C124925)
        high = raster.bits_float32(0x3F66DB6E)
        reciprocal = raster.float32(1.0 / 800.0)
        recovered = raster.float32(
            raster.float32(high * reciprocal)
            - raster.float32(low * reciprocal)
        )
        direct = raster.float32((high - low) / 800.0)
        self.assertEqual(raster.float32_bits(recovered), 0x3A924924)
        self.assertEqual(raster.float32_bits(direct), 0x3A924925)

    def test_axis_trace_compression_is_lossless(self) -> None:
        size = 7
        primitive = raster.raster_primitive_ids(size)
        y, x = np.indices((size, size))
        table = np.empty((2, size, 4), dtype=np.uint32)
        for primitive_id in (0, 1):
            for channel in range(4):
                table[primitive_id, :, channel] = [
                    (primitive_id + 1) * 0x1000
                    + channel * 0x100
                    + coordinate
                    for coordinate in range(size)
                ]
        active = np.empty((size, size, 4), dtype=np.uint32)
        active[..., 0] = table[primitive, x, 0]
        active[..., 1] = table[primitive, y, 1]
        active[..., 2] = table[primitive, x, 2]
        active[..., 3] = table[primitive, y, 3]

        compressed = raster.compress_axis_trace(active)

        np.testing.assert_array_equal(
            raster.reconstruct_axis_trace(compressed),
            active,
        )
        # The unreachable primitive-1 texel is canonicalized.
        np.testing.assert_array_equal(
            compressed[1, -1, :],
            compressed[0, -1, :],
        )

    def test_axis_trace_rejects_nonseparable_input(self) -> None:
        active = np.zeros((4, 4, 4), dtype=np.uint32)
        active[0, 0, 0] = 1
        with self.assertRaisesRegex(ValueError, "not separable"):
            raster.compress_axis_trace(active)


if __name__ == "__main__":
    unittest.main()
