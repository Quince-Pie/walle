"""Run the actual Walle layer-shell process in a private headless compositor."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import shutil
import subprocess
import sys
import tempfile
import time

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--binary',type=Path,required=True)
p.add_argument('--variant',choices=('clear','regular'),default='clear')
p.add_argument('--appearance',choices=('light','dark','auto'),default='light')
p.add_argument('--motion',choices=('sweep','lens'),default='sweep')
p.add_argument('--tint',default='none')
p.add_argument('--device',default='discrete')
p.add_argument('--child-env',action='append',default=[],metavar='NAME=VALUE')
p.add_argument('--output',type=Path)
p.add_argument('--image-a',type=Path,default=Path('~/.config/bg/0008.jpg').expanduser())
p.add_argument('--image-b',type=Path,default=Path('~/.config/bg/0010.jpg').expanduser())
a=p.parse_args()
base=Path.cwd()/'build/verification'
base.mkdir(parents=True,exist_ok=True)
run=a.output or Path(tempfile.mkdtemp(prefix='walle-preview-',dir=base))
if a.output:run.mkdir(parents=True,exist_ok=False)
runtime=run/'runtime';runtime.mkdir(mode=0o700)
config=run/'compositor';config.mkdir()
(config/'rc.xml').write_text('<labwc_config><core><gap>0</gap></core></labwc_config>\n')
frames=run/'frames';frames.mkdir()
wall=run/'walle.ini'
wall.write_text(f'''[default]
files =
    fill_center:{a.image_a.resolve()}
    fill_center:{a.image_b.resolve()}
timeout = 0
randomize = false
gamemode = false
transition = true
transition_duration = 2.4
transition_variant = {a.variant}
transition_appearance = {a.appearance}
transition_motion = {a.motion}
transition_tint = {a.tint}
''')
env=os.environ.copy()
for key in ('DBUS_SESSION_BUS_ADDRESS','WAYLAND_DISPLAY','DISPLAY','LABWC_PID','SWAYSOCK','I3SOCK','VK_LAYER_ENABLES'):
 env.pop(key,None)
env.update(XDG_RUNTIME_DIR=str(runtime),XDG_CONFIG_HOME=str(config),XDG_CACHE_HOME=str(run/'cache'),
 WLR_BACKENDS='headless',WLR_HEADLESS_OUTPUTS='1',WLR_RENDERER='vulkan',WLR_RENDER_DRM_DEVICE='/dev/dri/renderD128',
 WALLE_VULKAN_VALIDATION='1',VK_LAYER_VALIDATE_SYNC='1',LABWC_UPDATE_ACTIVATION_ENV='0')
child=[str(a.binary.resolve()),'-c',str(wall),'--vulkan-device',a.device,'--preview',str(frames)]
if a.child_env:
 child=['env',*a.child_env,*child]
status_file=run/'child-status.json'
wrapper="import json,subprocess,sys; p=subprocess.Popen(sys.argv[2:]); code=p.wait(); open(sys.argv[1],'w').write(json.dumps({'pid':p.pid,'returncode':code})); sys.exit(code)"
labwc=shutil.which('labwc')
if not labwc:raise SystemExit('labwc is required for isolated compositor checks')
command=[labwc,'-C',str(config),'-S',shlex.join([sys.executable,'-c',wrapper,str(status_file),*child])]
r={'command':command,'binary_sha256':hashlib.sha256(a.binary.read_bytes()).hexdigest(),'timeout':60,'cleanup':[]}
start=time.monotonic()
with (run/'output.txt').open('w') as stream:
 proc=subprocess.Popen(command,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
 r['pid']=proc.pid
 try:r['returncode']=proc.wait(timeout=60)
 except subprocess.TimeoutExpired:
  os.killpg(proc.pid,signal.SIGTERM);r['cleanup'].append('SIGTERM owned process group')
  try:r['returncode']=proc.wait(timeout=3)
  except subprocess.TimeoutExpired:
   os.killpg(proc.pid,signal.SIGKILL);r['cleanup'].append('SIGKILL owned process group');r['returncode']=proc.wait()
r['elapsed']=time.monotonic()-start
r['child']=json.loads(status_file.read_text()) if status_file.exists() else None
text=(run/'output.txt').read_text()
meta=frames/'frames.json'
r['metadata']=json.loads(meta.read_text()) if meta.exists() else None
images=list(frames.glob('frame-*.bgra'))
r['frames']=len(images)
r['errors']=[line for line in text.splitlines() if any(s in line for s in ('[Vulkan ERROR]','Validation failed:','runtime error:','AddressSanitizer','FATAL:'))]
r['passed']=r['returncode']==0 and r['child'] is not None and r['child']['returncode']==0 and len(images)==61 and r['metadata'] is not None and not r['errors'] and not r['cleanup']
if r['metadata']:
 expected=r['metadata']['width']*r['metadata']['height']*4
 r['passed']=r['passed'] and all(x.stat().st_size==expected for x in images)
(run/'result.json').write_text(json.dumps(r,indent=2)+'\n')
print(run)
print(json.dumps(r))
raise SystemExit(0 if r['passed'] else 1)
