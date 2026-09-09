"""Pure canvas and node-position calculations for the swimlane compiler."""

from __future__ import annotations

import math

from . import (
    clearance, contracts, geometry as core_geometry, labels, routing, routing_policy,
    sizing,
)


DEFAULTS = {
    "x": 40,
    "y": 40,
    "title_height": 36,
    "lane_header_height": 32,
    "row_gap": 96,
    "top_padding": 40,
    "bottom_padding": 52,
}
SLOT_ORDER = {"left": 0, "main": 1, "right": 2}
SLOT_GAP = 20.0
SLOT_SIDE_PADDING = 20.0
PROFILE_SLOT_GAPS = {"compact": 20.0, "review": 32.0, "long-form": 40.0}
PROFILE_SIDE_PADDING = {"compact": 20.0, "review": 24.0, "long-form": 32.0}
PROFILE_ROW_GAPS = {"compact": 80.0, "review": 96.0, "long-form": 104.0}
PHASE_RAIL_WIDTH = 76.0


def canvas_values(spec: dict) -> dict:
    values = dict(DEFAULTS)
    values.update(spec.get("canvas", {}))
    return values


def layout_profile(spec: dict) -> str:
    if spec.get("schema_version") != contracts.V3_SCHEMA_VERSION:
        return "legacy"
    return spec.get("layout", {}).get("profile", "review")


def profile_slot_gap(spec: dict) -> float:
    profile = layout_profile(spec)
    return PROFILE_SLOT_GAPS.get(profile, SLOT_GAP)


def profile_side_padding(spec: dict) -> float:
    profile = layout_profile(spec)
    return PROFILE_SIDE_PADDING.get(profile, SLOT_SIDE_PADDING)


def effective_node_slot(node: dict) -> str:
    """Return the semantic horizontal slot used by the v3 compiler."""
    if "slot" in node:
        return node["slot"]
    if node.get("anchor"):
        return node["anchor"]["side"]
    return "main"


def v3_slot_row_required_width(
    row_nodes: list[dict],
    *,
    gap: float,
    side_padding: float,
) -> float:
    left_extent, right_extent = v3_slot_row_extents(row_nodes, gap=gap)
    return 2 * side_padding + left_extent + right_extent


def v3_slot_row_extents(
    row_nodes: list[dict],
    *,
    gap: float,
) -> tuple[float, float]:
    by_slot = {effective_node_slot(node): node for node in row_nodes}
    if "main" not in by_slot:
        content = sum(sizing.node_size(node)[0] for node in row_nodes) + gap * max(
            0, len(row_nodes) - 1
        )
        return content / 2.0, content / 2.0

    main_width = sizing.node_size(by_slot["main"])[0]
    left_extent = main_width / 2.0
    right_extent = main_width / 2.0
    if "left" in by_slot:
        left_extent += gap + sizing.node_size(by_slot["left"])[0]
    if "right" in by_slot:
        right_extent += gap + sizing.node_size(by_slot["right"])[0]
    return left_extent, right_extent


def v3_lane_main_axes(
    spec: dict,
    lane_widths: dict[str, float],
) -> dict[str, float]:
    gap = profile_slot_gap(spec)
    side_padding = profile_side_padding(spec)
    by_lane_rank: dict[tuple[str, int], list[dict]] = {}
    for node in spec["nodes"]:
        if "x" in node:
            continue
        by_lane_rank.setdefault((node["lane"], int(node["rank"])), []).append(node)

    extents: dict[str, tuple[float, float]] = {}
    for (lane_id, _rank), row_nodes in by_lane_rank.items():
        if not any(effective_node_slot(node) == "main" for node in row_nodes):
            continue
        left, right = v3_slot_row_extents(row_nodes, gap=gap)
        previous = extents.get(lane_id, (0.0, 0.0))
        extents[lane_id] = max(previous[0], left), max(previous[1], right)

    axes: dict[str, float] = {}
    for lane_id, (left, right) in extents.items():
        spare = max(0.0, lane_widths[lane_id] - left - right - 2 * side_padding)
        axes[lane_id] = side_padding + spare / 2.0 + left
    return axes


def v3_node_x_positions(
    spec: dict,
    lane_widths: dict[str, float],
) -> dict[str, float]:
    """Compile left/main/right slots into deterministic lane-local positions."""
    if spec.get("schema_version") != contracts.V3_SCHEMA_VERSION:
        return {}
    gap = profile_slot_gap(spec)
    lane_axes = v3_lane_main_axes(spec, lane_widths)

    positions: dict[str, float] = {}
    by_lane_rank: dict[tuple[str, int], list[dict]] = {}
    for node in spec["nodes"]:
        if "x" in node:
            continue
        by_lane_rank.setdefault((node["lane"], int(node["rank"])), []).append(node)

    for (lane_id, _rank), row_nodes in by_lane_rank.items():
        ordered = sorted(row_nodes, key=lambda item: SLOT_ORDER[effective_node_slot(item)])
        by_slot = {effective_node_slot(node): node for node in ordered}
        if "main" in by_slot:
            main = by_slot["main"]
            main_width = sizing.node_size(main)[0]
            main_x = lane_axes.get(lane_id, lane_widths[lane_id] / 2.0) - main_width / 2.0
            positions[main["id"]] = main_x
            if "left" in by_slot:
                left = by_slot["left"]
                positions[left["id"]] = main_x - gap - sizing.node_size(left)[0]
            if "right" in by_slot:
                right = by_slot["right"]
                positions[right["id"]] = main_x + main_width + gap
        else:
            widths = [sizing.node_size(node)[0] for node in ordered]
            content_width = sum(widths) + gap * max(0, len(ordered) - 1)
            cursor = (lane_widths[lane_id] - content_width) / 2.0
            for node, width in zip(ordered, widths):
                positions[node["id"]] = cursor
                cursor += width + gap
    return positions


