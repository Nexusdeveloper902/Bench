"""The only module that imports simcore: Document -> Simulation -> RunResult. No SPICE text is built here;
netlist lines, models, .save lines and vector names come from app.electrical.build_netlist."""
from dataclasses import dataclass, field
import simcore
from .model import Document, ModelError


@dataclass
class RunResult:
    ok: bool
    status: str                      # "success" | "failed" (the engine ran and errored) | "blocked" (pre-check refused)
    message: str = ""                # one-line user message
    series: dict = field(default_factory=dict)   # ngspice vector name (or derived 'v(Part)') -> [(t, v)]
    findings: list = field(default_factory=list)  # core validator findings [{code,severity,message,nodes,devices,layer}]
    diagnostics: list = field(default_factory=list)  # app pre-run checks [{code,severity,message,part,pins,param}]
    signals: list = field(default_factory=list)  # readable names, see components.build_netlist
    details: str = ""                # developer diagnostics: error type, full message, findings, deck
    excluded: list = field(default_factory=list)  # unsimulated parts left out of the circuit (named in the message)

    @property
    def state(self):
        return {"success": "completed", "blocked": "blocked"}.get(self.status, "failed")

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


SIM_ERRORS = (simcore.InvalidCircuitError, simcore.InvalidConfigError, simcore.ConvergenceError, simcore.SolverError,
              simcore.BackendError, simcore.UnsupportedError, simcore.UnavailableError)   # not SimError itself: see R6


def _diff(series, a, b):
    """v(a)-v(b) from core node series; net '0' is identically 0 (shares the other's time base)."""
    ref = series.get(f"v({a})") or series.get(f"v({b})") or next(iter(series.values()))
    va = series.get(f"v({a})") or [(t, 0.0) for t, _ in ref]
    vb = series.get(f"v({b})") or [(t, 0.0) for t, _ in ref]
    assert len(va) == len(vb), "node series have different time bases"
    return [(t, x - y) for (t, x), (_, y) in zip(va, vb)]


def run_document(doc: Document) -> RunResult:
    """Validate + build + run. Failures come back with a user message, the app diagnostics / core findings and
    developer `details`: status 'blocked' if the pre-check refused the run (the core was never called), 'failed' if
    the core errored. Only core errors are caught: an exception from app code is a bug and propagates."""
    diags = doc.validate()
    errors = [d for d in diags if d["severity"] == "error"]
    if errors:
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        msg = f"[{errors[0]['code']}] {errors[0]['message']}{more}"
        return RunResult(False, "blocked", msg, diagnostics=diags, details=_details("ValidationError", msg, (), diags))
    try:
        cfg, nl = doc.config(), doc.netlist()
    except ModelError as e:    # validate() should have caught it; kept as a safety net
        return RunResult(False, "blocked", str(e), diagnostics=diags, details=_details("ModelError", e, (), diags))
    excluded = nl["excluded"]
    deck = nl["models"] + nl["lines"] + nl["saves"]
    findings = []
    try:
        s = simcore.Simulation("app")
        s.build_circuit(nl["lines"])
        for m in nl["models"]: s.add_model(m)
        for line in nl["saves"]: s.add(line)
        s.configure(**cfg)
        s.observe(*nl["observables"])
        findings = list(s.validate())
        s.run()
        series = {n: list(s.observable(n)) for n in nl["observables"]}
    except SIM_ERRORS as e:   # typed core errors give a clear label
        kind = type(e).__name__
        msg = str(e) if kind == "InvalidCircuitError" else f"{kind}: {e}"
        return RunResult(False, "failed", msg, findings=findings, diagnostics=diags,
                         details=_details(kind, e, findings, diags, deck), excluded=excluded)
    for name, (a, b) in nl["derived"].items():   # pure subtraction of core node series
        series[name] = _diff(series, a, b)
    note = f"; not simulated: {', '.join(excluded)}" if excluded else ""
    return RunResult(True, "success", f"ok, {len(next(iter(series.values())))} points{note}", series, findings,
                     diags, nl["signals"], _details("ok", f"{cfg}", findings, diags, deck), excluded)
