"""Library batch D (task 8): digital and mixed-signal ICs - logic gates, Schmitt triggers, tri-state buffers, flip-flops and
latches, counters, shift registers, digital multiplexers, an 8-bit ADC and an 8-bit R-2R DAC.

Design (see HANDOFF.md, Task 8): every part is an ngspice subcircuit. The digital logic is built from XSPICE code models (d_nand,
d_dff, d_jkff, d_dlatch, ...) between analog bridges, so every pin is an ordinary analog node: inputs go through a supply-ratiometric
adc_bridge stage and outputs through a dac_bridge, a polynomial source that scales it to the supply pins, and a series ROUT. Because
XSPICE `A` devices and B sources are not analysed by Bench's static validator, every such part validates as `limited` (the
note names exactly what was left to ngspice); only the R-2R DAC (resistors and switches) is `full`.

The behaviour is checked through the REAL path (Document -> netlist -> app.sim -> benchcore -> ngspice): exact logic function against
truth tables, state sequences and closed forms (`analytic`), and the datasheet levels the gate models target (`reference`). The models
are project-authored behavioural fits, NOT vendor models: a pass means the logic function and the nominal levels hold, not that
propagation delay, fan-out or power match a manufacturer's part."""
import itertools

import pytest

from app.catalog import BUILTIN_ROOT, CATALOG
from app.model import Document
from app.sim import run_document
from test_library_batch_c import Circuit, crossings

NEW = {   # id -> (model_kind, verification, validator status)
    "SN74HC00": ("behavioral", "reference", "limited"), "SN74HC02": ("behavioral", "reference", "limited"),
    "SN74HC04": ("behavioral", "reference", "limited"), "SN74HC08": ("behavioral", "reference", "limited"),
    "SN74HC32": ("behavioral", "reference", "limited"), "SN74HC86": ("behavioral", "reference", "limited"),
    "SN74LS00": ("behavioral", "reference", "limited"),
    "CD4001": ("behavioral", "reference", "limited"), "CD4011": ("behavioral", "reference", "limited"),
    "CD4069": ("behavioral", "reference", "limited"), "CD4070": ("behavioral", "reference", "limited"),
    "CD4081": ("behavioral", "reference", "limited"),
    "SN74HC14": ("behavioral", "reference", "limited"), "CD40106": ("behavioral", "reference", "limited"),
    "SN74HC125": ("behavioral", "reference", "limited"), "SN74HC244": ("behavioral", "reference", "limited"),
    "SN74HC74": ("behavioral", "analytic", "limited"), "SN74HC107": ("behavioral", "analytic", "limited"),
    "CD4013": ("behavioral", "analytic", "limited"), "CD4027": ("behavioral", "analytic", "limited"),
    "SN74HC573": ("behavioral", "analytic", "limited"),
    "SN74HC161": ("behavioral", "analytic", "limited"), "CD4017": ("behavioral", "analytic", "limited"),
    "CD4040": ("behavioral", "analytic", "limited"), "SN74HC393": ("behavioral", "analytic", "limited"),
    "SN74HC595": ("behavioral", "analytic", "limited"), "CD4094": ("behavioral", "analytic", "limited"),
    "SN74HC151": ("behavioral", "analytic", "limited"), "SN74HC157": ("behavioral", "analytic", "limited"),
    "ADC_8BIT": ("behavioral", "analytic", "limited"), "DAC_8BIT": ("project-subckt", "analytic", "full"),
}
LIMITED = {k for k, v in NEW.items() if v[2] == "limited"}


# ---------------------------------------------------------------- helpers
class C(Circuit):
    """Circuit with logic helpers."""

    def power(self, u, v=5, vcc="vcc", gnd="gnd"):
        s = self.vdc(v, f"{u}.{vcc}")
        self.w(f"{u}.{gnd}", "0")
        return s

    def pulse(self, pin, td, pw, per, v=5, tr="10n"):
        s = self.add("V", mode="PULSE", v1=0, v2=v, td=td, tr=tr, tf=tr, pw=pw, per=per)
        self.w(f"{s}.p", pin)
        self.w(f"{s}.n", "0")
        return s

    def clock(self, pin, T, v=5, td=0.0):
        """Rising edges at td + T/2 + k*T (high for the second half of each period)."""
        return self.pulse(pin, td + T / 2, T / 2 - 1e-8, T, v)

    def dc(self, pin, v):
        return self.vdc(v, pin)

    def counter_bit(self, pin, k, T, v=5):
        """Level (j >> k) & 1 during slot j (slot j = [j*T, (j+1)*T))."""
        return self.pulse(pin, T * 2 ** k, T * 2 ** k - 1e-8, T * 2 ** (k + 1), v)

    def pattern(self, pin, bits, T, v=5, td=0.0):
        """Level bits[i] during [td + i*T, td + (i+1)*T), then low: one pulse per run of ones, in series."""
        runs, i = [], 0
        while i < len(bits):
            if bits[i]:
                j = i
                while j < len(bits) and bits[j]:
                    j += 1
                runs.append((i, j))
                i = j
            else:
                i += 1
        if not runs:
            return self.dc(pin, 0)
        prev = "0"
        for a, b in runs:
            s = self.add("V", mode="PULSE", v1=0, v2=v, td=td + a * T, tr="10n", tf="10n", pw=(b - a) * T - 1e-8, per=1)
            self.w(f"{s}.n", prev)
            prev = f"{s}.p"
        self.w(prev, pin)

    def load(self, pin, r="100k"):
        return self.r(r, pin, "0")

    def hi(self, pin, t, vdd=5.0):
        return self.v(pin, t) > vdd / 2

    def word(self, pins, t, vdd=5.0):
        return sum(int(self.hi(p, t, vdd)) << i for i, p in enumerate(pins))


def pins_of(id):
    return [p.id for p in CATALOG.get(id).pins]


# ---------------------------------------------------------------- every package loads, validates and records its kind
@pytest.mark.parametrize("id", sorted(NEW))
def test_package_loads_validates_and_records_its_kind(id):
    c = CATALOG.get(id)
    s = c.sim["electrical"]
    kind, ver, status = NEW[id]
    assert CATALOG.problems == [] and c.simulated and c.readiness == "simulated" and c.path.is_relative_to(BUILTIN_ROOT)
    assert c.category == "Electrical" and c.subcategory and (s["model_kind"], s["verification"]) == (kind, ver)
    assert s["device"] == "X" and s["_status"] == status, id
    assert bool(s["_limits"]) == (status == "limited"), id
    assert s["provenance"]["license"] == "MIT" and s["provenance"]["source"].startswith("Bench project")
    assert any("not a vendor" in x for x in c.limitations) or id == "DAC_8BIT", id
    if s["verification"] == "reference":       # a datasheet fit says which datasheet and which revision
        assert s["provenance"]["url"].startswith("https://") and s["provenance"]["version"], id
    if status == "limited":                    # the generated note names the A devices / B sources left to ngspice
        note = [x for x in c.limitations if x.startswith("Static validation is limited")]
        assert note and ("XSPICE code-model device" in note[0]), id
    assert not any(w in " ".join(c.limitations).lower() for w in ("validated against", "manufacturer-accurate"))
    ids = [p.id for p in c.pins]
    assert len(ids) == len(set(ids)) and ("vcc" in ids or "vdd" in ids or id == "DAC_8BIT")


def test_catalog_grew_by_exactly_these_parts():
    assert set(NEW) <= set(CATALOG.defs) and len(NEW) == 31 and len(CATALOG.defs) >= 117 + len(NEW)     # batch E adds more (test_library_batch_e.py)


def test_shared_model_files_are_emitted_once_per_deck():
    c = C()
    for kind in ("SN74HC00", "SN74HC74", "SN74HC161", "SN74HC595", "CD4017", "SN74HC151", "ADC_8BIT", "SN74HC14"):
        u = c.add(kind)
        for pin in CATALOG.get(kind).pins:
            c.r("1meg", f"{u}.{pin.id}", "0")
    lines = c.d.netlist()["models"]
    for f in ("io.lib", "gates.lib", "seq.lib", "mux.lib", "conv.lib"):
        assert len([m for m in lines if m.startswith("* bench-def") and m.split()[2].endswith("digital/" + f)]) == 1, f
    assert sum(1 for m in lines if m.lower().startswith(".subckt bench_din ")) == 1


# ---------------------------------------------------------------- logic gates: truth tables
TT = {   # id -> (function of the inputs, inputs per gate, gates, supply pins, supply V)
    "SN74HC00": (lambda a, b: not (a and b), 2, 4, ("vcc", "gnd"), 5), "SN74HC02": (lambda a, b: not (a or b), 2, 4, ("vcc", "gnd"), 5),
    "SN74HC04": (lambda a: not a, 1, 6, ("vcc", "gnd"), 5), "SN74HC08": (lambda a, b: a and b, 2, 4, ("vcc", "gnd"), 5),
    "SN74HC32": (lambda a, b: a or b, 2, 4, ("vcc", "gnd"), 5), "SN74HC86": (lambda a, b: a != b, 2, 4, ("vcc", "gnd"), 5),
    "SN74LS00": (lambda a, b: not (a and b), 2, 4, ("vcc", "gnd"), 5),
    "CD4001": (lambda a, b: not (a or b), 2, 4, ("vdd", "vss"), 10), "CD4011": (lambda a, b: not (a and b), 2, 4, ("vdd", "vss"), 10),
    "CD4069": (lambda a: not a, 1, 6, ("vdd", "vss"), 10), "CD4070": (lambda a, b: a != b, 2, 4, ("vdd", "vss"), 10),
    "CD4081": (lambda a, b: a and b, 2, 4, ("vdd", "vss"), 10),
}


