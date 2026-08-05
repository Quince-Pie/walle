#!/usr/bin/env python3
"""Decompose Apple's live wallpaper transition into spatial and temporal laws.

The full-screen portion of each capture supplies its own two endpoints:

* the strongest post-expansion frame is the fully materialized glass image;
* the delayed, clock-free control is the incoming wallpaper.

Every later real frame is projected onto the line between those endpoints in
both code-value and linear-light sRGB space.  The residual falsifies a scalar
materialization law; it is never hidden by a visual similarity score.  Forward
sequences form the fit partition and reverse sequences remain held out.
"""

import argparse
import io
import json
import math
import platform
import statistics
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Self

import numpy as np
from numpy.typing import NDArray
from PIL import Image


type JsonObject = dict[str, Any]
type CodeImage = NDArray[np.uint8]
type FloatImage = NDArray[np.float32]
type BoolImage = NDArray[np.bool_]

FORWARD_MODE = "wallpaper-transition"
REVERSE_MODE = "wallpaper-transition-reverse"
FULL_SCREEN_PROGRESS_FLOOR = 0.62


@dataclass(slots=True)
class Artifact:
    path: Path
    manifest: JsonObject
    _root: Path | None
    _archive: zipfile.ZipFile | None
    _prefix: PurePosixPath

    @classmethod
    def open(cls, path: Path) -> Self:
        if path.is_dir():
            manifest_path = path / "manifest.json"
            return cls(
                path=path,
                manifest=json.loads(manifest_path.read_text()),
                _root=path.resolve(),
                _archive=None,
                _prefix=PurePosixPath(),
            )
        if not zipfile.is_zipfile(path):
            raise ValueError(f"artifact is neither a directory nor a ZIP: {path}")
        archive = zipfile.ZipFile(path)
        manifests = [
            name
            for name in archive.namelist()
            if PurePosixPath(name).name == "manifest.json"
        ]
        if len(manifests) != 1:
            archive.close()
            raise ValueError(
                f"expected one manifest.json in {path}, found {len(manifests)}"
            )
        manifest_name = PurePosixPath(manifests[0])
        return cls(
            path=path,
            manifest=json.loads(archive.read(str(manifest_name))),
            _root=None,
            _archive=archive,
            _prefix=manifest_name.parent,
        )

    def close(self) -> None:
        if self._archive is not None:
            self._archive.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def read(self, relative: str) -> bytes:
        logical = PurePosixPath(relative)
        if logical.is_absolute() or ".." in logical.parts:
            raise ValueError(f"unsafe artifact path: {relative!r}")
        if self._archive is not None:
            return self._archive.read(str(self._prefix / logical))
        assert self._root is not None
        candidate = (self._root / Path(*logical.parts)).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as error:
            raise ValueError(f"artifact path escapes root: {relative!r}") from error
        return candidate.read_bytes()

    def image(self, relative: str) -> CodeImage:
        with Image.open(io.BytesIO(self.read(relative))) as source:
            return np.asarray(source.convert("RGB"), dtype=np.uint8).copy()


def inclusion_mask(shape: tuple[int, int], exclusions: object) -> BoolImage:
    height, width = shape
    included = np.ones((height, width), dtype=np.bool_)
    if not isinstance(exclusions, list):
        return included
    for value in exclusions:
        if not isinstance(value, dict):
            continue
        x = int(value.get("x", 0))
        y = int(value.get("y", 0))
        rectangle_width = int(value.get("width", 0))
        rectangle_height = int(value.get("height", 0))
        left = max(0, x)
        top = max(0, y)
        right = min(width, x + rectangle_width)
        bottom = min(height, y + rectangle_height)
        if left < right and top < bottom:
            included[top:bottom, left:right] = False
    return included


def srgb_decode(codes: CodeImage) -> FloatImage:
    values = codes.astype(np.float32) / np.float32(255)
    return np.where(
        values <= np.float32(0.04045),
        values / np.float32(12.92),
        np.power(
            (values + np.float32(0.055)) / np.float32(1.055),
            np.float32(2.4),
        ),
    ).astype(np.float32, copy=False)


def srgb_encode(values: FloatImage) -> FloatImage:
    bounded = np.clip(values, 0, 1)
    return (
        np.where(
            bounded <= np.float32(0.0031308),
            bounded * np.float32(12.92),
            np.float32(1.055)
            * np.power(bounded, np.float32(1 / 2.4))
            - np.float32(0.055),
        )
        * np.float32(255)
    ).astype(np.float32, copy=False)


