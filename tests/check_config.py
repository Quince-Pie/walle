"""Run only the assembled executable's display-free --check-config path."""
import argparse
from pathlib import Path
import hashlib
import json
import os
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary', type=Path, required=True)
parser.add_argument('--output', type=Path, default=Path('build/tests/config'))
args = parser.parse_args()
ROOT = args.output.resolve()
ROOT.mkdir(parents=True, exist_ok=True)
BIN = args.binary.resolve()
before = hashlib.sha256(BIN.read_bytes()).hexdigest()
cases = [
    ('hex6', 'transition_tint=#12aBcF\n', True),
    ('hex8-alpha0', 'transition_tint=#12aBcF00\n', True),
    ('none', 'transition_tint=none\n', True),
    ('light', 'transition_appearance=light\ntransition_motion=sweep\n', True),
    ('dark-lens', 'transition_appearance=dark\ntransition_motion=lens\n', True),
    ('auto', 'transition_appearance=auto\n', True),
    ('tiny', 'transition_duration=0x1p-148\n', True),
    ('bad-tint', 'transition_tint=#12abCG\n', False),
    ('bad-alpha', 'transition_tint=#1122330z\n', False),
    ('bad-appearance', 'transition_appearance=system\n', False),
    ('bad-motion', 'transition_motion=fade\n', False),
]
environment = dict(os.environ)
environment.pop('DISPLAY', None)
environment.pop('WAYLAND_DISPLAY', None)
results = []
for name, body, valid in cases:
    config = ROOT / f'{name}.ini'
    config.write_text('[default]\n' + body)
    result = subprocess.run([str(BIN), '--check-config', '-c', str(config)],
                            capture_output=True, text=True, env=environment, timeout=5)
    assert (result.returncode == 0) == valid, (name, result.returncode, result.stdout, result.stderr)
    assert 'Vulkan' not in result.stderr and 'Wayland' not in result.stderr, (name, result.stderr)
    results.append(dict(name=name, returncode=result.returncode,
                        stdout=result.stdout.strip(), stderr=result.stderr.strip()))
after = hashlib.sha256(BIN.read_bytes()).hexdigest()
assert before == after, 'binary changed during CLI contract run'
report = dict(cases=len(results), failures=0, binary=str(BIN), binary_sha256=before,
              displays_unset=True, results=results)
(ROOT / 'cli-result.json').write_text(json.dumps(report, indent=2) + '\n')
print(dict(cases=len(results), failures=0, binary_sha256=before))
