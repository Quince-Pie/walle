#!/usr/bin/env python3
"""Validate and measure live Apple glassBackground interventions."""

import argparse
import hashlib
import json
import platform
import resource
import time
import zipfile
from collections import defaultdict
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, BinaryIO, Self

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from liquid_glass_point_sweep import (
    SweepSet,
    center_patch_value,
)


type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

SCHEMA_VERSION = 3
RIG_VERSION = "filter-intervention-1.2.0"
IMAGE_SIDE = 1024
BLOCK_SIZE = 64
GRID_SIDE = 16
PATCH_RADIUS = 20
CUBE_CODES = np.asarray(
    (0, 36, 73, 109, 146, 182, 219, 255),
    dtype=np.int64,
)
PATTERN_NAMES = ("gray-256", "cube-8-p0", "cube-8-p1")
BASELINE_INPUTS: dict[str, bool | np.float32] = {
    "inputFaceColorMatrixBlack":
        np.float32(0.07500000298023224),
    "inputFaceColorMatrixSaturation":
        np.float32(1.059999942779541),
    "inputFaceColorMatrixWhite":
        np.float32(1.149999976158142),
    "inputFaceOpacity": np.float32(1),
    "inputClamp": np.float32(1.3758244514465332),
    "inputSDRHoldingToneEnabled": True,
    "inputSDRShadowOpacity": np.float32(0.23999999463558197),
    "inputSDRHoldingToneWhite":
        np.float32(0.9700000286102295),
}
INTERVENTIONS: tuple[
    tuple[str, dict[str, bool | np.float32]],
    ...,
] = (
    ("baseline", {}),
    (
        "face-saturation-1",
        {"inputFaceColorMatrixSaturation": np.float32(1)},
    ),
    (
        "face-saturation-0",
        {"inputFaceColorMatrixSaturation": np.float32(0)},
    ),
    (
        "face-black-0",
        {"inputFaceColorMatrixBlack": np.float32(0)},
    ),
    (
        "face-white-1",
        {"inputFaceColorMatrixWhite": np.float32(1)},
    ),
    (
        "holding-white-1",
        {"inputSDRHoldingToneWhite": np.float32(1)},
    ),
    (
        "holding-disabled",
        {"inputSDRHoldingToneEnabled": False},
    ),
    (
        "identity-face",
        {
            "inputFaceColorMatrixBlack": np.float32(0),
            "inputFaceColorMatrixWhite": np.float32(1),
            "inputFaceColorMatrixSaturation": np.float32(1),
            "inputSDRHoldingToneEnabled": False,
        },
    ),
    (
        "holding-only",
        {
            "inputFaceColorMatrixBlack": np.float32(0),
            "inputFaceColorMatrixWhite": np.float32(1),
            "inputFaceColorMatrixSaturation": np.float32(1),
        },
    ),
    (
        "affine-only",
        {
            "inputFaceColorMatrixSaturation": np.float32(1),
            "inputSDRHoldingToneEnabled": False,
        },
    ),
    (
        "saturation-only",
        {
            "inputFaceColorMatrixBlack": np.float32(0),
            "inputFaceColorMatrixWhite": np.float32(1),
            "inputSDRHoldingToneEnabled": False,
        },
    ),
    (
        "grayscale-only",
        {
            "inputFaceColorMatrixBlack": np.float32(0),
            "inputFaceColorMatrixWhite": np.float32(1),
            "inputFaceColorMatrixSaturation": np.float32(0),
            "inputSDRHoldingToneEnabled": False,
        },
    ),
    (
        "sdr-shadow-0",
        {"inputSDRShadowOpacity": np.float32(0)},
    ),
    (
        "clamp-1",
        {"inputClamp": np.float32(1)},
    ),
    (
        "face-opacity-0",
        {"inputFaceOpacity": np.float32(0)},
    ),
)


@dataclass(frozen=True, slots=True)
class StateSamples:
    inputs: IntArray
    outputs: IntArray


