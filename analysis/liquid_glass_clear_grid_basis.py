#!/usr/bin/env python3
"""Measure v2.16 clear-grid phase and stage-order interventions."""

import argparse
import hashlib
import json
import platform
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_clear_state_fit import (
    STATE_THRESHOLDS,
    SampleGrid,
    sample_grid,
    state_masks,
)
from liquid_glass_spatial_fit import CaptureSet


type BoolArray = NDArray[np.bool_]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

RIG_VERSION = "2.16.0"
SCENE = "circle-4000-center"
CONTROL_SCENE = "circle-0500-center"
BOUNDARY_AMPLITUDES = (1, 2, 3, 15, 16, 17, 31, 32, 33, 47, 48, 49, 63, 64)
CELL_AMPLITUDES = (1, 17, 32, 63, 64)
CELL_EFFECTIVE_AMPLITUDES = {1: 0, 17: 4, 32: 8, 63: 16, 64: 16}
PHASES = ((0, 0), (0, 1), (1, 0), (1, 1))
DEFAULT_SAMPLE_STRIDE = 17
SAMPLE_MARGIN_PIXELS = 512


@dataclass(slots=True)
class SampleCache:
    captures: CaptureSet
    grid: SampleGrid
    decoded_backgrounds: set[str]
    outputs: dict[str, IntArray]
    references: dict[str, IntArray]

    @classmethod
    def create(cls, captures: CaptureSet, grid: SampleGrid) -> "SampleCache":
        return cls(
            captures=captures,
            grid=grid,
            decoded_backgrounds=set(),
            outputs={},
            references={},
        )

    def output(self, background: str) -> IntArray:
        if background not in self.outputs:
            image = self.captures.image(
                background,
                SCENE,
                "clear",
                "dark",
            )
            self.outputs[background] = np.asarray(
                image[self.grid.y, self.grid.x],
                dtype=np.int64,
            )
            self.decoded_backgrounds.add(background)
        return self.outputs[background]

    def reference(self, background: str) -> IntArray:
        if background not in self.references:
            image = self.captures.reference_image(background)
            self.references[background] = np.asarray(
                image[self.grid.y, self.grid.x],
                dtype=np.int64,
            )
            self.decoded_backgrounds.add(background)
        return self.references[background]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def grid_background(amplitude: int, phase_y: int, phase_x: int) -> str:
    if (
        amplitude not in range(1, 65)
        or (phase_y, phase_x) not in PHASES
        or (
            (phase_y, phase_x) != (0, 0)
            and amplitude not in BOUNDARY_AMPLITUDES
        )
    ):
        raise ValueError("invalid v2.16 shifted-grid probe")
    return (
        f"noise-rgb-a{amplitude:03d}-grid2-shift-"
        f"{phase_y}{phase_x}-train"
    )


def cell_background(amplitude: int, phase_y: int, phase_x: int) -> str:
    if amplitude not in CELL_AMPLITUDES or (phase_y, phase_x) not in PHASES:
        raise ValueError("invalid v2.16 cell-basis probe")
    return (
        f"noise-rgb-a{amplitude:03d}-cell2-basis-"
        f"{phase_y}{phase_x}-train"
    )


def source_control_backgrounds() -> tuple[str, ...]:
    return (
        *(grid_background(amplitude, 0, 0) for amplitude in (1, 17, 32, 64)),
        *(grid_background(32, phase_y, phase_x) for phase_y, phase_x in PHASES[1:]),
        *(cell_background(32, phase_y, phase_x) for phase_y, phase_x in PHASES),
    )


def selected(values: IntArray, mask: BoolArray | None) -> IntArray:
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("expected sampled RGB values")
    if mask is None:
        return values
    if mask.ndim != 1 or mask.size != values.shape[0]:
        raise ValueError("sample mask does not match RGB values")
    return values[mask]


def signed_counts(values: IntArray) -> JsonObject:
    unique, counts = np.unique(values, return_counts=True)
    return {
        str(int(value)): int(count)
        for value, count in zip(unique, counts, strict=True)
    }


