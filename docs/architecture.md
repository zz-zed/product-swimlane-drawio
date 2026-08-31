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

The standard-library Python tool validates input, calculates lane and node geometry, allocates ports, routes orthogonal connectors, places labels, emits native Draw.io XML, and returns an atomic delivery receipt.

### Validator

Strict validation covers semantic consistency and diagram-quality heuristics. Diagnostics use stable codes, evidence, affected semantic IDs, and supported fixes. Warnings fail strict validation.

### Editable artifact

The output is native, uncompressed `.drawio`, not a flattened image. Draw.io Desktop or diagrams.net can move nodes, resize lanes, edit labels, and adjust connectors without a generation dependency.

### Incremental update loop

After local editing, `inspect` reads the latest file rather than relying on an older JSON source. `patch` applies declared semantic changes while preserving unrelated geometry and compatible manual waypoints. `compare` checks the before-and-after files against the declared patch.

## Trust boundaries

- The model proposes semantics; it is not trusted to optimize raw geometry directly.
- The schema rejects unknown fields instead of silently accepting typos.
- The generator is deterministic, but deterministic output is not automatically visually perfect.
- Strict validation and visual review are independent evidence.
- The latest user-saved `.drawio` is canonical after local editing.
- Incompatible or manually created Draw.io files require migration or controlled rebuilding before safe semantic patching.

## Distribution

Claude Code, Codex, and Agent Skills installers all resolve the same canonical directory: `skills/product-swimlane-drawio`. Marketplace manifests provide platform metadata without duplicating implementation files.
