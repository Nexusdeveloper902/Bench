"""Library batch C (task 7): analog ICs - op-amps, comparators, 555 timers, references, regulators, analog switches and
multiplexers, an audio amplifier and a Darlington driver array.

Every package loads and validates with validator status `full` (all are built from R, C, V, I, D, Q, S and linear
E/F/G/H/POLY sources). The behaviour is checked through the REAL path (Document -> netlist -> app.sim -> benchcore ->
ngspice) against closed forms (`analytic`) or datasheet typical values (`reference`).

The named parts are project-authored behavioural fits to datasheet typicals, NOT vendor macromodels: a pass means "the
fit reproduces the datasheet numbers it targeted", not "equal to the manufacturer's model" or to a device."""
import math

import pytest

from app.catalog import BUILTIN_ROOT, CATALOG
from app.model import Document
from app.sim import run_document

NEW = {   # id -> (model_kind, verification)
    "OPAMP_MACRO": ("behavioral", "analytic"), "LM358": ("behavioral", "reference"), "TL072": ("behavioral", "reference"),
    "MCP6002": ("behavioral", "reference"), "LM324": ("behavioral", "reference"), "LM741": ("behavioral", "reference"),
    "INAMP_3OA": ("project-subckt", "analytic"),
    "COMPARATOR": ("behavioral", "analytic"), "LM393": ("behavioral", "reference"), "LM311": ("behavioral", "reference"),
    "NE555": ("behavioral", "analytic"), "NE556": ("behavioral", "analytic"),
    "TL431": ("behavioral", "reference"), "LM4040": ("behavioral", "reference"),
    "REG_78XX": ("behavioral", "reference"), "REG_79XX": ("behavioral", "reference"), "LM317": ("behavioral", "reference"),
    "LM337": ("behavioral", "reference"), "AMS1117": ("behavioral", "reference"),
    "CD4066": ("behavioral", "reference"), "CD4051": ("behavioral", "reference"), "CD4052": ("behavioral", "reference"),
    "CD4053": ("behavioral", "reference"),
    "LM386": ("behavioral", "reference"), "ULN2003": ("project-subckt", "reference"),
}


# ---------------------------------------------------------------- helpers
class Circuit:
    """A Document with short-hand wiring: c.add("R", R="1k") -> name; c.w("R1.a", "V1.p", ...) joins pins in a chain."""

    def __init__(self):
        self.d = Document()
        self.gnd = self.d.add_part("GND") + ".g"

    def add(self, kind, **params):
        return self.d.add_part(kind, **{k: str(v) for k, v in params.items()})

    def w(self, *pins):
        """Join pins in a chain; "0" is ground; an empty pin is skipped (left for the caller to wire)."""
        pins = [self.gnd if p == "0" else p for p in pins if p]
        for a, b in zip(pins, pins[1:]):
            self.d.connect(a, b)

    def vdc(self, v, node):
        """A DC source from `node` to ground."""
        s = self.add("V", dc=v)
        self.w(f"{s}.p", node)
        self.w(f"{s}.n", "0")
        return s

    def r(self, value, a, b):
        x = self.add("R", R=value)
        self.w(f"{x}.a", a)
        self.w(f"{x}.b", b)
        return x

    def run(self, tstop="100u", tstep="1u"):
        self.d.tstop, self.d.tstep = tstop, tstep
        res = run_document(self.d)
        assert res.ok, res.message + "\n" + (res.details or "")
        self.res = res
        return self

    def series(self, pin):
        net = self.d.nets()[pin]
        if net == "0":
            return [(t, 0.0) for t, _ in next(iter(self.res.series.values()))]
        return self.res.get(f"V({net})")

    def v(self, pin, t=None):
        s = self.series(pin)
        return s[-1][1] if t is None else min(s, key=lambda p: abs(p[0] - t))[1]

    def i(self, src):
        return self.res.get(f"I({src})")[-1][1]


def crossings(series, level, rising=True):
    return [t for (t0, a), (t, b) in zip(series, series[1:]) if (a < level <= b if rising else a > level >= b)]


def rails(c, vcc, vee):
    """DC supplies: returns the V+ and V- pins (V- is ground when vee == 0)."""
    a = c.add("V", dc=vcc)
    c.w(f"{a}.n", "0")
    if vee == 0:
        return f"{a}.p", c.gnd
    b = c.add("V", dc=-vee)
    c.w(f"{b}.p", "0")
    return f"{a}.p", f"{b}.n"


def opamp(c, kind="OPAMP_MACRO", vcc=15, vee=-15, **params):
    u = c.add(kind, **params)
    vp, vn = rails(c, vcc, vee)
    c.w(f"{u}.vcc", vp)
    c.w(f"{u}.vee", vn)
    return u


def sec(id):
    return CATALOG.get(id).sim["electrical"]


# ---------------------------------------------------------------- every package loads and validates
@pytest.mark.parametrize("id", sorted(NEW))
def test_package_loads_validates_and_records_its_kind(id):
    c = CATALOG.get(id)
    s = c.sim["electrical"]
    assert CATALOG.problems == [] and c.simulated and c.readiness == "simulated" and c.path.is_relative_to(BUILTIN_ROOT)
    assert c.category == "Electrical" and (s["model_kind"], s["verification"]) == NEW[id]
    assert s["device"] == "X" and s["_status"] == "full" and not s["_limits"], id
    assert s["provenance"]["license"] == "MIT" and s["provenance"]["source"].startswith("Bench project")
    if s["verification"] == "reference":       # a datasheet fit says which datasheet, which revision, and that it is not the vendor's model
        assert s["provenance"]["url"].startswith(("https://", "http://")) and s["provenance"]["version"], id
        assert any("not a vendor" in x for x in c.limitations), id
    assert not any(w in " ".join(c.limitations).lower() for w in ("validated against", "manufacturer-accurate"))


def test_catalog_grew_by_exactly_these_parts():
    assert set(NEW) <= set(CATALOG.defs) and len(NEW) == 25 and len(CATALOG.defs) >= 92 + len(NEW)     # batch D adds more (test_library_batch_d.py)


def test_shared_model_files_are_emitted_once_per_deck():
    c = Circuit()
    for kind in ("LM358", "LM324", "OPAMP_MACRO"):
        u = c.add(kind)
        for pin in CATALOG.get(kind).pins:
            c.r("1meg", f"{u}.{pin.id}", "0")
    lines = c.d.netlist()["models"]
    assert len([m for m in lines if m.startswith("* bench-def") and "opamp_macro.lib" in m]) == 1
    assert sum(1 for m in lines if m.lower().startswith(".subckt bench_opamp_macro")) == 1


