"""Live runs through the Python API (task 9.5a), real ngspice: LiveRun iterated on a worker thread, controls from
another, replay, refusal, recovery, pacing, GIL release. Contract: docs/plans/ngspice-library/LIVE.md."""
import bisect
import math
import threading
import time

import pytest

import benchcore
from benchcore import BackendError, InvalidConfigError, Simulation, SolverError

LDR = ["V1 vcc 0 DC 5", "XRL1 vcc out bench_ldr R10=10000 GAMMA=0.7 LUX=10", "R1 out 0 10k",
       ".subckt bench_ldr a b params: R10=10k GAMMA=0.7 LUX=10", "VSTIM stim 0 DC {LUX}", "RSTIM stim 0 1G",
       "B1 a b I={V(a,b)/(R10*pow(V(stim)/10,-GAMMA))}", ".ends bench_ldr"]


def ldr(extra=(), tstop=2e-3, tstep=10e-6):
    return Simulation("ldr").build_circuit(LDR + list(extra)).configure(tstep=tstep, tstop=tstop, tmax=tstep).observe("v(out)")


def rc(tstop=5e-3):
    return Simulation("rc").build_circuit(["V1 in 0 DC 5", "R1 in out 1k", "C1 out 0 1u IC=0"]).configure(
        tstep=10e-6, tstop=tstop, tmax=10e-6).observe("v(out)")


def ldr_out(lux):
    return 5 * 10e3 / (10e3 + 10e3 * (lux / 10) ** -0.7)


def interp(s, t):
    k = min(max(bisect.bisect_left(s, t, key=lambda p: p[0]), 1), len(s) - 1)
    (t0, v0), (t1, v1) = s[k - 1], s[k]
    return v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def drain(run, name="v(out)"):
    out = []
    for c in run:
        out += c["series"][name]
    return out


def test_lux_step_at_t1_matches_the_ldr_law_before_and_after():
    run = ldr().live(["v.xrl1.vstim"], chunk=1e-3)
    a = next(run)
    run.set({"v.xrl1.vstim": 100.0})
    b = next(run)
    assert b["done"] and b["changes"] == [(1e-3, "v.xrl1.vstim", 100.0)]
    assert a["series"]["v(out)"][-1][0] == 1e-3
    assert all(v == pytest.approx(ldr_out(10), abs=1e-9) for _, v in a["series"]["v(out)"])
    assert all(t > 1e-3 and v == pytest.approx(ldr_out(100), abs=1e-9) for t, v in b["series"]["v(out)"])
    with pytest.raises(StopIteration):
        next(run)
    assert run.state == "done"


def test_live_run_from_a_worker_thread_replays_as_a_batch_run():
    sim = ldr(["C1 out 0 100n IC=0"], tstop=4e-3, tstep=2e-6)
    run = sim.live(["v.xrl1.vstim"], chunk=1e-4, realtime=0.2)
    out, seqs = [], []

    def worker():
        for c in run:
            out.extend(c["series"]["v(out)"])

    w = threading.Thread(target=worker)
    w.start()
    time.sleep(0.005)
    seqs.append(run.set({"v.xrl1.vstim": 300.0}))
    time.sleep(0.007)
    seqs.append(run.set({"v.xrl1.vstim": 2.0}))
    w.join()
    log = run.changes
    assert seqs == [1, 2] and [c[2] for c in log] == [300.0, 2.0]
    rep = sim.replay(log)["series"]["v(out)"]
    assert max(abs(v - interp(rep, t)) for t, v in out) < 2e-3
    assert sim.replay(log)["series"]["v(out)"] == rep     # deterministic


