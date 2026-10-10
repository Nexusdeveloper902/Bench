# Task 11: architecture review (adversarial)

Date: 2026-10-10. Branch `milestone/component-ecosystem` at `54a2e0c`, diff `1b2c335..HEAD -- app docs`.
Inputs: `00-brief.md`, `02-plan.md` (decisions, §12 amendments, T5/T6 notes), `docs/persistence.md`,
`docs/adding-components.md`. This was a read-only review. No repo file was changed except this report. All
experiments ran from the session scratchpad against the real app, the real catalog and real ngspice.

Baseline: `QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests` gives **388 passed**. `python3 -m app.catalog`
gives **16 definitions, 0 errors, 0 warnings**.

## 1. Primary question: is a future-domain part handled by the same mechanism as an electrical part?

**Verdict: yes, with three qualifications.**

The two kinds of part go through one path:

- discovery: `catalog.reload` and `rglob`;
- validation: `load_definition`, plus `SIM_DOMAINS` only for `[sim.*]`;
- one `Definition` type;
- the library card (`Card(defn)`) and the category nav (`CATALOG.categories()`);
- placement (`MainWindow.place` and `Document.add_part`);
- the inspector form (`Definition.fields` and `Param`);
- persistence (`to_dict` / `from_dict` / `_resolve`);
- the run pre-check (`Document.unmodeled`, `validate`, `netlist`).

No UI module branches on a kind id, a category or a domain. `grep` over `window/canvas/library/inspector/symbols/widgets/results/sim` finds only:

- the examples and `load_demo`, which the plan allows;
- branches on `readiness`, `placeholder` and `ground` that come from the declared capabilities.

The differences in treatment come from capabilities, as the brief requires:

| capability | placed | wired | run | badge |
|---|---|---|---|---|
| `sim:electrical` | yes | yes | simulated | "Simulated" |
| `interface` only | yes | yes | wired: Run blocked (`NOT-SIMULATED`). Unwired: excluded and named. | "Interface only" |
| none (placeable) | yes | no pins | excluded and named | "Reference only" |

The qualifications:

1. **Treated differently when it should not be: a part with no symbol loses its declared presentation.** A part drawn
   as the generic body (no `symbol`, which the doc says is optional) never shows its `value` label or its
   `show_current` readout, even though both validate (F5). In practice this hits new electrical parts. Every
   interface-only seed is generic anyway.
2. **The electrical binding is weaker than the catalog.** Validation accepts templates that bypass the canvas
   topology (F2). Two definitions can also silently share one SPICE `.model` name (F1). Both give a "success" run
   over a circuit that is not the one drawn. This is the opposite of "never fake behaviour", although it reaches the
   electrical part rather than the unsupported one.
3. **"Simulated" means electrical, hard-coded in the generic catalog.** `Definition.simulated` and `readiness` test
   for `"sim:electrical"`. `SIM_DOMAINS` registers a validator only. A second domain registered there would still be
   labelled "Interface only" and hidden by "Simulated only" (F12). This is honest, since nothing fake happens. But the
   "registration point" covers validation only.

### 1.1 Empirical run (following only `docs/adding-components.md`)

The scratch root `parts/` held two hand-written packages:

- **`SG90_SERVO`**: category **`Robotics`** (new, not in TAXONOMY), subcategory `Actuators`, `domain = "mechanical"`,
  designator `M`, `capabilities = ["interface"]`, 3 pins (`GND`, `VCC`, `PWM`, with names like "GND (brown)"), one
  limitation, no symbol.
- **`D_ZENER_5V1`**: Electrical/Semiconductors, designator `D`, a `[[params]]` `BV` (unit V, `min_exclusive`),
  `value = "BV"`, `show_current = true`, no symbol, and the following section:
  `[sim.electrical] device = "D"`, `line = "{dev} {a} {k} dz_{dev_lc}"`,
  `models = [".model dz_{dev_lc} D(Is=1n N=1.5 Rs=1 BV={BV} IBV=5m)"]`, `current = "@{dev_lc}[id]"`.

