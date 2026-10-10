//! Gate-level stress test: 4-bit ripple-carry adder, exhaustive vectors.
//!
//! 60 NAND gates (B-behavioral) + 9 PWL input sources, 512 input
//! combinations in ONE transient run, every sum/carry bit checked against
//! integer arithmetic. Plus a transistor-level (MOS) NAND truth table.
//! This stresses what the core claims: deep combinational chains, long carry
//! propagation, hundreds of synchronized PWL events, validator silence on a
//! large DAG, and pause-free throughput on a dense netlist.

use simcore::kernel::{run_simulation, SimRequest};

const VDD: f64 = 5.0;
const TH: f64 = 2.5; // logic threshold
const SLOT: f64 = 1e-6; // 1us per combination
const RISE: f64 = 10e-9; // PWL edge width

struct Net {
    lines: Vec<String>,
    gate_count: usize,
    node_seq: usize,
}

impl Net {
    fn new() -> Self {
        Net {
            lines: vec![],
            gate_count: 0,
            node_seq: 0,
        }
    }
    fn node(&mut self, prefix: &str) -> String {
        let n = format!("{}{}", prefix, self.node_seq);
        self.node_seq += 1;
        n
    }
    /// NAND2 primitive: y = !(a & b). Inputs tied together make an inverter.
    fn nand2(&mut self, a: &str, b: &str) -> String {
        let y = self.node("n");
        self.lines.push(format!(
            "B{} {} 0 V=((V({})>{})&&(V({})>{})?0:{})",
            self.node_seq, y, a, TH, b, TH, VDD
        ));
        self.gate_count += 1;
        y
    }
    fn inv(&mut self, a: &str) -> String {
        self.nand2(a, a)
    }
    fn and2(&mut self, a: &str, b: &str) -> String {
        let t = self.nand2(a, b);
        self.inv(&t)
    }
    fn or2(&mut self, a: &str, b: &str) -> String {
        let ta = self.inv(a);
        let tb = self.inv(b);
        self.nand2(&ta, &tb)
    }
    fn xor2(&mut self, a: &str, b: &str) -> String {
        let n1 = self.nand2(a, b);
        let n2 = self.nand2(a, &n1);
        let n3 = self.nand2(b, &n1);
        self.nand2(&n2, &n3)
    }
    /// Full adder. Returns (sum, cout).
    fn full_adder(&mut self, a: &str, b: &str, cin: &str) -> (String, String) {
        let x1 = self.xor2(a, b);
        let s = self.xor2(&x1, cin);
        let w1 = self.and2(a, b);
        let w2 = self.and2(&x1, cin);
        let cout = self.or2(&w1, &w2);
        (s, cout)
    }
}

/// PWL source cycling through one bit of the combo counter. Holds each
/// combo value for the full slot (final point repeats the last value: a PWL
/// segment to 0 would ramp across the last slot).
fn pwl_input(name: &str, node: &str, bit: usize, ncomb: usize) -> String {
    let val = |k: usize| (((k >> bit) & 1) as i32 * 5).to_string();
    let mut pts = vec![format!("0 {}", val(0))];
    for k in 1..ncomb {
        pts.push(format!("{} {}", k as f64 * SLOT, val(k - 1)));
        pts.push(format!("{} {}", k as f64 * SLOT + RISE, val(k)));
    }
    pts.push(format!("{} {}", ncomb as f64 * SLOT, val(ncomb - 1)));
    format!("V{} {} 0 PWL({})", name, node, pts.join(" "))
}

