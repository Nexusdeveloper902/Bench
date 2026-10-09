//! Production simulation core.
//!
//! Dependency direction (enforced):
//! frontend -> Simulation API -> kernel -> electrical domain -> ngspice adapter.
//! The core never depends on GUI, rendering, or frontend event loops.

pub mod bsource;
pub mod components;
pub mod error;
pub mod kernel;
pub mod ngspice;
pub mod thin;
pub mod validate;

pub use error::SimError;
pub use kernel::{run_simulation, pause_exact, parse_tstop, SimRequest, SimResult};

use pyo3::create_exception;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

create_exception!(_simcore, InvalidCircuitError, pyo3::exceptions::PyValueError);
create_exception!(_simcore, InvalidConfigError, pyo3::exceptions::PyValueError);
create_exception!(_simcore, ConvergenceError, pyo3::exceptions::PyRuntimeError);
create_exception!(_simcore, SolverError, pyo3::exceptions::PyRuntimeError);
create_exception!(_simcore, BackendError, pyo3::exceptions::PyRuntimeError);
create_exception!(_simcore, UnsupportedError, pyo3::exceptions::PyRuntimeError);
create_exception!(_simcore, UnavailableError, pyo3::exceptions::PyRuntimeError);

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
    match res.fallback_reason {
        Some(s) => d.set_item("fallback_reason", s)?,
        None => d.set_item("fallback_reason", py.None())?,
    };
    Ok(d)
}

/// validate(netlist) -> list of {code, severity, message, nodes, devices, layer}.
#[pyfunction(name = "validate")]
fn py_validate<'py>(py: Python<'py>, netlist: Vec<String>) -> PyResult<Bound<'py, PyList>> {
    let out = PyList::empty(py);
    for f in validate::validate(&netlist) {
        let d = PyDict::new(py);
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
fn _simcore(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_run_simulation, m)?)?;
    m.add_function(wrap_pyfunction!(py_validate, m)?)?;
    m.add_function(wrap_pyfunction!(py_required_tmax, m)?)?;
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