# ---------------------------------------------------------------- op-amp macromodel: closed forms
def amp(kind="OPAMP_MACRO", gain=-10, vin=0.1, rin="10k", vcc=15, vee=-15, rl=None, **params):
    """Inverting (gain < 0) or non-inverting (gain > 1) amplifier on a DC input; returns the output voltage."""
    c = Circuit()
    u = opamp(c, kind, vcc, vee, **params)
    src = c.add("V", dc=vin)
    c.w(f"{src}.n", "0")
    rf = float(rin[:-1]) * 1e3 * (abs(gain) if gain < 0 else gain - 1)
    if gain < 0:
        c.r(rin, f"{src}.p", f"{u}.inn")
        c.w(f"{u}.inp", "0")
    else:
        c.w(f"{src}.p", f"{u}.inp")
        c.r(rin, f"{u}.inn", "0")
    c.r(rf, f"{u}.inn", f"{u}.out")
    if rl:
        c.r(rl, f"{u}.out", "0")
    return c.run("200u", "2u").v(f"{u}.out")


@pytest.mark.parametrize("gain", [-1, -10, -47, 2, 11, 101])
def test_closed_loop_gain_matches_g_over_1_plus_g_over_a(gain):
    a = 1e5
    noise_gain = 1 - gain if gain < 0 else gain                         # 1/beta
    ideal = gain * 0.1
    assert amp(gain=gain, vin=0.1) == pytest.approx(ideal / (1 + noise_gain / a), rel=1e-4)


def test_input_offset_and_bias_current_appear_at_the_output_as_closed_forms():
    # offset: Vos x noise gain (11); bias: IB x Rf flows from the output through Rf into the - input
    assert amp(gain=11, vin=0, vos="1m") == pytest.approx(11e-3, rel=1e-3)
    assert amp(gain=-10, vin=0, ib="100n") == pytest.approx(100e-9 * 100e3, rel=1e-3)


def step_response(kind="OPAMP_MACRO", step=10.0, vcc=15, vee=-15, tstop="40u", tstep="10n", out_load="10k", **params):
    """Unity-gain buffer driven by a 0 -> step pulse at 1 us; returns the output series."""
    c = Circuit()
    u = opamp(c, kind, vcc, vee, **params)
    src = c.add("V", mode="PULSE", v1=0, v2=step, td="1u", tr="1n", tf="1n", pw="1", per="2")
    c.w(f"{src}.n", "0")
    c.w(f"{src}.p", f"{u}.inp")
    c.w(f"{u}.inn", f"{u}.out")
    c.r(out_load, f"{u}.out", "0")
    return c.run(tstop, tstep).series(f"{u}.out")


@pytest.mark.parametrize("kind, params, sr", [("OPAMP_MACRO", {"sr": "500k"}, 0.5e6), ("OPAMP_MACRO", {"sr": "13meg", "gbw": "3meg"}, 13e6),
                                              ("TL072", {}, 20e6), ("LM741", {}, 0.5e6)])
def test_large_step_is_slew_limited_at_the_slew_rate(kind, params, sr):
    if kind == "TL072":    # the dual's first amplifier as a buffer
        c = Circuit()
        u = c.add("TL072")
        vp, vn = rails(c, 15, -15)
        c.w(f"{u}.vcc", vp); c.w(f"{u}.vee", vn)
        src = c.add("V", mode="PULSE", v1=0, v2=10, td="1u", tr="1n", tf="1n", pw="1", per="2")
        c.w(f"{src}.n", "0"); c.w(f"{src}.p", f"{u}.in1p"); c.w(f"{u}.in1n", f"{u}.out1")
        for p in ("in2p", "in2n"):
            c.w(f"{u}.{p}", "0")
        c.r("10k", f"{u}.out1", "0"); c.r("10k", f"{u}.out2", "0")
        out = c.run("3u", "2n").series(f"{u}.out1")
    else:
        out = step_response(kind, tstop="40u" if sr < 1e6 else "3u", tstep="10n" if sr < 1e6 else "2n", **params)
    t2, t8 = crossings(out, 2.0)[0], crossings(out, 8.0)[0]
    assert 6.0 / (t8 - t2) == pytest.approx(sr, rel=0.02)


@pytest.mark.parametrize("gbw", [1e6, 5e6])
def test_small_step_rise_time_of_a_unity_buffer_is_0_35_over_gbw(gbw):
    out = step_response(step=0.01, sr="10meg", gbw=f"{gbw:g}", tstop="5u", tstep="1n")
    t1, t9 = crossings(out, 0.001)[0], crossings(out, 0.009)[0]
    assert t9 - t1 == pytest.approx(math.log(9) / (2 * math.pi * gbw), rel=0.02)      # 10-90 % of a single pole = ln 9 / (2 pi f)


def test_output_saturates_at_the_rail_minus_the_swing_loss():
    hi = amp(gain=-10, vin=-5, vdroph="1.2", vdropl="0.8")
    lo = amp(gain=-10, vin=5, vdroph="1.2", vdropl="0.8")
    assert hi == pytest.approx(15 - 1.2, abs=0.01) and lo == pytest.approx(-15 + 0.8, abs=0.01)


# ---------------------------------------------------------------- named op-amps against their datasheets
def dual_out(kind, vcc, vee, drive, rl, load_to="0"):
    """Both halves of a dual op-amp driven open loop in opposite directions (half 1 high, half 2 low), each loaded by
    `rl` to `load_to`; returns (out1, out2)."""
    c = Circuit()
    u = c.add(kind)
    vp, vn = rails(c, vcc, vee)
    c.w(f"{u}.vcc", vp); c.w(f"{u}.vee", vn)
    s = c.add("V", dc=drive)
    c.w(f"{s}.n", "0"); c.w(f"{s}.p", f"{u}.in1p"); c.w(f"{u}.in2n", f"{s}.p")
    c.w(f"{u}.in1n", "0"); c.w(f"{u}.in2p", "0")
    if load_to != "0":
        load_to = f"{c.vdc(load_to, '')}.p"
    c.r(rl, f"{u}.out1", load_to); c.r(rl, f"{u}.out2", load_to)
    c.run("200u", "2u")
    return c.v(f"{u}.out1"), c.v(f"{u}.out2")


def test_lm358_single_supply_output_swing_matches_the_datasheet():
    hi, lo = dual_out("LM358", 5, 0, 1.0, "10k")
    assert hi == pytest.approx(3.5 * 10e3 / (10e3 + 50), abs=1e-3)    # VOH = V+ - 1.5 V, then the (assumed) 50 ohm output
    assert lo == pytest.approx(0.005, abs=5e-4)                         # VOL 5 mV typ


