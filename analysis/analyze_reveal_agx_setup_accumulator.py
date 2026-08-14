#!/usr/bin/env python3
"""Authenticate and analyze the AGX triangle-setup accumulator capture.

The Metal probe submits direct public post-guard triangles with translated,
scaled, signed, and constant vertex varyings.  The command-stream interposer
replaces the probe's offset stores with the rasterizer's raw ``LDCF``
coefficient triples.  This analyzer validates the complete copied closure,
reconstructs every submitted vertex word from the public reveal catalog, and
scores input-only setup hypotheses without opening a rendered image.
"""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


ROOT: Final = Path(__file__).resolve().parent.parent
ANALYSIS: Final = ROOT / "analysis"
LG_ANALYSIS: Final = ROOT / "lg-test" / "Analysis"
sys.path[:0] = [str(ANALYSIS), str(LG_ANALYSIS)]

import analyze_reveal_agx_basis_phase as phase  # noqa: E402
import analyze_reveal_agx_clip_setup_split as setup  # noqa: E402
import analyze_reveal_agx_ldcf_export as export  # noqa: E402
import analyze_reveal_agx_top_left_setup as top_left  # noqa: E402
import generate_reveal_agx_setup_accumulator_plan as generator  # noqa: E402
import raster_tile_coefficient_model_v3 as coefficient  # noqa: E402
import raster_tile_selector_model as tile  # noqa: E402
import raster_tile_selector_model_v4 as composite  # noqa: E402


type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
type JsonObject = dict[str, JsonValue]
type CoefficientTriple = tuple[int, int, int]
type Vertex = tuple[float, ...]
type CaptureWords = NDArray[np.uint32]

CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-setup-accumulator-v1"
)
PLAN_PATH: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTEX_PATH: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
CAPTURE_DIRECTORY: Final = CAPTURE_ROOT / "capture"
CAPTURE_MANIFEST: Final = CAPTURE_DIRECTORY / "manifest.json"
CAPTURE_RAW: Final = CAPTURE_DIRECTORY / "reveal-agx-setup-accumulator.raw"
TRACE_PATH: Final = CAPTURE_ROOT / "capture.stderr"
INVENTORY_PATH: Final = CAPTURE_ROOT / "inventory.sha256"
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "setup-accumulator-analysis"
    / "reveal-agx-setup-accumulator-result.json"
)

EXPECTED_IDENTITIES: Final = {
    generator.CATALOG_DEFAULT: (
        "bc8b96dc4d3dc7c2fb6383dda49baa839eb207b60128739604ad8ddcd9402bd6"
    ),
    ANALYSIS / "generate_reveal_agx_setup_accumulator_plan.py": (
        "72dd82d1c5e66b7875f34638513021a35cc286bafe037e614d68fb5e966d89c6"
    ),
    ANALYSIS / "reveal_agx_setup_accumulator_probe.swift": (
        "c7b786b7a7144959dfb44447289360fcd16dd801ccc0f8d8aec995c8a6c04e42"
    ),
    ANALYSIS / "macos_agx_iokit_trace.c": (
        "27395c57e1e086724eeb402aa9cbda4fb9cfbed7ae82e8ccf97b32fc1d50b6f6"
    ),
    ANALYSIS / "analyze_reveal_agx_basis_phase.py": (
        "620c254f9774b36f1fdeba70706d4a9cd4a2dafa0f518e4e19cdab987a8dc692"
    ),
    ANALYSIS / "analyze_reveal_agx_clip_setup_split.py": (
        "591f9a9fef2caafe43d4d1464377deeacaf2fd5c057cb1337703ac3a1f4f820c"
    ),
    ANALYSIS / "analyze_reveal_agx_ldcf_export.py": (
        "8b611d3137efe94bb776cd95d47ff066414984ec7e924d659aea37f41967d8b2"
    ),
    ANALYSIS / "analyze_reveal_agx_top_left_setup.py": (
        "b5540da8bf406a0ffd48c07fb2e04e60ab37cbcb5bd465a1106aabd92f9f4f48"
    ),
    setup.P25_PATH: setup.P25_SHA256,
    PLAN_PATH: "d867bb4c1ca09c12ef41ae0695694dc772107534aeefadfec596fa621b8fbaf2",
    VERTEX_PATH: ("a58c6ce54e7087f8957a841646fcd27fd50e9b58d4983c6a83e01aed4408adf4"),
    CAPTURE_MANIFEST: (
        "05a3fca22bc66242e46ebbea4da9796aa93fef46c558fa9a724051344f042f71"
    ),
    CAPTURE_RAW: "7f043a504bdbc6c0f0b266a77cd28c9368e7d24da52e90677a16a1ff8a64c927",
    TRACE_PATH: "bd6069ceaa39e8ce11ee6ed9ca07b0b7e09de6406051457552af01cb2c5ad1c3",
    INVENTORY_PATH: (
        "8abce7a17eb79bb5f48cbe2e70089964590248be2aff75c357be9ca6b076be3b"
    ),
    CAPTURE_ROOT / "libwalle-agx-ldcf-export.dylib": (
        "3b7cd0a7d925b85953a688b818d8a2892af33fd8b8ca14333c56614be1775610"
    ),
    CAPTURE_ROOT / "reveal-agx-setup-accumulator-probe": (
        "644f04ead8a07779c782030b2659e7aea88a031da142532fcbf69bfcfedde298"
    ),
}

