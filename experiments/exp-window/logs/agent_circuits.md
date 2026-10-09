# Continuation-aware event windowing (circuits thread): report

Date: 2026-10-04. Env: ngspice-47 batch subprocess + timeout per call, Ryzen 5 5600G.
Runner: `exp-window/logs/run_circuits.py` (tran/wrdata pattern vendored from
`exp-safety/helper_safety.py`; `read_wrdata` + `validator.parse_value` imported).
Raw: `exp-window/logs/agent_circuits.json`. Tmp: `/tmp/opencode`, `cir_` prefix.
Total campaign runtime: ~32 s (well under budget).

Tags: **[E]** measured · **[D]** docs/manual · **[I]** inference · **[U]** unresolved ·
**[C]** conditional (true under stated conditions).

Reference per test = single full-sim run with `TMAX = w/10` (100 ns events → 10 ns,
≈100 kpts / ≈2.6 s per 1 ms sim). Segmented = coarse (`TMAX_C` = 1 µs) + fine
window (`TMAX_F` = 10 ns) + coarse, `.ic`+`UIC` handoff. PWL = B-event restated as
PWL/EXPLICIT source, full sim at `TMAX_C` = 1 µs.

## 1. Handoff recipe (what preserves state) [E]

- Capacitor nodes: `.ic v(node)=<prev-end>` on the continuation segment **and
  remove the device `IC=`** (device `IC=0` overrides `.ic`; verified: kept `IC=0`
  → segment restarted from 0, dropped it → exact to 1e-7) [E].
- Inductor branches: device `IC=<prev i(L)>` **plus** `.ic v(node)=<prev>`
  (ngspice `.ic` rejects `i()` — parse error; device-IC path verified exact:
  49.084→49.250 mA vs analytic 49.25022 mA) [E].
- B/E/D devices carry no state; no action needed [I].
- Every segment runs `UIC`; every B-expression `TIME` constant is shifted by
  −t_start (generic `shift_expr`; DC/PWL-slow sources need no shift once settled;
  slow SIN drive is PWL-resampled per segment) [E].
- `.ic` on B/E-driven nodes is accepted and harmless (tested both `.ic` and
  device-IC on an E-driven cap node — identical) [E].

## 2. Margin rule (proposed, with numbers) [C]

`margin = max(20 × TMAX_coarse, 5 × w_min)`, window = [first_edge − margin,
last_edge + margin]; cluster event edges with gaps < 2 × margin into one window.
Here: `max(20 µs, 0.5 µs)` = **20 µs**. Rationale: ~20 coarse steps for LTE
history rebuild after restart; 5 widths of pre-event baseline [I].
TMAX_coarse = 1 µs was validated for all smooth backgrounds here (RC τ = 1 ms,
1 kHz sine, RL τ = 100 µs); stiffer backgrounds need their own coarse check [C].

## 3. Results (peak = event-shape err, post = err after event+0.5 µs, fin = final-state err)

| test | seg peak / post / fin | PWL peak / post | npts ref/seg/PWL | wall ref/seg/PWL |
|---|---|---|---|---|
| T1 RC memory | 0.29 µV / 0.29 µV / 0.21 µV (rel 2e-7) | 26 µV / 5 µV | 100k / 5.0k / 1.0k | 2539 / 144 / 32 ms |
| T2a RL step+blip | 1.4 µV (v), 16 nV (iL) / 1.4 µV / 0.3 nA | plateau 2e-5¹ / 6 µV | 100k / 5.0k / 1.0k | 2594 / 149 / 37 ms |
| T2b flyback −6.31 V spike | 20 nV (rel 3e-9), iL 10 pA / 0.1 nV / ~0 | −6.321 vs −6.309 (0.2%) / 2.6 mV | 100k / 5.0k / 1.1k | 2597 / 146 / 34 ms |
| T3 diode knee | peak 3 µV (5e-6)², knee-masked 2 µV / 9 µV / 6 nV | peak 0.8 mV / 83 µV | 100k / 8.9k / 1.0k | 2605 / 262 / 34 ms |
| T4 E-amp gain-2 (15 V peak) | 0.4 mV (3e-5) / 0 / 0 | plateau exact¹ / 0 | 100k / 5.0k / 1.0k | 2551 / 148 / 33 ms |
| T5 triggered event | 36 µV / 0.3 µV / 0.2 µV | 10 µV / 5 µV | 100k / 5.0k / 1.0k | 2566 / 146 / 33 ms |
| T6 multi (per-event / merged) | 0.3 µV both / 0.2 µV / 0.2 µV | 30 µV / 8 µV | 100k / 13.1k / 24.9k / 1.1k | 2546 / 398 / 655 / 36 ms |
| T7 boundary (0 / 5 µs margin) | 0.3 µV both / 0.3 µV / 0.2 µV | 26 µV / 5 µV | 100k / 1.5k / 2.0k / 1.0k | 2620 / 61 / 34 ms |

