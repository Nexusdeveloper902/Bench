//! Live transient runs: values change while the run is in progress and results stream out in chunks.
//!
//! Mechanism (probed, see docs/plans/ngspice-library/LIVE.md): ngspice's native stop/resume, the same path as the
//! batch pause schedule. The run pauses at every chunk boundary (`stop when time = T`, a solver breakpoint; every
//! pause is verified exact). Pending changes are applied there with `alter` and read back, then `resume`. A change
//! therefore takes effect exactly at a recorded boundary time, and the next step starts first-order from a
//! breakpoint, so the response has no artefact beyond the physical step. The change log replays as one blocking
//! run (`replay`) with pauses only at the change times.
//!
//! One engine: a live run claims it from `LiveSim::start` until it finishes (done, cancelled, failed or dropped);
//! a batch run asked for meanwhile is refused (`refuse_if_live`). Every ngspice call goes through the adapter, so
//! the task-2 guarantees hold per chunk: a poisoned library is reloaded and the chunk fails, `stderr Error` lines
//! and solver failures are errors, an empty or non-finite grid is an error.

use crate::error::SimError;
use crate::kernel::{apply_tmax_policy, check_observables, gates, nonfinite_or_short, parse_tstop, pause_exact,
                    refuse_unsaved_device_observables, scan_loud_failure, with_reason, SimResult};
use crate::ngspice;
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Condvar, Mutex};
use std::time::{Duration, Instant};

static ACTIVE: AtomicBool = AtomicBool::new(false);

/// Batch runs call this under the op lock: the engine belongs to the live run until it ends.
pub(crate) fn refuse_if_live() -> Result<(), SimError> {
    if ACTIVE.load(Ordering::SeqCst) {
        return Err(SimError::Lifecycle {
            message: "a live run is active: stop it (cancel or close) before starting another run".to_string(),
        });
    }
    Ok(())
}

/// One applied change: from sim time `t` on, `device` (ngspice's flattened name) has `value`.
#[derive(Debug, Clone, PartialEq)]
pub struct Change {
    pub t: f64,
    pub device: String,
    pub value: f64,
}

/// Points computed since the previous chunk. `changes` were applied at the start of this chunk (their `t` is the
/// previous chunk's end). `wall`: active wall seconds since the first chunk (paused time excluded, pacing
/// included); `rtf`: sim seconds per wall second achieved so far.
#[derive(Debug, Clone)]
pub struct Chunk {
    pub time: Vec<f64>,
    pub series: HashMap<String, Vec<(f64, f64)>>,
    pub changes: Vec<Change>,
    pub t_end: f64,
    pub done: bool,
    pub wall: f64,
    pub rtf: f64,
    /// Every `LiveRun::set` call numbered up to this one was applied at or before this chunk's start.
    pub seq: u64,
}

fn backend(e: String) -> SimError {
    SimError::BackendFailure { message: e }
}

fn loud(hint: String) -> SimError {
    let l = hint.to_lowercase();
    if l.contains("timestep too small") || l.contains("no convergence") {
        SimError::ConvergenceFailure { message: hint }
    } else {
        SimError::SolverFailure { message: hint.clone(), log_hint: hint }
    }
}

fn config(message: String) -> SimError {
    SimError::InvalidConfig { message }
}

/// The parameter `alter <dev> = v` sets, read back as `@dev[param]`: V/I sources their DC value, R its resistance.
fn param_of(dev: &str) -> Result<&'static str, SimError> {
    let ok = !dev.is_empty() && dev.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '.');
    match dev.chars().next() {
        Some('v') | Some('i') if ok => Ok("dc"),
        Some('r') if ok => Ok("resistance"),
        _ => Err(config(format!(
            "'{}' cannot be changed live: only V and I sources (DC value) and R (resistance) can, named as ngspice \
             flattens them (lower case, e.g. v.xrl1.vstim)",
            dev
        ))),
    }
}

