#define _POSIX_C_SOURCE 200809L

#include <GLES3/gl3.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef void(GL_APIENTRYP shader_source_fn)(GLuint, GLsizei, const GLchar* const*, const GLint*);
typedef void(GL_APIENTRYP use_program_fn)(GLuint);
typedef void(GL_APIENTRYP draw_elements_fn)(GLenum, GLsizei, GLenum, const void*);
typedef void(GL_APIENTRYP buffer_sub_data_fn)(GLenum, GLintptr, GLsizeiptr, const void*);

static shader_source_fn   real_shader_source;
static use_program_fn     real_use_program;
static draw_elements_fn   real_draw_elements;
static buffer_sub_data_fn real_buffer_sub_data;
static GLuint             apple_sqrt_texture;
static GLuint             current_program;
static float              analytic_center[2];
static float              analytic_radius;
static bool               analytic_geometry_valid;

static const char fragment_prefix[]
    = "#version 300 es\n"
      "precision highp float;\n"
      "precision highp int;\n"
      "in vec2 v_SDF;\n"
      "layout(location = 0) out float RevealCoverage;\n";

static const char fragment_prefix_320[]
    = "#version 320 es\n"
      "precision highp float;\n"
      "precision highp int;\n"
      "in vec2 v_SDF;\n"
      "layout(location = 0) out float RevealCoverage;\n";

static const char fragment_baseline[]
    = "void main(){float d=length(v_SDF);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
      "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;"
      "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_separate[]
    = "void main(){float d=sqrt(v_SDF.x*v_SDF.x+v_SDF.y*v_SDF.y);"
      "float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);float a=clamp((1.0-d)/f+0.5,0.0,1.0);"
      "float "
      "h=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_fma[]
    = "void main(){float d=sqrt(fma(v_SDF.y,v_SDF.y,v_SDF.x*v_SDF.x));"
      "float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);float a=clamp((1.0-d)/f+0.5,0.0,1.0);"
      "float "
      "h=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_fma_reversed[]
    = "void main(){float d=sqrt(fma(v_SDF.x,v_SDF.x,v_SDF.y*v_SDF.y));"
      "float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);float a=clamp((1.0-d)/f+0.5,0.0,1.0);"
      "float "
      "h=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_no_half[]
    = "void main(){float d=length(v_SDF);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
      "float a=clamp((1.0-d)/f+0.5,0.0,1.0);RevealCoverage=roundEven(a*255.0)/255.0;}\n";

static const char fragment_half_attachment[]
    = "void main(){float d=length(v_SDF);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
      "float "
      "a=clamp((1.0-d)/f+0.5,0.0,1.0);RevealCoverage=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;}\n";

static const char fragment_no_half_attachment[]
    = "void main(){float d=length(v_SDF);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
      "RevealCoverage=clamp((1.0-d)/f+0.5,0.0,1.0);}\n";

static const char fragment_reciprocal[]
    = "void main(){float d=length(v_SDF);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
      "float a=clamp((1.0-d)*(1.0/f)+0.5,0.0,1.0);float "
      "h=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;"
      "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_linear_derivative[]
    = "void main(){float d=length(v_SDF);vec2 n=v_SDF/d;"
      "float f=max(abs(dot(n,dFdx(v_SDF)))+abs(dot(n,dFdy(v_SDF))),1e-4);"
      "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;"
      "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

