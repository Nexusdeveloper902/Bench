"""Subcircuit-backed packages (device = "X"): definition text from `defs` files (package-local or models:<path> under
the root's _models/) and inline `body`, emitted once per deck in dependency order, through the REAL path
Document -> netlist -> app.sim -> benchcore -> ngspice. Oracles are closed-form (non-inverting gain G/(1+G/A), divider,
beta equation, Shockley), never "ngspice accepted the deck"."""
import hashlib
import math

import benchcore
import pytest
from conftest import write_def
from app import catalog, electrical
from app.catalog import BUILTIN_ROOT, CATALOG
from app.model import Document, ModelError
from app.sim import run_document
from test_analytic_diode import VT

REFS = ("OPAMP", "OPAMP_DUAL", "NTC_THERMISTOR")
DIV = ".subckt div top mid\nR1 top mid 1k\nR2 mid 0 3k\n.ends div\n"
RP = "* default and override\n.subckt rp a b params: RA=1k\nR1 a b {RA}\n.ends\n"


def p(key, default, unit=""):
    return {"key": key, "label": key, "unit": unit, "default": default}


def xpart(root, id, line, pins=("a", "b"), params=(), files=None, reload=True, **elec):
    """root/<id>/component.toml for an X part (+ package-local files) -> its directory."""
    def m(d):
        d["designator"] = "U"
        d["pins"] = [{"id": x, "at": [0, 20 * i]} for i, x in enumerate(pins)]
        d["params"] = list(params)
        d["sim"] = {"electrical": {"device": "X", "line": line, "model_kind": "project-subckt", **elec}}
    f = write_def(root, id, mutate=m, reload=False)
    for name, text in (files or {}).items(): (f.parent / name).write_text(text, encoding="utf-8")
    if reload: catalog.reload([BUILTIN_ROOT, root])
    return f.parent


def shared(root, rel, text, record=True, **rec):
    """root/_models/<rel> plus its MANIFEST.toml record (correct sha256 unless overridden)."""
    f = root / "_models" / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    if record:
        r = {"path": rel, "origin": "project", "source": "test", "license": "MIT",
             "sha256": hashlib.sha256(text.encode()).hexdigest(), **rec}
        with open(root / "_models" / "MANIFEST.toml", "a", encoding="utf-8") as m:
            m.write("[[file]]\n" + "".join(f'{k} = "{v}"\n' for k, v in r.items()))


def sim_problems(field=None):
    return [x for x in CATALOG.problems if x["code"] == "DEF-SIM" and field in (None, x["field"])]


def rejected(id, *needles):
    msgs = " | ".join(x["message"] for x in sim_problems())
    assert id not in CATALOG.defs and all(n in msgs for n in needles), CATALOG.problems


def wire(d, *pairs):
    for a, b in pairs: d.connect(a, b)


def run(d, tstop="200u", tstep="1u"):
    d.tstop, d.tstep = tstop, tstep
    res = run_document(d)
    assert res.ok, res.message + "\n" + res.details
    return res


def final(res, net):
    return res.stats(f"V({net})")["final"]


def internal(d, vecs, saves=(), tstop=2e-4):
    """Final values of subcircuit-internal vectors (v(xu1.n2), @r.xu1.rout[i]) for the document's own deck."""
    nl = d.netlist()
    s = benchcore.Simulation("internal").build_circuit(nl["lines"])
    for m in nl["models"]: s.add_model(m)
    for v in saves: s.add(f".save all {v}")
    s.configure(tstep=1e-6, tstop=tstop, tmax=1e-6).observe(*vecs)
    s.run()
    return {v: s.observable(v)[-1][1] for v in vecs}


def noninv(d, u, gain, src="V1.p"):
    """Wire op-amp `u` as a non-inverting amplifier of gain 1 + Rf/Rg (Rg = 1k) from `src`."""
    rf, rg = d.add_part("R", R=f"{(gain - 1)}k"), d.add_part("R", R="1k")
    wire(d, (src, f"{u}.inp"), (f"{u}.out", f"{rf}.a"), (f"{rf}.b", f"{u}.inn"), (f"{rg}.a", f"{u}.inn"),
         (f"{rg}.b", "GND1.g"))


# ---------------------------------------------------------------- shipped reference packages
def test_reference_packages_are_fully_body_validated():
    assert CATALOG.problems == []
    for i in REFS:
        c = CATALOG.get(i)
        sec = c.sim["electrical"]
        assert c.simulated and sec["device"] == "X" and (sec["_status"], sec["_limits"]) == ("full", ())
        assert electrical.status_note(sec) is None and not any("Static validation" in x for x in c.limitations)
        assert sec["model_kind"] == "project-subckt" and sec["verification"] == "analytic"


