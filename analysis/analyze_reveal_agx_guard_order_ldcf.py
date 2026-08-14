#!/usr/bin/env python3
"""Compare built-in AGX guard setup with all explicit clip-distance orders.

The 24 explicit-order probes use the same public reveal source triangles and
sample locations as the built-in viewport-guard probe.  A standalone shader
patch exports the four raw ``LDCF`` coefficient triples from every run.  This
analyzer authenticates those captures, compares coefficients before any
rendered output is opened, and classifies exact matches by clipped-polygon
topology.
"""

import argparse
import hashlib
import itertools
import json
import re
import sys
import tarfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Final

import numpy as np
from numpy.typing import NDArray


ROOT: Final = Path(__file__).resolve().parent.parent
ANALYSIS: Final = ROOT / "analysis"
sys.path.insert(0, str(ANALYSIS))

import analyze_reveal_agx_basis_phase as phase  # noqa: E402


type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
type JsonObject = dict[str, JsonValue]
type Words = NDArray[np.uint32]

CATALOG: Final = ROOT / "build" / "analysis-agx-basis" / "reveal-agx-basis-catalog.json"
BUILTIN_CAPTURE: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-ldcf-export" / "capture-export"
)
ORDER_ROOT: Final = ROOT / "build" / "analysis-agx-basis" / "direct-order-ldcf" / "full"
ORDER_ARCHIVE: Final = (
    ROOT / "build" / "analysis-agx-basis" / "direct-order-ldcf" / "orders.tar"
)
ORDER_PROVENANCE: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "direct-guard-equivalence-provenance"
    / "full-capture"
)
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "direct-order-ldcf-analysis"
    / "reveal-agx-guard-order-ldcf-result.json"
)

EXPECTED_ARCHIVE_SHA256: Final = (
    "f2eb77d59f02ba5c061aba9ef21361df0018be8849cae571fec0ccd6981b1ad6"
)
EXPECTED_ARCHIVE_BYTES: Final = 28_199_424
EXPORT_STARTS: Final = (5, 21, 37, 53)
ORDER_EXPRESSIONS: Final = (
    "position.x + 512.0f",
    "2560.0f - position.x",
    "position.y + 512.0f",
    "2560.0f - position.y",
)
EXPECTED_ORDERS: Final = tuple(
    "".join(str(value) for value in order) for order in itertools.permutations(range(4))
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
    return value


def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} is not a string")
    return value


def _require_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} is not an integer")
    return value


def _capture_coefficients(words: Words) -> Words:
    triples: list[Words] = []
    for start in EXPORT_STARTS:
        triple = words[:, start, :3]
        if not all(
            np.array_equal(words[:, start + offset, :3], triple)
            for offset in range(1, 16)
        ):
            raise ValueError(f"export triple at vector {start} is not repeated")
        if np.any((triple & np.uint32(0x7F80_0000)) == np.uint32(0x7F80_0000)):
            raise ValueError(f"export triple at vector {start} is not finite")
        triples.append(triple)
    return np.concatenate(triples, axis=1).astype("<u4", copy=False)


def _validate_record_metadata(
    words: Words,
    samples: tuple[phase.Sample, ...],
) -> None:
    for sample in samples:
        record = words[sample.record_index]
        if tuple(int(value) for value in record[0]) != (
            *sample.pixel,
            0,
            sample.case_index,
        ):
            raise ValueError(f"record {sample.record_index} header differs")
        if tuple(int(value) for value in record[1]) != (
            sample.record_index,
            sample.state,
            sample.source_primitive,
            sample.child_ordinal,
        ):
            raise ValueError(f"record {sample.record_index} identity differs")
        if tuple(int(value) for value in record[2]) != (
            sample.sample_ordinal,
            *sample.tile,
            0,
        ):
            raise ValueError(f"record {sample.record_index} sample metadata differs")


def _source_order(source: Path) -> tuple[int, int, int, int]:
    text = source.read_text(encoding="utf-8")
    assignments = re.findall(
        r"^\s*output\.clipDistance\[(\d)\] = ([^;]+);$",
        text,
        flags=re.MULTILINE,
    )
    if len(assignments) != 4 or [int(index) for index, _ in assignments] != list(
        range(4)
    ):
        raise ValueError(f"clip-distance assignments differ in {source}")
    expression_to_plane = {
        expression: plane for plane, expression in enumerate(ORDER_EXPRESSIONS)
    }
    try:
        return tuple(expression_to_plane[expression] for _, expression in assignments)  # type: ignore[return-value]
    except KeyError as error:
        raise ValueError(f"unknown clip-distance expression in {source}") from error


