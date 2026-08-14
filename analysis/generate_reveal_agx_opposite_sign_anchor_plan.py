#!/usr/bin/env python3.14
"""Generate an opposite-sign multi-anchor M1 probe for the first-product join.

The retained multi-anchor capture only contains same-sign first-product pairs,
so it cannot discriminate the cancellation behaviour that the production
residuals exercise.  This plan keeps that capture's geometry, tile, and
anchor-amplification structure and changes only the base varying values, so
both non-anchor displacement products carry opposite signs.
"""

import argparse
import json
from pathlib import Path
from typing import Final

import generate_reveal_agx_public_child_mantissa_ruler_plan as ruler
import generate_reveal_agx_single_axis_multi_anchor_plan as multi_anchor


ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "opposite-sign-anchor-plan-v1"
)
OPPOSITE_SIGN_BASE_VALUES: Final = (-0.9999999403953552, 0.0, -1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    ruler.BASE_VALUES = OPPOSITE_SIGN_BASE_VALUES
    manifest = multi_anchor.generate(arguments.output)
    manifest["schema"] = "walle-reveal-agx-opposite-sign-anchor-plan-manifest-v1"
    manifest["baseValues"] = list(OPPOSITE_SIGN_BASE_VALUES)
    (arguments.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
