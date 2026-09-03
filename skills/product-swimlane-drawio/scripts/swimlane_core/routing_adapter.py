"""Native edge styles, XML writeback, and routing state integration."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from . import contracts, document, geometry as core_geometry, labels, ports, routing


def edge_style(
    edge_type: str,
    exit_side: str,
    entry_side: str,
    exit_offset: float,
    entry_offset: float,
) -> str:
    exit_x, exit_y = core_geometry.port_xy(exit_side, exit_offset)
    entry_x, entry_y = core_geometry.port_xy(entry_side, entry_offset)
    extra = "dashed=1;" if edge_type == "async" else ""
    return (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
        "endArrow=block;endFill=1;labelBackgroundColor=#ffffff;fontSize=11;"
        f"exitX={contracts.number(exit_x)};exitY={contracts.number(exit_y)};exitDx=0;exitDy=0;"
        f"entryX={contracts.number(entry_x)};entryY={contracts.number(entry_y)};entryDx=0;entryDy=0;{extra}"
    )


def set_edge_label_position(
    cell: ET.Element,
    full_path: list[tuple[float, float]],
    label_choice: tuple[int, dict[str, float]] | None,
) -> None:
    for key in (
        contracts.DATA_LABEL_LEFT,
        contracts.DATA_LABEL_TOP,
        contracts.DATA_LABEL_WIDTH,
        contracts.DATA_LABEL_HEIGHT,
        contracts.DATA_LABEL_SEGMENT,
    ):
        cell.attrib.pop(key, None)
    geom = cell.find("mxGeometry")
    if geom is None:
        return
    existing_offset = geom.find("./mxPoint[@as='offset']")
    if existing_offset is not None:
        geom.remove(existing_offset)
    if label_choice is None:
        return
    segment_index, box = label_choice
    cell.attrib.update(
        {
            contracts.DATA_LABEL_LEFT: contracts.number(box["left"]),
            contracts.DATA_LABEL_TOP: contracts.number(box["top"]),
            contracts.DATA_LABEL_WIDTH: contracts.number(box["width"]),
            contracts.DATA_LABEL_HEIGHT: contracts.number(box["height"]),
            contracts.DATA_LABEL_SEGMENT: str(segment_index),
        }
    )
    midpoint = labels.polyline_midpoint(full_path)
    desired = (
        box["left"] + box["width"] / 2,
        box["top"] + box["height"] / 2,
    )
    ET.SubElement(
        geom,
        "mxPoint",
        {
            "as": "offset",
            "x": contracts.number(desired[0] - midpoint[0]),
            "y": contracts.number(desired[1] - midpoint[1]),
        },
    )


def reflow_automatic_edge_labels(
    root: ET.Element,
    pool: ET.Element,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    preferred_sides: dict[str, str] | None = None,
) -> None:
    """Place labels after all routes exist so later edges cannot invalidate them."""
    records = document.edge_records(root)
    paths = {
        edge_id: document.edge_polyline(cell, lanes, nodes)
        for edge_id, cell in records.items()
    }
    node_boxes = [
        core_geometry.node_bounds_in_pool(record, lanes[record["lane"]])
        for record in nodes.values()
    ]
    pool_geometry = document.parse_geometry(pool)
    container = {
        "left": 0.0,
        "right": pool_geometry["width"],
        "top": 0.0,
        "bottom": pool_geometry["height"],
    }
    main_pairs = set(zip(document.read_main_path(pool), document.read_main_path(pool)[1:]))

    def order(item: tuple[str, ET.Element]) -> tuple[int, str]:
        edge_id, cell = item
        pair = (cell.attrib.get(contracts.DATA_FROM), cell.attrib.get(contracts.DATA_TO))
        route = cell.attrib.get(contracts.DATA_ROUTE, "auto")
        return (0 if pair in main_pairs else 2 if route == "back" else 1, edge_id)

    assigned_labels: list[dict[str, float]] = []
    for edge_id, cell in sorted(records.items(), key=order):
        path = paths.get(edge_id, [])
        label = cell.attrib.get("value", "")
        other_segments = [
            segment
            for other_id, other_path in paths.items()
            if other_id != edge_id
            for segment in zip(other_path, other_path[1:])
        ]
        choice = labels.choose_label_box(
            path,
            label,
            node_boxes,
            other_segments,
            assigned_labels,
            (preferred_sides or {}).get(edge_id),
            container,
            cell.attrib.get(contracts.DATA_ROUTE, "auto") == "back",
        )
        set_edge_label_position(cell, path, choice)
        if choice is not None:
            assigned_labels.append(choice[1])


def apply_edge_route(
    cell: ET.Element,
    edge: dict,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    allocator: ports.PortAllocator,
    routing_context: dict | None = None,
) -> ET.Element:
    routed = routing.route_edge(
        edge, document.routing_lane_views(lanes), document.routing_node_views(nodes),
        allocator, routing_context,
    )
    style = edge_style(
        edge.get("type", "flow"), routed["exit_side"], routed["entry_side"],
        routed["exit_offset"], routed["entry_offset"],
    )
    cell.attrib.update(
        {
            "source": nodes[edge["from"]]["cell"].attrib["id"],
            "target": nodes[edge["to"]]["cell"].attrib["id"],
            "style": style,
            "value": str(edge.get("label", "")),
            contracts.DATA_EDGE_TYPE: edge.get("type", "flow"),
            contracts.DATA_FROM: edge["from"],
            contracts.DATA_TO: edge["to"],
            contracts.DATA_ROUTE: routed["route"],
            contracts.DATA_EXIT_SIDE: routed["exit_side"],
            contracts.DATA_ENTRY_SIDE: routed["entry_side"],
            contracts.DATA_EXIT_OFFSET: contracts.number(routed["exit_offset"]),
            contracts.DATA_ENTRY_OFFSET: contracts.number(routed["entry_offset"]),
            contracts.DATA_ALLOW_PORT_REUSE: "1" if edge.get("allow_port_reuse") else "0",
            contracts.DATA_WAYPOINTS_ORIGIN: "explicit" if "waypoints" in edge else "automatic",
            contracts.DATA_EXIT_SIDE_EXPLICIT: "1" if "exit_side" in edge else "0",
            contracts.DATA_ENTRY_SIDE_EXPLICIT: "1" if "entry_side" in edge else "0",
            contracts.DATA_EXIT_OFFSET_EXPLICIT: "1" if "exit_offset" in edge else "0",
            contracts.DATA_ENTRY_OFFSET_EXPLICIT: "1" if "entry_offset" in edge else "0",
        }
    )
    if edge.get("branch"):
        cell.attrib[contracts.DATA_BRANCH] = edge["branch"]
    else:
        cell.attrib.pop(contracts.DATA_BRANCH, None)
    if edge.get("flow_role"):
        cell.attrib[contracts.DATA_FLOW_ROLE] = edge["flow_role"]
    else:
        cell.attrib.pop(contracts.DATA_FLOW_ROLE, None)
    if edge.get("outcome"):
        cell.attrib[contracts.DATA_OUTCOME] = edge["outcome"]
    else:
        cell.attrib.pop(contracts.DATA_OUTCOME, None)
    document.set_edge_points(cell, routed["points"])
    set_edge_label_position(cell, routed["full_path"], routed["label_choice"])
    if routing_context is not None:
        routing_context.setdefault("paths", {})[edge["id"]] = routed["full_path"]
        routing_context.setdefault("endpoints", {})[edge["id"]] = (edge["from"], edge["to"])
        if routed["label_choice"] is not None:
            routing_context.setdefault("labels", {})[edge["id"]] = routed["label_choice"][1]
    return cell


def seed_routing_context(
    context: dict,
    edges: dict[str, ET.Element],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    *,
    exclude: set[str] | None = None,
) -> None:
    excluded = exclude or set()
    for edge_id, cell in edges.items():
        if edge_id in excluded:
            continue
        path = document.edge_polyline(cell, lanes, nodes)
        if len(path) < 2:
            continue
        context.setdefault("paths", {})[edge_id] = path
        context.setdefault("endpoints", {})[edge_id] = (
            cell.attrib.get(contracts.DATA_FROM),
            cell.attrib.get(contracts.DATA_TO),
        )
        label_bounds = document.stored_label_bounds(cell)
        if label_bounds is not None:
            context.setdefault("labels", {})[edge_id] = label_bounds


def reserve_existing_ports(
    root: ET.Element,
    allocator: ports.PortAllocator,
    *,
    exclude: set[str] | None = None,
) -> None:
    excluded = exclude or set()
    for edge_id, cell in document.edge_records(root).items():
        if edge_id in excluded:
            continue
        allow_reuse = cell.attrib.get(contracts.DATA_ALLOW_PORT_REUSE) == "1"
        exit_port = document.port_from_style(cell, "exit")
        entry_port = document.port_from_style(cell, "entry")
        if exit_port and cell.attrib.get(contracts.DATA_FROM):
            allocator.reserve(
                cell.attrib[contracts.DATA_FROM],
                exit_port[0],
                exit_port[1],
                edge_id,
                allow_reuse=allow_reuse,
                fail_on_conflict=False,
            )
        if entry_port and cell.attrib.get(contracts.DATA_TO):
            allocator.reserve(
                cell.attrib[contracts.DATA_TO],
                entry_port[0],
                entry_port[1],
                edge_id,
                allow_reuse=allow_reuse,
                fail_on_conflict=False,
            )


def existing_edge_spec(cell: ET.Element, *, for_reroute: bool = False) -> dict:
    spec = {
        "id": cell.attrib[contracts.DATA_SEMANTIC_ID],
        "from": cell.attrib.get(contracts.DATA_FROM),
        "to": cell.attrib.get(contracts.DATA_TO),
        "type": cell.attrib.get(contracts.DATA_EDGE_TYPE, "flow"),
        "label": cell.attrib.get("value", ""),
        "route": cell.attrib.get(contracts.DATA_ROUTE, "auto"),
        "allow_port_reuse": cell.attrib.get(contracts.DATA_ALLOW_PORT_REUSE) == "1",
    }
    if cell.attrib.get(contracts.DATA_BRANCH):
        spec["branch"] = cell.attrib[contracts.DATA_BRANCH]
    if cell.attrib.get(contracts.DATA_FLOW_ROLE):
        spec["flow_role"] = cell.attrib[contracts.DATA_FLOW_ROLE]
    if cell.attrib.get(contracts.DATA_OUTCOME):
        spec["outcome"] = cell.attrib[contracts.DATA_OUTCOME]
    explicit_waypoints = cell.attrib.get(contracts.DATA_WAYPOINTS_ORIGIN) == "explicit"
    exit_port = document.port_from_style(cell, "exit")
    entry_port = document.port_from_style(cell, "entry")
    preserve_exit_side = (
        not for_reroute
        or explicit_waypoints
        or cell.attrib.get(contracts.DATA_EXIT_SIDE_EXPLICIT) == "1"
    )
    preserve_entry_side = (
        not for_reroute
        or explicit_waypoints
        or cell.attrib.get(contracts.DATA_ENTRY_SIDE_EXPLICIT) == "1"
    )
    preserve_exit_offset = (
        not for_reroute
        or explicit_waypoints
        or cell.attrib.get(contracts.DATA_EXIT_OFFSET_EXPLICIT) == "1"
    )
    preserve_entry_offset = (
        not for_reroute
        or explicit_waypoints
        or cell.attrib.get(contracts.DATA_ENTRY_OFFSET_EXPLICIT) == "1"
    )
    if exit_port and preserve_exit_side:
        spec["exit_side"] = exit_port[0]
    if exit_port and preserve_exit_offset:
        spec["exit_offset"] = exit_port[1]
    if entry_port and preserve_entry_side:
        spec["entry_side"] = entry_port[0]
    if entry_port and preserve_entry_offset:
        spec["entry_offset"] = entry_port[1]
    if explicit_waypoints:
        spec["waypoints"] = [
            {"x": x, "y": y}
            for x, y in document.edge_waypoints(cell)
        ]
    return spec


def edge_route_is_locally_valid(
    cell: ET.Element,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
) -> bool:
    points = document.edge_polyline(cell, lanes, nodes)
    if len(points) < 2:
        return False
    return routing.polyline_is_locally_valid(
        points, document.routing_lane_views(lanes), document.routing_node_views(nodes),
        cell.attrib.get(contracts.DATA_FROM), cell.attrib.get(contracts.DATA_TO),
    )
