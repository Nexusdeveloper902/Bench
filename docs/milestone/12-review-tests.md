# Task 12: test-suite review (adversarial)

Date: 2026-10-10. Branch `milestone/component-ecosystem` at `ad1456a`, diff `1b2c335..HEAD -- app`. A read-only review of the repo. Every mutation ran in throwaway copies of `app/` in the session scratchpad, against the real catalog, window and ngspice. Baseline: 388 passed.
(The reviewer's tools could not write this file, so the orchestrator saved it from the reviewer's report. The rows for caught mutants are summarised, not listed; the verdict, survivors, findings and flakiness results are kept in full.)

## 1. Verdict

**The suite proves behavior, not existence, for every brief §10 scenario.**
- **How the scenarios are driven:** through `MainWindow` with QTest mouse and key events, real `component.toml` files in `tmp_path`, real saved JSON read back from disk, and the real engine. The only monkeypatches are native file and message dialogs and the R6 fault injection.
- **Reference values the app doesn't compute:** a bisection Shockley solver, the RC closed form, and a golden netlist captured from the pre-catalog code.
- **Mutation score: 53/69 non-equivalent mutants killed (77 %).** 70 mutants were run; R2 is equivalent today. Areas killed 100 %:
  - pre-check blocked vs excluded;
  - the "not simulated: X" message;
  - placeholder identity;
  - inspector write-back;
  - electrical templates and numerics;
  - DEF-PIN, DEF-PARAM, DEF-DUP-ID and DEF-CAPABILITY(limitations).

**Not exhaustive: 16 realistic regressions survive**, none in the main path of a required scenario. They cluster in five places:
1. **Untested readiness state and category mechanism:** the `placeable` / "Reference only" readiness state and a category outside TAXONOMY have zero tests.
2. **Doc-link URL schemes:** the allowlist is untested for schemes such as `file://` and `javascript:`.
3. **Search and nav counts:** search is not pinned for tags, subcategory or the name-prefix tier, and nav counts are not pinned under the "Simulated only" toggle.
4. **Seed pinouts:** they are only sampled, so a transposition in the ESP32 or DHT22 passes.
5. **`[sim.electrical]` line grammar:** it is not pinned for `line` (ties to architecture F2).

## 2. Scenario → test coverage (B = behavior, B/E = mostly behavior, gap = no test)

| Brief §10 scenario | Tests | Judgment |
|---|---|---|
| Catalog discovery | test_catalog (discovered_under_category, env_roots, builtin_catalog), test_library_ui::test_library_shows_all_definitions_by_category, test_milestone_e2e::test_1 | B (only TAXONOMY categories are used, see T4) |
| Search: name, alias, keyword, ranking | test_catalog::test_search_name_alias_keyword_and_ranking, test_library_ui::test_search_alias_and_empty_state, test_milestone_e2e::test_2 | B (prefix tier, tags and subcategory are not pinned) |
| Category filter + Simulated only | test_catalog::test_category_filter_and_simulated_only, test_library_ui (category_filter, simulated_only_toggle), e2e test_2 | B (nav counts under the toggle are not checked) |
| Category counts | test_library_ui::test_library_shows_all_definitions_by_category | B/E (tautological, see T3) |
| Extensibility | e2e test_3 (TEST_DIODE placed by mouse, run, bisection oracle), test_library_ui::test_new_definition_appears_without_code, test_missing_icon_fallback, test_catalog::test_emit_is_data_driven | B |
| Adding a category | none | **gap** (T4) |
| Unsupported ESP32: place, inspect, save, reload | e2e test_4, test_library_ui (unsupported_part_inspector, details_of_interface_only_part), test_persistence_v2::test_roundtrip_led_plus_interface_only_part, test_canvas_ui::test_badge_on_interface_only_part, test_seed_packages::test_esp32_pins | B (pinouts only sampled, see T5) |
| Placeholder | test_persistence_v2 (4 tests), e2e test_7, test_canvas_ui::test_placeholder_drawn_and_movable | B (string params only, see T7; older versions untested, see T8) |
| Capability: no false success | test_capabilities (12), e2e test_5, test_canvas_ui::test_run_blocked_... | B |
| `placeable` state | none | **gap** (T2) |
| Core Unsupported/Unavailable error mapped to failed | none | **gap** (T11) |
| Invalid definitions | test_catalog::test_invalid_definitions (24 cases) + 4 others, test_library_ui::test_catalog_problems_banner, e2e test_6 | B (gaps V3 and V6, see T6) |
| Doc links safe | test_library_ui::test_doc_links_only_open_safe_targets | B (URL schemes untested, see T9) |
| Electrical regression | golden netlist, LED via catalog, 1N4148, analytic tests, M04/M07/M12/M16/M17/M18/RC | B |
| End to end | test_milestone_e2e (14) | B |
| Persistence | test_persistence_v2, test_persistence_roundtrip, e2e test_4/test_7/test_m07 | B |
| UI per design | test_canvas_ui (19), test_library_ui (18) | B/E (T10, T14) |

