"""Electrical domain binding: validates a definition's [sim.electrical] section and renders it to SPICE lines.
Pure Python, imports only .components. A part's `line` / `models` are str.format templates; the catalog never sees
SPICE text and no per-kind code exists here. Definition text (`defs` files, `body`) is literal ngspice text: it is
never formatted, and each unit is emitted once per deck however many parts use it (docs/adding-components.md)."""
import ast
import hashlib
import re
import string
import tomllib
from pathlib import Path
from typing import NamedTuple

from .components import ModelError

class Device(NamedTuple):
    """One SPICE element letter a top-level part may use (ngspice 47, probed: DESIGN.md 3.1 and 4.2).
    pins: allowed pin counts (= node fields, in SPICE order). models: {model type: supported levels, or None for
    any}; when set, the field after the nodes must name one of the part's own `models` of such a type. current: the
    one vector `current` may name (a wrong `.save @dev[x]` can crash ngspice), None when none is readable.
    benchcore/src/validate.rs DEVICES covers every letter, these included (parity: test_device_table_parity)."""
    pins: tuple
    models: dict
    current: str | None


BJT, MOS, MES = (1, 2, 4, 8, 9), (1, 2, 3, 4, 5, 6, 8, 9, 10, 14, 49, 54, 55, 56, 57, 58, 68, 73), (1, 2, 3, 4, 5, 6)
DEVICES = {     # primitive: no models; model-backed: models; X: subcircuit-backed (ports = the .subckt's)
    "R": Device((2,), {}, "@{dev_lc}[i]"),
    "C": Device((2,), {}, "@{dev_lc}[i]"),
    "L": Device((2,), {}, "@{dev_lc}[i]"),
    "V": Device((2,), {}, "i({dev_lc})"),
    "I": Device((2,), {}, "@{dev_lc}[c]"),
    "B": Device((2,), {}, "@{dev_lc}[i]"),      # V= / I= expression; pins may not appear in it (node rule)
    "E": Device((4,), {}, "@{dev_lc}[i]"),      # linear: o+ o- c+ c- gain
    "G": Device((4,), {}, "@{dev_lc}[i]"),
    "T": Device((4,), {}, None),                # p1+ p1- p2+ p2- Z0= TD=
    "D": Device((2,), {"d": (1, 3)}, "@{dev_lc}[id]"),
    "S": Device((4,), {"sw": None}, "@{dev_lc}[i]"),                    # n+ n- nc+ nc- model
    "Q": Device((3, 4), {"npn": BJT, "pnp": BJT}, "@{dev_lc}[ic]"),
    "J": Device((3,), {"njf": (1, 2), "pjf": (1, 2)}, "@{dev_lc}[id]"),
    "M": Device((3, 4), {"nmos": MOS, "pmos": MOS, "vdmos": None}, "@{dev_lc}[id]"),   # 3 pins iff VDMOS
    "Z": Device((3,), {"nmf": MES, "pmf": MES}, "@{dev_lc}[cd]"),     # @z[id] can segfault ngspice
    "X": Device(range(1, 1000), {}, None),      # node fields = the named .subckt's ports, in order
}
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
KEYS = ("device", "line", "models", "current", "ground", "defs", "body", "model_kind", "verification", "provenance", "ac")
MODEL_KINDS = ("primitive", "model", "third-party-model", "project-subckt", "third-party-subckt", "behavioral",
               "equivalent")
X_KINDS = ("project-subckt", "third-party-subckt", "behavioral", "equivalent")
VERIFICATION = ("analytic", "reference", "smoke", "none")
PROVENANCE_KEYS = ("source", "url", "version", "license", "attribution", "retrieved", "changes", "notes")
MANIFEST_KEYS = ("path", "origin", "source", "url", "version", "license", "attribution", "retrieved", "sha256", "changes")
MODELS_DIR = "_models"          # <library root>/_models/: shared definition files, `models:<path>` in `defs`


class Unit(NamedTuple):
    """One definition unit (a `defs` file or a `body`), parsed once at catalog load and emitted once per deck.
    lines: deck lines (comments and blank lines dropped, `+` continuations kept). subckts: top-level .subckt name
    (lower case) -> (ports, {param: default}). models: top-level .model name -> card. xrefs: (instance, subckt name,
    node count) of every X line inside a body."""
    label: str
    sha: str
    lines: tuple
    subckts: dict
    models: dict
    xrefs: tuple


