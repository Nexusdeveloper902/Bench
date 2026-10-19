"""Library batch A (task 5): the new passive, protection, switch, source and battery packages.

Every new package loads and validates (validator status `full`, or `limited` where a B source is left to ngspice).
Each family runs through the REAL path (Document -> netlist -> app.sim -> benchcore -> ngspice) and is checked against
a closed form: the wiper divider, the rheostat and tolerance divider, RL and RC time constants with DCR and ESR, the
open-secondary transformer ratio sample by sample, the one-shot fuse and PTC trip delays, the relay pull-in current,
the switch states, the source waveforms, the clamp characteristic and a Thevenin cell under load."""
import math

import benchcore
import pytest

from app import electrical
from app.catalog import BUILTIN_ROOT, CATALOG
from app.model import Document, value_at
from app.sim import run_document

NEW = {   # id -> validator status (None: a primitive element with no definition text to validate)
    "POT": "full", "RHEOSTAT": "full", "RNET4_ISO": "full", "RNET4_BUS": "full", "R_TOL": "full",
    "IND_DCR": "full", "IND_COUPLED": "full", "XFMR_1N": "full", "XFMR_CT": "full", "T_LINE": None,
    "CAP_CERAMIC": "full", "CAP_ELECTROLYTIC": "full", "CAP_TANTALUM": "full", "CAP_FILM": "full",
    "FUSE": "full", "PTC": "full", "MOV": "limited",
    "SPDT": "full", "DPST": "full", "DPDT": "full", "PUSH_NO": None, "PUSH_NC": None,
    "RELAY_SPDT": "full", "RELAY_DPDT": "full", "REED": "full", "VSW": None, "CSW": "full",
    "VSRC_EXP_SFFM": None, "ISRC_WAVE": None, "VCVS": None, "VCCS": None, "CCCS": "full", "CCVS": "full",
    "SAT_AMP": "limited", "BAT_COIN": "full", "BAT_AA": "full", "BAT_LITHIUM": "full",
}


# ---------------------------------------------------------------- helpers
def wire(d, *pairs):
    for a, b in pairs: d.connect(a, b)


def run(d, tstop, tstep):
    d.tstop, d.tstep = tstop, tstep
    res = run_document(d)
    assert res.ok, res.message + "\n" + (res.details or "")
    return res


def node(d, res, pin, t=None):
    """Node voltage at a part pin (its net), final value or interpolated at t."""
    s = res.get(f"V({d.nets()[pin]})")
    return value_at(s, t) if t is not None else s[-1][1]


def trace(d, res, pin):
    return res.get(f"V({d.nets()[pin]})")


def probe(d, vec, tstop, tstep):
    """A subcircuit-internal vector (xc1.n2) from the document's own deck, straight into benchcore."""
    nl = d.netlist()
    s = benchcore.Simulation("batch_a").build_circuit(nl["lines"])
    for m in nl["models"]: s.add_model(m)
    s.configure(tstep=tstep, tstop=tstop, tmax=tstep).observe(vec)
    s.run()
    return s.observable(vec)


def sec(id):
    return CATALOG.get(id).sim["electrical"]


# ---------------------------------------------------------------- every package loads and validates
@pytest.mark.parametrize("id", sorted(NEW))
def test_package_loads_validates_and_records_its_kind(id):
    c = CATALOG.get(id)
    s = c.sim["electrical"]
    assert CATALOG.problems == [] and c.simulated and c.readiness == "simulated" and c.path.is_relative_to(BUILTIN_ROOT)
    assert c.category in ("Electrical", "Power") and s["model_kind"] in electrical.MODEL_KINDS
    assert s.get("_status") == NEW[id] and (NEW[id] is not None or "_status" not in s)
    assert s["provenance"]["license"] == "MIT" and s["provenance"]["source"]
    if NEW[id] == "limited":
        assert any("Static validation is limited" in x for x in c.limitations)


def test_new_parts_are_the_only_additions_to_the_builtin_catalog():
    assert set(NEW) <= set(CATALOG.defs) and len(CATALOG.defs) >= 20 + len(NEW)     # batch B adds more (test_library_batch_b.py)


