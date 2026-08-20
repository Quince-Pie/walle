#!/usr/bin/env python3
"""One typed reader for DesignLibrary's decoded parameter blobs.

The field table records each scalar's byte offset and a type string that is
either `Double` or `Float`.  A reader that tests for anything else - the
Core Graphics spelling `CGFloat`, say - falls through to the Float branch
and reads a Double's low four bytes: zero for a small round value, garbage
for a large one.  That failure is silent and convincing, because the fields
that happen to be genuinely Float keep working, and it cost this campaign a
published "the offsets are wrong" conclusion that was entirely the reader's
fault.

So the dispatch lives here, once, and every instrument imports it.

The other trap this module exists to prevent: the `_nil` case.  Apple's
parameters are SIZE-DEPENDENT, and the table decoded without shape
dimensions is a degenerate no-geometry default whose values differ
materially from the ones any real element uses - refraction.innerHeight
12.0 against 20.0, faceEffects.ycc.black 0.85 against 0.50.  Session 201
mis-fitted the lens by trusting it.  `read` therefore takes an explicit
case name and `size_law` reports how a field actually varies with the
element, so a constant is never inferred from a single sample.

Usage as a library:

    from apple_parameters import ParameterTable
    table = ParameterTable(decode_dir)
    table.read("regular_light_640", "faceEffects.ycc.black")   # -> 0.50
    table.size_law("regular_light", "edgeBleed.height")        # -> linear 0.35 * size
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

CONTEXT = "designlibrary_material_context_parameters_local_macos_26_6_1_result.json"
BASIS = "designlibrary_parameters_mixer_basis_local_macos_26_6_1_result.json"


class ParameterTable:
    def __init__(self, decode_dir: Path):
        decode_dir = Path(decode_dir)
        context = json.loads((decode_dir / CONTEXT).read_text())
        self.fields = json.loads((decode_dir / BASIS).read_text())["scalarFields"]
        self.blobs = {name: bytes.fromhex(entry["normalizedHex"])
                      for entry in context["uniqueNormalizedParameters"].values()
                      for name in entry["caseNames"]}
        kinds = {spec["type"] for spec in self.fields.values()}
        unexpected = kinds - {"Double", "Float"}
        if unexpected:
            raise ValueError(f"unhandled scalar types in the table: {sorted(unexpected)}")

    # -- reading ---------------------------------------------------------
    def cases(self) -> list[str]:
        return sorted(self.blobs)

    def names(self) -> list[str]:
        return sorted(self.fields)

    def read(self, case: str, field: str) -> float:
        spec = self.fields[field]
        fmt = "<d" if spec["type"] == "Double" else "<f"
        return struct.unpack_from(fmt, self.blobs[case], spec["offset"])[0]

    def sizes_for(self, prefix: str) -> list[tuple[int, str]]:
        """(size, case) for every sized case of a variant/appearance prefix,
        excluding `_nil` and any range/derived case."""
        out = []
        for case in self.blobs:
            if not case.startswith(prefix + "_"):
                continue
            tail = case[len(prefix) + 1:]
            if not tail.isdigit():
                continue                       # _nil, _range_*, _127_5, ...
            out.append((int(tail), case))
        return sorted(out)

    # -- structure -------------------------------------------------------
    def size_law(self, prefix: str, field: str, tolerance: float = 1e-6) -> dict:
        """Describe how a field varies with element size: constant, exactly
        proportional, or proportional up to a saturation knee."""
        samples = [(size, self.read(case, field))
                   for size, case in self.sizes_for(prefix)]
        if len(samples) < 2:
            return {"kind": "insufficient", "samples": samples}
        values = [v for _, v in samples]
        if max(values) - min(values) <= tolerance:
            return {"kind": "constant", "value": values[0], "samples": samples}

        def affine(points):
            """(intercept, slope) if one line passes through every point."""
            if len(points) < 2:
                return None
            (s0, v0), (s1, v1) = points[0], points[-1]
            if s1 == s0:
                return None
            slope = (v1 - v0) / (s1 - s0)
            intercept = v0 - slope * s0
            scale = max(1.0, max(abs(v) for _, v in points))
            if any(abs(intercept + slope * s - v) > 1e-6 * scale for s, v in points):
                return None
            return intercept, slope

        # a plateau is a value repeated by the LARGEST sizes; a single extreme
        # sample is not a saturation, which is what mistook `blur.distances.0`
        # (exactly -size/2, no knee) for a saturating law
        plateau = values[-1]
        tail = [s for s, v in samples if abs(v - plateau) <= tolerance]
        moving = [(s, v) for s, v in samples if abs(v - plateau) > tolerance]
        if len(tail) >= 2 and moving:
            line = affine(moving)
            if line is not None:
                intercept, slope = line
                law = {"kind": "affine-saturating" if abs(intercept) > tolerance
                       else "proportional-saturating",
                       "intercept": intercept, "slope": slope,
                       "ceiling": plateau, "samples": samples}
                if slope:
                    law["knee"] = (plateau - intercept) / slope
                return law

        line = affine(samples)
        if line is not None:
            intercept, slope = line
            return {"kind": "affine" if abs(intercept) > tolerance else "proportional",
                    "intercept": intercept, "slope": slope, "samples": samples}
        return {"kind": "irregular", "samples": samples}


def _main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="regenerate the decoded parameter artifact at REAL element "
                    "sizes (the shipped one held the degenerate _nil table)")
    parser.add_argument("--decode-dir", type=Path, required=True)
    parser.add_argument("--prefixes", nargs="*",
                        default=["regular_light", "regular_dark",
                                 "clear_light", "clear_dark"])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    table = ParameterTable(args.decode_dir)
    artifact: dict = {"osBuild": "25G76", "schema": 2,
                      "note": "size-dependent table; _nil is a no-geometry "
                              "default and is reported separately",
                      "fields": {}}
    for field in table.names():
        entry: dict = {}
        for prefix in args.prefixes:
            law = table.size_law(prefix, field)
            if law["kind"] == "insufficient":
                continue
            record = {"kind": law["kind"]}
            if law["kind"] == "constant":
                record["value"] = law["value"]
            elif law["kind"] != "irregular":
                record["slope"] = law["slope"]
                record["intercept"] = law.get("intercept", 0.0)
                if "ceiling" in law:
                    record["ceiling"] = law["ceiling"]
                    record["kneeSize"] = law.get("knee")
            else:
                record["samples"] = law["samples"]
            entry[prefix] = record
        nil = f"{args.prefixes[0]}_nil"
        if nil in table.blobs:
            entry["_nil"] = table.read(nil, field)
        artifact["fields"][field] = entry

    kinds: dict = {}
    for field, entry in artifact["fields"].items():
        for prefix, record in entry.items():
            if isinstance(record, dict):
                kinds[record["kind"]] = kinds.get(record["kind"], 0) + 1
    print(f"{len(artifact['fields'])} fields; law kinds across variants: {kinds}")
    for field, entry in sorted(artifact["fields"].items()):
        parts = []
        for prefix in args.prefixes:
            record = entry.get(prefix)
            if not isinstance(record, dict):
                continue
            kind = record["kind"]
            if kind == "constant":
                parts.append(f"{prefix}={record['value']:g}")
            elif kind in ("proportional", "affine"):
                bias = (f"{record['intercept']:+g}" if kind == "affine" else "")
                parts.append(f"{prefix}={record['slope']:g}*size{bias}")
            elif kind in ("proportional-saturating", "affine-saturating"):
                bias = (f"{record['intercept']:+g}"
                        if kind == "affine-saturating" else "")
                bound = "min" if record["slope"] > 0 else "max"
                parts.append(f"{prefix}={bound}({record['ceiling']:g}, "
                             f"{record['slope']:g}*size{bias})")
            else:
                parts.append(f"{prefix}=irregular")
        if parts and len(set(parts)) > 0:
            print(f"   {field:34s} {'   '.join(parts)}")

    if args.out:
        args.out.write_text(json.dumps(artifact, indent=1))


if __name__ == "__main__":
    _main()
