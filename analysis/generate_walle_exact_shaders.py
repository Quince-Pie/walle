#!/usr/bin/env python3
"""Generate Walle-owned variants of the byte-gated Apple shaders."""

import argparse
import hashlib
import json
from pathlib import Path

from liquid_glass_shader_specialization import (
    load_amd_exact_circle_shader,
    load_amd_gles_compatible_circle_shader,
    target_gles_320,
)


ROOT = Path(__file__).resolve().parent.parent
VERTEX_SOURCE = ROOT / "analysis/apple_glass_reference.vert.glsl"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generate(output: Path, *, api: str) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    if api == "desktop":
        sources = {
            "vertex": VERTEX_SOURCE.read_text(encoding="utf-8"),
            "clear": load_amd_exact_circle_shader(
                "clear", coordinate_mode=None
            ),
            "regular": load_amd_exact_circle_shader(
                "regular", coordinate_mode=None
            ),
        }
        target = "OpenGL 4.5 core"
    elif api == "gles":
        sources = {
            "vertex": target_gles_320(
                VERTEX_SOURCE.read_text(encoding="utf-8")
            ),
            "clear": target_gles_320(
                load_amd_gles_compatible_circle_shader(
                    "clear", coordinate_mode=None
                )
            ),
            "regular": target_gles_320(
                load_amd_gles_compatible_circle_shader(
                    "regular", coordinate_mode=None
                )
            ),
        }
        target = "OpenGL ES 3.2"
    else:
        raise ValueError(f"unsupported graphics API: {api}")
    names = {
        "vertex": "apple_glass_exact.vert.glsl",
        "clear": "apple_glass_exact_clear.frag.glsl",
        "regular": "apple_glass_exact_regular.frag.glsl",
    }
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "target": target,
        "files": {},
    }
    files = manifest["files"]
    assert isinstance(files, dict)
    for kind, source in sources.items():
        encoded = source.encode()
        name = names[kind]
        (output / name).write_bytes(encoded)
        files[kind] = {
            "path": name,
            "byteCount": len(encoded),
            "sha256": sha256(encoded),
        }
    encoded_manifest = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    (output / "manifest.json").write_text(encoded_manifest, encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--api",
        choices=("desktop", "gles"),
        default="desktop",
    )
    arguments = parser.parse_args()
    print(
        json.dumps(
            generate(arguments.output, api=arguments.api),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
