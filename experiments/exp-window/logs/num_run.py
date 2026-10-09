"""Numerical-analysis thread: segmented (.ic+uic) continuation quantification.
Batch subprocess only, timeout-contained. Reuses exp-safety/helper_safety.py.
Tmp artifacts use num_ prefix in /tmp/opencode.
"""
import sys, os, json, math, time, bisect
sys.path.insert(0, '<repo>/experiments/exp-safety')
import helper_safety as H

OUT_JSON = '<repo>/experiments/exp-window/logs/agent_numerical.json'
os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
TMP = '/tmp/opencode'
R, C = 10e3, 100e-9
LC_C, LC_L = 100e-9, 10e-3
T_RC = 1e-3

def rc_seg(v0, dur_s, tmax_s):
    base = ["RC seg", "V1 drv 0 DC 5", f"R1 drv cap {R}",
            f"C1 cap 0 {C} IC={v0!r}", f".ic v(cap)={v0!r}",
            f".tran {tmax_s} {dur_s!r} uic"]
    return H.tran_run(base, ["time", "v(cap)"], timeout=30, tag="num_rc")

def rc_ref(tmax="1u"):
    return rc_seg(0.0, T_RC, tmax)

def lc_seg(v0, i0, dur_s, tmax_s="1u"):
    base = ["LC seg", f"C1 tank 0 {LC_C} IC={v0!r}", f"L1 tank 0 {LC_L} IC={i0!r}",
            f".ic v(tank)={v0!r}", f".tran {tmax_s} {dur_s!r} uic"]
    return H.tran_run(base, ["time", "v(tank)", "i(l1)"], timeout=30, tag="num_lc")

def b_seg(vcap0, dur_s, tmax_s, t_off=0.0):
    # Continuation requires shifting absolute-TIME event expressions by the
    # segment start offset: ngspice resets TIME to 0 on every restarted run
    # (proven by probe: unshifted fine window sees drv=0 throughout, and the
    # coarse segA fabricates a u(0)=0.5 phantom 2.5V point at its endpoint).
    e = (f"V=5*(u(TIME+{t_off!r}-500u)-u(TIME+{t_off!r}-500.1u))")
    base = ["B blip", f"B1 drv 0 {e}",
            f"R1 drv cap {R}", f"C1 cap 0 {C} IC={vcap0!r}",
            f".ic v(cap)={vcap0!r}", f".tran {tmax_s} {dur_s!r} uic"]
    return H.tran_run(base, ["time", "v(drv)", "v(cap)"], timeout=90, tag="num_b")

def b_seg_unshifted(vcap0, dur_s, tmax_s):
    base = ["B blip", "B1 drv 0 V=5*(u(TIME-500u)-u(TIME-500.1u))",
            f"R1 drv cap {R}", f"C1 cap 0 {C} IC={vcap0!r}",
            f".ic v(cap)={vcap0!r}", f".tran {tmax_s} {dur_s!r} uic"]
    return H.tran_run(base, ["time", "v(drv)", "v(cap)"], timeout=90, tag="num_b")

def tail_v(series, key):
    return series[key][-1][1] if series.get(key) else None

def stat(r):
    return {"npts": r.get("npts"), "wall_ms": r.get("wall_ms"),
            "dt_min": r.get("dt_min"), "dt_max": r.get("dt_max"),
            "accepted": r.get("accepted"), "rejected": r.get("rejected"),
            "tran_iter": r.get("tran_iter"), "iter_per_pt": r.get("iter_per_pt"),
            "rc": r.get("rc")}

res = {"meta": {"tool": "ngspice-47 batch subprocess", "T_RC_ms": 1.0,
                "note": "1ms windows for iteration speed; cost scales as T_sim/w_min per safety report"},
       "t1_splits": [], "t1_nseg": [], "t2": {}, "t3": {}, "t4": {}, "t5": {}}

