#!/usr/bin/env python3
"""Compare preparation-to-first-frame latency in two unwrapped Tracy CSVs."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import statistics
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Preparation:
    start_ns: int
    duration_ns: int
    value: str

    @property
    def end_ns(self) -> int:
        return self.start_ns + self.duration_ns


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-window-ms", type=float, default=5.0)
    parser.add_argument("--warmup-batches", type=int, default=1)
    return parser.parse_args()


def read_zones(path: Path) -> tuple[list[Preparation], list[int]]:
    preparations: list[Preparation] = []
    frame_starts: list[int] = []
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            match row["name"]:
                case "prepare wallpaper":
                    preparations.append(
                        Preparation(
                            start_ns=int(row["ns_since_start"]),
                            duration_ns=int(row["exec_time_ns"]),
                            value=row["value"],
                        )
                    )
                case "transition frame":
                    frame_starts.append(int(row["ns_since_start"]))
    return sorted(preparations, key=lambda zone: zone.start_ns), sorted(frame_starts)


def batch_preparations(
    preparations: list[Preparation], window_ns: int
) -> list[list[Preparation]]:
    batches: list[list[Preparation]] = []
    for preparation in preparations:
        if not batches or preparation.start_ns - batches[-1][-1].start_ns > window_ns:
            batches.append([preparation])
        else:
            batches[-1].append(preparation)
    return batches


def match_latencies(
    path: Path, window_ns: int
) -> list[list[dict[str, float | int | str]]]:
    preparations, frame_starts = read_zones(path)
    matched_batches: list[list[dict[str, float | int | str]]] = []
    for batch in batch_preparations(preparations, window_ns):
        first_frame_index = bisect.bisect_left(
            frame_starts, max(preparation.end_ns for preparation in batch)
        )
        batch_frames = frame_starts[first_frame_index : first_frame_index + len(batch)]
        if len(batch_frames) != len(batch):
            raise RuntimeError(f"{path}: not enough frames after a preparation batch")
        matched_batches.append(
            [
                {
                    "preparationStartMilliseconds": preparation.start_ns / 1_000_000,
                    "preparationEndMilliseconds": preparation.end_ns / 1_000_000,
                    "frameStartMilliseconds": frame_start / 1_000_000,
                    "latencyMilliseconds": (frame_start - preparation.end_ns)
                    / 1_000_000,
                    "preparationValue": preparation.value,
                }
                for preparation, frame_start in zip(
                    sorted(batch, key=lambda zone: zone.end_ns),
                    batch_frames,
                    strict=True,
                )
            ]
        )
    return matched_batches


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(
    batches: list[list[dict[str, float | int | str]]],
) -> dict[str, float | int]:
    latencies = [
        float(match["latencyMilliseconds"]) for batch in batches for match in batch
    ]
    if not latencies:
        raise RuntimeError("no matched latency samples")
    return {
        "count": len(latencies),
        "minMilliseconds": min(latencies),
        "medianMilliseconds": statistics.median(latencies),
        "meanMilliseconds": statistics.fmean(latencies),
        "p95Milliseconds": percentile(latencies, 0.95),
        "p99Milliseconds": percentile(latencies, 0.99),
        "maxMilliseconds": max(latencies),
    }


def main() -> int:
    args = parse_args()
    if args.batch_window_ms < 0 or args.warmup_batches < 0:
        raise ValueError("batch window and warmup batch count must be nonnegative")
    window_ns = int(args.batch_window_ms * 1_000_000)
    baseline_batches = match_latencies(args.baseline, window_ns)
    candidate_batches = match_latencies(args.candidate, window_ns)
    if args.warmup_batches >= min(len(baseline_batches), len(candidate_batches)):
        raise ValueError("warmup batch count leaves no samples")

    baseline_measured = baseline_batches[args.warmup_batches :]
    candidate_measured = candidate_batches[args.warmup_batches :]
    baseline_summary = summarize(baseline_measured)
    candidate_summary = summarize(candidate_measured)
    report = {
        "schemaVersion": 1,
        "classification": (
            "paired descriptive Tracy preparation-end to first-frame-start latency"
        ),
        "baseline": {
            "source": str(args.baseline),
            "summary": baseline_summary,
            "batches": baseline_batches,
        },
        "candidate": {
            "source": str(args.candidate),
            "summary": candidate_summary,
            "batches": candidate_batches,
        },
        "method": {
            "batchWindowMilliseconds": args.batch_window_ms,
            "warmupBatchesExcluded": args.warmup_batches,
            "simultaneousPreparationsUseDistinctFollowingFrames": True,
        },
        "change": {
            statistic: {
                "milliseconds": float(candidate_summary[statistic])
                - float(baseline_summary[statistic]),
                "percent": 100.0
                * (
                    float(candidate_summary[statistic])
                    / float(baseline_summary[statistic])
                    - 1.0
                ),
            }
            for statistic in ("medianMilliseconds", "meanMilliseconds")
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "baseline": baseline_summary,
                "candidate": candidate_summary,
                "change": report["change"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
