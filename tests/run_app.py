"""Compile and run app contracts without a compositor or Vulkan device."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
parser.add_argument("--cc", default=os.environ.get("CC", "gcc"))
parser.add_argument("--profile", choices=("release", "sanitize"), default="release")
args = parser.parse_args()
root = args.repo_root.resolve()
out = root / "build/tests/app" / args.profile
out.mkdir(parents=True, exist_ok=True)
pkg = subprocess.run(["pkg-config", "--cflags", "--libs", "vips", "wayland-client", "libsystemd", "liburing", "inih", "libxxhash"], check=True, text=True, capture_output=True, timeout=30)
flags = ["-std=c23", "-O2", "-ffp-contract=off", "-Wall", "-Wextra", "-Wpedantic", "-Werror=implicit-function-declaration", "-Werror=incompatible-pointer-types", "-Werror=return-type", "-ffunction-sections", "-fdata-sections", "-I.", "-Imaterial", "-Iprotocols"]
if args.profile == "sanitize":
    flags += ["-O1", "-g", "-U_FORTIFY_SOURCE", "-fno-stack-protector", "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"]
else:
    flags += ["-D_FORTIFY_SOURCE=3", "-fstack-protector-strong"]
binary = out / "contracts"
command = shlex.split(args.cc) + flags + ["tests/app_contracts.c", "shiro.c", "protocols/wlr-layer-shell-unstable-v1.c", "protocols/xdg-shell.c", "-Wl,--gc-sections", "-Wl,--wrap=clock_gettime", "-Wl,--wrap=wl_proxy_marshal_flags", "-Wl,--wrap=wl_proxy_destroy"] + shlex.split(pkg.stdout) + ["-pthread", "-lm", "-o", str(binary)]
subprocess.run(command, cwd=root, check=True, timeout=120)
run = subprocess.run([str(binary)], cwd=root, capture_output=True, text=True, timeout=30)
(out / "stdout.txt").write_text(run.stdout)
(out / "stderr.txt").write_text(run.stderr)
if run.returncode:
    print(run.stdout, end="")
    print(run.stderr, end="")
    raise SystemExit(run.returncode)
result = json.loads(run.stdout.splitlines()[-1])
result.update(command=command, source_sha256=hashlib.sha256((root / "walle.c").read_bytes()).hexdigest(), profile=args.profile)
(out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
print(f"app: {result['assertions']} assertions passed ({args.profile})")