def _selected(values: FloatImage, included: BoolImage) -> NDArray[np.float32]:
    return values[included].reshape(-1)


def _projection(
    *,
    frame: FloatImage,
    incoming: FloatImage,
    glass: FloatImage,
    included: BoolImage,
    encoded_prediction: bool,
) -> JsonObject:
    delta = glass - incoming
    observed = frame - incoming
    selected_delta = _selected(delta, included)
    selected_observed = _selected(observed, included)
    denominator = float(
        np.dot(
            selected_delta.astype(np.float64),
            selected_delta.astype(np.float64),
        )
    )
    if denominator == 0:
        raise ValueError("glass anchor is identical to incoming endpoint")
    alpha = float(
        np.dot(
            selected_observed.astype(np.float64),
            selected_delta.astype(np.float64),
        )
        / denominator
    )
    channel_alpha: list[float] = []
    for channel in range(3):
        channel_delta = delta[..., channel][included].astype(np.float64)
        channel_observed = observed[..., channel][included].astype(np.float64)
        channel_denominator = float(np.dot(channel_delta, channel_delta))
        channel_alpha.append(
            float(np.dot(channel_observed, channel_delta) / channel_denominator)
            if channel_denominator
            else math.nan
        )

    prediction = incoming + np.float32(alpha) * delta
    prediction_codes = (
        srgb_encode(prediction) if encoded_prediction else prediction
    )
    observed_codes = (
        srgb_encode(frame) if encoded_prediction else frame
    )
    continuous_error = np.abs(
        _selected(observed_codes - prediction_codes, included)
    ).astype(np.float64)
    quantized = np.rint(np.clip(prediction_codes, 0, 255))
    observed_quantized = np.rint(np.clip(observed_codes, 0, 255))
    quantized_error = np.abs(
        _selected(observed_quantized - quantized, included)
    ).astype(np.float64)
    return {
        "alpha": alpha,
        "channelAlpha": channel_alpha,
        "channelAlphaSpread": float(
            max(channel_alpha) - min(channel_alpha)
        ),
        "continuousMeanAbsoluteCodes": float(continuous_error.mean()),
        "continuousRMSECodes": float(
            np.sqrt(np.mean(np.square(continuous_error)))
        ),
        "quantizedMeanAbsoluteCodes": float(quantized_error.mean()),
        "quantizedMismatchFraction": float(
            np.count_nonzero(quantized_error) / quantized_error.size
        ),
        "quantizedMaximumAbsoluteCodes": float(
            quantized_error.max(initial=0)
        ),
    }


def frame_projection(
    *,
    frame: CodeImage,
    incoming: CodeImage,
    glass: CodeImage,
    included: BoolImage,
) -> JsonObject:
    code = _projection(
        frame=frame.astype(np.float32),
        incoming=incoming.astype(np.float32),
        glass=glass.astype(np.float32),
        included=included,
        encoded_prediction=False,
    )
    linear = _projection(
        frame=srgb_decode(frame),
        incoming=srgb_decode(incoming),
        glass=srgb_decode(glass),
        included=included,
        encoded_prediction=True,
    )
    preferred = min(
        ("codeValue", code),
        ("linearLightSRGB", linear),
        key=lambda item: float(item[1]["quantizedMeanAbsoluteCodes"]),
    )[0]
    return {
        "codeValue": code,
        "linearLightSRGB": linear,
        "lowerQuantizedResidualModel": preferred,
    }


def endpoint_strength(
    frame: CodeImage,
    incoming: CodeImage,
    included: BoolImage,
) -> float:
    difference = (
        frame.astype(np.float32) - incoming.astype(np.float32)
    )
    selected = _selected(difference, included).astype(np.float64)
    return float(np.sqrt(np.mean(np.square(selected))))


def endpoint_comparison(
    frame: CodeImage,
    incoming: CodeImage,
    included: BoolImage,
) -> JsonObject:
    difference = np.abs(
        frame.astype(np.int16) - incoming.astype(np.int16)
    )
    selected = difference[included].reshape(-1)
    changed_pixels = np.any(difference != 0, axis=2) & included
    mismatched_channels = int(np.count_nonzero(selected))
    return {
        "analysisRegionBitExact": mismatched_channels == 0,
        "mismatchedChannels": mismatched_channels,
        "mismatchedPixels": int(np.count_nonzero(changed_pixels)),
        "maximumAbsoluteCode": int(selected.max(initial=0)),
        "meanAbsoluteCode": float(selected.mean()),
    }


