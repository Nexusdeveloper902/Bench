"""The only module that imports benchcore: Document -> Simulation -> RunResult, and live runs (LiveSession). No SPICE
text is built here; netlist lines, models, .save lines and vector names come from app.electrical.build_netlist, and
the devices a live run may change from app.electrical.live_devices (docs/plans/ngspice-library/LIVE.md)."""
import threading
from dataclasses import dataclass, field
import benchcore
from .catalog import get
from .electrical import live_devices
from .model import Document, ModelError


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


def _prepare(doc):
    """App pre-checks and netlist -> ((diags, cfg, nl), None), or (None, a 'blocked' RunResult)."""
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
    s.configure(**cfg)
    s.observe(*nl["observables"])
    return s


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
    return live_devices(sec, part, c.values(params, part))


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
    raise from iteration: they end it and land in `result`, as in run_document."""

    def __init__(self, doc, chunk=None, realtime=None):
        self._lock = threading.Lock()
        self._run, self._pending, self._log, self._t = None, [], [], 0.0
        prep, self.result = _prepare(doc)
        if self.result:
            return
        self._diags, self._cfg, nl = prep
        self._nl, self._findings = nl, []
        self._start = _start_params(doc, nl)
        self._parts = {n: (k, dict(p)) for n, (k, p) in self._start.items()}
        devices = sorted({dv for n, (k, p) in self._parts.items() for dv in _live(n, k, p)[0]})
        try:
            s = _simulation(self._cfg, nl)
            self._findings = list(s.validate())
            self._run = s.live(devices, chunk, realtime)
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
        for n, pts in series.items():
            self.result.series[n].extend(pts)
        if c["done"]:
            self._end()
        return {"t": c["t"], "time": c["time"], "series": series, "changes": changes, "done": c["done"],
                "wall": c["wall"], "rtf": c["rtf"]}

    def _end(self, error=None):
        r = self.result
        if r.status != "running":
            return
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
        """This session's change log replayed on the circuit it started from -> RunResult (blocking). A session that
        never started returns its own blocked / failed result."""
        if self._run is None:
            return self.result
        return _replay(self._diags, self._cfg, self._nl, self._start, self._log)


def replay_document(doc, changes):
    """A live run's change log [(t, part, key, value)] replayed on doc as one blocking run -> RunResult like
    run_document. Each change applies exactly at its t, as in the live run; the waveform matches the live run's
    within solver tolerance (its extra chunk boundaries move the time steps). 'blocked' for a change that is not a
    live change of doc."""
    prep, blocked = _prepare(doc)
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
