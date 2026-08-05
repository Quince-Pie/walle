#!/usr/bin/env python3
"""Record the exact Apple intrinsic-table mantissas reached by one pass."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from apple_glass_reference_renderer import AppleGlassReferenceRenderer
from liquid_glass_glsl_end_to_end_gate import (
    configure_recovered_material,
)


def hexadecimal_u32(value: str) -> int:
    parsed = int(value, 0)
    if not 0 <= parsed <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("value must fit uint32")
    return parsed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("intrinsic_table", type=Path)
    parser.add_argument("coefficient_table", type=Path)
    parser.add_argument("output_bitset", type=Path)
    parser.add_argument(
        "--source-slope-bits",
        required=True,
        type=hexadecimal_u32,
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--device-index", type=int)
    arguments = parser.parse_args()

    context_arguments: dict[str, object] = {}
    if arguments.device_index is not None:
        context_arguments["device_index"] = arguments.device_index
    with AppleGlassReferenceRenderer(
        arguments.capture,
        intrinsic_table=arguments.intrinsic_table,
        interpolant_coefficient_table=arguments.coefficient_table,
        interpolant_source_slope_bits=arguments.source_slope_bits,
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        context_arguments=context_arguments,
    ) as renderer:
        configure_recovered_material(renderer)
        renderer.program["CoordinateMode"].value = 5
        words = renderer.record_intrinsic_usage()
        implementation = renderer.implementation

    arguments.output_bitset.parent.mkdir(parents=True, exist_ok=True)
    words.astype("<u4", copy=False).tofile(arguments.output_bitset)
    bits = np.unpackbits(
        words.view(np.uint8),
        axis=1,
        bitorder="little",
    )
    operation_names = ("sqrt", "rsqrt", "reciprocal")
    operation_mantissas = [
        np.flatnonzero(bits[index])
        for index in range(len(operation_names))
    ]
    mantissas = np.unique(np.concatenate(operation_mantissas))
    table = np.fromfile(arguments.intrinsic_table, dtype=np.uint8)
    if table.size != 1 << 23:
        raise ValueError("intrinsic table must contain 2^23 bytes")
    page_measurements = []
    for page_shift in (4, 5, 6, 7, 8, 9, 10, 11, 12):
        page_size = 1 << page_shift
        used_pages = np.unique(mantissas >> page_shift)
        page_measurements.append({
            "pageSize": page_size,
            "usedPages": int(used_pages.size),
            "densePageBytes": int(used_pages.size * page_size),
            "topLevelEntries": (1 << 23) // page_size,
        })
    codes, counts = np.unique(table[mantissas], return_counts=True)
    report = {
        "liquidGlassIntrinsicWorkingSetSchemaVersion": 1,
        "capture": str(arguments.capture),
        "implementation": implementation,
        "intrinsicTable": {
            "path": str(arguments.intrinsic_table),
            "sha256": sha256_file(arguments.intrinsic_table),
            "bytes": arguments.intrinsic_table.stat().st_size,
        },
        "bitset": {
            "path": str(arguments.output_bitset),
            "sha256": sha256_file(arguments.output_bitset),
            "bytes": arguments.output_bitset.stat().st_size,
        },
        "measurement": {
            "usedMantissas": int(mantissas.size),
            "fractionOfTable": float(mantissas.size / (1 << 23)),
            "minimumMantissa": (
                int(mantissas.min()) if mantissas.size else None
            ),
            "maximumMantissa": (
                int(mantissas.max()) if mantissas.size else None
            ),
            "codeDistribution": [
                {"code": int(code), "count": int(count)}
                for code, count in zip(codes, counts, strict=True)
            ],
            "operations": [
                {
                    "name": name,
                    "usedMantissas": int(values.size),
                    "fractionOfTable": float(
                        values.size / (1 << 23)
                    ),
                    "minimumMantissa": (
                        int(values.min()) if values.size else None
                    ),
                    "maximumMantissa": (
                        int(values.max()) if values.size else None
                    ),
                }
                for name, values in zip(
                    operation_names,
                    operation_mantissas,
                    strict=True,
                )
            ],
            "pageMeasurements": page_measurements,
        },
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.report is None:
        print(rendered, end="")
    else:
        arguments.report.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
