"""Task 9.7 probes (not app code): can a live run go on indefinitely with bounded memory and exact pauses?
usage: python3 indefinite_probe.py <probe> [args]   (one probe per process)
Results are summarised in ../LIVE.md ("Indefinite runs")."""
import ctypes as C, sys, time

from ng_probe import LOG, lib, VI  # noqa: F401  (ng_probe initialises the library on import)
from live_probe import circ, cmd, vec


def rss_mb():
    for l in open("/proc/self/status"):
        if l.startswith("VmRSS"):
            return int(l.split()[1]) / 1024


def rc(tstop, save=()):
    return ["indef rc", "V1 in 0 DC 5", "R1 in out 1k", "C1 out 0 1u IC=0", *save,
            f".tran 10u {tstop} 0 10u uic", ".end"]


def chunks(n, chunk=1e-3, report=500):
    """Pause every `chunk` s for n chunks; print RSS, plot length and pause exactness."""
    t0, worst = time.perf_counter(), 0.0
    for k in range(1, n + 1):
        cmd(f"stop when time = {k * chunk:.17e}")
        cmd("run" if k == 1 else "resume")
        cmd("delete all")
        t = vec("time") if (k % report == 0 or k == 1) else None
        if t:
            worst = max(worst, abs(t[-1] - k * chunk))
            print(f"chunk {k:6d}  t_end {t[-1]:.9g}  plot len {len(t):8d}  rss {rss_mb():8.1f} MB  "
                  f"{time.perf_counter() - t0:6.1f} s  worst pause err {worst:.2e}")


def p_huge(tstop="1e6"):
    """A huge tstop: does load preallocate, does the plot grow without bound?"""
    print("rss before load", rss_mb())
    assert circ(rc(tstop)) == 0
    print("rss after load", rss_mb())
    chunks(5000)


class VV(C.Structure):
    _fields_ = [("name", C.c_char_p), ("creal", C.c_double), ("cimag", C.c_double), ("is_scale", C.c_int),
                ("is_complex", C.c_int)]


class VA(C.Structure):
    _fields_ = [("veccount", C.c_int), ("vecindex", C.c_int), ("vecsa", C.POINTER(C.POINTER(VV)))]


DATA = C.CFUNCTYPE(C.c_int, C.POINTER(VA), C.c_int, C.c_int, C.c_void_p)
SEEN, CALLS, ROWS = [], [0], []   # sampled callbacks, callback count, every row (when ROWS_ON)
ROWS_ON = [False]


def _on_data(p, n, i, u):
    a = p.contents
    CALLS[0] += 1
    if ROWS_ON[0]:
        ROWS.append({a.vecsa[j].contents.name.decode(): a.vecsa[j].contents.creal for j in range(a.veccount)})
    if CALLS[0] <= 3 or CALLS[0] % 100000 == 0:
        SEEN.append((a.vecindex, [(a.vecsa[j].contents.name.decode(), a.vecsa[j].contents.creal)
                                  for j in range(a.veccount)]))
    return 0


_PCB = C.CFUNCTYPE(C.c_int, C.c_char_p, C.c_int, C.c_void_p)
_KEEP = [_PCB(lambda m, i, u: LOG.append(m.decode(errors="replace")) or 0), _PCB(lambda m, i, u: 0),
         C.CFUNCTYPE(C.c_int, C.c_int, C.c_bool, C.c_bool, C.c_int, C.c_void_p)(lambda *a: 0), DATA(_on_data),
         C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p)(lambda *a: 0),
         C.CFUNCTYPE(C.c_int, C.c_bool, C.c_int, C.c_void_p)(lambda *a: 0)]
lib.ngSpice_Init(*_KEEP, None)   # re-init with a SendData callback that records


def p_savenone():
    """`.save none` (manual: for shared ngspice with SendData): vector lengths, RSS, callback contents."""
    seen, calls = SEEN, CALLS
    assert circ(rc("1e6", [".save none", ".save v(out)", ".save @r1[i]"])) == 0
    print("load log", [l for l in LOG if "rror" in l or "arn" in l])
    chunks(3000)
    for name in ("time", "v(out)", "@r1[i]", "v(in)"):
        p = lib.ngGet_Vec_Info(name.encode())
        print(name, "len", p.contents.len if p else None)
    print("callbacks", calls[0])
    for s in seen[:3] + seen[-2:]:
        print("callback", s)


