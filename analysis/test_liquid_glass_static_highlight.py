import hashlib
import json
import struct
import unittest
from pathlib import Path

import numpy as np

from liquid_glass_static_highlight import build_static_highlight


CAPTURE_ROOT = Path(
    "artifacts/liquid-glass-introspection-30575220842"
)


def pipeline_fragment(snapshot: dict[str, object]) -> str:
    pipeline = snapshot.get("pipeline")
    if not isinstance(pipeline, dict):
        return ""
    descriptor = pipeline.get("creationDescriptor")
    return (
        str(descriptor.get("fragmentFunction", ""))
        if isinstance(descriptor, dict)
        else ""
    )


def payload(snapshot: dict[str, object]) -> bytes:
    record = snapshot.get("payload")
    if not isinstance(record, dict) or not isinstance(record.get("hex"), str):
        raise ValueError("captured snapshot has no payload")
    return bytes.fromhex(str(record["hex"]))


class StaticHighlightTests(unittest.TestCase):
    def test_generated_inputs_match_all_four_captured_draws(self) -> None:
        comparisons = 0
        prefix_hashes: dict[str, str] = {}
        for capture in sorted(CAPTURE_ROOT.iterdir()):
            runtime = json.loads(
                (capture / "runtime.json").read_text(encoding="utf-8")
            )
            material = runtime["materialProfileEvidence"]["material"]
            appearance = "dark" if "-dark-" in capture.name else "light"
            generated = build_static_highlight(material, appearance)
            snapshots = runtime["carendererLocalBackdropEvidence"]["render"][
                "metalBufferSnapshots"
            ]["snapshots"]
            highlight = [
                snapshot
                for snapshot in snapshots
                if pipeline_fragment(snapshot) == "A2Xghfc"
            ]

            def latest(stage: str, index: int) -> dict[str, object]:
                return max(
                    (
                        snapshot
                        for snapshot in highlight
                        if snapshot.get("stage") == stage
                        and snapshot.get("index") == index
                    ),
                    key=lambda snapshot: int(snapshot["sequence"]),
                )

            vertex_source = payload(latest("vertex", 1))
            captured_vertices = np.asarray(
                [
                    struct.unpack_from("<8f", vertex_source, index * 48)
                    for index in range(4)
                ],
                dtype=np.float32,
            )
            captured_indices = np.asarray(
                struct.unpack_from("<6H", payload(latest("index", -1))),
                dtype=np.uint16,
            )
            captured_uniform = payload(latest("fragment", 1))[:0xF8]

            np.testing.assert_array_equal(
                generated.vertices,
                captured_vertices,
            )
            np.testing.assert_array_equal(
                generated.indices,
                captured_indices,
            )
            self.assertEqual(generated.uniform_payload, captured_uniform)
            comparisons += (
                generated.vertices.nbytes
                + generated.indices.nbytes
                + len(generated.uniform_payload)
            )
            prefix_hashes[f"{material}-{appearance}"] = hashlib.sha256(
                generated.uniform_payload
            ).hexdigest()

        self.assertEqual(comparisons, 1_552)
        self.assertEqual(
            prefix_hashes,
            {
                "clear-dark": (
                    "8d1566019f250198bc23c0cae912bb2c94c79f7829f40dafc200bb18091b4297"
                ),
                "clear-light": (
                    "ffd866689cc7391ffc0a19cf61446ea5b173869316ea7de6df0f37ab9506024a"
                ),
                "regular-dark": (
                    "bd34dd789e2db6e19d2badf5c4625bcdb42bbbfcc7db0c159017bff7d3f37129"
                ),
                "regular-light": (
                    "30b19e8ab9929db4306f7b54145a59575ce5cae0cbf72b8def454edf9115d39d"
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()
