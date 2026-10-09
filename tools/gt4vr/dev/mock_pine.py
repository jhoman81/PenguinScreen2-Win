#!/usr/bin/env python3
"""Mock PenguinScreen2 PINE server with a toy 'GT4' running in it, for testing gt4cam.py
without a PS2 (Linux: unix socket).

    MOCK_EMULOG=/tmp/mock_emulog.txt python3 mock_pine.py /tmp/mockpine.sock /tmp/mockq &
    python3 ../gt4cam.py --socket /tmp/mockpine.sock info

It speaks PINE plus PenguinScreen2's VR extensions (0xE0 qhist, 0xE1/0xE2 memory watch), runs a
fake camera at 60 Hz, emulates hook caves on GT4's sway calls (0x0037B1B4/BC/C4) in "bumper view",
and a nop run at 0x004956A0..0x0049573F for exec-probe (logged to MOCK_EMULOG). Switch views or
hold L1 through the control socket:
    python3 -c "import socket; socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM).sendto(b'view=1', '/tmp/mockpine.sock.ctl')"
"""
import json, math, os, socket, struct, sys, threading, time

RAM = bytearray(0x02000000)
LOCK = threading.Lock()
STATE = {"l1": False, "yaw": 0.0, "view": 0, "frame": 0}
WATCHES = {}   # slot -> (start, end, cond)
HITS = {}      # (pc, watch, is_write) -> row dict
QDIR = sys.argv[2] if len(sys.argv) > 2 else "/tmp"
EMULOG = os.environ.get("MOCK_EMULOG")
LOGGED = {}

LOOK = 0x00812340        # look-back yaw: 0, or pi while L1 held
PERFRAME = 0x00812350    # value the game rewrites every frame
MATRIX = 0x00900000      # camera rotation matrix (16-aligned)
VEC_CHASE = 0x00A00000   # offset vectors per view
VEC_BUMPER = 0x00A00010
SITE = 0x0037B304


def w32(a, v): struct.pack_into("<I", RAM, a, v & 0xFFFFFFFF)
def r32(a): return struct.unpack_from("<I", RAM, a)[0]
def wf(a, v): struct.pack_into("<f", RAM, a, v)


def setup():
    for i, w in enumerate([0xC60C0000, 0xC60D0004, 0x0C129E11, 0xC60E0008]):
        w32(SITE + 4 * i, w)
    func = [0x27BDFFC0, 0xAFBF0030, 0xC62C0030, 0x0C100000, 0x00000000, 0xC62C0030,
            0x0C100040, 0x00000000, 0xE6400000, 0x8FBF0030, 0x03E00008, 0x27BD0040]
    for i, w in enumerate(func):
        w32(0x00300100 + 4 * i, w)
    # a leaf helper with no prologue: stores and returns
    for i, w in enumerate([0xAC800000, 0x03E00008, 0x00000000]):
        w32(0x00310000 + 4 * i, w)
    # GT4 sway block: jal RotZ / RotX / RotY with their delay slots
    for i, w in enumerate([0x0C129E76, 0x46000306, 0x0C129E62, 0x4600AB06, 0x0C129E6C, 0x4600A306]):
        w32(0x0037B1B4 + 4 * i, w)
    struct.pack_into("<3f", RAM, VEC_CHASE, 0.0, 1.2, -6.0)
    struct.pack_into("<3f", RAM, VEC_BUMPER, 0.0, 0.55, 1.9)
    # junk floats so searches have something to reject
    for i in range(0, 0x4000, 4):
        wf(0x00C00000 + i, math.sin(i) * 3)


def record(pc, op, addr, is_write, gpr):
    for slot, (s, e, cond) in WATCHES.items():
        if s <= addr < e and (cond & (2 if is_write else 1)):
            k = (pc, slot, is_write)
            if k in HITS:
                HITS[k]["hits"] += 1
            else:
                HITS[k] = {"pc": pc, "op": op, "hits": 1, "frame": STATE["frame"], "watch": slot,
                           "is_write": is_write, "gpr": gpr}


