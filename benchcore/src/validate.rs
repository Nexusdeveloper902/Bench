//! Static topology + component-terminal validator (pre-simulation).
//!
//! Direct port of the validated `exp-safety/validator.py` logic:
//! DC-conductance BFS, ISRC-OPEN (D4 silent class), V-LOOP union-find,
//! NO-GROUND, DUP-NAME, MALFORMED, degenerate values, B-expression checks.
//! Severity `error` rejects before simulation; `warn` proceeds with review.
//!
//! Subcircuits (DESIGN 4.6, ngspice 47 semantics, probed): the deck is read into a tree of definitions
//! (`.subckt` bodies, nested ones included). Every definition an instance reaches is checked once (element
//! syntax, model and subckt references through the lexical scope chain, port and param lists, duplicates);
//! parameters are resolved per instance chain (ngspice scopes them dynamically); connectivity runs on the
//! flattened circuit (ports map to the instance's nets, `0`/`gnd`/`.global` nodes stay global, internal
//! nodes are prefixed with the instance path, `x1.xa.n2`). Definitions no instance reaches are not analysed,
//! as ngspice ignores them. What the parser cannot judge gets LIMITED-ANALYSIS, never a silent pass.
//! A clean result proves syntax, references and DC topology only, never electrical well-posedness.

use std::collections::{HashMap, HashSet};

pub const GROUND: &str = "0";

#[derive(Debug, Clone, PartialEq)]
pub struct Finding {
    pub code: String,
    pub severity: String, // "error" | "warn"
    pub message: String,
    pub nodes: Vec<String>,
    pub devices: Vec<String>,
    pub layer: String,
    /// "" top level; ".subckt <name>" a definition (reported once, whatever the instance count);
    /// an instance path such as "x1.xa" (connectivity and parameters, per instance).
    pub scope: String,
}

fn finding(
    code: &str,
    severity: &str,
    message: String,
    nodes: Vec<String>,
    devices: Vec<String>,
    layer: &str,
) -> Finding {
    Finding {
        code: code.to_string(),
        severity: severity.to_string(),
        message,
        nodes,
        devices,
        layer: layer.to_string(),
        scope: String::new(),
    }
}

/// Diagnostic class of a code. The five the spec keeps apart, plus "value" for numeric and expression checks.
pub fn class_of(code: &str) -> &'static str {
    match code {
        "MALFORMED" | "DUP-NAME" | "SUBCKT-UNBALANCED" => "syntax",
        "UNDEFINED-MODEL" | "UNDEFINED-SUBCKT" | "UNDEFINED-PARAM" | "UNDEFINED-ELEMENT" | "UNKNOWN-INSTANCE-PARAM"
        | "DUP-SUBCKT" | "DUP-MODEL" | "SUBCKT-RECURSION" => "dependency",
        "NO-GROUND" | "NO-DC-PATH" | "ISRC-OPEN" | "V-LOOP" | "DANGLING" | "SHORTED-VSRC" | "SHORTED-PASSIVE" => {
            "connectivity"
        }
        "UNKNOWN-KIND" | "UNSUPPORTED" => "unsupported",
        "LIMITED-ANALYSIS" | "OPAQUE-X" => "limited",
        _ => "value", // BAD-VALUE NEG-R NEG-CL ZERO-R ZERO-CL B-*
    }
}

fn limited(name: &str, message: String) -> Finding {
    finding("LIMITED-ANALYSIS", "warn", message, vec![], vec![name.to_string()], "static")
}

fn is_ground(n: &str) -> bool {
    n == GROUND || n.eq_ignore_ascii_case("gnd") // ngspice: `gnd` is node 0, in bodies too (probed)
}

/// Parse ngspice number with scale suffix + optional unit ("1k", "1mH", "1meg").
/// Longest scale prefix wins ("meg" before "m"). Returns None if unparseable.
pub fn parse_value(tok: &str) -> Option<f64> {
    let s = tok.trim();
    if s.is_empty() {
        return None;
    }
    // split leading numeric part from suffix
    let mut num_end = 0usize;
    let bytes = s.as_bytes();
    let mut i = 0;
    if i < bytes.len() && (bytes[i] == b'+' || bytes[i] == b'-') {
        i += 1;
    }
    let mut has_digit = false;
    let mut has_dot = false;
    while i < bytes.len() {
        let c = bytes[i];
        if c.is_ascii_digit() {
            has_digit = true;
            i += 1;
        } else if c == b'.' && !has_dot {
            has_dot = true;
            i += 1;
        } else {
            break;
        }
    }
    // exponent
    if i < bytes.len() && (bytes[i] == b'e' || bytes[i] == b'E') {
        let mut j = i + 1;
        if j < bytes.len() && (bytes[j] == b'+' || bytes[j] == b'-') {
            j += 1;
        }
        let mut exp_digits = 0;
        while j < bytes.len() && bytes[j].is_ascii_digit() {
            j += 1;
            exp_digits += 1;
        }
        if exp_digits > 0 {
            i = j;
        }
    }
    if !has_digit {
        return None;
    }
    num_end = i;
    let num: f64 = s[..num_end].parse().ok()?;
    let suf = s[num_end..].trim().to_lowercase();
    if suf.is_empty() {
        return Some(num);
    }
    if suf.starts_with("meg") {
        return Some(num * 1e6);
    }
    let first = suf.chars().next().unwrap();
    match first {
        'f' => Some(num * 1e-15),
        'p' => Some(num * 1e-12),
        'n' => Some(num * 1e-9),
        'u' => Some(num * 1e-6),
        'm' => Some(num * 1e-3),
        'k' => Some(num * 1e3),
        'g' => Some(num * 1e9),
        't' => Some(num * 1e12),
        _ => {
            if first.is_ascii_alphabetic() {
                Some(num) // pure unit, no scale
            } else {
                None
            }
        }
    }
}

// ---- device definition table (ngspice 47, probed; DESIGN.md 3.1 / 4.2) ----

/// What follows an element's node list, i.e. how the parser finds where the nodes end.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Tail {
    /// R C L: a value (or a model name, or an `R=` expression).
    Value,
    /// V I: an optional source spec (DC, PULSE, SIN, PWL, ...).
    Source,
    /// B: a `V=` or `I=` expression.
    Behav,
    /// E G: `c+ c- gain` | `VALUE=` / `vol=` / `cur=` | `TABLE {..}` | `POLY(n) <2n nodes> coeffs`.
    Ctrl,
    /// F H: `Vsense gain` | `POLY(n) Vsense.. coeffs` (names another element).
    Sense,
    /// W: `Vsense model`.
    SenseModel,
    /// K: `L1 L2 k` (no nodes; names two inductors).
    Couple,
    /// D Q J M Z S O U Y P: nodes until the first token that names a `.model`.
    Model,
    /// T: `key=value` params (Z0, TD, F, NL).
    Params,
    /// A: XSPICE port lists (`[a b]`, `~in`, `%vd(a b)`, `NULL`), then the model.
    Ports,
    /// X: ports, subckt name, params.
    Subckt,
}

/// Which node pairs conduct at DC (for NO-DC-PATH); control inputs never do.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Dc {
    /// No DC edge (C, I, G, F, K).
    None,
    /// Between the first two nodes (R, L, V, D, E/H outputs, S/W switch pair, U, B with `V=`).
    Pair,
    /// Between every pair of nodes (Q, J, Z, X, A, P: optimistic, never a false NO-DC-PATH).
    AllPairs,
    /// Every pair except the insulated gate, node 2 (M, incl. VDMOS).
    NoGate,
    /// Port 1 (nodes 1-2) and port 2 (nodes 3-4) of a two-port line (T, O, Y).
    PortPairs,
}

/// One element letter as ngspice 47 reads it. `models`: accepted `.model` types (lower case).
/// `covered`: the static checks model it fully; otherwise an EXOTIC-DEVICE warning says the
/// runtime verdict is load-bearing.
#[derive(Debug, Clone, Copy)]
pub struct DeviceSpec {
    pub letter: char,
    pub min_nodes: usize,
    pub max_nodes: usize,
    pub tail: Tail,
    pub models: &'static [&'static str],
    pub dc: Dc,
    pub covered: bool,
}

const fn dev(letter: char, min_nodes: usize, max_nodes: usize, tail: Tail, models: &'static [&'static str], dc: Dc, covered: bool) -> DeviceSpec {
    DeviceSpec { letter, min_nodes, max_nodes, tail, models, dc, covered }
}

/// Every element letter this ngspice build accepts. N (OSDI) is absent: no `.osdi` models are installed.
pub const DEVICES: &[DeviceSpec] = &[
    dev('R', 2, 2, Tail::Value, &[], Dc::Pair, true),
    dev('C', 2, 2, Tail::Value, &[], Dc::None, true),
    dev('L', 2, 2, Tail::Value, &[], Dc::Pair, true),
    dev('K', 0, 0, Tail::Couple, &[], Dc::None, true),
    dev('V', 2, 2, Tail::Source, &[], Dc::Pair, true),
    dev('I', 2, 2, Tail::Source, &[], Dc::None, true),
    dev('B', 2, 2, Tail::Behav, &[], Dc::Pair, true), // Pair only with V= (see dc_edges)
    dev('E', 2, 4, Tail::Ctrl, &[], Dc::Pair, true),
    dev('G', 2, 4, Tail::Ctrl, &[], Dc::None, true),
    dev('F', 2, 2, Tail::Sense, &[], Dc::None, true),
    dev('H', 2, 2, Tail::Sense, &[], Dc::Pair, true),
    dev('D', 2, 2, Tail::Model, &["d"], Dc::Pair, true),
    dev('Q', 3, 5, Tail::Model, &["npn", "pnp"], Dc::AllPairs, true),
    dev('J', 3, 3, Tail::Model, &["njf", "pjf"], Dc::AllPairs, true),
    dev('M', 3, 7, Tail::Model, &["nmos", "pmos", "vdmos"], Dc::NoGate, true),
    dev('Z', 3, 3, Tail::Model, &["nmf", "pmf"], Dc::AllPairs, true),
    dev('S', 4, 4, Tail::Model, &["sw"], Dc::Pair, true),
    dev('W', 2, 2, Tail::SenseModel, &["csw"], Dc::Pair, true),
    dev('T', 4, 4, Tail::Params, &[], Dc::PortPairs, false),
    dev('O', 4, 4, Tail::Model, &["ltra"], Dc::PortPairs, false),
    dev('U', 3, 3, Tail::Model, &["urc"], Dc::Pair, false),
    dev('Y', 4, 4, Tail::Model, &["txl"], Dc::PortPairs, false),
    dev('P', 4, 64, Tail::Model, &["cpl"], Dc::AllPairs, false),
    dev('A', 1, usize::MAX, Tail::Ports, &[], Dc::AllPairs, false),
    dev('X', 1, usize::MAX, Tail::Subckt, &[], Dc::AllPairs, true), // flattened when its .subckt is in the deck
];

/// Functions an ngspice 47 expression may call, in `{}` (numparam) or in a B / E-VALUE expression (probed one
/// deck per name; v() and i() are handled apart). The union of both contexts: a name valid in only one of them
/// passes here and fails in the engine. `if` and `table` are PSpice (UNSUPPORTED via pspice_only_fn).
const FUNCS: &[&str] = &[
    "sqrt", "sqr", "sin", "cos", "tan", "asin", "acos", "atan", "arctan", "sinh", "cosh", "tanh", "asinh", "acosh",
    "atanh", "exp", "ln", "log", "log10", "abs", "int", "nint", "floor", "ceil", "sgn", "u", "u2", "uramp", "agauss",
    "gauss", "aunif", "unif", "pow", "pwr", "max", "min", "ternary_fcn", "limit", "ddt", "pwl", "if", "table",
];
/// Names an expression may read without a `.param` (probed: pi and e only outside `{}`).
const CONSTS: &[&str] = &["time", "temper", "hertz", "pi", "e"];
/// Flattening stops past this many elements (LIMITED-ANALYSIS); a guard against exponential nesting.
const MAX_FLAT: usize = 100_000;

pub fn device_spec(letter: char) -> Option<&'static DeviceSpec> {
    let l = letter.to_ascii_uppercase();
    DEVICES.iter().find(|d| d.letter == l)
}

