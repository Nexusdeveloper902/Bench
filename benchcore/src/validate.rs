//! Static topology + component-terminal validator (pre-simulation).
//!
//! Direct port of the validated `exp-safety/validator.py` logic:
//! DC-conductance BFS, ISRC-OPEN (D4 silent class), V-LOOP union-find,
//! NO-GROUND, DUP-NAME, MALFORMED, degenerate values, B-expression checks.
//! Severity `error` rejects before simulation; `warn` proceeds with review.

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
    }
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

#[derive(Debug, Clone)]
struct Device {
    name: String,
    nodes: Vec<String>,
    raw: String,
    kind: char,
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

fn dc_conductance(kind: char, dev: &Device) -> bool {
    match kind {
        'R' | 'L' | 'V' | 'E' | 'H' | 'D' | 'Q' | 'M' | 'J' | 'X' => true,
        'B' => b_is_voltage(&dev.raw),
        _ => false,
    }
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

struct Parsed {
    devices: Vec<Device>,
    b_exprs: Vec<(String, Vec<String>, String, bool)>, // name, pnodes, expr, is_v
    analyses: Vec<String>,
    errors: Vec<Finding>,
}

fn parse_netlist(lines: &[String]) -> Parsed {
    let mut devices: Vec<Device> = vec![];
    let mut b_exprs: Vec<(String, Vec<String>, String, bool)> = vec![];
    let mut analyses: Vec<String> = vec![];
    let mut errors: Vec<Finding> = vec![];
    if lines.is_empty() {
        return Parsed {
            devices,
            b_exprs,
            analyses,
            errors,
        };
    }
    // join `+` continuations
    let mut joined: Vec<String> = vec![];
    for raw in lines.iter().skip(1) {
        let s = raw.trim();
        if s.starts_with('+') && !joined.is_empty() {
            let last = joined.pop().unwrap();
            joined.push(format!("{} {}", last, s[1..].trim()));
        } else {
            joined.push(raw.clone());
        }
    }
    // pre-pass: .model / .subckt names (devices precede .model cards in a deck)
    // name -> (type word, level); type/level feed the max-node-count check for Q and M
    let mut models: HashMap<String, (String, u32)> = HashMap::new();
    let mut subckts: HashMap<String, usize> = HashMap::new();
    let mut has_include = false;
    let mut depth = 0usize; // a .model inside a .subckt body is local to it in ngspice
    for raw in joined.iter() {
        let tok: Vec<&str> = raw.split_whitespace().collect();
        if tok.first().is_some_and(|t| t.eq_ignore_ascii_case(".ends")) {
            depth = depth.saturating_sub(1);
        }
        if tok.len() < 2 {
            continue;
        }
        match tok[0].to_lowercase().as_str() {
            ".model" if depth == 0 => {
                // `npn(is=1 level = 4)`, `npn level=4`: parens and '=' act as separators
                let rest = tok[2..].join(" ").to_lowercase().replace(['(', ')', '='], " ");
                let w: Vec<&str> = rest.split_whitespace().collect();
                let level = w
                    .iter()
                    .position(|t| *t == "level")
                    .and_then(|i| w.get(i + 1))
                    .and_then(|v| v.parse::<f64>().ok())
                    .map_or(1, |v| v as u32);
                let ty = w.first().map_or(String::new(), |t| t.to_string());
                models.insert(tok[1].to_lowercase(), (ty, level));
            }
            ".subckt" => {
                depth += 1;
                let ports = positional(&tok[2..])
                    .iter()
                    .take_while(|t| *t != "params:")
                    .count();
                subckts.insert(tok[1].to_lowercase(), ports);
            }
            ".include" | ".inc" | ".lib" => has_include = true,
            _ => {}
        }
    }
    let undefined_model = |name: &str, model: &str, nodes: &[String]| -> Option<Finding> {
        if has_include || models.contains_key(&model.to_lowercase()) {
            return None;
        }
        Some(finding(
            "UNDEFINED-MODEL",
            "error",
            format!(
                "{}: model '{}' has no .model card in this deck (ngspice aborts the run)",
                name, model
            ),
            nodes.to_vec(),
            vec![name.to_string()],
            "authoring",
        ))
    };
    let mut in_control = false;
    let mut in_subckt = false;
    let mut seen: HashSet<String> = HashSet::new();
    for raw in joined.iter() {
        let s = raw.trim();
        if s.is_empty() || s.starts_with('*') {
            continue;
        }
        let low = s.to_lowercase();
        if low == ".control" {
            in_control = true;
            continue;
        }
        if low == ".endc" {
            in_control = false;
            continue;
        }
        if in_control {
            continue;
        }
        if low == ".end" {
            break;
        }
        if s.starts_with('.') {
            let tok: Vec<&str> = s.split_whitespace().collect();
            if tok.is_empty() {
                continue;
            }
            let cmd = tok[0].to_lowercase();
            if cmd == ".subckt" {
                in_subckt = true;
            } else if cmd == ".ends" {
                in_subckt = false;
            }
            if [".tran", ".op", ".dc", ".ac", ".noise", ".pz"].contains(&cmd.as_str()) {
                analyses.push(s.to_string());
            }
            continue;
        }
        if in_subckt {
            continue; // subcircuit bodies are opaque to top-level topology
        }
        let parts: Vec<&str> = s.split_whitespace().collect();
        if parts.is_empty() {
            continue;
        }
        let name = parts[0].to_string();
        let kind = name.chars().next().unwrap_or('?').to_ascii_uppercase();
        let low_name = name.to_lowercase();
        if !seen.insert(low_name.clone()) {
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
        match kind {
            'R' | 'C' | 'L' | 'D' => {
                let nodes = if parts.len() >= 3 {
                    lower_nodes(&parts[1..3])
                } else {
                    vec![]
                };
                if parts.len() < 4 {
                    errors.push(finding(
                        "MALFORMED",
                        "error",
                        format!("{}: {} needs 2 nodes + value", name, kind),
                        nodes.clone(),
                        vec![name.clone()],
                        "authoring",
                    ));
                } else if kind == 'D' {
                    errors.extend(undefined_model(&name, parts[3], &nodes));
                }
                devices.push(Device {
                    name,
                    nodes,
                    raw: s.to_string(),
                    kind,
                });
            }
            'V' | 'I' | 'B' => {
                let nodes = if parts.len() >= 3 {
                    lower_nodes(&parts[1..3])
                } else {
                    vec![]
                };
                if parts.len() < 3 {
                    errors.push(finding(
                        "MALFORMED",
                        "error",
                        format!("{}: {} needs 2 nodes", name, kind),
                        nodes.clone(),
                        vec![name.clone()],
                        "authoring",
                    ));
                }
                if kind == 'B' {
                    // extract V=/I= expr
                    let expr_opt = extract_b_expr(s);
                    match expr_opt {
                        Some((is_v, expr)) => {
                            b_exprs.push((name.clone(), nodes.clone(), expr, is_v));
                        }
                        None => {
                            errors.push(finding(
                                "B-NOEXPR",
                                "error",
                                format!("{}: B-source missing V=/I= expression", name),
                                nodes.clone(),
                                vec![name.clone()],
                                "expression",
                            ));
                        }
                    }
                }
                devices.push(Device {
                    name,
                    nodes,
                    raw: s.to_string(),
                    kind,
                });
            }
            'E' | 'G' => {
                let nodes = if parts.len() >= 5 {
                    lower_nodes(&parts[1..5])
                } else {
                    vec![]
                };
                if parts.len() < 6 {
                    errors.push(finding(
                        "MALFORMED",
                        "error",
                        format!("{}: {} needs 4 nodes + gain", name, kind),
                        nodes.clone(),
                        vec![name.clone()],
                        "authoring",
                    ));
                }
                devices.push(Device {
                    name,
                    nodes,
                    raw: s.to_string(),
                    kind,
                });
            }
            'H' | 'F' => {
                let nodes = if parts.len() >= 3 {
                    lower_nodes(&parts[1..3])
                } else {
                    vec![]
                };
                devices.push(Device {
                    name,
                    nodes,
                    raw: s.to_string(),
                    kind,
                });
            }
            'Q' | 'J' | 'M' => {
                let pos = positional(&parts);
                // ngspice reads the required nodes, then takes the first token that names a
                // model; tokens before it are optional nodes (Q: substrate, thermal; M: SOI).
                let (min, max) = match kind {
                    'Q' => (3, 5),
                    'M' => (4, 7),
                    _ => (3, 3),
                };
                let n = (min..=max)
                    .find(|&k| pos.get(k + 1).is_some_and(|t| models.contains_key(t)))
                    .unwrap_or(min);
                let mut nodes = vec![];
                if pos.len() < n + 2 {
                    // only a short line is malformed: node and model names are separate namespaces
                    let msg = match pos.iter().skip(1).find(|t| models.contains_key(*t)) {
                        Some(m) => format!(
                            "{}: model name '{}' in a node position; {} needs {} nodes + model",
                            name, m, kind, n
                        ),
                        None => format!("{}: {} needs {} nodes + model", name, kind, n),
                    };
                    errors.push(finding("MALFORMED", "error", msg, vec![], vec![name.clone()], "authoring"));
                } else {
                    // ngspice (47, probed): GP and other non-VBIC/HICUM/Mextram BJTs take at most 4
                    // nodes; non-SOI MOSFET levels at most 4. Only checked for models defined here.
                    let cap = match (kind, models.get(&pos[n + 1])) {
                        ('Q', Some((ty, lv))) if ty == "npn" || ty == "pnp" => {
                            Some(if [4, 6, 8, 9].contains(lv) { 5 } else { 4 })
                        }
                        ('M', Some((ty, lv))) if ty == "nmos" || ty == "pmos" => {
                            Some(if [10, 55, 56, 57, 58, 60].contains(lv) { 7 } else { 4 })
                        }
                        _ => None,
                    };
                    if cap.is_some_and(|c| n > c) {
                        let (_, lv) = &models[&pos[n + 1]];
                        errors.push(finding(
                            "MALFORMED",
                            "error",
                            format!(
                                "{}: model '{}' (level {}) accepts at most {} nodes, got {} (ngspice: too many nodes)",
                                name, pos[n + 1], lv, cap.unwrap(), n
                            ),
                            vec![],
                            vec![name.clone()],
                            "authoring",
                        ));
                    } else {
                        nodes = pos[1..=n].to_vec();
                        errors.extend(undefined_model(&name, &pos[n + 1], &nodes));
                    }
                }
                devices.push(Device {
                    name,
                    nodes,
                    raw: s.to_string(),
                    kind,
                });
            }
            'K' => {
                devices.push(Device {
                    name,
                    nodes: vec![],
                    raw: s.to_string(),
                    kind,
                });
            }
            'X' => {
                let pos = positional(&parts);
                let mut nodes = vec![];
                if pos.len() < 3 {
                    errors.push(finding(
                        "MALFORMED",
                        "error",
                        format!("{}: X needs nodes + subcircuit name", name),
                        vec![],
                        vec![name.clone()],
                        "authoring",
                    ));
                } else {
                    let n = pos.len() - 2;
                    match subckts.get(&pos[pos.len() - 1]) {
                        Some(&ports) if ports != n => errors.push(finding(
                            "MALFORMED",
                            "error",
                            format!(
                                "{}: subcircuit '{}' has {} ports, instance gives {} nodes",
                                name,
                                pos[pos.len() - 1],
                                ports,
                                n
                            ),
                            vec![],
                            vec![name.clone()],
                            "authoring",
                        )),
                        _ => nodes = pos[1..=n].to_vec(),
                    }
                }
                devices.push(Device {
                    name,
                    nodes,
                    raw: s.to_string(),
                    kind,
                });
            }
            'O' | 'U' | 'Y' | 'N' | 'T' | 'S' | 'W' | 'Z' => {
                let nodes = if parts.len() >= 3 {
                    lower_nodes(&parts[1..3])
                } else {
                    vec![]
                };
                errors.push(finding(
                    "EXOTIC-DEVICE",
                    "warn",
                    format!(
                        "{}: kind '{}' is outside static coverage (no DC/loop model); runtime verdict is load-bearing",
                        name, kind
                    ),
                    nodes.clone(),
                    vec![name.clone()],
                    "static",
                ));
                devices.push(Device {
                    name,
                    nodes,
                    raw: s.to_string(),
                    kind,
                });
            }
            _ => {
                errors.push(finding(
                    "UNKNOWN-KIND",
                    "error",
                    format!("{}: unknown device kind '{}'", name, kind),
                    vec![],
                    vec![name.clone()],
                    "static",
                ));
            }
        }
    }
    Parsed {
        devices,
        b_exprs,
        analyses,
        errors,
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

fn v_refs(expr: &str) -> Vec<String> {
    // V(nodename) references (case-insensitive V, identifier)
    let mut out = vec![];
    let chars: Vec<char> = expr.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if (chars[i] == 'V' || chars[i] == 'v')
            && (i == 0 || !(chars[i - 1].is_ascii_alphanumeric() || chars[i - 1] == '_'))
        {
            let mut j = i + 1;
            while j < chars.len() && chars[j].is_whitespace() {
                j += 1;
            }
            if j < chars.len() && chars[j] == '(' {
                j += 1;
                while j < chars.len() && chars[j].is_whitespace() {
                    j += 1;
                }
                let start = j;
                if j < chars.len() && (chars[j].is_ascii_alphabetic() || chars[j] == '_') {
                    j += 1;
                    while j < chars.len()
                        && (chars[j].is_ascii_alphanumeric() || chars[j] == '_')
                    {
                        j += 1;
                    }
                    out.push(chars[start..j].iter().collect::<String>().to_lowercase());
                }
            }
        }
        i += 1;
    }
    out
}

/// Run all static checks. Returns findings (errors + warns).
pub fn validate(lines: &[String]) -> Vec<Finding> {
    let p = parse_netlist(lines);
    let mut out = p.errors.clone();
    let devices = &p.devices;

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

    // per-device degenerate checks
    for d in devices {
        let k = d.kind;
        if (k == 'V' || k == 'E' || k == 'B')
            && d.nodes.len() >= 2
            && d.nodes[0] == d.nodes[1]
        {
            if k == 'B' && !b_is_voltage(&d.raw) {
                // current source shorted is fine-ish; skip
            } else {
                out.push(finding(
                    "SHORTED-VSRC",
                    "error",
                    format!(
                        "{}: voltage source shorted (same node both terminals)",
                        d.name
                    ),
                    vec![d.nodes[0].clone()],
                    vec![d.name.clone()],
                    "static",
                ));
            }
        }
        if (k == 'R' || k == 'C' || k == 'L')
            && d.nodes.len() >= 2
            && d.nodes[0] == d.nodes[1]
        {
            out.push(finding(
                "SHORTED-PASSIVE",
                "warn",
                format!("{}: {} shorted (both terminals on '{}')", d.name, k, d.nodes[0]),
                vec![d.nodes[0].clone()],
                vec![d.name.clone()],
                "authoring",
            ));
        }
        if k == 'R' || k == 'C' || k == 'L' {
            let parts: Vec<&str> = d.raw.split_whitespace().collect();
            if parts.len() >= 4 {
                match parse_value(parts[3]) {
                    None => {
                        out.push(finding(
                            "BAD-VALUE",
                            "warn",
                            format!("{}: unparseable value '{}'", d.name, parts[3]),
                            d.nodes.clone(),
                            vec![d.name.clone()],
                            "authoring",
                        ));
                    }
                    Some(v) => {
                        if k == 'R' && v < 0.0 {
                            out.push(finding(
                                "NEG-R",
                                "error",
                                format!("{}: negative resistance {} (non-physical; silent-risk)", d.name, parts[3]),
                                d.nodes.clone(),
                                vec![d.name.clone()],
                                "authoring",
                            ));
                        } else if (k == 'C' || k == 'L') && v < 0.0 {
                            out.push(finding(
                                "NEG-CL",
                                "error",
                                format!("{}: negative {} value {}", d.name, k, parts[3]),
                                d.nodes.clone(),
                                vec![d.name.clone()],
                                "authoring",
                            ));
                        } else if k == 'R' && v == 0.0 {
                            out.push(finding(
                                "ZERO-R",
                                "warn",
                                format!("{}: zero resistance (ideal short; V-loop risk)", d.name),
                                d.nodes.clone(),
                                vec![d.name.clone()],
                                "static",
                            ));
                        } else if (k == 'C' || k == 'L') && v == 0.0 {
                            out.push(finding(
                                "ZERO-CL",
                                "error",
                                format!("{}: zero {} value (degenerate)", d.name, k),
                                d.nodes.clone(),
                                vec![d.name.clone()],
                                "authoring",
                            ));
                        }
                    }
                }
            }
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
    let mut adj: HashMap<String, Vec<(String, String)>> = HashMap::new();
    for d in devices {
        if dc_conductance(d.kind, d) && d.nodes.len() >= 2 {
            if matches!(d.kind, 'Q' | 'M' | 'J' | 'X') {
                for i in 0..d.nodes.len() {
                    for j in (i + 1)..d.nodes.len() {
                        // MOSFET gate (index 1) is insulated: no DC edge
                        if d.kind == 'M' && (i == 1 || j == 1) {
                            continue;
                        }
                        let a = d.nodes[i].clone();
                        let b = d.nodes[j].clone();
                        adj.entry(a.clone()).or_default().push((b.clone(), d.name.clone()));
                        adj.entry(b).or_default().push((a, d.name.clone()));
                    }
                }
                continue;
            }
            let a = d.nodes[0].clone();
            let b = d.nodes[1].clone();
            adj.entry(a.clone()).or_default().push((b.clone(), d.name.clone()));
            adj.entry(b).or_default().push((a, d.name.clone()));
        }
    }
    let mut reached: HashSet<String> = HashSet::new();
    let mut stack: Vec<String> = vec![];
    if has_ground {
        reached.insert(GROUND.to_string());
        stack.push(GROUND.to_string());
    }
    while let Some(n) = stack.pop() {
        if let Some(v) = adj.get(&n) {
            for (m, _) in v.clone() {
                if reached.insert(m.clone()) {
                    stack.push(m);
                }
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
                d.kind == 'I'
                    || d.kind == 'G'
                    || d.kind == 'F'
                    || (d.kind == 'B' && {
                        let up = d.raw.to_uppercase();
                        // I= present?
                        contains_b_current(&up)
                    })
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

    // B-expression checks
    let dev_terminals: HashSet<String> = nodes.clone();
    let has_tran = p
        .analyses
        .iter()
        .any(|a| a.trim().to_lowercase().starts_with(".tran"));
    for (bname, pnodes, expr, is_v) in &p.b_exprs {
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
            if !dev_terminals.contains(&r) && r != "0" {
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

    // OPAQUE-X
    for d in devices {
        if d.kind == 'X' {
            out.push(finding(
                "OPAQUE-X",
                "warn",
                format!(
                    "{}: subcircuit instance is opaque to static checks (DC paths inside unverified; expand or whitelist)",
                    d.name
                ),
                d.nodes.clone(),
                vec![d.name.clone()],
                "static",
            ));
        }
    }

    // B-CYCLE (mutual reference warn)
    {
        let mut drivers: HashMap<String, Vec<String>> = HashMap::new();
        for (bname, pnodes, _e, _v) in &p.b_exprs {
            for pn in pnodes {
                drivers.entry(pn.clone()).or_default().push(bname.clone());
            }
        }
        let mut breads: HashMap<String, HashSet<String>> = HashMap::new();
        for (bname, _pn, expr, _v) in &p.b_exprs {
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
                            let mut cyc: Vec<String> =
                                stack[pos..].to_vec();
                            cyc.push(w.clone());
                            let mut key = {
                                let mut s: Vec<String> =
                                    cyc.iter().cloned().collect::<HashSet<_>>().into_iter().collect();
                                s.sort();
                                s
                            };
                            let _ = key;
                            let mut key2 = cyc.clone();
                            key2.sort();
                            key2.dedup();
                            if reported.insert(key2) {
                                out.push(finding(
                                    "B-CYCLE",
                                    "warn",
                                    format!(
                                        "behavioral loop {}: multistable/convergence class; needs .ic + state elements and intent review (runtime scan is backstop)",
                                        cyc.join(" -> ")
                                    ),
                                    vec![],
                                    {
                                        let mut d: Vec<String> = cyc.clone();
                                        d.sort();
                                        d.dedup();
                                        d
                                    },
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
        let keys: Vec<String> = breads.keys().cloned().collect();
        for k in keys {
            if color.get(&k).cloned().unwrap_or(Color::Black) == Color::White {
                let mut stack = vec![];
                dfs(&k, &breads, &mut color, &mut stack, &mut out, &mut reported);
            }
        }
    }

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
pub fn b_exprs(lines: &[String]) -> Vec<String> {
    parse_netlist(lines)
        .b_exprs
        .into_iter()
        .map(|(_, _, e, _)| e)
        .collect()
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
