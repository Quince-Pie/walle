import unittest

import liquid_glass_exact_specialization_gate as gate


class ExactSpecializationGateTests(unittest.TestCase):
    def test_distribution_uses_nearest_rank_samples(self) -> None:
        result = gate.distribution([5.0, 1.0, 4.0, 2.0, 3.0])

        self.assertEqual(result["sampleCount"], 5)
        self.assertEqual(result["minimum"], 1.0)
        self.assertEqual(result["median"], 3.0)
        self.assertEqual(result["maximum"], 5.0)

    def test_distribution_rejects_empty_measurement(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            gate.distribution([])

    def test_fixture_matrix_covers_both_materials_and_appearances(self) -> None:
        fixtures = gate.default_fixtures()

        self.assertEqual(
            {fixture.name for fixture in fixtures},
            {"clear-light", "clear-dark", "regular-light", "regular-dark"},
        )
        self.assertEqual(len(fixtures), 4)

    def test_regular_requires_twenty_percent_median_reduction(self) -> None:
        self.assertFalse(gate.performance_gate_passed("regular", 19.999))
        self.assertTrue(gate.performance_gate_passed("regular", 20.0))

    def test_clear_permits_at_most_two_percent_median_regression(self) -> None:
        self.assertFalse(gate.performance_gate_passed("clear", -2.001))
        self.assertTrue(gate.performance_gate_passed("clear", -2.0))
        self.assertTrue(gate.performance_gate_passed("clear", 0.0))


if __name__ == "__main__":
    unittest.main()
