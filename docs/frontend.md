# Frontend (native PySide6 app over simcore)

## Layout

- `app/components.py` - component catalog (metadata per kind), SI unit parse/format, netlist builder (all SPICE text, `.model`/`.save` lines and ngspice vector names). Pure Python. Contract: `docs/frontend-components.md`.
- `app/model.py` - `Document`: parts, wires, params (all stored as strings), rotation, net extraction, pre-run `validate()`, save format v1. Pure Python.
- `app/sim.py` - the only module importing `simcore`. `run_document(doc) -> RunResult`; never raises.
- `app/history.py` - undo/redo: snapshots of `Document.to_dict()` (minus the `simulation` section); `with hist.change(doc):` records only if the doc changed; restores in place. Pure Python.
- `app/window.py` - `MainWindow` (QGraphicsScene canvas, catalog-driven inspector, problems panel, simulation controls, matplotlib waveform viewer). Handlers only call model/sim. Structure, controls/state and the viewer data contract: `docs/frontend-architecture.md`.
- `app/tests/test_e2e.py` - end-to-end tests on the real core.

## Integration decisions

- **Adapter boundary.** `sim.py` is the single seam: `Document -> netlist dict -> simcore.Simulation -> RunResult`. Nothing else knows about the core.
- **No second sim model.** The UI never computes circuit behaviour. Plots and stats are the core's series; nothing is re-simulated or derived.
- **Netlist mapping.** Union-find over wired pins gives nets; any net with a ground pin is `0`, others are `n1, n2, ...` in part-creation order. Each part maps to SPICE text through its catalog entry (`components.build_netlist`); see `docs/frontend-components.md`. Observables: `v(<net>)`, `i(<vsrc>)`, `@<dev>[i]`, plus readable aliases (`V(C1)`, `I(R1)`) in `RunResult.signals`.
- **SI input, plain floats out.** Users type `1k`, `10u`, `4.7 nF`, `2.2M` (M = mega, as in SI; `meg` also works). `components.parse_si` converts to float and the core always receives `repr(float)`, so SPICE's `M` = milli never applies.
- **UI-side validation.** `Document.validate()` returns structured diagnostics (bad run settings, empty circuit, NO-GROUND, unconnected part/terminal, bad parameter) before the core is called. The core only reports malformed values as a vague "circuit not parsed", so this check is required.
- **Error surfacing.** `run_document` converts app diagnostics and every `simcore.SimError` into `RunResult(ok=False, status="failed", message=...)`, keeping `diagnostics`, core `findings` and developer `details` (error type, full message, netlist sent). Core `InvalidCircuitError` messages keep their `[NO-GROUND]` / `[NO-DC-PATH]` codes. The window shows `FAILED: <message>` in red and clears the stats. Observables come from the netlist, so the UI cannot request a nonexistent one. The core raising for an unknown observable (core test T24) is the backstop.

## LED, battery, derived voltages

- `LED` (pins `a` anode, `k` cathode, no params): `D<n> a k dled` plus `.model dled D(Is=1e-20 N=1.8 Rs=5)` (red, Vf ~1.97 V at 9 mA), added once, only when placed. Saves `@d<n>[id]`. Its instance name uses the `D` prefix (SPICE letter picks the device type), so it shares `D1, D2..` numbering with the plain diode.
- `BAT` (pins `p`, `n`, param `dc`): `V<n> p n DC <dc>`, shares the `V` prefix; `i(v<n>)` is observable. `V` (DC/PULSE/SIN) is unchanged.
- Derived observable `v(<Part>)` = v(first pin) - v(second pin) for every two-pin part (`v(R1)`, `v(D1)`, `v(V1)`). `Document.netlist()["derived"]` maps it to the two nets; `sim._diff` subtracts the core's node series (net `0` = 0). Pure subtraction of core output, nothing is simulated. They appear in `RunResult.observables`/`stats`.
- A cathode left unconnected still solves (core reports an open-end finding); a fully unconnected LED fails in the core.

## LED glow, playback, blink example

