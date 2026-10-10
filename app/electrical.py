"""Electrical domain binding: validates a definition's [sim.electrical] section and renders it to SPICE lines.
Pure Python, imports only .components. A part's `line` / `models` are str.format templates; the catalog never sees
SPICE text and no per-kind code exists here."""
import re
import string

from .components import ModelError

DEVICE_LETTERS = "RCLDVI"      # letters the core validator models statically; extend after checking validate.rs
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
CURRENT = re.compile(r"(@\{dev_lc\}\[[a-z]+\]|i\(\{dev_lc\}\))\Z")
KEYS = ("device", "line", "models", "current", "ground")


def _names(tpl):
    """Placeholder names of a template; ValueError if one is not a plain identifier (no '.', '[', '!', ':')."""
    out = set()
    for _, name, spec, conv in string.Formatter().parse(tpl):
        if name is None: continue
        if not IDENT.match(name) or spec or conv:
            raise ValueError(f"placeholder {{{name}{'!' + conv if conv else ''}{':' + spec if spec else ''}}} "
                             "is not a plain identifier")
        out.add(name)
    return out


def check(section, pins, params):
    """DEF-SIM diagnostics for a [sim.electrical] section ({code, severity, message, field}); [] when valid."""
    out = []

    def bad(msg, field=""):
        out.append({"code": "DEF-SIM", "severity": "error", "message": msg,
                    "field": "sim.electrical" + (f".{field}" if field else "")})

    def template(tpl, field, names, prefix):
        if not isinstance(tpl, str): return bad("must be a string; write the SPICE text as one quoted line", field) or set()
        if "\n" in tpl or "\r" in tpl: bad("must be a single line; use one entry per SPICE line", field)
        if prefix and not tpl.startswith(prefix): bad(f"must start with '{prefix}'", field)
        try: used = _names(tpl)
        except ValueError as e: return bad(f"{e}; use plain names such as {{dev}} or {{R}}", field) or set()
        for n in sorted(used - names):
            bad(f"unknown placeholder {{{n}}}; allowed here: {', '.join(sorted(names))}", field)
        return used

    pids, by_key = [p.id for p in pins], {p.key: p for p in params}
    for k in section:
        if k not in KEYS: bad(f"unknown key '{k}'; allowed: {', '.join(KEYS)}", k)
    ground = section.get("ground")
    if "ground" in section and not isinstance(ground, bool): bad("must be true or false", "ground")
    if ground is True:
        if "line" in section: bad("a section has either 'line' or 'ground = true', not both", "line")
        for k in ("device", "models", "current"):
            if k in section: bad("a ground section has no device, line, models or current", k)
        if len(pids) != 1: bad(f"a ground part has exactly one pin (this one has {len(pids)})", "ground")
        return out
    if "line" not in section: bad("needs 'line' (or 'ground = true' for a reference node)", "line")
    dev = section.get("device")
    if not (isinstance(dev, str) and len(dev) == 1 and dev in DEVICE_LETTERS):
        bad(f"device must be one of {', '.join(DEVICE_LETTERS)}; got {dev!r}", "device")
    for p in pids:
        if not IDENT.match(p) or p in ("dev", "dev_lc"):
            bad(f"pin id '{p}' cannot be a template placeholder; use letters, digits and _ starting with a letter "
                "(not dev or dev_lc)", "line")
    line, branches = section.get("line"), {}     # branch label (None = plain line) -> (template, applicable params)
    if isinstance(line, str):
        branches[None] = (line, {p.key for p in params if not p.when})
    elif isinstance(line, dict):
        sel = line.get("select")
        sp = by_key.get(sel)
        if sp is None or sp.kind != "choice":
            bad(f"select must name a choice parameter; got {sel!r}", "line.select")
        else:
            for k in sorted(set(line) - {"select", *sp.choices}): bad(f"'{k}' is not a choice of {sel}", f"line.{k}")
            for c in sp.choices:
                if c not in line: bad(f"missing a line for choice '{c}' of {sel}", f"line.{c}")
                else: branches[c] = (line[c], {p.key for p in params if set(p.when) <= {(sel, c)}})
    elif line is not None:
        bad("must be a string or a table {select = <choice param>, <choice> = <line>}", "line")
    if section.get("device") in tuple(DEVICE_LETTERS) and len(pids) != 2:
        bad(f"a {section['device']} device has exactly two terminals; this part has {len(pids)} pins", "line")
    for lab, (tpl, ok) in branches.items():
        field = "line" if lab is None else f"line.{lab}"
        template(tpl, field, {"dev", "dev_lc", *pids, *ok}, "{dev} ")
        if not isinstance(tpl, str) or not tpl.startswith("{dev} "): continue
        if ";" in tpl or "$" in tpl: bad("must not contain ';' or '$' (SPICE comments would hide what follows)", field)
        nodes, tail = tpl.split(None, 3)[1:3], (tpl.split(None, 3) + [""])[3]
        try: tail_names = _names(tail)
        except ValueError: continue    # already reported by template()
        if sorted(nodes) != sorted(f"{{{p}}}" for p in pids) or tail_names & set(pids):
            bad("the node fields must be exactly the pin placeholders, once each, right after {dev} "
                f"(here: {' '.join('{' + p + '}' for p in pids)}); a literal node or a pin elsewhere would connect "
                "to nets that are not drawn", field)
    models = section.get("models", [])
    if not isinstance(models, list): bad("must be a list of .model lines", "models")
    else:
        for i, m in enumerate(models):
            template(m, f"models[{i}]", {"dev", "dev_lc", *(p.key for p in params if not p.when)}, ".model ")
            if isinstance(m, str) and m.startswith(".model ") and len(m.split()) < 3:
                bad("needs a model name and a type: '.model <name> <TYPE>(...)'", f"models[{i}]")
    cur = section.get("current")
    if "current" in section and not (isinstance(cur, str) and CURRENT.match(cur)):
        bad("must look like '@{dev_lc}[i]' or 'i({dev_lc})'", "current")
    return out


