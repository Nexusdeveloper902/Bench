"""Seed packages (T7): real pinouts, interface-only parts that are never simulated, and one electrical part whose
model is data only. Sources are cited in each package's `docs`; docs/adding-components.md examples must validate."""
import re
from pathlib import Path

import pytest

from app.catalog import BUILTIN_ROOT, CATALOG, SIM_DOMAINS, load_definition
from app.model import Document
from app.sim import run_document
from test_analytic_diode import final, shockley   # the Shockley oracle shared with the diode tests

SEEDS = {"ESP32_DEVKITC", "ARDUINO_UNO_R3", "DHT22", "SSD1306_I2C", "PIEZO_BUZZER", "D_1N4148"}
INTERFACE_SEEDS = SEEDS - {"D_1N4148"}
DOC = Path(__file__).resolve().parents[2] / "docs" / "adding-components.md"


def test_seeds_load_cleanly():
    assert CATALOG.problems == []                       # no error and no warning from any package
    assert SEEDS <= set(CATALOG.defs)
    for sid in SEEDS:
        d = CATALOG.get(sid)
        assert d.path.is_relative_to(BUILTIN_ROOT) and (d.path / "component.toml").is_file(), sid
        assert d.docs and all(u.startswith("https://") for u in d.docs), f"{sid} must cite its source URL"


def test_esp32_pins():
    d = CATALOG.get("ESP32_DEVKITC")
    assert len(d.pins) == 38 and len(set(d.terminals)) == 38
    j2 = sorted((p for p in d.pins if p.at[0] == 0), key=lambda p: p.at[1])
    j3 = sorted((p for p in d.pins if p.at[0] == 160), key=lambda p: p.at[1])
    assert len(j2) == 19 and len(j3) == 19
    assert [p.id for p in d.pins if p.name == "GND"] == ["GND1", "GND2", "GND3"]
    assert (j2[0].name, j2[-1].name) == ("3V3", "5V")          # J2 pin 1 and pin 19, as printed in the guide
    assert (j3[0].name, j3[-1].name) == ("GND", "CLK")         # J3 pin 1 and pin 19
    assert d.pos["IO2"] == (160, 300) and "GPIO2" in next(p for p in d.pins if p.id == "IO2").description
    assert d.readiness == "interface" and not d.simulated and d.sim == {}


def test_categories_present():
    cats = set(CATALOG.categories())
    assert {"Electrical", "Power", "Microcontrollers", "Sensors", "Displays", "Audio"} <= cats
    assert "Communication" not in cats                  # left empty on purpose: no invented entry


def test_no_seed_claims_unregistered_simulation():
    for sid in SEEDS:
        d = CATALOG.get(sid)
        for cap in d.capabilities:
            if cap.startswith("sim:"):
                assert cap[4:] in SIM_DOMAINS and cap[4:] in d.sim, sid
        if sid in INTERFACE_SEEDS:
            assert not d.simulated and d.sim == {} and d.readiness == "interface", sid
            assert any(line.startswith("Not simulated") for line in d.limitations), sid


def test_1n4148_series_current():
    d = Document()
    d.add_part("V", dc="5"); d.add_part("R", R="1k"); d.add_part("D_1N4148"); d.add_part("GND")
    for a, b in (("V1.p", "R1.a"), ("R1.b", "D1.a"), ("D1.k", "GND1.g"), ("V1.n", "GND1.g")):
        d.connect(a, b)
    d.tstop, d.tstep = "100u", "10u"
    assert d.netlist()["models"] == [".model d1n4148 D(Is=2.52n N=1.752 Rs=0.568)"]
    res = run_document(d)
    assert res.ok, res.message
    i_exp, _ = shockley(5.0, 1e3, 2.52e-9, 1.752, 0.568)     # bisection on V = 5 - 1000 I, Is, N, Rs as on the card
    assert final(res, "I(D1)") == pytest.approx(i_exp, rel=2e-3)


