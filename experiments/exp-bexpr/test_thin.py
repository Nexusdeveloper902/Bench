"""Thin-layer equivalence + validation battery."""
import sys, json, time, re
sys.path.insert(0, '.')
from helper import run_cir, has_error
from thin import build_subckt, lower_expr, LayerError, selfref_warn

results = {'equivalence': [], 'rejections': [], 'warnings': []}

def sim(net, vec='v(out)'):
    r = run_cir(net)
    m = re.search(re.escape(vec) + r'\s*=\s*([-\deE+.]+)', r['out'])
    return (float(m.group(1)) if m else None), has_error(r['out']), r['rc']

# --- 1. equivalence: same models via thin vs hand-native ---
# gain + affine + clip + diode
thin_gain = build_subckt('gain10', ['a', 'b', 'gnd'], {'G': 10}, [
    {'kind': 'Bv', 'name': 'g1', 'p': 'b', 'n': 'gnd', 'tag': 'gain',
     'expr': ['mul', ['param', 'G'], ['v', 'a']]}])
print('\n'.join(thin_gain))

cases = [
    ('gain', thin_gain, ['t', 'V1 in 0 DC 0.5', 'R1 in 0 1k', 'X1 in out gnd gain10'] + thin_gain +
     ['.op', '.control', 'run', 'print v(out)', '.endc', '.end'], 5.0),
    ('affine', build_subckt('aff', ['a', 'b', 'gnd'], {}, [
        {'kind': 'Bv', 'name': 'a1', 'p': 'b', 'n': 'gnd', 'tag': 'affine',
         'expr': ['add', ['mul', ['num', 2], ['v', 'a']], ['num', 0.7]]}]),
     None, 1.7),
    ('clip', build_subckt('clip', ['a', 'b', 'gnd'], {}, [
        {'kind': 'Bv', 'name': 'c1', 'p': 'b', 'n': 'gnd', 'tag': 'clip',
         'expr': ['clamp', ['v', 'a'], ['num', -1], ['num', 1]]}]),
     None, 1.0),
    ('diode', build_subckt('dd', ['a', 'k', 'gnd'], {'IS': 1e-14, 'NVT': 0.038775}, [
        {'kind': 'Bi', 'name': 'd1', 'p': 'a', 'n': 'k', 'tag': 'shockley',
         'expr': ['mul', ['param', 'IS'],
                  ['sub', ['call', 'exp', [['div', ['vdiff', 'a', 'k'], ['param', 'NVT']]]], ['num', 1]]]}]),
     None, None),
]
hand = {
    'gain': ['t', 'V1 in 0 DC 0.5', 'R1 in 0 1k', 'B1 out 0 V=10*V(in)', 'R2 out 0 1k',
             '.op', '.control', 'run', 'print v(out)', '.endc', '.end'],
    'affine': ['t', 'V1 in 0 DC 0.5', 'R1 in 0 1k', 'B1 out 0 V=2*V(in)+0.7', 'R2 out 0 1k',
               '.op', '.control', 'run', 'print v(out)', '.endc', '.end'],
    'clip': ['t', 'V1 in 0 DC 5', 'R1 in 0 1k', 'B1 out 0 V=min(max(V(in),-1),1)', 'R2 out 0 1k',
             '.op', '.control', 'run', 'print v(out)', '.endc', '.end'],
    'diode': ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'B1 out 0 I=1e-14*(exp(V(out)/0.038775)-1)', 'C1 out 0 1p',
              '.op', '.control', 'run', 'print v(out)', '.endc', '.end'],
}
thin_full = {
    'gain': ['t', 'V1 in 0 DC 0.5', 'R1 in 0 1k', 'X1 in out 0 gain10'] + cases[0][1] +
            ['.op', '.control', 'run', 'print v(out)', '.endc', '.end'],
    'affine': ['t', 'V1 in 0 DC 0.5', 'R1 in 0 1k', 'X1 in out 0 aff'] + cases[1][1] +
              ['.op', '.control', 'run', 'print v(out)', '.endc', '.end'],
    'clip': ['t', 'V1 in 0 DC 5', 'R1 in 0 1k', 'X1 in out 0 clip'] + cases[2][1] +
            ['.op', '.control', 'run', 'print v(out)', '.endc', '.end'],
    'diode': ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'X1 out 0 0 dd'] + cases[3][1] + ['C1 out 0 1p'] +
              ['.op', '.control', 'run', 'print v(out)', '.endc', '.end'],
}
# note: thin subckt pins are (a,b) + gnd free node; X1 in out 0 maps gnd->0. ok.
for name in ('gain', 'affine', 'clip', 'diode'):
    hv, herrs, hrc = sim(hand[name])
    t0 = time.perf_counter()
    tv, terrs, trc = sim(thin_full[name])
    t1 = time.perf_counter()
    match = hv is not None and tv is not None and abs(hv - tv) < 1e-9 * max(1, abs(hv))
    print(f"{name:8s} hand={hv} thin={tv} identical={match} hand_err={herrs} thin_err={terrs}")
    results['equivalence'].append({'model': name, 'hand': hv, 'thin': tv, 'identical': match})

# translation time (pure python, no sim)
import timeit
t = timeit.timeit(lambda: lower_expr(['clamp', ['v', 'a'], ['num', -1], ['num', 1]], {'a'}, {}), number=200)
print(f"lower_expr 200x: {t*1e3:.1f}ms total = {t*1e3/200:.3f}ms per expr")
results['translation_ms_per_expr'] = t * 1e3 / 200

