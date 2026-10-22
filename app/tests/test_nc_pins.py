"""G9 and G22: `nc` pins (drawn and wirable, no model port) and the LM741 / LM311 pins that use them."""
import pytest
from conftest import write_def

from app.catalog import CATALOG
from app.model import Document
from app.sim import run_document
from test_library_batch_c import Circuit, opamp


def nc_resistor(extra_root, **top):
    """A resistor with a third pin `n` that has no model port."""
    def mutate(d):
        d["pins"].append({"id": "n", "name": "NC", "at": [30, 0]})
        d["nc"] = ["n"]
        d.update(top)
    return write_def(extra_root, "NC_R", mutate=mutate)


def divider(kind="NC_R"):
    c = Circuit()
    v = c.vdc(10, "")
    r1, r2 = c.add(kind, R="1k"), c.add("R", R="1k")
    c.w(f"{v}.p", f"{r1}.a")
    c.w(f"{r1}.b", f"{r2}.a")
    c.w(f"{r2}.b", "0")
    c.w(f"{v}.n", "0")
    return c, r1


def codes(diags):
    return sorted((x["code"], tuple(x["pins"])) for x in diags)


def test_nc_pin_is_a_real_pin_but_not_a_model_port(extra_root):
    nc_resistor(extra_root)
    c = CATALOG.get("NC_R")
    assert CATALOG.problems == [] and c.terminals == ("a", "b", "n") and c.model_pins == ("a", "b") and c.nc == ("n",)
    assert c.pos["n"] == (30, 0) and [p.name for p in c.pins] == ["a", "b", "NC"]       # drawn at its position, in the pin list
    c2, r1 = divider()
    assert c2.d.pins(r1) == [f"{r1}.a", f"{r1}.b", f"{r1}.n"]                          # the document still has the pin
    nl = c2.d.netlist()
    line = next(l for l in nl["lines"] if l.startswith(f"R{r1} "))
    assert len(line.split()) == 4 and line.endswith("1000.0")                              # R name, two nodes, value


def test_nc_pin_wired_or_open_changes_nothing_in_the_deck_or_the_result(extra_root):
    nc_resistor(extra_root)
    c, r1 = divider()
    open_nl = c.d.netlist()
    c.run()
    open_v = c.v(f"{r1}.b")
    c.d.connect(f"{r1}.n", c.gnd)                           # wire the nc pin to ground: it still has no model port
    wired_nl = c.d.netlist()
    assert wired_nl["lines"] == open_nl["lines"] and wired_nl["observables"] == open_nl["observables"]
    c.run()
    assert c.v(f"{r1}.b") == pytest.approx(open_v) == pytest.approx(5.0, abs=1e-6)


def test_nc_pin_gets_no_unconnected_pin_warning_but_model_pins_still_do(extra_root):
    nc_resistor(extra_root)
    c, r1 = divider()
    assert [x for x in c.d.validate() if x["code"].startswith("UNCONNECTED")] == []   # `n` is open and silent
    c.d.disconnect(f"{r1}.b")
    got = [x for x in c.d.validate() if x["code"].startswith("UNCONNECTED")]
    assert (("UNCONNECTED-PIN", (f"{r1}.b",)) in codes(got)) and not any(f"{r1}.n" in x["pins"] for x in got)   # a model pin is reported, the nc pin is not


def test_part_with_only_nc_pins_wired_is_still_an_unconnected_part(extra_root):
    nc_resistor(extra_root)
    c, r1 = divider()
    c.d.disconnect(f"{r1}.a")
    c.d.disconnect(f"{r1}.b")
    c.d.connect(f"{r1}.n", c.gnd)                           # only the nc pin is wired
    got = [x for x in c.d.validate() if x["code"] == "UNCONNECTED-PART"]
    assert [x["part"] for x in got] == [r1] and got[0]["pins"] == [f"{r1}.a", f"{r1}.b"]     # reported over the model pins only


@pytest.mark.parametrize("nc, field, msg", [
    (["zz"], "nc", "not a pin id"),
    (["n", "n"], "nc", "twice"),
])
def test_bad_nc_list_is_rejected_with_def_pin(extra_root, nc, field, msg):
    nc_resistor(extra_root, nc=nc)
    assert "NC_R" not in CATALOG.defs
    bad = [p for p in CATALOG.problems if p["code"] == "DEF-PIN" and p["field"] == field]
    assert bad and msg in bad[0]["message"]


