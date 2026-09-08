"""Routing decisions over plain node/lane views and explicit per-operation state."""

from __future__ import annotations

from dataclasses import replace

from . import clearance, contracts, geometry as core_geometry, labels, port_planner, ports, routing_policy


ROUTE_BEND_PENALTY = 32.0
ROUTE_CONFLICT_PENALTY = 4000.0
ROUTE_LABEL_CONFLICT_PENALTY = 2500.0
POOL_EDGE_MARGIN = 8.0

ROUTE_COMPLETE = "complete"
ROUTE_FAILED = "failed"


class RouteDecision:
    """A pure route result for one already-selected port pair."""

    __slots__ = ("edge_id", "assignment", "routed")

    def __init__(self, edge_id: str, assignment, routed: dict) -> None:
        self.edge_id = edge_id
        self.assignment = assignment
        self.routed = routed


class RouteFailure:
    """Structured feasibility feedback; it never changes caller-owned state."""

    __slots__ = (
        "code", "edge_id", "message", "locked", "component_key",
        "assignment_key", "suggested_offsets", "evidence", "supported_fixes",
    )

    def __init__(
        self,
        code: str,
        edge_id: str | None,
        message: str,
        *,
        locked: bool = False,
        component_key=None,
        assignment_key=None,
        suggested_offsets: tuple[float, float] | None = None,
        evidence: dict | None = None,
        supported_fixes=(),
    ) -> None:
        self.code = code
        self.edge_id = edge_id
        self.message = message
        self.locked = locked
        self.component_key = component_key
        self.assignment_key = assignment_key
        self.suggested_offsets = suggested_offsets
        self.evidence = dict(evidence or {})
        self.supported_fixes = tuple(supported_fixes)


class RouteSearchBudget:
    """Finite routing feedback limits, independent from port-search budgets."""

    __slots__ = ("max_component_replans", "max_batch_replays", "max_path_candidates",
                 "max_candidate_evaluations", "max_label_pairs", "max_batch_label_pairs")

    def __init__(self, max_component_replans: int = 6, max_batch_replays: int = 64,
                 max_path_candidates: int = 128, max_candidate_evaluations: int = 8192,
                 max_label_pairs: int = 32, max_batch_label_pairs: int = 128) -> None:
        component_replans = int(max_component_replans)
        batch_replays = int(max_batch_replays)
        if component_replans <= 0 or batch_replays <= 0:
            raise contracts.DiagramError("Route search budgets must be positive")
        self.max_component_replans = component_replans
        self.max_batch_replays = batch_replays
        self.max_path_candidates = int(max_path_candidates)
        self.max_candidate_evaluations = int(max_candidate_evaluations)
        self.max_label_pairs = int(max_label_pairs)
        self.max_batch_label_pairs = int(max_batch_label_pairs)
        if min(self.max_path_candidates, self.max_candidate_evaluations,
               self.max_label_pairs, self.max_batch_label_pairs) <= 0:
            raise contracts.DiagramError("Route search budgets must be positive")


class BatchRouteResult:
    """Pure, all-or-nothing result of one bounded port/route planning batch."""

    __slots__ = (
        "status", "decisions", "failure", "port_plan", "batch_replays",
        "component_replans", "routing_order", "linked_edge_pairs",
        "label_choices", "planning",
    )

    def __init__(
        self,
        status: str,
        *,
        decisions=(),
        failure: RouteFailure | None = None,
        port_plan=None,
        batch_replays: int = 0,
        component_replans=None,
        routing_order=(),
        linked_edge_pairs=(),
        label_choices=None, planning=None,
    ) -> None:
        self.status = status
        self.decisions = tuple(decisions)
        self.failure = failure
        self.port_plan = port_plan
        self.batch_replays = int(batch_replays)
        self.component_replans = dict(component_replans or {})
        self.routing_order = tuple(routing_order)
        self.linked_edge_pairs = tuple(linked_edge_pairs)
        self.label_choices = dict(label_choices or {})
        self.planning = dict(planning or {})


def inferred_spec_route_class(edge: dict, nodes: dict[str, dict]) -> str:
    requested = edge.get("route", "auto")
    if requested != "auto":
        return requested
    def rank(node: dict) -> int:
        if "rank" in node:
            return int(node["rank"])
        return int(node["semantic"].get("rank", "0"))

    source_rank = rank(nodes[edge["from"]])
    target_rank = rank(nodes[edge["to"]])
    if edge.get("type") == "retry" or target_rank < source_rank:
        return "back"
    if target_rank > source_rank:
        return "forward"
    return "side"


def infer_route_class(edge: dict, source: dict, target: dict) -> str:
    requested = edge.get("route", "auto")
    if requested not in routing_policy.ROUTE_CLASSES:
        raise contracts.DiagramError(f"Unsupported route class: {requested}")
    if requested != "auto":
        return requested
    source_rank = int(source["semantic"].get("rank", "0"))
    target_rank = int(target["semantic"].get("rank", "0"))
    if edge.get("type") == "retry" or target_rank < source_rank:
        return "back"
    if target_rank > source_rank:
        return "forward"
    return "side"


def preferred_sides(
    edge: dict,
    route_class: str,
    source: dict,
    target: dict,
    lanes: dict[str, dict],
    *,
    main_path_pairs: set[tuple[str, str]] | None = None,
    outgoing_counts: dict[str, int] | None = None,
    bottom_reserved_sources: set[str] | None = None,
    v3_semantics: bool = False,
) -> tuple[str, str]:
    branch = edge.get("branch")
    if branch is not None and branch not in routing_policy.BRANCH_CLASSES:
        raise contracts.DiagramError(f"Unsupported branch class: {branch}")
    source_type = source["semantic"].get("type", "process")
    target_type = target["semantic"].get("type", "process")
    source_rank = int(source["semantic"].get("rank", "0"))
    target_rank = int(target["semantic"].get("rank", "0"))
    is_main_path = (edge["from"], edge["to"]) in (main_path_pairs or set())
    same_lane_down = source["lane"] == target["lane"] and target_rank > source_rank
    actual_split = (outgoing_counts or {}).get(edge["from"], 0) > 1

    if route_class == "back":
        source_index = list(lanes).index(source["lane"])
        target_index = list(lanes).index(target["lane"])
        if edge.get("flow_role") and abs(source_index - target_index) == 1:
            if source_index < target_index:
                default_exit, default_entry = "right", "right"
            else:
                default_exit, default_entry = "left", "left"
        elif edge.get("flow_role") and source_index > target_index:
            default_exit, default_entry = "right", "left"
        elif edge.get("flow_role") and source_index < target_index:
            default_exit, default_entry = "left", "right"
        else:
            default_exit = "left"
            default_entry = "left"
        if (
            default_exit == default_entry
            and (source["lane"] == target["lane"] or not edge.get("flow_role"))
            and edge.get("exit_side") is None
            and edge.get("entry_side") is None
        ):
            target_lane = lanes[target["lane"]]
            target_bounds = core_geometry.node_bounds_in_pool(target, target_lane)
            lane_bounds = target_lane["geometry"]
            left_gutter = target_bounds["left"] - lane_bounds["x"]
            right_gutter = (
                lane_bounds["x"] + lane_bounds["width"] - target_bounds["right"]
            )
            required_gutter = (
                routing_policy.LANE_BOUNDARY_CLEARANCE
                + clearance.CLEARANCE_THRESHOLD_PX
                + core_geometry.GEOMETRY_TOLERANCE
            )
            left_available = left_gutter + core_geometry.GEOMETRY_TOLERANCE >= required_gutter
            right_available = right_gutter + core_geometry.GEOMETRY_TOLERANCE >= required_gutter
            if right_available and not left_available:
                default_exit = default_entry = "right"
            elif left_available and not right_available:
                default_exit = default_entry = "left"
            elif (
                left_available
                and right_available
                and len(lanes) > 1
                and source.get("semantic", {}).get("type", source.get("type"))
                != "decision"
            ):
                # A same-lane return in an outer lane should use the canvas-
                # facing gutter.  The inward gutter is where cross-lane main
                # carriers terminate and is therefore predictably ambiguous.
                # Decision retry loops retain their established left-side
                # branch convention when both gutters are equally usable.
                default_exit = default_entry = (
                    "right" if target_index >= len(lanes) / 2 else "left"
                )
    elif route_class == "forward":
        if (
            is_main_path
            and (
                same_lane_down
                or (
                    v3_semantics
                    and target_rank > source_rank
                    and edge["from"] not in (bottom_reserved_sources or set())
                )
            )
        ):
            default_exit = "bottom"
        elif (
            v3_semantics
            and source_type == "decision"
            and same_lane_down
            and (target_type == "end" or edge["from"] not in (bottom_reserved_sources or set()))
        ):
            default_exit = "bottom"
        elif source_type == "decision" and branch == "positive" and source["lane"] != target["lane"]:
            source_index = list(lanes).index(source["lane"])
            target_index = list(lanes).index(target["lane"])
            default_exit = "right" if target_index > source_index else "left"
        elif source_type == "decision" and branch == "positive" and actual_split:
            default_exit = "right"
        elif source_type == "decision" and branch == "negative":
            source_index = list(lanes).index(source["lane"])
            target_index = list(lanes).index(target["lane"])
            default_exit = "right" if target_index > source_index else "left"
        else:
            default_exit = "bottom"
        default_entry = "top"
    else:
        source_index = list(lanes).index(source["lane"])
        target_index = list(lanes).index(target["lane"])
        if target_index >= source_index:
            default_exit, default_entry = "right", "left"
        else:
            default_exit, default_entry = "left", "right"

    exit_side = ports.validate_side(edge.get("exit_side", default_exit), "exit_side")
    entry_side = ports.validate_side(edge.get("entry_side", default_entry), "entry_side")
    return exit_side, entry_side


def normalize_waypoints(values) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in values or []:
        if isinstance(item, dict):
            if "x" not in item or "y" not in item:
                raise contracts.DiagramError("Every waypoint object must contain x and y")
            x, y = item["x"], item["y"]
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            x, y = item
        else:
            raise contracts.DiagramError("Waypoints must be {x, y} objects or [x, y] pairs")
        points.append((float(x), float(y)))
    return points


def internal_lane_boundaries(lanes: dict[str, dict]) -> list[float]:
    right_edges = {
        round(record["geometry"]["x"] + record["geometry"]["width"], 4)
        for record in lanes.values()
    }
    if not right_edges:
        return []
    pool_right = max(right_edges)
    return sorted(edge for edge in right_edges if edge < pool_right - core_geometry.GEOMETRY_TOLERANCE)


def safe_vertical_corridor(
    candidate: float,
    boundaries: list[float],
    direction: str,
    pool_width: float,
) -> float:
    """Move an automatic vertical corridor away from internal lane boundaries."""
    if direction not in {"left", "right"}:
        raise contracts.DiagramError(f"Unsupported corridor direction: {direction}")

    lower = POOL_EDGE_MARGIN
    upper = max(lower, pool_width - POOL_EDGE_MARGIN)
    candidate = min(max(candidate, lower), upper)
    safe_gap = routing_policy.LANE_BOUNDARY_CLEARANCE + core_geometry.GEOMETRY_TOLERANCE

    for _ in range(len(boundaries) + 1):
        conflict = next(
            (
                boundary
                for boundary in boundaries
                if abs(candidate - boundary) < routing_policy.LANE_BOUNDARY_CLEARANCE
            ),
            None,
        )
        if conflict is None:
            break
        shifted = conflict - safe_gap if direction == "left" else conflict + safe_gap
        shifted = min(max(shifted, lower), upper)
        if abs(shifted - candidate) < core_geometry.GEOMETRY_TOLERANCE:
            break
        candidate = shifted

    return candidate


def automatic_waypoints(
    route_class: str,
    source_bounds: dict[str, float],
    target_bounds: dict[str, float],
    source_point: tuple[float, float],
    target_point: tuple[float, float],
    exit_side: str,
    entry_side: str,
    pool_width: float,
    lane_boundaries: list[float],
) -> list[tuple[float, float]]:
    sx, sy = source_point
    tx, ty = target_point

    if route_class == "forward":
        if exit_side == "bottom" and entry_side == "top" and abs(sx - tx) < core_geometry.GEOMETRY_TOLERANCE:
            return []
        corridor_y = (sy + ty) / 2
        if exit_side == "bottom":
            return core_geometry.compact_points([(sx, corridor_y), (tx, corridor_y)])
        if exit_side in {"left", "right"}:
            escape_x = sx + (routing_policy.ROUTE_CLEARANCE if exit_side == "right" else -routing_policy.ROUTE_CLEARANCE)
            escape_x = safe_vertical_corridor(
                escape_x, lane_boundaries, exit_side, pool_width
            )
            return core_geometry.compact_points([(escape_x, sy), (escape_x, corridor_y), (tx, corridor_y)])
        return core_geometry.compact_points([(sx, corridor_y), (tx, corridor_y)])

    if route_class == "back":
        if exit_side == entry_side == "left":
            route_x = safe_vertical_corridor(
                min(source_bounds["left"], target_bounds["left"]) - routing_policy.ROUTE_CLEARANCE,
                lane_boundaries,
                "left",
                pool_width,
            )
            return core_geometry.compact_points([(route_x, sy), (route_x, ty)])
        if exit_side == entry_side == "right":
            route_x = safe_vertical_corridor(
                max(source_bounds["right"], target_bounds["right"]) + routing_policy.ROUTE_CLEARANCE,
                lane_boundaries,
                "right",
                pool_width,
            )
            return core_geometry.compact_points([(route_x, sy), (route_x, ty)])
        corridor_y = (sy + ty) / 2
        return core_geometry.compact_points([(sx, corridor_y), (tx, corridor_y)])

    if abs(sy - ty) < core_geometry.GEOMETRY_TOLERANCE:
        return []
    if exit_side == "right":
        route_x = safe_vertical_corridor(
            source_bounds["right"] + routing_policy.ROUTE_CLEARANCE,
            lane_boundaries,
            "right",
            pool_width,
        )
    elif exit_side == "left":
        route_x = safe_vertical_corridor(
            source_bounds["left"] - routing_policy.ROUTE_CLEARANCE,
            lane_boundaries,
            "left",
            pool_width,
        )
    else:
        route_x = (sx + tx) / 2
    return core_geometry.compact_points([(route_x, sy), (route_x, ty)])


