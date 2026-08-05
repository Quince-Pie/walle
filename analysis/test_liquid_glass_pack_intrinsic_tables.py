import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

import liquid_glass_pack_intrinsic_tables as packing


class PackedIntrinsicTableTests(unittest.TestCase):
    def test_every_source_bit_round_trips(self) -> None:
        codes = np.arange(256, dtype=np.uint8)

        sqrt_words, rsqrt_words = packing.pack_codes(codes)

        packing.validate_lossless(codes, sqrt_words, rsqrt_words)
        self.assertEqual(sqrt_words.nbytes, codes.nbytes // 2)
        self.assertEqual(rsqrt_words.nbytes, codes.nbytes // 4)

    def test_invalid_code_count_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "multiple of 16"):
            packing.pack_codes(np.zeros(15, dtype=np.uint8))

    def test_circle_reciprocal_matches_captured_exact_bits(self) -> None:
        table = Path(
            "artifacts/apple-float-intrinsics-r8-30556057571.bin"
        )
        if not table.exists():
            self.skipTest("captured exhaustive intrinsic table is absent")

        result = packing.circle_scale_reciprocal_bits(400.0, table)

        self.assertEqual(result, 0x3AD65B63)

    def test_missing_reciprocal_code_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            table = Path(directory) / "short.bin"
            table.write_bytes(b"\x00")
            with self.assertRaisesRegex(ValueError, "has no reciprocal code"):
                packing.circle_scale_reciprocal_bits(400.0, table)


if __name__ == "__main__":
    unittest.main()
