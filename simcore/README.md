# simcore 0.1.0 — production simulation core

Rust kernel + electrical domain over libngspice, Python Simulation API via
PyO3 (`_simcore` + `python/simcore`).

- Build: `cargo build` · test: `cargo test` (26 real-engine tests: 23 correctness + 3 gate-level, ~0.7 s)
- Python wheel: `maturin build` then `pip install target/wheels/simcore-*.whl`
  (`import simcore`); wrapper API: `python/simcore/__init__.py`;
  Python smoke: `python -m pytest tests/python`
- Docs: `docs/` (architecture, api, electrical, components, testing,
  research-to-production)

Experimental predecessors (`experiments/`, `exp-*/`) are reference-only and
untouched. See `docs/research-to-production.md` for what was adopted,
retired, and deliberately diverged.
