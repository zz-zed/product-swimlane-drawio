"""Controlled patch operations over the current saved XML scene.

The caller owns snapshots, record refresh boundaries, metadata and delivery.
Operations mutate only this call's supplied records; failure does not promise
whole-tree rollback. Existing edge edits are staged until routing succeeds.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import math
import xml.etree.ElementTree as ET

from . import (
    construction, contracts, document, geometry as core_geometry, layout,
    routing, routing_adapter, sizing, spec_validation,
)


@dataclass
class PatchScene:
    """Current records, refreshed explicitly by the patch coordinator.

    Node additions update the supplied records and pool_width. XML references
    remain live; cached geometry changes only at the original write/read sites.
    """

    root: ET.Element
    pool: ET.Element
    values: dict
    lanes: dict[str, dict]
    nodes: dict[str, dict]
    pool_width: float


@dataclass(frozen=True)
class PatchDeletions:
    """Declared deletion sets checked against the initial saved records."""

    edge_ids: set[str]
    lane_ids: set[str]
    node_ids: set[str]
    phase_ids: set[str]


@dataclass(frozen=True)
class NodeAdditions:
    """Original new-node order and schema used before the group stage."""

    new_nodes: list[dict]
    schema_version: str


@dataclass(frozen=True)
class GroupChanges:
    """Semantic group changes after the new-node records are refreshed."""

    added_ids: list[str]
    updated_ids: list[str]
    deleted_ids: list[str]


@dataclass(frozen=True)
class EdgeChanges:
    """Declared and effective edge effects needed by the final receipt."""

    new_edges: list[dict]
    explicit_reroute_ids: set[str]
    auto_reroute_ids: set[str]
    reroute_ids: set[str]
    label_updated_ids: set[str]
    manual_waypoint_edges_affected_by_lane_changes: list[str]


def reflow_lane_order_geometry(
    pool: ET.Element,
    order: list[str],
    lanes: dict[str, dict],
    previous: dict[str, dict],
) -> list[dict]:
    phase_rail_width = (
        float(pool.attrib.get(contracts.DATA_PHASE_RAIL_WIDTH, layout.PHASE_RAIL_WIDTH))
        if pool.attrib.get(contracts.DATA_PHASE_PRESENTATION) == "rail"
        else 0.0
    )
    cursor = phase_rail_width
    shifts: list[dict] = []
    for lane_id in order:
        record = lanes[lane_id]
        geom = record["cell"].find("mxGeometry")
        assert geom is not None
        width = float(geom.attrib.get("width", "0"))
        old = previous.get(lane_id)
        geom.attrib["x"] = contracts.number(cursor)
        record["geometry"] = document.parse_geometry(record["cell"])
        if old is not None and (
            abs(old["x"] - cursor) >= core_geometry.GEOMETRY_TOLERANCE
            or abs(old["width"] - width) >= core_geometry.GEOMETRY_TOLERANCE
        ):
            shifts.append(
                {
                    "id": lane_id,
                    "from_x": old["x"],
                    "to_x": cursor,
                    "from_width": old["width"],
                    "to_width": width,
                }
            )
        cursor += width

    pool_geom = pool.find("mxGeometry")
    assert pool_geom is not None
    pool_geom.attrib["width"] = contracts.number(cursor)
    pool.attrib[contracts.DATA_LANE_ORDER] = json.dumps(
        order, ensure_ascii=True, separators=(",", ":")
    )
    return shifts


def apply_lane_operations(
    root: ET.Element,
    pool: ET.Element,
    lanes: dict[str, dict],
    values: dict,
    changes: dict,
) -> tuple[list[str], dict[str, dict], list[dict]]:
    """Apply lane semantics and deterministic horizontal geometry.

    Existing lane-local node geometry remains untouched. Inserting or resizing
    a lane changes lane/pool geometry; saved routes still require an explicit
    reroute declaration when their local validity would be lost.
    """
    order = document.read_lane_order(pool, root, lanes)
    deleted = set(changes.get("delete_lanes", []))
    for lane_id in deleted:
        if lane_id not in lanes:
            raise contracts.DiagramError(
                f"Cannot delete missing lane: {lane_id}",
                code="patch/missing-lane",
            )
    if len(order) - len(deleted) + len(changes.get("lanes", [])) < 1:
        raise contracts.DiagramError(
            "A diagram must retain at least one lane",
            code="patch/delete-last-lane",
            supported_fixes=["retain-one-lane", "add-replacement-lane"],
        )

    updates = {item["id"]: item for item in changes.get("update_lanes", [])}
    for lane_id in updates:
        if lane_id not in lanes:
            raise contracts.DiagramError(
                f"Cannot update missing lane: {lane_id}",
                code="patch/missing-lane",
            )
        if lane_id in deleted:
            raise contracts.DiagramError(
                f"Lane {lane_id} cannot be updated and deleted in one patch",
                code="patch/conflicting-operation",
                subject={"kind": "lane", "id": lane_id},
            )

    added_ids = {item["id"] for item in changes.get("lanes", [])}
    duplicate_added = sorted(added_ids.intersection(lanes))
    if duplicate_added:
        raise contracts.DiagramError(
            f"Lane already exists: {duplicate_added[0]}",
            code="patch/duplicate-lane",
        )

    previous = {
        lane_id: {
            "x": record["geometry"]["x"],
            "width": record["geometry"]["width"],
        }
        for lane_id, record in lanes.items()
    }
    for lane_id in deleted:
        root.remove(lanes[lane_id]["cell"])
        order.remove(lane_id)
        del lanes[lane_id]

    lane_height_value = next(
        (record["geometry"]["height"] for record in lanes.values()),
        layout.lane_height(int(pool.attrib.get(contracts.DATA_MAX_RANK, "1")), values),
    )
    for lane in changes.get("lanes", []):
        placement = "before" if "before" in lane else "after"
        reference = lane[placement]
        if reference not in order:
            raise contracts.DiagramError(
                f"New lane {lane['id']} references missing placement lane {reference}",
                code="patch/lane-placement-target",
                subject={"kind": "lane", "id": lane["id"]},
                evidence={"placement": placement, "reference": reference},
                supported_fixes=["use-existing-placement-lane"],
            )
        index = order.index(reference) + (1 if placement == "after" else 0)
        order.insert(index, lane["id"])
        width = float(lane.get("width", 200))
        cell = construction.create_lane_cell(
            root,
            pool,
            lane,
            values,
            x=0,
            width=width,
            height=lane_height_value,
        )
        lanes[lane["id"]] = {"cell": cell, "geometry": document.parse_geometry(cell)}

    for lane_id, update in updates.items():
        cell = lanes[lane_id]["cell"]
        if "label" in update:
            cell.attrib["value"] = update["label"]
        if "width" in update:
            geom = cell.find("mxGeometry")
            assert geom is not None
            geom.attrib["width"] = contracts.number(update["width"])
            lanes[lane_id]["geometry"] = document.parse_geometry(cell)

    shifts = reflow_lane_order_geometry(pool, order, lanes, previous)
    return order, lanes, shifts


def current_groups_for_patch(pool: ET.Element) -> list[dict]:
    return copy.deepcopy(document.json_attribute(pool, contracts.DATA_GROUPS, list, []))


def apply_group_operations(
    pool: ET.Element,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    changes: dict,
) -> tuple[list[str], list[str], list[str]]:
    groups = current_groups_for_patch(pool)
    by_id = {group.get("id"): group for group in groups}
    if len(by_id) != len(groups) or None in by_id:
        raise document.managed_metadata_error(
            "Managed group metadata is invalid",
            attribute=contracts.DATA_GROUPS,
        )
    deleted = set(changes.get("delete_groups", []))
    for group_id in deleted:
        if group_id not in by_id:
            raise contracts.DiagramError(
                f"Cannot delete missing group: {group_id}",
                code="patch/missing-group",
            )
    groups = [group for group in groups if group["id"] not in deleted]
    by_id = {group["id"]: group for group in groups}

    updated_ids: list[str] = []
    for update in changes.get("update_groups", []):
        group_id = update["id"]
        if group_id not in by_id:
            raise contracts.DiagramError(
                f"Cannot update missing group: {group_id}",
                code="patch/missing-group",
            )
        if group_id in deleted:
            raise contracts.DiagramError(
                f"Group {group_id} cannot be updated and deleted in one patch",
                code="patch/conflicting-operation",
                subject={"kind": "group", "id": group_id},
            )
        by_id[group_id].update(update)
        updated_ids.append(group_id)

    added_ids: list[str] = []
    for group in changes.get("groups", []):
        if group["id"] in by_id:
            raise contracts.DiagramError(
                f"Group already exists: {group['id']}",
                code="patch/duplicate-group",
            )
        item = copy.deepcopy(group)
        groups.append(item)
        by_id[item["id"]] = item
        added_ids.append(item["id"])

    member_to_group: dict[str, str] = {}
    for group in groups:
        contracts.validate_group_object(group, f"group[{group['id']}]")
        if group["lane"] not in lanes:
            raise contracts.DiagramError(
                f"Group {group['id']} references a deleted or missing lane",
                code="patch/group-lane-dependency",
                subject={"kind": "group", "id": group["id"]},
                evidence={"lane": group["lane"]},
                supported_fixes=["delete-group", "update-group-lane"],
            )
        for node_id in group["nodes"]:
            if node_id not in nodes:
                raise contracts.DiagramError(
                    f"Group {group['id']} references a deleted or missing node",
                    code="patch/group-node-dependency",
                    subject={"kind": "group", "id": group["id"]},
                    evidence={"node": node_id},
                    supported_fixes=["delete-group", "update-group-nodes"],
                )
            if nodes[node_id]["lane"] != group["lane"]:
                raise contracts.DiagramError(
                    f"Group {group['id']} contains a node from another lane",
                    code="semantic/group-lane",
                    subject={"kind": "group", "id": group["id"]},
                    evidence={"node": node_id},
                )
            if node_id in member_to_group:
                raise contracts.DiagramError(
                    f"Node {node_id} belongs to more than one group",
                    code="semantic/group-membership",
                    subject={"kind": "node", "id": node_id},
                )
            member_to_group[node_id] = group["id"]

    for node_id, record in nodes.items():
        cell = record["cell"]
        group_id = member_to_group.get(node_id)
        if group_id:
            cell.attrib[contracts.DATA_GROUP_ID] = group_id
        else:
            cell.attrib.pop(contracts.DATA_GROUP_ID, None)
    pool.attrib[contracts.DATA_GROUPS] = json.dumps(
        groups, ensure_ascii=True, separators=(",", ":")
    )
    return sorted(added_ids), sorted(updated_ids), sorted(deleted)


def patch_node_automatic_x(
    node: dict,
    lane_record: dict,
    nodes: dict[str, dict],
    layout_profile_name: str,
) -> float:
    lane_width = lane_record["geometry"]["width"]
    width, _ = sizing.node_size(node)
    slot = layout.effective_node_slot(node)
    rank = int(node["rank"])
    row = [
        record
        for record in nodes.values()
        if record["lane"] == node["lane"]
        and int(record["cell"].attrib.get(contracts.DATA_RANK, "0")) == rank
    ]
    occupied = {
        record["cell"].attrib.get(contracts.DATA_SLOT, "main"): record
        for record in row
    }
    if slot in occupied:
        raise contracts.DiagramError(
            f"Node {node['id']} conflicts with an occupied lane-local slot",
            code="layout/slot-conflict",
            subject={"kind": "node", "id": node["id"]},
            evidence={"lane": node["lane"], "rank": rank, "slot": slot},
            supported_fixes=["assign-distinct-slot", "change-rank", "set-explicit-geometry"],
        )

    gap = layout.PROFILE_SLOT_GAPS.get(layout_profile_name, layout.PROFILE_SLOT_GAPS["review"])
    if node.get("anchor"):
        target_id = node["anchor"]["node"]
        target = nodes.get(target_id)
        if target is None:
            raise contracts.DiagramError(
                f"Note {node['id']} anchors to a missing node",
                code="semantic/anchor-target",
                subject={"kind": "node", "id": node["id"]},
                evidence={"anchor": target_id},
            )
        target_rank = int(target["cell"].attrib.get(contracts.DATA_RANK, "0"))
        if target["lane"] != node["lane"] or target_rank != rank:
            raise contracts.DiagramError(
                f"Note {node['id']} must share lane and rank with its anchor",
                code="semantic/anchor-alignment",
                subject={"kind": "node", "id": node["id"]},
            )
        target_geom = target["geometry"]
        if node["anchor"]["side"] == "left":
            x = target_geom["x"] - gap - width
        else:
            x = target_geom["x"] + target_geom["width"] + gap
    elif slot == "main" or "main" not in occupied:
        x = (lane_width - width) / 2.0
    else:
        main_geom = occupied["main"]["geometry"]
        if slot == "left":
            x = main_geom["x"] - gap - width
        else:
            x = main_geom["x"] + main_geom["width"] + gap

    side_padding = layout.PROFILE_SIDE_PADDING.get(
        layout_profile_name,
        layout.PROFILE_SIDE_PADDING["review"],
    )
    if x < side_padding - core_geometry.GEOMETRY_TOLERANCE:
        raise contracts.DiagramError(
            f"Node {node['id']} does not fit in the requested left slot without moving existing geometry",
            code="layout/slot-space",
            subject={"kind": "node", "id": node["id"]},
            evidence={"required_x": x, "minimum_x": side_padding},
            supported_fixes=["widen-and-realign-lane", "set-explicit-geometry", "change-slot"],
        )
    return x


def edge_route_update_requested(cell: ET.Element, update: dict) -> bool:
    """Only an effective route edit or explicit true reroute grants authority."""
    if update.get("reroute") is True:
        return True
    current = routing_adapter.existing_edge_spec(cell)
    for field in spec_validation.ROUTING_FIELDS - {"reroute"}:
        if field not in update:
            continue
        if field == "waypoints":
            requested = [
                (float(point["x"]), float(point["y"])) if isinstance(point, dict)
                else (float(point[0]), float(point[1])) for point in update[field]
            ]
            if (requested != document.edge_waypoints(cell)
                    or cell.get(contracts.DATA_WAYPOINTS_ORIGIN) != "explicit"):
                return True
        elif update[field] != current.get(field):
            return True
    return False


def check_deletion_dependencies(
    scene: PatchScene, changes: dict, existing_edges: dict[str, ET.Element],
    phases: dict[str, ET.Element],
) -> PatchDeletions:
    """Check deletion dependencies in saved-record order without writing XML."""
    pool, lanes, nodes = scene.pool, scene.lanes, scene.nodes
    deleted_edge_ids = set(changes.get("delete_edges", []))
    deleted_lane_ids = set(changes.get("delete_lanes", []))
    deleted_node_ids = set(changes.get("delete_nodes", []))
    deleted_phase_ids = set(changes.get("delete_phases", []))
    existing_lane_nodes = {
        lane_id: sorted(
            node_id for node_id, record in nodes.items() if record["lane"] == lane_id
        )
        for lane_id in deleted_lane_ids
        if lane_id in lanes
    }
    undeclared_lane_nodes = {
        lane_id: [node_id for node_id in node_ids if node_id not in deleted_node_ids]
        for lane_id, node_ids in existing_lane_nodes.items()
        if any(node_id not in deleted_node_ids for node_id in node_ids)
    }
    if undeclared_lane_nodes:
        raise contracts.DiagramError(
            "Deleting a lane requires explicitly deleting every node in that lane",
            code="semantic/lane-not-empty",
            evidence={"lanes": undeclared_lane_nodes},
            supported_fixes=["add-delete-nodes", "retain-lane"],
        )
    for edge_id in deleted_edge_ids:
        if edge_id not in existing_edges:
            raise contracts.DiagramError(f"Cannot delete missing edge: {edge_id}", code="patch/missing-edge")
    for node_id in deleted_node_ids:
        if node_id not in nodes:
            raise contracts.DiagramError(f"Cannot delete missing node: {node_id}", code="patch/missing-node")
    for phase_id in deleted_phase_ids:
        if phase_id not in phases:
            raise contracts.DiagramError(f"Cannot delete missing phase: {phase_id}", code="patch/missing-phase")

    undeclared_incident = sorted(
        edge_id
        for edge_id, cell in existing_edges.items()
        if edge_id not in deleted_edge_ids
        and (
            cell.attrib.get(contracts.DATA_FROM) in deleted_node_ids
            or cell.attrib.get(contracts.DATA_TO) in deleted_node_ids
        )
    )
    if undeclared_incident:
        raise contracts.DiagramError(
            "Deleting a node requires explicitly deleting every incident edge",
            code="patch/incident-edge",
            evidence={"edges": undeclared_incident},
            supported_fixes=["add-delete-edges"],
        )

    deleted_main_path_nodes = deleted_node_ids.intersection(document.read_main_path(pool))
    if deleted_main_path_nodes and "main_path" not in changes:
        raise contracts.DiagramError(
            "Deleting a main_path node requires supplying the replacement main_path",
            code="patch/main-path",
            evidence={"deleted_nodes": sorted(deleted_main_path_nodes)},
            supported_fixes=["supply-main-path"],
        )
    return PatchDeletions(deleted_edge_ids, deleted_lane_ids, deleted_node_ids, deleted_phase_ids)


def check_node_type_dependencies(
    nodes: dict[str, dict], changes: dict, existing_edges: dict[str, ET.Element],
    deleted_edge_ids: set[str],
) -> list[dict]:
    """Check explicit incident reroutes and return the original edge updates."""
    edge_updates = changes.get("update_edges", [])
    explicit_type_reroutes = {
        update["id"]
        for update in edge_updates
        if update["id"] in existing_edges
        and edge_route_update_requested(existing_edges[update["id"]], update)
    }
    for update in changes.get("update_nodes", []):
        node_id = update["id"]
        if node_id not in nodes or "type" not in update:
            continue
        current_type = nodes[node_id]["cell"].attrib.get(contracts.DATA_NODE_TYPE, "process")
        if current_type == update["type"]:
            continue
        incident = sorted(
            edge_id
            for edge_id, cell in existing_edges.items()
            if edge_id not in deleted_edge_ids
            and node_id in {cell.attrib.get(contracts.DATA_FROM), cell.attrib.get(contracts.DATA_TO)}
            and edge_id not in explicit_type_reroutes
        )
        if incident:
            raise contracts.DiagramError(
                "Changing a node type requires explicit rerouting of every incident edge",
                code="patch/node-type-incident-edge",
                subject={"kind": "node", "id": node_id},
                evidence={"edges": incident, "from": current_type, "to": update["type"]},
                supported_fixes=["add-update-edges-reroute"],
            )
    return edge_updates


def apply_declared_deletions(
    root: ET.Element, nodes: dict[str, dict], existing_edges: dict[str, ET.Element],
    phases: dict[str, ET.Element], deletions: PatchDeletions,
) -> None:
    """Delete declared edges, nodes and phases; the caller then rebuilds records."""
    deleted_edge_ids = deletions.edge_ids
    deleted_node_ids = deletions.node_ids
    deleted_phase_ids = deletions.phase_ids
    for edge_id in deleted_edge_ids:
        root.remove(existing_edges[edge_id])
    for node_id in deleted_node_ids:
        root.remove(nodes[node_id]["cell"])
    for phase_id in deleted_phase_ids:
        root.remove(phases[phase_id])


def apply_node_updates(
    nodes: dict[str, dict], changes: dict, allow_geometry_updates: bool,
) -> set[str]:
    """Update existing nodes and their cached geometry; return actual motion IDs."""
    moved_node_ids: set[str] = set()

    for update in changes.get("update_nodes", []):
        semantic_id = update.get("id")
        if semantic_id not in nodes:
            raise contracts.DiagramError(f"Cannot update missing node: {semantic_id}")
        cell = nodes[semantic_id]["cell"]
        if "label" in update:
            cell.attrib["value"] = str(update["label"])
        if "type" in update:
            kind = update["type"]
            if kind not in construction.NODE_STYLES:
                raise contracts.DiagramError(f"Unsupported node type: {kind}")
            cell.attrib["style"] = construction.NODE_STYLES[kind]
            cell.attrib[contracts.DATA_NODE_TYPE] = kind
        requested_geometry = any(key in update for key in ("x", "y", "width", "height"))
        if requested_geometry and not allow_geometry_updates:
            raise contracts.DiagramError("Existing geometry update requires --allow-geometry-updates")
        if requested_geometry:
            geom = cell.find("mxGeometry")
            assert geom is not None
            previous_geometry = document.parse_geometry(cell)
            kind = cell.attrib.get(contracts.DATA_NODE_TYPE, "process")
            geometry_update = dict(update)
            if kind in sizing.FIXED_ASPECT_NODE_TYPES:
                update_width = geometry_update.get("width")
                update_height = geometry_update.get("height")
                if update_width is not None and update_height is not None:
                    if abs(float(update_width) - float(update_height)) >= core_geometry.GEOMETRY_TOLERANCE:
                        raise contracts.DiagramError(
                            f"Fixed-aspect node {semantic_id} requires equal width and height",
                            code="geometry/fixed-aspect-ratio",
                            subject={"kind": "node", "id": semantic_id},
                            evidence={"width": update_width, "height": update_height},
                            supported_fixes=["set-equal-width-and-height", "remove-one-size-dimension"],
                        )
                elif update_width is not None:
                    geometry_update["height"] = update_width
                elif update_height is not None:
                    geometry_update["width"] = update_height
            for key in ("x", "y", "width", "height"):
                if key in geometry_update:
                    geom.attrib[key] = contracts.number(geometry_update[key])
            nodes[semantic_id]["geometry"] = document.parse_geometry(cell)
            if nodes[semantic_id]["geometry"] != previous_geometry:
                moved_node_ids.add(semantic_id)
    return moved_node_ids


def apply_node_additions(
    scene: PatchScene, changes: dict, lane_order: list[str], lane_shifts: list[dict],
) -> NodeAdditions:
    """Add nodes against current records, retaining lane expansion and anchor order."""
    root, pool, values = scene.root, scene.pool, scene.values
    lanes, nodes, pool_width = scene.lanes, scene.nodes, scene.pool_width
    new_nodes = changes.get("nodes", [])
    schema_version = pool.attrib.get(contracts.DATA_SCHEMA_VERSION, "1")
    if schema_version != contracts.V3_SCHEMA_VERSION:
        v3_new_nodes = [
            node["id"] for node in new_nodes if "slot" in node or "anchor" in node
        ]
        if v3_new_nodes:
            raise contracts.DiagramError(
                "slot and anchor intent require a schema version 3 diagram",
                code="schema/version-field",
                evidence={"nodes": v3_new_nodes, "schema_version": schema_version},
                supported_fixes=["migrate-to-v3", "remove-v3-fields"],
            )
    new_node_by_id = {node["id"]: node for node in new_nodes}
    new_node_ids = set(new_node_by_id)
    all_target_ids = set(nodes) | new_node_ids
    for node in new_nodes:
        if node["id"] in nodes:
            raise contracts.DiagramError(f"Node already exists: {node['id']}")
        if node.get("lane") not in lanes:
            raise contracts.DiagramError(f"Unknown lane for node {node.get('id')}: {node.get('lane')}")
        anchor = node.get("anchor")
        if anchor and anchor["node"] not in all_target_ids:
            raise contracts.DiagramError(
                f"Note {node['id']} anchors to a missing node",
                code="semantic/anchor-target",
                subject={"kind": "node", "id": node["id"]},
                evidence={"anchor": anchor["node"]},
            )
        if not anchor:
            continue
        target_id = anchor["node"]
        if target_id in nodes:
            target_lane = nodes[target_id]["lane"]
            target_rank = int(nodes[target_id]["cell"].attrib.get(contracts.DATA_RANK, "0"))
        else:
            target = new_node_by_id[target_id]
            target_lane = target["lane"]
            target_rank = int(target["rank"])
        if target_lane != node["lane"] or target_rank != int(node["rank"]):
            raise contracts.DiagramError(
                f"Note {node['id']} must share lane and rank with its anchor",
                code="semantic/anchor-alignment",
                subject={"kind": "node", "id": node["id"]},
                evidence={
                    "anchor": target_id,
                    "note_lane": node["lane"],
                    "anchor_lane": target_lane,
                    "note_rank": int(node["rank"]),
                    "anchor_rank": target_rank,
                },
                supported_fixes=["align-note-with-anchor", "remove-anchor"],
            )
        if "slot" in node and node["slot"] != anchor["side"]:
            raise contracts.DiagramError(
                f"Note {node['id']} slot conflicts with its anchor side",
                code="layout/anchor-slot-conflict",
                subject={"kind": "node", "id": node["id"]},
                evidence={"slot": node["slot"], "anchor_side": anchor["side"]},
                supported_fixes=["match-slot-to-anchor", "remove-explicit-slot"],
            )

    layout_profile_name = pool.attrib.get(contracts.DATA_LAYOUT_PROFILE, "review")
    ordered_new_nodes = sorted(new_nodes, key=lambda node: bool(node.get("anchor")))
    for node in ordered_new_nodes:
        lane_cell = lanes[node["lane"]]["cell"]
        lane_width = lanes[node["lane"]]["geometry"]["width"]
        automatic_x = None
        if schema_version == contracts.V3_SCHEMA_VERSION and "x" not in node:
            automatic_x = patch_node_automatic_x(
                node,
                lanes[node["lane"]],
                nodes,
                layout_profile_name,
            )
            width, _ = sizing.node_size(node)
            side_padding = layout.PROFILE_SIDE_PADDING.get(
                layout_profile_name,
                layout.PROFILE_SIDE_PADDING["review"],
            )
            required_width = automatic_x + width + side_padding
            if required_width > lane_width + core_geometry.GEOMETRY_TOLERANCE:
                before_reflow = {
                    lane_id: dict(record["geometry"])
                    for lane_id, record in lanes.items()
                }
                lane_geom = lane_cell.find("mxGeometry")
                assert lane_geom is not None
                lane_geom.attrib["width"] = contracts.number(math.ceil(required_width))
                lanes[node["lane"]]["geometry"] = document.parse_geometry(lane_cell)
                expansion_shifts = reflow_lane_order_geometry(
                    pool,
                    lane_order,
                    lanes,
                    before_reflow,
                )
                by_lane = {item["id"]: item for item in lane_shifts}
                for item in expansion_shifts:
                    existing = by_lane.get(item["id"])
                    if existing:
                        existing["to_x"] = item["to_x"]
                        existing["to_width"] = item["to_width"]
                    else:
                        lane_shifts.append(item)
                        by_lane[item["id"]] = item
                lane_width = lanes[node["lane"]]["geometry"]["width"]
                pool_width = document.parse_geometry(pool)["width"]
        created = construction.create_node_cell(
            root,
            lane_cell,
            node,
            lane_width,
            values,
            automatic_x=automatic_x,
        )
        nodes[node["id"]] = {
            "cell": created,
            "geometry": document.parse_geometry(created),
            "lane": node["lane"],
        }
    scene.pool_width = pool_width
    return NodeAdditions(new_nodes, schema_version)


def apply_group_patch(
    pool: ET.Element, lanes: dict[str, dict], nodes: dict[str, dict], changes: dict,
    schema_version: str,
) -> GroupChanges:
    """Apply groups after node-record refresh and before edge-update validation."""
    group_patch_requested = any(
        field in changes for field in ("update_groups", "groups", "delete_groups")
    )
    if schema_version == contracts.V3_SCHEMA_VERSION:
        added_group_ids, updated_group_ids, deleted_group_ids = apply_group_operations(
            pool, lanes, nodes, changes
        )
    elif group_patch_requested:
        raise contracts.DiagramError(
            "Group patch operations require a schema version 3 diagram",
            code="schema/version-field",
            evidence={"schema_version": schema_version},
            supported_fixes=["migrate-to-v3", "remove-group-operations"],
        )
    else:
        added_group_ids, updated_group_ids, deleted_group_ids = [], [], []
    return GroupChanges(added_group_ids, updated_group_ids, deleted_group_ids)


def apply_edge_operations(
    scene: PatchScene, before_patch: ET.ElementTree, changes: dict,
    existing_edges: dict[str, ET.Element], edge_updates: list[dict],
    moved_node_ids: set[str], new_nodes: list[dict], lane_shifts: list[dict],
) -> EdgeChanges:
    """Plan only declared/new edges, stage labels, then apply the completed batch."""
    root, pool, lanes, nodes = scene.root, scene.pool, scene.lanes, scene.nodes
    for update in edge_updates:
        if update["id"] not in existing_edges:
            raise contracts.DiagramError(f"Cannot update missing edge: {update['id']}", code="patch/missing-edge")

    explicit_reroute_ids = {
        update["id"] for update in edge_updates
        if edge_route_update_requested(existing_edges[update["id"]], update)
    }
    label_updated_ids = {
        update["id"] for update in edge_updates
        if "label" in update and str(update["label"]) != existing_edges[update["id"]].get("value", "")
    }
    changed_lane_ids = {item["id"] for item in lane_shifts}
    lane_impacted_edge_ids = {
        edge_id
        for edge_id, cell in existing_edges.items()
        if (
            nodes.get(cell.attrib.get(contracts.DATA_FROM, ""), {}).get("lane")
            in changed_lane_ids
            or nodes.get(cell.attrib.get(contracts.DATA_TO, ""), {}).get("lane")
            in changed_lane_ids
        )
    }
    manual_waypoint_edges_affected_by_lane_changes = sorted(
        edge_id
        for edge_id in lane_impacted_edge_ids
        if existing_edges[edge_id].attrib.get(contracts.DATA_WAYPOINTS_ORIGIN) == "explicit"
    )
    before_lanes, before_nodes = document.lane_node_records(
        document.graph_root(before_patch), document.find_pool(before_patch),
    )
    before_edges = document.edge_records(document.graph_root(before_patch))
    spatial_scene_changed = bool(moved_node_ids or changed_lane_ids or new_nodes)
    invalid_frozen_routes = {
        edge_id
        for edge_id, cell in existing_edges.items()
        if spatial_scene_changed and edge_id not in explicit_reroute_ids
        and routing_adapter.edge_route_is_locally_valid(before_edges[edge_id], before_lanes, before_nodes)
        and not routing_adapter.edge_route_is_locally_valid(cell, lanes, nodes)
    }
    if invalid_frozen_routes:
        raise contracts.DiagramError(
            "Node or lane changes invalidate saved routes; declare the affected edge reroutes",
            code="patch/route-update-required", evidence={"edges": sorted(invalid_frozen_routes)},
            supported_fixes=["add-update-edges-reroute", "retain-node-lane-geometry"],
        )
    auto_reroute_ids: set[str] = set()
    reroute_ids = explicit_reroute_ids | auto_reroute_ids
    update_by_id = {update["id"]: update for update in edge_updates}
    effective_main_path = list(changes.get("main_path", document.read_main_path(pool)))
    updated_specs: dict[str, dict] = {}
    for semantic_id, cell in existing_edges.items():
        edge = routing_adapter.existing_edge_spec(cell, for_reroute=semantic_id in reroute_ids)
        edge.update(
            {
                key: value
                for key, value in update_by_id.get(semantic_id, {}).items()
                if key != "reroute"
            }
        )
        updated_specs[semantic_id] = edge

    new_edges = changes.get("edges", [])
    # Resolve text changes on a detached scene before reserving frozen label
    # obstacles. A failed batch must not leak partial edge text/geometry.
    staged_root = copy.deepcopy(root)
    staged_edges = document.edge_records(staged_root)
    for edge_id in label_updated_ids - reroute_ids:
        staged_edges[edge_id].set("value", str(update_by_id[edge_id]["label"]))
    routing_adapter.reflow_mutable_edge_labels(
        staged_root, pool, lanes, nodes, label_updated_ids - reroute_ids,
        preserve_position=True, exclude_obstacles=reroute_ids,
    )
    routing_context = routing.new_routing_context(
        effective_main_path,
        [*updated_specs.values(), *new_edges],
        document.routing_node_views(nodes),
        v3_semantics=pool.attrib.get(contracts.DATA_SCHEMA_VERSION) == contracts.V3_SCHEMA_VERSION,
    )
    routing_context["arrowhead_clearance_profiles"] = (
        routing_adapter.arrowhead_clearance_profiles(
            [*updated_specs.values(), *new_edges], lanes, nodes,
            existing_edges=existing_edges,
        )
    )
    routing_adapter.seed_routing_context(
        routing_context, staged_edges, lanes, nodes, exclude=reroute_ids,
        require_measurable=bool(reroute_ids or new_edges), pool=pool,
    )
    for edge in new_edges:
        if edge["id"] in existing_edges:
            raise contracts.DiagramError(f"Edge already exists: {edge['id']}")
    mutable_edge_ids = set(reroute_ids) | {edge["id"] for edge in new_edges}
    all_specs = [*updated_specs.values(), *new_edges]
    # Share the exact explicit-field and point-preservation decisions between
    # native preflight and final writeback; compiled defaults are not updates.
    reroute_explicit_fields = {}
    reroute_points_actions = {}
    for edge_id in sorted(reroute_ids):
        update = update_by_id.get(edge_id, {})
        existing_explicit = {
            field for field, marker in (
                ("waypoints", contracts.DATA_WAYPOINTS_ORIGIN),
                ("exit_side", contracts.DATA_EXIT_SIDE_EXPLICIT),
                ("entry_side", contracts.DATA_ENTRY_SIDE_EXPLICIT),
                ("exit_offset", contracts.DATA_EXIT_OFFSET_EXPLICIT),
                ("entry_offset", contracts.DATA_ENTRY_OFFSET_EXPLICIT),
            ) if existing_edges[edge_id].attrib.get(marker) in {"explicit", "1"}
        }
        reroute_explicit_fields[edge_id] = existing_explicit | set(update)
        reroute_points_actions[edge_id] = (
            "replace_explicit" if "waypoints" in update
            else "preserve_existing" if "waypoints" in existing_explicit
            else "replace_automatic"
        )
    routing_context["native_label_profiles"] = routing_adapter.native_label_profiles(
        [edge for edge in all_specs if edge["id"] in mutable_edge_ids], pool, lanes, nodes,
        existing_edges=existing_edges,
        explicit_by_edge=reroute_explicit_fields, points_actions_by_edge=reroute_points_actions,
    )
    batch = routing.plan_route_batch(
        all_specs, document.routing_lane_views(lanes), document.routing_node_views(nodes),
        main_path=effective_main_path, mutable_edge_ids=mutable_edge_ids,
        routing_context=routing_context,
        v3_semantics=pool.attrib.get(contracts.DATA_SCHEMA_VERSION) == contracts.V3_SCHEMA_VERSION,
    )
    if batch.status != routing.ROUTE_COMPLETE:
        raise routing_adapter.route_batch_error(batch)
    decisions = {decision.edge_id: decision for decision in batch.decisions}
    for edge_id in label_updated_ids - reroute_ids:
        existing_edges[edge_id].attrib.clear()
        existing_edges[edge_id].attrib.update(staged_edges[edge_id].attrib)
        existing_edges[edge_id][:] = [copy.deepcopy(child) for child in staged_edges[edge_id]]
    for edge_id, update in update_by_id.items():
        for field, attribute in (("flow_role", contracts.DATA_FLOW_ROLE), ("outcome", contracts.DATA_OUTCOME)):
            if field in update:
                existing_edges[edge_id].set(attribute, str(update[field]))
    # No edge XML has been changed before this point.  Existing records retain
    # their unrelated style tokens/geometry children; additions are appended.
    for edge_id in sorted(reroute_ids):
        edge = updated_specs[edge_id]
        explicit = reroute_explicit_fields[edge_id]
        points_action = reroute_points_actions[edge_id]
        routing_adapter.apply_route_decision(
            existing_edges[edge_id], edge, decisions[edge_id], lanes, nodes,
            existing=True, explicit_fields=explicit, points_action=points_action,
            routing_context=routing_context,
        )
    for edge in routing.edge_routing_order(new_edges, effective_main_path, document.routing_node_views(nodes)):
        construction.create_edge_cell(
            root, pool, edge, lanes, nodes, routing_context=routing_context,
            decision=decisions[edge["id"]], explicit_fields=set(edge),
        )
    routing_adapter.apply_label_plan(document.edge_records(root), batch.decisions, batch.label_choices)
    routing_adapter.reflow_mutable_edge_labels(
        root, pool, lanes, nodes, mutable_edge_ids - set(batch.label_choices),
        routing_context.get("label_sides", {}),
        preserve_position=True,
    )
    return EdgeChanges(
        new_edges, explicit_reroute_ids, auto_reroute_ids, reroute_ids,
        label_updated_ids, manual_waypoint_edges_affected_by_lane_changes,
    )


def apply_phase_operations(
    root: ET.Element, pool: ET.Element, phases: dict[str, ET.Element],
    values: dict, changes: dict, pool_width: float,
) -> None:
    """Update and append phases using the caller's current phase map and width."""
    for update in changes.get("update_phases", []):
        phase_id = update["id"]
        if phase_id not in phases:
            raise contracts.DiagramError(f"Cannot update missing phase: {phase_id}", code="patch/missing-phase")
        construction.apply_phase_update(phases[phase_id], update, values, pool_width)
    for phase in changes.get("phases", []):
        if phase["id"] in phases:
            raise contracts.DiagramError(f"Phase already exists: {phase['id']}", code="patch/duplicate-phase")
        construction.create_phase_cell(root, pool, phase, values, pool_width)


