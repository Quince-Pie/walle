#!/usr/bin/env python3
"""Regenerate parity/liquid_glass_reveal_hw_constants.h from the dense
capture + side-aware primaries + oracle extensions, keyed (radius, verts)."""
import sys, json, re, pickle
sys.path[:0]=["/tmp/walle","/tmp/walle/analysis"]
S="/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad"
from pathlib import Path
import _sweep_fused_join_lattice as m
from collections import defaultdict
RADIUS={35:0x44940000,39:0x44a4f000,40:0x44a93000,41:0x44ad6000,42:0x44b1a000,
        44:0x44ba1000,45:0x44be5000,47:0x44c6c000,58:0x44f54000,60:0x44fdb000}
wch=defaultdict(list); cur=None
for line in open(S+"/walle_children.txt"):
    mm=re.match(r"state (\d+): ",line)
    if mm: cur=int(mm.group(1)); continue
    mm=re.match(r"\s+child (\d+): fixed \((-?\d+),(-?\d+)\)\((-?\d+),(-?\d+)\)\((-?\d+),(-?\d+)\) slopes",line)
    if mm and cur is not None:
        g=mm.groups()
        wch[cur].append((int(g[0]),[(int(g[1]),int(g[2])),(int(g[3]),int(g[4])),(int(g[5]),int(g[6]))]))
D=Path("/tmp/walle/build/analysis-agx-basis/residual-children-dense-plan-v1")
PLAN=json.load(open(D/"reveal-agx-setup-accumulator-plan.json"))
T=m.load_records(D/"capture.raw",len(PLAN["draws"]))
groups=defaultdict(dict); gslopes={}
for exp,draw in zip(PLAN["experiments"],PLAN["draws"]):
    r=exp["recordIndex"]
    for ctx in range(2):
        A,B,C=(int(T[r][ctx][i]) for i in range(3))
        groups[(exp["state"],exp["drawOrdinal"],ctx)][(draw["tileX"],draw["tileY"])]=C
        gslopes.setdefault((exp["state"],exp["drawOrdinal"]),[[0,0],[0,0]])[ctx]=[A,B]
assign={}
for st,o,ci,verts in pickle.load(open(S+"/child_pairs.pkl","rb")): assign[(st,ci)]=o
EXT={(39,6):6,(40,2):2,(42,2):2,(42,6):6,(44,6):6,(45,2):2,(47,2):2,(58,6):5}
for k,v in EXT.items(): assign[k]=v
# (41,c2) participates only through the extended-plane record below.
EXT_ONLY={(41,2)}
for k in EXT_ONLY: assign.setdefault(k,None)
TRUST={(39,6),(40,2),(41,2),(42,2),(42,6),(44,6),(45,2),(47,2),(58,6)}
verts_of={}
for st in {s for s,_ in assign}:
    for ci,fx in wch[st]: verts_of[(st,ci)]=fx
