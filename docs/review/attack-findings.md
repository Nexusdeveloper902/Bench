# Adversarial integration test findings

Scripts: `docs/review/attack/`. Run from repo root with `QT_QPA_PLATFORM=offscreen python3 docs/review/attack/<script>.py`.
Status: complete (2026-10-06).

## B1 (MEDIUM) Run uses the old parameter value while the inspector shows the new one
Repro: `docs/review/attack/a4_pending_edit.py`. LED example, click the R1 Resistance field, type `1k`, press F5
(or click the toolbar Run button) without Enter.
Expected: the typed value is committed (or Run refuses) before simulating.
Actual: field shows `1k`, the run simulates R = 330 (I(D1) = 9.18 mA, ~3 mA expected) and reports SUCCESS.
The edit is committed only on later focus-out, which then silently discards the result. `run()` only flushes
tstop/tstep (`cfg_changed`), never the focused inspector field.

## B2 (MEDIUM) Changing Stop time / Time step after a run does not invalidate the result (stale waveform)
Repro: `docs/review/attack/a3_undo_stale.py` (STALE line). LED example, Run, type `1m` in Stop time.
Expected: result marked out of date like a parameter edit ("results out of date - press Run").
Actual: state stays `completed`, the 0-5 ms waveform stays plotted. `MainWindow._key()` hashes only parts/params/
wires, and `cfg_changed` never calls `_check_stale`.

## B3 (HIGH) Run limit allows multi-GB, UI-freezing runs
Repro: `docs/review/attack/a7_gui_files_and_limits.py` (last lines); model-level timing in `a1_electrical.py`.
LED example, Stop time `50`, Time step `10u` (5e6 points, just under the `tstop/tstep > 5e6` guard), Run.
Expected: bounded memory/time, or a progress/cancel path.
Actual: the UI thread is blocked 26 s and peak RSS grows by ~5.7 GB for a 4-part circuit (series kept as Python
lists of `(t, v)` tuples, one per signal). Memory scales with signal count, so a 20-part circuit at the same
settings will exhaust RAM. No cancel button; the run is synchronous on the GUI thread.

## B4 (MEDIUM) Malformed files raise uncaught exceptions instead of the "Open failed" dialog
Repro: `docs/review/attack/a5_files.py` (`conn ints`, `deep nesting`) and GUI path in `a7_gui_files_and_limits.py`.
`"connections": [[1, 2]]` -> `AttributeError: 'int' object has no attribute 'partition'` (from `_check_pin`);
`[[[[...` (100k deep) -> `RecursionError`. Both escape `Document.load` (only OSError/UnicodeDecodeError/
JSONDecodeError/ModelError are mapped), so `MainWindow.open` (catches ModelError only) shows no dialog; the
exception goes to the Qt event loop and the user sees nothing happen.

## B5 (LOW-MEDIUM) Part names from files are not validated and reach the SPICE deck verbatim
Repro: `docs/review/attack/a6_injection.py`, `docs/review/attack/a6b_injection_isrc.py`, `a5_files.py`
(`name with space`, `name empty`, `name newline`).
A hand-edited name such as `I1 n1 0 DC 0\nV9 ...` is emitted as a device line: the canvas shows a 1 mA source,
the run succeeds (ok=True) but simulates 0 A (V(n1) 1.0 V -> 0.0 V). `my R` fails with the cryptic
`SolverError: stderr Error: circuit not parsed.`; `""` is accepted as a part name. Dot commands (`.control`/`shell`,
`.include`) are blocked only incidentally because `.` splits pin refs. Names should match `[A-Za-z][A-Za-z0-9_]*`.

## B6 (LOW) Loader accepts other non-canonical data
Repro: `docs/review/attack/a5_files.py`, `a7_gui_files_and_limits.py`.
- Self-wires (`R1.a`-`R1.a`) and duplicate/reversed duplicate wires load as-is (the editor's `connect()` refuses
  them); a duplicate draws a false junction dot.
- Layout `x: NaN/Infinity` loads; re-saving writes `NaN` (not valid strict JSON). `x: 1e308` loads, then every
  canvas hit test raises `OverflowError` in `seg_dist`, so the canvas stops responding to the mouse.
- `"version": 1.0` / `true`, `"rotation": 90.0`, `x: true` are accepted; JSON duplicate keys silently keep the last.
- Saving over a read-only (0444) file succeeds and resets it to 0644 (`os.replace`); saving to a symlink replaces
  the link with a regular file (the target is left unchanged).