@pytest.mark.parametrize("id", sorted(TT))
def test_gate_truth_table_for_every_gate_in_the_package(id):
    """All gates of the package share the same input pattern; every output is checked against the full truth table."""
    fn, nin, ngates, (vcc, gnd), vdd = TT[id]
    c = C()
    u = c.add(id)
    c.power(u, vdd, vcc, gnd)
    T = 2e-6
    for k in range(nin):                       # input k of every gate is one net, driven by counter bit k
        first = f"{u}.{'ab'[k]}1" if nin == 2 else f"{u}.a1"
        c.counter_bit(first, k, T, vdd)
        for g in range(2, ngates + 1):
            c.w(first, f"{u}.{'ab'[k]}{g}" if nin == 2 else f"{u}.a{g}")
    for g in range(1, ngates + 1):
        c.load(f"{u}.y{g}")
    c.run(tstop=f"{2 ** nin * T:.3e}", tstep="20n")
    high, low = {"SN74LS00": (3.3, 0.35)}.get(id, (vdd * 0.995, 0.02))
    for j in range(2 ** nin):
        bits = [bool((j >> k) & 1) for k in range(nin)]
        want = fn(*bits)
        for g in range(1, ngates + 1):
            v = c.v(f"{u}.y{g}", (j + 0.9) * T)
            assert (v >= high) if want else (v <= low), (id, g, bits, v)
            if id == "SN74LS00":
                assert (3.3 <= v <= 3.5) if want else (0.2 <= v <= 0.3), v


# ---------------------------------------------------------------- levels: thresholds, output drive, supply tracking
def nand_with(id, vcc, a_level=None, b_level=None, load=None, a_ramp=None, tstop="2u", tstep="20n", pins=("a1", "b1", "y1"), sup=("vcc", "gnd")):
    c = C()
    u = c.add(id)
    c.power(u, vcc, *sup)
    a, b, y = (f"{u}.{p}" for p in pins)
    if a_ramp is not None:
        c.pulse(a, 0, 1, 10, v=a_ramp[0], tr=a_ramp[1])
    else:
        c.dc(a, a_level)
    c.dc(b, vcc if b_level is None else b_level)
    c.load(y, load or "1meg")
    return c, u, y, a


@pytest.mark.parametrize("vcc", [3.3, 5.0, 6.0])
def test_hc_levels_scale_with_the_supply_and_guaranteed_inputs_are_read_correctly(vcc):
    """VIL = 0.3 x VCC reads low (output high), VIH = 0.7 x VCC reads high (output low); outputs swing to the rails."""
    for a_level, want_high in ((0.3 * vcc, True), (0.7 * vcc, False), (0.0, True), (vcc, False)):
        c, u, y, _ = nand_with("SN74HC00", vcc, a_level)
        c.run()
        assert c.v(y) == pytest.approx(vcc if want_high else 0.0, abs=0.02), (vcc, a_level)


@pytest.mark.parametrize("vcc", [3.3, 5.0])
def test_hc_switching_point_is_half_the_supply(vcc):
    """A slow ramp on a NAND input: the output starts to fall inside the 0.45-0.55 x VCC band and is down by its upper edge."""
    c, u, y, a = nand_with("SN74HC00", vcc, a_ramp=(vcc, "100u"), tstop="105u", tstep="100n")
    c.run(tstop="105u", tstep="100n")
    ys = c.series(y)
    t_start = next(t for t, v in ys if v < 0.9 * vcc)
    t_done = next(t for t, v in ys if v < 0.1 * vcc)
    assert 0.40 * vcc <= c.v(a, t_start) <= 0.50 * vcc and 0.50 * vcc <= c.v(a, t_done) <= 0.60 * vcc


@pytest.mark.parametrize("vcc", [4.75, 5.0, 5.25])
def test_ls_input_thresholds_are_ttl_levels_independent_of_the_supply(vcc):
    """TTL: below 0.8 V is low, above 2.0 V is high (the model switches at about 1.3 V whatever VCC is)."""
    for a_level, want_high in ((0.8, True), (2.0, False)):
        c, u, y, _ = nand_with("SN74LS00", vcc, a_level, b_level=vcc)
        c.run()
        assert (c.v(y) > 3.0) if want_high else (c.v(y) < 0.5), (vcc, a_level, c.v(y))
    c, u, y, a = nand_with("SN74LS00", vcc, a_ramp=(vcc, "100u"), tstop="105u", tstep="100n")
    c.run(tstop="105u", tstep="100n")
    t = next(t for t, v in c.series(y) if v < 1.0)
    assert 1.1 <= c.v(a, t) <= 1.6


@pytest.mark.parametrize("vdd", [5.0, 10.0, 15.0])
def test_cd4000_is_ratiometric(vdd):
    """CD4011: 0.3 x VDD reads low and 0.7 x VDD high at every supply; outputs reach both rails."""
    for a_level, want_high in ((0.3 * vdd, True), (0.7 * vdd, False)):
        c, u, y, _ = nand_with("CD4011", vdd, a_level, pins=("a1", "b1", "y1"), sup=("vdd", "vss"))
        c.run()
        assert c.v(y) == pytest.approx(vdd if want_high else 0.0, abs=0.02 * vdd), (vdd, a_level)


def test_output_impedance_and_the_datasheet_drive_windows():
    """ROUT sets VOH/VOL under load. 74HC00 datasheet at 4 mA (4.5 V): VOH >= 3.98 V (3.84 over temperature), VOL <= 0.26 V (0.33)."""
    c, u, y, _ = nand_with("SN74HC00", 5, 0.0, load="1250")           # high output into 1250 ohm: 4 mA
    c.run()
    assert c.v(y) == pytest.approx(5 * 1250 / (1250 + 45), rel=1e-3) and c.v(y) >= 3.98
    c = C()
    u = c.add("SN74HC00")
    c.power(u, 5)
    c.dc(f"{u}.a1", 5)
    c.dc(f"{u}.b1", 5)
    r = c.add("R", R="1250")                                          # low output sinking 4 mA from the supply
    c.vdc(5, f"{r}.a")
    c.w(f"{r}.b", f"{u}.y1")
    c.run()
    assert c.v(f"{u}.y1") == pytest.approx(5 * 45 / (1250 + 45), rel=1e-2) and c.v(f"{u}.y1") <= 0.26
    c = C()                                                           # the rout parameter reaches the netlist
    u = c.add("SN74HC00", rout="200")
    c.power(u, 5)
    c.dc(f"{u}.a1", 0)
    c.dc(f"{u}.b1", 5)
    c.load(f"{u}.y1", "1k")
    c.run()
    assert c.v(f"{u}.y1") == pytest.approx(5 * 1000 / 1200, rel=1e-3)


def test_ls_output_levels_and_cd4000_output_impedance():
    c, u, y, _ = nand_with("SN74LS00", 5.0, 0.0, load="100k")
    c.run()
    assert 3.3 <= c.v(y) <= 3.5                                        # VOH typ 3.4 V
    c2 = C()                                                           # low output sinking 8 mA: VOL <= 0.5 V (datasheet 0.35 typ, 0.5 max)
    u2 = c2.add("SN74LS00")
    c2.power(u2, 5)
    c2.dc(f"{u2}.a1", 5)
    c2.dc(f"{u2}.b1", 5)
    r = c2.add("R", R="560")                                           # (5 - 0.35) / 560 = 8.3 mA
    c2.vdc(5, f"{r}.a")
    c2.w(f"{r}.b", f"{u2}.y1")
    c2.run()
    assert 0.25 <= c2.v(f"{u2}.y1") <= 0.5
    c = C()
    u = c.add("CD4011")                                                # 400 ohm at 10 V: 1 mA drops 0.4 V (datasheet IOL min 1.3 mA at 0.5 V)
    c.power(u, 10, "vdd", "vss")
    c.dc(f"{u}.a1", 10)
    c.dc(f"{u}.b1", 10)
    r = c.add("R", R="10k")
    c.vdc(10, f"{r}.a")
    c.w(f"{r}.b", f"{u}.y1")
    c.run()
    assert c.v(f"{u}.y1") == pytest.approx(10 * 400 / 10400, rel=1e-2)


def _delay(id, pins, sup, vdd, edge):
    """Time from the 50 % point of an input edge to the 50 % point of the output edge (ns)."""
    c = C()
    u = c.add(id)
    c.power(u, vdd, *sup)
    a, b, y = (f"{u}.{p}" for p in pins)
    c.pulse(a, 50e-9, 100e-9, 400e-9, v=vdd, tr="10n")
    c.dc(b, vdd)
    c.load(y, "100k")
    c.run(tstop="400n", tstep="0.5n")
    t_in = crossings(c.series(a), vdd / 2, rising=(edge == "rise"))[0]
    t_out = crossings(c.series(y), vdd / 2, rising=(edge == "fall"))[0]
    return (t_out - t_in) * 1e9


