#!/usr/bin/env python3
"""
gt4cam.py - reverse-engineering helper for PenguinScreen2 VR profiles.

Built around Gran Turismo 4 (SCUS-97328, CRC 77E61C8A) but the generic
commands work on any PS2 game. It talks to the emulator over PINE, plus
PenguinScreen2's VR extensions to it:
    0xE0  flush the stereo depth histogram (needs --qhist-live <dir>)
    0xE1  arm / clear a memory watch (records PC, opcode and all GPRs)
    0xE2  drain memory-watch hits

Enable PINE first: Settings > Advanced > PINE, or in PCSX2.ini
    [EmuCore]
    EnablePINE = true

Pure standard library, Python 3.8+. Run `gt4cam.py -h` or
`gt4cam.py <command> -h`. README.md has the GT4 walkthrough.
"""

import argparse
import array
import json
import math
import os
import re
import socket
import struct
import sys
import time

# --------------------------------------------------------------------------
# PINE client
# --------------------------------------------------------------------------

OP_READ8, OP_READ16, OP_READ32, OP_READ64 = 0x00, 0x01, 0x02, 0x03
OP_WRITE8, OP_WRITE16, OP_WRITE32, OP_WRITE64 = 0x04, 0x05, 0x06, 0x07
OP_VERSION, OP_TITLE, OP_ID, OP_UUID, OP_GAMEVER, OP_STATUS = 0x08, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F
OP_VR_QHIST, OP_VR_MEMWATCH, OP_VR_MEMWATCH_POLL = 0xE0, 0xE1, 0xE2

PINE_DEFAULT_SLOT = 28011
MAIN_RAM = 0x02000000
# The server caps a reply at 450,000 bytes; 8 bytes per Read64.
READS_PER_BATCH = 50000

MEMWATCH_ROW = struct.Struct("<IIIIBBHI32Q")  # 280 bytes, see VR/MemWatch.h
assert MEMWATCH_ROW.size == 280


class PineError(RuntimeError):
    pass


def default_socket_path(slot):
    if sys.platform == "darwin":
        base = os.environ.get("TMPDIR") or "/tmp"
    else:
        base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    path = os.path.join(base, "pcsx2.sock")
    if slot != PINE_DEFAULT_SLOT:
        path += ".%d" % slot
    return path


