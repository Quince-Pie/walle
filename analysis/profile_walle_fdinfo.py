#!/usr/bin/env python3
"""Sample one Walle DRM client and process at a fixed cadence."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Final

KIB_PER_MIB: Final = 1024.0
KIB_MULTIPLIER: Final = {"KiB": 1, "MiB": 1024, "GiB": 1024 * 1024}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--duration", type=float, default=75.0)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def read_drm_fdinfo(pid: int) -> dict[str, int | str]:
    for path in sorted(
        Path(f"/proc/{pid}/fdinfo").iterdir(), key=lambda item: int(item.name)
    ):
        fields: dict[str, int | str] = {}
        try:
            lines = path.read_text().splitlines()
        except FileNotFoundError:
            continue  # another thread closed this unrelated fd after enumeration
        for line in lines:
            if not line.startswith("drm-"):
                continue
            key, raw_value = line.split(":", 1)
            value = raw_value.strip()
            quantity_and_unit = value.split()
            if (
                len(quantity_and_unit) == 2
                and quantity_and_unit[0].isdigit()
                and quantity_and_unit[1] in KIB_MULTIPLIER
            ):
                fields[key] = (
                    int(quantity_and_unit[0]) * KIB_MULTIPLIER[quantity_and_unit[1]]
                )
            elif value.endswith(" ns"):
                fields[key] = int(value.removesuffix(" ns"))
            else:
                try:
                    fields[key] = int(value)
                except ValueError:
                    fields[key] = value
        if "drm-client-id" in fields:
            fields["fd"] = int(path.name)
            return fields
    raise RuntimeError(f"process {pid} has no DRM fdinfo client")


def read_process(pid: int) -> dict[str, int]:
    stat_fields = Path(f"/proc/{pid}/stat").read_text().split()
    status: dict[str, int] = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        first = raw_value.strip().split(maxsplit=1)
        if first and first[0].isdigit():
            status[key] = int(first[0])
    return {
        "cpuTicks": int(stat_fields[13]) + int(stat_fields[14]),
        "rssKiB": status.get("VmRSS", 0),
        "threads": status.get("Threads", 0),
        "voluntaryContextSwitches": status.get("voluntary_ctxt_switches", 0),
        "involuntaryContextSwitches": status.get("nonvoluntary_ctxt_switches", 0),
    }


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "median": statistics.median(values),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
        "mean": statistics.fmean(values),
    }


def main() -> int:
    args = parse_args()
    if args.pid <= 0 or args.duration <= 0 or args.interval <= 0:
        raise ValueError("pid, duration, and interval must be positive")

    exe = Path(f"/proc/{args.pid}/exe").resolve(strict=True)
    clock_ticks = os.sysconf("SC_CLK_TCK")
    started_ns = time.monotonic_ns()
    deadline_ns = started_ns + int(args.duration * 1_000_000_000)
    next_sample_ns = started_ns
    samples: list[dict[str, int | float | str]] = []
    previous: dict[str, int | float | str] | None = None

    while True:
        now_ns = time.monotonic_ns()
        if now_ns < next_sample_ns:
            time.sleep((next_sample_ns - now_ns) / 1_000_000_000)
            now_ns = time.monotonic_ns()
        try:
            drm = read_drm_fdinfo(args.pid)
            process = read_process(args.pid)
        except FileNotFoundError:
            break

        sample: dict[str, int | float | str] = {
            "elapsedSeconds": (now_ns - started_ns) / 1_000_000_000,
            "clientId": int(drm["drm-client-id"]),
            "pdev": str(drm.get("drm-pdev", "")),
            "residentVramMiB": int(drm.get("drm-resident-vram", 0)) / KIB_PER_MIB,
            "totalVramMiB": int(drm.get("drm-total-vram", 0)) / KIB_PER_MIB,
            "sharedVramMiB": int(drm.get("drm-shared-vram", 0)) / KIB_PER_MIB,
            "purgeableVramMiB": int(drm.get("drm-purgeable-vram", 0)) / KIB_PER_MIB,
            "residentGttMiB": int(drm.get("drm-resident-gtt", 0)) / KIB_PER_MIB,
            "rssMiB": process["rssKiB"] / KIB_PER_MIB,
            **process,
            "gfxEngineNanoseconds": int(drm.get("drm-engine-gfx", 0)),
        }
        if previous is not None:
            elapsed = float(sample["elapsedSeconds"]) - float(
                previous["elapsedSeconds"]
            )
            gfx_delta = int(sample["gfxEngineNanoseconds"]) - int(
                previous["gfxEngineNanoseconds"]
            )
            cpu_delta = int(sample["cpuTicks"]) - int(previous["cpuTicks"])
            sample["gfxEnginePercent"] = max(0.0, gfx_delta / (elapsed * 10_000_000.0))
            sample["cpuSingleCorePercent"] = max(
                0.0, cpu_delta * 100.0 / (clock_ticks * elapsed)
            )
        samples.append(sample)
        previous = sample
        if now_ns >= deadline_ns:
            break
        next_sample_ns += int(args.interval * 1_000_000_000)

    if len(samples) < 2:
        raise RuntimeError("fewer than two samples were captured")

    metric_names = (
        "residentVramMiB",
        "totalVramMiB",
        "sharedVramMiB",
        "purgeableVramMiB",
        "residentGttMiB",
        "rssMiB",
        "gfxEnginePercent",
        "cpuSingleCorePercent",
    )
    report = {
        "schemaVersion": 1,
        "classification": "Linux DRM fdinfo and procfs wall-clock sample",
        "pid": args.pid,
        "executable": str(exe),
        "sampleIntervalSeconds": args.interval,
        "requestedDurationSeconds": args.duration,
        "observedDurationSeconds": float(samples[-1]["elapsedSeconds"]),
        "sampleCount": len(samples),
        "metrics": {
            name: summarize(
                [float(sample[name]) for sample in samples if name in sample]
            )
            for name in metric_names
        },
        "firstSample": samples[0],
        "lastSample": samples[-1],
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps({key: report[key] for key in report if key != "samples"}, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
