#!/usr/bin/env python3
"""Recover Apple's 32x32-tile raster iterator coefficients."""

import argparse
import json
from pathlib import Path

from liquid_glass_raster_interpolant import (
    write_live_coefficient_artifact,
)


def hexadecimal_u32(value: str) -> int:
    parsed = int(value, 0)
    if not 0 <= parsed <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("value must fit uint32")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("axis_table", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--source-slope-bits",
        required=True,
        type=hexadecimal_u32,
    )
    parser.add_argument("--manifest", type=Path)
    arguments = parser.parse_args()
    report = write_live_coefficient_artifact(
        arguments.axis_table,
        arguments.output,
        source_slope_bits=arguments.source_slope_bits,
        manifest=arguments.manifest,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
