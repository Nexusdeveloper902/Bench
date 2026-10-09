"""Undo/redo as Document.to_dict() snapshots. No Qt, no simcore. tstop/tstep are not history (typing there is
never undone and undo never rewinds them)."""
import copy
from contextlib import contextmanager
from .model import Document

LIMIT = 200


def _state(doc):
    d = copy.deepcopy(doc.to_dict()); d.pop("simulation"); return d


def _restore(doc, state):
    new = Document.from_dict({**state, "simulation": doc.sim_config()})
    new.path = doc.path
    doc.__dict__.update(new.__dict__)   # in place: window/test references to doc stay valid


class History:
    def __init__(self):
        self.undo_stack, self.redo_stack, self._pending = [], [], None

    def clear(self):
        self.undo_stack.clear(); self.redo_stack.clear(); self._pending = None

    def begin(self, doc):
        self._pending = _state(doc)

    def end(self, doc):
        """Record the change since begin() iff the document actually changed. Returns True if recorded."""
        before, self._pending = self._pending, None
        if before is None or before == _state(doc):
            return False
        self.undo_stack.append(before); del self.undo_stack[:-LIMIT]; self.redo_stack.clear()
        return True

    @contextmanager
    def change(self, doc):
        self.begin(doc)
        try:
            yield
        finally:
            self.end(doc)

    def can_undo(self): return bool(self.undo_stack)
    def can_redo(self): return bool(self.redo_stack)

    def undo(self, doc):
        if not self.undo_stack: return False
        self.redo_stack.append(_state(doc)); _restore(doc, self.undo_stack.pop()); return True

    def redo(self, doc):
        if not self.redo_stack: return False
        self.undo_stack.append(_state(doc)); _restore(doc, self.redo_stack.pop()); return True
