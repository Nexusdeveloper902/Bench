"""Catalog core: discovery, search, definition validation, electrical binding (golden netlists), SVG symbols.
The global CATALOG is rebuilt by the `extra_root` fixture; tests never mutate CATALOG.defs directly."""
import json
import pytest
from conftest import write_def, minimal_def
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsSimpleTextItem
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from app import catalog, electrical
from app.catalog import CATALOG, BUILTIN_ROOT, load_definition
from app.components import ModelError, ROTATIONS
from app.model import Document
from app.sim import run_document
from app.symbols import add_symbol
import golden_netlists

KINDS = {"R", "C", "L", "V", "BAT", "I", "D", "GND", "LED", "SW"}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def problems(code=None, field=None):
    return [p for p in CATALOG.problems if code in (None, p["code"]) and field in (None, p["field"])]


def interface_only(d):
    d["capabilities"] = ["interface"]; d["limitations"] = ["Not simulated."]
    del d["sim"]


# ---------------------------------------------------------------- discovery
def test_builtin_catalog_has_ten_kinds_and_no_problems():
    assert set(CATALOG.defs) == KINDS and CATALOG.problems == []
    assert CATALOG.categories() == ["Electrical", "Power"]
    assert {c.id for c in CATALOG.defs.values() if c.category == "Power"} == {"BAT"}
    assert all(c.readiness == "simulated" and c.path.is_relative_to(BUILTIN_ROOT) for c in CATALOG.defs.values())
    assert CATALOG.get("GND").ground and not CATALOG.get("R").ground
    with pytest.raises(ModelError, match="unknown component type NOPE"): CATALOG.get("NOPE")


def test_definition_discovered_under_category(extra_root):
    write_def(extra_root, "TEMP_SENSOR", category="Sensors", subcategory="Temperature", name="Thermistor")
    assert CATALOG.problems == []
    assert CATALOG.categories() == ["Electrical", "Sensors", "Power"]      # TAXONOMY order, not alphabetical
    d = CATALOG.get("TEMP_SENSOR")
    assert d.name == "Thermistor" and d.terminals == ("a", "b") and d.defaults() == {"R": "100"}
    assert [x.id for x in CATALOG.search("thermistor")] == ["TEMP_SENSOR"]
    assert [x.id for x in CATALOG.search(category="Sensors")] == ["TEMP_SENSOR"]
    catalog.reload()                                                       # roots are rescanned: gone again
    assert "TEMP_SENSOR" not in CATALOG.defs and CATALOG.categories() == ["Electrical", "Power"]


def test_env_roots_discovered(extra_root, monkeypatch):
    write_def(extra_root, "FROM_ENV", reload=False)
    monkeypatch.setenv("SIM_COMPONENT_PATH", str(extra_root))
    assert catalog.default_roots() == [BUILTIN_ROOT, extra_root]
    assert "FROM_ENV" in catalog.reload().defs                              # fixture teardown restores the defaults


def test_search_name_alias_keyword_and_ranking(extra_root):
    assert CATALOG.search("light emitting")[0].id == "LED"                 # alias
    assert CATALOG.search("resistor")[0].id == "R"                         # exact name
    assert CATALOG.search("zzz") == []
    assert CATALOG.search("INDICATOR")[0].id == "LED"                      # keyword, case-insensitive
    assert {d.id for d in CATALOG.search("source")} >= {"V", "I", "BAT"}   # subcategory / tags
    assert [d.id for d in CATALOG.search("dc source")] == ["BAT", "I", "V"]   # AND over tokens, ties by name
    assert CATALOG.search("d")[0].id == "D"                                # exact id outranks mere substring matches
    # ranking: exact name, then name prefix, then other matches; ties by name
    write_def(extra_root, "RES_TOOL", name="Resistor tool", description="x")
    write_def(extra_root, "ZED", name="Zed", description="mentions resistor")
    assert [d.id for d in CATALOG.search("resistor")] == ["R", "RES_TOOL", "SW", "ZED"]   # SW: its description says so


def test_category_filter_and_simulated_only(extra_root):
    write_def(extra_root, "BOARD", mutate=interface_only, category="Microcontrollers", name="Dev board")
    assert CATALOG.get("BOARD").readiness == "interface" and not CATALOG.get("BOARD").simulated
    assert {d.id for d in CATALOG.search(category="Microcontrollers")} == {"BOARD"}
    assert "BOARD" in {d.id for d in CATALOG.search()} and "BOARD" not in {d.id for d in CATALOG.search(simulated_only=True)}
    assert {d.id for d in CATALOG.search(category="Electrical", simulated_only=True)} == KINDS - {"BAT"}
    assert CATALOG.search(category="Audio") == []