class Pine:
    def __init__(self, socket_path=None, slot=PINE_DEFAULT_SLOT, tcp=None, timeout=20.0):
        if tcp is None and sys.platform == "win32":
            tcp = "127.0.0.1:%d" % slot
        try:
            if tcp:
                host, port = tcp.rsplit(":", 1)
                self.sock = socket.create_connection((host, int(port)), timeout=timeout)
                self.where = tcp
            else:
                path = socket_path or default_socket_path(slot)
                self.where = path
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(timeout)
                s.connect(path)
                self.sock = s
        except OSError as e:
            if tcp:
                hint = (
                    "  - Enable PINE: Tools > Show Advanced Settings, then Settings > Advanced >\n"
                    "    PINE Settings > Enable (slot 28011). Close and reopen the emulator after\n"
                    "    changing it, and don't edit the .ini while the emulator is open: it\n"
                    "    saves its own copy of the settings over your edit.\n"
                    "  - The emulator log should say 'PINE: listening on 127.0.0.1:28011' (newer\n"
                    "    builds), and `netstat -ano | findstr 28011` should show LISTENING.\n"
                    "  - RetroAchievements hardcore mode turns PINE off.")
            else:
                hint = (
                    "  - Is PINE enabled (Settings > Advanced > PINE) and the emulator open?\n"
                    "  - Flatpak: the socket lives inside the sandbox; run this script via\n"
                    "    `flatpak enter <instance> python3 ...` (see README.md).")
            raise PineError("could not reach PINE at %s (%s).\n%s"
                            % (socket_path or tcp or default_socket_path(slot), e, hint))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _recv(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise PineError("PINE connection closed by the emulator")
            buf += chunk
        return bytes(buf)

    def call(self, payload):
        self.sock.sendall(struct.pack("<I", len(payload) + 4) + payload)
        (size,) = struct.unpack("<I", self._recv(4))
        body = self._recv(size - 4)
        if not body or body[0] != 0:
            raise PineError("PINE command refused (no game running, or see the emulator log)")
        return body[1:]

    # --- memory ---------------------------------------------------------
    def read_block(self, start, length, progress=False):
        a0 = start & ~7
        a1 = (start + length + 7) & ~7
        out = bytearray()
        addr = a0
        total = a1 - a0
        last_pct = -1
        while addr < a1:
            n = min(READS_PER_BATCH, (a1 - addr) // 8)
            req = bytearray(5 * n)
            for i in range(n):
                struct.pack_into("<BI", req, 5 * i, OP_READ64, addr + 8 * i)
            out += self.call(bytes(req))
            addr += 8 * n
            if progress:
                pct = int(100 * (addr - a0) / total)
                if pct != last_pct and pct % 10 == 0:
                    sys.stderr.write("\r  reading RAM... %3d%%" % pct)
                    sys.stderr.flush()
                    last_pct = pct
        if progress:
            sys.stderr.write("\r  reading RAM... done \n")
        off = start - a0
        return bytes(out[off:off + length])

    def read32(self, addr):
        return struct.unpack("<I", self.call(struct.pack("<BI", OP_READ32, addr)))[0]

    def read_words(self, addr, count):
        raw = self.read_block(addr, 4 * count)
        return list(struct.unpack("<%dI" % count, raw))

    def write32(self, addr, value):
        self.call(struct.pack("<BII", OP_WRITE32, addr, value & 0xFFFFFFFF))

    def write16(self, addr, value):
        self.call(struct.pack("<BIH", OP_WRITE16, addr, value & 0xFFFF))

    def write8(self, addr, value):
        self.call(struct.pack("<BIB", OP_WRITE8, addr, value & 0xFF))

    def write_batch(self, items):
        """items: [(addr, size, raw_int)] written in one round trip."""
        req = bytearray()
        for addr, size, raw in items:
            if size == 4:
                req += struct.pack("<BII", OP_WRITE32, addr, raw & 0xFFFFFFFF)
            elif size == 2:
                req += struct.pack("<BIH", OP_WRITE16, addr, raw & 0xFFFF)
            else:
                req += struct.pack("<BIB", OP_WRITE8, addr, raw & 0xFF)
        if req:
            self.call(bytes(req))

    # --- strings / status -------------------------------------------------
    def _string(self, op):
        data = self.call(bytes([op]))
        (size,) = struct.unpack_from("<I", data, 0)
        return data[4:4 + size].split(b"\0", 1)[0].decode("utf-8", "replace")

    def version(self):
        return self._string(OP_VERSION)

    def title(self):
        return self._string(OP_TITLE)

    def serial(self):
        return self._string(OP_ID)

    def crc(self):
        return int(self._string(OP_UUID), 16)

    def game_version(self):
        return self._string(OP_GAMEVER)

    def status(self):
        (s,) = struct.unpack("<I", self.call(bytes([OP_STATUS])))
        return {0: "running", 1: "paused", 2: "shutdown"}.get(s, str(s))

    # --- PenguinScreen2 VR extensions -------------------------------------
    def memwatch_arm(self, addr, size, cond, stop=False):
        """cond: 1 read, 2 write, 3 both. Returns the watch slot."""
        data = self.call(struct.pack("<BIBBB", OP_VR_MEMWATCH, addr, size, cond, 1 if stop else 0))
        return data[0]

    def memwatch_clear(self):
        self.call(struct.pack("<BIBBB", OP_VR_MEMWATCH, 0, 0, 0, 0))

    def memwatch_poll(self):
        data = self.call(bytes([OP_VR_MEMWATCH_POLL]))
        (size,) = struct.unpack_from("<I", data, 0)
        payload = data[4:4 + size]
        count, dropped = struct.unpack_from("<II", payload, 0)
        rows = []
        for i in range(count):
            f = MEMWATCH_ROW.unpack_from(payload, 8 + i * MEMWATCH_ROW.size)
            rows.append({"pc": f[0], "op": f[1], "hits": f[2], "frame": f[3],
                         "watch": f[4], "is_write": bool(f[5]), "gpr": list(f[8:40])})
        return rows, dropped

    def qhist_flush(self):
        data = self.call(bytes([OP_VR_QHIST]))
        (size,) = struct.unpack_from("<I", data, 0)
        return data[4:4 + size].split(b"\0", 1)[0].decode("utf-8", "replace")


# --------------------------------------------------------------------------
# R5900 disassembler (the subset that matters for camera code)
# --------------------------------------------------------------------------

GPR = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
       "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"]


def sext16(v):
    return v - 0x10000 if v & 0x8000 else v


def hexoff(v):
    return ("-0x%x" % -v) if v < 0 else ("0x%x" % v)


SPECIAL = {0: "sll", 2: "srl", 3: "sra", 4: "sllv", 6: "srlv", 7: "srav", 8: "jr", 9: "jalr", 10: "movz",
           11: "movn", 12: "syscall", 13: "break", 15: "sync", 16: "mfhi", 17: "mthi", 18: "mflo", 19: "mtlo",
           20: "dsllv", 22: "dsrlv", 23: "dsrav", 24: "mult", 25: "multu", 26: "div", 27: "divu", 32: "add",
           33: "addu", 34: "sub", 35: "subu", 36: "and", 37: "or", 38: "xor", 39: "nor", 40: "mfsa", 41: "mtsa",
           42: "slt", 43: "sltu", 44: "dadd", 45: "daddu", 46: "dsub", 47: "dsubu", 56: "dsll", 58: "dsrl",
           59: "dsra", 60: "dsll32", 62: "dsrl32", 63: "dsra32"}
IMM_ALU = {8: "addi", 9: "addiu", 10: "slti", 11: "sltiu", 12: "andi", 13: "ori", 14: "xori", 24: "daddi",
           25: "daddiu"}
BRANCH2 = {4: "beq", 5: "bne", 20: "beql", 21: "bnel"}
BRANCH1 = {6: "blez", 7: "bgtz", 22: "blezl", 23: "bgtzl"}
REGIMM = {0: "bltz", 1: "bgez", 2: "bltzl", 3: "bgezl", 16: "bltzal", 17: "bgezal"}
LOADS = {26: "ldl", 27: "ldr", 30: "lq", 32: "lb", 33: "lh", 34: "lwl", 35: "lw", 36: "lbu", 37: "lhu",
         38: "lwr", 39: "lwu", 55: "ld"}
STORES = {31: "sq", 40: "sb", 41: "sh", 42: "swl", 43: "sw", 44: "sdl", 45: "sdr", 46: "swr", 63: "sd"}
COP1_S = {0: "add.s", 1: "sub.s", 2: "mul.s", 3: "div.s", 4: "sqrt.s", 5: "abs.s", 6: "mov.s", 7: "neg.s",
          22: "rsqrt.s", 24: "adda.s", 25: "suba.s", 26: "mula.s", 28: "madd.s", 29: "msub.s",
          30: "madda.s", 31: "msuba.s", 36: "cvt.w.s", 40: "max.s", 41: "min.s", 48: "c.f.s",
          50: "c.eq.s", 52: "c.lt.s", 54: "c.le.s"}


class Insn:
    __slots__ = ("pc", "word", "text", "kind", "target", "fpr_out", "base", "offset", "rt")

    def __init__(self, pc, word):
        self.pc = pc
        self.word = word
        self.kind = "other"
        self.target = None
        self.fpr_out = None
        self.base = None
        self.offset = None
        self.rt = None
        self.text = self._decode()

    def _decode(self):
        w, pc = self.word, self.pc
        if w == 0:
            self.kind = "nop"
            return "nop"
        op = w >> 26
        rs, rt, rd = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
        sa, fn = (w >> 6) & 31, w & 63
        imm = w & 0xFFFF
        simm = sext16(imm)
        R = GPR
        if op == 0:
            name = SPECIAL.get(fn, "special.%d" % fn)
            if fn in (0, 2, 3, 56, 58, 59, 60, 62, 63):
                return "%-8s $%s, $%s, %d" % (name, R[rd], R[rt], sa)
            if fn == 8:
                self.kind = "jr"
                return "%-8s $%s" % (name, R[rs])
            if fn == 9:
                self.kind = "jalr"
                return "%-8s $%s, $%s" % (name, R[rd], R[rs])
            if fn in (12, 13, 15):
                return name
            if fn in (16, 18, 40):
                return "%-8s $%s" % (name, R[rd])
            if fn in (17, 19, 41):
                return "%-8s $%s" % (name, R[rs])
            if fn in (24, 25, 26, 27):
                return "%-8s $%s, $%s" % (name, R[rs], R[rt])
            return "%-8s $%s, $%s, $%s" % (name, R[rd], R[rs], R[rt])
        if op == 1:
            self.kind = "branch"
            self.target = (pc + 4 + (simm << 2)) & 0xFFFFFFFF
            return "%-8s $%s, 0x%08x" % (REGIMM.get(rt, "regimm.%d" % rt), R[rs], self.target)
        if op in (2, 3):
            self.target = ((pc + 4) & 0xF0000000) | ((w & 0x03FFFFFF) << 2)
            self.kind = "jal" if op == 3 else "j"
            return "%-8s 0x%08x" % ("jal" if op == 3 else "j", self.target)
        if op in BRANCH2:
            self.kind = "branch"
            self.target = (pc + 4 + (simm << 2)) & 0xFFFFFFFF
            if op == 4 and rs == 0 and rt == 0:
                return "%-8s 0x%08x" % ("b", self.target)
            return "%-8s $%s, $%s, 0x%08x" % (BRANCH2[op], R[rs], R[rt], self.target)
        if op in BRANCH1:
            self.kind = "branch"
            self.target = (pc + 4 + (simm << 2)) & 0xFFFFFFFF
            return "%-8s $%s, 0x%08x" % (BRANCH1[op], R[rs], self.target)
        if op in IMM_ALU:
            if op in (9, 25) and rs == 29 and rt == 29 and simm < 0:
                self.kind = "prologue"
            if op in (12, 13, 14):
                return "%-8s $%s, $%s, 0x%x" % (IMM_ALU[op], R[rt], R[rs], imm)
            return "%-8s $%s, $%s, %d" % (IMM_ALU[op], R[rt], R[rs], simm)
        if op == 15:
            return "%-8s $%s, 0x%04x" % ("lui", R[rt], imm)
        if op in LOADS or op in STORES:
            self.kind = "load" if op in LOADS else "store"
            self.base, self.offset, self.rt = rs, simm, rt
            return "%-8s $%s, %s($%s)" % (LOADS.get(op) or STORES.get(op), R[rt], hexoff(simm), R[rs])
        if op in (49, 57):  # lwc1 / swc1
            self.kind = "fload" if op == 49 else "fstore"
            self.base, self.offset, self.rt = rs, simm, rt
            if op == 49:
                self.fpr_out = rt
            return "%-8s $f%d, %s($%s)" % ("lwc1" if op == 49 else "swc1", rt, hexoff(simm), R[rs])
        if op in (54, 62):  # lqc2 / sqc2
            self.kind = "vload" if op == 54 else "vstore"
            self.base, self.offset, self.rt = rs, simm, rt
            return "%-8s $vf%d, %s($%s)" % ("lqc2" if op == 54 else "sqc2", rt, hexoff(simm), R[rs])
        if op == 17:
            fmt_ = rs
            ft, fs, fd = rt, rd, sa
            if fmt_ == 0:
                return "%-8s $%s, $f%d" % ("mfc1", R[rt], fs)
            if fmt_ == 4:
                self.kind = "fpu"
                self.fpr_out = fs
                return "%-8s $%s, $f%d" % ("mtc1", R[rt], fs)
            if fmt_ in (2, 6):
                return "%-8s $%s, $%d" % ("cfc1" if fmt_ == 2 else "ctc1", R[rt], fs)
            if fmt_ == 8:
                self.kind = "branch"
                self.target = (pc + 4 + (simm << 2)) & 0xFFFFFFFF
                return "%-8s 0x%08x" % (["bc1f", "bc1t", "bc1fl", "bc1tl"][rt & 3], self.target)
            if fmt_ == 16:
                name = COP1_S.get(fn, "cop1.s.%d" % fn)
                self.kind = "fpu"
                if fn in (0, 1, 2, 3, 22, 28, 29, 40, 41):
                    self.fpr_out = fd
                    return "%-8s $f%d, $f%d, $f%d" % (name, fd, fs, ft)
                if fn == 4:
                    self.fpr_out = fd
                    return "%-8s $f%d, $f%d" % (name, fd, ft)
                if fn in (5, 6, 7, 36):
                    self.fpr_out = fd
                    return "%-8s $f%d, $f%d" % (name, fd, fs)
                return "%-8s $f%d, $f%d" % (name, fs, ft)
            if fmt_ == 20 and fn == 32:
                self.kind = "fpu"
                self.fpr_out = fd
                return "%-8s $f%d, $f%d" % ("cvt.s.w", fd, fs)
            return "cop1     0x%08x" % w
        if op == 18:
            if rs == 1:
                return "%-8s $%s, $vf%d" % ("qmfc2", R[rt], rd)
            if rs == 5:
                return "%-8s $%s, $vf%d" % ("qmtc2", R[rt], rd)
            if rs in (2, 6):
                return "%-8s $%s, $vi%d" % ("cfc2" if rs == 2 else "ctc2", R[rt], rd)
            return "cop2     0x%08x  (VU0 macro op)" % w
        if op == 16:
            return "cop0     0x%08x" % w
        if op == 28:
            return "mmi      0x%08x" % w
        if op == 47:
            return "cache    0x%x, %s($%s)" % (rt, hexoff(simm), R[rs])
        return "op%02d     0x%08x" % (op, w)


def disasm_lines(pine, addr, count, mark=None):
    words = pine.read_words(addr, count)
    lines = []
    for i, w in enumerate(words):
        pc = addr + 4 * i
        ins = Insn(pc, w)
        tag = "=>" if pc == mark else "  "
        lines.append("%s %08x: %08x  %s" % (tag, pc, w, ins.text))
    return lines


# --------------------------------------------------------------------------
# Function analysis: find JAL sites that pass floats - code-hook candidates
# --------------------------------------------------------------------------

def find_function(pine, pc, back=1024, fwd=1024):
    start_scan = max(0x00100000, pc - 4 * back)
    nback = (pc - start_scan) // 4
    words = pine.read_words(start_scan, nback + fwd)
    insns = [Insn(start_scan + 4 * i, w) for i, w in enumerate(words)]
    here = nback
    start = None
    for i in range(here, -1, -1):
        if insns[i].kind == "prologue":
            start = i
            break
    end = None
    for i in range(here, len(insns) - 1):
        if insns[i].kind == "jr" and (insns[i].word >> 21) & 31 == 31:
            end = i + 1  # include delay slot
            break
    if start is None:
        start = max(0, here - 64)
    if end is None:
        end = min(len(insns) - 1, here + 64)
    return insns[start:end + 1], insns[here]


def float_args_for_call(func, k, window=6):
    """For the JAL at func[k], return {fpr: source_text} for f12..f15 set in
    the few instructions before it or in its delay slot."""
    args = {}
    lo = max(0, k - window)
    seq = func[lo:k] + ([func[k + 1]] if k + 1 < len(func) else [])
    for ins in seq:
        if ins.fpr_out is not None and 12 <= ins.fpr_out <= 15:
            args[ins.fpr_out] = " ".join(ins.text.split())
    return args


def suggest_hooks(func):
    out = []
    for k, ins in enumerate(func):
        if ins.kind != "jal":
            continue
        args = float_args_for_call(func, k)
        if args:
            out.append((ins, args))
    # same operand fed to two different callees: the sin/cos-of-one-angle pattern
    by_src = {}
    for ins, args in out:
        for fpr, src in args.items():
            if src.startswith("lwc1"):
                by_src.setdefault(src, set()).add(ins.target)
    pairs = {src for src, targets in by_src.items() if len(targets) >= 2}
    return out, pairs


def print_hook_report(func, here, label="watched access"):
    print("  function 0x%08x..0x%08x (%d instructions; %s at 0x%08x)"
          % (func[0].pc, func[-1].pc, len(func), label, here.pc))
    calls, pairs = suggest_hooks(func)
    if not calls:
        print("  no JALs passing floats in f12..f15 here - look one level up (the caller).")
        return
    print("  calls that pass floats (code-hook candidates):")
    for ins, args in calls:
        argtxt = "; ".join("$f%d <- %s" % (r, s) for r, s in sorted(args.items()))
        flag = "  [same operand as another call: sin/cos pair?]" if any(s in pairs for s in args.values()) else ""
        print("    0x%08x  jal 0x%08x   %s%s" % (ins.pc, ins.target, argtxt, flag))
    first_src = next(iter(calls[0][1].values()))
    group = [c for c in calls if first_src in c[1].values()] if first_src in pairs else calls[:1]
    print("\n  YAML (verify by testing before trusting it)%s:"
          % (" - both calls take the same angle, so hook both or the basis shears" if len(group) > 1 else ""))
    print("    codeHooks:")
    for n, (ins, args) in enumerate(group):
        fpr = sorted(args)[0]
        print("      - { enabled: true, hookAddress: 0x%08X, tailJump: 0x%08X, caveAddress: 0x%08X,"
              % (ins.pc, ins.target, GT4_CAVE_DEFAULT + 0x20 * n))
        print("          scratchAddress: 0x%08X, source: head_yaw, scale: 1.0, axisSign: 1, targetFpr: %d }"
              % (GT4_CAVE_DEFAULT + 0x60 + 4 * n, fpr))
    print("    (scale 57.29578 if the callee takes degrees; caves must pass `cave-check` first)")


# --------------------------------------------------------------------------
# Snapshots and value search
# --------------------------------------------------------------------------

ENC = {"f32": ("f", 4), "s32": ("i", 4), "u32": ("I", 4), "s16": ("h", 2), "u16": ("H", 2),
       "s8": ("b", 1), "u8": ("B", 1)}


def session_dir(args):
    d = args.session
    os.makedirs(os.path.join(d, "snaps"), exist_ok=True)
    os.makedirs(os.path.join(d, "cands"), exist_ok=True)
    return d


def snap_paths(d, name):
    return os.path.join(d, "snaps", name + ".bin"), os.path.join(d, "snaps", name + ".json")


def load_snap(d, name):
    binp, meta = snap_paths(d, name)
    if not os.path.exists(binp):
        raise SystemExit("no snapshot named '%s' in %s (take one with `snap %s`)" % (name, d, name))
    with open(meta) as f:
        m = json.load(f)
    with open(binp, "rb") as f:
        return m, f.read()


def as_array(raw, enc):
    code, size = ENC[enc]
    n = len(raw) // size
    a = array.array(code)
    a.frombytes(raw[:n * size])
    if sys.byteorder != "little":
        a.byteswap()
    return a


def decode_at(raw, start, addr, enc):
    code, size = ENC[enc]
    return struct.unpack_from("<" + code, raw, addr - start)[0]


def near(x, target, tol=1e-3):
    return isinstance(x, (int, float)) and math.isfinite(x) and abs(x - target) <= tol


EXPR_ENV = {"near": near, "pi": math.pi, "abs": abs, "deg": math.degrees, "rad": math.radians,
            "isfinite": math.isfinite, "min": min, "max": max,
            "ang16": lambda v: v * (2 * math.pi / 65536.0)}

PRESETS = {
    # rest = L1 released, back = L1 held (rear view)
    "lookback": [
        ("f32", "near(rest, 0, 1e-4) and (near(abs(back), pi, 0.02) or near(abs(back), 180, 0.5))"),
        ("s16", "rest == 0 and back == -32768"),
        ("u16", "rest == 0 and back == 0x8000"),
        ("s32", "rest == 0 and back in (0x8000, -0x8000)"),
    ],
}


def cand_path(d, name):
    return os.path.join(d, "cands", name + ".json")


def save_cands(d, name, entries):
    with open(cand_path(d, name), "w") as f:
        json.dump({"entries": entries}, f)


def load_cands(d, name):
    p = cand_path(d, name)
    if not os.path.exists(p):
        raise SystemExit("no candidate set '%s' in %s" % (name, d))
    with open(p) as f:
        return [tuple(e) for e in json.load(f)["entries"]]


def run_expr(expr, names, snaps, enc, within=None, start=None):
    fn = eval("lambda %s: %s" % (", ".join(names), expr), dict(EXPR_ENV))
    arrays = [as_array(raw, enc) for raw in snaps]
    size = ENC[enc][1]
    hits = []
    if within is not None:
        for addr in within:
            i = (addr - start) // size
            if 0 <= i < len(arrays[0]):
                try:
                    if fn(*[a[i] for a in arrays]):
                        hits.append(addr)
                except (ValueError, OverflowError, ZeroDivisionError):
                    pass
        return hits
    n = len(arrays[0])
    step = 1 << 20
    for base in range(0, n, step):
        cols = [a[base:base + step] for a in arrays]
        for j, vals in enumerate(zip(*cols)):
            try:
                if fn(*vals):
                    hits.append(start + (base + j) * size)
            except (ValueError, OverflowError, ZeroDivisionError):
                pass
        sys.stderr.write("\r  scanning %s... %3d%%" % (enc, min(100, int(100 * (base + step) / n))))
        sys.stderr.flush()
    sys.stderr.write("\r  scanning %s... done \n" % enc)
    return hits


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def parse_int(s):
    return int(str(s), 0)


def split_list(values):
    """Accept `a,b`, `a b` or `"a,b"`. PowerShell turns an unquoted a,b into an
    array, which reaches us as separate arguments."""
    out = []
    for v in values or []:
        out += [x.strip() for x in str(v).split(",") if x.strip()]
    return out


def connect(args):
    return Pine(socket_path=args.socket, slot=args.slot, tcp=args.tcp)


GT4_RETAIL = ("SCUS-97328", 0x77E61C8A)
# Camera-placement path, as reconstructed by the community "GT5 Camera
# Placement" patch: lwc1 f12,0(s0); lwc1 f13,4(s0); jal 0x004A7844; lwc1 f14,8(s0)
GT4_OFFSET_SITE = 0x0037B304
GT4_OFFSET_WORDS = [0xC60C0000, 0xC60D0004, 0x0C129E11, 0xC60E0008]
# Free memory for code-hook caves. Below the ELF (which loads at 0x00100000); the block
# 0x000F1100..0x000F11FF holds the profile's three head-look caves (+0x00/+0x20/+0x40), their scratch
# words (+0x60..+0x6B), the seat offset (+0x70..+0x7B), the seat/position cave (+0x80..+0xB7) and
# head position (+0xC0..+0xCB); +0xD0..+0xF3 is for hooktest's and trace-offset's temporary caves.
# (Not 0x00495600+: all zero and never read or written, but GT4 executes it - nops - in races.)
GT4_CAVE_DEFAULT = 0x000F1100
GT4_CAVE_LEN = 0x100


def cmd_info(args):
    p = connect(args)
    print("PINE      %s" % p.where)
    print("emulator  %s (%s)" % (p.version(), p.status()))
    serial, crc = p.serial(), p.crc()
    print("game      %s  serial %s  CRC %08X  version %s" % (p.title(), serial, crc, p.game_version()))
    if (serial, crc) == GT4_RETAIL:
        words = p.read_words(GT4_OFFSET_SITE, 4)
        ok = words == GT4_OFFSET_WORDS
        print("GT4 retail: camera-offset site 0x%08x %s" % (GT4_OFFSET_SITE, "matches" if ok else "DIFFERS (patched?)"))
        cave = p.read_block(GT4_CAVE_DEFAULT, GT4_CAVE_LEN)
        nz = sum(1 for b in cave if b)
        print("GT4 retail: cave 0x%08x..+0x%x %s" % (GT4_CAVE_DEFAULT, GT4_CAVE_LEN,
              "is all zero" if nz == 0 else "has %d non-zero bytes (fine if the VR head camera is armed and "
              "these are its hooks - `disasm 0x%08x --count 64` shows; otherwise pick another cave)" % (nz, GT4_CAVE_DEFAULT)))
    elif serial.startswith("SCUS-97436"):
        print("Online Public Beta / Spec II build: addresses differ from retail; generic commands only.")
    try:
        p.memwatch_poll()
        print("VR extensions: memory watch available")
    except PineError:
        print("VR extensions: memory watch NOT available (stock PCSX2, not PenguinScreen2?)")
    p.close()


def cmd_snap(args):
    d = session_dir(args)
    p = connect(args)
    start, end = parse_int(args.start), parse_int(args.end)
    status = p.status()
    if status != "paused":
        print("note: emulator is %s; pause it first for a consistent snapshot." % status)
    raw = p.read_block(start, end - start, progress=True)
    binp, meta = snap_paths(d, args.name)
    with open(binp, "wb") as f:
        f.write(raw)
    with open(meta, "w") as f:
        json.dump({"start": start, "end": end, "time": time.time(), "serial": p.serial(),
                   "crc": "%08X" % p.crc()}, f)
    print("saved %s (0x%08x..0x%08x, %.1f MB)" % (args.name, start, end, len(raw) / 1e6))
    p.close()


def cmd_find(args):
    d = session_dir(args)
    names = split_list(args.snaps)
    if args.expr is None:
        stray = [n for n in names if not n.isidentifier()]
        if len(stray) == 1:
            args.expr = stray[0]
            names.remove(stray[0])
    for n in names:
        if not n.isidentifier():
            raise SystemExit("snapshot name '%s' must be a Python identifier to use in an expression" % n)
    loaded = [load_snap(d, n) for n in names]
    metas = [m for m, _ in loaded]
    raws = [r for _, r in loaded]
    start = metas[0]["start"]
    if any(m["start"] != start or m["end"] != metas[0]["end"] for m in metas):
        raise SystemExit("snapshots cover different ranges; take them with the same --start/--end")
    if args.preset:
        if args.preset not in PRESETS:
            raise SystemExit("unknown preset; have: %s" % ", ".join(PRESETS))
        if len(names) != 2:
            raise SystemExit("preset '%s' wants exactly two snapshots (e.g. --snaps rest,back)" % args.preset)
        rename = {"rest": names[0], "back": names[1]}
        jobs = [(enc, re.sub(r"\b(rest|back)\b", lambda mt: rename[mt.group(1)], expr))
                for enc, expr in PRESETS[args.preset]]
    else:
        if not args.expr:
            raise SystemExit("give an expression or --preset")
        jobs = [(args.enc, args.expr)]
    within = load_cands(d, args.within) if args.within else None
    entries = []
    for enc, expr in jobs:
        if within is not None:
            addrs = [a for a, e in within if e == enc]
            if not addrs:
                continue
            hits = run_expr(expr, names, raws, enc, within=addrs, start=start)
        else:
            hits = run_expr(expr, names, raws, enc, start=start)
        entries += [(a, enc) for a in hits]
    entries.sort()
    print("%d candidate(s)" % len(entries))
    for addr, enc in entries[:args.show]:
        vals = ", ".join("%s=%s" % (n, fmt_val(decode_at(r, start, addr, enc), enc)) for n, r in zip(names, raws))
        print("  0x%08x %-4s %s" % (addr, enc, vals))
    if len(entries) > args.show:
        print("  ... (%d more)" % (len(entries) - args.show))
    if args.save:
        save_cands(d, args.save, entries)
        print("saved candidate set '%s'" % args.save)


def fmt_val(v, enc):
    if enc == "f32":
        return "%.6g" % v
    if enc in ("u32",):
        return "0x%08x" % v
    return str(v)


def cmd_cands(args):
    d = session_dir(args)
    entries = load_cands(d, args.name)
    p = connect(args)
    print("%d candidate(s) in '%s', live values:" % (len(entries), args.name))
    for addr, enc in entries[:args.show]:
        print("  0x%08x %-4s %s" % (addr, enc, fmt_val(read_val(p, addr, enc), enc)))
    p.close()


def read_val(p, addr, enc):
    code, size = ENC[enc]
    raw = p.read_block(addr, size)
    return struct.unpack("<" + code, raw)[0]


def write_val(p, addr, value, enc):
    code, size = ENC[enc]
    raw = struct.pack("<" + code, value)
    if size == 4:
        p.write32(addr, struct.unpack("<I", raw)[0])
    elif size == 2:
        p.write16(addr, struct.unpack("<H", raw)[0])
    else:
        p.write8(addr, raw[0])


def cmd_peek(args):
    p = connect(args)
    addr = parse_int(args.addr)
    size = ENC[args.enc][1]
    period = 1.0 / args.hz
    try:
        while True:
            vals = [fmt_val(read_val(p, addr + i * size, args.enc), args.enc) for i in range(args.n)]
            line = "0x%08x %s: %s" % (addr, args.enc, "  ".join(vals))
            if not args.live:
                print(line)
                break
            sys.stdout.write("\r" + line + "    ")
            sys.stdout.flush()
            time.sleep(period)
    except KeyboardInterrupt:
        print()
    p.close()


def cmd_poke(args):
    p = connect(args)
    addr = parse_int(args.addr)
    value = float(args.value) if args.enc == "f32" else parse_int(args.value)
    original = read_val(p, addr, args.enc)
    code, size = ENC[args.enc]
    want = struct.pack("<" + code, value)
    if args.hold <= 0:
        write_val(p, addr, value, args.enc)
        time.sleep(0.25)
        after = read_val(p, addr, args.enc)
        print("wrote %s (was %s); 250 ms later it reads %s"
              % (fmt_val(value, args.enc), fmt_val(original, args.enc), fmt_val(after, args.enc)))
        if struct.pack("<" + code, after) != want:
            print("-> the game rewrote it. Absolute/anchored writes at vsync will fight the game;"
                  " use a code hook on whatever computes it.")
        else:
            print("-> it stuck. A camera `writes` op can drive this address.")
        p.close()
        return
    period = 1.0 / args.hz
    writes = reverts = 0
    t_end = time.time() + args.hold
    try:
        while time.time() < t_end:
            if writes:
                cur = p.read_block(addr, size)
                if cur != want:
                    reverts += 1
            write_val(p, addr, value, args.enc)
            writes += 1
            time.sleep(period)
    except KeyboardInterrupt:
        pass
    finally:
        if not args.keep:
            write_val(p, addr, original, args.enc)
    print("held %s for %d writes; the game overwrote it before %d of them (%.0f%%)%s"
          % (fmt_val(value, args.enc), writes, reverts, 100.0 * reverts / max(1, writes - 1),
             "" if args.keep else "; original %s restored" % fmt_val(original, args.enc)))
    p.close()


def raw_of(value, enc):
    code, size = ENC[enc]
    return size, struct.unpack("<" + {4: "I", 2: "H", 1: "B"}[size], struct.pack("<" + code, value))[0]


def hold_group(p, group, seconds, hz):
    """Keep test values written on every (addr, enc, value) in group, then put
    back what was there before."""
    originals = [(a, ENC[e][1], raw_of(read_val(p, a, e), e)[1]) for a, e, _ in group]
    writes = [(a,) + raw_of(v, e) for a, e, v in group]
    t_end = time.time() + seconds
    try:
        while time.time() < t_end:
            p.write_batch(writes)
            time.sleep(1.0 / hz)
    finally:
        p.write_batch(originals)


def ask(prompt):
    while True:
        a = input(prompt).strip().lower()
        if a in ("y", "n", "q"):
            return a


def cmd_bisect(args):
    d = session_dir(args)
    entries = load_cands(d, args.name)
    if args.enc:
        entries = [e for e in entries if e[1] == args.enc]
    if not entries:
        raise SystemExit("no candidates%s in '%s'" % (" of type " + args.enc if args.enc else "", args.name))
    meta, back_raw = load_snap(d, args.back)
    start = meta["start"]

    def test_value(addr, enc):
        b = decode_at(back_raw, start, addr, enc)  # its look-back value, i.e. 180 degrees
        v = b / 6.0                                # -> 30 degrees in the same units
        return v if enc == "f32" else int(round(v))

    p = connect(args)
    remaining = list(entries)
    print("%d candidate(s). Each round holds a 30-degree test value on half of them for %.0f s." % (len(remaining), args.seconds))
    print("Drive (or sit) in bumper view, don't touch L1, and watch whether the view turns.")
    print("Save a state first (F1); if the game crashes, load it (F3) and re-run: progress is saved as '%s'.\n" % args.save)
    rnd = 0
    while len(remaining) > 1:
        rnd += 1
        half = remaining[:len(remaining) // 2]
        input("round %d: %d of %d candidates. Press Enter to start..." % (rnd, len(half), len(remaining)))
        hold_group(p, [(a, e, test_value(a, e)) for a, e in half], args.seconds, args.hz)
        ans = ask("Did the view turn or look sideways? [y/n, q to stop] ")
        if ans == "q":
            break
        remaining = half if ans == "y" else remaining[len(remaining) // 2:]
        save_cands(d, args.save, remaining)
    if len(remaining) == 1:
        a, e = remaining[0]
        input("final check on 0x%08x (%s). Press Enter..." % (a, e))
        hold_group(p, [(a, e, test_value(a, e))], args.seconds, args.hz)
        if ask("Did the view turn? [y/n] ") == "y":
            print("\nFound it: 0x%08x (%s). Now check whether GT4 fights the write:" % (a, e))
            print("  g4 poke 0x%08x %s --enc %s --hold 5" % (a, fmt_val(test_value(a, e), e), e))
        else:
            print("\nNo luck: the look-back angle isn't among these. Either it's computed fresh each frame")
            print("(use the code-hook route, README section 4) or a wrong answer sent the search the wrong way;")
            print("re-run on '%s' to try again." % args.name)
    else:
        print("%d candidate(s) left in '%s'." % (len(remaining), args.save))
    p.close()


def describe_access(row):
    ins = Insn(row["pc"], row["op"])
    ea = ""
    if ins.base is not None:
        ea = "  ea=0x%08x ($%s=0x%08x)" % ((row["gpr"][ins.base] + ins.offset) & 0xFFFFFFFF,
                                         GPR[ins.base], row["gpr"][ins.base] & 0xFFFFFFFF)
    ra = row["gpr"][31] & 0xFFFFFFFF
    sp = row["gpr"][29] & 0xFFFFFFFF
    return "%s pc=0x%08x hits=%-6d %s%s  ra=0x%08x sp=0x%08x" % (
        "W" if row["is_write"] else "R", row["pc"], row["hits"], " ".join(ins.text.split()), ea, ra, sp)


def cmd_watch(args):
    p = connect(args)
    addr = parse_int(args.addr)
    cond = {"r": 1, "w": 2, "rw": 3}[args.mode]
    p.memwatch_clear()
    slot = p.memwatch_arm(addr, args.size, cond)
    print("watching 0x%08x..+%d (%s) in slot %d for %.0f s - keep driving / do the thing..."
          % (addr, args.size, args.mode, slot, args.seconds))
    rows_all = {}
    dropped = 0
    t_end = time.time() + args.seconds
    try:
        while time.time() < t_end:
            time.sleep(0.5)
            rows, d = p.memwatch_poll()
            dropped += d
            for r in rows:
                key = (r["pc"], r["is_write"])
                if key in rows_all:
                    rows_all[key]["hits"] += r["hits"]
                else:
                    rows_all[key] = r
    except KeyboardInterrupt:
        pass
    finally:
        p.memwatch_clear()
    if not rows_all:
        print("no hits. (Is the game running, not paused? Is this address used in the current scene?)")
        p.close()
        return
    rows = sorted(rows_all.values(), key=lambda r: -r["hits"])
    print("%d distinct access site(s)%s:" % (len(rows), " (+%d dropped)" % dropped if dropped else ""))
    for r in rows:
        print("  " + describe_access(r))
    if args.no_analyze:
        p.close()
        return
    seen = set()
    for r in rows[:args.analyze]:
        if r["pc"] in seen:
            continue
        seen.add(r["pc"])
        print("\n--- around 0x%08x ---" % r["pc"])
        for line in disasm_lines(p, r["pc"] - 4 * 8, 17, mark=r["pc"]):
            print("  " + line)
        func, here = find_function(p, r["pc"])
        print()
        print_hook_report(func, here)
        # Leaf helpers (VU0 matrix stores and the like) return straight to their
        # caller, so $ra at the store names the call site: analyze that too.
        ra = r["gpr"][31] & 0xFFFFFFFF
        if suggest_hooks(func)[0] or not (0x00100000 <= ra < 0x02000000) or ra & 3:
            continue
        call_site = ra - 8
        print("\n--- caller: the JAL at 0x%08x (returns to 0x%08x) ---" % (call_site, ra))
        for line in disasm_lines(p, call_site - 4 * 8, 12, mark=call_site):
            print("  " + line)
        cfunc, chere = find_function(p, call_site)
        print()
        print_hook_report(cfunc, chere, label="call site")
    p.close()


def cmd_disasm(args):
    p = connect(args)
    for line in disasm_lines(p, parse_int(args.addr) & ~3, args.count):
        print(line)
    p.close()


def cmd_func(args):
    p = connect(args)
    func, here = find_function(p, parse_int(args.pc) & ~3)
    if args.listing:
        for ins in func:
            print("%s %08x: %08x  %s" % ("=>" if ins.pc == here.pc else "  ", ins.pc, ins.word, ins.text))
        print()
    print_hook_report(func, here)
    p.close()


def is_rotation(m, tol):
    rows = (m[0:3], m[4:7], m[8:11])
    for r in rows:
        if abs(r[0] * r[0] + r[1] * r[1] + r[2] * r[2] - 1.0) > tol:
            return False
    for a, b in ((0, 1), (0, 2), (1, 2)):
        ra, rb = rows[a], rows[b]
        if abs(ra[0] * rb[0] + ra[1] * rb[1] + ra[2] * rb[2]) > tol:
            return False
    return True


def cmd_matrices(args):
    d = session_dir(args)
    names = split_list(args.snaps)
    if len(names) != 2:
        raise SystemExit("--snaps wants two names: before,after (car or camera pointing a different way)")
    (ma, ra), (mb, rb) = load_snap(d, names[0]), load_snap(d, names[1])
    start = ma["start"]
    if mb["start"] != start:
        raise SystemExit("snapshots cover different ranges")
    A, B = as_array(ra, "f32"), as_array(rb, "f32")
    tol = args.tol
    found = []
    n = len(A) - 16
    for i in range(0, n, 4):  # 16-byte aligned 4x4 (sceVu0FMATRIX layout)
        r0 = A[i] * A[i] + A[i + 1] * A[i + 1] + A[i + 2] * A[i + 2]
        if not abs(r0 - 1.0) <= tol:  # also rejects NaN; most of RAM fails here
            continue
        m = A[i:i + 16]
        if not is_rotation(m, tol):
            continue
        if abs(m[0] - 1) < 1e-6 and abs(m[5] - 1) < 1e-6 and abs(m[10] - 1) < 1e-6:
            continue  # identity
        mb_ = B[i:i + 16]
        if not is_rotation(mb_, tol):
            continue
        delta = max(abs(p - q) for p, q in zip(m[:11], mb_[:11]))
        if delta < args.min_change:
            continue
        found.append((start + 4 * i, delta, m, mb_))
    found.sort(key=lambda t: -t[1])
    print("%d rotation matrices that changed between %s and %s" % (len(found), names[0], names[1]))
    for addr, delta, m, mb_ in found[:args.show]:
        yaw_a = math.degrees(math.atan2(m[2], m[10]))
        yaw_b = math.degrees(math.atan2(mb_[2], mb_[10]))
        t = (m[12], m[13], m[14])
        print("  0x%08x  change %.3f  yaw %7.1f -> %7.1f deg  row3 (%.1f, %.1f, %.1f)"
              % (addr, delta, yaw_a, yaw_b, t[0], t[1], t[2]))
    if found:
        print("next: `watch 0x%08x --mode w` to find the code that builds it." % found[0][0])


def cmd_ptrscan(args):
    d = session_dir(args)
    names = split_list(args.snaps)
    targets = [parse_int(t) for t in split_list(args.targets)]
    if len(names) != 2 or len(targets) != 2:
        raise SystemExit("--snaps a,b and --targets addrA,addrB (the same variable in two sessions)")
    (ma, ra), (mb, rb) = load_snap(d, names[0]), load_snap(d, names[1])
    start = ma["start"]
    A, B = as_array(ra, "u32"), as_array(rb, "u32")
    ta, tb = targets
    mask = 0x01FFFFFF
    out, masked = [], []
    for i, (va, vb) in enumerate(zip(A, B)):
        ma_, mb2 = va & mask, vb & mask
        off = ta - ma_
        if 0 <= off <= args.max_offset and tb - mb2 == off:
            raw_ok = va == ma_ and vb == mb2 and not (va & 3) and va != 0
            (out if raw_ok else masked).append((start + 4 * i, off, va, vb))
    print("%d static pointer(s) that lead to the target in both sessions:" % len(out))
    for paddr, off, va, vb in out[:args.show]:
        print("  [0x%08x] + 0x%x   (pointed at 0x%08x / 0x%08x)" % (paddr, off, va, vb))
    if masked:
        print("%d more only match with the kseg/uncached bits stripped (e.g. 0x%08x). PenguinScreen2's"
              " pointer walk rejects values >= 32 MB, so those can't be a `base: pointer`." % (len(masked), masked[0][2]))
    if out:
        paddr, off, _, _ = out[0]
        print("\nYAML:\n    base: { pointer: 0x%08X }\n    writes:\n      - { address: 0x%X, relative: true, ... }"
              % (paddr, off))


RAD2ARCMIN = 60.0 * 180.0 / math.pi


def cmd_qhist(args):
    if args.file:
        path = args.file
    else:
        p = connect(args)
        path = p.qhist_flush()
        p.close()
        print("flushed %s" % path)
    with open(path) as f:
        h = json.load(f)
    b = h["binning"]
    bpo, wmin = b["bins_per_octave"], b["log2w_min"]
    cov = h["bins"]["coverage"]
    s = h["summary"]
    wp = s["w_percentiles"]
    c = h["census"]
    total = sum(cov) or 1.0
    print("game %s CRC %s, frame %s, target %sx%s" % (h["key"]["serial"], h["key"]["crc"], h["key"]["frame"],
                                                   h["key"]["unscaled_w"], h["key"]["unscaled_h"]))
    print("\ncoverage by depth (w = 1/Q, log scale):")
    peak = max(cov) or 1.0
    for i in range(0, len(cov), bpo // 2 or 1):
        chunk = sum(cov[i:i + (bpo // 2 or 1)])
        if chunk / total < 0.0005:
            continue
        w = 2 ** (wmin + (i + 0.5) / bpo)
        bar = "#" * int(round(40 * chunk / (peak * (bpo // 2 or 1))))
        print("  w~%9.3g  %5.1f%%  %s" % (w, 100 * chunk / total, bar))
    print("\nw percentiles: p05 %.3g  p25 %.3g  p50 %.3g  p75 %.3g  p95 %.3g   (span %.1f octaves)"
          % (wp["p05"], wp["p25"], wp["p50"], wp["p75"], wp["p95"], s["octave_span_p05_p95"]))
    print("draws: displaced %d, UV/FST flat %d, uniform-Q pinned %d, wide-Q %d, STQ-flagged %d, total %d"
          % (c["displaced"], c["fst_excluded"], c["uniform_q_pinned"], c["wide_q_displaced"],
             c["accurate_stq_flagged"], c["total"]))
    nf = h["overflow"]["non_finite"]["coverage"] if "non_finite" in h["overflow"] else 0.0
    if c["displaced"] == 0:
        print("\nNothing was displaced - is VR stereo on and the headset session running?")
        return
    # Suggestion
    conv = float("%.2g" % max(wp["p05"], 1e-6))
    parallel = (0.053 / args.screen_dist) * RAD2ARCMIN
    cap = 0.95 * parallel / (args.screen_arc * 60.0)
    # 1 degree between the flat HUD (zero disparity) and infinity, unless the screen geometry caps it lower
    one_deg = 60.0 / (args.screen_arc * 60.0)
    sep = round(min(one_deg, cap), 4)
    d50 = sep * max(0.0, 1.0 - conv / wp["p50"]) if wp["p50"] > 0 else 0.0
    print("\nsuggested linear map:  separation: %.4f   convergence: %g" % (sep, conv))
    print("  -> nearest 5%% of the scene sits on the screen surface; the median depth uses %.0f%% of the"
          " depth budget." % (100 * d50 / sep if sep else 0))
    print("  Linear is shaped like real stereopsis: depth is strong up close and fades with distance.")
    if s["octave_span_p05_p95"] > 5.0 or (sep and d50 / sep > 0.85):
        print("\nalternative log map (spreads depth evenly over %.1f octaves; more depth in the distance,"
              " less physical):" % s["octave_span_p05_p95"])
        print("    map: log\n    log: { w0: %.3g, w1: %.3g, dfar: %.4f }" % (wp["p05"], wp["p95"], sep))
    if total and nf / (total + nf) > 0.05:
        print("\n%.0f%% of displaced coverage had a non-finite Q - try `zDrivenDepth: true`."
              % (100 * nf / (total + nf)))
    if c["wide_q_displaced"] > 0.3 * c["displaced"]:
        print("\nMany draws span a wide Q range (big ground/road polygons); that's normal for a racer.")


def cmd_cave_check(args):
    """Is a region really unused? Two independent checks over the whole length:
    memory watches (catch EE loads/stores, 128 bytes at a time, rotating through the region) and
    content sampling (catches anything that changes it, DMA included)."""
    p = connect(args)
    addr = parse_int(args.addr) & ~15
    length = (parse_int(args.length) + 15) & ~15
    base = p.read_block(addr, length)
    nz = [i for i, bt in enumerate(base) if bt]
    print("0x%08x..+0x%x: %s" % (addr, length, "all zero" if not nz else "%d non-zero bytes (first at +0x%x)" % (len(nz), nz[0])))
    windows = [(addr + off, min(128, length - off)) for off in range(0, length, 128)]
    per = args.seconds / len(windows)
    print("watching all 0x%x bytes for %.0f s (%d window(s) of up to 128 bytes, %.0f s each) and sampling the contents"
          " - play normally: a race, and menus/loading if you can..." % (length, args.seconds, len(windows), per))
    rows_all = []
    changed = {}          # offset -> (first new value, seconds into the check)
    t0 = time.time()
    try:
        for w_addr, w_len in windows:
            p.memwatch_clear()
            for i in range(0, w_len, 16):
                p.memwatch_arm(w_addr + i, 16, 3)
            t_end = time.time() + per
            while time.time() < t_end:
                now = p.read_block(addr, length)
                if now != base:
                    for i in range(0, length, 4):
                        if now[i:i + 4] != base[i:i + 4] and i not in changed:
                            changed[i] = (struct.unpack_from("<I", now, i)[0], time.time() - t0)
                time.sleep(0.05)
            rows, _ = p.memwatch_poll()
            rows_all += rows
    except KeyboardInterrupt:
        print("(stopped early)")
    finally:
        p.memwatch_clear()
    if rows_all:
        print("NOT free - EE code reads/writes it:")
        seen = set()
        for r in rows_all:
            key = (r["pc"], r["is_write"])
            if key not in seen:
                seen.add(key)
                print("  " + describe_access(r))
    if changed:
        print("NOT free - the contents changed (%d word(s)):" % len(changed))
        for off in sorted(changed)[:16]:
            v, t = changed[off]
            print("  0x%08x  %08x -> %08x  after %.1f s" % (addr + off, struct.unpack_from("<I", base, off)[0], v, t))
    if not rows_all and not changed:
        print("no access and no change seen. Plausibly free (watches can't see instruction fetch).")
    p.close()


def default_emulog():
    home = os.path.expanduser("~")
    cands = [os.path.join(home, "Documents", "PenguinScreen2", "logs", "emulog.txt"),
             os.path.join(home, "OneDrive", "Documents", "PenguinScreen2", "logs", "emulog.txt"),
             os.path.join(home, ".var", "app", "org.penguinvr.penguinscreen2", "config", "PenguinScreen2",
                          "logs", "emulog.txt"),
             os.path.join(home, ".config", "PenguinScreen2", "logs", "emulog.txt")]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


PROBE_RE = re.compile(r"(?:Unrecognized op |Unknown R5900 Standard: )(c[0-3][0-9a-f]{6})\b", re.I)


def cmd_exec_probe(args):
    """Does the game EXECUTE a region? (cave-check only sees data reads/writes.)
    Fills it with words the EE recompiler doesn't know - opcode 0x30, with the word's own address in
    the low 26 bits - which it skips like a nop but logs ("EE: Unrecognized op c00f1104") when it
    compiles them. So executing them changes nothing, and the emulator log names every word that ran."""
    p = connect(args)
    addr = parse_int(args.addr) & ~3
    length = (parse_int(args.length) + 3) & ~3
    log = args.log or default_emulog()
    if not log:
        raise SystemExit("can't find emulog.txt - pass --log <path to Documents\\PenguinScreen2\\logs\\emulog.txt>")
    orig = p.read_block(addr, length)
    if any(orig) and not args.force:
        raise SystemExit("0x%08x..+0x%x isn't all zero - the probe would replace whatever is there "
                         "(head camera hooks still installed? restart the emulator with it off)" % (addr, length))
    words = [(0xC0000000 | ((addr + i) & 0x03FFFFFF)) for i in range(0, length, 4)]
    log_start = os.path.getsize(log)
    try:
        p.write_batch([(addr + 4 * i, 4, w) for i, w in enumerate(words)])
        print("probing 0x%08x..+0x%x for %.0f s - drive (and visit menus/loading if you can). Log: %s"
              % (addr, length, args.seconds, log))
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        print("(stopped early)")
    finally:
        p.write_batch([(addr + i, 4, struct.unpack_from("<I", orig, i)[0]) for i in range(0, length, 4)])
        print("restored the original contents.")
    time.sleep(0.5)
    with open(log, "rb") as f:
        size = os.path.getsize(log)
        f.seek(log_start if size >= log_start else 0)
        text = f.read().decode("utf-8", "replace")
    ran = set()
    for m in PROBE_RE.finditer(text):
        w = int(m.group(1), 16)
        a = w & 0x03FFFFFF
        if addr <= a < addr + length:
            ran.add(a)
    if not ran:
        print("no word in the region ran. Combined with a clean cave-check, it looks free.")
    else:
        rs = sorted(ran)
        spans, s0, prev = [], rs[0], rs[0]
        for a in rs[1:] + [None]:
            if a is None or a != prev + 4:
                spans.append((s0, prev))
                if a is not None:
                    s0 = a
            if a is not None:
                prev = a
        print("NOT free - the game executed %d word(s) here:" % len(ran))
        for s, e in spans:
            print("  0x%08x..0x%08x" % (s, e + 3))
    p.close()


def mips_lui(rt, imm):
    return 0x3C000000 | (rt << 16) | (imm & 0xFFFF)


def mips_sw(rt, off, base):
    return 0xAC000000 | (base << 21) | (rt << 16) | (off & 0xFFFF)


def mips_j(target):
    return 0x08000000 | ((target >> 2) & 0x03FFFFFF)


def cmd_trace_offset(args):
    """GT4 retail: capture s0 (the camera offset vector) at the camera-placement site."""
    p = connect(args)
    serial, crc = p.serial(), p.crc()
    if (serial, crc) != GT4_RETAIL and not args.force:
        raise SystemExit("this command is specific to %s CRC %08X (running: %s %08X)" % (GT4_RETAIL + (serial, crc)))
    words = p.read_words(GT4_OFFSET_SITE, 4)
    if words != GT4_OFFSET_WORDS:
        raise SystemExit("code at 0x%08x is not what we expect (a camera patch enabled?): %s"
                         % (GT4_OFFSET_SITE, " ".join("%08x" % w for w in words)))
    cave = parse_int(args.cave)
    scratch = cave + 0x20
    if any(p.read_block(cave, 0x24)):
        raise SystemExit("cave 0x%08x is not zero; pass --cave with a free region" % cave)
    hi = ((scratch + 0x8000) >> 16) & 0xFFFF
    lo = scratch & 0xFFFF
    code = [mips_lui(1, hi),               # lui   $at, hi(scratch)
            mips_sw(16, lo, 1),            # sw    $s0, lo(scratch)($at)
            GT4_OFFSET_WORDS[0],           # lwc1  $f12, 0($s0)    (the instruction we displaced)
            mips_j(GT4_OFFSET_SITE + 8),   # j     0x0037B30C      (back to the jal; $f13 loaded in our delay slot)
            0]                             # nop
    p.write32(scratch, 0)
    for i, w in enumerate(code):
        p.write32(cave + 4 * i, w)
    seen = {}
    try:
        p.write32(GT4_OFFSET_SITE, mips_j(cave))   # its delay slot is the original lwc1 $f13
        print("hooked 0x%08x -> cave 0x%08x; sampling for %.0f s (switch views with SELECT)..."
              % (GT4_OFFSET_SITE, cave, args.seconds))
        t_end = time.time() + args.seconds
        while time.time() < t_end:
            s0 = p.read32(scratch)
            if s0:
                seen[s0] = seen.get(s0, 0) + 1
            time.sleep(0.01)
    finally:
        p.write32(GT4_OFFSET_SITE, GT4_OFFSET_WORDS[0])
        for i in range(len(code)):
            p.write32(cave + 4 * i, 0)
        p.write32(scratch, 0)
        print("restored original code.")
    if not seen:
        print("the site never ran - are you in a race (not paused)?")
        p.close()
        return
    print("offset vectors read by the camera code (address: x, y, z):")
    for s0, cnt in sorted(seen.items(), key=lambda kv: -kv[1]):
        a = s0 & 0x01FFFFFF
        x, y, z = struct.unpack("<3f", p.read_block(a, 12))
        tag = "  <- chase cam (z = -6.0)" if abs(z + 6.0) < 1e-4 else ""
        print("  0x%08x  (%7.3f, %7.3f, %7.3f)  seen %d%s" % (a, x, y, z, cnt, tag))
    print("\nTest one with:  gt4cam.py poke 0x<addr+4> <y+0.3> --hold 5   (camera should rise 30 cm)")
    p.close()


# --------------------------------------------------------------------------
# Code-hook test: install the same trampoline + cave PenguinScreen2 uses and drive it by hand
# --------------------------------------------------------------------------

def mips_jal(target):
    return 0x0C000000 | ((target >> 2) & 0x03FFFFFF)


def mips_lwc1(ft, off, base):
    return 0xC4000000 | (base << 21) | (ft << 16) | (off & 0xFFFF)


def mips_add_s(fd, fs, ft):
    return 0x46000000 | (ft << 16) | (fs << 11) | (fd << 6)


def hi_lo(addr):
    return ((addr + 0x8000) >> 16) & 0xFFFF, addr & 0xFFFF


def code_hook_cave(scratch, tail, fpr, marker=None):
    """The 5-word cave CameraDriver.cpp's AssembleHook() builds:
         lui $at, hi(scratch); lwc1 $f1, lo(scratch)($at); add.s $fT, $fT, $f1; j tail; nop
    With `marker`, the j's delay slot does `sw $ra, lo(marker)($at)` instead of the nop, so the
    host can tell the site actually ran ($ra = hook + 8 there). Same registers, same size."""
    hi, lo = hi_lo(scratch)
    words = [mips_lui(1, hi), mips_lwc1(1, lo, 1), mips_add_s(fpr, fpr, 1), mips_j(tail), 0]
    if marker is not None:
        if hi_lo(marker)[0] != hi:
            raise ValueError("scratch and marker must share their upper half")
        words[4] = mips_sw(31, hi_lo(marker)[1], 1)
    return words


def f32_bits(v):
    return struct.unpack("<I", struct.pack("<f", v))[0]


# GT4 retail, camera build function 0x0037B020: the game's camera-local sway rotations, applied
# before the camera offset (so they turn the view about the eye). Helpers take degrees in $f12.
# Only run when the camera mode (vtable+0xB8) is 0 or 25. name: (jal site, delay-slot word, helper)
GT4_SWAY_HOOKS = {
    "roll": (0x0037B1B4, 0x46000306, 0x004A79D8),    # jal RotZ ; mov.s $f12, $f0
    "pitch": (0x0037B1BC, 0x4600AB06, 0x004A7988),   # jal RotX ; mov.s $f12, $f21
    "yaw": (0x0037B1C4, 0x4600A306, 0x004A79B0),     # jal RotY ; mov.s $f12, $f20
}
SIGN_HINT = {
    "head_yaw": "head_yaw is positive when you turn your head LEFT: with --hold +20, if the camera "
                "looked left (road slid right) keep axisSign 1, if it looked right use axisSign -1.",
    "head_pitch": "head_pitch is positive when you look UP: with --hold +20, if the camera tipped up "
                  "keep axisSign 1, if it tipped down use axisSign -1.",
    "head_roll": "head_roll is positive when you tilt your head LEFT (left ear down): with --hold +20, "
                 "if the horizon tilted clockwise keep axisSign 1, otherwise use axisSign -1.",
}


def cmd_hooktest(args):
    p = connect(args)
    site = args.site.lower()
    source = args.source
    expect_slot = None
    if site in GT4_SWAY_HOOKS:
        serial, crc = p.serial(), p.crc()
        if (serial, crc) != GT4_RETAIL and not args.force:
            raise SystemExit("'%s' is a GT4 retail site (%s CRC %08X); running %s %08X - pass an address instead"
                             % ((site,) + GT4_RETAIL + (serial, crc)))
        hook, expect_slot, _ = GT4_SWAY_HOOKS[site]
        source = source or "head_" + site
    else:
        hook = parse_int(args.site)
    source = source or "head_yaw"
    cave, scratch = parse_int(args.cave), parse_int(args.scratch)
    marker = scratch + 4
    if (hook | cave | scratch) & 3:
        raise SystemExit("hook, cave and scratch addresses must be 4-byte aligned")
    if hi_lo(marker)[0] != hi_lo(scratch)[0]:
        raise SystemExit("--scratch is at the very end of a 64 KB page; pick one a few bytes earlier")
    trampoline = mips_jal(cave)

    word = p.read32(hook)
    cave_now = p.read_words(cave, 5)
    if word == trampoline and (cave_now[3] >> 26) == 2:
        word = 0x0C000000 | (cave_now[3] & 0x03FFFFFF)
        p.write32(hook, word)
        time.sleep(0.1)
        p.write_batch([(cave + 4 * i, 4, 0) for i in range(5)] + [(scratch, 4, 0), (marker, 4, 0)])
        print("0x%08x still had a hooktest trampoline from an interrupted run - restored the original "
              "jal 0x%08x first." % (hook, (word & 0x03FFFFFF) << 2))
        cave_now = [0] * 5
    if (word >> 26) != 3:
        raise SystemExit("0x%08x holds %08x (%s), not a jal - a code hook can only replace a jal.\n"
                         "If the VR head camera is on with a codeHooks profile, turn it off and retry."
                         % (hook, word, Insn(hook, word).text))
    tail = ((hook + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
    tail_words = p.read_words(tail, 5)
    if (tail_words[0] >> 16) == 0x3C01 and (tail_words[3] >> 26) == 2:
        raise SystemExit("0x%08x already jumps to a code-hook cave at 0x%08x - the VR head camera has hooked "
                         "it. Turn the head camera off (or take codeHooks out of the profile) and retry."
                         % (hook, tail))
    slot = p.read32(hook + 4)
    if expect_slot is not None and slot != expect_slot and not args.force:
        raise SystemExit("the delay slot at 0x%08x is %08x, expected %08x - different game build?"
                         % (hook + 4, slot, expect_slot))
    if (any(cave_now) or p.read32(scratch) or p.read32(marker)) and not args.force:
        raise SystemExit("cave 0x%08x..+0x14 or scratch 0x%08x..+8 is not zero; pick free space with "
                         "--cave/--scratch (see cave-check)" % (cave, scratch))

    print("site:")
    for line in disasm_lines(p, hook - 8, 5, mark=hook):
        print("  " + line)
    print("hooking 0x%08x: jal 0x%08x -> jal cave 0x%08x, which adds scratch 0x%08x to $f%d, then j 0x%08x"
          % (hook, tail, cave, scratch, args.fpr, tail))

    code = code_hook_cave(scratch, tail, args.fpr, marker)
    p.write_batch([(scratch, 4, 0), (marker, 4, 0)] + [(cave + 4 * i, 4, w) for i, w in enumerate(code)])
    amp = args.amp if args.amp is not None else (25.0 if args.units == "deg" else 0.44)
    unit = "deg" if args.units == "deg" else "rad"
    if args.hold is not None:
        print("holding %+g %s for %.0f s - Ctrl+C stops early" % (args.hold, unit, args.seconds))
    else:
        print("sweeping +/-%g %s every %.1f s for %.0f s - Ctrl+C stops early" % (amp, unit, args.period, args.seconds))
    print("watch whether the view turns about the driver's eye, orbits the car, or does nothing.\n")

    checks = ran = 0
    ras = set()
    lost = False
    last_ran = None
    try:
        p.write32(hook, trampoline)
        t0 = time.time()
        next_check = t0 + 0.25
        next_print = t0
        while True:
            now = time.time()
            t = now - t0
            if t >= args.seconds:
                break
            v = args.hold if args.hold is not None else amp * math.sin(2 * math.pi * t / args.period)
            p.write32(scratch, f32_bits(v))
            if now >= next_check:
                next_check = now + 0.25
                if p.read32(hook) != trampoline:
                    lost = True
                    break
                m = p.read32(marker)
                checks += 1
                last_ran = bool(m)
                if m:
                    ran += 1
                    ras.add(m)
                    p.write32(marker, 0)
            if now >= next_print:
                next_print = now + 0.1
                state = "-" if last_ran is None else ("yes" if last_ran else "NO ")
                sys.stdout.write("\r  offset %+7.2f %s   site running: %s  " % (v, unit, state))
                sys.stdout.flush()
            time.sleep(1 / 60)
    except KeyboardInterrupt:
        pass
    finally:
        if p.read32(hook) == trampoline:
            p.write32(hook, word)
        time.sleep(0.1)
        p.write_batch([(cave + 4 * i, 4, 0) for i in range(5)] + [(scratch, 4, 0), (marker, 4, 0)])
        print("\nrestored the original jal at 0x%08x and cleared the cave." % hook)

    if lost:
        print("the game rewrote 0x%08x while hooked (code reloaded - a menu or loading screen?). Retry in the race."
              % hook)
    if checks == 0:
        p.close()
        return
    if ran == 0:
        print("the hooked call never ran in %d checks: this view doesn't go through 0x%08x%s." % (
            checks, hook, " (GT4: the sway block only runs in camera modes 0 and 25 - try the other view)"
            if site in GT4_SWAY_HOOKS else ""))
        print("is the game unpaused and in a race?")
        p.close()
        return
    odd = sorted(r for r in ras if r != hook + 8)
    print("the hooked call ran in %d of %d checks%s." % (
        ran, checks, "" if not odd else " (unexpected return address(es): %s)" % ", ".join("%08x" % r for r in odd)))
    scale = 57.29578 if args.units == "deg" else 1.0
    print("\nIf the view turned about the driver's eye, this site is a head-look hook:")
    print("  - { enabled: true, hookAddress: 0x%08X, tailJump: 0x%08X, caveAddress: 0x%08X,"
          % (hook, tail, GT4_CAVE_DEFAULT + 0x20 * {"yaw": 0, "pitch": 1, "roll": 2}.get(site, 0)))
    print("      scratchAddress: 0x%08X, source: %s, scale: %s, axisSign: 1, targetFpr: %d }"
          % (GT4_CAVE_DEFAULT + 0x60 + 4 * {"yaw": 0, "pitch": 1, "roll": 2}.get(site, 0), source,
             scale, args.fpr))
    if source in SIGN_HINT:
        print("\nSign: " + SIGN_HINT[source])
    print("If it orbited the car instead, it is a car-space rotation - not usable for head look.")
    p.close()


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="See README.md for the Gran Turismo 4 walkthrough.")
    ap.add_argument("--socket", help="PINE unix socket path (default: $XDG_RUNTIME_DIR/pcsx2.sock)")
    ap.add_argument("--slot", type=int, default=PINE_DEFAULT_SLOT, help="PINE slot (default 28011)")
    ap.add_argument("--tcp", help="HOST:PORT instead of a unix socket (Windows builds)")
    ap.add_argument("--session", default="gt4cam-session", help="folder for snapshots and candidate sets")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", help="check the connection, game, and GT4-specific sites")
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("snap", help="save EE RAM to a named snapshot (pause the game first)")
    s.add_argument("name")
    s.add_argument("--start", default="0x00100000")
    s.add_argument("--end", default="0x02000000")
    s.set_defaults(fn=cmd_snap)

    s = sub.add_parser("find", help="search snapshots with an expression or a preset")
    s.add_argument("expr", nargs="?", help='e.g. "near(rest,0) and near(abs(back),pi,0.02)"')
    s.add_argument("--snaps", required=True, nargs="+",
                   help="snapshot names, comma- or space-separated (they become variables)")
    s.add_argument("--enc", default="f32", choices=sorted(ENC))
    s.add_argument("--preset", help="built-in search: " + ", ".join(PRESETS))
    s.add_argument("--within", help="only test addresses in this saved candidate set")
    s.add_argument("--save", help="save the hits as a candidate set")
    s.add_argument("--show", type=int, default=40)
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("cands", help="show live values for a candidate set")
    s.add_argument("name")
    s.add_argument("--show", type=int, default=40)
    s.set_defaults(fn=cmd_cands)

    s = sub.add_parser("peek", help="read values (optionally live)")
    s.add_argument("addr")
    s.add_argument("--enc", default="f32", choices=sorted(ENC))
    s.add_argument("--n", type=int, default=1, help="how many consecutive values")
    s.add_argument("--live", action="store_true")
    s.add_argument("--hz", type=float, default=10.0)
    s.set_defaults(fn=cmd_peek)

    s = sub.add_parser("poke", help="write a value once or hold it, and report if the game fights back")
    s.add_argument("addr")
    s.add_argument("value")
    s.add_argument("--enc", default="f32", choices=sorted(ENC))
    s.add_argument("--hold", type=float, default=0.0, help="seconds to keep rewriting it")
    s.add_argument("--hz", type=float, default=120.0)
    s.add_argument("--keep", action="store_true", help="don't restore the original value afterwards")
    s.set_defaults(fn=cmd_poke)

    s = sub.add_parser("bisect", help="find which candidate turns the view, by halving the set each round")
    s.add_argument("name", help="candidate set, e.g. look2")
    s.add_argument("--back", required=True, help="snapshot taken while looking back (sets each test value)")
    s.add_argument("--enc", choices=sorted(ENC), help="only test candidates of this type")
    s.add_argument("--seconds", type=float, default=3.0)
    s.add_argument("--hz", type=float, default=120.0)
    s.add_argument("--save", default="bisect", help="where to keep the remaining candidates")
    s.set_defaults(fn=cmd_bisect)

    s = sub.add_parser("watch", help="memory watch: which code reads/writes an address (VR extension)")
    s.add_argument("addr")
    s.add_argument("--size", type=int, default=4, help="1..16 bytes")
    s.add_argument("--mode", choices=["r", "w", "rw"], default="w")
    s.add_argument("--seconds", type=float, default=5.0)
    s.add_argument("--analyze", type=int, default=2, help="how many access sites to disassemble")
    s.add_argument("--no-analyze", action="store_true")
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("disasm", help="disassemble EE code")
    s.add_argument("addr")
    s.add_argument("--count", type=int, default=32)
    s.set_defaults(fn=cmd_disasm)

    s = sub.add_parser("func", help="find the function around a PC and list code-hook candidates")
    s.add_argument("pc")
    s.add_argument("--listing", action="store_true", help="print the whole function")
    s.set_defaults(fn=cmd_func)

    s = sub.add_parser("matrices", help="find rotation matrices that changed between two snapshots")
    s.add_argument("--snaps", required=True, nargs="+", help="before,after")
    s.add_argument("--tol", type=float, default=2e-3)
    s.add_argument("--min-change", type=float, default=0.02)
    s.add_argument("--show", type=int, default=30)
    s.set_defaults(fn=cmd_matrices)

    s = sub.add_parser("ptrscan", help="find a static pointer to a variable that moves between sessions")
    s.add_argument("--snaps", required=True, nargs="+", help="snapshot from session A, snapshot from session B")
    s.add_argument("--targets", required=True, nargs="+", help="the variable's address in A, in B")
    s.add_argument("--max-offset", type=lambda v: int(v, 0), default=0x2000)
    s.add_argument("--show", type=int, default=20)
    s.set_defaults(fn=cmd_ptrscan)

    s = sub.add_parser("qhist", help="flush and read the stereo depth histogram; suggest stereo values")
    s.add_argument("--file", help="analyze an existing qhist JSON instead of flushing")
    s.add_argument("--screen-arc", type=float, default=100.0, help="virtual screen arc in degrees")
    s.add_argument("--screen-dist", type=float, default=2.0, help="virtual screen distance in metres")
    s.set_defaults(fn=cmd_qhist)

    s = sub.add_parser("cave-check", help="check that a code-cave region is unused")
    s.add_argument("addr")
    s.add_argument("length")
    s.add_argument("--seconds", type=float, default=30.0)
    s.set_defaults(fn=cmd_cave_check)

    s = sub.add_parser("exec-probe", help="check whether the game executes a region (reads the emulator log)")
    s.add_argument("addr")
    s.add_argument("length")
    s.add_argument("--seconds", type=float, default=30.0)
    s.add_argument("--log", help="path to emulog.txt (default: found under Documents\\PenguinScreen2\\logs)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_exec_probe)

    s = sub.add_parser("trace-offset", help="GT4 retail: find the active camera's offset vector")
    s.add_argument("--seconds", type=float, default=4.0)
    s.add_argument("--cave", default="0x000F11D0")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_trace_offset)

    s = sub.add_parser("hooktest", help="temporarily install a code hook on a jal and swing its float argument")
    s.add_argument("site", help="jal address, or for GT4 retail: yaw, pitch or roll (the camera sway calls)")
    s.add_argument("--units", choices=["deg", "rad"], default="deg", help="what the called helper takes (GT4: deg)")
    s.add_argument("--amp", type=float, help="sweep amplitude (default 25 deg / 0.44 rad)")
    s.add_argument("--period", type=float, default=4.0, help="seconds per sweep")
    s.add_argument("--hold", type=float, help="hold this constant offset instead of sweeping (for the sign)")
    s.add_argument("--seconds", type=float, default=12.0)
    s.add_argument("--fpr", type=int, default=12, help="float register the jal's argument is in (default 12)")
    s.add_argument("--source", help="head_yaw / head_pitch / head_roll, for the printed YAML")
    s.add_argument("--cave", default="0x000F11D0", help="5 free words for the temporary cave")
    s.add_argument("--scratch", default="0x000F11E8", help="8 free bytes: the offset value and a 'ran' marker")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_hooktest)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except PineError as e:
        raise SystemExit("error: %s" % e)


if __name__ == "__main__":
    main()
