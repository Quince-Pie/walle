import unittest

import numpy as np

from liquid_glass_clear_geometry_fit import (
    ShapeGeometry,
    equality_metrics,
    refine_threshold,
    threshold_brackets,
)


class LiquidGlassClearGeometryFitTests(unittest.TestCase):
    def test_circle_coordinate_is_normalized_radius(self) -> None:
        geometry = ShapeGeometry(
            kind="circle",
            center_x=10.0,
            center_y=20.0,
            half_width=100.0,
            half_height=100.0,
            corner_radius=100.0,
        )

        coordinate = geometry.normalized_signed_distance(
            np.asarray([10.0, 70.0, 110.0]),
            np.asarray([20.0, 100.0, 20.0]),
        )

        np.testing.assert_allclose(coordinate, [0.0, 1.0, 1.0])

    def test_rectangle_coordinate_uses_the_nearest_physical_boundary(self) -> None:
        geometry = ShapeGeometry(
            kind="roundedRect",
            center_x=0.0,
            center_y=0.0,
            half_width=3000.0,
            half_height=2000.0,
            corner_radius=0.0,
        )

        coordinate = geometry.normalized_signed_distance(
            np.asarray([1000.0, 0.0, 2500.0]),
            np.asarray([0.0, 500.0, 0.0]),
        )

        np.testing.assert_allclose(coordinate, [0.0, 0.25, 0.75])

    def test_rounded_rectangle_center_depth_is_its_inradius(self) -> None:
        geometry = ShapeGeometry(
            kind="roundedRect",
            center_x=0.0,
            center_y=0.0,
            half_width=300.0,
            half_height=200.0,
            corner_radius=80.0,
        )

        coordinate = geometry.normalized_signed_distance(
            np.asarray([0.0]),
            np.asarray([0.0]),
        )

        np.testing.assert_allclose(coordinate, [0.0])

    def test_threshold_refinement_recovers_a_synthetic_boundary(self) -> None:
        first = np.linspace(0.3, 0.45, 1501).reshape(1, -1)
        second = np.full_like(first, 0.4)
        threshold = 0.37
        equal = (first < threshold) == (second < threshold)

        record = refine_threshold(
            {"a": first, "b": second},
            {("a", "b"): equal},
            lower=0.3,
            upper=0.45,
            step=1e-4,
        )

        self.assertAlmostEqual(record["selected"], threshold, places=4)

    def test_equality_metrics_keeps_state_and_output_events_separate(self) -> None:
        metrics = equality_metrics(
            np.asarray([True, True, False, False]),
            np.asarray([True, False, True, False]),
        )

        self.assertEqual(metrics["sameStatePixels"], 2)
        self.assertEqual(metrics["differentStatePixels"], 2)
        self.assertEqual(metrics["equalOutputGivenSameState"], 0.5)
        self.assertEqual(metrics["equalOutputGivenDifferentState"], 0.5)

    def test_threshold_brackets_are_disjoint_and_cover_the_unit_interval(
        self,
    ) -> None:
        brackets = threshold_brackets()

        self.assertEqual(brackets[0][0], 0.0)
        self.assertEqual(brackets[-1][1], 1.0)
        self.assertTrue(
            all(
                left[1] == right[0]
                for left, right in zip(brackets, brackets[1:])
            )
        )


if __name__ == "__main__":
    unittest.main()