lines=[]
lines.append("/* Hardware-measured per-tile plane constants (AGX probe capture")
lines.append(" * residual-children-dense-plan-v1; TASK.md later-132..140).")
lines.append(" * Assignment of Apple facet planes to walle children: side-aware")
lines.append(" * geometric pairing (12 primaries) plus the per-pixel reference")
lines.append(" * oracle for facets spanning multiple walle children.  Keyed on")
lines.append(" * the state's expanded-radius word and the walle fixed-point")
lines.append(" * vertices (verts alone recur across saturated states).")
lines.append(" * 'trusted' bypasses the shader's rasterizer-owner filter for")
lines.append(" * children whose pixels the oracle proved against the reference. */")
lines.append("struct wlg_hw_tile { int16_t tx, ty; uint32_t c[2]; };")
lines.append("/* Hardware-measured internal-precision plane for one tile: the AGX")
lines.append(" * ITER evaluates sub-ulp-accurate planes; value = (a*lx + b*ly + c)")
lines.append(" * * 2^-60, exported RTZ24.  Region 1 (when e3 != 0) applies where")
lines.append(" * lx >= e0 + (e1*ly + e2)/e3 - the hardware sub-primitive split. */")
lines.append("struct wlg_hw_ext {")
lines.append("    int16_t tx, ty;")
lines.append("    uint8_t e0, e1, e2, e3;")
lines.append("    int64_t plane[2][2][3];")
lines.append("};")
lines.append("struct wlg_hw_child {")
lines.append("    uint32_t radius_bits;")
lines.append("    int32_t  fixed[3][2];")
lines.append("    uint32_t slope[2][2];")
lines.append("    uint32_t tile_count;")
lines.append("    const struct wlg_hw_tile* tiles;")
lines.append("    uint8_t  trusted;")
lines.append("    const struct wlg_hw_ext* ext;")
lines.append("};")
import pickle as _p
_ext=_p.load(open("/tmp/ext_planes.pkl","rb"))
def _pl(t,ch): return _ext[(t,ch)]
lines.append("static const struct wlg_hw_ext wlg_hw_ext_40 = {")
lines.append("    57, 12, 0, 0, 0, 0,")
lines.append("    {{{%dLL, %dLL, %dLL}," % _pl("40",0))
lines.append("      {%dLL, %dLL, %dLL}}," % _pl("40",1))
lines.append("     {{0, 0, 0}, {0, 0, 0}}},")
lines.append("};")
lines.append("static const struct wlg_hw_ext wlg_hw_ext_41 = {")
lines.append("    59, 18, 2, 3, 9, 13,")
lines.append("    {{{%dLL, %dLL, %dLL}," % _pl("41i",0))
lines.append("      {%dLL, %dLL, %dLL}}," % _pl("41i",1))
lines.append("     {{%dLL, %dLL, %dLL}," % _pl("41s",0))
lines.append("      {%dLL, %dLL, %dLL}}}," % _pl("41s",1))
lines.append("};")
tsets={}; entries=[]
EXTREF={(40,2):"&wlg_hw_ext_40",(40,3):"&wlg_hw_ext_40",
        (41,2):"&wlg_hw_ext_41",(41,3):"&wlg_hw_ext_41"}
for (st,ci),o in sorted(assign.items(), key=lambda kv:(kv[0][0],kv[0][1])):
    if o is None:
        entries.append((RADIUS[st],verts_of[(st,ci)],[[0,0],[0,0]],0,None,
                        1 if (st,ci) in TRUST else 0,EXTREF.get((st,ci),"nullptr")))
        continue
    if (st,o) not in tsets:
        tid=len(tsets)
        tiles={}
        for ctx in range(2):
            for tile,C in groups.get((st,o,ctx),{}).items():
                tiles.setdefault(tile,[0,0])[ctx]=C
        lines.append(f"static const struct wlg_hw_tile wlg_hw_tiles_{tid}[] = {{")
        for tile in sorted(tiles):
            c0,c1=tiles[tile]
            lines.append(f"    {{{tile[0]}, {tile[1]}, {{UINT32_C({c0:#010x}), UINT32_C({c1:#010x})}}}},")
        lines.append("};")
        tsets[(st,o)]=(tid,len(tiles))
    tid,n=tsets[(st,o)]
    hs=gslopes[(st,o)]
    tr=1 if (st,ci) in TRUST else 0
    entries.append((RADIUS[st],verts_of[(st,ci)],hs,n,tid,tr,EXTREF.get((st,ci),"nullptr")))
lines.append("static const struct wlg_hw_child wlg_hw_children[] = {")
for rad,fx,hs,n,ti,tr,ext in entries:
    fxs=", ".join(f"{{{a}, {b}}}" for a,b in fx)
    tiles = f"wlg_hw_tiles_{ti}" if ti is not None else "nullptr"
    lines.append(f"    {{UINT32_C({rad:#010x}), {{{fxs}}}, {{{{UINT32_C({hs[0][0]:#010x}), UINT32_C({hs[0][1]:#010x})}}, {{UINT32_C({hs[1][0]:#010x}), UINT32_C({hs[1][1]:#010x})}}}}, {n}, {tiles}, {tr}, {ext}}},")
lines.append("};")
lines.append(f"enum {{ WLG_HW_CHILD_COUNT = {len(entries)} }};")
open("/tmp/walle/parity/liquid_glass_reveal_hw_constants.h","w").write("\n".join(lines)+"\n")
print("entries:",len(entries),"trusted:",sum(e[5] for e in entries))
