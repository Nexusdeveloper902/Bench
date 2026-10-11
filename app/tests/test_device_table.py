"""The electrical device table (app/electrical.py DEVICES): every top-level letter loads from a test-local
component.toml, is refused with a wrong pin count, model type/level or current, renders lines the core validator
accepts (parity with benchcore/src/validate.rs DEVICES), and the new letters simulate to hand-calculated values
through Document -> netlist -> app.sim -> benchcore -> ngspice."""
import pytest
import benchcore
from conftest import write_def
from app import catalog, electrical
from app.catalog import CATALOG
from app.model import Document
from app.sim import run_document


def p(key, default):
    return {"key": key, "label": key, "unit": "", "default": default}


# letter -> (pins, line, models, params); one valid top-level part per letter in DEVICES
EXAMPLES = {
    "R": ("ab", "{dev} {a} {b} 1k", [], []),
    "C": ("ab", "{dev} {a} {b} 1n", [], []),
    "L": ("ab", "{dev} {a} {b} 1u", [], []),
    "V": ("pn", "{dev} {p} {n} DC {v}", [], [p("v", "1")]),
    "I": ("pn", "{dev} {p} {n} DC 1m", [], []),
    "B": ("pn", "{dev} {p} {n} V={v}", [], [p("v", "1.5")]),
    "E": ("o n c m", "{dev} {o} {n} {c} {m} {gain}", [], [p("gain", "3")]),
    "G": ("o n c m", "{dev} {o} {n} {c} {m} {gm}", [], [p("gm", "1m")]),
    "T": ("a b c d", "{dev} {a} {b} {c} {d} Z0=50 TD=1n", [], []),
    "D": ("ak", "{dev} {a} {k} dm", [".model dm D(Is=1e-14)"], []),
    "S": ("a b c m", "{dev} {a} {b} {c} {m} swm", [".model swm SW(vt=1 ron=1 roff=1meg)"], []),
    "Q": ("cbe", "{dev} {c} {b} {e} qm", [".model qm NPN(Bf=100)"], []),
    "J": ("dgs", "{dev} {d} {g} {s} jm", [".model jm NJF(Beta=1e-4 Vto=-2)"], []),
    "M": ("dgsb", "{dev} {d} {g} {s} {b} nm W=100u L=10u", [".model nm NMOS(level=1 Vto=1 Kp=2e-5)"], []),
    "Z": ("dgs", "{dev} {d} {g} {s} zm", [".model zm NMF(level=1 vto=-2 beta=2.5m b=0.3 alpha=2 lambda=0)"], []),
}
VDMOS = ("dgs", "{dev} {d} {g} {s} vd", [".model vd VDMOS(Vto=1 Kp=1e-4 lambda=0)"], [])


def section(letter, ex, **extra):
    pins, line, models, params = ex
    sec = {"device": letter, "line": line, **extra}
    if models: sec["models"] = list(models)
    return pins.split() if " " in pins else list(pins), params, sec


def define(root, id, letter, ex, reload=True, **extra):
    pins, params, sec = section(letter, ex, **extra)

    def m(d):
        d["designator"] = letter
        d["pins"] = [{"id": x, "at": [10 * i, 0]} for i, x in enumerate(pins)]
        d["params"] = params
        d["sim"] = {"electrical": sec}
    write_def(root, id, mutate=m, reload=reload)


def sim_problems(field=None):
    return [x for x in CATALOG.problems if x["code"] == "DEF-SIM" and field in (None, x["field"])]


def test_examples_cover_the_table():
    assert set(EXAMPLES) | {"X"} == set(electrical.DEVICES)     # X needs definition text: test_subckt_packages.py


