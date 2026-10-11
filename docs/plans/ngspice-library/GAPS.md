# Known gaps

This is a running list for the gap-fix task, which runs between library batch 9 and the final review. When any task finds a gap, it appends a line here with an ID, its source task, the symptom, a repro and its current handling. The gap-fix task marks each entry `fixed`, `wontfix (reason)` or `deferred (reason)`.

| ID | From | Gap | Current handling | Status |
|---|---|---|---|---|
| G1 | T4 | A node joined only by capacitors is rejected, but ngspice solves it under `.tran ... uic` | Static reject (false positive) | open |
| G2 | T4 | The static check passes a function name that only some expression contexts accept; the engine then fails at runtime | Runtime error, after task 2's recovery | open |
| G3 | T2 | `M` level 60 (SOI3) aborts the whole process with 4 or 5 nodes | Refused | open: confirm the refusal is the right fix or find a safe subset |
| G4 | T2 | Diode levels 4 and 5 run but are undocumented | Refused | open |
| G5 | T2 | A 4-node VDMOS gets accepted by ngspice but gives a different answer | Rejected | open |
| G6 | T2 | `limit()` returns its input instead of clamping | UNSUPPORTED | open |
| G7 | T2 | Lossy LTRA (`O`) hangs ngspice; lossless works | Only lossless T/O | open: add a timeout or watchdog, or keep the static block |
| G8 | T4 | A `K` line coupling a resistor segfaults ngspice | Static reject | open: check the other K misuse forms |
| G9 | T3 | `nc` pins (no model port) are not supported; unused pins are tied off inside the body | Workaround | open |
| G10 | T1 | PSpice/LTspice vendor macromodels need `ngbehavior=ps`, which Bench pins off | Vendor models deferred | open: per-package compat mode? |
| G11 | T5 | A source whose negative pin is left unwired is accepted and simulates | Accepted (should warn or error) | open |
| G12 | T5 | There is no list-valued param type, so PWL sources can't be authored as packages | PWL skipped | open |
| G13 | T5 | Current-source EXP/SFFM variants and a dedicated AC-analysis source were not added | Skipped | open (check whether infra or scope) |
| G14 | T6 | ngspice's diode model silently raises `IBV` to `Is*BV/Vt` when the card asks for less (a warning only), so a card cannot state "leakage X at the rated voltage" when `Is` is large; the knee then sits at `BV` with current `Is` | Cards put `BV` at the rating and leave leakage to the saturation current (limitations say so); nothing detects it | open (maybe a validator `LIMITED-ANALYSIS` note for D models whose `IBV` is below `Is*BV/Vt`) |
| G15 | T6 | A latching subckt (SCR) with BJT transit time `Tf=Tr=1u` and floating internal nodes drove them to 1000 V and beyond after turn-off (trapezoidal integration of the regenerative loop; the result was a wrong, silent "success"). `Tf=Tr=0` fails to converge; `Tf=Tr=10n` is stable | Library uses `Tf=Tr=10n` and a 100 Meg leak; no static or runtime check catches a node that runs away to a diode breakdown | open (a runtime plausibility check on internal nodes far outside the supply range, or a method pin) |
| G16 | T6 | The sign convention of `Vto` for P-type models is not validated: a PJF with `Vto=+2.5` (the usual datasheet sign) loads and simulates as a dead device (1e-11 A); ngspice needs `Vto=-2.5` for PJF and for PMOS (negative) | Cards use the sign ngspice needs; tests check IDSS | open (type-aware sign check on `Vto`) |
| G17 | T6 | Bench has no AC analysis, so varactor capacitance, JFET/BJT small-signal gain and fT can be checked only through transient behaviour (the varactor test uses a voltage ramp) | Tests use ramps and pulses | open (scope: needs `.ac` support) |
| G18 | T6 | Phototransistor and photodiode (SPEC section 5) were not added (assigned to the sensors batch) | Deferred to batch 9 | open |
| G19 | T6 | Named MOSFET/BJT/JFET cards are project-authored datasheet fits, not vendor models; no vendor card's redistribution terms could be verified, so none was copied | Fits with datasheet URLs and revision recorded in provenance; labelled "not a vendor SPICE model" | open (revisit if a vendor library with a redistribution grant is adopted) |
| G20 | T7 | Bench runs `.tran` with tmax = tstep and exposes no integration method. A behavioural op-amp macromodel with an algebraic gain stage and near-ideal clamp diodes (N=0.01) rang numerically (a sustained 5-20 V "oscillation", a silent wrong success) when tstep was near 1/GBW, e.g. an LM324 gain-of-2 stage at tstep 2 us | `bench_opamp_macro` adds a pole at 100 x GBW on its first stage; a scan (4 parameter sets x 5 gains x 5 time steps) is clean | open (a per-deck `method=gear` option, or a ringing/plausibility check) |
| G21 | T7 | Switching-regulator ICs (buck/boost) were not added: no averaged or switch-level model could be checked against a datasheet with transient-only analysis | Deferred | open |
| G22 | T7 | Pins with no modelled function (LM741 offset null and NC, LM311 BALANCE and STROBE) are left off the symbols, because `nc` pins are not supported (G9) | Pins omitted; limitations say so | open (with G9) |
| G23 | T7 | Library search matches substrings of descriptions, so whole-catalog search assertions break on unrelated words ("esp" in "response" matched LM311/LM393 in `test_milestone_e2e`) | Batch C reworded the two descriptions | open (word-boundary matching, or tests filtered to their fixtures) |
