---
illustration_id: 03
type: comparison
style: blueprint
language: en
aspect: 16:9
---

Use case: productivity-visual
Asset type: shared inline GitHub README technical illustration
Primary request: Explain the two safe operating modes of an editable Draw.io swimlane Agent Skill.

LAYOUT: landscape 16:9, two equal horizontal blueprint pipelines stacked vertically with a clear divider. The upper pipeline is new creation; the lower pipeline is incremental update. Both terminate in the same native editable file symbol. Use generous whitespace.

ZONES:
- TOP PIPELINE, exact lane title "CREATE": semantic JSON document → module labeled "BUILD" → shield labeled "VALIDATE" → native file labeled ".DRAWIO".
- BOTTOM PIPELINE, exact lane title "UPDATE": existing native file labeled ".DRAWIO" → magnifier labeled "INSPECT" → patch document labeled "PATCH" → paired checks labeled "VALIDATE + COMPARE" → updated native file labeled ".DRAWIO".
- A thin feedback arrow returns from the updated file to the beginning of UPDATE, with exact small label "LATEST SAVED FILE".
- Bottom centered exact takeaway label "MANUAL GEOMETRY PRESERVED".

COLORS: off-white engineering paper, faint gray grid, deep slate text and outlines, engineering blue for CREATE, navy for UPDATE, pale blue fills, one restrained amber highlight on PATCH. Color values and color names are rendering guidance only; never display them.

STYLE: precise blueprint diagram, crisp vector geometry, consistent thin line weights, straight and 90-degree arrows only, compact technical icons, no people, no scenes, no 3D, no gradients, no decorative flourishes.

TEXT: Render only the exact uppercase labels specified above. Keep text large and sparse. No extra explanations, no placeholder text, no watermark, no logos, no garbled characters.

CONSTRAINTS: The visual must communicate preservation rather than full regeneration. Do not depict cloud services or MCP components.

ASPECT: 16:9 landscape, medium-density technical illustration.