# ---------------------------------------------------------------- validation
def _no_id(d): del d["id"]
def _dup_pin(d): d["pins"] = [{"id": "a", "at": [0, 15]}, {"id": "a", "at": [60, 15]}]
def _same_pos(d): d["pins"][1]["at"] = [0, 15]
def _abc(d): d["params"][0]["default"] = "abc"
def _no_section(d): del d["sim"]
def _mcu(d): d["capabilities"].append("sim:mcu")
def _omit_pin(d): d["sim"]["electrical"]["line"] = "{dev} {a} {R}"
def _attr(d): d["sim"]["electrical"]["line"] = "{dev} {a} {b} {dev.__class__}"
def _dev_x(d): d["sim"]["electrical"]["device"] = "X"
def _pres_pins(d): d["presentation"]["pins"] = []
def _unit(d): d["params"][0]["unit"] = "parsec"
def _choice_default(d): d["params"] = [{"key": "m", "label": "M", "kind": "choice", "choices": ["x", "y"], "default": "z"}]
def _when(d): d["params"][0]["when"] = {"R": "x"}
def _escape(d): d["presentation"]["symbol"] = "../../../etc/passwd"
def _glow(d): d["presentation"]["indicator"] = "glow"
def _no_iface(d): d["capabilities"] = ["sim:electrical"]
def _no_limits(d): interface_only(d); del d["limitations"]
def _fmt2(d): d["format"] = 2
def _model_not_model(d): d["sim"]["electrical"]["models"] = ["R1 a b 1"]
def _multiline(d): d["sim"]["electrical"]["line"] = "{dev} {a} {b} {R}\nR2 a b 1"
def _clash(d): d["params"][0]["key"] = "a"
def _bad_current(d): d["sim"]["electrical"]["current"] = "v({dev_lc})"
def _ground_two_pins(d): d["sim"]["electrical"] = {"ground": True}

INVALID = [
    (_no_id, "DEF-FIELD", "id"), ("[ broken", "DEF-PARSE", ""), (_pres_pins, "DEF-FIELD", "presentation.pins"),
    (_dup_pin, "DEF-PIN", "pins[1].id"), (_same_pos, "DEF-PIN", "pins[1].at"), (_abc, "DEF-PARAM", "params[0].default"),
    (_no_section, "DEF-CAPABILITY", "capabilities"), (_mcu, "DEF-CAPABILITY", "capabilities"),
    (_omit_pin, "DEF-SIM", "sim.electrical.line"), (_attr, "DEF-SIM", "sim.electrical.line"),
    (_dev_x, "DEF-SIM", "sim.electrical.device"), (_unit, "DEF-PARAM", "params[0].unit"),
    (_choice_default, "DEF-PARAM", "params[0].default"), (_when, "DEF-PARAM", "params[0].when"),
    (_escape, "DEF-FIELD", "presentation.symbol"), (_glow, "DEF-FIELD", "presentation.indicator"),
    (_no_iface, "DEF-CAPABILITY", "capabilities"), (_no_limits, "DEF-CAPABILITY", "limitations"),
    (_fmt2, "DEF-FIELD", "format"), (_model_not_model, "DEF-SIM", "sim.electrical.models[0]"),
    (_multiline, "DEF-SIM", "sim.electrical.line"), (_clash, "DEF-PARAM", "params[0].key"),
    (_bad_current, "DEF-SIM", "sim.electrical.current"), (_ground_two_pins, "DEF-SIM", "sim.electrical.ground"),
]


@pytest.mark.parametrize("mutate,code,fld", INVALID, ids=[getattr(m, "__name__", "broken_toml") for m, *_ in INVALID])
def test_invalid_definitions(extra_root, mutate, code, fld):
    if isinstance(mutate, str):
        (extra_root / "bad").mkdir(); (extra_root / "bad" / "component.toml").write_text(mutate); catalog.reload([BUILTIN_ROOT, extra_root])
    else:
        write_def(extra_root, "BAD", mutate=mutate)
    hit = problems(code, fld)
    assert hit and all(p["severity"] == "error" for p in hit), CATALOG.problems
    assert hit[0]["file"].endswith("component.toml") and hit[0]["message"]
    assert "BAD" not in CATALOG.defs and set(CATALOG.defs) == KINDS         # rejected, built-ins untouched


