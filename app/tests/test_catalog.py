"""Catalog core: discovery, search, definition validation, electrical binding (golden netlists), SVG symbols.
The global CATALOG is rebuilt by the `extra_root` fixture; tests never mutate CATALOG.defs directly."""
import json, os
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
from test_library_batch_b import NEW as BATCH_B
from test_library_batch_c import NEW as BATCH_C

KINDS = {"R", "C", "L", "V", "BAT", "I", "D", "GND", "LED", "SW"}     # the ten built-ins; seed packages add more
ALL_CATEGORIES = ["Electrical", "Microcontrollers", "Sensors", "Displays", "Audio", "Power"]   # with the seeds


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
    assert KINDS <= set(CATALOG.defs) and CATALOG.problems == []
    assert CATALOG.categories() == ALL_CATEGORIES
    assert {c.id for c in CATALOG.defs.values() if c.category == "Power"} == {"BAT", "BAT_COIN", "BAT_AA", "BAT_LITHIUM"}
    assert all(c.readiness == "simulated" and c.path.is_relative_to(BUILTIN_ROOT) for c in CATALOG.defs.values() if c.id in KINDS)
    assert CATALOG.get("GND").ground and not CATALOG.get("R").ground
    with pytest.raises(ModelError, match="unknown component type NOPE"): CATALOG.get("NOPE")


def test_definition_discovered_under_category(extra_root):
    write_def(extra_root, "TEMP_SENSOR", category="Sensors", subcategory="Temperature", name="Thermistor")
    assert CATALOG.problems == []
    assert CATALOG.categories() == ALL_CATEGORIES[:1] + ["Microcontrollers", "Sensors", "Displays", "Audio", "Power"]   # TAXONOMY order, not alphabetical
    d = CATALOG.get("TEMP_SENSOR")
    assert d.name == "Thermistor" and d.terminals == ("a", "b") and d.defaults() == {"R": "100"}
    assert "TEMP_SENSOR" in [x.id for x in CATALOG.search("thermistor")]
    assert "TEMP_SENSOR" in [x.id for x in CATALOG.search(category="Sensors")]
    catalog.reload()                                                       # roots are rescanned: gone again
    assert "TEMP_SENSOR" not in CATALOG.defs and CATALOG.categories() == ALL_CATEGORIES


def test_env_roots_discovered(extra_root, monkeypatch):
    write_def(extra_root, "FROM_ENV", reload=False)
    monkeypatch.setenv("SIM_COMPONENT_PATH", str(extra_root))
    assert catalog.default_roots() == [BUILTIN_ROOT, extra_root]
    assert "FROM_ENV" in catalog.reload().defs                              # fixture teardown restores the defaults


def test_same_root_listed_twice_or_nested_is_not_a_duplicate(extra_root, monkeypatch):
    """F4: SIM_COMPONENT_PATH=X with `python3 -m app.catalog X` (both documented) must not report every part as its own duplicate."""
    import subprocess, sys
    write_def(extra_root, "FROM_ENV", reload=False)
    (extra_root / "nested").mkdir()
    write_def(extra_root / "nested", "NESTED_PART", reload=False)
    monkeypatch.setenv("SIM_COMPONENT_PATH", f"{extra_root}{os.pathsep}{extra_root / '.'}{os.pathsep}{extra_root / 'nested'}")
    c = catalog.reload()
    assert not [p for p in c.problems if p["code"] == "DEF-DUP-ID"]
    assert {"FROM_ENV", "NESTED_PART"} <= set(c.defs)
    cli = subprocess.run([sys.executable, "-m", "app.catalog", str(extra_root)], capture_output=True, text=True,
                         cwd=str(BUILTIN_ROOT.parents[1]), env={**os.environ, "SIM_COMPONENT_PATH": str(extra_root)})
    assert cli.returncode == 0, cli.stdout
    assert "0 errors" in cli.stdout.splitlines()[-1]


