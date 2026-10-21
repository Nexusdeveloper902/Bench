"""The only module that imports benchcore: Document -> Simulation -> RunResult, and live runs (LiveSession). No SPICE
text is built here; netlist lines, models, .save lines and vector names come from app.electrical.build_netlist, and
the devices a live run may change from app.electrical.live_devices (docs/plans/ngspice-library/LIVE.md)."""
import bisect
import math
import re
import threading
from dataclasses import dataclass, field
import benchcore
from .catalog import get
from .electrical import live_devices
from .components import format_si
from .model import MAX_POINTS, MAX_SAMPLES, Document, ModelError, size_error


@dataclass
class RunResult:
    ok: bool
    status: str                      # "success" | "failed" (the engine ran and errored) | "blocked" (pre-check refused)
    #                                  live runs also: "running" (chunks still coming) | "cancelled" (stopped early)
    message: str = ""                # one-line user message
    series: dict = field(default_factory=dict)   # ngspice vector name (or derived 'v(Part)') -> [(t, v)]
    findings: list = field(default_factory=list)  # core validator findings [{code,severity,message,nodes,devices,layer}]
    diagnostics: list = field(default_factory=list)  # app pre-run checks [{code,severity,message,part,pins,param}]
    signals: list = field(default_factory=list)  # readable names, see components.build_netlist
    details: str = ""                # developer diagnostics: error type, full message, findings, deck
    excluded: list = field(default_factory=list)  # unsimulated parts left out of the circuit (named in the message)
    changes: list = field(default_factory=list)   # live runs and their replays: [(t, part, key, value)] applied
    analysis: str = "transient"      # "ac": series are [(frequency Hz, complex value)] (G17)

    @property
    def state(self):
        return {"success": "completed", "blocked": "blocked", "running": "running", "cancelled": "cancelled"}.get(
            self.status, "failed")

    @property
    def observables(self):
        return list(self.series)

    def signal(self, name):
        """Signal dict by readable name ('V(C1)', 'I(R1)', 'V(n2)') or by vector name ('v(n2)', '@r1[i]')."""
        return next((s for s in self.signals if name in (s["name"], s["vector"])), None)

    def get(self, name):
        """Series by readable or vector name. KeyError if absent."""
        s = self.signal(name)
        return self.series[s["vector"] if s else name]

    def stats(self, name):
        v = [p[1] for p in self.get(name)]
        return {"final": v[-1], "min": min(v), "max": max(v)}


def _details(kind, msg, findings=(), diagnostics=(), deck=()):
    out = [f"{kind}: {msg}"]
    out += [f"diagnostic [{d['severity']}] {d['code']}: {d['message']}" for d in diagnostics]
    out += [f"finding [{f['severity']}] {f['code']}: {f['message']} nodes={f.get('nodes')} devices={f.get('devices')}"
            for f in findings]
    if deck:
        out += ["--- netlist sent to the core ---", *deck]
    return "\n".join(out)


SIM_ERRORS = (benchcore.InvalidCircuitError, benchcore.InvalidConfigError, benchcore.ConvergenceError, benchcore.SolverError,
              benchcore.BackendError, benchcore.UnsupportedError, benchcore.UnavailableError)   # not SimError itself: see R6


def _diff(series, a, b):
    """v(a)-v(b) from core node series; net '0' is identically 0 (shares the other's time base)."""
    ref = series.get(f"v({a})") or series.get(f"v({b})") or next(iter(series.values()))
    va = series.get(f"v({a})") or [(t, 0.0) for t, _ in ref]
    vb = series.get(f"v({b})") or [(t, 0.0) for t, _ in ref]
    assert len(va) == len(vb), "node series have different time bases"
    return [(t, x - y) for (t, x), (_, y) in zip(va, vb)]


