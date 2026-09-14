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

For eligible metadata omissions, `migrate` checks original XML facts and plans only exact pool-attribute repairs under the existing schema. It does not call build, patch, metadata refresh, or routing. A write requires the reviewed input SHA, applicable missing-hash acceptance, projected and serialized strict checks, exact preservation, and atomic no-clobber delivery. `compare --migration` independently recomputes this plan and compares the full document under the serialization-signature formatting boundary; it neither accepts a baseline nor substitutes for strict validation.

### Explicit semantic context

`validate --context` and `inspect --context` can add an explicitly supplied,
read-only semantic check to a managed v3 diagram. The context binds to the
actual diagram bytes and existing semantic-model identity; it does not change
the Draw.io schema, embedded model hash, diagram, or context file. Without the
option, the existing command paths retain their normal behavior.

The context layer evaluates declared scopes and optional group relationships
through stable IDs and explicit topology. It does not infer process facts from
labels, geometry, rank, or same-lane placement, and it does not call patch,
migration, layout, routing, writers, renderers, or network services. A context
validation failure is kept separate from the underlying artifact diagnostics.

An optional provenance component adds declared sources, facts, and field
bindings. Its fact status is separate from binding validity: a confirmed record
is only a supplied declaration, while a current binding requires its exact
field and source snapshots to match. The engine does not read a source
reference, network resource, token, or private document, and it does not
authenticate a confirmer. Provenance-aware writers deliver a new diagram/context
bundle whose completion manifest is the final commit point; explicit readers
verify that manifest before accepting the two members.

### Visual review evidence

The `review prepare` and `review record` actions create separate immutable
evidence packages for an exact managed v3 artifact. Prepare snapshots native
objects and optional context without rendering. Record validates and copies an
externally supplied report, PNGs, and export logs; it does not execute a report
command, invoke a model, or modify the diagram. Strict validation, preview
export, agent image review, and human review remain independent states. PNG and
calibration checks establish bounded file integrity and declared coordinate
consistency, not image understanding or exporter/reviewer authentication.

## Trust boundaries

- The model proposes semantics; it is not trusted to optimize raw geometry directly.
- The schema rejects unknown fields instead of silently accepting typos.
- The generator is deterministic, but deterministic output is not automatically visually perfect.
- Strict validation and visual review are independent evidence.
- The latest user-saved `.drawio` is canonical after local editing.
- A patch is bound to the exact inspected input bytes through SHA-256; a later save invalidates that baseline.
- Patch can accept reviewed direct semantic edits explicitly; migration rejects known drift and accepts a new baseline only when the historical hash is absent. Neither can override malformed schema composition.
- Migration supports narrowly missing metadata in otherwise verifiable managed diagrams. Incompatible or manually created drawings require controlled rebuilding.

## Current implementation and page scope

The portable CLI is limited to file arguments, input summaries, authorization preflight checks, command dispatch and output, and exception mapping. Its adjacent private modules own domain behavior:

