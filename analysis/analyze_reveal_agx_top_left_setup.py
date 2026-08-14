#!/usr/bin/env python3
"""Recover AGX's ordinary setup anchor and re-forward built-in clip endpoints.

The direct-child coefficient capture isolates ordinary triangle setup.  This
analyzer selects an anchor rule using only its discovery split, checks the rule
on the untouched holdout, and then uses that already-selected rule to forward
the independently recovered direct clip endpoints into the paired built-in
guard capture.  It never opens a rendered image or reference pixel.
"""

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np


ROOT: Final = Path(__file__).resolve().parent.parent
ANALYSIS: Final = ROOT / "analysis"
sys.path.insert(0, str(ANALYSIS))

import analyze_reveal_agx_basis_phase as phase  # noqa: E402
import analyze_reveal_agx_clip_setup_split as setup  # noqa: E402
import analyze_reveal_agx_clip_weight_tomography as weight  # noqa: E402
import analyze_reveal_agx_endpoint_isolation as endpoint  # noqa: E402
import analyze_reveal_agx_ldcf_export as export  # noqa: E402


type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
type JsonObject = dict[str, JsonValue]
type FixedPositions = tuple[tuple[int, int], tuple[int, int], tuple[int, int]]
type AnchorSelector = Callable[[FixedPositions], int]

OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "top-left-setup-analysis"
    / "reveal-agx-top-left-setup-result.json"
)

