#!/usr/bin/env python3
"""Tests for point-in-time dynamic-backdrop payload auditing."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import liquid_glass_dynamic_backdrop_payload as audit


class RawPayloadTests(unittest.TestCase):
    def test_zero_payload_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = bytes(16)
            (root / "zero.raw").write_bytes(payload)
            result = audit._raw_payload(
                {
                    "rawCapture": True,
                    "rawFile": "zero.raw",
                    "rawBytes": len(payload),
                },
                root=root,
                name="zero fixture",
            )
        self.assertTrue(result["allZero"])
        self.assertEqual(result["nonzeroByteCount"], 0)
        self.assertEqual(result["uniqueBGRA8PixelCount"], 1)
        self.assertEqual(result["sha256"], hashlib.sha256(payload).hexdigest())

    def test_nonzero_bgra_pixels_are_counted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = bytes.fromhex("00000000 010203ff 010203ff")
            (root / "field.raw").write_bytes(payload)
            result = audit._raw_payload(
                {
                    "rawCapture": True,
                    "rawFile": "field.raw",
                    "rawBytes": len(payload),
                },
                root=root,
                name="field fixture",
            )
        self.assertFalse(result["allZero"])
        self.assertEqual(result["nonzeroByteCount"], 8)
        self.assertEqual(result["uniqueBGRA8PixelCount"], 2)

    def test_raw_path_cannot_escape_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "artifact"
            root.mkdir()
            outside = Path(directory) / "outside.raw"
            outside.write_bytes(bytes(4))
            with self.assertRaisesRegex(ValueError, "escapes"):
                audit._raw_payload(
                    {
                        "rawCapture": True,
                        "rawFile": "../outside.raw",
                        "rawBytes": 4,
                    },
                    root=root,
                    name="escape fixture",
                )


if __name__ == "__main__":
    unittest.main()