## 3. Mutation testing

**Totals: 70 mutants, 53 caught, 16 survived, 1 equivalent. Score 53/69 = 77 %.**

Survivors (each CONFIRMED by a green full run):

| id | mutation | file |
|---|---|---|
| S1 | search: name-prefix ranking tier dropped | catalog.py |
| S4 | search: tags/subcategory not searched | catalog.py |
| C1 | library nav counts ignore the "Simulated only" toggle | library.py |
| C4 | categories outside TAXONOMY hidden from the nav | catalog.py |
| R1 | a non-simulated pin-less part reported as `interface` instead of `placeable` | catalog.py |
| V3 | DEF-CAPABILITY: `interface` on a pin-less part accepted | catalog.py |
| V6 | DEF-SIM: `line` need not start with `{dev} ` | electrical.py |
| D3 | doc_target: any URL scheme accepted (`file://`, `javascript:`) | widgets.py |
| D5 | catalog: any `://` docs entry passes the allowlist | catalog.py |
| P2 | placeholder params coerced to str on load | model.py |
| I2 | an older saved version becomes a placeholder (current code silently upgrades; untested either way) | model.py |
| N5 | core UnsupportedError/UnavailableError no longer mapped to `failed` | sim.py |
| Z3 | wire hit tolerance not scaled by zoom | canvas.py |
| Z5 | fit() zoom may exceed 100 % | canvas.py |
| X1 | ESP32 IO14/IO12 positions transposed | esp32_devkitc/component.toml |
| X2 | DHT22 VDD/DATA positions swapped | dht22/component.toml |

R2 (any `sim:*` capability counts as simulated) is equivalent today because only one domain exists (architecture F12).

The 53 caught mutants span search AND/OR and aliases, filtering, nav counts vs search, TAXONOMY order, the unmodeled() derivation and validation rules V1, V2, V4, V5, V7, V8 and V9. They also cover the doc links (symlink, extension, load-time type), placeholders P1, P3 and P4, incompatibility I1 and I3–I7, the pre-check B1–B5, and messages N1–N3. Rounding them out are inspector write-back W1–W6, Q1, hit-testing Z1, Z2 and Z4, electrical emission E1–E8, the badge tooltip X3 and the 1N4148 model X4. Many kills rest on a single exact assertion.

## 4. Weak-test findings (no high-severity finding)

