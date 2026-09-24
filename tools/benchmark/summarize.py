from pathlib import Path
import hashlib
import json
import math
import sys

METRICS=('gpu_total_ns','gpu_frame_ns','gpu_scene_ns','gpu_capture_ns','gpu_draw_ns','gpu_tail_ns',
         'cpu_build_ns','cpu_render_calls_ns','cpu_retry_wait_ns','cpu_completion_wait_ns','cpu_total_ns')
def quantile(values,q):
    ordered=sorted(values);position=(len(ordered)-1)*q;low=math.floor(position);high=math.ceil(position)
    return ordered[low]+(ordered[high]-ordered[low])*(position-low)
def stats(values):
    return dict(count=len(values),median=quantile(values,.5),p95=quantile(values,.95),p99=quantile(values,.99),max=max(values))
def summarize(path):
    records=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    case=next(r for r in records if r['type']=='case')
    assert case.get('scenario')=='actual_app_horizontal_center' and case.get('origin')==[.5,.5] and case.get('direction')==[1,0]
    ends=[r for r in records if r['type']=='run_end']
    assert len(ends)==6 and {r['run'] for r in ends}==set(range(6))
    assert all(r['status']=='PASS' and r['validation_errors']==0
               and r['owned_bytes_after_renderer_destroy']==0
               and r['owned_allocations_after_renderer_destroy']==0 for r in ends)
    lifecycle={name:stats([r[name] for r in ends if r['run']]) for name in ('startup_ns','teardown_ns')}
    runs=[]
    warm_all=[];first=[]
    for run in range(1,6):
        warm=[r for r in records if r['type']=='sample' and r['run']==run and r['phase']=='warm']
        cold=[r for r in records if r['type']=='sample' and r['run']==run and r['phase']=='first_use']
        assert len(warm)==119 and [r['index'] for r in warm]==list(range(1,120))
        assert all(r['built_backdrop']==r['required_backdrop'] for r in warm)
        assert len(cold)==1 and cold[0]['built_backdrop'] and cold[0]['progress']==.5
        warm_all.extend(warm);first.extend(cold)
        runs.append(dict(run=run,metrics={m:stats([r[m] for r in warm]) for m in METRICS},retries=sum(r['retries'] for r in warm),
            first_use=cold[0],memory=[r for r in records if r['type']=='memory' and r['run']==run]))
    devices=[r for r in records if r['type']=='device']
    identities=[{k:v for k,v in d.items() if k not in ('type','run')} for d in devices]
    assert identities and all(d==identities[0] for d in identities)
    device_hash=hashlib.sha256(json.dumps(identities[0],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    pooled={m:stats([r[m] for r in warm_all]) for m in METRICS}
    ranges={m:{s:[min(r['metrics'][m][s] for r in runs),max(r['metrics'][m][s] for r in runs)] for s in ('median','p95','p99','max')} for m in METRICS}
    cadence=case['cadence_ms']*1e6
    return dict(case=case,device=identities[0],device_sha256=device_hash,lifecycle=lifecycle,
        quantiles='linear interpolation at (n-1)*q; units ns',runs=runs,pooled_warm=pooled,run_range=ranges,
        first_use={m:stats([r[m] for r in first]) for m in METRICS},warm_retries=sum(r['retries'] for r in warm_all),
        cadence_comparison=dict(interval_ns=cadence,samples_above=sum(r['gpu_total_ns']>cadence for r in warm_all),samples=len(warm_all)),
        scope='one device/resolution/material/motion; no averaging across cases; offscreen same renderer path, one optimal present image; no architecture dominance inference')
if __name__=='__main__':
    path=Path(sys.argv[1]);result=summarize(path)
    target=Path(sys.argv[2]) if len(sys.argv)>2 else path.with_suffix('.summary.json')
    target.write_text(json.dumps(result,indent=2)+'\n');print(target)