_COMMENT = re.compile(r"(;|(?:^|\s)(?:\$|//)).*")


def parse_def(text, label="", sha=""):
    """-> (Unit, [problem]). Header-level checks only (directive allowlist, balanced .subckt/.ends, duplicate names);
    element syntax, references and connectivity inside bodies are validate_bodies()'s."""
    probs, lines, logical = [], [], []
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("*"): continue
        s = _COMMENT.sub("", s).rstrip()
        if not s: continue
        lines.append(s)
        if s.startswith("+"):
            if logical: logical[-1] += " " + s[1:].strip()
            else: probs.append("starts with a '+' continuation line")
        else:
            logical.append(s)
    depth, subckts, models, xrefs = 0, {}, {}, []
    for s in logical:
        t = re.sub(r"\s*=\s*", "=", s).split()
        w = t[0].lower()
        if w == ".subckt":
            if len(t) < 2:
                probs.append("'.subckt' needs a name"); continue
            ports, params = [], {}
            for tok in t[2:]:
                if tok.lower() == "params:": continue
                if "=" in tok: params[tok.split("=", 1)[0].lower()] = tok.split("=", 1)[1]
                elif params: probs.append(f".subckt {t[1]}: port '{tok}' comes after a parameter")
                else: ports.append(tok)
            if depth == 0:
                if t[1].lower() in subckts: probs.append(f"defines .subckt '{t[1]}' twice; ngspice keeps the first")
                subckts[t[1].lower()] = (tuple(ports), params)
            depth += 1
        elif w == ".ends":
            if depth: depth -= 1
            else: probs.append("'.ends' without a matching '.subckt'")
        elif w == ".model":
            if len(t) < 3:
                probs.append(f"'{s}' needs a model name and a type"); continue
            if depth == 0:
                if t[1].lower() in models: probs.append(f"defines .model '{t[1]}' twice; ngspice keeps the first")
                models[t[1].lower()] = s
        elif w in (".param", ".func"):
            if depth == 0: probs.append(f"'{w}' outside a .subckt body would be global to every circuit; move it "
                                        "into the body that uses it")
        elif w.startswith("."):
            probs.append(f"directive '{t[0]}' is not allowed in definition text (allowed: .subckt, .ends, .model, and "
                         ".param / .func inside a body); packages are untrusted input")
        elif depth == 0:
            probs.append(f"element line '{s[:40]}' is outside a .subckt body")
        elif w[0] == "x":
            pos = [x for x in t[1:] if "=" not in x]
            pos = pos[:[x.lower() for x in pos].index("params:")] if "params:" in (x.lower() for x in pos) else pos
            if pos: xrefs.append((t[0], pos[-1].lower(), len(pos) - 1))
    if depth: probs.append(f"{depth} '.subckt' without '.ends' (ngspice aborts the circuit)")
    return Unit(label, sha, tuple(lines), subckts, models, tuple(xrefs)), probs


def probe_decks(section, pins, params, units):
    """The decks the static validator reads for one part, one per `line` branch (without the title): the instance
    with default params, each pin on its own net with a 1 GΩ leak to ground (so the part's own text is judged, not
    a circuit around it), the rendered `models`, then the definition text."""
    line = section["line"]
    sel = line["select"] if isinstance(line, dict) else None
    nets = {p.id: f"pin_{p.id.lower()}" for p in pins}
    leaks = [f"Rbench_leak{i} {n} 0 1g" for i, n in enumerate(nets.values(), 1)]
    decks = []
    for choice in next((p.choices for p in params if p.key == sel), (None,)):
        texts = {p.key: p.default for p in params} | ({sel: choice} if sel else {})
        lines, models = emit(section, "U1", nets, {p.key: p.parse(texts[p.key]) for p in params if p.applies(texts)})
        decks.append([*lines, *leaks, *models, *(s for u in units for s in u.lines)])
    return decks


