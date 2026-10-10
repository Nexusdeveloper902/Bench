"""Component catalog: discovers `component.toml` packages, validates them, and answers lookups/search. Pure Python.

A package is a directory holding component.toml (+ optional SVG/doc files). Built-in root: app/library/. Extra roots:
env SIM_COMPONENT_PATH (os.pathsep separated). The catalog never parses SPICE: a simulation domain registers its
validator in SIM_DOMAINS and owns its `[sim.<domain>]` section (see app.electrical). Format: docs/milestone/02-plan.md."""
import math
import os
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from . import electrical
from .components import ModelError, Param, format_si, parse_si, rotate

BUILTIN_ROOT = Path(__file__).parent / "library"
TAXONOMY = ("Electrical", "Microcontrollers", "Sensors", "Displays", "Communication", "Audio", "Power")
DOMAINS = ("electrical", "digital", "mcu", "audio", "rf", "optical", "mechanical")
CAPABILITIES = ("interface",)                    # plus "sim:<d>" for d in SIM_DOMAINS
SIM_DOMAINS = {"electrical": electrical.check}   # the one place a simulation domain plugs in its validator
UNITS = ("", "Ω", "F", "H", "V", "A", "s", "Hz")
DOC_EXTENSIONS = (".pdf", ".html", ".htm", ".md", ".txt", ".png", ".svg")   # local docs files the app will open

WEB_URL = ("http://", "https://")                 # the only URL schemes a docs entry may use
SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")   # file:, javascript:, ftp: ... : never opened
ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
PIN_RE = re.compile(r"[A-Za-z0-9_]+\Z")
CHOICE_RE = re.compile(r"[A-Za-z0-9_.+\-]+\Z")
DESIGNATOR_RE = re.compile(r"[A-Z]+\Z")
TOP = ("format", "id", "version", "name", "description", "designator", "category", "subcategory", "aliases", "keywords",
       "tags", "domain", "capabilities", "limitations", "docs", "pins", "params", "presentation", "sim")
PIN_KEYS = ("id", "name", "at", "description")
PARAM_KEYS = ("key", "label", "unit", "default", "kind", "choices", "min", "min_exclusive", "max", "when", "help")
PRES_KEYS = ("box", "symbol", "icon", "value", "indicator", "show_current", "animate_when")


@dataclass(frozen=True)
class Pin:
    id: str
    name: str
    at: tuple
    description: str = ""


@dataclass(frozen=True)
class Definition:
    id: str
    version: int
    name: str
    category: str
    domain: str
    capabilities: tuple
    pins: tuple
    box: tuple
    description: str = ""
    designator: str = "U"
    subcategory: str = ""
    aliases: tuple = ()
    keywords: tuple = ()
    tags: tuple = ()
    limitations: tuple = ()
    docs: tuple = ()
    params: tuple = ()
    symbol: object = None           # None | Path | {"select": param key, "files": {choice: Path | None}}
    icon: Path = None
    value_param: str = None         # param shown as the part's value label on the canvas
    indicator: str = None           # "glow": lit by the part's simulated current
    show_current: bool = False      # print the simulated current next to the part
    animate_when: tuple = ()        # ((param, choice), ...): results of such a run autoplay
    sim: dict = field(default_factory=dict)   # domain -> opaque section, checked by SIM_DOMAINS[domain]
    path: Path = None               # package directory

    @property
    def terminals(self):
        return tuple(p.id for p in self.pins)

    @property
    def pos(self):
        return {p.id: p.at for p in self.pins}

    @property
    def ground(self):
        return bool(self.sim.get("electrical", {}).get("ground"))

    @property
    def simulated(self):
        return "sim:electrical" in self.capabilities

    @property
    def readiness(self):
        return "simulated" if self.simulated else "interface" if "interface" in self.capabilities else "placeable"

    def param(self, key):
        return next(p for p in self.params if p.key == key)

    def defaults(self):
        return {p.key: p.default for p in self.params}

    def fields(self, params):
        """Param keys that apply to these values (e.g. PULSE fields only in PULSE mode), in display order."""
        return [p.key for p in self.params if p.applies(params)]

    def values(self, params, owner=""):
        """Parse every applicable param. Raises ModelError on the first bad one."""
        return {k: self.param(k).parse(params[k], owner) for k in self.fields(params)}

    def symbol_for(self, params):
        s = self.symbol
        return s["files"].get(params.get(s["select"])) if isinstance(s, dict) else s


