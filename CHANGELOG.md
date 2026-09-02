# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.1] - 2026-09-02

### Fixed

- Check the actual Skill release inventory rather than decoding ignored local caches as text. Tracked caches, unexpected packaged files and polluted exports still fail.
- Validate phase Z-order within each parent, accepting Draw.io's depth-first XML serialization while still rejecting phase backgrounds above sibling content.
- Preserve unknown cells' relative sibling paint order during phase normalization. If safe layering conflicts with those anchors, refuse the candidate instead of crossing them.
- Detect sibling paint-order differences and complete unmanaged-cell subtree changes in `compare`, including changes previously hidden by a declared label patch.

### Tests and evidence

- Freeze v1/v2/v3 CLI, diagnostics, hashes, atomic-output and patch/compare baselines, with exact repeat-build and unordered-input checks.
- Add a small fictional combination corpus that separates strict passes, a known request/response/retry routing conflict, and explicit-port rejection.
- Add timeout-bounded same-topology timing, memory and label-overlap probes, plus separate editor and visual evidence contracts.

### Compatibility

- Strict validation no longer mistakes depth-first editor serialization for unsafe phase layering. Actual layer, visibility and interaction checks remain enforced. No routing, semantic-hash or patch-permission changes; generated files only update the tool-version stamp. Arrowhead clearance and structured visual repair remain design-only.
- `compare` adds optional `changed_sibling_order`, `unexpected_sibling_order`, and `unexpected_unmanaged_cells` evidence when applicable. Unexpected changes make `preserved` false and CLI exit 1; unchanged receipts retain their existing shape. Cross-parent XML serialization alone is not a paint-order change. Non-strict patching still does not make unmanaged content a strict-quality deliverable.

## [0.5.0] - 2026-08-31

### Added

- Add versioned semantic-model hashes and `managed`, `recoverable`, and `unsafe` artifact states for detecting undeclared semantic drift without treating local geometry or style edits as corruption.
- Add input SHA-256 baselines for inspection and patching, plus an explicit reviewed `--accept-model-drift` path for intentional direct semantic edits.
- Add integrity diagnostics for semantic-hash mismatches, unsupported schema composition, unmanaged vertices, and unsupported hash versions.
- Add stable `before` / `after` lane insertion, lane label and width updates, and dependency-safe lane deletion to semantic patches.
- Add v3 `slot` and note `anchor` placement for patch-added nodes, including bounded lane expansion when a right-side placement needs more space.
- Add explicit group patch operations needed to reconcile group membership when nodes or lanes are removed.

### Changed

- Refresh managed metadata after build and patch, and include input integrity evidence in patch receipts.
- Require incremental updates to bind to the exact file inspected before the patch.
- Separate requested semantic changes from dependent lane shifts and automatic edge reroutes in patch receipts.
- Require explicit incident-edge rerouting when a patch changes a node type.

### Fixed

- Prevent `compare` from treating every pool-level semantic change as declared merely because a patch file was supplied; only integrity metadata refreshes are ignored automatically.
- Return structured integrity diagnostics for malformed nested managed metadata instead of generic input or internal errors.
- Detect disagreement between the managed group model and mirrored node group membership.
- Prevent the test-only dynamic module loader from writing Python bytecode into the packaged Skill directory during CI.

### Tests

- Add regressions for geometry-safe hashing, reviewed semantic rebaselining, required and changed-input rejection, legacy metadata upgrades, malformed schema composition, nested metadata, group mirrors, undeclared pool changes, and unmanaged vertices.
- Add a regression proving that loading the tool module leaves the Skill directory unchanged when the test suite runs without `-B`.
- Add regressions for lane insertion, lane resizing, safe lane deletion, group dependencies, v3 slot and anchor placement, node-type reroute guards, exact waypoint preservation, and impact-aware compare receipts.

## [0.4.1] - 2026-08-31

### Added

- Add opt-in `--strict` delivery gates to `build` and `patch`; warnings now stop the command before the requested output is written.
- Add stable `internal/unexpected` JSON diagnostics for unanticipated failures without exposing implementation details.

### Changed

- Make the Skill, bilingual README commands, and fictional example use strict build and patch delivery by default.
- Report `strict_mode` and `quality_gate_passed` on successful build and patch operations.
- Clarify that v3 behavior patterns and groups are currently validated, preserved semantic metadata rather than active pattern-specific layout grammars.
- Document the bounded non-global solver, the current single-file standard-library implementation, and the single-page diagram scope.

### Fixed

- Replace the constant manual-waypoint success flag with a patch-time measurement and an explicit checked-waypoint count; report `null` when no pre-existing explicit waypoints apply.
- Reject v3-only layout, slot, anchor, flow-role, and outcome fields from v2 specifications consistently in both the declarative JSON Schema and runtime validation.

