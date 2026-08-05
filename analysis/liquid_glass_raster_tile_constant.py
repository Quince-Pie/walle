#!/usr/bin/env python3
"""Audit candidate AGX tile-plane constant arithmetic against paired pulls.

The paired-pull recovery report represents each measured tile plane as one
27-bit slope and one binary32 constant.  Those values are observational
proxies: this audit deliberately does not claim that AGX stores either value
in that form internally.
"""

import argparse
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import liquid_glass_geometry_coordinate_gate as geometry
import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_tile_numerator as recovery
import raster_tile_numerator_v2_contract as capture


type JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class BinaryTerm:
    index: int
    lsb_exponent: int


@dataclass(frozen=True, slots=True)
class ProductConfiguration:
    output_bits: int
    truncation_bits: int
    bias_units: int

    @property
    def name(self) -> str:
        return f"p{self.output_bits}-t{self.truncation_bits}-b{self.bias_units}"


ENDPOINT_PRODUCT_CONFIGURATIONS = (
    ProductConfiguration(25, 16, 28),
    ProductConfiguration(25, 17, 14),
    ProductConfiguration(25, 17, 15),
    ProductConfiguration(27, 16, 14),
)
RECIPROCAL_PRODUCT_CONFIGURATIONS = (
    ProductConfiguration(27, 19, 20),
    ProductConfiguration(27, 15, 108),
)
ENDPOINT_FACTORIZATIONS = (
    "endpoint-x-float-edge-distance",
    "float-edge-distance-x-endpoint",
    "endpoint-x-edge-distance",
    "endpoint-edge-x-distance",
    "endpoint-distance-x-edge",
    "edge-x-endpoint-distance",
    "distance-x-endpoint-edge",
    "edge-distance-x-endpoint",
)
ENDPOINT_PIPELINES = (
    "stage-edge-exact-distance",
    "stage-distance-exact-edge",
    "stage-edge-stage-distance",
    "stage-distance-stage-edge",
)


def float32_bits(value: float) -> int:
    return raster.float32_bits(raster.float32(value))


def endpoint_term(
    bits: int,
    opposite_edge: int,
    distance: int,
    configuration: ProductConfiguration,
    *,
    factorization: str,
) -> BinaryTerm | None:
    """Multiply a binary32 endpoint by one signed integer edge weight."""

    if distance == 0 or bits & 0x7FFF_FFFF == 0:
        return None
    value = raster.bits_float32(bits)
    magnitude_bits = bits & 0x7FFF_FFFF
    significand, lsb_exponent = geometry.float_significand_and_lsb_exponent(
        magnitude_bits
    )
    distance_magnitude = abs(distance)
    if factorization in {
        "endpoint-x-float-edge-distance",
        "float-edge-distance-x-endpoint",
    }:
        weight_significand, weight_exponent = (
            geometry.float_significand_and_lsb_exponent(
                float32_bits(float(opposite_edge * distance_magnitude))
            )
        )
        if factorization == "endpoint-x-float-edge-distance":
            multiplicand, multiplicand_exponent = significand, lsb_exponent
            multiplier, multiplier_exponent = weight_significand, weight_exponent
        else:
            multiplicand, multiplicand_exponent = weight_significand, weight_exponent
            multiplier, multiplier_exponent = significand, lsb_exponent
        index, exponent = geometry.product_stage(
            multiplicand,
            multiplicand_exponent,
            multiplier,
            multiplier_exponent,
            output_bits=configuration.output_bits,
            truncation_bits=configuration.truncation_bits,
            bias_units=configuration.bias_units,
        )
        negative = (value < 0.0) != (distance < 0)
        return BinaryTerm(-index if negative else index, exponent)
    factors = {
        "endpoint-x-edge-distance": (
            significand,
            opposite_edge * distance_magnitude,
        ),
        "endpoint-edge-x-distance": (
            significand * opposite_edge,
            distance_magnitude,
        ),
        "endpoint-distance-x-edge": (
            significand * distance_magnitude,
            opposite_edge,
        ),
        "edge-x-endpoint-distance": (
            opposite_edge,
            significand * distance_magnitude,
        ),
        "distance-x-endpoint-edge": (
            distance_magnitude,
            significand * opposite_edge,
        ),
        "edge-distance-x-endpoint": (
            opposite_edge * distance_magnitude,
            significand,
        ),
    }
    multiplicand, multiplier = factors[factorization]
    index, exponent = geometry.product_stage(
        multiplicand,
        lsb_exponent,
        multiplier,
        0,
        output_bits=configuration.output_bits,
        truncation_bits=configuration.truncation_bits,
        bias_units=configuration.bias_units,
    )
    negative = (value < 0.0) != (distance < 0)
    return BinaryTerm(-index if negative else index, exponent)


