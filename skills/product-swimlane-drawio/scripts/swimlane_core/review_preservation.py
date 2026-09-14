"""Independent exact XML protection and native repair metrics; no repair replay."""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from xml.parsers import expat

from . import (clearance, context_native, contracts, document, geometry, labels,
               migration, review_evidence)

REPAIR_RULE_VERSION = "visual-repair-v1"
REPAIR_INTENTS = {"reposition-edge-label", "reroute-edge"}
REPAIR_LABEL_ATTRIBUTES = {contracts.DATA_LABEL_LEFT, contracts.DATA_LABEL_TOP, contracts.DATA_LABEL_WIDTH,
                           contracts.DATA_LABEL_HEIGHT, contracts.DATA_LABEL_SEGMENT}
REPAIR_PORT_KEYS = {endpoint: tuple(endpoint + suffix for suffix in ("X", "Y", "Dx", "Dy"))
                    for endpoint in ("exit", "entry")}


def preservation_failure(message, *, code="review/candidate-ineligible", **evidence):
    raise contracts.DiagramError(message, code=code, evidence=evidence)


def preservation_parse(raw):
    parser = expat.ParserCreate(namespace_separator="|")
    namespaces = []
    parser.StartNamespaceDeclHandler = lambda prefix, uri: namespaces.append((prefix, uri))
    try:
        parser.Parse(raw, True)
    except expat.ExpatError as exc:
        preservation_failure("Repair requires a supported original XML payload", reason=type(exc).__name__)
    if namespaces:
        preservation_failure("Namespace declarations are not supported by the exact repair serializer", reason="namespace-declaration")
    tree, reasons = migration.parse_native_bytes(raw)
    if reasons:
        preservation_failure("Repair cannot preserve this original XML payload", reasons=reasons)
    context_native.original_model(tree)
    return tree


def preservation_signature(element):
    return (element.tag, tuple(sorted(element.attrib.items())), element.text, element.tail,
            tuple(preservation_signature(child) for child in element))


def preservation_endpoint_locked(cell, endpoint):
    side = contracts.DATA_EXIT_SIDE_EXPLICIT if endpoint == "exit" else contracts.DATA_ENTRY_SIDE_EXPLICIT
    offset = contracts.DATA_EXIT_OFFSET_EXPLICIT if endpoint == "exit" else contracts.DATA_ENTRY_OFFSET_EXPLICIT
    return cell.get(side) == "1" or cell.get(offset) == "1"


def preservation_port_metadata(endpoint):
    return ((contracts.DATA_EXIT_SIDE, contracts.DATA_EXIT_OFFSET) if endpoint == "exit"
            else (contracts.DATA_ENTRY_SIDE, contracts.DATA_ENTRY_OFFSET))


def preservation_native_eligibility(tree, edge_id, intent):
    if intent not in REPAIR_INTENTS:
        preservation_failure("Repair intent is outside the whitelist", code="review/action-not-authorized")
    cells = document.edge_records(document.graph_root(tree))
    if edge_id not in cells:
        preservation_failure("Repair target edge is absent", code="review/reference-invalid", edge_id=edge_id)
    cell = cells[edge_id]
    geometries = cell.findall("mxGeometry")
    if len(geometries) != 1 or geometries[0].get("as") != "geometry" or geometries[0].get("relative") != "1":
        preservation_failure("Repair requires one original relative=1 geometry binding", edge_id=edge_id)
    geom = geometries[0]
    offsets = geom.findall("./mxPoint[@as='offset']")
    if len(offsets) > 1 or (offsets and not {"x", "y"} <= set(offsets[0].attrib)):
        preservation_failure("Repair cannot normalize an ambiguous or incomplete offset", edge_id=edge_id)
    pool = document.find_pool(tree)
    lanes, nodes = document.lane_node_records(document.graph_root(tree), pool)
    path = document.edge_polyline(cell, lanes, nodes)
    measured = document.edge_label_measurement(cell, path, {"pool": pool, "lanes": lanes, "nodes": nodes})
    if measured["status"] == "not_available" or (intent == "reposition-edge-label" and measured["status"] != "available"):
        preservation_failure("Repair requires a supported native label carrier", edge_id=edge_id, reason=measured.get("reason"))
    if intent == "reroute-edge":
        if cell.get(contracts.DATA_WAYPOINTS_ORIGIN) != "automatic":
            preservation_failure("Explicit or unknown waypoint origin cannot be rerouted", edge_id=edge_id)
        tokens = cell.get("style", "").split(";")
        for endpoint, keys in REPAIR_PORT_KEYS.items():
            for key in keys:
                if sum(token.split("=", 1)[0] == key for token in tokens) != 1:
                    preservation_failure("Reroute requires unique existing native port keys", edge_id=edge_id, key=key)
        arrays = geom.findall("./Array[@as='points']")
        if len(arrays) > 1:
            preservation_failure("Reroute cannot normalize multiple points arrays", edge_id=edge_id)
        for array in arrays:
            for point in array.findall("mxPoint"):
                if set(point.attrib) != {"x", "y"} or len(point) or point.text is not None or point.tail is not None:
                    preservation_failure("Point-owned payload cannot be reassigned by rerouting", edge_id=edge_id)
    return cell


