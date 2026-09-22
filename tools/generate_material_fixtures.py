"""Generate independent C fixtures from retained Python source, never C output."""
import argparse
from pathlib import Path
import hashlib
import json
import math
import struct
import sys

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host-source',type=Path,required=True)
parser.add_argument('--output-dir',type=Path,required=True)
args=parser.parse_args()
SOURCE=args.host_source.resolve()
OUT=args.output_dir.resolve()
OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(SOURCE))
from lg_material.material_adapter import render_inputs
from lg_renderer import numeric
import lg_host as H
import lg_colormap as CM

def half(values):return struct.pack('<'+'e'*len(values),*values)
def matrix(value):return half(H.vibrant_color_matrix_uniforms(value['m'],clamp=value['clamp']or 0,preserve_hue=value['preserveHue']or 0))
def cbytes(data):
    return '{\n'+''.join('    '+','.join(f'0x{x:02x}' for x in data[i:i+16])+',\n' for i in range(0,len(data),16))+'}'
def number(value):return ('INFINITY' if value>0 else '-INFINITY')if math.isinf(value) else float(value).hex()
def doubles(values):return '{'+','.join(number(v)for v in values)+'}'
def parent_of_gradient(node):
    for child in node.get('sublayers',()):
        if child.get('props',{}).get('effect',{}).get('class')=='CASDFGradientEffect':return node
        found=parent_of_gradient(child)
        if found:return found
    return None

cases=[];ramp_reference=None
tints=(None,(227,93,54,255),(41,199,143,0),(66,111,219,145))
sizes=((96,48,1),(1920,1080,2))
for style in(0,1):
 for dark in(False,True):
  for rgba in tints:
   for w,h,backing in sizes:
    model=dict(style=style,appearance='dark'if dark else'light',key=True,w=w,h=h,scale=backing,adaptive=1)
    if rgba is not None:model['tint']=[v/255 for v in rgba]
    ref=render_inputs(model)
    gb={k:numeric(v)for k,v in ref['gb'].items()if v is not None and k!='inputSourceSublayerName'}
    for key in tuple(gb):
     if key.endswith('FillColor'):gb[key]=H.premultiply(gb[key])
    ss=ref['scale'];M=(backing,0,0,-backing)
    glass=H.pack_glass_background_lph(H.glass_background_uniforms(gb,ss,(1024,576),M=M,headroom=1,gamma=2.2))
    kfh={k:numeric(v)for k,v in ref['kfh'].items()}
    lo,hi=H.group_blur_radii(gb);hp,hm=H.sdf_padding(H.FX_KEY_FILL,kfh)
    plan=[ref['scale'],ref['margin'],lo,hi,ref['output']['minimum'],ref['output']['maximum'],ref['smoothness'],ref['ovalization'],float(hp),float(hm),float(H.f32(2*gb['inputShadowRadius'])),H.glass_max_range(gb),ref['face_opacity'],ref['highlight_opacity'],*gb['inputShadowOffset']]
    entry=dict(name=f'{style}-{int(dark)}-{rgba}-{w}x{h}',style=style,dark=dark,w=w,h=h,backing=backing,rgba=rgba,source_scale=ss,
        glass=glass,face=matrix(ref['vcm_face']),highlight=matrix(ref['vcm_highlight']),
        key_fill=half(H.key_fill_highlight_params(kfh,opacity=ref['highlight_opacity'])),plan=plan,
        kernel=0x45 if gb['inputBleedOpacity']>0 else 0x47,
        presence=[ref['gb']is not None,ref['kfh']is not None,bool(ref['tinted_layers']),bool(ref['backdrop_properties']['tracksLuma'])])
    if rgba is not None:
     node=ref['tinted_layers'][0];effect=node['props']['effect'];props=node['props']
     mask=next(n for n in ref['sdf_layers']if n.get('props',{}).get('effect',{}).get('class')=='CASDFFillEffect')
     color_inputs=node['filters'][0]['inputs'];entry['tint']=half(H.vibrant_color_matrix_uniforms(color_inputs['inputColorMatrix']['matrix']))
     colors=[numeric(c)for c in effect['colors']]
     ramp,_=CM.create_color_map(CM.configure(colors,effect['distances'],effect['interpolationPoints'],effect['premultiplied']))
     ramp_bytes=CM.to_half_texture(ramp).tobytes()
     if ramp_reference is None:ramp_reference=ramp_bytes
     assert ramp_reference==ramp_bytes
     gradient=H.gradient_params(effect['distances'],effect_offset=props['effectOffset'])
     entry['gradient']=struct.pack('<4f',*gradient[:4])+half(gradient[4:])
     entry['fill']=half(H.fill_params({'color':numeric(mask['props']['effect']['color'])},effect_offset=mask['props']['effectOffset'],opacity=mask['opacity']))
     mp,mm=H.sdf_padding(H.FX_FILL,smoothness=mask['props']['smoothness'])
     gp,gm=H.sdf_padding(H.FX_GRADIENT,{'colors':colors,'locations':effect['distances']},smoothness=props['smoothness'])
     entry['tint_plan']=[float(mp),float(mm),float(gp),float(gm),parent_of_gradient(ref['tree'])['opacity'],props['smoothness'],mask['props']['smoothness'],props['effectOffset'],*effect['distances']]
    cases.append(entry)