@pytest.mark.parametrize("id, sup, vdd, lo, hi", [("SN74HC00", ("vcc", "gnd"), 5, 9, 18), ("SN74LS00", ("vcc", "gnd"), 5, 8, 15), ("CD4011", ("vdd", "vss"), 10, 55, 140)])
@pytest.mark.parametrize("edge", ["rise", "fall"])
def test_propagation_delay_is_the_tpd_parameter_plus_the_bridges(id, sup, vdd, lo, hi, edge):
    """NAND input to output: TPD + about 2 ns of bridge delay, inside the datasheet typical-to-maximum window."""
    d = _delay(id, ("a1", "b1", "y1"), sup, vdd, edge)
    assert lo <= d <= hi, d


def test_tpd_parameter_sets_the_delay():
    c = C()
    u = c.add("SN74HC00", tpd="40n")
    c.power(u, 5)
    c.pulse(f"{u}.a1", 50e-9, 300e-9, 800e-9, tr="10n")
    c.dc(f"{u}.b1", 5)
    c.load(f"{u}.y1", "100k")
    c.run(tstop="400n", tstep="0.5n")
    d = (crossings(c.series(f"{u}.y1"), 2.5, rising=False)[0] - crossings(c.series(f"{u}.a1"), 2.5)[0]) * 1e9
    assert 40 <= d <= 46


# ---------------------------------------------------------------- Schmitt triggers: real hysteresis
def staircase(c, pin, steps):
    """Voltage that equals `level` from time `t` on, for each (t, level) in order: one step source per change, in series."""
    prev, last = "0", 0.0
    for t, level in steps:
        s = c.add("V", mode="PULSE", v1=0, v2=level - last, td=t, tr="10n", tf="10n", pw=1, per=10)
        c.w(f"{s}.n", prev)
        prev, last = f"{s}.p", level
    c.w(prev, pin)


@pytest.mark.parametrize("id, sup, vdd, vtp, vtn", [("SN74HC14", ("vcc", "gnd"), 5.0, 0.556, 0.356), ("SN74HC14", ("vcc", "gnd"), 3.3, 0.556, 0.356),
                                                     ("CD40106", ("vdd", "vss"), 5.0, 0.6, 0.38), ("CD40106", ("vdd", "vss"), 10.0, 0.6, 0.38),
                                                     ("CD40106", ("vdd", "vss"), 15.0, 0.6, 0.38)])
def test_schmitt_thresholds_from_a_slow_triangle(id, sup, vdd, vtp, vtn):
    """The output falls when the rising input crosses VT+ and rises when the falling input crosses VT-: two distinct thresholds."""
    c = C()
    u = c.add(id)
    c.power(u, vdd, *sup)
    s = c.add("V", mode="PULSE", v1=0, v2=vdd, td=1e-6, tr="100u", tf="100u", pw="1u", per=1)
    c.w(f"{s}.p", f"{u}.a1")
    c.w(f"{s}.n", "0")
    c.load(f"{u}.y1")
    c.run(tstop="215u", tstep="100n")
    ys, a = c.series(f"{u}.y1"), f"{u}.a1"
    t_fall = crossings(ys, vdd / 2, rising=False)[0]
    t_rise = crossings(ys, vdd / 2, rising=True)[-1]
    assert t_fall < 101e-6 < t_rise                              # falls on the way up, rises on the way down
    assert c.v(a, t_fall) == pytest.approx(vtp * vdd, abs=0.03 * vdd)
    assert c.v(a, t_rise) == pytest.approx(vtn * vdd, abs=0.03 * vdd)
    assert c.v(a, t_fall) - c.v(a, t_rise) == pytest.approx((vtp - vtn) * vdd, abs=0.04 * vdd)


def test_schmitt_holds_its_state_between_the_thresholds_where_a_plain_inverter_does_not():
    """Held at 2.2 V (inside the HC14 band 1.78-2.78 V) the output depends on history; the 74HC04 reads 2.2 V as low."""
    c = C()
    u, g = c.add("SN74HC14"), c.add("SN74HC04")
    c.power(u)
    c.power(g)
    steps = [(1e-6, 2.2), (3e-6, 5.0), (5e-6, 2.2), (7e-6, 0.0), (9e-6, 2.2)]
    staircase(c, f"{u}.a1", steps)
    c.w(f"{u}.a1", f"{g}.a1")
    c.load(f"{u}.y1")
    c.load(f"{g}.y1")
    c.run(tstop="11u", tstep="20n")
    seq = [(0.5e-6, 5.0), (2.5e-6, 5.0), (4.5e-6, 0.0), (6.5e-6, 0.0), (8.5e-6, 5.0), (10.5e-6, 5.0)]
    for t, want in seq:
        assert c.v(f"{u}.y1", t) == pytest.approx(want, abs=0.02), t
    assert c.v(f"{g}.y1", 2.5e-6) == pytest.approx(5.0, abs=0.02) and c.v(f"{g}.y1", 6.5e-6) == pytest.approx(5.0, abs=0.02)   # 2.2 V < 2.25 V: low
    assert c.v(f"{u}.y1", 10.5e-6) == pytest.approx(5.0, abs=0.02)


def test_schmitt_ignores_noise_inside_the_band_and_follows_a_full_swing():
    """1 MHz sine of 0.9 V peak-to-peak centred at 2.28 V (inside the 1.78-2.78 V band): no output edge. A 5 V swing: one edge per half-cycle."""
    def edges(vo, va):
        c = C()
        u = c.add("SN74HC14")
        c.power(u)
        s = c.add("V", mode="SIN", vo=vo, va=va, freq=1e6)
        c.w(f"{s}.p", f"{u}.a1")
        c.w(f"{s}.n", "0")
        c.load(f"{u}.y1")
        c.run(tstop="5u", tstep="10n")
        ys = [(t, v) for t, v in c.series(f"{u}.y1") if t > 0.2e-6]      # the state at power-up inside the band is undefined
        return len(crossings(ys, 2.5, rising=True)) + len(crossings(ys, 2.5, rising=False)), min(v for _, v in ys), max(v for _, v in ys)
    n, lo, hi = edges(2.28, 0.45)
    assert n == 0 and (lo > 4.9 or hi < 0.1)
    n, lo, hi = edges(2.5, 2.5)
    assert 9 <= n <= 10 and lo < 0.1 and hi > 4.9


def test_hex_schmitt_every_gate_inverts_independently():
    c = C()
    u = c.add("CD40106")
    c.power(u, 10, "vdd", "vss")
    for g in range(1, 7):
        c.counter_bit(f"{u}.a{g}", g - 1, 4e-6, 10)
        c.load(f"{u}.y{g}")
    c.run(tstop="256u", tstep="200n")
    for j in range(64):
        assert [c.hi(f"{u}.y{g}", (j + 0.9) * 4e-6, 10) for g in range(1, 7)] == [not (j >> (g - 1)) & 1 for g in range(1, 7)], j


# ---------------------------------------------------------------- tri-state buffers
@pytest.mark.parametrize("id, n", [("SN74HC125", 4), ("SN74HC244", 8)])
def test_tristate_buffers_pass_data_when_enabled_and_float_when_not(id, n):
    c = C()
    u = c.add(id)
    c.power(u)
    T = 2e-6
    ref = c.add("V", dc=2.5)                                   # a 10 k pull towards 2.5 V on every output
    c.w(f"{ref}.n", "0")
    first = f"{u}.a1"
    c.counter_bit(first, 0, T)
    if id == "SN74HC125":
        c.counter_bit(f"{u}.oe1", 1, T)
        for g in range(2, 5):
            c.w(f"{u}.oe1", f"{u}.oe{g}")
    else:
        c.counter_bit(f"{u}.oe1", 1, T)
        c.counter_bit(f"{u}.oe2", 2, T)
    for g in range(2, n + 1):
        c.w(first, f"{u}.a{g}")
    for g in range(1, n + 1):
        c.r("10k", f"{u}.y{g}", f"{ref}.p")
    c.run(tstop="16u", tstep="20n")
    for j in range(8 if id == "SN74HC244" else 4):
        a, oe1 = j & 1, (j >> 1) & 1
        oe2 = ((j >> 2) & 1) if id == "SN74HC244" else oe1
        for g in range(1, n + 1):
            oe = oe1 if g <= (4 if id == "SN74HC244" else n) else oe2
            v = c.v(f"{u}.y{g}", (j + 0.9) * T)
            want = 2.5 if oe else (5.0 if a else 0.0)
            assert v == pytest.approx(want, abs=0.05), (id, j, g, v)


def test_unwired_output_enable_reads_as_enabled():
    c = C()
    u = c.add("SN74HC125")
    c.power(u)
    c.dc(f"{u}.a1", 5)
    ref = c.add("V", dc=1.0)
    c.w(f"{ref}.n", "0")
    c.r("10k", f"{u}.y1", f"{ref}.p")
    c.run()
    assert c.v(f"{u}.y1") > 4.9                                          # driven high by the buffer, not floating at the 1 V pull


# ---------------------------------------------------------------- flip-flops
T = 2e-6
FF = {   # id -> (clock-edge offset inside a period for the active edge, set pin, reset pin, active level of set/reset, supply)
    "SN74HC74": dict(sup=("vcc", "gnd"), vdd=5, s="pre", r="clr", act=0), "CD4013": dict(sup=("vdd", "vss"), vdd=10, s="s", r="r", act=1),
}


def ff_circuit(id, g=1):
    c = C()
    u = c.add(id)
    f = FF[id]
    c.power(u, f["vdd"], *f["sup"])
    inactive = 0 if f["act"] else f["vdd"]
    for g_ in (1, 2):
        if g_ != g:
            c.dc(f"{u}.{f['s']}{g_}", inactive); c.dc(f"{u}.{f['r']}{g_}", inactive)
    return c, u, f


