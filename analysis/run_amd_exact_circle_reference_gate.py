#!/usr/bin/env python3
"""Gate Walle's AMD circle specialization against complete Apple frames.

This is deliberately narrower than a production-Walle gate. It independently
constructs the static wallpaper, backdrop pyramid, profile, pass geometry,
destination prepass, and final-highlight inputs. It retains no captured render
input, but it still renders outside Walle's production process and display.
"""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import moderngl
import raster_tile_selector_model as raster_arithmetic

from apple_glass_reference_renderer import DrawGeometry
from liquid_glass_independent_mips import (
    generated_static_source_pyramid_levels_from_wallpaper,
)
from liquid_glass_runtime_raster_coefficients import runtime_quad_from_vertices
from liquid_glass_shader_specialization import (
    load_amd_exact_circle_shader,
    load_amd_gles_compatible_circle_shader,
)
from liquid_glass_static_geometry import (
    build_static_circle_geometry,
    canonical_static_circle_geometry_request,
)
from liquid_glass_static_background import (
    coordinate_hash_prepass_bgra,
    coordinate_hash_wallpaper_rgba,
)
from liquid_glass_static_profile import (
    build_static_profile,
    canonical_static_profile_request,
)
from liquid_glass_static_highlight import build_static_highlight
from run_captured_input_reference_oracle import (
    FIXTURES,
    render_fixture,
    validate_provenance,
)


type JsonObject = dict[str, Any]

ROOT = Path(__file__).resolve().parent.parent

SCOPE: JsonObject = {
    "amdCircleSpecializationRendered": True,
    "independentlyGeneratedBackdropPyramid": True,
    "independentlyGeneratedStaticProfilePayloads": True,
    "capturedPrivateProfilePayloads": False,
    "independentlyGeneratedStaticPassGeometry": True,
    "capturedPassGeometry": False,
    "independentlyGeneratedStaticWallpaper": True,
    "independentlyGeneratedDestinationPrepass": True,
    "capturedDestinationPrepass": False,
    "independentlyGeneratedFinalHighlightInputs": True,
    "capturedFinalHighlightInputs": False,
    "productionWalleProcessRendered": False,
    "productionWalleDisplayContextUsed": False,
    "physicalRetinaOutput": False,
    "formalLiquidGlassParity": False,
}


def graphics_device() -> JsonObject:
    context = moderngl.create_standalone_context(backend="egl")
    try:
        info = context.info
        return {
            "vendor": info.get("GL_VENDOR"),
            "renderer": info.get("GL_RENDERER"),
            "version": info.get("GL_VERSION"),
        }
    finally:
        context.release()


