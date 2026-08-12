#!/usr/bin/env python3
"""Tests for dynamic Liquid Glass crop/allocation recovery."""

import unittest

import liquid_glass_dynamic_allocation_policy as allocation


GEOMETRY = {
    "centerX": 512,
    "centerY": 512,
    "height": 800,
    "shape": "circle",
    "width": 800,
    "windowHeight": 1_024,
    "windowWidth": 1_024,
}


class AlignmentTests(unittest.TestCase):
    def test_allocation_boundary_is_inclusive_of_clamp_index(self) -> None:
        self.assertEqual(allocation.align_up(448), 448)
        self.assertEqual(allocation.align_up(449), 512)

    def test_rejects_empty_extent(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            allocation.align_up(0)


class PolicyTests(unittest.TestCase):
    def test_new_sample_28_stays_on_512_by_512_producer(self) -> None:
        predicted = allocation.predict_policy(
            GEOMETRY,
            remaining=0.8749866485595703,
            scale=0.5625066757202148,
        )
        self.assertEqual(predicted["cropOrigin"], [92, 35])
        self.assertEqual(
            predicted["textureCoordinateClamp"],
            [0, 0, 448, 448],
        )
        self.assertEqual(predicted["producerExtent"], [512, 512])
        self.assertEqual(predicted["destinationExtent"], [512, 512])

    def test_old_sample_28_exposes_independent_axis_transition(self) -> None:
        predicted = allocation.predict_policy(
            GEOMETRY,
            remaining=0.8760004043579102,
            scale=0.5619997978210449,
        )
        self.assertEqual(predicted["cropOrigin"], [91, 36])
        self.assertEqual(
            predicted["textureCoordinateClamp"],
            [0, 0, 448, 447],
        )
        self.assertEqual(predicted["producerExtent"], [512, 448])
        self.assertEqual(predicted["destinationExtent"], [512, 512])

    def test_clear_endpoint_uses_integral_axis_conventions(self) -> None:
        predicted = allocation.predict_policy(
            GEOMETRY,
            remaining=1.0,
            scale=0.5,
        )
        self.assertEqual(predicted["cropOrigin"], [57, 56])
        self.assertEqual(
            predicted["textureCoordinateClamp"],
            [0, 0, 398, 399],
        )
        self.assertEqual(predicted["producerExtent"], [448, 448])
        self.assertEqual(predicted["destinationExtent"], [448, 448])

    def test_zero_remaining_is_outside_retained_dynamic_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "remaining"):
            allocation.predict_policy(
                GEOMETRY,
                remaining=0.0,
                scale=1.0,
            )


if __name__ == "__main__":
    unittest.main()
