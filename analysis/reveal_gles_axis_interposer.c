#define _POSIX_C_SOURCE 200809L

#include <GLES3/gl3.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/*
 * This is an analysis-only bridge.  Including the implementation gives this
 * translation unit access to the admitted static AGX coordinate generator
 * without adding a production API solely for an experiment.
 */
#include "../parity/liquid_glass_raster.c"

typedef void(GL_APIENTRYP shader_source_fn)(GLuint, GLsizei, const GLchar* const*, const GLint*);
typedef void(GL_APIENTRYP use_program_fn)(GLuint);
typedef void(GL_APIENTRYP buffer_sub_data_fn)(GLenum, GLintptr, GLsizeiptr, const void*);
typedef void(GL_APIENTRYP draw_elements_fn)(GLenum, GLsizei, GLenum, const void*);

static shader_source_fn   real_shader_source;
static use_program_fn     real_use_program;
static buffer_sub_data_fn real_buffer_sub_data;
static draw_elements_fn   real_draw_elements;

static GLuint  current_program;
static GLuint  axis_texture;
static GLuint  apple_sqrt_texture;
static uint8_t vertex_bytes[16 * 48];
static size_t  vertex_count;
static uint16_t captured_indices[54];
static size_t   captured_index_count;
static uint8_t* p25_bitmap;

static uint64_t generated_quad_count;
static uint64_t generated_word_count;
static uint64_t uploaded_byte_count;
static uint64_t maximum_texture_bytes;
static uint64_t generation_nanoseconds;
static uint64_t submission_nanoseconds;
static const char* generation_failure;

static const char native_axis_fragment_source[] =
    "#version 300 es\n"
    "precision highp float;\n"
    "precision highp int;\n"
    "in vec2 v_SDF;\n"
    "uniform vec2 RevealResolution;\n"
    "uniform highp usampler2D AxisTable;\n"
    "uniform int AxisStart;\n"
    "uniform int AxisPrimitive;\n"
    "layout(location=0) out float RevealCoverage;\n"
    "uint rr(uint v,uint s){if(s==0u)return v;uint t=v>>s,m=(1u<<s)-1u,r=v&m,h=1u<<(s-1u);"
    "if(r>h||(r==h&&(t&1u)!=0u))++t;return t;}"
    "uint hb(float v){uint b=floatBitsToUint(v),sg=(b>>16u)&0x8000u,e=(b>>23u)&255u,m=b&0x7fffffu;"
    "if(e==255u)return sg|(m==0u?0x7c00u:0x7e00u);if(e==0u)return sg;int u=int(e)-127,he=u+15;"
    "if(he>=31)return sg|0x7c00u;if(he<=0){if(u<-25)return sg;uint q=rr(m|0x800000u,uint(-u-1));"
    "return sg|min(q,0x400u);}uint q=rr(m,13u);if(q==0x400u){q=0u;++he;if(he>=31)return sg|0x7c00u;}"
    "return sg|(uint(he)<<10u)|q;}"
    "vec2 agx(){int x=int(gl_FragCoord.x),y=int(RevealResolution.y)-1-int(gl_FragCoord.y);"
    "uvec4 ax=texelFetch(AxisTable,ivec2(x-AxisStart,AxisPrimitive),0);"
    "uvec4 ay=texelFetch(AxisTable,ivec2(y-AxisStart,AxisPrimitive),0);"
    "return vec2(uintBitsToFloat(ax.x),uintBitsToFloat(ay.y));}"
    "void main(){vec2 p=agx();float d=length(p);if(v_SDF.x>1e30)d=v_SDF.x;"
    "float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);float a=clamp((1.0-d)/f+0.5,0.0,1.0);"
    "float h=(a==0.0||a==1.0)?a:unpackHalf2x16(hb(a)).x;RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char owner_axis_fragment_source[] =
    "#version 320 es\n"
    "precision highp float;\n"
    "precision highp int;\n"
    "in vec2 v_SDF;\n"
    "uniform vec2 RevealResolution;\n"
    "uniform highp usampler2D AxisTable;\n"
    "uniform int AxisStart;\n"
    "uniform int AxisPrimitive;\n"
    "uniform ivec2 AxisOriginFixed;\n"
    "uniform ivec2 AxisExtentFixed;\n"
    "uniform int AxisAscendingDiagonal;\n"
    "layout(location=0) out float RevealCoverage;\n"
    "uint rr(uint v,uint s){if(s==0u)return v;uint t=v>>s,m=(1u<<s)-1u,r=v&m,h=1u<<(s-1u);"
    "if(r>h||(r==h&&(t&1u)!=0u))++t;return t;}"
    "uint hb(float v){uint b=floatBitsToUint(v),sg=(b>>16u)&0x8000u,e=(b>>23u)&255u,m=b&0x7fffffu;"
    "if(e==255u)return sg|(m==0u?0x7c00u:0x7e00u);if(e==0u)return sg;int u=int(e)-127,he=u+15;"
    "if(he>=31)return sg|0x7c00u;if(he<=0){if(u<-25)return sg;uint q=rr(m|0x800000u,uint(-u-1));"
    "return sg|min(q,0x400u);}uint q=rr(m,13u);if(q==0x400u){q=0u;++he;if(he>=31)return sg|0x7c00u;}"
    "return sg|(uint(he)<<10u)|q;}"
    "uvec2 m64(int a,int b){int hi,lo;imulExtended(a,b,hi,lo);return uvec2(uint(lo),uint(hi));}"
    "uvec2 a64(uvec2 a,uvec2 b){uint lo=a.x+b.x;return uvec2(lo,a.y+b.y+uint(lo<a.x));}"
    "bool l64(uvec2 a,uvec2 b){int ah=int(a.y),bh=int(b.y);return ah!=bh?ah<bh:a.x<b.x;}"
    "int owner(ivec2 c){ivec2 r=c*256+ivec2(128)-AxisOriginFixed;"
    "uvec2 x=m64(r.x,AxisExtentFixed.y),y=m64(r.y,AxisExtentFixed.x);"
    "if(AxisAscendingDiagonal!=0)return l64(x,y)?1:0;"
    "return l64(a64(x,y),m64(AxisExtentFixed.x,AxisExtentFixed.y))?1:0;}"
    "vec2 agx(ivec2 c){int p=owner(c);uvec4 ax=texelFetch(AxisTable,ivec2(c.x-AxisStart,p),0);"
    "uvec4 ay=texelFetch(AxisTable,ivec2(c.y-AxisStart,p),0);"
    "return vec2(uintBitsToFloat(ax.x),uintBitsToFloat(ay.y));}"
    "void main(){ivec2 c=ivec2(int(gl_FragCoord.x),int(RevealResolution.y)-1-int(gl_FragCoord.y));"
    "float d=length(agx(c)),dx=length(agx(ivec2(c.x^1,c.y))),dy=length(agx(ivec2(c.x,c.y^1)));"
    "if(v_SDF.x>1e30||AxisPrimitive>2)d=v_SDF.x;float f=max(abs(dx-d)+abs(dy-d),1e-4);"
    "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=(a==0.0||a==1.0)?a:unpackHalf2x16(hb(a)).x;"
    "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

