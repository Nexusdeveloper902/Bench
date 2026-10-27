# MCU + libngspice coupling: experimental validation report

> **Errata (audit 2026-10-27).** E08 is not a closed loop: the firmware runs to completion first (`firmware/e08_events.js`), ngspice then replays its recorded edges, and the "ADC feedback" and fault count are Python post-processing of the finished waveform (`e08_closed_loop.py`, analysis section); the firmware never reads an analog value. E04 contains no MCU: it polls a finished ngspice waveform in Python. No tracked script calls avr8js `onADCRead`. The coupling mechanism here (EXTERNAL sources + `ngSpice_Init_Sync`) is not the one the production engine uses; a closed loop on `benchcore`'s live run is documented in the audit, not in this prototype.

Throwaway prototype in this repo. Question: can a native host run AVR firmware against a
real nonlinear ngspice circuit, synchronize the two domains at cross-domain events, get
correct electrical measurements, efficiently enough for iterative model development?

Short answer: **yes, with a narrow, evidence-backed recipe** (EXTERNAL source +
breakpoints for known MCU events + bounded step for unknown analog events, sequential
single-instance use, SPICE-primitive/B-source models first, OSDI as fallback).
Rollback is not needed for anything tested. Same-handle concurrency crashes; renamed
copies work. Overall co-simulation is slower than real-time; only the MCU alone is
faster than real-time.

Environment: `ENV.txt` (ngspice-47, libngspice.so.0.0.16, node 24, avr8js 0.21.1,
python 3.14, openvaf 23.5.0, Ryzen 5 5600G). Reproduce: `scripts/run_all.sh`.
Raw results: `results/*.json`.

Claim classes: **[E]** verified experimentally · **[D]** verified from authoritative
documentation · **[I]** strong inference · **[U]** unresolved · **[X]** contradicted.
An independent reproduction agent re-ran e01/e02/e08/e10: all correctness figures
bit-identical, timings within noise — **REPRODUCED**. An adversarial reviewer probed
all claims; its corrections are incorporated below (items marked "adv").

## 1. What was actually tested

- **[E]** libngspice embedding via Python ctypes (init/circ/op/run/tran/vector read).
  `run` blocks; `bg_run` backgrounds (e11; adv: startup race + teardown-join needed).
- **[E]** Real AVR8js 0.21.1 blink firmware (SBI/RCALL/CBI loop, PORTB5): event list
  with cycle timestamps; instr/s measured.
- **[E]** GPIO→LED coupling (R 1k + D(IS=1e-14,N=1.5) + GND, EXTERNAL source) at
  100 Hz–1 MHz, strategies naive / tmax-bound / breakpoint / sync-clamp.
- **[E]** ADC-style polling on RC charge (ground truth vs analytic; poll periods
  5–200 µs; chunked `.ic`+`uic` continuation check).
- **[E]** B-source comparator threshold + EXTERNAL narrow-blip miss probe.
- **[E]** Multi-instance: sequential alternating, same-handle concurrent threads
  (subprocess-contained), renamed-copy concurrent threads.
- **[E]** Modeling paths: `.model` D sweep, B-source diode, subcircuit,
  OpenVAF 23.5.0 compile → OSDI load via shared lib; `alter` vs reload loop timing.
- **[E]** Closed loop E08: 294 real firmware events → breakpoints → LED sim → ADC
  sampling + fault decision, full overhead split.

## 2. What worked

- Embedding, EXTERNAL coupling, breakpoints (0.0 ns placement at all freqs), sync
  clamp (max step exactly the bound), ADC polling (delay ≤ T_poll, reads ∝ 1/T_poll),
  chunked continuation (4.8 nV final-voltage agreement, adv-corrected from "0.0000 mV"),
  B-source diode (0.59 mV of D-model), OSDI diode (8.4 µV of D-model, compile ~46 ms,
  in-process reload works), sequential instance reuse, renamed-copy concurrency,
  `alter`/reload loops (~3–4 ms/iter), bg_run with proper join.
- Closed loop: 294 real AVR events → 1512 pts, edge placement 0.0 ns, ADC 1174 reads,
  0 faults, Vf 1.0365 V / 3.964 mA.

## 3. What failed

- **[E]** Same-`.so` concurrent threads: allocator crash (`free(): invalid pointer`;
  observed SIGABRT rc=-6 and SIGSEGV rc=-11 across runs). Do not share one
  libngspice handle across threads.
- **[E]** `ngSpice_nospinit/nospiceinit()` before Init: deterministic SIGSEGV
  (pathfinder probe; repo omits them).
- **[E]** `pre_osdi` via `ngSpice_Command`: "no such command"; use `osdi <file>`
  **before** `circ` (order load-bearing).
