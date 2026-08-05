#!/usr/bin/env python3
"""Load measured production-range square and near-square selector tables."""

import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from liquid_glass_runtime_raster_coefficients import (
    RasterCase,
    determinant_selector_index,
)


WIDTH_FIXED_LOWER = 196_608
SELECTOR_COUNT = 32_769
NEAR_SQUARE_HEIGHT_DELTAS = (
    -256,
    -128,
    -64,
    -32,
    -16,
    -8,
    -4,
    -2,
    -1,
    1,
    2,
    4,
    8,
    16,
    32,
    64,
    128,
    256,
)
NEAR_SQUARE_SELECTOR_COUNT = SELECTOR_COUNT * len(NEAR_SQUARE_HEIGHT_DELTAS)


@dataclass(frozen=True, slots=True)
class SelectorUse:
    table_index: int
    base: int
    selected: int

    @property
    def offset(self) -> int:
        return self.selected - self.base


@dataclass(frozen=True, slots=True)
class SquareSelectorCalibration:
    path: Path
    selectors: tuple[int, ...]
    nearSquarePath: Path | None = None
    nearSquareSelectors: tuple[int, ...] = ()

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        near_square_path: Path | None = None,
    ) -> "SquareSelectorCalibration":
        raw = zlib.decompress(path.read_bytes())
        expected_bytes = SELECTOR_COUNT * 4
        if len(raw) != expected_bytes:
            raise ValueError(
                f"square selector table has {len(raw)} bytes; "
                f"expected {expected_bytes}"
            )
        near_square_selectors: tuple[int, ...] = ()
        if near_square_path is not None:
            near_square_raw = zlib.decompress(near_square_path.read_bytes())
            near_square_expected_bytes = NEAR_SQUARE_SELECTOR_COUNT * 4
            if len(near_square_raw) != near_square_expected_bytes:
                raise ValueError(
                    "near-square selector table has "
                    f"{len(near_square_raw)} bytes; expected "
                    f"{near_square_expected_bytes}"
                )
            near_square_selectors = struct.unpack(
                f"<{NEAR_SQUARE_SELECTOR_COUNT}I",
                near_square_raw,
            )
        return cls(
            path=path,
            selectors=struct.unpack(f"<{SELECTOR_COUNT}I", raw),
            nearSquarePath=near_square_path,
            nearSquareSelectors=near_square_selectors,
        )

    def _near_square_selector(self, case: RasterCase) -> int | None:
        if not self.nearSquareSelectors:
            return None
        orientations = (
            (case.widthFixed, case.heightFixed - case.widthFixed),
            (case.heightFixed, case.widthFixed - case.heightFixed),
        )
        for width_fixed, height_delta in orientations:
            if (
                WIDTH_FIXED_LOWER
                <= width_fixed
                < WIDTH_FIXED_LOWER + SELECTOR_COUNT
                and height_delta in NEAR_SQUARE_HEIGHT_DELTAS
            ):
                delta_index = NEAR_SQUARE_HEIGHT_DELTAS.index(height_delta)
                width_index = width_fixed - WIDTH_FIXED_LOWER
                return self.nearSquareSelectors[
                    delta_index * SELECTOR_COUNT + width_index
                ]
        return None

    def use_for(
        self,
        case: RasterCase,
        base_table: Sequence[int],
    ) -> SelectorUse:
        minimum_extent = min(case.widthFixed, case.heightFixed)
        maximum_extent = max(case.widthFixed, case.heightFixed)
        if not (
            WIDTH_FIXED_LOWER <= minimum_extent
            and maximum_extent < WIDTH_FIXED_LOWER + len(self.selectors)
        ):
            raise ValueError(
                f"{case.name} fixed extents {case.widthFixed}x"
                f"{case.heightFixed} are outside the selector calibration"
            )
        table_index, _ = determinant_selector_index(
            case,
            selector_table_length=len(base_table),
        )
        if case.widthFixed == case.heightFixed:
            selected = self.selectors[
                case.widthFixed - WIDTH_FIXED_LOWER
            ]
        else:
            selected = self._near_square_selector(case)
            if selected is None:
                raise ValueError(
                    f"{case.name} non-square extent {case.widthFixed}x"
                    f"{case.heightFixed} is outside the loaded near-square "
                    "selector calibration"
                )
        return SelectorUse(
            table_index=table_index,
            base=base_table[table_index],
            selected=selected,
        )


def base_selector_use(
    case: RasterCase,
    base_table: Sequence[int],
) -> SelectorUse:
    table_index, _ = determinant_selector_index(
        case,
        selector_table_length=len(base_table),
    )
    selected = base_table[table_index]
    return SelectorUse(
        table_index=table_index,
        base=selected,
        selected=selected,
    )
