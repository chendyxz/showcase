"""A failed or closed first control connection must not strand Wi-Fi handoff."""
from pathlib import Path
import subprocess
import tempfile

source = (Path(__file__).resolve().parents[1] / "source/carplay_services.m").read_text()
function = source[source.index("static void ctrl_connect_thread("):source.index("/* ═", source.index("static void ctrl_connect_thread("))]
connect = source[source.index("static void ctrl_send_connect("):source.index("static void ctrl_connect_thread(")]
harness = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdlib.h>
#include <assert.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#define CTRL_RESOLVE_ROUNDS 10
#define CTRL_CONNECT_ATTEMPTS 3
#define CTRL_RETRY_SEC 2
#define SOURCE_VERSION "509.0"
#define DEVICE_ID_INT "1234"
static uint32_t g_ctrlIfIndex = 9;
static bool g_ctrlConnected;
static int resolutions, sends, closes;
typedef struct { char host[256]; uint16_t port; bool resolved; } ResolveResult;
static bool resolve_ctrl_port(ResolveResult *r) {
    resolutions++;
    assert(resolutions < 100); /* detect busy or stuck loops */
    snprintf(r->host,sizeof(r->host),"phone.local");
    r->port = 1000 + resolutions; r->resolved = true; return true;
}
static int try_connect(const char *h,uint16_t p,uint32_t i,bool v,int t) {
    (void)h; (void)i; (void)v; (void)t;
    assert(p == 1000 + resolutions);
    if (resolutions <= 15) { errno=ECONNREFUSED; return -1; }
    return 42;
}
static ssize_t mock_send(int s,const void *p,size_t n,int flags) {
    (void)p; (void)flags; assert(s == 42);
    if (++sends == 1) { errno=EPIPE; return -1; }
    return n;
}
static ssize_t mock_recv(int s,void *p,size_t n,int flags) {
    (void)p; (void)n; (void)flags; assert(s == 42); return 0;
}
static int mock_close(int s) { assert(s == 42); assert(++closes == sends); return 0; }
static int mock_setsockopt(int s,int l,int o,const void *p,socklen_t n) {
    (void)s; (void)l; (void)o; (void)p; (void)n; return 0;
}
static unsigned int mock_sleep(unsigned int seconds) {
    assert(seconds > 0);
    if (sends == 2) { assert(closes == 2 && !g_ctrlConnected); exit(0); }
    return 0;
}
#define send mock_send
#define recv mock_recv
#define close mock_close
#define setsockopt mock_setsockopt
#define sleep mock_sleep
''' + connect + function + r'''
int main(void) { ctrl_connect_thread(NULL); return 1; }
'''
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory)
    (path / "check.c").write_text(harness)
    subprocess.run(["clang", str(path / "check.c"), "-o", str(path / "check")], check=True)
    subprocess.run([str(path / "check")], check=True, stdout=subprocess.DEVNULL)
print("PASS: control retry after stale ports and a closed initial connection")