# ---------------------------------------------------------------- loading
MAX_GEOM = 1e4   # definition geometry (box, pin at), canvas units: beyond this the canvas overflows (F9)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _geom(v):
    return _num(v) and abs(v) <= MAX_GEOM


def _svg_problem(path):
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as e:
        return f"is not well-formed XML ({e})"
    if root.tag.rpartition("}")[2] != "svg":
        return "root element is not <svg>"
    vb = re.split(r"[,\s]+", root.get("viewBox", "").strip())
    try:
        if len(vb) == 4 and all(math.isfinite(float(x)) for x in vb): return None
    except ValueError:
        pass
    return "has no viewBox with 4 numbers"


def load_definition(path):
    """Read and validate one component.toml -> (Definition | None, [diagnostic]). Any error diagnostic rejects it;
    warnings (DEF-ASSET) keep it with a fallback."""
    path = Path(path)
    out, ident = [], [None]

    def bad(code, msg, fld, sev="error"):
        out.append({"code": code, "severity": sev, "message": msg, "file": str(path), "field": fld, "id": ident[0]})

    try:
        with open(path, "rb") as f: raw = tomllib.load(f)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        bad("DEF-PARSE", f"cannot read the definition: {e}; fix the TOML syntax", "")
        return None, out

    def unknown(table, allowed, where):
        for k in table:
            if k not in allowed:
                hint = ("; top-level keys must come before the first [table]" if k in TOP and where else
                        f"; allowed: {', '.join(allowed)}")
                bad("DEF-FIELD", f"unknown key '{k}'{f' in [{where}]' if where else ''}{hint}",
                    f"{where}.{k}" if where else k)

    def typed(table, key, typ, fld, what, required=False, default=None):
        """Value of table[key] if it has type `typ` (bools never count as numbers), else `default` + diagnostic."""
        if key not in table:
            if required: bad("DEF-FIELD", f"missing required key '{key}'; add it ({what})", fld)
            return default
        v = table[key]
        if not isinstance(v, typ) or (isinstance(v, bool) and bool not in (typ if isinstance(typ, tuple) else (typ,))):
            bad("DEF-FIELD", f"'{key}' must be {what}; got {v!r}", fld)
            return default
        return v

    def strs(table, key, fld):
        v = typed(table, key, list, fld, "a list of strings", default=[])
        if not all(isinstance(x, str) for x in v):
            bad("DEF-FIELD", f"'{key}' must be a list of strings", fld); return []
        return v

    unknown(raw, TOP, "")
    ver = typed(raw, "format", int, "format", "the integer 1", True)
    if ver is not None and ver != 1: bad("DEF-FIELD", f"unsupported format {ver}; this catalog reads format = 1", "format")
    ident[0] = typed(raw, "id", str, "id", "a string like \"LED\"", True)
    if ident[0] is not None and not ID_RE.match(ident[0]):
        bad("DEF-FIELD", f"bad id {ident[0]!r}; use letters, digits and _ starting with a letter", "id")
    version = typed(raw, "version", int, "version", "an integer >= 1", True)
    if version is not None and version < 1: bad("DEF-FIELD", "version must be >= 1; start at 1", "version")
    name = typed(raw, "name", str, "name", "the display name", True)
    category = typed(raw, "category", str, "category", "e.g. \"Electrical\"", True)
    if category is not None:    # free text; a case-insensitive match of a TAXONOMY entry is that entry
        category = next((t for t in TAXONOMY if t.lower() == category.strip().lower()), category.strip())
        if not category: bad("DEF-FIELD", "category must not be empty; use a TAXONOMY name or a new category name", "category")
    domain = typed(raw, "domain", str, "domain", f"one of {', '.join(DOMAINS)}", True)
    if domain is not None and domain not in DOMAINS: bad("DEF-FIELD", f"unknown domain {domain!r}; use one of {', '.join(DOMAINS)}", "domain")
    description = typed(raw, "description", str, "description", "a string", default="")
    subcategory = typed(raw, "subcategory", str, "subcategory", "a string", default="")
    designator = typed(raw, "designator", str, "designator", "capital letters like \"R\"", default="U")
    if not DESIGNATOR_RE.match(designator): bad("DEF-FIELD", f"bad designator {designator!r}; use capital letters only", "designator"); designator = "U"
    aliases, keywords, tags = (strs(raw, k, k) for k in ("aliases", "keywords", "tags"))
    caps, limits, docs = (strs(raw, k, k) for k in ("capabilities", "limitations", "docs"))
    if "capabilities" not in raw: bad("DEF-FIELD", "missing required key 'capabilities'; add it (may be [])", "capabilities")

    # ---- pins
    pins, seen_at = [], {}
    rp = typed(raw, "pins", list, "pins", "a list of tables (may be [])", True, [])
    for i, t in enumerate(rp):
        f = f"pins[{i}]"
        if not isinstance(t, dict): bad("DEF-FIELD", "must be a table {id, at, ...}", f); continue
        unknown(t, PIN_KEYS, f)
        pid = t.get("id")
        if not isinstance(pid, str) or not PIN_RE.match(pid):
            bad("DEF-PIN", f"bad pin id {pid!r}; use letters, digits and _", f"{f}.id"); continue
        if any(p.id == pid for p in pins): bad("DEF-PIN", f"duplicate pin id '{pid}'; ids must be unique", f"{f}.id"); continue
        at = t.get("at")
        if not (isinstance(at, list) and len(at) == 2 and all(_geom(x) for x in at)):
            bad("DEF-PIN", f"pin '{pid}': 'at' must be two numbers [x, y] within ±{MAX_GEOM:g}", f"{f}.at"); continue
        at = tuple(at)
        if at in seen_at: bad("DEF-PIN", f"pins '{seen_at[at]}' and '{pid}' are both at {list(at)}; move one", f"{f}.at"); continue
        seen_at[at] = pid
        pname, pdesc = typed(t, "name", str, f"{f}.name", "a string", default=pid), typed(t, "description", str, f"{f}.description", "a string", default="")
        pins.append(Pin(pid, pname, at, pdesc))

    # ---- params
    params, rp = [], typed(raw, "params", list, "params", "a list of tables", default=[])
    for i, t in enumerate(rp):
        f = f"params[{i}]"
        if not isinstance(t, dict): bad("DEF-FIELD", "must be a table {key, label, default, ...}", f); continue
        unknown(t, PARAM_KEYS, f)
        key = typed(t, "key", str, f"{f}.key", "a string", True)
        label = typed(t, "label", str, f"{f}.label", "a string", True)
        default = t.get("default")
        if "default" not in t: bad("DEF-FIELD", "missing required key 'default'; add it (a string, as a user would type it)", f"{f}.default")
        elif not isinstance(default, str): bad("DEF-PARAM", f"default must be a string such as \"1k\"; got {default!r}", f"{f}.default"); default = None
        unit = typed(t, "unit", str, f"{f}.unit", "a unit symbol", default="")
        if unit not in UNITS: bad("DEF-PARAM", f"unknown unit {unit!r}; use one of {', '.join(repr(u) for u in UNITS)}", f"{f}.unit"); unit = ""
        kind = typed(t, "kind", str, f"{f}.kind", "\"number\" or \"choice\"", default="number")
        if kind not in ("number", "choice"): bad("DEF-PARAM", f"kind must be \"number\" or \"choice\"; got {kind!r}", f"{f}.kind"); kind = "number"
        choices = strs(t, "choices", f"{f}.choices")
        for c in choices:
            if not CHOICE_RE.match(c): bad("DEF-PARAM", f"bad choice {c!r}; use letters, digits and _.+-", f"{f}.choices")
        if kind == "choice" and not choices: bad("DEF-PARAM", "a choice parameter needs 'choices'", f"{f}.choices")
        if kind == "number" and choices: bad("DEF-PARAM", "'choices' is only for kind = \"choice\"", f"{f}.choices")
        lo, hi = t.get("min"), t.get("max")
        for k, v in (("min", lo), ("max", hi)):
            if v is not None and not _num(v): bad("DEF-PARAM", f"{k} must be a number", f"{f}.{k}")
        excl = typed(t, "min_exclusive", bool, f"{f}.min_exclusive", "true or false", default=False)
        when = typed(t, "when", dict, f"{f}.when", "a table like {mode = \"PULSE\"}", default={})
        if not all(isinstance(v, str) for v in when.values()): bad("DEF-PARAM", "'when' values must be choice strings", f"{f}.when"); when = {}
        hlp = typed(t, "help", str, f"{f}.help", "a string", default="")
        if key is None or label is None or default is None: continue
        if not ID_RE.match(key): bad("DEF-PARAM", f"bad param key {key!r}; use letters, digits and _ starting with a letter", f"{f}.key"); continue
        if key in ("dev", "dev_lc") or any(key == p.id for p in pins) or any(key == q.key for q in params):
            bad("DEF-PARAM", f"param key '{key}' clashes with a pin id, a template name (dev, dev_lc) or another param; rename it", f"{f}.key"); continue
        p = Param(key, label, unit, default, kind, tuple(choices), lo if _num(lo) else None, excl, hi if _num(hi) else None,
                  tuple(when.items()), hlp)
        try: p.parse(default, key)
        except ModelError as e: bad("DEF-PARAM", f"default {default!r} is not valid ({e}); fix the default", f"{f}.default")
        params.append(p)
    pk = {p.key: p for p in params}
    for i, p in enumerate(params):
        for k, v in p.when:
            if k == p.key or k not in pk or pk[k].kind != "choice" or v not in pk[k].choices:
                bad("DEF-PARAM", f"'when' {k} = {v!r} must name another choice parameter and one of its choices", f"params[{i}].when")

    # ---- presentation + assets
    pres = typed(raw, "presentation", dict, "presentation", "a table with box = [x0, y0, w, h]", True, {})
    unknown(pres, PRES_KEYS, "presentation")
    base = path.parent.resolve()

    def asset(rel, fld, svg):
        """Path of a package-relative file, or None (+ DEF-FIELD if it escapes the package, DEF-ASSET if unusable)."""
        if not isinstance(rel, str): bad("DEF-FIELD", "must be a file name (string)", fld); return None
        p = (base / rel).resolve()
        if Path(rel).is_absolute() or not p.is_relative_to(base):
            bad("DEF-FIELD", f"{rel!r} must stay inside the package directory; use a relative file name", fld); return None
        if not p.is_file():
            bad("DEF-ASSET", f"file {rel!r} not found; a fallback is drawn instead", fld, "warning"); return None
        why = _svg_problem(p) if svg else None
        if why: bad("DEF-ASSET", f"{rel!r} {why}; a fallback is drawn instead", fld, "warning"); return None
        return p

    box = pres.get("box")
    if "box" not in pres: bad("DEF-FIELD", "missing required key 'box'; add [x0, y0, w, h]", "presentation.box")
    elif not (isinstance(box, list) and len(box) == 4 and all(_geom(x) for x in box) and box[2] > 0 and box[3] > 0):
        bad("DEF-FIELD", f"'box' must be four numbers [x0, y0, w, h] within ±{MAX_GEOM:g} with w, h > 0", "presentation.box"); box = None
    sym, icon = pres.get("symbol"), None
    if isinstance(sym, dict):
        sel = sym.get("select")
        sp = pk.get(sel)
        if sp is None or sp.kind != "choice": bad("DEF-FIELD", f"symbol.select must name a choice parameter; got {sel!r}", "presentation.symbol.select"); sym = None
        else:
            for k in sorted(set(sym) - {"select", *sp.choices}): bad("DEF-FIELD", f"'{k}' is not a choice of {sel}", f"presentation.symbol.{k}")
            for c in sp.choices:
                if c not in sym: bad("DEF-FIELD", f"symbol has no file for choice '{c}'", f"presentation.symbol.{c}")
            sym = {"select": sel, "files": {c: asset(sym[c], f"presentation.symbol.{c}", True) for c in sp.choices if c in sym}}
    elif sym is not None:
        sym = asset(sym, "presentation.symbol", True)
    if "icon" in pres: icon = asset(pres["icon"], "presentation.icon", True)
    for d in docs:
        if d.startswith(WEB_URL): continue
        if SCHEME_RE.match(d):   # a click hands the target to the desktop: never file:, javascript:, ftp: ...
            bad("DEF-FIELD", f"docs entry {d!r} uses a URL scheme that is not allowed; use an http(s) URL or a file "
                             "inside the package", "docs"); continue
        if Path(d).suffix.lower() not in DOC_EXTENSIONS:   # a click opens it with the desktop: never an executable type
            bad("DEF-FIELD", f"docs file {d!r} has a type that is not allowed; use an http(s) URL or a file ending in "
                             f"{', '.join(DOC_EXTENSIONS)}", "docs")
        else:
            asset(d, "docs", False)
    value = typed(pres, "value", str, "presentation.value", "a param key", default=None)
    if value is not None and value not in pk: bad("DEF-FIELD", f"value names unknown param {value!r}", "presentation.value"); value = None
    ind = typed(pres, "indicator", str, "presentation.indicator", "\"glow\"", default=None)
    if ind is not None and ind != "glow": bad("DEF-FIELD", f"unknown indicator {ind!r}; only \"glow\" exists", "presentation.indicator"); ind = None
    show = typed(pres, "show_current", bool, "presentation.show_current", "true or false", default=False)
    aw = typed(pres, "animate_when", list, "presentation.animate_when", "a list of [param, choice] pairs", default=[])
    anim = []
    for pr in aw:
        if isinstance(pr, list) and len(pr) == 2 and pr[0] in pk and pk[pr[0]].kind == "choice" and pr[1] in pk[pr[0]].choices:
            anim.append((pr[0], pr[1]))
        else: bad("DEF-FIELD", f"animate_when entry {pr!r} must be [choice param, one of its choices]", "presentation.animate_when")

    # ---- capabilities and simulation sections
    sim = typed(raw, "sim", dict, "sim", "a table of [sim.<domain>] sections", default={})
    if not all(isinstance(v, dict) for v in sim.values()): bad("DEF-FIELD", "'sim' may only hold [sim.<domain>] tables", "sim"); sim = {}
    iface = "interface" in caps
    if iface != bool(pins): bad("DEF-CAPABILITY", ("'interface' is required when the part has pins; add it" if pins else
                                "'interface' declares a pin interface but the part has no pins; remove it"), "capabilities")
    for c in caps:
        if c == "interface": continue
        d = c.partition(":")[2] if c.startswith("sim:") else None
        if d is None: bad("DEF-CAPABILITY", f"unknown capability {c!r}; allowed: interface, sim:<domain>", "capabilities")
        elif d not in SIM_DOMAINS: bad("DEF-CAPABILITY", f"{c!r}: no '{d}' simulation domain exists; describe it under limitations", "capabilities")
        elif d not in sim: bad("DEF-CAPABILITY", f"{c!r} needs a [sim.{d}] section; add it", "capabilities")
    for d in sim:
        if f"sim:{d}" not in caps: bad("DEF-CAPABILITY", f"[sim.{d}] needs the capability 'sim:{d}' (or remove the section)", f"sim.{d}")
        elif d in SIM_DOMAINS:
            for x in SIM_DOMAINS[d](sim[d], tuple(pins), tuple(params)):
                out.append({**x, "file": str(path), "id": ident[0]})
    if not any(c.startswith("sim:") for c in caps) and not limits:
        bad("DEF-CAPABILITY", "a part without a sim:* capability needs at least one limitations entry saying what is not modelled", "limitations")
    for k in ("indicator", "show_current"):
        if (ind if k == "indicator" else show) and "current" not in sim.get("electrical", {}):
            bad("DEF-FIELD", f"{k} needs sim.electrical.current (it displays the simulated current)", f"presentation.{k}")

    if any(x["severity"] == "error" for x in out):
        return None, out
    return Definition(ident[0], version, name, category, domain, tuple(caps), tuple(pins), tuple(box), description,
                      designator, subcategory, tuple(aliases), tuple(keywords), tuple(tags), tuple(limits), tuple(docs),
                      tuple(params), sym, icon, value, ind, show, tuple(anim), sim, path.parent), out


