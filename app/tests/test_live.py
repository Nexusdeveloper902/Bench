"""Live runs through app/sim.py (task 9.5a), real ngspice: which params are live (package data), LiveSession driven from
a worker thread with controls from another, replay_document, refusal, recovery, pacing. Contract:
docs/plans/ngspice-library/LIVE.md."""
import math
import threading
import time

import pytest

from app.catalog import CATALOG
from app.components import ModelError
from app.electrical import live_devices
from app.model import value_at
from app.sim import LiveSession, live_params, replay_document, run_document
from app.tests.test_library_batch_c import Circuit

STIMULI = {"LDR": "lux", "PTC_THERMISTOR": "temp", "RTD_PT100": "temp", "RTD_PT1000": "temp", "PHOTODIODE": "irr",
           "PHOTOTRANSISTOR": "irr", "HALL_LINEAR": "b", "FSR": "force", "STRAIN_GAUGE": "eps", "STRAIN_BRIDGE": "eps",
           "POSITION_SENSOR": "pos", "PRESSURE_BRIDGE": "pres", "LM35": "temp", "DC_MOTOR": "speed",
           "HUMIDITY_CAP": "rh", "SOLENOID": "pos"}


def ldr_out(lux, vs=5.0):
    return vs * 10e3 / (10e3 + 10e3 * (lux / 10) ** -0.7)


def divider(kind="LDR", tstop="2m", tstep="10u", **params):
    """V1 (5 V) -> part a, part b -> 10k -> ground; returns (circuit, part, source, output net)."""
    c = Circuit()
    v = c.vdc("5", "")
    p = c.add(kind, **params)
    c.r("10k", f"{p}.b", "0")
    c.w(f"{v}.p", f"{p}.a")
    c.d.tstop, c.d.tstep = tstop, tstep
    return c, p, v, f"V({c.d.nets()[p + '.b']})"


def collect(s):
    for _ in s: pass
    return s.result


class Worker:
    """Iterate a session on a thread, as the UI will; on exit cancel and join, so a failing test never leaves a
    worker blocked in a paused run (which would hold the engine and hang the suite)."""
    def __init__(self, s):
        self.s, self.t = s, threading.Thread(target=collect, args=(s,))

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.s.cancel()
        self.t.join()


# ---------------------------------------------------------------- what is live (package data only)
@pytest.mark.parametrize("kind", sorted(STIMULI))
def test_every_stimulus_param_is_live_and_flagged(kind):
    d = CATALOG.get(kind)
    devices, keys = live_devices(d.sim["electrical"], "U1", d.values(d.defaults(), "U1"))
    assert keys[STIMULI[kind]] is True and [k for k, s in keys.items() if s] == [STIMULI[kind]]
    assert "v.xu1.vstim" in devices


@pytest.mark.parametrize("kind", ["MIC_ELECTRET", "C", "NE555", "SN74HC00"])
def test_static_parts_have_no_stimulus(kind):
    d = CATALOG.get(kind)
    _, keys = live_devices(d.sim["electrical"], "U1", d.values(d.defaults(), "U1"))
    assert not any(keys.values())


def test_primitives_are_live_and_waveforms_are_not():
    c = Circuit()
    v = c.vdc("5", "")
    sw = c.add("SW", state="open")
    pot = c.add("POT")
    pulse = c.add("V", mode="PULSE")
    keys = {(p["part"], p["key"]): p["stimulus"] for p in live_params(c.d)}
    assert {(v, "dc"), (sw, "state"), (pot, "pos"), (pot, "R")} <= set(keys) and not any(keys.values())
    assert not any(p == pulse for p, _ in keys)


