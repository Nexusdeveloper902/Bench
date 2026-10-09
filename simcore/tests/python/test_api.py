"""Public Python API smoke: `import simcore` from the installed wheel
(regression: the wheel used to ship only `_simcore`), RC vs analytic, and
unknown observables raising instead of returning empty series.

Run: maturin build && pip install --force-reinstall target/wheels/*.whl
     python -m pytest tests/python
"""
import math

import pytest

import simcore
from simcore import InvalidConfigError, Simulation


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
    assert simcore.Simulation.__module__ == "simcore"
