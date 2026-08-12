#!/usr/bin/env python3
"""Test preregistered tile-constant candidates on schema-3 discovery pulls."""

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

import liquid_glass_geometry_coordinate_gate as geometry
import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_tile_constant as constant_math
import liquid_glass_raster_tile_numerator as numerator_math
import validate_raster_tile_numerator as capture


type JsonObject = dict[str, Any]

EXPECTED_RAW_SHA256 = "c260075c6865c8d95749a6b6db51e441a37f9e2448ca4a4c1cfea8baac78c99b"
DISCOVERY_ENDPOINT_ROLE = "selector-discovery"
MAX_EXAMPLES = 4_096
P28_BELOW_FLOOR_MODEL = "slope-ideal-p27-below-floor|p28-exact-nearest"
P28_INTERNAL_MODEL = "slope-internal|p28-exact-nearest"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def raw_records(root: Path) -> np.memmap:
    path = root / "raster-tile-numerator.raw"
    if path.stat().st_size != capture.raw_bytes():
        raise ValueError("schema-3 tile-selector byte count differs")
    if sha256_file(path) != EXPECTED_RAW_SHA256:
        raise ValueError("schema-3 tile-selector evidence differs")
    return np.memmap(
        path,
        mode="r",
        dtype="<u4",
        shape=(
            len(capture.CASES),
            len(capture.ENDPOINTS),
            capture.SLOT_COUNT,
            capture.RECORD_COMPONENT_COUNT,
        ),
    )


def paired_sample_groups(
    capture_case: capture.CaptureCase,
) -> dict[tuple[int, int, int], tuple[capture.SamplePosition, ...]]:
    groups: dict[tuple[int, int, int], list[capture.SamplePosition]] = defaultdict(list)
    for sample in capture.sample_positions(capture_case):
        groups[(sample.axis, sample.primitive, sample.tile)].append(sample)
    return {
        key: tuple(sorted(samples, key=lambda sample: sample.edge))
        for key, samples in groups.items()
        if len(samples) == 2
    }


def physical_constant(
    capture_case: capture.CaptureCase,
    endpoint: capture.EndpointCase,
    *,
    axis: int,
    primitive: int,
    tile: int,
    reciprocal_index: int,
) -> int:
    low = raster.bits_float32(endpoint.lowBits)
    high = raster.bits_float32(endpoint.highBits)
    extent = capture_case.width if axis == 0 else capture_case.height
    opposite_edge = capture_case.height if axis == 0 else capture_case.width
    origin = capture_case.originX if axis == 0 else capture_case.originY
    determinant = capture_case.width * capture_case.height
    if axis == 0 and primitive == 0:
        anchor = high
        anchor_position = origin + extent
    else:
        anchor = low
        anchor_position = origin
    return raster.float32_bits(
        geometry.physical_plane_constant(
            anchor,
            high - low,
            float(anchor_position),
            tile * capture.TILE_SIZE,
            opposite_edge=opposite_edge,
            determinant=determinant,
            reciprocal_index=reciprocal_index,
        )
    )