#define GLSL_EXACT_SQRT                                                                            \
    "void ff(uint b,out uint s,out int e){uint x=(b>>23u)&255u;if(x==0u){s=b&0x7fffffu;e=-149;}"   \
    "else{s=(b&0x7fffffu)|0x800000u;e=int(x)-150;}}"                                               \
    "void mf(uint l,uint r,out uint s,out int e){uint a,c;int b,d;ff(l,a,b);ff(r,c,d);e=min(b,d);" \
    "s=(a<<uint(b-e))+(c<<uint(d-e));e-=1;}"                                                       \
    "uvec3 sh(uint v,int n){if(n<0||n>=96)return uvec3(0);if(n==0)return uvec3(v,0,0);"            \
    "if(n<32)return uvec3(v<<uint(n),v>>uint(32-n),0);if(n==32)return uvec3(0,v,0);"               \
    "if(n<64)return uvec3(0,v<<uint(n-32),v>>uint(64-n));return uvec3(0,0,v<<uint(n-64));}"        \
    "int sc(uint vb,uint ms,int me){uint vs;int ve;ff(vb,vs,ve);uint hi,lo;umulExtended(ms,ms,hi," \
    "lo);int n=ve-2*me;if(n<0)return 1;if(n>=96)return -1;uvec3 a=uvec3(lo,hi,0),b=sh(vs,n);"       \
    "if(a.z!=b.z)return a.z<b.z?-1:1;if(a.y!=b.y)return a.y<b.y?-1:1;"                             \
    "if(a.x!=b.x)return a.x<b.x?-1:1;return 0;}"                                                   \
    "float ir(float v){float r=sqrt(v);if(!(v>0.0)||isinf(v))return r;uint vb=floatBitsToUint(v)," \
    "rb=floatBitsToUint(r);for(int i=0;i<4;++i){uint s;int e;mf(rb-1u,rb,s,e);"                    \
    "if(sc(vb,s,e)>0){--rb;continue;}mf(rb,rb+1u,s,e);if(sc(vb,s,e)<0){++rb;continue;}break;}"     \
    "return uintBitsToFloat(rb);}"

static const char owner_apple_sqrt_fragment_source[] =
    "#version 320 es\n"
    "precision highp float;\n"
    "precision highp int;\n"
    "in vec2 v_SDF;\n"
    "uniform vec2 RevealResolution;\n"
    "uniform highp usampler2D AxisTable;\n"
    "uniform highp usampler2D AppleFastSqrtTable;\n"
    "uniform int AxisStart;\n"
    "uniform int AxisPrimitive;\n"
    "uniform ivec2 AxisOriginFixed;\n"
    "uniform ivec2 AxisExtentFixed;\n"
    "uniform int AxisAscendingDiagonal;\n"
    "layout(location=0) out float RevealCoverage;\n"
    "uint rr(uint v,uint s){if(s==0u)return v;uint t=v>>s,m=(1u<<s)-1u,r=v&m,h=1u<<(s-1u);"
    "if(r>h||(r==h&&(t&1u)!=0u))++t;return t;}"
    "uint hb(float v){uint b=floatBitsToUint(v),sg=(b>>16u)&0x8000u,e=(b>>23u)&255u,m=b&0x7fffffu;"
    "if(e==255u)return sg|(m==0u?0x7c00u:0x7e00u);if(e==0u)return sg;int u=int(e)-127,he=u+15;"
    "if(he>=31)return sg|0x7c00u;if(he<=0){if(u<-25)return sg;uint q=rr(m|0x800000u,uint(-u-1));"
    "return sg|min(q,0x400u);}uint q=rr(m,13u);if(q==0x400u){q=0u;++he;if(he>=31)return sg|0x7c00u;}"
    "return sg|(uint(he)<<10u)|q;}"
    GLSL_EXACT_SQRT
    "uvec2 m64(int a,int b){int hi,lo;imulExtended(a,b,hi,lo);return uvec2(uint(lo),uint(hi));}"
    "uvec2 a64(uvec2 a,uvec2 b){uint lo=a.x+b.x;return uvec2(lo,a.y+b.y+uint(lo<a.x));}"
    "bool l64(uvec2 a,uvec2 b){int ah=int(a.y),bh=int(b.y);return ah!=bh?ah<bh:a.x<b.x;}"
    "int owner(ivec2 c){ivec2 r=c*256+ivec2(128)-AxisOriginFixed;"
    "uvec2 x=m64(r.x,AxisExtentFixed.y),y=m64(r.y,AxisExtentFixed.x);"
    "if(AxisAscendingDiagonal!=0)return l64(x,y)?1:0;"
    "return l64(a64(x,y),m64(AxisExtentFixed.x,AxisExtentFixed.y))?1:0;}"
    "vec2 agx(ivec2 c){int p=owner(c);uvec4 ax=texelFetch(AxisTable,ivec2(c.x-AxisStart,p),0);"
    "uvec4 ay=texelFetch(AxisTable,ivec2(c.y-AxisStart,p),0);"
    "return vec2(uintBitsToFloat(ax.x),uintBitsToFloat(ay.y));}"
    "float ad(vec2 p){float s=fma(p.y,p.y,p.x*p.x),r=ir(s);uint b=floatBitsToUint(s),m=b&0x7fffffu;"
    "uint c=texelFetch(AppleFastSqrtTable,ivec2(int(m&4095u),int(m>>12u)),0).r;"
    "uint q=((b>>23u)&1u)==0u?c&3u:(c>>2u)&3u;return uintBitsToFloat(uint(int(floatBitsToUint(r))"
    "+int(q)-1));}"
    "void main(){ivec2 c=ivec2(int(gl_FragCoord.x),int(RevealResolution.y)-1-int(gl_FragCoord.y));"
    "float d=ad(agx(c)),dx=ad(agx(ivec2(c.x^1,c.y))),dy=ad(agx(ivec2(c.x,c.y^1)));"
    "if(v_SDF.x>1e30||AxisPrimitive>2)d=v_SDF.x;float f=max(abs(dx-d)+abs(dy-d),1e-4);"
    "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=(a==0.0||a==1.0)?a:unpackHalf2x16(hb(a)).x;"
    "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

#define GLOBAL_OWNER_UNIFORMS                                                                      \
    "uniform int AxisStarts[4];uniform int AxisSlot;uniform int AxisPrimitive;"                    \
    "uniform int OwnerSlotCount;uniform ivec2 OwnerOriginFixed[4];"                                \
    "uniform ivec2 OwnerExtentFixed[4];uniform ivec4 OwnerBounds[4];"                              \
    "uniform int OwnerAscending[4];uniform int OwnerActiveMask[4];"