def validate_bodies(units, section, pins, params):
    """Static validation of the definition text a part uses, by benchcore's validator (the one every run deck goes
    through) on probe_decks(). -> (status, errors, warnings, limits): errors and warnings are [(message, field)];
    limits names exactly what was not analysed. status: "failed" (errors: they reject the package as DEF-SIM),
    "limited" (no error, some construct left to ngspice), "full" (every element parsed, every reference resolved,
    DC topology analysed; never a proof that the circuit is electrically well-posed)."""
    import benchcore     # lazy: the rest of this module is pure Python
    errors, warnings, limits = {}, {}, {}
    for deck in probe_decks(section, pins, params, units):
        for f in benchcore.validate(["bench probe", *deck, ".tran 1u 10u uic", ".end"]):
            key = (f"{f['code']}: {f['message']}", "line" if not f["scope"] else "body" if "body" in section else "defs")
            if f["severity"] == "error": errors[key] = None          # dicts: ordered, one entry per message
            elif f["class"] == "limited": limits[key] = None
            else: warnings[key] = None
    status = "failed" if errors else "limited" if limits else "full"
    return status, list(errors), list(warnings), [m for m, _ in limits]


def status_note(section):
    """The line a part's limitations get from its body-validation status; None when there is nothing to add."""
    if section.get("_status") == "limited":
        return ("Static validation is limited; Bench's validator left these to ngspice: " +
                "; ".join(section["_limits"]))


def _read(path, cache):
    """(text, sha256) of a definition file, read once per catalog load; ValueError if unreadable."""
    key = ("file", path)
    if key not in cache:
        try: data = path.read_bytes()
        except OSError as e: raise ValueError(f"cannot read it ({e.strerror})") from None
        try: cache[key] = (data.decode("utf-8"), hashlib.sha256(data).hexdigest())
        except UnicodeDecodeError: raise ValueError("is not UTF-8 text") from None
    return cache[key]


def manifest(root, cache):
    """{path: record} of <root>/_models/MANIFEST.toml (read once per catalog load), or a str saying why not."""
    key = ("manifest", root)
    if key not in cache:
        f = root / MODELS_DIR / "MANIFEST.toml"
        try:
            with open(f, "rb") as fh: recs = tomllib.load(fh).get("file", [])
            cache[key] = {r["path"]: r for r in recs if isinstance(r, dict) and isinstance(r.get("path"), str)}
            paths = [r["path"] for r in recs if isinstance(r, dict) and isinstance(r.get("path"), str)]
            dups = sorted({p for p in paths if paths.count(p) > 1})
            if dups:        # the last record would silently win, so a stale one could hide behind it
                cache[key] = f"{f} has more than one [[file]] record for {', '.join(dups)}"
        except (OSError, tomllib.TOMLDecodeError, AttributeError) as e:
            cache[key] = f"{f} is missing or unreadable ({e})"
    return cache[key]


def manifest_problems(rec, sha):
    """Why a MANIFEST.toml record is not a complete provenance record for a file with this sha256; [] when it is."""
    out = [f"unknown key '{k}'" for k in rec if k not in MANIFEST_KEYS]
    if rec.get("origin") not in ("project", "third-party"): out.append("origin must be \"project\" or \"third-party\"")
    out += [f"'{k}' must be a non-empty string" for k in ("source", "license", "sha256")
            if not (isinstance(rec.get(k), str) and rec[k].strip())]
    if rec.get("origin") == "third-party" and not rec.get("url"): out.append("a third-party file needs 'url'")
    if isinstance(rec.get("sha256"), str) and rec["sha256"] and rec["sha256"] != sha:
        out.append(f"sha256 does not match the file (it is {sha}): update the record after checking the change")
    return out