¹ Pointwise-interp PWL numbers on near-vertical edges (T2a 0.27 V, T4 6.0 V) are
output-sampling + 1 ns-PWL-vs-ideal-step construction artifacts, refuted by
plateau values: T2a 0.50279 vs 0.50277, T4 14.9997 vs 14.9997 [E].
² T3 pointwise 17 mV likewise = ideal-step straddle phase on diode-steepened
edges (peaks agree to 3 µV); masked metric excludes ±0.3 µs around edges [E].

## 4. Per-question answers

**1. RC memory [E].** Yes — coarse→fine→coarse preserves the 1 ms-τ state to
0.2 µV with 20× fewer points (17× wall). Nothing observable is lost.

**2. Inductive [E].** Yes for both: RL blip (v and iL continuous through handoff,
final iL err 0.3 nA) and flyback — inductor current (9.93 mA at commutation) AND
the −6.31 V/100 ns spike shape transfer to 20 nV. Inductor handoff needs the
device-IC + `.ic v` pair (§1).

**3. Nonlinear knee [E/C].** Peak preserved to 3 µV, knee region (excl. ideal-step
edges) to 2 µV, post-event to 9 µV. Caveat: pointwise comparison ON a B-step
edge through a diode is straddle-luck (≈17 mV here) in BOTH single and
segmented runs — use peak/masked metrics. Slow-sine resample density must scale
with segment length (fixed 61-pt resample caused 2.1 mV post err; duration-scaled
→ 9 µV) [E].

**4. Feedback [E].** Yes — E-amp loop output returns to exactly 5.000 V
post-event (0.0 err), 15 V blip peak to 0.4 mV. Post-event loop state agrees
bit-for-bit here. (Series-B injection was needed to avoid divider-loading the
input operating point — a test-design note, not a windowing effect.)

**5. State-dependent timing [E].** Coarse pre-scan is *more* reliable than asked:
RC 2.5 V crossing err = 1.4 ns at TMAX=5 µs → 2 ps at 200 ns (smooth-crossing
interp, ≪ TMAX-scale). Predicted event misplaced by 22 ps ≪ 20 µs margin →
windowed run peak err 36 µV. Counterfactual [I]: for STEP-like triggers the
safety spike measured ≈476 ns err at TMAX=1 µs — the 20 µs margin still covers
it 40×, but margins < ~1 µs would misplace step-triggered windows. Rule: margin
≫ prescan timing err; smooth thresholds: any margin works; step triggers: keep
the 20×TMAX_coarse term.

**6. Multiple events [E].** Per-event windows (13.1 kpts / 398 ms) and one merged
window (24.9 kpts / 655 ms) are equally exact (0.3 µV); merged costs ~2× because
the 241 µs fine span is serialized at 10 ns. PWL: 1.1 kpts / 36 ms at 30 µV.
Tradeoff: merge when gaps < ~2× margin (saves restarts), split otherwise [C].

**7. Boundary event [E].** Falsification FAILED (good news): blip 50 ns after a
0-margin boundary restarts exactly (0.3 µV, same as 5 µs margin control).
Restart transient settles in ~ns on these circuits; `.ic` from the coarse
segment is the only carry and it is exact. Aasegment-boundary hazard likely needs
tighter margins (≪50 ns), stiffer state, or ideal-discontinuity AT the boundary
to bite — not probed further [U].

**PWL-rewrite vs windowing [E].** PWL wins cost (70–100× fewer pts, ~35 ms/run:
breakpoints do the refinement for free) and matches peaks to 1e-4–2e-3 with
exact post-event. Costs: (a) it re-models the event (finite 1 ns edges vs ideal
steps — edge samples differ by construction); (b) needs known event times
(same as windowing); (c) `uramp`-style gradual B-shapes need interior PWL kinks
(20 ns kinks used in T2b → 0.2% spike agreement). Windowing keeps the original B
model untouched and is 17–20× cheaper than full-fine; PWL is another 5× cheaper
than windowing.1
...[truncated 1353 chars]