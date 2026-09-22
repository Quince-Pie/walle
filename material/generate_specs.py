"""Generate typed C source definitions, never sampled material outputs."""
from pathlib import Path
import math
import json
ROOT=Path(__file__).resolve().parent
templates=json.loads((ROOT/'source_spec_templates.json').read_text())['templates']
def d(x):return ('INFINITY' if x>0 else '-INFINITY') if math.isinf(x) else float(x).hex()
def f(x):return d(x)+'f'
def arr(a,fn=d):return '{'+','.join(fn(x)for x in a)+'}'
def mp(v):return '{.in='+arr(v['inputRange'])+',.out='+arr(v['outputRange'])+',.upper='+d(v['upperMaxInputResult']or 0)+',.transition='+d(v['upperMaxInputTransition']or 0)+',.reverse='+str(bool(v['reverseOutput'])).lower()+',.has_upper='+str(v['upperMaxInputResult']is not None and v['upperMaxInputTransition']is not None).lower()+'}'
def ycc(v):
 keys=('normalFill','dodgeFill','burnFill');mask=sum(1<<i for i,k in enumerate(keys)if v[k]is not None)
 return '{.black='+f(v['black'])+',.white='+f(v['white'])+',.saturation='+f(v['saturation'])+',.fill={'+','.join(arr(v[k]or(0,0,0,0),f)for k in keys)+'},.mask='+str(mask)+'}'
def iom(v):return '{.value='+d(v.get('const',0))+',.mapped='+str(v['tag']==1).lower()+((', .map='+mp(v['clm']))if v['tag']==1 else'')+'}'
out=['/* Source SpecV1 definitions; see generate_specs.py/source_manifest.json. */','static const struct wm_spec wm_specs[4] = {']
for name in ('regular_light','regular_dark','clear_light','clear_dark'):
 s=templates[name];sh=s['shadow'];b=s['blur'];r=s['refraction'];face=s['faceEffect'];e=s['edgeBleed'];h=s['highlights']
 out.append('/* '+name+' */{.backdrop_scale='+f(s['backdropScale'])+',')
 out.append('.shadow={.height='+d(sh['normalizedHeight'])+',.amount='+d(sh['normalizedAmount'])+',.blur_radius='+d(sh['blurRadius'])+',.radius='+d(sh['shadowRadius'])+',.offset='+arr(sh['offset'])+',.opacity='+mp(sh['opacity'])+',.vibrancy='+mp(sh['vibrancyContribution'])+',.ycc='+ycc(sh['ycc'])+'},')
 out.append('.blur={.opacity='+f(b['opacity'])+',.opacities='+arr(b['opacities'],f)+',.distances='+arr([x['value']for x in b['distances']])+',.distance_kinds='+arr([x['kind']for x in b['distances']],str)+',.radius='+mp(b['blurRadius'])+'},')
 out.append('.refraction={'+','.join('.'+cn+'='+d(r[jn])for cn,jn in(('inner_height','normalizedInnerHeight'),('inner_amount','normalizedInnerAmount'),('outer_height','normalizedOuterHeight'),('outer_amount','normalizedOuterAmount')))+',.inner_height_range='+arr(r['innerHeightRange'])+',.inner_amount_range='+arr(r['innerAmountRange'])+',.outer_opacity='+f(r['outerOpacity'])+'},')
 out.append('.face={.opacity='+f(face['opacity'])+',.ycc='+ycc(face['ycc'])+'},')
 out.append('.bleed={.amount='+d(e['normalizedAmount'])+',.height='+d(e['normalizedHeight'])+',.blur_radius='+d(e['normalizedBlurRadius'])+',.maximum_blur='+d(e['maxBlurRadius'])+',.opacity='+mp(e['opacity'])+',.ycc='+ycc(e['ycc'])+',.darken='+str(bool(e['useDarkenBlending'])).lower()+'},')
 out.append('.highlight={.hdr='+f(h['white'])+',.key_opacity='+f(h['keyOpacity'])+',.fill_opacity='+f(h['fillOpacity'])+',.curvature='+d(h['curvature'])+',.amount='+d(h['amount'])+',.key_offset='+d(h['keyOffset'])+',.fill_offset='+d(h['fillOffset'])+',.spread='+iom(h['spread'])+',.key_height='+iom(h['keyHeight'])+',.fill_height='+iom(h['fillHeight'])+',.key_ycc='+ycc(h['keyYCC'])+',.fill_ycc='+ycc(h['fillYCC'])+'}},')
out.append('};')
(ROOT/'material_specs.h').write_text('\n'.join(out)+'\n')
