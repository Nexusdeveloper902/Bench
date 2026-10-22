"""W3 (GAPS G27, G28, G29), real ngspice and the real validator.

G27/G28: the humidity capacitor and the solenoid take their stimulus as a node and conserve charge and flux on a
stimulus step. Oracles are the closed forms (q and phi continuous, relaxation with the new time constant); the
non-conserving form would keep the voltage (current) instead and fail them by a wide margin. Probes:
docs/plans/gap-closure/probes/.

G29: the validator evaluates every B source, E/G VALUE and behavioural R/C/L expression over the stimulus ranges a
package declares (a number with both `min` and `max`) and the other params' defaults. A domain error, a division
through 0 or an overflow inside those ranges rejects the package; what the check cannot bound keeps the part
`limited` and says why."""
import dataclasses
import math

import pytest

from app import electrical
from app.catalog import CATALOG
from app.sim import replay_document
from test_library_batch_c import Circuit
from test_subckt_packages import p, rejected, sim_problems, xpart


def at(series, t):
    return min(series, key=lambda s: abs(s[0] - t))[1]


# ---------------------------------------------------------------- G27: charge on a humidity step
def humidity_step(rh0, rh1, t0, tstop, tstep, c0="100p", cs="2p", r="1k"):
    c = Circuit(); hc = c.add("HUMIDITY_CAP", rh=str(rh0), c0=c0, cs=cs); rr = c.add("R", R=r)
    c.vdc(1, f"{rr}.a"); c.w(f"{rr}.b", f"{hc}.a"); c.w(f"{hc}.b", "0")
    c.d.tstop, c.d.tstep = tstop, tstep
    res = replay_document(c.d, [(t0, hc, "rh", rh1)])
    assert res.ok, res.message
    return res.get(f"V({c.d.nets()[hc + '.a']})")


def test_humidity_step_conserves_charge_and_relaxes_with_the_new_time_constant():
    """C(20 %) = 140 pF charged to 1 V, then C(80 %) = 260 pF: q is continuous, so v jumps to 140/260 and returns to 1 V
    with tau = R·C1 = 260 ns (an ngspice C='expr(V(stim))' would stay at 1 V): v = 1 - (1 - C0/C1)·exp(-k)."""
    c0, c1, tau, t0 = 140e-12, 260e-12, 260e-9, 3e-6
    v = humidity_step(20, 80, t0, "6u", "1n")
    assert at(v, t0 - 1e-9) == pytest.approx(1.0, abs=1e-4)
    for k in (0.01, 0.5, 1, 2, 4):
        t = t0 + k * tau
        assert at(v, t) == pytest.approx(1 - (1 - c0 / c1) * math.exp(-k), abs=3e-3), k
    assert at(v, 6e-6 - 1e-9) == pytest.approx(1.0, abs=1e-4)


def test_humidity_step_down_raises_the_voltage_and_the_charge_moved_is_dc_times_v():
    """C 260 -> 140 pF at 1 V: v jumps to 260/140, and the charge the source supplies is (C1 - C0)·V = -120 pC."""
    c0, c1, t0 = 260e-12, 140e-12, 3e-6
    v = humidity_step(80, 20, t0, "6u", "1n")
    tau = 1e3 * c1
    for k in (0.01, 1, 2):
        assert at(v, t0 + k * tau) == pytest.approx(1 + (c0 / c1 - 1) * math.exp(-k), abs=4e-3), k
    assert at(v, 6e-6 - 1e-9) == pytest.approx(1.0, abs=1e-4)
    pts = [(t, x) for t, x in v if t >= t0]
    q = sum((b[0] - a[0]) * ((1 - a[1]) + (1 - b[1])) / 2e3 for a, b in zip(pts, pts[1:]))     # integral of (1 - v)/R from t0
    assert q == pytest.approx(c1 - c0, rel=0.03)


def test_humidity_ramp_has_no_timestep_artifact():
    """A slow RH change (a live slider drag) gives the smooth quasi-static answer: no spike at the change."""
    v = humidity_step(20, 80, 3e-6, "6u", "1n")
    after = [x for t, x in v if t > 3e-6]
    assert max(after) <= 1.0 + 1e-3 and min(after) >= 140 / 260 - 3e-3


# ---------------------------------------------------------------- G28: flux on a plunger step
def plunger_step(pos0, pos1, t0, tstop, tstep):
    c = Circuit(); s = c.add("SOLENOID", pos=str(pos0)); v = c.vdc(10, f"{s}.a"); c.w(f"{s}.b", "0")
    c.d.tstop, c.d.tstep = tstop, tstep
    res = replay_document(c.d, [(t0, s, "pos", pos1)])
    assert res.ok, res.message
    return [(t, abs(i)) for t, i in res.get(f"I({v})")]