def game_loop():
    while True:
        with LOCK:
            STATE["frame"] += 1
            STATE["yaw"] += 0.01
            wf(LOOK, math.pi if STATE["l1"] else 0.0)
            wf(PERFRAME, 1.0)
            y = STATE["yaw"]
            m = [math.cos(y), 0, math.sin(y), 0, 0, 1, 0, 0, -math.sin(y), 0, math.cos(y), 0, 10, 2, 30, 1]
            struct.pack_into("<16f", RAM, MATRIX, *m)
            gpr = [0] * 32
            gpr[17] = 0x00812300
            gpr[18] = MATRIX
            gpr[4] = MATRIX
            gpr[29] = 0x01FFF000
            gpr[31] = 0x00300114        # returns just after the jal at 0x0030010C
            record(0x00310000, 0xAC800000, MATRIX, True, gpr)
            # camera placement site: emulate a patched `j cave` storing s0
            w = r32(SITE)
            s0 = VEC_CHASE if STATE["view"] == 0 else VEC_BUMPER
            if (w >> 26) == 2:
                cave = (w & 0x03FFFFFF) << 2
                lui, sw = r32(cave), r32(cave + 4)
                hi = lui & 0xFFFF
                lo = sw & 0xFFFF
                lo = lo - 0x10000 if lo & 0x8000 else lo
                w32(((hi << 16) + lo) & 0x1FFFFFF, s0)
            # sway block only runs in the bumper view (view 1); emulate any hook caves on it
            if STATE["view"] == 1:
                for k, site in enumerate((0x0037B1B4, 0x0037B1BC, 0x0037B1C4)):
                    w = r32(site)
                    tgt = (w & 0x03FFFFFF) << 2
                    added = 0.0
                    if (w >> 26) == 3 and (r32(tgt) >> 16) == 0x3C01:
                        hi = r32(tgt) & 0xFFFF
                        def sx(v): return v - 0x10000 if v & 0x8000 else v
                        sc = ((hi << 16) + sx(r32(tgt + 4) & 0xFFFF)) & 0x1FFFFFF
                        added = struct.unpack_from("<f", RAM, sc)[0]
                        d = r32(tgt + 16)
                        if (d >> 26) == 0x2B and ((d >> 16) & 31) == 31:
                            w32(((hi << 16) + sx(d & 0xFFFF)) & 0x1FFFFFF, site + 8)
                    wf(0x00812400 + 4 * k, added)
            # GT4 runs a stretch of zero words (nops) at 0x004956A0..0x0049573F every frame in a race;
            # the recompiler logs unknown opcodes when it compiles them (once per change)
            if EMULOG:
                for a in range(0x004956A0, 0x00495740, 4):
                    w = r32(a)
                    if (w >> 26) == 0x30 and LOGGED.get(a) != w:
                        LOGGED[a] = w
                        with open(EMULOG, "a") as f:
                            f.write("[%10.4f] Unknown R5900 Standard: %08X\n[%10.4f] EE: Unrecognized op %x\n"
                                    % (time.time() % 1000, w, time.time() % 1000, w))
            gpr2 = [0] * 32
            gpr2[16] = s0
            record(SITE, 0xC60C0000, s0, False, gpr2)
        time.sleep(1 / 60)


def qhist_json():
    bpo, lo, n = 6, -6, 96
    cov = [0.0] * n
    for i in range(n):
        w = 2 ** (lo + (i + 0.5) / bpo)
        cov[i] = math.exp(-((math.log2(w) - 4.0) ** 2) / 4.0) * 0.02  # mode near w=16
    return {"binning": {"bins_per_octave": bpo, "count": n, "log2w_max": 10, "log2w_min": lo},
            "bins": {"coverage": cov, "density": [0] * n, "dom_coverage": [0] * n, "prims": [1] * n,
                     "q_moment": [0] * n, "verts": [3] * n},
            "census": {"accurate_stq_flagged": 0, "displaced": 5200, "fst_excluded": 800, "mono_centre": 0,
                       "stereo_off": 0, "total": 6000, "uniform_q_pinned": 0, "wide_q_displaced": 2100},
            "key": {"crc": "0x77e61c8a", "dump": "", "frame": STATE["frame"], "serial": "SCUS-97328",
                    "unscaled_h": 448, "unscaled_w": 640, "widescreen_hack": False},
            "overflow": {"far": {"coverage": 0.01, "prims": 1, "q_moment": 0, "verts": 3},
                         "near": {"coverage": 0, "prims": 0, "q_moment": 0, "verts": 0},
                         "non_finite": {"coverage": 0, "prims": 0, "q_moment": 0, "verts": 0}},
            "schema": 3,
            "summary": {"coverage_per_prim": 0.001, "modes": [16.0], "octave_span_p05_p95": 5.4,
                        "total_coverage": 0.9, "valley_depth": 0,
                        "w_percentiles": {"p01": 1.2, "p05": 2.3, "p10": 3.4, "p25": 7.0, "p50": 16.0,
                                          "p75": 37.0, "p90": 70.0, "p95": 98.0, "p99": 200.0}},
            "tool": "qhist"}


