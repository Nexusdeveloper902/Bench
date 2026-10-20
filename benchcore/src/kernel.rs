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
    /// Plausibility warnings on a run that otherwise succeeded (G15): see `implausible_internal_nodes`.
    pub warnings: Vec<String>,
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
pub(crate) fn apply_tmax_policy(mut lines: Vec<String>) -> Result<Vec<String>, SimError> {
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

pub(crate) fn scan_loud_failure(log: &[String]) -> Option<String> {
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
        // parse problems ngspice reports but still returns rc 0 for (L6)
        "unknown subckt",
        "parameters for subcircuit type",
        "instance line, ignored",
        "unknown model type",
        "failed",
    ];
    // "failed" alone is broad; require it alongside solver context is handled
    // by ordering: specific patterns first, generic last, benign lines (e.g.
    // "0 warnings") don't contain these tokens.
    let clean = |line: &str| {
        let t = line.trim();
        t.strip_prefix("stderr ").or_else(|| t.strip_prefix("stdout ")).unwrap_or(t).to_string()
    };
    for line in log {
        let l = line.to_lowercase();
        // every ngspice `Error...` line on stderr is a failure, whatever the rc
        if l.starts_with("stderr error") {
            return Some(clean(line));
        }
        for p in &pats {
            if l.contains(p) {
                // skip obviously benign mentions
                if l.contains("0 failed") || l.contains("no failed") {
                    continue;
                }
                return Some(clean(line));
            }
        }
    }
    None
}

/// `msg: <first ngspice error lines>` (L12: the reason lives in the log).
pub(crate) fn with_reason(msg: String) -> String {
    let why = ngspice::error_lines(3);
    if why.is_empty() {
        msg
    } else {
        format!("{}: {}", msg, why.join(" | "))
    }
}

