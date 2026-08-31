# Layout Contract v3

## Objective

The layout compiler turns logical intent into a deterministic scene. It allocates space before it routes edges and treats readable geometry as a contract rather than a best-effort side effect.

## Author-facing controls

The initial v3 slice exposes only controls that describe intent:

- Lane order and optional requested width.
- Global rank bands.
- Lane-local `left`, `main`, and `right` slots.
- Groups for parallel, branch, merge, exception, or support regions.
- Notes anchored to a node on its left or right.
- Optional existing explicit geometry controls for exceptional cases.
- Phase presentation as translucent `bands` or a labeled left-side `rail`.

Agents should start without absolute coordinates, ports, or waypoints. A diagnostic may recommend one supported control after automatic compilation fails.

## Space allocation order

1. Measure node and label requirements.
2. Calculate lane capacity from the widest occupied rank band.
3. Reserve lane-local slots and their gaps.
4. Place nodes inside each rank band.
5. Reserve main, branch, response, and return corridors.
6. Route connectors and place labels.
7. Validate the final scene used for Draw.io serialization.

## Slot rules

- A single node with no authored slot uses `main` and remains centered.
- Multiple nodes in the same lane and rank are ordered `left → main → right`.
- Every occupied slot receives its own horizontal footprint.
- The lane expands when the occupied slots, node widths, gaps, and padding do not fit.
- Two nodes cannot occupy the same lane, rank, and slot without explicit non-overlapping geometry.
- A left or right note anchor selects the matching side slot when `slot` is omitted.

Slot spacing is profile-aware: `compact` starts at 20 px, `review` at 32 px, and `long-form` at 40 px. Lane-side padding starts at 20, 24, and 32 px respectively. These are compiler defaults, not schema constants.

## Groups

Groups describe local topology and layout intent:

- `parallel`: members are intended to be read as concurrent work.
- `branch`: members are alternative or side-path work.
- `merge`: members converge on a later step.
- `exception`: members are denial, retry, fallback, or failure work.
- `support`: members are notes, materials, or explanatory artifacts.

The initial slice stores group metadata and uses its membership for validation. Later compiler stages may add group frames or group-specific spacing without changing Process IR.

## Scene hard constraints

- No node-node overlap.
- No node outside its lane.
- No connector through an unrelated node.
- Orthogonal automatic routes only.
- Distinct attach points for distinct connectors unless reuse is intentional.
- No automatic internal segment shorter than 16 px.
- Labels require a clear carrier and cannot overlap nodes, routes, or other labels.
- Manual geometry pins and explicit waypoints are never silently changed.

## Port priority

- A downward main-path edge leaves from the source bottom and enters the target top, including across lanes.
- After sides are selected, both endpoints use the center port (`0.5`) when it is free.
- Secondary offsets are conflict-resolution slots, not route-shortening controls.
- A decision exception may leave toward its target lane, while a same-lane terminal outcome directly below the decision leaves from the bottom.
- A cross-lane side outcome may share the decision's rank to produce a centered horizontal handoff. A directly following same-lane terminal stays on the lane's main axis for a straight vertical connection.
- An adjacent-lane retry leaves toward the target and enters through the target's outer-side slot, preserving the facing request/response corridor; only longer returns default to an outer-bottom corridor.

## Quality profiles

The v3 layout object accepts:

- `compact`: small flows where density is preferred.
- `review`: default product-review output.
- `long-form`: long lifecycle diagrams with larger spacing and phase organization.

The profiles now select minimum automatic rank gaps of 80, 96, and 104 px respectively, together with the slot spacing above. Explicit canvas geometry remains authoritative. These thresholds are backed by the private reference benchmark and can evolve without changing Process IR.

## Phase presentation

- `bands` keeps phase cells behind the complete lane area and makes lane bodies transparent.
- `rail` reserves a 76 px label column before the first lane, keeps lane bodies opaque, and aligns each phase label with its rank range.

The rail is part of pool geometry, not a business lane. Nodes and routing coordinates remain lane-local, while later lane positions include the reserved rail width.

## Layout receipt

The compiler should return stable author-facing evidence:

- Selected behavior pattern and layout profile.
- Effective lane widths.
- Solved slots and node bounds.
- Expanded ranks or lanes and their contributors.
- Route and label diagnostics.
- Supported fixes for every unresolved issue.

Solver iterations and candidate scores are not authoring controls and should not appear in the ordinary receipt.
