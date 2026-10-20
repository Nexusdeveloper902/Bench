//! Production simulation core.
//!
//! Dependency direction (enforced):
//! frontend -> Simulation API -> kernel -> electrical domain -> ngspice adapter.
//! The core never depends on GUI, rendering, or frontend event loops.

pub mod bsource;
pub mod components;
pub mod error;
pub mod kernel;
pub mod live;
pub mod ngspice;
pub mod thin;
pub mod validate;

pub use error::SimError;
pub use kernel::{run_ac, run_simulation, pause_exact, parse_tstop, AcResult, SimRequest, SimResult};

use pyo3::create_exception;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::HashMap;

create_exception!(_benchcore, InvalidCircuitError, pyo3::exceptions::PyValueError);
create_exception!(_benchcore, InvalidConfigError, pyo3::exceptions::PyValueError);
create_exception!(_benchcore, ConvergenceError, pyo3::exceptions::PyRuntimeError);
create_exception!(_benchcore, SolverError, pyo3::exceptions::PyRuntimeError);
create_exception!(_benchcore, BackendError, pyo3::exceptions::PyRuntimeError);
create_exception!(_benchcore, UnsupportedError, pyo3::exceptions::PyRuntimeError);
create_exception!(_benchcore, UnavailableError, pyo3::exceptions::PyRuntimeError);

fn map_err(e: SimError) -> PyErr {
    match e {
        SimError::InvalidCircuit { code, message } => {
            InvalidCircuitError::new_err(format!("[{}] {}", code, message))
        }
        SimError::InvalidConfig { message } => InvalidConfigError::new_err(message),
        SimError::ConvergenceFailure { message } => ConvergenceError::new_err(message),
        SimError::SolverFailure { message, .. } => SolverError::new_err(message),
        SimError::Unsupported { message } => UnsupportedError::new_err(message),
        SimError::BackendFailure { message } => BackendError::new_err(message),
        SimError::Lifecycle { message } => BackendError::new_err(message),
        SimError::Unavailable { message } => UnavailableError::new_err(message),
    }
}

/// run_simulation(netlist, observables, stops) -> dict.
/// Raises typed errors on hard failures; fallback metadata
/// (used_pauses/fallback_reason) is returned, never silent.
#[pyfunction(name = "run_simulation")]
#[pyo3(signature = (netlist, observables, stops=None))]
fn py_run_simulation<'py>(
    py: Python<'py>,
    netlist: Vec<String>,
    observables: Vec<String>,
    stops: Option<Vec<f64>>,
) -> PyResult<Bound<'py, PyDict>> {
    let req = SimRequest {
        netlist,
        observables,
        stops: stops.unwrap_or_default(),
    };
    // Release the GIL across the (millisecond-scale, mutex-serialized) sim.
    let res = py.detach(|| run_simulation(req)).map_err(map_err)?;
    result_dict(py, res)
}

fn result_dict(py: Python<'_>, res: SimResult) -> PyResult<Bound<'_, PyDict>> {
    let d = PyDict::new(py);
    let series_d = PyDict::new(py);
    for (k, v) in &res.series {
        let pairs: Vec<(f64, f64)> = v.clone();
        series_d.set_item(k, pairs)?;
    }
    d.set_item("series", series_d)?;
    d.set_item("time", PyList::new(py, res.time.clone())?)?;
    d.set_item("npts", res.npts as u64)?;
    d.set_item("wall_ms", res.wall_ms)?;
    d.set_item("used_pauses", res.used_pauses)?;
    d.set_item("warnings", PyList::new(py, res.warnings.clone())?)?;
    match res.fallback_reason {
        Some(s) => d.set_item("fallback_reason", s)?,
        None => d.set_item("fallback_reason", py.None())?,
    };
    Ok(d)
}

/// run_ac(netlist, observables) -> {"frequency": [Hz], "series": {name: [(re, im)]}, "npts", "wall_ms"}.
/// The deck carries exactly one `.ac dec|oct|lin points fstart fstop` card; observables are v(node) / i(vsrc).
#[pyfunction(name = "run_ac")]
fn py_run_ac<'py>(py: Python<'py>, netlist: Vec<String>, observables: Vec<String>) -> PyResult<Bound<'py, PyDict>> {
    let res = py.detach(|| kernel::run_ac(&netlist, &observables)).map_err(map_err)?;
    let d = PyDict::new(py);
    let series = PyDict::new(py);
    for (k, v) in res.series {
        series.set_item(k, v)?;
    }
    d.set_item("npts", res.frequency.len() as u64)?;
    d.set_item("frequency", res.frequency)?;
    d.set_item("series", series)?;
    d.set_item("wall_ms", res.wall_ms)?;
    Ok(d)
}

fn changes_list(changes: &[live::Change]) -> Vec<(f64, String, f64)> {
    changes.iter().map(|c| (c.t, c.device.clone(), c.value)).collect()
}

/// LiveRun(netlist, observables, devices=[], chunk=None, realtime=None): a transient that runs while values
/// change. Iterate it on a worker thread (each step computes one chunk with the GIL released); call set/pause/
/// resume/cancel from any thread (they never wait for the engine). See docs/plans/ngspice-library/LIVE.md.
#[pyclass(name = "LiveRun", frozen)]
struct PyLiveRun(live::LiveRun);

