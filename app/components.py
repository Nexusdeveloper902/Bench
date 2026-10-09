"""Component catalog + SI units + netlist builder. Pure Python: no Qt, no simcore.

Every part kind the app offers is one `Component` entry in CATALOG. Consumers (model, sim, GUI) read metadata
from here instead of branching on the kind; the only per-kind code is each entry's `emit` function, which turns
already-validated parameter values into SPICE device lines for the core's netlist-in Simulation API.
"""
import math
import re
from decimal import Decimal
from dataclasses import dataclass, field


class ModelError(ValueError):
    """User-fixable document problem (bad number, bad pin, bad file, ...)."""


# ---------------------------------------------------------------- SI units
PREFIXES = {"T": 12, "G": 9, "meg": 6, "Meg": 6, "MEG": 6, "M": 6, "k": 3, "K": 3,   # power of ten
            "m": -3, "u": -6, "\u00b5": -6, "\u03bc": -6, "n": -9, "p": -12, "f": -15}
UNIT_ALIASES = {"\u03a9": "Ω", "\u2126": "Ω", "ohm": "Ω", "ohms": "Ω", "Ohm": "Ω", "Ohms": "Ω",
                "F": "F", "H": "H", "V": "V", "A": "A", "s": "s", "Hz": "Hz", "hz": "Hz"}
_NUM = re.compile(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*?)\s*$")
EXAMPLES = "e.g. 1k, 10u, 4.7n, 2.2meg, 1e-6"


def parse_si(text, unit="", what="value"):
    """'4.7k' / '10 µF' / '1e-6' / '2.2meg' -> float. Case-sensitive SI prefixes: M = mega (also meg/MEG),
    m = milli, u/µ = micro. An optional trailing unit must match `unit`. Raises ModelError with a clear message."""
    s = str(text).strip()
    try:
        x = float(s)                        # plain numbers, incl. inf/nan (caught below)
    except ValueError:
        m = _NUM.fullmatch(s)
        if not m:
            raise ModelError(f"{what}: '{text}' is not a number ({EXAMPLES})")
        x, rest = Decimal(m.group(1)), m.group(2)
        if rest:
            if rest in UNIT_ALIASES:        # bare unit, no prefix: '5 V', '1 F' (farad, not femto)
                if UNIT_ALIASES[rest] != unit:
                    raise ModelError(f"{what}: unit '{rest}' does not fit; expected {unit or 'no unit'}")
                rest = ""
            else:
                pre = next((p for p in sorted(PREFIXES, key=len, reverse=True) if rest.startswith(p)), None)
                if pre is None:
                    raise ModelError(f"{what}: '{text}' is not a number ({EXAMPLES})")
                x = x.scaleb(PREFIXES[pre]); rest = rest[len(pre):].strip()   # exact: 10u -> 1e-05
            if rest and UNIT_ALIASES.get(rest) != unit:
                raise ModelError(f"{what}: unit '{rest}' does not fit; expected {unit or 'no unit'}")
        x = float(x)
    if not math.isfinite(x):
        raise ModelError(f"{what}: must be finite")
    return x


