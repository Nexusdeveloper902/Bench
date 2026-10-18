//! Minimal libngspice FFI adapter (electrical-domain boundary).
//!
//! Covers exactly the validated surface: init / circ / command / vector read /
//! current-plot query / all-vectors count. No bg threads, no Sync callbacks in
//! v1. Sequential single-handle discipline: one process-wide Mutex guards every
//! lib call; the handle type is !Send + !Sync.
//!
//! Recovery: when ngspice calls the controlled-exit callback ("cannot recover
//! and awaits to be reset or detached"; e.g. an undefined `{param}` or E/G
//! LAPLACE), the call that triggered it returns Err and the library is
//! unloaded (dlclose); the next call loads and initialises a fresh copy.

use libloading::{Library, Symbol};
use std::collections::HashMap;
use std::ffi::{CStr, CString};
use std::os::raw::{c_char, c_double, c_int, c_void};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};

const LIB_PATH: &str = "/usr/lib/libngspice.so";

#[repr(C)]
struct VectorInfo {
    v_name: *const c_char,
    v_type: c_int,
    v_flags: i16,
    _pad: i16,
    v_realdata: *mut c_double,
    v_compdata: *mut c_void,
    v_length: c_int,
}

type SendCharCb =
    unsafe extern "C" fn(*const c_char, c_int, *mut c_void) -> c_int;
type SendStatCb =
    unsafe extern "C" fn(*const c_char, c_int, *mut c_void) -> c_int;
// NG_BOOL is C++ bool (1 byte); use u8 and convert (never pass Rust bool).
type ControlledExitCb = unsafe extern "C" fn(
    c_int,
    u8,
    u8,
    c_int,
    *mut c_void,
) -> c_int;
type SendDataCb = unsafe extern "C" fn(*mut c_void, c_int, c_int, *mut c_void) -> c_int;
type SendInitDataCb = unsafe extern "C" fn(*mut c_void, c_int, *mut c_void) -> c_int;
type BGThreadRunningCb = unsafe extern "C" fn(u8, c_int, *mut c_void) -> c_int;

struct Lib {
    _lib: Library,
    init: unsafe extern "C" fn(
        SendCharCb,
        SendStatCb,
        ControlledExitCb,
        SendDataCb,
        SendInitDataCb,
        BGThreadRunningCb,
        *mut c_void,
    ) -> c_int,
    command: unsafe extern "C" fn(*const c_char) -> c_int,
    circ: unsafe extern "C" fn(*const *const c_char) -> c_int,
    vec_info: unsafe extern "C" fn(*const c_char) -> *mut VectorInfo,
    cur_plot: unsafe extern "C" fn() -> *const c_char,
    all_vecs: unsafe extern "C" fn(*const c_char) -> *const *const c_char,
    set_bkpt: unsafe extern "C" fn(c_double) -> u8,
}

struct SimState {
    lib: Option<Lib>,
    inited: bool,
}

// Process-wide guard: every lib interaction holds this.
static SIM: OnceLock<Mutex<SimState>> = OnceLock::new();
// Operation guard: held across an entire run_simulation sequence so parallel
// callers (e.g. cargo test threads) serialize whole circ->run->read runs
// instead of interleaving individual commands on the singleton handle.
static OP: OnceLock<Mutex<()>> = OnceLock::new();
// Callback logs live separately so callbacks never contend with SIM.
static LOGS: OnceLock<Mutex<(Vec<String>, Vec<String>)>> = OnceLock::new();
// Set by the controlled-exit callback; consumed after every circ/command.
static EXITED: AtomicBool = AtomicBool::new(false);

fn sim() -> &'static Mutex<SimState> {
    SIM.get_or_init(|| Mutex::new(SimState { lib: None, inited: false }))
}

fn op() -> &'static Mutex<()> {
    OP.get_or_init(|| Mutex::new(()))
}

fn logs() -> &'static Mutex<(Vec<String>, Vec<String>)> {
    LOGS.get_or_init(|| Mutex::new((vec![], vec![])))
}

/// Hold across a full run sequence. Non-reentrant: use `*_inner` helpers inside.
pub fn op_lock() -> std::sync::MutexGuard<'static, ()> {
    op().lock().expect("op lock poisoned")
}

