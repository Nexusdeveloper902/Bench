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

#[test]
fn t48_thermal_node_only_on_models_that_take_one() {
    let deck = |q: &str, model: &str| {
        findings(&["t", "Vcc vcc 0 5", "Rc vcc c 1k", "Vb b 0 0.7", "Rt tj 0 1k", q, model])
    };
    let bad = |f: &[benchcore::validate::Finding]| {
        f.iter().any(|x| x.code == "MALFORMED" && x.message.contains("at most 4 nodes"))
    };
    let q5 = "Q1 c b 0 0 tj m";
    // GP (default level, level 1, level 2): 5 nodes rejected by ngspice ("Too many nodes")
    for m in [".model m NPN(IS=1e-14)", ".model m NPN(LEVEL=1)", ".model m NPN LEVEL = 2", ".model m npn level=1"] {
        let f = deck(q5, m);
        assert!(bad(&f), "{}: {:?}", m, f);
        assert!(f.iter().any(|x| x.message.contains("'m'")), "message names the model: {:?}", f);
    }
    // GP with 4 nodes is fine
    assert!(!deck("Q1 c b 0 0 m", ".model m NPN(IS=1e-14)").iter().any(|x| x.severity == "error"));
    // VBIC (4, 9) and HICUM L2 (8): 5th node accepted; level spelled any way, inside or outside parens
    for m in [".model m NPN LEVEL=4", ".model m NPN(level = 4)", ".model m npn (is=1e-14 LEVEL = 8)", ".model m NPN\n+ LEVEL=9"] {
        let lines: Vec<&str> = m.split('\n').collect();
        let mut d = vec!["t", "Vcc vcc 0 5", "Rc vcc c 1k", "Vb b 0 0.7", "Rt tj 0 1k", q5];
        d.extend(lines);
        let f = findings(&d);
        assert!(!f.iter().any(|x| x.severity == "error"), "{}: {:?}", m, f);
    }
    // `+` continuation carrying the level, and the model card after the device (as above)
    let f = findings(&["t", "Vcc vcc 0 5", "Rc vcc c 1k", "Vb b 0 0.7", "Rt tj 0 1k", q5, ".model m NPN(IS=1e-14", "+ level = 1)"]);
    assert!(bad(&f), "{:?}", f);
    // model not defined in the deck (.include): stays permissive
    let f = findings(&["t", "Vcc vcc 0 5", "Rc vcc c 1k", "Vb b 0 0.7", "Rt tj 0 1k", q5, ".include x.lib"]);
    assert!(!f.iter().any(|x| x.severity == "error"), "{:?}", f);
    // MOSFET: non-SOI levels take 4 nodes (ngspice: "too many nodes connected to instance")
    let m5 = |model: &str| findings(&["t", "Vdd d 0 5", "Vg g 0 2", "M1 d g 0 0 x m", "Rx x 0 1", model]);
    assert!(bad(&m5(".model m NMOS(LEVEL=1)")));
    assert!(bad(&m5(".model m NMOS")));
    assert!(!m5(".model m NMOS LEVEL=10").iter().any(|x| x.severity == "error"));
}

// ---- engine robustness (docs/plans/ngspice-library/DESIGN.md 4.5) ----

fn tran(lines: &[&str], obs: &[&str]) -> Result<benchcore::SimResult, benchcore::SimError> {
    let mut deck: Vec<String> = lines.iter().map(|s| s.to_string()).collect();
    deck.push(".tran 1u 20u 0 1u uic".to_string());
    deck.push(".end".to_string());
    run_simulation(SimRequest {
        netlist: deck,
        observables: obs.iter().map(|s| s.to_string()).collect(),
        stops: vec![],
    })
}

fn divider_ok() {
    let r = tran(&["div", "V1 a 0 DC 4", "R1 a b 1k", "R2 b 0 3k"], &["v(b)"]).expect("valid deck after recovery");
    let last = r.series["v(b)"].last().unwrap().1;
    assert!((last - 3.0).abs() < 1e-6, "divider {} vs 3V", last);
}

/// L7: an undefined `{param}` makes ngspice call the exit callback and refuse
/// every later circuit. The run must fail as BackendFailure naming the reason,
/// and the next valid deck in the same process must succeed (library reloaded).
/// LAPLACE is the same class. Both go through the raw adapter: since task 4 the
/// validator stops both before the engine (UNDEFINED-PARAM, UNSUPPORTED).
#[test]
fn t60_poisoned_engine_recovers() {
    match tran(&["poison", "V1 a 0 DC 1", "R1 a 0 {nope}"], &["v(a)"]) {
        Err(benchcore::SimError::InvalidCircuit { code, message }) => {
            assert!(code == "UNDEFINED-PARAM" && message.contains("'nope'"), "{} {}", code, message)
        }
        other => panic!("the static gate must refuse an undefined param, got {:?}", other.map(|r| r.npts)),
    }
    let e = poison_raw(&["poison", "V1 a 0 DC 1", "R1 a 0 {nope}"]);
    assert!(e.to_lowercase().contains("nope"), "reason must be named: {}", e);
    divider_ok();
    divider_ok();
    poison_raw(&["lap", "V1 a 0 DC 1", "R1 a 0 1k", "E1 b 0 a 0 LAPLACE {V(a)} = {1/(1+s)}", "R2 b 0 1k"]);
    divider_ok();
}

