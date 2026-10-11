//! Simulation correctness tests: real engine, physical assertions.
//!
//! Categories: correctness (analytic), pause/resume equivalence, seam events,
//! failure cases. Every test exercises libngspice through the public kernel
//! API. Tolerances are justified inline; see docs/testing.md.

use benchcore::kernel::{run_simulation, SimRequest};
use std::collections::HashMap;

fn rc_deck() -> Vec<String> {
    vec![
        "RC charge".to_string(),
        "V1 in 0 DC 5".to_string(),
        "R1 in out 1k".to_string(),
        "C1 out 0 1u IC=0".to_string(),
        ".tran 10u 5m 0 10u uic".to_string(),
        ".end".to_string(),
    ]
}

fn interp_at(t: &[f64], v: &[f64], tq: f64) -> f64 {
    assert!(t.len() == v.len() && t.len() >= 2);
    if tq <= t[0] {
        return v[0];
    }
    for i in 1..t.len() {
        if t[i] >= tq {
            let a = (tq - t[i - 1]) / (t[i] - t[i - 1]).max(1e-30);
            return v[i - 1] + a * (v[i] - v[i - 1]);
        }
    }
    *v.last().unwrap()
}

fn max_abs_diff(a: &[f64], b: &[f64]) -> f64 {
    a.iter()
        .zip(b.iter())
        .map(|(x, y)| (x - y).abs())
        .fold(0.0, f64::max)
}

/// T01 RC charging vs analytic exp. Tol 20mV full-trace: reltol=1e-3 on 5V FS
/// is a 5mV solver floor; LTE at TMAX=tau/100 accumulates to a few mV, so 20mV
/// is ~4x floor: catches TSTEP/TMAX swaps or missing UIC (volt-scale errors)
/// without asserting option defaults.
#[test]
fn t01_rc_charge_matches_exponential() {
    let req = SimRequest {
        netlist: rc_deck(),
        observables: vec!["v(out)".to_string()],
        stops: vec![],
    };
    let r = run_simulation(req).expect("rc sim must succeed");
    assert!(r.npts > 50, "too few points: {}", r.npts);
    assert!(!r.used_pauses);
    let tv: Vec<(f64, f64)> = r.series["v(out)"].clone();
    let t: Vec<f64> = tv.iter().map(|(x, _)| *x).collect();
    let v: Vec<f64> = tv.iter().map(|(_, y)| *y).collect();
    assert!(t.windows(2).all(|w| w[1] > w[0]), "time must be strictly increasing");
    assert!(v.iter().all(|x| x.is_finite()));
    let tau = 1e-3;
    let worst = t
        .iter()
        .zip(v.iter())
        .map(|(tt, vv)| (vv - 5.0 * (1.0 - (-tt / tau).exp())).abs())
        .fold(0.0, f64::max);
    assert!(worst < 0.020, "max analytic deviation {} exceeds 20mV", worst);
    let vfinal = interp_at(&t, &v, 5e-3);
    assert!(
        (vfinal - 4.96631).abs() < 0.005,
        "final {} vs 4.96631",
        vfinal
    );
}

/// T02 resistor divider + KCL. Tol 200uV = 200x vntol: Newton residual +
/// print rounding; 2nA = 2000x abstol. Asserting 1e-6V would test printf.
#[test]
fn t02_divider_dc_and_kcl() {
    let deck = vec![
        "divider".to_string(),
        "V1 in 0 DC 5".to_string(),
        "R1 in out 1k".to_string(),
        "R2 out 0 1k".to_string(),
        ".op".to_string(),
        ".end".to_string(),
    ];
    let req = SimRequest {
        netlist: deck,
        observables: vec!["v(out)".to_string()],
        stops: vec![],
    };
    let r = run_simulation(req).expect("divider .op must succeed");
    // .op has no time grid; observable read may be empty via tran path --
    // fall back to direct vector check through a tran-less run is covered by
    // the kernel returning empty series; instead verify via transient settle.
    // (ngspice .op vectors live on a different plot; kernel v1 targets tran.)
    let _ = r;
    // transient cross-check of the same divider ratio
    let deck2 = vec![
        "divider tran".to_string(),
        "V1 in 0 DC 5".to_string(),
        "R1 in out 1k".to_string(),
        "R2 out 0 1k".to_string(),
        ".tran 1u 1m 0 1u uic".to_string(),
        ".end".to_string(),
    ];
    let req2 = SimRequest {
        netlist: deck2,
        observables: vec!["v(out)".to_string(), "i(v1)".to_string()],
        stops: vec![],
    };
    let r2 = run_simulation(req2).expect("divider tran must succeed");
    let tv = &r2.series["v(out)"];
    let last = tv.last().unwrap().1;
    assert!(
        (last - 2.5).abs() < 200e-6,
        "divider out {} vs 2.5",
        last
    );
    // KCL: source current must equal the divider current 5V/2k = 2.5mA.
    // Tol 1uA = 1e6 x abstol: DC-precise, never printf-scale.
    let ilast = r2.series["i(v1)"].last().unwrap().1.abs();
    assert!(
        (ilast - 2.5e-3).abs() < 1e-6,
        "divider current {} vs 2.5mA",
        ilast
    );
}

