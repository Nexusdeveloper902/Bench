# Testing

One entry point: `scripts/test` (Python stdlib; needs `pytest-xdist`: `pip install pytest-xdist`).

| Command | When | Guarantees |
|---|---|---|
| `scripts/test check` | before every push; CI runs it | **Every** test runs once: app suite in parallel (`pytest-xdist`) in a seeded random order, then the `timing` tests serially, then `cargo test` and benchcore Python tests, then `python3 -m app.catalog`. Any failing phase fails the run. |
| `scripts/test check -- app/tests/test_canvas.py` | tight local loop | Only what you name (pytest args after `--`); benchcore and catalog are skipped. **Not a substitute for `check`.** |
| `scripts/test stress` | changes to live/threading/Qt lifetime; before merge/release; CI weekly + manual | The live tests and the full suite under varied hostile conditions (below). Finds ordering, threading and scheduling bugs that a plain run does not. |

`check` prints the shuffle seed, per-phase wall time, and a repro command for each failed phase.
`--seed N`, `-n N`, `--dist load|loadfile`, `--junit DIR` are available; `stress` also takes `--rounds N` (repeat the matrix, seed+1 each round).

## Why parallel and shuffled

The suite is CPU-bound on one core (~12 min serial, mostly `catalog.reload` and `MainWindow` construction; ngspice is ~2 %).
Engine state is per process, so xdist workers are independent. Random order exposes tests that only pass because of what ran
before them. `timing` tests (marker in `pytest.ini`) assert wall-clock behaviour and run serially on an otherwise
quiet machine after the parallel phase.

## Stress conditions

Each run is a fresh process; the summary lists condition + seed per run. Probes (`app/tests/probes.py`, loaded with `-p`) fail
the test on Qt cross-thread warnings ("Timers cannot be stopped from another thread", "different thread",
"Cannot send events", "QBasicTimer", "QPainter") and on a gc collection starting on a non-main thread while a QApplication exists.
`-X faulthandler` plus `faulthandler_timeout=300` dumps stacks of a hung test.

1. live tests (`test_live_ui.py`, `test_live.py`, `test_live_duration.py`) with probes, shuffled, and gc thresholds 50,5,5
   (`BENCH_TEST_GC_THRESHOLD`; the same variable tells the conftest leak guard the change is intended)
2. same with GIL switch interval 1e-6 s, and 0.2 s (`BENCH_SWITCH_INTERVAL`)
3. same pinned to one core (`taskset -c K`) with two busy loops on that core
4. same under `chrt --idle 0` pinned to core K with a 50 % duty busy loop at normal priority on K
5. `benchcore/tests/python/test_live.py` in its own session (its basename clashes with the app one)
6. full app suite in parallel with probes and gc 50,5,5

No retries anywhere: a failure is a failure. Busy loops are always killed (finally / SIGTERM).

## Reproducing a failure

The failure summary prints a complete command, e.g. `cd <repo> && QT_QPA_PLATFORM=offscreen BENCH_SWITCH_INTERVAL=1e-6 python3 -X faulthandler -m pytest ... --shuffle-seed 123 ...`.
For a CPU-contention run the command is prefixed with the same `taskset`/`chrt`; start the busy loops yourself
(`taskset -c K python3 -c 'while 1: pass'`). Order-dependent failures reproduce serially: `python3 -m pytest -p app.tests.shuffle --shuffle-seed N app/tests`
(add `-n 12` to reproduce a parallel one; the same seed gives the same order on every worker).

## What replaced the old process

Before: the full app suite twice, `test_live_ui.py` up to 20 times, then benchcore (~53 min). Repetition found nothing; the real past
flakes (gc on a worker thread, `73cb002`; a Qt paint segfault, `docs/plans/gap-closure/W1.md`) were found by probes and low gc thresholds.
`check` replaces the 2x run (once, parallel, shuffled); `stress` replaces the 20x loop with conditions that actually provoke those bugs.

## Limitations

- Shuffle is by module then test; class-scoped state is not kept together.
- Contention conditions depend on the machine; a quiet CI runner or fewer cores changes how hard they bite.
- `timing` tests can still be disturbed by other processes on the machine.
- Stress is probabilistic: a pass lowers, not eliminates, the chance of a scheduling-dependent bug.
- benchcore and app tests are never collected in one pytest session (`test_live.py` basename clash); `pytest.ini` `testpaths` only covers `app/tests`.
