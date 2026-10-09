# Reproduce

Requires: ngspice-47 (`ngspice --version`), python3 + numpy (scipy not needed
here). Embedded D4 cross-check additionally uses `../coupling-proto/src/ngspice_ctypes.py`
(repo file, unchanged). Prior work (`experiments/`, `exp-bexpr/`,
`exp-numerical/`, `results/`) untouched; everything new is in `exp-safety/`.

```bash
cd exp-safety
python3 run_bench.py             # ~1 min  -> logs/bench.json (validator benchmark)
python3 run_events.py            # ~2.5 min -> logs/events.json (blip/sine/multi/MCU/PWL)
python3 run_events2.py           # ~10 s   -> logs/events2.json (fixes + edge/fragility)
python3 run_events3.py           # ~20 s   -> logs/events3.json (smooth-hump w/1 vs w/10)
python3 run_runtime.py           # ~1 min  -> logs/runtime_matrix.json
python3 run_d4.py                # ~1 min  -> logs/d4.json
python3 run_advers.py            # ~3 min  -> logs/advers.json (21 probes + 300 fuzz)
python3 tests/test_regression.py # ~30 s   -> exit 0 iff all 32 guarantees hold
```

Expected tallies: bench `valid FP 0/16, malformed FN 0/15`;
runtime matrix `7 both-accept, 5 validator-earlier, 1 ngspice-only,
2 validator-only, 1 runtime-only, 2 cost/questionable-pass, 0 HOLE`;
fuzz `agree-ok 143, contained 157, FP 0, SILENT-WRONG 0, CHECK 0`;
regression `ALL PASS` (exit 0).

Total campaign ≈ 9 min (dominated by the deliberate 1M-point policy-cost runs).
