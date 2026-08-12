#!/usr/bin/env python3
"""Tests for dynamic Liquid Glass backdrop evidence recovery."""

import struct
import unittest

from liquid_glass_dynamic_backdrop import (
    _producer_boundary_snapshots,
    decode_copy_base_uniform,
    float32,
    float32_bits,
    float32_ulp_distance,
    recover_crop_origin,
)


class ProducerBoundaryTests(unittest.TestCase):
    def test_prefers_the_point_in_time_boundary(self) -> None:
        producer_input = {
            "sequence": 20,
            "address": "0x1000",
        }
        copy_source = {
            "sequence": 30,
            "texture": {"address": "0x2000"},
        }
        input_snapshot = {"rawFile": "input.raw"}
        output_snapshot = {"rawFile": "output.raw"}
        render = {
            "dynamicBackdropProducerBoundary": {
                "schemaVersion": 1,
                "boundaryCount": 1,
                "records": [
                    {
                        "capturePoint": (
                            "blit-after-producer-render-before-copy-base-compute"
                        ),
                        "producerInputAddress": "0x1000",
                        "producerInputBindingSequence": 20,
                        "producerOutputAddress": "0x2000",
                        "copyBaseBindingSequence": 30,
                        "input": input_snapshot,
                        "output": output_snapshot,
                    }
                ],
            }
        }

        self.assertEqual(
            _producer_boundary_snapshots(
                render,
                producer_input=producer_input,
                copy_source=copy_source,
            ),
            (input_snapshot, output_snapshot),
        )

    def test_rejects_a_post_frame_substitute(self) -> None:
        render = {
            "dynamicBackdropProducerBoundary": {
                "schemaVersion": 1,
                "boundaryCount": 1,
                "records": [
                    {
                        "capturePoint": "post-frame",
                        "producerInputAddress": "0x1000",
                        "producerInputBindingSequence": 20,
                        "producerOutputAddress": "0x2000",
                        "copyBaseBindingSequence": 30,
                        "input": {},
                        "output": {},
                    }
                ],
            }
        }

        with self.assertRaisesRegex(ValueError, "boundary join"):
            _producer_boundary_snapshots(
                render,
                producer_input={"sequence": 20, "address": "0x1000"},
                copy_source={
                    "sequence": 30,
                    "texture": {"address": "0x2000"},
                },
            )


class CopyBaseUniformTests(unittest.TestCase):
    def test_decodes_signed_offsets_and_unsigned_extents(self) -> None:
        payload = bytearray(32)
        struct.pack_into("<2h", payload, 0, -5, -3)
        struct.pack_into("<4h", payload, 8, 0, 1, 447, 448)
        struct.pack_into("<2H", payload, 16, 512, 512)
        struct.pack_into("<2H", payload, 20, 256, 256)
        struct.pack_into("<H", payload, 24, 1)
        payload[26] = 1

        self.assertEqual(
            decode_copy_base_uniform(bytes(payload)),
            {
                "textureCoordinateBase": [-5, -3],
                "textureCoordinateClamp": [0, 1, 447, 448],
                "destinationLevel0Size": [512, 512],
                "destinationLevel1Size": [256, 256],
                "destinationLevel1": 1,
                "noBaseMip": True,
            },
        )

    def test_rejects_truncated_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "shorter than 32 bytes"):
            decode_copy_base_uniform(bytes(31))


class CropOriginTests(unittest.TestCase):
    def test_recovers_rectangular_producer_crop(self) -> None:
        width = 448
        height = 512
        crop_x = 92
        crop_y = 35
        mvp = [0.0] * 16
        mvp[0] = float32(2.0 / width)
        mvp[5] = float32(-2.0 / height)
        mvp[10] = 1.0
        mvp[12] = float32(-1.0 - 2.0 * crop_x / width)
        mvp[13] = float32(1.0 + 2.0 * crop_y / height)
        mvp[15] = 1.0

        recovered = recover_crop_origin(tuple(mvp), width=width, height=height)

        self.assertEqual(recovered["origin"], [crop_x, crop_y])
        self.assertLess(recovered["maximumIntegralResidual"], 1.0e-4)
        self.assertTrue(recovered["orthographicScaleBitsExact"])


class Float32Tests(unittest.TestCase):
    def test_ulp_distance_tracks_adjacent_values_across_signs(self) -> None:
        one = float32(1.0)
        next_one = struct.unpack("<f", struct.pack("<I", float32_bits(one) + 1))[0]
        minus_one = float32(-1.0)
        next_minus_one_toward_zero = struct.unpack(
            "<f", struct.pack("<I", float32_bits(minus_one) - 1)
        )[0]

        self.assertEqual(float32_ulp_distance(one, next_one), 1)
        self.assertEqual(
            float32_ulp_distance(minus_one, next_minus_one_toward_zero),
            1,
        )


if __name__ == "__main__":
    unittest.main()
