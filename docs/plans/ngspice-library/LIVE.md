# Live runs (task 9.5a): mechanism, contract, limits

A live run is a transient that runs while the user changes a part's value (a sensor's lux, °C, N, mT; a switch, a pot,
a source). The circuit responds from the moment of the change, and results stream out in chunks. Engine:
`benchcore/src/live.rs`. Python: `benchcore.LiveRun` / `benchcore.replay`, and `app/sim.py` (`LiveSession`,
`live_params`, `replay_document`). Probes: `probes/live_probe.py` (`python3 live_probe.py <name>`, one per process).

## Decision

**Mechanism (a): ngspice's native stop/resume, the batch pause path, with `alter` at each pause.**
1. The run pauses at every chunk boundary `k·chunk` (`stop when time = T`, a solver breakpoint).
2. Each pause is verified exact.
3. Queued changes are applied there with `alter <dev> = <v>` and read back with `print @dev[param]`.
4. Then `delete all` clears the fired stop, the next stop is set, and the run resumes.

A change therefore takes effect exactly at a recorded boundary time. The step after a breakpoint is first-order and
small, so the response has no artefact beyond the physical step. The change log `(t, device, value)` replays
deterministically as one blocking run that pauses only at the change times.

| candidate | evidence | verdict |
|---|---|---|
| (a) stop/alter/resume | LDR step at t1: the point at t1 has the old value and the next point (t1 + 1 µs) the new one, error 0 either side (`step`). RC source change: 1.6e-5 V from analytic, the same class as a batch PWL step (1.5e-5 V; `rc`). Stops added while paused pause exactly (`stops`). | **chosen** |
| (b) EXTERNAL source (GetVSRCData) | Not probed: coupling-proto REPORT §5 and §10.5 show the callback must be a pure function of sim time. A user change is not: it would land at whatever solver time asks next, rejected steps included, and with no breakpoint. It also needs `ngSpice_Init_Sync` and a different `VSTIM` line in every sensor body, which would change golden netlists. | rejected |
| (c) bg_run + bg_halt/alter/bg_resume | Halts at an arbitrary time (3.884 ms in one run, `bg`). The next step crosses the change at full size with no breakpoint: **2.5e-3 V** error vs analytic on the RC, against 1.6e-5 V for (a). REPORT e11 also found a startup race, a core dump on teardown while running, and a 5× slowdown. | rejected |

## ngspice facts probed for this task (ngspice-47)

- **Flattened name** of a source inside a subckt instance: `XRL1`'s `VSTIM` is `v.xrl1.vstim` (`r.xrv1.r1` for a
  resistor, `rsw1` at top level).
- **Alter forms.** `alter v.xrl1.vstim = 100`, `alter v.xrl1.vstim dc = 100`, `alter @v.xrl1.vstim[dc] = 100` and
  `alter v.xrl1.vstim dc 100` all work while paused. `alter vstim ...` and `alter xrl1.vstim ...` do not: they return
  rc 0 and print `stderr Error: no such device or model name`.
- **Silent alter errors.** An unknown device or param is rc 0 plus a `stderr Error` line, so every alter is read back.
- **Resistor alters** (switch state, pot wiper `r.xrv1.r1` + `r.xrv1.r2`) take effect mid-run. So does an I source's DC.
- **Readback.** `print @dev[param]` is safe and prints the value with 7 significant digits.
- **Unsaved `@dev[param]` reads.** Reading one through `ngGet_Vec_Info` at a pause corrupts ngspice: the next
  resume, or the next `time` read, dumps core. Live runs therefore refuse an `@` observable with no `.save`. The batch
  pause path still has this crash (`pause_at` plus an unsaved `@v1[dc]` observable): **G32**.
- **Fired stops accumulate** and are rescanned every step. 300 accumulated stops on a 30k-point RC cost 2.6× a plain
  run (208 vs 78 ms).
- **`delete all` while paused** keeps the list at one stop: 96 ms against 78 ms plain for 300 chunks, about 8 µs of
  commands per chunk. `.save`d device vectors stay intact, and 3000 chunks reach tstop. Before the first `run` it
  would drop the deck's `.save` entries, so it is only sent while paused.
- **`stop clear` while paused** did not stop a pending `stop when` from firing. Do not rely on it.
- **Breaking values.** lux = 0 or 1e300 on the LDR prints `stderr Error: ... out of range for /` and stops the
  analysis, with rc 0. It is caught by the log scan (task 2) and becomes a `SolverError`.
- **XSPICE.** A d_dff toggle run in 97 chunks shows the same 20 output edges as one run, but edges move by up to
  0.29 µs (tstep 1 µs) because the extra breakpoints move the analog grid around the dac_bridge ramp. Live runs allow
  A devices: each pause is still verified, and an inexact one is an error. O, U and OSDI are refused (the batch fence).

