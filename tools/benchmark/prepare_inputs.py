"""Prepare the declared original-image workload outside measurement."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
parser=argparse.ArgumentParser()
parser.add_argument('--repo-root',type=Path,required=True)
parser.add_argument('--work-dir',type=Path,required=True)
parser.add_argument('--image-a',type=Path,default=Path.home()/'.config/bg/0008.jpg')
parser.add_argument('--image-b',type=Path,default=Path.home()/'.config/bg/0010.jpg')
args=parser.parse_args();work=args.work_dir.expanduser().resolve();repo=args.repo_root.expanduser().resolve()
build=json.loads((work/'build/build.json').read_text());assert build['repo_root']==str(repo)
inputs=[args.image_a.expanduser().resolve(),args.image_b.expanduser().resolve()]
if any(not p.is_file() for p in inputs):raise SystemExit('supply existing --image-a and --image-b files')
records=[]
for w,h in ((1920,1080),(2560,2880),(5120,2880)):
    out=work/'inputs'/f'{w}x{h}';out.mkdir(parents=True,exist_ok=True)
    for label,source in zip(('a','b'),inputs,strict=True):
        target=out/(label+'.rgba')
        command=[str(work/'build/prepare'),str(source),str(w),str(h),str(target)]
        subprocess.run(command,check=True);payload=target.read_bytes()
        assert len(payload)==w*h*4 and set(payload[3::4])=={255}
        records.append(dict(label=label,width=w,height=h,source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),prepared=str(target),prepared_sha256=hashlib.sha256(payload).hexdigest(),bytes=len(payload),command=command))
record=dict(repo_root=str(repo),method='libvips thumbnail centered cover/no_rotate; sRGB; black flatten if alpha; opaque alpha; uchar',preparation_outside_timing=True,inputs=records)
(work/'inputs/inputs.json').write_text(json.dumps(record,indent=2)+'\n');print(work/'inputs/inputs.json')