- **[E]** Naive coarse steps miss EXTERNAL edges (up to ~1 step) and miss 2 µs
  analog blips entirely at 50/5 µs tmax (E05b; adv: E05's own PWL probe is invalid
  as miss evidence because PWL self-breakpoints).
- **[E]** bg teardown while thread active core-dumps; bg has a startup race
  (running() False for first ms). bg 50 ms-tran was ~5× slower than sync run.
- No convergence failures were inducible on benign circuits **[E, weak detector]**.

## 4. Measured performance (toy 2–6 node circuits; does not generalize, adv)

| case | wall | pts | evals/step | speed |
|---|---|---|---|---|
| 1 ms LED tran | 2.8 ms | 1008 | ~2.8 | 0.36× |
| 10 kHz GPIO: naive / clamp / bkpt | 0.8 / 3.1 / 0.9 ms | 96 / 440 / 115 | 3.7 / 2.3 / 3.3 | 0.5 / 0.1 / 0.4× |
| 1 MHz GPIO: tmax / bkpt | 134 / 17 ms | 20008 / 2036 | 2.1 / 3.2 | ~0.001 / 0.01× |
| RC ground truth 2 ms | 5.2 ms | 2011 | — | 0.4× |
| OSDI diode tran | 9.9 ms (3.5× D-model) | — | — | — |
| AVR8js | 12–30 M instr/s, 18–42 M cyc/s across configs (±8% repeats) | — | — | ~1.1–2.6× RT alone |
| Closed loop E08 | 52 ms total (node spawn 34 + AVR 5 + SPICE 11.5 + misc) | 1512 | 3.2 | 0.11× system (0.3× w/o spawn) |
| Python sync-callback cost | ~2.6 µs/call in-sim | — | — | — |

Evals/step range over all runs is 2.1–6.0 (adv correction; discontinuities cost
iterations). At 1 MHz × 100 steps/period, real-time is impossible — co-simulation
is a batch analysis tool, not HIL.

## 5. Synchronization findings

- Known MCU events (GPIO edges): **breakpoints are sufficient and optimal** —
  0.0 ns placement (adv: this is placement tautology — a solve lands exactly on the
  event; waveform *between* events remains step-quantized), cheapest point count at
  every frequency (115 vs 440 vs 96 at 10 kHz; 2036 vs 20008 at 1 MHz).
  Caveat: breakpoints are single-use per run — re-arm every run/segment (adv probe).
- Unknown analog events: bound the step. Sync-clamp honored exactly (max step
  1.00 µs for 1 µs bound, err 280 ns); tmax≡Tmax confirmed by 2-arg/4-arg
  equivalence probe (adv). Cost 2.5–208× points vs naive (adv correction).
- ADC polling: detection delay ≤ T_poll (adv: samples were phase-lucky;
  worst-case bound untested; sampler was next-hold, not interpolation).
- **No rollback needed** for anything tested: monotone threshold error ≈ tstep
  (35/6.5/0.77/0.33 µs); multi-crossing/ringing waveforms untested **[U]**.
- VSRC callback is pure in time (381 calls, 0 impure, adv probe) — safe *because*
  the drive is stateless. Stateful MCU-in-callback (ADC-triggered branch) untested **[U]**.
- GetSyncData fires 2×/step (loc 0/1); clamp is shrink-only; VSRC callback itself
  cannot steer timestep **[E]** (ngspice-specialist probes).

## 6. Multi-instance findings

- **[E]** Sequential alternating circuits: 6/6 exact (0.5/1.5 V), no cross-talk.
- **[E]** Same-handle concurrent: SIGABRT/SIGSEGV (rc=-6/-11 across runs).
  Renamed `.so` copies concurrent: both exact (0.5/1.5 V, 2008 pts).
  Matches manual §15.6.1 (per-D verdict **[D]**).
- Sequential plots share one namespace — re-read vectors per run; long-run
  leak/quit-reinit untested **[U]**.

## 7. Verilog-A/OpenVAF findings

- **[E]** OpenVAF 23.5.0 binary (54 MB download): cold compile 46 ms/model;
  `osdi`-before-`circ` loads in-process (0.07 ms); diode matches D-model by 8.4 µV;
  N=2.0 recompile+reload loop 47+9 ms with exact Vf; param-only edit 0.94 ms.
- **[E]** OSDI tran 3.5× slower than built-in D on this toy.
- **[D]** Loader requires OpenVAF-reloaded OSDI ≥ 0.4 (binary strings).
- Verdict: viable fallback, not the default — B-sources/subcircuits cover the
  tested needs with zero toolchain and faster sims. VA learning curve, lint
  strictness, and temp/noise-model behavior untested **[U]**.

