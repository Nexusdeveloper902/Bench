"""G27/G28 probe (W3): ngspice-47 behavioural capacitor, against charge conservation on a stimulus step.

Circuit: V=1 V -- R=1k -- node a -- C(stim) -- 0, stim steps 0 -> 1 at t0 = 5 us over `tr`, C = C0 + CS*stim
(C0 = CS = 1 nF).  Reference: the charge form dq/dt = (V - q/C(t))/R, q = C(t) v, integrated with scipy
(LSODA, rtol 1e-12) over the same C(t) ramp.  q is continuous across the step; v jumps to C0/C1*V.
The voltage form i = C(t) dv/dt keeps v continuous instead.  Forms probed:
  native   C1 a 0 C='C0+CS*V(stim)'            ngspice's own behavioural C
  nativeq  C1 a 0 q='(C0+CS*V(stim))*V(a)'     ngspice's own behavioural charge form
  bddt     B1 a 0 I=ddt((C0+CS*V(stim))*V(a))  the validator's B-DDT case
  bcap     Eq/Cu/Vz/Fq: q = C(stim)*v as a voltage, i = dq/dt through a 1 F capacitor (ngspice's own
           capacitor integration, LTE-controlled, no ddt())
Run: python3 cl_probe.py   (needs ngspice on PATH, numpy, scipy)
"""
import os
import subprocess
import sys
import tempfile

import numpy as np
from scipy.integrate import solve_ivp

C0 = CS = 1e-9
R, V, T0, TEND = 1e3, 1.0, 5e-6, 15e-6

FORMS = {
    "native": "C1 a 0 C='{c0}+{cs}*V(stim)'",
    "nativeq": "C1 a 0 q='({c0}+{cs}*V(stim))*V(a)'",
    "bddt": "B1 a 0 I=ddt(({c0}+{cs}*V(stim))*V(a))",
    "bcap": ("Eq qn 0 VALUE={{({c0}+{cs}*V(stim))*V(a)}}\nCu qn x 1\nVz x 0 0\nFq a 0 Vz 1\n"
             "Rq qn 0 1G"),
}


def deck(form, tr, tstep, opts=""):
    return f"""probe {form}
V1 in 0 {V}
R1 in a {R}
VS stim 0 PWL(0 0 {T0} 0 {T0 + tr} 1)
{FORMS[form].format(c0=C0, cs=CS)}
{opts}
.tran {tstep} {TEND}
.control
set noaskquit
run
wrdata OUT.txt v(a)
.endc
.end
"""


def run(form, tr, tstep, opts=""):
    with tempfile.TemporaryDirectory() as d:
        txt = deck(form, tr, tstep, opts).replace("OUT.txt", os.path.join(d, "o.txt"))
        p = os.path.join(d, "d.cir")
        with open(p, "w") as f:
            f.write(txt)
        r = subprocess.run(["ngspice", "-b", p], capture_output=True, text=True)
        try:
            a = np.loadtxt(os.path.join(d, "o.txt"))
        except Exception:
            return None, r.stdout[-400:] + r.stderr[-400:]
    return a[:, [0, 1]], ""


def reference(tr, charge_form=True):
    def c(t):
        return C0 + CS * np.clip((t - T0) / tr, 0, 1)

    if charge_form:                          # state q
        def f(t, y):
            return [(V - y[0] / c(t)) / R]
        y0 = [C0 * V]
    else:                                    # state v
        def f(t, y):
            return [(V - y[0]) / R / c(t)]
        y0 = [V]
    s = solve_ivp(f, (0, TEND), y0, method="LSODA", rtol=1e-12, atol=1e-15, max_step=tr / 20,
                  dense_output=True)
    return (lambda t: s.sol(t)[0] / c(t)) if charge_form else (lambda t: s.sol(t)[0])


def main():
    print(f"{'form':8} {'tr':>6} {'tstep':>6} {'maxerr(q-form)':>15} {'maxerr(v-form)':>15} "
          f"{'v(t0+3us)':>10} {'ref q/v':>14}")
    for tr in (1e-8, 1e-9, 1e-6):
        refq, refv = reference(tr, True), reference(tr, False)
        for form in FORMS:
            for tstep in (1e-8, 1e-9):
                a, err = run(form, tr, tstep)
                if a is None:
                    print(f"{form:8} {tr:6.0e} {tstep:6.0e}   FAILED {err!r}")
                    continue
                t, v = a[:, 0], a[:, 1]
                m = t > 0
                eq = np.max(np.abs(v[m] - np.array([refq(x) for x in t[m]])))
                ev = np.max(np.abs(v[m] - np.array([refv(x) for x in t[m]])))
                v3 = np.interp(T0 + 3e-6, t, v)
                print(f"{form:8} {tr:6.0e} {tstep:6.0e} {eq:15.3e} {ev:15.3e} {v3:10.5f} "
                      f"{float(refq(T0 + 3e-6)):.5f}/{float(refv(T0 + 3e-6)):.5f}")


if __name__ == "__main__":
    sys.exit(main())