#[pymethods]
impl PyLiveRun {
    #[new]
    #[pyo3(signature = (netlist, observables, devices=vec![], chunk=None, realtime=None))]
    fn new(py: Python<'_>, netlist: Vec<String>, observables: Vec<String>, devices: Vec<String>, chunk: Option<f64>,
           realtime: Option<f64>) -> PyResult<Self> {
        py.detach(|| live::LiveRun::start(&netlist, &observables, &devices, chunk, realtime))
            .map(PyLiveRun)
            .map_err(map_err)
    }

    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    /// -> {"time", "series", "changes", "t", "done", "wall", "rtf", "seq"}; StopIteration when the run has ended.
    fn __next__<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
        let Some(c) = py.detach(|| self.0.next_chunk()).map_err(map_err)? else { return Ok(None) };
        let d = PyDict::new(py);
        let series = PyDict::new(py);
        for (k, v) in c.series {
            series.set_item(k, v)?;
        }
        d.set_item("time", c.time)?;
        d.set_item("series", series)?;
        d.set_item("changes", changes_list(&c.changes))?;
        d.set_item("t", c.t_end)?;
        d.set_item("done", c.done)?;
        d.set_item("wall", c.wall)?;
        d.set_item("rtf", c.rtf)?;
        d.set_item("seq", c.seq)?;
        Ok(Some(d))
    }

    /// set({device: value}) -> int: applied together at the next chunk boundary, by the chunk whose "seq" is at
    /// least the returned number.
    fn set(&self, changes: HashMap<String, f64>) -> PyResult<u64> {
        self.0.set(&changes.into_iter().collect::<Vec<_>>()).map_err(map_err)
    }

    fn pause(&self) {
        self.0.pause()
    }

    fn resume(&self) {
        self.0.resume()
    }

    fn cancel(&self) {
        self.0.cancel()
    }

    /// End now and release the engine; waits for a chunk in progress (GIL released). Idempotent.
    fn close(&self, py: Python<'_>) {
        py.detach(|| self.0.close())
    }

    #[getter]
    fn state(&self) -> &'static str {
        self.0.state()
    }

    /// [(t, device, value)] applied so far: the log replay() takes.
    #[getter]
    fn changes(&self) -> Vec<(f64, String, f64)> {
        changes_list(&self.0.changes())
    }
}

/// replay(netlist, observables, changes) -> dict like run_simulation's: the change log [(t, device, value)] as one
/// blocking run, paused only at the change times. Refused while a live run is active.
#[pyfunction(name = "replay")]
fn py_replay<'py>(py: Python<'py>, netlist: Vec<String>, observables: Vec<String>,
                  changes: Vec<(f64, String, f64)>) -> PyResult<Bound<'py, PyDict>> {
    let log: Vec<live::Change> = changes.into_iter().map(|(t, device, value)| live::Change { t, device, value }).collect();
    let res = py.detach(|| live::replay(&netlist, &observables, &log)).map_err(map_err)?;
    result_dict(py, res)
}

/// validate(netlist) -> list of {code, class, severity, message, nodes, devices, layer, scope}.
/// class: syntax | dependency | connectivity | unsupported | limited | value. scope: "" top level,
/// ".subckt <name>" a definition, or an instance path such as "x1.xa".
#[pyfunction(name = "validate")]
fn py_validate<'py>(py: Python<'py>, netlist: Vec<String>) -> PyResult<Bound<'py, PyList>> {
    let out = PyList::empty(py);
    for f in validate::validate(&netlist) {
        let d = PyDict::new(py);
        d.set_item("class", validate::class_of(&f.code))?;
        d.set_item("scope", f.scope)?;
        d.set_item("code", f.code)?;
        d.set_item("severity", f.severity)?;
        d.set_item("message", f.message)?;
        d.set_item("nodes", f.nodes)?;
        d.set_item("devices", f.devices)?;
        d.set_item("layer", f.layer)?;
        out.append(d)?;
    }
    Ok(out)
}

/// required_tmax(expr) -> float | None. 'unknown' TIME raises InvalidConfig.
#[pyfunction(name = "required_tmax")]
fn py_required_tmax(expr: String) -> PyResult<Option<f64>> {
    use crate::bsource::{required_tmax_for_expr, TmaxNeed};
    match required_tmax_for_expr(&expr, 10.0, 50.0).0 {
        TmaxNeed::Bound(b) => Ok(Some(b)),
        TmaxNeed::None => Ok(None),
        TmaxNeed::Unknown => Err(InvalidConfigError::new_err(
            "unquantifiable TIME dependence: explicit TMAX required",
        )),
    }
}

#[pymodule]
fn _benchcore(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_run_simulation, m)?)?;
    m.add_function(wrap_pyfunction!(py_run_ac, m)?)?;
    m.add_function(wrap_pyfunction!(py_validate, m)?)?;
    m.add_function(wrap_pyfunction!(py_required_tmax, m)?)?;
    m.add_function(wrap_pyfunction!(py_replay, m)?)?;
    m.add_class::<PyLiveRun>()?;
    m.add("InvalidCircuitError", m.py().get_type::<InvalidCircuitError>())?;
    m.add("InvalidConfigError", m.py().get_type::<InvalidConfigError>())?;
    m.add("ConvergenceError", m.py().get_type::<ConvergenceError>())?;
    m.add("SolverError", m.py().get_type::<SolverError>())?;
    m.add("BackendError", m.py().get_type::<BackendError>())?;
    m.add("UnsupportedError", m.py().get_type::<UnsupportedError>())?;
    m.add("UnavailableError", m.py().get_type::<UnavailableError>())?;
    m.add("__version__", "0.1.0")?;
    Ok(())
}