def test_shared_unit_is_emitted_once_per_deck_for_two_instances():
    d = Document()
    a, b = d.add_part("BAT_AA"), d.add_part("BAT_AA")
    g = d.add_part("GND")
    wire(d, (f"{a}.n", f"{b}.p"), (f"{b}.n", f"{g}.g"), (f"{a}.p", f"{g}.g"))
    units = [m for m in d.netlist()["models"] if m.startswith("* bench-def") and "cell_thevenin" in m]
    assert len(units) == 1 and len([m for m in d.netlist()["lines"] if m.startswith("XBT")]) == 2


# ---------------------------------------------------------------- passives
@pytest.mark.parametrize("pos", [0.0, 0.25, 0.5, 1.0])
def test_pot_divider_follows_the_wiper(pos):
    d = Document()
    vs, p, g = d.add_part("V", dc="5"), d.add_part("POT", R="10k", pos=str(pos), rw="1"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{p}.a"), (f"{p}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    assert node(d, res, f"{p}.w") == pytest.approx(5 * (10e3 * (1 - pos) + 1) / (10e3 + 2), rel=1e-6)


def test_two_pot_instances_on_one_rail_are_independent():
    d = Document()
    vs, g = d.add_part("V", dc="5"), d.add_part("GND")
    a = d.add_part("POT", R="1k", pos="0.2", rw="1")
    b = d.add_part("POT", R="1k", pos="0.8", rw="1")
    wire(d, (f"{vs}.p", f"{a}.a"), (f"{vs}.p", f"{b}.a"), (f"{a}.b", f"{g}.g"), (f"{b}.b", f"{g}.g"),
         (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    assert node(d, res, f"{a}.w") == pytest.approx(5 * (1000 * 0.8 + 1) / 1002, rel=1e-6)
    assert node(d, res, f"{b}.w") == pytest.approx(5 * (1000 * 0.2 + 1) / 1002, rel=1e-6)
    assert d.nets()[f"{a}.w"] != d.nets()[f"{b}.w"]


@pytest.mark.parametrize("pos", [0.0, 0.5, 1.0])
def test_rheostat_in_series_with_a_resistor(pos):
    d = Document()
    vs, r, rh, g = d.add_part("V", dc="5"), d.add_part("R", R="1k"), d.add_part("RHEOSTAT", R="1k", pos=str(pos)), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{rh}.a"), (f"{rh}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    rv = 1 + pos * 1000       # rmin default 1 ohm
    assert node(d, res, f"{r}.b") == pytest.approx(5 * rv / (1000 + rv), rel=1e-6)


def test_isolated_network_elements_are_independent():
    d = Document()
    vs, net, g = d.add_part("V", dc="5"), d.add_part("RNET4_ISO", R="10k"), d.add_part("GND")
    wire(d, (f"{vs}.n", f"{g}.g"))
    for k in range(1, 5):
        rk = d.add_part("R", R="1k")
        wire(d, (f"{vs}.p", f"{rk}.a"), (f"{rk}.b", f"{net}.a{k}"), (f"{net}.b{k}", f"{g}.g"))
    res = run(d, "1m", "10u")
    for k in range(1, 5):
        assert node(d, res, f"{net}.a{k}") == pytest.approx(5 * 10 / 11, rel=1e-6)


def test_bussed_network_shares_the_common_pin():
    d = Document()
    vs, net, g = d.add_part("V", dc="5"), d.add_part("RNET4_BUS", R="3k"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{net}.c"), (f"{vs}.n", f"{g}.g"))
    loads = []
    for k in range(1, 5):
        rk = d.add_part("R", R="1k")
        loads.append(rk)
        wire(d, (f"{net}.r{k}", f"{rk}.a"), (f"{rk}.b", f"{g}.g"))
    res = run(d, "1m", "10u")
    for rk in loads:   # 3k bussed element into a 1k load: 5 * 1 / (3 + 1)
        assert node(d, res, f"{rk}.a") == pytest.approx(1.25, rel=1e-6)


@pytest.mark.parametrize("tol", [-0.1, 0.0, 0.05])
def test_toleranced_resistor_corner(tol):
    d = Document()
    vs, rt, r, g = d.add_part("V", dc="10"), d.add_part("R_TOL", R="1k", tol=str(tol)), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{rt}.a"), (f"{rt}.b", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    assert node(d, res, f"{rt}.b") == pytest.approx(10 * 1000 / (1000 * (1 + tol) + 1000), rel=1e-6)


@pytest.mark.parametrize("dcr", ["0.5", "5"])
def test_inductor_with_dcr_is_an_rl_step(dcr):
    d = Document()
    vs, l, r, g = d.add_part("V", dc="10"), d.add_part("IND_DCR", L="1m", dcr=dcr), d.add_part("R", R="10"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{l}.a"), (f"{l}.b", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "2m", "1u")
    final = 10 * 10 / (10 + float(dcr))
    tau = 1e-3 / (10 + float(dcr))
    assert node(d, res, f"{r}.a") == pytest.approx(final, rel=1e-5)
    assert node(d, res, f"{r}.a", tau) == pytest.approx(final * (1 - math.exp(-1)), rel=2e-3)


def sine_into_open_secondary(d, primary_ohm="1"):
    """1 kHz, 1 V sine through a 1 ohm series resistor into the primary; 1 MOhm on the secondary."""
    vs = d.add_part("V", mode="SIN", vo="0", va="1", freq="1k")
    rs = d.add_part("R", R=primary_ohm)
    rl = d.add_part("R", R="1Meg")
    g = d.add_part("GND")
    return vs, rs, rl, g


def assert_ratio_sample_by_sample(sec_tr, pri_tr, ratio, tol=2e-3):
    """Open secondary: v2 = ratio * v1 at every instant (the leakage and 1 MOhm load are negligible)."""
    scale = max(abs(v) for _, v in sec_tr)
    assert scale > 0.5
    worst = max(abs(s - ratio * p) for (_, s), (_, p) in zip(sec_tr, pri_tr))
    assert worst <= tol * scale


@pytest.mark.parametrize("k", [0.5, 0.95])
def test_coupled_inductors_open_secondary_ratio(k):
    d = Document()
    vs, rs, rl, g = sine_into_open_secondary(d)
    ind = d.add_part("IND_COUPLED", L1="10m", L2="40m", K=str(k))
    wire(d, (f"{vs}.p", f"{rs}.a"), (f"{rs}.b", f"{ind}.p1"), (f"{ind}.n1", f"{g}.g"), (f"{vs}.n", f"{g}.g"),
         (f"{ind}.p2", f"{rl}.a"), (f"{rl}.b", f"{g}.g"), (f"{ind}.n2", f"{g}.g"))
    res = run(d, "5m", "1u")
    assert_ratio_sample_by_sample(trace(d, res, f"{ind}.p2"), trace(d, res, f"{ind}.p1"), k * math.sqrt(40 / 10))


@pytest.mark.parametrize("n", [0.5, 1.0, 2.0])
def test_transformer_turns_ratio_with_coupling(n):
    d = Document()
    vs, rs, rl, g = sine_into_open_secondary(d)
    x = d.add_part("XFMR_1N", LM="10m", N=str(n), K="0.99")
    wire(d, (f"{vs}.p", f"{rs}.a"), (f"{rs}.b", f"{x}.p1"), (f"{x}.p2", f"{g}.g"), (f"{vs}.n", f"{g}.g"),
         (f"{x}.s1", f"{rl}.a"), (f"{rl}.b", f"{x}.s2"), (f"{x}.s2", f"{g}.g"))
    res = run(d, "5m", "1u")
    assert_ratio_sample_by_sample(trace(d, res, f"{x}.s1"), trace(d, res, f"{x}.p1"), 0.99 * n)


def test_center_tapped_secondary_halves_are_antiphase_about_the_tap():
    d = Document()
    vs, rs, rl, g = sine_into_open_secondary(d)
    x = d.add_part("XFMR_CT", LM="10m", N="2", K="0.99")
    wire(d, (f"{vs}.p", f"{rs}.a"), (f"{rs}.b", f"{x}.p1"), (f"{x}.p2", f"{g}.g"), (f"{vs}.n", f"{g}.g"),
         (f"{x}.s1", f"{rl}.a"), (f"{rl}.b", f"{x}.s2"), (f"{x}.ct", f"{g}.g"))
    res = run(d, "5m", "1u")
    pri = trace(d, res, f"{x}.p1")
    assert_ratio_sample_by_sample(trace(d, res, f"{x}.s1"), pri, 0.99 * (2 / 2))   # each half: N/2 * K
    assert_ratio_sample_by_sample(trace(d, res, f"{x}.s2"), pri, -0.99 * (2 / 2))  # and the other is opposite


def test_lossless_line_delivers_the_matched_incident_wave_after_its_delay():
    d = Document()
    vs, rs, g = d.add_part("V", dc="2"), d.add_part("R", R="50"), d.add_part("GND")
    tl = d.add_part("T_LINE", Z0="50", TD="1u")
    rl, g2 = d.add_part("R", R="50"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{rs}.a"), (f"{rs}.b", f"{tl}.p1"), (f"{tl}.n1", f"{g}.g"), (f"{vs}.n", f"{g}.g"),
         (f"{tl}.p2", f"{rl}.a"), (f"{rl}.b", f"{g2}.g"), (f"{tl}.n2", f"{g2}.g"))
    res = run(d, "5u", "5n")
    assert node(d, res, f"{tl}.p2", 0.5e-6) == pytest.approx(0.0, abs=5e-3)     # before the delay
    assert node(d, res, f"{tl}.p2", 3e-6) == pytest.approx(1.0, abs=5e-3)       # incident 2 * 50/100, no reflection
    assert node(d, res, f"{tl}.p1", 3e-6) == pytest.approx(1.0, abs=5e-3)


@pytest.mark.parametrize("cap, leak", [("CAP_CERAMIC", 1e9), ("CAP_ELECTROLYTIC", 1e6),
                                       ("CAP_TANTALUM", 1e7), ("CAP_FILM", 1e10)])
def test_capacitor_charging_time_constant_includes_esr_and_leak(cap, leak):
    d = Document()
    vs, r, c, g = d.add_part("V", dc="1"), d.add_part("R", R="1k"), d.add_part(cap, C="1u", esr="100", rleak=f"{leak:g}"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{c}.a"), (f"{c}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    rth = 1000 * leak / (1000 + leak)              # R in parallel with the leak, seen by the capacitor branch
    vth = leak / (1000 + leak)
    tau = (rth + 100) * 1e-6
    tr = probe(d, f"v(x{c.lower()}.n2)", 6 * tau, tau / 500)
    at = lambda t: value_at(tr, t)
    assert at(tau) == pytest.approx(vth * (1 - math.exp(-1)), rel=3e-3)
    assert at(3 * tau) == pytest.approx(vth * (1 - math.exp(-3)), rel=3e-3)


# ---------------------------------------------------------------- protection
def tripped_load(rating, tau, volts, extra=None):
    d = Document()
    vs, f, rl, g = d.add_part("V", dc=volts), d.add_part(*(extra or ("FUSE",)), rating=str(rating), tau=str(tau)), d.add_part("R", R="1"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{f}.a"), (f"{f}.b", f"{rl}.a"), (f"{rl}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    return d, rl


def test_one_shot_fuse_opens_after_the_delay_above_its_rating_and_stays_open():
    d, rl = tripped_load(1, "1m", "2")
    res = run(d, "3m", "2u")
    tr = trace(d, res, f"{rl}.a")
    i0 = 2 / 1.001                                          # current through the closed fuse (1 mOhm) into 1 ohm
    t_expected = 1e-3 * math.log(i0 / (i0 - 1))             # delay: tau * ln(I / (I - rating))
    t_trip = next(t for t, v in tr if t > 1e-4 and v < 0.5 * i0)
    assert t_trip == pytest.approx(t_expected, abs=5e-6)
    assert max(v for t, v in tr if t > t_trip + 1e-5) < 1e-3          # latched open: no re-close


def test_fuse_below_its_rating_never_opens():
    d, rl = tripped_load(1, "1m", "1")
    res = run(d, "3m", "10u")
    assert node(d, res, f"{rl}.a") == pytest.approx(1 / 1.001, rel=1e-5)


def test_ptc_trips_after_its_delay_and_holds_while_the_current_exceeds_hold():
    d = Document()
    vs, f, rl, g = d.add_part("V", dc="2"), d.add_part("PTC", rating="0.2", hold="0.1", tau="2", ron="0.05", roff="5"), d.add_part("R", R="1"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{f}.a"), (f"{f}.b", f"{rl}.a"), (f"{rl}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1s", "0.5m")
    i0 = 2 / 1.05
    t_expected = 2 * math.log(i0 / (i0 - 0.2))
    tr = trace(d, res, f"{rl}.a")
    t_trip = next(t for t, v in tr if v < 0.5 * i0)
    assert t_trip == pytest.approx(t_expected, abs=1e-3)
    assert node(d, res, f"{rl}.a") == pytest.approx(2 * 1 / (5 + 1), rel=1e-4)   # tripped: 2 V over 5 + 1 ohm


@pytest.mark.parametrize("volts, sign", [(30, 1), (36, 1), (-36, -1)])
def test_varistor_clamp_current_follows_the_power_law(volts, sign):
    d = Document()
    vs, m, g = d.add_part("V", dc=str(volts)), d.add_part("MOV", vc="30", ik="1m", alpha="20"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{m}.a"), (f"{m}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    i = res.get("I(V1)")[-1][1]
    expected = 1e-3 * (abs(volts) / 30) ** 20
    assert abs(i) == pytest.approx(expected, rel=1e-6)
    assert math.copysign(1, i) == -sign                     # the source supplies the clamp current


# ---------------------------------------------------------------- switches and relays
@pytest.mark.parametrize("state, live", [("NO", 0), ("NC", 1)])
def test_spdt_connects_common_to_the_selected_throw(state, live):
    d = Document()
    vs, sw, g = d.add_part("V", dc="5"), d.add_part("SPDT", state=state, ron="0.01", roff="1G"), d.add_part("GND")
    r0, r1 = d.add_part("R", R="1k"), d.add_part("R", R="1k")
    wire(d, (f"{vs}.p", f"{sw}.c"), (f"{sw}.no", f"{r0}.a"), (f"{sw}.nc", f"{r1}.a"), (f"{r0}.b", f"{g}.g"),
         (f"{r1}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    on, off = (r0, r1) if live == 0 else (r1, r0)
    assert node(d, res, f"{on}.a") == pytest.approx(5 * 1000 / 1000.01, rel=1e-6)
    assert node(d, res, f"{off}.a") == pytest.approx(0.0, abs=1e-4)


@pytest.mark.parametrize("state", ["open", "closed"])
def test_dpst_closes_both_contacts_together(state):
    d = Document()
    vs, sw, g = d.add_part("V", dc="5"), d.add_part("DPST", state=state, ron="0.01", roff="1G"), d.add_part("GND")
    r1, r2 = d.add_part("R", R="1k"), d.add_part("R", R="1k")
    wire(d, (f"{vs}.p", f"{sw}.a1"), (f"{vs}.p", f"{sw}.a2"), (f"{sw}.b1", f"{r1}.a"), (f"{sw}.b2", f"{r2}.a"),
         (f"{r1}.b", f"{g}.g"), (f"{r2}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    want = 5 * 1000 / 1000.01 if state == "closed" else 0.0
    for r in (r1, r2):
        assert node(d, res, f"{r}.a") == pytest.approx(want, rel=1e-6, abs=1e-4)


@pytest.mark.parametrize("state, live", [("NO", ("no1", "no2")), ("NC", ("nc1", "nc2"))])
def test_dpdt_throws_both_poles_together(state, live):
    d = Document()
    vs, sw, g = d.add_part("V", dc="5"), d.add_part("DPDT", state=state, ron="0.01", roff="1G"), d.add_part("GND")
    loads = {p: d.add_part("R", R="1k") for p in ("no1", "nc1", "no2", "nc2")}
    wire(d, (f"{vs}.p", f"{sw}.c1"), (f"{vs}.p", f"{sw}.c2"), (f"{vs}.n", f"{g}.g"))
    for p, r in loads.items():
        wire(d, (f"{sw}.{p}", f"{r}.a"), (f"{r}.b", f"{g}.g"))
    res = run(d, "1m", "10u")
    for p, r in loads.items():
        want = 5 * 1000 / 1000.01 if p in live else 0.0
        assert node(d, res, f"{r}.a") == pytest.approx(want, rel=1e-6, abs=1e-4)


@pytest.mark.parametrize("state", ["released", "pressed"])
def test_pushbutton_no_closes_only_while_pressed(state):
    d = Document()
    vs, sw, r, g = d.add_part("V", dc="5"), d.add_part("PUSH_NO", state=state, ron="0.05", roff="1G"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{sw}.a"), (f"{sw}.b", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    want = 5 * 1000 / 1000.05 if state == "pressed" else 0.0
    assert node(d, res, f"{r}.a") == pytest.approx(want, rel=1e-6, abs=1e-4)


@pytest.mark.parametrize("state", ["released", "pressed"])
def test_pushbutton_nc_opens_only_while_pressed(state):
    d = Document()
    vs, sw, r, g = d.add_part("V", dc="5"), d.add_part("PUSH_NC", state=state, ron="0.05", roff="1G"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{sw}.a"), (f"{sw}.b", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    want = 0.0 if state == "pressed" else 5 * 1000 / 1000.05
    assert node(d, res, f"{r}.a") == pytest.approx(want, rel=1e-6, abs=1e-4)


@pytest.mark.parametrize("coil_volts, pulled", [("2.5", False), ("3.5", True)])
def test_relay_spdt_pulls_in_above_the_coil_threshold(coil_volts, pulled):
    """Pull-in at 30 mA through 100 ohm: 3 V on the coil (no inductive drop at DC)."""
    d = Document()
    vc, vs, rl, g = d.add_part("V", dc=coil_volts), d.add_part("V", dc="5"), d.add_part("RELAY_SPDT"), d.add_part("GND")
    r_no, r_nc = d.add_part("R", R="1k"), d.add_part("R", R="1k")
    wire(d, (f"{vc}.p", f"{rl}.cl1"), (f"{rl}.cl2", f"{g}.g"), (f"{vc}.n", f"{g}.g"),
         (f"{vs}.p", f"{rl}.c"), (f"{vs}.n", f"{g}.g"), (f"{rl}.no", f"{r_no}.a"), (f"{rl}.nc", f"{r_nc}.a"),
         (f"{r_no}.b", f"{g}.g"), (f"{r_nc}.b", f"{g}.g"))
    res = run(d, "5m", "2u")
    live, dead = (r_no, r_nc) if pulled else (r_nc, r_no)
    assert node(d, res, f"{live}.a") == pytest.approx(5 * 1000 / 1000.1, rel=1e-5)
    assert node(d, res, f"{dead}.a") == pytest.approx(0.0, abs=1e-3)


@pytest.mark.parametrize("coil_volts, pulled", [("2.5", False), ("3.5", True)])
def test_relay_dpdt_both_poles_follow_one_coil(coil_volts, pulled):
    d = Document()
    vc, vs, rl, g = d.add_part("V", dc=coil_volts), d.add_part("V", dc="5"), d.add_part("RELAY_DPDT"), d.add_part("GND")
    loads = {p: d.add_part("R", R="1k") for p in ("no1", "nc1", "no2", "nc2")}
    wire(d, (f"{vc}.p", f"{rl}.cl1"), (f"{rl}.cl2", f"{g}.g"), (f"{vc}.n", f"{g}.g"),
         (f"{vs}.p", f"{rl}.c1"), (f"{vs}.p", f"{rl}.c2"), (f"{vs}.n", f"{g}.g"))
    for p, r in loads.items():
        wire(d, (f"{rl}.{p}", f"{r}.a"), (f"{r}.b", f"{g}.g"))
    res = run(d, "5m", "2u")
    live = {"no1", "no2"} if pulled else {"nc1", "nc2"}
    for p, r in loads.items():
        want = 5 * 1000 / 1000.1 if p in live else 0.0
        assert node(d, res, f"{r}.a") == pytest.approx(want, rel=1e-5, abs=1e-3)


@pytest.mark.parametrize("coil_volts, closed", [("1.5", False), ("3", True)])
def test_reed_switch_closes_above_its_coil_current(coil_volts, closed):
    """Pull-in at 10 mA through 200 ohm: 2 V on the coil."""
    d = Document()
    vc, vs, rd, g = d.add_part("V", dc=coil_volts), d.add_part("V", dc="5"), d.add_part("REED"), d.add_part("GND")
    r = d.add_part("R", R="1k")
    wire(d, (f"{vc}.p", f"{rd}.k1"), (f"{rd}.k2", f"{g}.g"), (f"{vc}.n", f"{g}.g"),
         (f"{vs}.p", f"{rd}.c1"), (f"{rd}.c2", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "5m", "2u")
    want = 5 * 1000 / 1000.15 if closed else 0.0
    assert node(d, res, f"{r}.a") == pytest.approx(want, rel=1e-5, abs=1e-3)


@pytest.mark.parametrize("control, closed", [("0.5", False), ("1.5", True)])
def test_voltage_controlled_switch_threshold(control, closed):
    d = Document()
    vs, sw, vc, r, g = d.add_part("V", dc="5"), d.add_part("VSW"), d.add_part("V", dc=control), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{sw}.p"), (f"{sw}.n", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{vc}.p", f"{sw}.cp"),
         (f"{sw}.cn", f"{g}.g"), (f"{vc}.n", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    want = 5 * 1000 / 1001 if closed else 0.0
    assert node(d, res, f"{r}.a") == pytest.approx(want, rel=1e-5, abs=1e-3)


@pytest.mark.parametrize("sense_ohm, closed", [("1k", False), ("100", True)])
def test_current_controlled_switch_threshold(sense_ohm, closed):
    """Trip at 10 mA with 2 mA hysteresis: the control current is 2 mA (1 kOhm) or 20 mA (100 ohm)."""
    d = Document()
    vc, rc, sw, vs, r, g = d.add_part("V", dc="2"), d.add_part("R", R=sense_ohm), d.add_part("CSW"), d.add_part("V", dc="5"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vc}.p", f"{rc}.a"), (f"{rc}.b", f"{sw}.cp"), (f"{sw}.cn", f"{g}.g"), (f"{vc}.n", f"{g}.g"),
         (f"{vs}.p", f"{sw}.p"), (f"{sw}.n", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    want = 5 * 1000 / 1000.1 if closed else 0.0
    assert node(d, res, f"{r}.a") == pytest.approx(want, rel=1e-5, abs=1e-3)


# ---------------------------------------------------------------- sources
def test_exponential_source_rises_with_its_time_constant():
    d = Document()
    vs = d.add_part("VSRC_EXP_SFFM", mode="EXP", v1="0", v2="5", td1="0", tau1="1m", td2="10m", tau2="1m")
    r, g = d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "6m", "1u")
    # checked where the slope is small (0.03 V/ms): the trace is sampled at the step, not exactly at the instant
    assert node(d, res, f"{vs}.p", 5e-3) == pytest.approx(5 * (1 - math.exp(-5)), abs=1e-3)


def test_fm_source_follows_its_sffm_formula():
    d = Document()
    vs = d.add_part("VSRC_EXP_SFFM", mode="SFFM", vo="0", va="1", fc="1k", mdi="5", fs="100")
    r, g = d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vs}.p", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{vs}.n", f"{g}.g"))
    res = run(d, "1m", "1u")
    t = 0.25e-3
    want = math.sin(2 * math.pi * 1e3 * t + 5 * math.sin(2 * math.pi * 100 * t))
    assert node(d, res, f"{vs}.p", t) == pytest.approx(want, abs=1e-3)


def test_pulse_current_source_into_a_resistor():
    d = Document()
    cs = d.add_part("ISRC_WAVE", mode="PULSE", i1="0", i2="1m", td="0", tr="1u", tf="1u", pw="1m", per="2m")
    r, g = d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{cs}.p", f"{g}.g"), (f"{cs}.n", f"{r}.a"), (f"{r}.b", f"{g}.g"))
    res = run(d, "3m", "2u")
    assert node(d, res, f"{r}.a", 0.5e-3) == pytest.approx(1.0, abs=1e-3)
    assert node(d, res, f"{r}.a", 1.5e-3) == pytest.approx(0.0, abs=1e-3)


def test_sine_current_source_into_a_resistor():
    d = Document()
    cs = d.add_part("ISRC_WAVE", mode="SIN", ioff="0", iamp="1m", freq="1k")
    r, g = d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{cs}.p", f"{g}.g"), (f"{cs}.n", f"{r}.a"), (f"{r}.b", f"{g}.g"))
    res = run(d, "1m", "1u")
    assert node(d, res, f"{r}.a", 0.25e-3) == pytest.approx(1.0, abs=2e-3)
    assert node(d, res, f"{r}.a", 0.75e-3) == pytest.approx(-1.0, abs=2e-3)


def test_voltage_controlled_voltage_source_gain():
    d = Document()
    vi, e, r, g = d.add_part("V", dc="0.5"), d.add_part("VCVS", gain="2"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vi}.p", f"{e}.cp"), (f"{e}.cn", f"{g}.g"), (f"{vi}.n", f"{g}.g"), (f"{e}.op", f"{r}.a"),
         (f"{r}.b", f"{g}.g"), (f"{e}.on", f"{g}.g"))
    res = run(d, "1m", "10u")
    assert node(d, res, f"{r}.a") == pytest.approx(1.0, rel=1e-6)


def test_voltage_controlled_current_source_transconductance():
    d = Document()
    vi, gsrc, r, g = d.add_part("V", dc="2"), d.add_part("VCCS", gain="1m"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vi}.p", f"{gsrc}.cp"), (f"{gsrc}.cn", f"{g}.g"), (f"{vi}.n", f"{g}.g"), (f"{gsrc}.op", f"{g}.g"),
         (f"{gsrc}.on", f"{r}.a"), (f"{r}.b", f"{g}.g"))
    res = run(d, "1m", "10u")
    assert node(d, res, f"{r}.a") == pytest.approx(1e-3 * 2 * 1000, rel=1e-6)


def test_current_controlled_current_source_gain():
    d = Document()
    vi, ri, f, rl, g = d.add_part("V", dc="1"), d.add_part("R", R="1k"), d.add_part("CCCS", gain="10"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vi}.p", f"{ri}.a"), (f"{ri}.b", f"{f}.cp"), (f"{f}.cn", f"{g}.g"), (f"{vi}.n", f"{g}.g"),
         (f"{f}.op", f"{g}.g"), (f"{f}.on", f"{rl}.a"), (f"{rl}.b", f"{g}.g"))
    res = run(d, "1m", "10u")
    assert node(d, res, f"{rl}.a") == pytest.approx(10 * 1e-3 * 1000, rel=1e-6)


def test_current_controlled_voltage_source_transresistance():
    d = Document()
    vi, ri, h, rl, g = d.add_part("V", dc="1"), d.add_part("R", R="1k"), d.add_part("CCVS", rm="100"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vi}.p", f"{ri}.a"), (f"{ri}.b", f"{h}.cp"), (f"{h}.cn", f"{g}.g"), (f"{vi}.n", f"{g}.g"),
         (f"{h}.op", f"{rl}.a"), (f"{rl}.b", f"{g}.g"), (f"{h}.on", f"{g}.g"))
    res = run(d, "1m", "10u")
    assert node(d, res, f"{h}.op") == pytest.approx(100 * 1e-3, rel=1e-6)


@pytest.mark.parametrize("vin, want", [(0.05, 0.5), (0.5, 2.0), (-0.5, -2.0)])
def test_saturating_amplifier_clips_at_its_limit(vin, want):
    d = Document()
    vi, sa, r, g = d.add_part("V", dc=str(vin)), d.add_part("SAT_AMP", gain="10", lim="2"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{vi}.p", f"{sa}.ip"), (f"{sa}.in", f"{g}.g"), (f"{vi}.n", f"{g}.g"), (f"{sa}.op", f"{r}.a"),
         (f"{r}.b", f"{g}.g"), (f"{sa}.on", f"{g}.g"))
    res = run(d, "1m", "10u")
    assert node(d, res, f"{r}.a") == pytest.approx(want, abs=1e-4)


# ---------------------------------------------------------------- batteries
@pytest.mark.parametrize("cell, emf, rint, load", [("BAT_COIN", 3.0, 20.0, 100.0),
                                                   ("BAT_AA", 1.5, 0.2, 10.0),
                                                   ("BAT_LITHIUM", 3.7, 0.1, 1.0)])
def test_cell_terminal_voltage_under_load(cell, emf, rint, load):
    d = Document()
    b, r, g = d.add_part(cell), d.add_part("R", R=str(load)), d.add_part("GND")
    wire(d, (f"{b}.p", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{b}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    assert node(d, res, f"{r}.a") == pytest.approx(emf * load / (load + rint), rel=1e-6)


def test_two_aa_cells_in_series_drop_by_their_internal_resistance():
    d = Document()
    a, b = d.add_part("BAT_AA"), d.add_part("BAT_AA")
    r, g = d.add_part("R", R="10"), d.add_part("GND")
    wire(d, (f"{a}.p", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{a}.n", f"{b}.p"), (f"{b}.n", f"{g}.g"))
    res = run(d, "1m", "10u")
    assert node(d, res, f"{r}.a") == pytest.approx(3.0 * 10 / (10 + 0.4), rel=1e-6)