| id | sev | status | where | problem | suggested test change |
|---|---|---|---|---|---|
| T4 | medium | CONFIRMED (C4) | test_catalog.py:46,:79; test_library_ui.py | No test adds a category outside TAXONOMY, though the brief requires that a new category needs no UI change. | Add a def with `category="Robotics"`. Assert `CATALOG.categories()[-1] == "Robotics"` and a nav row "Robotics" with count 1; clicking it shows only that part. A second unknown category sorts alphabetically after the TAXONOMY ones. |
| T2 | medium | CONFIRMED (R1) | no test; theme.py:30 | The `placeable` / "Reference only" state is untested. | A pin-less, sim-less def with limitations. Assert readiness `placeable` and the badge "Reference only". It places, and `validate()` gives a NOT-SIMULATED warning only. An LED circuit containing it succeeds with `excluded == ["U1"]`; that part alone gives "Nothing to simulate". |
| T9 | medium | CONFIRMED (D3, D5) | test_library_ui.py:270-283; widgets.py:201; catalog.py docs loop | The allowlist is never tested for URL schemes; `openUrl` hands the QUrl to the desktop. | `docs=["file:///etc/passwd","javascript:alert(1)","ftp://host/a.pdf"]` → DEF-FIELD for each, and `doc_target` returns None for each. |
| T5 | medium | CONFIRMED (X1, X2) | test_seed_packages.py:171-190 | ESP32 pins are sampled at the ends only; DHT22, Uno, SSD1306 and piezo pinouts are not asserted. | Table-driven expected order per package, transcribed independently from the cited datasheets (ESP32 J2/J3, 38 rows; DHT22; SSD1306; Uno headers). Assert the pins sorted by position equal the table. |
| T1 | medium | CONFIRMED (S1, S4) | test_catalog.py:64-75 | The prefix tier, tag and subcategory matching, and alias-exact ranking are unobservable. | Name-prefix vs description fixtures; an alias-exact part ranks first; `search("Semiconductors") == [D_1N4148, D, LED]`; `search("3.3-6 V") == [DHT22]`. |
| T3 | low | CONFIRMED (C1) | test_library_ui.py:47-48 | Nav counts are compared with the function the UI itself calls (a tautology), and only with the default toggle and query. | Hard-code the seeded counts. After toggling: Microcontrollers 0, Electrical 10, All 11. After typing "esp": Microcontrollers 1, Electrical 0. |
| T6 | low | CONFIRMED (V3, V6) | test_catalog.py:103-124 | Missing invalid cases. | Add `_iface_no_pins` → DEF-CAPABILITY and `_line_no_dev` → DEF-SIM. After the F2 fix, add a pin after `;` and a literal node. |
| T7 | low | CONFIRMED (P2) | test_persistence_v2.py:22-27 | The placeholder fixture has string params only. | Use params `{"gain": 5, "taps": [1, {"z": null}]}` and assert identical JSON after re-save. |
| T8 | low | CONFIRMED (I2) | test_persistence_v2.py | No test of a saved version lower than the catalog's (architecture F3); docs and D19 disagree with the code. | Load v1 against a v2 def and assert the decided policy. |
| T10 | low | CONFIRMED (Z3, Z5) | test_canvas_ui.py:74-88,:115 | Wire tolerance is unchecked at any zoom; the fit ≤ 100 % cap is unchecked. | At 25 %, a click 4 px from a wire selects it; at 400 %, a click 10 px away selects nothing. For a single small part, `fit()` leaves the zoom at 1.0. |
| T11 | low | CONFIRMED (N5) | test_capabilities.py:91-98 | No test covers the SIM_ERRORS mapping for UnsupportedError/UnavailableError. | Fault-inject each error type and assert status `failed`, the type name in the message or details, and `excluded` preserved. |
| T12 | low | PLAUSIBLE | test_milestone_e2e.py:335-336,:352 | M16 (±10 µs on an 80 µs edge) and M17 (2 %) are looser than needed. | M16 ±2 µs; M17 rel 2e-3 (check against the engine first). |
| T13 | low | CONFIRMED | test_library_ui.py:98,:191; test_catalog.py:213 | Minor tautologies; one test writes the doc param directly instead of using the UI. | Assert the visible chip text; drive `edits["mode"].setCurrentText("PULSE")`. |
| T14 | low | CONFIRMED | test_canvas_ui.py:193-200 | Pixel constants duplicate theme/layout values. | Use `theme.DOCK_H` and relational assertions. |
| T15 | low | CONFIRMED (R2) | test_seed_packages.py:199-207 | readiness/simulated are hard-wired to `sim:electrical` (F12), with no test of a second domain. | `monkeypatch.setitem(SIM_DOMAINS, "digital", ...)` and assert the intended behaviour. |
| T16 | low | CONFIRMED | test_gui_review.py:91 | The overlap check was relaxed to "above OR below". | Assert the design order: plot bottom ≤ Play row top. |
| T17 | low | PLAUSIBLE | golden_netlists.py:42-43 | `__main__` regenerates the golden oracle from current code. | Remove it, or require `--bless`. |

T4, T2, T9, T5 and T1 should be fixed before calling the coverage complete.

## 5. Flakiness

| Run | Result |
|---|---|
| Full suite ×2 | 388 passed (≈37 s) |
| GUI-heavy group (10 files) ×3 | 110 passed each |
| 12 files run alone | all pass |
| Reverse file order | 388 passed |
| 70 mutants, 5 in parallel | no failure outside the mutated feature |

Risks (PLAUSIBLE, not reproduced):
- **F-1:** the `win` fixtures in test_library_ui, test_led_pixels, test_led_e2e, test_canvas and test_led_glow return a shown window and never close it. Convert them to a shared `yield` fixture in conftest.
- **F-2:** test_led_pixels.py:50-58 is a real-timer loop. Drive `w.tick()` instead.
- **F-3:** fixed `qWait` sleeps are used as barriers. Replace them with `qWaitFor`.
