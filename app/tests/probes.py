"""Reliability probes for scripts/test stress, loaded with `-p app.tests.probes`. Not part of a normal run.

* Qt cross-thread warnings (timers/painters/events used from the wrong thread) fail the test they occur in.
* A garbage collection starting on a non-main thread while a QApplication exists fails the test (the 73cb002
  segfault class: collecting Qt wrappers off the GUI thread).
* env BENCH_SWITCH_INTERVAL=<s>    sys.setswitchinterval (tiny values force GIL hand-offs at awkward points)
* env BENCH_TEST_GC_THRESHOLD=a,b,c  is applied by conftest (every pytest process, child runs included), not here
"""
import gc
import os
import re
import sys
import threading

import pytest

_QT_BAD = re.compile(r"Timers cannot be|different thread|Cannot send events|QBasicTimer|QPainter|"
                     r"Cannot create children for a parent that is in a different thread|QObject::.*thread", re.I)
_hits = []        # (message, thread name) from the Qt message handler
_gc_hits = []     # (thread name, nodeid)
_current = [""]
# This test starts gc on a thread named "other" on purpose, to prove the app survives it. Exact nodeid only:
# no file-wide exemptions (test_live.py hits are findings, not noise).
_GC_EXEMPT = "app/tests/test_live_ui.py::test_a_dropped_windows_canvas_scene_is_deleted_on_the_gui_thread"


def pytest_configure(config):
    if (v := os.environ.get("BENCH_SWITCH_INTERVAL")):
        sys.setswitchinterval(float(v))
    from PySide6.QtCore import qInstallMessageHandler

    def handler(mode, ctx, msg):
        if _QT_BAD.search(msg):
            _hits.append((msg, threading.current_thread().name))
        sys.stderr.write(msg + "\n")
    config._probe_handler = handler       # keep a reference; PySide does not
    qInstallMessageHandler(handler)

    def on_gc(phase, info):
        if phase == "start" and threading.current_thread() is not threading.main_thread():
            from PySide6.QtCore import QCoreApplication
            if QCoreApplication.instance() is not None and _current[0] != _GC_EXEMPT:
                _gc_hits.append((threading.current_thread().name, _current[0]))
    gc.callbacks.append(on_gc)
    config._probe_gc = on_gc


def pytest_unconfigure(config):
    if getattr(config, "_probe_gc", None) in gc.callbacks:
        gc.callbacks.remove(config._probe_gc)


@pytest.fixture(autouse=True)
def _probes(request):
    _current[0] = request.node.nodeid
    yield
    hits, gcs = list(_hits), list(_gc_hits)
    _hits.clear(); _gc_hits.clear()
    problems = [f"Qt warning on thread {th!r}: {m}" for m, th in hits]
    problems += [f"gc ran on non-main thread {th!r} (during {n}) with a QApplication alive" for th, n in gcs]
    if problems:
        pytest.fail("probe: " + "; ".join(problems), pytrace=False)
