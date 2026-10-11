"""Probe harness (task 1, not app code): drive /usr/lib/libngspice.so exactly like benchcore/src/ngspice.rs
(ngSpice_Init with print/stat/exit/data/initdata/bg callbacks, ngSpice_Circ, 'run'), bypassing Bench's validator.
usage: python3 ng_probe.py deck.cir [vec ...]   -> filtered log, rc codes, last value of each vec.
Device vectors (@x[y]) need a '.save all @x[y]' line in the deck. Run each risky deck in its own process: some errors
leave the library unusable for the rest of the process (DESIGN.md 3.2). As a module: `run(lines, vecs, cmds)`."""
import ctypes as C, sys

lib = C.CDLL("/usr/lib/libngspice.so")
LOG = []
PCB = C.CFUNCTYPE(C.c_int, C.c_char_p, C.c_int, C.c_void_p)
EXIT = C.CFUNCTYPE(C.c_int, C.c_int, C.c_bool, C.c_bool, C.c_int, C.c_void_p)
DATA = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_void_p)
INIT = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p)
BG = C.CFUNCTYPE(C.c_int, C.c_bool, C.c_int, C.c_void_p)
pcb = PCB(lambda m, i, u: LOG.append(m.decode(errors="replace")) or 0)
scb = PCB(lambda m, i, u: 0)
ecb = EXIT(lambda *a: 0)
dcb = DATA(lambda *a: 0)
icb = INIT(lambda *a: 0)
bcb = BG(lambda *a: 0)
lib.ngSpice_Init(pcb, scb, ecb, dcb, icb, bcb, None)


class VI(C.Structure):
    _fields_ = [("name", C.c_char_p), ("type", C.c_int), ("flags", C.c_short), ("pad", C.c_short),
                ("real", C.POINTER(C.c_double)), ("comp", C.c_void_p), ("len", C.c_int)]


lib.ngGet_Vec_Info.restype = C.POINTER(VI)


def run(lines, vecs=(), cmds=("run",)):
    LOG.clear()
    arr = (C.c_char_p * (len(lines) + 1))(*[l.encode() for l in lines], None)
    rc = lib.ngSpice_Circ(arr)
    rcs = [lib.ngSpice_Command(c.encode()) for c in cmds]
    out = {}
    for v in vecs:
        p = lib.ngGet_Vec_Info(v.encode())
        if p and p.contents.len > 0 and p.contents.real:
            out[v] = p.contents.real[p.contents.len - 1]
        else:
            out[v] = None
    log = list(LOG)
    lib.ngSpice_Command(b"destroy all")
    return rc, rcs, out, log


if __name__ == "__main__":
    lines = open(sys.argv[1]).read().splitlines()
    rc, rcs, out, log = run(lines, sys.argv[2:])
    noise = ("stdout ******", "stdout ** ", "stdout Note:", "stdout Doing", "stdout Reference", "stdout No.",
             "stdout Initial", "stdout Using", "stdout Simulation", "stdout ----")
    for l in log:
        if not l.startswith(noise) and l.strip() not in ("stdout", "stderr"):
            print("  |", l)
    print("circ rc", rc, "cmd rcs", rcs)
    for k, v in out.items():
        print(f"  {k} = {v}")