def add_terms(terms: tuple[BinaryTerm | None, ...]) -> BinaryTerm | None:
    present = tuple(term for term in terms if term is not None)
    if not present:
        return None
    exponent = min(term.lsb_exponent for term in present)
    index = sum(term.index << (term.lsb_exponent - exponent) for term in present)
    return None if index == 0 else BinaryTerm(index, exponent)


def staged_binary_product(
    term: BinaryTerm,
    multiplier: int,
    configuration: ProductConfiguration,
) -> BinaryTerm:
    index, exponent = geometry.product_stage(
        abs(term.index),
        term.lsb_exponent,
        multiplier,
        0,
        output_bits=configuration.output_bits,
        truncation_bits=configuration.truncation_bits,
        bias_units=configuration.bias_units,
    )
    return BinaryTerm(-index if term.index < 0 else index, exponent)


def endpoint_pipeline_term(
    bits: int,
    opposite_edge: int,
    distance: int,
    configuration: ProductConfiguration,
    *,
    pipeline: str,
) -> BinaryTerm | None:
    if distance == 0 or bits & 0x7FFF_FFFF == 0:
        return None
    value = raster.bits_float32(bits)
    significand, exponent = geometry.float_significand_and_lsb_exponent(
        bits & 0x7FFF_FFFF
    )
    term = BinaryTerm(significand, exponent)
    distance_magnitude = abs(distance)
    if pipeline == "stage-edge-exact-distance":
        term = staged_binary_product(term, opposite_edge, configuration)
        term = BinaryTerm(term.index * distance_magnitude, term.lsb_exponent)
    elif pipeline == "stage-distance-exact-edge":
        term = staged_binary_product(term, distance_magnitude, configuration)
        term = BinaryTerm(term.index * opposite_edge, term.lsb_exponent)
    elif pipeline == "stage-edge-stage-distance":
        term = staged_binary_product(term, opposite_edge, configuration)
        term = staged_binary_product(term, distance_magnitude, configuration)
    elif pipeline == "stage-distance-stage-edge":
        term = staged_binary_product(term, distance_magnitude, configuration)
        term = staged_binary_product(term, opposite_edge, configuration)
    else:
        raise ValueError(f"unknown endpoint pipeline: {pipeline}")
    negative = (value < 0.0) != (distance < 0)
    return BinaryTerm(-term.index if negative else term.index, term.lsb_exponent)


def reciprocal_term(
    numerator: BinaryTerm | None,
    determinant: int,
    reciprocal_index: int,
    configuration: ProductConfiguration,
    *,
    swapped: bool,
) -> int:
    if numerator is None:
        return 0
    reciprocal_exponent = -(determinant - 1).bit_length() - 24
    magnitude = abs(numerator.index)
    if swapped:
        index, exponent = geometry.product_stage(
            reciprocal_index,
            reciprocal_exponent,
            magnitude,
            numerator.lsb_exponent,
            output_bits=configuration.output_bits,
            truncation_bits=configuration.truncation_bits,
            bias_units=configuration.bias_units,
        )
    else:
        index, exponent = geometry.product_stage(
            magnitude,
            numerator.lsb_exponent,
            reciprocal_index,
            reciprocal_exponent,
            output_bits=configuration.output_bits,
            truncation_bits=configuration.truncation_bits,
            bias_units=configuration.bias_units,
        )
    value = math.ldexp(index, exponent)
    if numerator.index < 0:
        value = -value
    return float32_bits(value)