#define GLOBAL_OWNER_FUNCTIONS                                                                     \
    "uvec2 m64(int a,int b){int hi,lo;imulExtended(a,b,hi,lo);return uvec2(uint(lo),uint(hi));}"   \
    "uvec2 a64(uvec2 a,uvec2 b){uint lo=a.x+b.x;return uvec2(lo,a.y+b.y+uint(lo<a.x));}"           \
    "bool l64(uvec2 a,uvec2 b){int ah=int(a.y),bh=int(b.y);return ah!=bh?ah<bh:a.x<b.x;}"          \
    "int op(int s,ivec2 c){ivec2 r=c*256+ivec2(128)-OwnerOriginFixed[s];"                          \
    "uvec2 x=m64(r.x,OwnerExtentFixed[s].y),y=m64(r.y,OwnerExtentFixed[s].x);"                     \
    "if(OwnerAscending[s]!=0)return l64(x,y)?1:0;return "                                          \
    "l64(a64(x,y),m64(OwnerExtentFixed[s].x,OwnerExtentFixed[s].y))?1:0;}"                         \
    "int oc(ivec2 c){int code=0;for(int s=0;s<4;++s){if(s>=OwnerSlotCount)break;ivec4 b="          \
    "OwnerBounds[s];if(c.x<b.x||c.y<b.y||c.x>=b.z||c.y>=b.w)continue;int p=op(s,c);"               \
    "if((OwnerActiveMask[s]&(1<<p))!=0)code=s*2+p+1;}return code;}"                                \
    "vec2 agx(ivec2 c){int code=oc(c),s=AxisSlot,p=AxisPrimitive;if(code>0){--code;s=code/2;"      \
    "p=code&1;}int st=AxisStarts[s];uvec4 ax=texelFetch(AxisTable,ivec2(c.x-st,s*2+p),0);"         \
    "uvec4 ay=texelFetch(AxisTable,ivec2(c.y-st,s*2+p),0);"                                       \
    "return vec2(uintBitsToFloat(ax.x),uintBitsToFloat(ay.y));}"

#define GLOBAL_HALF_FUNCTIONS                                                                      \
    "uint rr(uint v,uint s){if(s==0u)return v;uint t=v>>s,m=(1u<<s)-1u,r=v&m,h=1u<<(s-1u);"       \
    "if(r>h||(r==h&&(t&1u)!=0u))++t;return t;}"                                                   \
    "uint hb(float v){uint b=floatBitsToUint(v),sg=(b>>16u)&0x8000u,e=(b>>23u)&255u,"             \
    "m=b&0x7fffffu;if(e==255u)return sg|(m==0u?0x7c00u:0x7e00u);if(e==0u)return sg;"              \
    "int u=int(e)-127,he=u+15;if(he>=31)return sg|0x7c00u;if(he<=0){if(u<-25)return sg;"          \
    "uint q=rr(m|0x800000u,uint(-u-1));return sg|min(q,0x400u);}uint q=rr(m,13u);"                 \
    "if(q==0x400u){q=0u;++he;if(he>=31)return sg|0x7c00u;}return sg|(uint(he)<<10u)|q;}"

static const char global_owner_axis_fragment_source[] =
    "#version 320 es\n"
    "precision highp float;precision highp int;in vec2 v_SDF;uniform vec2 RevealResolution;"
    "uniform highp usampler2D AxisTable;"
    GLOBAL_OWNER_UNIFORMS
    "layout(location=0) out float RevealCoverage;"
    GLOBAL_HALF_FUNCTIONS GLOBAL_OWNER_FUNCTIONS
    "void main(){ivec2 c=ivec2(int(gl_FragCoord.x),int(RevealResolution.y)-1-int(gl_FragCoord.y));"
    "float d=length(agx(c)),dx=length(agx(ivec2(c.x^1,c.y))),dy=length(agx(ivec2(c.x,c.y^1)));"
    "if(v_SDF.x>1e30)d=v_SDF.x;float f=max(abs(dx-d)+abs(dy-d),1e-4);"
    "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=(a==0.0||a==1.0)?a:unpackHalf2x16(hb(a)).x;"
    "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char global_owner_apple_sqrt_fragment_source[] =
    "#version 320 es\n"
    "precision highp float;precision highp int;in vec2 v_SDF;uniform vec2 RevealResolution;"
    "uniform highp usampler2D AxisTable;uniform highp usampler2D AppleFastSqrtTable;"
    "uniform int ExactDivisionMode;"
    GLOBAL_OWNER_UNIFORMS
    "layout(location=0) out float RevealCoverage;"
    GLOBAL_HALF_FUNCTIONS GLSL_EXACT_SQRT GLOBAL_OWNER_FUNCTIONS
    "float ad(vec2 p){float s=fma(p.y,p.y,p.x*p.x),r=ir(s);uint b=floatBitsToUint(s),m=b&0x7fffffu;"
    "uint c=texelFetch(AppleFastSqrtTable,ivec2(int(m&4095u),int(m>>12u)),0).r;"
    "uint q=((b>>23u)&1u)==0u?c&3u:(c>>2u)&3u;return uintBitsToFloat(uint(int(floatBitsToUint(r))"
    "+int(q)-1));}"
    "uint qb(uvec2 n,int i){return i>=32?((n.y>>uint(i-32))&1u):((n.x>>uint(i))&1u);}"
    "uint qd(uint s,uint sh,uint d,out uint rem){uvec2 n=uvec2(s<<sh,s>>uint(32u-sh));"
    "uint q=0u;rem=0u;for(int i=47;i>=0;--i){rem=(rem<<1u)|qb(n,i);if(rem>=d){rem-=d;"
    "if(i<32)q|=1u<<uint(i);}}return q;}"
    "float ed(float a,float b){if(a==0.0)return a;uint ab=floatBitsToUint(a),bb=floatBitsToUint(b),"
    "ae=(ab>>23u)&255u,be=(bb>>23u)&255u;if(ae==0u||be==0u||ae==255u||be==255u)return a/b;"
    "uint as=(ab&0x7fffffu)|0x800000u,bs=(bb&0x7fffffu)|0x800000u;bool ge=as>=bs;uint rem;"
    "uint q=qd(as,ge?23u:24u,bs,rem);uint twice=rem<<1u;if(twice>bs||(twice==bs&&(q&1u)!=0u))++q;"
    "int e=int(ae)-int(be)-(ge?0:1)+127;if(q==0x1000000u){q>>=1u;++e;}if(e<=0||e>=255)return a/b;"
    "return uintBitsToFloat(((ab^bb)&0x80000000u)|(uint(e)<<23u)|(q&0x7fffffu));}"
    "void main(){ivec2 c=ivec2(int(gl_FragCoord.x),int(RevealResolution.y)-1-int(gl_FragCoord.y));"
    "float d=ad(agx(c)),dx=ad(agx(ivec2(c.x^1,c.y))),dy=ad(agx(ivec2(c.x,c.y^1)));"
    "if(v_SDF.x>1e30)d=v_SDF.x;float f=max(abs(dx-d)+abs(dy-d),1e-4);"
    "float n=1.0-d,a;if(n<=-0.5*f)a=0.0;else if(n>=0.5*f)a=1.0;else "
    "a=(ExactDivisionMode!=0?ed(n,f):n/f)+0.5;"
    "float h=(a==0.0||a==1.0)?a:unpackHalf2x16(hb(a)).x;"
    "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static void axis_fail(const char* message)
{
    fprintf(stderr, "reveal axis interposer: %s\n", message);
    exit(EXIT_FAILURE);
}

