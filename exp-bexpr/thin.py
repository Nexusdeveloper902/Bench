"""Thin validated expression layer -> native ngspice B-source text.

Scope (justified by exp-bexpr evidence):
  R1 clamp: provide clamp() -> min(max()) because limit() is silently wrong [E].
  R2 spellings: and/or/not words -> &&/||/! because &/| fail cryptic [E].
  R3 allowlist: reject IF/int/table/poly/stp/white/idt/sdt/atan2/arcan/TEMP Gauss-etc
     BEFORE sim with a helpful message; gauss() segfaults ngspice [E].
  R4 node checking: V(typo) silently creates floating nodes -> convergence walls [E].
  R5 static traps: literal div-by-zero (1/0 -> 1e32 silent), self-reference B loops,
     exp() of huge args; warn/error pre-sim [E].
  R6 smoothed corners: smax/smin helpers (sqrt-eps) for feedback stability [E-indep].
NOT in scope: topology (series vs shunt), new semantics, scripting. Output is
plain B-source text, one device per line, with `* [tag]` comments for traceability.
"""
import math, re

_NUMVAL = re.compile(r'^[0-9][0-9.eE+-]*(meg|k|u|n|p|g|m|t|f)?$', re.I)

def _numval(v, what):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return
    if isinstance(v, str) and _NUMVAL.match(v.strip()):
        return
    raise LayerError(f"{what} value {v!r} is not numeric/scaled (injection guard)")

IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
# allowlisted pure functions, lowered 1:1 to ngspice names
CALLS_1TO1 = {'exp', 'ln', 'log', 'log10', 'sqrt', 'sin', 'cos', 'tan',
              'asin', 'acos', 'atan', 'sinh', 'cosh', 'tanh', 'abs',
              'min', 'max', 'floor', 'ceil', 'sgn', 'u', 'uramp', 'pwl',
              'pow', 'ddt'}
# NOTE: 'pwr' deliberately NOT allowlisted: pwr(-2,2) = -4 (sign-preserving)
# while pow(-2,2) = +4. Agents conflate them; rejected with guidance [E-adv].
# rejected with specific guidance
REJECT = {
    'if': 'use ["tern",cond,a,b] (ngspice has no if(); ternary ?: works)',
    'int': 'unsupported; use floor()/ceil()',
    'table': 'unsupported in B expr; use pwl(x,...)',
    'poly': 'unsupported in B expr; use explicit arithmetic or pwl()',
    'stp': 'unsupported; use u()/uramp()',
    'step': 'unsupported; use u()/uramp()',
    'idt': 'no integrator in B expr; use C + current-source state node (see recipe)',
    'sdt': 'unsupported; see idt',
    'atan2': 'unsupported; use atan()',
    'arctan': 'unsupported; use atan()',
    'temp': 'bare TEMP undefined; use ["temper"] (Celsius)',
    'tnom': 'unsupported bare; thread as a param instead',
    'white': 'unsupported noise source in B expr',
    'gauss': 'REFUSED: gauss() segfaults ngspice-47 in B expr (verified)',
    'limit': 'limit() is silently wrong in B expr (verified; 2-arg const form even varies run to run); use ["clamp",x,lo,hi]',
    'pwr': 'pwr() preserves input sign (pwr(-2,2)=-4) unlike pow() (pow(-2,2)=+4); use ["call","pow",...] explicitly',
    'foo': '(unknown function)',
}

class LayerError(Exception):
    pass

def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)

def validate_call(name):
    n = name.lower()
    if n in CALLS_1TO1:
        return n
    if n in REJECT:
        raise LayerError(f"function '{name}' rejected: {REJECT[n]}")
    raise LayerError(f"function '{name}' not in allowlist {sorted(CALLS_1TO1)}; refusing to guess")

