//! Minimal libngspice FFI adapter (electrical-domain boundary).
//!
//! Covers exactly the validated surface: init / circ / command / vector read /
//! current-plot query / all-vectors count. No bg threads, no Sync callbacks in
//! v1. Sequential single-handle discipline: one process-wide Mutex guards every
//! lib call; the handle type is !Send + !Sync. Library is never unloaded.

use libloading::{Library, Symbol};
use std::collections::HashMap;
use std::ffi::{CStr, CString};
use std::os::raw::{c_char, c_double, c_int, c_void};
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
    0
}

unsafe extern "C" fn cb_senddata(_p: *mut c_void, _n: c_int, _id: c_int, _ud: *mut c_void) -> c_int {
    0
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
        // Never unload: leak the handle so callbacks/static state stay valid.
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
        if rc != 0 {
            return Err(format!("ngSpice_Init rc={}", rc));
        }
        // Leak the Library itself (mem::forget via Option dance): keep inside
        // SimState but never Drop it -- process-lifetime singleton.
        st.lib = Some(l);
        st.inited = true;
        // Prevent Library Drop from ever running: SimState lives in a
        // OnceLock statics and is never destroyed before process exit.
        Ok(())
    }
}

/// Ensure init (idempotent). Clears per-run logs.
pub fn ensure_init() -> Result<(), String> {
    load_lib()?;
    if let Ok(mut lg) = logs().lock() {
        lg.0.clear();
        lg.1.clear();
    }
    Ok(())
}

pub fn do_circ(lines: &[String]) -> Result<i32, String> {
    load_lib()?;
    for l in lines {
        if l.contains('\0') {
            return Err("netlist line contains interior NUL (refused: deck would mutate)".to_string());
        }
    }
    let st = sim().lock().map_err(|e| format!("sim lock poisoned: {}", e))?;
    let lib = st.lib.as_ref().ok_or("lib not loaded")?;
    let cstrs: Vec<CString> = lines
        .iter()
        .map(|l| CString::new(l.as_str()).unwrap_or_default())
        .collect();
    let mut ptrs: Vec<*const c_char> = cstrs.iter().map(|c| c.as_ptr()).collect();
    ptrs.push(std::ptr::null());
    let rc = unsafe { (lib.circ)(ptrs.as_ptr()) };
    Ok(rc)
}

pub fn do_command(cmd: &str) -> Result<i32, String> {
    load_lib()?;
    let st = sim().lock().map_err(|e| format!("sim lock poisoned: {}", e))?;
    let lib = st.lib.as_ref().ok_or("lib not loaded")?;
    let c = CString::new(cmd).map_err(|e| format!("bad command: {}", e))?;
    let rc = unsafe { (lib.command)(c.as_ptr()) };
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
        Some(slice.to_vec())
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

/// Read observable + time from the CURRENT plot (stale-plot safe).
pub fn read_series(observables: &[String]) -> (HashMap<String, Vec<f64>>, Vec<f64>) {
    let plot = cur_plot_name();
    let mut out: HashMap<String, Vec<f64>> = HashMap::new();
    for v in observables {
        let mut data: Option<Vec<f64>> = None;
        if !plot.is_empty() {
            data = read_vec_qualified(&format!("{}.{}", plot, v));
        }
        if data.is_none() {
            data = read_vec_qualified(v);
        }
        out.insert(v.clone(), data.unwrap_or_default());
    }
    let mut t: Vec<f64> = vec![];
    if !plot.is_empty() {
        if let Some(v) = read_vec_qualified(&format!("{}.time", plot)) {
            t = v;
        }
    }
    if t.is_empty() {
        if let Some(v) = read_vec_qualified("time") {
            t = v;
        }
    }
    (out, t)
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
}