_FMT = [(1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"),
        (1e-12, "p"), (1e-15, "f")]


def format_si(x, unit="", digits=4):
    """1000 -> '1 kΩ' (unit 'Ω'), 1e-5 -> '10 µF'. Round-trips through parse_si to `digits` significant digits."""
    x = float(x)
    if x == 0 or not math.isfinite(x):
        return f"{x:g} {unit}".strip()
    scale, pre = next(((s, p) for s, p in _FMT if abs(x) >= s * (1 - 1e-12)), _FMT[-1])
    return f"{x / scale:.{digits}g} {pre}{unit}".strip()


# ---------------------------------------------------------------- metadata
@dataclass(frozen=True)
class Param:
    key: str                    # stored key in part["params"]
    label: str                  # human name, e.g. "Resistance"
    unit: str = ""              # SI unit symbol ("Ω", "F", "H", "V", "A", "s", "Hz") or ""
    default: str = "0"          # stored as text, exactly as a user would type it
    kind: str = "number"        # "number" (SI text) | "choice"
    choices: tuple = ()         # for kind == "choice"
    min: float = None           # bound (None = unbounded)
    min_exclusive: bool = False  # True: value must be > min, else >= min
    max: float = None
    when: tuple = ()            # ((other_key, value), ...): param applies only if all match

    def applies(self, params):
        return all(params.get(k) == v for k, v in self.when)

    def parse(self, text, owner=""):
        """Validated value: float for numbers, str for choices. Raises ModelError naming owner + key."""
        what = f"{owner} {self.key}".strip()
        if self.kind == "choice":
            if text not in self.choices:
                raise ModelError(f"{what}: '{text}' must be one of {', '.join(self.choices)}")
            return text
        x = parse_si(text, self.unit, what)
        if self.min is not None and (x <= self.min if self.min_exclusive else x < self.min):
            raise ModelError(f"{what}: must be {'>' if self.min_exclusive else '>='} {self.min:g}")
        if self.max is not None and x > self.max:
            raise ModelError(f"{what}: must be <= {self.max:g}")
        return x


@dataclass(frozen=True)
class Observable:
    name: str       # readable, '{name}' = part name, e.g. 'I({name})'
    vector: str     # ngspice vector, '{dev}' = lowercase SPICE device name, e.g. '@{dev}[i]'
    quantity: str   # "current" | "voltage"


@dataclass(frozen=True)
class Component:
    id: str                     # stable kind id, saved in files
    label: str                  # display name
    prefix: str                 # auto-name prefix (R -> R1, R2 ...)
    terminals: tuple            # terminal names, in SPICE node order
    pos: dict                   # terminal -> (x, y) in local unrotated coords, relative to the part anchor
    box: tuple                  # (x0, y0, w, h) body rectangle in local coords; rotation is about its centre
    symbol: str                 # symbol key for the GUI renderer
    params: tuple = ()          # Param...
    letter: str = None          # SPICE device letter; None = not a SPICE device (Ground)
    emit: object = None         # (dev, nodes, values) -> (lines, models)
    observables: tuple = ()     # Observable... (part-specific; V(part) across 2-terminal parts is generic)
    value_param: str = None     # param shown as the part's value label on the canvas
    ground: bool = False        # every terminal is the reference node '0'

    def param(self, key):
        return next(p for p in self.params if p.key == key)

    def defaults(self):
        return {p.key: p.default for p in self.params}

    def fields(self, params):
        """Param keys that apply to these values (e.g. PULSE fields only in PULSE mode), in display order."""
        return [p.key for p in self.params if p.applies(params)]

    def values(self, params, owner=""):
        """Parse every applicable param. Raises ModelError on the first bad one."""
        return {k: self.param(k).parse(params[k], owner) for k in self.fields(params)}

    def device(self, name):
        """SPICE device name for part `name` (the letter decides the device type: SW1 -> RSW1)."""
        return name if name.upper().startswith(self.letter) else self.letter + name


def _pins(a, b):   # left/right terminals of the standard 60 x 30 body
    return {a: (0, 15), b: (60, 15)}


def _r(f):          # numeric value as the core receives it
    return repr(float(f))


I_THROUGH = Observable("I({name})", "@{dev}[i]", "current")
I_DIODE = Observable("I({name})", "@{dev}[id]", "current")
I_SOURCE = Observable("I({name})", "i({dev})", "current")
POS = dict(min=0, min_exclusive=True)
LED_MODEL = ".model dled D(Is=1e-20 N=1.8 Rs=5)"   # red LED: Vf ~1.98 V at 10 mA
WAVE = {"DC": ["dc"], "PULSE": ["v1", "v2", "td", "tr", "tf", "pw", "per"], "SIN": ["vo", "va", "freq"]}


def _vsrc(dev, n, v):
    vals = " ".join(_r(v[k]) for k in WAVE[v["mode"]])
    return [f"{dev} {n[0]} {n[1]} DC {vals}" if v["mode"] == "DC" else f"{dev} {n[0]} {n[1]} {v['mode']}({vals})"], []


def _diode(dev, n, v):
    model = f"dmod_{dev.lower()}"
    return [f"{dev} {n[0]} {n[1]} {model}"], [f".model {model} D(Is={_r(v['Is'])} N={_r(v['N'])} Rs={_r(v['Rs'])})"]


def _p(key, label, unit, default, **kw):
    return Param(key, label, unit, default, **kw)


_VS = [_p("mode", "Waveform", "", "DC", kind="choice", choices=("DC", "PULSE", "SIN"))] + [
    _p(k, lab, u, d, when=(("mode", m),), **kw) for m, k, lab, u, d, kw in (
        ("DC", "dc", "Voltage", "V", "5", {}),
        ("PULSE", "v1", "Initial value", "V", "0", {}), ("PULSE", "v2", "Pulsed value", "V", "5", {}),
        ("PULSE", "td", "Delay", "s", "0", dict(min=0)), ("PULSE", "tr", "Rise time", "s", "1e-6", POS),
        ("PULSE", "tf", "Fall time", "s", "1e-6", POS), ("PULSE", "pw", "Pulse width", "s", "1e-3", POS),
        ("PULSE", "per", "Period", "s", "2e-3", POS),
        ("SIN", "vo", "Offset", "V", "0", {}), ("SIN", "va", "Amplitude", "V", "1", {}),
        ("SIN", "freq", "Frequency", "Hz", "1000", POS))]

CATALOG = {c.id: c for c in (
    Component("R", "Resistor", "R", ("a", "b"), _pins("a", "b"), (0, 0, 60, 30), "resistor",
              (_p("R", "Resistance", "Ω", "1000", **POS),), "R",
              lambda d, n, v: ([f"{d} {n[0]} {n[1]} {_r(v['R'])}"], []), (I_THROUGH,), "R"),
    Component("C", "Capacitor", "C", ("a", "b"), _pins("a", "b"), (0, 0, 60, 30), "capacitor",
              (_p("C", "Capacitance", "F", "1e-6", **POS), _p("ic", "Initial voltage", "V", "0")), "C",
              lambda d, n, v: ([f"{d} {n[0]} {n[1]} {_r(v['C'])} IC={_r(v['ic'])}"], []), (I_THROUGH,), "C"),
    Component("L", "Inductor", "L", ("a", "b"), _pins("a", "b"), (0, 0, 60, 30), "inductor",
              (_p("L", "Inductance", "H", "1e-3", **POS), _p("ic", "Initial current", "A", "0")), "L",
              lambda d, n, v: ([f"{d} {n[0]} {n[1]} {_r(v['L'])} IC={_r(v['ic'])}"], []), (I_THROUGH,), "L"),
    Component("V", "Voltage source", "V", ("p", "n"), _pins("p", "n"), (0, 0, 60, 30), "vsource",
              tuple(_VS), "V", _vsrc, (I_SOURCE,), "dc"),
    Component("BAT", "Battery", "V", ("p", "n"), _pins("p", "n"), (0, 0, 60, 30), "battery",
              (_p("dc", "Voltage", "V", "5"),), "V",
              lambda d, n, v: ([f"{d} {n[0]} {n[1]} DC {_r(v['dc'])}"], []), (I_SOURCE,), "dc"),
    Component("I", "Current source", "I", ("p", "n"), _pins("p", "n"), (0, 0, 60, 30), "isource",
              (_p("dc", "Current", "A", "1e-3"),), "I",
              lambda d, n, v: ([f"{d} {n[0]} {n[1]} DC {_r(v['dc'])}"], []), (), "dc"),
    Component("D", "Diode", "D", ("a", "k"), _pins("a", "k"), (0, 0, 60, 30), "diode",
              (_p("Is", "Saturation current", "A", "1e-14", **POS), _p("N", "Emission coefficient", "", "1", **POS),
               _p("Rs", "Series resistance", "Ω", "0", min=0)), "D", _diode, (I_DIODE,)),
    Component("GND", "Ground", "GND", ("g",), {"g": (0, 10)}, (-16, 8, 32, 24), "ground", ground=True),
    # SPICE letter decides the device, so LED reuses the D prefix (LED parts are named D1, D2 ...)
    Component("LED", "LED", "D", ("a", "k"), _pins("a", "k"), (0, 0, 60, 30), "led", (), "D",
              lambda d, n, v: ([f"{d} {n[0]} {n[1]} dled"], [LED_MODEL]), (I_DIODE,)),
    # Ideal-ish switch = a real resistor (Ron/Roff) chosen by `state`. A real S device would need the core
    # validator to treat S/W as DC-conducting (it reports NO-DC-PATH today); time-controlled switching is out of scope.
    Component("SW", "Switch", "SW", ("a", "b"), _pins("a", "b"), (0, 0, 60, 30), "switch",
              (_p("state", "State", "", "open", kind="choice", choices=("open", "closed")),
               _p("ron", "On resistance", "Ω", "0.01", **POS), _p("roff", "Off resistance", "Ω", "1e9", **POS)), "R",
              lambda d, n, v: ([f"{d} {n[0]} {n[1]} {_r(v['ron'] if v['state'] == 'closed' else v['roff'])}"], []),
              (I_THROUGH,), "state"),
)}


def get(kind):
    try:
        return CATALOG[kind]
    except KeyError:
        raise ModelError(f"unknown part kind {kind}") from None


def summary(kind, params):
    """Canvas value label, e.g. '1 kΩ', '5 V', 'PULSE', 'open'. Shows raw text if it does not parse."""
    c = get(kind)
    if not c.value_param:
        return ""
    p = c.param(c.value_param)
    if not p.applies(params):    # e.g. a PULSE source has no 'dc': show the selector value instead
        p = next(q for q in c.params if q.kind == "choice")
    text = params.get(p.key, "")
    if p.kind == "choice":
        return text
    try:
        return format_si(parse_si(text, p.unit), p.unit)
    except ModelError:
        return text


ROTATIONS = (0, 90, 180, 270)


def rotate(pt, center, rot):
    """Rotate local point `pt` clockwise (screen coords, y down) by rot in ROTATIONS about `center`."""
    x, y = pt[0] - center[0], pt[1] - center[1]
    for _ in range((rot // 90) % 4):
        x, y = -y, x
    return (center[0] + x, center[1] + y)


def terminal_offset(kind, terminal, rot=0):
    """Terminal position relative to the part anchor (x, y) after rotation."""
    c = get(kind)
    x0, y0, w, h = c.box
    return rotate(c.pos[terminal], (x0 + w / 2, y0 + h / 2), rot)


# ---------------------------------------------------------------- netlist builder
def build_netlist(parts, nets):
    """parts: {name: {"kind", "params", ...}} in creation order; nets: {"Part.term": net}.
    -> {"lines", "models", "saves", "observables", "derived", "signals"}; raises ModelError on a bad value.

    observables: ngspice vector names to read from the core. derived: {'v(R1)': (netA, netB)} across-part
    voltages that sim.py computes by subtracting two core node series. signals: readable names for all of them,
    [{"name", "vector", "quantity", "unit", "kind": "node"|"across"|"current", "part", "pins"}]."""
    if not parts:
        raise ModelError("circuit is empty")
    lines, models, cur, derived, signals = [], [], [], {}, []
    used = set()
    for name, d in parts.items():
        c = get(d["kind"])
        if c.letter is None:
            continue
        nodes = [nets[f"{name}.{t}"] for t in c.terminals]
        used |= set(nodes)
        dev = c.device(name)
        ls, ms = c.emit(dev, nodes, c.values(d["params"], name))
        lines += ls
        models += [m for m in ms if m not in models]
        if len(nodes) == 2:
            derived[f"v({name})"] = tuple(nodes)
            signals.append({"name": f"V({name})", "vector": f"v({name})", "quantity": "voltage", "unit": "V",
                            "kind": "across", "part": name, "pins": [f"{name}.{t}" for t in c.terminals]})
        for o in c.observables:
            vec = o.vector.format(dev=dev.lower())
            cur.append(vec)
            signals.append({"name": o.name.format(name=name), "vector": vec, "quantity": o.quantity,
                            "unit": "A" if o.quantity == "current" else "V", "kind": "current", "part": name,
                            "pins": [f"{name}.{c.terminals[0]}"]})
    if not lines:
        raise ModelError("circuit has no components")
    node_obs = [f"v({n})" for n in sorted(used - {"0"}, key=lambda s: int(s[1:]))]
    members = {}
    for pin, n in nets.items():
        members.setdefault(n, []).append(pin)
    signals = [{"name": f"V({v[2:-1]})", "vector": v, "quantity": "voltage", "unit": "V", "kind": "node",
                "part": None, "pins": members[v[2:-1]]} for v in node_obs] + signals
    # vectors only reachable through '.save' (device internals, written @dev[x]); i(vsrc) is always saved
    saves = [f".save all {v}" for v in cur if v.startswith("@")]
    # observable order (kept from the pre-catalog app): nodes, source currents, device currents
    obs = node_obs + [v for v in cur if not v.startswith("@")] + [v for v in cur if v.startswith("@")]
    return {"lines": lines, "models": models, "saves": saves, "observables": obs, "derived": derived,
            "signals": signals}