/// Run a deck through the raw adapter (no validator) and return the abort message; it must say "reloaded".
fn poison_raw(lines: &[&str]) -> String {
    use benchcore::ngspice as ng;
    let _op = ng::op_lock();
    ng::ensure_init().unwrap();
    ng::teardown();
    let mut deck: Vec<String> = lines.iter().map(|s| s.to_string()).collect();
    deck.extend([".tran 1u 20u 0 1u uic".to_string(), ".end".to_string()]);
    let e = ng::do_circ(&deck).expect_err("the deck must abort ngspice");
    assert!(e.contains("reloaded"), "{}", e);
    ng::teardown();
    e
}

/// XSPICE code models are loaded again by the reload (spinit runs on init).
#[test]
fn t61_xspice_works_after_reload() {
    poison_raw(&["poison", "V1 a 0 DC 1", "R1 a 0 {nope2}"]);
    let r = tran(
        &[
            "inv", "V1 a 0 DC 3.3", "R1 a 0 1k",
            "A1 [a] [d] adc", ".model adc adc_bridge(in_low=1 in_high=2)",
            "A2 d o inv", ".model inv d_inverter",
            "A3 [o] [y] dac", ".model dac dac_bridge(out_low=0 out_high=3.3)", "R2 y 0 1k",
        ],
        &["v(y)"],
    )
    .expect("xspice after reload");
    let last = r.series["v(y)"].last().unwrap().1;
    assert!(last.abs() < 1e-3, "inverter of 3.3V must be ~0, got {}", last);
}

/// L6: ngspice prints these errors but returns rc 0 and an empty or stale
/// plot. Each must be an error, never Ok, and a valid run must follow.
#[test]
fn t62_silent_parse_failures_are_errors() {
    use benchcore::ngspice as ng;
    // through the raw adapter: the validator would reject some of these first
    for (deck, why) in [
        (vec!["us", "V1 a 0 1", "R1 a 0 1k", "X1 a 0 nosuch"], "unknown subckt"),
        (vec!["pm", "V1 a 0 1", "R1 a 0 1k", "X1 a 0 0 s", ".subckt s p q", "R1 p q 1k", ".ends"], "parameters for subcircuit"),
        (vec!["fn", "V1 a 0 1", "R1 a 0 1k", "R2 a"], "instance line, ignored"),
    ] {
        let mut lines: Vec<String> = deck.iter().map(|s| s.to_string()).collect();
        lines.push(".tran 1u 20u 0 1u uic".to_string());
        lines.push(".end".to_string());
        {
            let _op = ng::op_lock();
            ng::ensure_init().unwrap();
            ng::teardown();
            assert_eq!(ng::do_circ(&lines).unwrap(), 0, "{}: ngspice itself returns rc 0", why);
            let (log, _) = ng::take_logs();
            assert!(log.iter().any(|l| l.to_lowercase().contains(why)), "{}: {:?}", why, log);
            ng::teardown();
        }
        // and through the kernel (the validator must not be the only gate)
        let r = run_simulation(SimRequest { netlist: lines, observables: vec!["v(a)".to_string()], stops: vec![] });
        assert!(r.is_err(), "{} must fail, got {:?}", why, r.map(|r| r.npts));
    }
    divider_ok();
}

/// An unknown subckt used to return Ok with 0 points: now SolverFailure (or a
/// static rejection), on both the plain and the paused path.
#[test]
fn t63_unknown_subckt_is_err_on_both_paths() {
    for stops in [vec![], vec![5e-6]] {
        let r = run_simulation(SimRequest {
            netlist: ["us", "V1 a 0 1", "R1 a 0 1k", "X1 a 0 nosuch", ".tran 1u 20u 0 1u uic", ".end"]
                .iter().map(|s| s.to_string()).collect(),
            observables: vec!["v(a)".to_string()],
            stops,
        });
        assert!(r.is_err(), "got {:?}", r.map(|r| r.npts));
    }
}

// ---- device definition table (validate.rs DEVICES / LEVELS) ----

const T20: &str = ".tran 1u 20u 0 1u uic";