CAPTURE_SCHEMA: Final = "walle-reveal-agx-setup-accumulator-capture-v1"
PLAN_SCHEMA: Final = "walle-reveal-agx-setup-accumulator-plan-v1"
RECORD_VECTOR_COUNT: Final = 101
RECORD_WORD_COUNT: Final = RECORD_VECTOR_COUNT * 4
PATCHED_LINE: Final = re.compile(
    r"^AGX_IO coefficient export patched handle=(\d+) shader=0x([0-9a-f]+)$",
    flags=re.MULTILINE,
)
MATCH_LINE: Final = re.compile(
    r"^AGX_IO coefficient export matches=(\d+) applied=(\d+)$",
    flags=re.MULTILINE,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _identity(path: Path) -> JsonObject:
    return {
        "path": _relative(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _require_dict(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} is not an object")
    return value  # type: ignore[return-value]


def _require_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} is not an array")
    return value


def _require_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} is not an integer")
    return value


def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} is not a string")
    return value


def _verify_identities() -> list[JsonObject]:
    identities: list[JsonObject] = []
    for path, expected in EXPECTED_IDENTITIES.items():
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"SHA-256 differs for {_relative(path)}: {actual}")
        identities.append(_identity(path))
    return identities


def _verify_inventory() -> JsonObject:
    expected_paths: set[str] = set()
    entries: list[JsonObject] = []
    for line_number, line in enumerate(
        INVENTORY_PATH.read_text(encoding="utf-8").splitlines(), start=1
    ):
        try:
            expected, relative = line.split("  ", maxsplit=1)
        except ValueError as error:
            raise ValueError(f"invalid inventory line {line_number}") from error
        path = ROOT / relative
        if not path.is_file() or _sha256(path) != expected:
            raise ValueError(f"inventory entry differs: {relative}")
        expected_paths.add(
            path.resolve().relative_to(CAPTURE_ROOT.resolve()).as_posix()
        )
        entries.append(_identity(path))
    actual_paths = {
        path.relative_to(CAPTURE_ROOT).as_posix()
        for path in CAPTURE_ROOT.rglob("*")
        if path.is_file() and path != INVENTORY_PATH
    }
    if expected_paths != actual_paths:
        raise ValueError("capture inventory file set differs")
    return {
        "path": _relative(INVENTORY_PATH),
        "sha256": _sha256(INVENTORY_PATH),
        "entryCount": len(entries),
        "exactFileSet": True,
        "entries": entries,
    }


