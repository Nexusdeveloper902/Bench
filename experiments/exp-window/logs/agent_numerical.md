# Numerical-analysis thread: segmented (.ic+uic) continuation in ngspice-47 batch

Date: 2026-10-04. Env: ngspice-47 batch via subprocess, 1 ms windows (iteration speed;
cost scales as T_sim/w_min per safety report, so ratios transfer to 10 ms).
Method: `exp-window/logs/num_run.py` reuses `exp-safety/helper_safety.py`
(`tran_run`/`run_cir`, subprocess + timeout). Handoff state is read from wrdata
series tails. Tmp files use `num_` prefix in `/tmp/opencode`.
Raw numbers: `agent_numerical.json` (this log dir).

Claim tags: **[E]** verified experimentally · **[D]** authoritative docs ·
**[I]** strong inference · **[U]** unresolved · **[C]** cost/counting measurement
(no solver judgment; extends the safety-report tag set, which has no [C]).

## 0. Headline results

1. RC (C-only state): split-point phase does not matter; 2–8 segments accumulate
   ≤0.03 µV final error. Handoff is exact to print resolution **[E]**.
2. Every restart resets the step size to 10 ns with geometric ramp-up
   (10/20/40/80/160/320 ns), regardless of TMAX — LTE-reset/startup signature,
   +11 pts per restart on a 1 ms RC run **[E]**.
3. LC tank restarts cleanly **iff both states are handed off**
   (C voltage via `.ic`, L current via device `IC=`): final error 15 µV (3 ppm),
   energy jump 1.3e-13 J (1e-7 rel) **[E]**. `.ic` cannot carry inductor current
   (`.ic i(L1)=…` is a syntax error, rc=1) **[E]** — forgetting L state destroys
   99.8% of the energy (adversarial case, §4).
4. **TIME resets to 0 on every restarted run** **[E]**. Absolute-TIME B-source
   events are invisible to unshifted continuation segments (fine window saw
   drv=0; peak error −5 V) and a coarse segment whose endpoint lands on the
   event kink fabricates a `u(0)=0.5` phantom 2.5 V point **[E]**.
   Segments must time-shift event expressions (`TIME+t_off`) — the shift is part
   of the mechanism, not optional.
5. Zero-margin window fails exactly by kink contact (§3): pre-window coarse
   endpoint touches the rising edge → phantom kick → 3.9 mV final error on a
   303 µV signal **[E]** (expected failure, mechanism identified).
6. Safe rule for this circuit class: **margin,settle ≥ 10× event width**
   (1 µs for 100 ns blip) gives 1 nV final error at 0.34% of full-fine cost
   (345 pts / 27 ms vs 100 kpts / 2.55 s) **[E][C]**.
7. Restarts do not inflate rejected steps: 0 rejected in all 40+ runs, ref and
   segmented alike; iter/pt 1.97–2.00 everywhere **[E]**.

## 1. RC handoff error (5 V step → R 10 k → C 100 n, τ = 1 ms, T = 1 ms)

Reference: single run, TMAX = 1 µs, 1011 pts, Vfinal = 3.16060294 V **[C]**.

| split (ms) | region (slope) | Vsplit (V) | Vfinal err |
|---|---|---|---|
| 0.1 | slewing (4.52 kV/s) | 0.475813 | 0.0 µV |
| 0.3 | slewing (3.70 kV/s) | 1.295909 | 0.0 µV |
| 0.5 | slewing (3.03 kV/s) | 1.967347 | 0.0 µV |
| 0.693 (½-charge) | slewing (2.50 kV/s) | 2.500000 | 0.0 µV |
| 0.9 | flat (2.03 kV/s) | 2.967152 | 0.0 µV |

Segment counts (equal splits): n=2 → 0.0 µV; n=4 → −0.01 µV; n=8 → −0.03 µV;
1022/1033/1055 total pts. Conclusion: no phase dependence, no meaningful
accumulation — error is flat at ≲0.03 µV (sublinear: 4× segments → 3×
sub-print-resolution noise, i.e. no growth at all) **[E]**. RC+C-only handoff
via `.ic v(cap)` is numerically transparent. (Prior e04 chunked check's 4.8 nV
is the same phenomenon at tighter print precision **[E]**.)

## 2. Restart order-reduction (dt sequence + local error + LC ringing)

- Uninterrupted run holds dt = 1 µs steady (dt_min 10 ns is only the t=0
  startup point) **[E]**. Every restarted segment opens with
  10/20/40/80/160/320 ns geometric ramp before recovering to TMAX **[E]** —
  consistent with first-step Backward-Euler / LTE-history reset after losing
  the multistep/truncation history **[I]** (ngspice uses trapezoidal
  integration **[D]**; the exact first-step method flag was not traced to
  source — reset behavior itself is measured, mechanism class inferred).
- RC seg2 first-5-step values track the reference slope with no overshoot **[E]**.
- LC tank (L = 10 mH, C = 100 nF, f0 = 5033 Hz), TMAX = 1 µs, T = 1 ms:
  - restart at v≈0/i=max (t = 50.96 µs): max |Δv| over first 5 steps (time-aligned
    vs interpolated ref) = 20 µV; final err −15.3 µV / +0.066 µA **[E]**.
  - restart at v≈−peak/max |di/dt| = −499 A/s (t = 100.96 µs): max |Δv| first 5
    steps = 335 µV (~13% of the 2.5 mV local movement — the di/dt scaling
    signature of startup order reduction) **[E]**; final err −15.1 µV, i.e.
    identical to the gentle-split case: the local error damps out, none of it
    persists **[E]**.
  - Ringing/overshoot: none. Post-restart envelope peak is 40 µV *below* the
    reference envelope in both cases (trapezoidal damping, no excitation) **[E]**.