/// T03 LC period within 1%: interp error alone is ~0.25% at TMAX/T=1/200, so
/// 1% catches unit slips (mH->H = 31x in f) and Gear-damping regressions.
#[test]
fn t03_lc_period() {
    let deck = vec![
        "lc".to_string(),
        "L1 out 0 1mH IC=0".to_string(),
        "C1 out 0 1uF IC=5".to_string(),
        ".tran 1u 2m 0 1u uic".to_string(),
        ".end".to_string(),
    ];
    let req = SimRequest {
        netlist: deck,
        observables: vec!["v(out)".to_string()],
        stops: vec![],
    };
    let r = run_simulation(req).expect("lc sim must succeed");
    let tv = &r.series["v(out)"];
    let t: Vec<f64> = tv.iter().map(|(x, _)| *x).collect();
    let v: Vec<f64> = tv.iter().map(|(_, y)| *y).collect();
    // rising 2.5V crossings
    let mut cross = vec![];
    for i in 1..v.len() {
        if v[i - 1] < 2.5 && v[i] >= 2.5 {
            let a = (2.5 - v[i - 1]) / (v[i] - v[i - 1]).max(1e-30);
            cross.push(t[i - 1] + a * (t[i] - t[i - 1]));
        }
    }
    assert!(cross.len() >= 3, "need >=3 crossings, got {}", cross.len());
    let periods: Vec<f64> = cross.windows(2).map(|w| w[1] - w[0]).collect();
    let f0 = 1.0 / (2.0 * std::f64::consts::PI * (1e-3f64 * 1e-6f64).sqrt());
    for p in &periods {
        let f = 1.0 / p;
        assert!(
            ((f - f0) / f0).abs() < 0.01,
            "f {} vs f0 {}",
            f,
            f0
        );
    }
}

/// T04 diode operating range (never an exact fake value): N=1.5/IS=1e-14 LED
/// root is ~1.035V; bounds +-130mV span TEMP 0-50C drift (~90mV) + solver mV.
#[test]
fn t04_diode_op_range() {
    let deck = vec![
        "diode".to_string(),
        "V1 in 0 DC 5".to_string(),
        "R1 in out 1k".to_string(),
        "D1 out 0 DLED".to_string(),
        ".model DLED D(IS=1e-14 N=1.5)".to_string(),
        ".tran 1u 1m 0 1u uic".to_string(),
        ".end".to_string(),
    ];
    let req = SimRequest {
        netlist: deck,
        observables: vec!["v(out)".to_string()],
        stops: vec![],
    };
    let r = run_simulation(req).expect("diode sim must succeed");
    let last = r.series["v(out)"].last().unwrap().1;
    assert!(
        (0.90..=1.15).contains(&last),
        "diode Vf {} outside [0.90, 1.15]",
        last
    );
}

/// T05 pause/resume identity (REPORT6 method): the honest reference is
/// bkpt-cont -- the same circuit run uninterrupted on the SAME breakpoint
/// grid (via ngSpice_SetBkpt) -- not the plain grid. stop-resume must equal
/// bkpt-cont index-wise at 1nV (state preserved); both differ from plain cont
/// only by breakpoint-forced points. A .ic-restart seam would be ~0.2mV.
#[test]
fn t05_pause_resume_identity() {
    use benchcore::ngspice as ng;
    let mk = |stops: Vec<f64>| SimRequest {
        netlist: rc_deck(),
        observables: vec!["v(out)".to_string()],
        stops,
    };
    // same-grid reference: uninterrupted run with solver breakpoint at 2.5ms
    let (bt, bv) = {
        let _op = ng::op_lock();
        ng::ensure_init().unwrap();
        ng::teardown();
        assert_eq!(ng::do_circ(&rc_deck()).unwrap(), 0);
        assert!(ng::set_bkpt(2.5e-3).unwrap());
        assert_eq!(ng::do_command("run").unwrap(), 0);
        let (raw, t) = ng::read_series(&["v(out)".to_string()]);
        let v = raw["v(out)"].clone();
        ng::teardown();
        (t, v)
    };
    let b = run_simulation(mk(vec![2.5e-3])).expect("paused run");
    assert!(b.used_pauses, "fallback: {:?}", b.fallback_reason);
    let pt: Vec<f64> = b.series["v(out)"].iter().map(|(x, _)| *x).collect();
    let pv: Vec<f64> = b.series["v(out)"].iter().map(|(_, y)| *y).collect();
    assert_eq!(pt.len(), bt.len(), "same grid required: {} vs {}", pt.len(), bt.len());
    let worst_t: f64 = pt.iter().zip(bt.iter()).map(|(a, c)| (a - c).abs()).fold(0.0, f64::max);
    let worst_v = max_abs_diff(&pv, &bv);
    assert!(worst_t < 1e-12, "grid time diff {}", worst_t);
    assert!(worst_v < 1e-9, "pause state diff {} exceeds 1nV", worst_v);
    // determinism: same paused request twice is bit-identical
    let b2 = run_simulation(mk(vec![2.5e-3])).expect("paused repeat");
    let pv2: Vec<f64> = b2.series["v(out)"].iter().map(|(_, y)| *y).collect();
    assert_eq!(pv.len(), pv2.len());
    assert!(max_abs_diff(&pv, &pv2) == 0.0, "paused runs must be bit-identical");
}

