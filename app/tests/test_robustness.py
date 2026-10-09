"""Hostile / malformed input at the model boundary (docs/review/attack-findings.md B3-B6): every bad file raises ONE
clear error type (ModelError, message starts 'bad simulation file' or 'cannot open'), names are safe, runs are bounded."""
import copy, json
import pytest
from app.model import Document, ModelError, MAX_POINTS, MAX_SAMPLES
from app.sim import run_document


def good():
    return Document.led_example().to_dict()


def mutate(f):
    d = good(); f(d); return d


BAD = {
    "conn ints": lambda d: d.update(connections=[[1, 2]]),
    "conn not list": lambda d: d.update(connections=5),
    "conn 3": lambda d: d.update(connections=[["R1.a", "R1.b", "D1.a"]]),
    "self wire": lambda d: d.update(connections=[["R1.a", "R1.a"]]),
    "dup wire": lambda d: d.update(connections=[["R1.a", "R1.b"], ["R1.a", "R1.b"]]),
    "rev dup wire": lambda d: d.update(connections=[["R1.a", "R1.b"], ["R1.b", "R1.a"]]),
    "dangling": lambda d: d.update(connections=[["R1.a", "R9.b"]]),
    "NaN x": lambda d: d["layout"]["R1"].update(x=float("nan")),
    "inf x": lambda d: d["layout"]["R1"].update(x=float("inf")),
    "huge x": lambda d: d["layout"]["R1"].update(x=1e308),
    "bool x": lambda d: d["layout"]["R1"].update(x=True),
    "float rot": lambda d: d["layout"]["R1"].update(rotation=90.0),
    "unknown type": lambda d: d["components"]["R1"].update(type="XYZ"),
    "type list": lambda d: d["components"]["R1"].update(type=["R"]),
    "params list": lambda d: d["components"]["R1"].update(params=[1]),
    "param nested": lambda d: d["components"]["R1"]["params"].update(R=[1]),
    "components list": lambda d: d.update(components=[]),
    "layout list": lambda d: d.update(layout=[]),
    "simulation list": lambda d: d.update(simulation=[]),
    "version float": lambda d: d.update(version=1.0),
    "version bool": lambda d: d.update(version=True),
    "name space": lambda d: d["components"].update({"my R": d["components"].pop("R1")}),
    "name empty": lambda d: d["components"].update({"": d["components"].pop("R1")}),
    "name newline": lambda d: d["components"].update({"I1 n1 0 DC 0\nV9 a 0 1": d["components"].pop("R1")}),
    "name dot": lambda d: d["components"].update({"R.1": d["components"].pop("R1")}),
    "name digit first": lambda d: d["components"].update({"1R": d["components"].pop("R1")}),
}


@pytest.mark.parametrize("name", BAD)
def test_bad_file_is_one_clear_error(name, tmp_path):
    d = mutate(BAD[name])
    p = tmp_path / "x.json"; p.write_text(json.dumps(d))     # NaN/Infinity are written as the JS-ish tokens
    with pytest.raises(ModelError, match="bad simulation file|cannot open"):
        Document.load(p)


def test_deep_nesting_and_duplicate_keys(tmp_path):
    p = tmp_path / "deep.json"; p.write_text("[" * 100000 + "]" * 100000)
    with pytest.raises(ModelError, match="cannot open|bad simulation file"):
        Document.load(p)
    p.write_text('{"format": "sim-circuit", "version": 1, "components": {"R1": {"type": "R", "params": {}}, '
                 '"R1": {"type": "R", "params": {}}}, "connections": [], "simulation": {"tstop": "1m", "tstep": "1u"}}')
    with pytest.raises(ModelError, match="duplicate"):
        Document.load(p)


def test_good_file_still_loads_and_roundtrips(tmp_path):
    d = Document.led_example(); p = tmp_path / "ok.json"; d.save(p)
    assert Document.load(p).to_dict() == d.to_dict()


@pytest.mark.parametrize("name", ["R2", "Rload", "r_1", "GND9"])
def test_rename_accepts_safe_names(name):
    d = Document.led_example(); d.rename_part("R1", name)
    assert name in d.parts and "R1" not in d.parts
    assert ("V1.p", f"{name}.a") in d.wires and run_document(d).ok


@pytest.mark.parametrize("name", ["", " ", "my R", "R1\nV9 a 0 1", "a.b", "1R", "R;1", "D1", "é"])
def test_rename_rejects_unsafe_or_duplicate(name):
    d = Document.led_example()
    with pytest.raises(ModelError, match="name"):
        d.rename_part("R1", name)
    assert "R1" in d.parts


def test_run_size_limit_has_user_message_and_is_fast():
    d = Document.led_example(); d.tstop, d.tstep = "50", "10u"          # 5e6 points: froze the UI for 26 s
    with pytest.raises(ModelError, match=r"too many points.*increase the time step"):
        d.config()
    r = run_document(d)
    assert not r.ok and "too many points" in r.message and "BAD-CONFIG" in r.message
    assert "ModelError" in r.details or "ValidationError" in r.details


def test_run_size_limit_scales_with_signal_count_and_keeps_examples():
    big = Document.led_example()
    big.tstop, big.tstep = repr(MAX_POINTS * 1e-6), "1u"                 # exactly at the point limit: fine
    big.config()
    big.tstop = repr((MAX_POINTS + 1) * 1e-6)
    with pytest.raises(ModelError): big.config()
    for ex in (Document.led_example(), Document.blink_example()):
        ex.config()


def test_sample_limit_counts_signals():
    d = Document()
    for _ in range(20): d.add_part("R")
    d.tstop, d.tstep = repr(MAX_POINTS / 2 * 1e-6), "1u"         # 50k points is fine for 4 parts, not for 20
    assert 3 * 20 * MAX_POINTS / 2 > MAX_SAMPLES
    with pytest.raises(ModelError, match="too many points"): d.config()