static uint64_t elapsed_nanoseconds(const struct timespec* start, const struct timespec* end)
{
    int64_t seconds = (int64_t)end->tv_sec - (int64_t)start->tv_sec;
    int64_t nanoseconds = seconds * INT64_C(1'000'000'000) + (int64_t)end->tv_nsec
                          - (int64_t)start->tv_nsec;
    return nanoseconds < 0 ? 0 : (uint64_t)nanoseconds;
}

static void load_real_symbol(void* destination, size_t size, const char* name)
{
    void* symbol = dlsym(RTLD_NEXT, name);
    if (symbol == nullptr || size != sizeof symbol)
        axis_fail("cannot resolve a GLES entry point");
    memcpy(destination, &symbol, size);
}

static char* join_shader_source(GLsizei count, const GLchar* const* strings, const GLint* lengths)
{
    size_t total = 0;
    for (GLsizei index = 0; index < count; ++index) {
        size_t length = lengths != nullptr && lengths[index] >= 0 ? (size_t)lengths[index]
                                                                  : strlen(strings[index]);
        if (length > SIZE_MAX - total - 1)
            return nullptr;
        total += length;
    }
    char* result = malloc(total + 1);
    if (result == nullptr)
        return nullptr;
    size_t offset = 0;
    for (GLsizei index = 0; index < count; ++index) {
        size_t length = lengths != nullptr && lengths[index] >= 0 ? (size_t)lengths[index]
                                                                  : strlen(strings[index]);
        memcpy(result + offset, strings[index], length);
        offset += length;
    }
    result[offset] = '\0';
    return result;
}

static bool axis_enabled(void)
{
    const char* mode = getenv("WALLE_REVEAL_AXIS_ABLATION");
    return mode != nullptr
           && (strcmp(mode, "native") == 0 || strcmp(mode, "owner-xor") == 0
               || strcmp(mode, "owner-xor-apple-sqrt") == 0
               || strcmp(mode, "owner-xor-apple-sqrt-exact-div") == 0
               || strcmp(mode, "owner-xor-local") == 0
               || strcmp(mode, "owner-xor-apple-sqrt-local") == 0);
}

static bool owner_xor_enabled(void)
{
    const char* mode = getenv("WALLE_REVEAL_AXIS_ABLATION");
    return mode != nullptr
           && (strcmp(mode, "owner-xor") == 0
               || strcmp(mode, "owner-xor-apple-sqrt") == 0
               || strcmp(mode, "owner-xor-apple-sqrt-exact-div") == 0
               || strcmp(mode, "owner-xor-local") == 0
               || strcmp(mode, "owner-xor-apple-sqrt-local") == 0);
}

static bool owner_apple_sqrt_enabled(void)
{
    const char* mode = getenv("WALLE_REVEAL_AXIS_ABLATION");
    return mode != nullptr
           && (strcmp(mode, "owner-xor-apple-sqrt") == 0
               || strcmp(mode, "owner-xor-apple-sqrt-exact-div") == 0
               || strcmp(mode, "owner-xor-apple-sqrt-local") == 0);
}

static bool exact_division_enabled(void)
{
    const char* mode = getenv("WALLE_REVEAL_AXIS_ABLATION");
    return mode != nullptr && strcmp(mode, "owner-xor-apple-sqrt-exact-div") == 0;
}

static bool global_owner_enabled(void)
{
    const char* mode = getenv("WALLE_REVEAL_AXIS_ABLATION");
    return mode != nullptr
           && (strcmp(mode, "owner-xor") == 0
               || strcmp(mode, "owner-xor-apple-sqrt") == 0
               || strcmp(mode, "owner-xor-apple-sqrt-exact-div") == 0);
}

static void ensure_p25_bitmap(void)
{
    if (p25_bitmap != nullptr)
        return;
    const char* path = getenv("WALLE_REVEAL_P25_BITMAP");
    if (path == nullptr)
        path = "/tmp/walle/lg-test/Analysis/raster_p25_selector_ceil_bits.bin";
    FILE* input = fopen(path, "rb");
    if (input == nullptr) {
        fprintf(stderr, "reveal axis interposer: cannot open %s: %s\n", path, strerror(errno));
        exit(EXIT_FAILURE);
    }
    constexpr size_t byte_count = 1u << 21;
    p25_bitmap = malloc(byte_count);
    if (p25_bitmap == nullptr || fread(p25_bitmap, 1, byte_count, input) != byte_count
        || fgetc(input) != EOF || fclose(input) != 0) {
        axis_fail("invalid P25 selector bitmap");
    }
}

static void ensure_axis_texture(void)
{
    if (axis_texture != 0)
        return;
    glGenTextures(1, &axis_texture);
    glBindTexture(GL_TEXTURE_2D, axis_texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
}

static void ensure_apple_sqrt_texture(void)
{
    if (apple_sqrt_texture != 0)
        return;
    const char* path = getenv("WALLE_APPLE_FAST_SQRT_TABLE");
    if (path == nullptr)
        path = "/tmp/walle/artifacts/apple-float-intrinsics-r8-30556057571.bin";
    FILE* input = fopen(path, "rb");
    if (input == nullptr) {
        fprintf(stderr, "reveal axis interposer: cannot open %s: %s\n", path, strerror(errno));
        exit(EXIT_FAILURE);
    }
    constexpr size_t byte_count = 1u << 23;
    uint8_t* table = malloc(byte_count);
    if (table == nullptr || fread(table, 1, byte_count, input) != byte_count || fgetc(input) != EOF
        || fclose(input) != 0) {
        axis_fail("invalid Apple fast-sqrt table");
    }
    GLint prior_active;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &prior_active);
    glActiveTexture(GL_TEXTURE15);
    glGenTextures(1, &apple_sqrt_texture);
    glBindTexture(GL_TEXTURE_2D, apple_sqrt_texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R8UI, 4096, 2048, 0, GL_RED_INTEGER, GL_UNSIGNED_BYTE, table);
    glActiveTexture((GLenum)prior_active);
    free(table);
    if (glGetError() != GL_NO_ERROR)
        axis_fail("Apple fast-sqrt texture upload failed");
}

static void expand_quad(size_t group, bool compact, struct walle_lg_vertex output[static 6])
{
    for (size_t local = 0; local < 6; ++local) {
        size_t source_index = captured_indices[group * 6 + local];
        if (source_index >= vertex_count)
            axis_fail("captured reveal index is outside the vertex upload");
        const uint8_t* source = vertex_bytes + source_index * 48;
        float position[4];
        float coordinates[2];
        memcpy(position, source, sizeof position);
        memcpy(coordinates, source + (compact ? 16 : 24), sizeof coordinates);
        memcpy(output[local].position, position, sizeof position);
        memcpy(output[local].sdf, coordinates, sizeof coordinates);
        memcpy(output[local].source, coordinates, sizeof coordinates);
    }
}

static double triangle_area(const struct walle_lg_vertex triangle[static 3])
{
    double ab_x = (double)triangle[1].position[0] - triangle[0].position[0];
    double ab_y = (double)triangle[1].position[1] - triangle[0].position[1];
    double ac_x = (double)triangle[2].position[0] - triangle[0].position[0];
    double ac_y = (double)triangle[2].position[1] - triangle[0].position[1];
    return ab_x * ac_y - ab_y * ac_x;
}

static bool complete_compact_quad(const struct walle_lg_vertex triangle[static 3],
                                  struct walle_lg_vertex       output[static 6])
{
    float low[2]  = {triangle[0].position[0], triangle[0].position[1]};
    float high[2] = {triangle[0].position[0], triangle[0].position[1]};
    for (size_t vertex = 1; vertex < 3; ++vertex) {
        for (size_t axis = 0; axis < 2; ++axis) {
            if (triangle[vertex].position[axis] < low[axis])
                low[axis] = triangle[vertex].position[axis];
            if (triangle[vertex].position[axis] > high[axis])
                high[axis] = triangle[vertex].position[axis];
        }
    }
    if (low[0] == high[0] || low[1] == high[1])
        return false;

    bool present[2][2] = {};
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        size_t x = triangle[vertex].position[0] == high[0] ? 1 : 0;
        size_t y = triangle[vertex].position[1] == high[1] ? 1 : 0;
        if ((triangle[vertex].position[0] != low[0] && x == 0)
            || (triangle[vertex].position[1] != low[1] && y == 0) || present[x][y]) {
            return false;
        }
        present[x][y] = true;
    }
    size_t missing_x = 2;
    size_t missing_y = 2;
    for (size_t x = 0; x < 2; ++x) {
        for (size_t y = 0; y < 2; ++y) {
            if (!present[x][y]) {
                missing_x = x;
                missing_y = y;
            }
        }
    }
    if (missing_x > 1 || missing_y > 1)
        return false;

    struct walle_lg_vertex missing = triangle[0];
    missing.position[0]            = missing_x == 0 ? low[0] : high[0];
    missing.position[1]            = missing_y == 0 ? low[1] : high[1];
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        if (triangle[vertex].position[0] == missing.position[0]) {
            missing.sdf[0]    = triangle[vertex].sdf[0];
            missing.source[0] = triangle[vertex].source[0];
        }
        if (triangle[vertex].position[1] == missing.position[1]) {
            missing.sdf[1]    = triangle[vertex].sdf[1];
            missing.source[1] = triangle[vertex].source[1];
        }
    }
    size_t diagonal[2] = {SIZE_MAX, SIZE_MAX};
    for (size_t left = 0; left < 3; ++left) {
        for (size_t right = left + 1; right < 3; ++right) {
            if (triangle[left].position[0] != triangle[right].position[0]
                && triangle[left].position[1] != triangle[right].position[1]) {
                diagonal[0] = left;
                diagonal[1] = right;
            }
        }
    }
    if (diagonal[0] == SIZE_MAX)
        return false;
    memcpy(output, triangle, 3 * sizeof *output);
    output[3] = triangle[diagonal[0]];
    output[4] = missing;
    output[5] = triangle[diagonal[1]];
    return true;
}

