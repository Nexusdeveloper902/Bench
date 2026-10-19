# Gap closure plan

This plan closes the deferred and open rows in `docs/plans/ngspice-library/GAPS.md`. The base commit is 6a8bd94 on `feat/multi-terminal-transistors`. Each workstream runs as one subagent in its own git worktree and branch. Workstreams in the same wave run in parallel. The orchestrator verifies each branch with real exit codes, merges it into `feat/multi-terminal-transistors`, and pushes; nothing goes to main.

## Wave 1 (parallel)

| WS | Gaps | Owns (primary files) | Model |
|---|---|---|---|
| W1 AC analysis | G17, G13 (AC source) | `benchcore/src/{ngspice,kernel,lib}.rs` (batch path), `app/sim.py` batch run, document `analysis` field, `app/results.py` Bode view, run settings in `app/window.py` | Opus, high |
| W2 Live mode | G34, G38, G35, G39, G40, G41, G42 | `benchcore/src/live.rs`, `app/sim.py` LiveSession/live_params, live parts of `app/window.py`, `app/inspector.py` | Opus, medium |
| W3 Behavioural sensors and validator | G27, G28, G29 | `benchcore/src/validate.rs`, humidity/solenoid and B-driven sensor packages | Sonnet, high |
| W4 Package schema and digital | G9, G22, G24, G25 | `app/catalog.py`, `app/electrical.py` (`nc` pins), LM741/LM311 symbols, `_models/digital/*`, ADC/DAC packages | Sonnet, high |

## Wave 2 (parallel, after wave 1 merges)

| WS | Gaps | Owns | Model |
|---|---|---|---|
| W5 Vendor-model path | G10, G19 | `compat = "ps"` emission in `app/electrical.py` and the engine, the licence policy, and vendor files only with a verified redistribution grant | Sonnet, high |
| W6 Switching regulators | G21 | averaged buck/boost packages, checked with AC (W1) and transients | Sonnet, high |

## Wave 3

Integration review (Opus, high):
- adversarial review of the merged branch;
- the full regression run;
- GAPS.md rows updated;
- an addendum to `REPORT.md`.

## Rules for every workstream

- **Shared files:** don't edit `GAPS.md`, `HANDOFF.md` or `REPORT.md`. Write notes to `docs/plans/gap-closure/<WS>.md` and put the proposed GAPS row status in the final reply. The orchestrator merges these.
- **Generated docs:** regenerate `docs/library.md` if packages change. It is regenerated again after each merge.
- **Wheel builds:** worktrees must not install the wheel to the shared user site. Build in the worktree, then run the tests against a worktree-local copy:
  ```
  pip install --no-deps --force-reinstall --target .pylib target/wheels/benchcore-*.whl
  PYTHONPATH=$PWD/.pylib
  ```
  Use the absolute worktree path for `PYTHONPATH`.
- **Exit codes:** capture real ones (`cmd; echo rc=$?`), never `| tail &&`.
- **Commits:** commit to your own worktree branch, locally only. Never push or touch main.
- **A gap that can't be closed honestly:** say so and propose wontfix with the reason. Don't fake it.
