#define _POSIX_C_SOURCE 200809L
#include "quickjs.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdarg.h>
#include <stddef.h>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <io.h>
#include <fcntl.h>
#else
#include <sys/resource.h>
#endif

#define FRAME_MAX (1024u * 1024u)
static int send_frame(const char *text,size_t n);
/* Public JS_NewRuntime2 allocator: retain an out-of-band failure bit even when
   NG cannot allocate an Error and JavaScript catches its null fallback. */
typedef struct {size_t used;size_t limit;int failed;int notified;const char *token;} Quota;
#ifdef _MSC_VER
/* MSVC's C stddef.h lacks max_align_t; keep the 64-bit CRT's 16-byte alignment. */
typedef __declspec(align(16)) union {unsigned char minimum_alignment[16];struct {size_t size;} value;} Allocation;
#else
typedef union {max_align_t alignment;unsigned char minimum_alignment[16];struct {size_t size;} value;} Allocation;
#endif
_Static_assert(sizeof(Allocation)%16==0,"Quota allocation headers must preserve 16-byte alignment");
static void quota_failure(Quota *q) {
    q->failed=1;
    if(!q->notified) {
        q->notified=1;
        char message[160];int n=snprintf(message,sizeof(message),"{\"kind\":\"resource_limit\",\"operation\":\"%s\"}",q->token);
        if(n>0&&(size_t)n<sizeof(message))send_frame(message,(size_t)n);
    }
}
static void *quota_malloc(void *opaque,size_t size) {
    Quota *q=opaque;
    if(size>SIZE_MAX-sizeof(Allocation)||size+sizeof(Allocation)>q->limit-q->used){quota_failure(q);return NULL;}
    Allocation *block=malloc(sizeof(Allocation)+size);
    if(!block){quota_failure(q);return NULL;}
    block->value.size=size;q->used+=sizeof(Allocation)+size;return block+1;
}
static void quota_free(void *opaque,void *ptr) {
    if(!ptr)return;
    Quota *q=opaque;Allocation *block=(Allocation*)ptr-1;
    q->used-=sizeof(Allocation)+block->value.size;free(block);
}
static void *quota_calloc(void *opaque,size_t count,size_t size) {
    if(count&&size>SIZE_MAX/count){quota_failure(opaque);return NULL;}
    size_t length=count*size;void *ptr=quota_malloc(opaque,length);
    if(ptr)memset(ptr,0,length);
    return ptr;
}
static void *quota_realloc(void *opaque,void *ptr,size_t size) {
    if(!ptr)return quota_malloc(opaque,size);
    if(!size){quota_free(opaque,ptr);return NULL;}
    Quota *q=opaque;Allocation *old=(Allocation*)ptr-1;
    size_t previous=old->value.size,without=q->used-previous-sizeof(Allocation);
    if(size>SIZE_MAX-sizeof(Allocation)||size+sizeof(Allocation)>q->limit-without){quota_failure(q);return NULL;}
    Allocation *block=realloc(old,sizeof(Allocation)+size);
    if(!block){quota_failure(q);return NULL;}
    block->value.size=size;q->used=without+sizeof(Allocation)+size;return block+1;
}
static size_t quota_size(const void *ptr){return ptr?((const Allocation*)ptr-1)->value.size:0;}
static const JSMallocFunctions quota_functions={quota_calloc,quota_malloc,quota_free,quota_realloc,quota_size};
/* Bounded output builder replaces POSIX open_memstream on every platform. */
typedef struct {char *data; size_t length; int failed; char **result; size_t *size;} Buffer;
static Buffer *new_buffer(char **result,size_t *size) {
    Buffer *b=calloc(1,sizeof(*b));if(!b)exit(70);
    b->data=malloc(FRAME_MAX+1);if(!b->data){free(b);exit(70);}
    b->result=result;b->size=size;*result=b->data;*size=0;return b;
}
static void buf_write(const char *text,size_t length,Buffer *b) {
    if(b->failed||length>FRAME_MAX-b->length){b->failed=1;return;}
    memcpy(b->data+b->length,text,length);b->length+=length;
}
static void buf_putc(int c,Buffer *b){char v=(char)c;buf_write(&v,1,b);}
static void buf_puts(const char *text,Buffer *b){buf_write(text,strlen(text),b);}
static void buf_printf(Buffer *b,const char *format,...) {
    if(b->failed)return;
    va_list args;va_start(args,format);
    int n=vsnprintf(b->data+b->length,FRAME_MAX+1-b->length,format,args);va_end(args);
    if(n<0||(size_t)n>FRAME_MAX-b->length){b->failed=1;return;}
    b->length+=(size_t)n;
}
static void buf_close(Buffer *b){*b->size=b->failed?FRAME_MAX+1:b->length;free(b);}

