#!/usr/bin/env python3
"""Validate and solve the exhaustive Liquid Glass pair sweep."""

import argparse
import hashlib
import json
import platform
import re
import resource
import time
import zipfile
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, BinaryIO, Self

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from scipy.optimize import linprog
from scipy.sparse import (
    csr_matrix,
    hstack,
    vstack,
)
from scipy.sparse.linalg import lsqr


type BoolArray = NDArray[np.bool_]
type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

SCHEMA_VERSION = 4
RIG_VERSION = "pair-sweep-1.1.0"
IMAGE_SIDE = 1024
BLOCK_SIZE = 32
GRID_SIDE = 32
PAIR_PAGE_COUNT = 64
PATTERN_COUNT = 7 * PAIR_PAGE_COUNT
PATCH_RADIUS = 5
BASELINE_CODE = 128
FEATURES_PER_CHANNEL = 255
FEATURE_COUNT = 3 * FEATURES_PER_CHANNEL + 1
# Stay farther inside each quantizer bin than HiGHS' feasibility tolerance.
# Otherwise a mathematically open upper edge can be returned on the adjacent
# integer code even when the interval model itself is feasible.
INTERVAL_MARGIN = 1e-5

PAIR_RG_PATTERN = re.compile(
    r"pair-rg-b(?P<anchor>\d{3})-p(?P<page>\d{2})"
)
PAIR_RB_PATTERN = re.compile(r"pair-rb-g128-p(?P<page>\d{2})")
PAIR_GB_PATTERN = re.compile(r"pair-gb-r128-p(?P<page>\d{2})")
LATIN_PATTERN = re.compile(
    r"latin-rgb-(?P<variant>[ab])-p(?P<page>\d{2})"
)
LATIN_COEFFICIENTS = {
    "a": (73, 151, 37),
    "b": (151, 73, 19),
}
EXPECTED_PAIR_SWEEP_DESIGN = {
    "pairPageCount": PAIR_PAGE_COUNT,
    "pairsPerPage": GRID_SIDE * GRID_SIDE,
    "uniformCenterPatchRadius": PATCH_RADIUS,
    "redGreenBlueAnchors": [0, 128, 255],
    "redBlueGreenAnchor": 128,
    "greenBlueRedAnchor": 128,
    "latinBlueFunctions": [
        {
            "name": "a",
            "redCoefficient": 73,
            "greenCoefficient": 151,
            "offset": 37,
            "modulus": 256,
        },
        {
            "name": "b",
            "redCoefficient": 151,
            "greenCoefficient": 73,
            "offset": 19,
            "modulus": 256,
        },
    ],
}


@dataclass(frozen=True, slots=True)
class PairSamples:
    requested: IntArray
    inputs: IntArray
    outputs: IntArray
    pattern_indices: IntArray
    pattern_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IntervalFit:
    coefficients: FloatArray
    minimum_extra_half_width: float
    iterations: int
    seconds: float


class PairSweep(AbstractContextManager["PairSweep"]):
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

    def file_bytes(self, name: str) -> bytes:
        with self.open_file(name) as stream:
            return stream.read()

    def image(self, name: str) -> NDArray[np.uint8]:
        with self.open_file(name) as stream:
            with Image.open(stream) as image:
                return np.asarray(image.convert("RGBA"), dtype=np.uint8)

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


def exhaustive_pair(page: int) -> tuple[IntArray, IntArray]:
    index = (
        page * GRID_SIDE * GRID_SIDE
        + np.arange(GRID_SIDE * GRID_SIDE, dtype=np.int64)
    )
    return (
        ((index >> 8) & 255).reshape(GRID_SIDE, GRID_SIDE),
        (index & 255).reshape(GRID_SIDE, GRID_SIDE),
    )


def pattern_colors(name: str) -> IntArray:
    if match := PAIR_RG_PATTERN.fullmatch(name):
        red, green = exhaustive_pair(int(match["page"]))
        blue = np.full_like(red, int(match["anchor"]))
    elif match := PAIR_RB_PATTERN.fullmatch(name):
        red, blue = exhaustive_pair(int(match["page"]))
        green = np.full_like(red, BASELINE_CODE)
    elif match := PAIR_GB_PATTERN.fullmatch(name):
        green, blue = exhaustive_pair(int(match["page"]))
        red = np.full_like(green, BASELINE_CODE)
    elif match := LATIN_PATTERN.fullmatch(name):
        red, green = exhaustive_pair(int(match["page"]))
        red_factor, green_factor, offset = LATIN_COEFFICIENTS[
            match["variant"]
        ]
        blue = (
            red_factor * red + green_factor * green + offset
        ) & 255
    else:
        raise ValueError(f"unknown pair-sweep pattern: {name}")
    return np.stack((red, green, blue), axis=-1)


