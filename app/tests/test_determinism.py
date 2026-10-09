"""Run -> rerun determinism through the REAL path. ngspice is deterministic for a given deck, and each
run_document call builds a fresh Simulation, so equal documents must give bit-identical arrays regardless of
what ran before. Exact equality (not approx) is intended: any difference means state leaked between runs."""
import simcore
from app.model import Document
from app.sim import run_document


def rlc(C="1u"):
    d = Document()
    d.add_part("V", dc="5"); d.add_part("R", R="10"); d.add_part("L", L="1m"); d.add_part("C", C=C)
    d.add_part("D", Is="1p"); d.add_part("GND")
    for a, b in (("V1.p", "R1.a"), ("R1.b", "L1.a"), ("L1.b", "C1.a"), ("C1.b", "GND1.g"), ("V1.n", "GND1.g"),
                 ("L1.b", "D1.a"), ("D1.k", "GND1.g")):
        d.connect(a, b)
    d.tstop, d.tstep = "1m", "1u"
    return d


def test_rerun_same_document_is_bit_identical():
    d = rlc()
    a, b = run_document(d), run_document(d)
    assert a.ok and b.ok and a.series == b.series and len(a.get("V(C1)")) > 500


def test_independent_documents_are_bit_identical():
    assert run_document(rlc()).series == run_document(rlc()).series


def test_other_runs_in_between_do_not_leak():
    """run A -> run a different circuit and a failing one -> run A again: identical to the first A."""
    first = run_document(rlc())
    other = run_document(rlc("2.2u"))
    bad = Document(); bad.add_part("R"); bad.add_part("C"); bad.connect("R1.a", "C1.a")
    assert other.ok and not run_document(bad).ok
    again = run_document(rlc())
    assert first.series == again.series and other.series != first.series


def test_edit_and_revert_parameter_restores_identical_result():
    """Change C, rerun (must differ), change it back, rerun: identical to the original."""
    d = rlc()
    a = run_document(d)
    d.set_param("C1", "C", "1.01u"); b = run_document(d)
    d.set_param("C1", "C", "1u"); c = run_document(d)
    assert a.series == c.series and a.series != b.series


def test_core_simulation_reset_and_rerun_is_identical():
    """The core object's own lifecycle: run -> reset -> run on ONE Simulation gives identical vectors."""
    nl, cfg = rlc().netlist(), rlc().config()
    s = simcore.Simulation("det")
    s.build_circuit(nl["lines"])
    for m in nl["models"]: s.add_model(m)
    for line in nl["saves"]: s.add(line)
    s.configure(**cfg); s.observe(*nl["observables"])
    s.run(); first = {n: list(s.observable(n)) for n in nl["observables"]}
    assert s.state == "completed"
    s.reset(); assert s.state == "idle"
    s.run(); second = {n: list(s.observable(n)) for n in nl["observables"]}
    assert first == second
