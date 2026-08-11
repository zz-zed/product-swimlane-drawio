# Semantic schema

Use stable ASCII IDs containing letters, digits, underscores, or hyphens. Visible labels may use any language.

## Build specification

Required top-level fields:

- `title`: visible diagram title.
- `lanes`: ordered vertical lanes.
- `nodes`: lane-owned steps.
- `edges`: directed connections.

Neutral skeleton:

```json
{
  "title": "<diagram-title>",
  "lanes": [
    {"id": "lane-a", "label": "<lane-label>", "width": 200}
  ],
  "nodes": [
    {"id": "node-a", "lane": "lane-a", "rank": 1, "type": "start", "label": ""}
  ],
  "edges": []
}
```

### Lane fields

- `id`: required stable semantic ID.
- `label`: required visible label.
- `width`: optional pixel width; default `200`.

### Node fields

- `id`: required stable semantic ID.
- `lane`: required owning lane ID.
- `rank`: required global sequence number starting at `1`.
- `type`: required; `start`, `end`, `process`, `decision`, or `note`.
- `label`: required visible label; it may be empty.
- `width`, `height`, `x`, `y`: optional geometry. Avoid fixed positions unless reproducing an approved layout.

### Edge fields

- `id`: required stable semantic ID.
- `from`, `to`: required source and target node IDs.
- `type`: optional; `flow`, `call`, `return`, `retry`, or `async`.
- `label`: optional visible label.
- `route`: optional; `auto`, `forward`, `back`, or `side`.
  - `auto`: infer from ranks and edge type.
  - `forward`: normal progress to a later rank; defaults to bottom exit and top entry.
  - `back`: return to an earlier step; defaults to side ports and an outer route.
  - `side`: same-rank or lateral interaction; defaults to facing side ports.
- `branch`: optional decision hint; `positive` or `negative`. It changes the default decision exit to right or left.
- `exit_side`, `entry_side`: optional; `top`, `bottom`, `left`, or `right`.
- `exit_offset`, `entry_offset`: optional position along the selected side from `0.05` to `0.95`. The tool allocates distinct offsets when omitted.
- `allow_port_reuse`: optional boolean; default `false`.
- `waypoints`: optional pool-local coordinates as `{ "x": number, "y": number }` objects or two-number arrays.

Use automatic routing first. Add explicit ports or waypoints only after a strict-validation warning or rendered-preview issue.

### Canvas fields

Optional `canvas` fields are `x`, `y`, `title_height`, `lane_header_height`, `row_gap`, `top_padding`, and `bottom_padding`.

## Patch specification

A patch may contain `update_nodes`, `update_edges`, `nodes`, and `edges`. Include only requested changes.

Neutral skeleton:

```json
{
  "update_nodes": [
    {"id": "<existing-node-id>", "label": "<updated-label>"}
  ],
  "update_edges": [
    {
      "id": "<existing-edge-id>",
      "reroute": true,
      "exit_side": "left",
      "entry_side": "left"
    }
  ],
  "nodes": [
    {"id": "node-new", "lane": "lane-a", "rank": 2, "type": "process", "label": "<new-label>"}
  ],
  "edges": [
    {"id": "edge-new", "from": "<existing-node-id>", "to": "node-new", "type": "flow", "label": ""}
  ]
}
```

`update_edges` behavior:

- Updating only `label` preserves the existing route and waypoints.
- Supplying `reroute: true` recomputes the route from the existing semantic fields.
- Supplying any routing field recomputes only that edge.
- Explicit port changes reserve the selected ports and fail on unintended reuse.

Ordinary patching preserves node geometry. Supplying `x`, `y`, `width`, or `height` for an existing node requires `--allow-geometry-updates`.

## Validation constraints

- Lane, node, and edge IDs must be unique within their collections.
- Every node must reference an existing lane.
- Every edge must reference existing source and target nodes.
- Every node rank must be an integer greater than or equal to `1`.
- New patch IDs must not collide with existing semantic IDs.
- Strict validation fails when routing-quality warnings remain.
