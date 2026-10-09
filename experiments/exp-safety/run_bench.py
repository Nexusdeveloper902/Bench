"""Validator benchmark: valid circuits (no false positives) + malformed
(circuit must be rejected pre-sim). Also records ngspice loudness for the
'requires actually running' comparison. Writes logs/bench.json."""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))
import validator as V
from helper_safety import run_cir

VALID = {
    'V01-rc': ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'C1 out 0 1u IC=0',
               '.tran 10u 5m UIC', '.end'],
    'V02-divider': ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'R2 out 0 1k',
                    '.op', '.end'],
    'V03-diode': ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'D1 out 0 DLED',
                  '.model DLED D(IS=1e-14 N=1.5)', '.op', '.end'],
    'V04-eamp': ['t', 'V1 inp 0 DC 2.5', 'E1 out 0 inp fb 100000',
                 'R1 out fb 1k', 'R2 fb 0 1k', '.op', '.end'],
    'V05-schmitt': ['t', 'V1 in 0 PWL(0 0 1m 0 1.01m 5 2m 5 2.01m 0 3m 0)',
                    'R1 in p 10k', 'R2 out p 30k', 'C1 p 0 100p IC=0',
                    'B1 out 0 V=(V(p)>2.5)?5:0', 'R3 out 0 1k',
                    '.tran 5u 3m UIC', '.end'],
    'V06-lctank': ['t', 'L1 out 0 1mH IC=0', 'C1 out 0 1uF IC=5',
                   '.tran 1u 2m UIC', '.end'],
    'V07-ring': ['t', 'B1 o1 0 V=5*(1-u(V(o3)-2.5))', 'R1 o1 n1 1k',
                 'C1 n1 0 1n IC=0', 'B2 o2 0 V=5*(1-u(V(n1)-2.5))',
                 'R2 o2 n2 1k', 'C2 n2 0 1n IC=2',
                 'B3 o3 0 V=5*(1-u(V(n2)-2.5))', 'R3 o3 0 1k',
                 '.tran 10n 10u UIC', '.end'],
    'V08-ladder': ['t', 'V1 in 0 PWL(0 0 10u 0 11u 5 2m 5)',
                   'R0 in n0 1k', 'C0 n0 0 1n IC=0',
                   'R1 n0 n1 1k', 'C1 n1 0 1n IC=0',
                   'R2 n1 n2 1k', 'C2 n2 0 1n IC=0',
                   '.tran 1u 2m UIC', '.end'],
    'V09-tanh': ['t', 'V1 in 0 DC 5', 'R1 in 0 1k',
                 'B1 out 0 V=10*tanh(V(in)/2)', 'R2 out 0 1k',
                 '.op', '.end'],
    'V10-isrc-loaded': ['t', 'I1 0 out DC 1m', 'R1 out 0 1k',
                        '.op', '.end'],
    'V11-led': ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'D1 out 0 DLED',
                '.model DLED D(IS=1e-14 N=1.5)', '.tran 10u 5m', '.end'],
    'V12-pwl-rc': ['t', 'V1 in 0 PWL(0 0 1m 0 1.000001m 5 5m 5)',
                   'R1 in out 1k', 'C1 out 0 1u IC=0',
                   '.tran 1u 3m UIC', '.end'],
    'V13-bv-unloaded': ['t', 'B1 n1 0 V=5', '.op', '.end'],
    'V14-series-v': ['t', 'V1 a 0 DC 3', 'V2 b a DC 2', 'R1 b 0 1k',
                     '.op', '.end'],
    'V15-weak-tie': ['t', 'V1 in 0 DC 5', 'R1 in out 1k', 'R2 out 0 1T',
                     '.op', '.end'],
    'V16-vzero-r': ['t', 'V1 a 0 DC 5', 'R0 a b 0', 'R1 b 0 1k',
                    '.op', '.end'],
}

