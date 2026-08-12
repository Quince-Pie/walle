#!/usr/bin/env python3
"""Recover Apple raster setup precision from schema-8 tomography."""

import argparse
import hashlib
import json
import struct
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

import liquid_glass_raster_interpolant as raster


type Axis = Literal["x", "y"]
type JsonObject = dict[str, Any]
type UIntSurface = NDArray[np.uint32]
type Role = Literal["discovery", "holdout", "all"]

PULL_OFFSET = 0.9375
SLOPE_SEARCH_RADIUS = 64
RECIPROCAL_OFFSET_RADIUS = 128


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TomographyCase:
    root: Path
    record: JsonObject

    @property
    def name(self) -> str:
        return str(self.record["name"])

    @property
    def role(self) -> str:
        return str(self.record["role"])

    @property
    def width(self) -> int:
        return int(self.record["crop"]["width"])

    @property
    def height(self) -> int:
        return int(self.record["crop"]["height"])

    @property
    def origin_x(self) -> int:
        return int(self.record["crop"]["originX"])

    @property
    def origin_y(self) -> int:
        return int(self.record["crop"]["originY"])

    @property
    def denominator(self) -> int:
        return int(self.record["deltaDenominator"])

    @property
    def numerators(self) -> list[int]:
        return [
            int(value)
            for value in self.record["deltaNumerators"]
        ]

    def output_record(self, delta_index: int) -> JsonObject:
        matches = [
            output
            for output in self.record["outputs"]
            if int(output["deltaIndex"]) == delta_index
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{self.name} delta {delta_index} output is not unique"
            )
        return matches[0]

    def surface(self, delta_index: int) -> UIntSurface:
        output = self.output_record(delta_index)
        path = self.root / str(output["file"])
        values = np.fromfile(path, dtype="<u4")
        expected = self.width * self.height * 4
        if values.size != expected:
            raise ValueError(
                f"{self.name} delta {delta_index} has "
                f"{values.size} values; expected {expected}"
            )
        return values.reshape(self.height, self.width, 4)

    def primitive_mask(self) -> UIntSurface:
        surface = self.surface(7)
        primitive = surface[..., 3]
        if np.any((primitive != 0) & (primitive != 1)):
            raise ValueError(
                f"{self.name} has an invalid primitive ID"
            )
        if not np.any(primitive == 0) or not np.any(primitive == 1):
            raise ValueError(
                f"{self.name} does not cover both primitives"
            )
        return primitive


@dataclass(frozen=True, slots=True)
class TomographySlope:
    case: TomographyCase
    delta_index: int
    axis: Axis
    primitive: int
    accepted_bits: frozenset[int]

    @property
    def other_dimension(self) -> int:
        return self.case.height if self.axis == "x" else self.case.width

    @property
    def numerator(self) -> Fraction:
        return Fraction(
            self.case.numerators[self.delta_index]
            * self.other_dimension,
            self.case.denominator,
        )