def endpoint_weighted_constant(
    low_bits: int,
    high_bits: int,
    *,
    extent: int,
    opposite_edge: int,
    displacement: int,
    determinant: int,
    reciprocal_index: int,
    endpoint_configuration: ProductConfiguration,
    reciprocal_configuration: ProductConfiguration,
    endpoint_factorization: str,
    swap_reciprocal_product: bool,
) -> int:
    """Evaluate the two endpoint-weighted determinant numerators separately."""

    numerator = add_terms(
        (
            endpoint_term(
                low_bits,
                opposite_edge,
                extent - displacement,
                endpoint_configuration,
                factorization=endpoint_factorization,
            ),
            endpoint_term(
                high_bits,
                opposite_edge,
                displacement,
                endpoint_configuration,
                factorization=endpoint_factorization,
            ),
        )
    )
    return reciprocal_term(
        numerator,
        determinant,
        reciprocal_index,
        reciprocal_configuration,
        swapped=swap_reciprocal_product,
    )


def endpoint_pipeline_constant(
    low_bits: int,
    high_bits: int,
    *,
    extent: int,
    opposite_edge: int,
    displacement: int,
    determinant: int,
    reciprocal_index: int,
    endpoint_configuration: ProductConfiguration,
    reciprocal_configuration: ProductConfiguration,
    endpoint_pipeline: str,
    swap_reciprocal_product: bool,
) -> int:
    numerator = add_terms(
        (
            endpoint_pipeline_term(
                low_bits,
                opposite_edge,
                extent - displacement,
                endpoint_configuration,
                pipeline=endpoint_pipeline,
            ),
            endpoint_pipeline_term(
                high_bits,
                opposite_edge,
                displacement,
                endpoint_configuration,
                pipeline=endpoint_pipeline,
            ),
        )
    )
    return reciprocal_term(
        numerator,
        determinant,
        reciprocal_index,
        reciprocal_configuration,
        swapped=swap_reciprocal_product,
    )


def endpoint_dot_product_constant(
    low_bits: int,
    high_bits: int,
    *,
    extent: int,
    opposite_edge: int,
    displacement: int,
    determinant: int,
    reciprocal_index: int,
    endpoint_configuration: ProductConfiguration,
    reciprocal_configuration: ProductConfiguration,
    swap_reciprocal_product: bool,
) -> int:
    """Fuse endpoint-weighted partial products before the first rounding."""

    weighted: list[tuple[int, int, int]] = []
    for bits, weight in (
        (low_bits, opposite_edge * (extent - displacement)),
        (high_bits, opposite_edge * displacement),
    ):
        if weight == 0 or bits & 0x7FFF_FFFF == 0:
            continue
        value = raster.bits_float32(bits)
        significand, exponent = geometry.float_significand_and_lsb_exponent(
            bits & 0x7FFF_FFFF
        )
        negative = (value < 0.0) != (weight < 0)
        weighted.append(
            (-significand if negative else significand, exponent, abs(weight))
        )
    if not weighted:
        return 0
    common_exponent = min(exponent for _, exponent, _ in weighted)
    exact = sum(
        significand * weight << (exponent - common_exponent)
        for significand, exponent, weight in weighted
    )
    if exact == 0:
        return 0
    if exact < 0:
        # The available captures do not yet discriminate a signed fused dot
        # product well enough to justify inventing its rounding convention.
        raise ValueError("negative fused endpoint dot product is unresolved")
    truncated = sum(
        geometry.partial_product_sum(
            significand,
            weight,
            endpoint_configuration.truncation_bits,
        )
        << (exponent - common_exponent)
        for significand, exponent, weight in weighted
    )
    product_shift = exact.bit_length() - endpoint_configuration.output_bits
    if product_shift < 0:
        raise ValueError("endpoint dot product does not fill the requested precision")
    numerator = BinaryTerm(
        (
            truncated
            + (
                endpoint_configuration.bias_units
                << endpoint_configuration.truncation_bits
            )
        )
        >> product_shift,
        common_exponent + product_shift,
    )
    return reciprocal_term(
        numerator,
        determinant,
        reciprocal_index,
        reciprocal_configuration,
        swapped=swap_reciprocal_product,
    )


