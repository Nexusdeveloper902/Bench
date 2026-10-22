# Task: Expand Bench's ngspice Engine Support and Build a Comprehensive Electrical Component Library

(Original brief, verbatim. Source of truth for the sequential-agent effort; progress lives in `HANDOFF.md` next to this file.)

## Objective

Expand Bench into a much more capable, extensible ngspice-based electronics simulator by fixing the foundational limitations in its existing electrical pipeline and then implementing a broad component library.

The repository currently has important constraints:

- The modeled and validated device set is limited to R, C, L, D, V, I, Q, J, and M.
- The current validation implementation in `benchcore/src/validate.rs` does not adequately inspect subcircuit internals.
- Subcircuit instances receive limited validation, while `.subckt` bodies are treated as opaque.
- Component packages cannot currently carry reusable subcircuit bodies; `docs/adding-components.md` primarily documents device letters and fixed netlist templates.

The milestone report also identifies transistors, operational amplifiers, and subcircuits as requiring additional engine work.

Treat these findings as the starting point, not as a complete description of the repository. Inspect the actual implementation and verify the current behavior before making changes.

**Do not try to implement hundreds of catalog entries on top of these limitations. First make the existing component and simulation infrastructure capable of representing, generating, validating, and simulating the additional devices correctly. Then expand the library substantially.**

Do the implementation, not just a plan or architecture proposal.

## 1. Phase one: understand the complete pipeline

Inspect the relevant source code, tests, documentation, and component packages.

Trace the actual flow from:
- Component definitions and metadata.
- Symbol pins and electrical terminals.
- Circuit connectivity and net assignment.
- Component instantiation and parameters.
- Netlist generation.
- Model and subcircuit resolution.
- Validation and diagnostics.
- ngspice execution.
- Simulation results and errors.

Identify where supported device types are enumerated, where netlist templates are selected, how terminal counts are determined, and how model cards and subcircuits enter the generated deck.

Inspect `benchcore/src/validate.rs`, `docs/adding-components.md`, and `docs/milestone/REPORT.md`, along with their callers, related implementations, and tests.

Establish which capabilities already exist elsewhere in the repository so that you extend the actual system rather than duplicate it.

## 2. Phase two: generalize component and device support

Extend the existing architecture to support the device types and model constructs needed for a substantial electrical library.

Requirements:

- Device support must not be constrained to the current hardcoded set of element letters.
- Terminal counts and ordering must be represented explicitly and validated against the selected device or subcircuit interface.
- Component definitions must be able to describe the correct netlist representation and parameter mapping.
- Device model references, model types, and relevant model-level constraints must be handled correctly.
- The system must distinguish device primitives, model-backed devices, and subcircuit-backed components.
- Existing component definitions and saved circuits should remain compatible wherever reasonably possible.
- New support must follow the syntax and semantics of the actual ngspice version used by Bench.

Support the additional ngspice constructs needed for the intended component library, including relevant semiconductor devices, controlled sources, switches, behavioral elements, and subcircuits where supported.

Do not assume that every SPICE dialect uses identical syntax, terminal ordering, parameter conventions, or model semantics. Use ngspice's actual behavior as the source of truth.

Avoid creating a universal simulation abstraction that hides important device-specific semantics. Use reusable infrastructure with explicit device definitions and correct validation.

## 3. Phase three: make subcircuits first-class component packages

Component packages must be capable of defining or referencing a real ngspice subcircuit.

Design this capability to fit the existing package architecture rather than inventing a parallel component system.

A subcircuit-backed component should be able to define or reference:
- Its stable component identifier.
- Its symbol and electrical pins.
- Its external port names and ordering.
- Its `.subckt` definition or model-file dependency.
- Required parameters and defaults.
- Nested subcircuits and model dependencies.
- Source and licensing information.
- Its simulation-support status and known fidelity limitations.

The system must correctly map visible component pins to the ordered external terminals of the subcircuit.

Support multiple instances of a subcircuit without name collisions, including internal nodes, local element names, and nested dependencies as required by ngspice semantics.

Handle parameters and default values correctly. Avoid duplicating the same model body into every instance if the simulator can reference one shared definition.

