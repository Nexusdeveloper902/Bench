# Components and models

## Representation

`components.rs`: structural devices (R/C/L/V/I/D/B/K/T) + `.model` cards +
subcircuit instances. A deck is title + device lines + models + one `.tran`
+ `.end`. Devices describe electrical behavior; nothing pokes solver state.

## Supported in v1 (bounded-full)

- Primitives R/C/L (with `IC=`), independent V/I (DC/PULSE/PWL/SIN), diode D
  with `.model`, mutual K, lossy T, behavioral B (V=/I=).
- Subcircuits: `.subckt` definitions (nested ones too) + `X` instances, with
  `params:`. The validator analyses every body an instance reaches
  ("Subcircuits" below).
- Params `{P}` and scale suffixes via `parse_value` (`meg` longest-match;
  bare `m` is milli -- refused as ambiguous in thin-authored values).

## Thin behavioral layer (`thin.rs`)

Allowlisted AST → B-source text. Allowlist (`CALLS_1TO1`) is the intended
growth surface. Rejected pre-sim with guidance: `gauss` (segfault), `limit`
(silently wrong → use `clamp`), `pwr` (sign trap → use `pow`), `idt/sdt`
(use C + source), `if/table/poly/stp/atan2/TEMP/white`, literal div-by-zero,
`exp` of huge literals, `ln/sqrt` of invalid literals, self-referencing
voltage outputs, unknown nodes/params/senses. Lowered lines carry
`* [thin:tag]` trace comments. Output is audited for control/shell escapes.

## Validation (`validate.rs`)

Every finding has a `class` (`class_of`, the PyO3 dict key) and a `scope`:
`""` top level, `.subckt <name>` a definition (reported once, whatever the
instance count; nested names are `outer/inner`), or an instance path such as
`x1.xa` (connectivity and parameters, per instance).

| class | codes | severity |
|---|---|---|
| syntax | MALFORMED, DUP-NAME, SUBCKT-UNBALANCED | error |
| dependency | UNDEFINED-MODEL, UNDEFINED-SUBCKT, UNDEFINED-PARAM, UNDEFINED-ELEMENT, UNKNOWN-INSTANCE-PARAM, DUP-SUBCKT, DUP-MODEL, SUBCKT-RECURSION | error |
| connectivity | NO-GROUND, NO-DC-PATH, ISRC-OPEN (D4 1-ohm silent class), V-LOOP, SHORTED-VSRC; DANGLING, SHORTED-PASSIVE | error; warn |
| unsupported | UNSUPPORTED, UNKNOWN-KIND | error |
| limited | LIMITED-ANALYSIS, OPAQUE-X | warn |
| value | NEG-R/CL, ZERO-CL, B-SELFREF (V= only; Bi self-read is a legal conductance), B-IMPLICIT-NODE, B-DIVZERO; ZERO-R, BAD-VALUE, B-DDT, B-TIME-IN-OP, B-CYCLE | error; warn |

`benchcore.validate(deck)` (Python) runs the checks without a simulation
session. The first line is the title and is skipped; `*` lines and `;`, ` $`,
` //` comments are dropped before `+` continuations are joined; `.control`
blocks are skipped. A clean result proves syntax, references and DC topology,
never electrical well-posedness.

### Subcircuits

ngspice 47 semantics, each probed (`docs/plans/ngspice-library/HANDOFF.md`,
task 4):

- The deck is read into a tree of definitions (depth-correct `.subckt` /
  `.ends`, so nested definitions are local to their body).
- Only definitions an instance reaches are analysed: ngspice ignores the
  others, even malformed ones. Each reached definition is checked once (scope
  `.subckt <name>`): every element against the device table, the port list
  (twice-listed port: DUP-NAME; a port named `0`/`gnd` is global ground, so the
  outside net is left unconnected: MALFORMED), the header params (declared
  twice: DUP-NAME, ngspice uses the last).
