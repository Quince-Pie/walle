#!/usr/bin/env python3
"""Validate and analyze compact exhaustive Liquid Glass point sweeps."""

import argparse
import hashlib
import json
import platform
import zipfile
from collections import Counter, defaultdict
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, BinaryIO, Self

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from liquid_glass_clear_point_stage import (
    categorical_additive_design,
    feature_matrix,
    minimum_interval_fit,
    prediction_metrics,
)


type BoolArray = NDArray[np.bool_]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

SCHEMA_VERSION = 2
RIG_VERSION = "point-sweep-1.1.0"
CENTER = 32
CENTER_RADIUS = 20


@dataclass(frozen=True, slots=True)
class SweepSamples:
    inputs: IntArray
    outputs: IntArray
    occurrences: IntArray
    observations: int
    conflicting_inputs: int
    maximum_outputs_per_input: int


class SweepSet(AbstractContextManager["SweepSet"]):
    def __init__(self, root: Path, archive: zipfile.ZipFile | None) -> None:
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

    def image(self, name: str) -> NDArray[np.uint8]:
        with self.open_file(name) as stream:
            with Image.open(stream) as image:
                return np.asarray(image.convert("RGBA"), dtype=np.uint8)

    def file_bytes(self, name: str) -> bytes:
        with self.open_file(name) as stream:
            return stream.read()

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


def difference_report(left: IntArray, right: IntArray) -> JsonObject:
    if left.shape != right.shape:
        raise ValueError("point-sweep images have different shapes")
    difference = np.abs(left - right)
    changed = np.any(difference[..., :3] != 0, axis=-1)
    return {
        "pixels": int(changed.size),
        "changedPixels": int(np.count_nonzero(changed)),
        "changedPixelFraction": float(np.mean(changed)),
        "maximumChannelDelta": int(difference[..., :3].max(initial=0)),
        "meanAbsoluteChannelDelta": float(
            difference[..., :3].mean()
        ),
    }


def center_patch_value(
    image: NDArray[np.uint8],
    row: int,
    column: int,
    block_size: int,
) -> tuple[int, int, int]:
    center_y = row * block_size + CENTER
    center_x = column * block_size + CENTER
    patch = image[
        center_y - CENTER_RADIUS : center_y + CENTER_RADIUS + 1,
        center_x - CENTER_RADIUS : center_x + CENTER_RADIUS + 1,
        :3,
    ]
    values = np.unique(patch.reshape(-1, 3), axis=0)
    if values.shape != (1, 3):
        raise ValueError(
            f"block ({row}, {column}) has a nonuniform center patch"
        )
    return tuple(int(value) for value in values[0])


def collect_samples(
    observations: list[
        tuple[tuple[int, int, int], tuple[int, int, int]]
    ],
) -> SweepSamples:
    mappings: defaultdict[
        tuple[int, int, int],
        set[tuple[int, int, int]],
    ] = defaultdict(set)
    counts: Counter[tuple[int, int, int]] = Counter()
    for source, output in observations:
        mappings[source].add(output)
        counts[source] += 1
    ordered = sorted(mappings)
    return SweepSamples(
        inputs=np.asarray(ordered, dtype=np.int64),
        outputs=np.asarray(
            [min(mappings[source]) for source in ordered],
            dtype=np.int64,
        ),
        occurrences=np.asarray(
            [counts[source] for source in ordered],
            dtype=np.int64,
        ),
        observations=len(observations),
        conflicting_inputs=sum(
            len(outputs) != 1 for outputs in mappings.values()
        ),
        maximum_outputs_per_input=max(
            map(len, mappings.values()),
            default=0,
        ),
    )


def categorical_report(samples: SweepSamples) -> JsonObject:
    design = categorical_additive_design(samples.inputs)
    interval_slacks = {
        quantizer: [
            minimum_interval_fit(
                design.features,
                samples.outputs[:, channel],
                quantizer,
            ).minimum_extra_half_width
            for channel in range(3)
        ]
        for quantizer in ("floor", "nearest")
    }
    coefficients = np.linalg.lstsq(
        design.features,
        samples.outputs.astype(np.float64),
        rcond=None,
    )[0]
    predicted = np.clip(
        np.rint(design.features @ coefficients),
        0,
        255,
    ).astype(np.int64)
    ranks: JsonObject = {}
    for channel in range(3):
        selected = np.asarray(
            [
                input_channel == channel
                for input_channel, _ in design.labels
            ],
            dtype=np.bool_,
        )
        contributions = coefficients[:-1][selected]
        _, singular_values, right = np.linalg.svd(
            contributions,
            full_matrices=False,
        )
        direction = right[0]
        if direction[channel] < 0:
            direction = -direction
        direction = direction / direction[channel]
        energy = np.square(singular_values)
        ranks[str(channel)] = {
            "inputCodes": int(contributions.shape[0]),
            "singularValues": singular_values.tolist(),
            "rankOneEnergyFraction": float(energy[0] / energy.sum()),
            "outputDirectionNormalizedToOwnChannel":
                direction.tolist(),
        }
    return {
        "features": int(design.features.shape[1]),
        "matrixRank": int(np.linalg.matrix_rank(design.features)),
        "intervalMinimumExtraHalfWidthCodesByChannel":
            interval_slacks,
        "allChannelsIntervalFeasible": {
            quantizer: all(slack <= 1e-9 for slack in slacks)
            for quantizer, slacks in interval_slacks.items()
        },
        "leastSquaresNearestMetrics": prediction_metrics(
            predicted,
            samples.outputs,
        ),
        "inputChannelContributionRank": ranks,
    }