def _prepare(doc, live=False):
    """App pre-checks and netlist -> ((diags, cfg, nl), None), or (None, a 'blocked' RunResult). live: a live run or
    its replay, which are transient only."""
    if live and doc.analysis != "transient":
        msg = "Live runs are transient only: switch the analysis to Transient to run live."
        return None, RunResult(False, "blocked", msg, details=_details("AnalysisError", msg))
    diags = doc.validate()
    errors = [d for d in diags if d["severity"] == "error"]
    if errors:
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        msg = f"[{errors[0]['code']}] {errors[0]['message']}{more}"
        return None, RunResult(False, "blocked", msg, diagnostics=diags, details=_details("ValidationError", msg, (), diags))
    try:
        cfg, nl = doc.config(), doc.netlist()
    except ModelError as e:    # validate() should have caught it; kept as a safety net
        return None, RunResult(False, "blocked", str(e), diagnostics=diags, details=_details("ModelError", e, (), diags))
    return (diags, cfg, nl), None


def _simulation(cfg, nl):
    s = benchcore.Simulation("app")
    s.build_circuit(nl["lines"])
    for m in nl["models"]: s.add_model(m)
    for line in nl["saves"]: s.add(line)
    if "ac" in cfg: s.ac(*cfg["ac"])
    else: s.configure(**cfg)
    s.observe(*nl["observables"])
    return s


def _ac_netlist(nl):
    """An AC run reads node voltages and source branch currents only: in an ac plot a device's @ vector holds its
    operating-point value at every frequency, not a small-signal one (probed, G17), so those currents are left out."""
    ac = lambda v: not v.startswith("@")
    dropped = [s["name"] for s in nl["signals"] if not ac(s["vector"])]
    return {**nl, "saves": [], "observables": [o for o in nl["observables"] if ac(o)],
            "signals": [s for s in nl["signals"] if ac(s["vector"])], "no_ac": dropped}


def _run_ac(diags, cfg, nl):
    nl, findings = _ac_netlist(nl), []
    if not nl["observables"]:   # only device currents, all dropped: nothing to sweep (PR #3 review)
        msg = ("AC has nothing to show: this circuit has no node voltage or source current, and device currents are "
               "operating-point values in AC. Add a source or a node other than ground.")
        return RunResult(False, "blocked", msg, diagnostics=diags, details=_details("AnalysisError", msg),
                         excluded=nl["excluded"], analysis="ac")
    try:
        s = _simulation(cfg, nl)
        findings = list(s.validate())
        s.run()
        f = s.frequency
        series = {n: [(fq, complex(*z)) for fq, z in zip(f, s.observable(n))] for n in nl["observables"]}
    except SIM_ERRORS as e:
        r = _failed(e, findings, diags, nl)
        r.analysis = "ac"
        return r
    _derive(series, nl)
    notes = ["no part has an AC stimulus, so every signal is 0: give a voltage or current source an AC magnitude"] \
        if all(v == 0 for pts in series.values() for _, v in pts) else []
    if nl["no_ac"]:
        notes.append("device currents are operating-point values in AC and are not shown (source currents are)")
    return RunResult(True, "success", _ok_message(series, nl, notes), series, findings, diags, nl["signals"],
                     _details("ok", f"{cfg}", findings, diags, _deck(nl)), nl["excluded"], analysis="ac")


def _deck(nl):
    return nl["models"] + nl["lines"] + nl["saves"]


def _failed(e, findings, diags, nl):
    kind = type(e).__name__   # typed core errors give a clear label
    msg = str(e) if kind == "InvalidCircuitError" else f"{kind}: {e}"
    return RunResult(False, "failed", msg, findings=findings, diagnostics=diags,
                     details=_details(kind, e, findings, diags, _deck(nl)), excluded=nl["excluded"])


def _derive(series, nl):
    for name, (a, b) in nl["derived"].items():   # pure subtraction of core node series
        series[name] = _diff(series, a, b)
    return series


def _ok_message(series, nl, warnings=()):
    note = f"; not simulated: {', '.join(nl['excluded'])}" if nl["excluded"] else ""
    note += "".join(f"; warning: {w}" for w in warnings)
    return f"ok, {len(next(iter(series.values())))} points{note}"


