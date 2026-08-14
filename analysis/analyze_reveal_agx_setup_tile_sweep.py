#!/usr/bin/env python3
"""Authenticate the wide-tile AGX setup capture and recover its C pipeline.

The three Metal captures contain the same public triangles and varying patterns
at nine widely separated coefficient tiles.  They expose rasterizer-generated
``(A, B, C)`` words directly and never read a rendered image.  This analyzer
authenticates the copied closure, reconstructs every submitted vertex record,
and discriminates whether C uses two independently reciprocated tile terms or
one shared reciprocal after their numerator-space sum.
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

import analyze_reveal_agx_setup_accumulator as accumulator  # noqa: E402
import generate_reveal_agx_setup_tile_sweep_plan as generator  # noqa: E402


type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
type JsonObject = dict[str, JsonValue]
type CaptureWords = NDArray[np.uint32]
type VertexWords = NDArray[np.uint32]
type Vertex = tuple[float, ...]

PLAN_ROOT: Final = ROOT / "build" / "analysis-agx-basis" / "setup-tile-sweep-plan-v1"
CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-setup-tile-sweep-v1"
)
PLAN_MANIFEST: Final = PLAN_ROOT / "manifest.json"
INVENTORY_PATH: Final = CAPTURE_ROOT / "inventory.sha256"
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "setup-tile-sweep-analysis"
    / "reveal-agx-setup-tile-sweep-result.json"
)

PLAN_HASHES: Final = (
    "43d3e8d2ebd47c5f161c4d5ccea783902b97e38d8b5f2d0ea4f57bd4cb14cea8",
    "d648bafd2332a9bdcc79b3d8c93d94cbadd6e2b7ce7219f958146be76e5b8cb0",
    "de54223b3007321810d9bb3d97a660461ceb05a76ce35c9ef0e8b9dc9ee6afaa",
)
CAPTURE_MANIFEST_HASHES: Final = (
    "0b500aefe7807f0ad105b387785551f6c99c18cc1e9019797c23a18fc7c41c4c",
    "c8595f4cdbea43fd83276604e4970b16e3ff8a8d10027bb93a85cb7e6282d04e",
    "eb858b85a37797b9ddd855ebe4bc4a5cc8e6b35643490c57ea92877530c681e7",
)
CAPTURE_RAW_HASHES: Final = (
    "7cd15a488494aeed625f603650e97b2a56f119cc972693a1b62007c13ad8b157",
    "f4464334b2e419e6066a3081d134eed5beb5536fb1a36c06bc81cbb50a17406b",
    "81f1e2492f8b4c1e4b3fc90140cccc13411552bd9d1a491b5c1e51a24930acab",
)
TRACE_HASHES: Final = (
    "2a6ee7a53eead873c9886267593f129a410188671afff4ab0f1198881e74587e",
    "6750000209656b7336e4cb3a9807207c1ae7e05202662ca45f6f526d93ca5e34",
    "4229f2897bb9ea7077208b247f4cbcbb69fd782777afcd8f07f5156f2f148f16",
)
VERTEX_SHA256: Final = (
    "a58c6ce54e7087f8957a841646fcd27fd50e9b58d4983c6a83e01aed4408adf4"
)
EXPECTED_IDENTITIES: Final = {
    PLAN_MANIFEST: "3105e3e210891f66aebf1d4d06835891d80c0075121ac83452081bb75b9978bd",
    INVENTORY_PATH: "c0d93e314d33cab45f059251541fb5c6209f8fc003a1804130e92730f5e1aec1",
    ANALYSIS / "generate_reveal_agx_setup_tile_sweep_plan.py": (
        "413af7147acba952be78ed497c134919431b976b1da7be7e52550e3eacaf309d"
    ),
    ANALYSIS / "analyze_reveal_agx_setup_accumulator.py": (
        "260f1d9530a16d4c6f50ed6b5030ded3be82b77282bde59249662dabda560099"
    ),
    ANALYSIS / "reveal_agx_setup_accumulator_probe.swift": (
        "c7b786b7a7144959dfb44447289360fcd16dd801ccc0f8d8aec995c8a6c04e42"
    ),
    ANALYSIS / "macos_agx_iokit_trace.c": (
        "27395c57e1e086724eeb402aa9cbda4fb9cfbed7ae82e8ccf97b32fc1d50b6f6"
    ),
    generator.CATALOG_DEFAULT: (
        "bc8b96dc4d3dc7c2fb6383dda49baa839eb207b60128739604ad8ddcd9402bd6"
    ),
    accumulator.setup.P25_PATH: accumulator.setup.P25_SHA256,
}

PLAN_SCHEMA: Final = "walle-reveal-agx-setup-accumulator-plan-v1"
CAPTURE_SCHEMA: Final = "walle-reveal-agx-setup-accumulator-capture-v1"
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


def _verify_hash(path: Path, expected: str) -> JsonObject:
    actual = _sha256(path)
    if actual != expected:
        raise ValueError(f"SHA-256 differs for {_relative(path)}: {actual}")
    return _identity(path)


def _verify_inventory() -> JsonObject:
    expected_paths: set[str] = set()
    entries: list[JsonObject] = []
    for number, line in enumerate(
        INVENTORY_PATH.read_text(encoding="utf-8").splitlines(), start=1
    ):
        try:
            expected, relative = line.split("  ", maxsplit=1)
        except ValueError as error:
            raise ValueError(f"invalid inventory line {number}") from error
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


def _expected_tiles() -> list[JsonObject]:
    _catalog, samples = accumulator.phase._load_catalog(  # noqa: SLF001
        generator.CATALOG_DEFAULT
    )
    sample_by_record = {sample.record_index: sample for sample in samples}
    result: list[JsonObject] = []
    for record_index in accumulator.generator.TARGET_RECORDS:
        target = sample_by_record[record_index]
        child = accumulator.phase._canonical_children(target)[  # noqa: SLF001
            target.child_ordinal_within_source
        ]
        positions = accumulator.setup._fixed_positions(child)  # noqa: SLF001
        selected = generator._select_wide_tiles(  # noqa: SLF001
            generator._interior_tiles(positions)  # noqa: SLF001
        )
        result.append(
            {
                "targetRecordIndex": record_index,
                "pixels": [list(pixel) for pixel in selected],
                "tiles": [list(generator._tile(pixel)) for pixel in selected],  # noqa: SLF001
            }
        )
    return result


def _load_plans() -> tuple[dict[str, object], ...]:
    manifest = _require_dict(
        json.loads(PLAN_MANIFEST.read_text(encoding="utf-8")), "plan manifest"
    )
    if (
        manifest.get("schema") != "walle-reveal-agx-setup-tile-sweep-plans-v1"
        or manifest.get("selectedTiles") != _expected_tiles()
        or manifest.get("targetCount") != 8
        or manifest.get("patternCount") != 107
        or manifest.get("selectedTileCountPerTarget") != 9
    ):
        raise ValueError("plan manifest differs from public reconstruction")

    patterns = accumulator.generator._patterns()  # noqa: SLF001
    expected_pattern_metadata = [
        {key: value for key, value in pattern.items() if key != "values"}
        for pattern in patterns
    ]
    plans: list[dict[str, object]] = []
    for batch in range(3):
        plan_path = (
            PLAN_ROOT / f"batch-{batch}" / "reveal-agx-setup-accumulator-plan.json"
        )
        vertex_path = (
            PLAN_ROOT / f"batch-{batch}" / "reveal-agx-setup-accumulator-vertices.bin"
        )
        _verify_hash(plan_path, PLAN_HASHES[batch])
        _verify_hash(vertex_path, VERTEX_SHA256)
        if vertex_path.read_bytes() != accumulator.VERTEX_PATH.read_bytes():
            raise ValueError(f"batch {batch} public vertex reconstruction differs")
        plan = _require_dict(
            json.loads(plan_path.read_text(encoding="utf-8")), f"batch {batch} plan"
        )
        census = _require_dict(plan.get("census"), f"batch {batch} census")
        sweep = _require_dict(plan.get("tileSweep"), f"batch {batch} sweep")
        if (
            plan.get("schema") != PLAN_SCHEMA
            or plan.get("patterns") != expected_pattern_metadata
            or census
            != {
                "coefficientTripleCount": 10_272,
                "drawCount": 2_568,
                "patternCount": 107,
                "targetCount": 8,
            }
            or sweep.get("batchIndex") != batch
            or sweep.get("batchCount") != 3
            or sweep.get("selectedTileCountPerTarget") != 9
        ):
            raise ValueError(f"batch {batch} plan structure differs")
        selected = _expected_tiles()
        targets = _require_list(plan.get("targets"), f"batch {batch} targets")
        for target_index, target_value in enumerate(targets):
            target = _require_dict(target_value, "target")
            if (
                target.get("targetRecordIndex")
                != selected[target_index]["targetRecordIndex"]
                or target.get("pixels") != selected[target_index]["pixels"][batch::3]
                or target.get("tiles") != selected[target_index]["tiles"][batch::3]
            ):
                raise ValueError(f"batch {batch} target tile selection differs")
        plans.append(plan)
    return tuple(plans)


def _load_captures(
    plans: tuple[dict[str, object], ...],
) -> tuple[CaptureWords, ...]:
    captures: list[CaptureWords] = []
    for batch, plan in enumerate(plans):
        directory = CAPTURE_ROOT / f"capture-{batch}"
        manifest_path = directory / "manifest.json"
        raw_path = directory / "reveal-agx-setup-accumulator.raw"
        trace_path = CAPTURE_ROOT / f"capture-{batch}.stderr"
        stdout_path = CAPTURE_ROOT / f"capture-{batch}.stdout"
        _verify_hash(manifest_path, CAPTURE_MANIFEST_HASHES[batch])
        _verify_hash(raw_path, CAPTURE_RAW_HASHES[batch])
        _verify_hash(trace_path, TRACE_HASHES[batch])
        if stdout_path.read_bytes():
            raise ValueError(f"batch {batch} stdout is nonempty")
        manifest = _require_dict(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            f"batch {batch} capture manifest",
        )
        capture_identity = _require_dict(
            manifest.get("capture"), f"batch {batch} capture identity"
        )
        plan_identity = _require_dict(
            manifest.get("plan"), f"batch {batch} plan identity"
        )
        vertex_identity = _require_dict(
            manifest.get("vertexData"), f"batch {batch} vertex identity"
        )
        executable = _require_dict(
            manifest.get("executable"), f"batch {batch} executable"
        )
        if (
            manifest.get("schema") != CAPTURE_SCHEMA
            or capture_identity.get("sha256") != CAPTURE_RAW_HASHES[batch]
            or capture_identity.get("bytes") != 4_149_888
            or capture_identity.get("recordCount") != 2_568
            or capture_identity.get("recordVectorCount") != RECORD_VECTOR_COUNT
            or plan_identity.get("sha256") != PLAN_HASHES[batch]
            or vertex_identity.get("sha256") != VERTEX_SHA256
            or executable.get("sha256")
            != "644f04ead8a07779c782030b2659e7aea88a031da142532fcbf69bfcfedde298"
        ):
            raise ValueError(f"batch {batch} capture closure differs")
        trace = trace_path.read_text(encoding="utf-8")
        if PATCHED_LINE.findall(trace) != [("1", "28c0")]:
            raise ValueError(f"batch {batch} patch target differs")
        if MATCH_LINE.findall(trace) != [("1", "1")]:
            raise ValueError(f"batch {batch} patch count differs")
        draw_count = len(_require_list(plan.get("draws"), "draws"))
        words = np.fromfile(raw_path, dtype="<u4")
        if words.size != draw_count * RECORD_WORD_COUNT:
            raise ValueError(f"batch {batch} capture shape differs")
        captures.append(words.reshape(draw_count, RECORD_VECTOR_COUNT, 4))
    return tuple(captures)


def _vertices(words: VertexWords, record_index: int) -> tuple[Vertex, ...]:
    return accumulator._vertices(words, record_index)  # noqa: SLF001


def _shared_reciprocal_constant_bits(
    vertices: tuple[Vertex, Vertex, Vertex],
    component: int,
    tile_position: tuple[int, int],
    bitmap: bytes,
    *,
    join_precision: int,
    reciprocal_truncation: int,
) -> int:
    positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
    determinant = accumulator.setup._determinant(positions)  # noqa: SLF001
    anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
    values = tuple(
        accumulator.setup._float32(vertex[2 + component])  # noqa: SLF001
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
    middle_terms: list[Fraction] = []
    for axis in range(2):
        numerator = sum(
            (
                accumulator.setup._first_product(  # noqa: SLF001
                    accumulator.setup._float32(values[index] - values[anchor]),  # noqa: SLF001
                    edges[axis][index] / 256.0,
                    bias_units=15,
                )
                for index in range(3)
                if index != anchor
            ),
            start=Fraction(),
        )
        sign, numerator_index, numerator_exponent = accumulator.setup._normalize_signed(  # noqa: SLF001
            numerator, precision_bits=27, rounding="nearest-even"
        )
        displacement = Fraction(
            tile_position[axis] * 32 * 256 - positions[anchor][axis], 256
        )
        if sign == 0 or displacement == 0:
            middle_terms.append(Fraction())
            continue
        distance_bits = accumulator.setup._float_bits(float(abs(displacement)))  # noqa: SLF001
        distance_index, distance_exponent = accumulator._positive_float_components(  # noqa: SLF001
            distance_bits
        )
        middle_index, middle_exponent = accumulator.coefficient.column_product_stage(
            numerator_index,
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
        term = middle_index * accumulator._power_of_two(middle_exponent)  # noqa: SLF001
        if sign * (-1 if displacement < 0 else 1) < 0:
            term = -term
        middle_terms.append(term)

    anchor_bits = accumulator.setup._float_bits(values[anchor])  # noqa: SLF001
    joined = sum(middle_terms, start=Fraction())
    if joined == 0:
        return anchor_bits
    sign, joined_index, joined_exponent = accumulator.setup._normalize_signed(  # noqa: SLF001
        joined, precision_bits=join_precision, rounding="nearest-even"
    )
    selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
        determinant, bitmap
    )
    coefficient_index, coefficient_exponent = accumulator.tile.product_stage(
        joined_index,
        joined_exponent,
        selector,
        selector_exponent,
        output_bits=27,
        truncation_bits=reciprocal_truncation,
        bias_units=20,
    )
    anchor_value = accumulator.export._fraction(anchor_bits)  # noqa: SLF001
    coefficient = (
        sign
        * coefficient_index
        * accumulator._power_of_two(  # noqa: SLF001
            coefficient_exponent
        )
    )
    return accumulator.composite.quantize_composite_constant_bits(
        anchor_value + coefficient
    )


def _summary(deltas: list[int]) -> JsonObject:
    histogram = Counter(deltas)
    return {
        "count": len(deltas),
        "exactCount": histogram[0],
        "withinOneUlpCount": sum(
            count for delta, count in histogram.items() if abs(delta) <= 1
        ),
        "minimumUlpDelta": min(deltas),
        "maximumUlpDelta": max(deltas),
        "smallDeltaHistogram": {
            str(delta): histogram[delta] for delta in range(-64, 65) if histogram[delta]
        },
    }


def _score_dense(
    plans: tuple[dict[str, object], ...], captures: tuple[CaptureWords, ...]
) -> JsonObject:
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    candidates = {
        "separate-reciprocals-p27-join": [],
        "shared-reciprocal-p27-trunc19": [],
        "shared-reciprocal-p28-trunc20": [],
        "shared-reciprocal-p29-trunc21": [],
        "shared-reciprocal-p30-trunc22": [],
    }
    by_term_count: dict[int, list[int]] = defaultdict(list)
    exported_slopes: dict[tuple[int, int, int], tuple[int, int]] = {}
    first_residuals: list[JsonObject] = []
    metadata_comparisons = 0
    for batch, (plan, words) in enumerate(zip(plans, captures, strict=True)):
        vertex_path = (
            PLAN_ROOT / f"batch-{batch}" / "reveal-agx-setup-accumulator-vertices.bin"
        )
        vertex_words = np.fromfile(vertex_path, dtype="<u4").reshape(-1, 3, 8)
        for draw_value in _require_list(plan.get("draws"), "draws"):
            draw = _require_dict(draw_value, "draw")
            record_index = _require_int(draw.get("recordIndex"), "record index")
            record = words[record_index]
            expected_metadata = (
                (
                    _require_int(draw.get("x"), "x"),
                    _require_int(draw.get("y"), "y"),
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
            for vector, expected in enumerate(expected_metadata):
                if tuple(int(value) for value in record[vector]) != expected:
                    raise ValueError(f"batch {batch} record {record_index} differs")
                metadata_comparisons += 4
            vertices = _vertices(vertex_words, record_index)
            triples = accumulator._triples(record)  # noqa: SLF001
            tile_position = (
                _require_int(draw.get("tileX"), "tile x"),
                _require_int(draw.get("tileY"), "tile y"),
            )
            for component, triple in enumerate(triples):
                group = (
                    _require_int(draw.get("targetIndex"), "target index"),
                    _require_int(draw.get("patternIndex"), "pattern index"),
                    component,
                )
                prior = exported_slopes.setdefault(group, triple[:2])
                if prior != triple[:2]:
                    raise ValueError("exported A/B changed across tile positions")
                anchor, x_term, y_term = accumulator._factorized_components(  # noqa: SLF001
                    vertices, component, tile_position, bitmap
                )
                separate = accumulator._accumulated_constant_bits(  # noqa: SLF001
                    anchor, x_term, y_term, "x-plus-y-p27-nearest"
                )
                candidates["separate-reciprocals-p27-join"].append(
                    accumulator.export._float_ulp_delta(triple[2], separate)  # noqa: SLF001
                )
                selected = 0
                for precision in range(27, 31):
                    name = f"shared-reciprocal-p{precision}-trunc{precision - 8}"
                    predicted = _shared_reciprocal_constant_bits(
                        vertices,
                        component,
                        tile_position,
                        bitmap,
                        join_precision=precision,
                        reciprocal_truncation=precision - 8,
                    )
                    delta = accumulator.export._float_ulp_delta(  # noqa: SLF001
                        triple[2], predicted
                    )
                    candidates[name].append(delta)
                    if precision == 28:
                        selected = delta
                        term_count = int(x_term != 0) + int(y_term != 0)
                        by_term_count[term_count].append(delta)
                        if delta and len(first_residuals) < 32:
                            first_residuals.append(
                                {
                                    "batch": batch,
                                    "targetIndex": group[0],
                                    "patternIndex": group[1],
                                    "component": component,
                                    "tile": list(tile_position),
                                    "actualBits": f"0x{triple[2]:08x}",
                                    "predictedBits": f"0x{predicted:08x}",
                                    "actualMinusPredictedFloatUlps": selected,
                                }
                            )

    summaries = {name: _summary(deltas) for name, deltas in candidates.items()}
    selected = _require_dict(
        summaries["shared-reciprocal-p28-trunc20"], "selected dense summary"
    )
    if (
        metadata_comparisons != 92_448
        or len(exported_slopes) != 3_424
        or (
            selected.get("count"),
            selected.get("exactCount"),
            selected.get("withinOneUlpCount"),
            selected.get("minimumUlpDelta"),
            selected.get("maximumUlpDelta"),
        )
        != (30_816, 30_792, 30_811, -32, 32)
    ):
        raise ValueError("dense selected-model census differs")
    term_summaries = {
        str(term_count): _summary(deltas)
        for term_count, deltas in sorted(by_term_count.items())
    }
    if (
        _require_dict(term_summaries["0"], "zero-term summary").get("exactCount") != 864
        or _require_dict(term_summaries["1"], "one-term summary").get("exactCount")
        != 7_722
        or (
            _require_dict(term_summaries["2"], "two-term summary").get("count"),
            _require_dict(term_summaries["2"], "two-term summary").get("exactCount"),
        )
        != (22_230, 22_206)
    ):
        raise ValueError("dense term-count census differs")
    return {
        "coefficientWordCount": 30_816,
        "metadataWordComparisonCount": metadata_comparisons,
        "tileInvariantExportedSlopeGroupCount": len(exported_slopes),
        "candidateResults": summaries,
        "selectedMode": "shared-reciprocal-p28-trunc20",
        "selectedByNonzeroTileTermCount": term_summaries,
        "firstSelectedResiduals": first_residuals,
    }


def _score_original_capture() -> JsonObject:
    plan = _require_dict(
        json.loads(accumulator.PLAN_PATH.read_text(encoding="utf-8")), "original plan"
    )
    words = np.fromfile(accumulator.CAPTURE_RAW, dtype="<u4").reshape(
        -1, RECORD_VECTOR_COUNT, 4
    )
    vertex_words = np.fromfile(accumulator.VERTEX_PATH, dtype="<u4").reshape(-1, 3, 8)
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    deltas: list[int] = []
    for draw_value in _require_list(plan.get("draws"), "original draws"):
        draw = _require_dict(draw_value, "original draw")
        record = _require_int(draw.get("recordIndex"), "record index")
        vertices = _vertices(vertex_words, record)
        tile_position = (
            _require_int(draw.get("tileX"), "tile x"),
            _require_int(draw.get("tileY"), "tile y"),
        )
        for component, triple in enumerate(accumulator._triples(words[record])):  # noqa: SLF001
            predicted = _shared_reciprocal_constant_bits(
                vertices,
                component,
                tile_position,
                bitmap,
                join_precision=28,
                reciprocal_truncation=20,
            )
            deltas.append(
                accumulator.export._float_ulp_delta(triple[2], predicted)  # noqa: SLF001
            )
    summary = _summary(deltas)
    if (
        summary.get("count"),
        summary.get("exactCount"),
        summary.get("withinOneUlpCount"),
        summary.get("minimumUlpDelta"),
        summary.get("maximumUlpDelta"),
    ) != (10_272, 10_260, 10_271, -2, 1):
        raise ValueError("original-capture transfer census differs")
    return summary


def analyze() -> JsonObject:
    verified = [
        _verify_hash(path, expected) for path, expected in EXPECTED_IDENTITIES.items()
    ]
    inventory = _verify_inventory()
    plans = _load_plans()
    captures = _load_captures(plans)
    dense = _score_dense(plans, captures)
    transfer = _score_original_capture()
    return {
        "schemaVersion": 1,
        "classification": "output-blind wide-tile AGX setup-accumulator tomography",
        "authority": {
            "referencePixelsRead": False,
            "usesPublicRevealGeometryOnly": True,
            "captureMutatesProductionRenderer": False,
            "rawCoefficientTriplesEstablished": True,
            "sharedReciprocalArchitectureEstablished": True,
            "exactAccumulatorLawRecovered": False,
            "productionIntegrationAuthorized": False,
        },
        "inputs": {
            "analyzer": _identity(Path(__file__).resolve()),
            "verifiedDependencies": verified,
            "planManifest": _identity(PLAN_MANIFEST),
            "captureInventory": inventory,
            "batchPlans": [
                _identity(
                    PLAN_ROOT
                    / f"batch-{batch}"
                    / "reveal-agx-setup-accumulator-plan.json"
                )
                for batch in range(3)
            ],
            "captureManifests": [
                _identity(CAPTURE_ROOT / f"capture-{batch}" / "manifest.json")
                for batch in range(3)
            ],
            "captureRaws": [
                _identity(
                    CAPTURE_ROOT
                    / f"capture-{batch}"
                    / "reveal-agx-setup-accumulator.raw"
                )
                for batch in range(3)
            ],
        },
        "captureAuthentication": {
            "batchCount": 3,
            "drawCount": 7_704,
            "coefficientTripleCount": 30_816,
            "selectedTileCountPerTarget": 9,
            "targetCount": 8,
            "patternCount": 107,
            "matchingShaderCountPerBatch": 1,
            "patchedShaderOffset": "0x28c0",
            "appliedPatchCountPerBatch": 1,
            "publicVertexDataReconstructedExactly": True,
            "exactInventoryFileSet": True,
        },
        "denseTileDiscriminator": dense,
        "originalThreeTileCaptureTransfer": transfer,
        "conclusion": (
            "AGX forms the signed X and Y displacement-weighted setup numerators, "
            "joins them on a p28 lattice, and applies one shared P25 reciprocal "
            "product with a 20-bit discarded partial-product boundary before "
            "adding the top-left anchor. This predicts 30,792/30,816 dense C words "
            "and 10,260/10,272 earlier C words exactly. Every zero-term and "
            "single-axis dense case is exact; the remaining 24 dense words all "
            "involve opposite-sign two-axis cancellation."
        ),
        "nextExperiment": (
            "Discriminate the remaining fused signed two-product carry/rounding "
            "inside the p28 join using cancellation-centered X/Y displacement "
            "pairs, then apply the recovered setup pipeline to the 88 visible "
            "multi-plane post-guard children."
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
