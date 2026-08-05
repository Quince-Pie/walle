import hashlib
import json
import unittest
from pathlib import Path

import numpy as np

import liquid_glass_raster_quotient_fine_mantissa as fine


class RasterQuotientFineMantissaTests(unittest.TestCase):
    def test_input_generator_and_prediction_hash_are_preregistered(self):
        path = (
            Path(__file__).resolve().parent.parent
            / "lg-test"
            / "Analysis"
            / "raster_quotient_fine_mantissa_preregistration.json"
        )
        preregistration = json.loads(path.read_text(encoding="utf-8"))
        significands = fine.generate_significands()
        table = fine.prediction_table(preregistration, significands)

        self.assertEqual(significands.shape, (8_192,))
        self.assertEqual(np.unique(significands).size, 8_192)
        self.assertEqual(table.shape, (24, 8_192))
        self.assertEqual(
            hashlib.sha256(table.tobytes(order="C")).hexdigest(),
            fine.PREDICTED_TRUTH_SHA256,
        )

    def test_nearest_product_control_is_distinct_from_physical_model(self):
        significands = fine.generate_significands()
        physical = fine.corpus.truncated_radix2_product_bits(
            100,
            21_474_837,
            significands,
            operand_precision_bits=24,
            partial_product_truncation_bits=16,
            rounding_bias=0x14_00_00,
        )[0]
        nearest = fine.nearest_product27_bits(
            100,
            21_474_837,
            significands,
        )
        self.assertEqual(
            int(np.count_nonzero(physical != nearest)),
            93,
        )


if __name__ == "__main__":
    unittest.main()
