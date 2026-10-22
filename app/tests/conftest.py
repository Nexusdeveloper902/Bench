import os, sys, tempfile
# Isolation, decided before any Qt/app import so nothing can reach the developer's real session or config:
# - force the offscreen platform (setdefault let a shell's QT_QPA_PLATFORM=wayland;xcb open real windows) and drop
#   QT_QPA_PLATFORMTHEME (a desktop platform theme such as kde loads plugins and restyles the offscreen app);
# - point every XDG base dir at a per-process temp dir: QSettings("Bench", "Bench") (theme.py, window.py) is
#   NativeFormat, i.e. $XDG_CONFIG_HOME/Bench/Bench.conf, which the tests used to overwrite in ~/.config.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.pop("QT_QPA_PLATFORMTHEME", None)
_HOME = tempfile.mkdtemp(prefix="bench-test-xdg-")
for _k, _d in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
    os.environ[_k] = os.path.join(_HOME, _d)
os.environ["XDG_CONFIG_DIRS"] = os.path.join(_HOME, "config-dirs")
import atexit, shutil
atexit.register(shutil.rmtree, _HOME, ignore_errors=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import gc, json, weakref
import shiboken6
import pytest


def _toml(d):
    """Serialize a definition dict to TOML: top-level scalars/lists, then [[pins]], [[params]], [presentation], [sim.*].
    Scalars go through json.dumps (valid TOML); tables become inline tables."""
    def j(v):
        if isinstance(v, dict): return "{" + ", ".join(f"{json.dumps(k)} = {j(x)}" for k, x in v.items()) + "}"
        if isinstance(v, list): return "[" + ", ".join(map(j, v)) + "]"
        return json.dumps(v, ensure_ascii=False)
    out = [f"{k} = {j(v)}" for k, v in d.items() if k not in ("pins", "params", "presentation", "sim") or v == []]   # `pins = []` needs the key
    for k in ("pins", "params"):
        for t in d.get(k, ()):
            out += [f"[[{k}]]"] + [f"{a} = {j(b)}" for a, b in t.items()]
    if "presentation" in d:
        out += ["[presentation]"] + [f"{a} = {j(b)}" for a, b in d["presentation"].items()]
    for dom, sec in d.get("sim", {}).items():
        out += [f"[sim.{dom}]"] + [f"{a} = {j(b)}" for a, b in sec.items()]
    return "\n".join(out) + "\n"


def minimal_def(id="TESTPART"):
    """A valid electrical definition (a resistor-like 2-pin part) as a dict; tests mutate it."""
    return {"format": 1, "id": id, "version": 1, "name": f"Test {id}", "category": "Electrical", "domain": "electrical",
            "capabilities": ["interface", "sim:electrical"],
            "pins": [{"id": "a", "at": [0, 15]}, {"id": "b", "at": [60, 15]}],
            "params": [{"key": "R", "label": "Resistance", "unit": "Ω", "default": "100"}],
            "presentation": {"box": [0, 0, 60, 30]},
            "sim": {"electrical": {"device": "R", "line": "{dev} {a} {b} {R}"}}}


def write_def(root, id="TESTPART", mutate=None, reload=True, **top):
    """Write root/<id>/component.toml from minimal_def with `top` keys replaced and `mutate(dict)` applied; reload
    the catalog over [builtin, root] unless reload=False. -> the file path."""
    from app import catalog
    d = minimal_def(id); d.update(top)
    if mutate: mutate(d)
    f = root / id.lower() / "component.toml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_toml(d), encoding="utf-8")
    if reload: catalog.reload([catalog.BUILTIN_ROOT, root])
    return f


def wait_for(cond, timeout=3000):
    """QTest.qWaitFor (not in PySide6): pump events until cond() is true or timeout ms pass -> cond()'s final value."""
    import time
    from PySide6.QtTest import QTest
    end = time.monotonic() + timeout / 1000
    while not cond() and time.monotonic() < end: QTest.qWait(5)
    return bool(cond())


def destroy(w):
    """Close and really delete a window now. deleteLater() alone only queues the delete for the next event-loop
    turn, so a test that ends without one leaves its whole widget tree alive (and restyled by every later setStyleSheet)."""
    from PySide6.QtCore import QCoreApplication, QEvent
    w.hide(); w.close(); w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def shown_window():
    """A shown MainWindow. Hidden, closed and deleted after the test: a window left alive keeps its shortcuts and timers."""
    from PySide6.QtWidgets import QApplication
    from app.window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow(); w.show(); w.canvas.setFocus()
    yield w
    destroy(w)


@pytest.fixture
def new_window():
    """Factory for MainWindows a test builds itself: every one is destroyed after the test, pass or fail."""
    from PySide6.QtWidgets import QApplication
    from app.window import MainWindow
    QApplication.instance() or QApplication([])
    made = []
    def make():
        made.append(MainWindow()); return made[-1]
    yield make
    for w in made:
        if shiboken6.isValid(w): w.end_live(); destroy(w)


@pytest.fixture
def extra_root(tmp_path):
    """tmp dir registered as a second component root; the global CATALOG is rebuilt from the defaults afterwards."""
    from app import catalog
    catalog.reload([catalog.BUILTIN_ROOT, tmp_path])
    yield tmp_path
    catalog.reload()


# ---------------------------------------------------------------- per-test leak guard
# Always on and cheap. A test that leaves state behind fails ITSELF, naming what leaked (instead of a random later
# test failing, or the whole suite slowing down). The guard cleans up what it found, so one leak cannot cascade.
_SESSIONS = weakref.WeakSet()    # every LiveSession ever built; a live run holds the process-wide engine until it ends
_BASE = {}


def _gc_baseline():
    """The gc thresholds the session was started with (a stress plugin may lower them): BENCH_TEST_GC_THRESHOLD="a,b,c"
    declares them explicitly, otherwise whatever they were at session start."""
    env = os.environ.get("BENCH_TEST_GC_THRESHOLD")
    return tuple(int(x) for x in env.split(",")) if env else _BASE["gc"]


def pytest_sessionstart(session):
    _BASE["gc"] = gc.get_threshold()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    out = yield
    if out.get_result().failed: item.stash.setdefault(_FAILED, []).append(call.when)


_FAILED = pytest.StashKey[list]()


@pytest.fixture(autouse=True)
def _leak_guard(request):
    from app import catalog, sim
    if not _BASE.get("patched"):
        init = sim.LiveSession.__init__
        def tracked(self, *a, **k):
            _SESSIONS.add(self); init(self, *a, **k)
        sim.LiveSession.__init__ = tracked
        _BASE["patched"] = True
        _BASE["catalog"] = (list(catalog.default_roots()), set(catalog.CATALOG.defs), len(catalog.CATALOG.problems))
    yield
    leaks = []
    if request.node.stash.get(_FAILED, None):   # a failing test's traceback frames hold its locals (a live session, a window)
        sys.last_type = sys.last_value = sys.last_traceback = None
        for a in ("last_exc",):
            if hasattr(sys, a): setattr(sys, a, None)
        gc.collect()
    if not gc.isenabled(): leaks.append("gc left disabled (a live run's worker never finished?)"); gc.enable()
    if gc.get_threshold() != _gc_baseline(): leaks.append(f"gc thresholds changed to {gc.get_threshold()}"); gc.set_threshold(*_gc_baseline())
    for s in list(_SESSIONS):
        r = s._run
        if r is not None and r.state in ("running", "paused"):
            leaks.append(f"live run still holding the engine ({r.state})"); s.cancel(); s.close()
    from PySide6.QtWidgets import QApplication
    if QApplication.instance():
        from PySide6.QtCore import QCoreApplication, QEvent
        from app.window import MainWindow
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)   # windows the test already deleteLater()'d are gone
        for w in QApplication.topLevelWidgets():
            if not shiboken6.isValid(w): continue   # owned by a window destroyed earlier in this loop
            if w.isVisible() or isinstance(w, MainWindow):   # a hidden MainWindow still holds its whole widget tree
                leaks.append(f"{'visible' if w.isVisible() else 'hidden'} top-level {type(w).__name__} {w.windowTitle()!r} never destroyed")
                if isinstance(w, MainWindow): w.end_live()
                destroy(w)
    now = (list(catalog.CATALOG.roots), set(catalog.CATALOG.defs), len(catalog.CATALOG.problems))
    if now != _BASE["catalog"]:
        leaks.append("global CATALOG differs from the default (a reload over other roots was not undone)"); catalog.reload()
    assert not leaks, "test leaked: " + "; ".join(leaks)
