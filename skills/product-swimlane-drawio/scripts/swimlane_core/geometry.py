"""Pure coordinate, segment, and rectangle helpers for swimlane routing."""

from __future__ import annotations

from . import contracts


GEOMETRY_TOLERANCE = 0.75


def port_xy(side: str, offset: float) -> tuple[float, float]:
    if side == "top":
        return offset, 0.0
    if side == "bottom":
        return offset, 1.0
    if side == "left":
        return 0.0, offset
    if side == "right":
        return 1.0, offset
    raise contracts.DiagramError(f"Unsupported port side: {side}")


def port_point(bounds: dict[str, float], side: str, offset: float) -> tuple[float, float]:
    if side == "top":
        return bounds["left"] + bounds["width"] * offset, bounds["top"]
    if side == "bottom":
        return bounds["left"] + bounds["width"] * offset, bounds["bottom"]
    if side == "left":
        return bounds["left"], bounds["top"] + bounds["height"] * offset
    if side == "right":
        return bounds["right"], bounds["top"] + bounds["height"] * offset
    raise contracts.DiagramError(f"Unsupported port side: {side}")


def compact_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    compacted: list[tuple[float, float]] = []
    for point in points:
        if not compacted or point != compacted[-1]:
            compacted.append(point)
    return compacted


def remove_collinear_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove middle points that do not change an orthogonal path's direction."""
    simplified: list[tuple[float, float]] = []
    for point in compact_points(points):
        simplified.append(point)
        while len(simplified) >= 3:
            first, middle, last = simplified[-3:]
            same_x = (
                abs(first[0] - middle[0]) < GEOMETRY_TOLERANCE
                and abs(middle[0] - last[0]) < GEOMETRY_TOLERANCE
                and min(first[1], last[1]) - GEOMETRY_TOLERANCE
                <= middle[1]
                <= max(first[1], last[1]) + GEOMETRY_TOLERANCE
            )
            same_y = (
                abs(first[1] - middle[1]) < GEOMETRY_TOLERANCE
                and abs(middle[1] - last[1]) < GEOMETRY_TOLERANCE
                and min(first[0], last[0]) - GEOMETRY_TOLERANCE
                <= middle[0]
                <= max(first[0], last[0]) + GEOMETRY_TOLERANCE
            )
            if not (same_x or same_y):
                break
            simplified.pop(-2)
    return simplified


def segment_length(segment: tuple[tuple[float, float], tuple[float, float]]) -> float:
    return abs(segment[1][0] - segment[0][0]) + abs(segment[1][1] - segment[0][1])


def polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(segment_length(segment) for segment in zip(points, points[1:]))


def bend_count(points: list[tuple[float, float]]) -> int:
    axes = [segment_axis(segment) for segment in zip(points, points[1:])]
    axes = [axis for axis in axes if axis != "point"]
    return sum(first != second for first, second in zip(axes, axes[1:]))


def bounds_overlap(first: dict[str, float], second: dict[str, float], *, gap: float = 0.0) -> bool:
    return not (
        first["right"] + gap <= second["left"]
        or second["right"] + gap <= first["left"]
        or first["bottom"] + gap <= second["top"]
        or second["bottom"] + gap <= first["top"]
    )


def segment_axis(segment: tuple[tuple[float, float], tuple[float, float]]) -> str:
    (x1, y1), (x2, y2) = segment
    if abs(x1 - x2) < GEOMETRY_TOLERANCE:
        return "vertical"
    if abs(y1 - y2) < GEOMETRY_TOLERANCE:
        return "horizontal"
    return "diagonal"


def value_between(value: float, start: float, end: float, *, strict: bool = False) -> bool:
    low, high = sorted((start, end))
    margin = GEOMETRY_TOLERANCE if strict else -GEOMETRY_TOLERANCE
    return low + margin < value < high - margin if strict else low + margin <= value <= high - margin


def segment_crosses_bounds(segment: tuple[tuple[float, float], tuple[float, float]], bounds: dict[str, float]) -> bool:
    (x1, y1), (x2, y2) = segment
    axis = segment_axis(segment)
    if axis == "vertical":
        return (
            bounds["left"] + GEOMETRY_TOLERANCE < x1 < bounds["right"] - GEOMETRY_TOLERANCE
            and max(min(y1, y2), bounds["top"]) < min(max(y1, y2), bounds["bottom"])
        )
    if axis == "horizontal":
        return (
            bounds["top"] + GEOMETRY_TOLERANCE < y1 < bounds["bottom"] - GEOMETRY_TOLERANCE
            and max(min(x1, x2), bounds["left"]) < min(max(x1, x2), bounds["right"])
        )
    return False


def segment_intersects_box(segment: tuple[tuple[float, float], tuple[float, float]], box: dict[str, float], *, gap: float = 0.0) -> bool:
    expanded = {
        "left": box["left"] - gap,
        "right": box["right"] + gap,
        "top": box["top"] - gap,
        "bottom": box["bottom"] + gap,
    }
    (x1, y1), (x2, y2) = segment
    axis = segment_axis(segment)
    if axis == "horizontal":
        return expanded["top"] <= y1 <= expanded["bottom"] and max(
            min(x1, x2), expanded["left"]
        ) <= min(max(x1, x2), expanded["right"])
    if axis == "vertical":
        return expanded["left"] <= x1 <= expanded["right"] and max(
            min(y1, y2), expanded["top"]
        ) <= min(max(y1, y2), expanded["bottom"])
    return False


def segments_conflict(first: tuple[tuple[float, float], tuple[float, float]], second: tuple[tuple[float, float], tuple[float, float]]) -> bool:
    first_axis = segment_axis(first)
    second_axis = segment_axis(second)
    if "diagonal" in {first_axis, second_axis}:
        return False

    (ax1, ay1), (ax2, ay2) = first
    (bx1, by1), (bx2, by2) = second
    if first_axis != second_axis:
        vertical = first if first_axis == "vertical" else second
        horizontal = second if first_axis == "vertical" else first
        vx = vertical[0][0]
        hy = horizontal[0][1]
        return value_between(vx, horizontal[0][0], horizontal[1][0], strict=True) and value_between(
            hy, vertical[0][1], vertical[1][1], strict=True
        )

    if first_axis == "vertical" and abs(ax1 - bx1) < GEOMETRY_TOLERANCE:
        overlap = min(max(ay1, ay2), max(by1, by2)) - max(min(ay1, ay2), min(by1, by2))
        return overlap > GEOMETRY_TOLERANCE
    if first_axis == "horizontal" and abs(ay1 - by1) < GEOMETRY_TOLERANCE:
        overlap = min(max(ax1, ax2), max(bx1, bx2)) - max(min(ax1, ax2), min(bx1, bx2))
        return overlap > GEOMETRY_TOLERANCE
    return False
