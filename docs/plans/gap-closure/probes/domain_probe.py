"""G29 probe (W3): what ngspice 47 does with out-of-domain B-source expressions (the cases the interval pass reports).

One .op deck per expression: `B1 n 0 V=<expr>` with `R1 n 0 1k`; prints v(n) and the run log's error lines.
Run: python3 domain_probe.py   (needs ngspice on PATH)
"""
import os
import subprocess
import tempfile

CASES = [
    "sqrt(-4)", "ln(-1)", "log(0)", "log10(-10)", "1/0", "1/(x-1)", "exp(710)", "exp(100)", "exp(-800)",
    "pow(-2,0.5)", "pow(-2,2)", "pow(0,-1)", "pow(0,0.5)", "(-2)^0.5", "0^-1", "pwr(-2,0.5)", "1e200*1e200",
    "asin(2)", "acos(-2)", "acosh(0.5)", "atanh(2)", "tan(1.5707963267948966)", "5%0",
    "pow(2,0.5)", "sqrt(4)", "ln(2)",
    # precedence and associativity the interval parser must match
    "-2^2", "2^3^2", "8/2/2", "1-2-3", "2*3%4", "1<2 ? 5 : 6", "1>2 ? 5 : 6", "3>2 && 1>2", "1 || 0", "!0", "-x^2",
    "2**3", "1e3+1k", "10meg/1meg", "1.5e-3*2", "u(-1)", "u(1)", "uramp(-3)", "min(2,3)", "max(2,3)", "sgn(-4)",
    "exp(200)", "exp(227)", "exp(228)", "exp(300)", "exp(500)", "exp(709)", "1e150*1e150", "1e300*10", "pow(10,300)",
    "pow(10,400)", "10^400", "1e308*10", "sinh(300)", "sinh(800)", "1/1e-300", "5/0.0", "pow(2,-2000)",
    "abs(-4)", "floor(2.7)", "ceil(2.2)", "nint(2.5)", "ternary_fcn(1,5,6)", "ln(0)", "log(-1)", "sinh(2)",
]


def run(expr):
    deck = f"""probe
.param x=1
B1 n 0 V={expr}
R1 n 0 1k
.control
set noaskquit
op
print v(n)
.endc
.end
"""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "d.cir")
        with open(p, "w") as f:
            f.write(deck)
        r = subprocess.run(["ngspice", "-b", p], capture_output=True, text=True)
    out = r.stdout + r.stderr
    val = next((l.split("=")[1].strip() for l in out.splitlines() if l.strip().startswith("v(n)")), "-")
    msgs = [l.strip() for l in out.splitlines()
            if any(k in l.lower() for k in ("error", "warning", "illegal", "singular", "nan", "inf", "fatal"))]
    return val, msgs[:2]


if __name__ == "__main__":
    for e in CASES:
        v, m = run(e)
        print(f"{e:28} -> v(n) = {v:14} {m}")