/// Model `level`s this ngspice build has, per model type (probed one process per level). A type not
/// listed takes no level. Missing: D 2; Q 3, 5, 6 (Mextram), 7, 10+; J 3+; M 7, 11-13, 15-48, 50-53, 59,
/// 61-67, 69-72, 74+; Z 7+. M 60 (SOI3) exists but aborts the process (spGetElement assertion) with
/// fewer than 6 nodes, so it is refused.
pub const LEVELS: &[(&str, &[u32])] = &[
    ("d", &[1, 3]),
    ("npn", &[1, 2, 4, 8, 9]),
    ("pnp", &[1, 2, 4, 8, 9]),
    ("njf", &[1, 2]),
    ("pjf", &[1, 2]),
    ("nmos", &[1, 2, 3, 4, 5, 6, 8, 9, 10, 14, 49, 54, 55, 56, 57, 58, 68, 73]),
    ("pmos", &[1, 2, 3, 4, 5, 6, 8, 9, 10, 14, 49, 54, 55, 56, 57, 58, 68, 73]),
    ("nmf", &[1, 2, 3, 4, 5, 6]),
    ("pmf", &[1, 2, 3, 4, 5, 6]),
];

/// Model types other simulators use that this build rejects or misreads, with the reason.
const FOREIGN_MODELS: &[(&str, &str)] = &[
    ("vswitch", "PSpice VSWITCH needs ngbehavior=ps, which Bench pins off; use SW"),
    ("iswitch", "PSpice ISWITCH needs ngbehavior=ps, which Bench pins off; use CSW"),
    ("lpnp", "lateral PNP (LPNP) is not in ngspice 47; use PNP"),
];

/// Max nodes a Q/M model takes (ngspice 47: GP BJT 4, VBIC/HICUM 5; non-SOI MOSFET 4, SOI 7, VDMOS 5).
fn node_cap(kind: char, ty: &str, lv: u32) -> Option<usize> {
    match (kind, ty) {
        ('Q', "npn" | "pnp") => Some(if [4, 8, 9].contains(&lv) { 5 } else { 4 }),
        ('M', "nmos" | "pmos") => Some(if [10, 55, 56, 57, 58].contains(&lv) { 7 } else { 4 }),
        ('M', "vdmos") => Some(5),
        _ => None,
    }
}

/// First PSpice-only function call in an element line (`if(`, `limit(`, `table(`): ngspice 47 either
/// aborts on them (if, table) or silently computes something else (limit). `skip` is a token index
/// that may legitimately be a keyword (E/G `TABLE {..}`).
fn pspice_only_fn(parts: &[&str], skip: Option<usize>) -> Option<String> {
    for (i, t) in parts.iter().enumerate() {
        if Some(i) == skip {
            continue;
        }
        let low = t.to_lowercase();
        let b = low.as_bytes();
        for f in ["if", "limit", "table"] {
            let mut from = 0;
            while let Some(p) = low[from..].find(f) {
                let s = from + p;
                let e = s + f.len();
                let before_ok = s == 0 || !(b[s - 1].is_ascii_alphanumeric() || b[s - 1] == b'_');
                let after = low[e..].trim_start();
                let next_tok_paren = after.is_empty() && parts.get(i + 1).is_some_and(|n| n.starts_with('('));
                if before_ok && (after.starts_with('(') || next_tok_paren) {
                    return Some(f.to_string());
                }
                from = e;
            }
        }
    }
    None
}

/// XSPICE port tokens -> node names: drops `[`, `]`, `~`, `%type` modifiers, parens and NULL.
fn xspice_nodes(toks: &[&str]) -> Vec<String> {
    let mut out = vec![];
    for t in toks {
        let t = match t.strip_prefix('%') {
            Some(r) => r.trim_start_matches(|c: char| c.is_ascii_alphanumeric() || c == '_'),
            None => t,
        };
        for piece in t.split(['[', ']', '(', ')']) {
            let p = piece.trim_start_matches('~').to_lowercase();
            if !p.is_empty() && p != "null" {
                out.push(p);
            }
        }
    }
    out
}

/// `POLY(n)` starting at `parts[i]` (also `POLY (n)`) -> (n, the tokens after it).
fn poly<'a, 'b>(parts: &'a [&'b str], i: usize) -> Option<(usize, &'a [&'b str])> {
    let mut j = i;
    let mut spec = String::new();
    while j < parts.len() && !spec.contains(')') {
        spec.push_str(parts[j]);
        j += 1;
    }
    let up = spec.to_uppercase();
    let inner = up.strip_prefix("POLY")?.trim_start().strip_prefix('(')?;
    let close = inner.find(')')?;
    if close + 1 != inner.len() {
        return None;
    }
    let n = inner[..close].trim().parse::<usize>().ok().filter(|n| *n > 0)?;
    Some((n, &parts[j..]))
}

/// Model reference checks for one element (DESIGN 4.2): defined, right type for the letter,
/// level present in this build, node count accepted by that model. Empty when fine.
#[allow(clippy::too_many_arguments)]
fn check_model(
    name: &str,
    spec: &DeviceSpec,
    model: &str,
    n: usize,
    models: &HashMap<String, (String, u32)>,
    has_include: bool,
    nodes: &[String],
    raw: &str,
) -> Vec<Finding> {
    let kind = spec.letter;
    let err = |code: &str, msg: String| vec![finding(code, "error", msg, nodes.to_vec(), vec![name.to_string()], "authoring")];
    let Some((ty, lv)) = models.get(model) else {
        if has_include {
            return vec![]; // .include may supply the card
        }
        return err(
            "UNDEFINED-MODEL",
            format!("{}: model '{}' has no .model card in this deck (ngspice aborts the run)", name, model),
        );
    };
    if spec.models.is_empty() {
        return vec![]; // A: XSPICE code-model types are checked by ngspice ("Unknown model type")
    }
    if let Some((_, why)) = FOREIGN_MODELS.iter().find(|(t, _)| t == ty) {
        return err("UNSUPPORTED", format!("{}: model '{}' is {}: {}", name, model, ty.to_uppercase(), why));
    }
    if !spec.models.contains(&ty.as_str()) {
        let want: Vec<String> = spec.models.iter().map(|t| t.to_uppercase()).collect();
        return err(
            "MALFORMED",
            format!("{}: model '{}' is type {}; a {} device needs {}", name, model, ty.to_uppercase(), kind, want.join(" or ")),
        );
    }
    if let Some((_, lvls)) = LEVELS.iter().find(|(t, _)| t == ty).filter(|_| *lv != 0) {
        if !lvls.contains(lv) {
            let l: Vec<String> = lvls.iter().map(|x| x.to_string()).collect();
            return err(
                "UNSUPPORTED",
                format!(
                    "{}: model '{}' is {} level {}, which this ngspice build does not have (levels: {})",
                    name, model, ty.to_uppercase(), lv, l.join(", ")
                ),
            );
        }
    }
    if kind == 'M' && ty == "vdmos" {
        let thermal = raw.split_whitespace().any(|t| t.eq_ignore_ascii_case("thermal"));
        if !((n == 3 && !thermal) || (n == 5 && thermal)) {
            return err(
                "MALFORMED",
                format!(
                    "{}: a VDMOS takes 3 nodes (d g s), or 5 (d g s tj tc) with the 'thermal' flag; got {} nodes{}",
                    name, n, if thermal { " and 'thermal'" } else { "" }
                ),
            );
        }
    } else if kind == 'M' && n < 4 {
        return err("MALFORMED", format!("{}: model name '{}' in a node position; M needs 4 nodes + model", name, model));
    }
    if let Some(cap) = node_cap(kind, ty, *lv).filter(|_| *lv != 0) {
        if n > cap {
            return err(
                "MALFORMED",
                format!(
                    "{}: model '{}' (level {}) accepts at most {} nodes, got {} (ngspice: too many nodes)",
                    name, model, lv, cap, n
                ),
            );
        }
    }
    vec![]
}

/// Node-index pairs of `d` that conduct at DC (its `dc` rule, decided on the element's own text).
fn dc_edges(d: &Device) -> Vec<(usize, usize)> {
    let n = d.nodes.len();
    if n < 2 {
        return vec![];
    }
    let all = |skip: Option<usize>| -> Vec<(usize, usize)> {
        (0..n)
            .flat_map(|i| (i + 1..n).map(move |j| (i, j)))
            .filter(|(i, j)| Some(*i) != skip && Some(*j) != skip)
            .collect()
    };
    match d.dc {
        Dc::None => vec![],
        Dc::Pair => vec![(0, 1)],
        Dc::AllPairs => all(None),
        Dc::NoGate => all(Some(1)),
        Dc::PortPairs if n >= 4 => vec![(0, 1), (2, 3)],
        Dc::PortPairs => vec![(0, 1)],
    }
}

/// DC rule of one element (local names): the table's, except for current-output elements (G, B with `I=`):
/// they act as a conductance when they read the voltage across their own output (`I=V(a,b)/1k`,
/// `G1 a b a b 1m`), and are open at DC otherwise.
fn dc_rule(kind: char, raw: &str, nodes: &[String]) -> Dc {
    if !(kind == 'G' || (kind == 'B' && !b_is_voltage(raw))) {
        return device_spec(kind).map_or(Dc::None, |s| s.dc);
    }
    let mut ctrl: HashSet<String> = v_refs(raw).into_iter().collect();
    ctrl.extend(nodes.iter().skip(2).cloned());
    let outs: Vec<&String> = nodes.iter().take(2).filter(|n| !is_ground(n)).collect();
    if !outs.is_empty() && outs.iter().all(|n| ctrl.contains(*n)) {
        Dc::Pair
    } else {
        Dc::None
    }
}

#[derive(Debug, Clone)]
struct Device {
    name: String,
    nodes: Vec<String>,
    raw: String,
    kind: char,
    dc: Dc,
    sub: Option<usize>, // X: the .subckt definition it instantiates (index into Deck::defs)
}

fn is_ident(s: &str) -> bool {
    let mut c = s.chars();
    match c.next() {
        Some(a) if a.is_ascii_alphabetic() || a == '_' => (),
        _ => return false,
    }
    s.chars()
        .all(|ch| ch.is_ascii_alphanumeric() || ch == '_')
}

fn b_is_voltage(raw: &str) -> bool {
    // find V= or I= (word-ish) -- first assignment wins like python regex \b([VI])\s*=
    let up: String = raw.to_string();
    let chars: Vec<char> = up.chars().collect();
    let mut idx = 0;
    while idx < chars.len() {
        let c = chars[idx];
        if (c == 'V' || c == 'v' || c == 'I' || c == 'i') && idx > 0 {
            let prev = chars[idx - 1];
            if !(prev.is_ascii_alphanumeric() || prev == '_') {
                let mut j = idx + 1;
                while j < chars.len() && chars[j].is_whitespace() {
                    j += 1;
                }
                if j < chars.len() && chars[j] == '=' {
                    return c == 'V' || c == 'v';
                }
            }
        } else if idx == 0 && (c == 'V' || c == 'v' || c == 'I' || c == 'i') {
            let mut j = idx + 1;
            while j < chars.len() && chars[j].is_whitespace() {
                j += 1;
            }
            if j < chars.len() && chars[j] == '=' {
                return c == 'V' || c == 'v';
            }
        }
        idx += 1;
    }
    false
}

fn short_edge(kind: char, dev: &Device) -> bool {
    match kind {
        'V' | 'E' | 'H' => true,
        'B' => b_is_voltage(&dev.raw),
        'L' => true,
        'R' => {
            let parts: Vec<&str> = dev.raw.split_whitespace().collect();
            if parts.len() >= 4 {
                if let Some(v) = parse_value(parts[3]) {
                    return v == 0.0;
                }
            }
            false
        }
        _ => false,
    }
}

/// Lower-cased tokens before the first `key=value` param (also `key = value`, spaced).
fn positional(parts: &[&str]) -> Vec<String> {
    parts
        .iter()
        .enumerate()
        .take_while(|(i, t)| !t.contains('=') && !parts.get(i + 1).is_some_and(|n| n.starts_with('=')))
        .map(|(_, t)| t.to_lowercase())
        .collect()
}

// ---- deck structure: the definition tree (task 4, DESIGN 4.6) ----

/// Deck lines as ngspice reads them: title dropped; `*` lines, blank lines and inline comments (`;`, ` $`, ` //`)
/// removed; `+` continuations joined; `.control` blocks skipped; nothing after `.end`.
fn logical_lines(lines: &[String]) -> Vec<String> {
    let mut out: Vec<String> = vec![];
    let mut in_control = false;
    for raw in lines.iter().skip(1) {
        let mut s = raw.trim();
        let cut = [";", " $", "\t$", " //", "\t//"].iter().filter_map(|p| s.find(p)).min();
        if let Some(i) = cut {
            s = s[..i].trim_end();
        }
        if s.is_empty() || s.starts_with('*') || s.starts_with('$') || s.starts_with("//") {
            continue;
        }
        let low = s.to_lowercase();
        if low == ".control" {
            in_control = true;
        } else if low == ".endc" {
            in_control = false;
        } else if in_control {
        } else if low == ".end" {
            break;
        } else if let (Some(rest), Some(last)) = (s.strip_prefix('+'), out.last_mut()) {
            last.push(' ');
            last.push_str(rest.trim());
        } else {
            out.push(s.to_string());
        }
    }
    out
}

