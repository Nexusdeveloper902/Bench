"""Static topology + component-terminal validator (pre-simulation).

Input: raw ngspice netlist lines (first line = title per ngspice semantics).
Output: list of findings {code, severity, message, nodes, devices, layer}.

Severity: 'error' = reject before sim (silent-danger or certain failure);
          'warn'  = proceed only with runtime backstop + logged rationale.

Layer tags follow the ownership split in REPORT.md:
  authoring  = component/package authoring mistake (bad values, signs, topology intent)
  static     = static package validation (this validator's load-bearing checks)
  runtime    = needs actual ngspice run (loud failures, iteration health)
  post       = post-simulation analysis (magnitude plausibility, event coverage)
  expression = behavioral-expression authoring (thin.py territory, re-checked here
               at netlist level for defense in depth)

Graph reasoning (no simulation):
  DC-conductance edges conduct at DC: R, L, V, E, Bv, H, D (diode, conservative:
  assumed conduct; direction-insensitive for path existence), Q/M/J (any
  semiconductor terminal pair, conservative). Open at DC: C, I, G, Bi, F,
  transmission lines / opaque X (warn).
  Voltage-short edges (for V-loop): V, E, Bv, H, L, R==0.
"""
import re

GROUND = '0'

SCALE = {'f': 1e-15, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'm': 1e-3,
         'k': 1e3, 'meg': 1e6, 'g': 1e9, 't': 1e12}
_NUM_RE = re.compile(r'^\s*([+-]?[\d.]+(?:e[+-]?\d+)?)\s*([a-zA-Z]*)\s*$')


def parse_value(tok):
    """Parse ngspice number with scale suffix + optional unit (1mH, 1uF, 1kOhm).

    Returns float or None. Longest scale prefix wins ('meg' before 'm');
    trailing unit letters are ignored. Unknown leading suffix -> None.
    """
    if tok is None:
        return None
    m = _NUM_RE.match(str(tok).strip())
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    suf = m.group(2).lower()
    if not suf:
        return val
    if suf.startswith('meg'):
        return val * SCALE['meg']
    first = suf[0]
    if first in ('f', 'p', 'n', 'u', 'm', 'k', 'g', 't'):
        # ngspice scale prefix (t = tera); trailing unit letters ignored
        return val * SCALE[first]
    if first.isalpha():
        # pure unit with no scale (V, A, H, F, s, ohm, ...) -> plain number
        return val
    return None


def _strip_comment(line):
    # ngspice comment lines start with * ; inline ; comment only inside .control
    s = line.strip()
    if not s or s.startswith('*'):
        return ''
    return line.split(';')[0] if False else line  # keep it simple: no inline strip


class Device:
    def __init__(self, name, nodes, raw, kind):
        self.name = name
        self.nodes = nodes  # lowercased node list
        self.raw = raw
        self.kind = kind  # single upper letter
        self.params = raw