// ---- callbacks: static, never capture, never panic across FFI ----
unsafe extern "C" fn cb_sendchar(msg: *const c_char, _id: c_int, _ud: *mut c_void) -> c_int {
    if msg.is_null() {
        return 0;
    }
    let s = CStr::from_ptr(msg).to_string_lossy().into_owned();
    if let Ok(mut lg) = logs().lock() {
        lg.0.push(s);
        if lg.0.len() > 4000 {
            let drain = lg.0.len() - 4000;
            lg.0.drain(..drain);
        }
    }
    0
}

unsafe extern "C" fn cb_sendstat(msg: *const c_char, _id: c_int, _ud: *mut c_void) -> c_int {
    if msg.is_null() {
        return 0;
    }
    let s = CStr::from_ptr(msg).to_string_lossy().into_owned();
    if let Ok(mut lg) = logs().lock() {
        lg.1.push(s);
    }
    0
}

unsafe extern "C" fn cb_exit(
    _status: c_int,
    _imm: u8,
    _quit: u8,
    _id: c_int,
    _ud: *mut c_void,
) -> c_int {
    EXITED.store(true, Ordering::SeqCst);
    0
}

/// The prefix of ngspice's `vecvalues` this adapter reads (name, real value).
#[repr(C)]
struct VecValues {
    name: *const c_char,
    creal: c_double,
}

#[repr(C)]
struct VecValuesAll {
    count: c_int,
    _index: c_int,
    vecs: *const *const VecValues,
}

/// Points streamed by SendData while a capture is on (live runs: their decks say `.save none`, so ngspice keeps one
/// point per vector and the points exist only here until taken).
struct Capture {
    want: Vec<String>,
    idx: Option<(usize, Vec<Option<usize>>)>, // (time, each wanted vector): resolved on the first point of a run
    time: Vec<f64>,
    data: Vec<Vec<f64>>,
}

fn capture() -> &'static Mutex<Option<Capture>> {
    static C: OnceLock<Mutex<Option<Capture>>> = OnceLock::new();
    C.get_or_init(|| Mutex::new(None))
}

/// Does SendData's vector `name` carry observable `obs`? `v(n)` is `n`, `i(x)` is `x#branch`, others verbatim.
fn vec_is(obs: &str, name: &str) -> bool {
    let inner = |p: &str| obs.strip_prefix(p).and_then(|s| s.strip_suffix(')'));
    obs == name || inner("v(") == Some(name) || inner("i(").is_some_and(|x| name.strip_suffix("#branch") == Some(x))
}

unsafe extern "C" fn cb_senddata(p: *mut c_void, _n: c_int, _id: c_int, _ud: *mut c_void) -> c_int {
    let Ok(mut g) = capture().lock() else { return 0 };
    let (Some(c), false) = (g.as_mut(), p.is_null()) else { return 0 };
    let all = &*(p as *const VecValuesAll);
    if all.count <= 0 || all.vecs.is_null() {
        return 0;
    }
    let vecs = std::slice::from_raw_parts(all.vecs, all.count as usize);
    if c.idx.is_none() {
        let names: Vec<String> = vecs
            .iter()
            .map(|v| if v.is_null() || (**v).name.is_null() { String::new() } else {
                CStr::from_ptr((**v).name).to_string_lossy().to_lowercase()
            })
            .collect();
        let Some(t) = names.iter().position(|n| n == "time") else { return 0 };
        c.idx = Some((t, c.want.iter().map(|o| names.iter().position(|n| vec_is(o, n))).collect()));
    }
    let (t, idx) = c.idx.as_ref().unwrap();
    let val = |i: usize| vecs.get(i).filter(|v| !v.is_null()).map_or(f64::NAN, |v| (**v).creal);
    c.time.push(val(*t));
    for (k, i) in idx.iter().enumerate() {
        if let Some(i) = i {
            c.data[k].push(val(*i));
        }
    }
    0
}

/// Start collecting `observables` (and time) from SendData; any earlier capture is dropped.
pub fn capture_start(observables: &[String]) {
    if let Ok(mut g) = capture().lock() {
        *g = Some(Capture {
            want: observables.iter().map(|o| o.to_lowercase()).collect(),
            idx: None,
            time: vec![],
            data: vec![vec![]; observables.len()],
        });
    }
}

