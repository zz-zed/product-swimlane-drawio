"""Pure node text measurement and sizing policy."""

from __future__ import annotations

import unicodedata

from . import contracts, geometry as core_geometry


NODE_SIZES = {
    "start": (36, 36), "end": (36, 36), "process": (132, 42),
    "decision": (96, 72), "note": (138, 52),
}
FIXED_ASPECT_NODE_TYPES = {"start", "end"}
LABELED_FIXED_NODE_MIN_SIZE = 48.0
PROCESS_TEXT_LINE_HEIGHT = 14.0
PROCESS_VERTICAL_PADDING = 10.0
MAX_AUTOMATIC_PROCESS_HEIGHT = 66.0

def estimated_text_lines(text: str, width: float, *, diamond: bool = False) -> int:
    usable_width = max(12.0, width - (28.0 if diamond else 16.0))
    capacity = max(1, int(usable_width / 7.0))
    lines = 0
    for logical_line in (text.splitlines() or [""]):
        units = sum(
            2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
            for character in logical_line
        )
        lines += max(1, (units + capacity - 1) // capacity)
    return lines

def recommended_process_height(label: str, width: float) -> float:
    if not label:
        return float(NODE_SIZES["process"][1])
    lines = estimated_text_lines(label, width)
    return min(
        MAX_AUTOMATIC_PROCESS_HEIGHT,
        max(
            float(NODE_SIZES["process"][1]),
            PROCESS_VERTICAL_PADDING + PROCESS_TEXT_LINE_HEIGHT * lines,
        ),
    )

def node_size(node: dict) -> tuple[float, float]:
    kind = node.get("type", "process")
    if kind not in NODE_SIZES:
        raise contracts.DiagramError(f"Unsupported node type: {kind}")
    default_width, default_height = NODE_SIZES[kind]
    explicit_width = node.get("width")
    explicit_height = node.get("height")
    if kind in FIXED_ASPECT_NODE_TYPES:
        if explicit_width is not None and explicit_height is not None:
            if abs(float(explicit_width) - float(explicit_height)) >= core_geometry.GEOMETRY_TOLERANCE:
                raise contracts.DiagramError(
                    f"Fixed-aspect node {node.get('id', '<unknown>')} requires equal width and height",
                    code="geometry/fixed-aspect-ratio",
                    subject={"kind": "node", "id": node.get("id")},
                    evidence={"width": explicit_width, "height": explicit_height},
                    supported_fixes=["set-equal-width-and-height", "remove-one-size-dimension"],
                )
            diameter = float(explicit_width)
        elif explicit_width is not None:
            diameter = float(explicit_width)
        elif explicit_height is not None:
            diameter = float(explicit_height)
        else:
            diameter = float(default_width)
        if str(node.get("label", "")).strip():
            diameter = max(diameter, LABELED_FIXED_NODE_MIN_SIZE)
        return diameter, diameter

    if kind == "decision" and explicit_width is None:
        label_units = sum(
            2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
            for character in str(node.get("label", ""))
            if character != "\n"
        )
        width = min(168.0, max(float(default_width), 8.0 + label_units * 4.0))
    else:
        width = float(explicit_width if explicit_width is not None else default_width)
    if explicit_height is not None:
        height = float(explicit_height)
    elif kind == "process":
        height = recommended_process_height(str(node.get("label", "")), width)
    elif kind == "decision":
        lines = estimated_text_lines(str(node.get("label", "")), width, diamond=True)
        height = max(float(default_height), 16.0 + 16.0 * lines)
    else:
        height = float(default_height)
    return width, height
