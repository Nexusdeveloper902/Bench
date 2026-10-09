"""Netlist injection that RUNS: a hand-edited file names a current source (no observables, so nothing breaks)
'I1 n1 0 DC 0\nV9 n1 0 DC 100\nIX'. The extra line 'V9 n1 0 DC 100' (a 100 V source that is not in the
document, not on the canvas) is simulated and changes the plotted results. Names cannot contain '.', so dot
commands (.control/shell, .include) are blocked only by accident of the pin-ref syntax."""
import sys, os, json, tempfile
sys.path.insert(0, ".")
from app.model import Document
from app.sim import run_document

d = Document()
d.add_part("I", dc="1m"); d.add_part("R", R="1k"); d.add_part("GND")
for a, b in (("I1.n", "R1.a"), ("R1.b", "GND1.g"), ("I1.p", "GND1.g")): d.connect(a, b)
d.tstop, d.tstep = "1m", "10u"
honest = run_document(d); print("honest: V(n1) final =", honest.stats("V(n1)")["final"])
x = d.to_dict(); evil = "I1 n1 0 DC 0\nV9 n1 0 DC 100\nIX"
x["components"][evil] = x["components"].pop("I1"); x["layout"][evil] = x["layout"].pop("I1")
x["connections"] = [[c.replace("I1.", evil + ".") for c in w] for w in x["connections"]]
p = os.path.join(tempfile.mkdtemp(), "evil.json"); json.dump(x, open(p, "w"))
doc = Document.load(p); r = run_document(doc)
print("evil file: ok =", r.ok, "| V(n1) final =", r.stats("V(n1)")["final"] if r.ok else r.message)
print("parts in document:", [n.replace("\n", "\\n") for n in doc.parts])