# ---------- T1a: split-point phase ----------
r_ref, s_ref = rc_ref("1u")
VREF = tail_v(s_ref, "v(cap)")
ts_ref = [t for t, _ in s_ref["time"]]
res["t1_ref"] = {"Vfinal": VREF, **stat(r_ref)}
for t_split in [0.1e-3, 0.3e-3, 0.5e-3, 0.693147e-3, 0.9e-3]:
    r1, s1 = rc_seg(0.0, t_split, "1u")
    v_sp = tail_v(s1, "v(cap)")
    r2, s2 = rc_seg(v_sp, T_RC - t_split, "1u")
    v_fin = tail_v(s2, "v(cap)")
    # phase: analytic slope dv/dt at split = (5-v)/tau
    slope = (5 - v_sp) / (R * C)
    res["t1_splits"].append({
        "t_split_ms": t_split * 1e3, "Vsplit": v_sp,
        "slope_kV_s": slope / 1e3,
        "region": "slewing" if slope > 1.0e3 else "flat",
        "Vfinal_seg": v_fin, "err_uV": (v_fin - VREF) * 1e6,
        "seg1": stat(r1), "seg2": stat(r2),
        "npts_total": (r1.get("npts") or 0) + (r2.get("npts") or 0)})
# ---------- T1b: error accumulation vs nseg ----------
for nseg in [2, 4, 8]:
    seg = T_RC / nseg
    v0 = 0.0; tot = 0; wall = 0; acc = 0; rej = 0
    for k in range(nseg):
        rk, sk = rc_seg(v0, seg, "1u")
        v0 = tail_v(sk, "v(cap)")
        tot += rk.get("npts") or 0; wall += rk.get("wall_ms") or 0
        acc += rk.get("accepted") or 0; rej += rk.get("rejected") or 0
    res["t1_nseg"].append({"nseg": nseg, "Vfinal": v0,
                           "err_uV": (v0 - VREF) * 1e6,
                           "npts_total": tot, "wall_ms_total": wall,
                           "accepted": acc, "rejected": rej})

# ---------- T2+T4: LC restart ----------
r_lc, s_lc = lc_seg(5.0, 0.0, T_RC)
ts = [t for t, _ in s_lc["time"]]; vs = [v for _, v in s_lc["v(tank)"]]
il = [v for _, v in s_lc["i(l1)"]]
E = [0.5 * LC_C * v * v + 0.5 * LC_L * i * i for v, i in zip(vs, il)]
res["t2"]["ref"] = {"Vfinal": vs[-1], "Ifinal": il[-1], "E0": E[0],
                    "Efinal": E[-1], "rel_drift": (E[-1] - E[0]) / E[0], **stat(r_lc)}
res["t4"]["ref_E0"] = E[0]; res["t4"]["ref_Efinal"] = E[-1]
res["t4"]["ref_rel_drift"] = (E[-1] - E[0]) / E[0]
res["t2"]["cases"] = []
for name, t_target in [("vzero_imax", 50e-6), ("vnegpeak_didtmax", 100e-6)]:
    idx = bisect.bisect_left(ts, t_target)
    t_sp = ts[idx]; v_sp, i_sp = vs[idx], il[idx]
    r1, s1 = lc_seg(5.0, 0.0, t_sp)  # seg1 sanity (handoff state check)
    v1 = tail_v(s1, "v(tank)"); i1 = tail_v(s1, "i(l1)")
    r2, s2 = lc_seg(v1, i1, T_RC - t_sp)
    v2 = [v for _, v in s2["v(tank)"]]; i2 = [v for _, v in s2["i(l1)"]]
    t2 = [t for t, _ in s2["time"]]
    E2 = [0.5 * LC_C * v * v + 0.5 * LC_L * i * i for v, i in zip(v2, i2)]
    E1tail = 0.5 * LC_C * v1 * v1 + 0.5 * LC_L * i1 * i1
    # time-aligned local error: seg2 time t maps to ref time t_sp+t; interp ref (uniform 1us)
    errs = []
    for k in range(min(5, len(t2))):
        ta = t_sp + t2[k]
        j = bisect.bisect_left(ts, ta)
        j = max(1, min(j, len(ts) - 1))
        f = (ta - ts[j - 1]) / max(ts[j] - ts[j - 1], 1e-18)
        v_ref = vs[j - 1] + f * (vs[j] - vs[j - 1])
        errs.append(abs(v2[k] - v_ref))
    # ringing check: max |v| overshoot beyond ref envelope in seg2
    ring = max(abs(v) for v in v2) - max(abs(v) for v in vs)
    res["t2"]["cases"].append({
        "name": name, "t_split_us": t_sp * 1e6, "v_split": v_sp, "i_split_mA": i_sp * 1e3,
        "didt_A_s": v_sp / LC_L, "dvdt_kV_s": -i_sp / LC_C / 1e3,
        "handoff_match_uV": (v1 - v_sp) * 1e6, "handoff_match_nA": (i1 - i_sp) * 1e9,
        "Vfinal_seg": v2[-1], "Vfinal_ref": vs[-1], "err_uV": (v2[-1] - vs[-1]) * 1e6,
        "err_i_uA": (i2[-1] - il[-1]) * 1e6,
        "E_jump_at_restart_J": E2[0] - E1tail,
        "E_jump_rel": (E2[0] - E1tail) / E[0],
        "Efinal_seg": E2[-1], "Efinal_rel_err": (E2[-1] - E[-1]) / E[0],
        "first5_dt_s": [(b - a) for a, b in zip(t2[:6], t2[1:7])],
        "max_abs_err_first5_V": max(errs) if errs else None,
        "overshoot_vs_ref_V": ring,
        "seg2": stat(r2)})
    if name == "vzero_imax":
        res["t4"]["restart_E_jump_J"] = E2[0] - E1tail
        res["t4"]["restart_E_jump_rel"] = (E2[0] - E1tail) / E[0]
        res["t4"]["seg_Efinal"] = E2[-1]