fn sample_at(t: &[f64], v: &[f64], tq: f64) -> f64 {
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

/// 1-bit full adder, exhaustive 8 combos: fast correctness anchor.
#[test]
fn t30_fulladder_1bit_exhaustive() {
    let tstop = 8.0 * SLOT;
    // rebuild with sum/cout observed (observables must exist pre-run)
    let mut deck2 = vec!["fa1".to_string()];
    deck2.push(pwl_input("A", "a", 0, 8));
    deck2.push(pwl_input("B", "b", 1, 8));
    deck2.push(pwl_input("C", "cin", 2, 8));
    let mut net2 = Net::new();
    let (s2, c2) = net2.full_adder("a", "b", "cin");
    deck2.extend(net2.lines.clone());
    deck2.push(format!(".tran 20n {} 0 20n uic", tstop));
    deck2.push(".end".to_string());
    let r2 = run_simulation(SimRequest {
        netlist: deck2,
        observables: vec![format!("v({})", s2), format!("v({})", c2)],
        stops: vec![],
    })
    .expect("fa1 must run");
    let ts: Vec<f64> = r2.series[&format!("v({})", s2)].iter().map(|(x, _)| *x).collect();
    let vs: Vec<f64> = r2.series[&format!("v({})", s2)].iter().map(|(_, y)| *y).collect();
    let tc: Vec<f64> = r2.series[&format!("v({})", c2)].iter().map(|(x, _)| *x).collect();
    let vc: Vec<f64> = r2.series[&format!("v({})", c2)].iter().map(|(_, y)| *y).collect();
    for k in 0..8 {
        let a = (k >> 0) & 1;
        let b = (k >> 1) & 1;
        let cin = (k >> 2) & 1;
        let tq = (k as f64 + 0.9) * SLOT; // settled, near slot end
        let gs = sample_at(&ts, &vs, tq);
        let gc = sample_at(&tc, &vc, tq);
        let es = ((a + b + cin) & 1) as f64 * VDD;
        let ec = (((a + b + cin) >> 1) & 1) as f64 * VDD;
        assert!(
            (gs - es).abs() < 0.25,
            "combo {}: sum {} vs {}",
            k,
            gs,
            es
        );
        assert!(
            (gc - ec).abs() < 0.25,
            "combo {}: cout {} vs {}",
            k,
            gc,
            ec
        );
    }
}

/// 4-bit ripple-carry adder, ALL 512 input combinations in one run.
/// Carry ripples through 4 full adders (60 NANDs); every S bit and Cout is
/// checked against integer arithmetic. Tolerance 0.5 V: B gates drive exact
/// rails once settled; 0.5 V is 10x inside the 2.5 V noise margin and 10x
/// outside any solver-slop scale on a 5 V rail.
#[test]
fn t31_ripple_carry_4bit_exhaustive() {
    const N: usize = 512; // a[3:0] b[3:0] cin
    let mut net = Net::new();
    let mut deck = vec!["rca4".to_string()];
    for i in 0..4 {
        deck.push(pwl_input(&format!("A{}", i), &format!("a{}", i), i, N));
        deck.push(pwl_input(&format!("B{}", i), &format!("b{}", i), 4 + i, N));
    }
    deck.push(pwl_input("CIN", "cin", 8, N));
    let mut sums = vec![];
    let mut carry = "cin".to_string();
    for i in 0..4 {
        let (s, c) = net.full_adder(&format!("a{}", i), &format!("b{}", i), &carry);
        sums.push(s);
        carry = c;
    }
    let cout = carry;
    deck.extend(net.lines.clone());
    eprintln!("gates: {}", net.gate_count);
    assert!(net.gate_count == 60, "expect 15 NANDs/bit x4, got {}", net.gate_count);
    let tstop = N as f64 * SLOT;
    deck.push(format!(".tran 50n {} 0 50n uic", tstop));
    deck.push(".end".to_string());

    let obs: Vec<String> = sums
        .iter()
        .map(|s| format!("v({})", s))
        .chain(std::iter::once(format!("v({})", cout)))
        .collect();
    let r = run_simulation(SimRequest {
        netlist: deck,
        observables: obs.clone(),
        stops: vec![],
    })
    .expect("rca4 must run");
    eprintln!("npts={} wall={:.1}ms", r.npts, r.wall_ms);
    assert!(r.time.last().cloned().unwrap_or(0.0) >= tstop - 1e-9);
    let grids: Vec<(Vec<f64>, Vec<f64>)> = obs
        .iter()
        .map(|o| {
            let tv = &r.series[o];
            (tv.iter().map(|(x, _)| *x).collect(), tv.iter().map(|(_, y)| *y).collect())
        })
        .collect();
    let mut bad = 0;
    for k in 0..N {
        let a = (k & 0xF) as i32;
        let b = ((k >> 4) & 0xF) as i32;
        let cin = ((k >> 8) & 1) as i32;
        let total = a + b + cin;
        let tq = (k as f64 + 0.9) * SLOT;
        for bit in 0..4 {
            let g = sample_at(&grids[bit].0, &grids[bit].1, tq);
            let e = (((total >> bit) & 1) as f64) * VDD;
            if (g - e).abs() >= 0.5 {
                eprintln!("MISMATCH k={} (a={} b={} cin={}): s{}={} vs {}", k, a, b, cin, bit, g, e);
                bad += 1;
            }
        }
        let g = sample_at(&grids[4].0, &grids[4].1, tq);
        let e = (((total >> 4) & 1) as f64) * VDD;
        if (g - e).abs() >= 0.5 {
            eprintln!("MISMATCH k={} carry: {} vs {}", k, g, e);
            bad += 1;
        }
    }
    assert_eq!(bad, 0, "{} bit errors over 512 combos", bad);
}

/// Transistor-level NAND (2x NMOS pulldown, 2x PMOS pullup, Level-1 MOS
/// models): full truth table in one run. Proves real transistor devices
/// simulate through the core, not just behavioral B-sources.
#[test]
fn t32_mos_nand_truth_table() {
    // combos k: bit0=a, bit1=b -> (0,0),(1,0),(0,1),(1,1); NAND: 1 1 1 0
    let va = pwl_input("A", "a", 0, 4);
    let vb = pwl_input("B", "b", 1, 4);
    let deck = vec![
        "mosnand".to_string(),
        ".model NM NMOS(Level=1 VTO=0.7 KP=20u LAMBDA=0.01)".to_string(),
        ".model PM PMOS(Level=1 VTO=-0.7 KP=10u LAMBDA=0.01)".to_string(),
        va,
        vb,
        "M1 y a n1 n1 NM W=2u L=1u".to_string(),
        "M2 n1 b 0 0 NM W=2u L=1u".to_string(),
        "M3 y a vdd vdd PM W=4u L=1u".to_string(),
        "M4 y b vdd vdd PM W=4u L=1u".to_string(),
        "VDD vdd 0 DC 5".to_string(),
        ".tran 20n 4u 0 20n uic".to_string(),
        ".end".to_string(),
    ];
    let r = run_simulation(SimRequest {
        netlist: deck,
        observables: vec!["v(y)".to_string()],
        stops: vec![],
    })
    .expect("mos nand must run");
    let tv = &r.series["v(y)"];
    let t: Vec<f64> = tv.iter().map(|(x, _)| *x).collect();
    let v: Vec<f64> = tv.iter().map(|(_, y)| *y).collect();
    // windows: [0,1]=(0,0) [1,2]=(1,0) [2,3]=(0,1) [3,4]=(1,1) -> NAND 1 1 1 0
    let expect = [5.0, 5.0, 5.0, 0.0];
    for (k, e) in expect.iter().enumerate() {
        let g = sample_at(&t, &v, (k as f64 + 0.9) * 1e-6);
        assert!((g - e).abs() < 0.5, "mos nand combo {}: {} vs {}", k, g, e);
    }
}
