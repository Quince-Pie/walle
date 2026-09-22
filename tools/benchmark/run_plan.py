"""Print or serially execute the fixed five-run confirmation matrix."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import time
from summarize import summarize
parser=argparse.ArgumentParser()
parser.add_argument('--repo-root',type=Path,required=True)
parser.add_argument('--work-dir',type=Path,required=True)
parser.add_argument('--device',required=True,choices=('discrete','integrated'))
parser.add_argument('--execute',action='store_true',help='requires the exclusive coordinated GPU lease')
parser.add_argument('--out',type=Path)
args=parser.parse_args();repo=args.repo_root.expanduser().resolve();work=args.work_dir.expanduser().resolve()
build=json.loads((work/'build/build.json').read_text());inputs=json.loads((work/'inputs/inputs.json').read_text())
assert build['repo_root']==str(repo)==inputs['repo_root']
assert hashlib.sha256(Path(build['binary']['path']).read_bytes()).hexdigest()==build['binary']['sha256']
for item in build['harness_sources']+build['repository_build_files']+build['repository_application_sources']:
    if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()!=item['sha256']:
        raise SystemExit('source/provenance drift; rebuild before measurement: '+item['path'])

for item in inputs['inputs']:assert hashlib.sha256(Path(item['prepared']).read_bytes()).hexdigest()==item['prepared_sha256']
materials=(('clear_light_untinted',1,0,'0'),('regular_dark_untinted',0,1,'0'),('clear_light_tinted',1,0,'20bc9b96'))
cases=[]
for w,h,cadence,label in ((1920,1080,4.171,'current_239.760Hz'),(2560,2880,16.676,'current_59.967Hz'),(5120,2880,16.667,'stress_60Hz')):
 for material,style,dark,tint in materials:
  for motion,name in ((0,'sweep'),(1,'lens')):
   tag=f'{w}x{h}_{material}_{name}'
   command=[str(work/'build/benchmark'),args.device,str(w),str(h),str(work/f'inputs/{w}x{h}/a.rgba'),str(work/f'inputs/{w}x{h}/b.rgba'),str(style),str(motion),str(dark),tint,str(cadence)]
   cases.append(dict(tag=tag,display_scope=label,command=command))
if not args.execute:print(json.dumps(cases,indent=2));raise SystemExit(0)
out=args.out.expanduser().resolve() if args.out else work/'results'/args.device
if out==repo or repo in out.parents:raise SystemExit('raw result --out must be outside the repository')
out.mkdir(parents=True,exist_ok=False)
(out/'provenance.json').write_text(json.dumps(dict(build=build,inputs=inputs,cases=cases),indent=2)+'\n')
env=os.environ.copy();env.update(WALLE_VULKAN_VALIDATION='1',VK_LAYER_VALIDATE_SYNC='1');env.pop('VK_LAYER_ENABLES',None)
results=[];start=time.monotonic()
for case in cases:
 before=time.monotonic();stdout=out/(case['tag']+'.jsonl');stderr=out/(case['tag']+'.stderr')
 with stdout.open('w') as a,stderr.open('w') as b:
  try:process=subprocess.run(case['command'],env=env,stdout=a,stderr=b,timeout=180);code=process.returncode
  except subprocess.TimeoutExpired:code=124
 row=dict(case=case['tag'],returncode=code,elapsed_seconds=time.monotonic()-before)
 results.append(row);(out/'execution.json').write_text(json.dumps(results,indent=2)+'\n');print(row,flush=True)
 if code:raise SystemExit(code)
 summary=summarize(stdout);(out/(case['tag']+'.summary.json')).write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(dict(device=args.device,cases=len(cases),elapsed_seconds=time.monotonic()-start)))
