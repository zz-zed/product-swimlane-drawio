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

Use automatic routing first. It routes `main_path` edges before ordinary branches and returns, allocates endpoint ports jointly, removes duplicate and collinear points, and scores orthogonal candidates by bends, length, short segments, unrelated-lane intrusion, obstacle clearance, reciprocal separation, main-path continuity, and label capacity. A same-lane downward main-path edge prefers a bottom-to-top direct connection. Adjacent-rank cross-lane flow keeps centered endpoints unless an explicit override or a hard collision requires otherwise; it does not trade balanced `0.5 -> 0.5` ports for marginally shorter `0.1/0.9` alignment. A real split or cross-lane branch exits toward its target lane; the conventional right-side positive exit is used only when it does not create a backwards hook.

Returns and retries use a separate target-lane side slot when possible. A new build widens an automatic target lane when the slot does not fit, then recomputes later lanes and automatic routes. Existing diagrams and nodes with explicit `x` coordinates keep their geometry; if no internal gutter remains, validation diagnoses the borrowed lane. Add explicit ports or waypoints only after a structured diagnostic or visual-review issue. Explicit waypoints are never simplified or silently rewritten; strict validation still reports their routing defects.

Automatic edge labels prefer the longest clear independent horizontal segment, with a clear vertical segment as a fallback. The route planner accounts for node and connector bounds, then performs a global label reflow after all routes exist. Automatic rank spacing and decision width grow only when the default compact grid cannot provide a clear carrier or contain multilingual content, unless the specification explicitly fixes the corresponding geometry.

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

The result includes schema version, main path, phases, lane order, node geometry, edge ports, waypoints, and current validation. Connectors manually redrawn in Draw.io without Skill metadata appear under `unmanaged_edges` with their recoverable source, target, label, ports, and waypoints. Validation reports `interoperability/unmanaged-edges`; source/target topology is still considered when checking reachability and main-path continuity, but the missing stable edge ID is not silently recreated.

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

Build and patch outputs also include an atomic-delivery receipt with path, byte count, and SHA-256 digest. Patch output includes the IDs added, updated, deleted, and automatically rerouted. The QA receipt includes `main_path_bends`, `short_segments`, `label_conflicts`, `reciprocal_ambiguities`, `manual_waypoints_preserved`, and `visual_review`. When the current agent cannot inspect a rendered image, `visual_review` is `not_available`; a clean strict result does not change that status.

## Compatibility

- Specifications without `schema_version` are treated as legacy v1 inputs and remain buildable.
- Compatible v0.1.x `.drawio` files remain inspectable and patchable.
- v2-only semantic checks apply after a v2 build or after a patch explicitly supplies `main_path`.
- Manually created Draw.io files without compatible semantic metadata require migration or a controlled rebuild.