## Contract

**One engine.**
- A live run claims the single libngspice handle from start to end (done, cancelled, failed, closed, or dropped).
- While it holds the claim, `run_simulation`, `replay` and a second live start are refused with
  `SimError::Lifecycle`. That is `BackendError` in Python, message "a live run is active: ...", and
  `run_document` returns status "failed".
- Each chunk holds the op lock only while it computes.

**Task-2 guarantees, per chunk.**
- Every ngspice call goes through `ngspice.rs`, so a controlled exit reloads the library and fails the chunk.
  A run on a reloaded engine never continues silently (`poisoned_engine_during_a_live_run_fails_the_run_and_reloads`).
- `stderr Error` lines, solver and convergence failures, an inexact pause, an observable missing over the chunk, and
  non-finite or non-monotonic points are all typed errors.
- Compat mode stays pinned (`do_circ`), and the deck passes the same `gates()` and TMAX policy as a batch run.

**Batch runs are unchanged.**
- The only change in `kernel.rs` is the `refuse_if_live()` check in `run_simulation`, plus the shared `gates()` and
  `pub(crate)` helpers.
- Golden netlists are untouched: `build_netlist` did not change.

**Changes.**
- Only devices declared at start can change. Each must exist (read back at load) and be an R (resistance) or a V/I
  source with no time function (a DC alter on a PULSE/SIN source would do nothing, so it is refused).
- A change made during chunk k applies at chunk k's end, so the latency is one chunk.
- Several changes before a boundary apply together, in order. For the same device the last value wins.

**Pacing.**
- `realtime = r` holds sim time to r × active wall time, where paused time is not active.
- Every chunk reports `wall` (active seconds) and `rtf` (sim seconds per wall second so far). Measured: 0.1994 for
  0.2, and 0.1 within 10 % in the tests.
- With pacing on, the default chunk is `min(tstop/100, 0.05·r)`, so a paced chunk spans at most 50 ms of wall time.

**Determinism.** `replay(deck, obs, log)` applies each change at its recorded time. Live vs replay: at most 5.1e-5 V
on an LDR + 100 nF run with two changes, and a replay of a replay is bit-identical. The difference comes from the live
run's extra chunk breakpoints.

## What is live (derived from package data, no new package field)

`app/electrical.live_devices(section, part, values)`:
- **Top-level R/V/I parts.** The value of a `{dev} n1 n2 [DC] {key}` line is live: resistor `R`, `V`/battery `dc`,
  current source `dc`. A choice that selects between such lines is live too: SW/PUSH_NO/PUSH_NC `state`.
- **X parts.** Every R/V/I line in the `.subckt` whose value is one `{expression}` over the instance params
  (+ − × ÷ and parentheses) can be altered. A body param is live when no other body line mentions it, and its key
  when the instance line passes it once as `NAME={key}`.
  - This covers every task-9 stimulus (`VSTIM stim … DC {P}`, flagged `stimulus`). The flag is set for LDR lux,
    PTC/RTD_PT100/RTD_PT1000/LM35 temp, PHOTODIODE/PHOTOTRANSISTOR irr, HALL_LINEAR b, FSR force,
    STRAIN_GAUGE/STRAIN_BRIDGE eps, POSITION_SENSOR pos, PRESSURE_BRIDGE pres and DC_MOTOR speed.
  - It also covers POT and RHEOSTAT (R, pos, rw/rmin), SPDT/DPST/DPDT state (instance lines that only swap which keys
    feed the body), and battery voc/rint.
  - It also covers internal R/V/I values such as capacitor ESR, op-amp macro vos/ib/iq and SCR IH: 49 parts in all.
- **Not live.** Anything in a B/E/G expression, a `.model`, a waveform (PULSE/SIN/PWL), C or L values, a mode
  switch, or a nested X instance. The microphone's SPL is in a SIN, so it is not live. HUMIDITY_CAP RH and SOLENOID
  plunger position are static (G27, G28).

## Limits
- Latency is one chunk. ngspice cannot be interrupted inside a chunk (bg_halt was rejected above), so cancel, pause
  and changes wait for the chunk in progress. Keep chunks short for heavy circuits.
- A change queued during the last chunk is never applied (the run ends at tstop) and is not in the log.
- A value inside a param's declared range can still break the solve: LDR lux = 1e300, FSR force = 1e-300. The chunk
  fails loudly. Sensor params have no upper bound for a slider (G31).
- XSPICE edges can move by up to about a third of a time step between a live run and its batch or replay run (G33).
- One live run at a time. Parallel runs need processes (REPORT §6).
