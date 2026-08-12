#!/usr/bin/env python3
"""Tests for the controlled dynamic-backdrop byte gate."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np

import liquid_glass_controlled_backdrop as controlled


class ControlledInputTests(unittest.TestCase):
    def test_preregistered_coordinate_hash_field(self) -> None:
        field = controlled.controlled_input()
        self.assertEqual(field.shape, (1_024, 1_024, 4))
        self.assertTrue(np.all(field[..., 3] == 255))
        self.assertEqual(
            hashlib.sha256(field.tobytes()).hexdigest(),
            controlled.CONTROLLED_INPUT_SHA256,
        )
        self.assertEqual(
            np.unique(field.view("<u4").reshape(-1)).size,
            863_520,
        )


class SamplerTests(unittest.TestCase):
    def test_half_phase_uses_exact_four_texel_mean(self) -> None:
        texture = np.asarray(
            [
                [[0, 0, 0, 255], [4, 8, 12, 255]],
                [[8, 16, 24, 255], [12, 24, 36, 255]],
            ],
            dtype=np.uint8,
        )
        sampled = controlled._sample_bgra8_linear(
            texture,
            coordinates_x=np.asarray([0.5], dtype=np.float32),
            coordinates_y=np.asarray([0.5], dtype=np.float32),
        )
        np.testing.assert_array_equal(
            sampled,
            np.asarray([[[6, 12, 18, 255]]], dtype=np.uint8),
        )

    def test_sampler_clamps_at_texture_edge(self) -> None:
        texture = np.asarray(
            [
                [[3, 5, 7, 255], [20, 30, 40, 255]],
                [[50, 60, 70, 255], [80, 90, 100, 255]],
            ],
            dtype=np.uint8,
        )
        sampled = controlled._sample_bgra8_linear(
            texture,
            coordinates_x=np.asarray([0.0], dtype=np.float32),
            coordinates_y=np.asarray([0.0], dtype=np.float32),
        )
        np.testing.assert_array_equal(sampled[0, 0], texture[0, 0])


class EvidenceLayoutTests(unittest.TestCase):
    def test_quad_topology_requires_sequential_indexed_quads(self) -> None:
        controlled._validate_quad_indices(
            (0, 1, 2, 2, 3, 0),
            vertex_count=4,
        )
        with self.assertRaisesRegex(ValueError, "topology"):
            controlled._validate_quad_indices(
                (0, 2, 1, 2, 3, 0),
                vertex_count=4,
            )

    def test_raw_texture_path_cannot_escape_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "artifact"
            root.mkdir()
            outside = Path(directory) / "outside.raw"
            outside.write_bytes(bytes(4))
            with self.assertRaisesRegex(ValueError, "escapes"):
                controlled._raw_texture(
                    {
                        "rawCapture": True,
                        "rawFile": "../outside.raw",
                        "rawBytes": 4,
                        "bytesPerRow": 4,
                        "width": 1,
                        "height": 1,
                        "pixelFormat": 80,
                    },
                    root=root,
                    name="escape fixture",
                )


if __name__ == "__main__":
    unittest.main()
