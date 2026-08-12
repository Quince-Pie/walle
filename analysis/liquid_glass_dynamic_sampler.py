#!/usr/bin/env python3
"""Measure where live Liquid Glass capture time is spent.

The capture manifest records the raw CGWindow snapshot duration for retained
frames and the total number of sampler attempts.  Comparing those values with
the presented timeline distinguishes a slow screenshot backend from work done
after the snapshot, such as realizing and converting the embedded clock strip.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

type JsonObject = dict[str, Any]

SAMPLER_SLEEP_SECONDS = 0.001


def _load_manifest(path: Path) -> tuple[JsonObject, str]:
    if path.is_dir():
        manifest_path = path / "manifest.json"
        return json.loads(manifest_path.read_text()), str(manifest_path)

    if not zipfile.is_zipfile(path):
        raise ValueError(f"artifact is neither a directory nor a ZIP: {path}")
    with zipfile.ZipFile(path) as archive:
        matches = [
            name
            for name in archive.namelist()
            if Path(name).name == "manifest.json"
        ]
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one manifest.json in {path}, found "
                f"{len(matches)}"
            )
        return json.loads(archive.read(matches[0])), f"{path}!/{matches[0]}"


def _median(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return statistics.median(materialized) if materialized else None


def _sequence_measurement(
    sequence: JsonObject,
    *,
    run: str,
) -> JsonObject:
    attempts = int(sequence["captureAttempts"])
    decoded = int(sequence["decodedSamples"])
    frames = list(sequence["frames"])
    live_frames = [
        frame for frame in frames if float(frame["actualSeconds"]) > 0
    ]
    actual_end = max(
        (float(frame["actualSeconds"]) for frame in live_frames),
        default=0.0,
    )
    capture_durations = [
        float(frame["captureDurationSeconds"]) for frame in live_frames
    ]
    capture_median = _median(capture_durations)
    attempt_period = actual_end / attempts if attempts else None
    measured_and_sleep = (
        capture_median + SAMPLER_SLEEP_SECONDS
        if capture_median is not None
        else None
    )
    unmeasured = (
        max(0.0, attempt_period - measured_and_sleep)
        if attempt_period is not None and measured_and_sleep is not None
        else None
    )

    return {
        "run": run,
        "id": sequence["id"],
        "captureAttempts": attempts,
        "decodedSamples": decoded,
        "retainedFramesIncludingInitial": len(frames),
        "retainedCaptureDurations": len(capture_durations),
        "actualEndSeconds": actual_end,
        "effectiveAttemptsPerSecond":
            attempts / actual_end if actual_end > 0 else None,
        "medianRawCaptureMilliseconds":
            capture_median * 1_000 if capture_median is not None else None,
        "observedMillisecondsPerAttempt":
            attempt_period * 1_000 if attempt_period is not None else None,
        "configuredSleepMilliseconds": SAMPLER_SLEEP_SECONDS * 1_000,
        "estimatedUnmeasuredMillisecondsPerAttempt":
            unmeasured * 1_000 if unmeasured is not None else None,
        "observedPeriodToRawCaptureRatio":
            attempt_period / capture_median
            if attempt_period is not None
            and capture_median is not None
            and capture_median > 0
            else None,
    }


def _aggregate(rows: list[JsonObject]) -> JsonObject:
    def med(key: str) -> float | None:
        return _median(
            float(row[key]) for row in rows if row.get(key) is not None
        )

    return {
        "sequences": len(rows),
        "captureAttempts": sum(int(row["captureAttempts"]) for row in rows),
        "decodedSamples": sum(int(row["decodedSamples"]) for row in rows),
        "retainedFramesIncludingInitial": sum(
            int(row["retainedFramesIncludingInitial"]) for row in rows
        ),
        "medianRawCaptureMilliseconds": med(
            "medianRawCaptureMilliseconds"
        ),
        "medianObservedMillisecondsPerAttempt": med(
            "observedMillisecondsPerAttempt"
        ),
        "medianEstimatedUnmeasuredMillisecondsPerAttempt": med(
            "estimatedUnmeasuredMillisecondsPerAttempt"
        ),
        "medianObservedPeriodToRawCaptureRatio": med(
            "observedPeriodToRawCaptureRatio"
        ),
    }


def measure(paths: list[Path]) -> JsonObject:
    artifacts: list[JsonObject] = []
    all_rows: list[JsonObject] = []
    for path in paths:
        manifest, manifest_source = _load_manifest(path)
        rows = [
            _sequence_measurement(sequence, run=str(path))
            for sequence in manifest.get("dynamicSequences", [])
        ]
        if not rows:
            raise ValueError(f"artifact has no dynamic sequences: {path}")
        all_rows.extend(rows)
        artifacts.append({
            "artifact": str(path),
            "manifest": manifest_source,
            "ciCommit": manifest.get("ciCommit"),
            "osBuild": manifest.get("osBuild"),
            "windowPixelSize": manifest.get("windowPixelSize"),
            "summary": _aggregate(rows),
            "sequences": rows,
        })

    aggregate = _aggregate(all_rows)
    aggregate["inference"] = (
        "The raw CGWindow snapshot call is not the limiting operation when "
        "the observed sampler period is many times its recorded duration. "
        "The remainder occurs after snapshot return; in the current sampler "
        "the dominant full-frame operation there is presentation-clock pixel "
        "realization and canonical color conversion."
    )
    aggregate["measurementCaveat"] = (
        "Raw capture duration is available for retained frames only. Medians "
        "therefore diagnose scale and location of the bottleneck but are not "
        "a complete per-attempt CPU trace."
    )
    return {
        "schemaVersion": 1,
        "implementation": {
            "file": "analysis/liquid_glass_dynamic_sampler.py",
            "python": platform.python_version(),
        },
        "samplerSleepMilliseconds": SAMPLER_SLEEP_SECONDS * 1_000,
        "artifacts": artifacts,
        "aggregate": aggregate,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    report = measure(args.artifacts)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.write_text(encoded)
        print(args.output)


if __name__ == "__main__":
    main()