def candidate_constants(
    capture_case: capture.CaptureCase,
    endpoint: capture.EndpointCase,
    *,
    axis: int,
    primitive: int,
    tile: int,
    reciprocal_index: int,
) -> dict[str, int]:
    low = raster.bits_float32(endpoint.lowBits)
    high = raster.bits_float32(endpoint.highBits)
    extent = capture_case.width if axis == 0 else capture_case.height
    opposite_edge = capture_case.height if axis == 0 else capture_case.width
    origin = capture_case.originX if axis == 0 else capture_case.originY
    displacement = tile * capture.TILE_SIZE - origin
    determinant = capture_case.width * capture_case.height
    exact = (
        raster.float32_bits_fraction(endpoint.lowBits)
        + (
            raster.float32_bits_fraction(endpoint.highBits)
            - raster.float32_bits_fraction(endpoint.lowBits)
        )
        * displacement
        / extent
    )
    result = {
        "physical-anchor": physical_constant(
            capture_case,
            endpoint,
            axis=axis,
            primitive=primitive,
            tile=tile,
            reciprocal_index=reciprocal_index,
        ),
        **{
            f"simple:{name}": bits
            for name, bits in numerator_math.simple_constant_models(
                low,
                high,
                extent,
                displacement,
            ).items()
        },
        "p28-exact-nearest": raster.round_fraction_to_float32_bits(
            raster.quantize_binary_significand(exact, 28)
        ),
    }
    for endpoint_configuration in constant_math.ENDPOINT_PRODUCT_CONFIGURATIONS:
        for reciprocal_configuration in constant_math.RECIPROCAL_PRODUCT_CONFIGURATIONS:
            name = (
                f"normalized-weight:{endpoint_configuration.name}:"
                f"{reciprocal_configuration.name}"
            )
            result[name] = constant_math.endpoint_weighted_constant(
                endpoint.lowBits,
                endpoint.highBits,
                extent=extent,
                opposite_edge=opposite_edge,
                displacement=displacement,
                determinant=determinant,
                reciprocal_index=reciprocal_index,
                endpoint_configuration=endpoint_configuration,
                reciprocal_configuration=reciprocal_configuration,
                endpoint_factorization="endpoint-x-float-edge-distance",
                swap_reciprocal_product=False,
            )
    return result


def slope_rebase_constants(
    capture_case: capture.CaptureCase,
    endpoint: capture.EndpointCase,
    *,
    axis: int,
    primitive: int,
    tile: int,
    slope: float,
) -> dict[str, int]:
    low = raster.bits_float32(endpoint.lowBits)
    high = raster.bits_float32(endpoint.highBits)
    extent = capture_case.width if axis == 0 else capture_case.height
    origin = capture_case.originX if axis == 0 else capture_case.originY
    tile_origin = tile * capture.TILE_SIZE
    result: dict[str, int] = {}
    for anchor_name, anchor, anchor_position in (
        ("low", low, origin),
        ("high", high, origin + extent),
    ):
        displacement = tile_origin - anchor_position
        prefix = f"slope-rebase:{anchor_name}"
        result[f"{prefix}:fma"] = raster.float32_bits(
            raster.float32(math.fma(float(displacement), slope, anchor))
        )
        result[f"{prefix}:mul-add"] = raster.float32_bits(
            raster.float32(
                anchor + raster.float32(float(displacement) * raster.float32(slope))
            )
        )
        result[f"{prefix}:compensated"] = constant_math.compensated_rebase_constant(
            anchor,
            displacement,
            slope,
        )
        try:
            result[f"{prefix}:physical"] = raster.float32_bits(
                geometry.physical_rebase_constant(
                    anchor,
                    float(anchor_position),
                    tile_origin,
                    slope,
                )
            )
        except ValueError:
            pass
    physical_anchor = "high" if axis == 0 and primitive == 0 else "low"
    return {
        f"{name}:physical-primitive": bits
        for name, bits in result.items()
        if name.startswith(f"slope-rebase:{physical_anchor}:")
    } | result


def pulls_match(
    records: np.memmap,
    *,
    case_index: int,
    endpoint_index: int,
    samples: tuple[capture.SamplePosition, ...],
    axis: int,
    slope: float,
    constant_bits: int,
) -> bool:
    constant = raster.bits_float32(constant_bits)
    for sample in samples:
        coordinate = sample.x if axis == 0 else sample.y
        local_pixel = coordinate - sample.tile * capture.TILE_SIZE
        actual = records[case_index, endpoint_index, sample.slot, : capture.PULL_COUNT]
        for numerator, expected in zip(
            capture.PULL_NUMERATORS,
            actual,
            strict=True,
        ):
            if raster.pull_iterator_bits(
                local_pixel + numerator / 16,
                slope,
                constant,
            ) != int(expected):
                return False
    return True


def endpoint_features(endpoint: capture.EndpointCase) -> JsonObject:
    fields = endpoint.name.split("-")
    if len(fields) == 4 and fields[0] == "mantissa":
        return {
            "base": int(fields[1][1:]),
            "residue": int(fields[2][1:]),
            "span": int(fields[3][1:]),
            "reverse": False,
        }
    if len(fields) == 6 and fields[0] == "mantissa":
        return {
            "base": int(fields[1][1:]),
            "residue": None,
            "span": abs(endpoint.highBits - endpoint.lowBits),
            "reverse": True,
        }
    raise ValueError(f"unexpected selector endpoint name: {endpoint.name}")