/// A source whose own line has a time function ignores a DC alter during a transient: refuse it rather than
/// accept a change that does nothing. The device line is found by its local name (the last dotted segment).
fn check_dc_only(lines: &[String], dev: &str) -> Result<(), SimError> {
    let local = dev.rsplit('.').next().unwrap_or(dev);
    let func = regex::Regex::new(r"\b(pulse|sin|pwl|exp|sffm|am|trnoise|trrandom|external)\b").unwrap();
    let braces = regex::Regex::new(r"\{[^}]*\}").unwrap();
    for l in lines.iter().skip(1) {
        let low = l.trim().to_lowercase();
        if low.split_whitespace().next() == Some(local) && func.is_match(&braces.replace_all(&low, "")) {
            return Err(config(format!("'{}' has a time function ({}); only a DC value can be changed live", dev, l.trim())));
        }
    }
    Ok(())
}

/// `print @dev[param]` -> its value (ngGet_Vec_Info on a device parameter crashes ngspice 47; `print` does not).
fn readback(dev: &str) -> Result<f64, SimError> {
    let name = format!("@{}[{}]", dev, param_of(dev)?);
    ngspice::do_command(&format!("print {}", name)).map_err(backend)?;
    let log = ngspice::drain_logs();
    let want = format!("{} = ", name);
    log.iter()
        .find_map(|l| l.strip_prefix("stdout ").and_then(|s| s.trim().strip_prefix(want.as_str())))
        .and_then(|v| v.trim().parse::<f64>().ok())
        .ok_or_else(|| {
            let why: Vec<&str> = log.iter().filter_map(|l| l.strip_prefix("stderr ")).map(str::trim).collect();
            config(format!("live device '{}' is not in the circuit: {}", dev, why.join(" | ")))
        })
}

/// One live transient on the singleton engine (the kernel layer: no threads, no pacing; see `LiveRun`).
pub struct LiveSim {
    obs: Vec<String>,
    devices: HashSet<String>,
    tstop: f64,
    t_now: f64,
    read: usize,
    started: bool,
    open: bool,
}

impl LiveSim {
    /// Gate, load and claim. `devices`: the ngspice device names that may change during the run (each must exist
    /// and be a DC source or a resistor). Errors are the batch path's typed errors; a batch run is refused while
    /// this run is open, and starting a second live run is refused too.
    pub fn start(netlist: &[String], observables: &[String], devices: &[String]) -> Result<LiveSim, SimError> {
        let _op = ngspice::op_lock();
        refuse_if_live()?;
        gates(netlist)?;
        let lines = apply_tmax_policy(netlist.to_vec())?;
        let tstop = parse_tstop(&lines).ok_or_else(|| config("a live run needs a .tran line with a stop time".into()))?;
        // the batch pause fence, minus XSPICE A devices (probed: same events, see LIVE.md)
        if lines.iter().skip(1).any(|l| {
            let s = l.trim().to_lowercase();
            s.starts_with(".osdi") || s.starts_with('o') || s.starts_with('u')
        }) {
            return Err(SimError::Unavailable {
                message: "live runs are not available with O/U transmission lines or OSDI devices".to_string(),
            });
        }
        refuse_unsaved_device_observables(&lines, observables)?;
        let mut devs = HashSet::new();
        for d in devices {
            let d = d.to_lowercase();
            param_of(&d)?;
            check_dc_only(&lines, &d)?;
            devs.insert(d);
        }
        ngspice::ensure_init().map_err(backend)?;
        ngspice::teardown();
        ACTIVE.store(true, Ordering::SeqCst);
        let mut sim = LiveSim { obs: observables.to_vec(), devices: devs, tstop, t_now: 0.0, read: 0, started: false, open: true };
        match sim.load(&lines) {
            Ok(()) => Ok(sim),
            Err(e) => {
                sim.shutdown();
                Err(e)
            }
        }
    }

    fn load(&mut self, lines: &[String]) -> Result<(), SimError> {
        let rc = ngspice::do_circ(lines).map_err(backend)?;
        if rc != 0 {
            return Err(backend(with_reason(format!("circ rc={}", rc))));
        }
        if let Some(hint) = scan_loud_failure(&ngspice::drain_logs()) {
            return Err(loud(hint));
        }
        for d in &self.devices {
            readback(d)?;
        }
        Ok(())
    }

    pub fn tstop(&self) -> f64 {
        self.tstop
    }

    /// Sim time reached (the last chunk's end; 0 before the first chunk).
    pub fn t_now(&self) -> f64 {
        self.t_now
    }