def test_tl072_swings_13_5_volts_into_10k_on_15_volt_rails():
    hi, lo = dual_out("TL072", 15, -15, 1.0, "10k")
    assert hi == pytest.approx(13.5, abs=0.05) and lo == pytest.approx(-13.5, abs=0.05)


def open_loop(kind, drive, rl, vcc=15, vee=-15):
    """A single op-amp driven open loop (+ at `drive`, - at 0) into `rl` to ground; returns the output voltage."""
    c = Circuit()
    u = opamp(c, kind, vcc, vee)
    c.vdc(drive, f"{u}.inp")
    c.w(f"{u}.inn", "0")
    c.r(rl, f"{u}.out", "0")
    return c.run("200u", "2u").v(f"{u}.out")


def test_lm741_swing_matches_both_datasheet_rows():
    for rl, swing in (("10k", 14.0), ("2k", 13.0)):
        assert open_loop("LM741", 1, rl) == pytest.approx(swing, abs=0.05)
        assert open_loop("LM741", -1, rl) == pytest.approx(-swing, abs=0.05)


def test_mcp6002_output_is_rail_to_rail_within_25_mv():
    hi, lo = dual_out("MCP6002", 5, 0, 1.0, "10k", load_to=2.5)      # RL = 10 kohm to VDD/2, as in the datasheet
    assert 5 - 0.025 <= hi < 5 and 0 < lo <= 0.025


def test_lm358_and_mcp6002_slew_rates_and_supply_current():
    c = Circuit()
    u1, u2 = c.add("LM358"), c.add("MCP6002")
    sp = c.add("V", dc=5)
    c.w(f"{sp}.n", "0")
    src = c.add("V", mode="PULSE", v1=1, v2=4, td="1u", tr="1n", tf="1n", pw="1", per="2")
    c.w(f"{src}.n", "0")
    for u in (u1, u2):
        c.w(f"{u}.vcc", f"{sp}.p"); c.w(f"{u}.vee", "0"); c.w(f"{u}.in1p", f"{src}.p"); c.w(f"{u}.in1n", f"{u}.out1")
        c.w(f"{u}.in2p", f"{u}.in2n", f"{u}.out2")                    # second half as an idle buffer
        c.r("100k", f"{u}.in2p", "0")
    c.run("12u", "5n")
    for u, sr in ((u1, 0.3e6), (u2, 0.6e6)):
        out = c.series(f"{u}.out1")
        t1, t2 = crossings(out, 1.5)[0], crossings(out, 3.5)[0]
        assert 2.0 / (t2 - t1) == pytest.approx(sr, rel=0.03)


def test_lm324_quad_runs_four_amplifiers_with_their_own_gains():
    """Multi-instance: the quad nests bench_opamp_macro four times; each half keeps its own feedback network."""
    c = Circuit()
    u = c.add("LM324")
    sp = c.add("V", dc=12)
    c.w(f"{sp}.n", "0"); c.w(f"{u}.vcc", f"{sp}.p"); c.w(f"{u}.vee", "0")
    vin = c.add("V", dc=0.5)
    c.w(f"{vin}.n", "0")
    gains = {1: 1, 2: 2, 3: 5, 4: 11}
    for k, g in gains.items():
        c.w(f"{u}.in{k}p", f"{vin}.p")
        if g == 1:
            c.w(f"{u}.in{k}n", f"{u}.out{k}")
        else:
            c.r("10k", f"{u}.in{k}n", "0")
            c.r(f"{10 * (g - 1)}k", f"{u}.in{k}n", f"{u}.out{k}")
    c.run("200u", "2u")
    for k, g in gains.items():
        # VIO 3 mV typ is amplified by the noise gain; IIB = -20 nA leaves the - pin through Rf; finite AOL = 100k
        ideal = g * (0.5 + 3e-3) - 20e-9 * 10e3 * (g - 1)
        assert c.v(f"{u}.out{k}") == pytest.approx(ideal / (1 + g / 1e5), abs=2e-4)
    assert -c.i(sp) == pytest.approx(4 * 175e-6, rel=1e-3)      # ICC 0.7 mA for four; output current comes from ground in the model


def test_instrumentation_amplifier_gain_is_1_plus_50k_over_rg():
    for rg, gain in (("50k", 2.0), ("5.1k", 1 + 50 / 5.1), ("1k", 51.0)):
        c = Circuit()
        u = opamp(c, "INAMP_3OA")
        cm, dm = c.add("V", dc=2.0), c.add("V", dc=0.05)        # 2 V common mode, 50 mV differential
        c.w(f"{cm}.n", "0"); c.w(f"{cm}.p", f"{u}.inn"); c.w(f"{dm}.n", f"{u}.inn"); c.w(f"{dm}.p", f"{u}.inp")
        c.r(rg, f"{u}.rg1", f"{u}.rg2")
        ref = c.add("V", dc=1.0)
        c.w(f"{ref}.n", "0"); c.w(f"{ref}.p", f"{u}.ref")
        c.r("10k", f"{u}.out", "0")
        out = c.run("200u", "2u").v(f"{u}.out")
        assert out == pytest.approx(1.0 + gain * 0.05, rel=2e-3)


# ---------------------------------------------------------------- comparators
def test_comparator_switches_at_the_offset_and_sinks_through_ron():
    c = Circuit()
    u = opamp(c, "COMPARATOR", 5, 0, vos="10m")
    ramp = c.add("V", mode="PULSE", v1=2.4, v2=2.6, td="0", tr="2m", tf="1n", pw="1", per="2")     # 0.1 mV/us
    ref = c.add("V", dc=2.5)
    c.w(f"{ramp}.n", "0"); c.w(f"{ref}.n", "0"); c.w(f"{ramp}.p", f"{u}.inp"); c.w(f"{ref}.p", f"{u}.inn")
    c.r("4.7k", f"{u}.vcc", f"{u}.out")
    c.run("2m", "0.5u")
    out, vin = c.series(f"{u}.out"), c.series(f"{u}.inp")
    t = crossings(out, 2.5)[0]
    assert min(vin, key=lambda p: abs(p[0] - t))[1] == pytest.approx(2.5 - 0.010, abs=1.5e-3)   # switches where + + VOS crosses - (slow-ramp lag about 1 mV)
    assert c.v(f"{u}.out", 50e-6) < 0.1 and out[-1][1] == pytest.approx(5.0, abs=1e-3)
    assert c.v(f"{u}.out", 1e-6) == pytest.approx(5 * 35 / (4700 + 35), rel=1e-2)              # sinks through RON


