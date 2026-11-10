#define _POSIX_C_SOURCE 200809L
#include "quickjs.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <signal.h>
#include <sys/resource.h>

#define FRAME_MAX (1024u * 1024u)
static volatile sig_atomic_t cancelled = 0;
typedef struct { double deadline; unsigned calls; unsigned jobs; int interrupted; int unhandled; JSValue reason; const char *token; } State;
static double now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec*1000.0+t.tv_nsec/1000000.0; }
static void on_cancel(int sig) { (void)sig; cancelled=1; }
static int interrupt(JSContext *ctx, void *opaque) { (void)ctx; State *s=opaque; if(cancelled || now_ms()>=s->deadline) { s->interrupted=1; return 1; } return 0; }
static void quote(FILE *out, const char *text, size_t n) {
    fputc('"',out);
    for(size_t i=0;i<n;i++) { unsigned char c=(unsigned char)text[i];
        if(c=='"'||c=='\\') { fputc('\\',out); fputc(c,out); }
        else if(c<32) fprintf(out,"\\u%04x",c); else fputc(c,out);
    } fputc('"',out);
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
    JSValue error=JS_GetException(ctx); const char *message=JS_ToCString(ctx,error);
    char *out=NULL; size_t len=0; FILE *stream=open_memstream(&out,&len);
    fprintf(stream,"{\"kind\":\"error\",\"category\":");quote(stream,category,strlen(category));
    fputs(",\"message\":",stream);quote(stream,message?message:"exception unavailable",message?strlen(message):21);
    fprintf(stream,",\"interrupted\":%s,\"jobs\":%u,\"calls\":%u}",s->interrupted?"true":"false",s->jobs,s->calls);
    fclose(stream); send_frame(out,len);free(out);
    if(message)JS_FreeCString(ctx,message); JS_FreeValue(ctx,error);
}
static JSValue bridge(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv) {
    (void)this_val; State *s=JS_GetContextOpaque(ctx);
    if(argc!=1 || !JS_IsString(argv[0])) return JS_ThrowTypeError(ctx,"Bridge accepts one typed data string");
    if(interrupt(ctx,s)) return JS_ThrowInternalError(ctx,"operation cancelled before host dispatch");
    if(++s->calls>64) return JS_ThrowInternalError(ctx,"bridge request limit");
    size_t size; const char *payload=JS_ToCStringLen(ctx,&size,argv[0]);
    if(!payload) return JS_EXCEPTION;
    if(size>262144) { JS_FreeCString(ctx,payload);return JS_ThrowRangeError(ctx,"bridge payload limit"); }
    char *out=NULL;size_t n=0;FILE *stream=open_memstream(&out,&n);
    fputs("{\"kind\":\"bridge\",\"operation\":",stream);quote(stream,s->token,64);fprintf(stream,",\"request\":%u,\"payload\":",s->calls);quote(stream,payload,size);fputc('}',stream);fclose(stream);
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
int main(int argc,char **argv) {
    if(argc!=6 || strlen(argv[5])!=64) return 64;
    double timeout_ms=strtod(argv[1],NULL);size_t heap=(size_t)strtoull(argv[2],NULL,10);size_t stack=(size_t)strtoull(argv[3],NULL,10);unsigned max_jobs=(unsigned)strtoul(argv[4],NULL,10);
    if(timeout_ms<=0||heap<1024||stack<16384||!max_jobs)return 64;
    struct rlimit core_limit={0,0};setrlimit(RLIMIT_CORE,&core_limit);
    signal(SIGUSR1,on_cancel);signal(SIGINT,on_cancel);
    size_t bootstrap_len,source_len;char *bootstrap=read_frame(&bootstrap_len);char *source=read_frame(&source_len);
    if(!bootstrap||!source) {free(bootstrap);free(source);return 65;}
    JSRuntime *rt=JS_NewRuntime();if(!rt)return 70;
    JS_SetMemoryLimit(rt,heap);JS_SetMaxStackSize(rt,stack);JS_SetCanBlock(rt,false);
    JSContext *ctx=JS_NewContext(rt);if(!ctx) {JS_FreeRuntime(rt);free(bootstrap);free(source);return 71;}
    State s={.deadline=now_ms()+timeout_ms,.reason=JS_UNDEFINED,.token=argv[5]};JS_SetContextOpaque(ctx,&s);
    JS_SetInterruptHandler(rt,interrupt,&s);JS_SetHostPromiseRejectionTracker(rt,rejected,&s);
    JSValue global=JS_GetGlobalObject(ctx);JS_SetPropertyStr(ctx,global,"__native",JS_NewCFunction(ctx,bridge,"nativeTypedBridge",1));JS_FreeValue(ctx,global);
    JSValue render=JS_Eval(ctx,bootstrap,bootstrap_len,"<trusted-bootstrap>",JS_EVAL_TYPE_GLOBAL);free(bootstrap);
    if(JS_IsException(render)) {free(source);error_frame(ctx,&s,"bootstrap");JS_SetHostPromiseRejectionTracker(rt,NULL,NULL);JS_FreeValue(ctx,s.reason);JS_FreeContext(ctx);JS_FreeRuntime(rt);return 1;}
    s.deadline=now_ms()+timeout_ms;double start=now_ms();
    JSValue result=JS_Eval(ctx,source,source_len,"<owned-source>",JS_EVAL_TYPE_GLOBAL);free(source);
    int failed=JS_IsException(result);
    if(failed)error_frame(ctx,&s,"evaluate");
    while(!failed && JS_IsJobPending(rt)) {
        if(++s.jobs>max_jobs) {JS_ThrowInternalError(ctx,"pending job limit");error_frame(ctx,&s,"job_limit");failed=1;break;}
        JSContext *jobctx=NULL;
        if(JS_ExecutePendingJob(rt,&jobctx)<0) {error_frame(jobctx?jobctx:ctx,&s,"pending_job");failed=1;break;}
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
        if(JS_IsException(rendered)){error_frame(ctx,&s,"render");failed=1;}
        else {
            size_t n;const char *text=JS_ToCStringLen(ctx,&n,rendered);
            if(!text||n>262144){JS_ThrowRangeError(ctx,"result output limit");error_frame(ctx,&s,"output_limit");failed=1;}
            else {JSMemoryUsage usage;JS_ComputeMemoryUsage(rt,&usage);char *out=NULL;size_t size=0;FILE *stream=open_memstream(&out,&size);
                fputs("{\"kind\":\"result\",\"tree\":",stream);fwrite(text,1,n,stream);
                fprintf(stream,",\"elapsed_ms\":%.3f,\"jobs\":%u,\"calls\":%u,\"js_memory_used\":%lld}",now_ms()-start,s.jobs,s.calls,(long long)usage.memory_used_size);
                fclose(stream);send_frame(out,size);free(out);}
            if(text)JS_FreeCString(ctx,text);
        }JS_FreeValue(ctx,rendered);
    }
    JS_SetHostPromiseRejectionTracker(rt,NULL,NULL);
    JS_FreeValue(ctx,result);JS_FreeValue(ctx,render);JS_FreeValue(ctx,s.reason);JS_FreeContext(ctx);JS_FreeRuntime(rt);
    return failed?1:0;
}
