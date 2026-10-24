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

1. live tests (`test_live_ui.py`, `test_live.py`, `test_live_duration.py`) with probes, shuffled, gc thresholds 50,5,5
   (`BENCH_TEST_GC_THRESHOLD`; the same variable tells the conftest leak guard the change is intended) and GIL switch
   interval 1e-6 s (`BENCH_SWITCH_INTERVAL`)
2. same with switch interval 0.2 s
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

## Evidence

Measured timings: see below

Injected-bug experiments, run on scratch worktrees (never committed). The machine was shared with other jobs, so only
detection counts are given, never durations. "Old" = the b2da66d process (full suite in default order, `test_live_ui.py` repeated, quiet process).

| Bug injected | Old detected | New detected | Caught by | Notes |
|---|---|---|---|---|
| Revert `73cb002` (source only; its two regression tests removed, i.e. the tree before the fix) | 0/5 (`test_live_ui.py` x5, 100 passed each) | 5/5 (live files, probes, gc 50,5,5, switch 1e-6, seeds 1-5: 13 ERRORs each); probes alone with default gc thresholds 1/1 | probe: gc started on a non-main thread with a QApplication alive | Fails at teardown of each live test, naming the thread and test |
| Same revert, regression tests kept (as in the repo) | 5/5 | not run | the two regression tests | Once a specific test exists, repetition finds it too; the gain is catching the class before such a test is written |
| Test leaves the global catalog reloaded with an extra part | 0/1 (1666 passed) | 1/1 | leak guard | ERROR at the leaking test: "global CATALOG differs from the default" |
| Test leaves a shown `MainWindow` | 0/1 (1666 passed) | 1/1 | leak guard | "visible top-level MainWindow ... never destroyed" |
| Test leaves a `LiveSession` running | suite red (148 failures in 15 files), 0 at the leaker | 1/1, 1665 others pass | leak guard | Old: failures are in unrelated later files ("a live run is active"); New: only the leaking test errors, no cascade |
| Order dependency: B passes only if A ran first | 0 (default order always passes) | serial full suite, seeds 1-10: in-file pair 5/10, cross-module pair 3/10, either 7/10; parallel `check`, seeds 1-4: 4/4 (cross-module pair every time, in-file pair 2/4) | shuffle (+ xdist worker split) | A state shared only inside one worker is also exposed by the worker split |
| Pre-fix racy live tests (`b2da66d` versions of 4 tests) | 0/5 (quiet) | stress conditions measured with these 4 tests: switch 1e-6+gc 0/5; switch 0.2 0/5; pinned core + 2 spin 0/5; idle priority + duty 0/5 | none of the current stress conditions | Only 24 busy loops on all cores + switch 1e-6 (not a stress condition) failed it: 2/20 |
| Current versions of the same 4 tests | n/a | 0/20 under 24 busy loops + switch 1e-6 | | Deterministic now (gated worker, wait for applied change) |

Reading it honestly: the leak guard and probes detect their bug classes on every run where the old process detected nothing
or pointed at the wrong test; shuffle detects order dependence with probability about 1/2 per seed per pair (hence a fresh seed every run); the
current stress contention conditions did not provoke the old timing races on this machine, so that fix rests on the tests now being deterministic, not on stress.

Coverage guard (`scripts/test check`): validated with a SIGKILLed xdist worker (the run fails, junit still lists the crashed test),
a killed serial phase (reported MISSING), `-k`/`addopts` deselection (reported MISSING; this passed before the fix because the collect step inherited it), parametrized ids containing `::`, nested classes, unicode ids.

Strength audit, `git diff b2da66d..HEAD -- app/tests benchcore/tests`: the same 1665 app node ids are collected at both commits, the benchcore ids
are identical, no `assert` line was removed, no tolerance (`approx`, `abs=`, `rel=`) or timeout changed, no skip/xfail added, no app source changed.
Changes: teardown `hide/close/deleteLater` became `destroy()` (same plus delivering the delete); `MainWindow()` became the `new_window` fixture (same window, destroyed after);
two `wait_for(...)` asserts added to the replay/apply live tests (stronger preconditions); `sleep`s in two `test_live.py` tests became a gated worker
(the assertions are unchanged; changes now land at an exact chunk); three wall-clock tests got `@pytest.mark.timing` (still run, serially, thresholds unchanged);
`test_theme` dropped `QSettings.setPath(IniFormat...)` (the app uses the native format, now isolated by conftest's XDG dirs); `test_live.py` workers run with gc off like the window does.
Worth knowing: that last change means `test_live.py` no longer exercises gc on its worker thread; the app prevents that in production (`73cb002`) and the probes check it elsewhere.

## Limitations

- Shuffle is by module then test; class-scoped state is not kept together.
- Contention conditions depend on the machine; a quiet CI runner or fewer cores changes how hard they bite.
- `timing` tests can still be disturbed by other processes on the machine.
- Stress is probabilistic: a pass lowers, not eliminates, the chance of a scheduling-dependent bug.
- benchcore and app tests are never collected in one pytest session (`test_live.py` basename clash); `pytest.ini` `testpaths` only covers `app/tests`.

## Exceptions in Qt overrides fail the test

The leak guard in `app/tests/conftest.py` fails any test during which an exception escapes a Qt virtual override
(`resizeEvent`, `event`, `sizeHint`, ...). PySide leaves the error pending inside the native call, and in shiboken 6.12
the next override lookup in that state `Py_XDECREF`s a borrowed function, so a later call segfaults (use-after-free), not
just a printed traceback. From an event-loop pass the exception reaches `sys.excepthook` (the guard records it); from a
Python-called native call (`show`, `resize`, `sendEvent`) it is raised at that call. Windows must be destroyed before the
catalog is restored (`extra_root` does so). Standalone repro: `experiments/pyside-override-refcount/`; guard proof:
`app/tests/test_override_guard.py`.
