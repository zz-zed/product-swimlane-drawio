"""Native edge styles, XML writeback, and routing state integration."""

from __future__ import annotations

import copy
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


def arrowhead_clearance_profiles(
    edges: list[dict],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    *,
    existing_edges: dict[str, ET.Element] | None = None,
) -> dict[str, dict]:
    """Describe the renderer-relevant terminal state without changing XML.

    Existing edges keep their complete native style as their effective style;
    new edges use exactly the canonical default writer style.  Anchor values do
    not affect arrowhead capability, but retaining them for existing records is
    important because a caller may carry custom, unsupported renderer tokens.
    ``routing_context`` owns this as immutable planning input.
    """
    existing = existing_edges or {}
    profiles: dict[str, dict] = {}
    for edge in edges:
        target_id = edge.get("to")
        target = nodes.get(target_id)
        if target is None:
            continue
        target_cell = target["cell"]
        target_bounds = core_geometry.node_bounds_in_pool(target, lanes[target["lane"]])
        current = existing.get(edge["id"])
        profiles[edge["id"]] = {
            "edge_style": (
                current.attrib.get("style", "")
                if current is not None
                else edge_style(edge.get("type", "flow"), "bottom", "top", 0.5, 0.5)
            ),
            "target_style": target_cell.attrib.get("style", ""),
            "target_type": target_cell.attrib.get(contracts.DATA_NODE_TYPE, "process"),
            "target_bounds": dict(target_bounds),
        }
    return profiles


def native_label_profiles(edges, pool, lanes, nodes, *, existing_edges=None,
                          explicit_by_edge=None, points_actions_by_edge=None):
    """Capture the actual authorized writer inputs without modifying the scene.

    Existing geometry, parent and unsupported payload remain visible to native
    capability checks. Only fields the route writer will replace are projected;
    saved, non-mutable XML is measured separately by seed_routing_context.
    """
    profiles = {}
    scene = {"pool": pool, "lanes": lanes, "nodes": nodes}
    for edge in edges:
        edge_id = edge["id"]
        current = (existing_edges or {}).get(edge_id)
        # An effective saved-edge spec contains defaults as well as authored
        # updates; only an explicit map may authorize text/type replacement.
        explicit = (explicit_by_edge or {}).get(edge_id, set(edge) if current is None else set())
        if current is None:
            cell = ET.Element("mxCell", {
                "parent": pool.get("id", ""), "value": str(edge.get("label", "")),
                "style": edge_style(edge.get("type", "flow"), "bottom", "top", .5, .5),
            })
            document.geometry(cell, relative=1)
        else:
            cell = copy.deepcopy(current)
            # Install the same eight keys in the writer's order. This also
            # captures resetting native Dx/Dy to zero and deduplicating keys;
            # the pure candidate projector substitutes only the selected X/Y.
            saved_style = document.style_values(cell.get("style", ""))
            for key, default in (("exitX", "0.5"), ("exitY", "1"),
                                 ("exitDx", "0"), ("exitDy", "0"),
                                 ("entryX", "0.5"), ("entryY", "0"),
                                 ("entryDx", "0"), ("entryDy", "0")):
                value = default if key.endswith(("Dx", "Dy")) else saved_style.get(key, default)
                document.set_style_option(cell, key, value)
            if "type" in explicit:
                document.set_style_option(cell, "dashed", "1" if edge.get("type") == "async" else "0")
            if "label" in explicit:
                cell.set("value", str(edge.get("label", "")))
            action = (points_actions_by_edge or {}).get(edge_id, "replace_automatic")
            if action not in {"preserve_existing", "replace_explicit", "replace_automatic"}:
                raise ValueError(f"Unsupported edge points action: {action}")
            if cell.find("mxGeometry") is None and action != "preserve_existing":
                document.geometry(cell, relative=1)
        # The writer always retargets the native and semantic endpoint IDs.
        cell.attrib.update({
            "source": nodes[edge["from"]]["cell"].get("id", ""),
            "target": nodes[edge["to"]]["cell"].get("id", ""),
            contracts.DATA_FROM: edge["from"], contracts.DATA_TO: edge["to"],
        })
        profiles[edge_id] = document.extract_native_label_profile(cell, scene=scene)
    return profiles