def apply_main_path_update(
    pool: ET.Element, changes: dict, deleted_node_ids: set[str],
) -> None:
    """Write a declared main path and retain the final deleted-node check."""
    if "main_path" in changes:
        pool.attrib[contracts.DATA_MAIN_PATH] = json.dumps(
            changes["main_path"], ensure_ascii=True, separators=(",", ":")
        )
        if pool.attrib.get(contracts.DATA_SCHEMA_VERSION) not in contracts.STRUCTURED_SCHEMA_VERSIONS:
            pool.attrib[contracts.DATA_SCHEMA_VERSION] = contracts.SCHEMA_VERSION
    elif deleted_node_ids.intersection(document.read_main_path(pool)):
        raise contracts.DiagramError(
            "Deleting a main_path node requires supplying the replacement main_path",
            code="patch/main-path",
            evidence={"deleted_nodes": sorted(deleted_node_ids.intersection(document.read_main_path(pool)))},
            supported_fixes=["supply-main-path"],
        )


def extend_canvas_for_nodes(
    pool: ET.Element, lanes: dict[str, dict], values: dict, new_nodes: list[dict],
) -> None:
    """Extend height only for newly added ranks; do not refresh cached records."""
    requested_max_rank = max(
        [int(pool.attrib.get(contracts.DATA_MAX_RANK, "1"))]
        + [int(node["rank"]) for node in new_nodes]
    )
    if requested_max_rank > int(pool.attrib.get(contracts.DATA_MAX_RANK, "1")):
        new_lane_height = layout.lane_height(requested_max_rank, values)
        for lane in lanes.values():
            lane["cell"].find("mxGeometry").attrib["height"] = contracts.number(new_lane_height)
        pool_geom = pool.find("mxGeometry")
        assert pool_geom is not None
        pool_geom.attrib["height"] = contracts.number(values["title_height"] + new_lane_height)
        pool.attrib[contracts.DATA_MAX_RANK] = str(requested_max_rank)


def refresh_phase_geometry(
    phases: dict[str, ET.Element], values: dict, pool_width: float,
) -> None:
    """Recompute current phase geometry after the caller's final phase refresh."""
    for cell in phases.values():
        construction.apply_phase_update(cell, construction.phase_cell_spec(cell), values, pool_width)
