---
name: product-swimlane-drawio
description: Create, inspect, and incrementally update native, editable Draw.io vertical swimlane process diagrams with a versioned semantic schema, confirmed main path, phases, stable IDs, safe deletion, geometry-preserving patches, structured diagnostics, and visual-quality checks. Use when an agent must turn a natural-language process into a local .drawio file or safely revise a compatible diagram without discarding manual layout changes.
---

# Editable Draw.io Swimlanes

Build native, uncompressed `.drawio` files with the bundled Python tool. Keep the latest user-saved `.drawio` canonical after local editing.

## Requirements

- Use Python 3.10 or later.
- The complete `product-swimlane-drawio` Skill directory is the runtime unit. Do not copy only `scripts/drawio_swimlane.py`; keep its adjacent `swimlane_core` package and use the existing relative CLI path. No pip install, `PYTHONPATH`, or other dependency is required.
- Resolve referenced files relative to this `SKILL.md`; never assume an installation path or working directory.
- Treat Draw.io Desktop or the web app as an optional editor and renderer, not as a generation dependency.
- Keep task inputs and outputs outside the skill directory.

## Choose the task

- **New diagram:** confirm the structure, then read the [build contract](references/schema.md#build-specification) and use the build workflow below.
- **Modify an existing diagram:** use the latest saved file; read the [patch contract](references/schema.md#patch-specification), [inspection rules](references/schema.md#inspection-and-diagnostics), [artifact integrity](references/schema.md#artifact-integrity), and [compatibility matrix](references/schema.md#compatibility).
- **Read-only check:** read only the relevant [inspection](references/schema.md#inspection-and-diagnostics), [integrity](references/schema.md#artifact-integrity), or [compatibility](references/schema.md#compatibility) section, then choose the requested `inspect`, `validate`, or `compare` command. Do not build, patch, overwrite, or automatically repair a diagram during a read-only request.

Use information already supplied by the user; ask only for a missing fact that changes process meaning, ownership, the main path, or a safety authorization. When the user asks to confirm the structure first, wait without creating a specification or diagram.

Use the same tool version for a patch and its subsequent comparison. For read-only review of an older completed patch, read the [cross-version limitation](references/schema.md#compatibility): a producing-version mismatch can cause comparison failure. Explain the evidence without marking it passed, changing the saved version stamp, or automatically rebuilding/patching the files.

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

1. Read the [build contract](references/schema.md#build-specification). Use its v3 contract for new diagrams; keep v2 only for compatibility work.
2. Translate the confirmed structure into a task-local JSON specification with `schema_version: "3"`, a fitting `behavior_pattern`, stable semantic IDs, and the confirmed `main_path`. Add `phases` only when the process has meaningful horizontal stages.
3. Express repeated topology with `groups`, same-rank composition with `left/main/right` slots, and explanatory notes with semantic anchors. Choose `compact`, `review`, or `long-form` spacing. When phases are navigation labels rather than colored backgrounds, use `layout.phase_presentation: "rail"`. Start without absolute coordinates; let the compiler expand lanes and place slots deterministically.
4. Mark primary progress as `flow_role: main`, branches and joins with their corresponding roles, historical return or retry as `route: back`, and same-rank interaction as `route: side`. Use stable `outcome` IDs plus `branch` where a decision has distinct results.
5. Build and run strict validation:

   ```bash
   python3 "<skill-root>/scripts/drawio_swimlane.py" build --spec "<spec.json>" --output "<diagram.drawio>" --strict
   python3 "<skill-root>/scripts/drawio_swimlane.py" validate --input "<diagram.drawio>" --strict
   ```

6. Follow structured diagnostic codes and `supported_fixes`. If strict validation reports a warning, correct the specification and rebuild. Do not declare completion from XML validity alone. The Agent owns process meaning and layout intent; the script calculates ports, route candidates, geometry, and labels. Do not hand-calculate those algorithmic details.
7. When a renderer is available, export a preview before handoff. The script's raw `visual_review` receipt remains `not_available`; report preview export, any later Agent image inspection, and human review as separate evidence.

## Routing semantics

- Route the confirmed `main_path` before ordinary branches and returns so its channels remain visually dominant.
- Keep every downward main-path continuation bottom-to-top, including a main path that crosses into another lane. Do not send a decision's normal continuation through a side hook merely because its target is in another lane.
- Allocate source and target ports as a pair. Prefer the center (`0.5`) of the selected source and target sides whenever those ports are free; only move to secondary offsets for an actual port conflict or explicit override. Treat endpoint alignment as secondary to balanced attachment points.
- Route returns and retries after forward paths, using independent return channels; retain explicit waypoints unchanged.
- Keep decision outcomes semantically explicit and let the script select safe route/label candidates. Use manual ports or waypoints only after diagnostic or visual evidence identifies a need.
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
- If a preview is available, later Agent image inspection may check clipped labels, ambiguous arrow direction, hidden arrowheads, excessive detours, and visual collisions; human review is separate again. Never claim a raw runtime visual review passed.
- Report `strict validation`, `preview export`, `raw visual_review`, later `Agent image inspection`, and `human review` as separate statuses.
- Treat `visual_review: "not_available"` as an explicit incomplete visual-review status, never as a strict-validation success alias.

## Update an existing diagram

1. Start from the latest user-saved `.drawio`, never an older JSON specification.
2. Inspect the file before planning a patch:

   ```bash
   python3 "<skill-root>/scripts/drawio_swimlane.py" inspect --input "<current.drawio>"
   ```

3. Read `has_semantic_metadata`, `managed_state`, the integrity diagnostics, and `input.sha256` from the inspection result.
   - For `managed`, proceed from the reported SHA-256 baseline.
   - For `recoverable`, review every diagnostic. A legacy file with only `integrity/model-hash-missing` may be upgraded by a reviewed patch. Unmanaged vertices or connectors must be reconciled or preserved deliberately.
   - For `unsafe`, stop. A schema-composition error requires migration or controlled rebuilding. A model-hash mismatch may be accepted only after the user confirms that the direct semantic edits are intentional and the patch represents them.
   The legacy `compatible` field is only a coarse alias; do not use it as the sole safety decision. If a user redraws a connector directly in Draw.io, report it under `unmanaged_edges` and the `interoperability/unmanaged-edges` diagnostic. Recover its source/target relationship for review, but do not pretend its stable semantic ID was preserved.
4. Put only requested updates, additions, deletions, phase changes, or a replacement `main_path` in a task-local patch file. Add lanes relative to a stable neighboring lane with exactly one of `before` or `after`; do not use a numeric index. Explicitly list incident edges when deleting a node. Deleting a lane also requires explicit deletion of every owned node and reconciliation of affected edges, `main_path`, and groups.
5. Write to a new output file, validate, and compare against the declared patch:

   ```bash
   python3 "<skill-root>/scripts/drawio_swimlane.py" patch --input "<current.drawio>" --expected-input-sha256 "<sha256-from-inspect>" --changes "<changes.json>" --output "<updated.drawio>" --strict
   python3 "<skill-root>/scripts/drawio_swimlane.py" validate --input "<updated.drawio>" --strict
   python3 "<skill-root>/scripts/drawio_swimlane.py" compare --before "<current.drawio>" --after "<updated.drawio>" --changes "<changes.json>"
   ```

   `compare` is a separate delivery gate: require exit code 0 and `preserved: true`. Strict validation alone does not authorize handoff. An undeclared geometry, attribute, add/delete, unknown-content, or sibling-order difference blocks delivery.
6. Use `update_edges` with `reroute: true` to change ports or routing without moving nodes.
7. When changing an existing node type, explicitly reroute every incident edge in the same patch. Do not leave old port semantics attached to a new shape.
8. For a new v3 node, use `slot` or `anchor` when that intent is known. The patcher preserves existing node-local geometry, expands right-side lane space when necessary, and reports downstream lane shifts separately.
9. Use `--allow-geometry-updates` only when the user explicitly requests moving or resizing existing nodes. The tool reroutes only incident edges that become invalid and reports their IDs.
10. Keep valid manual waypoints and all unrelated geometry unchanged. If a lane change affects an edge with explicit waypoints, report it for visual review; never rewrite the waypoints silently.
11. Keep the input unchanged until the user approves replacement. Do not use `--force` without explicit replacement intent.
12. Use `--accept-model-drift` only for reviewed, intentional semantic edits made directly in Draw.io. Never use it for schema-composition errors, unmanaged content, or an input SHA-256 mismatch.

If diagnostics offer no safe authorized fix, a correction makes no progress, or a fix would change confirmed semantics or manual layout, stop with the diagnostic evidence and ask for a decision. Never lower strictness, add speculative waypoints, or use `--force` / `--accept-model-drift` to suppress the issue.

## Layout and handoff

- Give each participating system or role one full-height vertical lane.
- Use global `rank` values for top-to-bottom order; assign the same rank to parallel steps.
- In v3, use `left/main/right` slots for nodes sharing a lane and rank. Use groups to preserve why nodes are parallel, branching, merging, exceptional, or supportive; equal ranks alone do not carry that meaning.
- Anchor notes to the node they explain. Do not position floating annotations with guessed coordinates when a semantic anchor is sufficient.
- Keep nodes structurally parented to their owning lanes.
- When phases use `bands`, keep semantic Z-order as phase backgrounds, lanes, nodes, then connectors; make lane bodies transparent. When phases use `rail`, reserve the left label column and keep lane bodies opaque. Phase cells remain non-interactive in both modes.
- Prefer three to five lanes per page; split dense exception detail when necessary.
- Preserve stable IDs across revisions.
- Report the inspected input SHA-256, input managed state, drift acceptance status, requested semantic IDs, dependent lane shifts, and automatically rerouted edges from the patch receipt.
- Report the output path, byte count, and SHA-256 digest from the atomic-delivery receipt.
- Report `main_path_bends`, `short_segments`, `label_conflicts`, `reciprocal_ambiguities`, `manual_waypoints_preserved`, and `visual_review` from the QA receipt. Treat `manual_waypoints_preserved: null` as not applicable because no pre-existing explicit waypoints were checked; never present it as a successful preservation measurement.
- Deliver `.drawio` as the editable source. Treat SVG, PNG, or PDF as optional previews.

## Package neutrality

- Keep the package free of user data, organization names, proprietary terminology, and domain-specific sample flows.
- Never store generated specifications, diagrams, previews, or test fixtures in the skill directory.
- Keep the core workflow compatible with the Agent Skills directory format; isolate product-specific metadata in its optional metadata directory.

## Limits

Use this skill for editable vertical swimlane process diagrams. Use another representation for strict BPMN conformance, infrastructure topology, or free-form presentation graphics.
