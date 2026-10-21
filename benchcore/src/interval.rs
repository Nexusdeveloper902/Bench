//! Interval evaluation of ngspice expressions (GAPS G29): the value check the static validator runs on B sources,
//! E/G `VALUE=` and behavioural R/C/L expressions.
//!
//! An expression is evaluated over a *range* for every name it reads: a package parameter that declares both
//! `min` and `max`, a stimulus node driven by a `V` source whose value is such a parameter (or a SIN, PULSE, PWL
//! hull), a `.param`. Everything else (`V(x)` of a circuit node, `I(vsrc)`, `@dev[..]`, `temper`, a user `.func`)
//! is *unknown*. The operations that ngspice 47 fails, clamps or silently gets wrong (probed one deck per case,
//! `docs/plans/gap-closure/probes/domain_probe.py`) are checked:
//!
//! | operation | ngspice 47 | finding |
//! |---|---|---|
//! | `sqrt`, `ln`, `log`, `log10` of a negative | fatal `out of range` | B-DOMAIN |
//! | `ln`, `log` of 0 | silently -1e99 | B-DOMAIN |
//! | `asin`, `acos`, `acosh`, `atanh` out of domain | NaN | B-DOMAIN |
//! | `pow`, `^`: negative base, non-integer exponent | silently 0 | B-DOMAIN |
//! | `pow`, `^`: base 0, negative exponent | silently 0, or fatal | B-DOMAIN |
//! | division by a range that reaches 0 | silently 1e32 | B-DIVZERO |
//! | `exp` above 227 | silently clamped to 1e99 | B-OVERFLOW |
//! | `sinh`, `cosh` above 709, a product or power past f64 | fatal | B-OVERFLOW |
//!
//! Interval arithmetic over-estimates (it cannot see that two occurrences of one name are the same number), so a
//! hit is only reported as an *error* once a sample of the ranges (corners, mid points, a few interior points,
//! every range at once) reproduces it with exact point arithmetic; the finding then names the witness. A hit no
//! sample reproduces, and every operation whose operand range is unknown, is *unproved*: the caller keeps its
//! LIMITED-ANALYSIS note and says which operation. A name outside a declared range (a live edit, a `.dc` sweep of
//! a parameter) is not covered: the caller drops the range of anything a `.dc` sweeps.

use std::collections::HashMap;
use std::fmt;

use crate::validate::parse_value;

const INF: f64 = f64::INFINITY;
/// ngspice 47 clamps `exp(x)` to 1e99 from x = 227.9 (probed).
const EXP_CLAMP: f64 = 227.0;
/// `sinh`/`cosh` fail from about 710 (probed: sinh(800) is fatal).
const SINH_MAX: f64 = 709.0;

// ---------------------------------------------------------------------------------------------------- intervals

/// A set of reals: `[lo, hi]` with each end open or closed. `known == false` means no information at all.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Iv {
    pub lo: f64,
    pub hi: f64,
    pub lo_open: bool,
    pub hi_open: bool,
    pub known: bool,
}

impl Iv {
    pub const UNKNOWN: Iv = Iv { lo: -INF, hi: INF, lo_open: true, hi_open: true, known: false };

    pub fn point(x: f64) -> Iv {
        Iv { lo: x, hi: x, lo_open: false, hi_open: false, known: true }
    }

    /// Closed ends unless the declared bound is exclusive; an infinite end is always open.
    pub fn range(lo: f64, hi: f64, lo_open: bool, hi_open: bool) -> Iv {
        Iv { lo, hi, lo_open: lo_open || lo.is_infinite(), hi_open: hi_open || hi.is_infinite(), known: true }
    }

    fn whole() -> Iv {
        Iv::range(-INF, INF, true, true)
    }

    pub fn is_point(&self) -> bool {
        self.known && self.lo == self.hi
    }

    fn is_empty(&self) -> bool {
        self.lo > self.hi || (self.lo == self.hi && (self.lo_open || self.hi_open))
    }

    fn has_zero(&self) -> bool {
        (self.lo < 0.0 || (self.lo == 0.0 && !self.lo_open)) && (self.hi > 0.0 || (self.hi == 0.0 && !self.hi_open))
    }

    fn has_neg(&self) -> bool {
        self.lo < 0.0
    }

    pub fn neg(self) -> Iv {
        Iv { lo: -self.hi, hi: -self.lo, lo_open: self.hi_open, hi_open: self.lo_open, known: self.known }
    }

    pub fn hull(a: Iv, b: Iv) -> Iv {
        if !(a.known && b.known) {
            return Iv::UNKNOWN;
        }
        let (lo, lo_open) = if a.lo < b.lo { (a.lo, a.lo_open) } else if b.lo < a.lo { (b.lo, b.lo_open) } else { (a.lo, a.lo_open && b.lo_open) };
        let (hi, hi_open) = if a.hi > b.hi { (a.hi, a.hi_open) } else if b.hi > a.hi { (b.hi, b.hi_open) } else { (a.hi, a.hi_open && b.hi_open) };
        Iv::range(lo, hi, lo_open, hi_open)
    }

    /// Smallest and largest of candidate end values, an end open when every candidate reaching it is open.
    fn from_candidates(c: &[(f64, bool)]) -> Iv {
        let c: Vec<&(f64, bool)> = c.iter().filter(|(v, _)| !v.is_nan()).collect();
        if c.is_empty() {
            return Iv::whole();
        }
        let lo = c.iter().map(|(v, _)| *v).fold(INF, f64::min);
        let hi = c.iter().map(|(v, _)| *v).fold(-INF, f64::max);
        let lo_open = c.iter().filter(|(v, _)| *v == lo).all(|(_, o)| *o);
        let hi_open = c.iter().filter(|(v, _)| *v == hi).all(|(_, o)| *o);
        Iv::range(lo, hi, lo_open, hi_open)
    }

    pub fn add(self, b: Iv) -> Iv {
        if !(self.known && b.known) {
            return Iv::UNKNOWN;
        }
        Iv::range(self.lo + b.lo, self.hi + b.hi, self.lo_open || b.lo_open, self.hi_open || b.hi_open)
    }

    fn mul(self, b: Iv) -> Iv {
        if !(self.known && b.known) {
            return Iv::UNKNOWN;
        }
        let mut c = vec![];
        for &(x, xo) in &[(self.lo, self.lo_open), (self.hi, self.hi_open)] {
            for &(y, yo) in &[(b.lo, b.lo_open), (b.hi, b.hi_open)] {
                // 0 * inf is 0 (the product of the limits); a closed 0 end makes the product closed
                let zero_closed = (x == 0.0 && !xo) || (y == 0.0 && !yo);
                c.push((if x == 0.0 || y == 0.0 { 0.0 } else { x * y }, (xo || yo) && !zero_closed));
            }
        }
        Iv::from_candidates(&c)
    }

    /// 1/self; self must not contain 0 (an open 0 end gives an unbounded, open result).
    fn recip(self) -> Iv {
        if self.lo >= 0.0 {
            let (hi, hi_open) = if self.lo == 0.0 { (INF, true) } else { (1.0 / self.lo, self.lo_open) };
            let (lo, lo_open) = if self.hi == INF { (0.0, true) } else { (1.0 / self.hi, self.hi_open) };
            Iv::range(lo, hi, lo_open, hi_open)
        } else {
            self.neg().recip().neg()
        }
    }

