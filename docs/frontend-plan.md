# Frontend plan (schematic editor + transient plots over simcore)

## Core facts (inspected 2026-10-05)

- Core: `simcore/` — Rust crate (kernel + libngspice FFI) exposed to Python via
  PyO3/maturin. Public API: `simcore.Simulation` (`simcore/python/simcore/__init__.py`).
  All numerics live in libngspice (`/usr/lib/libngspice.so`, ngspice 47).
- Build/install: `cd simcore && maturin build && pip install --user --force-reinstall target/wheels/simcore-*.whl`.
- Tests: `cargo test` (26 real-engine), `python -m pytest simcore/tests/python` (3).
- Verified: V1 5 V → R 1k → C 1u → 0, `v(out)` vs `5(1-exp(-t/1ms))` over 5τ:
  max error 14.7 µV (511 pts, ~1.4 ms wall). Pause path also exact.

### Fixes made in core (genuine blockers)

1. Packaging: wheel shipped only `_simcore`; `import simcore` (the documented API)
   failed. Now `module-name = "simcore._simcore"`, `python-source = "python"`.
2. Unknown/unsaved observables silently returned empty (or 1-point) series, so a
   stale probe would plot nothing with no error. Now `InvalidConfigError` naming
   the observable (Rust T24 + Python test).

### Known core quirks the UI must respect (not blockers)

- Malformed values / bad tstop/tstep surface as `SolverError("... circuit not
  parsed")`: UI validates numeric fields itself (positive floats) before run.
- SPICE suffix trap: `1M` = milli. UI emits plain floats (`repr(float)`), never suffixes.
- `Simulation.state` stays `"running"` after an exception: UI does not use it.
- One run at a time per process (core serializes). Runs are ms-scale.

## Core API surface the UI uses (only these)

```python
from simcore import Simulation, InvalidCircuitError, InvalidConfigError, \
    ConvergenceError, SolverError, BackendError, UnsupportedError, UnavailableError
s = Simulation(title)
s.build_circuit(lines)            # device lines from the schematic
s.add(".save all @r1[i]")         # per R/C/L, so component currents are observable
s.add_model(".model dmod D")      # only if a diode is placed
s.configure(tstep=..., tstop=..., tmax=...)   # uic defaults True
s.observe(*names)                 # "v(n3)", "i(v1)", "@r1[i]"
findings = s.validate()           # [{code, severity, message, nodes, devices}]
res = s.run()                     # {series:{name:[(t,v)]}, time, npts, wall_ms, ...}
s.observable(name)                # [(t, v)]
```

Errors: `InvalidCircuitError` message starts with `[CODE]` (e.g. `[NO-DC-PATH]`);
UI shows it in the status bar and highlights `findings[*].devices/nodes`.

## Components offered

| UI part | Netlist line | Params |
|---|---|---|
| Resistor | `R<n> a b <R>` | R (Ω) |
| Capacitor | `C<n> a b <C> IC=<v0>` | C (F), initial V |
| Voltage source | `V<n> + - DC <V>` / `PULSE(v1 v2 td tr tf pw per)` / `SIN(vo va f)` | mode + values |
| Ground | net named `0` | — |
| Extras (cheap, core-supported) | Inductor `L<n> a b <L> IC=<i0>`, Current source `I<n> + - DC <I>`, Diode `D<n> a k dmod` + `.model dmod D` | |

## UI action → core mapping

- Place: UI-only (part, position, rotation, auto name R1/C1/...). No core call.
- Connect: UI-only wires between pins. On run, union-find over pins → nets;
  any net touching a Ground pin is `0`, others `n1, n2, ...` (stable by creation).
- Configure: edit params in a side panel; stored in the UI model; validated as floats.
- Validate (on every edit, debounced, and before run): `s.validate()` →
  warnings/errors shown; no simulation.
- Run: build lines from the model → `build_circuit` + `.save` lines →
  `configure` from the toolbar (tstop, tstep; tmax = tstep) →
  `observe(v(<net>) for each net ≠ 0, i(<vsrc>), @<dev>[i])` → `run()`.
- Inspect: click a wire → plot `v(<net>)`; click a part → plot its current
  (and voltage across it as `v(a)-v(b)` computed from two core series, pure subtraction).
  Plot data always comes from the last `run()` result; nothing is re-simulated in the UI.

## Stack and structure

PySide6 6.11 + matplotlib 3.11 (QtAgg) — both already installed for Python 3.14,
which is the same interpreter the core wheel targets. No new dependencies.
(Tk is available but weaker for a canvas editor; PyQt5 is legacy.)

```
Sim/app/
  __main__.py      # python -m app
  model.py         # parts, wires, params, net extraction, netlist lines (pure, no Qt)
  sim.py           # the only file importing simcore: model -> Simulation -> result
  window.py        # QMainWindow: palette, QGraphicsScene canvas, param panel, toolbar
  plot.py          # FigureCanvasQTAgg widget, plots [(t,v)] series
tests/
  test_model.py    # netlist generation / net naming
  test_e2e.py      # drives the real core
```

## Plots

`sim.py` returns the `res["series"]` dict; `plot.py` draws `t, v = zip(*series[name])`
with units (s, V, A). Multiple probes overlay; rerun replaces data.

## End-to-end tests (real core, no mocks)

- `QT_QPA_PLATFORM=offscreen python -m pytest tests/` (verified offscreen Qt works).
- E2E: construct the main window, place V/R/C/Ground and wires through the same
  model/controller methods the UI handlers call, set Vs=5, R=1k, C=1u, tstop=5 ms,
  trigger Run, then assert the plotted series for the capacitor net matches
  `Vs(1-exp(-t/RC))` within 20 mV (core tolerance, `simcore/docs/testing.md`).
- Error path: delete the ground → Run → status shows `[NO-GROUND]`/`[NO-DC-PATH]`, no plot.
- Probe a deleted net → `InvalidConfigError` shown, not an empty plot.
