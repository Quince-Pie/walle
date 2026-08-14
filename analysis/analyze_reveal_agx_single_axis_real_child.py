#!/usr/bin/env python3.14
"""Invert the single-axis real-child M1 coefficient capture."""

import argparse
import hashlib
import json
import re
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np

import analyze_reveal_agx_setup_accumulator as accumulator
import analyze_reveal_agx_setup_tile_sweep as sweep
import analyze_reveal_agx_two_product_ruler as ruler
import analyze_reveal_agx_two_product_tomography as tomography


type JsonObject = dict[str, object]

ROOT: Final = Path(__file__).resolve().parent.parent
PLAN_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "single-axis-real-child-plan-v1"
)
CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-single-axis-real-child-v1"
)
PLAN: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTICES: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
RAW: Final = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
MANIFEST: Final = CAPTURE_ROOT / "capture" / "manifest.json"
STDERR: Final = CAPTURE_ROOT / "capture.stderr"
STDOUT: Final = CAPTURE_ROOT / "capture.stdout"
INTERPOSER: Final = CAPTURE_ROOT / "libwalle-agx-ldcf-export.dylib"
EXECUTABLE: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-probe"
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "single-axis-real-child-analysis"
    / "result.json"
)
DRAW_COUNT: Final = 364
RECORD_VECTOR_COUNT: Final = 101
RECORD_WORD_COUNT: Final = RECORD_VECTOR_COUNT * 4
EXPECTED: Final = {
    PLAN_ROOT
    / "manifest.json": "39a4e805b5d01888eb6438976c0a860dd95f7da99078391590a17d960448e717",
    PLAN: "f0f1518571031c2902090e928199134159ae4b3550cc5f6294a175aa1d591504",
    VERTICES: "3c9e360b1416ebdf139754c283e91fee1d8705ab502372d08dbf62b2c04bbf1d",
    RAW: "b82092dc0deb9d564aa676f8b5cbee8394b02b22811e85cba5019f4641297620",
    MANIFEST: "1ba8518def0019e251287d6dfb3fe5098fd5475bb12e0eff04d6ce9b16bbbfb2",
    STDERR: "3608d8d1097d67b6652afd47f0378e32526876af92f8dde03afb9570151c0c4a",
    STDOUT: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    INTERPOSER: "9ead667be857c2fa3ed8a9b110d6d33edb24cf6d7ddf575427d17740e0ff1e8f",
    EXECUTABLE: "0eba15db7f872a845398f91cacce7446f9e92cbd22276da3e42e5b9148be4ea9",
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


def _compatible_source_offsets(
    actual_middle_offset: int,
    baseline_middle: tuple[int, int, int],
    baseline_numerator: tuple[int, int, int],
    displacement: Fraction,
) -> list[int]:
    middle_sign, middle_index, middle_exponent = baseline_middle
    numerator_sign, numerator_index, numerator_exponent = baseline_numerator
    distance_index, distance_exponent = accumulator._positive_float_components(  # noqa: SLF001
        accumulator.setup._float_bits(float(abs(displacement)))  # noqa: SLF001
    )
    target_index = middle_index + actual_middle_offset
    compatible: list[int] = []
    for offset in range(-512, 513):
        if numerator_index + offset <= 0:
            continue
        candidate_index, candidate_exponent = (
            accumulator.coefficient.column_product_stage(
                numerator_index + offset,
                numerator_exponent,
                distance_index,
                distance_exponent,
                output_bits=27,
                truncation_bits=19,
                bias_units=10,
                carry_mode="top-columns",
                propagated_column_count=1,
                sticky_carry_limit=1,
            )
        )
        if (
            candidate_index == target_index
            and candidate_exponent == middle_exponent
            and numerator_sign * (-1 if displacement < 0 else 1) == middle_sign
        ):
            compatible.append(offset)
    return compatible


def analyze() -> JsonObject:
    for path, expected in EXPECTED.items():
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"SHA-256 differs: {path.relative_to(ROOT)}: {actual}")
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    census = plan.get("census")
    capture = manifest.get("capture")
    if (
        plan.get("schema") != "walle-reveal-agx-setup-accumulator-plan-v1"
        or not isinstance(census, dict)
        or census.get("drawCount") != DRAW_COUNT
        or census.get("coefficientTripleCount") != DRAW_COUNT * 4
        or census.get("variantDrawCounts")
        != {"x-zero-y-only": 120, "y-zero-x-only": 244}
        or not isinstance(capture, dict)
        or capture.get("recordCount") != DRAW_COUNT
        or capture.get("sha256") != EXPECTED[RAW]
        or STDOUT.stat().st_size != 0
    ):
        raise ValueError("single-axis real-child closure differs")
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
        raise ValueError("coefficient export census differs")
    words = np.fromfile(RAW, dtype="<u4").reshape(DRAW_COUNT, RECORD_VECTOR_COUNT, 4)
    vertex_words = np.fromfile(VERTICES, dtype="<u4").reshape(DRAW_COUNT, 3, 8)
    experiments = sweep._require_list(plan.get("experiments"), "experiments")  # noqa: SLF001
    draws = sweep._require_list(plan.get("draws"), "draws")  # noqa: SLF001
    bitmap = accumulator.setup.P25_PATH.read_bytes()

    middle_widths = Counter[int]()
    middle_offsets = Counter[int]()
    source_widths = Counter[int]()
    source_offsets = Counter[int]()
    by_variant = {
        "x-zero-y-only": Counter[int](),
        "y-zero-x-only": Counter[int](),
    }
    records: list[JsonObject] = []
    for experiment_value, draw_value in zip(experiments, draws, strict=True):
        experiment = sweep._require_dict(experiment_value, "experiment")  # noqa: SLF001
        draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
        record_index = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
        vertices = sweep._vertices(vertex_words, record_index)  # noqa: SLF001
        positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
        determinant = accumulator.setup._determinant(positions)  # noqa: SLF001
        top_left = accumulator.top_left._top_left(positions)  # noqa: SLF001
        zero_axis = sweep._require_int(experiment.get("zeroAxis"), "zero axis")  # noqa: SLF001
        active_axis = 1 - zero_axis
        tile = (
            sweep._require_int(draw.get("tileX"), "tile x"),  # noqa: SLF001
            sweep._require_int(draw.get("tileY"), "tile y"),  # noqa: SLF001
        )
        displacement = Fraction(
            tile[active_axis] * 32 * 256 - positions[top_left][active_axis], 256
        )
        if (
            displacement == 0
            or Fraction(
                tile[zero_axis] * 32 * 256 - positions[top_left][zero_axis], 256
            )
            != 0
        ):
            raise ValueError("single-axis displacement contract differs")
        term_value = experiment.get("middleTerm")
        anchors_value = sweep._require_list(experiment.get("anchors"), "anchors")  # noqa: SLF001
        if not isinstance(term_value, dict) or len(anchors_value) != 4:
            raise ValueError("single-axis metadata differs")
        term = (
            sweep._require_int(term_value.get("sign"), "term sign"),
            sweep._require_int(term_value.get("index"), "term index"),
            sweep._require_int(term_value.get("exponent"), "term exponent"),
        )
        selector, selector_exponent = accumulator.setup._p25_selector(
            determinant, bitmap
        )  # noqa: SLF001
        compatible_sets: list[set[int]] = []
        triples = accumulator._triples(words[record_index])  # noqa: SLF001
        for anchor_value, triple in zip(anchors_value, triples, strict=True):
            anchor = sweep._require_dict(anchor_value, "anchor")  # noqa: SLF001
            anchor_bits = int(str(anchor["anchorBits"]), 16)
            compatible_sets.append(
                tomography._compatible_offsets(  # noqa: SLF001
                    int(triple[2]),
                    anchor_bits,
                    term[0],
                    term[1],
                    term[2],
                    selector,
                    selector_exponent,
                )
            )
        compatible_middle = sorted(set.intersection(*compatible_sets))
        middle_widths[len(compatible_middle)] += 1
        if len(compatible_middle) == 1:
            middle_offsets[compatible_middle[0]] += 1
        baseline_numerator, products = ruler._slope_numerator(  # noqa: SLF001
            vertices, 0, active_axis, top_left
        )
        compatible_source = sorted(
            {
                offset
                for middle_offset in compatible_middle
                for offset in _compatible_source_offsets(
                    middle_offset,
                    term,
                    baseline_numerator,
                    displacement,
                )
            }
        )
        source_widths[len(compatible_source)] += 1
        variant = str(experiment["variant"])
        if len(compatible_source) == 1:
            source_offsets[compatible_source[0]] += 1
            by_variant[variant][compatible_source[0]] += 1
        records.append(
            {
                "recordIndex": record_index,
                "variant": variant,
                "zeroAxis": zero_axis,
                "activeAxis": active_axis,
                "variableUlpOffset": experiment["variableUlpOffset"],
                "split": experiment["split"],
                "baselineMiddleTerm": {
                    "sign": term[0],
                    "index": term[1],
                    "exponent": term[2],
                },
                "compatibleMiddleTermOffsets": compatible_middle,
                "baselineSourceNumerator": {
                    "sign": baseline_numerator[0],
                    "index": baseline_numerator[1],
                    "exponent": baseline_numerator[2],
                },
                "sourceProducts": list(products),
                "compatibleSourceNumeratorOffsets": compatible_source,
            }
        )

    return {
        "schema": "walle-reveal-agx-single-axis-real-child-analysis-v1",
        "classification": "output-blind single-axis M1 source-product inversion",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExports": True,
            "hasFrozenDiscoveryHoldoutSplit": True,
            "establishesTwoSourceProductLaw": False,
            "authorizesProductionMutation": False,
        },
        "inputs": {
            "analyzer": _identity(Path(__file__).resolve()),
            "closure": [_identity(path) for path in EXPECTED],
        },
        "census": {
            "drawCount": DRAW_COUNT,
            "middleOffsetWidthHistogram": {
                str(key): value for key, value in sorted(middle_widths.items())
            },
            "uniqueMiddleOffsetHistogram": {
                str(key): value for key, value in sorted(middle_offsets.items())
            },
            "sourceOffsetWidthHistogram": {
                str(key): value for key, value in sorted(source_widths.items())
            },
            "uniqueSourceOffsetHistogram": {
                str(key): value for key, value in sorted(source_offsets.items())
            },
            "uniqueSourceOffsetHistogramByVariant": {
                variant: {str(key): value for key, value in sorted(counts.items())}
                for variant, counts in by_variant.items()
            },
        },
        "records": records,
        "conclusion": (
            "One tile displacement is exactly zero in each variant. Four common "
            "anchors invert the surviving middle term, which is then propagated "
            "back through the measured displacement product to bound the two-source "
            "slope numerator. No rendered output is opened."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    result = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