def _manifest_executable(manifest: dict[str, object]) -> tuple[int, str]:
    executable = _require_dict(manifest.get("executable"), "manifest executable")
    return (
        _require_int(executable.get("bytes"), "executable bytes"),
        _require_str(executable.get("sha256"), "executable SHA-256"),
    )


def _validate_trace(path: Path) -> JsonObject:
    text = path.read_text(encoding="utf-8", errors="strict")
    patches = re.findall(
        r"^AGX_IO coefficient export patched handle=(\d+) shader=0x([0-9a-f]+)$",
        text,
        flags=re.MULTILINE,
    )
    seals = re.findall(
        r"^AGX_IO coefficient export matches=(\d+) applied=(\d+)$",
        text,
        flags=re.MULTILINE,
    )
    if patches != [("1", "28c0")] or seals != [("1", "1")]:
        raise ValueError(f"coefficient-export trace seal differs in {path}")
    return {**_identity(path), "patchCount": 1, "matchCount": 1, "applyCount": 1}


def _validate_archive(archive: Path, extracted_root: Path) -> JsonObject:
    if archive.stat().st_size != EXPECTED_ARCHIVE_BYTES:
        raise ValueError("order archive byte count differs")
    if _sha256(archive) != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("order archive SHA-256 differs")

    expected_files = {
        path.relative_to(extracted_root).as_posix(): path
        for path in extracted_root.rglob("*")
        if path.is_file()
    }
    archived_files: dict[str, tuple[int, str]] = {}
    directory_count = 0
    member_names: set[str] = set()
    with tarfile.open(archive, mode="r:") as stream:
        for member in stream.getmembers():
            raw_name = member.name.removeprefix("./")
            name = "." if not raw_name else raw_name.rstrip("/")
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"unsafe archive member {member.name}")
            if name in member_names:
                raise ValueError(f"duplicate archive member {member.name}")
            member_names.add(name)
            if member.isdir():
                directory_count += 1
                continue
            if not member.isfile():
                raise ValueError(f"non-regular archive member {member.name}")
            extracted = stream.extractfile(member)
            if extracted is None:
                raise ValueError(f"cannot read archive member {member.name}")
            data = extracted.read()
            archived_files[name] = (len(data), hashlib.sha256(data).hexdigest())

    if set(archived_files) != set(expected_files):
        raise ValueError("archive and extracted regular-file sets differ")
    for name, path in expected_files.items():
        if archived_files[name] != (path.stat().st_size, _sha256(path)):
            raise ValueError(f"archive member differs from extracted file: {name}")
    return {
        **_identity(archive),
        "regularFileCount": len(archived_files),
        "directoryCount": directory_count,
        "unsafeMemberCount": 0,
        "duplicateMemberCount": 0,
        "payloadMismatchCount": 0,
    }


