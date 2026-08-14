#!/usr/bin/env python3
"""Rank recovered AGX clip deltas against the admitted P25/carry pipeline.

The retained Metal probe exposes generated varying deltas through offset pulls;
it does not read reveal pixels.  This analyzer freezes a record-identity split,
ranks arithmetic policies on discovery records only, and opens the holdout only
for the already-ranked finalists.
"""

import argparse
import functools
import hashlib
import json
import struct
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
LG_ANALYSIS: Final = ROOT / "lg-test" / "Analysis"
sys.path[:0] = [str(ROOT / "analysis"), str(LG_ANALYSIS)]

import analyze_raster_clip_arithmetic_discriminator as clip_analysis  # noqa: E402
import analyze_raster_clip_boundary_tomography as boundary_analysis  # noqa: E402
import analyze_reveal_agx_clip_setup_split as setup_split  # noqa: E402
import infer_raster_clip_algorithm as legacy  # noqa: E402
import model_raster_general_height_arithmetic as two_stage  # noqa: E402
import raster_tile_coefficient_model_v3 as carry_model  # noqa: E402
import validate_raster_clip_arithmetic_discriminator as capture  # noqa: E402


type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type RecoveredRecord = tuple[int, int, int, int, int]

DEFAULT_CAPTURE_ROOT: Final = (
    ROOT
    / "artifacts"
    / "gh-run-30678295250"
    / "liquid-glass-raster-clip-arithmetic-discriminator-30678295250"
)
P25_PATH: Final = ROOT / "parity" / "raster_p25_selector_ceil_bits.bin"
HOLDOUT_THRESHOLD: Final = 64
EXPECTED_RECOVERED_COUNT: Final = 63_735
EXPECTED_DISCOVERY_COUNT: Final = 47_680
EXPECTED_HOLDOUT_COUNT: Final = 16_055
EXPECTED_RECOVERED_SHA256: Final = (
    "cc696508044b82ad83216bbd93ca1e02837a2bf0370e2d4d2ea1c8a9416ab2eb"
)
EXPECTED_HASHES: Final = {
    "analysis/infer_raster_clip_algorithm.py": (
        "be9d3587320ba6037ffd72ce6d92dfec400ef45f95f24cfd8f74f220ff802f17"
    ),
    "analysis/analyze_reveal_agx_clip_setup_split.py": (
        "591f9a9fef2caafe43d4d1464377deeacaf2fd5c057cb1337703ac3a1f4f820c"
    ),
    "lg-test/Analysis/analyze_raster_clip_arithmetic_discriminator.py": (
        "badaca73974c2d42885c29f180fabb171c8574d26ee254f16558f264c4169dea"
    ),
    "lg-test/Analysis/validate_raster_clip_arithmetic_discriminator.py": (
        "8dfc837ae2b31ef5627e3b109138ee2bd1e7e30390a13e8760c4abad900eea95"
    ),
    "lg-test/Analysis/model_raster_general_height_arithmetic.py": (
        "7c8422c940d228eb3c747bc9011abc2b56889865b30d097fdd9d3df1dfd798fb"
    ),
    "lg-test/Analysis/raster_tile_coefficient_model_v3.py": (
        "99c1725d9fdec0877b8510fb92aaa4a4ee398e61b0c579f0b1c1a0471520f1fe"
    ),
    "parity/raster_p25_selector_ceil_bits.bin": (
        "9fbc083dfd9c89fc0bcdc89308acfc4530d408e93789a7dab89ee59ff60a198f"
    ),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Policy:
    route: str
    product: str
    second_bias: int

    @property
    def name(self) -> str:
        return f"{self.route}-{self.product}-bias{self.second_bias}"


@dataclass(slots=True, kw_only=True)
class Score:
    count: int = 0
    exact: int = 0
    within_one: int = 0
    within_two: int = 0
    within_four: int = 0
    absolute_ulp_error: int = 0
    maximum_ulp_error: int = 0

    def add(self, predicted: int, observed: int) -> None:
        delta = abs(predicted - observed)
        self.count += 1
        self.exact += delta == 0
        self.within_one += delta <= 1
        self.within_two += delta <= 2
        self.within_four += delta <= 4
        self.absolute_ulp_error += delta
        self.maximum_ulp_error = max(self.maximum_ulp_error, delta)

    def as_json(self) -> JsonObject:
        return {
            "coefficientCount": self.count,
            "exactCount": self.exact,
            "mismatchCount": self.count - self.exact,
            "withinOneUlpCount": self.within_one,
            "withinTwoUlpCount": self.within_two,
            "withinFourUlpCount": self.within_four,
            "absoluteUlpError": self.absolute_ulp_error,
            "maximumUlpError": self.maximum_ulp_error,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _check_dependencies() -> list[JsonObject]:
    authenticated: list[JsonObject] = []
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"dependency identity differs: {relative}: {actual}")
        authenticated.append(
            {"path": relative, "bytes": path.stat().st_size, "sha256": actual}
        )
    return authenticated


def _is_holdout(record: RecoveredRecord) -> bool:
    distance, witness, source, _exact, _observed = record
    identity = struct.pack("<III", distance, witness, source)
    return hashlib.sha256(identity).digest()[0] < HOLDOUT_THRESHOLD


def _power_of_two(exponent: int) -> Fraction:
    return Fraction(1 << exponent) if exponent >= 0 else Fraction(1, 1 << -exponent)


@functools.cache
def _first_product(source_bits: int, numerator_fixed: int) -> tuple[int, int] | None:
    if numerator_fixed == 0:
        return None
    source_index, source_exponent = setup_split._positive_float_components(  # noqa: SLF001
        source_bits
    )
    numerator_bits = legacy._fixed_bits(numerator_fixed)  # noqa: SLF001
    numerator_index, numerator_exponent = (  # noqa: SLF001
        setup_split._positive_float_components(numerator_bits)
    )
    return two_stage.product_stage(
        source_index,
        source_exponent,
        numerator_index,
        numerator_exponent,
        output_bits=27,
        truncation_bits=16,
        bias_units=15,
    )


def _second_product(
    first: tuple[int, int] | None,
    denominator_fixed: int,
    bitmap: bytes,
    policy: Policy,
) -> Fraction:
    selector, selector_exponent = setup_split._p25_selector(  # noqa: SLF001
        denominator_fixed,
        bitmap,
    )
    # The calibrated selector represents 2^16 / determinant.  A triangle
    # numerator has one fixed edge divided by 2^8, while this one-dimensional
    # ratio already expressed its numerator in pixels.  Remove the extra 2^8.
    selector_exponent -= 8
    return _second_product_with_selector(
        first,
        (selector, selector_exponent),
        policy,
    )


def _second_product_with_selector(
    first: tuple[int, int] | None,
    selector_pair: tuple[int, int],
    policy: Policy,
) -> Fraction:
    if first is None:
        return Fraction(0)
    first_index, first_exponent = first
    selector, selector_exponent = selector_pair
    if policy.product == "simple":
        index, exponent = two_stage.product_stage(
            first_index,
            first_exponent,
            selector,
            selector_exponent,
            output_bits=27,
            truncation_bits=19,
            bias_units=policy.second_bias,
        )
    elif policy.product == "carry":
        index, exponent = carry_model.column_product_stage(
            first_index,
            first_exponent,
            selector,
            selector_exponent,
            output_bits=27,
            truncation_bits=19,
            bias_units=policy.second_bias,
            carry_mode="top-columns",
            propagated_column_count=1,
            sticky_carry_limit=1,
        )
    else:
        raise ValueError(f"unknown product policy: {policy.product}")
    return index * _power_of_two(exponent)


def _predict(
    record: RecoveredRecord,
    bitmap: bytes,
    policy: Policy,
) -> int:
    distance, _witness, source, _exact, _observed = record
    denominator = legacy.SPAN_FIXED + distance
    if policy.route == "keep":
        term = _second_product(
            _first_product(source, legacy.SPAN_FIXED),
            denominator,
            bitmap,
            policy,
        )
        return legacy._round(term)  # noqa: SLF001

    removed = _second_product(
        _first_product(source, distance),
        denominator,
        bitmap,
        policy,
    )
    if policy.route == "remove-staged":
        return legacy._sub(source, legacy._round(removed))  # noqa: SLF001
    if policy.route == "remove-fused":
        return legacy._round(legacy._fraction(source) - removed)  # noqa: SLF001
    high = source - 0x0080_0000
    low = high | 0x8000_0000
    if policy.route == "endpoint-staged":
        generated = legacy._add(low, legacy._round(removed))  # noqa: SLF001
        return legacy._sub(high, generated)  # noqa: SLF001
    if policy.route == "endpoint-fused":
        generated = legacy._round(  # noqa: SLF001
            legacy._fraction(low) + removed  # noqa: SLF001
        )
        return legacy._sub(high, generated)  # noqa: SLF001
    raise ValueError(f"unknown route: {policy.route}")


def _policies() -> tuple[Policy, ...]:
    return tuple(
        Policy(route=route, product=product, second_bias=bias)
        for route in (
            "keep",
            "remove-staged",
            "remove-fused",
            "endpoint-staged",
            "endpoint-fused",
        )
        for product in ("simple", "carry")
        for bias in range(32)
    )


def _score(
    records: list[RecoveredRecord],
    bitmap: bytes,
    policies: tuple[Policy, ...],
) -> dict[str, Score]:
    scores = {policy.name: Score() for policy in policies}
    grouped: dict[tuple[str, int], dict[str, Policy]] = {}
    for policy in policies:
        grouped.setdefault((policy.product, policy.second_bias), {})[policy.route] = (
            policy
        )
    for record in records:
        distance, _witness, source, _exact, observed = record
        denominator = legacy.SPAN_FIXED + distance
        selector, selector_exponent = setup_split._p25_selector(  # noqa: SLF001
            denominator,
            bitmap,
        )
        selector_pair = (selector, selector_exponent - 8)
        keep_first = _first_product(source, legacy.SPAN_FIXED)
        remove_first = _first_product(source, distance)
        for (product, bias), routes in grouped.items():
            representative = next(iter(routes.values()))
            keep = _second_product_with_selector(
                keep_first,
                selector_pair,
                representative,
            )
            removed = _second_product_with_selector(
                remove_first,
                selector_pair,
                representative,
            )
            predictions = {
                "keep": legacy._round(keep),  # noqa: SLF001
                "remove-staged": legacy._sub(  # noqa: SLF001
                    source,
                    legacy._round(removed),  # noqa: SLF001
                ),
                "remove-fused": legacy._round(  # noqa: SLF001
                    legacy._fraction(source) - removed  # noqa: SLF001
                ),
            }
            high = source - 0x0080_0000
            low = high | 0x8000_0000
            generated_staged = legacy._add(  # noqa: SLF001
                low,
                legacy._round(removed),  # noqa: SLF001
            )
            generated_fused = legacy._round(  # noqa: SLF001
                legacy._fraction(low) + removed  # noqa: SLF001
            )
            predictions["endpoint-staged"] = legacy._sub(  # noqa: SLF001
                high,
                generated_staged,
            )
            predictions["endpoint-fused"] = legacy._sub(  # noqa: SLF001
                high,
                generated_fused,
            )
            for route, policy in routes.items():
                scores[policy.name].add(predictions[route], observed)
    return scores


def _ranking(policies: tuple[Policy, ...], scores: dict[str, Score]) -> list[Policy]:
    return sorted(
        policies,
        key=lambda policy: (
            scores[policy.name].count - scores[policy.name].exact,
            scores[policy.name].absolute_ulp_error,
            scores[policy.name].maximum_ulp_error,
            policy.name,
        ),
    )


def _first_mismatches(
    records: list[RecoveredRecord],
    bitmap: bytes,
    policy: Policy,
    *,
    limit: int,
) -> list[JsonObject]:
    mismatches: list[JsonObject] = []
    for distance, witness, source, exact, observed in records:
        record = (distance, witness, source, exact, observed)
        predicted = _predict(record, bitmap, policy)
        if predicted == observed:
            continue
        mismatches.append(
            {
                "distanceFixed": distance,
                "witnessIndex": witness,
                "sourceBits": f"0x{source:08x}",
                "correctlyRoundedRatioBits": f"0x{exact:08x}",
                "predictedBits": f"0x{predicted:08x}",
                "observedBits": f"0x{observed:08x}",
                "predictedMinusObservedFloatUlps": predicted - observed,
            }
        )
        if len(mismatches) == limit:
            break
    return mismatches


def analyze(capture_root: Path) -> JsonObject:
    dependencies = _check_dependencies()
    manifest, raw_path = capture.validate_manifest(capture_root)
    if manifest.get("ciCommit") != clip_analysis.CI_COMMIT:
        raise ValueError("clip capture commit differs")

    records = clip_analysis.load_records(raw_path)
    _, groups = capture.case_catalog()
    legacy_selectors = boundary_analysis.load_fractional_selectors()
    recovered, recovery = clip_analysis.recover_matched_scale_effective_deltas(
        records,
        groups,
        legacy_selectors,
    )
    if len(recovered) != EXPECTED_RECOVERED_COUNT:
        raise ValueError("unique recovered coefficient count differs")
    if recovery.get("effectiveDeltaStreamSha256") != EXPECTED_RECOVERED_SHA256:
        raise ValueError("recovered effective-delta stream identity differs")

    discovery = [record for record in recovered if not _is_holdout(record)]
    holdout = [record for record in recovered if _is_holdout(record)]
    if (len(discovery), len(holdout)) != (
        EXPECTED_DISCOVERY_COUNT,
        EXPECTED_HOLDOUT_COUNT,
    ):
        raise ValueError("discovery/holdout census differs")

    bitmap = P25_PATH.read_bytes()
    policies = _policies()
    discovery_scores = _score(discovery, bitmap, policies)
    ranked = _ranking(policies, discovery_scores)

    finalists = tuple(ranked[:16])
    holdout_scores = _score(holdout, bitmap, finalists)
    winner = finalists[0]
    winner_full = Score()
    for split_scores in (discovery_scores, holdout_scores):
        current = split_scores[winner.name]
        winner_full.count += current.count
        winner_full.exact += current.exact
        winner_full.within_one += current.within_one
        winner_full.within_two += current.within_two
        winner_full.within_four += current.within_four
        winner_full.absolute_ulp_error += current.absolute_ulp_error
        winner_full.maximum_ulp_error = max(
            winner_full.maximum_ulp_error,
            current.maximum_ulp_error,
        )

    return {
        "schemaVersion": 1,
        "classification": "output-blind AGX clip P25/carry discovery-holdout replay",
        "source": {
            "captureRoot": str(capture_root),
            "captureCommit": clip_analysis.CI_COMMIT,
            "manifestSha256": _sha256(capture_root / "manifest.json"),
            "rawSha256": _sha256(raw_path),
            "dependencies": dependencies,
        },
        "recovery": recovery,
        "split": {
            "identity": "sha256(le32(distanceFixed,witnessIndex,sourceBits))",
            "holdoutPredicate": f"digest[0] < {HOLDOUT_THRESHOLD}",
            "discoveryCount": len(discovery),
            "holdoutCount": len(holdout),
            "holdoutOpenedAfterDiscoveryRanking": True,
        },
        "modelSpace": {
            "modelCount": len(policies),
            "firstStage": {
                "outputBits": 27,
                "truncationBits": 16,
                "biasUnits": 15,
            },
            "reciprocal": "P25 calibrated selector with one-dimensional exponent adjustment",
            "secondStage": {
                "outputBits": 27,
                "truncationBits": 19,
                "biasUnits": list(range(32)),
                "products": ["simple partial-product truncation", "top-column carry"],
            },
            "routes": [
                "direct retained-span product",
                "binary32-materialized removal then subtraction",
                "internal-dyadic removal with one final rounding",
                "materialized removal added to the low endpoint, then subtracted from high",
                "internal-dyadic removal added to the low endpoint, then subtracted from high",
            ],
        },
        "discoveryRanking": [
            {"name": policy.name, **discovery_scores[policy.name].as_json()}
            for policy in ranked
        ],
        "holdoutFinalists": [
            {
                "name": policy.name,
                "discovery": discovery_scores[policy.name].as_json(),
                "holdout": holdout_scores[policy.name].as_json(),
            }
            for policy in finalists
        ],
        "winner": {
            "name": winner.name,
            "discovery": discovery_scores[winner.name].as_json(),
            "holdout": holdout_scores[winner.name].as_json(),
            "combined": winner_full.as_json(),
            "firstDiscoveryMismatches": _first_mismatches(
                discovery,
                bitmap,
                winner,
                limit=24,
            ),
            "firstHoldoutMismatches": _first_mismatches(
                holdout,
                bitmap,
                winner,
                limit=24,
            ),
        },
        "authority": {
            "referencePixelsRead": False,
            "renderedCoverageRead": False,
            "capturedCoefficientPullsRead": True,
            "clipGeneratedAttributeLawRecovered": winner_full.exact
            == winner_full.count,
            "productionIntegrationAuthorized": False,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = analyze(args.capture_root)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