    /// Apply `apply` at the current time, then run to `until` (to the end when `until` is at or past tstop).
    /// The run ends (engine released) on error and after the last chunk.
    pub fn advance(&mut self, apply: &[(String, f64)], until: f64) -> Result<Chunk, SimError> {
        let _op = ngspice::op_lock();
        if !self.open {
            return Err(SimError::Lifecycle { message: "the live run has ended".to_string() });
        }
        let r = self.advance_locked(apply, until);
        if r.as_ref().map_or(true, |c| c.done) {
            self.shutdown();
        }
        r
    }

    fn advance_locked(&mut self, apply: &[(String, f64)], until: f64) -> Result<Chunk, SimError> {
        let tol = |t: f64| (1e-12f64).max(t.abs() * 1e-9);
        if !until.is_finite() || until <= self.t_now + 4.0 * tol(until) {
            return Err(config(format!("live chunk end {} must be after {}", until, self.t_now)));
        }
        let last = until >= self.tstop - 4.0 * tol(self.tstop);
        let mut changes = vec![];
        for (dev, v) in apply {
            let dev = dev.to_lowercase();
            if !self.devices.contains(&dev) {
                return Err(config(format!("'{}' was not declared live when the run started", dev)));
            }
            if !v.is_finite() {
                return Err(config(format!("live value for '{}' must be finite", dev)));
            }
            ngspice::do_command(&format!("alter {} = {:.17e}", dev, v)).map_err(backend)?;
            let got = readback(&dev)?; // `print` shows 7 significant digits
            if (got - v).abs() > 1e-6 * v.abs() {
                return Err(backend(format!("alter {} = {} did not take effect (reads {})", dev, v, got)));
            }
            changes.push(Change { t: self.t_now, device: dev, value: *v });
        }
        if self.started {
            // fired stops stay listed and are rescanned every step; `delete all` while paused keeps saved vectors
            // (probed). Never before the first `run`: it would drop the deck's .save entries.
            ngspice::do_command("delete all").map_err(backend)?;
        }
        if !last {
            ngspice::do_command(&format!("stop when time = {:.17e}", until)).map_err(backend)?;
        }
        let rc = ngspice::do_command(if self.started { "resume" } else { "run" }).map_err(backend)?;
        self.started = true;
        if rc != 0 {
            return Err(backend(with_reason(format!("live run rc={}", rc))));
        }
        let log = ngspice::drain_logs();
        if let Some(hint) = scan_loud_failure(&log) {
            return Err(loud(hint));
        }
        let target = if last { self.tstop } else { until };
        let (raw, t) = ngspice::read_series_from(&self.obs, self.read);
        match t.last() {
            Some(&end) if pause_exact(end, target) => {}
            got => {
                return Err(SimError::SolverFailure {
                    message: format!("live run stopped at {:?}, expected {}", got, target),
                    log_hint: log.join("\n"),
                })
            }
        }
        let series: HashMap<String, Vec<(f64, f64)>> = self
            .obs
            .iter()
            .map(|o| {
                let d = raw.get(o).cloned().unwrap_or_default();
                (o.clone(), t.iter().copied().zip(d).collect())
            })
            .collect();
        check_observables(&series, &t)?;
        if nonfinite_or_short(&t, &raw) || (self.read > 0 && t[0] <= self.t_now) {
            return Err(SimError::SolverFailure {
                message: "non-finite or non-monotonic grid in a live chunk".to_string(),
                log_hint: log.join("\n"),
            });
        }
        self.read += t.len();
        self.t_now = target;
        Ok(Chunk { time: t, series, changes, t_end: target, done: last, wall: 0.0, rtf: 0.0, seq: 0 })
    }

    /// Release the engine (op lock held by the caller).
    fn shutdown(&mut self) {
        if self.open {
            ngspice::teardown();
            self.open = false;
            ACTIVE.store(false, Ordering::SeqCst);
        }
    }

    /// End the run now and release the engine (blocks until a chunk in progress elsewhere has finished).
    pub fn finish(&mut self) {
        if self.open {
            let _op = ngspice::op_lock();
            self.shutdown();
        }
    }
}

impl Drop for LiveSim {
    fn drop(&mut self) {
        self.finish();
    }
}

