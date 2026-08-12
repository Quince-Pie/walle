#!/usr/bin/env python3
"""Verify Apple's captured matrix constructor and every packed result."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from liquid_glass_profile_matrix import decode_profile
from liquid_glass_transition_matrix import (
    APPLE_MATRIX_BASIS_BYTES,
    APPLE_MATRIX_BASIS_SHA256,
    MATRIX_FIELD_GROUPS,
    expected_matrix_field_bits,
)


type JsonObject = dict[str, Any]

REPORT_NAME = "transition-timeline.json"
RENDER_CALL_OFFSETS = (0x338, 0x3AC, 0x500)
BASIS_REFERENCES = (
    (0xB8, 0xBC, 2),
    (0xE0, 0xE4, 1),
)
CONCATENATION_CODE_RANGE = (0x1A4, 0x3EC)
CONCATENATION_CODE_SHA256 = (
    "5795b5d8d65446ad62571c36eb417dd94b7ec4acb9523fbcfcba6d31ff159300"
)
CRITICAL_CONSTRUCTOR_INSTRUCTIONS = {
    0x34: 0x1E21_3800,
    0x68: 0x1E22_C040,
    0x6C: 0x1E6C_1001,
    0x70: 0x1F41_8400,
    0x74: 0x1E62_4000,
    0xCC: 0x9400_0036,
    0xDC: 0x9400_0032,
    0xF0: 0x9400_002D,
    0xF8: 0xBD40_0E80,
    0x100: 0x1E20_3821,
    0x108: 0x4F81_9042,
    0x124: 0x1E22_2821,
    0x144: 0x1E21_2841,
    0x148: 0x1E20_2860,
}
EXPECTED_INTERVENTIONS = (
    "baseline-endpoint",
    "neutral-axes",
    "white-low",
    "white-high",
    "black-low",
    "black-high",
    "saturation-zero",
    "saturation-low",
    "saturation-high",
    "opacity-zero",
    "opacity-quarter",
    "opacity-half",
    "opacity-three-quarter",
    "fill-low",
    "fill-high",
    "combined-holdout",
)
MATRIX_FIELDS = tuple(
    field
    for _, _, fields in MATRIX_FIELD_GROUPS
    for field in fields
)


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} is not an object")
    return value


def _hex_integer(value: object, field: str) -> int:
    if not isinstance(value, str):
        raise ValueError(f"{field} is not a hexadecimal string")
    try:
        return int(value, 16)
    except ValueError as error:
        raise ValueError(f"{field} is not valid hexadecimal") from error


def _capture_bytes(
    value: object,
    *,
    field: str,
    expected_length: int,
) -> bytes:
    capture = _mapping(value, field)
    encoded = capture.get("hex")
    if (
        capture.get("lengthBytes") != expected_length
        or not isinstance(encoded, str)
        or len(encoded) != expected_length * 2
    ):
        raise ValueError(f"{field} byte length differs")
    try:
        raw = bytes.fromhex(encoded)
    except ValueError as error:
        raise ValueError(f"{field} is not hexadecimal") from error
    digest = hashlib.sha256(raw).hexdigest()
    if capture.get("sha256") != digest:
        raise ValueError(f"{field} SHA-256 differs")
    return raw


def _word(code: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(code):
        raise ValueError("instruction offset is outside captured code")
    return int.from_bytes(code[offset : offset + 4], "little")


def _branch_target(
    code: bytes,
    *,
    offset: int,
    code_address: int,
) -> tuple[int, int]:
    instruction = _word(code, offset)
    if instruction & 0xFC00_0000 != 0x9400_0000:
        raise ValueError(f"instruction 0x{offset:x} is not BL")
    immediate = instruction & 0x03FF_FFFF
    if immediate & 0x0200_0000:
        immediate -= 0x0400_0000
    return instruction, code_address + offset + immediate * 4


def _page_relative_target(
    code: bytes,
    *,
    code_address: int,
    adrp_offset: int,
    add_offset: int,
    register: int,
) -> tuple[int, int, int]:
    adrp = _word(code, adrp_offset)
    add = _word(code, add_offset)
    if (
        adrp & 0x9F00_0000 != 0x9000_0000
        or adrp & 0x1F != register
        or add & 0xFF00_0000 != 0x9100_0000
        or add & 0x1F != register
        or (add >> 5) & 0x1F != register
    ):
        raise ValueError("matrix basis does not use expected ADRP+ADD")
    page_immediate = (
        ((adrp >> 5) & 0x7FFFF) << 2
    ) | ((adrp >> 29) & 0x3)
    if page_immediate & 0x10_0000:
        page_immediate -= 0x20_0000
    target_page = (
        (code_address + adrp_offset) & ~0xFFF
    ) + page_immediate * 0x1000
    add_immediate = (add >> 10) & 0xFFF
    if (add >> 22) & 1:
        add_immediate <<= 12
    return adrp, add, target_page + add_immediate


def _report_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.is_file():
        return resolved
    report = resolved / REPORT_NAME
    if not report.is_file():
        raise ValueError(f"{resolved} has no {REPORT_NAME}")
    return report


def _call_site(matrix_basis: Mapping[str, Any]) -> Mapping[str, Any]:
    records = matrix_basis.get("records")
    if not isinstance(records, list):
        raise ValueError("matrix basis records are absent")
    neutral = [
        record
        for record in records
        if isinstance(record, Mapping)
        and record.get("name") == "neutral-axes"
    ]
    if len(neutral) != 1:
        raise ValueError("neutral-axes intervention is not unique")
    render = _mapping(neutral[0].get("render"), "neutral render")
    bindings = render.get("glassFragmentUniformBindings")
    if not isinstance(bindings, list):
        raise ValueError("neutral render bindings are absent")
    call_sites = [
        binding.get("uniformCallSite")
        for binding in bindings
        if isinstance(binding, Mapping)
        and binding.get("uniformCallSite") is not None
    ]
    if len(call_sites) != 1:
        raise ValueError("uniform call-site evidence is not unique")
    call_site = _mapping(call_sites[0], "uniformCallSite")
    if (
        call_site.get("schemaVersion") != 4
        or call_site.get(
            "glassMatrixConstructorConstantDataCaptureCount"
        )
        != 1
    ):
        raise ValueError("uniform call-site schema differs")
    return call_site


def constructor_evidence(
    matrix_basis: Mapping[str, Any],
) -> JsonObject:
    """Independently validate the render→constructor→constant chain."""

    call_site = _call_site(matrix_basis)
    frames = call_site.get("frames")
    if not isinstance(frames, list):
        raise ValueError("uniform call-site frames are absent")
    candidates = [
        frame
        for frame in frames
        if isinstance(frame, Mapping)
        and frame.get("matrixConstructorConstantData") is not None
    ]
    if len(candidates) != 1:
        raise ValueError("constructor constant frame is not unique")
    frame = candidates[0]

    symbol = _mapping(frame.get("symbolCode"), "symbolCode")
    symbol_code = _capture_bytes(
        symbol,
        field="symbolCode",
        expected_length=0x2000,
    )
    symbol_address = _hex_integer(
        symbol.get("startAddress"),
        "symbolCode.startAddress",
    )
    decoded_calls = [
        _branch_target(
            symbol_code,
            offset=offset,
            code_address=symbol_address,
        )
        for offset in RENDER_CALL_OFFSETS
    ]
    call_targets = {target for _, target in decoded_calls}
    if len(call_targets) != 1:
        raise ValueError("render constructor BL targets differ")

    constructor = _mapping(
        frame.get("matrixConstructorCode"),
        "matrixConstructorCode",
    )
    constructor_code = _capture_bytes(
        constructor,
        field="matrixConstructorCode",
        expected_length=0x800,
    )
    constructor_address = _hex_integer(
        constructor.get("startAddress"),
        "matrixConstructorCode.startAddress",
    )
    if call_targets != {constructor_address}:
        raise ValueError("render BL target is not constructor capture")
    for offset, expected in CRITICAL_CONSTRUCTOR_INSTRUCTIONS.items():
        if _word(constructor_code, offset) != expected:
            raise ValueError(
                f"constructor instruction 0x{offset:x} differs"
            )
    concat_start, concat_end = CONCATENATION_CODE_RANGE
    concat_digest = hashlib.sha256(
        constructor_code[concat_start:concat_end]
    ).hexdigest()
    if concat_digest != CONCATENATION_CODE_SHA256:
        raise ValueError("matrix concatenation instruction bytes differ")

    constant_capture = _mapping(
        frame.get("matrixConstructorConstantData"),
        "matrixConstructorConstantData",
    )
    constant_data = _capture_bytes(
        constant_capture,
        field="matrixConstructorConstantData",
        expected_length=160,
    )
    if (
        constant_capture.get("matrixByteCount") != 80
        or hashlib.sha256(constant_data).hexdigest()
        != APPLE_MATRIX_BASIS_SHA256
        or constant_data != APPLE_MATRIX_BASIS_BYTES
    ):
        raise ValueError("captured Apple matrix basis bytes differ")

    decoded_references = [
        (
            reference,
            _page_relative_target(
                constructor_code,
                code_address=constructor_address,
                adrp_offset=reference[0],
                add_offset=reference[1],
                register=reference[2],
            ),
        )
        for reference in BASIS_REFERENCES
    ]
    targets = [decoded[2] for _, decoded in decoded_references]
    if targets[1] != targets[0] + 80:
        raise ValueError("captured matrix operands are not adjacent")
    start_address = _hex_integer(
        constant_capture.get("startAddress"),
        "matrixConstructorConstantData.startAddress",
    )
    if start_address != targets[0]:
        raise ValueError("constant capture does not start at first operand")
    expected_references = [
        {
            "adrpOffset": reference[0],
            "addOffset": reference[1],
            "register": reference[2],
            "adrpInstruction": f"{decoded[0]:08x}",
            "addInstruction": f"{decoded[1]:08x}",
            "address": f"0x{decoded[2]:016x}",
        }
        for reference, decoded in decoded_references
    ]
    if constant_capture.get("sourceReferences") != expected_references:
        raise ValueError("serialized matrix basis references differ")

    return {
        "uniformCallSiteSchema": call_site["schemaVersion"],
        "renderCodeSha256": hashlib.sha256(symbol_code).hexdigest(),
        "constructorAddress": f"0x{constructor_address:016x}",
        "constructorCodeSha256":
            hashlib.sha256(constructor_code).hexdigest(),
        "concatenationCodeRange": [
            f"0x{concat_start:x}",
            f"0x{concat_end:x}",
        ],
        "concatenationCodeSha256": concat_digest,
        "basisAddresses": [
            f"0x{target:016x}" for target in targets
        ],
        "basisAddressDeltaBytes": targets[1] - targets[0],
        "basisBytes": len(constant_data),
        "basisSha256": hashlib.sha256(constant_data).hexdigest(),
        "basisMatchesEmbeddedModel": True,
    }


def analyze(path: Path) -> JsonObject:
    report_path = _report_path(path)
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    uniforms = _mapping(
        report.get("dynamicBackgroundUniforms"),
        "dynamicBackgroundUniforms",
    )
    matrix_basis = _mapping(
        uniforms.get("matrixUniformBasis"),
        "matrixUniformBasis",
    )
    records = matrix_basis.get("records")
    if (
        matrix_basis.get("schemaVersion") != 1
        or matrix_basis.get("executed") is not True
        or not isinstance(records, list)
        or tuple(
            record.get("name")
            for record in records
            if isinstance(record, Mapping)
        )
        != EXPECTED_INTERVENTIONS
    ):
        raise ValueError("matrix intervention corpus differs")

    evidence = constructor_evidence(matrix_basis)
    coefficient_count = 0
    matching_coefficients = 0
    states: list[JsonObject] = []
    for record in records:
        intervention = _mapping(record, "matrix basis record")
        values = _mapping(
            _mapping(
                intervention.get("filter"),
                "matrix basis filter",
            ).get("inputValues"),
            "matrix basis inputValues",
        )
        render = _mapping(
            intervention.get("render"),
            "matrix basis render",
        )
        bindings = render.get("glassFragmentUniformBindings")
        if not isinstance(bindings, list) or len(bindings) != 2:
            raise ValueError("matrix intervention draw count differs")
        profiles = []
        for binding in bindings:
            payload = _mapping(
                _mapping(binding, "matrix binding").get("payload"),
                "matrix binding payload",
            )
            encoded = payload.get("hex")
            if not isinstance(encoded, str):
                raise ValueError("matrix binding payload is absent")
            profiles.append(decode_profile(bytes.fromhex(encoded)))
        actual = {
            field: profiles[0]["fields"][field]["bits"]
            for field in MATRIX_FIELDS
        }
        second = {
            field: profiles[1]["fields"][field]["bits"]
            for field in MATRIX_FIELDS
        }
        if actual != second:
            raise ValueError("main and shadow matrix payloads differ")
        expected = expected_matrix_field_bits(values)
        mismatches = [
            {
                "field": field,
                "component": component,
                "expected": expected[field][component],
                "actual": actual[field][component],
            }
            for field in MATRIX_FIELDS
            for component in range(4)
            if expected[field][component] != actual[field][component]
        ]
        state_coefficients = len(MATRIX_FIELDS) * 4
        state_matches = state_coefficients - len(mismatches)
        coefficient_count += state_coefficients
        matching_coefficients += state_matches
        states.append({
            "name": intervention["name"],
            "coefficients": state_coefficients,
            "matchingCoefficients": state_matches,
            "bitExact": not mismatches,
            "mismatches": mismatches,
        })

    return {
        "schemaVersion": 1,
        "analysis":
            "apple-liquid-glass-private-matrix-constructor",
        "artifact": str(report_path.parent),
        "timelineJsonSha256": hashlib.sha256(
            report_bytes
        ).hexdigest(),
        "implementation": {
            "file":
                "analysis/liquid_glass_transition_matrix_basis.py",
            "python": platform.python_version(),
        },
        "constructorEvidence": evidence,
        "validation": {
            "interventions": len(states),
            "draws": len(states) * 2,
            "matrixRows": len(states) * len(MATRIX_FIELDS),
            "coefficients": coefficient_count,
            "matchingCoefficients": matching_coefficients,
            "mismatchedCoefficients":
                coefficient_count - matching_coefficients,
            "allCoefficientsBitExact":
                coefficient_count == matching_coefficients,
            "combinedHoldoutBitExact":
                states[-1]["name"] == "combined-holdout"
                and states[-1]["bitExact"],
        },
        "states": states,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifact",
        type=Path,
        help="schema-5 transition artifact or its timeline JSON",
    )
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(arguments.artifact)
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
    validation = report["validation"]
    return 0 if (
        report["constructorEvidence"]["basisMatchesEmbeddedModel"]
        and validation["allCoefficientsBitExact"]
        and validation["combinedHoldoutBitExact"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