@pytest.mark.parametrize("letter", sorted(EXAMPLES) + ["VDMOS"])
def test_device_table_parity(extra_root, letter):
    """Every Python letter renders a line the Rust validator reads with the same arity and model rules: no
    MALFORMED / UNSUPPORTED / UNDEFINED-MODEL / UNKNOWN-KIND (DESIGN 4.2; no new PyO3 API needed)."""
    ltr, ex = ("M", VDMOS) if letter == "VDMOS" else (letter, EXAMPLES[letter])
    define(extra_root, "PART", ltr, ex)
    assert CATALOG.problems == []
    c = CATALOG.get("PART")
    lines, models = electrical.emit(c.sim["electrical"], f"{ltr}1", {x: f"n{i + 1}" for i, x in enumerate(c.terminals)},
                                    c.values(c.defaults()))
    found = benchcore.Simulation("parity").build_circuit(lines + models).validate()
    assert not [f for f in found if f["code"] in ("MALFORMED", "UNSUPPORTED", "UNDEFINED-MODEL", "UNKNOWN-KIND")], found


@pytest.mark.parametrize("letter", sorted(EXAMPLES))
def test_each_letter_loads_and_its_current_is_exact(extra_root, letter):
    spec = electrical.DEVICES[letter]
    define(extra_root, "PART", letter, EXAMPLES[letter], **({"current": spec.current} if spec.current else {}))
    assert CATALOG.problems == [] and "PART" in CATALOG.defs
    for wrong in ("@{dev_lc}[id]", "@{dev_lc}[i]", "@{dev_lc}[cd]", "i({dev_lc})", "@{dev_lc}[ib]"):
        if wrong == spec.current: continue
        define(extra_root, "PART", letter, EXAMPLES[letter], current=wrong)
        assert sim_problems("sim.electrical.current") and "PART" not in CATALOG.defs, (letter, wrong)


@pytest.mark.parametrize("letter", sorted(EXAMPLES))
def test_each_letter_rejects_a_wrong_pin_count(extra_root, letter):
    pins, line, models, params = EXAMPLES[letter]
    pins = pins.split() if " " in pins else list(pins)
    drop = pins[-1]
    define(extra_root, "PART", letter, (" ".join(pins[:-1]) + " ", line.replace(f" {{{drop}}}", ""), models, params))
    assert sim_problems("sim.electrical.line") and "PART" not in CATALOG.defs
    assert any("terminals" in x["message"] for x in sim_problems()), CATALOG.problems


@pytest.mark.parametrize("letter,card,why", [
    ("D", ".model dm NPN", "needs D"),
    ("D", ".model dm D(level=2)", "level 2"),
    ("Q", ".model qm NPN(level=6)", "level 6"),                # Mextram: absent from this build
    ("Q", ".model qm LPNP", "needs NPN or PNP"),
    ("J", ".model jm NJF(level=3)", "level 3"),
    ("M", ".model nm NMOS(level=60)", "level 60"),             # SOI3 aborts the process
    ("M", ".model nm VDMOS", "VDMOS takes 3 pins"),
    ("M", ".model nm NMOS(level={lv})", "not a number"),
    ("S", ".model swm VSWITCH(ron=1)", "needs SW"),
    ("Z", ".model zm NJF", "needs NMF or PMF"),
    ("Z", ".model zm NMF(level=7)", "level 7"),
])
def test_wrong_model_type_or_level_rejected(extra_root, letter, card, why):
    pins, line, _, params = EXAMPLES[letter]
    define(extra_root, "PART", letter, (pins, line, [card], params))
    assert any(why in x["message"] for x in sim_problems("sim.electrical.line")), CATALOG.problems
    assert "PART" not in CATALOG.defs


def test_three_pin_m_needs_vdmos(extra_root):
    define(extra_root, "VD", "M", VDMOS)
    assert CATALOG.problems == []
    define(extra_root, "VD", "M", ("dgs", VDMOS[1], [".model vd NMOS"], []))
    assert any("3 only with a VDMOS model" in x["message"] for x in sim_problems()), CATALOG.problems


