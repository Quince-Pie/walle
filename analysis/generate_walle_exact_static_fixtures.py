#!/usr/bin/env python3
"""Package independent static inputs for the Walle-owned OpenGL gate."""

import argparse
import hashlib
import json
import struct
from pathlib import Path

import numpy as np
import raster_tile_selector_model as raster_arithmetic

from apple_glass_reference_renderer import bgra_raw
from liquid_glass_independent_mips import (
    generated_static_source_pyramid_levels_from_wallpaper,
)
from liquid_glass_runtime_raster_coefficients import (
    coefficient_table,
    runtime_quad_from_vertices,
    slopes_bits,
)
from liquid_glass_static_background import (
    coordinate_hash_prepass_bgra,
    coordinate_hash_wallpaper_rgba,
)
from liquid_glass_static_geometry import (
    build_static_circle_geometry,
    canonical_static_circle_geometry_request,
)
from liquid_glass_static_highlight import build_static_highlight
from liquid_glass_static_profile import (
    build_static_profile,
    canonical_static_profile_request,
)
from run_captured_input_reference_oracle import FIXTURES


MAGIC = b"WALLELG1"
CONFIG_FORMAT = "<8s11I"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_file(
    directory: Path,
    name: str,
    data: bytes,
    files: dict[str, object],
    *,
    role: str,
) -> None:
    (directory / name).write_bytes(data)
    files[name] = {
        "byteCount": len(data),
        "role": role,
        "sha256": sha256(data),
    }


def generate_fixture(
    output: Path,
    *,
    fixture: object,
    wallpaper: np.ndarray,
    destination_bgra: np.ndarray,
    selector_table: tuple[int, ...],
) -> dict[str, object]:
    name = str(getattr(fixture, "name"))
    material = "clear" if name.startswith("clear-") else "regular"
    appearance = "dark" if name.endswith("-dark") else "light"
    directory = output / name
    directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, object] = {}

    geometry = build_static_circle_geometry(
        canonical_static_circle_geometry_request(material)
    )
    highlight = build_static_highlight(material, appearance)
    profile = build_static_profile(
        canonical_static_profile_request(material, appearance)
    )
    quad = runtime_quad_from_vertices(geometry.main_vertices, name=name)
    tile_start, coefficients = coefficient_table(
        quad,
        selector_table=selector_table,
    )
    slopes = slopes_bits(quad, selector_table)
    levels = generated_static_source_pyramid_levels_from_wallpaper(
        wallpaper,
        material=material,
    )
    config = struct.pack(
        CONFIG_FORMAT,
        MAGIC,
        1024,
        1024,
        0 if material == "clear" else 1,
        0 if appearance == "light" else 1,
        len(levels),
        tile_start,
        coefficients.shape[1],
        *slopes,
    )
    write_file(directory, "config.bin", config, files, role="independent-input")
    write_file(
        directory,
        "main-vertices.f32",
        geometry.main_vertices.astype("<f4", copy=False).tobytes(),
        files,
        role="independent-input",
    )
    write_file(
        directory,
        "shadow-vertices.f32",
        geometry.shadow_vertices.astype("<f4", copy=False).tobytes(),
        files,
        role="independent-input",
    )
    write_file(
        directory,
        "shadow-indices.u16",
        geometry.shadow_indices.astype("<u2", copy=False).tobytes(),
        files,
        role="independent-input",
    )
    write_file(
        directory,
        "highlight-vertices.f32",
        highlight.vertices.astype("<f4", copy=False).tobytes(),
        files,
        role="independent-input",
    )
    write_file(
        directory,
        "highlight-indices.u16",
        highlight.indices.astype("<u2", copy=False).tobytes(),
        files,
        role="independent-input",
    )
    write_file(
        directory,
        "profile.bin",
        profile,
        files,
        role="independent-input",
    )
    write_file(
        directory,
        "highlight-uniform.bin",
        highlight.uniform_payload,
        files,
        role="independent-input",
    )
    write_file(
        directory,
        "interpolant-coefficients.rgba32ui",
        coefficients.astype("<u4", copy=False).tobytes(),
        files,
        role="independent-input",
    )

    destination_rgba = np.ascontiguousarray(
        np.flipud(destination_bgra[..., [2, 1, 0, 3]])
    )
    write_file(
        directory,
        "destination.rgba8",
        destination_rgba.tobytes(),
        files,
        role="independent-input",
    )
    for level, (width, height, raw) in levels.items():
        bgra = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
        rgba = np.ascontiguousarray(bgra[..., [2, 1, 0, 3]])
        write_file(
            directory,
            f"source-mip-{level}.rgba8",
            rgba.tobytes(),
            files,
            role="independent-input",
        )

    reference_top_left = bgra_raw(
        Path(getattr(fixture, "path")) / "carenderer-live-tree-bgra8.raw",
        width=1024,
        height=1024,
    )
    reference_bottom_left = np.ascontiguousarray(np.flipud(reference_top_left))
    write_file(
        directory,
        "reference-bottom-left.rgba8",
        reference_bottom_left.tobytes(),
        files,
        role="captured-comparison-oracle-only",
    )
    manifest = {
        "schemaVersion": 1,
        "name": name,
        "material": material,
        "appearance": appearance,
        "renderInputsCaptured": False,
        "capturedFinalOutputUsedForComparisonOnly": True,
        "files": files,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)
    wallpaper = coordinate_hash_wallpaper_rgba()
    destination = coordinate_hash_prepass_bgra()
    selector_table = raster_arithmetic.load_selector_table()
    manifests = [
        generate_fixture(
            arguments.output,
            fixture=fixture,
            wallpaper=wallpaper,
            destination_bgra=destination,
            selector_table=selector_table,
        )
        for fixture in FIXTURES
    ]
    manifest = {
        "schemaVersion": 1,
        "fixtures": manifests,
    }
    encoded = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    (arguments.output / "manifest.json").write_text(
        encoded,
        encoding="utf-8",
    )
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