```
$ python3 -m app.catalog $S/parts
18 definitions, 0 errors, 0 warnings
```

The headless driver ran `SIM_COMPONENT_PATH=$S/parts QT_QPA_PLATFORM=offscreen python3 drive.py`. It drives
`MainWindow` with QTest card clicks, canvas clicks and pin-to-pin wire clicks. Output (abridged):

```
categories: ['Electrical', 'Microcontrollers', 'Sensors', 'Displays', 'Audio', 'Power', 'Robotics']
Robotics cards: ['SG90_SERVO']        search 'micro servo': ['SG90_SERVO']     search '1N4733A': ['D_ZENER_5V1']
search subcategory 'actuators': ['SG90_SERVO']   search category 'robotics': ['SG90_SERVO']
servo card badge: Interface only  tile fallback: True     simulated-only has servo? False  zener? True
placed: V1 R1 D1 GND1 M1
inspector servo: 'SG90 micro servo', 'M1 · SG90_SERVO · v1', 'Interface only', pins GND (brown)/GND, +5V (red)/VCC,
                 PWM (orange)/PWM, '• Not simulated: the PWM decoder, ...', 'type SG90_SERVO   version 1'
zener fields: ['BV'] labels: {'BV': 'Breakdown voltage'}
RUN1: success ok, 511 points; not simulated: M1 excluded ['M1']
  I(D1) final = -0.00688073 A, V(D1) final = -5.11927 V        # 12 V, 1 kΩ, reverse zener: (12-5.119)/1k = 6.88 mA
RUN2 servo->GND only: blocked [NOT-SIMULATED] M1 (SG90 micro servo) has no electrical model and is wired ...
RUN3 after undo: success ok, 511 points; not simulated: M1
saved records: {'D1': {'type': 'D_ZENER_5V1','version': 1,'params': {'BV': '5.1'}}, 'M1': {'type': 'SG90_SERVO','version': 1,'params': {}}}
reload placeholders: []   byte-identical resave: True   RUN reloaded: success ...; not simulated: M1
problems panel: ['Warning: M1 (SG90 micro servo) is not simulated and is excluded from the run.  [NOT-SIMULATED]']
rotate -> 90, delete -> gone, undo -> {'kind': 'SG90_SERVO', ..., 'rot': 90}
```

Both parts were discovered, validated, listed, searchable by alias, category and subcategory, placed, wired,
inspected, saved, reloaded byte-identically, and run, with no code change. The new category appeared in the nav
after the TAXONOMY categories.

### 1.2 Where the doc was wrong or insufficient while authoring

- The doc says `category = "Electrical"  # one of the TAXONOMY categories`. The code accepts any string
  (`Robotics` works) and lists unknown categories after TAXONOMY, alphabetically. The doc implies that a new category
  is impossible, and it never says how one is added. That is the brief's "adding a category" requirement. See F11.
- `[sim.electrical]` rules are not in the doc. The allowed keys (`device line models current ground`), the
  `line = {select = ...}` form, the rule that model templates may only use params without `when`, the `current` regex,
  "one SPICE line per part" and the model-name scope are only reachable through "see plan §5". The doc example uses a
  fixed, global model name (`d1n4148`) without warning that this name is shared across the whole netlist. That leads
  directly to F1.
- A part without pins must still write `pins = []`. Omitting the key gives `missing required key 'pins'`. The doc
  never says that `pins` is required, and `[[pins]]` tables cannot express an empty list.
- `python3 -m app.catalog ~/my-parts` reports false errors once the user has set `SIM_COMPONENT_PATH` as the doc
  suggests (F4).
- The doc says "readiness `simulated` (Simulates)", but the UI badge reads "Simulated" (`theme.READINESS`).
- The doc says "Unknown keys anywhere are an error (`DEF-FIELD`)". Unknown keys in `[sim.electrical]` are `DEF-SIM`.
- The doc does not say that a part without a symbol loses `value` and `show_current` on the canvas (F5).

## 2. Findings

