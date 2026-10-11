"""The multi-terminal design generalizes: PNP, 4-pin MOSFET and JFET defined ONLY by test-local component.toml files
(nothing shipped), run through Document -> netlist -> app.sim -> benchcore -> ngspice and checked against hand calculations."""
import pytest
from conftest import write_def
from app.model import Document
from app.components import ModelError
from app.sim import run_document


def pins(*ids):
    return [{"id": p, "at": [10 * i, 0]} for i, p in enumerate(ids)]


def part(root, id, dev, ids, line, model, cur, params=()):
    def m(d):
        d["designator"] = dev
        d["pins"] = pins(*ids); d["params"] = list(params)
        d["sim"] = {"electrical": {"device": dev, "line": line, "models": [model], "current": cur}}
    write_def(root, id, mutate=m, reload=False)


def mos(root, id="NMOS4", model=".model nmod NMOS(Vto=1 Kp=2e-5 Lambda=0)"):
    part(root, id, "M", "dgsb", "{dev} {d} {g} {s} {b} nmod W={W} L={L}", model, "@{dev_lc}[id]",
         [{"key": "W", "label": "W", "unit": "", "default": "100u"}, {"key": "L", "label": "L", "unit": "", "default": "10u"}])


@pytest.fixture
def root(extra_root):
    from app import catalog
    yield extra_root
    catalog.reload()


def load(root):
    from app import catalog
    catalog.reload([catalog.BUILTIN_ROOT, root])


def final(res, n):
    return res.stats(n)["final"]


def test_pnp_high_side_common_emitter_kvl(root):
    part(root, "PNP3", "Q", "cbe", "{dev} {c} {b} {e} pmod", ".model pmod PNP(Is=1e-14 Bf=100)", "@{dev_lc}[ic]")
    load(root)
    d = Document()
    d.add_part("V", dc="5"); d.add_part("R", R="430k"); d.add_part("R", R="1k"); d.add_part("PNP3"); d.add_part("GND")
    for a, b in (("V1.p", "Q1.e"), ("Q1.b", "R1.a"), ("R1.b", "GND1.g"), ("Q1.c", "R2.a"), ("R2.b", "GND1.g"),
                 ("V1.n", "GND1.g")):
        d.connect(a, b)
    d.tstop, d.tstep = "100u", "10u"
    n = d.nets()
    assert len({n["Q1.c"], n["Q1.b"], n["Q1.e"]}) == 3
    res = run_document(d); assert res.ok, res.message
    ib = final(res, "I(R1)"); vc = final(res, f"V({n['Q1.c']})"); vb = final(res, f"V({n['Q1.b']})")
    ic = final(res, "I(R2)")
    assert ic == pytest.approx(vc / 1e3, rel=1e-3)                      # KVL on Rc
    assert ib == pytest.approx(vb / 430e3, rel=1e-3)                    # KVL on Rb
    assert 0.55 < 5 - vb < 0.8                                          # Veb of a forward junction
    assert ic == pytest.approx(100 * ib, rel=0.02) and vc < vb          # forward-active, beta = Bf
    sig = next(s for s in res.signals if s["name"] == "I(Q1)")
    assert sig["pins"] == ["Q1.c"] and sig["vector"] == "@q1[ic]"
    assert abs(final(res, "I(Q1)")) == pytest.approx(ic, rel=1e-3)


def cs_doc(kind="NMOS4", gate_wired=True, vg="3"):
    d = Document()
    d.add_part("V", dc="10"); d.add_part("V", dc=vg); d.add_part("R", R="5k"); d.add_part(kind); d.add_part("GND")
    w = [("V1.p", "R1.a"), ("R1.b", "M1.d"), ("M1.s", "GND1.g"), ("M1.b", "GND1.g"), ("V1.n", "GND1.g"), ("V2.n", "GND1.g")]
    if gate_wired: w.append(("V2.p", "M1.g"))
    for a, b in w: d.connect(a, b)
    d.tstop, d.tstep = "100u", "10u"
    return d


