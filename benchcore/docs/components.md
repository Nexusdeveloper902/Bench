# Components and models

## Representation

`components.rs`: structural devices (R/C/L/V/I/D/B/K/T) + `.model` cards +
subcircuit instances. A deck is title + device lines + models + one `.tran`
+ `.end`. Devices describe electrical behavior; nothing pokes solver state.

## Supported in v1 (bounded-full)

- Primitives R/C/L (with `IC=`), independent V/I (DC/PULSE/PWL/SIN), diode D
  with `.model`, mutual K, lossy T, behavioral B (V=/I=).
- Subcircuits: `.subckt` definitions + `X` instances. Bodies are opaque to
  the top-level validator (fixed vs prototype leakage).
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

Pre-sim gates (error): NO-DC-PATH, ISRC-OPEN (D4 1-ohm silent class),
V-LOOP, NO-GROUND, SHORTED-VSRC, NEG-R/CL, ZERO-CL, DUP-NAME, MALFORMED,
B-SELFREF (V= only; Bi self-read is a legal conductance), B-IMPLICIT-NODE,
B-DIVZERO. Warns (proceed with review): DANGLING, SHORTED-PASSIVE, ZERO-R,
BAD-VALUE, B-DDT, B-TIME-IN-OP, B-CYCLE, OPAQUE-X, EXOTIC-DEVICE.
DC model: R/L/V/E/Bv/H/D/Q/M/J conduct; C/I/G/Bi/F open; X optimistic +
warn. First line is the title and is skipped; `+` continuations joined;
`.control` and `.subckt` bodies skipped.

## What is NOT in v1

OSDI compile+load as first-class (fenced), XSPICE code models on pause
paths (fenced), full package marketplace semantics, thermal/noise variants.
Rationale: evidence covers primitives → subcircuits → B-sources; OSDI pause
preservation is inferred-but-untested, so it stays fallback-only.
