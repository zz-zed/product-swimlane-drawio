# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/zz-zed/product-swimlane-drawio/releases/tag/v0.1.0