/// Every letter in the table: a valid deck passes the validator without errors AND runs through the
/// real engine to a hand-calculated value. Original letters (R C L D V I Q J M) double as regression
/// tests; the value proves the validator's node reading matches what ngspice actually built.
#[test]
fn t64_every_letter_runs_to_hand_value() {
    // (label, deck body, observable, expected, abs tol, use max |v| instead of the last value)
    let cases: Vec<(&str, Vec<&str>, &str, f64, f64, bool)> = vec![
        ("R divider", vec!["V1 a 0 DC 4", "R1 a b 1k", "R2 b 0 3k"], "v(b)", 3.0, 1e-6, false),
        ("C charged", vec!["V1 a 0 DC 1", "R1 a b 1", "C1 b 0 1n"], "v(b)", 1.0, 1e-6, false),
        ("L short at DC", vec!["V1 a 0 DC 1", "R1 a b 1k", "L1 b 0 1u"], "i(v1)", -1e-3, 1e-8, false),
        ("K transformer k=0.99", vec!["V1 a 0 SIN(0 1 100k)", "R0 a p 1", "L1 p 0 1m", "L2 c 0 1m", "R3 c 0 1meg", "K1 L1 L2 0.99"], "v(c)", 0.99, 0.06, true),
        ("V+I", vec!["I1 0 b DC 1m", "R2 b 0 1k"], "v(b)", 1.0, 1e-6, false),
        ("D Vf", vec!["V1 a 0 DC 5", "R1 a b 1k", "D1 b 0 dd", ".model dd D(IS=1e-14)"], "v(b)", 0.6929, 0.005, false),
        ("D level 3", vec!["V1 a 0 DC 5", "R1 a b 1k", "D1 b 0 dd", ".model dd D(IS=1e-14 level=3)"], "v(b)", 0.6929, 0.005, false),
        ("B 3*V(a)", vec!["V1 a 0 DC 2", "R1 a 0 1k", "B1 b 0 V=3*V(a)", "R2 b 0 1k"], "v(b)", 6.0, 1e-6, false),
        ("E linear", vec!["V1 a 0 DC 2", "R1 a 0 1k", "E1 b 0 a 0 3", "R2 b 0 1k"], "v(b)", 6.0, 1e-6, false),
        ("E gain=", vec!["V1 a 0 DC 2", "R1 a 0 1k", "E1 b 0 a 0 gain=3", "R2 b 0 1k"], "v(b)", 6.0, 1e-6, false),
        ("E VALUE", vec!["V1 a 0 DC 2", "R1 a 0 1k", "E1 b 0 VALUE={3*V(a)}", "R2 b 0 1k"], "v(b)", 6.0, 1e-6, false),
        ("E vol=", vec!["V1 a 0 DC 2", "R1 a 0 1k", "E1 b 0 vol='3*V(a)'", "R2 b 0 1k"], "v(b)", 6.0, 1e-6, false),
        ("E TABLE", vec!["V1 a 0 DC 2", "R1 a 0 1k", "E1 b 0 TABLE {V(a)} = (0,0) (4,12)", "R2 b 0 1k"], "v(b)", 6.0, 1e-6, false),
        ("E POLY(1)", vec!["V1 a 0 DC 2", "R1 a 0 1k", "E1 b 0 POLY(1) a 0 0 3", "R2 b 0 1k"], "v(b)", 6.0, 1e-6, false),
        ("G linear", vec!["V1 a 0 DC 2", "R1 a 0 1k", "G1 b 0 a 0 1m", "R2 b 0 1k"], "v(b)", -2.0, 1e-6, false),
        ("G cur=", vec!["V1 a 0 DC 2", "R1 a 0 1k", "G1 b 0 cur='1m*V(a)'", "R2 b 0 1k"], "v(b)", -2.0, 1e-6, false),
        ("F", vec!["V1 a 0 DC 2", "V2 c 0 0", "R3 a c 1k", "F1 b 0 V2 2", "R2 b 0 1k"], "v(b)", -4.0, 1e-6, false),
        ("H", vec!["V1 a 0 DC 2", "V2 c 0 0", "R3 a c 1k", "H1 b 0 V2 500", "R2 b 0 1k"], "v(b)", 1.0, 1e-6, false),
        ("H POLY(1)", vec!["V1 a 0 DC 2", "V2 c 0 0", "R3 a c 1k", "H1 b 0 POLY(1) V2 0 500", "R2 b 0 1k"], "v(b)", 1.0, 1e-6, false),
        ("S on", vec!["V1 a 0 DC 2", "V3 c 0 DC 2", "S1 a b c 0 sw", "R2 b 0 1k", ".model sw SW(vt=1 ron=1 roff=1meg)"], "v(b)", 2.0 * 1000.0 / 1001.0, 1e-6, false),
        ("S off", vec!["V1 a 0 DC 2", "V3 c 0 DC 0", "S1 a b c 0 sw", "R2 b 0 1k", ".model sw SW(vt=1 ron=1 roff=1meg)"], "v(b)", 2.0 * 1000.0 / 1_001_000.0, 1e-7, false),
        ("W on", vec!["V1 a 0 DC 2", "V2 c 0 0", "R3 a c 1k", "W1 a b V2 csw", "R2 b 0 1k", ".model csw CSW(it=1m ron=1 roff=1meg)"], "v(b)", 2.0 * 1000.0 / 1001.0, 1e-6, false),
        ("T matched", vec!["V1 a 0 DC 2", "T1 a 0 b 0 Z0=50 TD=1n", "R2 b 0 50"], "v(b)", 2.0, 1e-6, false),
        ("O lossless", vec!["V1 a 0 DC 2", "O1 a 0 b 0 lt", "R2 b 0 50", ".model lt LTRA(R=0 L=250n C=100p len=1)"], "v(b)", 2.0, 1e-6, false),
        ("U urc", vec!["V1 a 0 DC 2", "U1 a b 0 ur L=1m", "R2 b 0 1k", ".model ur URC(K=1.2 fmax=1G rperl=10 cperl=1p)"], "v(b)", 2.0, 1e-3, false),
        ("Y txl", vec!["V1 a 0 DC 2", "Y1 a 0 b 0 ty", "R2 b 0 50", ".model ty txl R=0.1 L=1n G=0 C=1p length=1"], "v(b)", 2.0, 0.02, false),
        ("P cpl", vec!["V1 a 0 DC 2", "P1 a 0 b 0 cp", "R2 b 0 50", ".model cp CPL R=0.1 L=1n G=0 C=1p length=1"], "v(b)", 2.0, 0.02, false),
        ("Q 2N2222 CE", vec!["Vcc vcc 0 DC 5", "Rb vcc b 430k", "Rc vcc c 1k", "Q1 c b 0 q2n2222", Q22], "v(c)", 5.0 - 255.9 * (5.0 - 0.65) / 430e3 * 1e3, 0.25, false),
        ("J NJF Idss", vec!["V1 a 0 DC 10", "R1 a d 5k", "J1 d 0 0 jm", ".model jm NJF(Beta=1e-4 Vto=-2)"], "v(d)", 8.0, 0.01, false),
        ("M level 1 sat", vec!["V1 a 0 DC 10", "Vg g 0 DC 3", "R1 a d 5k", "M1 d g 0 0 nm W=100u L=10u", ".model nm NMOS(level=1 Vto=1 Kp=2e-5)"], "v(d)", 8.0, 0.01, false),
        // VDMOS: Kp/2 (Vgs-Vto)^2 = 0.2 mA; its subthreshold smoothing leaves 0.2 % at Vov = 2 V (1 % tol)
        ("M VDMOS sat", vec!["V1 a 0 DC 100", "Vg g 0 DC 3", "R1 a d 10", "M1 d g 0 vd", ".model vd VDMOS(Vto=1 Kp=1e-4 lambda=0)", ".save all @m1[id]"], "@m1[id]", 2e-4, 2e-6, false),
        ("Z Statz sat", vec!["V1 a 0 DC 10", "R1 a d 1k", "Z1 d 0 0 mz", ".model mz NMF(level=1 vto=-2 beta=2.5m b=0.3 alpha=2 lambda=0)"], "v(d)", 10.0 - 2.5e-3 * 4.0 / 1.6 * 1e3, 1e-4, false),
        ("A inverter", vec![
            "V1 a 0 DC 0", "R1 a 0 1k", "A1 [a] [d] adc", ".model adc adc_bridge(in_low=1 in_high=2)",
            "A2 d o inv", ".model inv d_inverter", "A3 [o] [y] dac", ".model dac dac_bridge(out_low=0 out_high=3.3)", "R2 y 0 1k",
        ], "v(y)", 3.3, 1e-3, false),
        ("X", vec!["V1 a 0 DC 4", "X1 a b div", "R3 b 0 1meg", ".subckt div p q", "R1 p q 1k", "R2 q 0 3k", ".ends"], "v(b)", 3.0, 0.01, false),
    ];
    for (label, body, obs, want, tol, use_max) in cases {
        let mut deck = vec!["t"];
        deck.extend(body.iter());
        let f = findings(&deck);
        let errs: Vec<_> = f.iter().filter(|x| x.severity == "error").collect();
        assert!(errs.is_empty(), "{}: validator rejects a deck ngspice runs: {:?}", label, errs);
        let mut lines: Vec<String> = deck.iter().map(|s| s.to_string()).collect();
        lines.push(if label.starts_with('K') { ".tran 0.1u 20u 0 0.1u uic" } else { T20 }.to_string());
        lines.push(".end".to_string());
        let r = run_simulation(SimRequest { netlist: lines, observables: vec![obs.to_string()], stops: vec![] })
            .unwrap_or_else(|e| panic!("{}: {:?}", label, e));
        let s = &r.series[obs];
        let got = if use_max { s.iter().map(|(_, y)| y.abs()).fold(0.0, f64::max) } else { s.last().unwrap().1 };
        assert!((got - want).abs() <= tol, "{}: {} = {} vs {} (tol {})", label, obs, got, want, tol);
    }
}

