"""Build a locked benchmark snapshot inside the repository's Nix dev shell."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess

TOOLS=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--repo-root',type=Path,required=True)
parser.add_argument('--work-dir',type=Path,required=True)
parser.add_argument('--cc',default='gcc')
args=parser.parse_args()
repo=args.repo_root.expanduser().resolve();work=args.work_dir.expanduser().resolve()
if repo==work or repo in work.parents:
    raise SystemExit('--work-dir must be outside the repository (raw artifacts are not repository content)')
if not (repo/'flake.nix').is_file():raise SystemExit('repo-root must contain the authoritative flake.nix')
source=work/'build/source';source.mkdir(parents=True,exist_ok=True)
backend=repo/'vulkan_renderer.c'
shader_names=re.findall(r'#embed "build/shaders/([^"]+)"',backend.read_text())
if not shader_names:raise SystemExit('renderer shader embed paths were not found')
commands=[]
def run(command):
    commands.append(command);subprocess.run(command,check=True)
# Build only the repository's declared shaders/protocol, not a second shader recipe.
run(['make','-C',str(repo),'MODE=release','-j2',*[f'build/shaders/{n}' for n in shader_names],
     'protocols/linux-dmabuf-v1.c','protocols/linux-dmabuf-v1.h'])
for name in ('vulkan_renderer.c','vulkan_renderer.h','transition.c','transition.h'):
    shutil.copy2(repo/name,source/name)
(source/'material').mkdir(exist_ok=True)
for path in sorted((repo/'material').glob('*')):
    if path.suffix in ('.h','.c'):shutil.copy2(path,source/'material'/path.name)
(source/'protocols').mkdir(exist_ok=True)
for name in ('linux-dmabuf-v1.c','linux-dmabuf-v1.h'):
    shutil.copy2(repo/'protocols'/name,source/'protocols'/name)
(source/'build/shaders').mkdir(parents=True,exist_ok=True)
for name in shader_names:shutil.copy2(repo/'build/shaders'/name,source/'build/shaders'/name)
# Retain all shader source text alongside binaries for reproducibility.
(source/'shaders').mkdir(exist_ok=True)
for path in sorted((repo/'shaders').glob('*.slang')):shutil.copy2(path,source/'shaders'/path.name)
flags=['-std=c23','-O3','-DNDEBUG','-flto=auto','-fno-plt','-ffp-contract=off','-Wall','-Wextra','-Wpedantic',
       '-Wshadow','-Wimplicit-fallthrough','-Werror','-fstack-protector-strong','-D_FORTIFY_SOURCE=3']
pkg=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','vulkan','wayland-client','libdrm'],text=True))
run([args.cc,*flags,'-I'+str(source),'-I'+str(source/'material'),str(TOOLS/'benchmark.c'),
     str(source/'transition.c'),*[str(source/'material'/name) for name in ('material.c','material_math.c','applelog.c','capture.c','geometry.c','scissor.c')],
     str(source/'protocols/linux-dmabuf-v1.c'),'-o',str(work/'build/benchmark'),*pkg,'-lm'])
vips=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','vips'],text=True))
run([args.cc,*flags,str(TOOLS/'prepare.c'),'-o',str(work/'build/prepare'),*vips])
def digest(path):return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),size=path.stat().st_size)
record=dict(repo_root=str(repo),commands=commands,compiler_path=shutil.which(args.cc),
 compiler_version=subprocess.check_output([args.cc,'--version'],text=True),
 shader_compiler_path=shutil.which('slangc'),shader_compiler_version=subprocess.check_output(['slangc','-version'],text=True,stderr=subprocess.STDOUT),
 package_versions=subprocess.check_output(['pkg-config','--modversion','vulkan','wayland-client','libdrm','vips'],text=True),
 binary=digest(work/'build/benchmark'),preparation_binary=digest(work/'build/prepare'),
 sources=[digest(p) for p in sorted(source.rglob('*')) if p.is_file()],
 harness_sources=[digest(p) for p in sorted(TOOLS.glob('*')) if p.is_file() and p.suffix in ('.py','.c','.json','.md')],
 repository_build_files=[digest(repo/n) for n in ('Makefile','flake.nix','flake.lock')],
 repository_application_sources=[digest(p) for p in sorted(repo.iterdir()) if p.is_file() and p.suffix in ('.c','.h')])
(work/'build/build.json').write_text(json.dumps(record,indent=2)+'\n')
print(work/'build/build.json')
