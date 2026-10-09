# Mission: Build the Component Ecosystem and Transform the Native UI

The project already has a substantial electrical simulation subsystem, a native application, and a working GUI-to-simulator path demonstrated by an LED example.

Now build the next major subsystem: **a polished, Tinkercad-inspired simulation workspace backed by a genuinely extensible component ecosystem.**

The work has two tightly connected parts:

1. Design the new interface in Penpot, then implement it in the existing native application.
2. Build a component framework that makes it straightforward to introduce new components and categories, including components whose simulation behavior has not yet been implemented.

This is not merely a visual redesign, nor is it a request to implement hundreds of components. The goal is to establish a complete, usable foundation that can grow as the simulator gains more capabilities.

## 1. Inspect the current project

Examine the native application, Simulation API, electrical backend, component representations, persistence, tests, documentation, and any existing Penpot designs or design integrations. Run the current tests and verify the existing working LED workflow. Identify what exists, what can be reused, what is incomplete, and what must change. Do not replace working systems without a concrete reason. Verify documentation against code and tests. Produce a concise implementation plan; resolve architectural questions through investigation and targeted experiments.

## 2. Design the interface in Penpot FIRST

Create the actual interface design in Penpot before implementing the UI redesign. Target: a coherent, Tinkercad-inspired electronics workspace (not a clone) where building and inspecting circuits is the primary activity.

Establish visual language, navigation, major panels, reusable UI elements, interaction patterns. Design the complete set of important screens and states: component library, searching and filtering, component placement, circuit editing, selection, component properties, simulation controls, result inspection, unsupported-component states. Define interactive states: empty search results, selected components, invalid definitions, loading, simulation failure, components without simulation support.

Use Penpot reusable components and a consistent visual system: typography, spacing, color, iconography, component-card patterns, panel layouts, interaction specs. Retain elements of the existing visual identity worth preserving. Review the design before implementation; correct inconsistent interactions or missing states.

**The implementation should follow the actual Penpot design.** If a detail is impractical in the existing application, adapt deliberately and document why.

## 3. Transform the application into a usable simulation workspace

Implement the designed interface in the existing native app: component library, primary workspace canvas, properties inspector, simulation controls/results, per the Penpot design. Interactions: select, place, move, rotate, delete components; create/remove connections; edit properties; navigate (zoom, pan, selection feedback, clear connections). No decorative effects that don't improve the experience. Keep using the existing Simulation API. Do not move simulation logic into UI or duplicate the electrical model in the frontend.

## 4. Build a shared, extensible component catalog (central deliverable)

A registry/catalog that discovers, describes, validates, and displays components independently of whether they can be simulated. Metadata:

* Stable identity and version.
* Display name, description, aliases, keywords, tags.
* Category and subcategory.
* Icon and presentation assets.
* Domain classification.
* Pins/terminals/ports or other declared interfaces.
* Configurable parameters: types, defaults, units, validation constraints.
* Documentation references.
* References to simulation implementations when they exist.
* Explicit capability declarations and limitations.

Choose representation/package format from the existing codebase and extensibility needs. Not a list of frontend hardcoded cases. Adding a component = well-defined registration/package-loading workflow; it then appears through reusable UI (card, name, icon, properties panel) without modifying unrelated frontend code. Adding a category must not require redesigning the app. Document how to add and validate components.

## 5. Separate component definitions from simulation implementations

Existence, appearance, interfaces, configuration, and simulation behavior are separate concerns. A diode uses its electrical model and the existing electrical backend. An ESP32 can appear in the catalog, be placed, show pins/metadata, and be persisted without MCU emulation. Share catalog and lifecycle; do not force a universal simulation representation. Establish registration points for domain implementations without designing the internals of nonexistent domains. Prefer declarative capabilities over inferring from name/category.

Never give an unimplemented component fake behavior. Editor distinguishes: placeable / interface defined / simulation implemented. Preserve unsupported components in projects and explain limitations. If a circuit contains a component that can't participate in a simulation, validation must report it; never claim a successful physical simulation when part of the circuit wasn't modeled.

## 6. Make the component library genuinely useful

Backed by the actual catalog, per the Penpot design: full-text search over metadata; category filtering/navigation; tags/keywords/attributes; clear names and icons; descriptions/details; consistent cards and selection states; properties/details view; empty and error states; simulation readiness/capability indication; graceful handling of missing assets or invalid definitions.

Initial taxonomy suggestion: Electrical, Microcontrollers, Sensors, Displays, Communication, Audio, Power. Do not fill categories with imaginary or misleading entries.

## 7. Establish the component-authoring workflow

Support both implemented components and future ones present for design/documentation. Support package metadata, assets, interface definitions, parameter schemas, domain-specific models, docs, tests. Validate definitions; invalid packages produce actionable diagnostics. Keep domain-specific requirements separate from generic catalog requirements (a diode author provides electrical model; an MCU author provides board appearance, identity, pinout, docs without inventing an electrical model). Reuse existing component/model work. No marketplace, scripting platform, or package-management service.

## 8. Integrate with the existing electrical simulator

Existing supported components must keep constructing real circuits through the actual Simulation API and electrical backend. Map new definitions/editor representation correctly to the simulator's circuit representation. Don't couple the catalog to ngspice internals. Keep the LED example as a regression case. Fix genuine integration gaps at the right boundary and document why. Do not rewrite the electrical backend.

## 9. Persistence

Persist component identities, versions, parameter values, placement/orientation, connections, applicable simulation configuration — including components without simulation implementations. Save/reload a project with both working electrical components and unsupported future-domain components without loss. Sensible behavior for missing, invalid, or incompatible definitions; never silently substitute an unrelated component. Keep persisted format independent of transient frontend/rendering state. Document persistence and compatibility decisions.

## 10. Testing and real validation

Tests must demonstrate behavior, not existence of classes/buttons/schemas. Required scenarios:

* **Catalog discovery:** register a valid definition; discoverable under correct metadata and category.
* **Search and filtering:** search by name/alias, apply category filters, verify actual results.
* **Extensibility:** add a new valid definition with no component-specific frontend code; the app discovers and displays it.
* **Unsupported components:** place an ESP32, inspect metadata and pins, save, reload, identity/config intact.
* **Capability handling:** unsupported simulation operation is reported accurately; no false success.
* **Invalid definitions:** malformed/incomplete definitions are rejected or reported as documented.
* **Electrical regression:** real supported circuit through the real backend with numerical checks.
* **End-to-end:** real application + Simulation API; don't mock catalog, persistence, or engine in integration tests.

Mocks only for isolated infrastructure tests. Keep existing suites green. Add regression tests for defects found; use numerical reference expectations.

## 12. Scope

No MCU emulation, Wi-Fi/Bluetooth, audio simulation, marketplace, AI/MCP integration, or new simulation kernel. Accommodate them without pretending they exist. Don't polish icons while the catalog stays hardcoded; don't build an abstract registry poorly integrated into the app.

## 13. Completion criteria

* Implementable Penpot design exists and the app follows it.
* Workspace provides the designed component-discovery and circuit-editing workflows.
* Catalog is searchable/filterable with reusable presentation and category mechanisms.
* Definitions separate from domain-specific simulation implementations.
* New components register without unrelated frontend modifications.
* Unsupported future-domain components placeable and persistable without fabricated behavior.
* Existing electrical components still work through the actual simulation core.
* Meaningful behavioral and integration tests pass.
* Documentation explains how to add components and extend the system.

Do not report unimplemented capabilities as completed.
