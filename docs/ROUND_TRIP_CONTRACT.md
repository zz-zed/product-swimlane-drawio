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

## Inspect

Compatible diagrams return both semantic intent and current geometry. Ordinary Draw.io files without Skill metadata require a generic digest or controlled migration; they must not be presented as safely patchable semantic diagrams.

## Patch

- Semantic updates apply to declared IDs only.
- Unrelated geometry remains unchanged.
- Manual waypoints remain exact unless the user explicitly reroutes that edge.
- Moving or resizing an existing node requires geometry authorization.
- A new v3 layout intent field may affect only its declared group or incident region unless a required lane expansion shifts later lanes.

## Manual corrections

Future reconciliation should translate repeated manual corrections into layout intent when the mapping is unambiguous:

- Horizontal placement becomes a lane-local slot or alignment lock.
- Repeated local spacing becomes a rank-band constraint.
- A manually separated return becomes a corridor lock.
- A nearby note becomes an anchor relationship.

Ambiguous edits remain manual geometry and receive a reconciliation diagnostic; they are never silently generalized.

## Delivery safety

1. Write a candidate to a new path.
2. Compile and validate the exact candidate.
3. Export and review an optional preview.
4. Replace a reviewed target only with explicit replacement intent.
5. Re-inspect and revalidate after Draw.io saves the file.

A failed candidate never replaces the last known good artifact.
