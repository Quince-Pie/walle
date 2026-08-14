#!/usr/bin/env python3
"""Score the recovered AGX clip endpoint and tile-setup pipeline together."""

import argparse
import json
import sys
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import score_reveal_agx_direct_endpoint_setup as direct  # noqa: E402
import score_reveal_agx_shared_setup as shared  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=int)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    direct._install_direct_endpoint_model()  # noqa: SLF001
    shared.scorer._overlay_arbitrary_triangle = (  # noqa: SLF001
        shared._overlay_arbitrary_triangle  # noqa: SLF001
    )
    result = shared.scorer.score(state_only=arguments.state)
    result["schema"] = "walle-reveal-agx-direct-endpoint-shared-setup-score-v1"
    result["model"] = (
        "recovered AGX direct endpoint materialization and p28 "
        "shared-reciprocal tile-local setup with full-vector helper lanes"
    )
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded)


if __name__ == "__main__":
    main()
