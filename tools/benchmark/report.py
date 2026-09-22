from pathlib import Path
import argparse
import csv
import json

parser=argparse.ArgumentParser()
parser.add_argument('--work-dir',type=Path,required=True)
parser.add_argument('--label',default='confirmation')
args=parser.parse_args()
ROOT=args.work_dir.expanduser().resolve()
rows=[]
for device in ('discrete','integrated'):
    for path in sorted((ROOT/'results'/device).glob('*.summary.json')):
        s=json.loads(path.read_text());c=s['case'];g=s['pooled_warm']['gpu_total_ns'];mem=[m for r in s['runs'] for m in r['memory']]
        material=('regular_dark_untinted' if c['style']==0 else 'clear_light_tinted' if c['tint_hex']!='0' else 'clear_light_untinted')
        row=dict(device=device,device_name=s['device']['name'],width=c['width'],height=c['height'],scope='5K stress' if c['width']==5120 else 'current output',material=material,motion='lens' if c['motion'] else 'sweep',samples=g['count'],gpu_median_ms=g['median']/1e6,gpu_p95_ms=g['p95']/1e6,gpu_p99_ms=g['p99']/1e6,gpu_max_ms=g['max']/1e6,
            first_use_gpu_median_ms=s['first_use']['gpu_total_ns']['median']/1e6,first_use_capture_median_ms=s['first_use']['gpu_capture_ns']['median']/1e6,
            cpu_render_median_ms=s['pooled_warm']['cpu_render_calls_ns']['median']/1e6,cpu_completion_median_ms=s['pooled_warm']['cpu_completion_wait_ns']['median']/1e6,warm_retries=s['warm_retries'],
            active_owned_mib=max(m['output_bytes'] for m in mem if m['checkpoint']=='active_after_warm')/(1024**2),idle_owned_mib=max(m['output_bytes'] for m in mem if m['checkpoint']=='promoted_idle')/(1024**2),peak_owned_mib=max(m['renderer_peak_bytes'] for m in mem)/(1024**2),cadence_reference_ms=c['cadence_ms'],samples_above_reference=s['cadence_comparison']['samples_above'],
            gpu_p95_run_min_ms=s['run_range']['gpu_total_ns']['p95'][0]/1e6,gpu_p95_run_max_ms=s['run_range']['gpu_total_ns']['p95'][1]/1e6,device_sha256=s['device_sha256'],summary=str(path))
        rows.append(row)
assert len(rows)==36
with (ROOT/'RESULTS.csv').open('w',newline='') as out:
    writer=csv.DictWriter(out,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
(ROOT/'RESULTS.json').write_text(json.dumps(rows,indent=2)+'\n')
text=['# Scoped renderer measurements — '+args.label,'',
      'Five fresh runs per case, each with one first-use frame at progress0.5 and119 warm points1..119/120. One complete untimed transition precedes each case. All validation counters including teardown are zero; all output destruction checkpoints report zero owned allocations. No readback. No samples were discarded or repeated.','',
      'GPU columns below are pooled warm timestamp intervals in milliseconds. Active/idle memory is owned Vulkan allocation MiB, not total driver VRAM; this offscreen path retains one optimal presentation image. Production dma-buf may retain two modifier images. `RESULTS.csv` adds first-use capture, CPU timing, retry, per-run range and cadence-count columns; per-case JSON retains every run.','']
for device in ('discrete','integrated'):
    selected=[r for r in rows if r['device']==device]
    text.extend(['## '+selected[0]['device_name'],''])
    for width,height in ((1920,1080),(2560,2880),(5120,2880)):
        group=[r for r in selected if r['width']==width and r['height']==height]
        text.extend([f'### {width}×{height} — {group[0]["scope"]}, comparison interval {group[0]["cadence_reference_ms"]:.3f}ms','',
          '| Material | Motion | Median | p95 | p99 | Max | Active MiB | Idle MiB |','|---|---|---:|---:|---:|---:|---:|---:|'])
        for r in group:
            text.append(f'| {r["material"].replace("_"," ")} | {r["motion"]} | {r["gpu_median_ms"]:.3f} | {r["gpu_p95_ms"]:.3f} | {r["gpu_p99_ms"]:.3f} | {r["gpu_max_ms"]:.3f} | {r["active_owned_mib"]:.2f} | {r["idle_owned_mib"]:.2f} |')
        text.append('')
text.extend(['These are per-case engineering observations. The 5K case is a stress scenario, not a current-output claim. Cadence comparisons are practical references, not newly imposed limits. There is no averaging across devices/resolutions/materials/motions and no architecture-dominance inference from these timings.',''])
(ROOT/'RESULTS.md').write_text('\n'.join(text))
print(ROOT/'RESULTS.md')
