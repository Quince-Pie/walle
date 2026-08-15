#!/usr/bin/env python3
"""Score walle's ANIMATING reveal against the hardware's live frames.

The 65-state ladder scores the rounded path - an explicitly set progress, which
goes through Core Animation's model layer and lays out on whole pixels.  It is
byte-exact and says nothing about the path users actually see, because an
animating layer's presentation values are interpolated without re-laying-out.
This scores that second path.

The progress values are constants, found once by searching each frame and
recorded here, exactly as the off-ladder gate records its own.  They are not
re-searched at run time: the point is to catch a regression in the geometry
law, not to re-fit it.  Each frame's expected mismatch is recorded with it, so
an improvement is visible too.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
EXTENT = 2048
# The manifest's analysisExclusionPixels: a clock-probe band the harness writes
# over the top of every frame.
EXCLUDED_ROWS = 8

# frame index -> (progress, mismatched pixels when this was recorded)
EXPECTED = {
    1: (0.0136210684, 125),
    2: (0.0740005070, 0),
    4: (0.1748244920, 0),
    6: (0.2822963200, 0),
    8: (0.3764788370, 0),
    10: (0.4704757597, 0),
    12: (0.5645734865, 35),
    14: (0.6720809384, 7),
}

type JsonObject = dict[str, object]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=Path, required=True,
                        help="directory of hardware frame-NNNN.png files")
    parser.add_argument("--capture", type=Path, required=True,
                        help="walle's capture directory, one state per frame in order")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    rows = slice(EXCLUDED_ROWS, EXTENT)
    records: list[JsonObject] = []
    worst = 0
    regressed = []
    for slot, index in enumerate(sorted(EXPECTED)):
        progress, expected = EXPECTED[index]
        reference = np.asarray(
            Image.open(arguments.frames / f"frame-{index:04d}.png").convert("RGBA")
        )[rows, :, 0].astype(int)
        mask = np.frombuffer(
            (arguments.capture / f"state-{slot:04d}.r8").read_bytes(), dtype=np.uint8
        ).reshape(EXTENT, EXTENT)[rows, :].astype(int)
        delta = mask - reference
        count = int((delta != 0).sum())
        records.append({
            "frame": index,
            "progress": progress,
            "mismatchedPixels": count,
            "expectedMismatchedPixels": expected,
            "maximumDelta": int(np.abs(delta).max()),
            "scoredPixels": int(delta.size),
        })
        worst = max(worst, count)
        if count > expected:
            regressed.append(index)
        print(f"  frame {index:2d} progress {progress:.10f}  mismatched {count:6d} "
              f"(expected {expected:6d})  maxDelta {records[-1]['maximumDelta']}")

    report = {
        "schemaVersion": 1,
        "classification": "walle's animating reveal against the M1's live frames",
        "osBuild": "25G76",
        "worstMismatchedPixels": worst,
        "byteExactFrames": sum(1 for r in records if r["mismatchedPixels"] == 0),
        "frameCount": len(records),
        "records": records,
    }
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"byte-exact {report['byteExactFrames']}/{report['frameCount']}, "
          f"worst {worst} of {records[0]['scoredPixels']} scored pixels")
    if regressed:
        print(f"REGRESSED on frames {regressed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