/// T06 three stops: same-grid bkpt-cont reference at all three stops.
/// stop-resume must equal it index-wise at 1nV.
#[test]
fn t06_multi_breakpoint() {
    use benchcore::ngspice as ng;
    let mk = |stops: Vec<f64>| SimRequest {
        netlist: rc_deck(),
        observables: vec!["v(out)".to_string()],
        stops,
    };
    let stops = vec![1e-3, 2.5e-3, 4e-3];
    let (bt, bv) = {
        let _op = ng::op_lock();
        ng::ensure_init().unwrap();
        ng::teardown();
        assert_eq!(ng::do_circ(&rc_deck()).unwrap(), 0);
        for st in &stops {
            assert!(ng::set_bkpt(*st).unwrap());
        }
        assert_eq!(ng::do_command("run").unwrap(), 0);
        let (raw, t) = ng::read_series(&["v(out)".to_string()]);
        let v = raw["v(out)"].clone();
        ng::teardown();
        (t, v)
    };
    let b = run_simulation(mk(stops)).expect("multi");
    assert!(b.used_pauses, "fallback: {:?}", b.fallback_reason);
    let pt: Vec<f64> = b.series["v(out)"].iter().map(|(x, _)| *x).collect();
    let pv: Vec<f64> = b.series["v(out)"].iter().map(|(_, y)| *y).collect();
    assert_eq!(pt.len(), bt.len(), "same grid required: {} vs {}", pt.len(), bt.len());
    assert!(max_abs_diff(&pv, &bv) < 1e-9, "multi-pause seam diff");
}

/// T07 event at seam: PWL knee exactly at stop. B-source edges would be
/// stepped over silently (B2 finding), so PWL is mandatory here.
#[test]
fn t07_event_at_seam() {
    let deck = vec![
        "seam".to_string(),
        "V1 in 0 PWL(0 0 2m 0 2.001m 5 5m 5)".to_string(),
        "R1 in out 1k".to_string(),
        "C1 out 0 1u IC=0".to_string(),
        ".tran 10u 5m 0 10u uic".to_string(),
        ".end".to_string(),
    ];
    let mk = |stops: Vec<f64>| SimRequest {
        netlist: deck.clone(),
        observables: vec!["v(out)".to_string(), "v(in)".to_string()],
        stops,
    };
    // placement first: the PWL knee must sit at 2.0005ms +-20us, else the
    // continuity check below could pass on a smoothed-over edge.
    let full = run_simulation(mk(vec![])).expect("seam full");
    let vin = &full.series["v(in)"];
    let tknee = vin.iter().find(|(_, y)| *y >= 2.5).map(|(x, _)| *x).unwrap();
    assert!((tknee - 2.0005e-3).abs() < 20e-6, "knee at {} vs 2.0005ms", tknee);
    let b = run_simulation(mk(vec![2e-3])).expect("seam paused");
    assert!(b.used_pauses, "fallback: {:?}", b.fallback_reason);
    // continuity across the seam on v(out)
    let v: Vec<(f64, f64)> = b.series["v(out)"].clone();
    // find sample nearest 2ms from each side
    let before = v.iter().filter(|(t, _)| *t <= 2e-3).last().unwrap().1;
    let after = v.iter().find(|(t, _)| *t >= 2e-3).unwrap().1;
    assert!(
        (after - before).abs() < 5e-3,
        "seam jump {} exceeds 5mV",
        (after - before).abs()
    );
}