def test_plunger_step_conserves_flux_and_the_current_relaxes_with_the_new_l_over_r():
    """10 V over RC = 10 ohm. L 20 mH settles to 1 A; closing the plunger (60 mH) at 15 ms keeps phi = L·i, so the
    current falls to 1/3 A and rises again with tau = 6 ms (an ngspice L='expr(V(stim))' would keep 1 A)."""
    t0 = 15e-3
    i = plunger_step(0, 1, t0, "40m", "2u")
    assert at(i, t0 - 1e-6) == pytest.approx(1.0, abs=1e-3)
    assert at(i, t0 + 4e-6) == pytest.approx(1 / 3, abs=6e-3)
    for k in (0.5, 1, 2, 3):
        assert at(i, t0 + k * 6e-3) == pytest.approx(1 - (2 / 3) * math.exp(-k), abs=4e-3), k


def test_plunger_opening_raises_the_current_with_flux_conserved():
    """60 mH settled to 1 A, opening to 20 mH at 40 ms: the current jumps to 3 A and falls back with tau = 2 ms."""
    t0 = 40e-3
    i = plunger_step(1, 0, t0, "55m", "2u")
    assert at(i, t0 + 4e-6) == pytest.approx(3.0, abs=0.04)
    for k in (0.5, 1, 2):
        assert at(i, t0 + k * 2e-3) == pytest.approx(1 + 2 * math.exp(-k), abs=0.02), k


# ---------------------------------------------------------------- G29: shipped parts
NINE = ["LDR", "PTC_THERMISTOR", "RTD_PT100", "RTD_PT1000", "FSR", "STRAIN_GAUGE", "STRAIN_BRIDGE", "POSITION_SENSOR",
        "PRESSURE_BRIDGE", "MOV", "SAT_AMP"]


@pytest.mark.parametrize("id", NINE)
def test_b_driven_parts_are_proved_over_their_stimulus_range_and_full(id):
    sec = CATALOG.get(id).sim["electrical"]
    assert sec["_status"] == "full" and not sec["_limits"]


@pytest.mark.parametrize("id,key,change", [
    ("LDR", "lux", dict(min_exclusive=False)),            # lux = 0 is a power of 0 with a negative exponent
    ("FSR", "force", dict(min_exclusive=False)),
    ("STRAIN_GAUGE", "eps", dict(min=-5e5, max=5e5)),     # 1 + GF·eps·1e-6 reaches 0 at -500000
    ("STRAIN_BRIDGE", "eps", dict(min=-5e5, max=5e5)),
    ("RTD_PT100", "temp", dict(min=0, max=8000)),         # 1 + A·T + B·T² passes through 0 near 6980 degC
    ("PTC_THERMISTOR", "temp", dict(min=-1000, max=200)),  # exp(-(T - 120)/3) passes the clamp of exp()
])
def test_a_wider_stimulus_range_than_the_law_allows_is_caught(id, key, change):
    c = CATALOG.get(id)
    params = tuple(dataclasses.replace(q, **change) if q.key == key else q for q in c.params)
    status, errors, _, _ = electrical.validate_bodies(c.sim["electrical"]["_units"], c.sim["electrical"], c.pins, params)
    assert status == "failed" and errors and errors[0][0].split(":")[0] in ("B-DOMAIN", "B-DIVZERO", "B-OVERFLOW"), (id, errors)
    assert key.upper() in errors[0][0] or "V(stim)" in errors[0][0], errors     # the message names a witness


def test_a_param_with_one_bound_is_checked_at_its_default_only(extra_root):
    """Only a number with both min and max is a stimulus range; Y (min only) is a point at its default, even though
    its allowed values include 0 (the documented ceiling: the user's own value is checked when the deck runs)."""
    body_part(extra_root, "V(a,b)/Y", dict(min=0, max=1), extra_params=[dict(p("y", "1"), min=-5)], line_extra=" Y={y}", header_extra=" Y=1")
    assert CATALOG.get("SENS").sim["electrical"]["_status"] == "full"


# ---------------------------------------------------------------- G29: authored packages (positive and negative)
def body_part(root, expr, rng, extra_params=(), kind="B1 a b I=", line_extra="", header_extra=""):
    xpart(root, "SENS", "{dev} {a} {b} sens X={x}" + line_extra, reload=False,
          params=[dict(p("x", "1"), **rng), *extra_params],
          body=f".subckt sens a b params: X=1{header_extra}\nVSTIM stim 0 DC {{X}}\nRSTIM stim 0 1G\n{kind}{{{expr}}}\n.ends sens\n")
    from app import catalog
    catalog.reload([catalog.BUILTIN_ROOT, root])