text=['/* Generated only from retained macOS26.6.1 Python source. See material_fixture_provenance.json. */',
      'static const uint8_t expected_tint_ramp[2048] = '+cbytes(ramp_reference)+';',
      'static const struct material_fixture material_fixtures[32] = {']
for e in cases:
 rgba=e['rgba'] or(0,0,0,0)
 text.append('{.name='+json.dumps(e['name'])+',.input={.style='+str(e['style'])+',.dark='+str(e['dark']).lower()+',.active=true,.width_points='+number(e['w'])+',.height_points='+number(e['h'])+',.backing_scale='+number(e['backing'])+',.tint={.present='+str(e['rgba']is not None).lower()+',.srgb={'+','.join(map(str,rgba))+'}}},')
 text.append('.domain={.source_width=1024,.source_height=576,.source_scale='+number(e['source_scale'])+',.transform='+doubles((e['backing'],0,0,-e['backing']))+',.headroom=1,.gamma=2.2,.global_light=false,.light_angle=0x1.921fb54442d18p+0,.light_opacity=NAN,.light_spread=NAN,.light_height=NAN},')
 for field in('glass','face','highlight','key_fill','tint','gradient','fill'):
  if field in e:text.append('.'+field+'='+cbytes(e[field])+',')
 text.append('.plan='+doubles(e['plan'])+',.presence={'+','.join(str(v).lower()for v in e['presence'])+'},.kernel='+str(e['kernel'])+',')
 if 'tint_plan'in e:text.append('.tint_plan='+doubles(e['tint_plan'])+',')
 text.append('},')
text.append('};')
(OUT/'material_fixture_data.h').write_text('\n'.join(text)+'\n')
inputs=[SOURCE/'lg_host.py',SOURCE/'lg_colormap.py',SOURCE/'lg_renderer.py',*sorted((SOURCE/'lg_material').glob('*.py')),*sorted((SOURCE/'lg_material').glob('*.json'))]
provenance=dict(cases=len(cases),cross_product='2 public styles × 2 incoming appearances × 4 tint definitions × 2 sizes/backing scales',
    oracle='render_inputs plus lg_host/colormap source functions; no C candidate output used',
    scope='active fixed appearance (adaptive=1), source defaults, SDR, encoded RGBA bytes',
    fixture_sha256=hashlib.sha256((OUT/'material_fixture_data.h').read_bytes()).hexdigest(),
    generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    sources={str(p.relative_to(SOURCE)):hashlib.sha256(p.read_bytes()).hexdigest()for p in inputs})
(OUT/'material_fixture_provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
print('generated',len(cases),'source-only fixtures',provenance['fixture_sha256'])
