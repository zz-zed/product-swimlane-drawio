---
title: "product-swimlane-drawio"
topic: "technical open-source Agent Skill"
data_type: "system overview and process"
complexity: "moderate"
point_count: 7
source_language: "English and Simplified Chinese"
user_language: "Simplified Chinese"
---

## Main Topic

An Agent Skill that converts a confirmed process structure into a native editable Draw.io vertical swimlane diagram, validates it deterministically, and supports safe incremental updates.

## Learning Objectives

After viewing the visuals, the viewer should understand:

1. What the project produces and why the result remains locally editable.
2. How semantic JSON separates model reasoning from deterministic layout, routing, and validation.
3. How creating a new diagram differs from patching an existing compatible diagram.

## Target Audience

- **Knowledge Level**: Developers, product managers, and Agent tool users.
- **Context**: Evaluating or installing an open-source Skill from GitHub.
- **Expectations**: Quickly understand outputs, dependencies, workflow, and editing boundary.

## Content Type Analysis

- **Data Structure**: A left-to-right transformation pipeline plus two related operating modes.
- **Key Relationships**: Agent semantics feed a deterministic local engine; the output remains editable; local edits become the canonical input for later patches.
- **Visual Opportunities**: One project overview and one create-versus-update workflow.

## Key Data Points (Verbatim)

- "Native, uncompressed `.drawio` output"
- "No Draw.io dependency for generation"
- "The bundled Python tool uses only the standard library."
- "OpenAI Codex"
- "Claude Code"

## Layout × Style Signals

- System composition suggests `structural-breakdown` or `bento-grid`.
- Process transformation suggests `linear-progression`.
- A GitHub technical audience suggests `technical-schematic`, `pop-laboratory`, or `ikea-manual`.
- Moderate complexity favors a landscape canvas with restrained on-image text.

## Design Instructions

- Keep English and Simplified Chinese README versions equivalent.
- Use Codex native ImageGen.
- No watermark.
- Store final assets under `docs/illustrations/`.
- Prefer reusable technical visuals over decorative scenes.

## Recommended Combinations

1. **structural-breakdown + technical-schematic**: Best expression of semantic and deterministic layers.
2. **bento-grid + pop-laboratory**: Strong feature overview with a modern open-source technical feel.
3. **linear-progression + ikea-manual**: Clearest process-first explanation with low visual noise.