/// The points captured since the last take, like `read_series_from` (an observable SendData does not carry reads
/// as empty).
pub fn capture_take(observables: &[String]) -> (HashMap<String, Vec<f64>>, Vec<f64>) {
    let Ok(mut g) = capture().lock() else { return (HashMap::new(), vec![]) };
    let Some(c) = g.as_mut() else { return (HashMap::new(), vec![]) };
    let found: Vec<bool> = match &c.idx {
        Some((_, idx)) => idx.iter().map(Option::is_some).collect(),
        None => vec![false; observables.len()],
    };
    let out = observables
        .iter()
        .zip(c.data.iter_mut())
        .zip(found)
        .map(|((o, d), f)| (o.clone(), if f { std::mem::take(d) } else { vec![] }))
        .collect();
    (out, std::mem::take(&mut c.time))
}

/// Unload the library (dlclose); the next call loads a fresh copy. A live run's `.save none` sets a process-wide
/// flag in libngspice that nothing resets (outitf.c `savenone`, probed: every later plot keeps one point), so a
/// live run ends with this.
pub fn unload() {
    if let Ok(mut st) = sim().lock() {
        st.lib = None;
        st.inited = false;
    }
}

pub fn capture_stop() {
    if let Ok(mut g) = capture().lock() {
        *g = None;
    }
}

/// `ngSpice_Command(NULL)`: frees the control structures ngspice keeps for every command sent (manual 15.3.2.6).
/// Long live runs send a few commands per chunk; probed: +0.8 kB per chunk without it, flat with it.
pub fn release_commands() -> Result<(), String> {
    load_lib()?;
    let mut st = sim().lock().map_err(|e| format!("sim lock poisoned: {}", e))?;
    let lib = st.lib.as_ref().ok_or("lib not loaded")?;
    unsafe { (lib.command)(std::ptr::null()) };
    after_call(&mut st)
}

unsafe extern "C" fn cb_sendinit(_p: *mut c_void, _n: c_int, _ud: *mut c_void) -> c_int {
    0
}

unsafe extern "C" fn cb_bg(_run: u8, _id: c_int, _ud: *mut c_void) -> c_int {
    0
}

fn load_lib() -> Result<(), String> {
    let mut st = sim().lock().map_err(|e| format!("sim lock poisoned: {}", e))?;
    if st.inited {
        return Ok(());
    }
    unsafe {
        let lib = Library::new(LIB_PATH).map_err(|e| format!("dlopen {}: {}", LIB_PATH, e))?;
        let l: Lib = Lib {
            init: *lib
                .get::<unsafe extern "C" fn(
                    SendCharCb,
                    SendStatCb,
                    ControlledExitCb,
                    SendDataCb,
                    SendInitDataCb,
                    BGThreadRunningCb,
                    *mut c_void,
                ) -> c_int>(b"ngSpice_Init")
                .map_err(|e| format!("ngSpice_Init missing: {}", e))?,
            command: *lib
                .get::<unsafe extern "C" fn(*const c_char) -> c_int>(b"ngSpice_Command")
                .map_err(|e| format!("ngSpice_Command missing: {}", e))?,
            circ: *lib
                .get::<unsafe extern "C" fn(*const *const c_char) -> c_int>(b"ngSpice_Circ")
                .map_err(|e| format!("ngSpice_Circ missing: {}", e))?,
            vec_info: *lib
                .get::<unsafe extern "C" fn(*const c_char) -> *mut VectorInfo>(
                    b"ngGet_Vec_Info",
                )
                .map_err(|e| format!("ngGet_Vec_Info missing: {}", e))?,
            cur_plot: *lib
                .get::<unsafe extern "C" fn() -> *const c_char>(b"ngSpice_CurPlot")
                .map_err(|e| format!("ngSpice_CurPlot missing: {}", e))?,
            all_vecs: *lib
                .get::<unsafe extern "C" fn(*const c_char) -> *const *const c_char>(
                    b"ngSpice_AllVecs",
                )
                .map_err(|e| format!("ngSpice_AllVecs missing: {}", e))?,
            set_bkpt: *lib
                .get::<unsafe extern "C" fn(c_double) -> u8>(b"ngSpice_SetBkpt")
                .map_err(|e| format!("ngSpice_SetBkpt missing: {}", e))?,
            _lib: lib,
        };
        let rc = (l.init)(
            cb_sendchar,
            cb_sendstat,
            cb_exit,
            cb_senddata,
            cb_sendinit,
            cb_bg,
            std::ptr::null_mut(),
        );
        EXITED.store(false, Ordering::SeqCst);
        if rc != 0 {
            return Err(format!("ngSpice_Init rc={}", rc));
        }
        // Kept until ngspice asks to be detached (`after_call`).
        st.lib = Some(l);
        st.inited = true;
        Ok(())
    }
}