def test_diagnostic_messages_say_what_and_how_to_fix(extra_root):
    write_def(extra_root, "BAD", mutate=_pres_pins)
    assert "unknown key 'pins' in [presentation]" in problems("DEF-FIELD")[0]["message"]
    write_def(extra_root, "BAD", mutate=lambda d: d["presentation"].update(designator="U"))   # the TOML key-after-table trap
    assert "top-level keys must come before the first [table]" in problems("DEF-FIELD", "presentation.designator")[0]["message"]
    write_def(extra_root, "BAD", mutate=_attr)
    assert any("{dev.__class__}" in p["message"] and "plain" in p["message"] for p in problems("DEF-SIM"))
    write_def(extra_root, "BAD", mutate=_mcu)
    assert "no 'mcu' simulation domain exists; describe it under limitations" in problems("DEF-CAPABILITY")[0]["message"]
    write_def(extra_root, "BAD", mutate=_abc)
    assert "'abc'" in problems("DEF-PARAM")[0]["message"] and problems("DEF-PARAM")[0]["id"] == "BAD"
    (extra_root / "broken").mkdir(); (extra_root / "broken" / "component.toml").write_text("id = \n")
    catalog.reload([BUILTIN_ROOT, extra_root])
    assert "line 1" in problems("DEF-PARSE")[0]["message"]


def test_duplicate_id_keeps_first(extra_root, tmp_path_factory):
    write_def(extra_root, "R", name="Impostor resistor")                    # clashes with the built-in R
    other = tmp_path_factory.mktemp("other")
    write_def(extra_root, "DUP", reload=False); write_def(other, "DUP", name="Second", reload=False)
    catalog.reload([BUILTIN_ROOT, extra_root, other])
    assert CATALOG.get("R").name == "Resistor" and CATALOG.get("R").path.is_relative_to(BUILTIN_ROOT)
    assert CATALOG.get("DUP").name != "Second"
    dups = problems("DEF-DUP-ID")
    assert {p["id"] for p in dups} == {"R", "DUP"} and all(p["severity"] == "error" for p in dups)
    assert all(str(BUILTIN_ROOT) in p["message"] or str(extra_root) in p["message"] for p in dups)
    assert [d for d in CATALOG.defs.values() if d.name == "Impostor resistor"] == []


def test_missing_symbol_is_warning_with_fallback(extra_root, qapp):
    write_def(extra_root, "NOSYM", mutate=lambda d: d["presentation"].update(symbol="nope.svg", icon="icon.svg"))
    (extra_root / "nosym" / "icon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")   # no viewBox
    assert {(p["code"], p["severity"], p["field"]) for p in CATALOG.problems} == {
        ("DEF-ASSET", "warning", "presentation.symbol"), ("DEF-ASSET", "warning", "presentation.icon")}
    d = CATALOG.get("NOSYM")                                                # warnings keep the definition
    assert d.symbol is None and d.icon is None and d.symbol_for({}) is None
    sc = QGraphicsScene()
    items = add_symbol(sc, {"kind": "NOSYM", "x": 100, "y": 100, "rot": 0, "params": d.defaults()}, d, "#222222")
    assert not any(isinstance(i, QGraphicsSvgItem) for i in items)          # generic labelled box instead
    texts = {i.text() for i in items if isinstance(i, QGraphicsSimpleTextItem)}
    assert {"Test NOSYM", "a", "b"} <= texts


def test_bad_symbol_svg_is_warning(extra_root):
    write_def(extra_root, "BADSVG", mutate=lambda d: d["presentation"].update(symbol="s.svg"), reload=False)
    (extra_root / "badsvg" / "s.svg").write_text("<svg><oops></svg>")
    catalog.reload([BUILTIN_ROOT, extra_root])
    assert problems("DEF-ASSET", "presentation.symbol")[0]["severity"] == "warning" and "BADSVG" in CATALOG.defs


def test_bad_definition_does_not_block_good_one(extra_root):
    write_def(extra_root, "AAA_BAD", mutate=_abc, reload=False)             # sorts first: still must not stop the scan
    write_def(extra_root, "ZZZ_GOOD", reload=False)
    catalog.reload([BUILTIN_ROOT, extra_root])
    assert "ZZZ_GOOD" in CATALOG.defs and "AAA_BAD" not in CATALOG.defs and len(CATALOG.defs) == 11
    assert [p["id"] for p in CATALOG.problems] == ["AAA_BAD"]


def test_load_definition_reports_unreadable_file(tmp_path):
    d, diags = load_definition(tmp_path / "missing.toml")
    assert d is None and diags[0]["code"] == "DEF-PARSE"