def automatic_polyline_is_safe(
    points: list[tuple[float, float]],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    source_id: str,
    target_id: str,
) -> bool:
    """Check geometry constraints before accepting an automatic simplification."""
    lane_boundaries = internal_lane_boundaries(lanes)
    obstacle_bounds = {
        node_id: core_geometry.node_bounds_in_pool(record, lanes[record["lane"]])
        for node_id, record in nodes.items()
    }
    segments = list(zip(points, points[1:]))
    for index, segment in enumerate(segments):
        axis = core_geometry.segment_axis(segment)
        if axis == "diagonal":
            return False
        if axis == "vertical":
            x = segment[0][0]
            if any(
                abs(x - boundary) < routing_policy.LANE_BOUNDARY_CLEARANCE
                for boundary in lane_boundaries
            ):
                return False
        for node_id, bounds in obstacle_bounds.items():
            if node_id == source_id and index == 0:
                continue
            if node_id == target_id and index == len(segments) - 1:
                continue
            if core_geometry.segment_crosses_bounds(segment, bounds):
                return False
    return True


def simplify_automatic_waypoints(
    route_class: str,
    source_point: tuple[float, float],
    target_point: tuple[float, float],
    exit_side: str,
    entry_side: str,
    points: list[tuple[float, float]],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    source_id: str,
    target_id: str,
) -> list[tuple[float, float]]:
    """Prefer the fewest safe bends for automatically generated orthogonal routes."""
    full_path = core_geometry.remove_collinear_points([source_point, *points, target_point])

    if route_class == "forward" and entry_side == "top":
        sx, sy = source_point
        tx, _ = target_point
        exits_toward_target = (
            exit_side == "right" and tx > sx + core_geometry.GEOMETRY_TOLERANCE
        ) or (
            exit_side == "left" and tx < sx - core_geometry.GEOMETRY_TOLERANCE
        )
        if exits_toward_target:
            direct_elbow = core_geometry.remove_collinear_points(
                [source_point, (tx, sy), target_point]
            )
            if automatic_polyline_is_safe(
                direct_elbow,
                lanes,
                nodes,
                source_id,
                target_id,
            ):
                full_path = direct_elbow

    if (route_class == "back" and exit_side == entry_side and entry_side in {"left", "right"}
            and nodes[source_id]["lane"] == nodes[target_id]["lane"]):
        target = nodes[target_id]
        target_lane = lanes[target["lane"]]["geometry"]
        target_bounds = core_geometry.node_bounds_in_pool(target, lanes[target["lane"]])
        safe_gap = routing_policy.LANE_BOUNDARY_CLEARANCE + core_geometry.GEOMETRY_TOLERANCE
        if entry_side == "left":
            corridor_x = target_lane["x"] + safe_gap
            has_internal_space = corridor_x < target_bounds["left"] - core_geometry.GEOMETRY_TOLERANCE
        else:
            corridor_x = target_lane["x"] + target_lane["width"] - safe_gap
            has_internal_space = corridor_x > target_bounds["right"] + core_geometry.GEOMETRY_TOLERANCE
        if has_internal_space:
            _, sy = source_point
            _, ty = target_point
            target_lane_path = core_geometry.remove_collinear_points(
                [source_point, (corridor_x, sy), (corridor_x, ty), target_point]
            )
            if automatic_polyline_is_safe(
                target_lane_path,
                lanes,
                nodes,
                source_id,
                target_id,
            ):
                full_path = target_lane_path

    return full_path[1:-1]


def endpoint_direction_is_valid(
    points: list[tuple[float, float]],
    exit_side: str,
    entry_side: str,
) -> bool:
    if len(points) < 2:
        return False
    (sx, sy), (nx, ny) = points[0], points[1]
    (px, py), (tx, ty) = points[-2], points[-1]
    first_axis = core_geometry.segment_axis((points[0], points[1]))
    last_axis = core_geometry.segment_axis((points[-2], points[-1]))
    if first_axis != ("vertical" if exit_side in {"top", "bottom"} else "horizontal"):
        return False
    if last_axis != ("vertical" if entry_side in {"top", "bottom"} else "horizontal"):
        return False
    source_ok = {
        "top": ny <= sy + core_geometry.GEOMETRY_TOLERANCE,
        "bottom": ny >= sy - core_geometry.GEOMETRY_TOLERANCE,
        "left": nx <= sx + core_geometry.GEOMETRY_TOLERANCE,
        "right": nx >= sx - core_geometry.GEOMETRY_TOLERANCE,
    }[exit_side]
    target_ok = {
        "top": py <= ty + core_geometry.GEOMETRY_TOLERANCE,
        "bottom": py >= ty - core_geometry.GEOMETRY_TOLERANCE,
        "left": px <= tx + core_geometry.GEOMETRY_TOLERANCE,
        "right": px >= tx - core_geometry.GEOMETRY_TOLERANCE,
    }[entry_side]
    return source_ok and target_ok


def offset_point(point: tuple[float, float], side: str, distance: float) -> tuple[float, float]:
    x, y = point
    return {
        "top": (x, y - distance),
        "bottom": (x, y + distance),
        "left": (x - distance, y),
        "right": (x + distance, y),
    }[side]


def segments_near_parallel(
    first: tuple[tuple[float, float], tuple[float, float]],
    second: tuple[tuple[float, float], tuple[float, float]],
    *,
    clearance: float = routing_policy.NEAR_PARALLEL_CLEARANCE,
) -> bool:
    axis = core_geometry.segment_axis(first)
    if axis != core_geometry.segment_axis(second) or axis not in {"horizontal", "vertical"}:
        return False
    if axis == "horizontal":
        distance = abs(first[0][1] - second[0][1])
        overlap = min(max(first[0][0], first[1][0]), max(second[0][0], second[1][0])) - max(
            min(first[0][0], first[1][0]), min(second[0][0], second[1][0])
        )
    else:
        distance = abs(first[0][0] - second[0][0])
        overlap = min(max(first[0][1], first[1][1]), max(second[0][1], second[1][1])) - max(
            min(first[0][1], first[1][1]), min(second[0][1], second[1][1])
        )
    return core_geometry.GEOMETRY_TOLERANCE < distance < clearance and overlap >= routing_policy.MIN_INTERNAL_SEGMENT


def path_has_hairpin(points: list[tuple[float, float]]) -> bool:
    segments = list(zip(points, points[1:]))
    for first, second in zip(segments, segments[1:]):
        axis = core_geometry.segment_axis(first)
        if axis != core_geometry.segment_axis(second) or axis not in {"horizontal", "vertical"}:
            continue
        first_delta = (
            first[1][0] - first[0][0]
            if axis == "horizontal"
            else first[1][1] - first[0][1]
        )
        second_delta = (
            second[1][0] - second[0][0]
            if axis == "horizontal"
            else second[1][1] - second[0][1]
        )
        if first_delta * second_delta < 0:
            return True
    for first, middle, last in zip(segments, segments[1:], segments[2:]):
        first_axis = core_geometry.segment_axis(first)
        if first_axis != core_geometry.segment_axis(last) or first_axis == core_geometry.segment_axis(middle):
            continue
        first_delta = (
            first[1][0] - first[0][0]
            if first_axis == "horizontal"
            else first[1][1] - first[0][1]
        )
        last_delta = (
            last[1][0] - last[0][0]
            if first_axis == "horizontal"
            else last[1][1] - last[0][1]
        )
        if first_delta * last_delta < 0 and core_geometry.segment_length(middle) < routing_policy.MIN_INTERNAL_SEGMENT:
            return True
    return False


class QualityIssue:
    """Plain geometric evidence shared by planning and XML diagnostics.

    The order of issues is the legacy collector order. No diagnostic formatting,
    XML objects, candidate score, or caller-owned state enters these predicates.
    """

    __slots__ = ("code", "subject_ids", "evidence", "scope")

    def __init__(self, code, subject_ids, evidence=None, scope="path"):
        self.code = code
        self.subject_ids = tuple(subject_ids)
        self.evidence = dict(evidence or {})
        self.scope = scope

    def to_dict(self):
        return {"code": self.code, "subject_ids": list(self.subject_ids),
                "evidence": dict(self.evidence), "scope": self.scope}


def path_shape_issues(edge, points, lanes, nodes, node_bounds, internal_boundaries):
    """Short internals, safely avoidable forward bends, and hairpins.

    ``edge`` contains plain id/from/to/route/waypoints_origin fields and optional
    exit_port/entry_port (side, offset) pairs. Endpoint segments retain their
    original short-segment exemption. Lanes/nodes are routing views, not XML.
    """
    issues = []
    segments = list(zip(points, points[1:]))
    short_segments = [
        {"index": index, "length": core_geometry.segment_length(segment)}
        for index, segment in enumerate(segments[1:-1], start=1)
        if core_geometry.segment_length(segment)
        < routing_policy.MIN_INTERNAL_SEGMENT - core_geometry.GEOMETRY_TOLERANCE
    ]
    if short_segments:
        issues.append(QualityIssue("routing/short-segment", (edge.get("id"),), {
            "segments": short_segments, "minimum": routing_policy.MIN_INTERNAL_SEGMENT,
            "waypoints_origin": edge.get("waypoints_origin", "unknown"),
        }))
    bends = core_geometry.bend_count(points)
    if edge.get("route") == "forward" and bends > 2:
        source_id, target_id = edge.get("from"), edge.get("to")
        exit_port, entry_port = edge.get("exit_port"), edge.get("entry_port")
        if source_id in nodes and target_id in nodes and exit_port and entry_port:
            pool_width = max(lane["geometry"]["x"] + lane["geometry"]["width"]
                             for lane in lanes.values())
            pool_height = max(lane["geometry"]["y"] + lane["geometry"]["height"]
                              for lane in lanes.values())
            candidates = route_candidates(
                "forward", points[0], points[-1], exit_port[0], entry_port[0],
                node_bounds[source_id], node_bounds[target_id],
                lanes[nodes[target_id]["lane"]]["geometry"],
                pool_width, pool_height, internal_boundaries, [],
            )
            if any(
                core_geometry.bend_count(candidate) <= 2
                and not path_has_hairpin(candidate)
                and all(core_geometry.segment_length(segment)
                        >= routing_policy.MIN_INTERNAL_SEGMENT - core_geometry.GEOMETRY_TOLERANCE
                        for segment in list(zip(candidate, candidate[1:]))[1:-1])
                and automatic_polyline_is_safe(candidate, lanes, nodes, source_id, target_id)
                for candidate in candidates
            ):
                issues.append(QualityIssue("routing/excessive-bends", (edge.get("id"),),
                                           {"bends": bends, "maximum": 2}))
    if path_has_hairpin(points):
        issues.append(QualityIssue("routing/hairpin", (edge.get("id"),)))
    return tuple(issues)


def segment_quality_issues(edge, segments, internal_boundaries, node_bounds):
    """Ordered per-segment orthogonality, boundary and node violations.

    A diagonal emits only its orthogonality issue, as in the XML collector.
    Only the first source segment and the last target segment are exempt from
    their own terminal node; intermediate re-entry is still a crossing.
    """
    issues = []
    subject = (edge.get("id"),)
    for index, segment in enumerate(segments):
        axis = core_geometry.segment_axis(segment)
        if axis == "diagonal":
            issues.append(QualityIssue("routing/non-orthogonal", subject))
            continue
        if axis == "vertical":
            x = segment[0][0]
            if any(abs(x - boundary) < core_geometry.GEOMETRY_TOLERANCE
                   for boundary in internal_boundaries):
                issues.append(QualityIssue("routing/lane-boundary-overlap", subject, {"x": x}))
            elif any(abs(x - boundary) < routing_policy.LANE_BOUNDARY_CLEARANCE
                     for boundary in internal_boundaries):
                issues.append(QualityIssue("routing/lane-boundary-clearance", subject, {
                    "distance": min(abs(x - boundary) for boundary in internal_boundaries),
                    "minimum": routing_policy.LANE_BOUNDARY_CLEARANCE,
                }))
        for node_id, bounds in node_bounds.items():
            if node_id == edge.get("from") and index == 0:
                continue
            if node_id == edge.get("to") and index == len(segments) - 1:
                continue
            if core_geometry.segment_crosses_bounds(segment, bounds):
                issues.append(QualityIssue("routing/node-crossing", subject, {"node": node_id}))
    return tuple(issues)


def back_corridor_issues(edge, segments, lanes, nodes, node_bounds):
    """Same-lane returns need an internal corridor; cross-lane returns may approach directly."""
    if edge.get("route") != "back" or edge.get("waypoints_origin") != "automatic":
        return ()
    target_id, entry_port = edge.get("to"), edge.get("entry_port")
    if target_id not in nodes or not entry_port or entry_port[0] not in {"left", "right"}:
        return ()
    source_id = edge.get("from")
    if source_id in nodes and nodes[source_id]["lane"] != nodes[target_id]["lane"]:
        return ()
    target = nodes[target_id]
    lane = lanes[target["lane"]]["geometry"]
    bounds = node_bounds[target_id]
    safe_gap = routing_policy.LANE_BOUNDARY_CLEARANCE - core_geometry.GEOMETRY_TOLERANCE
    vertical_x = [segment[0][0] for segment in segments
                  if core_geometry.segment_axis(segment) == "vertical"]
    if entry_port[0] == "left":
        internal = any(lane["x"] + safe_gap <= x < bounds["left"] for x in vertical_x)
    else:
        internal = any(bounds["right"] < x <= lane["x"] + lane["width"] - safe_gap
                       for x in vertical_x)
    if internal:
        return ()
    return (QualityIssue("routing/back-corridor-outside-target-lane", (edge.get("id"),), {
        "target_lane": target["lane"], "entry_side": entry_port[0], "vertical_x": vertical_x,
    }),)


