"""CPU-only Vulkan dimension admission checks; build shaders/protocols first."""
from pathlib import Path
import argparse
import shlex
import subprocess
import tempfile
parser=argparse.ArgumentParser()
parser.add_argument('--repo-root',type=Path,required=True)
parser.add_argument('--cc',default='gcc')
args=parser.parse_args();repo=args.repo_root.expanduser().resolve()
source=Path(__file__).resolve().with_name('renderer_bounds.c')
# The C file belongs in repo/tests; it includes ../vulkan_renderer.c directly.
if source.parent != repo/'tests':
    raise SystemExit('install renderer_bounds.c and this runner in repo/tests before invoking')
pkg=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','vulkan','wayland-client','libdrm'],text=True,timeout=30))
with tempfile.TemporaryDirectory(prefix='walle-renderer-bounds-') as directory:
    binary=Path(directory)/'bounds'
    command=[*shlex.split(args.cc),'-std=c23','-O2','-ffp-contract=off','-Wall','-Wextra','-Wpedantic','-Wshadow','-Wimplicit-fallthrough','-Werror',
      '-I'+str(repo),'-I'+str(repo/'material'),str(source),str(repo/'protocols/linux-dmabuf-v1.c'),
      '-Wl,--wrap=vkCreateImage','-Wl,--wrap=vkDestroyImage','-Wl,--wrap=vkWaitForFences','-o',str(binary),*pkg,'-lm']
    subprocess.run(command,check=True,timeout=120)
    subprocess.run([str(binary)],check=True,timeout=30)
