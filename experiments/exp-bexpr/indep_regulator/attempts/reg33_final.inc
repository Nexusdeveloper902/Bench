* reg33: 5V->3.3V LDO behavioral model (native ngspice, B-source only)
* pins: vin out gnd | nominal 3.3V, dropout 0.3V, soft limit ~0.5A
.subckt reg33 vin out gnd
Btgt tgt gnd V = (v(vin) < 3.6) ? (v(vin) - 0.3) : 3.3
Bout oint gnd V = 0.5*(v(tgt)+sqrt(v(tgt)*v(tgt)+1e-6)) - 0.05*i(vsens) - 50*0.5*((i(vsens)-0.5)+sqrt((i(vsens)-0.5)*(i(vsens)-0.5)+1e-6))
Vsens oint out DC 0
.ends reg33
