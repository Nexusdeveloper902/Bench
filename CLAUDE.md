# Bench

PySide6 circuit editor (`app/`) over `benchcore` (Rust/PyO3 ngspice engine, built with maturin).
`experiments/` holds throwaway spikes; `experiments/coupling-proto/` is the MCU coupling prototype (see its `REPORT.md`).

## Commands

```bash
python3 -m app                                                   # launch GUI
QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests         # app tests (headless)
(cd benchcore && cargo test && python3 -m pytest -q tests/python)  # engine tests
experiments/coupling-proto/scripts/run_all.sh                    # prototype e01–e11 -> experiments/coupling-proto/results/*.json
```

After changing Rust in `benchcore/`, rebuild and reinstall the wheel before running app tests:

```bash
cd benchcore && maturin build && pip install --user --force-reinstall target/wheels/benchcore-*.whl
```

## Notes

- Needs ngspice 47 + `libngspice.so`; `e09` needs OpenVAF via `OPENVAf` env.
- `node`/`npm` only for AVR firmware (`avr8js`), no JS build.
- Frontend design docs: `docs/frontend*.md`; test approach: `docs/testing-strategy.md`.