def apply_label_plan(records, decisions, choices):
    """Write only a successful batch's authorized native label positions."""
    by_id = {decision.edge_id: decision for decision in decisions}
    for edge_id, choice in choices.items():
        decision = by_id[edge_id]
        set_edge_label_position(records[edge_id], decision.routed["native_path"], choice)


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
    # Reset both native relative components when installing an authorized
    # absolute offset; otherwise a saved x/y drag would be applied twice.
    geom.set("relative", "1")
    if "x" in geom.attrib:
        geom.set("x", "0")
    if "y" in geom.attrib:
        geom.set("y", "0")
    _, planned_offset = labels.candidate_label_geometry(full_path, label_choice)
    if existing_offset is None:
        existing_offset = ET.SubElement(geom, "mxPoint", {"as": "offset"})
    existing_offset.set("x", planned_offset["x"])
    existing_offset.set("y", planned_offset["y"])


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
    measured_labels = {
        edge_id: document.edge_label_measurement(cell, paths[edge_id], {"lanes": lanes, "nodes": nodes, "pool": pool})
        for edge_id, cell in records.items()
    }
    paths = {edge_id: measured_labels[edge_id].get("path") if measured_labels[edge_id].get("path_available") else path
             for edge_id, path in paths.items()}
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
            size=(measured_labels[edge_id]["bounds"]["width"], measured_labels[edge_id]["bounds"]["height"])
            if measured_labels[edge_id]["status"] == "available" else None,
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
    return apply_route_decision(
        cell, edge, routing.RouteDecision(edge["id"], None, routed), lanes, nodes,
        write_label_position=True, routing_context=routing_context,
    )