# adversarial: drop inductor state
idx = bisect.bisect_left(ts, 50e-6); v_sp = vs[idx]
base_bad = ["LC BAD", f"C1 tank 0 {LC_C} IC={v_sp!r}", f"L1 tank 0 {LC_L} IC=0",
            f".ic v(tank)={v_sp!r}", ".tran 1u 950u uic"]
r_bad, s_bad = H.tran_run(base_bad, ["time", "v(tank)", "i(l1)"], timeout=30, tag="num_lcbad")
vb = [v for _, v in s_bad["v(tank)"]] if s_bad.get("v(tank)") else [float("nan")]
ib = [v for _, v in s_bad["i(l1)"]] if s_bad.get("i(l1)") else [float("nan")]
Ebad0 = 0.5 * LC_C * vb[0] * vb[0] + 0.5 * LC_L * ib[0] * ib[0]
res["t2"]["adversarial_no_L_state"] = {
    "Vfinal_seg": vb[-1], "Vfinal_ref": vs[-1], "err_V": vb[-1] - vs[-1],
    "E0_bad_J": Ebad0, "E0_true_J": E[idx] if idx < len(E) else None,
    "E_drop_rel": (Ebad0 - E[0]) / E[0], **stat(r_bad)}

# ---------- T3: margin/settle sweep ----------
T0, W, T_END = 500e-6, 100e-9, 1e-3
r_ff, s_ff = b_seg(0.0, T_END, "10n")
tsf = [t for t, _ in s_ff["time"]]
dvf = [v for _, v in s_ff["v(drv)"]]; cvf = [v for _, v in s_ff["v(cap)"]]
peak_ref = max(dvf)
# tail at window-exit reference needs interpolation; store series for reuse
def interp(ts, vv, ta):
    j = max(1, min(bisect.bisect_left(ts, ta), len(ts) - 1))
    f = (ta - ts[j - 1]) / max(ts[j] - ts[j - 1], 1e-18)
    return vv[j - 1] + f * (vv[j] - vv[j - 1])
res["t3"]["ref"] = {"npts": r_ff.get("npts"), "wall_ms": r_ff.get("wall_ms"),
                    "drv_peak": peak_ref, "cap_peak": max(cvf),
                    "cap_final": cvf[-1], **{k: r_ff.get(k) for k in
                    ("accepted", "rejected", "tran_iter", "iter_per_pt", "dt_max", "dt_min")}}
