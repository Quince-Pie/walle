"""Self-contained source-fixture CPU checks with the production release flags."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--cc',default=os.environ.get('CC','cc'))
    p.add_argument('--sanitize',action='store_true')
    a=p.parse_args();root=a.root.resolve();tests=Path(__file__).resolve().parent
    out=root/'build/tests/material';out.mkdir(parents=True,exist_ok=True)
    profile='sanitized'if a.sanitize else'release'
    sources=[tests/'material_contracts.c',root/'material/material.c',root/'material/material_math.c',root/'material/applelog.c']
    inputs=[*sources,tests/'material_fixture_data.h',tests/'material_fixture_provenance.json',*sorted((root/'material').glob('*.h'))]
    before={str(path):hashlib.sha256(path.read_bytes()).hexdigest()for path in inputs}
    flags=['-std=c23','-ffp-contract=off','-Wall','-Wextra','-Wpedantic','-Werror=implicit-function-declaration',
           '-Werror=incompatible-pointer-types','-Werror=return-type',f'-I{root}']
    flags+=['-O1','-g','-U_FORTIFY_SOURCE','-fno-stack-protector','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer']if a.sanitize else['-O3','-flto=auto','-fno-plt','-fstack-protector-strong','-D_FORTIFY_SOURCE=3','-DNDEBUG','-pthread']
    binary=out/f'material-{profile}'
    command=shlex.split(a.cc)+flags+[str(path)for path in sources]+['-lm','-o',str(binary)]
    subprocess.run(command,check=True,timeout=120)
    result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=60)
    print(result.stdout,end='');print(result.stderr,end='',file=sys.stderr)
    after={str(path):hashlib.sha256(path.read_bytes()).hexdigest()for path in inputs}
    report=dict(profile=profile,command=command,returncode=result.returncode,source_unchanged=before==after,
                inputs=before,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                result=json.loads(result.stdout)if result.returncode==0 else None,stderr=result.stderr)
    (out/f'{profile}.json').write_text(json.dumps(report,indent=2)+'\n')
    return result.returncode if result.returncode else(0 if report['source_unchanged']else 1)
if __name__=='__main__':raise SystemExit(main())