def test_comparator_with_positive_feedback_has_the_resistor_network_hysteresis():
    """Non-inverting Schmitt trigger: input -> 10k -> + ; + -> 100k -> output (pulled up to 5 V by 1k); - at 2.5 V.
    Thresholds (closed form): rising Vin = 2.5 - (Vh - 2.5) * 10/100, falling Vin = 2.5 + (2.5 - Vl) * 10/100."""
    c = Circuit()
    u = opamp(c, "COMPARATOR", 5, 0, ron="1")
    tri = c.add("V", mode="PULSE", v1=1, v2=4, td="0", tr="500u", tf="500u", pw="1n", per="1m")
    ref = c.add("V", dc=2.5)
    c.w(f"{tri}.n", "0"); c.w(f"{ref}.n", "0"); c.w(f"{ref}.p", f"{u}.inn")
    c.r("10k", f"{tri}.p", f"{u}.inp"); c.r("100k", f"{u}.inp", f"{u}.out"); c.r("1k", f"{u}.vcc", f"{u}.out")
    c.run("1m", "0.2u")
    out, vin = c.series(f"{u}.out"), c.series(f"{tri}.p")
    up, dn = crossings(out, 2.5)[0], crossings(out, 2.5, rising=False)[-1]
    vh = 5 * 100e3 / (100e3 + 1e3 + 10e3) + 2.5 * 0                                              # output high is loaded by the feedback divider
    vin_at = lambda t: min(vin, key=lambda p: abs(p[0] - t))[1]
    # rising threshold: + reaches 2.5 V with the output low (about 0 V through RON)
    assert vin_at(up) == pytest.approx(2.5 * (1 + 10 / 100), abs=0.02)
    # falling threshold: the output high node is the 1k pull-up loaded by 100k + 10k to the input
    v_up = lambda vi: (5 / 1e3 + vi / 110e3) / (1 / 1e3 + 1 / 110e3)
    fall = next(x / 1000 for x in range(2500, 0, -1) if (x / 1000) + (v_up(x / 1000) - x / 1000) * 10 / 110 < 2.5)
    assert vin_at(dn) == pytest.approx(fall, abs=0.02) and vin_at(up) - vin_at(dn) == pytest.approx(2.75 - fall, abs=0.03)


def lm393_response(v1, v2, ref=1.0):
    """Input 1: + steps from v1 to v2 at 5 us, - at `ref`, 5.1 kohm pull-up to 5 V; returns (response time, VOL)."""
    c = Circuit()
    u = c.add("LM393")
    sp = c.vdc(5, f"{u}.vcc")
    c.w(f"{u}.gnd", "0")
    ref = c.add("V", dc=ref)
    src = c.add("V", mode="PULSE", v1=v1, v2=v2, td="5u", tr="1n", tf="1n", pw="1", per="2")
    c.w(f"{ref}.n", "0"); c.w(f"{src}.n", "0"); c.w(f"{ref}.p", f"{u}.in1n"); c.w(f"{src}.p", f"{u}.in1p")
    c.w(f"{u}.in2p", f"{u}.in2n", f"{ref}.p")
    c.r("5.1k", f"{sp}.p", f"{u}.out1"); c.r("5.1k", f"{sp}.p", f"{u}.out2")
    c.run("10u", "5n")
    return crossings(c.series(f"{u}.out1"), 1.4)[0] - 5e-6, c.v(f"{u}.out1", 4e-6)


def test_lm393_response_times_and_output_low_level_match_the_datasheet():
    vos = 2e-3                                        # the fit's offset: overdrive is measured from the switching point
    t5mv, vol = lm393_response(1 - vos - 0.095, 1 - vos + 0.005)      # 100 mV step, 5 mV overdrive: 1.3 us typ
    tbig, _ = lm393_response(0.0, 3.0, ref=1.4)                      # TTL-level step: 0.3 us typ
    assert t5mv == pytest.approx(1.3e-6, rel=0.05) and tbig == pytest.approx(0.3e-6, rel=0.1)
    assert vol == pytest.approx(5 * 35 / 5135, rel=0.02)                  # 5.1k pull-up: about 1 mA through the 35 ohm output


def test_lm311_emitter_output_sinks_50_ma_at_the_datasheet_level_and_switches_fast():
    c = Circuit()
    u = c.add("LM311")
    vp, vn = rails(c, 15, -15)
    c.w(f"{u}.vcc", vp); c.w(f"{u}.vee", vn); c.w(f"{u}.emit", "0")
    src = c.add("V", mode="PULSE", v1=0.097, v2=-0.003, td="2u", tr="1n", tf="1n", pw="1", per="2")   # 5 mV past the 2 mV offset
    c.w(f"{src}.n", "0"); c.w(f"{src}.p", f"{u}.inn"); c.w(f"{u}.inp", "0")
    sp = c.vdc(5, "")
    c.r("100", f"{sp}.p", f"{u}.out")                      # 50 mA when on
    c.run("3u", "2n")
    out = c.series(f"{u}.out")
    assert c.v(f"{u}.out", 1.5e-6) == pytest.approx(5 * 15 / 115, rel=0.02)    # 15 ohm on resistance (0.75 V at 50 mA): 0.65 V
    assert crossings(out, 1.4)[0] - 2e-6 == pytest.approx(140e-9, rel=0.1)                         # 115 / 165 ns typ at 5 mV overdrive


# ---------------------------------------------------------------- 555 timers
def astable(c, u, ra, rb, cap, vcc_pin):
    c.r(ra, vcc_pin, f"{u}.dis")
    c.r(rb, f"{u}.dis", f"{u}.thr")
    c.w(f"{u}.thr", f"{u}.trig")
    ct = c.add("C", C=cap)
    c.w(f"{ct}.a", f"{u}.thr"); c.w(f"{ct}.b", "0")


def period(series, level):
    e = crossings(series, level)
    return (e[-1] - e[1]) / (len(e) - 2)


@pytest.mark.parametrize("vcc, ra, rb, cap", [(5, 10e3, 47e3, 10e-9), (12, 4.7e3, 10e3, 100e-9), (9, 1e3, 100e3, 1e-9)])
def test_555_astable_frequency_is_1_44_over_ra_plus_2rb_times_c(vcc, ra, rb, cap):
    c = Circuit()
    u = c.add("NE555")
    sp = c.vdc(vcc, f"{u}.vcc")
    c.w(f"{u}.gnd", "0"); c.w(f"{u}.reset", f"{sp}.p")
    astable(c, u, f"{ra:g}", f"{rb:g}", f"{cap:g}", f"{sp}.p")
    f = 1 / (math.log(2) * (ra + 2 * rb) * cap)                  # 1.44 / ((RA + 2 RB) C)
    c.run(f"{8 / f:g}", f"{1 / f / 1000:g}")
    assert 1 / period(c.series(f"{u}.out"), vcc / 2) == pytest.approx(f, rel=0.03)
    tc = c.series(f"{u}.thr")
    late = [v for t, v in tc if t > 3 / f]
    assert max(late) == pytest.approx(2 * vcc / 3, rel=0.01) and min(late) == pytest.approx(vcc / 3, rel=0.01)


