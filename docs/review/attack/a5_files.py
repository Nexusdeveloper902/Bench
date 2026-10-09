"""Persistence attacks: garbage/hand-edited JSON, byte stability, unwritable path, GUI open of bad files."""
import sys, os, json, tempfile, stat
sys.path.insert(0, ".")
from app.model import Document, ModelError
from app.sim import run_document

tmp = tempfile.mkdtemp()
base = Document.led_example().to_dict()


def try_load(label, text):
    p = os.path.join(tmp, "f.json")
    with open(p, "w" if isinstance(text, str) else "wb") as f: f.write(text)
    try:
        d = Document.load(p)
    except ModelError as e:
        print(f"[{label}] rejected: {str(e)[:110]}"); return None
    except Exception as e:
        print(f"[{label}] CRASH {type(e).__name__}: {e}"); return "crash"
    r = run_document(d)
    print(f"[{label}] LOADED parts={list(d.parts)} wires={len(d.wires)} run ok={r.ok} msg={r.message[:100]!r}")
    return d


def mut(fn):
    d = json.loads(json.dumps(base)); fn(d); return json.dumps(d)


try_load("empty", ""); try_load("garbage", "\x00\xff garbage"); try_load("truncated", json.dumps(base)[:80])
try_load("list", "[]"); try_load("null", "null"); try_load("v2", mut(lambda d: d.update(version=2)))
try_load("version str", mut(lambda d: d.update(version="1")))
try_load("version 1.0", mut(lambda d: d.update(version=1.0)))
try_load("version true", mut(lambda d: d.update(version=True)))
try_load("format other", mut(lambda d: d.update(format="kicad")))
try_load("dangling conn", mut(lambda d: d["connections"].append(["R9.a", "D1.a"])))
try_load("bad pin", mut(lambda d: d["connections"].append(["R1.z", "D1.a"])))
try_load("self wire", mut(lambda d: d["connections"].append(["R1.a", "R1.a"])))
try_load("dup wire", mut(lambda d: d["connections"].append(["R1.b", "D1.a"])))
try_load("rev dup wire", mut(lambda d: d["connections"].append(["D1.a", "R1.b"])))
try_load("3-pin conn", mut(lambda d: d["connections"].append(["R1.a", "D1.a", "V1.p"])))
try_load("conn as str", mut(lambda d: d["connections"].append("R1.aD1.a")))
try_load("conn ints", mut(lambda d: d["connections"].append([1, 2])))
try_load("unknown type", mut(lambda d: d["components"].update(X1={"type": "TRANSISTOR", "params": {}})))
try_load("unknown param", mut(lambda d: d["components"]["R1"]["params"].update(foo="1")))
try_load("param number", mut(lambda d: d["components"]["R1"]["params"].update(R=330)))
try_load("param null", mut(lambda d: d["components"]["R1"]["params"].update(R=None)))
try_load("param list", mut(lambda d: d["components"]["R1"]["params"].update(R=[1, 2])))
try_load("param 1e999", mut(lambda d: d["components"]["R1"]["params"].update(R="1e999")))
try_load("param huge num", '{"format":"sim-circuit","version":1,"components":{"R1":{"type":"R","params":{"R":1e999}}},'
         '"connections":[],"simulation":{"tstop":"1m","tstep":"1u"}}')
try_load("NaN layout", mut(lambda d: d["layout"]["R1"].update(x=float("nan"))))
try_load("inf layout", mut(lambda d: d["layout"]["R1"].update(x=float("inf"))))
try_load("huge layout", mut(lambda d: d["layout"]["R1"].update(x=1e308)))
try_load("bool layout", mut(lambda d: d["layout"]["R1"].update(x=True)))
try_load("rot 45", mut(lambda d: d["layout"]["R1"].update(rotation=45)))
try_load("rot 90.0", mut(lambda d: d["layout"]["R1"].update(rotation=90.0)))
try_load("layout for missing part", mut(lambda d: d["layout"].update(ZZ={"x": 1, "y": 2})))
try_load("no layout", mut(lambda d: d.pop("layout")))
try_load("tstop num", mut(lambda d: d["simulation"].update(tstop=0.005)))
try_load("tstop null", mut(lambda d: d["simulation"].update(tstop=None)))
try_load("analysis ac", mut(lambda d: d["simulation"].update(analysis="ac")))
try_load("name with space", mut(lambda d: (d["components"].__setitem__("my R", d["components"].pop("R1")),
                                          d["layout"].__setitem__("my R", d["layout"].pop("R1")),
                                          d.__setitem__("connections", [[c.replace("R1", "my R") for c in w] for w in d["connections"]]))))