def edge_pair_issues(first_edge, first_segments, second_edge, second_segments):
    """Pair conflicts, close parallels and reciprocal ambiguity, in that order.

    Caller chooses pair order (the XML collector sorts semantic IDs). Shared
    endpoints retain core_geometry.segments_conflict's exact exemptions.
    """
    issues = []
    subject = (first_edge.get("id"), second_edge.get("id"))
    evidence = {"other_edge": second_edge.get("id")}
    conflict = any(core_geometry.segments_conflict(first, second)
                   for first in first_segments for second in second_segments)
    near_parallel = any(segments_near_parallel(first, second)
                        for first in first_segments for second in second_segments)
    if conflict:
        issues.append(QualityIssue("routing/edge-conflict", subject, evidence, "edge_pair"))
    if near_parallel:
        issues.append(QualityIssue("routing/near-parallel-conflict", subject, {
            **evidence, "minimum": routing_policy.NEAR_PARALLEL_CLEARANCE,
        }, "edge_pair"))
    reciprocal = (first_edge.get("from") == second_edge.get("to")
                  and first_edge.get("to") == second_edge.get("from"))
    if reciprocal and (conflict or near_parallel):
        issues.append(QualityIssue("routing/reciprocal-ambiguity", subject, evidence, "edge_pair"))
    return tuple(issues)


def route_candidates(
    route_class: str,
    source_point: tuple[float, float],
    target_point: tuple[float, float],
    exit_side: str,
    entry_side: str,
    source_bounds: dict[str, float],
    target_bounds: dict[str, float],
    target_lane: dict[str, float],
    pool_width: float,
    pool_height: float,
    lane_boundaries: list[float],
    base_waypoints: list[tuple[float, float]],
    minimum_carrier_span: float = routing_policy.MIN_INTERNAL_SEGMENT,
) -> list[list[tuple[float, float]]]:
    sx, sy = source_point
    tx, ty = target_point
    candidates: list[list[tuple[float, float]]] = []

    def add(full_path: list[tuple[float, float]]) -> None:
        simplified = core_geometry.remove_collinear_points(full_path)
        if simplified not in candidates and endpoint_direction_is_valid(
            simplified, exit_side, entry_side
        ):
            candidates.append(simplified)

    add([source_point, *base_waypoints, target_point])
    if abs(sx - tx) < core_geometry.GEOMETRY_TOLERANCE or abs(sy - ty) < core_geometry.GEOMETRY_TOLERANCE:
        add([source_point, target_point])
    add([source_point, (tx, sy), target_point])
    add([source_point, (sx, ty), target_point])

    source_escape = offset_point(source_point, exit_side, routing_policy.ROUTE_CLEARANCE)
    target_escape = offset_point(target_point, entry_side, routing_policy.ROUTE_CLEARANCE)
    if exit_side == "top" and entry_side == "bottom" and sy > ty:
        jetty = max(
            4.0,
            min(routing_policy.ROUTE_CLEARANCE, (sy - ty - minimum_carrier_span) / 2),
        )
        source_escape = offset_point(source_point, exit_side, jetty)
        target_escape = offset_point(target_point, entry_side, jetty)
    elif exit_side == "bottom" and entry_side == "top" and sy < ty:
        jetty = max(
            4.0,
            min(routing_policy.ROUTE_CLEARANCE, (ty - sy - minimum_carrier_span) / 2),
        )
        source_escape = offset_point(source_point, exit_side, jetty)
        target_escape = offset_point(target_point, entry_side, jetty)
    safe_gap = routing_policy.LANE_BOUNDARY_CLEARANCE + core_geometry.GEOMETRY_TOLERANCE
    target_columns = [
        target_lane["x"] + safe_gap,
        target_lane["x"] + target_lane["width"] - safe_gap,
        source_escape[0],
        target_escape[0],
        POOL_EDGE_MARGIN,
        pool_width - POOL_EDGE_MARGIN,
    ]
    for raw_x in target_columns:
        direction = "right" if raw_x >= (sx + tx) / 2 else "left"
        corridor_x = safe_vertical_corridor(raw_x, lane_boundaries, direction, pool_width)
        add(
            [
                source_point,
                source_escape,
                (corridor_x, source_escape[1]),
                (corridor_x, target_escape[1]),
                target_escape,
                target_point,
            ]
        )

        # When a side-exiting source sits immediately above or below a
        # vertically-entered target, the generic midpoint carriers can still
        # cut through the source.  Use the narrow free band between both
        # shapes, and connect directly to a corridor that is already outside
        # the requested source side.  This also avoids the tiny corrective
        # segment created when a nominal jetty is pushed off a lane boundary.
        carrier_y = None
        if entry_side == "top" and ty > source_bounds["bottom"]:
            lower = source_bounds["bottom"] + core_geometry.GEOMETRY_TOLERANCE
            upper = ty - clearance.CLEARANCE_THRESHOLD_PX
            if lower <= upper + core_geometry.GEOMETRY_TOLERANCE:
                carrier_y = (lower + upper) / 2
        elif entry_side == "bottom" and ty < source_bounds["top"]:
            lower = ty + clearance.CLEARANCE_THRESHOLD_PX
            upper = source_bounds["top"] - core_geometry.GEOMETRY_TOLERANCE
            if lower <= upper + core_geometry.GEOMETRY_TOLERANCE:
                carrier_y = (lower + upper) / 2
        corridor_exits_source = (
            exit_side == "left" and corridor_x <= sx + core_geometry.GEOMETRY_TOLERANCE
        ) or (
            exit_side == "right" and corridor_x >= sx - core_geometry.GEOMETRY_TOLERANCE
        )
        if carrier_y is not None and corridor_exits_source:
            add(
                [
                    source_point,
                    (corridor_x, sy),
                    (corridor_x, carrier_y),
                    (tx, carrier_y),
                    target_point,
                ]
            )

        if route_class == "back":
            # A long cross-lane return must first leave the source rank before
            # traversing other lanes.  Carrying it through the source center
            # line is likely to cut through peers that share that rank.
            carrier_y = (
                source_bounds["bottom"] + routing_policy.ROUTE_CLEARANCE
                if ty < sy
                else source_bounds["top"] - routing_policy.ROUTE_CLEARANCE
            )
            add(
                [
                    source_point,
                    source_escape,
                    (source_escape[0], carrier_y),
                    (corridor_x, carrier_y),
                    (corridor_x, target_escape[1]),
                    target_escape,
                    target_point,
                ]
            )
            outer_y = pool_height - POOL_EDGE_MARGIN
            add(
                [
                    source_point,
                    source_escape,
                    (source_escape[0], outer_y),
                    (corridor_x, outer_y),
                    (corridor_x, target_escape[1]),
                    target_escape,
                    target_point,
                ]
            )

    for corridor_y in ((sy + ty) / 2, sy + routing_policy.ROUTE_CLEARANCE, ty - routing_policy.ROUTE_CLEARANCE):
        add(
            [
                source_point,
                source_escape,
                (source_escape[0], corridor_y),
                (target_escape[0], corridor_y),
                target_escape,
                target_point,
            ]
        )
    return candidates


def candidate_score(
    points: list[tuple[float, float]],
    *,
    route_class: str,
    is_main_path: bool,
    same_lane_down: bool,
    target_lane: dict[str, float],
    target_bounds: dict[str, float],
    entry_side: str,
    existing_segments: list[tuple[tuple[float, float], tuple[float, float]]],
    reciprocal_segments: list[tuple[tuple[float, float], tuple[float, float]]],
    label_choice: tuple[int, dict[str, float]] | None,
    has_label: bool,
    prefer_target_lane_corridor: bool = True,
) -> float:
    segments = list(zip(points, points[1:]))
    bends = core_geometry.bend_count(points)
    score = core_geometry.polyline_length(points) + bends * ROUTE_BEND_PENALTY
    internal = segments[1:-1]
    score += sum(
        5000.0 for segment in internal if core_geometry.segment_length(segment) < routing_policy.MIN_INTERNAL_SEGMENT
    )
    if path_has_hairpin(points):
        score += 8000.0
    if is_main_path and same_lane_down and bends:
        score += 12000.0 * bends
    if route_class == "forward" and bends > 2:
        score += 5000.0 * (bends - 2)
    for segment in segments:
        for other in existing_segments:
            if core_geometry.segments_conflict(segment, other):
                score += ROUTE_CONFLICT_PENALTY
            elif segments_near_parallel(segment, other):
                score += ROUTE_CONFLICT_PENALTY / 2
        for other in reciprocal_segments:
            if core_geometry.segments_conflict(segment, other) or segments_near_parallel(segment, other):
                score += ROUTE_CONFLICT_PENALTY * 2
    if route_class == "back" and prefer_target_lane_corridor:
        vertical_x = [
            segment[0][0]
            for segment in segments[1:-1]
            if core_geometry.segment_axis(segment) == "vertical"
        ]
        lane_left = target_lane["x"] + routing_policy.LANE_BOUNDARY_CLEARANCE + core_geometry.GEOMETRY_TOLERANCE
        lane_right = (
            target_lane["x"]
            + target_lane["width"]
            - routing_policy.LANE_BOUNDARY_CLEARANCE
            - core_geometry.GEOMETRY_TOLERANCE
        )
        left_slots = [
            value
            for value in vertical_x
            if lane_left <= value < target_bounds["left"] - core_geometry.GEOMETRY_TOLERANCE
        ]
        right_slots = [
            value
            for value in vertical_x
            if target_bounds["right"] + core_geometry.GEOMETRY_TOLERANCE < value <= lane_right
        ]
        has_target_lane_slot = bool(left_slots if entry_side == "left" else right_slots)
        if entry_side in {"top", "bottom"}:
            has_target_lane_slot = bool(left_slots or right_slots)
        if not has_target_lane_slot:
            score += 6000.0
    if has_label and label_choice is None:
        score += ROUTE_LABEL_CONFLICT_PENALTY
    return score


def _endpoint_is_fixed(edge: dict, assignment, endpoint: str) -> bool:
    planned = assignment.exit if endpoint == "exit" else assignment.entry
    return (
        edge.get(f"{endpoint}_offset") is not None
        or planned.source in {"explicit", "locked"}
    )


def _node_shares_lane_rank(node_id: str, nodes: dict[str, dict]) -> bool:
    node = nodes[node_id]
    rank = node.get("semantic", {}).get("rank", node.get("rank"))
    return any(
        other_id != node_id
        and other.get("lane") == node.get("lane")
        and other.get("semantic", {}).get("rank", other.get("rank")) == rank
        for other_id, other in nodes.items()
    )


def _aligned_main_path_offsets(
    edge: dict,
    assignment,
    source_bounds: dict[str, float],
    target_bounds: dict[str, float],
) -> tuple[float, float] | None:
    """Return the nearest exact tangent alignment allowed by endpoint locks."""
    exit_side = assignment.exit.side
    entry_side = assignment.entry.side
    vertical_pair = exit_side in {"top", "bottom"} and entry_side in {"top", "bottom"}
    horizontal_pair = exit_side in {"left", "right"} and entry_side in {"left", "right"}
    if not (vertical_pair or horizontal_pair):
        return None

    if vertical_pair:
        source_start, source_span = source_bounds["left"], source_bounds["width"]
        target_start, target_span = target_bounds["left"], target_bounds["width"]
    else:
        source_start, source_span = source_bounds["top"], source_bounds["height"]
        target_start, target_span = target_bounds["top"], target_bounds["height"]

    exit_fixed = _endpoint_is_fixed(edge, assignment, "exit")
    entry_fixed = _endpoint_is_fixed(edge, assignment, "entry")
    source_min = source_start + 0.05 * source_span
    source_max = source_start + 0.95 * source_span
    target_min = target_start + 0.05 * target_span
    target_max = target_start + 0.95 * target_span
    if exit_fixed:
        source_min = source_max = source_start + assignment.exit.offset * source_span
    if entry_fixed:
        target_min = target_max = target_start + assignment.entry.offset * target_span
    lower = max(source_min, target_min)
    upper = min(source_max, target_max)
    if lower > upper + core_geometry.GEOMETRY_TOLERANCE:
        return None

    current_source = source_start + assignment.exit.offset * source_span
    current_target = target_start + assignment.entry.offset * target_span
    source_center = source_start + source_span / 2
    target_center = target_start + target_span / 2
    coordinate_candidates = {
        min(max(value, lower), upper)
        for value in (
            source_center,
            target_center,
            current_source,
            current_target,
            (current_source + current_target) / 2,
        )
    }

    def alignment_preference(coordinate: float) -> tuple:
        source_offset = (coordinate - source_start) / source_span
        target_offset = (coordinate - target_start) / target_span
        centered_endpoints = sum(
            abs(offset - 0.5) <= core_geometry.GEOMETRY_TOLERANCE / span
            for offset, span in (
                (source_offset, source_span),
                (target_offset, target_span),
            )
        )
        return (
            -centered_endpoints,
            abs(source_offset - 0.5) + abs(target_offset - 0.5),
            abs(coordinate - current_source) + abs(coordinate - current_target),
            coordinate,
        )

    coordinate = min(coordinate_candidates, key=alignment_preference)
    exit_offset = round((coordinate - source_start) / source_span, 6)
    entry_offset = round((coordinate - target_start) / target_span, 6)
    if (
        abs(exit_offset - assignment.exit.offset) <= core_geometry.GEOMETRY_TOLERANCE / max(source_span, 1.0)
        and abs(entry_offset - assignment.entry.offset) <= core_geometry.GEOMETRY_TOLERANCE / max(target_span, 1.0)
    ):
        return None
    return exit_offset, entry_offset