Severity: **high** means a success run, or a saved file, that silently differs from what the user built. **Medium**
means a broken documented workflow or silent loss or substitution of user data. **Low** means a robustness,
coupling or documentation gap with no silent wrong result.

| id | sev | file:line | what is wrong | repro (all run, outputs quoted) | suggested fix (smallest, right boundary) | status |
|---|---|---|---|---|---|---|
| F1 | **high** | `app/electrical.py:140` | `.model` lines are deduplicated by exact text only. Two definitions that use the same model **name** with different parameters both emit it, ngspice keeps the first, and every instance of the other part silently runs on the wrong model. The doc example teaches fixed names (`d1n4148`), and LED uses `dled`. | Copy `app/library/electrical/led` to an extra root as `LED_GREEN`, changing only `Is=1e-22 N=2.0`. LED_GREEN alone: `I(D1)=0.0078515`. LED + LED_GREEN: `success ... I(D1)=0.0091772, I(D2)=0.0091772` (green on the red model). Order reversed: both `0.0078515`. Models: `['.model dled D(Is=1e-20 ...)', '.model dled D(Is=1e-22 ...)']`. | In `build_netlist`, key models by `split()[1].lower()`. If a name is already present with different text, raise `ModelError("model 'dled' is defined differently by LED and LED_GREEN")`. Optionally add a catalog-wide check at `reload` for fixed (placeholder-free) model names. State in the doc that model names are global and should use `{dev_lc}` or a unique prefix. | CONFIRMED |
| F2 | **high** | `app/electrical.py:79-84` (`check`) | The template check only requires that every pin placeholder appears *somewhere* in the line. It does not check that pins sit in the node fields, or that only pins do. (a) A literal node (`n1`, `0`) attaches the part to the app's internal nets with no wire on the canvas. (b) A pin placed after a SPICE comment (`;`/`$`) "reaches SPICE" but is not connected. Both validate cleanly and run as `success`. | (a) `TAP`: 1 pin `a`, `line = "{dev} {a} n1 {R}"`, R=10, wired only to GND. Problems `[]`, validate `[]`, deck `RU1 0 n1 10.0`, run `success`, `I(V1) = -0.509 A` vs `-0.00918 A` without it. The battery is loaded through a connection that is not drawn. (b) `POT`: pins a, b, w, `line = "{dev} {a} {b} {R} ; {w}"`, with w wired to R1.b. Deck `RU1 n1 0 100.0 ; n2`, run `success`, so the wiper does nothing. | In `electrical.check`: every `DEVICE_LETTERS` device (R C L D V I) has exactly two nodes. Require exactly 2 pins, require each line branch to start with `{dev} {p} {q} ` with p and q the two pin ids, forbid pin placeholders after that, and reject `;`, `$` and `*` in templates. | CONFIRMED |
| F3 | medium | `app/model.py:312-319, 371-373`, `docs/persistence.md:41` | An older saved definition version loads as a normal part and is silently upgraded: the stored version becomes `d.version` and the next save writes it. D19 and the authoring doc say to bump `version` when "a parameter changes meaning". That bump is never acted on, so a meaning change silently reinterprets old files. persistence.md rule 4 contradicts D19. | `CAPX` v1: param `C` = µF via `line "{dev} {a} {b} {C}u"`, saved with `C = "10"`. Define v2 (same key, now farads). `Document.load` gives no placeholder and `version 2`. Resaved record `{'type':'CAPX','version':2,'params':{'C':'10'}}`. Netlist `CU1 n1 n2 10.0` (10 F instead of 10 µF). | In `_resolve`: `if ver != c.version` gives an `incompatible` placeholder. D19 already makes every bump breaking, and additive params do not bump. A gentler alternative is a `compatible_from` key in the definition. Update persistence.md rule 4. | CONFIRMED |
| F4 | medium | `app/catalog.py:366-388, 442-444`; `docs/adding-components.md:18` | Roots are not deduplicated, and files already seen are not skipped. The documented validator `python3 -m app.catalog ~/my-parts` with `SIM_COMPONENT_PATH=~/my-parts` set (as the same doc recommends) loads the root twice. Every part becomes `DEF-DUP-ID` "already defined by" **itself**, with exit 1. The same happens in the app for `SIM_COMPONENT_PATH=a:a`, or for a root nested inside another. | `SIM_COMPONENT_PATH=$S/parts python3 -m app.catalog $S/parts` prints `error DEF-DUP-ID .../zener_5v1/component.toml [id] id 'D_ZENER_5V1' is already defined by .../zener_5v1/component.toml` and `18 definitions, 2 errors`. | In `reload`, deduplicate `roots` by `resolve()`, and skip a `component.toml` whose resolved path was already loaded. | CONFIRMED |
| F5 | low | `app/canvas.py:506-507` | A generic-body part never gets `_labels`, so its declared `value` and `show_current` (both validated) are never drawn. Parts are treated differently depending only on whether an optional SVG exists. | Same Zener, with and without `symbol = "symbol.svg"`, after a run. Generic body texts: `['1 kΩ','12 V','Anode','Cathode','D1',...]`. With a symbol: `['-6.88 mA','1 kΩ','12 V','5.1 V','D1',...]`. | Draw the value and current rows for generic bodies too (for example under the box). Keep skipping only the name, which the body already prints. | CONFIRMED |
| F6 | medium | `app/model.py:285-291, 334`; `docs/persistence.md:57-58` | The compatibility policy allows newer writers to add optional keys within v2 ("additive only"). This reader drops unknown keys at every level and rewrites the file as v2. An older app therefore silently destroys newer data on save. This includes placeholder records, which the docs promise to keep "unchanged". | Load the LED example with `notes` (top), `components.R1.tolerance` and `layout.R1.locked`, then `to_dict()`: `top 'notes' kept: False record key kept: False layout key kept: False`. | Either keep unknown keys (store the raw extras per record, top level and layout entry, and merge them on `to_dict`), or change the policy so that any new key bumps the file version, which the old reader refuses. | CONFIRMED |
| F7 | medium | `app/catalog.py:383-388`; `app/model.py:309` | Shadowing: a user part saved by id, later shadowed by a built-in (or earlier-root) definition with the same id, reopens silently as the **other** component whenever pin and param ids match. The banner shows `DEF-DUP-ID` for the definition, but nothing says which circuit parts were re-bound. The brief says "never silently substitute". | Roots `[builtin, B]`: `MYPART` "User's 100 ohm shunt", saved. Reload `[builtin, A, B]` with A's `MYPART` "Vendor sensor": `placeholders: [] resolves to: Vendor sensor \| problems: ['DEF-DUP-ID']`. | Keep the set of ids that had a DUP-ID in `reload`. For parts of those types, `from_dict` or `validate` emits a warning ("U1: type X is defined by two packages; using <file>"). Namespacing ids is the larger fix. | CONFIRMED |
| F8 | low | `app/electrical.py:96-99`; `app/model.py:215-219` | SPICE device-name collisions are not pre-checked. `device_name` prefixes the letter (SW1 becomes `RSW1`), so a user part with the valid designator `RSW` (device R) is named `RSW1` and collides. `validate()` passes, the core fails with `[DUP-NAME]`, and the UI has no rename to fix it. | User `SHUNT` with `designator="RSW"` and `SW`: names `['V1','GND1','SW1','RSW1']`, validate `[]`, lines `RSW1 n1 n2 0.01`, `RSW1 n2 0 1000.0`, run `failed [DUP-NAME] duplicate device name 'RSW1'`. | Extend `DUP-NAME-CASE` in `validate()` to compare `device_name(...)`, lower-cased, of the modeled parts. Or have `add_part` skip names whose device name is taken. | CONFIRMED |
| F9 | low | `app/catalog.py:203, 270-273` | `box` and pin `at` are only checked for finiteness. `MAX_COORD` bounds layout but not definition geometry. Huge values break the canvas. | `box = [0,0,1e12,1e12]`, pin at `[1e12,15]`: validates. `place` + `select` + `fit` raise `OverflowError` in `canvas._place_overlays` (`part_bar.move`), from a Qt override. | Bound the coordinates in `load_definition` (for example `abs(v) <= 1e4`, `DEF-FIELD`/`DEF-PIN`). | CONFIRMED |
| F10 | low | `app/library.py:58`, `app/widgets.py:66` (`label`), details and inspector headers | Definition `name`, `description` and pin names go into `QLabel`s with `Qt.AutoText`, so markup in a package renders as rich text, including `<img src=...>` local file loads. Limitations are escaped (`bullets`), but these strings are not. | `name = "<b>Bold</b><img src='/usr/.../firefox.png'>Rich"`: card label `textFormat AutoText`, rendered as rich text. | Call `setTextFormat(Qt.PlainText)` in `widgets.label` by default, and opt into rich text where it is intended. | CONFIRMED |
| F11 | low | `app/catalog.py:180`, `adding-components.md:35` | `category` is free text with no normalisation and no warning. `"electrical"`, `"Communications"` and `""` all load, and each creates its own nav entry (an empty name for `""`). The doc says the value must be "one of the TAXONOMY categories", which is false. | `load_definition` for categories `'electrical'`, `''` and `'Communications'`: all `def=OK []`. | Reject an empty category. Add a `DEF-FIELD` warning when the category differs from a TAXONOMY entry only in case or plural form. Fix the doc: free text, TAXONOMY order first, then alphabetical. | CONFIRMED |
| F12 | low | `app/catalog.py:84-90, 325-327`; `app/canvas.py:558-569`; `app/model.py:24-29` | The electrical domain leaks into the generic catalog and presentation: (1) `simulated` and `readiness` mean `sim:electrical`, and `SIM_DOMAINS` is validator-only. (2) The catalog reads `sim.electrical.current` for `indicator` and `show_current`. (3) `indicator = "glow"` paints an LED triangle at the box centre with LED current thresholds, for any part. None of this fakes behaviour, but a second domain cannot plug in without editing the catalog and the model. | `SIM_DOMAINS["digital"] = lambda *a: []`, part `AND2` with `sim:digital`: `simulated False readiness interface`, hidden by Simulated only. Run: `success ...; not simulated: U1`. | Acceptable for this milestone, given that no other domain exists. Document in the plan or authoring doc that "Simulated" currently means the electrical run, and that a second domain must also extend `Definition.simulated` and `Document.unmodeled`. Draw glow as a halo only, not an LED glyph. | CONFIRMED |
| F13 | low | `app/catalog.py:379` | `rglob` (Python 3.14) does not follow symlinked directories. A package directory symlinked into a root is silently not discovered, with no diagnostic. | Root holds `sympart -> ../elsewhere/sympart`: `discovered? False problems: []`. | `root.rglob("component.toml", recurse_symlinks=True)`. Asset checks already resolve paths against the package. Or emit a warning for a skipped symlinked directory. | CONFIRMED |
| F14 | low | `app/model.py:374-376`, `docs/persistence.md:45` | Placeholder pins are re-derived from connections on every `from_dict`, which history also uses. After a placeholder's wire is removed, an undo of an **unrelated** later action also removes the pin. persistence.md says the placeholder "survives undo/redo". The plan's T6 notes mention the limitation, but the user-facing doc does not. | Missing type `NOPE` wired `X1.p1`: remove the wire (pins `['p1']`), add R, undo: pins `[]`. Params stay verbatim (`{'k': [1, {'z': None}]}`). | Document it in persistence.md. Or carry `placeholder.pins` in the history snapshot only (not the file) by keeping it in `_state`. | CONFIRMED |