    fn map_inc(self, f: fn(f64) -> f64) -> Iv {
        if !self.known {
            return Iv::UNKNOWN;
        }
        Iv::range(f(self.lo), f(self.hi), self.lo_open, self.hi_open)
    }

    /// Does the expression reach exactly `v`?
    fn at(&self, v: f64) -> bool {
        (self.lo < v || (self.lo == v && !self.lo_open)) && (self.hi > v || (self.hi == v && !self.hi_open))
    }
}

impl fmt::Display for Iv {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if !self.known {
            return write!(f, "unknown");
        }
        let n = |x: f64| if x.is_infinite() { if x > 0.0 { "inf".to_string() } else { "-inf".to_string() } } else { format!("{:.6}", x).trim_end_matches('0').trim_end_matches('.').to_string() };
        let g = |x: f64| if x != 0.0 && (x.abs() < 1e-4 || x.abs() >= 1e9) { format!("{:e}", x) } else { n(x) };
        if self.lo == self.hi {
            return write!(f, "{}", g(self.lo));
        }
        write!(f, "{}{}, {}{}", if self.lo_open { "(" } else { "[" }, g(self.lo), g(self.hi), if self.hi_open { ")" } else { "]" })
    }
}

// ---------------------------------------------------------------------------------------------------- syntax

#[derive(Clone, Debug, PartialEq)]
pub enum E {
    Num(f64),
    Var(String),
    /// v(a) or v(a,b): lower-case node names
    V(Vec<String>),
    /// i(name), @dev[param]: a circuit current or device vector
    Unk(String),
    Neg(Box<E>),
    Not(Box<E>),
    Bin(Op, Box<E>, Box<E>),
    Call(String, Vec<E>),
    Tern(Box<E>, Box<E>, Box<E>),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Op {
    Add,
    Sub,
    Mul,
    Div,
    Mod,
    Pow,
    Lt,
    Le,
    Gt,
    Ge,
    Eq,
    Ne,
    And,
    Or,
}

impl Op {
    fn sym(self) -> &'static str {
        match self {
            Op::Add => "+", Op::Sub => "-", Op::Mul => "*", Op::Div => "/", Op::Mod => "%", Op::Pow => "^", Op::Lt => "<",
            Op::Le => "<=", Op::Gt => ">", Op::Ge => ">=", Op::Eq => "==", Op::Ne => "!=", Op::And => "&&", Op::Or => "||",
        }
    }

    fn is_cmp(self) -> bool {
        matches!(self, Op::Lt | Op::Le | Op::Gt | Op::Ge)
    }
}

impl fmt::Display for E {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            E::Num(x) => write!(f, "{}", x),
            E::Var(n) => write!(f, "{}", n),
            E::V(n) => write!(f, "V({})", n.join(",")),
            E::Unk(n) => write!(f, "{}", n),
            E::Neg(a) => write!(f, "-({})", a),
            E::Not(a) => write!(f, "!({})", a),
            E::Bin(op, a, b) => write!(f, "({}{}{})", a, op.sym(), b),
            E::Call(n, a) => write!(f, "{}({})", n, a.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(",")),
            E::Tern(c, a, b) => write!(f, "({}?{}:{})", c, a, b),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
enum Tok {
    Num(f64),
    Id(String),
    Node(char, Vec<String>), // v(...) / i(...)
    Unk(String),
    Sym(&'static str),
}

fn lex(s: &str) -> Result<Vec<Tok>, String> {
    let c: Vec<char> = s.chars().collect();
    let mut out = vec![];
    let mut i = 0;
    while i < c.len() {
        let ch = c[i];
        if ch.is_whitespace() || ch == '\'' {
            i += 1;
        } else if ch.is_ascii_digit() || (ch == '.' && c.get(i + 1).is_some_and(|d| d.is_ascii_digit())) {
            let st = i;
            while i < c.len() && (c[i].is_ascii_digit() || c[i] == '.') {
                i += 1;
            }
            if i < c.len() && (c[i] == 'e' || c[i] == 'E') {
                let mut j = i + 1;
                if j < c.len() && (c[j] == '+' || c[j] == '-') {
                    j += 1;
                }
                if j < c.len() && c[j].is_ascii_digit() {
                    i = j;
                    while i < c.len() && c[i].is_ascii_digit() {
                        i += 1;
                    }
                }
            }
            while i < c.len() && c[i].is_ascii_alphabetic() {
                i += 1; // scale suffix and unit: 1k, 10uF, 1meg
            }
            let t: String = c[st..i].iter().collect();
            out.push(Tok::Num(parse_value(&t).ok_or_else(|| format!("number '{}'", t))?));
        } else if ch.is_ascii_alphabetic() || ch == '_' {
            let st = i;
            while i < c.len() && (c[i].is_ascii_alphanumeric() || c[i] == '_') {
                i += 1;
            }
            let id: String = c[st..i].iter().collect::<String>().to_lowercase();
            let mut j = i;
            while j < c.len() && c[j].is_whitespace() {
                j += 1;
            }
            if (id == "v" || id == "i") && c.get(j) == Some(&'(') {
                let st = j + 1;
                let mut depth = 0;
                let mut k = j;
                while k < c.len() {
                    if c[k] == '(' {
                        depth += 1;
                    } else if c[k] == ')' {
                        depth -= 1;
                        if depth == 0 {
                            break;
                        }
                    }
                    k += 1;
                }
                if k >= c.len() {
                    return Err(format!("unclosed {}(", id));
                }
                let inner: String = c[st..k].iter().collect();
                out.push(Tok::Node(id.chars().next().unwrap(), inner.split(',').map(|t| t.trim().to_lowercase()).collect()));
                i = k + 1;
            } else {
                out.push(Tok::Id(id));
            }
        } else if ch == '@' {
            let st = i;
            while i < c.len() && (c[i].is_ascii_alphanumeric() || "_@.#[]".contains(c[i])) {
                i += 1;
            }
            out.push(Tok::Unk(c[st..i].iter().collect()));
        } else {
            let two: String = c[i..(i + 2).min(c.len())].iter().collect();
            let sym = match two.as_str() {
                "**" => Some("^"),
                "<=" => Some("<="),
                ">=" => Some(">="),
                "==" => Some("=="),
                "!=" | "<>" => Some("!="),
                "&&" => Some("&&"),
                "||" => Some("||"),
                _ => None,
            };
            if let Some(sy) = sym {
                out.push(Tok::Sym(sy));
                i += 2;
                continue;
            }
            let sym = match ch {
                '+' => "+", '-' => "-", '*' => "*", '/' => "/", '%' => "%", '^' => "^", '<' => "<", '>' => ">", '!' => "!",
                '(' | '{' => "(", ')' | '}' => ")", ',' => ",", '?' => "?", ':' => ":", '=' => "=",
                _ => return Err(format!("character '{}'", ch)),
            };
            out.push(Tok::Sym(sym));
            i += 1;
        }
    }
    Ok(out)
}

struct P {
    t: Vec<Tok>,
    i: usize,
}

impl P {
    fn peek(&self) -> Option<&Tok> {
        self.t.get(self.i)
    }

