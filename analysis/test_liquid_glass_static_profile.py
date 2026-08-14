import hashlib
import json
import unittest
from pathlib import Path

from liquid_glass_profile_matrix import _glass_uniform_snapshots, _payload
from liquid_glass_static_profile import (
    PROFILE_BYTE_COUNT,
    StaticProfileRequest,
    build_static_profile,
    canonical_static_profile_request,
)


CAPTURE_ROOT = Path(
    "artifacts/liquid-glass-introspection-30575220842"
)

EXPECTED_SHA256 = {
    ("clear", "light"): (
        "641c05867fa4c104e2fa730e5aaec5f406415a86a183dc8ed5e9f417447307cd"
    ),
    ("clear", "dark"): (
        "641c05867fa4c104e2fa730e5aaec5f406415a86a183dc8ed5e9f417447307cd"
    ),
    ("regular", "light"): (
        "3998c5348a9514b9258250bf473a94b1e2ac9201f0ce712bea9462eec2b3ccb6"
    ),
    ("regular", "dark"): (
        "af5399ab1c0e8815864b093d1f94eefa1d27787a16e4f4f0a5cda0a65d54f851"
    ),
}


class StaticProfileTests(unittest.TestCase):
    def test_all_four_generated_profiles_match_captured_bytes(self) -> None:
        observed: set[tuple[str, str]] = set()
        for capture in sorted(CAPTURE_ROOT.iterdir()):
            runtime = json.loads(
                (capture / "runtime.json").read_text(encoding="utf-8")
            )
            material = runtime["materialProfileEvidence"]["material"]
            appearance = runtime["materialProfileEvidence"][
                "requestedAppearance"
            ]
            snapshots = _glass_uniform_snapshots(runtime)[1]
            captured = _payload(snapshots[0])[:PROFILE_BYTE_COUNT]
            generated = build_static_profile(
                canonical_static_profile_request(material, appearance)
            )

            self.assertEqual(generated, captured)
            self.assertEqual(
                hashlib.sha256(generated).hexdigest(),
                EXPECTED_SHA256[(material, appearance)],
            )
            observed.add((material, appearance))

        self.assertEqual(observed, set(EXPECTED_SHA256))

    def test_invalid_geometry_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive finite extents"):
            build_static_profile(
                StaticProfileRequest(
                    material="clear",
                    appearance="light",
                    width=0.0,
                    height=800.0,
                    source_virtual_width=896,
                    source_virtual_height=896,
                )
            )


if __name__ == "__main__":
    unittest.main()