def difference_report(
    left: IntArray,
    right: IntArray,
    *,
    mask: BoolArray | None = None,
) -> JsonObject:
    if left.shape != right.shape:
        raise ValueError("difference operands must have identical shapes")
    delta = selected(left - right, mask)
    pixels = delta.shape[0]
    channels = delta.size
    absolute = np.abs(delta)
    return {
        "pixels": pixels,
        "channels": channels,
        "exactChannelFraction": (
            float(np.count_nonzero(delta == 0)) / channels if channels else None
        ),
        "changedPixelFraction": (
            float(np.count_nonzero(np.any(delta != 0, axis=1))) / pixels
            if pixels
            else None
        ),
        "meanAbsoluteCodes": (
            float(absolute.sum()) / channels if channels else None
        ),
        "rootMeanSquareCodes": (
            float(np.sqrt(np.square(delta.astype(np.float64)).mean()))
            if channels
            else None
        ),
        "maximumAbsoluteCodes": int(absolute.max(initial=0)),
        "signedDeltaCounts": signed_counts(delta),
    }


def relation_report(
    relation: IntArray,
    *,
    mask: BoolArray | None = None,
) -> JsonObject:
    zero = np.zeros_like(relation)
    return difference_report(relation, zero, mask=mask)


def output_phase_masks(grid: SampleGrid, eligible: BoolArray) -> dict[str, BoolArray]:
    return {
        f"{phase_y}{phase_x}": (
            eligible
            & (grid.y % 2 == phase_y)
            & (grid.x % 2 == phase_x)
        )
        for phase_y, phase_x in PHASES
    }


def stratified_difference(
    left: IntArray,
    right: IntArray,
    *,
    grid: SampleGrid,
    eligible: BoolArray,
    states: IntArray,
) -> JsonObject:
    return {
        "all": difference_report(left, right, mask=eligible),
        "byOutputPhase": {
            phase: difference_report(left, right, mask=mask)
            for phase, mask in output_phase_masks(grid, eligible).items()
        },
        "byState": {
            str(state): difference_report(
                left,
                right,
                mask=eligible & (states == state),
            )
            for state in range(STATE_THRESHOLDS.size + 1)
            if np.any(eligible & (states == state))
        },
    }


def sampled_summary(
    values: IntArray,
    *,
    mask: BoolArray,
) -> JsonObject:
    samples = selected(values, mask)
    return {
        "channels": samples.size,
        "meanCodes": samples.mean(axis=0).tolist(),
        "standardDeviationCodes": samples.std(axis=0).tolist(),
        "minimumCodes": samples.min(axis=0).tolist(),
        "maximumCodes": samples.max(axis=0).tolist(),
    }


def interior_output(
    captures: CaptureSet,
    background: str,
    *,
    margin: int = SAMPLE_MARGIN_PIXELS,
) -> NDArray[np.uint8]:
    record = captures.records[(background, SCENE, "clear", "dark")]
    with captures.image_file(str(record["file"])) as image:
        codes = np.asarray(image.convert("RGB"), dtype=np.uint8)
    if (
        margin < 0
        or codes.shape[0] <= 2 * margin
        or codes.shape[1] <= 2 * margin
    ):
        raise ValueError("invalid full-image interior margin")
    if margin == 0:
        return codes
    return codes[margin:-margin, margin:-margin]


def interior_difference_report(
    left: NDArray[np.uint8],
    right: NDArray[np.uint8],
) -> JsonObject:
    if left.shape != right.shape or left.ndim != 3 or left.shape[2] != 3:
        raise ValueError("interior RGB images must have identical shapes")
    return difference_report(
        left.astype(np.int16).reshape(-1, 3),
        right.astype(np.int16).reshape(-1, 3),
    )


