"""SI units + parameter metadata + rotation math. Pure Python: no Qt, no benchcore.

Component definitions live in app/library/**/component.toml and are loaded by app.catalog; SPICE rendering is
app.electrical. This module only holds what both of them (and the model) share."""
import math
import re
from decimal import Decimal
from dataclasses import dataclass


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
    kind: str = "number"        # "number" (SI text) | "choice" | "points" (time-value pairs, e.g. a PWL waveform)
    choices: tuple = ()         # for kind == "choice"
    min: float = None           # bound (None = unbounded)
    min_exclusive: bool = False  # True: value must be > min, else >= min
    max: float = None
    when: tuple = ()            # ((other_key, value), ...): param applies only if all match
    help: str = ""              # one-line hint (tooltip)
    scale: str = ""             # live-run slider: "log" | "linear" | "" (log for Ω, else linear)

    def slider(self, value):
        """(lo, hi, log): the live-run slider range for a number param starting at `value`. The ends are min/max
        where the package gives them; a log slider with no positive min starts six decades below max. A missing end
        is taken around value (x/÷100 on a log slider, ±2|value| or ±1 on a linear one). value is always inside."""
        log = (self.scale or ("log" if self.unit == "Ω" else "linear")) == "log" and value > 0
        lo, hi = self.min, self.max
        if log:
            if lo is None or lo <= 0:
                lo = hi / 1e6 if hi is not None else value / 100
            hi = hi if hi is not None else value * 100
        else:
            m = 2 * abs(value) or 1.0
            lo, hi = (-m if lo is None else lo), (m if hi is None else hi)
        return min(lo, value), max(hi, value), log

    def applies(self, params):
        return all(params.get(k) == v for k, v in self.when)

    def parse(self, text, owner=""):
        """Validated value: float for numbers, str for choices, and for points the pairs as SPICE text ("0.0 0.0 0.001
        5.0"; times in s, strictly increasing from >= 0; min/max bound the values). Raises ModelError naming owner + key."""
        what = f"{owner} {self.key}".strip()
        if self.kind == "choice":
            if text not in self.choices:
                raise ModelError(f"{what}: '{text}' must be one of {', '.join(self.choices)}")
            return text
        if self.kind == "points":
            pairs = [q.split() for q in re.split(r"[,;]", str(text)) if q.strip()]
            if len(pairs) < 2 or any(len(q) != 2 for q in pairs):
                raise ModelError(f"{what}: write two or more 'time value' pairs separated by commas, e.g. 0 0, 1m 5")
            pts = [(parse_si(t, "s", f"{what} time"), Param(self.key, self.label, self.unit, min=self.min,
                    min_exclusive=self.min_exclusive, max=self.max).parse(v, owner)) for t, v in pairs]
            times = [t for t, _ in pts]
            if times[0] < 0 or any(b <= a for a, b in zip(times, times[1:])):
                raise ModelError(f"{what}: times must start at 0 or later and strictly increase")
            return " ".join(f"{t!r} {v!r}" for t, v in pts)
        x = parse_si(text, self.unit, what)
        if self.min is not None and (x <= self.min if self.min_exclusive else x < self.min):
            raise ModelError(f"{what}: must be {'>' if self.min_exclusive else '>='} {self.min:g}")
        if self.max is not None and x > self.max:
            raise ModelError(f"{what}: must be <= {self.max:g}")
        return x


ROTATIONS = (0, 90, 180, 270)


def rotate(pt, center, rot):
    """Rotate local point `pt` clockwise (screen coords, y down) by rot in ROTATIONS about `center`."""
    x, y = pt[0] - center[0], pt[1] - center[1]
    for _ in range((rot // 90) % 4):
        x, y = -y, x
    return (center[0] + x, center[1] + y)
