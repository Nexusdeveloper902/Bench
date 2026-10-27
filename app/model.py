"""Circuit document: parts, wires, params, nets, netlist lines. Pure Python, no Qt, no benchcore."""
import copy, json, math, os, re

from .catalog import CATALOG, PIN_RE, get
from .components import ROTATIONS, ModelError, Param
from .electrical import build_netlist, device_name
FORMAT, VERSION = "sim-circuit", 2   # writer version; the reader accepts 0, 1 and 2 (docs/persistence.md)
RUN_PARAMS = {"tstop": Param("tstop", "Stop time", "s", "5m", min=0, min_exclusive=True),
              "tstep": Param("tstep", "Time step", "s", "10u", min=0, min_exclusive=True),
              "live": Param("duration", "Live run duration", "s", "", min=0, min_exclusive=True),
              "points": Param("points", "Points", "", "20", min=1),            # AC sweep (G17)
              "fstart": Param("fstart", "Start frequency", "Hz", "1", min=0, min_exclusive=True),
              "fstop": Param("fstop", "Stop frequency", "Hz", "1M", min=0, min_exclusive=True)}
ANALYSES, SWEEPS = ("transient", "ac"), ("dec", "oct", "lin")   # AC: small-signal sweep, points per decade/octave/total
AC_DEFAULTS = {"sweep": "dec", **{k: RUN_PARAMS[k].default for k in ("points", "fstart", "fstop")}}
LIVE_STOP_TIME, LIVE_INDEFINITE = ("", "stop time"), ("indefinite", "inf", "∞")   # live duration words
METHODS = ("trap", "gear")   # integration method; gear damps trapezoidal ringing (GAPS G30), trap is ngspice's default


# Run size limits (a result is kept as Python (t, v) tuples, ~100 B per sample; the core runs on a worker thread):
MAX_POINTS = 100_000        # tstop / tstep; normal examples use 500-2000
MAX_SAMPLES = 2_000_000     # points x estimated series (~3 per part): ~200 MB worst case
NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")   # part names reach the SPICE deck verbatim: keep them inert
MAX_COORD = 1e6             # canvas coordinates; larger overflows hit-testing


