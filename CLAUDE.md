# Sim

PySide6 circuit editor (`app/`) over `simcore` (Rust/PyO3 ngspice engine, built with maturin).
Root-level `experiments/`, `firmware/`, `src/` are the throwaway coupling prototype (see `REPORT.md`).

## Commands

```bash
python3 -m app                                                   # launch GUI
QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests         # app tests (headless)
(cd simcore && cargo test && python3 -m pytest -q tests/python)  # engine tests
scripts/run_all.sh                                               # prototype experiments e01–e11 -> results/*.json
```

After changing Rust in `simcore/`, rebuild and reinstall the wheel before running app tests:

```bash
cd simcore && maturin build && pip install --user --force-reinstall target/wheels/simcore-*.whl
```

## Notes

- Needs ngspice 47 + `libngspice.so`; `e09` needs OpenVAF via `OPENVAf` env.
- `node`/`npm` only for AVR firmware (`avr8js`), no JS build.
- Frontend design docs: `docs/frontend*.md`; test approach: `docs/testing-strategy.md`.
