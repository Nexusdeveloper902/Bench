# Research → production

Source tree (`experiments/`, `exp-bexpr/`, `exp-native/`, `exp-numerical/`,
`exp-safety/`, `exp-window/`, `src/ngspice_ctypes.py`) is reference-only and
untouched. Nothing is imported from it; validated logic was re-implemented in
Rust with the evidence as specification.

## Adopted

- Native pause/resume (`exp-native/REPORT6.md`): `stop when time = T` exact
  form + `run` + `resume`-only-in-progress, per-pause verification, fallback
  to full sim, no `tran` while paused, no bg threads, CurPlot-qualified
  reads, sequential-handle discipline. Same-grid bkpt-cont comparison method
  is now the T05/T06 regression design.
- Validator gates + layer tags (`exp-safety/validator.py`): full port, with
  one intentional fix -- `.subckt` bodies are now opaque (the prototype
  leaked body devices into top-level topology, found via t20).
- B bandwidth → TMAX (`exp-safety/bsource.py`): wired as tighten-only
  `.tran` rewrite; unknown TIME demands explicit TMAX (T16/T17 prove it).
- Thin allowlist + traps (`exp-bexpr/thin.py`): `thin.rs` keeps the exact
  allowlist, gauss/limit/pwr/div-zero/self-ref/milli guards.
- Tolerance realism (`exp-numerical/`): reltol-scale tolerances, never
  FP-equality on physics; the advertising of fixed-fine baselines as
  benchmarks was not carried over (PWL/breakpoints are the real baseline).
- EXTERNAL/PWL event recipe (`REPORT.md`): PWL knees used for seam tests;
  B-edges never trusted for placement (T07 rationale).

## Superseded / retired

- Reconstructed continuation (`exp-window/` `.ic`+`uic` + TIME-shift +
  16-code refusal taxonomy): retired from the production path. Native resume
  eliminates every model-class/window/handoff/seam refusal (REPORT6 §7);
  only operational fallbacks remain (`unknown_tstop`, `stop_outside_interval`,
  `stops_not_strictly_increasing`, `stops_too_close`, `stop_rejected`,
  `inexact_*`, `incomplete_after_resume`, `unsupported_pause_domain`).
- Plan/execute continuation framework: no equivalent exists; the request is
  `(netlist, observables, stops)`.
- `bg_run`/`bg_halt` production use: rejected (startup race, invisible
  progress, teardown core-dump).
- Coarse/fine per-segment TMAX: impossible on the native path; documented as
  limitation, not implemented.

## Intentionally left outside production

- `exp-window/` segmented runner + G1-G6 gates (reference only).
- OSDI toolchain path (no binary in env; fence, not feature).
- AVR8js coupling loop (next phase owns it as a stop-schedule producer).
- `scripts/run_all.sh` batch harness (superseded by `cargo test`).

## Deliberate divergences from prototypes

1. `.subckt` opacity (above).
2. `parse_value("1meg5")`-class tokens: still accepted as 1e6 (F2, minor,
   open: tighten to BAD-VALUE).
3. Python `thin.build_subckt` IDENT/sandbox guards have no Rust counterpart
   yet (thin is library-only, no netlist-string path calls it).
4. Teardown issues `stop clear` + `destroy all` + `delete all` (all best-effort,
   errors ignored); there is no `ngSpice_Reset` binding yet.