# ---------------------------------------------------------------- catalog
class Catalog:
    def __init__(self):
        self.defs, self.problems, self.roots = {}, [], []
        self.shadowed = {}      # id -> [kept path, rejected path, ...] for ids defined by several packages (DEF-DUP-ID)

    def get(self, id):
        try:
            return self.defs[id]
        except KeyError:
            raise ModelError(f"unknown component type {id}") from None

    def categories(self):
        """Categories that have definitions: TAXONOMY order first, then the rest alphabetically."""
        have = {d.category for d in self.defs.values()}
        return [c for c in TAXONOMY if c in have] + sorted(have - set(TAXONOMY), key=str.lower)

    def search(self, text="", category=None, simulated_only=False):
        """Case-insensitive AND over whitespace tokens, each a substring of the metadata. Ranking: exact name/id/alias,
        then name prefix, then the rest; ties by name."""
        q, toks = text.strip().lower(), text.lower().split()
        out = []
        for d in self.defs.values():
            if (category and d.category != category) or (simulated_only and not d.simulated): continue
            hay = "\n".join([d.id, d.name, *d.aliases, *d.keywords, *d.tags, d.description, d.category, d.subcategory]).lower()
            if not all(t in hay for t in toks): continue
            exact = q and q in (d.id.lower(), d.name.lower(), *(a.lower() for a in d.aliases))
            out.append((0 if exact else 1 if q and d.name.lower().startswith(q) else 2, d.name.lower(), d))
        return [d for *_, d in sorted(out, key=lambda x: x[:2])]