def _units(section, where, bad):
    """Resolve and parse `defs` then `body` -> [Unit] (a unit that cannot be read is reported and skipped)."""
    where = where or {}
    pkg, root, cache = where.get("dir"), where.get("root"), where.setdefault("cache", {})
    units, refs = [], section.get("defs", [])
    if not (isinstance(refs, list) and all(isinstance(r, str) for r in refs)):
        bad("must be a list of file names: package-relative, or models:<path> under the library's _models/", "defs")
        refs = []
    for i, ref in enumerate(refs):
        fld = f"defs[{i}]"
        shared = ref.startswith("models:")
        rel = ref[len("models:"):] if shared else ref
        if shared and root is None:
            bad(f"{ref!r}: models: files resolve under a library root's {MODELS_DIR}/, and this package was loaded "
                "without one (load it through the catalog)", fld); continue
        base = (Path(root) / MODELS_DIR if shared else Path(pkg or ".")).resolve()
        p = (base / rel).resolve()
        if not rel or Path(rel).is_absolute() or not p.is_relative_to(base):
            bad(f"{ref!r} must stay inside {'the ' + MODELS_DIR + ' directory' if shared else 'the package directory'}; "
                "use a relative path", fld); continue
        if not p.is_file():
            bad(f"{ref!r} not found (looked for {p})", fld); continue
        try: text, sha = _read(p, cache)
        except ValueError as e:
            bad(f"{ref!r} {e}", fld); continue
        if shared:
            recs = manifest(Path(root), cache)
            rel_key = p.relative_to(base).as_posix()
            if isinstance(recs, str): bad(f"{ref!r} needs a provenance record: {recs}", fld)
            elif rel_key not in recs: bad(f"{ref!r} has no [[file]] record with path = {rel_key!r} in "
                                          f"{MODELS_DIR}/MANIFEST.toml; add one (origin, source, license, sha256)", fld)
            else:
                for why in manifest_problems(recs[rel_key], sha): bad(f"{MODELS_DIR}/MANIFEST.toml record for {rel_key!r}: {why}", fld)
            label = f"{MODELS_DIR}/{rel_key}"
        else:
            label = (p.relative_to(Path(root).resolve()) if root and p.is_relative_to(Path(root).resolve()) else p.name)
            label = Path(label).as_posix()
        unit, probs = parse_def(text, label, sha)
        for why in probs: bad(f"{ref}: {why}", fld)
        units.append(unit)
    if "body" in section:
        body = section["body"]
        if not isinstance(body, str): bad("must be a string holding .subckt ... .ends text", "body")
        else:
            d = Path(pkg).resolve() if pkg else None
            here = (d.relative_to(Path(root).resolve()).as_posix() if d and root and d.is_relative_to(Path(root).resolve())
                    else d.name if d else "package")
            unit, probs = parse_def(body, f"{here}/component.toml#body", hashlib.sha256(body.encode()).hexdigest())
            for why in probs: bad(why, "body")
            units.append(unit)
    seen = {}               # (kind, name) -> label: one unit per name within a part
    for i, u in enumerate(units):
        if u.sha in (v.sha for v in units[:i]): bad(f"{u.label} is listed twice", "defs"); continue
        for kind, names in (("subckt", u.subckts), ("model", u.models)):
            for n in names:
                if (kind, n) in seen: bad(f".{kind} '{n}' is defined by both {seen[kind, n]} and {u.label}; ngspice "
                                          "keeps only the first", "defs")
                seen[kind, n] = u.label
        for inst, name, nodes in u.xrefs:      # nested deps: defined in this unit or one listed before it
            ports = next((x.subckts[name][0] for x in units[:i + 1] if name in x.subckts), None)
            if ports is None and not _local_subckt(u, name):
                bad(f"{u.label}: {inst} instantiates .subckt '{name}', which neither this text nor an earlier 'defs' "
                    "entry defines; list the file that defines it before this one", "defs")
            elif ports is not None and len(ports) != nodes:
                bad(f"{u.label}: {inst} gives {nodes} nodes but .subckt '{name}' has {len(ports)} ports", "defs")
    return units


def _local_subckt(unit, name):
    """True if `name` is a .subckt nested inside one of the unit's bodies (local to it in ngspice)."""
    return any(re.match(rf"\.subckt\s+{re.escape(name)}(\s|$)", s, re.I) for s in unit.lines)


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