/// `s` after its first `n` whitespace-separated tokens.
fn after_tokens(s: &str, n: usize) -> &str {
    let mut rest = s.trim_start();
    for _ in 0..n {
        rest = rest[rest.find(char::is_whitespace).unwrap_or(rest.len())..].trim_start();
    }
    rest
}

/// Whitespace-separated tokens, keeping `{...}`, `(...)` and `'...'` groups whole (`r = {a + b}`).
fn group_tokens(s: &str) -> Vec<String> {
    let (mut out, mut cur, mut depth, mut quote) = (vec![], String::new(), 0i32, false);
    for c in s.chars() {
        match c {
            '\'' => quote = !quote,
            '{' | '(' if !quote => depth += 1,
            '}' | ')' if !quote => depth -= 1,
            _ => {}
        }
        if c.is_whitespace() && depth <= 0 && !quote {
            if !cur.is_empty() {
                out.push(std::mem::take(&mut cur));
            }
        } else {
            cur.push(c);
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

/// Positional tokens (lower case) and `name=value` pairs (name lower case) of an X line tail, a `.subckt`
/// header tail or a `.param` line: `a b s params: r = 2k q={x + 1}` -> ([a, b, s], [(r, 2k), (q, {x + 1})]).
fn args(s: &str) -> (Vec<String>, Vec<(String, String)>) {
    let t = group_tokens(s);
    let (mut pos, mut kv) = (vec![], vec![]);
    let mut i = 0;
    while i < t.len() {
        let tok = &t[i];
        i += 1;
        if tok.eq_ignore_ascii_case("params:") {
            continue;
        }
        let (k, mut v) = match tok.find('=') {
            Some(p) => (tok[..p].to_string(), tok[p + 1..].to_string()),
            None if t.get(i).is_some_and(|n| n.starts_with('=')) => {
                i += 1;
                (tok.clone(), t[i - 1][1..].to_string())
            }
            None => {
                pos.push(tok.to_lowercase());
                continue;
            }
        };
        if v.is_empty() {
            if let Some(n) = t.get(i) {
                v = n.clone();
                i += 1;
            }
        }
        kv.push((k.to_lowercase(), v));
    }
    (pos, kv)
}

/// Contents of the outermost `{...}` and `'...'` groups of a line (the ngspice expressions in it).
fn brace_groups(s: &str) -> Vec<String> {
    let (mut out, mut cur, mut depth, mut quote) = (vec![], String::new(), 0usize, false);
    for c in s.chars() {
        if quote {
            if c == '\'' {
                quote = false;
                out.push(std::mem::take(&mut cur));
            } else {
                cur.push(c);
            }
            continue;
        }
        match c {
            '{' => {
                if depth > 0 {
                    cur.push(c);
                }
                depth += 1;
            }
            '}' if depth > 0 => {
                depth -= 1;
                if depth == 0 {
                    out.push(std::mem::take(&mut cur));
                } else {
                    cur.push(c);
                }
            }
            '\'' if depth == 0 => quote = true,
            _ if depth > 0 => cur.push(c),
            _ => {}
        }
    }
    out
}

/// Names an expression reads: (lower-case name, called as a function). Skips numbers with their scale suffix
/// (`1k`, `2.5meg`, `1e-3`), the arguments of v() and i() (node and source names), `@dev[param]` vectors and
/// `name=` keywords (a B source's `tc1=`).
fn expr_idents(e: &str) -> Vec<(String, bool)> {
    let c: Vec<char> = e.chars().collect();
    let mut out = vec![];
    let mut i = 0;
    while i < c.len() {
        let ch = c[i];
        if ch == '@' {
            while i < c.len() && c[i] != ']' && !c[i].is_whitespace() {
                i += 1;
            }
        } else if ch.is_ascii_digit() || (ch == '.' && c.get(i + 1).is_some_and(|d| d.is_ascii_digit())) {
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
            while i < c.len() && (c[i].is_ascii_alphanumeric() || c[i] == '_') {
                i += 1; // scale suffix and unit: 1k, 10uF, 1meg
            }
        } else if ch.is_ascii_alphabetic() || ch == '_' {
            let s = i;
            while i < c.len() && (c[i].is_ascii_alphanumeric() || c[i] == '_') {
                i += 1;
            }
            let id: String = c[s..i].iter().collect::<String>().to_lowercase();
            let mut j = i;
            while j < c.len() && c[j].is_whitespace() {
                j += 1;
            }
            let next = c.get(j).copied();
            if next == Some('(') && (id == "v" || id == "i") {
                let mut depth = 0;
                i = j;
                while i < c.len() {
                    if c[i] == '(' {
                        depth += 1;
                    } else if c[i] == ')' {
                        depth -= 1;
                        if depth == 0 {
                            i += 1;
                            break;
                        }
                    }
                    i += 1;
                }
            } else if !(next == Some('=') && c.get(j + 1) != Some(&'=')) {
                out.push((id, next == Some('(')));
            }
        } else {
            i += 1;
        }
    }
    out
}

/// `.model` type word and level (lower case): level 1 when absent, 0 when it is not a literal number.
fn card_type(rest: &[&str]) -> (String, u32) {
    // `npn(is=1 level = 4)`, `npn level=4`: parens and '=' act as separators
    let rest = rest.join(" ").to_lowercase().replace(['(', ')', '='], " ");
    let w: Vec<&str> = rest.split_whitespace().collect();
    let level = match w.iter().position(|t| *t == "level") {
        None => 1,
        Some(i) => w.get(i + 1).and_then(|v| v.parse::<f64>().ok()).map_or(0, |v| v as u32),
    };
    (w.first().map_or(String::new(), |t| t.to_string()), level)
}

/// One scope: the top level (index 0) or a `.subckt` body.
#[derive(Debug, Clone, Default)]
struct Def {
    name: String,
    label: String, // lexical path: "outer/inner"
    parent: usize,
    ports: Vec<String>,
    params: Vec<(String, String)>,              // header params: name, default
    lines: Vec<String>,                         // element lines
    exprs: Vec<(String, String, Vec<String>)>,  // directive expressions: (owner, text, local names)
    names: HashSet<String>,                     // header params, .param and .func names (expressions)
    models: HashMap<String, (String, u32)>,     // local .model cards: type, level
    cards: HashMap<String, String>,             // local .model cards, normalised (DUP-MODEL)
    subckts: HashMap<String, usize>,            // local .subckt definitions
    text: String,                               // normalised definition text (DUP-SUBCKT)
    notes: Vec<Finding>,                        // raised while reading it
}

struct Deck {
    defs: Vec<Def>,
    globals: HashSet<String>,
    has_include: bool,
    analyses: Vec<String>,
    errors: Vec<Finding>, // structure: unbalanced .subckt/.ends
}

/// Scope chain of definition `i`, outermost first (`[0, outer, i]`).
fn chain(dk: &Deck, mut i: usize) -> Vec<usize> {
    let mut v = vec![i];
    while i != 0 {
        i = dk.defs[i].parent;
        v.push(i);
    }
    v.reverse();
    v
}

fn parse_deck(lines: &[String]) -> Deck {
    let mut dk = Deck { defs: vec![Def::default()], globals: HashSet::new(), has_include: false, analyses: vec![], errors: vec![] };
    let mut stack = vec![0usize];
    let mut dups: Vec<(usize, usize, usize)> = vec![]; // (scope, first, later) for one .subckt name
    for s in logical_lines(lines) {
        let tok: Vec<&str> = s.split_whitespace().collect();
        let cmd = tok[0].to_lowercase();
        let cur = *stack.last().unwrap();
        if cmd == ".subckt" {
            let name = tok.get(1).map_or(String::new(), |t| t.to_lowercase());
            let (ports, params) = args(after_tokens(&s, 2));
            let idx = dk.defs.len();
            let label = if cur == 0 { name.clone() } else { format!("{}/{}", dk.defs[cur].label, name) };
            let mut d = Def { name: name.clone(), label, parent: cur, ports, ..Default::default() };
            d.names = params.iter().map(|(k, _)| k.clone()).collect();
            d.exprs = params.iter().map(|(k, v)| (format!("default of {}", k), v.clone(), vec![])).collect();
            d.params = params;
            if name.is_empty() {
                dk.errors.push(finding("MALFORMED", "error", "'.subckt' needs a name".into(), vec![], vec![], "authoring"));
            }
            match dk.defs[cur].subckts.get(&name) {
                Some(&first) => dups.push((cur, first, idx)),
                None => {
                    dk.defs[cur].subckts.insert(name, idx);
                }
            }
            dk.defs.push(d);
            stack.push(idx);
        }
        let norm = if cmd == ".ends" { ".ends".to_string() } else { tok.join(" ").to_lowercase() };
        for &i in &stack[1..] {
            dk.defs[i].text.push_str(&norm);
            dk.defs[i].text.push('\n');
        }
        if cmd == ".subckt" {
            continue;
        }
        if cmd == ".ends" {
            if stack.len() > 1 {
                stack.pop();
            } else {
                dk.errors.push(finding("SUBCKT-UNBALANCED", "error", "'.ends' without a matching '.subckt'".into(), vec![], vec![], "authoring"));
            }
            continue;
        }
        if !s.starts_with('.') {
            dk.defs[cur].lines.push(s);
            continue;
        }
        let rest = after_tokens(&s, 1);
        let def = &mut dk.defs[cur];
        match cmd.as_str() {
            ".model" if tok.len() >= 2 => {
                let name = tok[1].to_lowercase();
                let card = tok[2..].join(" ").to_lowercase();
                match def.cards.get(&name) {
                    Some(c) if *c != card => def.notes.push(finding(
                        "DUP-MODEL",
                        "error",
                        format!(".model '{}' is defined twice with different parameters; ngspice silently keeps the first", name),
                        vec![],
                        vec![],
                        "authoring",
                    )),
                    Some(_) => {}
                    None => {
                        def.cards.insert(name.clone(), card);
                        def.models.insert(name.clone(), card_type(&tok[2..]));
                    }
                }
                def.exprs.extend(brace_groups(&s).into_iter().map(|e| (format!(".model {}", name), e, vec![])));
            }
            ".param" => {
                for (k, v) in args(rest).1 {
                    match k.find('(') {
                        Some(p) => {
                            // `.param f(x) = {..}`: a function
                            let locals = k[p + 1..].trim_end_matches(')').split(',').map(|a| a.trim().to_string()).collect();
                            def.names.insert(k[..p].trim().to_string());
                            def.exprs.push((format!(".param {}", k), v, locals));
                        }
                        None => {
                            def.names.insert(k.clone());
                            def.exprs.push((format!(".param {}", k), v, vec![]));
                        }
                    }
                }
            }
            ".func" => match (rest.find('('), rest.find(')')) {
                (Some(o), Some(c)) if o < c && o > 0 => {
                    let name = rest[..o].trim().to_lowercase();
                    let locals = rest[o + 1..c].split(',').map(|a| a.trim().to_lowercase()).collect();
                    def.names.insert(name.clone());
                    def.exprs.push((format!(".func {}", name), rest[c + 1..].trim().trim_start_matches('=').to_string(), locals));
                }
                _ => def.notes.push(finding("MALFORMED", "error", format!("'{}' must be .func name(args) {{expression}}", s), vec![], vec![], "authoring")),
            },
            ".global" => dk.globals.extend(tok[1..].iter().map(|t| t.to_lowercase())),
            ".include" | ".inc" | ".lib" => dk.has_include = true,
            ".tran" | ".op" | ".dc" | ".ac" | ".noise" | ".pz" if cur == 0 => dk.analyses.push(s.clone()),
            _ if cur != 0 => def.notes.push(limited(
                &def.name.clone(),
                format!("directive '{}' inside a .subckt body is passed to ngspice unanalysed", tok[0]),
            )),
            _ => {} // top-level control (.save, .options, .ic, ...): the deck's business, not the circuit's
        }
    }
    for &i in &stack[1..] {
        dk.errors.push(finding(
            "SUBCKT-UNBALANCED",
            "error",
            format!(".subckt '{}' has no '.ends' (ngspice aborts the circuit)", dk.defs[i].name),
            vec![],
            vec![],
            "authoring",
        ));
    }
    for (scope, first, later) in dups {
        if dk.defs[first].text != dk.defs[later].text {
            let msg = format!(
                ".subckt '{}' is defined twice with different bodies; ngspice keeps the first and ignores the other",
                dk.defs[first].name
            );
            dk.defs[scope].notes.push(finding("DUP-SUBCKT", "error", msg, vec![], vec![], "authoring"));
        }
    }
    dk
}

/// Lexical findings and parsed elements of one definition (computed once, when an instance first reaches it).
#[derive(Default)]
struct DefInfo {
    devices: Vec<Device>,                       // local names and nodes
    b_exprs: Vec<(String, Vec<String>, String, bool)>, // name, pnodes, expr, is_v
    exprs: Vec<(String, String, Vec<String>)>,  // every expression: (owner, text, local names)
    findings: Vec<Finding>,
}

fn analyse_def(dk: &Deck, idx: usize, has_tran: bool) -> DefInfo {
    let def = &dk.defs[idx];
    let (mut models, mut subckts) = (HashMap::new(), HashMap::new());
    for &c in &chain(dk, idx) {
        // outermost first, so a local definition shadows an enclosing or global one (probed)
        models.extend(dk.defs[c].models.iter().map(|(k, v)| (k.clone(), v.clone())));
        subckts.extend(dk.defs[c].subckts.iter().map(|(k, v)| (k.clone(), *v)));
    }
    let mut info = DefInfo { exprs: def.exprs.clone(), findings: def.notes.clone(), ..Default::default() };
    let bad = |code: &str, msg: String| finding(code, "error", msg, vec![], vec![], "authoring");
    let mut ports = HashSet::new();
    for p in &def.ports {
        if is_ground(p) {
            info.findings.push(bad("MALFORMED", format!("port '{}' is ngspice's global ground: the net an instance connects there is left unconnected; rename the port", p)));
        } else if !ports.insert(p.as_str()) {
            info.findings.push(bad("DUP-NAME", format!("port '{}' is listed twice; ngspice connects only the first", p)));
        }
    }
    let mut declared = HashSet::new();
    for (k, _) in &def.params {
        if !declared.insert(k.as_str()) {
            info.findings.push(bad("DUP-NAME", format!("parameter '{}' is declared twice; ngspice silently uses the last", k)));
        }
    }
    for (owner, e, _) in &def.exprs {
        if let Some(f) = pspice_only_fn(&e.split_whitespace().collect::<Vec<_>>(), None) {
            info.findings.push(bad("UNSUPPORTED", format!("{}: {}() is PSpice syntax, not an ngspice 47 function", owner, f)));
        }
    }
    let mut seen: HashMap<String, char> = HashMap::new();
    let mut refs: Vec<(String, char, Vec<String>)> = vec![];
    for s in &def.lines {
        element(s, dk, &models, &subckts, &mut seen, &mut refs, &mut info);
    }
    // K, F, H, W name other elements; ngspice resolves those names inside the same body only (probed)
    for (el, kind, names) in &refs {
        for r in names {
            if r.contains('.') {
                info.findings.push(limited(el, format!("{}: reference '{}' crosses a subcircuit boundary and is not checked", el, r)));
                continue;
            }
            let Some(&k) = seen.get(r) else {
                let here = if idx == 0 { "deck" } else { "body" };
                info.findings.push(bad("UNDEFINED-ELEMENT", format!("{}: no element '{}' in this {} (ngspice: fatal error)", el, r, here)));
                continue;
            };
            if *kind == 'K' && k != 'L' {
                let why = format!("{}: K couples inductors, '{}' is a {} (ngspice crashes on a resistor, ignores a source)", el, r, k);
                info.findings.push(bad("MALFORMED", why));
            } else if *kind != 'K' && !matches!(k, 'V' | 'E' | 'H' | 'B') {
                let why = format!("{}: the controlling element '{}' is a {}; it must be a V, E, H or B source (ngspice: unknown controlling source)", el, r, k);
                info.findings.push(bad("MALFORMED", why));
            }
        }
    }
    values(&mut info);
    let mut terms: HashSet<String> = info.devices.iter().flat_map(|d| d.nodes.iter().cloned()).collect();
    terms.extend(def.ports.iter().cloned());
    terms.extend(dk.globals.iter().cloned());
    let bf = b_checks(&info.b_exprs, &terms, has_tran);
    info.findings.extend(bf);
    for d in info.devices.iter().filter(|d| d.kind == 'X' && d.sub.is_none() && dk.has_include) {
        info.findings.push(finding(
            "OPAQUE-X",
            "warn",
            format!("{}: its .subckt is not in the deck (an .include may supply it); its body is not analysed", d.name),
            vec![],
            vec![d.name.clone()],
            "static",
        ));
    }
    if idx != 0 {
        for f in info.findings.iter_mut() {
            f.message = format!(".subckt {}: {}", def.label, f.message);
            f.scope = format!(".subckt {}", def.label);
        }
    }
    info
}

/// One element line of a definition (local names): arity from the device table, model and subckt references
/// through the scope chain, instance params, expressions, limited-analysis notes.
#[allow(clippy::too_many_arguments)]
fn element(
    s: &str,
    dk: &Deck,
    models: &HashMap<String, (String, u32)>,
    subckts: &HashMap<String, usize>,
    seen: &mut HashMap<String, char>,
    refs: &mut Vec<(String, char, Vec<String>)>,
    info: &mut DefInfo,
) {
    let has_include = dk.has_include;
    let mut errors: Vec<Finding> = vec![];
    let parts: Vec<&str> = s.split_whitespace().collect();
    let name = parts[0].to_string();
    let kind = name.chars().next().unwrap_or('?').to_ascii_uppercase();
    if seen.insert(name.to_lowercase(), kind).is_some() {
        errors.push(finding(
            "DUP-NAME",
            "error",
            format!("duplicate device name '{}'", name),
            vec![],
            vec![name.clone()],
            "static",
        ));
    }
    let lower_nodes = |ps: &[&str]| ps.iter().map(|p| p.to_lowercase()).collect::<Vec<_>>();
    let err = |code: &str, msg: String, nodes: &[String]| {
        finding(code, "error", msg, nodes.to_vec(), vec![name.clone()], "authoring")
    };
    let Some(spec) = device_spec(kind) else {
        info.findings.extend(errors);
        info.findings.push(if kind == 'N' {
            err("UNSUPPORTED", format!("{}: N (OSDI / Verilog-A) devices need compiled .osdi models, which this ngspice build does not have", name), &[])
        } else {
            finding("UNKNOWN-KIND", "error", format!("{}: unknown device kind '{}'", name, kind), vec![], vec![name.clone()], "static")
        });
        return;
    };
    let two = |ps: &[&str]| if ps.len() >= 3 { lower_nodes(&ps[1..3]) } else { vec![] };
    let form = parts.get(3).map(|t| t.to_uppercase()).unwrap_or_default();
    let assign = |kw: &str| form.starts_with(kw) && (form.contains('=') || parts.get(4).is_some_and(|n| n.starts_with('=')));
    let mut skip_fn_check = None;
    let (mut behav, mut sub, mut model): (Option<&str>, Option<usize>, Option<String>) = (None, None, None);
    let nodes: Vec<String> = match spec.tail {
        Tail::Value => {
            if parts.len() < 4 {
                errors.push(err("MALFORMED", format!("{}: {} needs 2 nodes + value", name, kind), &two(&parts)));
            }
            two(&parts)
        }
        Tail::Source | Tail::Behav => {
            if parts.len() < 3 {
                errors.push(err("MALFORMED", format!("{}: {} needs 2 nodes", name, kind), &two(&parts)));
            }
            if kind == 'B' {
                behav = Some("B");
                match extract_b_expr(s) {
                    Some((is_v, expr)) => info.b_exprs.push((name.clone(), two(&parts), expr, is_v)),
                    None => errors.push(finding(
                        "B-NOEXPR",
                        "error",
                        format!("{}: B-source missing V=/I= expression", name),
                        two(&parts),
                        vec![name.clone()],
                        "expression",
                    )),
                }
            }
            two(&parts)
        }
        Tail::Ctrl => {
            if parts.iter().any(|t| t.to_uppercase().starts_with("LAPLACE")) {
                errors.push(err(
                    "UNSUPPORTED",
                    format!("{}: LAPLACE is not supported by ngspice 47 (it aborts the engine); build the transfer function from R, L, C and linear sources", name),
                    &two(&parts),
                ));
                two(&parts)
            } else if assign("VALUE") || assign("VOL") || assign("CUR") {
                behav = Some("expression");
                two(&parts)
            } else if form == "TABLE" || form.starts_with("TABLE{") {
                behav = Some("TABLE");
                skip_fn_check = Some(3);
                two(&parts)
            } else if form.starts_with("POLY") {
                match poly(&parts, 3) {
                    Some((n, rest)) if rest.len() > 2 * n => {
                        let mut v = two(&parts);
                        v.extend(lower_nodes(&rest[..2 * n]));
                        v
                    }
                    _ => {
                        errors.push(err("MALFORMED", format!("{}: {} POLY(n) needs n control node pairs + coefficients", name, kind), &two(&parts)));
                        two(&parts)
                    }
                }
            } else if parts.len() < 6 {
                errors.push(err("MALFORMED", format!("{}: {} needs 4 nodes + gain", name, kind), &[]));
                if parts.len() >= 5 { lower_nodes(&parts[1..5]) } else { vec![] }
            } else {
                lower_nodes(&parts[1..5])
            }
        }
        Tail::Sense => {
            let ctl = if form.starts_with("POLY") {
                poly(&parts, 3).filter(|(n, rest)| rest.len() > *n).map(|(n, rest)| lower_nodes(&rest[..n]))
            } else {
                (parts.len() >= 5).then(|| vec![parts[3].to_lowercase()])
            };
            match ctl {
                Some(c) => refs.push((name.clone(), kind, c)),
                None => errors.push(err("MALFORMED", format!("{}: {} needs 2 nodes + a controlling V source name + gain", name, kind), &two(&parts))),
            }
            two(&parts)
        }
        Tail::SenseModel => {
            if parts.len() < 5 {
                errors.push(err("MALFORMED", format!("{}: {} needs 2 nodes + a controlling V source name + model", name, kind), &two(&parts)));
            } else {
                refs.push((name.clone(), kind, vec![parts[3].to_lowercase()]));
                model = Some(parts[4].to_lowercase());
                errors.extend(check_model(&name, spec, &parts[4].to_lowercase(), 2, models, has_include, &two(&parts), s));
            }
            two(&parts)
        }
        Tail::Couple => {
            if parts.len() < 4 {
                errors.push(err("MALFORMED", format!("{}: K needs two inductor names + coupling factor", name), &[]));
            } else {
                refs.push((name.clone(), kind, lower_nodes(&parts[1..3])));
            }
            vec![]
        }
        Tail::Params => {
            let pos = positional(&parts);
            let n = pos.len().saturating_sub(1);
            if n != spec.min_nodes {
                errors.push(err("MALFORMED", format!("{}: {} needs {} nodes then key=value params (got {} positional tokens)", name, kind, spec.min_nodes, n), &[]));
                vec![]
            } else {
                pos[1..].to_vec()
            }
        }
        Tail::Model => {
            let pos = positional(&parts);
            // ngspice reads the required nodes, then takes the first token that names a
            // model; tokens before it are optional nodes (Q: substrate, thermal; M: SOI).
            let typical = if kind == 'M' { 4 } else { spec.min_nodes };
            let n = (spec.min_nodes..=spec.max_nodes)
                .take_while(|k| k + 1 < pos.len())
                .find(|&k| models.contains_key(&pos[k + 1]))
                .unwrap_or(typical);
            if pos.len() < n + 2 {
                // only a short line is malformed: node and model names are separate namespaces
                let msg = match pos.iter().skip(1).find(|t| models.contains_key(*t)) {
                    Some(m) => format!("{}: model name '{}' in a node position; {} needs {} nodes + model", name, m, kind, n),
                    None => format!("{}: {} needs {} nodes + model", name, kind, n),
                };
                errors.push(err("MALFORMED", msg, &[]));
                vec![]
            } else {
                model = Some(pos[n + 1].clone());
                let found = check_model(&name, spec, &pos[n + 1], n, models, has_include, &pos[1..=n], s);
                let fatal = !found.is_empty();
                errors.extend(found);
                if fatal { vec![] } else { pos[1..=n].to_vec() }
            }
        }
        Tail::Ports => {
            if parts.len() < 3 {
                errors.push(err("MALFORMED", format!("{}: A needs ports + model", name), &[]));
                vec![]
            } else {
                errors.extend(check_model(&name, spec, &parts[parts.len() - 1].to_lowercase(), 0, models, has_include, &[], s));
                xspice_nodes(&parts[1..parts.len() - 1])
            }
        }
        Tail::Subckt => {
            let (pos, kv) = args(after_tokens(s, 1));
            if pos.len() < 2 {
                errors.push(err("MALFORMED", format!("{}: X needs nodes + subcircuit name", name), &[]));
                vec![]
            } else {
                let n = pos.len() - 1;
                match subckts.get(&pos[n]).map(|&t| (t, &dk.defs[t])) {
                    Some((_, d)) if d.ports.len() != n => {
                        errors.push(err(
                            "MALFORMED",
                            format!("{}: subcircuit '{}' has {} ports, instance gives {} nodes", name, pos[n], d.ports.len(), n),
                            &[],
                        ));
                        vec![]
                    }
                    Some((t, d)) => {
                        for (k, _) in kv.iter().filter(|(k, _)| !d.params.iter().any(|(p, _)| p == k)) {
                            let have: Vec<&str> = d.params.iter().map(|(p, _)| p.as_str()).collect();
                            errors.push(err(
                                "UNKNOWN-INSTANCE-PARAM",
                                format!(
                                    "{}: .subckt {} declares no parameter '{}' (it has: {}); ngspice would ignore it silently",
                                    name, pos[n], k, if have.is_empty() { "none".to_string() } else { have.join(", ") }
                                ),
                                &[],
                            ));
                        }
                        sub = Some(t);
                        pos[..n].to_vec()
                    }
                    None => {
                        if !has_include {
                            errors.push(err(
                                "UNDEFINED-SUBCKT",
                                format!("{}: no .subckt '{}' is visible here (ngspice: 'unknown subckt'); a .subckt nested in another body is local to it", name, pos[n]),
                                &pos[..n],
                            ));
                        }
                        pos[..n].to_vec()
                    }
                }
            }
        }
    };
    if let Some(f) = pspice_only_fn(&parts, skip_fn_check) {
        errors.push(err(
            "UNSUPPORTED",
            format!("{}: {}() is PSpice syntax; ngspice 47 aborts or computes something else (use the ternary a ? b : c, min/max, pwl)", name, f),
            &nodes,
        ));
    }
    if !spec.covered {
        let what = match kind {
            'A' => "XSPICE code-model device: its port directions, digital/analog domains and model parameters are checked only by ngspice",
            _ => "transmission line: delays, losses and coupling are not checked",
        };
        errors.push(limited(&name, format!("{}: {}; DC paths are assumed across its ports", name, what)));
    }
    if let Some(b) = behav {
        errors.push(limited(&name, format!("{}: behavioural {} not analysed: its value, domain and DC behaviour are not checked", name, b)));
    }
    if model.as_ref().and_then(|m| models.get(m)).is_some_and(|(_, lv)| *lv == 0) {
        errors.push(limited(&name, format!("{}: model '{}' has a level that is not a literal number; level and node-count checks skipped", name, model.unwrap())));
    }
    let exprs = if kind == 'B' {
        extract_b_expr(s).map(|(_, e)| vec![e]).unwrap_or_default()
    } else if behav == Some("expression") {
        s.find('=').map(|i| vec![s[i + 1..].to_string()]).unwrap_or_default()
    } else {
        brace_groups(s)
    };
    info.exprs.extend(exprs.into_iter().map(|e| (name.clone(), e, vec![])));
    info.findings.extend(errors);
    let dc = dc_rule(kind, s, &nodes);
    info.devices.push(Device { name, nodes, raw: s.to_string(), kind, dc, sub });
}

/// Value checks of a definition's R, C, L (local; expression values are resolved per instance instead).
fn values(info: &mut DefInfo) {
    for d in &info.devices {
        let k = d.kind;
        if !(k == 'R' || k == 'C' || k == 'L') {
            continue;
        }
        let parts: Vec<&str> = d.raw.split_whitespace().collect();
        let Some(tok) = parts.get(3).filter(|t| !t.starts_with('{') && !t.starts_with('\'')) else {
            continue;
        };
        let f = |code: &str, sev: &str, msg: String, layer: &str| finding(code, sev, msg, d.nodes.clone(), vec![d.name.clone()], layer);
        info.findings.push(match parse_value(tok) {
            None => f("BAD-VALUE", "warn", format!("{}: unparseable value '{}'", d.name, tok), "authoring"),
            Some(v) if k == 'R' && v < 0.0 => f("NEG-R", "error", format!("{}: negative resistance {} (non-physical; silent-risk)", d.name, tok), "authoring"),
            Some(v) if v < 0.0 => f("NEG-CL", "error", format!("{}: negative {} value {}", d.name, k, tok), "authoring"),
            Some(v) if k == 'R' && v == 0.0 => f("ZERO-R", "warn", format!("{}: zero resistance (ideal short; V-loop risk)", d.name), "static"),
            Some(v) if v == 0.0 => f("ZERO-CL", "error", format!("{}: zero {} value (degenerate)", d.name, k), "authoring"),
            Some(_) => continue,
        });
    }
}

fn extract_b_expr(s: &str) -> Option<(bool, String)> {
    // find V= or I= assignment (case-insensitive, word boundary-ish)
    let chars: Vec<char> = s.chars().collect();
    let mut idx = 0;
    while idx < chars.len() {
        let c = chars[idx];
        if c == 'V' || c == 'v' || c == 'I' || c == 'i' {
            let prev_ok = if idx == 0 {
                true
            } else {
                let p = chars[idx - 1];
                !(p.is_ascii_alphanumeric() || p == '_')
            };
            if prev_ok {
                let mut j = idx + 1;
                while j < chars.len() && chars[j].is_whitespace() {
                    j += 1;
                }
                if j < chars.len() && chars[j] == '=' {
                    let is_v = c == 'V' || c == 'v';
                    let expr: String = chars[j + 1..].iter().collect();
                    return Some((is_v, expr.trim().to_string()));
                }
            }
        }
        idx += 1;
    }
    None
}

/// V(node) and V(node, node) references in an expression (lower case; dotted names are flattened nodes).
fn v_refs(expr: &str) -> Vec<String> {
    let chars: Vec<char> = expr.chars().collect();
    let mut out = vec![];
    let node = |j: &mut usize, out: &mut Vec<String>| {
        while *j < chars.len() && chars[*j].is_whitespace() {
            *j += 1;
        }
        let start = *j;
        if *j < chars.len() && (chars[*j].is_ascii_alphabetic() || chars[*j] == '_') {
            while *j < chars.len() && (chars[*j].is_ascii_alphanumeric() || chars[*j] == '_' || chars[*j] == '.') {
                *j += 1;
            }
            out.push(chars[start..*j].iter().collect::<String>().to_lowercase());
        }
    };
    for i in 0..chars.len() {
        if (chars[i] == 'V' || chars[i] == 'v') && (i == 0 || !(chars[i - 1].is_ascii_alphanumeric() || chars[i - 1] == '_')) {
            let mut j = i + 1;
            while j < chars.len() && chars[j].is_whitespace() {
                j += 1;
            }
            if j < chars.len() && chars[j] == '(' {
                j += 1;
                node(&mut j, &mut out);
                while j < chars.len() && chars[j].is_whitespace() {
                    j += 1;
                }
                if j < chars.len() && chars[j] == ',' {
                    j += 1;
                    node(&mut j, &mut out);
                }
            }
        }
    }
    out
}

/// The flattened circuit: every element an instance reaches, nodes mapped to deck-global names.
struct Flat<'a> {
    dk: &'a Deck,
    has_tran: bool,
    infos: Vec<Option<DefInfo>>,
    devices: Vec<Device>,
    findings: Vec<Finding>,
    reported: HashSet<(usize, String, String)>,
    capped: bool,
}

impl Flat<'_> {
    fn node(&self, n: &str, path: &str, ports: &HashMap<String, String>) -> String {
        if is_ground(n) {
            GROUND.to_string()
        } else if let Some(m) = ports.get(n) {
            m.clone()
        } else if path.is_empty() || self.dk.globals.contains(n) {
            n.to_string()
        } else {
            format!("{}.{}", path, n)
        }
    }

    /// Expand instance `path` of definition `idx`. `chain`: the definitions on the instance chain, `idx` last;
    /// their params, `.param`s and `.func`s are what its expressions may read (ngspice scopes them dynamically).
    fn expand(&mut self, idx: usize, path: &str, ports: &HashMap<String, String>, chain: &mut Vec<usize>) {
        let dk = self.dk;
        if self.infos[idx].is_none() {
            let info = analyse_def(dk, idx, self.has_tran);
            self.findings.extend(info.findings.iter().cloned());
            self.infos[idx] = Some(info);
        }
        let (exprs, devices) = {
            let i = self.infos[idx].as_ref().unwrap();
            (i.exprs.clone(), i.devices.clone())
        };
        let visible: HashSet<&str> = chain.iter().flat_map(|&c| dk.defs[c].names.iter().map(|s| s.as_str())).collect();
        for (owner, e, locals) in &exprs {
            for (id, call) in expr_idents(e) {
                let known = visible.contains(id.as_str())
                    || locals.contains(&id)
                    || if call { FUNCS.contains(&id.as_str()) } else { CONSTS.contains(&id.as_str()) };
                if known || !self.reported.insert((idx, owner.clone(), id.clone())) {
                    continue;
                }
                let at = if idx == 0 { String::new() } else { format!("instance {} of .subckt {}: ", path, dk.defs[idx].label) };
                let msg = if call {
                    format!("{}{}: '{}()' is neither an ngspice function nor a .func visible here (ngspice aborts the circuit)", at, owner, id)
                } else {
                    format!(
                        "{}{}: '{}' is not a parameter visible here: no .param, and no parameter of this instance's .subckt or of an enclosing one (ngspice: 'Undefined parameter', which aborts the circuit)",
                        at, owner, id
                    )
                };
                let mut f = finding("UNDEFINED-PARAM", "error", msg, vec![], vec![owner.clone()], "authoring");
                f.scope = path.to_string();
                self.findings.push(f);
            }
        }
        for d in devices {
            let nodes: Vec<String> = d.nodes.iter().map(|n| self.node(n, path, ports)).collect();
            let name = if path.is_empty() {
                d.name.clone()
            } else {
                format!("{}.{}.{}", d.kind.to_ascii_lowercase(), path, d.name.to_lowercase())
            };
            if let Some(t) = d.sub {
                if chain.contains(&t) {
                    if self.reported.insert((t, "recursion".to_string(), String::new())) {
                        let via: Vec<&str> = chain.iter().skip(1).map(|&c| dk.defs[c].label.as_str()).collect();
                        let msg = format!(
                            "{}: .subckt {} instantiates itself (through {}); ngspice expands it until it gives up with 'unknown subckt'",
                            name, dk.defs[t].label, via.join(" -> ")
                        );
                        let mut f = finding("SUBCKT-RECURSION", "error", msg, vec![], vec![name.clone()], "authoring");
                        f.scope = path.to_string();
                        self.findings.push(f);
                    }
                } else if self.devices.len() >= MAX_FLAT {
                    if !self.capped {
                        self.capped = true;
                        let msg = format!("{}: flattening stopped at {} elements; deeper instances are not analysed", name, MAX_FLAT);
                        self.findings.push(limited(&name, msg));
                    }
                } else {
                    let p = if path.is_empty() { d.name.to_lowercase() } else { format!("{}.{}", path, d.name.to_lowercase()) };
                    let map: HashMap<String, String> = dk.defs[t].ports.iter().cloned().zip(nodes).collect();
                    chain.push(t);
                    self.expand(t, &p, &map, chain);
                    chain.pop();
                    continue;
                }
            }
            self.devices.push(Device { name, nodes, ..d }); // unexpanded X: all-pairs DC, never a false NO-DC-PATH
        }
    }
}

/// Instance path a connectivity finding belongs to: its first node's prefix (`x1.xa.n2` -> `x1.xa`), or for
/// device-only findings the device's (`r.x1.r1` -> `x1`); "" at top level.
fn scope_of(f: &Finding) -> String {
    match f.nodes.first() {
        Some(n) => n.rfind('.').map_or(String::new(), |i| n[..i].to_string()),
        None => f
            .devices
            .iter()
            .find_map(|d| d.split_once('.').and_then(|(_, rest)| rest.rfind('.').map(|i| rest[..i].to_string())))
            .unwrap_or_default(),
    }
}

/// Connectivity of the flattened circuit: the pre-subcircuit checks, now over every instance's elements.
fn topology(devices: &[Device]) -> Vec<Finding> {
    let mut out = vec![];
    let mut nodes: HashSet<String> = HashSet::new();
    for d in devices {
        for n in &d.nodes {
            nodes.insert(n.clone());
        }
    }
    let has_ground = nodes.contains(GROUND);
    if !has_ground && !nodes.is_empty() {
        let mut ns: Vec<String> = nodes.iter().cloned().collect();
        ns.sort();
        out.push(finding(
            "NO-GROUND",
            "error",
            "no '0' ground node present; every node is floating".to_string(),
            ns,
            vec![],
            "static",
        ));
    }

    // per-device degenerate checks that depend on the (mapped) nodes
    for d in devices {
        let k = d.kind;
        if (k == 'V' || k == 'E' || k == 'B') && d.nodes.len() >= 2 && d.nodes[0] == d.nodes[1] && (k != 'B' || b_is_voltage(&d.raw)) {
            out.push(finding(
                "SHORTED-VSRC",
                "error",
                format!("{}: voltage source shorted (same node both terminals)", d.name),
                vec![d.nodes[0].clone()],
                vec![d.name.clone()],
                "static",
            ));
        }
        if (k == 'R' || k == 'C' || k == 'L') && d.nodes.len() >= 2 && d.nodes[0] == d.nodes[1] {
            out.push(finding(
                "SHORTED-PASSIVE",
                "warn",
                format!("{}: {} shorted (both terminals on '{}')", d.name, k, d.nodes[0]),
                vec![d.nodes[0].clone()],
                vec![d.name.clone()],
                "authoring",
            ));
        }
    }

    // incidence / dangling
    let mut incidence: HashMap<String, usize> = HashMap::new();
    for d in devices {
        for n in &d.nodes {
            *incidence.entry(n.clone()).or_insert(0) += 1;
        }
    }
    let mut inc_keys: Vec<String> = incidence.keys().cloned().collect();
    inc_keys.sort();
    for n in inc_keys {
        let c = incidence[&n];
        if n == GROUND {
            continue;
        }
        if c < 2 {
            let devs: Vec<String> = devices
                .iter()
                .filter(|d| d.nodes.contains(&n))
                .map(|d| d.name.clone())
                .collect();
            out.push(finding(
                "DANGLING",
                "warn",
                format!("node '{}' has only {} terminal connection(s) (open end?)", n, c),
                vec![n],
                devs,
                "static",
            ));
        }
    }

    // DC path BFS
    let mut adj: HashMap<String, Vec<String>> = HashMap::new();
    for d in devices {
        for (i, j) in dc_edges(d) {
            let a = d.nodes[i].clone();
            let b = d.nodes[j].clone();
            adj.entry(a.clone()).or_default().push(b.clone());
            adj.entry(b).or_default().push(a);
        }
    }
    let mut reached: HashSet<String> = HashSet::new();
    let mut stack: Vec<String> = vec![];
    if has_ground {
        reached.insert(GROUND.to_string());
        stack.push(GROUND.to_string());
    }
    while let Some(n) = stack.pop() {
        for m in adj.get(&n).into_iter().flatten() {
            if reached.insert(m.clone()) {
                stack.push(m.clone());
            }
        }
    }
    let mut sorted_nodes: Vec<String> = nodes.iter().cloned().collect();
    sorted_nodes.sort();
    for n in &sorted_nodes {
        if n == GROUND {
            continue;
        }
        if !reached.contains(n) {
            let devs: Vec<String> = devices
                .iter()
                .filter(|d| d.nodes.contains(n))
                .map(|d| d.name.clone())
                .collect();
            out.push(finding(
                "NO-DC-PATH",
                "error",
                format!(
                    "node '{}' has no DC path to ground (capacitors/current-sources/MOSFET gates are open at DC)",
                    n
                ),
                vec![n.clone()],
                devs,
                "static",
            ));
        }
    }

    // ISRC-OPEN subset
    for n in &sorted_nodes {
        if n == GROUND || reached.contains(n) {
            continue;
        }
        let incident: Vec<&Device> = devices.iter().filter(|d| d.nodes.contains(n)).collect();
        if !incident.is_empty()
            && incident.iter().all(|d| {
                d.kind == 'I' || d.kind == 'G' || d.kind == 'F' || (d.kind == 'B' && contains_b_current(&d.raw.to_uppercase()))
            })
        {
            let names: Vec<String> = incident.iter().map(|d| d.name.clone()).collect();
            out.push(finding(
                "ISRC-OPEN",
                "error",
                format!(
                    "node '{}' connects ONLY to current-source terminals ({}): ngspice returns V=I*1ohm SILENTLY (verified). Refuse before sim.",
                    n,
                    names.join(", ")
                ),
                vec![n.clone()],
                names,
                "static",
            ));
        }
    }

    // V-LOOP union-find
    let mut parent: HashMap<String, String> = HashMap::new();
    for n in nodes.iter() {
        parent.insert(n.clone(), n.clone());
    }
    fn find(parent: &mut HashMap<String, String>, x: &str) -> String {
        let mut r = x.to_string();
        while parent.get(&r).map(|s| s.as_str()) != Some(r.as_str()) {
            let p = parent[&r].clone();
            let gp = parent.get(&p).cloned().unwrap_or(p.clone());
            parent.insert(r.clone(), gp.clone());
            r = gp;
        }
        r
    }
    for d in devices {
        if short_edge(d.kind, d) && d.nodes.len() >= 2 {
            let a = d.nodes[0].clone();
            let b = d.nodes[1].clone();
            if a == b {
                continue;
            }
            let ra = find(&mut parent, &a);
            let rb = find(&mut parent, &b);
            if ra == rb {
                out.push(finding(
                    "V-LOOP",
                    "error",
                    format!(
                        "voltage-source/short loop via {} ('{}' already short-connected to '{}')",
                        d.name, a, b
                    ),
                    vec![a, b],
                    vec![d.name.clone()],
                    "static",
                ));
            } else {
                parent.insert(rb, ra);
            }
        }
    }
    for f in out.iter_mut() {
        f.scope = scope_of(f);
    }
    out
}

/// B-source checks of one definition (local names). `terms`: the nodes some element or port of it uses.
fn b_checks(b_exprs: &[(String, Vec<String>, String, bool)], terms: &HashSet<String>, has_tran: bool) -> Vec<Finding> {
    let mut out = vec![];
    for (bname, pnodes, expr, is_v) in b_exprs {
        if *is_v {
            for pn in pnodes {
                let refs = v_refs(expr);
                if refs.contains(pn) {
                    out.push(finding(
                        "B-SELFREF",
                        "error",
                        format!(
                            "{}: expression reads its own output node '{}' (algebraic loop; verified no-solution class)",
                            bname, pn
                        ),
                        vec![pn.clone()],
                        vec![bname.clone()],
                        "expression",
                    ));
                }
            }
        }
        for r in v_refs(expr) {
            if !terms.contains(&r) && !is_ground(&r) && !r.contains('.') {
                out.push(finding(
                    "B-IMPLICIT-NODE",
                    "error",
                    format!(
                        "{}: V({}) references node '{}' with no device terminal (implicit floating node)",
                        bname, r, r
                    ),
                    vec![r],
                    vec![bname.clone()],
                    "expression",
                ));
            }
        }
        if expr.to_lowercase().contains("ddt") {
            out.push(finding(
                "B-DDT",
                "warn",
                format!(
                    "{}: ddt() of a discontinuity yields a pure timestep artifact (peak=V/dt); never read as physics",
                    bname
                ),
                pnodes.clone(),
                vec![bname.clone()],
                "expression",
            ));
        }
        // literal /0 (not /0.x)
        if has_literal_divzero(expr) {
            out.push(finding(
                "B-DIVZERO",
                "error",
                format!(
                    "{}: literal division by zero (ngspice silently yields ~1e32)",
                    bname
                ),
                pnodes.clone(),
                vec![bname.clone()],
                "expression",
            ));
        }
        if expr.to_uppercase().contains("TIME") && !has_tran {
            out.push(finding(
                "B-TIME-IN-OP",
                "warn",
                format!(
                    "{}: TIME-dependent expression without .tran (TIME is silently 0 in .op)",
                    bname
                ),
                pnodes.clone(),
                vec![bname.clone()],
                "expression",
            ));
        }
    }

    // B-CYCLE (mutual reference warn)
    let mut drivers: HashMap<String, Vec<String>> = HashMap::new();
    for (bname, pnodes, _e, _v) in b_exprs {
        for pn in pnodes {
            drivers.entry(pn.clone()).or_default().push(bname.clone());
        }
    }
    let mut breads: HashMap<String, HashSet<String>> = HashMap::new();
    for (bname, _pn, expr, _v) in b_exprs {
        let mut reads = HashSet::new();
        for r in v_refs(expr) {
            if let Some(drvs) = drivers.get(&r) {
                for drv in drvs {
                    if drv != bname {
                        reads.insert(drv.clone());
                    }
                }
            }
        }
        breads.insert(bname.clone(), reads);
    }
    // DFS cycle detection
    #[derive(PartialEq, Clone, Copy)]
    enum Color {
        White,
        Gray,
        Black,
    }
    let mut color: HashMap<String, Color> = HashMap::new();
    for k in breads.keys() {
        color.insert(k.clone(), Color::White);
    }
    let mut reported: HashSet<Vec<String>> = HashSet::new();
    fn dfs(
        u: &str,
        breads: &HashMap<String, HashSet<String>>,
        color: &mut HashMap<String, Color>,
        stack: &mut Vec<String>,
        out: &mut Vec<Finding>,
        reported: &mut HashSet<Vec<String>>,
    ) {
        color.insert(u.to_string(), Color::Gray);
        stack.push(u.to_string());
        if let Some(vs) = breads.get(u) {
            let mut vs: Vec<&String> = vs.iter().collect();
            vs.sort();
            for w in vs {
                match color.get(w.as_str()).cloned().unwrap_or(Color::Black) {
                    Color::Gray => {
                        let pos = stack.iter().position(|x| x == w).unwrap_or(0);
                        let mut cyc: Vec<String> = stack[pos..].to_vec();
                        cyc.push(w.clone());
                        let mut key = cyc.clone();
                        key.sort();
                        key.dedup();
                        if reported.insert(key.clone()) {
                            out.push(finding(
                                "B-CYCLE",
                                "warn",
                                format!(
                                    "behavioral loop {}: multistable/convergence class; needs .ic + state elements and intent review (runtime scan is backstop)",
                                    cyc.join(" -> ")
                                ),
                                vec![],
                                key,
                                "expression",
                            ));
                        }
                    }
                    Color::White => {
                        dfs(w, breads, color, stack, out, reported);
                    }
                    Color::Black => {}
                }
            }
        }
        stack.pop();
        color.insert(u.to_string(), Color::Black);
    }
    let mut keys: Vec<String> = breads.keys().cloned().collect();
    keys.sort();
    for k in keys {
        if color.get(&k).cloned().unwrap_or(Color::Black) == Color::White {
            let mut stack = vec![];
            dfs(&k, &breads, &mut color, &mut stack, &mut out, &mut reported);
        }
    }
    out
}

/// Run all static checks. Returns findings (errors + warns).
pub fn validate(lines: &[String]) -> Vec<Finding> {
    let dk = parse_deck(lines);
    let has_tran = dk.analyses.iter().any(|a| a.trim().to_lowercase().starts_with(".tran"));
    let mut fl = Flat {
        dk: &dk,
        has_tran,
        infos: (0..dk.defs.len()).map(|_| None).collect(),
        devices: vec![],
        findings: vec![],
        reported: HashSet::new(),
        capped: false,
    };
    fl.expand(0, "", &HashMap::new(), &mut vec![0]);
    let mut out = dk.errors.clone();
    out.extend(fl.findings);
    out.extend(topology(&fl.devices));
    out
}

fn contains_b_current(up: &str) -> bool {
    // detect "I =" assignment in uppercased raw
    let chars: Vec<char> = up.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == 'I' {
            let prev_ok = if i == 0 {
                true
            } else {
                let pc = chars[i - 1];
                !(pc.is_ascii_alphanumeric() || pc == '_')
            };
            if prev_ok {
                let mut j = i + 1;
                while j < chars.len() && chars[j].is_whitespace() {
                    j += 1;
                }
                if j < chars.len() && chars[j] == '=' {
                    // exclude "TIME" etc.: require that this I is the source type, i.e.
                    // preceded by B-name pattern "Bxxx p n I=" -- approximate: the char before
                    // whitespace run... keep simple: accept.
                    return true;
                }
            }
        }
        i += 1;
    }
    false
}

