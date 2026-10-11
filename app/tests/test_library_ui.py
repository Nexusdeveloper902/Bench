"""T8: library panel and inspector, driven through the real widgets with the real catalog (offscreen). Everything on
screen comes from component.toml packages: a new definition needs no UI code."""
import dataclasses
import pytest
from conftest import write_def
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton
from app import widgets
from app.catalog import CATALOG
from app.model import Document
from app.window import MainWindow, pin_pos
from test_persistence_v2 import FUTURE


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(shown_window):
    return shown_window


def type_search(w, text):
    s = w.library.search
    s.clear(); s.setFocus(); QTest.keyClicks(s, text)


def ids(w):
    return list(w.library.cards)


def click_widget(wd, button=Qt.LeftButton):
    QTest.mouseClick(wd, button, Qt.NoModifier, wd.rect().center())


def canvas_click(w, pt):
    QTest.mouseClick(w.canvas.viewport(), Qt.LeftButton, Qt.NoModifier, w.canvas.mapFromScene(QPointF(*pt)))


def test_library_shows_all_definitions_by_category(win):
    lib = win.library
    assert len(lib.cards) == len(CATALOG.defs) and set(lib.cards) == set(CATALOG.defs)
    assert [n.name.text() for n in lib.navs.values()] == ["All"] + CATALOG.categories()   # no hardcoded list in the UI
    for key, nav in lib.navs.items():
        assert nav.count.text() == str(len(CATALOG.search("", key))), key
    assert not lib.banner.isVisible()


def test_search_alias_and_empty_state(win):
    lib = win.library
    type_search(win, "light")
    assert "LED" in ids(win)
    type_search(win, "light emitting")                     # alias of LED: found, and ranked first
    assert ids(win)[0] == "LED"
    type_search(win, "zzz")
    assert ids(win) == [] and lib.empty.isVisible() and "zzz" in lib.empty_title.text()
    assert lib.navs[None].count.text() == "0"
    click_widget(lib.empty_btn)                            # "Clear search"
    assert lib.search.text() == "" and len(ids(win)) == len(CATALOG.defs) and not lib.empty.isVisible()
    type_search(win, "resistor"); QTest.keyClick(lib.search, Qt.Key_Escape)   # Esc clears the field
    assert lib.search.text() == ""


def test_category_filter(win):
    lib = win.library
    cat = "Microcontrollers"
    click_widget(lib.navs[cat])
    assert lib.category == cat and ids(win) and all(CATALOG.defs[i].category == cat for i in ids(win))
    assert lib.navs[cat].property("selected") and not lib.navs[None].property("selected")
    type_search(win, "esp32")                              # category and search combine
    assert ids(win) == ["ESP32_DEVKITC"]
    click_widget(lib.navs[None])
    assert ids(win) == ["ESP32_DEVKITC"]                   # still filtered by the query, across categories


def test_simulated_only_toggle_and_empty_category(win):
    lib = win.library
    n_all = len(ids(win))
    click_widget(lib.toggle)
    shown = ids(win)
    assert 0 < len(shown) < n_all and all(CATALOG.defs[i].simulated for i in shown)
    assert all(c.badge.text() == "Simulated" for c in lib.cards.values())
    click_widget(lib.navs["Microcontrollers"])             # every microcontroller is interface-only
    assert ids(win) == [] and lib.empty.isVisible() and "No simulated components in Microcontrollers" in lib.empty_title.text()
    assert "hidden" in lib.empty_text.text() and lib.empty_btn.text() == "Show all"
    click_widget(lib.empty_btn)
    assert not lib.toggle.isChecked() and ids(win) and all(CATALOG.defs[i].category == "Microcontrollers" for i in ids(win))