def _measure_candidate_clearance(candidate, profile):
    required = {"target_bounds", "target_type", "target_style", "edge_style"}
    if not isinstance(profile, dict) or not required <= set(profile):
        return clearance.ClearanceMeasurement(
            status=clearance.STATUS_NOT_AVAILABLE,
            reason="missing_clearance_profile",
        )
    return clearance.measure_arrowhead_clearance(
        candidate,
        target_bounds=profile["target_bounds"],
        target_type=profile["target_type"],
        target_style=profile["target_style"],
        edge_style=profile["edge_style"],
    )


def _candidate_quality(edge, assignment, path, lanes, nodes, context):
    """Use the same geometric facts as final XML collectors, before scoring."""
    view = dict(edge, route=infer_route_class(edge, nodes[edge["from"]], nodes[edge["to"]]),
                waypoints_origin="automatic", exit_port=(assignment.exit.side, assignment.exit.offset),
                entry_port=(assignment.entry.side, assignment.entry.offset))
    bounds = {key: core_geometry.node_bounds_in_pool(node, lanes[node["lane"]])
              for key, node in nodes.items()}
    boundaries = internal_lane_boundaries(lanes)
    segments = list(zip(path, path[1:]))
    issues = [*path_shape_issues(view, path, lanes, nodes, bounds, boundaries),
              *segment_quality_issues(view, segments, boundaries, bounds),
              *back_corridor_issues(view, segments, lanes, nodes, bounds)]
    if not endpoint_direction_is_valid(path, assignment.exit.side, assignment.entry.side):
        issues.append(QualityIssue("routing/endpoint-direction", (edge["id"],), {}, "edge"))
    if ((edge["from"], edge["to"]) in context.get("main_path_pairs", ())
            and nodes[edge["from"]]["lane"] == nodes[edge["to"]]["lane"]
            and int(nodes[edge["to"]]["semantic"].get("rank", 0)) > int(nodes[edge["from"]]["semantic"].get("rank", 0))
            and all(nodes[key]["semantic"].get("slot", "main") == "main" for key in (edge["from"], edge["to"]))
            and core_geometry.bend_count(path)):
        issues.append(QualityIssue("layout/main-path-zigzag", (edge["id"],), {}, "edge"))
    for other_id, other_path in sorted(context.get("paths", {}).items()):
        if other_id == edge["id"]:
            continue
        source, target = context.get("endpoints", {}).get(other_id, (None, None))
        issues.extend(edge_pair_issues(view, segments, {"id": other_id, "from": source, "to": target},
                                      list(zip(other_path, other_path[1:]))))
    return [issue.to_dict() for issue in issues]


def _route_rejection_key(edge_id, assignment, hints, profiles, bounds, paths, frozen_labels):
    """A rejected label carrier applies only to its exact geometric environment."""
    return (assignment.assignment_key,
            tuple((contracts.number(x), contracts.number(y)) for x, y in hints),
            repr(profiles.get(edge_id)), repr(bounds),
            tuple((key, tuple(value)) for key, value in sorted(paths.items()) if key != edge_id),
            repr(frozen_labels))


def _native_candidate_failure(edge, failures, counts, *, budget=None):
    blockers = sorted({subject for item in failures for subject in item.get("subject_ids", ())
                       if subject and subject != edge["id"]})
    reasons = [item.get("evidence", {}).get("reason") for item in failures]
    unsupported = any(reason for reason in reasons)
    immutable = any(reason and reason not in {"editor_router_additional_turns_required",
                                              "uncalibrated_off_center_perimeter"} for reason in reasons)
    samples, per_reason = [], {}
    for item in failures:
        key = item.get("code"), item.get("evidence", {}).get("reason")
        if len(samples) < 32 and per_reason.get(key, 0) < 3:
            samples.append(item)
            per_reason[key] = per_reason.get(key, 0) + 1
    return RouteFailure(
        "routing/route-search-budget" if budget else "routing/no-safe-route", edge["id"],
        "Automatic route candidate budget was exhausted" if budget else
        "No feasible automatic route for the selected endpoints",
        locked=immutable,
        evidence={"planning": {"version": 1, "stage": "candidate", "reason":
                  "budget_exhausted" if budget else "native_profile_unavailable" if unsupported else
                  "candidate_space_exhausted", "budget": budget, "counts": dict(counts),
                  "blocking_edge_ids": blockers, "details": samples,
                  "detail_count": len(failures), "truncated": len(failures) > len(samples)}},
        supported_fixes=("reroute-edge", "align-ports", "review-native-label-geometry"))


def route_edge_at_ports(
    edge: dict,
    assignment,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    routing_context: dict | None = None,
    *,
    allow_unsafe_base: bool = False,
    clearance_profile: dict | None = None,
    require_clearance: bool = False,
):
    """Route one edge at a fixed port assignment without reserving or writing state."""
    context = routing_context or {}
    if edge["from"] not in nodes or edge["to"] not in nodes:
        return RouteFailure(
            "routing/missing-endpoint",
            edge.get("id"),
            f"Edge {edge.get('id')} references a missing node",
            locked=True,
            evidence={"from": edge.get("from"), "to": edge.get("to")},
            supported_fixes=("repair-edge-endpoints",),
        )
    if (
        assignment.edge_id != edge.get("id")
        or assignment.exit.node_id != edge["from"]
        or assignment.entry.node_id != edge["to"]
    ):
        return RouteFailure(
            "routing/port-assignment-mismatch",
            edge.get("id"),
            f"Port assignment does not match edge {edge.get('id')}",
            locked=True,
            evidence={"assignment": assignment.assignment_key},
            supported_fixes=("align-ports",),
        )
    source = nodes[edge["from"]]
    target = nodes[edge["to"]]
    source_lane = lanes[source["lane"]]
    target_lane = lanes[target["lane"]]
    route_class = infer_route_class(edge, source, target)
    main_path_pairs = context.get("main_path_pairs", set())
    exit_side = ports.validate_side(assignment.exit.side, "exit_side")
    entry_side = ports.validate_side(assignment.entry.side, "entry_side")
    exit_offset = ports.validate_offset(assignment.exit.offset, "exit_offset")
    entry_offset = ports.validate_offset(assignment.entry.offset, "entry_offset")
    explicit_mismatch = (
        (edge.get("exit_side") is not None and ports.validate_side(edge["exit_side"], "exit_side") != exit_side)
        or (edge.get("entry_side") is not None and ports.validate_side(edge["entry_side"], "entry_side") != entry_side)
        or (
            edge.get("exit_offset") is not None
            and abs(ports.validate_offset(edge["exit_offset"], "exit_offset") - exit_offset)
            > core_geometry.GEOMETRY_TOLERANCE / 100
        )
        or (
            edge.get("entry_offset") is not None
            and abs(ports.validate_offset(edge["entry_offset"], "entry_offset") - entry_offset)
            > core_geometry.GEOMETRY_TOLERANCE / 100
        )
    )
    if explicit_mismatch:
        return RouteFailure(
            "routing/port-assignment-mismatch",
            edge.get("id"),
            f"Port assignment overrides an explicit endpoint on edge {edge.get('id')}",
            locked=True,
            evidence={"assignment": assignment.assignment_key},
            supported_fixes=("align-ports",),
        )
    source_bounds = core_geometry.node_bounds_in_pool(source, source_lane)
    target_bounds = core_geometry.node_bounds_in_pool(target, target_lane)
    source_point = core_geometry.port_point(source_bounds, exit_side, exit_offset)
    target_point = core_geometry.port_point(target_bounds, entry_side, entry_offset)
    pool_width = max(record["geometry"]["x"] + record["geometry"]["width"] for record in lanes.values())
    pool_height = max(
        record["geometry"]["y"] + record["geometry"]["height"]
        for record in lanes.values()
    )
    label_container = {
        "left": 0.0,
        "right": pool_width,
        "top": 0.0,
        "bottom": pool_height,
    }
    lane_boundaries = internal_lane_boundaries(lanes)
    node_boxes = [
        core_geometry.node_bounds_in_pool(record, lanes[record["lane"]])
        for record in nodes.values()
    ]
    existing_paths = context.get("paths", {})
    existing_segments = [
        segment
        for path in existing_paths.values()
        for segment in zip(path, path[1:])
    ]
    reciprocal_segments = [
        segment
        for edge_id, path in existing_paths.items()
        if context.get("endpoints", {}).get(edge_id) == (edge["to"], edge["from"])
        for segment in zip(path, path[1:])
    ]
    existing_labels = list(context.get("labels", {}).values())
    preferred_label_side = context.get("label_sides", {}).get(edge["id"])
    label = str(edge.get("label", ""))
    label_choice: tuple[int, dict[str, float]] | None = None
    if "waypoints" in edge:
        points = normalize_waypoints(edge["waypoints"])
        full_path = core_geometry.compact_points([source_point, *points, target_point])
        label_choice = labels.choose_label_box(
            full_path,
            label,
            node_boxes,
            existing_segments,
            existing_labels,
            preferred_label_side,
            label_container,
            route_class == "back",
        )
    else:
        base_points = automatic_waypoints(
            route_class,
            source_bounds,
            target_bounds,
            source_point,
            target_point,
            exit_side,
            entry_side,
            pool_width,
            lane_boundaries,
        )
        base_points = simplify_automatic_waypoints(
            route_class,
            source_point,
            target_point,
            exit_side,
            entry_side,
            base_points,
            lanes,
            nodes,
            edge["from"],
            edge["to"],
        )
        candidates = route_candidates(
            route_class,
            source_point,
            target_point,
            exit_side,
            entry_side,
            source_bounds,
            target_bounds,
            target_lane["geometry"],
            pool_width,
            pool_height,
            lane_boundaries,
            base_points,
            max(
                routing_policy.MIN_INTERNAL_SEGMENT,
                labels.edge_label_size(label)[1] + labels.EDGE_LABEL_PADDING,
            )
            if label.strip()
            else routing_policy.MIN_INTERNAL_SEGMENT,
        )
        native = context.get("native_label_profiles", {}).get(edge["id"])
        counts = {}
        if native is not None:
            # All local rejections consume the same finite candidate budget.
            unique = {}
            for candidate in candidates:
                key = tuple((contracts.number(x), contracts.number(y)) for x, y in candidate[1:-1])
                unique.setdefault(key, candidate)
            safe_candidates = []
            budget = context["candidate_budget"]
            for index, candidate in enumerate(unique.values()):
                if index >= budget["max_path_candidates"]:
                    return _native_candidate_failure(edge, [], counts, budget="path_candidates")
                if budget["evaluations"] + index >= budget["max_candidate_evaluations"]:
                    return _native_candidate_failure(edge, [], counts, budget="candidate_evaluations")
                counts["candidate_evaluations"] = index + 1
                if automatic_polyline_is_safe(candidate, lanes, nodes, edge["from"], edge["to"]):
                    safe_candidates.append(candidate)
                else:
                    counts["routing/local-obstacle"] = counts.get("routing/local-obstacle", 0) + 1
        else:
            safe_candidates = [candidate for candidate in candidates
                               if automatic_polyline_is_safe(candidate, lanes, nodes, edge["from"], edge["to"])]
        if not safe_candidates:
            if not allow_unsafe_base:
                exit_fixed = (
                    edge.get("exit_offset") is not None
                    or assignment.exit.source in {"explicit", "locked"}
                )
                entry_fixed = (
                    edge.get("entry_offset") is not None
                    or assignment.entry.source in {"explicit", "locked"}
                )
                return RouteFailure(
                    "routing/no-safe-route",
                    edge.get("id"),
                    f"No safe route exists for the selected ports on edge {edge.get('id')}",
                    locked=exit_fixed and entry_fixed,
                    evidence={
                        "exit_side": exit_side,
                        "exit_offset": exit_offset,
                        "entry_side": entry_side,
                        "entry_offset": entry_offset,
                        **({"planning": {"version": 1, "counts": counts,
                             "reason": "candidate_space_exhausted"}} if native is not None else {}),
                    },
                    supported_fixes=(
                        "allocate-distinct-port", "align-ports", "reroute-edge",
                        "increase-lane-width",
                    ),
                )
            safe_candidates = [core_geometry.compact_points([source_point, *base_points, target_point])]
        frozen_labels = {
            label_id: box for label_id, box in context.get("frozen_label_obstacles", {}).items()
            if label_id != edge["id"]
        }
        blocked_labels = set()
        obstacle_free_candidates = []
        for candidate in safe_candidates:
            overlaps = {
                label_id for label_id, box in frozen_labels.items()
                if any(core_geometry.segment_intersects_box(segment, box, gap=1.0)
                       for segment in zip(candidate, candidate[1:]))
            }
            if overlaps:
                blocked_labels.update(overlaps)
            else:
                obstacle_free_candidates.append(candidate)
        if not obstacle_free_candidates:
            return RouteFailure(
                "routing/no-safe-route",
                edge.get("id"),
                f"No automatic route avoids the saved labels on edge {edge.get('id')}",
                locked=(
                    _endpoint_is_fixed(edge, assignment, "exit")
                    and _endpoint_is_fixed(edge, assignment, "entry")
                ),
                evidence={
                    "frozen_label_edges": sorted(blocked_labels),
                    "candidate_count": len(safe_candidates),
                    **({"planning": {"version": 1, "counts": counts,
                         "reason": "candidate_space_exhausted", "blocking_label_ids": sorted(blocked_labels)}}
                       if native is not None else {}),
                },
                supported_fixes=("reroute-edge", "move-edge-label", "increase-lane-width"),
            )
        safe_candidates = obstacle_free_candidates
        is_main_path = (edge["from"], edge["to"]) in main_path_pairs
        source_rank = int(source["semantic"].get("rank", "0"))
        target_rank = int(target["semantic"].get("rank", "0"))
        same_lane_down = source["lane"] == target["lane"] and target_rank > source_rank
        clearance_by_path = {}
        if require_clearance:
            measurements = []
            for candidate in safe_candidates:
                measurement = _measure_candidate_clearance(candidate, clearance_profile)
                measurements.append((candidate, measurement))
                if (
                    measurement.status != clearance.STATUS_COMPLETE
                    or measurement.violation is False
                ):
                    clearance_by_path[tuple(candidate)] = measurement
            safe_candidates = [
                candidate for candidate, measurement in measurements
                if measurement.status != clearance.STATUS_COMPLETE
                or measurement.violation is False
            ]
            if not safe_candidates:
                return RouteFailure(
                    "routing/arrowhead-clearance",
                    edge.get("id"),
                    f"No automatic route has at least {clearance.CLEARANCE_THRESHOLD_PX:g}px "
                    f"of measurable arrowhead clearance on edge {edge.get('id')}",
                    locked=(
                        _endpoint_is_fixed(edge, assignment, "exit")
                        and _endpoint_is_fixed(edge, assignment, "entry")
                    ),
                    evidence={
                        "minimum_terminal_run_px": clearance.CLEARANCE_THRESHOLD_PX,
                        "candidate_count": len(measurements),
                        **({"planning": {"version": 1, "counts": counts,
                             "reason": "candidate_space_exhausted"}} if native is not None else {}),
                    },
                    supported_fixes=(
                        "reroute-edge", "increase-target-lane-gutter",
                        "set-explicit-waypoints",
                    ),
                )

        if (
            is_main_path
            and same_lane_down
            and exit_side == "bottom"
            and entry_side == "top"
            and not _node_shares_lane_rank(edge["from"], nodes)
            and not _node_shares_lane_rank(edge["to"], nodes)
            and not any(core_geometry.bend_count(candidate) == 0 for candidate in safe_candidates)
        ):
            suggested_offsets = _aligned_main_path_offsets(
                edge, assignment, source_bounds, target_bounds
            )
            if suggested_offsets is not None:
                return RouteFailure(
                    "layout/main-path-zigzag",
                    edge.get("id"),
                    f"Main-path ports can be realigned to avoid a zigzag on edge {edge.get('id')}",
                    locked=False,
                    suggested_offsets=suggested_offsets,
                    evidence={"suggested_offsets": suggested_offsets,
                              **({"planning": {"version": 1, "counts": counts}} if native is not None else {})},
                    supported_fixes=("align-ports", "reroute-edge"),
                )

        native = context.get("native_label_profiles", {}).get(edge["id"])
        preflights = {}
        if native is not None:
            failures, feasible = [], []
            unique = {}
            for candidate in safe_candidates:
                key = tuple((contracts.number(x), contracts.number(y)) for x, y in candidate[1:-1])
                unique.setdefault(key, candidate)
            for candidate_index, (key, candidate) in enumerate(unique.items()):
                rejection_key = _route_rejection_key(edge["id"], assignment, candidate[1:-1],
                        context.get("native_label_profiles", {}), context.get("rejection_bounds", {}),
                        dict(context.get("rejection_paths", {}), **context.get("paths", {})),
                        context.get("frozen_label_obstacles", {}))
                if rejection_key in context.get("rejected_paths", {}).get(edge["id"], set()):
                    continue
                measured = labels.preflight_candidate(native, assignment, candidate[1:-1])
                issues = _candidate_quality(edge, assignment, candidate, lanes, nodes, context)
                if measured["path_status"] != "available" or measured["label_status"] == "not_available":
                    issues.append({"code": "text/edge-label-geometry-unavailable", "subject_ids": [edge["id"]],
                                   "evidence": {"reason": measured["path_reason"] or measured["label_reason"],
                                                "profile": measured["profile"]}})
                elif measured["path"] != candidate:
                    issues.extend(_candidate_quality(edge, assignment, measured["path"], lanes, nodes, context))
                if issues:
                    for issue in issues:
                        counts[issue["code"]] = counts.get(issue["code"], 0) + 1
                    failures.extend(issues)
                else:
                    feasible.append(candidate)
                    preflights[tuple(candidate)] = measured
            if not feasible:
                return _native_candidate_failure(edge, failures, counts)
            safe_candidates = feasible

        ranked: list[tuple[float, list[tuple[float, float]], tuple[int, dict[str, float]] | None]] = []
        for candidate in safe_candidates:
            candidate_label = labels.choose_label_box(
                candidate,
                label,
                node_boxes,
                existing_segments,
                existing_labels,
                preferred_label_side,
                label_container,
                route_class == "back",
                size=preflights[tuple(candidate)]["size"] if native is not None else None,
            )
            score = candidate_score(
                candidate,
                route_class=route_class,
                is_main_path=is_main_path,
                same_lane_down=same_lane_down,
                target_lane=target_lane["geometry"],
                target_bounds=target_bounds,
                entry_side=entry_side,
                existing_segments=existing_segments,
                reciprocal_segments=reciprocal_segments,
                label_choice=candidate_label,
                has_label=bool(label.strip()),
                prefer_target_lane_corridor=source["lane"] == target["lane"],
            )
            if (
                require_clearance
                and clearance_by_path[tuple(candidate)].status
                != clearance.STATUS_COMPLETE
            ):
                score += ROUTE_CONFLICT_PENALTY
            ranked.append((score, candidate, candidate_label))
        _, full_path, label_choice = min(
            ranked,
            key=lambda item: (
                item[0],
                len(item[1]),
                [(round(x, 4), round(y, 4)) for x, y in item[1]],
                tuple((contracts.number(x), contracts.number(y)) for x, y in item[1]),
            ),
        )
        points = full_path[1:-1]
    routed = {
        "points": points,
        "route": route_class,
        "exit_side": exit_side,
        "entry_side": entry_side,
        "exit_offset": exit_offset,
        "entry_offset": entry_offset,
        "full_path": core_geometry.compact_points([source_point, *points, target_point]),
        "label_choice": label_choice,
    }
    if "waypoints" not in edge and require_clearance:
        routed["arrowhead_clearance"] = clearance_by_path[tuple(full_path)].to_dict()
    if "waypoints" not in edge and context.get("native_label_profiles", {}).get(edge["id"]) is not None:
        measured = preflights[tuple(full_path)]
        routed["native_path"] = measured["path"]
        routed["native_measurement"] = measured
        routed["candidate_evaluations"] = counts.get("candidate_evaluations", 0)
    return RouteDecision(edge["id"], assignment, routed)


