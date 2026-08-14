#!/usr/bin/env python3
"""Decompose Walle's 91 residuals with retained Apple ITER values.

This is a retrospective causal diagnostic.  It first renders the current
public-input Walle candidate, identifies its residual coordinates against the
retained physical corpus, and only then evaluates the independently captured
Apple setup interpolants at those coordinates.  It does not derive or
authorize a production setup law.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray
from PIL import Image


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "lg-test" / "Analysis")]

import _analyze_reveal_raster_trace as reveal  # noqa: E402
import score_reveal_v74_public_raster as public  # noqa: E402


type JsonObject = dict[str, object]
type U8Plane = NDArray[np.uint8]

SETUP_ROOT: Final = Path("/tmp/walle-analysis/standalone-A2-setup-v1")
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "residual-apple-setup-analysis"
    / "reveal-residual-apple-setup-result.json"
)
EXPECTED_SETUP_STATES: Final = frozenset(
    {31, 33, 34, 35, 36, 37, 39, 40, 41, 42, 44, 45, 46, 47, 58, 60}
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> JsonObject:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _reference(state: int) -> U8Plane:
    path = public.DEFAULT_CORPUS / f"frame-{state:04}.png"
    rgba = np.asarray(Image.open(path).convert("RGBA"))
    if not (
        rgba.shape == (public.HEIGHT, public.WIDTH, 4)
        and np.array_equal(rgba[..., 0], rgba[..., 1])
        and np.array_equal(rgba[..., 0], rgba[..., 2])
        and bool(np.all(rgba[..., 3] == np.uint8(255)))
    ):
        raise ValueError(f"state {state} reference is not opaque grayscale")
    return rgba[..., 0]


def _apple_codes(
    raw: NDArray[np.uint32], x: NDArray[np.int64], y: NDArray[np.int64]
) -> U8Plane:
    partner_x = x ^ 1
    partner_y = y ^ 1
    sdf_x = raw[y, x, 1].view("<f4")
    sdf_y = raw[y, x, 2].view("<f4")
    sdf_partner_x = raw[y, partner_x, 1].view("<f4")
    sdf_partner_y = raw[partner_y, x, 2].view("<f4")
    distance = reveal.circle_distance(sdf_x, sdf_y)
    distance_x = reveal.circle_distance(sdf_partner_x, sdf_y)
    distance_y = reveal.circle_distance(sdf_x, sdf_partner_y)
    feather = np.maximum(
        np.asarray(
            np.abs(distance_x - distance) + np.abs(distance_y - distance),
            dtype=np.float32,
        ),
        np.float32(1e-4),
    )
    alpha = (
        np.clip(
            np.asarray(
                (np.float32(1) - distance) / feather + np.float32(0.5),
                dtype=np.float32,
            ),
            0,
            1,
        )
        .astype(np.float16)
        .astype(np.float32)
    )
    return np.rint(alpha * np.float32(255)).astype(np.uint8)


def analyze(setup_root: Path = SETUP_ROOT) -> JsonObject:
    states = {
        int(path.name.removeprefix("state-")): path
        for path in setup_root.glob("state-*")
        if path.is_dir()
    }
    if states.keys() != EXPECTED_SETUP_STATES:
        raise ValueError("retained Apple setup state inventory differs")

    selector = reveal.raster_arithmetic.load_selector_table()
    p25 = reveal.P25_BITMAP.read_bytes()
    encoded_inventory = bytearray()
    state_results: list[JsonObject] = []
    setup_inputs: list[JsonObject] = []
    total_residual = 0
    apple_matches_reference = 0
    apple_matches_current = 0
    unexplained = 0
    for state, directory in sorted(states.items()):
        report_path = directory / "reveal-a2-setup-report.json"
        raw_path = directory / "reveal-a2-setup-rgba32uint.raw"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        output = report.get("output")
        if not isinstance(output, dict) or (
            report.get("classification")
            != "standalone replay of captured retained A2 geometry on Apple Metal"
            or report.get("capturedAppleBuffersMutated") is not False
            or report.get("liveAppleFrameMutated") is not False
            or output.get("components") != ["primitive-id", "sdf-x", "sdf-y", "first-x"]
            or output.get("rawBytes") != 67_108_864
            or output.get("sha256") != _sha256(raw_path)
        ):
            raise ValueError(f"state {state} Apple setup closure differs")
        setup_inputs.append(
            {
                "state": state,
                "report": _identity(report_path),
                "raw": _identity(raw_path),
            }
        )

        current, _unsupported = public.render_public_state(
            state, base=selector, bitmap=p25
        )
        reference = _reference(state)
        y, x = np.nonzero(current != reference)
        x = x.astype(np.int64)
        y = y.astype(np.int64)
        raw = np.memmap(
            raw_path,
            mode="r",
            dtype="<u4",
            shape=(public.HEIGHT, public.WIDTH, 4),
        )
        apple = _apple_codes(raw, x, y)
        current_values = current[y, x]
        reference_values = reference[y, x]
        matches_reference = apple == reference_values
        matches_current_only = (apple == current_values) & ~matches_reference
        other = ~(matches_reference | matches_current_only)
        for index in range(len(x)):
            encoded_inventory.extend(
                (
                    f"{state}\t{int(x[index])}\t{int(y[index])}\t"
                    f"{int(current_values[index])}\t{int(apple[index])}\t"
                    f"{int(reference_values[index])}\n"
                ).encode()
            )
        residual_count = len(x)
        current_state_matches = int(np.count_nonzero(matches_current_only))
        reference_state_matches = int(np.count_nonzero(matches_reference))
        other_count = int(np.count_nonzero(other))
        total_residual += residual_count
        apple_matches_reference += reference_state_matches
        apple_matches_current += current_state_matches
        unexplained += other_count
        state_results.append(
            {
                "state": state,
                "currentResidualCount": residual_count,
                "appleSetupMatchesPhysicalReferenceCount": reference_state_matches,
                "appleSetupMatchesCurrentWalleOnlyCount": current_state_matches,
                "unexplainedCount": other_count,
            }
        )

    if (
        total_residual,
        apple_matches_reference,
        apple_matches_current,
        unexplained,
    ) != (91, 82, 9, 0):
        raise ValueError("Apple setup residual decomposition differs")
    return {
        "schemaVersion": 1,
        "classification": "retrospective Apple-setup decomposition of Walle residuals",
        "authority": {
            "referencePixelsRead": True,
            "outputBlindSetupCapture": True,
            "derivesProductionSetupLaw": False,
            "productionIntegrationAuthorized": False,
        },
        "inputs": {
            "analyzer": _identity(Path(__file__).resolve()),
            "setupRoot": str(setup_root),
            "setupStates": setup_inputs,
            "publicRasterScorer": _identity(Path(public.__file__).resolve()),
            "circleArithmetic": _identity(Path(reveal.__file__).resolve()),
        },
        "residualCoordinateEncoding": (
            "state<TAB>x<TAB>y<TAB>walle<TAB>appleSetup<TAB>physical<LF>"
        ),
        "residualCoordinateInventorySha256": hashlib.sha256(
            encoded_inventory
        ).hexdigest(),
        "currentWalleResidualCount": total_residual,
        "appleSetupMatchesPhysicalReferenceCount": apple_matches_reference,
        "appleSetupMatchesCurrentWalleOnlyCount": apple_matches_current,
        "unexplainedCount": unexplained,
        "conclusion": (
            "Retained Apple ITER values explain all 91 current residual coordinates: "
            "82 become the physical reference byte and nine state-42 coordinates remain "
            "equal to Walle/Apple offscreen while physical presentation is one code lower."
        ),
        "states": state_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup-root", type=Path, default=SETUP_ROOT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    result = analyze(arguments.setup_root)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["conclusion"], indent=2))


if __name__ == "__main__":
    main()
