"""Library batch B (task 6): diodes, BJTs, MOSFETs, JFETs, thyristors and the IGBT.

Every package loads and validates (validator status `full`). The behaviour is checked through the REAL path
(Document -> netlist -> app.sim -> benchcore -> ngspice) against datasheet windows (named parts, `reference`), closed
forms (generic parts, `analytic`) or the behaviour the subcircuit is built to show (thyristors and the IGBT, `smoke`).

The named-part models are project-authored fits to published datasheet values, NOT vendor SPICE models, so a pass
here means "inside the datasheet windows the fit targeted", not "equal to the manufacturer's model" or to a device.
The datasheet limits used below are in each package's provenance notes and limitations."""
import math

import pytest

from app import electrical
from app.catalog import BUILTIN_ROOT, CATALOG
from app.model import Document
from app.sim import run_document

VT = 0.025865          # kT/q at ngspice's default 27 C

NEW = {   # id -> (model_kind, verification)
    "D_GENERIC": ("model", "analytic"), "D_1N400X": ("model", "reference"), "D_UF4007": ("model", "reference"),
    "D_1N5819": ("model", "reference"), "D_BAT54": ("model", "reference"), "ZENER": ("project-subckt", "reference"),
    "TVS_UNI": ("equivalent", "analytic"), "TVS_BI": ("equivalent", "analytic"), "VARACTOR": ("model", "analytic"),
    "LED_COLOR": ("model", "analytic"),
    "Q_NPN": ("model", "analytic"), "Q_PNP": ("model", "analytic"), "Q_2N3904": ("model", "reference"),
    "Q_2N3906": ("model", "reference"), "Q_BC547B": ("model", "reference"), "Q_BC557B": ("model", "reference"),
    "Q_TIP31C": ("model", "reference"), "Q_TIP32C": ("model", "reference"), "Q_2N3055": ("model", "reference"),
    "Q_TIP120": ("project-subckt", "reference"),
    "NMOS": ("model", "analytic"), "PMOS": ("model", "analytic"), "M_2N7000": ("model", "reference"),
    "M_BS170": ("model", "reference"), "M_BSS138": ("model", "reference"), "M_IRF540N": ("model", "reference"),
    "M_IRF9540N": ("model", "reference"), "M_IRLZ44N": ("model", "reference"),
    "J_J201": ("model", "reference"), "J_2N5457": ("model", "reference"), "J_2N5460": ("model", "reference"),
    "SCR": ("equivalent", "smoke"), "TRIAC": ("equivalent", "smoke"), "DIAC": ("equivalent", "smoke"),
    "IGBT": ("equivalent", "smoke"),
}


# ---------------------------------------------------------------- helpers
def wire(d, *pairs):
    for a, b in pairs: d.connect(a, b)


def run(d, tstop="100u", tstep="10u"):
    d.tstop, d.tstep = tstop, tstep
    res = run_document(d)
    assert res.ok, res.message + "\n" + (res.details or "")
    return res


def final(res, name):
    return res.stats(name)["final"]


def volt(d, res, pin):
    net = d.nets()[pin]
    return 0.0 if net == "0" else res.get(f"V({net})")[-1][1]


def forced(part, current, params=None, reverse=False, tstop="100u", tstep="10u", a="a", k="k"):
    """Force `current` through a two-terminal diode-like part and return the voltage across it (anode - cathode)."""
    d = Document()
    p, i, g = d.add_part(part, **(params or {})), d.add_part("I", dc=str(current)), d.add_part("GND")
    if not reverse:
        wire(d, (f"{i}.p", f"{g}.g"), (f"{i}.n", f"{p}.{a}"), (f"{p}.{k}", f"{g}.g"))
    else:
        wire(d, (f"{i}.p", f"{g}.g"), (f"{i}.n", f"{p}.{k}"), (f"{p}.{a}", f"{g}.g"))
    res = run(d, tstop, tstep)
    return abs(volt(d, res, f"{p}.{k if reverse else a}") - volt(d, res, f"{p}.{a if reverse else k}"))


def sec(id):
    return CATALOG.get(id).sim["electrical"]


# ---------------------------------------------------------------- every package loads and validates
@pytest.mark.parametrize("id", sorted(NEW))
def test_package_loads_validates_and_records_its_kind(id):
    c = CATALOG.get(id)
    s = c.sim["electrical"]
    assert CATALOG.problems == [] and c.simulated and c.readiness == "simulated" and c.path.is_relative_to(BUILTIN_ROOT)
    assert c.category == "Electrical" and (s["model_kind"], s["verification"]) == NEW[id]
    assert s["provenance"]["license"] == "MIT" and s["provenance"]["source"]
    if s["device"] == "X":
        assert s["_status"] == "full" and not s["_limits"], id
    if s["verification"] == "reference":       # a datasheet-fitted card must say where the datasheet is
        assert s["provenance"]["url"].startswith("https://") and "datasheet" in s["provenance"]["source"]
        assert any("not a vendor SPICE model" in x or "not a vendor" in x for x in c.limitations), id
    assert not any(w in " ".join(c.limitations).lower() for w in ("validated against", "manufacturer-accurate"))


def test_catalog_grew_by_exactly_these_parts():
    assert set(NEW) <= set(CATALOG.defs) and len(NEW) == 35 and len(CATALOG.defs) == 57 + len(NEW)