- Models and subckts resolve lexically: the body, then enclosing bodies, then
  the top level; a local definition shadows the outer one. A card or `.subckt`
  of the instantiating body is not visible to a top-level definition it uses.
  Missing: UNDEFINED-MODEL / UNDEFINED-SUBCKT. Port count: MALFORMED. An
  instance param the `.subckt` does not declare: UNKNOWN-INSTANCE-PARAM (ngspice
  drops it silently). A `.subckt` or `.model` defined twice in one scope with
  different text: DUP-SUBCKT / DUP-MODEL (ngspice keeps the first). An instance
  chain that reaches its own definition: SUBCKT-RECURSION.
- Params resolve dynamically, per instance chain, as numparam does: an
  expression may read the params, `.param`s and `.func`s of its own definition
  and of every instance above it, top-level `.param`s, and the builtins (`FUNCS`,
  `CONSTS`, probed). Anything else in `{...}`, `'...'`, a B expression or an E/G
  `VALUE=` is UNDEFINED-PARAM (ngspice aborts the circuit). The contents of
  `v()`/`i()` are node and source names, not params.
- K, F, H and W name elements of the same body: missing, UNDEFINED-ELEMENT; K
  naming a non-inductor, or F/H/W naming anything but a V, E, H or B source,
  MALFORMED (ngspice 47 segfaults on K coupling a resistor).
- Connectivity runs on the flattened circuit: ports map to the instance's nets,
  `0`, `gnd` and `.global` nodes stay global, other nodes become `x1.xa.n2`. So
  an internal node needs a DC path through the body or a port, and a winding
  with no galvanic path to the rest is fine as long as something outside grounds
  it (ngspice: singular matrix otherwise). No body needs a ground of its own.
  A node with only capacitors stays NO-DC-PATH, as at top level (stricter than
  ngspice under `uic`, which solves it when the rest of the circuit fixes it).
- A B with `I=` or a G that reads the voltage across its own output is a DC
  conductance there (`I=V(a,b)/1k`, `G1 a b a b 1m`); otherwise it is a current
  source and gives no DC path.
- Flattening stops at 100 000 elements (LIMITED-ANALYSIS).

LIMITED-ANALYSIS (warn) names one element or directive per finding, once per
definition: XSPICE `A` devices (ports, domains and code-model parameters are
ngspice's), transmission lines T O U Y P (delays and losses), B expressions,
E/G `VALUE=`/`vol=`/`cur=`/`TABLE` expressions, a model level that is not a
literal number, a K/F/H/W reference with a `.` (across a subckt boundary), a
directive other than `.model` `.param` `.func` `.subckt` `.ends` inside a body.
OPAQUE-X (warn): an X whose `.subckt` is not in the deck while an `.include` or
`.lib` may supply it; with no include it is UNDEFINED-SUBCKT.

### Device table

`DEVICES` holds one row per element letter ngspice 47 accepts: node count
(min/max), what ends the node list (`Tail`: value, source spec, `V=`/`I=`,
E/G forms, controlling-source name, model token, `key=value`, XSPICE ports,
subckt), accepted `.model` types, DC edges (`Dc`) and whether static checks
cover the letter fully (LIMITED-ANALYSIS otherwise). `LEVELS` lists the model levels this build has, per
type (probed one level per process). The parser dispatches on the row, not the
letter.

| letters | nodes | ends with | model types | DC edges |
|---|---|---|---|---|
| R C L | 2 | value | - | R, L: pair; C: none |
| V I | 2 | source spec | - | V: pair; I: none |
| B | 2 | `V=` / `I=` | - | pair with `V=`; with `I=` only when it reads its own output (limited) |
| E G | 4 linear, 2 for `VALUE=`/`vol=`/`cur=`/`TABLE`, 2+2n for `POLY(n)` | gain / expression | - | E: output pair; G: pair only when it reads its own output (expressions limited) |
| F H | 2 | V-source name + gain, or `POLY(n)` | - | H: pair; F: none |
| K | 0 | two inductor names + k | - | none |
| D | 2 | model | D (1, 3) | pair |
| Q | 3-5 | model | NPN PNP (1, 2, 4, 8, 9) | all pairs |
| J | 3 | model | NJF PJF (1, 2) | all pairs |
| M | 4-7; VDMOS 3, or 5 with `thermal` | model | NMOS PMOS (see `LEVELS`), VDMOS | all pairs but the gate |
| Z | 3 | model | NMF PMF (1-6) | all pairs |
| S | 4 | model | SW | switch pair |
| W | 2 | V-source name + model | CSW | switch pair |
| T | 4 | `Z0=` `TD=` | - | port pairs (limited) |
| O Y | 4 | model | LTRA, TXL | port pairs (limited) |
| U | 3 | model | URC | pair (limited) |
| P | 4+ | model | CPL | all pairs (limited) |
| A | port lists | model (code model, checked by ngspice) | any | all pairs (limited) |
| X | ports of its `.subckt` | subckt name, then `name=value` params | - | flattened; all pairs + OPAQUE-X only when the `.subckt` is not in the deck |

MALFORMED (error): a line too short for its row; a model of the wrong type for
the letter; more nodes than the model's level takes (Q Gummel-Poon 4, VBIC/HICUM
5; M non-SOI 4, SOI 7); a VDMOS with other than 3 nodes (or 5 with `thermal`); a
3-node M whose model is not VDMOS. Like ngspice, the model is the first token
after the required nodes that names a `.model` card. A MALFORMED device carries
no nodes, so it adds no DANGLING noise.

