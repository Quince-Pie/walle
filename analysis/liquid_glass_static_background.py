#!/usr/bin/env python3
"""Construct the canonical static diagnostic wallpaper without capture data."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


type CodeImage = NDArray[np.uint8]


def coordinate_hash_wallpaper_rgba(
    *,
    width: int = 1024,
    height: int = 1024,
) -> CodeImage:
    """Return the rig's opaque one-pixel coordinate hash in source order."""
    if width <= 0 or height <= 0:
        raise ValueError("wallpaper dimensions must be positive")

    columns = np.arange(width, dtype=np.uint32)[None, :]
    rows = np.arange(height, dtype=np.uint32)[:, None]
    with np.errstate(over="ignore"):
        hashed = (
            columns * np.uint32(0x045D9F3B)
            ^ rows * np.uint32(0x119DE1F3)
        )
    red = hashed.astype(np.uint8)
    green = (hashed >> np.uint32(8)).astype(np.uint8)
    blue = (hashed >> np.uint32(16)).astype(np.uint8)
    alpha = np.full((height, width), 255, dtype=np.uint8)
    return np.ascontiguousarray(np.stack((red, green, blue, alpha), axis=2))


def coordinate_hash_prepass_bgra(
    *,
    width: int = 1024,
    height: int = 1024,
) -> CodeImage:
    """Return Apple's top-level BGRA8 readback of the diagnostic wallpaper."""
    rgba = coordinate_hash_wallpaper_rgba(width=width, height=height)
    return np.ascontiguousarray(np.flipud(rgba[..., [2, 1, 0, 3]]))
