#!/usr/bin/env python3
"""Build and run CPU transition contracts against this repository's sources."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--build-dir', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cc', default=os.environ.get('CC', 'gcc'))
    parser.add_argument('--pkg-config', default=os.environ.get('PKG_CONFIG', 'pkg-config'))
    parser.add_argument('--profile', choices=('fortify', 'sanitize'), default='fortify')
    args = parser.parse_args()
    root = args.repo_root.resolve()
    tests = Path(__file__).resolve().parent
    build = (args.build_dir or root / 'build/tests/transition' / args.profile).resolve()
    build.mkdir(parents=True, exist_ok=True)
    output = (args.output or build / 'result.json').resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cc = shlex.split(args.cc)
    pkg_config = shlex.split(args.pkg_config)
    if not cc or not pkg_config:
        parser.error('compiler and pkg-config commands must not be empty')
    library = [root / name for name in (
        'transition.c', 'material/material.c', 'material/material_math.c',
        'material/applelog.c', 'material/capture.c', 'material/geometry.c',
        'material/scissor.c')]
    sources = [tests / 'transition_contracts.c', tests / 'transition_fortify_bounds.c',
               tests / 'transition_check.h', *library, root / 'transition.h',
               root / 'vulkan_renderer.h', *sorted((root / 'material').glob('*.h'))]
    missing = [str(p) for p in sources if not p.is_file()]
    if missing:
        parser.error('missing repository sources: ' + ', '.join(missing))
    commands: list[dict[str, object]] = []
    result: dict[str, object] = {
        'status': 'FAIL', 'profile': args.profile, 'repo_root': str(root), 'commands': commands,
        'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        'scope': 'CPU geometry, frame assembly and packet contracts; no GPU or pixel-parity claim',
    }

    def command(argv: list[str], label: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(argv, text=True, capture_output=True, timeout=180, check=False)
        (build / f'{label}.stdout.txt').write_text(completed.stdout)
        (build / f'{label}.stderr.txt').write_text(completed.stderr)
        commands.append({'argv': argv, 'returncode': completed.returncode,
                         'stdout': completed.stdout, 'stderr': completed.stderr})
        if completed.returncode:
            raise RuntimeError(f'{label} failed (exit {completed.returncode}); see {build}')
        return completed

    try:
        result['compiler_version'] = command([*cc, '--version'], 'compiler').stdout.splitlines()[0]
        wayland = shlex.split(command([*pkg_config, '--cflags', 'wayland-client'], 'headers').stdout)
        flags = ['-std=c23', '-ffp-contract=off', '-fno-fast-math', '-Wall', '-Wextra', '-Wpedantic',
                 '-Wshadow', '-Wimplicit-fallthrough', '-Werror=implicit-function-declaration',
                 '-Werror=implicit-int', '-Werror=incompatible-pointer-types', '-Werror=return-type',
                 f'-I{root}', f'-I{root / "material"}', f'-I{tests}', *wayland]
        if args.profile == 'fortify':
            flags += ['-O2', '-U_FORTIFY_SOURCE', '-D_FORTIFY_SOURCE=3', '-fstack-protector-strong']
        else:
            flags += ['-O1', '-g3', '-U_FORTIFY_SOURCE', '-D_FORTIFY_SOURCE=0',
                      '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                      '-fno-omit-frame-pointer']
        programs = [('transition_contracts', [tests / 'transition_contracts.c', *library])]
        if args.profile == 'fortify':
            programs.append(('transition_fortify_bounds',
                             [tests / 'transition_fortify_bounds.c', root / 'material/geometry.c']))
        for name, inputs in programs:
            executable = build / name
            command([*cc, *flags, *map(str, inputs), '-lm', '-o', str(executable)], name + '.build')
            completed = command([str(executable)], name + '.run')
            print(completed.stdout, end='')
        result['status'] = 'PASS'
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        result['error'] = str(error)
        print(error, file=sys.stderr)
    finally:
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(f'CPU transition {args.profile}: {result["status"]}; {output}')
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