def _load_plan() -> tuple[dict[str, object], tuple[phase.Sample, ...]]:
    catalog, samples = phase._load_catalog(generator.CATALOG_DEFAULT)  # noqa: SLF001
    plan = _require_dict(json.loads(PLAN_PATH.read_text(encoding="utf-8")), "plan")
    if plan.get("schema") != PLAN_SCHEMA:
        raise ValueError("plan schema differs")
    census = _require_dict(plan.get("census"), "plan census")
    if census != {
        "coefficientTripleCount": 10_272,
        "drawCount": 2_568,
        "patternCount": 107,
        "targetCount": 8,
    }:
        raise ValueError("plan census differs")
    if plan.get("authority") != {
        "establishesAGXAccumulatorLaw": False,
        "opensReferencePixels": False,
        "usesOutputFeedback": False,
        "usesPublicRevealGeometryOnly": True,
    }:
        raise ValueError("plan authority differs")
    catalog_identity = _require_dict(plan.get("catalog"), "plan catalog")
    if catalog_identity.get("sha256") != _sha256(generator.CATALOG_DEFAULT):
        raise ValueError("plan catalog identity differs")

    patterns = generator._patterns()  # noqa: SLF001
    expected_pattern_metadata = [
        {key: value for key, value in pattern.items() if key != "values"}
        for pattern in patterns
    ]
    if plan.get("patterns") != expected_pattern_metadata:
        raise ValueError("plan patterns differ from generator")

    sample_by_record = {sample.record_index: sample for sample in samples}
    expected_targets: list[JsonObject] = []
    expected_draws: list[JsonObject] = []
    expected_vertices = bytearray()
    for target_index, target_record in enumerate(generator.TARGET_RECORDS):
        target = sample_by_record[target_record]
        siblings = tuple(
            sample
            for sample in samples
            if sample.case_index == target.case_index
            and sample.child_ordinal == target.child_ordinal
        )
        expected_targets.append(
            {
                "targetRecordIndex": target_record,
                "caseIndex": target.case_index,
                "state": target.state,
                "sourcePrimitive": target.source_primitive,
                "childOrdinal": target.child_ordinal,
                "childOrdinalWithinSource": target.child_ordinal_within_source,
                "sampleRecords": [sample.record_index for sample in siblings],
                "pixels": [list(sample.pixel) for sample in siblings],
                "tiles": [list(sample.tile) for sample in siblings],
            }
        )
        child = phase._canonical_children(target)[  # noqa: SLF001
            target.child_ordinal_within_source
        ]
        for sample in siblings:
            for pattern_index, pattern in enumerate(patterns):
                values = pattern["values"]
                if not isinstance(values, tuple):
                    raise ValueError("generated pattern values differ")
                record_index = len(expected_draws)
                for local_vertex, vertex in enumerate(child):
                    lane_values = tuple(values[lane][local_vertex] for lane in range(4))
                    expected_vertices.extend(
                        generator.VERTEX.pack(
                            phase._bits(vertex[0]),  # noqa: SLF001
                            phase._bits(vertex[1]),  # noqa: SLF001
                            0,
                            0,
                            *(phase._bits(value) for value in lane_values),  # noqa: SLF001
                        )
                    )
                expected_draws.append(
                    {
                        "recordIndex": record_index,
                        "targetIndex": target_index,
                        "targetRecordIndex": target_record,
                        "sampleRecordIndex": sample.record_index,
                        "sampleOrdinal": sample.sample_ordinal,
                        "patternIndex": pattern_index,
                        "x": sample.pixel[0],
                        "y": sample.pixel[1],
                        "tileX": sample.tile[0],
                        "tileY": sample.tile[1],
                    }
                )
    if plan.get("targets") != expected_targets or plan.get("draws") != expected_draws:
        raise ValueError("plan targets or draw ordering differ")
    if VERTEX_PATH.read_bytes() != expected_vertices:
        raise ValueError("vertex data differs from public reconstruction")
    if catalog.get("census") is None:
        raise ValueError("catalog census is absent")
    return plan, samples


def _load_capture(plan: dict[str, object]) -> tuple[dict[str, object], CaptureWords]:
    manifest = _require_dict(
        json.loads(CAPTURE_MANIFEST.read_text(encoding="utf-8")), "capture manifest"
    )
    if manifest.get("schema") != CAPTURE_SCHEMA:
        raise ValueError("capture schema differs")
    if manifest.get("authority") != {
        "establishesAGXAccumulatorLaw": False,
        "mutatesProductionRenderer": False,
        "opensReferencePixels": False,
        "usesPublicRevealInputsOnly": True,
    }:
        raise ValueError("capture authority differs")
    expected_nested = {
        "plan": (PLAN_PATH, "file"),
        "vertexData": (VERTEX_PATH, "file"),
        "capture": (CAPTURE_RAW, "file"),
    }
    for name, (path, file_key) in expected_nested.items():
        identity = _require_dict(manifest.get(name), f"manifest {name}")
        if (
            identity.get(file_key) != path.name
            or identity.get("bytes") != path.stat().st_size
            or identity.get("sha256") != _sha256(path)
        ):
            raise ValueError(f"manifest {name} identity differs")
    executable = _require_dict(manifest.get("executable"), "manifest executable")
    if executable.get("bytes") != (
        CAPTURE_ROOT / "reveal-agx-setup-accumulator-probe"
    ).stat().st_size or executable.get("sha256") != _sha256(
        CAPTURE_ROOT / "reveal-agx-setup-accumulator-probe"
    ):
        raise ValueError("manifest executable identity differs")
    capture = _require_dict(manifest.get("capture"), "manifest capture")
    draw_count = _require_int(
        _require_dict(plan.get("census"), "plan census").get("drawCount"),
        "draw count",
    )
    if (
        capture.get("recordCount") != draw_count
        or capture.get("recordVectorCount") != RECORD_VECTOR_COUNT
        or capture.get("recordBytes") != RECORD_WORD_COUNT * 4
    ):
        raise ValueError("capture shape differs")
    words = np.fromfile(CAPTURE_RAW, dtype="<u4")
    if words.size != draw_count * RECORD_WORD_COUNT:
        raise ValueError("capture word count differs")

    trace = TRACE_PATH.read_text(encoding="utf-8", errors="strict")
    if PATCHED_LINE.findall(trace) != [("1", "28c0")]:
        raise ValueError("coefficient patch target differs")
    if MATCH_LINE.findall(trace) != [("1", "1")]:
        raise ValueError("coefficient patch count differs")
    return manifest, words.reshape(draw_count, RECORD_VECTOR_COUNT, 4)


