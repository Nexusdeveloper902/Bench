# Engine design: multi-terminal devices (task 2)

Scope: `benchcore/src/validate.rs`, `benchcore/src/components.rs`, `app/electrical.py`.
Every ngspice claim below was re-run on ngspice 47 (`ngspice -b`) on 2026-10-10, not taken from the spike.

## Verified facts (they drive the rules)
| Deck | ngspice 47 | Validator today |
|---|---|---|
| CE: `Q1 c b 0 q2n2222` + card | v(c)=3.38865, Ic=1.61135 mA, Ib=10.0990 uA (`.op` and `.tran uic` agree) | clean |
| `Q1 c 0 q2n2222` (2 nodes) | "incomplete or empty netlist" | DANGLING warn on node `q2n2222` only |
| `M1 d g 0 NM` (3 nodes) | same opaque error | nothing |
| `Q1 c b 0 qnope` / `D1 a 0 dnope` (no `.model`) | "can't find model", run aborted | nothing |
| `Q1 c b 0 0 q` (substrate) / `Q1 c b 0 q 2` (area) | both fine; floating substrate settles to v(c) | - |
| BJT base open or cap-only | solves (junctions + gmin), v(b)=0.19 V | DANGLING warn if unwired |
| JFET gate open | solves (gate junctions), v(g)=0.12 V | DANGLING warn |
| MOSFET gate open or cap-only | **singular matrix "check node g"**, gmin stepping fails (.op and .tran) | nothing (all-pairs clump) |
| MOSFET bulk open | solves (bulk junctions) | - |
| `X1 n 0 0 foo` against `.subckt foo a b` | aborted | OPAQUE-X warn only |

So the spike's point (c) is half right: the all-pairs clump is *correct* for Q and J (ngspice
really conducts through the junctions), and wrong only for the MOSFET gate.

Deck order: `Simulation._deck()` emits devices **before** `.model` lines, so model/subckt names
must be collected in a pre-pass over the joined lines, not on the fly.

## 1. Node-count table (`parse_netlist`)
"Positional tokens" = `parts` up to the first param (`W=2u`, or spaced `W = 2u`).
`models` / `subckts` = pre-pass sets (lower-case) from every top-level `.model <name>` and every
`.subckt <name> <ports..>` line in the deck. A `.model` inside a `.subckt` body is local to it in
ngspice, so it is not collected.

| Letter | Nodes | Model token | MALFORMED when |
|---|---|---|---|
| D | 2 (a k) | `parts[3]` | unchanged (`parts.len() < 4`) |
| Q | 3 (c b e) to 5 (+ substrate, thermal node) | `pos[n+1]` | `pos.len() < n + 2` |
| J | 3 (d g s) | `pos[4]` | `pos.len() < 5` |
| M | 4 (d g s b) to 7 (SOI nodes) | `pos[n+1]` | `pos.len() < n + 2` |
| X | `pos.len()-2` (last positional = subckt name) | - | `pos.len() < 3`, or name in `subckts` and node count != its port count |

Optional-node disambiguation (mirrors ngspice, which reads the required nodes and then takes the
first token that names a model): `n` = the smallest count from min to max whose next token is in
`models`; otherwise min. With the model only in an `.include`d file this falls back to min nodes
(optional nodes ignored, no false error).

On a short line (`pos.len() < n + 2`) the MALFORMED message names a model token found in a node
position ("model name 'nm' in a node position; M needs 4 nodes + model"). This catches
`M1 d g s NM W=2u L=1u`. A full-length line with a node that shares a model's name is not flagged:
ngspice keeps node and model names apart.
Message format follows the existing arms: `"{name}: Q needs 3 nodes (c b e) + model"`. Severity
`error`, layer `authoring` (same as the R/C/L/D arms). On MALFORMED push the device with
`nodes: vec![]` so a model name never becomes a DANGLING node (noise on an already-blocked deck).

## 2. UNDEFINED-MODEL (new)
`code "UNDEFINED-MODEL"`, `error`, layer `authoring`, `devices [name]`, `nodes` = the device nodes,
message `"{name}: model '{m}' has no .model card in this deck (ngspice aborts the run)"`.
- Applies to D, Q, J, M only (model mandatory in ngspice; R/C/L model tokens are optional, not checked).
- Skipped for the whole deck if any line starts with `.include`, `.inc` or `.lib` (case-insensitive):
  external libraries are out of scope, never guessed.
- Not applied to X (subckt libraries are out of scope; OPAQUE-X already warns).
- No built-in model names exist in ngspice for these letters (verified: any undefined name aborts),
  so there is no allowlist to maintain.
- Not run on MALFORMED devices (one finding per root cause).

