#!/usr/bin/env python3
"""Full-tile per-pixel value capture: s40 o2 tile (57,12), s41 o2 tile (59,18)."""
import hashlib, json, struct, sys
from pathlib import Path
sys.path[:0] = ["/tmp/walle"]
ROOT = Path("/tmp/walle")
OUT = ROOT / "build" / "analysis-agx-basis" / "tile-value-plan-v1"
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
JOBS = [(40,2,57,12),(41,2,59,18)]
vertices = bytearray(); draws=[]; experiments=[]
for st,o,tx,ty in JOBS:
    verts = children[st][o]
    for ly in range(32):
        for lx in range(32):
            x,y = tx*32+lx, ty*32+ly
            record=len(draws)
            for v in verts:
                vertices.extend(VERTEX.pack(v[0],v[1],0,0,v[2],v[3],v[2],v[3]))
            experiments.append({"recordIndex":record,"variant":"residual-value",
                "state":st,"x":x,"y":y,"walleByte":0,"appleByte":0,"drawOrdinal":o})
            draws.append({"recordIndex":record,"targetIndex":0,"targetRecordIndex":0,
                "sampleRecordIndex":0,"sampleOrdinal":0,"patternIndex":record,
                "x":x,"y":y,"tileX":tx,"tileY":ty})
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
print(len(draws),"draws")
