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
- **Conservatively migrate an older managed diagram:** read [conservative metadata migration](references/schema.md#conservative-metadata-migration). Use it only for same-schema metadata omissions in a single-page managed drawing; it cannot adopt drawing semantics, convert schemas, or repair drift.
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
- Plan source and target ports across the complete mutable edge batch, then allocate each pair together. Prefer the center (`0.5`) of the selected source and target sides whenever those ports are free and yield a valid route; move to secondary offsets for an actual conflict, route failure, main-path continuity, or explicit override. Keep successful unrelated port components stable when one component is replanned.
- Route returns and retries after forward paths, using independent return channels; retain explicit waypoints unchanged.
- Across lanes, prefer a clear direct elbow or outer return corridor without adding a vertical leg solely to enter the target lane. Within one lane, retain the internal return corridor. In v3, a same-lane downward decision branch uses a free bottom exit before a side hook; reserved main-path and explicit ports remain protected.
- Keep decision outcomes semantically explicit and let the script select safe route/label candidates. Use manual ports or waypoints only after diagnostic or visual evidence identifies a need.
- Automatic candidates must pass shared geometry checks and native-path/label preflight before scoring. Side, port, path and batch-label repairs are bounded; an exhausted search is a failure, not a low-quality output.
- Read optional `evidence.planning` for the failed stage, native reason, blockers and budget. Automatic planning can fail before final validation even without `--strict`; an empty label does not hide an unsupported native path. Saved-file commands and diagnostic severity conventions remain, but saved v2/v3 cross-lane automatic returns can have different warnings or strict results because their target-lane vertical corridor is no longer required.
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
- Calibrated arrowhead terminal-run clearance for supported default Draw.io connector and target styles; unsupported rendering states remain `not_available`, not passed.
- Same-lane main-path zigzags.
- Edge labels without a clear carrier or overlapping nodes, connectors, or other labels. Current native label geometry is used; unsupported styles report `text/edge-label-geometry-unavailable`, which fails strict validation. Estimated text bounds are not pixel-accurate visual evidence.
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
6. A label-only update freezes saved ports and waypoints, including GUI edits still marked `automatic`. It first keeps the saved label position, then tries positions on that same path. If none fits, shorten the text, move the label, or explicitly request that edge reroute. Use `update_edges` with `reroute: true` to change ports or routing without moving nodes; an existing task request to adjust the route supplies this intent. Explicit waypoints remain protected even with `reroute: true`; replace them only by providing `waypoints` in the patch.
7. When changing an existing node type, explicitly reroute every incident edge in the same patch. Do not leave old port semantics attached to a new shape.
8. For a new v3 node, use `slot` or `anchor` when that intent is known. The patcher preserves existing node-local geometry, expands right-side lane space when necessary, and reports downstream lane shifts separately.
9. Use `--allow-geometry-updates` only when the user explicitly requests moving or resizing existing nodes. If a node or lane change invalidates a saved route, explicitly declare the affected edge reroute; geometry permission alone does not authorize changing that route.
10. Keep valid manual waypoints and all unrelated geometry unchanged. If a lane change affects an edge with explicit waypoints, report it for visual review; never rewrite the waypoints silently.
11. Keep the input unchanged until the user approves replacement. Do not use `--force` without explicit replacement intent.
12. Use `--accept-model-drift` only for reviewed, intentional semantic edits made directly in Draw.io. Never use it for schema-composition errors, unmanaged content, or an input SHA-256 mismatch.

If diagnostics offer no safe authorized fix, a correction makes no progress, or a fix would change confirmed semantics or manual layout, stop with the diagnostic evidence and ask for a decision. Never lower strictness, add speculative waypoints, or use `--force` / `--accept-model-drift` to suppress the issue.

## Conservatively migrate an older managed diagram

Use migration only when a single-page managed file already has verifiable identity, ownership, topology, schema semantics, and raw geometry. Do not use it for ordinary nonmanaged drawings, unknown nodes or edges that need adoption, schema conversion, empty/invalid metadata, hash drift, or any missing core process fact. This workflow is distinct from patching: it never builds, normalizes, reroutes, refreshes general metadata, or infers a lane order from geometry.

1. Run a read-only plan first:

   ```bash
   python3 "<skill-root>/scripts/drawio_swimlane.py" migrate --input "<old.drawio>" --dry-run
   ```

   Read the input SHA-256, classification, reasons, planned changes, strict-validation evidence, and `can_write`. The classifications are `not-needed`, `automatic`, `confirmation-required`, `unsafe`, and `unsupported`; they describe metadata eligibility rather than a strict-quality pass. Do not create an output for `not-needed`.
2. Only `automatic`, or `confirmation-required` with the user's explicit acceptance for this invocation, can proceed. `--accept-unverified-baseline` applies only to an otherwise valid missing historical hash; it cannot override hash drift, an unsupported rule/schema, an empty value, missing identity/topology, or unsupported drawing content. Never treat a previous dry run as acceptance for a later command.
3. For a write, require the reviewed SHA and a distinct, not-yet-existing output path. Do not offer `--force`, an in-place write, non-strict delivery, schema conversion, or `--accept-model-drift` as a migration substitute:

   ```bash
   python3 "<skill-root>/scripts/drawio_swimlane.py" migrate \
     --input "<old.drawio>" --output "<migrated.drawio>" \
     --expected-input-sha256 "<sha256-from-dry-run-or-inspect>" \
     --accept-unverified-baseline
   ```

   Omit the acceptance flag for `automatic`; never add it just in case. The command must preserve the input and an existing destination, recheck the source before atomic no-clobber delivery, and require projected plus serialized strict validation and preservation checks.
4. Confirm that the plan changes no more than the exact eligible pool attributes: absent `data-lane-order`, absent `data-model-hash-version`, accepted absent `data-model-hash`, and `data-tool-version` only when another allowed repair occurs. Existing valid hashes stay byte-for-byte unchanged. Every other attribute, semantic fact, geometry, route, unknown XML payload, text/tail, and sibling order remains protected.
5. Independently verify a delivered migration; do not use `--changes` in this mode:

   ```bash
   python3 "<skill-root>/scripts/drawio_swimlane.py" compare \
     --before "<old.drawio>" --after "<migrated.drawio>" --migration
   ```

   Require exit code 0 and `preserved: true`. This comparison recomputes the plan from `before`; it rejects incorrect, missing, moved, or extra changes and all unrelated content/geometry/order differences. It does not establish that a missing-hash baseline was accepted or replace strict validation.

Historical-source reconstruction and reproducible neutral mutations establish limited format evidence only. Keep historical editor-save, current editor-save, export, Agent visual review, and human acceptance separate, and do not claim real historical missing-field originals were verified unless they actually were.

## Layout and handoff

- Give each participating system or role one full-height vertical lane.
- Use global `rank` values for top-to-bottom order; assign the same rank to parallel steps.
- In v3, use `left/main/right` slots for nodes sharing a lane and rank. Use groups to preserve why nodes are parallel, branching, merging, exceptional, or supportive; equal ranks alone do not carry that meaning.
- Anchor notes to the node they explain. Do not position floating annotations with guessed coordinates when a semantic anchor is sufficient.
- Keep nodes structurally parented to their owning lanes.
- When phases use `bands`, keep semantic Z-order as phase backgrounds, lanes, nodes, then connectors; make lane bodies transparent. When phases use `rail`, reserve the left label column and keep lane bodies opaque. Phase cells remain non-interactive in both modes.
- Prefer three to five lanes per page; split dense exception detail when necessary.
- Preserve stable IDs across revisions.
- Lead the handoff with the file, actual changes, validation, and outstanding visual evidence. Attach the input SHA-256, managed state, drift acceptance, requested IDs, dependent lane shifts, `label_updated_edges`, `label_repositioned_edges`, `rerouted_edges`, and `saved_routes` as receipt details.
- Report the output path, byte count, and SHA-256 digest from the atomic-delivery receipt.
- Report `main_path_bends`, `short_segments`, `label_conflicts`, `reciprocal_ambiguities`, `arrowhead_clearance`, `manual_waypoints_preserved`, and `visual_review` from the QA receipt. Treat partial or `not_available` arrowhead coverage as incomplete evidence, not a pass. Treat `manual_waypoints_preserved: null` as not applicable because no pre-existing explicit waypoints were checked; never present it as a successful preservation measurement.
- Deliver `.drawio` as the editable source. Treat SVG, PNG, or PDF as optional previews.

## Package neutrality

- Keep the package free of user data, organization names, proprietary terminology, and domain-specific sample flows.
- Never store generated specifications, diagrams, previews, or test fixtures in the skill directory.
- Keep the core workflow compatible with the Agent Skills directory format; isolate product-specific metadata in its optional metadata directory.

## Limits

Use this skill for editable vertical swimlane process diagrams. Use another representation for strict BPMN conformance, infrastructure topology, or free-form presentation graphics.
