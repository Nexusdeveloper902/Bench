"""Public Simulation API: simulation concepts, never ngspice concepts.

Frontend-facing surface::

    sim = Simulation()
    sim.build_circuit([...components...])
    sim.configure(tstop=5e-3, tmax=10e-6)
    sim.pause_at(2.5e-3)
    result = sim.run()
    v = sim.observable("v(out)")

Implemented over the Rust kernel (_benchcore). All ngspice details
(stop/run/resume commands, plots, TMAX slots) stay below this boundary.
"""

from . import _benchcore as _k

from ._benchcore import (
    InvalidCircuitError,
    InvalidConfigError,
    ConvergenceError,
    SolverError,
    BackendError,
    UnsupportedError,
    UnavailableError,
)

__all__ = [
    "Simulation",
    "LiveRun",
    "replay",
    "run_ac",
    "validate",
    "SimError",
    "InvalidCircuitError",
    "InvalidConfigError",
    "ConvergenceError",
    "SolverError",
    "BackendError",
    "UnsupportedError",
    "UnavailableError",
]

SimError = Exception

# validate(deck) -> [finding dict]: the static checks every run applies, on a whole deck (title line first), with no
# simulation session (the catalog validates package definition text with it).
validate = _k.validate

# LiveRun(deck, observables, devices=[], chunk=None, realtime=None): a transient that runs while values change;
# replay(deck, observables, [(t, device, value)]) -> dict like run(). Contract: docs/plans/ngspice-library/LIVE.md.
LiveRun = _k.LiveRun
replay = _k.replay

# run_ac(deck, observables) -> {"frequency": [Hz], "series": {name: [(re, im)]}, "npts", "wall_ms"}: an AC small-signal
# run of a deck with one `.ac dec|oct|lin points fstart fstop` card (Simulation.ac builds it). Observables: v(node),
# i(vsrc); device @ vectors are refused (in an ac plot they hold operating-point values, not small-signal ones).
run_ac = _k.run_ac


class Simulation:
    """One sequential simulation. Not thread-safe: one handle, one run at a
    time (matches the backend's sequential-handle discipline)."""

    def __init__(self, title="sim"):
        self._title = title
        self._devices = []
        self._models = []
        self._tstep = 10e-6
        self._tstop = 1e-3
        self._tstart = 0.0
        self._tmax = None
        self._uic = True
        self._method = "trap"
        self._stops = []
        self._observables = []
        self._ac = None             # (sweep, points, fstart, fstop): run an AC analysis instead of the transient
        self._last = None
        self._state = "idle"

    # -- circuit building -------------------------------------------------
    def add(self, line):
        """Add one netlist device/model line (validated on run)."""
        self._devices.append(line)
        return self

    def build_circuit(self, lines):
        self._devices = list(lines)
        return self

    def add_model(self, line):
        self._models.append(line)
        return self

    def modify(self, old, new):
        """Replace one device line (between runs only)."""
        self._devices = [new if l == old else l for l in self._devices]
        return self

    # -- configuration ----------------------------------------------------
    def configure(self, tstep=None, tstop=None, tstart=None, tmax=None, uic=None, method=None):
        """method: integration method, "trap" (ngspice's default) or "gear" (damps trapezoidal ringing: the
        alternating-sample artefact sharp edges excite on high-Q nodes; it also damps real high-Q ringing slightly)."""
        if tstep is not None:
            self._tstep = float(tstep)
        if tstop is not None:
            self._tstop = float(tstop)
        if tstart is not None:
            self._tstart = float(tstart)
        if tmax is not None:
            self._tmax = float(tmax)
        if uic is not None:
            self._uic = bool(uic)
        if method is not None:
            if method not in ("trap", "gear"):
                raise InvalidConfigError(f"integration method must be 'trap' or 'gear', not {method!r}")
            self._method = method
        return self

    def ac(self, sweep="dec", points=20, fstart=1.0, fstop=1e6):
        """Make this an AC small-signal run: `sweep` dec | oct | lin, `points` per decade / per octave / in total,
        from fstart to fstop (Hz). The engine checks the values (InvalidConfigError). ac(None) goes back to transient."""
        self._ac = None if sweep is None else (str(sweep), int(points), float(fstart), float(fstop))
        return self

    def observe(self, *names):
        self._observables = list(names)
        return self

    # -- events / pause schedule ------------------------------------------
    def pause_at(self, t):
        """Request a native pause at simulation time t (declared pre-run)."""
        self._stops.append(float(t))
        self._stops.sort()
        return self

    def clear_stops(self):
        self._stops = []
        return self

    # -- lifecycle ---------------------------------------------------------
    def _deck(self):
        tran = ".tran {} {} {}".format(self._tstep, self._tstop, self._tstart)
        tran += " {}".format(self._tmax) if self._tmax is not None else " "
        if self._uic:
            tran += " uic"
        opts = [".options method=gear"] if self._method == "gear" else []
        if self._ac:
            tran = ".ac {} {} {!r} {!r}".format(*self._ac)
        return (
            [self._title] + self._devices + self._models + opts + [tran, ".end"]
        )

    def validate(self):
        """Static findings without running (list of dicts)."""
        return _k.validate(self._deck())

    def run(self):
        self._state = "running"
        deck = self._deck()
        try:
            if self._ac:
                res = _k.run_ac(deck, list(self._observables))
            else:
                res = _k.run_simulation(deck, list(self._observables), list(self._stops))
        except BaseException:
            self._last = None
            self._state = "failed"
            raise
        self._last = res
        self._state = "completed"   # res["used_pauses"] says whether native pauses were hit on the way
        return res

    def live(self, devices=(), chunk=None, realtime=None, target=None):
        """Start a live run of this deck -> LiveRun (iterate it on a worker thread). devices: ngspice names
        (e.g. "v.xrl1.vstim") that set() may change during the run. target: wall seconds per chunk (chunks adapt,
        `chunk` is the largest; None = fixed chunks). Refused while another run holds the engine."""
        return _k.LiveRun(self._deck(), list(self._observables), list(devices), chunk, realtime, target)

    def replay(self, changes):
        """Replay a live run's change log [(t, device, value)] as one blocking run of this deck -> dict like run()."""
        self._state = "running"
        try:
            self._last = _k.replay(self._deck(), list(self._observables), [tuple(c) for c in changes])
        except BaseException:
            self._last, self._state = None, "failed"
            raise
        self._state = "completed"
        return self._last

    def reset(self):
        self._last = None
        self._stops = []
        self._state = "idle"
        return self

    def destroy(self):
        self.reset()
        self._devices = []
        self._models = []
        return self

    # -- results -----------------------------------------------------------
    @property
    def state(self):
        return self._state

    @property
    def time(self):
        return list(self._last.get("time", [])) if self._last else []

    @property
    def frequency(self):
        """The frequency grid (Hz) of the last AC run; observable() then gives (re, im) per frequency."""
        return list(self._last.get("frequency", [])) if self._last else []

    def observable(self, name):
        if not self._last:
            raise InvalidConfigError("no run yet")
        return list(self._last["series"].get(name, []))

    @property
    def used_pauses(self):
        return bool(self._last and self._last.get("used_pauses"))

    @property
    def fallback_reason(self):
        return self._last.get("fallback_reason") if self._last else None