    fn eat(&mut self, s: &str) -> bool {
        if matches!(self.peek(), Some(Tok::Sym(x)) if *x == s) {
            self.i += 1;
            true
        } else {
            false
        }
    }

    fn tern(&mut self) -> Result<E, String> {
        let c = self.or()?;
        if self.eat("?") {
            let a = self.tern()?;
            if !self.eat(":") {
                return Err("'?' without ':'".into());
            }
            let b = self.tern()?;
            return Ok(E::Tern(Box::new(c), Box::new(a), Box::new(b)));
        }
        Ok(c)
    }

    fn level(&mut self, ops: &[(&str, Op)], next: fn(&mut P) -> Result<E, String>) -> Result<E, String> {
        let mut l = next(self)?;
        'outer: loop {
            for (s, op) in ops {
                if self.eat(s) {
                    let r = next(self)?;
                    l = E::Bin(*op, Box::new(l), Box::new(r));
                    continue 'outer;
                }
            }
            return Ok(l);
        }
    }

    fn or(&mut self) -> Result<E, String> {
        self.level(&[("||", Op::Or)], P::and)
    }

    fn and(&mut self) -> Result<E, String> {
        self.level(&[("&&", Op::And)], P::eq)
    }

    fn eq(&mut self) -> Result<E, String> {
        self.level(&[("==", Op::Eq), ("!=", Op::Ne)], P::rel)
    }

    fn rel(&mut self) -> Result<E, String> {
        self.level(&[("<=", Op::Le), (">=", Op::Ge), ("<", Op::Lt), (">", Op::Gt)], P::add)
    }

    fn add(&mut self) -> Result<E, String> {
        self.level(&[("+", Op::Add), ("-", Op::Sub)], P::mul)
    }

    fn mul(&mut self) -> Result<E, String> {
        self.level(&[("*", Op::Mul), ("/", Op::Div), ("%", Op::Mod)], P::unary)
    }

    fn unary(&mut self) -> Result<E, String> {
        if self.eat("-") {
            return Ok(E::Neg(Box::new(self.unary()?)));
        }
        if self.eat("+") {
            return self.unary();
        }
        if self.eat("!") {
            return Ok(E::Not(Box::new(self.unary()?)));
        }
        self.pow()
    }

    /// `^` and `**` associate to the left and bind tighter than a unary minus (probed: 2^3^2 = 64, -2^2 = -4).
    fn pow(&mut self) -> Result<E, String> {
        let mut l = self.primary()?;
        while self.eat("^") {
            let r = self.pow_arg()?;
            l = E::Bin(Op::Pow, Box::new(l), Box::new(r));
        }
        Ok(l)
    }

    fn pow_arg(&mut self) -> Result<E, String> {
        if self.eat("-") {
            return Ok(E::Neg(Box::new(self.pow_arg()?)));
        }
        if self.eat("+") {
            return self.pow_arg();
        }
        self.primary()
    }

    fn primary(&mut self) -> Result<E, String> {
        let Some(t) = self.t.get(self.i).cloned() else { return Err("expression ends early".into()) };
        self.i += 1;
        match t {
            Tok::Num(x) => Ok(E::Num(x)),
            Tok::Node('v', n) if (1..=2).contains(&n.len()) && n.iter().all(|s| !s.is_empty()) => Ok(E::V(n)),
            Tok::Node('i', n) if n.len() == 1 && !n[0].is_empty() => Ok(E::Unk(format!("i({})", n[0]))),
            Tok::Node(..) => Err("v() or i() with a bad argument list".into()),
            Tok::Unk(s) => Ok(E::Unk(s)),
            Tok::Id(id) => {
                if self.peek() == Some(&Tok::Sym("(")) {
                    self.i += 1;
                    let mut args = vec![];
                    if !self.eat(")") {
                        loop {
                            args.push(self.tern()?);
                            if self.eat(")") {
                                break;
                            }
                            if !self.eat(",") {
                                return Err(format!("{}( without ')'", id));
                            }
                        }
                    }
                    Ok(E::Call(id, args))
                } else {
                    Ok(E::Var(id))
                }
            }
            Tok::Sym("(") => {
                let e = self.tern()?;
                if !self.eat(")") {
                    return Err("'(' without ')'".into());
                }
                Ok(e)
            }
            Tok::Sym(s) => Err(format!("unexpected '{}'", s)),
        }
    }
}

/// Parse one expression (outer `{}` / `'` already allowed). A trailing `name=value` list (a B source's `tc1=`, `tc2=`,
/// `temp=`) is ignored.
pub fn parse(s: &str) -> Result<E, String> {
    let mut p = P { t: lex(s)?, i: 0 };
    let e = p.tern()?;
    match p.peek() {
        None => Ok(e),
        Some(Tok::Id(_)) if p.t.get(p.i + 1) == Some(&Tok::Sym("=")) => Ok(e),
        Some(t) => Err(format!("unexpected {:?} after the expression", t)),
    }
}

// ---------------------------------------------------------------------------------------------------- evaluation

/// Where `V(node)` comes from: a source whose value is an expression of the environment, or a fixed range.
#[derive(Clone, Debug)]
pub enum Node {
    Expr(E),
    Fixed(Iv),
}

#[derive(Clone, Debug, Default)]
pub struct Env {
    pub vars: HashMap<String, Iv>,
    pub nodes: HashMap<String, Node>,
    /// ngspice's first iterate: every node voltage and branch current is 0
    pub zero: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Issue {
    pub code: &'static str,
    /// the operation, as the key that tells two hits of one expression apart
    pub what: String,
    pub msg: String,
    /// ngspice stops the run on it (sqrt, ln, log, log10 of a negative, an overflow), rather than returning a wrong number
    pub fatal: bool,
}

#[derive(Default)]
struct Ev {
    issues: Vec<Issue>,
    unproved: Vec<String>,
    tainted: bool,
    /// (divisor, its value) of every division whose divisor was a point: a sign change between samples is a zero
    dens: Vec<(String, f64)>,
}

fn g(x: f64) -> String {
    Iv::point(x).to_string()
}

impl Ev {
    fn issue(&mut self, code: &'static str, what: String, msg: String) {
        self.issue_f(false, code, what, msg);
    }

    /// `fatal`: ngspice stops the run on it, rather than computing a wrong number.
    fn issue_f(&mut self, fatal: bool, code: &'static str, what: String, msg: String) {
        let i = Issue { code, what, msg, fatal };
        if !self.issues.iter().any(|x| x.code == code && x.what == i.what) {
            self.issues.push(i);
        }
    }

    /// The value of an operation that just failed: nothing is known, and what reads it is not reported again.
    fn fail(&mut self) -> Iv {
        self.tainted = true;
        Iv::UNKNOWN
    }

    fn unproved(&mut self, msg: String) {
        if !self.tainted && !self.unproved.contains(&msg) {
            self.unproved.push(msg);
        }
    }

    fn node(&mut self, name: &str, env: &Env, depth: usize) -> Iv {
        if matches!(name, "0" | "gnd") {
            return Iv::point(0.0);
        }
        if env.zero {
            return Iv::point(0.0);
        }
        match env.nodes.get(name) {
            Some(Node::Fixed(iv)) => *iv,
            Some(Node::Expr(e)) if depth < 8 => self.ev(e, env, depth + 1),
            _ => Iv::UNKNOWN,
        }
    }