@pytest.mark.parametrize("id", sorted(FF))
def test_d_flip_flop_captures_d_on_the_rising_clock_edge_only(id):
    """D changes just after each rising edge. Q takes the value D had AT the edge, never follows D between edges; Q-bar is the complement."""
    c, u, f = ff_circuit(id)
    vdd = f["vdd"]
    inactive = 0 if f["act"] else vdd
    bits = [0, 1, 1, 0, 1, 0, 0, 1, 1, 0]
    c.clock(f"{u}.ck1", T, vdd)                                  # rising at (k + 0.5) T
    c.pattern(f"{u}.d1", bits, T, vdd, td=0.75 * T)              # D changes at (k + 0.75) T: bits[k - 1] is the value at edge k
    c.dc(f"{u}.{f['s']}1", inactive)
    c.dc(f"{u}.{f['r']}1", inactive)
    c.load(f"{u}.q1"); c.load(f"{u}.qb1")
    c.run(tstop=f"{(len(bits) + 1) * T:.3e}", tstep="20n")
    for k in range(1, len(bits)):
        want = bool(bits[k - 1])
        for t in ((k + 0.6) * T, (k + 0.74) * T):               # right after the edge, and just before D moves again
            assert c.hi(f"{u}.q1", t, vdd) == want, (id, k, t)
            assert c.hi(f"{u}.qb1", t, vdd) == (not want), (id, k)
        assert c.hi(f"{u}.q1", (k + 0.95) * T, vdd) == want      # D has moved (to bits[k]) but Q has not
    assert any(bits[k] != bits[k - 1] for k in range(1, len(bits)))


@pytest.mark.parametrize("id", sorted(FF))
def test_flip_flop_asynchronous_set_and_reset_override_the_clock(id):
    c, u, f = ff_circuit(id)
    vdd, act = f["vdd"], f["act"]
    c.clock(f"{u}.ck1", T, vdd)
    c.dc(f"{u}.d1", 0)                                          # D = 0: the clock alone would keep Q low
    on, off = (vdd, 0.0) if act else (0.0, vdd)
    # set asserted from 1.0 T to 1.4 T (between clock edges), reset from 2.0 T to 2.3 T
    s = c.add("V", mode="PULSE", v1=off, v2=on, td=1.0 * T, tr="10n", tf="10n", pw=0.4 * T, per=100 * T)
    r = c.add("V", mode="PULSE", v1=off, v2=on, td=2.0 * T, tr="10n", tf="10n", pw=0.3 * T, per=100 * T)
    for src, pin in ((s, f["s"]), (r, f["r"])):
        c.w(f"{src}.p", f"{u}.{pin}1"); c.w(f"{src}.n", "0")
    c.load(f"{u}.q1"); c.load(f"{u}.qb1")
    c.run(tstop=f"{4 * T:.3e}", tstep="20n")
    assert not c.hi(f"{u}.q1", 0.9 * T, vdd)
    assert c.hi(f"{u}.q1", 1.2 * T, vdd) and not c.hi(f"{u}.qb1", 1.2 * T, vdd)           # set: no clock needed
    assert c.hi(f"{u}.q1", 1.45 * T, vdd)                                                 # and it stays set after release (clock edge at 1.5 T takes D = 0 only later)
    assert not c.hi(f"{u}.q1", 2.2 * T, vdd) and c.hi(f"{u}.qb1", 2.2 * T, vdd)          # reset
    assert not c.hi(f"{u}.q1", 3.6 * T, vdd)


@pytest.mark.parametrize("id", sorted(FF))
def test_flip_flop_divides_the_clock_by_two_and_the_two_flip_flops_are_independent(id):
    """Q-bar wired to D: Q toggles on every rising edge. Flip-flop 2 (a different data pattern) is unaffected by flip-flop 1."""
    c, u, f = ff_circuit(id, g=0)
    vdd = f["vdd"]
    c.clock(f"{u}.ck1", T, vdd)
    c.w(f"{u}.qb1", f"{u}.d1")
    c.clock(f"{u}.ck2", T, vdd)
    bits2 = [1, 0, 0, 1, 1, 0, 1, 1]
    c.pattern(f"{u}.d2", bits2, T, vdd, td=0.75 * T)
    c.load(f"{u}.q1"); c.load(f"{u}.q2")
    c.run(tstop=f"{9 * T:.3e}", tstep="20n")
    q1 = [c.hi(f"{u}.q1", (k + 0.9) * T, vdd) for k in range(8)]
    assert q1 == [True, False] * 4                               # toggles every edge (Q starts low, so the first edge sets it)
    assert len(crossings(c.series(f"{u}.q1"), vdd / 2)) == 5     # a clean divide-by-2: Q rises on clock edges 0, 2, 4, 6 and 8
    q2 = [c.hi(f"{u}.q2", (k + 0.9) * T, vdd) for k in range(1, 8)]
    assert q2 == [bool(b) for b in bits2[:7]]


# ---------------------------------------------------------------- JK flip-flops
JK = {"SN74HC107": dict(sup=("vcc", "gnd"), vdd=5, edge=1.0, s=None, r="r", act=0), "CD4027": dict(sup=("vdd", "vss"), vdd=10, edge=0.5, s="s", r="r", act=1)}
JKSEQ = [(1, 0), (0, 0), (0, 1), (0, 0), (1, 1), (1, 1), (1, 1), (0, 1), (1, 0)]
JKQ = [1, 1, 0, 0, 1, 0, 1, 0, 1]                                # Q after each edge


@pytest.mark.parametrize("id", sorted(JK))
def test_jk_flip_flop_set_reset_hold_and_toggle(id):
    f = JK[id]
    vdd = f["vdd"]
    off = 0.0 if f["act"] else vdd
    c = C()
    u = c.add(id)
    c.power(u, vdd, *f["sup"])
    c.clock(f"{u}.ck1", T, vdd)                                   # rising at (k + .5) T, falling at (k + 1) T
    c.pattern(f"{u}.j1", [j for j, _ in JKSEQ], T, vdd, td=0.25 * T)
    c.pattern(f"{u}.k1", [k for _, k in JKSEQ], T, vdd, td=0.25 * T)
    if f["s"]:
        c.dc(f"{u}.{f['s']}1", 0)
    on = vdd if f["act"] else 0.0
    r = c.add("V", mode="PULSE", v1=off, v2=on, td=0.0, tr="10n", tf="10n", pw=0.2 * T, per=100 * T)       # reset at power-up, then released
    c.w(f"{r}.p", f"{u}.{f['r']}1"); c.w(f"{r}.n", "0")
    c.load(f"{u}.q1"); c.load(f"{u}.qb1")
    c.run(tstop=f"{(len(JKSEQ) + 1) * T:.3e}", tstep="20n")
    for k, q in enumerate(JKQ):
        t = (k + f["edge"] + 0.2) * T                              # 0.2 T after the active edge
        assert c.hi(f"{u}.q1", t, vdd) == bool(q), (id, k, JKSEQ[k])
        assert c.hi(f"{u}.qb1", t, vdd) == (not q)
    # the other clock edge does nothing: toggling edges of Q sit on the active clock edge
    tog = crossings(c.series(f"{u}.q1"), vdd / 2) + crossings(c.series(f"{u}.q1"), vdd / 2, rising=False)
    assert tog and all(((t / T) % 1.0 - (f["edge"] % 1.0)) % 1.0 < 0.05 for t in tog if t > 0.5 * T), [t / T for t in tog]


def test_jk_asynchronous_reset_and_set():
    """HC107: R low clears at once, whatever the clock; CD4027: SET high sets and RESET high clears (and SET/RESET beat the clock)."""
    for id, vdd, sup, act in (("SN74HC107", 5, ("vcc", "gnd"), 0), ("CD4027", 10, ("vdd", "vss"), 1)):
        c = C()
        u = c.add(id)
        c.power(u, vdd, *sup)
        c.clock(f"{u}.ck1", T, vdd)
        c.dc(f"{u}.j1", vdd)                                       # J = K = 1: toggles on every active edge
        c.dc(f"{u}.k1", vdd)
        on, off = (vdd, 0.0) if act else (0.0, vdd)
        r = c.add("V", mode="PULSE", v1=off, v2=on, td=3.6 * T, tr="10n", tf="10n", pw=1.0 * T, per=100 * T)
        c.w(f"{r}.p", f"{u}.r1"); c.w(f"{r}.n", "0")
        if act:
            c.dc(f"{u}.s1", 0)
        c.load(f"{u}.q1")
        c.run(tstop=f"{7 * T:.3e}", tstep="20n")
        assert not c.hi(f"{u}.q1", 4.0 * T, vdd) and not c.hi(f"{u}.q1", 4.55 * T, vdd)   # held in reset across the edge at 4.5 T (which would toggle it)
        assert c.hi(f"{u}.q1", 5.8 * T, vdd)                                                   # released: the next active edge toggles it again
    c = C()                                                        # CD4027 set
    u = c.add("CD4027")
    c.power(u, 10, "vdd", "vss")
    c.dc(f"{u}.ck1", 0); c.dc(f"{u}.j1", 0); c.dc(f"{u}.k1", 10); c.dc(f"{u}.r1", 0)
    s = c.add("V", mode="PULSE", v1=0, v2=10, td=1e-6, tr="10n", tf="10n", pw=1e-6, per=100e-6)
    c.w(f"{s}.p", f"{u}.s1"); c.w(f"{s}.n", "0")
    c.load(f"{u}.q1")
    c.run(tstop="5u", tstep="20n")
    assert not c.hi(f"{u}.q1", 0.5e-6, 10) and c.hi(f"{u}.q1", 1.5e-6, 10) and c.hi(f"{u}.q1", 4e-6, 10)


