"""Independent libvips memory-render oracle versus application GPU endpoint bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import numpy as np

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--prepare", type=Path, required=True)
parser.add_argument("--frames", type=Path, required=True)
parser.add_argument("--a", type=Path, required=True)
parser.add_argument("--b", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
meta = json.loads((args.frames / "frames.json").read_text())
w, h = meta["width"], meta["height"]
results = []
for source, index, name in ((args.a, 0, "A"), (args.b, meta["frames"] - 1, "B")):
    output = args.output / f"oracle-{name}.rgba"
    subprocess.run([str(args.prepare), str(source), str(w), str(h), str(output)], check=True, timeout=30)
    expected = np.fromfile(output, dtype=np.uint8).reshape(h, w, 4)[..., [2, 1, 0, 3]]
    actual_path = args.frames / f"frame-{index:04d}.bgra"
    actual = np.fromfile(actual_path, dtype=np.uint8).reshape(h, w, 4)
    error = np.abs(expected.astype(np.int16) - actual.astype(np.int16))
    result = dict(endpoint=name, input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  output_sha256=hashlib.sha256(actual_path.read_bytes()).hexdigest(),
                  differing_bytes=int(np.count_nonzero(error)), max_error=int(error.max()),
                  opaque=bool(np.all(actual[..., 3] == 255)))
    results.append(result)
report = dict(width=w, height=h, results=results,
              passed=all(r["differing_bytes"] == 0 and r["opaque"] for r in results))
(args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
raise SystemExit(0 if report["passed"] else 1)