def _order_capture(
    order: str,
    samples: tuple[phase.Sample, ...],
    builtin_words: Words,
) -> tuple[JsonObject, Words]:
    source = ORDER_PROVENANCE / f"guard-order-{order}.swift"
    binary = ORDER_PROVENANCE / f"guard-order-{order}"
    compile_trace = ORDER_PROVENANCE / f"order-{order}.compile.stderr"
    original_capture = ORDER_PROVENANCE / f"capture-order-{order}"
    capture_root = ORDER_ROOT / f"order-{order}"
    capture = capture_root / "capture"

    if "".join(str(value) for value in _source_order(source)) != order:
        raise ValueError(f"source order differs for {order}")
    original_manifest, original_words, original_raw = phase._load_capture(  # noqa: SLF001
        original_capture,
        catalog_path=CATALOG,
        record_count=len(samples),
    )
    export_manifest, export_words, export_raw = phase._load_capture(  # noqa: SLF001
        capture,
        catalog_path=CATALOG,
        record_count=len(samples),
    )
    _validate_record_metadata(export_words, samples)
    if not np.array_equal(export_words[:, :3, :], original_words[:, :3, :]):
        raise ValueError(f"patched and unpatched metadata differ for {order}")
    if not np.array_equal(export_words[:, :3, :], builtin_words[:, :3, :]):
        raise ValueError(f"explicit and built-in metadata differ for {order}")

    original_executable = _manifest_executable(original_manifest)
    export_executable = _manifest_executable(export_manifest)
    if original_executable != export_executable:
        raise ValueError(f"patched capture executable identity differs for {order}")
    if original_executable != (binary.stat().st_size, _sha256(binary)):
        raise ValueError(f"provenance binary identity differs for {order}")
    stdout = capture_root / "stdout"
    if stdout.stat().st_size:
        raise ValueError(f"stdout is not empty for {order}")

    coefficients = _capture_coefficients(export_words)
    return (
        {
            "order": order,
            "source": _identity(source),
            "binary": _identity(binary),
            "compileTrace": _identity(compile_trace),
            "unmodifiedCapture": {
                "manifest": _identity(original_capture / "manifest.json"),
                "raw": _identity(original_raw),
            },
            "coefficientExportCapture": {
                "manifest": _identity(capture / "manifest.json"),
                "raw": _identity(export_raw),
                "trace": _validate_trace(capture_root / "stderr"),
                "stdout": _identity(stdout),
            },
            "coefficientArraySha256": hashlib.sha256(
                coefficients.tobytes()
            ).hexdigest(),
        },
        coefficients,
    )


def _comparison_census(matches: NDArray[np.bool_]) -> JsonObject:
    record_matches = np.all(matches, axis=1)
    triple_matches = np.all(matches.reshape(matches.shape[0], 4, 3), axis=2)
    return {
        "exactRecordCount": int(np.count_nonzero(record_matches)),
        "mismatchRecordCount": int(
            record_matches.size - np.count_nonzero(record_matches)
        ),
        "exactComponentTripleCount": int(np.count_nonzero(triple_matches)),
        "mismatchComponentTripleCount": int(
            triple_matches.size - np.count_nonzero(triple_matches)
        ),
        "exactCoefficientWordCount": int(np.count_nonzero(matches)),
        "mismatchCoefficientWordCount": int(matches.size - np.count_nonzero(matches)),
    }


