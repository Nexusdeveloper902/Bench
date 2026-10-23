# Bench

PySide6 circuit editor (`app/`) over `benchcore` (Rust/PyO3 ngspice engine, built with maturin).
`experiments/` holds throwaway spikes; `experiments/coupling-proto/` is the MCU coupling prototype (see its `REPORT.md`).

## Commands

```bash
python3 -m app                                                   # launch GUI
scripts/test check                                               # EVERYTHING (app parallel+shuffled, timing, engine, catalog); needs pytest-xdist; see docs/testing.md
scripts/test stress                                              # reliability matrix; run for live/threading/Qt-lifetime changes
scripts/test check -- app/tests/test_canvas.py                   # focused loop only; never a substitute for `check`
QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests         # app tests (headless)
(cd benchcore && cargo test && python3 -m pytest -q tests/python)  # engine tests
experiments/coupling-proto/scripts/run_all.sh                    # prototype e01–e11 -> experiments/coupling-proto/results/*.json
```

After changing Rust in `benchcore/`, rebuild and reinstall the wheel before running app tests:

```bash
cd benchcore && maturin build && pip install --user --force-reinstall --break-system-packages target/wheels/benchcore-*.whl
```

`--break-system-packages` is for this machine only: its system Python is externally managed (PEP 668, `/usr/lib/python3.14/EXTERNALLY-MANAGED`), so pip refuses the install without it. A Python without that marker (a venv, CI) does not need it, which is why README.md omits it.

## Subagent models

Always set `model` explicitly when spawning subagents; never let them inherit Opus by default.

- `haiku`: mechanical work (running tests/commands, timing, collecting output).
- `sonnet`: analysis, audits, and implementation.
- `opus`: only for hard debugging (e.g. a flake Sonnet failed to root-cause).

## Agent gotchas (learned the hard way)

- **`pkill -f` / `pgrep -f` kill your own shell.** The tool runs your command via `bash -c "<whole command>"`, so a
  pattern like `pkill -f pytest` matches that bash process too and the command dies (exit 144) mid-way. List with
  `ps -eo pid,args | grep -E 'pytest' | grep -v -E 'grep|ps -eo'`, then `kill <PID>...`. Never chain more work after a
  `pkill -f` in the same command.
- **Worktrees from `isolation: "worktree"` may start from the wrong commit** (seen: `main`'s old merge, not the current
  branch). Create them yourself from the intended tip, `git worktree add -b <branch> ../Bench-wt/<name> <tip>`, pass the
  path to the agent, and have it verify `git log -1` and the collected test count before working.
- **Ended sessions orphan processes.** When Claude Code exits, background agents stop but their `pytest`/`scripts/test`
  children keep running. Before resuming, `ps` for them and kill by PID.
- **Concurrent runs ruin timings.** Measure only when nothing else (another agent's suite) is running; label numbers
  taken under contention as such.
- **The shell exports `QT_QPA_PLATFORM=wayland;xcb` and `QT_QPA_PLATFORMTHEME=kde`.** conftest forces offscreen for
  pytest, but ad-hoc scripts that import Qt must set `QT_QPA_PLATFORM=offscreen` themselves or real windows open.
- **Never collect `app/tests` and `benchcore/tests/python` in one pytest session**: both have a `test_live.py`
  (import file mismatch).
- **Scratchpad `/tmp/...` is wiped on reboot.** Anything needed across sessions (handoffs, repro data) goes outside `/tmp`.
- **Subagents can't write report files** (the Write tool refuses); they return findings as text and the orchestrator saves them.
- **No `/usr/bin/time`, `stress` or `stress-ng` here**; use bash `time`, and `taskset`/`chrt` + busy loops for CPU contention.
- **A GateGuard PreToolUse hook** asks for facts before the first Bash/Edit of a session and before destructive
  commands. Answer it and retry; don't disable it.

## Notes

- Needs ngspice 47 + `libngspice.so`; `e09` needs OpenVAF via `OPENVAf` env.
- `node`/`npm` only for AVR firmware (`avr8js`), no JS build.
- Frontend design docs: `docs/frontend*.md`; test approach: `docs/testing-strategy.md`.
