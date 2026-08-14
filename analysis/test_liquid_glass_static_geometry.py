import json
import struct
import unittest
from pathlib import Path

import numpy as np

from liquid_glass_geometry_transfer import (
    _glass_snapshots,
    _snapshot_bytes,
    _vertices,
)
from liquid_glass_static_geometry import (
    build_static_circle_geometry,
    canonical_static_circle_geometry_request,
)


CAPTURE_ROOT = Path(
    "artifacts/liquid-glass-introspection-30575220842"
)


class StaticCircleGeometryTests(unittest.TestCase):
    def test_generated_meshes_match_all_four_captured_buffers(self) -> None:
        comparisons = 0
        for capture in sorted(CAPTURE_ROOT.iterdir()):
            runtime = json.loads(
                (capture / "runtime.json").read_text(encoding="utf-8")
            )
            material = runtime["materialProfileEvidence"]["material"]
            generated = build_static_circle_geometry(
                canonical_static_circle_geometry_request(material)
            )
            vertex_snapshots = _glass_snapshots(
                runtime,
                stage="vertex",
                index=1,
            )
            index_snapshot = _glass_snapshots(
                runtime,
                stage="index",
                index=-1,
            )[0]
            captured_main = np.asarray(
                _vertices(vertex_snapshots[0], 6),
                dtype=np.float32,
            )
            captured_shadow = np.asarray(
                _vertices(vertex_snapshots[1], 16),
                dtype=np.float32,
            )
            captured_indices = np.asarray(
                struct.unpack_from(
                    "<48H",
                    _snapshot_bytes(index_snapshot),
                ),
                dtype=np.uint16,
            )

            np.testing.assert_array_equal(generated.main_vertices, captured_main)
            np.testing.assert_array_equal(
                generated.shadow_vertices,
                captured_shadow,
            )
            np.testing.assert_array_equal(
                generated.shadow_indices,
                captured_indices,
            )
            comparisons += (
                generated.main_vertices.size
                + generated.shadow_vertices.size
                + generated.shadow_indices.size
            )

        self.assertEqual(comparisons, 896)


if __name__ == "__main__":
    unittest.main()