# ---------------------------------------------------------------- 74HC573 octal latch
def test_octal_latch_is_transparent_while_le_is_high_and_holds_when_it_falls():
    c = C()
    u = c.add("SN74HC573")
    c.power(u)
    c.pulse(f"{u}.le", 1e-6, 2e-6 - 1e-8, 4e-6)                    # LE high 1-3 us and 5-7 us
    c.dc(f"{u}.oeb", 0)
    c.pulse(f"{u}.d1", 2e-6, 2e-6 - 1e-8, 100e-6)                  # D1 high 2-4 us; D2..D4 follow D1, D5..D8 stay low
    for i in (2, 3, 4):
        c.w(f"{u}.d1", f"{u}.d{i}")
    for i in (5, 6, 7, 8):
        c.dc(f"{u}.d{i}", 0)
    for i in range(1, 9):
        c.load(f"{u}.q{i}")
    c.run(tstop="8u", tstep="20n")
    want = [(1.5e-6, 0), (2.5e-6, 1), (3.5e-6, 1), (4.5e-6, 1), (5.5e-6, 0), (7.5e-6, 0)]    # (time, Q1..Q4): follow, follow, hold, hold (D fell at 4 us), follow, hold
    for t, q in want:
        assert [c.hi(f"{u}.q{i}", t) for i in range(1, 9)] == [bool(q)] * 4 + [False] * 4, t


def test_octal_latch_output_enable_tristates_all_eight_outputs():
    c = C()
    u = c.add("SN74HC573")
    c.power(u)
    c.dc(f"{u}.le", 5)
    c.dc(f"{u}.d1", 5)
    for i in range(2, 9):
        c.dc(f"{u}.d{i}", 5 if i % 2 else 0)
    c.pulse(f"{u}.oeb", 2e-6, 2e-6, 100e-6)                         # outputs enabled for 0-2 us and 4 us on, disabled 2-4 us
    ref = c.add("V", dc=2.5)
    c.w(f"{ref}.n", "0")
    for i in range(1, 9):
        c.r("10k", f"{u}.q{i}", f"{ref}.p")
    c.run(tstop="6u", tstep="20n")
    assert [round(c.v(f"{u}.q{i}", 1e-6) / 5) for i in range(1, 9)] == [1, 0, 1, 0, 1, 0, 1, 0]
    assert all(abs(c.v(f"{u}.q{i}", 3e-6) - 2.5) < 0.05 for i in range(1, 9))          # Hi-Z: the pull reaches 2.5 V
    assert [round(c.v(f"{u}.q{i}", 5e-6) / 5) for i in range(1, 9)] == [1, 0, 1, 0, 1, 0, 1, 0]       # enabled again


# ---------------------------------------------------------------- counters
def test_hc161_counts_the_binary_sequence_and_rco_marks_fifteen():
    c = C()
    u = c.add("SN74HC161")
    c.power(u)
    c.clock(f"{u}.clk", T)
    for p in ("clrb", "loadb", "enp", "ent", "a", "b", "c", "d"):         # clear/load/enables wired inactive-high; data low
        c.dc(f"{u}.{p}", 0 if p in "abcd" else 5)
    for p in ("qa", "qb", "qc", "qd", "rco"):
        c.load(f"{u}.{p}")
    c.run(tstop=f"{40 * T:.3e}", tstep="40n")
    for k in range(40):                                              # k + 1 edges seen at the sample after edge k
        t = (k + 0.5) * T + 0.4 * T
        n = (k + 1) % 16
        assert c.word([f"{u}.q{x}" for x in "abcd"], t) == n, (k, n)
        assert c.hi(f"{u}.rco", t) == (n == 15), k
    assert c.word([f"{u}.q{x}" for x in "abcd"], 0.4 * T) == 0       # starts at zero before the first edge


def test_hc161_unwired_enables_and_load_default_to_counting():
    c = C()
    u = c.add("SN74HC161")
    c.power(u)
    c.clock(f"{u}.clk", T)
    for p in ("qa", "qb", "qc", "qd"):
        c.load(f"{u}.{p}")
    c.run(tstop=f"{6 * T:.3e}", tstep="40n")
    assert c.word([f"{u}.q{x}" for x in "abcd"], 5.9 * T) == 6


def low_windows(c, pin, windows, tstop, unit, v=5):
    """Level `v`, except 0 inside each (t0, t1) window (an active-low control pulse): built from `pattern` slots of width `unit`."""
    n = int(round(tstop / unit))
    bits = [0 if any(t0 <= i * unit < t1 - 1e-12 for t0, t1 in windows) else 1 for i in range(n)]
    c.pattern(pin, bits, unit, v)


def test_hc161_async_clear_synchronous_load_and_both_enables():
    c = C()
    u = c.add("SN74HC161")
    c.power(u)
    c.clock(f"{u}.clk", T)                                           # rising edges at 0.5, 1.5, 2.5 ... T
    for p, v in (("a", 0), ("b", 5), ("c", 0), ("d", 5)):            # data 1010 = 10
        c.dc(f"{u}.{p}", v)
    tstop, unit = 18 * T, 0.05 * T
    low_windows(c, f"{u}.loadb", [(2.0 * T, 3.0 * T)], tstop, unit)             # only the edge at 2.5 T sees LOAD low
    low_windows(c, f"{u}.enp", [(6.0 * T, 8.0 * T)], tstop, unit)               # ENP low across the edges at 6.5 and 7.5 T
    low_windows(c, f"{u}.clrb", [(11.6 * T, 11.8 * T)], tstop, unit)            # CLR low while the clock is low
    low_windows(c, f"{u}.ent", [(9.6 * T, 10.4 * T), (14.0 * T, 16.0 * T)], tstop, unit)   # ENT low: RCO off at 15; edges 14.5 and 15.5 hold
    for p in ("qa", "qb", "qc", "qd", "rco"):
        c.load(f"{u}.{p}")
    c.run(tstop=f"{tstop:.3e}", tstep="40n")
    q = lambda t: c.word([f"{u}.q{x}" for x in "abcd"], t * T)
    assert [q(0.9), q(1.9)] == [1, 2]                                # counting: edges at 0.5 and 1.5
    assert [q(2.9), q(3.9), q(4.9)] == [10, 11, 12]                  # the edge at 2.5 loaded 10, then counting resumes
    assert [q(5.9), q(6.9), q(7.9)] == [13, 13, 13]                  # ENP low (6-8 T): the edges at 6.5 and 7.5 hold
    assert [q(8.9), q(9.9)] == [14, 15]
    assert c.hi(f"{u}.rco", 9.55 * T) and not c.hi(f"{u}.rco", 9.9 * T) and c.hi(f"{u}.rco", 10.45 * T)   # RCO = ENT and count 15
    assert q(10.9) == 0 and q(11.55) == 1                            # rolled over at 10.5 T, one more edge at 11.5 T
    assert q(11.9) == 0 and q(12.4) == 0                             # cleared at 11.6 T with no clock edge in between
    assert [q(12.9), q(13.9), q(14.9), q(15.9), q(16.9)] == [1, 2, 2, 2, 3]   # ENT low (14-16 T): edges 14.5, 15.5 hold


def test_two_hc161_chained_through_rco_count_to_forty():
    """Multi-instance: RCO of the low counter enables the high counter, an 8-bit synchronous counter (two copies of one subckt)."""
    c = C()
    lo, hi = c.add("SN74HC161"), c.add("SN74HC161")
    for u in (lo, hi):
        c.power(u)
        for p in ("clrb", "loadb", "enp", "a", "b", "c", "d"):
            c.dc(f"{u}.{p}", 0 if p in "abcd" else 5)
    c.clock(f"{lo}.clk", T)
    c.w(f"{lo}.clk", f"{hi}.clk")
    c.dc(f"{lo}.ent", 5)
    c.w(f"{lo}.rco", f"{hi}.ent")
    for u in (lo, hi):
        for p in ("qa", "qb", "qc", "qd"):
            c.load(f"{u}.{p}")
    c.load(f"{hi}.rco")
    c.run(tstop=f"{40 * T:.3e}", tstep="40n")
    for k in range(40):
        t = (k + 0.9) * T
        n = k + 1
        got = c.word([f"{lo}.q{x}" for x in "abcd"], t) + 16 * c.word([f"{hi}.q{x}" for x in "abcd"], t)
        assert got == n, (k, got, n)
    assert len(c.d.netlist()["models"]) > 0 and sum(1 for m in c.d.netlist()["models"] if m.lower().startswith(".subckt bench_74hc161")) == 1


# ---------------------------------------------------------------- CD4017 decade counter
def high_windows(c, pin, windows, tstop, unit, v=5):
    """Level 0, except `v` inside each (t0, t1) window (an active-high control pulse)."""
    n = int(round(tstop / unit))
    c.pattern(pin, [1 if any(t0 <= i * unit < t1 - 1e-12 for t0, t1 in windows) else 0 for i in range(n)], unit, v)