typedef struct { double deadline; Quota *quota; unsigned calls; unsigned jobs; int interrupted; int unhandled; JSValue reason; const char *token; } State;
static double now_ms(void) {
#ifdef _WIN32
    LARGE_INTEGER value,frequency;QueryPerformanceCounter(&value);QueryPerformanceFrequency(&frequency);
    return (double)value.QuadPart*1000.0/(double)frequency.QuadPart;
#else
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000.0+t.tv_nsec/1000000.0;
#endif
}
static int interrupt(JSContext *ctx, void *opaque) { (void)ctx; State *s=opaque; if(s->quota->failed)return 1; if(now_ms()>=s->deadline) { s->interrupted=1; return 1; } return 0; }
static void quote(Buffer *out, const char *text, size_t n) {
    buf_putc('"',out);
    for(size_t i=0;i<n;i++) { unsigned char c=(unsigned char)text[i];
        if(c=='"'||c=='\\') { buf_putc('\\',out); buf_putc(c,out); }
        else if(c<32) buf_printf(out,"\\u%04x",c); else buf_putc(c,out);
    } buf_putc('"',out);
}
static int send_frame(const char *text, size_t n) {
    if(n>FRAME_MAX) return -1;
    unsigned char h[4]={(unsigned char)(n>>24),(unsigned char)(n>>16),(unsigned char)(n>>8),(unsigned char)n};
    if(fwrite(h,1,4,stdout)!=4 || fwrite(text,1,n,stdout)!=n) return -1;
    return fflush(stdout);
}
static char *read_frame(size_t *n) {
    unsigned char h[4]; if(fread(h,1,4,stdin)!=4) return NULL;
    *n=((size_t)h[0]<<24)|((size_t)h[1]<<16)|((size_t)h[2]<<8)|h[3];
    if(*n>FRAME_MAX) return NULL;
    char *p=malloc(*n+1); if(!p) return NULL;
    if(fread(p,1,*n,stdin)!=*n) {free(p);return NULL;} p[*n]=0;return p;
}
static void error_frame(JSContext *ctx, State *s, const char *category) {
    JSValue error=JS_GetException(ctx);
    /* This quota failure flag is outside JavaScript and cannot be caught/reset. */
    const char *message=NULL;int allocated=0;
    if(s->quota->failed)message="JavaScript memory allocation limit exceeded";
    else {message=JS_ToCString(ctx,error);allocated=message!=NULL;}
    if(!message)message="Exception details unavailable; JavaScript memory limit may be exhausted";
    char *out=NULL; size_t len=0; Buffer *stream=new_buffer(&out,&len);
    buf_printf(stream,"{\"kind\":\"error\",\"category\":");quote(stream,category,strlen(category));
    buf_puts(",\"message\":",stream);quote(stream,message?message:"exception unavailable",message?strlen(message):21);
    buf_printf(stream,",\"interrupted\":%s,\"jobs\":%u,\"calls\":%u}",s->interrupted?"true":"false",s->jobs,s->calls);
    buf_close(stream); send_frame(out,len);free(out);
    if(allocated) {JS_FreeCString(ctx,message);}
    JS_FreeValue(ctx,error);
}
static JSValue bridge(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv) {
    (void)this_val; State *s=JS_GetContextOpaque(ctx);
    if(argc!=1 || !JS_IsString(argv[0])) return JS_ThrowTypeError(ctx,"Bridge accepts one typed data string");
    if(interrupt(ctx,s)) return JS_ThrowInternalError(ctx,"operation cancelled before host dispatch");
    if(++s->calls>64) return JS_ThrowInternalError(ctx,"bridge request limit");
    size_t size; const char *payload=JS_ToCStringLen(ctx,&size,argv[0]);
    if(!payload) return JS_EXCEPTION;
    if(size>262144) { JS_FreeCString(ctx,payload);return JS_ThrowRangeError(ctx,"bridge payload limit"); }
    char *out=NULL;size_t n=0;Buffer *stream=new_buffer(&out,&n);
    buf_puts("{\"kind\":\"bridge\",\"operation\":",stream);quote(stream,s->token,64);buf_printf(stream,",\"request\":%u,\"payload\":",s->calls);quote(stream,payload,size);buf_putc('}',stream);buf_close(stream);
    JS_FreeCString(ctx,payload);
    int status=send_frame(out,n);free(out);if(status) return JS_ThrowInternalError(ctx,"bridge write failed");
    // This blocking host boundary is intentionally covered by the *parent*
    // hard watchdog too: VM interruption cannot interrupt arbitrary host I/O.
    char *reply=read_frame(&n);if(!reply)return JS_ThrowInternalError(ctx,"bridge response unavailable");
    if(interrupt(ctx,s)) {free(reply);return JS_ThrowInternalError(ctx,"operation cancelled after host dispatch");}
    JSValue result=JS_NewStringLen(ctx,reply,n);free(reply);return result;
}
static void rejected(JSContext *ctx,JSValueConst promise,JSValueConst reason,bool handled,void *opaque) {
    (void)promise;State *s=opaque;
    if(handled) {if(s->unhandled>0)s->unhandled--;return;}
    s->unhandled++;JS_FreeValue(ctx,s->reason);s->reason=JS_DupValue(ctx,reason);
}
static int configure_io(void) {
#ifdef _WIN32
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX);
    if(_setmode(_fileno(stdin),_O_BINARY)<0||_setmode(_fileno(stdout),_O_BINARY)<0)return -1;
