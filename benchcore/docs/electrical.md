# Electrical backend (libngspice)

## Responsibility

Own the entire ngspice conversation: init once, `circ` the deck, optionally
install `stop when time = T` lines, `run`, `resume` per stop, read
current-plot vectors, teardown. Everything above receives only series +
status. File: `src/ngspice.rs` (FFI) + `src/kernel.rs` (sequencing).

## Why this shape

Validated findings (REPORT6): `stop when time = T` + `run` + `resume`
pauses exactly (measured 5e-6 double, diff 8.5e-22) and preserves the live
CKTcircuit (states, RHS, step history, breakpoints) with bit-identical
grids across 8 hard state classes. `tran` while paused restarts from zero,
so only `resume` is issued in progress. `option`/`set` cannot change the
TRAN job's max step: per-segment step policy is impossible on this path.

## Safety rules (enforced in code)

1. Exact-`=` stops only; every pause verified at
   `tol = max(1e-12, 1e-9·|T|)`; deviations fall back to full simulation.
2. Never `tran`/`run` while in progress; never mutate between pause/resume.
3. `run`/`resume`/`circ` return codes checked; loud log patterns
   (`singular|timestep|convergence|stepping failed|...`) plus non-finite or
   non-monotonic grids are hard errors, never fallback series.
4. Reads are current-plot qualified (`<plot>.time`, never bare `tran1.time`
   first); vectors copied immediately; complex vectors refused explicitly.
5. One process-wide OP mutex across the whole sequence; callbacks touch only
   the log lock; library never unloaded; `stop clear` + `destroy all`
   isolate sequential runs. No bg threads in v1 (startup race + invisible
   progress + teardown core-dump, REPORT e11).
6. Interior-NUL deck lines refused (they would silently mutate the deck).

## Step policy

Bounded by the B-source bandwidth rule (`bsource.rs`): events
`TMAX ≤ min_width/10`, sines `≤ min_period/50`, applied as tighten-only
rewrite of the `.tran` 4th arg. Unknown TIME without explicit TMAX is
`InvalidConfig`. PWL/SIN/PULSE sources already get solver breakpoints and
need no bound. This policy is why the 10 µs B-pulse test passes.

## Known limitations

- No per-segment TMAX (ngspice limitation, not a bug).
- XSPICE/OSDI pause behavior untested → fenced to full-sim fallback.
- `snsave`/`snload` file snapshots not used (XSPICE exclusion + no need).
- Long-run handle reuse beyond tested loop counts is bounded by teardown
  but not soak-tested; treat >10⁴ sequential sims as uncharacterized.
