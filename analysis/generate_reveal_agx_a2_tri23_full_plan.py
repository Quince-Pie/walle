#!/usr/bin/env python3
"""Full-tile A2 transfer-plane capture for tris 2/3 of states 40/41/42."""
import hashlib, json, struct, sys
from pathlib import Path
sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]
import _sweep_fused_join_lattice as m

ROOT = Path("/tmp/walle")
OUT = ROOT / "build" / "analysis-agx-basis" / "a2-tri23-full-plan-v1"
TRACE = Path("/tmp/walle-analysis/A2-geometry-sweep-v74")
VERTEX = struct.Struct("<8I")
ONE = 0x3F800000

def _sha256(path):
    d = hashlib.sha256()
    with path.open("rb") as s:
        while b := s.read(1<<22): d.update(b)
    return d.hexdigest()

def load_mesh(state):
    tr = json.load(open(TRACE / f"state-{state}" /
                        "reveal-mask-trace.json"))["nativeScale"]["A2Geometry"]
    vs = bytes.fromhex(tr["vertexStreamHex"])
    verts = [struct.unpack_from("<II", vs, i * 48) for i in range(tr["vertexCount"])]
    idx = tr["indices"]
    return verts, [tuple(idx[i:i+3]) for i in range(0, len(idx), 3)]

def inside(px, py, pos):
    xs = [m.bits_f32(p[0]) for p in pos]; ys = [m.bits_f32(p[1]) for p in pos]
    det = (xs[1]-xs[0])*(ys[2]-ys[0]) - (ys[1]-ys[0])*(xs[2]-xs[0])
    if det == 0: return False
    sgn = 1 if det > 0 else -1
    for e in range(3):
        ax, ay = xs[e], ys[e]; bx, by = xs[(e+1)%3], ys[(e+1)%3]
        if sgn*((bx-ax)*(py+0.5-ay) - (by-ay)*(px+0.5-ax)) < 4.0: return False
    return True

def all_tiles(pos):
    xs = [m.bits_f32(p[0]) for p in pos]; ys = [m.bits_f32(p[1]) for p in pos]
    out = []
    for ty in range(max(0,int(min(ys)//32)), min(63,int(max(ys)//32))+1):
        for tx in range(max(0,int(min(xs)//32)), min(63,int(max(xs)//32))+1):
            for ox, oy in ((15,15),(5,5),(25,25),(5,25),(25,5)):
                px, py = tx*32+ox, ty*32+oy
                if inside(px, py, pos):
                    out.append((tx,ty,px,py)); break
    return out

vertices = bytearray(); draws = []; experiments = []
for state in (40, 41, 42):
    verts, tris = load_mesh(state)
    for tindex in (2, 3):
        pos = [verts[v] for v in tris[tindex]]
        if len(set(pos)) < 3: continue
        for tx, ty, px, py in all_tiles(pos):
            record = len(draws)
            for (vx, vy) in pos:
                vertices.extend(VERTEX.pack(vx, vy, 0, 0, ONE, ONE, ONE, ONE))
            experiments.append({"recordIndex":record,"inputOrdinal":record,
                "variant":"a2-tri23-full","split":"discovery","state":state,
                "drawOrdinal":tindex,"anchor":0,"family":"one","offset":tindex})
            draws.append({"recordIndex":record,"targetIndex":0,
                "targetRecordIndex":0,"sampleRecordIndex":0,"sampleOrdinal":0,
                "patternIndex":record,"x":px,"y":py,"tileX":tx,"tileY":ty})

OUT.mkdir(parents=True, exist_ok=True)
vp = OUT/"reveal-agx-setup-accumulator-vertices.bin"
vp.write_bytes(vertices)
plan = {"schema":"walle-reveal-agx-setup-accumulator-plan-v1",
    "authority":{"opensReferencePixels":False,"usesOutputFeedback":False,
                 "establishesA2AllTrianglePlanes":True},
    "target":{"width":2048,"height":2048},
    "vertexData":{"file":vp.name,"bytes":len(vertices),"sha256":_sha256(vp),
        "recordCount":len(draws),"verticesPerRecord":3,"wordsPerVertex":8,
        "layout":"positionXY,pad2,varyingRGBA; little-endian uint32"},
    "experiments":experiments,"draws":draws,
    "census":{"targetCount":8,"patternCount":len(draws),"drawCount":len(draws),
              "coefficientTripleCount":len(draws)*4}}
pp = OUT/"reveal-agx-setup-accumulator-plan.json"
pp.write_text(json.dumps(plan, indent=2, sort_keys=True)+"\n")
print(len(draws), "draws ->", OUT)