Severity counts: **high 2, medium 4, low 8**.

## 3. Checked and held up

- **No per-kind UI code.** No UI module names a kind, category or domain. Cards, nav, badges, inspector fields,
  pins, limitations and docs all come from `Definition`. The new `Robotics` category and the new interface and
  electrical parts needed no code change (§1.1).
- **Unsupported parts never get behaviour.** A wired interface-only part blocks Run (`blocked`, core not called).
  Unwired, it is excluded and named in `RunResult.excluded`, in the success message ("not simulated: M1") and in the
  Problems panel. Every case below was run:
  - interface-only part wired only to ground: blocked;
  - part with zero pins (placeable, `Communication`): `success ...; not simulated: U1`;
  - ESP32 wired only to a DHT22: blocked;
  - placeholder unwired: `success ...; not simulated: X1`;
  - placeholder wired: blocked with `MISSING-DEFINITION`;
  - undo and redo across these.

  `excluded` is always all of `unmodeled()`, so no success path can omit a part. `Document.netlist()` raises if a
  wired unmodeled part slips past `validate`. D7 (wired means blocked, even when the island is unrelated, e.g.
  ESP32 to DHT22) is over-strict but never fake. I agree with keeping it.
- **Net numbering.** Excluded parts never renumber `n1..`, because modeled pins are named first.
- **Persistence.**
  - v0 and v1 migration, version > 2 refused, and JSON duplicate keys rejected.
  - NaN and Inf coordinates rejected, and bad pin refs rejected (`PIN_RE`).
  - Aliases and case variants of an id are never resolved (exact match only).
  - An unknown param or pin gives an `incompatible` placeholder.
  - Placeholder `type`, `version` and `params` (even nested non-strings) are written back verbatim.
  - Resave of an app-written file with a new interface part and a new electrical part is byte-identical.