UNSUPPORTED (error, with the reason): a model level this build lacks (D 2;
Q 3, 5, 6 Mextram, 7, 10+; J 3+; M 7, 60 SOI3 which aborts the process, and the
other gaps in `LEVELS`; Z 7+), PSpice model types (`VSWITCH`, `ISWITCH`, `LPNP`),
E/G `LAPLACE` (aborts the engine), `if()` / `limit()` / `table()` calls (abort,
or silently wrong for `limit`), and N (no OSDI models in this build).

UNDEFINED-MODEL (error): a model-backed element whose model name has no
`.model` card in its scope chain (ngspice aborts the run). A card inside a
`.subckt` body is local to that body and the bodies nested in it. Skipped for
the whole deck when any line is `.include`, `.inc` or `.lib` (as UNDEFINED-SUBCKT).

An unwired or capacitor-only MOSFET gate is NO-DC-PATH (the gate has no DC
edge); a BJT base or JFET gate is not, since ngspice conducts through its
junctions.

## Engine robustness (`ngspice.rs`, `kernel.rs`)

- Controlled exit: when ngspice calls the exit callback ("cannot recover and
  awaits to be reset or detached": an undefined `{param}`, LAPLACE, ...), the
  call returns `BackendFailure` naming ngspice's error lines, and the library is
  unloaded (dlclose) and re-initialised on the next call. Later runs succeed.
- Every `stderr Error...` log line, and the parse messages ngspice prints with
  rc 0 (`unknown subckt`, `parameters for subcircuit type`, `instance line,
  ignored`, `Unknown model type`), is a `SolverFailure`. A transient that yields
  no time grid is a `SolverFailure`, never `ok` with 0 points. `circ rc=N`
  errors carry ngspice's first error lines.
- `unset ngbehavior` runs before every circuit, so a `./.spiceinit` or
  `~/.spiceinit` cannot switch the deck into PSpice/LTspice compatibility. The one exception is a deck that
  carries the comment line `* bench-compat: ps`: then `set ngbehavior=ps` follows the unset, for that circuit only.

## What is NOT in v1

OSDI compile+load as first-class (fenced; N is UNSUPPORTED), XSPICE code models on pause
paths (fenced: A decks fall back to full simulation), full package marketplace semantics, thermal/noise variants.
Rationale: evidence covers primitives → subcircuits → B-sources; OSDI pause
preservation is inferred-but-untested, so it stays fallback-only.
