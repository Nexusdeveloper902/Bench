# Layer frictions (thin.py, read 2026-10-03) — evidence-backed, no raw-B bypass used

All models below were authored with AST + build_subckt only. Probes via friction_probe.py + ngspice -b.

## F1 — I() sense names not validated (COST 1 extra iteration, T4)
- Probe: lower_expr(['i','typo_sense'], {'a'}, {}) -> `I(typo_sense)` ACCEPTED, no LayerError.
- Task hit: T4_attempt01 used ['i','sens'] (suffix, matches senses-set). Thin accepted; ngspice fatal: `b.x1.bo1: unknown controlling source v.x1.v_sens` + `no such parameter`. Correct spelling is ['i','Vsens'] (full V-prefixed device name), undiscoverable from docstring.
- Rejected-message quality: 1/5 (no layer message at all; failure surfaces only as cryptic sim fatal).
- Fix: validate ['i',s] against {Vsense full names} ∪ {Vsense suffixes} and suggest the exact spelling.

## F2 — Bv/Bi p/n nodes not validated (latent hole, contradicts R4 claim)
- Probe: build_subckt('t',['a','k'],{},[Bv p='otup' n='gnd' expr=['v','a']]) ACCEPTED -> `Bb1 otup gnd V=V(a)` (floating node, convergence wall at sim).
- Code: build_subckt has `for nd in ('p','n'): pass` (no-op). Only V() expr nodes are checked.
- Quality: 1/5 (silent accept; R4 docstring claims typo guard but p/n exempt).
- Did not cost an iteration here (we spelled p/n correctly) but would cost a convergence debug elsewhere.

## F3 — R/C values cannot be params
- Probe: R value '{RVAL}' with params={'RVAL':5} -> LayerError: `R value '{RVAL}' is not numeric/scaled (injection guard)`.
- Impact: Rs=5 had to be literal; param sweep of Rs/C impossible (must rebuild subckt or use B-source Rout instead). Not fatal for these tasks (literal 5/10p worked).
- Quality: 2/5 (correctly blocks injection, but message misleads for legit use; no workaround hint).
- Fix: allow ['param','RVAL'] or {RVAL} substitution for R/C, or document B-source alternative.

## F4 — Vsense/R/C/internal_nodes undocumented (discovery tax)
- build_subckt docstring: `devices: list of dicts {kind:'Bv'|'Bi', p,n, expr, tag}` — omits Vsense/R/C and the `internal_nodes` key (the only way to declare tgt/mid/oint). Had to read code lines 165-175 to discover `d.get('internal_nodes',[])`.
- Impact: T2/T4 setup required code reading; first T2 attempt failed with `unknown node 'mid'` until internal_nodes found.
- Quality: 2/5 (feature exists and works, but undiscoverable).
- Note: `nodes|senses` union also confuses (allows V(sens) pseudo-node, rejects V(Vsens)); I() needs full name while V() needs suffix — inconsistent.

## F5 — smax/smin eps fixed 1e-12, no tuning knob
- Lowering: smax -> sqrt(...+1e-12) hardcoded. Proven native reg33 used 1e-6 for C1 continuity. Thin's 1e-12 still converged here (T4 green), but a stiffer loop could need softer knee with no AST knob (would require hand-rolled sqrt(poly) via call, which is allowed but defeats the helper).
- Quality: 3/5 (helper worked, limitation documented nowhere; not a rejection, so no message to rate — rating reflects configurability).
- Fix: optional eps arg, e.g. ['smax',x,y,eps].

## F6 — self-reference not enforced (warn only, opt-in)
- Probe: Bv out->gnd with expr ['v','out'] ACCEPTED by build_subckt; only selfref_warn() (separate, uncalled) flags: `likely algebraic loop; use RC state node`.
- Quality: 3/5 (warn text is good and actionable, but not automatic — easy to miss).
- Fix: call selfref_warn inside build_subckt (warn, not error, to allow intentional feedback like T4's i(vsens) loop).

## Non-frictions (good rejections, sampled for calibration)
- if->tern hint, limit->clamp, gauss REFUSED(segfault), idt recipe, div-zero 1e32, typo-node guard, unknown-param, exp-overflow, pwl arity, injection guard: all 4-5/5 (specific, actionable, pre-sim). Examples in test_thin.py output. These are why T1/T3 passed first try.
- Translation cost negligible (~0.002 ms/expr, 200x benchmark).

## Friction tally
- Blocking (had to work around): none — all 4 tasks completed with allowed ops, no raw-B bypass.
- Costing iterations: F1 (T4 1 fail), testbench trap T2 (not layer fault, but layer gave no OP-sanity hint).
- Discovery tax: F4 (code reading), F3/F5/F6 (ceilings not hit this round).