fn clear_logs() {
    if let Ok(mut lg) = logs().lock() {
        lg.0.clear();
        lg.1.clear();
    }
}

/// Ensure init (idempotent). Clears per-run logs.
pub fn ensure_init() -> Result<(), String> {
    load_lib()?;
    clear_logs();
    Ok(())
}

/// ngspice's own error lines from the current log (`stderr Error...` and the
/// lines that explain a failed parse), prefix stripped, at most `max`.
pub fn error_lines(max: usize) -> Vec<String> {
    let (log, _) = take_logs();
    log.iter()
        .filter_map(|l| l.strip_prefix("stderr "))
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.to_lowercase().starts_with("warning: vector"))
        .take(max)
        .map(str::to_string)
        .collect()
}

/// After every circ/command: if ngspice fired the controlled-exit callback,
/// unload the library (the next call reloads it) and report the failure.
fn after_call(st: &mut SimState) -> Result<(), String> {
    if !EXITED.swap(false, Ordering::SeqCst) {
        return Ok(());
    }
    st.lib = None; // dlclose: a poisoned library never serves another run
    st.inited = false;
    let why = error_lines(4);
    Err(format!(
        "ngspice aborted and was reloaded: {}",
        if why.is_empty() { "no error text".to_string() } else { why.join(" | ") }
    ))
}

pub fn do_circ(lines: &[String]) -> Result<i32, String> {
    load_lib()?;
    for l in lines {
        if l.contains('\0') {
            return Err("netlist line contains interior NUL (refused: deck would mutate)".to_string());
        }
    }
    let mut st = sim().lock().map_err(|e| format!("sim lock poisoned: {}", e))?;
    let lib = st.lib.as_ref().ok_or("lib not loaded")?;
    let cstrs: Vec<CString> = lines
        .iter()
        .map(|l| CString::new(l.as_str()).unwrap_or_default())
        .collect();
    let mut ptrs: Vec<*const c_char> = cstrs.iter().map(|c| c.as_ptr()).collect();
    ptrs.push(std::ptr::null());
    // Pin the compatibility mode: a ./.spiceinit or ~/.spiceinit read at init
    // may set ngbehavior (ps/lt/...), which changes how the deck is parsed.
    let unset = CString::new("unset ngbehavior").unwrap();
    let rc = unsafe {
        (lib.command)(unset.as_ptr());
        (lib.circ)(ptrs.as_ptr())
    };
    after_call(&mut st)?;
    Ok(rc)
}

pub fn do_command(cmd: &str) -> Result<i32, String> {
    load_lib()?;
    let mut st = sim().lock().map_err(|e| format!("sim lock poisoned: {}", e))?;
    let lib = st.lib.as_ref().ok_or("lib not loaded")?;
    let c = CString::new(cmd).map_err(|e| format!("bad command: {}", e))?;
    let rc = unsafe { (lib.command)(c.as_ptr()) };
    after_call(&mut st)?;
    Ok(rc)
}

pub fn cur_plot_name() -> String {
    let st = match sim().lock() {
        Ok(s) => s,
        Err(_) => return String::new(),
    };
    let lib = match st.lib.as_ref() {
        Some(l) => l,
        None => return String::new(),
    };
    unsafe {
        let p = (lib.cur_plot)();
        if p.is_null() {
            return String::new();
        }
        CStr::from_ptr(p).to_string_lossy().into_owned()
    }
}

/// Read a vector by fully-qualified name. Returns None if missing/empty/complex.
pub fn read_vec_qualified(name: &str) -> Option<Vec<f64>> {
    read_vec_from(name, 0)
}

/// Points `from..` of a vector (live chunks read only what is new). None if missing/empty/complex.
fn read_vec_from(name: &str, from: usize) -> Option<Vec<f64>> {
    let st = sim().lock().ok()?;
    let lib = st.lib.as_ref()?;
    unsafe {
        let c = CString::new(name).ok()?;
        let p = (lib.vec_info)(c.as_ptr());
        if p.is_null() {
            return None;
        }
        let vi = &*p;
        let n = vi.v_length;
        if n <= 0 || vi.v_realdata.is_null() {
            return None; // complex (compdata) or empty -> explicit None
        }
        let n = n as usize;
        if n > 10_000_000 {
            return None;
        }
        let slice = std::slice::from_raw_parts(vi.v_realdata, n);
        Some(slice[from.min(n)..].to_vec())
    }
}

