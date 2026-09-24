#!/usr/bin/env python3
"""Compare production quad clipping with UUID-gated original CPU output bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--profile", choices=("fortify", "sanitize"), default="fortify")
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = parser.parse_args()
    root, tests = args.repo_root.resolve(), Path(__file__).resolve().parent
    build = (args.build_dir or root / "build/tests/quad-clip" / args.profile).resolve()
    build.mkdir(parents=True, exist_ok=True)
    source = [root / "material/clip.c", tests / "quad_clip_contracts.c"]
    inputs = [*source, root / "material/clip.h", root / "material/geometry.h",
              tests / "transition_check.h", tests / "quad_clip_fixture.bin",
              tests / "quad_clip_fixture_provenance.json", Path(__file__).resolve()]
    hashes = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    report = dict(status="FAIL", profile=args.profile, source_sha256=hashes(), commands=[],
                  scope="single-part no-surface uniform-axis CPU clipping; no general GPU-space clipping claim")

    def command(argv: list[str], name: str) -> subprocess.CompletedProcess:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
        (build / f"{name}.stdout.txt").write_text(result.stdout)
        (build / f"{name}.stderr.txt").write_text(result.stderr)
        report["commands"].append(dict(argv=argv, returncode=result.returncode,
                                        stdout=result.stdout, stderr=result.stderr))
        if result.returncode:
            raise RuntimeError(f"{name} failed (exit{result.returncode}); see {build}")
        return result

    try:
        compiler = shlex.split(args.cc)
        if not compiler:
            raise ValueError("compiler command is empty")
        report["compiler"] = command([*compiler, "--version"], "compiler").stdout.splitlines()[0]
        provenance = json.loads((tests / "quad_clip_fixture_provenance.json").read_text())
        if hashlib.sha256((tests / "quad_clip_fixture.bin").read_bytes()).hexdigest() != provenance["fixture_sha256"]:
            raise ValueError("native fixture hash mismatch")
        flags = ["-std=c23", "-ffp-contract=off", "-fno-fast-math", "-Wall", "-Wextra",
                 "-Wpedantic", "-Wshadow", "-Werror=implicit-function-declaration",
                 "-Werror=incompatible-pointer-types", "-Werror=return-type",
                 f"-I{root / 'material'}", f"-I{tests}"]
        if args.profile == "fortify":
            flags += ["-O2", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=3", "-fstack-protector-strong"]
        else:
            flags += ["-O1", "-g3", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=0",
                      "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"]
        binary = build / "quad-clip"
        command([*compiler, *flags, *map(str, source), "-lm", "-o", str(binary)], "build")
        report["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        report["counts"] = json.loads(command([str(binary), str(tests / "quad_clip_fixture.bin")], "run").stdout)
        report["source_unchanged"] = hashes() == report["source_sha256"]
        if not report["source_unchanged"]:
            raise RuntimeError("sources changed during verification")
        report["status"] = "PASS"
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        report["error"] = str(error)
    (build / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "profile", "counts", "error") if key in report}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
