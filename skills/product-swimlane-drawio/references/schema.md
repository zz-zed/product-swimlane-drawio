# Semantic schema and patch contract

Use stable ASCII IDs containing letters, digits, underscores, or hyphens. Visible labels may use any language. The executable v2 contract is [schema.json](schema.json).

## Contents

- [Build specification](#build-specification)
- [Main path and phases](#main-path-and-phases)
- [Nodes and edges](#nodes-and-edges)
- [Patch specification](#patch-specification)
- [Inspection and diagnostics](#inspection-and-diagnostics)
- [Compatibility](#compatibility)

## Build specification

New diagrams use `schema_version: "2"`. Unknown fields are rejected at every level.

```json
{
  "schema_version": "2",
  "title": "<diagram-title>",
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

Required v2 fields are `schema_version`, `title`, `lanes`, `nodes`, `edges`, and `main_path`. `phases` and `canvas` are optional.

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

Use automatic routing first. It removes duplicate and collinear points, prefers a safe single elbow for a forward side-exit/top-entry connection, and keeps a back route inside the target lane gutter. A new build widens an automatic target lane when needed. Existing diagrams and nodes with explicit `x` coordinates keep their geometry; if no internal gutter remains, validation diagnoses the borrowed lane. Add explicit ports or waypoints only after a structured diagnostic or visual-review issue. Explicit waypoints are never simplified automatically.

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
- Updating only an edge label preserves its existing route and waypoints.
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

The result includes schema version, main path, phases, lane order, node geometry, edge ports, waypoints, and current validation.

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

Strict validation fails when warnings remain. Build and patch outputs also include an atomic-delivery receipt with path, byte count, and SHA-256 digest. Patch output includes the IDs added, updated, deleted, and automatically rerouted.

## Compatibility

- Specifications without `schema_version` are treated as legacy v1 inputs and remain buildable.
- Compatible v0.1.x `.drawio` files remain inspectable and patchable.
- v2-only semantic checks apply after a v2 build or after a patch explicitly supplies `main_path`.
- Manually created Draw.io files without compatible semantic metadata require migration or a controlled rebuild.