# ---------------------------------------------------------------- live runs
def test_ldr_lux_step_at_t1_matches_the_law_before_and_after():
    c, ldr, _, out = divider()
    s = LiveSession(c.d, chunk=1e-3)
    first = next(s)
    assert first["t"] == 1e-3 and first["changes"] == []
    s.set_stimulus(ldr, 100)                      # key None: the LDR's one stimulus, lux
    second = next(s)
    assert second["changes"] == [(1e-3, ldr, "lux", 100.0)] and second["done"]
    r = s.result
    assert r.ok and r.status == "success" and s.state == "completed"
    for t, v in r.get(out):
        assert v == pytest.approx(ldr_out(10) if t <= 1e-3 else ldr_out(100), abs=1e-9), t
    assert run_document(c.d).ok                   # the engine was released


def test_live_session_replays_as_a_batch_run():
    c, ldr, v, out = divider(tstop="4m", tstep="2u")
    cap = c.add("C", C="100n")
    c.w(f"{cap}.a", f"{ldr}.b")
    c.w(f"{cap}.b", "0")
    s = LiveSession(c.d, chunk=1e-4, realtime=0.2)
    with Worker(s) as w:
        time.sleep(0.005)
        s.set_stimulus(ldr, "300")
        time.sleep(0.007)
        s.set_stimulus(v, 4, key="dc")
        s.set_stimulus(ldr, 2.0)
        w.t.join()
    assert s.result.status == "success"
    log = s.changes
    assert [(p, k, x) for _, p, k, x in log] == [(ldr, "lux", 300.0), (v, "dc", 4.0), (ldr, "lux", 2.0)]
    assert log[1][0] == log[2][0] and all(abs(t / 1e-4 - round(t / 1e-4)) < 1e-6 for t, *_ in log)
    rep = replay_document(c.d, log)
    assert rep.ok and s.replay().get(out) == rep.get(out)
    live, again = s.result.get(out), rep.get(out)
    assert max(abs(x - value_at(again, t)) for t, x in live) < 2e-3
    assert live[-1][1] == pytest.approx(again[-1][1], abs=1e-4)


def test_switch_and_pot_change_live_together():
    c = Circuit()
    sw = c.add("SW", state="open")
    pot = c.add("POT", R="10k", pos="0.5")
    v = c.vdc("6", f"{sw}.a")
    c.w(f"{sw}.b", f"{pot}.a")
    c.w(f"{pot}.b", "0")
    c.r("1meg", f"{pot}.w", "0")
    c.d.tstop, c.d.tstep = "2m", "10u"
    s = LiveSession(c.d, chunk=1e-3)
    a = next(s)
    s.set_stimulus(sw, "closed", key="state")
    s.set_stimulus(pot, 0.2, key="pos")
    b = next(s)
    wiper = s.result.signal(f"V({c.d.nets()[pot + '.w']})")["vector"]
    assert a["series"][wiper][-1][1] == pytest.approx(0, abs=1e-4)          # open switch: ~0 V
    ra, rb = 10e3 * 0.2 + 1, 10e3 * 0.8 + 1                                  # POT: R*pos+rw, R*(1-pos)+rw
    rbl = rb * 1e6 / (rb + 1e6)
    assert b["series"][wiper][-1][1] == pytest.approx(6 * rbl / (0.01 + ra + rbl), rel=1e-6)
    assert [(p, k) for _, p, k, _ in b["changes"]] == [(sw, "state"), (pot, "pos")]
    assert replay_document(c.d, s.changes).get(wiper)[-1][1] == pytest.approx(b["series"][wiper][-1][1], rel=1e-9)


def test_pause_and_resume_keep_the_capacitor_continuous():
    c = Circuit()
    cap = c.add("C", C="1u")
    r = c.add("R", R="1k")
    c.vdc("5", f"{r}.a")
    c.w(f"{r}.b", f"{cap}.a")
    c.w(f"{cap}.b", "0")
    c.d.tstop, c.d.tstep = "5m", "10u"
    s = LiveSession(c.d, chunk=2.5e-4)
    out = f"V({c.d.nets()[cap + '.a']})"
    s.pause()
    with Worker(s) as w:
        time.sleep(0.03)
        assert s.state == "paused" and s.result.get(out) == []
        s.resume()
        time.sleep(0.002)
        s.pause()
        time.sleep(0.02)
        s.resume()
        w.t.join()
    pts = s.result.get(out)
    assert max(abs(v - 5 * (1 - math.exp(-t / 1e-3))) for t, v in pts) < 0.02
    assert all(b[0] > a[0] for a, b in zip(pts, pts[1:]))


