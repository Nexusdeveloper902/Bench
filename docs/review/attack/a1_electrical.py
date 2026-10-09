"""Electrical edge cases through Document -> run_document (the same path the GUI Run uses)."""
import sys, time, math
sys.path.insert(0, ".")
from app.model import Document
from app.sim import run_document


def build(parts, wires, tstop="1m", tstep="10u"):
    d = Document()
    for kind, kw in parts: d.add_part(kind, **kw)
    for a, b in wires: d.connect(a, b)
    d.tstop, d.tstep = tstop, tstep
    return d


def show(label, d):
    t = time.time(); r = run_document(d); dt = time.time() - t
    fin = {s["name"]: round(r.stats(s["name"])["final"], 6) for s in r.signals} if r.ok else {}
    print(f"[{label}] ok={r.ok} {dt:.2f}s msg={r.message[:150]!r}")
    if r.findings: print("   findings:", [(f["code"], f["severity"]) for f in r.findings])
    if fin: print("   finals:", fin)
    return r


G = ("GND", {})
# sanity of each component in a small circuit
show("R divider", build([("V", {"dc": 10}), ("R", {"R": "1k"}), ("R", {"R": "1k"}), G],
                        [("V1.p", "R1.a"), ("R1.b", "R2.a"), ("R2.b", "GND1.g"), ("V1.n", "GND1.g")]))
show("I source into R (1mA*1k => V(n)=?)", build([("I", {"dc": "1m"}), ("R", {"R": "1k"}), G],
                        [("I1.p", "R1.a"), ("R1.b", "GND1.g"), ("I1.n", "GND1.g")]))
show("L in series w R (steady I=V/R)", build([("V", {"dc": 5}), ("R", {"R": "100"}), ("L", {"L": "1m"}), G],
                        [("V1.p", "R1.a"), ("R1.b", "L1.a"), ("L1.b", "GND1.g"), ("V1.n", "GND1.g")], "1m", "1u"))
show("Diode fwd", build([("V", {"dc": 5}), ("R", {"R": "1k"}), ("D", {}), G],
                        [("V1.p", "R1.a"), ("R1.b", "D1.a"), ("D1.k", "GND1.g"), ("V1.n", "GND1.g")]))
show("LED", build([("V", {"dc": 5}), ("R", {"R": "330"}), ("LED", {}), G],
                  [("V1.p", "R1.a"), ("R1.b", "D1.a"), ("D1.k", "GND1.g"), ("V1.n", "GND1.g")]))
show("Switch closed", build([("V", {"dc": 5}), ("SW", {"state": "closed"}), ("R", {"R": "1k"}), G],
                            [("V1.p", "SW1.a"), ("SW1.b", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g")]))
show("Switch open", build([("V", {"dc": 5}), ("SW", {"state": "open"}), ("R", {"R": "1k"}), G],
                          [("V1.p", "SW1.a"), ("SW1.b", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g")]))
# topology attacks
show("two grounds", build([("V", {"dc": 5}), ("R", {}), G, G],
                          [("V1.p", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND2.g")]))
show("floating subcircuit", build([("V", {"dc": 5}), ("R", {}), G, ("R", {}), ("R", {})],
                                  [("V1.p", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g"),
                                   ("R2.a", "R3.a"), ("R2.b", "R3.b")]))
show("shorted V source", build([("V", {"dc": 5}), G], [("V1.p", "V1.n"), ("V1.n", "GND1.g")]))
show("shorted V source via wire to gnd both", build([("V", {"dc": 5}), G], [("V1.p", "GND1.g"), ("V1.n", "GND1.g")]))
show("C + I source only (no DC path)", build([("I", {"dc": "1m"}), ("C", {}), G],
                                            [("I1.p", "C1.a"), ("C1.b", "GND1.g"), ("I1.n", "GND1.g")]))
show("I source open (series switch open)", build([("I", {"dc": "1m"}), ("SW", {}), G],
                                            [("I1.p", "SW1.a"), ("SW1.b", "GND1.g"), ("I1.n", "GND1.g")]))
show("V||L loop", build([("V", {"dc": 5}), ("L", {}), G], [("V1.p", "L1.a"), ("L1.b", "GND1.g"), ("V1.n", "GND1.g")]))
show("L-L loop", build([("V", {"dc": 5}), ("R", {}), ("L", {}), ("L", {}), G],
                       [("V1.p", "R1.a"), ("R1.b", "L1.a"), ("L1.b", "GND1.g"), ("L2.a", "L1.a"), ("L2.b", "GND1.g"),
                        ("V1.n", "GND1.g")]))
show("V||C", build([("V", {"dc": 5}), ("C", {}), G], [("V1.p", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")]))
show("two V parallel different", build([("V", {"dc": 5}), ("V", {"dc": 3}), G],
                                        [("V1.p", "V2.p"), ("V1.n", "GND1.g"), ("V2.n", "GND1.g")]))
show("I sources series conflicting", build([("I", {"dc": "1m"}), ("I", {"dc": "2m"}), ("R", {}), G],
                                           [("I1.p", "I2.n"), ("I2.p", "R1.a"), ("R1.b", "GND1.g"), ("I1.n", "GND1.g")]))
show("only ground", build([G], []))
show("diode reverse", build([("V", {"dc": 5}), ("R", {}), ("D", {}), G],
                        [("V1.p", "R1.a"), ("R1.b", "D1.k"), ("D1.a", "GND1.g"), ("V1.n", "GND1.g")]))
# param attacks on R
for val in ["0", "-1", "1e999", "nan", "inf", "-inf", "1e300", "1e-300", "", " ", "１k", "1kΩ", "1 k", "1e", "1..2",
            "0x10", "1_000", "1k5", "1mega", "1MEG", "1Meg", "1f", "1F", "5V", "µ", "1µ", "10uF", "1,5"]:
    d = build([("V", {"dc": 5}), ("R", {"R": val}), G], [("V1.p", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g")])
    r = run_document(d)
    print(f"R={val!r:10} ok={r.ok} msg={r.message[:90]!r}", (r.stats('I(R1)')['final'] if r.ok else ""))
# huge but finite values in other places
for kind, key, val in [("C", "C", "1e300"), ("L", "L", "1e300"), ("C", "C", "1e-30"), ("V", "dc", "1e300"),
                       ("I", "dc", "1e30"), ("D", "Is", "1"), ("D", "N", "1e-6"), ("SW", "ron", "1e-300"),
                       ("C", "ic", "1e30"), ("L", "ic", "1e30"), ("V", "dc", "-1e300")]:
    d = Document(); n = d.add_part(kind); d.add_part("R"); g = d.add_part("GND")
    p = d.pins(n)
    d.connect(p[0], "R1.a"); d.connect("R1.b", "GND1.g"); d.connect(p[1], "GND1.g")
    d.set_param(n, key, val); d.tstop, d.tstep = "1m", "10u"
    show(f"{kind}.{key}={val}", d)
# timing
for tstop, tstep in [("1m", "2m"), ("1m", "1m"), ("0", "1u"), ("1m", "0"), ("1e999", "1"), ("50", "10u"),
                     ("1e-15", "1e-18"), ("1e-20", "1e-26"), ("1e6", "1")]:
    d = build([("V", {"dc": 5}), ("R", {}), G], [("V1.p", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g")], tstop, tstep)
    r = show(f"tstop={tstop} tstep={tstep}", d)
    if r.ok: print("   points", len(r.series["v(n1)"]), "last t", r.series["v(n1)"][-1][0])
