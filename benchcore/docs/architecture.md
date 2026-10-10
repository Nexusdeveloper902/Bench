# Production architecture: benchcore 0.1.0

## Kernel responsibilities

The kernel owns simulation lifecycle (idle → ready → running → paused →
complete/failed), the declared-stop schedule, backend invocation, pause
verification, fallback selection, and result shaping. It owns no numerics:
all solving happens inside libngspice. All device physics lives in the
electrical domain (netlists + models).

## Public Simulation API

`python/benchcore/__init__.py:Simulation` exposes simulation concepts:
create, build_circuit/add/add_model, configure, observe, pause_at,
clear_stops, validate, run, observable/time/state, modify, reset, destroy.
The Rust surface (`src/kernel.rs:run_simulation(SimRequest)`) is
`(netlist, observables, stops) -> SimResult`. Raw ngspice commands are not
part of either contract; the PyO3 module exposes only `run_simulation`,
`validate`, `required_tmax` plus typed errors.

## Domain abstraction

`SimRequest` carries no domain tag in v1 because only the electrical domain
exists. The extension point is explicit: future domains add a `domain`
discriminator plus their own request/response types beside -- never inside
-- `kernel.rs`. Shared kernel services (stops, verification, fallback,
typed errors, series shaping) are reused; each domain owns its backend.

## Electrical domain

Netlist-in / series-out. Components (`components.rs`) describe structure
(R/C/L/V/I/D/B/K/T + `.model`/subcircuits); the thin layer (`thin.rs`)
lower an allowlisted AST to B-source text; `bsource.rs` derives TMAX bounds;
`validate.rs` gates topology pre-sim. ngspice is a numerical engine behind
`ngspice.rs`; nothing above it names plots, commands, or breakpoints.

## ngspice adapter

Minimal FFI: Init/Circ/Command/Get_Vec_Info/CurPlot/AllVecs/SetBkpt over
`/usr/lib/libngspice.so` via libloading. Process-wide `OP` mutex serializes
whole runs; callback logs live on a separate lock so callbacks never contend
with the handle. Library is never unloaded. Callbacks are static no-ops that
only buffer logs. Teardown (`stop clear` + `destroy all` + `delete all`, best-effort) runs
before every simulation and after every successful pause-path run;
fallback returns rely on the next run's pre-teardown for isolation.

## Simulation state

Running-transient state (CKTstates history, RHS, delta/order, breakpoint
table) lives entirely inside libngspice and is never copied: pause/resume
preserves it by never leaving the analysis. Kernel-side state is only the
request, the verified stop list, and the copied-out series.

## Event scheduling

v1 schedule = sorted stop list declared before `run`. Each stop becomes
`stop when time = T` (exact `=` form; `>` overshoots and is never issued).
Stops must be strictly increasing inside (0, tstop) with ≥4×tol separation.
Future discrete domains (MCU/network/audio) will contribute stop times to
this same schedule; the kernel already verifies each pause independently.

## Pause/resume

`stop` + `run` + `resume`×n on one handle, same analysis throughout (only
`resume` is issued while in progress; `tran`/`run` would restart from zero).
Every pause time is verified (`tol = max(1e-12, 1e-9·|T|)`); any inexact,
missing, or extra pause falls back to full simulation. Step policy cannot
change across the boundary (ngspice limitation, REPORT6 §5): native resume
is a synchronization mechanism, not a step optimizer. Step economy comes
from PWL/breakpoints declared before the run.

## Measurements / observables

Current-plot-qualified vector reads (`<plot>.v(out)`, `<plot>.time`), copied
immediately (lib-owned pointers never escape). Complex vectors (NULL
realdata) yield explicit absence, never fabricated zeros. Results are
`{observable: [(t, v)], time, npts, wall_ms, used_pauses, fallback_reason}`.

## Component / model representation

Bounded-full scope: primitives + behavioral B (thin allowlist) +
subcircuits + `.model`/params + validator gates. OSDI/XSPICE decks are
accepted for full runs but fenced off the pause path
(`unsupported_pause_domain` fallback). `.subckt` bodies are opaque to
top-level topology (fixed vs the Python prototype, which leaked them).

## Error handling

`SimError`: InvalidCircuit{code} (validator gate, pre-sim), InvalidConfig
(bad stops/deck/unknown-TIME), SolverFailure / ConvergenceFailure (loud log
patterns + non-finite grids; hard errors, never fallback), Unsupported,
BackendFailure, Lifecycle, Unavailable. Python maps each to a typed
exception (`InvalidCircuitError`, `ConvergenceError`, ...) preserving codes.

## Lifecycle

`Simulation`: idle → running → completed | failed (a raising `run` leaves `failed`; `used_pauses` in the result says whether native pauses were hit; runs are synchronous so `paused` is never observed from Python); `reset` clears
results + stops; `destroy` clears the deck. Backend handles are
process-singleton; `stop clear`/`destroy all` isolate sequential runs.

## Extensibility boundaries

Allowed to grow: new domains beside electrical, new Component variants,
thin allowlist entries (`CALLS_1TO1` is the intended growth surface),
validator codes with evidence. Not allowed: GUI imports in the core,
raw-command passthrough in the public API, per-segment TMAX changes,
bg-thread pausing, shared-handle threading.

## Testing strategy

`tests/correctness.rs`: real-engine integration tests (analytic references,
same-grid pause equivalence, seam placement, absolute physics anchors,
failure codes). `cargo test` is the gate; Python API smoke-covered via the
`Simulation` path. See `docs/testing.md` for the tolerance rationale.

## Future MCU / domain integration

MCU (or network/audio) domains join as stop-time producers and
between-pause decision makers: they supply the schedule the kernel already
consumes, and read observables the kernel already returns. No kernel change
needed for the first coupled loop; only a driver that maps domain events to
`pause_at(t)` and post-pause actions to `modify` + fresh runs or `alter`-free
resumes.
