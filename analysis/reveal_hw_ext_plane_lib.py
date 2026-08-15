import sys, json, struct
sys.path[:0]=["/tmp/walle","/tmp/walle/analysis"]
from pathlib import Path
import numpy as np
from fractions import Fraction
import _sweep_fused_join_lattice as m
def wval_f(w):
    s=-1 if w>>31 else 1
    e=(w>>23)&0xff; mant=w&0x7fffff
    if e==0: return Fraction(s*mant)*Fraction(2)**(-149)
    return Fraction(s*(mant|0x800000))*Fraction(2)**(e-150)
def ulp_f(w): return Fraction(2)**(((w>>23)&0xff)-150)
def f32_rne_fr(v):
    if v==0: return 0
    neg=v<0; a=abs(v)
    num,den=a.numerator,a.denominator
    e=num.bit_length()-den.bit_length()
    sh=25-e
    if sh>=0: num<<=sh
    else: den<<=-sh
    q,rem=divmod(num,den)
    if q.bit_length()>25: rem|=q&1; q>>=1; e+=1
    g=q&1; q>>=1
    if g and (rem or (q&1)): q+=1
    if q.bit_length()>24: q>>=1; e+=1
    return ((e-24+127+23)<<23 | (q&0x7fffff)) | (0x80000000 if neg else 0)
def f32_rtz_fr(v):
    if v==0: return 0
    neg=v<0; a=abs(v)
    num,den=a.numerator,a.denominator
    e=num.bit_length()-den.bit_length()
    sh=24-e
    if sh>=0: num<<=sh
    else: den<<=-sh
    q=num//den
    if q.bit_length()>24: q>>=1; e+=1
    if q.bit_length()<24: q=(num*2)//den; e-=1
    if q.bit_length()>24: q>>=1; e+=1
    return ((e-24+127+23)<<23 | (q&0x7fffff)) | (0x80000000 if neg else 0)
def add_f32(w1,w2,delta): return f32_rne_fr(wval_f(w1)+wval_f(w2)*delta)
def bump(w,k): return (w & 0x80000000) | (((w & 0x7fffffff) + k) & 0x7fffffff)
def decompose(bits):
    s=-1 if bits>>31 else 1
    e=(bits>>23)&0xff
    if e==0: return (bits&0x7fffff),-149,s
    return (bits&0x7fffff)|0x800000,e-150,s
DV=Path("/tmp/walle/build/analysis-agx-basis/tile-value-plan-v1")
plan=json.loads((DV/"reveal-agx-setup-accumulator-plan.json").read_text())
raw=(DV/"capture.raw").read_bytes()
RW=36
D = Path("/tmp/walle/build/analysis-agx-basis/residual-children-dense-plan-v1")
PLAN = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))
tiledata={}
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    key=(exp["state"],exp["drawOrdinal"],draw["tileX"],draw["tileY"])
    r=exp["recordIndex"]
    tiledata[key]=([int(T[r][ctx][2]) for ctx in range(2)],
                   [[int(T[r][ctx][0]),int(T[r][ctx][1])] for ctx in range(2)])
def affine_rtz(cw,aw,bw,lx,ly):
    mc,ec,sc=decompose(cw) if cw else (0,0,1)
    mx,ex,sx=decompose(aw) if aw else (0,0,1)
    my,ey,sy=decompose(bw) if bw else (0,0,1)
    px=mx*(2*lx+1); ex-=1
    py=my*(2*ly+1); ey-=1
    cand=[e for e,c in ((ec,mc),(ex,px),(ey,py)) if c]
    if not cand: return (cw>>31)<<31
    grid=max(cand)-64
    tot=0
    if mc: tot+=sc*(mc<<(ec-grid))
    if px: tot+=sx*(px<<(ex-grid))
    if py: tot+=sy*(py<<(ey-grid))
    if tot==0: return 0
    sb=1 if tot<0 else 0
    mag=abs(tot); low=mag.bit_length()-24
    mant=(mag>>low) if low>0 else (mag<<-low)
    return (sb<<31)|((grid+low+150)<<23)|(mant&0x7fffff)
def collect_full(st,tx,ty):
    cw,sl=tiledata[(st,2,tx,ty)]
    interior={}; sliver={}
    for exp in plan["experiments"]:
        if exp["state"]!=st: continue
        x,y=exp["x"],exp["y"]
        words=struct.unpack_from(f"<{RW}I",raw,exp["recordIndex"]*RW*4)
        c=(words[16],words[17])
        pxw=words[20:22] if x%2==0 else words[24:26]
        pyw=words[28:30] if y%2==0 else words[32:34]
        allw=struct.unpack_from(f"<{RW}I",raw,exp["recordIndex"]*RW*4)
        m0=affine_rtz(cw[0],sl[0][0],sl[0][1],x&31,y&31)
        m1=affine_rtz(cw[1],sl[1][0],sl[1][1],x&31,y&31)
        if abs(((c[0]>>23)&0xff)-((m0>>23)&0xff))>1 or abs(((c[1]>>23)&0xff)-((m1>>23)&0xff))>1:
            continue
        d=(interior if (c[0]==m0 and c[1]==m1) else sliver)
        d[(x,y)]=(c,tuple(pxw),tuple(pyw),allw)
    return interior,sliver