def ideal_slope_variants(
    endpoint: capture.EndpointCase,
    extent: int,
    internal: float,
) -> dict[str, float]:
    delta = raster.float32_bits_fraction(
        endpoint.highBits
    ) - raster.float32_bits_fraction(endpoint.lowBits)
    sign = -1 if delta < 0 else 1
    magnitude = abs(delta) / extent
    exponent = raster.floor_binary_exponent(magnitude)
    step = raster.power_of_two(exponent - 27 + 1)
    toward_zero_magnitude = raster.quantize_binary_significand_directed(
        magnitude,
        27,
        "down",
    )
    return {
        "internal": internal,
        "ideal-p27-nearest": float(
            sign * raster.quantize_binary_significand(magnitude, 27)
        ),
        "ideal-p27-toward-zero": float(sign * toward_zero_magnitude),
        "ideal-p27-below-floor": float(sign * (toward_zero_magnitude - step)),
    }


def analyze(root: Path) -> JsonObject:
    records = raw_records(root)
    selector_table = geometry.load_selector_table(geometry.SELECTOR_TABLE_PATH)
    model_matches: Counter[str] = Counter()
    signature_counts: Counter[tuple[str, ...]] = Counter()
    feature_signatures: Counter[tuple[int, int | None, int, bool, tuple[str, ...]]] = (
        Counter()
    )
    no_candidate_examples: list[JsonObject] = []
    p28_below_floor_residuals: list[JsonObject] = []
    p28_internal_residuals: list[JsonObject] = []
    group_count = 0
    candidate_union_count = 0
    discriminating_group_count = 0

    for case_index, capture_case in enumerate(capture.CASES):
        if capture_case.role == "sealed-holdout":
            continue
        groups = paired_sample_groups(capture_case)
        determinant = capture_case.width * capture_case.height
        reciprocal_index = geometry.reciprocal_selector(determinant, selector_table)
        for endpoint_index, endpoint in enumerate(capture.ENDPOINTS):
            if endpoint.role != DISCOVERY_ENDPOINT_ROLE:
                continue
            low = raster.bits_float32(endpoint.lowBits)
            high = raster.bits_float32(endpoint.highBits)
            features = endpoint_features(endpoint)
            for (axis, primitive, tile), samples in groups.items():
                extent = capture_case.width if axis == 0 else capture_case.height
                opposite_edge = capture_case.height if axis == 0 else capture_case.width
                slope = geometry.internal_slope(
                    high - low,
                    opposite_edge=opposite_edge,
                    determinant=determinant,
                    reciprocal_index=reciprocal_index,
                )
                slopes = ideal_slope_variants(endpoint, extent, slope)
                constants = candidate_constants(
                    capture_case,
                    endpoint,
                    axis=axis,
                    primitive=primitive,
                    tile=tile,
                    reciprocal_index=reciprocal_index,
                )
                constants.update(
                    slope_rebase_constants(
                        capture_case,
                        endpoint,
                        axis=axis,
                        primitive=primitive,
                        tile=tile,
                        slope=slope,
                    )
                )
                names_by_prediction: dict[tuple[float, int], list[str]] = defaultdict(
                    list
                )
                for slope_name, candidate_slope in slopes.items():
                    for constant_name, bits in constants.items():
                        names_by_prediction[candidate_slope, bits].append(
                            f"slope-{slope_name}|{constant_name}"
                        )
                matching: list[str] = []
                for (candidate_slope, bits), names in names_by_prediction.items():
                    if pulls_match(
                        records,
                        case_index=case_index,
                        endpoint_index=endpoint_index,
                        samples=samples,
                        axis=axis,
                        slope=candidate_slope,
                        constant_bits=bits,
                    ):
                        matching.extend(names)
                signature = tuple(sorted(matching))
                group_count += 1
                candidate_union_count += bool(signature)
                discriminating_group_count += len(names_by_prediction) > 1
                model_matches.update(signature)
                signature_counts[signature] += 1
                feature_signatures[
                    (
                        int(features["base"]),
                        features["residue"],
                        int(features["span"]),
                        bool(features["reverse"]),
                        signature,
                    )
                ] += 1
                if P28_BELOW_FLOOR_MODEL not in signature:
                    origin = capture_case.originX if axis == 0 else capture_case.originY
                    p28_below_floor_residuals.append(
                        {
                            "case": capture_case.name,
                            "endpoint": endpoint.name,
                            "axis": "x" if axis == 0 else "y",
                            "primitive": primitive,
                            "tile": tile,
                            "displacement": tile * capture.TILE_SIZE - origin,
                            "base": features["base"],
                            "residue": features["residue"],
                            "span": features["span"],
                            "reverse": features["reverse"],
                            "internalP28Matched": P28_INTERNAL_MODEL in signature,
                        }
                    )
                if P28_INTERNAL_MODEL not in signature:
                    p28_internal_residuals.append(
                        {
                            "case": capture_case.name,
                            "endpoint": endpoint.name,
                            "axis": "x" if axis == 0 else "y",
                            "primitive": primitive,
                            "tile": tile,
                            "base": features["base"],
                            "residue": features["residue"],
                            "span": features["span"],
                            "reverse": features["reverse"],
                        }
                    )
                if not signature and len(no_candidate_examples) < MAX_EXAMPLES:
                    origin = capture_case.originX if axis == 0 else capture_case.originY
                    no_candidate_examples.append(
                        {
                            "case": capture_case.name,
                            "endpoint": endpoint.name,
                            "axis": "x" if axis == 0 else "y",
                            "primitive": primitive,
                            "tile": tile,
                            "displacement": tile * capture.TILE_SIZE - origin,
                            "candidateConstantBits": sorted(
                                {f"0x{bits:08x}" for _, bits in names_by_prediction}
                            ),
                        }
                    )

    return {
        "liquidGlassRasterTileSelectorAnalysisSchemaVersion": 1,
        "source": str(root),
        "rawSha256": EXPECTED_RAW_SHA256,
        "scope": {
            "endpointRole": DISCOVERY_ENDPOINT_ROLE,
            "sealedHoldoutRead": False,
            "sealedCaseCount": sum(
                case.role == "sealed-holdout" for case in capture.CASES
            ),
        },
        "measurement": {
            "groupCount": group_count,
            "discriminatingGroupCount": discriminating_group_count,
            "candidateUnionMatchCount": candidate_union_count,
            "candidateUnionMatchRate": candidate_union_count / group_count,
            "noCandidateGroupCount": group_count - candidate_union_count,
            "p28BelowFloorResidualCount": len(p28_below_floor_residuals),
            "p28BelowFloorResiduals": p28_below_floor_residuals,
            "p28InternalResidualCount": len(p28_internal_residuals),
            "p28InternalResiduals": p28_internal_residuals,
            "modelMatches": [
                {
                    "name": name,
                    "matchCount": count,
                    "matchRate": count / group_count,
                }
                for name, count in sorted(
                    model_matches.items(), key=lambda item: (-item[1], item[0])
                )
            ],
            "modelSignatureCounts": [
                {"models": list(signature), "count": count}
                for signature, count in sorted(
                    signature_counts.items(), key=lambda item: (-item[1], item[0])
                )
            ],
            "featureSignatureCounts": [
                {
                    "base": key[0],
                    "residue": key[1],
                    "span": key[2],
                    "reverse": key[3],
                    "models": list(key[4]),
                    "count": count,
                }
                for key, count in sorted(
                    feature_signatures.items(),
                    key=lambda item: (
                        (
                            item[0][0],
                            -1 if item[0][1] is None else item[0][1],
                            item[0][2],
                            item[0][3],
                        ),
                        -item[1],
                        item[0][4],
                    ),
                )
            ],
            "noCandidateExamples": no_candidate_examples,
        },
        "conclusions": {
            "candidateUnionIsPredictiveAlgorithm": False,
            "selectorLawEstablished": False,
            "sealedHoldoutAuthorized": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.write_text(
        json.dumps(analyze(arguments.probe), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
