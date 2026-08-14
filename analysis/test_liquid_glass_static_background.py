import json
import unittest
from pathlib import Path

import numpy as np

from liquid_glass_independent_mips import wallpaper_source_snapshot
from liquid_glass_static_background import (
    coordinate_hash_prepass_bgra,
    coordinate_hash_wallpaper_rgba,
)


CAPTURE_ROOT = Path(
    "artifacts/liquid-glass-introspection-30575220842"
)


class StaticBackgroundTests(unittest.TestCase):
    def test_generated_wallpaper_and_prepass_match_all_captures(self) -> None:
        wallpaper = coordinate_hash_wallpaper_rgba()
        prepass = coordinate_hash_prepass_bgra()
        comparisons = 0

        for capture in sorted(CAPTURE_ROOT.iterdir()):
            runtime = json.loads(
                (capture / "runtime.json").read_text(encoding="utf-8")
            )
            source = wallpaper_source_snapshot(runtime)
            captured_wallpaper = np.fromfile(
                capture / str(source["rawFile"]),
                dtype=np.uint8,
            ).reshape(1024, 1024, 4)
            np.testing.assert_array_equal(wallpaper, captured_wallpaper)
            comparisons += wallpaper.size

            for name in (
                "carenderer-live-tree-pre-final-pass-bgra8.raw",
                "carenderer-local-backdrop-pre-final-pass-bgra8.raw",
            ):
                captured_prepass = np.fromfile(
                    capture / name,
                    dtype=np.uint8,
                ).reshape(1024, 1024, 4)
                np.testing.assert_array_equal(prepass, captured_prepass)
                comparisons += prepass.size

        self.assertEqual(comparisons, 50_331_648)


if __name__ == "__main__":
    unittest.main()
