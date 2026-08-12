# product-swimlane-drawio

## Overview

Show how an Agent turns confirmed process semantics into a validated, native `.drawio` file that remains locally editable.

## Learning Objectives

The viewer will understand:

1. The project output and local editing benefit.
2. The deterministic generation pipeline.
3. The distinction between new builds and incremental patches.

---

## Section 1: Semantic input

**Content**:

- Natural-language process
- Confirm lanes, main path, branches, and assumptions
- Strict v2 JSON specification

**Visual Element**: An Agent conversation feeding a compact semantic document containing lanes, nodes, edges, and a main path.

**Text Labels**:

- English: "Confirm structure", "Semantic JSON"
- Chinese: "确认结构", "语义 JSON"

---

## Section 2: Deterministic local engine

**Content**:

- "Semantic port allocation and orthogonal routing"
- "Strict structural and routing-quality checks"
- "The bundled Python tool uses only the standard library."

**Visual Element**: A local engine with distinct layout, routing, and validation modules. Do not depict an MCP or cloud service.

**Text Labels**:

- English: "Layout", "Route", "Validate", "Local Python"
- Chinese: "布局", "路由", "校验", "本地 Python"

---

## Section 3: Editable output

**Content**:

- "Native, uncompressed `.drawio` output"
- "No Draw.io dependency for generation"
- "Local editing in Draw.io Desktop or diagrams.net"

**Visual Element**: A native file opening into a clean vertical swimlane diagram with visible selection handles to communicate editability.

**Text Labels**:

- English: "Native .drawio", "Edit locally"
- Chinese: "原生 .drawio", "本地编辑"

---

## Section 4: Two operating modes

**Content**:

- New: confirmed structure → build → validate → editable file
- Update: latest saved `.drawio` → inspect → semantic patch → validate and compare

**Visual Element**: Two parallel lanes sharing validation and editable output, with the update lane looping from the locally edited file.

**Text Labels**:

- English: "Create", "Update", "Preserve geometry"
- Chinese: "从零生成", "增量修改", "保留几何"

---

## Design Instructions

- Landscape, GitHub README friendly.
- High contrast at reduced display width.
- Minimal text; never use paragraphs inside the image.
- Consistent blue, cyan, white, slate, and a restrained amber accent.
- No watermark, logos, organization names, proprietary terminology, or business examples.