Support reusable model packages and dependencies so that a component author can add an IC by supplying its definition, pin mapping, and model dependencies rather than modifying multiple core subsystems.

Ensure model packaging, path resolution, generated netlists, and any model caching remain correct and reproducible.

Update `docs/adding-components.md` with the actual supported package format and examples.

## 4. Phase four: validate subcircuits and generated netlists properly

Replace the current opaque-subcircuit limitation with meaningful validation.

The validator should inspect subcircuit definitions and their bodies, including:
- Subcircuit declarations and external port lists.
- Internal element definitions and their supported terminal counts.
- Internal node references and node consistency.
- Model declarations and references.
- Nested subcircuit instances and their port counts.
- Parameter declarations, overrides, and references where supported.
- Dependency resolution.
- Duplicate identifiers and conflicting definitions.
- Invalid element syntax and malformed device definitions.
- Correct handling of supported continuation lines and model syntax.

Perform validation at the appropriate stages: package ingestion, circuit assembly, and generated-deck validation as appropriate to the existing design.

### Connectivity and grounding

Preserve existing connectivity and floating-node checks while extending them to subcircuits.

Account for the fact that valid electrical circuits do not necessarily require every internal node to connect directly to ground. Do not implement simplistic checks that reject legitimate floating subcircuits, isolated differential inputs, transformer windings, or other valid configurations.

Distinguish external circuit connectivity from internal subcircuit connectivity.

Treat node 0 and ground references according to actual ngspice semantics. Do not assume every subcircuit must contain an explicit internal ground connection.

Use meaningful graph and reference analysis where it is appropriate, but do not claim to prove a circuit electrically well-posed merely because its syntax and node references are valid.

### Unsupported syntax and opaque constructs

Do not silently treat unrecognized constructs as validated.

Where the parser cannot safely understand a construct:
- Produce a clear diagnostic or explicitly limited validation status.
- Preserve legitimate ngspice syntax when it can safely be passed through.
- Avoid false positives for valid constructs that the validator does not analyze.
- Never claim complete internal validation when parts of the subcircuit remain opaque.

The validator must distinguish syntax errors, unresolved dependencies, connectivity issues, unsupported constructs, and warnings about limited analysis.

## 5. Phase five: implement a broad component library

Once the foundational infrastructure works, expand the component library substantially. Aim for hundreds of meaningful definitions and variants where feasible, without manufacturing aliases or placeholders to inflate the count.

Prioritize:

**Passive components**
- Resistors, potentiometers, rheostats, resistor arrays.
- Ceramic, electrolytic, tantalum, and film capacitors.
- Inductors, coupled inductors, transformers.
- Fuses, resettable-fuse equivalents, and transmission-line elements where supported.

**Diodes**
- General-purpose, switching, fast-recovery, and Schottky diodes.
- Zener and TVS diodes.
- LEDs of multiple colors.
- Infrared LEDs, photodiodes, phototransistors, varactors, and other supported devices.

**Transistors**
- NPN and PNP BJTs.
- Small-signal, switching, and power BJTs.
- N-channel and P-channel MOSFETs.
- Logic-level and power MOSFETs.
- JFETs, IGBTs, SCRs, TRIACs, DIACs, and other compatible semiconductor devices.

Include generic parameterized models and representative named devices backed by credible model data.

**Switches and control devices**
- SPST, SPDT, DPST, DPDT, and related switch configurations.
- Pushbuttons, maintained switches, and controlled switches.
- Relays, reed switches, and suitable electrical equivalents.

**Sources and power electronics**
- DC and AC sources.
- Pulse, sinusoidal, piecewise-linear, and behavioral sources.
- Batteries and nonideal sources.
- Voltage references and current sources.
- Rectifiers, linear regulators, adjustable regulators, and compatible switching-regulator models.
- Protection devices and controlled sources.

**Sensors**
- LDRs with configurable illumination-dependent resistance.
- NTC and PTC thermistors.
- RTDs and supported temperature-sensor equivalents.
- Photodiodes and phototransistors.
- Hall-sensor electrical equivalents.
- Force-sensitive resistors, strain gauges, and position sensors.
- Humidity, pressure, gas, and other sensors where credible electrical models can be represented.
- Microphone and ultrasonic-transducer electrical equivalents.
- Current-sensing elements and other useful electrical sensor models.

