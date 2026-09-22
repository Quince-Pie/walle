#define _GNU_SOURCE
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

static PFN_vkQueueSubmit2 next_submit;
static ssize_t (*next_pread)(int,void *,size_t,off_t);
static void *(*next_mmap)(void *,size_t,int,int,int,off_t);
static int (*next_close)(int);
static unsigned fail_at;
static _Atomic unsigned uploads,frames,injections,opened,closed;
static _Atomic bool source_fds[4096];
static int report_fd=-1;
static const char *cache;

static void emit(const char *text)
{if(report_fd>=0)(void)syscall(SYS_write,report_fd,text,strlen(text));}
static void track(int fd)
{
    if(fd<0||fd>=4096||!cache)return;
    char link[64],path[4096];snprintf(link,sizeof link,"/proc/self/fd/%d",fd);
    ssize_t n=readlink(link,path,sizeof path-1);if(n<0)return;path[n]=0;
    size_t prefix=strlen(cache);
    bool image_cache=!strncmp(path,cache,prefix)&&!strncmp(path+prefix,"/walle/",7);
    if(!image_cache&&!strstr(path,"memfd:walle-render"))return;
    if(!atomic_exchange(&source_fds[fd],true)) {
        atomic_fetch_add(&opened,1);
        char line[96];snprintf(line,sizeof line,"{\"event\":\"source_open\",\"fd\":%d}\n",fd);emit(line);
    }
}
[[gnu::constructor]] static void initialize(void)
{
    void *symbol=dlsym(RTLD_NEXT,"vkQueueSubmit2");memcpy(&next_submit,&symbol,sizeof symbol);
    symbol=dlsym(RTLD_NEXT,"pread");memcpy(&next_pread,&symbol,sizeof symbol);
    symbol=dlsym(RTLD_NEXT,"mmap");memcpy(&next_mmap,&symbol,sizeof symbol);
    symbol=dlsym(RTLD_NEXT,"close");memcpy(&next_close,&symbol,sizeof symbol);
    cache=getenv("XDG_CACHE_HOME");
    const char *stage=getenv("WALLE_FAIL_UPLOAD"),*file=getenv("WALLE_FAULT_TRACE");
    fail_at=stage?(unsigned)strtoul(stage,nullptr,10):0;
    if(file)report_fd=open(file,O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC,0600);
}
VKAPI_ATTR VkResult VKAPI_CALL vkQueueSubmit2(VkQueue queue,uint32_t count,const VkSubmitInfo2 *submits,VkFence fence)
{
    /* Actual Wayland frames signal a presentation semaphore; source uploads
     * have one command buffer and no wait/signal semaphores. */
    bool upload=count==1&&submits&&submits[0].commandBufferInfoCount==1&&
        submits[0].waitSemaphoreInfoCount==0&&submits[0].signalSemaphoreInfoCount==0;
    unsigned ordinal=upload?atomic_fetch_add(&uploads,1)+1:0;
    if(!upload)atomic_fetch_add(&frames,1);
    bool fail=upload&&fail_at&&ordinal==fail_at&&atomic_fetch_add(&injections,1)==0;
    char line[192];snprintf(line,sizeof line,"{\"event\":\"submit\",\"upload\":%s,\"upload_ordinal\":%u,\"injected\":%s}\n",upload?"true":"false",ordinal,fail?"true":"false");emit(line);
    if(fail)return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    return next_submit?next_submit(queue,count,submits,fence):VK_ERROR_INITIALIZATION_FAILED;
}
ssize_t pread(int fd,void *data,size_t length,off_t offset)
{
    ssize_t result=next_pread?next_pread(fd,data,length,offset):syscall(SYS_pread64,fd,data,length,offset);
    if(result>0)track(fd);
    return result;
}
void *mmap(void *address,size_t length,int protection,int flags,int fd,off_t offset)
{
    void *result=next_mmap?next_mmap(address,length,protection,flags,fd,offset):(void *)syscall(SYS_mmap,address,length,protection,flags,fd,offset);
    if(result!=MAP_FAILED&&fd>=0&&(protection&PROT_WRITE))track(fd);
    return result;
}
int close(int fd)
{
    int result=next_close?next_close(fd):(int)syscall(SYS_close,fd);
    if(result==0&&fd>=0&&fd<4096&&atomic_exchange(&source_fds[fd],false)) {
        atomic_fetch_add(&closed,1);char line[96];snprintf(line,sizeof line,"{\"event\":\"source_close\",\"fd\":%d}\n",fd);emit(line);
    }
    return result;
}
[[gnu::destructor]] static void summarize(void)
{
    unsigned remaining=0;for(unsigned i=0;i<4096;++i)remaining+=atomic_load(&source_fds[i]);
    char line[256];snprintf(line,sizeof line,"{\"event\":\"summary\",\"uploads\":%u,\"frames\":%u,\"injections\":%u,\"source_opened\":%u,\"source_closed\":%u,\"source_remaining\":%u}\n",atomic_load(&uploads),atomic_load(&frames),atomic_load(&injections),atomic_load(&opened),atomic_load(&closed),remaining);emit(line);
}