def test_cancel_mid_run_then_batch_works():
    c, ldr, _, out = divider(tstop="5m")
    s = LiveSession(c.d, chunk=1e-4, realtime=0.05)
    with Worker(s):
        time.sleep(0.03)
    assert s.result.status == "cancelled" and s.state == "cancelled" and 0 < len(s.result.get(out)) < 400
    with pytest.raises(ModelError, match="no live run"):
        s.set_stimulus(ldr, 5)
    assert run_document(c.d).ok


def test_engine_error_mid_run_fails_the_session_and_the_engine_recovers():
    c, ldr, _, out = divider()
    s = LiveSession(c.d, chunk=5e-4)
    next(s)
    s.set_stimulus(ldr, "1e-300")                 # inside the param's range (> 0), breaks the LDR law in ngspice
    assert collect(s).status == "failed" and s.state == "failed"
    assert "SolverError" in s.result.message and len(s.result.get(out)) > 10    # the points before the error stay
    assert run_document(c.d).ok and collect(LiveSession(c.d)).ok


def test_a_batch_run_is_refused_while_live_and_a_blocked_circuit_never_starts():
    c, ldr, _, _ = divider()
    s = LiveSession(c.d)
    r = run_document(c.d)
    assert r.status == "failed" and "live run is active" in r.message
    assert LiveSession(c.d).result.status == "failed"         # a second live run too
    s.close()
    assert s.result.status == "cancelled" and run_document(c.d).ok
    c.d.tstep = "1"                                           # tstep > tstop: the pre-check blocks
    b = LiveSession(c.d)
    assert b.result.status == "blocked" and list(b) == [] and b.state == "blocked" and b.replay() is b.result


def test_bad_live_changes_are_user_errors():
    c, ldr, v, _ = divider()
    rfix = next(n for n in c.d.parts if n.startswith("R") and n != ldr)
    s = LiveSession(c.d)
    for part, value, key, why in [(ldr, 0, None, "must be > 0"), (ldr, 5, "r10", "cannot change during a live run"),
                                  ("X9", 5, None, "no part X9"), (v, 5, None, "no stimulus"),
                                  (rfix, "abc", "R", "R")]:
        with pytest.raises(ModelError, match=why):
            s.set_stimulus(part, value, key)
    s.close()
    assert replay_document(c.d, [(1e-3, ldr, "r10", 5.0)]).status == "blocked"


def test_pacing_holds_the_requested_factor():
    c, *_ = divider(tstop="20m")
    s = LiveSession(c.d, realtime=0.1)
    t0 = time.perf_counter()
    last = list(s)[-1]
    assert last["rtf"] == pytest.approx(0.1, rel=0.1)
    assert time.perf_counter() - t0 == pytest.approx(0.2, abs=0.04)


# ---------------------------------------------------------------- gap closure W2 (G34, G35, G40, G42)
def test_adaptive_chunks_shrink_to_meet_the_wall_time_target():
    """G34: LiveSession sizes chunks to TARGET_WALL by default; a target no chunk can meet shrinks the next chunk
    to the floor (chunk / 1024) at once, and a change still lands on a boundary."""
    c, ldr, _, out = divider()
    s = LiveSession(c.d, chunk=1e-4, target=1e-9)
    assert next(s)["t"] == 1e-4
    assert next(s)["t"] == pytest.approx(1e-4 + 1e-4 / 1024, rel=1e-12)
    s.set_stimulus(ldr, 100)
    c3 = next(s)
    assert c3["changes"][0][0] == pytest.approx(1e-4 + 1e-4 / 1024, rel=1e-12)
    s.close()
    fast = LiveSession(c.d, chunk=1e-4)           # default target (50 ms): a light circuit keeps the full chunk
    assert [round(x["t"] / 1e-4, 6) for x in fast][:3] == [1, 2, 3]


