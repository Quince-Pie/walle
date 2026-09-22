"""Optional private-compositor upload-failure and two-output app controls."""
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

SOURCE_ROOT=Path(__file__).resolve().parent
PROJECT=SOURCE_ROOT.parents[1]
ROOT=PROJECT/"build/tests/upload-faults"
TRACE=PROJECT/"build/tests/lifecycle/trace.so"
COLORS=((64,32,192),(216,128,32))

def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def inside(run,binary,device,stage,multi):
    report={'cleanup':[],'passed':False};app=None;environment=os.environ.copy()
    environment['WALLE_VULKAN_VALIDATION']='1';environment['VK_LAYER_VALIDATE_SYNC']='1'
    if multi:
        symbols=subprocess.check_output(['nm','-a',str(binary)],text=True)
        offset=next(line.split()[0]for line in symbols.splitlines()if line.endswith(' render_thread_worker'))
        environment.update(LD_PRELOAD=str(TRACE),WALLE_LIFECYCLE_TRACE=str(run/'trace.jsonl'),WALLE_WORKER_OFFSET=offset)
    else:
        environment.update(LD_PRELOAD=str(ROOT/'submit_fault.so'),WALLE_FAIL_UPLOAD=str(stage),WALLE_FAULT_TRACE=str(run/'trace.jsonl'))
    command=[str(binary),'-c',str(run/'walle.ini'),'--vulkan-device',device]
    if not multi:command+=['--preview',str(run/'frames')]
    started=time.monotonic()
    try:
        with(run/'app-output.txt').open('w')as stream:
            app=subprocess.Popen(command,env=environment,stdout=stream,stderr=subprocess.STDOUT);report['app_pid']=app.pid
            if multi:
                import numpy as np
                from PIL import Image
                evidence={};deadline=time.monotonic()+25
                while time.monotonic()<deadline:
                    if app.poll()is not None:raise RuntimeError(f'early app exit:{app.returncode}')
                    trace=run/'trace.jsonl';events=[json.loads(x)for x in trace.read_text().splitlines()if x.endswith('}')]if trace.exists()else[]
                    jobs=[e for e in events if e['event']=='decode_start'];outputs={e['output']for e in jobs}
                    if len(outputs)==2 and all({0,1}.issubset({e['index']for e in jobs if e['output']==o})for o in outputs):
                        for name in('HEADLESS-1','HEADLESS-2'):
                            shot=subprocess.run([shutil.which('grim')or'grim','-o',name,'-t','png','-'],capture_output=True,timeout=5)
                            if shot.returncode:raise RuntimeError(shot.stderr.decode())
                            image=Image.open(BytesIO(shot.stdout)).convert('RGB');pixels=np.asarray(image)
                            fractions=[float(np.all(pixels==c,axis=2).mean())for c in COLORS]
                            if max(fractions)<.995 and sum(fractions)>.01:
                                (run/f'{name}-active.png').write_bytes(shot.stdout)
                                evidence[name]={'fractions':fractions,'size':image.size,'ns':time.monotonic_ns()}
                        if len(evidence)==2:
                            report.update(outputs=list(outputs),jobs=jobs,active_output_frames=evidence);break
                    time.sleep(.05)
                else:raise TimeoutError('two outputs must each create A/B jobs and produce a mixed frame')
                app.send_signal(signal.SIGTERM);report['signal']='SIGTERM after both output frame observations'
                report['app_returncode']=app.wait(timeout=10)
            else:report['app_returncode']=app.wait(timeout=25)
        report['elapsed']=time.monotonic()-started
        output=(run/'app-output.txt').read_text()
        report['validation_or_sanitizer_errors']=[line for line in output.splitlines()if any(x in line for x in('[Vulkan ERROR]','Validation failed:','AddressSanitizer','runtime error:'))]
        assert not report['validation_or_sanitizer_errors'],report['validation_or_sanitizer_errors']
        trace=[json.loads(x)for x in(run/'trace.jsonl').read_text().splitlines()if x.endswith('}')]
        report['trace']=trace
        if multi:
            assert report['app_returncode']==0
            assert all(e['state']==0 for e in report['jobs'])
            assert 'Shutting down...'in output
        else:
            summary=next(e for e in trace if e['event']=='summary');report['summary']=summary
            assert summary['injections']==1 and summary['uploads']==stage,summary
            assert summary['source_opened']>=(1 if stage==1 else 2),summary
            assert summary['source_remaining']==0 and summary['source_opened']==summary['source_closed'],summary
            assert report['app_returncode']==1,report['app_returncode']
            assert '[PREVIEW] Failed: wallpaper texture upload failed'in output
            assert not(run/'frames/frames.json').exists()
        report['passed']=True
    except Exception as error:report['failure']=repr(error)
    finally:
        if app and app.poll()is None:
            app.terminate();report['cleanup'].append('SIGTERM owned app after failure')
            try:app.wait(timeout=3)
            except subprocess.TimeoutExpired:app.kill();app.wait();report['cleanup'].append('SIGKILL owned app')
        if app:report['app_returncode']=app.returncode
        (run/'child-result.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['passed'] else 1

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=PROJECT/'build/bin/release/walle')
    parser.add_argument('--stage',type=int,choices=(1,2,3),default=1)
    parser.add_argument('--multi',action='store_true');parser.add_argument('--device',default='discrete')
    parser.add_argument('--inside',type=Path);parser.add_argument('--output',type=Path)
    a=parser.parse_args()
    if a.inside:return inside(a.inside,a.binary.resolve(),a.device,a.stage,a.multi)
    ROOT.mkdir(parents=True,exist_ok=True)
    run=a.output.resolve()if a.output else Path(tempfile.mkdtemp(prefix='run-',dir=ROOT))
    if a.output:run.mkdir(parents=True,exist_ok=False)
    runtime=run/'runtime';runtime.mkdir(mode=0o700);compositor=run/'compositor';compositor.mkdir();(run/'frames').mkdir()
    (compositor/'rc.xml').write_text('<labwc_config><core><gap>0</gap></core></labwc_config>\n')
    for i,c in enumerate(COLORS):(run/f'image-{i}.ppm').write_bytes(b'P6\n320 180\n255\n'+bytes(c)*(320*180))
    (run/'walle.ini').write_text('[default]\nfiles=\n'+''.join(f'    fill_center:{run/f"image-{i}.ppm"}\n'for i in range(2))+f'timeout={1 if a.multi else 0}\nrandomize=false\ngamemode=false\ntransition=true\ntransition_duration=2\ntransition_variant=regular\ntransition_appearance=light\ntransition_motion=sweep\ntransition_tint=none\n')
    env=os.environ.copy()
    for key in('DBUS_SESSION_BUS_ADDRESS','WAYLAND_DISPLAY','DISPLAY','LABWC_PID','SWAYSOCK','I3SOCK','VK_LAYER_ENABLES','LD_PRELOAD'):env.pop(key,None)
    env.update(XDG_RUNTIME_DIR=str(runtime),XDG_CONFIG_HOME=str(compositor),XDG_CACHE_HOME=str(run/'cache'),
               WLR_BACKENDS='headless',WLR_HEADLESS_OUTPUTS='2'if a.multi else'1',WLR_RENDERER='vulkan',
               WLR_RENDER_DRM_DEVICE='/dev/dri/renderD128',LABWC_UPDATE_ACTIVATION_ENV='0')
    child=[sys.executable,str(Path(__file__).resolve()),'--inside',str(run),'--binary',str(a.binary.resolve()),'--device',a.device,'--stage',str(a.stage)]
    if a.multi:child+=['--multi']
    command=[shutil.which('labwc')or'labwc','-C',str(compositor),'-S',shlex.join(child)]
    report={'command':command,'binary_sha256':digest(a.binary),'mode':'multi-output'if a.multi else'upload-failure','stage':a.stage,'cleanup':[]}
    with(run/'compositor-output.txt').open('w')as stream:
        proc=subprocess.Popen(command,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True);report['compositor_pid']=proc.pid
        try:report['compositor_returncode']=proc.wait(timeout=45)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGTERM);report['cleanup'].append('SIGTERM owned process group')
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait();report['cleanup'].append('SIGKILL owned process group')
            report['compositor_returncode']=proc.returncode
    result=run/'child-result.json';report['child']=json.loads(result.read_text())if result.exists()else None
    report['binary_unchanged']=digest(a.binary)==report['binary_sha256']
    report['passed']=report['binary_unchanged']and report['compositor_returncode']==0 and bool(report['child']and report['child']['passed'])and not report['cleanup']
    (run/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(run);print(json.dumps(report));return 0 if report['passed']else 1
if __name__=='__main__':raise SystemExit(main())
