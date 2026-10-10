# Bench prototype (throwaway): MCU + libngspice coupling validation

Answers: can a native host run AVR firmware against a real nonlinear ngspice
circuit, synchronize at cross-domain events, measure correctly, fast enough for
iterative model development? See `REPORT.md` (yes, with a narrow recipe).

## Reproduce

Requires: `python3`, `node`/`npm`, ngspice 47 + `libngspice.so`, gcc (nothing else;
`avr-gcc` NOT required — firmware uses avr8js' in-dist assembler).
For `e09_osdi.py`: OpenVAF binary, path via `OPENVAf` env (default
`/tmp/opencode/openvaf`; download from https://openvaf.semimod.de/download).

```bash
scripts/run_all.sh
```

Runs e01–e11 in order, writes `results/*.json`. Full suite ≈ 1–2 min.

## Layout

- `src/ngspice_ctypes.py` — minimal libngspice ctypes wrapper (init/circ/command/
  vector read, Init_Sync). No production abstractions.
- `e*.py` — one script per question (embed, gpio sweep, adc, threshold,
  miss, multi-instance, models, closed loop, osdi, clamp, bg).
- `firmware/e02_blink.js`, `firmware/e08_events.js` — real AVR8js firmware + event emitter.
- `models/*.va` — Verilog-A resistor + diode sources.
- `scripts/run_all.sh`, `scripts/t2_probe.py` — repro + crash-contained probe.
- `results/*.json` — raw measurement logs. `ENV.txt` — versions.
- `REPORT.md` — final report with claim classification.

## Key findings (details + caveats in REPORT.md)

- EXTERNAL + breakpoints: 0.0 ns event placement, 100 Hz–1 MHz, cheapest.
- Unknown analog events need bounded steps (sync-clamp exact); naive steps miss them.
- Same-handle threads crash (SIGABRT); renamed `.so` copies work concurrently.
- B-source ≈ D-model (0.6 mV); OSDI ≈ D-model (8 µV), 46 ms compile, 3.5× tran cost.
- No rollback needed for anything tested. System slower than real-time; MCU alone faster.
