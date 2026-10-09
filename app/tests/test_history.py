"""Headless undo/redo: every op kind restores Document.to_dict() exactly."""
import copy
from app.model import Document
from app.history import History


def snap(d): return copy.deepcopy(d.to_dict())


def check(op, doc=None):
    d = doc or Document.led_example(); h = History()
    before = snap(d)
    with h.change(d): op(d)
    after = snap(d)
    assert after != before
    assert h.undo(d) and snap(d) == before
    assert h.redo(d) and snap(d) == after
    assert h.undo(d) and snap(d) == before
    assert not h.undo(d)


def test_place(): check(lambda d: d.add_part("R", 10, 20))
def test_place_on_empty(): check(lambda d: d.add_part("BAT", 0, 0), Document())
def test_delete_part_removes_wires(): check(lambda d: d.remove_part("R1"))
def test_move(): check(lambda d: d.parts["R1"].update(x=500, y=300))
def test_connect(): check(lambda d: d.connect("R1.a", "D1.k"))
def test_disconnect_pin(): check(lambda d: d.disconnect("D1.k"))
def test_delete_wire(): check(lambda d: d.remove_wire(("V1.p", "R1.a")))
def test_param(): check(lambda d: d.set_param("R1", "R", "470"))


def test_unchanged_not_recorded():
    d = Document.led_example(); h = History()
    with h.change(d): d.set_param("R1", "R", "330")
    assert not h.can_undo()


def test_new_change_clears_redo_and_stack_orders():
    d = Document(); h = History()
    with h.change(d): d.add_part("R")
    with h.change(d): d.add_part("C")
    h.undo(d); assert list(d.parts) == ["R1"] and h.can_redo()
    with h.change(d): d.add_part("L")
    assert not h.can_redo() and list(d.parts) == ["R1", "L1"]
    h.undo(d); h.undo(d); assert d.parts == {}


def test_undo_keeps_typed_tstop():
    d = Document(); h = History()
    with h.change(d): d.add_part("R")
    d.tstop = "9e-3"; h.undo(d)
    assert d.tstop == "9e-3"