def preservation_label_projection(before, after):
    for cell in (before, after):
        for key in REPAIR_LABEL_ATTRIBUTES:
            cell.attrib.pop(key, None)
    old, new = before.find("mxGeometry"), after.find("mxGeometry")
    for key in ("x", "y"):
        if (key in old.attrib) != (key in new.attrib):
            preservation_failure("Repair changed native geometry attribute presence", code="review/protected-change", field=key)
        if key in old.attrib:
            old.set(key, "<mutable>"); new.set(key, "<mutable>")
    old_offsets, new_offsets = old.findall("./mxPoint[@as='offset']"), new.findall("./mxPoint[@as='offset']")
    if not old_offsets:
        if new_offsets:
            point = new_offsets[0]
            if (len(new_offsets) != 1 or set(point.attrib) != {"as", "x", "y"} or len(point)
                    or point.text is not None or point.tail is not None or list(new)[-1] is not point):
                preservation_failure("New offset exceeds its exact leaf structure", code="review/protected-change")
            new.remove(point)
    else:
        if len(new_offsets) != 1:
            preservation_failure("Existing offset was removed or duplicated", code="review/protected-change")
        for point in (old_offsets[0], new_offsets[0]):
            for key in ("x", "y"):
                if key not in point.attrib:
                    preservation_failure("Existing offset coordinate was removed", code="review/protected-change")
                point.set(key, "<mutable>")


def preservation_points_projection(before, after):
    old_geom, new_geom = before.find("mxGeometry"), after.find("mxGeometry")
    old_arrays, new_arrays = old_geom.findall("./Array[@as='points']"), new_geom.findall("./Array[@as='points']")
    if len(old_arrays) > 1 or len(new_arrays) > 1:
        preservation_failure("Points arrays became ambiguous", code="review/protected-change")
    for array in [*old_arrays, *new_arrays]:
        for point in array.findall("mxPoint"):
            if set(point.attrib) != {"x", "y"} or len(point) or point.text is not None or point.tail is not None:
                preservation_failure("Reroute changed protected point-owned content", code="review/protected-change")
    def plain(array):
        return (set(array.attrib) == {"as"} and array.text is None and array.tail is None
                and all(child.tag == "mxPoint" for child in array))
    if old_arrays and not new_arrays:
        if len(old_arrays[0]) or not plain(old_arrays[0]):
            preservation_failure("A nonempty or opaque original points container was removed", code="review/protected-change")
        old_geom.remove(old_arrays[0])
    elif not old_arrays and new_arrays:
        if not plain(new_arrays[0]) or list(new_geom)[-1] is not new_arrays[0]:
            preservation_failure("New points container exceeds its exact appended structure", code="review/protected-change")
        new_geom.remove(new_arrays[0])
    elif old_arrays:
        if plain(old_arrays[0]) and plain(new_arrays[0]):
            old_arrays[0][:] = []; new_arrays[0][:] = []
        else:
            for array in (old_arrays[0], new_arrays[0]):
                projected, in_run = [], False
                for child in array:
                    if child.tag == "mxPoint":
                        if not in_run:
                            projected.append(ET.Element("__mutable_point_run__"))
                        in_run = True
                    else:
                        projected.append(child); in_run = False
                array[:] = projected


