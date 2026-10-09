# Repro

Env: ngspice-47 batch, python3, no extra deps beyond exp-safety.

```bash
cd <repo>/experiments/exp-window
python3 tests/test_window.py      # ~4 min, 20 checks, ALL PASS (small windows)
python3 tests/test_hardening.py   # ~5 min, 34 checks, ALL PASS (spike-2 adversarial regression)
python3 run_main.py               # ~5 s verification probes -> logs/window_main.json
python3 run_target.py              # ~30 s: 1M-pt ref + segmented + PWL -> logs/target_demo.json
```

Subagent threads (runners kept at `logs/num_run.py`, `logs/run_circuits.py`;
ngspice/adversarial runners used /tmp scratch):

```bash
python3 logs/num_run.py        # numerical thread (~40 runs)
python3 logs/run_circuits.py   # circuits thread (~32 s)
```

Prior suites unaffected (window.py consumers only):

```bash
cd ../exp-safety && python3 tests/test_regression.py  # ALL PASS
```

Spike-2 evidence: `REPORT2.md` (synthesis) + thread scratch (not in repo):
`/tmp/opencode/exp2/thread_{numerical,hidden,margin,multi,advers}/`.