## 3. DC conduction (the `adj` block, ~691-711)
Rule: a multi-terminal device connects **all its conducting terminals** to each other. Reachability
is all the BFS uses, so per-junction edges (b-e, b-c, c-s) are equivalent to a clump.
- Q: all nodes (b-e, b-c junctions; substrate ties to c). Unchanged.
- J: all nodes (g-d, g-s junctions). Unchanged.
- M: d, s, b only; **gate (index 1) gets no edge.** Floating/cap-only gate -> existing `NO-DC-PATH`
  error (matches the ngspice singular matrix). Edit: skip `i == 1 || j == 1` when `kind == 'M'`.
- X: move into the same all-pairs branch (today only `nodes[0]-nodes[1]` connect, a latent
  NO-DC-PATH false positive on port 3+). Opaque devices are assumed to conduct.
- NO-DC-PATH message: "(capacitors/current-sources/MOSFET gates are open at DC)".

Floating BJT base / JFET gate: **no new code.** ngspice solves them; an unwired pin already gets
`DANGLING` warn. A base driven only through a capacitor is not flagged (ceiling, see risks).

Interactions: `short_edge`/V-LOOP untouched (no transistor is a short). ISRC-OPEN unchanged: a gate
driven only by an I-source is not "only current sources" (the M terminal is incident), so it
reports NO-DC-PATH, which is right. DANGLING counts nodes as parsed, so it improves for free
once MALFORMED devices carry no nodes.

## 4. `components.rs` (not implemented)
**Not done, on purpose:** no `Component::Q/J/M` variants were added. No Rust caller uses `Component`
(grep `Component::` outside the file: none; see `docs/milestone/01-audit.md` R9), and the app emits
SPICE lines from `app/electrical.py`. The proposal below is kept for reference only:
```rust
Q { name: String, c: String, b: String, e: String, model: String },
J { name: String, d: String, g: String, s: String, model: String },
M { name: String, d: String, g: String, s: String, b: String, model: String },
```
`line()`: `"Q{name} {c} {b} {e} {model}"` etc. `model` may carry trailing params (`"NM W=2u L=1u"`),
as `D` does. No substrate field (YAGNI). Update the module doc line "(R, C, L, V, I, D)".

## 5. `app/electrical.py`
- `DEVICE_LETTERS = "RCLDVIQJM"`.
- Replace `len(pids) != 2` with `PINS = {"Q": (3, 4), "J": (3,), "M": (4,)}`, default `(2,)`;
  message `"a Q device has 3 or 4 terminals; this part has N pins"`.
- Node check: `n = len(pids)`; `toks = tpl.split(None, n + 1)`; `nodes, tail = toks[1:n+1], (toks + [""])[n+1]`.
  Keep the sorted() set comparison (order is the author's business; SPICE order c b e is in the template).
- Current signal pin: the pin in the template's first node field (`line.split()[1][1:-1]` of the
  resolved line in `build_netlist`). Identical to `pins[0]` for every shipped part (all list pins in
  line order), and gives c for `@q1[ic]`, d for `@m1[id]`/`@j1[id]`.
  For Q/J/M, `check()` additionally requires `current` to be `@{dev_lc}[ic]` (Q) or `@{dev_lc}[id]`
  (J/M), so the attribution is always node 1. `ib`/`ie` signals: skipped, add with a key->node map.
- Across voltages: keep `len(pins) == 2` only. Node voltages already cover v(c), v(b); no V(Q1).
- `CURRENT` regex already accepts `@{dev_lc}[ic]`; `build_netlist` already emits
  `.save all @q1[ic]` for `@` vectors. The kernel keeps refusing an unsaved `@q1[ic]` loudly (no change).

## Backward-compat risks
1. `adder.rs::t32_mos_nand_truth_table`: every gate is driven by a PWL V source and every bulk
   reaches 0 or vdd through d/s, so the gate rule does not fire. Must stay green.
2. Decks with D/Q/J/M whose card arrives via `.include`/`.lib`: protected by the skip rule.
3. Decks that put the card only inside a `.subckt` body and use the device at top level:
   UNDEFINED-MODEL, as ngspice rejects them too (the card is local to the subckt).
4. Q/M with optional nodes and an included model: parsed with the minimum node count; an optional
   node's net may get a DANGLING warn.
5. App: unwired MOSFET gate now blocks Run with NO-DC-PATH (correct, previously a singular-matrix failure).
6. Capacitor-only base nodes still run silently (base floats near 0.2 V). Upgrade: a per-device
   "control terminal reachable only through own junctions" check, if it bites.
7. `benchcore/docs/components.md` lists finding codes: add UNDEFINED-MODEL there (task 10).
8. `experiments/exp-safety/validator.py` is a separate Python port; not kept in sync (throwaway).
9. A Gummel-Poon (level 1) Q given a 5th (thermal) node passes the validator but ngspice rejects it
   (only VBIC/HICUM have that node). Not checked: it would need model levels.