MALFORMED = {
    # id: (netlist, expected_error_codes_subset, ngspice_expect 'loud'|'silent')
    'M01-float-pair': (['t', 'V1 in 0 DC 5', 'R1 in mid 1k', 'Rfloat n1 n2 1k',
                        '.op', '.end'], ['NO-DC-PATH'], 'loud'),
    'M02-vloop': (['t', 'V1 out 0 DC 5', 'V2 out 0 DC 4',
                   '.op', '.end'], ['V-LOOP'], 'loud'),
    'M03-isrc-open': (['t', 'I1 0 out DC 1m', '.op', '.end'],
                      ['ISRC-OPEN', 'NO-DC-PATH'], 'silent'),
    'M04-isrc-pair': (['t', 'I1 n1 n2 DC 1m', '.op', '.end'],
                      ['ISRC-OPEN', 'NO-DC-PATH'], 'silent'),
    'M05-bi-open': (['t', 'B1 out 0 I=1m', '.op', '.end'],
                    ['ISRC-OPEN', 'NO-DC-PATH'], 'silent'),
    'M06-caploop': (['t', 'V1 in 0 DC 5', 'C1 in mid 1u', 'C2 mid out 1u',
                     'R1 out 0 1k', '.op', '.end'], ['NO-DC-PATH'], 'loud'),
    'M07-selfref': (['t', 'V1 in 0 DC 5', 'R1 in out 1k',
                     'B1 out 0 V=V(out)+1', 'R2 out 0 1k',
                     '.op', '.end'], ['B-SELFREF'], 'loud'),
    'M08-shorted-v': (['t', 'V1 out out DC 5', '.op', '.end'],
                      ['SHORTED-VSRC'], 'loud'),
    'M09-no-ground': (['t', 'V1 n1 n2 DC 5', 'R1 n1 n2 1k',
                       '.op', '.end'], ['NO-GROUND'], 'loud'),
    'M10-b-typo': (['t', 'V1 in 0 DC 5', 'R1 in out 1k',
                    'B1 out 0 V=5*V(typo)+V(in)', 'R2 out 0 1k',
                    '.op', '.end'], ['B-IMPLICIT-NODE'], 'loud'),
    'M11-c-coupled': (['t', 'V1 in 0 DC 5', 'C1 in mid 1u', 'R1 mid out 1k',
                       '.op', '.end'], ['NO-DC-PATH'], 'loud'),
    'M12-neg-r': (['t', 'V1 in 0 DC 5', 'R1 in out -1k', 'R2 out 0 1k',
                   '.op', '.end'], ['NEG-R'], 'loud'),
    'M13-vloop3': (['t', 'V1 a 0 DC 1', 'V2 b a DC 1', 'V3 b 0 DC 3',
                    '.op', '.end'], ['V-LOOP'], 'loud'),
    'M14-ics-cutset': (['t', 'V1 a 0 DC 5', 'R1 a n1 1k', 'I1 n1 n2 DC 1m',
                        'R2 n2 n3 1k', 'C3 n3 n1 1u', '.op', '.end'],
                       ['NO-DC-PATH'], 'loud'),
    'M15-time-in-op': (['t', 'B1 out 0 V=5*u(TIME-1m)', 'R1 out 0 1k',
                        '.op', '.end'], ['B-TIME-IN-OP'], 'silent', 'warn'),
}


def main():
    rec = {'valid': [], 'malformed': []}
    print("=== VALID (expect zero errors) ===")
    for vid, net in VALID.items():
        f = V.validate(net)
        errs = V.codes(f, 'error')
        warns = V.codes(f, 'warn')
        ok = not errs
        rec['valid'].append({'id': vid, 'errors': errs, 'warns': warns,
                             'fp': not ok})
        print(f"{vid:16s} errors={errs} warns={warns} {'OK' if ok else 'FALSE-POSITIVE'}")
    print("=== MALFORMED (expect rejection + ngspice loudness) ===")
    for mid, spec in MALFORMED.items():
        net, exp_codes, ng_exp = spec[0], spec[1], spec[2]
        level = spec[3] if len(spec) > 3 else 'error'
        f = V.validate(net)
        errs = V.codes(f, 'error')
        warns = V.codes(f, 'warn')
        got = warns if level == 'warn' else errs
        hit = all(c in got for c in exp_codes)
        ctl = [l for l in net if l.strip().lower() != '.end'] + [
            '.control', 'run', '.endc', '.end']
        r = run_cir(ctl, timeout=20)
        loud = bool(r['warns'] or r['fatals'] or r['rc'] != 0)
        ng_ok = (loud and ng_exp == 'loud') or ((not loud) and ng_exp == 'silent')
        rec['malformed'].append({'id': mid, 'errors': errs, 'warns': warns,
                                 'expected': exp_codes, 'level': level,
                                 'rejected': hit,
                                 'ngspice_loud': loud,
                                 'ngspice_expect': ng_exp,
                                 'ngspice_match': ng_ok,
                                 'rc': r['rc'],
                                 'warns_n': len(r['warns']),
                                 'fatals': r['fatals'][:2]})
        print(f"{mid:16s} rej={hit} {errs} | ngspice loud={loud} (exp {ng_exp}) "
              f"{'OK' if (hit and ng_ok) else 'MISS'}")
    n_fp = sum(1 for v in rec['valid'] if v['fp'])
    n_fn = sum(1 for m in rec['malformed'] if not m['rejected'])
    n_ng = sum(1 for m in rec['malformed'] if not m['ngspice_match'])
    print(f"\nvalid FP: {n_fp}/{len(VALID)}  malformed FN: {n_fn}/{len(MALFORMED)} "
          f" ngspice-expect mismatch: {n_ng}/{len(MALFORMED)}")
    os.makedirs('logs', exist_ok=True)
    with open('logs/bench.json', 'w') as fh:
        json.dump(rec, fh, indent=1)
    print('-> logs/bench.json')


if __name__ == '__main__':
    main()
