"""Saved-diagram operations, declared comparison and candidate validation.

Compare replays patch on a copy. Patch uses lower-level operations and never
calls compare; file replacement and authorization remain outside this module.
"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET

from . import (
    construction, contracts, document, layout, metadata, patch_operations,
    routing_adapter, spec_validation, validation as core_validation,
)


def saved_edge_preservation_guard(before: ET.ElementTree, candidate: ET.ElementTree, changes: dict) -> dict:
    """Check saved XML independently of patch replay and route planning.

    The only removable projection is declared text/label position or two
    descriptive metadata fields. Every point (including duplicates), native
    port, origin marker and opaque child remains in the protected projection.
    """
    document.route_mutable_opaque_guard(before, candidate, changes)
    before_edges = document.edge_records(document.graph_root(before))
    after_edges = document.edge_records(document.graph_root(candidate))
    updates = {item["id"]: item for item in changes.get("update_edges", [])}
    deleted = set(changes.get("delete_edges", []))
    frozen = {
        edge_id for edge_id, cell in before_edges.items()
        if edge_id not in deleted and not patch_operations.edge_route_update_requested(cell, updates.get(edge_id, {}))
    }
    left, right = copy.deepcopy(before), copy.deepcopy(candidate)
    left_edges = document.edge_records(document.graph_root(left))
    right_edges = document.edge_records(document.graph_root(right))
    changed_routes = []
    for edge_id in sorted(frozen):
        old, new = left_edges[edge_id], right_edges.get(edge_id)
        if new is None:
            changed_routes.append(edge_id)
            continue
        update = updates.get(edge_id, {})
        label_changed = "label" in update and str(update["label"]) != before_edges[edge_id].get("value", "")
        for cell in (old, new):
            for field, attribute in (("flow_role", contracts.DATA_FLOW_ROLE), ("outcome", contracts.DATA_OUTCOME)):
                if field in update:
                    cell.attrib.pop(attribute, None)
            if label_changed:
                cell.attrib.pop("value", None)
                for key in (contracts.DATA_LABEL_LEFT, contracts.DATA_LABEL_TOP,
                            contracts.DATA_LABEL_WIDTH, contracts.DATA_LABEL_HEIGHT,
                            contracts.DATA_LABEL_SEGMENT):
                    cell.attrib.pop(key, None)
                # Only the first native geometry's known label coordinates
                # may change. Duplicate geometry and extension payload stay.
                geometry = cell.find("mxGeometry")
                if geometry is not None:
                    for key in ("x", "y", "relative"):
                        geometry.attrib.pop(key, None)
                    offset = geometry.find("./mxPoint[@as='offset']")
                    if offset is None:
                        offset = ET.SubElement(geometry, "mxPoint", {"as": "offset"})
                    for key in ("x", "y"):
                        offset.attrib.pop(key, None)
    differences = document.cell_payload_changes(left, right)
    affected = {item["semantic_id"] for item in differences
                if item["kind"] == "edge" and item["semantic_id"] in frozen}
    affected.update(edge_id for edge_id in frozen & right_edges.keys()
                    if document.comparison_attributes(left_edges[edge_id]) !=
                       document.comparison_attributes(right_edges[edge_id]))
    for edge_id, old in before_edges.items():
        if (edge_id in after_edges and old.get(contracts.DATA_WAYPOINTS_ORIGIN) == "explicit"
                and "waypoints" not in updates.get(edge_id, {})):
            old_payload = document.cell_payload_signatures(before)[f"edge:{edge_id}"]
            new_payload = document.cell_payload_signatures(candidate)[f"edge:{edge_id}"]
            def point_payload(payload):
                return {path: value for path, value in payload.items() if "/Array[" in path}
            if point_payload(old_payload) != point_payload(new_payload):
                affected.add(edge_id)
    # Payload comparison includes attributes; the explicit missing case is
    # retained because content diff helpers only compare shared semantic IDs.
    violations = sorted(set(changed_routes) | set(affected))
    if violations:
        raise contracts.DiagramError(
            "Patch changed protected saved edge content",
            code="patch/preservation-violation", evidence={"edges": violations, "differences": differences},
            supported_fixes=["retain-saved-edge", "declare-edge-reroute"],
        )
    return {"checked": len(frozen), "preserved": True if frozen else None, "changed_edges": []}


def patch_tree(tree: ET.ElementTree, changes: dict, allow_geometry_updates: bool) -> dict:
    spec_validation.validate_patch_spec(changes)
    before_patch = copy.deepcopy(tree)
    pool = document.find_pool(tree)
    root = document.graph_root(tree)
    values = document.values_from_pool(pool, layout.DEFAULTS)
    lanes, nodes = document.lane_node_records(root, pool)
    existing_edges = document.edge_records(root)
    explicit_waypoints_before = {
        edge_id: document.edge_waypoints(cell)
        for edge_id, cell in existing_edges.items()
        if cell.attrib.get(contracts.DATA_WAYPOINTS_ORIGIN) == "explicit"
    }
    phases = document.phase_records(root, pool)
    had_phases_before_patch = bool(phases)
    pool_width = max(
        record["geometry"]["x"] + record["geometry"]["width"]
        for record in lanes.values()
    )

    scene = patch_operations.PatchScene(root, pool, values, lanes, nodes, pool_width)
    deletions = patch_operations.check_deletion_dependencies(scene, changes, existing_edges, phases)
    deleted_edge_ids, deleted_lane_ids = deletions.edge_ids, deletions.lane_ids
    deleted_node_ids, deleted_phase_ids = deletions.node_ids, deletions.phase_ids
    edge_updates = patch_operations.check_node_type_dependencies(
        nodes, changes, existing_edges, deleted_edge_ids,
    )
    patch_operations.apply_declared_deletions(root, nodes, existing_edges, phases, deletions)

    lanes, nodes = document.lane_node_records(root, pool)
    lane_order, lanes, lane_shifts = patch_operations.apply_lane_operations(
        root, pool, lanes, values, changes
    )
    pool_width = document.parse_geometry(pool)["width"]
    existing_edges = document.edge_records(root)
    phases = document.phase_records(root, pool)
    scene.lanes, scene.nodes, scene.pool_width = lanes, nodes, pool_width
    moved_node_ids = patch_operations.apply_node_updates(nodes, changes, allow_geometry_updates)
    additions = patch_operations.apply_node_additions(scene, changes, lane_order, lane_shifts)
    new_nodes, schema_version, pool_width = additions.new_nodes, additions.schema_version, scene.pool_width

    lanes, nodes = document.lane_node_records(root, pool)
    scene.lanes, scene.nodes = lanes, nodes
    groups = patch_operations.apply_group_patch(pool, lanes, nodes, changes, schema_version)
    added_group_ids, updated_group_ids, deleted_group_ids = groups.added_ids, groups.updated_ids, groups.deleted_ids
    existing_edges = document.edge_records(root)
    edges = patch_operations.apply_edge_operations(
        scene, before_patch, changes, existing_edges, edge_updates,
        moved_node_ids, new_nodes, lane_shifts,
    )
    new_edges = edges.new_edges
    explicit_reroute_ids, auto_reroute_ids = edges.explicit_reroute_ids, edges.auto_reroute_ids
    reroute_ids, label_updated_ids = edges.reroute_ids, edges.label_updated_ids
    manual_waypoint_edges_affected_by_lane_changes = edges.manual_waypoint_edges_affected_by_lane_changes

    phases = document.phase_records(root, pool)
    patch_operations.apply_phase_operations(root, pool, phases, values, changes, pool_width)
    patch_operations.apply_main_path_update(pool, changes, deleted_node_ids)
    patch_operations.extend_canvas_for_nodes(pool, lanes, values, new_nodes)

    phases = document.phase_records(root, pool)
    patch_operations.refresh_phase_geometry(phases, values, pool_width)

    construction.normalize_phase_layering(
        root,
        pool,
        restore_lane_fill_without_phases=had_phases_before_patch,
    )
    metadata.refresh_managed_metadata(tree)

    remaining_explicit_waypoints = {
        edge_id: points
        for edge_id, points in explicit_waypoints_before.items()
        if edge_id not in deleted_edge_ids
    }
    final_edges = document.edge_records(root)
    saved_routes = saved_edge_preservation_guard(before_patch, tree, changes)
    original_edges = document.edge_records(document.graph_root(before_patch))
    def label_position(cell):
        geometry = cell.find("mxGeometry")
        if geometry is None:
            return None
        return (tuple((key, geometry.get(key)) for key in ("x", "y", "relative")),
                document.element_signature(geometry.find("./mxPoint[@as='offset']")))
    label_repositioned_ids = sorted(
        edge_id for edge_id in (label_updated_ids | reroute_ids)
        if label_position(original_edges[edge_id]) != label_position(final_edges[edge_id])
    )
    rerouted_ids = sorted(
        edge_id for edge_id in reroute_ids
        if (original_edges[edge_id].get("source"), original_edges[edge_id].get("target"),
            document.port_from_style(original_edges[edge_id], "exit"), document.port_from_style(original_edges[edge_id], "entry"),
            document.edge_waypoints(original_edges[edge_id])) !=
           (final_edges[edge_id].get("source"), final_edges[edge_id].get("target"),
            document.port_from_style(final_edges[edge_id], "exit"), document.port_from_style(final_edges[edge_id], "entry"),
            document.edge_waypoints(final_edges[edge_id]))
    )
    manual_waypoints_preserved = (
        all(
            edge_id in final_edges
            and final_edges[edge_id].attrib.get(contracts.DATA_WAYPOINTS_ORIGIN) == "explicit"
            and document.edge_waypoints(final_edges[edge_id]) == points
            for edge_id, points in remaining_explicit_waypoints.items()
        )
        if remaining_explicit_waypoints
        else None
    )

    return {
        "added_lanes": sorted(lane["id"] for lane in changes.get("lanes", [])),
        "updated_lanes": sorted(lane["id"] for lane in changes.get("update_lanes", [])),
        "deleted_lanes": sorted(deleted_lane_ids),
        "lane_order": lane_order,
        "lane_geometry_changes": sorted(lane_shifts, key=lambda item: item["id"]),
        "dependent_lane_shifts": sorted(
            (
                item
                for item in lane_shifts
                if item["id"]
                not in {lane["id"] for lane in changes.get("update_lanes", [])}
            ),
            key=lambda item: item["id"],
        ),
        "updated_nodes": sorted(update["id"] for update in changes.get("update_nodes", [])),
        "updated_edges": sorted(update["id"] for update in edge_updates),
        "label_updated_edges": sorted(label_updated_ids),
        "label_repositioned_edges": label_repositioned_ids,
        "rerouted_edges": rerouted_ids,
        "saved_routes": saved_routes,
        "auto_rerouted_edges": sorted(auto_reroute_ids - explicit_reroute_ids),
        "added_nodes": sorted(node["id"] for node in new_nodes),
        "added_edges": sorted(edge["id"] for edge in new_edges),
        "deleted_nodes": sorted(deleted_node_ids),
        "deleted_edges": sorted(deleted_edge_ids),
        "added_phases": sorted(phase["id"] for phase in changes.get("phases", [])),
        "updated_phases": sorted(phase["id"] for phase in changes.get("update_phases", [])),
        "deleted_phases": sorted(deleted_phase_ids),
        "added_groups": added_group_ids,
        "updated_groups": updated_group_ids,
        "deleted_groups": deleted_group_ids,
        "main_path_updated": "main_path" in changes,
        "manual_waypoints_preserved": manual_waypoints_preserved,
        "manual_waypoints_checked": len(remaining_explicit_waypoints),
        "manual_waypoint_edges_affected_by_lane_changes": (
            manual_waypoint_edges_affected_by_lane_changes
        ),
        "requested_changes": {
            "lanes": sorted(
                {lane["id"] for lane in changes.get("lanes", [])}
                | {lane["id"] for lane in changes.get("update_lanes", [])}
                | deleted_lane_ids
            ),
            "nodes": sorted(
                {node["id"] for node in new_nodes}
                | {node["id"] for node in changes.get("update_nodes", [])}
                | deleted_node_ids
            ),
            "edges": sorted(
                {edge["id"] for edge in new_edges}
                | {edge["id"] for edge in edge_updates}
                | deleted_edge_ids
            ),
        },
        "dependency_changes": {
            "shifted_lanes": sorted(item["id"] for item in lane_shifts),
            "auto_rerouted_edges": sorted(auto_reroute_ids - explicit_reroute_ids),
        },
    }


def allowed_missing_from_patch(changes: dict | None) -> set[str]:
    if not changes:
        return set()
    allowed = {f"lane:{semantic_id}" for semantic_id in changes.get("delete_lanes", [])}
    allowed.update(f"node:{semantic_id}" for semantic_id in changes.get("delete_nodes", []))
    allowed.update(f"edge:{semantic_id}" for semantic_id in changes.get("delete_edges", []))
    allowed.update(f"phase:{semantic_id}" for semantic_id in changes.get("delete_phases", []))
    return allowed


def compare_trees(before: ET.ElementTree, after: ET.ElementTree, changes: dict | None = None) -> dict:
    """Compare an actual result with the exact result of the declared patch.

    A patch declaration is executable, not an allowlist. Replaying it on a
    copy of ``before`` makes every permitted attribute, geometry change,
    reroute, addition, and deletion explicit. This prevents an unrelated edit
    from being hidden behind a broadly authorized semantic cell.
    """
    def geometry_payloads(tree):
        return {key: {path: value for path, value in fields.items()
                      if path.startswith("mxCell/mxGeometry[")}
                for key, fields in document.cell_payload_signatures(tree).items()}

    before_payloads = geometry_payloads(before)
    after_payloads = geometry_payloads(after)
    before_cells = document.semantic_cells(before)
    after_cells = document.semantic_cells(after)
    missing = sorted(set(before_cells) - set(after_cells))
    added = sorted(set(after_cells) - set(before_cells))
    changed_geometry: list[str] = []
    changed_attributes: list[str] = []

    for key in sorted(set(before_cells) & set(after_cells)):
        before_cell = before_cells[key]
        after_cell = after_cells[key]
        if before_payloads[key] != after_payloads[key]:
            changed_geometry.append(key)
        before_attributes = document.comparison_attributes(before_cell)
        after_attributes = document.comparison_attributes(after_cell)
        if before_attributes != after_attributes:
            changed_attributes.append(key)

    if changes is None:
        expected_tree = before
        expected_cells = before_cells
        expected_added: set[str] = set()
        expected_missing: set[str] = set()
    else:
        expected_tree = copy.deepcopy(before)
        patch_tree(expected_tree, changes, allow_geometry_updates=True)
        expected_cells = document.semantic_cells(expected_tree)
        expected_added = set(expected_cells) - set(before_cells)
        expected_missing = set(before_cells) - set(expected_cells)

    expected_payloads = geometry_payloads(expected_tree)
    expected_geometry: list[str] = []
    expected_attributes: list[str] = []
    unexpected_geometry: list[str] = []
    unexpected_attributes: list[str] = []
    common_expected_actual = set(expected_cells) & set(after_cells)
    for key in sorted(common_expected_actual):
        expected_cell = expected_cells[key]
        actual_cell = after_cells[key]
        if expected_payloads[key] != after_payloads[key]:
            unexpected_geometry.append(key)
        expected_cell_attributes = document.comparison_attributes(expected_cell)
        actual_attributes = document.comparison_attributes(actual_cell)
        if expected_cell_attributes != actual_attributes:
            unexpected_attributes.append(key)
        if key in before_cells:
            before_cell = before_cells[key]
            if before_payloads[key] != expected_payloads[key]:
                expected_geometry.append(key)
            if document.comparison_attributes(before_cell) != expected_cell_attributes:
                expected_attributes.append(key)

    actual_keys = set(after_cells)
    expected_keys = set(expected_cells)
    unexpected_added = sorted(actual_keys - expected_keys)
    unexpected_expected_missing = sorted(expected_keys - actual_keys)
    unexpected_missing = sorted(
        (set(missing) - expected_missing) | set(unexpected_expected_missing)
    )
    allowed_missing = sorted(expected_missing)
    allowed = sorted(
        set(expected_geometry)
        | set(expected_attributes)
        | expected_added
        | expected_missing
    )
    changed_order = document.sibling_order_changes(before, after)
    unexpected_order = document.sibling_order_changes(expected_tree, after)
    expected_unmanaged = document.unmanaged_cell_signatures(expected_tree)
    actual_unmanaged = document.unmanaged_cell_signatures(after)
    unexpected_unmanaged = []
    for cell_id in sorted(expected_unmanaged.keys() | actual_unmanaged.keys(),
                          key=lambda value: value or ""):
        if expected_unmanaged.get(cell_id) != actual_unmanaged.get(cell_id):
            change = ("added" if cell_id not in expected_unmanaged else
                      "missing" if cell_id not in actual_unmanaged else "changed")
            unexpected_unmanaged.append({"cell_id": cell_id, "change": change})
    changed_content = document.cell_payload_changes(before, after)
    unexpected_content = document.cell_payload_changes(expected_tree, after)
    preservation_errors = []
    if changes is not None:
        try:
            saved_edge_preservation_guard(before, after, changes)
        except contracts.DiagramError as error:
            if error.code != "patch/preservation-violation":
                raise
            preservation_errors.append(error.diagnostic())
    preserved = (
        not unexpected_missing
        and not unexpected_geometry
        and not unexpected_attributes
        and not unexpected_added
        and not unexpected_order
        and not unexpected_unmanaged
        and not unexpected_content
        and not preservation_errors
    )
    result = {
        "preserved": preserved,
        "existing_cells_checked": len(set(before_cells) & set(after_cells)),
        "added_cells": added,
        "missing_cells": missing,
        "changed_geometry": changed_geometry,
        "changed_attributes": changed_attributes,
        "allowed_changes": allowed,
        "allowed_missing": allowed_missing,
        "unexpected_missing": unexpected_missing,
        "unexpected_geometry": unexpected_geometry,
        "unexpected_attributes": unexpected_attributes,
        "unexpected_added": unexpected_added,
    }
    # Add evidence only when applicable, keeping existing clean CLI receipts
    # byte-for-byte compatible. Missing optional fields mean no such change.
    if changed_order:
        result["changed_sibling_order"] = changed_order
    if unexpected_order:
        result["unexpected_sibling_order"] = unexpected_order
    if unexpected_unmanaged:
        result["unexpected_unmanaged_cells"] = unexpected_unmanaged
    if changed_content:
        result["changed_cell_content"] = changed_content
    if unexpected_content:
        result["unexpected_cell_content"] = unexpected_content
    if preservation_errors:
        result["unexpected_preservation"] = preservation_errors
    return result


def inspect_tree(tree: ET.ElementTree) -> dict:
    pool = document.find_pool(tree)
    root = document.graph_root(tree)
    lanes, nodes = document.lane_node_records(root, pool)
    phases = document.phase_records(root, pool)
    lane_items = sorted(lanes.items(), key=lambda item: item[1]["geometry"]["x"])
    lane_index = {lane_id: index for index, (lane_id, _) in enumerate(lane_items)}

    lane_specs = [
        {
            "id": lane_id,
            "label": record["cell"].attrib.get("value", ""),
            "x": record["geometry"]["x"],
            "width": record["geometry"]["width"],
        }
        for lane_id, record in lane_items
    ]
    node_specs = []
    for node_id, record in sorted(
        nodes.items(),
        key=lambda item: (
            int(item[1]["cell"].attrib.get(contracts.DATA_RANK, "0")),
            lane_index.get(item[1]["lane"], 999),
            item[0],
        ),
    ):
        node_spec = {
            "id": node_id,
            "lane": record["lane"],
            "rank": int(record["cell"].attrib.get(contracts.DATA_RANK, "0")),
            "type": record["cell"].attrib.get(contracts.DATA_NODE_TYPE, "process"),
            "label": record["cell"].attrib.get("value", ""),
            **record["geometry"],
        }
        if record["cell"].attrib.get(contracts.DATA_SLOT):
            node_spec["slot"] = record["cell"].attrib[contracts.DATA_SLOT]
        if record["cell"].attrib.get(contracts.DATA_ANCHOR):
            node_spec["anchor"] = json.loads(record["cell"].attrib[contracts.DATA_ANCHOR])
        if record["cell"].attrib.get(contracts.DATA_GROUP_ID):
            node_spec["group_id"] = record["cell"].attrib[contracts.DATA_GROUP_ID]
        node_specs.append(node_spec)

    edge_specs = []
    for edge_id, cell in sorted(document.edge_records(root).items()):
        edge = routing_adapter.existing_edge_spec(cell)
        points = document.edge_waypoints(cell)
        if points:
            edge["waypoints"] = [{"x": x, "y": y} for x, y in points]
        edge["label_geometry"] = document.edge_label_measurement(
            cell, document.edge_polyline(cell, lanes, nodes), {"lanes": lanes, "nodes": nodes, "pool": pool},
        )
        edge_specs.append(edge)

    phase_specs = [construction.phase_cell_spec(cell) for _, cell in sorted(phases.items())]
    validation = core_validation.validate_tree(tree)
    result = {
        "compatible": validation.get("managed_state") != "unsafe",
        "has_semantic_metadata": validation.get("has_semantic_metadata", True),
        "managed_state": validation.get("managed_state", "recoverable"),
        "tool_version": validation.get("tool_version"),
        "model_hash_version": validation.get("model_hash_version"),
        "stored_model_hash": validation.get("stored_model_hash"),
        "computed_model_hash": validation.get("computed_model_hash"),
        "model_hash_matches": validation.get("model_hash_matches"),
        "schema_version": pool.attrib.get(contracts.DATA_SCHEMA_VERSION, "1"),
        "title": pool.attrib.get("value", ""),
        "main_path": document.read_main_path(pool),
        "lanes": lane_specs,
        "phases": phase_specs,
        "nodes": node_specs,
        "edges": edge_specs,
        "unmanaged_edges": document.unmanaged_edge_specs(root, nodes),
        "validation": validation,
    }
    if result["schema_version"] == contracts.V3_SCHEMA_VERSION:
        result["behavior_pattern"] = pool.attrib.get(contracts.DATA_BEHAVIOR_PATTERN, "custom")
        result["layout"] = {
            "profile": pool.attrib.get(contracts.DATA_LAYOUT_PROFILE, "review"),
            "phase_presentation": pool.attrib.get(contracts.DATA_PHASE_PRESENTATION, "bands"),
        }
        result["groups"] = json.loads(pool.attrib.get(contracts.DATA_GROUPS, "[]"))
    return result


def _check_delivery_candidate(
    accepted: ET.ElementTree, candidate: ET.ElementTree, strict: bool,
    *, before: ET.ElementTree | None = None, changes: dict | None = None,
) -> dict:
    """Gate the parsed temporary file, before the atomic output replacement."""
    if document.serialization_signature(accepted) != document.serialization_signature(candidate):
        raise contracts.DiagramError(
            "Serialized candidate changed accepted XML structure or content",
            code="delivery/candidate-preservation-failed",
            evidence={"serialized_structure_matches": False},
        )
    preservation = compare_trees(accepted, candidate)
    if not preservation["preserved"]:
        raise contracts.DiagramError(
            "Serialized candidate changed accepted diagram content",
            code="delivery/candidate-preservation-failed",
            evidence={"comparison": preservation},
        )
    if before is not None:
        saved_edge_preservation_guard(before, candidate, changes)
    result = core_validation.validate_tree(candidate)
    strict_failed = bool(strict and result["warnings"])
    if not result["valid"] or strict_failed:
        raise contracts.DiagramError(
            "Serialized candidate failed strict validation" if strict_failed
            else "Serialized candidate failed validation",
            code="delivery/strict-validation-failed" if strict_failed
            else "delivery/validation-failed",
            evidence={"strict": strict, "diagnostics": result["diagnostics"]},
        )
    if before is not None:
        comparison = compare_trees(before, candidate, changes)
        if not comparison["preserved"]:
            raise contracts.DiagramError(
                "Serialized patch candidate differs from the declared patch",
                code="delivery/candidate-preservation-failed",
                evidence={"comparison": comparison},
            )
    return result
