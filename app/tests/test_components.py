"""Qt-free tests: component catalog contract, SI units, netlist emission, validation, save format v1."""
import json
import pytest
from app import catalog as cat
from app.catalog import CATALOG
from app.components import ROTATIONS, ModelError, parse_si, format_si
from app import electrical
from app.model import Document
from app.sim import run_document


# ---------------- SI units ----------------
@pytest.mark.parametrize("text,unit,want", [
    ("1k", "Ω", 1e3), ("10u", "F", 1e-5), ("4.7n", "F", 4.7e-9), ("1meg", "Ω", 1e6), ("1MEG", "Ω", 1e6),
    ("2.2M", "Ω", 2.2e6), ("5m", "s", 5e-3), ("10 µF", "F", 1e-5), ("10 μF", "F", 1e-5), ("1kΩ", "Ω", 1e3),
    ("1 kohm", "Ω", 1e3), ("330 ohm", "Ω", 330), ("5 V", "V", 5), ("1F", "F", 1.0), ("1f", "F", 1e-15),
    ("1e-6", "F", 1e-6), ("-2.5", "V", -2.5), (".5", "", 0.5), ("1kHz", "Hz", 1e3), ("100 p", "F", 1e-10)])
def test_parse_si(text, unit, want):
    assert parse_si(text, unit) == pytest.approx(want, rel=1e-15)


def test_parse_si_exact_decimal():
    assert parse_si("10u") == 1e-05 and parse_si("4.7n") == 4.7e-09   # no 9.999e-06 artefacts sent to the core


@pytest.mark.parametrize("text,unit,needle", [
    ("abc", "Ω", "not a number"), ("", "Ω", "not a number"), ("1x", "Ω", "not a number"), ("5 V", "Ω", "does not fit"),
    ("1kF", "Ω", "does not fit"), ("nan", "", "finite"), ("inf", "", "finite"), ("1..2", "", "not a number")])
def test_parse_si_errors(text, unit, needle):
    with pytest.raises(ModelError, match=needle):
        parse_si(text, unit, "R1 R")


@pytest.mark.parametrize("x,unit,want", [(1000, "Ω", "1 kΩ"), (1e-5, "F", "10 µF"), (4.7e-9, "F", "4.7 nF"),
                                         (5, "V", "5 V"), (0, "V", "0 V"), (1e9, "Ω", "1 GΩ"), (-0.002, "A", "-2 mA")])
def test_format_si(x, unit, want):
    assert format_si(x, unit) == want
    assert parse_si(want, unit) == pytest.approx(x)   # what we display is accepted back as input


# ---------------- catalog contract ----------------
REQUIRED = {"GND", "R", "C", "L", "V", "I", "D", "LED", "SW"}


def test_catalog_covers_milestone_set():
    assert REQUIRED <= set(CATALOG.defs)


@pytest.mark.parametrize("kind", sorted(k for k, d in CATALOG.defs.items() if d.simulated))   # interface-only seeds: see test_seed_packages
def test_catalog_entry_contract(kind):
    c = CATALOG.get(kind)
    assert c.id == kind and c.name and c.designator and c.terminals
    assert (c.symbol_for(c.defaults()) is not None) and c.simulated and c.version >= 1
    assert set(c.pos) == set(c.terminals) and len(c.box) == 4
    keys = [p.key for p in c.params]
    assert len(keys) == len(set(keys))
    vals = c.values(c.defaults(), "X1")              # every default is valid input
    assert set(vals) == set(c.fields(c.defaults()))
    for p in c.params:
        assert p.label and p.kind in ("number", "choice")
        if p.kind == "choice": assert p.default in p.choices
    sec = c.sim["electrical"]
    if c.ground:
        assert "device" not in sec
    else:
        lines, models = electrical.emit(sec, c.designator + "1", {t: f"n{i + 1}" for i, t in enumerate(c.terminals)}, vals)
        assert lines and all(l.upper().startswith(sec["device"]) for l in lines)
    for r in ROTATIONS:                          # rotation keeps terminals at distinct positions
        offs = [cat.terminal_offset(kind, t, r) for t in c.terminals]
        assert len(set(offs)) == len(offs)


