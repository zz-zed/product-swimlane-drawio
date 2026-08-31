# Benchmark Plan

## Purpose

The benchmark suite distinguishes structural correctness, deterministic geometry, visual readability, and round-trip stability. Passing XML validation alone is not a quality result.

## Test sets

### Public synthetic fixtures

Public tests use fictional actors, systems, and labels. They contain no user data, organization names, or copied business flows.

The first v3 fixture covers:

- Two vertical lanes.
- One fork into two lane-local slots.
- One join back to the main slot.
- A note anchored beside its related process.
- A native editable Draw.io output.

Later fixtures should cover request/response/retry, approval loops, multiple outcomes, exception ends, phases, and long-form spacing.

### Private reference suite

Real approved diagrams remain outside the public Skill and repository history. They are used only as acceptance references for relative layout behavior and must not be copied into examples, tests, documentation, prompts, or generated assets.

The first anonymous baseline contains six single-page references: 23 lanes, 123 visible nodes, and 116 connectors. Every page contains parallel rank bands, four contain lane-local parallelism, and the set contains both same-rank exchanges and historical returns. All connectors use orthogonal styles. Median rank gaps range from 70 to 120 px; median lane widths range from 190 to 325 px. These statistics support profile-aware spacing, lane-local slots, phase navigation, and separate long-return corridors without exposing labels or business topology.

Run the neutral analyzer against references kept outside the repository:

```bash
python3 tools/reference_benchmark.py "<reference.drawio>" --output "<private-report.json>"
```

The report contains file names and anonymous geometry counts only. It never serializes cell labels, diagram titles, or edge text.

## Objective gates

- Deterministic rebuild produces identical Draw.io bytes.
- Schema validation rejects unknown or incompatible fields.
- Strict validation has zero errors and zero warnings.
- No node-node overlap or lane-capacity violation.
- No automatic short segment, hairpin, route overlap, or unrelated-lane intrusion.
- Every label has a clear carrier.
- Inspect recovers v3 behavior, slot, group, anchor, flow-role, and outcome metadata.
- v1 and v2 fixtures remain compatible.
- Patch preserves unrelated geometry and manual waypoints.
- Long cross-lane retries leave from the source's outer side, travel in a clear outer corridor, and enter through the target lane's internal gutter.
- A phase rail reserves its own column and does not make business lanes transparent.
- A lane-wide main axis shifts when one-sided notes need space, keeping main-path nodes aligned without symmetric blank expansion.

## Visual gate

Preview review is recorded separately from strict validation. A multimodal review may identify ambiguity or poor balance that deterministic checks do not yet cover, but it never replaces deterministic gates or final human review for important diagrams.

## Change policy

A new layout rule should be supported by more than one failure class or by a clear hard constraint. One real diagram must not create a domain-specific exception in the public engine.
