# Multi-terminal devices and the 2N2222 (plan)

## Findings (what is actually 2-terminal)
- `benchcore/src/validate.rs`: `parse_netlist` reads exactly 2 nodes for R/C/L/D/V/I/B. Q/J/M already have arms (lines ~384-400) and count as DC paths, but are not tested end to end.
- `benchcore/src/components.rs`: the `Component` builder enum has no Q/M/J variant.
- `app/electrical.py`: `DEVICE_LETTERS = "RCLDVI"`; the line check reads only `split(None, 3)[1:3]` (2 nodes); `len(pids) != 2` rule for letters; `V(part)` across-voltage only for 2 pins; the current signal is attributed to `pins[0]`.
- `app/catalog.py`, `app/model.py`, the canvas: pins are already lists, so 3+ pins should work. The generic symbol/layout needs checking for 3 pins.

## Tasks, routed by effort
| # | Task | Model | Why |
|---|------|-------|-----|
| 1 | Spike: netlist `Q1 c b e 2n2222` through benchcore. Confirm validator findings, `.save @q1[ic]`, convergence | sonnet | Small and exploratory, but touches the engine |
| 2 | Engine design: per-letter node-count table in `validate.rs` (Q=3, J=3, M=4, X=n), MALFORMED on wrong count; floating-node/DC-path rules for the base/gate | **opus** | Cross-cutting validation semantics; a wrong rule gives false errors or misses real ones |
| 3 | Implement #2 in Rust + `components.rs` Q/J/M variants | sonnet | Mechanical once designed |
| 4 | Rust tests: `benchcore/tests` (parse, malformed, floating base) and python `test_api.py` (CE amp op point) | sonnet | Straightforward |
| 5 | `app/electrical.py`: add `QJM` to `DEVICE_LETTERS`, node count = `len(pids)`, pin-order check for N nodes, per-letter pin counts, current signal on the right pin | sonnet | Small, well-bounded |
| 6 | Canvas/symbol: BJT symbol SVG, 3-pin layout, hit-testing, wiring | sonnet | UI, verified by existing canvas tests |
| 7 | `app/library/electrical/q_2n2222/` (`component.toml`, SVG, `.model` card, limitations) | haiku | Declarative data, following docs/adding-components.md |
| 8 | Tests: catalog validation, golden netlist, analytic bias (Ic = beta*Ib, Vce), persistence round-trip, GUI place/wire | sonnet | Needs analytic reasoning, not deep design |
| 9 | Generalisation proof: throwaway MOSFET (4-pin M) and PNP defs in tests only | sonnet | Proves the design without shipping parts |
| 10 | Docs: adding-components (multi-terminal rules), frontend docs, README parts list | haiku | Prose from finished code |
| 11 | Review: rust-reviewer + python-reviewer agents, fix findings | opus | Judgement-heavy |
| 12 | Push, open PR, watch CI, fix failures | haiku, escalate to sonnet on red CI | Mechanical |

Budget: opus on #2 and #11 only. Fallback when a sonnet task fails twice: opus.
