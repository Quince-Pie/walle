#!/usr/bin/env python3
"""Decode and verify Apple's live Liquid Glass transition uniforms."""

import argparse
import hashlib
import json
import math
import platform
import re
import struct
from pathlib import Path
from typing import Any

from liquid_glass_profile_matrix import (
    FIELD_SPECS,
    GLASS_FRAGMENTS,
    decode_profile,
)
from liquid_glass_transition_matrix import (
    expected_matrix_field_bits,
)


type JsonObject = dict[str, Any]

REPORT_NAME = "transition-timeline.json"
EXPECTED_SAMPLE_INDICES = (1, 4, 8, 12, 16, 20, 24, 28, 32)
EXPECTED_PROFILES = {
    ("clear", "dark"),
    ("clear", "light"),
    ("regular", "dark"),
    ("regular", "light"),
}
UNRESOLVED_FIELDS: set[str] = set()
NUMBER = re.compile(
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?",
    re.IGNORECASE,
)


def float32_bits(value: float) -> str:
    return (
        f"0x{struct.unpack('<I', struct.pack('<f', value))[0]:08x}"
    )


def float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def half_bits(value: float) -> str:
    return (
        f"0x{struct.unpack('<H', struct.pack('<e', value))[0]:04x}"
    )


def reciprocal(value: float) -> float:
    return math.inf if value == 0 else 1.0 / value


def _numeric(values: JsonObject, key: str) -> float:
    value = values.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"filter input {key} is not numeric: {value!r}")
    return float(value)


def _boolean(values: JsonObject, key: str) -> bool:
    value = values.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"filter input {key} is not boolean: {value!r}")
    return value


def _color_alpha(values: JsonObject, key: str) -> float:
    value = values.get(key)
    if value is None:
        return 0.0
    if not isinstance(value, dict):
        raise ValueError(f"filter input {key} is not a color: {value!r}")
    alpha = value.get("alpha")
    if not isinstance(alpha, (int, float)) or isinstance(alpha, bool):
        raise ValueError(
            f"filter input {key} has no numeric alpha: {value!r}"
        )
    return float(alpha)


def _half_inputs(
    values: JsonObject,
    prefix: str,
    count: int,
) -> list[str]:
    return [
        half_bits(_numeric(values, f"{prefix}{index}"))
        for index in range(count)
    ]


def _shadow_offset(values: JsonObject) -> tuple[float, float]:
    value = values.get("inputShadowOffset")
    if not isinstance(value, dict):
        raise ValueError("inputShadowOffset is not serialized")
    description = value.get("description")
    if not isinstance(description, str):
        raise ValueError("inputShadowOffset has no description")
    components = [float(item) for item in NUMBER.findall(description)]
    if len(components) != 2:
        raise ValueError(
            f"inputShadowOffset description differs: {description!r}"
        )
    return components[0], components[1]