def _triples(record: NDArray[np.uint32]) -> tuple[CoefficientTriple, ...]:
    return export._triples(record)  # noqa: SLF001


def _validate_records(plan: dict[str, object], words: CaptureWords) -> JsonObject:
    draws = _require_list(plan.get("draws"), "plan draws")
    metadata_words = 0
    repeated_words = 0
    finite_words = 0
    for draw_value in draws:
        draw = _require_dict(draw_value, "plan draw")
        record_index = _require_int(draw.get("recordIndex"), "record index")
        record = words[record_index]
        expected = (
            (
                _require_int(draw.get("x"), "draw x"),
                _require_int(draw.get("y"), "draw y"),
                0,
                _require_int(draw.get("targetIndex"), "target index"),
            ),
            (
                record_index,
                _require_int(draw.get("targetRecordIndex"), "target record"),
                _require_int(draw.get("sampleRecordIndex"), "sample record"),
                _require_int(draw.get("patternIndex"), "pattern index"),
            ),
            (
                _require_int(draw.get("tileX"), "tile x"),
                _require_int(draw.get("tileY"), "tile y"),
                _require_int(draw.get("sampleOrdinal"), "sample ordinal"),
                _require_int(draw.get("targetIndex"), "target index"),
            ),
        )
        for vector_index, expected_vector in enumerate(expected):
            if tuple(int(value) for value in record[vector_index]) != expected_vector:
                raise ValueError(f"record {record_index} metadata differs")
            metadata_words += 4
        triples = _triples(record)
        finite_words += len(triples) * 3
        repeated_words += len(triples) * 15 * 3
    return {
        "recordCount": len(draws),
        "metadataWordComparisonCount": metadata_words,
        "coefficientTripleCount": len(draws) * 4,
        "coefficientWordCount": finite_words,
        "repeatedStoreWordComparisonCount": repeated_words,
        "allCoefficientWordsFinite": True,
    }


def _power_of_two(exponent: int) -> Fraction:
    return Fraction(1 << exponent) if exponent >= 0 else Fraction(1, 1 << -exponent)


def _positive_float_components(bits: int) -> tuple[int, int]:
    exponent = (bits >> 23) & 0xFF
    if bits >> 31 or exponent in {0, 0xFF}:
        raise ValueError("positive normal binary32 required")
    return (1 << 23) | (bits & 0x7F_FFFF), exponent - 150


