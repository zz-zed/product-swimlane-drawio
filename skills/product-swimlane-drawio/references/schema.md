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
- [Conservative metadata migration](#conservative-metadata-migration)

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

When at least one phase exists, the generator and patcher enforce semantic Z-order as phase backgrounds, lanes, nodes, then connectors. Z-order is evaluated among siblings sharing a parent; Draw.io may serialize lane descendants before the next lane without changing paint order. Lane bodies use a transparent `swimlaneFillColor` so the bands remain visible, while phase cells use `connectable=0` and `pointerEvents=0` so they cannot intercept node selection. Without phases, lane bodies retain their opaque white fill. Strict validation reports `layout/phase-z-order`, `layout/phase-lane-visibility`, or `layout/phase-interactive` when a saved Draw.io file violates these editability rules.

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

Use automatic routing first. It plans endpoint ports for the complete mutable edge batch, routes `main_path` edges before ordinary branches and returns, removes duplicate and collinear points, and scores orthogonal candidates by bends, length, short segments, unrelated-lane intrusion, obstacle clearance, reciprocal separation, main-path continuity, label capacity, and supported arrowhead terminal-run clearance. A failed route rejects that conflict component's paired assignment and tries the next bounded assignment without disturbing successful unrelated components. A downward main-path edge prefers a bottom-to-top connection even when it crosses into another lane. For every selected side pair, automatic routing prefers centered `0.5 -> 0.5` attachment points when they yield a valid route and moves to secondary offsets for an actual conflict, failed route, main-path continuity, or explicit override; it does not trade balanced endpoints for marginally shorter alignment alone. A true exception branch exits toward its target lane. In v3, a same-lane downward decision branch whose bottom is not reserved by the main path exits from the decision bottom instead of creating a side hook.

Assign the same rank to a decision and a cross-lane side outcome when the intended reading is a horizontal handoff; v3 aligns their centers. For a directly following same-lane terminal, keep the terminal on the lane's main axis so the automatic route is a straight bottom-to-top connection.

Returns and retries keep a separate, collision-free corridor. Same-lane returns retain the internal target-lane gutter rule. Cross-lane returns may use a clear direct elbow or an outer source-side corridor; they do not need an extra vertical leg inside the target lane. Before trying opposite source/target sides that can force a large loop, the finite side search tries an outward same-side pair. In v3, a same-lane downward decision branch to a process prefers bottom-to-top when the bottom is not reserved for another main continuation; explicit ports remain authoritative. Existing build-time lane expansion is unchanged, and saved geometry is never moved to shorten a route. Add explicit ports or waypoints only after a structured diagnostic or visual-review issue. Explicit waypoints are never simplified or silently rewritten; strict validation still reports their routing defects.

Automatic edge labels prefer the longest clear independent horizontal segment, with a clear vertical segment as a fallback. The route planner accounts for node and connector bounds, then performs a global label reflow after all routes exist. Automatic rank spacing and decision width grow only when the default compact grid cannot provide a clear carrier or contain multilingual content, unless the specification explicitly fixes the corresponding geometry.

For an automatic `back`, `retry`, or `return` route, source proximity takes precedence over raw segment length: the label uses the nearest clear carrier to the source action and avoids distant outer-canvas detours. Collision and container checks still apply. Explicit waypoints remain unchanged.

## Patch specification

The calibrated arrowhead rule currently covers the Draw.io 31.3.2 default filled block arrow (`endSize=6`, edge stroke width 1) on unscaled, orthogonal, non-rounded routes entering supported process, decision, or end shapes. The terminal run is measured from the last effective turn to the target's model perimeter and must be at least 16 px, subject to the shared geometry tolerance. Unsupported styles or shapes report `not_available`; they do not become an inferred pass. Automatic candidates that measurably fail are rejected. Explicit waypoint paths are never moved to satisfy the rule and remain subject to a strict diagnostic.

A patch may contain:

- `update_lanes`, `update_nodes`, `update_edges`, `update_phases`, `update_groups`.
- New `lanes`, `nodes`, `edges`, `phases`, or `groups`.
- `delete_lanes`, `delete_nodes`, `delete_edges`, `delete_phases`, `delete_groups` as arrays of semantic IDs.
- `main_path` to replace the confirmed normal path.

```json
{
  "lanes": [
    {"id": "lane-b", "label": "<new-lane>", "width": 180, "after": "lane-a"}
  ],
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
- A new lane must name exactly one existing `before` or `after` lane. Numeric indexes are not a patch interface. A lane update may change `label` or `width`; existing lane-local node geometry remains authoritative.
- Deleting a lane requires explicitly deleting every owned node. The same patch must reconcile incident edges, any affected `main_path`, and groups; otherwise it fails with a dependency diagnostic. At least one lane must remain.
- Group additions, updates, and deletions are supported for v3 dependency reconciliation. Group membership remains mirrored on member nodes and is revalidated after the patch.
- A patch-added v3 node consumes `slot` and `anchor` intent. It must not occupy an existing lane/rank/slot. Right-side placement may widen the lane and shift later lanes; left-side placement that would require moving existing user geometry is rejected unless explicit geometry is supplied.
- Updating only an edge label preserves saved ports, waypoint contents/order and route-origin fields. The saved native label position is tried first; any necessary label move stays on that path. Empty or unchanged text does not trigger repositioning. `automatic` origin does not establish that a user never edited the saved route.
- `reroute: true` or an effective routing-field change authorizes recomputing that edge. `reroute: false` and identical values do not. Existing explicit waypoints survive `reroute: true`; only a supplied `waypoints` array replaces them, including an explicit empty array. Repeated and collinear explicit points are preserved.
- Changing a node type requires every surviving incident edge to be explicitly rerouted in the same patch.
- Existing node geometry requires `--allow-geometry-updates`.
- A node or lane operation that invalidates a saved route requires an explicit route update for that edge; otherwise `patch/route-update-required` refuses the candidate. A still-valid saved path is preserved.
- Valid manual waypoints and unrelated geometry remain unchanged. A lane change that moves an endpoint lane reports affected explicit-waypoint edges for visual review without rewriting them.
- Unknown cells anchor their relative sibling drawing order during phase normalization. Managed cells may be sorted between those anchors, not across them; a conflict with safe phase layering is rejected without output. Unknown content still requires review and is not silently promoted to managed content.
- Deleting a node requires explicitly listing every incident edge in `delete_edges`.
- Deleting a node on `main_path` requires a replacement `main_path` in the same patch.
- Write to a new output path. Use `--force` only when replacing a reviewed output intentionally.

## Inspection and diagnostics

Inspect a compatible file before patching:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" inspect --input "<current.drawio>"
```

The result includes the input path, byte count, SHA-256 digest, schema version, main path, phases, lane order, node geometry, edge ports, waypoints, each edge's `label_geometry`, artifact-integrity state, and current validation. Its `arrowhead_clearance` summary identifies the rule version, coverage status, checked/unavailable/not-applicable edges, and measurable violations. `partial` or `not_available` is incomplete evidence, not success. Connectors manually redrawn in Draw.io without Skill metadata appear under `unmanaged_edges` with their recoverable source, target, label, ports, and waypoints. Validation reports `interoperability/unmanaged-edges`; source/target topology is still considered when checking reachability and main-path continuity, but the missing stable edge ID is not silently recreated.

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

Automatic build/reroute candidates now pass shared geometric checks and the
existing native measurement profile before scoring. A finite coordinator can
retry automatic sides, offsets, paths and label positions while preserving
explicit choices and frozen geometry. The limits are 6 repairs per original
port component, 64 batch replays, 128 distinct paths per port pair, 8192 path
evaluations overall, 32 trials per label pair and 128 label-pair trials overall.
The default native label position is eligible only when explicitly measured
and checked alongside the finite carrier candidates; a failed placement never
silently falls back to a default offset.

Failures may include optional `evidence.planning` with stage, reason, blockers,
counts and budgets. `routing/no-safe-route` with `native_profile_unavailable`
retains the native reason; `candidate_space_exhausted` means the declared finite
domain was exhausted. `routing/route-search-budget` identifies a search limit;
port-domain failures retain their port diagnostic. These early failures also
apply to non-strict automatic generation. Saved-file commands and diagnostic
severity conventions remain, but v2/v3 saved automatic cross-lane returns no
longer require a vertical leg inside the target lane and can therefore have
different warnings or strict results. These changes do not extend the
supported native profile. No unsuccessful batch is written, and successful
top-level receipts retain their existing shape.

Build and patch outputs also include an atomic-delivery receipt with path, byte count, and SHA-256 digest. Standard delivery uses `--strict`; if warnings remain, the command exits without writing the requested output. Successful receipts expose `strict_mode` and `quality_gate_passed`. Patch output includes requested lane/node/edge changes, lane order, dependent lane shifts, automatic reroutes, affected explicit-waypoint edges, and the inspected input integrity evidence. The QA receipt includes `main_path_bends`, `short_segments`, `label_conflicts`, `reciprocal_ambiguities`, `arrowhead_clearance`, `manual_waypoints_preserved`, `manual_waypoints_checked`, and `visual_review`. Waypoint preservation is measured only by patch against pre-existing explicit waypoint sets; it is `null` when no explicit waypoint was applicable. Arrowhead coverage is a calibrated deterministic check for supported rendering profiles; `partial` and `not_available` must remain explicit. In this runtime, raw `visual_review` is always `not_available`; a clean strict result, preview export, later Agent image inspection, or human review does not change that raw field. Report those later review layers separately.

### Native edge labels and patch receipts

`label_geometry` reads native `mxGeometry` relative x/y, offset, text, style and
reconstructed editor path. Generation-time `data-label-*` caches are never
current-position evidence. A measurement is `available`, `not_available`, or
`not_applicable` (no visible label); available bounds have `bounds_quality:
estimated`. The aggregate coverage is `complete`, `partial`, `not_available`,
or `not_applicable`, with checked, unavailable and not-applicable edge counts
and IDs. Complete coverage means supported geometry, not pixel-perfect text.

The supported profile covers plain ASCII and basic/extension-A CJK text in default Helvetica,
8–24 px fonts, explicit LF/CRLF line breaks, default orthogonal connectors with the
default block marker or no marker, straight facing terminals and reconstructible
saved hint paths. Default rectangular terminals and centered decision/circle
ports are supported. Rich HTML, wrapping constraints, rotation, named styles,
other scripts, Arial/custom fonts, the html=0 renderer, off-center
curved/diamond perimeters and unmodeled router cases
are unavailable. `text/edge-label-geometry-unavailable` is a strict-failing
warning. Spatial planning refuses an unmeasurable frozen label; it cannot be
silently omitted from obstacles. Inspect and validate are read-only.

Patch receipts separate actual `label_updated_edges`, `label_repositioned_edges`
and `rerouted_edges`. `saved_routes` reports the frozen existing-edge count,
preservation result (null when none apply), and changed IDs. The older
`manual_waypoints_checked` / `manual_waypoints_preserved` fields retain their
explicit-waypoint scope. A node/lane move can alter absolute endpoint positions
without rewriting saved ports or points; dependency shifts remain separately
reported.

## Artifact integrity

`compare` also checks sibling drawing order and complete unmanaged-cell
subtrees. When present, `changed_sibling_order` / `unexpected_sibling_order`
list raw parent IDs and before/after raw cell-ID sequences of shared siblings;
`unexpected_unmanaged_cells` lists raw cell IDs with `added`, `missing`, or
`changed`. Empty optional fields are omitted. Unexpected evidence makes
`preserved=false` and CLI exit 1. A declared patch is replayed to distinguish
supported additions/deletions from unexplained ordering changes. Reordering
XML entries across different parents alone is not a drawing-order change.

Managed cell content is also compared as ordered native payload, including
repeated geometry, extension subtrees, attributes, mixed text/tails and inherited
`xml:space`. Optional `changed_cell_content` / `unexpected_cell_content` entries
identify semantic ID, kind, native path and change. Unexpected content or an
independent saved-edge preservation violation makes `preserved=false`.
Formatting whitespace between known structures may be normalized; opaque text
is retained. ElementTree comments/processing instructions and arbitrary-document
byte-level losslessness are outside this contract.

Build and patch serialize a working copy to a candidate in the target directory,
parse and check the actual candidate before atomic replacement. Patch also runs
the independent preservation guard and same-version compare. Any failure keeps
the input and existing output intact.

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

The five commands have different safety boundaries. Being inspectable does not make an input safe to patch.

| Input or condition | build (spec input) | inspect / strict validate (`.drawio` input) | patch (`.drawio` input) | compare (`.drawio` pair) |
|---|---|---|---|---|
| Legacy v1 spec without `schema_version` | Builds with the legacy interpretation | Its resulting compatible file is inspectable and validated under existing rules | Uses the existing compatible-file path | Compares declared changes and preservation evidence |
| v2 or v3 spec / resulting compatible managed file | Builds with the existing version rules | Reports state and applies normal strict rules | Permitted only with the inspected SHA-256 and existing authorization gates | Checks differences; it is not a patch-safety classifier |
| Missing model hash on an otherwise compatible old file | — | Inspect reports `recoverable`; strict retains the existing failure | A reviewed patch can upgrade the hash | Reports actual differences |
| Model-hash drift | — | Inspect reports `unsafe`; strict fails | Requires an equivalent reviewed patch and explicit `--accept-model-drift`; that flag cannot bypass other integrity failures | An identical pair may show no difference; that does not make the input safe |
| Unknown schema/hash version | — | Reports the unsupported/integrity condition and strict rejects it | Rejected; do not guess or auto-upgrade | Can compare bytes/evidence but does not approve a patch |
| Unknown vertex or manually redrawn edge without semantic ID | — | Preserves and reports the existing interoperability/unknown-content diagnostic; strict must not be treated as an automatic pass | Review and preserve deliberately; do not silently adopt a stable ID | Reports unmanaged-content and sibling-order differences |
| Ordinary nonmanaged Draw.io file | — | Inspect exits 2; strict validate exits 1 | Patch exits 2 | Can show `preserved: true` for an identical pair, but this proves only no observed difference |
| Supported old input → current patch → same-version compare | — | Inspect and strict validate use the normal input checks | Standard edit workflow, subject to all existing authorization and integrity gates | Valid declared changes can pass; real tampering must still fail |
| Current compare reviewing a patch already completed by 0.5.1 | — | Inspectability and strict validity do not prove patch preservation | A read-only review does not authorize another patch | 0.6.0 may reject the old result solely because its producing-tool stamp differs |

`compare --changes` replays the declared patch with the running tool version. When 0.6.0 reviews an `after` produced by 0.5.1, the replay may differ only in `data-tool-version`, producing `unexpected_attributes: ["pool:main"]`, `preserved: false`, and exit 1. This is an existing cross-version limitation, not proof of damaged geometry or semantics, and not a passing comparison. Use the same version for a new patch and its comparison; editing a supported old input with the current version is a different workflow from reviewing an old completed result.

Do not ignore pool-level differences: the pool also holds protected semantic and other attributes. Inspect the actual evidence for other changes. Never rewrite the user's or historical `after` stamp to make comparison pass, and never automatically rebuild or patch during a read-only review. If needed, use a trusted original producing version in isolation for read-only verification, or obtain authorization to apply the intended patch from the correct input using the current version. Preserve the original files and audit records in either case.

Version 3 is the default for new diagrams and adds behavior patterns, slots, note anchors, groups, flow roles, and layout profiles. Structured semantic checks apply after a v2 or v3 build, or after a patch explicitly supplies `main_path`. Manually created files without compatible semantic metadata still require a controlled rebuild; migration cannot silently adopt them.

## Conservative metadata migration

This conservative same-schema metadata-repair contract adds `migrate` and `compare --migration`, without changing the established default behavior of `build`, `inspect`, `patch`, `validate`, or `compare --changes`.

The supported input is one uncompressed `mxfile` / `diagram` / `mxGraphModel` / `root` with one managed pool. Migration preflights original XML and rejects forms that cannot be protected, including multiple pages, compressed or unsupported wrappers, comments, processing instructions other than the XML declaration, DTD/custom entities, and unsupported explicit namespace payload. It does not change the legacy parser. It retains normal meaning for built-in escapes, numeric entities, and `xml:space`.

Raw facts—not reader defaults—must establish identity, owner, type, rank, endpoints, route, and phase ranges. A v1 file may omit schema/main path only under its established interpretation; v2/v3 require their original schema/main-path facts. Missing newer-schema-only semantics, duplicate native or same-kind semantic IDs, raw/semantic endpoint or lane-parent contradictions, invalid existing lane order, empty metadata, an unsupported rule/version, and an existing nonmatching hash are unsafe. A normal nonmanaged drawing, an unknown drawing identity requiring adoption, or unprotectable/unsupported drawing content is unsupported. If both apply, report unsafe precedence while retaining both reasons.

Run a dry plan before asking for any write:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" migrate --input "<old.drawio>" --dry-run
```

Dry-run never serializes XML or creates a candidate/output directory. It returns input path/bytes/SHA-256, legacy source state, a same-schema semantic summary, planned changes, source/projected/serialized validation slots, preservation/delivery evidence where evaluated, and these classifications:

| Classification | Eligibility rule | Write effect |
|---|---|---|
| `not-needed` | No repair is needed, even if a producing stamp is old or absent. | No copy; `written: false`. This does not certify strict quality. |
| `automatic` | A valid stored hash matches and only derived lane order and/or hash version is absent. | Eligible after all projected strict checks. |
| `confirmation-required` | Historical hash is absent while all other required raw facts are valid. | Requires explicit acceptance of the current semantics for this command only. |
| `unsafe` | Structural/raw identity, schema/rule/hash, core-fact, or existing-metadata contradiction. | Refuse; acceptance cannot override it. |
| `unsupported` | The drawing or XML payload needs adoption or lies outside this protected scope. | Refuse; acceptance cannot override it. |

`can_write` means an actual repair is eligible, projected strict validation passes, and any applicable baseline acceptance was supplied. It is false for `not-needed`, unsafe/unsupported inputs, strict-quality blockers, and unaccepted missing hashes; it does not promise later source freshness or output delivery. `baseline_accepted` is true only when this missing-hash invocation explicitly supplied acceptance.

Write with a reviewed input SHA and a distinct new path. `--accept-unverified-baseline` is permitted only for the `confirmation-required` missing-hash case; it cannot accept model-hash drift, invalid/empty metadata, missing core semantics, or another refusal reason. There is no migration `--force`, non-strict mode, target-schema conversion, in-place mode, or `--accept-model-drift` alias.

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" migrate \
  --input "<old.drawio>" --output "<migrated.drawio>" \
  --expected-input-sha256 "<reviewed-sha256>" \
  --accept-unverified-baseline
```

Omit that flag for `automatic`. The write rejects path aliases and an occupied output, keeps the input and pre-existing target unchanged on failure, checks the SHA and source again before same-directory atomic no-clobber publication, and never uses replace/copy fallback. It accepts delivery only after exact in-memory changes, projected strict validation, serialized readback/signature/semantic checks, independently recomputed comparison, and serialized strict validation. A post-commit temporary-cleanup warning records successful `written: true` separately from quality; a pre-commit failure remains a delivery failure.

The plan contains only actual changes, in this order: `data-lane-order`, `data-model-hash-version`, `data-model-hash`, `data-tool-version`. Every change identifies the actual pool cell, semantic ID, old presence/value, new string value, and stable reason. At most these four pool attributes may change: absent lane order uses the reader's existing root-entry order (never geometric sorting); absent hash version may be filled; an absent hash needs the narrow explicit acceptance; and the tool version changes only when another allowed repair occurs. A valid existing hash remains byte-for-byte unchanged. This is a precise change plan, not a pool-attribute allowlist: all other attributes, cells, semantic facts, geometry, routing, unknown XML payload, mixed text/tails, and sibling order must remain protected.

Verify delivery independently:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" compare \
  --before "<old.drawio>" --after "<migrated.drawio>" --migration
```

`--migration` and `--changes` are mutually exclusive. The migration comparison recomputes the plan from `before`; it does not trust a writer receipt. It rejects a wrong cell/value, missing or extra attribute, and every unrelated structure, identity, semantic, geometry, unknown-payload, text/tail, or order difference. A valid XML `after` with such a difference yields `preserved: false`; malformed input is a refusal. A passing comparison proves rule-bound preservation only: it neither accepts an unverified baseline nor substitutes for strict validation.

The stable receipt uses `operation: "migrate"`, `migration_rule_version: "1"`, `producing_tool_version`, `input`, `source_managed_state`, `classification`, `can_write`, `reasons`, `semantic_summary`, `planned_changes`, `baseline_acceptance_required`, `baseline_accepted`, `written`, `output`, `validation`, `preservation`, and `delivery`. Migration comparison uses `operation: "compare-migration"`, its before/after receipts, classification, planned changes, preservation, differences, and reasons. The candidate exit convention is: `0` for completed dry-run classification, not-needed, or successful delivery; `1` for write-time candidate strict/preservation failure; `2` for invalid input/parameters, expected-SHA, alias/output, classification, confirmation, or precommit I/O refusal; and `3` for unexpected internal errors.

Historical-source reconstruction and reproducible neutral synthetic mutations are limited format evidence; they do not demonstrate that a real historical missing-field artifact is compatible or that historical business need existed. Record historical reconstruction, synthetic mutation, historical/current editor saves, export, Agent visual review, and human acceptance as separate evidence types. Do not claim real historical missing-field-original compatibility until it is actually verified.
