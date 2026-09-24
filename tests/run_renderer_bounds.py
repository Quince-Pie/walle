"""CPU-only Vulkan dimension admission checks; build shaders/protocols first."""
from pathlib import Path
import argparse
import contextlib
import json
import shlex
import subprocess
import tempfile
parser=argparse.ArgumentParser()
parser.add_argument('--repo-root',type=Path,required=True)
parser.add_argument('--cc',default='gcc')
parser.add_argument('--profile',choices=('fortify','sanitize'),default='fortify')
parser.add_argument('--case',choices=('all','bounds','presentation'),default='all')
parser.add_argument('--build-dir',type=Path)
args=parser.parse_args();repo=args.repo_root.expanduser().resolve()
source=Path(__file__).resolve().with_name('renderer_bounds.c')
# The C file belongs in repo/tests; it includes ../vulkan_renderer.c directly.
if source.parent != repo/'tests':
    raise SystemExit('install renderer_bounds.c and this runner in repo/tests before invoking')
pkg=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','vulkan','wayland-client','libdrm'],text=True,timeout=30))
if args.build_dir:
    args.build_dir=args.build_dir.expanduser().resolve()
    args.build_dir.mkdir(parents=True,exist_ok=True)
context=(contextlib.nullcontext(str(args.build_dir)) if args.build_dir
         else tempfile.TemporaryDirectory(prefix='walle-renderer-bounds-'))
with context as directory:
    cases=(('renderer_bounds.c',('vkCreateImage','vkDestroyImage','vkWaitForFences')),
           ('newpresent_admission.c',('vkGetPhysicalDeviceImageFormatProperties',
             'vkGetPhysicalDeviceImageFormatProperties2','vkCreateImage','vkGetImageMemoryRequirements',
             'vkAllocateMemory','vkBindImageMemory','vkCreateImageView','vkDestroyImage',
             'vkDestroyImageView','vkFreeMemory','vkEnumeratePhysicalDevices',
             'vkCreateWaylandSurfaceKHR','vkGetPhysicalDeviceFormatProperties2',
             'wl_proxy_get_version','wl_proxy_marshal_flags')))
    for filename,wrappers in cases:
        if (args.case=='bounds' and filename!='renderer_bounds.c'
            or args.case=='presentation' and filename!='newpresent_admission.c'):
            continue
        binary=Path(directory)/Path(filename).stem
        profile=(['-O2','-U_FORTIFY_SOURCE','-D_FORTIFY_SOURCE=3','-fstack-protector-strong']
                 if args.profile=='fortify' else ['-O1','-g3','-U_FORTIFY_SOURCE',
                 '-D_FORTIFY_SOURCE=0','-fsanitize=address,undefined',
                 '-fno-sanitize-recover=all','-fno-omit-frame-pointer'])
        command=[*shlex.split(args.cc),'-std=c23',*profile,'-ffp-contract=off','-fno-fast-math','-Wall','-Wextra','-Wpedantic','-Wshadow','-Wimplicit-fallthrough','-Werror',
          '-ffunction-sections','-fdata-sections','-I'+str(repo),'-I'+str(repo/'material'),
          str(source.with_name(filename)),str(repo/'protocols/linux-dmabuf-v1.c'),
          '-Wl,--gc-sections',*[f'-Wl,--wrap={name}' for name in wrappers],'-o',str(binary),*pkg,'-lm']
        if args.build_dir:
            (Path(directory)/(binary.name+'.command.json')).write_text(json.dumps(command,indent=2)+'\n')
        subprocess.run(command,check=True,timeout=120)
        subprocess.run([str(binary)],check=True,timeout=30)
