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
        self._stops = []
        self._observables = []
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
    def configure(self, tstep=None, tstop=None, tstart=None, tmax=None, uic=None):
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
        return (
            [self._title] + self._devices + self._models + [tran, ".end"]
        )

    def validate(self):
        """Static findings without running (list of dicts)."""
        return _k.validate(self._deck())

    def run(self):
        self._state = "running"
        deck = self._deck()
        try:
            res = _k.run_simulation(deck, list(self._observables), list(self._stops))
        except BaseException:
            self._last = None
            self._state = "failed"
            raise
        self._last = res
        self._state = "completed"   # res["used_pauses"] says whether native pauses were hit on the way
        return res

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
        return list(self._last["time"]) if self._last else []

    def observable(self, name):
        if not self._last:
            raise InvalidConfigError("no run yet")
        return list(self._last["series"].get(name, []))

    @property
    def used_pauses(self):
        return bool(self._last and self._last["used_pauses"])

    @property
    def fallback_reason(self):
        return self._last["fallback_reason"] if self._last else None
