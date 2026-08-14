#!/usr/bin/env python3
"""Run the recovered Apple shader against immutable captured inputs.

This gate deliberately does not render Walle's production shader.  Its only
authority is the captured-input reference domain described in ``SCOPE``.
"""

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "analysis"
LG_ANALYSIS = ROOT / "lg-test" / "Analysis"
for module_path in (ANALYSIS, LG_ANALYSIS):
    if str(module_path) not in sys.path:
        sys.path.insert(0, str(module_path))

from apple_glass_reference_renderer import (  # noqa: E402
    AppleGlassReferenceRenderer,
    bgra_raw,
    compare_images,
)
from liquid_glass_runtime_raster_coefficients import (  # noqa: E402
    RuntimeQuad,
    coefficient_table,
    runtime_quad,
    slopes_bits,
)
from liquid_glass_independent_mips import (  # noqa: E402
    generated_copy_and_mips_from_producer,
    generated_static_source_pyramid,
    generated_source_mip_overrides,
)
from liquid_glass_shader_specialization import (  # noqa: E402
    load_specialized_exact_final_shader,
)
import raster_tile_selector_model as raster_arithmetic  # noqa: E402


type JsonObject = dict[str, Any]

CAPTURE_ROOT = Path(
    "artifacts/liquid-glass-introspection-30575220842"
)
INTRINSIC_TABLE = Path("artifacts/apple-float-intrinsics-r8-30556057571.bin")
PRODUCTION_SHADER = Path("shaders/frag.glsl")
REFERENCE_SHADER = Path("analysis/apple_glass_reference.frag.glsl")
EXPECTED_PRODUCTION_SHADER_SHA256 = (
    "6489828f12de599da9633d6183266a81b71ed846a1b03c03cb4eb9c23639352d"
)

EXPECTED_SOURCE_SHA256 = {
    "analysis/apple_glass_reference.frag.glsl": (
        "e55157fce1bbb96282e8b34d5f89e9f4b4cc22e2aed7ef171d4a870b9ab09610"
    ),
    "analysis/apple_glass_reference.vert.glsl": (
        "99d6942f6b39b52460c23b4e52c498f2d98a03cb6ffc32d87ef6e94c43e7a958"
    ),
    "analysis/apple_glass_reference_renderer.py": (
        "cc60beb7fe2654113d9c695f561f3db6d930e862b555fb568d9f4885910e8631"
    ),
    "analysis/liquid_glass_backdrop_pyramid.py": (
        "f67be9eb8eadc7633c5092a6e9ea3664e41089403ce420420db9dd3ff05bd558"
    ),
    "analysis/liquid_glass_profile_matrix.py": (
        "f8e3c1889b999f29b6cc09a50aa1e4f8881414af2560b2f06dc2b50bff858611"
    ),
    "analysis/liquid_glass_raster_interpolant.py": (
        "44976f1eea71a67a9843c42885fb600b90bbe42447b0864d74dffd1898c28d61"
    ),
    "analysis/liquid_glass_geometry_transfer.py": (
        "622f6bfa71bebc2255b3ebd6713498878a74394001ef176a81368414cef4c84f"
    ),
    "analysis/liquid_glass_independent_mips.py": (
        "932cf6a8ff261eb15a414cabf22468b5c459b755468bc863d8e1ae31dcdf52ab"
    ),
    "analysis/liquid_glass_runtime_raster_coefficients.py": (
        "f9c1759a0e75ad527754f2d43ab1ace5c9e9077632fd4cd30d13a0341270d489"
    ),
    "analysis/liquid_glass_shader_specialization.py": (
        "5826d850b869a30f19462240355520be3996dafe0fda4d240f2cb2a91bb0241f"
    ),
    "analysis/liquid_glass_static_profile.py": (
        "bd145eac6b0337825a725b79daa91d31175df6ec4a3fedfb617b277dbb77b9b8"
    ),
    "analysis/liquid_glass_static_geometry.py": (
        "13165815a355b6883a9edbd821d195482f624a888786afeda061b1e9e4be9857"
    ),
    "analysis/liquid_glass_static_background.py": (
        "29a5c43ccf9d7371ca8d609c5814cf904964b7e7b363f69c66840d75f48b4cdd"
    ),
    "analysis/liquid_glass_static_highlight.py": (
        "0da14294a3a474e27dc64977badb87031acb77dfd59f7ce78a9b5cbfc7e1e259"
    ),
    "lg-test/Analysis/raster_tile_coefficient_model.py": (
        "69b8546e26dbd17009260621b1a72610e9c25efee21e68022b07c9f00c599248"
    ),
    "lg-test/Analysis/raster_tile_coefficient_model_v3.py": (
        "99c1725d9fdec0877b8510fb92aaa4a4ee398e61b0c579f0b1c1a0471520f1fe"
    ),
    "lg-test/Analysis/raster_tile_iterator_model.py": (
        "bf0b926e759f9234d924ccd82654abc07ad8bb9ff10c9058ba5a284886dd2429"
    ),
    "lg-test/Analysis/raster_tile_selector_model.py": (
        "1874d8c452dce244bd8ae2cb7fbf05d0d454a815c0c37c285e1c377524c9369d"
    ),
    "lg-test/Analysis/raster_tile_selector_model_v4.py": (
        "f1918f51974f486510ce5d68724cff015bb2e7f618a1153016d464cd9240a103"
    ),
    "parity/liquid_glass_static_profile.c": (
        "f871cac9ff04614344da9625e75c9e72a77e9b27b3c2bb09471f39b64542f340"
    ),
    "parity/liquid_glass_static_profile.h": (
        "70635f2cba97b9ebaf6f609129a0b60b17bed0b5287900ff5f1f658e886ee172"
    ),
    str(INTRINSIC_TABLE): (
        "fff71cc0d4428677ca5bc58b91212a7166b701e4efe504c3d71cab70846d0449"
    ),
}

