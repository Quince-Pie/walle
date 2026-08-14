#!/usr/bin/env python3
"""Decode an allocation snapshot captured by macos_agx_iokit_trace.c."""

from __future__ import annotations

import argparse
import ctypes
import os
import re
from dataclasses import dataclass
from pathlib import Path


ALLOCATION_NAME = re.compile(
    r"alloc-(?P<handle>[0-9]+)-gpu-(?P<gpu>[0-9a-fA-F]{16})-size-(?P<size>[0-9]+)\.bin"
)


@dataclass(frozen=True, slots=True)
class Allocation:
    handle: int
    gpu: int
    data: bytes

    @property
    def end(self) -> int:
        return self.gpu + len(self.data)


def load_allocations(directory: Path) -> list[Allocation]:
    allocations: list[Allocation] = []
    for path in sorted(directory.glob("alloc-*-gpu-*-size-*.bin")):
        match = ALLOCATION_NAME.fullmatch(path.name)
        if match is None:
            continue
        data = path.read_bytes()
        declared_size = int(match["size"])
        if len(data) != declared_size:
            raise ValueError(
                f"{path}: expected {declared_size} bytes, found {len(data)}"
            )
        allocations.append(
            Allocation(
                handle=int(match["handle"]),
                gpu=int(match["gpu"], 16),
                data=data,
            )
        )
    if not allocations:
        raise ValueError(f"no captured allocations in {directory}")
    return allocations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("library", type=Path)
    parser.add_argument("dump", type=Path)
    parser.add_argument("address", type=lambda value: int(value, 0))
    parser.add_argument("--chip-id", type=lambda value: int(value, 0), default=0x6001)
    parser.add_argument("--kind", choices=("vdm", "cdm", "usc"), default="vdm")
    parser.add_argument("--label", default="captured")
    args = parser.parse_args()

    allocations = load_allocations(args.dump)
    library = ctypes.CDLL(args.library)

    read_callback_type = ctypes.CFUNCTYPE(
        ctypes.c_size_t,
        ctypes.c_uint64,
        ctypes.c_size_t,
        ctypes.c_void_p,
    )
    write_callback_type = ctypes.CFUNCTYPE(
        ctypes.c_ssize_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
    )

    @read_callback_type
    def read_gpu_memory(address: int, size: int, output: int) -> int:
        for allocation in allocations:
            if allocation.gpu <= address < allocation.end:
                offset = address - allocation.gpu
                available = min(size, len(allocation.data) - offset)
                ctypes.memmove(output, allocation.data[offset : offset + available], available)
                return available
        os.write(2, f"unmapped GPU read: 0x{address:x}+0x{size:x}\n".encode())
        return 0

    @write_callback_type
    def write_output(buffer: int, size: int) -> int:
        return os.write(1, ctypes.string_at(buffer, size))

    class DecoderConfig(ctypes.Structure):
        _fields_ = [
            ("chip_id", ctypes.c_uint32),
            ("read_gpu_mem", read_callback_type),
            ("stream_write", write_callback_type),
        ]

    library.libagxdecode_init.argtypes = [ctypes.POINTER(DecoderConfig)]
    library.libagxdecode_init.restype = None
    library.agxdecode_new_context.argtypes = [ctypes.c_uint64]
    library.agxdecode_new_context.restype = ctypes.c_void_p
    library.agxdecode_destroy_context.argtypes = [ctypes.c_void_p]
    library.agxdecode_destroy_context.restype = None
    decoder = getattr(library, f"libagxdecode_{args.kind}")
    decoder.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_bool]
    decoder.restype = None
    library.libagxdecode_shutdown.argtypes = []
    library.libagxdecode_shutdown.restype = None

    config = DecoderConfig(args.chip_id, read_gpu_memory, write_output)
    context = library.agxdecode_new_context(0)
    if not context:
        raise MemoryError("agxdecode_new_context failed")

    library.libagxdecode_init(ctypes.byref(config))
    try:
        decoder(context, args.address, args.label.encode(), True)
    finally:
        library.libagxdecode_shutdown()
        library.agxdecode_destroy_context(context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
