"""Actual-app lifecycle controls inside one private Labwc process group."""
import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import numpy as np
from PIL import Image

SOURCE_ROOT=Path(__file__).resolve().parent
PROJECT=SOURCE_ROOT.parents[1]
ROOT=PROJECT/"build/tests/lifecycle"
COLORS=((64,32,192),(216,128,32),(16,192,128))

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_config(run,changed=False):
    text='[default]\nfiles =\n'+''.join(f'    fill_center:{run/f"image-{i}.ppm"}\n' for i in range(3))
    text+='timeout=1\nrandomize=false\ngamemode=false\ntransition=true\n'
    text+=('transition_duration=6\ntransition_variant=clear\ntransition_appearance=dark\ntransition_motion=lens\ntransition_tint=#29C78F80\n'
           if changed else 'transition_duration=4.5\ntransition_variant=regular\ntransition_appearance=light\ntransition_motion=sweep\ntransition_tint=none\n')
    temporary=run/'next.ini';temporary.write_text(text);os.replace(temporary,run/'walle.ini')

def inner(run,binary,device):
    report={'events':[],'observations':[],'checks':{},'cleanup':[],'passed':False}
    app=None
    trace=run/'trace.jsonl'
    def events():
        if not trace.exists():return []
        lines=trace.read_text().splitlines()
        return [json.loads(line) for line in lines if line.endswith('}')]
    def starts():return [e for e in events() if e['event']=='decode_start']
    def snapshot(label=None):
        result=subprocess.run([shutil.which('grim') or 'grim','-t','png','-'],capture_output=True,timeout=5)
        if result.returncode:raise RuntimeError(f'grim: {result.stderr.decode()}')
        image=Image.open(BytesIO(result.stdout)).convert('RGB');array=np.asarray(image)
        fractions=[float(np.all(array==color,axis=2).mean()) for color in COLORS]
        value={'ns':time.monotonic_ns(),'size':image.size,'fractions':fractions,'sha256':hashlib.sha256(result.stdout).hexdigest()}
        if label:(run/f'{label}.png').write_bytes(result.stdout);value['label']=label;report['observations'].append(value)
        return value
    def wait_for(name,predicate,timeout=25):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if app is not None and app.poll() is not None:raise RuntimeError(f'app exited early:{app.returncode} while {name}')
            result=predicate()
            if result:return result
            # Sampling cadence only; success always requires an observed event/pixel condition.
            time.sleep(.05)
        raise TimeoutError(name)
    def mixed():
        value=snapshot()
        return value if max(value['fractions'])<.995 and sum(value['fractions'])>.01 else None
    def configure_output(w,h,scale):
        result=subprocess.run([str(ROOT/'output-control'),str(w),str(h),str(scale)],capture_output=True,text=True,timeout=8)
        record={'event':'output_control','ns':time.monotonic_ns(),'requested':[w,h,scale],
                'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
        report['events'].append(record)
        if result.returncode:raise RuntimeError(f'private output management unavailable:{record}')
        return record['ns']
    try:
        configure_output(640,360,1)
        symbols=subprocess.check_output(['nm','-a',str(binary)],text=True)
        worker=next(line.split()[0] for line in symbols.splitlines() if line.endswith(' render_thread_worker'))
        environment=dict(os.environ,LD_PRELOAD=str(ROOT/'trace.so'),WALLE_LIFECYCLE_TRACE=str(trace),WALLE_WORKER_OFFSET=worker)
        with (run/'app-output.txt').open('w') as output:
            app=subprocess.Popen([str(binary),'-c',str(run/'walle.ini'),'--vulkan-device',device],env=environment,stdout=output,stderr=subprocess.STDOUT)
            report['app_pid']=app.pid
            wait_for('trace initialized',lambda:any(e['event']=='trace_ready' for e in events()))
            wait_for('initial A frame',lambda:snapshot()['fractions'][0]>.999)
            snapshot('initial-A')
            first=wait_for('B decode started',lambda:next((e for e in starts() if e['index']==1),None))
            wait_for('B transition visibly active',mixed)
            snapshot('before-reload')
            write_config(run,True)
            reload_ns=time.monotonic_ns();report['events'].append({'event':'config_replaced','ns':reload_ns})
            wait_for('hot reload consumed',lambda:'[HOT RELOAD] Updating output' in (run/'app-output.txt').read_text())
            snapshot('after-reload')
            wait_for('B reaches plain target',lambda:snapshot()['fractions'][1]>.999,timeout=25)
            snapshot('plain-B')
            second=wait_for('coalesced C decode',lambda:next((e for e in starts() if e['index']==2),None))
            before_second=[e for e in starts() if first['ns']<=e['ns']<=second['ns']]
            ticks=[e for e in events() if e['event']=='timer_read' and first['ns']<e['ns']<second['ns']]
            report['checks']['coalesced_worker_indices']=[e['index'] for e in before_second]
            report['checks']['timer_expirations_while_B']=sum(e['expirations'] for e in ticks)
            report['checks']['B_job_to_C_job_seconds']=(second['ns']-first['ns'])/1e9
            assert [e['index'] for e in before_second]==[1,2],before_second
            assert sum(e['expirations'] for e in ticks)>=2,ticks
            assert second['ns']-first['ns']>=4_000_000_000,(first,second)
            assert first['variant']==1 and first['motion']==0 and first['appearance']==1 and not first['tinted'],first
            assert second['variant']==0 and second['motion']==1 and second['appearance']==2 and second['rgba']==[41,199,143,128] and second['tinted'],second
            report['checks']['next_job_uses_reloaded_material']=True
            wait_for('C transition visibly active',mixed)
            snapshot('C-before-resize')
            changed=configure_output(800,450,1)
            wait_for('resized decode',lambda:next((e for e in starts() if e['ns']>changed and e['width']==800 and e['height']==450),None))
            wait_for('resized screen',lambda:snapshot()['size']==(800,450))
            snapshot('resized')
            changed=configure_output(800,450,2)
            wait_for('scale-only new decode',lambda:next((e for e in starts() if e['ns']>changed and e['width']==800 and e['height']==450 and e['scale']==2),None))
            report['checks']['resize_and_scale_decode']=True
            wait_for('post-resize transition visibly active',mixed,timeout=25)
            snapshot('before-SIGTERM')
            report['events'].append({'event':'SIGTERM','ns':time.monotonic_ns(),'pid':app.pid})
            app.send_signal(signal.SIGTERM)
            report['app_returncode']=app.wait(timeout=10)
        report['trace']=events()
        report['checks']['all_jobs_started_idle']=all(e['state']==0 for e in starts())
        assert report['checks']['all_jobs_started_idle'],starts()
        assert report['app_returncode']==0,report['app_returncode']
        output=(run/'app-output.txt').read_text()
        report['errors']=[line for line in output.splitlines() if any(token in line for token in
            ('[Vulkan ERROR]','Validation failed:','runtime error:','AddressSanitizer','FATAL:','Transition stopped'))]
        assert not report['errors'],report['errors']
        report['checks']['graceful_shutdown_observed']='Shutting down...' in output
        assert report['checks']['graceful_shutdown_observed']
        report['passed']=True
    except Exception as error:
        report['failure']=repr(error)
    finally:
        if app is not None:
            if app.poll() is None:
                app.terminate();report['cleanup'].append('SIGTERM owned app after failure')
                try:app.wait(timeout=5)
                except subprocess.TimeoutExpired:app.kill();app.wait();report['cleanup'].append('SIGKILL owned app')
            report['app_returncode']=app.returncode
        report['trace']=events()
        (run/'child-result.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['passed'] else 1

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,default=PROJECT/'build/bin/release/walle')
    p.add_argument('--device',default='discrete');p.add_argument('--inside',type=Path);p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.inside:return inner(a.inside,a.binary.resolve(),a.device)
    ROOT.mkdir(parents=True,exist_ok=True)
    expected=(ROOT/'app-source.sha256').read_text().split()[0]
    if digest(PROJECT/'walle.c')!=expected:raise RuntimeError('rebuild lifecycle helpers after app source changes')
    run=a.output.resolve() if a.output else Path(tempfile.mkdtemp(prefix='run-',dir=ROOT))
    if a.output:run.mkdir(parents=True,exist_ok=False)
    runtime=run/'runtime';runtime.mkdir(mode=0o700)
    compositor=run/'compositor';compositor.mkdir()
    (compositor/'rc.xml').write_text('<labwc_config><core><gap>0</gap></core></labwc_config>\n')
    for i,color in enumerate(COLORS):
        (run/f'image-{i}.ppm').write_bytes(b'P6\n320 180\n255\n'+bytes(color)*(320*180))
    write_config(run)
    environment=os.environ.copy()
    for name in('DBUS_SESSION_BUS_ADDRESS','WAYLAND_DISPLAY','DISPLAY','LABWC_PID','SWAYSOCK','I3SOCK','VK_LAYER_ENABLES','LD_PRELOAD'):
        environment.pop(name,None)
    environment.update(XDG_RUNTIME_DIR=str(runtime),XDG_CONFIG_HOME=str(compositor),XDG_CACHE_HOME=str(run/'cache'),
        WLR_BACKENDS='headless',WLR_HEADLESS_OUTPUTS='1',WLR_RENDERER='vulkan',WLR_RENDER_DRM_DEVICE='/dev/dri/renderD128',
        WALLE_VULKAN_VALIDATION='1',VK_LAYER_VALIDATE_SYNC='1',LABWC_UPDATE_ACTIVATION_ENV='0')
    child=[sys.executable,str(Path(__file__).resolve()),'--inside',str(run),'--binary',str(a.binary.resolve()),'--device',a.device]
    command=[shutil.which('labwc') or 'labwc','-C',str(compositor),'-S',shlex.join(child)]
    result={'command':command,'binary_sha256':digest(a.binary),'source_sha256':digest(PROJECT/'walle.c'),'cleanup':[]}
    with (run/'compositor-output.txt').open('w') as output:
        process=subprocess.Popen(command,env=environment,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        result['compositor_pid']=process.pid
        try:result['compositor_returncode']=process.wait(timeout=100)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGTERM);result['cleanup'].append('SIGTERM owned process group')
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait();result['cleanup'].append('SIGKILL owned process group')
            result['compositor_returncode']=process.returncode
    child_result=run/'child-result.json';result['child']=json.loads(child_result.read_text()) if child_result.exists() else None
    result['passed']=result['compositor_returncode']==0 and bool(result['child'] and result['child']['passed'] and result['child']['app_returncode']==0) and not result['cleanup']
    result['binary_unchanged']=digest(a.binary)==result['binary_sha256'];result['passed'] &= result['binary_unchanged']
    (run/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(run);print(json.dumps(result));return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
