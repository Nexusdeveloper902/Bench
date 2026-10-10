"""Circuits whose emitted netlist is pinned in fixtures/netlist_golden.json (captured from the pre-catalog
components.build_netlist). Built only through the Document API so old and new code build the same circuits."""
import json
from pathlib import Path
from app import electrical
from app.catalog import CATALOG
from app.model import Document

FIXTURE = Path(__file__).with_name("fixtures") / "netlist_golden.json"


def _grounded(*parts):
    """Each part is (kind, params); every part's last pin goes to the one Ground."""
    d = Document()
    for k, p in parts: d.add_part(k, **p)
    d.add_part("GND")
    for n in list(d.parts)[:-1]: d.connect(d.pins(n)[-1], "GND1.g")
    return d


def cases():
    pulse = dict(mode="PULSE", v1=0, v2=3.3, td="1u", tr="2u", tf="5u", pw="1m", per="3m")
    kinds = ("V", "R", "C", "L", "I", "D", "LED", "SW", "BAT")
    out = {
        "all_kinds_defaults": _grounded(*[(k, {}) for k in kinds]),
        "v_dc": _grounded(("V", dict(dc="12"))),
        "v_pulse": _grounded(("V", pulse)),
        "v_sin": _grounded(("V", dict(mode="SIN", vo="0.5", va="1", freq="2k"))),
        "sw_open": _grounded(("SW", {})),
        "sw_closed": _grounded(("SW", dict(state="closed", ron="0.1", roff="1meg"))),
        "d_custom_and_leds": _grounded(("D", dict(Is="2e-14", N="1.5", Rs="10")), ("D", {}), ("LED", {}), ("LED", {})),
        "c_l_initial": _grounded(("C", dict(C="4.7n", ic="2")), ("L", dict(L="10m", ic="1m")), ("R", dict(R="2.2meg"))),
        "bat_i": _grounded(("BAT", dict(dc="9")), ("I", dict(dc="5m"))),
        "led_example": Document.led_example(),
        "blink_example": Document.blink_example(),
    }
    return out


def snapshot():
    """The oracle's shape: electrical.build_netlist output (no 'excluded' key), as test_template_netlist_matches_golden compares it."""
    return {n: json.loads(json.dumps(electrical.build_netlist(d.parts, d.nets(), CATALOG))) for n, d in cases().items()}


if __name__ == "__main__":
    import sys
    if "--bless" not in sys.argv:   # regenerating is a deliberate act: only for an intended netlist change
        sys.exit(f"refusing to overwrite {FIXTURE} without --bless (python3 -m app.tests.golden_netlists --bless)")
    FIXTURE.write_text(json.dumps(snapshot(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE}")