/// T09 stale-plot guard: two different sims back-to-back must not leak grids.
#[test]
fn t09_sequential_isolation() {
    let rc = run_simulation(SimRequest {
        netlist: rc_deck(),
        observables: vec!["v(out)".to_string()],
        stops: vec![],
    })
    .expect("rc");
    let div = run_simulation(SimRequest {
        netlist: vec![
            "div".to_string(),
            "V1 in 0 DC 5".to_string(),
            "R1 in out 1k".to_string(),
            "R2 out 0 1k".to_string(),
            ".tran 1u 1m 0 1u uic".to_string(),
            ".end".to_string(),
        ],
        observables: vec!["v(out)".to_string()],
        stops: vec![],
    })
    .expect("div");
    assert!(rc.npts > 10 && div.npts > 10);
    let rc_last = rc.series["v(out)"].last().unwrap().1;
    let div_last = div.series["v(out)"].last().unwrap().1;
    assert!((rc_last - 4.966).abs() < 0.05, "rc last {}", rc_last);
    assert!((div_last - 2.5).abs() < 0.01, "div last {}", div_last);
    assert!(
        (rc_last - div_last).abs() > 1.0,
        "grids look shared/stale"
    );
}

/// T10-T14 failure cases: exact error codes, in order.
#[test]
fn t10_v_loop_rejected() {
    let r = run_simulation(SimRequest {
        netlist: vec![
            "vloop".to_string(),
            "V1 out 0 DC 5".to_string(),
            "V2 out 0 DC 4".to_string(),
            ".op".to_string(),
            ".end".to_string(),
        ],
        observables: vec![],
        stops: vec![],
    });
    match r {
        Err(benchcore::SimError::InvalidCircuit { code, .. }) => {
            assert!(code.contains("V-LOOP"), "code {}", code)
        }
        other => panic!("expected V-LOOP, got {:?}", other.map(|_| ())),
    }
}

#[test]
fn t11_no_ground_rejected() {
    let r = run_simulation(SimRequest {
        netlist: vec![
            "nognd".to_string(),
            "V1 n1 n2 DC 5".to_string(),
            "R1 n1 n2 1k".to_string(),
            ".op".to_string(),
            ".end".to_string(),
        ],
        observables: vec![],
        stops: vec![],
    });
    match r {
        Err(benchcore::SimError::InvalidCircuit { code, .. }) => {
            assert!(code.contains("NO-GROUND"), "code {}", code)
        }
        other => panic!("expected NO-GROUND, got {:?}", other.map(|_| ())),
    }
}

#[test]
fn t12_isrc_open_rejected() {
    let r = run_simulation(SimRequest {
        netlist: vec![
            "isrc".to_string(),
            "I1 0 out DC 1m".to_string(),
            ".op".to_string(),
            ".end".to_string(),
        ],
        observables: vec![],
        stops: vec![],
    });
    match r {
        Err(benchcore::SimError::InvalidCircuit { code, .. }) => {
            assert!(code.contains("ISRC-OPEN"), "code {}", code)
        }
        other => panic!("expected ISRC-OPEN, got {:?}", other.map(|_| ())),
    }
}

#[test]
fn t13_shorted_vsrc_rejected() {
    let r = run_simulation(SimRequest {
        netlist: vec![
            "short".to_string(),
            "V1 out out DC 5".to_string(),
            ".op".to_string(),
            ".end".to_string(),
        ],
        observables: vec![],
        stops: vec![],
    });
    match r {
        Err(benchcore::SimError::InvalidCircuit { code, .. }) => {
            assert!(code.contains("SHORTED-VSRC"), "code {}", code)
        }
        other => panic!("expected SHORTED-VSRC, got {:?}", other.map(|_| ())),
    }
}

#[test]
fn t14_bad_stops_fall_back_with_reason() {
    for (stops, reason) in [
        (vec![0.0], "stop_outside_interval"),
        (vec![6e-3], "stop_outside_interval"),
        (vec![2e-3, 2e-3], "stops_not_strictly_increasing"),
        (vec![3e-3, 2e-3], "stops_not_strictly_increasing"),
    ] {
        let r = run_simulation(SimRequest {
            netlist: rc_deck(),
            observables: vec!["v(out)".to_string()],
            stops,
        })
        .expect("fallback must still return a series");
        assert!(!r.used_pauses);
        assert_eq!(r.fallback_reason.as_deref(), Some(reason));
        // fallback series is still correct AND identical to a fresh full
        // run (fallback is the ordinary path, not a reconstruction).
        let last = r.series["v(out)"].last().unwrap().1;
        assert!((last - 4.96631).abs() < 0.01, "fallback last {}", last);
        let ref_ = run_simulation(SimRequest {
            netlist: rc_deck(),
            observables: vec!["v(out)".to_string()],
            stops: vec![],
        })
        .expect("reference full run");
        let a: Vec<f64> = r.series["v(out)"].iter().map(|(_, y)| *y).collect();
        let b2: Vec<f64> = ref_.series["v(out)"].iter().map(|(_, y)| *y).collect();
        assert_eq!(a.len(), b2.len());
        assert!(max_abs_diff(&a, &b2) == 0.0, "fallback must equal full run");
    }
}