## Task 3 (Rust) edit list
1. `validate.rs`: pre-pass in `parse_netlist` after joining lines -> `models: HashSet<String>`,
   `subckts: HashMap<String, usize>`, `has_include: bool`.
2. Replace the `'Q' | 'J'`, `'M'`, `'X'` arms per section 1; add the model-in-node MALFORMED.
3. UNDEFINED-MODEL per section 2 for D/Q/J/M (D arm: model = `parts[3]`).
4. `adj` block: M gate exclusion, X into the all-pairs branch, NO-DC-PATH message tweak.
5. `components.rs`: section 4 (dropped).
6. Rebuild + reinstall the wheel (CLAUDE.md command) before task 4/5 runs.

## Task 4 (tests) with assertions
Rust, `benchcore/tests/correctness.rs` (via `benchcore::validate::validate` and `run_simulation`):
- `validate`: `Q1 c 0 q2n2222` + card -> codes contain `MALFORMED`, no `DANGLING` on `q2n2222`.
- `M1 d g s NM W=2u L=1u` + NM card -> `MALFORMED` ("node position").
- `Q1 c b 0 qnope`, no card -> `UNDEFINED-MODEL`; same deck plus `.include x.lib` -> no `UNDEFINED-MODEL`.
- `D1 a 0 dnope` -> `UNDEFINED-MODEL`; `D1 out 0 DLED` with card after it -> clean (order independence).
- `Q1 c b 0 0 q2n2222` -> clean, 4 nodes (no DANGLING on `0`); `Q1 c b 0 q2n2222 2` -> clean.
- MOS floating gate `Vdd vdd 0 5 / Rd vdd d 10k / M1 d g 0 0 NM` -> `NO-DC-PATH` with nodes `["g"]`;
  same with `C1 g 0 1p` -> still `NO-DC-PATH`; with `Vg g 0 2` -> clean.
- MOS floating bulk `M1 d g 0 bb NM` (+Vg) -> no NO-DC-PATH on `bb`.
- BJT floating base `Rc vcc c 1k / Q1 c b 0 q2n2222` -> no `error`; `DANGLING` warn on `b`.
- `X1 n 0 0 foo` with `.subckt foo a b` -> `MALFORMED`; `X1 a b c foo3` (3-port subckt, port c
  only on X1 plus `C1 c 0 1n`) -> no NO-DC-PATH on `c`.
- Existing `t32_mos_nand_truth_table` unchanged and green.

Python, `benchcore/tests/python/test_api.py` (CE amp, `.tran` uic as the app runs it):
```python
s = Simulation("ce").build_circuit(["Vcc vcc 0 DC 5", "Rb vcc b 430k", "Rc vcc c 1k", "Q1 c b 0 q2n2222"])
s.add_model(Q2N2222).add(".save all @q1[ic] @q1[ib]").configure(tstep=1e-6, tstop=1e-4)
s.observe("v(c)", "v(b)", "@q1[ic]", "@q1[ib]"); assert s.validate() == []; s.run()
vc, vb, ic, ib = (s.observable(n)[-1][1] for n in ("v(c)", "v(b)", "@q1[ic]", "@q1[ib]"))
assert abs(vc - 3.389) < 0.01 and abs(ic - 1.611e-3) < 1e-5 and abs(ib - 10.10e-6) < 0.05e-6
assert abs(ic - (5 - vc) / 1e3) < 1e-7      # KVL on Rc, not Bf*Ib
assert abs(ib - (5 - vb) / 430e3) < 1e-9    # KVL on Rb
```
`Q2N2222` = the standard card (IS=14.34f BF=255.9 VAF=74.03 ... RB=10) that task 7 ships; measured
here: v(c)=3.388643, v(b)=0.657439, Ic=1.611357e-3, Ib=1.009898e-5. Plus: without the `.save` line,
`run()` raises `InvalidConfigError` matching `"@q1\[ic\]"` (verified: "not available ... or not saved").

## Task 5 (`app/electrical.py`) edit list
1. `DEVICE_LETTERS`, `PINS` table, pin-count message (section 5).
2. N-node split in the line-template check.
3. Q/J/M `current` restriction to `[ic]`/`[id]`.
4. `build_netlist`: current signal `pins` = first node field of the resolved line.
5. Tests (`app/tests/test_catalog.py` style): 3-pin Q passes, 2-pin Q fails with the pin-count
   message, 3-pin M fails, a Q template missing `{e}` fails the node check, `current = "@{dev_lc}[ib]"`
   on Q fails; `test_components` golden lines unchanged.