def test_cd4017_decoded_outputs_carry_reset_and_clock_inhibit():
    c = C()
    u = c.add("CD4017")
    c.power(u, 10, "vdd", "vss")
    c.clock(f"{u}.clk", T, 10)                                           # rising edges at (k + 0.5) T, high until (k + 1) T
    tstop, unit = 30 * T, 0.05 * T
    high_windows(c, f"{u}.rst", [(13.8 * T, 14.0 * T)], tstop, unit, 10)
    high_windows(c, f"{u}.inh", [(3.2 * T, 6.8 * T)], tstop, unit, 10)   # blocks the edges at 3.5, 4.5, 5.5 and 6.5 T
    for i in range(10):
        c.load(f"{u}.q{i}")
    c.load(f"{u}.co")
    c.run(tstop=f"{tstop:.3e}", tstep="50n")

    def state(t):
        return [i for i in range(10) if c.hi(f"{u}.q{i}", t * T, 10)]
    assert state(0.3) == [0] and c.hi(f"{u}.co", 0.3 * T, 10)             # power-up: count 0
    # count after the edge at (k + 0.5) T: 1 2 3, inhibited 3 3 3, then INH falling while CLK is high advances once (as the datasheet
    # says), then 5 6 7 8 9, wraps to 0
    exp = {0: 1, 1: 2, 2: 3, 3: 3, 4: 3, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7, 10: 8, 11: 9, 12: 0}
    for k, n in exp.items():
        assert state(k + 0.95) == [n], (k, n)
        assert c.hi(f"{u}.co", (k + 0.95) * T, 10) == (n < 5), k         # CARRY OUT high for counts 0-4, low for 5-9
    assert state(13.75) == [1] and state(13.95) == [0]                    # reset at 13.8 T clears at once, no clock needed
    assert state(14.95) == [1] and state(15.95) == [2]                    # counting resumes after the reset
    assert all(len(state(k + 0.9)) == 1 for k in range(30))               # always exactly one decoded output


# ---------------------------------------------------------------- ripple counters
def test_cd4040_counts_falling_edges_in_binary_and_reset_clears_it():
    c = C()
    u = c.add("CD4040")
    c.power(u, 10, "vdd", "vss")
    T4 = 4e-6
    c.clock(f"{u}.clk", T4, 10)                                          # falling edges at (k + 1) T4
    tstop, unit = 44 * T4, 0.05 * T4
    high_windows(c, f"{u}.rst", [(20.5 * T4, 21.5 * T4)], tstop, unit, 10)   # a reset that spans the falling edge at 21 T4
    qs = [f"{u}.q{i}" for i in range(1, 13)]
    for q in qs:
        c.load(q)
    c.run(tstop=f"{tstop:.3e}", tstep="100n")
    assert c.word(qs, 0.5 * T4, 10) == 0 and c.word(qs, 0.9 * T4, 10) == 0       # the rising edge at 0.5 T4 does not count
    for n in range(1, 21):
        assert c.word(qs, (n + 0.5) * T4, 10) == n, n
    assert c.word(qs, 20.4 * T4, 10) == 20
    assert c.word(qs, 20.9 * T4, 10) == 0 and c.word(qs, 21.4 * T4, 10) == 0     # held in reset, the falling edge at 21 T4 is ignored
    assert c.word(qs, 22.5 * T4, 10) == 1                                        # released at 21.5 T4: the edge at 22 T4 counts
    for n in range(23, 42):
        assert c.word(qs, (n + 0.5) * T4, 10) == n - 21, n


def test_cd4040_ripple_delay_grows_stage_by_stage():
    """7 -> 8 flips Q1, Q2, Q3 (falling) and Q4 (rising): each change comes one stage delay (TPD + bridges) after the previous one."""
    c = C()
    u = c.add("CD4040")
    c.power(u, 10, "vdd", "vss")
    c.clock(f"{u}.clk", 1e-6, 10)                                        # falling edges at 1, 2, ... 8 us
    c.dc(f"{u}.rst", 0)
    for i in range(1, 6):
        c.load(f"{u}.q{i}")
    c.run(tstop="9.5u", tstep="4n")
    t_edge = 8e-6
    q1 = [t for t in crossings(c.series(f"{u}.q1"), 5, rising=False) if t > t_edge - 1e-7][0] - t_edge
    q2 = [t for t in crossings(c.series(f"{u}.q2"), 5, rising=False) if t > t_edge - 1e-7][0] - t_edge
    q3 = [t for t in crossings(c.series(f"{u}.q3"), 5, rising=False) if t > t_edge - 1e-7][0] - t_edge
    q4 = [t for t in crossings(c.series(f"{u}.q4"), 5, rising=True) if t > t_edge - 1e-7][0] - t_edge
    stage = 62e-9
    assert [round(x / stage) for x in (q1, q2, q3, q4)] == [1, 2, 3, 4]
    assert q4 == pytest.approx(4 * stage, abs=30e-9)


def test_hc393_two_independent_4_bit_counters():
    c = C()
    u = c.add("SN74HC393")
    c.power(u)
    c.clock(f"{u}.clk1", T)                                              # falling edges at (k + 1) T
    c.clock(f"{u}.clk2", 2 * T)                                          # half the rate
    tstop, unit = 40 * T, 0.05 * T
    high_windows(c, f"{u}.clr1", [(10.2 * T, 10.4 * T)], tstop, unit)     # clears counter 1 only, between edges
    c.dc(f"{u}.clr2", 0)
    q1 = [f"{u}.q1{x}" for x in "abcd"]
    q2 = [f"{u}.q2{x}" for x in "abcd"]
    for q in q1 + q2:
        c.load(q)
    c.run(tstop=f"{tstop:.3e}", tstep="40n")
    for n in range(1, 10):
        assert c.word(q1, (n + 0.5) * T) == n
    assert c.word(q1, 10.1 * T) == 10 and c.word(q1, 10.35 * T) == 0 and c.word(q1, 10.8 * T) == 0
    for n in range(11, 38):
        assert c.word(q1, (n + 0.5) * T) == (n - 10) % 16, n
    for n in range(1, 19):                                               # counter 2: falling edges at (2m + 2) T = 2, 4, ...
        assert c.word(q2, (2 * n + 0.5) * T) == n % 16, n


# ---------------------------------------------------------------- shift registers
def byte_bits(b):
    return [(b >> (7 - i)) & 1 for i in range(8)]                      # most significant bit first


def shift_circuit(id, vdd, sup, bytes_, T=T, ser="ser", clk="srclk", latch=None):
    """Serial stream of `bytes_` (MSB first) on SER, shift clock rising mid-slot."""
    c = C()
    u = c.add(id)
    c.power(u, vdd, *sup)
    bits = [b for byte in bytes_ for b in byte_bits(byte)]
    c.pattern(f"{u}.{ser}", bits, T, vdd)
    c.clock(f"{u}.{clk}", T, vdd)
    return c, u


def test_hc595_shifts_a_byte_and_latches_it_on_rclk():
    B1, B2 = 0xB2, 0x6D
    c, u = shift_circuit("SN74HC595", 5, ("vcc", "gnd"), [B1, B2])
    c.pulse(f"{u}.rclk", 8.25 * T, 0.1 * T, 8 * T)                       # RCLK rises at 8.25 T and 16.25 T (after the 8th and 16th shift edges)
    c.dc(f"{u}.srclrb", 5)
    c.dc(f"{u}.oeb", 0)
    q = [f"{u}.q{x}" for x in "abcdefgh"]
    for p in q + [f"{u}.qs"]:
        c.load(p)
    c.run(tstop=f"{18 * T:.3e}", tstep="40n")
    assert c.word(q, 8.0 * T) == 0 and c.word(q, 4.0 * T) == 0              # nothing reaches the outputs before RCLK
    assert c.word(q, 8.4 * T) == B1 and c.word(q, 12.0 * T) == B1 and c.word(q, 16.0 * T) == B1   # latched; more shifting does not touch it
    assert c.word(q, 16.4 * T) == B2 and c.word(q, 17.9 * T) == B2
    # QH' is the last stage of the shift register, one bit per clock: it shows the byte shifted out of an 8-bit register
    qs = [int(c.hi(f"{u}.qs", (k + 0.9) * T)) for k in range(17)]
    assert qs[7:15] == byte_bits(B1)                                    # after edge 7 the first bit has reached QH; bit i is there after edge 7 + i


def test_hc595_clear_empties_the_shift_register_only_and_oe_tristates_the_outputs():
    B = 0xA5
    c, u = shift_circuit("SN74HC595", 5, ("vcc", "gnd"), [B, 0, 0])
    tstop, unit = 26 * T, 0.05 * T
    c.pulse(f"{u}.rclk", 8.25 * T, 0.1 * T, 100 * T)                      # latch B
    low_windows(c, f"{u}.srclrb", [(10.6 * T, 10.8 * T)], tstop, unit)    # clear the shift register (B is already latched)
    c.pulse(f"{u}.oeb", 20 * T, 3 * T, 100 * T)                           # outputs disabled 20-23 T
    ref = c.add("V", dc=2.5)
    c.w(f"{ref}.n", "0")
    q = [f"{u}.q{x}" for x in "abcdefgh"]
    for p in q:
        c.r("10k", p, f"{ref}.p")
    c.run(tstop=f"{tstop:.3e}", tstep="40n")
    assert c.word(q, 9.0 * T) == B                                       # latched byte
    assert c.word(q, 10.9 * T) == B                                      # SRCLR does not clear the storage register
    assert all(abs(c.v(p, 21 * T) - 2.5) < 0.1 for p in q)                # Hi-Z while OE is high
    assert c.word(q, 24 * T) == B


