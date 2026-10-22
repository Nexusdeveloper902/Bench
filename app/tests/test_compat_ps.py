"""G10: `compat = "ps"` packages. A package opts in and its `defs` files are `.include`d (not inlined) under ngspice's
PSpice compatibility mode, requested by the deck comment `* bench-compat: ps`. The model text here is project-authored
PSpice syntax (IF(), LIMIT(), PARAMS:); oracles are closed forms (a clamp, a threshold), through the REAL path
Document -> netlist -> app.sim -> benchcore -> ngspice."""
import hashlib
import re

import benchcore
import pytest
from conftest import write_def
from app import catalog, electrical
from app.catalog import BUILTIN_ROOT, CATALOG
from test_library_batch_c import Circuit
from test_subckt_packages import p, rejected, shared, xpart

PSLIB = """* project-authored PSpice-syntax test model (MIT)
.SUBCKT PSLIM in out PARAMS: LO=-1 HI=1
E1 x 0 VALUE = { LIMIT(V(in), {LO}, {HI}) }
R1 x out 1
R2 out 0 1MEG
.ENDS
.SUBCKT PSCMP inp inn out PARAMS: VH=1 VL=0 TH=0
E1 x 0 VALUE = { IF(V(inp,inn) > {TH}, {VH}, {VL}) }
R1 x out 1
R2 out 0 1MEG
.ENDS
"""
LIM = "{dev} {i} {o} PSLIM LO={LO} HI={HI}"
LIM_PARAMS = (p("LO", "-1", "V"), p("HI", "1", "V"))


def clamp_part(root, id="PS_CLAMP", reload=True, **elec):
    return xpart(root, id, LIM, pins=("i", "o"), params=LIM_PARAMS, files={"ps.lib": PSLIB}, reload=reload,
                 defs=["ps.lib"], compat="ps", **elec)


def build(vin, lo="-1", hi="2", kind="PS_CLAMP"):
    c = Circuit()
    v = c.vdc(vin, "")
    x = c.add(kind, LO=lo, HI=hi)
    c.w(f"{v}.p", f"{x}.i")
    c.r("10k", f"{x}.o", "0")
    return c, x


@pytest.fixture
def ps_root(extra_root):
    clamp_part(extra_root)
    assert CATALOG.problems == [] and "PS_CLAMP" in CATALOG.defs, CATALOG.problems
    return extra_root


@pytest.mark.parametrize("vin,want", [(3.0, 2.0), (-4.0, -1.0), (0.5, 0.5)])
def test_ps_model_runs_and_clamps_like_the_closed_form(ps_root, vin, want):
    c, x = build(vin)
    c.run()
    assert c.v(f"{x}.o") == pytest.approx(want * 10e3 / (10e3 + 1), abs=1e-3)      # 1 ohm out of the model, 10k load


def test_deck_includes_the_file_once_with_one_marker(ps_root):
    c, x = build(3.0)
    y = c.add("PS_CLAMP", LO="-2", HI="2")
    c.w(f"{y}.i", f"{x}.i")
    c.r("10k", f"{y}.o", "0")
    models = c.d.netlist()["models"]
    inc = [m for m in models if m.startswith(".include")]
    assert models.count(electrical.COMPAT_PS) == 1 and len(inc) == 1
    path = re.fullmatch(r'\.include "(.+)"', inc[0]).group(1)
    assert path == str((ps_root / "ps_clamp" / "ps.lib").resolve())
    assert not any("LIMIT" in m.upper() for m in models if not m.startswith("*")), "the text is included, not inlined"
    assert models.index(electrical.COMPAT_PS) < models.index(inc[0])
    assert sum(m.startswith("* bench-def") for m in models) == 1
    c.run()                                                  # two instances, one file, both clamp


def test_validator_does_not_reject_the_deck_and_says_it_is_limited(ps_root):
    c, x = build(3.0)
    res = c.run().res
    assert not [f for f in res.findings if f["severity"] == "error"]
    d = CATALOG.get("PS_CLAMP")
    assert any("ps mode" in s for s in d.limitations)        # the package says its text is not analysed


def test_no_opt_in_means_the_netlist_is_unchanged(ps_root):
    c = Circuit()
    v = c.vdc(5, "")
    r = c.r("1k", "", "0")
    c.w(f"{v}.p", f"{r}.a")
    nl = c.d.netlist()
    assert electrical.COMPAT_PS not in nl["models"] and not any(".include" in m for m in nl["models"])
    assert not any(electrical.COMPAT_PS in line for line in nl["lines"])


def test_mode_is_requested_per_deck_and_does_not_leak_to_the_next_run(ps_root):
    c, x = build(3.0)
    c.run()                                                  # ps mode on for this deck
    plain = Circuit()
    v = plain.vdc(5, "")
    r1 = plain.r("1k", "", "")
    plain.w(f"{v}.p", f"{r1}.a")
    r2 = plain.r("1k", f"{r1}.b", "0")
    plain.run()
    assert plain.v(f"{r1}.b") == pytest.approx(2.5)
    # the same PSpice file with no marker: ngspice silently evaluates LIMIT() as 0 (a wrong "success"), so the mode
    # really was unset again, and the opt-in is what makes the model right
    out = []
    for marker in (False, True):
        s = benchcore.Simulation("t")
        s.build_circuit(["V3 big 0 DC 3", "X2 big o2 PSLIM LO=-1 HI=2", "R10 o2 0 10k"])
        s.add_model(f'.include "{ps_root / "ps_clamp" / "ps.lib"}"')
        if marker: s.add_model(electrical.COMPAT_PS)
        s.configure(tstep=1e-5, tstop=1e-4).observe("v(o2)")
        r = s.run()["series"]["v(o2)"][-1]
        out.append(r[1] if isinstance(r, tuple) else r)
    assert out[0] == 0.0 and out[1] == pytest.approx(2.0, abs=1e-3)


