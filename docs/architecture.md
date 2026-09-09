# Architecture

`product-swimlane-drawio` separates language-model reasoning from geometry. The agent confirms what the process means; the local engine decides how that confirmed structure becomes an editable diagram.

## Data flow

```text
Natural-language process
        ↓
Agent confirmation
        ↓
Versioned semantic JSON
        ↓
Deterministic layout and routing
        ↓
Native uncompressed .drawio
        ↓
Strict validation and optional preview review
        ↓
Local Draw.io editing
        ↓
Inspect latest file → semantic patch → compare
```

## Components

### Agent Skill

The Skill guides an agent to confirm lanes, the normal path, decisions, returns, retries, phases, and unresolved assumptions before generation. It also defines the handoff and incremental-update workflow.

### Semantic model

Schema v3 is the default for new diagrams. It represents lanes, nodes, edges, a confirmed `main_path`, behavior patterns, groups, lane-local slots, note anchors, flow roles, outcomes, layout profiles, and optional phases. Schema v2 remains supported for compatibility. Stable ASCII IDs separate machine identity from visible labels and make later inspection and patching possible.

### Deterministic engine

The standard-library Python tool validates input, calculates lane and node geometry, allocates ports, routes orthogonal connectors, places labels, emits native Draw.io XML, and returns an atomic delivery receipt. The complete Skill directory is its distribution unit: the CLI entrypoint loads its adjacent private `swimlane_core` package from any working directory, with no pip installation or repository tools required.

The engine uses a bounded compilation pipeline, not an unbounded global solver. It allocates lane and rank space, plans endpoint ports for the complete mutable edge batch, routes the main path before branches and returns, and performs label reflow against the compiled scene. A failed route feeds its rejected component assignment back to the bounded port planner; successful unrelated components remain stable. It does not repeatedly optimize the whole diagram until a subjective visual optimum is reached, and it does not infer durable layout intent from a person's drag operations. Deterministic means that the same supported input produces the same bytes; it does not mean that every valid process is automatically presentation-perfect.

### Validator

Strict validation covers semantic consistency and diagram-quality heuristics. Diagnostics use stable codes, evidence, affected semantic IDs, and supported fixes. Warnings fail strict validation.

### Editable artifact

The output is native, uncompressed `.drawio`, not a flattened image. Draw.io Desktop or diagrams.net can move nodes, resize lanes, edit labels, and adjust connectors without a generation dependency.

### Incremental update loop

After local editing, `inspect` reads the latest file rather than relying on an older JSON source. It reports the exact input digest and whether embedded semantics are managed, recoverable, or unsafe. `patch` requires the inspected digest as a baseline, applies declared semantic changes while preserving unrelated geometry and compatible manual waypoints, and refreshes the versioned semantic-model hash. Lane operations use stable neighboring IDs, enforce dependency-safe deletion, and expose downstream shifts separately from requested changes. Patch-added v3 nodes compile `slot` and note `anchor` intent against the current saved geometry.

`compare` applies a supplied patch to a copy of the before tree and compares that expected result with the supplied after file; patch processing does not call compare. The serialized delivery candidate must also pass an independent serialization-signature gate, so a replay result cannot justify a serialization change by itself. Failed CLI operations do not replace an input or existing target file. Internal patch processing does not promise a whole-object rollback after every exception. Compare checks managed payloads, preserved unknown subtrees, and sibling order within the diagram content; it does not claim to cover every outer `mxfile` attribute or wrapper detail.

### Managed artifact identity

The pool cell stores the producing tool version, model-hash version, stable lane order, and a hash of process meaning. The hash covers semantic IDs, labels, ownership, ordering, topology, main path, phases, and v3 layout intent. It excludes user-editable visual state such as coordinates, sizes, styles, lane widths, phase colors, ports, and manual waypoints. This separation detects undeclared semantic drift without treating ordinary Draw.io layout adjustments as corruption.

## Trust boundaries