#else
    struct rlimit core_limit={0,0};setrlimit(RLIMIT_CORE,&core_limit);
#endif
    return 0;
}
int main(int argc,char **argv) {
    if(configure_io())return 74;
    /* Explicit native smoke mode tests all byte values and CRT pipe framing;
       it evaluates no JavaScript and has no database or filesystem capability. */
    if(argc==3 && strcmp(argv[1],"--probe-framing")==0) {
        unsigned count=(unsigned)strtoul(argv[2],NULL,10);
        if(!count||count>16)return 64;
        for(unsigned index=0;index<count;index++) {
            size_t length=0;char *data=read_frame(&length);
            if(!data)return 65;
            int failed=send_frame(data,length);free(data);
            if(failed)return 74;
        }
        return 0;
    }
    if(argc!=6 || strlen(argv[5])!=64) return 64;
    for(unsigned i=0;i<64;i++)if(!((argv[5][i]>='0'&&argv[5][i]<='9')||(argv[5][i]>='a'&&argv[5][i]<='f')))return 64;
    double timeout_ms=strtod(argv[1],NULL);size_t heap=(size_t)strtoull(argv[2],NULL,10);size_t stack=(size_t)strtoull(argv[3],NULL,10);unsigned max_jobs=(unsigned)strtoul(argv[4],NULL,10);
    if(timeout_ms<=0||heap<1024||stack<16384||!max_jobs)return 64;
    size_t bootstrap_len,source_len;char *bootstrap=read_frame(&bootstrap_len);char *source=read_frame(&source_len);
    if(!bootstrap||!source) {free(bootstrap);free(source);return 65;}
    Quota quota={.limit=heap,.token=argv[5]};
    JSRuntime *rt=JS_NewRuntime2(&quota_functions,&quota);if(!rt)return 70;
    /* The public custom allocator enforces the total quota including headers;
       disabling the separate internal limit ensures every failure is recorded. */
    JS_SetMemoryLimit(rt,0);JS_SetMaxStackSize(rt,stack);JS_SetCanBlock(rt,false);
    JSContext *ctx=JS_NewContext(rt);if(!ctx) {JS_FreeRuntime(rt);free(bootstrap);free(source);return 71;}
    State s={.deadline=now_ms()+timeout_ms,.quota=&quota,.reason=JS_UNDEFINED,.token=argv[5]};JS_SetContextOpaque(ctx,&s);
    JS_SetInterruptHandler(rt,interrupt,&s);JS_SetHostPromiseRejectionTracker(rt,rejected,&s);
    JSValue global=JS_GetGlobalObject(ctx);JS_SetPropertyStr(ctx,global,"__native",JS_NewCFunction(ctx,bridge,"nativeTypedBridge",1));JS_FreeValue(ctx,global);
    JSValue render=JS_Eval(ctx,bootstrap,bootstrap_len,"<trusted-bootstrap>",JS_EVAL_TYPE_GLOBAL);free(bootstrap);
    if(JS_IsException(render)) {free(source);error_frame(ctx,&s,"bootstrap");JS_SetHostPromiseRejectionTracker(rt,NULL,NULL);JS_FreeValue(ctx,s.reason);JS_FreeContext(ctx);JS_FreeRuntime(rt);return 1;}
    s.deadline=now_ms()+timeout_ms;double start=now_ms();
    JSValue result=JS_Eval(ctx,source,source_len,"<owned-source>",JS_EVAL_TYPE_GLOBAL);free(source);
    int failed=JS_IsException(result)||quota.failed;
    if(failed)error_frame(ctx,&s,"evaluate");
    while(!failed && JS_IsJobPending(rt)) {
        if(++s.jobs>max_jobs) {JS_ThrowInternalError(ctx,"pending job limit");error_frame(ctx,&s,"job_limit");failed=1;break;}
        JSContext *jobctx=NULL;
        if(JS_ExecutePendingJob(rt,&jobctx)<0) {error_frame(jobctx?jobctx:ctx,&s,"pending_job");failed=1;break;}
        if(quota.failed){error_frame(ctx,&s,"memory_limit");failed=1;break;}
    }
    if(!failed && s.unhandled) {JS_Throw(ctx,JS_DupValue(ctx,s.reason));error_frame(ctx,&s,"unhandled_rejection");failed=1;}
    if(!failed && JS_IsPromise(result)) {
        JSPromiseStateEnum status=JS_PromiseState(ctx,result);
        if(status==JS_PROMISE_PENDING) {JS_ThrowInternalError(ctx,"Promise is still pending without host events");error_frame(ctx,&s,"pending_promise");failed=1;}
        else {JSValue value=JS_PromiseResult(ctx,result);JS_FreeValue(ctx,result);result=value;
            if(status==JS_PROMISE_REJECTED){JS_Throw(ctx,JS_DupValue(ctx,result));error_frame(ctx,&s,"promise_rejected");failed=1;}}
    }
    if(!failed) {
        JSValue rendered=JS_Call(ctx,render,JS_UNDEFINED,1,&result);
        if(JS_IsException(rendered)||quota.failed){error_frame(ctx,&s,"render");failed=1;}
        else {
            size_t n;const char *text=JS_ToCStringLen(ctx,&n,rendered);
            if(!text||n>262144){JS_ThrowRangeError(ctx,"result output limit");error_frame(ctx,&s,"output_limit");failed=1;}
            else {JSMemoryUsage usage;JS_ComputeMemoryUsage(rt,&usage);char *out=NULL;size_t size=0;Buffer *stream=new_buffer(&out,&size);
                buf_puts("{\"kind\":\"result\",\"tree\":",stream);buf_write(text,n,stream);
                buf_printf(stream,",\"elapsed_ms\":%.3f,\"jobs\":%u,\"calls\":%u,\"js_memory_used\":%lld,\"native\":{\"pointer_bits\":%u",now_ms()-start,s.jobs,s.calls,(long long)usage.memory_used_size,(unsigned)(sizeof(void*)*8));
#ifdef _WIN32
                buf_printf(stream,",\"windows_no_console\":%s",GetConsoleWindow()==NULL?"true":"false");
#endif
                buf_puts("}}",stream);
                buf_close(stream);send_frame(out,size);free(out);}
            if(text)JS_FreeCString(ctx,text);
        }JS_FreeValue(ctx,rendered);
    }
    JS_SetHostPromiseRejectionTracker(rt,NULL,NULL);
    JS_FreeValue(ctx,result);JS_FreeValue(ctx,render);JS_FreeValue(ctx,s.reason);JS_FreeContext(ctx);JS_FreeRuntime(rt);
    return failed?1:0;
}