## 8. AVR backend findings

- **[E]** AVR8js 0.21.1: cycle counting exact (SBI/RJMP verified); GPIO listener
  synchronous in writing instruction; `setPin`→PCINT verified; ADC override hook
  (`onADCRead`) is the analog-injection point; Timer0/IRQ latencies exact;
  deterministic across runs.
- **[X]** "619-cycle period = hardware truth": CBI costs 1 cycle in avr8js vs 2 on
  silicon (source-confirmed) → true period 620; ~9 µs drift over E08's span. Coupling
  fidelity is relative to avr8js time and unaffected.
- Speed 18–42 M cycles/s depending on config (adv: "27–41 M" cherry-picked; e08
  helper ran ~18 M). No avr-gcc in env (`pacman -S avr-gcc avr-libc`, ~420 MB,
  verified available **[D]**); tiny firmware via in-dist assembler is sufficient.
  SLEEP/SPM unimplemented; USART/TWI/SPI/EEPROM/watchdog untested **[U]**.

## 9. Edit/rebuild/test-loop findings

- **[E]** Primitive `.model` edit → circ-reload 0.1 ms + run 2.8 ms; `alter`+run
  2.96 ms/iter vs full reload 3.87 ms/iter; B-source edit identical cost class;
  OSDI equation edit 47 ms + 9 ms, param-only 0.94 ms.
- Verdict: hundreds of iterations/hour practical on toys; loop latency is
  sim-dominated, so model choice matters less than step policy at scale **[I]**.

## 10. Architectural conclusions

1. Couple via EXTERNAL V/I sources + Init_Sync; MCU owns known-event breakpoints,
   SPICE step bound covers unknown analog bandwidth. No rollback.
2. One libngspice handle = one sequential user. Parallelism = processes or
   renamed copies, never shared-handle threads.
3. Prefer sync `run` with re-armed breakpoints per segment; chunked `.ic`+`uic`
   continuation validated only for memoryless-plus-C state — gate hidden-state
   circuits (L/B-source-C/OSDI) before relying on it **[U]**.
4. Default model path: primitives → subcircuits → B-sources → OSDI/Verilog-A.
5. Drive must be a pure function of sim time inside VSRC callbacks.

## 11. Remaining unknowns

Stiff/convergence-hostile circuits; stateful MCU-in-callback purity; multi-crossing
thresholds; hidden-state continuation; OSDI temp/noise + lint burden; bg_run
throughput penalty cause; avr-gcc firmware path; >2 instances; lockstep-coupled
multi-instance stepping.

## 12. Recommended next architecture (smallest change supported by evidence)

Single-process-per-simulation worker: Python (or Node+koffi) loop =
`AVR8js --(events+breakpoints)--> libngspice(EXTERNAL, tmax/clamp) -->
vectors --> ADC/poll decisions --> next segment`, sequential handle reuse,
netlist fragment per component instance (pin→node map + `.model`/subckt text +
probe list + step-bound policy). No universal IR — this 5-tuple is the entire
abstraction the experiments justify (see §13).

## 13. Evidence for every major conclusion + Component-IR needs

Table: conclusion → experiment → artifact.

| conclusion | experiment | artifact |
|---|---|---|
| embed + KVL-exact LED I/V | e01 | `results/e01.json`, `experiments/e01_embed_led.py` |
| real AVR toggles + speed | e02 | `results/e02.json`, `firmware/e02_blink.js` |
| miss without bound; bkpt 0.0 ns; cost table | e03 | `results/e03.json` |
| ADC bound; chunked 4.8 nV | e04 | `results/e04.json` |
| threshold ≈ tstep; EXTERNAL miss/catch | e05/e05b | `results/e05.json`, `e05b.json` |
| same-handle crash; renamed-copy OK | e06 | `results/e06.json`, `scripts/t2_probe.py` |
| B-source 0.59 mV; alter 2.96 ms | e07 | `results/e07.json` |
| 294 real events → 0.0 ns, overhead split | e08 | `results/e08.json`, `firmware/e08_events.js` |
| OSDI 8.4 µV, 46 ms compile, 3.5× tran | e09 | `results/e09.json`, `models/*.va` |
| clamp exact; bkpt cheapest | e10 | `results/e10.json` |
| bg_run immediate + join discipline | e11 | `results/e11.json` |

Host→experiment information needs (the only IR justified): per component
**pin→node map, drive stub (EXTERNAL source line), event schedule (breakpoint
times), probe list (vector names), step-bound policy (tmax/clamp value +
rationale), model text (`.model`/subckt/B-source/`.osdi` path)**. Anything beyond
this tuple is unevidenced abstraction — do not build it.
