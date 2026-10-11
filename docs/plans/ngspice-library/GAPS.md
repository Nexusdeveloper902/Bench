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
