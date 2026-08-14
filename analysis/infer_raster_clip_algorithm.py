#!/usr/bin/env python3
"""Rank concrete clip-interpolation algorithms against the retained M1 oracle.

The source capture varies a rectangle edge across the AGX guard boundary while
holding its post-clip span fixed.  Its fragment program exposes enough
interpolant pulls to recover the effective generated varying delta without
reading a rendered reference image.  This program compares that recovered
mapping with concrete, ordered IEEE/Apple arithmetic expressions.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import struct
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
LG_ANALYSIS: Final = ROOT / "lg-test" / "Analysis"
sys.path.insert(0, str(LG_ANALYSIS))

import analyze_raster_clip_arithmetic_discriminator as clip_analysis  # noqa: E402
import analyze_raster_clip_boundary_tomography as boundary_analysis  # noqa: E402
import model_raster_general_height_arithmetic as two_stage  # noqa: E402
import validate_raster_clip_arithmetic_discriminator as capture  # noqa: E402


type JsonObject = dict[str, object]
type Model = Callable[[int, int], int]

SPAN_FIXED: Final = 5 * 256 * capture.UNITS_PER_PIXEL // 4
RECIPROCAL_DELTAS: Final = (
    ROOT
    / "artifacts"
    / "gh-run-30556057571"
    / "liquid-glass-float-intrinsic-probe-30556057571"
    / "float-fast-reciprocal-deltas-i8.bin"
)
RECIPROCAL_DELTAS_SHA256: Final = (
    "4f3d7ead253db2f8f51b561b94ed858c5b21c1419d6184b8b4f48bd3027d6916"
)


@dataclass(frozen=True, slots=True)
class Context:
    delta: int
    low: int
    high: int
    span: int
    distance: int
    denominator: int
    keep_ieee: int
    remove_ieee: int
    keep_fast: int
    remove_fast: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _fraction(bits: int) -> Fraction:
    return boundary_analysis.float32_fraction(bits)


def _round(value: Fraction) -> int:
    return boundary_analysis.fraction_float32_bits(value)


def _add(left: int, right: int) -> int:
    return _round(_fraction(left) + _fraction(right))


def _sub(left: int, right: int) -> int:
    return _round(_fraction(left) - _fraction(right))


def _mul(left: int, right: int) -> int:
    return _round(_fraction(left) * _fraction(right))


def _div(left: int, right: int) -> int:
    return _round(_fraction(left) / _fraction(right))


def _fma(left: int, right: int, addend: int) -> int:
    return _round(_fraction(left) * _fraction(right) + _fraction(addend))


def _negate(bits: int) -> int:
    return bits ^ 0x8000_0000


def _fixed_bits(value: int) -> int:
    return _round(Fraction(value, capture.UNITS_PER_PIXEL))


def _fast_reciprocal(bits: int, deltas: bytes) -> int:
    value = _fraction(bits)
    if value <= 0:
        raise ValueError("the clip denominator must be positive")
    reciprocal = _round(1 / value)
    mantissa = bits & 0x007F_FFFF
    correction = struct.unpack_from("<b", deltas, mantissa)[0]
    result = reciprocal + correction
    if result & 0x7F80_0000 == 0x7F80_0000:
        raise ValueError("fast reciprocal correction escaped the finite range")
    return result


def _rounding_interval(bits: int) -> tuple[Fraction, Fraction]:
    if bits == 0 or bits & 0x7F80_0000 == 0x7F80_0000:
        raise ValueError("factor recovery requires a positive finite normal")
    value = _fraction(bits)
    return ((_fraction(bits - 1) + value) / 2, (value + _fraction(bits + 1)) / 2)


def _recover_single_multiply_factors(
    recovered: list[tuple[int, int, int, int, int]],
) -> JsonObject:
    by_distance: dict[int, list[tuple[int, int]]] = {}
    for distance, _witness, source, _exact, observed in recovered:
        by_distance.setdefault(distance, []).append((source, observed))

    classifications: Counter[str] = Counter()
    exact_ratio_inside = 0
    rounded_ratio_inside = 0
    first: list[JsonObject] = []
    for distance, observations in sorted(by_distance.items()):
        lower: Fraction | None = None
        upper: Fraction | None = None
        for source, observed in observations:
            output_lower, output_upper = _rounding_interval(observed)
            source_value = _fraction(source)
            candidate_lower = output_lower / source_value
            candidate_upper = output_upper / source_value
            lower = candidate_lower if lower is None else max(lower, candidate_lower)
            upper = candidate_upper if upper is None else min(upper, candidate_upper)
        if lower is None or upper is None:
            raise AssertionError("a recovered distance has no observations")

        exact_ratio = Fraction(SPAN_FIXED, SPAN_FIXED + distance)
        rounded_ratio = _round(exact_ratio)
        exact_ratio_inside += lower <= exact_ratio <= upper
        rounded_ratio_inside += lower <= _fraction(rounded_ratio) <= upper
        candidates: list[int] = []
        if lower <= upper:
            center = _round((lower + upper) / 2)
            for bits in range(max(1, center - 16), center + 17):
                if lower <= _fraction(bits) <= upper:
                    candidates.append(bits)
        if lower > upper:
            classification = "empty-real-factor-intersection"
        elif len(candidates) == 1:
            classification = "unique-float32-factor"
        elif candidates:
            classification = "multiple-float32-factors"
        else:
            classification = "real-factor-but-no-float32"
        classifications[classification] += 1
        if len(first) < 32 and (
            classification != "unique-float32-factor" or candidates[0] != rounded_ratio
        ):
            first.append(
                {
                    "distanceFixed": distance,
                    "observationCount": len(observations),
                    "classification": classification,
                    "exactRatioInsideInterval": lower <= exact_ratio <= upper,
                    "roundedRatioBits": f"0x{rounded_ratio:08x}",
                    "candidateFactorBits": [
                        f"0x{candidate:08x}" for candidate in candidates
                    ],
                }
            )
    return {
        "distanceCount": len(by_distance),
        "classificationCounts": dict(sorted(classifications.items())),
        "exactRatioInsideIntervalCount": exact_ratio_inside,
        "roundedRatioInsideIntervalCount": rounded_ratio_inside,
        "firstNontrivialDistances": first,
    }


def _normalized_index(value: Fraction, width: int) -> tuple[int, int]:
    if value <= 0:
        raise ValueError("a positive factor is required")
    exponent = value.numerator.bit_length() - value.denominator.bit_length()
    if value < boundary_analysis.power_of_two(exponent):
        exponent -= 1
    lsb_exponent = exponent - width + 1
    scaled = value / boundary_analysis.power_of_two(lsb_exponent)
    index, remainder = divmod(scaled.numerator, scaled.denominator)
    doubled = 2 * remainder
    if doubled > scaled.denominator or (doubled == scaled.denominator and index & 1):
        index += 1
    if index == 1 << width:
        index >>= 1
        lsb_exponent += 1
    return index, lsb_exponent


def _direct_multiplier_output(
    delta_bits: int,
    multiplier_index: int,
    multiplier_exponent: int,
    *,
    remove: bool,
    output_bits: int,
    truncation_bits: int,
    bias_units: int,
) -> int:
    delta_index, delta_exponent = two_stage.general.float_significand_and_lsb_exponent(
        delta_bits
    )
    result_index, result_exponent = two_stage.product_stage(
        delta_index,
        delta_exponent,
        multiplier_index,
        multiplier_exponent,
        output_bits=output_bits,
        truncation_bits=truncation_bits,
        bias_units=bias_units,
    )
    term = _round(
        Fraction(result_index) * boundary_analysis.power_of_two(result_exponent)
    )
    return _sub(delta_bits, term) if remove else term


def _recover_direct_multiplier_table(
    recovered: list[tuple[int, int, int, int, int]],
    *,
    remove: bool,
    multiplier_bits: int,
    output_bits: int,
    truncation_bits: int,
    bias_units: int,
    radius: int,
) -> JsonObject:
    by_distance: dict[int, list[tuple[int, int]]] = {}
    for distance, _witness, source, _exact, observed in recovered:
        by_distance.setdefault(distance, []).append((source, observed))

    multiplicities: Counter[int] = Counter()
    unique_offsets: Counter[int] = Counter()
    first_nonunique: list[JsonObject] = []
    for distance, observations in sorted(by_distance.items()):
        if remove and distance == 0:
            if not all(source == observed for source, observed in observations):
                raise AssertionError("zero-distance removal is not identity")
            multiplicities[1] += 1
            unique_offsets[0] += 1
            continue
        factor = Fraction(
            distance if remove else SPAN_FIXED,
            SPAN_FIXED + distance,
        )
        center, exponent = _normalized_index(factor, multiplier_bits)
        accepted: list[int] = []
        for candidate in range(max(1, center - radius), center + radius + 1):
            if all(
                _direct_multiplier_output(
                    source,
                    candidate,
                    exponent,
                    remove=remove,
                    output_bits=output_bits,
                    truncation_bits=truncation_bits,
                    bias_units=bias_units,
                )
                == observed
                for source, observed in observations
            ):
                accepted.append(candidate)
        multiplicities[len(accepted)] += 1
        if len(accepted) == 1:
            unique_offsets[accepted[0] - center] += 1
        elif len(first_nonunique) < 32:
            first_nonunique.append(
                {
                    "distanceFixed": distance,
                    "observationCount": len(observations),
                    "centerIndex": center,
                    "acceptedIndexOffsets": [value - center for value in accepted],
                }
            )
    return {
        "removeThenSubtract": remove,
        "multiplierBits": multiplier_bits,
        "outputBits": output_bits,
        "truncationBits": truncation_bits,
        "biasUnits": bias_units,
        "searchRadius": radius,
        "distanceCount": len(by_distance),
        "candidateMultiplicity": {
            str(count): distances for count, distances in sorted(multiplicities.items())
        },
        "uniqueIndexOffsetFromRne": {
            str(offset): count for offset, count in sorted(unique_offsets.items())
        },
        "firstNonuniqueDistances": first_nonunique,
    }


def _original_triangle_plane_gate(
    records: clip_analysis.RecordArray,
    groups: tuple[capture.ProbeGroup, ...],
    selectors: tuple[int, ...],
) -> JsonObject:
    by_group: dict[str, JsonObject] = {}
    total = 0
    accepted = 0
    first_failures: list[JsonObject] = []
    for group in groups:
        group_total = capture.DISTANCE_COUNT * capture.WITNESS_COUNT
        group_accepted = 0
        for distance in range(capture.DISTANCE_COUNT):
            width_fixed = group.post_clip_span_fixed + distance
            for witness, source in enumerate(capture.DELTA_BITS):
                slope = boundary_analysis.modeled_slope(
                    selectors,
                    source,
                    width_fixed=width_fixed,
                    height_fixed=group.cross_span * capture.UNITS_PER_PIXEL,
                )
                matches = clip_analysis.accepts_slope(
                    clip_analysis.case_records(records, group, distance),
                    witness_index=witness,
                    slope_bits=slope,
                )
                group_accepted += matches
                if not matches and len(first_failures) < 32:
                    first_failures.append(
                        {
                            "group": group.name,
                            "distanceFixed": distance,
                            "witnessIndex": witness,
                            "sourceBits": f"0x{source:08x}",
                            "predictedSlopeBits": f"0x{slope:08x}",
                        }
                    )
        by_group[group.name] = {
            "coefficientCount": group_total,
            "acceptedCount": group_accepted,
            "rejectedCount": group_total - group_accepted,
        }
        total += group_total
        accepted += group_accepted
    return {
        "model": "original-unclipped-triangle-plane-reused-after-clipping",
        "coefficientCount": total,
        "acceptedCount": accepted,
        "rejectedCount": total - accepted,
        "exact": accepted == total,
        "byGroup": by_group,
        "firstFailures": first_failures,
    }


def _context(delta_bits: int, distance_fixed: int, deltas: bytes) -> Context:
    half_bits = delta_bits - 0x0080_0000
    low_bits = half_bits | 0x8000_0000
    span_bits = _fixed_bits(SPAN_FIXED)
    distance_bits = _fixed_bits(distance_fixed)
    denominator_bits = _fixed_bits(SPAN_FIXED + distance_fixed)
    one_bits = 0x3F80_0000
    reciprocal_ieee = _div(one_bits, denominator_bits)
    reciprocal_fast = _fast_reciprocal(denominator_bits, deltas)
    return Context(
        delta=delta_bits,
        low=low_bits,
        high=half_bits,
        span=span_bits,
        distance=distance_bits,
        denominator=denominator_bits,
        keep_ieee=_mul(span_bits, reciprocal_ieee),
        remove_ieee=_mul(distance_bits, reciprocal_ieee),
        keep_fast=_mul(span_bits, reciprocal_fast),
        remove_fast=_mul(distance_bits, reciprocal_fast),
    )


def _selector_index(value_fixed: int, selector_count: int) -> int:
    exponent = value_fixed.bit_length() - 1
    if exponent <= 23:
        normalized = value_fixed << (23 - exponent)
    else:
        scaled = Fraction(value_fixed, 1 << (exponent - 23))
        normalized, remainder = divmod(scaled.numerator, scaled.denominator)
        doubled = 2 * remainder
        if doubled > scaled.denominator or (
            doubled == scaled.denominator and normalized & 1
        ):
            normalized += 1
    if normalized == 1 << 24:
        normalized >>= 1
    mantissa = normalized - (1 << 23)
    quantized = ((mantissa + 2) // 4) * 4
    index = quantized // 4
    if not 0 <= index < selector_count:
        raise ValueError("clip denominator escaped the reciprocal selector table")
    return index


def _two_stage_ratio(
    delta_bits: int,
    numerator_fixed: int,
    denominator_fixed: int,
    selectors: tuple[int, ...],
    *,
    first_bias: int,
    first_truncation: int = 16,
    reciprocal_bias: int = 20,
    reciprocal_truncation: int = 19,
) -> int:
    if numerator_fixed == 0:
        return 0
    delta_index, delta_exponent = two_stage.general.float_significand_and_lsb_exponent(
        delta_bits
    )
    numerator_bits = _fixed_bits(numerator_fixed)
    numerator_scale_index, numerator_scale_exponent = (
        two_stage.general.float_significand_and_lsb_exponent(numerator_bits)
    )
    numerator_index, numerator_exponent = two_stage.product_stage(
        delta_index,
        delta_exponent,
        numerator_scale_index,
        numerator_scale_exponent,
        output_bits=27,
        truncation_bits=first_truncation,
        bias_units=first_bias,
    )
    selector = selectors[_selector_index(denominator_fixed, len(selectors))]
    # One fixed-point denominator carries eight fractional-coordinate bits.
    selector_exponent = -(denominator_fixed - 1).bit_length() - 24 + 8
    result_index, result_exponent = two_stage.product_stage(
        numerator_index,
        numerator_exponent,
        selector,
        selector_exponent,
        output_bits=27,
        truncation_bits=reciprocal_truncation,
        bias_units=reciprocal_bias,
    )
    return _round(
        Fraction(result_index) * boundary_analysis.power_of_two(result_exponent)
    )


def _two_stage_keep(
    delta_bits: int,
    distance_fixed: int,
    selectors: tuple[int, ...],
    *,
    first_bias: int,
    remove: bool,
) -> int:
    term = _two_stage_ratio(
        delta_bits,
        distance_fixed if remove else SPAN_FIXED,
        SPAN_FIXED + distance_fixed,
        selectors,
        first_bias=first_bias,
    )
    return _sub(delta_bits, term) if remove else term


def _models(deltas: bytes, selectors: tuple[int, ...]) -> dict[str, Model]:
    one = 0x3F80_0000

    @functools.cache
    def context(delta: int, distance: int) -> Context:
        return _context(delta, distance, deltas)

    def weighted_numerator_staged(context: Context) -> int:
        return _add(
            _mul(context.low, context.span),
            _mul(context.high, context.distance),
        )

    def weighted_numerator_low_fma(context: Context) -> int:
        return _fma(
            context.low,
            context.span,
            _mul(context.high, context.distance),
        )

    def weighted_numerator_high_fma(context: Context) -> int:
        return _fma(
            context.high,
            context.distance,
            _mul(context.low, context.span),
        )

    def weighted_effective(
        delta: int,
        distance: int,
        numerator: Callable[[Context], int],
        *,
        fast: bool,
    ) -> int:
        current = context(delta, distance)
        generated = _mul(
            numerator(current),
            _fast_reciprocal(current.denominator, deltas)
            if fast
            else _div(one, current.denominator),
        )
        return _sub(current.high, generated)

    models: dict[str, Model] = {
        "exact-ratio-rne": lambda delta, distance: _round(
            _fraction(delta) * Fraction(SPAN_FIXED, SPAN_FIXED + distance)
        ),
        "delta-times-ieee-divided-factor": lambda delta, distance: _mul(
            delta,
            _div(
                _fixed_bits(SPAN_FIXED),
                _fixed_bits(SPAN_FIXED + distance),
            ),
        ),
        "delta-times-ieee-reciprocal-factor": lambda delta, distance: _mul(
            delta, context(delta, distance).keep_ieee
        ),
        "delta-times-fast-reciprocal-factor": lambda delta, distance: _mul(
            delta, context(delta, distance).keep_fast
        ),
        "delta-over-denominator-times-span": lambda delta, distance: _mul(
            _div(delta, context(delta, distance).denominator),
            context(delta, distance).span,
        ),
        "delta-times-span-over-denominator": lambda delta, distance: _div(
            _mul(delta, context(delta, distance).span),
            context(delta, distance).denominator,
        ),
        "delta-minus-staged-ieee-removal": lambda delta, distance: _sub(
            delta,
            _mul(delta, context(delta, distance).remove_ieee),
        ),
        "delta-minus-fused-ieee-removal": lambda delta, distance: _fma(
            _negate(delta), context(delta, distance).remove_ieee, delta
        ),
        "delta-minus-staged-fast-removal": lambda delta, distance: _sub(
            delta,
            _mul(delta, context(delta, distance).remove_fast),
        ),
        "delta-minus-fused-fast-removal": lambda delta, distance: _fma(
            _negate(delta), context(delta, distance).remove_fast, delta
        ),
        "delta-times-one-minus-ieee-removal": lambda delta, distance: _mul(
            delta, _sub(one, context(delta, distance).remove_ieee)
        ),
        "delta-times-one-minus-fast-removal": lambda delta, distance: _mul(
            delta, _sub(one, context(delta, distance).remove_fast)
        ),
        "weighted-endpoints-staged-ieee": lambda delta, distance: weighted_effective(
            delta,
            distance,
            weighted_numerator_staged,
            fast=False,
        ),
        "weighted-endpoints-staged-fast": lambda delta, distance: weighted_effective(
            delta,
            distance,
            weighted_numerator_staged,
            fast=True,
        ),
        "weighted-endpoints-low-fma-ieee": lambda delta, distance: weighted_effective(
            delta,
            distance,
            weighted_numerator_low_fma,
            fast=False,
        ),
        "weighted-endpoints-low-fma-fast": lambda delta, distance: weighted_effective(
            delta,
            distance,
            weighted_numerator_low_fma,
            fast=True,
        ),
        "weighted-endpoints-high-fma-ieee": lambda delta, distance: weighted_effective(
            delta,
            distance,
            weighted_numerator_high_fma,
            fast=False,
        ),
        "weighted-endpoints-high-fma-fast": lambda delta, distance: weighted_effective(
            delta,
            distance,
            weighted_numerator_high_fma,
            fast=True,
        ),
        "weighted-endpoints-exact-ratio": lambda delta, distance: _sub(
            context(delta, distance).high,
            _round(
                (
                    _fraction(context(delta, distance).low)
                    * _fraction(context(delta, distance).span)
                    + _fraction(context(delta, distance).high)
                    * _fraction(context(delta, distance).distance)
                )
                / _fraction(context(delta, distance).denominator)
            ),
        ),
        "staged-low-plus-ieee-remove-then-subtract": lambda delta, distance: _sub(
            context(delta, distance).high,
            _add(
                context(delta, distance).low,
                _mul(delta, context(delta, distance).remove_ieee),
            ),
        ),
        "fused-low-plus-ieee-remove-then-subtract": lambda delta, distance: _sub(
            context(delta, distance).high,
            _fma(
                delta,
                context(delta, distance).remove_ieee,
                context(delta, distance).low,
            ),
        ),
        "staged-low-plus-fast-remove-then-subtract": lambda delta, distance: _sub(
            context(delta, distance).high,
            _add(
                context(delta, distance).low,
                _mul(delta, context(delta, distance).remove_fast),
            ),
        ),
        "fused-low-plus-fast-remove-then-subtract": lambda delta, distance: _sub(
            context(delta, distance).high,
            _fma(
                delta,
                context(delta, distance).remove_fast,
                context(delta, distance).low,
            ),
        ),
        "staged-high-minus-ieee-remove-then-subtract-low": lambda delta, distance: _sub(
            _sub(
                context(delta, distance).high,
                _mul(delta, context(delta, distance).remove_ieee),
            ),
            context(delta, distance).low,
        ),
        "fused-high-minus-ieee-remove-then-subtract-low": lambda delta, distance: _sub(
            _fma(
                _negate(delta),
                context(delta, distance).remove_ieee,
                context(delta, distance).high,
            ),
            context(delta, distance).low,
        ),
        "staged-high-minus-fast-remove-then-subtract-low": lambda delta, distance: _sub(
            _sub(
                context(delta, distance).high,
                _mul(delta, context(delta, distance).remove_fast),
            ),
            context(delta, distance).low,
        ),
        "fused-high-minus-fast-remove-then-subtract-low": lambda delta, distance: _sub(
            _fma(
                _negate(delta),
                context(delta, distance).remove_fast,
                context(delta, distance).high,
            ),
            context(delta, distance).low,
        ),
    }
    for first_bias in (14, 15):
        models[f"agx-two-stage-{first_bias}-20"] = (
            lambda delta, distance, first_bias=first_bias: _two_stage_keep(
                delta,
                distance,
                selectors,
                first_bias=first_bias,
                remove=False,
            )
        )
        models[f"delta-minus-agx-two-stage-removal-{first_bias}-20"] = (
            lambda delta, distance, first_bias=first_bias: _two_stage_keep(
                delta,
                distance,
                selectors,
                first_bias=first_bias,
                remove=True,
            )
        )
    return models


def analyze(root: Path) -> JsonObject:
    manifest, raw_path = capture.validate_manifest(root)
    if manifest.get("ciCommit") != clip_analysis.CI_COMMIT:
        raise ValueError("clip-arithmetic capture commit differs")
    records = clip_analysis.load_records(raw_path)
    _, groups = capture.case_catalog()
    selectors = boundary_analysis.load_fractional_selectors()
    recovered, recovery = clip_analysis.recover_matched_scale_effective_deltas(
        records,
        groups,
        selectors,
    )
    if _sha256(RECIPROCAL_DELTAS) != RECIPROCAL_DELTAS_SHA256:
        raise ValueError("Apple fast-reciprocal table identity differs")
    deltas = RECIPROCAL_DELTAS.read_bytes()

    ranked: list[JsonObject] = []
    for name, model in _models(deltas, selectors).items():
        offsets: Counter[int] = Counter()
        first_mismatches: list[JsonObject] = []
        matches = 0
        for distance, witness, source, exact, observed in recovered:
            predicted = model(source, distance)
            matches += predicted == observed
            offsets[predicted - observed] += 1
            if predicted != observed and len(first_mismatches) < 16:
                first_mismatches.append(
                    {
                        "distanceFixed": distance,
                        "witnessIndex": witness,
                        "sourceBits": f"0x{source:08x}",
                        "exactRatioBits": f"0x{exact:08x}",
                        "predictedBits": f"0x{predicted:08x}",
                        "observedBits": f"0x{observed:08x}",
                        "predictedMinusObservedFloatUlps": predicted - observed,
                    }
                )
        ranked.append(
            {
                "name": name,
                "coefficientCount": len(recovered),
                "matchCount": matches,
                "mismatchCount": len(recovered) - matches,
                "predictedMinusObservedFloatUlpDistribution": {
                    str(offset): count for offset, count in sorted(offsets.items())
                },
                "firstMismatches": first_mismatches,
            }
        )
    ranked.sort(key=lambda result: (int(result["mismatchCount"]), str(result["name"])))
    return {
        "schemaVersion": 1,
        "classification": "output-blind concrete AGX clip arithmetic inference",
        "source": {
            "captureCommit": clip_analysis.CI_COMMIT,
            "manifestSha256": clip_analysis.MANIFEST_SHA256,
            "rawSha256": clip_analysis.RAW_SHA256,
            "fastReciprocalDeltasSha256": RECIPROCAL_DELTAS_SHA256,
        },
        "recovery": recovery,
        "originalTrianglePlaneGate": _original_triangle_plane_gate(
            records,
            groups,
            selectors,
        ),
        "singleRoundedMultiplyFactorRecovery": (
            _recover_single_multiply_factors(recovered)
        ),
        "directFixedMultiplierRecovery": [
            _recover_direct_multiplier_table(
                recovered,
                remove=remove,
                multiplier_bits=25,
                output_bits=27,
                truncation_bits=19,
                bias_units=20,
                radius=64,
            )
            for remove in (False, True)
        ],
        "modelCount": len(ranked),
        "ranking": ranked,
        "referencePixelsRead": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = analyze(args.root)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
