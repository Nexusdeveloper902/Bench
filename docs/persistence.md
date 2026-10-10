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
2. Saved `version` != `d.version` (older or newer): placeholder, reason `incompatible`; the reason states the saved and
   the current version. A version bump is a breaking change (D19), so an older file is never silently reinterpreted
   (a part saved as 10 with a µF line must not become 10 F). There is no automatic upgrade.
3. A param key not in `d`, or a connection to a pin not in `d`: placeholder, reason `incompatible`.
4. Otherwise a normal part (same version); params added later with a default take their defaults.

A placeholder keeps `type`, `version` and `params` verbatim (no coercion) and its position and rotation. Its pins
are the sorted pin names its connections use. It can be moved, rotated, deleted and wired to those pins, its params
are read-only, and it survives undo/redo and save. Its pin list is fixed when the file is opened: history snapshots
carry it, so undoing an unrelated edit never drops a pin whose wire was deleted (the pin list is not written to the
file). Saving writes its record back unchanged (a load/save cycle of a file written by this app is byte-identical). It is never simulated: see below.

## Shadowed definitions

If two packages define the same `id` (`DEF-DUP-ID`), the first root wins and the part binds to it. A saved part of that
type gets a `SHADOWED-DEFINITION` warning in Problems naming the part, the id and every definition path, so a file is
never silently re-bound to a different component.

## Simulation and unsupported parts

`Document.validate()` reports every part without `sim:electrical` (interface-only definitions and placeholders):
an error if any of its pins is wired (`NOT-SIMULATED`, `MISSING-DEFINITION`, `INCOMPATIBLE-DEFINITION`; Run is
`blocked`), a warning if it is unwired (the part is excluded; `RunResult.excluded` and the success message
`not simulated: U1` name it). `Document.netlist()` itself raises for a wired unsimulated part.

## Compatibility rules

- Within a version changes are additive only: new component types, new params with defaults, new optional keys.
  Unknown extra keys at the top level, in a component record or in a layout entry are kept and written back
  unchanged on save (in v2 they are appended after the known keys), so a newer writer's additive keys survive an
  older app. Unknown keys inside `simulation` and in v0 files are not kept.
- Bump the file `version` for anything else; the reader keeps reading older versions.
- Definition `version` (integer): bump when a pin or param is removed or renamed, or a param changes meaning. Adding a
  param with a default does not bump it (`docs/milestone/02-plan.md` D19).