def test_shared_model_files_have_matching_records_and_are_emitted_once():
    d = Document()
    a, b = d.add_part("LED_COLOR", color="blue"), d.add_part("LED_COLOR", color="ir")
    g, v1, r1, r2 = d.add_part("GND"), d.add_part("V", dc="5"), d.add_part("R", R="470"), d.add_part("R", R="470")
    wire(d, (f"{v1}.p", f"{r1}.a"), (f"{v1}.p", f"{r2}.a"), (f"{r1}.b", f"{a}.a"), (f"{r2}.b", f"{b}.a"),
         (f"{a}.k", f"{g}.g"), (f"{b}.k", f"{g}.g"), (f"{v1}.n", f"{g}.g"))
    nl = d.netlist()
    assert len([m for m in nl["models"] if m.startswith("* bench-def") and "led.lib" in m]) == 1
    res = run(d)
    # the blue LED drops about 3.1 V and the IR one 1.25 V at 20 mA: 470 ohm gives different currents on one rail
    assert volt(d, res, f"{a}.a") > 2.7 and volt(d, res, f"{b}.a") < 1.5


# ---------------------------------------------------------------- diodes: forward voltage
@pytest.mark.parametrize("part, params, amps, lo, hi", [
    # D_1N400X: Vishay maximum VF = 1.1 V at 1 A; the typical curve is 0.6-0.75 V at 10 mA and 0.88-1.0 V at 1 A
    ("D_1N400X", {}, 10e-3, 0.62, 0.70), ("D_1N400X", {}, 1.0, 0.88, 1.05),
    # D_UF4007: VF max 1.7 V at 1 A
    ("D_UF4007", {}, 10e-3, 0.70, 0.80), ("D_UF4007", {}, 1.0, 1.10, 1.50),
    # D_1N5819: VF max 0.60 V at 1 A and 0.90 V at 3 A
    ("D_1N5819", {}, 10e-3, 0.20, 0.28), ("D_1N5819", {}, 1.0, 0.35, 0.60), ("D_1N5819", {}, 3.0, 0.40, 0.90),
    # D_BAT54: ST maxima 0.24 V / 0.32 V / 0.40 V / 0.50 V / 0.90 V at 0.1 / 1 / 10 / 30 / 100 mA
    ("D_BAT54", {}, 0.1e-3, 0.10, 0.24), ("D_BAT54", {}, 1e-3, 0.20, 0.32), ("D_BAT54", {}, 10e-3, 0.30, 0.40),
    ("D_BAT54", {}, 30e-3, 0.38, 0.50), ("D_BAT54", {}, 100e-3, 0.50, 0.90),
])
def test_named_diode_forward_voltage_is_inside_the_datasheet_window(part, params, amps, lo, hi):
    assert lo <= forced(part, amps, params) <= hi


def test_generic_diode_follows_the_shockley_equation_with_series_resistance():
    for amps in (1e-3, 10e-3):
        v = forced("D_GENERIC", amps, {"Is": "1e-12", "N": "1.5", "Rs": "2"})
        assert v == pytest.approx(1.5 * VT * math.log(amps / 1e-12 + 1) + 2 * amps, rel=2e-3)


def test_generic_diode_breakdown_is_at_bv_when_the_current_is_ibv():
    assert forced("D_GENERIC", 1e-3, {"BV": "10", "IBV": "1e-3"}, reverse=True) == pytest.approx(10, rel=2e-3)
    assert forced("D_GENERIC", 1e-6, {"BV": "10", "IBV": "1e-3"}, reverse=True) < 10     # below the knee


