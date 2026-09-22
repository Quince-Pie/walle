#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wayland-client.h>
#include "output-management.h"

struct state {struct wl_display *display; struct zwlr_output_manager_v1 *manager;
    struct zwlr_output_head_v1 *head; uint32_t serial; bool ready; int result;};
static void string_event(void *data,struct zwlr_output_head_v1 *head,const char *value)
{(void)data;(void)head;(void)value;}
static void pair_event(void *data,struct zwlr_output_head_v1 *head,int32_t a,int32_t b)
{(void)data;(void)head;(void)a;(void)b;}
static void int_event(void *data,struct zwlr_output_head_v1 *head,int32_t value)
{(void)data;(void)head;(void)value;}
static void uint_event(void *data,struct zwlr_output_head_v1 *head,uint32_t value)
{(void)data;(void)head;(void)value;}
static void head_finished(void *data,struct zwlr_output_head_v1 *head)
{(void)data;(void)head;}
static void mode_size(void *data,struct zwlr_output_mode_v1 *mode,int32_t w,int32_t h)
{(void)data;(void)mode;(void)w;(void)h;}
static void mode_refresh(void *data,struct zwlr_output_mode_v1 *mode,int32_t refresh)
{(void)data;(void)mode;(void)refresh;}
static void mode_flag(void *data,struct zwlr_output_mode_v1 *mode)
{(void)data;(void)mode;}
static const struct zwlr_output_mode_v1_listener mode_listener={.size=mode_size,.refresh=mode_refresh,.preferred=mode_flag,.finished=mode_flag};
static void mode_event(void *data,struct zwlr_output_head_v1 *head,struct zwlr_output_mode_v1 *mode)
{(void)head;zwlr_output_mode_v1_add_listener(mode,&mode_listener,data);}
static void current_mode(void *data,struct zwlr_output_head_v1 *head,struct zwlr_output_mode_v1 *mode)
{(void)data;(void)head;(void)mode;}
static const struct zwlr_output_head_v1_listener head_listener={
    .name=string_event,.description=string_event,.physical_size=pair_event,.mode=mode_event,
    .enabled=int_event,.current_mode=current_mode,.position=pair_event,.transform=int_event,
    .scale=int_event,.finished=head_finished,.make=string_event,.model=string_event,
    .serial_number=string_event,.adaptive_sync=uint_event};
static void manager_head(void *data,struct zwlr_output_manager_v1 *manager,struct zwlr_output_head_v1 *head)
{(void)manager;struct state *s=data;if(!s->head)s->head=head;zwlr_output_head_v1_add_listener(head,&head_listener,data);}
static void manager_done(void *data,struct zwlr_output_manager_v1 *manager,uint32_t serial)
{(void)manager;struct state *s=data;s->serial=serial;s->ready=true;}
static void manager_finished(void *data,struct zwlr_output_manager_v1 *manager)
{(void)manager;((struct state *)data)->result=-1;}
static const struct zwlr_output_manager_v1_listener manager_listener={manager_head,manager_done,manager_finished};
static void global(void *data,struct wl_registry *registry,uint32_t name,const char *interface,uint32_t version)
{
    struct state *s=data;
    if(!strcmp(interface,zwlr_output_manager_v1_interface.name)) {
        s->manager=wl_registry_bind(registry,name,&zwlr_output_manager_v1_interface,version<4?version:4);
        zwlr_output_manager_v1_add_listener(s->manager,&manager_listener,s);
    }
}
static void removed(void *data,struct wl_registry *registry,uint32_t name)
{(void)data;(void)registry;(void)name;}
static const struct wl_registry_listener registry_listener={global,removed};
static void succeeded(void *data,struct zwlr_output_configuration_v1 *configuration)
{(void)configuration;((struct state *)data)->result=1;}
static void failed(void *data,struct zwlr_output_configuration_v1 *configuration)
{(void)configuration;((struct state *)data)->result=-1;}
static const struct zwlr_output_configuration_v1_listener configuration_listener={succeeded,failed,failed};
int main(int argc,char **argv)
{
    if(argc!=4)return 2;
    int width=atoi(argv[1]),height=atoi(argv[2]);double scale=strtod(argv[3],nullptr);
    if(width<=0||height<=0||scale<=0)return 2;
    struct state s={};s.display=wl_display_connect(nullptr);if(!s.display)return 2;
    struct wl_registry *registry=wl_display_get_registry(s.display);wl_registry_add_listener(registry,&registry_listener,&s);
    if(wl_display_roundtrip(s.display)<0||wl_display_roundtrip(s.display)<0||!s.ready||!s.manager||!s.head)return 2;
    struct zwlr_output_configuration_v1 *configuration=zwlr_output_manager_v1_create_configuration(s.manager,s.serial);
    zwlr_output_configuration_v1_add_listener(configuration,&configuration_listener,&s);
    struct zwlr_output_configuration_head_v1 *head=zwlr_output_configuration_v1_enable_head(configuration,s.head);
    zwlr_output_configuration_head_v1_set_custom_mode(head,width,height,0);
    zwlr_output_configuration_head_v1_set_scale(head,wl_fixed_from_double(scale));
    zwlr_output_configuration_v1_apply(configuration);
    while(!s.result&&wl_display_dispatch(s.display)>=0){}
    printf("{\"applied\":%s,\"width\":%d,\"height\":%d,\"scale\":%.6g}\n",s.result==1?"true":"false",width,height,scale);
    zwlr_output_configuration_v1_destroy(configuration);wl_display_disconnect(s.display);
    return s.result==1?0:1;
}
