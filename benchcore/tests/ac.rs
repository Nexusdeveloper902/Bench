//! AC small-signal analysis (G17): real engine, analytic answers.

use benchcore::kernel::{run_ac, run_simulation, SimRequest};
use benchcore::SimError;
use std::f64::consts::PI;

fn deck(lines: &[&str], ac: &str) -> Vec<String> {
    let mut d: Vec<String> = lines.iter().map(|s| s.to_string()).collect();
    d.push(ac.to_string());
    d.push(".end".to_string());
    d
}

fn obs(names: &[&str]) -> Vec<String> {
    names.iter().map(|s| s.to_string()).collect()
}

fn mag(z: (f64, f64)) -> f64 {
    z.0.hypot(z.1)
}

fn deg(z: (f64, f64)) -> f64 {
    z.1.atan2(z.0).to_degrees()
}

const RC: [&str; 4] = ["rc lowpass", "V1 in 0 DC 0 AC 1", "R1 in out 1k", "C1 out 0 1u"];

/// RC low-pass, 1 kΩ / 1 µF: H = 1 / (1 + j f/fc), fc = 1/(2πRC) = 159.155 Hz. ngspice solves the linear
/// small-signal system directly, so the match is to rounding (1e-9), over five decades.
#[test]
fn ac01_rc_lowpass_matches_transfer_function() {
    let r = run_ac(&deck(&RC, ".ac dec 20 1 100k"), &obs(&["v(out)", "v(in)", "i(v1)"])).expect("rc ac");
    let fc = 1.0 / (2.0 * PI * 1e3 * 1e-6);
    assert_eq!(r.frequency.len(), 101, "dec 20 over 5 decades, both ends included");
    assert!((r.frequency[0] - 1.0).abs() < 1e-12 && (r.frequency[100] - 1e5).abs() < 1e-6);
    for (f, z) in r.frequency.iter().zip(&r.series["v(out)"]) {
        let x = f / fc;
        let (m, p) = (1.0 / (1.0 + x * x).sqrt(), -x.atan().to_degrees());
        assert!((mag(*z) - m).abs() < 1e-9 * m.max(1e-3), "|H({})| {} vs {}", f, mag(*z), m);
        assert!((deg(*z) - p).abs() < 1e-6, "arg H({}) {} vs {}", f, deg(*z), p);
    }
    assert!(r.series["v(in)"].iter().all(|z| (z.0 - 1.0).abs() < 1e-12 && z.1.abs() < 1e-12), "AC 1 at 0°");
}

/// The -3 dB point: at f = fc, |H| = 1/√2 (-3.0103 dB) and the phase is -45°.
#[test]
fn ac02_rc_minus_3db_point_and_phase() {
    let fc = 1.0 / (2.0 * PI * 1e3 * 1e-6);
    let r = run_ac(&deck(&RC, &format!(".ac lin 1 {:.17e} {:.17e}", fc, fc)), &obs(&["v(out)"])).expect("rc at fc");
    let z = r.series["v(out)"][0];
    let db = 20.0 * mag(z).log10();
    assert!((db + 10.0 * 2f64.log10()).abs() < 1e-9, "{} dB", db);
    assert!((deg(z) + 45.0).abs() < 1e-7, "{}°", deg(z));
}

/// Series RLC (1 mH, 1 µF, 10 Ω), output across R: unity gain at f0 = 1/(2π√LC) = 5032.9 Hz, phase 0, and the
/// source current is -1/R there (ngspice's i(v1) flows into the + terminal). Q = √(L/C)/R = 3.16 sets the -3 dB
/// bandwidth f0/Q.
#[test]
fn ac03_rlc_resonance() {
    let (l, c, rr): (f64, f64, f64) = (1e-3, 1e-6, 10.0);
    let f0 = 1.0 / (2.0 * PI * (l * c).sqrt());
    let lines = ["rlc", "V1 in 0 DC 0 AC 1", "L1 in a 1m", "C1 a out 1u", "R1 out 0 10"];
    let r = run_ac(&deck(&lines, &format!(".ac lin 1 {:.17e} {:.17e}", f0, f0)), &obs(&["v(out)", "i(v1)"]))
        .expect("rlc at f0");
    let (v, i) = (r.series["v(out)"][0], r.series["i(v1)"][0]);
    assert!((mag(v) - 1.0).abs() < 1e-9 && deg(v).abs() < 1e-6, "|H(f0)| {} ∠{}", mag(v), deg(v));
    assert!((i.0 + 1.0 / rr).abs() < 1e-9 && i.1.abs() < 1e-9, "i(v1) {:?}", i);
    // the sweep peaks at f0 and is -3 dB at the band edges f0 (√(1+1/4Q²) ± 1/2Q)
    let sweep = run_ac(&deck(&lines, ".ac dec 400 100 1meg"), &obs(&["v(out)"])).expect("rlc sweep");
    let (k, _) = sweep.series["v(out)"].iter().enumerate().fold((0, 0.0), |b, (k, z)| if mag(*z) > b.1 { (k, mag(*z)) } else { b });
    assert!((sweep.frequency[k] / f0 - 1.0).abs() < 0.003, "peak at {} vs {}", sweep.frequency[k], f0);
    let q = (l / c).sqrt() / rr;
    let s = (1.0 + 1.0 / (4.0 * q * q)).sqrt();
    for fe in [f0 * (s - 1.0 / (2.0 * q)), f0 * (s + 1.0 / (2.0 * q))] {
        let e = run_ac(&deck(&lines, &format!(".ac lin 1 {:.17e} {:.17e}", fe, fe)), &obs(&["v(out)"])).unwrap();
        assert!((mag(e.series["v(out)"][0]) - 0.5f64.sqrt()).abs() < 1e-9, "band edge {}", fe);
    }
}