def _sampled_child_analysis(
    catalog: JsonObject,
    samples: tuple[phase.Sample, ...],
    matches_by_order: dict[str, NDArray[np.bool_]],
) -> tuple[list[JsonObject], JsonObject]:
    cases_value = catalog.get("cases")
    if not isinstance(cases_value, list):
        raise ValueError("catalog cases are not an array")
    cases = [_require_dict(value, "catalog case") for value in cases_value]
    grouped: dict[tuple[int, int], list[phase.Sample]] = defaultdict(list)
    for sample in samples:
        grouped[(sample.case_index, sample.child_ordinal)].append(sample)

    rows: list[JsonObject] = []
    topology = Counter[tuple[int, str]]()
    no_match_states = Counter[int]()
    no_match_source_primitives = Counter[int]()
    no_match_child_ordinals = Counter[int]()
    exact_order_count_distribution = Counter[int]()
    for (case_index, child_ordinal), entries in sorted(grouped.items()):
        entries.sort(key=lambda sample: sample.record_index)
        record_indices = [sample.record_index for sample in entries]
        exact_orders = [
            order
            for order, matches in matches_by_order.items()
            if bool(np.all(matches[record_indices]))
        ]
        per_record_any = [
            any(bool(np.all(matches[index])) for matches in matches_by_order.values())
            for index in record_indices
        ]
        case = cases[case_index]
        children_value = case.get("children")
        if not isinstance(children_value, list):
            raise ValueError("catalog children are not an array")
        source_child_count = len(children_value)
        status = "exact-under-at-least-one-order" if exact_orders else "no-exact-order"
        topology[(source_child_count, status)] += 1
        exact_order_count_distribution[len(exact_orders)] += 1
        first = entries[0]
        if not exact_orders:
            no_match_states[first.state] += 1
            no_match_source_primitives[first.source_primitive] += 1
            no_match_child_ordinals[first.child_ordinal_within_source] += 1
            if any(per_record_any):
                raise ValueError("no-match child has an individually matched record")
        rows.append(
            {
                "caseIndex": case_index,
                "state": first.state,
                "sourcePrimitive": first.source_primitive,
                "sourceFanChildCount": source_child_count,
                "childOrdinal": child_ordinal,
                "childOrdinalWithinSource": first.child_ordinal_within_source,
                "recordIndices": record_indices,
                "exactOrders": exact_orders,
                "perRecordHasAnyExactOrder": per_record_any,
                "status": status,
            }
        )

    expected_topology = {
        (1, "exact-under-at-least-one-order"): 92,
        (2, "exact-under-at-least-one-order"): 40,
        (2, "no-exact-order"): 98,
    }
    if dict(topology) != expected_topology:
        raise ValueError(f"sampled-child topology census differs: {dict(topology)}")
    if any(
        row["sourceFanChildCount"] == 1 and row["status"] == "no-exact-order"
        for row in rows
    ):
        raise ValueError("single-child source has no exact explicit order")
    if any(
        row["sourceFanChildCount"] != 2 and row["status"] == "no-exact-order"
        for row in rows
    ):
        raise ValueError("no-match child does not belong to a two-child source")

    return rows, {
        "sampledChildCount": len(rows),
        "singleChildSourceSampledChildCount": 92,
        "singleChildSourceExactChildCount": 92,
        "twoChildSourceSampledChildCount": 138,
        "twoChildSourceExactChildCount": 40,
        "twoChildSourceNoExactOrderChildCount": 98,
        "noExactOrderRecordCount": 98 * 3,
        "noExactOrderRecordWithAnyIndividualMatchCount": 0,
        "exactOrderCountPerChildDistribution": {
            str(count): occurrences
            for count, occurrences in sorted(exact_order_count_distribution.items())
        },
        "noExactOrderPerState": {
            str(state): count for state, count in sorted(no_match_states.items())
        },
        "noExactOrderPerSourcePrimitive": {
            str(primitive): count
            for primitive, count in sorted(no_match_source_primitives.items())
        },
        "noExactOrderPerChildOrdinalWithinSource": {
            str(ordinal): count
            for ordinal, count in sorted(no_match_child_ordinals.items())
        },
        "conclusion": (
            "All 92 sampled coefficient planes from one-child clipped sources are "
            "reproduced exactly by at least one explicit clip-distance order. Of "
            "138 sampled coefficient planes from two-child fan sources, 40 are "
            "reproduced and 98 are not; none of the 294 records belonging to those "
            "98 children has an exact match under any of the 24 orders. The "
            "remaining built-in-versus-explicit difference is therefore confined "
            "to a subset of the quad/fan path, not generic endpoint mixing or all "
            "clipped-triangle setup."
        ),
    }


