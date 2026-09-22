#include "material/material.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

struct material_fixture {
    const char *name;
    struct wm_material_input input;
    struct wm_render_domain domain;
    uint8_t glass[216],face[48],highlight[48],key_fill[40],tint[48],gradient[24],fill[16];
    double plan[16],tint_plan[11];
    bool presence[4];
    uint32_t kernel;
};
#include "material_fixture_data.h"

static size_t compared_bytes;
static unsigned compared_scalars;
static bool same_bytes(const char *name,const char *field,const void *actual,const void *expected,size_t count)
{
    const uint8_t *a=actual,*e=expected;
    for(size_t i=0;i<count;++i)if(a[i]!=e[i]) {
        fprintf(stderr,"%s %s byte%zu: got%02x expected%02x\n",name,field,i,a[i],e[i]);
        return false;
    }
    compared_bytes+=count;return true;
}
static bool same_doubles(const char *name,const char *field,const double *actual,const double *expected,size_t count)
{
    for(size_t i=0;i<count;++i) {
        if(memcmp(actual+i,expected+i,sizeof(double))) {
            fprintf(stderr,"%s %s[%zu]: got%a expected%a\n",name,field,i,actual[i],expected[i]);
            return false;
        }
        ++compared_scalars;
    }
    return true;
}
int main(void)
{
    unsigned count=0;
    for(size_t i=0;i<sizeof material_fixtures/sizeof *material_fixtures;++i) {
        const struct material_fixture *f=material_fixtures+i;
        struct wm_recipe *recipe=wm_recipe_create(&f->input);
        if(!recipe){fprintf(stderr,"%s create failed\n",f->name);return 1;}
        struct wm_shader_packet p;
        bool ok=wm_recipe_pack(recipe,&f->domain,&p);
        wm_recipe_destroy(recipe);
        if(!ok){fprintf(stderr,"%s pack failed\n",f->name);return 1;}
        if(!same_bytes(f->name,"glass216",p.glass_lph,f->glass,sizeof f->glass)||
           !same_bytes(f->name,"face48",p.face_vcm,f->face,sizeof f->face)||
           !same_bytes(f->name,"highlight48",p.highlight_vcm,f->highlight,sizeof f->highlight)||
           !same_bytes(f->name,"key_fill40",p.key_fill,f->key_fill,sizeof f->key_fill))return 1;
        const struct wm_plan_parameters *q=&p.plan;
        double actual[16]={q->backdrop_scale,q->margin,q->blur_min,q->blur_max,
            q->output_minimum,q->output_maximum,q->smoothness,q->ovalization,
            q->highlight_pad,q->highlight_maximum,q->shadow_grow,q->maximum_refraction,
            q->face_opacity,q->highlight_opacity,q->shadow_offset[0],q->shadow_offset[1]};
        bool presence[4]={q->has_backdrop,q->has_highlight,q->has_tint,q->tracks_luma};
        if(!same_doubles(f->name,"plan",actual,f->plan,16)||
           !same_bytes(f->name,"presence",presence,f->presence,sizeof presence)||
           !same_bytes(f->name,"kernel",&p.glass_texture_function,&f->kernel,sizeof f->kernel))return 1;
        if(f->input.tint.present) {
            if(!same_bytes(f->name,"tint48",p.tint_vcm,f->tint,sizeof f->tint)||
               !same_bytes(f->name,"gradient24",p.tint_gradient,f->gradient,sizeof f->gradient)||
               !same_bytes(f->name,"fill16",p.tint_mask_fill,f->fill,sizeof f->fill)||
               !same_bytes(f->name,"ramp2048",p.tint_ramp_rgba16f,expected_tint_ramp,sizeof expected_tint_ramp))return 1;
            double tint_plan[11]={q->tint_mask_pad,q->tint_mask_maximum,q->tint_gradient_pad,q->tint_gradient_maximum,
                q->tint_group_opacity,q->tint_gradient_smoothness,q->tint_mask_smoothness,q->tint_effect_offset,
                q->tint_distances[0],q->tint_distances[1],q->tint_distances[2]};
            if(!same_doubles(f->name,"tint plan",tint_plan,f->tint_plan,11))return 1;
        }
        ++count;
    }
    printf("{\"cases\":%u,\"bytes\":%zu,\"scalars\":%u,\"mismatches\":0}\n",count,compared_bytes,compared_scalars);
    return 0;
}