/// Inductor state preserved across pause (flux ~ current continuity).
#[test]
fn t15_inductor_pause_resume() {
    let deck = vec![
        "rl".to_string(),
        "V1 in 0 DC 5".to_string(),
        "R1 in out 100".to_string(),
        "L1 out 0 10mH IC=0".to_string(),
        ".tran 10u 5m 0 10u uic".to_string(),
        ".end".to_string(),
    ];
    let mk = |stops: Vec<f64>| SimRequest {
        netlist: deck.clone(),
        observables: vec!["i(v1)".to_string()],
        stops,
    };
    let a = run_simulation(mk(vec![])).expect("full");
    let b = run_simulation(mk(vec![2.5e-3])).expect("paused");
    assert!(b.used_pauses, "fallback: {:?}", b.fallback_reason);
    let ta: Vec<f64> = a.series["i(v1)"].iter().map(|(x, _)| *x).collect();
    let va: Vec<f64> = a.series["i(v1)"].iter().map(|(_, y)| *y).collect();
    let tb: Vec<f64> = b.series["i(v1)"].iter().map(|(x, _)| *x).collect();
    let vb: Vec<f64> = b.series["i(v1)"].iter().map(|(_, y)| *y).collect();
    assert!(!va.is_empty() && !vb.is_empty(), "need branch current vectors");
    let peak = va.iter().map(|x| x.abs()).fold(0.0, f64::max).max(1e-12);
    let mut worst: f64 = 0.0;
    for (t, x) in ta.iter().zip(va.iter()) {
        if *t < tb[0] || *t > *tb.last().unwrap() {
            continue;
        }
        worst = worst.max((x - interp_at(&tb, &vb, *t)).abs());
    }
    let rel = worst / peak;
    assert!(rel < 1e-6, "inductor pause rel diff {}", rel);
    let _h: HashMap<String, ()> = HashMap::new();
}

/// F1 proof: 2us B-pulse on a 5ms window. The raw backend steps over this
/// silently (peak 0.0000, verified outside the harness); the kernel tightens
/// .tran TMAX to min_width/10 = 200ns and captures it. A 10us pulse would NOT
/// discriminate (raw LTE happens to land a point on it), so the width is
/// load-bearing: do not widen it without re-verifying the raw miss.
#[test]
fn t16_b_pulse_captured_by_tmax_policy() {
    let deck = vec![
        "bpulse".to_string(),
        "B1 mid 0 V=5*(u(TIME-2m)-u(TIME-2.002m))".to_string(),
        "R1 mid out 1k".to_string(),
        "C1 out 0 1u IC=0".to_string(),
        "V1 in 0 DC 0".to_string(),
        "R0 in 0 1k".to_string(),
        ".tran 10u 5m 0".to_string(),
        ".end".to_string(),
    ];
    let r = run_simulation(SimRequest {
        netlist: deck,
        observables: vec!["v(mid)".to_string()],
        stops: vec![],
    })
    .expect("b-pulse sim must succeed");
    let peak: f64 = r.series["v(mid)"].iter().map(|(_, y)| *y).fold(0.0, f64::max);
    assert!(peak >= 4.5, "B-pulse missed (peak {} < 4.5V): TMAX policy failed", peak);
}

/// Unknown TIME without explicit TMAX must be refused pre-sim, not silently
/// stepped over.
#[test]
fn t17_unknown_time_demands_bound() {
    let r = run_simulation(SimRequest {
        netlist: vec![
            "unk".to_string(),
            "B1 out 0 V=TIME*TIME/1e-6".to_string(),
            "R1 out 0 1k".to_string(),
            "V1 n0 0 DC 0".to_string(),
            "R0 n0 0 1k".to_string(),
            "V2 in 0 DC 0".to_string(),
            "R2 in 0 1k".to_string(),
            ".tran 10u 1m 0".to_string(),
            ".end".to_string(),
        ],
        observables: vec!["v(out)".to_string()],
        stops: vec![],
    });
    match r {
        Err(benchcore::SimError::InvalidConfig { .. }) => {}
        other => panic!("expected InvalidConfig for unknown TIME, got {:?}", other.map(|_| ())),
    }
}

/// B-SELFREF (voltage source reading its own output) is a verified
/// no-solution class: reject pre-sim.
#[test]
fn t18_b_selfref_rejected() {
    let r = run_simulation(SimRequest {
        netlist: vec![
            "selfref".to_string(),
            "V1 in 0 DC 5".to_string(),
            "R1 in out 1k".to_string(),
            "B1 out 0 V=V(out)+1".to_string(),
            ".op".to_string(),
            ".end".to_string(),
        ],
        observables: vec![],
        stops: vec![],
    });
    match r {
        Err(benchcore::SimError::InvalidCircuit { code, .. }) => {
            assert!(code.contains("B-SELFREF"), "code {}", code)
        }
        other => panic!("expected B-SELFREF, got {:?}", other.map(|_| ())),
    }
}

