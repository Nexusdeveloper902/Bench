//! B-source temporal bandwidth -> timestep bound policy.
//!
//! Port of validated `exp-safety/bsource.py`: TIME-bearing B-sources get no
//! solver breakpoints, so the runtime derives an explicit 4th-arg TMAX:
//! events `min_width/10`, sines `min_period/50`. Unknown TIME -> `Unknown`.

use crate::validate::parse_value;
use regex::Regex;

#[derive(Debug, Clone, Default)]
pub struct Bandwidth {
    pub widths: Vec<f64>,
    pub periods: Vec<f64>,
    pub events: Vec<f64>,
    pub unknown_time: bool,
    pub has_time: bool,
}

pub fn extract_bandwidth(expr: &str) -> Bandwidth {
    let has_time = expr.to_uppercase().contains("TIME");
    if !has_time {
        return Bandwidth {
            has_time: false,
            ..Default::default()
        };
    }
    let mut widths = vec![];
    let mut periods = vec![];
    let mut events: Vec<f64> = vec![];

    // u(TIME-a) times
    if let Ok(re) = Regex::new(r"(?i)u\s*\(\s*TIME\s*-\s*([^\s)]+)\s*\)") {
        let mut times = vec![];
        for cap in re.captures_iter(expr) {
            if let Some(m) = cap.get(1) {
                if let Some(v) = parse_value(m.as_str()) {
                    times.push(v);
                }
            }
        }
        if times.len() >= 2 {
            times.sort_by(|a, b| a.partial_cmp(b).unwrap());
            for w in times.windows(2) {
                if w[1] > w[0] {
                    widths.push(w[1] - w[0]);
                }
            }
        }
        // events
        for cap in re.captures_iter(expr) {
            if let Some(m) = cap.get(1) {
                if let Some(v) = parse_value(m.as_str()) {
                    if !events.contains(&v) {
                        events.push(v);
                    }
                }
            }
        }
    }
    // TIME comparisons
    if let Ok(re) = Regex::new(r"(?i)TIME\s*[<>=]+\s*([^\s)?,;]+)") {
        for cap in re.captures_iter(expr) {
            if let Some(m) = cap.get(1) {
                if let Some(v) = parse_value(m.as_str()) {
                    events.push(v);
                }
            }
        }
    }
    // pwl(TIME, t0,v0, t1,v1...)
    if let Ok(re) = Regex::new(r"(?i)pwl\s*\(\s*TIME\s*,([^)]*)\)") {
        for cap in re.captures_iter(expr) {
            if let Some(m) = cap.get(1) {
                let mut nums = vec![];
                for tok in m.as_str().split(',') {
                    if let Some(v) = parse_value(tok.trim()) {
                        nums.push(v);
                    }
                }
                let ts: Vec<f64> = nums.iter().step_by(2).cloned().collect();
                if ts.len() >= 2 {
                    let mut dts = vec![];
                    for w in ts.windows(2) {
                        if w[1] > w[0] {
                            dts.push(w[1] - w[0]);
                        }
                    }
                    if !dts.is_empty() {
                        widths.push(dts.into_iter().fold(f64::INFINITY, f64::min));
                    }
                }
            }
        }
    }
    // sin/cos(k*TIME)
    if let Ok(re) = Regex::new(r"(?i)(?:sin|cos)\s*\(\s*([^)]*TIME[^)]*)\)") {
        for cap in re.captures_iter(expr) {
            if let Some(m) = cap.get(1) {
                let arg = m.as_str();
                let c = coef_before(arg).or_else(|| coef_after(arg));
                if let Some(c) = c {
                    if c != 0.0 {
                        periods.push(2.0 * std::f64::consts::PI / c.abs());
                    }
                }
            }
        }
    }

    let unknown = has_time && widths.is_empty() && periods.is_empty() && events.is_empty();
    Bandwidth {
        widths,
        periods,
        events,
        unknown_time: unknown,
        has_time: true,
    }
}

fn coef_before(arg: &str) -> Option<f64> {
    let re = Regex::new(r"(?i)([0-9.eE+\-]+(?:meg|k|u|n|p|g|m|t|f)?)\s*\*\s*TIME").ok()?;
    let cap = re.captures(arg)?;
    parse_value(cap.get(1)?.as_str())
}

fn coef_after(arg: &str) -> Option<f64> {
    let re = Regex::new(r"(?i)TIME\s*\*\s*([0-9.eE+\-]+(?:meg|k|u|n|p|g|m|t|f)?)").ok()?;
    let cap = re.captures(arg)?;
    parse_value(cap.get(1)?.as_str())
}

#[derive(Debug, Clone, PartialEq)]
pub enum TmaxNeed {
    None,
    Bound(f64),
    Unknown,
}

/// Required TMAX for one B expression.
pub fn required_tmax_for_expr(expr: &str, event_div: f64, sine_div: f64) -> (TmaxNeed, Bandwidth) {
    let bw = extract_bandwidth(expr);
    if !bw.has_time {
        return (TmaxNeed::None, bw);
    }
    if bw.unknown_time {
        return (TmaxNeed::Unknown, bw);
    }
    let mut bounds = vec![];
    if !bw.widths.is_empty() {
        bounds.push(bw.widths.iter().fold(f64::INFINITY, |a, &b| a.min(b)) / event_div);
    }
    if !bw.periods.is_empty() {
        bounds.push(bw.periods.iter().fold(f64::INFINITY, |a, &b| a.min(b)) / sine_div);
    }
    if !bounds.is_empty() {
        return (
            TmaxNeed::Bound(bounds.into_iter().fold(f64::INFINITY, f64::min)),
            bw,
        );
    }
    (TmaxNeed::Unknown, bw)
}
