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

The engine uses a bounded compilation pipeline, not an unbounded global solver. It allocates lane and rank space, selects among finite route candidates for each automatic edge, and performs label reflow against the compiled scene. It does not repeatedly optimize the whole diagram until a subjective visual optimum is reached, and it does not infer durable layout intent from a person's drag operations. Deterministic means that the same supported input produces the same bytes; it does not mean that every valid process is automatically presentation-perfect.

### Validator

Strict validation covers semantic consistency and diagram-quality heuristics. Diagnostics use stable codes, evidence, affected semantic IDs, and supported fixes. Warnings fail strict validation.

### Editable artifact

The output is native, uncompressed `.drawio`, not a flattened image. Draw.io Desktop or diagrams.net can move nodes, resize lanes, edit labels, and adjust connectors without a generation dependency.

### Incremental update loop

After local editing, `inspect` reads the latest file rather than relying on an older JSON source. It reports the exact input digest and whether embedded semantics are managed, recoverable, or unsafe. `patch` requires the inspected digest as a baseline, applies declared semantic changes while preserving unrelated geometry and compatible manual waypoints, and refreshes the versioned semantic-model hash. Lane operations use stable neighboring IDs, enforce dependency-safe deletion, and expose downstream shifts separately from requested changes. Patch-added v3 nodes compile `slot` and note `anchor` intent against the current saved geometry. `compare` replays the declaration and checks the exact before-and-after result.

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

The portable CLI retains compilation, routing, label strategy, validation, patch and compare orchestration. Four narrow private modules now own shared contracts, pure geometry, Draw.io document adaptation, and managed semantic metadata respectively. They are one-way implementation boundaries inside the Skill, not separately installed packages or new public APIs. The public interface remains the five CLI commands and their structured JSON receipts; internal functions are not compatibility guarantees.

Each generated file is a single-page process view. The tool does not provide multi-page navigation, cross-page connectors, or cross-file references. Split a dense end-to-end process and its exception detail into separate `.drawio` files when one page would no longer be readable.

## Distribution

Claude Code, Codex, and Agent Skills installers all resolve the same canonical directory: `skills/product-swimlane-drawio`. Install or copy the complete directory rather than the entry script alone. Marketplace manifests provide platform metadata without duplicating implementation files.
