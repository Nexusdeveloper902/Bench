// E08 helper: emit real AVR8js toggle events as JSON for the Python coupler.
// Same blink firmware as e02; prints FULL event list (no truncation).
const { CPU, AVRIOPort, portBConfig, avrInstruction } = require('avr8js');
const { asmProgram } = require('avr8js/dist/cjs/utils/test-utils');
const F_CPU = 16e6;
const { program } = asmProgram(`
  SBI 0x04, 5
main:
  SBI 0x05, 5
  RCALL delay
  CBI 0x05, 5
  RCALL delay
  RJMP main
delay:
  LDI R24, 100
loop:
  DEC R24
  BRNE loop
  RET
`);
const cpu = new CPU(program);
const portB = new AVRIOPort(cpu, portBConfig);
const events = [];
let pinState = 0;
portB.addListener((val) => {
  const s = (val & 0x20) ? 1 : 0;
  if (s !== pinState) { pinState = s; events.push({ t: cpu.cycles / F_CPU, v: s ? 5 : 0 }); }
});
const N = 60000;
const t0 = performance.now();
for (let i = 0; i < N; i++) { avrInstruction(cpu); cpu.tick(); }
const wallMs = performance.now() - t0;
console.log(JSON.stringify({
  f_cpu: F_CPU, n_instr: N, wallMs, totalCycles: cpu.cycles,
  simTime: cpu.cycles / F_CPU, instrPerSec: Math.round(N / (wallMs / 1000)),
  events,
}));