def protected_repair_comparison(before, after, actions):
    old, new = copy.deepcopy(before), copy.deepcopy(after)
    old_cells, new_cells = document.semantic_cells(old), document.semantic_cells(new)
    geometry_changed = False
    try:
        for action in actions:
            target, intent = action["target"], action["intent"]
            if target.get("kind") != "edge":
                preservation_failure("Only typed edges can be repair targets", code="review/protected-change")
            sid = target["id"]
            old_cell = preservation_native_eligibility(old, sid, intent)
            new_cell = preservation_native_eligibility(new, sid, intent)
            geometry_changed = geometry_changed or preservation_signature(old_cell.find("mxGeometry")) != preservation_signature(new_cell.find("mxGeometry"))
            if intent == "reroute-edge":
                geometry_changed = geometry_changed or any(old_cell.get(key) != new_cell.get(key) for endpoint in REPAIR_PORT_KEYS for key in preservation_port_metadata(endpoint))
                for endpoint, keys in REPAIR_PORT_KEYS.items():
                    if not preservation_endpoint_locked(old_cell, endpoint):
                        for key in preservation_port_metadata(endpoint):
                            old_cell.set(key, "<mutable>"); new_cell.set(key, "<mutable>")
                        for cell in (old_cell, new_cell):
                            tokens = cell.get("style", "").split(";")
                            cell.set("style", ";".join(token.split("=", 1)[0] + "=<mutable>"
                                     if token.split("=", 1)[0] in keys else token for token in tokens))
            preservation_label_projection(old_cell, new_cell)
            if intent == "reroute-edge":
                preservation_points_projection(old_cell, new_cell)
        old_pool, new_pool = document.find_pool(old), document.find_pool(new)
        if old_pool.get(contracts.DATA_TOOL_VERSION) != new_pool.get(contracts.DATA_TOOL_VERSION):
            if new_pool.get(contracts.DATA_TOOL_VERSION) != contracts.TOOL_VERSION or not geometry_changed:
                preservation_failure("Invalid repair tool stamp change", code="review/protected-change")
            new_pool.set(contracts.DATA_TOOL_VERSION, old_pool.get(contracts.DATA_TOOL_VERSION))
        preserved = preservation_signature(old.getroot()) == preservation_signature(new.getroot())
        changed = sorted(key for key in set(old_cells) | set(new_cells)
                         if key not in old_cells or key not in new_cells
                         or preservation_signature(old_cells[key]) != preservation_signature(new_cells[key]))
        return {"rule_version": REPAIR_RULE_VERSION, "preserved": preserved,
                "differences": changed[:32] if changed else [] if preserved else ["xml-envelope-or-order"]}
    except contracts.DiagramError as exc:
        return {"rule_version": REPAIR_RULE_VERSION, "preserved": False,
                "differences": [exc.code], "reason": str(exc)}


def preservation_scene(tree):
    pool = document.find_pool(tree)
    root = document.graph_root(tree)
    lanes, nodes = document.lane_node_records(root, pool)
    cells = document.edge_records(root)
    paths = {sid: document.edge_polyline(cell, lanes, nodes) for sid, cell in cells.items()}
    scene = {"pool": pool, "lanes": lanes, "nodes": nodes}
    measured = {sid: document.edge_label_measurement(cell, paths[sid], scene) for sid, cell in cells.items()}
    boxes = {sid: geometry.node_bounds_in_pool(node, lanes[node["lane"]]) for sid, node in nodes.items()}
    return pool, lanes, nodes, cells, paths, measured, boxes