def expected_pattern_names() -> tuple[str, ...]:
    names = [
        f"pair-rg-b{blue:03d}-p{page:02d}"
        for blue in (0, 128, 255)
        for page in range(PAIR_PAGE_COUNT)
    ]
    names.extend(
        name
        for page in range(PAIR_PAGE_COUNT)
        for name in (
            f"pair-rb-g128-p{page:02d}",
            f"pair-gb-r128-p{page:02d}",
        )
    )
    names.extend(
        f"latin-rgb-{variant}-p{page:02d}"
        for variant in ("a", "b")
        for page in range(PAIR_PAGE_COUNT)
    )
    return tuple(names)


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
        raise ValueError(f"pair-sweep image shape differs: {image.shape}")
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
                    "pair-sweep center patch is nonuniform at "
                    f"{np.count_nonzero(changed)} blocks"
                )
    return center.astype(np.int64)


def difference_metrics(left: IntArray, right: IntArray) -> JsonObject:
    difference = np.abs(left - right)
    changed = np.any(difference != 0, axis=-1)
    return {
        "values": int(difference.size),
        "changedPixels": int(np.count_nonzero(changed)),
        "changedPixelFraction": float(np.mean(changed)),
        "meanAbsoluteChannelDelta": float(difference.mean()),
        "maximumChannelDelta": int(difference.max(initial=0)),
    }


def collect_samples(sweep: PairSweep) -> tuple[PairSamples, JsonObject]:
    manifest = sweep.manifest
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(
            f"expected pair-sweep schema {SCHEMA_VERSION}, got "
            f"{manifest.get('schemaVersion')!r}"
        )
    if manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(
            f"expected pair-sweep rig {RIG_VERSION}, got "
            f"{manifest.get('rigVersion')!r}"
        )
    if manifest.get("windowKey") is not True:
        raise ValueError("pair-sweep window was not key")
    if (
        manifest.get("pixelWidth") != IMAGE_SIDE
        or manifest.get("pixelHeight") != IMAGE_SIDE
        or manifest.get("blockSize") != BLOCK_SIZE
        or manifest.get("gridSide") != GRID_SIDE
    ):
        raise ValueError("pair-sweep geometry differs")
    if manifest.get("pairSweepDesign") != EXPECTED_PAIR_SWEEP_DESIGN:
        raise ValueError("pair-sweep generator contract differs")

    records = manifest.get("patterns", [])
    if len(records) != PATTERN_COUNT:
        raise ValueError(
            f"expected {PATTERN_COUNT} pair patterns, "
            f"got {len(records)}"
        )
    names = tuple(record["name"] for record in records)
    if names != expected_pattern_names():
        raise ValueError("pair-sweep pattern catalog differs")

    requested_samples: list[IntArray] = []
    input_samples: list[IntArray] = []
    output_samples: list[IntArray] = []
    pattern_indices: list[IntArray] = []
    controls: list[JsonObject] = []

    for pattern_index, record in enumerate(records):
        name = record["name"]
        expected_colors = pattern_colors(name)
        if record.get("cellCount") != GRID_SIDE * GRID_SIDE:
            raise ValueError(f"{name} cell count differs")

        source_bytes = sweep.file_bytes(record["sourceFile"])
        control_bytes = sweep.file_bytes(record["controlFile"])
        clear_bytes = sweep.file_bytes(record["clearFile"])
        for kind, value, expected_hash in (
            ("source", source_bytes, record["sourceFileSha256"]),
            ("control", control_bytes, record["controlFileSha256"]),
            ("clear", clear_bytes, record["clearFileSha256"]),
        ):
            if bytes_sha256(value) != expected_hash:
                raise ValueError(f"{name} {kind} file hash differs")

        source = sweep.image(record["sourceFile"])
        control = sweep.image(record["controlFile"])
        clear = sweep.image(record["clearFile"])
        if not all(
            np.all(image[..., 3] == 255)
            for image in (source, control, clear)
        ):
            raise ValueError(f"{name} contains nonopaque pixels")
        expected_source = expanded_source(expected_colors)
        if not np.array_equal(source, expected_source):
            raise ValueError(f"{name} source regeneration differs")
        if bytes_sha256(memoryview(control)) != record[
            "controlPixelSha256"
        ]:
            raise ValueError(f"{name} control pixels differ")
        if bytes_sha256(memoryview(clear)) != record[
            "clearPixelSha256"
        ]:
            raise ValueError(f"{name} clear pixels differ")
        if (
            record["controlStabilitySamples"] < 2
            or record["clearStabilitySamples"] < 2
        ):
            raise ValueError(f"{name} lacks stable captures")

        control_centers = uniform_center_grid(control)
        clear_centers = uniform_center_grid(clear)
        requested = expected_colors.reshape(-1, 3)
        inputs = control_centers.reshape(-1, 3)
        outputs = clear_centers.reshape(-1, 3)
        requested_samples.append(requested)
        input_samples.append(inputs)
        output_samples.append(outputs)
        pattern_indices.append(
            np.full(
                requested.shape[0],
                pattern_index,
                dtype=np.int64,
            )
        )
        controls.append({
            "pattern": name,
            **difference_metrics(requested, inputs),
        })

    samples = PairSamples(
        requested=np.concatenate(requested_samples),
        inputs=np.concatenate(input_samples),
        outputs=np.concatenate(output_samples),
        pattern_indices=np.concatenate(pattern_indices),
        pattern_names=names,
    )
    maximum_delta = max(
        record["maximumChannelDelta"] for record in controls
    )
    if maximum_delta > 1:
        raise ValueError(
            "pair-sweep source/control round trip exceeds one code"
        )
    return samples, {
        "allMaximumChannelDeltaAtMostOne": maximum_delta <= 1,
        "maximumChannelDelta": maximum_delta,
        "maximumChangedPixelFraction": max(
            record["changedPixelFraction"] for record in controls
        ),
        "maximumMeanAbsoluteChannelDelta": max(
            record["meanAbsoluteChannelDelta"] for record in controls
        ),
        "patterns": controls,
    }


