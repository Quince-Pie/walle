#!/usr/bin/env python3
"""Hold one GL texture resident for external amdgpu fdinfo sampling."""

import argparse
import json
import os
import time

import moderngl


GL_RGBA32UI = 0x8D70
GL_RGBA8I = 0x8D8E
GL_R8UI = 0x8232


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "context",
            "full",
            "axis",
            "coefficient",
            "correction",
            "intrinsic",
            "exact-full",
            "exact-axis",
            "exact-coefficient",
        ),
    )
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--device-index", type=int)
    arguments = parser.parse_args()

    context_arguments: dict[str, object] = {"backend": "egl"}
    if arguments.device_index is not None:
        context_arguments["device_index"] = arguments.device_index
    context = moderngl.create_standalone_context(**context_arguments)
    textures: list[moderngl.Texture] = []
    logical_bytes = 0
    if arguments.mode in ("full", "exact-full"):
        size = 1024 * 1024 * 4 * 4
        logical_bytes += size
        textures.append(context.texture(
            (1024, 1024),
            4,
            bytes(size),
            alignment=1,
            dtype="u4",
            internal_format=GL_RGBA32UI,
        ))
    if arguments.mode in ("axis", "exact-axis"):
        size = 800 * 2 * 4 * 4
        logical_bytes += size
        textures.append(context.texture(
            (800, 2),
            4,
            bytes(size),
            alignment=1,
            dtype="u4",
            internal_format=GL_RGBA32UI,
        ))
    if arguments.mode in ("coefficient", "exact-coefficient"):
        size = 26 * 2 * 4 * 4
        logical_bytes += size
        textures.append(context.texture(
            (26, 2),
            4,
            bytes(size),
            alignment=1,
            dtype="u4",
            internal_format=GL_RGBA32UI,
        ))
    if arguments.mode == "correction":
        size = 800 * 800 * 4
        logical_bytes += size
        textures.append(context.texture(
            (800, 800),
            4,
            bytes(size),
            alignment=1,
            dtype="i1",
            internal_format=GL_RGBA8I,
        ))
    if arguments.mode in (
        "intrinsic",
        "exact-full",
        "exact-axis",
        "exact-coefficient",
    ):
        size = 4096 * 2048
        logical_bytes += size
        textures.append(context.texture(
            (4096, 2048),
            1,
            bytes(size),
            alignment=1,
            dtype="u1",
            internal_format=GL_R8UI,
        ))
    context.finish()
    print(
        json.dumps({
            "pid": os.getpid(),
            "mode": arguments.mode,
            "logicalBytes": logical_bytes,
            "glRenderer": context.info["GL_RENDERER"],
        }),
        flush=True,
    )
    time.sleep(arguments.seconds)
    for texture in textures:
        texture.release()
    context.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