def test_nc_pin_in_the_line_template_is_an_error(extra_root):
    def mutate(d):
        d["pins"].append({"id": "n", "at": [30, 0]})
        d["nc"] = ["n"]
        d["sim"]["electrical"]["line"] = "{dev} {a} {b} {n} {R}"
    write_def(extra_root, "NC_R", mutate=mutate)
    assert "NC_R" not in CATALOG.defs
    assert [p for p in CATALOG.problems if p["code"] == "DEF-SIM"]


def test_all_pins_nc_leaves_no_model_port(extra_root):
    write_def(extra_root, "NC_R", mutate=lambda d: d.update(nc=["a", "b"]))
    assert "NC_R" not in CATALOG.defs and [p for p in CATALOG.problems if p["code"] == "DEF-SIM"]


def test_nc_must_be_a_list_of_strings(extra_root):
    nc_resistor(extra_root, nc="n")
    assert "NC_R" not in CATALOG.defs and [p for p in CATALOG.problems if p["code"] == "DEF-FIELD" and p["field"] == "nc"]


# ---------------------------------------------------------------- G22: LM741 and LM311
# pin id -> (x, y) of the pins the symbols had before the nc pins were added: saved documents connect to these ids
OLD_LM741 = {"inp": (0, 20), "inn": (0, 60), "out": (80, 40), "vcc": (40, 0), "vee": (40, 80)}
OLD_LM311 = {"inp": (0, 40), "inn": (0, 60), "out": (80, 40), "vcc": (40, 0), "vee": (40, 80), "emit": (80, 60)}


@pytest.mark.parametrize("id, old, nc, numbers", [
    ("LM741", OLD_LM741, {"null1": "pin 1", "null2": "pin 5", "nc8": "pin 8"}, None),
    ("LM311", OLD_LM311, {"bal": "pin 5", "strb": "pin 6"}, None),
])
def test_ic_symbols_gain_their_unmodelled_pins_without_moving_the_old_ones(id, old, nc, numbers):
    c = CATALOG.get(id)
    assert CATALOG.problems == [] and set(c.nc) == set(nc) and c.model_pins == tuple(old)
    assert {p: c.pos[p] for p in old} == old                      # saved documents still connect
    assert len(c.terminals) == len(old) + len(nc) and len(set(c.pos.values())) == len(c.terminals)
    for pid, num in nc.items():                                    # datasheet pin numbers
        assert num in next(p for p in c.pins if p.id == pid).description
    assert c.version == 1 and not any("not part of the symbol" in x for x in c.limitations)


def test_lm741_follower_runs_with_the_null_pins_open_and_silent():
    c = Circuit()
    u = opamp(c, "LM741")
    c.vdc(1.0, f"{u}.inp")
    c.w(f"{u}.out", f"{u}.inn")
    c.r("10k", f"{u}.out", "0")
    assert not [x for x in c.d.validate() if x["code"].startswith("UNCONNECTED")]
    c.run(tstop="20u", tstep="0.5u")
    assert c.v(f"{u}.out") == pytest.approx(1.0, abs=5e-3)
    deck = " ".join(c.d.netlist()["lines"])
    assert all(f"{pin}" not in deck for pin in ("null1", "null2", "nc8"))
    before = c.d.netlist()["lines"]
    c.d.connect(f"{u}.null1", f"{u}.null2")                         # a wire between the null pins: no model port, so no effect
    assert c.d.netlist()["lines"] == before


def test_lm311_comparator_runs_with_balance_and_strobe_open():
    c = Circuit()
    u = c.add("LM311")
    c.vdc(5, f"{u}.vcc")
    c.w(f"{u}.vee", "0")
    c.w(f"{u}.emit", "0")
    c.vdc(2.0, f"{u}.inn")
    c.vdc(3.0, f"{u}.inp")
    pull = c.add("V", dc=5)
    c.w(f"{pull}.n", "0")
    c.r("1k", f"{pull}.p", f"{u}.out")
    assert not [x for x in c.d.validate() if x["code"].startswith("UNCONNECTED")]
    c.run(tstop="2u", tstep="50n")
    assert c.v(f"{u}.out") > 4.9                                    # + input above - input: the open collector is off, the pull-up wins