def polynomial_report(samples: SweepSamples) -> JsonObject:
    families = (
        "linear",
        "diagonal-quadratic",
        "full-quadratic",
        "full-cubic",
    )
    return {
        family: {
            quantizer: {
                "minimumExtraHalfWidthCodesByChannel": [
                    minimum_interval_fit(
                        feature_matrix(samples.inputs, family),
                        samples.outputs[:, channel],
                        quantizer,
                    ).minimum_extra_half_width
                    for channel in range(3)
                ]
            }
            for quantizer in ("floor", "nearest")
        }
        for family in families
    }


def runtime_gray_report(
    samples: SweepSamples,
    runtime_evidence: JsonObject,
) -> JsonObject:
    values = runtime_evidence["glassBackground"]["inputValues"]
    white = np.float32(values["inputFaceColorMatrixWhite"])
    black = np.float32(values["inputFaceColorMatrixBlack"])
    holding = np.float32(values["inputSDRHoldingToneWhite"])
    gray = np.all(samples.inputs == samples.inputs[:, :1], axis=1)
    inputs = samples.inputs[gray, 0].astype(np.float32)
    actual = samples.outputs[gray]
    continuous = (
        black * np.float32(255)
        + holding * (white - black) * inputs
    )
    predicted = np.clip(
        np.floor(continuous),
        0,
        255,
    ).astype(np.int64)[:, np.newaxis]
    predicted = np.repeat(predicted, 3, axis=1)
    return {
        "model": (
            "floor(black*255 + holdingToneWhite"
            "*(white-black)*inputCode)"
        ),
        "grayCodes": int(inputs.size),
        "whiteFloat32": float(white),
        "blackFloat32": float(black),
        "holdingToneWhiteFloat32": float(holding),
        "metrics": prediction_metrics(predicted, actual),
    }


def repeatability_report(
    primary: SweepSet,
    repeat: SweepSet,
) -> JsonObject:
    primary_files = {
        record[key]
        for record in primary.manifest["patterns"]
        for key in ("sourceFile", "controlFile", "clearFile")
    }
    repeat_files = {
        record[key]
        for record in repeat.manifest["patterns"]
        for key in ("sourceFile", "controlFile", "clearFile")
    }
    if primary_files != repeat_files:
        raise ValueError("point-sweep repeat has a different file catalog")
    differences = []
    for name in sorted(primary_files):
        left = primary.image(name).astype(np.int64)
        right = repeat.image(name).astype(np.int64)
        report = difference_report(left, right)
        if report["changedPixels"]:
            differences.append({"file": name, **report})
    return {
        "sharedPngFiles": len(primary_files),
        "exactPngFiles": len(primary_files) - len(differences),
        "differingPngFiles": len(differences),
        "differences": differences,
    }


