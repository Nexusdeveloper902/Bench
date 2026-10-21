"""Library batch E (task 9): sensors and other electrical devices.

Sensors expose their physical stimulus as an instance parameter. Each stimulus is a source on its own node (`stim`, a
VSTIM source to the common reference) and the electrical response reads that node, so a later task can drive the node
with a time-varying waveform. The electrical response is a closed form (LDR power law, PT100 and PT1000 with the IEC
60751 A and B coefficients, PTC switching curve, photodiode and phototransistor currents, Hall and LM35 transfer, strain
bridge, pressure, microphone) or a lumped equivalent (crystals, resonators, speaker, motor, optocoupler, shunt, current
transformer). Every package is checked through the REAL path (Document -> netlist -> app.sim -> benchcore -> ngspice)
against its closed form. Packages whose response is a B source, or a controlled source whose gain is an expression, were
`limited` until the interval check of G29 proved them over their stimulus ranges (test_value_checks_w3.py); all are `full`.

The models are project-authored and generic unless the provenance says otherwise. A pass means the element's law holds,
not that a device's datasheet curve is reproduced."""
import math

import pytest

from app.catalog import BUILTIN_ROOT, CATALOG
from app.model import Document
from test_library_batch_c import Circuit

NEW = {   # id -> (model_kind, validator status)
    "LDR": ("equivalent", "full"), "PTC_THERMISTOR": ("equivalent", "full"),
    "RTD_PT100": ("equivalent", "full"), "RTD_PT1000": ("equivalent", "full"),
    "PHOTODIODE": ("equivalent", "full"), "PHOTOTRANSISTOR": ("equivalent", "full"),
    "HALL_LINEAR": ("behavioral", "full"), "FSR": ("equivalent", "full"),
    "STRAIN_GAUGE": ("equivalent", "full"), "STRAIN_BRIDGE": ("equivalent", "full"),
    "POSITION_SENSOR": ("behavioral", "full"), "LM35": ("behavioral", "full"),
    "MIC_ELECTRET": ("behavioral", "full"), "PIEZO_ULTRASONIC": ("equivalent", "full"),
    "CURRENT_SHUNT": ("equivalent", "full"), "CURRENT_TRANSFORMER": ("equivalent", "full"),
    "PRESSURE_BRIDGE": ("behavioral", "full"), "HUMIDITY_CAP": ("equivalent", "full"),
    "OPTOCOUPLER": ("equivalent", "full"), "DC_MOTOR": ("equivalent", "full"),
    "SOLENOID": ("equivalent", "full"), "SPEAKER": ("equivalent", "full"),
    "CRYSTAL_32K": ("equivalent", "full"), "CRYSTAL_16M": ("equivalent", "full"),
    "CERAMIC_RES_16M": ("equivalent", "full"),
}
# sensors whose stimulus is a VSTIM source on node `stim` (HUMIDITY_CAP and SOLENOID use the charge and flux forms, G27 and G28;
# the crystals, resonators and speaker have no stimulus)
STIMULUS = {"LDR", "PTC_THERMISTOR", "RTD_PT100", "RTD_PT1000", "PHOTODIODE", "PHOTOTRANSISTOR", "HALL_LINEAR", "FSR",
            "STRAIN_GAUGE", "STRAIN_BRIDGE", "POSITION_SENSOR", "LM35", "MIC_ELECTRET", "PRESSURE_BRIDGE", "DC_MOTOR", "HUMIDITY_CAP", "SOLENOID"}


# ---------------------------------------------------------------- helpers
def ohms(kind, v=1.0, tstop="1u", tstep="1u", **params):
    """Resistance of a two-terminal part: a DC source of v volts across it, R = V / |I|."""
    c = Circuit(); x = c.add(kind, **params); s = c.vdc(v, f"{x}.a"); c.w(f"{x}.b", "0")
    c.run(tstop, tstep)
    return v / abs(c.i(s))


def source_current(c, src):
    return [(t, abs(v)) for t, v in c.res.get(f"I({src})")]


def sample(series, t):
    return min(series, key=lambda p: abs(p[0] - t))[1]