    fn ev(&mut self, e: &E, env: &Env, depth: usize) -> Iv {
        match e {
            E::Num(x) => Iv::point(*x),
            E::Var(n) => match n.as_str() {
                "pi" if !env.vars.contains_key("pi") => Iv::point(std::f64::consts::PI),
                "e" if !env.vars.contains_key("e") => Iv::point(std::f64::consts::E),
                "time" | "hertz" if !env.vars.contains_key(n) => Iv::range(0.0, INF, false, true),
                _ => env.vars.get(n).copied().unwrap_or(Iv::UNKNOWN),
            },
            E::V(n) => {
                let a = self.node(&n[0], env, depth);
                match n.get(1) {
                    None => a,
                    Some(b) => {
                        let b = self.node(b, env, depth);
                        a.add(b.neg())
                    }
                }
            }
            E::Unk(_) if env.zero => Iv::point(0.0),
            E::Unk(_) => Iv::UNKNOWN,
            E::Neg(a) => self.ev(a, env, depth).neg(),
            E::Not(a) => {
                let a = self.ev(a, env, depth);
                if a.known && !a.at(0.0) {
                    Iv::point(0.0)
                } else if a.is_point() && a.lo == 0.0 {
                    Iv::point(1.0)
                } else {
                    Iv::range(0.0, 1.0, false, false)
                }
            }
            E::Bin(op, a, b) => {
                let (x, y) = (self.ev(a, env, depth), self.ev(b, env, depth));
                self.bin(*op, a, b, x, y)
            }
            E::Call(n, args) => {
                let v: Vec<Iv> = args.iter().map(|a| self.ev(a, env, depth)).collect();
                self.call(n, args, &v)
            }
            E::Tern(c, a, b) => self.tern(c, a, b, env, depth),
        }
    }

    fn tern(&mut self, c: &E, a: &E, b: &E, env: &Env, depth: usize) -> Iv {
        let cv = self.ev(c, env, depth);
        let decided = |v: &Iv| v.known && (!v.at(0.0) || (v.is_point() && v.lo == 0.0));
        if decided(&cv) {
            return if cv.at(0.0) { self.ev(b, env, depth) } else { self.ev(a, env, depth) };
        }
        let (et, ef) = (narrow(env, c, true, self, depth), narrow(env, c, false, self, depth));
        let t = et.map(|en| self.ev(a, &en, depth));
        let f = ef.map(|en| self.ev(b, &en, depth));
        match (t, f) {
            (Some(t), Some(f)) => Iv::hull(t, f),
            (Some(t), None) => t,
            (None, Some(f)) => f,
            (None, None) => Iv::UNKNOWN,
        }
    }

    fn bin(&mut self, op: Op, ea: &E, eb: &E, x: Iv, y: Iv) -> Iv {
        match op {
            Op::Add => x.add(y),
            Op::Sub => x.add(y.neg()),
            Op::Mul => x.mul(y),
            Op::Div => self.div(eb, x, y),
            Op::Mod => {
                self.unproved(format!("'{}%{}': % is not evaluated", ea, eb));
                Iv::UNKNOWN
            }
            Op::Pow => self.pow(&format!("({}^{})", ea, eb), x, y),
            Op::And | Op::Or => Iv::range(0.0, 1.0, false, false),
            _ => {
                // a comparison is decided when the ranges do not overlap
                if !(x.known && y.known) {
                    return Iv::range(0.0, 1.0, false, false);
                }
                let lt = x.hi < y.lo || (x.hi == y.lo && (x.hi_open || y.lo_open));
                let gt = y.hi < x.lo || (y.hi == x.lo && (y.hi_open || x.lo_open));
                let le = x.hi <= y.lo;
                let ge = y.hi <= x.lo;
                let t = |b: bool| Iv::point(if b { 1.0 } else { 0.0 });
                match op {
                    Op::Lt if lt => t(true),
                    Op::Lt if ge => t(false),
                    Op::Le if le => t(true),
                    Op::Le if gt => t(false),
                    Op::Gt if gt => t(true),
                    Op::Gt if le => t(false),
                    Op::Ge if ge => t(true),
                    Op::Ge if lt => t(false),
                    _ => Iv::range(0.0, 1.0, false, false),
                }
            }
        }
    }

    fn div(&mut self, den: &E, x: Iv, y: Iv) -> Iv {
        if !y.known {
            self.unproved(format!("division by `{}`: its range is not known", den));
            return Iv::UNKNOWN;
        }
        if y.has_zero() {
            self.issue("B-DIVZERO", den.to_string(), format!("division by `{}`, which ranges over {} and so reaches 0 (ngspice silently returns about 1e32)", den, y));
            return self.fail();
        }
        if y.is_point() {
            self.dens.push((den.to_string(), y.lo));
        }
        x.mul(y.recip())
    }

    fn pow(&mut self, what: &str, a: Iv, b: Iv) -> Iv {
        let int_exp = b.is_point() && b.lo.fract() == 0.0 && b.lo.abs() < 1e6;
        if !a.known && int_exp && b.lo >= 0.0 {
            // a whole power of anything: no hazard (an even one is not negative)
            return if b.lo % 2.0 == 0.0 { Iv::range(0.0, INF, false, true) } else { Iv::UNKNOWN };
        }
        if !(a.known && b.known) {
            self.unproved(format!("{}: base or exponent range is not known", what));
            return Iv::UNKNOWN;
        }
        if a.has_neg() && !int_exp {
            self.issue("B-DOMAIN", what.to_string(), format!("{}: the base ranges over {} (negative) with exponent {}; ngspice silently returns 0 for a negative base and a non-integer exponent", what, a, b));
            return self.fail();
        }
        if a.has_zero() && b.lo < 0.0 {
            self.issue("B-DOMAIN", what.to_string(), format!("{}: the base ranges over {} and reaches 0 with exponent {} (negative); ngspice silently returns 0 or fails", what, a, b));
            return self.fail();
        }
        if int_exp {
            let n = b.lo as i32;
            let m = n.abs();
            let (lo, hi) = (a.lo.powi(m), a.hi.powi(m));
            let p = if m == 0 {
                Iv::point(1.0)
            } else if m % 2 == 1 || a.lo >= 0.0 {
                Iv::range(lo, hi, a.lo_open, a.hi_open)
            } else if a.hi <= 0.0 {
                Iv::range(hi, lo, a.hi_open, a.lo_open)
            } else {
                Iv::range(0.0, lo.max(hi), false, false)
            };
            if p.lo.is_infinite() && !a.lo.is_infinite() || p.hi.is_infinite() && !a.hi.is_infinite() {
                self.issue_f(true, "B-OVERFLOW", what.to_string(), format!("{}: the power overflows for a base up to {}", what, a));
            }
            return if n < 0 { p.recip() } else { p };
        }
        // a >= 0: x^y is monotone in each argument for a fixed other one, so the corners bound it
        let mut c = vec![];
        for &(x, xo) in &[(a.lo, a.lo_open), (a.hi, a.hi_open)] {
            for &(y, yo) in &[(b.lo, b.lo_open), (b.hi, b.hi_open)] {
                let v = x.powf(y);
                if v.is_infinite() && x.is_finite() && x > 0.0 && y.is_finite() {
                    self.issue_f(true, "B-OVERFLOW", what.to_string(), format!("{}: {}^{} overflows (ngspice fails with 'out of range')", what, g(x), g(y)));
                }
                c.push((v, xo || yo));
            }
        }
        Iv::from_candidates(&c)
    }