pub(crate) fn nonfinite_or_short(time: &[f64], series: &HashMap<String, Vec<f64>>) -> bool {
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
pub(crate) fn check_observables(series: &HashMap<String, Vec<(f64, f64)>>, t: &[f64]) -> Result<(), SimError> {
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

type FullRun = (HashMap<String, Vec<(f64, f64)>>, Vec<f64>, f64, Vec<String>);

fn full_run_inner(lines: &[String], obs: &[String]) -> Result<FullRun, SimError> {
    ngspice::ensure_init().map_err(|e| SimError::BackendFailure { message: e })?;
    ngspice::teardown();
    let t0 = std::time::Instant::now();
    let rc = ngspice::do_circ(lines).map_err(|e| SimError::BackendFailure { message: e })?;
    if rc != 0 {
        return Err(SimError::BackendFailure {
            message: with_reason(format!("circ rc={}", rc)),
        });
    }
    let rc = ngspice::do_command("run").map_err(|e| SimError::BackendFailure { message: e })?;
    if rc != 0 {
        return Err(SimError::BackendFailure {
            message: with_reason(format!("run rc={}", rc)),
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
    // a transient that produced no time grid never succeeded (L6)
    if t.is_empty() && parse_tstop(lines).is_some() {
        return Err(SimError::SolverFailure {
            message: "transient analysis produced no data points".to_string(),
            log_hint: log.join("\n"),
        });
    }
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
    Ok((series, t, wall, implausible_internal_nodes()))
}

/// G15: a node inside a subcircuit instance (`x1.ax`) whose peak |V| exceeds every circuit-level node's by far (more
/// than 10x and 100 V) is a runaway, not a result: the probed case is an SCR model whose floating internal nodes ran to
/// its 1000 V diode breakdown after turn-off on a 12 V circuit, reported as success. ngspice gives no error, so the run
/// stays a success with a warning naming the node. Reads the plot's saved node vectors only (never `@` vectors).
fn implausible_internal_nodes() -> Vec<String> {
    let plot = ngspice::cur_plot_name();
    if plot.is_empty() {
        return vec![];
    }
    let peak = |name: &str| {
        ngspice::read_vec_qualified(&format!("{}.{}", plot, name)).map_or(0.0, |v| v.iter().fold(0.0f64, |m, x| m.max(x.abs())))
    };
    let (mut outer, mut inner) = (0.0f64, vec![]);
    for n in ngspice::vec_names(&plot) {
        if n.contains('#') || n.starts_with('@') || n.eq_ignore_ascii_case("time") {
            continue;
        }
        if n.contains('.') {
            inner.push((peak(&n), n));
        } else {
            outer = outer.max(peak(&n));
        }
    }
    let limit = (10.0 * outer).max(outer + 100.0);
    let mut bad: Vec<(f64, String)> = inner.into_iter().filter(|(v, _)| *v > limit).collect();
    bad.sort_by(|a, b| b.0.total_cmp(&a.0));
    match bad.first() {
        None => vec![],
        Some((v, n)) => vec![format!(
            "implausible internal node: {} reached {:.3e} V while no circuit node exceeded {:.3e} V{}; a part's model ran \
             away (a numerical artefact, or the model outside its valid range): do not trust this result",
            n, v, outer, if bad.len() > 1 { format!(" ({} such nodes)", bad.len()) } else { String::new() }
        )],
    }
}

/// An `@dev[param]` observable with no `.save` line naming it. Read through ngGet_Vec_Info at a pause it corrupts
/// ngspice and the next resume dumps core (probed, LIVE.md; G32), so batch and live runs refuse it up front.
pub(crate) fn refuse_unsaved_device_observables(lines: &[String], observables: &[String]) -> Result<(), SimError> {
    let saves: String = lines.iter().filter(|l| l.trim().to_lowercase().starts_with(".save")).map(|l| l.to_lowercase()).collect();
    match observables.iter().find(|o| o.starts_with('@') && !saves.contains(&o.to_lowercase())) {
        Some(o) => Err(SimError::InvalidConfig { message: format!("observable {} needs a .save line naming it", o) }),
        None => Ok(()),
    }
}

/// Run a request: native pauses when requested and exactly achievable,
/// otherwise ordinary full simulation (fallback). Solver failures are errors.
pub fn run_simulation(req: SimRequest) -> Result<SimResult, SimError> {
    // Whole-sequence serialization on the singleton handle.
    let _op = ngspice::op_lock();
    crate::live::refuse_if_live()?;
    run_simulation_inner(req)
}

/// The static gates every run passes before touching the backend: validator, control-flow escapes, empty deck.
/// Shared by batch and live runs (live runs then apply the same TMAX policy).
pub(crate) fn gates(netlist: &[String]) -> Result<(), SimError> {
    let findings = validate::validate(&netlist.to_vec());
    if validate::has_error(&findings) {
        let codes = validate::error_codes(&findings);
        let first = findings.iter().find(|f| f.severity == "error").unwrap();
        return Err(SimError::InvalidCircuit {
            code: codes.join("+"),
            message: first.message.clone(),
        });
    }
    // deny control-flow escapes (skip title line 0: ngspice title semantics)
    for l in netlist.iter().skip(1) {
        let ll = l.trim().to_lowercase();
        if ll == "quit" || ll.starts_with("quit ") || ll.contains("controlled exit") {
            return Err(SimError::InvalidConfig {
                message: "netlist contains control-flow escape (quit)".to_string(),
            });
        }
    }
    // empty-deck guard (validate() yields no findings for title-only decks)
    {
        let has_content = netlist.iter().skip(1).any(|l| {
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
    Ok(())
}

fn run_simulation_inner(req: SimRequest) -> Result<SimResult, SimError> {
    // 0. static gates first (fail fast, before touching the backend)
    gates(&req.netlist)?;
    if analyses(&req.netlist).iter().any(|a| a.starts_with(".ac ")) {
        return Err(SimError::InvalidConfig { message: "an .ac deck is run with run_ac, not run_simulation".to_string() });
    }
    refuse_unsaved_device_observables(&req.netlist, &req.observables)?;
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
        let (series, t, wall, warnings) = full_run_inner(&lines, &obs)?;
        let n = t.len();
        return Ok(SimResult {
            series,
            time: t,
            npts: n,
            wall_ms: wall,
            used_pauses: false,
            fallback_reason: None,
            warnings,
        });
    }

    // Validate stop schedule upfront.
    let fb = |reason: &str| -> Result<SimResult, SimError> {
        let (series, t, wall, warnings) = full_run_inner(&lines, &obs)?;
        Ok(SimResult {
            npts: t.len(),
            time: t.clone(),
            series,
            wall_ms: wall,
            used_pauses: false,
            fallback_reason: Some(reason.to_string()),
            warnings,
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
    let warnings = implausible_internal_nodes();
    ngspice::teardown();
    let n = t.len();
    Ok(SimResult {
        series,
        time: t,
        npts: n,
        wall_ms: wall,
        used_pauses: true,
        fallback_reason: None,
        warnings,
    })
}

// ---------------------------------------------------------------- AC small-signal analysis (G17)

/// An `.ac` run: the frequency grid (Hz) and each observable as (re, im) at every frequency.
#[derive(Debug, Clone)]
pub struct AcResult {
    pub frequency: Vec<f64>,
    pub series: HashMap<String, Vec<(f64, f64)>>,
    pub wall_ms: f64,
}

/// The deck's analysis cards (title line skipped), lowercased.
fn analyses(lines: &[String]) -> Vec<String> {
    let cards = [".tran", ".ac", ".dc", ".op", ".noise", ".pz", ".tf", ".sens", ".disto", ".pss", ".sp"];
    lines
        .iter()
        .skip(1)
        .map(|l| l.trim().to_lowercase())
        .filter(|l| l.split_whitespace().next().is_some_and(|w| cards.contains(&w)))
        .collect()
}

const AC_MAX_POINTS: f64 = 1e6;

/// The sweep of the deck's one `.ac dec|oct|lin <points> <fstart> <fstop>` card, checked: ngspice runs a bad one
/// without any error (probed: `.ac dec 10 0 1k` leaves no ac plot and logs nothing), so it is refused here.
pub fn ac_sweep(lines: &[String]) -> Result<(String, f64, f64, f64), SimError> {
    let bad = |m: String| Err(SimError::InvalidConfig { message: m });
    let cards = analyses(lines);
    if cards.len() != 1 || !cards[0].starts_with(".ac ") {
        return bad(format!("an AC run needs exactly one .ac card and no other analysis; found: {}", cards.join(" | ")));
    }
    let t: Vec<&str> = cards[0].split_whitespace().skip(1).collect();
    let nums: Vec<Option<f64>> = t.iter().skip(1).map(|s| parse_value(s)).collect();
    let (Some(kind), [Some(n), Some(f1), Some(f2)]) = (t.first(), nums.as_slice()) else {
        return bad(format!("bad .ac card '{}': use .ac dec|oct|lin <points> <fstart> <fstop>", cards[0]));
    };
    let (n, f1, f2) = (*n, *f1, *f2);
    let span = match *kind {
        "dec" => n * (f2 / f1).log10(),
        "oct" => n * (f2 / f1).log2(),
        "lin" => n - 1.0,
        _ => return bad(format!("AC sweep must be dec, oct or lin, not '{}'", kind)),
    };
    if !(n >= 1.0 && n.fract() == 0.0) {
        return bad(format!("AC points must be a whole number of at least 1, not {}", n));
    }
    if *kind == "lin" && n == 2.0 {
        // probed: ngspice 47 returns only fstart for every `lin 2` sweep (lin 1 and lin 3+ are exact)
        return bad("a lin AC sweep of 2 points returns 1 point in ngspice 47: use 3 or more".to_string());
    }
    let one_point = *kind == "lin" && n == 1.0;
    if !(f1 > 0.0 && f1.is_finite() && f2.is_finite() && (f2 > f1 || (one_point && f2 == f1))) {
        return bad(format!("AC frequencies need 0 < fstart < fstop (got {} and {})", f1, f2));
    }
    if span + 1.0 > AC_MAX_POINTS {
        return bad(format!("AC sweep has too many points ({:.3e}; limit {:.0e})", span + 1.0, AC_MAX_POINTS));
    }
    Ok((kind.to_string(), n, f1, f2))
}

/// Run an `.ac` deck. Observables are node voltages `v(n)` and branch currents `i(vsrc)`: `@dev[param]` vectors are
/// refused, because in an ac plot they hold the device's operating-point value repeated at every frequency, not a
/// small-signal quantity (probed: `@d1[id]`, `@q1[ic]`, `@i1[c]` constant and real; `@r1[i]`, `@c1[i]`, `@l1[i]` empty).
pub fn run_ac(netlist: &[String], observables: &[String]) -> Result<AcResult, SimError> {
    let _op = ngspice::op_lock();
    crate::live::refuse_if_live()?;
    gates(netlist)?;
    let (kind, n, _, _) = ac_sweep(netlist)?;
    if let Some(o) = observables.iter().find(|o| o.starts_with('@')) {
        return Err(SimError::InvalidConfig {
            message: format!(
                "{} is an operating-point device quantity, not an AC signal: observe a node voltage or a source current",
                o
            ),
        });
    }
    let backend = |e: String| SimError::BackendFailure { message: e };
    ngspice::ensure_init().map_err(backend)?;
    ngspice::teardown();
    let t0 = std::time::Instant::now();
    let rc = ngspice::do_circ(netlist).map_err(backend)?;
    if rc != 0 {
        return Err(backend(with_reason(format!("circ rc={}", rc))));
    }
    let rc = ngspice::do_command("run").map_err(backend)?;
    if rc != 0 {
        return Err(backend(with_reason(format!("run rc={}", rc))));
    }
    let wall_ms = t0.elapsed().as_secs_f64() * 1e3;
    let (log, _) = ngspice::take_logs();
    if let Some(hint) = scan_loud_failure(&log) {
        let l = hint.to_lowercase();
        if l.contains("timestep too small") || l.contains("no convergence") {
            return Err(SimError::ConvergenceFailure { message: hint });
        }
        return Err(SimError::SolverFailure { message: hint.clone(), log_hint: hint });
    }
    let no_data = |what: &str| SimError::SolverFailure { message: format!("AC analysis {}", what), log_hint: log.join("\n") };
    let plot = ngspice::cur_plot_name();
    if !plot.to_lowercase().starts_with("ac") {
        return Err(no_data("produced no data points"));
    }
    let frequency: Vec<f64> = ngspice::read_cvec_qualified(&format!("{}.frequency", plot))
        .unwrap_or_default()
        .into_iter()
        .map(|z| z.0)
        .collect();
    if frequency.is_empty() {
        return Err(no_data("produced no data points"));
    }
    if kind == "lin" && frequency.len() as f64 != n {
        return Err(no_data(&format!("returned {} points for a lin sweep of {}", frequency.len(), n)));
    }
    let mut series = HashMap::new();
    let mut missing = vec![];
    for o in observables {
        let v = ngspice::read_cvec_qualified(&format!("{}.{}", plot, o)).unwrap_or_default();
        if v.len() != frequency.len() {
            missing.push(o.as_str());
        }
        series.insert(o.clone(), v);
    }
    if !missing.is_empty() {
        missing.sort();
        return Err(SimError::InvalidConfig {
            message: format!(
                "observable(s) not available over the frequency grid (unknown node/branch): {}",
                missing.join(", ")
            ),
        });
    }
    let finite = series.values().flatten().all(|(a, b)| a.is_finite() && b.is_finite());
    if !finite || frequency.iter().any(|f| !f.is_finite()) || frequency.windows(2).any(|w| !(w[1] > w[0])) {
        return Err(no_data("gave a non-finite value or a non-increasing frequency grid"));
    }
    Ok(AcResult { frequency, series, wall_ms })
}
