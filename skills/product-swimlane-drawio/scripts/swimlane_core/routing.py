"""Routing decisions over plain node/lane views and explicit per-operation state."""

from __future__ import annotations

from . import contracts, geometry as core_geometry, labels, ports, routing_policy


ROUTE_BEND_PENALTY = 32.0
ROUTE_CONFLICT_PENALTY = 4000.0
ROUTE_LABEL_CONFLICT_PENALTY = 2500.0
POOL_EDGE_MARGIN = 8.0


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


def route_edge(
    edge: dict,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    allocator: ports.PortAllocator,
    routing_context: dict | None = None,
) -> dict:
    context = routing_context or {}
    if edge["from"] not in nodes or edge["to"] not in nodes:
        raise contracts.DiagramError(f"Edge {edge.get('id')} references a missing node")
    source = nodes[edge["from"]]
    target = nodes[edge["to"]]
    source_lane = lanes[source["lane"]]
    target_lane = lanes[target["lane"]]
    route_class = infer_route_class(edge, source, target)
    main_path_pairs = context.get("main_path_pairs", set())
    outgoing_counts = context.get("outgoing_counts", {})
    exit_side, entry_side = preferred_sides(
        edge,
        route_class,
        source,
        target,
        lanes,
        main_path_pairs=main_path_pairs,
        outgoing_counts=outgoing_counts,
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
            safe_candidates = [core_geometry.compact_points([source_point, *base_points, target_point])]
        is_main_path = (edge["from"], edge["to"]) in main_path_pairs
        source_rank = int(source["semantic"].get("rank", "0"))
        target_rank = int(target["semantic"].get("rank", "0"))
        same_lane_down = source["lane"] == target["lane"] and target_rank > source_rank
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
    return {
        "points": points,
        "route": route_class,
        "exit_side": exit_side,
        "entry_side": entry_side,
        "exit_offset": exit_offset,
        "entry_offset": entry_offset,
        "full_path": core_geometry.compact_points([source_point, *points, target_point]),
        "label_choice": label_choice,
    }


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
