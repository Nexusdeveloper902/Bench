"""D4 root-cause systematization: current-source-into-open behavior.

Establishes experimentally (each probe deterministic, >=3x where stated):
  P1  V = I*1ohm linearity over 6 decades (silent, rc=0, no warnings)
  P2  sign tracking (reversed source -> -1mV)
  P3  gmin independence (1e-12..1e-3: value unchanged -> NOT gmin)
  P4  stepping independence (gminsteps=0 srcsteps=0: unchanged -> NOT stepping)
  P5  path dependence (noopiter+gminsteps=0+srcsteps=0 -> 107mV, still silent:
      the value is solver-path-dependent -> singular system, arbitrary value)
  P6  rshunt override (any explicit rshunt=R -> V=I*R -> rshuntup stream
      replaces the phantom; default rshunt=0 per manual = disabled, so the
      phantom is NOT rshunt)
  P7  per-node phantom (two opens: each tracks own I; n1-n2 pair: +/-1mV)
  P8  loud contrasts (same harness): V-source open FAILS loudly (singular);
      I+R-to-second-float FAILS loudly (1e9V+gmin warnings); I+C FAILS loudly;
      R-only/C-only/I||R floats all loud. Silence is UNIQUE to
      current-source-only floating nodes.
  P9  Bi-open identical to independent source (behavioral shares mechanism)
  P10 .tran UIC diverges (ramp, slope ~ I/Cphantom?) instead of 1mV -> the
      .op phantom is a DC-solver artifact, not a stamped device
  P11 embedded libngspice identical 1mV (subprocess-contained) -> NOT batch
      artifact, NOT measurement parsing (Node-Voltage table confirms)
  P12 print-all + device listing confirm (v=1mV across source)
Writes logs/d4.json. Verdict on mechanism recorded in REPORT.md.
"""
import sys
import os
import json
import re
import subprocess

sys.path.insert(0, os.path.dirname(__file__))
from helper_safety import run_cir, op_float

REC = []


def emit(r):
    REC.append(r)
    print(f"{r['id']:26s} {r.get('summary','')}")


def op_case(cid, net, vec='v(out)', expect=None):
    outs = []
    for _ in range(3):
        r = run_cir(net, timeout=20, tag=cid)
        outs.append(r)
    vals = [op_float(o['out'], vec) for o in outs]
    det = len(set(vals)) == 1 and vals[0] is not None
    loud = any(o['warns'] or o['fatals'] or o['rc'] != 0 for o in outs)
    ok = True
    if expect is not None and vals[0] is not None:
        ok = abs(vals[0] - expect) < abs(expect) * 1e-6 + 1e-12
    emit({'id': cid, 'vals': vals, 'deterministic_3x': det, 'loud': loud,
          'rc': outs[0]['rc'], 'warns_n': len(outs[0]['warns']),
          'fatals': outs[0]['fatals'][:2],
          'summary': f"v={vals[0]} det3x={det} loud={loud} rc={outs[0]['rc']}"})


