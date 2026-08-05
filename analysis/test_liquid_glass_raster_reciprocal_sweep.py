import unittest

import liquid_glass_raster_reciprocal_sweep as sweep


class RasterReciprocalSweepTests(unittest.TestCase):
    def test_width_roles_and_hashes_are_frozen(self):
        discovery = sweep.selected_widths(holdout=False)
        holdout = sweep.selected_widths(holdout=True)

        self.assertEqual(len(discovery), sweep.DISCOVERY_WIDTH_COUNT)
        self.assertEqual(
            sweep.uint32_sha256(discovery),
            sweep.DISCOVERY_WIDTHS_SHA256,
        )
        self.assertEqual(len(holdout), sweep.HOLDOUT_WIDTH_COUNT)
        self.assertEqual(
            sweep.uint32_sha256(holdout),
            sweep.HOLDOUT_WIDTHS_SHA256,
        )

    def test_normalized_width_classes_do_not_cross_roles(self):
        roles_by_class: dict[int, set[bool]] = {}
        for width in range(sweep.WIDTH_LOWER, sweep.WIDTH_UPPER + 1):
            roles_by_class.setdefault(
                sweep.normalization_class(width),
                set(),
            ).add(sweep.is_holdout_width(width))

        self.assertTrue(all(len(roles) == 1 for roles in roles_by_class.values()))
        self.assertTrue(
            all(sweep.is_holdout_width(width) for width in sweep.PRODUCTION_HOLDOUT_WIDTHS)
        )

    def test_position_rule_is_interior_and_visible(self):
        for width in (128, 184, 10_045, 16_384):
            positions = sweep.expected_positions(width)
            self.assertGreaterEqual(len(positions), 4)
            for position in positions:
                self.assertTrue(0 <= int(position["x"]) < sweep.TARGET_WIDTH)
                self.assertTrue(0 <= int(position["y"]) < sweep.TARGET_HEIGHT)


if __name__ == "__main__":
    unittest.main()
