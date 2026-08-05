import hashlib
import json
import unittest
from pathlib import Path

import numpy as np

import liquid_glass_raster_quotient_holdout as holdout


class RasterQuotientHoldoutTests(unittest.TestCase):
    def test_preregistered_prediction_recomputes_exact_hash(self):
        path = (
            Path(__file__).resolve().parent.parent
            / "lg-test"
            / "Analysis"
            / "raster_quotient_holdout_preregistration.json"
        )
        preregistration = json.loads(path.read_text(encoding="utf-8"))
        table = holdout.preregistered_prediction_table(preregistration)

        self.assertEqual(table.shape, (16, 32_768))
        self.assertEqual(
            hashlib.sha256(table.tobytes(order="C")).hexdigest(),
            preregistration["predictedTruthTable"]["sha256"],
        )

    def test_error_distribution_is_signed_float_ulp_delta(self):
        observed = np.array([10, 20, 30], dtype="<u4")
        predicted = np.array([11, 20, 28], dtype="<u4")
        self.assertEqual(
            holdout.error_distribution(observed, predicted),
            {"-1": 1, "0": 1, "2": 1},
        )


if __name__ == "__main__":
    unittest.main()
