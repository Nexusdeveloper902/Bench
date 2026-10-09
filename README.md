# Sim

## Native circuit app (`app/`)

PySide6 schematic editor + transient plot over the real `simcore` (ngspice) engine. Build LED circuit step by step, shortcuts, stale-result handling: see `docs/frontend.md`. After Run the LED glows by its core current; a time slider + Play scrubs the run (try `File > Load blinking LED example`).

```bash
python3 -m app                                   # launch GUI
QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests    # 65 tests (incl. LED workflow e2e)
(cd simcore && cargo test && python3 -m pytest -q tests/python)
```

Needs the simcore wheel installed (`cd simcore && maturin build && pip install --user --force-reinstall target/wheels/simcore-*.whl`).

## Experiments (`experiments/`)

Throwaway spikes, each in its own folder with its own `REPORT*.md` / `REPRO.md`:

- `coupling-proto/` — MCU + libngspice coupling validation (see its `README.md`).
- `exp-bexpr/` — native ngspice behavioral expressions vs a thin validated layer.
- `exp-native/` — native resume vs reconstructed continuation.
- `exp-numerical/` — numerical robustness and convergence.
- `exp-safety/` — simulation safety boundary.
- `exp-window/` — continuation-aware event windowing.