def test_card_content_and_readiness_badges(win):
    cards = win.library.cards
    assert cards["R"].badge.text() == "Simulated" and cards["R"].name.text() == "Resistor"
    esp = cards["ESP32_DEVKITC"]
    assert esp.badge.text() == "Interface only" and esp.badge.toolTip() == "Not simulated"
    assert esp.desc.text().startswith("Espressif")
    tags = list(CATALOG.defs["ESP32_DEVKITC"].tags[:2])
    assert [c.full for c in esp.chips] == tags
    for c, t in zip(esp.chips, tags):      # the visible text: the whole tag, or elided with the whole tag in the tooltip
        assert c.text() == t or (c.text().endswith("…") and c.toolTip() == t), (c.text(), c.toolTip())


def test_card_click_then_canvas_click_places_part(win):
    lib = win.library
    click_widget(lib.card("R"))
    assert win.tool == "R" and lib.card("R").property("armed") and lib.card("R").pill.isVisible()
    assert "Placing Resistor" in win.status.text()
    canvas_click(win, (185, 105))
    assert list(win.doc.parts) == ["R1"] and win.doc.parts["R1"]["x"] == 180 and win.sel == "R1"
    assert win.tool is None and not lib.card("R").property("armed")
    click_widget(lib.card("R")); click_widget(lib.card("R"))       # clicking the armed card again disarms
    assert win.tool is None and not lib.card("R").property("armed")
    click_widget(lib.card("C")); click_widget(lib.card("R"))       # exactly one card is armed
    assert win.tool == "R" and not lib.card("C").property("armed")
    QTest.keyClick(win.canvas, Qt.Key_Escape)
    assert win.tool is None and not lib.card("R").property("armed")


def test_details_view_right_click_and_shift_enter(win):
    lib = win.library
    click_widget(lib.card("D"), Qt.RightButton)
    assert lib.in_details() and lib.selected == "D" and lib.card("D").property("selected")
    texts = " ".join(l.text() for l in lib.detail_page.widget().findChildren(QLabel))
    for needle in ("Diode", "id D", "version 1", "Anode", "Cathode", "Saturation current", "1e-14"):
        assert needle in texts, needle
    lib.back()
    assert not lib.in_details() and lib.card("D").property("selected")      # R1: stays selected after Back
    lib.card("R").setFocus(); QTest.keyClick(lib.card("R"), Qt.Key_Return, Qt.ShiftModifier)
    assert lib.in_details() and lib.selected == "R" and "id R" in " ".join(
        l.text() for l in lib.detail_page.widget().findChildren(QLabel))
    lib.back()
    assert not lib.card("D").property("selected") and lib.card("R").property("selected")
    click_widget(lib.card("C"))                            # arming another card clears the selected card
    assert lib.selected is None
    assert win.sel is None                                 # library state never touches the canvas selection / inspector
    assert win.inspector.view == "none"


def test_details_of_interface_only_part(win):
    lib = win.library
    lib.show_details("ESP32_DEVKITC")
    texts = " ".join(l.text() for l in lib.detail_page.widget().findChildren(QLabel))
    assert "Interface only" in texts and "PINS (38)" in texts and "No configurable parameters." in texts
    assert "no MCU emulation" in texts
    place = [b for b in lib.detail_page.widget().findChildren(QPushButton) if b.text() == "Place"][0]
    click_widget(place)
    assert win.tool == "ESP32_DEVKITC" and not lib.in_details() and lib.selected is None


def test_catalog_problems_banner(extra_root, qapp):
    write_def(extra_root, "BROKEN", mutate=lambda d: d["pins"].append({"id": "a", "at": [1, 1]}))
    w = MainWindow(); w.show()
    lib = w.library
    assert lib.banner.isVisible() and lib.problem_count == 1
    assert lib.banner_toggle.text().startswith("1 definition could not be loaded")
    assert not lib.banner_body.isVisible()
    click_widget(lib.banner_toggle)                        # expands: file, code, message
    body = " ".join(l.text() for l in lib.banner_body.findChildren(QLabel))
    assert lib.banner_body.isVisible() and "broken/component.toml" in body and "DEF-PIN" in body and "duplicate pin" in body
    assert "BROKEN" not in lib.cards and "R" in lib.cards   # the good parts still load