def test_hc595_srclr_clears_the_shift_register_before_it_is_latched():
    B = 0xFF
    c, u = shift_circuit("SN74HC595", 5, ("vcc", "gnd"), [B])
    tstop, unit = 12 * T, 0.05 * T
    low_windows(c, f"{u}.srclrb", [(7.6 * T, 7.8 * T)], tstop, unit)      # after all 8 bits are in, before RCLK
    c.pulse(f"{u}.rclk", 9.25 * T, 0.1 * T, 100 * T)
    c.dc(f"{u}.oeb", 0)
    q = [f"{u}.q{x}" for x in "abcdefgh"]
    for p in q:
        c.load(p)
    c.run(tstop=f"{tstop:.3e}", tstep="40n")
    assert c.word(q, 9.6 * T) == 0


def test_two_hc595_cascade_into_a_16_bit_register():
    """Multi-instance: QH' of the first feeds SER of the second; after 16 clocks the first byte sits in the second register."""
    B1, B2 = 0xC3, 0x5A
    c = C()
    u1, u2 = c.add("SN74HC595"), c.add("SN74HC595")
    for u in (u1, u2):
        c.power(u)
        c.dc(f"{u}.srclrb", 5)
        c.dc(f"{u}.oeb", 0)
    c.pattern(f"{u1}.ser", byte_bits(B1) + byte_bits(B2), T, 5)
    c.clock(f"{u1}.srclk", T)
    c.w(f"{u1}.srclk", f"{u2}.srclk")
    c.w(f"{u1}.qs", f"{u2}.ser")
    c.pulse(f"{u1}.rclk", 16.25 * T, 0.1 * T, 100 * T)
    c.w(f"{u1}.rclk", f"{u2}.rclk")
    q1 = [f"{u1}.q{x}" for x in "abcdefgh"]
    q2 = [f"{u2}.q{x}" for x in "abcdefgh"]
    for p in q1 + q2:
        c.load(p)
    c.run(tstop=f"{18 * T:.3e}", tstep="40n")
    assert c.word(q1, 17.5 * T) == B2 and c.word(q2, 17.5 * T) == B1
    assert c.word(q1 + q2, 17.5 * T) == B2 | (B1 << 8)
    assert c.word(q1 + q2, 10 * T) == 0


def test_cd4094_shift_and_store_with_strobe_and_output_enable():
    B = 0x96
    c, u = shift_circuit("CD4094", 10, ("vdd", "vss"), [B, 0xFF], ser="data", clk="clock")
    c.pulse(f"{u}.strobe", 7.7 * T, 0.25 * T, 100 * T, v=10)                    # strobe high 7.7-7.95 T: after the 8th shift edge, before the 9th
    c.dc(f"{u}.oe", 10)
    q = [f"{u}.q{i}" for i in range(1, 9)]
    for p in q + [f"{u}.qs", f"{u}.qsb"]:
        c.load(p)
    c.run(tstop=f"{14 * T:.3e}", tstep="40n")
    assert c.word(q, 7.6 * T, 10) == 0                                    # before the strobe
    assert c.word(q, 7.99 * T, 10) == B and c.word(q, 12 * T, 10) == B    # held after the strobe falls, more clocks do not change it
    # Q1 is the first stage, so it holds the last bit shifted in: after 8 bits the first-sent MSB is in Q8
    assert [int(c.hi(q[7 - i], 7.99 * T, 10)) for i in range(8)] == byte_bits(B)
    # serial outputs: QS follows the last stage on the rising edge (after edge 7), Q'S the same bit half a clock later
    assert c.hi(f"{u}.qs", 7.7 * T, 10) == bool(byte_bits(B)[0]) and c.hi(f"{u}.qs", 7.99 * T, 10) == bool(byte_bits(B)[0])
    assert c.hi(f"{u}.qsb", 7.7 * T, 10) is False and c.hi(f"{u}.qsb", 8.2 * T, 10) == bool(byte_bits(B)[0])   # first bit is 1, Q'S still old (0) until the falling edge


def test_cd4094_strobe_high_is_transparent_and_output_enable_low_is_high_impedance():
    c, u = shift_circuit("CD4094", 10, ("vdd", "vss"), [0xFF], ser="data", clk="clock")
    c.dc(f"{u}.strobe", 10)                                              # strobe held high: the outputs follow the shift register
    c.pulse(f"{u}.oe", 0, 6 * T, 100 * T, v=10)                          # enabled for the first 6 T, then Hi-Z
    ref = c.add("V", dc=5)
    c.w(f"{ref}.n", "0")
    q = [f"{u}.q{i}" for i in range(1, 9)]
    for p in q:
        c.r("10k", p, f"{ref}.p")
    c.run(tstop=f"{9 * T:.3e}", tstep="40n")
    assert c.word(q, 1.9 * T, 10) == 0b11 and c.word(q, 3.9 * T, 10) == 0b1111        # follows the shift register edge by edge (Q1 is the newest bit)
    assert all(abs(c.v(p, 7.9 * T) - 5.0) < 0.1 for p in q)               # OE low: floats, the 10k pull to 5 V wins


# ---------------------------------------------------------------- digital multiplexers
@pytest.mark.parametrize("data", [[1, 0, 0, 1, 1, 0, 1, 0], [0, 1, 1, 0, 0, 1, 0, 1]])
def test_hc151_selects_each_of_the_eight_data_inputs_and_the_enable_forces_low(data):
    c = C()
    u = c.add("SN74HC151")
    c.power(u)
    for i, d in enumerate(data):
        c.dc(f"{u}.d{i}", 5 * d)
    for k, p in enumerate("abc"):
        c.counter_bit(f"{u}.{p}", k, T)                                  # slot j selects channel j (a is the least significant select)
    gb = c.add("V", mode="PULSE", v1=0, v2=5, td=8 * T, tr="10n", tf="10n", pw=8 * T, per=100 * T)   # enable low for slots 0-7, high for 8-15
    c.w(f"{gb}.p", f"{u}.gb"); c.w(f"{gb}.n", "0")
    c.load(f"{u}.y"); c.load(f"{u}.w")
    c.run(tstop=f"{16 * T:.3e}", tstep="20n")
    for j in range(8):                                                   # enabled: Y = D(j), W = complement
        assert c.hi(f"{u}.y", (j + 0.9) * T) == bool(data[j]) and c.hi(f"{u}.w", (j + 0.9) * T) == (not data[j]), j
    for j in range(8, 16):                                               # G high: Y low, W high whatever the selection
        assert not c.hi(f"{u}.y", (j + 0.9) * T) and c.hi(f"{u}.w", (j + 0.9) * T), j


def test_hc157_selects_a_or_b_for_all_four_channels_and_enable_forces_low():
    c = C()
    u = c.add("SN74HC157")
    c.power(u)
    a_in, b_in = {1: 1, 2: 0, 3: 1, 4: 0}, {1: 1, 2: 1, 3: 0, 4: 0}
    for g in range(1, 5):
        c.dc(f"{u}.a{g}", 5 * a_in[g])
        c.dc(f"{u}.b{g}", 5 * b_in[g])
        c.load(f"{u}.y{g}")
    c.counter_bit(f"{u}.s", 0, T)                                        # slot j: select = j & 1, enable (active low) = (j >> 1) & 1
    c.counter_bit(f"{u}.gb", 1, T)
    c.run(tstop=f"{4 * T:.3e}", tstep="20n")
    for j in range(4):
        sel, g_hi = j & 1, (j >> 1) & 1
        for g in range(1, 5):
            want = 0 if g_hi else (b_in[g] if sel else a_in[g])
            assert c.hi(f"{u}.y{g}", (j + 0.9) * T) == bool(want), (j, g)


def test_unwired_enables_default_to_enabled():
    c = C()
    u = c.add("SN74HC157")
    c.power(u)
    c.dc(f"{u}.s", 0)
    c.dc(f"{u}.a1", 5)
    c.dc(f"{u}.b1", 0)
    c.load(f"{u}.y1")
    c.run(tstop="1u", tstep="20n")
    assert c.hi(f"{u}.y1", 0.9e-6)


# ---------------------------------------------------------------- ADC and DAC
REF = 5.12                      # 20 mV per code


def test_adc_code_equals_floor_of_256_vin_over_ref_within_one_lsb():
    """A slow ramp sampled every 10 us (ramp 10 mV/us, so vin = 0.05 + 0.1 k V at the k-th edge) over and beyond full scale."""
    c = C()
    u = c.add("ADC_8BIT")
    c.power(u)
    c.dc(f"{u}.ref", REF)
    c.pulse(f"{u}.vin", 0, 1, 10, v=5.3, tr="530u")
    c.clock(f"{u}.clk", 10e-6)                                           # sampling edges at 5, 15, 25 ... us
    out = [f"{u}.d{k}" for k in range(8)]
    for p in out:
        c.load(p)
    c.run(tstop="540u", tstep="0.5u")
    worst = 0
    for k in range(52):
        t_edge = 5e-6 + 10e-6 * k
        vin = 5.3 * t_edge / 530e-6
        ideal = min(int(256 * vin / REF), 255)
        code = c.word(out, t_edge + 6e-6)
        assert abs(code - ideal) <= 1, (k, vin, code, ideal)
        worst = max(worst, abs(code - ideal))
        if abs(256 * vin / REF - round(256 * vin / REF)) > 0.3:          # away from a code boundary the quantiser is exact
            assert code == ideal, (k, vin, code, ideal)
    assert c.word(out, 5e-6 + 10e-6 * 51 + 6e-6) == 255                  # over-range input clamps at 255
    assert c.word(out, 3e-6) == 0                                        # power-up code