- **Validation.**
  - Malformed TOML gives `DEF-PARSE`.
  - The TOML key trap gives a hint.
  - Unknown keys are reported.
  - A symlinked `symbol.svg` that points outside the package gives `DEF-FIELD` (realpath check).
  - A billion-laughs SVG is refused by expat in 0.1 s, as a `DEF-ASSET` warning with fallback.
  - A part with 5000 pins loads in 0.4 s.
  - Docs links: only `http(s)` or allowed-extension files whose realpath is inside the package (`widgets.doc_target`).
  - A definition that shadows a built-in is rejected and the built-in is kept (but see F7 for saved files).
  - A bad definition does not block good ones.
- **Template injection.**
  - Placeholders must be identifiers.
  - Lines start with `{dev} `, and models start with `.model `.
  - Newlines are rejected.
  - Numbers go through `repr(float)`, and choices match a regex.
  - Part names match `NAME_RE`.

  F2 is about topology, not code execution.
- **Boundary.** `simcore` is imported only in `sim.py`. The catalog imports `electrical`, which is pure, but never
  ngspice. R6: only typed `simcore` errors are caught.
- **Doc example blocks validate** (`test_doc_examples_validate`), and both of my doc-only parts validated the first
  time.

## 4. Commands used (scratchpad scripts, throwaway)

```
QT_QPA_PLATFORM=offscreen python3 -m pytest -q app/tests                      # 388 passed
python3 -m app.catalog $S/parts ; SIM_COMPONENT_PATH=$S/parts python3 -m app.catalog $S/parts   # F4
SIM_COMPONENT_PATH=$S/parts QT_QPA_PLATFORM=offscreen python3 $S/drive.py      # §1.1 (QTest-driven MainWindow)
python3 $S/collide.py $S/parts2      # F1   python3 $S/literal.py ; python3 $S/pot.py   # F2
python3 $S/persist.py                # F3   python3 $S/devname.py   # F6, F8   python3 $S/shadow.py   # F7
SIM_COMPONENT_PATH=$S/parts:$S/parts3 python3 $S/labels.py   # F5   python3 $S/valid.py ; $S/ui_weird.py   # F9-F11, F13
python3 $S/domain.py                 # F12  python3 $S/fake.py      # §3 fake-behaviour matrix, F14
```

`$S` is the session scratchpad. Each script is under 40 lines and uses the real `app` package, `conftest.write_def`,
`Document`, `run_document` and `MainWindow`. No mocks were used.