/// Each letter's malformed and unsupported forms are refused statically with the right code.
#[test]
fn t65_malformed_and_unsupported_per_letter() {
    let base = ["t", "V1 a 0 DC 2", "R1 a 0 1k", "V2 c 0 0", "R3 a c 1k"];
    let cases: Vec<(&str, Vec<&str>, &str)> = vec![
        ("R no value", vec!["R2 a 0"], "MALFORMED"),
        ("C no value", vec!["C1 a"], "MALFORMED"),
        ("K no factor", vec!["L1 a 0 1m", "L2 a 0 1m", "K1 L1 L2"], "MALFORMED"),
        ("V one node", vec!["V9 a"], "MALFORMED"),
        ("D no model", vec!["D1 a 0"], "MALFORMED"),
        ("D undefined", vec!["D1 a 0 dz"], "UNDEFINED-MODEL"),
        ("D level 2", vec!["D1 a 0 dd", ".model dd D(level=2)"], "UNSUPPORTED"),
        ("D wrong type", vec!["D1 a 0 dd", ".model dd NPN"], "MALFORMED"),
        ("Q level 6 Mextram", vec!["Q1 a c 0 qm", ".model qm NPN(level=6)"], "UNSUPPORTED"),
        ("Q level 3", vec!["Q1 a c 0 qm", ".model qm NPN level=3"], "UNSUPPORTED"),
        ("Q LPNP", vec!["Q1 a c 0 qm", ".model qm LPNP"], "UNSUPPORTED"),
        ("Q diode model", vec!["Q1 a c 0 qm", ".model qm D"], "MALFORMED"),
        ("J level 3", vec!["J1 a 0 0 jm", ".model jm NJF(level=3)"], "UNSUPPORTED"),
        ("M level 7", vec!["M1 a c 0 0 nm", ".model nm NMOS(level=7)"], "UNSUPPORTED"),
        ("M level 60 aborts", vec!["M1 a c 0 0 0 0 nm", ".model nm NMOS(level=60)"], "UNSUPPORTED"),
        ("M NMOS 3 nodes", vec!["M1 a c 0 nm", ".model nm NMOS"], "MALFORMED"),
        ("M VDMOS 4 nodes", vec!["M1 a c 0 0 vd", ".model vd VDMOS"], "MALFORMED"),
        ("M VDMOS thermal 3 nodes", vec!["M1 a c 0 vd thermal", ".model vd VDMOS"], "MALFORMED"),
        ("Z level 7", vec!["Z1 a 0 0 mz", ".model mz NMF(level=7)"], "UNSUPPORTED"),
        ("Z JFET model", vec!["Z1 a 0 0 mz", ".model mz NJF"], "MALFORMED"),
        ("B if()", vec!["B1 b 0 V=if(V(a)>1, 1, 0)", "R2 b 0 1k"], "UNSUPPORTED"),
        ("B limit()", vec!["B1 b 0 V=limit(V(a), 0, 1)", "R2 b 0 1k"], "UNSUPPORTED"),
        ("R table()", vec!["R2 a 0 R={table(V(a), 0, 1, 1, 2)}"], "UNSUPPORTED"),
        ("E LAPLACE", vec!["E1 b 0 LAPLACE {V(a)} = {1/(1+s)}", "R2 b 0 1k"], "UNSUPPORTED"),
        ("E 3 nodes", vec!["E1 b 0 a 3", "R2 b 0 1k"], "MALFORMED"),
        ("E POLY(2) short", vec!["E1 b 0 POLY(2) a 0 0 3", "R2 b 0 1k"], "MALFORMED"),
        ("G no gain", vec!["G1 b 0 a 0", "R2 b 0 1k"], "MALFORMED"),
        ("F no gain", vec!["F1 b 0 V2", "R2 b 0 1k"], "MALFORMED"),
        ("S 3 nodes", vec!["S1 a b c sw", "R2 b 0 1k", ".model sw SW"], "MALFORMED"),
        ("S VSWITCH", vec!["S1 a b c 0 sw", "R2 b 0 1k", ".model sw VSWITCH(ron=1)"], "UNSUPPORTED"),
        ("W undefined", vec!["W1 a b V2 cz", "R2 b 0 1k"], "UNDEFINED-MODEL"),
        ("W SW model", vec!["W1 a b V2 sw", "R2 b 0 1k", ".model sw SW"], "MALFORMED"),
        ("T 3 nodes", vec!["T1 a 0 b Z0=50 TD=1n", "R2 b 0 50"], "MALFORMED"),
        ("U wrong model", vec!["U1 a b 0 ur", "R2 b 0 1k", ".model ur LTRA"], "MALFORMED"),
        ("A undefined", vec!["A1 [a] [d] nomodel"], "UNDEFINED-MODEL"),
        ("N OSDI", vec!["N1 a 0 vamod"], "UNSUPPORTED"),
    ];
    for (label, extra, code) in cases {
        let mut deck = base.to_vec();
        deck.extend(extra.iter());
        let f = findings(&deck);
        assert!(f.iter().any(|x| x.code == code && x.severity == "error"), "{}: want {}, got {:?}", label, code, f);
    }
}