def load_tomography_cases(
    root: Path,
    *,
    role: Role = "discovery",
) -> list[TomographyCase]:
    manifest = json.loads(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("schemaVersion") not in {
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
    }:
        raise ValueError(
            "raster probe schema 8 through 18 is required"
        )
    records = manifest.get("reciprocalTomographyCases", [])
    cases = [
        TomographyCase(root=root, record=record)
        for record in records
        if role == "all" or record.get("role") == role
    ]
    if not cases:
        raise ValueError(f"no tomography cases have role {role}")
    return cases


def axis_pull_records(
    surface: UIntSurface,
    mask: UIntSurface,
    *,
    axis: Axis,
    primitive: int,
) -> tuple[dict[int, int], dict[int, int]]:
    channels = (0, 1) if axis == "x" else (2, 3)
    return (
        raster.axis_records(
            surface,
            mask,
            axis=axis,
            primitive=primitive,
            channel=channels[0],
        ),
        raster.axis_records(
            surface,
            mask,
            axis=axis,
            primitive=primitive,
            channel=channels[1],
        ),
    )


def signed_float32_rounding_interval(
    bits: int,
) -> tuple[Fraction, Fraction]:
    magnitude = bits & 0x7fffffff
    if magnitude == 0:
        return (
            -raster.power_of_two(-150),
            raster.power_of_two(-150),
        )
    if magnitude >= 0x7f800000:
        raise ValueError("finite pull output is required")
    lower, upper = raster.float32_rounding_interval(magnitude)
    if bits & 0x80000000:
        return -upper, -lower
    return lower, upper


def exact_pull_constant(
    positions: list[float],
    targets: list[int],
    slope_bits: int,
) -> tuple[int, bool] | None:
    """Find one float32 C in the exact fused-rounding intersection."""

    slope = raster.float32_bits_fraction(slope_bits)
    lower: Fraction | None = None
    upper: Fraction | None = None
    for position, target in zip(positions, targets, strict=True):
        output_lower, output_upper = (
            signed_float32_rounding_interval(target)
        )
        product = Fraction.from_float(position) * slope
        candidate_lower = output_lower - product
        candidate_upper = output_upper - product
        lower = (
            candidate_lower
            if lower is None
            else max(lower, candidate_lower)
        )
        upper = (
            candidate_upper
            if upper is None
            else min(upper, candidate_upper)
        )
        if lower > upper:
            return None
    if lower is None or upper is None:
        raise ValueError("pull samples cannot be empty")

    midpoint = (lower + upper) / 2
    center = np.float32(float(midpoint))
    candidates = {raster.float32_bits(float(center))}
    below = center
    above = center
    for _ in range(4):
        below = np.nextafter(below, np.float32(-np.inf))
        above = np.nextafter(above, np.float32(np.inf))
        candidates.add(raster.float32_bits(float(below)))
        candidates.add(raster.float32_bits(float(above)))

    valid = sorted(
        bits
        for bits in candidates
        if (
            lower
            <= raster.float32_bits_fraction(bits)
            <= upper
            and all(
                raster.pull_iterator_bits(
                    position,
                    raster.bits_float32(slope_bits),
                    raster.bits_float32(bits),
                )
                == target
                for position, target in zip(
                    positions,
                    targets,
                    strict=True,
                )
            )
        )
    )
    if not valid:
        return None
    selected = min(
        valid,
        key=lambda bits: (
            abs(raster.float32_bits_fraction(bits) - midpoint),
            bits,
        ),
    )
    selected_value = np.float32(raster.bits_float32(selected))
    adjacent = (
        np.nextafter(selected_value, np.float32(-np.inf)),
        np.nextafter(selected_value, np.float32(np.inf)),
    )
    ambiguous = any(
        lower
        <= raster.float32_bits_fraction(
            raster.float32_bits(float(value))
        )
        <= upper
        for value in adjacent
    )
    return selected, ambiguous


def recover_tomography_slope(
    case: TomographyCase,
    *,
    delta_index: int,
    axis: Axis,
    primitive: int,
    mask: UIntSurface,
) -> tuple[TomographySlope, JsonObject]:
    if (
        delta_index == 7
        and axis == "y"
        and case.output_record(7).get("primitiveIDPacking")
            == "channel-3-raw-uint"
    ):
        raise ValueError("delta 7 y pull is reserved for primitive ID")
    surface = case.surface(delta_index)
    first, second = axis_pull_records(
        surface,
        mask,
        axis=axis,
        primitive=primitive,
    )
    if first.keys() != second.keys():
        raise ValueError(
            f"{case.name} {axis} pull coordinates differ"
        )
    origin = case.origin_x if axis == "x" else case.origin_y
    dimension = case.width if axis == "x" else case.height
    delta = Fraction(
        case.numerators[delta_index],
        case.denominator,
    )
    nominal = raster.float32(float(delta / dimension))
    candidate_bits = raster.neighboring_float32_bits(
        nominal,
        SLOPE_SEARCH_RADIUS,
    )
    coordinates_by_tile = raster.tile_coordinates(
        first,
        origin=origin,
    )

    accepted: list[
        tuple[int, dict[int, tuple[int, bool]]]
    ] = []
    for slope_bits in candidate_bits:
        constants_by_tile: dict[int, tuple[int, bool]] = {}
        for tile, coordinates in coordinates_by_tile.items():
            positions: list[float] = []
            targets: list[int] = []
            for coordinate in coordinates:
                tile_position = float((origin + coordinate) % 32)
                positions.extend((
                    tile_position,
                    tile_position + PULL_OFFSET,
                ))
                targets.extend((
                    first[coordinate],
                    second[coordinate],
                ))
            constant = exact_pull_constant(
                positions,
                targets,
                slope_bits,
            )
            if constant is None:
                break
            constants_by_tile[tile] = constant
        else:
            accepted.append((slope_bits, constants_by_tile))

    if not accepted:
        raise ValueError(
            f"{case.name} delta {delta_index} {axis} primitive "
            f"{primitive} has no exact slope"
        )
    accepted.sort(key=lambda item: item[0])
    accepted_bits = frozenset(
        slope_bits
        for slope_bits, _ in accepted
    )
    model = TomographySlope(
        case=case,
        delta_index=delta_index,
        axis=axis,
        primitive=primitive,
        accepted_bits=accepted_bits,
    )
    selected_bits, selected_constants = accepted[0]
    constant_bytes = b"".join(
        struct.pack("<II", tile, constant[0])
        for tile, constant in sorted(selected_constants.items())
    )
    selected_slope = raster.bits_float32(selected_bits)
    selected_mismatches = 0
    observed_values = 0
    for coordinate in first:
        tile = (origin + coordinate) // 32
        position = float((origin + coordinate) % 32)
        constant = raster.bits_float32(
            selected_constants[tile][0]
        )
        expected = (first[coordinate], second[coordinate])
        predicted = (
            raster.pull_iterator_bits(
                position,
                selected_slope,
                constant,
            ),
            raster.pull_iterator_bits(
                position + PULL_OFFSET,
                selected_slope,
                constant,
            ),
        )
        count = int(np.count_nonzero(
            mask[:, coordinate] == primitive
            if axis == "x"
            else mask[coordinate, :] == primitive
        ))
        observed_values += 2 * count
        if predicted != expected:
            selected_mismatches += 2 * count

    return model, {
        "deltaIndex": delta_index,
        "axis": axis,
        "primitive": primitive,
        "nominalSlopeBits":
            f"0x{raster.float32_bits(nominal):08x}",
        "acceptedSlopeBits": [
            f"0x{bits:08x}"
            for bits in sorted(accepted_bits)
        ],
        "acceptedSlopeCount": len(accepted_bits),
        "slopeFullyDetermined": len(accepted_bits) == 1,
        "tileCount": len(selected_constants),
        "ambiguousTileConstantCount": sum(
            ambiguous
            for _, ambiguous in selected_constants.values()
        ),
        "selectedTileCoefficientSha256": hashlib.sha256(
            constant_bytes
        ).hexdigest(),
        "observedFloatValues": observed_values,
        "mismatchedFloatValues": selected_mismatches,
        "exact": selected_mismatches == 0,
    }


def slope_reciprocal_interval(
    observation: TomographySlope,
) -> tuple[Fraction, Fraction]:
    intervals = [
        raster.float32_rounding_interval(bits)
        for bits in observation.accepted_bits
    ]
    return (
        min(lower for lower, _ in intervals)
        / observation.numerator,
        max(upper for _, upper in intervals)
        / observation.numerator,
    )


def predicted_bits(
    observation: TomographySlope,
    reciprocal: Fraction,
) -> int:
    return raster.round_fraction_to_float32_bits(
        observation.numerator * reciprocal
    )


def staged_product_bits(
    observation: TomographySlope,
    reciprocal: Fraction,
    *,
    product_precision_bits: int,
    product_rounding: Literal[
        "down",
        "nearest-even",
        "up",
    ],
) -> int:
    product = raster.quantize_binary_significand_directed(
        observation.numerator * reciprocal,
        product_precision_bits,
        product_rounding,
    )
    return raster.round_fraction_to_float32_bits(product)


def staged_matching_offsets(
    observations: list[TomographySlope],
    *,
    reciprocal_precision_bits: int,
    product_precision_bits: int = 27,
    product_rounding: Literal[
        "down",
        "nearest-even",
        "up",
    ] = "nearest-even",
    offset_radius: int = RECIPROCAL_OFFSET_RADIUS,
) -> list[int]:
    """Return reciprocal lattice offsets accepted by every slope."""

    if not observations:
        raise ValueError("slope observations are required")
    areas = {
        observation.case.width * observation.case.height
        for observation in observations
    }
    if len(areas) != 1:
        raise ValueError(
            "staged reciprocal matching requires one geometry"
        )
    exact_reciprocal = Fraction(1, areas.pop())
    matching: list[int] = []
    for offset in range(-offset_radius, offset_radius + 1):
        reciprocal = raster.quantize_binary_significand(
            exact_reciprocal,
            reciprocal_precision_bits,
            lattice_offset=offset,
        )
        if all(
            staged_product_bits(
                observation,
                reciprocal,
                product_precision_bits=product_precision_bits,
                product_rounding=product_rounding,
            )
            in observation.accepted_bits
            for observation in observations
        ):
            matching.append(offset)
    return matching


def deduplicate_primitive_observations(
    observations: list[TomographySlope],
) -> list[TomographySlope]:
    grouped: dict[
        tuple[str, int, Axis],
        TomographySlope,
    ] = {}
    for observation in observations:
        key = (
            observation.case.name,
            observation.delta_index,
            observation.axis,
        )
        previous = grouped.get(key)
        if previous is None:
            grouped[key] = observation
            continue
        common = previous.accepted_bits & observation.accepted_bits
        if not common:
            raise ValueError(
                f"{key} primitive slope candidates disagree"
            )
        grouped[key] = TomographySlope(
            case=previous.case,
            delta_index=previous.delta_index,
            axis=previous.axis,
            primitive=0,
            accepted_bits=frozenset(common),
        )
    return list(grouped.values())


def staged_setup_hypothesis_report(
    observations: list[TomographySlope],
) -> JsonObject:
    """Search staged precision boundaries on discovery records."""

    records = deduplicate_primitive_observations(observations)
    by_case: dict[str, list[TomographySlope]] = {}
    for record in records:
        by_case.setdefault(record.case.name, []).append(record)

    def evaluate(
        *,
        reciprocal_precision_bits: int,
        product_precision_bits: int,
        product_rounding: Literal[
            "down",
            "nearest-even",
            "up",
        ],
    ) -> tuple[int, int, dict[str, list[int]]]:
        exact_geometries = 0
        best_record_matches = 0
        offsets_by_case: dict[str, list[int]] = {}
        for name, case_records in by_case.items():
            case = case_records[0].case
            exact_reciprocal = Fraction(
                1,
                case.width * case.height,
            )
            matching: list[int] = []
            best = 0
            for offset in range(
                -RECIPROCAL_OFFSET_RADIUS,
                RECIPROCAL_OFFSET_RADIUS + 1,
            ):
                reciprocal = raster.quantize_binary_significand(
                    exact_reciprocal,
                    reciprocal_precision_bits,
                    lattice_offset=offset,
                )
                matches = sum(
                    staged_product_bits(
                        record,
                        reciprocal,
                        product_precision_bits=
                            product_precision_bits,
                        product_rounding=product_rounding,
                    )
                    in record.accepted_bits
                    for record in case_records
                )
                best = max(best, matches)
                if matches == len(case_records):
                    matching.append(offset)
            exact_geometries += bool(matching)
            best_record_matches += best
            offsets_by_case[name] = matching
        return (
            exact_geometries,
            best_record_matches,
            offsets_by_case,
        )

    product_search: list[JsonObject] = []
    for product_precision in range(24, 33):
        for rounding in ("down", "nearest-even", "up"):
            geometries, matches, _ = evaluate(
                reciprocal_precision_bits=29,
                product_precision_bits=product_precision,
                product_rounding=rounding,
            )
            product_search.append({
                "reciprocalLatticePrecisionBits": 29,
                "productPrecisionBits": product_precision,
                "productRounding": rounding,
                "exactGeometryCount": geometries,
                "geometryCount": len(by_case),
                "bestMatchedSlopeRecords": matches,
                "slopeRecordCount": len(records),
                "exact": (
                    geometries == len(by_case)
                    and matches == len(records)
                ),
            })

    reciprocal_search: list[JsonObject] = []
    for reciprocal_precision in range(24, 33):
        geometries, matches, offsets = evaluate(
            reciprocal_precision_bits=reciprocal_precision,
            product_precision_bits=27,
            product_rounding="nearest-even",
        )
        reciprocal_search.append({
            "reciprocalLatticePrecisionBits":
                reciprocal_precision,
            "productPrecisionBits": 27,
            "productRounding": "nearest-even",
            "exactGeometryCount": geometries,
            "geometryCount": len(by_case),
            "bestMatchedSlopeRecords": matches,
            "slopeRecordCount": len(records),
            "matchingOffsetsByCase": offsets,
            "exact": (
                geometries == len(by_case)
                and matches == len(records)
            ),
        })

    direct_float_matches = 0
    exact_27_matches = 0
    for record in records:
        dimension = (
            record.case.width
            if record.axis == "x"
            else record.case.height
        )
        delta = Fraction(
            record.case.numerators[record.delta_index],
            record.case.denominator,
        )
        quotient = delta / dimension
        direct_bits = raster.round_fraction_to_float32_bits(
            quotient
        )
        quantized_bits = raster.round_fraction_to_float32_bits(
            raster.quantize_binary_significand_directed(
                quotient,
                27,
                "nearest-even",
            )
        )
        direct_float_matches += (
            direct_bits in record.accepted_bits
        )
        exact_27_matches += (
            quantized_bits in record.accepted_bits
        )

    exact_products = [
        candidate
        for candidate in product_search
        if candidate["exact"]
    ]
    exact_reciprocals = [
        candidate
        for candidate in reciprocal_search
        if candidate["exact"]
    ]
    minimum_reciprocal_precision = min(
        (
            int(candidate["reciprocalLatticePrecisionBits"])
            for candidate in exact_reciprocals
        ),
        default=None,
    )
    return {
        "status": "discovery-candidate-needs-reciprocal-law",
        "slopeRecordsExcludeDuplicatePrimitive": len(records),
        "geometryCount": len(by_case),
        "baselines": {
            "ordinaryCorrectlyRoundedFloat32Division": {
                "matchedSlopeRecords": direct_float_matches,
                "slopeRecordCount": len(records),
                "exact": direct_float_matches == len(records),
            },
            "exactQuotientRoundedTo27BitsThenFloat32": {
                "matchedSlopeRecords": exact_27_matches,
                "slopeRecordCount": len(records),
                "exact": exact_27_matches == len(records),
            },
        },
        "productPrecisionSearch": product_search,
        "reciprocalPrecisionSearch": reciprocal_search,
        "exactProductConfigurations": exact_products,
        "minimumExactReciprocalLatticePrecisionBits":
            minimum_reciprocal_precision,
        "selectedDiscoveryCandidate": {
            "reciprocalLatticePrecisionBits":
                minimum_reciprocal_precision,
            "productPrecisionBits": 27,
            "productRounding": "nearest-even",
            "finalCoefficientRounding": "float32-nearest-even",
            "exactGeometryCount": len(by_case),
            "exactSlopeRecordCount": len(records),
            "reciprocalApproximationFullyDetermined": False,
            "holdoutAuthorized": False,
        },
        "fullyDetermined": False,
    }


def matching_offsets(
    observations: list[TomographySlope],
    *,
    precision_bits: int,
) -> list[int]:
    if not observations:
        raise ValueError("slope observations are required")
    area = observations[0].case.width * observations[0].case.height
    exact = Fraction(1, area)
    return [
        offset
        for offset in range(
            -RECIPROCAL_OFFSET_RADIUS,
            RECIPROCAL_OFFSET_RADIUS + 1,
        )
        if all(
            predicted_bits(
                observation,
                raster.quantize_binary_significand(
                    exact,
                    precision_bits,
                    lattice_offset=offset,
                ),
            )
            in observation.accepted_bits
            for observation in observations
        )
    ]


def reciprocal_report(
    observations: list[TomographySlope],
) -> JsonObject:
    by_case: dict[str, list[TomographySlope]] = {}
    for observation in observations:
        by_case.setdefault(
            observation.case.name,
            [],
        ).append(observation)

    geometries: list[JsonObject] = []
    for name, records in sorted(by_case.items()):
        case = records[0].case
        area = case.width * case.height
        exact = Fraction(1, area)
        lower = max(
            slope_reciprocal_interval(record)[0]
            for record in records
        )
        upper = min(
            slope_reciprocal_interval(record)[1]
            for record in records
        )
        interval_nonempty = lower < upper
        exponent = raster.floor_binary_exponent(exact)
        step27 = raster.power_of_two(exponent - 26)
        nearest27 = raster.quantize_binary_significand(
            exact,
            27,
        )
        primitive_intervals: dict[str, JsonObject] = {}
        for primitive in (0, 1):
            selected = [
                record
                for record in records
                if record.primitive == primitive
            ]
            primitive_lower = max(
                slope_reciprocal_interval(record)[0]
                for record in selected
            )
            primitive_upper = min(
                slope_reciprocal_interval(record)[1]
                for record in selected
            )
            primitive_intervals[str(primitive)] = {
                "nonempty":
                    primitive_lower < primitive_upper,
                "lowerHexApproximation":
                    float(primitive_lower).hex(),
                "upperHexApproximation":
                    float(primitive_upper).hex(),
                "widthIn27BitSteps": float(
                    (primitive_upper - primitive_lower)
                    / step27
                ),
            }
        precision_candidates = {
            str(precision): matching_offsets(
                records,
                precision_bits=precision,
            )
            for precision in range(24, 33)
        }
        geometries.append({
            "name": name,
            "role": case.role,
            "width": case.width,
            "height": case.height,
            "area": area,
            "observationCount": len(records),
            "acceptedSlopePatternCount": sum(
                len(record.accepted_bits)
                for record in records
            ),
            "continuousInterval": {
                "nonempty": interval_nonempty,
                "lower": raster.fraction_text(lower),
                "upper": raster.fraction_text(upper),
                "lowerHexApproximation": float(lower).hex(),
                "upperHexApproximation": float(upper).hex(),
                "containsExactReciprocal":
                    interval_nonempty and lower <= exact <= upper,
                "widthIn27BitSteps": float(
                    (upper - lower) / step27
                ),
                "midpointErrorIn27BitSteps": float(
                    ((lower + upper) / 2 - exact) / step27
                ),
            },
            "nearest27": {
                "valueHex": float(nearest27).hex(),
                "exactMinusNearestInSteps": float(
                    (exact - nearest27) / step27
                ),
                "contained": lower <= nearest27 <= upper,
            },
            "matchingOffsetsByPrecision":
                precision_candidates,
            "primitiveIntervals": primitive_intervals,
            "slopeConstraintIntervals": [
                {
                    "deltaIndex": record.delta_index,
                    "axis": record.axis,
                    "primitive": record.primitive,
                    "acceptedSlopeBits": [
                        f"0x{bits:08x}"
                        for bits in sorted(record.accepted_bits)
                    ],
                    "lowerHexApproximation": float(
                        slope_reciprocal_interval(record)[0]
                    ).hex(),
                    "upperHexApproximation": float(
                        slope_reciprocal_interval(record)[1]
                    ).hex(),
                    "midpointErrorIn27BitSteps": float(
                        (
                            sum(
                                slope_reciprocal_interval(record)
                            ) / 2
                            - exact
                        )
                        / step27
                    ),
                }
                for record in records
            ],
        })

    all_intervals_nonempty = all(
        bool(geometry["continuousInterval"]["nonempty"])
        for geometry in geometries
    )
    return {
        "status": (
            "measured-intervals-not-yet-portable-law"
            if all_intervals_nonempty
            else "single-shared-inverse-area-expression-falsified"
        ),
        "coefficientExpression": (
            "A = roundFloat32("
            "exactZeroBasedDelta*oppositeEdge*inverseArea)"
        ),
        "precisionSearchBits": [24, 32],
        "latticeOffsetSearchRadius":
            RECIPROCAL_OFFSET_RADIUS,
        "geometries": geometries,
        "allContinuousIntervalsNonempty":
            all_intervals_nonempty,
        "singlePrecisionExplainsEveryGeometry": [
            precision
            for precision in range(24, 33)
            if all(
                geometry["matchingOffsetsByPrecision"][
                    str(precision)
                ]
                for geometry in geometries
            )
        ],
        "fullyDetermined": False,
    }


def analyze_tomography(
    root: Path,
    *,
    role: Role = "discovery",
) -> JsonObject:
    manifest_path = root / "manifest.json"
    cases = load_tomography_cases(root, role=role)
    case_reports: list[JsonObject] = []
    observations: list[TomographySlope] = []
    observed_values = 0
    mismatched_values = 0
    ambiguous_slopes = 0
    ambiguous_constants = 0

    for case in cases:
        mask = case.primitive_mask()
        models: list[JsonObject] = []
        for delta_index in range(8):
            for axis in ("x", "y"):
                if delta_index == 7 and axis == "y":
                    continue
                for primitive in (0, 1):
                    observation, report = recover_tomography_slope(
                        case,
                        delta_index=delta_index,
                        axis=axis,
                        primitive=primitive,
                        mask=mask,
                    )
                    observations.append(observation)
                    models.append(report)
                    observed_values += int(
                        report["observedFloatValues"]
                    )
                    mismatched_values += int(
                        report["mismatchedFloatValues"]
                    )
                    ambiguous_slopes += not bool(
                        report["slopeFullyDetermined"]
                    )
                    ambiguous_constants += int(
                        report["ambiguousTileConstantCount"]
                    )
        case_reports.append({
            "name": case.name,
            "role": case.role,
            "width": case.width,
            "height": case.height,
            "origin": [case.origin_x, case.origin_y],
            "primitivePixelCounts": {
                str(primitive): int(np.count_nonzero(
                    mask == primitive
                ))
                for primitive in (0, 1)
            },
            "models": models,
            "exact": all(
                bool(model["exact"])
                for model in models
            ),
        })

    return {
        "liquidGlassRasterTomographyAnalysisSchemaVersion": 1,
        "probe": str(root),
        "manifestSha256": sha256_file(manifest_path),
        "selectedRole": role,
        "holdoutOpened": role in {"holdout", "all"},
        "pullRule": (
            "roundNearestEvenFMA("
            "A*pixelWithin32x32Tile+C)"
        ),
        "cases": case_reports,
        "inverseArea": reciprocal_report(observations),
        "stagedSetupHypothesis":
            staged_setup_hypothesis_report(observations),
        "measurement": {
            "caseCount": len(cases),
            "slopeModelCount": len(observations),
            "observedFloatValues": observed_values,
            "mismatchedFloatValues": mismatched_values,
            "exact": mismatched_values == 0,
            "ambiguousSlopeModels": ambiguous_slopes,
            "ambiguousTileConstants": ambiguous_constants,
            "portableCoefficientSetupFullyDetermined": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze schema-8 raster reciprocal tomography."
    )
    parser.add_argument("probe", type=Path)
    parser.add_argument(
        "--role",
        choices=("discovery", "holdout", "all"),
        default="discovery",
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze_tomography(
        arguments.probe,
        role=arguments.role,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0 if report["measurement"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
