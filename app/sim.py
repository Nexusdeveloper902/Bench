"""The only module that imports simcore: Document -> Simulation -> RunResult. No SPICE text is built here;
netlist lines, models, .save lines and vector names come from app.components.build_netlist."""
from dataclasses import dataclass, field
import simcore
from .model import Document, ModelError


@dataclass
class RunResult:
    ok: bool
    status: str                      # "success" | "failed"
    message: str = ""                # one-line user message
    series: dict = field(default_factory=dict)   # ngspice vector name (or derived 'v(Part)') -> [(t, v)]
    findings: list = field(default_factory=list)  # core validator findings [{code,severity,message,nodes,devices,layer}]
    diagnostics: list = field(default_factory=list)  # app pre-run checks [{code,severity,message,part,pins,param}]
    signals: list = field(default_factory=list)  # readable names, see components.build_netlist
    details: str = ""                # developer diagnostics: error type, full message, findings, deck

    @property
    def state(self):
        return "completed" if self.ok else "failed"

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


def _diff(series, a, b):
    """v(a)-v(b) from core node series; net '0' is identically 0 (shares the other's time base)."""
    ref = series.get(f"v({a})") or series.get(f"v({b})") or next(iter(series.values()))
    va = series.get(f"v({a})") or [(t, 0.0) for t, _ in ref]
    vb = series.get(f"v({b})") or [(t, 0.0) for t, _ in ref]
    assert len(va) == len(vb), "node series have different time bases"
    return [(t, x - y) for (t, x), (_, y) in zip(va, vb)]


def run_document(doc: Document) -> RunResult:
    """Validate + build + run. Never raises; failures come back as status='failed' with a user message,
    the app diagnostics / core findings, and developer `details`."""
    diags = doc.validate()
    errors = [d for d in diags if d["severity"] == "error"]
    if errors:
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        msg = f"[{errors[0]['code']}] {errors[0]['message']}{more}"
        return RunResult(False, "failed", msg, diagnostics=diags, details=_details("ValidationError", msg, (), diags))
    try:
        cfg, nl = doc.config(), doc.netlist()
    except ModelError as e:    # validate() should have caught it; kept as a safety net
        return RunResult(False, "failed", str(e), diagnostics=diags, details=_details("ModelError", e, (), diags))
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
        for name, (a, b) in nl["derived"].items():   # pure subtraction of core node series
            series[name] = _diff(series, a, b)
    except simcore.SimError as e:   # SimError is Exception; typed errors below give a clear label
        kind = type(e).__name__
        msg = str(e) if kind == "InvalidCircuitError" else f"{kind}: {e}"
        return RunResult(False, "failed", msg, findings=findings, diagnostics=diags,
                         details=_details(kind, e, findings, diags, deck))
    return RunResult(True, "success", f"ok, {len(next(iter(series.values())))} points", series, findings,
                     diags, nl["signals"], _details("ok", f"{cfg}", findings, diags, deck))