def test_symlinked_package_directory_is_warned_not_silently_skipped(extra_root, tmp_path_factory):
    """F13: a package reached through a symlinked directory is not loaded (no loops either), and says so."""
    outside = tmp_path_factory.mktemp("outside")
    write_def(outside, "SYMPART", reload=False)
    (extra_root / "sympart").symlink_to(outside / "sympart", target_is_directory=True)
    (extra_root / "loop").symlink_to(extra_root, target_is_directory=True)                 # a cycle must not hang
    c = catalog.reload([BUILTIN_ROOT, extra_root])
    assert "SYMPART" not in c.defs and KINDS <= set(c.defs)
    warn = [p for p in c.problems if p["code"] == "DEF-LINK"]
    assert sorted(p["file"] for p in warn) == sorted([str(extra_root / "sympart"), str(extra_root / "loop")])
    assert all(p["severity"] == "warning" for p in warn)
    assert "copy the directory" in warn[0]["message"]


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
    mine = {"R", "RES_TOOL", "SW", "ZED"}            # other shipped parts may mention resistors too
    assert [d.id for d in CATALOG.search("resistor") if d.id in mine] == ["R", "RES_TOOL", "SW", "ZED"]   # SW: its description says so


def test_search_prefix_alias_tags_and_subcategory(extra_root):
    """D20 ranking tiers and every searched field, pinned with fixtures whose only match is the field under test."""
    write_def(extra_root, "A_MARKWID", name="Mark widget", reload=False)               # name sorts first, matches mid-name
    write_def(extra_root, "B_WIDMARK", name="Widget mark", reload=False)               # name PREFIX: must come first
    write_def(extra_root, "C_YAK", name="Yak", aliases=["wid"], reload=False)         # exact alias: before the prefix tier
    write_def(extra_root, "D_TAG", name="Plain", tags=["qwertytag"], subcategory="Qwertysub", reload=False)
    catalog.reload([BUILTIN_ROOT, extra_root])
    mine = {"A_MARKWID", "B_WIDMARK", "C_YAK"}       # shipped parts may match "wid" too (bandwidth)
    assert [d.id for d in CATALOG.search("wid") if d.id in mine] == ["C_YAK", "B_WIDMARK", "A_MARKWID"]
    assert [d.id for d in CATALOG.search("qwertytag")] == ["D_TAG"]                    # tags
    assert [d.id for d in CATALOG.search("qwertysub")] == ["D_TAG"]                    # subcategory
    assert [d.id for d in CATALOG.search("Semiconductors")] == ["D_1N4148", "Q_2N2222", "D", "LED"]
    assert [d.id for d in CATALOG.search("3.3-6 V")] == ["DHT22"]


def test_placeable_part_is_reference_only(extra_root):
    """No pins, no sim:* : readiness `placeable`. It is never simulated, never blocks, and is named when excluded."""
    def note(d):
        d["pins"], d["params"], d["capabilities"] = [], [], []; d["limitations"] = ["Documentation only."]; del d["sim"]
    write_def(extra_root, "NOTE", note, name="Datasheet note")
    n = CATALOG.get("NOTE")
    assert CATALOG.problems == [] and n.readiness == "placeable" and not n.simulated and n.terminals == ()
    from app.theme import READINESS
    assert READINESS[n.readiness][0] == "Reference only"
    d = Document.led_example(); u = d.add_part("NOTE")
    assert d.unmodeled() == [u] and d.pins(u) == []
    assert [(x["code"], x["severity"]) for x in d.validate()] == [("NOT-SIMULATED", "warning")]
    r = run_document(d)
    assert r.ok and r.excluded == [u] and f"not simulated: {u}" in r.message
    alone = Document(); alone.add_part("NOTE")
    r = run_document(alone)
    assert not r.ok and "Nothing to simulate" in r.message