def size_error(tstop, tstep, nparts):
    """None, or why a batch run of tstop at tstep would exceed the result limits (MAX_POINTS, MAX_SAMPLES)."""
    pts, series = tstop / tstep, 3 * nparts + 1
    if pts > MAX_POINTS or pts * series > MAX_SAMPLES:
        return (f"too many points ({pts:.3g} points x ~{series} signals; limit {MAX_POINTS:,} points "
                f"/ {MAX_SAMPLES:,} samples)")


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
        #                      + "placeholder": {"reason": "missing"|"incompatible", "pins": [ids], "detail": str}
        #                        only for parts loaded without a usable definition (kind/version/params kept verbatim)
        #                      + "extra": {"record": {...}, "layout": {...}}: unknown keys of the file's component
        #                        record / layout entry, written back unchanged (only when there are any)
        self.wires = []      # [("R1.b", "C1.a"), ...]
        self.tstop = RUN_PARAMS["tstop"].default
        self.tstep = RUN_PARAMS["tstep"].default
        self.method = "trap"
        self.analysis = "transient"   # or "ac": the AC sweep in self.ac (texts as typed)
        self.ac = dict(AC_DEFAULTS)
        self.live_tstop = ""  # live-run duration as typed: "" = the stop time, "indefinite", or a time ("30", "2m")
        self.path = None     # file last saved to / loaded from (Save target; Save As = save(new_path))
        self._count = {}
        self.extra = {}      # unknown top-level keys of a loaded file, written back unchanged (newer writers)

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
        self._editable(name)
        if key not in self.parts[name]["params"]:
            raise ModelError(f"{name} has no parameter {key}")
        self.parts[name]["params"][key] = str(value)

    def check_param(self, name, key, value=None):
        """Parse `value` (default: the stored text) with the catalog metadata -> float | choice str.
        Raises ModelError with a user message. Does not modify the document."""
        self._editable(name)
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

    def _editable(self, name):
        if "placeholder" in self.parts[name]:
            raise ModelError(f"{name} has no usable definition; its parameters are read-only")

    def definition(self, name):
        """Catalog Definition of part `name`, or None for a placeholder (missing/incompatible definition)."""
        d = self.parts[name]
        return None if "placeholder" in d else get(d["kind"])

    def placeholders(self):
        return [n for n, d in self.parts.items() if "placeholder" in d]

    def unmodeled(self):
        """Names of parts the electrical simulation cannot model: placeholders, and parts whose definition has no
        sim:electrical. Never substituted, never silently dropped: see netlist() and validate()."""
        return [n for n in self.parts if (c := self.definition(n)) is None or not c.simulated]

    def wired(self, name):
        return any(e.partition(".")[0] == name for w in self.wires for e in w)

    def pins(self, name):
        d = self.parts[name]
        return [f"{name}.{p}" for p in (d["placeholder"]["pins"] if "placeholder" in d else get(d["kind"]).terminals)]

    def _check_pin(self, ref):
        name, _, pin = ref.partition(".") if isinstance(ref, str) else (None, 0, None)
        if name not in self.parts or f"{name}.{pin}" not in self.pins(name):
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
        unm = set(self.unmodeled())   # modeled pins are named first, so excluded parts never renumber the nets
        order = [pin for k in (False, True) for n in self.parts if (n in unm) == k for pin in self.pins(n)]
        for p in order: find(p)
        for a, b in self.wires: parent[find(a)] = find(b)
        gnd = {find(p) for n, d in self.parts.items() if (c := self.definition(n)) and c.ground for p in self.pins(n)}
        names, out = {}, {}
        for p in order:
            r = find(p)
            if r not in names:
                names[r] = "0" if r in gnd else f"n{sum(v != '0' for v in names.values()) + 1}"
            out[p] = names[r]
        return out

    # ---- simulation input ----
    def config(self):
        """-> kwargs for Simulation.configure, or {"ac": (sweep, points, fstart, fstop)} for an AC analysis.
        Raises ModelError."""
        if self.analysis == "ac":
            return {"ac": self.ac_sweep()}
        tstop = RUN_PARAMS["tstop"].parse(self.tstop, "Run")
        tstep = RUN_PARAMS["tstep"].parse(self.tstep, "Run")
        if tstep > tstop:
            raise ModelError("Run tstep must be <= tstop")
        if err := size_error(tstop, tstep, len(self.parts)):
            raise ModelError(f"Run: {err}: increase the time step or shorten the stop time")
        return {"tstep": tstep, "tstop": tstop, "tmax": tstep, **({"method": self.method} if self.method != "trap" else {})}

    def ac_sweep(self):
        """The AC sweep -> (sweep, points, fstart, fstop), within the batch result limits. Raises ModelError."""
        a = self.ac
        if a["sweep"] not in SWEEPS:
            raise ModelError(f"AC sweep must be one of {', '.join(SWEEPS)}, not {a['sweep']!r}")
        n, f1, f2 = (RUN_PARAMS[k].parse(a[k], "AC") for k in ("points", "fstart", "fstop"))
        if n != int(n):
            raise ModelError(f"AC points must be a whole number, not {a['points']}")
        if f2 <= f1:
            raise ModelError("AC stop frequency must be above the start frequency")
        pts = {"dec": n * math.log10(f2 / f1), "oct": n * math.log2(f2 / f1), "lin": n - 1}[a["sweep"]] + 1
        if pts > MAX_POINTS or pts * (3 * len(self.parts) + 1) > MAX_SAMPLES:
            raise ModelError(f"AC: too many points ({pts:.3g}; limit {MAX_POINTS:,} points / {MAX_SAMPLES:,} samples): "
                             "fewer points or a narrower range")
        return a["sweep"], int(n), f1, f2

    def live_duration(self):
        """Live-run duration in seconds: the stop time when unset, math.inf for indefinite. Raises ModelError."""
        t = self.live_tstop.strip().lower()
        if t in LIVE_STOP_TIME:
            return RUN_PARAMS["tstop"].parse(self.tstop, "Run")
        return math.inf if t in LIVE_INDEFINITE else RUN_PARAMS["live"].parse(self.live_tstop, "Live run")

    def sim_config(self):
        """Simulation settings as persisted (text as typed). `analysis` is "transient" or "ac" (G17); `method`, `ac`
        (the AC sweep) and `live_tstop` are written only when they are not the default, so files without them stay
        byte-identical."""
        out = {"analysis": self.analysis, "tstop": self.tstop, "tstep": self.tstep}
        if self.method != "trap": out["method"] = self.method
        if self.ac != AC_DEFAULTS: out["ac"] = dict(self.ac)
        if self.live_tstop.strip(): out["live_tstop"] = self.live_tstop   # live-run duration (task 9.7)
        return out

    def netlist(self):
        """-> electrical.build_netlist(...) dict over the modeled parts, plus "excluded": the unmodeled parts left out
        (always all of them: callers must report them). Raises ModelError on bad values, or if an unmodeled part is
        wired (a part that cannot be simulated must never be dropped from a circuit it is connected to)."""
        skip = self.unmodeled()
        if bad := [n for n in skip if self.wired(n)]:
            raise ModelError(f"{', '.join(bad)} cannot be simulated and {'is' if len(bad) == 1 else 'are'} wired to "
                             "the circuit: disconnect to run")
        nl = build_netlist({n: p for n, p in self.parts.items() if n not in skip}, self.nets(), CATALOG,
                           ac=self.analysis == "ac")
        return {**nl, "excluded": skip}

    def validate(self):
        """Pre-run checks -> [diag(...)]: run settings, duplicate names, unsimulated parts, empty circuit, missing
        ground, unconnected parts/terminals, invalid parameters. Electrical checks (floating islands, source loops,
        ...) are left to the core."""
        out = []
        try:
            self.config()
        except ModelError as e:
            out.append(diag("BAD-CONFIG", "error", str(e)))
        first = {}
        for n in self.parts:
            if (o := first.setdefault(n.lower(), n)) != n:
                out.append(diag("DUP-NAME-CASE", "error",
                                f"{o} and {n} differ only in case; SPICE names are case-insensitive.", n))
        unm = self.unmodeled()
        dev_names = {}
        for n in self.parts:    # F8: SW1 is SPICE device RSW1; a part named RSW1 would collide in the deck
            if n in unm or self.definition(n).ground: continue
            dev = device_name(self.definition(n).sim["electrical"], n)
            if (o := dev_names.setdefault(dev.lower(), n)) != n:
                out.append(diag("DUP-DEVICE", "error", f"{o} and {n} both become SPICE device '{dev}'; rename one part "
                                "(the device letter is added to the name).", n))
        for n in unm:
            out.append(self._unmodeled_diag(n))
        for n, d in self.parts.items():   # F7: a type several packages define binds to the first one, never silently
            if (paths := CATALOG.shadowed.get(d["kind"])) and "placeholder" not in d:
                out.append(diag("SHADOWED-DEFINITION", "warning",
                                f"{n}: component type '{d['kind']}' is defined by more than one package; using {paths[0]}, "
                                f"ignoring {', '.join(map(str, paths[1:]))}. Check that it is the part you saved.", n))
        devices = [n for n, d in self.parts.items() if n not in unm and not self.definition(n).ground]
        if not devices:
            msg = "Nothing to simulate: place simulated components." if unm else "Circuit is empty: place some components."
            out.append(diag("EMPTY", "error", msg))
            out.sort(key=lambda x: x["severity"] != "error")
            return out
        nets, wired = self.nets(), {p for w in self.wires for p in w}
        if not any(nets[p] == "0" for n in devices for p in self.pins(n)):
            out.append(diag("NO-GROUND", "error", "No ground: place a Ground and wire it to the circuit."))
        for n, d in self.parts.items():
            if n in unm: continue
            c = get(d["kind"])
            pins = [f"{n}.{t}" for t in c.model_pins]     # nc pins have no model port: never warned about
            loose = [p for p in pins if p not in wired]
            if len(loose) == len(pins) and n in devices:
                out.append(diag("UNCONNECTED-PART", "error", f"{n} is not connected to anything.", n, pins))
            else:
                out += [diag("UNCONNECTED-PIN", "warning", f"{p} is not connected.", n, [p]) for p in loose]
            for k in c.fields(d["params"]):
                try:
                    c.param(k).parse(d["params"][k], n)
                except ModelError as e:
                    out.append(diag("BAD-PARAM", "error", str(e), n, (), k))
        out.sort(key=lambda x: x["severity"] != "error")   # stable: errors first, in check order
        return out

    def _unmodeled_diag(self, n):
        """Error if the unmodeled part is wired (the run is refused), else a warning (it is excluded and reported)."""
        d, wired = self.parts[n], self.wired(n)
        if ph := d.get("placeholder"):
            code = "MISSING-DEFINITION" if ph["reason"] == "missing" else "INCOMPATIBLE-DEFINITION"
            msg = f"{n}: {ph['detail']}; kept unchanged"
            msg += ". It is wired to the circuit: disconnect it to run." if wired else "; excluded from the run."
        else:
            code, name = "NOT-SIMULATED", f"{n} ({get(d['kind']).name})"
            msg = (f"{name} has no electrical model and is wired to the circuit: disconnect it to run." if wired else
                   f"{name} is not simulated and is excluded from the run.")
        return diag(code, "error" if wired else "warning", msg, n, self.pins(n))

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

    # ---- persistence (format v2, see docs/persistence.md) ----
    def to_dict(self):
        x = {n: p.get("extra", {}) for n, p in self.parts.items()}
        return {"format": FORMAT, "version": VERSION,
                "components": {n: {"type": p["kind"], "version": p["version"], "params": copy.deepcopy(p["params"]),
                                   **copy.deepcopy(x[n].get("record", {}))} for n, p in self.parts.items()},
                "connections": [list(w) for w in self.wires],
                "simulation": self.sim_config(),
                "layout": {n: {"x": p["x"], "y": p["y"], "rotation": p["rot"], **copy.deepcopy(x[n].get("layout", {}))}
                           for n, p in self.parts.items()},
                **copy.deepcopy(self.extra)}

    @classmethod
    def from_dict(cls, d, pins=None):
        """v2 dict, v1 (components without versions), or the unversioned v0 layout {parts:{name:{kind,x,y,params}},
        wires, tstop, tstep}. Validates the whole schema; every problem is a ModelError('bad simulation file: ...').
        A component with no usable definition (unknown type, newer version, unknown param or pin) is not an error:
        it loads as a placeholder (see docs/persistence.md). `pins` ({name: [pin ids]}) is the history's memory of
        placeholder pins, so they are not re-derived from the connections alone."""
        try:
            return cls._from_dict(d, pins or {})
        except ModelError as e:
            raise ModelError(f"bad simulation file: {e}") from None
        except (KeyError, TypeError, AttributeError, ValueError, RecursionError, OverflowError) as e:
            raise ModelError(f"bad simulation file: {e!r}") from None

    @staticmethod
    def _resolve(typ, ver, params, pins):
        """-> (Definition, None) or (None, (reason, detail)) for a saved component. Ids are matched exactly."""
        c = CATALOG.defs.get(typ)
        if c is None:
            return None, ("missing", f"component type {typ!r} (version {ver}) is not in the catalog")
        if ver != c.version:   # D19: every bump is breaking, so an older version is not reinterpreted either
            return None, ("incompatible", f"saved with {typ} version {ver}; this catalog has version {c.version}")
        bad = [f"parameter {k!r}" for k in params if k not in c.defaults()] + \
              [f"pin {p!r}" for p in sorted(pins) if p not in c.terminals]
        if bad:
            return None, ("incompatible", f"saved with {typ} version {ver} using {', '.join(bad)}, which "
                                          f"{typ} version {c.version} does not have")
        return c, None

    @classmethod
    def _from_dict(cls, d, kept):
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
            extra = {}
        elif type(ver) is int and 1 <= ver <= VERSION and d.get("format") == FORMAT:
            comps, wires, sim, layout = d["components"], d["connections"], d["simulation"], d.get("layout", {})
            extra = {k: v for k, v in d.items() if k not in ("format", "version", "components", "connections", "simulation", "layout")}
        elif type(ver) is int and ver > VERSION:
            raise ModelError(f"file format version {ver} is newer than this app supports ({VERSION})")
        else:
            raise ModelError("not a simulation file (missing format/version)")
        need(comps, dict, "components"); need(layout, dict, "layout"); need(sim, dict, "simulation")
        need(wires, (list, tuple), "connections")
        recs = {}
        for n, c in comps.items():
            check_name(n)
            need(c, dict, n)
            typ = need(c["type"], str, f"{n} type")
            v = need(c.get("version"), int, f"{n} version") if ver == 2 else 1   # v0/v1 files only hold version-1 parts
            if v < 1: raise ModelError(f"{n}: bad version {v}")
            params = need(c["params"], dict, f"{n} params")
            lay = need(layout.get(n, {}), dict, f"{n} layout")
            x, y, rot = lay.get("x", 0), lay.get("y", 0), lay.get("rotation", 0)
            ok = all(isinstance(q, (int, float)) and not isinstance(q, bool) and math.isfinite(q) and abs(q) <= MAX_COORD
                     for q in (x, y))
            if not ok or type(rot) is not int or rot not in ROTATIONS:
                raise ModelError(f"{n}: bad layout {lay}")
            xr = {k: val for k, val in c.items() if k not in ("type", "version", "params")}
            xl = {k: val for k, val in lay.items() if k not in ("x", "y", "rotation")}
            recs[n] = (typ, v, params, x, y, rot, {k: val for k, val in (("record", xr), ("layout", xl)) if val})
        wl, seen, used = [], set(), {}   # used: part -> pin ids its connections reference
        for w in wires:
            if not isinstance(w, (list, tuple)) or len(w) != 2: raise ModelError(f"bad connection {w}")
            for ref in w:
                name, _, pin = ref.partition(".") if isinstance(ref, str) else (None, 0, "")
                if name not in recs or not PIN_RE.match(pin): raise ModelError(f"no such pin {ref}")
                used.setdefault(name, set()).add(pin)
            if w[0] == w[1]: raise ModelError(f"connection {list(w)} joins a pin to itself")
            if frozenset(w) in seen: raise ModelError(f"duplicate connection {list(w)}")
            seen.add(frozenset(w))
            wl.append(tuple(w))
        doc = cls()
        doc.extra = copy.deepcopy(extra)
        for n, (typ, v, raw, x, y, rot, xtra) in recs.items():
            comp, why = cls._resolve(typ, v, raw, used.get(n, ()))
            if comp:
                params = comp.defaults()          # older files may lack params added later: use defaults
                for k, val in raw.items(): params[k] = str(need(val, (str, int, float), f"{n} {k}"))
                doc.parts[n] = {"kind": comp.id, "version": comp.version, "x": x, "y": y, "rot": rot, "params": params}
            else:   # kept verbatim, never substituted; its pins are the ones the connections use
                doc.parts[n] = {"kind": typ, "version": v, "x": x, "y": y, "rot": rot, "params": copy.deepcopy(raw),
                                "placeholder": {"reason": why[0], "pins": sorted({*used.get(n, ()), *kept.get(n, ())}),
                                                "detail": why[1]}}
            if xtra: doc.parts[n]["extra"] = copy.deepcopy(xtra)
        doc.wires = wl
        doc.tstop, doc.tstep = (str(need(sim[k], (str, int, float), k)) for k in ("tstop", "tstep"))
        doc.analysis = sim.get("analysis", "transient")
        if doc.analysis not in ANALYSES:
            raise ModelError(f"unsupported analysis {doc.analysis!r} (use one of {', '.join(ANALYSES)})")
        ac = need(sim.get("ac", {}), dict, "ac")
        if bad := sorted(set(ac) - set(AC_DEFAULTS)):
            raise ModelError(f"unknown AC setting {bad[0]!r} (use {', '.join(AC_DEFAULTS)})")
        doc.ac = {**AC_DEFAULTS, **{k: str(need(v, (str, int, float), f"ac {k}")) for k, v in ac.items()}}
        if doc.ac["sweep"] not in SWEEPS:
            raise ModelError(f"unknown AC sweep {doc.ac['sweep']!r} (use one of {', '.join(SWEEPS)})")
        doc.live_tstop = str(need(sim.get("live_tstop", ""), (str, int, float), "live_tstop"))
        doc.method = sim.get("method", "trap")
        if doc.method not in METHODS:
            raise ModelError(f"unknown integration method {doc.method!r} (use one of {', '.join(METHODS)})")
        for kind, c in CATALOG.defs.items():  # keep auto-naming collision free
            k = len(c.designator)
            doc._count[kind] = max([int(n[k:]) for n, p in doc.parts.items()
                                    if p["kind"] == kind and n[k:].isdigit()] or [0])
        return doc

    def save(self, path=None):
        """Write format v2 JSON to `path` (default: self.path) and remember it. Atomic: a failed write leaves the
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