def handle(buf):
    out = bytearray()
    i = 0
    while i < len(buf):
        op = buf[i]; i += 1
        if op <= 3:
            a = struct.unpack_from("<I", buf, i)[0] & 0x1FFFFFF; i += 4
            n = 1 << op
            out += RAM[a:a + n]
        elif 4 <= op <= 7:
            n = 1 << (op - 4)
            a = struct.unpack_from("<I", buf, i)[0] & 0x1FFFFFF; i += 4
            RAM[a:a + n] = buf[i:i + n]; i += n
        elif op in (0x08, 0x0B, 0x0C, 0x0D, 0x0E):
            s = {0x08: "PCSX2 v2.mock", 0x0B: "Gran Turismo 4", 0x0C: "SCUS-97328",
                 0x0D: "77e61c8a", 0x0E: "1.00"}[op].encode() + b"\0"
            out += struct.pack("<I", len(s)) + s
        elif op == 0x0F:
            out += struct.pack("<I", 1)
        elif op == 0xE0:
            p = os.path.join(QDIR, "qhist-live-SCUS-97328-001-f%d.json" % STATE["frame"])
            with open(p, "w") as f:
                json.dump(qhist_json(), f)
            s = p.encode() + b"\0"
            out += struct.pack("<I", len(s)) + s
        elif op == 0xE1:
            a, size, cond, stop = struct.unpack_from("<IBBB", buf, i); i += 7
            if size == 0:
                WATCHES.clear(); HITS.clear(); out += b"\xff"
            else:
                slot = next(k for k in range(8) if k not in WATCHES)
                WATCHES[slot] = (a, a + size, cond)
                out += bytes([slot])
        elif op == 0xE2:
            rows = list(HITS.values()); HITS.clear()
            payload = struct.pack("<II", len(rows), 0)
            for r in rows:
                payload += struct.pack("<IIIIBBHI32Q", r["pc"], r["op"], r["hits"], r["frame"], r["watch"],
                                       1 if r["is_write"] else 0, 0, 0, *r["gpr"])
            out += struct.pack("<I", len(payload)) + payload
        else:
            return struct.pack("<IB", 5, 0xFF)
    return struct.pack("<IB", len(out) + 5, 0) + bytes(out)


def serve(path):
    if os.path.exists(path):
        os.unlink(path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path); srv.listen(4)
    ctl = path + ".ctl"   # tiny control channel for the test harness
    def ctl_loop():
        if os.path.exists(ctl):
            os.unlink(ctl)
        c = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); c.bind(ctl)
        while True:
            msg = c.recv(64).decode()
            k, v = msg.split("=")
            with LOCK:
                STATE[k] = (v == "1") if k == "l1" else int(v)
    threading.Thread(target=ctl_loop, daemon=True).start()
    while True:
        conn, _ = srv.accept()
        def client(conn):
            try:
                while True:
                    hdr = conn.recv(4, socket.MSG_WAITALL)
                    if len(hdr) < 4:
                        return
                    n = struct.unpack("<I", hdr)[0]
                    body = b""
                    while len(body) < n - 4:
                        body += conn.recv(n - 4 - len(body))
                    with LOCK:
                        resp = handle(body)
                    conn.sendall(resp)
            finally:
                conn.close()
        threading.Thread(target=client, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    setup()
    threading.Thread(target=game_loop, daemon=True).start()
    serve(sys.argv[1])