res["t3"]["sweep"] = []
for margin, settle in [(0.0, 0.0), (100e-9, 100e-9), (1e-6, 1e-6), (10e-6, 10e-6)]:
    w0, w1 = T0 - margin, T0 + W + settle
    rA, sA = b_seg(0.0, w0, "10u", 0.0)
    vA = tail_v(sA, "v(cap)")
    rB, sB = b_seg(vA, w1 - w0, "10n", w0)
    vB = tail_v(sB, "v(cap)")
    peakB = max(v for _, v in sB["v(drv)"]) if sB.get("v(drv)") else float("nan")
    rC, sC = b_seg(vB, T_END - w1, "10u", w1)
    vC = tail_v(sC, "v(cap)")
    cap_exit_ref = interp(tsf, cvf, w1)
    ntot = sum(x.get("npts") or 0 for x in (rA, rB, rC))
    wall = sum(x.get("wall_ms") or 0 for x in (rA, rB, rC))
    res["t3"]["sweep"].append({
        "margin_s": margin, "settle_s": settle,
        "drv_peak_err_V": peakB - peak_ref,
        "tail_err_at_exit_V": vB - cap_exit_ref, "tail_exit_V": vB,
        "tail_exit_ref_V": cap_exit_ref,
        "final_V": vC, "final_ref_V": cvf[-1], "final_err_V": vC - cvf[-1],
        "final_err_rel_to_cap_peak": (vC - cvf[-1]) / max(max(cvf), 1e-18),
        "npts_total": ntot, "wall_ms_total": wall,
        "cost_ratio_vs_fullfine": ntot / max(r_ff.get("npts") or 1, 1),
        "segA": stat(rA), "segB": stat(rB), "segC": stat(rC)})

# naive (unshifted) continuation as the expected-failure demonstration
rNA, sNA = b_seg_unshifted(0.0, T0, "10u")
vNA = tail_v(sNA, "v(cap)")
rNB, sNB = b_seg_unshifted(vNA, W, "10n")
peakNB = max(v for _, v in sNB["v(drv)"]) if sNB.get("v(drv)") else float("nan")
rNC, sNC = b_seg_unshifted(tail_v(sNB, "v(cap)"), T_END - T0 - W, "10u")
res["t3"]["naive_unshifted"] = {
    "drv_peak_V": peakNB, "drv_peak_err_V": peakNB - peak_ref,
    "final_V": tail_v(sNC, "v(cap)"), "final_ref_V": cvf[-1],
    "note": "TIME resets to 0 per segment; absolute-TIME event invisible without shift"}

# ---------- T5: stepping stats ----------
res["t5"]["rc_ref"] = {k: r_ref.get(k) for k in
    ("accepted", "rejected", "tran_iter", "iter_per_pt", "npts", "wall_ms")}
tot_acc = sum((c.get("accepted") or 0) for c in [r_ff])
res["t5"]["b_fullfine"] = {k: r_ff.get(k) for k in
    ("accepted", "rejected", "tran_iter", "iter_per_pt", "npts", "wall_ms")}
rej_rates = {}
for row in res["t3"]["sweep"]:
    a = sum(s.get("accepted") or 0 for s in (row["segA"], row["segB"], row["segC"]))
    rj = sum(s.get("rejected") or 0 for s in (row["segA"], row["segB"], row["segC"]))
    rej_rates[f"m{row['margin_s']}_s{row['settle_s']}"] = {
        "accepted": a, "rejected": rj,
        "reject_rate": rj / max(a + rj, 1)}
res["t5"]["b_segmented_reject_rates"] = rej_rates
res["t5"]["lc_ref"] = {k: r_lc.get(k) for k in
    ("accepted", "rejected", "tran_iter", "iter_per_pt", "npts", "wall_ms")}

with open(OUT_JSON, "w") as f:
    json.dump(res, f, indent=1)
print("wrote", OUT_JSON)
print(json.dumps({"t1": [(c["t_split_ms"], c["err_uV"]) for c in res["t1_splits"]],
                  "t1n": [(c["nseg"], c["err_uV"]) for c in res["t1_nseg"]],
                  "t2": [(c["name"], c["err_uV"]) for c in res["t2"]["cases"]],
                  "t3": [(c["margin_s"], c["settle_s"], c["final_err_V"], c["cost_ratio_vs_fullfine"]) for c in res["t3"]["sweep"]]}, indent=1))
