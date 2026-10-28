# Milestones and next steps

Status as of 2026-10-27. This file comes from an independent audit of `main` at `03b5b43`. The audit report is kept outside the repo; its findings are summarised here.

## Done on `audit/integration`

| Change | Where | What it fixes |
|---|---|---|
| ngspice log scan is anchored | `benchcore/src/kernel.rs` (`scan_loud_failure`) | Valid circuits whose title, node, device or model name contained "failed" or "timestep" were reported as solver errors. Now stdout lines (banner, title echo, tables) are skipped, failures are matched by anchored message prefixes, and every stderr `Error...` line still fails the run. "doAnalyses: pause requested" is no longer a failure. |
| Configurable `libngspice` path | `benchcore/src/ngspice.rs` | The path was hard-coded to `/usr/lib/libngspice.so`. It now honours `BENCH_LIBNGSPICE`, then falls back to a search list; the error names every path tried. See README. |
| `benchcore.CoSim` | `benchcore` (`Simulation.cosim()`) | Exact-time co-simulation: `advance(until, changes)` stops exactly at `until`, which is what firmware-in-the-loop needs. `LiveRun` is unchanged. |
| Background batch runs | `app/window.py` (`RunWorker`) | Batch, AC and live replay runs no longer freeze the GUI. Stop abandons the run (a blocking ngspice call cannot be interrupted); a Run pressed meanwhile waits for the engine. See `docs/frontend-architecture.md`, "Background runs". |
| Docs refresh | README, `docs/milestone/REPORT.md`, `experiments/coupling-proto` | Test counts updated; historical claims annotated (the prototype's `e08` loop is open-loop, and `e04` contains no MCU). |

## Feasibility gates for firmware-in-the-loop

| Gate | Result | Notes |
|---|---|---|
| P1 exact-time advance | **Pass** | 294/294 real firmware edges paused exactly. 10⁴ irregular advances: 0 inexact pauses, ~0.15 ms per advance, flat memory. Closed loop with lookahead: 0 ns edge error, deterministic. |
| P2 realistic-circuit throughput (RTF ≥ 0.01 at a 10 µs quantum) | **Pass, marginal** | ~30-node circuit (Uno pin → 2N2222 → LED, RC → LM358 → ADC): RTF 0.010–0.024, measured under load. Cost ≈ one batch run + ~145 µs of engine overhead per chunk; the engine is ~79% of it and avr8js plus IPC ~19%. |
| P3 XSPICE digital part in the loop | **Slow** | A 74HC04 works in live runs (edges within 9 ns of a batch reference). XSPICE costs ~3.7× even in batch, so RTF is 0.008, below the bar. |
| P4 background, cancellable batch runs | **Done** | See above. |
| P5 Intel HEX path | **Pass** | A 40-line strict HEX loader plus an Arduino-core blink compiled outside Bench: half-periods 500.010–500.014 ms in avr8js. |
| P6 log-scan false positives | **Fixed** | See above. |

## M1: Uno in the loop (next)

Place the Arduino Uno part, point it at a compiled `.hex`, wire its pins into the circuit and press Live. The sketch runs. Its outputs drive the circuit through a realistic output stage, and `analogRead` and `digitalRead` see the simulated node voltages.

**Toolchain policy:** Bench's input is a `.hex` file, whatever produced it (Arduino IDE export, arduino-cli, avr-gcc, PlatformIO). Bench never requires a compiler. Examples and tests use committed precompiled `.hex` files, each next to its source and exact build command. CI never compiles firmware. Shipped examples are plain avr-libc C, because the Arduino core is LGPL.

Scope:
1. A `sim:mcu` domain. The Uno gets a `[sim.mcu]` section: core, `f_cpu`, per-pin roles (`digital` / `analog-in` / `power` / `nc`), VOH/VOL, R_out and input thresholds.
2. A firmware runner: a Node subprocess running avr8js. It loads Intel HEX and speaks a line protocol.
3. A pin bridge in the netlist. A driven pin is a `V` source plus a series R_out; a read pin is a node observable.
4. `McuSession` in `app/sim.py`, mirroring `LiveSession`. It is built on `CoSim`, with lookahead edge placement and **event-driven quanta**: it advances straight to the next edge or ADC sample, because chunk count dominates the cost. Cancel, pause and failure behave as in live runs, and the Node process is always killed.
5. UI: a firmware path in the Uno inspector, digital pin traces, and an RTF indicator.
6. Persistence: `firmware = {path, sha256}`. A missing or changed file is reported on load and blocks the run.

Non-goals: ESP32 or other cores, the firmware editor and compiler (M2), USART/SPI/I2C devices, real-time guarantees, more than one MCU per circuit.

Acceptance criteria:
1. **Blink:** a pin-13 blink at 500 ms on Uno + 330 Ω + LED. LED edges match the firmware's cycle-derived times within 1 ns, and the period is within 0.1%.
2. **Closed loop:** a hysteretic ADC sketch regulating an RC node. Periods at Δ = 10 µs and Δ = 2 µs agree within 1%, and repeated runs give identical change logs.
3. **ADC:** for a DC divider at 0, 1.25, 2.5 and 4.99 V, `analogRead` returns floor(V/5·1024) ±1.
4. **Failures:**
   - A corrupt HEX file is blocked before the engine starts.
   - A killed Node process ends the run as `failed`, and the next batch run succeeds.
   - Cancel returns within one quantum + 50 ms.
5. **Persistence:** save, reopen and re-run gives the same change log.
6. **No regressions:** `scripts/test check` and `scripts/test stress` are green. Stress gains MCU cases (subprocess killed, slow subprocess, cancel during a step), and the leak guard also covers orphaned Node processes.
7. **Performance:** 2 s of simulated blink finishes in under 60 s of wall time on the reference machine, or the measured RTF is shown and the target renegotiated.

Risks: per-chunk overhead on real circuits, lookahead causality bugs when firmware reads between edges, avr8js fidelity gaps (CBI timing, no SLEEP), Node lifecycle on Qt shutdown, scope creep.

## M2: built-in firmware editor and compiler

A code editor panel for the MCU part and a compiler that ships with Bench: write a sketch, build it and run it with no toolchain installed. The built-in compiler is one more producer of `.hex`; imported `.hex` files keep working. Open questions:
- **Shipping the compiler:** bundling avr-gcc + avr-libc costs ~100–400 MB per platform. A WebAssembly compiler run by the Node runtime Bench already uses for avr8js is the alternative to evaluate.
- Arduino-core support and its LGPL obligations.
- Compiler diagnostics mapped onto editor lines.
- Sketch source stored in the project file, together with the built `.hex`'s sha256, so runs are reproducible.

## M3: `.op` and DC sweep

Today only transient, AC and live analyses exist. Add an operating point (`.op`, annotated on the schematic) and a DC sweep (`.dc`) with plots. Both can be checked against analytic references, and the background-run worker already covers the UX.

## M4: packaging

Linux first: a wheel or AppImage that bundles or discovers ngspice 47 (`BENCH_LIBNGSPICE` is the hook) and checks the ngspice version at start-up. Windows and macOS follow; their ngspice builds are the real work.

## Known limits to address along the way

- **`MAX_SAMPLES` (`app/model.py`)** counts every node as a signal. It refuses a 20 ms at 1 µs run of the ~30-node MCU test circuit. Count only the observed signals, or decimate.
- **Per-chunk engine overhead** is ~145 µs. Event-driven quanta cut the chunk count; profile the stop/resume path for the rest.
- **XSPICE cost** is ~3.7×. Prefer analog behavioural models for logic in MCU circuits, or accept the lower RTF and show it.
- **gmin-stepping warnings** ("Dynamic/True gmin stepping failed") still fail a run even when ngspice recovers. Decide whether they should be warnings.
- **`LiveWorker._live_done`** re-enables gc before `QThread.wait()` returns, the ordering `RunWorker` avoids by being a joined Python thread. Port it.
- **Test flakiness under heavy load:** the `scripts/test stress` probes phase can flag gc on the xdist receiver thread (seen on `main` too: 1 of 6 seeds), and `test_live.py::test_pacing_holds_the_requested_factor` is wall-clock sensitive.