def check(section, pins, params, where=None):
    """DEF-SIM diagnostics for a [sim.electrical] section ({code, severity, message, field}); [] when valid.
    where: {"dir": package dir, "root": library root or None, "cache": dict shared by one catalog load}; `defs` paths
    resolve against it, never the working directory. A valid section with definition text gets the parsed units in
    section["_units"] (read by build_netlist), its body-validation status in section["_status"] and what was not
    analysed in section["_limits"] (validate_bodies)."""
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
        for k in ("device", "models", "current", "defs", "body"):
            if k in section: bad("a ground section has no device, line, models, current, defs or body", k)
        if len(pids) != 1: bad(f"a ground part has exactly one pin (this one has {len(pids)})", "ground")
        return out
    if "line" not in section: bad("needs 'line' (or 'ground = true' for a reference node)", "line")
    dev = section.get("device")
    spec = DEVICES.get(dev) if isinstance(dev, str) else None
    if spec is None:
        bad(f"device must be one of {', '.join(DEVICES)}; got {dev!r}", "device")
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
    letter = section.get("device")
    units = _units(section, where, bad) if "defs" in section or "body" in section else []
    if letter == "X" and "defs" not in section and "body" not in section:
        bad("an X part needs the .subckt it instantiates: add 'defs' or 'body'", "line")
    cards = {}                     # model name -> (type, level or None) of the section's own .model lines
    for m in section.get("models", []) if isinstance(section.get("models"), list) else []:
        if isinstance(m, str) and len(m.split()) > 2:
            cards[m.split()[1].lower()] = model_type(m)
    for u in units:                # ... and of the top-level cards in its definition text
        for n, card in u.models.items():
            if n in cards: bad(f"model '{n}' is in both 'models' and {u.label}; ngspice keeps only the first", "models")
            cards[n] = model_type(card)
    subckts = {n: s for u in units for n, s in u.subckts.items()}
    if spec and len(pids) not in spec.pins:
        want = " or ".join(map(str, spec.pins)) if len(spec.pins) < 5 else f"{spec.pins[0]} or more"
        bad(f"a {letter} device has {want} terminals; this part has {len(pids)} pins", "line")
    for lab, (tpl, ok) in branches.items():
        field = "line" if lab is None else f"line.{lab}"
        template(tpl, field, {"dev", "dev_lc", *pids, *ok}, "{dev} ")
        if not isinstance(tpl, str) or not tpl.startswith("{dev} "): continue
        if ";" in tpl or "$" in tpl: bad("must not contain ';' or '$' (SPICE comments would hide what follows)", field)
        n = len(pids)
        toks = tpl.split(None, n + 1)
        nodes, tail = toks[1:n + 1], (toks + [""])[n + 1]
        try: tail_names = _names(tail)
        except ValueError: continue    # already reported by template()
        if sorted(nodes) != sorted(f"{{{p}}}" for p in pids) or tail_names & set(pids):
            bad("the node fields must be exactly the pin placeholders, once each, right after {dev} "
                f"(here: {' '.join('{' + p + '}' for p in pids)}); a literal node or a pin elsewhere would connect "
                "to nets that are not drawn", field)
        elif spec and spec.models:
            name = (tail.split() or [""])[0].lower()
            if name not in cards:
                # ngspice reads extra nodes (Q substrate, M SOI) until a token names a model
                bad("the field after the node fields must be a model named in 'models'; anything else is read as "
                    "another node and connects to a net that is not drawn", field)
            else:
                for msg in _model_problems(letter, spec, name, *cards[name], len(pids)): bad(msg, field)
        elif letter == "X":
            for msg in _instance_problems(tail, subckts, n): bad(msg, field)
    models = section.get("models", [])
    if not isinstance(models, list): bad("must be a list of .model lines", "models")
    else:
        for i, m in enumerate(models):
            template(m, f"models[{i}]", {"dev", "dev_lc", *(p.key for p in params if not p.when)}, ".model ")
            if isinstance(m, str) and m.startswith(".model ") and len(m.split()) < 3:
                bad("needs a model name and a type: '.model <name> <TYPE>(...)'", f"models[{i}]")
    cur = section.get("current")
    if "current" in section and spec:     # exact per-letter allowlist: an unverified `.save @dev[x]` can crash ngspice
        if spec.current is None:
            bad(f"a {letter} device has no current Bench can read; remove 'current'", "current")
        elif cur != spec.current:
            bad(f"a {letter} device's current must be '{spec.current}' (the current into its first node)", "current")
    if "ac" in section:   # AC-analysis stimulus (G17): appended to the line in AC decks only, so transient decks never change
        tpl = section["ac"]
        if letter not in ("V", "I"): bad("only an independent V or I source has an AC stimulus", "ac")
        elif template(tpl, "ac", {p.key for p in params if not p.when}, "AC ") and (";" in tpl or "$" in tpl):
            bad("must not contain ';' or '$' (SPICE comments would hide what follows)", "ac")
    kind = section.get("model_kind")
    if "model_kind" in section and kind not in MODEL_KINDS: bad(f"must be one of {', '.join(MODEL_KINDS)}", "model_kind")
    elif letter == "X" and kind not in X_KINDS:
        bad(f"an X part must say what backs it: model_kind = one of {', '.join(X_KINDS)}", "model_kind")
    if "verification" in section and section["verification"] not in VERIFICATION:
        bad(f"must be one of {', '.join(VERIFICATION)}", "verification")
    prov = section.get("provenance")
    if "provenance" in section:
        if not isinstance(prov, dict) or not all(isinstance(v, str) for v in prov.values()):
            bad("must be a table of strings {source, url, license, ...}", "provenance"); prov = {}
        for k in sorted(set(prov) - set(PROVENANCE_KEYS)): bad(f"unknown key '{k}'; allowed: {', '.join(PROVENANCE_KEYS)}", f"provenance.{k}")
    if isinstance(kind, str) and kind.startswith("third-party") and not (prov or {}).get("license"):
        local = [r for r in section.get("defs", []) if isinstance(r, str) and not r.startswith("models:")]
        if local or "body" in section or "models" in section:   # models: files carry their own MANIFEST record
            bad("third-party model text needs [sim.electrical.provenance] with source, url and license", "provenance")
    if units and not out:
        status, errors, warnings, limits = validate_bodies(units, section, pins, params)
        for msg, fld in errors: bad(msg, fld)
        out += [{"code": "DEF-SIM", "severity": "warning", "message": msg, "field": f"sim.electrical.{fld}"}
                for msg, fld in warnings]
        if not errors: section["_units"], section["_status"], section["_limits"] = tuple(units), status, tuple(limits)
    return out


