# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/zz-zed/product-swimlane-drawio/releases/tag/v0.1.0
