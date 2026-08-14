#!/usr/bin/env python3
"""Launch one Walle build, capture DRM fdinfo, and stop it cleanly."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from profile_walle_fdinfo import read_drm_fdinfo


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=float, default=15.0)
    return parser.parse_args()


def wait_for_drm_client(process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = process.poll()
        if status is not None:
            raise RuntimeError(f"Walle exited before profiling with status {status}")
        try:
            read_drm_fdinfo(process.pid)
            return
        except (FileNotFoundError, RuntimeError):
            time.sleep(0.02)
    raise TimeoutError(f"Walle did not expose DRM fdinfo within {timeout:g} seconds")


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)


def main() -> int:
    args = parse_args()
    binary = args.binary.resolve(strict=True)
    config = args.config.resolve(strict=True)
    if args.duration <= 0 or args.interval <= 0 or args.startup_timeout <= 0:
        raise ValueError("durations and interval must be positive")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.output.with_suffix(".log")
    environment = os.environ.copy()
    environment["MESA_DEBUG"] = "context"

    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [str(binary), "-c", str(config)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
        )
        try:
            wait_for_drm_client(process, args.startup_timeout)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("profile_walle_fdinfo.py")),
                    "--pid",
                    str(process.pid),
                    "--duration",
                    str(args.duration),
                    "--interval",
                    str(args.interval),
                    "--output",
                    str(args.output),
                ],
                check=False,
            )
            return completed.returncode
        finally:
            stop_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
