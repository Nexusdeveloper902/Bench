"""Persistence v2 (docs/persistence.md): v1 migration, interface-only parts, placeholders for missing/incompatible
definitions. Real catalog, real files in tmp_path, real engine; no mocks."""
import json
import pytest
from conftest import write_def
from app import catalog
from app.history import History
from app.model import Document, ModelError
from app.sim import run_document

# audit 01-audit.md section 5: what format v1 looked like on disk (LED example)
V1_LED = {"format": "sim-circuit", "version": 1,
          "components": {"V1": {"type": "BAT", "params": {"dc": "5"}},
                         "R1": {"type": "R", "params": {"R": "330"}},
                         "D1": {"type": "LED", "params": {}},
                         "GND1": {"type": "GND", "params": {}}},
          "connections": [["V1.p", "R1.a"], ["R1.b", "D1.a"], ["D1.k", "GND1.g"], ["V1.n", "GND1.g"]],
          "simulation": {"analysis": "transient", "tstop": "5m", "tstep": "10u"},
          "layout": {"V1": {"x": 40, "y": 100, "rotation": 0}, "R1": {"x": 180, "y": 60, "rotation": 0},
                     "D1": {"x": 320, "y": 100, "rotation": 0}, "GND1": {"x": 440, "y": 220, "rotation": 0}}}
# a v2 file holding a part type this catalog does not know (plan section 8)
FUTURE = {**V1_LED, "version": 2,
          "components": {**{n: {"type": c["type"], "version": 1, "params": c["params"]}
                            for n, c in V1_LED["components"].items()},
                         "U2": {"type": "FUTURE_RELAY", "version": 3, "params": {"coil": "5"}}},
          "connections": V1_LED["connections"] + [["U2.COM", "U2.NO"]],
          "layout": {**V1_LED["layout"], "U2": {"x": 600, "y": 500, "rotation": 90}}}


def board(root, **top):
    """Interface-only ESP32-like definition (version 2, one parameter, no sim:* capability)."""
    def mutate(d):
        d["capabilities"] = ["interface"]; d.pop("sim")
        d["pins"] = [{"id": "3V3", "at": [0, 20]}, {"id": "GND", "at": [0, 40]},
                     {"id": "TX", "at": [80, 20]}, {"id": "RX", "at": [80, 40]}]
        d["params"] = [{"key": "clock", "label": "Clock", "unit": "Hz", "default": "80M"}]
        d["presentation"] = {"box": [0, 0, 80, 60]}
        d["limitations"] = ["No MCU emulation."]
        d.update(top)
    return write_def(root, "TESTBOARD", mutate, version=2)


def dump(p, d):
    p.write_text(json.dumps(d, indent=1))
    return p


def test_v1_file_migrates(tmp_path):
    d = Document.load(dump(tmp_path / "v1.json", V1_LED))
    out = d.to_dict()
    assert out["version"] == 2
    assert {n: c["version"] for n, c in out["components"].items()} == dict.fromkeys(V1_LED["components"], 1)
    assert {n: (c["type"], c["params"]) for n, c in out["components"].items()} == \
           {n: (c["type"], c["params"]) for n, c in V1_LED["components"].items()}
    assert out["connections"] == V1_LED["connections"] and out["layout"] == V1_LED["layout"]
    assert out["simulation"] == V1_LED["simulation"]
    # identical simulation to the same circuit built in code
    a, b = run_document(d), run_document(Document.led_example())
    assert a.ok and a.series == b.series and a.excluded == []
    assert a.stats("I(D1)")["final"] == pytest.approx(9.1772e-3, rel=1e-3)
    assert (tmp_path / "v1.json").read_text() == json.dumps(V1_LED, indent=1)   # migration is in memory only


def test_v2_component_needs_version(tmp_path):
    bad = json.loads(json.dumps(FUTURE)); del bad["components"]["R1"]["version"]
    with pytest.raises(ModelError, match="R1"):
        Document.load(dump(tmp_path / "x.json", bad))


def test_roundtrip_led_plus_interface_only_part(extra_root):
    board(extra_root)
    d = Document.led_example()
    u = d.add_part("TESTBOARD", 600, 40, rot=90, clock="240M")
    assert u == "U1"
    p1, p2 = extra_root / "a.json", extra_root / "b.json"
    d.save(p1)
    d2 = Document.load(p1)
    d2.save(p2)
    assert p1.read_bytes() == p2.read_bytes()
    rec = json.loads(p1.read_text())
    assert rec["components"]["U1"] == {"type": "TESTBOARD", "version": 2, "params": {"clock": "240M"}}
    assert rec["layout"]["U1"] == {"x": 600, "y": 40, "rotation": 90}
    assert d2.parts["U1"] == d.parts["U1"] and d2.definition("U1").readiness == "interface"
    assert "placeholder" not in d2.parts["U1"] and d2.pins("U1") == ["U1.3V3", "U1.GND", "U1.TX", "U1.RX"]


