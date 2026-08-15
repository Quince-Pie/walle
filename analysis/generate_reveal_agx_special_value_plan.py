#!/usr/bin/env python3
"""Per-pixel hw value capture for the two special pixels + partner lanes."""
import hashlib, json, struct, sys
from pathlib import Path
sys.path[:0] = ["/tmp/walle"]
import _sweep_fused_join_lattice as model
ROOT = Path("/tmp/walle")
OUT = ROOT / "build" / "analysis-agx-basis" / "special-value-plan-v2"
VERTEX = struct.Struct("<8I")
S = Path("/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/"
         "4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")
def _sha256(p):
    d = hashlib.sha256()
    with p.open("rb") as s:
        while b := s.read(1<<22): d.update(b)
    return d.hexdigest()
children = {}
for line in (S/"childgeo_all_residual_states.txt").read_text().splitlines():
    if "CHILDSDF" not in line: continue
    t = line[line.index("CHILDSDF"):].split()
    children.setdefault(int(t[1]), {})[int(t[2])] = [
        [int(x,16) for x in t[3+4*v:7+4*v]] for v in range(3)]
def fixed(w): return int(round(model.bits_f32(w)*256.0))
def contains(verts, x, y):
    fx=[(fixed(v[0]),fixed(v[1])) for v in verts]
    det=((fx[1][0]-fx[0][0])*(fx[2][1]-fx[0][1])
         -(fx[1][1]-fx[0][1])*(fx[2][0]-fx[0][0]))
    if det==0: return False
    ex_=1 if det>0 else -1
    cx,cy=256*x+128,256*y+128
    for e in range(3):
        nx=(e+1)%3
        ex2=fx[nx][0]-fx[e][0]; ey=fx[nx][1]-fx[e][1]
        cr=ex2*(cy-fx[e][1])-ey*(cx-fx[e][0])
        if cr==0:
            ox=-ex2 if ex_<0 else ex2; oy=-ey if ex_<0 else ey
            if not ((oy==0 and ox<0) or oy>0): return False
            continue
        if (1 if cr>0 else -1)!=ex_: return False
    return True
PIXELS = [(40,1847,402),(40,1846,402),(40,1847,403),
          (41,1897,606),(41,1896,606),(41,1897,607)]
vertices = bytearray(); draws=[]; experiments=[]
for st,x,y in PIXELS:
    for o,verts in sorted(children[st].items()):
        if not contains(verts,x,y): continue
        record=len(draws)
        for v in verts:
            vertices.extend(VERTEX.pack(v[0],v[1],0,0,v[2],v[3],v[2],v[3]))
        experiments.append({"recordIndex":record,"variant":"residual-value",
            "state":st,"x":x,"y":y,"walleByte":0,"appleByte":0,"drawOrdinal":o})
        draws.append({"recordIndex":record,"targetIndex":0,"targetRecordIndex":0,
            "sampleRecordIndex":0,"sampleOrdinal":0,"patternIndex":record,
            "x":x,"y":y,"tileX":x//32,"tileY":y//32})
        print(f"s{st} ({x},{y}) o{o}",file=sys.stderr)
OUT.mkdir(parents=True, exist_ok=True)
vp=OUT/"reveal-agx-setup-accumulator-vertices.bin"
vp.write_bytes(vertices)
plan={"schema":"walle-reveal-agx-setup-accumulator-plan-v1",
 "authority":{"opensReferencePixels":False,"usesOutputFeedback":False,
  "establishesDegenerateChildSetupLaw":False,"establishesPerPixelEvaluationLaw":True},
 "target":{"width":2048,"height":2048},
 "vertexData":{"file":vp.name,"bytes":len(vertices),"sha256":_sha256(vp),
  "recordCount":len(draws),"verticesPerRecord":3,"wordsPerVertex":8,
  "layout":"positionXY,pad2,varyingRGBA; little-endian uint32"},
 "experiments":experiments,"draws":draws,
 "census":{"targetCount":8,"patternCount":len(draws),"drawCount":len(draws),
  "coefficientTripleCount":len(draws)*4}}
pp=OUT/"reveal-agx-setup-accumulator-plan.json"
pp.write_text(json.dumps(plan,indent=2,sort_keys=True)+"\n")
print(len(draws),"draws ->",OUT)