Passed: empty/garbage/truncated/list/null/BOM/v2/unknown type/unknown param/dangling ref/bad pin/3-pin
connection/unsupported analysis are rejected with clear messages; save->load->save is byte-identical (LED and
blink examples); unwritable dir, missing dir, directory path -> "cannot save ...: <OS reason>"; file format holds
only components/connections/simulation plus a separate `layout` block (x, y, rotation), with no Qt/canvas state.

## B7 (MEDIUM, usability, confirmed) Inspector fields crushed at 1300x850
Repro: `xvfb-run -a python3 docs/review/attack/a8b_visual_fixed.py` -> `docs/review/attack/shots/fixed_V1.png`.
With the window held at 1300x850, a PULSE voltage source's 7 fields are squeezed to 8-9 px high (18 px font), so
the values cannot be read and the labels overlap. The window's minimum size hint is 1049 px high with that
selection (837 px for a capacitor), and the Play row is drawn over the plot. Capacitor `10u` was not clipped
under xvfb (26 px field); the clipping depends on the available height, so a smaller screen or window chrome
brings it back.

## B8 (LOW, usability, confirmed) Rotate/Delete appear three times
`a8_visual.py`: toolbar `Rotate`/`Delete`, inspector `Rotate (R)`/`Delete selected`, and the Edit menu.
The two button sets are worded differently but do the same thing.

## B9 (LOW, usability, confirmed) LED example shows raw `5e-3` / `10e-6`
`a8_visual.py` / `shots/led_example.png`: the run fields show raw exponent text, while the hint and tooltips
advertise SI input (`5m`, `10u`). The blink example shows `8.5e-3` / `2e-5` the same way.

## B10 (LOW) Waveforms never contain t = 0
Every series starts at t = tstep/100 (for example 1e-7 s for the LED example), so the initial condition is never
plotted and the cursor cannot reach 0. Check: `run_document(Document.led_example()).series['v(n1)'][0]`.

## B11 (NEEDS INVESTIGATION) Intermittent segfault in `MainWindow._problems` (`QListWidget.clear`)
Repro: `docs/review/attack/a3g_crash_peek.py all,peek` (crashes about 1 run in 3; the faulthandler trace ends in
window.py:784 `self.problems.clear()` via redo). It happens only when the harness has fetched Python wrappers of
inspector widgets (`form.itemAt(0).widget()`) that `QFormLayout.removeRow` later deletes. The same steps without
that fetch (`a3f_crash_bisect.py all`) and real-key undo/redo did not crash in 4 or more runs each. This is likely a
shiboken stale-wrapper problem (PySide 6.11.2 / Python 3.14), made easier to hit because `refresh()` destroys and
rebuilds the whole inspector on every edit. The existing suite passed 3 of 3 runs.

## Checked and OK
- All 9 parts placed and wired with the mouse in V-R-DUT-GND (`a2_gui_build_rotate.py`). Results match hand
  calculations: divider, I*R, L steady state V/R, diode 0.693 V / 4.31 mA, LED 1.97 V / 9.18 mA, switch closed and
  open, RC at 5 tau. Four rotations return the pins to their start, wires follow, rotated pins can be hit, moving
  keeps the wires, deleting removes the attached wires, and undo restores them.
- Undo/redo over all edit types (place, wire, rotate, number and choice params, mode, move, right-click
  disconnect, delete wire, delete part): 18/18 steps restore exactly, undo past empty is a no-op, redo-all equals
  the final state, and a 300-step random fuzz left no dangling wires or stale selection.
- Topology: two grounds OK. The core rejects a floating island, a shorted V source, C + I only, V||L, L-L loops,
  parallel V sources and series I sources, each with a clear code. Bad values (0, negative, NaN, inf, 1e999, empty,
  unit mismatch) are rejected. tstep > tstop and tstop/tstep > 5e6 are refused. A failed run never leaves the
  state at `running`, and back-to-back runs are bit-identical.
- Boundary: only `app/sim.py` imports `simcore` (outside tests). The GUI only interpolates and maps results
  (`value_at`, `led_level`), and `sim._diff` is a node-series subtraction.
- Minor observations: an open current source through the 1 GOhm "open" switch silently gives -1e6 V; `1_000` and
  full-width digits are accepted as numbers; source currents use the SPICE sign (I(V1) = -5 mA when delivering).