def test_four_pin_nmos_common_source_saturation(root):
    mos(root); load(root)
    d = cs_doc(); n = d.nets()
    toks = d.netlist()["lines"][-1].split()                              # d g s b order, then model and W/L
    assert toks[:6] == ["M1", n["M1.d"], n["M1.g"], "0", "0", "nmod"] and len({n["M1.d"], n["M1.g"], "0"}) == 3
    res = run_document(d); assert res.ok, res.message
    idrain = 0.5 * 2e-5 * 10 * (3 - 1) ** 2        # Kp/2 * W/L * (Vgs-Vto)^2 = 0.4 mA
    vd = final(res, f"V({n['M1.d']})")
    assert vd == pytest.approx(10 - idrain * 5e3, rel=0.02) and vd > 3 - 1       # saturated: Vds > Vov
    assert final(res, "I(M1)") == pytest.approx(idrain, rel=0.02)
    assert final(res, "I(R1)") == pytest.approx(idrain, rel=0.02)
    sig = next(s for s in res.signals if s["name"] == "I(M1)")
    assert sig["pins"] == ["M1.d"] and sig["vector"] == "@m1[id]"


def test_jfet_three_pin_if_default_njf_behaves(root):
    part(root, "NJF3", "J", "dgs", "{dev} {d} {g} {s} jmod", ".model jmod NJF(Beta=1e-4 Vto=-2)", "@{dev_lc}[id]")
    load(root)
    d = Document()
    d.add_part("V", dc="10"); d.add_part("R", R="5k"); d.add_part("NJF3"); d.add_part("GND")
    for a, b in (("V1.p", "R1.a"), ("R1.b", "J1.d"), ("J1.g", "GND1.g"), ("J1.s", "GND1.g"), ("V1.n", "GND1.g")):
        d.connect(a, b)
    d.tstop, d.tstep = "100u", "10u"
    res = run_document(d); assert res.ok, res.message
    idrain = 1e-4 * 2 ** 2                                              # Beta (Vgs-Vto)^2 at Vgs=0 = 0.4 mA, Vds=8 > 2
    assert final(res, "I(R1)") == pytest.approx(idrain, rel=0.02)
    assert final(res, "I(J1)") == pytest.approx(idrain, rel=0.02)


def test_unwired_mosfet_gate_blocks_run_with_no_dc_path(root):
    mos(root); load(root)
    d = cs_doc(gate_wired=False)
    res = run_document(d)
    assert not res.ok and "NO-DC-PATH" in res.message
    assert "singular" not in res.message.lower() and "Traceback" not in res.message


def test_same_model_name_different_params_conflicts(root):
    from app import catalog
    mos(root, "NMOS_A"); mos(root, "NMOS_B", ".model nmod NMOS(Vto=2 Kp=2e-5 Lambda=0)"); load(root)
    assert [(p["code"], p["id"]) for p in catalog.CATALOG.problems] == [("DEF-SIM", "NMOS_B")]   # rejected at load
    assert "nmod" in catalog.CATALOG.problems[0]["message"] and "NMOS_B" not in catalog.CATALOG.defs


def test_same_model_name_instances_with_different_params_raise(root):
    mos(root, "NMOS_V", ".model nmod NMOS(Vto={VT} Kp=2e-5 Lambda=0)")
    f = root / "nmos_v" / "component.toml"
    f.write_text(f.read_text().replace("[[params]]", '[[params]]\nkey = "VT"\nlabel = "Vt"\nunit = ""\ndefault = "1"\n[[params]]', 1))
    load(root)
    d = Document(); d.add_part("NMOS_V"); d.add_part("NMOS_V", VT="2")
    with pytest.raises(ModelError, match="model 'nmod' gets different parameters"):
        d.netlist()
