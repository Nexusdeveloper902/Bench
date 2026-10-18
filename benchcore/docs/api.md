# Simulation API

## Concepts

`Simulation` is a handle to one sequential simulation, not a circuit object.
Build the deck (`build_circuit`/`add`/`add_model`), declare what to watch
(`observe`), declare when to synchronize (`pause_at`), set the window
(`configure`), then `run`. Read `observable(name)`, `time`, `state`,
`used_pauses`, `fallback_reason`. `modify` edits the deck between runs;
`reset` clears results + stops; `destroy` clears everything.

`configure(method="gear")` adds `.options method=gear` (default `"trap"` adds nothing). `run()`'s dict has
`warnings`: plausibility warnings on a run that succeeded, today a subcircuit-internal node that ran far beyond every
circuit node (GAPS G15). An `@dev[param]` observable needs a `.save` naming it, or the run is refused (G32).

## Example

```python
from benchcore import Simulation  # pip install target/wheels/benchcore-*.whl
s = Simulation("RC")
s.build_circuit(["V1 in 0 DC 5", "R1 in out 1k", "C1 out 0 1u IC=0"])
s.configure(tstep=10e-6, tstop=5e-3, tmax=10e-6)
s.observe("v(out)").pause_at(2.5e-3)
r = s.run()  # r: series/time/npts/wall_ms/used_pauses/fallback_reason
```

## Contracts

- `pause_at(t)` declares intent pre-run; pauses are exact breakpoints, not
  async interrupts. `run` with no stops is an ordinary full simulation.
- `used_pauses=True` only if every stop paused exactly and the run completed
  at tstop. Otherwise `fallback_reason` names the cause and the returned
  series is the ordinary full simulation (never a reconstruction).
- Failures raise typed errors: `InvalidCircuitError` (with `[CODE]` prefix),
  `InvalidConfigError`, `ConvergenceError`, `SolverError`, `BackendError`,
  `UnsupportedError`, `UnavailableError`. Convergence/solver problems are
  never returned as plausible series.
- `validate()` returns static findings without simulating.
- An observable the engine cannot supply over the full time grid (unknown
  node/branch, or a device quantity like `@r1[i]` without `.save all @r1[i]`)
  raises `InvalidConfigError` naming it; series are never empty/truncated.
- Time unit is seconds; observables use ngspice vector names (`v(out)`,
  `i(v1)`). The API never requires knowledge of `stop`/`resume`/plots/TMAX:
  `bsource` TMAX tightening happens automatically; unknown-TIME B-sources
  without an explicit `tmax` raise `InvalidConfigError`.
- Threading: one `Simulation` = one sequential user. Parallel sims must use
  separate processes (shared-handle threads crash libngspice; the core
  serializes but does not parallelize).

## Live runs

```python
run = s.live(["v.xrl1.vstim"], chunk=1e-4, realtime=1.0)   # benchcore.LiveRun
for chunk in run:            # on a worker thread; GIL released while a chunk computes
    ...                      # chunk: time, series (new points), changes, t, done, wall, rtf, seq
run.set({"v.xrl1.vstim": 100.0})   # any thread: applied at the next chunk boundary
run.pause(); run.resume(); run.cancel(); run.close()
s.replay(run.changes)        # the change log as one blocking run, like run()
```

A live run owns the engine until it ends: `run`/`replay`/another `live` raise
`BackendError` meanwhile. Only declared R (resistance) and V/I DC values change.
Contract, mechanism and limits: `docs/plans/ngspice-library/LIVE.md`.