def test_no_banner_without_problems(win):
    assert CATALOG.problems == [] and not win.library.banner.isVisible()


def test_new_definition_appears_without_code(extra_root, qapp):
    def mutate(d):
        d["params"] = [{"key": "gain", "label": "Gain factor", "unit": "A", "default": "2m"}]
        d["sim"]["electrical"]["line"] = "{dev} {a} {b} 100"
    write_def(extra_root, "ACME_PART", mutate, name="Acme gizmo", description="Brand new part.", designator="X")
    w = MainWindow(); w.show()
    card = w.library.card("ACME_PART")
    assert card is not None and card.name.text() == "Acme gizmo" and card.tile.property("fallback")   # generic glyph icon
    click_widget(card); canvas_click(w, (205, 205))
    assert list(w.doc.parts) == ["X1"]
    click_widget(w.library.card("GND")); canvas_click(w, (365, 305))
    for a, b in (("X1.a", "GND1.g"), ("X1.b", "GND1.g")):
        canvas_click(w, (pin_pos(w.doc, a).x(), pin_pos(w.doc, a).y()))
        canvas_click(w, (pin_pos(w.doc, b).x(), pin_pos(w.doc, b).y()))
    assert len(w.doc.wires) == 2
    w.select("X1")
    ed = w.inspector.field("Gain factor")
    assert ed.text() == "2m" and any(l.text() == "A" for l in w.inspector.findChildren(QLabel, "unit"))
    ed.setFocus(); ed.selectAll(); QTest.keyClicks(ed, "5m"); QTest.keyClick(ed, Qt.Key_Return)
    assert w.doc.parts["X1"]["params"]["gain"] == "5m"


def test_inspector_from_schema_mode_switch(win):
    win.library.card("V").click(); canvas_click(win, (65, 105))
    ins = win.inspector
    assert list(ins.edits) == ["mode", "dc"]
    win.select("V1")
    ins.edits["mode"].setCurrentText("PULSE"); QApplication.processEvents()   # through the UI, like a user
    assert win.doc.parts["V1"]["params"]["mode"] == "PULSE"
    assert len(ins.edits) == 8 and sum(not isinstance(e, QComboBox) for e in ins.edits.values()) == 7
    combo = ins.edits["mode"]                                                 # re-rendered by the change
    combo.setCurrentText("SIN"); QApplication.processEvents()          # a choice change re-renders the `when` fields
    assert win.doc.parts["V1"]["params"]["mode"] == "SIN" and list(win.inspector.edits) == ["mode", "vo", "va", "freq"]
    win.inspector.edits["mode"].setCurrentText("DC"); QApplication.processEvents()
    assert list(win.inspector.edits) == ["mode", "dc"]


def test_invalid_input_shows_error_and_leaves_model_unchanged(win):
    win.library.card("R").click(); canvas_click(win, (185, 105))
    ins = win.inspector
    ed = ins.field("Resistance")
    ed.setFocus(); ed.selectAll(); QTest.keyClicks(ed, "abc")
    assert ins.boxes["R"].property("invalid") and "not a number" in ins.message("R") and ins.msgs["R"].isVisible()
    assert ins.banner.isVisible()
    assert win.doc.parts["R1"]["params"]["R"] == "1000"                # nothing is stored while typing
    ed.selectAll(); QTest.keyClicks(ed, "4.7k")
    assert not ins.boxes["R"].property("invalid") and "4.7 kΩ" in ins.message("R") and not ins.banner.isVisible()
    assert win.doc.parts["R1"]["params"]["R"] == "1000"
    QTest.keyClick(ed, Qt.Key_Return)                                  # commit: now the model changes, undo restores
    assert win.doc.parts["R1"]["params"]["R"] == "4.7k"
    win.undo(); assert win.doc.parts["R1"]["params"]["R"] == "1000"