@pytest.mark.parametrize("kind,shown", [("CAP_CERAMIC", set()), ("CAP_ELECTROLYTIC", set()), ("OPAMP_MACRO", set()),
                                        ("SCR", set()), ("CRYSTAL_16M", set()), ("SPDT", {"state"}),
                                        ("HALL_LINEAR", {"b"}), ("POT", {"R", "pos"}), ("LDR", {"lux"})])
def test_a_package_live_list_overrides_the_derived_rule(kind, shown):
    """G35: `live = [...]` in [sim.electrical] keeps only the meaningful controls (internal ESR, vos/ib/iq, SCR
    internals and crystal rm are hidden); a package without it keeps the derived rule (LDR)."""
    c = Circuit()
    p = c.add(kind)
    assert {x["key"] for x in live_params(c.d) if x["part"] == p} == shown


def test_every_package_live_key_is_one_the_derived_rule_can_change():
    for d in CATALOG.defs.values():
        sec = d.sim.get("electrical", {}) if d.simulated else {}
        if "live" in sec:
            _, keys = live_devices(sec, "U1", d.values(d.defaults(), "U1"))
            assert set(sec["live"]) <= set(keys), (d.id, sec["live"], keys)


def test_a_live_list_is_checked_and_an_empty_one_makes_nothing_live(extra_root):
    from app import catalog
    from conftest import write_def
    write_def(extra_root, "LIVEBAD", mutate=lambda d: d["sim"]["electrical"].update(live=["nope"]))
    assert any(p["id"] == "LIVEBAD" and p["field"] == "sim.electrical.live" for p in catalog.CATALOG.problems)
    write_def(extra_root, "LIVENONE", mutate=lambda d: d["sim"]["electrical"].update(live=[]))
    c = Circuit()
    r = c.add("LIVENONE")
    v = c.vdc("5", f"{r}.a")
    c.w(f"{r}.b", "0")
    c.d.tstop, c.d.tstep = "1m", "10u"
    assert [x["part"] for x in live_params(c.d)] == [v]
    s = LiveSession(c.d)
    with pytest.raises(ModelError, match="cannot change during a live run"):
        s.set_stimulus(r, 50, key="R")
    assert collect(s).status == "success"


def test_tstop_defaults_are_pinned_to_the_batch_stop_time():
    """G40, the rewrite: missing or zero PULSE pw/per, SIN freq and SFFM fc/fs become the batch run's values."""
    from app.sim import pin_tstop_defaults as pin
    lines = ["V1 a 0 PULSE(0 5 0 1u 1u 0.5m)", "V2 b 0 SIN(0 1)", "I3 c 0 pulse(0 1)", "V4 sin 0 DC 5",
             "V5 d 0 SIN(0 1 0 1m)", "V6 e 0 SFFM(0 1 {F} 2)", "R1 a b 1k", "V7 f 0 DC {sin(1)}"]
    assert pin(lines, 2e-3, 1e-5, True) == [
        "V1 a 0 PULSE(0 5 0 1u 1u 0.5m 0.002)", "V2 b 0 SIN(0 1 500.0)",
        "I3 c 0 pulse(0 1 0 1e-05 1e-05 0.002 0.002)", "V4 sin 0 DC 5", "V5 d 0 SIN(0 1 500.0 1m)",
        "V6 e 0 SFFM(0 1 {F} 2 500.0)", "R1 a b 1k", "V7 f 0 DC {sin(1)}"]
    for bad in ("V1 a 0 PULSE 0 5", "V1 a 0 PULSE(0 5", "V1 a 0 AM(1 0 0 1k)"):
        assert pin([bad], 2e-3, 1e-5, False) == [bad]               # same tstop: passed on as written
        with pytest.raises(ModelError, match="Live run refused"):
            pin([bad], 2e-3, 1e-5, True)