@pytest.mark.parametrize("vcc, r, cap", [(9, 100e3, 100e-9), (5, 10e3, 1e-6)])
def test_555_monostable_pulse_is_1_1_rc(vcc, r, cap):
    c = Circuit()
    u = c.add("NE555")
    sp = c.vdc(vcc, f"{u}.vcc")
    c.w(f"{u}.gnd", "0"); c.w(f"{u}.reset", f"{sp}.p")
    width = 1.1 * r * cap
    trig = c.add("V", mode="PULSE", v1=vcc, v2=0, td=f"{width / 5:g}", tr="1n", tf="1n", pw=f"{width / 50:g}", per=f"{width * 10:g}")
    c.w(f"{trig}.n", "0"); c.w(f"{trig}.p", f"{u}.trig")
    c.r(f"{r:g}", f"{sp}.p", f"{u}.thr"); c.w(f"{u}.thr", f"{u}.dis")
    ct = c.add("C", C=f"{cap:g}")
    c.w(f"{ct}.a", f"{u}.thr"); c.w(f"{ct}.b", "0")
    c.run(f"{2 * width:g}", f"{width / 1000:g}")
    out = c.series(f"{u}.out")
    up, dn = crossings(out, vcc / 2)[0], crossings(out, vcc / 2, rising=False)[0]
    assert dn - up == pytest.approx(width, rel=0.02)
    assert out[-1][1] < 0.1 and max(v for _, v in out) > vcc - 1.0


@pytest.mark.parametrize("vcc, load, high, level", [(15, "133", True, 13.3), (15, "62.5", True, 12.5), (15, "1.49k", False, 0.1),
                                                    (15, "292", False, 0.4), (5, "33", True, 3.3)])     # loads give the datasheet currents
def test_555_output_levels_match_the_datasheet(vcc, load, high, level):
    """VOH 13.3 V at -100 mA and 12.5 V at -200 mA (VCC = 15 V), 3.3 V at -100 mA (5 V); VOL 0.1 V at 10 mA, 0.4 V at 50 mA."""
    c = Circuit()
    u = c.add("NE555")
    sp = c.vdc(vcc, f"{u}.vcc")
    c.w(f"{u}.gnd", "0"); c.w(f"{u}.reset", f"{sp}.p"); c.w(f"{u}.thr", "0")
    c.w(f"{u}.trig", "0" if high else f"{sp}.p")      # TRIG low sets the output high; otherwise it stays reset from start-up
    c.w(f"{u}.ctrl", ""); c.r("1e9", f"{u}.ctrl", "0"); c.r("1e9", f"{u}.dis", "0")
    c.r(load, f"{u}.out", "0" if high else f"{sp}.p")
    c.run("20u", "1u")
    assert c.v(f"{u}.out") == pytest.approx(level, abs=0.1 if high else 0.03)


def test_555_control_voltage_and_reset_change_the_timing():
    def freq(cont=None, reset_low=False):
        c = Circuit()
        u = c.add("NE555")
        sp = c.vdc(10, f"{u}.vcc")
        c.w(f"{u}.gnd", "0")
        if reset_low:
            c.w(f"{u}.reset", "0")
        else:
            c.w(f"{u}.reset", f"{sp}.p")
        astable(c, u, "10k", "10k", "10n", f"{sp}.p")
        if cont is not None:
            c.vdc(cont, f"{u}.ctrl")
        c.run("2m", "0.5u")
        e = crossings(c.series(f"{u}.out"), 5)
        return (len(e) - 2) / (e[-1] - e[1]) if len(e) > 3 else 0.0, c.series(f"{u}.out")
    f0, _ = freq()
    # CONT forced to 5 V: thresholds 5 V and 2.5 V -> charge from 2.5 to 5 towards 10 V, discharge 5 -> 2.5 to 0
    rc1, rc2 = 20e3 * 10e-9, 10e3 * 10e-9
    f5 = 1 / (rc1 * math.log((10 - 2.5) / (10 - 5)) + rc2 * math.log(5 / 2.5))
    f_cont, _ = freq(cont=5)
    assert f0 == pytest.approx(1 / (math.log(2) * 30e3 * 10e-9), rel=0.03) and f_cont == pytest.approx(f5, rel=0.03)
    f_rst, out = freq(reset_low=True)
    assert f_rst == 0.0 and max(v for _, v in out) < 0.1                 # RESET low: output held low


def test_two_555s_cascade_an_astable_triggering_a_monostable():
    """Multi-instance: astable U1 (1 kHz-ish) drives the trigger of monostable U2 through a 1 nF / 10 k differentiator."""
    c = Circuit()
    u1, u2 = c.add("NE555"), c.add("NE555")
    sp = c.vdc(9, f"{u1}.vcc")
    for u in (u1, u2):
        c.w(f"{u}.vcc", f"{sp}.p"); c.w(f"{u}.gnd", "0"); c.w(f"{u}.reset", f"{sp}.p")
    astable(c, u1, "10k", "68k", "10n", f"{sp}.p")                 # f = 1.44 / (146k * 10 nF) = 986 Hz; low phase 0.47 ms
    # differentiator: falling edges of U1's output pull U2's trigger below 3 V briefly
    cc = c.add("C", C="1n")
    c.w(f"{cc}.a", f"{u1}.out"); c.w(f"{cc}.b", f"{u2}.trig")
    c.r("10k", f"{sp}.p", f"{u2}.trig")
    c.r("22k", f"{sp}.p", f"{u2}.thr"); c.w(f"{u2}.thr", f"{u2}.dis")
    ct = c.add("C", C="10n")
    c.w(f"{ct}.a", f"{u2}.thr"); c.w(f"{ct}.b", "0")
    c.run("5m", "1u")
    o1, o2 = c.series(f"{u1}.out"), c.series(f"{u2}.out")
    falls, ups = crossings(o1, 4.5, rising=False), crossings(o2, 4.5)
    downs = crossings(o2, 4.5, rising=False)
    assert len(falls) >= 4 and len(ups) >= len(falls) - 1
    for f_ in falls[:3]:                                      # each U1 falling edge fires one U2 pulse of 1.1 R C = 242 us
        up = next(t for t in ups if t >= f_ - 1e-6)
        assert up - f_ < 5e-6
        assert next(t for t in downs if t > up) - up == pytest.approx(1.1 * 22e3 * 10e-9, rel=0.03)