def test_rotation_math():
    # R pins at (0,15)/(60,15), body centre (30,15): 90 deg cw puts a on top, b at the bottom
    assert cat.terminal_offset("R", "a", 90) == (30, -15) and cat.terminal_offset("R", "b", 90) == (30, 45)
    assert cat.terminal_offset("R", "a", 180) == (60, 15)
    assert cat.terminal_offset("R", "a", 360 - 90) == (30, 45)


def test_param_bounds_messages():
    d = Document(); d.add_part("R")
    with pytest.raises(ModelError, match=r"R1 R: must be > 0"): d.check_param("R1", "R", "0")
    with pytest.raises(ModelError, match=r"must be one of open, closed"):
        d.add_part("SW"); d.check_param("SW1", "state", "half")
    assert d.check_param("R1", "R", "2.2k") == 2200.0
    with pytest.raises(ModelError, match="no parameter"): d.add_part("R", R="1", bogus="2")


def test_summary():
    assert cat.summary("R", {"R": "1k"}) == "1 kΩ"
    assert cat.summary("C", {"C": "1e-6", "ic": "0"}) == "1 µF"
    assert cat.summary("V", dict(CATALOG.get("V").defaults(), mode="PULSE")) == "PULSE"
    assert cat.summary("SW", CATALOG.get("SW").defaults()) == "open"
    assert cat.summary("R", {"R": "oops"}) == "oops" and cat.summary("GND", {}) == ""


# ---------------- netlist emission ----------------
def test_netlist_emission_all_kinds():
    d = Document()
    for k in ("V", "R", "C", "L", "I", "D", "LED", "SW", "BAT"):
        d.add_part(k)
    d.add_part("GND")
    for n in list(d.parts)[:-1]:            # every part between its own node and ground
        a, b = d.pins(n)
        d.connect(b, "GND1.g")
    d.set_param("R1", "R", "4.7k"); d.set_param("C1", "C", "10u"); d.set_param("SW1", "state", "closed")
    nl = d.netlist()
    assert nl["lines"] == ["V1 n1 0 DC 5.0", "R1 n2 0 4700.0", "C1 n3 0 1e-05 IC=0.0", "L1 n4 0 0.001 IC=0.0",
                           "I1 n5 0 DC 0.001", "D1 n6 0 dmod_d1", "D2 n7 0 dled", "RSW1 n8 0 0.01", "V2 n9 0 DC 5.0"]
    assert nl["models"] == [".model dmod_d1 D(Is=1e-14 N=1.0 Rs=0.0)", ".model dled D(Is=1e-20 N=1.8 Rs=5)"]
    assert nl["saves"] == [f".save all {v}" for v in ("@r1[i]", "@c1[i]", "@l1[i]", "@d1[id]", "@d2[id]", "@rsw1[i]")]
    assert nl["observables"][:9] == [f"v(n{i})" for i in range(1, 10)]
    assert "i(v1)" in nl["observables"] and "i(v2)" in nl["observables"]
    sig = {s["name"]: s for s in nl["signals"]}
    assert sig["V(C1)"]["vector"] == "v(C1)" and sig["V(C1)"]["kind"] == "across"
    assert sig["I(SW1)"]["vector"] == "@rsw1[i]" and sig["I(R1)"]["unit"] == "A"
    assert sig["V(n3)"]["pins"] == ["C1.a"] and sig["I(V1)"]["vector"] == "i(v1)"
    d.set_param("SW1", "state", "open")
    assert "RSW1 n8 0 1000000000.0" in d.netlist()["lines"]
    d.set_param("V1", "mode", "PULSE")
    assert "V1 n1 0 PULSE(0.0 5.0 0.0 1e-06 1e-06 0.001 0.002)" in d.netlist()["lines"]