Sensors must expose relevant external stimulus parameters where appropriate. Model their electrical responses through compatible ngspice elements or subcircuits. Do not introduce a general physical-environment simulator.

**Analog ICs**
- Operational amplifiers.
- Comparators.
- Timers such as the 555 family.
- Voltage references and regulators.
- Analog switches and multiplexers.
- Audio amplifiers and driver ICs.
- Other useful analog devices with compatible models.

**Digital and mixed-signal ICs**
- Common logic gates and buffers.
- Schmitt-trigger devices.
- Multiplexers and analog switches.
- Flip-flops, latches, counters, and shift registers when suitable electrical subcircuits are available.
- Common 74-series and CD4000-series models.
- Compatible ADC and DAC models.

Include these only as ngspice-compatible electrical models or subcircuits. Do not implement MCU emulation, firmware execution, HDL simulation, or a separate digital simulator.

**Other electrical devices**
- Optocouplers.
- Motors and solenoids represented by electrical equivalents.
- Speakers and transducers represented by electrical equivalents.
- Connectors, terminals, probes, and measurement elements.
- Crystals, resonators, and other useful electrical components.

Adapt the scope to the models and features the actual engine can support. If a component cannot be represented meaningfully through the current pipeline, defer it instead of adding a fake implementation.

## 6. Model quality and licensing

Prefer credible existing ngspice-compatible models and subcircuits when available.

For every external model, preserve provenance, version information where available, licensing terms, and required attribution.

For project-authored models, distinguish validated behavior from approximations.

Do not invent manufacturer-accurate specifications or imply that a simplified model reproduces every characteristic of a physical device.

Record whether each component uses a native ngspice primitive, an existing model, a project-authored subcircuit, a behavioral model, or a simplified electrical equivalent.

Model dependencies must resolve deterministically. Generated netlists must contain valid declarations in an order and structure accepted by ngspice.

## 7. Testing strategy

Build tests alongside each foundational change.

**Device support**
- Correct terminal counts and ordering.
- Correct netlist generation for supported element types.
- Correct model-level handling and parameter validation.
- Regression coverage for existing R, C, L, D, V, I, Q, J, and M components.

**Subcircuits**
- Correct external port mapping.
- Valid nested subcircuits.
- Correct model and dependency resolution.
- Correct parameter propagation.
- Unique internal nodes and element identifiers across instances.
- Detection of malformed definitions and unresolved dependencies.
- Correct distinction between external and internal connectivity.
- Clear diagnostics for constructs the validator cannot fully inspect.

**Actual simulation**
- Generate representative decks and run them through the real ngspice installation.
- Verify passive circuits, transistor circuits, op-amp circuits, sensor circuits, and multi-component subcircuits.
- Compare outputs with analytical expectations or trusted model references.
- Include circuits containing multiple instances of the same subcircuit.
- Confirm that existing supported circuits continue to work.

Do not treat successful parsing as proof of correct electrical behavior. Do not claim a model is validated solely because ngspice accepted the deck.

## 8. Implementation discipline

Work in coherent, testable increments.

First fix the foundational limitations. Then implement the component families using the resulting infrastructure.

Avoid unrelated refactors, unnecessary frameworks, duplicate component systems, and premature abstractions. Preserve compatibility where reasonably possible.

Do not stop after writing an architecture plan. Implement as much of the work as possible in the current task, running tests and resolving regressions as you go.

If the scope exceeds the available time, leave the repository in a working state and report completed work, outstanding tasks, and genuine blockers accurately.

## 9. Final deliverables

Report:
- The original architectural limitations confirmed during inspection.
- The engine, package-format, netlist-generation, and validation changes implemented.
- The component families and definitions actually added.
- The distinction between catalog entries, device models, and subcircuits.
- The number of meaningful new component definitions.
- The model sources and licensing details.
- Tests actually executed and their results.
- Remaining limitations, unsupported ngspice constructs, and recommended next steps.

**Success means Bench can represent, package, generate, validate, and simulate a substantially broader set of real electrical components through its existing ngspice engine, with subcircuits treated as first-class components and validation that is honest about what it can and cannot prove.**