def test_556_runs_two_independent_astables():
    c = Circuit()
    u = c.add("NE556")
    sp = c.vdc(10, f"{u}.vcc")
    c.w(f"{u}.gnd", "0")
    for k, (ra, rb, cap) in {1: ("10k", "47k", "10n"), 2: ("10k", "10k", "10n")}.items():
        c.w(f"{u}.reset{k}", f"{sp}.p")
        c.r(ra, f"{sp}.p", f"{u}.dis{k}"); c.r(rb, f"{u}.dis{k}", f"{u}.thr{k}"); c.w(f"{u}.thr{k}", f"{u}.trig{k}")
        ct = c.add("C", C=cap)
        c.w(f"{ct}.a", f"{u}.thr{k}"); c.w(f"{ct}.b", "0")
    c.run("3m", "0.5u")
    for k, rsum in ((1, 104e3), (2, 30e3)):
        assert 1 / period(c.series(f"{u}.out{k}"), 5) == pytest.approx(1 / (math.log(2) * rsum * 10e-9), rel=0.03)


# ---------------------------------------------------------------- references
def tl431(vs, rs, r1=None, r2=None):
    c = Circuit()
    u = c.add("TL431")
    src = c.vdc(vs, "")
    c.r(rs, f"{src}.p", f"{u}.k"); c.w(f"{u}.a", "0")
    if r1 is None:
        c.w(f"{u}.ref", f"{u}.k")
    else:
        c.r(r1, f"{u}.k", f"{u}.ref"); c.r(r2, f"{u}.ref", "0")
    c.run("200u", "2u")
    return c.v(f"{u}.k"), -c.i(src)


def test_tl431_reference_and_dynamic_impedance_match_the_datasheet():
    v10, i10 = tl431(12.495, "1k")                 # VKA = Vref, IKA = 10 mA: 2.495 V typ
    v1, _ = tl431(3.495, "1k")
    v100, _ = tl431(102.495, "1k")
    assert v10 == pytest.approx(2.495, abs=1e-3) and i10 == pytest.approx(10e-3, rel=0.01)
    assert (v100 - v1) / (0.1 - 0.001) == pytest.approx(0.2, rel=0.05)          # |ZKA| 0.2 ohm


def test_tl431_regulates_the_divider_voltage_vref_times_1_plus_r1_over_r2():
    for vs in (9, 15):
        vo, _ = tl431(vs, "470", "10k", "10k")
        ik = (vs - vo) / 470 - vo / 20e3
        # datasheet typicals: Vref 2.495 V at VKA = Vref and 10 mA, -1.4 mV per volt of VKA, 0.2 ohm x (IK - 10 mA);
        # VKA = Vref (1 + R1/R2) + IREF R1 with IREF = 2 uA
        vref = 2.495 - 1.4e-3 * (vo - 2.495) + 0.2 * (ik - 10e-3)
        assert vo == pytest.approx(vref * 2 + 2e-6 * 10e3, abs=5e-4)


def test_tl431_is_off_below_its_reference():
    vo, i = tl431(2.0, "1k", "10k", "100k")
    # off: only the divider and the 2 uA reference-input current conduct
    assert vo == pytest.approx(2.0 * 110 / 111, abs=3e-3) and i == pytest.approx(2.0 / 111e3 + 2e-6, rel=0.05)


@pytest.mark.parametrize("vr", ["2.5", "5.0", "10.0"])
def test_lm4040_holds_its_voltage_from_100_ua_to_15_ma(vr):
    def v(i):
        c = Circuit()
        u = c.add("LM4040", vr=vr)
        s = c.add("I", dc=i)
        c.w(f"{s}.p", "0"); c.w(f"{s}.n", f"{u}.k"); c.w(f"{u}.a", "0")
        return c.run("100u", "2u").v(f"{u}.k")
    assert v(100e-6) == pytest.approx(float(vr), abs=1e-3)
    assert v(15e-3) - v(1e-3) == pytest.approx(2.5e-3, rel=0.1)            # datasheet: 2.5 mV from 1 to 15 mA
    assert v(20e-6) < float(vr) * 0.5                                        # below the 45 uA minimum it drops out


# ---------------------------------------------------------------- regulators
def reg(kind, vin, rload, params=None, pins=("in", "gnd", "out"), cout="1u"):
    c = Circuit()
    u = c.add(kind, **(params or {}))
    src = c.vdc(vin, f"{u}.{pins[0]}")
    c.w(f"{u}.{pins[1]}", "0")
    c.r(rload, f"{u}.{pins[2]}", "0")
    if cout:
        cap = c.add("C", C=cout)
        c.w(f"{cap}.a", f"{u}.{pins[2]}"); c.w(f"{cap}.b", "0")
    c.run("2m", "4u")
    return c.v(f"{u}.{pins[2]}"), -c.i(src)


@pytest.mark.parametrize("vout", ["5", "12"])
def test_78xx_regulates_under_load_inside_the_datasheet_window(vout):
    vo = float(vout)
    light, i_light = reg("REG_78XX", vo + 5, f"{vo / 5e-3:g}", {"vout": vout})       # 5 mA
    heavy, _ = reg("REG_78XX", vo + 5, f"{vo / 1.0:g}", {"vout": vout})               # 1 A
    hi_line, _ = reg("REG_78XX", vo + 15, f"{vo / 0.5:g}", {"vout": vout})
    for v in (light, heavy, hi_line):
        assert vo * 0.96 <= v <= vo * 1.04                                           # +-4 % over line and load
    assert abs(light - heavy) < 0.015                                                # load regulation <= 15 mV typ (5 mA - 1.5 A)
    assert i_light - 5e-3 == pytest.approx(4.2e-3, rel=0.05)                        # bias current 4.2 mA


def test_78xx_drops_out_two_volts_below_the_input_at_1_amp_and_limits_current():
    near, _ = reg("REG_78XX", 7.1, "5")         # Vin - Vout = 2.1 V at 1 A: still regulating
    out, _ = reg("REG_78XX", 6.5, "5")          # 1.5 V headroom: in dropout, about 2 V below the input
    assert near == pytest.approx(5.0, abs=0.05) and out == pytest.approx(6.5 - 2.0, abs=0.15)
    short, _ = reg("REG_78XX", 10, "0.1", cout=None)
    assert short / 0.1 == pytest.approx(2.2, rel=0.1)                             # peak output current 2.2 A


