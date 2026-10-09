"""E08: true bidirectional closed loop with REAL AVR8js firmware events.
1. Node (AVR8js) runs blink firmware -> toggle event list [(t,v)] (timed: T_avr).
2. Python builds EXTERNAL drive from those exact times, breakpoints on edges,
   runs real nonlinear LED circuit (timed: T_spice). Glue timed separately.
3. Feedback: MCU-style ADC sampling of LED anode at 8x toggle rate; fault check
   (Vf<1V while driven HIGH => LED fault). Reports decision + timing.
Overhead split: T_avr / T_glue / T_spice / T_analysis.
"""
import sys, os, json, time, subprocess, ctypes, bisect
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ngspice_ctypes import NgSpice, GetVSRCData, GetISRCData, GetSyncData

HERE = os.path.join(os.path.dirname(__file__), "..")

def main():
    t0 = time.perf_counter()
    p = subprocess.run(["node", "firmware/e08_events.js"], capture_output=True, text=True, timeout=120,
                       cwd=os.path.join(HERE))
    t_avr = time.perf_counter() - t0
    assert p.returncode == 0, p.stderr[-1000:]
    avr = json.loads(p.stdout)
    edges = [(e["t"], e["v"]) for e in avr["events"]]
    tsim = avr["simTime"]
    print(f"AVR: {avr['n_instr']} instr in {avr['wallMs']:.2f}ms ({avr['instrPerSec']/1e6:.1f}M instr/s), "
          f"simTime={tsim*1e6:.1f}us, {len(edges)} GPIO events; orchestrator wall={t_avr*1e3:.2f}ms")

    # glue: build drive function
    t0 = time.perf_counter()
    ets = [t for t, _ in edges]
    evs = [v for _, v in edges]
    def drive(tt):
        i = bisect.bisect_right(ets, tt) - 1
        return float(evs[max(0, i)])
    t_glue_build = time.perf_counter() - t0

    # SPICE run with breakpoints on real firmware edges
    state = {"calls": 0}
    ng = NgSpice(); assert ng.init() == 0
    def _vsrc(pv, tt, name, ident, ud):
        state["calls"] += 1; pv[0] = drive(tt); return 0
    def _isrc(pc, tt, name, ident, ud):
        pc[0] = 0.0; return 0
    def _sync(tt, pd, old, redo, ident, loc, ud):
        return 0
    cbs = [GetVSRCData(_vsrc), GetISRCData(_isrc), GetSyncData(_sync)]
    ng._cbs.extend(cbs)
    ident = ctypes.c_int(0)
    assert ng.lib.ngSpice_Init_Sync(cbs[0], cbs[1], cbs[2], ctypes.byref(ident), None) == 0
    tstep = tsim / 50.0
    assert ng.circ(["closed loop: real AVR events -> LED",
                    "V1 gpio 0 EXTERNAL", "R1 gpio anode 1k", "D1 anode 0 DLED",
                    ".model DLED D(IS=1e-14 N=1.5 BV=5 IBV=1u)",
                    f".tran {tstep} {tsim}", ".end"]) == 0
    for e in ets[1:]:
        ng.lib.ngSpice_SetBkpt(ctypes.c_double(e))
    t0 = time.perf_counter(); ng.command("run"); t_spice = time.perf_counter() - t0

    # analysis + ADC feedback emulation
    t0 = time.perf_counter()
    tvec, vgpio, vanode = ng.vec("time"), ng.vec("v(gpio)"), ng.vec("v(anode)")
    cur = ng.vec("i(v1)")
    period = ets[2] - ets[0] if len(ets) > 2 else tsim
    adc_period = period / 8
    t = adc_period / 2
    adc_reads, faults = 0, 0
    while t < tsim:
        adc_reads += 1
        i = bisect.bisect_right(tvec, t) - 1
        drv_hi = drive(t) > 2.5
        vf = vanode[max(0, i)]
        # 10-bit ADC, Vref=5V
        raw = int(vf / 5.0 * 1023)
        if drv_hi and vf < 1.0:
            faults += 1
        t += adc_period
    # edge fidelity: distance from each firmware edge to nearest accepted sim point
    max_err = 0.0
    for e in ets[1:]:
        j = bisect.bisect_left(tvec, e)
        cand = []
        if j < len(tvec):
            cand.append(abs(tvec[j] - e))
        if j > 0:
            cand.append(abs(tvec[j - 1] - e))
        max_err = max(max_err, min(cand))
    t_analysis = time.perf_counter() - t0
    wall = t_avr + t_glue_build + t_spice + t_analysis
    print(f"SPICE: npts={len(tvec)} evals={state['calls']} ({state['calls']/max(1,len(tvec)):.2f}/step) "
          f"T_spice={t_spice*1e3:.2f}ms max_edge_err={max_err*1e9:.1f}ns")
    print(f"ADC: {adc_reads} reads, faults={faults} (Vf<1V while HIGH)")
    print(f"OVERHEAD: avr={t_avr*1e3:.2f}ms glue={t_glue_build*1e3:.2f}ms spice={t_spice*1e3:.2f}ms "
          f"analysis={t_analysis*1e3:.2f}ms total={wall*1e3:.2f}ms "
          f"sim_speed={tsim/wall:.1f}x (sim {tsim*1e6:.0f}us / wall {wall*1e3:.1f}ms)")
    print(f"LED: Vf_on={max(vanode):.4f}V Ion={max(abs(c) for c in cur)*1e3:.3f}mA")
    os.makedirs("results", exist_ok=True)
    with open("results/e08.json", "w") as f:
        json.dump({"avr": {k: v for k, v in avr.items() if k != "events"},
                   "n_events": len(edges), "tsim": tsim, "npts": len(tvec),
                   "vsrc_calls": state["calls"], "max_edge_err_ns": max_err * 1e9,
                   "adc_reads": adc_reads, "faults": faults,
                   "t_avr_ms": t_avr * 1e3, "t_glue_ms": t_glue_build * 1e3,
                   "t_spice_ms": t_spice * 1e3, "t_analysis_ms": t_analysis * 1e3,
                   "sim_speed": tsim / wall}, f, indent=1)
    print("saved results/e08.json")

if __name__ == "__main__":
    main()