# ---------------------------------------------------------------- electrical binding
def test_template_netlist_matches_golden():
    """Every kind in every mode, through electrical.build_netlist, equals the output captured from the pre-catalog code."""
    golden = json.loads(golden_netlists.FIXTURE.read_text())
    docs = golden_netlists.cases()
    assert set(docs) == set(golden)
    assert {p["kind"] for d in docs.values() for p in d.parts.values()} == KINDS
    for name, d in docs.items():
        got = electrical.build_netlist(d.parts, d.nets(), CATALOG)
        assert json.loads(json.dumps(got)) == golden[name], name
        assert d.netlist() == {**got, "excluded": []}


def test_emit_is_data_driven(extra_root):
    """A brand-new electrical part needs no Python: its device letter, line and model come from the definition."""
    write_def(extra_root, "DIV", mutate=lambda d: d["sim"]["electrical"].update(models=[".model m_{dev_lc} R(tc1={R})"]))
    doc = Document(); n = doc.add_part("DIV", R="2.2k"); doc.add_part("GND"); doc.connect(f"{n}.b", "GND1.g")
    nl = doc.netlist()
    assert n == "U1" and nl["lines"] == ["RU1 n1 0 2200.0"]                 # letter decides the device: U1 -> RU1
    assert nl["models"] == [".model m_ru1 R(tc1=2200.0)"] and nl["saves"] == []     # no `current`: nothing observed
    assert [s["name"] for s in nl["signals"]] == ["V(n1)", "V(U1)"]


def test_build_netlist_refuses_unmodeled_part(extra_root):
    write_def(extra_root, "BOARD", mutate=interface_only)
    parts = {"U1": {"kind": "BOARD", "params": {}}}
    with pytest.raises(ModelError, match=r"U1 \(Test BOARD\) has no electrical model"):
        electrical.build_netlist(parts, {"U1.a": "n1", "U1.b": "n2"}, CATALOG)          # never skipped silently
    d = Document(); d.add_part("R"); d.add_part("GND"); u = d.add_part("BOARD"); d.connect("R1.b", "GND1.g")
    assert u == "U1" and d.unmodeled() == ["U1"] and d.definition("U1").readiness == "interface"
    assert d.netlist()["lines"] == ["R1 n1 0 1000.0"]                                   # Document hands over modeled parts only
    only = Document(); only.add_part("BOARD")
    with pytest.raises(ModelError, match="circuit is empty"): only.netlist()


def test_led_example_runs_through_catalog_binding():
    r = run_document(Document.led_example())
    assert r.ok, r.message
    i = r.stats("I(D1)")["final"]
    assert i == pytest.approx(9.1772e-3, rel=1e-4) and len(r.get("I(D1)")) == 511
    assert r.stats("V(D1)")["final"] == pytest.approx(1.97151, rel=1e-4)


# ---------------------------------------------------------------- symbols
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_catalog_symbol_drawn_with_pins_at_its_ends(qapp, kind):
    """Each builtin symbol is an SVG item, and in every rotation its scene bounds (+-1) contain all its pins."""
    c = CATALOG.get(kind)
    for params in ([c.defaults()] + ([dict(c.defaults(), state="closed")] if kind == "SW" else [])):
        files = {c.symbol_for(params)}
        assert None not in files and all(f.suffix == ".svg" and f.is_file() for f in files)
        for rot in ROTATIONS:
            part = {"kind": kind, "x": 200, "y": 200, "rot": rot, "params": params}
            sc = QGraphicsScene()
            (item,) = add_symbol(sc, part, c, "#222222")
            assert isinstance(item, QGraphicsSvgItem)
            box = item.sceneBoundingRect().adjusted(-1, -1, 1, 1)
            for t in c.terminals:
                ox, oy = catalog.terminal_offset(kind, t, rot)
                assert box.contains(QPointF(200 + ox, 200 + oy)), (kind, rot, t)


def test_switch_symbol_follows_state_and_colour_follows_part(qapp):
    c = CATALOG.get("SW")
    assert c.symbol_for({"state": "open"}).name == "switch-open.svg"
    assert c.symbol_for({"state": "closed"}).name == "switch-closed.svg"
    from app.symbols import renderer
    f = str(c.symbol_for({"state": "open"}))
    assert renderer(f, "#222222") is renderer(f, "#222222") and renderer(f, "#222222") is not renderer(f, "#e07000")
    assert renderer(f + ".missing", "#222222") is None