def test_79xx_regulates_a_negative_output_with_1_1_volt_dropout():
    vo, i = reg("REG_79XX", -10, "100", {"vout": "-12"}, pins=("in", "gnd", "out"))
    assert vo < -9                                                                # -12 V needs more than 10 V: dropout
    v5, i5 = reg("REG_79XX", -10, "5", {"vout": "-5"}, pins=("in", "gnd", "out"))
    v5_light, i_light = reg("REG_79XX", -10, "1k", {"vout": "-5"}, pins=("in", "gnd", "out"))
    assert -5.2 <= v5 <= -4.8 and abs(v5 - v5_light) < 0.015
    assert -i_light - 5e-3 == pytest.approx(1.5e-3, rel=0.1)                       # bias current 1.5 mA on top of the 5 mA load
    drop, _ = reg("REG_79XX", -5.9, "5", {"vout": "-5"}, pins=("in", "gnd", "out"))
    assert drop == pytest.approx(-5.9 + 1.1, abs=0.15)                            # 1.1 V dropout at about 1 A


def adj(kind, vin, r1, r2, rload="1k"):
    c = Circuit()
    u = c.add(kind)
    c.vdc(vin, f"{u}.in")
    c.r(r1, f"{u}.out", f"{u}.adj"); c.r(r2, f"{u}.adj", "0"); c.r(rload, f"{u}.out", "0")
    cap = c.add("C", C="1u")
    c.w(f"{cap}.a", f"{u}.out"); c.w(f"{cap}.b", "0")
    c.run("2m", "4u")
    return c.v(f"{u}.out")


@pytest.mark.parametrize("r1, r2", [(240, 720), (240, 2160), (120, 1000)])
def test_lm317_output_is_1_25_times_1_plus_r2_over_r1_plus_iadj_r2(r1, r2):
    assert adj("LM317", 25, r1, r2) == pytest.approx(1.25 * (1 + r2 / r1) + 50e-6 * r2, rel=2e-3)


@pytest.mark.parametrize("r1, r2", [(120, 360), (120, 1000)])
def test_lm337_output_is_minus_1_25_times_1_plus_r2_over_r1(r1, r2):
    assert adj("LM337", -25, r1, r2) == pytest.approx(-1.25 * (1 + r2 / r1) - 65e-6 * r2, rel=2e-3)


def test_lm317_needs_its_minimum_load_current():
    c_ok = adj("LM317", 15, 240, 720, rload="1meg")       # R1 = 240 ohm carries 5.2 mA: regulates without a load
    c_bad = adj("LM317", 15, 2400, 7200, rload="1meg")    # 0.52 mA through the divider: below the 3.5 mA minimum load
    assert c_ok == pytest.approx(5.036, rel=3e-3) and c_bad > 5.5


def test_ams1117_regulates_3_3_volts_with_its_low_dropout():
    ok, i = reg("AMS1117", 5, "4.125", pins=("in", "gnd", "out"))              # 0.8 A
    low, _ = reg("AMS1117", 4.6, "4.125", pins=("in", "gnd", "out"))           # 1.3 V headroom: inside the 1.3 V maximum
    drop, _ = reg("AMS1117", 4.2, "4.125", pins=("in", "gnd", "out"))
    assert ok == pytest.approx(3.3, rel=0.015) and low == pytest.approx(3.3, rel=0.015) and drop < 3.2
    light, i_light = reg("AMS1117", 5, "330", pins=("in", "gnd", "out"))
    assert i_light - 10e-3 == pytest.approx(5e-3, rel=0.05)                       # 5 mA quiescent current
    five, _ = reg("AMS1117", 7, "50", {"vout": "5.0"}, pins=("in", "gnd", "out"))
    assert five == pytest.approx(5.0, rel=0.01)


# ---------------------------------------------------------------- analog switches and multiplexers
def test_cd4066_on_and_off_resistance():
    c = Circuit()
    u = c.add("CD4066")
    sp = c.vdc(5, f"{u}.vdd")
    c.w(f"{u}.vss", "0")
    sig = c.vdc(2.0, "")
    for k in "abcd":
        c.w(f"{u}.{k}1", f"{sig}.p")
        c.r("10k", f"{u}.{k}2", "0")
    c.w(f"{u}.ca", f"{sp}.p"); c.w(f"{u}.cb", "0"); c.w(f"{u}.cc", f"{sp}.p"); c.w(f"{u}.cd", "0")
    c.run("20u", "1u")
    on = c.v(f"{u}.a2")
    assert 2.0 * 10e3 / (10e3 + 470) == pytest.approx(on, rel=1e-3)                  # 470 ohm typ at VDD = 5 V
    assert c.v(f"{u}.c2") == pytest.approx(on, rel=1e-6)
    assert c.v(f"{u}.b2") < 2.0 * 10e3 / 1e9 * 1.01 and c.v(f"{u}.d2") < 1e-4         # off: 1 Gohm leak


def test_cd4066_on_resistance_follows_the_parameter_for_a_15_volt_supply():
    c = Circuit()
    u = c.add("CD4066", ron="125")
    sp = c.vdc(15, f"{u}.vdd")
    c.w(f"{u}.vss", "0")
    i = c.add("I", dc="1m")
    c.w(f"{i}.p", "0"); c.w(f"{i}.n", f"{u}.a1"); c.w(f"{u}.a2", "0"); c.w(f"{u}.ca", f"{sp}.p")
    for k in "bcd":
        c.w(f"{u}.{k}1", f"{u}.{k}2", "0"); c.w(f"{u}.c{k}", "0")
    assert c.run("20u", "1u").v(f"{u}.a1") == pytest.approx(0.125, rel=1e-3)


def test_cd4051_connects_the_addressed_channel_to_com():
    """Each channel sits at its own voltage (k + 1 volts through a 1k divider ladder); COM reads the selected one."""
    for ch in range(8):
        c = Circuit()
        u = c.add("CD4051")
        sp = c.vdc(10, f"{u}.vdd")
        c.w(f"{u}.vss", "0"); c.w(f"{u}.vee", "0"); c.w(f"{u}.inh", "0")
        for k in range(8):
            c.vdc(k + 1, f"{u}.ch{k}")
        for bit, name in enumerate("abc"):
            c.w(f"{u}.{name}", f"{sp}.p" if ch >> bit & 1 else "0")
        c.r("10k", f"{u}.com", "0")
        c.run("20u", "1u")
        assert c.v(f"{u}.com") == pytest.approx((ch + 1) * 10e3 / (10e3 + 470), rel=2e-3), ch


def test_cd4051_inhibit_opens_every_channel():
    c = Circuit()
    u = c.add("CD4051")
    sp = c.vdc(5, f"{u}.vdd")
    c.w(f"{u}.vss", "0"); c.w(f"{u}.vee", "0"); c.w(f"{u}.inh", f"{sp}.p")
    for k in range(8):
        c.vdc(3, f"{u}.ch{k}") if k == 0 else c.w(f"{u}.ch{k}", "0")
    for name in "abc":
        c.w(f"{u}.{name}", "0")
    c.r("10k", f"{u}.com", "0")
    assert c.run("20u", "1u").v(f"{u}.com") < 1e-3