def full_interior_cell_equivalence(
    captures: CaptureSet,
) -> tuple[JsonObject, set[str]]:
    baseline = interior_output(captures, "gray-128")
    decoded = {"gray-128"}
    targets: dict[int, NDArray[np.uint8]] = {0: baseline}
    result: JsonObject = {}
    for cell_amplitude, effective_amplitude in CELL_EFFECTIVE_AMPLITUDES.items():
        if effective_amplitude not in targets:
            background = grid_background(effective_amplitude, 0, 0)
            targets[effective_amplitude] = interior_output(captures, background)
            decoded.add(background)
        target = targets[effective_amplitude]
        phase_records: JsonObject = {}
        for phase_y, phase_x in PHASES:
            background = cell_background(
                cell_amplitude,
                phase_y,
                phase_x,
            )
            codes = interior_output(captures, background)
            decoded.add(background)
            phase_records[f"{phase_y}{phase_x}"] = (
                interior_difference_report(codes, target)
            )
        result[str(cell_amplitude)] = {
            "effectiveAmplitudeCodes": effective_amplitude,
            "expectedByNearestInteger2x2Mean": True,
            "phases": phase_records,
        }
    return (
        {
            "marginPixels": SAMPLE_MARGIN_PIXELS,
            "widthPixels": int(baseline.shape[1]),
            "heightPixels": int(baseline.shape[0]),
            "pixelsPerComparison": int(
                baseline.shape[0] * baseline.shape[1]
            ),
            "cellAmplitudeToEffectiveAmplitude": result,
        },
        decoded,
    )


def source_controls(captures: CaptureSet) -> JsonObject:
    records = captures.records
    controls = []
    for background in source_control_backgrounds():
        record = records[(background, CONTROL_SCENE, "none", "dark")]
        controls.append(
            {
                "background": background,
                "sourceDiff": record.get("sourceDiff"),
                "stable": record.get("stable"),
                "stabilitySamples": record.get("stabilitySamples"),
            }
        )
    return {
        "required": len(source_control_backgrounds()),
        "available": len(controls),
        "allStable": all(record["stable"] is True for record in controls),
        "allPixelExact": all(
            record["sourceDiff"]
            == {
                "changedPixels": 0,
                "maxChannelDelta": 0,
                "meanAbsoluteChannelDelta": 0,
            }
            for record in controls
        ),
        "records": controls,
    }


def cell_phase_identity(
    cache: SampleCache,
    *,
    grid: SampleGrid,
    eligible: BoolArray,
    states: IntArray,
) -> JsonObject:
    result: JsonObject = {}
    for amplitude in CELL_AMPLITUDES:
        images = {
            phase: cache.output(cell_background(amplitude, *phase))
            for phase in PHASES
        }
        pairs: JsonObject = {}
        for left_index, left_phase in enumerate(PHASES):
            for right_phase in PHASES[left_index + 1 :]:
                pairs[
                    f"{left_phase[0]}{left_phase[1]}-"
                    f"{right_phase[0]}{right_phase[1]}"
                ] = stratified_difference(
                    images[left_phase],
                    images[right_phase],
                    grid=grid,
                    eligible=eligible,
                    states=states,
                )
        result[str(amplitude)] = {"pairs": pairs}
    return result


def cell_quarter_amplitude_equivalence(
    cache: SampleCache,
    *,
    grid: SampleGrid,
    eligible: BoolArray,
    states: IntArray,
) -> JsonObject:
    result: JsonObject = {}
    for cell_amplitude, full_amplitude in ((32, 8), (64, 16)):
        full = cache.output(grid_background(full_amplitude, 0, 0))
        result[str(cell_amplitude)] = {
            f"{phase_y}{phase_x}": stratified_difference(
                cache.output(
                    cell_background(cell_amplitude, phase_y, phase_x)
                ),
                full,
                grid=grid,
                eligible=eligible,
                states=states,
            )
            for phase_y, phase_x in PHASES
        }
    return result


def cell_superposition(
    cache: SampleCache,
    baseline: IntArray,
    *,
    eligible: BoolArray,
) -> JsonObject:
    result: JsonObject = {}
    for amplitude in CELL_AMPLITUDES:
        full = cache.output(grid_background(amplitude, 0, 0))
        cells = [
            cache.output(cell_background(amplitude, phase_y, phase_x))
            for phase_y, phase_x in PHASES
        ]
        relation = full + 3 * baseline - sum(cells)
        result[str(amplitude)] = relation_report(
            relation,
            mask=eligible,
        )
    return result