def route_edge(
    edge: dict,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    allocator: ports.PortAllocator,
    routing_context: dict | None = None,
) -> dict:
    """Compatibility route API with its historical allocator and base fallback."""
    context = routing_context or {}
    if edge["from"] not in nodes or edge["to"] not in nodes:
        raise contracts.DiagramError(f"Edge {edge.get('id')} references a missing node")
    source = nodes[edge["from"]]
    target = nodes[edge["to"]]
    source_lane = lanes[source["lane"]]
    target_lane = lanes[target["lane"]]
    route_class = infer_route_class(edge, source, target)
    exit_side, entry_side = preferred_sides(
        edge,
        route_class,
        source,
        target,
        lanes,
        main_path_pairs=context.get("main_path_pairs", set()),
        outgoing_counts=context.get("outgoing_counts", {}),
        bottom_reserved_sources=context.get("bottom_reserved_sources", set()),
        v3_semantics=bool(context.get("v3_semantics", False)),
    )
    source_bounds = core_geometry.node_bounds_in_pool(source, source_lane)
    target_bounds = core_geometry.node_bounds_in_pool(target, target_lane)
    exit_offset, entry_offset = ports.allocate_port_pair(
        allocator,
        edge,
        source_bounds,
        target_bounds,
        exit_side,
        entry_side,
        context.get("port_limits", {}).get(edge["id"]),
        prefer_center_ports=(
            (
                bool(context.get("v3_semantics", False))
                and (
                    route_class == "back"
                    or source["semantic"].get("type") == "decision"
                )
            )
            or (
                route_class == "forward"
                and source["lane"] != target["lane"]
                and exit_side in {"top", "bottom"}
                and entry_side in {"top", "bottom"}
            )
        ),
    )
    assignment = port_planner.EdgePortAssignment(
        edge["id"],
        port_planner.PlannedEndpoint(
            edge["from"], exit_side, exit_offset,
            "explicit" if edge.get("exit_offset") is not None else "derived",
        ),
        port_planner.PlannedEndpoint(
            edge["to"], entry_side, entry_offset,
            "explicit" if edge.get("entry_offset") is not None else "derived",
        ),
    )
    outcome = route_edge_at_ports(
        edge, assignment, lanes, nodes, context, allow_unsafe_base=True
    )
    if isinstance(outcome, RouteFailure):
        raise contracts.DiagramError(outcome.message)
    return outcome.routed


def new_routing_context(
    main_path: list[str],
    edges: list[dict],
    nodes: dict[str, dict] | None = None,
    *,
    v3_semantics: bool = False,
) -> dict:
    bottom_reserved_sources: set[str] = set()
    for edge in edges:
        if not nodes or edge["from"] not in nodes or edge["to"] not in nodes:
            continue
        source = nodes[edge["from"]]
        target = nodes[edge["to"]]
        source_type = (
            source["semantic"].get("type", "process")
            if "semantic" in source
            else source.get("type", "process")
        )
        target_type = (
            target["semantic"].get("type", "process")
            if "semantic" in target
            else target.get("type", "process")
        )
        source_rank = int(
            source["semantic"].get("rank", "0")
            if "semantic" in source
            else source.get("rank", 0)
        )
        target_rank = int(
            target["semantic"].get("rank", "0")
            if "semantic" in target
            else target.get("rank", 0)
        )
        source_lane = source.get("lane")
        target_lane = target.get("lane")
        if (
            source_type == "decision"
            and target_type == "end"
            and source_lane == target_lane
            and target_rank > source_rank
        ):
            bottom_reserved_sources.add(edge["from"])
    return {
        "main_path_pairs": set(zip(main_path, main_path[1:])),
        "outgoing_counts": {
            source_id: sum(edge["from"] == source_id for edge in edges)
            for source_id in {edge["from"] for edge in edges}
        },
        "bottom_reserved_sources": bottom_reserved_sources,
        "v3_semantics": v3_semantics,
        "paths": {},
        "endpoints": {},
        "labels": {},
        "frozen_label_obstacles": {},
        "port_limits": {},
        "label_sides": {},
    }


def derive_port_limits(
    context: dict,
    edges: list[dict],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
) -> None:
    """Keep automatic branch ports on the non-crossing side of explicit returns."""

    def merge(edge_id: str, endpoint: str, bound: str, value: float) -> None:
        limits = context.setdefault("port_limits", {}).setdefault(edge_id, {}).setdefault(
            endpoint, {}
        )
        if bound == "min":
            limits[bound] = max(limits.get(bound, 0.05), min(0.95, value))
        else:
            limits[bound] = min(limits.get(bound, 0.95), max(0.05, value))

    for back_edge in edges:
        source_id = back_edge.get("from")
        target_id = back_edge.get("to")
        if source_id not in nodes or target_id not in nodes:
            continue
        source = nodes[source_id]
        target = nodes[target_id]
        if infer_route_class(back_edge, source, target) != "back":
            continue
        if "exit_side" not in back_edge or "exit_offset" not in back_edge:
            continue
        back_side = ports.validate_side(back_edge["exit_side"], "exit_side")
        back_offset = ports.validate_offset(back_edge["exit_offset"], "exit_offset")
        source_bounds = core_geometry.node_bounds_in_pool(source, lanes[source["lane"]])
        target_bounds = core_geometry.node_bounds_in_pool(target, lanes[target["lane"]])
        span = source_bounds["height"] if back_side in {"left", "right"} else source_bounds["width"]
        normalized_clearance = routing_policy.NEAR_PARALLEL_CLEARANCE / max(span, 1.0)

        for other in edges:
            if other.get("id") == back_edge.get("id"):
                continue
            other_source = nodes.get(other.get("from"))
            other_target = nodes.get(other.get("to"))
            if other_source is None or other_target is None:
                continue
            route_class = infer_route_class(other, other_source, other_target)
            exit_side, entry_side = preferred_sides(
                other,
                route_class,
                other_source,
                other_target,
                lanes,
                main_path_pairs=context.get("main_path_pairs", set()),
                outgoing_counts=context.get("outgoing_counts", {}),
                bottom_reserved_sources=context.get("bottom_reserved_sources", set()),
                v3_semantics=bool(context.get("v3_semantics", False)),
            )
            if other.get("to") == source_id and entry_side == back_side and back_side in {"left", "right"}:
                target_is_above = target_bounds["top"] < source_bounds["top"]
                merge(
                    other["id"],
                    "entry",
                    "min" if target_is_above else "max",
                    back_offset + normalized_clearance
                    if target_is_above
                    else back_offset - normalized_clearance,
                )
            if other.get("from") == source_id and exit_side == back_side and back_side in {"top", "bottom"}:
                target_is_right = (
                    target_bounds["left"] + target_bounds["right"]
                    > source_bounds["left"] + source_bounds["right"]
                )
                merge(
                    other["id"],
                    "exit",
                    "max" if target_is_right else "min",
                    back_offset - normalized_clearance
                    if target_is_right
                    else back_offset + normalized_clearance,
                )
                context.setdefault("label_sides", {})[other["id"]] = (
                    "left" if target_is_right else "right"
                )