def test_every_shared_model_file_has_one_matching_provenance_record():
    base = BUILTIN_ROOT / "_models"
    recs = electrical.manifest(BUILTIN_ROOT, {})
    files = sorted(f.relative_to(base).as_posix() for f in base.rglob("*")
                   if f.is_file() and f.name != "MANIFEST.toml" and "LICENSES" not in f.parts)
    assert files and sorted(recs) == files
    for rel in files:
        sha = hashlib.sha256((base / rel).read_bytes()).hexdigest()
        assert electrical.manifest_problems(recs[rel], sha) == [], rel
        lic = recs[rel]["license"]
        assert not lic.startswith("LicenseRef-") or (base / "LICENSES" / f"{lic[11:]}.txt").is_file()


def test_definition_paths_do_not_depend_on_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = catalog.reload()
    assert c.problems == [] and set(REFS) <= set(c.defs)


# ---------------------------------------------------------------- port mapping
def test_inverting_amplifier_maps_inp_inn_out_in_port_order():
    d = Document()
    d.add_part("V", dc="1"); d.add_part("R", R="1k"); d.add_part("R", R="4k"); d.add_part("OPAMP"); d.add_part("GND")
    wire(d, ("V1.p", "R1.a"), ("R1.b", "U1.inn"), ("U1.out", "R2.a"), ("R2.b", "U1.inn"), ("U1.inp", "GND1.g"),
         ("V1.n", "GND1.g"))
    assert d.netlist()["lines"][-1] == "XU1 0 n2 n3 bench_opamp A=100000.0 GBW=1000000.0"
    a = 1e5                                                       # -Rf/Rin with finite open-loop gain
    assert final(run(d), d.nets()["U1.out"]) == pytest.approx(-4 / (1 + 5 / a), rel=1e-6)


RP3 = 3e3 * 1e6 / (3e3 + 1e6)                                   # R2 in parallel with the 1 MΩ load


@pytest.mark.parametrize("line,v_tap", [("{dev} {hi} {tap} div", 4 * RP3 / (1e3 + RP3)),  # hi -> top, tap -> mid
                                        ("{dev} {tap} {hi} div", 4 * 1e6 / (1e6 + 1e3))])  # swapped: hi -> mid
