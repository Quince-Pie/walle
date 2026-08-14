#!/usr/bin/env python3.14
"""Invert the determinant-amplified M1 AGX two-product coefficient capture."""

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Final

import numpy as np

import analyze_reveal_agx_two_product_tomography as tomography


type JsonObject = dict[str, object]

ROOT: Final = Path(__file__).resolve().parent.parent
PLAN_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "two-product-amplification-plan-v1"
)
CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-two-product-amplification-v1"
)
PLAN: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTICES: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
RAW: Final = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
CAPTURE_MANIFEST: Final = CAPTURE_ROOT / "capture" / "manifest.json"
STDERR: Final = CAPTURE_ROOT / "capture.stderr"
STDOUT: Final = CAPTURE_ROOT / "capture.stdout"
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "two-product-amplification-analysis"
    / "result.json"
)
DRAW_COUNT: Final = 12_805
RECORD_VECTOR_COUNT: Final = 101
RECORD_WORD_COUNT: Final = RECORD_VECTOR_COUNT * 4

EXPECTED: Final = {
    PLAN_ROOT
    / "manifest.json": "980cfdc5c2e1af3c1f1f2dce592bfe8bfde17fd0a711c70a94fcbe34e013233c",
    PLAN: "60b6581e1ea6931e271b4dd45ed890ef291d5f6c2da2be0d16c285c5f4bc963c",
    VERTICES: "aaafaf5b1dcb505fd6a7645bc82c7e5fca4721db222f34fb1c959b97aa801947",
    RAW: "486855aee693e2bc6c1b28d4c765436e3df28840df2ca1e44a5df4fb409d738a",
    CAPTURE_MANIFEST: "6d4f28d822e71b012267cfe7256280fa2685f353e5a64104e6adcfa5a65e7b6e",
    STDERR: "f76b645f61341e59fc2d56618de34d1d02f49c1b54ea917f4a9bf3917b52e8df",
    STDOUT: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> JsonObject:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _load() -> tuple[JsonObject, np.ndarray, np.ndarray]:
    for path, expected in EXPECTED.items():
        if _sha256(path) != expected:
            raise ValueError(f"SHA-256 differs: {path.relative_to(ROOT)}")
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    manifest = json.loads(CAPTURE_MANIFEST.read_text(encoding="utf-8"))
    census = plan.get("census")
    capture = manifest.get("capture")
    if (
        plan.get("schema") != "walle-reveal-agx-setup-accumulator-plan-v1"
        or not isinstance(census, dict)
        or census.get("drawCount") != DRAW_COUNT
        or census.get("patternCount") != DRAW_COUNT
        or census.get("coefficientTripleCount") != DRAW_COUNT * 4
        or census.get("baseCaseCount") != 24
        or census.get("determinantRatioCount") != 10
        or census.get("discoveryPatternCount") != 9_496
        or census.get("holdoutPatternCount") != 3_309
        or manifest.get("schema") != "walle-reveal-agx-setup-accumulator-capture-v1"
        or not isinstance(capture, dict)
        or capture.get("recordCount") != DRAW_COUNT
        or capture.get("recordVectorCount") != RECORD_VECTOR_COUNT
        or capture.get("sha256") != EXPECTED[RAW]
        or STDOUT.stat().st_size != 0
    ):
        raise ValueError("two-product amplification closure differs")
    trace = STDERR.read_text(encoding="utf-8")
    if re.findall(
        r"^AGX_IO coefficient export patched handle=(\d+) shader=0x([0-9a-f]+)$",
        trace,
        flags=re.MULTILINE,
    ) != [("1", "28c0")] or re.findall(
        r"^AGX_IO coefficient export matches=(\d+) applied=(\d+)$",
        trace,
        flags=re.MULTILINE,
    ) != [("1", "1")]:
        raise ValueError("coefficient-export patch census differs")
    words = np.fromfile(RAW, dtype="<u4")
    vertices = np.fromfile(VERTICES, dtype="<u4")
    if words.size != DRAW_COUNT * RECORD_WORD_COUNT:
        raise ValueError("two-product amplification capture word count differs")
    if vertices.size != DRAW_COUNT * 3 * 8:
        raise ValueError("two-product amplification vertex word count differs")
    return (
        plan,
        words.reshape(DRAW_COUNT, RECORD_VECTOR_COUNT, 4),
        vertices.reshape(DRAW_COUNT, 3, 8),
    )


def _offset_summary(records: list[JsonObject]) -> JsonObject:
    exact_offsets: list[int] = []
    widths: list[int] = []
    baseline: list[int] = []
    clean_exact_offsets: list[int] = []
    for record in records:
        interval = record["compatibleJoinOffsetIntersection"]
        assert isinstance(interval, dict)
        values = interval["values"]
        assert isinstance(values, list)
        widths.append(len(values))
        if len(values) == 1:
            exact_offsets.append(int(values[0]))
            if record["slopesExact"]:
                clean_exact_offsets.append(int(values[0]))
        deltas = record["actualMinusPredictedFloatUlps"]
        assert isinstance(deltas, list)
        baseline.extend(int(delta) for delta in deltas)
    return {
        "drawCount": len(records),
        "baselineExactConstantCount": Counter(baseline)[0],
        "baselineConstantCount": len(baseline),
        "intervalWidthHistogram": {
            str(value): count for value, count in sorted(Counter(widths).items())
        },
        "uniqueJoinOffsetHistogram": {
            str(value): count for value, count in sorted(Counter(exact_offsets).items())
        },
        "cleanUniqueJoinOffsetHistogram": {
            str(value): count
            for value, count in sorted(Counter(clean_exact_offsets).items())
        },
    }


def analyze() -> JsonObject:
    plan, _words, _vertices = _load()
    experiments = plan["experiments"]
    assert isinstance(experiments, list)

    original_load = tomography._load  # noqa: SLF001
    original_draw_count = tomography.DRAW_COUNT
    tomography._load = _load  # type: ignore[method-assign]  # noqa: SLF001
    tomography.DRAW_COUNT = DRAW_COUNT
    try:
        base = tomography.analyze()
    finally:
        tomography._load = original_load  # type: ignore[method-assign]  # noqa: SLF001
        tomography.DRAW_COUNT = original_draw_count

    records = base["records"]
    assert isinstance(records, list)
    by_ratio: dict[str, list[JsonObject]] = defaultdict(list)
    by_case: dict[int, list[JsonObject]] = defaultdict(list)
    cross_ratio: dict[tuple[int, int, int], list[tuple[int, list[int]]]] = defaultdict(
        list
    )
    for record, experiment in zip(records, experiments, strict=True):
        assert isinstance(record, dict)
        assert isinstance(experiment, dict)
        ratio = str(experiment["determinantRatio"])
        ratio_index = int(experiment["ratioIndex"])
        case_index = int(experiment["caseIndex"])
        record["ratioIndex"] = ratio_index
        record["determinantRatio"] = ratio
        by_ratio[ratio].append(record)
        by_case[case_index].append(record)
        interval = record["compatibleJoinOffsetIntersection"]
        assert isinstance(interval, dict)
        values = interval["values"]
        assert isinstance(values, list)
        key = (
            case_index,
            int(experiment["firstNonanchorUlpOffset"]),
            int(experiment["secondNonanchorUlpOffset"]),
        )
        cross_ratio[key].append((ratio_index, [int(value) for value in values]))

    singleton_vectors: Counter[tuple[int, ...]] = Counter()
    complete_groups = 0
    complete_singleton_groups = 0
    for values in cross_ratio.values():
        values.sort()
        if len(values) != 10:
            continue
        complete_groups += 1
        if all(len(interval) == 1 for _ratio, interval in values):
            complete_singleton_groups += 1
            singleton_vectors[tuple(interval[0] for _ratio, interval in values)] += 1

    base["schema"] = "walle-reveal-agx-two-product-amplification-analysis-v1"
    base["classification"] = (
        "output-blind determinant-amplified two-product M1 coefficient tomography"
    )
    base["inputs"] = {
        "analyzer": _identity(Path(__file__).resolve()),
        "closure": [_identity(path) for path in EXPECTED],
    }
    base["census"].update(  # type: ignore[union-attr]
        {
            "drawCount": DRAW_COUNT,
            "discoveryDrawCount": 9_496,
            "holdoutDrawCount": 3_309,
            "determinantRatioCount": 10,
            "completeCrossRatioGroupCount": complete_groups,
            "completeSingletonCrossRatioGroupCount": complete_singleton_groups,
        }
    )
    base["amplification"] = {
        "byDeterminantRatio": {
            ratio: _offset_summary(ratio_records)
            for ratio, ratio_records in by_ratio.items()
        },
        "byBaseCase": {
            str(case): _offset_summary(case_records)
            for case, case_records in sorted(by_case.items())
        },
        "completeSingletonOffsetVectorHistogram": {
            ",".join(str(value) for value in vector): count
            for vector, count in singleton_vectors.most_common()
        },
    }
    base["conclusion"] = (
        "The authenticated coefficient export varies determinant magnitude while "
        "preserving the two-product operand family. Ratio-stratified join intervals "
        "are reported without reading rendered output or fitting a correction law."
    )
    return base


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    result = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["census"], indent=2, sort_keys=True))
    print(json.dumps(result["amplification"]["byDeterminantRatio"], indent=2))  # type: ignore[index]


if __name__ == "__main__":
    main()
