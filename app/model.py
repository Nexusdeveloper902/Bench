"""Circuit document: parts, wires, params, nets, netlist lines. Pure Python, no Qt, no simcore."""
import json, math, os, re

from .catalog import CATALOG, get
from .components import ROTATIONS, ModelError, Param
from .electrical import build_netlist
FORMAT, VERSION = "sim-circuit", 1
RUN_PARAMS = {"tstop": Param("tstop", "Stop time", "s", "5m", min=0, min_exclusive=True),
              "tstep": Param("tstep", "Time step", "s", "10u", min=0, min_exclusive=True)}


# Run size limits (a result is kept as Python (t, v) tuples, ~100 B per sample, and the core runs on the GUI thread):
MAX_POINTS = 100_000        # tstop / tstep; normal examples use 500-2000
MAX_SAMPLES = 2_000_000     # points x estimated series (~3 per part): ~200 MB worst case
NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")   # part names reach the SPICE deck verbatim: keep them inert
MAX_COORD = 1e6             # canvas coordinates; larger overflows hit-testing


def check_name(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ModelError(f"bad part name {name!r}: use letters, digits and _ only, starting with a letter")


I_OFF, I_FULL = 1e-4, 15e-3   # LED display mapping: dark at <=0.1 mA, full glow at >=15 mA


def led_level(i):
    """Display brightness in [0,1] from a core LED current (A). A mapping only, nothing simulated."""
    return max(0.0, min(1.0, (i - I_OFF) / (I_FULL - I_OFF)))


def value_at(series, t):
    """Linear interpolation in a core series [(t, v)] (clamped to its ends). Reads core output only."""
    from bisect import bisect_left
    k = bisect_left(series, t, key=lambda p: p[0])
    if k == 0: return series[0][1]
    if k == len(series): return series[-1][1]
    (t0, v0), (t1, v1) = series[k - 1], series[k]
    return v1 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def fields(kind, params):
    """Param keys the UI should show for a part (catalog order, only those that apply to `params`)."""
    return get(kind).fields(params)


def diag(code, severity, message, part=None, pins=(), param=None):
    """One pre-run diagnostic. severity: 'error' blocks the run, 'warning' does not."""
    return {"code": code, "severity": severity, "message": message, "part": part, "pins": list(pins), "param": param}


class Document:
    def __init__(self):
        self.parts = {}      # name -> {"kind","version","x","y","rot","params"}  (insertion order = creation order)
        self.wires = []      # [("R1.b", "C1.a"), ...]
        self.tstop = RUN_PARAMS["tstop"].default
        self.tstep = RUN_PARAMS["tstep"].default
        self.path = None     # file last saved to / loaded from (Save target; Save As = save(new_path))
        self._count = {}

    # ---- editing ----
    def add_part(self, kind, x=0, y=0, rot=0, **params):
        c = get(kind)
        if rot not in ROTATIONS:
            raise ModelError(f"rotation must be one of {ROTATIONS}")
        n = self._count.get(kind, 0) + 1
        name = f"{c.designator}{n}"
        while name in self.parts:
            n += 1; name = f"{c.designator}{n}"
        self._count[kind] = n
        p = c.defaults()
        for k, v in params.items():
            if k not in p: raise ModelError(f"{c.name} has no parameter {k}")
            p[k] = str(v)
        self.parts[name] = {"kind": kind, "version": c.version, "x": x, "y": y, "rot": rot, "params": p}
        return name

    def remove_part(self, name):
        del self.parts[name]
        self.wires = [w for w in self.wires if not any(e.split(".")[0] == name for e in w)]

    def remove_wire(self, wire):
        self.wires = [w for w in self.wires if w != tuple(wire)]

    def set_param(self, name, key, value):
        """Store the text as typed (validated at run / by validate()); see check_param for edit-time checks."""
        if key not in self.parts[name]["params"]:
            raise ModelError(f"{name} has no parameter {key}")
        self.parts[name]["params"][key] = str(value)

    def check_param(self, name, key, value=None):
        """Parse `value` (default: the stored text) with the catalog metadata -> float | choice str.
        Raises ModelError with a user message. Does not modify the document."""
        d = self.parts[name]
        return get(d["kind"]).param(key).parse(d["params"][key] if value is None else str(value), name)

    def rotate(self, name, deg=90):
        """Rotate a part clockwise by a multiple of 90 degrees (stored as 0/90/180/270)."""
        if deg % 90:
            raise ModelError("rotation must be a multiple of 90 degrees")
        d = self.parts[name]
        d["rot"] = (d.get("rot", 0) + deg) % 360

    def rename_part(self, old, new):
        """Rename a part (and its wires). The name must be a safe, unused identifier."""
        check_name(new)
        if new != old and new in self.parts:
            raise ModelError(f"bad part name {new!r}: already used")
        self.parts = {(new if n == old else n): p for n, p in self.parts.items()}
        self.wires = [tuple(f"{new}.{e.partition('.')[2]}" if e.partition(".")[0] == old else e for e in w)
                      for w in self.wires]

    def definition(self, name):
        """Catalog Definition of part `name`."""
        return get(self.parts[name]["kind"])

    def unmodeled(self):
        """Names of parts the electrical simulation cannot model (their definition has no sim:electrical)."""
        return [n for n, d in self.parts.items() if not get(d["kind"]).simulated]

    def pins(self, name):
        return [f"{name}.{p}" for p in get(self.parts[name]["kind"]).terminals]

    def _check_pin(self, ref):
        name, _, pin = ref.partition(".") if isinstance(ref, str) else (None, 0, None)
        if name not in self.parts or pin not in get(self.parts[name]["kind"]).terminals:
            raise ModelError(f"no such pin {ref}")

    def connect(self, p1, p2):
        self._check_pin(p1); self._check_pin(p2)
        if p1 == p2 or (p1, p2) in self.wires or (p2, p1) in self.wires:
            return
        self.wires.append((p1, p2))

    def disconnect(self, pin):
        """Remove every wire touching this pin."""
        self.wires = [w for w in self.wires if pin not in w]

    # ---- nets ----
    def nets(self):
        """pin ref -> net name. Union-find over wires; nets with a ground pin are '0'; others n1.. by first pin."""
        parent = {}
        def find(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]; a = parent[a]
            return a
        order = [pin for n in self.parts for pin in self.pins(n)]
        for p in order: find(p)
        for a, b in self.wires: parent[find(a)] = find(b)
        gnd = {find(p) for n, d in self.parts.items() if get(d["kind"]).ground for p in self.pins(n)}
        names, out = {}, {}
        for p in order:
            r = find(p)
            if r not in names:
                names[r] = "0" if r in gnd else f"n{sum(v != '0' for v in names.values()) + 1}"
            out[p] = names[r]
        return out

    # ---- simulation input ----
    def config(self):
        """-> kwargs for Simulation.configure. Raises ModelError."""
        tstop = RUN_PARAMS["tstop"].parse(self.tstop, "Run")
        tstep = RUN_PARAMS["tstep"].parse(self.tstep, "Run")
        if tstep > tstop:
            raise ModelError("Run tstep must be <= tstop")
        pts, series = tstop / tstep, 3 * len(self.parts) + 1
        if pts > MAX_POINTS or pts * series > MAX_SAMPLES:
            raise ModelError(f"Run: too many points ({pts:.3g} points x ~{series} signals; limit {MAX_POINTS:,} points "
                             f"/ {MAX_SAMPLES:,} samples): increase the time step or shorten the stop time")
        return {"tstep": tstep, "tstop": tstop, "tmax": tstep}

    def sim_config(self):
        """Simulation settings as persisted (text as typed). Only transient analysis exists in the core."""
        return {"analysis": "transient", "tstop": self.tstop, "tstep": self.tstep}

    def netlist(self):
        """-> electrical.build_netlist(...) dict over the modeled parts. Raises ModelError on bad values."""
        skip = set(self.unmodeled())
        return build_netlist({n: p for n, p in self.parts.items() if n not in skip}, self.nets(), CATALOG)

    def validate(self):
        """Pre-run checks -> [diag(...)]: run settings, empty circuit, missing ground, unconnected parts/terminals,
        invalid parameters. Electrical checks (floating islands, source loops, ...) are left to the core."""
        out = []
        try:
            self.config()
        except ModelError as e:
            out.append(diag("BAD-CONFIG", "error", str(e)))
        devices = [n for n, d in self.parts.items() if not get(d["kind"]).ground]
        if not devices:
            out.append(diag("EMPTY", "error", "Circuit is empty: place some components."))
            return out
        nets, wired = self.nets(), {p for w in self.wires for p in w}
        if not any(nets[p] == "0" for n in devices for p in self.pins(n)):
            out.append(diag("NO-GROUND", "error", "No ground: place a Ground and wire it to the circuit."))
        for n, d in self.parts.items():
            pins = self.pins(n)
            loose = [p for p in pins if p not in wired]
            if len(loose) == len(pins) and n in devices:
                out.append(diag("UNCONNECTED-PART", "error", f"{n} is not connected to anything.", n, pins))
            else:
                out += [diag("UNCONNECTED-PIN", "warning", f"{p} is not connected.", n, [p]) for p in loose]
            c = get(d["kind"])
            for k in c.fields(d["params"]):
                try:
                    c.param(k).parse(d["params"][k], n)
                except ModelError as e:
                    out.append(diag("BAD-PARAM", "error", str(e), n, (), k))
        out.sort(key=lambda x: x["severity"] != "error")   # stable: errors first, in check order
        return out

    @classmethod
    def led_example(cls):
        """Battery 5 V + R 330 + LED, wired and grounded."""
        d = cls()
        d.add_part("BAT", 40, 100); d.add_part("R", 180, 60, R=330); d.add_part("LED", 320, 100)
        d.add_part("GND", 440, 220)
        for a, b in (("V1.p", "R1.a"), ("R1.b", "D1.a"), ("D1.k", "GND1.g"), ("V1.n", "GND1.g")):
            d.connect(a, b)
        return d

    @classmethod
    def blink_example(cls):
        """PULSE source 0->5 V (2 ms period, 50% duty, 10 us edges) + R 330 + LED; 4.25 periods, ends ON.
        tmax = tstep = 20 us <= period/50, so edges are resolved."""
        d = cls()
        d.add_part("V", 40, 100, mode="PULSE", v1=0, v2=5, td=0, tr=1e-5, tf=1e-5, pw=1e-3, per=2e-3)
        d.add_part("R", 180, 60, R=330); d.add_part("LED", 320, 100); d.add_part("GND", 440, 220)
        for a, b in (("V1.p", "R1.a"), ("R1.b", "D1.a"), ("D1.k", "GND1.g"), ("V1.n", "GND1.g")):
            d.connect(a, b)
        d.tstop, d.tstep = "8.5m", "20u"
        return d

    # ---- persistence (format v1, see docs/frontend-components.md) ----
    def to_dict(self):
        return {"format": FORMAT, "version": VERSION,
                "components": {n: {"type": p["kind"], "params": dict(p["params"])} for n, p in self.parts.items()},
                "connections": [list(w) for w in self.wires],
                "simulation": self.sim_config(),
                "layout": {n: {"x": p["x"], "y": p["y"], "rotation": p["rot"]} for n, p in self.parts.items()}}

    @classmethod
    def from_dict(cls, d):
        """v1 dict, or the unversioned v0 layout {parts:{name:{kind,x,y,params}}, wires, tstop, tstep}.
        Validates the whole schema; every problem is a ModelError('bad simulation file: ...')."""
        try:
            return cls._from_dict(d)
        except ModelError as e:
            raise ModelError(f"bad simulation file: {e}") from None
        except (KeyError, TypeError, AttributeError, ValueError, RecursionError, OverflowError) as e:
            raise ModelError(f"bad simulation file: {e!r}") from None

    @classmethod
    def _from_dict(cls, d):
        def need(v, typ, what):
            if not isinstance(v, typ) or isinstance(v, bool): raise ModelError(f"{what} has the wrong type")
            return v
        if not isinstance(d, dict): raise ModelError("not a JSON object")
        ver = d.get("version", 0 if "parts" in d else None)
        if ver == 0 and type(ver) is int and "parts" in d:
            parts = need(d["parts"], dict, "parts")
            comps = {n: {"type": need(p, dict, n)["kind"], "params": p["params"]} for n, p in parts.items()}
            layout = {n: {"x": p["x"], "y": p["y"], "rotation": 0} for n, p in parts.items()}
            wires, sim = d["wires"], {"tstop": d["tstop"], "tstep": d["tstep"]}
        elif type(ver) is int and ver == VERSION and d.get("format") == FORMAT:
            comps, wires, sim, layout = d["components"], d["connections"], d["simulation"], d.get("layout", {})
        elif type(ver) is int and ver > VERSION:
            raise ModelError(f"file format version {ver} is newer than this app supports ({VERSION})")
        else:
            raise ModelError("not a simulation file (missing format/version)")
        need(comps, dict, "components"); need(layout, dict, "layout"); need(sim, dict, "simulation")
        need(wires, (list, tuple), "connections")
        doc = cls()
        for n, c in comps.items():
            check_name(n)
            need(c, dict, n)
            comp = get(need(c["type"], str, f"{n} type"))
            params = comp.defaults()          # older files may lack params added later: use defaults
            for k, v in need(c["params"], dict, f"{n} params").items():
                if k not in params: raise ModelError(f"{n}: unknown parameter {k}")
                params[k] = str(need(v, (str, int, float), f"{n} {k}"))
            lay = need(layout.get(n, {}), dict, f"{n} layout")
            x, y, rot = lay.get("x", 0), lay.get("y", 0), lay.get("rotation", 0)
            ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) <= MAX_COORD
                     for v in (x, y))
            if not ok or type(rot) is not int or rot not in ROTATIONS:
                raise ModelError(f"{n}: bad layout {lay}")
            doc.parts[n] = {"kind": comp.id, "version": comp.version, "x": x, "y": y, "rot": rot, "params": params}
        seen = set()
        for w in wires:
            if not isinstance(w, (list, tuple)) or len(w) != 2: raise ModelError(f"bad connection {w}")
            for pin in w: doc._check_pin(pin)
            if w[0] == w[1]: raise ModelError(f"connection {list(w)} joins a pin to itself")
            if frozenset(w) in seen: raise ModelError(f"duplicate connection {list(w)}")
            seen.add(frozenset(w))
            doc.wires.append(tuple(w))
        doc.tstop, doc.tstep = (str(need(sim[k], (str, int, float), k)) for k in ("tstop", "tstep"))
        if sim.get("analysis", "transient") != "transient":
            raise ModelError(f"unsupported analysis {sim['analysis']}")
        for kind, c in CATALOG.defs.items():  # keep auto-naming collision free
            k = len(c.designator)
            doc._count[kind] = max([int(n[k:]) for n, p in doc.parts.items()
                                    if p["kind"] == kind and n[k:].isdigit()] or [0])
        return doc

    def save(self, path=None):
        """Write format v1 JSON to `path` (default: self.path) and remember it. Atomic: a failed write leaves the
        old file intact. Raises ModelError (with the OS reason) on failure."""
        path = path or self.path
        if not path:
            raise ModelError("no file name to save to")
        text, tmp = json.dumps(self.to_dict(), indent=1), f"{path}.tmp"   # serialise before touching disk
        try:
            with open(tmp, "w") as f: f.write(text)
            os.replace(tmp, path)
        except OSError as e:
            try: os.remove(tmp)
            except OSError: pass
            raise ModelError(f"cannot save {path}: {e.strerror or e}") from None
        self.path = str(path)

    @classmethod
    def load(cls, path):
        def no_dups(pairs):
            keys = [k for k, _ in pairs]
            dup = next((k for k in keys if keys.count(k) > 1), None)
            if dup is not None: raise ModelError(f"duplicate id {dup!r}")
            return dict(pairs)
        try:
            with open(path) as f: doc = cls.from_dict(json.load(f, object_pairs_hook=no_dups))
        except ModelError as e:
            if str(e).startswith("bad simulation file"): raise
            raise ModelError(f"bad simulation file: {e}") from None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as e:
            raise ModelError(f"cannot open {path}: {e}") from None
        doc.path = str(path)
        return doc