/// Loud solver failures are hard errors, never fallback series.
/// Unseeded cross-coupled B latch: validator warns B-CYCLE only (intent
/// ambiguous), ngspice aborts with "timestep too small".
#[test]
fn t19_loud_failure_is_error() {
    let r = run_simulation(SimRequest {
        netlist: vec![
            "latch".to_string(),
            "B1 o1 0 V=5*(1-u(V(o2)-2.5))".to_string(),
            "B2 o2 0 V=5*(1-u(V(o1)-2.5))".to_string(),
            "C1 o1 0 1n".to_string(),
            "C2 o2 0 1n".to_string(),
            ".tran 10n 10u 0 10n uic".to_string(),
            ".end".to_string(),
        ],
        observables: vec!["v(o1)".to_string()],
        stops: vec![],
    });
    match r {
        Err(benchcore::SimError::SolverFailure { .. })
        | Err(benchcore::SimError::ConvergenceFailure { .. }) => {}
        other => panic!("expected solver/convergence error, got {:?}", other.map(|_| ())),
    }
}

/// XSPICE/OSDI pause fence is fail-closed. This backend build has no XSPICE
/// code models ("Unknown model type", verified): with stops the fence must
/// divert to full simulation (never a native pause into unverified event
/// semantics), and the backend refusal must surface as Err -- never an
/// Ok series, empty or otherwise. A canned-Ok stub fails this test.
#[test]
fn t20_xspice_pause_fenced() {
    let deck = || {
        vec![
            "afence".to_string(),
            "V1 in 0 DC 5".to_string(),
            "R1 in out 1k".to_string(),
            "C1 out 0 1u IC=0".to_string(),
            "A1 out 0 mycode".to_string(),
            ".model mycode xspice()".to_string(),
            ".tran 10u 5m 0 10u uic".to_string(),
            ".end".to_string(),
        ]
    };
    match run_simulation(SimRequest {
        netlist: deck(),
        observables: vec!["v(out)".to_string()],
        stops: vec![2.5e-3],
    }) {
        Err(_) => {}
        Ok(r) => panic!("XSPICE deck with stops must not succeed (used_pauses={})", r.used_pauses),
    }
    match run_simulation(SimRequest {
        netlist: deck(),
        observables: vec!["v(out)".to_string()],
        stops: vec![],
    }) {
        Err(_) => {}
        Ok(_) => panic!("XSPICE deck must not succeed without stops either"),
    }
}

/// Non-finite stops are caller bugs -> InvalidConfig (not silent full run).
#[test]
fn t21_nonfinite_stops_rejected() {
    let r = run_simulation(SimRequest {
        netlist: rc_deck(),
        observables: vec!["v(out)".to_string()],
        stops: vec![f64::NAN],
    });
    match r {
        Err(benchcore::SimError::InvalidConfig { .. }) => {}
        other => panic!("expected InvalidConfig, got {:?}", other.map(|_| ())),
    }
}

/// T15 anchor: absolute RL physics -- final current ~V/R and tau=L/R from the
/// 10-90% rise, independent of any paused-vs-full comparison.
#[test]
fn t22_rl_absolute_physics() {
    let deck = vec![
        "rl".to_string(),
        "V1 in 0 DC 5".to_string(),
        "R1 in out 100".to_string(),
        "L1 out 0 10mH IC=0".to_string(),
        ".tran 10u 5m 0 10u uic".to_string(),
        ".end".to_string(),
    ];
    let r = run_simulation(SimRequest {
        netlist: deck,
        observables: vec!["i(v1)".to_string()],
        stops: vec![],
    })
    .expect("rl sim");
    let tv = &r.series["i(v1)"];
    let t: Vec<f64> = tv.iter().map(|(x, _)| *x).collect();
    let v: Vec<f64> = tv.iter().map(|(_, y)| *y).collect();
    let final_i = v.last().cloned().unwrap_or(0.0).abs();
    assert!((final_i - 0.05).abs() / 0.05 < 0.01, "final {} vs 50mA", final_i);
    // 10-90% rise of I(inf)*(1-exp(-t/tau)), tau = L/R = 100us
    let t10 = t.iter().zip(v.iter()).find(|(_, y)| y.abs() >= 0.1 * 0.05).map(|(x, _)| *x).unwrap();
    let t90 = t.iter().zip(v.iter()).find(|(_, y)| y.abs() >= 0.9 * 0.05).map(|(x, _)| *x).unwrap();
    let measured_tau = (t90 - t10) / 2.197;
    assert!((measured_tau - 100e-6).abs() / 100e-6 < 0.05, "tau {} vs 100us", measured_tau);
}