def test_a_pulse_with_a_default_period_repeats_at_the_stop_time_in_an_indefinite_run(extra_root):
    """G40, end to end: a package PULSE without a period repeats every stop time in the batch run; an indefinite
    live run (tstop = tstep x 1e12) keeps that period instead of following its own huge tstop."""
    from conftest import write_def
    write_def(extra_root, "PULSEDEF", mutate=lambda d: d["sim"]["electrical"].update(
        device="V", line="{dev} {a} {b} PULSE(0 5 0 1u 1u 0.5m)"))
    c = Circuit()
    v = c.add("PULSEDEF")
    c.r("1k", f"{v}.a", "0")
    c.w(f"{v}.b", "0")
    c.d.tstop, c.d.tstep = "2m", "10u"
    out = f"V({c.d.nets()[v + '.a']})"
    batch = run_document(c.d).get(out)
    c.d.live_tstop = "indefinite"
    s = LiveSession(c.d, chunk=1e-3)
    for chunk in s:
        if chunk["t"] >= 5e-3: s.cancel()
    live = s.result.get(out)
    assert s.result.status == "cancelled"
    assert value_at(batch, 0.25e-3) == pytest.approx(5) and value_at(batch, 1.5e-3) == pytest.approx(0, abs=1e-9)
    for t, want in ((2.25e-3, 5), (3e-3, 0), (4.25e-3, 5)):        # the period is the batch stop time, 2 ms
        assert value_at(live, t) == pytest.approx(want, abs=1e-6), t


def test_overview_is_bounded_and_keeps_the_extremes():
    """G42, the record: 1e6 points through 10k chunks stay within `buckets` buckets, the extremes of the whole run
    are kept exactly, and before() gives a time-ordered coarse series with one time base."""
    from app.sim import Overview
    o = Overview(1e-3, buckets=100)
    for k in range(10_000):
        ts = [(k * 100 + i) * 1e-5 for i in range(100)]
        o.add({"a": [(t, math.sin(t)) for t in ts],
               "b": [(t, 7.0 if k == 1234 and i == 50 else 0.0) for i, t in enumerate(ts)]})
        assert len(o.b) <= 100
    co = o.before(math.inf)
    assert max(v for _, v in co["b"]) == 7.0 and min(v for _, v in co["a"]) == pytest.approx(-1, abs=1e-9)
    assert [t for t, _ in co["a"]] == [t for t, _ in co["b"]] == sorted(t for t, _ in co["a"]) and len(co["a"]) <= 200


def test_after_a_long_run_the_whole_run_is_kept_coarsely_with_its_extremes():
    """G42, end to end: an indefinite run keeps the last stop time in full and, before it, the decimated record, so
    a lux spike long out of the window is still in the result (its value exactly) after Stop."""
    c, ldr, _, out = divider(tstop="2m")
    c.d.live_tstop = "indefinite"
    s = LiveSession(c.d, chunk=1e-3)
    for k, chunk in enumerate(s, 1):
        if k == 5: s.set_stimulus(ldr, 1000)
        if k == 8: s.set_stimulus(ldr, 10)
        if k == 40: s.cancel()
    pts = s.result.get(out)
    assert pts[0][0] < 1e-4 and pts[-1][0] == pytest.approx(40e-3)
    assert s.result.stats(out)["max"] == pytest.approx(ldr_out(1000), abs=1e-9)
    assert value_at(pts, 6e-3) == pytest.approx(ldr_out(1000), abs=1e-6)
    window = [t for t, _ in pts if t >= 38e-3]
    assert len(window) > 150 and len(pts) - len(window) <= 2 * s.overview.n