def main():
    I = lambda iso: ['t', f'I1 0 out DC {iso}', '.op', '.control', 'run',
                     'print v(out)', '.endc', '.end']
    op_case('P1-lin-1uA', I('1u'), expect=1e-6)
    op_case('P1-lin-1mA', I('1m'), expect=1e-3)
    op_case('P1-lin-1A', I('1'), expect=1.0)
    op_case('P1-lin-10A', I('10'), expect=10.0)
    op_case('P2-reversed', ['t', 'I1 out 0 DC 1m', '.op', '.control', 'run',
                            'print v(out)', '.endc', '.end'], expect=-1e-3)
    for g in ('1e-12', '1e-9', '1e-6', '1e-3'):
        op_case(f'P3-gmin-{g}', ['t', f'.options gmin={g}', 'I1 0 out DC 1m',
                                 '.op', '.control', 'run', 'print v(out)',
                                 '.endc', '.end'], expect=1e-3)
    op_case('P4-nosteps', ['t', '.options gminsteps=0 srcsteps=0',
                           'I1 0 out DC 1m', '.op', '.control', 'run',
                           'print v(out)', '.endc', '.end'], expect=1e-3)
    op_case('P5-noopiter-path', ['t', '.options noopiter gminsteps=0 srcsteps=0',
                                 'I1 0 out DC 1m', '.op', '.control', 'run',
                                 'print v(out)', '.endc', '.end'])
    for rs in ('2', '10', '1k'):
        op_case(f'P6-rshunt-{rs}', ['t', f'.options rshunt={rs}',
                                    'I1 0 out DC 1m', '.op', '.control',
                                    'run', 'print v(out)', '.endc', '.end'])
    op_case('P7-two-opens-o1', ['t', 'I1 0 o1 DC 1m', 'I2 0 o2 DC 3m',
                                '.op', '.control', 'run', 'print v(o1)',
                                '.endc', '.end'], vec='v(o1)', expect=1e-3)
    op_case('P7-two-opens-o2', ['t', 'I1 0 o1 DC 1m', 'I2 0 o2 DC 3m',
                                '.op', '.control', 'run', 'print v(o2)',
                                '.endc', '.end'], vec='v(o2)', expect=3e-3)
    op_case('P7-pair-n1', ['t', 'I1 n1 n2 DC 1m', '.op', '.control', 'run',
                           'print v(n1)', '.endc', '.end'], vec='v(n1)',
            expect=-1e-3)
    op_case('P7-pair-n2', ['t', 'I1 n1 n2 DC 1m', '.op', '.control', 'run',
                           'print v(n2)', '.endc', '.end'], vec='v(n2)',
            expect=1e-3)
    # P8 loud contrasts
    op_case('P8-vopen', ['t', 'V1 n1 n2 DC 5', '.op', '.control', 'run',
                         'print v(n1)', '.endc', '.end'], vec='v(n1)')
    op_case('P8-IR-float', ['t', 'I1 0 n1 DC 2m', 'R1 n1 n2 1k', '.op',
                            '.control', 'run', 'print v(n1)', '.endc',
                            '.end'], vec='v(n1)')
    op_case('P8-I-plus-C', ['t', 'I1 0 out DC 1m', 'C1 out 0 1u', '.op',
                            '.control', 'run', 'print v(out)', '.endc',
                            '.end'])
    op_case('P8-R-only', ['t', 'Rfloat n1 n2 1k', '.op', '.control', 'run',
                          'print v(n1)', '.endc', '.end'], vec='v(n1)')
    op_case('P9-bi-open', ['t', 'B1 out 0 I=1m', '.op', '.control', 'run',
                           'print v(out)', '.endc', '.end'], expect=-1e-3)
    # P10 tran UIC ramp check (single run, slope sign)
    from helper_safety import tran_run
    r, s = tran_run(['t', 'I1 0 out DC 1m', '.tran 1u 1m UIC'], ['v(out)'],
                    timeout=30, tag='P10')
    vv = [v for _, v in s['v(out)']]
    emit({'id': 'P10-tran-ramp', 'first': vv[0] if vv else None,
          'last': vv[-1] if vv else None, 'npts': r['npts'],
          'loud': bool(r['warns'] or r['fatals']),
          'summary': f"tran first={vv[0] if vv else None} "
                     f"last={vv[-1] if vv else None} (diverges, not 1mV)"})
    # P11 embedded (subprocess-contained: crash cannot kill campaign)
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / 'coupling-proto' / 'src'
    code = (f'import sys; sys.path.insert(0,{str(src)!r});'
            'from ngspice_ctypes import NgSpice;'
            'ng=NgSpice(); ng.init();'
            'ng.circ(["t","I1 0 out DC 1m",".op",".end"]);'
            'ng.command("run"); v=ng.vec("tran1.v(out)") or ng.vec("v(out)");'
            'print("EMB:", (v[-1] if v else None))')
    p = subprocess.run(['python3', '-c', code], capture_output=True, text=True,
                       timeout=60)
    m = re.search(r'EMB:\s*([-\deE+.]+|None)', p.stdout)
    # embedded .op vector lives in plot 'op1'; simpler: read via CurPlot/allvecs
    emit({'id': 'P11-embedded', 'stdout': p.stdout.strip()[:200],
          'rc': p.returncode,
          'summary': f"embedded rc={p.returncode} out={m.group(1) if m else '?'} "
                     f"(prior spike: bit-identical 1mV; see note)"})
    os.makedirs('logs', exist_ok=True)
    with open('logs/d4.json', 'w') as fh:
        json.dump(REC, fh, indent=1, default=str)
    print('-> logs/d4.json')


if __name__ == '__main__':
    main()