def packed_rgb(values: IntArray) -> IntArray:
    return (
        (values[:, 0] << 16)
        | (values[:, 1] << 8)
        | values[:, 2]
    )


def unique_mapping(
    inputs: IntArray,
    outputs: IntArray,
) -> tuple[IntArray, IntArray, JsonObject]:
    packed = packed_rgb(inputs)
    order = np.argsort(packed, kind="stable")
    ordered_inputs = inputs[order]
    ordered_outputs = outputs[order]
    same_input = np.all(
        ordered_inputs[1:] == ordered_inputs[:-1],
        axis=1,
    )
    different_output = np.any(
        ordered_outputs[1:] != ordered_outputs[:-1],
        axis=1,
    )
    conflicting_pairs = same_input & different_output
    boundaries = np.concatenate((
        np.asarray((True,)),
        ~same_input,
    ))
    first = order[boundaries]
    occurrences = np.diff(
        np.append(np.flatnonzero(boundaries), ordered_inputs.shape[0])
    )
    return (
        inputs[first],
        outputs[first],
        {
            "observations": int(inputs.shape[0]),
            "distinctInputColors": int(first.size),
            "repeatedInputColors": int(
                np.count_nonzero(occurrences > 1)
            ),
            "minimumOccurrencesPerInput": int(occurrences.min()),
            "maximumOccurrencesPerInput": int(occurrences.max()),
            "conflictingAdjacentObservations": int(
                np.count_nonzero(conflicting_pairs)
            ),
            "maximumOutputsPerInputAtMostOne": not np.any(
                conflicting_pairs
            ),
        },
    )


def categorical_additive_design(inputs: IntArray) -> csr_matrix:
    if inputs.ndim != 2 or inputs.shape[1] != 3:
        raise ValueError("pair inputs must have shape (samples, 3)")
    sample_count = inputs.shape[0]
    row_parts: list[IntArray] = [
        np.arange(sample_count, dtype=np.int64)
    ]
    column_parts: list[IntArray] = [
        np.full(sample_count, FEATURE_COUNT - 1, dtype=np.int64)
    ]
    for channel in range(3):
        codes = inputs[:, channel]
        selected = codes != BASELINE_CODE
        adjusted = np.where(codes < BASELINE_CODE, codes, codes - 1)
        row_parts.append(np.flatnonzero(selected))
        column_parts.append(
            channel * FEATURES_PER_CHANNEL + adjusted[selected]
        )
    rows = np.concatenate(row_parts)
    columns = np.concatenate(column_parts)
    data = np.ones(rows.size, dtype=np.float64)
    return csr_matrix(
        (data, (rows, columns)),
        shape=(sample_count, FEATURE_COUNT),
    )


