#!/usr/bin/env python3
"""Generate direct-child vertex inputs for the output-blind AGX setup probe.

The held source-triangle probe lets Apple's clipper generate every post-guard
triangle.  This companion input stream submits the public canonical children
directly, with the same one-hot source basis transported to their vertices.
Comparing raw ``LDCF`` triples between the two draws separates clipper
interpolation from ordinary triangle setup without opening reference pixels.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "analysis")]

import analyze_reveal_agx_basis_phase as phase  # noqa: E402


CATALOG_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "reveal-agx-basis-catalog.json"
)
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "direct-child-input-v2-canonical"
)
VERTEX = struct.Struct("<8I")
EXPECTED_RECORD_COUNT: Final = 690


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def generate(
    catalog_path: Path,
    output_directory: Path,
    *,
    basis_mode: str,
) -> dict[str, object]:
    if basis_mode not in {"canonical-transport", "local-onehot"}:
        raise ValueError("unknown direct-child basis mode")
    catalog, samples = phase._load_catalog(catalog_path)  # noqa: SLF001
    if len(samples) != EXPECTED_RECORD_COUNT:
        raise ValueError("direct-child record count differs")
    if output_directory.exists():
        raise FileExistsError(output_directory)
    output_directory.mkdir()

    encoded = bytearray()
    child_keys: set[tuple[int, int]] = set()
    for sample in samples:
        children = phase._canonical_children(sample)  # noqa: SLF001
        if sample.child_ordinal_within_source >= len(children):
            raise ValueError(f"record {sample.record_index} child is absent")
        child = children[sample.child_ordinal_within_source]
        child_keys.add((sample.case_index, sample.child_ordinal_within_source))
        for local_vertex, vertex in enumerate(child):
            if len(vertex) != 6:
                raise ValueError("canonical child vertex width differs")
            basis = (
                vertex[2:]
                if basis_mode == "canonical-transport"
                else tuple(
                    1.0 if component == local_vertex else 0.0 for component in range(3)
                )
                + (float(1 << local_vertex),)
            )
            encoded.extend(
                VERTEX.pack(
                    phase._bits(vertex[0]),  # noqa: SLF001
                    phase._bits(vertex[1]),  # noqa: SLF001
                    0,
                    0,
                    *(phase._bits(value) for value in basis),  # noqa: SLF001
                )
            )

    binary_path = output_directory / "reveal-agx-direct-child-vertices.bin"
    binary_path.write_bytes(encoded)
    script_path = Path(__file__).resolve()
    manifest = {
        "schema": "walle-reveal-agx-direct-child-input-v2",
        "authority": {
            "usesPublicRevealInputsOnly": True,
            "opensReferencePixels": False,
            "canonicalClipArithmeticIsAProbeHypothesis": True,
            "establishesAppleClipSetupLaw": False,
        },
        "catalog": {
            "path": str(catalog_path),
            "bytes": catalog_path.stat().st_size,
            "sha256": _sha256(catalog_path),
        },
        "input": {
            "file": binary_path.name,
            "bytes": len(encoded),
            "sha256": _sha256(binary_path),
            "recordCount": len(samples),
            "uniqueChildCount": len(child_keys),
            "verticesPerRecord": 3,
            "wordsPerVertex": 8,
            "layout": "positionXY,pad2,basis012,linear124; little-endian uint32",
            "basisMode": basis_mode,
        },
        "generator": {
            "path": str(script_path),
            "bytes": script_path.stat().st_size,
            "sha256": _sha256(script_path),
        },
        "catalogCensus": catalog["census"],
    }
    manifest_path = output_directory / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=CATALOG_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument(
        "--basis-mode",
        choices=("canonical-transport", "local-onehot"),
        default="canonical-transport",
    )
    arguments = parser.parse_args()
    report = generate(
        arguments.catalog,
        arguments.output,
        basis_mode=arguments.basis_mode,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