def parse_netlist(lines):
    """Parse netlist -> (devices, b_exprs, analyses, options, errors).

    devices: list[Device]; b_exprs: {devname: (pnodes, expr_str, is_voltage)};
    analyses: list of analysis lines (.tran/.op/.dc/.ac); options: dict.
    First line is always the title (ngspice semantics) and is skipped.
    .control blocks are skipped for topology (vectors/probes only).
    """
    errors = []
    devices = []
    b_exprs = {}
    analyses = []
    options = {}
    if not lines:
        return devices, b_exprs, analyses, options, ['empty netlist']
    body = lines[1:]  # skip title
    # Join ngspice `+` continuation lines into the preceding line BEFORE any
    # parsing, so B expressions / .models split across lines are classified
    # and bandwidth-analyzed as ngspice sees them [E spike-3 H5: un-joined
    # `+` truncated a B expr to `5*(u(TIME-200u)`, hiding the 2nd edge from
    # the event set while ngspice fired the full pulse].
    joined_body = []
    for raw in body:
        s = raw.strip()
        if s.startswith('+') and joined_body:
            joined_body[-1] = joined_body[-1] + ' ' + s[1:].strip()
        else:
            joined_body.append(raw)
    body = joined_body
    in_control = False
    seen_names = set()
    for ln, raw in enumerate(body, start=2):
        s = raw.strip()
        if not s or s.startswith('*'):
            continue
        low = s.lower()
        if low == '.control':
            in_control = True
            continue
        if low == '.endc':
            in_control = False
            continue
        if in_control:
            continue
        if low == '.end':
            break
        if s.startswith('.'):
            tok = s.split()
            cmd = tok[0].lower()
            if cmd in ('.tran', '.op', '.dc', '.ac', '.noise', '.pz'):
                analyses.append(s)
            elif cmd == '.options':
                for t in tok[1:]:
                    if '=' in t:
                        k, v = t.split('=', 1)
                        options[k.lower()] = v
            elif cmd in ('.model', '.subckt', '.ends', '.ic', '.nodeset',
                         '.param', '.include', '.lib', '.global', '.temp',
                         '.osdi', '.alter', '.save', '.plot', '.print',
                         '.fourier', '.option'):
                pass  # not topology-relevant here
            else:
                pass
            continue
        # device line
        parts = s.split()
        if not parts:
            continue
        name = parts[0]
        kind = name[0].upper()
        low_name = name.lower()
        if low_name in seen_names:
            errors.append({'code': 'DUP-NAME', 'severity': 'error',
                           'message': f"duplicate device name '{name}'",
                           'nodes': [], 'devices': [name], 'layer': 'static'})
        seen_names.add(low_name)
        if kind in ('R', 'C', 'L', 'D'):
            nodes = [p.lower() for p in parts[1:3]] if len(parts) >= 3 else []
            if len(parts) < 4:
                errors.append({'code': 'MALFORMED', 'severity': 'error',
                               'message': f"{name}: {kind} needs 2 nodes + value",
                               'nodes': nodes, 'devices': [name],
                               'layer': 'authoring'})
            devices.append(Device(name, nodes, s, kind))
        elif kind in ('V', 'I', 'B'):
            nodes = [p.lower() for p in parts[1:3]] if len(parts) >= 3 else []
            if len(parts) < 3:
                errors.append({'code': 'MALFORMED', 'severity': 'error',
                               'message': f"{name}: {kind} needs 2 nodes",
                               'nodes': nodes, 'devices': [name],
                               'layer': 'authoring'})
            devices.append(Device(name, nodes, s, kind))
            if kind == 'B':
                m = re.search(r'\b([VI])\s*=\s*(.*)$', s, re.I)
                if m:
                    b_exprs[name] = (nodes, m.group(2).strip(),
                                     m.group(1).upper() == 'V')
                else:
                    errors.append({'code': 'B-NOEXPR', 'severity': 'error',
                                   'message': f"{name}: B-source missing V=/I= expression",
                                   'nodes': nodes, 'devices': [name],
                                   'layer': 'expression'})
        elif kind in ('E', 'G'):
            nodes = [p.lower() for p in parts[1:5]] if len(parts) >= 5 else []
            if len(parts) < 6:
                errors.append({'code': 'MALFORMED', 'severity': 'error',
                               'message': f"{name}: {kind} needs 4 nodes + gain",
                               'nodes': nodes, 'devices': [name],
                               'layer': 'authoring'})
            devices.append(Device(name, nodes, s, kind))
        elif kind in ('H', 'F'):
            nodes = [p.lower() for p in parts[1:3]] if len(parts) >= 3 else []
            devices.append(Device(name, nodes, s, kind))
        elif kind in ('Q', 'J'):
            nodes = [p.lower() for p in parts[1:4]] if len(parts) >= 4 else []
            devices.append(Device(name, nodes, s, kind))
        elif kind == 'M':
            nodes = [p.lower() for p in parts[1:5]] if len(parts) >= 5 else []
            devices.append(Device(name, nodes, s, kind))
        elif kind == 'K':
            devices.append(Device(name, [], s, kind))  # coupling, no new nodes
        elif kind == 'X':
            # subcircuit instance: Xname n1 n2 ... subname ; last token = subname
            nodes = [p.lower() for p in parts[1:-1]] if len(parts) >= 3 else []
            x = Device(name, nodes, s, kind)
            devices.append(x)
        elif kind in ('O', 'U', 'Y', 'N', 'T', 'S', 'W', 'Z'):
            nodes = [p.lower() for p in parts[1:3]] if len(parts) >= 3 else []
            devices.append(Device(name, nodes, s, kind))
            errors.append({'code': 'EXOTIC-DEVICE', 'severity': 'warn',
                           'message': f"{name}: kind '{kind}' is outside static coverage "
                                      f"(no DC/loop model); runtime verdict is load-bearing",
                           'nodes': nodes, 'devices': [name],
                           'layer': 'static'})
        else:
            errors.append({'code': 'UNKNOWN-KIND', 'severity': 'error',
                           'message': f"{name}: unknown device kind '{kind}'",
                           'nodes': [], 'devices': [name], 'layer': 'static'})
    return devices, b_exprs, analyses, options, errors