def test_unsupported_part_inspector(win):
    d = Document.led_example(); d.add_part("ESP32_DEVKITC", 600, 40); d.connect("U1.3V3", "R1.a")
    win._reset(d); win.select("U1")
    ins = win.inspector
    badge = ins.findChildren(widgets.Badge)[0]
    assert badge.text() == "Interface only" and badge.toolTip() == "Not simulated"
    assert not ins.edits
    table = ins.findChildren(widgets.Table)[0]
    assert len(table.rows) == 38
    states = {s.text() for s in ins.findChildren(widgets.PinState)}
    assert states == {"wired", "open"}
    texts = " ".join(l.text() for l in ins.findChildren(QLabel))
    assert "PINS (38)" in texts and "no MCU emulation" in texts and "No configurable parameters." in texts
    assert any("espressif.com" in b.text() for b in ins.findChildren(QPushButton)) and "type ESP32_DEVKITC" in texts


def test_placeholder_inspector_is_read_only(win):
    win._reset(Document.from_dict(FUTURE)); win.select("U2")
    ins = win.inspector
    assert ins.view == "placeholder" and ins.findChildren(widgets.Badge)[0].text() == "Placeholder"
    assert list(ins.edits) == ["coil"] and ins.edits["coil"].text() == "5" and not ins.edits["coil"].isEnabled()
    texts = " ".join(l.text() for l in ins.findChildren(QLabel))
    assert "FUTURE_RELAY" in texts and "Kept unchanged" in texts and "PINS (FROM CONNECTIONS)" in texts
    assert [r for r in ins.findChildren(widgets.PinState)] and "delete" in ins.buttons
    ins.buttons["delete"].click()
    assert "U2" not in win.doc.parts


def test_wire_and_nothing_selected_views(win):
    win.load_led_example(); win.run()
    ins = win.inspector
    assert ins.view == "none" and "Nothing selected" in " ".join(l.text() for l in ins.findChildren(QLabel))
    win.select_wire(("R1.b", "D1.a"))
    assert ins.view == "wire"
    texts = " ".join(l.text() for l in ins.findChildren(QLabel))
    assert "R1  ·  pin b" in texts and "D1  ·  pin Anode" in texts and "2 pins joined" in texts
    ins.buttons["plot"].click()
    node = win.doc.nets()["R1.b"]
    assert win.probe.currentText() == f"V({node})"
    ins.buttons["delete"].click()
    assert ("R1.b", "D1.a") not in win.doc.wires and ins.view == "none"


def test_definition_text_is_shown_as_plain_text(extra_root, qapp):
    """F10: markup in a package's name, description or pin name is shown as text, never rendered (no <img>, no <b>)."""
    def mutate(d):
        d["name"] = "<b>Bold</b><img src=x>Rich"
        d["description"] = "<i>Italic</i> <img src=x>"
        d["pins"][0]["name"] = "<b>Pin</b>"
    write_def(extra_root, "MARKUP", mutate, designator="M")
    w = MainWindow(); w.show()
    card = w.library.card("MARKUP")
    assert card.name.text() == "<b>Bold</b><img src=x>Rich" and card.name.textFormat() == Qt.PlainText
    w.library.show_details("MARKUP")
    shown = [l for l in w.library.detail_page.widget().findChildren(QLabel) if "<" in l.text()]
    assert {"<b>Bold</b><img src=x>Rich", "<i>Italic</i> <img src=x>", "<b>Pin</b>"} <= {l.text() for l in shown}
    assert all(l.textFormat() == Qt.PlainText for l in shown)