def test_template_netlist_matches_golden():
    """Literal lines captured from the pre-catalog emit code (complements test_netlist_emission_all_kinds);
    fixtures/netlist_golden.json pins the full dict for every kind and mode (see test_catalog)."""
    def one(kind, **p):
        d = Document(); n = d.add_part(kind, **p); d.add_part("GND"); d.connect(d.pins(n)[-1], "GND1.g")
        return d.netlist()
    assert one("V", mode="PULSE", v1=0, v2=3.3, td="1u", tr="2u", tf="5u", pw="1m", per="3m")["lines"] == \
        ["V1 n1 0 PULSE(0.0 3.3 1e-06 2e-06 5e-06 0.001 0.003)"]
    assert one("V", mode="SIN", vo="0.5", va="1", freq="2k")["lines"] == ["V1 n1 0 SIN(0.5 1.0 2000.0)"]
    assert one("SW")["lines"] == ["RSW1 n1 0 1000000000.0"]
    assert one("SW", state="closed", ron="0.1")["lines"] == ["RSW1 n1 0 0.1"]
    nl = one("D", Is="2e-14", N="1.5", Rs="10")
    assert nl["lines"] == ["D1 n1 0 dmod_d1"] and nl["models"] == [".model dmod_d1 D(Is=2e-14 N=1.5 Rs=10.0)"]
    assert one("C", C="4.7n", ic="2")["lines"] == ["C1 n1 0 4.7e-09 IC=2.0"]
    assert one("L", L="10m", ic="1m")["lines"] == ["L1 n1 0 0.01 IC=0.001"]


def test_netlist_bad_value_raises():
    d = Document(); d.add_part("R", R="-1"); d.add_part("GND"); d.connect("R1.a", "GND1.g")
    with pytest.raises(ModelError, match="R1 R: must be > 0"):
        d.netlist()


# ---------------- pre-run validation ----------------
def codes(doc, sev=None):
    return [x["code"] for x in doc.validate() if sev is None or x["severity"] == sev]


def test_validate_empty_and_ground():
    d = Document()
    assert codes(d) == ["EMPTY"]
    d.add_part("R"); d.add_part("V")
    d.connect("V1.p", "R1.a"); d.connect("V1.n", "R1.b")
    assert codes(d, "error") == ["NO-GROUND"]
    d.add_part("GND")                                    # placed but unwired: still no ground
    assert "NO-GROUND" in codes(d, "error")
    d.connect("GND1.g", "R1.b")
    assert codes(d) == []


def test_validate_unconnected_and_params():
    d = Document(); d.add_part("V"); d.add_part("R"); d.add_part("GND"); d.add_part("C")
    d.connect("V1.p", "R1.a"); d.connect("V1.n", "GND1.g")
    d.set_param("R1", "R", "abc"); d.tstop = "0"
    ds = d.validate()
    errs = [(x["code"], x["part"], x["param"]) for x in ds if x["severity"] == "error"]
    assert errs == [("BAD-CONFIG", None, None), ("BAD-PARAM", "R1", "R"), ("UNCONNECTED-PART", "C1", None)]
    warn = [x["pins"] for x in ds if x["code"] == "UNCONNECTED-PIN"]
    assert warn == [["R1.b"]]
    assert ds[-1]["severity"] == "warning"               # errors first


def test_run_document_returns_structured_failure():
    d = Document(); d.add_part("R"); d.add_part("V"); d.connect("V1.p", "R1.a"); d.connect("V1.n", "R1.b")
    res = run_document(d)
    assert not res.ok and res.state == "blocked" and res.status == "blocked" and res.message.startswith("[NO-GROUND]")
    assert res.diagnostics[0]["code"] == "NO-GROUND" and "NO-GROUND" in res.details


def test_run_document_core_failure_keeps_details():
    # passes app checks; the core rejects it (capacitor-only node has no DC path)
    d = Document(); d.add_part("V"); d.add_part("C"); d.add_part("C"); d.add_part("GND")
    for a, b in (("V1.p", "C1.a"), ("C1.b", "C2.a"), ("C2.b", "GND1.g"), ("V1.n", "GND1.g")):
        d.connect(a, b)
    res = run_document(d)
    assert not res.ok and "NO-DC-PATH" in res.message
    assert "netlist sent to the core" in res.details and "C1 n1 n2" in res.details


# ---------------- save format v1 ----------------
def sample():
    d = Document()
    d.add_part("V", 40, 100); d.add_part("R", 160, 60, rot=90, R="1k"); d.add_part("C", 300, 140, C="10u")
    d.add_part("SW", 200, 0, state="closed"); d.add_part("GND", 200, 260)
    for a, b in (("V1.p", "SW1.a"), ("SW1.b", "R1.a"), ("R1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g")):
        d.connect(a, b)
    d.rotate("C1"); d.rotate("C1", 180)
    d.tstop, d.tstep = "5m", "10u"
    return d


