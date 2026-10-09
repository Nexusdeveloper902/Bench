# Continuation-aware event windowing: report

Date: 2026-10-04. Env: ngspice-47 batch (`ngspice -b`, subprocess + timeout
per call), Ryzen 5 5600G. Prior work untouched; all new work in `exp-window/`
(`window.py`, `run_main.py`, `run_target.py`, `tests/test_window.py`, `logs/`).
Existing suite `exp-safety/tests/test_regression.py` still ALL PASS.

Tags: **[E]** measured · **[D]** docs/manual · **[I]** inference · **[U]**
unresolved · **[C]** contradicted. Four parallel threads (numerical, ngspice
behavior, circuit-modeling, adversarial) plus main-thread verification;
raw logs in `logs/agent_{numerical,ngspice,circuits,adversarial}.json/md`,
`logs/window_main.json`, `logs/target_demo.json`.

## Verdict

**Validated only for a restricted class of circuits.**

Coarse→fine→coarse continuation via `.ic`+`uic` + device-`IC=` rewrite +
per-segment TIME-shift is exact (sub-µV) for **R/C/memoryless + L (with
explicit IC handoff) + parseable B-TIME events**, gated by G1–G6 in
`window.py`. It is **refused by construction** for diode TT charge, B `ddt`
near seams, T-lines, K, OSDI/X/exotic, unquantifiable TIME. The PWL-rewrite
alternative (known events as PWL sources, free solver breakpoints) is
validated and another ~5× cheaper where restating the event is acceptable.

The 10 ms / 100 ns target is **beaten, not just met**: 0.124% of the 1M-point
cost (40× better than the 5% target) at 0.15 µV final-state agreement.

## Headline numbers [E]

Target demo (100 ns B-blip at 5 ms in 10 ms RC charge, ref TMAX=10 ns):

| run | peak | final | in-window maxerr | npts | wall |
|---|---|---|---|---|---|
| ref full-fine | 5.0 | 3.03240705 | — | 1,000,007 | 25.4 s |
| segmented (margin 10×w) | 5.0 (err 0) | err 0.15 µV | 0.14 µV / event exact | 1,241 (0.124%) | 57 ms (446×) |
| PWL restatement, coarse | 5.0 (err 0) | err 0.19 µV | exact | 1,047 (0.105%) | 34 ms |

(Main-thread re-verified; an early "-289 µV post-err" was a comparison
artifact — out-of-window clamp of coarse output vs fine ref — replaced by
the in-window overlay above.)

Small-window regression (1 ms, in `tests/test_window.py`, ALL 20 PASS):
segmented 1.24% cost / 0.16 µV; PWL 0.147% cost / peak exact.

## Gating facts (all [E], each ≥2 threads + main repro)

1. **TIME resets to 0 every segment.** Naive continuation misses absolute-TIME
   events entirely (5→0 V, silent). Every segment must TIME-shift B/PWL/PULSE/
   SIN constants by −t_start (`shift_b_expr`; TSTART does not offset TIME).
   A coarse endpoint landing on an event kink fabricates a `u(0)=0.5`
   phantom point (~6.5 mV spurious charge on a 500 µV signal).
2. **`.ic` carries node voltages exactly** (RC split-phase independent,
   0.0 µV; 2–8 segments accumulate ≤0.03 µV). **It cannot carry inductor
   current** (`.ic i(L)=…` is a fatal syntax error); L state rides on device
   `IC=` line rewrites (exact to 7.6e-10 A; forgetting it: 0.316 A jump,
   −99.8% energy). UIC is load-bearing for L instance-ICs (without it they
   silently reset to DC-OP), harmless for C — always pass `uic`.