def edge_routing_order(edges: list[dict], main_path: list[str], nodes: dict[str, dict]) -> list[dict]:
    pair_index = {
        pair: index for index, pair in enumerate(zip(main_path, main_path[1:]))
    }

    def key(edge: dict) -> tuple[int, int, str]:
        pair = (edge["from"], edge["to"])
        if pair in pair_index:
            return 0, pair_index[pair], edge["id"]
        route_class = inferred_spec_route_class(edge, nodes)
        priority = 2 if route_class == "back" else 1
        source = nodes[edge["from"]]
        source_rank = source.get("rank")
        if source_rank is None:
            source_rank = source["semantic"].get("rank", "0")
        return priority, int(source_rank), edge["id"]

    return sorted(edges, key=key)


def _clone_routing_value(value):
    if isinstance(value, dict):
        return {key: _clone_routing_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone_routing_value(item) for item in value]
    if isinstance(value, set):
        return {_clone_routing_value(item) for item in value}
    if isinstance(value, tuple):
        return tuple(_clone_routing_value(item) for item in value)
    return value


def _linked_reciprocal_pairs(edges: list[dict]) -> tuple[tuple[str, str], ...]:
    """Link only explicit reverse topology; labels and edge types are irrelevant."""
    pairs = []
    for index, first in enumerate(edges):
        for second in edges[index + 1:]:
            if first.get("from") == second.get("to") and first.get("to") == second.get("from"):
                pairs.append(tuple(sorted((first["id"], second["id"]))))
    return tuple(sorted(set(pairs)))


def _port_assignment_signature(plan, edge_ids) -> tuple:
    assignments = plan.by_edge()
    return tuple(sorted(assignments[edge_id].assignment_key for edge_id in edge_ids))


def _component_edge_ids(plan, edge_id: str) -> tuple[str, ...]:
    for component in plan.components:
        if edge_id in component.edge_ids:
            return component.edge_ids
    return (edge_id,)


def _port_plan_evidence(plan, issue=None) -> dict:
    evidence = {
        "plan_status": plan.status,
        "attempts": plan.attempts,
        "candidate_pairs": plan.candidate_pairs,
        "budget": {
            "max_endpoint_candidates": plan.budget.max_endpoint_candidates,
            "max_backtracks_per_group": plan.budget.max_backtracks_per_group,
            "max_attempts": plan.budget.max_attempts,
        },
    }
    if issue is not None:
        if issue.node_id is not None:
            evidence["node"] = issue.node_id
        if issue.side is not None:
            evidence["side"] = issue.side
        if issue.edge_ids:
            evidence["edges"] = issue.edge_ids
    return evidence


def _port_plan_supported_fixes(plan) -> tuple[str, ...]:
    if plan.status == port_planner.PLAN_CONSTRAINT_CONFLICT:
        return ("allocate-distinct-port", "align-ports")
    if plan.status == port_planner.PLAN_CAPACITY_EXHAUSTED:
        return ("increase-lane-width", "allocate-distinct-port")
    if plan.status == port_planner.PLAN_BUDGET_EXHAUSTED:
        return ("reroute-edge", "increase-lane-width")
    return (
        "allocate-distinct-port", "align-ports", "reroute-edge",
        "increase-lane-width",
    )


def _port_plan_failure(plan, *, batch_replays=0, component_replans=None, routing_order=(), linked_edge_pairs=()):
    issue = plan.issues[0] if plan.issues else None
    edge_id = issue.edge_ids[0] if issue is not None and issue.edge_ids else None
    component_key = None
    assignment_key = None
    if edge_id is not None:
        try:
            component_key = plan.component_assignment_key(edge_id)
            assignment_key = plan.component_assignment_signature(component_key)
        except KeyError:
            pass
    return BatchRouteResult(
        ROUTE_FAILED,
        failure=RouteFailure(
            issue.code if issue is not None else "routing/port-plan-exhausted",
            edge_id,
            issue.message if issue is not None else "No complete port plan is available",
            locked=(plan.status == port_planner.PLAN_CONSTRAINT_CONFLICT),
            component_key=component_key,
            assignment_key=assignment_key,
            evidence=_port_plan_evidence(plan, issue),
            supported_fixes=_port_plan_supported_fixes(plan),
        ),
        port_plan=plan,
        batch_replays=batch_replays,
        component_replans=component_replans,
        routing_order=routing_order,
        linked_edge_pairs=linked_edge_pairs,
    )


def _record_trial_decision(context: dict, edge: dict, decision: RouteDecision) -> None:
    routed = decision.routed
    context.setdefault("paths", {})[edge["id"]] = list(routed["full_path"])
    context.setdefault("endpoints", {})[edge["id"]] = (edge["from"], edge["to"])
    if routed["label_choice"] is not None:
        context.setdefault("labels", {})[edge["id"]] = dict(routed["label_choice"][1])


def _default_route_replanner(preparation, current_plan, failed_edge_id, rejected_assignment_keys):
    component_identifier = current_plan.component_assignment_key(failed_edge_id)
    return port_planner.replan_port_plan(
        preparation,
        component_identifier,
        rejected_assignment_signatures=rejected_assignment_keys,
        previous_plan=current_plan,
    )


def _focused_route_replanner(
    preparation,
    current_plan,
    failed_edge_id: str,
    rejected_assignment_keys,
    suggested_offsets: tuple[float, float],
):
    """Try one route-supplied exact alignment without changing provenance."""
    focused_requests = []
    found = False
    for request in preparation.edge_requests:
        if request.edge_id != failed_edge_id:
            focused_requests.append(request)
            continue
        found = True
        exit_offset, entry_offset = suggested_offsets
        exit_request = request.exit
        entry_request = request.entry
        if exit_request.hard_offset is None:
            exit_request = replace(
                exit_request,
                minimum_offset=exit_offset,
                maximum_offset=exit_offset,
            )
        if entry_request.hard_offset is None:
            entry_request = replace(
                entry_request,
                minimum_offset=entry_offset,
                maximum_offset=entry_offset,
            )
        focused_requests.append(replace(request, exit=exit_request, entry=entry_request))
    if not found:
        return current_plan
    component_identifier = current_plan.component_assignment_key(failed_edge_id)
    return port_planner.replan_port_plan(
        preparation,
        component_identifier,
        rejected_assignment_signatures=rejected_assignment_keys,
        previous_plan=current_plan,
        edge_requests=tuple(focused_requests),
    )


def plan_route_batch(
    edges: list[dict],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    *,
    main_path: list[str] | None = None,
    mutable_edge_ids=None,
    locked_offsets: dict[str, tuple[float | None, float | None]] | None = None,
    routing_context: dict | None = None,
    v3_semantics: bool = False,
    port_budget=None,
    route_budget: RouteSearchBudget | None = None,
    replanner=None,
    clearance_profiles: dict[str, dict] | None = None,
) -> BatchRouteResult:
    """Jointly plan fixed ports and safe routes with bounded, atomic replays.

    The routine is deliberately pure: it does not reserve a ``PortAllocator``,
    mutate the supplied context, or write XML.  A failed replay is discarded in
    full before another port plan is considered.
    """
    if routing_context is not None and "native_label_profiles" in routing_context and replanner is None:
        return _plan_feasible_route_batch(
            edges, lanes, nodes, main_path=main_path, mutable_edge_ids=mutable_edge_ids,
            locked_offsets=locked_offsets, routing_context=routing_context,
            v3_semantics=v3_semantics, port_budget=port_budget, route_budget=route_budget,
            clearance_profiles=clearance_profiles,
        )
    edge_list = list(edges)
    requested_main_path = list(main_path or [])
    mutable = {edge["id"] for edge in edge_list} if mutable_edge_ids is None else set(mutable_edge_ids)
    active_route_budget = route_budget or RouteSearchBudget()
    linked_pairs = _linked_reciprocal_pairs(edge_list)
    base_context = new_routing_context(
        requested_main_path, edge_list, nodes, v3_semantics=v3_semantics
    )
    if routing_context is not None:
        supplied = _clone_routing_value(routing_context)
        for key in ("paths", "endpoints", "labels", "frozen_label_obstacles", "port_limits", "label_sides"):
            base_context.setdefault(key, {}).update(supplied.get(key, {}))
        if clearance_profiles is None and "arrowhead_clearance_profiles" in supplied:
            clearance_profiles = supplied["arrowhead_clearance_profiles"]
    if clearance_profiles is not None:
        base_context["arrowhead_clearance_profiles"] = _clone_routing_value(
            clearance_profiles
        )
    for key in ("paths", "endpoints", "labels", "frozen_label_obstacles"):
        for edge_id in mutable:
            base_context.setdefault(key, {}).pop(edge_id, None)
    frozen_ids = {edge["id"] for edge in edge_list} - mutable
    missing_frozen_paths = sorted(frozen_ids - set(base_context.get("paths", {})))
    if missing_frozen_paths:
        failure = RouteFailure(
            "routing/frozen-route-missing",
            missing_frozen_paths[0],
            "Frozen edges require their existing route paths in the routing context",
            locked=True,
            evidence={"edges": missing_frozen_paths},
            supported_fixes=("reroute-edge",),
        )
        return BatchRouteResult(ROUTE_FAILED, failure=failure)
    derive_port_limits(base_context, edge_list, lanes, nodes)

    endpoint_sides = {}
    node_bounds = {
        node_id: core_geometry.node_bounds_in_pool(record, lanes[record["lane"]])
        for node_id, record in nodes.items()
    }
    main_pairs = base_context.get("main_path_pairs", set())
    main_edge_ids = set()
    for edge in edge_list:
        if edge["from"] not in nodes or edge["to"] not in nodes:
            failure = RouteFailure(
                "routing/missing-endpoint", edge.get("id"),
                f"Edge {edge.get('id')} references a missing node", locked=True,
                evidence={"from": edge.get("from"), "to": edge.get("to")},
                supported_fixes=("repair-edge-endpoints",),
            )
            return BatchRouteResult(
                ROUTE_FAILED, failure=failure, linked_edge_pairs=linked_pairs
            )
        source, target = nodes[edge["from"]], nodes[edge["to"]]
        route_class = infer_route_class(edge, source, target)
        endpoint_sides[edge["id"]] = preferred_sides(
            edge,
            route_class,
            source,
            target,
            lanes,
            main_path_pairs=main_pairs,
            outgoing_counts=base_context.get("outgoing_counts", {}),
            bottom_reserved_sources=base_context.get("bottom_reserved_sources", set()),
            v3_semantics=v3_semantics,
        )
        if (edge["from"], edge["to"]) in main_pairs:
            main_edge_ids.add(edge["id"])

    requests = port_planner.collect_port_requests(
        edge_list,
        node_bounds,
        endpoint_sides,
        mutable_edge_ids=mutable,
        main_axis_edge_ids=main_edge_ids,
        locked_offsets=locked_offsets,
        offset_limits=base_context.get("port_limits", {}),
    )
    preparation = port_planner.prepare_port_plan(
        requests, budget=port_budget, linked_edge_pairs=linked_pairs
    )
    plan = port_planner.initial_port_plan(preparation)
    ordered_edges = edge_routing_order(
        [edge for edge in edge_list if edge["id"] in mutable],
        requested_main_path,
        nodes,
    )
    ordered_ids = tuple(edge["id"] for edge in ordered_edges)
    if plan.status != port_planner.PLAN_COMPLETE:
        return _port_plan_failure(
            plan, routing_order=ordered_ids, linked_edge_pairs=linked_pairs
        )

    replan = replanner or _default_route_replanner
    rejected_by_component = {}
    component_replans = {}
    batch_replays = 0
    while batch_replays < active_route_budget.max_batch_replays:
        batch_replays += 1
        assignments = plan.by_edge()
        trial_context = _clone_routing_value(base_context)
        decisions = []
        failed = None
        failed_edge = None
        for edge in ordered_edges:
            outcome = route_edge_at_ports(
                edge,
                assignments[edge["id"]],
                lanes,
                nodes,
                trial_context,
                clearance_profile=(clearance_profiles or {}).get(edge["id"]),
                require_clearance=clearance_profiles is not None,
            )
            if isinstance(outcome, RouteFailure):
                failed = outcome
                failed_edge = edge
                break
            decisions.append(outcome)
            _record_trial_decision(trial_context, edge, outcome)
        if failed is None:
            return BatchRouteResult(
                ROUTE_COMPLETE,
                decisions=decisions,
                port_plan=plan,
                batch_replays=batch_replays,
                component_replans=component_replans,
                routing_order=ordered_ids,
                linked_edge_pairs=linked_pairs,
            )

        component_edges = _component_edge_ids(plan, failed_edge["id"])
        component_key = plan.component_assignment_key(failed_edge["id"])
        assignment_key = plan.component_assignment_signature(component_key)
        failed.component_key = component_key
        failed.assignment_key = assignment_key
        rejected = rejected_by_component.setdefault(component_key, set())
        rejected.add(assignment_key)
        assignment = assignments[failed_edge["id"]]
        exit_fixed = (
            failed_edge.get("exit_offset") is not None
            or assignment.exit.source in {"explicit", "locked"}
        )
        entry_fixed = (
            failed_edge.get("entry_offset") is not None
            or assignment.entry.source in {"explicit", "locked"}
        )
        is_locked = (
            failed.locked
            or "waypoints" in failed_edge
            or failed_edge["id"] not in mutable
            or (exit_fixed and entry_fixed)
        )
        failed.locked = is_locked
        if is_locked:
            return BatchRouteResult(
                ROUTE_FAILED,
                failure=failed,
                port_plan=plan,
                batch_replays=batch_replays,
                component_replans=component_replans,
                routing_order=ordered_ids,
                linked_edge_pairs=linked_pairs,
            )

        next_plan = None
        focused_attempted = False
        while component_replans.get(component_key, 0) < active_route_budget.max_component_replans:
            component_replans[component_key] = component_replans.get(component_key, 0) + 1
            use_focused = (
                replanner is None
                and failed.suggested_offsets is not None
                and not focused_attempted
            )
            if use_focused:
                focused_attempted = True
                candidate_plan = _focused_route_replanner(
                    preparation,
                    plan,
                    failed_edge["id"],
                    tuple(sorted(rejected)),
                    failed.suggested_offsets,
                )
            else:
                candidate_plan = replan(
                    preparation,
                    plan,
                    failed_edge["id"],
                    tuple(sorted(rejected)),
                )
            if candidate_plan.status != port_planner.PLAN_COMPLETE:
                if use_focused:
                    continue
                return _port_plan_failure(
                    candidate_plan,
                    batch_replays=batch_replays,
                    component_replans=component_replans,
                    routing_order=ordered_ids,
                    linked_edge_pairs=linked_pairs,
                )
            candidate_component_edges = _component_edge_ids(candidate_plan, failed_edge["id"])
            if candidate_plan.component_assignment_key(failed_edge["id"]) != component_key:
                failure = RouteFailure(
                    "routing/port-plan-conflict",
                    failed_edge["id"],
                    "Replanning changed the failed conflict-component boundary",
                    locked=True,
                    component_key=component_key,
                    evidence={"component": component_key},
                    supported_fixes=("reroute-edge", "align-ports"),
                )
                return BatchRouteResult(
                    ROUTE_FAILED, failure=failure, port_plan=candidate_plan,
                    batch_replays=batch_replays, component_replans=component_replans,
                    routing_order=ordered_ids, linked_edge_pairs=linked_pairs,
                )
            current_other = _port_assignment_signature(
                plan, set(assignments) - set(component_edges)
            )
            candidate_other = _port_assignment_signature(
                candidate_plan, set(candidate_plan.by_edge()) - set(component_edges)
            )
            if current_other != candidate_other:
                failure = RouteFailure(
                    "routing/port-plan-conflict",
                    failed_edge["id"],
                    "Replanning changed ports outside the failed conflict component",
                    locked=True,
                    component_key=component_key,
                    evidence={"component": component_key},
                    supported_fixes=("reroute-edge", "align-ports"),
                )
                return BatchRouteResult(
                    ROUTE_FAILED, failure=failure, port_plan=candidate_plan,
                    batch_replays=batch_replays, component_replans=component_replans,
                    routing_order=ordered_ids, linked_edge_pairs=linked_pairs,
                )
            candidate_key = candidate_plan.component_assignment_signature(component_key)
            if candidate_key in rejected:
                continue
            next_plan = candidate_plan
            break
        if next_plan is None:
            failure = RouteFailure(
                "routing/port-plan-exhausted",
                failed_edge["id"],
                "No unrejected port assignment is available for the failed route component",
                component_key=component_key,
                assignment_key=assignment_key,
                evidence={
                    "component": component_key,
                    "assignment": assignment_key,
                    "rejected_assignments": len(rejected),
                },
                supported_fixes=(
                    "allocate-distinct-port", "align-ports", "reroute-edge",
                    "increase-lane-width",
                ),
            )
            return BatchRouteResult(
                ROUTE_FAILED,
                failure=failure,
                port_plan=plan,
                batch_replays=batch_replays,
                component_replans=component_replans,
                routing_order=ordered_ids,
                linked_edge_pairs=linked_pairs,
            )
        plan = next_plan

    failure = RouteFailure(
        "routing/route-search-budget",
        None,
        "Routing batch replay budget was exhausted",
        evidence={
            "batch_replays": batch_replays,
            "max_batch_replays": active_route_budget.max_batch_replays,
            "component_replans": dict(component_replans),
        },
        supported_fixes=("reroute-edge", "increase-lane-width"),
    )
    return BatchRouteResult(
        ROUTE_FAILED,
        failure=failure,
        port_plan=plan,
        batch_replays=batch_replays,
        component_replans=component_replans,
        routing_order=ordered_ids,
        linked_edge_pairs=linked_pairs,
    )