static GLint geometric_primitive(const struct runtime_quad*    quad,
                                 const struct walle_lg_vertex triangle[static 3])
{
    int32_t sample_x = (int32_t)(((double)triangle[0].position[0]
                                  + triangle[1].position[0] + triangle[2].position[0])
                                 / 3.0);
    int32_t sample_y = (int32_t)(((double)triangle[0].position[1]
                                  + triangle[1].position[1] + triangle[2].position[1])
                                 / 3.0);
    int64_t relative_x = (int64_t)sample_x * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2
                         - quad->raster.origin_x_fixed;
    int64_t relative_y = (int64_t)sample_y * SUBPIXEL_SCALE + SUBPIXEL_SCALE / 2
                         - quad->raster.origin_y_fixed;
    if (quad->ascending_diagonal) {
        return relative_y * quad->raster.width_fixed > relative_x * quad->raster.height_fixed ? 1
                                                                                                : 0;
    }
    int64_t diagonal = relative_x * quad->raster.height_fixed
                       + relative_y * quad->raster.width_fixed;
    int64_t area = (int64_t)quad->raster.width_fixed * quad->raster.height_fixed;
    return diagonal < area ? 1 : 0;
}

static bool upload_quad_axis(const struct walle_lg_vertex vertices[static 6],
                             GLint                        start_location)
{
    struct runtime_quad quad;
    if (!runtime_quad_from_vertices(vertices, &quad)) {
        generation_failure = "runtime quad reconstruction";
        return false;
    }
    ensure_p25_bitmap();
    const struct walle_lg_raster_calibration calibration = {
        .p25_ceil_bits          = p25_bitmap,
        .p25_selector_bit_count = UINT64_C(1) << 24,
    };
    uint32_t selector;
    int      selector_exponent;
    if (!base_selector(&quad, &calibration, &selector, &selector_exponent)) {
        generation_failure = "P25 base selector";
        return false;
    }

    int32_t bounds[4];
    visible_bounds(&quad.raster, bounds);
    int32_t first = (bounds[0] < bounds[1] ? bounds[0] : bounds[1]) - 1;
    int32_t end   = (bounds[2] > bounds[3] ? bounds[2] : bounds[3]) + 1;
    if (end <= first) {
        generation_failure = "empty visible bounds";
        return false;
    }
    uint32_t count = (uint32_t)(end - first);
    size_t word_count_value;
    if (!word_count(WALLE_LG_RASTER_PRIMITIVE_COUNT,
                    count,
                    WALLE_LG_RASTER_CHANNEL_COUNT,
                    &word_count_value)) {
        generation_failure = "axis word-count overflow";
        return false;
    }
    uint32_t* words = malloc(word_count_value * sizeof *words);
    if (words == nullptr) {
        generation_failure = "axis allocation";
        return false;
    }

    struct timespec generation_start;
    struct timespec generation_end;
    if (clock_gettime(CLOCK_MONOTONIC, &generation_start) != 0)
        axis_fail("cannot read monotonic clock");
    int32_t  generated_start;
    uint32_t generated_count;
    bool generated = axis_table(&quad,
                                selector,
                                selector_exponent,
                                false,
                                1,
                                &generated_start,
                                &generated_count,
                                words);
    if (clock_gettime(CLOCK_MONOTONIC, &generation_end) != 0)
        axis_fail("cannot read monotonic clock");
    generation_nanoseconds += elapsed_nanoseconds(&generation_start, &generation_end);
    if (!generated || generated_start != first || generated_count != count) {
        generation_failure = "axis arithmetic";
        free(words);
        return false;
    }

    glTexImage2D(GL_TEXTURE_2D,
                 0,
                 GL_RGBA32UI,
                 (GLsizei)count,
                 2,
                 0,
                 GL_RGBA_INTEGER,
                 GL_UNSIGNED_INT,
                 words);
    glUniform1i(start_location, first);
    free(words);
    size_t bytes = word_count_value * sizeof(uint32_t);
    ++generated_quad_count;
    generated_word_count += word_count_value;
    uploaded_byte_count += bytes;
    if (bytes > maximum_texture_bytes)
        maximum_texture_bytes = bytes;
    GLenum error = glGetError();
    if (error != GL_NO_ERROR) {
        static char description[64];
        snprintf(description, sizeof description, "texture upload GL error 0x%04x", error);
        generation_failure = description;
        return false;
    }
    return true;
}

