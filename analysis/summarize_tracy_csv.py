#!/usr/bin/env python3
"""Summarize unwrapped tracy-csvexport CPU zones without hiding outliers."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import TextIO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, nargs="?")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize_nanoseconds(values: list[int]) -> dict[str, float | int]:
    milliseconds = [value / 1_000_000.0 for value in values]
    return {
        "count": len(values),
        "totalMilliseconds": sum(milliseconds),
        "minMilliseconds": min(milliseconds),
        "medianMilliseconds": statistics.median(milliseconds),
        "meanMilliseconds": statistics.fmean(milliseconds),
        "p95Milliseconds": percentile(milliseconds, 0.95),
        "p99Milliseconds": percentile(milliseconds, 0.99),
        "maxMilliseconds": max(milliseconds),
    }


def summarize(stream: TextIO) -> dict[str, object]:
    zones: defaultdict[str, list[int]] = defaultdict(list)
    values: defaultdict[str, defaultdict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for row in csv.DictReader(stream):
        name = row.get("name", "")
        execution_time = row.get("exec_time_ns", "")
        if not name or not execution_time:
            continue
        zones[name].append(int(execution_time))
        value = row.get("value", "").strip()
        if value:
            values[name][value] += 1
    return {
        "schemaVersion": 1,
        "classification": "descriptive statistics from unwrapped Tracy CPU zones",
        "zones": {
            name: {
                **summarize_nanoseconds(durations),
                "valueCounts": dict(sorted(values[name].items())),
            }
            for name, durations in sorted(zones.items())
        },
    }


def main() -> int:
    args = parse_args()
    if args.input is None:
        report = summarize(sys.stdin)
    else:
        with args.input.open(newline="") as stream:
            report = summarize(stream)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
