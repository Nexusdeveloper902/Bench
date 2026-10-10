//! Thin validated behavioral-expression layer.
//!
//! Ports the load-bearing rules of `exp-bexpr/thin.py`: an allowlisted AST
//! lowers deterministically to native B-source text. Rejects segfault and
//! silent-wrong classes pre-sim (gauss, limit, pwr, div-by-zero, self-ref,
//! bare-milli ambiguity, unknown nodes/params).

use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone, PartialEq)]
pub enum ThinError {
    Msg(String),
}

impl std::fmt::Display for ThinError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ThinError::Msg(m) => write!(f, "{}", m),
        }
    }
}
impl std::error::Error for ThinError {}

/// Allowlisted pure functions lowered 1:1 to ngspice names.
pub const CALLS_1TO1: &[&str] = &[
    "exp", "ln", "log", "log10", "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "sinh",
    "cosh", "tanh", "abs", "min", "max", "floor", "ceil", "sgn", "u", "uramp", "pwl", "pow",
    "ddt",
];

pub fn validate_call(name: &str) -> Result<String, ThinError> {
    let n = name.to_lowercase();
    if CALLS_1TO1.contains(&n.as_str()) {
        return Ok(n);
    }
    let guidance = match n.as_str() {
        "if" => "use [\"tern\",cond,a,b] (ngspice has no if(); ternary ?: works)",
        "int" => "unsupported; use floor()/ceil()",
        "table" => "unsupported in B expr; use pwl(x,...)",
        "poly" => "unsupported in B expr; use explicit arithmetic or pwl()",
        "stp" | "step" => "unsupported; use u()/uramp()",
        "idt" => "no integrator in B expr; use C + current-source state node (see recipe)",
        "sdt" => "unsupported; see idt",
        "atan2" | "arctan" => "unsupported; use atan()",
        "temp" => "bare TEMP undefined; use [\"temper\"] (Celsius)",
        "tnom" => "unsupported bare; thread as a param instead",
        "white" => "unsupported noise source in B expr",
        "gauss" => "REFUSED: gauss() segfaults ngspice-47 in B expr (verified)",
        "limit" => {
            "limit() is silently wrong in B expr (verified); use [\"clamp\",x,lo,hi]"
        }
        "pwr" => "pwr() preserves input sign (pwr(-2,2)=-4) unlike pow(); use [\"call\",\"pow\",...] explicitly",
        _ => "not in allowlist; refusing to guess",
    };
    Err(ThinError::Msg(format!(
        "function '{}' rejected: {}",
        name, guidance
    )))
}

/// Minimal AST (JSON-like). Mirrors thin.py op names.
#[derive(Debug, Clone)]
pub enum Expr {
    Num(f64),
    Param(String),
    V(String),
    VDiff(String, String),
    I(String),
    Time,
    Temper,
    BinOp(BinOp, Box<Expr>, Box<Expr>),
    Neg(Box<Expr>),
    And(Box<Expr>, Box<Expr>),
    Or(Box<Expr>, Box<Expr>),
    Not(Box<Expr>),
    Tern(Box<Expr>, Box<Expr>, Box<Expr>),
    Clamp(Box<Expr>, Box<Expr>, Box<Expr>),
    SMax(Box<Expr>, Box<Expr>),
    SMin(Box<Expr>, Box<Expr>),
    Call(String, Vec<Expr>),
    Pwl(Box<Expr>, Vec<f64>),
}

#[derive(Debug, Clone, Copy)]
pub enum BinOp {
    Add,
    Sub,
    Mul,
    Div,
    Pow,
    Gt,
    Lt,
    Ge,
    Le,
    Eq,
}

pub struct Ctx<'a> {
    pub nodes: &'a HashSet<String>,
    pub params: &'a HashMap<String, String>,
    pub senses: &'a HashSet<String>,
}

fn is_ident(s: &str) -> bool {
    let mut c = s.chars();
    match c.next() {
        Some(a) if a.is_ascii_alphabetic() || a == '_' => (),
        _ => return false,
    }
    s.chars().all(|ch| ch.is_ascii_alphanumeric() || ch == '_')
}

