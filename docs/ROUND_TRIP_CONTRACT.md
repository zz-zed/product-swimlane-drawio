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

Inspection returns semantic intent, current geometry, the exact input SHA-256 digest, and one of three managed states: `managed`, `recoverable`, or `unsafe`. A missing hash in an older managed file is recoverable. A semantic-model hash mismatch or malformed schema composition is unsafe. Ordinary Draw.io files without Skill metadata require a generic digest or controlled migration; they must not be presented as safely patchable semantic diagrams.

## Patch

- Semantic updates apply to declared IDs only.
- Lane insertion uses stable `before` / `after` references. Lane deletion requires explicit reconciliation of owned nodes, incident edges, `main_path`, and groups.
- Unrelated geometry remains unchanged.
- Manual waypoints remain exact unless the user explicitly reroutes that edge.
- Moving or resizing an existing node requires geometry authorization.
- A new v3 layout intent field may affect only its declared group or incident region unless a required lane expansion shifts later lanes.
- Patch-added v3 nodes consume `slot` and note `anchor` intent; dependent lane shifts and automatic reroutes are separate receipt evidence.
- A node-type change requires explicit rerouting of all surviving incident edges.
- A patch must name the `input.sha256` observed during inspection through `--expected-input-sha256`, preventing a later save from silently changing the baseline.
- Reviewed direct semantic edits require both an equivalent declared patch and `--accept-model-drift`. Schema-composition errors cannot be overridden.

## Manual corrections

Future reconciliation should translate repeated manual corrections into layout intent when the mapping is unambiguous:

- Horizontal placement becomes a lane-local slot or alignment lock.
- Repeated local spacing becomes a rank-band constraint.
- A manually separated return becomes a corridor lock.
- A nearby note becomes an anchor relationship.

Ambiguous edits remain manual geometry and receive a reconciliation diagnostic; they are never silently generalized.

## Delivery safety

1. Inspect the latest user-saved input and capture its SHA-256 digest and managed state.
2. Write a candidate to a new path while requiring the captured input digest.
3. Compile and validate the exact candidate.
4. Export and review an optional preview.
5. Replace a reviewed target only with explicit replacement intent.
6. Re-inspect and revalidate after Draw.io saves the file.

A failed candidate never replaces the last known good artifact.
