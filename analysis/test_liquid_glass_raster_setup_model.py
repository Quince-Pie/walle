import unittest
from fractions import Fraction

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_setup_model as setup


class RasterSetupModelTests(unittest.TestCase):
    def test_staged_model_uses_named_internal_precisions(self) -> None:
        delta = Fraction(513, 1024)
        dimension = 56
        reciprocal = raster.quantize_binary_significand(
            Fraction(1, dimension),
            25,
        )
        product = raster.quantize_binary_significand(
            delta * reciprocal,
            27,
        )
        self.assertEqual(
            setup.reciprocal_25_product_27(delta, dimension),
            raster.round_fraction_to_float32_bits(product),
        )

    def test_model_report_preserves_signed_ulp_errors(self) -> None:
        samples: list[setup.JsonObject] = [
            {
                "baseCase":
                    "tomography-discovery-factor-h064-w056",
                "axisDimension": 56,
                "numeratorIndex": 0,
                "deltaNumerator": 513,
                "deltaDenominator": 1024,
                "observedBits": "0x3c12db6e",
            },
        ]
        predicted = setup.correctly_rounded_divide(
            Fraction(513, 1024),
            56,
        )
        samples[0]["observedBits"] = f"0x{predicted + 1:08x}"
        report = setup.model_report(
            samples,
            setup.correctly_rounded_divide,
        )
        self.assertEqual(report["matchCount"], 0)
        self.assertEqual(
            report["floatUlpErrorDistribution"],
            {"1": 1},
        )
        self.assertEqual(
            report["mismatches"][0][
                "observedMinusPredictedFloatUlp"
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()