fn has_literal_divzero(expr: &str) -> bool {
    let chars: Vec<char> = expr.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '/' {
            let mut j = i + 1;
            while j < chars.len() && chars[j].is_whitespace() {
                j += 1;
            }
            if j < chars.len() && chars[j] == '0' {
                let k = j + 1;
                if k >= chars.len() || !(chars[k].is_ascii_digit() || chars[k] == '.') {
                    return true;
                }
            }
        }
        i += 1;
    }
    false
}

pub fn has_error(findings: &[Finding]) -> bool {
    findings.iter().any(|f| f.severity == "error")
}

/// B-source expression strings in a deck (for TMAX policy).
/// Top level only: bodies' TIME terms carry params the policy cannot bound (DESIGN L14, unchanged).
pub fn b_exprs(lines: &[String]) -> Vec<String> {
    analyse_def(&parse_deck(lines), 0, true).b_exprs.into_iter().map(|(_, _, e, _)| e).collect()
}

pub fn error_codes(findings: &[Finding]) -> Vec<String> {
    let mut v: Vec<String> = findings
        .iter()
        .filter(|f| f.severity == "error")
        .map(|f| f.code.clone())
        .collect();
    v.sort();
    v.dedup();
    v
}

#[allow(dead_code)]
fn _assert_ident(s: &str) -> bool {
    is_ident(s)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_rows_are_unique_and_levels_name_table_types() {
        let mut seen = HashSet::new();
        for d in DEVICES {
            assert!(seen.insert(d.letter), "duplicate row {}", d.letter);
            assert!(d.min_nodes <= d.max_nodes, "{}", d.letter);
            assert_eq!(d.tail == Tail::Model, !d.models.is_empty() && d.letter != 'W', "{}", d.letter);
        }
        assert!(device_spec('n').is_none() && device_spec('q').is_some());
        for (ty, lv) in LEVELS {
            assert!(DEVICES.iter().any(|d| d.models.contains(ty)), "{} not used by any row", ty);
            assert!(!lv.is_empty());
        }
        assert!(!LEVELS.iter().any(|(t, l)| (*t == "npn" && l.contains(&6)) || (*t == "nmos" && l.contains(&60))));
    }

    #[test]
    fn poly_spec_forms() {
        let p: Vec<&str> = "E1 o 0 POLY(2) a 0 b 0 0 1 1".split_whitespace().collect();
        assert_eq!(poly(&p, 3).map(|(n, r)| (n, r.len())), Some((2, 7)));
        let p: Vec<&str> = "E1 o 0 POLY (1) a 0 0 3".split_whitespace().collect();
        assert_eq!(poly(&p, 3).map(|(n, r)| (n, r.len())), Some((1, 4)));
        let p: Vec<&str> = "E1 o 0 POLY(x) a 0".split_whitespace().collect();
        assert!(poly(&p, 3).is_none());
        let p: Vec<&str> = "E1 o 0 POLY(0) a 0".split_whitespace().collect();
        assert!(poly(&p, 3).is_none());
    }

    #[test]
    fn xspice_port_tokens() {
        assert_eq!(xspice_nodes(&["[a", "b]", "~y"]), vec!["a", "b", "y"]);
        assert_eq!(xspice_nodes(&["[", "a", "]", "NULL"]), vec!["a"]);
        assert_eq!(xspice_nodes(&["%vd(a", "0)", "%v", "out"]), vec!["a", "0", "out"]);
        assert_eq!(xspice_nodes(&["%vd", "(in", "inb)"]), vec!["in", "inb"]);
    }

    #[test]
    fn pspice_function_detection() {
        let t = |s: &str, skip| pspice_only_fn(&s.split_whitespace().collect::<Vec<_>>(), skip);
        assert_eq!(t("B1 o 0 V=if(V(a)>1,1,0)", None).as_deref(), Some("if"));
        assert_eq!(t("B1 o 0 V=LIMIT (V(a),0,1)", None).as_deref(), Some("limit"));
        assert_eq!(t("R1 a b R={table(V(a),0,1)}", None).as_deref(), Some("table"));
        assert_eq!(t("E1 o 0 TABLE {V(a)} = (0,0) (1,1)", Some(3)), None);
        // identifiers that merely contain the names, and nodes named like them, are fine
        assert_eq!(t("B1 o 0 V=vif(1)+mylimit(2)+table_x", None), None);
        assert_eq!(t("R1 if limit 1k", None), None);
    }

    // ---- subcircuit validation (task 4): one test per diagnostic class, then the false-positive guards ----

    fn v(deck: &[&str]) -> Vec<Finding> {
        let mut lines = vec!["t".to_string()];
        lines.extend(deck.iter().map(|s| s.to_string()));
        validate(&lines)
    }

    /// (code, scope) of every error, and of every finding when `all`.
    fn got(f: &[Finding], all: bool) -> Vec<(String, String)> {
        f.iter().filter(|x| all || x.severity == "error").map(|x| (x.code.clone(), x.scope.clone())).collect()
    }

    fn one(f: &[Finding], code: &str, scope: &str) -> bool {
        got(f, true).contains(&(code.to_string(), scope.to_string()))
    }

    const DRIVE: [&str; 2] = ["V1 a 0 1", "R0 a b 1k"];

    fn with<'a>(extra: &[&'a str]) -> Vec<&'a str> {
        let mut d: Vec<&str> = DRIVE.to_vec();
        d.extend(extra);
        d
    }

    #[test]
    fn class_of_keeps_the_five_classes_apart() {
        for (code, class) in [
            ("MALFORMED", "syntax"), ("DUP-NAME", "syntax"), ("SUBCKT-UNBALANCED", "syntax"),
            ("UNDEFINED-MODEL", "dependency"), ("UNDEFINED-SUBCKT", "dependency"), ("UNDEFINED-PARAM", "dependency"),
            ("UNDEFINED-ELEMENT", "dependency"), ("UNKNOWN-INSTANCE-PARAM", "dependency"), ("DUP-SUBCKT", "dependency"),
            ("DUP-MODEL", "dependency"), ("SUBCKT-RECURSION", "dependency"),
            ("NO-GROUND", "connectivity"), ("NO-DC-PATH", "connectivity"), ("ISRC-OPEN", "connectivity"),
            ("V-LOOP", "connectivity"), ("DANGLING", "connectivity"), ("SHORTED-VSRC", "connectivity"),
            ("SHORTED-PASSIVE", "connectivity"), ("UNKNOWN-KIND", "unsupported"), ("UNSUPPORTED", "unsupported"),
            ("LIMITED-ANALYSIS", "limited"), ("OPAQUE-X", "limited"), ("NEG-R", "value"), ("B-SELFREF", "value"),
        ] {
            assert_eq!(class_of(code), class, "{}", code);
        }
    }

    #[test]
    fn syntax_class_in_bodies() {
        let f = v(&with(&["X1 b s", ".subckt s p", "R1 p 0 1k"]));
        assert!(one(&f, "SUBCKT-UNBALANCED", ""), "{:?}", f);
        assert!(one(&v(&with(&["R1 b 0 1k", ".ends"])), "SUBCKT-UNBALANCED", ""));
        assert!(one(&v(&with(&["R1 b 0 1k", ".subckt", ".ends"])), "MALFORMED", ""));
        // a malformed element inside a body is reported once, against the definition
        let f = v(&with(&["X1 b s", "X2 b s", ".subckt s p", "R1 p", "R2 p 0 1k", ".ends"]));
        assert_eq!(got(&f, false), vec![("MALFORMED".to_string(), ".subckt s".to_string())], "{:?}", f);
        // duplicate element names: an error within one body, fine across two bodies
        let f = v(&with(&["X1 b s", ".subckt s p", "R1 p 0 1k", "R1 p 0 2k", ".ends"]));
        assert!(one(&f, "DUP-NAME", ".subckt s"), "{:?}", f);
        let f = v(&with(&["X1 b s", "X2 b t", "R1 b 0 1k", ".subckt s p", "R1 p 0 1k", ".ends", ".subckt t p", "R1 p 0 1k", ".ends"]));
        assert!(got(&f, false).is_empty(), "{:?}", f);
        // ports: listed twice, or named like ground (ngspice silently leaves the outside net unconnected)
        let f = v(&with(&["R2 c 0 1k", "X1 b c s", ".subckt s p p", "R1 p 0 1k", ".ends"]));
        assert!(one(&f, "DUP-NAME", ".subckt s"), "{:?}", f);
        for port in ["0", "gnd", "GND"] {
            let sub = format!(".subckt s p {}", port);
            let f = v(&with(&["R2 c 0 1k", "X1 b c s", sub.as_str(), "R1 p 0 1k", ".ends"]));
            assert!(f.iter().any(|x| x.code == "MALFORMED" && x.message.contains("global ground")), "{}: {:?}", port, f);
        }
        let f = v(&with(&["X1 b s", ".subckt s p params: r=1k r=2k", "R1 p 0 {r}", ".ends"]));
        assert!(f.iter().any(|x| x.code == "DUP-NAME" && x.message.contains("parameter 'r'")), "{:?}", f);
    }

    #[test]
    fn dependency_class_follows_ngspice_scoping() {
        // UNDEFINED-SUBCKT: nowhere, or nested in another body (local to it)
        assert!(one(&v(&with(&["X1 b nosuch"])), "UNDEFINED-SUBCKT", ""));
        let f = v(&with(&["X1 b in2", ".subckt outer p", "R5 p 0 1k", ".subckt in2 r", "R1 r 0 1k", ".ends", ".ends"]));
        assert!(one(&f, "UNDEFINED-SUBCKT", ""), "{:?}", f);
        // nor from a top-level definition instantiated by the outer body (lexical, not dynamic)
        let f = v(&with(&["X1 b outer", ".subckt outer p", "X2 p top2", ".subckt in2 r", "R1 r 0 1k", ".ends", ".ends",
            ".subckt top2 q", "X3 q in2", ".ends"]));
        assert!(one(&f, "UNDEFINED-SUBCKT", ".subckt top2"), "{:?}", f);
        // models: a card in a nested definition is not visible to the outer body, nor the outer body's card to a
        // top-level definition it instantiates
        let f = v(&with(&["X1 b outer", ".subckt outer p", "D1 p 0 dl", ".subckt inner q", ".model dl D", "R1 q 0 1k", ".ends", ".ends"]));
        assert!(one(&f, "UNDEFINED-MODEL", ".subckt outer"), "{:?}", f);
        let f = v(&with(&["X1 b outer", ".subckt outer p", ".model dl D", "X2 p inner", ".ends", ".subckt inner q", "D1 q 0 dl", ".ends"]));
        assert!(one(&f, "UNDEFINED-MODEL", ".subckt inner"), "{:?}", f);
        // UNDEFINED-PARAM is resolved per instance (ngspice scopes params dynamically)
        let f = v(&with(&["X1 b s", ".subckt s p", "R1 p 0 {nope}", ".ends"]));
        assert_eq!(got(&f, false), vec![("UNDEFINED-PARAM".to_string(), "x1".to_string())], "{:?}", f);
        let f = v(&with(&["X1 b s", "X2 b t", ".subckt s p params: ra=1k", "X3 p u", ".ends", ".subckt t p", "X4 p u", ".ends",
            ".subckt u q", "R1 q 0 {ra}", ".ends"]));
        assert_eq!(got(&f, false), vec![("UNDEFINED-PARAM".to_string(), "x2.x4".to_string())], "only via t: {:?}", f);
        let f = v(&with(&["X1 b s", ".subckt s p", "R1 p 0 {foo(3k)}", "B1 p 0 I=bar(V(p))", ".ends"]));
        assert_eq!(f.iter().filter(|x| x.code == "UNDEFINED-PARAM").count(), 2, "unknown functions: {:?}", f);
        // UNKNOWN-INSTANCE-PARAM: ngspice would drop it silently
        let f = v(&with(&["X1 b s q=3", ".subckt s p params: r=1k", "R1 p 0 {r}", ".ends"]));
        assert!(one(&f, "UNKNOWN-INSTANCE-PARAM", ""), "{:?}", f);
        // DUP-SUBCKT / DUP-MODEL: only when the bodies differ (ngspice keeps the first)
        let f = v(&with(&["X1 b s", ".subckt s p", "R1 p 0 1k", ".ends", ".subckt s p", "R1 p 0 2k", ".ends"]));
        assert!(one(&f, "DUP-SUBCKT", ""), "{:?}", f);
        let f = v(&with(&["X1 b s", ".subckt s p", "R1 p 0 1k", ".ends", ".subckt s p", "R1 p 0 1k", ".ends s"]));
        assert!(got(&f, false).is_empty(), "identical: {:?}", f);
        let f = v(&with(&["D1 b 0 dd", ".model dd D(is=1e-14)", ".model dd D(is=2e-14)"]));
        assert!(one(&f, "DUP-MODEL", ""), "{:?}", f);
        // recursion, direct and through another definition
        let f = v(&with(&["X1 b s", ".subckt s p", "X2 p s", "R1 p 0 1k", ".ends"]));
        assert!(one(&f, "SUBCKT-RECURSION", "x1"), "{:?}", f);
        let f = v(&with(&["X1 b s", ".subckt s p", "X2 p t", "R1 p 0 1k", ".ends", ".subckt t p", "X3 p s", ".ends"]));
        assert!(f.iter().any(|x| x.code == "SUBCKT-RECURSION" && x.message.contains("s -> t")), "{:?}", f);
        // elements named by K, F, H, W live in the same body
        let f = v(&with(&["X1 b s", ".subckt s p", "L1 p 0 1m", "K1 L1 L9 0.9", ".ends"]));
        assert!(one(&f, "UNDEFINED-ELEMENT", ".subckt s"), "{:?}", f);
        let f = v(&with(&["X1 b s", ".subckt s p", "F1 p 0 V1 2", ".ends"]));
        assert!(one(&f, "UNDEFINED-ELEMENT", ".subckt s"), "top-level V1 is not visible: {:?}", f);
        let f = v(&with(&["R3 b 0 1k", "L1 b 0 1m", "K1 L1 R3 0.9"]));
        assert!(f.iter().any(|x| x.code == "MALFORMED" && x.message.contains("crashes")), "{:?}", f);
        let f = v(&with(&["R3 b 0 1k", "F1 b 0 R3 2"]));
        assert!(f.iter().any(|x| x.code == "MALFORMED" && x.message.contains("controlling")), "{:?}", f);
    }

    #[test]
    fn connectivity_class_on_the_flattened_circuit() {
        // floating internal island: reported per instance, with its path
        let deck = with(&["X1 b s", "X2 b s", ".subckt s p", "R1 p 0 1k", "C1 m n 1n", ".ends"]);
        let f = v(&deck);
        let scopes: Vec<String> = f.iter().filter(|x| x.code == "NO-DC-PATH").map(|x| x.scope.clone()).collect();
        assert_eq!(scopes, vec!["x1", "x1", "x2", "x2"], "{:?}", f);
        assert!(has_node(&f, "NO-DC-PATH", "x2.m"));
        // an isolated transformer winding is singular in ngspice; a leak to ground fixes it (probed)
        let xf = ["V2 i 0 SIN(0 1 1k)", "R9 i j 1", "X1 j xf", ".subckt xf p", "L1 p 0 1m", "L2 m n 1m", "R1 m n 1k", "K1 L1 L2 0.99"];
        let f = v(&[&xf[..], &[".ends"]].concat());
        assert!(has_node(&f, "NO-DC-PATH", "x1.m") && has_node(&f, "NO-DC-PATH", "x1.n"), "{:?}", f);
        assert!(got(&v(&[&xf[..], &["Rl n 0 1g", ".ends"]].concat()), false).is_empty());
        // port mapping can short a source or close a loop that the body alone does not show
        let f = v(&with(&["X1 b b s", ".subckt s p q", "V9 p q 1", ".ends"]));
        assert!(one(&f, "SHORTED-VSRC", ""), "{:?}", f);
        let f = v(&["V1 a 0 1", "X1 a 0 s", ".subckt s p q", "V9 p q 2", ".ends"]);
        assert!(one(&f, "V-LOOP", ""), "{:?}", f);
        // ISRC-OPEN inside a body
        let f = v(&with(&["X1 b s", ".subckt s p", "R1 p 0 1k", "I1 m 0 1m", ".ends"]));
        assert!(has_node(&f, "ISRC-OPEN", "x1.m"), "{:?}", f);
    }

    fn has_node(f: &[Finding], code: &str, node: &str) -> bool {
        f.iter().any(|x| x.code == code && x.nodes.iter().any(|n| n == node))
    }

    #[test]
    fn unsupported_class_in_bodies() {
        let f = v(&with(&["X1 b s", ".subckt s p", "E1 m 0 LAPLACE {V(p)} = {1/(1+s)}", "R1 m 0 1k", ".ends"]));
        assert!(one(&f, "UNSUPPORTED", ".subckt s"), "{:?}", f);
        assert!(one(&v(&with(&["X1 b s", ".subckt s p", "N1 p 0 va", ".ends"])), "UNSUPPORTED", ".subckt s"));
        let f = v(&with(&["X1 b s", ".subckt s p", ".param k={if(1,2,3)}", "R1 p 0 {k}", ".ends"]));
        assert!(one(&f, "UNSUPPORTED", ".subckt s"), "{:?}", f);
    }

    #[test]
    fn limited_class_names_what_is_not_analysed() {
        let lim = |f: &[Finding]| -> Vec<String> {
            f.iter().filter(|x| class_of(&x.code) == "limited").map(|x| format!("{} {}", x.scope, x.devices.join(","))).collect()
        };
        let f = v(&with(&["X1 b s", ".subckt s p", "R1 p 0 1k", "A1 [p] [d] adc", ".model adc adc_bridge", ".ends"]));
        assert_eq!(lim(&f), vec![".subckt s A1"], "{:?}", f);
        assert!(got(&f, false).is_empty(), "{:?}", f);
        let f = v(&with(&["X1 b s", ".subckt s p", "B1 p 0 I=V(p)/1k", "E1 m 0 VALUE={2*V(p)}", "R1 m 0 1k", ".ends"]));
        assert_eq!(lim(&f), vec![".subckt s B1", ".subckt s E1"], "{:?}", f);
        assert_eq!(lim(&v(&with(&["T1 b 0 c 0 Z0=50 TD=1n", "R2 c 0 50"]))), vec![" T1"]);
        let f = v(&with(&["X1 b s", ".include x.lib"]));
        assert_eq!((lim(&f), got(&f, false)), (vec![" X1".to_string()], vec![]), "{:?}", f);
        let f = v(&with(&["X1 b s", ".subckt s p", ".ic v(p)=0", "D1 p 0 dd", ".model dd D(level={lv})", ".ends"]));
        assert_eq!(lim(&f), vec![".subckt s s", ".subckt s D1"], "{:?}", f);
    }

    /// Legitimate ngspice constructs that must stay clean: no error, and no limited note either.
    #[test]
    fn false_positive_guards() {
        let clean: Vec<(&str, Vec<&str>)> = vec![
            ("0 inside a body is global, even as the only ground", vec!["V1 a b 1", "R2 a b 1k", "X1 b s", ".subckt s p", "R1 p 0 1k", ".ends"]),
            ("gnd is node 0", vec!["V1 a gnd 1", "R1 a gnd 1k", "X1 a s", ".subckt s p", "R2 p GND 1k", ".ends"]),
            (".global node", vec!["V1 vg 0 2", ".global vg", "X1 b s", "R3 b 0 1k", ".subckt s p", "R1 vg p 1k", ".ends"]),
            ("floating subckt: no ground of its own", vec!["V1 a 0 1", "X1 a b rr", "R2 b 0 1k", ".subckt rr p q", "R1 p m 1k", "R2 m q 1k", ".ends"]),
            ("differential input pair, no internal ground", vec!["V1 a 0 1", "V2 c 0 0.5", "X1 a c o amp", "R3 o 0 1k",
                ".subckt amp inp inn out", "Rin inp inn 1meg", "E1 out 0 inp inn 1e5", ".ends"]),
            ("transformer, no galvanic path between windings, each grounded through the ports",
             vec!["V1 a 0 SIN(0 1 1k)", "R0 a p 1", "X1 p 0 s1 s2 xf", "R1 s1 s2 1k", "R2 s2 0 1meg",
                  ".subckt xf p q s t", "L1 p q 1m", "L2 s t 1m", "K1 L1 L2 0.99", ".ends"]),
            ("continuations and ; $ // comments in a body", vec!["V1 a 0 1", "X1 a b s RA=2k ; comment", "R2 b 0 1k",
                ".subckt s p q", "* comment", "+ params: RA=1k", "R1 p q $ comment", "* between", "+ {RA} // comment", ".ends"]),
            ("local model shadows a global one", vec!["V1 a 0 5", "R0 a b 1k", "X1 b s", ".model dl D(is=1e-20)", ".subckt s p", ".model dl D(is=1e-14)", "D1 p 0 dl", ".ends"]),
            ("model from the enclosing body", vec!["V1 a 0 5", "R0 a b 1k", "X1 b outer", ".subckt outer p", ".model dl D", "X2 p inner", ".subckt inner q", "D1 q 0 dl", ".ends", ".ends"]),
            ("nested definition used from a sibling nested body", vec!["V1 a 0 1", "R0 a b 1k", "X1 b outer", ".subckt outer p", "X2 p in1",
                ".subckt in1 q", "X3 q in2", ".ends", ".subckt in2 r", "R1 r 0 1k", ".ends", ".ends"]),
            ("local definition shadows a global one", vec!["V1 a 0 1", "R0 a b 1k", "X1 b outer", ".subckt outer p", "X2 p s", ".subckt s r", "R1 r 0 1k", ".ends", ".ends", ".subckt s r", "R1 r 0 3k", ".ends"]),
            ("params: dynamic scope, defaults, .param, .func, instance params:", vec!["V1 a 0 1", "R0 a b 1k", ".param RG=3k", "X1 b outer params: RA={RG}",
                ".subckt outer p params: RA=1k", ".param RB={RA*2}", ".func dbl(x) {2*x}", "X2 p inner", ".ends",
                ".subckt inner q", "R1 q 0 {dbl(RB)+RA+sqrt(4)+0*temper}", ".ends"]),
            ("G reading its own output is a DC path", vec!["V1 a 0 1", "X1 a s", ".subckt s p", "G1 p m p m 1m", "R1 m 0 1k", ".ends"]),
            ("unreached definitions are not analysed (ngspice ignores them)", vec!["V1 a 0 1", "R1 a 0 1k", ".subckt junk p", "R1 p", "D1 p 0 nomodel", "R2 p 0 {nope}", ".ends"]),
            ("spaced params and params: keyword", vec!["V1 a 0 1", "R0 a b 1k", "X1 b s params: r = 3k", ".subckt s p params: r = 1k", "R1 p 0 {r}", ".ends"]),
        ];
        for (label, deck) in clean {
            let f = v(&deck);
            let bad: Vec<&Finding> = f.iter().filter(|x| x.severity == "error" || class_of(&x.code) == "limited").collect();
            assert!(bad.is_empty(), "{}: {:?}", label, bad);
        }
        // a B I= reading its own output is a conductance: no false NO-DC-PATH (still limited: expression unread)
        let f = v(&["V1 a 0 1", "X1 a s", ".subckt s p", "B1 p m I=V(p,m)/1k", "B2 m 0 I=V(m)/1k", ".ends"]);
        assert!(!f.iter().any(|x| x.code == "NO-DC-PATH" || x.code == "ISRC-OPEN"), "{:?}", f);
    }

    #[test]
    fn expression_and_token_helpers() {
        let ids = |e: &str| expr_idents(e).into_iter().map(|(n, c)| format!("{}{}", n, if c { "()" } else { "" })).collect::<Vec<_>>();
        assert_eq!(ids("RA*2.5meg + 1e-3*exp(B) - V(x1.n, 0) + i(V1) + @r1[i]"), vec!["ra", "exp()", "b"]);
        assert_eq!(ids("V(a)*gain tc1=0.01 tc2 = 0"), vec!["gain"]);
        assert_eq!(ids("a==b ? 1k : 10uF"), vec!["a", "b"]);
        assert_eq!(args("a b s params: r = 2k q={x + 1}"), (
            vec!["a".to_string(), "b".to_string(), "s".to_string()],
            vec![("r".to_string(), "2k".to_string()), ("q".to_string(), "{x + 1}".to_string())]
        ));
        assert_eq!(brace_groups("R1 a b {RA*2} tc1='k + 1'"), vec!["RA*2", "k + 1"]);
        assert_eq!(after_tokens(".subckt s a b", 2), "a b");
        assert_eq!(v_refs("V(a,b)+v( c )-V(x1.n)"), vec!["a", "b", "c", "x1.n"]);
        assert_eq!(card_type(&["D(is=1", "level={lv})"]), ("d".to_string(), 0));
        assert_eq!(card_type(&["NPN", "level", "=", "4"]), ("npn".to_string(), 4));
        let l = logical_lines(&["t", "R1 a b ; x", "* c", "+ 1k", ".control", "run", ".endc", "R2 a 0 1k $ y", ".end", "R3 z"]
            .iter().map(|s| s.to_string()).collect::<Vec<_>>());
        assert_eq!(l, vec!["R1 a b 1k", "R2 a 0 1k"]);
    }

    #[test]
    fn dc_edges_follow_the_table() {
        let d = |raw: &str, nodes: &[&str]| {
            let nodes: Vec<String> = nodes.iter().map(|s| s.to_string()).collect();
            let kind = raw.chars().next().unwrap();
            Device { name: raw.split_whitespace().next().unwrap().to_string(), dc: dc_rule(kind, raw, &nodes), nodes, raw: raw.to_string(), kind, sub: None }
        };
        assert_eq!(dc_edges(&d("M1 d g s b nm", &["d", "g", "s", "b"])), vec![(0, 2), (0, 3), (2, 3)]);
        assert_eq!(dc_edges(&d("S1 a b c 0 sw", &["a", "b", "c", "0"])), vec![(0, 1)]);
        assert_eq!(dc_edges(&d("T1 a 0 b 0 Z0=50 TD=1n", &["a", "0", "b", "0"])), vec![(0, 1), (2, 3)]);
        assert!(dc_edges(&d("G1 o 0 c 0 1m", &["o", "0", "c", "0"])).is_empty());
        assert!(dc_edges(&d("B1 o 0 I=1m", &["o", "0"])).is_empty());
        assert_eq!(dc_edges(&d("B1 o 0 V=1", &["o", "0"])), vec![(0, 1)]);
    }
}