def expected_field_bits(
    material: str,
    appearance: str,
    values: JsonObject,
) -> dict[str, list[str]]:
    """Return bit-exact rules that do not require color-matrix recovery."""

    direct_float = {
        "inner_refraction_amount": "inputInnerRefractionAmount",
        "outer_refraction_amount": "inputOuterRefractionAmount",
        "refraction_threshold_0": "inputRefractionDistance0",
        "refraction_threshold_1": "inputRefractionDistance1",
        "edge_bleed_amount": "inputBleedAmount",
        "shadow_amount": "inputShadowAmount",
        "shadow_contribution": "inputShadowVibrancyContribution",
    }
    inverse_float = {
        "inner_refraction_inverse_height":
            "inputInnerRefractionHeight",
        "outer_refraction_inverse_height":
            "inputOuterRefractionHeight",
        "edge_bleed_inverse_height": "inputBleedHeight",
        "shadow_inverse_height": "inputShadowHeight",
        "shadow_inverse_radius": "inputShadowRadius",
    }
    expected = {
        field: [float32_bits(_numeric(values, key))]
        for field, key in direct_float.items()
    }
    expected.update({
        field: [
            float32_bits(reciprocal(_numeric(values, key)))
        ]
        for field, key in inverse_float.items()
    })

    blur_scale = 0.8 if material == "clear" else 0.4
    expected["blur_radius"] = [
        float32_bits(
            float32(blur_scale)
            * float32(_numeric(values, "inputBlurRadius"))
        )
    ]
    expected["edge_bleed_blur_radius"] = [
        float32_bits(
            float32(0.2)
            * float32(_numeric(values, "inputBleedBlurRadius"))
        )
    ]
    expected["shadow_blur_radius"] = [
        float32_bits(
            float32(0.2)
            * float32(_numeric(values, "inputShadowBlurRadius"))
        )
    ]
    offset_x, offset_y = _shadow_offset(values)
    expected["shadow_offset"] = [
        float32_bits(offset_x),
        float32_bits(-offset_y),
    ]

    blur_opacity = [
        _numeric(values, f"inputBlurOpacity{index}")
        for index in range(4)
    ]
    expected["blur_alpha"] = [
        half_bits(blur_opacity[0]),
        half_bits(blur_opacity[0] - blur_opacity[1]),
        half_bits(blur_opacity[1] - blur_opacity[2]),
        half_bits(blur_opacity[2] - blur_opacity[3]),
    ]
    expected["blur_distance"] = _half_inputs(
        values,
        "inputBlurDistance",
        4,
    )
    expected["edge_bleed_distance"] = _half_inputs(
        values,
        "inputBleedDistance",
        2,
    )

    direct_half = {
        "edge_bleed_opacity": "inputBleedOpacity",
        "face_opacity": "inputFaceOpacity",
        "shadow_distance_offset": "inputShadowDistanceOffset",
        "shadow_opacity": "inputShadowOpacity",
        "refraction_opacity": "inputRefractionOpacity",
        "sdr_white_value": "inputSDRHoldingToneWhite",
    }
    expected.update({
        field: [half_bits(_numeric(values, key))]
        for field, key in direct_half.items()
    })
    expected["holding_tone_opacity"] = [
        half_bits(
            1.0
            if _boolean(values, "inputSDRHoldingToneEnabled")
            else 0.0
        )
    ]
    expected["sdr_shadow_distance"] = [
        half_bits(_numeric(values, "inputSDRGradientDistance0")),
        half_bits(_numeric(values, "inputSDRGradientDistance1")),
    ]
    clamp_delta = (
        0.15625
        if material == "clear"
        else 0.03125
        if appearance == "light"
        else 0.0
    )
    expected["clamp_limit"] = [half_bits(
        float32(
            1.0
            + float32(clamp_delta)
            * float32(_numeric(values, "inputFaceOpacity"))
        )
    )]
    expected["preserve_hue"] = [
        half_bits(
            1.0
            if _boolean(values, "inputClampPreserveHue")
            else 0.0
        )
    ]
    expected["bleed_darken"] = (
        [half_bits(1.0), half_bits(0.0)]
        if _boolean(values, "inputBleedDarkenBlend")
        else [half_bits(-1.0), half_bits(1.0)]
    )
    expected["float_mix_workaround"] = [half_bits(0.0)]
    expected["complex_refraction"] = [half_bits(1.0)]
    expected["shadow_face_opacity"] = [float32_bits(
        _color_alpha(values, "inputShadowColorMatrixFillColor")
        + _numeric(values, "inputSDRShadowOpacity")
    )]
    return expected