class InterventionSweep(
    AbstractContextManager["InterventionSweep"]
):
    def __init__(
        self,
        root: Path,
        archive: zipfile.ZipFile | None,
    ) -> None:
        self.root = root
        self.archive = archive
        with self.open_file("manifest.json") as stream:
            self.manifest = json.load(stream)

    @classmethod
    def open(cls, root: Path) -> Self:
        resolved = root.resolve()
        if resolved.is_file():
            return cls(resolved, zipfile.ZipFile(resolved))
        return cls(resolved, None)

    def open_file(self, name: str) -> BinaryIO:
        if self.archive is not None:
            return self.archive.open(name)
        return (self.root / name).open("rb")

    def file_bytes(self, name: str) -> bytes:
        with self.open_file(name) as stream:
            return stream.read()

    def image(self, name: str) -> NDArray[np.uint8]:
        with self.open_file(name) as stream:
            with Image.open(stream) as image:
                return np.asarray(
                    image.convert("RGBA"),
                    dtype=np.uint8,
                )

    def __exit__(self, *exc: object) -> None:
        if self.archive is not None:
            self.archive.close()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def bytes_sha256(value: bytes | memoryview) -> str:
    return hashlib.sha256(value).hexdigest()


def pattern_colors(name: str) -> IntArray:
    if name == "gray-256":
        codes = np.arange(256, dtype=np.int64)
        return np.repeat(codes[:, np.newaxis], 3, axis=1).reshape(
            GRID_SIDE,
            GRID_SIDE,
            3,
        )
    if name not in ("cube-8-p0", "cube-8-p1"):
        raise ValueError(f"unknown intervention pattern: {name}")
    offset = 0 if name.endswith("p0") else 256
    index = np.arange(offset, offset + 256, dtype=np.int64)
    return np.column_stack((
        CUBE_CODES[(index // 64) % 8],
        CUBE_CODES[(index // 8) % 8],
        CUBE_CODES[index % 8],
    )).reshape(GRID_SIDE, GRID_SIDE, 3)


def expanded_source(colors: IntArray) -> NDArray[np.uint8]:
    rgb = np.repeat(
        np.repeat(colors.astype(np.uint8), BLOCK_SIZE, axis=0),
        BLOCK_SIZE,
        axis=1,
    )
    alpha = np.full((*rgb.shape[:2], 1), 255, dtype=np.uint8)
    return np.concatenate((rgb, alpha), axis=-1)


def uniform_center_grid(image: NDArray[np.uint8]) -> IntArray:
    if image.shape != (IMAGE_SIDE, IMAGE_SIDE, 4):
        raise ValueError(
            f"filter intervention image shape differs: {image.shape}"
        )
    centers = (
        np.arange(GRID_SIDE, dtype=np.int64) * BLOCK_SIZE
        + BLOCK_SIZE // 2
    )
    center = image[
        centers[:, np.newaxis],
        centers[np.newaxis, :],
        :3,
    ]
    for delta_y in range(-PATCH_RADIUS, PATCH_RADIUS + 1):
        for delta_x in range(-PATCH_RADIUS, PATCH_RADIUS + 1):
            sample = image[
                (centers + delta_y)[:, np.newaxis],
                (centers + delta_x)[np.newaxis, :],
                :3,
            ]
            if not np.array_equal(sample, center):
                changed = np.any(sample != center, axis=-1)
                raise ValueError(
                    "filter intervention center patch is nonuniform "
                    f"at {np.count_nonzero(changed)} blocks"
                )
    return center.astype(np.int64)


def numeric_value_matches(
    actual: object,
    expected: bool | np.float32,
) -> bool:
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    if isinstance(actual, bool) or not isinstance(actual, int | float):
        return False
    return (
        np.asarray(np.float32(actual)).view(np.uint32).item()
        == np.asarray(expected).view(np.uint32).item()
    )


def validate_intervention_catalog(manifest: JsonObject) -> None:
    records = manifest.get("interventions")
    if not isinstance(records, list) or len(records) != len(
        INTERVENTIONS
    ):
        raise ValueError("filter intervention catalog differs")
    for record, (name, overrides) in zip(
        records,
        INTERVENTIONS,
        strict=True,
    ):
        if record.get("name") != name:
            raise ValueError("filter intervention order differs")
        actual = record.get("overrides")
        if not isinstance(actual, dict) or set(actual) != set(overrides):
            raise ValueError(f"{name} override keys differ")
        if not all(
            numeric_value_matches(actual[key], expected)
            for key, expected in overrides.items()
        ):
            raise ValueError(f"{name} override values differ")


def expected_state_inputs(
    state_name: str,
) -> dict[str, bool | np.float32]:
    overrides = dict(INTERVENTIONS)[state_name]
    return BASELINE_INPUTS | overrides


def validate_state_inputs(
    state_name: str,
    actual: object,
) -> None:
    expected = expected_state_inputs(state_name)
    if not isinstance(actual, dict) or set(actual) != set(expected):
        raise ValueError(f"{state_name} live filter keys differ")
    if not all(
        numeric_value_matches(actual[key], value)
        for key, value in expected.items()
    ):
        raise ValueError(f"{state_name} live filter values differ")


def difference_metrics(
    left: IntArray,
    right: IntArray,
) -> JsonObject:
    difference = np.abs(left - right)
    changed = np.any(difference != 0, axis=-1)
    return {
        "channelValues": int(difference.size),
        "changedInputColors": int(np.count_nonzero(changed)),
        "changedInputFraction": float(np.mean(changed)),
        "meanAbsoluteChannelDelta": float(difference.mean()),
        "maximumChannelDelta": int(difference.max(initial=0)),
    }


def unique_mapping(
    inputs: IntArray,
    outputs: IntArray,
) -> tuple[StateSamples, JsonObject]:
    mappings: defaultdict[
        tuple[int, int, int],
        set[tuple[int, int, int]],
    ] = defaultdict(set)
    for input_color, output_color in zip(
        inputs,
        outputs,
        strict=True,
    ):
        mappings[tuple(map(int, input_color))].add(
            tuple(map(int, output_color))
        )
    conflicts = {
        key: values
        for key, values in mappings.items()
        if len(values) != 1
    }
    ordered = sorted(mappings)
    samples = StateSamples(
        inputs=np.asarray(ordered, dtype=np.int64),
        outputs=np.asarray(
            [next(iter(mappings[key])) for key in ordered],
            dtype=np.int64,
        ),
    )
    return samples, {
        "observations": int(inputs.shape[0]),
        "distinctInputColors": len(ordered),
        "conflictingInputColors": len(conflicts),
        "maximumOutputsPerInput": max(
            map(len, mappings.values()),
            default=0,
        ),
    }


def collect_samples(
    sweep: InterventionSweep,
) -> tuple[
    dict[str, StateSamples],
    dict[str, IntArray],
    JsonObject,
]:
    manifest = sweep.manifest
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("filter intervention schema differs")
    if manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError("filter intervention rig differs")
    if manifest.get("windowKey") is not True:
        raise ValueError("filter intervention window was not key")
    if (
        manifest.get("pixelWidth") != IMAGE_SIDE
        or manifest.get("pixelHeight") != IMAGE_SIDE
        or manifest.get("blockSize") != BLOCK_SIZE
        or manifest.get("gridSide") != GRID_SIDE
        or manifest.get("uniformCenterPatchRadius") != PATCH_RADIUS
    ):
        raise ValueError("filter intervention geometry differs")
    validate_intervention_catalog(manifest)

    records = manifest.get("patterns")
    if (
        not isinstance(records, list)
        or tuple(record.get("name") for record in records)
        != PATTERN_NAMES
    ):
        raise ValueError("filter intervention pattern catalog differs")

    input_parts: list[IntArray] = []
    output_parts: dict[str, list[IntArray]] = {
        name: [] for name, _ in INTERVENTIONS
    }
    gray_outputs: dict[str, IntArray] = {}
    controls: list[JsonObject] = []
    expected_output_names = tuple(
        name for name, _ in INTERVENTIONS
    )

    for record in records:
        name = record["name"]
        expected_colors = pattern_colors(name)
        expected_cells = [
            {
                "index": index,
                "red": int(color[0]),
                "green": int(color[1]),
                "blue": int(color[2]),
            }
            for index, color in enumerate(
                expected_colors.reshape(-1, 3)
            )
        ]
        if record.get("cells") != expected_cells:
            raise ValueError(f"{name} cell catalog differs")

        source_bytes = sweep.file_bytes(record["sourceFile"])
        control_bytes = sweep.file_bytes(record["controlFile"])
        if bytes_sha256(source_bytes) != record["sourceFileSha256"]:
            raise ValueError(f"{name} source file hash differs")
        if bytes_sha256(control_bytes) != record["controlFileSha256"]:
            raise ValueError(f"{name} control file hash differs")
        source = sweep.image(record["sourceFile"])
        control = sweep.image(record["controlFile"])
        if not np.array_equal(source, expanded_source(expected_colors)):
            raise ValueError(f"{name} source regeneration differs")
        if bytes_sha256(memoryview(control)) != record[
            "controlPixelSha256"
        ]:
            raise ValueError(f"{name} control pixel hash differs")
        if record.get("controlStabilitySamples", 0) < 2:
            raise ValueError(f"{name} control lacks stability")
        if not np.all(control[..., 3] == 255):
            raise ValueError(f"{name} control contains alpha")
        input_centers = uniform_center_grid(control).reshape(-1, 3)
        input_parts.append(input_centers)
        controls.append({
            "pattern": name,
            **difference_metrics(
                expected_colors.reshape(-1, 3),
                input_centers,
            ),
        })

        outputs = record.get("outputs")
        if (
            not isinstance(outputs, list)
            or tuple(output.get("name") for output in outputs)
            != expected_output_names
        ):
            raise ValueError(f"{name} output catalog differs")
        for output_record in outputs:
            state_name = output_record["name"]
            validate_state_inputs(
                state_name,
                output_record.get("filterInputs"),
            )
            capture_bytes = sweep.file_bytes(output_record["file"])
            if bytes_sha256(capture_bytes) != output_record[
                "fileSha256"
            ]:
                raise ValueError(
                    f"{name}/{state_name} file hash differs"
                )
            capture = sweep.image(output_record["file"])
            if bytes_sha256(memoryview(capture)) != output_record[
                "pixelSha256"
            ]:
                raise ValueError(
                    f"{name}/{state_name} pixel hash differs"
                )
            if output_record.get("stabilitySamples", 0) < 2:
                raise ValueError(
                    f"{name}/{state_name} lacks stability"
                )
            if not np.all(capture[..., 3] == 255):
                raise ValueError(
                    f"{name}/{state_name} contains alpha"
                )
            centers = uniform_center_grid(capture).reshape(-1, 3)
            output_parts[state_name].append(centers)
            if name == "gray-256":
                gray_outputs[state_name] = centers

    inputs = np.concatenate(input_parts)
    states: dict[str, StateSamples] = {}
    mapping_reports: JsonObject = {}
    for state_name, _ in INTERVENTIONS:
        samples, mapping = unique_mapping(
            inputs,
            np.concatenate(output_parts[state_name]),
        )
        if mapping["conflictingInputColors"]:
            raise ValueError(
                f"{state_name} has conflicting repeated inputs"
            )
        states[state_name] = samples
        mapping_reports[state_name] = mapping

    maximum_control_delta = max(
        record["maximumChannelDelta"] for record in controls
    )
    if maximum_control_delta > 1:
        raise ValueError(
            "filter intervention control exceeds one source code"
        )
    return states, gray_outputs, {
        "maximumChannelDelta": maximum_control_delta,
        "patterns": controls,
        "stateMappings": mapping_reports,
    }


def point_mapping(path: Path) -> StateSamples:
    observations: defaultdict[
        tuple[int, int, int],
        set[tuple[int, int, int]],
    ] = defaultdict(set)
    with SweepSet.open(path) as sweep:
        manifest = sweep.manifest
        block_size = manifest["blockSize"]
        grid_side = manifest["gridSide"]
        for record in manifest["patterns"]:
            control = sweep.image(record["controlFile"])
            clear = sweep.image(record["clearFile"])
            for row in range(grid_side):
                for column in range(grid_side):
                    source = center_patch_value(
                        control,
                        row,
                        column,
                        block_size,
                    )
                    output = center_patch_value(
                        clear,
                        row,
                        column,
                        block_size,
                    )
                    observations[source].add(output)
    if any(len(outputs) != 1 for outputs in observations.values()):
        raise ValueError("trusted point sweep contains conflicts")
    ordered = sorted(observations)
    return StateSamples(
        inputs=np.asarray(ordered, dtype=np.int64),
        outputs=np.asarray(
            [next(iter(observations[key])) for key in ordered],
            dtype=np.int64,
        ),
    )


def compare_mappings(
    left: StateSamples,
    right: StateSamples,
) -> JsonObject:
    left_values = {
        tuple(map(int, key)): value
        for key, value in zip(
            left.inputs,
            left.outputs,
            strict=True,
        )
    }
    right_values = {
        tuple(map(int, key)): value
        for key, value in zip(
            right.inputs,
            right.outputs,
            strict=True,
        )
    }
    shared = sorted(set(left_values) & set(right_values))
    left_output = np.asarray(
        [left_values[key] for key in shared],
        dtype=np.int64,
    )
    right_output = np.asarray(
        [right_values[key] for key in shared],
        dtype=np.int64,
    )
    return {
        "sharedInputColors": len(shared),
        **difference_metrics(left_output, right_output),
    }


def analyze(
    sweep: InterventionSweep,
    trusted_point_sweep: Path | None,
) -> JsonObject:
    started = time.perf_counter()
    states, gray_outputs, controls = collect_samples(sweep)
    baseline = states["baseline"]
    state_differences = {
        name: compare_mappings(baseline, samples)
        for name, samples in states.items()
        if name != "baseline"
    }
    state_input_round_trips = {
        name: difference_metrics(samples.inputs, samples.outputs)
        for name, samples in states.items()
    }
    holding_equivalence = compare_mappings(
        states["holding-disabled"],
        states["holding-white-1"],
    )
    trusted_comparison = None
    if trusted_point_sweep is not None:
        trusted_comparison = compare_mappings(
            baseline,
            point_mapping(trusted_point_sweep),
        )
    elapsed = time.perf_counter() - started
    report: JsonObject = {
        "liquidGlassFilterInterventionAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_filter_interventions.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
            "pillow": package_version("pillow"),
        },
        "source": {
            "path": str(sweep.root),
            "sha256": (
                file_sha256(sweep.root)
                if sweep.root.is_file()
                else None
            ),
            "ciCommit": sweep.manifest.get("ciCommit"),
            "osVersion": sweep.manifest.get("osVersion"),
        },
        "sampleDesign": {
            "patterns": len(PATTERN_NAMES),
            "observationsPerState": 3 * GRID_SIDE * GRID_SIDE,
            "uniformCenterPatchSidePixels": 2 * PATCH_RADIUS + 1,
        },
        "sourceControlRoundTrip": controls,
        "interventionDifferencesFromBaseline": state_differences,
        "interventionRoundTripsToInput": state_input_round_trips,
        "crossInterventionEquivalences": {
            "holdingDisabledVsHoldingWhiteOne":
                holding_equivalence,
        },
        "grayTransferByState": {
            name: values.tolist()
            for name, values in gray_outputs.items()
        },
        "resourceMeasurements": {
            "analysisSeconds": elapsed,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "uniformCentersVerified": True,
            "stateMappingsDeterministicAcrossRepeatedInputs": True,
            "identityFaceMatchesInputExactly":
                state_input_round_trips["identity-face"][
                    "changedInputColors"
                ] == 0,
            "holdingDisabledMatchesHoldingWhiteOneExactly":
                holding_equivalence["changedInputColors"] == 0,
            "productionShaderAuthorized": False,
        },
    }
    if trusted_comparison is not None:
        report["trustedPointSweepComparison"] = {
            "path": str(trusted_point_sweep),
            "sha256": file_sha256(trusted_point_sweep),
            **trusted_comparison,
        }
        report["conclusion"][
            "baselineMatchesTrustedPointSweepExactly"
        ] = trusted_comparison["changedInputColors"] == 0
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and analyze live glassBackground interventions."
        )
    )
    parser.add_argument("sweep", type=Path)
    parser.add_argument("--trusted-point-sweep", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    with InterventionSweep.open(arguments.sweep) as sweep:
        report = analyze(sweep, arguments.trusted_point_sweep)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