/// Replay a change log as one blocking run: pauses only at the change times, each change applied there. With the
/// log of a live run it reproduces that run's waveform (within solver tolerance: the live run also paused at its
/// chunk boundaries). Changes at t = 0 apply before the run starts.
pub fn replay(netlist: &[String], observables: &[String], changes: &[Change]) -> Result<SimResult, SimError> {
    let mut log = changes.to_vec();
    if log.iter().any(|c| !(c.t.is_finite() && c.t >= 0.0)) {
        return Err(config("change times must be finite and >= 0".into()));
    }
    log.sort_by(|a, b| a.t.total_cmp(&b.t)); // stable: same-time changes keep their order
    let devices: Vec<String> = log.iter().map(|c| c.device.clone()).collect();
    let wall = Instant::now();
    let mut sim = LiveSim::start(netlist, observables, &devices)?;
    if log.last().is_some_and(|c| c.t >= sim.tstop()) {
        return Err(config(format!("change times must be before the stop time {}", sim.tstop())));
    }
    let mut time = vec![];
    let mut series: HashMap<String, Vec<(f64, f64)>> = observables.iter().map(|o| (o.clone(), vec![])).collect();
    let mut i = 0;
    loop {
        let t0 = sim.t_now();
        let mut apply = vec![];
        while i < log.len() && log[i].t <= t0 {
            apply.push((log[i].device.clone(), log[i].value));
            i += 1;
        }
        let until = log.get(i).map_or(sim.tstop(), |c| c.t);
        let c = sim.advance(&apply, until)?;
        time.extend(c.time);
        for (k, v) in c.series {
            series.entry(k).or_default().extend(v);
        }
        if c.done {
            break;
        }
    }
    Ok(SimResult {
        npts: time.len(),
        time,
        series,
        wall_ms: wall.elapsed().as_secs_f64() * 1e3,
        used_pauses: true,
        fallback_reason: None,
        warnings: vec![],
    })
}

#[derive(Default)]
struct Ctl {
    pending: Vec<(String, f64)>,
    paused: bool,
    cancel: bool,
    end: Option<&'static str>,
    log: Vec<Change>,
    seq: u64,
}

/// A live run with its controls: the worker thread iterates `next_chunk`; any thread calls `set`, `pause`,
/// `resume`, `cancel`. The controls only touch a small state lock, never the engine, so they never wait for a
/// chunk. Pacing: with `rtf` set, sim time is held to rtf x wall time (paused time excluded).
pub struct LiveRun {
    sim: Mutex<Option<LiveSim>>,
    devices: HashSet<String>,
    ctl: Mutex<Ctl>,
    cv: Condvar,
    chunk: f64,
    rtf: Option<f64>,
    clock: Mutex<Option<(Instant, Duration)>>, // (first chunk start, paused total)
    count: AtomicU64,                          // chunks started: boundaries are k * chunk, never a running sum
}

impl LiveRun {
    /// `chunk`: sim seconds per chunk; default tstop/100, and no more than 50 ms of wall time when paced.
    /// It is also the latency of a change: a change made during a chunk applies at that chunk's end.
    pub fn start(netlist: &[String], observables: &[String], devices: &[String], chunk: Option<f64>,
                 rtf: Option<f64>) -> Result<LiveRun, SimError> {
        if rtf.is_some_and(|r| !(r.is_finite() && r > 0.0)) {
            return Err(config("realtime factor must be a positive number".into()));
        }
        let sim = LiveSim::start(netlist, observables, devices)?;
        let tstop = sim.tstop();
        let chunk = chunk.unwrap_or_else(|| rtf.map_or(tstop / 100.0, |r| (tstop / 100.0).min(0.05 * r)));
        if !(chunk.is_finite() && chunk > 0.0 && tstop / chunk <= 100_000.0) {
            return Err(config(format!("chunk must be positive and at least tstop/100000 (got {})", chunk)));
        }
        Ok(LiveRun {
            devices: sim.devices.clone(),
            sim: Mutex::new(Some(sim)),
            ctl: Mutex::new(Ctl::default()),
            cv: Condvar::new(),
            chunk,
            rtf,
            clock: Mutex::new(None),
            count: AtomicU64::new(0),
        })
    }

