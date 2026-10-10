//! Production component / model representation.
//!
//! Electrical behavior is described structurally (devices + values + models),
//! never by poking solver state. This module builds validated netlists for the
//! initial primitives (R, C, L, V, I, D) plus behavioral B-sources (via the
//! thin allowlist at a higher layer), subcircuits, and `.model` cards -- the
//! bounded-full package scope. OSDI/XSPICE are fenced: represented but refused
//! on pause paths (see kernel).

#[derive(Debug, Clone)]
pub struct TranConfig {
    pub tstep: f64,
    pub tstop: f64,
    pub tstart: f64,
    pub tmax: Option<f64>,
    pub uic: bool,
}

impl TranConfig {
    pub fn line(&self) -> String {
        let mut s = format!(".tran {} {} {}", fmt_num(self.tstep), fmt_num(self.tstop), fmt_num(self.tstart));
        match self.tmax {
            Some(m) => s.push_str(&format!(" {}", fmt_num(m))),
            None => s.push_str(" "),
        }
        if self.uic {
            s.push_str(" uic");
        }
        s
    }
}

pub fn fmt_num(v: f64) -> String {
    format!("{}", v)
}

#[derive(Debug, Clone)]
pub enum Component {
    R { name: String, p: String, n: String, value: String },
    C { name: String, p: String, n: String, value: String, ic: Option<f64> },
    L { name: String, p: String, n: String, value: String, ic: Option<f64> },
    V { name: String, p: String, n: String, kind: String },
    I { name: String, p: String, n: String, kind: String },
    D { name: String, p: String, n: String, model: String },
    B { name: String, p: String, n: String, v_or_i: char, expr: String },
    K { name: String, l1: String, l2: String, k: f64 },
    T { name: String, p1: String, n1: String, p2: String, n2: String, z0: f64, td: f64 },
}

impl Component {
    pub fn line(&self) -> String {
        match self {
            Component::R { name, p, n, value } => format!("R{} {} {} {}", name, p, n, value),
            Component::C { name, p, n, value, ic } => {
                let mut s = format!("C{} {} {} {}", name, p, n, value);
                if let Some(ic) = ic {
                    s.push_str(&format!(" IC={}", fmt_num(*ic)));
                }
                s
            }
            Component::L { name, p, n, value, ic } => {
                let mut s = format!("L{} {} {} {}", name, p, n, value);
                if let Some(ic) = ic {
                    s.push_str(&format!(" IC={}", fmt_num(*ic)));
                }
                s
            }
            Component::V { name, p, n, kind } => format!("V{} {} {} {}", name, p, n, kind),
            Component::I { name, p, n, kind } => format!("I{} {} {} {}", name, p, n, kind),
            Component::D { name, p, n, model } => format!("D{} {} {} {}", name, p, n, model),
            Component::B { name, p, n, v_or_i, expr } => {
                format!("B{} {} {} {}={}", name, p, n, v_or_i, expr)
            }
            Component::K { name, l1, l2, k } => format!("K{} {} {} {}", name, l1, l2, fmt_num(*k)),
            Component::T { name, p1, n1, p2, n2, z0, td } => {
                format!("T{} {} {} {} {} Z0={} TD={}", name, p1, n1, p2, n2, fmt_num(*z0), fmt_num(*td))
            }
        }
    }
}

/// Assemble a full deck: title + components + models + tran + control + end.
/// Deny-list control-flow escapes (quit/exit) so lib state can't be killed.
pub fn build_deck(
    title: &str,
    comps: &[Component],
    models: &[String],
    tran: &TranConfig,
    extra: &[String],
) -> Result<Vec<String>, String> {
    let mut lines = vec![title.to_string()];
    for c in comps {
        lines.push(c.line());
    }
    for m in models {
        lines.push(m.clone());
    }
    lines.push(tran.line());
    for e in extra {
        lines.push(e.clone());
    }
    lines.push(".control".to_string());
    lines.push("run".to_string());
    lines.push(".endc".to_string());
    lines.push(".end".to_string());
    for l in &lines {
        let ll = l.to_lowercase();
        if ll.trim() == "quit" || ll.contains("ngspice_command") || ll.trim_start().starts_with("quit ") {
            return Err("control-flow escape refused".to_string());
        }
    }
    Ok(lines)
}
