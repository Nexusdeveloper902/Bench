"""Task 9.6 gap fixes at the app level (docs/plans/ngspice-library/GAPS.md). Engine-level tests: benchcore/tests
(correctness.rs t71-t77+, tests/python/test_api.py). Real ngspice throughout."""
from app.model import Document
from app.sim import run_document
from test_subckt_packages import xpart, wire

RUNAWAY_SCR = """.subckt rscr a k g
.model rd D(Is=1e-12 N=1 BV=1000 IBV=1u)
.model rn NPN(Is=1e-14 Bf=100 Br=1 Ikf=20 Ise=1e-12 Ne=1.5 Rb=0.1 Tf=1u Tr=1u)
.model rp PNP(Is=1e-14 Bf=40 Br=1 Ikf=20 Ise=1e-12 Ne=1.5 Rb=0.1 Tf=1u Tr=1u)
DA a ay rd
RA ay ax 20m
QP gi x ax rp
QN x gi kx rn
RXA ax x 130
RK kx k 1m
RG g gi 100
RGK gi k 3.3k
.ends rscr
"""


def test_runaway_internal_node_is_a_warning_on_a_successful_run(extra_root):
    """G15: task 6's first SCR draft (Tf = Tr = 1 us, floating internal anode nodes) drops out on a 12 V supply and its
    internal nodes run to the 1000 V diode breakdown; ngspice says nothing. The run is still a success (the terminal
    values are the solver's), but the result now names the node in its findings and its message."""
    xpart(extra_root, "RSCR", "{dev} {a} {k} {g} rscr", pins=("a", "k", "g"), body=RUNAWAY_SCR)
    d = Document()
    vs, rl, s, vg, rg, g = (d.add_part("V", dc="12"), d.add_part("R", R="5k"), d.add_part("RSCR"),
                            d.add_part("V", mode="PULSE", v1="0", v2="5", td="100u", tr="1u", tf="1u", pw="300u", per="1"),
                            d.add_part("R", R="1k"), d.add_part("GND"))
    wire(d, (f"{vs}.p", f"{rl}.a"), (f"{rl}.b", f"{s}.a"), (f"{s}.k", f"{g}.g"), (f"{vs}.n", f"{g}.g"),
         (f"{vg}.p", f"{rg}.a"), (f"{rg}.b", f"{s}.g"), (f"{vg}.n", f"{g}.g"))
    d.tstop, d.tstep = "1.5m", "5u"
    res = run_document(d)
    assert res.ok and res.status == "success"
    warned = [f for f in res.findings if f["code"] == "IMPLAUSIBLE-NODE"]
    assert len(warned) == 1 and warned[0]["severity"] == "warn" and f"x{s.lower()}." in warned[0]["message"]
    assert "warning: implausible internal node" in res.message


def test_shipped_scr_dropping_out_has_no_runaway_warning():
    """G15 found the shipped bench_scr (100 Meg leak, Tf = 10 ns) still ran its internal nodes to 833 V when it dropped
    out below its holding current; a 10 pF anode-cathode capacitance (CAK) fixed it. Same drop-out circuit as batch B."""
    d = Document()
    vs, rl, s, vg, rg, g = (d.add_part("V", dc="12"), d.add_part("R", R="5000"), d.add_part("SCR"),
                            d.add_part("V", mode="PULSE", v1="0", v2="5", td="100u", tr="1u", tf="1u", pw="300u", per="1"),
                            d.add_part("R", R="1k"), d.add_part("GND"))
    wire(d, (f"{vs}.p", f"{rl}.a"), (f"{rl}.b", f"{s}.a"), (f"{s}.k", f"{g}.g"), (f"{vs}.n", f"{g}.g"),
         (f"{vg}.p", f"{rg}.a"), (f"{rg}.b", f"{s}.g"), (f"{vg}.n", f"{g}.g"))
    d.tstop, d.tstep = "1.5m", "5u"
    res = run_document(d)
    assert res.ok and not [f for f in res.findings if f["code"] == "IMPLAUSIBLE-NODE"], res.message
    assert res.stats(f"V({d.nets()[f'{s}.a']})")["final"] > 11.9          # dropped out: blocking again