def apply_route_decision(
    cell: ET.Element,
    edge: dict,
    decision: routing.RouteDecision,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    *,
    existing: bool = False,
    explicit_fields: set[str] | None = None,
    points_action: str = "replace_automatic",
    write_label_position: bool = False,
    routing_context: dict | None = None,
) -> ET.Element:
    """Apply an already planned route without re-running allocation/routing.

    The decision comes from ``plan_route_batch``.  Keeping this writeback small
    is what lets patch freeze unrelated XML while still moving the eight native
    edge-anchor keys required by Draw.io.
    """
    routed = decision.routed
    explicit = explicit_fields if explicit_fields is not None else set(edge)
    if existing:
        for key, value in (
            ("exitX", core_geometry.port_xy(routed["exit_side"], routed["exit_offset"])[0]),
            ("exitY", core_geometry.port_xy(routed["exit_side"], routed["exit_offset"])[1]),
            ("exitDx", 0), ("exitDy", 0),
            ("entryX", core_geometry.port_xy(routed["entry_side"], routed["entry_offset"])[0]),
            ("entryY", core_geometry.port_xy(routed["entry_side"], routed["entry_offset"])[1]),
            ("entryDx", 0), ("entryDy", 0),
        ):
            document.set_style_option(cell, key, contracts.number(value))
        if "type" in explicit:
            document.set_style_option(cell, "dashed", "1" if edge.get("type") == "async" else "0")
    else:
        new_style = edge_style(
            edge.get("type", "flow"), routed["exit_side"], routed["entry_side"],
            routed["exit_offset"], routed["entry_offset"],
        )
    attributes = {
        "source": nodes[edge["from"]]["cell"].attrib["id"],
        "target": nodes[edge["to"]]["cell"].attrib["id"],
    }
    if not existing:
        # New canonical cells retain the historical XML insertion sequence:
        # source, target, style, value, then semantic metadata.  Updating an
        # existing dict key does not move it, so patch keeps authored order.
        attributes["style"] = new_style
        attributes["value"] = str(edge.get("label", ""))
    elif "label" in explicit:
        attributes["value"] = str(edge.get("label", ""))
    attributes.update(
        {
            contracts.DATA_EDGE_TYPE: edge.get("type", cell.attrib.get(contracts.DATA_EDGE_TYPE, "flow")),
            contracts.DATA_FROM: edge["from"],
            contracts.DATA_TO: edge["to"],
            contracts.DATA_ROUTE: routed["route"],
            contracts.DATA_EXIT_SIDE: routed["exit_side"],
            contracts.DATA_ENTRY_SIDE: routed["entry_side"],
            contracts.DATA_EXIT_OFFSET: contracts.number(routed["exit_offset"]),
            contracts.DATA_ENTRY_OFFSET: contracts.number(routed["entry_offset"]),
            contracts.DATA_ALLOW_PORT_REUSE: "1" if edge.get("allow_port_reuse") else "0",
            contracts.DATA_WAYPOINTS_ORIGIN: "explicit" if "waypoints" in explicit else "automatic",
            contracts.DATA_EXIT_SIDE_EXPLICIT: "1" if "exit_side" in explicit else "0",
            contracts.DATA_ENTRY_SIDE_EXPLICIT: "1" if "entry_side" in explicit else "0",
            contracts.DATA_EXIT_OFFSET_EXPLICIT: "1" if "exit_offset" in explicit else "0",
            contracts.DATA_ENTRY_OFFSET_EXPLICIT: "1" if "entry_offset" in explicit else "0",
        }
    )
    cell.attrib.update(attributes)
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
    points = routed["points"]
    if points_action == "replace_explicit":
        # Routing uses a normalized calculation view.  XML writeback must
        # retain authoring details such as duplicate
        # and collinear points (and an explicit empty list).
        points = []
        for point in edge.get("waypoints", []):
            if isinstance(point, dict):
                points.append((float(point["x"]), float(point["y"])))
            else:
                # The public schema also permits a compact [x, y] form.
                points.append((float(point[0]), float(point[1])))
    document.set_edge_points(cell, points, action=points_action)
    if write_label_position:
        set_edge_label_position(cell, routed["full_path"], routed["label_choice"])
    if routing_context is not None:
        routing_context.setdefault("paths", {})[edge["id"]] = routed["full_path"]
        routing_context.setdefault("endpoints", {})[edge["id"]] = (edge["from"], edge["to"])
        if routed["label_choice"] is not None:
            routing_context.setdefault("labels", {})[edge["id"]] = routed["label_choice"][1]
    return cell