/// DC edges come from the table: switch pairs conduct, control inputs do not; T ports conduct;
/// XSPICE digital nodes are not reported floating; nested .subckt bodies are depth-correct (L4).
#[test]
fn t66_dc_edges_and_nested_bodies() {
    // S: b reaches ground only through the switch pair -> fine; control node driven by a capacitor -> floating
    let f = findings(&["t", "V1 a 0 2", "S1 a b c 0 sw", "R2 b 0 1k", "C1 c 0 1n", ".model sw SW"]);
    assert!(!has_on(&f, "NO-DC-PATH", "b") && has_on(&f, "NO-DC-PATH", "c"), "{:?}", f);
    // G output is a current source: a node driven only by G is ISRC-OPEN
    let f = findings(&["t", "V1 a 0 2", "R1 a 0 1k", "G1 b 0 a 0 1m"]);
    assert!(has_on(&f, "ISRC-OPEN", "b"), "{:?}", f);
    // E output pair conducts, its control pair does not
    let f = findings(&["t", "V1 a 0 2", "R1 a 0 1k", "E1 b 0 c 0 2", "R2 b 0 1k", "C1 c 0 1n"]);
    assert!(!has_on(&f, "NO-DC-PATH", "b") && has_on(&f, "NO-DC-PATH", "c"), "{:?}", f);
    // T: the far port reaches ground through the line
    let f = findings(&["t", "V1 a 0 2", "T1 a 0 b 0 Z0=50 TD=1n", "C2 b 0 1p"]);
    assert!(!has(&f, "NO-DC-PATH"), "{:?}", f);
    assert!(f.iter().any(|x| x.code == "LIMITED-ANALYSIS" && x.severity == "warn" && x.devices == ["T1"]), "{:?}", f);
    // XSPICE: the digital node between two A devices is not a false NO-DC-PATH
    let f = findings(&[
        "t", "V1 a 0 0", "R1 a 0 1k", "A1 [a] [d] adc", "A2 d o inv", "A3 [o] [y] dac", "R2 y 0 1k",
        ".model adc adc_bridge", ".model inv d_inverter", ".model dac dac_bridge",
    ]);
    assert!(!f.iter().any(|x| x.severity == "error"), "{:?}", f);
    // %vd(a 0) port syntax: nodes a and 0, the model is the last token
    let f = findings(&["t", "V1 a 0 1", "R1 a 0 1k", "A1 %vd(a 0) %vd(b 0) g1", ".model g1 gain(gain=3)", "R2 b 0 1k"]);
    assert!(!f.iter().any(|x| x.severity == "error"), "{:?}", f);
    // L4: a nested .ends no longer exposes the rest of the outer body as top level. Since task 4 the body is
    // analysed per instance: its floating capacitor island is x1.zz / x1.yy (ngspice: singular matrix, probed)
    let f = findings(&[
        "t", "V1 a 0 1", "X1 a 0 outer",
        ".subckt outer p q", ".subckt inner c d", "R1 c d 1k", ".ends", "Cfloat zz yy 1n", "R9 p q 1k", ".ends",
    ]);
    assert!(!has_on(&f, "NO-DC-PATH", "zz") && !has_on(&f, "NO-DC-PATH", "yy"), "nested body leaked to top level: {:?}", f);
    let z = f.iter().find(|x| x.code == "NO-DC-PATH" && x.nodes == ["x1.zz"]).expect("internal island reported");
    assert_eq!((z.scope.as_str(), z.devices.as_slice()), ("x1", &["c.x1.cfloat".to_string()][..]));
}