def test_missing_definition_kept_verbatim(tmp_path):
    p = dump(tmp_path / "f.json", FUTURE)
    d = Document.load(p)
    u = d.parts["U2"]
    assert (u["kind"], u["version"], u["params"]) == ("FUTURE_RELAY", 3, {"coil": "5"})
    assert u["placeholder"]["reason"] == "missing" and u["placeholder"]["pins"] == ["COM", "NO"]
    assert d.definition("U2") is None and d.unmodeled() == ["U2"] and d.placeholders() == ["U2"]
    d.save(tmp_path / "g.json")
    assert (tmp_path / "g.json").read_text() == p.read_text()          # byte for byte, U2 record included
    rec = json.loads((tmp_path / "g.json").read_text())
    assert rec["components"]["U2"] == FUTURE["components"]["U2"] and ["U2.COM", "U2.NO"] in rec["connections"]


def test_placeholder_geometry_and_pins(tmp_path):
    d = Document.load(dump(tmp_path / "f.json", FUTURE))
    box, pos = catalog.geometry(d.parts["U2"])
    assert box == (0, 0, 80, 80) and pos == {"COM": (0, 20), "NO": (0, 40)}
    assert catalog.pin_offset({**d.parts["U2"], "rot": 0}, "NO") == (0, 40)
    assert catalog.pin_offset(d.parts["U2"], "NO") == (40, 0)    # rot 90 about the body centre (40, 40)
    d.connect("U2.COM", "R1.a")                       # can still be wired to an existing pin
    with pytest.raises(ModelError, match="no such pin"):
        d.connect("U2.XX", "R1.a")


def test_newer_version_and_unknown_param_become_incompatible(tmp_path):
    t = json.loads(json.dumps(FUTURE))
    t["components"]["D1"]["version"] = 2                        # LED is version 1 here
    t["components"]["R1"]["params"]["bogus"] = "1"
    t["components"]["V1"]["params"]["dc"] = 5                   # a normal part stores numbers as text
    t["connections"].append(["GND1.zz", "V1.n"])                # a pin GND does not have
    d = Document.load(dump(tmp_path / "x.json", t))
    why = {n: d.parts[n]["placeholder"] for n in d.placeholders()}
    assert set(why) == {"U2", "D1", "R1", "GND1"} and all(w["reason"] == "incompatible" for n, w in why.items() if n != "U2")
    assert "version 2" in why["D1"]["detail"] and "'bogus'" in why["R1"]["detail"] and "'zz'" in why["GND1"]["detail"]
    assert d.parts["V1"]["params"] == {"dc": "5"} and "placeholder" not in d.parts["V1"]
    out = d.to_dict()["components"]
    assert out["D1"] == t["components"]["D1"] and out["R1"] == t["components"]["R1"]
    assert d.pins("GND1") == ["GND1.g", "GND1.zz"]


def test_alias_is_not_resolved(extra_root, tmp_path):
    board(extra_root, aliases=["ESP32"])
    assert catalog.CATALOG.search("ESP32")                      # searchable by alias ...
    t = json.loads(json.dumps(FUTURE)); t["components"]["U2"] = {"type": "ESP32", "version": 1, "params": {}}
    d = Document.load(dump(tmp_path / "x.json", t))
    assert d.parts["U2"]["kind"] == "ESP32" and d.parts["U2"]["placeholder"]["reason"] == "missing"   # ... never resolved


def test_unknown_type_cannot_be_added():
    with pytest.raises(ModelError, match="unknown component type"):
        Document().add_part("FUTURE_RELAY")


def test_placeholder_params_read_only_and_undo_redo_preserves_it(tmp_path):
    d = Document.load(dump(tmp_path / "f.json", FUTURE))
    for call in (lambda: d.set_param("U2", "coil", "9"), lambda: d.check_param("U2", "coil")):
        with pytest.raises(ModelError, match="U2 has no usable definition; its parameters are read-only"):
            call()
    h, before = History(), d.to_dict()
    with h.change(d): d.parts["U2"]["x"] = 700; d.rotate("U2")
    with h.change(d): d.remove_wire(("U2.COM", "U2.NO"))
    with h.change(d): d.remove_part("R1")
    assert h.undo(d) and h.undo(d) and "placeholder" in d.parts["U2"] and d.parts["U2"]["x"] == 700
    assert d.parts["U2"]["placeholder"]["pins"] == ["COM", "NO"] and ("U2.COM", "U2.NO") in d.wires
    assert h.undo(d) and d.to_dict() == before
    assert h.redo(d) and d.parts["U2"]["rot"] == 180 and d.parts["U2"]["params"] == {"coil": "5"}
    assert d.parts["U2"]["params"] is not FUTURE["components"]["U2"]["params"]   # history never aliases raw input