def _variable_blur_texture_extent(
    render: JsonObject,
) -> tuple[int, int]:
    provenance = render.get("metalCommandProvenance")
    if not isinstance(provenance, dict):
        raise ValueError("render has no Metal command provenance")
    records = provenance.get("records")
    if not isinstance(records, list):
        raise ValueError("render has no Metal command records")
    extents: list[tuple[int, int]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        pipeline = record.get("pipeline")
        texture = record.get("texture")
        if not isinstance(pipeline, dict) or not isinstance(
            texture,
            dict,
        ):
            continue
        if (
            record.get("kind") != "texture"
            or record.get("stage") != "compute"
            or record.get("index") != 1
            or pipeline.get("label")
                != "com.apple.coreanimation."
                   "variable_blur_copy_base_mip_compute"
        ):
            continue
        width = texture.get("width")
        height = texture.get("height")
        if (
            type(width) is not int
            or type(height) is not int
            or width <= 0
            or height <= 0
        ):
            raise ValueError(
                "variable-blur destination extent is invalid"
            )
        extents.append((width, height))
    if len(extents) != 1:
        raise ValueError(
            "render has "
            f"{len(extents)} variable-blur destination extents"
        )
    return extents[0]


def expected_geometry_field_bits(
    material: str,
    geometry: JsonObject,
    render: JsonObject,
) -> dict[str, list[str]]:
    width = geometry.get("width")
    height = geometry.get("height")
    if (
        not isinstance(width, (int, float))
        or isinstance(width, bool)
        or not isinstance(height, (int, float))
        or isinstance(height, bool)
        or width <= 0
        or height <= 0
    ):
        raise ValueError("transition geometry extent is invalid")
    half_width = 0.5 * float(width)
    half_height = 0.5 * float(height)
    texture_width, texture_height = _variable_blur_texture_extent(
        render
    )
    texture_virtualization = 2 if material == "clear" else 4
    return {
        "sdf_arg": [
            float32_bits(half_width),
            float32_bits(half_height),
            float32_bits(4.0),
            float32_bits(0.0 if material == "clear" else 0.5),
        ],
        "sdf_transform": [
            float32_bits(1.0),
            float32_bits(0.0),
            float32_bits(0.0),
            float32_bits(1.0),
        ],
        "sdf_arg2": [
            float32_bits(1.0),
            float32_bits(1.0),
            float32_bits(min(half_width, half_height)),
            float32_bits(0.0),
        ],
        "displacement_matrix": [
            float32_bits(
                1.0 / (texture_virtualization * texture_width)
            ),
            float32_bits(0.0),
            float32_bits(0.0),
            float32_bits(
                -1.0 / (texture_virtualization * texture_height)
            ),
        ],
    }


def _fragment(binding: JsonObject) -> str:
    pipeline = binding.get("pipeline", {})
    descriptor = (
        pipeline.get("creationDescriptor", {})
        if isinstance(pipeline, dict)
        else {}
    )
    return (
        str(descriptor.get("fragmentFunction", ""))
        if isinstance(descriptor, dict)
        else ""
    )


def _payload(binding: JsonObject) -> bytes:
    payload = binding.get("payload", {})
    encoded = payload.get("hex") if isinstance(payload, dict) else None
    if not isinstance(encoded, str):
        raise ValueError("uniform binding has no hexadecimal payload")
    decoded = bytes.fromhex(encoded)
    if payload.get("lengthBytes") != len(decoded):
        raise ValueError("uniform binding payload length differs")
    return decoded


def _report_paths(root: Path) -> list[Path]:
    direct = root / REPORT_NAME
    if direct.is_file():
        return [direct]
    return sorted(root.glob(f"*/{REPORT_NAME}"))


def _static_profiles(path: Path | None) -> dict[tuple[str, str], str]:
    if path is None:
        return {}
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        (
            str(profile["material"]),
            str(profile["requestedAppearance"]),
        ): str(profile["profile"]["glassHex"])
        for profile in report.get("profiles", [])
    }