- The model proposes semantics; it is not trusted to optimize raw geometry directly.
- The schema rejects unknown fields instead of silently accepting typos.
- The generator is deterministic, but deterministic output is not automatically visually perfect.
- Strict validation and visual review are independent evidence.
- The latest user-saved `.drawio` is canonical after local editing.
- A patch is bound to the exact inspected input bytes through SHA-256; a later save invalidates that baseline.
- Reviewed direct semantic edits can establish a new baseline explicitly, but malformed schema composition cannot be overridden.
- Incompatible or manually created Draw.io files require migration or controlled rebuilding before safe semantic patching.

## Current implementation and page scope

The portable CLI is limited to file arguments, input summaries, authorization preflight checks, command dispatch and output, and exception mapping. Its adjacent private modules own domain behavior:

| Module | Responsibility |
| --- | --- |
| `clearance` | Calibrated, read-only model-perimeter arrowhead-clearance measurements for supported Draw.io styles and target shapes. |
| `contracts` | Shared constants, errors, diagnostics, and numeric serialization. |
| `geometry` | Bounds, ports, polylines, intersections, and geometric comparisons. |
| `document` | Draw.io XML readers and writers, raw routing views, native order, file receipts, and atomic output. |
| `metadata` | Managed semantic identity, model hashing, and explicit metadata refresh. |
| `sizing` | Text estimates and node sizes. |
| `routing_policy` | Shared routing and validation thresholds. |
| `ports` | Port candidates, pair allocation, and per-operation allocator state. |
| `port_planner` | Whole-batch endpoint requests, conflict components, bounded paired assignments, and route-feedback replanning. |
| `labels` | Label dimensions, candidates, scoring, and placement. |
| `routing` | Route candidates, selection, scoring, and explicit routing context. |
| `routing_adapter` | Conversion between native XML and route decisions, including applying styles, points, and label geometry. |
| `spec_validation` | Build and patch input structure, fields, objects, and cross-object constraints; it does not read or write files. |
| `layout` | Canvas, lane, rank, slot, and anchor value calculations; it does not write XML or introduce new layout intent. |
| `construction` | Native lane, node, edge, and phase-cell construction, phase updates, and hierarchy rules shared by build and patch work. |
| `build` | New-diagram orchestration from a specification to a native tree, using layout, construction, routing, and metadata. |
| `patch_operations` | Controlled lane, node, edge, phase, and group changes with their incremental dependencies for one declared operation state. |
| `roundtrip` | Patch coordination, saved-edit protection, declared-patch comparison, inspection, and domain-level delivery-candidate checks. |
| `validation` | Ordered diagnostic collectors and read-only validation summaries. |

The dependency direction is CLI to build or roundtrip; build to construction, layout, specification validation, and the shared core; and roundtrip to patch operations, construction, layout, specification validation, validation, and the shared core. Validation, routing, and the other shared modules do not import the higher-level orchestrators. `compare` calls patch processing only for its forward replay; patch operations do not depend on compare or delivery gates.

Routing consumes plain node and lane views rather than XML elements. The document views retain raw geometry and semantic values so conversion, defaults, and errors occur at the existing decision points. Planners and routing context are explicit operation-local state; there is no process-wide route cache. Build plans all edges as one mutable batch. Patch plans new edges and existing edges with declared route changes while treating frozen connector paths and labels as obstacles. A spatial change that makes a frozen route invalid requires an explicit route declaration; existing manual waypoints and explicit port locks are never silently rewritten. Validation reads the latest tree and calls shared geometry, sizing, label, routing, and clearance helpers without calling the XML routing adapter or refreshing metadata.

These are implementation boundaries inside one complete Skill, not separately installed packages or new public APIs. The public interface remains the five CLI commands and their structured JSON receipts; internal functions are not compatibility guarantees.

Each generated file is a single-page process view. The tool does not provide multi-page navigation, cross-page connectors, or cross-file references. Split a dense end-to-end process and its exception detail into separate `.drawio` files when one page would no longer be readable.

## Distribution

Claude Code, Codex, and Agent Skills installers all resolve the same canonical directory: `skills/product-swimlane-drawio`. Install or copy the complete directory rather than the entry script alone. Marketplace manifests provide platform metadata without duplicating implementation files.