/// t20 relies on the engine refusing an unknown XSPICE code model; with A accepted statically the
/// refusal must come from the engine and name the cause.
#[test]
fn t67_unknown_code_model_fails_in_engine() {
    let e = tran(&["a", "V1 a 0 DC 5", "R1 a 0 1k", "A1 a 0 mycode", ".model mycode xspice()"], &["v(a)"])
        .expect_err("unknown code model");
    let msg = format!("{:?}", e).to_lowercase();
    assert!(msg.contains("unknown model type") || msg.contains("not parsed") || msg.contains("error"), "{}", msg);
}

/// Task 3: the deck shape the app emits for subcircuit-backed packages (top-level X instances, definitions once
/// after the devices, `* bench-def` frames, a nested X whose body uses a top-level card, a subckt default and an
/// instance override) passes the static gate and runs to the divider values; internal nodes are per instance.
#[test]
fn t68_app_shaped_subckt_deck() {
    let deck = [
        "app", "V1 a 0 DC 10",
        "XU1 a b rp", "R1 b 0 1k",                    // RA default 1k: v(b) = 5
        "XU2 a c rp RA=3000.0", "R2 c 0 1k",          // override 3k: v(c) = 2.5
        "XU3 a d pair", "R3 d 0 1meg",                // two diodes in series, then 1 MΩ
        "* bench-def _models/x/dm.lib 0123abcd", ".model bench_dm D(Is=1e-14)",
        "* bench-def pkg/component.toml#body 89abcdef",
        ".subckt rp p q params: RA=1k", "R1 p m {RA/2}", "R2 m q {RA/2}", ".ends rp",
        ".subckt pair a k", "X1 a m half", "X2 m k half", ".ends pair",
        ".subckt half a k", "D1 a k bench_dm", ".ends half",
    ];
    let f = findings(&deck);
    assert!(!f.iter().any(|x| x.severity == "error"), "{:?}", f);
    let r = tran(&deck, &["v(b)", "v(c)", "v(d)", "v(xu1.m)", "v(xu2.m)", "v(xu3.m)"]).expect("app-shaped deck runs");
    let last = |k: &str| r.series[k].last().unwrap().1;
    assert!((last("v(b)") - 5.0).abs() < 1e-6 && (last("v(c)") - 2.5).abs() < 1e-6, "{} {}", last("v(b)"), last("v(c)"));
    assert!((last("v(xu1.m)") - 7.5).abs() < 1e-6 && (last("v(xu2.m)") - 6.25).abs() < 1e-6);
    let (vd, i) = (10.0 - last("v(d)"), last("v(d)") / 1e6);   // two identical junctions share the drop
    let vj = 0.025865 * (i / 1e-14 + 1.0).ln();                  // Shockley, Vt at 27 C
    assert!((vd / 2.0 - vj).abs() < 1e-3 && (last("v(xu3.m)") - (10.0 - vd / 2.0)).abs() < 1e-6, "{} {} {}", vd, vj, last("v(xu3.m)"));
}

// ---- subcircuit-aware validator (task 4, docs/plans/ngspice-library/DESIGN.md 4.6) ----

fn last(r: &benchcore::SimResult, k: &str) -> f64 {
    r.series[k].last().unwrap().1
}