EXPECTED_DEPENDENCIES: Final = {
    ANALYSIS
    / "analyze_reveal_agx_clip_setup_split.py": "591f9a9fef2caafe43d4d1464377deeacaf2fd5c057cb1337703ac3a1f4f820c",
    ANALYSIS
    / "analyze_reveal_agx_clip_weight_tomography.py": "95aec239d9fb11040ff02ffc318a7a823c5bbbc23bf87f7a76bfb9160a531b63",
    ANALYSIS
    / "analyze_reveal_agx_endpoint_isolation.py": "5154d8c5d8b63110a3b2760ebd3d3667d419e0b271f64aa1971c0911212bf239",
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "reveal-agx-clip-setup-split-result.json": "737bcd1aee20dc75258bf45b4bc4a2e942f7ffe9ea5d9d7e0e4bc978525e2f3e",
    ROOT
    / "build"
    / "analysis-agx-direct-user-clip"
    / "endpoint-isolation-analysis"
    / "reveal-agx-endpoint-isolation-result.json": "9a155cc42decba1285068d4b0d8bbe6ed8ab3d5534bf29462813a9c05fb3b845",
    setup.P25_PATH: setup.P25_SHA256,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _top_left(positions: FixedPositions) -> int:
    return min(range(3), key=lambda index: (*reversed(positions[index]), index))


ANCHOR_RULES: Final[dict[str, AnchorSelector]] = {
    "vertex-0": lambda _positions: 0,
    "vertex-1": lambda _positions: 1,
    "vertex-2": lambda _positions: 2,
    "minimum-x-then-y": lambda positions: min(
        range(3), key=lambda index: (*positions[index], index)
    ),
    "maximum-x-then-y": lambda positions: max(
        range(3), key=lambda index: (*positions[index], -index)
    ),
    "top-left-minimum-y-then-x": _top_left,
    "bottom-right-maximum-y-then-x": lambda positions: max(
        range(3), key=lambda index: (*reversed(positions[index]), -index)
    ),
}


def _anchor_slope(
    vertices: tuple[setup.Vertex, setup.Vertex, setup.Vertex],
    component: int,
    axis: int,
    bitmap: bytes,
    anchor: int,
) -> int:
    positions = setup._fixed_positions(vertices)  # noqa: SLF001
    determinant = setup._determinant(positions)  # noqa: SLF001
    values = tuple(setup._float32(vertex[2 + component]) for vertex in vertices)  # noqa: SLF001
    edge_fixed = (
        (
            positions[1][1] - positions[2][1],
            positions[2][1] - positions[0][1],
            positions[0][1] - positions[1][1],
        )
        if axis == 0
        else (
            positions[2][0] - positions[1][0],
            positions[0][0] - positions[2][0],
            positions[1][0] - positions[0][0],
        )
    )
    numerator = sum(
        (
            setup._first_product(  # noqa: SLF001
                setup._float32(values[index] - values[anchor]),  # noqa: SLF001
                edge_fixed[index] / 256.0,
                bias_units=15,
            )
            for index in range(3)
            if index != anchor
        ),
        start=Fraction(0),
    )
    normalized = setup._normalize_signed(  # noqa: SLF001
        numerator,
        precision_bits=27,
        rounding="nearest-even",
    )
    return setup._reciprocal_product(normalized, determinant, bitmap)  # noqa: SLF001


def _score_anchor(
    cases: tuple[setup.CoefficientCase, ...],
    bitmap: bytes,
    selector: AnchorSelector,
    *,
    split: str,
    examples: bool,
) -> JsonObject:
    deltas: list[int] = []
    first_mismatches: list[JsonObject] = []
    child_count = 0
    for entry in cases:
        if split != "all" and entry.split != split:
            continue
        child_count += 1
        positions = setup._fixed_positions(entry.vertices)  # noqa: SLF001
        anchor = selector(positions)
        for component in range(4):
            for axis in range(2):
                predicted = _anchor_slope(
                    entry.vertices,
                    component,
                    axis,
                    bitmap,
                    anchor,
                )
                actual = entry.actual[component][axis]
                delta = export._float_ulp_delta(actual, predicted)  # noqa: SLF001
                deltas.append(delta)
                if examples and delta and len(first_mismatches) < 16:
                    first_mismatches.append(
                        {
                            "caseIndex": entry.sample.case_index,
                            "state": entry.sample.state,
                            "sourcePrimitive": entry.sample.source_primitive,
                            "childOrdinal": entry.sample.child_ordinal,
                            "component": component,
                            "axis": axis,
                            "anchorVertex": anchor,
                            "actualBits": f"0x{actual:08x}",
                            "predictedBits": f"0x{predicted:08x}",
                            "actualMinusPredictedFloatUlps": delta,
                        }
                    )
    distribution = Counter(deltas)
    return {
        "split": split,
        "childCount": child_count,
        "slopeWordCount": len(deltas),
        "exactCount": distribution[0],
        "withinOneUlpCount": sum(
            count for delta, count in distribution.items() if abs(delta) <= 1
        ),
        "deltaDistribution": {
            str(delta): count for delta, count in sorted(distribution.items())
        },
        "firstMismatches": first_mismatches,
    }


def _ordinary_setup_gate(bitmap: bytes) -> JsonObject:
    _catalog, samples = phase._load_catalog(setup.CATALOG)  # noqa: SLF001
    direct_spec = next(
        spec for spec in setup.CAPTURE_SPECS if spec.name == "direct-canonical-child"
    )
    direct = setup._load_capture(direct_spec, samples)  # noqa: SLF001
    cases = setup._coefficient_cases(samples, direct.words)  # noqa: SLF001

    discovery = {
        name: _score_anchor(
            cases,
            bitmap,
            selector,
            split="discovery",
            examples=False,
        )
        for name, selector in ANCHOR_RULES.items()
    }
    ranked = sorted(
        discovery,
        key=lambda name: (
            int(discovery[name]["exactCount"]),
            int(discovery[name]["withinOneUlpCount"]),
            name,
        ),
        reverse=True,
    )
    winner = ranked[0]
    if winner != "top-left-minimum-y-then-x":
        raise ValueError(f"ordinary setup anchor winner changed: {winner}")
    holdout = _score_anchor(
        cases,
        bitmap,
        ANCHOR_RULES[winner],
        split="holdout",
        examples=True,
    )
    combined = _score_anchor(
        cases,
        bitmap,
        ANCHOR_RULES[winner],
        split="all",
        examples=True,
    )
    if (
        discovery[winner]["exactCount"] != 1_331
        or discovery[winner]["withinOneUlpCount"] != 1_336
        or holdout["exactCount"] != 504
        or combined["exactCount"] != 1_835
        or combined["withinOneUlpCount"] != 1_840
        or combined["deltaDistribution"] != {"-1": 1, "0": 1_835, "1": 4}
    ):
        raise ValueError("top-left ordinary setup census differs")
    return {
        "selectionUsesDiscoveryOnly": True,
        "candidateRules": discovery,
        "ranking": ranked,
        "winner": winner,
        "winnerDefinition": (
            "choose the vertex with minimum 1/256-quantized screen y, breaking "
            "ties by minimum quantized x and then submitted vertex index"
        ),
        "holdout": holdout,
        "combined": combined,
        "holdoutAllExact": True,
        "allMeasuredSlopesWithinOneFloatUlp": True,
    }


def _builtin_endpoint_reforward_gate(bitmap: bytes) -> JsonObject:
    captures: dict[str, endpoint.Capture] = {}
    capture_inputs: list[JsonObject] = []
    for spec in endpoint.CAPTURE_SPECS:
        capture, authentication = endpoint._load_capture(spec)  # noqa: SLF001
        captures[spec.name] = capture
        capture_inputs.append(authentication)

    table = np.fromfile(endpoint.RECIPROCAL_TABLE, dtype="<u4")
    if table.size != endpoint.RECIPROCAL_ENTRY_COUNT:
        raise ValueError("direct reciprocal table length differs")
    generated = endpoint._predict_direct_values(table)  # noqa: SLF001
    _outer, inner = endpoint._pattern_endpoints()  # noqa: SLF001

    plan, plan_prefix_sha256 = weight._read_plan_prefix()  # noqa: SLF001
    weight._validate_plan(plan)  # noqa: SLF001
    records = plan.reshape(endpoint.DISTANCE_COUNT, endpoint.PATTERN_COUNT, -1)
    observed = captures["builtin-guard"].coefficient_bits
    mismatches: list[JsonObject] = []
    predictions = np.empty_like(generated)

    for distance in range(endpoint.DISTANCE_COUNT):
        geometry = tuple(_float(int(word)) for word in records[distance, 0, 8:12])
        left, right, top, bottom = geometry
        if left != setup._float32(-64.0 - distance / 256.0):  # noqa: SLF001
            raise ValueError("endpoint source left coordinate differs")
        if (right, top, bottom) != (192.0, 96.0, 160.0):
            raise ValueError("endpoint source rectangle differs")
        vertices_xy = ((-64.0, bottom), (right, bottom), (right, top))
        positions = tuple(
            (setup._subpixel_fixed(x), setup._subpixel_fixed(y))  # noqa: SLF001
            for x, y in vertices_xy
        )
        anchor = _top_left(positions)  # type: ignore[arg-type]
        if anchor != 2:
            raise ValueError("built-in main-child top-left anchor differs")
        for pattern in range(endpoint.PATTERN_COUNT):
            for lane in range(endpoint.LANE_COUNT):
                generated_bits = int(generated[distance, pattern, lane])
                inner_bits = int(inner[pattern, lane])
                vertices = (
                    (*vertices_xy[0], _float(generated_bits)),
                    (*vertices_xy[1], _float(inner_bits)),
                    (*vertices_xy[2], _float(inner_bits)),
                )
                predicted = _anchor_slope(
                    vertices,
                    component=0,
                    axis=0,
                    bitmap=bitmap,
                    anchor=anchor,
                )
                predictions[distance, pattern, lane] = predicted
                actual = int(observed[distance, pattern, lane, 0])
                if predicted != actual and len(mismatches) < 16:
                    mismatches.append(
                        {
                            "distanceFixed": distance,
                            "pattern": pattern,
                            "lane": lane,
                            "predictedBits": f"0x{predicted:08x}",
                            "actualBits": f"0x{actual:08x}",
                        }
                    )

    actual_a = observed[:, :, :, 0]
    mismatch_count = int(np.count_nonzero(predictions != actual_a))
    if mismatch_count != 0:
        raise ValueError("built-in main-child endpoint re-forward differs")
    if np.count_nonzero(observed[:, :, :, 1]) != 0:
        raise ValueError("built-in main-child B coefficients differ from zero")
    return {
        "captureInputs": capture_inputs,
        "planDiscoveryPrefixSha256": plan_prefix_sha256,
        "mainChildGeometry": (
            "generated left/bottom endpoint, submitted right/bottom vertex, "
            "submitted right/top vertex"
        ),
        "generatedEndpointSource": (
            "independently recovered direct reciprocal plus 24/18/17 endpoint "
            "products and one binary32 addition"
        ),
        "setupRule": "top-left-anchor P25 two-product ordinary triangle setup",
        "coefficient": "A/x slope",
        "comparisonCount": int(predictions.size),
        "mismatchCount": mismatch_count,
        "predictionSha256": hashlib.sha256(
            memoryview(np.ascontiguousarray(predictions)).cast("B")
        ).hexdigest(),
        "observedSha256": hashlib.sha256(
            memoryview(np.ascontiguousarray(actual_a)).cast("B")
        ).hexdigest(),
        "firstMismatches": mismatches,
        "allEightPatternsExact": True,
        "oppositeSignCancellationExact": True,
        "interpretation": (
            "the earlier 15,312 one-ULP differences came from inverting A with "
            "binary32((inner-generated)/256), not from built-in endpoint mixing; "
            "the measured top-left setup re-forward predicts every observed A word"
        ),
    }


def analyze() -> JsonObject:
    dependencies: list[JsonObject] = []
    for path, expected in EXPECTED_DEPENDENCIES.items():
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"SHA-256 differs for {path.relative_to(ROOT)}: {actual}")
        dependencies.append(
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": actual,
            }
        )
    bitmap = setup.P25_PATH.read_bytes()
    ordinary = _ordinary_setup_gate(bitmap)
    builtin = _builtin_endpoint_reforward_gate(bitmap)
    script = Path(__file__).resolve()
    return {
        "schema": "walle-reveal-agx-top-left-setup-v1",
        "passed": True,
        "classification": "output-blind AGX ordinary-setup and endpoint re-forward",
        "authority": {
            "referencePixelsRead": False,
            "renderedCoverageRead": False,
            "ordinarySetupAnchorSelectedFromDiscoveryOnly": True,
            "ordinarySetupHoldoutPassed": True,
            "builtinSinglePlaneEndpointReforwardRecovered": True,
            "builtinMultiplaneGeneratedVaryingLawRecovered": False,
            "allOrdinarySetupAccumulatorDetailsRecovered": False,
            "productionParityAuthorized": False,
        },
        "inputs": {
            "analyzer": {
                "path": str(script.relative_to(ROOT)),
                "bytes": script.stat().st_size,
                "sha256": _sha256(script),
            },
            "dependencies": dependencies,
        },
        "ordinaryTriangleSetup": ordinary,
        "builtinEndpointReforward": builtin,
        "conclusion": (
            "AGX ordinary arbitrary-varying setup selects the quantized top-left "
            "vertex as the two-product delta anchor over the measured domain. The "
            "untouched holdout is exact and all measured slopes are within one ULP. "
            "Using that rule, the independently recovered endpoint law predicts all "
            "262,176 built-in main-child A coefficients exactly. Remaining reveal "
            "work is the multi-plane generated-varying lineage and the five measured "
            "one-ULP ordinary-setup accumulator cases, not signed endpoint mixing."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    result = analyze()
    encoded = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(encoded)
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "ordinarySetupExact": result["ordinaryTriangleSetup"]["combined"][
                    "exactCount"
                ],  # type: ignore[index]
                "ordinarySetupTotal": result["ordinaryTriangleSetup"]["combined"][
                    "slopeWordCount"
                ],  # type: ignore[index]
                "endpointReforwardExact": result["builtinEndpointReforward"][
                    "comparisonCount"
                ],  # type: ignore[index]
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