def side_pair_candidates(edge, default, lanes, nodes, *, main=False, locked=(None, None), occupied=None):
    """Finite geometric alternatives; explicit endpoints never change side."""
    if "waypoints" in edge or main:
        return (default,)
    source, target = nodes[edge["from"]], nodes[edge["to"]]
    route = infer_route_class(edge, source, target)
    indices = {lane: index for index, lane in enumerate(lanes)}
    delta = indices[target["lane"]] - indices[source["lane"]]
    facing = "left" if delta > 0 else "right" if delta < 0 else default[1]
    escape = (("top", facing) if route == "back" else ("bottom", "top") if route == "forward"
              else (("right", "left") if delta > 0 else ("left", "right")))
    fixed = tuple(edge.get(prefix + "_side") is not None or edge.get(prefix + "_offset") is not None
                  or locked[index] is not None for index, prefix in enumerate(("exit", "entry")))
    sides = ("top", "right", "bottom", "left")
    values = [default, escape]
    if route == "back" and delta:
        # Before mixing opposite source/target sides, try the source's outer
        # corridor. Opposite sides can force a loop around the entire scene.
        outer = "left" if delta > 0 else "right"
        values.append((outer, outer))
    values.extend(((escape[0], default[1]), (default[0], escape[1])))
    values.extend(sorted(((a, b) for a in sides for b in sides), key=lambda pair:
                         (sum((occupied or {}).get((edge[field], pair[index]), 0)
                              for index, field in enumerate(("from", "to"))),
                          sum(pair[index] != default[index] for index in (0, 1)),
                          sides.index(pair[0]), sides.index(pair[1]))))
    return tuple(dict.fromkeys(pair for pair in values
                               if all(not fixed[index] or pair[index] == default[index] for index in (0, 1))))


def _side_domain_fits(edge_id, pair, requests):
    """Prune only proven singleton clashes for this one-side-change trial."""
    own = next(request for request in requests if request.edge_id == edge_id)
    for index, endpoint in enumerate((own.exit, own.entry)):
        changed = replace(endpoint, side=pair[index])
        value = changed.hard_offset
        if value is None and changed.supported_offsets is not None and len(changed.supported_offsets) == 1:
            value = changed.supported_offsets[0]
        if value is None:
            continue
        for request in requests:
            if request.edge_id == edge_id:
                continue
            for other in (request.exit, request.entry):
                other_value = other.hard_offset
                if other_value is None and other.supported_offsets is not None and len(other.supported_offsets) == 1:
                    other_value = other.supported_offsets[0]
                if (other.node_id, other.side) == (changed.node_id, changed.side) and other_value is not None:
                    if not port_planner._pair_offsets_compatible(changed, value, other, other_value,
                                                               core_geometry.GEOMETRY_TOLERANCE / 100):
                        return False
    return True


def _port_dependency_closure(edge_ids, requests, links, *, prospective=None):
    """Include actual current/prospective port coupling, never entire lanes."""
    members = set(edge_ids)
    groups = {}
    for request in requests:
        for endpoint in (request.exit, request.entry):
            groups.setdefault((endpoint.node_id, endpoint.side), set()).add(request.edge_id)
            if prospective and request.edge_id in prospective:
                side = prospective[request.edge_id][0 if endpoint.endpoint == "exit" else 1]
                groups.setdefault((endpoint.node_id, side), set()).add(request.edge_id)
    sets = [*groups.values(), *(set(pair) for pair in links)]
    changed = True
    while changed:
        before = set(members)
        for group in sets:
            if members & group:
                members.update(group)
        changed = members != before
    return members


def _supported_offsets(profiles, edges):
    domains = {}
    for edge in edges:
        if "waypoints" in edge:
            continue
        for endpoint, terminal in profiles.get(edge["id"], {}).get("terminals", {}).items():
            kind = terminal.get("type")
            style = terminal.get("style", "")
            flags = {part for part in style.split(";") if part and "=" not in part}
            values = {part.split("=", 1)[0]: part.split("=", 1)[1] for part in style.split(";") if "=" in part}
            if (kind == "decision" and flags == {"rhombus"}
                    or kind in {"start", "end"} and flags == {"ellipse"} and values.get("aspect") == "fixed"):
                if not any(key in values for key in ("shape", "rotation", "perimeter", "direction", "flipH", "flipV", "perimeterSpacing")):
                    domains.setdefault(edge["id"], {})[endpoint] = (.5,)
    return domains


