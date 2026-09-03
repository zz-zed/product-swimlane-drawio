"""Pure edge-label sizing, candidate generation, and placement."""

from __future__ import annotations

import unicodedata

from . import geometry as core_geometry


EDGE_LABEL_FONT_SIZE = 11.0
EDGE_LABEL_HEIGHT = 18.0
EDGE_LABEL_PADDING = 8.0
EDGE_LABEL_VERTICAL_PADDING = 2.0
EDGE_LABEL_GAP = 5.0

def edge_label_size(label: str) -> tuple[float, float]:
    logical_lines = label.splitlines() or [""]
    widest_units = max(
        sum(
            2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
            for character in line
        )
        for line in logical_lines
    )
    width = min(190.0, max(28.0, widest_units * (EDGE_LABEL_FONT_SIZE * 0.54) + 12.0))
    height = max(EDGE_LABEL_HEIGHT, len(logical_lines) * 14.0 + 4.0)
    return width, height

def label_box_candidates(
    points: list[tuple[float, float]],
    label: str,
) -> list[tuple[int, dict[str, float], float]]:
    if not label.strip():
        return []
    width, height = edge_label_size(label)
    candidates: list[tuple[int, dict[str, float], float]] = []
    for index, segment in enumerate(zip(points, points[1:])):
        length = core_geometry.segment_length(segment)
        axis = core_geometry.segment_axis(segment)
        (x1, y1), (x2, y2) = segment
        if axis == "horizontal" and length >= width + EDGE_LABEL_PADDING:
            low_x, high_x = sorted((x1, x2))
            center_positions = (
                (low_x + high_x) / 2,
                low_x + width / 2 + EDGE_LABEL_GAP,
                low_x + width * 1.5 + EDGE_LABEL_GAP * 2,
                high_x - width / 2 - EDGE_LABEL_GAP,
                high_x - width * 1.5 - EDGE_LABEL_GAP * 2,
            )
            for center_x in dict.fromkeys(round(value, 4) for value in center_positions):
                if not low_x + width / 2 <= center_x <= high_x - width / 2:
                    continue
                for top in (y1 - height - EDGE_LABEL_GAP, y1 + EDGE_LABEL_GAP):
                    box = {
                        "left": center_x - width / 2,
                        "right": center_x + width / 2,
                        "top": top,
                        "bottom": top + height,
                        "width": width,
                        "height": height,
                    }
                    candidates.append((index, box, length + 1000.0))
        elif axis == "vertical" and length >= height + EDGE_LABEL_VERTICAL_PADDING:
            low_y, high_y = sorted((y1, y2))
            center_positions = (
                (low_y + high_y) / 2,
                low_y + height / 2 + EDGE_LABEL_GAP,
                low_y + height * 2 + EDGE_LABEL_GAP * 2,
                high_y - height / 2 - EDGE_LABEL_GAP,
                high_y - height * 2 - EDGE_LABEL_GAP * 2,
            )
            for center_y in dict.fromkeys(round(value, 4) for value in center_positions):
                if not low_y + height / 2 <= center_y <= high_y - height / 2:
                    continue
                for left in (x1 + EDGE_LABEL_GAP, x1 - width - EDGE_LABEL_GAP):
                    box = {
                        "left": left,
                        "right": left + width,
                        "top": center_y - height / 2,
                        "bottom": center_y + height / 2,
                        "width": width,
                        "height": height,
                    }
                    candidates.append((index, box, length))
    return sorted(candidates, key=lambda item: -item[2])

def choose_label_box(
    points: list[tuple[float, float]],
    label: str,
    node_boxes: list[dict[str, float]],
    other_segments: list[tuple[tuple[float, float], tuple[float, float]]],
    other_labels: list[dict[str, float]],
    preferred_side: str | None = None,
    container_bounds: dict[str, float] | None = None,
    prefer_source_proximity: bool = False,
) -> tuple[int, dict[str, float]] | None:
    candidates = label_box_candidates(points, label)

    def side_preference(item: tuple[int, dict[str, float], float]) -> int:
        if preferred_side not in {"left", "right"}:
            return 0
        segment_index, box, _ = item
        segment = list(zip(points, points[1:]))[segment_index]
        if core_geometry.segment_axis(segment) != "vertical":
            return 1
        box_center = box["left"] + box["width"] / 2
        is_preferred = (
            box_center < segment[0][0]
            if preferred_side == "left"
            else box_center > segment[0][0]
        )
        return 0 if is_preferred else 2

    if prefer_source_proximity and points:
        source_x, source_y = points[0]

        def source_distance(item: tuple[int, dict[str, float], float]) -> float:
            _, box, _ = item
            center_x = box["left"] + box["width"] / 2
            center_y = box["top"] + box["height"] / 2
            return (center_x - source_x) ** 2 + (center_y - source_y) ** 2

        candidates = sorted(
            candidates,
            key=lambda item: (source_distance(item), side_preference(item), -item[2]),
        )
    if preferred_side in {"left", "right"}:
        if not prefer_source_proximity:
            candidates = sorted(candidates, key=side_preference)
    for segment_index, box, _ in candidates:
        if container_bounds is not None and not (
            container_bounds["left"] <= box["left"]
            and box["right"] <= container_bounds["right"]
            and container_bounds["top"] <= box["top"]
            and box["bottom"] <= container_bounds["bottom"]
        ):
            continue
        if any(core_geometry.bounds_overlap(box, node_box, gap=2.0) for node_box in node_boxes):
            continue
        if any(core_geometry.bounds_overlap(box, other, gap=2.0) for other in other_labels):
            continue
        if any(core_geometry.segment_intersects_box(segment, box, gap=2.0) for segment in other_segments):
            continue
        own_segments = list(zip(points, points[1:]))
        if any(
            index != segment_index and core_geometry.segment_intersects_box(segment, box, gap=2.0)
            for index, segment in enumerate(own_segments)
        ):
            continue
        return segment_index, box
    return None

def polyline_midpoint(points: list[tuple[float, float]]) -> tuple[float, float]:
    total = core_geometry.polyline_length(points)
    if total <= core_geometry.GEOMETRY_TOLERANCE:
        return points[0] if points else (0.0, 0.0)
    remaining = total / 2
    for segment in zip(points, points[1:]):
        length = core_geometry.segment_length(segment)
        if remaining <= length:
            ratio = remaining / length if length else 0.0
            return (
                segment[0][0] + (segment[1][0] - segment[0][0]) * ratio,
                segment[0][1] + (segment[1][1] - segment[0][1]) * ratio,
            )
        remaining -= length
    return points[-1]
