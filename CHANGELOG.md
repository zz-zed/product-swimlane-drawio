# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/zz-zed/product-swimlane-drawio/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/zz-zed/product-swimlane-drawio/releases/tag/v0.1.0