def minimum_interval_fit(
    features: csr_matrix,
    outputs: IntArray,
    quantizer: str,
) -> IntervalFit:
    if (
        features.shape[0] != outputs.size
        or outputs.ndim != 1
        or not outputs.size
    ):
        raise ValueError("pair interval samples do not align")
    if quantizer == "floor":
        lower = outputs.astype(np.float64) + INTERVAL_MARGIN
        upper = outputs.astype(np.float64) + 1.0 - INTERVAL_MARGIN
    elif quantizer == "nearest":
        lower = outputs.astype(np.float64) - 0.5 + INTERVAL_MARGIN
        upper = outputs.astype(np.float64) + 0.5 - INTERVAL_MARGIN
    else:
        raise ValueError(f"unknown quantizer: {quantizer}")

    upper_rows = outputs < 255
    lower_rows = outputs > 0
    constraints = vstack((
        hstack((
            features[upper_rows],
            -np.ones((np.count_nonzero(upper_rows), 1)),
        )),
        hstack((
            -features[lower_rows],
            -np.ones((np.count_nonzero(lower_rows), 1)),
        )),
    ), format="csr")
    limits = np.concatenate((
        upper[upper_rows],
        -lower[lower_rows],
    ))
    objective = np.zeros(features.shape[1] + 1, dtype=np.float64)
    objective[-1] = 1
    started = time.perf_counter()
    result = linprog(
        objective,
        A_ub=constraints,
        b_ub=limits,
        bounds=(
            *((None, None) for _ in range(features.shape[1])),
            (0, None),
        ),
        method="highs",
        options={"presolve": True},
    )
    seconds = time.perf_counter() - started
    if not result.success:
        raise RuntimeError(
            f"pair interval solve failed: {result.message}"
        )
    return IntervalFit(
        coefficients=result.x[:-1],
        minimum_extra_half_width=float(result.x[-1]),
        iterations=int(result.nit),
        seconds=seconds,
    )


def quantized_prediction(
    continuous: FloatArray,
    quantizer: str,
) -> IntArray:
    if quantizer == "floor":
        quantized = np.floor(continuous)
    elif quantizer == "nearest":
        quantized = np.floor(continuous + 0.5)
    else:
        raise ValueError(f"unknown quantizer: {quantizer}")
    return np.clip(quantized, 0, 255).astype(np.int64)


def prediction_metrics(predicted: IntArray, actual: IntArray) -> JsonObject:
    difference = predicted - actual
    absolute = np.abs(difference)
    exact = difference == 0
    return {
        "channelValues": int(actual.size),
        "exactChannelFraction": float(np.mean(exact)),
        "exactPixelFraction": float(np.mean(np.all(exact, axis=1))),
        "meanAbsoluteErrorCodes": float(absolute.mean()),
        "maximumAbsoluteErrorCodes": int(absolute.max(initial=0)),
        "missedInputColors": int(
            np.count_nonzero(np.any(~exact, axis=1))
        ),
    }


def interval_report(
    features: csr_matrix,
    outputs: IntArray,
) -> JsonObject:
    report: JsonObject = {}
    for quantizer in ("floor", "nearest"):
        fits = [
            minimum_interval_fit(
                features,
                outputs[:, channel],
                quantizer,
            )
            for channel in range(3)
        ]
        coefficients = np.column_stack([
            fit.coefficients for fit in fits
        ])
        predicted = quantized_prediction(
            np.asarray(features @ coefficients),
            quantizer,
        )
        report[quantizer] = {
            "minimumExtraHalfWidthCodesByChannel": [
                fit.minimum_extra_half_width for fit in fits
            ],
            "allChannelsMathematicallyFeasible": all(
                fit.minimum_extra_half_width <= 1e-8
                for fit in fits
            ),
            "iterationsByChannel": [
                fit.iterations for fit in fits
            ],
            "solveSecondsByChannel": [
                fit.seconds for fit in fits
            ],
            "predictionAtReturnedFeasiblePoint":
                prediction_metrics(predicted, outputs),
            "coefficientsByOutputChannel": coefficients.T.tolist(),
        }
    return report