### Tests

- Add regressions for strict build and patch atomicity, unexpected-error envelopes, manual-waypoint receipts, and JSON Schema/runtime field conformance.
- Add representative v2/v3 acceptance and rejection cases that exercise the declarative Schema and the actual CLI without adding a runtime or test dependency.

## [0.4.0] - 2026-08-31

### Added

- Add a complete fictional request-review example with its prompt, semantic specification, compact same-lane rework loop, exported preview, and deterministic local Draw.io rebuild instructions.
- Add architecture and design-principles documentation for the semantic, deterministic, editable, incremental, and validated workflow.
- Add Process IR v3 with behavior patterns, layout profiles, lane-local slots, semantic groups, note anchors, flow roles, stable multi-outcome IDs, and phase-rail presentation.
- Add anonymous reference benchmarking and v3 layout-contract documentation without publishing private labels or business topology.

### Changed

- Reposition the project around editable product and business vertical swimlanes that remain maintainable after local Draw.io edits.
- Refresh Claude and Codex marketplace metadata for the `0.4.0` release while continuing to let the Claude marketplace own the plugin manifest version.
- Compile new diagrams through the v3 intent-first layout path while preserving v1 and v2 compatibility.
- Prefer centered semantic ports, straight same-rank handoffs, main-axis terminal outcomes, separated retry corridors, and source-adjacent labels for automatic return routes.
- Present the public request-review example as a v3 `approval-loop` with long-form spacing and a phase navigation rail.

### Fixed

- Preserve Process IR v3 metadata when a patch replaces `main_path` instead of silently downgrading the diagram to schema v2.
- Keep phase-rail styling byte-stable during unrelated patches so `compare` does not report undeclared phase changes.

### Documentation

- Rebuild both READMEs around a 30-second quick start, product proof, direct-XML comparison, the edit-inspect-patch loop, explicit scope boundaries, and separate multimodal reliability disclosures.
- Keep only final image assets in the README illustration directory.
- Keep generated `.drawio` files and illustration-production artifacts outside Git while retaining curated PNG assets for GitHub documentation and fictional examples.

### Tests

- Add release regressions for the complete example, deterministic byte-for-byte rebuilding, strict validation, final-only illustration assets, bilingual product positioning, architecture documents, and `0.4.0` marketplace metadata.
- Add v3 regressions for fork/join slots, request/response separation, multi-outcome decisions, horizontal decision handoffs, straight terminal branches, phase rails, profile spacing, adaptive lane axes, long retries, source-adjacent retry labels, and preservation of legacy diagrams.
- Add a v3 local-edit → inspect → patch → compare regression covering unrelated manual geometry, groups, anchors, phase rails, outcomes, flow roles, and schema-version preservation.
- Rebuild the public example twice in temporary storage during release tests so deterministic `.drawio` output is verified without committing generated diagram files.

## [0.3.1] - 2026-08-28

### Changed

- Let the Claude marketplace own the Claude plugin version by omitting `version` from `.claude-plugin/plugin.json`; retain explicit `0.3.1` versions in the Claude marketplace entry and Codex plugin manifest.

### Tests

- Update manifest consistency coverage for marketplace-managed Claude versions.

## [0.3.0] - 2026-08-28

### Added

- Add repository-local Claude Code and Codex plugin manifests and marketplace catalogs for native marketplace installation.
- Keep Claude Code, Codex, and `npx skills` installations on the same canonical `skills/product-swimlane-drawio` implementation.

### Documentation

- Document native Claude Code and Codex Plugin Marketplace installation alongside the existing Agent Skills installation path.

### Tests

- Add manifest consistency coverage and verify that both plugin ecosystems resolve the repository's single canonical Skill source.

## [0.2.2] - 2026-08-17

### Added

- Add strict diagnostics for short internal segments, unnecessary bends, hairpins, near-parallel crowding, reciprocal ambiguity, main-path zigzags, and edge-label carrier or overlap failures.
- Add QA receipt fields for main-path bends, short segments, label conflicts, reciprocal ambiguities, manual-waypoint preservation, and independent visual-review availability.
- Detect connectors manually redrawn in Draw.io without semantic IDs, expose their recoverable endpoints through `inspect`, and distinguish metadata loss from a genuinely missing connection.

### Changed

- Route confirmed main-path edges before branches and returns, keeping same-lane decision continuations bottom-to-top instead of producing right-side hooks.
- Allocate endpoint ports jointly, reserve separation around return ports, and score multiple automatic orthogonal candidates using geometry, channel, reciprocal, and label-clearance costs.
- Place automatic edge labels on clear carrier segments and adapt rank spacing, decision width, lane width, and return-channel geometry for dense multilingual flows.
- Recompute automatic label placement when an edge label changes while preserving explicit waypoint geometry.
- Prefer centered ports for adjacent-rank cross-lane flow, retain the compact 96 px grid when labels fit, and reflow automatic labels after every route has been established.