#define GLSL_EXACT_SQRT                                                                            \
    "void ff(uint b,out uint s,out int e){uint x=(b>>23u)&255u;if(x==0u){s=b&0x7fffffu;e=-149;}"   \
    "else{s=(b&0x7fffffu)|0x800000u;e=int(x)-150;}}"                                               \
    "void mf(uint l,uint r,out uint s,out int e){uint a,c;int b,d;ff(l,a,b);ff(r,c,d);e=min(b,d);" \
    "s=(a<<uint(b-e))+(c<<uint(d-e));e-=1;}"                                                       \
    "uvec3 sh(uint v,int n){if(n<0||n>=96)return uvec3(0);if(n==0)return uvec3(v,0,0);"            \
    "if(n<32)return uvec3(v<<uint(n),v>>uint(32-n),0);if(n==32)return uvec3(0,v,0);"               \
    "if(n<64)return uvec3(0,v<<uint(n-32),v>>uint(64-n));return uvec3(0,0,v<<uint(n-64));}"        \
    "int sc(uint vb,uint ms,int me){uint vs;int ve;ff(vb,vs,ve);uint "                             \
    "hi,lo;umulExtended(ms,ms,hi,lo);"                                                             \
    "int n=ve-2*me;if(n<0)return 1;if(n>=96)return -1;uvec3 a=uvec3(lo,hi,0),b=sh(vs,n);"          \
    "if(a.z!=b.z)return a.z<b.z?-1:1;if(a.y!=b.y)return a.y<b.y?-1:1;"                             \
    "if(a.x!=b.x)return a.x<b.x?-1:1;return 0;}"                                                   \
    "float ir(float v){float r=sqrt(v);if(!(v>0.0)||isinf(v))return r;uint vb=floatBitsToUint(v)," \
    "rb=floatBitsToUint(r);for(int i=0;i<4;++i){uint s;int "                                       \
    "e;mf(rb-1u,rb,s,e);if(sc(vb,s,e)>0){--rb;continue;}"                                          \
    "mf(rb,rb+1u,s,e);if(sc(vb,s,e)<0){++rb;continue;}break;}return uintBitsToFloat(rb);}"

#define GLSL_EXACT_HALF                                                                            \
    "uint hu(uint b){if((b&0x7fffu)==0u)return 1u;if((b&0x8000u)!=0u)return "                      \
    "b==0xfc00u?0xfbffu:b-1u;"                                                                     \
    "return b==0x7c00u?b:b+1u;}"                                                                   \
    "uint hd(uint b){if((b&0x7fffu)==0u)return 0x8001u;if((b&0x8000u)!=0u)return "                 \
    "b==0xfc00u?b:b+1u;"                                                                           \
    "return b==0x7c00u?0x7bffu:b-1u;}"                                                             \
    "float eh(float v){uint b=packHalf2x16(vec2(v,0))&0xffffu;float r=unpackHalf2x16(b).x;"        \
    "if(v==r||isnan(v))return r;bool up=v>r;uint a=up?hu(b):hd(b);float x=unpackHalf2x16(a).x;"    \
    "float m=(isinf(r)||isinf(x))?((r<0.0||x<0.0)?-65520.0:65520.0):(r+x)*0.5;"                    \
    "bool choose=up?v>m:v<m;if(v==m)choose=(a&1u)==0u;return unpackHalf2x16(choose?a:b).x;}"

#define GLSL_IDEAL_HALF                                                                            \
    "uint rr(uint v,uint s){if(s==0u)return v;uint t=v>>s,m=(1u<<s)-1u,r=v&m,h=1u<<(s-1u);"        \
    "if(r>h||(r==h&&(t&1u)!=0u))++t;return t;}"                                                    \
    "uint ib(float v){uint "                                                                       \
    "b=floatBitsToUint(v),sign=(b>>16u)&0x8000u,e=(b>>23u)&255u,m=b&0x7fffffu;"                    \
    "if(e==255u)return sign|(m==0u?0x7c00u:0x7e00u);if(e==0u)return sign;int u=int(e)-127,h=u+15;" \
    "if(h>=31)return sign|0x7c00u;if(h<=0){if(u<-25)return sign;uint "                             \
    "q=rr(m|0x800000u,uint(-u-1));"                                                                \
    "return sign|min(q,0x400u);}uint q=rr(m,13u);if(q==0x400u){q=0u;++h;if(h>=31)return "          \
    "sign|0x7c00u;}"                                                                               \
    "return sign|(uint(h)<<10u)|q;}"

static const char fragment_exact_sqrt[] = GLSL_EXACT_SQRT
    "void main(){float d=ir(fma(v_SDF.y,v_SDF.y,v_SDF.x*v_SDF.x));"
    "float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);float a=clamp((1.0-d)/f+0.5,0.0,1.0);"
    "float h=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_exact_half[] = GLSL_EXACT_HALF
    "void main(){float d=length(v_SDF);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
    "float a=clamp((1.0-d)/f+0.5,0.0,1.0);RevealCoverage=roundEven(eh(a)*255.0)/255.0;}\n";

