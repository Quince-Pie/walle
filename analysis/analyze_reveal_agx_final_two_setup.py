#!/usr/bin/env python3.14
"""Audit the focused M1 setup capture and reject a false lane-phase model."""

import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Final

import numpy as np


type JsonObject = dict[str, object]

ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_setup_accumulator as accumulator  # noqa: E402
import analyze_reveal_agx_guard_fan_diagonal as fan  # noqa: E402
import score_reveal_agx_top_left_setup as scorer  # noqa: E402


CAPTURE_ROOT: Final = ROOT / "build" / "analysis-agx-basis" / "final-two-setup-plan-v2"
PLAN_PATH: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTEX_PATH: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
RAW_PATH: Final = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
CATALOG_PATH: Final = (
    ROOT / "build" / "analysis-agx-basis" / "reveal-agx-basis-catalog.json"
)
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "final-two-setup-analysis" / "result.json"
)
RAW_SHA256: Final = "feebdc12f29df7a967c757701270ef06e5eef8890b8f4506b60aa2bce26c1841"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _float(word: int) -> float:
    return struct.unpack("<f", struct.pack("<I", word))[0]


def _vertices(words: np.ndarray, record: int) -> tuple[tuple[float, ...], ...]:
    return tuple(
        tuple(_float(int(word)) for word in vertex) for vertex in words[record]
    )


def _selected_child(
    vertices: tuple[tuple[float, ...], ...], pixel: tuple[int, int]
) -> tuple[int, tuple[tuple[float, ...], ...]]:
    polygon = tuple(
        scorer.public._clip_triangle_preserving_start(list(vertices))  # noqa: SLF001
    )
    children = tuple(
        (polygon[0], polygon[index], polygon[index + 1])
        for index in range(1, len(polygon) - 1)
    )
    matches = tuple(
        (ordinal, child)
        for ordinal, child in enumerate(children)
        if fan._triangle_contains_sample(child, pixel)  # noqa: SLF001
    )
    if len(matches) != 1:
        raise ValueError(f"pixel {pixel} belongs to {len(matches)} clipped children")
    return matches[0]


def _source_case(catalog: JsonObject, state: int, primitive: int) -> JsonObject:
    cases = catalog.get("cases")
    if not isinstance(cases, list):
        raise TypeError("catalog cases are absent")
    matches = [
        value
        for value in cases
        if isinstance(value, dict)
        and value.get("state") == state
        and value.get("sourcePrimitive") == primitive
    ]
    if len(matches) != 1:
        raise ValueError(f"state {state} primitive {primitive} is not unique")
    return matches[0]


def _crosses_both_positive_guards(case: JsonObject) -> bool:
    words = case.get("sourceVertexBits")
    if not isinstance(words, list):
        raise TypeError("source vertices are absent")
    vertices = [tuple(_float(int(word)) for word in vertex) for vertex in words]
    high = scorer.public.raster_prototype.GUARD_HIGH
    return (
        max(vertex[0] for vertex in vertices) > high
        and max(vertex[1] for vertex in vertices) > high
    )