/// T07 placement: the PWL knee must appear in v(in) at the right time, and
/// post-knee v(out) must follow the analytic step response -- not merely be
/// continuous.
#[test]
fn t23_seam_event_placement() {
    let deck = vec![
        "seam".to_string(),
        "V1 in 0 PWL(0 0 2m 0 2.001m 5 5m 5)".to_string(),
        "R1 in out 1k".to_string(),
        "C1 out 0 1u IC=0".to_string(),
        ".tran 10u 5m 0 10u uic".to_string(),
        ".end".to_string(),
    ];
    let r = run_simulation(SimRequest {
        netlist: deck,
        observables: vec!["v(out)".to_string(), "v(in)".to_string()],
        stops: vec![],
    })
    .expect("seam full");
    let vin = &r.series["v(in)"];
    let t: Vec<f64> = vin.iter().map(|(x, _)| *x).collect();
    let vv: Vec<f64> = vin.iter().map(|(_, y)| *y).collect();
    // knee: v(in) crosses 2.5V within +-20us of 2.0005ms
    let tcross = t.iter().zip(vv.iter()).find(|(_, y)| **y >= 2.5).map(|(x, _)| *x).unwrap();
    assert!((tcross - 2.0005e-3).abs() < 20e-6, "knee at {} vs 2.0005ms", tcross);
    // post-knee trajectory: v(out) at 3ms vs analytic step from ~0V
    let vout = &r.series["v(out)"];
    let to: Vec<f64> = vout.iter().map(|(x, _)| *x).collect();
    let vo: Vec<f64> = vout.iter().map(|(_, y)| *y).collect();
    let got = interp_at(&to, &vo, 3e-3);
    let dt: f64 = 3e-3 - 2.001e-3;
    let expect = 5.0 * (1.0 - (-dt / 1e-3).exp());
    assert!((got - expect).abs() < 0.020, "post-knee {} vs {}", got, expect);
}

/// T24 unknown / unsaved observables are caller errors, not empty or
/// truncated series (a frontend probe on a deleted node must not plot
/// nothing silently). Checked on both the full and the pause path; `@r1[i]`
/// is unsaved in tran and previously came back as a 1-point series.
#[test]
fn t24_unknown_observable_is_error() {
    for (obs, stops) in [
        ("v(nonexistent)", vec![]),
        ("@r1[i]", vec![]),
        ("v(nonexistent)", vec![2.5e-3]),
    ] {
        let err = run_simulation(SimRequest {
            netlist: rc_deck(),
            observables: vec!["v(out)".to_string(), obs.to_string()],
            stops,
        })
        .expect_err("unknown observable must be rejected");
        match err {
            benchcore::SimError::InvalidConfig { message } => {
                assert!(message.contains(obs), "message must name {}: {}", obs, message)
            }
            other => panic!("expected InvalidConfig, got {:?}", other),
        }
    }
}

// ---- multi-terminal device validation (docs/plans/engine-design.md, task 4) ----

use benchcore::validate::validate;

const NM: &str = ".model NM NMOS(LEVEL=1 VTO=1 KP=1m)";
const Q22: &str = ".model q2n2222 NPN(IS=14.34f BF=255.9)";

fn findings(lines: &[&str]) -> Vec<benchcore::validate::Finding> {
    validate(&lines.iter().map(|s| s.to_string()).collect::<Vec<_>>())
}

fn has(f: &[benchcore::validate::Finding], code: &str) -> bool {
    f.iter().any(|x| x.code == code)
}

fn has_on(f: &[benchcore::validate::Finding], code: &str, node: &str) -> bool {
    f.iter().any(|x| x.code == code && x.nodes.iter().any(|n| n == node))
}

#[test]
fn t40_malformed_arity() {
    // 2-node Q: model name sits in node position
    let f = findings(&["t", "V1 c 0 5", "Q1 c 0 q2n2222", Q22]);
    assert!(has(&f, "MALFORMED"), "{:?}", f);
    assert!(!has_on(&f, "DANGLING", "q2n2222"), "{:?}", f);
    // MOSFET with 3 nodes: model in node position
    let f = findings(&["t", "V1 d 0 5", "Vg g 0 2", "M1 d g s NM W=2u L=1u", NM]);
    assert!(has(&f, "MALFORMED"), "{:?}", f);
    assert!(f.iter().any(|x| x.code == "MALFORMED" && x.message.contains("node position")), "{:?}", f);
    // J with 2 nodes
    assert!(has(&findings(&["t", "V1 d 0 5", "J1 d 0 jm", ".model jm NJF"]), "MALFORMED"));
    // X with too few nodes for the subckt
    let f = findings(&["t", "V1 n 0 5", "X1 n 0 0 foo", ".subckt foo a b", "R1 a b 1k", ".ends"]);
    assert!(has(&f, "MALFORMED"), "{:?}", f);
}