static const char fragment_ideal_half[] = GLSL_IDEAL_HALF
    "void main(){float d=length(v_SDF);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
    "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=unpackHalf2x16(ib(a)).x;"
    "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_exact_half_fast[] = GLSL_EXACT_HALF
    "void main(){float d=length(v_SDF);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
    "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=(a==0.0||a==1.0)?a:eh(a);"
    "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_ideal_half_fast[] = GLSL_IDEAL_HALF
    "void main(){float d=length(v_SDF);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
    "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=(a==0.0||a==1.0)?a:unpackHalf2x16(ib(a)).x;"
    "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_exact_sqrt_half[] = GLSL_EXACT_SQRT GLSL_EXACT_HALF
    "void main(){float d=ir(fma(v_SDF.y,v_SDF.y,v_SDF.x*v_SDF.x));"
    "float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);float a=clamp((1.0-d)/f+0.5,0.0,1.0);"
    "RevealCoverage=roundEven(eh(a)*255.0)/255.0;}\n";

static const char fragment_apple_sqrt_exact[]
    = "precision highp usampler2D;uniform usampler2D AppleFastSqrtTable;" GLSL_EXACT_SQRT
      "float ad(float x,float y){float s=fma(y,y,x*x);float r=ir(s);uint b=floatBitsToUint(s);"
      "uint m=b&0x007fffffu;uint "
      "c=texelFetch(AppleFastSqrtTable,ivec2(int(m&4095u),int(m>>12u)),0).r;"
      "uint q=((b>>23u)&1u)==0u?c&3u:(c>>2u)&3u;return "
      "uintBitsToFloat(uint(int(floatBitsToUint(r))+int(q)-1));}"
      "void main(){float d=ad(v_SDF.x,v_SDF.y);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
      "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;"
      "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_apple_sqrt_exact_half[]
    = "precision highp usampler2D;uniform usampler2D "
      "AppleFastSqrtTable;" GLSL_EXACT_SQRT GLSL_EXACT_HALF
      "float ad(float x,float y){float s=fma(y,y,x*x);float r=ir(s);uint b=floatBitsToUint(s);"
      "uint m=b&0x007fffffu;uint "
      "c=texelFetch(AppleFastSqrtTable,ivec2(int(m&4095u),int(m>>12u)),0).r;"
      "uint q=((b>>23u)&1u)==0u?c&3u:(c>>2u)&3u;return "
      "uintBitsToFloat(uint(int(floatBitsToUint(r))+int(q)-1));}"
      "void main(){float d=ad(v_SDF.x,v_SDF.y);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
      "float a=clamp((1.0-d)/f+0.5,0.0,1.0);RevealCoverage=roundEven(eh(a)*255.0)/255.0;}\n";

static const char fragment_half_selfcheck[] = GLSL_EXACT_HALF GLSL_IDEAL_HALF
    "void main(){uint x=uint(gl_FragCoord.x-0.5),y=uint(gl_FragCoord.y-0.5),i=y*2048u+x;"
    "uint boundary=(i/33u)%15360u;int delta=int(i%33u)-16;float l=unpackHalf2x16(boundary).x;"
    "float "
    "r=unpackHalf2x16(boundary+1u).x,m=(l+r)*0.5,v=uintBitsToFloat(uint(int(floatBitsToUint(m))+"
    "delta));"
    "if(v_SDF.x>1e30)v=v_SDF.x;uint raw=packHalf2x16(vec2(v,0))&0xffffu;"
    "uint repaired=packHalf2x16(vec2(eh(v),0))&0xffffu,ideal=ib(v);"
    "uint code=repaired!=ideal?255u:(raw!=ideal?128u:1u);RevealCoverage=float(code)/255.0;}\n";

static const char fragment_apple_sqrt[]
    = "precision highp usampler2D;uniform usampler2D AppleFastSqrtTable;"
      "float ad(float x,float y){float s=fma(y,y,x*x);float r=sqrt(s);uint b=floatBitsToUint(s);"
      "uint m=b&0x007fffffu;uint "
      "c=texelFetch(AppleFastSqrtTable,ivec2(int(m&4095u),int(m>>12u)),0).r;"
      "uint q=((b>>23u)&1u)==0u?c&3u:(c>>2u)&3u;return "
      "uintBitsToFloat(uint(int(floatBitsToUint(r))+int(q)-1));}"
      "void main(){float d=ad(v_SDF.x,v_SDF.y);float f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
      "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;"
      "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_analytic[]
    = "uniform vec2 AnalyticCenter;uniform float AnalyticRadius;uniform vec2 RevealResolution;"
      "vec2 asdf(){return "
      "(vec2(gl_FragCoord.x,RevealResolution.y-gl_FragCoord.y)-AnalyticCenter)/AnalyticRadius;}"
      "void main(){float d=length(asdf());if(v_SDF.x>1e30)d=v_SDF.x;float "
      "f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
      "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;"
      "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static const char fragment_analytic_no_half[]
    = "uniform vec2 AnalyticCenter;uniform float AnalyticRadius;uniform vec2 RevealResolution;"
      "vec2 asdf(){return "
      "(vec2(gl_FragCoord.x,RevealResolution.y-gl_FragCoord.y)-AnalyticCenter)/AnalyticRadius;}"
      "void main(){float d=length(asdf());if(v_SDF.x>1e30)d=v_SDF.x;float "
      "f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
      "float a=clamp((1.0-d)/f+0.5,0.0,1.0);RevealCoverage=roundEven(a*255.0)/255.0;}\n";

