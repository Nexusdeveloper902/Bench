# Testing

## Categories

- `tests/correctness.rs` (all real-engine integration/simulation tests, no
  mocks): analytic correctness (T01 RC 20 mV, T02 divider 200 µV, T03 LC 1%,
  T04 diode range, T22 RL absolute, T23 knee placement), pause equivalence
  (T05/T06 same-grid 1 nV, T15 inductor, T07 seam continuity), policy proofs
  (T16 B-pulse capture, T17 unknown-TIME refusal), failure codes
  (T10-T13, T18, T19, T21), fallback contracts (T14, T20), isolation (T09).
- Unit scope (validator/bsource/thin pure functions) is exercised
  indirectly through T10-T18; dedicated unit tests are next (noted below).
- `tests/adder.rs` (gate-level stress): T30 1-bit FA exhaustive (8 combos),
  T31 4-bit ripple-carry exhaustive (512 combos, 60 NANDs, ~15k pts, ~0.4 s;
  every S bit + Cout vs integer arithmetic, 0.5 V = 10x inside noise margin),
  T32 transistor-level MOS NAND truth table (Level-1 NMOS/PMOS).
  Caught-real-bugs note: initial failures were stimulus/oracle errors (PWL
  final-point ramp, wrong truth-table mapping), found by waveform inspection,
  not by loosening tolerances.
- Python API path is smoke-verified (Simulation → kernel → libngspice).
- Coverage hole (accepted): T31 cannot detect an a↔b operand transposition
  in stimulus or wiring -- addition commutes, so swapped operands give identical
  sums. Mutation-verified: NAND→AND flips and output swaps are caught
  (576/2560 and 512/2048 bit-errors); the transposition is invisible by
  construction, not by loose tolerance.

## Running

`cargo test` (integration suite ~0.1 s on Ryzen 5 5600G; circuit sims are
millisecond-scale). Single-threaded `-- --test-threads=1` for deterministic
ordering; the suite also passes with default parallelism (OP-serialized).

## Tolerance rationale

- 20 mV (RC trace): reltol=1e-3 × 5 V FS = 5 mV floor; LTE at TMAX=τ/100
  accumulates to mV. 20 mV ≈ 4× floor: catches TSTEP/TMAX swaps and missing
  UIC (volt-scale), never asserts option defaults.
- 200 µV / 2 nA (divider): 200× vntol / 2000× abstol: Newton residual +
  rounding. Tighter would test printf.
- 1 % (LC f): interp alone is ~0.25%; 1 % catches unit slips (31×) and
  Gear damping while passing Trap.
- Diode ±130 mV range: spans TEMP 0-50 °C drift (~90 mV) + solver mV;
  rejects open/short/wrong-N. No exact fake value (N=1.5 root is ~1.035 V,
  not 0.7 V).
- 1 nV same-grid pause diff: 1000× ULP but 1e5× below any `.ic`-restart
  seam (~0.2 mV): separates preserved state (pass) from reconstruction
  (fail). Plain-grid comparisons use analytic/50 µV reasoning instead
  (grid effects ≠ pause effects, REPORT6 §4).
- B-pulse 4.5/5 V: binary capture discriminator (miss reads ~0 V).
- RL τ 5 %: LTE + interp headroom on a 100 µs constant.

## Known limitations

- No dedicated unit-test file yet for validate/bsource/thin edge cases
  (F2 `1meg5`, continuation joins, sine-coefficient parsing): covered by
  integration behavior, targeted unit tests recommended next.
- No soak test for >10⁴ sequential sims (RSS/plot-table growth).
- No multi-tone/intermodulation or stiff-nonlinear tolerance case yet
  (inherited unknowns from exp-numerical).
- OSDI has no test (no OpenVAF binary in env) -- fenced, not proven.
