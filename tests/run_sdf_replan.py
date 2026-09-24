#!/usr/bin/env python3
"""Inject CPU-only SDF resource failures into the real renderer's public frame API."""
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
    if tests != root / "tests":
        parser.error("this runner must belong to the tested repository")
    build = (args.build_dir or root / "build/tests/sdf-replan" / args.profile).resolve()
    build.mkdir(parents=True, exist_ok=True)
    inputs = [root / "vulkan_renderer.c", root / "vulkan_renderer.h",
              tests / "sdf_replan_contracts.c", tests / "transition_check.h", Path(__file__).resolve(),
              *sorted((root / "material").glob("*.h"))]
    hashes = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    report = dict(status="FAIL", profile=args.profile, source_sha256=hashes(), commands=[],
                  scope="CPU-only real renderer admission/resource status; no Vulkan instance/device or GPU")

    def command(argv: list[str], name: str) -> subprocess.CompletedProcess:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
        (build / f"{name}.stdout.txt").write_text(result.stdout)
        (build / f"{name}.stderr.txt").write_text(result.stderr)
        report["commands"].append(dict(argv=argv, returncode=result.returncode,
                                       stdout=result.stdout, stderr=result.stderr))
        if result.returncode:
            raise RuntimeError(f"{name} failed(exit{result.returncode}); see {build}")
        return result

    try:
        cc = shlex.split(args.cc)
        if not cc:
            raise ValueError("compiler command is empty")
        report["compiler"] = command([*cc, "--version"], "compiler").stdout.splitlines()[0]
        pkg = shlex.split(command(["pkg-config", "--cflags", "--libs", "vulkan", "wayland-client", "libdrm"], "headers").stdout)
        flags = ["-std=c23", "-ffp-contract=off", "-fno-fast-math", "-Wall", "-Wextra", "-Wpedantic",
                 "-Wshadow", "-Werror", "-ffunction-sections", "-fdata-sections",
                 f"-I{root}", f"-I{root / 'material'}", f"-I{tests}"]
        if args.profile == "fortify":
            flags += ["-O2", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=3", "-fstack-protector-strong"]
        else:
            flags += ["-O1", "-g3", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=0",
                      "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"]
        wrappers = ("vkCreateImage", "vkGetImageMemoryRequirements", "vkAllocateMemory", "vkBindImageMemory",
                    "vkCreateImageView", "vkDestroyImage", "vkDestroyImageView", "vkFreeMemory",
                    "vkResetCommandBuffer", "vkBeginCommandBuffer", "vkQueueSubmit2", "vkGetFenceStatus")
        binary = build / "sdf-replan"
        command([*cc, *flags, str(tests / "sdf_replan_contracts.c"),
                 str(root / "protocols/linux-dmabuf-v1.c"), "-Wl,--gc-sections",
                 *(f"-Wl,--wrap={name}" for name in wrappers), "-o", str(binary), *pkg, "-lm"], "build")
        report["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        report["counts"] = json.loads(command([str(binary)], "run").stdout)
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
