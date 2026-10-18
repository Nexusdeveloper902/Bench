//! Live runs against real ngspice (task 9.5a): a change at t affects the solution from t on, live == replay,
//! pause/resume continuity, cancel, errors and recovery, batch refusal, pacing, several live parts.
//! The live claim is process-wide, so every test holds SERIAL (cargo runs tests on parallel threads).

use benchcore::kernel::{run_simulation, SimRequest};
use benchcore::live::{replay, Change, LiveRun, LiveSim};
use benchcore::SimError;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

static SERIAL: Mutex<()> = Mutex::new(());

fn serial() -> MutexGuard<'static, ()> {
    SERIAL.lock().unwrap_or_else(|e| e.into_inner())
}

fn deck(lines: &[&str]) -> Vec<String> {
    lines.iter().map(|s| s.to_string()).chain([".end".to_string()]).collect()
}

/// LDR (the task-9 package body) in a divider: out = 5·10k/(10k + R10·(lux/10)^-0.7).
fn ldr_deck(extra: &[&str], tran: &str) -> Vec<String> {
    let mut l = vec!["ldr", "V1 vcc 0 DC 5", "XRL1 vcc out bench_ldr R10=10000 GAMMA=0.7 LUX=10", "R1 out 0 10k",
                     ".subckt bench_ldr a b params: R10=10k GAMMA=0.7 LUX=10", "VSTIM stim 0 DC {LUX}",
                     "RSTIM stim 0 1G", "B1 a b I={V(a,b)/(R10*pow(V(stim)/10,-GAMMA))}", ".ends bench_ldr"];
    l.extend_from_slice(extra);
    l.push(tran);
    deck(&l)
}

fn ldr_out(lux: f64) -> f64 {
    5.0 * 10e3 / (10e3 + 10e3 * (lux / 10.0).powf(-0.7))
}

fn rc_deck(tstop: &str) -> Vec<String> {
    deck(&["rc", "V1 in 0 DC 5", "R1 in out 1k", "C1 out 0 1u IC=0", &format!(".tran 10u {} 0 10u uic", tstop)])
}

fn interp(s: &[(f64, f64)], t: f64) -> f64 {
    let k = s.partition_point(|p| p.0 < t);
    if k == 0 {
        return s[0].1;
    }
    if k == s.len() {
        return s[k - 1].1;
    }
    let ((t0, v0), (t1, v1)) = (s[k - 1], s[k]);
    v0 + (v1 - v0) * (t - t0) / (t1 - t0)
}

/// Max |a - b| on a uniform grid, skipping `skip` (a window around a step, where interpolation between grids
/// with different points says nothing about the solution).
fn max_diff(a: &[(f64, f64)], b: &[(f64, f64)], tstop: f64, skip: &[f64]) -> f64 {
    (0..=2000)
        .map(|i| tstop * i as f64 / 2000.0)
        .filter(|t| skip.iter().all(|s| (t - s).abs() > 2e-5))
        .map(|t| (interp(a, t) - interp(b, t)).abs())
        .fold(0.0, f64::max)
}

/// Drain a LiveRun on this thread, concatenating its chunks.
fn collect(run: &LiveRun, name: &str) -> Vec<(f64, f64)> {
    let mut out = vec![];
    while let Some(c) = run.next_chunk().expect("chunk") {
        out.extend(&c.series[name]);
    }
    out
}

fn batch(lines: Vec<String>, obs: &str) -> Result<Vec<(f64, f64)>, SimError> {
    run_simulation(SimRequest { netlist: lines, observables: vec![obs.to_string()], stops: vec![] })
        .map(|r| r.series[obs].clone())
}

#[test]
fn ldr_lux_step_takes_effect_exactly_at_t1() {
    let _g = serial();
    let mut sim = LiveSim::start(&ldr_deck(&[], ".tran 10u 2m 0 10u uic"), &["v(out)".into()], &["v.xrl1.vstim".into()])
        .unwrap();
    let a = sim.advance(&[], 1e-3).unwrap();
    assert!(!a.done && a.t_end == 1e-3);
    let b = sim.advance(&[("V.XRL1.VSTIM".into(), 100.0)], 2e-3).unwrap(); // names are case-insensitive
    assert!(b.done);
    assert_eq!(b.changes, vec![Change { t: 1e-3, device: "v.xrl1.vstim".into(), value: 100.0 }]);
    let before = &a.series["v(out)"];
    let after = &b.series["v(out)"];
    assert_eq!(before.last().unwrap().0, 1e-3, "the chunk ends exactly at t1");
    for (_, v) in before {
        assert!((v - ldr_out(10.0)).abs() < 1e-9, "before t1: {} vs {}", v, ldr_out(10.0));
    }
    for (t, v) in after {
        assert!(*t > 1e-3 && (v - ldr_out(100.0)).abs() < 1e-9, "after t1: {} at {}", v, t);
    }
    // the engine was released at the end: a batch run works
    assert!(batch(rc_deck("1m"), "v(out)").is_ok());
}