def _b_is_voltage(raw):
    m = re.search(r'\b([VI])\s*=', raw, re.I)
    return m is not None and m.group(1).upper() == 'V'


def _dc_conductance(kind, dev):
    if kind in ('R', 'L', 'V', 'E', 'H', 'D', 'Q', 'M', 'J', 'X'):
        # X is opaque: optimistic (assume internal path) to avoid false
        # positives; OPAQUE-X warn separately demands expansion/whitelist.
        return True
    if kind == 'B':
        return _b_is_voltage(dev.raw)
    return False


def _short_edge(kind, dev):
    """Edges that are ideal shorts for the V-loop check."""
    if kind in ('V', 'E', 'H'):
        return True
    if kind == 'B':
        return _b_is_voltage(dev.raw)
    if kind == 'L':
        return True
    if kind == 'R':
        parts = dev.raw.split()
        if len(parts) >= 4:
            v = parse_value(parts[3])
            if v is not None and v == 0:
                return True
    return False


def validate(lines):
    """Run all static checks. Returns list of finding dicts."""
    devices, b_exprs, analyses, options, errors = parse_netlist(lines)
    out = list(errors)

    nodes = set()
    for d in devices:
        nodes.update(d.nodes)
    has_ground = GROUND in nodes

    if not has_ground and nodes:
        out.append({'code': 'NO-GROUND', 'severity': 'error',
                    'message': "no '0' ground node present; every node is floating",
                    'nodes': sorted(nodes), 'devices': [],
                    'layer': 'static'})

    # ---- per-device degenerate checks ----
    for d in devices:
        k = d.kind
        if k in ('V', 'E', 'B') and len(d.nodes) >= 2 and d.nodes[0] == d.nodes[1]:
            if k == 'B' and not re.search(r'\bV\s*=', d.raw, re.I):
                pass
            else:
                out.append({'code': 'SHORTED-VSRC', 'severity': 'error',
                            'message': f"{d.name}: voltage source shorted (same node both terminals)",
                            'nodes': [d.nodes[0]], 'devices': [d.name],
                            'layer': 'static'})
        if k in ('R', 'C', 'L') and len(d.nodes) >= 2 and d.nodes[0] == d.nodes[1]:
            out.append({'code': 'SHORTED-PASSIVE', 'severity': 'warn',
                        'message': f"{d.name}: {k} shorted (both terminals on '{d.nodes[0]}')",
                        'nodes': [d.nodes[0]], 'devices': [d.name],
                        'layer': 'authoring'})
        if k in ('R', 'C', 'L'):
            parts = d.raw.split()
            if len(parts) >= 4:
                v = parse_value(parts[3])
                if v is None:
                    out.append({'code': 'BAD-VALUE', 'severity': 'warn',
                                'message': f"{d.name}: unparseable value '{parts[3]}'",
                                'nodes': list(d.nodes), 'devices': [d.name],
                                'layer': 'authoring'})
                elif k == 'R' and v < 0:
                    out.append({'code': 'NEG-R', 'severity': 'error',
                                'message': f"{d.name}: negative resistance {parts[3]} (non-physical; silent-risk)",
                                'nodes': list(d.nodes), 'devices': [d.name],
                                'layer': 'authoring'})
                elif k in ('C', 'L') and v < 0:
                    out.append({'code': 'NEG-CL', 'severity': 'error',
                                'message': f"{d.name}: negative {k} value {parts[3]}",
                                'nodes': list(d.nodes), 'devices': [d.name],
                                'layer': 'authoring'})
                elif k == 'R' and v == 0:
                    out.append({'code': 'ZERO-R', 'severity': 'warn',
                                'message': f"{d.name}: zero resistance (ideal short; V-loop risk)",
                                'nodes': list(d.nodes), 'devices': [d.name],
                                'layer': 'static'})
                elif k in ('C', 'L') and v == 0:
                    out.append({'code': 'ZERO-CL', 'severity': 'error',
                                'message': f"{d.name}: zero {k} value (degenerate)",
                                'nodes': list(d.nodes), 'devices': [d.name],
                                'layer': 'authoring'})

    # ---- incidence / dangling ----
    incidence = {}
    for d in devices:
        for n in d.nodes:
            incidence[n] = incidence.get(n, 0) + 1
    for n, c in sorted(incidence.items()):
        if n == GROUND:
            continue
        if c < 2:
            devs = [d.name for d in devices if n in d.nodes]
            out.append({'code': 'DANGLING', 'severity': 'warn',
                        'message': f"node '{n}' has only {c} terminal connection(s) (open end?)",
                        'nodes': [n], 'devices': devs, 'layer': 'static'})

    # ---- DC path to ground (BFS over conductance edges) ----
    adj = {}
    for d in devices:
        if _dc_conductance(d.kind, d) and len(d.nodes) >= 2:
            if d.kind in ('Q', 'M', 'J'):
                # conservative clique: any terminal pair may conduct at DC
                for i in range(len(d.nodes)):
                    for j in range(i + 1, len(d.nodes)):
                        a, b = d.nodes[i], d.nodes[j]
                        adj.setdefault(a, []).append((b, d.name))
                        adj.setdefault(b, []).append((a, d.name))
                continue
            a, b = d.nodes[0], d.nodes[1]
            adj.setdefault(a, []).append((b, d.name))
            adj.setdefault(b, []).append((a, d.name))
            if d.kind in ('E', 'G') and len(d.nodes) >= 4:
                # controlling side is sensing (no DC path); output side only
                pass
    reached = set([GROUND]) if has_ground else set()
    stack = [GROUND] if has_ground else []
    while stack:
        n = stack.pop()
        for m, _ in adj.get(n, []):
            if m not in reached:
                reached.add(m)
                stack.append(m)
    for n in sorted(nodes):
        if n == GROUND:
            continue
        if n not in reached:
            devs = [d.name for d in devices if n in d.nodes]
            out.append({'code': 'NO-DC-PATH', 'severity': 'error',
                        'message': f"node '{n}' has no DC path to ground (capacitors/current-sources are open at DC)",
                        'nodes': [n], 'devices': devs, 'layer': 'static'})

    # ---- current-source-only nodes (D4 silent class, subset of NO-DC-PATH) ----
    for n in sorted(nodes):
        if n == GROUND or n in reached:
            continue
        incident = [d for d in devices if n in d.nodes]
        if incident and all(d.kind in ('I', 'G', 'F') or
                            (d.kind == 'B' and bool(re.search(r'\bI\s*=', d.raw, re.I)))
                            for d in incident):
            out.append({'code': 'ISRC-OPEN', 'severity': 'error',
                        'message': f"node '{n}' connects ONLY to current-source terminals "
                                   f"({', '.join(d.name for d in incident)}): ngspice returns "
                                   f"V=I*1ohm SILENTLY (verified). Refuse before sim.",
                        'nodes': [n],
                        'devices': [d.name for d in incident],
                        'layer': 'static'})

    # ---- voltage-source loops (union-find over short edges) ----
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        parent[rb] = ra
        return True

    for n in nodes:
        parent[n] = n
    for d in devices:
        if _short_edge(d.kind, d) and len(d.nodes) >= 2:
            a, b = d.nodes[0], d.nodes[1]
            if a == b:
                continue
            if not union(a, b):
                out.append({'code': 'V-LOOP', 'severity': 'error',
                            'message': f"voltage-source/short loop via {d.name} "
                                       f"('{a}' already short-connected to '{b}')",
                            'nodes': [a, b], 'devices': [d.name],
                            'layer': 'static'})
                # keep going to report all, structure stays merged

    # ---- B-expression checks ----
    expr_nodes = set()  # nodes referenced only inside B exprs
    dev_terminals = set(nodes)
    for name, (pnodes, expr, is_v) in b_exprs.items():
        up = expr.upper()
        # self-reference: a VOLTAGE-source B reading its own output node is an
        # algebraic loop with no impedance (verified C4 no-solution class).
        # A CURRENT-source B reading its own node is a normal behavioral
        # conductance (e.g. I=V(n)/R) and is allowed.
        if is_v:
            for p in pnodes:
                if re.search(r'\bV\s*\(\s*' + re.escape(p) + r'\b', expr, re.I):
                    out.append({'code': 'B-SELFREF', 'severity': 'error',
                                'message': f"{name}: expression reads its own output node '{p}' "
                                           f"(algebraic loop; verified no-solution class)",
                                'nodes': [p], 'devices': [name],
                                'layer': 'expression'})
        # implicit-node typo: V(foo) where foo never a device terminal
        for m in re.finditer(r'\bV\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\b', expr):
            ref = m.group(1).lower()
            if ref not in dev_terminals and ref != '0':
                expr_nodes.add(ref)
                out.append({'code': 'B-IMPLICIT-NODE', 'severity': 'error',
                            'message': f"{name}: V({m.group(1)}) references node '{ref}' "
                                       f"with no device terminal (implicit floating node)",
                            'nodes': [ref], 'devices': [name],
                            'layer': 'expression'})
        if re.search(r'\bddt\s*\(', expr, re.I):
            out.append({'code': 'B-DDT', 'severity': 'warn',
                        'message': f"{name}: ddt() of a discontinuity yields a pure "
                                   f"timestep artifact (peak=V/dt); never read as physics",
                        'nodes': list(pnodes), 'devices': [name],
                        'layer': 'expression'})
        if re.search(r'/\s*0(?![\d.])', expr):
            out.append({'code': 'B-DIVZERO', 'severity': 'error',
                        'message': f"{name}: literal division by zero (ngspice silently yields ~1e32)",
                        'nodes': list(pnodes), 'devices': [name],
                        'layer': 'expression'})
        # TIME in a .op-only deck
        has_tran = any(a.strip().lower().startswith('.tran') for a in analyses)
        if re.search(r'\bTIME\b', expr, re.I) and not has_tran:
            out.append({'code': 'B-TIME-IN-OP', 'severity': 'warn',
                        'message': f"{name}: TIME-dependent expression without .tran "
                                   f"(TIME is silently 0 in .op)",
                        'nodes': list(pnodes), 'devices': [name],
                        'layer': 'expression'})

    # ---- B dependency cycles (mutual reference without state) ----
    # A B device 'drives' its output nodes; another B 'reads' a node via V().
    # A dependency cycle (B1 reads node driven by B2 which reads node driven
    # by B1) is the latch/multistable class: valid only with .ic + state
    # elements, otherwise a convergence wall. Static cannot decide intent,
    # so this is a warn; the runtime loud-failure scan is the backstop.
    drivers = {}  # node -> [bname]
    for name, (pnodes, _expr, _isv) in b_exprs.items():
        for p in pnodes:
            drivers.setdefault(p, []).append(name)
    breads = {}
    for name, (_pn, expr, _isv) in b_exprs.items():
        reads = set()
        for m in re.finditer(r'\bV\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\b', expr):
            ref = m.group(1).lower()
            for drv in drivers.get(ref, []):
                if drv != name:
                    reads.add(drv)
        breads[name] = reads
    # cycle detection (DFS)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in breads}
    cyc_reported = set()

    def dfs(u, stack):
        color[u] = GRAY
        stack.append(u)
        for w in breads.get(u, ()):
            if color.get(w, BLACK) == GRAY:
                cyc = stack[stack.index(w):] + [w]
                key = tuple(sorted(set(cyc)))
                if key not in cyc_reported:
                    cyc_reported.add(key)
                    out.append({'code': 'B-CYCLE', 'severity': 'warn',
                                'message': f"behavioral loop {' -> '.join(cyc)}: "
                                           f"multistable/convergence class; needs .ic + state "
                                           f"elements and intent review (runtime scan is backstop)",
                                'nodes': [], 'devices': list(key),
                                'layer': 'expression'})
            elif color.get(w, BLACK) == WHITE:
                dfs(w, stack)
        stack.pop()
        color[u] = BLACK

    for n in list(breads):
        if color[n] == WHITE:
            dfs(n, [])

    # ---- opaque subcircuits ----
    for d in devices:
        if d.kind == 'X':
            out.append({'code': 'OPAQUE-X', 'severity': 'warn',
                        'message': f"{d.name}: subcircuit instance is opaque to static checks "
                                   f"(DC paths inside unverified; expand or whitelist)",
                        'nodes': list(d.nodes), 'devices': [d.name],
                        'layer': 'static'})
    return out


def has_error(findings):
    return any(f['severity'] == 'error' for f in findings)


def codes(findings, severity=None):
    if severity:
        return sorted(f['code'] for f in findings if f['severity'] == severity)
    return sorted(f['code'] for f in findings)
