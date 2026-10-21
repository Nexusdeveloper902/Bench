"""G28 probe (W3): ngspice-47 behavioural inductor, against flux conservation on a stimulus step.

Circuit: V=1 V -- R=1k -- node a -- L(stim) -- 0.  stim steps 0 -> 1 at t0 = 5 us over `tr`, L = L0 + LS*stim
(L0 = LS = 1 mH).  Reference (flux form): d(phi)/dt = V - R*phi/L(t), i = phi/L; phi is continuous across the step
and i jumps to L0/L1 * V/R.  The current form v = L(t) di/dt keeps i continuous instead.  Forms probed:
  native   L1 a 0 L='L0+LS*V(stim)'
  nativeflux  L1 a 0 flux='(L0+LS*V(stim))*i(L1)'
  bddt     B1 a 0 V=ddt((L0+LS*V(stim))*I(Vz))   (L in series with a 0 V sense source)
  bcap     Ephi = L(stim)*I(Vz) as a voltage; v = dphi/dt through a 1 F capacitor read by Hv (ngspice's own
           capacitor integration, no ddt())
Run: python3 ll_probe.py   (needs ngspice on PATH, numpy, scipy)
"""
import os
import subprocess
import sys
import tempfile

import numpy as np
from scipy.integrate import solve_ivp

L0 = LS = 1e-3
R, V, T0, TEND = 1e3, 1.0, 5e-6, 15e-6

FORMS = {
    "native": "L1 a 0 L='{l0}+{ls}*V(stim)'",
    "nativeflux": "L1 a 0 flux='({l0}+{ls}*V(stim))*i(L1)'",
    "bddt": "Vz a x 0\nB1 x 0 V=ddt(({l0}+{ls}*V(stim))*I(Vz))",
    "bcap": ("Vz a x 0\nEphi ph 0 VALUE={{({l0}+{ls}*V(stim))*I(Vz)}}\nCu ph y 1\nVzs y 0 0\nHv x 0 Vzs 1"),
}


def deck(form, tr, tstep):
    return f"""probe {form}
V1 in 0 {V}
R1 in a {R}
VS stim 0 PWL(0 0 {T0} 0 {T0 + tr} 1)
{FORMS[form].format(l0=L0, ls=LS)}
.tran {tstep} {TEND}
.control
set noaskquit
run
wrdata OUT.txt v(a)
.endc
.end
"""


def run(form, tr, tstep):
    with tempfile.TemporaryDirectory() as d:
        txt = deck(form, tr, tstep).replace("OUT.txt", os.path.join(d, "o.txt"))
        p = os.path.join(d, "d.cir")
        with open(p, "w") as f:
            f.write(txt)
        r = subprocess.run(["ngspice", "-b", p], capture_output=True, text=True)
        try:
            a = np.loadtxt(os.path.join(d, "o.txt"))
        except Exception:
            return None, r.stdout[-400:] + r.stderr[-400:]
    return a[:, [0, 1]], ""


def reference(tr, flux_form=True):
    """Returns v_a(t) = V - R*i(t)."""
    def lt(t):
        return L0 + LS * np.clip((t - T0) / tr, 0, 1)

    if flux_form:
        def f(t, y):
            return [V - R * y[0] / lt(t)]
        y0 = [L0 * V / R]
    else:
        def f(t, y):
            return [(V - R * y[0]) / lt(t)]
        y0 = [V / R]
    s = solve_ivp(f, (0, TEND), y0, method="LSODA", rtol=1e-12, atol=1e-18, max_step=tr / 20,
                  dense_output=True)
    if flux_form:
        return lambda t: V - R * s.sol(t)[0] / lt(t)
    return lambda t: V - R * s.sol(t)[0]


def main():
    print(f"{'form':8} {'tr':>6} {'tstep':>6} {'maxerr(phi-form)':>17} {'maxerr(i-form)':>15} "
          f"{'v(t0+3us)':>10} {'ref phi/i':>14}")
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
                print(f"{form:8} {tr:6.0e} {tstep:6.0e} {eq:17.3e} {ev:15.3e} {v3:10.5f} "
                      f"{float(refq(T0 + 3e-6)):.5f}/{float(refv(T0 + 3e-6)):.5f}")


if __name__ == "__main__":
    sys.exit(main())
