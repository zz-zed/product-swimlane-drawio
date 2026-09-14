"""Bounded label/route construction under exact independent XML protection."""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET

from . import (context_native, contracts, document, geometry, review_preservation,
               routing, routing_adapter, validation)


def repair_style_values(cell, updates):
    tokens = cell.get("style", "").split(";")
    cell.set("style", ";".join(token.split("=", 1)[0] + "=" + updates[token.split("=", 1)[0]]
             if token.split("=", 1)[0] in updates else token for token in tokens))


def repair_install_points(cell, points):
    geom = cell.find("mxGeometry")
    original = geom.find("./Array[@as='points']")
    index = list(geom).index(original) if original is not None else None
    document.set_edge_points(cell, points, action="replace_automatic")
    if original is not None and original not in list(geom):
        # Preserve the original container even when its mutable points become
        # empty; deletion is not an exemption for a previously nonempty array.
        geom.insert(index, original)


def repair_route_candidates(tree, mutable):
    root, pool = document.graph_root(tree), document.find_pool(tree)
    lanes, nodes = document.lane_node_records(root, pool)
    records = document.edge_records(root)
    specs = []
    for sid, cell in sorted(records.items()):
        spec = routing_adapter.existing_edge_spec(cell, for_reroute=sid in mutable)
        if sid in mutable:
            for endpoint in ("exit", "entry"):
                if review_preservation.preservation_endpoint_locked(cell, endpoint):
                    side, offset = document.port_from_style(cell, endpoint)
                    spec[endpoint + "_side"], spec[endpoint + "_offset"] = side, offset
        specs.append(spec)
    main_path = document.read_main_path(pool)
    lane_views, node_views = document.routing_lane_views(lanes), document.routing_node_views(nodes)
    context = routing.new_routing_context(main_path, specs, node_views, v3_semantics=True)
    routing_adapter.seed_routing_context(context, records, lanes, nodes, exclude=mutable, require_measurable=True, pool=pool)
    context["native_label_profiles"] = routing_adapter.native_label_profiles(specs, pool, lanes, nodes, existing_edges=records)
    profiles = routing_adapter.arrowhead_clearance_profiles(specs, lanes, nodes, existing_edges=records)
    batch = routing.plan_route_batch(specs, lane_views, node_views, main_path=main_path, mutable_edge_ids=mutable,
                                    routing_context=context, v3_semantics=True, clearance_profiles=profiles)
    if batch.status != routing.ROUTE_COMPLETE:
        raise routing_adapter.route_batch_error(batch)
    for decision in batch.decisions:
        if decision.edge_id not in mutable:
            review_preservation.preservation_failure("Planner returned an undeclared mutable edge", code="review/protected-change")
        cell, routed = records[decision.edge_id], decision.routed
        if routed["route"] != cell.get(contracts.DATA_ROUTE):
            review_preservation.preservation_failure("Rerouting would change the declared route class", code="review/protected-change")
        updates = {}
        for endpoint in ("exit", "entry"):
            side, offset = routed[endpoint + "_side"], routed[endpoint + "_offset"]
            current = document.port_from_style(cell, endpoint)
            if review_preservation.preservation_endpoint_locked(cell, endpoint):
                if current != (side, offset):
                    review_preservation.preservation_failure("Rerouting would change an explicit endpoint lock", code="review/protected-change")
            else:
                x, y = geometry.port_xy(side, offset)
                updates.update({endpoint + "X": contracts.number(x), endpoint + "Y": contracts.number(y),
                                endpoint + "Dx": "0", endpoint + "Dy": "0"})
                side_key, offset_key = review_preservation.preservation_port_metadata(endpoint)
                cell.set(side_key, side); cell.set(offset_key, contracts.number(offset))
        repair_style_values(cell, updates)
        repair_install_points(cell, routed["points"])
        choice = batch.label_choices.get(decision.edge_id, routed["label_choice"])
        if choice is not None:
            routing_adapter.set_edge_label_position(cell, routed.get("native_path", routed["full_path"]), choice)
    return {"status": batch.status, "routing_order": list(batch.routing_order), "mutable_edges": sorted(mutable)}


def generate_repair_candidate(raw, actions):
    before = review_preservation.preservation_parse(raw)
    if not actions or len(actions) > 32:
        review_preservation.preservation_failure("A candidate requires one to 32 explicit actions")
    seen = set()
    for action in actions:
        target = action["target"]
        if target.get("kind") != "edge" or target["id"] in seen:
            review_preservation.preservation_failure("Repair actions require unique typed edges")
        seen.add(target["id"])
        review_preservation.preservation_native_eligibility(before, target["id"], action["intent"])
    tree = copy.deepcopy(before)
    route_ids = {a["target"]["id"] for a in actions if a["intent"] == "reroute-edge"}
    label_ids = {a["target"]["id"] for a in actions if a["intent"] == "reposition-edge-label"}
    route_receipt = repair_route_candidates(tree, route_ids) if route_ids else None
    if label_ids:
        root, pool = document.graph_root(tree), document.find_pool(tree)
        lanes, nodes = document.lane_node_records(root, pool)
        routing_adapter.reflow_mutable_edge_labels(root, pool, lanes, nodes, label_ids, preserve_position=False)
    changed = review_preservation.preservation_signature(before.getroot()) != review_preservation.preservation_signature(tree.getroot())
    if changed:
        document.find_pool(tree).set(contracts.DATA_TOOL_VERSION, contracts.TOOL_VERSION)
    candidate_raw = ET.tostring(tree.getroot(), encoding="utf-8", short_empty_elements=True)
    candidate = review_preservation.preservation_parse(candidate_raw)
    comparison = review_preservation.protected_repair_comparison(before, candidate, actions)
    result = validation.validate_tree(candidate)
    context_native.original_model(candidate)
    metrics = [{"target": action["target"], "intent": action["intent"],
                "before": review_preservation.repair_native_metric(before, action["target"]["id"], action["intent"]),
                "after": review_preservation.repair_native_metric(candidate, action["target"]["id"], action["intent"])} for action in actions]
    status = "no_change" if not changed else "technical_passed" if result["valid"] and not result["warnings"] and comparison["preserved"] else "technical_failed"
    return {"status": status, "raw": candidate_raw, "tree": candidate, "validation": result,
            "protected_projection": comparison, "metrics": metrics, "route_planning": route_receipt}