- After a run the LED symbol is drawn with a fill and radial halo: grey/dark when off, red-orange plus halo when on, and a `x.x mA` label (also on plain diodes, no glow). Brightness = `model.led_level(i)` of the core's `@d<n>[id]` series: 0 at <= 0.1 mA, linear to 1 at >= 15 mA (a display mapping, nothing simulated). Reversed/open LEDs are dark; a stale result (any edit) clears glow, cursor and readout. `canvas.led_glow(name)` returns the level in [0,1].
- Playback: under the plot, a Play/Pause button and a time slider move a cursor `MainWindow.cur_t` (default: end of run). The canvas glow and current labels, a red vertical cursor line on the plot, and a readout (`t = ... ms  <observable> = ...`) follow it. Values come from `model.value_at` (linear interpolation of `RunResult.series`). Playback is wall-clock driven: a 40 ms timer calls `tick(dt)` with the real elapsed time, one pass over the simulated window = `LOOP_SECONDS` (4 s, so the blink example blinks ~1 Hz) and wraps; slow frames skip ahead instead of stretching the blink. Each frame repaints the canvas immediately and moves the plot cursor by blitting (an animated line over a cached background), never a full matplotlib redraw (~40 ms, it starved the timer). `MainWindow.autoplay` (set by `app/main.py`, default False so tests stay deterministic) starts looping after a successful Run when a non-DC V source exists; Play/Pause still toggles. `app/tests/test_led_pixels.py` grabs the canvas and checks painted LED pixels, not accessors. A new run/edit stops playback.
- `File > Load blinking LED example` (also on the toolbar): PULSE source 0 -> 5 V, 2 ms period, 50% duty, 10 us edges, R 330, LED. tstop 8.5 ms, tstep 20 us. `Document.config()` sets tmax = tstep (20 us <= period/50) so edges are resolved. Press Run (F5), it plays by itself (Pause stops it): the LED turns on and off with the pulses. Run ends in the ON phase.

## Known quirks and limits

- Series start at the first solver step (about 1e-7 s), not at t=0. Interpolating at t=0 fails.
- tstop/tstep edits are not undoable (skipped: they are committed per keystroke, undoing would fight typing).
- tstop/tstep are committed to the document on every keystroke (and at Run), so selecting a part never loses them. They are not part of undo history.
- Param edits are checked as you type (`Document.check_param`: red field + message) and stored as typed on Enter/focus-out, even if invalid, so UI and document never diverge; `Document.validate()` lists them in the Problems panel and blocks Run.
- A circuit edit (param, connect/disconnect, delete, undo/redo; not moves, not tstop/tstep) marks the result stale: `result` cleared, plot/stats/findings cleared, status `results out of date - press Run`. `MainWindow._key()` is the signature compared against the last run.
- Pins on nets named by a core finding (`nodes`, e.g. DANGLING) get an amber ring on the canvas; the text stays in the findings label.
- Floating nets fail in the core (reported as failed with the core's message and details), not with a UI highlight. Unconnected parts/pins are app diagnostics (Problems panel).
- One run at a time per process; the run is synchronous on the GUI thread (ms-scale for the supported circuits).
- Only DC/PULSE/SIN voltage sources, DC current source, diode (per-part `.model` with Is/N/Rs), fixed red LED model, and a static Switch (a resistor: Ron when closed, Roff when open; no time-controlled switching).
- Tests that use `QApplication` must run with `QT_QPA_PLATFORM=offscreen`; `app/tests/conftest.py` sets it.

## Build the LED circuit in the GUI

1. Click `Battery`, click the canvas; then `Resistor`, `LED`, `Ground` the same way (each click places one part, snapped to the grid).
2. Wire by clicking a pin then another pin: `V1.p -> R1.a`, `R1.b -> D1.a`, `D1.k -> GND1.g`, `V1.n -> GND1.g`. Red hollow pins are unconnected, green are wired.
3. Click V1, set `Voltage (V)` to 5; click R1, set `Resistance (Ω)` to 330 (SI suffixes work: `330`, `1k`, `4.7k`; press Enter or click away to commit).
4. Press Run (F5). Expect `SUCCESS`, LED current ~9 mA, V(D1) ~2 V; pick signals (`V(n1)`, `V(D1)`, `I(D1)`, ...) in the dropdown or click a wire. Reversed LED: success with ~0 current. No battery: `FAILED` with the core message.
5. Shortcuts: F5 run, Ctrl+F5 re-run, R rotate, Ctrl+Z undo, Ctrl+Shift+Z / Ctrl+Y redo, Delete/Backspace remove selection, Esc cancel tool/pending wire, right-click pin disconnects, Ctrl+N/O/S, Ctrl+Shift+S save as. `File > Load LED example` builds the same circuit.

`app/tests/test_led_e2e.py` replays exactly this with real mouse/key events and checks the core numbers (KVL, Ohm, undo/redo, stale state).

## Run

```
python3 -m app            # GUI
python3 -m app --smoke    # headless demo: build RC, run, print status
```

Requires the simcore wheel installed:
`cd simcore && maturin build && pip install --user --force-reinstall target/wheels/simcore-*.whl`.

## Test

```
QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests   # real core, offscreen Qt (GUI workflows: test_gui_workflow.py)
cd simcore && cargo test && python3 -m pytest -q tests/python
```

## Next phase

Not done yet:
- Wires are routed with one right-angle corner (no obstacle avoidance); New/Open/example loads clear undo history.
- Plots do not overlay multiple probes.
- Finding highlight is pin rings only (no per-part/wire highlight, no click-to-focus from the findings text).
- Run on a worker thread with cancel/pause (needs core support; runs are synchronous today).