def analyze(
    sweep: SweepSet,
    *,
    repeat: SweepSet | None,
    runtime_evidence: JsonObject | None,
) -> JsonObject:
    manifest = sweep.manifest
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(
            f"expected point-sweep schema {SCHEMA_VERSION}, got "
            f"{manifest.get('schemaVersion')!r}"
        )
    if manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(
            f"expected point-sweep rig {RIG_VERSION}, got "
            f"{manifest.get('rigVersion')!r}"
        )
    if manifest.get("windowKey") is not True:
        raise ValueError("point-sweep window was not key")

    block_size = int(manifest["blockSize"])
    grid_side = int(manifest["gridSide"])
    observations: list[
        tuple[tuple[int, int, int], tuple[int, int, int]]
    ] = []
    controls: list[JsonObject] = []
    for record in manifest["patterns"]:
        source_bytes = sweep.file_bytes(record["sourceFile"])
        control_bytes = sweep.file_bytes(record["controlFile"])
        clear_bytes = sweep.file_bytes(record["clearFile"])
        for kind, value, expected in (
            ("source", source_bytes, record["sourceFileSha256"]),
            ("control", control_bytes, record["controlFileSha256"]),
            ("clear", clear_bytes, record["clearFileSha256"]),
        ):
            actual = bytes_sha256(value)
            if actual != expected:
                raise ValueError(
                    f"{record['name']} {kind} file hash differs"
                )

        source = sweep.image(record["sourceFile"])
        control = sweep.image(record["controlFile"])
        clear = sweep.image(record["clearFile"])
        expected_shape = (
            int(manifest["pixelHeight"]),
            int(manifest["pixelWidth"]),
            4,
        )
        if (
            source.shape != expected_shape
            or control.shape != expected_shape
            or clear.shape != expected_shape
        ):
            raise ValueError(f"{record['name']} dimensions differ")
        if bytes_sha256(memoryview(control)) != record[
            "controlPixelSha256"
        ]:
            raise ValueError(f"{record['name']} control pixels differ")
        if bytes_sha256(memoryview(clear)) != record[
            "clearPixelSha256"
        ]:
            raise ValueError(f"{record['name']} clear pixels differ")
        if (
            record["controlStabilitySamples"] < 2
            or record["clearStabilitySamples"] < 2
        ):
            raise ValueError(f"{record['name']} lacks stability samples")

        requested = {
            (cell["row"], cell["column"]): (
                cell["red"],
                cell["green"],
                cell["blue"],
            )
            for cell in record["cells"]
        }
        if len(requested) != grid_side * grid_side:
            raise ValueError(f"{record['name']} cell catalog differs")
        for row in range(grid_side):
            for column in range(grid_side):
                source_value = center_patch_value(
                    source,
                    row,
                    column,
                    block_size,
                )
                if source_value != requested[(row, column)]:
                    raise ValueError(
                        f"{record['name']} source cell differs"
                    )
                control_value = center_patch_value(
                    control,
                    row,
                    column,
                    block_size,
                )
                clear_value = center_patch_value(
                    clear,
                    row,
                    column,
                    block_size,
                )
                observations.append((control_value, clear_value))
        controls.append(
            {
                "pattern": record["name"],
                **difference_report(
                    source.astype(np.int64),
                    control.astype(np.int64),
                ),
            }
        )

    samples = collect_samples(observations)
    if samples.conflicting_inputs:
        raise ValueError("identical point inputs have conflicting outputs")
    polynomial = polynomial_report(samples)
    categorical = categorical_report(samples)
    report: JsonObject = {
        "liquidGlassPointSweepSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_point_sweep.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
            "pillow": package_version("pillow"),
            "scipy": package_version("scipy"),
        },
        "source": {
            "path": str(sweep.root),
            "sha256": (
                file_sha256(sweep.root)
                if sweep.root.is_file()
                else None
            ),
            "rigVersion": manifest.get("rigVersion"),
            "ciCommit": manifest.get("ciCommit"),
            "osVersion": manifest.get("osVersion"),
        },
        "sampleDesign": {
            "patterns": len(manifest["patterns"]),
            "blockSizePixels": block_size,
            "uniformCenterPatchSidePixels": 2 * CENTER_RADIUS + 1,
            "observations": samples.observations,
            "distinctInputColors": int(samples.inputs.shape[0]),
            "inputsRepeatedAcrossPositions": int(
                np.count_nonzero(samples.occurrences > 1)
            ),
            "minimumOccurrencesPerInput": int(
                samples.occurrences.min()
            ),
            "maximumOccurrencesPerInput": int(
                samples.occurrences.max()
            ),
            "conflictingInputColors": samples.conflicting_inputs,
            "maximumOutputsPerInput": (
                samples.maximum_outputs_per_input
            ),
        },
        "sourceControlRoundTrip": {
            "allMaximumChannelDeltaAtMostOne": all(
                record["maximumChannelDelta"] <= 1
                for record in controls
            ),
            "patterns": controls,
        },
        "intervalPolynomialFits": polynomial,
        "categoricalAdditiveDiagnostic": categorical,
        "conclusion": {
            "uniformCentersVerified": True,
            "pointMappingDeterministicAcrossPositions": True,
            "singleAffineStageRejected": all(
                polynomial["linear"][quantizer][
                    "minimumExtraHalfWidthCodesByChannel"
                ][channel]
                > 1e-9
                for quantizer in ("floor", "nearest")
                for channel in range(3)
            ),
            "fullCubicStageRejected": all(
                polynomial["full-cubic"][quantizer][
                    "minimumExtraHalfWidthCodesByChannel"
                ][channel]
                > 1e-9
                for quantizer in ("floor", "nearest")
                for channel in range(3)
            ),
            "categoricalAdditiveStageFeasible": all(
                categorical["allChannelsIntervalFeasible"].values()
            ),
            "intermediateDiscreteStageRequired": True,
            "productionShaderAuthorized": False,
        },
    }
    if repeat is not None:
        report["repeatability"] = repeatability_report(sweep, repeat)
    if runtime_evidence is not None:
        report["runtimeAnchoredGrayModel"] = runtime_gray_report(
            samples,
            runtime_evidence,
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and analyze exhaustive Liquid Glass point sweeps."
        )
    )
    parser.add_argument("sweep", type=Path)
    parser.add_argument("--repeat", type=Path)
    parser.add_argument("--runtime-evidence", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    runtime = (
        json.loads(
            arguments.runtime_evidence.read_text(encoding="utf-8")
        )
        if arguments.runtime_evidence is not None
        else None
    )
    with SweepSet.open(arguments.sweep) as sweep:
        if arguments.repeat is None:
            report = analyze(
                sweep,
                repeat=None,
                runtime_evidence=runtime,
            )
        else:
            with SweepSet.open(arguments.repeat) as repeat:
                report = analyze(
                    sweep,
                    repeat=repeat,
                    runtime_evidence=runtime,
                )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