def test_rectifier_leaks_far_less_than_the_datasheet_maximum_below_breakdown_and_breaks_down_at_bv():
    d = Document()
    vs, r, p, g = d.add_part("V", dc="90"), d.add_part("R", R="1k"), d.add_part("D_1N400X", BV="100"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{p}.k"), (f"{p}.a", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    assert abs(final(run(d), "I(V1)")) < 5e-6                   # Vishay: IR <= 5 uA at the rated voltage (max)
    assert forced("D_1N400X", 1e-3, {"BV": "100"}, reverse=True) == pytest.approx(100, rel=0.01)    # IBV = 1 mA at BV


def test_schottky_reverse_leakage_at_30_volts_is_within_the_st_maximum():
    d = Document()
    vs, r, p, g = d.add_part("V", dc="30"), d.add_part("R", R="10k"), d.add_part("D_BAT54"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{p}.k"), (f"{p}.a", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    assert abs(final(run(d), "I(V1)")) <= 1.4e-6        # ST: IR <= 1 uA at 30 V; the model adds its saturation floor


def test_fast_recovery_diode_recovers_inside_the_datasheet_trr():
    """Vishay trr test: 0.5 A forward, 1 A reverse, recovery to 0.25 A, must be <= 75 ns for the UF4007."""
    def recovery(part):
        d = Document()
        vs, r, p, g = d.add_part("V", mode="PULSE", v1="26.3", v2="-50", td="300n", tr="1n", tf="1n", pw="2u", per="4u"), d.add_part("R", R="50"), d.add_part(part), d.add_part("GND")
        wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{p}.a"), (f"{p}.k", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
        res = run(d, "1.2u", "1n")
        va, vd = res.get(f"V({d.nets()[f'{r}.a']})"), res.get(f"V({d.nets()[f'{r}.b']})")
        cur = [(t, (x - y) / 50) for (t, x), (_, y) in zip(va, vd)]
        t0 = next(t for (t, i), (t2, i2) in zip(cur, cur[1:]) if t > 300e-9 and i > 0 >= i2)
        pk = min(cur, key=lambda c: c[1])
        t1 = next(t for (t, i), (t2, i2) in zip(cur, cur[1:]) if t > pk[0] and i <= -0.25 < i2)
        return t1 - t0, pk[1]
    trr, peak = recovery("D_UF4007")
    assert 40e-9 <= trr <= 75e-9 and peak < -0.9              # the reverse current reaches the 1 A drive before recovering
    slow, _ = recovery("D_1N400X")                            # no stored charge is modelled for the rectifier: it recovers far faster
    assert slow < trr


# ---------------------------------------------------------------- zener, TVS, varactor, LED
@pytest.mark.parametrize("vz, izt, zz", [(3.3, 76e-3, 10), (5.1, 49e-3, 7), (12, 21e-3, 9), (43, 6e-3, 70)])
def test_zener_voltage_and_dynamic_impedance_match_the_vishay_table_row(vz, izt, zz):
    """1N4728A, 1N4733A, 1N4742A, 1N4755A: VZ at IZT within the +-5 % tolerance and ZZT as listed (typ)."""
    params = {"VZ": vz, "IZT": izt, "ZZ": zz}
    v0 = forced("ZENER", izt, params, reverse=True)
    assert v0 == pytest.approx(vz, rel=0.01)
    dv = forced("ZENER", izt * 1.1, params, reverse=True) - forced("ZENER", izt * 0.9, params, reverse=True)
    assert dv / (0.2 * izt) == pytest.approx(zz, rel=0.05)


def test_zener_forward_drop_is_below_the_datasheet_maximum():
    assert 0.6 < forced("ZENER", 0.2) <= 1.2          # Vishay: VF <= 1.2 V at 200 mA


def test_zener_regulates_a_varying_supply():
    def vout(vin):
        d = Document()
        vs, r, z, rl, g = (d.add_part("V", dc=str(vin)), d.add_part("R", R="100"), d.add_part("ZENER"), d.add_part("R", R="1k"),
                           d.add_part("GND"))
        wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{z}.k"), (f"{r}.b", f"{rl}.a"), (f"{z}.a", f"{g}.g"), (f"{rl}.b", f"{g}.g"),
             (f"{vs}.n", f"{g}.g"))
        return volt(d, run(d), f"{z}.k")
    lo, hi = vout(9), vout(15)
    assert 4.8 <= lo <= 5.2 and 5.2 <= hi <= 5.5     # 5.1 V at 49 mA; the voltage rises by ZZ x the extra current
    assert (hi - lo) / 6 == pytest.approx(6.95 / (100 + 6.95), rel=0.2)          # line regulation = (Zz || RL) / (R + Zz || RL)


def test_three_zeners_in_series_share_one_subcircuit_and_keep_their_own_voltages():
    d = Document()
    vs, r, g = d.add_part("V", dc="30"), d.add_part("R", R="500"), d.add_part("GND")
    z = [d.add_part("ZENER", VZ=v, IZT="10m", ZZ="5") for v in ("5.1", "6.8", "12")]
    wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{z[0]}.k"), (f"{z[0]}.a", f"{z[1]}.k"), (f"{z[1]}.a", f"{z[2]}.k"),
         (f"{z[2]}.a", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d)
    assert len([m for m in d.netlist()["models"] if m.startswith("* bench-def") and "bench_zener" in " ".join(d.netlist()["models"])]) >= 1
    assert sum(1 for m in d.netlist()["models"] if m.strip().startswith(".subckt bench_zener")) == 1
    i = (30 - 23.9) / 500                                   # about 12 mA, close to the 10 mA test current
    for part, vz in zip(z, (5.1, 6.8, 12.0)):
        assert volt(d, res, f"{part}.k") - volt(d, res, f"{part}.a") == pytest.approx(vz + 5 * (i - 10e-3), rel=0.01)


@pytest.mark.parametrize("vrwm, vc, ipp", [(12, 19.9, 20.1), (5, 9.2, 43.5), (24, 38.9, 10.3)])
def test_tvs_breaks_down_at_1p11_standoff_and_clamps_at_vc_at_ipp(vrwm, vc, ipp):
    params = {"VRWM": vrwm, "VC": vc, "IPP": ipp}
    assert forced("TVS_UNI", 1e-3, params, reverse=True) == pytest.approx(1.11 * vrwm, rel=0.01)
    assert forced("TVS_UNI", ipp, params, reverse=True) == pytest.approx(vc, rel=0.01)
    assert forced("TVS_UNI", 0.1e-3, params, reverse=True) < 1.11 * vrwm                   # blocking below the knee
    assert forced("TVS_UNI", 1.0, params) < 1.2                                            # forward: an ordinary diode
    # bidirectional: the same clamp in both polarities
    for sign in (1, -1):
        assert forced("TVS_BI", sign * ipp, params, a="a", k="b") == pytest.approx(vc, rel=0.02)
        assert forced("TVS_BI", sign * 1e-3, params, a="a", k="b") == pytest.approx(1.11 * vrwm, rel=0.02)


def test_tvs_protects_a_load_from_a_surge():
    d = Document()
    vs, r, t, g = d.add_part("V", mode="PULSE", v1="0", v2="100", td="10u", tr="1u", tf="1u", pw="50u", per="200u"), d.add_part("R", R="5"), d.add_part("TVS_UNI"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{t}.k"), (f"{t}.a", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "100u", "0.5u")
    peak = max(v for _, v in res.get(f"V({d.nets()[f'{t}.k']})"))
    i_peak = (100 - peak) / 5
    assert 13.3 < peak < 21 and i_peak > 10                         # clamped well below the 100 V surge


def test_varactor_capacitance_follows_the_depletion_law():
    """A reverse ramp of 1 V/us across the diode draws C(V) dV/dt (the series resistance and leakage are negligible)."""
    def cap(m, vmid):
        d = Document()
        vs, r, v, g = (d.add_part("V", mode="PULSE", v1=str(vmid - 1), v2=str(vmid + 1), td="1u", tr="2u", tf="1u", pw="10u", per="20u"),
                       d.add_part("R", R="1"), d.add_part("VARACTOR", Cjo="30p", Vj="0.7", M=m, Rs="0.01"), d.add_part("GND"))
        wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{v}.k"), (f"{v}.a", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
        i = res_i = run(d, "4u", "10n").get("I(V1)")
        return abs(min(i, key=lambda p: abs(p[0] - 2e-6))[1]) / 1e6          # mid-ramp: V = vmid
    for m, vmid in ((0.5, 5), (0.5, 1.5), (0.75, 5)):
        assert cap(str(m), vmid) == pytest.approx(30e-12 / (1 + vmid / 0.7) ** m, rel=0.02)


@pytest.mark.parametrize("color, lo, hi", [("ir", 1.20, 1.30), ("red", 1.90, 2.00), ("yellow", 2.00, 2.10),
                                           ("green", 2.10, 2.20), ("blue", 3.05, 3.15), ("white", 3.15, 3.25)])
def test_led_forward_voltage_at_20_ma_is_typical_for_its_colour(color, lo, hi):
    assert lo <= forced("LED_COLOR", 20e-3, {"color": color}) <= hi


def test_led_forward_voltage_rises_with_wavelength_order():
    v = [forced("LED_COLOR", 20e-3, {"color": c}) for c in ("ir", "red", "yellow", "green", "blue", "white")]
    assert v == sorted(v)


# ---------------------------------------------------------------- bipolar transistors
def bjt_bias(id_, vce, ib, pnp):
    """Emitter grounded, collector held at vce by a source, base current forced: returns (|Ic|, vbe)."""
    d = Document()
    q, vc, ibs, g = d.add_part(id_), d.add_part("V", dc=str(-vce if pnp else vce)), d.add_part("I", dc=str(ib)), d.add_part("GND")
    if pnp:
        wire(d, (f"{ibs}.p", f"{q}.b"), (f"{ibs}.n", f"{g}.g"), (f"{vc}.p", f"{q}.c"), (f"{vc}.n", f"{g}.g"), (f"{q}.e", f"{g}.g"))
    else:
        wire(d, (f"{ibs}.n", f"{q}.b"), (f"{ibs}.p", f"{g}.g"), (f"{vc}.p", f"{q}.c"), (f"{vc}.n", f"{g}.g"), (f"{q}.e", f"{g}.g"))
    res = run(d)
    return abs(final(res, f"I({vc})")), abs(volt(d, res, f"{q}.b"))


def bjt_sat(id_, ic, ib, pnp, vcc=10.0):
    """Collector resistor sets Ic ~ ic; base current forced to ib: returns (|vce|, |vbe|, Ic)."""
    d = Document()
    q, vs, rc, ibs, g = d.add_part(id_), d.add_part("V", dc=str(-vcc if pnp else vcc)), d.add_part("R", R=str((vcc - 0.3) / ic)), d.add_part("I", dc=str(ib)), d.add_part("GND")
    if pnp:
        wire(d, (f"{ibs}.p", f"{q}.b"), (f"{ibs}.n", f"{g}.g"), (f"{vs}.p", f"{rc}.a"), (f"{vs}.n", f"{g}.g"))
        wire(d, (f"{rc}.b", f"{q}.c"), (f"{q}.e", f"{g}.g"))
    else:
        wire(d, (f"{ibs}.n", f"{q}.b"), (f"{ibs}.p", f"{g}.g"), (f"{vs}.p", f"{rc}.a"), (f"{rc}.b", f"{q}.c"), (f"{q}.e", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d)
    vce, vbe = abs(volt(d, res, f"{q}.c")), abs(volt(d, res, f"{q}.b"))
    return vce, vbe, (vcc - vce) / ((vcc - 0.3) / ic)


# (id, pnp, vce, ic, hFE window from the datasheet, expected hFE of the fit used to choose the base current)
HFE = [
    ("Q_2N3904", False, 1.0, 0.1e-3, 40, 300, 120), ("Q_2N3904", False, 1.0, 1e-3, 70, 300, 160), ("Q_2N3904", False, 1.0, 10e-3, 100, 300, 180),
    ("Q_2N3904", False, 1.0, 50e-3, 60, 300, 165), ("Q_2N3904", False, 1.0, 100e-3, 30, 300, 140),
    ("Q_2N3906", True, 1.0, 0.1e-3, 60, 300, 105), ("Q_2N3906", True, 1.0, 1e-3, 80, 300, 150), ("Q_2N3906", True, 1.0, 10e-3, 100, 300, 175),
    ("Q_2N3906", True, 1.0, 50e-3, 60, 300, 160), ("Q_2N3906", True, 1.0, 100e-3, 30, 300, 135),
    ("Q_BC547B", False, 5.0, 2e-3, 200, 450, 305), ("Q_BC557B", True, 5.0, 2e-3, 200, 450, 310),
    ("Q_TIP31C", False, 4.0, 1.0, 25, 1000, 64), ("Q_TIP31C", False, 4.0, 3.0, 10, 50, 42),
    ("Q_TIP32C", True, 4.0, 1.0, 25, 1000, 64), ("Q_TIP32C", True, 4.0, 3.0, 10, 50, 42),
    ("Q_2N3055", False, 4.0, 4.0, 20, 70, 37), ("Q_2N3055", False, 4.0, 10.0, 5, 200, 24),
]


@pytest.mark.parametrize("id_, pnp, vce, ic, lo, hi, est", HFE)
def test_named_bjt_gain_is_inside_the_datasheet_window(id_, pnp, vce, ic, lo, hi, est):
    icm, _ = bjt_bias(id_, vce, ic / est, pnp)
    assert icm == pytest.approx(ic, rel=0.2)              # biased at the datasheet test current
    assert lo <= icm / (ic / est) <= hi


# (id, pnp, Ic, Ib, VCE(sat) max, (VBE(sat) window))
SAT = [
    ("Q_2N3904", False, 10e-3, 1e-3, 0.2, (0.65, 0.85)), ("Q_2N3904", False, 50e-3, 5e-3, 0.4, (0.65, 0.95)),
    ("Q_2N3906", True, 10e-3, 1e-3, 0.25, (0.65, 0.85)), ("Q_2N3906", True, 50e-3, 5e-3, 0.4, (0.65, 0.95)),
    ("Q_BC547B", False, 10e-3, 0.5e-3, 0.25, (0.6, 0.77)), ("Q_BC557B", True, 10e-3, 0.5e-3, 0.30, (0.6, 0.8)),
    ("Q_BC557B", True, 100e-3, 5e-3, 0.65, (0.7, 1.0)),
    ("Q_TIP31C", False, 3.0, 0.375, 1.2, (0.7, 1.8)), ("Q_TIP32C", True, 3.0, 0.375, 1.2, (0.7, 1.8)),
    ("Q_2N3055", False, 4.0, 0.4, 1.1, (0.7, 1.5)), ("Q_2N3055", False, 10.0, 3.3, 3.0, (0.7, 3.0)),
]


@pytest.mark.parametrize("id_, pnp, ic, ib, vce_max, vbe", SAT)
def test_named_bjt_saturation_voltages_are_inside_the_datasheet_limits(id_, pnp, ic, ib, vce_max, vbe):
    vcc = 40.0 if ic >= 3 else 10.0
    vce, vb, icm = bjt_sat(id_, ic, ib, pnp, vcc=vcc)
    assert icm == pytest.approx(ic, rel=0.1)
    assert vce <= vce_max and vbe[0] <= vb <= vbe[1]
    assert icm / ib >= 2.9                                 # forced at the datasheet's base current (forced gain 3 to 20)


def test_generic_bjt_beta_and_early_voltage_follow_the_parameters():
    for id_, pnp in (("Q_NPN", False), ("Q_PNP", True)):
        ic1, _ = bjt_bias(id_, 5.0, 10e-6, pnp)
        ic2, _ = bjt_bias(id_, 10.0, 10e-6, pnp)
        assert ic1 / 10e-6 == pytest.approx(100 * (1 + 5 / 100), rel=0.03)         # Bf (1 + Vce/Vaf) with the defaults
        assert ic2 / ic1 == pytest.approx((1 + 10 / 100) / (1 + 5 / 100), rel=0.01)


def test_tip120_darlington_gain_saturation_and_base_drop():
    """Fairchild: hFE >= 1000 at 0.5 A and 3 A (VCE = 3 V), VCE(sat) <= 2 V at 3 A / 12 mA, <= 4 V at 5 A / 20 mA, VBE(on) <= 2.5 V."""
    for ic, est in ((0.5, 1440), (3.0, 2375)):
        icm, vbe = bjt_bias("Q_TIP120", 3.0, ic / est, False)
        assert icm / (ic / est) >= 1000 and vbe <= 2.5
    vce, vbe, icm = bjt_sat("Q_TIP120", 3.0, 12e-3, False, vcc=20.0)
    assert vce <= 2.0 and icm == pytest.approx(3.0, rel=0.1)
    vce5, _, icm5 = bjt_sat("Q_TIP120", 5.0, 20e-3, False, vcc=20.0)
    assert vce5 <= 4.0 and icm5 == pytest.approx(5.0, rel=0.1)


def test_two_darlington_instances_share_one_subcircuit_and_run_independently():
    d = Document()
    vs, g = d.add_part("V", dc="12"), d.add_part("GND")
    qa, qb = d.add_part("Q_TIP120"), d.add_part("Q_TIP120")
    rba, rbb = d.add_part("R", R="33k"), d.add_part("R", R="15k")
    rla, rlb = d.add_part("R", R="10"), d.add_part("R", R="10")
    wire(d, (f"{vs}.p", f"{rba}.a"), (f"{vs}.p", f"{rbb}.a"), (f"{rba}.b", f"{qa}.b"), (f"{rbb}.b", f"{qb}.b"),
         (f"{vs}.p", f"{rla}.a"), (f"{vs}.p", f"{rlb}.a"), (f"{rla}.b", f"{qa}.c"), (f"{rlb}.b", f"{qb}.c"),
         (f"{qa}.e", f"{g}.g"), (f"{qb}.e", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    nl = d.netlist()
    assert sum(1 for m in nl["models"] if m.strip().startswith(".subckt bench_darlington")) == 1
    assert len([ln for ln in nl["lines"] if ln.startswith("X")]) == 2
    res = run(d)
    ia, ib = (12 - volt(d, res, f"{qa}.c")) / 10, (12 - volt(d, res, f"{qb}.c")) / 10
    iba, ibb = (12 - volt(d, res, f"{qa}.b")) / 33e3, (12 - volt(d, res, f"{qb}.b")) / 15e3
    assert ib > ia > 0.2                                     # more base current, more collector current
    assert 800 < ia / iba < 3000 and 800 < ib / ibb < 3000     # both gains are Darlington-sized (the stages saturate a little)
    assert iba == pytest.approx((12 - 1.5) / 33e3, rel=0.1) and ibb > 2 * iba


# ---------------------------------------------------------------- MOSFETs
def mos_current(id_, pch, vgs, vds, params=None, four=False):
    d = Document()
    m, vg, vd, g = d.add_part(id_, **(params or {})), d.add_part("V", dc=str(-vgs if pch else vgs)), d.add_part("V", dc=str(-vds if pch else vds)), d.add_part("GND")
    wire(d, (f"{vg}.p", f"{m}.g"), (f"{vg}.n", f"{g}.g"), (f"{vd}.p", f"{m}.d"), (f"{vd}.n", f"{g}.g"), (f"{m}.s", f"{g}.g"))
    if four: d.connect(f"{m}.b", f"{g}.g")
    return abs(final(run(d), f"I({vd})"))


@pytest.mark.parametrize("id_, pch, vto", [("NMOS", False, 1.0), ("PMOS", True, -1.0)])
def test_generic_mosfet_is_square_law_above_threshold_and_off_below(id_, pch, vto):
    kp, wl, lam = 50e-6, 100.0, 0.02
    for vgs in (1.5, 2.0, 3.0):
        want = kp * wl / 2 * (vgs - abs(vto)) ** 2 * (1 + lam * 5)
        assert mos_current(id_, pch, vgs, 5.0, four=True) == pytest.approx(want, rel=0.02)
    assert mos_current(id_, pch, 0.8, 5.0, four=True) < 1e-7                     # below threshold
    # triode: Id = Kp W/L ((Vgs - Vt) Vds - Vds^2/2) (1 + lambda Vds) at small Vds
    assert mos_current(id_, pch, 3.0, 0.2, four=True) == pytest.approx(kp * wl * (2 * 0.2 - 0.02) * (1 + lam * 0.2), rel=0.02)
    # W and L parameters scale the current
    assert mos_current(id_, pch, 2.0, 5.0, {"W": "200u", "L": "2u"}, four=True) == pytest.approx(
        kp * 100 / 2 * (2 - abs(vto)) ** 2 * (1 + lam * 5), rel=0.02)


# (id, pch, test current for VGS(th), window, (Vgs, Id) -> max Rds(on) in ohm, order-of-magnitude floor)
MOS = [
    ("M_2N7000", False, 1e-3, (0.8, 3.0), (10.0, 0.5), 5.0, 0.5), ("M_2N7000", False, 1e-3, (0.8, 3.0), (4.5, 0.075), 0.4 / 0.075, 0.5),
    ("M_BS170", False, 1e-3, (0.8, 3.0), (10.0, 0.2), 5.0, 0.5), ("M_BSS138", False, 250e-6, (0.5, 1.5), (10.0, 0.22), 3.5, 0.3),
    ("M_IRF540N", False, 250e-6, (2.0, 4.0), (10.0, 16.0), 0.044, 0.005), ("M_IRF9540N", True, 250e-6, (2.0, 4.0), (10.0, 11.0), 0.117, 0.01),
    ("M_IRLZ44N", False, 250e-6, (1.0, 2.0), (10.0, 25.0), 0.022, 0.003), ("M_IRLZ44N", False, 250e-6, (1.0, 2.0), (5.0, 25.0), 0.025, 0.003),
    ("M_IRLZ44N", False, 250e-6, (1.0, 2.0), (4.0, 21.0), 0.035, 0.003),
]


@pytest.mark.parametrize("id_, pch, ith, window, point, rmax, rfloor", MOS)
def test_named_mosfet_threshold_and_on_resistance_are_inside_the_datasheet_limits(id_, pch, ith, window, point, rmax, rfloor):
    # threshold: diode-connected, forced ID
    d = Document()
    m, i, g = d.add_part(id_), d.add_part("I", dc=str(ith)), d.add_part("GND")
    if pch:
        wire(d, (f"{i}.p", f"{m}.d"), (f"{i}.n", f"{g}.g"), (f"{m}.g", f"{m}.d"), (f"{m}.s", f"{g}.g"))
    else:
        wire(d, (f"{i}.n", f"{m}.d"), (f"{i}.p", f"{g}.g"), (f"{m}.g", f"{m}.d"), (f"{m}.s", f"{g}.g"))
    vth = abs(volt(d, run(d), f"{m}.d"))
    assert window[0] <= vth <= window[1]
    # on-resistance: gate driven, drain forced to the datasheet current
    vgs, idv = point
    d = Document()
    m, vg, i, g = d.add_part(id_), d.add_part("V", dc=str(-vgs if pch else vgs)), d.add_part("I", dc=str(idv)), d.add_part("GND")
    if pch:
        wire(d, (f"{i}.p", f"{m}.d"), (f"{i}.n", f"{g}.g"))
    else:
        wire(d, (f"{i}.n", f"{m}.d"), (f"{i}.p", f"{g}.g"))
    wire(d, (f"{vg}.p", f"{m}.g"), (f"{vg}.n", f"{g}.g"), (f"{m}.s", f"{g}.g"))
    rds = abs(volt(d, run(d), f"{m}.d")) / idv
    assert rfloor < rds <= rmax


def test_power_mosfet_gate_charge_stays_below_the_datasheet_maximum():
    """Switching an inductive-style load (80 V, 16 A, freewheel diode): charge into the gate to 10 V <= 71 nC (Infineon)."""
    d = Document()
    vdd, il, df, m, vg, rg, g = (d.add_part("V", dc="80"), d.add_part("I", dc="16"), d.add_part("D_GENERIC", Rs="0.01"),
                                 d.add_part("M_IRF540N"), d.add_part("V", mode="PULSE", v1="0", v2="12", td="1u", tr="10n", tf="1u", pw="40u", per="100u"),
                                 d.add_part("R", R="1k"), d.add_part("GND"))
    wire(d, (f"{vdd}.p", f"{il}.p"), (f"{il}.n", f"{m}.d"), (f"{df}.a", f"{m}.d"), (f"{df}.k", f"{vdd}.p"),
         (f"{vdd}.n", f"{g}.g"), (f"{m}.s", f"{g}.g"), (f"{vg}.p", f"{rg}.a"), (f"{rg}.b", f"{m}.g"), (f"{vg}.n", f"{g}.g"))
    res = run(d, "30u", "20n")
    vsrc, vgate = res.get(f"V({d.nets()[f'{rg}.a']})"), res.get(f"V({d.nets()[f'{m}.g']})")
    q, tq = 0.0, None
    for (t0, a0), (t1, a1), (_, b0), (_, b1) in zip(vsrc, vsrc[1:], vgate, vgate[1:]):
        if b1 >= 10 and tq is None:
            tq = t1
            break
        q += (t1 - t0) * ((a0 - b0) + (a1 - b1)) / 2 / 1000
    assert tq is not None and 45e-9 <= q <= 71e-9              # the fit targets about 60 nC; the datasheet maximum is 71 nC


# ---------------------------------------------------------------- JFETs
@pytest.mark.parametrize("id_, pch, vds, lo, hi, typ", [("J_J201", False, 20, 0.2e-3, 1.0e-3, 0.5e-3), ("J_2N5457", False, 15, 1e-3, 5e-3, 3e-3),
                                                         ("J_2N5460", True, 15, 1e-3, 5e-3, 3e-3)])
def test_jfet_idss_is_inside_the_datasheet_window_and_pinches_off(id_, pch, vds, lo, hi, typ):
    def idrain(vgs):
        d = Document()
        j, vd, vg, g = d.add_part(id_), d.add_part("V", dc=str(-vds if pch else vds)), d.add_part("V", dc=str(vgs)), d.add_part("GND")
        wire(d, (f"{vd}.p", f"{j}.d"), (f"{vd}.n", f"{g}.g"), (f"{vg}.p", f"{j}.g"), (f"{vg}.n", f"{g}.g"), (f"{j}.s", f"{g}.g"))
        return abs(final(run(d), f"I({vd})"))
    idss = idrain(0.0)
    assert lo <= idss <= hi and idss == pytest.approx(typ, rel=0.03)
    vp = {"J_J201": -0.8, "J_2N5457": -2.0, "J_2N5460": 2.5}[id_]
    assert idrain(vp * 1.05) < idss * 1e-3                      # pinched off beyond the cutoff voltage
    assert idrain(vp / 2) == pytest.approx(idss * 0.25, rel=0.05)    # square law: (1 - Vgs/Vp)^2 at half pinch-off


# ---------------------------------------------------------------- thyristors and IGBT
def scr_circuit(rload, gate_v="5", gate_td="100u", gate_pw="300u", vsupply="12", part="SCR", params=None):
    d = Document()
    vs, rl, s, vgp, rg, g = (d.add_part("V", dc=vsupply), d.add_part("R", R=str(rload)), d.add_part(part, **(params or {})),
                             d.add_part("V", mode="PULSE", v1="0", v2=gate_v, td=gate_td, tr="1u", tf="1u", pw=gate_pw, per="1"),
                             d.add_part("R", R="1k"), d.add_part("GND"))
    wire(d, (f"{vs}.p", f"{rl}.a"), (f"{rl}.b", f"{s}.a"), (f"{s}.k", f"{g}.g"), (f"{vs}.n", f"{g}.g"),
         (f"{vgp}.p", f"{rg}.a"), (f"{rg}.b", f"{s}.g"), (f"{vgp}.n", f"{g}.g"))
    return d, s


def anode(rload, **kw):
    d, s = scr_circuit(rload, **kw)
    res = run(d, "1.5m", "5u")
    tr = res.get(f"V({d.nets()[f'{s}.a']})")
    at = lambda t: min(tr, key=lambda p: abs(p[0] - t))[1]
    return at(50e-6), at(1.2e-3)


def test_scr_blocks_until_triggered_latches_after_the_gate_pulse_and_drops_out_below_holding_current():
    before, after = anode(100)
    assert before == pytest.approx(12, abs=0.05)             # blocking before the gate pulse
    assert after < 2.2                                       # latched on at 0.1 A after the gate pulse has ended
    # holding current IH = 5 mA: 2 mA through the device cannot keep it on, 12 mA can
    assert anode(5000)[1] > 11.5
    assert anode(500)[1] < 2.2
    # a gate pulse that is too weak (0.6 V through 1 k) does not fire it
    assert anode(100, gate_v="0.6")[1] > 11.9


def test_scr_holding_current_follows_the_ih_parameter():
    for ih in ("2m", "20m"):
        ihv = 0.002 if ih == "2m" else 0.02
        assert anode((12 - 1.6) / (1.5 * ihv), params={"IH": ih})[1] < 3          # 1.5 IH keeps it on
        assert anode((12 - 1.6) / (0.5 * ihv), params={"IH": ih})[1] > 11            # 0.5 IH drops it out


def test_scr_blocks_reverse_voltage():
    d = Document()
    vs, rl, s, g = d.add_part("V", dc="-100"), d.add_part("R", R="1k"), d.add_part("SCR"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{rl}.a"), (f"{rl}.b", f"{s}.a"), (f"{s}.k", f"{g}.g"), (f"{s}.g", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    assert abs(final(run(d), "I(V1)")) < 1e-5


def test_triac_conducts_both_half_cycles_after_gate_pulses_and_only_then():
    def load_trace(gated):
        d = Document()
        vs, rl, t, g = d.add_part("V", mode="SIN", vo="0", va="40", freq="50"), d.add_part("R", R="40"), d.add_part("TRIAC"), d.add_part("GND")
        gp, gn = d.add_part("V", mode="PULSE", v1="0", v2="5" if gated else "0", td="2m", tr="1u", tf="1u", pw="1m", per="20m"), \
            d.add_part("V", mode="PULSE", v1="0", v2="-5" if gated else "0", td="12m", tr="1u", tf="1u", pw="1m", per="20m")
        r1, r2 = d.add_part("R", R="1k"), d.add_part("R", R="1k")
        wire(d, (f"{vs}.p", f"{rl}.a"), (f"{rl}.b", f"{t}.mt2"), (f"{t}.mt1", f"{g}.g"), (f"{vs}.n", f"{g}.g"),
             (f"{gp}.p", f"{r1}.a"), (f"{r1}.b", f"{t}.g"), (f"{gn}.p", f"{r2}.a"), (f"{r2}.b", f"{t}.g"),
             (f"{gp}.n", f"{g}.g"), (f"{gn}.n", f"{g}.g"))
        res = run(d, "40m", "20u")
        return res.get(f"V({d.nets()[f'{rl}.b']})")
    on, off = load_trace(True), load_trace(False)
    assert max(v for t, v in on if 1.0e-3 < t < 1.8e-3) > 10         # not yet fired: the node follows the supply
    # fired at 2 ms (positive) and 12 ms (negative, quadrant III): the terminal then sits within a diode drop or two of 0 V
    assert max(abs(v) for t, v in on if 3.5e-3 < t < 9e-3) < 3.0
    assert max(abs(v) for t, v in on if 13.5e-3 < t < 19e-3) < 3.0
    # no gate pulses: it never fires and follows the supply in both polarities
    assert max(v for t, v in off if 5e-3 < t < 9e-3) > 30 and min(v for t, v in off if 15e-3 < t < 19e-3) < -30


@pytest.mark.parametrize("vbo", [32, 40])
@pytest.mark.parametrize("sign", [1, -1])
def test_diac_breaks_over_at_vbo_in_both_polarities_and_drops_back(vbo, sign):
    d = Document()
    vs, r, c, x, rl, g = (d.add_part("V", dc=str(60 * sign)), d.add_part("R", R="10k"), d.add_part("C", C="100n"), d.add_part("DIAC", VBO=str(vbo)),
                          d.add_part("R", R="100"), d.add_part("GND"))
    wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{c}.a"), (f"{c}.b", f"{g}.g"), (f"{r}.b", f"{x}.a"), (f"{x}.b", f"{rl}.a"),
         (f"{rl}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "5m", "2u")
    tr = [v * sign for _, v in res.get(f"V({d.nets()[f'{r}.b']})")]
    assert max(tr) == pytest.approx(vbo, abs=2.0)              # charged to breakover, no further
    assert tr[-1] < 8                                          # fired: the capacitor voltage collapsed to the on-state drop


def test_diac_does_not_conduct_below_breakover():
    d = Document()
    vs, x, rl, g = d.add_part("V", dc="25"), d.add_part("DIAC"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{x}.a"), (f"{x}.b", f"{rl}.a"), (f"{rl}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    assert abs(final(run(d, "1m", "5u"), "I(V1)")) < 1e-5


def test_igbt_turns_on_above_its_gate_threshold_with_a_bipolar_on_state_drop():
    def vce(vge, amps):
        d = Document()
        q, vg, i, g = d.add_part("IGBT"), d.add_part("V", dc=str(vge)), d.add_part("I", dc=str(amps)), d.add_part("GND")
        wire(d, (f"{i}.n", f"{q}.c"), (f"{i}.p", f"{g}.g"), (f"{vg}.p", f"{q}.g"), (f"{vg}.n", f"{g}.g"), (f"{q}.e", f"{g}.g"))
        return volt(d, run(d), f"{q}.c")
    # on-state: PNP emitter-base drop plus the channel and drift resistances (about 0.9 V at 1 A, 2.6 V at 20 A with the defaults)
    assert vce(15, 1) == pytest.approx(0.9, abs=0.1)
    assert vce(15, 20) == pytest.approx(2.6, abs=0.3)
    assert vce(15, 1) < vce(15, 5) < vce(15, 20)
    assert vce(15, 20) < vce(8, 20)                       # less gate drive, more channel resistance

    def leak(vge):
        d = Document()
        q, vg, vc, g = d.add_part("IGBT"), d.add_part("V", dc=str(vge)), d.add_part("V", dc="50"), d.add_part("GND")
        wire(d, (f"{vc}.p", f"{q}.c"), (f"{vc}.n", f"{g}.g"), (f"{vg}.p", f"{q}.g"), (f"{vg}.n", f"{g}.g"), (f"{q}.e", f"{g}.g"))
        return abs(final(run(d), f"I({vc})"))
    assert leak(3) < 1e-5                              # well below the 5 V threshold: the model floor, about 1 uA at 50 V
    assert leak(4.9) < 5e-3                           # just below it: VDMOS subthreshold conduction, times the PNP gain
    assert leak(7) > 0.1 and leak(15) > leak(7)       # conducting, and more gate drive gives more (saturation) current