static const char fragment_analytic_apple_sqrt[]
    = "precision highp usampler2D;uniform usampler2D AppleFastSqrtTable;"
      "uniform vec2 AnalyticCenter;uniform float AnalyticRadius;uniform vec2 RevealResolution;"
      "vec2 asdf(){return "
      "(vec2(gl_FragCoord.x,RevealResolution.y-gl_FragCoord.y)-AnalyticCenter)/AnalyticRadius;}"
      "float ad(vec2 p){float s=fma(p.y,p.y,p.x*p.x);float r=sqrt(s);uint b=floatBitsToUint(s);"
      "uint m=b&0x007fffffu;uint "
      "c=texelFetch(AppleFastSqrtTable,ivec2(int(m&4095u),int(m>>12u)),0).r;"
      "uint q=((b>>23u)&1u)==0u?c&3u:(c>>2u)&3u;return "
      "uintBitsToFloat(uint(int(floatBitsToUint(r))+int(q)-1));}"
      "void main(){float d=ad(asdf());if(v_SDF.x>1e30)d=v_SDF.x;float "
      "f=max(abs(dFdx(d))+abs(dFdy(d)),1e-4);"
      "float a=clamp((1.0-d)/f+0.5,0.0,1.0);float h=unpackHalf2x16(packHalf2x16(vec2(a,0))).x;"
      "RevealCoverage=roundEven(h*255.0)/255.0;}\n";

static void load_real_symbol(void* destination, size_t size, const char* name)
{
    void* symbol = dlsym(RTLD_NEXT, name);
    if (symbol == NULL || size != sizeof(symbol)) {
        fprintf(stderr, "reveal arithmetic interposer: cannot resolve %s\n", name);
        exit(EXIT_FAILURE);
    }
    memcpy(destination, &symbol, size);
}

static char* joined_source(GLsizei count, const GLchar* const* strings, const GLint* lengths)
{
    size_t total = 0;
    for (GLsizei index = 0; index < count; ++index) {
        size_t length = lengths != NULL && lengths[index] >= 0 ? (size_t)lengths[index]
                                                               : strlen(strings[index]);
        if (length > SIZE_MAX - total - 1)
            return NULL;
        total += length;
    }
    char* result = malloc(total + 1);
    if (result == NULL)
        return NULL;
    size_t offset = 0;
    for (GLsizei index = 0; index < count; ++index) {
        size_t length = lengths != NULL && lengths[index] >= 0 ? (size_t)lengths[index]
                                                               : strlen(strings[index]);
        memcpy(result + offset, strings[index], length);
        offset += length;
    }
    result[offset] = '\0';
    return result;
}

