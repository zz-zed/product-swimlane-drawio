---
name: product-swimlane-drawio
description: Create, inspect, and incrementally update native, editable Draw.io vertical swimlane process diagrams with a versioned semantic schema, confirmed main path, phases, stable IDs, safe deletion, geometry-preserving patches, structured diagnostics, and visual-quality checks. Use when an agent must turn a natural-language process into a local .drawio file or safely revise a compatible diagram without discarding manual layout changes.
---

# Editable Draw.io Swimlanes

Build native, uncompressed `.drawio` files with the bundled Python tool. Keep the latest user-saved `.drawio` canonical after local editing.

## Requirements

- Use Python 3.10 or later.
- Resolve referenced files relative to this `SKILL.md`; never assume an installation path or working directory.
- Treat Draw.io Desktop or the web app as an optional editor and renderer, not as a generation dependency.
- Keep task inputs and outputs outside the skill directory.

## Start from natural language

Do not ask the user to understand ranks, node types, ports, calls, or returns. Ask only for missing high-impact information in everyday terms:

- What the diagram describes.
- Which systems or roles participate.
- What starts the process.
- What happens in the normal path, in order.
- What can fail, branch, return, or retry.
- What marks completion.
- What must stay out of scope.
- Which ambiguous steps are manual or automatic.

When the user asks to confirm the structure first, do not generate files yet. Return a compact confirmation card containing:

1. Lane order from left to right.
2. Numbered main path with one owner per step.
3. Decision and exception paths, including their return targets.
4. Assumptions and unresolved questions.

Wait for explicit confirmation. Never add unprovided intermediate steps, data exchanges, owners, or outcomes. Mark uncertain items as unresolved.

## Build after confirmation

1. Read [references/schema.md](references/schema.md). Use its v3 contract for new diagrams; keep v2 only for compatibility work.
2. Translate the confirmed structure into a task-local JSON specification with `schema_version: "3"`, a fitting `behavior_pattern`, stable semantic IDs, and the confirmed `main_path`. Add `phases` only when the process has meaningful horizontal stages.
3. Express repeated topology with `groups`, same-rank composition with `left/main/right` slots, and explanatory notes with semantic anchors. Choose `compact`, `review`, or `long-form` spacing. When phases are navigation labels rather than colored backgrounds, use `layout.phase_presentation: "rail"`. Start without absolute coordinates; let the compiler expand lanes and place slots deterministically.
4. Mark primary progress as `flow_role: main`, branches and joins with their corresponding roles, historical return or retry as `route: back`, and same-rank interaction as `route: side`. Use stable `outcome` IDs plus `branch` where a decision has distinct results.
5. Build and run strict validation:

   ```bash
   python3 "<skill-root>/scripts/drawio_swimlane.py" build --spec "<spec.json>" --output "<diagram.drawio>" --strict
   python3 "<skill-root>/scripts/drawio_swimlane.py" validate --input "<diagram.drawio>" --strict
   ```

6. Follow structured diagnostic codes and `supported_fixes`. If strict validation reports a warning, correct the specification and rebuild. Do not declare completion from XML validity alone.
7. When a renderer is available, export a preview before handoff. Inspect it only when the current agent can analyze images; otherwise ask the user to review it and report that model visual review was not performed.

## Routing semantics

- Route the confirmed `main_path` before ordinary branches and returns so its channels remain visually dominant.
- Keep every downward main-path continuation bottom-to-top, including a main path that crosses into another lane. Do not send a decision's normal continuation through a side hook merely because its target is in another lane.
- Reserve a decision's top for incoming flow. Point a true exception branch toward its target lane. When a same-lane outcome terminates directly below the decision, leave from the bottom rather than creating a side hook.
- Put a side outcome on the decision's rank when the branch should read as a horizontal handoff. Keep a directly following same-lane terminal on the lane's main axis so the decision can connect bottom-to-top without a folded detour.
- For a binary decision, use distinct `positive` and `negative` branches. For three or more outgoing edges, give every edge an `outcome` ID; reuse an outcome only when one result intentionally triggers multiple actions. Treat `branch` as a directional hint that may repeat.
- Allocate source and target ports as a pair. Prefer the center (`0.5`) of the selected source and target sides whenever those ports are free; only move to secondary offsets for an actual port conflict or explicit override. Treat endpoint alignment as secondary to balanced attachment points.
- Route retries and returns after forward paths. Keep their target trunks in an independent outer-side slot inside the historical target lane and at least 16 pixels from the matching forward or response channel. An adjacent-lane retry should leave toward the target but enter through the target's outer side, keeping the normal facing corridor clear. A long cross-lane return may leave from the source's outer side and use the outer bottom corridor before entering the target gutter.
- Allow new-diagram layout to widen an automatic target lane when its side gutter cannot safely contain a return or retry trunk. Recompute downstream lane positions and automatic routes from the expanded geometry.
- Give each connection a distinct port by default. Set `allow_port_reuse` only for an intentional convergence.
- Keep cross-lane vertical segments at least 16 pixels away from lane boundaries.
- Score automatic candidates by bends, length, short segments, lane intrusion, node and connector clearance, reciprocal separation, main-path continuity, and label capacity.
- Put ordinary labels on the longest clear independent segment available. For an automatic retry or return, prefer the nearest clear carrier to the source action instead of a distant outer-canvas detour. After all automatic routes exist, reflow labels globally so later return paths cannot invalidate earlier label placement. Re-route or increase automatic spacing before accepting a label collision.
- Use explicit `exit_side`, `entry_side`, offsets, or waypoints only when semantic defaults cannot produce a clear route.
- Never simplify or silently rewrite explicit waypoints. Diagnose their quality issues and require an intentional edit instead.