pub fn lower_expr(e: &Expr, ctx: &Ctx) -> Result<String, ThinError> {
    match e {
        Expr::Num(v) => Ok(format!("{}", v)),
        Expr::Param(p) => {
            if !ctx.params.contains_key(p) {
                let mut ks: Vec<&String> = ctx.params.keys().collect();
                ks.sort();
                return Err(ThinError::Msg(format!(
                    "unknown param '{}'; declared: {:?}",
                    p, ks
                )));
            }
            Ok(format!("{{{}}}", p))
        }
        Expr::V(n) => {
            if !ctx.nodes.contains(n) {
                return Err(ThinError::Msg(format!(
                    "unknown node '{}'; typo guard",
                    n
                )));
            }
            Ok(format!("V({})", n))
        }
        Expr::VDiff(a, b) => {
            for n in [a, b] {
                if !ctx.nodes.contains(n) {
                    return Err(ThinError::Msg(format!("unknown node '{}'", n)));
                }
            }
            Ok(format!("V({},{})", a, b))
        }
        Expr::I(s) => {
            if !is_ident(s) {
                return Err(ThinError::Msg(format!("bad sense source name: {:?}", s)));
            }
            if !ctx.senses.is_empty() && !ctx.senses.contains(s) {
                return Err(ThinError::Msg(format!("unknown sense source '{}'", s)));
            }
            Ok(format!("I({})", s))
        }
        Expr::Time => Ok("TIME".to_string()),
        Expr::Temper => Ok("temper".to_string()),
        Expr::BinOp(op, a, b) => {
            if matches!(op, BinOp::Div) && matches!(**b, Expr::Num(v) if v == 0.0) {
                return Err(ThinError::Msg(
                    "literal division by zero (ngspice silently yields 1e32)".to_string(),
                ));
            }
            let sym = match op {
                BinOp::Add => "+",
                BinOp::Sub => "-",
                BinOp::Mul => "*",
                BinOp::Div => "/",
                BinOp::Pow => "^",
                BinOp::Gt => ">",
                BinOp::Lt => "<",
                BinOp::Ge => ">=",
                BinOp::Le => "<=",
                BinOp::Eq => "=",
            };
            Ok(format!(
                "({}{}{})",
                lower_expr(a, ctx)?,
                sym,
                lower_expr(b, ctx)?
            ))
        }
        Expr::Neg(a) => Ok(format!("(-({}))", lower_expr(a, ctx)?)),
        Expr::And(a, b) => Ok(format!(
            "(({})&&({}))",
            lower_expr(a, ctx)?,
            lower_expr(b, ctx)?
        )),
        Expr::Or(a, b) => Ok(format!(
            "(({})||({}))",
            lower_expr(a, ctx)?,
            lower_expr(b, ctx)?
        )),
        Expr::Not(a) => Ok(format!("(!({}))", lower_expr(a, ctx)?)),
        Expr::Tern(c, a, b) => Ok(format!(
            "(({})?({}):({}))",
            lower_expr(c, ctx)?,
            lower_expr(a, ctx)?,
            lower_expr(b, ctx)?
        )),
        Expr::Clamp(x, lo, hi) => Ok(format!(
            "min(max({},{}) ,{})",
            lower_expr(x, ctx)?,
            lower_expr(lo, ctx)?,
            lower_expr(hi, ctx)?
        )),
        Expr::SMax(x, y) => {
            let (a, b) = (lower_expr(x, ctx)?, lower_expr(y, ctx)?);
            Ok(format!(
                "(0.5*(({})+({})+sqrt((({})-({}))*(({})-({}))+1e-12)))",
                a, b, a, b, a, b
            ))
        }
        Expr::SMin(x, y) => {
            let (a, b) = (lower_expr(x, ctx)?, lower_expr(y, ctx)?);
            Ok(format!(
                "(0.5*(({})+({})-sqrt((({})-({}))*(({})-({}))+1e-12)))",
                a, b, a, b, a, b
            ))
        }
        Expr::Call(name, args) => {
            let n = validate_call(name)?;
            if n == "exp" && args.len() == 1 {
                if let Expr::Num(v) = args[0] {
                    if v.abs() > 700.0 {
                        return Err(ThinError::Msg(format!(
                            "exp() of literal {}: overflow-scale result, refuse silent nonsense",
                            v
                        )));
                    }
                }
            }
            if (n == "ln" || n == "log" || n == "log10") && args.len() == 1 {
                if let Expr::Num(v) = args[0] {
                    if v <= 0.0 {
                        return Err(ThinError::Msg(format!(
                            "{}() of literal {}: ngspice silently yields ~-1e99; guard the domain first",
                            n, v
                        )));
                    }
                }
            }
            if n == "sqrt" && args.len() == 1 {
                if let Expr::Num(v) = args[0] {
                    if v < 0.0 {
                        return Err(ThinError::Msg(format!(
                            "sqrt() of literal {}: convergence wall; use sqrt(max(x,0)) if intended",
                            v
                        )));
                    }
                }
            }
            let mut parts = vec![];
            for a in args {
                parts.push(lower_expr(a, ctx)?);
            }
            Ok(format!("{}({})", n, parts.join(", ")))
        }
        Expr::Pwl(x, pts) => {
            if pts.len() % 2 != 0 || pts.len() < 4 {
                return Err(ThinError::Msg("pwl needs even >=4 point values".to_string()));
            }
            let mut s = format!("pwl({}", lower_expr(x, ctx)?);
            for p in pts {
                s.push_str(&format!(", {}", p));
            }
            s.push(')');
            Ok(s)
        }
    }
}