try_load("lowercase dup r1/R1", mut(lambda d: (d["components"].__setitem__("r1", {"type": "R", "params": {"R": "1k"}}),
                                              d["connections"].extend([["r1.a", "R1.a"], ["r1.b", "R1.b"]]))))
try_load("name = GND1 for R", mut(lambda d: (d["components"].__setitem__("GND1x", d["components"].pop("R1")),
                                            d.__setitem__("connections", [[c.replace("R1", "GND1x") for c in w] for w in d["connections"]]))))
try_load("name empty", mut(lambda d: d["components"].__setitem__("", {"type": "R", "params": {}})))
try_load("name newline", mut(lambda d: d["components"].__setitem__("R2\nV9 n1 0 DC 100", {"type": "R", "params": {}})))
try_load("param injection", mut(lambda d: d["components"]["R1"]["params"].update(R="1k\nV9 n1 0 100")))
raw = json.dumps(base).replace('"R1": {"type"', '"D1": {"type": "R", "params": {}}, "R1": {"type"', 1)
try_load("duplicate key D1", raw)
try_load("BOM", "﻿" + json.dumps(base))
try_load("v0 file", json.dumps({"parts": {"R1": {"kind": "R", "x": 0, "y": 0, "params": {"R": "1k"}}}, "wires": [],
                                "tstop": "1m", "tstep": "1u"}))
try_load("deep nesting", "[" * 100000 + "]" * 100000)

# byte stability
p1, p2 = os.path.join(tmp, "a.json"), os.path.join(tmp, "b.json")
for ex in (Document.led_example(), Document.blink_example()):
    ex.save(p1); Document.load(p1).save(p2)
    print("save->load->save byte-identical:", open(p1, "rb").read() == open(p2, "rb").read())
d = Document.load(p1); d.parts["R1"]["params"]["R"] = "330"; d.save(p2)
# params given as numbers in file: do they survive as typed?
# file has GUI coupling? list keys
print("top-level keys:", list(base), "| component keys:", list(base["components"]["R1"]), "| layout:", base["layout"]["R1"])
# unwritable paths
for p in ("/proc/x.json", "/nonexistent/dir/x.json", "/", tmp):
    try: Document.led_example().save(p); print("SAVED to", p, "?!")
    except ModelError as e: print("save", p, "->", e)
    except Exception as e: print("save", p, "-> CRASH", type(e).__name__, e)
ro = os.path.join(tmp, "ro"); os.mkdir(ro); os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
try: Document.led_example().save(os.path.join(ro, "x.json")); print("saved into read-only dir?!")
except ModelError as e: print("ro dir ->", e)
# existing read-only file in a writable dir: os.replace succeeds -> silently overwrites a read-only file
rof = os.path.join(tmp, "readonly.json"); open(rof, "w").write("KEEP"); os.chmod(rof, 0o444)
try: Document.led_example().save(rof); print("READ-ONLY FILE OVERWRITTEN:", open(rof).read()[:30].replace("\n", " "), oct(os.stat(rof).st_mode))
except ModelError as e: print("ro file ->", e)
# symlink target replaced by a regular file?
tgt = os.path.join(tmp, "target.json"); open(tgt, "w").write("{}"); ln = os.path.join(tmp, "link.json"); os.symlink(tgt, ln)
Document.led_example().save(ln)
print("symlink still a link after save:", os.path.islink(ln), "| target updated:", open(tgt).read() != "{}")
