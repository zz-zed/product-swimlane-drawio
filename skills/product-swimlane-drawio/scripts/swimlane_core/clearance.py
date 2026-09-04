"""Calibrated model-perimeter terminal-run checks for Draw.io connectors.

The runtime intentionally does not attempt to reproduce Draw.io's renderer.
It measures an unscaled model-space terminal run from the final effective turn
to a supported target shape's perimeter.  Marker length and renderer backoff
were used to calibrate the threshold, but are not subtracted a second time.
Unsupported rendering states remain explicit rather than becoming a pass.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Mapping, Sequence

from . import geometry


RULE_VERSION = "drawio-31.3.2-default-block-v1"
PROFILE_ID = "drawio-31.3.2-default-block-model-perimeter-v1"
CLEARANCE_THRESHOLD_PX = 16.0
STYLE_EPSILON = 1e-9
COORDINATE_TOLERANCE_PX = geometry.GEOMETRY_TOLERANCE

STATUS_COMPLETE = "complete"
STATUS_NOT_AVAILABLE = "not_available"
STATUS_NOT_APPLICABLE = "not_applicable"

Point = tuple[float, float]


@dataclass(frozen=True)
class ClearanceMeasurement:
    """A read-only result for one target arrowhead clearance measurement."""

    status: str
    rule_version: str = RULE_VERSION
    measurement_basis: str = "model_perimeter_terminal_run"
    profile_id: str | None = None
    terminal_run_px: float | None = None
    minimum_terminal_run_px: float | None = None
    geometry_tolerance_px: float | None = None
    violation: bool | None = None
    last_actual_turn: Point | None = None
    nominal_endpoint: Point | None = None
    model_attachment: Point | None = None
    terminal_axis: str | None = None
    target_shape: str | None = None
    reason: str | None = None

    @property
    def clearance_px(self) -> float | None:
        """Compatibility alias for callers that need the measured run length."""
        return self.terminal_run_px

    @property
    def threshold_px(self) -> float | None:
        """Compatibility alias for the calibrated model-space threshold."""
        return self.minimum_terminal_run_px

    @property
    def last_turn(self) -> Point | None:
        """Compatibility alias for the final effective turn."""
        return self.last_actual_turn

    @property
    def attachment(self) -> Point | None:
        """Compatibility alias for the model perimeter attachment point."""
        return self.model_attachment

    def to_dict(self) -> dict:
        result = asdict(self)
        # Keep the evidence self-describing for existing diagnostic consumers.
        result.update(
            {
                "clearance_px": self.clearance_px,
                "threshold_px": self.threshold_px,
                "last_turn": self.last_turn,
                "attachment": self.attachment,
            }
        )
        return result


def _measurement(status: str, **values) -> ClearanceMeasurement:
    return ClearanceMeasurement(status=status, **values)


def _style_parts(style: str) -> tuple[set[str], dict[str, str]]:
    flags: set[str] = set()
    values: dict[str, str] = {}
    for raw_part in style.split(";"):
        part = raw_part.strip()
        if not part:
            continue
        if "=" not in part:
            flags.add(part)
            continue
        key, value = part.split("=", 1)
        values[key.strip()] = value.strip()
    return flags, values


def _numeric_style_value(values: Mapping[str, str], key: str, default: float) -> float | None:
    raw = values.get(key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _matches_number(values: Mapping[str, str], key: str, expected: float) -> bool:
    value = _numeric_style_value(values, key, expected)
    return value is not None and abs(value - expected) <= STYLE_EPSILON


def _supported_edge_style(edge_style: str) -> tuple[str, str | None]:
    _, values = _style_parts(edge_style)
    arrow = values.get("endArrow")
    if arrow == "none":
        return STATUS_NOT_APPLICABLE, "no_end_arrow"
    if arrow != "block":
        return STATUS_NOT_AVAILABLE, "unsupported_end_arrow"
    if not _matches_number(values, "endFill", 1.0):
        return STATUS_NOT_AVAILABLE, "unsupported_end_fill"
    if not _matches_number(values, "endSize", 6.0):
        return STATUS_NOT_AVAILABLE, "unsupported_end_size"
    if not _matches_number(values, "strokeWidth", 1.0):
        return STATUS_NOT_AVAILABLE, "unsupported_edge_stroke_width"
    if values.get("edgeStyle") != "orthogonalEdgeStyle":
        return STATUS_NOT_AVAILABLE, "unsupported_edge_style"
    if values.get("jettySize") != "auto":
        return STATUS_NOT_AVAILABLE, "unsupported_jetty_size"
    if values.get("rounded", "0") not in {"0", "false"}:
        return STATUS_NOT_AVAILABLE, "unsupported_rounded_edge"
    if values.get("curved", "0") not in {"0", "false"}:
        return STATUS_NOT_AVAILABLE, "unsupported_curved_edge"
    return STATUS_COMPLETE, None


def _supported_target_shape(target_type: str, target_style: str) -> tuple[str | None, str | None]:
    if target_type not in {"process", "decision", "end"}:
        return None, "unsupported_target_type"
    flags, values = _style_parts(target_style)
    rotation = _numeric_style_value(values, "rotation", 0.0)
    if rotation is None or abs(rotation) > STYLE_EPSILON:
        return None, "unsupported_target_rotation"
    if "perimeter" in values:
        return None, "unsupported_custom_perimeter"

    expected_stroke = 1.5 if target_type == "end" else 1.0
    if not _matches_number(values, "strokeWidth", expected_stroke):
        return None, "unsupported_target_stroke_width"

    shape_value = values.get("shape")
    if target_type == "process":
        if shape_value is not None or flags & {"ellipse", "rhombus"}:
            return None, "target_shape_mismatch"
        if values.get("rounded", "0") not in {"0", "false"}:
            return None, "unsupported_process_shape"
        return "rectangle", None
    if target_type == "decision":
        if "rhombus" not in flags or shape_value is not None:
            return None, "target_shape_mismatch"
        return "diamond", None
    if "ellipse" not in flags or shape_value is not None or values.get("aspect") != "fixed":
        return None, "target_shape_mismatch"
    return "ellipse", None


def _normalized_bounds(bounds: Mapping[str, float]) -> dict[str, float] | None:
    try:
        left = float(bounds["left"])
        right = float(bounds["right"])
        top = float(bounds["top"])
        bottom = float(bounds["bottom"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (left, right, top, bottom)):
        return None
    if right <= left or bottom <= top:
        return None
    return {"left": left, "right": right, "top": top, "bottom": bottom}


def _axis(first: Point, second: Point) -> str:
    delta_x = second[0] - first[0]
    delta_y = second[1] - first[1]
    if abs(delta_x) <= STYLE_EPSILON and abs(delta_y) <= STYLE_EPSILON:
        return "point"
    if abs(delta_x) < COORDINATE_TOLERANCE_PX:
        return "vertical"
    if abs(delta_y) < COORDINATE_TOLERANCE_PX:
        return "horizontal"
    return "diagonal"


def _is_monotonic_collinear(first: Point, middle: Point, last: Point) -> bool:
    axis_first = _axis(first, middle)
    axis_second = _axis(middle, last)
    if axis_first not in {"horizontal", "vertical"} or axis_first != axis_second:
        return False
    if axis_first == "horizontal":
        return (middle[0] - first[0]) * (last[0] - middle[0]) > STYLE_EPSILON
    return (middle[1] - first[1]) * (last[1] - middle[1]) > STYLE_EPSILON


def _effective_points(points: Sequence[Sequence[float]]) -> list[Point] | None:
    """Normalize only a calculation copy; preserve every non-zero turn."""
    result: list[Point] = []
    try:
        for raw in points:
            point = (float(raw[0]), float(raw[1]))
            if not all(math.isfinite(value) for value in point):
                return None
            if result and point == result[-1]:
                continue
            result.append(point)
    except (IndexError, TypeError, ValueError):
        return None

    simplified: list[Point] = []
    for point in result:
        simplified.append(point)
        while len(simplified) >= 3 and _is_monotonic_collinear(*simplified[-3:]):
            simplified.pop(-2)
    return simplified


def _nominal_endpoint_is_attached(
    endpoint: Point,
    axis: str,
    direction: float,
    bounds: Mapping[str, float],
) -> bool:
    if axis == "horizontal":
        expected = bounds["left"] if direction > 0 else bounds["right"]
        actual = endpoint[0]
    else:
        expected = bounds["top"] if direction > 0 else bounds["bottom"]
        actual = endpoint[1]
    return abs(actual - expected) <= geometry.GEOMETRY_TOLERANCE


def _shape_attachment(
    shape: str,
    endpoint: Point,
    axis: str,
    direction: float,
    bounds: Mapping[str, float],
) -> Point | None:
    left, right = bounds["left"], bounds["right"]
    top, bottom = bounds["top"], bounds["bottom"]
    center_x, center_y = (left + right) / 2.0, (top + bottom) / 2.0
    half_width, half_height = (right - left) / 2.0, (bottom - top) / 2.0

    if shape == "rectangle":
        return (
            (left if direction > 0 else right, endpoint[1])
            if axis == "horizontal"
            else (endpoint[0], top if direction > 0 else bottom)
        )

    if axis == "horizontal":
        ratio = abs(endpoint[1] - center_y) / half_height
        if ratio > 1.0 + COORDINATE_TOLERANCE_PX / half_height:
            return None
        if shape == "diamond":
            extent = half_width * (1.0 - min(ratio, 1.0))
        else:
            extent = half_width * math.sqrt(max(0.0, 1.0 - ratio * ratio))
        return (center_x - extent if direction > 0 else center_x + extent, endpoint[1])

    ratio = abs(endpoint[0] - center_x) / half_width
    if ratio > 1.0 + COORDINATE_TOLERANCE_PX / half_width:
        return None
    if shape == "diamond":
        extent = half_height * (1.0 - min(ratio, 1.0))
    else:
        extent = half_height * math.sqrt(max(0.0, 1.0 - ratio * ratio))
    return (endpoint[0], center_y - extent if direction > 0 else center_y + extent)


def measure_arrowhead_clearance(
    points: Sequence[Sequence[float]],
    *,
    target_bounds: Mapping[str, float],
    target_type: str,
    target_style: str,
    edge_style: str,
) -> ClearanceMeasurement:
    """Measure the final effective model-space segment without mutating points.

    ``points`` includes source and nominal target endpoints.  The endpoint is a
    bounding-box port in Draw.io XML, so supported diamond and ellipse targets
    are projected to their true model perimeter before the terminal run is
    measured.  Renderer marker/backoff behavior is already represented by the
    calibrated threshold and is deliberately not subtracted here.
    """
    edge_status, edge_reason = _supported_edge_style(edge_style)
    if edge_status != STATUS_COMPLETE:
        return _measurement(edge_status, reason=edge_reason)

    shape, shape_reason = _supported_target_shape(target_type, target_style)
    if shape is None:
        return _measurement(STATUS_NOT_AVAILABLE, reason=shape_reason)

    bounds = _normalized_bounds(target_bounds)
    if bounds is None:
        return _measurement(STATUS_NOT_AVAILABLE, target_shape=shape, reason="invalid_target_bounds")
    effective_points = _effective_points(points)
    if effective_points is None or len(effective_points) < 2:
        return _measurement(STATUS_NOT_AVAILABLE, target_shape=shape, reason="insufficient_path")

    last_turn, nominal_endpoint = effective_points[-2:]
    axis = _axis(last_turn, nominal_endpoint)
    if axis not in {"horizontal", "vertical"}:
        return _measurement(STATUS_NOT_AVAILABLE, target_shape=shape, reason="unsupported_terminal_segment")
    direction = (
        nominal_endpoint[0] - last_turn[0]
        if axis == "horizontal"
        else nominal_endpoint[1] - last_turn[1]
    )
    if not _nominal_endpoint_is_attached(nominal_endpoint, axis, direction, bounds):
        return _measurement(STATUS_NOT_AVAILABLE, target_shape=shape, reason="floating_or_unattached_target")

    attachment = _shape_attachment(shape, nominal_endpoint, axis, direction, bounds)
    if attachment is None:
        return _measurement(STATUS_NOT_AVAILABLE, target_shape=shape, reason="perimeter_intersection_unavailable")
    # The path axis already tolerates sub-pixel coordinate drift.  Measure the
    # run on that axis so the tolerated perpendicular drift is not counted as
    # additional arrowhead clearance.
    terminal_run = (
        abs(attachment[0] - last_turn[0])
        if axis == "horizontal"
        else abs(attachment[1] - last_turn[1])
    )
    attachment_direction = (
        attachment[0] - last_turn[0]
        if axis == "horizontal"
        else attachment[1] - last_turn[1]
    )
    if attachment_direction * direction <= STYLE_EPSILON:
        return _measurement(STATUS_NOT_AVAILABLE, target_shape=shape, reason="attachment_not_forward")

    return _measurement(
        STATUS_COMPLETE,
        profile_id=PROFILE_ID,
        terminal_run_px=terminal_run,
        minimum_terminal_run_px=CLEARANCE_THRESHOLD_PX,
        geometry_tolerance_px=geometry.GEOMETRY_TOLERANCE,
        violation=(
            terminal_run
            < CLEARANCE_THRESHOLD_PX - COORDINATE_TOLERANCE_PX
        ),
        last_actual_turn=last_turn,
        nominal_endpoint=nominal_endpoint,
        model_attachment=attachment,
        terminal_axis=axis,
        target_shape=shape,
    )