| Module | Responsibility |
| --- | --- |
| `clearance` | Calibrated, read-only model-perimeter arrowhead-clearance measurements for supported Draw.io styles and target shapes. |
| `contracts` | Shared constants, errors, diagnostics, and numeric serialization. |
| `geometry` | Bounds, ports, polylines, intersections, and geometric comparisons. |
| `document` | Draw.io XML readers and writers, raw routing views, native order, file receipts, and atomic output. |
| `metadata` | Managed semantic identity, model hashing, and explicit metadata refresh. |
| `migration` | Original-byte eligibility checks, exact same-schema metadata plans, conservative delivery, and independent migration comparison. |
| `context_native` | Read original managed v3 identity, ownership, endpoint, and geometry facts for an opt-in context check without repairing defaults. |
| `semantic_context` | Load the explicit context with bounded JSON parsing, bind it to the inspected artifact, coordinate read-only checks, and assemble the context receipt. |
| `pattern_rules` | Pure predicates over declared semantic scopes; it uses explicit topology rather than labels, layout, or routing. |
| `group_rules` | Pure predicates over declared v3 group relationships, including topology coverage and reference-only group kinds. |
| `provenance` | Pure source/fact/binding schema, field projections, validity assessment, declared transitions, and preservation checks. |
| `context_bundle` | Bounded context inputs plus a shared secure no-clobber publisher for immutable context and review members; E3 keeps its fixed diagram/context protocol. |
| `context_workflow` | Explicit context-aware adapters for build, patch, compare, inspect, validate, and eligible migration. |
| `preview_png` | Bounded structural validation for accepted PNG bytes; it does not identify image content. |
| `review_evidence` | Pure review-report, object-index, state, typed-reference, and calibration checks. |
| `review_workflow` | Explicit immutable prepare and record package adapters; neither renders, invokes a reviewer, nor repairs an artifact. |
| `review_preservation` | Independent XML protection projection and native before/after metrics for an authorized candidate; it rejects unsupported serialization carriers. |
| `review_repair` | Bounded construction of authorized edge-label or automatic-route candidates; it cannot authorize a target or accept a candidate. |
| `review_state` | Immutable two-attempt claim, result, and marker packages; it fails closed on an incomplete or conflicting chain. |
| `review_cycle` | Explicit plan, repair, candidate prepare/record, and assessment adapters that bind every input by raw digest; it does not render or authenticate external review. |
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

The dependency direction is CLI to build, roundtrip, migration, semantic context, explicit context workflow, or review workflow; build to construction, layout, specification validation, and the shared core; and roundtrip to patch operations, construction, layout, specification validation, validation, and the shared core. Migration reads document, metadata, validation, and shared contract facts without invoking build, patch, or routing. Semantic context reads original artifact facts and invokes pure pattern, group, and provenance rules without invoking layout, routing, or network services. The context workflow coordinates explicit bundle I/O and existing operations; it does not change their no-context paths. Review workflow consumes snapshots and pure PNG/review checks without invoking renderers, models, routing, patch, or repair. Validation, routing, and the other shared modules do not import the higher-level orchestrators. Declared-patch `compare` calls patch processing only for its forward replay; patch operations do not depend on compare or delivery gates.

Routing consumes plain node and lane views rather than XML elements. The document views retain raw geometry and semantic values so conversion, defaults, and errors occur at the existing decision points. Planners and routing context are explicit operation-local state; there is no process-wide route cache. Build plans all edges as one mutable batch. Patch plans new edges and existing edges with declared route changes while treating frozen connector paths and labels as obstacles. A spatial change that makes a frozen route invalid requires an explicit route declaration; existing manual waypoints and explicit port locks are never silently rewritten. Validation reads the latest tree and calls shared geometry, sizing, label, routing, and clearance helpers without calling the XML routing adapter or refreshing metadata. The repair cycle reuses bounded route and label planning only for explicitly authorized targets, then independently checks the complete XML preservation projection, immutable claim chain, fresh external-evidence bindings, and assessment receipt. It does not change the existing six-command defaults or turn a review report into an authorization.

These are implementation boundaries inside one complete Skill, not separately installed packages or new public APIs. The public interface includes six CLI commands and their structured JSON receipts: build, inspect, patch, validate, compare, and migrate. The original five command defaults remain unchanged; migration comparison is an explicit compare mode. Internal functions are not compatibility guarantees.

Each generated file is a single-page process view. The tool does not provide multi-page navigation, cross-page connectors, or cross-file references. Split a dense end-to-end process and its exception detail into separate `.drawio` files when one page would no longer be readable.

## Distribution

Claude Code, Codex, and Agent Skills installers all resolve the same canonical directory: `skills/product-swimlane-drawio`. Install or copy the complete directory rather than the entry script alone. Marketplace manifests provide platform metadata without duplicating implementation files.
