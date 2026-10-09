# Frontend architecture (native GUI)

Companion docs: `docs/frontend.md` (overview, quirks, how to run), `docs/frontend-components.md` (catalog, SI
units, save format, `RunResult`).

## Layers and the one seam

```
app/window.py   Qt only: canvas, inspector, problems, controls, viewer      (imports model, components, sim, history)
app/history.py  undo/redo snapshots                                          (pure Python)
app/model.py    Document: parts, wires, nets, validate(), save/load          (pure Python)
app/components.py  CATALOG metadata, SI units, netlist builder              (pure Python)
app/sim.py      run_document(doc) -> RunResult        <- ONLY importer of simcore
simcore         Simulation API -> Rust kernel -> libngspice
```

Rules the GUI keeps:

- Nothing in `app/window.py` imports `simcore` or builds SPICE text. It calls `run_document(doc)` and reads the
  returned `RunResult`. No solving in the GUI: displayed numbers are core series, linear interpolation of them
  (`model.value_at`, cursor readout) or their min/max/final (`RunResult.stats`).
- No per-component branching. Geometry comes from `Component.pos`/`box` via `components.terminal_offset`; the
  inspector from `Component.params` (`Param.label/unit/kind/choices`); canvas value labels from
  `components.summary`. The only per-symbol code is `window.SYMBOLS`: a dict from the catalog's `symbol` key to a
  painter. Adding a component with an existing symbol needs no GUI change; a new symbol needs one `SYMBOLS` entry.
  Display-only sets keyed by symbol: `SHOW_CURRENT` (diode, LED print their current) and `GLOWS` (LED).

## Window structure (`app/window.py`)

| Area | What | Backed by |
|---|---|---|
| File toolbar / menu | New, Open, Save, Save As, examples, Undo, Redo | `Document.save/load`, `History` |
| Simulation toolbar / menu | `Analysis: Transient`, Stop time, Time step, Run (F5), Re-run (Ctrl+F5), Reset, state | `Document.tstop/tstep`, `run_document` |
| Parts toolbar | one button per `CATALOG` entry (catalog order), Rotate, Delete (also in the Edit menu; not duplicated in the inspector) | `Document.add_part/rotate/remove_*` |
| Canvas (`Canvas`) | place, select, drag, rotate, wire (click-click or drag), right-click pin disconnects, click wire selects + plots its node | `Document` |
| Inspector | rows from catalog `Param` metadata; number -> SI text field, choice -> combo box | `Document.check_param/set_param` |
| Problems | live `Document.validate()` list (errors first); click selects the part | `Document.validate` |
| Waveform viewer | signal picker, plot, Play + time slider, cursor readout, final/min/max, failure message, details | `RunResult` only |

Drawing: part transform = translate to anchor, rotate (clockwise, 0/90/180/270) about the body centre, same
convention as `components.rotate`. Wires are drawn with one right-angle corner; of the two possible corners the one
crossing fewer part bodies wins, ties leave the first pin along its own axis. A pin with 2+ wires gets a junction
dot. Pins: green filled = wired, red hollow = unconnected, blue = pending wire start, enlarged on hover. Selected
part: orange ink and a dashed orange box; selected wire: thick orange.

## GUI -> API integration and the simulation workflow

1. Edits go through `Document` methods inside `with hist.change(doc)` (place, delete, move, rotate, connect,
   disconnect, param edit), so each is one undo step. Stop time / time step are committed to the document on every
   keystroke and are not undo history (documented in `docs/frontend.md`).
2. Inspector number fields validate as you type with `Document.check_param` (red border + message, or the
   interpreted value such as `Capacitance = 10 µF`). On Enter/focus-out the text is stored as typed, valid or not,
   so the document and the UI never disagree; invalid values show as BAD-PARAM in Problems and block Run.
3. Run: `cfg_changed()` -> state `running` -> `run_document(doc)` -> `RunResult` -> state `completed`/`failed`.
   `run_document` never raises by contract; if it ever does (an app bug) the window builds a failed `RunResult`
   with the traceback in `details` instead of hiding it.
4. Any circuit edit after a run (not moves/rotation, not tstop/tstep) makes the result stale: it is dropped, the
   viewer cleared, state back to `idle`, status `results out of date - press Run`.

## Controls and state

| State | When | Shown |
|---|---|---|
| idle | no result: start, after Reset, after an edit made the result stale | grey `● idle`, plot says "Press Run (F5)" |
| running | during `run_document` (label repainted before the call) | blue `● running` |
| completed | `RunResult.ok` | green `● completed`, `SUCCESS: ok, N points` |
| failed | `RunResult.ok == False` | red `● failed`, `FAILED: <message>`, red message under the plot, details |

- Re-run = Reset + Run. Reset drops the result but keeps the circuit, settings and the picked signal.
- **No pause/stop.** The core runs synchronously in milliseconds on the GUI thread and has no cancel; its
  `pause_at` is a pre-declared breakpoint list, not an interactive pause. The controls are omitted rather than faked.
  The Play/Pause button under the plot pauses *playback* of a finished result, not the solver.