# ---------------------------------------------------------------- end to end, against hand values
@pytest.fixture
def root(extra_root):
    yield extra_root
    catalog.reload()


def run(d, tstop="20u"):
    d.tstop, d.tstep = tstop, "1u"
    res = run_document(d)
    assert res.ok, res.message
    return res


def final(res, name):
    return res.stats(name)["final"]


def wire(d, *pairs):
    for a, b in pairs: d.connect(a, b)


@pytest.mark.parametrize("vc,closed", [("2", True), ("0", False)])
def test_switch_follows_its_control_voltage(root, vc, closed):
    define(root, "SWV", "S", EXAMPLES["S"], current="@{dev_lc}[i]")
    d = Document()
    d.add_part("V", dc="2"); d.add_part("V", dc=vc); d.add_part("R", R="1k"); d.add_part("SWV"); d.add_part("GND")
    wire(d, ("V1.p", "S1.a"), ("S1.b", "R1.a"), ("R1.b", "GND1.g"), ("V2.p", "S1.c"), ("S1.m", "GND1.g"),
         ("V1.n", "GND1.g"), ("V2.n", "GND1.g"))
    res = run(d)
    r = 1.0 if closed else 1e6                                   # ron / roff of the card
    assert final(res, f"V({d.nets()['S1.b']})") == pytest.approx(2 * 1000 / (1000 + r), rel=1e-6)
    assert final(res, "I(S1)") == pytest.approx(2 / (1000 + r), rel=1e-6)


def controlled(root, letter, gain):
    define(root, "CS", letter, (EXAMPLES[letter][0], EXAMPLES[letter][1], [], [p(EXAMPLES[letter][3][0]["key"], gain)]),
           current="@{dev_lc}[i]")
    d = Document()
    d.add_part("V", dc="2"); d.add_part("R", R="1k"); d.add_part("CS"); d.add_part("GND")
    wire(d, ("V1.p", f"{letter}1.c"), (f"{letter}1.m", "GND1.g"), (f"{letter}1.o", "R1.a"), ("R1.b", "GND1.g"),
         (f"{letter}1.n", "GND1.g"), ("V1.n", "GND1.g"))
    return d, run(d)


def test_vcvs_gain(root):
    d, res = controlled(root, "E", "3")
    assert final(res, f"V({d.nets()['E1.o']})") == pytest.approx(6.0, abs=1e-6)      # gain * V(c)
    assert abs(final(res, "I(E1)")) == pytest.approx(6e-3, rel=1e-6)                 # into the 1k load


def test_vccs_transconductance(root):
    d, res = controlled(root, "G", "1m")
    # G drives gm*V(c) = 2 mA from o through the source to n, so the 1k load sees -2 V
    assert final(res, f"V({d.nets()['G1.o']})") == pytest.approx(-2.0, abs=1e-6)
    assert final(res, "I(G1)") == pytest.approx(2e-3, rel=1e-6)


def test_behavioural_source(root):
    define(root, "BV", "B", EXAMPLES["B"], current="@{dev_lc}[i]")
    d = Document()
    d.add_part("BV", v="1.5"); d.add_part("R", R="1k"); d.add_part("GND")
    wire(d, ("B1.p", "R1.a"), ("R1.b", "GND1.g"), ("B1.n", "GND1.g"))
    res = run(d)
    assert final(res, "V(B1)") == pytest.approx(1.5, abs=1e-9) and abs(final(res, "I(B1)")) == pytest.approx(1.5e-3)


def test_matched_transmission_line_passes_dc(root):
    define(root, "TL", "T", EXAMPLES["T"])
    d = Document()
    d.add_part("V", dc="2"); d.add_part("R", R="50"); d.add_part("TL"); d.add_part("GND")
    wire(d, ("V1.p", "T1.a"), ("T1.b", "GND1.g"), ("T1.c", "R1.a"), ("T1.d", "GND1.g"), ("R1.b", "GND1.g"),
         ("V1.n", "GND1.g"))
    res = run(d)
    assert final(res, "V(R1)") == pytest.approx(2.0, abs=1e-6)