#[test]
fn rc_source_change_matches_analytic_and_a_batch_pwl_step() {
    let _g = serial();
    let (tau, t1) = (1e-3, 1e-3);
    let v1 = 5.0 * (1.0 - (-t1 / tau as f64).exp());
    let ana = |t: f64| if t <= t1 { 5.0 * (1.0 - (-t / tau).exp()) } else { v1 * (-(t - t1) / tau).exp() };
    let run = LiveRun::start(&rc_deck("3m"), &["v(out)".into()], &["v1".into()], Some(1e-3), None).unwrap();
    let mut live = run.next_chunk().unwrap().unwrap().series["v(out)"].clone();
    run.set(&[("v1".into(), 0.0)]).unwrap();
    live.extend(collect(&run, "v(out)"));
    assert_eq!(run.state(), "done");
    let err = live.iter().map(|(t, v)| (v - ana(*t)).abs()).fold(0.0, f64::max);
    assert!(err < 1e-4, "live vs analytic {}", err);
    let pwl = batch(deck(&["rc", "V1 in 0 PWL(0 5 1m 5 1.000000001m 0)", "R1 in out 1k", "C1 out 0 1u IC=0",
                           ".tran 10u 3m 0 10u uic"]), "v(out)").unwrap();
    assert!(max_diff(&live, &pwl, 3e-3, &[]) < 1e-4, "live vs batch PWL {}", max_diff(&live, &pwl, 3e-3, &[]));
}

#[test]
fn live_run_replays_as_a_batch_run() {
    let _g = serial();
    // RC load on the LDR so the state carries across changes; two changes from another thread at wall times
    let lines = ldr_deck(&["C1 out 0 100n IC=0"], ".tran 2u 4m 0 2u uic");
    let run = Arc::new(LiveRun::start(&lines, &["v(out)".into()], &["v.xrl1.vstim".into()], Some(1e-4), Some(0.2)).unwrap());
    let ctl = run.clone();
    let t = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(5));
        ctl.set(&[("v.xrl1.vstim".into(), 300.0)]).unwrap();
        std::thread::sleep(Duration::from_millis(7));
        ctl.set(&[("v.xrl1.vstim".into(), 2.0)]).unwrap();
    });
    let live = collect(&run, "v(out)");
    t.join().unwrap();
    let log = run.changes();
    assert_eq!(log.len(), 2, "{:?}", log);
    assert!(log.iter().all(|c| (c.t / 1e-4 - (c.t / 1e-4).round()).abs() < 1e-6), "changes land on boundaries: {:?}", log);
    let rep = replay(&lines, &["v(out)".into()], &log).unwrap();
    let r = &rep.series["v(out)"];
    let skip: Vec<f64> = log.iter().map(|c| c.t).collect();
    let d = max_diff(&live, r, 4e-3, &skip);
    assert!(d < 2e-3, "live vs replay {}", d);
    // and the replay is deterministic
    let again = replay(&lines, &["v(out)".into()], &log).unwrap();
    assert_eq!(again.series["v(out)"], *r);
}

#[test]
fn pause_and_resume_keep_the_capacitor_continuous() {
    let _g = serial();
    let run = Arc::new(LiveRun::start(&rc_deck("5m"), &["v(out)".into()], &[], Some(2.5e-4), None).unwrap());
    let mut out = vec![];
    for _ in 0..4 {
        out.extend(run.next_chunk().unwrap().unwrap().series["v(out)"].clone());
    }
    run.pause();
    assert_eq!(run.state(), "paused");
    let ctl = run.clone();
    let t = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(60));
        ctl.resume();
    });
    let first = run.next_chunk().unwrap().unwrap(); // blocks until resumed
    t.join().unwrap();
    assert!(first.wall < 0.05, "paused time is not active time: {}", first.wall);
    let (tp, vp) = *out.last().unwrap();
    let (tn, vn) = first.series["v(out)"][0];
    let slope = 5.0 / 1e-3 * (-tp / 1e-3f64).exp();
    assert!((vn - vp).abs() <= 1.5 * slope * (tn - tp), "jump across the pause: {} -> {}", vp, vn);
    out.extend(first.series["v(out)"].clone());
    out.extend(collect(&run, "v(out)"));
    let err = out.iter().map(|(t, v)| (v - 5.0 * (1.0 - (-t / 1e-3f64).exp())).abs()).fold(0.0, f64::max);
    assert!(err < 0.02, "same as an uninterrupted RC charge: {}", err);
}