def _instance_problems(tail, subckts, n):
    """Why `<subckt> NAME=value ...` (the X line after its node fields) does not match a .subckt of the part."""
    toks = tail.split()
    name = toks[0] if toks else ""
    if name.lower() not in subckts:
        return [f"the field after the node fields must name a .subckt defined in 'defs' or 'body' (literally, no "
                f"placeholder); got {name!r}, defined: {', '.join(sorted(subckts)) or 'none'}"]
    ports, declared = subckts[name.lower()]
    out = [] if len(ports) == n else [f".subckt {name} has {len(ports)} ports ({' '.join(ports)}) but the line has {n} "
                                      "node fields; node field i connects to port i"]
    for tok in toks[1:]:
        k, eq, v = tok.partition("=")
        if not (eq and IDENT.match(k) and v):
            out.append(f"'{tok}' after the subckt name must be NAME=value (no spaces, no 'params:')")
        elif k.lower() not in declared:
            out.append(f".subckt {name} declares no parameter '{k}' (it has: {', '.join(declared) or 'none'}); "
                       "ngspice would ignore it silently")
    return out


def model_type(text):
    """(type, level or None) of a `.model <name> <TYPE>(... level=N ...)` line, lower case; level None when absent
    (ngspice's default 1) or not a number (a {placeholder})."""
    w = " ".join(text.split()[2:]).lower().replace("(", " ").replace(")", " ").replace("=", " ").split()
    lv = w[w.index("level") + 1] if "level" in w[:-1] else None
    try: lv = None if lv is None else int(float(lv))
    except ValueError: lv = "?"
    return (w[0] if w else ""), lv


def _model_problems(letter, spec, name, ty, lv, npins):
    """Why model `name` (type ty, level lv) cannot back a `letter` part with npins pins; [] when it can."""
    if ty not in spec.models:
        return [f"model '{name}' is type {ty.upper() or '?'}; a {letter} device needs "
                f"{' or '.join(t.upper() for t in spec.models)}"]
    levels = spec.models[ty]
    if lv == "?":
        return [f"model '{name}' has a level that is not a number; write the level literally (it decides the model)"]
    if levels is not None and (lv or 1) not in levels:
        return [f"model '{name}' is {ty.upper()} level {lv}, which ngspice 47 does not have "
                f"(levels: {', '.join(map(str, levels))})"]
    if letter == "M" and (npins == 3) != (ty == "vdmos"):
        return [f"a M device has 4 terminals; this part has 3 pins (3 only with a VDMOS model, d g s)" if npins == 3 else
                f"a VDMOS takes 3 pins (d g s); this part has {npins}"]
    return []


def model_key(text):
    """(lower-case name, normalised body) of a `.model <name> <body>` line. ngspice model names are global to the deck
    and case-insensitive; two lines with one name and different bodies cannot both be in force."""
    t = text.split()
    return t[1].lower(), " ".join(t[2:]).lower()


def fixed_models(section):
    """Deck-global names this section defines -> fingerprint; two parts may share a name only with one fingerprint
    (ngspice keeps the first definition, silently for .model). Keys: a model name (`models` lines whose name has no
    placeholder: fingerprint = the card body; top-level cards of definition text: the unit), ("subckt", name)."""
    out = {}
    for m in section.get("models", ()):
        k, body = model_key(m)
        if "{" not in k: out[k] = body
    for u in section.get("_units", ()):
        out.update({k: ("unit", u.sha) for k in u.models})
        out.update({("subckt", k): ("unit", u.sha) for k in u.subckts})
    return out