pub fn all_vecs_count(plot: &str) -> usize {
    let st = match sim().lock() {
        Ok(s) => s,
        Err(_) => return 0,
    };
    let lib = match st.lib.as_ref() {
        Some(l) => l,
        None => return 0,
    };
    unsafe {
        let c = match CString::new(plot) {
            Ok(c) => c,
            Err(_) => return 0,
        };
        let p = (lib.all_vecs)(c.as_ptr());
        if p.is_null() {
            return 0;
        }
        let mut n = 0;
        let mut i = 0;
        loop {
            let s = *p.add(i);
            if s.is_null() {
                break;
            }
            n += 1;
            i += 1;
            if i > 100000 {
                break;
            }
        }
        n
    }
}

/// Names of every vector in `plot` (nodes, `x#branch` currents, saved `@dev[param]`s, `time`).
pub fn vec_names(plot: &str) -> Vec<String> {
    let Ok(st) = sim().lock() else { return vec![] };
    let Some(lib) = st.lib.as_ref() else { return vec![] };
    let Ok(c) = CString::new(plot) else { return vec![] };
    let mut out = vec![];
    unsafe {
        let p = (lib.all_vecs)(c.as_ptr());
        if p.is_null() {
            return out;
        }
        let mut i = 0;
        while i < 100_000 {
            let s = *p.add(i);
            if s.is_null() {
                break;
            }
            out.push(CStr::from_ptr(s).to_string_lossy().into_owned());
            i += 1;
        }
    }
    out
}

/// Read observable + time from the CURRENT plot (stale-plot safe).
pub fn read_series(observables: &[String]) -> (HashMap<String, Vec<f64>>, Vec<f64>) {
    read_series_from(observables, 0)
}

/// `read_series`, points `from..` only (a missing vector reads as empty).
pub fn read_series_from(observables: &[String], from: usize) -> (HashMap<String, Vec<f64>>, Vec<f64>) {
    let plot = cur_plot_name();
    let mut out: HashMap<String, Vec<f64>> = HashMap::new();
    for v in observables {
        let mut data: Option<Vec<f64>> = None;
        if !plot.is_empty() {
            data = read_vec_from(&format!("{}.{}", plot, v), from);
        }
        if data.is_none() {
            data = read_vec_from(v, from);
        }
        out.insert(v.clone(), data.unwrap_or_default());
    }
    let mut t: Vec<f64> = vec![];
    if !plot.is_empty() {
        if let Some(v) = read_vec_from(&format!("{}.time", plot), from) {
            t = v;
        }
    }
    if t.is_empty() {
        if let Some(v) = read_vec_from("time", from) {
            t = v;
        }
    }
    (out, t)
}

/// Take and clear the ngspice output log (live runs scan each chunk's own lines).
pub fn drain_logs() -> Vec<String> {
    match logs().lock() {
        Ok(mut lg) => {
            lg.1.clear();
            std::mem::take(&mut lg.0)
        }
        Err(_) => vec![],
    }
}

pub fn take_logs() -> (Vec<String>, Vec<String>) {
    match logs().lock() {
        Ok(lg) => (lg.0.clone(), lg.1.clone()),
        Err(_) => (vec![], vec![]),
    }
}

/// Install a solver breakpoint (same-grid reference runs). Must be called
/// while no analysis is in progress.
pub fn set_bkpt(t: f64) -> Result<bool, String> {
    load_lib()?;
    let st = sim().lock().map_err(|e| format!("sim lock poisoned: {}", e))?;
    let lib = st.lib.as_ref().ok_or("lib not loaded")?;
    let rc = unsafe { (lib.set_bkpt)(t as c_double) };
    Ok(rc != 0)
}

/// Best-effort teardown so sequential sims on the singleton handle stay
/// isolated: clear stops, destroy circuits. Errors ignored (teardown).
pub fn teardown() {
    let _ = do_command("stop clear");
    let _ = do_command("destroy all");
    let _ = do_command("delete all");
    clear_logs(); // teardown chatter is not part of the next run's log
}