#[test]
fn cancel_mid_run_then_batch_works() {
    let _g = serial();
    let run = Arc::new(LiveRun::start(&rc_deck("5m"), &["v(out)".into()], &[], Some(1e-4), Some(0.05)).unwrap());
    let worker = {
        let r = run.clone();
        std::thread::spawn(move || {
            let mut n = 0;
            while r.next_chunk().unwrap().is_some() {
                n += 1;
            }
            n
        })
    };
    std::thread::sleep(Duration::from_millis(30));
    run.cancel();
    let n = worker.join().unwrap();
    assert!(n > 0 && n < 50, "cancelled mid-run after {} chunks", n);
    assert_eq!(run.state(), "cancelled");
    assert!(matches!(run.set(&[]), Err(SimError::Lifecycle { .. })));
    let out = batch(rc_deck("5m"), "v(out)").unwrap();
    assert!((out.last().unwrap().1 - 5.0 * (1.0 - (-5f64).exp())).abs() < 0.02);
    // close() without a worker releases the engine too
    let idle = LiveRun::start(&rc_deck("1m"), &["v(out)".into()], &[], None, None).unwrap();
    idle.close();
    assert!(batch(rc_deck("1m"), "v(out)").is_ok());
}

#[test]
fn batch_runs_are_refused_while_live() {
    let _g = serial();
    let mut sim = LiveSim::start(&rc_deck("2m"), &["v(out)".into()], &[]).unwrap();
    sim.advance(&[], 1e-3).unwrap();
    assert!(matches!(batch(rc_deck("1m"), "v(out)"), Err(SimError::Lifecycle { .. })));
    assert!(matches!(replay(&rc_deck("1m"), &["v(out)".into()], &[]), Err(SimError::Lifecycle { .. })));
    assert!(matches!(LiveSim::start(&rc_deck("1m"), &["v(out)".into()], &[]), Err(SimError::Lifecycle { .. })));
    // the live run itself is undisturbed
    let end = sim.advance(&[], 2e-3).unwrap();
    assert!(end.done);
    assert!(batch(rc_deck("1m"), "v(out)").is_ok());
}

#[test]
fn solver_error_mid_run_is_an_error_and_the_engine_recovers() {
    let _g = serial();
    let run = LiveRun::start(&ldr_deck(&[], ".tran 10u 2m 0 10u uic"), &["v(out)".into()], &["v.xrl1.vstim".into()],
                             Some(5e-4), None).unwrap();
    run.next_chunk().unwrap().unwrap();
    run.set(&[("v.xrl1.vstim".into(), 0.0)]).unwrap(); // lux 0: the LDR law divides by zero
    match run.next_chunk() {
        Err(SimError::SolverFailure { message, .. }) => assert!(message.contains("out of range"), "{}", message),
        other => panic!("expected a solver failure, got {:?}", other.map(|c| c.map(|c| c.t_end))),
    }
    assert_eq!(run.state(), "failed");
    assert!(run.next_chunk().unwrap().is_none());
    assert!(batch(rc_deck("1m"), "v(out)").is_ok());
    let mut again = LiveSim::start(&ldr_deck(&[], ".tran 10u 2m 0 10u uic"), &["v(out)".into()], &[]).unwrap();
    assert!(again.advance(&[], 2e-3).unwrap().done);
}

#[test]
fn poisoned_engine_during_a_live_run_fails_the_run_and_reloads() {
    let _g = serial();
    use benchcore::ngspice as ng;
    let mut sim = LiveSim::start(&rc_deck("2m"), &["v(out)".into()], &[]).unwrap();
    sim.advance(&[], 1e-3).unwrap();
    {
        // inject ngspice's controlled exit while the live run is paused (task 2's poison deck)
        let _op = ng::op_lock();
        let e = ng::do_circ(&deck(&["poison", "V1 a 0 DC 1", "R1 a 0 {nope}", ".tran 1u 20u 0 1u uic"])).unwrap_err();
        assert!(e.contains("reloaded"), "{}", e);
    }
    assert!(sim.advance(&[], 2e-3).is_err(), "a live run on a reloaded engine must fail, never continue silently");
    assert!(batch(rc_deck("1m"), "v(out)").is_ok());
    let run = LiveRun::start(&rc_deck("1m"), &["v(out)".into()], &[], None, None).unwrap();
    assert!(collect(&run, "v(out)").len() > 50);
}