def repair_native_metric(tree, edge_id, intent):
    pool, lanes, nodes, cells, paths, measured, boxes = preservation_scene(tree)
    cell, path = cells[edge_id], paths[edge_id]
    if intent == "reposition-edge-label":
        label = measured[edge_id]
        if label["status"] != "available":
            preservation_failure("Label metric is unavailable", edge_id=edge_id)
        segments = {sid: list(zip(points, points[1:])) for sid, points in paths.items()}
        label_boxes = {sid: item["bounds"] for sid, item in measured.items() if item["status"] == "available"}
        pool_geometry = document.parse_geometry(pool)
        conflicts = labels.label_placement_conflicts(edge_id, label["carrier_segment"], label["bounds"], segments,
                        boxes, label_boxes, {"left": 0, "top": 0, "right": pool_geometry["width"], "bottom": pool_geometry["height"]})
        return {"metric_version": 1, "kind": "edge-label", "target": {"kind": "edge", "id": edge_id},
                "bounds_quality": label["bounds_quality"], "label_position": label["position"],
                "node_overlap_count": len(conflicts["blocking_node_ids"]), "label_overlap_count": len(conflicts["blocking_label_ids"]),
                "unrelated_edge_overlap_count": len(conflicts["blocking_edge_ids"]), "carrier_usable": label["carrier_usable"],
                "outside_container": conflicts["outside_container"], "conflict_count": sum(len(conflicts[key]) for key in
                    ("blocking_node_ids", "blocking_label_ids", "blocking_edge_ids")) + int(not label["carrier_usable"]) + int(conflicts["outside_container"]),
                "conflicts": conflicts}
    target = nodes[cell.get(contracts.DATA_TO)]
    result = clearance.measure_arrowhead_clearance(path, target_bounds=boxes[cell.get(contracts.DATA_TO)],
                   target_type=target["cell"].get(contracts.DATA_NODE_TYPE), target_style=target["cell"].get("style", ""), edge_style=cell.get("style", ""))
    if result.status != clearance.STATUS_COMPLETE:
        preservation_failure("Route clearance metric is unavailable", edge_id=edge_id, reason=result.reason)
    crossed = sorted(sid for sid, box in boxes.items() if sid not in {cell.get(contracts.DATA_FROM), cell.get(contracts.DATA_TO)}
                     and any(geometry.segment_intersects_box(segment, box, gap=0) for segment in zip(path, path[1:])))
    metric = {"metric_version": 1, "kind": "edge-route", "target": {"kind": "edge", "id": edge_id},
              "bend_count": geometry.bend_count(path), "manhattan_length": sum(abs(a[0]-b[0])+abs(a[1]-b[1]) for a,b in zip(path,path[1:])),
              "terminal_clearance": result.terminal_run_px, "minimum_terminal_clearance": result.minimum_terminal_run_px,
              "clearance_violation": result.violation, "node_crossing_count": len(crossed), "crossed_node_ids": crossed}
    for value in metric.values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            review_evidence.review_number(value)
    return metric


def repair_metric_comparison(before, after, code):
    if before["kind"] == "edge-label":
        improved = after["conflict_count"] < before["conflict_count"]
        non_regressing = after["conflict_count"] <= before["conflict_count"]
        resolved = after["conflict_count"] == 0 and after["carrier_usable"]
    else:
        non_regressing = (after["node_crossing_count"] <= before["node_crossing_count"]
                          and int(after["clearance_violation"]) <= int(before["clearance_violation"])
                          and after["bend_count"] <= before["bend_count"] and after["manhattan_length"] <= before["manhattan_length"])
        if code == "visual/arrowhead-hidden":
            improved = non_regressing and after["terminal_clearance"] >= before["terminal_clearance"] + 1 and not after["clearance_violation"]
            resolved = not after["clearance_violation"]
        else:
            improved = non_regressing and (after["bend_count"] < before["bend_count"] or after["manhattan_length"] <= before["manhattan_length"] - 1)
            resolved = after["bend_count"] <= 2 and not after["clearance_violation"] and after["node_crossing_count"] == 0
    return {"improved": bool(improved), "non_regressing": bool(non_regressing), "resolved": bool(resolved)}
