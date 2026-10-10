//! Simulation kernel: lifecycle, pause/resume, verification, fallback.
//!
//! Production semantics (from exp-native REPORT6):
//! - Native resume is a *synchronization mechanism*, not a step optimizer.
//! - Stops are declared BEFORE the run (`stop when time = T`, exact form
//!   only); `run`; then `resume` per stop; then final `resume`.
//! - Only `resume` is issued while in progress (never `tran`/`run`: those
//!   restart the analysis from zero).
//! - Every pause time is verified; any deviation falls back to the ordinary
//!   full simulation. Fallback never makes a correct sim impossible.
//! - Solver-loud failures (singular/timestep/convergence) are hard errors,
//!   never silent fallback series.

use crate::error::SimError;
use crate::ngspice;
use crate::validate::{self, parse_value};
use crate::bsource::{required_tmax_for_expr, TmaxNeed};
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct SimRequest {
    pub netlist: Vec<String>,
    pub observables: Vec<String>,
    pub stops: Vec<f64>,
}

#[derive(Debug, Clone)]
pub struct SimResult {
    pub series: HashMap<String, Vec<(f64, f64)>>,
    pub time: Vec<f64>,
    pub npts: usize,
    pub wall_ms: f64,
    pub used_pauses: bool,
    pub fallback_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum SimStatus {
    Idle,
    Ready,
    Running,
    Paused { at: f64 },
    Complete,
    Failed(String),
}

/// Existing explicit 4th-arg TMAX on the .tran line, if any.
fn tran_tmax(lines: &[String]) -> Option<f64> {
    for raw in lines {
        let s = raw.trim();
        if s.to_lowercase().starts_with(".tran") {
            let toks: Vec<&str> = s.split_whitespace().skip(1).collect();
            let mut vals: Vec<f64> = vec![];
            for t in toks {
                if t.eq_ignore_ascii_case("uic") {
                    continue;
                }
                if let Some(v) = parse_value(t) {
                    vals.push(v);
                } else {
                    break; // parameterized token: positional meaning unclear
                }
            }
            if vals.len() >= 4 {
                return Some(vals[3]);
            }
            return None;
        }
    }
    None
}

/// F1: enforce the B-source bandwidth policy. For every TIME-bearing B
/// expression, require TMAX <= min_width/10 (events) / min_period/50 (sines).
/// Tightens the .tran line when the required bound is stricter than the
/// explicit one; demands an explicit user bound for unquantifiable TIME.
fn apply_tmax_policy(mut lines: Vec<String>) -> Result<Vec<String>, SimError> {
    let exprs = validate::b_exprs(&lines);
    if exprs.is_empty() {
        return Ok(lines);
    }
    let mut need: Option<f64> = None;
    let mut unknown = false;
    for e in &exprs {
        match required_tmax_for_expr(e, 10.0, 50.0).0 {
            TmaxNeed::Bound(b) => {
                need = Some(need.map_or(b, |n: f64| n.min(b)));
            }
            TmaxNeed::Unknown => unknown = true,
            TmaxNeed::None => {}
        }
    }
    let explicit = tran_tmax(&lines);
    if let Some(b) = need {
        let tight = match explicit {
            Some(cur) => cur.min(b),
            None => b,
        };
        // rewrite the first .tran line's 4th positional arg
        for line in lines.iter_mut() {
            if line.trim().to_lowercase().starts_with(".tran") {
                let toks: Vec<String> =
                    line.split_whitespace().map(|s| s.to_string()).collect();
                // toks[0]=".tran"; need TSTEP TSTOP TSTART TMAX [UIC...]
                let mut rest = toks[1..].to_vec();
                while rest.len() < 3 {
                    rest.push("".to_string());
                }
                // preserve UIC tail
                let uic = rest.iter().any(|t| t.eq_ignore_ascii_case("uic"));
                let mut nums: Vec<String> =
                    rest.into_iter().filter(|t| !t.eq_ignore_ascii_case("uic")).collect();
                while nums.len() < 4 {
                    nums.push(String::new());
                }
                nums[3] = format!("{}", tight);
                *line = format!("{} {}", toks[0], nums.join(" "));
                if uic {
                    line.push_str(" uic");
                }
                break;
            }
        }
        return Ok(lines);
    }
    if unknown && explicit.is_none() {
        return Err(SimError::InvalidConfig {
            message: "B-source with unquantifiable TIME dependence requires an explicit .tran TMAX (4th arg)".to_string(),
        });
    }
    Ok(lines)
}

/// Parse t_stop from the .tran line. Handles 2/3/4-arg forms + UIC + units.
pub fn parse_tstop(lines: &[String]) -> Option<f64> {
    for raw in lines {
        let s = raw.trim();
        if s.to_lowercase().starts_with(".tran") {
            let toks: Vec<&str> = s.split_whitespace().skip(1).collect();
            let mut vals: Vec<f64> = vec![];
            for t in toks {
                if t.eq_ignore_ascii_case("uic") {
                    continue;
                }
                if let Some(v) = parse_value(t) {
                    vals.push(v);
                }
            }
            if vals.len() >= 2 {
                return Some(vals[1]);
            }
        }
    }
    None
}

pub fn pause_exact(got: f64, want: f64) -> bool {
    let tol = (1e-12f64).max(want.abs() * 1e-9);
    (got - want).abs() <= tol
}

fn scan_loud_failure(log: &[String]) -> Option<String> {
    let pats = [
        "singular",
        "singular matrix",
        "timestep too small",
        "timestep",
        "no convergence",
        "convergence",
        "stepping failed",
        "gmin stepping failed",
        "source stepping failed",
        "dc solution failed",
        "aborted",
        "doanalyses: timestep",
        "not parsed",
        "mif-error",
        "unable to find definition",
        "failed",
    ];
    // "failed" alone is broad; require it alongside solver context is handled
    // by ordering: specific patterns first, generic last, benign lines (e.g.
    // "0 warnings") don't contain these tokens.
    for line in log {
        let l = line.to_lowercase();
        for p in &pats {
            if l.contains(p) {
                // skip obviously benign mentions
                if l.contains("0 failed") || l.contains("no failed") {
                    continue;
                }
                return Some(line.trim().to_string());
            }
        }
    }
    None
}

fn nonfinite_or_short(time: &[f64], series: &HashMap<String, Vec<f64>>) -> bool {
    if time.iter().any(|v| !v.is_finite()) {
        return true;
    }
    for v in series.values() {
        if v.iter().any(|x| !x.is_finite()) {
            return true;
        }
    }
    // strictly increasing time required
    for w in time.windows(2) {
        if !(w[1] > w[0]) {
            return true;
        }
    }
    false
}

fn read_all(obs: &[String]) -> (HashMap<String, Vec<(f64, f64)>>, Vec<f64>) {
    let obs_s: Vec<String> = obs.to_vec();
    let (raw, t) = ngspice::read_series(&obs_s);
    let mut series = HashMap::new();
    for o in &obs_s {
        let d = raw.get(o).cloned().unwrap_or_default();
        let m = t.len().min(d.len());
        series.insert(o.clone(), (0..m).map(|i| (t[i], d[i])).collect());
    }
    (series, t)
}

/// An observable the backend could not supply over the whole time grid
/// (unknown node/branch, or a device quantity that was never saved) is a
/// caller error, never an empty/truncated series. Skipped when there is no
/// time grid (.op decks).
fn check_observables(series: &HashMap<String, Vec<(f64, f64)>>, t: &[f64]) -> Result<(), SimError> {
    if t.is_empty() {
        return Ok(());
    }
    let mut bad: Vec<&String> = series.iter().filter(|(_, v)| v.len() != t.len()).map(|(k, _)| k).collect();
    if bad.is_empty() {
        return Ok(());
    }
    bad.sort();
    Err(SimError::InvalidConfig {
        message: format!(
            "observable(s) not available over the time grid (unknown node/branch or not saved): {}",
            bad.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")
        ),
    })
}

fn full_run_inner(lines: &[String], obs: &[String]) -> Result<(HashMap<String, Vec<(f64, f64)>>, Vec<f64>, f64), SimError> {
    ngspice::ensure_init().map_err(|e| SimError::BackendFailure { message: e })?;
    ngspice::teardown();
    let t0 = std::time::Instant::now();
    let rc = ngspice::do_circ(lines).map_err(|e| SimError::BackendFailure { message: e })?;
    if rc != 0 {
        return Err(SimError::BackendFailure {
            message: format!("circ rc={}", rc),
        });
    }
    let rc = ngspice::do_command("run").map_err(|e| SimError::BackendFailure { message: e })?;
    if rc != 0 {
        return Err(SimError::BackendFailure {
            message: format!("run rc={}", rc),
        });
    }
    let wall = t0.elapsed().as_secs_f64() * 1e3;
    let (log, _stat) = ngspice::take_logs();
    if let Some(hint) = scan_loud_failure(&log) {
        if hint.to_lowercase().contains("timestep too small")
            || hint.to_lowercase().contains("no convergence")
        {
            return Err(SimError::ConvergenceFailure { message: hint });
        }
        return Err(SimError::SolverFailure {
            message: hint.clone(),
            log_hint: hint,
        });
    }
    let _ = rc;
    let (series, t) = read_all(obs);
    check_observables(&series, &t)?;
    let flat_full: HashMap<String, Vec<f64>> = series
        .iter()
        .map(|(k, v)| (k.clone(), v.iter().map(|(_, y)| *y).collect()))
        .collect();
    // F11: NaN/non-monotonic grids are solver failures on both paths.
    if nonfinite_or_short(&t, &flat_full) {
        // empty grids on .op decks are fine (no time vector); only fail transients
        if parse_tstop(lines).is_some() {
            return Err(SimError::SolverFailure {
                message: "non-finite or non-monotonic grid".to_string(),
                log_hint: log.join("\n"),
            });
        }
    }
    Ok((series, t, wall))
}

/// Run a request: native pauses when requested and exactly achievable,
/// otherwise ordinary full simulation (fallback). Solver failures are errors.
pub fn run_simulation(req: SimRequest) -> Result<SimResult, SimError> {
    // Whole-sequence serialization on the singleton handle.
    let _op = ngspice::op_lock();
    run_simulation_inner(req)
}

fn run_simulation_inner(req: SimRequest) -> Result<SimResult, SimError> {
    // 0. static gates first (fail fast, before touching the backend)
    let findings = validate::validate(&req.netlist.iter().map(|s| s.to_string()).collect::<Vec<_>>());
    if validate::has_error(&findings) {
        let codes = validate::error_codes(&findings);
        let first = findings.iter().find(|f| f.severity == "error").unwrap();
        return Err(SimError::InvalidCircuit {
            code: codes.join("+"),
            message: first.message.clone(),
        });
    }
    // deny control-flow escapes (skip title line 0: ngspice title semantics)
    for l in req.netlist.iter().skip(1) {
        let ll = l.trim().to_lowercase();
        if ll == "quit" || ll.starts_with("quit ") || ll.contains("controlled exit") {
            return Err(SimError::InvalidConfig {
                message: "netlist contains control-flow escape (quit)".to_string(),
            });
        }
    }
    // empty-deck guard (validate() yields no findings for title-only decks)
    {
        let has_content = req.netlist.iter().skip(1).any(|l| {
            let s = l.trim();
            !(s.is_empty() || s.starts_with('*') || s.eq_ignore_ascii_case(".end"))
        });
        if !has_content {
            return Err(SimError::InvalidCircuit {
                code: "MALFORMED".to_string(),
                message: "empty netlist (no devices or analyses)".to_string(),
            });
        }
    }

    let mut lines = req.netlist.clone();
    let obs = req.observables.clone();
    let stops_in = req.stops.clone();
    // F13: non-finite stops are caller bugs, not "no stops requested".
    if stops_in.iter().any(|s| !s.is_finite()) {
        return Err(SimError::InvalidConfig {
            message: "stop times must be finite".to_string(),
        });
    }
    if stops_in.len() > 1024 {
        return Err(SimError::InvalidConfig {
            message: format!("too many stops ({})", stops_in.len()),
        });
    }
    let stops = stops_in;
    // F19: OSDI/XSPICE pause fence -- event semantics unverified there.
    // (skip title line 0, which is never a device.)
    let unsupported_pause = lines.iter().skip(1).any(|l| {
        let s = l.trim();
        if s.is_empty() || s.starts_with('*') || s.starts_with('.') {
            return s.to_lowercase().starts_with(".osdi");
        }
        matches!(
            s.chars().next().map(|c| c.to_ascii_uppercase()),
            Some('A') | Some('O') | Some('U')
        )
    });
    // F1: B-source bandwidth -> TMAX policy (tighten-only, never loosen).
    lines = apply_tmax_policy(lines)?;
    let t_stop = parse_tstop(&lines);

    // No stops: ordinary full simulation.
    if stops.is_empty() {
        let (series, t, wall) = full_run_inner(&lines, &obs)?;
        let n = t.len();
        return Ok(SimResult {
            series,
            time: t,
            npts: n,
            wall_ms: wall,
            used_pauses: false,
            fallback_reason: None,
        });
    }

    // Validate stop schedule upfront.
    let fb = |reason: &str| -> Result<SimResult, SimError> {
        let (series, t, wall) = full_run_inner(&lines, &obs)?;
        Ok(SimResult {
            npts: t.len(),
            time: t.clone(),
            series,
            wall_ms: wall,
            used_pauses: false,
            fallback_reason: Some(reason.to_string()),
        })
    };
    let t_stop = match t_stop {
        Some(v) => v,
        None => return fb("unknown_tstop"),
    };
    // F19: unsupported pause domains fall back to full simulation (still
    // correct; pauses just aren't attempted there).
    if unsupported_pause && !stops.is_empty() {
        return fb("unsupported_pause_domain");
    }
    if stops.iter().any(|s| !(*s > 0.0 && *s < t_stop)) {
        return fb("stop_outside_interval");
    }
    for w in stops.windows(2) {
        if !(w[1] > w[0]) {
            return fb("stops_not_strictly_increasing");
        }
    }
    {
        let mut seen = std::collections::HashSet::new();
        for s in &stops {
            if !seen.insert(s.to_bits()) {
                return fb("duplicate_stops");
            }
        }
    }
    // min separation: 4x verification tolerance so close stops can't false-pass
    for w in stops.windows(2) {
        let tol = (1e-12f64).max(w[1].abs() * 1e-9);
        if (w[1] - w[0]) < 4.0 * tol {
            return fb("stops_too_close");
        }
    }

    // Native path: single critical section across circ->stops->run->resumes->reads.
    ngspice::ensure_init().map_err(|e| SimError::BackendFailure { message: e })?;
    ngspice::teardown();
    let t0 = std::time::Instant::now();
    let rc = ngspice::do_circ(&lines).map_err(|e| SimError::BackendFailure { message: e })?;
    if rc != 0 {
        return fb("circ_rejected");
    }
    for s in &stops {
        let rc = ngspice::do_command(&format!("stop when time = {:.17e}", s))
            .map_err(|e| SimError::BackendFailure { message: e })?;
        if rc != 0 {
            return fb("stop_rejected");
        }
    }
    let rc_run = ngspice::do_command("run").map_err(|e| SimError::BackendFailure { message: e })?;
    if rc_run != 0 {
        return fb("run_rejected");
    }
    // verify first pause from CURRENT plot only
    let (_, t) = read_all(&obs);
    if t.is_empty() || !pause_exact(*t.last().unwrap(), stops[0]) {
        return fb("inexact_first_pause");
    }
    for (i, s) in stops.iter().skip(1).enumerate() {
        let rc = ngspice::do_command("resume").map_err(|e| SimError::BackendFailure { message: e })?;
        if rc != 0 {
            return fb(&format!("resume_rejected_{}", i + 1));
        }
        let (_, t) = read_all(&obs);
        if t.is_empty() || !pause_exact(*t.last().unwrap(), *s) {
            return fb(&format!("inexact_pause_{}", i + 1));
        }
    }
    let rc = ngspice::do_command("resume").map_err(|e| SimError::BackendFailure { message: e })?;
    if rc != 0 {
        return fb("resume_rejected_final");
    }
    let wall = t0.elapsed().as_secs_f64() * 1e3;
    let (log, _) = ngspice::take_logs();
    if let Some(hint) = scan_loud_failure(&log) {
        if hint.to_lowercase().contains("timestep too small")
            || hint.to_lowercase().contains("no convergence")
        {
            return Err(SimError::ConvergenceFailure { message: hint });
        }
        return Err(SimError::SolverFailure {
            message: hint.clone(),
            log_hint: hint,
        });
    }
    let (series, t) = read_all(&obs);
    if t.is_empty() || !pause_exact(*t.last().unwrap(), t_stop) {
        return fb("incomplete_after_resume");
    }
    check_observables(&series, &t)?;
    // monotonic + finite guard on the pause path too: NaN/short grids are
    // solver failures, never silent success (F11).
    let flat: HashMap<String, Vec<f64>> = series
        .iter()
        .map(|(k, v)| (k.clone(), v.iter().map(|(_, y)| *y).collect()))
        .collect();
    if nonfinite_or_short(&t, &flat) {
        return Err(SimError::SolverFailure {
            message: "non-finite or non-monotonic grid after resume".to_string(),
            log_hint: log.join("\n"),
        });
    }
    ngspice::teardown();
    let n = t.len();
    Ok(SimResult {
        series,
        time: t,
        npts: n,
        wall_ms: wall,
        used_pauses: true,
        fallback_reason: None,
    })
}