def test_node_field_i_connects_to_port_i(extra_root, line, v_tap):
    xpart(extra_root, "DIVX", line, pins=("hi", "tap"), body=DIV)
    assert CATALOG.problems == []
    d = Document()
    d.add_part("V", dc="4"); d.add_part("DIVX"); d.add_part("R", R="1meg"); d.add_part("GND")
    wire(d, ("V1.p", "U1.hi"), ("U1.tap", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g"))
    assert final(run(d, "20u"), d.nets()["U1.tap"]) == pytest.approx(v_tap, rel=1e-9)


def test_port_count_and_subckt_name_are_checked(extra_root):
    xpart(extra_root, "BADX", "{dev} {a} {b} {c} div", pins=("a", "b", "c"), body=DIV)
    rejected("BADX", ".subckt div has 2 ports (top mid) but the line has 3 node fields")
    xpart(extra_root, "BADX", "{dev} {a} {b} nodiv", body=DIV)
    rejected("BADX", "must name a .subckt defined in 'defs' or 'body'", "defined: div")
    xpart(extra_root, "BADX", "{dev} {a} {b} div params: x=1", body=DIV)
    rejected("BADX", "must be NAME=value")


# ---------------------------------------------------------------- several instances, one definition
def test_three_instances_share_one_definition_with_their_own_internals():
    d = Document()
    d.add_part("V", dc="0.5"); d.add_part("GND")
    us = [d.add_part("OPAMP") for _ in range(3)]
    for u, g in zip(us, (2, 11, 4)): noninv(d, u, g)
    wire(d, ("V1.n", "GND1.g"))
    nl = d.netlist()
    assert sum(l.startswith(".subckt bench_opamp ") for l in nl["models"]) == 1
    assert [l for l in nl["models"] if l.startswith("* bench-def")] == [
        f"* bench-def _models/generic/opamp.lib {CATALOG.get('OPAMP').sim['electrical']['_units'][0].sha[:8]}"]
    assert [l.split()[0] for l in nl["lines"] if l.startswith("X")] == ["XU1", "XU2", "XU3"]
    res = run(d)
    for u, g in zip(us, (2, 11, 4)):
        assert final(res, d.nets()[f"{u}.out"]) == pytest.approx(0.5 * g / (1 + g / 1e5), rel=1e-6)
    # internal node n2 (after the pole) and element rout exist once per instance, with that instance's values
    vals = internal(d, ["v(xu1.n2)", "v(xu2.n2)", "@r.xu1.rout[i]", "@r.xu2.rout[i]"],
                    saves=["@r.xu1.rout[i]", "@r.xu2.rout[i]"])
    assert vals["v(xu1.n2)"] == pytest.approx(1.0, rel=1e-3) and vals["v(xu2.n2)"] == pytest.approx(5.5, rel=1e-3)
    assert vals["@r.xu1.rout[i]"] == pytest.approx(1.0 / 2e3, rel=1e-3)         # 1 V over Rf + Rg = 2k
    assert vals["@r.xu2.rout[i]"] == pytest.approx(5.5 / 11e3, rel=1e-3)        # 5.5 V over 11k


# ---------------------------------------------------------------- nested dependencies
def test_dual_op_amp_nests_the_shared_definition_and_emits_it_first():
    d = Document()
    d.add_part("V", dc="1"); d.add_part("V", dc="2"); d.add_part("GND")
    dual = d.add_part("OPAMP_DUAL"); single = d.add_part("OPAMP")      # the dual is used first
    wire(d, ("V1.p", f"{dual}.inp1"), (f"{dual}.out1", f"{dual}.inn1"), ("V2.p", f"{dual}.inp2"),
         (f"{dual}.out2", f"{dual}.inn2"), ("V1.n", "GND1.g"), ("V2.n", "GND1.g"))
    noninv(d, single, 3, src="V2.p")
    models = d.netlist()["models"]
    defs = [l.split()[2] for l in models if l.startswith("* bench-def")]
    assert defs == ["_models/generic/opamp.lib", "electrical/opamp_dual/component.toml#body"]    # dependency first
    assert sum(l.startswith(".subckt bench_opamp ") for l in models) == 1
    res = run(d)
    n = d.nets()
    assert final(res, n[f"{dual}.out1"]) == pytest.approx(1 / (1 + 1e-5), rel=1e-6)    # follower: 1/(1 + 1/A)
    assert final(res, n[f"{dual}.out2"]) == pytest.approx(2 / (1 + 1e-5), rel=1e-6)
    assert final(res, n[f"{single}.out"]) == pytest.approx(6 / (1 + 3e-5), rel=1e-6)
    vals = internal(d, ["v(xu1.xa.n2)", "v(xu1.xb.n2)", "v(xu2.n2)"])
    assert vals["v(xu1.xa.n2)"] == pytest.approx(1.0, rel=1e-3) and vals["v(xu1.xb.n2)"] == pytest.approx(2.0, rel=1e-3)


PAIR = ".subckt pair a k\nX1 a m half\nX2 m k half\n.ends pair\n"
HALF = ".subckt half a k\nD1 a k bench_dm\n.ends half\n"


def test_nested_dependency_across_two_files_and_a_shared_model(extra_root):
    shared(extra_root, "diodes/dm.lib", "* shared card\n.model bench_dm D(Is=1e-14 N=1)\n")
    xpart(extra_root, "PAIRX", "{dev} {a} {k} pair", pins=("a", "k"), files={"half.lib": HALF},
          defs=["models:diodes/dm.lib", "half.lib"], body=PAIR)
    assert CATALOG.problems == []
    d = Document()
    d.add_part("V", dc="5"); d.add_part("R", R="1k"); d.add_part("PAIRX"); d.add_part("PAIRX"); d.add_part("GND")
    wire(d, ("V1.p", "R1.a"), ("R1.b", "U1.a"), ("U1.k", "U2.a"), ("U2.k", "GND1.g"), ("V1.n", "GND1.g"))
    models = d.netlist()["models"]
    assert [l.split()[2] for l in models if l.startswith("* bench-def")] == [
        "_models/diodes/dm.lib", "pairx/half.lib", "pairx/component.toml#body"]
    assert models.count(".model bench_dm D(Is=1e-14 N=1)") == 1
    res = run(d, "20u")
    i = res.stats("I(R1)")["final"]
    v4 = final(res, d.nets()["U1.a"])                               # four junctions in series carry i
    assert i == pytest.approx((5 - v4) / 1e3, rel=1e-9)
    assert i == pytest.approx(1e-14 * math.expm1(v4 / 4 / VT), rel=2e-3)


@pytest.mark.parametrize("defs,why", [
    (["half.lib"], "UNDEFINED-MODEL: .subckt half: D1: model 'bench_dm' has no .model card"),   # no model file
    (["models:diodes/dm.lib"], "instantiates .subckt 'half'"),   # half.lib not listed
])
def test_unresolved_nested_dependency(extra_root, defs, why):
    """Both are load errors. The missing card used to pass the load and fail only in the engine (task 3)."""
    shared(extra_root, "diodes/dm.lib", ".model bench_dm D(Is=1e-14)\n")
    xpart(extra_root, "PAIRX", "{dev} {a} {k} pair", pins=("a", "k"), files={"half.lib": HALF}, defs=defs, body=PAIR)
    rejected("PAIRX", why)


def test_dependency_listed_after_its_user_is_an_error(extra_root):
    shared(extra_root, "diodes/dm.lib", ".model bench_dm D(Is=1e-14)\n")
    xpart(extra_root, "PAIRX", "{dev} {a} {k} pair", pins=("a", "k"),
          files={"pair.lib": PAIR, "half.lib": HALF}, defs=["models:diodes/dm.lib", "pair.lib", "half.lib"])
    rejected("PAIRX", "pairx/pair.lib: X1 instantiates .subckt 'half'")


# ---------------------------------------------------------------- params
def test_subckt_default_and_instance_override_propagate(extra_root):
    xpart(extra_root, "RDEF", "{dev} {a} {b} rp", files={"rp.lib": RP}, defs=["rp.lib"])           # RA from .subckt
    xpart(extra_root, "ROVR", "{dev} {a} {b} rp RA={r}", params=[p("r", "2k", "Ω")], files={"rp.lib": RP},
          defs=["rp.lib"])
    assert CATALOG.problems == []
    d = Document()
    d.add_part("V", dc="10"); d.add_part("RDEF"); d.add_part("ROVR"); d.add_part("ROVR", r="5k"); d.add_part("GND")
    wire(d, ("V1.p", "U1.a"), ("V1.p", "U2.a"), ("V1.p", "U3.a"), ("U1.b", "GND1.g"), ("U2.b", "GND1.g"),
         ("U3.b", "GND1.g"), ("V1.n", "GND1.g"))
    nl = d.netlist()
    assert nl["lines"][1:] == ["XU1 n1 0 rp", "XU2 n1 0 rp RA=2000.0", "XU3 n1 0 rp RA=5000.0"]
    assert sum(l.startswith(".subckt rp ") for l in nl["models"]) == 1          # two packages, one identical file
    vals = internal(d, ["@r.xu1.r1[i]", "@r.xu2.r1[i]", "@r.xu3.r1[i]"],
                    saves=["@r.xu1.r1[i]", "@r.xu2.r1[i]", "@r.xu3.r1[i]"], tstop=2e-5)
    assert [vals[f"@r.xu{k}.r1[i]"] for k in (1, 2, 3)] == pytest.approx([10 / 1e3, 10 / 2e3, 10 / 5e3], rel=1e-9)


def ntc(r25, b, t):
    return r25 * math.exp(b * (1 / (t + 273.15) - 1 / 298.15))


def test_ntc_parameters_reach_the_body_per_instance():
    d = Document()
    d.add_part("V", dc="5"); d.add_part("GND")
    for t in ("25", "50", "-10"):
        rt, r = d.add_part("NTC_THERMISTOR", temp=t), d.add_part("R", R="10k")
        wire(d, ("V1.p", f"{rt}.a"), (f"{rt}.b", f"{r}.a"), (f"{r}.b", "GND1.g"))
    wire(d, ("V1.n", "GND1.g"))
    assert sum(l.startswith(".subckt bench_ntc ") for l in d.netlist()["models"]) == 1
    res = run(d, "20u")
    for k, t in zip((1, 2, 3), (25, 50, -10)):
        rt = ntc(10e3, 3950, t)
        assert final(res, d.nets()[f"RT{k}.b"]) == pytest.approx(5 * 10e3 / (10e3 + rt), rel=1e-9), t
    assert ntc(10e3, 3950, 25) == pytest.approx(10e3)


def test_unknown_instance_param_is_rejected_not_ignored(extra_root):
    xpart(extra_root, "RBAD", "{dev} {a} {b} rp RB={r}", params=[p("r", "2k")], body=RP)
    rejected("RBAD", ".subckt rp declares no parameter 'RB' (it has: ra)", "ignore it silently")


# ---------------------------------------------------------------- malformed packages and unresolved files
@pytest.mark.parametrize("elec,why", [
    ({"body": ".subckt div top mid\nR1 top mid 1k\n"}, "without '.ends'"),
    ({"body": DIV + ".include /etc/passwd\n"}, "directive '.include' is not allowed"),
    ({"body": DIV + ".control\nshell rm -rf /\n.endc\n"}, "directive '.control' is not allowed"),
    ({"body": "R9 a b 1k\n" + DIV}, "is outside a .subckt body"),
    ({"body": ".param k=2\n" + DIV}, "'.param' outside a .subckt body"),
    ({"body": DIV + DIV}, "defines .subckt 'div' twice"),
    ({"body": DIV + ".ends\n"}, "'.ends' without a matching '.subckt'"),
    ({"defs": ["missing.lib"]}, "'missing.lib' not found"),
    ({"defs": ["../../escape.lib"]}, "must stay inside the package directory"),
    ({"defs": ["models:../../escape.lib"]}, "must stay inside the _models directory"),
    ({"defs": ["models:nope.lib"]}, "'models:nope.lib' not found"),
    ({"defs": "div.lib"}, "must be a list of file names"),
    ({"body": DIV.replace("3k", "4k"), "defs": ["div.lib"]}, "is defined by both"),
    ({"body": DIV, "defs": ["div.lib"]}, "is listed twice"),
    ({"body": DIV, "model_kind": "magic"}, "must be one of"),
    ({"body": DIV, "model_kind": "model"}, "an X part must say what backs it"),
    ({"body": DIV, "model_kind": "third-party-subckt"}, "needs [sim.electrical.provenance]"),
    ({"body": DIV, "verification": "trust me"}, "must be one of analytic"),
    ({"body": DIV, "provenance": {"license": "MIT", "colour": "red"}}, "unknown key 'colour'"),
    ({"body": DIV, "current": "@{dev_lc}[i]"}, "has no current"),
    ({}, "needs the .subckt it instantiates"),
])
def test_malformed_package_is_rejected_with_a_reason(extra_root, elec, why):
    (extra_root / "escape.lib").write_text(DIV)
    xpart(extra_root, "BADX", "{dev} {a} {b} div", files={"div.lib": DIV}, **elec)
    rejected("BADX", why)


def test_shared_file_needs_a_complete_current_provenance_record(extra_root):
    shared(extra_root, "a/div.lib", DIV, record=False)
    xpart(extra_root, "SHX", "{dev} {a} {b} div", defs=["models:a/div.lib"])
    rejected("SHX", "MANIFEST.toml is missing")
    shared(extra_root, "b/div.lib", DIV, sha256="0" * 64)
    xpart(extra_root, "SHX", "{dev} {a} {b} div", defs=["models:b/div.lib"])
    rejected("SHX", "sha256 does not match the file")
    shared(extra_root, "c/div.lib", DIV, origin="third-party")
    xpart(extra_root, "SHX", "{dev} {a} {b} div", defs=["models:c/div.lib"])
    rejected("SHX", "a third-party file needs 'url'")
    shared(extra_root, "d/div.lib", DIV, url="https://example.org/div", origin="third-party")
    xpart(extra_root, "SHX", "{dev} {a} {b} div", defs=["models:d/div.lib"], model_kind="third-party-subckt")
    assert CATALOG.problems == [] and "SHX" in CATALOG.defs     # the record carries the license


def test_conflicting_definitions_across_packages_keep_the_first(extra_root):
    xpart(extra_root, "AX", "{dev} {a} {b} div", body=DIV, reload=False)
    xpart(extra_root, "BX", "{dev} {a} {b} div", body=DIV.replace("3k", "4k"))     # same name, other body
    assert "AX" in CATALOG.defs and "BX" not in CATALOG.defs
    assert any(".subckt name 'div' is also defined by AX" in x["message"] for x in sim_problems())


def test_conflicting_model_between_a_card_and_definition_text(extra_root):
    write_def(extra_root, "DCARD", mutate=lambda d: d.update(
        pins=[{"id": "a", "at": [0, 0]}, {"id": "k", "at": [20, 0]}], params=[],
        sim={"electrical": {"device": "D", "line": "{dev} {a} {k} bench_dm", "models": [".model bench_dm D(Is=2e-14)"]}}),
        reload=False)
    shared(extra_root, "diodes/dm.lib", ".model bench_dm D(Is=1e-14)\n")
    xpart(extra_root, "PAIRX", "{dev} {a} {k} pair", pins=("a", "k"), files={"half.lib": HALF},
          defs=["models:diodes/dm.lib", "half.lib"], body=PAIR)
    assert "DCARD" in CATALOG.defs and "PAIRX" not in CATALOG.defs
    assert any("model name 'bench_dm' is also defined by DCARD" in x["message"] for x in sim_problems())


def test_deck_refuses_a_rendered_model_name_that_definition_text_already_defines(extra_root):
    """Placeholder model names escape the load-time check ({dev_lc} is only known per circuit): the deck checks."""
    shared(extra_root, "diodes/dm.lib", ".model d1 D(Is=1e-14)\n")
    write_def(extra_root, "DTPL", mutate=lambda d: d.update(
        pins=[{"id": "a", "at": [0, 0]}, {"id": "k", "at": [20, 0]}], params=[], designator="D",
        sim={"electrical": {"device": "D", "line": "{dev} {a} {k} {dev_lc}", "models": [".model {dev_lc} D(Is=2e-14)"]}}),
        reload=False)
    xpart(extra_root, "HALFX", "{dev} {a} {k} half", pins=("a", "k"), files={"half.lib": HALF.replace("bench_dm", "d1")},
          defs=["models:diodes/dm.lib", "half.lib"])
    assert CATALOG.problems == []
    d = Document()
    d.add_part("V", dc="1"); d.add_part("HALFX"); d.add_part("DTPL"); d.add_part("GND")
    with pytest.raises(ModelError, match="model 'd1' is defined differently by HALFX and DTPL"):
        d.netlist()


def test_model_backed_part_can_use_a_card_from_a_shared_file(extra_root):
    shared(extra_root, "diodes/dm.lib", ".model bench_dm D(Is=1e-14)\n.model bench_dz D(Is=1e-14 level=2)\n")
    write_def(extra_root, "DSH", mutate=lambda d: d.update(
        pins=[{"id": "a", "at": [0, 0]}, {"id": "k", "at": [20, 0]}], params=[],
        sim={"electrical": {"device": "D", "line": "{dev} {a} {k} bench_dm", "defs": ["models:diodes/dm.lib"],
                            "model_kind": "model", "current": "@{dev_lc}[id]"}}))
    assert CATALOG.problems == []
    write_def(extra_root, "DSH", mutate=lambda d: d.update(
        pins=[{"id": "a", "at": [0, 0]}, {"id": "k", "at": [20, 0]}], params=[],
        sim={"electrical": {"device": "D", "line": "{dev} {a} {k} bench_dz", "defs": ["models:diodes/dm.lib"]}}))
    rejected("DSH", "D level 2, which ngspice 47 does not have")


# ---------------------------------------------------------------- persistence
def test_save_load_round_trip_with_subcircuit_parts(tmp_path):
    d = Document()
    d.add_part("V", dc="0.5"); d.add_part("GND")
    u = d.add_part("OPAMP", gbw="2meg"); noninv(d, u, 11)
    rt = d.add_part("NTC_THERMISTOR", temp="40"); r = d.add_part("R", R="10k")
    wire(d, ("V1.p", f"{rt}.a"), (f"{rt}.b", f"{r}.a"), (f"{r}.b", "GND1.g"), ("V1.n", "GND1.g"))
    a = run(d)
    f = tmp_path / "x.json"
    d.save(f)
    d2 = Document.load(f)
    assert d2.netlist() == d.netlist() and d2.parts[u]["params"]["gbw"] == "2meg"
    b = run(d2)
    assert a.series == b.series                                    # same deck, deterministic ngspice
    assert final(b, d2.nets()[f"{u}.out"]) == pytest.approx(0.5 * 11 / (1 + 11e-5), rel=1e-6)
    assert final(b, d2.nets()[f"{rt}.b"]) == pytest.approx(0.5 * 10e3 / (10e3 + ntc(10e3, 3950, 40)), rel=1e-9)


def test_rust_validator_reads_the_emitted_deck_without_errors():
    """Parity for X (the other letters: test_device_table_parity): the deck Bench emits has no static error."""
    d = Document()
    d.add_part("V", dc="1"); d.add_part("GND"); u = d.add_part("OPAMP_DUAL"); s = d.add_part("OPAMP")
    wire(d, ("V1.p", f"{u}.inp1"), (f"{u}.out1", f"{u}.inn1"), ("V1.p", f"{u}.inp2"), (f"{u}.out2", f"{u}.inn2"),
         ("V1.n", "GND1.g"))
    noninv(d, s, 2)
    nl = d.netlist()
    found = benchcore.Simulation("parity").build_circuit(nl["lines"] + nl["models"]).validate()
    assert found == []                                  # bodies analysed through both nesting levels: nothing opaque


# ---------------------------------------------------------------- body validation at ingestion (task 4)
NOT_SIMULATED = {"ARDUINO_UNO_R3", "DHT22", "ESP32_DEVKITC", "PIEZO_BUZZER", "SSD1306_I2C"}


def test_every_shipped_part_passes_the_static_validator():
    """The 3 reference packages, the 17 originals and the 37 batch-A parts: every simulated part's probe deck (instance
    with default params, a 1 GΩ leak per pin, its models and definition text) is fully analysed with no finding, except
    the two batch-A B-source parts (MOV, SAT_AMP), which are `limited` and name what was left to ngspice. The 5 parts
    with no electrical model and the ground symbol have nothing to validate (their limitations say what is not modelled)."""
    LIMITED = {"MOV", "SAT_AMP", "T_LINE"}       # B sources and the ngspice T element are limited by construction
    seen = set()
    for i, c in CATALOG.defs.items():
        sec = c.sim.get("electrical") if c.simulated else None
        if sec is None or sec.get("ground"):
            assert i in NOT_SIMULATED | {"GND"}, i
            continue
        seen.add(i)
        status, errors, warnings, limits = electrical.validate_bodies(sec.get("_units", ()), sec, c.pins, c.params)
        if i in LIMITED:
            assert status == "limited" and errors == [] and warnings == [] and limits, i
        else:
            assert (status, errors, warnings, limits) == ("full", [], [], []), i
        assert sec.get("_status", "full") == ("limited" if i in {"MOV", "SAT_AMP"} else "full"), i   # stored for X parts only
    assert len(seen) == 86 and set(REFS) <= seen and len(CATALOG.defs) == 92


BRES = ".subckt bres a b params: R=1k\nB1 a b I=V(a,b)/{R}\n.ends bres\n"      # behavioural resistor


def test_limited_status_names_what_was_not_analysed_and_the_part_still_simulates(extra_root):
    xpart(extra_root, "BRES", "{dev} {a} {b} bres R={r}", params=[p("r", "2k", "Ω")], body=BRES)
    assert CATALOG.problems == []
    c = CATALOG.get("BRES")
    sec = c.sim["electrical"]
    assert sec["_status"] == "limited" and len(sec["_limits"]) == 1 and "B1: behavioural B not analysed" in sec["_limits"][0]
    assert electrical.status_note(sec) in c.limitations and ".subckt bres" in electrical.status_note(sec)
    d = Document()
    d.add_part("V", dc="3"); d.add_part("BRES"); d.add_part("R", R="1k"); d.add_part("GND")
    wire(d, ("V1.p", "U1.a"), ("U1.b", "R1.a"), ("R1.b", "GND1.g"), ("V1.n", "GND1.g"))
    res = run(d, "20u")
    assert final(res, d.nets()["U1.b"]) == pytest.approx(1.0, rel=1e-6)          # 3 V over 2k + 1k
    assert [(f["code"], f["class"], f["scope"]) for f in res.findings] == [("LIMITED-ANALYSIS", "limited", ".subckt bres")]


AMP = ".subckt amp inp inn out\nRin inp inn 1meg\nE1 out 0 inp inn 10\n.ends\n"
XF = ".subckt xf p q s t\nL1 p q 1m\nL2 s t 1m\nK1 L1 L2 0.99\n.ends\n"


@pytest.mark.parametrize("pins,line,body", [
    (("a", "b"), "{dev} {a} {b} rr", ".subckt rr p q\nR1 p m 1k\nR2 m q 1k\n.ends\n"),    # no ground of its own
    (("inp", "inn", "out"), "{dev} {inp} {inn} {out} amp", AMP),                            # isolated differential input
    (("p", "q", "s", "t"), "{dev} {p} {q} {s} {t} xf", XF),                                # windings, no galvanic path
    (("a",), "{dev} {a} tie", ".subckt tie p\nR1 p gnd 1k\n.ends\n"),                     # gnd inside is ground
    (("a", "b"), "{dev} {a} {b} outer",                                                     # nested local def + .func
     ".subckt outer p q params: R=1k\n.func dbl(x) {2*x}\nX1 p q half\n.subckt half r s\nR1 r s {dbl(R)}\n.ends\n.ends\n"),
])
def test_legitimate_bodies_load_fully_validated(extra_root, pins, line, body):
    xpart(extra_root, "OKX", line, pins=pins, body=body)
    assert CATALOG.problems == [] and CATALOG.get("OKX").sim["electrical"]["_status"] == "full"


@pytest.mark.parametrize("body,why", [
    (".subckt s a b\nD1 a b nomodel\n.ends\n", "UNDEFINED-MODEL"),
    (".subckt s a b\nR1 a b {nope}\n.ends\n", "UNDEFINED-PARAM: instance xu1 of .subckt s: R1: 'nope'"),
    (".subckt s a b\nX1 a b t Q=1\n.subckt t p q params: R=1k\nR1 p q {R}\n.ends\n.ends\n", "UNKNOWN-INSTANCE-PARAM"),
    (".subckt s a b\nR1 a b 1k\nC1 m n 1n\n.ends\n", "NO-DC-PATH: node 'xu1.m'"),            # floating internal node
    (".subckt s a b\nL1 a b 1m\nL2 m n 1m\nR1 m n 1k\nK1 L1 L2 0.99\n.ends\n", "NO-DC-PATH"),  # isolated winding
    (".subckt s a b\nR1 a b 1k\nK1 L1 R1 0.9\n.ends\n", "UNDEFINED-ELEMENT"),
    (".subckt s a b\nL1 a b 1m\nR1 a b 1k\nK1 L1 R1 0.9\n.ends\n", "ngspice crashes on a resistor"),
    (".subckt s a b\nR1 a b 1k\nR1 a b 2k\n.ends\n", "DUP-NAME"),
    (".subckt s a b\nX1 a b s\n.ends\n", "SUBCKT-RECURSION"),
    (".subckt s a gnd\nR1 a gnd 1k\n.ends\n", "global ground"),
    (".subckt s a b\nE1 a b LAPLACE {V(a)} = {1/(1+s)}\n.ends\n", "UNSUPPORTED"),
    (".subckt s a b\nR9 a\n.ends\n", "MALFORMED: .subckt s: R9"),
    (".subckt s a b\nX1 a b nested\n.ends\n.subckt t p\n.subckt nested c d\nR1 c d 1k\n.ends\n.ends\n", "UNDEFINED-SUBCKT"),
])
def test_body_errors_reject_the_package(extra_root, body, why):
    xpart(extra_root, "BADX", "{dev} {a} {b} s", body=body)
    rejected("BADX", why)
    assert all(x["field"] == "sim.electrical.body" for x in sim_problems() if x["severity"] == "error"), sim_problems()


def test_isolated_winding_with_a_leak_resistor_loads(extra_root):
    xpart(extra_root, "XF1", "{dev} {a} {b} s", body=".subckt s a b\nL1 a b 1m\nL2 m n 1m\nR1 m n 1k\nRl n 0 1g\n"
                                                     "K1 L1 L2 0.99\n.ends\n")
    assert CATALOG.problems == [] and CATALOG.get("XF1").sim["electrical"]["_status"] == "full"


def test_the_run_deck_carries_connectivity_through_the_ports(extra_root):
    """Generated-deck validation: the same transformer part is fine with its secondary grounded and refused (by the
    core, before ngspice) when the user's wiring leaves the secondary floating; ngspice would hit a singular matrix
    (probed: 'K secondary through ports, floating outside'). The finding names the user's net, at top level."""
    xpart(extra_root, "XFMR", "{dev} {p} {q} {s} {t} xf", pins=("p", "q", "s", "t"), body=XF)

    def doc(ground_secondary):
        d = Document()
        d.add_part("V", dc="1"); d.add_part("R", R="10"); d.add_part("XFMR"); d.add_part("R", R="1k"); d.add_part("GND")
        wire(d, ("V1.p", "R1.a"), ("R1.b", "U1.p"), ("U1.q", "GND1.g"), ("V1.n", "GND1.g"), ("U1.s", "R2.a"),
             ("R2.b", "U1.t"))
        if ground_secondary: wire(d, ("U1.t", "GND1.g"))
        d.tstop, d.tstep = "20u", "1u"
        return d
    assert run_document(doc(True)).ok
    d = doc(False)
    res = run_document(d)
    assert not res.ok and res.status == "failed" and "NO-DC-PATH" in res.message, res.message
    floating = {d.nets()["U1.s"], d.nets()["U1.t"]}
    assert {(f["code"], f["scope"], f["nodes"][0]) for f in res.findings if f["severity"] == "error"} == {
        ("NO-DC-PATH", "", n) for n in floating}