def default_roots():
    return [BUILTIN_ROOT] + [Path(p) for p in os.environ.get("SIM_COMPONENT_PATH", "").split(os.pathsep) if p]


def reload(roots=None):
    """Rebuild the process-wide CATALOG in place (every `from .catalog import CATALOG` stays valid)."""
    roots = [Path(r) for r in (default_roots() if roots is None else roots)]
    defs, problems, origin, fixed, shadowed = {}, [], {}, {}, {}   # fixed: placeholder-free model name -> (body, definition id)
    loaded = set()          # resolved package files: a root listed twice, or nested in another, is read once
    for root in roots:
        if not root.is_dir():
            problems.append({"code": "DEF-PARSE", "severity": "warning", "file": str(root), "field": "", "id": None,
                             "message": "component root is not a directory; check SIM_COMPONENT_PATH"})
            continue
        for link in root.rglob("*"):   # rglob does not descend into a symlinked directory (no loops): say so
            if link.is_symlink() and link.is_dir():
                problems.append({"code": "DEF-LINK", "severity": "warning", "file": str(link), "field": "", "id": None,
                                 "message": "a symbolic link to a directory is not followed, so the package behind it is "
                                            "not loaded; copy the directory into the root instead"})
        for f in sorted(root.rglob("component.toml")):
            if (real := f.resolve()) in loaded: continue
            loaded.add(real)
            d, diags = load_definition(f)
            problems += diags
            if d is None: continue
            if d.id in defs:
                problems.append({"code": "DEF-DUP-ID", "severity": "error", "file": str(f), "field": "id", "id": d.id,
                                 "message": f"id {d.id!r} is already defined by {origin[d.id]}; that one is kept, "
                                            "rename this id"})
                shadowed.setdefault(d.id, [origin[d.id]]).append(f)
                continue
            mine = electrical.fixed_models(d.sim.get("electrical", {}))
            if clash := next((k for k, body in mine.items() if fixed.get(k, (body,))[0] != body), None):
                problems.append({"code": "DEF-SIM", "severity": "error", "file": str(f), "field": "sim.electrical.models",
                                 "id": d.id, "message": f"model name '{clash}' is also used by {fixed[clash][1]} with "
                                 "different parameters, and model names are global to the netlist; rename it or add "
                                 "{dev_lc} to the name"})
                continue
            fixed.update({k: (body, d.id) for k, body in mine.items()})
            defs[d.id], origin[d.id] = d, f
    CATALOG.defs, CATALOG.problems, CATALOG.roots, CATALOG.shadowed = defs, problems, roots, shadowed
    return CATALOG


