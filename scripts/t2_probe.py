import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from ngspice_ctypes import NgSpice
import threading
r = {}
def w(k, v):
    ng = NgSpice()
    ng.init()
    ng.circ(["c", "V1 in 0 DC %s" % v, "R1 in out 1k", "R2 out 0 1k", ".tran 1u 2m", ".end"])
    ng.command("tran 1u 2m")
    x = ng.vec("v(out)")
    r[k] = x[-1] if x else None
ts = [threading.Thread(target=w, args=(i, 1 + i)) for i in range(2)]
[t.start() for t in ts]
[t.join(30) for t in ts]
print("SAME_LIB_CONCURRENT", r)