def test_model_name_collision_between_definitions(extra_root):
    """F1: a model name is global to the deck. Same name, different body is refused when the catalog loads; the same
    body is shared; and build_netlist is the backstop for names the catalog cannot see (per-instance bodies)."""
    mod = lambda body: (lambda d: d["sim"]["electrical"].update(device="D", line="{dev} {a} {b} dled", models=[f".model dled {body}"]))
    write_def(extra_root, "LED_GREEN", mod("D(Is=1e-22 N=2.0 Rs=5)"), reload=False)
    write_def(extra_root, "LED_COPY", mod("D(Is=1e-20 N=1.8 Rs=5)"), reload=False)      # builtin LED's body: shared, not a clash
    catalog.reload([BUILTIN_ROOT, extra_root])
    assert "LED_GREEN" not in CATALOG.defs and "LED_COPY" in CATALOG.defs
    (p,) = problems("DEF-SIM", "sim.electrical.models")
    assert p["id"] == "LED_GREEN" and "'dled'" in p["message"] and "LED" in p["message"]
    write_def(extra_root, "SHARED", lambda d: d["sim"]["electrical"].update(
        device="D", line="{dev} {a} {b} shr", models=[".model shr D(Is={R})"]))        # fixed name, per-instance body
    doc = Document(); doc.add_part("SHARED", R="1e-12"); doc.add_part("SHARED", R="2e-12"); doc.add_part("GND")
    doc.connect("U1.a", "GND1.g"); doc.connect("U2.a", "GND1.g")
    with pytest.raises(ModelError, match="model 'shr' gets different parameters in different SHARED parts"):
        doc.netlist()
    doc.set_param("U2", "R", "1e-12")
    assert len(doc.netlist()["models"]) == 1


def test_category_policy_and_nav(extra_root):
    """Free-text categories are allowed (no UI change); a case-insensitive TAXONOMY match is normalised; empty is an
    error; new categories sort alphabetically after the TAXONOMY ones."""
    write_def(extra_root, "ROBO", category="Robotics", reload=False)
    write_def(extra_root, "ACT", category="actuators", reload=False)
    write_def(extra_root, "SENS", category="sensors", reload=False)
    write_def(extra_root, "EMPTYCAT", category="  ", reload=False)
    catalog.reload([BUILTIN_ROOT, extra_root])
    assert CATALOG.get("SENS").category == "Sensors" and "EMPTYCAT" not in CATALOG.defs
    assert [p["field"] for p in problems("DEF-FIELD")] == ["category"]
    assert CATALOG.categories() == ALL_CATEGORIES + ["actuators", "Robotics"]
    assert [d.id for d in CATALOG.search(category="Robotics")] == ["ROBO"]


def test_category_filter_and_simulated_only(extra_root):
    write_def(extra_root, "BOARD", mutate=interface_only, category="Microcontrollers", name="Dev board")
    assert CATALOG.get("BOARD").readiness == "interface" and not CATALOG.get("BOARD").simulated
    assert {d.id for d in CATALOG.search(category="Microcontrollers")} == {"BOARD", "ESP32_DEVKITC", "ARDUINO_UNO_R3"}
    assert "BOARD" in {d.id for d in CATALOG.search()} and "BOARD" not in {d.id for d in CATALOG.search(simulated_only=True)}
    assert {d.id for d in CATALOG.search(category="Electrical", simulated_only=True)} == (KINDS - {"BAT"}) | {"D_1N4148", "Q_2N2222", "OPAMP", "OPAMP_DUAL"} | {
        "POT", "RHEOSTAT", "RNET4_ISO", "RNET4_BUS", "R_TOL", "IND_DCR", "IND_COUPLED", "XFMR_1N", "XFMR_CT", "T_LINE",
        "CAP_CERAMIC", "CAP_ELECTROLYTIC", "CAP_TANTALUM", "CAP_FILM", "FUSE", "PTC", "MOV", "SPDT", "DPST", "DPDT",
        "PUSH_NO", "PUSH_NC", "RELAY_SPDT", "RELAY_DPDT", "REED", "VSW", "CSW", "VSRC_EXP_SFFM", "ISRC_WAVE", "VCVS",
        "VCCS", "CCCS", "CCVS", "SAT_AMP"} | set(BATCH_B) | set(BATCH_C)     # batch A (test_library_batch_a.py), B and C (test_library_batch_[bc].py)
    assert CATALOG.search(category="Communication") == []