def device_name(section, part_name):
    """SPICE device name: the letter decides the device type (SW1 -> RSW1), so it is prefixed unless present."""
    letter = section["device"]
    return part_name if part_name.upper().startswith(letter) else letter + part_name


def _r(v):   # numbers reach the core as repr(float); choices as text
    return v if isinstance(v, str) else repr(float(v))


def _line(section, values):
    line = section["line"]
    return line[values[line["select"]]] if isinstance(line, dict) else line


def emit(section, part_name, nodes, values, ac=False):
    """-> (lines, models). nodes: {pin id: net}; values: parsed applicable params {key: float | choice}. ac: an AC
    deck, whose source lines carry the section's `ac` stimulus."""
    dev = device_name(section, part_name)
    kw = {**{k: _r(v) for k, v in values.items()}, **nodes, "dev": dev, "dev_lc": dev.lower()}
    line = _line(section, values) + (" " + section["ac"] if ac and "ac" in section else "")
    return [line.format(**kw)], [m.format(**kw) for m in section.get("models", [])]


_VALUE_LINE = re.compile(r"\{dev\}\s+\S+\s+\S+\s+(?:dc\s+)?\{(\w+)\}\Z", re.I)
_BODY_VALUE = re.compile(r"([rvi]\w*)\s+(\S+)\s+\S+\s+(?:dc\s+)?\{([^{}]*)\}\Z", re.I)
_OPS = {ast.Add: float.__add__, ast.Sub: float.__sub__, ast.Mult: float.__mul__, ast.Div: float.__truediv__}


def _arith(expr, env):
    """Value of a plain body expression (numbers, params, + - * / and parentheses), or None for anything else."""
    def ev(n):
        if isinstance(n, ast.Constant) and type(n.value) in (int, float): return float(n.value)
        if isinstance(n, ast.Name): return env[n.id.lower()]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub): return -ev(n.operand)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.left), ev(n.right))
        raise ValueError
    try: return ev(ast.parse(expr.strip(), mode="eval").body)
    except (SyntaxError, KeyError, ValueError, ZeroDivisionError): return None


def _subckt_lines(units, name):
    """Logical lines inside .subckt `name` (header and .ends excluded) as (depth, text); nested bodies at depth 2+."""
    for u in units:
        logical = []
        for s in u.lines:
            if s.startswith("+") and logical: logical[-1] += " " + s[1:].strip()
            else: logical.append(s)
        out, depth = [], 0
        for s in logical:
            t = s.split()
            w = t[0].lower()
            if depth == 0:
                depth = int(w == ".subckt" and len(t) > 1 and t[1].lower() == name)
                continue
            if w == ".subckt": depth += 1
            if w == ".ends":
                depth -= 1
                if depth == 0: return out
                continue
            out.append((depth, s))
    return []


def live_devices(section, part_name, values):
    """What a live run can change for one part, from package data alone (docs/plans/ngspice-library/LIVE.md).
    -> ({ngspice device name: value}, {param key: stimulus?}). A live change is an ngspice `alter` of an R
    (resistance) or a V/I source (DC value):
    - top-level R/V/I parts: the value of a `{dev} n1 n2 [DC] {key}` line; a choice that selects between such lines
      (a switch's state) is live too;
    - X parts: each R/V/I line of the .subckt whose value is one {expression} over the instance params; a body
      param is live when no other body line mentions it, and its key when the instance line passes it once as
      NAME={key} and no `models` line uses it. stimulus: it reaches a `VSTIM stim ...` source (sensor convention).
    Everything else (B/E/G values, models, waveforms, C and L) is fixed for the run."""
    letter, dev = section["device"], device_name(section, part_name).lower()
    line, cur = section["line"], _line(section, values)
    if letter in "RVI":
        m = _VALUE_LINE.match(cur.strip())
        if not m or not isinstance(values.get(m[1]), float): return {}, {}
        keys = {m[1]: False}
        if isinstance(line, dict) and all(_VALUE_LINE.match(t.strip()) for k, t in line.items() if k != "select"):
            keys[line["select"]] = False
        return {dev: values[m[1]]}, keys
    if letter != "X": return {}, {}
    devices, keys = _x_live(section, dev, cur, values)
    if isinstance(line, dict):     # a choice between instance lines that differ only in which keys they pass
        tpls = {k: t for k, t in line.items() if k != "select"}
        if len({re.sub(r"=\{\w+\}", "=*", t) for t in tpls.values()}) == 1 and all(
                set(re.findall(r"=\{(\w+)\}", t)) <= set(_x_live(section, dev, t, values)[1]) for t in tpls.values()):
            keys[line["select"]] = False
    return devices, keys