def cross_freq(series, t1, t2):
    """Ring frequency from rising zero crossings after removing a moving mean of about one tenth of the window."""
    pts = [(t, v) for t, v in series if t1 <= t <= t2]
    n = max(1, len(pts) // 10)
    mean = [sum(w for _, w in pts[max(0, i - n):i + n]) / len(pts[max(0, i - n):i + n]) for i in range(len(pts))]
    d = [(t, v - m) for (t, v), m in zip(pts, mean)]
    cr = [ta + (tb - ta) * (-a) / (b - a) for (ta, a), (tb, b) in zip(d, d[1:]) if a < 0 <= b]
    return (len(cr) - 1) / (cr[-1] - cr[0])


def ring_of_source_current(kind, tr, tstep, tstop, t1, rsrc="1"):
    """A 1 V step through rsrc into a resonator; returns its ring frequency seen in the source current."""
    c = Circuit(); x = c.add(kind); r = c.add("R", R=rsrc)
    v = c.add("V", mode="PULSE", v1="0", v2="1", td="0", tr=tr, tf=tr, pw="1", per="2")
    c.w(f"{v}.n", "0"); c.w(f"{v}.p", f"{r}.a"); c.w(f"{r}.b", f"{x}.a"); c.w(f"{x}.b", "0")
    c.run(tstop, tstep)
    return cross_freq(c.res.get(f"I({v})"), t1, tstop)


def netlist_text(kind, **params):
    d = Document(); d.add_part(kind, **{k: str(v) for k, v in params.items()})
    nl = d.netlist()
    return "\n".join(nl["lines"] + nl["models"])


# ---------------------------------------------------------------- every package loads, validates and records its kind
@pytest.mark.parametrize("id", sorted(NEW))
def test_package_loads_validates_and_records_its_kind(id):
    c = CATALOG.get(id)
    s = c.sim["electrical"]
    kind, status = NEW[id]
    assert CATALOG.problems == [] and c.simulated and c.readiness == "simulated" and c.path.is_relative_to(BUILTIN_ROOT)
    assert c.category in ("Sensors", "Electrical") and c.subcategory
    assert s["device"] == "X" and (s["model_kind"], s["verification"]) == (kind, "analytic"), id
    assert s["_status"] == status, id
    assert not s.get("_limits"), id         # G29: every B source and expression gain is value-checked over its stimulus range
    assert s["provenance"]["license"] == "MIT" and s["provenance"]["source"], id
    assert any(w in x.lower() for x in c.limitations for w in ("not", "no ")), id          # every package says what it does not model
    ids = [p.id for p in c.pins]
    assert len(ids) == len(set(ids)), id


def test_catalog_grew_by_exactly_these_parts():
    assert set(NEW) <= set(CATALOG.defs) and len(NEW) == 25 and len(CATALOG.defs) >= 148 + len(NEW)     # task 9.6 adds the PWL sources (test_gaps_96.py)


@pytest.mark.parametrize("kind", sorted(STIMULUS))
def test_stimulus_is_a_source_on_its_own_node(kind):
    text = netlist_text(kind)
    assert "VSTIM stim" in text and "stim" in text, kind             # the response reads V(stim); the source drives it


def test_shared_model_files_are_emitted_once_per_deck():
    d = Document()
    for kind in ("RTD_PT100", "RTD_PT1000", "CRYSTAL_32K", "PIEZO_ULTRASONIC", "CERAMIC_RES_16M"):
        d.add_part(kind)
    lines = d.netlist()["models"]
    for f in ("sensors/rtd.lib", "resonators/bvd.lib"):
        assert len([m for m in lines if m.startswith("* bench-def") and m.split()[2].endswith(f)]) == 1, f


# ---------------------------------------------------------------- LDR, RTD, PTC
@pytest.mark.parametrize("lux", ["10", "100", "1000"])
def test_ldr_resistance_follows_r10_times_power_law(lux):
    assert ohms("LDR", lux=lux, r10="10k", gamma="0.7") == pytest.approx(10e3 * (float(lux) / 10) ** -0.7, rel=1e-6)



@pytest.mark.parametrize("kind,temp,want", [
    ("RTD_PT100", "0", 100.00), ("RTD_PT100", "100", 138.51), ("RTD_PT100", "850", 390.48),
    ("RTD_PT1000", "100", 1385.1)])
def test_rtd_resistance_matches_iec_60751_coefficients(kind, temp, want):
    r0 = 1000.0 if kind == "RTD_PT1000" else 100.0
    t = float(temp)
    cvd = r0 * (1 + 3.9083e-3 * t - 5.775e-7 * t * t)
    got = ohms(kind, temp=temp)
    assert got == pytest.approx(cvd, rel=1e-6)
    assert got == pytest.approx(want, rel=1e-4)                  # the IEC 60751 table values (100.00, 138.51, ...)


def test_two_rtd_instances_in_series_share_one_definition():
    c = Circuit(); a = c.add("RTD_PT100", temp="0"); b = c.add("RTD_PT100", temp="100")
    c.w(f"{a}.b", f"{b}.a"); s = c.vdc(1, f"{a}.a"); c.w(f"{b}.b", "0")
    c.run("1u", "1u")
    assert 1 / abs(c.i(s)) == pytest.approx(100.0 + 138.5055, rel=1e-5)


PTC_T = [25, 105, 120, 135, 150]


@pytest.mark.parametrize("t", PTC_T)
def test_ptc_resistance_follows_the_switching_curve(t):
    want = 100 * 1000 ** (1 / (1 + math.exp(-(t - 120) / 3)))
    assert ohms("PTC_THERMISTOR", temp=str(t), r25="100", rratio="1000", tc="120", w="3") == pytest.approx(want, rel=1e-6)


def test_ptc_step_is_about_three_decades_across_the_switching_temperature():
    cold = ohms("PTC_THERMISTOR", temp="100")
    hot = ohms("PTC_THERMISTOR", temp="140")
    assert cold == pytest.approx(100.0, rel=1e-2) and hot == pytest.approx(1e5, rel=1e-2)
    assert ohms("PTC_THERMISTOR", temp="120") == pytest.approx(100 * math.sqrt(1000), rel=1e-6)   # the midpoint of the log step


# ---------------------------------------------------------------- photodiode, phototransistor, optocoupler
@pytest.mark.parametrize("irr", [1, 2, 4])
def test_photodiode_voltage_across_a_load_is_proportional_to_irradiance(irr):
    c = Circuit(); pd = c.add("PHOTODIODE", irr=str(irr))
    c.vdc(5, f"{pd}.k"); c.r("10k", f"{pd}.a", "0"); c.run("1m", "10u")
    assert c.v(f"{pd}.a") == pytest.approx(50e-6 * irr * 10e3, rel=2e-3)      # SENS · E · RL, reverse biased at 5 V


def test_photodiode_is_dark_at_zero_irradiance():
    c = Circuit(); pd = c.add("PHOTODIODE", irr="0")
    c.vdc(5, f"{pd}.k"); c.r("10k", f"{pd}.a", "0"); c.run("1m", "10u")
    assert abs(c.v(f"{pd}.a")) < 1e-4                                          # only the 1 nA junction leakage


def test_phototransistor_collector_current_is_bf_plus_one_times_the_base_photocurrent():
    c = Circuit(); pt = c.add("PHOTOTRANSISTOR", irr="10"); r = c.add("R", R="470")
    c.vdc(5, f"{r}.a"); c.w(f"{r}.b", f"{pt}.c"); c.w(f"{pt}.e", "0"); c.run("1m", "10u")
    ic = (5 - c.v(f"{pt}.c")) / 470
    assert ic / (5e-6 * 10) == pytest.approx(101, rel=5e-3)                    # active: Vce about 2.5 V


@pytest.mark.parametrize("ctr", ["1", "0.5"])
def test_optocoupler_collector_current_is_ctr_times_led_current(ctr):
    c = Circuit(); o = c.add("OPTOCOUPLER", ctr=ctr)
    r = c.add("R", R="100"); vled = c.vdc(5, f"{r}.a"); c.w(f"{r}.b", f"{o}.a"); c.w(f"{o}.k", "0")
    rl = c.add("R", R="100"); c.vdc(5, f"{rl}.a"); c.w(f"{rl}.b", f"{o}.c"); c.w(f"{o}.e", "0")
    c.run("1m", "10u")
    i_f = source_current(c, vled)[-1][1]
    i_c = (5 - c.v(f"{o}.c")) / 100
    assert c.v(f"{o}.c") > 1.0                                                  # still active, not saturated
    assert i_c / i_f == pytest.approx(float(ctr), rel=1e-3)


def test_optocoupler_saturates_when_the_load_is_too_big_for_the_transfer():
    c = Circuit(); o = c.add("OPTOCOUPLER", ctr="1")
    r = c.add("R", R="330"); c.vdc(5, f"{r}.a"); c.w(f"{r}.b", f"{o}.a"); c.w(f"{o}.k", "0")
    rl = c.add("R", R="1k"); c.vdc(5, f"{rl}.a"); c.w(f"{rl}.b", f"{o}.c"); c.w(f"{o}.e", "0")
    c.run("1m", "10u")
    assert c.v(f"{o}.c") < 0.3                                                  # the transistor saturates, Vce(sat) low


# ---------------------------------------------------------------- Hall, strain, FSR, position, LM35, pressure, humidity
@pytest.mark.parametrize("vcc,b", [(5, 0), (5, 10), (5, -20), (3.3, -20)])
def test_hall_output_is_ratiometric_half_supply_plus_sensitivity_times_field(vcc, b):
    c = Circuit(); h = c.add("HALL_LINEAR", b=str(b), sens="2.5m")
    c.vdc(vcc, f"{h}.vcc"); c.w(f"{h}.gnd", "0"); c.run("1u", "1u")
    assert c.v(f"{h}.out") == pytest.approx(vcc / 2 + 2.5e-3 * b, abs=1e-6)


@pytest.mark.parametrize("eps", [0, 1000, -500])
def test_strain_bridge_output_is_the_quarter_bridge_closed_form(eps):
    c = Circuit(); b = c.add("STRAIN_BRIDGE", eps=str(eps)); c.vdc(1, f"{b}.ep"); c.w(f"{b}.em", "0")
    c.run("1u", "1u")
    x = 2 * eps * 1e-6
    assert c.v(f"{b}.op") - c.v(f"{b}.om") == pytest.approx(x / (2 * (2 + x)), abs=1e-9)


@pytest.mark.parametrize("eps", [0, 1000, -500])
def test_strain_gauge_resistance_is_r0_times_one_plus_gf_epsilon(eps):
    assert ohms("STRAIN_GAUGE", eps=str(eps)) == pytest.approx(350 * (1 + 2 * eps * 1e-6), rel=1e-6)


@pytest.mark.parametrize("force", [1, 4])
def test_fsr_resistance_follows_the_power_law_in_force(force):
    want = 1 / (1 / 10e6 + 1 / (10e3 / force))                                  # RREF·(F/FREF)^-1 in parallel with RLEAK
    assert ohms("FSR", force=str(force)) == pytest.approx(want, rel=1e-6)


@pytest.mark.parametrize("pos", [0, 0.25, 1])
def test_position_sensor_output_is_ratiometric_in_the_stroke(pos):
    c = Circuit(); p = c.add("POSITION_SENSOR", pos=str(pos)); c.vdc(5, f"{p}.vcc"); c.w(f"{p}.gnd", "0")
    c.run("1u", "1u")
    assert c.v(f"{p}.out") == pytest.approx(5 * (0.1 + 0.8 * pos), abs=1e-6)


@pytest.mark.parametrize("temp", [0, 25, 100])
def test_lm35_output_is_10_mv_per_degree(temp):
    c = Circuit(); l = c.add("LM35", temp=str(temp)); c.vdc(5, f"{l}.vs"); c.w(f"{l}.gnd", "0")
    c.run("1u", "1u")
    assert c.v(f"{l}.out") == pytest.approx(0.01 * temp, abs=1e-6)


def test_pressure_bridge_output_is_sensitivity_times_pressure_times_excitation():
    c = Circuit(); pb = c.add("PRESSURE_BRIDGE", pres="3")
    c.vdc(5, f"{pb}.vp"); c.w(f"{pb}.vn", "0"); c.r("1G", f"{pb}.op", f"{pb}.om"); c.w(f"{pb}.om", "0")
    c.run("1u", "1u")
    assert c.v(f"{pb}.op") - c.v(f"{pb}.om") == pytest.approx(2e-3 * 3 * 5, rel=1e-4)


def test_humidity_capacitor_sets_an_rc_time_constant():
    """C = C0 + CS·RH = 180 pF + 0.4 pF/%·50 = 200 pF; with 10 kΩ, tau = 2 µs."""
    c = Circuit(); hc = c.add("HUMIDITY_CAP", rh="50"); r = c.add("R", R="10k")
    c.vdc(1, f"{r}.a"); c.w(f"{r}.b", f"{hc}.a"); c.w(f"{hc}.b", "0")
    c.run("20u", "0.02u")
    assert c.v(f"{hc}.a", 2e-6) == pytest.approx(1 - math.exp(-1), abs=0.01)


# ---------------------------------------------------------------- microphone, shunt, current transformer
@pytest.mark.parametrize("spl", [94, 74])
def test_microphone_tone_amplitude_follows_the_spl(spl):
    c = Circuit(); m = c.add("MIC_ELECTRET", spl=str(spl), freq="1k"); c.vdc(3, f"{m}.vdd")
    c.run("3m", "1u")
    tail = [v for t, v in c.series(f"{m}.out") if t > 1e-3]
    amp = (max(tail) - min(tail)) / 2
    assert amp == pytest.approx(10 ** (-44 / 20) * 20e-6 * 10 ** (spl / 20), rel=5e-3)
    assert c.v(f"{m}.out") == pytest.approx(3 - 0.5e-3 * 2200, abs=0.02)   # bias drop across the drain resistor


def test_current_shunt_sense_voltage_is_i_times_rs():
    c = Circuit(); sh = c.add("CURRENT_SHUNT", rs="10m"); i = c.add("I", dc="1")
    c.w(f"{i}.p", "0"); c.w(f"{i}.n", f"{sh}.ia"); c.w(f"{sh}.ib", "0")
    c.run("1u", "1u")
    assert c.v(f"{sh}.sa") - c.v(f"{sh}.sb") == pytest.approx(0.01, rel=1e-6)


def test_current_transformer_burden_voltage_is_ip_rb_over_n():
    c = Circuit(); ct = c.add("CURRENT_TRANSFORMER", n="100", lm="1", rb="10")
    i = c.add("ISRC_WAVE", mode="SIN", ioff="0", iamp="1", freq="1k")
    r = c.add("R", R="1m")
    c.w(f"{i}.p", "0"); c.w(f"{i}.n", f"{ct}.p1"); c.w(f"{ct}.p2", f"{r}.a"); c.w(f"{r}.b", "0")
    c.w(f"{ct}.o2", "0"); c.r("1G", f"{ct}.o1", "0")
    c.run("3m", "1u")
    tail = [v for t, v in c.series(f"{ct}.o1") if t > 1e-3]
    assert (max(tail) - min(tail)) / 2 == pytest.approx(1 * 10 / 100, rel=1e-2)


# ---------------------------------------------------------------- motor, solenoid, speaker
def test_dc_motor_stall_current_is_v_over_ra_and_running_current_subtracts_back_emf():
    stall = Circuit(); m = stall.add("DC_MOTOR", speed="0"); v = stall.vdc(12, f"{m}.a"); stall.w(f"{m}.b", "0")
    stall.run("20m", "5u")
    assert source_current(stall, v)[-1][1] == pytest.approx(12 / 2, rel=1e-4)
    run = Circuit(); m = run.add("DC_MOTOR", speed="1000"); v = run.vdc(12, f"{m}.a"); run.w(f"{m}.b", "0")
    run.run("20m", "5u")
    e = 0.02 * 1000 * 2 * math.pi / 60                                          # KE·ω, 1000 rpm
    assert source_current(run, v)[-1][1] == pytest.approx((12 - e) / 2, rel=1e-4)


def test_dc_motor_armature_inductance_sets_a_tau_of_l_over_r():
    c = Circuit(); m = c.add("DC_MOTOR", speed="0"); v = c.vdc(12, f"{m}.a"); c.w(f"{m}.b", "0")
    c.run("3m", "5u")
    i = source_current(c, v)
    assert sample(i, 0.5e-3) == pytest.approx(6 * (1 - math.exp(-1)), rel=1e-2)   # tau = 1 mH / 2 Ω = 0.5 ms


@pytest.mark.parametrize("pos,tau", [(0, 2e-3), (1, 6e-3)])
def test_solenoid_current_rises_with_l_over_r_across_the_plunger_range(pos, tau):
    c = Circuit(); s = c.add("SOLENOID", pos=str(pos)); v = c.vdc(10, f"{s}.a"); c.w(f"{s}.b", "0")
    c.run("12m", "5u")
    assert sample(source_current(c, v), tau) == pytest.approx(1 * (1 - math.exp(-1)), rel=1e-2)   # V/R = 1 A


def test_speaker_dc_resistance_is_re():
    """The run starts from zero (UIC), and the motional branch settles with its ~10 ms time constant first."""
    assert ohms("SPEAKER", v=1.0, tstop="300m", tstep="0.5m") == pytest.approx(6.0, rel=1e-3)


def test_speaker_voice_coil_inductance_sets_an_early_rise_of_le_over_re():
    """With a voltage step, the current rises with tau = Le/Re = 0.1 mH / 6 Ω = 16.7 µs before the motional branch
    takes current; the current reaches 1 - 1/e of its final value V/Re at that time."""
    c = Circuit(); s = c.add("SPEAKER")
    v = c.add("V", mode="PULSE", v1="0", v2="1", td="0", tr="1n", tf="1n", pw="1", per="2")
    c.w(f"{v}.n", "0"); c.w(f"{v}.p", f"{s}.a"); c.w(f"{s}.b", "0")
    c.run("200u", "0.1u")
    i = source_current(c, v)
    assert sample(i, 16.7e-6) == pytest.approx((1 - math.exp(-1)) / 6, rel=0.03)


def test_speaker_motional_branch_rings_at_the_damped_thiele_small_frequency():
    """A current step into the driver rings at omega_d = sqrt(w0^2 - a^2) of the parallel RLC that the Thiele-Small
    electrical analogy gives: Rp = Bl²/Rms, Lp = Cms·Bl², Cp = Mms/Bl²."""
    bl, mms, cms, rms = 3.5, 0.01, 0.0005, 1.0
    rp, lp, cp = bl * bl / rms, cms * bl * bl, mms / (bl * bl)
    w0 = 1 / math.sqrt(lp * cp)
    a = 1 / (2 * rp * cp)
    fd = math.sqrt(w0 * w0 - a * a) / (2 * math.pi)
    c = Circuit(); s = c.add("SPEAKER")
    i = c.add("ISRC_WAVE", mode="PULSE", i1="0", i2="1m", td="0", tr="100u", tf="100u", pw="1", per="2")
    c.w(f"{i}.p", "0"); c.w(f"{i}.n", f"{s}.a"); c.w(f"{s}.b", "0")
    c.run("150m", "0.1m")
    series = c.series(f"{s}.a")
    assert cross_freq(series, 0.02, 0.15) == pytest.approx(fd, rel=1e-2)
    assert fd == pytest.approx(70.7, abs=0.2)                                    # the generic driver rings near 71 Hz


# ---------------------------------------------------------------- resonators: series resonance from the transient ring
@pytest.mark.parametrize("kind,tr,step,tstop,t1,fs", [
    ("CRYSTAL_32K", "10u", "0.5u", "3m", "0.3m", 1 / (2 * math.pi * math.sqrt(6.74e3 * 3.5e-15))),
    ("CRYSTAL_16M", "20n", "0.2n", "2u", "0.3u", 1 / (2 * math.pi * math.sqrt(7.07e-3 * 14e-15))),
    ("CERAMIC_RES_16M", "20n", "0.2n", "3u", "0.3u", 1 / (2 * math.pi * math.sqrt(197.9e-6 * 0.5e-12))),
    ("PIEZO_ULTRASONIC", "5u", "0.2u", "3m", "0.5m", 1 / (2 * math.pi * math.sqrt(39.6e-3 * 400e-12))),
])
def test_resonator_rings_at_its_series_resonance(kind, tr, step, tstop, t1, fs):
    """1 V step through 1 Ω: the source current rings at fs = 1/(2π√(Lm·Cm)), within 0.5 %."""
    tstop_s = {"m": 1e-3, "u": 1e-6, "n": 1e-9}
    end = float(tstop[:-1]) * tstop_s[tstop[-1]]
    start = float(t1[:-1]) * tstop_s[t1[-1]]
    assert ring_of_source_current(kind, tr, step, end, start) == pytest.approx(fs, rel=5e-3)
