# product-swimlane-drawio

[English](README.md) | [简体中文](README.zh-CN.md)

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-compatible-5B5BD6.svg)](https://agentskills.io/)

![product-swimlane-drawio overview](docs/product-swimlane-overview.png)

Create and incrementally update native, editable Draw.io vertical swimlane diagrams from natural language.

The skill focuses on process diagrams where each participant, role, or system owns a vertical lane. It combines structure confirmation, deterministic layout, semantic connector ports, stable IDs, incremental updates, and routing-quality validation.

## Why this skill

Directly generated Draw.io XML often produces tangled connectors, unclear return paths, and fragile edits. This skill uses a neutral JSON model and a deterministic local tool to make the result easier to review and continue editing.

- Native, uncompressed `.drawio` output
- Full-height vertical swimlanes
- Global top-to-bottom process ranks
- Decisions, branches, returns, retries, and cross-lane flows
- Semantic port allocation and orthogonal routing
- Stable IDs for incremental changes
- Strict structural and routing-quality checks
- No Draw.io dependency for generation
- Local editing in Draw.io Desktop or diagrams.net

## Workflow

```text
Natural-language process
        ↓
Confirm lanes, main path, branches, and assumptions
        ↓
Neutral JSON specification
        ↓
Build native .drawio
        ↓
Strict validation and visual inspection
        ↓
Edit locally or apply a semantic patch
```

## Supported agents

The package follows the Agent Skills directory format and targets:

- OpenAI Codex
- Claude Code
- Other Agent Skills-compatible tools on a best-effort basis

Agent-specific metadata is isolated under `agents/`; the workflow and Python tool do not depend on a single agent runtime.

## Requirements

- Python 3.10 or later
- An Agent Skills-compatible coding agent
- Optional: Draw.io Desktop or [diagrams.net](https://app.diagrams.net/) for visual editing and export

The bundled Python tool uses only the standard library.

## Install

Install globally for Codex and Claude Code with [`npx skills`](https://github.com/vercel-labs/skills):

```bash
npx skills add zz-zed/product-swimlane-drawio \
  --skill product-swimlane-drawio \
  -g \
  -a codex \
  -a claude-code
```

Inspect discovery before installing:

```bash
npx skills add zz-zed/product-swimlane-drawio --list
```

## Use with an agent

Ask the agent to confirm the structure before generating the file:

```text
Use product-swimlane-drawio to create an editable vertical swimlane diagram.
First confirm the lane order, main path, branches, returns, and assumptions.
Do not generate the file until I approve the structure.
```

For an existing compatible diagram:

```text
Use product-swimlane-drawio to update this .drawio file.
Preserve existing node geometry and manual layout changes.
Apply only the requested semantic changes, then validate and compare the result.
```

## Use the local tool directly

Build and validate:

```bash
python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  build --spec process.json --output process.drawio

python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  validate --input process.drawio --strict
```

Patch and compare:

```bash
python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  patch --input process.drawio --changes changes.json --output process-updated.drawio

python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  compare --before process.drawio --after process-updated.drawio --changes changes.json
```

The semantic input format is documented in [`references/schema.md`](skills/product-swimlane-drawio/references/schema.md).

## Incremental editing boundary

Safe patching depends on semantic metadata and stable IDs produced by this skill. A manually created or incompatible `.drawio` file may require migration or a controlled rebuild before semantic patching is reliable.

By default, patching preserves existing node geometry. Geometry updates require an explicit command-line flag and should only be used when movement or resizing was intentionally requested.

## Validation

Strict validation checks structural integrity and routing heuristics, including:

- Missing endpoints and duplicate semantic IDs
- Nodes outside their lanes
- Unintentional port reuse
- Connectors aligned with lane boundaries
- Connectors crossing nodes
- Overlapping, crossing, or non-orthogonal connector segments

Automated validation does not replace visual review. Inspect the rendered diagram for clipped labels, ambiguous arrow direction, hidden arrowheads, and excessive detours before sharing it.

## Design scope

Use this skill for editable vertical swimlane process diagrams. It is not intended to provide strict BPMN conformance, infrastructure topology, or free-form presentation graphics.

## Development

Run the neutral test suite:

```bash
python3 -m unittest discover -s tests -v
```

Verify local Skill discovery:

```bash
npx skills add . --list
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution requirements.

## Security and privacy

The skill runs local scripts with the permissions available to the invoking agent. Review the Skill instructions and scripts before installation.

The published package contains no user data, organization names, proprietary terminology, generated diagrams, or domain-specific sample flows. Keep task inputs and outputs outside the Skill directory.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

Released under the [MIT License](LICENSE).

Draw.io and diagrams.net are third-party products. This project is not affiliated with or endorsed by their maintainers.