def _runtime_findings(res):
    """The core's plausibility warnings on a successful run (G15: a runaway internal node) as core findings."""
    return [{"code": "IMPLAUSIBLE-NODE", "severity": "warn", "message": w, "nodes": [], "devices": [], "layer": "runtime",
             "class": "value", "scope": ""} for w in res.get("warnings", ())]


def run_document(doc: Document) -> RunResult:
    """Validate + build + run. Failures come back with a user message, the app diagnostics / core findings and
    developer `details`: status 'blocked' if the pre-check refused the run (the core was never called), 'failed' if
    the core errored. Only core errors are caught: an exception from app code is a bug and propagates."""
    prep, blocked = _prepare(doc)
    if blocked: return blocked
    diags, cfg, nl = prep
    if "ac" in cfg:
        return _run_ac(diags, cfg, nl)
    findings = []
    try:
        s = _simulation(cfg, nl)
        findings = list(s.validate())
        warned = _runtime_findings(s.run())
        series = {n: list(s.observable(n)) for n in nl["observables"]}
    except SIM_ERRORS as e:
        return _failed(e, findings, diags, nl)
    _derive(series, nl)
    findings += warned
    return RunResult(True, "success", _ok_message(series, nl, [f["message"] for f in warned]), series, findings, diags, nl["signals"],
                     _details("ok", f"{cfg}", findings, diags, _deck(nl)), nl["excluded"])


# ---------------------------------------------------------------- live runs (task 9.5a; LIVE.md)
def _text(value):
    return value if isinstance(value, str) else repr(float(value))


def _live(part, kind, params):
    """live_devices for one part from its param texts -> ({device: value}, {key: stimulus?}); ModelError if a
    value does not parse."""
    c = get(kind)
    sec = c.sim.get("electrical") if c.simulated else None
    if not sec or sec.get("ground"):
        return {}, {}
    devices, keys = live_devices(sec, part, c.values(params, part))
    if "live" in sec:   # G35: the package's explicit list overrides the derived rule ([] = nothing is live)
        keys = {k: s for k, s in keys.items() if k in sec["live"]}
    # devices stay as derived (a change alters only what its key moves); a part with no live key declares none
    return (devices if keys else {}), keys


def _change(part, kind, params, key, value):
    """One live change -> (new param texts, {device: new value} for the devices it moves). ModelError (a user
    message) when the param cannot change live or the value is refused by the param's own checks."""
    old, keys = _live(part, kind, params)
    if key not in keys:
        raise ModelError(f"{part} {key} cannot change during a live run")
    new = {**params, key: _text(value)}
    return new, {d: v for d, v in _live(part, kind, new)[0].items() if old.get(d) != v}


def _start_params(doc, nl):
    return {n: (d["kind"], dict(d["params"])) for n, d in doc.parts.items() if n not in nl["excluded"]}


INDEFINITE_STEPS = 1e12   # an indefinite live run is a transient of tstep x this, never reached (LIVE.md)
TARGET_WALL = 0.05        # G34: a live chunk aims at this much compute (wall) time, so controls answer within ~50 ms

# G40: time-function slots ngspice fills from the run's tstop when missing or 0 -> (slot index, default from tstop).
# PULSE(v1 v2 td tr tf pw per): pw, per = tstop; SIN(vo va freq ...): freq = 1/tstop; SFFM(vo va fc mdi fs): fc, fs =
# 1/tstop. Slots before them that are missing get ngspice's own (tstop-free) defaults, PAD.
_TSTOP_SLOTS = {"pulse": {5: lambda T: T, 6: lambda T: T}, "sin": {2: lambda T: 1 / T},
                "sffm": {2: lambda T: 1 / T, 4: lambda T: 1 / T}}
_PAD = {"pulse": lambda dt: ["0", "0", "0", repr(dt), repr(dt)], "sin": lambda dt: ["0", "0"],
        "sffm": lambda dt: ["0", "0", "0", "0"]}
_TFUNC = re.compile(r"(\{[^}]*\})|\b(pulse|sin|sffm|am)\b(\s*\(([^()]*)\))?", re.I)   # {expressions} are skipped
_ZERO = re.compile(r"[+-]?(?:0+\.?0*|\.0+)(?:e[+-]?\d+)?[a-z]*", re.I)