# ---------------------------------------------------------------- validation
def _no_id(d): del d["id"]
def _dup_pin(d): d["pins"] = [{"id": "a", "at": [0, 15]}, {"id": "a", "at": [60, 15]}]
def _same_pos(d): d["pins"][1]["at"] = [0, 15]
def _abc(d): d["params"][0]["default"] = "abc"
def _no_section(d): del d["sim"]
def _mcu(d): d["capabilities"].append("sim:mcu")
def _omit_pin(d): d["sim"]["electrical"]["line"] = "{dev} {a} {R}"
def _attr(d): d["sim"]["electrical"]["line"] = "{dev} {a} {b} {dev.__class__}"
def _dev_x(d): d["sim"]["electrical"]["device"] = "X"                    # an X part without defs/body
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
def _line_no_dev(d): d["sim"]["electrical"]["line"] = "{a} {b} {R}"
def _literal_node(d): d["sim"]["electrical"]["line"] = "{dev} {a} 0 {b}"           # F2: b is not a node, 0 is undrawn ground
def _tap(d): d["pins"] = d["pins"][:1]; d["sim"]["electrical"]["line"] = "{dev} {a} n1 {R}"   # F2 repro: 1 pin, literal n1
def _pin_after_comment(d): d["sim"]["electrical"]["line"] = "{dev} {a} {R} ; {b}"   # F2: {b} "reaches SPICE" but is a comment
def _pin_in_tail(d): d["sim"]["electrical"]["line"] = "{dev} {a} {b} {R} {a}"
def _three_pins(d): d["pins"].append({"id": "w", "at": [30, 0]}); d["sim"]["electrical"]["line"] = "{dev} {a} {b} {R} {w}"
def _iface_no_pins(d): interface_only(d); d["pins"] = []                           # V3: `interface` needs pins
def _ground_two_pins(d): d["sim"]["electrical"] = {"ground": True}
def _huge_box(d): d["presentation"]["box"] = [0, 0, 1e12, 1e12]                 # F9: overflows canvas hit-testing
def _huge_pin(d): d["pins"][0]["at"] = [1e12, 15]

INVALID = [
    (_no_id, "DEF-FIELD", "id"), ("[ broken", "DEF-PARSE", ""), (_pres_pins, "DEF-FIELD", "presentation.pins"),
    (_dup_pin, "DEF-PIN", "pins[1].id"), (_same_pos, "DEF-PIN", "pins[1].at"), (_abc, "DEF-PARAM", "params[0].default"),
    (_no_section, "DEF-CAPABILITY", "capabilities"), (_mcu, "DEF-CAPABILITY", "capabilities"),
    (_omit_pin, "DEF-SIM", "sim.electrical.line"), (_attr, "DEF-SIM", "sim.electrical.line"),
    (_dev_x, "DEF-SIM", "sim.electrical.line"), (_unit, "DEF-PARAM", "params[0].unit"),
    (_choice_default, "DEF-PARAM", "params[0].default"), (_when, "DEF-PARAM", "params[0].when"),
    (_escape, "DEF-FIELD", "presentation.symbol"), (_glow, "DEF-FIELD", "presentation.indicator"),
    (_no_iface, "DEF-CAPABILITY", "capabilities"), (_no_limits, "DEF-CAPABILITY", "limitations"),
    (_fmt2, "DEF-FIELD", "format"), (_model_not_model, "DEF-SIM", "sim.electrical.models[0]"),
    (_multiline, "DEF-SIM", "sim.electrical.line"), (_clash, "DEF-PARAM", "params[0].key"),
    (_line_no_dev, "DEF-SIM", "sim.electrical.line"), (_literal_node, "DEF-SIM", "sim.electrical.line"),
    (_tap, "DEF-SIM", "sim.electrical.line"), (_pin_after_comment, "DEF-SIM", "sim.electrical.line"), (_pin_in_tail, "DEF-SIM", "sim.electrical.line"),
    (_three_pins, "DEF-SIM", "sim.electrical.line"), (_iface_no_pins, "DEF-CAPABILITY", "capabilities"),
    (_bad_current, "DEF-SIM", "sim.electrical.current"), (_ground_two_pins, "DEF-SIM", "sim.electrical.ground"),
    (_huge_box, "DEF-FIELD", "presentation.box"), (_huge_pin, "DEF-PIN", "pins[0].at"),
]