def analyze(
    root: Path,
    *,
    static_profile_matrix: Path | None = None,
) -> JsonObject:
    root = root.resolve()
    reports = []
    for path in _report_paths(root):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("schemaVersion") != 5:
            raise ValueError(f"{path}: transition schema is not 5")
        uniforms = report.get("dynamicBackgroundUniforms", {})
        if uniforms.get("requested") is True:
            reports.append((path, report, uniforms))
    if len(reports) != 4:
        raise ValueError(
            f"{root} has {len(reports)} dynamic uniform reports; expected 4"
        )

    static_profiles = _static_profiles(static_profile_matrix)
    observed_profiles: set[tuple[str, str]] = set()
    analyzed_profiles: list[JsonObject] = []
    formula_checks = 0
    formula_matches = 0
    all_draw_bodies_equal = True
    all_endpoints_static_exact = bool(static_profiles)

    for path, report, uniforms in reports:
        material = str(report.get("material"))
        appearance = str(report.get("appearance"))
        geometry = report.get("geometry")
        if not isinstance(geometry, dict):
            raise ValueError(f"{path}: transition geometry is missing")
        profile_key = (material, appearance)
        if profile_key in observed_profiles:
            raise ValueError(f"duplicate dynamic profile: {profile_key}")
        observed_profiles.add(profile_key)
        expected_fragment = GLASS_FRAGMENTS.get(material)
        records = uniforms.get("records")
        if (
            uniforms.get("executed") is not True
            or uniforms.get("presentationLayerReplayed") is not False
            or uniforms.get("sampleIndices")
                != list(EXPECTED_SAMPLE_INDICES)
            or not isinstance(records, list)
            or len(records) != len(EXPECTED_SAMPLE_INDICES)
        ):
            raise ValueError(f"{path}: dynamic uniform evidence is incomplete")

        states: list[JsonObject] = []
        for expected_index, record in zip(
            EXPECTED_SAMPLE_INDICES,
            records,
            strict=True,
        ):
            if record.get("sampleIndex") != expected_index:
                raise ValueError(f"{path}: uniform sample order differs")
            values = record.get("filter", {}).get("inputValues")
            render = record.get("render")
            if not isinstance(values, dict) or not isinstance(render, dict):
                raise ValueError(f"{path}: uniform state is incomplete")
            bindings = [
                binding
                for binding in render.get(
                    "glassFragmentUniformBindings",
                    [],
                )
                if isinstance(binding, dict)
                and _fragment(binding) == expected_fragment
            ]
            if len(bindings) != 2:
                raise ValueError(
                    f"{path}: sample {expected_index} has "
                    f"{len(bindings)} background bindings"
                )
            decoded = [
                decode_profile(_payload(binding))
                for binding in bindings
            ]
            draw_bodies_equal = (
                decoded[0]["glassHex"] == decoded[1]["glassHex"]
            )
            all_draw_bodies_equal &= draw_bodies_equal
            expected_bits = expected_field_bits(
                material,
                appearance,
                values,
            )
            expected_bits.update(
                expected_matrix_field_bits(values)
            )
            expected_bits.update(expected_geometry_field_bits(
                material,
                geometry,
                render,
            ))
            checks = {
                name: decoded[0]["fields"][name]["bits"] == bits
                for name, bits in expected_bits.items()
            }
            formula_checks += len(checks)
            formula_matches += sum(checks.values())
            states.append({
                "sampleIndex": expected_index,
                "requestedProgress": record.get("requestedProgress"),
                "remaining": record.get("remaining"),
                "remainingMatchesFilterFaceOpacity":
                    record.get("remaining")
                    == values.get("inputFaceOpacity"),
                "fragmentFunction": expected_fragment,
                "drawUniformBodiesEqual": draw_bodies_equal,
                "renderDurationSeconds":
                    render.get("durationSeconds"),
                "glassSha256": decoded[0]["glassSha256"],
                "formulaChecks": checks,
                "expectedFormulaBits": expected_bits,
                "filterInputs": values,
                "profile": decoded[0],
            })

        endpoint = states[-1]
        static_hex = static_profiles.get(profile_key)
        endpoint_static_exact = (
            static_hex is not None
            and endpoint["profile"]["glassHex"] == static_hex
        )
        all_endpoints_static_exact &= endpoint_static_exact
        analyzed_profiles.append({
            "artifact": str(path.parent),
            "timelineJsonSha256": hashlib.sha256(
                path.read_bytes()
            ).hexdigest(),
            "material": material,
            "appearance": appearance,
            "method": uniforms.get("method"),
            "modelTargetPath": uniforms.get("modelTargetPath"),
            "states": states,
            "endpointMatchesIndependentStaticProfile":
                endpoint_static_exact,
        })

    analyzed_profiles.sort(
        key=lambda value: (
            str(value["material"]),
            str(value["appearance"]),
        )
    )
    mapped_fields = {
        name
        for profile in analyzed_profiles
        for state in profile["states"]
        for name in state["formulaChecks"]
    }
    all_fields = {name for name, _, _ in FIELD_SPECS}
    return {
        "schemaVersion": 1,
        "analysis": "private-liquid-glass-transition-uniform-law",
        "artifact": str(root),
        "implementation": {
            "file": "analysis/liquid_glass_transition_uniforms.py",
            "python": platform.python_version(),
        },
        "integrity": {
            "dynamicReports": len(reports),
            "profiles": len(analyzed_profiles),
            "states": sum(
                len(profile["states"])
                for profile in analyzed_profiles
            ),
            "draws": sum(
                2 * len(profile["states"])
                for profile in analyzed_profiles
            ),
            "completeFourProfileMatrix":
                observed_profiles == EXPECTED_PROFILES,
            "presentationLayerReplayed": False,
            "mainAndShadowGlassBodiesEqual":
                all_draw_bodies_equal,
            "endpointGlassBodiesMatchIndependentStaticCaptures":
                all_endpoints_static_exact,
        },
        "fieldLawCoverage": {
            "totalFields": len(all_fields),
            "bitExactMappedFields": len(mapped_fields),
            "mappedFields": sorted(mapped_fields),
            "remainingFields": sorted(all_fields - mapped_fields),
            "expectedRemainingFields": sorted(UNRESOLVED_FIELDS),
            "formulaChecks": formula_checks,
            "formulaMatches": formula_matches,
            "allMappedFieldBitsExact":
                formula_matches == formula_checks,
        },
        "keyFindings": {
            "clampLimit":
                "binary16(1 + endpointDelta*inputFaceOpacity), "
                "where endpointDelta is 0.15625 for clear, "
                "0.03125 for regular-light, and 0 for "
                "regular-dark; not inputClamp",
            "blurRadius":
                "float32(0.8*inputBlurRadius) for clear; "
                "float32(0.4*inputBlurRadius) for regular",
            "blurAlpha":
                "binary16([a0, a0-a1, a1-a2, a2-a3])",
            "bleedDarken":
                "true -> half2(1,0); false -> half2(-1,1)",
            "sdfGeometry":
                "sdf half-extents come from the requested geometry; "
                "the SDF transform is identity",
            "displacementMatrix":
                "float32 reciprocal variable-blur texture extent, "
                "virtualized by 2x for clear and 4x for regular",
            "shadowFaceOpacity":
                "float32(shadow fill alpha + inputSDRShadowOpacity)",
            "colorMatrices":
                "captured QuartzCore RGB↔luma/chroma operands; "
                "three scalar float32 4x5 concatenations in ARM "
                "FMA order; source-over fill; binary16 FCVTN packing",
        },
        "profiles": analyzed_profiles,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifact",
        type=Path,
        help="downloaded schema-5 transition artifact directory",
    )
    parser.add_argument(
        "--static-profile-matrix",
        type=Path,
        help="independent static profile analysis for endpoint matching",
    )
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(
        arguments.artifact,
        static_profile_matrix=arguments.static_profile_matrix,
    )
    encoded = json.dumps(
        report,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    checks = report["fieldLawCoverage"]
    integrity = report["integrity"]
    return 0 if (
        checks["allMappedFieldBitsExact"]
        and checks["remainingFields"] == checks["expectedRemainingFields"]
        and integrity["completeFourProfileMatrix"]
        and integrity["mainAndShadowGlassBodiesEqual"]
        and integrity[
            "endpointGlassBodiesMatchIndependentStaticCaptures"
        ]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