def _x_live(section, dev, cur, values):
    """live_devices for one X instance line template `cur`."""
    toks = cur.split()
    name = next((t.lower() for t in toks[1:] if not t.startswith("{") and "=" not in t), None)
    lines = _subckt_lines(section.get("_units", ()), name)
    models = " ".join(section.get("models", ()))
    inst = {m[1].lower(): m[2] for m in (re.fullmatch(r"(\w+)=\{(\w+)\}", t) for t in toks) if m
            if isinstance(values.get(m[2]), float) and cur.count("{" + m[2] + "}") == 1 and "{" + m[2] + "}" not in models}
    env = {p: values[k] for p, k in inst.items()}
    alter = {}                     # line index -> (device, expression, is the stimulus source)
    for i, (depth, s) in enumerate(lines):
        m = depth == 1 and _BODY_VALUE.match(s)
        if m and _arith(m[3], env) is not None:
            alter[i] = (f"{m[1][0].lower()}.{dev}.{m[1].lower()}", m[3], m[1].lower() == "vstim" and m[2].lower() == "stim")
    devices, keys = {}, {}
    for p, k in inst.items():
        uses = [i for i, (_, s) in enumerate(lines) if re.search(rf"(?<![\w.]){re.escape(p)}(?![\w(])", s, re.I)]
        if uses and all(i in alter for i in uses):
            keys[k] = any(alter[i][2] for i in uses)
            devices.update({alter[i][0]: _arith(alter[i][1], env) for i in uses})
    return devices, keys


def build_netlist(parts, nets, defs, ac=False):
    """parts: {name: {"kind", "params", ...}} in creation order; nets: {"Part.pin": net}; defs: a Catalog; ac: an AC
    deck (sources emit their `ac` stimulus).
    -> {"lines", "models", "saves", "observables", "derived", "signals"}; raises ModelError on a bad value or a
    part without an electrical binding (never skips one silently).

    observables: ngspice vector names to read from the core. derived: {'v(R1)': (netA, netB)} across-part
    voltages that sim.py computes by subtracting two core node series. signals: readable names for all of them,
    [{"name", "vector", "quantity", "unit", "kind": "node"|"across"|"current", "part", "pins"}]."""
    if not parts:
        raise ModelError("circuit is empty")
    lines, models, cur, derived, signals = [], [], [], {}, []
    used, owner = set(), {}      # owner: global name (fixed_models keys) -> (fingerprint, kind of the first part)
    units = set()                # sha256 of definition units already in the deck
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
        ls, ms = emit(sec, name, nodes, c.values(d["params"], name), ac)
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
        for u in sec.get("_units", ()):    # each unit once per deck, in first-use order: a part lists its deps first
            if u.sha in units: continue
            for key, fp in fixed_models({"_units": (u,)}).items():
                if owner.setdefault(key, (fp, d["kind"]))[0] != fp:
                    what = f".subckt '{key[1]}'" if isinstance(key, tuple) else f"model '{key}'"
                    raise ModelError(f"{what} is defined differently by {owner[key][1]} and {d['kind']}")
            units.add(u.sha)
            models += [f"* bench-def {u.label} {u.sha[:8]}", *u.lines]
        if len(pins) == 2:
            derived[f"v({name})"] = tuple(nodes[t] for t in pins)
            signals.append({"name": f"V({name})", "vector": f"v({name})", "quantity": "voltage", "unit": "V",
                            "kind": "across", "part": name, "pins": [f"{name}.{t}" for t in pins]})
        if "current" in sec:
            vec = sec["current"].format(dev_lc=dev.lower())
            cur.append(vec)
            signals.append({"name": f"I({name})", "vector": vec, "quantity": "current", "unit": "A", "kind": "current",
                            "part": name, "pins": [f"{name}.{_line(sec, c.values(d['params'], name)).split()[1][1:-1]}"]})
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
