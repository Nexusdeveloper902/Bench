"""The conftest leak guard fails a test in which an exception escapes a Qt virtual override (shiboken 6.12 can then
free a live override function: experiments/pyside-override-refcount). Proven by running a tiny test in a child pytest."""
import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INNER = '''
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

class W(QWidget):
    def event(self, e):
        if e.type() == QEvent.User: raise KeyError("boom-in-override")
        return super().event(e)

def test_inner():
    QApplication.instance() or QApplication([])
    w = W()
    QCoreApplication.postEvent(w, QEvent(QEvent.User))
    QCoreApplication.processEvents()          # event-loop pass: nothing in Python to receive the exception
    w.deleteLater()
'''


def test_an_exception_escaping_an_override_fails_the_test(tmp_path):
    (tmp_path / "test_inner.py").write_text(INNER)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "app.tests.conftest", "-p", "no:cacheprovider", "--rootdir", str(tmp_path),
                        str(tmp_path / "test_inner.py")], cwd=ROOT, capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "exception escaped a Qt override: KeyError: 'boom-in-override' (in W.event test_inner.py" in out, out
