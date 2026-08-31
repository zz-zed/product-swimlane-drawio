# Semantic schema and patch contract

Use stable ASCII IDs containing letters, digits, underscores, or hyphens. Visible labels may use any language. The build-spec v2/v3 field contract is [schema.json](schema.json) and is checked against the runtime field sets in the test suite. Legacy v1 compatibility is implemented by the runtime and is intentionally outside that JSON Schema.

## Contents

- [Build specification](#build-specification)
- [Main path and phases](#main-path-and-phases)
- [Nodes and edges](#nodes-and-edges)
- [Patch specification](#patch-specification)
- [Inspection and diagnostics](#inspection-and-diagnostics)
- [Artifact integrity](#artifact-integrity)
- [Compatibility](#compatibility)

## Build specification

New diagrams use `schema_version: "3"`. Version 2 remains supported for compatibility. Unknown fields are rejected at every level.

```json
{
  "schema_version": "3",
  "title": "<diagram-title>",
  "behavior_pattern": "linear",
  "lanes": [
    {"id": "lane-a", "label": "<lane-label>", "width": 200}
  ],
  "nodes": [
    {"id": "node-start", "lane": "lane-a", "rank": 1, "type": "start", "label": ""},
    {"id": "node-end", "lane": "lane-a", "rank": 2, "type": "end", "label": ""}
  ],
  "edges": [
    {"id": "edge-a", "from": "node-start", "to": "node-end"}
  ],
  "main_path": ["node-start", "node-end"],
  "phases": [
    {"id": "phase-a", "label": "<phase-label>", "from_rank": 1, "to_rank": 2}
  ]
}
```

Required v3 fields are `schema_version`, `title`, `behavior_pattern`, `lanes`, `nodes`, `edges`, and `main_path`. `groups`, `layout`, `phases`, and `canvas` are optional.

`behavior_pattern` records the process topology before geometry is compiled. Supported values are `linear`, `approval-loop`, `request-response`, `fork-join`, `fan-in`, `lifecycle`, and `custom`. Use `custom` only when no more specific pattern fits.

Version 3 adds layout intent without requiring coordinates:

- `slot` places a node in the `left`, `main`, or `right` position for its lane and rank. The compiler expands the lane when the occupied slots do not fit.
- A `note` may use `anchor: {"node": "<id>", "side": "left|right"}`. The note must share its anchor's lane and rank; its slot is inferred from the anchor side when omitted.
- `groups` explicitly record `parallel`, `branch`, `merge`, `exception`, or `support` structures. Every group belongs to one lane and a node may belong to at most one group.
- `layout.profile` may be `compact`, `review`, or `long-form`; the default is `review`. Profiles select progressively larger automatic rank gaps, slot gaps, and lane padding. Explicit `canvas.row_gap` remains authoritative.
- `layout.phase_presentation` may be `bands` or `rail`. `bands` keeps translucent phase backgrounds across all lanes. `rail` reserves a narrow labeled phase column to the left of the lanes and keeps lane bodies opaque; use it for long lifecycle diagrams where phase names are navigation rather than background emphasis.
- Edges may add `flow_role` (`main`, `branch`, `fork`, `join`, `return`, `retry`, `exception`, or `response`) and a stable `outcome` ID.

For a binary decision, distinct `positive` and `negative` branches remain sufficient. A v3 decision with three or more outgoing edges must give every edge a non-empty `outcome` and expose at least two distinct outcome IDs. Several edges may intentionally share one outcome when a single result triggers multiple actions; `branch` remains a directional hint and may repeat across non-primary outcomes.

Keep process facts, behavior patterns, and layout intent separate. Use coordinates only for an approved manual composition or a geometry-preserving update.

Lane fields:

- `id`: required semantic ID.
- `label`: required visible label.
- `width`: optional requested width; minimum `120`, default `200`. For a new automatic layout, the tool may increase it when a back or retry target needs a safe internal side gutter. Nodes without explicit `x` coordinates are re-centered and later lanes shift with the expanded geometry.

Optional `canvas` fields are `x`, `y`, `title_height`, `lane_header_height`, `row_gap`, `top_padding`, and `bottom_padding`.

## Main path and phases

`main_path` records the user-confirmed normal path. It must:

- Contain at least two distinct node IDs.
- Begin with a `start` node and end with an `end` node.
- Reference existing nodes.
- Have an edge for every consecutive node pair.
- Progress through non-decreasing global ranks.

Do not put returns, retries, or exception-only nodes in `main_path`.

A phase is an optional horizontal band across every vertical lane:

- `id`: required semantic ID.
- `label`: required visible label.
- `from_rank`, `to_rank`: inclusive rank range; `to_rank` must not exceed the maximum node rank.
- `fill_color`: optional `#RRGGBB` value.

When at least one phase exists, the generator and patcher enforce semantic Z-order as phase backgrounds, lanes, nodes, then connectors. Lane bodies use a transparent `swimlaneFillColor` so the bands remain visible, while phase cells use `connectable=0` and `pointerEvents=0` so they cannot intercept node selection. Without phases, lane bodies retain their opaque white fill. Strict validation reports `layout/phase-z-order`, `layout/phase-lane-visibility`, or `layout/phase-interactive` when a saved Draw.io file violates these editability rules.

## Nodes and edges

Node fields:

- `id`, `lane`, `rank`, `type`, and `label` are required.
- `rank` is a global integer starting at `1`; equal ranks represent parallel steps.
- `type` is `start`, `end`, `process`, `decision`, or `note`.
- `width`, `height`, `x`, and `y` are optional geometry. Avoid fixed positions for new diagrams unless reproducing an approved layout.

Node geometry rules:

- `start` and `end` are fixed-aspect circles. If only `width` or `height` is supplied, the tool uses that value for both dimensions. If both are supplied, they must be equal. A labeled `start` is at least `48 x 48`.
- `end` is an unlabeled solid termination point. Its `label` must be an empty string; use a preceding process node when visible completion text is required.
- A `process` without an explicit `height` grows from the `42` pixel default according to estimated wrapped text lines, up to `66` pixels. Longer text remains subject to the overflow diagnostic. An explicit height is preserved, but strict validation warns when it creates substantially more vertical padding than the label requires.

Edge fields:

- `id`, `from`, and `to` are required.
- `type`: optional `flow`, `call`, `return`, `retry`, or `async`.
- `label`: optional visible label.
- `route`: optional `auto`, `forward`, `back`, or `side`.
- `branch`: optional `positive` or `negative` decision outcome.
- `exit_side`, `entry_side`: optional `top`, `bottom`, `left`, or `right`.
- `exit_offset`, `entry_offset`: optional value from `0.05` to `0.95`.
- `allow_port_reuse`: optional boolean; default `false`.
- `waypoints`: optional pool-local `{ "x": number, "y": number }` objects or two-number arrays.

Use automatic routing first. It routes `main_path` edges before ordinary branches and returns, allocates endpoint ports jointly, removes duplicate and collinear points, and scores orthogonal candidates by bends, length, short segments, unrelated-lane intrusion, obstacle clearance, reciprocal separation, main-path continuity, and label capacity. A downward main-path edge prefers a bottom-to-top connection even when it crosses into another lane. For every selected side pair, automatic routing prefers centered `0.5 -> 0.5` attachment points and moves to secondary offsets only for a real port conflict; it does not trade balanced endpoints for marginally shorter `0.1/0.9` alignment. A true exception branch exits toward its target lane. A same-lane outcome that terminates directly below a decision exits from the decision bottom instead of creating a side hook.

Assign the same rank to a decision and a cross-lane side outcome when the intended reading is a horizontal handoff; v3 aligns their centers. For a directly following same-lane terminal, keep the terminal on the lane's main axis so the automatic route is a straight bottom-to-top connection.

Returns and retries use a separate target-lane outer-side slot when possible. An adjacent-lane retry leaves toward the target but enters through the target's outer side, keeping the normal facing request/response corridor clear. The outer source side and bottom corridor remain available for longer returns that must avoid intervening lanes. A new build widens an automatic target lane when the slot does not fit, then recomputes later lanes and automatic routes. Existing diagrams and nodes with explicit `x` coordinates keep their geometry; if no internal gutter remains, validation diagnoses the borrowed lane. Add explicit ports or waypoints only after a structured diagnostic or visual-review issue. Explicit waypoints are never simplified or silently rewritten; strict validation still reports their routing defects.

Automatic edge labels prefer the longest clear independent horizontal segment, with a clear vertical segment as a fallback. The route planner accounts for node and connector bounds, then performs a global label reflow after all routes exist. Automatic rank spacing and decision width grow only when the default compact grid cannot provide a clear carrier or contain multilingual content, unless the specification explicitly fixes the corresponding geometry.

For an automatic `back`, `retry`, or `return` route, source proximity takes precedence over raw segment length: the label uses the nearest clear carrier to the source action and avoids distant outer-canvas detours. Collision and container checks still apply. Explicit waypoints remain unchanged.

## Patch specification

A patch may contain:

- `update_nodes`, `update_edges`, `update_phases`.
- New `nodes`, `edges`, or `phases`.
- `delete_nodes`, `delete_edges`, `delete_phases` as arrays of semantic IDs.
- `main_path` to replace the confirmed normal path.

```json
{
  "update_nodes": [
    {"id": "node-a", "label": "<updated-label>"}
  ],
  "update_edges": [
    {"id": "edge-a", "reroute": true}
  ],
  "delete_edges": ["edge-b"]
}
```

Patch rules:

- Include only requested changes.
- Updating an edge label recomputes automatic label placement and may choose another automatic route. Explicit waypoints remain byte-for-byte equivalent in geometry.
- `reroute: true` or any routing field recomputes that edge.
- Existing node geometry requires `--allow-geometry-updates`.
- Moving or resizing a node automatically reroutes only incident edges whose routes become invalid.
- Valid manual waypoints and unrelated geometry remain unchanged.
- Deleting a node requires explicitly listing every incident edge in `delete_edges`.
- Deleting a node on `main_path` requires a replacement `main_path` in the same patch.
- Write to a new output path. Use `--force` only when replacing a reviewed output intentionally.

## Inspection and diagnostics

Inspect a compatible file before patching:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" inspect --input "<current.drawio>"
```

The result includes the input path, byte count, SHA-256 digest, schema version, main path, phases, lane order, node geometry, edge ports, waypoints, artifact-integrity state, and current validation. Connectors manually redrawn in Draw.io without Skill metadata appear under `unmanaged_edges` with their recoverable source, target, label, ports, and waypoints. Validation reports `interoperability/unmanaged-edges`; source/target topology is still considered when checking reachability and main-path continuity, but the missing stable edge ID is not silently recreated.

Validation keeps the legacy `errors` and `warnings` arrays and also returns structured diagnostics:

```json
{
  "code": "routing/non-orthogonal",
  "severity": "warning",
  "message": "<message>",
  "subject": {"kind": "edge", "id": "edge-a"},
  "evidence": {},
  "supported_fixes": ["reroute-edge"]
}
```

Strict validation fails when warnings remain. Routing diagnostics include short internal segments, unnecessary bends, hairpins, near-parallel crowding, reciprocal ambiguity, lane-boundary and node conflicts, and same-lane main-path zigzags. Text diagnostics include missing clear edge-label carriers and label overlap with nodes, connectors, or other labels. Layout diagnostics treat phase bands above editable content, opaque phase-bearing lanes, and interactive phase cells as hard errors.

Build and patch outputs also include an atomic-delivery receipt with path, byte count, and SHA-256 digest. Standard delivery uses `--strict`; if warnings remain, the command exits without writing the requested output. Successful receipts expose `strict_mode` and `quality_gate_passed`. Patch output includes the IDs added, updated, deleted, and automatically rerouted, together with the inspected input digest and integrity state. The QA receipt includes `main_path_bends`, `short_segments`, `label_conflicts`, `reciprocal_ambiguities`, `manual_waypoints_preserved`, `manual_waypoints_checked`, and `visual_review`. Waypoint preservation is measured only by patch against pre-existing explicit waypoint sets; it is `null` when no explicit waypoint was applicable. When the current agent cannot inspect a rendered image, `visual_review` is `not_available`; a clean strict result does not change that status.

## Artifact integrity

Generated files carry a versioned semantic-model hash. The hash covers the stable process meaning required for safe patching:

- Schema version, title, lane order, lane IDs, and lane labels.
- Node IDs, owning lanes, ranks, types, labels, slots, and semantic anchors.
- Edge IDs, endpoints, types, labels, route classes, branches, flow roles, and outcomes.
- Confirmed main path, phase IDs/labels/rank ranges, v3 behavior pattern, layout profile, phase presentation, and semantic groups.

The hash intentionally excludes visual state that Draw.io users may edit locally: coordinates, dimensions, styles, lane widths, phase colors, ports, port offsets, and manual waypoints. These remain protected by geometry-aware patching and `compare`, not by the semantic hash.

`inspect` reports `has_semantic_metadata`, `managed_state`, `tool_version`, `model_hash_version`, `stored_model_hash`, `computed_model_hash`, and `model_hash_matches`. Use `managed_state` instead of the coarse legacy `compatible` alias:

- `managed`: semantic metadata is structurally valid and the stored model hash matches the current semantic content.
- `recoverable`: semantic metadata can be read, but the file predates model hashing or contains unmanaged Draw.io content that requires review. A missing hash can be upgraded by a reviewed patch; unmanaged content must not be silently discarded.
- `unsafe`: semantic content differs from the stored hash, or the embedded schema composition is invalid. Do not patch until the discrepancy has been reviewed.

Use the exact `input.sha256` returned by `inspect` as the patch baseline:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" patch \
  --input "<current.drawio>" \
  --expected-input-sha256 "<sha256-from-inspect>" \
  --changes "<changes.json>" \
  --output "<updated.drawio>" \
  --strict
```

If a user intentionally changed semantic labels or relationships directly in Draw.io, review those changes and represent them in the patch, then add `--accept-model-drift` to re-establish the managed baseline. This override does not bypass malformed schema composition, changed input bytes, or other integrity errors. Never use it merely to make a failing patch proceed.

## Compatibility

- Specifications without `schema_version` are treated as legacy v1 inputs and remain buildable.
- Version 2 specifications and generated files remain buildable, inspectable, patchable, and subject to the same structured quality checks as before.
- Version 3 is the default for new diagrams and adds behavior patterns, slots, note anchors, groups, flow roles, and layout profiles.
- Compatible v0.1.x `.drawio` files remain inspectable and can be upgraded to current integrity metadata by a reviewed patch.
- Structured semantic checks apply after a v2 or v3 build, or after a patch explicitly supplies `main_path`.
- Manually created Draw.io files without compatible semantic metadata require migration or a controlled rebuild.
