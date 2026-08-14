import json
import unittest
from pathlib import Path

import numpy as np

from liquid_glass_independent_mips import (
    generate_source_mips,
    generated_copy_and_mips_from_producer,
    generated_static_source_pyramid,
    read_codes,
    source_snapshot,
)


ROOT = Path(__file__).resolve().parent.parent
CAPTURE_ROOT = ROOT / "artifacts/liquid-glass-introspection-30575220842"
GEOMETRY_ROOT = (
    ROOT / "artifacts/gh-run-30589303022/download"
)


class LiquidGlassIndependentMipTests(unittest.TestCase):
    def test_copy_and_mips_are_exact_across_geometry_corpus(self) -> None:
        captures = [
            *sorted(CAPTURE_ROOT.iterdir()),
            *sorted(GEOMETRY_ROOT.glob("liquid-glass-geometry-*")),
        ]
        checked_bytes = 0
        for capture in captures:
            runtime = json.loads(
                (capture / "runtime.json").read_text(encoding="utf-8")
            )
            snapshots = sorted(
                source_snapshot(runtime)["mipSnapshots"],
                key=lambda level: int(level["level"]),
            )
            generated = generated_copy_and_mips_from_producer(capture)
            for snapshot in snapshots:
                reference = read_codes(
                    capture / str(snapshot["rawFile"]),
                    width=int(snapshot["width"]),
                    height=int(snapshot["height"]),
                )
                predicted = np.frombuffer(
                    generated[int(snapshot["level"])],
                    dtype=np.uint8,
                ).reshape(reference.shape)
                np.testing.assert_array_equal(predicted, reference)
                checked_bytes += reference.size

        self.assertEqual(checked_bytes, 6_942_880)

    def test_static_pyramid_is_exact_from_wallpaper_source(self) -> None:
        checked_bytes = 0
        for capture in sorted(CAPTURE_ROOT.iterdir()):
            runtime = json.loads(
                (capture / "runtime.json").read_text(encoding="utf-8")
            )
            snapshots = sorted(
                source_snapshot(runtime)["mipSnapshots"],
                key=lambda level: int(level["level"]),
            )
            generated = generated_static_source_pyramid(capture)
            for snapshot in snapshots:
                reference = read_codes(
                    capture / str(snapshot["rawFile"]),
                    width=int(snapshot["width"]),
                    height=int(snapshot["height"]),
                )
                predicted = np.frombuffer(
                    generated[int(snapshot["level"])],
                    dtype=np.uint8,
                ).reshape(reference.shape)
                np.testing.assert_array_equal(predicted, reference)
                checked_bytes += reference.size

        self.assertEqual(checked_bytes, 3_579_520)

    def test_all_archived_mip_levels_are_exact(self) -> None:
        checked_bytes = 0
        for capture in sorted(CAPTURE_ROOT.iterdir()):
            runtime = json.loads(
                (capture / "runtime.json").read_text(encoding="utf-8")
            )
            snapshots = sorted(
                source_snapshot(runtime)["mipSnapshots"],
                key=lambda level: int(level["level"]),
            )
            first = snapshots[0]
            base = read_codes(
                capture / str(first["rawFile"]),
                width=int(first["width"]),
                height=int(first["height"]),
            )

            generated = generate_source_mips(base, level_count=len(snapshots))

            for snapshot, predicted in zip(snapshots, generated, strict=True):
                reference = read_codes(
                    capture / str(snapshot["rawFile"]),
                    width=int(snapshot["width"]),
                    height=int(snapshot["height"]),
                )
                np.testing.assert_array_equal(predicted, reference)
                if int(snapshot["level"]) > 0:
                    checked_bytes += reference.size

        self.assertEqual(checked_bytes, 794_240)

    def test_first_and_later_reductions_are_not_interchangeable(self) -> None:
        capture = (
            CAPTURE_ROOT
            / "liquid-glass-introspection-regular-light-30575220842"
        )
        runtime = json.loads(
            (capture / "runtime.json").read_text(encoding="utf-8")
        )
        snapshots = sorted(
            source_snapshot(runtime)["mipSnapshots"],
            key=lambda level: int(level["level"]),
        )
        base = read_codes(
            capture / str(snapshots[0]["rawFile"]),
            width=int(snapshots[0]["width"]),
            height=int(snapshots[0]["height"]),
        )

        generated = generate_source_mips(base, level_count=len(snapshots))

        self.assertEqual(int(generated[3][8, 0, 2]), 110)


if __name__ == "__main__":
    unittest.main()