def test_doc_examples_validate(tmp_path):
    blocks = re.findall(r"```toml\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    assert len(blocks) >= 2
    for i, text in enumerate(blocks):
        f = tmp_path / f"example{i}" / "component.toml"
        f.parent.mkdir()
        f.write_text(text, encoding="utf-8")
        d, diags = load_definition(f)
        assert d is not None and not [x for x in diags if x["severity"] == "error"], (i, diags)


# Pinouts transcribed from the sources cited in each package's `docs`, NOT from component.toml (T5). Listed in the
# order printed there; the test sorts the definition's pins by position (column, then row) and compares names.
ESP32_J2 = ["3V3", "EN", "VP", "VN", "IO34", "IO35", "IO32", "IO33", "IO25", "IO26", "IO27", "IO14", "IO12", "GND",
            "IO13", "D2", "D3", "CMD", "5V"]                       # Espressif DevKitC V4 guide, header block J2, rows 1-19
ESP32_J3 = ["GND", "IO23", "IO22", "TX", "RX", "IO21", "GND", "IO19", "IO18", "IO5", "IO17", "IO16", "IO4", "IO0",
            "IO2", "IO15", "D1", "D0", "CLK"]                      # same guide, header block J3, rows 1-19
PINOUTS = {
    "ESP32_DEVKITC": ESP32_J2 + ESP32_J3,
    # Arduino A000066 full pinout: power header, analog header, digital headers (left column, then right column)
    "ARDUINO_UNO_R3": ["NC", "IOREF", "RESET", "+3V3", "+5V", "GND", "GND", "VIN", "A0", "A1", "A2", "A3", "A4", "A5",
                       "D19/SCL", "D18/SDA", "AREF", "GND", "D13", "D12", "D11", "D10", "D9", "D8",
                       "D7", "D6", "D5", "D4", "D3", "D2", "D1/TX", "D0/RX"],
    "DHT22": ["VDD", "DATA", "NC", "GND"],                         # datasheet: pins 1-4 left to right: VDD, DATA, NULL, GND
    "SSD1306_I2C": ["VCC", "GND", "SCL", "SDA"],                   # Shillehtek manual pin table
    "PIEZO_BUZZER": ["Pin 1", "Pin 2"],                            # two terminals, no polarity stated by the datasheet
}


@pytest.mark.parametrize("sid", sorted(PINOUTS))
def test_pinout_matches_cited_source(sid):
    d = CATALOG.get(sid)
    ordered = sorted(d.pins, key=lambda p: p.at)                   # (x, y): left column top to bottom, then right
    assert [p.name for p in ordered] == PINOUTS[sid]
    assert len(set(d.terminals)) == len(d.pins) == len(PINOUTS[sid])


def test_simulated_means_electrical_until_a_domain_extends_the_run(extra_root, monkeypatch):
    """F12/T15, pins today's decision: registering a domain validator in SIM_DOMAINS makes `sim:digital` a valid
    capability, but "simulated" (readiness, the Simulated-only filter, the run) still means sim:electrical. A real
    second domain must also change Definition.simulated/readiness and Document.unmodeled (docs/adding-components.md)."""
    from conftest import write_def
    monkeypatch.setitem(SIM_DOMAINS, "digital", lambda *a: [])
    def logic(d):
        d["capabilities"] = ["interface", "sim:digital"]; del d["sim"]["electrical"]; d["sim"]["digital"] = {}
    write_def(extra_root, "AND2", logic, domain="digital")
    g = CATALOG.get("AND2")
    assert CATALOG.problems == [] and "sim:digital" in g.capabilities
    assert (g.simulated, g.readiness) == (False, "interface") and g not in CATALOG.search(simulated_only=True)
    d = Document.led_example(); u = d.add_part("AND2")
    assert d.unmodeled() == [u]
    r = run_document(d)
    assert r.ok and r.excluded == [u]
