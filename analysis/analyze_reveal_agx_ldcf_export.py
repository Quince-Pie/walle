#!/usr/bin/env python3
"""Authenticate and decode the analysis-only AGX LDCF coefficient export.

The companion macOS interposer changes one authenticated standalone probe
shader so four existing result-store regions receive the raw ``LDCF``
coefficient triples instead of evaluated interpolation pulls.  This analyzer
joins that export to the unmodified phase capture and the public-input child
catalog.  It never opens a reference image.

The report deliberately separates two facts:

* the export directly establishes the binary32 triples consumed by the
  fragment shader; and
* an input-only rule that generates those triples is still a hypothesis.

The original pull capture is retained as an independent evaluator check.  Its
effective explicit-offset coordinates are the documented Metal offsets minus
one half pixel on each axis.  That correction is measured here rather than
silently folded into the coefficient data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "analysis")]

import analyze_reveal_agx_basis_phase as phase  # noqa: E402
import recover_reveal_postguard_plane_setup as recovery  # noqa: E402


type JsonObject = dict[str, object]
type CoefficientTriple = tuple[int, int, int]
type ExportRecord = NDArray[np.uint32]

CATALOG_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "reveal-agx-basis-catalog.json"
)
PHASE_CAPTURE_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-phase-capture"
)
EXPORT_ROOT_DEFAULT: Final = ROOT / "build" / "analysis-agx-basis" / "macos-ldcf-export"
ORIGINAL_COMMAND_ROOT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-phase-command" / "command-dump-v2b"
)
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "reveal-agx-ldcf-export-result.json"
)

EXPORT_STARTS: Final = (5, 21, 37, 53)
EXPORT_COMPONENT_NAMES: Final = ("basis0", "basis1", "basis2", "linear124")
PULL_LATTICES: Final = (
    ("x0", 5, lambda phase_index: (Fraction(phase_index, 16), Fraction(0))),
    ("y0", 21, lambda phase_index: (Fraction(0), Fraction(phase_index, 16))),
    (
        "x31",
        37,
        lambda phase_index: (Fraction(31 * 16 + phase_index, 16), Fraction(0)),
    ),
    (
        "y31",
        53,
        lambda phase_index: (Fraction(0), Fraction(31 * 16 + phase_index, 16)),
    ),
    (
        "xHalf",
        69,
        lambda phase_index: (Fraction(phase_index, 16), Fraction(1, 2)),
    ),
    (
        "yHalf",
        85,
        lambda phase_index: (Fraction(1, 2), Fraction(phase_index, 16)),
    ),
)

SHADER_SIGNATURE: Final = bytes(
    (
        0xA1,
        0xA9,
        0x02,
        0x40,
        0x00,
        0x40,
        0x10,
        0x00,
        0x61,
        0x95,
        0x03,
        0x80,
        0x00,
        0x40,
        0x00,
        0x00,
    )
)
SHADER_EDITS: Final = (
    (
        0xE2,
        bytes.fromhex("a1b9064000400000"),
        bytes.fromhex("a1f5064000400000"),
    ),
    (
        0xEA,
        bytes.fromhex("a1a9074000400000"),
        bytes.fromhex("a1e5074000400000"),
    ),
    (
        0xF2,
        bytes.fromhex("a185084000400001"),
        bytes.fromhex("a1d5084000400000"),
    ),
    (
        0xFA,
        bytes.fromhex("a195094000400000"),
        bytes.fromhex("a1c5094000400000"),
    ),
    (
        0x102,
        bytes.fromhex("fe259c629b00"),
        bytes.fromhex("00c0b8030000"),
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _hex(word: int) -> str:
    return f"0x{word:08x}"


def _fraction(word: int) -> Fraction:
    return recovery._fraction(word)  # noqa: SLF001


def _round(value: Fraction) -> int:
    return recovery._round_fraction(value)  # noqa: SLF001


def _ordered_key(word: int) -> int:
    return recovery._ordered_float_key(word)  # noqa: SLF001


def _nested_fma(x: Fraction, y: Fraction, triple: CoefficientTriple) -> int:
    slope_x, slope_y, constant = triple
    inner = _round(y * _fraction(slope_y) + _fraction(constant))
    return _round(x * _fraction(slope_x) + _fraction(inner))


def _triples(record: ExportRecord) -> tuple[CoefficientTriple, ...]:
    result: list[CoefficientTriple] = []
    for start in EXPORT_STARTS:
        expected = tuple(int(value) for value in record[start, :3])
        if any(
            tuple(int(value) for value in record[start + offset, :3]) != expected
            for offset in range(1, 16)
        ):
            raise ValueError(f"export triple at vector {start} is not repeated")
        if any(word & 0x7F80_0000 == 0x7F80_0000 for word in expected):
            raise ValueError(f"export triple at vector {start} is not finite")
        result.append(expected)  # type: ignore[arg-type]
    return tuple(result)


def _source_vertices(sample: phase.Sample) -> tuple[phase.Vertex, ...]:
    vertices: list[phase.Vertex] = []
    for vertex_index, words in enumerate(sample.source_vertices):
        vertices.append(
            (
                phase._float(words[0]),  # noqa: SLF001
                phase._float(words[1]),  # noqa: SLF001
                *(1.0 if component == vertex_index else 0.0 for component in range(3)),
                float(1 << vertex_index),
            )
        )
    return tuple(vertices)


def _float_ulp_delta(actual: int, expected: int) -> int:
    return _ordered_key(actual) - _ordered_key(expected)


def _delta_census(deltas: list[int]) -> JsonObject:
    histogram = Counter(deltas)
    return {
        "count": len(deltas),
        "exactCount": histogram[0],
        "withinOneUlpCount": sum(
            count for delta, count in histogram.items() if abs(delta) <= 1
        ),
        "withinTwoUlpsCount": sum(
            count for delta, count in histogram.items() if abs(delta) <= 2
        ),
        "withinFourUlpsCount": sum(
            count for delta, count in histogram.items() if abs(delta) <= 4
        ),
        "minimumUlpDelta": min(deltas),
        "maximumUlpDelta": max(deltas),
        "smallDeltaHistogram": {
            str(delta): histogram[delta] for delta in range(-8, 9) if histogram[delta]
        },
    }


def _inventory(directory: Path) -> JsonObject:
    entries: list[JsonObject] = []
    encoded = bytearray()
    total = 0
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        relative = path.relative_to(directory).as_posix()
        size = path.stat().st_size
        digest = _sha256(path)
        entries.append({"path": relative, "bytes": size, "sha256": digest})
        encoded.extend(f"{relative}\t{size}\t{digest}\n".encode())
        total += size
    return {
        "fileCount": len(entries),
        "totalBytes": total,
        "orderedInventorySha256": hashlib.sha256(encoded).hexdigest(),
        "entries": entries,
    }


def _find_shader_allocation(directory: Path) -> tuple[Path, int, bytes]:
    matches: list[tuple[Path, int, bytes]] = []
    for path in sorted(directory.glob("alloc-*.bin")):
        data = path.read_bytes()
        offset = data.find(SHADER_SIGNATURE)
        if offset < 0:
            continue
        if data.find(SHADER_SIGNATURE, offset + 1) >= 0:
            raise ValueError(f"shader signature is not unique in {path}")
        matches.append((path, offset, data))
    if len(matches) != 1:
        raise ValueError(f"expected one shader allocation, found {len(matches)}")
    return matches[0]


def _validate_shader_patch(
    original_directory: Path,
    patched_directory: Path,
) -> JsonObject:
    original_path, original_offset, original = _find_shader_allocation(
        original_directory
    )
    patched_path, patched_offset, patched = _find_shader_allocation(patched_directory)
    if original_offset != patched_offset:
        raise ValueError("original and patched shader offsets differ")
    for relative, before, after in SHADER_EDITS:
        start = original_offset + relative
        if original[start : start + len(before)] != before:
            raise ValueError(f"original shader edit preimage differs at {relative:#x}")
        if patched[start : start + len(after)] != after:
            raise ValueError(f"patched shader edit differs at {relative:#x}")
    return {
        "shaderOffset": original_offset,
        "signatureHex": SHADER_SIGNATURE.hex(),
        "originalAllocation": {
            "path": str(original_path),
            "bytes": len(original),
            "sha256": _sha256(original_path),
        },
        "patchedAllocation": {
            "path": str(patched_path),
            "bytes": len(patched),
            "sha256": _sha256(patched_path),
        },
        "edits": [
            {
                "shaderRelativeOffset": relative,
                "preimageHex": before.hex(),
                "replacementHex": after.hex(),
            }
            for relative, before, after in SHADER_EDITS
        ],
    }


def _pull_replay(
    original_words: NDArray[np.uint32],
    export_words: NDArray[np.uint32],
    *,
    subtract_half_pixel: bool,
) -> JsonObject:
    lattice_counts: dict[str, Counter[str]] = defaultdict(Counter)
    mismatch_deltas: list[int] = []
    first_mismatches: list[JsonObject] = []
    for record_index, (original_record, export_record) in enumerate(
        zip(original_words, export_words, strict=True)
    ):
        triples = _triples(export_record)
        for lattice_name, start, coordinates in PULL_LATTICES:
            for phase_index in range(16):
                x, y = coordinates(phase_index)
                if subtract_half_pixel:
                    x -= Fraction(1, 2)
                    y -= Fraction(1, 2)
                for component, triple in enumerate(triples):
                    predicted = _nested_fma(x, y, triple)
                    observed = int(original_record[start + phase_index, component])
                    exact = predicted == observed
                    lattice_counts[lattice_name]["total"] += 1
                    lattice_counts[lattice_name]["exact" if exact else "mismatch"] += 1
                    if exact:
                        continue
                    delta = _float_ulp_delta(observed, predicted)
                    mismatch_deltas.append(delta)
                    if len(first_mismatches) < 32:
                        first_mismatches.append(
                            {
                                "recordIndex": record_index,
                                "lattice": lattice_name,
                                "phase": phase_index,
                                "component": component,
                                "observedBits": _hex(observed),
                                "predictedBits": _hex(predicted),
                                "observedMinusPredictedFloatUlps": delta,
                            }
                        )
    total = sum(counts["total"] for counts in lattice_counts.values())
    exact = sum(counts["exact"] for counts in lattice_counts.values())
    return {
        "coordinateConvention": (
            "documented offset minus (0.5,0.5)"
            if subtract_half_pixel
            else "documented offset without correction"
        ),
        "totalWordCount": total,
        "exactWordCount": exact,
        "mismatchWordCount": total - exact,
        "perLattice": {
            name: dict(sorted(counts.items()))
            for name, counts in sorted(lattice_counts.items())
        },
        "mismatchUlpCensus": _delta_census(mismatch_deltas)
        if mismatch_deltas
        else None,
        "firstMismatches": first_mismatches,
    }


def _split_name(sample: phase.Sample) -> str:
    encoded = bytearray()
    for vertex in sample.source_vertices:
        encoded.extend(struct.pack(f"<{len(vertex)}I", *vertex))
    encoded.extend(struct.pack("<I", sample.child_ordinal_within_source))
    return "holdout" if hashlib.sha256(encoded).digest()[0] < 64 else "discovery"


def _coefficient_groups(
    samples: tuple[phase.Sample, ...],
    export_words: NDArray[np.uint32],
) -> tuple[list[JsonObject], JsonObject]:
    grouped: dict[
        tuple[int, int, int], list[tuple[phase.Sample, CoefficientTriple]]
    ] = defaultdict(list)
    for sample in samples:
        for component, triple in enumerate(_triples(export_words[sample.record_index])):
            grouped[(sample.case_index, sample.child_ordinal, component)].append(
                (sample, triple)
            )

    source_deltas: list[int] = []
    child_deltas: list[int] = []
    split_counts: Counter[str] = Counter()
    linear_relation_counts: Counter[str] = Counter()
    groups: list[JsonObject] = []
    child_split_seen: set[tuple[int, int]] = set()
    for (case_index, child_ordinal, component), entries in sorted(grouped.items()):
        first_sample = entries[0][0]
        slopes = {(triple[0], triple[1]) for _, triple in entries}
        if len(slopes) != 1:
            raise ValueError(
                f"LDCF slopes vary across tiles for case {case_index} child {child_ordinal}"
            )
        actual_x, actual_y = next(iter(slopes))
        source = _source_vertices(first_sample)
        canonical = phase._canonical_children(first_sample)[  # noqa: SLF001
            first_sample.child_ordinal_within_source
        ]
        source_x = phase._plane_slope_bits(  # noqa: SLF001
            source, axis=0, component=2 + component
        )
        source_y = phase._plane_slope_bits(  # noqa: SLF001
            source, axis=1, component=2 + component
        )
        child_x = phase._plane_slope_bits(  # noqa: SLF001
            canonical, axis=0, component=2 + component
        )
        child_y = phase._plane_slope_bits(  # noqa: SLF001
            canonical, axis=1, component=2 + component
        )
        current_source_deltas = [
            _float_ulp_delta(actual_x, source_x),
            _float_ulp_delta(actual_y, source_y),
        ]
        current_child_deltas = [
            _float_ulp_delta(actual_x, child_x),
            _float_ulp_delta(actual_y, child_y),
        ]
        source_deltas.extend(current_source_deltas)
        child_deltas.extend(current_child_deltas)
        split = _split_name(first_sample)
        child_key = (case_index, child_ordinal)
        if child_key not in child_split_seen:
            split_counts[split] += 1
            child_split_seen.add(child_key)
        groups.append(
            {
                "caseIndex": case_index,
                "state": first_sample.state,
                "sourcePrimitive": first_sample.source_primitive,
                "childOrdinal": child_ordinal,
                "childOrdinalWithinSource": first_sample.child_ordinal_within_source,
                "component": component,
                "componentName": EXPORT_COMPONENT_NAMES[component],
                "split": split,
                "recordIndices": [sample.record_index for sample, _ in entries],
                "tiles": [list(sample.tile) for sample, _ in entries],
                "slopeBits": [_hex(actual_x), _hex(actual_y)],
                "tileConstantBits": [_hex(triple[2]) for _, triple in entries],
                "sourceSlopeBits": [_hex(source_x), _hex(source_y)],
                "canonicalChildSlopeBits": [_hex(child_x), _hex(child_y)],
                "actualMinusSourceFloatUlps": current_source_deltas,
                "actualMinusCanonicalChildFloatUlps": current_child_deltas,
            }
        )

    by_child: dict[tuple[int, int], list[JsonObject]] = defaultdict(list)
    for group in groups:
        by_child[(int(group["caseIndex"]), int(group["childOrdinal"]))].append(group)
    for child_groups in by_child.values():
        child_groups.sort(key=lambda group: int(group["component"]))
        if len(child_groups) != 4:
            raise ValueError("child is missing an exported component")
        for axis in range(2):
            words = [int(str(group["slopeBits"][axis]), 16) for group in child_groups]  # type: ignore[index]
            predicted_linear = _round(
                _fraction(words[0]) + 2 * _fraction(words[1]) + 4 * _fraction(words[2])
            )
            actual_linear = words[3]
            linear_relation_counts[
                "exact" if actual_linear == predicted_linear else "mismatch"
            ] += 1

    return groups, {
        "childCount": len(child_split_seen),
        "componentGroupCount": len(groups),
        "axisCoefficientCount": len(child_deltas),
        "tileInvariantSlopeGroupCount": len(groups),
        "splitChildCounts": dict(sorted(split_counts.items())),
        "sourcePlaneComparison": _delta_census(source_deltas),
        "canonicalChildPlaneComparison": _delta_census(child_deltas),
        "linear124FromBasisSlopeRelation": dict(sorted(linear_relation_counts.items())),
    }


def analyze(
    catalog_path: Path,
    phase_capture_directory: Path,
    export_root: Path,
    original_command_root: Path,
) -> JsonObject:
    catalog, samples = phase._load_catalog(catalog_path)  # noqa: SLF001
    phase_manifest, phase_words, phase_raw = phase._load_capture(  # noqa: SLF001
        phase_capture_directory,
        catalog_path=catalog_path,
        record_count=len(samples),
    )
    export_directory = export_root / "capture-export"
    export_manifest, export_words, export_raw = phase._load_capture(  # noqa: SLF001
        export_directory,
        catalog_path=catalog_path,
        record_count=len(samples),
    )
    if not np.array_equal(phase_words[:, :5, :], export_words[:, :5, :]):
        raise ValueError("unmodified record metadata differs between captures")
    for sample in samples:
        expected_header = (*sample.pixel, 0, sample.case_index)
        expected_identity = (
            sample.record_index,
            sample.state,
            sample.source_primitive,
            sample.child_ordinal,
        )
        record = export_words[sample.record_index]
        if tuple(int(value) for value in record[0]) != expected_header:
            raise ValueError(f"record {sample.record_index} header differs")
        if tuple(int(value) for value in record[1]) != expected_identity:
            raise ValueError(f"record {sample.record_index} identity differs")
        _triples(record)

    trace_path = export_root / "capture.stderr"
    trace = trace_path.read_text(encoding="utf-8", errors="strict")
    patch_lines = re.findall(
        r"^AGX_IO coefficient export patched handle=(\d+) shader=0x([0-9a-f]+)$",
        trace,
        flags=re.MULTILINE,
    )
    match_lines = re.findall(
        r"^AGX_IO coefficient export matches=(\d+) applied=(\d+)$",
        trace,
        flags=re.MULTILINE,
    )
    if patch_lines != [("1", "28c0")] or match_lines != [("1", "1")]:
        raise ValueError("coefficient-export trace seal differs")

    groups, group_census = _coefficient_groups(samples, export_words)
    corrected_replay = _pull_replay(
        phase_words,
        export_words,
        subtract_half_pixel=True,
    )
    nominal_replay = _pull_replay(
        phase_words,
        export_words,
        subtract_half_pixel=False,
    )
    shader_patch = _validate_shader_patch(
        original_command_root,
        export_root / "command-dump",
    )
    script_path = Path(__file__).resolve()
    return {
        "schemaVersion": 1,
        "classification": "output-blind direct AGX LDCF coefficient export",
        "authority": {
            "referencePixelsRead": False,
            "usesPublicRevealGeometryOnly": True,
            "patchAppliesOnlyToAuthenticatedStandaloneProbe": True,
            "rawCoefficientTriplesEstablished": True,
            "coefficientsAreFiniteBinary32": True,
            "fragmentEvaluationOrder": "ffma(x, A, ffma(y, B, C))",
            "inputOnlyClipSetupLawRecovered": False,
            "productionIntegrationAuthorized": False,
        },
        "inputs": {
            "analyzer": {"path": str(script_path), "sha256": _sha256(script_path)},
            "catalog": {"path": str(catalog_path), "sha256": _sha256(catalog_path)},
            "phaseAnalyzerDependency": {
                "path": str(Path(phase.__file__).resolve()),
                "sha256": _sha256(Path(phase.__file__).resolve()),
                "scope": "catalog parsing and public canonical-child construction only",
            },
            "interposerSource": {
                "path": str(ROOT / "analysis" / "macos_agx_iokit_trace.c"),
                "sha256": _sha256(ROOT / "analysis" / "macos_agx_iokit_trace.c"),
            },
            "probeSource": {
                "path": str(ROOT / "analysis" / "reveal_agx_basis_phase_probe.swift"),
                "sha256": _sha256(
                    ROOT / "analysis" / "reveal_agx_basis_phase_probe.swift"
                ),
            },
            "unmodifiedCapture": {
                "directory": str(phase_capture_directory),
                "rawSha256": _sha256(phase_raw),
                "manifestSha256": _sha256(phase_capture_directory / "manifest.json"),
                "executableSha256": phase_manifest["executable"]["sha256"],  # type: ignore[index]
            },
            "coefficientExport": {
                "directory": str(export_root),
                "rawSha256": _sha256(export_raw),
                "manifestSha256": _sha256(export_directory / "manifest.json"),
                "traceSha256": _sha256(trace_path),
                "interposerDylibSha256": _sha256(
                    export_root / "libwalle-agx-ldcf-export.dylib"
                ),
                "executableSha256": export_manifest["executable"]["sha256"],  # type: ignore[index]
            },
            "commandDump": _inventory(export_root / "command-dump"),
        },
        "patchEvidence": shader_patch,
        "census": {
            "recordCount": len(samples),
            "exportedComponentCountPerRecord": 4,
            "coefficientWordCount": len(samples) * 4 * 3,
            "repeatedStoreWordComparisonCount": len(samples) * 4 * 15 * 3,
            "metadataWordComparisonCount": len(samples) * 5 * 4,
            **group_census,
        },
        "coordinateOriginDiscriminator": {
            "corrected": corrected_replay,
            "uncorrected": nominal_replay,
            "conclusion": (
                "Metal explicit offsets evaluate in the measured frame obtained by "
                "subtracting (0.5,0.5) from the probe's documented local offsets"
            ),
        },
        "catalogCensus": catalog["census"],
        "coefficientGroups": groups,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=CATALOG_DEFAULT)
    parser.add_argument("--phase-capture", type=Path, default=PHASE_CAPTURE_DEFAULT)
    parser.add_argument("--export-root", type=Path, default=EXPORT_ROOT_DEFAULT)
    parser.add_argument(
        "--original-command-root",
        type=Path,
        default=ORIGINAL_COMMAND_ROOT_DEFAULT,
    )
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    report = analyze(
        arguments.catalog,
        arguments.phase_capture,
        arguments.export_root,
        arguments.original_command_root,
    )
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(encoded)
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "records": report["census"]["recordCount"],  # type: ignore[index]
                "correctedPullMismatches": report["coordinateOriginDiscriminator"][  # type: ignore[index]
                    "corrected"
                ]["mismatchWordCount"],
                "uncorrectedPullMismatches": report["coordinateOriginDiscriminator"][  # type: ignore[index]
                    "uncorrected"
                ]["mismatchWordCount"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