def physical_plane_prediction(
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
        anchor_value = high
        anchor_position = origin + extent
    else:
        anchor_value = low
        anchor_position = origin
    return float32_bits(
        geometry.physical_plane_constant(
            anchor_value,
            high - low,
            float(anchor_position),
            tile * capture.TILE_SIZE,
            opposite_edge=opposite_edge,
            determinant=determinant,
            reciprocal_index=reciprocal_index,
        )
    )


def recovered_proxy_slopes(
    report: JsonObject,
) -> dict[tuple[str, str, str, int], tuple[float, ...]]:
    """Intersect each endpoint's observational slope candidates across tiles."""

    endpoints = {endpoint.name: endpoint for endpoint in capture.ENDPOINTS}
    cases = {case.name: case for case in capture.CASES}
    candidates: dict[
        tuple[str, str, str, int],
        list[set[Fraction]],
    ] = defaultdict(list)
    for group in report["groups"]:
        case_name = str(group["case"])
        endpoint_name = str(group["endpoint"])
        endpoint = endpoints[endpoint_name]
        case = cases[case_name]
        axis = str(group["axis"])
        extent = case.width if axis == "x" else case.height
        delta = raster.float32_bits_fraction(
            endpoint.highBits
        ) - raster.float32_bits_fraction(endpoint.lowBits)
        if delta == 0:
            continue
        ideal = delta / extent
        centered = recovery.signed_quantized_slope(ideal)
        step = recovery.slope_step(ideal)
        offsets = {
            int(offset)
            for values in group["slopeOffsetsByConstant"].values()
            for offset in values
        }
        key = (case_name, endpoint_name, axis, int(group["primitive"]))
        candidates[key].append({centered + offset * step for offset in offsets})
    return {
        key: tuple(float(value) for value in sorted(set.intersection(*sets)))
        for key, sets in candidates.items()
    }


def compensated_rebase_constant(
    anchor: float,
    displacement: int,
    slope: float,
) -> int:
    high = raster.float32(slope)
    low = raster.float32(slope - high)
    nearest = raster.float32(math.fma(float(displacement), high, anchor))
    residual = raster.float32(math.fma(float(displacement), high, anchor - nearest))
    correction = raster.float32(math.fma(float(displacement), low, residual))
    return float32_bits(raster.float32(nearest + correction))


def unique_proxy_slopes(report: JsonObject) -> JsonObject:
    by_setup: dict[tuple[str, str, int], list[set[str]]] = defaultdict(list)
    for group in report["unitSpanSlopeLaw"]["groups"]:
        key = (str(group["case"]), str(group["axis"]), int(group["primitive"]))
        by_setup[key].append(set(group["sharedSlopeHex"]))
    groups: list[JsonObject] = []
    for (case_name, axis, primitive), candidates in sorted(by_setup.items()):
        intersection = set.intersection(*candidates)
        groups.append(
            {
                "case": case_name,
                "axis": axis,
                "primitive": primitive,
                "tileCount": len(candidates),
                "candidateCount": len(intersection),
                "slopeHex": sorted(intersection),
            }
        )
    return {
        "groupCount": len(groups),
        "allNonempty": all(group["candidateCount"] for group in groups),
        "uniqueCount": sum(group["candidateCount"] == 1 for group in groups),
        "maximumCandidateCount": max(group["candidateCount"] for group in groups),
        "observationalProxyOnly": True,
        "groups": groups,
    }


def analyze(report_path: Path) -> JsonObject:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    selector_table = geometry.load_selector_table(geometry.SELECTOR_TABLE_PATH)
    cases = {case.name: case for case in capture.CASES}
    endpoints = {endpoint.name: endpoint for endpoint in capture.ENDPOINTS}
    proxy_slopes = recovered_proxy_slopes(report)
    model_matches: Counter[str] = Counter()
    comparisons = 0
    residuals: list[JsonObject] = []
    endpoint_matches_by_group: dict[str, set[str]] = defaultdict(set)

    for group in report["groups"]:
        constants = [int(value, 16) for value in group["constantBits"]]
        if len(constants) != 1:
            continue
        comparisons += 1
        observed = constants[0]
        capture_case = cases[str(group["case"])]
        endpoint = endpoints[str(group["endpoint"])]
        axis = 0 if group["axis"] == "x" else 1
        primitive = int(group["primitive"])
        tile = int(group["tile"])
        extent = capture_case.width if axis == 0 else capture_case.height
        opposite_edge = capture_case.height if axis == 0 else capture_case.width
        origin = capture_case.originX if axis == 0 else capture_case.originY
        displacement = tile * capture.TILE_SIZE - origin
        determinant = capture_case.width * capture_case.height
        reciprocal_index = geometry.reciprocal_selector(determinant, selector_table)

        predictions = {
            "physical-anchor": physical_plane_prediction(
                capture_case,
                endpoint,
                axis=axis,
                primitive=primitive,
                tile=tile,
                reciprocal_index=reciprocal_index,
            ),
            **{
                f"simple:{name}": int(bits, 16)
                for name, bits in group["simpleConstantModels"].items()
            },
        }
        slope_candidates = proxy_slopes[
            (
                capture_case.name,
                endpoint.name,
                str(group["axis"]),
                primitive,
            )
        ]
        for slope_index, slope in enumerate(slope_candidates):
            for anchor_name, anchor, anchor_position in (
                ("low", raster.bits_float32(endpoint.lowBits), origin),
                (
                    "high",
                    raster.bits_float32(endpoint.highBits),
                    origin + extent,
                ),
            ):
                anchor_displacement = tile * capture.TILE_SIZE - anchor_position
                prefix = f"proxy-slope-{slope_index}:{anchor_name}-anchor"
                predictions[f"{prefix}:float"] = float32_bits(
                    math.fma(float(anchor_displacement), slope, anchor)
                )
                predictions[f"{prefix}:compensated"] = compensated_rebase_constant(
                    anchor,
                    anchor_displacement,
                    slope,
                )
                try:
                    predictions[f"{prefix}:physical"] = float32_bits(
                        geometry.physical_rebase_constant(
                            anchor,
                            float(anchor_position),
                            tile * capture.TILE_SIZE,
                            slope,
                        )
                    )
                except ValueError:
                    pass
        for endpoint_configuration in ENDPOINT_PRODUCT_CONFIGURATIONS:
            for reciprocal_configuration in RECIPROCAL_PRODUCT_CONFIGURATIONS:
                for endpoint_factorization in ENDPOINT_FACTORIZATIONS:
                    for swap_reciprocal_product in (False, True):
                        name = (
                            f"endpoint-weighted:{endpoint_configuration.name}:"
                            f"{reciprocal_configuration.name}:"
                            f"ep-{endpoint_factorization}:"
                            f"recip-{'swap' if swap_reciprocal_product else 'ordered'}"
                        )
                        try:
                            predictions[name] = endpoint_weighted_constant(
                                endpoint.lowBits,
                                endpoint.highBits,
                                extent=extent,
                                opposite_edge=opposite_edge,
                                displacement=displacement,
                                determinant=determinant,
                                reciprocal_index=reciprocal_index,
                                endpoint_configuration=endpoint_configuration,
                                reciprocal_configuration=reciprocal_configuration,
                                endpoint_factorization=endpoint_factorization,
                                swap_reciprocal_product=swap_reciprocal_product,
                            )
                        except ValueError:
                            continue
                for swap_reciprocal_product in (False, True):
                    name = (
                        f"endpoint-dot:{endpoint_configuration.name}:"
                        f"{reciprocal_configuration.name}:"
                        f"recip-{'swap' if swap_reciprocal_product else 'ordered'}"
                    )
                    try:
                        predictions[name] = endpoint_dot_product_constant(
                            endpoint.lowBits,
                            endpoint.highBits,
                            extent=extent,
                            opposite_edge=opposite_edge,
                            displacement=displacement,
                            determinant=determinant,
                            reciprocal_index=reciprocal_index,
                            endpoint_configuration=endpoint_configuration,
                            reciprocal_configuration=reciprocal_configuration,
                            swap_reciprocal_product=swap_reciprocal_product,
                        )
                    except ValueError:
                        continue
                for endpoint_pipeline in ENDPOINT_PIPELINES:
                    for swap_reciprocal_product in (False, True):
                        name = (
                            f"endpoint-pipeline:{endpoint_configuration.name}:"
                            f"{reciprocal_configuration.name}:"
                            f"ep-{endpoint_pipeline}:"
                            f"recip-{'swap' if swap_reciprocal_product else 'ordered'}"
                        )
                        try:
                            predictions[name] = endpoint_pipeline_constant(
                                endpoint.lowBits,
                                endpoint.highBits,
                                extent=extent,
                                opposite_edge=opposite_edge,
                                displacement=displacement,
                                determinant=determinant,
                                reciprocal_index=reciprocal_index,
                                endpoint_configuration=endpoint_configuration,
                                reciprocal_configuration=reciprocal_configuration,
                                endpoint_pipeline=endpoint_pipeline,
                                swap_reciprocal_product=swap_reciprocal_product,
                            )
                        except ValueError:
                            continue

        matching = sorted(
            name for name, predicted in predictions.items() if predicted == observed
        )
        model_matches.update(matching)
        group_key = (
            f"{capture_case.name}:{group['axis']}:p{primitive}:"
            f"d{displacement}:e{endpoint.name}"
        )
        endpoint_matches_by_group[group_key].update(matching)
        if "physical-anchor" not in matching:
            residuals.append(
                {
                    "case": capture_case.name,
                    "caseRole": capture_case.role,
                    "endpoint": endpoint.name,
                    "axis": group["axis"],
                    "primitive": primitive,
                    "tile": tile,
                    "displacement": displacement,
                    "observedBits": f"0x{observed:08x}",
                    "physicalBits": f"0x{predictions['physical-anchor']:08x}",
                    "matchingModels": matching,
                }
            )

    union_names = {
        name
        for name in model_matches
        if name == "physical-anchor" or name.startswith("simple:")
    }
    base_union_count = sum(
        bool(models & union_names) for models in endpoint_matches_by_group.values()
    )
    all_union_count = sum(bool(models) for models in endpoint_matches_by_group.values())
    model_table = [
        {
            "name": name,
            "matchCount": count,
            "comparisonCount": comparisons,
            "matchRate": count / comparisons,
            "exact": count == comparisons,
        }
        for name, count in sorted(
            model_matches.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return {
        "liquidGlassRasterTileConstantAnalysisSchemaVersion": 1,
        "sourceReport": str(report_path),
        "classification": {
            "recoveredConstantsAreObservationalProxies": True,
            "fixed28BitExactLinearHiddenConstantHypothesisEstablished": False,
            "candidateUnionIsPredictiveAlgorithm": False,
        },
        "proxySlopeIntersections": unique_proxy_slopes(report),
        "perEndpointProxySlopeIntersections": {
            "groupCount": len(proxy_slopes),
            "emptyCount": sum(not values for values in proxy_slopes.values()),
            "uniqueCount": sum(len(values) == 1 for values in proxy_slopes.values()),
            "maximumCandidateCount": max(map(len, proxy_slopes.values())),
            "observationalProxyOnly": True,
        },
        "measurement": {
            "uniqueConstantComparisonCount": comparisons,
            "physicalAnchorMatchCount": model_matches["physical-anchor"],
            "physicalAnchorResidualCount": len(residuals),
            "physicalOrSimpleUnionMatchCount": base_union_count,
            "allCandidateUnionMatchCount": all_union_count,
            "models": model_table,
            "physicalAnchorResiduals": residuals,
        },
        "conclusions": {
            "constantArithmeticFullyDetermined": False,
            "selectorLawEstablished": False,
            "prospectiveHoldoutAuthorized": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.write_text(
        json.dumps(analyze(arguments.report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