static const char* selected_fragment(const char* variant)
{
    if (strcmp(variant, "baseline") == 0 || strcmp(variant, "no-dither") == 0)
        return fragment_baseline;
    if (strcmp(variant, "separate") == 0)
        return fragment_separate;
    if (strcmp(variant, "fma") == 0)
        return fragment_fma;
    if (strcmp(variant, "fma-reversed") == 0)
        return fragment_fma_reversed;
    if (strcmp(variant, "no-half") == 0 || strcmp(variant, "no-half-no-dither") == 0)
        return fragment_no_half;
    if (strcmp(variant, "half-attachment") == 0)
        return fragment_half_attachment;
    if (strcmp(variant, "no-half-attachment") == 0)
        return fragment_no_half_attachment;
    if (strcmp(variant, "reciprocal") == 0)
        return fragment_reciprocal;
    if (strcmp(variant, "linear-derivative") == 0)
        return fragment_linear_derivative;
    if (strcmp(variant, "exact-sqrt") == 0)
        return fragment_exact_sqrt;
    if (strcmp(variant, "exact-half") == 0)
        return fragment_exact_half;
    if (strcmp(variant, "ideal-half") == 0)
        return fragment_ideal_half;
    if (strcmp(variant, "exact-half-fast") == 0)
        return fragment_exact_half_fast;
    if (strcmp(variant, "ideal-half-fast") == 0)
        return fragment_ideal_half_fast;
    if (strcmp(variant, "exact-sqrt-half") == 0)
        return fragment_exact_sqrt_half;
    if (strcmp(variant, "apple-sqrt") == 0)
        return fragment_apple_sqrt;
    if (strcmp(variant, "apple-sqrt-exact") == 0)
        return fragment_apple_sqrt_exact;
    if (strcmp(variant, "apple-sqrt-exact-half") == 0)
        return fragment_apple_sqrt_exact_half;
    if (strcmp(variant, "half-selfcheck") == 0)
        return fragment_half_selfcheck;
    if (strcmp(variant, "analytic") == 0)
        return fragment_analytic;
    if (strcmp(variant, "analytic-no-half") == 0)
        return fragment_analytic_no_half;
    if (strcmp(variant, "analytic-apple-sqrt") == 0)
        return fragment_analytic_apple_sqrt;
    return NULL;
}

GL_APICALL void GL_APIENTRY glShaderSource(GLuint               shader,
                                           GLsizei              count,
                                           const GLchar* const* strings,
                                           const GLint*         lengths)
{
    if (real_shader_source == NULL)
        load_real_symbol(&real_shader_source, sizeof(real_shader_source), "glShaderSource");
    const char* variant = getenv("WALLE_REVEAL_ARITHMETIC_ABLATION");
    if (variant == NULL) {
        real_shader_source(shader, count, strings, lengths);
        return;
    }
    char* source = joined_source(count, strings, lengths);
    if (source == NULL) {
        fprintf(stderr, "reveal arithmetic interposer: cannot join shader source\n");
        exit(EXIT_FAILURE);
    }
    bool needs_fma
        = strcmp(variant, "fma") == 0 || strcmp(variant, "fma-reversed") == 0
          || strcmp(variant, "apple-sqrt") == 0 || strcmp(variant, "analytic-apple-sqrt") == 0
          || strcmp(variant, "exact-sqrt") == 0 || strcmp(variant, "exact-sqrt-half") == 0
          || strcmp(variant, "apple-sqrt-exact") == 0
          || strcmp(variant, "apple-sqrt-exact-half") == 0;
    if (needs_fma && strstr(source, "RevealResolution") != NULL
        && strstr(source, "in_Position") != NULL) {
        char* version = strstr(source, "#version 300 es");
        if (version == NULL) {
            fprintf(stderr, "reveal arithmetic interposer: reveal vertex version differs\n");
            exit(EXIT_FAILURE);
        }
        version[10]            = '2';
        const GLchar* upgraded = source;
        real_shader_source(shader, 1, &upgraded, NULL);
        free(source);
        return;
    }
    if (strstr(source, "RevealCoverage") == NULL || strstr(source, "v_SDF") == NULL) {
        free(source);
        real_shader_source(shader, count, strings, lengths);
        return;
    }
    const char* body = selected_fragment(variant);
    if (body == NULL) {
        fprintf(stderr, "reveal arithmetic interposer: unknown variant %s\n", variant);
        exit(EXIT_FAILURE);
    }
    const char* prefix        = needs_fma ? fragment_prefix_320 : fragment_prefix;
    size_t      prefix_length = strlen(prefix);
    size_t      body_length   = strlen(body);
    char*       replacement   = malloc(prefix_length + body_length + 1);
    if (replacement == NULL) {
        fprintf(stderr, "reveal arithmetic interposer: cannot allocate replacement shader\n");
        exit(EXIT_FAILURE);
    }
    memcpy(replacement, prefix, prefix_length);
    memcpy(replacement + prefix_length, body, body_length + 1);
    const GLchar* replacement_pointer = replacement;
    real_shader_source(shader, 1, &replacement_pointer, NULL);
    free(replacement);
    free(source);
}

