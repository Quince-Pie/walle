#!/usr/bin/env python3
"""Normalize exact parameters exposed by Apple's Liquid Glass layer tree."""

import argparse
import hashlib
import json
import platform
import re
import zipfile
from pathlib import Path
from typing import Any


type JsonObject = dict[str, Any]

MATRIX_TYPE = "{CAColorMatrix=ffffffffffffffffffff}"
POINTER = re.compile(r"0x[0-9a-fA-F]+")
FNV1A64 = re.compile(r"[0-9a-f]{16}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def bytes_fnv1a64(value: bytes) -> str:
    result = 0xCBF29CE484222325
    for byte in value:
        result ^= byte
        result = (result * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{result:016x}"


def read_runtime(path: Path) -> tuple[JsonObject, bytes]:
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError(
                    "runtime archive has duplicate members"
                )
            try:
                encoded = archive.read("runtime.json")
            except KeyError as error:
                raise ValueError(
                    "runtime archive has no runtime.json"
                ) from error
    else:
        encoded = path.read_bytes()
    return json.loads(encoded), encoded


def normalized(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): normalized(child)
            for key, child in sorted(value.items())
        }
    if isinstance(value, list):
        return [normalized(child) for child in value]
    if isinstance(value, str):
        return POINTER.sub("0xADDR", value)
    return value


def mirror_value(
    records: Any,
    label: str,
) -> Any:
    if not isinstance(records, list):
        raise ValueError("runtime mirror record is not a list")
    found = [
        child["value"]
        for record in records
        if isinstance(record, dict)
        for child in record.get("children", [])
        if (
            isinstance(child, dict)
            and child.get("label") == label
            and "value" in child
        )
    ]
    if len(found) != 1:
        raise ValueError(
            f"runtime mirror has {len(found)} {label!r} values"
        )
    return normalized(found[0])


def filter_inputs(root: Any, name: str) -> list[JsonObject]:
    found: list[JsonObject] = []
    if isinstance(root, dict):
        if root.get("description") == name:
            values = root.get("inputValues")
            if not isinstance(values, dict):
                raise ValueError(f"{name} has no input-value dictionary")
            found.append(values)
        for value in root.values():
            found.extend(filter_inputs(value, name))
    elif isinstance(root, list):
        for value in root:
            found.extend(filter_inputs(value, name))
    return found


def one_repeated_record(
    records: list[JsonObject],
    label: str,
) -> JsonObject:
    if not records:
        raise ValueError(f"runtime tree has no {label} record")
    canonical = {
        json.dumps(normalized(record), sort_keys=True)
        for record in records
    }
    if len(canonical) != 1:
        raise ValueError(f"runtime tree has conflicting {label} records")
    return normalized(records[0])


def exact_matrix(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("vibrant color matrix is not a typed value")
    floats = value.get("float32LittleEndian")
    words = value.get("uint32LittleEndianHex")
    if (
        value.get("objCType") != MATRIX_TYPE
        or value.get("lengthBytes") != 80
        or not isinstance(floats, list)
        or len(floats) != 20
        or not isinstance(words, list)
        or len(words) != 20
    ):
        raise ValueError("vibrant color matrix has an unexpected layout")
    rows = [floats[offset : offset + 5] for offset in range(0, 20, 5)]
    if rows[3] != [0, 0, 0, 1, 0]:
        raise ValueError("vibrant color matrix has no identity alpha row")
    return {
        "objCType": MATRIX_TYPE,
        "lengthBytes": 80,
        "hex": value.get("hex"),
        "uint32LittleEndianHex": words,
        "float32LittleEndian": floats,
        "rows4x5": rows,
    }


def exact_sdf_render_records(value: Any, label: str) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ValueError(f"{label} SDF render inventory is not a list")
    result: list[JsonObject] = []
    for index, untyped_record in enumerate(value):
        if not isinstance(untyped_record, dict):
            raise ValueError(f"{label} SDF render {index} is not a record")
        record = normalized(untyped_record)
        class_name = record.get("class")
        if (
            not isinstance(class_name, str)
            or "sdf" not in class_name.lower()
            or record.get("tree") != label
            or record.get("ordinal") != index
            or not isinstance(record.get("path"), list)
        ):
            raise ValueError(f"{label} SDF render {index} identity differs")
        if record.get("rendered") is False:
            if not isinstance(record.get("reason"), str):
                raise ValueError(
                    f"{label} SDF render {index} has no skip reason"
                )
            result.append(record)
            continue
        width = record.get("width")
        height = record.get("height")
        raw_bytes = record.get("rawBytes")
        if (
            record.get("rendered") is not True
            or not isinstance(width, int)
            or not isinstance(height, int)
            or width <= 0
            or height <= 0
            or raw_bytes != width * height * 4
            or record.get("bytesPerRow") != width * 4
            or record.get("pixelFormat")
            != "RGBA8 premultiplied-last sRGB"
            or not isinstance(record.get("rawFile"), str)
            or not isinstance(record.get("pngFile"), str)
            or not isinstance(record.get("pngBytes"), int)
            or not isinstance(record.get("fnv1a64"), str)
            or FNV1A64.fullmatch(record["fnv1a64"]) is None
        ):
            raise ValueError(f"{label} SDF render {index} layout differs")
        for field in (
            "channelMinima",
            "channelMaxima",
            "channelNonzeroCounts",
        ):
            values = record.get(field)
            if not isinstance(values, list) or len(values) != 4:
                raise ValueError(
                    f"{label} SDF render {index} {field} differs"
                )
        result.append(record)
    return result


def sdf_render_artifacts(
    runtime_path: Path,
    records: list[JsonObject],
) -> JsonObject:
    filenames: list[tuple[str, int | None, str | None]] = []
    for record in records:
        if record.get("rendered") is not True:
            continue
        filenames.extend(
            [
                (
                    record["rawFile"],
                    record["rawBytes"],
                    record["fnv1a64"],
                ),
                (
                    record["pngFile"],
                    record["pngBytes"],
                    None,
                ),
            ]
        )
    names = [name for name, _, _ in filenames]
    if len(names) != len(set(names)):
        raise ValueError("SDF render artifact names are not unique")

    payloads: dict[str, bytes] = {}
    if runtime_path.is_file() and zipfile.is_zipfile(runtime_path):
        with zipfile.ZipFile(runtime_path) as archive:
            archive_names = archive.namelist()
            if len(archive_names) != len(set(archive_names)):
                raise ValueError("runtime archive has duplicate members")
            for name in names:
                try:
                    payloads[name] = archive.read(name)
                except KeyError as error:
                    raise ValueError(
                        f"runtime archive has no {name}"
                    ) from error
    else:
        base = runtime_path.parent
        for name in names:
            payloads[name] = (base / name).read_bytes()

    result: JsonObject = {}
    for name, expected_bytes, expected_fnv in filenames:
        payload = payloads[name]
        if expected_bytes is not None and len(payload) != expected_bytes:
            raise ValueError(f"{name} byte count differs")
        fnv = bytes_fnv1a64(payload)
        if expected_fnv is not None and fnv != expected_fnv:
            raise ValueError(f"{name} FNV-1a checksum differs")
        result[name] = {
            "bytes": len(payload),
            "sha256": bytes_sha256(payload),
            "fnv1a64": fnv,
        }
    return result


def analyze(
    runtime_path: Path,
    *,
    artifact_path: Path | None = None,
) -> JsonObject:
    runtime, runtime_bytes = read_runtime(runtime_path)
    schema = runtime.get("schemaVersion")
    if schema not in {4, 10, 11}:
        raise ValueError(
            f"expected runtime schema 4, 10, or 11, got {schema!r}"
        )

    glass_records = filter_inputs(runtime, "glassBackground")
    vibrant_records = filter_inputs(runtime, "vibrantColorMatrix")
    glass = one_repeated_record(glass_records, "glassBackground")
    vibrant = one_repeated_record(
        vibrant_records,
        "vibrantColorMatrix",
    )
    matrix = exact_matrix(vibrant.get("inputColorMatrix"))

    objects = runtime.get("runtimeObjectValues")
    if not isinstance(objects, dict):
        raise ValueError("runtime object values are missing")
    backdrop = normalized(objects.get("CABackdropLayer"))
    sdf_layer = normalized(objects.get("CASDFLayer"))
    sdf_output = normalized(objects.get("CASDFOutputEffect"))
    sdf_highlight = normalized(
        objects.get("CASDFKeyFillHighlightEffect")
    )
    if not all(
        isinstance(value, dict)
        for value in (backdrop, sdf_layer, sdf_output, sdf_highlight)
    ):
        raise ValueError("required Liquid Glass runtime objects are missing")

    sdf_element = normalized(objects.get("CASDFElementLayer"))
    mirrors = runtime.get("sdfRuntimeMirrors")
    sdf_geometry: JsonObject | None = None
    sdf_renders: JsonObject | None = None
    sdf_render_files: JsonObject | None = None
    sdf_render_nonzero: bool | None = None
    if schema >= 10:
        if not isinstance(sdf_element, dict):
            raise ValueError("SDF element values are missing")
        expected_element = {
            "contentsOneValueDistance": 1,
            "contentsZeroValueDistance": 0,
            "gradientOvalization": 0,
            "hitTestsAsFill": True,
            "mode": "bounds",
            "name": None,
            "operation": "union",
        }
        if sdf_element != expected_element:
            raise ValueError("SDF element values differ")
        if not isinstance(mirrors, dict):
            raise ValueError("SDF runtime mirrors are missing")
        swift_sdf = mirrors.get("SwiftUI.SDFLayer")
        distance_range = mirror_value(
            swift_sdf,
            "distanceRange",
        )
        shape_bounds = mirror_value(
            swift_sdf,
            "shapeBounds",
        )
        ovalization = mirror_value(
            swift_sdf,
            "ovalization",
        )
        if (
            not isinstance(distance_range, dict)
            or not isinstance(shape_bounds, dict)
            or distance_range.get("description")
            != "Optional(ClosedRange(-400.0...8.0))"
            or shape_bounds.get("description")
            != (
                "(2.842170943040401e-14, "
                "2.842170943040401e-14, 800.0, 800.0)"
            )
            or ovalization != 0
        ):
            raise ValueError("live SDF geometry values differ")
        sdf_geometry = {
            "elementLayer": sdf_element,
            "distanceRange": distance_range,
            "shapeBounds": shape_bounds,
            "ovalization": ovalization,
        }
    if schema >= 11:
        model_renders = exact_sdf_render_records(
            runtime.get("modelSDFLayerRenders"),
            "model",
        )
        presentation_renders = exact_sdf_render_records(
            runtime.get("presentationSDFLayerRenders"),
            "presentation",
        )
        sdf_renders = {
            "model": model_renders,
            "presentation": presentation_renders,
        }
        sdf_render_files = sdf_render_artifacts(
            runtime_path,
            model_renders + presentation_renders,
        )
        rendered_records = [
            record
            for record in model_renders + presentation_renders
            if record.get("rendered") is True
        ]
        sdf_render_nonzero = any(
            any(record["channelNonzeroCounts"])
            for record in rendered_records
        )

    artifact_hash = (
        file_sha256(artifact_path)
        if artifact_path is not None
        else None
    )
    return {
        "liquidGlassRuntimeEvidenceSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_runtime_probe.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
        },
        "source": {
            "runtimePath": str(runtime_path),
            "runtimeSchemaVersion": schema,
            "runtimeBytes": len(runtime_bytes),
            "runtimeSha256": bytes_sha256(runtime_bytes),
            "sdfRenderArtifacts": sdf_render_files,
            "artifactPath": (
                str(artifact_path)
                if artifact_path is not None
                else None
            ),
            "artifactSha256": artifact_hash,
            "osVersion": runtime.get("osVersion"),
            "metalDevice": runtime.get("metalDevice"),
        },
        "capture": {
            "windowKey": runtime.get("windowKey"),
            "windowColorSpace": runtime.get("windowColorSpace"),
            "screenColorSpace": runtime.get("screenColorSpace"),
            "gpuTraceStarted": runtime.get("captureStarted"),
            "gpuTraceError": runtime.get("captureError"),
        },
        "pipeline": {
            "glassBackgroundCopies": len(glass_records),
            "glassBackgroundCopiesIdentical": True,
            "vibrantColorMatrixCopies": len(vibrant_records),
            "vibrantColorMatrixCopiesIdentical": True,
            "backdropLayer": backdrop,
            "sdfLayer": sdf_layer,
            "sdfOutputEffect": sdf_output,
            "sdfKeyFillHighlightEffect": sdf_highlight,
            "sdfGeometry": sdf_geometry,
            "sdfLayerRenders": sdf_renders,
        },
        "glassBackground": {
            "inputCount": len(glass),
            "inputValues": glass,
        },
        "vibrantColorMatrix": {
            "inputValues": vibrant,
            "matrix": matrix,
        },
        "conclusion": {
            "liveAppleMaterialObserved": True,
            "halfResolutionBackdropConfirmed": (
                backdrop.get("scale") == 0.5
            ),
            "matrixRecoveredBitExactly": True,
            "sdfParametersRecovered": True,
            "sdfGeometryRangeRecovered": sdf_geometry is not None,
            "directSdfLayerRasterExposed": sdf_render_nonzero,
            "directSdfLayerRendersAllZero": (
                not sdf_render_nonzero
                if sdf_render_nonzero is not None
                else None
            ),
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize exact parameters from a GlassIntrospect runtime report."
        )
    )
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    report = analyze(
        arguments.runtime,
        artifact_path=arguments.artifact,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