def lower_expr(e, nodes, params, dev_pins=(), senses=frozenset()):
    """Lower AST to ngspice expression string. nodes: declared set. params: dict.
    senses: names of Vsense devices legal inside ["i",...] (F1 fix)."""
    if _is_num(e):
        return repr(float(e))
    if not isinstance(e, list) or not e:
        raise LayerError(f"bad expr node: {e!r}")
    op = e[0]
    if op == 'num':
        return repr(float(e[1]))
    if op == 'param':
        p = e[1]
        if p not in params:
            raise LayerError(f"unknown param '{p}'; declared: {sorted(params)}")
        return '{%s}' % p
    if op == 'v':
        n = e[1]
        if n not in nodes:
            raise LayerError(f"unknown node '{n}'; declared nodes: {sorted(nodes)} (typo guard)")
        return f'V({n})'
    if op == 'vdiff':
        a, b = e[1], e[2]
        for n in (a, b):
            if n not in nodes:
                raise LayerError(f"unknown node '{n}'; declared: {sorted(nodes)}")
        return f'V({a},{b})'
    if op == 'i':
        s = e[1]
        if not IDENT.match(s):
            raise LayerError(f"bad sense source name: {s!r}")
        if senses and s not in senses:
            raise LayerError(f"unknown sense source '{s}'; declared Vsense devices: {sorted(senses)}")
        return f'I({s})'
    if op == 'time':
        return 'TIME'
    if op == 'temper':
        return 'temper'
    if op in ('add', 'sub', 'mul', 'div', 'powop', 'gt', 'lt', 'ge', 'le', 'eq'):
        sym = {'add': '+', 'sub': '-', 'mul': '*', 'div': '/', 'powop': '^',
               'gt': '>', 'lt': '<', 'ge': '>=', 'le': '<=', 'eq': '='}[op]
        a = lower_expr(e[1], nodes, params, dev_pins, senses)
        b = lower_expr(e[2], nodes, params, dev_pins, senses)
        if op == 'div' and e[2] == 0 or (isinstance(e[2], list) and e[2] == ['num', 0]):
            raise LayerError("literal division by zero (ngspice silently yields 1e32)")
        return f'({a}{sym}{b})'
    if op == 'neg':
        return f'(-({lower_expr(e[1], nodes, params, dev_pins, senses)}))'
    if op in ('and', 'or'):
        sym = '&&' if op == 'and' else '||'
        return f'(({lower_expr(e[1], nodes, params, dev_pins, senses)}){sym}({lower_expr(e[2], nodes, params, dev_pins, senses)}))'
    if op == 'not':
        return f'(!({lower_expr(e[1], nodes, params, dev_pins, senses)}))'
    if op == 'tern':
        return f'(({lower_expr(e[1], nodes, params, dev_pins, senses)} )?({lower_expr(e[2], nodes, params, dev_pins, senses)}):({lower_expr(e[3], nodes, params, dev_pins, senses)}))'
    if op == 'clamp':
        x = lower_expr(e[1], nodes, params, dev_pins, senses)
        lo = lower_expr(e[2], nodes, params, dev_pins, senses)
        hi = lower_expr(e[3], nodes, params, dev_pins, senses)
        return f'min(max({x},{lo}),{hi})'
    if op == 'smax':
        x = lower_expr(e[1], nodes, params, dev_pins, senses)
        y = lower_expr(e[2], nodes, params, dev_pins, senses)
        return f'(0.5*(({x})+({y})+sqrt((({x})-({y}))*(({x})-({y}))+1e-12)))'
    if op == 'smin':
        x = lower_expr(e[1], nodes, params, dev_pins, senses)
        y = lower_expr(e[2], nodes, params, dev_pins, senses)
        return f'(0.5*(({x})+({y})-sqrt((({x})-({y}))*(({x})-({y}))+1e-12)))'
    if op == 'call':
        name = validate_call(e[1])
        args = ', '.join(lower_expr(a, nodes, params, dev_pins, senses) for a in e[2])
        if name == 'exp':
            # static magnitude tripwire on constant args
            try:
                v = float(lower_expr(e[2][0], nodes, params, dev_pins, senses))
                if abs(v) > 700:
                    raise LayerError(f"exp() of literal {v}: overflow-scale result, refuse silent nonsense")
            except LayerError:
                raise
            except Exception:
                pass
        if name in ('ln', 'log', 'log10'):
            try:
                v = float(lower_expr(e[2][0], nodes, params, dev_pins, senses))
                if v <= 0:
                    raise LayerError(f"{name}() of literal {v}: ngspice silently yields ~-1e99; guard the domain first")
            except LayerError:
                raise
            except Exception:
                pass
        if name == 'sqrt':
            try:
                v = float(lower_expr(e[2][0], nodes, params, dev_pins, senses))
                if v < 0:
                    raise LayerError(f"sqrt() of literal {v}: convergence wall; use sqrt(max(x,0)) if intended")
            except LayerError:
                raise
            except Exception:
                pass
        return f'{name}({args})'
    if op == 'pwl':
        x = lower_expr(e[1], nodes, params, dev_pins, senses)
        pts = e[2]
        if len(pts) % 2 or len(pts) < 4:
            raise LayerError("pwl needs even >=4 point values")
        return 'pwl(%s)' % (', '.join([x] + [repr(float(p)) for p in pts]))
    raise LayerError(f"unknown AST op '{op}'")