/// AC magnitude and phase on a current source: `AC 2m 90` into 1 kΩ is 2 V at +90°, at every frequency.
#[test]
fn ac04_current_source_magnitude_and_phase() {
    let r = run_ac(&deck(&["isrc", "I1 0 a DC 0 AC 2m 90", "R1 a 0 1k"], ".ac oct 2 100 1600"), &obs(&["v(a)"])).unwrap();
    assert_eq!(r.frequency.len(), 9);
    for z in &r.series["v(a)"] {
        assert!((mag(*z) - 2.0).abs() < 1e-9 && (deg(*z) - 90.0).abs() < 1e-7, "{:?}", z);
    }
}

fn config_err(r: Result<benchcore::AcResult, SimError>, needle: &str) {
    match r {
        Err(SimError::InvalidConfig { message }) => assert!(message.contains(needle), "{}", message),
        other => panic!("expected InvalidConfig containing {:?}, got {:?}", needle, other.map(|r| r.frequency.len())),
    }
}

/// Bad sweeps are refused before ngspice (which runs `.ac dec 10 0 1k` silently, producing no ac plot), and so
/// are device `@` vectors (operating-point values in an ac plot), unknown nodes and a second analysis.
#[test]
fn ac05_refusals() {
    let v = obs(&["v(out)"]);
    config_err(run_ac(&deck(&RC, ".ac dec 10 0 1k"), &v), "0 < fstart");
    config_err(run_ac(&deck(&RC, ".ac dec 10 1k 10"), &v), "0 < fstart");
    config_err(run_ac(&deck(&RC, ".ac dec 2.5 1 1k"), &v), "whole number");
    config_err(run_ac(&deck(&RC, ".ac log 10 1 1k"), &v), "dec, oct or lin");
    config_err(run_ac(&deck(&RC, ".ac lin 2 100 200"), &v), "use 3 or more");
    assert_eq!(run_ac(&deck(&RC, ".ac lin 3 100 300"), &v).unwrap().frequency, vec![100.0, 200.0, 300.0]);
    config_err(run_ac(&deck(&RC, ".ac dec 10 1"), &v), "bad .ac card");
    config_err(run_ac(&deck(&RC, ".ac dec 100000 1 1e300"), &v), "too many points");
    config_err(run_ac(&deck(&[&RC[..], &[".tran 1u 1m"]].concat(), ".ac dec 10 1 1k"), &v), "exactly one .ac");
    config_err(run_ac(&deck(&RC, ".tran 1u 1m"), &v), "exactly one .ac");
    config_err(run_ac(&deck(&RC, ".ac dec 10 1 1k"), &obs(&["@r1[i]"])), "operating-point");
    config_err(run_ac(&deck(&RC, ".ac dec 10 1 1k"), &obs(&["v(nosuch)"])), "v(nosuch)");
    let req = SimRequest { netlist: deck(&RC, ".ac dec 10 1 1k"), observables: v.clone(), stops: vec![] };
    match run_simulation(req) {
        Err(SimError::InvalidConfig { message }) => assert!(message.contains("run_ac"), "{}", message),
        other => panic!("run_simulation must refuse an .ac deck, got {:?}", other.map(|r| r.npts)),
    }
    // no `uic` in AC: the operating point needs a DC path, so the static gate refuses a capacitor-only node
    match run_ac(&deck(&["float", "V1 in 0 DC 0 AC 1", "C1 in a 1u", "C2 a 0 1u"], ".ac dec 10 1 1k"), &obs(&["v(a)"])) {
        Err(SimError::InvalidCircuit { code, .. }) => assert!(code.contains("NO-DC-PATH"), "{}", code),
        other => panic!("floating node must be refused, got {:?}", other.map(|r| r.frequency.len())),
    }
}

/// A run after an aborted (poisoned) engine reloads the library and succeeds, as transient runs do (L7).
#[test]
fn ac06_runs_after_engine_reload() {
    use benchcore::ngspice as ng;
    {
        let _op = ng::op_lock();
        ng::ensure_init().unwrap();
        ng::teardown();
        let poison = deck(&["poison", "V1 a 0 DC 1 AC 1", "R1 a 0 {nope_ac}"], ".ac dec 10 1 1k");
        assert!(ng::do_circ(&poison).expect_err("the deck must abort ngspice").contains("reloaded"));
    }
    let r = run_ac(&deck(&RC, ".ac dec 10 1 1k"), &obs(&["v(out)"])).expect("ac after reload");
    assert_eq!(r.frequency.len(), 31);
}