## Visual quality gate

Require strict validation to have no warnings. It checks:

- Schema version, main-path continuity, reachability, decisions, retries, and phase ranges.
- Broken endpoints and duplicate semantic IDs.
- Nodes outside their lanes.
- Likely node-label overflow for multilingual text.
- Fixed-aspect start/end geometry, unlabeled solid end nodes, and excessive process padding.
- Reused ports.
- Connectors collinear with lane boundaries.
- Connectors crossing nodes.
- Connector segments crossing, overlapping, or becoming non-orthogonal.
- Internal segments shorter than 16 pixels, unnecessary bends, hairpins, near-parallel crowding, and ambiguous reciprocal channels.
- Same-lane main-path zigzags.
- Edge labels without a clear carrier or overlapping nodes, connectors, or other labels.
- Phase backgrounds above editable content, opaque lane bodies hiding phase bands, or interactive phase cells.

Treat automated validation and visual review as separate evidence:

- Always run strict validation on the actual `.drawio` file being handed off.
- If Draw.io opens, moves, edits, or saves the file, re-run strict validation on that final saved file.
- If the current agent can analyze images, inspect the rendered preview for clipped labels, ambiguous arrow direction, hidden arrowheads, excessive detours, and visual collisions.
- If the current agent cannot analyze images, export a preview when possible and request user review. Never claim that visual review passed.
- Report `strict validation`, `preview export`, and `model visual review` as separate statuses.
- Treat `visual_review: "not_available"` as an explicit incomplete visual-review status, never as a strict-validation success alias.

## Update an existing diagram

1. Start from the latest user-saved `.drawio`, never an older JSON specification.
2. Inspect the file before planning a patch:

   ```bash
   python3 "<skill-root>/scripts/drawio_swimlane.py" inspect --input "<current.drawio>"
   ```

3. Confirm that the result is compatible. Otherwise explain that migration or rebuilding is required for safe patching.
   If a user redraws a connector directly in Draw.io, report it under `unmanaged_edges` and the `interoperability/unmanaged-edges` diagnostic. Recover its source/target relationship for review, but do not pretend its stable semantic ID was preserved.
4. Put only requested updates, additions, deletions, phase changes, or a replacement `main_path` in a task-local patch file. Explicitly list incident edges when deleting a node.
5. Write to a new output file, validate, and compare against the declared patch:

   ```bash
   python3 "<skill-root>/scripts/drawio_swimlane.py" patch --input "<current.drawio>" --changes "<changes.json>" --output "<updated.drawio>" --strict
   python3 "<skill-root>/scripts/drawio_swimlane.py" validate --input "<updated.drawio>" --strict
   python3 "<skill-root>/scripts/drawio_swimlane.py" compare --before "<current.drawio>" --after "<updated.drawio>" --changes "<changes.json>"
   ```

6. Use `update_edges` with `reroute: true` to change ports or routing without moving nodes.
7. Use `--allow-geometry-updates` only when the user explicitly requests moving or resizing existing nodes. The tool reroutes only incident edges that become invalid and reports their IDs.
8. Keep valid manual waypoints and all unrelated geometry unchanged.
9. Keep the input unchanged until the user approves replacement. Do not use `--force` without explicit replacement intent.

## Layout and handoff

- Give each participating system or role one full-height vertical lane.
- Use global `rank` values for top-to-bottom order; assign the same rank to parallel steps.
- In v3, use `left/main/right` slots for nodes sharing a lane and rank. Use groups to preserve why nodes are parallel, branching, merging, exceptional, or supportive; equal ranks alone do not carry that meaning.
- Anchor notes to the node they explain. Do not position floating annotations with guessed coordinates when a semantic anchor is sufficient.
- Keep nodes structurally parented to their owning lanes.
- When phases use `bands`, keep semantic Z-order as phase backgrounds, lanes, nodes, then connectors; make lane bodies transparent. When phases use `rail`, reserve the left label column and keep lane bodies opaque. Phase cells remain non-interactive in both modes.
- Prefer three to five lanes per page; split dense exception detail when necessary.
- Preserve stable IDs across revisions.
- Report added, updated, deleted, and automatically rerouted semantic IDs from the patch receipt.
- Report the output path, byte count, and SHA-256 digest from the atomic-delivery receipt.
- Report `main_path_bends`, `short_segments`, `label_conflicts`, `reciprocal_ambiguities`, `manual_waypoints_preserved`, and `visual_review` from the QA receipt. Treat `manual_waypoints_preserved: null` as not applicable because no pre-existing explicit waypoints were checked; never present it as a successful preservation measurement.
- Deliver `.drawio` as the editable source. Treat SVG, PNG, or PDF as optional previews.

## Package neutrality

- Keep the package free of user data, organization names, proprietary terminology, and domain-specific sample flows.
- Never store generated specifications, diagrams, previews, or test fixtures in the skill directory.
- Keep the core workflow compatible with the Agent Skills directory format; isolate product-specific metadata in its optional metadata directory.

## Limits

Use this skill for editable vertical swimlane process diagrams. Use another representation for strict BPMN conformance, infrastructure topology, or free-form presentation graphics.