    fn domain(&mut self, f: &str, arg: &E, a: Iv, ok: impl Fn(&Iv) -> bool, need: &str, why: &str) -> bool {
        if !a.known {
            self.unproved(format!("{}({}): the range of the argument is not known", f, arg));
            return false;
        }
        if !ok(&a) {
            self.issue_f(matches!(f, "sqrt" | "ln" | "log" | "log10") && a.lo < 0.0, "B-DOMAIN", format!("{}({})", f, arg), format!("{}({}): the argument ranges over {} and so leaves its domain ({}); {}", f, arg, a, need, why));
            return false;
        }
        true
    }

    fn call(&mut self, n: &str, args: &[E], v: &[Iv]) -> Iv {
        let a0 = v.first().copied().unwrap_or(Iv::UNKNOWN);
        let one = v.len() == 1;
        let sh = |i: usize| args.get(i).map_or(String::new(), |a| a.to_string());
        match n {
            "sqrt" if one => {
                let ok = self.domain("sqrt", &args[0], a0, |a| a.lo >= 0.0, "x >= 0", "ngspice fails the run with 'out of range for sqrt'");
                if ok { a0.map_inc(f64::sqrt) } else { self.fail() }
            }
            "ln" | "log" | "log10" if one => {
                let ok = self.domain(n, &args[0], a0, |a| a.lo > 0.0 || (a.lo == 0.0 && a.lo_open), "x > 0", "ngspice fails the run for x < 0 and silently returns -1e99 for 0");
                if !ok {
                    return self.fail();
                }
                a0.map_inc(if n == "log10" { f64::log10 } else { f64::ln })
            }
            "exp" if one => {
                if !a0.known {
                    self.unproved(format!("exp({}): the range of the argument is not known", sh(0)));
                    return Iv::range(0.0, INF, true, true);
                }
                if a0.hi > EXP_CLAMP {
                    self.issue("B-OVERFLOW", format!("exp({})", sh(0)), format!("exp({}): the argument ranges over {} (above {}); ngspice clamps exp() to 1e99 from 227.9, so the value is silently wrong", sh(0), a0, EXP_CLAMP));
                }
                Iv::range(a0.lo.exp(), a0.hi.exp().min(1e99), a0.lo_open, a0.hi_open)
            }
            "abs" if one => {
                if !a0.known {
                    Iv::range(0.0, INF, false, true)
                } else if a0.lo >= 0.0 {
                    a0
                } else if a0.hi <= 0.0 {
                    a0.neg()
                } else {
                    Iv::range(0.0, (-a0.lo).max(a0.hi), false, a0.lo_open && a0.hi_open && -a0.lo == a0.hi)
                }
            }
            "sin" | "cos" if one => Iv::range(-1.0, 1.0, false, false),
            "tan" if one => Iv::whole(),
            "atan" | "arctan" if one => {
                if a0.known { a0.map_inc(f64::atan) } else { Iv::range(-std::f64::consts::FRAC_PI_2, std::f64::consts::FRAC_PI_2, true, true) }
            }
            "tanh" if one => {
                if a0.known { a0.map_inc(f64::tanh) } else { Iv::range(-1.0, 1.0, true, true) }
            }
            "asinh" if one => a0.map_inc(f64::asinh),
            "sinh" | "cosh" if one => {
                if !a0.known {
                    self.unproved(format!("{}({}): the range of the argument is not known", n, sh(0)));
                    return if n == "cosh" { Iv::range(1.0, INF, false, true) } else { Iv::whole() };
                }
                if a0.lo < -SINH_MAX || a0.hi > SINH_MAX {
                    self.issue_f(true, "B-OVERFLOW", format!("{}({})", n, sh(0)), format!("{}({}): the argument ranges over {}; ngspice fails the run above about 710", n, sh(0), a0));
                    return self.fail();
                }
                if n == "sinh" {
                    a0.map_inc(f64::sinh)
                } else {
                    let (x, y) = (a0.lo.cosh(), a0.hi.cosh());
                    Iv::range(if a0.has_zero() { 1.0 } else { x.min(y) }, x.max(y), false, false)
                }
            }
            "asin" | "acos" if one => {
                if !self.domain(n, &args[0], a0, |a| a.lo >= -1.0 && a.hi <= 1.0, "-1 <= x <= 1", "ngspice returns NaN") {
                    return self.fail();
                }
                let (x, y) = if n == "asin" { (a0.lo.asin(), a0.hi.asin()) } else { (a0.hi.acos(), a0.lo.acos()) };
                Iv::range(x, y, false, false)
            }
            "acosh" if one => {
                if !self.domain(n, &args[0], a0, |a| a.lo >= 1.0, "x >= 1", "ngspice returns NaN") {
                    return self.fail();
                }
                a0.map_inc(f64::acosh)
            }
            "atanh" if one => {
                if !self.domain(n, &args[0], a0, |a| a.lo > -1.0 && a.hi < 1.0, "-1 < x < 1", "ngspice returns NaN") {
                    return self.fail();
                }
                a0.map_inc(f64::atanh)
            }
            "floor" | "ceil" | "nint" | "int" if one => a0.map_inc(match n {
                "floor" => f64::floor,
                "ceil" => f64::ceil,
                "nint" => f64::round,
                _ => f64::trunc,
            }),
            "sgn" if one => {
                if a0.known && a0.lo > 0.0 {
                    Iv::point(1.0)
                } else if a0.known && a0.hi < 0.0 {
                    Iv::point(-1.0)
                } else {
                    Iv::range(-1.0, 1.0, false, false)
                }
            }
            "u" | "u2" if one => {
                if n == "u" && a0.known && a0.lo > 0.0 {
                    Iv::point(1.0)
                } else if n == "u" && a0.known && a0.hi < 0.0 {
                    Iv::point(0.0)
                } else {
                    Iv::range(0.0, 1.0, false, false)
                }
            }
            "uramp" if one => {
                if !a0.known {
                    Iv::range(0.0, INF, false, true)
                } else {
                    Iv::range(a0.lo.max(0.0), a0.hi.max(0.0), a0.lo_open && a0.lo >= 0.0, a0.hi_open && a0.hi >= 0.0)
                }
            }
            "min" | "max" if v.len() == 2 => {
                let (a, b) = (v[0], v[1]);
                let lo = |x: f64, y: f64| if n == "min" { x.min(y) } else { x.max(y) };
                if !(a.known || b.known) {
                    return Iv::UNKNOWN;
                }
                // an unknown side is (-inf, inf): min(x, c) <= c and max(x, c) >= c still hold
                let (a, b) = (if a.known { a } else { Iv::whole() }, if b.known { b } else { Iv::whole() });
                let c = [(lo(a.lo, b.lo), a.lo_open && b.lo_open), (lo(a.hi, b.hi), a.hi_open && b.hi_open)];
                Iv::range(c[0].0, c[1].0, c[0].1, c[1].1)
            }
            "pow" | "pwr" if v.len() == 2 => {
                let what = format!("{}({},{})", n, sh(0), sh(1));
                if n == "pow" || (v[0].known && v[0].lo >= 0.0) {
                    return self.pow(&what, v[0], v[1]);
                }
                if !(v[0].known && v[1].known) {
                    self.unproved(format!("{}: base or exponent range is not known", what));
                    return Iv::UNKNOWN;
                }
                if v[0].hi <= 0.0 {
                    let p = self.pow(&what, v[0].neg(), v[1]);
                    return p.neg();
                }
                self.unproved(format!("{}: the base changes sign; pwr() is not evaluated across 0", what));
                Iv::UNKNOWN
            }
            "ternary_fcn" if v.len() == 3 => Iv::hull(v[1], v[2]),
            _ => {
                self.unproved(format!("{}() is not evaluated", n));
                Iv::UNKNOWN
            }
        }
    }
}

/// The environment in which the condition `c` is `truth`, or None when no value can make it so. Only a plain name
/// compared with an expression narrows (`x > 0 ? ln(x) : 0` is the guard idiom); anything else leaves the ranges alone.
fn narrow(env: &Env, c: &E, truth: bool, ev: &mut Ev, depth: usize) -> Option<Env> {
    match c {
        E::Not(a) => narrow(env, a, !truth, ev, depth),
        E::Bin(Op::And, l, r) if truth => narrow(&narrow(env, l, true, ev, depth)?, r, true, ev, depth),
        E::Bin(Op::Or, l, r) if !truth => narrow(&narrow(env, l, false, ev, depth)?, r, false, ev, depth),
        E::Bin(op, l, r) if op.is_cmp() => {
            // x op y must hold (op negated for a false condition)
            let op = if truth {
                *op
            } else {
                match op { Op::Lt => Op::Ge, Op::Le => Op::Gt, Op::Gt => Op::Le, _ => Op::Lt }
            };
            // the name (a param, or a node a source holds) on either side narrows; the other side is its bound
            let is_tgt = |x: &E| match x {
                E::Var(n) => env.vars.get(n).is_some_and(|v| v.known),
                E::V(ns) => ns.len() == 1 && env.nodes.contains_key(&ns[0]),
                _ => false,
            };
            let flip = |op: Op| match op { Op::Lt => Op::Gt, Op::Le => Op::Ge, Op::Gt => Op::Lt, _ => Op::Le };
            let (target, op, other) = if is_tgt(l) {
                (&**l, op, &**r)
            } else if is_tgt(r) {
                (&**r, flip(op), &**l)
            } else {
                return Some(env.clone());
            };
            let mut quiet = Ev::default();
            let (o, mut v) = (quiet.ev(other, env, depth), quiet.ev(target, env, depth));
            if !(o.known && v.known) {
                return Some(env.clone());
            }
            match op {
                Op::Gt | Op::Ge => {
                    let strict = op == Op::Gt || o.lo_open;
                    if o.lo > v.lo || (o.lo == v.lo && strict) {
                        v.lo = o.lo;
                        v.lo_open = strict;
                    }
                }
                _ => {
                    let strict = op == Op::Lt || o.hi_open;
                    if o.hi < v.hi || (o.hi == v.hi && strict) {
                        v.hi = o.hi;
                        v.hi_open = strict;
                    }
                }
            }
            if v.is_empty() {
                return None;
            }
            let mut en = env.clone();
            match target {
                E::Var(n) => {
                    en.vars.insert(n.clone(), v);
                }
                E::V(ns) => {
                    en.nodes.insert(ns[0].clone(), Node::Fixed(v));
                }
                _ => {}
            }
            Some(en)
        }
        _ => Some(env.clone()),
    }
}

// ---------------------------------------------------------------------------------------------------- analysis

/// What the check of one expression found.
#[derive(Debug, Default)]
pub struct Outcome {
    /// Reproduced with exact point arithmetic: the value ngspice would get wrong or fail on.
    pub errors: Vec<Issue>,
    /// What the check could not settle, one line each (an unknown range, an operation not evaluated, a hit no sample
    /// reproduces). The caller keeps its LIMITED-ANALYSIS note for these.
    pub unproved: Vec<String>,
}

fn collect(e: &E, env: &Env, vars: &mut Vec<String>, nodes: &mut Vec<String>, depth: usize) {
    match e {
        E::Var(n) => {
            if env.vars.get(n).is_some_and(|v| v.known && v.lo != v.hi) && !vars.contains(n) {
                vars.push(n.clone());
            }
        }
        E::V(ns) => {
            for n in ns {
                match env.nodes.get(n) {
                    Some(Node::Fixed(iv)) if iv.known && iv.lo != iv.hi && !nodes.contains(n) => nodes.push(n.clone()),
                    Some(Node::Expr(x)) if depth < 8 => collect(x, env, vars, nodes, depth + 1),
                    _ => {}
                }
            }
        }
        E::Neg(a) | E::Not(a) => collect(a, env, vars, nodes, depth),
        E::Bin(_, a, b) => {
            collect(a, env, vars, nodes, depth);
            collect(b, env, vars, nodes, depth);
        }
        E::Call(_, a) => a.iter().for_each(|x| collect(x, env, vars, nodes, depth)),
        E::Tern(c, a, b) => {
            collect(c, env, vars, nodes, depth);
            collect(a, env, vars, nodes, depth);
            collect(b, env, vars, nodes, depth);
        }
        E::Num(_) | E::Unk(_) => {}
    }
}

/// Sample points of a range: the ends (nudged inside an open end), the middle and the quarter points, and 0 when inside.
fn samples(v: &Iv) -> Vec<f64> {
    let span = |lo: f64, hi: f64| if hi - lo < 1e300 { hi - lo } else { 1e300 };
    let (lo, hi) = (if v.lo.is_finite() { v.lo } else { v.hi.min(0.0) - 1e6 }, if v.hi.is_finite() { v.hi } else { v.lo.max(0.0) + 1e6 });
    let w = span(lo, hi);
    let mut s = vec![
        if v.lo_open && v.lo.is_finite() { lo + w * 1e-6 } else { lo },
        if v.hi_open && v.hi.is_finite() { hi - w * 1e-6 } else { hi },
        lo + w * 0.5,
        lo + w * 0.25,
        lo + w * 0.75,
        lo + w * 0.01,
        lo + w * 0.99,
    ];
    if v.at(0.0) {
        s.push(0.0);
    }
    s
}

/// Sample the ranges `e` reads, every one at once, evaluating with exact point arithmetic. -> the distinct hits (each
/// with the first sample that reproduces it) and, per divisor, the first sample where it was positive and negative.
/// `zero`: ngspice's first Newton iterate, where every node voltage and branch current is 0, so only params vary.
fn sweep(e: &E, env: &Env, zero: bool) -> (Vec<Issue>, Vec<(String, Option<String>, Option<String>)>) {
    let (mut vars, mut nodes) = (vec![], vec![]);
    collect(e, env, &mut vars, &mut nodes, 0);
    let mut axes: Vec<(bool, String, Vec<f64>)> = vec![];
    for n in &vars {
        axes.push((true, n.clone(), samples(&env.vars[n])));
    }
    if !zero {
        for n in &nodes {
            if let Some(Node::Fixed(iv)) = env.nodes.get(n) {
                axes.push((false, n.clone(), samples(iv)));
            }
        }
    }
    let total: usize = axes.iter().map(|a| a.2.len()).product::<usize>().max(1);
    let mut seen: Vec<Issue> = vec![];
    let mut signs: Vec<(String, Option<String>, Option<String>)> = vec![];
    let mut state: u64 = 0x9E3779B97F4A7C15;
    for k in 0..total.min(4096) {
        let mut pe = env.clone();
        pe.zero = zero;
        let mut w = vec![];
        let mut idx = k;
        for (is_var, name, vals) in &axes {
            let x = if total <= 4096 {
                let x = vals[idx % vals.len()];
                idx /= vals.len();
                x
            } else {
                state = state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
                vals[(state >> 33) as usize % vals.len()]
            };
            if *is_var {
                pe.vars.insert(name.clone(), Iv::point(x));
                w.push(format!("{} = {}", name.to_uppercase(), g(x)));
            } else {
                pe.nodes.insert(name.clone(), Node::Fixed(Iv::point(x)));
                w.push(format!("V({}) = {}", name, g(x)));
            }
        }
        let mut pv = Ev::default();
        pv.ev(e, &pe, 0);
        for (what, val) in pv.dens {
            let k = match signs.iter().position(|s| s.0 == what) {
                Some(k) => k,
                None => {
                    signs.push((what, None, None));
                    signs.len() - 1
                }
            };
            let slot = if val > 0.0 { &mut signs[k].1 } else { &mut signs[k].2 };
            slot.get_or_insert_with(|| w.join(", "));
        }
        for i in pv.issues {
            // one witness per distinct hit: the first sample that reproduces it
            if !seen.iter().any(|k| k.code == i.code && k.what == i.what) {
                let at = if w.is_empty() { String::new() } else { format!(" (at {})", w.join(", ")) };
                seen.push(Issue { msg: format!("{}{}", i.msg, at), ..i });
            }
        }
    }
    (seen, signs)
}

/// Check one expression over the environment's ranges.
pub fn analyse(text: &str, env: &Env) -> Outcome {
    let mut out = Outcome::default();
    let e = match parse(text.trim().trim_matches(|c| c == '{' || c == '}' || c == '\'')) {
        Ok(e) => e,
        Err(why) => {
            out.unproved.push(format!("the expression is not parsed ({})", why));
            return out;
        }
    };
    let mut ev = Ev::default();
    ev.ev(&e, env, 0);
    out.unproved = ev.unproved;
    let mut seen = vec![];
    if !ev.issues.is_empty() {
        let (found, signs) = sweep(&e, env, false);
        seen = found;
        // a divisor that is positive at one sample and negative at another passes through 0 between them
        for (what, pos, neg) in signs {
            if let (Some(p), Some(n)) = (pos, neg) {
                if !seen.iter().any(|k| k.code == "B-DIVZERO" && k.what == what) {
                    let msg = format!("division by `{}`, which is positive at {} and negative at {}, so it passes through 0 (ngspice silently returns about 1e32)", what, p, n);
                    seen.push(Issue { code: "B-DIVZERO", what, msg, fatal: false });
                }
            }
        }
        // a hit the sample did not reproduce is unproved, not an error
        for i in &ev.issues {
            if !seen.iter().any(|s| s.code == i.code && s.what == i.what) {
                out.unproved.push(format!("cannot rule out: {}", i.msg));
            }
        }
    }
    // ngspice's first Newton iterate has every node voltage and branch current at 0, in every analysis (probed:
    // sqrt(V(in) - 4) on a 5 V source fails .op, .tran and .tran uic): a fatal function there kills the run
    let mut zero = Ev::default();
    let ez = Env { zero: true, ..env.clone() };
    zero.ev(&e, &ez, 0);
    if zero.issues.iter().any(|i| i.fatal) {
        for i in sweep(&e, env, true).0.into_iter().filter(|i| i.fatal) {
            if !seen.iter().any(|s| s.code == i.code && s.what == i.what) {
                seen.push(Issue { msg: format!("{}; this is ngspice's first iteration, where every node voltage and branch current starts at 0", i.msg), ..i });
            }
        }
    }
    out.errors = seen;
    out
}

/// Value of a parameter expression (`10k`, `{R0*2}`, `{LO+(LC-LO)*POS}`) in `env`; UNKNOWN when it cannot be evaluated.
pub fn value(text: &str, env: &Env) -> Iv {
    match parse(text.trim().trim_matches(|c| c == '{' || c == '}' || c == '\'')) {
        Ok(e) => {
            let mut ev = Ev::default();
            let v = ev.ev(&e, env, 0);
            if ev.issues.is_empty() && !v.is_empty() { v } else { Iv::UNKNOWN }
        }
        Err(_) => Iv::UNKNOWN,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env(vars: &[(&str, Iv)]) -> Env {
        Env { vars: vars.iter().map(|(k, v)| (k.to_string(), *v)).collect(), nodes: HashMap::new(), zero: false }
    }

    fn pt(text: &str) -> f64 {
        let v = value(text, &Env::default());
        assert!(v.is_point(), "{} -> {}", text, v);
        v.lo
    }

    /// Point arithmetic matches what ngspice 47 prints for the same text (probes/domain_probe.py).
    #[test]
    fn precedence_and_functions_match_ngspice() {
        assert_eq!(pt("-2^2"), -4.0);
        assert_eq!(pt("2^3^2"), 64.0);
        assert_eq!(pt("2**3"), 8.0);
        assert_eq!(pt("8/2/2"), 2.0);
        assert_eq!(pt("1-2-3"), -4.0);
        assert_eq!(pt("1e3+1k"), 2000.0);
        assert_eq!(pt("10meg/1meg"), 10.0);
        assert_eq!(pt("1<2 ? 5 : 6"), 5.0);
        assert_eq!(pt("1>2 ? 5 : 6"), 6.0);
        assert_eq!(pt("{pow(2,0.5)}"), 2f64.sqrt());
        assert_eq!(pt("sqrt(4)+ln(1)+abs(-3)"), 5.0);
        assert_eq!(pt("min(2,3)+max(2,3)"), 5.0);
        assert_eq!(pt("sgn(-4)"), -1.0);
        assert_eq!(pt("u(-1)+u(1)"), 1.0);
        assert_eq!(pt("2^-1"), 0.5);
        assert_eq!(pt("1.5e-3*2"), 3e-3);
        assert_eq!(pt("3.9083e-3 tc1=0.01"), 3.9083e-3);
    }

    fn found(text: &str, vars: &[(&str, Iv)]) -> (Vec<&'static str>, usize) {
        let o = analyse(text, &env(vars));
        (o.errors.iter().map(|i| i.code).collect(), o.unproved.len())
    }

    #[test]
    fn each_domain_error_is_found_with_a_witness() {
        let x = Iv::range(-1.0, 1.0, false, false);
        for (text, code) in [
            ("sqrt(x)", "B-DOMAIN"), ("ln(x)", "B-DOMAIN"), ("log10(x)", "B-DOMAIN"), ("1/x", "B-DIVZERO"),
            ("asin(2*x)", "B-DOMAIN"), ("acosh(x)", "B-DOMAIN"), ("atanh(x)", "B-DOMAIN"), ("pow(x,0.5)", "B-DOMAIN"),
            ("x^0.5", "B-DOMAIN"), ("pow(x,-1)", "B-DOMAIN"), ("exp(300*x)", "B-OVERFLOW"), ("sinh(900*x)", "B-OVERFLOW"),
            ("pow(10,400*(x+1))", "B-OVERFLOW"),
        ] {
            let o = analyse(text, &env(&[("x", x)]));
            assert!(o.errors.iter().any(|i| i.code == code), "{}: {:?}", text, o);
            assert!(o.errors.iter().all(|i| i.msg.contains("(at X = ")), "{}: no witness: {:?}", text, o);
        }
    }

    #[test]
    fn safe_expressions_are_proved_and_say_nothing() {
        let t = Iv::range(0.0, 850.0, false, false);
        let lux = Iv::range(0.0, 1e5, true, false); // exclusive 0, the LDR
        for (text, vars) in [
            ("1/(10k*pow(x/10,-0.7))", vec![("x", lux)]),
            ("1/(100*(1+3.9083e-3*x-5.775e-7*x*x))", vec![("x", t)]),
            ("sqrt(x)", vec![("x", t)]),
            ("ln(x)", vec![("x", lux)]),
            ("1/(1+exp(-(x-120)/3))", vec![("x", Iv::range(-55.0, 200.0, false, false))]),
            ("x>0 ? ln(x) : 0", vec![("x", Iv::range(-1.0, 1.0, false, false))]),
            ("x<=0 ? 0 : 1/x", vec![("x", Iv::range(-1.0, 1.0, false, false))]),
            ("1/(1+abs(x))", vec![("x", Iv::range(-5.0, 5.0, false, false))]),
            ("1/(2+sin(x))", vec![("x", Iv::UNKNOWN)]),
            ("1/exp(x)", vec![("x", Iv::range(-200.0, 200.0, false, false))]),
            ("1/max(x,1m)", vec![("x", Iv::UNKNOWN)]),
        ] {
            let o = analyse(text, &env(&vars));
            assert!(o.errors.is_empty() && o.unproved.is_empty(), "{}: {:?}", text, o);
        }
    }

    #[test]
    fn unknown_ranges_are_unproved_never_errors() {
        // V(x) is a circuit node: nothing is known about it
        for text in ["1/v(x)", "sqrt(v(x))", "ln(i(v1))", "pow(v(x),0.5)", "1/y", "foo(1)", "exp(v(x))", "1/(v(a)+v(b))"] {
            let o = analyse(text, &Env::default());
            assert!(o.errors.is_empty() && !o.unproved.is_empty(), "{}: {:?}", text, o);
        }
        // but an unknown numerator or an unknown operand of + - * is not a hazard
        for text in ["v(a,b)/1k", "v(a,b)*(1/3+v(c))", "i(v1)*2-v(x)", "-v(a)^2"] {
            let o = analyse(text, &Env::default());
            assert!(o.errors.is_empty() && o.unproved.is_empty(), "{}: {:?}", text, o);
        }
    }

    #[test]
    fn interval_overestimates_are_not_reported_as_errors() {
        // x - x is 0 only: 1/(1+x-x) is fine, but the interval sees [1-w, 1+w]; here w=2 so it contains 0
        let x = Iv::range(-1.0, 1.0, false, false);
        let (errs, unproved) = found("1/(0.5+x-x)", &[("x", x)]);
        assert!(errs.is_empty(), "no sample reaches 0");
        assert_eq!(unproved, 1, "the interval cannot rule it out");
    }

    #[test]
    fn open_ends_and_unbounded_poles_are_tolerated() {
        // (0, 10]: x never is 0, so 1/x is unbounded but defined; ln(x) -> -inf is a limit, not a hit
        let x = Iv::range(0.0, 10.0, true, false);
        for text in ["1/x", "ln(x)", "pow(x,-0.7)", "1/(1/x)", "x/x"] {
            let o = analyse(text, &env(&[("x", x)]));
            assert!(o.errors.is_empty(), "{}: {:?}", text, o);
        }
        // the same range closed at 0 is a hit
        let z = Iv::range(0.0, 10.0, false, false);
        assert!(!analyse("1/x", &env(&[("x", z)])).errors.is_empty());
    }

    #[test]
    fn stimulus_nodes_follow_their_source_expression() {
        let mut en = env(&[("lux", Iv::range(1.0, 100.0, false, false))]);
        en.nodes.insert("stim".into(), Node::Expr(parse("lux").unwrap()));
        // V(stim) - lux is exactly 0 (the node is the parameter), so 1/(1+V(stim)-lux) is fine: the sample sees it
        let o = analyse("1/(0.5+v(stim)-lux)", &en);
        assert!(o.errors.is_empty(), "{:?}", o);
        // 50.5 is the middle sample of [1, 100]: a sample must land on the zero to call it an error
        let o = analyse("1/(v(stim)-50.5)", &en);
        assert!(o.errors.iter().any(|i| i.code == "B-DIVZERO" && i.msg.contains("LUX = 50.5")), "{:?}", o);
        // a zero between two samples is found too: the divisor is positive at one and negative at another
        let o = analyse("1/(v(stim)-50)", &en);
        assert!(o.errors.iter().any(|i| i.code == "B-DIVZERO" && i.msg.contains("positive at LUX = 100") && i.msg.contains("negative at LUX = 1")), "{:?}", o);
        // a guard narrows a node as well as a param
        let o = analyse("v(stim)>10 ? 1/(v(stim)-5) : 0", &en);
        assert!(o.errors.is_empty() && o.unproved.is_empty(), "{:?}", o);
    }

    #[test]
    fn parse_failures_are_unproved() {
        for text in ["1 +", "foo bar", "v(", "1 ? 2", "3 $ 4"] {
            let o = analyse(text, &Env::default());
            assert!(o.errors.is_empty() && o.unproved.len() == 1, "{}: {:?}", text, o);
        }
    }

    #[test]
    fn multiplication_and_powers_keep_the_right_ends() {
        let m = |a: Iv, b: Iv| a.mul(b);
        let r = m(Iv::range(-2.0, 3.0, false, false), Iv::range(-5.0, 4.0, false, false));
        assert_eq!((r.lo, r.hi), (-15.0, 12.0));
        let r = m(Iv::range(0.0, 1.0, true, false), Iv::range(0.0, INF, false, true));
        assert_eq!((r.lo, r.hi, r.lo_open), (0.0, INF, false), "a closed 0 end keeps the product closed");
        let mut ev = Ev::default();
        let p = ev.pow("p", Iv::range(-3.0, 2.0, false, false), Iv::point(2.0));
        assert_eq!((p.lo, p.hi), (0.0, 9.0));
        let p = ev.pow("p", Iv::range(-3.0, -1.0, false, false), Iv::point(3.0));
        assert_eq!((p.lo, p.hi), (-27.0, -1.0));
        assert!(ev.issues.is_empty());
    }
}