def test_mesfet_statz_saturation(root):
    define(root, "MES", "Z", EXAMPLES["Z"], current="@{dev_lc}[cd]")
    d = Document()
    d.add_part("V", dc="10"); d.add_part("R", R="1k"); d.add_part("MES"); d.add_part("GND")
    wire(d, ("V1.p", "R1.a"), ("R1.b", "Z1.d"), ("Z1.g", "GND1.g"), ("Z1.s", "GND1.g"), ("V1.n", "GND1.g"))
    res = run(d)
    idrain = 2.5e-3 * 2 ** 2 / (1 + 0.3 * 2)       # Statz: beta Vov^2 / (1 + b Vov) = 6.25 mA, Vds = 3.75 > 3/alpha
    assert final(res, "I(Z1)") == pytest.approx(idrain, rel=1e-4)
    assert final(res, f"V({d.nets()['Z1.d']})") == pytest.approx(10 - idrain * 1e3, abs=1e-3)


def test_vdmos_drain_current_follows_square_law(root):
    define(root, "VD", "M", VDMOS, current="@{dev_lc}[id]")
    ids = {}
    for vg in ("3", "5"):
        d = Document()
        d.add_part("V", dc="100"); d.add_part("V", dc=vg); d.add_part("R", R="10"); d.add_part("VD"); d.add_part("GND")
        wire(d, ("V1.p", "R1.a"), ("R1.b", "M1.d"), ("V2.p", "M1.g"), ("M1.s", "GND1.g"), ("V1.n", "GND1.g"),
             ("V2.n", "GND1.g"))
        assert d.netlist()["lines"][-1].split()[:5] == ["M1", d.nets()["M1.d"], d.nets()["M1.g"], "0", "vd"]
        ids[vg] = final(run(d), "I(M1)")
    # Kp/2 (Vgs - Vto)^2: 0.2 mA and 0.8 mA; VDMOS subthreshold smoothing leaves < 0.3 % at these Vov
    assert ids["3"] == pytest.approx(0.5e-4 * 2 ** 2, rel=0.01)
    assert ids["5"] == pytest.approx(0.5e-4 * 4 ** 2, rel=0.01)


def test_package_that_aborts_ngspice_does_not_break_later_runs(root):
    """L7 through the app: `{{nope}}` renders a literal {nope} (an undefined ngspice parameter), which makes
    libngspice abort. Since task 4 the core's static gate refuses it first (UNDEFINED-PARAM), so the engine is not
    even touched; either way the run fails with the reason and the next document still runs. The reload itself is
    covered by correctness.rs t60/t61 and test_api.py::test_poisoned_engine_recovers."""
    define(root, "BADD", "D", ("ak", "{dev} {a} {k} dbad", [".model dbad D(Is={{nope}})"], []))
    assert CATALOG.problems == []
    bad = Document()
    bad.add_part("V", dc="5"); bad.add_part("R", R="1k"); bad.add_part("BADD"); bad.add_part("GND")
    wire(bad, ("V1.p", "R1.a"), ("R1.b", "D1.a"), ("D1.k", "GND1.g"), ("V1.n", "GND1.g"))
    bad.tstop, bad.tstep = "20u", "1u"
    res = run_document(bad)
    assert not res.ok and "nope" in res.message.lower(), res.message
    good = Document()
    good.add_part("V", dc="4"); good.add_part("R", R="1k"); good.add_part("R", R="3k"); good.add_part("GND")
    wire(good, ("V1.p", "R1.a"), ("R1.b", "R2.a"), ("R2.b", "GND1.g"), ("V1.n", "GND1.g"))
    assert final(run(good), "V(R2)") == pytest.approx(3.0, abs=1e-6)