def test_missing_icon_fallback(extra_root, qapp):
    def mutate(d):
        d["presentation"]["symbol"] = "nothing.svg"          # DEF-ASSET warning: the part loads, the icon is missing
    write_def(extra_root, "NOICON", mutate, designator="Q")
    w = MainWindow()
    c = w.library.card("NOICON")
    assert c is not None and c.tile.property("fallback") and c.tile.pix is None
    assert [l.text() for l in c.tile.findChildren(QLabel)] == ["Q"]       # designator letter glyph
    assert w.library.card("R").tile.pix is not None and not w.library.card("R").tile.property("fallback")


def test_doc_links_only_open_safe_targets(extra_root, qapp):
    """A docs entry that is not an http(s) URL or an allowed file type inside the package gets no link (and the
    catalog rejects the definition with an actionable message)."""
    f = write_def(extra_root, "RUNSH", docs=["run.sh"])
    probs = [p for p in CATALOG.problems if p["field"] == "docs"]
    assert probs and probs[0]["code"] == "DEF-FIELD" and "run.sh" in probs[0]["message"] and "RUNSH" not in CATALOG.defs
    (f.parent / "run.sh").write_text("#!/bin/sh\n"); (f.parent / "ok.md").write_text("# hi\n")
    (f.parent / "x.md").symlink_to("/etc/hostname")           # allowed type, but escapes the package through a symlink
    d = dataclasses.replace(CATALOG.defs["R"], path=f.parent, docs=("run.sh", "x.md", "../../etc/passwd.txt"))
    assert widgets.docs_block(d) is None
    assert widgets.doc_target(d, "run.sh") is None and widgets.doc_target(d, "x.md") is None
    d = dataclasses.replace(d, docs=("run.sh", "ok.md", "https://example.org/a"))
    blk = widgets.docs_block(d)
    assert [b.toolTip() for b in blk.findChildren(QPushButton)] == ["ok.md", "https://example.org/a"]



@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://host/a.pdf", "data:text/html,x"])
def test_doc_links_reject_other_url_schemes(extra_root, qapp, url):
    """D3/D5: only http(s) URLs (or package files) are docs; every other scheme is DEF-FIELD at load and never opens."""
    write_def(extra_root, "SCHEME", docs=[url])
    probs = [p for p in CATALOG.problems if p["field"] == "docs"]
    assert probs and probs[0]["code"] == "DEF-FIELD" and "scheme" in probs[0]["message"] and "SCHEME" not in CATALOG.defs
    assert widgets.doc_target(CATALOG.defs["R"], url) is None
    assert widgets.doc_target(CATALOG.defs["R"], "https://example.org/a").toString() == "https://example.org/a"


def test_new_category_appears_in_nav_without_code(extra_root, qapp):
    """T4/C4: a package with a category outside TAXONOMY adds a nav row (after the TAXONOMY ones, alphabetical)."""
    write_def(extra_root, "SERVO", category="Robotics", name="Servo")
    write_def(extra_root, "ARM", category="Actuators", name="Arm")
    w = MainWindow(); w.show(); lib = w.library
    assert [n.name.text() for n in lib.navs.values()][-3:] == ["Power", "Actuators", "Robotics"]
    assert lib.navs["Robotics"].count.text() == "1"
    click_widget(lib.navs["Robotics"])
    assert ids(w) == ["SERVO"] and lib.cards["SERVO"].badge.text() == "Simulated"
    click_widget(lib.navs["Actuators"])
    assert ids(w) == ["ARM"]


def test_nav_counts_follow_toggle_and_query(win):
    """T3: seeded counts are hard-coded (not the function the UI itself calls); the toggle and the query change them."""
    lib = win.library
    n = lambda k: lib.navs[k].count.text()
    assert (n(None), n("Electrical"), n("Microcontrollers"), n("Sensors")) == ("20", "13", "2", "2")
    click_widget(lib.toggle)
    assert (n(None), n("Electrical"), n("Microcontrollers"), n("Sensors")) == ("15", "13", "0", "1")
    click_widget(lib.toggle)
    type_search(win, "esp")
    assert (n(None), n("Microcontrollers"), n("Electrical")) == ("1", "1", "0")