def test_adc_ratiometric_reference_and_registered_output():
    """Halving REF doubles the code; the output only changes at the clock edge (a held input changing between edges is not seen)."""
    for ref, vin, want in ((2.56, 1.0, 100), (5.12, 1.0, 50), (1.28, 1.0, 200)):
        c = C()
        u = c.add("ADC_8BIT")
        c.power(u)
        c.dc(f"{u}.ref", ref)
        c.dc(f"{u}.vin", vin)
        c.clock(f"{u}.clk", 2e-6)
        out = [f"{u}.d{k}" for k in range(8)]
        for p in out:
            c.load(p)
        c.run(tstop="4u", tstep="20n")
        assert abs(c.word(out, 3.5e-6) - want) <= 1                      # 256 x vin / ref, to within one code
    c = C()
    u = c.add("ADC_8BIT")
    c.power(u)
    c.dc(f"{u}.ref", REF)
    c.pulse(f"{u}.vin", 1.2e-6, 10e-6, 100e-6, v=2.0)                    # steps 0 -> 2 V at 1.2 us, between the clock edges at 1 and 3 us
    c.clock(f"{u}.clk", 2e-6)
    out = [f"{u}.d{k}" for k in range(8)]
    for p in out:
        c.load(p)
    c.run(tstop="5u", tstep="20n")
    assert c.word(out, 2.5e-6) == 0                                      # the step is not seen until the next edge
    assert c.word(out, 4.0e-6) == int(256 * 2.0 / REF)


def test_dac_output_is_ref_times_code_over_256_for_all_256_codes():
    c = C()
    u = c.add("DAC_8BIT")
    c.dc(f"{u}.ref", REF)
    c.w(f"{u}.gnd", "0")
    for k in range(8):
        c.counter_bit(f"{u}.d{k}", k, 2e-6)                              # slot j carries code j
    c.load(f"{u}.out", "1e9")
    c.run(tstop=f"{256 * 2e-6:.3e}", tstep="0.25u")
    for code in range(256):
        v = c.v(f"{u}.out", (code + 0.9) * 2e-6)
        assert abs(v - REF * code / 256) < 0.0005, (code, v)            # far inside one LSB (20 mV)


def test_dac_output_resistance_is_r_and_the_threshold_parameter_works():
    c = C()
    u = c.add("DAC_8BIT", r="10k")
    c.dc(f"{u}.ref", 5.0)
    c.w(f"{u}.gnd", "0")
    for k in range(8):
        c.dc(f"{u}.d{k}", 5 if k == 7 else 0)                            # code 128
    c.load(f"{u}.out", "10k")                                            # a load equal to R halves the open-circuit 2.5 V
    c.run(tstop="2u", tstep="20n")
    assert c.v(f"{u}.out") == pytest.approx(1.25, rel=1e-3)
    c = C()
    u = c.add("DAC_8BIT", vth="1")                                       # a 1.5 V logic level is above a 1 V threshold, below the default
    c.dc(f"{u}.ref", 5.0)
    c.w(f"{u}.gnd", "0")
    for k in range(8):
        c.dc(f"{u}.d{k}", 1.5 if k == 7 else 0)
    c.load(f"{u}.out", "1e9")
    c.run(tstop="2u", tstep="20n")
    assert c.v(f"{u}.out") == pytest.approx(2.5, rel=1e-3)
    c = C()
    u = c.add("DAC_8BIT")
    c.dc(f"{u}.ref", 5.0)
    c.w(f"{u}.gnd", "0")
    for k in range(8):
        c.dc(f"{u}.d{k}", 1.5 if k == 7 else 0)
    c.load(f"{u}.out", "1e9")
    c.run(tstop="2u", tstep="20n")
    assert c.v(f"{u}.out") == pytest.approx(0.0, abs=1e-3)


def test_adc_into_dac_round_trip_is_within_one_lsb():
    """Mixed circuit: a slow ramp -> ADC (sampled every 4 us) -> DAC. The DAC lags the sample by the conversion delay and sits
    below the sampled input by the quantisation error, less than one LSB."""
    c = C()
    adc, dac = c.add("ADC_8BIT"), c.add("DAC_8BIT")
    c.power(adc)
    c.dc(f"{adc}.ref", REF)
    c.dc(f"{dac}.ref", REF)
    c.w(f"{dac}.gnd", "0")
    c.pulse(f"{adc}.vin", 0, 1, 10, v=5.0, tr="400u")
    c.clock(f"{adc}.clk", 4e-6)                                           # edges at 2, 6, 10 ... us
    for k in range(8):
        c.w(f"{adc}.d{k}", f"{dac}.d{k}")
    c.load(f"{dac}.out", "1e9")
    c.run(tstop="404u", tstep="0.5u")
    lsb = REF / 256
    for k in range(100):
        t_edge = 2e-6 + 4e-6 * k
        vin = 5.0 * t_edge / 400e-6
        vout = c.v(f"{dac}.out", t_edge + 3.5e-6)
        assert -0.001 <= vin - vout < lsb + 0.001, (k, vin, vout)
        assert vout == pytest.approx(lsb * int(vin / lsb), abs=0.0006), k


# ---------------------------------------------------------------- mixed circuit: 555 clock -> 8-bit counter -> DAC
def test_555_clock_drives_two_counters_that_drive_a_dac_staircase():
    """NE555 astable (about 10 kHz) clocks two chained 74HC161 (an 8-bit synchronous counter) whose outputs are the DAC code:
    every 555 output edge adds one LSB (20 mV) to the DAC output, across the nibble carry at 16 clocks."""
    c = C()
    tm, lo, hi, dac = c.add("NE555"), c.add("SN74HC161"), c.add("SN74HC161"), c.add("DAC_8BIT")
    sp = c.vdc(5, f"{tm}.vcc")
    c.w(f"{tm}.gnd", "0")
    c.w(f"{tm}.reset", f"{sp}.p")
    c.r("1k", f"{sp}.p", f"{tm}.dis")                                      # RA = 1 k, RB = 6.8 k, C = 10 n: f = 1.44 / (14.6 k x 10 n) = 9.9 kHz
    c.r("6.8k", f"{tm}.dis", f"{tm}.thr")
    c.w(f"{tm}.thr", f"{tm}.trig")
    cap = c.add("C", C="10n")
    c.w(f"{cap}.a", f"{tm}.thr")
    c.w(f"{cap}.b", "0")
    c.r("1e9", f"{tm}.ctrl", "0")
    for u in (lo, hi):
        c.w(f"{u}.vcc", f"{sp}.p")
        c.w(f"{u}.gnd", "0")
        for p in ("clrb", "loadb", "enp"):
            c.w(f"{u}.{p}", f"{sp}.p")
        for p in "abcd":
            c.w(f"{u}.{p}", "0")
    c.w(f"{lo}.ent", f"{sp}.p")
    c.w(f"{lo}.rco", f"{hi}.ent")
    c.w(f"{tm}.out", f"{lo}.clk")
    c.w(f"{tm}.out", f"{hi}.clk")
    c.dc(f"{dac}.ref", REF)
    c.w(f"{dac}.gnd", "0")
    for k, x in enumerate("abcd"):
        c.w(f"{lo}.q{x}", f"{dac}.d{k}")
        c.w(f"{hi}.q{x}", f"{dac}.d{k + 4}")
    c.load(f"{dac}.out", "1e9")
    c.run(tstop="3.2m", tstep="2u")
    edges = crossings(c.series(f"{tm}.out"), 2.5)
    assert len(edges) >= 30
    f = 1 / (edges[-1] - edges[0]) * (len(edges) - 1)
    assert f == pytest.approx(1 / (0.693 * 14.6e3 * 10e-9), rel=0.05)
    lsb = REF / 256
    steps = [c.v(f"{dac}.out", t + 0.4 * (edges[1] - edges[0])) / lsb for t in edges[:30]]    # code just after each rising 555 edge
    codes = [round(x) for x in steps]
    assert all(abs(x - r) < 0.05 for x, r in zip(steps, codes))             # on the 20 mV grid
    assert codes == list(range(1, 31)), codes                                # +1 LSB per clock edge, across the 15 -> 16 nibble carry too


def test_a_run_reports_only_limited_analysis_findings_for_the_digital_parts():
    """The generated deck's static findings are warnings only: `limited` class (XSPICE A devices, B sources) plus the unwired pins of the test."""
    c = C()
    u = c.add("SN74HC74")
    c.power(u)
    c.clock(f"{u}.ck1", T)
    c.w(f"{u}.qb1", f"{u}.d1")
    c.dc(f"{u}.pre1", 5)
    c.dc(f"{u}.clr1", 5)
    c.load(f"{u}.q1")
    c.run(tstop=f"{4 * T:.3e}", tstep="20n")
    assert {f["severity"] for f in c.res.findings} == {"warn"}              # no error
    assert {(f["code"], f["class"]) for f in c.res.findings} - {("DANGLING", "connectivity")} == {("LIMITED-ANALYSIS", "limited")}    # DANGLING: flip-flop 2 is left unwired