def test_v1_schema():
    t = sample().to_dict()
    assert t["format"] == "sim-circuit" and t["version"] == 2
    assert set(t) == {"format", "version", "components", "connections", "simulation", "layout"}
    assert t["components"]["R1"] == {"type": "R", "version": 1, "params": {"R": "1k"}}
    assert t["layout"]["R1"] == {"x": 160, "y": 60, "rotation": 90} and t["layout"]["C1"]["rotation"] == 270
    assert t["simulation"] == {"analysis": "transient", "tstop": "5m", "tstep": "10u"}
    assert ["R1.b", "C1.a"] in t["connections"]


def test_save_load_roundtrip_and_save_as(tmp_path):
    d = sample(); p1, p2 = tmp_path / "a.json", tmp_path / "b.json"
    d.save(p1)
    assert d.path == str(p1)
    d2 = Document.load(p1)
    assert d2.to_dict() == d.to_dict() and d2.parts == d.parts and d2.path == str(p1)
    assert d2.netlist() == d.netlist()
    d2.save(p2)                                          # Save As
    assert d2.path == str(p2) and json.loads(p2.read_text()) == json.loads(p1.read_text())
    assert d2.add_part("R") == "R2" and d2.add_part("SW") == "SW2"   # auto-naming continues after load
    a, b = run_document(d), run_document(Document.load(p1))
    assert a.ok and b.ok and a.series == b.series


def test_load_v0_unversioned(tmp_path):
    v0 = {"parts": {"V1": {"kind": "BAT", "x": 40, "y": 100, "params": {"dc": "5"}},
                    "R1": {"kind": "R", "x": 180, "y": 60, "params": {"R": "330"}},
                    "D1": {"kind": "D", "x": 320, "y": 100, "params": {}},
                    "GND1": {"kind": "GND", "x": 440, "y": 220, "params": {}}},
          "wires": [["V1.p", "R1.a"], ["R1.b", "D1.a"], ["D1.k", "GND1.g"], ["V1.n", "GND1.g"]],
          "tstop": "1e-3", "tstep": "1e-5"}
    p = tmp_path / "old.json"; p.write_text(json.dumps(v0))
    d = Document.load(p)
    assert d.parts["R1"]["rot"] == 0 and d.parts["D1"]["params"] == CATALOG.get("D").defaults()
    assert d.to_dict()["version"] == 2 and d.tstop == "1e-3"
    assert run_document(d).ok


@pytest.mark.parametrize("mutate,needle", [
    (lambda t: t.update(version=3), "newer"),
    (lambda t: t.pop("format"), "not a simulation file"),
    (lambda t: t["layout"]["R1"].update(rotation=45), "bad layout"),
    (lambda t: t["connections"].append(["Q9.a", "C1.a"]), "no such pin"),
    (lambda t: t.pop("simulation"), "simulation"),
])
def test_load_rejects_bad_files(tmp_path, mutate, needle):
    t = sample().to_dict(); mutate(t)
    p = tmp_path / "bad.json"; p.write_text(json.dumps(t))
    with pytest.raises(ModelError, match=needle):
        Document.load(p)


def test_load_non_object_and_missing(tmp_path):
    p = tmp_path / "x.json"; p.write_text("[1, 2]")
    with pytest.raises(ModelError, match="not a JSON object"): Document.load(p)
    with pytest.raises(ModelError, match="cannot open"): Document.load(tmp_path / "missing.json")


def test_save_failure_is_modelerror_and_keeps_old_file(tmp_path):
    d = sample()
    with pytest.raises(ModelError, match="cannot save"):
        d.save(tmp_path / "no-such-dir" / "x.json")
    assert d.path is None
    with pytest.raises(ModelError, match="no file name"):
        Document().save()
    p = tmp_path / "keep.json"; d.save(p); before = p.read_text()
    d.parts["R1"]["params"]["R"] = object()              # unserialisable: json.dump raises TypeError mid-write
    with pytest.raises(TypeError):
        d.save(p)
    assert p.read_text() == before                       # atomic write: original intact