/// Decks with nested, local and multi-instance subcircuits that the validator accepts (no error) simulate in
/// ngspice to hand values: acceptance is not a false negative on what ngspice runs.
#[test]
fn t69_accepted_subckt_decks_simulate() {
    let cases: Vec<(&str, Vec<&str>, &str, f64, f64)> = vec![
        // two instances of a definition whose body uses a .subckt local to it, which reads RT dynamically
        ("nested local def, override instance", vec![
            "V1 a 0 DC 4", "X1 a b outer", "X2 a c outer RT=3k", "R3 b 0 1k", "R4 c 0 1k",
            ".subckt outer p q params: RT=1k", "X1 p q half", ".subckt half r s", "R1 r s {RT}", ".ends", ".ends",
        ], "v(c)", 1.0, 1e-9),
        ("nested local def, default instance", vec![
            "V1 a 0 DC 4", "X1 a b outer", "X2 a c outer RT=3k", "R3 b 0 1k", "R4 c 0 1k",
            ".subckt outer p q params: RT=1k", "X1 p q half", ".subckt half r s", "R1 r s {RT}", ".ends", ".ends",
        ], "v(b)", 2.0, 1e-9),
        ("gnd inside the body is the only ground", vec!["V1 a b DC 1", "R2 a b 1k", "X1 b s", ".subckt s p", "R1 p gnd 1k", ".ends"], "v(a)", 1.0, 1e-9),
        ("differential input, no internal ground", vec![
            "V1 a 0 DC 1", "V2 c 0 DC 0.5", "X1 a c o amp", "R3 o 0 1k", ".subckt amp inp inn out", "Rin inp inn 1meg",
            "E1 out 0 inp inn 10", ".ends",
        ], "v(o)", 5.0, 1e-9),
        ("local .model shadows the global card", vec![
            "V1 a 0 DC 5", "R0 a b 1k", "X1 b s", ".model dl D(is=1e-20)", ".subckt s p", ".model dl D(is=1e-14)", "D1 p 0 dl", ".ends",
        ], "v(b)", 0.6929, 0.005),
        ("B I= conductance on an internal node", vec![
            "V1 a 0 DC 2", "X1 a b s", "R2 b 0 1k", ".subckt s p q", "B1 p m I=V(p,m)/1k", "R1 m q 1k", ".ends",
        ], "v(b)", 2.0 / 3.0, 1e-6),
        ("continuations and comments in a body", vec![
            "V1 a 0 DC 4", "X1 a b s RA=3k ; override", "R2 b 0 1k", ".subckt s p q", "+ params: RA=1k", "R1 p q $ series",
            "* comment between", "+ {RA} // value", ".ends",
        ], "v(b)", 1.0, 1e-9),
    ];
    for (label, body, obs, want, tol) in cases {
        let mut deck = vec!["t"];
        deck.extend(body.iter());
        let errs: Vec<_> = findings(&deck).into_iter().filter(|x| x.severity == "error").collect();
        assert!(errs.is_empty(), "{}: validator rejects a deck ngspice runs: {:?}", label, errs);
        let r = tran(&deck, &[obs]).unwrap_or_else(|e| panic!("{}: {:?}", label, e));
        assert!((last(&r, obs) - want).abs() <= tol, "{}: {} = {} vs {}", label, obs, last(&r, obs), want);
    }
    // a transformer whose windings share no galvanic path, each grounded through the ports outside
    let deck = [
        "t", "V1 a 0 SIN(0 1 100k)", "R0 a p 1", "X1 p 0 s1 0 xf", "R1 s1 0 1k",
        ".subckt xf p q s t", "L1 p q 1m", "L2 s t 1m", "K1 L1 L2 0.99", ".ends",
    ];
    assert!(!findings(&deck).iter().any(|x| x.severity == "error"), "{:?}", findings(&deck));
    let mut lines: Vec<String> = deck.iter().map(|s| s.to_string()).collect();
    lines.extend([".tran 0.1u 20u 0 0.1u uic".to_string(), ".end".to_string()]);
    let r = run_simulation(SimRequest { netlist: lines, observables: vec!["v(s1)".into()], stops: vec![] }).expect("transformer runs");
    let peak = r.series["v(s1)"].iter().map(|(_, y)| y.abs()).fold(0.0, f64::max);
    assert!((peak - 0.99).abs() < 0.06, "secondary peak {}", peak);
}

