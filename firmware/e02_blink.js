// E02: real AVR8js firmware toggling GPIO (PORTB5, Arduino LED pin).
// Firmware assembled from AVR asm via avr8js test-utils assembler.
// Loop: SBI PORTB,5 / RCALL delay / CBI PORTB,5 / RCALL delay / RJMP loop
// Delay subroutine gives a known cycle count per half-period.
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
const t0wall = performance.now();
portB.addListener((val, old) => {
  events.push({ cycles: cpu.cycles, t_us: cpu.cycles / F_CPU * 1e6, val: val & 0x20 ? 1 : 0 });
});
// run N instructions (cap)
const N = 200000;
for (let i = 0; i < N; i++) { avrInstruction(cpu); cpu.tick(); }
const wall = performance.now() - t0wall;
const instrPerSec = N / (wall / 1000);
console.log(JSON.stringify({
  progWords: program.length, progHex: Array.from(program.slice(0, 8)).map(w => w.toString(16)),
  N, wallMs: wall, instrPerSec: Math.round(instrPerSec),
  cyclesPerSec: Math.round(instrPerSec * (cpu.cycles / N)),
  totalCycles: cpu.cycles, simTimeUs: cpu.cycles / F_CPU * 1e6,
  gpioEvents: events.length,
  firstEvents: events.slice(0, 10),
  lastEvent: events[events.length - 1],
  togglePeriodCycles: events.length >= 3 ? events[2].cycles - events[0].cycles : null,
}, null, 1));