def build_subckt(name, pins, params, devices, allow_selfref=False):
    """devices: list of dicts. Kinds:
      Bv/Bi {name,p,n,expr,tag}: behavioral source; expr is AST (see lower_expr).
      Vsense {name,p,n}: 0V current-sense source, referenceable via ["i","<name>"].
      R {name,p,n,value}: value is numeric/scaled ("1k") or ["param","P"].
      C {name,p,n,value[,ic]}: same as R.
    'internal_nodes' per device declares extra nodes. Raises LayerError pre-sim.
    Direct self-reference (B expr reading its own output node) raises unless
    allow_selfref=True (expert latch territory: needs .ic + caps, verified fragile)."""
    if not IDENT.match(name):
        raise LayerError(f"bad subckt name {name!r} (injection guard)")
    for p in pins:
        if not IDENT.match(p):
            raise LayerError(f"bad pin name {p!r}")
    for k, v in params.items():
        if not IDENT.match(k):
            raise LayerError(f"bad param name {k!r}")
        _numval(v, f"param '{k}'")
    nodes = set(pins)
    for d in devices:
        for nd in ('p', 'n'):
            pass
        # collect internal nodes: allow 'internal' list via extra key
    internals = set()
    for d in devices:
        for k in d.get('internal_nodes', []):
            if not IDENT.match(k):
                raise LayerError(f"bad internal node {k!r}")
            internals.add(k)
    nodes |= internals
    # sense sources that I() may reference
    senses = {d['name'] for d in devices if d.get('kind') == 'Vsense'}
    lines = []
    hdr = f'.subckt {name} {" ".join(pins)}'
    if params:
        hdr += ' ' + ' '.join(f'{k}={v}' for k, v in params.items())
    lines.append(hdr)
    for d in devices:
        dn = d.get('name', '')
        if not dn or not IDENT.match(dn):
            raise LayerError(f"bad/missing device name {dn!r}")
        tag = d.get('tag', dn or d['kind'])
        k = d['kind']
        if k in ('Bv', 'Bi', 'Vsense', 'R', 'C'):
            for nd in ('p', 'n'):
                if d[nd] not in nodes:
                    raise LayerError(f"{dn}: unknown node '{d[nd]}'; declared pins+internals: {sorted(nodes)} (typo guard)")
        if k == 'Bv':
            s = lower_expr(d['expr'], nodes | senses, params, senses=senses)
            if not allow_selfref and _mentions(d['expr'], d['p']):
                raise LayerError(f"{dn}: expr reads its own output node '{d['p']}' - algebraic loop (verified fragile); use an RC state node instead")
            lines.append(f'* [thin:{tag}]')
            lines.append(f"B{dn} {d['p']} {d['n']} V={s}")
        elif k == 'Bi':
            s = lower_expr(d['expr'], nodes | senses, params, senses=senses)
            if not allow_selfref and _mentions(d['expr'], d['p']):
                raise LayerError(f"{dn}: expr reads its own output node '{d['p']}' - algebraic loop (verified fragile); use an RC state node instead")
            lines.append(f'* [thin:{tag}]')
            lines.append(f"B{dn} {d['p']} {d['n']} I={s}")
        elif k == 'Vsense':
            lines.append(f'V{dn} {d["p"]} {d["n"]} DC 0')
        elif k == 'R':
            lines.append(f'R{dn} {d["p"]} {d["n"]} {_rc_value(d["value"], params, "R")}')
        elif k == 'C':
            lines.append(f'C{dn} {d["p"]} {d["n"]} {_rc_value(d["value"], params, "C")}{f" IC={d["ic"]}" if "ic" in d else ""}')
        else:
            raise LayerError(f"unknown device kind '{k}'")
    lines.append('.ends')
    # global injection guard: no control/shell lines can be produced by construction;
    # verify anyway
    blob = '\n'.join(lines).lower()
    for bad in ('.control', '.include', 'shell', 'osdi', 'codemodel', '\n!', 'exec'):
        if bad in blob:
            raise LayerError(f"output failed sandbox audit: contains {bad!r}")
    return lines

def _rc_value(v, params, what):
    """R/C value: numeric/scaled literal, or ["param","P"] reference."""
    if isinstance(v, list):
        if len(v) == 2 and v[0] == 'param' and v[1] in params:
            return '{%s}' % v[1]
        raise LayerError(f"{what} value {v!r}: only ['param','NAME'] refs allowed (declared: {sorted(params)})")
    _numval(v, what)
    if isinstance(v, str) and v.strip()[-1:] in ('m', 'M') and not v.strip().lower().endswith('meg'):
        raise LayerError(f"{what} value {v!r}: bare 'm' means MILLI in ngspice (mega is 'meg'); refusing 1e9-scale ambiguity")
    return v

def _mentions(expr, node):
    """True if AST reads node via v/vdiff (self-reference heuristic)."""
    if not isinstance(expr, list) or not expr:
        return False
    if expr[0] == 'v' and len(expr) > 1 and expr[1] == node:
        return True
    if expr[0] == 'vdiff' and len(expr) > 2 and node in (expr[1], expr[2]):
        return True
    return any(_mentions(x, node) for x in expr[1:] if isinstance(x, list))
def selfref_warn(devices):
    """Warn on B devices whose lowered expr references their own output node."""
    warns = []
    for d in devices:
        if d.get('kind') in ('Bv', 'Bi'):
            s = str(d.get('expr'))
            if d['p'] in s or d['n'] in s:
                warns.append(f"{d.get('name')}: expr mentions own node ({d['p']}/{d['n']}) - likely algebraic loop; use RC state node")
    return warns
