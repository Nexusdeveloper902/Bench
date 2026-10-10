//! Typed simulation errors. The public contract exposes domain failures,
//! never raw ngspice strings. Backend log lines are attached as context.

use std::fmt;

/// Public error taxonomy (see docs/architecture.md section on error handling).
#[derive(Debug, Clone, PartialEq)]
pub enum SimError {
    /// Circuit rejected before simulation (carries validator code, e.g. V-LOOP).
    InvalidCircuit { code: String, message: String },
    /// Bad simulation configuration (bad stop times, bad tran, unknown tstop...).
    InvalidConfig { message: String },
    /// Solver ran but failed loudly (singular, timestep too small, no convergence).
    SolverFailure { message: String, log_hint: String },
    /// Explicit convergence failure (subset of solver failure kept distinct).
    ConvergenceFailure { message: String },
    /// Well-formed but not supported by this backend/version.
    Unsupported { message: String },
    /// libngspice transport failure (circ/command/vec call failed).
    BackendFailure { message: String },
    /// API used in the wrong state (e.g. resume with no paused sim).
    Lifecycle { message: String },
    /// Capability valid in principle but unavailable here (OSDI/XSPICE pause...).
    Unavailable { message: String },
}

impl fmt::Display for SimError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SimError::InvalidCircuit { code, message } => {
                write!(f, "invalid circuit [{}]: {}", code, message)
            }
            SimError::InvalidConfig { message } => write!(f, "invalid config: {}", message),
            SimError::SolverFailure { message, .. } => write!(f, "solver failure: {}", message),
            SimError::ConvergenceFailure { message } => {
                write!(f, "convergence failure: {}", message)
            }
            SimError::Unsupported { message } => write!(f, "unsupported: {}", message),
            SimError::BackendFailure { message } => write!(f, "backend failure: {}", message),
            SimError::Lifecycle { message } => write!(f, "lifecycle error: {}", message),
            SimError::Unavailable { message } => write!(f, "unavailable: {}", message),
        }
    }
}

impl std::error::Error for SimError {}
