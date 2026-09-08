# Draw.io Round-trip Contract

## Sources of truth

Round-trip editing has two coordinated authorities:

- Process meaning is identified by stable semantic IDs and embedded semantic metadata.
- Geometry is authoritative in the latest user-saved Draw.io file.

An older JSON specification must never overwrite newer local geometry.

## Generated metadata

The v3 Draw.io adapter stores enough metadata to recover:

- Schema version and main path.
- Behavior pattern and layout profile.
- Lane and node IDs.
- Rank, slot, group membership, and note anchor.
- Edge type, flow role, outcome, route class, ports, and waypoint origin.

This metadata supplements native Draw.io geometry; it does not prevent manual editing.

Generated artifacts also store the producing tool version, semantic-model hash version, stable lane order, and a semantic-model hash. The hash covers process identity and meaning but excludes locally editable visual state such as geometry, styles, lane widths, phase colors, ports, and manual waypoints.

## Inspect

Inspection returns semantic intent, current geometry, the exact input SHA-256 digest, and one of three managed states: `managed`, `recoverable`, or `unsafe`. A missing hash in an older managed file is recoverable. A semantic-model hash mismatch or malformed schema composition is unsafe. Ordinary Draw.io files without Skill metadata require a generic digest or controlled migration; they must not be presented as safely patchable semantic diagrams. Inspection is evidence, not patch permission.

## Patch

- Semantic updates apply to declared IDs only.
- Lane insertion uses stable `before` / `after` references. Lane deletion requires explicit reconciliation of owned nodes, incident edges, `main_path`, and groups.
- Unrelated geometry remains unchanged.
- Unknown cells retain their complete subtrees and relative order among siblings. Phase normalization may sort managed runs between unknown cells, but cannot move an existing sibling across an unknown cell. A resulting phase-layer conflict fails validation without writing the candidate, even in non-strict mode.
- Saved ports and waypoints remain exact for label-only updates, even when the origin is still `automatic`. Labels keep their native position when possible and otherwise move only on the saved path. Explicit waypoint arrays remain exact even with `reroute: true`; supplying `waypoints` is the intentional replacement operation.
- Moving or resizing an existing node requires geometry authorization. If a node/lane change invalidates a frozen route, `patch/route-update-required` requires an explicit route update for that edge.
- A new v3 layout intent field may affect only its declared group or incident region unless a required lane expansion shifts later lanes.
- Patch-added v3 nodes consume `slot` and note `anchor` intent; dependent lane shifts, text updates, label repositioning and rerouted edges are separate receipt evidence.
- A node-type change requires explicit rerouting of all surviving incident edges.
- A patch must name the `input.sha256` observed during inspection through `--expected-input-sha256`, preventing a later save from silently changing the baseline.
- Reviewed direct semantic edits require both an equivalent declared patch and `--accept-model-drift`. Schema-composition errors cannot be overridden.

## Native label evidence

Inspect, validate and routing obstacles share current native label measurement.
Relative x/y and offset are read from saved geometry; `data-label-*` remains
historical generation metadata. Supported bounds are estimated, and unsupported
styles or router geometry report `not_available` with a strict-failing warning.
Unmeasurable frozen labels block spatial planning. Coverage counts and IDs must
be reported alongside measured conflicts; zero conflicts is not a coverage pass.
See the [supported profile](../skills/product-swimlane-drawio/references/schema.md#native-edge-labels-and-patch-receipts).

## Manual corrections

`compare` checks paint order within each parent, including unknown cells; it
does not equate flat XML order across different parents with drawing order.
Optional `changed_sibling_order` and `unexpected_sibling_order` records contain
the raw parent ID and ordered raw cell IDs before/after (only shared siblings).
For declared patches, the expected order comes from the actual replayed patch,
so supported additions/deletions do not count as unexplained reordering.
`unexpected_unmanaged_cells` records raw cell IDs and `added`, `missing`, or
`changed` status, comparing full subtrees including attributes, geometry and
custom children. Native `object` / `UserObject` wrappers are kept as drawing
units using their nested cell's parent and their wrapper ID. Wrapper metadata
and all internal text/tails (including whitespace-only content) are checked
and protected from writer indentation. Only whitespace between root entries
is treated as formatting, unless inherited `xml:space="preserve"` applies.
Any unexpected order or unmanaged-content difference makes
`preserved=false` and CLI exit 1. These evidence fields are omitted when empty;
the existing semantic-cell count still counts managed cells only.

Managed cells also protect complete ordered native payloads: extensions,
repeated geometry, mixed text/tails, child order, and inherited `xml:space`.
`changed_cell_content` / `unexpected_cell_content` locate differences by
semantic ID, kind and native path. Known structural formatting whitespace may
normalize; opaque content does not. The guarantee excludes ElementTree-discarded
comments/processing instructions and arbitrary XML byte-for-byte preservation.
An independent saved-edge guard checks permissions without replaying the patch.

`compare` is a delivery gate after a declared patch: require exit 0 and
`preserved=true` in addition to strict validation. It compares observed
differences; an identical ordinary or unsafe input pair can still return
`preserved=true`, which does not classify the input as patch-safe.

Use the same tool version for a patch and its comparison. Reviewing a completed
0.5.1 patch with 0.6.0 is different from editing an old input with the current
tool: declared-patch replay writes the current `data-tool-version`, so an old
`after` may yield `unexpected_attributes: ["pool:main"]`, `preserved=false` and
exit 1 solely because of its producing stamp. This limitation does not damage
the input or prove a semantic/geometry defect, but the failed gate remains
failed. Other pool attributes may represent real changes; never waive the whole
pool or edit an old result's stamp to pass. Read-only checks must not trigger
automatic patching or rebuilding. See [compatibility](../skills/product-swimlane-drawio/references/schema.md#compatibility)
for isolated original-version verification and authorized current-version editing.

Future reconciliation should translate repeated manual corrections into layout intent when the mapping is unambiguous:

- Horizontal placement becomes a lane-local slot or alignment lock.
- Repeated local spacing becomes a rank-band constraint.
- A manually separated return becomes a corridor lock.
- A nearby note becomes an anchor relationship.

Ambiguous edits remain manual geometry and receive a reconciliation diagnostic; they are never silently generalized.

## Delivery safety

1. Inspect the latest user-saved input and capture its SHA-256 digest and managed state.
2. Write a candidate to a new path while requiring the captured input digest.
3. Parse the serialized candidate, verify it against accepted in-memory content, run the independent preservation guard, validate/strict-check it, and compare the patch before atomic replacement.
4. Export and review an optional preview.
5. Replace a reviewed target only with explicit replacement intent.
6. Re-inspect and revalidate after Draw.io saves the file.

A failed candidate never replaces the last known good artifact.
