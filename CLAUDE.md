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

## Notes

- Needs ngspice 47 + `libngspice.so`; `e09` needs OpenVAF via `OPENVAf` env.
- `node`/`npm` only for AVR firmware (`avr8js`), no JS build.
- Frontend design docs: `docs/frontend*.md`; test approach: `docs/testing-strategy.md`.
