# Process IR v3

## Purpose

Process IR describes what a product or business process means without asking an agent to optimize pixels. It remains independent from Draw.io geometry and from any particular layout or routing engine.

## Layers

The v3 compiler separates four concerns:

1. Process facts: participants, steps, decisions, artifacts, outcomes, and relationships.
2. Behavior pattern: the dominant interaction pattern used to choose a layout grammar.
3. Layout intent: logical ranks, lane-local slots, groups, anchors, and optional locks.
4. Compiled scene: measured geometry, ports, orthogonal routes, labels, and Draw.io XML.

The semantic and layout-intent layers are author-facing. Solver candidates and scoring remain implementation details.

## Supported behavior patterns

The first v3 contract accepts:

- `linear`
- `approval-loop`
- `request-response`
- `fork-join`
- `fan-in`
- `lifecycle`
- `custom`

A behavior pattern does not add facts or silently rewrite topology. It selects defaults and validation rules. `custom` means the authored topology and layout intent take precedence over a built-in grammar.

## Core process objects

### Lanes

A lane is one accountable system, role, or team. Lane order is authored. Width is a request, not a promise, when automatic layout needs more capacity.

### Nodes

Nodes keep the existing stable ID, lane, rank, type, and label fields. v3 adds:

- `slot`: `left`, `main`, or `right` inside one lane and rank band.
- `anchor`: an optional relative relationship used by notes and supporting artifacts.

`slot` is logical. It must never be interpreted as a fixed pixel coordinate.

### Groups

A group names a bounded set of nodes that should be reasoned about together. Initial kinds are:

- `parallel`
- `branch`
- `merge`
- `exception`
- `support`

Groups are semantic layout regions, not necessarily visible rectangles. A renderer may show a group only when its label or boundary adds useful information.

### Edges

Edges keep their existing type, route, branch, label, and optional geometry controls. v3 adds:

- `flow_role`: `main`, `branch`, `fork`, `join`, `return`, `retry`, or `response`.
- `outcome`: a stable outcome identifier that may be shared by several edges.

Shared `outcome` values support one decision result triggering more than one action without pretending that every outgoing edge represents a different outcome.

A binary decision may still use one `positive` and one `negative` edge without outcome IDs. A decision with three or more outgoing edges gives every edge an outcome ID and exposes at least two distinct outcomes. Multiple edges may share one outcome when that result triggers several actions, and more than one outcome may share the same `branch` direction hint.

## Invariants

- The confirmed `main_path` remains explicit.
- A node has exactly one owner lane.
- Nodes sharing a lane and rank require distinct slots unless they are explicitly locked with non-overlapping geometry.
- A note anchor references an existing node in the same lane for the initial compiler slice.
- An end node remains unlabeled and fixed-aspect.
- Unknown fields fail validation.
- Process IR never infers missing business steps, owners, or outcomes.

## Compatibility

- v1 remains the legacy build contract.
- v2 remains stable and must compile byte-for-byte as before.
- v3 adds author-facing layout intent while compiling to the same native, editable Draw.io cell model.