CATALOG = Catalog()
reload()


def get(kind):
    return CATALOG.get(kind)


def summary(kind, params):
    """Canvas value label, e.g. '1 kΩ', '5 V', 'PULSE', 'open'. Shows raw text if it does not parse."""
    c = get(kind)
    if not c.value_param:
        return ""
    p = c.param(c.value_param)
    if not p.applies(params):    # e.g. a PULSE source has no 'dc': show the selector value instead
        p = next(q for q in c.params if q.kind == "choice")
    text = params.get(p.key, "")
    if p.kind == "choice":
        return text
    try:
        return format_si(parse_si(text, p.unit), p.unit)
    except ModelError:
        return text


def placeholder_geometry(pins):
    """(box, {pin: at}) of the generic body of a part with no usable definition: 80 wide, pins down the left edge."""
    return (0, 0, 80, 40 * math.ceil((len(pins) + 1) / 2)), {p: (0, 20 * (i + 1)) for i, p in enumerate(pins)}


def geometry(part):
    """(box, {pin id: at}) of a document part: its definition's, or the placeholder body."""
    if part.get("placeholder"):
        return placeholder_geometry(part["placeholder"]["pins"])
    c = get(part["kind"])
    return c.box, c.pos


def pin_offset(part, pin):
    """Pin position relative to the part anchor (x, y) after the part's rotation."""
    (x0, y0, w, h), pos = geometry(part)
    return rotate(pos[pin], (x0 + w / 2, y0 + h / 2), part.get("rot", 0))


def terminal_offset(kind, terminal, rot=0):
    """pin_offset for a catalog kind (no placeholder)."""
    return pin_offset({"kind": kind, "rot": rot}, terminal)


if __name__ == "__main__":   # python3 -m app.catalog [extra_root ...]: validate every definition, exit 1 on any error
    import sys
    c = reload(default_roots() + [Path(a) for a in sys.argv[1:]])
    for p in c.problems:
        print(f"{p['severity']:7} {p['code']:12} {p['file']} [{p['field']}] {p['message']}")
    errors = sum(p["severity"] == "error" for p in c.problems)
    print(f"{len(c.defs)} definitions, {errors} errors, {len(c.problems) - errors} warnings")
    sys.exit(1 if errors else 0)