def _factorized_tile_term(
    signed_numerator: tuple[int, int, int],
    determinant: int,
    displacement: Fraction,
    bitmap: bytes,
) -> Fraction:
    sign, numerator, numerator_exponent = signed_numerator
    if sign == 0 or displacement == 0:
        return Fraction()
    displacement_bits = setup._float_bits(float(abs(displacement)))  # noqa: SLF001
    distance_index, distance_exponent = _positive_float_components(displacement_bits)
    middle_index, middle_exponent = coefficient.column_product_stage(
        numerator,
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
    selector, selector_exponent = setup._p25_selector(  # noqa: SLF001
        determinant, bitmap
    )
    output_index, output_exponent = tile.product_stage(
        middle_index,
        middle_exponent,
        selector,
        selector_exponent,
        output_bits=27,
        truncation_bits=19,
        bias_units=20,
    )
    value = output_index * _power_of_two(output_exponent)
    if sign * (-1 if displacement < 0 else 1) < 0:
        value = -value
    return value


def _factorized_constant_bits(
    vertices: tuple[Vertex, Vertex, Vertex],
    component_index: int,
    tile_position: tuple[int, int],
    bitmap: bytes,
) -> int:
    anchor, x_term, y_term = _factorized_components(
        vertices,
        component_index,
        tile_position,
        bitmap,
    )
    return composite.quantize_composite_constant_bits(anchor + x_term + y_term)


def _factorized_components(
    vertices: tuple[Vertex, Vertex, Vertex],
    component_index: int,
    tile_position: tuple[int, int],
    bitmap: bytes,
) -> tuple[Fraction, Fraction, Fraction]:
    positions = setup._fixed_positions(vertices)  # noqa: SLF001
    determinant = setup._determinant(positions)  # noqa: SLF001
    anchor = top_left._top_left(positions)  # noqa: SLF001
    values = tuple(
        setup._float32(vertex[2 + component_index])  # noqa: SLF001
        for vertex in vertices
    )
    edges = (
        (
            positions[1][1] - positions[2][1],
            positions[2][1] - positions[0][1],
            positions[0][1] - positions[1][1],
        ),
        (
            positions[2][0] - positions[1][0],
            positions[0][0] - positions[2][0],
            positions[1][0] - positions[0][0],
        ),
    )
    terms: list[Fraction] = []
    for axis in range(2):
        numerator = sum(
            (
                setup._first_product(  # noqa: SLF001
                    setup._float32(values[index] - values[anchor]),  # noqa: SLF001
                    edges[axis][index] / 256.0,
                    bias_units=15,
                )
                for index in range(3)
                if index != anchor
            ),
            start=Fraction(),
        )
        normalized = setup._normalize_signed(  # noqa: SLF001
            numerator, precision_bits=27, rounding="nearest-even"
        )
        displacement = Fraction(
            tile_position[axis] * 32 * 256 - positions[anchor][axis], 256
        )
        terms.append(
            _factorized_tile_term(normalized, determinant, displacement, bitmap)
        )
    return (
        export._fraction(setup._float_bits(values[anchor])),  # noqa: SLF001
        terms[0],
        terms[1],
    )


def _quantize_signed(
    value: Fraction,
    precision_bits: int,
    *,
    rounding: str,
) -> Fraction:
    if value == 0:
        return value
    magnitude = tile.quantize_binary_significand(
        abs(value),
        precision_bits,
        rounding=rounding,
    )
    return -magnitude if value < 0 else magnitude


def _delta_summary(deltas: list[int]) -> JsonObject:
    counts = Counter(deltas)
    return {
        "count": len(deltas),
        "exactCount": counts[0],
        "withinOneUlpCount": sum(
            count for delta, count in counts.items() if abs(delta) <= 1
        ),
        "minimumUlpDelta": min(deltas),
        "maximumUlpDelta": max(deltas),
        "smallDeltaHistogram": {
            str(delta): counts[delta] for delta in range(-16, 17) if counts[delta]
        },
    }


def _vertices(
    vertex_words: NDArray[np.uint32], record_index: int
) -> tuple[Vertex, ...]:
    return tuple(
        (
            phase._float(int(vertex[0])),  # noqa: SLF001
            phase._float(int(vertex[1])),  # noqa: SLF001
            *(phase._float(int(word)) for word in vertex[4:8]),  # noqa: SLF001
        )
        for vertex in vertex_words[record_index]
    )


def _factorized_model(
    plan: dict[str, object], words: CaptureWords, vertex_words: NDArray[np.uint32]
) -> JsonObject:
    bitmap = setup.P25_PATH.read_bytes()
    deltas: list[int] = []
    by_kind: dict[str, list[int]] = defaultdict(list)
    first_mismatches: list[JsonObject] = []
    patterns = _require_list(plan.get("patterns"), "plan patterns")
    for draw_value in _require_list(plan.get("draws"), "plan draws"):
        draw = _require_dict(draw_value, "plan draw")
        record_index = _require_int(draw.get("recordIndex"), "record index")
        pattern_index = _require_int(draw.get("patternIndex"), "pattern index")
        pattern = _require_dict(patterns[pattern_index], "pattern")
        kind = _require_str(pattern.get("kind"), "pattern kind")
        actual = _triples(words[record_index])
        vertices = _vertices(vertex_words, record_index)
        tile_position = (
            _require_int(draw.get("tileX"), "tile x"),
            _require_int(draw.get("tileY"), "tile y"),
        )
        for component_index in range(4):
            predicted = _factorized_constant_bits(
                vertices, component_index, tile_position, bitmap
            )
            delta = export._float_ulp_delta(  # noqa: SLF001
                actual[component_index][2], predicted
            )
            deltas.append(delta)
            by_kind[kind].append(delta)
            if delta and len(first_mismatches) < 32:
                first_mismatches.append(
                    {
                        "recordIndex": record_index,
                        "targetRecordIndex": draw["targetRecordIndex"],
                        "sampleRecordIndex": draw["sampleRecordIndex"],
                        "patternIndex": pattern_index,
                        "patternKind": kind,
                        "component": component_index,
                        "actualBits": f"0x{actual[component_index][2]:08x}",
                        "predictedBits": f"0x{predicted:08x}",
                        "actualMinusPredictedFloatUlps": delta,
                    }
                )
    result = {
        "name": "top-left-factorized-p27-tile-p28",
        "arithmetic": {
            "anchor": "minimum quantized y, then x, then vertex index",
            "slopeNumerator": "two 27-bit products, bias 15, p27 nearest",
            "tileProduct": "p27, truncate low 19, bias 10, one carry column",
            "reciprocalProduct": "P25 p27, truncate low 19, bias 20",
            "composite": "anchor + x term + y term, p28 nearest then binary32",
        },
        "overall": _delta_summary(deltas),
        "byPatternKind": {
            kind: _delta_summary(kind_deltas)
            for kind, kind_deltas in sorted(by_kind.items())
        },
        "firstMismatches": first_mismatches,
    }
    overall = _require_dict(result["overall"], "factorized overall")
    expected_by_kind = {
        "unit-onehot-plus-base": (4_992, 4_674, 4_933),
        "scaled-onehot-plus-one": (2_496, 2_399, 2_482),
        "signed-cancellation-about-one": (2_496, 2_293, 2_452),
        "constant-control": (288, 264, 288),
    }
    actual_by_kind = _require_dict(result["byPatternKind"], "factorized kinds")
    if (
        overall.get("count") != 10_272
        or overall.get("exactCount") != 9_630
        or overall.get("withinOneUlpCount") != 10_155
    ):
        raise ValueError("factorized accumulator census differs")
    for kind, expected in expected_by_kind.items():
        summary = _require_dict(actual_by_kind.get(kind), f"factorized {kind}")
        if (
            summary.get("count"),
            summary.get("exactCount"),
            summary.get("withinOneUlpCount"),
        ) != expected:
            raise ValueError(f"factorized {kind} census differs")
    return result


def _accumulated_constant_bits(
    anchor: Fraction,
    x_term: Fraction,
    y_term: Fraction,
    mode: str,
) -> int:
    match mode:
        case "unquantized-x-plus-y":
            value = anchor + x_term + y_term
        case "x-plus-y-p27-nearest":
            value = anchor + _quantize_signed(
                x_term + y_term,
                27,
                rounding="nearest-even",
            )
        case "x-plus-y-p27-down":
            value = anchor + _quantize_signed(
                x_term + y_term,
                27,
                rounding="down",
            )
        case "x-plus-y-p27-up":
            value = anchor + _quantize_signed(
                x_term + y_term,
                27,
                rounding="up",
            )
        case "x-plus-y-p28-nearest":
            value = anchor + _quantize_signed(
                x_term + y_term,
                28,
                rounding="nearest-even",
            )
        case "anchor-plus-x-p27-then-y":
            value = (
                _quantize_signed(anchor + x_term, 27, rounding="nearest-even") + y_term
            )
        case "anchor-plus-y-p27-then-x":
            value = (
                _quantize_signed(anchor + y_term, 27, rounding="nearest-even") + x_term
            )
        case "half-anchor-in-each-axis-p27":
            value = _quantize_signed(
                anchor / 2 + x_term,
                27,
                rounding="nearest-even",
            ) + _quantize_signed(
                anchor / 2 + y_term,
                27,
                rounding="nearest-even",
            )
        case _:
            raise ValueError(f"unknown accumulator mode: {mode}")
    return composite.quantize_composite_constant_bits(value)


def _accumulation_discriminator(
    plan: dict[str, object],
    words: CaptureWords,
    vertex_words: NDArray[np.uint32],
) -> JsonObject:
    bitmap = setup.P25_PATH.read_bytes()
    modes = (
        "unquantized-x-plus-y",
        "x-plus-y-p27-nearest",
        "x-plus-y-p27-down",
        "x-plus-y-p27-up",
        "x-plus-y-p28-nearest",
        "anchor-plus-x-p27-then-y",
        "anchor-plus-y-p27-then-x",
        "half-anchor-in-each-axis-p27",
    )
    deltas: dict[str, dict[str, list[int]]] = {
        mode: {"discovery": [], "holdout": [], "all": []} for mode in modes
    }
    patterns = _require_list(plan.get("patterns"), "plan patterns")
    nonconstant_count = 0
    for draw_value in _require_list(plan.get("draws"), "plan draws"):
        draw = _require_dict(draw_value, "plan draw")
        pattern_index = _require_int(draw.get("patternIndex"), "pattern index")
        pattern = _require_dict(patterns[pattern_index], "pattern")
        if pattern.get("kind") == "constant-control":
            continue
        record_index = _require_int(draw.get("recordIndex"), "record index")
        target_index = _require_int(draw.get("targetIndex"), "target index")
        split = "discovery" if target_index < 4 else "holdout"
        actual = _triples(words[record_index])
        vertices = _vertices(vertex_words, record_index)
        tile_position = (
            _require_int(draw.get("tileX"), "tile x"),
            _require_int(draw.get("tileY"), "tile y"),
        )
        for component_index in range(4):
            anchor, x_term, y_term = _factorized_components(
                vertices,
                component_index,
                tile_position,
                bitmap,
            )
            for mode in modes:
                predicted = _accumulated_constant_bits(
                    anchor,
                    x_term,
                    y_term,
                    mode,
                )
                delta = export._float_ulp_delta(  # noqa: SLF001
                    actual[component_index][2],
                    predicted,
                )
                deltas[mode][split].append(delta)
                deltas[mode]["all"].append(delta)
            nonconstant_count += 1

    candidates = {
        mode: {
            split: _delta_summary(split_deltas)
            for split, split_deltas in mode_deltas.items()
        }
        for mode, mode_deltas in deltas.items()
    }
    selected = _require_dict(
        candidates["x-plus-y-p27-nearest"],
        "selected accumulator candidate",
    )
    selected_all = _require_dict(selected["all"], "selected all split")
    selected_discovery = _require_dict(
        selected["discovery"], "selected discovery split"
    )
    selected_holdout = _require_dict(selected["holdout"], "selected holdout split")
    if (
        nonconstant_count != 9_984
        or (
            selected_all.get("exactCount"),
            selected_all.get("withinOneUlpCount"),
        )
        != (9_494, 9_882)
        or (
            selected_discovery.get("exactCount"),
            selected_discovery.get("withinOneUlpCount"),
        )
        != (4_751, 4_951)
        or (
            selected_holdout.get("exactCount"),
            selected_holdout.get("withinOneUlpCount"),
        )
        != (4_743, 4_931)
    ):
        raise ValueError("selected accumulator discriminator census differs")
    return {
        "selectionBasis": (
            "the independently measured p27-nearest setup lattice; the candidate "
            "also improves both target-disjoint halves over the unquantized sum"
        ),
        "selectedMode": "x-plus-y-p27-nearest",
        "nonconstantCoefficientCount": nonconstant_count,
        "split": "target indices 0--3 discovery; 4--7 holdout",
        "candidateResults": candidates,
        "powerVRTileRelativePStartReference": (
            "https://patents.google.com/patent/US20020130863A1/en"
        ),
        "conclusion": (
            "AGX combines the already factorized X and Y tile terms on a "
            "27-bit-nearest lattice before adding the anchor. Splitting half of "
            "pStart into each axis, as documented for a PowerVR-style evaluator, "
            "does not describe the exported AGX setup coefficient."
        ),
    }


def _constant_controls(plan: dict[str, object], words: CaptureWords) -> JsonObject:
    patterns = _require_list(plan.get("patterns"), "plan patterns")
    comparison_count = 0
    for draw_value in _require_list(plan.get("draws"), "plan draws"):
        draw = _require_dict(draw_value, "plan draw")
        pattern = _require_dict(
            patterns[_require_int(draw.get("patternIndex"), "pattern index")],
            "pattern",
        )
        if pattern.get("kind") != "constant-control":
            continue
        constants = _require_list(pattern.get("constantBits"), "constant bits")
        triples = _triples(words[_require_int(draw.get("recordIndex"), "record index")])
        for component_index, triple in enumerate(triples):
            if triple[:2] != (1, 1):
                raise ValueError("constant-control slope sentinel differs")
            if triple[2] != int(
                _require_str(constants[component_index], "constant"), 16
            ):
                raise ValueError("constant-control tile constant differs")
            comparison_count += 1
    if comparison_count != 288:
        raise ValueError("constant-control census differs")
    return {
        "coefficientTripleCount": comparison_count,
        "slopeBits": ["0x00000001", "0x00000001"],
        "tileConstantEqualsSubmittedConstantCount": comparison_count,
        "conclusion": (
            "AGX serializes mathematically zero A/B slopes as the minimum positive "
            "binary32 subnormal while preserving every constant C word, including -0"
        ),
    }


def _basis_dot_rejection(
    plan: dict[str, object], words: CaptureWords, vertex_words: NDArray[np.uint32]
) -> JsonObject:
    baseline: dict[tuple[int, int], tuple[CoefficientTriple, ...]] = {}
    for draw_value in _require_list(plan.get("draws"), "plan draws"):
        draw = _require_dict(draw_value, "plan draw")
        if draw.get("patternIndex") == 0:
            key = (
                _require_int(draw.get("targetIndex"), "target index"),
                _require_int(draw.get("sampleOrdinal"), "sample ordinal"),
            )
            baseline[key] = _triples(
                words[_require_int(draw.get("recordIndex"), "record index")]
            )
    deltas: list[int] = []
    for draw_value in _require_list(plan.get("draws"), "plan draws"):
        draw = _require_dict(draw_value, "plan draw")
        record_index = _require_int(draw.get("recordIndex"), "record index")
        key = (
            _require_int(draw.get("targetIndex"), "target index"),
            _require_int(draw.get("sampleOrdinal"), "sample ordinal"),
        )
        basis = baseline[key]
        actual = _triples(words[record_index])
        for component_index in range(4):
            value = sum(
                (
                    export._fraction(
                        int(vertex_words[record_index, vertex, 4 + component_index])
                    )  # noqa: SLF001
                    * export._fraction(basis[vertex][2])  # noqa: SLF001
                    for vertex in range(3)
                ),
                start=Fraction(),
            )
            predicted = composite.quantize_composite_constant_bits(value)
            deltas.append(
                export._float_ulp_delta(  # noqa: SLF001
                    actual[component_index][2], predicted
                )
            )
    summary = _delta_summary(deltas)
    if (
        summary["count"] != 10_272
        or summary["exactCount"] != 8_399
        or summary["withinOneUlpCount"] != 10_022
    ):
        raise ValueError("basis-dot rejection census differs")
    return {
        "hypothesis": (
            "combine the three captured local-onehot C coefficients with exact "
            "vertex-value products, one p28-nearest composite, and binary32 rounding"
        ),
        **summary,
        "rejected": True,
        "conclusion": (
            "raw onehot planes are not combined by one ordinary affine/FMA dot; "
            "AGX setup depends on staged absolute and subtractive arithmetic"
        ),
    }


def analyze() -> JsonObject:
    verified = _verify_identities()
    inventory = _verify_inventory()
    plan, _samples = _load_plan()
    manifest, words = _load_capture(plan)
    record_census = _validate_records(plan, words)
    vertex_words = np.fromfile(VERTEX_PATH, dtype="<u4").reshape(-1, 3, 8)
    constants = _constant_controls(plan, words)
    factorized = _factorized_model(plan, words, vertex_words)
    accumulation = _accumulation_discriminator(plan, words, vertex_words)
    basis_dot = _basis_dot_rejection(plan, words, vertex_words)
    script = Path(__file__).resolve()
    return {
        "schemaVersion": 1,
        "classification": "output-blind AGX triangle-setup accumulator tomography",
        "authority": {
            "referencePixelsRead": False,
            "usesPublicRevealGeometryOnly": True,
            "captureMutatesProductionRenderer": False,
            "rawCoefficientTriplesEstablished": True,
            "exactAccumulatorLawRecovered": False,
            "productionIntegrationAuthorized": False,
        },
        "inputs": {
            "analyzer": _identity(script),
            "verifiedDependencies": verified,
            "captureInventory": inventory,
            "plan": _identity(PLAN_PATH),
            "vertexData": _identity(VERTEX_PATH),
            "captureManifest": _identity(CAPTURE_MANIFEST),
            "captureRaw": _identity(CAPTURE_RAW),
            "captureRuntime": manifest,
        },
        "captureAuthentication": {
            "coefficientPatch": {
                "matchingShaderCount": 1,
                "patchedShaderOffset": "0x28c0",
                "appliedCount": 1,
            },
            "publicVertexDataReconstructedExactly": True,
            "exactInventoryFileSet": True,
            **record_census,
        },
        "constantControls": constants,
        "factorizedAccumulatorModel": factorized,
        "accumulationOrderDiscriminator": accumulation,
        "capturedBasisDotHypothesis": basis_dot,
        "conclusion": (
            "The capture directly establishes 10,272 finite AGX coefficient triples. "
            "Constant varyings preserve C bit-for-bit but use positive-min-subnormal "
            "A/B sentinels. Combining the factorized X and Y tile terms at p27 "
            "nearest before adding the anchor transfers across target-disjoint "
            "halves and predicts 9,494/9,984 nonconstant C words exactly, with "
            "9,882 within one ULP. Combining captured onehot planes through one "
            "ordinary affine dot is decisively worse. The remaining target is the "
            "upstream tile-product materialization and multi-plane generated-edge "
            "lineage, not fragment evaluation or a missing barycentric identity."
        ),
        "nextExperiment": (
            "Discriminate the residual tile-product partial-product and carry order "
            "with paired sign/scale patterns, then carry the selected p27 X-plus-Y "
            "law through multi-plane generated-edge lineage before opening mask "
            "output."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    result = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["conclusion"], indent=2))


if __name__ == "__main__":
    main()