def family_masks(samples: PairSamples) -> dict[str, BoolArray]:
    names = np.asarray(samples.pattern_names, dtype=object)
    observation_names = names[samples.pattern_indices]
    pair_baseline = np.fromiter(
        (
            name.startswith("pair-rg-b128")
            or name.startswith("pair-rb-g128")
            or name.startswith("pair-gb-r128")
            for name in observation_names
        ),
        dtype=np.bool_,
        count=observation_names.size,
    )
    return {
        "pairBaselineTraining": pair_baseline,
        "redGreenBlueZero": np.char.startswith(
            observation_names.astype(str),
            "pair-rg-b000",
        ),
        "redGreenBlue255": np.char.startswith(
            observation_names.astype(str),
            "pair-rg-b255",
        ),
        "latinA": np.char.startswith(
            observation_names.astype(str),
            "latin-rgb-a",
        ),
        "latinB": np.char.startswith(
            observation_names.astype(str),
            "latin-rgb-b",
        ),
    }


def least_squares_holdouts(samples: PairSamples) -> JsonObject:
    masks = family_masks(samples)
    training = masks["pairBaselineTraining"]
    training_features = categorical_additive_design(
        samples.requested[training]
    )
    coefficients = np.column_stack([
        lsqr(
            training_features,
            samples.outputs[training, channel],
            atol=1e-11,
            btol=1e-11,
            iter_lim=5000,
        )[0]
        for channel in range(3)
    ])
    report: JsonObject = {}
    for name, selected in masks.items():
        features = categorical_additive_design(
            samples.requested[selected]
        )
        predicted = np.clip(
            np.rint(features @ coefficients),
            0,
            255,
        ).astype(np.int64)
        report[name] = {
            "inputColors": int(np.count_nonzero(selected)),
            **prediction_metrics(
                predicted,
                samples.outputs[selected],
            ),
        }
    return report


def analyze(sweep: PairSweep) -> JsonObject:
    started = time.perf_counter()
    samples, controls = collect_samples(sweep)
    unique_inputs, unique_outputs, mapping = unique_mapping(
        samples.requested,
        samples.outputs,
    )
    if not mapping["maximumOutputsPerInputAtMostOne"]:
        raise ValueError(
            "identical generated pair inputs have conflicting outputs"
        )
    _, _, control_alias_mapping = unique_mapping(
        samples.inputs,
        samples.outputs,
    )
    features = categorical_additive_design(unique_inputs)
    intervals = interval_report(features, unique_outputs)
    elapsed = time.perf_counter() - started
    return {
        "liquidGlassPairSweepAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_pair_sweep.py",
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
            "rigVersion": sweep.manifest.get("rigVersion"),
            "ciCommit": sweep.manifest.get("ciCommit"),
            "osVersion": sweep.manifest.get("osVersion"),
        },
        "sampleDesign": {
            "patterns": len(samples.pattern_names),
            "blockSizePixels": BLOCK_SIZE,
            "uniformCenterPatchSidePixels": 2 * PATCH_RADIUS + 1,
            "modelInputDomain": (
                "exact regenerated source RGB codes before the "
                "8-bit no-glass capture round trip"
            ),
            **mapping,
        },
        "sourceControlRoundTrip": controls,
        "postCaptureControlAliases": {
            **control_alias_mapping,
            "interpretation": (
                "Distinct generated source codes can collapse onto one "
                "captured 8-bit control code while remaining distinct "
                "to the glass filter."
            ),
        },
        "sparseCategoricalAdditiveModel": {
            "features": features.shape[1],
            "constraintRowNonzerosBeforeSlack": int(features.nnz),
            "denseDesignBytesAvoided": int(
                features.shape[0]
                * features.shape[1]
                * np.dtype(np.float64).itemsize
            ),
            "csrDesignBytes": int(
                features.data.nbytes
                + features.indices.nbytes
                + features.indptr.nbytes
            ),
            "intervalFits": intervals,
            "leastSquaresTrainingAndHoldouts":
                least_squares_holdouts(samples),
        },
        "resourceMeasurements": {
            "analysisSeconds": elapsed,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "uniformCentersVerified": True,
            "mappingDeterministicAcrossRepeatedGeneratedInputs": True,
            "categoricalAdditiveStageFeasible": all(
                value["allChannelsMathematicallyFeasible"]
                for value in intervals.values()
            ),
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and solve an exhaustive Liquid Glass pair sweep."
        )
    )
    parser.add_argument("sweep", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    with PairSweep.open(arguments.sweep) as sweep:
        report = analyze(sweep)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