def reflow_mutable_edge_labels(
    root: ET.Element,
    pool: ET.Element,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    mutable_edge_ids: set[str],
    preferred_sides: dict[str, str] | None = None,
    *,
    preserve_position: bool = False,
    exclude_obstacles: set[str] | None = None,
) -> None:
    """Reflow only mutated labels; pre-existing labels are hard obstacles."""
    if not mutable_edge_ids:
        return
    records = document.edge_records(root)
    paths = {edge_id: document.edge_polyline(cell, lanes, nodes) for edge_id, cell in records.items()}
    node_boxes = [core_geometry.node_bounds_in_pool(record, lanes[record["lane"]]) for record in nodes.values()]
    pool_geometry = document.parse_geometry(pool)
    container = {"left": 0.0, "right": pool_geometry["width"], "top": 0.0, "bottom": pool_geometry["height"]}
    main_pairs = set(zip(document.read_main_path(pool), document.read_main_path(pool)[1:]))
    def order(item):
        edge_id, cell = item
        pair = (cell.attrib.get(contracts.DATA_FROM), cell.attrib.get(contracts.DATA_TO))
        return (0 if pair in main_pairs else 2 if cell.attrib.get(contracts.DATA_ROUTE, "auto") == "back" else 1, edge_id)
    scene = {"lanes": lanes, "nodes": nodes, "pool": pool}
    excluded = exclude_obstacles or set()
    assigned = []
    for edge_id, cell in records.items():
        if edge_id in mutable_edge_ids or edge_id in excluded:
            continue
        measured = document.edge_label_measurement(cell, paths[edge_id], scene)
        require_label_geometry(edge_id, measured)
        if measured["status"] == "available":
            assigned.append(measured["bounds"])
    for edge_id, cell in sorted(records.items(), key=order):
        if edge_id not in mutable_edge_ids:
            continue
        path = paths.get(edge_id, [])
        if not cell.get("value", "").strip():
            continue
        measured = document.edge_label_measurement(cell, path, scene)
        require_label_geometry(edge_id, measured)
        if measured["status"] == "not_applicable":
            continue
        native_path = measured.get("path") or path
        other_segments = [segment for other_id, other_path in paths.items() if other_id != edge_id and other_id not in excluded for segment in zip(other_path, other_path[1:])]
        def clear(box, carrier):
            return (container["left"] <= box["left"] and box["right"] <= container["right"]
                    and container["top"] <= box["top"] and box["bottom"] <= container["bottom"]
                    and not any(core_geometry.bounds_overlap(box, obstacle, gap=2.0) for obstacle in node_boxes + assigned)
                    and not any(core_geometry.segment_intersects_box(segment, box, gap=2.0) for segment in other_segments)
                    and not any(index != carrier and core_geometry.segment_intersects_box(segment, box, gap=2.0)
                                for index, segment in enumerate(zip(native_path, native_path[1:]))))
        if preserve_position and measured["status"] == "available" and measured.get("carrier_usable") and clear(measured["bounds"], measured["carrier_segment"]):
            assigned.append(measured["bounds"])
            continue
        choice = labels.choose_label_box(native_path, cell.attrib.get("value", ""), node_boxes, other_segments, assigned, (preferred_sides or {}).get(edge_id), container, cell.attrib.get(contracts.DATA_ROUTE, "auto") == "back", size=(measured["bounds"]["width"], measured["bounds"]["height"]))
        if choice is None:
            raise contracts.DiagramError(
                "Edge label has no clear span on its saved path",
                code="text/edge-label-no-clear-span", subject={"kind": "edge", "id": edge_id},
                evidence={"label": cell.get("value", ""), "path_frozen": True},
                supported_fixes=["shorten-edge-label", "move-edge-label", "declare-edge-reroute"],
            )
        set_edge_label_position(cell, native_path, choice)
        actual = document.edge_label_measurement(cell, path, scene)
        require_label_geometry(edge_id, actual)
        if not clear(actual["bounds"], actual.get("carrier_segment")):
            raise contracts.DiagramError(
                "Repositioned native label has no clear span on its saved path",
                code="text/edge-label-no-clear-span", subject={"kind": "edge", "id": edge_id},
                supported_fixes=["shorten-edge-label", "move-edge-label", "declare-edge-reroute"],
            )
        if choice is not None:
            assigned.append(actual["bounds"])


def require_label_geometry(edge_id: str, measured: dict) -> None:
    if measured["status"] == "not_available":
        raise contracts.DiagramError(
            "Saved label geometry is unavailable for spatial planning",
            code="text/edge-label-geometry-unavailable", subject={"kind": "edge", "id": edge_id},
            evidence={"reason": measured.get("reason"), "bounds_quality": measured.get("bounds_quality")},
            supported_fixes=["use-supported-label-style", "review-native-label"],
        )


def seed_routing_context(
    context: dict,
    edges: dict[str, ET.Element],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    *,
    exclude: set[str] | None = None,
    require_measurable: bool = True,
    pool: ET.Element | None = None,
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
        measured = document.edge_label_measurement(cell, path, {"lanes": lanes, "nodes": nodes, "pool": pool})
        if require_measurable:
            require_label_geometry(edge_id, measured)
        if measured["status"] == "available":
            context.setdefault("labels", {})[edge_id] = measured["bounds"]
            # Only saved labels are hard route obstacles. Labels selected in
            # the current batch may still be reflowed after its routes exist.
            context.setdefault("frozen_label_obstacles", {})[edge_id] = dict(measured["bounds"])


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