#[test]
fn bad_live_devices_are_refused() {
    let _g = serial();
    let lines = ldr_deck(&["V2 p 0 PULSE(0 1 0 1u 1u 1m 2m)", "R2 p 0 1k"], ".tran 10u 2m 0 10u uic");
    let obs = ["v(out)".to_string()];
    for (dev, why) in [("v.xrl1.nope", "not in the circuit"), ("c1", "cannot be changed live"),
                       ("v2", "time function"), ("v1;quit", "cannot be changed live")] {
        match LiveSim::start(&lines, &obs, &[dev.into()]) {
            Err(SimError::InvalidConfig { message }) => assert!(message.contains(why), "{}: {}", dev, message),
            other => panic!("{}: {:?}", dev, other.map(|_| ())),
        }
    }
    // device quantities need their .save (an unsaved one read at a pause makes the next resume dump core)
    match LiveSim::start(&rc_deck("1m"), &["@r1[i]".into()], &[]) {
        Err(SimError::InvalidConfig { message }) => assert!(message.contains(".save"), "{}", message),
        other => panic!("{:?}", other.map(|_| ())),
    }
    let mut saved = rc_deck("1m");
    saved.insert(4, ".save all @r1[i]".into());
    let run = LiveRun::start(&saved, &["@r1[i]".into()], &[], Some(2e-4), None).unwrap();
    assert!(collect(&run, "@r1[i]").len() > 50);
    let run = LiveRun::start(&lines, &obs, &["v1".into()], None, None).unwrap();
    assert!(matches!(run.set(&[("r1".into(), 1.0)]), Err(SimError::InvalidConfig { .. })));
    assert!(matches!(run.set(&[("v1".into(), f64::NAN)]), Err(SimError::InvalidConfig { .. })));
    run.close();
    assert!(batch(rc_deck("1m"), "v(out)").is_ok());
}

#[test]
fn pacing_holds_the_requested_realtime_factor() {
    let _g = serial();
    // 20 ms of sim at 0.1x real time: 200 ms of wall time
    let run = LiveRun::start(&rc_deck("20m"), &["v(out)".into()], &[], None, Some(0.1)).unwrap();
    let t0 = std::time::Instant::now();
    let mut last = None;
    while let Some(c) = run.next_chunk().unwrap() {
        last = Some(c);
    }
    let c = last.unwrap();
    let wall = t0.elapsed().as_secs_f64();
    assert!((c.rtf / 0.1 - 1.0).abs() < 0.1, "achieved {} for 0.1", c.rtf);
    assert!((wall - 0.2).abs() < 0.04, "wall {}", wall);
    // unpaced runs report what they achieved too
    let fast = LiveRun::start(&rc_deck("20m"), &["v(out)".into()], &[], None, None).unwrap();
    let mut rtf = 0.0;
    while let Some(c) = fast.next_chunk().unwrap() {
        rtf = c.rtf;
    }
    assert!(rtf > 0.1, "unpaced is faster than 0.1x here: {}", rtf);
}

#[test]
fn two_live_parts_change_independently() {
    let _g = serial();
    // LDR divider plus a switch (R) feeding a second divider; both change, at different and at the same boundary
    let lines = ldr_deck(&["V2 s 0 DC 3", "RSW1 s q 1e9", "R3 q 0 1k"], ".tran 10u 4m 0 10u uic");
    let obs = vec!["v(out)".to_string(), "v(q)".to_string()];
    let mut sim = LiveSim::start(&lines, &obs, &["v.xrl1.vstim".into(), "rsw1".into(), "v2".into()]).unwrap();
    let a = sim.advance(&[], 1e-3).unwrap();
    let b = sim.advance(&[("v.xrl1.vstim".into(), 1000.0)], 2e-3).unwrap();
    let c = sim.advance(&[("rsw1".into(), 0.01)], 3e-3).unwrap();
    let d = sim.advance(&[("v.xrl1.vstim".into(), 10.0), ("v2".into(), 6.0)], 4e-3).unwrap();
    let last = |c: &benchcore::live::Chunk, n: &str| c.series[n].last().unwrap().1;
    let q = |vs: f64, r: f64| vs * 1e3 / (1e3 + r);
    for (chunk, lux, vq) in [(&a, 10.0, q(3.0, 1e9)), (&b, 1000.0, q(3.0, 1e9)), (&c, 1000.0, q(3.0, 0.01)),
                             (&d, 10.0, q(6.0, 0.01))] {
        assert!((last(chunk, "v(out)") - ldr_out(lux)).abs() < 1e-9);
        assert!((last(chunk, "v(q)") - vq).abs() < 1e-9, "{} vs {}", last(chunk, "v(q)"), vq);
    }
    assert_eq!(d.changes.len(), 2);
    assert!(d.changes.iter().all(|c| c.t == 3e-3));
}