def _plan_feasible_route_batch(edges, lanes, nodes, *, main_path=None, mutable_edge_ids=None,
                               locked_offsets=None, routing_context=None, v3_semantics=False,
                               port_budget=None, route_budget=None, clearance_profiles=None):
    """Bounded repair of automatic candidates with immutable external obstacles.

    Every replay has one finite state. Previously accepted independent decisions
    and endpoint assignments are carried forward, not reoptimized. No caller
    objects, XML, saved paths, or explicit authoring fields are changed.
    """
    edge_list = list(edges)
    by_id = {edge["id"]: edge for edge in edge_list}
    mutable = set(by_id) if mutable_edge_ids is None else set(mutable_edge_ids)
    main_path = list(main_path or [])
    budget = route_budget or RouteSearchBudget()
    base = new_routing_context(main_path, edge_list, nodes, v3_semantics=v3_semantics)
    supplied = _clone_routing_value(routing_context or {})
    for key in ("paths", "endpoints", "labels", "frozen_label_obstacles", "port_limits", "label_sides"):
        base[key].update(supplied.get(key, {}))
        if key in {"paths", "endpoints", "labels", "frozen_label_obstacles"}:
            for edge_id in mutable:
                base[key].pop(edge_id, None)
    profiles = supplied.get("native_label_profiles", {})
    missing_profiles = sorted(edge["id"] for edge in edge_list
                              if edge["id"] in mutable and "waypoints" not in edge
                              and not profiles.get(edge["id"]))
    if missing_profiles:
        return BatchRouteResult(ROUTE_FAILED, failure=RouteFailure("routing/no-safe-route", missing_profiles[0],
                "Automatic routes require native candidate profiles", locked=True,
                evidence={"planning": {"version": 1, "stage": "candidate", "reason": "native_profile_unavailable",
                                       "missing_profiles": missing_profiles}}))
    clearance_profiles = clearance_profiles if clearance_profiles is not None else supplied.get("arrowhead_clearance_profiles")
    base["native_label_profiles"] = profiles
    frozen = set(by_id) - mutable
    missing = sorted(frozen - set(base["paths"]))
    if missing:
        return BatchRouteResult(ROUTE_FAILED, failure=RouteFailure("routing/frozen-route-missing", missing[0],
                                "Frozen edges require existing paths", locked=True, evidence={"edges": missing}))
    ordered = edge_routing_order([edge for edge in edge_list if edge["id"] in mutable], main_path, nodes)
    ordered_ids = tuple(edge["id"] for edge in ordered)
    order = {edge_id: index for index, edge_id in enumerate(ordered_ids)}
    main_ids = {edge["id"] for edge in edge_list if (edge["from"], edge["to"]) in base["main_path_pairs"]}
    bounds = {key: core_geometry.node_bounds_in_pool(node, lanes[node["lane"]]) for key, node in nodes.items()}
    links = _linked_reciprocal_pairs(edge_list)
    default_sides = {}
    for edge in edge_list:
        if edge["from"] not in nodes or edge["to"] not in nodes:
            return BatchRouteResult(ROUTE_FAILED, failure=RouteFailure("routing/missing-endpoint", edge["id"],
                                    "Edge references a missing endpoint", locked=True))
        default_sides[edge["id"]] = preferred_sides(
            edge, infer_route_class(edge, nodes[edge["from"]], nodes[edge["to"]]),
            nodes[edge["from"]], nodes[edge["to"]], lanes, main_path_pairs=base["main_path_pairs"],
            outgoing_counts=base["outgoing_counts"], bottom_reserved_sources=base["bottom_reserved_sources"],
            v3_semantics=v3_semantics)
    side_options = {edge["id"]: side_pair_candidates(edge, default_sides[edge["id"]], lanes, nodes,
                    main=edge["id"] in main_ids or edge["id"] not in mutable,
                    locked=(locked_offsets or {}).get(edge["id"], (None, None)),
                    occupied={key: sum(other["id"] != edge["id"] and
                               (other[field], default_sides[other["id"]][index]) == key
                               for other in edge_list for index, field in enumerate(("from", "to")))
                              for key in ((node_id, side) for node_id in nodes for side in ("top", "right", "bottom", "left"))})
                    for edge in edge_list}
    sides = dict(default_sides)
    side_indices = dict.fromkeys(by_id, 0)
    domains = _supported_offsets(profiles, ordered)
    derive_port_limits(base, edge_list, lanes, nodes)
    requests = port_planner.collect_port_requests(edge_list, bounds, sides, mutable_edge_ids=mutable,
                main_axis_edge_ids=main_ids, locked_offsets=locked_offsets,
                offset_limits=base["port_limits"], supported_offsets=domains)
    seed_for = {}
    for edge_id in sorted(by_id):
        component = tuple(sorted(_port_dependency_closure({edge_id}, requests, links)))
        seed_for[edge_id] = component
    replans = {}
    evaluated = 0
    label_pairs_used = 0
    stable = {}
    assignments = {}
    locked_labels = {}
    rejected_paths = {}
    previous_paths = dict(base["paths"])
    offset_attempted = set()
    pending_port_rejection = None
    pending_port_failure = None
    visited = set()
    history = []
    active = set(mutable)
    plan = None
    failure = None

    def finish(status, replay, *, choices=None):
        planning = {"version": 1, "candidate_evaluations": evaluated,
                    "label_pair_attempts": label_pairs_used,
                    "history": history[-32:], "history_count": len(history),
                    "truncated": len(history) > 32,
                    "budgets": {name: getattr(budget, name) for name in budget.__slots__}}
        if failure is not None:
            detail = failure.evidence.setdefault("planning", {})
            detail.update(planning)
        return BatchRouteResult(status, decisions=tuple(stable[key] for key in ordered_ids if key in stable) if status == ROUTE_COMPLETE else (),
                    failure=failure, port_plan=plan, batch_replays=replay, component_replans=replans,
                    routing_order=ordered_ids, linked_edge_pairs=links, label_choices=choices, planning=planning)

    for replay in range(1, budget.max_batch_replays + 1):
        locks = dict(locked_offsets or {})
        for edge_id, assignment in assignments.items():
            if edge_id not in active:
                locks[edge_id] = assignment.exit.offset, assignment.entry.offset
        requests = port_planner.collect_port_requests(edge_list, bounds, sides, mutable_edge_ids=mutable,
                    main_axis_edge_ids=main_ids, locked_offsets=locks,
                    offset_limits=base["port_limits"], supported_offsets=domains)
        preparation = port_planner.prepare_port_plan(requests, budget=port_budget, linked_edge_pairs=links)
        plan = port_planner.initial_port_plan(preparation)
        retry_failure = pending_port_failure
        pending_port_failure = None
        if pending_port_rejection is not None and plan.status == port_planner.PLAN_COMPLETE:
            pivot, key = pending_port_rejection
            plan = port_planner.replan_port_plan(preparation, plan.component_assignment_key(pivot),
                        previous_plan=plan, rejected_assignment_keys=(key,))
        pending_port_rejection = None
        failure = None
        context = _clone_routing_value(base)
        context["native_label_profiles"] = profiles
        context["candidate_budget"] = {"evaluations": evaluated,
             "max_path_candidates": budget.max_path_candidates,
             "max_candidate_evaluations": budget.max_candidate_evaluations}
        context["rejected_paths"] = rejected_paths
        context["rejection_paths"] = previous_paths
        context["rejection_bounds"] = bounds
        # Later stable routes are obstacles even before their turn in order.
        for key, decision in stable.items():
            if key not in active:
                _record_trial_decision(context, by_id[key], decision)
        if plan.status != port_planner.PLAN_COMPLETE:
            if retry_failure is not None and plan.status == port_planner.PLAN_CANDIDATE_EXHAUSTED:
                failure = retry_failure
                failure.evidence["port_retry"] = _port_plan_evidence(plan)
                subjects = {failure.edge_id}
            elif plan.status in {port_planner.PLAN_CONSTRAINT_CONFLICT, port_planner.PLAN_BUDGET_EXHAUSTED}:
                return _port_plan_failure(plan, batch_replays=replay, component_replans=replans,
                                          routing_order=ordered_ids, linked_edge_pairs=links)
            if failure is None:
                failure = _port_plan_failure(plan).failure
                subjects = set(plan.issues[0].edge_ids) if plan.issues else set(active)
            issue = plan.issues[0] if plan.issues and retry_failure is None else None
            if issue is not None and issue.node_id is not None and issue.side is not None:
                # Changing the unrelated endpoint cannot solve this domain.
                can_change_side = any(
                    any(pair[index] != issue.side for pair in side_options[request.edge_id])
                    for request in requests if request.edge_id in subjects
                    for index, endpoint in enumerate((request.exit, request.entry))
                    if endpoint.node_id == issue.node_id and endpoint.side == issue.side)
                if not can_change_side:
                    return finish(ROUTE_FAILED, replay)
        else:
            assignments = plan.by_edge()
            for edge in ordered:
                edge_id = edge["id"]
                if edge_id in stable and edge_id not in active:
                    outcome = stable[edge_id]
                else:
                    context["candidate_budget"]["evaluations"] = evaluated
                    outcome = route_edge_at_ports(edge, assignments[edge_id], lanes, nodes, context,
                                clearance_profile=(clearance_profiles or {}).get(edge_id),
                                require_clearance=clearance_profiles is not None)
                    if isinstance(outcome, RouteFailure):
                        evaluated += outcome.evidence.get("planning", {}).get("counts", {}).get("candidate_evaluations", 0)
                        failure = outcome
                        subjects = {edge_id}
                        break
                    evaluated += outcome.routed.get("candidate_evaluations", 0)
                stable[edge_id] = outcome
                previous_paths[edge_id] = outcome.routed.get("native_path", outcome.routed["full_path"])
                _record_trial_decision(context, edge, outcome)
            if failure is None:
                # Recheck every accepted automatic route against the complete
                # batch, including explicit and saved routes routed later.
                pair_context = _clone_routing_value(context)
                pair_context["paths"].update(previous_paths)
                for edge in ordered:
                    if "waypoints" in edge:
                        continue
                    decision = stable[edge["id"]]
                    issues = _candidate_quality(edge, assignments[edge["id"]],
                            decision.routed.get("native_path", decision.routed["full_path"]),
                            lanes, nodes, pair_context)
                    if issues:
                        failure = _native_candidate_failure(edge, issues, {})
                        subjects = {edge["id"]}
                        break
            if failure is None:
                items = [{"id": edge["id"], "text": edge.get("label", ""), "route": stable[edge["id"]].routed["route"],
                          "assignment": assignments[edge["id"]], "hints": stable[edge["id"]].routed["points"],
                          "profile": profiles[edge["id"]], "native": stable[edge["id"]].routed["native_measurement"]}
                         for edge in ordered if "native_measurement" in stable[edge["id"]].routed]
                paths = dict(base["paths"])
                paths.update({key: value.routed.get("native_path", value.routed["full_path"]) for key, value in stable.items()})
                container = {"left": 0., "top": 0.,
                    "right": max(lane["geometry"]["x"] + lane["geometry"]["width"] for lane in lanes.values()),
                    "bottom": max(lane["geometry"]["y"] + lane["geometry"]["height"] for lane in lanes.values())}
                label_result = labels.plan_label_batch(items, paths, bounds, container,
                                frozen_labels=base["frozen_label_obstacles"], preferred_sides=base["label_sides"],
                                locked_choices=locked_labels, max_label_pairs=budget.max_label_pairs,
                                max_batch_label_pairs=budget.max_batch_label_pairs - label_pairs_used)
                label_pairs_used += label_result["pair_attempts"]
                if label_result["status"] == "complete":
                    return finish(ROUTE_COMPLETE, replay, choices=label_result["choices"])
                detail = label_result["failure"]
                locked_labels.update(label_result.get("provisional_choices", {}))
                failure = RouteFailure("routing/route-search-budget" if detail["reason"] == "budget_exhausted" else
                        "routing/no-safe-route", detail["edge_id"], "No clear native label placement for this route batch",
                        evidence={"planning": dict(detail, version=1, stage="label_batch")},
                        supported_fixes=("move-edge-label", "reroute-edge"))
                subjects = {detail["edge_id"]}
        detail = failure.evidence.get("planning", {})
        blockers = set(detail.get("blocking_edge_ids", ())) | set(detail.get("blocking_label_ids", ()))
        if failure.code == "routing/route-search-budget":
            return finish(ROUTE_FAILED, replay)
        if failure.locked and detail.get("reason") == "native_profile_unavailable":
            return finish(ROUTE_FAILED, replay)
        # A label failure first retries one mutable owner against all other
        # accepted geometry; geometric blockers join only if that retry fails.
        label_pivot = None
        if detail.get("stage") == "label_batch":
            candidates = sorted((blockers | subjects) & mutable, key=lambda key: order.get(key, -1), reverse=True)
            label_pivot = next((key for key in candidates if key in stable and "waypoints" not in by_id[key]), None)
        subjects = ({label_pivot} if label_pivot else subjects | blockers) & set(by_id)
        if not subjects:
            subjects = set(active)
        closure = _port_dependency_closure(subjects, requests, links) & mutable
        history.append({"replay": replay, "failure": failure.code, "reason": detail.get("reason"),
                        "edges": sorted(subjects), "closure": sorted(closure),
                        "sides": {key: sides[key] for key in sorted(closure)}})
        seeds = {seed_for[key] for key in closure}
        if not closure or any(replans.get(seed, 0) >= budget.max_component_replans for seed in seeds):
            if closure:
                failure = RouteFailure("routing/route-search-budget", failure.edge_id,
                            "Seed component repair budget was exhausted", evidence={"planning": {
                            "reason": "budget_exhausted", "budget": "component_replans", "last_failure": failure.evidence}})
            return finish(ROUTE_FAILED, replay)
        # One contextual offset retry precedes changing sides. Reject only this
        # exact assignment, with the same obstacle snapshot; never carry it
        # across topology changes.
        pivot = failure.edge_id
        obstacle_key = tuple((key, tuple(value.routed["full_path"])) for key, value in sorted(stable.items()) if key not in closure)
        offset_key = (pivot, tuple(sorted(sides.items())), obstacle_key)
        changed = False
        if failure.suggested_offsets is not None and plan.status == port_planner.PLAN_COMPLETE:
            for index, endpoint in enumerate(("exit", "entry")):
                if getattr(assignments[pivot], endpoint).source not in {"explicit", "locked"}:
                    value = failure.suggested_offsets[index]
                    base["port_limits"].setdefault(pivot, {})[endpoint] = {"min": value, "max": value}
            changed = True
        elif label_pivot is not None:
            key = label_pivot
            rejection = _route_rejection_key(key, assignments[key], stable[key].routed["points"],
                            profiles, bounds, previous_paths, base["frozen_label_obstacles"])
            if rejection not in rejected_paths.get(key, set()):
                rejected_paths.setdefault(key, set()).add(rejection)
                changed = True
        elif (plan.status == port_planner.PLAN_COMPLETE and pivot in assignments and
              offset_key not in offset_attempted and "waypoints" not in by_id[pivot] and
              not failure.locked):
            offset_attempted.add(offset_key)
            pending_port_rejection = pivot, assignments[pivot].assignment_key
            pending_port_failure = failure
            changed = True
        if not changed:
            # Port-coupled returns are considered before changing established
            # main carriers. This applies to topology, not particular IDs.
            pivots = sorted(closure, key=lambda key: (key in main_ids, -order.get(key, -1)))
            for key in pivots:
                while side_indices[key] + 1 < len(side_options[key]):
                    side_indices[key] += 1
                    proposed = side_options[key][side_indices[key]]
                    if not _side_domain_fits(key, proposed, requests):
                        history[-1]["side_domain_rejections"] = history[-1].get("side_domain_rejections", 0) + 1
                        continue
                    sides[key] = proposed
                    closure = _port_dependency_closure(closure, requests, links, prospective={key: sides[key]}) & mutable
                    rejected_paths = {}
                    changed = True
                    break
                if changed:
                    break
        if not changed:
            return finish(ROUTE_FAILED, replay)
        # Charge every original seed in the *expanded* dependency closure.
        seeds = {seed_for[key] for key in closure}
        if any(replans.get(seed, 0) >= budget.max_component_replans for seed in seeds):
            failure = RouteFailure("routing/route-search-budget", failure.edge_id,
                    "Seed component repair budget was exhausted", evidence={"planning": {
                    "reason": "budget_exhausted", "budget": "component_replans"}})
            return finish(ROUTE_FAILED, replay)
        for seed in seeds:
            replans[seed] = replans.get(seed, 0) + 1
        history[-1]["closure"] = sorted(closure)
        for key in closure:
            stable.pop(key, None)
            locked_labels.pop(key, None)
        active = closure
        state = (tuple(sorted(sides.items())), pending_port_rejection,
                 tuple((key, tuple((end, tuple(sorted(limits.items()))) for end, limits in sorted(value.items())))
                       for key, value in sorted(base["port_limits"].items())),
                 tuple((key, tuple(sorted(value))) for key, value in sorted(rejected_paths.items())),
                 tuple(sorted((key, value.assignment_key) for key, value in assignments.items() if key not in active)))
        if state in visited:
            failure = RouteFailure("routing/route-search-budget", failure.edge_id,
                       "Repeated repair state rejected", evidence={"planning": {"reason": "budget_exhausted", "budget": "repeated_state"}})
            return finish(ROUTE_FAILED, replay)
        visited.add(state)
    failure = RouteFailure("routing/route-search-budget", failure.edge_id if failure else None,
                "Routing batch replay budget was exhausted", evidence={"planning": {"reason": "budget_exhausted", "budget": "batch_replays"}})
    return finish(ROUTE_FAILED, budget.max_batch_replays)


def polyline_is_locally_valid(
    points: list[tuple[float, float]],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    source_id: str | None,
    target_id: str | None,
) -> bool:
    if len(points) < 2:
        return False
    boundaries = internal_lane_boundaries(lanes)
    node_bounds = {
        semantic_id: core_geometry.node_bounds_in_pool(record, lanes[record["lane"]])
        for semantic_id, record in nodes.items()
    }
    for segment in zip(points, points[1:]):
        axis = core_geometry.segment_axis(segment)
        if axis == "diagonal":
            return False
        if axis == "vertical" and any(
            abs(segment[0][0] - boundary) < routing_policy.LANE_BOUNDARY_CLEARANCE
            for boundary in boundaries
        ):
            return False
        for node_id, bounds in node_bounds.items():
            if node_id in {source_id, target_id}:
                continue
            if core_geometry.segment_crosses_bounds(segment, bounds):
                return False
    return True