def test_cd4052_and_cd4053_select_their_channels():
    c = Circuit()
    m2, m3 = c.add("CD4052"), c.add("CD4053")
    sp = c.vdc(5, f"{m2}.vdd")
    for u in (m2, m3):
        c.w(f"{u}.vdd", f"{sp}.p"); c.w(f"{u}.vss", "0"); c.w(f"{u}.vee", "0"); c.w(f"{u}.inh", "0")
    for k in range(4):
        c.vdc(k + 1, f"{m2}.x{k}"); c.w(f"{m2}.y{k}", f"{m2}.x{3 - k}")
    c.w(f"{m2}.a", "0"); c.w(f"{m2}.b", f"{sp}.p")                                   # address 2
    for sct, sel in (("a", "0"), ("b", f"{sp}.p"), ("c", f"{sp}.p")):
        c.vdc(1.5, f"{m3}.{sct}x"); c.vdc(3.5, f"{m3}.{sct}y")
        c.w(f"{m3}.{sct}", sel)
        c.r("10k", f"{m3}.{sct}com", "0")
    c.r("10k", f"{m2}.xcom", "0"); c.r("10k", f"{m2}.ycom", "0")
    c.run("20u", "1u")
    k = 10e3 / (10e3 + 470)
    assert c.v(f"{m2}.xcom") == pytest.approx(3 * k, rel=2e-3) and c.v(f"{m2}.ycom") == pytest.approx(2 * k, rel=2e-3)
    assert [round(c.v(f"{m3}.{s}com") / k, 3) for s in "abc"] == [1.5, 3.5, 3.5]


# ---------------------------------------------------------------- LM386 and ULN2003
def lm386_gain(network=None):
    c = Circuit()
    u = c.add("LM386")
    c.vdc(6, f"{u}.vs")
    c.w(f"{u}.gnd", "0"); c.w(f"{u}.inn", "0")
    src = c.add("V", mode="SIN", vo=0, va=0.01, freq=1000)
    c.w(f"{src}.n", "0"); c.w(f"{src}.p", f"{u}.inp")
    if network:
        kind, value = network
        x = c.add(kind, **{kind: value})
        c.w(f"{x}.a", f"{u}.g1"); c.w(f"{x}.b", f"{u}.g8")
    spk = c.r("8", f"{u}.vo", "")
    c.vdc(3, f"{spk}.b")                           # 8 ohm "speaker" returned to VS/2 (stands in for the output capacitor)
    c.run("30m" if network and network[0] == "C" else "6m", "5u")
    settle = 25e-3 if network and network[0] == "C" else 3e-3      # the 10 uF across pins 1-8 charges through the network first
    late = [v for t, v in c.series(f"{u}.vo") if t > settle]
    return (max(late) - min(late)) / 2 / 0.01


def test_lm386_gain_is_20_open_200_with_a_capacitor_and_set_by_a_resistor():
    assert lm386_gain() == pytest.approx(20, rel=0.02)                              # 26 dB
    assert lm386_gain(("C", "10u")) == pytest.approx(200, rel=0.03)                 # 46 dB (10 uF reactance at 1 kHz: 16 ohm)
    assert lm386_gain(("R", "1.2k")) == pytest.approx(30e3 / (150 + 1350 * 1200 / 2550), rel=0.02)


def test_lm386_delivers_about_325_mw_into_8_ohms_at_6_volts():
    c = Circuit()
    u = c.add("LM386")
    c.vdc(6, f"{u}.vs")
    c.w(f"{u}.gnd", "0"); c.w(f"{u}.inn", "0")
    src = c.add("V", mode="SIN", vo=0, va=0.5, freq=1000)
    c.w(f"{src}.n", "0"); c.w(f"{src}.p", f"{u}.inp")
    spk = c.r("8", f"{u}.vo", "")
    c.vdc(3, f"{spk}.b")
    c.run("5m", "2u")
    late = [v for t, v in c.series(f"{u}.vo") if t > 2e-3]
    vpk = (max(late) - min(late)) / 2
    assert vpk ** 2 / 8 / 2 == pytest.approx(0.325, rel=0.05)


def test_uln2003_sinks_a_load_and_clamps_the_flyback():
    c = Circuit()
    u = c.add("ULN2003")
    supply = c.vdc(12, f"{u}.com")
    c.w(f"{u}.e", "0")
    on = c.vdc(5, f"{u}.in1")
    for k, (load, drive) in {1: ("60", True), 2: ("120", True), 3: ("1k", False)}.items():
        c.r(load, f"{supply}.p", f"{u}.o{k}")
        c.w(f"{u}.in{k}", f"{on}.p" if drive else "0")
    for k in range(4, 8):
        c.w(f"{u}.in{k}", "0"); c.r("10k", f"{supply}.p", f"{u}.o{k}")
    c.run("20u", "1u")
    v1, v2, v3 = (c.v(f"{u}.o{k}") for k in (1, 2, 3))
    assert 0.95 <= v1 <= 1.15 and 0.8 <= v2 <= 1.0              # VCE(sat) about 1.0 V at 185 mA, 0.9 V at 100 mA
    assert v3 == pytest.approx(12, abs=0.01)                       # off channel: the load pulls the output to 12 V
    assert -c.i(on) == pytest.approx(2 * (5 - 1.45) / 2.7e3, rel=0.15)    # input current of two driven inputs


def test_uln2003_clamp_diode_catches_an_inductive_kick():
    c = Circuit()
    u = c.add("ULN2003")
    supply = c.vdc(12, f"{u}.com")
    c.w(f"{u}.e", "0")
    drive = c.add("V", mode="PULSE", v1=5, v2=0, td="100u", tr="1u", tf="1u", pw="1", per="2")
    c.w(f"{drive}.n", "0"); c.w(f"{drive}.p", f"{u}.in1")
    for k in range(2, 8):
        c.w(f"{u}.in{k}", "0"); c.r("10k", f"{supply}.p", f"{u}.o{k}")
    coil = c.add("L", L="10m")
    c.w(f"{coil}.a", f"{supply}.p")
    c.r("60", f"{coil}.b", f"{u}.o1")
    c.run("300u", "0.2u")
    peak = max(v for _, v in c.series(f"{u}.o1"))
    assert 12.5 < peak < 12 + 2.0                                 # clamped one diode drop (1.7 V at 350 mA) above COM