struct prepared_axis_quad
{
    size_t                 source_group;
    struct walle_lg_vertex original[6];
    struct runtime_quad    runtime;
    bool                   active[2];
    GLint                  geometric_primitive[2];
    int32_t                axis_start;
    uint32_t               axis_count;
    size_t                 axis_word_count;
    uint32_t*              axis_words;
    int32_t                visible_bounds[4];
};

static bool generate_prepared_axis(struct prepared_axis_quad* prepared)
{
    ensure_p25_bitmap();
    const struct walle_lg_raster_calibration calibration = {
        .p25_ceil_bits          = p25_bitmap,
        .p25_selector_bit_count = UINT64_C(1) << 24,
    };
    uint32_t selector;
    int      selector_exponent;
    if (!base_selector(&prepared->runtime, &calibration, &selector, &selector_exponent))
        return false;
    visible_bounds(&prepared->runtime.raster, prepared->visible_bounds);
    int32_t first = (prepared->visible_bounds[0] < prepared->visible_bounds[1]
                         ? prepared->visible_bounds[0]
                         : prepared->visible_bounds[1])
                    - 1;
    int32_t end = (prepared->visible_bounds[2] > prepared->visible_bounds[3]
                       ? prepared->visible_bounds[2]
                       : prepared->visible_bounds[3])
                  + 1;
    if (end <= first)
        return false;
    uint32_t count = (uint32_t)(end - first);
    if (!word_count(WALLE_LG_RASTER_PRIMITIVE_COUNT,
                    count,
                    WALLE_LG_RASTER_CHANNEL_COUNT,
                    &prepared->axis_word_count)) {
        return false;
    }
    prepared->axis_words = malloc(prepared->axis_word_count * sizeof *prepared->axis_words);
    if (prepared->axis_words == nullptr)
        return false;
    struct timespec start;
    struct timespec finish;
    if (clock_gettime(CLOCK_MONOTONIC, &start) != 0)
        axis_fail("cannot read monotonic clock");
    bool generated = axis_table(&prepared->runtime,
                                selector,
                                selector_exponent,
                                false,
                                1,
                                &prepared->axis_start,
                                &prepared->axis_count,
                                prepared->axis_words);
    if (clock_gettime(CLOCK_MONOTONIC, &finish) != 0)
        axis_fail("cannot read monotonic clock");
    generation_nanoseconds += elapsed_nanoseconds(&start, &finish);
    if (!generated || prepared->axis_start != first || prepared->axis_count != count) {
        free(prepared->axis_words);
        prepared->axis_words = nullptr;
        return false;
    }
    ++generated_quad_count;
    generated_word_count += prepared->axis_word_count;
    return true;
}

static size_t prepare_axis_quads(bool                       compact,
                                 size_t                     group_count,
                                 struct prepared_axis_quad output[static 4])
{
    size_t prepared_count = 0;
    for (size_t group = 0; group < group_count; ++group) {
        struct walle_lg_vertex original[6];
        struct walle_lg_vertex generated[6];
        expand_quad(group, compact, original);
        bool active[2] = {
            triangle_area(original) != 0.0,
            triangle_area(original + 3) != 0.0,
        };
        if (!active[0] && !active[1])
            continue;
        if (prepared_count >= 4)
            axis_fail("more than four active public reveal quads");
        if (active[0] && active[1]) {
            memcpy(generated, original, sizeof generated);
        } else {
            const struct walle_lg_vertex* triangle = active[0] ? original : original + 3;
            if (!complete_compact_quad(triangle, generated))
                axis_fail("cannot complete an active right triangle");
        }
        struct prepared_axis_quad* prepared = &output[prepared_count];
        *prepared = (struct prepared_axis_quad){.source_group = group,
                                                .active       = {active[0], active[1]}};
        memcpy(prepared->original, original, sizeof original);
        if (!runtime_quad_from_vertices(generated, &prepared->runtime))
            axis_fail("cannot reconstruct a prepared axis quad");
        for (size_t ordinal = 0; ordinal < 2; ++ordinal) {
            if (active[ordinal]) {
                prepared->geometric_primitive[ordinal]
                    = geometric_primitive(&prepared->runtime, original + ordinal * 3);
            }
        }
        if (!generate_prepared_axis(prepared))
            axis_fail("cannot generate a packed exact axis table");
        ++prepared_count;
    }
    return prepared_count;
}