    fn ctl(&self) -> std::sync::MutexGuard<'_, Ctl> {
        self.ctl.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Queue changes for the next chunk boundary (a later value for the same device replaces a queued one).
    /// -> this call's number; a chunk whose `seq` is at least that applied it.
    pub fn set(&self, changes: &[(String, f64)]) -> Result<u64, SimError> {
        let mut c = self.ctl();
        if c.end.is_some() {
            return Err(SimError::Lifecycle { message: "the live run has ended".to_string() });
        }
        for (d, v) in changes {
            let d = d.to_lowercase();
            if !self.devices.contains(&d) {
                return Err(config(format!("'{}' was not declared live when the run started", d)));
            }
            if !v.is_finite() {
                return Err(config(format!("live value for '{}' must be finite", d)));
            }
        }
        for (d, v) in changes {
            let d = d.to_lowercase();
            c.pending.retain(|(p, _)| *p != d);
            c.pending.push((d, *v));
        }
        c.seq += 1;
        Ok(c.seq)
    }

    pub fn pause(&self) {
        self.ctl().paused = true;
    }

    pub fn resume(&self) {
        self.ctl().paused = false;
        self.cv.notify_all();
    }

    /// Ask the run to stop; the worker's next (or current, if paused) `next_chunk` ends it and returns None.
    pub fn cancel(&self) {
        self.ctl().cancel = true;
        self.cv.notify_all();
    }

    /// "running" | "paused" | "done" | "cancelled" | "failed".
    pub fn state(&self) -> &'static str {
        let c = self.ctl();
        c.end.unwrap_or(if c.paused { "paused" } else { "running" })
    }

    /// Every change applied so far, in order: the log `replay` takes.
    pub fn changes(&self) -> Vec<Change> {
        self.ctl().log.clone()
    }

    fn end(&self, slot: &mut Option<LiveSim>, why: &'static str) {
        if let Some(mut s) = slot.take() {
            s.finish();
        }
        let mut c = self.ctl();
        c.end.get_or_insert(why);
        c.pending.clear();
    }

    /// End the run now (blocks until a chunk in progress has finished). Idempotent.
    pub fn close(&self) {
        let mut slot = self.sim.lock().unwrap_or_else(|e| e.into_inner());
        self.end(&mut slot, "cancelled");
    }

    /// Compute the next chunk: blocks while paused, applies queued changes, runs, then sleeps for pacing.
    /// Ok(None) once the run has ended (done, cancelled, or after an error, which is returned once).
    pub fn next_chunk(&self) -> Result<Option<Chunk>, SimError> {
        let mut slot = self.sim.lock().unwrap_or_else(|e| e.into_inner());
        let Some(sim) = slot.as_mut() else { return Ok(None) };
        let (pending, seq) = {
            let mut c = self.ctl();
            let waited = Instant::now();
            let was_paused = c.paused;
            while c.paused && !c.cancel {
                c = self.cv.wait(c).unwrap_or_else(|e| e.into_inner());
            }
            if was_paused {
                if let Some((_, p)) = self.clock.lock().unwrap().as_mut() {
                    *p += waited.elapsed();
                }
            }
            if c.cancel {
                drop(c);
                self.end(&mut slot, "cancelled");
                return Ok(None);
            }
            (std::mem::take(&mut c.pending), c.seq)
        };
        let (start, _) = *self.clock.lock().unwrap().get_or_insert((Instant::now(), Duration::ZERO));
        let k = self.count.fetch_add(1, Ordering::SeqCst) + 1;
        let until = (k as f64 * self.chunk).min(sim.tstop());
        let mut chunk = match sim.advance(&pending, until) {
            Ok(c) => c,
            Err(e) => {
                self.end(&mut slot, "failed");
                return Err(e);
            }
        };
        let active = || start.elapsed().saturating_sub(self.clock.lock().unwrap().unwrap().1).as_secs_f64();
        if let Some(r) = self.rtf {
            let ahead = chunk.t_end / r - active();
            if ahead > 0.0 {
                let c = self.ctl();
                let _ = self.cv.wait_timeout_while(c, Duration::from_secs_f64(ahead), |c| !c.cancel);
            }
        }
        chunk.seq = seq;
        chunk.wall = active();
        chunk.rtf = if chunk.wall > 0.0 { chunk.t_end / chunk.wall } else { f64::INFINITY };
        self.ctl().log.extend(chunk.changes.iter().cloned());
        if chunk.done {
            slot.take(); // already released by advance
            self.ctl().end = Some("done");
        }
        Ok(Some(chunk))
    }
}