def effective_lane_widths(spec: dict) -> dict[str, float]:
    """Expand automatic-layout lanes enough to host internal back-route gutters."""
    widths = {
        lane["id"]: float(lane.get("width", 200))
        for lane in spec["lanes"]
    }
    nodes = {node["id"]: node for node in spec["nodes"]}
    required_gutter = (
        routing_policy.LANE_BOUNDARY_CLEARANCE
        + clearance.CLEARANCE_THRESHOLD_PX
        + core_geometry.GEOMETRY_TOLERANCE
    )

    for node in spec["nodes"]:
        if "x" in node:
            continue
        node_width, _ = sizing.node_size(node)
        widths[node["lane"]] = max(
            widths[node["lane"]],
            math.ceil(node_width + 8.0),
        )

    if spec.get("schema_version") == contracts.V3_SCHEMA_VERSION:
        gap = profile_slot_gap(spec)
        side_padding = profile_side_padding(spec)
        by_lane_rank: dict[tuple[str, int], list[dict]] = {}
        for node in spec["nodes"]:
            if "x" in node:
                continue
            by_lane_rank.setdefault((node["lane"], int(node["rank"])), []).append(node)
        for (lane_id, _rank), row_nodes in by_lane_rank.items():
            required_width = v3_slot_row_required_width(
                row_nodes,
                gap=gap,
                side_padding=side_padding,
            )
            widths[lane_id] = max(widths[lane_id], float(math.ceil(required_width)))

    for edge in spec["edges"]:
        if routing.inferred_spec_route_class(edge, nodes) != "back":
            continue
        target = nodes[edge["to"]]
        if "x" in target:
            continue
        target_width, _ = sizing.node_size(target)
        minimum_width = math.ceil(
            target_width + 2 * required_gutter + core_geometry.GEOMETRY_TOLERANCE
        )
        widths[target["lane"]] = max(widths[target["lane"]], float(minimum_width))

        if spec.get("schema_version") == contracts.V3_SCHEMA_VERSION:
            required_side_space = (
                routing_policy.LANE_BOUNDARY_CLEARANCE + routing_policy.ROUTE_CLEARANCE + core_geometry.GEOMETRY_TOLERANCE
            )
            for endpoint in (nodes[edge["from"]], target):
                if "x" in endpoint:
                    continue
                endpoint_width, _ = sizing.node_size(endpoint)
                endpoint_minimum = math.ceil(
                    endpoint_width + 2 * required_side_space
                )
                widths[endpoint["lane"]] = max(
                    widths[endpoint["lane"]],
                    float(endpoint_minimum),
                )

    return widths


def adaptive_canvas_values(spec: dict) -> dict:
    """Increase automatic rank spacing when nodes and edge labels need more room."""
    values = canvas_values(spec)
    if "row_gap" in spec.get("canvas", {}):
        return values
    if spec.get("schema_version") == contracts.V3_SCHEMA_VERSION:
        values["row_gap"] = PROFILE_ROW_GAPS[layout_profile(spec)]

    rank_heights: dict[int, float] = {}
    for node in spec["nodes"]:
        _, height = sizing.node_size(node)
        rank = int(node["rank"])
        rank_heights[rank] = max(rank_heights.get(rank, 0.0), height)

    required = float(values["row_gap"])
    labeled_pairs = {
        (int(next(node for node in spec["nodes"] if node["id"] == edge["from"])["rank"]),
         int(next(node for node in spec["nodes"] if node["id"] == edge["to"])["rank"]))
        for edge in spec["edges"]
        if str(edge.get("label", "")).strip()
    }
    for rank in sorted(rank_heights):
        next_rank = rank + 1
        if next_rank not in rank_heights:
            continue
        label_space = labels.EDGE_LABEL_HEIGHT + 4.0 if (rank, next_rank) in labeled_pairs else 16.0
        needed = rank_heights[rank] / 2 + rank_heights[next_rank] / 2 + label_space
        required = max(required, needed)
    values["row_gap"] = math.ceil(required / 8.0) * 8.0
    return values


def lane_height(max_rank: int, values: dict) -> float:
    content = values["top_padding"] + max(0, max_rank - 1) * values["row_gap"]
    return values["lane_header_height"] + content + 40 + values["bottom_padding"]


def node_y(node: dict, values: dict) -> float:
    _, height = sizing.node_size(node)
    center = values["lane_header_height"] + values["top_padding"] + (int(node["rank"]) - 1) * values["row_gap"]
    return float(node.get("y", center - height / 2))