def analyze() -> JsonObject:
    catalog, samples = phase._load_catalog(CATALOG)  # noqa: SLF001
    builtin_manifest, builtin_words, builtin_raw = phase._load_capture(  # noqa: SLF001
        BUILTIN_CAPTURE,
        catalog_path=CATALOG,
        record_count=len(samples),
    )
    _validate_record_metadata(builtin_words, samples)
    builtin_coefficients = _capture_coefficients(builtin_words)

    archive = _validate_archive(ORDER_ARCHIVE, ORDER_ROOT)
    order_inputs: list[JsonObject] = []
    coefficients_by_order: dict[str, Words] = {}
    for order in EXPECTED_ORDERS:
        identity, coefficients = _order_capture(order, samples, builtin_words)
        order_inputs.append(identity)
        coefficients_by_order[order] = coefficients

    matches_by_order = {
        order: coefficients == builtin_coefficients
        for order, coefficients in coefficients_by_order.items()
    }
    order_results = []
    for identity in order_inputs:
        order = str(identity["order"])
        order_results.append(
            {**identity, "comparison": _comparison_census(matches_by_order[order])}
        )
    order_results.sort(
        key=lambda row: (
            -int(row["comparison"]["exactRecordCount"]),  # type: ignore[index]
            -int(row["comparison"]["exactComponentTripleCount"]),  # type: ignore[index]
            -int(row["comparison"]["exactCoefficientWordCount"]),  # type: ignore[index]
            str(row["order"]),
        )
    )

    record_any = np.any(
        np.stack([np.all(matches, axis=1) for matches in matches_by_order.values()]),
        axis=0,
    )
    triple_any = np.any(
        np.stack(
            [
                np.all(matches.reshape(len(samples), 4, 3), axis=2)
                for matches in matches_by_order.values()
            ]
        ),
        axis=0,
    )
    word_any = np.any(np.stack(list(matches_by_order.values())), axis=0)
    if (
        np.count_nonzero(record_any) != 396
        or np.count_nonzero(triple_any) != 2_068
        or np.count_nonzero(word_any) != 7_349
    ):
        raise ValueError("all-order union census differs")

    child_rows, child_census = _sampled_child_analysis(
        catalog,
        samples,
        matches_by_order,
    )
    unique_arrays = {
        hashlib.sha256(coefficients.tobytes()).hexdigest()
        for coefficients in coefficients_by_order.values()
    }
    if len(unique_arrays) != 14:
        raise ValueError("unique explicit coefficient-array count differs")

    script = Path(__file__).resolve()
    return {
        "schemaVersion": 1,
        "classification": (
            "output-blind built-in guard versus exhaustive explicit clip-order "
            "LDCF coefficient comparison"
        ),
        "authority": {
            "referencePixelsRead": False,
            "usesPublicRevealGeometryOnly": True,
            "rawCoefficientTriplesCompared": True,
            "allFourClipDistanceOrdersEnumerated": True,
            "singleChildMeasuredEquivalenceEstablished": True,
            "quadFanSubsetDifferenceEstablished": True,
            "fullBuiltInQuadSetupLawRecovered": False,
            "productionIntegrationAuthorized": False,
        },
        "inputs": {
            "analyzer": _identity(script),
            "catalog": _identity(CATALOG),
            "phaseAnalyzerDependency": _identity(Path(phase.__file__).resolve()),
            "builtInCoefficientExport": {
                "manifest": _identity(BUILTIN_CAPTURE / "manifest.json"),
                "raw": _identity(builtin_raw),
                "executableSha256": _manifest_executable(builtin_manifest)[1],
                "coefficientArraySha256": hashlib.sha256(
                    builtin_coefficients.tobytes()
                ).hexdigest(),
            },
            "explicitOrderArchive": archive,
            "explicitOrders": order_results,
        },
        "census": {
            "orderCount": len(EXPECTED_ORDERS),
            "uniqueExplicitCoefficientArrayCount": len(unique_arrays),
            "recordCount": len(samples),
            "componentTripleCount": len(samples) * 4,
            "coefficientWordCount": len(samples) * 12,
            "recordExactUnderAnyOrderCount": int(np.count_nonzero(record_any)),
            "recordNoExactOrderCount": int(
                record_any.size - np.count_nonzero(record_any)
            ),
            "componentTripleExactUnderAnyOrderCount": int(np.count_nonzero(triple_any)),
            "componentTripleNoExactOrderCount": int(
                triple_any.size - np.count_nonzero(triple_any)
            ),
            "coefficientWordExactUnderAnyOrderCount": int(np.count_nonzero(word_any)),
            "coefficientWordNoExactOrderCount": int(
                word_any.size - np.count_nonzero(word_any)
            ),
            **child_census,
        },
        "rankedOrderResults": [
            {
                "rank": rank,
                "order": row["order"],
                "coefficientArraySha256": row["coefficientArraySha256"],
                **row["comparison"],  # type: ignore[arg-type]
            }
            for rank, row in enumerate(order_results, start=1)
        ],
        "sampledChildren": child_rows,
        "nextExperiment": (
            "Discriminate the built-in two-child fan path directly: submit both "
            "diagonals and controlled polygon rotations with recovered endpoint "
            "basis values, export LDCF triples, and solve the 98 unmatched child "
            "planes without consulting rendered pixels."
        ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    report = analyze()
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(encoded)
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "bestOrder": report["rankedOrderResults"][0]["order"],  # type: ignore[index]
                "recordExactUnderAnyOrderCount": report["census"][  # type: ignore[index]
                    "recordExactUnderAnyOrderCount"
                ],
                "twoChildSourceNoExactOrderChildCount": report["census"][  # type: ignore[index]
                    "twoChildSourceNoExactOrderChildCount"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