def p_exact():
    """LDR with an alter at 1 ms: stored vectors of a normal run vs the SendData rows of a `.save none` run."""
    from live_probe import LDR
    out = {}
    for mode, extra in (("stored", []), ("savenone", [".save none"])):
        lines = LDR[:1] + extra + LDR[1:]
        ROWS.clear(); ROWS_ON[0] = True
        assert circ(lines) == 0
        cmd("stop when time = 1m"); cmd("run")
        LOG.clear(); lib.ngSpice_Command(b"print @v.xrl1.vstim[dc]"); print(mode, "readback", [l for l in LOG if "dc" in l])
        cmd("alter v.xrl1.vstim dc = 100"); cmd("delete all"); cmd("resume")
        ROWS_ON[0] = False
        out[mode] = ([r["time"] for r in ROWS], [r["out"] for r in ROWS], vec("time"), vec("v(out)"), sorted(ROWS[0]))
        cmd("destroy all")
    st, sn = out["stored"], out["savenone"]
    print("callback names (stored / savenone):", st[4], sn[4])
    print("stored run: callback == plot:", st[0] == st[2] and st[1] == st[3], len(st[2]))
    print("savenone callback == stored plot (bitwise):", sn[0] == st[2] and sn[1] == st[3], len(sn[0]), "plot len", len(sn[2]))


def p_null(n="20000"):
    """`.save none` + ngSpice_Command(NULL) every 100 chunks: does the run continue exactly, is RSS flat?"""
    assert circ(rc("1e6", [".save none", ".save v(out)"])) == 0
    t0, r0 = time.perf_counter(), rss_mb()
    for k in range(1, int(n) + 1):
        cmd(f"stop when time = {k * 1e-4:.17e}"); cmd("run" if k == 1 else "resume"); cmd("delete all")
        if k % 100 == 0:
            lib.ngSpice_Command(None)
        if k % 2000 == 0:
            t = vec("time")
            print(f"chunk {k:6d} t {t[-1]:.9g} exact {t[-1] == k * 1e-4 or abs(t[-1] - k * 1e-4) < 1e-15} "
                  f"rss {rss_mb():.1f} MB (start {r0:.1f}) {time.perf_counter() - t0:.1f} s")


def p_nonull(n="20000"):
    """The same without the NULL command (the per-command growth)."""
    assert circ(rc("1e6", [".save none", ".save v(out)"])) == 0
    r0 = rss_mb()
    for k in range(1, int(n) + 1):
        cmd(f"stop when time = {k * 1e-4:.17e}"); cmd("run" if k == 1 else "resume"); cmd("delete all")
        if k % 2000 == 0:
            print(f"chunk {k:6d} rss {rss_mb():.1f} MB (start {r0:.1f})")



def p_saveall():
    """The app's saves are `.save all @dev[i]`: does `all` undo a preceding `.save none`?"""
    assert circ(rc("1e6", [".save none", ".save all @r1[i]", ".save all @c1[i]"])) == 0
    ROWS_ON[0] = True
    cmd("stop when time = 1m"); cmd("run")
    print("plot len", len(vec("time")), "callback rows", len(ROWS), "names", sorted(ROWS[-1]))


NEXT_SAVES = [""]


def p_leak(reset="", saves=""):
    """Does `.save none` leak into the next circuit? `reset`: commands (';'-separated) sent between the two."""
    NEXT_SAVES[0] = saves
    assert circ(rc("2m", [".save none"])) == 0
    cmd("run")
    print("save none run: plot len", len(vec("time")))
    for c in ("stop clear", "destroy all", "delete all", *[c for c in reset.split(";") if c]):
        print(c, cmd(c))
    assert circ(rc("2m", [l for l in NEXT_SAVES[0].split(";") if l])) == 0
    cmd("run")
    print("next circuit: plot len", len(vec("time")), "v(out) len", len(vec("v(out)")))


if __name__ == "__main__":
    globals()[f"p_{sys.argv[1]}"](*sys.argv[2:])