def model_key(text):
    """(lower-case name, normalised body) of a `.model <name> <body>` line. ngspice model names are global to the deck
    and case-insensitive; two lines with one name and different bodies cannot both be in force."""
    t = text.split()
    return t[1].lower(), " ".join(t[2:]).lower()


def fixed_models(section):
    """{name: body} of the section's models whose name has no placeholder (one deck-wide model for every instance)."""
    out = {}
    for m in section.get("models", ()):
        k, body = model_key(m)
        if "{" not in k: out[k] = body
    return out


def device_name(section, part_name):
    """SPICE device name: the letter decides the device type (SW1 -> RSW1), so it is prefixed unless present."""
    letter = section["device"]
    return part_name if part_name.upper().startswith(letter) else letter + part_name


def _r(v):   # numbers reach the core as repr(float); choices as text
    return v if isinstance(v, str) else repr(float(v))


def emit(section, part_name, nodes, values):
    """-> (lines, models). nodes: {pin id: net}; values: parsed applicable params {key: float | choice}."""
    dev = device_name(section, part_name)
    kw = {**{k: _r(v) for k, v in values.items()}, **nodes, "dev": dev, "dev_lc": dev.lower()}
    line = section["line"]
    if isinstance(line, dict): line = line[values[line["select"]]]
    return [line.format(**kw)], [m.format(**kw) for m in section.get("models", [])]


def build_netlist(parts, nets, defs):
    """parts: {name: {"kind", "params", ...}} in creation order; nets: {"Part.pin": net}; defs: a Catalog.
    -> {"lines", "models", "saves", "observables", "derived", "signals"}; raises ModelError on a bad value or a
    part without an electrical binding (never skips one silently).

    observables: ngspice vector names to read from the core. derived: {'v(R1)': (netA, netB)} across-part
    voltages that sim.py computes by subtracting two core node series. signals: readable names for all of them,
    [{"name", "vector", "quantity", "unit", "kind": "node"|"across"|"current", "part", "pins"}]."""
    if not parts:
        raise ModelError("circuit is empty")
    lines, models, cur, derived, signals = [], [], [], {}, []
    used, owner = set(), {}      # owner: model name -> (body, kind of the first part that emitted it)
    for name, d in parts.items():
        c = defs.get(d["kind"])
        sec = c.sim.get("electrical") if c.simulated else None
        if sec is None:
            raise ModelError(f"{name} ({c.name}) has no electrical model")
        if sec.get("ground"):
            continue
        pins = c.terminals
        nodes = {t: nets[f"{name}.{t}"] for t in pins}
        used |= set(nodes.values())
        dev = device_name(sec, name)
        ls, ms = emit(sec, name, nodes, c.values(d["params"], name))
        lines += ls
        for m in ms:
            key, body = model_key(m)
            if key not in owner:
                owner[key] = (body, d["kind"]); models.append(m)
            elif owner[key][0] != body:
                a, b = owner[key][1], d["kind"]
                raise ModelError(f"model '{key}' is defined differently by {a} and {b}" if a != b else
                                 f"model '{key}' gets different parameters in different {a} parts"
                                 ", so it cannot be shared: put {dev_lc} in the model name" )
        if len(pins) == 2:
            derived[f"v({name})"] = tuple(nodes[t] for t in pins)
            signals.append({"name": f"V({name})", "vector": f"v({name})", "quantity": "voltage", "unit": "V",
                            "kind": "across", "part": name, "pins": [f"{name}.{t}" for t in pins]})
        if "current" in sec:
            vec = sec["current"].format(dev_lc=dev.lower())
            cur.append(vec)
            signals.append({"name": f"I({name})", "vector": vec, "quantity": "current", "unit": "A", "kind": "current",
                            "part": name, "pins": [f"{name}.{pins[0]}"]})
    if not lines:
        raise ModelError("circuit has no components")
    node_obs = [f"v({n})" for n in sorted(used - {"0"}, key=lambda s: int(s[1:]))]
    members = {}
    for pin, n in nets.items():
        members.setdefault(n, []).append(pin)
    signals = [{"name": f"V({v[2:-1]})", "vector": v, "quantity": "voltage", "unit": "V",
                "kind": "node", "part": None, "pins": members[v[2:-1]]} for v in node_obs] + signals
    # vectors only reachable through '.save' (device internals, written @dev[x]); i(vsrc) is always saved
    saves = [f".save all {v}" for v in cur if v.startswith("@")]
    # observable order (kept from the pre-catalog app): nodes, source currents, device currents
    obs = node_obs + [v for v in cur if not v.startswith("@")] + [v for v in cur if v.startswith("@")]
    return {"lines": lines, "models": models, "saves": saves, "observables": obs, "derived": derived,
            "signals": signals}