def analyze() -> JsonObject:
    if _sha256(RAW_PATH) != RAW_SHA256:
        raise ValueError("focused M1 raw capture differs")
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or not isinstance(catalog, dict):
        raise TypeError("input JSON root differs")
    draws = plan.get("draws")
    if not isinstance(draws, list) or len(draws) != 720:
        raise ValueError("focused draw census differs")
    raw = np.fromfile(RAW_PATH, dtype="<u4").reshape(-1, 101, 4)
    vertex_words = np.fromfile(VERTEX_PATH, dtype="<u4").reshape(-1, 3, 8)
    expected_centers = {
        0: (
            (0x3F7FE85B, 0xBBBD2304),
            (0x3F7FB911, 0xBBBD2324),
            (0x3F7FE85B, 0xBBA57EA1),
        ),
        4: (
            (0x3F384208, 0x3F31BDD7),
            (0x3F3861CE, 0x3F31BDD7),
            (0x3F384208, 0x3F31DD9D),
        ),
    }
    center_checks = 0
    records: list[JsonObject] = []
    for target_index in (0, 4):
        for sample in range(3):
            record_index = (target_index * 3 + sample) * 30
            draw = draws[record_index]
            if not isinstance(draw, dict):
                raise TypeError("draw record differs")
            triples = accumulator._triples(raw[record_index])  # noqa: SLF001
            actual_center = tuple(int(word) for word in raw[record_index, 4, :2])
            if actual_center != expected_centers[target_index][sample]:
                raise ValueError("M1 ITER center differs")
            center_checks += 2
            vertices = _vertices(vertex_words, record_index)
            pixel = (int(draw["x"]), int(draw["y"]))
            child_ordinal, child = _selected_child(vertices, pixel)
            records.append(
                {
                    "targetIndex": target_index,
                    "sample": sample,
                    "pixel": list(pixel),
                    "xParity": pixel[0] & 1,
                    "selectedChildOrdinal": child_ordinal,
                    "selectedChildPositionBits": [
                        [
                            f"0x{struct.unpack('<I', struct.pack('<f', value))[0]:08x}"
                            for value in vertex[:2]
                        ]
                        for vertex in child
                    ],
                    "coefficientTriples": [
                        [f"0x{word:08x}" for word in triple] for triple in triples
                    ],
                    "centerBits": [f"0x{word:08x}" for word in actual_center],
                }
            )

    target_zero = [record for record in records if record["targetIndex"] == 0]
    target_four = [record for record in records if record["targetIndex"] == 4]
    if [record["selectedChildOrdinal"] for record in target_zero] != [0, 1, 0]:
        raise ValueError("state-41 focused samples do not select children 0,1,0")
    if [record["selectedChildOrdinal"] for record in target_four] != [0, 0, 0]:
        raise ValueError("state-61 focused samples do not share child zero")
    if target_zero[0]["coefficientTriples"] != target_zero[2]["coefficientTriples"]:
        raise ValueError("same state-41 child has different coefficient triples")
    if len({json.dumps(record["coefficientTriples"]) for record in target_four}) != 1:
        raise ValueError("same state-61 child has different coefficient triples")
    if target_four[0]["xParity"] == target_four[1]["xParity"]:
        raise ValueError("state-61 parity counterexample is absent")

    state41 = expected_centers[0]
    components = [
        tuple(np.asarray([word], dtype=np.uint32).view(np.float32)[0] for word in pair)
        for pair in state41
    ]
    distances = [
        scorer.public.reveal.circle_distance(
            np.asarray([x], dtype=np.float32),
            np.asarray([y], dtype=np.float32),
        )[0]
        for x, y in components
    ]
    feather = np.float32(
        abs(np.float32(distances[1] - distances[0]))
        + abs(np.float32(distances[2] - distances[0]))
    )
    alpha = np.float32((np.float32(1) - distances[0]) / feather + np.float32(0.5))
    encoded = int(
        np.uint8(
            np.rint(
                np.float32(np.float16(np.clip(alpha, np.float32(0), np.float32(1))))
                * np.float32(255)
            )
        )
    )
    if encoded != 248:
        raise ValueError("state-41 M1 samples do not encode to 248")

    selector = {
        state: _crosses_both_positive_guards(_source_case(catalog, state, 4))
        for state in range(60, 65)
    }
    if selector != {60: False, 61: True, 62: True, 63: True, 64: True}:
        raise ValueError("source-plane structural selector differs")

    return {
        "schema": "walle-reveal-agx-focused-setup-correction-v2",
        "authority": {
            "usesM1FixedFunctionCapture": True,
            "opensReferencePixels": False,
            "usesPerStateOrPixelCorrection": False,
            "rejectsLanePhaseModel": True,
            "productionIntegrationAuthorized": False,
        },
        "inputs": {
            "plan": {
                "path": str(PLAN_PATH.relative_to(ROOT)),
                "sha256": _sha256(PLAN_PATH),
            },
            "vertices": {
                "path": str(VERTEX_PATH.relative_to(ROOT)),
                "sha256": _sha256(VERTEX_PATH),
            },
            "capture": {
                "path": str(RAW_PATH.relative_to(ROOT)),
                "sha256": RAW_SHA256,
                "bytes": RAW_PATH.stat().st_size,
            },
            "catalog": {
                "path": str(CATALOG_PATH.relative_to(ROOT)),
                "sha256": _sha256(CATALOG_PATH),
            },
        },
        "m1Census": {
            "draws": len(draws),
            "coefficientWords": len(draws) * 4 * 3,
            "iterCenterChecks": center_checks,
            "childOwnershipChecks": len(records),
        },
        "falseLanePhaseDiscriminator": {
            "status": "rejected",
            "reason": (
                "The state-41 even-X sample is owned by clipped child 1 while both "
                "odd-X samples are owned by child 0. State 61 provides the direct "
                "counterexample: opposite X parities select the same child and export "
                "identical coefficient triples. Coefficients are primitive setup state, "
                "not per-fragment X-lane state."
            ),
            "records": records,
            "state41EncodedR8": encoded,
        },
        "sourcePlaneSelector": {
            "predicate": "max(source.x) > guardHigh && max(source.y) > guardHigh",
            "state60Through64": {
                str(state): value for state, value in selector.items()
            },
            "firstSelectedState": 61,
        },
        "conclusion": (
            "The focused capture does not establish an X-lane setup phase. Its apparent "
            "even/odd coefficient split is exactly a clipped-child ownership split, and "
            "same-child samples of opposite parity have identical A/B/C. The remaining "
            "problem is still AGX fixed-function post-clip child materialization."
        ),
    }


def main() -> None:
    output = OUTPUT_DEFAULT
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(analyze(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(output)


if __name__ == "__main__":
    main()