def _timeline_coordinate(
    *,
    actual: float,
    presented: float,
    duration: float,
) -> float:
    if presented < 0.995:
        return presented
    return max(presented, actual / duration)


def analyze_sequence(artifact: Artifact, sequence: JsonObject) -> JsonObject:
    duration = float(sequence["durationSeconds"])
    exclusions = sequence.get("analysisExclusionPixels", [])
    incoming_record = sequence.get("postSettleFrame")
    if not isinstance(incoming_record, dict):
        raise ValueError(f"{sequence['id']}: missing post-settle endpoint")
    incoming = artifact.image(str(incoming_record["file"]))
    included = inclusion_mask(incoming.shape[:2], exclusions)

    frames = [
        frame
        for frame in sequence["frames"]
        if isinstance(frame, dict)
        and float(frame["presentationProgress"]) >= FULL_SCREEN_PROGRESS_FLOOR
    ]
    if not frames:
        raise ValueError(f"{sequence['id']}: no full-screen live frames")
    loaded = [
        (
            endpoint_strength(
                image := artifact.image(str(frame["file"])),
                incoming,
                included,
            ),
            frame,
            image,
        )
        for frame in frames
    ]
    _, anchor_record, glass = max(loaded, key=lambda item: item[0])
    anchor_progress = float(anchor_record["presentationProgress"])
    anchor_strength = endpoint_strength(glass, incoming, included)

    samples: list[JsonObject] = []
    for _, frame_record, image in loaded:
        presented = float(frame_record["presentationProgress"])
        actual = float(frame_record["actualSeconds"])
        samples.append({
            "phase": "live",
            "file": frame_record["file"],
            "index": frame_record["index"],
            "captureBackend": frame_record.get("captureBackend"),
            "actualSeconds": actual,
            "presentationProgress": presented,
            "timelineCoordinate": _timeline_coordinate(
                actual=actual,
                presented=presented,
                duration=duration,
            ),
            "endpointComparison": endpoint_comparison(
                image,
                incoming,
                included,
            ),
            "projection": frame_projection(
                frame=image,
                incoming=incoming,
                glass=glass,
                included=included,
            ),
        })

    tail_values = sequence.get("tailFrames", [])
    if isinstance(tail_values, list):
        for tail in tail_values:
            if not isinstance(tail, dict):
                continue
            actual = float(tail["actualSeconds"])
            presented = float(tail["presentationProgress"])
            samples.append({
                "phase": "tail",
                "file": tail["file"],
                "sample": tail["sample"],
                "captureBackend": tail.get("captureBackend"),
                "actualSeconds": actual,
                "presentationProgress": presented,
                "tailProgress": tail.get("tailProgress"),
                "secondsAfterNominalEndpoint":
                    tail.get("secondsAfterNominalEndpoint"),
                "timelineCoordinate": _timeline_coordinate(
                    actual=actual,
                    presented=presented,
                    duration=duration,
                ),
                "endpointComparison": endpoint_comparison(
                    tail_image := artifact.image(str(tail["file"])),
                    incoming,
                    included,
                ),
                "projection": frame_projection(
                    frame=tail_image,
                    incoming=incoming,
                    glass=glass,
                    included=included,
                ),
            })
    samples.sort(key=lambda sample: (
        float(sample["actualSeconds"]),
        0 if sample["phase"] == "live" else 1,
    ))

    residuals = {
        model: [
            float(sample["projection"][model]["quantizedMeanAbsoluteCodes"])
            for sample in samples
        ]
        for model in ("codeValue", "linearLightSRGB")
    }
    preferred = min(
        residuals,
        key=lambda model: statistics.fmean(residuals[model]),
    )

    def convergence_record(sample: JsonObject) -> JsonObject:
        return {
            key: sample.get(key)
            for key in (
                "phase",
                "file",
                "index",
                "sample",
                "captureBackend",
                "actualSeconds",
                "presentationProgress",
                "tailProgress",
                "secondsAfterNominalEndpoint",
                "timelineCoordinate",
            )
            if key in sample
        } | {
            "endpointComparison": sample["endpointComparison"],
        }

    exact_samples = [
        sample
        for sample in samples
        if sample["endpointComparison"]["analysisRegionBitExact"]
    ]
    nonexact_samples = [
        sample
        for sample in samples
        if not sample["endpointComparison"]["analysisRegionBitExact"]
    ]
    last_nonexact_index = max(
        (
            index
            for index, sample in enumerate(samples)
            if not sample["endpointComparison"]["analysisRegionBitExact"]
        ),
        default=-1,
    )
    stable_suffix = samples[last_nonexact_index + 1 :]
    stable_suffix_start = stable_suffix[0] if stable_suffix else None
    backend_counts: dict[str, int] = {}
    for sample in samples:
        backend = sample.get("captureBackend")
        if isinstance(backend, str):
            backend_counts[backend] = backend_counts.get(backend, 0) + 1

    return {
        "id": sequence["id"],
        "mode": sequence["mode"],
        "partition":
            "training"
            if sequence["mode"] == FORWARD_MODE
            else "holdout",
        "overlay": sequence["overlay"],
        "appearance": sequence["appearance"],
        "durationSeconds": duration,
        "includedPixels": int(np.count_nonzero(included)),
        "excludedPixels": int(included.size - np.count_nonzero(included)),
        "incomingEndpointFile": incoming_record["file"],
        "glassAnchor": {
            "file": anchor_record["file"],
            "index": anchor_record["index"],
            "actualSeconds": anchor_record["actualSeconds"],
            "presentationProgress": anchor_progress,
            "endpointRMSECodes": anchor_strength,
        },
        "preferredBlendSpace": preferred,
        "meanQuantizedResidualCodes": {
            model: statistics.fmean(values)
            for model, values in residuals.items()
        },
        "endpointConvergence": {
            "comparisonRegion": "analysis pixels after declared exclusions",
            "observedSamples": len(samples),
            "bitExactSamples": len(exact_samples),
            "nonExactSamples": len(nonexact_samples),
            "captureBackendSampleCounts": backend_counts,
            "firstBitExactObservedSample":
                convergence_record(exact_samples[0])
                if exact_samples
                else None,
            "lastNonExactObservedSample":
                convergence_record(nonexact_samples[-1])
                if nonexact_samples
                else None,
            "stableBitExactSuffixStart":
                convergence_record(stable_suffix_start)
                if stable_suffix_start is not None
                else None,
            "stableBitExactSuffixSamples": len(stable_suffix),
            "stableBitExactEndpointObserved": bool(stable_suffix),
        },
        "samples": samples,
    }