- Analysis type is shown as `Transient` (the only analysis the core implements).

## Errors

- Before running: the Problems panel shows `Document.validate()` live (NO-GROUND, EMPTY, UNCONNECTED-PART,
  BAD-PARAM, BAD-CONFIG errors; UNCONNECTED-PIN warnings). Run re-checks the same list (`run_document`).
- After a failed run: short message in the status bar and in red under the plot; "Show details" expands
  `RunResult.details` (error type, full message, app diagnostics, core findings, netlist sent to the core). Core
  validator findings (e.g. DANGLING warnings) are listed under the plot and their nets get amber pin rings.
- File errors: Save/Save As/Open failures (`ModelError` from `Document.save/load`) show a critical dialog and a red
  `FAILED: ...` status; a failed open keeps the current circuit; a failed save keeps the document dirty.
- Unsaved changes: New, Open, example loads and closing the window ask Save / Discard / Cancel when the document
  differs from the last saved/loaded state (`MainWindow.dirty()`, `*` in the title). Hidden windows (scripts,
  tests) never prompt.

## Waveform viewer data contract

- Input: only `self.result` (a `RunResult` from `app/sim.py`). Picker items are `RunResult.signals` names grouped
  as Node voltages (`V(n1)`), Voltage across parts (`V(C1)`), Currents (`I(R1)`, `I(V1)`, `I(D1)`); the tooltip lists
  the pins on that node. The plotted series is `result.series[signal["vector"]]`, unmodified (x = seconds,
  y = volts/amperes); `MainWindow.view_data()` returns `(signal, series)` for tests and future consumers.
- Axis units are scaled by tick formatting only (`time (ms)`, `Voltage (mV)`, `Current (µA)`), so line data stays in
  SI base units.
- Cursor: slider, Play, or a click on the plot sets `cur_t`; readout `t = 10 ms  V(C1) = 3.1606 V` and canvas
  current labels/LED glow use `value_at(series, cur_t)`. Stats line: `final`, `min`, `max` of the selected series
  (`RunResult.stats`), SI-formatted to 6 digits.
- Clicking a wire after a run selects the node signal containing that wire's first pin; the ground net has no
  signal (status says it is the 0 V reference).
- Failure state: no signals, empty picker, plot says "Simulation failed", stats empty.

## Tests

`app/tests/test_gui_workflow.py` drives a real `MainWindow` with QTest mouse/key events (offscreen; also passes on
a real X server under `xvfb-run` with `QT_QPA_PLATFORM=xcb`): RC built by mouse with `1k`/`10u` typed in the
inspector, the viewer's line data compared point-by-point with `5(1-e^{-t/RC})` (tolerance 0.5 mV; measured error
~4 µV); save -> new window -> open -> rerun gives bit-identical data; run -> reset -> rerun deterministic;
undo/redo of rotate and param edits; inline validation; no-ground diagnostics before and after Run; a core
NO-DC-PATH failure with details; click-wire-to-plot; switch toggled in the inspector changes the real current;
unsaved-changes prompt and save/open error dialogs; every catalog symbol drawn with its pins on it in all rotations.
Only modal dialogs are monkeypatched.

## Known limitations

- Wire routing is a one-corner heuristic; wires can still cross parts or overlap in dense layouts.
- Junction dots only at pins (wires overlapping mid-segment are not detected as joined, and are not joined).
- Rotated two-terminal parts have pins off the 20 px grid (centre-based rotation); wiring is pin-to-pin so this
  does not affect connectivity.
- One plot, one signal at a time (no overlay); no zoom beyond matplotlib defaults.
- Runs are synchronous on the GUI thread; no cancel/pause.
- Stop time / time step are not part of undo history.
- The scene is a fixed 1000 x 700 area (scrollable).
- No pause/stop: the core is synchronous, so a running simulation cannot be interrupted. To bound it, a run is
  refused before starting when `tstop/tstep` exceeds `MAX_POINTS` (100 000) or points x estimated signals (3 per
  part + 1) exceeds `MAX_SAMPLES` (2 000 000, roughly 200 MB of Python tuples). Examples use 425-2000 points.
- The first waveform sample is at `tstep/100`, not t = 0, so the initial condition is never plotted and the cursor
  starts at that first sample.
- Part names must match `[A-Za-z][A-Za-z0-9_]*` (they reach the SPICE deck verbatim); there is no rename UI yet,
  only `Document.rename_part`. The loader rejects self-wires, duplicate wires, duplicate ids/keys, non-finite or
  |coordinate| > 1e6 positions, and wrong types with one error type (`ModelError`, "Open failed" dialog).
- Unconfirmed: an intermittent PySide6 (6.11.2, Python 3.14) segfault in `MainWindow._problems`
  (`QListWidget.clear`) seen only when a test harness holds Python wrappers of inspector widgets that
  `refresh()` later deletes during redo (`docs/review/attack/a3g_crash_peek.py`). Not reproduced with real input.