/// What ngspice does with a deck when nothing stops it: fails (an error line or an abort), or runs and computes
/// something other than what the deck says (silent).
enum Ngspice {
    Fails(&'static str),
    /// `got`: what ngspice computes; `written`: what the deck's text (one plausible reading of it) asks for.
    Silent { obs: &'static str, got: f64, written: f64 },
}

/// Run through the raw adapter (no validator) -> (last value of `obs`, ngspice's log and abort text, lower case).
fn raw(lines: &[&str], obs: &str) -> (Option<f64>, String) {
    use benchcore::ngspice as ng;
    let _op = ng::op_lock();
    ng::ensure_init().unwrap();
    ng::teardown();
    let mut deck: Vec<String> = lines.iter().map(|s| s.to_string()).collect();
    deck.extend([".tran 1u 20u 0 1u uic".to_string(), ".end".to_string()]);
    let mut text = String::new();
    match ng::do_circ(&deck).and_then(|_| ng::do_command("run")) {
        Err(e) => text.push_str(&e),
        Ok(_) => {}
    }
    let (series, _) = ng::read_series(&[obs.to_string()]);
    let (log, _) = ng::take_logs();
    text.push_str(&log.join("\n"));
    ng::teardown();
    (series.get(obs).and_then(|v| v.last().copied()), text.to_lowercase())
}

/// Every deck the validator rejects is one ngspice misbehaves on, run without the validator. Not run here:
/// K coupling a resistor, which the validator rejects as MALFORMED because ngspice 47 segfaults on it
/// (probed in a subprocess, exit -11).
#[test]
fn t70_rejected_decks_are_ones_ngspice_misbehaves_on() {
    let d = ["V1 a 0 DC 4", "R0 a b 1k"];
    let cases: Vec<(&str, Vec<&str>, &str, Ngspice)> = vec![
        ("undefined subckt", vec!["X1 b nosuch", "R3 b 0 3k"], "UNDEFINED-SUBCKT", Ngspice::Fails("unknown subckt")),
        ("model from a nested definition", vec!["X1 b outer", ".subckt outer p", "D1 p 0 dl", ".subckt inner q", ".model dl D", "R1 q 0 1k", ".ends", ".ends"],
         "UNDEFINED-MODEL", Ngspice::Fails("could not find a valid modelname")),
        ("undefined param in a body", vec!["X1 b s", ".subckt s p", "R1 p 0 {nope}", ".ends"], "UNDEFINED-PARAM", Ngspice::Fails("undefined parameter")),
        ("unknown function in a body", vec!["X1 b s", ".subckt s p", "R1 p 0 1k", "B1 p 0 I=foo(V(p))", ".ends"], "UNDEFINED-PARAM", Ngspice::Fails("no such function")),
        ("recursion", vec!["X1 b s", ".subckt s p", "X2 p s", "R1 p 0 1k", ".ends"], "SUBCKT-RECURSION", Ngspice::Fails("unknown subckt")),
        ("K names a missing inductor", vec!["X1 b s", ".subckt s p", "L1 p 0 1m", "K1 L1 L9 0.9", ".ends"], "UNDEFINED-ELEMENT", Ngspice::Fails("non-existent inductor")),
        ("F controlled by a top-level source", vec!["R3 b 0 3k", "X1 b s", ".subckt s p", "F1 p 0 V1 0.001", ".ends"], "UNDEFINED-ELEMENT", Ngspice::Fails("unknown controlling source")),
        ("duplicate element in a body", vec!["X1 b s", ".subckt s p", "R1 p 0 1k", "R1 p 0 2k", ".ends"], "DUP-NAME", Ngspice::Fails("error")),
        ("missing .ends", vec!["X1 b s", ".subckt s p", "R1 p 0 1k"], "SUBCKT-UNBALANCED", Ngspice::Fails("ends")),
        ("isolated winding inside", vec!["R3 b 0 3k", "X1 b xf", ".subckt xf p", "L1 p 0 1m", "L2 m n 1m", "R1 m n 1k", "K1 L1 L2 0.99", ".ends"],
         "NO-DC-PATH", Ngspice::Fails("singular matrix")),
        ("floating capacitor island inside", vec!["R3 b 0 3k", "X1 b s", ".subckt s p", "R1 p 0 1k", "C1 m n 1n", ".ends"], "NO-DC-PATH", Ngspice::Fails("singular matrix")),
        // silent: ngspice runs and computes something else than the text says
        ("unknown instance param (RB for RA)", vec!["X1 b s RB=3k", "R3 b 0 3k", ".subckt s p params: RA=1k", "R1 p 0 {RA}", ".ends"],
         "UNKNOWN-INSTANCE-PARAM", Ngspice::Silent { obs: "v(b)", got: 4.0 * 750.0 / 1750.0, written: 4.0 * 1500.0 / 2500.0 }),
        ("port named 0", vec!["X1 b q s", "R3 q 0 1k", ".subckt s p 0", "R1 p 0 1k", ".ends"],
         "MALFORMED", Ngspice::Silent { obs: "v(q)", got: 0.0, written: 4.0 * 1e3 / 3e3 }),
        ("duplicate port (b and q both on p)", vec!["X1 b q s", "R3 q 0 1k", ".subckt s p p", "R1 p 0 1k", ".ends"],
         "DUP-NAME", Ngspice::Silent { obs: "v(b)", got: 2.0, written: 4.0 * 500.0 / 1500.0 }),
        ("redefined .subckt", vec!["X1 b s", ".subckt s p", "R1 p 0 1k", ".ends", ".subckt s p", "R1 p 0 3k", ".ends"],
         "DUP-SUBCKT", Ngspice::Silent { obs: "v(b)", got: 2.0, written: 3.0 }),          // the first body wins
        ("redefined .model", vec!["D1 b 0 dd", ".model dd D(is=1e-14)", ".model dd D(is=1e-20)"],
         "DUP-MODEL", Ngspice::Silent { obs: "v(b)", got: 0.6929, written: 1.02 }),       // the first card wins
        ("parameter declared twice", vec!["X1 b s", ".subckt s p params: r=1k r=3k", "R1 p 0 {r}", ".ends"],
         "DUP-NAME", Ngspice::Silent { obs: "v(b)", got: 3.0, written: 2.0 }),            // the last one wins
    ];
    for (label, body, code, ng) in cases {
        let mut deck = vec!["t"];
        deck.extend(d.iter());
        deck.extend(body.iter());
        let f = findings(&deck);
        assert!(f.iter().any(|x| x.code == code && x.severity == "error"), "{}: want {}, got {:?}", label, code, f);
        let (val, log) = raw(&deck, match ng { Ngspice::Silent { obs, .. } => obs, _ => "v(b)" });
        match ng {
            Ngspice::Fails(why) => assert!(log.contains(why), "{}: ngspice did not fail with '{}': {:?} {}", label, why, val, log),
            Ngspice::Silent { obs, got, written } => {
                let v = val.unwrap_or_else(|| panic!("{}: ngspice did not run: {}", label, log));
                assert!(!log.contains("error"), "{}: ngspice reported it after all: {}", label, log);
                assert!((v - got).abs() < 0.01, "{}: {} = {}, expected ngspice's {}", label, obs, v, got);
                assert!((got - written).abs() > 0.1, "{}: not a silent misreading", label);
            }
        }
    }
    divider_ok();
}
