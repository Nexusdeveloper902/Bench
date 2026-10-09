# Persistence

Implemented in `app/model.py` (`Document.to_dict` / `from_dict` / `save` / `load`). Plan: `docs/milestone/02-plan.md` section 8.

## Format v2 (written)

```json
{"format": "sim-circuit", "version": 2,
 "components": {"R1": {"type": "R", "version": 1, "params": {"R": "330"}},
                "U1": {"type": "ESP32_DEVKITC", "version": 1, "params": {}}},
 "connections": [["R1.a", "U1.3V3"]],
 "simulation": {"analysis": "transient", "tstop": "5m", "tstep": "10u"},
 "layout": {"R1": {"x": 180, "y": 60, "rotation": 0}, "U1": {"x": 600, "y": 40, "rotation": 90}}}
```

- `type` is the exact catalog id, `version` the definition version it was saved with, `params` the text as typed.
- Pins are not stored; `connections` name them (`Part.pin`). `layout` is rendering data only.
- Simulation support is not stored: placing, wiring and saving work for every part, including interface-only ones.

## Reading

| file | handling |
|---|---|
| v0 (no `version`, has `parts`) | migrated in memory, treated as v1, rotation 0 |
| v1 | every component gets `version` 1 (v1 only ever held version-1 built-ins) |
| v2 | `version` required on each component |
| `version` > 2 | refused: "newer than this app supports" |

Migration never rewrites the file; it is rewritten only when the user saves (always as v2).

Whole-file errors (`ModelError("bad simulation file: ...")`): bad JSON or types, bad names or layout, a connection
naming a part that does not exist, duplicate or self connections, a non-transient analysis, `format` missing.

## Components without a usable definition

For each component, with `d` the catalog definition for `type` (exact id match; aliases are never resolved):

1. No `d`: placeholder, reason `missing`.
2. Saved `version` > `d.version`: placeholder, reason `incompatible`.
3. A param key not in `d`, or a connection to a pin not in `d`: placeholder, reason `incompatible`.
4. Otherwise a normal part; missing params take defaults and the stored version becomes `d.version`.

A placeholder keeps `type`, `version` and `params` verbatim (no coercion) and its position and rotation. Its pins
are the sorted pin names its connections use. It can be moved, rotated, deleted and wired to those pins, its params
are read-only, and it survives undo/redo and save. Saving writes its record back unchanged (a load/save cycle of a
file written by this app is byte-identical). It is never simulated: see below.

## Simulation and unsupported parts

`Document.validate()` reports every part without `sim:electrical` (interface-only definitions and placeholders):
an error if any of its pins is wired (`NOT-SIMULATED`, `MISSING-DEFINITION`, `INCOMPATIBLE-DEFINITION`; Run is
`blocked`), a warning if it is unwired (the part is excluded; `RunResult.excluded` and the success message
`not simulated: U1` name it). `Document.netlist()` itself raises for a wired unsimulated part.

## Compatibility rules

- Within a version changes are additive only: new component types, new params with defaults, new optional keys.
  Unknown extra keys in the file, in a component record or in a layout entry are ignored (and not preserved).
- Bump the file `version` for anything else; the reader keeps reading older versions.
- Definition `version` (integer): bump when a pin or param is removed or renamed, or a param changes meaning. Adding a
  param with a default does not bump it (`docs/milestone/02-plan.md` D19).
