#!/usr/bin/env python3
"""Run CPU cache/controller contracts against retained original native fixtures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "{" + ",".join(literal(v) for v in value) + "}"
    if isinstance(value, str):
        return json.dumps(value)
    raise TypeError(value)


def record(fields: dict) -> str:
    return "{" + ",".join(f".{key}={literal(value)}" for key, value in fields.items()) + "}"


def state_record(state: dict) -> str:
    fields = {key: state[key] for key in (
        "transform", "bounds", "elements", "copies", "eligible", "changed",
        "stored_bounds", "ring", "last_change")}
    fields["time"] = state.get("time", state.get("absolute_time"))
    for key in ("owner", "new_owner", "nested_disabled"):
        if key in state:
            fields[key] = state[key]
    if "case" in state:
        fields["case_index"] = state["case"]
    return record(fields)


def fixture_header(fixture: dict) -> str:
    """Translate recorded literals only; do not compute expected cache decisions."""
    cases = []
    for case in fixture["cases"]:
        environment, glass = case["environment"], case["glass"]
        fields = dict(name=case["name"], width=case["output"]["width"],
                      height=case["output"]["height"],
                      backing_scale=environment["backing_scale"], local_radius=glass["local_radius"])
        options = dict(style=0 if glass["style"] == "regular" else 1,
                       dark=environment["dark"], active=environment["active"],
                       motion=0 if case["motion"] == "sweep" else 1,
                       origin=case["origin"], direction=case["direction"])
        tint = glass["tint_rgba8"]
        option_text = record(options)[:-1] + ",.tint=" + record(
            dict(present=tint is not None, srgb=tint or [0, 0, 0, 0])) + "}"
        frames = []
        for frame in case["frames"]:
            f = {key: frame[key] for key in (
                "time", "progress", "cached", "redraw", "retain", "birth", "current",
                "native_copy_render_count")}
            f["endpoint"] = {"A": 0, "scene": 1, "B": 2}[frame["endpoint"]]
            f["has_state"] = frame["state"] is not None
            a, b, c, d, tx, ty = frame["declared_affine"]
            f["declared_transform"] = [a, b, 0., 0., c, d, 0., 0., 0., 0., 1., 0., tx, ty, 0., 1.]
            text = record(f)
            if f["has_state"]:
                text = text[:-1] + ",.state=" + state_record(frame["state"]) + "}"
            frames.append(text)
        cases.append(record(fields)[:-1] + ",.options=" + option_text
                     + ",.frames={\n" + ",\n".join(frames) + "}}")
    updates = ",\n".join(state_record(update) for update in fixture["updates"])
    return ("/* Generated from cache_native_fixture.json; original observations only. */\n"
            "static const struct native_case native_cases[] = {\n" + ",\n".join(cases)
            + "};\nstatic const struct native_update native_updates[] = {\n" + updates + "};\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    parser.add_argument("--pkg-config", default=os.environ.get("PKG_CONFIG", "pkg-config"))
    parser.add_argument("--profile", choices=("fortify", "sanitize"), default="fortify")
    parser.add_argument("--require-matched-inputs", action="store_true",
                        help="also fail if declared/native affine inputs have a retained transport gap")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    tests = Path(__file__).resolve().parent
    build = (args.build_dir or root / "build/tests/cache-controller" / args.profile).resolve()
    build.mkdir(parents=True, exist_ok=True)
    output = (args.output or build / "result.json").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    library = [root / name for name in (
        "transition.c", "material/material.c", "material/material_math.c", "material/applelog.c",
        "material/capture.c", "material/geometry.c", "material/scissor.c", "material/sdf_cache.c", "material/clip.c")]
    fixture_path = tests / "cache_native_fixture.json"
    inputs = [Path(__file__).resolve(), fixture_path, tests / "cache_controller_contracts.c",
              tests / "transition_check.h", root / "transition.h", root / "vulkan_renderer.h",
              *library, *sorted((root / "material").glob("*.h"))]
    hashes = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    result = dict(status="FAIL", profile=args.profile, source_sha256=hashes(), commands=[],
                  scope="CPU controller/native host decisions, commit/retry/reset; no GPU pixel claim",
                  excluded=["GPU resource lifetime and transient-copy coalescing: renderer qualification"],
                  recovery_reference="fresh independent controller owner at the same pose/time; native state corpus still checked exactly")

    def command(argv: list[str], name: str) -> subprocess.CompletedProcess:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
        (build / f"{name}.stdout.txt").write_text(completed.stdout)
        (build / f"{name}.stderr.txt").write_text(completed.stderr)
        result["commands"].append(dict(argv=argv, returncode=completed.returncode,
                                       stdout=completed.stdout, stderr=completed.stderr))
        if completed.returncode:
            raise RuntimeError(f"{name} failed (exit {completed.returncode}); see {build}")
        return completed

    try:
        cc = shlex.split(args.cc)
        pkg_config = shlex.split(args.pkg_config)
        if not cc or not pkg_config:
            raise ValueError("compiler and pkg-config commands must not be empty")
        result["compiler"] = command([*cc, "--version"], "compiler").stdout.splitlines()[0]
        flags = shlex.split(command([*pkg_config, "--cflags", "wayland-client"], "headers").stdout)
        fixture = json.loads(fixture_path.read_text())
        result["native_scene_input_gate"] = fixture["input_transport"]
        if (len(fixture["cases"]) != 4 or sum(len(c["frames"]) for c in fixture["cases"]) != 100
                or len(fixture["updates"]) != 250):
            raise ValueError("incomplete native fixture")
        header = build / "cache_native_data.h"
        header.write_text(fixture_header(fixture))
        flags += ["-std=c23", "-ffp-contract=off", "-fno-fast-math", "-Wall", "-Wextra",
                  "-Wpedantic", "-Wshadow", "-Werror=implicit-function-declaration",
                  "-Werror=incompatible-pointer-types", "-Werror=return-type",
                  f"-I{root}", f"-I{root / 'material'}", f"-I{tests}", f"-I{build}"]
        if args.profile == "fortify":
            flags += ["-O2", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=3", "-fstack-protector-strong"]
        else:
            flags += ["-O1", "-g3", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=0",
                      "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"]
        binary = build / "cache-controller"
        command([*cc, *flags, str(tests / "cache_controller_contracts.c"),
                 *map(str, library), "-Wl,--wrap=wm_sdf_cache_advance", "-lm", "-o", str(binary)], "build")
        result["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        completed = command([str(binary)], "run")
        result["counts"] = json.loads(completed.stdout)
        result["source_unchanged"] = hashes() == result["source_sha256"]
        if not result["source_unchanged"]:
            raise RuntimeError("source changed during verification")
        if fixture["input_transport"]["differences"]:
            result["status"] = "PASS_WITH_NATIVE_INPUT_GAP"
            if args.require_matched_inputs:
                result["status"] = "FAIL"
                result["error"] = "matched native scene input gate remains open; see retained transport differences"
        else:
            result["status"] = "PASS"
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        result["error"] = str(error)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("status", "profile", "counts", "error") if key in result}))
    return 0 if result["status"] in ("PASS", "PASS_WITH_NATIVE_INPUT_GAP") else 1


if __name__ == "__main__":
    raise SystemExit(main())
