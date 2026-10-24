# PySide override refcount bug (for an upstream report)

A Python override of a Qt virtual that raises leaves the exception pending inside the native call. In shiboken
`Sbk_GetPyOverride` (PySide6/shiboken6 6.12.0, Python 3.14), the branch taken while an error is pending does
`Py_XDECREF` on a BORROWED pointer to an override function of an unrelated class. Its class still references it, so
a later lookup (`getOverride` -> `method_dealloc`) is a use-after-free.

`repro.py` (run with `QT_QPA_PLATFORM=offscreen python3 repro.py`): an `event()` override raises on one posted event;
a queued `show` of a fresh QLabel subclass with a Python `sizeHint` runs in the same pass.

Observed here (PySide6 6.12.0, Python 3.14, Linux, offscreen), 3/3 runs: the `sizeHint` function's refcount drops
by one (3 -> 2) in every iteration; at iteration 154 the function is freed (refcount reads 4294967295, the freed
object) and the process dies with SIGSEGV (exit 139). Use `python3 -u` to see the output before the crash.

Bench consequence: any exception escaping a Qt override is treated as a memory-corruption hazard; `app/tests/conftest.py` fails the test.