def shifted_alignment(
    captures: CaptureSet,
    cache: SampleCache,
    *,
    grid: SampleGrid,
    eligible: BoolArray,
    states: IntArray,
) -> JsonObject:
    result: JsonObject = {}
    candidate_offsets = PHASES
    for amplitude in BOUNDARY_AMPLITUDES:
        base_background = grid_background(amplitude, 0, 0)
        base_output_image = captures.image(
            base_background,
            SCENE,
            "clear",
            "dark",
        )
        base_reference_image = captures.reference_image(base_background)
        cache.decoded_backgrounds.add(base_background)
        amplitude_records: JsonObject = {}
        for phase_y, phase_x in PHASES[1:]:
            shifted_background = grid_background(
                amplitude,
                phase_y,
                phase_x,
            )
            shifted_output = cache.output(shifted_background)
            shifted_reference = cache.reference(shifted_background)
            candidates: JsonObject = {}
            for offset_y, offset_x in candidate_offsets:
                shifted_grid = SampleGrid(
                    y=grid.y + offset_y,
                    x=grid.x + offset_x,
                )
                shifted_states, shifted_eligible = state_masks(
                    captures,
                    shifted_grid,
                )[SCENE]
                comparable = (
                    eligible
                    & shifted_eligible
                    & (states == shifted_states)
                )
                base_output = np.asarray(
                    base_output_image[
                        grid.y + offset_y,
                        grid.x + offset_x,
                    ],
                    dtype=np.int64,
                )
                base_reference = np.asarray(
                    base_reference_image[
                        grid.y + offset_y,
                        grid.x + offset_x,
                    ],
                    dtype=np.int64,
                )
                label = f"{offset_y}{offset_x}"
                candidates[label] = {
                    "source": difference_report(
                        shifted_reference,
                        base_reference,
                        mask=comparable,
                    ),
                    "output": difference_report(
                        shifted_output,
                        base_output,
                        mask=comparable,
                    ),
                }
            ranked = sorted(
                candidates,
                key=lambda label: (
                    candidates[label]["output"]["meanAbsoluteCodes"],
                    -candidates[label]["output"]["exactChannelFraction"],
                    label,
                ),
            )
            expected_label = f"{phase_y}{phase_x}"
            amplitude_records[expected_label] = {
                "expectedSourceAlignment": expected_label,
                "rankedOutputAlignments": ranked,
                "candidates": candidates,
            }
        result[str(amplitude)] = amplitude_records
    return result


def amplitude_traces(
    cache: SampleCache,
    baseline: IntArray,
    *,
    eligible: BoolArray,
    states: IntArray,
) -> JsonObject:
    groups = {
        "grid2-shift-00": [
            grid_background(amplitude, 0, 0)
            for amplitude in range(1, 65)
        ],
        **{
            f"grid2-shift-{phase_y}{phase_x}": [
                grid_background(amplitude, phase_y, phase_x)
                for amplitude in BOUNDARY_AMPLITUDES
            ]
            for phase_y, phase_x in PHASES[1:]
        },
        **{
            f"cell2-basis-{phase_y}{phase_x}": [
                cell_background(amplitude, phase_y, phase_x)
                for amplitude in CELL_AMPLITUDES
            ]
            for phase_y, phase_x in PHASES
        },
    }
    return {
        group: [
            {
                "background": background,
                "amplitudeCodes": int(background.split("-a", 1)[1][:3]),
                "output": sampled_summary(
                    cache.output(background),
                    mask=eligible,
                ),
                "deviationFromGray128": sampled_summary(
                    cache.output(background) - baseline,
                    mask=eligible,
                ),
                "byStateMeanDeviationCodes": {
                    str(state): selected(
                        cache.output(background) - baseline,
                        eligible & (states == state),
                    )
                    .mean(axis=0)
                    .tolist()
                    for state in range(STATE_THRESHOLDS.size + 1)
                    if np.any(eligible & (states == state))
                },
            }
            for background in backgrounds
        ]
        for group, backgrounds in groups.items()
    }