def test_source_with_an_unwired_negative_pin_runs_with_warnings():
    """G11 (wontfix, pinned): V1.p -> R -> GND with V1.n left unwired. ngspice solves it exactly (the open source carries
    no current, so V(R) = 0), the same as any other half-wired part, and both layers warn: the app's UNCONNECTED-PIN on
    V1.n and the core's DANGLING on its net. An error would refuse a solvable circuit the user can see is half-wired."""
    d = Document()
    v, r, g = d.add_part("V", dc="5"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{v}.p", f"{r}.a"), (f"{r}.b", f"{g}.g"))
    res = run_document(d)
    assert res.ok
    assert [(x["code"], x["pins"]) for x in res.diagnostics] == [("UNCONNECTED-PIN", [f"{v}.n"])]
    assert any(f["code"] == "DANGLING" and f["severity"] == "warn" for f in res.findings)
    assert abs(res.stats(f"I({r})")["max"]) < 1e-12


def _sign_flips(res, name, after):
    """Fraction of consecutive second differences that change sign: ~1 for an alternating-sample artefact."""
    v = [x for t, x in res.get(name) if t > after]
    d2 = [v[i + 2] - 2 * v[i + 1] + v[i] for i in range(len(v) - 2)]
    return sum((a > 0) != (b > 0) for a, b in zip(d2, d2[1:])) / (len(d2) - 1)


def test_gear_integration_removes_the_trapezoidal_artefact_on_a_crystal(tmp_path):
    """G30: a 1 ns edge into a 32.768 kHz crystal through 1 Ω. With the trapezoidal rule the source current alternates
    sign every sample (a numerical mode, larger than the crystal's real ring); Simulation > Gear integration (doc.method)
    removes it. The setting is saved only when it is not the default, and reloads."""
    def doc(method):
        d = Document()
        v, r, x, g = (d.add_part("V", mode="PULSE", v1="0", v2="1", td="0", tr="1n", tf="1n", pw="1", per="2"),
                      d.add_part("R", R="1"), d.add_part("CRYSTAL_32K"), d.add_part("GND"))
        wire(d, (f"{v}.p", f"{r}.a"), (f"{r}.b", f"{x}.a"), (f"{x}.b", f"{g}.g"), (f"{v}.n", f"{g}.g"))
        d.tstop, d.tstep, d.method = "2m", "0.5u", method
        return d
    trap, gear = run_document(doc("trap")), run_document(doc("gear"))
    assert trap.ok and gear.ok
    assert _sign_flips(trap, "I(V1)", 1e-4) > 0.9 and _sign_flips(gear, "I(V1)", 1e-4) < 0.1
    assert "method" not in doc("trap").to_dict()["simulation"]
    d = doc("gear"); d.save(tmp_path / "g.json")
    assert d.to_dict()["simulation"]["method"] == "gear" and Document.load(tmp_path / "g.json").method == "gear"


def test_search_matches_at_word_starts(extra_root):
    """G23: tokens match the start of a word, so "esp" finds ESP32 but no longer every description with "response"
    (batches C and E had to reword descriptions to keep whole-catalog search assertions true)."""
    from app import catalog
    from test_catalog import write_def
    write_def(extra_root, "RESP_PART", name="Filter", description="flat frequency response", reload=False)
    write_def(extra_root, "ESP_PART", name="Esp thing", description="x", reload=False)
    catalog.reload([catalog.BUILTIN_ROOT, extra_root])
    ids = [d.id for d in catalog.CATALOG.search("esp")]
    assert "ESP_PART" in ids and "ESP32_DEVKITC" in ids and "RESP_PART" not in ids
    assert "RESP_PART" in [d.id for d in catalog.CATALOG.search("respon")]
    assert "RESP_PART" in [d.id for d in catalog.CATALOG.search("frequency resp")]


def _isrc_into_1k(**params):
    from test_library_batch_a import node, run
    d = Document()
    cs, r, g = d.add_part("ISRC_WAVE", **params), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{cs}.p", f"{g}.g"), (f"{cs}.n", f"{r}.a"), (f"{r}.b", f"{g}.g"))
    return d, r, node, run


def test_current_source_exp_and_sffm_waveforms():
    """G13: ISRC_WAVE gains the EXP and SFFM modes VSRC_EXP_SFFM has (same ngspice syntax), checked against the closed
    forms through 1 kΩ: 1 mA EXP rise with tau 1 ms reads 1 V * (1 - e^-5) at 5 ms; SFFM follows sin(wc t + m sin(ws t))."""
    import math
    import pytest
    d, r, node, run = _isrc_into_1k(mode="EXP", ie1="0", ie2="1m", td1="0", tau1="1m", td2="10m", tau2="1m")
    assert node(d, run(d, "6m", "1u"), f"{r}.a", 5e-3) == pytest.approx(1 - math.exp(-5), abs=1e-3)
    d, r, node, run = _isrc_into_1k(mode="SFFM", io="0", ia="1m", fc="1k", mdi="5", fs="100")
    t = 0.25e-3
    want = math.sin(2 * math.pi * 1e3 * t + 5 * math.sin(2 * math.pi * 100 * t))
    assert node(d, run(d, "1m", "1u"), f"{r}.a", t) == pytest.approx(want, abs=1e-3)


def test_live_stimulus_limitations_no_longer_say_fixed_for_the_whole_run():
    """G36: a live run can change these stimuli, so their limitations say "fixed during a batch run"."""
    from app.catalog import CATALOG
    for i in ("LDR", "PTC_THERMISTOR", "RTD_PT100", "RTD_PT1000", "LM35", "PHOTODIODE", "PHOTOTRANSISTOR", "HALL_LINEAR",
              "FSR", "STRAIN_GAUGE", "POSITION_SENSOR", "PRESSURE_BRIDGE"):
        text = " ".join(CATALOG.get(i).limitations)
        assert "whole run" not in text and "a live run can change it" in text, i


def test_pwl_sources_follow_their_points_and_refuse_bad_lists(tmp_path):
    """G12: a "points" param kind (time-value pairs) makes PWL sources packageable; VSRC_PWL and ISRC_PWL interpolate
    linearly between their points and hold the last value. A bad list is a param error that blocks the run."""
    import pytest
    from app.catalog import CATALOG
    from app.components import ModelError
    from test_library_batch_a import node, run
    assert len(CATALOG.defs) == 185                                                   # 173 + the two PWL sources + the six converter widths (W4) + the four W6 converters
    d = Document()
    v, r, g = d.add_part("VSRC_PWL", pts="0 0, 1m 5, 2m 5, 3m -1"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{v}.p", f"{r}.a"), (f"{r}.b", f"{g}.g"), (f"{v}.n", f"{g}.g"))
    assert d.netlist()["lines"][0] == f"{v} n1 0 PWL(0.0 0.0 0.001 5.0 0.002 5.0 0.003 -1.0)"
    res = run(d, "4m", "10u")
    for t, want in ((0.5e-3, 2.5), (1.5e-3, 5.0), (2.5e-3, 2.0), (3.5e-3, -1.0)):
        assert node(d, res, f"{v}.p", t) == pytest.approx(want, abs=1e-3)
    d.save(tmp_path / "pwl.json"); assert Document.load(tmp_path / "pwl.json").parts[v]["params"]["pts"] == "0 0, 1m 5, 2m 5, 3m -1"
    d2, r2, node, run = _isrc_into_1k_pwl()
    assert node(d2, run(d2, "3m", "10u"), f"{r2}.a", 1e-3) == pytest.approx(2.0, abs=1e-3)   # 2 mA into 1 kΩ
    p = CATALOG.get("VSRC_PWL").param("pts")
    for bad in ("0 0", "0 0, 1m", "1m 0, 1m 5", "-1m 0, 1m 5", "0 0; 1m x"):
        with pytest.raises(ModelError): p.parse(bad, "V1")
    assert p.parse("0 0; 1m 5", "V1") == "0.0 0.0 0.001 5.0"
    d.set_param(v, "pts", "0 0, 0 1")
    assert not run_document(d).ok


def _isrc_into_1k_pwl():
    from test_library_batch_a import node, run
    d = Document()
    cs, r, g = d.add_part("ISRC_PWL", pts="0 0, 1m 2m, 3m 2m"), d.add_part("R", R="1k"), d.add_part("GND")
    wire(d, (f"{cs}.p", f"{g}.g"), (f"{cs}.n", f"{r}.a"), (f"{r}.b", f"{g}.g"))
    return d, r, node, run
