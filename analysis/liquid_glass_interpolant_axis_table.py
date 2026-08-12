#!/usr/bin/env python3
"""Create a lossless 25 KiB Apple raster-interpolant table."""

import argparse
import json
from pathlib import Path

from liquid_glass_raster_interpolant import write_axis_trace_artifact


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Losslessly separate Apple's full RGBA32UI interpolant trace "
            "into two primitive rows of RGBA32UI axis values."
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--manifest", type=Path)
    arguments = parser.parse_args()
    report = write_axis_trace_artifact(
        arguments.source,
        arguments.output,
        manifest=arguments.manifest,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
