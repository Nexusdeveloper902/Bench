"""PySide6/shiboken 6.12: a Python override that raises can free an UNRELATED live override function.

An event() override raises on one posted event, leaving the exception pending inside the native call. A queued
`show` of a new QLabel subclass with a Python sizeHint then runs in the same sendPostedEvents pass, and shiboken's
Sbk_GetPyOverride (error-already-pending branch) Py_XDECREFs a borrowed pointer. We print the sizeHint function's
refcount before/after; expect a drop (and eventually a use-after-free crash).
Run: QT_QPA_PLATFORM=offscreen python3 repro.py
"""
import sys
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget

app = QApplication([])
sys.excepthook = lambda *a: print("excepthook:", a[1])


class Bad(QWidget):
    def event(self, e):
        if e.type() == QEvent.User: raise RuntimeError("boom")
        return super().event(e)


def make_label_class():
    """a fresh QLabel subclass each time, so its sizeHint override has never been looked up yet"""
    def sizeHint(self): return super(cls, self).sizeHint()
    cls = type("Lbl", (QLabel,), {"sizeHint": sizeHint})
    return cls, sizeHint


drops = 0
for i in range(200):
    cls, fn = make_label_class()
    before = sys.getrefcount(fn)
    bad = Bad(); lbl = cls("x")
    QCoreApplication.postEvent(bad, QEvent(QEvent.User))                  # raises inside the pass
    QMetaObject.invokeMethod(lbl, "show", Qt.QueuedConnection)           # queued show -> sizeHint lookup, error pending
    QCoreApplication.sendPostedEvents(None, 0)
    QCoreApplication.processEvents()
    after = sys.getrefcount(fn)
    if i < 3 or after != before: print(f"iteration {i}: sizeHint refcount {before} -> {after}")
    drops += after < before
    lbl.deleteLater(); bad.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
print(f"refcount dropped in {drops}/200 iterations")
