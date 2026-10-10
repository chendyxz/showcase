"""Run the production Bluetooth handler against fragmented handshake traffic."""
from pathlib import Path
import subprocess
import tempfile

source = (Path(__file__).resolve().parents[1] / "source/carplay_bt.m").read_text()
state = source[source.index("/* ── State tracking"):source.index("/* ═", source.index("/* ── State tracking"))]
helpers = source[source.index("static uint8_t ck("):source.index("static void rfcomm_send_chunked(")]
receive = ""
if "static void receive_iap2(" in source:
    receive = source[source.index("static void handle_iap2_packet("):source.index("static void handler(")]
handler = source[source.index("static void handler("):source.index("int main(")]
harness = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <stdarg.h>
#include <assert.h>
#include <arpa/inet.h>
typedef uint8_t bd_addr_t[6];
#define R16(b,p) (((uint16_t)(b)[(p)+1])<<8|(b)[(p)])
#define BT_READY_PATH "/tmp/unused-showcase-test-ready"
#define BT_ID_PATH "/tmp/unused-showcase-test-id"
#define BT_PROGRESS_CONNECTED 1
static uint16_t active_cid = 7, rfcomm_mtu;
static int iap2_detected, link_established, detect_count;
static uint8_t my_initial_psn, my_next_psn, peer_psn;
static int bluetooth_handoff_requested, have_local_addr, setup_done;
static uint8_t local_bd_addr[6];
static uint8_t iap2_detect[] = {0xFF,0x55,0x02,0x00,0xEE,0x10};
static int messages, acks, syns, commands;
static uint16_t message_ids[16];
static int message_lengths[16];
static uint8_t command_addr[6];
static const void *hci_accept_connection_request, *hci_io_capability_request_reply;
static const void *hci_user_confirmation_request_reply, *rfcomm_accept_connection;
static int bt_send_cmd(const void *cmd, ...) {
    (void)cmd;
    va_list args; va_start(args, cmd);
    memcpy(command_addr, va_arg(args, uint8_t *), 6);
    va_end(args); commands++; return 0;
}
static void send_ack(void) { acks++; }
static void send_syn(void) { syns++; }
static void send_detect(void) {}
static void alarm_handler(int sig) { (void)sig; }
static void bt_progress(uint64_t bits) { (void)bits; }
static void perform_bt_setup(void) {}
static void handle_ctrl_msg(uint16_t id, uint8_t *params, int len) {
    assert(messages < 16 && len >= 0);
    for (int i = 0; i < len; i++) assert(params[i] == 0xA5);
    message_ids[messages] = id; message_lengths[messages++] = len;
}
''' + state + helpers + receive + handler + r'''
static int message(uint8_t *p, uint16_t id, int params) {
    int n = 6 + params;
    p[0]=p[1]=0x40; p[2]=n>>8; p[3]=n; p[4]=id>>8; p[5]=id;
    memset(p+6, 0xA5, params); return n;
}
static int frame(uint8_t *p, uint8_t seq, const uint8_t *body, int n) {
    int total = n ? n+10 : 9;
    p[0]=0xFF; p[1]=0x5A; p[2]=total>>8; p[3]=total;
    p[4]=0x40; p[5]=seq; p[6]=0; p[7]=n ? 1 : 0; p[8]=ck(p,8);
    if(n) { memcpy(p+9,body,n); p[9+n]=ck(p+9,n); }
    return total;
}
static void start_link(void) {
    reset_wireless_handoff_state();
    active_cid=7; messages=acks=syns=0; iap2_detected=1; link_established=1; peer_psn=10;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    uint8_t p[4096], body[2048];
    if (!strcmp(argv[1],"address")) {
        const uint8_t events[] = {0x04,0x31,0x33};
        const uint8_t expected[] = {0x90,0xB9,0x31,0xAC,0x86,0xA0};
        for (unsigned i=0;i<sizeof(events);i++) {
            uint8_t event[12]={events[i],10,0xA0,0x86,0xAC,0x31,0xB9,0x90};
            handler(4,0,event,sizeof(event));
#ifdef SHOWCASE_ROOTLESS
            assert(commands == 0);
#else
            assert(commands == (int)i+1);
            assert(!memcmp(command_addr,expected,6));
#endif
        }
    } else if (!strcmp(argv[1],"detect")) {
        iap2_detected=0;
        handler(7,active_cid,iap2_detect,2);
        handler(7,active_cid,iap2_detect+2,4);
        assert(iap2_detected && syns == 1);
    } else if (!strcmp(argv[1],"split")) {
        int n=frame(p,11,body,message(body,0xAA02,1000));
        for(int split=1;split<n;split++) {
            start_link();
            handler(7,active_cid,p,split);
            assert(messages == 0 && acks == 0);
            handler(7,active_cid,p+split,n-split);
            assert(messages == 1 && acks == 1 && message_lengths[0] == 1000);
        }
    } else if (!strcmp(argv[1],"combined")) {
        start_link();
        int n=frame(p,11,body,message(body,0xAA05,0));
        n+=frame(p+n,12,body,message(body,0x5702,0));
        handler(7,active_cid,p,n);
        assert(messages == 2 && message_ids[1] == 0x5702);
    } else if (!strcmp(argv[1],"control")) {
        start_link();
        int len=message(body,0xAA02,1000);
        int n=frame(p,11,body,400);
        handler(7,active_cid,p,n); assert(messages == 0);
        n=frame(p,12,body+400,len-400);
        handler(7,active_cid,p,n);
        assert(messages == 1 && message_lengths[0] == 1000);
        len=message(body,0xAA05,0);
        len+=message(body+len,0x5702,0);
        n=frame(p,13,body,len); handler(7,active_cid,p,n);
        assert(messages == 3 && message_ids[2] == 0x5702);
        handler(7,active_cid,p,n); assert(messages == 3 && acks == 4);
    } else if (!strcmp(argv[1],"invalid")) {
        start_link();
        int n=frame(p,11,body,message(body,0x5702,0));
        p[n-1]++; handler(7,active_cid,p,n);
        assert(messages == 0 && acks == 0);
        p[n-1]--; p[8]++; handler(7,active_cid,p,n);
        assert(messages == 0 && acks == 0);
        p[8]--; handler(7,active_cid,p,n); assert(messages == 1);
    } else if (!strcmp(argv[1],"reconnect")) {
        start_link();
        int n=frame(p,11,body,message(body,0x5702,0));
        handler(7,active_cid,p,8);
        uint8_t closed[]={0x81,2,7,0}; handler(4,0,closed,sizeof(closed));
        active_cid=7; iap2_detected=1; link_established=1; peer_psn=10;
        handler(7,active_cid,p,n); assert(messages == 1);
    } else if (!strcmp(argv[1],"channel")) {
        start_link();
        int n=frame(p,11,body,message(body,0x5702,0));
        handler(7,active_cid+1,p,n); assert(messages == 0);
    } else if (!strcmp(argv[1],"handshake")) {
        start_link(); link_established=0; my_initial_psn=20;
        int n=frame(p,10,NULL,0);
        p[4]=0xC0; p[6]=my_initial_psn; p[8]=ck(p,8);
        n+=frame(p+n,11,body,message(body,0x1D00,0));
        handler(7,active_cid,p,n);
        assert(link_established && messages == 1 && acks == 2);
    } else if (!strcmp(argv[1],"sequence")) {
        start_link();
        int n=frame(p,200,NULL,0); handler(7,active_cid,p,n);
        assert(peer_psn == 10);
        n=frame(p,12,body,message(body,0x5702,0));
        handler(7,active_cid,p,n); assert(messages == 0 && peer_psn == 10);
        n=frame(p,11,body,message(body,0x5702,0));
        handler(7,active_cid,p,n); assert(messages == 1);
        peer_psn=255;
        n=frame(p,0,body,message(body,0x5702,0));
        handler(7,active_cid,p,n); assert(messages == 2 && peer_psn == 0);
    } else if (!strcmp(argv[1],"limit")) {
        start_link();
        uint8_t large[UINT16_MAX], packet[UINT16_MAX];
        int len=message(large,0xAA02,UINT16_MAX-6);
        int n=frame(packet,11,large,32000); handler(7,active_cid,packet,n);
        assert(messages == 0);
        n=frame(packet,12,large+32000,len-32000); handler(7,active_cid,packet,n);
        assert(messages == 1 && message_lengths[0] == UINT16_MAX-6);
        start_link();
        len=message(large,0xAA02,UINT16_MAX-16);
        n=frame(packet,11,large,len); assert(n == UINT16_MAX);
        handler(7,active_cid,packet,n); assert(messages == 1);
    } else if (!strcmp(argv[1],"control_reset")) {
        start_link();
        int n=frame(p,11,body,message(body,0xAA02,1000)-400);
        handler(7,active_cid,p,n); assert(messages == 0);
        uint8_t closed[]={0x81,2,7,0}; handler(4,0,closed,sizeof(closed));
        active_cid=7; iap2_detected=1; link_established=1; peer_psn=10;
        n=frame(p,11,body,message(body,0x5702,0));
        handler(7,active_cid,p,n); assert(messages == 1 && message_ids[0] == 0x5702);
    } else { assert(0); }
    return 0;
}
'''
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory)
    (path / "check.c").write_text(harness)
    for variant in ([], ["-DSHOWCASE_ROOTLESS"]):
        subprocess.run(["clang", "-fsanitize=address,undefined", "-g", *variant,
                        str(path / "check.c"), "-o", str(path / "check")], check=True)
        for case in ("address", "detect", "split", "combined", "control", "invalid", "reconnect",
                     "channel", "handshake", "sequence", "limit", "control_reset"):
            subprocess.run([str(path / "check"), case], check=True, stdout=subprocess.DEVNULL)
print("PASS: rootful/rootless pairing addresses, fragmented and combined iAP2, checksums, reconnect")
