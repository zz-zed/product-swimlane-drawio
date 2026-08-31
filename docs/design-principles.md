# Design principles

## 1. Confirm semantics before drawing

The user should approve the lane order, main path, branches, returns, and assumptions before a file is generated. Unknown business facts remain unresolved; the agent must not invent hidden steps or owners.

## 2. Keep language reasoning out of geometry

Language models identify meaning and topology. A deterministic engine handles coordinates, spacing, ports, routing, label carriers, and XML serialization.

## 3. Make the normal path visually dominant

The confirmed main path is routed first. Same-lane progress prefers a direct top-to-bottom connection. Returns and retries use separate channels where space permits.

## 4. Deliver an editable source, not only a preview

The primary artifact is a native `.drawio` file. PNG or other exports are review and sharing formats, not replacements for the editable source.

## 5. Treat local editing as a first-class workflow

The next revision starts from the latest user-saved Draw.io file. Stable IDs and semantic metadata support inspection, targeted patches, and preservation of compatible manual geometry.

## 6. Change only what was requested

Patches contain declared additions, updates, and deletions. Geometry changes require explicit authorization. Unrelated nodes, connectors, phases, and manual waypoints remain unchanged whenever compatibility allows.

## 7. Prefer explicit failure over silent damage

Unknown schema fields, unsafe deletions, invalid main paths, output replacement, and unsupported files produce clear diagnostics instead of best-effort mutation.

## 8. Separate deterministic checks from visual judgment

Strict validation catches defined structural and routing problems. Preview export proves only that rendering succeeded. A multimodal review may find additional visual issues but is non-deterministic and does not replace human review for important diagrams.

## 9. Stay narrow

This project optimizes editable product and business vertical swimlanes. It does not attempt to become a general-purpose generator for BPMN, UML, C4, ERD, network topology, or presentation graphics.

## 10. Keep the public Skill neutral and portable

The Skill package contains no user data, organization-specific terminology, domain-specific sample flow, generated diagram, or dependency on a single agent runtime. Public examples live outside the Skill directory and use fictional, neutral content.
