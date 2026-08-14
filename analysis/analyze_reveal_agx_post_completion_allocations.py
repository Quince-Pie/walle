#!/usr/bin/env python3
"""Audit AGX allocation snapshots taken before submission and after completion."""

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
DEFAULT_CAPTURE: Final = (
    ROOT / "build" / "analysis-agx-tvb-post-completion" / "remote-capture"
)
DEFAULT_OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-tvb-post-completion"
    / "reveal-agx-post-completion-allocation-result.json"
)
ALLOCATION_NAME: Final = re.compile(
    r"(?P<post>post-)?alloc-(?P<handle>[0-9]+)-gpu-"
    r"(?P<gpu>[0-9a-f]{16})-size-(?P<size>[0-9]+)\.bin"
)
SHARED_BUFFER_NAME: Final = re.compile(
    r"(?P<post>post-)?submit-(?P<submission>[0-9]+)-"
    r"shmem-(?P<identifier>[0-9]+)\.bin"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class AllocationKey:
    handle: int
    gpu_address: int
    size: int


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def load_allocations(directory: Path, *, post: bool) -> dict[AllocationKey, Path]:
    result: dict[AllocationKey, Path] = {}
    for path in sorted(directory.iterdir()):
        match = ALLOCATION_NAME.fullmatch(path.name)
        if match is None or bool(match["post"]) != post:
            continue
        key = AllocationKey(
            handle=int(match["handle"]),
            gpu_address=int(match["gpu"], 16),
            size=int(match["size"]),
        )
        if key in result:
            raise ValueError(f"duplicate allocation key: {key}")
        if path.stat().st_size != key.size:
            raise ValueError(f"allocation size mismatch: {path}")
        result[key] = path
    if not result:
        raise ValueError(f"no {'post' if post else 'pre'} allocations in {directory}")
    return result


def load_shared_buffers(directory: Path, *, post: bool) -> dict[tuple[int, int], Path]:
    result: dict[tuple[int, int], Path] = {}
    for path in sorted(directory.iterdir()):
        match = SHARED_BUFFER_NAME.fullmatch(path.name)
        if match is None or bool(match["post"]) != post:
            continue
        key = (int(match["submission"]), int(match["identifier"]))
        if key in result:
            raise ValueError(f"duplicate shared buffer key: {key}")
        result[key] = path
    return result


def changed_spans(before: bytes, after: bytes) -> list[tuple[int, int]]:
    changed = [
        index
        for index, pair in enumerate(zip(before, after, strict=True))
        if pair[0] != pair[1]
    ]
    if not changed:
        return []
    spans: list[tuple[int, int]] = []
    start = previous = changed[0]
    for index in changed[1:]:
        if index != previous + 1:
            spans.append((start, previous + 1))
            start = index
        previous = index
    spans.append((start, previous + 1))
    return spans


def inventory(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": str(path.relative_to(ROOT)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def analyze(capture_root: Path) -> dict[str, object]:
    trace_root = capture_root / "trace"
    before_paths = load_allocations(trace_root, post=False)
    after_paths = load_allocations(trace_root, post=True)
    if before_paths.keys() != after_paths.keys():
        raise ValueError("pre/post allocation key sets differ")
    shared_before_paths = load_shared_buffers(trace_root, post=False)
    shared_after_paths = load_shared_buffers(trace_root, post=True)
    if shared_after_paths and shared_before_paths.keys() != shared_after_paths.keys():
        raise ValueError("pre/post shared-buffer key sets differ")

    raw_path = capture_root / "capture" / "reveal-agx-basis.raw"
    manifest_path = capture_root / "capture" / "manifest.json"
    stderr_path = capture_root / "probe.stderr"
    raw = raw_path.read_bytes()
    manifest = json.loads(manifest_path.read_text())
    if manifest["capture"]["bytes"] != len(raw):
        raise ValueError("capture manifest byte count differs")
    if manifest["capture"]["sha256"] != sha256_bytes(raw):
        raise ValueError("capture manifest hash differs")

    trace_text = stderr_path.read_text()
    marker = f"AGX_IO post-completion allocations={len(after_paths)}"
    if trace_text.count(marker) != 1:
        raise ValueError("post-completion marker count differs")
    if trace_text.count("AGX_IO trap4 ") != 1:
        raise ValueError("submission count differs")

    allocation_records: list[dict[str, object]] = []
    changed_records: list[dict[str, object]] = []
    raw_owner: AllocationKey | None = None
    for key in sorted(before_paths, key=lambda item: (item.handle, item.gpu_address)):
        before = before_paths[key].read_bytes()
        after = after_paths[key].read_bytes()
        spans = changed_spans(before, after)
        changed_count = sum(end - start for start, end in spans)
        owns_raw = after.startswith(raw) and not any(after[len(raw) :])
        if owns_raw:
            if raw_owner is not None:
                raise ValueError("multiple allocations contain the capture result")
            raw_owner = key
        record = {
            "handle": key.handle,
            "gpuAddress": f"0x{key.gpu_address:016x}",
            "bytes": key.size,
            "preSha256": sha256_bytes(before),
            "postSha256": sha256_bytes(after),
            "changedByteCount": changed_count,
            "changedSpanCount": len(spans),
            "changedSpans": [
                {"start": start, "endExclusive": end} for start, end in spans
            ],
            "containsExactCaptureResultAtOffsetZero": owns_raw,
        }
        allocation_records.append(record)
        if spans:
            changed_records.append(record)

    if raw_owner is None:
        raise ValueError("capture result does not join a post-completion allocation")
    if len(changed_records) != 1 or changed_records[0]["handle"] != raw_owner.handle:
        raise ValueError("a non-result CPU mapping changed")

    shared_records: list[dict[str, object]] = []
    shared_changed_count = 0
    for key in sorted(shared_after_paths):
        before = shared_before_paths[key].read_bytes()
        after = shared_after_paths[key].read_bytes()
        if len(before) != len(after):
            raise ValueError(f"shared-buffer size differs: {key}")
        spans = changed_spans(before, after)
        shared_changed_count += bool(spans)
        shared_records.append(
            {
                "submission": key[0],
                "identifier": key[1],
                "bytes": len(before),
                "preSha256": sha256_bytes(before),
                "postSha256": sha256_bytes(after),
                "changedByteCount": sum(end - start for start, end in spans),
                "changedSpanCount": len(spans),
                "changedSpans": [
                    {"start": start, "endExclusive": end} for start, end in spans
                ],
            }
        )

    if shared_after_paths:
        conclusion = (
            "All captured ordinary CPU mappings and shared command/result buffers "
            "have exact pre/post pairs. Only the exact fragment result mapping "
            "changed. Neither shared buffer changed at all. The public macOS "
            "userspace mappings therefore do not expose TA-written TVB, generated-"
            "vertex, or coefficient-setup contents. The next direct observation "
            "route is decoded Execute-TA pointer following into private heaps or "
            "m1n1/UAT access."
        )
    else:
        conclusion = (
            "All captured CPU-mapped allocations have exact pre/post pairs. The "
            "only changed mapping is the fragment result allocation; its postimage "
            "is exactly the captured result followed by a zero tail. No captured "
            "ordinary CPU mapping exposes TA-written TVB, generated-vertex, or "
            "coefficient-setup contents. The shared command/result buffers remain "
            "the next post-completion observation target."
        )

    return {
        "schema": "walle-reveal-agx-post-completion-allocation-result-v1",
        "classification": "output-blind post-completion AGX allocation audit",
        "capture": {
            "raw": {
                "path": str(raw_path.relative_to(ROOT)),
                "bytes": len(raw),
                "sha256": sha256_bytes(raw),
            },
            "manifest": {
                "path": str(manifest_path.relative_to(ROOT)),
                "sha256": sha256_file(manifest_path),
            },
            "submissionCount": 1,
            "postCompletionHookCount": 1,
        },
        "allocationCensus": {
            "pairCount": len(allocation_records),
            "changedPairCount": len(changed_records),
            "unchangedPairCount": len(allocation_records) - len(changed_records),
            "resultAllocationHandle": raw_owner.handle,
            "resultAllocationGpuAddress": f"0x{raw_owner.gpu_address:016x}",
            "resultAllocationBytes": raw_owner.size,
            "resultPayloadBytes": len(raw),
            "resultZeroTailBytes": raw_owner.size - len(raw),
            "nonResultChangedPairCount": 0,
            "records": allocation_records,
        },
        "sharedBufferCensus": {
            "postCompletionObserved": bool(shared_after_paths),
            "pairCount": len(shared_records),
            "changedPairCount": shared_changed_count,
            "unchangedPairCount": len(shared_records) - shared_changed_count,
            "records": shared_records,
        },
        "conclusion": conclusion,
        "authority": {
            "opensReferencePixels": False,
            "usesFinalMaskOutputForSelection": False,
            "establishesTVBLayout": False,
            "establishesParity": False,
        },
        "inventory": inventory(capture_root),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    result = analyze(args.capture.resolve())
    encoded = json.dumps(result, indent=2, sort_keys=True).encode() + b"\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    print(
        "post-completion AGX allocations: "
        f"{result['allocationCensus']['pairCount']} pairs, "
        f"{result['allocationCensus']['nonResultChangedPairCount']} "
        "non-result changes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