@pytest.mark.parametrize("mutate,code,fld", INVALID, ids=[getattr(m, "__name__", "broken_toml") for m, *_ in INVALID])
def test_invalid_definitions(extra_root, mutate, code, fld):
    if isinstance(mutate, str):
        (extra_root / "bad").mkdir(); (extra_root / "bad" / "component.toml").write_text(mutate); catalog.reload([BUILTIN_ROOT, extra_root])
    else:
        write_def(extra_root, "BAD", mutate=mutate)
    hit = problems(code, fld)
    assert hit and all(p["severity"] == "error" for p in hit), CATALOG.problems
    assert hit[0]["file"].endswith("component.toml") and hit[0]["id"] in ("BAD", None) and hit[0]["message"]
    assert "BAD" not in CATALOG.defs and KINDS <= set(CATALOG.defs)         # rejected, built-ins untouched


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
    base = len(catalog.reload().defs)
    write_def(extra_root, "AAA_BAD", mutate=_abc, reload=False)             # sorts first: still must not stop the scan
    write_def(extra_root, "ZZZ_GOOD", reload=False)
    catalog.reload([BUILTIN_ROOT, extra_root])
    assert "ZZZ_GOOD" in CATALOG.defs and "AAA_BAD" not in CATALOG.defs and len(CATALOG.defs) == base + 1
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


def test_golden_oracle_regeneration_needs_explicit_bless():
    """T17: `python3 -m app.tests.golden_netlists` refuses to overwrite the oracle unless given --bless."""
    import subprocess, sys
    before = golden_netlists.FIXTURE.read_bytes()
    r = subprocess.run([sys.executable, "-m", "app.tests.golden_netlists"], capture_output=True, text=True,
                       cwd=str(BUILTIN_ROOT.parents[1]))
    assert r.returncode != 0 and "--bless" in r.stderr + r.stdout
    assert golden_netlists.FIXTURE.read_bytes() == before


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


# ---------------------------------------------------------------- N-terminal devices (Q/J/M)
def _multi(letter, pins, line, **elec):
    def m(d):
        d["pins"] = [{"id": p, "at": [0, 10 * i]} for i, p in enumerate(pins)]
        d["params"] = []
        d["sim"]["electrical"] = {"device": letter, "line": line,     # a model type the letter accepts
                                  "models": [f".model {line.split()[-1]} {MODEL_TYPE[letter]}"], **elec}
    return m


MODEL_TYPE = {"Q": "NPN", "M": "NMOS", "J": "NJF", "R": "NPN"}
Q3 = ("Q", "cbe", "{dev} {c} {b} {e} qm")
M4 = ("M", "dgsb", "{dev} {d} {g} {s} {b} nm")


@pytest.mark.parametrize("letter,pins,line", [Q3, M4, ("J", "dgs", "{dev} {d} {g} {s} jm"),
                                              ("Q", "cbes", "{dev} {c} {b} {e} {s} qm")])
def test_multi_terminal_valid(extra_root, letter, pins, line):
    write_def(extra_root, "MT", mutate=_multi(letter, pins, line))
    assert CATALOG.problems == [] and CATALOG.get("MT").terminals == tuple(pins)


@pytest.mark.parametrize("letter,pins,line,msg", [
    ("Q", "ce", "{dev} {c} {e} qm", "a Q device has 3 or 4 terminals; this part has 2 pins"),
    ("M", "dgs", "{dev} {d} {g} {s} nm", "a M device has 4 terminals; this part has 3 pins"),
    ("J", "dgsb", "{dev} {d} {g} {s} {b} jm", "a J device has 3 terminals; this part has 4 pins"),
    ("R", "abc", "{dev} {a} {b} {c}", "a R device has 2 terminals; this part has 3 pins")])
def test_wrong_pin_count_rejected(extra_root, letter, pins, line, msg):
    write_def(extra_root, "MT", mutate=_multi(letter, pins, line))
    assert any(msg in p["message"] for p in problems("DEF-SIM", "sim.electrical.line")), CATALOG.problems
    assert "MT" not in CATALOG.defs


