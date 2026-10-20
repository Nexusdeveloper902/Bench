"""G24: no spurious clock edge at power-up in the edge-triggered digital packages.

XSPICE digital nodes start low and Bench runs the transient with UIC, so a clock path that is already high at t = 0 used to give
every edge-triggered d_* model one clock edge at the start (a held-high clock captured D, an ADC converted at t = 0). The clock
input stages are now `bench_dclk` (io.lib), which lets an edge through only after the clock has been seen at its idle level.

For every edge-triggered package (flip-flops, counters, shift registers, the ADC): a clock held at either level for the whole run
changes nothing, and the first real edge after a held-active start (or an idle start for a falling-edge part) is an ordinary
edge. Real path: Document -> netlist -> app.sim -> benchcore -> ngspice."""
import pytest

from test_library_batch_d import C

# edge: "r" = the part clocks on the rising edge, "f" = on the falling edge. tie: pin -> level (True = supply, False = 0, float = volts).
# watch: output pins, least significant first; idle = their power-up word; after = the word after exactly one active edge.
PARTS = {
    "SN74HC74": dict(vdd=5, sup=("vcc", "gnd"), clk="ck1", edge="r", tie={"d1": True, "pre1": True, "clr1": True},
                     watch=["q1"], idle=0, after=1),
    "CD4013": dict(vdd=10, sup=("vdd", "vss"), clk="ck1", edge="r", tie={"d1": True, "s1": False, "r1": False},
                   watch=["q1"], idle=0, after=1),
    "SN74HC107": dict(vdd=5, sup=("vcc", "gnd"), clk="ck1", edge="f", tie={"j1": True, "k1": True, "r1": True},
                      watch=["q1"], idle=0, after=1),
    "CD4027": dict(vdd=10, sup=("vdd", "vss"), clk="ck1", edge="r", tie={"j1": True, "k1": True, "s1": False, "r1": False},
                   watch=["q1"], idle=0, after=1),
    "SN74HC161": dict(vdd=5, sup=("vcc", "gnd"), clk="clk", edge="r", tie={}, watch=["qa", "qb", "qc", "qd"], idle=0, after=1),
    "CD4017": dict(vdd=10, sup=("vdd", "vss"), clk="clk", edge="r", tie={}, watch=[f"q{i}" for i in range(10)], idle=1, after=2),
    "CD4040": dict(vdd=10, sup=("vdd", "vss"), clk="clk", edge="f", tie={}, watch=[f"q{i}" for i in range(1, 13)], idle=0, after=1),
    "SN74HC393": dict(vdd=5, sup=("vcc", "gnd"), clk="clk1", edge="f", tie={}, watch=["q1a", "q1b", "q1c", "q1d"], idle=0, after=1),
    "SN74HC595": dict(vdd=5, sup=("vcc", "gnd"), clk="srclk", edge="r", tie={"ser": True, "srclrb": True, "oeb": False},
                      watch=[f"q{x}" for x in "abcdefgh"], idle=0, after=1, rclk=True),
    "CD4094": dict(vdd=10, sup=("vdd", "vss"), clk="clock", edge="r", tie={"data": True, "strobe": True, "oe": True},
                   watch=[f"q{i}" for i in range(1, 9)], idle=0, after=1),
    "ADC_8BIT": dict(vdd=5, sup=("vcc", "gnd"), clk="clk", edge="r", tie={"vin": 2.55, "ref": 5.0},
                     watch=[f"d{i}" for i in range(8)], idle=0, after=130),
}
T_CHECK = 3.0e-6


def build(id, wave):
    """wave: "high" | "low" | "first_edge" (starts at the level the part treats as idle-after... see below)."""
    p = PARTS[id]
    vdd = p["vdd"]
    c = C()
    u = c.add(id)
    c.power(u, vdd, *p["sup"])
    for pin, lv in p["tie"].items():
        c.dc(f"{u}.{pin}", vdd if lv is True else 0 if lv is False else lv)
    clk = f"{u}.{p['clk']}"
    if wave == "high":
        c.dc(clk, vdd)
    elif wave == "low":
        c.dc(clk, 0)
    else:
        # first_edge: held at the ACTIVE level from t = 0 (high for a rising-edge part), leaves it at 1 us, returns at 2 us; a
        # falling-edge part starts low (its idle level is high), rises at 1 us and has its first real falling edge at 2 us.
        # idle_start: starts at the idle level and has its first active edge at 1 us.
        act = vdd if p["edge"] == "r" else 0.0
        idle = vdd - act
        v1, v2 = (act, idle) if wave == "first_edge" else (idle, act)
        pw = 1e-6 if wave == "first_edge" else 50e-6
        s = c.add("V", mode="PULSE", v1=v1, v2=v2, td=1e-6, tr="10n", tf="10n", pw=pw, per=100e-6)
        c.w(f"{s}.p", clk)
        c.w(f"{s}.n", "0")
    if p.get("rclk"):       # shift-register parts: latch the shift register to the outputs with a rising edge at 2.5 us
        s = c.add("V", mode="PULSE", v1=0.0, v2=vdd, td=2.5e-6, tr="10n", tf="10n", pw=0.3e-6, per=100e-6)
        c.w(f"{s}.p", f"{u}.rclk")
        c.w(f"{s}.n", "0")
    for w in p["watch"]:
        c.load(f"{u}.{w}")
    c.run(tstop=f"{T_CHECK + 0.2e-6:.3e}", tstep="10n")
    return c, u, vdd, p


def word(id, wave):
    c, u, vdd, p = build(id, wave)
    return c.word([f"{u}.{w}" for w in p["watch"]], T_CHECK, vdd)


@pytest.mark.parametrize("id", sorted(PARTS))
@pytest.mark.parametrize("wave", ["high", "low"])
def test_clock_held_at_either_level_from_t0_gives_no_edge(id, wave):
    assert word(id, wave) == PARTS[id]["idle"], (id, wave)


@pytest.mark.parametrize("id", sorted(PARTS))
def test_first_real_edge_after_a_held_start_is_an_ordinary_edge(id):
    """Clock starts at its active level (rising-edge part) or idle level (falling-edge part): no edge at t = 0, one at 2 us."""
    assert word(id, "first_edge") == PARTS[id]["after"], id


@pytest.mark.parametrize("id", sorted(PARTS))
def test_clock_starting_idle_has_its_first_edge_counted(id):
    """The gate must not swallow a valid first edge: idle at t = 0 (low for a rising-edge part), active edge at 1 us."""
    assert word(id, "idle_start") == PARTS[id]["after"], id
