import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
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


@pytest.fixture
def shown_window():
    """A shown MainWindow. Hidden, closed and deleted after the test: a window left alive keeps its shortcuts and timers."""
    from PySide6.QtWidgets import QApplication
    from app.window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow(); w.show(); w.canvas.setFocus()
    yield w
    w.hide(); w.close(); w.deleteLater()


@pytest.fixture
def extra_root(tmp_path):
    """tmp dir registered as a second component root; the global CATALOG is rebuilt from the defaults afterwards."""
    from app import catalog
    catalog.reload([catalog.BUILTIN_ROOT, tmp_path])
    yield tmp_path
    catalog.reload()