def _curve(
    sequence: JsonObject,
    model: str,
    coordinate: str,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    coordinates: list[float] = []
    alphas: list[float] = []
    for sample in sequence["samples"]:
        match coordinate:
            case "actualSecondsNormalized":
                coordinate_value = (
                    float(sample["actualSeconds"])
                    / float(sequence["durationSeconds"])
                )
            case "presentationProgress":
                coordinate_value = float(sample["presentationProgress"])
            case "hybridTail":
                coordinate_value = float(sample["timelineCoordinate"])
            case _:
                raise ValueError(f"unknown timeline coordinate: {coordinate}")
        alpha = float(sample["projection"][model]["alpha"])
        if coordinates and math.isclose(
            coordinate_value, coordinates[-1], rel_tol=0, abs_tol=1e-12
        ):
            alphas[-1] = min(alphas[-1], alpha)
        else:
            coordinates.append(coordinate_value)
            alphas.append(alpha)
    return (
        np.asarray(coordinates, dtype=np.float64),
        np.minimum.accumulate(np.asarray(alphas, dtype=np.float64)),
    )


def holdout_report(
    sequences: list[JsonObject],
    *,
    coordinate: str = "actualSecondsNormalized",
) -> JsonObject:
    by_profile = {
        (
            str(sequence["mode"]),
            str(sequence["overlay"]),
            str(sequence["appearance"]),
        ): sequence
        for sequence in sequences
    }
    pairs: list[JsonObject] = []
    all_errors: list[float] = []
    for overlay in ("regular", "clear"):
        for appearance in ("light", "dark"):
            training = by_profile.get((FORWARD_MODE, overlay, appearance))
            holdout = by_profile.get((REVERSE_MODE, overlay, appearance))
            if training is None or holdout is None:
                continue
            model = str(training["preferredBlendSpace"])
            train_x, train_alpha = _curve(training, model, coordinate)
            holdout_x, holdout_alpha = _curve(holdout, model, coordinate)
            predicted = np.interp(
                holdout_x,
                train_x,
                train_alpha,
                left=train_alpha[0],
                right=train_alpha[-1],
            )
            error = np.abs(predicted - holdout_alpha)
            all_errors.extend(error.tolist())
            pairs.append({
                "overlay": overlay,
                "appearance": appearance,
                "blendSpace": model,
                "trainingSequence": training["id"],
                "holdoutSequence": holdout["id"],
                "holdoutSamples": int(error.size),
                "meanAbsoluteAlphaError": float(error.mean()),
                "maximumAbsoluteAlphaError": float(error.max(initial=0)),
            })
    return {
        "partitionRule":
            "wallpaper-transition fits; wallpaper-transition-reverse holds out",
        "timelineCoordinate": coordinate,
        "interpolator": "piecewise-linear monotone-envelope",
        "pairs": pairs,
        "aggregateMeanAbsoluteAlphaError":
            statistics.fmean(all_errors) if all_errors else None,
        "aggregateMaximumAbsoluteAlphaError":
            max(all_errors, default=None),
    }


def analyze(path: Path) -> JsonObject:
    with Artifact.open(path) as artifact:
        sequences = [
            analyze_sequence(artifact, sequence)
            for sequence in artifact.manifest.get("dynamicSequences", [])
            if sequence.get("mode") in {FORWARD_MODE, REVERSE_MODE}
        ]
        if not sequences:
            raise ValueError("artifact has no wallpaper-transition sequences")
        residual_values = [
            float(sample["projection"][sequence["preferredBlendSpace"]][
                "quantizedMeanAbsoluteCodes"
            ])
            for sequence in sequences
            for sample in sequence["samples"]
        ]
        active_residual_values = [
            float(sample["projection"][sequence["preferredBlendSpace"]][
                "quantizedMeanAbsoluteCodes"
            ])
            for sequence in sequences
            for sample in sequence["samples"]
            if 0.02
            < float(sample["projection"][sequence["preferredBlendSpace"]]["alpha"])
            < 0.98
        ]
        holdout = holdout_report(sequences)
        holdout["coordinateComparisons"] = {
            coordinate: {
                "aggregateMeanAbsoluteAlphaError":
                    comparison["aggregateMeanAbsoluteAlphaError"],
                "aggregateMaximumAbsoluteAlphaError":
                    comparison["aggregateMaximumAbsoluteAlphaError"],
            }
            for coordinate in ("presentationProgress", "hybridTail")
            for comparison in [
                holdout_report(sequences, coordinate=coordinate)
            ]
        }
        return {
            "schemaVersion": 2,
            "implementation": {
                "file":
                    "analysis/liquid_glass_transition_decomposition.py",
                "python": platform.python_version(),
                "numpy": np.__version__,
            },
            "artifact": str(path),
            "ciCommit": artifact.manifest.get("ciCommit"),
            "osBuild": artifact.manifest.get("osBuild"),
            "windowPoints": artifact.manifest.get("windowPoints"),
            "backingScaleFactor":
                artifact.manifest.get("backingScaleFactor"),
            "transitionOriginNormalized":
                artifact.manifest.get("transitionOriginNormalized"),
            "sequences": sequences,
            "holdout": holdout,
            "aggregate": {
                "samples": len(residual_values),
                "meanPreferredQuantizedResidualCodes":
                    statistics.fmean(residual_values),
                "maximumPreferredQuantizedResidualCodes":
                    max(residual_values),
                "activeBlendSamples": len(active_residual_values),
                "activeBlendMeanPreferredQuantizedResidualCodes":
                    statistics.fmean(active_residual_values)
                    if active_residual_values
                    else None,
                "activeBlendMaximumPreferredQuantizedResidualCodes":
                    max(active_residual_values, default=None),
                "scalarBlendIsBitExact":
                    all(value == 0 for value in residual_values),
                "temporalAlgorithmFullyDetermined": False,
                "productionShaderAuthorized": False,
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decompose real Apple live Liquid Glass frames"
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    report = analyze(args.artifact)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.write_text(encoded)
        print(args.output)


if __name__ == "__main__":
    main()