def run_gate(*, gles_compatible: bool = False) -> JsonObject:
    provenance = validate_provenance()
    device = graphics_device()
    renderer = str(device["renderer"])
    amd_radeonsi = device["vendor"] == "AMD" and "radeonsi" in renderer

    selector_table = raster_arithmetic.load_selector_table()
    shaders: JsonObject = {}
    fixture_results: list[JsonObject] = []
    wallpaper = coordinate_hash_wallpaper_rgba()
    destination = coordinate_hash_prepass_bgra()
    for fixture in FIXTURES:
        material = "clear" if fixture.name.startswith("clear-") else "regular"
        appearance = "dark" if fixture.name.endswith("-dark") else "light"
        shader_source = (
            load_amd_gles_compatible_circle_shader(material)
            if gles_compatible
            else load_amd_exact_circle_shader(material)
        )
        profile_payload = build_static_profile(
            canonical_static_profile_request(material, appearance)
        )
        geometry = build_static_circle_geometry(
            canonical_static_circle_geometry_request(material)
        )
        highlight = build_static_highlight(material, appearance)
        pyramid_levels = (
            generated_static_source_pyramid_levels_from_wallpaper(
                wallpaper,
                material=material,
            )
        )
        quad = runtime_quad_from_vertices(
            geometry.main_vertices,
            name=fixture.name,
        )
        shaders[material] = {
            "byteCount": len(shader_source.encode()),
            "sha256": hashlib.sha256(shader_source.encode()).hexdigest(),
        }
        fixture_result = render_fixture(
            fixture,
            fragment_shader_source=shader_source,
            selector_table=selector_table,
            renderer_arguments={
                "main_geometry": DrawGeometry(
                    vertices=geometry.main_vertices,
                    indices=None,
                ),
                "shadow_geometry": DrawGeometry(
                    vertices=geometry.shadow_vertices,
                    indices=geometry.shadow_indices,
                ),
                "profile_payload": profile_payload,
                "destination_bgra_data": destination,
                "source_mip_bgra_levels": pyramid_levels,
                "runtime_data": {},
                "final_highlight_geometry": DrawGeometry(
                    vertices=highlight.vertices,
                    indices=highlight.indices,
                ),
            },
            runtime_quad_override=quad,
            final_highlight_payload=highlight.uniform_payload,
        )
        fixture_result["profilePayload"] = {
            "source": "independent-static-profile-construction",
            "byteCount": len(profile_payload),
            "sha256": hashlib.sha256(profile_payload).hexdigest(),
        }
        fixture_result["passGeometry"] = {
            "source": "independent-static-circle-construction",
            "mainVertexComponentSha256": hashlib.sha256(
                geometry.main_vertices.tobytes()
            ).hexdigest(),
            "shadowVertexComponentSha256": hashlib.sha256(
                geometry.shadow_vertices.tobytes()
            ).hexdigest(),
            "shadowIndexSha256": hashlib.sha256(
                geometry.shadow_indices.tobytes()
            ).hexdigest(),
        }
        fixture_result["staticBackground"] = {
            "source": "independent-coordinate-hash-construction",
            "wallpaperRgbaSha256": hashlib.sha256(
                wallpaper.tobytes()
            ).hexdigest(),
            "destinationBgraSha256": hashlib.sha256(
                destination.tobytes()
            ).hexdigest(),
        }
        fixture_result["finalHighlight"] = {
            "source": "independent-static-highlight-construction",
            "vertexComponentSha256": hashlib.sha256(
                highlight.vertices.tobytes()
            ).hexdigest(),
            "indexSha256": hashlib.sha256(
                highlight.indices.tobytes()
            ).hexdigest(),
            "uniformPrefixByteCount": len(highlight.uniform_payload),
            "uniformPrefixSha256": hashlib.sha256(
                highlight.uniform_payload
            ).hexdigest(),
        }
        fixture_results.append(fixture_result)

    exact = all(
        result["comparison"]["exact"] for result in fixture_results
    )
    return {
        "schemaVersion": 1,
        "scope": SCOPE,
        "provenance": provenance,
        "device": device,
        "testedRenderer": {
            "kind": (
                "amd-gles-compatible-circle-reference-gate"
                if gles_compatible
                else "amd-circle-reference-gate"
            ),
            "shaders": shaders,
            "inputMode": (
                "independently-generated-complete-static-render-inputs"
            ),
        },
        "fixtures": fixture_results,
        "totals": {
            "checkedPixels": sum(
                result["checkedPixels"] for result in fixture_results
            ),
            "checkedBytes": sum(
                result["checkedBytes"] for result in fixture_results
            ),
            "mismatchedPixels": sum(
                result["comparison"]["mismatchedPixels"]
                for result in fixture_results
            ),
            "mismatchedBytes": sum(
                result["comparison"]["mismatchedBytes"]
                for result in fixture_results
            ),
        },
        "gate": {
            "amdRadeonsiDevice": amd_radeonsi,
            "amdCircleReferenceExact": exact,
            "observedDeviceAdmitted": amd_radeonsi and exact,
            "productionWalleParityEstablished": False,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--gles-compatible", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = run_gate(gles_compatible=arguments.gles_compatible)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(encoded, end="")
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    gate = report["gate"]
    return 0 if (
        gate["amdRadeonsiDevice"] and gate["amdCircleReferenceExact"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
