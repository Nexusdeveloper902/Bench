# Reproduce

Requires: ngspice-47 (`ngspice --version`), python3 + numpy + scipy.
Embedded cross-check additionally uses `../src/ngspice_ctypes.py` (repo file).

```bash
cd exp-numerical
python3 run_all.py        # ~75 s -> logs/num_results.json (39 records)
python3 advers_verify.py  # ~30 s -> logs/advers_verify.json
```

Expected: `reliable 29, failed 7, silent-failure 2, questionable 1`;
classifier confusion: all `failed`/`silent-failure` exact, B4 conservative,
6 dense-output runs cost-flagged; determinism all True; scipy cross-errors
1.5e-5 / 1.3e-3; Newton-vs-ngspice 0.592 mV (device physics, not error).

Known rev1 mistakes (fixed in rev2, kept as cautionary record): C1 topology,
D6a/D8L reference formulas, C2 algebraic loop, B2b `.tran` arg order, A2
output-density misread. See REPORT.md §3.