static void ensure_apple_sqrt_texture(void)
{
    if (apple_sqrt_texture != 0)
        return;
    const char* path = getenv("WALLE_APPLE_FAST_SQRT_TABLE");
    if (path == NULL)
        path = "/tmp/walle/artifacts/apple-float-intrinsics-r8-30556057571.bin";
    FILE* input = fopen(path, "rb");
    if (input == NULL) {
        fprintf(
            stderr, "reveal arithmetic interposer: cannot open %s: %s\n", path, strerror(errno));
        exit(EXIT_FAILURE);
    }
    constexpr size_t byte_count = 1u << 23;
    uint8_t*         table      = malloc(byte_count);
    if (table == NULL || fread(table, 1, byte_count, input) != byte_count || fgetc(input) != EOF
        || fclose(input) != 0) {
        fprintf(stderr, "reveal arithmetic interposer: invalid Apple sqrt table\n");
        exit(EXIT_FAILURE);
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
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R8UI, 4096, 2048, 0, GL_RED_INTEGER, GL_UNSIGNED_BYTE, table);
    glActiveTexture((GLenum)prior_active);
    free(table);
    if (glGetError() != GL_NO_ERROR) {
        fprintf(stderr, "reveal arithmetic interposer: Apple sqrt texture upload failed\n");
        exit(EXIT_FAILURE);
    }
}

GL_APICALL void GL_APIENTRY glUseProgram(GLuint program)
{
    if (real_use_program == NULL)
        load_real_symbol(&real_use_program, sizeof(real_use_program), "glUseProgram");
    real_use_program(program);
    current_program     = program;
    const char* variant = getenv("WALLE_REVEAL_ARITHMETIC_ABLATION");
    if (variant == NULL
        || (strcmp(variant, "apple-sqrt") != 0 && strcmp(variant, "analytic-apple-sqrt") != 0
            && strcmp(variant, "apple-sqrt-exact") != 0
            && strcmp(variant, "apple-sqrt-exact-half") != 0)
        || program == 0)
        return;
    GLint location = glGetUniformLocation(program, "AppleFastSqrtTable");
    if (location < 0)
        return;
    ensure_apple_sqrt_texture();
    GLint prior_active;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &prior_active);
    glActiveTexture(GL_TEXTURE15);
    glBindTexture(GL_TEXTURE_2D, apple_sqrt_texture);
    glUniform1i(location, 15);
    glActiveTexture((GLenum)prior_active);
}

GL_APICALL void GL_APIENTRY glBufferSubData(GLenum      target,
                                            GLintptr    offset,
                                            GLsizeiptr  size,
                                            const void* data)
{
    if (real_buffer_sub_data == NULL)
        load_real_symbol(&real_buffer_sub_data, sizeof(real_buffer_sub_data), "glBufferSubData");
    real_buffer_sub_data(target, offset, size, data);
    if (target != GL_ARRAY_BUFFER)
        return;
    analytic_geometry_valid = false;
    if (offset != 0 || size < 4 * 48 || size > 16 * 48 || size % 48 != 0 || data == NULL)
        return;
    size_t vertex_count            = (size_t)size / 48;
    float  minimum[2]              = {0.0f, 0.0f};
    float  maximum[2]              = {0.0f, 0.0f};
    float  max_first               = 0.0f;
    float  max_second              = 0.0f;
    float  compact_center[2]       = {0.0f, 0.0f};
    bool   compact_center_found[2] = {false, false};
    for (size_t vertex = 0; vertex < vertex_count; ++vertex) {
        const uint8_t* bytes = (const uint8_t*)data + vertex * 48;
        float          values[6];
        memcpy(values, bytes, 2 * sizeof(float));
        memcpy(values + 2, bytes + 16, 4 * sizeof(float));
        for (size_t axis = 0; axis < 2; ++axis) {
            if (vertex == 0 || values[axis] < minimum[axis])
                minimum[axis] = values[axis];
            if (vertex == 0 || values[axis] > maximum[axis])
                maximum[axis] = values[axis];
            float first  = values[2 + axis] < 0.0f ? -values[2 + axis] : values[2 + axis];
            float second = values[4 + axis] < 0.0f ? -values[4 + axis] : values[4 + axis];
            if (first > max_first)
                max_first = first;
            if (second > max_second)
                max_second = second;
            if (first == 0.0f) {
                if (compact_center_found[axis] && compact_center[axis] != values[axis])
                    return;
                compact_center[axis]       = values[axis];
                compact_center_found[axis] = true;
            }
        }
    }
    float half_extent_x = (maximum[0] - minimum[0]) * 0.5f;
    float half_extent_y = (maximum[1] - minimum[1]) * 0.5f;
    bool  compact       = max_first <= 1.01f && max_second > 2.0f;
    float radius_x      = half_extent_x - 1.0f;
    float radius_y      = half_extent_y - 1.0f;
    if (compact) {
        if (!compact_center_found[0] || !compact_center_found[1])
            return;
        radius_x          = maximum[0] - compact_center[0];
        float alternate_x = compact_center[0] - minimum[0];
        if (alternate_x > radius_x)
            radius_x = alternate_x;
        radius_y          = maximum[1] - compact_center[1];
        float alternate_y = compact_center[1] - minimum[1];
        if (alternate_y > radius_y)
            radius_y = alternate_y;
    }
    if (!(radius_x > 0.0f) || radius_x != radius_y)
        return;
    analytic_center[0]      = compact ? compact_center[0] : (minimum[0] + maximum[0]) * 0.5f;
    analytic_center[1]      = compact ? compact_center[1] : (minimum[1] + maximum[1]) * 0.5f;
    analytic_radius         = radius_x;
    analytic_geometry_valid = true;
}