def test_ps_mode_does_not_change_native_main_deck_expressions(ps_root):
    """Probed, not assumed: 17 native B-source forms (log, ln, **, ^, ternary, pwr, u, ...) give the same values with and
    without ps mode, so a Bench-authored deck is not altered by one opted-in part. (limit() IS translated in the main
    deck too, which is why the validator still refuses it there.)"""
    exprs = ["log(100)", "ln(100)", "log10(100)", "V(big)>2 ? 5 : 7", "pwr(2,3)", "2**3", "2^3", "sgn(-3)",
             "floor(2.7)+ceil(2.2)", "min(1,2)+max(1,2)", "abs(-2)+sqrt(16)+exp(0)+atan(1)*4", "u(V(big)-2)+uramp(2)"]
    body = ["V3 big 0 DC 3"] + [f"B{i} n{i} 0 V={e}" for i, e in enumerate(exprs)] + [f"R{i} n{i} 0 1k" for i in range(len(exprs))]
    obs = [f"v(n{i})" for i in range(len(exprs))]

    def run(ps):
        s = benchcore.Simulation("t")
        s.build_circuit(body)
        s.add_model(electrical.COMPAT_PS) if ps else None
        s.configure(tstep=1e-5, tstop=1e-4).observe(*obs)
        r = s.run()
        return [r["series"][o][-1][1] if isinstance(r["series"][o][-1], tuple) else r["series"][o][-1] for o in obs]
    assert run(True) == run(False)


def test_models_path_needs_the_manifest_and_emits_an_include(extra_root):
    shared(extra_root, "vendor/ps.lib", PSLIB)
    xpart(extra_root, "PS_SHARED", LIM, pins=("i", "o"), params=LIM_PARAMS, defs=["models:vendor/ps.lib"], compat="ps")
    assert CATALOG.problems == [], CATALOG.problems
    c, x = build(3.0, kind="PS_SHARED")
    inc = [m for m in c.d.netlist()["models"] if m.startswith(".include")]
    assert inc == [f'.include "{(extra_root / "_models" / "vendor" / "ps.lib").resolve()}"']
    c.run()
    assert c.v(f"{x}.o") == pytest.approx(2.0 * 10e3 / (10e3 + 1), abs=1e-3)


def test_comparator_if_and_params_syntax(extra_root):
    xpart(extra_root, "PS_CMP", "{dev} {p} {n} {o} PSCMP VH=3 VL=1 TH=0.5", pins=("p", "n", "o"),
          files={"ps.lib": PSLIB}, defs=["ps.lib"], compat="ps")
    assert CATALOG.problems == [], CATALOG.problems
    for vin, want in ((2.0, 3.0), (0.2, 1.0)):
        c = Circuit()
        v = c.vdc(vin, "")
        x = c.add("PS_CMP")
        c.w(f"{v}.p", f"{x}.p")
        c.w(f"{x}.n", "0")
        c.r("10k", f"{x}.o", "0")
        c.run()
        assert c.v(f"{x}.o") == pytest.approx(want * 10e3 / (10e3 + 1), abs=1e-3)


@pytest.mark.parametrize("mutate,needle", [
    ({"compat": "pspice"}, "must be \"ps\""),
    ({"compat": "ps", "defs": None}, "needs \"defs\" files"),
    ({"compat": "ps", "body": ".subckt b a z\nR1 a z 1\n.ends\n"}, "no \"body\""),
])
def test_compat_misuse_is_rejected(extra_root, mutate, needle):
    elec = {"defs": ["ps.lib"], "compat": "ps"} | mutate
    elec = {k: v for k, v in elec.items() if v is not None}
    xpart(extra_root, "PS_BAD", LIM, pins=("i", "o"), params=LIM_PARAMS, files={"ps.lib": PSLIB}, **elec)
    rejected("PS_BAD", needle)


def test_ps_file_is_still_held_to_the_directive_allowlist(extra_root):
    """An included file never gets to run `.control` (shell access) or add stray directives."""
    evil = PSLIB + ".control\nshell echo hi\n.endc\n"
    xpart(extra_root, "PS_EVIL", LIM, pins=("i", "o"), params=LIM_PARAMS, files={"ps.lib": evil}, defs=["ps.lib"],
          compat="ps")
    rejected("PS_EVIL", ".control")


def test_sha_is_the_deck_trace_of_the_file(ps_root):
    c, _ = build(1.0)
    sha = hashlib.sha256((ps_root / "ps_clamp" / "ps.lib").read_bytes()).hexdigest()[:8]
    assert any(m.startswith("* bench-def") and m.endswith(sha) for m in c.d.netlist()["models"])