#[test]
fn xspice_flip_flop_runs_live_with_the_same_edges() {
    let _g = serial();
    let lines = deck(&["x", "V1 clk 0 PULSE(0 5 10u 1u 1u 49u 100u)", "abr [clk] [dclk] adc1",
                       "aff qb dclk null null q qb dff1", "adac [q] [qa] dac1", "Rl qa 0 1k",
                       ".model adc1 adc_bridge(in_low=1 in_high=2)",
                       ".model dff1 d_dff(clk_delay=10n set_delay=10n reset_delay=10n)",
                       ".model dac1 dac_bridge(out_low=0 out_high=5 t_rise=1u t_fall=1u)", ".tran 1u 2m 0 1u"]);
    let edges = |s: &[(f64, f64)]| -> Vec<f64> {
        s.windows(2).filter(|w| (w[0].1 < 2.5) != (w[1].1 < 2.5)).map(|w| w[1].0).collect()
    };
    let b = edges(&batch(lines.clone(), "v(qa)").unwrap());
    let run = LiveRun::start(&lines, &["v(qa)".into()], &[], Some(2e-3 / 97.0), None).unwrap();
    let l = edges(&collect(&run, "v(qa)"));
    assert_eq!(b.len(), 20);
    assert_eq!(b.len(), l.len());
    let shift = b.iter().zip(&l).map(|(x, y)| (x - y).abs()).fold(0.0, f64::max);
    assert!(shift < 1e-6, "edges move by less than one 1 us step: {}", shift);
}

fn rss_kb() -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .unwrap()
        .lines()
        .find_map(|l| l.strip_prefix("VmRSS:"))
        .and_then(|v| v.split_whitespace().next()?.parse().ok())
        .unwrap()
}

/// Task 9.7: an indefinite live run is a huge tstop driven chunk by chunk. ngspice keeps one point per vector
/// (`.save none`, points streamed through SendData) and its per-command memory is released, so 30000 chunks
/// (300k points) leave RSS flat; every pause stays exact and a change lands exactly at its boundary.
#[test]
fn a_long_live_run_keeps_ngspice_memory_flat_and_changes_exact() {
    let _g = serial();
    let chunk = 1e-4;
    let run = LiveRun::start(&rc_deck("1e7"), &["v(out)".into()], &["v1".into()], Some(chunk), None).unwrap();
    let (mut base, mut prev, mut points) = (0, 0.0, 0usize);
    for k in 1..=30_000u64 {
        if k == 25_001 {
            run.set(&[("v1".into(), 0.0)]).unwrap(); // applied at the start of this chunk: t = 2.5 s
        }
        let c = run.next_chunk().unwrap().unwrap();
        assert_eq!(c.t_end, k as f64 * chunk, "the pause is exact at chunk {}", k);
        let s = &c.series["v(out)"];
        let end = s.last().unwrap().0; // the grid's last point is the pause, to float resolution
        assert!((k == 1 || s[0].0 > prev) && (end - c.t_end).abs() <= 1e-12 * c.t_end, "chunk {} ends at {}", k, end);
        if k == 25_001 {
            assert_eq!(c.changes, vec![Change { t: 2.5, device: "v1".into(), value: 0.0 }]);
            assert!((prev - 2.5f64).abs() < 1e-12 && s[0].0 > 2.5);
            for (t, v) in s {   // discharging from 5 V from exactly t = 2.5
                assert!((v - 5.0 * (-(t - 2.5) / 1e-3).exp()).abs() < 2e-3, "{} at {}", v, t);
            }
        }
        prev = c.t_end;
        points += s.len();
        if k == 2_000 {
            base = rss_kb();
        }
    }
    let grew = rss_kb() as i64 - base as i64;
    assert!(points > 290_000, "{} points", points);
    assert!(grew < 4_000, "ngspice memory grew by {} kB over 28000 chunks", grew);
    assert!(!matches!(run.state(), "done" | "failed"));
    run.cancel();
    assert!(run.next_chunk().unwrap().is_none());
    // `.save none` is sticky in libngspice: the engine was reloaded, so a batch run keeps all its points again
    assert!(batch(rc_deck("1m"), "v(out)").unwrap().len() > 100);
}