3. **Hidden state is uncarriable**: diode TT (43.5 mV silent tail divergence),
   `ddt()` at seams (impulse area 1→0; `.ic` on a B-driven node injects a
   3× phantom impulse), T-line in-flight pulses (5→0 V lost), K-coupled flux
   (unless every winding IC rewritten — denied by default). `idt()/sdt()`
   do not exist in ngspice-47 B-sources; OSDI untested (no binary) → deny.
   No `savestate/loadstate` exists in ngspice-47 batch (probed → "no such
   command").
4. **Margin rule: margin,settle ≥ 10× event width, endpoints ≥1 coarse step
   off kinks** (1 µs for 100 ns → 1 nV; 1× width leaves 2% tail error;
   0-margin fails by kink contact). Coarse TMAX must still resolve the slow
   background (10 µs coarse on τ=1 ms RC leaves 15 µV background error vs
   0.16 µV at 1 µs coarse — background truncation, not handoff).
5. **Restart LTE-reset is real but harmless here**: 10/20/40…ns geometric
   dt ramp (+11 pts/restart), LC max-di/dt local error 335 µV damps to
   identical 15 µV final; energy jump 1e-7 rel (40× below baseline drift);
   0 rejected steps everywhere (rusage cannot detect continuation breaks).
6. **Failures are SILENT**: `check_runtime` catches 0/6 silent breaks (rc=0,
   zero warnings, per-segment dt_max fine). Hence gates G1–G6 are
   load-bearing, not advisory. Endpoint agreement can mask breaks (L case
   finals agree while the stitch jumps 0.316 A) — continuity gating (G5) and
   reference spot-checks are required, not optional.

## Per-question answers

* state preservation: C exact; L exact iff IC rewritten; TT/ddt/T/K/OSDI
  refused [E]. * timing: smooth-threshold prescan to ~ns even at 5 µs coarse;
  step triggers err ~TMAX-scale (69 ns @1 µs, 476 ns family) → margin must
  cover prescan error + w/2 + tmax_fine [E]. * amplitude/shape: exact outside
  ideal-step edge straddle (±15 ns pointwise artifacts are sampling, use
  peak/masked metrics) [E]. * pre-event: coarse exact on smooth backgrounds
  [E]. * post/final: sub-µV [E]. * accumulation: ≤0.03 µV over 8 segments
  [E]. * invariants: LC energy jump 1e-7 rel [E]. * adaptive stepping: no
  reject inflation [E]. * B-TIME: shift mandatory [E]. * multi-events:
  per-event vs merged equally exact; merged ~2× cost; cluster gaps <2×margin
  [E/C]. * boundaries: 0-margin fails by kink contact; 50 ns-post-boundary
  with exact restart passed on benign RC — margin rule stands regardless [E].
  * state-dependent events: auto-prescan works for smooth crossings; step
  triggers keep the 20×TMAX_coarse margin term [E/I]. * failure behavior:
  silent PASS in 7/8 adversarial cases; 6-gate minimum rule in
  `agent_adversarial.md` §7 [E].

## Auto windows?

Yes for parseable B-events: `derive_windows` (bsource widths/events +
margin rule + gap clustering) matched manual windows exactly (0.3 µV runs)
[E]. `unknown_TIME` (bare TIME arithmetic) → verdict `unknown`, user must
supply windows [I, enforced]. State-dependent triggers: user supplies the
trigger rule; prescan + margin rule does the rest [C].

## PWL vs windowing [E]

PWL wins cost (~100–450× fewer pts than fine-B; ~5× cheaper than windowing)
with exact peaks/post-event, but re-models ideal steps as finite edges and
needs known times — same precondition as windowing. Windowing keeps the
original B model untouched at ~17–80× savings. Prefer PWL where restatement
is acceptable; window where the B model must stay authoritative.

## D4 secondary [U, narrowed]

pivtol=1e-18 / pivrel=1e-10 / KLU-toggle via `.options` do not move the
I×1 Ω phantom (still silent 1 mV) [E]; binary strings confirm pivot/SPARSE
machinery exists ("Minimum acceptable pivot", "IS_SPARSE(Matrix)") [E].
Exact source line still not traced (no ngspice sources in env) — remains
**[U]**; mitigation (static refusal) unaffected.

## Production mechanism (minimal)

`window.py` (~300 lines): `shift_b_expr`, `classify_state` (G1),
`derive_windows` (auto, G3), `run_segmented` (G3/G4/G5/G6),
`pwl_pulse_source` (alternative). `tests/test_window.py`: 20 checks,
ALL PASS (~4 min, no 1M-pt runs except via the logged demo).

## What remains [U]

Nonlinear-stiff backgrounds' coarse-TMAX validity; OSDI state class;
overlapping-window double-count at discontinuities (inferred, not measured);
generalization of the 10× margin beyond τ/w≈1e4–1e5 (re-sweep per class).