static void draw_global_owner(GLenum mode, GLenum type, bool compact, size_t group_count)
{
    struct timespec submission_start;
    struct timespec submission_end;
    if (clock_gettime(CLOCK_MONOTONIC, &submission_start) != 0)
        axis_fail("cannot read monotonic clock");

    struct prepared_axis_quad prepared[4] = {};
    size_t prepared_count = prepare_axis_quads(compact, group_count, prepared);
    if (prepared_count == 0)
        return;
    uint32_t packed_width = 0;
    for (size_t slot = 0; slot < prepared_count; ++slot) {
        if (prepared[slot].axis_count > packed_width)
            packed_width = prepared[slot].axis_count;
    }
    size_t packed_word_count;
    if (!word_count(prepared_count * 2,
                    packed_width,
                    WALLE_LG_RASTER_CHANNEL_COUNT,
                    &packed_word_count)) {
        axis_fail("packed axis word count overflows");
    }
    uint32_t* packed = calloc(packed_word_count, sizeof *packed);
    if (packed == nullptr)
        axis_fail("cannot allocate a packed exact axis table");
    for (size_t slot = 0; slot < prepared_count; ++slot) {
        for (size_t primitive = 0; primitive < 2; ++primitive) {
            size_t source = primitive * (size_t)prepared[slot].axis_count
                            * WALLE_LG_RASTER_CHANNEL_COUNT;
            size_t destination = (slot * 2 + primitive) * (size_t)packed_width
                                 * WALLE_LG_RASTER_CHANNEL_COUNT;
            memcpy(packed + destination,
                   prepared[slot].axis_words + source,
                   (size_t)prepared[slot].axis_count * WALLE_LG_RASTER_CHANNEL_COUNT
                       * sizeof(uint32_t));
        }
        free(prepared[slot].axis_words);
        prepared[slot].axis_words = nullptr;
    }

    GLint table_location        = glGetUniformLocation(current_program, "AxisTable");
    GLint starts_location       = glGetUniformLocation(current_program, "AxisStarts[0]");
    GLint slot_location         = glGetUniformLocation(current_program, "AxisSlot");
    GLint primitive_location    = glGetUniformLocation(current_program, "AxisPrimitive");
    GLint slot_count_location   = glGetUniformLocation(current_program, "OwnerSlotCount");
    GLint origins_location      = glGetUniformLocation(current_program, "OwnerOriginFixed[0]");
    GLint extents_location      = glGetUniformLocation(current_program, "OwnerExtentFixed[0]");
    GLint bounds_location       = glGetUniformLocation(current_program, "OwnerBounds[0]");
    GLint ascending_location    = glGetUniformLocation(current_program, "OwnerAscending[0]");
    GLint active_masks_location = glGetUniformLocation(current_program, "OwnerActiveMask[0]");
    if (table_location < 0 || starts_location < 0 || slot_location < 0 || primitive_location < 0
        || slot_count_location < 0 || origins_location < 0 || extents_location < 0
        || bounds_location < 0 || ascending_location < 0 || active_masks_location < 0) {
        axis_fail("global-owner uniforms are incomplete");
    }

    GLint starts[4]      = {};
    GLint origins[8]     = {};
    GLint extents[8]     = {};
    GLint bounds[16]     = {};
    GLint ascending[4]   = {};
    GLint active_masks[4] = {};
    for (size_t slot = 0; slot < prepared_count; ++slot) {
        starts[slot]            = prepared[slot].axis_start;
        origins[slot * 2]       = prepared[slot].runtime.raster.origin_x_fixed;
        origins[slot * 2 + 1]   = prepared[slot].runtime.raster.origin_y_fixed;
        extents[slot * 2]       = prepared[slot].runtime.raster.width_fixed;
        extents[slot * 2 + 1]   = prepared[slot].runtime.raster.height_fixed;
        memcpy(bounds + slot * 4, prepared[slot].visible_bounds, 4 * sizeof(GLint));
        ascending[slot] = prepared[slot].runtime.ascending_diagonal ? 1 : 0;
        for (size_t ordinal = 0; ordinal < 2; ++ordinal) {
            if (prepared[slot].active[ordinal])
                active_masks[slot] |= 1 << prepared[slot].geometric_primitive[ordinal];
        }
    }
    glUniform1iv(starts_location, 4, starts);
    glUniform1i(slot_count_location, (GLint)prepared_count);
    glUniform2iv(origins_location, 4, origins);
    glUniform2iv(extents_location, 4, extents);
    glUniform4iv(bounds_location, 4, bounds);
    glUniform1iv(ascending_location, 4, ascending);
    glUniform1iv(active_masks_location, 4, active_masks);

    GLint prior_active;
    GLint prior_binding;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &prior_active);
    glActiveTexture(GL_TEXTURE14);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &prior_binding);
    ensure_axis_texture();
    glBindTexture(GL_TEXTURE_2D, axis_texture);
    glTexImage2D(GL_TEXTURE_2D,
                 0,
                 GL_RGBA32UI,
                 (GLsizei)packed_width,
                 (GLsizei)(prepared_count * 2),
                 0,
                 GL_RGBA_INTEGER,
                 GL_UNSIGNED_INT,
                 packed);
    free(packed);
    glUniform1i(table_location, 14);
    size_t packed_bytes = packed_word_count * sizeof(uint32_t);
    uploaded_byte_count += packed_bytes;
    if (packed_bytes > maximum_texture_bytes)
        maximum_texture_bytes = packed_bytes;

    if (owner_apple_sqrt_enabled()) {
        GLint apple_location = glGetUniformLocation(current_program, "AppleFastSqrtTable");
        GLint exact_division_location = glGetUniformLocation(current_program, "ExactDivisionMode");
        if (apple_location < 0 || exact_division_location < 0)
            axis_fail("Apple arithmetic uniforms are absent");
        ensure_apple_sqrt_texture();
        glUniform1i(apple_location, 15);
        glUniform1i(exact_division_location, exact_division_enabled() ? 1 : 0);
        glActiveTexture(GL_TEXTURE14);
        glBindTexture(GL_TEXTURE_2D, axis_texture);
    }
    if (glGetError() != GL_NO_ERROR)
        axis_fail("packed global-owner setup failed");

    for (size_t slot = 0; slot < prepared_count; ++slot) {
        glUniform1i(slot_location, (GLint)slot);
        for (size_t ordinal = 0; ordinal < 2; ++ordinal) {
            if (!prepared[slot].active[ordinal])
                continue;
            glUniform1i(primitive_location, prepared[slot].geometric_primitive[ordinal]);
            const void* offset
                = (const void*)(uintptr_t)((prepared[slot].source_group * 6 + ordinal * 3)
                                           * sizeof(uint16_t));
            real_draw_elements(mode, 3, type, offset);
        }
    }
    if (clock_gettime(CLOCK_MONOTONIC, &submission_end) != 0)
        axis_fail("cannot read monotonic clock");
    submission_nanoseconds += elapsed_nanoseconds(&submission_start, &submission_end);
    glBindTexture(GL_TEXTURE_2D, (GLuint)prior_binding);
    glActiveTexture((GLenum)prior_active);
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY
glShaderSource(GLuint shader, GLsizei count, const GLchar* const* strings, const GLint* lengths)
{
    if (real_shader_source == nullptr)
        load_real_symbol(&real_shader_source, sizeof real_shader_source, "glShaderSource");
    if (!axis_enabled()) {
        real_shader_source(shader, count, strings, lengths);
        return;
    }
    char* source = join_shader_source(count, strings, lengths);
    if (source == nullptr)
        axis_fail("cannot join shader source");
    if (owner_xor_enabled() && strstr(source, "RevealResolution") != nullptr
        && strstr(source, "in_Position") != nullptr) {
        char* version = strstr(source, "#version 300 es");
        if (version == nullptr)
            axis_fail("reveal vertex shader version differs");
        version[10] = '2';
        const GLchar* upgraded = source;
        real_shader_source(shader, 1, &upgraded, nullptr);
        free(source);
        return;
    }
    if (strstr(source, "RevealCoverage") != nullptr && strstr(source, "v_SDF") != nullptr) {
        const GLchar* replacement = owner_apple_sqrt_enabled() && global_owner_enabled()
                                        ? global_owner_apple_sqrt_fragment_source
                                    : global_owner_enabled() ? global_owner_axis_fragment_source
                                    : owner_apple_sqrt_enabled()
                                        ? owner_apple_sqrt_fragment_source
                                    : owner_xor_enabled() ? owner_axis_fragment_source
                                                          : native_axis_fragment_source;
        real_shader_source(shader, 1, &replacement, nullptr);
    } else {
        real_shader_source(shader, count, strings, lengths);
    }
    free(source);
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY glUseProgram(GLuint program)
{
    if (real_use_program == nullptr)
        load_real_symbol(&real_use_program, sizeof real_use_program, "glUseProgram");
    real_use_program(program);
    current_program = program;
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY
glBufferSubData(GLenum target, GLintptr offset, GLsizeiptr size, const void* data)
{
    if (real_buffer_sub_data == nullptr) {
        load_real_symbol(&real_buffer_sub_data,
                         sizeof real_buffer_sub_data,
                         "glBufferSubData");
    }
    real_buffer_sub_data(target, offset, size, data);
    if (!axis_enabled() || offset != 0 || data == nullptr)
        return;
    if (target == GL_ARRAY_BUFFER && size >= 4 * 48 && size <= 16 * 48 && size % 48 == 0) {
        memcpy(vertex_bytes, data, (size_t)size);
        vertex_count = (size_t)size / 48;
    } else if (target == GL_ELEMENT_ARRAY_BUFFER && size >= 6 * 2 && size <= 54 * 2
               && size % (6 * 2) == 0) {
        memcpy(captured_indices, data, (size_t)size);
        captured_index_count = (size_t)size / sizeof(uint16_t);
    }
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY
glDrawElements(GLenum mode, GLsizei count, GLenum type, const void* indices)
{
    if (real_draw_elements == nullptr)
        load_real_symbol(&real_draw_elements, sizeof real_draw_elements, "glDrawElements");
    if (!axis_enabled() || current_program == 0 || mode != GL_TRIANGLES || type != GL_UNSIGNED_SHORT
        || indices != nullptr || count <= 0 || (size_t)count != captured_index_count
        || (size_t)count % 6 != 0) {
        real_draw_elements(mode, count, type, indices);
        return;
    }
    GLint table_location     = glGetUniformLocation(current_program, "AxisTable");
    GLint start_location     = glGetUniformLocation(current_program, "AxisStart");
    GLint primitive_location = glGetUniformLocation(current_program, "AxisPrimitive");
    GLint compact_location   = glGetUniformLocation(current_program, "RevealCompactFamily");
    GLint origin_location    = glGetUniformLocation(current_program, "AxisOriginFixed");
    GLint extent_location    = glGetUniformLocation(current_program, "AxisExtentFixed");
    GLint diagonal_location  = glGetUniformLocation(current_program, "AxisAscendingDiagonal");
    GLint apple_sqrt_location = glGetUniformLocation(current_program, "AppleFastSqrtTable");
    if (table_location < 0) {
        real_draw_elements(mode, count, type, indices);
        return;
    }
    if (compact_location < 0 || vertex_count == 0)
        axis_fail("axis program or captured geometry is incomplete");
    GLfloat compact_value;
    glGetUniformfv(current_program, compact_location, &compact_value);
    bool compact = compact_value == 1.0f;
    if (global_owner_enabled()) {
        draw_global_owner(mode, type, compact, (size_t)count / 6);
        return;
    }
    if (start_location < 0 || primitive_location < 0)
        axis_fail("local axis program is incomplete");
    if (owner_xor_enabled()
        && (origin_location < 0 || extent_location < 0 || diagonal_location < 0)) {
        axis_fail("owner-XOR uniforms are incomplete");
    }
    if (owner_apple_sqrt_enabled() && apple_sqrt_location < 0)
        axis_fail("Apple fast-sqrt sampler is absent");

    GLint prior_active;
    GLint prior_binding;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &prior_active);
    glActiveTexture(GL_TEXTURE14);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &prior_binding);
    ensure_axis_texture();
    glBindTexture(GL_TEXTURE_2D, axis_texture);
    glUniform1i(table_location, 14);
    if (owner_apple_sqrt_enabled()) {
        ensure_apple_sqrt_texture();
        glUniform1i(apple_sqrt_location, 15);
        glActiveTexture(GL_TEXTURE14);
        glBindTexture(GL_TEXTURE_2D, axis_texture);
    }

    struct timespec submission_start;
    struct timespec submission_end;
    if (clock_gettime(CLOCK_MONOTONIC, &submission_start) != 0)
        axis_fail("cannot read monotonic clock");
    size_t groups = (size_t)count / 6;
    for (size_t group = 0; group < groups; ++group) {
        struct walle_lg_vertex original[6];
        struct walle_lg_vertex generated_quad[6];
        expand_quad(group, compact, original);
        bool first_active  = triangle_area(original) != 0.0;
        bool second_active = triangle_area(original + 3) != 0.0;
        if (!first_active && !second_active)
            continue;
        if (first_active && second_active) {
            memcpy(generated_quad, original, sizeof generated_quad);
        } else {
            const struct walle_lg_vertex* active = first_active ? original : original + 3;
            if (!complete_compact_quad(active, generated_quad))
                axis_fail("cannot complete an active right triangle");
        }
        generation_failure = "unknown";
        if (!upload_quad_axis(generated_quad, start_location)) {
            fprintf(stderr,
                    "reveal axis interposer: group %zu failed: %s\n",
                    group,
                    generation_failure);
            exit(EXIT_FAILURE);
        }
        struct runtime_quad runtime;
        if (!runtime_quad_from_vertices(generated_quad, &runtime))
            axis_fail("cannot reconstruct a generated quad twice");
        if (owner_xor_enabled()) {
            glUniform2i(origin_location,
                        runtime.raster.origin_x_fixed,
                        runtime.raster.origin_y_fixed);
            glUniform2i(extent_location,
                        runtime.raster.width_fixed,
                        runtime.raster.height_fixed);
            glUniform1i(diagonal_location, runtime.ascending_diagonal ? 1 : 0);
        }
        for (size_t ordinal = 0; ordinal < 2; ++ordinal) {
            if ((ordinal == 0 && !first_active) || (ordinal == 1 && !second_active))
                continue;
            GLint primitive = geometric_primitive(&runtime, original + ordinal * 3);
            glUniform1i(primitive_location, primitive);
            const void* offset = (const void*)(uintptr_t)((group * 6 + ordinal * 3)
                                                          * sizeof(uint16_t));
            real_draw_elements(mode, 3, type, offset);
        }
    }
    if (clock_gettime(CLOCK_MONOTONIC, &submission_end) != 0)
        axis_fail("cannot read monotonic clock");
    submission_nanoseconds += elapsed_nanoseconds(&submission_start, &submission_end);
    glBindTexture(GL_TEXTURE_2D, (GLuint)prior_binding);
    glActiveTexture((GLenum)prior_active);
}

__attribute__((destructor)) static void report_axis_statistics(void)
{
    if (!axis_enabled())
        return;
    fprintf(stderr,
            "REVEAL_AXIS_QUADS=%llu\n"
            "REVEAL_AXIS_WORDS=%llu\n"
            "REVEAL_AXIS_UPLOAD_BYTES=%llu\n"
            "REVEAL_AXIS_MAX_TEXTURE_BYTES=%llu\n"
            "REVEAL_AXIS_GENERATION_NANOSECONDS=%llu\n"
            "REVEAL_AXIS_SUBMISSION_NANOSECONDS=%llu\n",
            (unsigned long long)generated_quad_count,
            (unsigned long long)generated_word_count,
            (unsigned long long)uploaded_byte_count,
            (unsigned long long)maximum_texture_bytes,
            (unsigned long long)generation_nanoseconds,
            (unsigned long long)submission_nanoseconds);
}