def build_report(
    captures: CaptureSet,
    *,
    stride: int = DEFAULT_SAMPLE_STRIDE,
) -> JsonObject:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(f"expected Liquid Glass rig {RIG_VERSION}")
    sample = captures.reference_image(grid_background(1, 0, 0))
    grid = sample_grid(
        sample.shape[:2],
        margin=SAMPLE_MARGIN_PIXELS,
        stride=stride,
    )
    del sample
    states, eligible = state_masks(captures, grid)[SCENE]
    cache = SampleCache.create(captures, grid)
    baseline = cache.output("gray-128")
    full_interior, full_interior_backgrounds = (
        full_interior_cell_equivalence(captures)
    )
    cache.decoded_backgrounds.update(full_interior_backgrounds)

    report = {
        "clearGridBasisAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_grid_basis.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "Pillow": package_version("Pillow"),
        },
        "source": {
            "artifact": captures.root.name,
            "rigVersion": captures.manifest.get("rigVersion"),
            "ciCommit": captures.manifest.get("ciCommit"),
            "osVersion": captures.manifest.get("osVersion"),
            "osBuild": captures.manifest.get("osBuild"),
        },
        "sampling": {
            "scene": SCENE,
            "marginPixels": SAMPLE_MARGIN_PIXELS,
            "stridePixels": stride,
            "sampledPixels": int(grid.y.size),
            "eligiblePixels": int(np.count_nonzero(eligible)),
            "stateThresholds": STATE_THRESHOLDS.tolist(),
        },
        "sourceControls": source_controls(captures),
        "fullInteriorCellEquivalence": full_interior,
        "cellPhaseIdentity": cell_phase_identity(
            cache,
            grid=grid,
            eligible=eligible,
            states=states,
        ),
        "cellQuarterAmplitudeEquivalence": (
            cell_quarter_amplitude_equivalence(
                cache,
                grid=grid,
                eligible=eligible,
                states=states,
            )
        ),
        "cellSuperposition": cell_superposition(
            cache,
            baseline,
            eligible=eligible,
        ),
        "shiftedAlignment": shifted_alignment(
            captures,
            cache,
            grid=grid,
            eligible=eligible,
            states=states,
        ),
        "amplitudeTraces": amplitude_traces(
            cache,
            baseline,
            eligible=eligible,
            states=states,
        ),
    }
    protected_backgrounds = sorted(
        {
            str(record.get("background"))
            for record in captures.manifest.get("captures", [])
            if "holdout" in str(record.get("background"))
            and (
                "-tomography-" in str(record.get("background"))
                or "-sweep-" in str(record.get("background"))
            )
        }
    )
    decoded_protected = sorted(
        background
        for background in cache.decoded_backgrounds
        if background in protected_backgrounds
    )
    if decoded_protected:
        raise AssertionError(
            f"protected output entered v2.16 analysis: {decoded_protected}"
        )
    report["policy"] = {
        "fitInputs": (
            "v2.16 training grid2-shift/cell2-basis outputs and historical "
            "gray-128 baseline"
        ),
        "protectedBackgrounds": protected_backgrounds,
        "protectedHoldoutOutputsDecoded": False,
        "decodedTrainingBackgrounds": sorted(cache.decoded_backgrounds),
        "productionShaderModified": False,
        "qualityGate": (
            "zero unequal decoded channels on fresh protected Apple captures"
        ),
    }
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure v2.16 phase-aligned clear-grid evidence without opening "
            "protected Apple outputs."
        )
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--stride",
        type=int,
        default=DEFAULT_SAMPLE_STRIDE,
        help="analysis sampling stride in pixels",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    captures = CaptureSet.open(args.artifact)
    try:
        report = build_report(captures, stride=args.stride)
    finally:
        captures.close()
    serialized = json.dumps(
        report,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    if args.report is None:
        print(serialized)
    else:
        args.report.write_text(f"{serialized}\n", encoding="utf-8")
        print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