def pin_tstop_defaults(lines, tstop, tstep, strict):
    """G40: the deck lines with every V/I time function that falls back to ngspice's tstop default (above) given
    that default explicitly, computed from `tstop` (the batch stop time). A live run's own tstop is its duration
    (tstep x 1e12 when indefinite), so without this a PULSE with no period would follow the live tstop, unlike the
    batch run. strict (the live tstop differs): a function this cannot check (no parentheses, split over '+'
    lines) or pin (AM without its MF and FC) raises ModelError instead of being passed on. A {expression} slot
    counts as given."""
    out = []
    for line in lines:
        toks = line.split(None, 3)   # name, n+, n-, the rest: only the rest can hold a time function
        if line.lstrip()[:1].lower() not in ("v", "i") or len(toks) < 4:
            out.append(line); continue

        def fix(m):
            if m[1]: return m[0]
            name, args = m[2].lower(), m[4]
            a = (args or "").replace(",", " ").split()
            if args is None or name == "am":
                if strict and (args is None or len(a) < 4 or any(_ZERO.fullmatch(x) for x in a[2:4])):
                    raise ModelError(f"Live run refused: '{line.strip()}' may leave a time to the run's stop time, "
                                     "and this live run's duration is not the stop time, so its waveform would differ "
                                     "from the batch run's. Give every time of the source explicitly, as "
                                     "PULSE(...)/SIN(...)/SFFM(...)/AM(...) on one line.")
                return m[0]
            slots = _TSTOP_SLOTS[name]
            if len(a) < max(slots) + 1:
                a += _PAD[name](tstep)[len(a):]
            for i, f in slots.items():
                while len(a) <= i: a.append("0")
                if _ZERO.fullmatch(a[i]): a[i] = repr(f(tstop))
            return f"{m[2]}({' '.join(a)})"
        rest = toks[3]
        out.append(line[:len(line) - len(rest)] + _TFUNC.sub(fix, rest))
    return out


def trim(series, t_from, cap):
    """Drop points before t_from from every series (they share one time base), keeping at most cap per series:
    a live run retains a scrolling window of sim time, so memory is bounded however long it runs."""
    ser = next(iter(series.values()), None)
    if not ser:
        return
    cut = max(bisect.bisect_left(ser, (t_from,)), len(ser) - cap)
    if cut > 0:
        for pts in series.values():
            del pts[:cut]


