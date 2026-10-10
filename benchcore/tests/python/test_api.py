"""Public Python API smoke: `import benchcore` from the installed wheel
(regression: the wheel used to ship only `_benchcore`), RC vs analytic, and
unknown observables raising instead of returning empty series.

Run: maturin build && pip install --force-reinstall target/wheels/*.whl
     python -m pytest tests/python
"""
import math

import pytest

import benchcore
from benchcore import InvalidConfigError, Simulation


def rc():
    return Simulation("RC").build_circuit(
        ["V1 in 0 DC 5", "R1 in out 1k", "C1 out 0 1u IC=0"]
    ).configure(tstep=10e-6, tstop=5e-3, tmax=10e-6)


def test_rc_matches_analytic():
    s = rc().observe("v(out)")
    s.run()
    pts = s.observable("v(out)")
    assert len(pts) > 50
    # 20 mV: same rationale as Rust T01 (docs/testing.md)
    assert max(abs(v - 5 * (1 - math.exp(-t / 1e-3))) for t, v in pts) < 0.020


def test_unknown_observable_raises():
    with pytest.raises(InvalidConfigError, match="v\\(nope\\)"):
        rc().observe("v(out)", "v(nope)").run()


def test_wrapper_is_the_package():
    assert benchcore.Simulation.__module__ == "benchcore"


# Standard 2N2222 card (task 7 ships the same).
Q2N2222 = (
    ".model q2n2222 NPN(IS=14.34f XTI=3 EG=1.11 VAF=74.03 BF=255.9 NE=1.307 "
    "ISE=14.34f IKF=.2847 XTB=1.5 BR=6.092 NC=2 ISC=0 IKR=0 RC=1 CJC=7.306p "
    "MJC=.3416 VJC=.75 FC=.5 CJE=22.01p MJE=.377 VJE=.75 TR=46.91n TF=411.1p "
    "ITF=.6 VTF=1.7 XTF=3 RB=10)"
)


def ce_amp(save=True):
    s = Simulation("ce").build_circuit(
        ["Vcc vcc 0 DC 5", "Rb vcc b 430k", "Rc vcc c 1k", "Q1 c b 0 q2n2222"]
    ).add_model(Q2N2222)
    if save:
        s.add(".save all @q1[ic] @q1[ib]")
    s.configure(tstep=1e-6, tstop=1e-4)
    return s.observe("v(c)", "v(b)", "@q1[ic]", "@q1[ib]")


def test_ce_amp_operating_point():
    s = ce_amp()
    assert s.validate() == []
    s.run()
    vc, vb, ic, ib = (
        s.observable(n)[-1][1] for n in ("v(c)", "v(b)", "@q1[ic]", "@q1[ib]")
    )
    assert abs(vc - 3.389) < 0.01
    assert abs(ic - 1.611e-3) < 1e-5
    assert abs(ib - 10.10e-6) < 0.05e-6
    assert abs(ic - (5 - vc) / 1e3) < 1e-7    # KVL on Rc (not Bf*Ib: Early effect)
    assert abs(ib - (5 - vb) / 430e3) < 1e-9  # KVL on Rb


def test_unsaved_device_current_raises():
    with pytest.raises(InvalidConfigError, match="@q1\\[ic\\]"):
        ce_amp(save=False).run()
