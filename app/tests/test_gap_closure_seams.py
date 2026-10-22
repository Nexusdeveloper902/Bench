"""Seams between the gap-closure workstreams (wave 3 review), through Document -> app.sim -> ngspice: AC analysis (G17)
of the charge and flux sensor forms (G27, G28) against the closed-form corner, and a `compat = "ps"` package (G10) in
an AC run and a live run (the live path was untested; both go through ngspice.rs do_circ)."""
import math

import pytest
from app.sim import LiveSession, run_document
from test_compat_ps import build, ps_root  # noqa: F401
from test_library_batch_c import Circuit


def ac_run(c, f1, f2):
    c.d.analysis, c.d.ac = "ac", {"sweep": "dec", "points": "20", "fstart": f1, "fstop": f2}
    r = run_document(c.d)
    assert r.ok and r.analysis == "ac", r.message + "\n" + r.details
    return r


def ac_at(c, r, pin, f):
    """(frequency, complex V) of the sweep point nearest f."""
    return min(r.get(f"V({c.d.nets()[pin]})"), key=lambda p: abs(math.log(p[0] / f)))


def parts(c, kind):
    """A 1 V AC source (its n grounded), the part and a 100 ohm resistor, for the caller to wire in series."""
    src, x, r = c.add("V", dc=0, ac=1), c.add(kind), c.add("R", R="100")
    c.w(f"{src}.n", "0")
    return src, x, r


def test_humidity_cap_charge_form_has_the_rc_corner_in_ac():
    c = Circuit()
    src, x, r = parts(c, "HUMIDITY_CAP")          # C = c0 + cs * rh = 180p + 0.4p * 50 = 200 pF
    c.w(f"{src}.p", f"{r}.a"); c.w(f"{r}.b", f"{x}.a"); c.w(f"{x}.b", "0")
    fc = 1 / (2 * math.pi * 100 * 200e-12)
    f, v = ac_at(c, ac_run(c, "1M", "1G"), f"{x}.a", fc)
    assert v == pytest.approx(1 / (1 + 1j * f / fc), abs=1e-6)


def test_solenoid_flux_form_has_the_rl_corner_in_ac():
    c = Circuit()
    src, x, r = parts(c, "SOLENOID")              # L = lo at pos 0 = 20 mH, rc = 10 ohm in series
    c.w(f"{src}.p", f"{x}.a"); c.w(f"{x}.b", f"{r}.a"); c.w(f"{r}.b", "0")
    fc = 110 / (2 * math.pi * 20e-3)
    f, v = ac_at(c, ac_run(c, "1", "1M"), f"{r}.a", fc)
    assert v == pytest.approx((100 / 110) / (1 + 1j * f / fc), abs=1e-6)


def test_ps_package_runs_in_ac_and_live(ps_root):
    c, x = build(0.5)                                          # clamp to [-1, 2] V, 1 ohm out, 10k load
    src = next(n for n, p in c.d.parts.items() if p["kind"] == "V")
    c.d.parts[src]["params"]["ac"] = "1"
    f, v = ac_at(c, ac_run(c, "10", "1M"), f"{x}.o", 1e3)     # inside the clamp: unity small-signal gain
    assert v == pytest.approx(10e3 / (10e3 + 1), abs=1e-6)
    c.d.analysis, c.d.tstop, c.d.tstep = "transient", "1m", "10u"
    c.d.parts[src]["params"]["dc"] = "3"                        # above HI: LIMIT() clamps only in ps mode
    s = LiveSession(c.d)
    assert s.result.status == "running", s.result.message
    try:
        for i, _ in enumerate(s):
            if i >= 3: break
    finally:
        s.cancel(); s.close()
    assert s.result.get(f"V({c.d.nets()[f'{x}.o']})")[-1][1] == pytest.approx(2 * 10e3 / (10e3 + 1), abs=1e-3)
