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

    __slots__ = ("max_component_replans", "max_batch_replays")

    def __init__(self, max_component_replans: int = 6, max_batch_replays: int = 64) -> None:
        component_replans = int(max_component_replans)
        batch_replays = int(max_batch_replays)
        if component_replans <= 0 or batch_replays <= 0:
            raise contracts.DiagramError("Route search budgets must be positive")
        self.max_component_replans = component_replans
        self.max_batch_replays = batch_replays


class BatchRouteResult:
    """Pure, all-or-nothing result of one bounded port/route planning batch."""

    __slots__ = (
        "status", "decisions", "failure", "port_plan", "batch_replays",
        "component_replans", "routing_order", "linked_edge_pairs",
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
    ) -> None:
        self.status = status
        self.decisions = tuple(decisions)
        self.failure = failure
        self.port_plan = port_plan
        self.batch_replays = int(batch_replays)
        self.component_replans = dict(component_replans or {})
        self.routing_order = tuple(routing_order)
        self.linked_edge_pairs = tuple(linked_edge_pairs)


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
            and target_type == "end"
            and same_lane_down
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

    if route_class == "back" and exit_side == entry_side and entry_side in {"left", "right"}:
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
    if route_class == "back":
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
        safe_candidates = [
            candidate
            for candidate in candidates
            if automatic_polyline_is_safe(
                candidate, lanes, nodes, edge["from"], edge["to"]
            )
        ]
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
                    },
                    supported_fixes=(
                        "allocate-distinct-port", "align-ports", "reroute-edge",
                        "increase-lane-width",
                    ),
                )
            safe_candidates = [core_geometry.compact_points([source_point, *base_points, target_point])]
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
                    evidence={"suggested_offsets": suggested_offsets},
                    supported_fixes=("align-ports", "reroute-edge"),
                )

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
        for key in ("paths", "endpoints", "labels", "port_limits", "label_sides"):
            base_context.setdefault(key, {}).update(supplied.get(key, {}))
        if clearance_profiles is None and "arrowhead_clearance_profiles" in supplied:
            clearance_profiles = supplied["arrowhead_clearance_profiles"]
    if clearance_profiles is not None:
        base_context["arrowhead_clearance_profiles"] = _clone_routing_value(
            clearance_profiles
        )
    for key in ("paths", "endpoints", "labels"):
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
