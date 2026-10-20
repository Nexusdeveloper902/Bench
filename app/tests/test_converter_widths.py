"""G25: the 4, 10 and 12-bit ADC and DAC packages, against the ideal transfer curves.

One package per width (a part's pin count cannot depend on a parameter), generated from the 8-bit body pattern in
`_models/digital/conv.lib`. The 8-bit pair is covered in test_library_batch_d.py; these are the same checks at the other widths."""
import pytest

from app.catalog import CATALOG
from test_library_batch_d import C

WIDTHS = (4, 10, 12)
REF = 5.12
WIDTH_ADCS = {f"ADC_{n}BIT" for n in WIDTHS}      # `limited` (A devices); the DACs are `full`
WIDTH_PARTS = WIDTH_ADCS | {f"DAC_{n}BIT" for n in WIDTHS}


def adc_code(n, vin, ref=REF, clock="edge"):
    """Code read after one conversion: vin held, one rising clock edge at 1 us ("edge") or a clock tied high ("high")."""
    c = C()
    u = c.add(f"ADC_{n}BIT")
    c.power(u)
    c.dc(f"{u}.ref", ref)
    c.dc(f"{u}.vin", vin)
    if clock == "edge":
        c.pulse(f"{u}.clk", 1e-6, 1e-6, 100e-6)
    else:
        c.dc(f"{u}.clk", 5)
    out = [f"{u}.d{k}" for k in range(n)]
    for p in out:
        c.load(p)
    c.run(tstop="3u", tstep="20n")
    return c.word(out, 2.5e-6)


def dac_levels(n, codes, ref=REF):
    """DAC output for each code, one 2 us slot per code (the bit patterns are built from `pattern`)."""
    c = C()
    u = c.add(f"DAC_{n}BIT")
    c.dc(f"{u}.ref", ref)
    c.w(f"{u}.gnd", "0")
    for k in range(n):
        c.pattern(f"{u}.d{k}", [(code >> k) & 1 for code in codes], 2e-6)
    c.load(f"{u}.out", "1e9")
    c.run(tstop=f"{len(codes) * 2e-6:.3e}", tstep="0.25u")
    return [c.v(f"{u}.out", (i + 0.9) * 2e-6) for i in range(len(codes))]


def sample_codes(n):
    top = 2 ** n - 1
    return sorted({0, 1, 2, 3, top // 2, top // 2 + 1, top // 3, top - 2, top - 1, top, 5 % (top + 1), 7 % (top + 1)})


@pytest.mark.parametrize("n", WIDTHS)
def test_packages_have_the_right_pins_and_kind(n):
    a, d = CATALOG.get(f"ADC_{n}BIT"), CATALOG.get(f"DAC_{n}BIT")
    assert CATALOG.problems == [] and a.simulated and d.simulated
    assert [p.id for p in a.pins if p.id.startswith("d")] == [f"d{k}" for k in range(n)] and len(a.pins) == n + 5
    assert [p.id for p in d.pins if p.id.startswith("d")] == [f"d{k}" for k in range(n)] and len(d.pins) == n + 3
    assert len({p.at for p in a.pins}) == len(a.pins) and len({p.at for p in d.pins}) == len(d.pins)
    assert a.sim["electrical"]["verification"] == d.sim["electrical"]["verification"] == "analytic"
    assert d.sim["electrical"]["_status"] == "full" and a.sim["electrical"]["_status"] == "limited"
    assert f"{n} bits" in " ".join(a.limitations) and f"{n} bits" in " ".join(d.limitations)


@pytest.mark.parametrize("n", WIDTHS)
def test_dac_output_is_ref_times_code_over_2_to_n(n):
    codes = sample_codes(n)
    lsb = REF / 2 ** n
    for code, v in zip(codes, dac_levels(n, codes)):
        assert abs(v - REF * code / 2 ** n) < 0.25 * lsb, (n, code, v)


@pytest.mark.parametrize("n", WIDTHS)
def test_adc_code_is_floor_of_2_to_n_vin_over_ref(n):
    lsb = REF / 2 ** n
    for code in sample_codes(n):
        assert adc_code(n, (code + 0.5) * lsb) == code, (n, code)             # mid-code input: exact
    assert adc_code(n, 0.0) == 0
    assert adc_code(n, REF * 1.1) == 2 ** n - 1                               # over-range clamps at full scale
    assert abs(adc_code(n, 3 * lsb) - 3) <= 1                                 # on a code boundary: within one code
    assert adc_code(n, 1.0, ref=2 * REF) == int(2 ** n * 1.0 / (2 * REF))     # ratiometric


@pytest.mark.parametrize("n", WIDTHS)
def test_adc_clock_tied_high_does_not_convert_at_power_up(n):
    assert adc_code(n, 2.5, clock="high") == 0


@pytest.mark.parametrize("n", WIDTHS)
def test_adc_into_dac_at_the_same_width_is_within_one_lsb(n):
    lsb = REF / 2 ** n
    for vin in (0.6, 2.3, 4.9):
        c = C()
        adc, dac = c.add(f"ADC_{n}BIT"), c.add(f"DAC_{n}BIT")
        c.power(adc)
        c.dc(f"{adc}.ref", REF)
        c.dc(f"{dac}.ref", REF)
        c.w(f"{dac}.gnd", "0")
        c.dc(f"{adc}.vin", vin)
        c.pulse(f"{adc}.clk", 1e-6, 1e-6, 100e-6)
        for k in range(n):
            c.w(f"{adc}.d{k}", f"{dac}.d{k}")
        c.load(f"{dac}.out", "1e9")
        c.run(tstop="3u", tstep="20n")
        assert -0.0005 <= vin - c.v(f"{dac}.out", 2.5e-6) < lsb + 0.0005, (n, vin)
