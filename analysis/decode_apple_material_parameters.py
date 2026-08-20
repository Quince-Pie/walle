#!/usr/bin/env python3
"""Decode Apple's OWN Liquid Glass material parameters (session 200).

Every law in walle up to this point was fitted from pixels.  This reads the
numbers instead: the lg-test rig's LLDB/Swift-metadata introspection captured
`DesignLibrary.GlassMaterialProvider.Parameters` (1025 bytes) and the
`BackgroundFilter` (504 bytes) it constructs, bitwise, for the four
material/appearance cases on macOS 26.6.1 (25G76), together with the Swift
field descriptors that name every offset.  This script joins the two and
prints the 102-field table.

Inputs (from the rig's Analysis/ directory on the capture host):
  designlibrary_parameters_mixer_basis_*_result.json      - field name/offset map
  designlibrary_material_appearance_parameters_*_result.json - the payload bytes
  designlibrary_background_filter_metadata_*_result.json  - struct layouts

What the table settles that pixels could not:
  * the material's layer inventory and which layers are OFF (lensing,
    controlContentLensing, controlDisplacement, contrastEdge, innerGlow and
    radiosity are all zero for wallpaper glass - walle models none of them,
    correctly);
  * `shadow.offset = (0, 8) pt` - the shadow is DIRECTIONAL, which the
    captures confirm (3.5x more darkening below a circle than above, left and
    right identical) and which walle had been rendering symmetrically;
  * `faceEffects.ycc` - the material's colour law is a black/white/saturation
    remap, and for `clear` it reproduces the exact flat table from two
    constants: out = 0.97 * (0.075 + 1.075 * in), 15/17 levels exact;
  * `backdropScale` 0.25 (regular) / 0.5 (clear) - Apple blurs a reduced
    backdrop;
  * `blur` is a five-distance stack (distances [-24, -1, 0, 0, 0], opacities
    [1, .5, .5, 1, 1], radius 8/3 regular / 2 clear), not the two-Gaussian
    mixture walle fits;
  * `refraction` is per variant (innerHeight 12.0 pt regular / 17.28 pt clear,
    innerAmount -38.4 / -48.0) with an outer lobe at 30% opacity on regular -
    though rescaling walle's pixel-fitted band by those ratios was FALSIFIED
    by the score gate, so the two parametrisations are not the same quantity.

Usage: decode_apple_material_parameters.py --re-dir <dir with the three JSONs>
"""
import argparse
import json
import struct
from pathlib import Path

FLOAT_FIELD_HINTS = ("ycc.", ".opacity", "backdropScale", "contentOpacity",
                     ".hdr", "Shift", "whitePointShift")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--re-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    basis = json.loads(next(args.re_dir.glob("*parameters_mixer_basis*result.json")).read_text())
    payload = json.loads(next(args.re_dir.glob("*material_appearance_parameters_local*result.json")).read_text())
    fields = basis["scalarFields"]
    mats = {n: bytes.fromhex(v["normalizedHex"])
            for v in payload["uniqueNormalizedParameters"].values()
            for n in v["caseNames"]}
    order = ["regular_light", "regular_dark", "clear_light", "clear_dark"]
    table = {}
    print("%-42s %12s %12s %12s %12s" % ("FIELD", *order))
    for name, meta in sorted(fields.items(), key=lambda kv: kv[1]["offset"]):
        fmt = "f" if any(h in name for h in FLOAT_FIELD_HINTS) else "d"
        vals = [struct.unpack_from("<" + fmt, mats[m], meta["offset"])[0] for m in order]
        table[name] = {"offset": meta["offset"], "format": fmt,
                       **dict(zip(order, vals))}
        print("%-42s %12.6g %12.6g %12.6g %12.6g" % (name, *vals))
    if args.out:
        args.out.write_text(json.dumps(table, indent=1))


if __name__ == "__main__":
    main()