@pytest.mark.parametrize("expr,rng,code", [
    ("V(a,b)*sqrt(V(stim))", dict(min=-1, max=4), "B-DOMAIN"),
    ("V(a,b)*ln(V(stim))", dict(min=0, max=4), "B-DOMAIN"),
    ("V(a,b)/(V(stim)-2)", dict(min=1, max=3), "B-DIVZERO"),
    ("V(a,b)*exp(V(stim))", dict(min=0, max=300), "B-OVERFLOW"),
    ("V(a,b)*pow(V(stim),0.5)", dict(min=-4, max=4), "B-DOMAIN"),
    ("V(a,b)*asin(V(stim))", dict(min=0, max=2), "B-DOMAIN"),
])
def test_authored_package_with_a_hazard_in_its_range_is_rejected_with_a_witness(extra_root, expr, rng, code):
    body_part(extra_root, expr, rng)
    rejected("SENS", code, "(at X = ")


@pytest.mark.parametrize("expr,rng", [
    ("V(a,b)*sqrt(V(stim))", dict(min=0, max=4)),
    ("V(a,b)*ln(V(stim))", dict(min=0.001, max=4)),
    ("V(a,b)/(V(stim)+2)", dict(min=1, max=3)),
    ("V(a,b)*exp(V(stim))", dict(min=0, max=100)),
    ("V(a,b)*(V(stim)>0 ? ln(V(stim)) : 0)", dict(min=-4, max=4)),
    ("V(a,b)/(1+abs(V(stim)))", dict(min=-4, max=4)),
])
def test_authored_package_proved_over_its_range_is_full(extra_root, expr, rng):
    body_part(extra_root, expr, rng)
    assert "SENS" in CATALOG.defs and CATALOG.problems == []
    sec = CATALOG.get("SENS").sim["electrical"]
    assert sec["_status"] == "full" and not sec["_limits"]


@pytest.mark.parametrize("expr,why", [
    ("V(a,b)/V(a)", "division by `V(a)`: its range is not known"),
    ("V(a,b)*sqrt(V(a))", "sqrt(V(a)): the range of the argument is not known"),
    ("V(a,b)*ddt(V(stim))", "ddt() is not evaluated"),
    ("V(a,b)*table(V(stim),0,0,1,1)", None),
])
def test_what_the_check_cannot_bound_stays_limited_and_says_why(extra_root, expr, why):
    body_part(extra_root, expr, dict(min=0, max=4))
    if why is None:
        return rejected("SENS", "table")       # PSpice syntax: refused outright, not merely unproved
    sec = CATALOG.get("SENS").sim["electrical"]
    assert sec["_status"] == "limited" and any(why in x for x in sec["_limits"]), sec["_limits"]


def test_interval_overestimate_is_unproved_not_an_error(extra_root):
    """x - x is 0, but intervals see [-w, w]: no sample reaches a zero of 1/(0.5 + x - x), so it is not an error."""
    body_part(extra_root, "V(a,b)/(0.5+V(stim)-V(stim))", dict(min=-1, max=1))
    sec = CATALOG.get("SENS").sim["electrical"]
    assert sec["_status"] == "limited" and any("cannot rule out" in x for x in sec["_limits"])


def test_behavioural_charge_form_in_a_package_is_value_checked(extra_root):
    xpart(extra_root, "QCAP", "{dev} {a} {b} qcap X={x}", params=[dict(p("x", "1"), min=0, max=2)],
          body=".subckt qcap a b params: X=1\nVSTIM stim 0 DC {X}\nRSTIM stim 0 1G\nC1 a b q='1n/(V(stim)-1)*V(a,b)'\n.ends qcap\n")
    rejected("QCAP", "B-DIVZERO")
    xpart(extra_root, "QCAP", "{dev} {a} {b} qcap X={x}", params=[dict(p("x", "1.5"), min=1, max=2, min_exclusive=True)],
          body=".subckt qcap a b params: X=1\nVSTIM stim 0 DC {X}\nRSTIM stim 0 1G\nC1 a b q='1n/(V(stim)-1)*V(a,b)'\n.ends qcap\n")
    assert "QCAP" in CATALOG.defs and CATALOG.get("QCAP").sim["electrical"]["_status"] == "full"


def test_the_non_conserving_native_forms_are_warned(extra_root):
    xpart(extra_root, "NCAP", "{dev} {a} {b} ncap X={x}", params=[dict(p("x", "1"), min=0, max=2)],
          body=".subckt ncap a b params: X=1\nVSTIM stim 0 DC {X}\nRSTIM stim 0 1G\nC1 a b C='1n*(1+V(stim))'\n.ends ncap\n")
    assert "NCAP" in CATALOG.defs
    assert any("B-CL-FORM" in x["message"] and "conserve" in x["message"] for x in CATALOG.problems), CATALOG.problems