#[test]
fn t41_undefined_model() {
    let q = ["t", "V1 c 0 5", "Vb b 0 1", "Q1 c b 0 qnope"];
    assert!(has(&findings(&q), "UNDEFINED-MODEL"));
    // .include may supply the card: check skipped
    let mut inc = q.to_vec();
    inc.push(".include x.lib");
    assert!(!has(&findings(&inc), "UNDEFINED-MODEL"));
    let f = findings(&["t", "V1 a 0 5", "D1 a 0 dnope"]);
    assert!(has(&f, "UNDEFINED-MODEL"), "{:?}", f);
    // card after the device: order independent
    let f = findings(&["t", "V1 out 0 5", "R1 out a 1k", "D1 a 0 DLED", ".model DLED D(IS=1e-18)"]);
    assert!(!has(&f, "UNDEFINED-MODEL") && !has(&f, "MALFORMED"), "{:?}", f);
}

#[test]
fn t42_four_node_q_ok() {
    for q in ["Q1 c b 0 0 q2n2222", "Q1 c b 0 q2n2222 2"] {
        let f = findings(&["t", "V1 vcc 0 5", "Rc vcc c 1k", "Vb b 0 1", q, Q22]);
        assert!(f.is_empty(), "{}: {:?}", q, f);
    }
}

#[test]
fn t43_mos_floating_gate() {
    let base = ["t", "Vdd vdd 0 5", "Rd vdd d 10k", "M1 d g 0 0 NM", NM];
    let f = findings(&base);
    let g = f.iter().find(|x| x.code == "NO-DC-PATH").expect("NO-DC-PATH");
    assert_eq!(g.nodes, vec!["g".to_string()]);
    // a capacitor is no DC path
    let mut c = base.to_vec();
    c.push("C1 g 0 1p");
    assert!(has_on(&findings(&c), "NO-DC-PATH", "g"));
    // driven gate: clean
    let mut v = base.to_vec();
    v.push("Vg g 0 2");
    assert!(findings(&v).is_empty(), "{:?}", findings(&v));
    // floating bulk is not flagged
    let f = findings(&["t", "Vdd vdd 0 5", "Rd vdd d 10k", "Vg g 0 2", "M1 d g 0 bb NM", NM]);
    assert!(!has_on(&f, "NO-DC-PATH", "bb"), "{:?}", f);
}

#[test]
fn t44_bjt_floating_base_only_dangling() {
    let f = findings(&["t", "Vcc vcc 0 5", "Rc vcc c 1k", "Q1 c b 0 q2n2222", Q22]);
    assert!(!f.iter().any(|x| x.severity == "error"), "{:?}", f);
    assert!(has_on(&f, "DANGLING", "b"), "{:?}", f);
    assert!(!has(&f, "NO-DC-PATH"), "{:?}", f);
}

#[test]
fn t45_subckt_port_not_no_dc_path() {
    let f = findings(&[
        "t", "V1 a 0 5", "V2 b 0 1", "X1 a b c foo3", "C1 c 0 1n",
        ".subckt foo3 p q r", "R1 p q 1k", "R2 q r 1k", ".ends",
    ]);
    assert!(!has_on(&f, "NO-DC-PATH", "c"), "{:?}", f);
}

#[test]
fn t46_optional_q_m_nodes_and_spaced_params() {
    // ngspice 47 accepts all of these (verified with `ngspice -b`); no false errors
    for deck in [
        // VBIC with a thermal node: Q c b e s tj model
        &["t", "Vcc vcc 0 5", "Rc vcc c 1k", "Vb b 0 0.7", "Q1 c b 0 0 tj qv", ".model qv NPN LEVEL=4", "Rt tj 0 1k"][..],
        // SOI MOSFET with a 5th node
        &["t", "Vdd d 0 5", "Vg g 0 2", "M1 d g 0 0 0 soim", ".model soim NMOS LEVEL=10"][..],
        // a node that shares a model's name is still a node
        &["t", "Vcc nm 0 5", "Rc nm c 1k", "Vb b 0 0.7", "Q1 nm b 0 q2n2222", Q22, NM][..],
    ] {
        let f = findings(deck);
        assert!(!f.iter().any(|x| x.severity == "error"), "{:?}: {:?}", deck, f);
    }
    // spaced `key = value` on an X line is a param, not the subckt name
    let f = findings(&[
        "t", "V1 a 0 5", "X1 a 0 foo r = 2k", ".subckt foo p q params: r=1k", "R1 p q {r}", ".ends",
    ]);
    assert!(!has(&f, "DANGLING"), "{:?}", f);
}

#[test]
fn t47_model_local_to_subckt_is_undefined_at_top_level() {
    // ngspice 47 aborts this deck: the card exists only inside the subckt body
    let f = findings(&[
        "t", "Vcc vcc 0 5", "Rc vcc c 1k", "Vb b 0 0.7", "Q1 c b 0 qin",
        ".subckt s a b", ".model qin NPN", "R1 a b 1k", ".ends",
    ]);
    assert!(has(&f, "UNDEFINED-MODEL"), "{:?}", f);
}