- Cost of the reset: +11 pts per restart on RC (1022 vs 1011), +~10 startup
  points absorbed in LC seg lengths **[C]**.

## 3. Margin/settle (100 ns B-blip at t0 = 500 µs into τ = 1 ms RC)

Blip: `B1 drv 0 V=5*(u(TIME−500u)−u(TIME−500.1u))`. Reference: full-window
TMAX = 10 ns (min_width/10), 100011 pts / 2554 ms, drv peak 5.0 V, cap peak
500 µV, cap final 303 µV. Coarse TMAX = 10 µs alone sees drv peak 0.0 V —
event fully missed **[E]**. Segments (all with TIME-shifted expressions):
coarse 10 µs / fine 10 ns / coarse 10 µs.

| margin | settle | drv peak err | tail err @ exit | final err | total pts | cost ratio | wall |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 0.0 V | +6.50 mV | +3.93 mV | 185 | 0.18% | 22 ms |
| 100 ns | 100 ns | 0.0 V | +10 µV | +6.1 µV (2%) | 185 | 0.18% | 22 ms |
| 1 µs | 1 µs | 0.0 V | −0.2 pV | −1.1 nV | 345 | 0.34% | 27 ms |
| 10 µs | 10 µs | 0.0 V | 0.0 | −1.1 nV | 2145 | 2.1% | 74 ms |

- 0/0 **fails as predicted** **[E]**: segA's coarse endpoint lands exactly on
  the rising kink; `u(0)=0.5` evaluates a phantom 2.5 V single point and pumps
  ~6.5 mV of spurious charge into C (13× the true 500 µV peak). Final error
  3.9 mV on a 303 µV signal. Mechanism: kink contact, not coverage.
- 100 ns (1× width): captures the peak but leaves 2% tail/final error **[E]** —
  marginal, not safe.
- ≥1 µs (≥10× width): exact to ~1 nV (0.0004%) **[E]**.
- **Minimum safe margin rule: margin,settle ≥ 10× event width, and strictly > 0
  with the pre-window endpoint kept ≥1 coarse step off any event kink**
  (here: ≥1 µs for a 100 ns event; 1 nV error at 0.34% cost, 95× wall speedup;
  10 µs costs 2.1% for no further accuracy gain) **[E][C]**. Generalization to
  other τ/width ratios is untested **[U]** — re-sweep per circuit class.

## 4. Conservation/invariants (lossless LC, E = ½Cv² + ½Li²)

- Uninterrupted: E0 = 1.250000e-6 J, Efinal = 1.249995e-6 J, drift −4.2e-6 rel
  (integrator dissipation baseline over ~5 periods at TMAX = 1 µs) **[E]**.
- Restarted (proper C+L handoff): energy jump at restart = −1.3e-13 J
  (1.0e-7 rel — 40× below the baseline drift); final-energy rel err −4.3e-6,
  i.e. indistinguishable from uninterrupted drift **[E]**. Restart is
  energy-transparent when the full state is carried.
- Adversarial (expected failure) **[E]**: restart carrying only `.ic v(tank)`
  with L IC reset to 0 — final-voltage error −4.94 V on a 5 V tank, energy drop
  −99.84% rel. Inductor state has no `.ic` syntax (hard error); the device
  `IC=` parameter is the only carrier, and silently defaulting it to 0 is the
  failure mode to gate against. Any continuation framework must refuse an
  L-carrying segment whose inductor currents are not explicitly handed off **[I]**.

## 5. Adaptive-stepping interaction (rusage all)

| case | accepted | rejected | iter/pt |
|---|---|---|---|
| RC ref | 1012 | 0 | 2.00 |
| LC ref | 1012 | 0 | 2.00 |
| B-blip full-fine ref | 100012 | 0 | 2.00 |
| B-blip segmented (all 4 sweeps) | 188–2148 | 0 | 1.97–2.00 |

Zero rejected steps in every run, reference and segmented **[E]**. Restart
does not inflate rejections; cost of continuation is purely the ~10-pt startup
ramp per segment (§2), not solver struggle. (Reject counters stayed at 0 even
for the failed cases — rusage cannot detect the §3/§4 failure modes; the
`REJECT-RATE>0.5` tripwire is a backstop for stiff pathology, not for
continuation correctness **[I]**.)

## 6. What this gates for continuation-aware windowing

1. TIME-shift of event expressions per segment is mandatory (not just `.ic`) **[E]**.
2. Full-state handoff is mandatory: `.ic` covers node voltages only; inductor
   currents ride on device `IC=`; B-source/L/OSDI hidden state beyond this
   (memoryless+C tested here) remains the gating unknown per the safety report **[U]**.
3. Window margins ≥10× event width with kink-avoiding coarse endpoints **[E]**.
4. Suggested static gates before relying on a segmented run **[I]**: refuse
   segments containing L unless every inductor current is mapped to an `IC=`
   value; refuse zero/negative margins; verify `max|dt_coarse_endpoint_to_kink|
   ≥ dt_coarse`; verify fine-window peak against the event's analytic peak.