SCOPE: JsonObject = {
    "capturedInputReferenceRenderer": True,
    "capturedPrivateUniforms": True,
    "capturedBackdropMips": True,
    "generatedRasterCoefficients": True,
    "productionWalleShaderRendered": False,
    "independentOpticalInputs": False,
    "physicalRetinaOutput": False,
    "formalLiquidGlassParity": False,
}


@dataclass(frozen=True, slots=True)
class Fixture:
    name: str
    directory: str
    tree_sha256: str
    file_count: int
    byte_count: int

    @property
    def path(self) -> Path:
        return CAPTURE_ROOT / self.directory


FIXTURES = (
    Fixture(
        "clear-light",
        "liquid-glass-introspection-clear-light-30575220842",
        "8878b38899e772781f1e45504de4db16ac5d234b892f53af5f66175043c96c59",
        426,
        918_562_768,
    ),
    Fixture(
        "clear-dark",
        "liquid-glass-introspection-clear-dark-30575220842",
        "190a347558b2bb4acb9521b5f90a69ea065bc285d6f87b67584c87be2ca54f9c",
        426,
        918_569_737,
    ),
    Fixture(
        "regular-light",
        "liquid-glass-introspection-regular-light-30575220842",
        "85d1ae00b6dccba3467fe34a27386defeeacdf1f193953805bb1b9d3e5e27f9a",
        471,
        953_694_227,
    ),
    Fixture(
        "regular-dark",
        "liquid-glass-introspection-regular-dark-30575220842",
        "06e67eea120d9aa06aa911d973841c4029b2f9cf3a4953556d30c83e4e9d756f",
        471,
        953_708_560,
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def fixture_manifest(fixture: Fixture) -> JsonObject:
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    for path in sorted(candidate for candidate in fixture.path.rglob("*") if candidate.is_file()):
        relative = path.relative_to(fixture.path).as_posix()
        size = path.stat().st_size
        digest.update(f"{sha256_file(path)}  ./{relative}\n".encode())
        file_count += 1
        byte_count += size
    return {
        "sha256": digest.hexdigest(),
        "fileCount": file_count,
        "byteCount": byte_count,
    }


def validate_provenance() -> JsonObject:
    sources: JsonObject = {}
    for relative, expected in EXPECTED_SOURCE_SHA256.items():
        actual = sha256_file(ROOT / relative)
        if actual != expected:
            raise ValueError(
                f"immutable oracle source drifted: {relative}: {actual} != {expected}"
            )
        sources[relative] = actual

    production_sha256 = sha256_file(ROOT / PRODUCTION_SHADER)
    if production_sha256 != EXPECTED_PRODUCTION_SHADER_SHA256:
        raise ValueError(
            "protected production shader drifted: "
            f"{production_sha256} != {EXPECTED_PRODUCTION_SHADER_SHA256}"
        )

    fixtures: JsonObject = {}
    for fixture in FIXTURES:
        manifest = fixture_manifest(fixture)
        expected = {
            "sha256": fixture.tree_sha256,
            "fileCount": fixture.file_count,
            "byteCount": fixture.byte_count,
        }
        if manifest != expected:
            raise ValueError(
                f"immutable fixture drifted: {fixture.name}: {manifest} != {expected}"
            )
        fixtures[fixture.name] = manifest

    return {
        "oracleSources": sources,
        "fixtures": fixtures,
        "productionShader": {
            "path": str(PRODUCTION_SHADER),
            "sha256": production_sha256,
            "renderedByThisGate": False,
        },
    }


def configure_exact_highlight(renderer: AppleGlassReferenceRenderer) -> None:
    values = {
        "HighlightCoordinateMode": 0,
        "HighlightCoverageArithmeticMode": 1,
        "HighlightDerivativeMode": 1,
        "HighlightFloatDivisionMode": 3,
        "HighlightNormalizeMode": 1,
        "HighlightSourceConstructionMode": 1,
        "HighlightSourceDivisionMode": 0,
        "HighlightVibrantArithmeticMode": 9,
    }
    for name, value in values.items():
        renderer.program[name].value = value


def render_fixture(
    fixture: Fixture,
    *,
    fragment_shader_source: str,
    selector_table: tuple[int, ...],
    source_mip_overrides: dict[int, bytes] | None = None,
    renderer_arguments: dict[str, object] | None = None,
    runtime_quad_override: RuntimeQuad | None = None,
    final_highlight_payload: bytes | None = None,
) -> JsonObject:
    capture = fixture.path
    quad = runtime_quad_override or runtime_quad(capture)
    tile_start, coefficients = coefficient_table(
        quad,
        selector_table=selector_table,
    )
    slopes = slopes_bits(quad, selector_table)
    reference = bgra_raw(
        capture / "carenderer-live-tree-bgra8.raw",
        width=1024,
        height=1024,
    )

    options: dict[str, object] = {
        "fragment_shader_source": fragment_shader_source,
        "intrinsic_table": ROOT / INTRINSIC_TABLE,
        "interpolant_coefficient_data": coefficients,
        "interpolant_tile_start": tile_start,
        "interpolant_slope_bits": slopes,
        "source_mip_bgra_overrides": source_mip_overrides,
        "load_interpolant_trace": False,
        "load_interpolant_axis_trace": False,
        "load_diagnostic_traces": False,
    }
    options.update(renderer_arguments or {})
    with AppleGlassReferenceRenderer(capture, **options) as renderer:
        configure_exact_highlight(renderer)
        candidate = renderer.render_complete(
            final_highlight_payload=final_highlight_payload
        )

    comparison = compare_images(reference, candidate)
    return {
        "name": fixture.name,
        "width": 1024,
        "height": 1024,
        "checkedPixels": 1024 * 1024,
        "checkedBytes": 1024 * 1024 * 4,
        "comparison": comparison.as_json(),
    }


def run_oracle(
    *,
    independent_mips: bool = False,
    independent_copy_mips: bool = False,
    independent_static_pyramid: bool = False,
) -> JsonObject:
    if sum((independent_mips, independent_copy_mips, independent_static_pyramid)) > 1:
        raise ValueError("independent input modes are mutually exclusive")
    provenance = validate_provenance()
    shader_source = load_specialized_exact_final_shader(ROOT / REFERENCE_SHADER)
    shader_sha256 = hashlib.sha256(shader_source.encode()).hexdigest()
    selector_table = raster_arithmetic.load_selector_table()
    fixture_results = [
        render_fixture(
            fixture,
            fragment_shader_source=shader_source,
            selector_table=selector_table,
            source_mip_overrides=(
                generated_static_source_pyramid(fixture.path)
                if independent_static_pyramid
                else generated_copy_and_mips_from_producer(fixture.path)
                if independent_copy_mips
                else generated_source_mip_overrides(fixture.path)
                if independent_mips
                else None
            ),
        )
        for fixture in FIXTURES
    ]
    exact = all(result["comparison"]["exact"] for result in fixture_results)
    report = {
        "schemaVersion": 1,
        "scope": SCOPE,
        "provenance": provenance,
        "testedRenderer": {
            "kind": "captured-input-reference-oracle",
            "fragmentShaderPath": str(REFERENCE_SHADER),
            "specializedFragmentShaderSha256": shader_sha256,
        },
        "fixtures": fixture_results,
        "totals": {
            "checkedPixels": sum(result["checkedPixels"] for result in fixture_results),
            "checkedBytes": sum(result["checkedBytes"] for result in fixture_results),
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
            "capturedInputReferenceExact": exact,
            "productionWalleParityEstablished": False,
        },
    }
    if independent_mips:
        report["scope"] = {
            **SCOPE,
            "capturedBackdropMips": False,
            "capturedBackdropBaseLevel": True,
            "independentlyGeneratedBackdropMipLevels": True,
        }
        report["testedRenderer"]["inputMode"] = (
            "captured-mip-zero-generated-descendants"
        )
        report["gate"]["independentMipSubstitutionExact"] = exact
    elif independent_copy_mips:
        report["scope"] = {
            **SCOPE,
            "capturedBackdropMips": False,
            "capturedBackdropProducer": True,
            "independentlyGeneratedCopyAndMipLevels": True,
        }
        report["testedRenderer"]["inputMode"] = (
            "captured-producer-generated-copy-and-mips"
        )
        report["gate"]["independentCopyAndMipsSubstitutionExact"] = exact
    elif independent_static_pyramid:
        report["scope"] = {
            **SCOPE,
            "capturedBackdropMips": False,
            "capturedBackdropBaseLevel": False,
            "capturedWallpaperSource": True,
            "independentlyGeneratedBackdropPyramid": True,
        }
        report["testedRenderer"]["inputMode"] = (
            "captured-wallpaper-generated-static-pyramid"
        )
        report["gate"]["independentStaticPyramidSubstitutionExact"] = exact
    return report


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--independent-mips", action="store_true")
    parser.add_argument("--independent-copy-mips", action="store_true")
    parser.add_argument("--independent-static-pyramid", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = run_oracle(
        independent_mips=arguments.independent_mips,
        independent_copy_mips=arguments.independent_copy_mips,
        independent_static_pyramid=arguments.independent_static_pyramid,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(encoded, end="")
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0 if report["gate"]["capturedInputReferenceExact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