### Fixed

- Keep phase bands behind lanes, nodes, and connectors; make phase-bearing lane bodies transparent and phase cells non-interactive so generated diagrams remain locally editable.
- Remove sub-16-pixel automatic doglegs caused by independently chosen offsets and fixed endpoint jetties.
- Keep forward and retry or return paths in distinguishable channels without borrowing unrelated lanes when target-lane space can be expanded.
- Preserve sufficient port-offset precision in Draw.io XML so aligned routes remain orthogonal after serialization and reinspection.

### Tests

- Expand standard-library unittest coverage to 48 cases, including same-lane decisions, centered adjacent-lane flow, paired forward and retry paths, same-rank long labels, phase crossings and Z-order, phase visibility, patch-time phase insertion, custom lane-style preservation, narrow-lane expansion, explicit-waypoint diagnostics, reciprocal ambiguity, main-path zigzags, global label conflicts, manually redrawn connector detection, patch geometry stability, and legacy v2 compatibility.

## [0.2.1] - 2026-08-12

### Changed

- Refresh the English and Chinese README introductions with language-specific project infographics and focused workflow and quality-gate illustrations.
- Clarify that generation does not require Draw.io MCP and document the latest fixed-aspect and node-height diagnostics.
- Separate quick terminal installation from agent-assisted installation in both README versions, keeping the recommended command minimal and advanced scope flags optional.

### Fixed

- Keep start and end nodes circular when only one size dimension is provided, and reject conflicting fixed-aspect dimensions.
- Keep solid end nodes unlabeled and diagnose non-empty end labels.
- Auto-size multiline process nodes from estimated text lines and warn about excessive explicit height.
- Simplify safe automatic forward routes from a decision side exit to a top-entry target instead of retaining short staircase doglegs.
- Keep automatic back and retry trunks inside the historical target lane gutter when space permits, and diagnose fallback routes that borrow unrelated lanes.
- Expand narrow automatic-layout target lanes before routing so returns and retries have a safe internal side gutter, then recenter nodes and recompute downstream geometry.
- Preserve explicit manual waypoints while removing duplicate or collinear points only from automatically generated routes.

## [0.2.0] - 2026-08-12

### Added

- Add a strict, versioned v2 JSON Schema with explicit `main_path` and optional horizontal phases.
- Add structured diagnostics with stable codes, subjects, evidence, and supported fixes.
- Add `inspect` output for compatible semantic metadata, geometry, ports, waypoints, and validation.
- Add explicit node, edge, and phase deletion with incident-edge and main-path safety checks.
- Add atomic build and patch output receipts with byte counts and SHA-256 digests.

### Changed

- Reject unknown specification and patch fields while retaining legacy v0.1.x input compatibility.
- Automatically reroute only invalid incident edges after an authorized node geometry update.
- Preserve valid manual waypoints and unrelated semantic-cell geometry during incremental updates.
- Refuse to replace an existing output unless `--force` is supplied.
- Extend strict validation to main-path continuity, reachability, decision outcomes, retry direction, phase ranges, and multilingual text-fit risk.

### Tests

- Expand neutral regression coverage for Schema v2, structured errors, inspection, safe deletion, atomic output protection, text-fit diagnostics, and geometry-aware edge repair.

## [0.1.1] - 2026-08-11

### Changed

- Keep automatically generated vertical connector corridors at least 16 pixels away from internal lane boundaries.
- Re-run strict validation after a diagram is opened, edited, moved, or saved in Draw.io.
- Separate strict validation, preview export, and model visual review in handoff reporting.

### Documentation

- Document the relative reliability and limits of text-only, multimodal, and human-reviewed output without claiming an unmeasured accuracy rate.

### Tests

- Add neutral regressions for safe lane-boundary clearance and near-boundary validation.

## [0.1.0] - 2026-08-11

### Added

- Natural-language structure confirmation before file generation.
- Native, uncompressed, editable Draw.io output.
- Full-height vertical swimlanes with global process ranks.
- Semantic routing for forward paths, lateral interactions, returns, and retries.
- Decision branch hints and explicit port or waypoint overrides.
- Stable semantic IDs for incremental diagram updates.
- Node and edge patching with geometry-preserving defaults.
- Before-and-after comparison against declared changes.
- Strict structural and routing-quality validation.
- Agent Skills-compatible packaging for Codex, Claude Code, and compatible tools.
- English and Simplified Chinese README documentation, with English as the default.

[Unreleased]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/zz-zed/product-swimlane-drawio/releases/tag/v0.1.0