class Overview:
    """G42: a bounded, decimated record of a whole live run, kept beside the full-resolution window: per time bucket,
    each series' min and max with their times. When there are more than `buckets`, neighbours merge pairwise (the
    width doubles), so memory is bounded however long the run is, and the run's extremes are kept exactly."""

    def __init__(self, width, buckets=2000):
        self.w, self.n, self.b = width, buckets, []   # b: [[index, ta, tb, {name: [tmin, vmin, tmax, vmax]}]]

    @staticmethod
    def _merge(x, y):
        for n, (t0, lo, t1, hi) in y.items():
            s = x[n]
            if lo < s[1]: s[0], s[1] = t0, lo
            if hi > s[3]: s[2], s[3] = t1, hi

    def add(self, series):
        """Fold one chunk's new points ({name: [(t, v)]}, one shared time base) in."""
        times = [t for t, _ in next(iter(series.values()), ())]
        i = 0
        while i < len(times):
            k = int(times[i] // self.w)
            j = max(i + 1, bisect.bisect_left(times, (k + 1) * self.w, i))
            st = {}
            for n, pts in series.items():
                seg = pts[i:j]
                lo, hi = min(seg, key=lambda p: p[1]), max(seg, key=lambda p: p[1])
                st[n] = [lo[0], lo[1], hi[0], hi[1]]
            if self.b and self.b[-1][0] == k:
                self.b[-1][2] = times[j - 1]; self._merge(self.b[-1][3], st)
            else:
                self.b.append([k, times[i], times[j - 1], st])
            i = j
        while len(self.b) > self.n:
            self.w *= 2
            out = []
            for k, ta, tb, st in self.b:
                if out and out[-1][0] == k // 2:
                    out[-1][2] = tb; self._merge(out[-1][3], st)
                else:
                    out.append([k // 2, ta, tb, st])
            self.b = out

    def before(self, t0):
        """{name: [(t, v)]}: the coarse record before t0 (two points per bucket: its min and max in time order, at the
        bucket's first and last time), to put in front of a window that starts at t0."""
        out = {}
        for k, ta, tb, st in self.b:
            if ta >= t0: break
            tb = tb if tb < t0 else (ta + t0) / 2
            for n, (tl, lo, th, hi) in st.items():
                pts = out.setdefault(n, [])
                pts += [(ta, lo)] if ta == tb else [(ta, lo), (tb, hi)] if tl <= th else [(ta, hi), (tb, lo)]
        return out


def live_params(doc):
    """[{"part", "key", "label", "unit", "stimulus"}]: what a live run of doc can change, at the parts' current
    values, in part order. stimulus: a sensor's physical input (lux, °C, N, mT, ...); the others are R values,
    source DC values, switch states. Parts that are not simulated, or whose values do not parse, have none."""
    out = []
    for name, d in doc.parts.items():
        if "placeholder" in d or not get(d["kind"]).simulated:
            continue
        try:
            keys = _live(name, d["kind"], d["params"])[1]
        except ModelError:
            continue
        p = get(d["kind"]).param
        out += [{"part": name, "key": k, "label": p(k).label, "unit": p(k).unit, "stimulus": s} for k, s in keys.items()]
    return out


class LiveSession:
    """A live run of a Document: the circuit runs while its live params change, and results arrive in chunks.

    Threading: iterate the session on ONE worker thread (`for chunk in session`; each step computes a chunk with
    the GIL released). set_stimulus / pause / resume / cancel may be called from any thread, the Qt thread
    included: they only queue or flag, never wait for the engine. close() waits for a chunk in progress.
    While a session is open no other run can start (run_document then fails with a BackendError), so end every
    session: iterate to the end, or cancel() and let the worker finish, or close().

    `result` is a RunResult that grows on the worker thread: status "running", then "success" / "cancelled" /
    "failed" (series so far are kept) / "blocked" (never started; iterating yields nothing). Core errors never
    raise from iteration: they end it and land in `result`, as in run_document.

    Duration (task 9.7): `tstop` is doc.live_duration() (the stop time unless the document sets its own live
    duration; math.inf = indefinite, until cancelled). `window` = min(stop time, tstop): `result` keeps only the
    last `window` of sim time, and at most `cap` points per series, so a long or indefinite run has bounded memory
    (a run no longer than the stop time keeps everything, as before)."""

    def __init__(self, doc, chunk=None, realtime=None, target=TARGET_WALL):
        self._lock = threading.Lock()
        self._run, self._pending, self._log, self._t = None, [], [], 0.0
        prep, self.result = _prepare(doc, live=True)
        if not self.result:
            try:
                self.tstop = doc.live_duration()
            except ModelError as e:
                self.result = RunResult(False, "blocked", str(e), diagnostics=prep[0],
                                        details=_details("ModelError", e, (), prep[0]))
        if self.result:
            return
        self._diags, cfg, nl = prep
        self._nparts = len(doc.parts)
        self.window = min(cfg["tstop"], self.tstop)
        self.indefinite = math.isinf(self.tstop)
        self._cfg = {**cfg, "tstop": cfg["tstep"] * INDEFINITE_STEPS if self.indefinite else self.tstop}
        # the chunk follows the window (the plot's time span), as the engine's default follows tstop
        chunk = chunk or (min(self.window / 100, 0.05 * realtime) if realtime else self.window / 100)
        self.cap = min(MAX_POINTS, MAX_SAMPLES // (len(nl["observables"]) + len(nl["derived"]) or 1))
        self.overview = Overview(self.window / 1000)   # G42: the whole run, coarsely, beside the retained window
        try:   # G40: time functions keep the batch run's tstop defaults (the replay, a batch run of another length, too)
            pin = lambda ls: pin_tstop_defaults(ls, cfg["tstop"], cfg["tstep"], self._cfg["tstop"] != cfg["tstop"])
            nl = {**nl, "lines": pin(nl["lines"]), "models": pin(nl["models"])}
        except ModelError as e:
            self.result = RunResult(False, "blocked", str(e), diagnostics=self._diags,
                                    details=_details("ModelError", e, (), self._diags, _deck(nl)))
            return
        self._nl, self._findings = nl, []
        self._start = _start_params(doc, nl)
        self._parts = {n: (k, dict(p)) for n, (k, p) in self._start.items()}
        devices = sorted({dv for n, (k, p) in self._parts.items() for dv in _live(n, k, p)[0]})
        try:
            s = _simulation(self._cfg, nl)
            self._findings = list(s.validate())
            self._run = s.live(devices, chunk, realtime, target)
        except SIM_ERRORS as e:
            self.result = _failed(e, self._findings, self._diags, nl)
            return
        series = {n: [] for n in [*nl["observables"], *nl["derived"]]}
        self.result = RunResult(False, "running", "running", series, self._findings, self._diags, nl["signals"],
                                _details("running", f"{self._cfg}", self._findings, self._diags, _deck(nl)),
                                nl["excluded"])

    def __iter__(self):
        return self

    def __next__(self):
        """-> {"t": sim time reached, "time": [t], "series": {vector: [(t, v)]} (the new points only, keyed like
        RunResult.series: use result.signal(name)["vector"] for a readable name), "changes": [(t, part, key, value)]
        applied at this chunk's start, "done", "wall": active wall seconds, "rtf": sim seconds per wall second}."""
        if self._run is None:
            raise StopIteration
        try:
            c = next(self._run)
        except StopIteration:
            self._end()
            raise
        except SIM_ERRORS as e:
            self._end(e)
            raise StopIteration from None
        with self._lock:
            applied = [p for p in self._pending if p[0] <= c["seq"]]
            self._pending = [p for p in self._pending if p[0] > c["seq"]]
        changes = [(self._t, part, key, value) for _, part, key, value in applied]
        self._log += changes
        self._t = c["t"]
        series = _derive({n: c["series"][n] for n in self._nl["observables"]}, self._nl)
        self.overview.add(series)
        for n, pts in series.items():
            self.result.series[n].extend(pts)
        trim(self.result.series, c["t"] - self.window, self.cap)
        if c["done"]:
            self._end()
        return {"t": c["t"], "time": c["time"], "series": series, "changes": changes, "done": c["done"],
                "wall": c["wall"], "rtf": c["rtf"]}

    def _end(self, error=None):
        r = self.result
        if r.status != "running":
            return
        first = next(iter(r.series.values()), None)
        if first:   # G42: the points trimmed from the window come back coarsely, so the whole run stays visible
            for n, pts in self.overview.before(first[0][0]).items():
                r.series[n][:0] = pts
        if error is not None:
            f = _failed(error, self._findings, self._diags, self._nl)
            r.status, r.message, r.details = "failed", f.message, f.details
        elif self._run.state == "cancelled":
            r.status, r.message = "cancelled", f"cancelled at {self._t:g} s"
        else:
            r.ok, r.status, r.message = True, "success", _ok_message(r.series, self._nl)
            r.details = _details("ok", f"{self._cfg}", self._findings, self._diags, _deck(self._nl))

    def set_stimulus(self, part, value, key=None):
        """Change a live param of `part` (key None: its stimulus, for parts with exactly one) to `value` (number or
        text as typed). Applied at the next chunk boundary and then listed in `changes`. Raises ModelError with a
        user message when the run is over, the param cannot change live, or the value is refused."""
        with self._lock:
            if self._run is None or self.result.status != "running":
                raise ModelError("no live run in progress")
            if part not in self._parts:
                raise ModelError(f"no part {part} in this run")
            kind, params = self._parts[part]
            if key is None:
                stim = [k for k, s in _live(part, kind, params)[1].items() if s]
                if len(stim) != 1:
                    raise ModelError(f"{part} has {'more than one stimulus' if stim else 'no stimulus'}: name the parameter")
                key = stim[0]
            new, alters = _change(part, kind, params, key, value)
            try:
                seq = self._run.set(alters)
            except SIM_ERRORS as e:   # the run ended meanwhile, or a device the run did not declare
                raise ModelError(f"{part} {key}: {e}") from None
            self._parts[part] = (kind, new)
            self._pending.append((seq, part, key, get(kind).param(key).parse(new[key], part)))

    def pause(self):
        if self._run is not None: self._run.pause()

    def resume(self):
        if self._run is not None: self._run.resume()

    def cancel(self):
        """Stop at the next chunk boundary (or now, if paused); the worker's iteration then ends."""
        if self._run is not None: self._run.cancel()

    def close(self):
        """End now and release the engine (waits for a chunk in progress). Safe to call more than once."""
        if self._run is not None:
            self._run.close()
            self._end()

    @property
    def state(self):
        """running | paused | completed | cancelled | failed | blocked."""
        if self.result.status == "running" and self._run is not None:
            return {"done": "completed"}.get(self._run.state, self._run.state)
        return self.result.state

    @property
    def changes(self):
        """[(t, part, key, value)] applied so far, in order: replay_document(doc, changes) reproduces the run."""
        return list(self._log)

    def replay(self):
        """This session's change log replayed on the circuit it started from -> RunResult (blocking): a batch run of
        the live duration, or of the time reached for an indefinite run. 'blocked' when that run would exceed the
        batch result limits (size_error). A session that never started returns its own blocked / failed result."""
        if self._run is None:
            return self.result
        cfg = {**self._cfg, "tstop": self._t} if self.indefinite else self._cfg
        err = size_error(cfg["tstop"], cfg["tstep"], self._nparts) if cfg["tstop"] > 0 else "it never ran"
        if err:
            msg = (f"Replay refused: the live run lasted {format_si(cfg['tstop'], 's', 4)}; as one batch run that is "
                   f"{err}. Replay a shorter live run, or increase the time step.")
            return RunResult(False, "blocked", msg, diagnostics=self._diags, details=_details("ReplayTooLong", msg),
                             changes=list(self._log))
        return _replay(self._diags, cfg, self._nl, self._start, self._log)


def replay_document(doc, changes):
    """A live run's change log [(t, part, key, value)] replayed on doc as one blocking run -> RunResult like
    run_document. Each change applies exactly at its t, as in the live run; the waveform matches the live run's
    within solver tolerance (its extra chunk boundaries move the time steps). 'blocked' for a change that is not a
    live change of doc."""
    prep, blocked = _prepare(doc, live=True)
    if blocked: return blocked
    diags, cfg, nl = prep
    return _replay(diags, cfg, nl, _start_params(doc, nl), changes)


def _replay(diags, cfg, nl, start, changes):
    parts, log, findings = {n: (k, dict(p)) for n, (k, p) in start.items()}, [], []
    try:
        for t, part, key, value in changes:
            if part not in parts:
                raise ModelError(f"no part {part} in this circuit")
            kind, params = parts[part]
            new, alters = _change(part, kind, params, key, value)
            parts[part] = (kind, new)
            log += [(t, d, v) for d, v in alters.items()]
    except ModelError as e:
        return RunResult(False, "blocked", str(e), diagnostics=diags, details=_details("ModelError", e, (), diags))
    try:
        s = _simulation(cfg, nl)
        findings = list(s.validate())
        s.replay(log)
        series = {n: list(s.observable(n)) for n in nl["observables"]}
    except SIM_ERRORS as e:
        return _failed(e, findings, diags, nl)
    _derive(series, nl)
    return RunResult(True, "success", _ok_message(series, nl), series, findings, diags, nl["signals"],
                     _details("ok", f"{cfg}", findings, diags, _deck(nl)), nl["excluded"], list(changes))