def test_pause_resume_from_another_thread_keeps_the_capacitor_continuous():
    run = rc().live(chunk=2.5e-4)
    out, gaps = [], []

    def worker():
        for c in run:
            if out:
                gaps.append((out[-1], c["series"]["v(out)"][0]))
            out.extend(c["series"]["v(out)"])

    w = threading.Thread(target=worker)
    run.pause()                         # paused before the first chunk: the worker waits
    w.start()
    try:
        time.sleep(0.03)
        assert out == [] and run.state == "paused"
        run.resume()
        time.sleep(0.002)
        run.pause()
        time.sleep(0.03)
        run.resume()
    finally:                            # never leave a worker blocked in a paused run (it holds the engine)
        run.cancel()
        w.join()
    assert max(abs(v - 5 * (1 - math.exp(-t / 1e-3))) for t, v in out) < 0.02
    for (tp, vp), (tn, vn) in gaps:
        assert abs(vn - vp) <= 1.5 * 5e3 * math.exp(-tp / 1e-3) * (tn - tp)


def test_cancel_mid_run_then_batch_and_live_work():
    run = rc().live(chunk=1e-4, realtime=0.05)
    n = []
    w = threading.Thread(target=lambda: n.append(sum(1 for _ in run)))
    w.start()
    time.sleep(0.03)
    run.cancel()
    w.join()
    assert 0 < n[0] < 50 and run.state == "cancelled"
    assert rc().run()["npts"] > 100
    assert len(drain(rc(1e-3).live())) > 50


def test_solver_error_mid_run_raises_and_the_engine_recovers():
    run = ldr().live(["v.xrl1.vstim"], chunk=5e-4)
    next(run)
    run.set({"v.xrl1.vstim": 0.0})
    with pytest.raises(SolverError, match="out of range"):
        next(run)
    assert run.state == "failed"
    with pytest.raises(StopIteration):
        next(run)
    assert rc().run()["npts"] > 100


def test_batch_runs_are_refused_while_live():
    run = rc().live(chunk=1e-3)
    next(run)
    for start in (lambda: rc().run(), lambda: rc().replay([]), lambda: rc().live()):
        with pytest.raises(BackendError, match="live run is active"):
            start()
    assert len(drain(run)) > 100       # the live run itself is undisturbed
    assert rc().run()["npts"] > 100


def test_close_without_a_worker_releases_the_engine():
    run = rc().live()
    run.close()
    run.close()
    assert run.state == "cancelled" and rc().run()["npts"] > 100


def test_pacing_holds_the_requested_factor():
    t0 = time.perf_counter()
    last = list(rc(20e-3).live(realtime=0.1))[-1]
    assert last["rtf"] == pytest.approx(0.1, rel=0.1)
    assert time.perf_counter() - t0 == pytest.approx(0.2, abs=0.04)


def test_two_live_devices_at_once():
    sim = ldr(["V2 s 0 DC 3", "RSW1 s q 1e9", "R3 q 0 1k"]).observe("v(out)", "v(q)")
    run = sim.live(["v.xrl1.vstim", "rsw1"], chunk=1e-3)
    next(run)
    run.set({"v.xrl1.vstim": 1000.0, "rsw1": 0.01})
    c = next(run)
    assert c["series"]["v(out)"][-1][1] == pytest.approx(ldr_out(1000), abs=1e-9)
    assert c["series"]["v(q)"][-1][1] == pytest.approx(3 * 1e3 / (1e3 + 0.01), abs=1e-9)
    assert sorted(d for _, d, _ in c["changes"]) == ["rsw1", "v.xrl1.vstim"]


def test_bad_devices_and_values_are_refused():
    with pytest.raises(InvalidConfigError, match="not in the circuit"):
        ldr().live(["v.xrl1.nope"])
    run = ldr().live(["v1"])
    with pytest.raises(InvalidConfigError, match="not declared live"):
        run.set({"r1": 5.0})
    run.close()


def test_the_gil_is_released_while_a_chunk_computes():
    sim = rc(0.2)            # 20000 steps in one chunk: tens of ms
    run = sim.live(chunk=0.2)
    count, done = [0], threading.Event()

    def worker():
        next(run)
        done.set()

    w = threading.Thread(target=worker)
    t0 = time.perf_counter()
    w.start()
    while not done.is_set():
        count[0] += 1
    w.join()
    assert time.perf_counter() - t0 > 0.01 and count[0] > 10000, count
    assert run.state == "done"


def test_package_exports():
    assert benchcore.LiveRun is benchcore._benchcore.LiveRun and benchcore.replay is benchcore._benchcore.replay