@pytest.mark.parametrize("line", [
    "{dev} {c} {b} qm",              # missing {e}
    "{dev} {c} {b} 0 qm",            # literal node
    "{dev} {c} {b} {b} qm",          # duplicate
    "{dev} {c} {b} {e} {c} qm",      # pin in the tail
])
def test_multi_terminal_node_check(extra_root, line):
    write_def(extra_root, "MT", mutate=_multi("Q", "cbe", line))
    assert problems("DEF-SIM", "sim.electrical.line") and "MT" not in CATALOG.defs


@pytest.mark.parametrize("letter,pins,line", [
    ("Q", "cbe", "{dev} {c} {b} {e} n1 qm"),         # ngspice: n1 = substrate, a hidden link to drawn net n1
    ("M", "dgsb", "{dev} {d} {g} {s} {b} n1 qm"),    # ngspice: n1 = SOI node
    ("Q", "cbe", "{dev} {c} {b} {e} qother"),        # model not declared by this part
])
def test_multi_terminal_field_after_nodes_must_be_own_model(extra_root, letter, pins, line):
    def m(d):
        _multi(letter, pins, line)(d)
        d["sim"]["electrical"]["models"] = [".model qm NPN"]
    write_def(extra_root, "MT", mutate=m)
    assert problems("DEF-SIM", "sim.electrical.line") and "MT" not in CATALOG.defs


def test_multi_terminal_node_order_is_the_authors(extra_root):
    write_def(extra_root, "MT", mutate=_multi("Q", "cbe", "{dev} {e} {b} {c} qm"))     # any order of the pins is fine
    assert CATALOG.problems == []


@pytest.mark.parametrize("letter,pins,line,cur,ok", [
    ("Q", "cbe", "{dev} {c} {b} {e} qm", "@{dev_lc}[ib]", False), ("Q", "cbe", "{dev} {c} {b} {e} qm", "@{dev_lc}[ic]", True),
    ("M", "dgsb", "{dev} {d} {g} {s} {b} nm", "@{dev_lc}[is]", False), ("M", "dgsb", "{dev} {d} {g} {s} {b} nm", "@{dev_lc}[id]", True),
    ("Q", "cbe", "{dev} {c} {b} {e} qm", "i({dev_lc})", False)])
def test_multi_terminal_current_restricted(extra_root, letter, pins, line, cur, ok):
    write_def(extra_root, "MT", mutate=_multi(letter, pins, line, current=cur))
    assert bool(problems("DEF-SIM", "sim.electrical.current")) is not ok and ("MT" in CATALOG.defs) is ok


def test_multi_terminal_netlist_and_current_pin(extra_root):
    write_def(extra_root, "QT", mutate=_multi("Q", "cbe", "{dev} {e} {b} {c} qm", current="@{dev_lc}[ic]",
                                              models=[".model qm NPN(Bf=100)"]))
    write_def(extra_root, "MT", mutate=_multi("M", "dgsb", "{dev} {d} {g} {s} {b} nm", current="@{dev_lc}[id]",
                                              models=[".model nm NMOS(Level=1)"]))
    assert CATALOG.problems == []
    d = Document()
    qn, mn = d.add_part("QT"), d.add_part("MT"); d.add_part("GND")
    for p in [*d.pins(qn), *d.pins(mn)]:
        d.connect(p, "GND1.g")
    nl = d.netlist()
    q, m = (s for s in nl["signals"] if s["kind"] == "current")
    assert nl["lines"] == [f"Q{qn} 0 0 0 qm", f"M{mn} 0 0 0 0 nm"]
    assert nl["saves"][0].startswith(".save all @q") and nl["saves"][0].endswith("[ic]")
    assert nl["saves"][1].endswith("[id]")
    assert q["pins"] == [f"{q['part']}.e"]          # first node field of the template, not pins[0] (= c)
    assert m["pins"] == [f"{m['part']}.d"]
    assert not [s for s in nl["signals"] if s["kind"] == "across"]