GL_APICALL void GL_APIENTRY glDrawElements(GLenum      mode,
                                           GLsizei     count,
                                           GLenum      type,
                                           const void* indices)
{
    if (real_draw_elements == NULL)
        load_real_symbol(&real_draw_elements, sizeof(real_draw_elements), "glDrawElements");
    const char* variant = getenv("WALLE_REVEAL_ARITHMETIC_ABLATION");
    if (variant != NULL
        && (strcmp(variant, "no-dither") == 0 || strcmp(variant, "no-half-no-dither") == 0)) {
        glDisable(GL_DITHER);
    }
    if (analytic_geometry_valid && current_program != 0 && variant != NULL
        && (strcmp(variant, "analytic") == 0 || strcmp(variant, "analytic-no-half") == 0
            || strcmp(variant, "analytic-apple-sqrt") == 0)) {
        GLint center_location = glGetUniformLocation(current_program, "AnalyticCenter");
        GLint radius_location = glGetUniformLocation(current_program, "AnalyticRadius");
        if (center_location < 0 || radius_location < 0) {
            fprintf(stderr, "reveal arithmetic interposer: analytic uniforms are absent\n");
            exit(EXIT_FAILURE);
        }
        glUniform2f(center_location, analytic_center[0], analytic_center[1]);
        glUniform1f(radius_location, analytic_radius);
    }
    const char* repetitions_text = getenv("WALLE_REVEAL_DRAW_REPETITIONS");
    if (repetitions_text == NULL) {
        real_draw_elements(mode, count, type, indices);
        return;
    }
    char* end;
    errno            = 0;
    long repetitions = strtol(repetitions_text, &end, 10);
    if (errno != 0 || end == repetitions_text || *end != '\0' || repetitions < 1
        || repetitions > 10'000) {
        fprintf(stderr, "reveal arithmetic interposer: invalid draw repetitions\n");
        exit(EXIT_FAILURE);
    }
    real_draw_elements(mode, count, type, indices);
    glFinish();
    struct timespec started;
    struct timespec finished;
    if (clock_gettime(CLOCK_MONOTONIC, &started) != 0) {
        fprintf(stderr, "reveal arithmetic interposer: monotonic clock failed\n");
        exit(EXIT_FAILURE);
    }
    for (long repetition = 0; repetition < repetitions; ++repetition)
        real_draw_elements(mode, count, type, indices);
    glFinish();
    if (clock_gettime(CLOCK_MONOTONIC, &finished) != 0) {
        fprintf(stderr, "reveal arithmetic interposer: monotonic clock failed\n");
        exit(EXIT_FAILURE);
    }
    int64_t seconds = (int64_t)finished.tv_sec - (int64_t)started.tv_sec;
    int64_t nanoseconds
        = seconds * INT64_C(1'000'000'000) + (int64_t)finished.tv_nsec - (int64_t)started.tv_nsec;
    fprintf(stderr, "REVEAL_DRAW_NANOSECONDS=%lld\n", (long long)(nanoseconds / repetitions));
}
