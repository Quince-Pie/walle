import unittest

import numpy as np

from apple_glass_reference_renderer import DrawGeometry
from liquid_glass_transition_geometry import transition_circle_geometry


def _main_template() -> DrawGeometry:
    return DrawGeometry(
        vertices=np.array(
            [
                (0, 0, 0, 1, -50, -50, 0, 1),
                (0, 0, 0, 1, 50, -50, 1, 1),
                (0, 0, 0, 1, 50, 50, 1, 0),
                (0, 0, 0, 1, 50, 50, 1, 0),
                (0, 0, 0, 1, -50, 50, 0, 0),
                (0, 0, 0, 1, -50, -50, 0, 1),
            ],
            dtype=np.float32,
        ),
        indices=None,
    )


class LiquidGlassTransitionGeometryTests(unittest.TestCase):
    def test_transition_displacement_precedes_metal_y_inversion(self) -> None:
        template = _main_template()
        geometry = transition_circle_geometry(
            main_template=template,
            shadow_template=template,
            diameter=100,
            requested_center=(600.25, 300.25),
            window_extent=(1000, 800),
            remaining=0.25,
        )

        self.assertEqual(geometry.effect_center, (599.5, 299.5))
        self.assertEqual(geometry.metal_effect_center, (599.5, 500.5))
        np.testing.assert_array_equal(
            geometry.main.vertices[:, :2],
            np.array(
                [
                    (543.5, 556.5),
                    (655.5, 556.5),
                    (655.5, 444.5),
                    (655.5, 444.5),
                    (543.5, 444.5),
                    (543.5, 556.5),
                ],
                dtype=np.float32,
            ),
        )

    def test_endpoint_uses_snapped_swiftui_center_in_metal_space(self) -> None:
        template = _main_template()
        geometry = transition_circle_geometry(
            main_template=template,
            shadow_template=template,
            diameter=100,
            requested_center=(600.25, 300.25),
            window_extent=(1000, 800),
            remaining=1,
        )

        self.assertEqual(geometry.effect_center, (600, 300))
        self.assertEqual(geometry.metal_effect_center, (600, 500))


if __name__ == "__main__":
    unittest.main()