# --- 2. rejection battery ---
reject_tests = [
    (['call', 'if', [['v', 'a'], ['num', 1], ['num', 0]]], 'no-if'),
    (['call', 'limit', [['v', 'a'], ['num', 0], ['num', 1]]], 'no-limit'),
    (['call', 'gauss', [['num', 0], ['num', 1]]], 'no-gauss'),
    (['call', 'pwr', [['num', -2], ['num', 2]]], 'no-pwr'),
    (['call', 'idt', [['v', 'a']]], 'no-idt'),
    (['call', 'foo', [['v', 'a']]], 'no-unknown-fn'),
    (['call', 'ln', [['num', 0]]], 'no-ln-zero'),
    (['call', 'sqrt', [['num', -4]]], 'no-sqrt-neg'),
    (['div', ['num', 1], ['num', 0]], 'no-div-zero'),
    (['v', 'typo_node'], 'typo-node'),
    (['param', 'NOSUCH'], 'unknown-param'),
    (['call', 'exp', [['num', 1000]]], 'exp-overflow'),
]
nodes, params = {'a', 'gnd'}, {}
for expr, label in reject_tests:
    try:
        s = lower_expr(expr, nodes, params)
        print(f"{label:16s} NOT-REJECTED -> {s}  *** HOLE ***")
        results['rejections'].append({'test': label, 'rejected': False, 'output': s})
    except LayerError as e:
        print(f"{label:16s} rejected: {e}")
        results['rejections'].append({'test': label, 'rejected': True, 'msg': str(e)})

# and/or lowering check
s = lower_expr(['and', ['gt', ['v', 'a'], ['num', 1]], ['lt', ['v', 'a'], ['num', 3]]], nodes, params)
print('and-lowering:', s)
assert '&&' in s and '&(' not in s.replace('&&', '')
s = lower_expr(['clamp', ['v', 'a'], ['num', -1], ['num', 1]], nodes, params)
print('clamp-lowering:', s)
assert s == 'min(max(V(a),-1.0),1.0)', s

# injection attempts
for bad_name in ['x\n.include /etc/passwd', 'a;shell', '.control', 'out gnd\nB9 0 0']:
    try:
        build_subckt(bad_name, ['a'], {}, [])
        print(f"INJECTION HOLE: {bad_name!r} accepted")
        results['rejections'].append({'test': f'inject:{bad_name!r}', 'rejected': False})
    except LayerError as e:
        print(f"inject {bad_name!r} rejected: {e}")
        results['rejections'].append({'test': f'inject:{bad_name!r}', 'rejected': True})

# selfref warn
w = selfref_warn([{'kind': 'Bv', 'name': 'b1', 'p': 'out', 'n': 'gnd', 'expr': ['v', 'out']}])
print('selfref warn:', w)
results['warnings'] = w

# F1/F2/F6: structural validation must now reject
struct_tests = []
try:
    build_subckt('t', ['a', 'b', 'gnd'], {}, [
        {'kind': 'Vsense', 'name': 'vs', 'p': 'a', 'n': 'm', 'internal_nodes': ['m']},
        {'kind': 'Bv', 'name': 'x', 'p': 'b', 'n': 'gnd',
         'expr': ['mul', ['i', 'typo_sense'], ['num', 2]]}])
    struct_tests.append(('I-typo-sense', False))
except LayerError as e:
    print('I-typo-sense rejected:', e); struct_tests.append(('I-typo-sense', True))
try:
    build_subckt('t', ['a', 'b', 'gnd'], {}, [
        {'kind': 'Bv', 'name': 'x', 'p': 'otup', 'n': 'gnd', 'expr': ['v', 'a']}])
    struct_tests.append(('p-node-typo', False))
except LayerError as e:
    print('p-node-typo rejected:', e); struct_tests.append(('p-node-typo', True))
try:
    build_subckt('t', ['a', 'b', 'gnd'], {}, [
        {'kind': 'Bv', 'name': 'x', 'p': 'b', 'n': 'gnd', 'expr': ['mul', ['v', 'b'], ['num', 2]]}])
    struct_tests.append(('selfref', False))
except LayerError as e:
    print('selfref rejected:', e); struct_tests.append(('selfref', True))
try:
    build_subckt('t', ['a', 'b', 'gnd'], {'RV': '1k'}, [
        {'kind': 'R', 'name': 'r1', 'p': 'a', 'n': 'b', 'value': ['param', 'RV']}])
    print('R-param-ref accepted (F3 fix)')
    struct_tests.append(('R-param-ref', True))
except LayerError as e:
    print('R-param-ref wrongly rejected:', e); struct_tests.append(('R-param-ref', False))
try:
    build_subckt('t', ['a', 'b', 'gnd'], {}, [
        {'kind': 'R', 'name': 'r1', 'p': 'a', 'n': 'b', 'value': '1M'}])
    struct_tests.append(('m-suffix', False))
except LayerError as e:
    print('m-suffix rejected:', e); struct_tests.append(('m-suffix', True))
results['structural'] = struct_tests

with open('logs/thin_results.json', 'w') as f:
    json.dump(results, f, indent=1, default=str)
print('saved logs/thin_results.json')
